from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bluepaper.config import Settings

bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Integrator API key. Send as `Authorization: Bearer <api-key>`. "
        "Browser clients omit this and complete Turnstile on "
        "`POST /v1/conversions` instead."
    ),
)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def key_status(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> str:
    """Return ``valid``, ``invalid``, or ``absent``."""
    if credentials is None:
        return "absent"
    if credentials.scheme.lower() != "bearer":
        return "invalid"
    token = credentials.credentials.encode("utf-8")
    expected = settings.api_key.encode("utf-8")
    if len(token) != len(expected) or not hmac.compare_digest(token, expected):
        return "invalid"
    return "valid"


def reject_invalid_key(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> str:
    key = key_status(credentials, settings)
    if key == "invalid":
        raise _unauthorized()
    return key
