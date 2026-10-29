"""Validate Bluethroat GitHub App user access tokens.

The distributed ``bluethroat`` CLI obtains a user token through GitHub's
device flow. BluePaper checks that token with the GitHub App credentials and
uses the returned numeric user id as the conversion owner.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from bluepaper.config import Settings

log = logging.getLogger("bluepaper.api.github_auth")

API = "https://api.github.com"
_API_VERSION = "2026-03-10"
_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_CACHE_TTL = 60.0
_MAX_CACHE = 1024

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TOKEN_RE = re.compile(r"^(?:ghu|gho)_[A-Za-z0-9_]{16,240}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")


class GitHubAuthRejected(Exception):
    """The token is not a live user token issued by this GitHub App."""


class GitHubAuthUnavailable(Exception):
    """GitHub could not be reached or the app credentials cannot be used."""


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str


_lock = threading.Lock()
_cache: dict[str, tuple[float, GitHubUser]] = {}


def configured(settings: Settings) -> bool:
    client_id = settings.github_client_id or ""
    return bool(
        client_id
        and settings.github_client_secret
        and _CLIENT_ID_RE.fullmatch(client_id)
    )


def misconfigured(settings: Settings) -> bool:
    client_id = settings.github_client_id or ""
    secret = settings.github_client_secret or ""
    if bool(client_id) != bool(secret):
        return True
    return bool(client_id) and not _CLIENT_ID_RE.fullmatch(client_id)


def is_user_token(token: str) -> bool:
    return len(token) <= 244 and bool(_TOKEN_RE.fullmatch(token))


def identify(token: str, settings: Settings) -> GitHubUser:
    """Return the GitHub identity represented by ``token``, or raise."""
    client_id = settings.github_client_id or ""
    secret = settings.github_client_secret or ""
    if not configured(settings):
        raise GitHubAuthUnavailable("misconfigured")
    key = _cache_key(client_id, secret, token)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]

    try:
        response = _check_token(client_id, secret, token)
    except httpx.HTTPError as exc:
        # The exception can retain the request, including both credentials.
        log.warning("github token check failed: %s", type(exc).__name__)
        raise GitHubAuthUnavailable("unreachable") from None
    if response.status_code != 200:
        if response.status_code in (401, 403, 404, 422):
            raise GitHubAuthRejected("rejected")
        log.warning("github token check HTTP %s", response.status_code)
        raise GitHubAuthUnavailable("status")
    try:
        body = response.json()
    except ValueError:
        # GitHub's response can echo the token; never log the body.
        log.warning("github token check returned a non-JSON body")
        raise GitHubAuthUnavailable("body") from None
    user = _user_from(body, client_id)
    ttl = _ttl(body)
    with _lock:
        _cache[key] = (time.monotonic() + ttl, user)
        _prune_cache()
    return user


def clear_identity_cache() -> None:
    with _lock:
        _cache.clear()


def _check_token(client_id: str, secret: str, token: str) -> httpx.Response:
    """Ask GitHub which app and user own the token."""
    with httpx.Client(
        timeout=_TIMEOUT,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        return client.post(
            f"{API}/applications/{client_id}/token",
            auth=(client_id, secret),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
                "User-Agent": "bluepaper",
            },
            json={"access_token": token},
        )


def _user_from(body: object, client_id: str) -> GitHubUser:
    if not isinstance(body, dict):
        raise GitHubAuthUnavailable("body")
    app = body.get("app")
    if not isinstance(app, dict):
        raise GitHubAuthRejected("app")
    got = app.get("client_id")
    if not isinstance(got, str) or not _same(got, client_id):
        raise GitHubAuthRejected("app")
    user = body.get("user")
    if not isinstance(user, dict):
        raise GitHubAuthRejected("user")
    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise GitHubAuthRejected("user")
    if user_id < 1 or user_id > 2**63 - 1:
        raise GitHubAuthRejected("user")
    login = user.get("login")
    if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login):
        login = ""
    return GitHubUser(id=user_id, login=login)


def _ttl(body: dict) -> float:
    expiry = _expiry(body.get("expires_at"))
    ttl = _CACHE_TTL
    if expiry is not None:
        ttl = min(ttl, expiry - time.time())
    if ttl <= 0:
        raise GitHubAuthRejected("expired")
    return ttl


def _expiry(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GitHubAuthRejected("expiry")
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubAuthRejected("expiry") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry.timestamp()


def _prune_cache() -> None:
    if len(_cache) <= _MAX_CACHE:
        return
    now = time.monotonic()
    live = {key: value for key, value in _cache.items() if value[0] > now}
    if len(live) > _MAX_CACHE:
        ranked = sorted(live.items(), key=lambda item: item[1][0], reverse=True)
        live = dict(ranked[:_MAX_CACHE])
    _cache.clear()
    _cache.update(live)


def _cache_key(client_id: str, secret: str, token: str) -> str:
    return hashlib.sha256(f"{client_id}\0{secret}\0{token}".encode()).hexdigest()


def _same(left: str, right: str) -> bool:
    return len(left) == len(right) and hmac.compare_digest(left, right)
