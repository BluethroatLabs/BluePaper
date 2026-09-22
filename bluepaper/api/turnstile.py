from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException, Request, status

from bluepaper.config import Settings

_SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_EXPECTED_ACTION = "queue-conversion"
_MAX_TOKEN_LEN = 2048
# Cloudflare's public always-pass testing secret. siteverify accepts the dummy
# token and returns a fixed payload, so action and hostname are not meaningful.
_TEST_PASS_SECRET = "1x0000000000000000000000000000000AA"


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host
    return None


def _hostnames(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="forbidden",
    )


def _siteverify(secret: str, token: str, remote_ip: str | None) -> dict:
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        _SITEVERIFY,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise _forbidden()
            parsed = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, HTTPException):
        raise _forbidden() from None
    if not isinstance(parsed, dict):
        raise _forbidden()
    return parsed


def verify_turnstile(
    token: str | None,
    remote_ip: str | None,
    settings: Settings,
) -> None:
    """Gate queueing on Cloudflare siteverify when a secret is configured."""
    secret = (settings.turnstile_secret or "").strip()
    if not secret:
        return
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LEN:
        raise _forbidden()
    result = _siteverify(secret, token, remote_ip)
    if secret == _TEST_PASS_SECRET:
        if result.get("success") is not True:
            raise _forbidden()
        return
    expected = _hostnames(settings.turnstile_hostnames)
    if (
        not expected
        or result.get("success") is not True
        or result.get("action") != _EXPECTED_ACTION
        or result.get("hostname") not in expected
    ):
        raise _forbidden()
