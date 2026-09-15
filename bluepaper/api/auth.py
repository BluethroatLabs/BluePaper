from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bluepaper.config import Settings

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Operator API key. Send as `Authorization: Bearer <api-key>`.",
)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    token = credentials.credentials.encode("utf-8")
    expected = settings.api_key.encode("utf-8")
    if len(token) != len(expected) or not hmac.compare_digest(token, expected):
        raise _unauthorized()
