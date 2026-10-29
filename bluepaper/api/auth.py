from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bluepaper.api import github_auth
from bluepaper.config import Settings

bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Integrator API key or Bluethroat GitHub App user token. Send as "
        "`Authorization: Bearer <token>`. Browser clients omit this and "
        "complete Turnstile on `POST /v1/conversions` instead."
    ),
)


@dataclass(frozen=True)
class Principal:
    kind: Literal["api_key", "github"]
    github_user_id: int | None = None
    github_login: str = ""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key or GitHub token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal | None:
    """Authenticate an integrator key or Bluethroat GitHub App user token."""
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        raise unauthorized()
    raw_token = credentials.credentials
    token = raw_token.encode("utf-8")
    expected = settings.api_key.encode("utf-8")
    if len(token) == len(expected) and hmac.compare_digest(token, expected):
        return Principal(kind="api_key")

    if not github_auth.is_user_token(raw_token):
        raise unauthorized()
    if not github_auth.configured(settings):
        if github_auth.misconfigured(settings):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub authorization could not be checked. Retry in a moment.",
            )
        raise unauthorized()
    try:
        user = github_auth.identify(raw_token, settings)
    except github_auth.GitHubAuthRejected:
        raise unauthorized() from None
    except github_auth.GitHubAuthUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub authorization could not be checked. Retry in a moment.",
        ) from None
    return Principal(
        kind="github",
        github_user_id=user.id,
        github_login=user.login,
    )
