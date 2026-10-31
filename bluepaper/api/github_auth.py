"""Validate Bluethroat GitHub App user access tokens.

The distributed ``bluethroat`` CLI obtains a user token through GitHub's
device flow. BluePaper checks that token with the GitHub App credentials and
uses the returned numeric user id as the conversion owner.

Every uncached check spends the app's shared GitHub API quota, and anyone can
send a token-shaped string. So rejections are cached too, uncached checks are
throttled per client and in total, and GitHub's rate-limit answers become
"unavailable" (503) rather than "bad token" (401).
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
_REJECTED_TTL = 300.0
_MAX_REJECTED = 4096
# Uncached checks. One client: a burst of 20, then one per 6 s. Everyone:
# a burst of 120, then one per second (3,600 an hour, under GitHub's 5,000).
_CLIENT_BURST = 20.0
_CLIENT_PER_SECOND = 1 / 6
_GLOBAL_BURST = 120.0
_GLOBAL_PER_SECOND = 1.0
_MAX_CLIENTS = 4096

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


class _Throttle:
    """Token buckets, one per key. Callers hold ``_lock``."""

    def __init__(self, burst: float, per_second: float, max_keys: int) -> None:
        self._burst = burst
        self._rate = per_second
        self._max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float) -> bool:
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        allowed = tokens >= 1
        self._buckets[key] = (tokens - 1 if allowed else tokens, now)
        if len(self._buckets) > self._max_keys:
            self._prune(now)
        return allowed

    def clear(self) -> None:
        self._buckets.clear()

    def _prune(self, now: float) -> None:
        # A bucket that has refilled is the same as no bucket.
        refill = self._burst / self._rate
        self._buckets = {
            key: value
            for key, value in self._buckets.items()
            if now - value[1] < refill
        }
        if len(self._buckets) > self._max_keys:
            self._buckets.clear()


_lock = threading.Lock()
_cache: dict[str, tuple[float, GitHubUser]] = {}
_rejected: dict[str, float] = {}
_per_client = _Throttle(_CLIENT_BURST, _CLIENT_PER_SECOND, _MAX_CLIENTS)
_overall = _Throttle(_GLOBAL_BURST, _GLOBAL_PER_SECOND, 1)


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


def identify(token: str, settings: Settings, client: str | None = None) -> GitHubUser:
    """Return the GitHub identity represented by ``token``, or raise.

    ``client`` (the caller's address) keys the per-client throttle.
    """
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
        rejected_until = _rejected.get(key)
        if rejected_until is not None and rejected_until > now:
            raise GitHubAuthRejected("rejected")
        if not _per_client.allow(client or "", now) or not _overall.allow("", now):
            log.warning("github token checks throttled")
            raise GitHubAuthUnavailable("throttled")

    try:
        user, ttl = _lookup(client_id, secret, token)
    except GitHubAuthRejected:
        with _lock:
            _rejected[key] = time.monotonic() + _REJECTED_TTL
            _prune_rejected()
        raise
    with _lock:
        _cache[key] = (time.monotonic() + ttl, user)
        _prune_cache()
    return user


def _lookup(client_id: str, secret: str, token: str) -> tuple[GitHubUser, float]:
    try:
        response = _check_token(client_id, secret, token)
    except httpx.HTTPError as exc:
        # The exception can retain the request, including both credentials.
        log.warning("github token check failed: %s", type(exc).__name__)
        raise GitHubAuthUnavailable("unreachable") from None
    if response.status_code == 404:
        # GitHub's answer for a token this app did not issue, or one revoked.
        raise GitHubAuthRejected("rejected")
    if response.status_code != 200:
        # 401: the app credentials are wrong. 403/429: rate limited. 422: the
        # check endpoint has been spammed. None of these says the token is bad.
        log.warning("github token check HTTP %s", response.status_code)
        raise GitHubAuthUnavailable("status")
    try:
        body = response.json()
    except ValueError:
        # GitHub's response can echo the token; never log the body.
        log.warning("github token check returned a non-JSON body")
        raise GitHubAuthUnavailable("body") from None
    return _user_from(body, client_id), _ttl(body)


def clear_identity_cache() -> None:
    with _lock:
        _cache.clear()
        _rejected.clear()
        _per_client.clear()
        _overall.clear()


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


def _prune_rejected() -> None:
    if len(_rejected) <= _MAX_REJECTED:
        return
    now = time.monotonic()
    live = {key: until for key, until in _rejected.items() if until > now}
    if len(live) > _MAX_REJECTED:
        ranked = sorted(live.items(), key=lambda item: item[1], reverse=True)
        live = dict(ranked[:_MAX_REJECTED])
    _rejected.clear()
    _rejected.update(live)


def _cache_key(client_id: str, secret: str, token: str) -> str:
    return hashlib.sha256(f"{client_id}\0{secret}\0{token}".encode()).hexdigest()


def _same(left: str, right: str) -> bool:
    return len(left) == len(right) and hmac.compare_digest(left, right)
