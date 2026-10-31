from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from bluepaper.api.app import create_app
from bluepaper.api.github_auth import clear_identity_cache
from bluepaper.config import Settings
from bluepaper.storage.base import Stores
from tests.bluepaper.conftest import auth

CLIENT_ID = "Iv1.test"
SECRET = "test-secret"
TOKEN = "ghu_" + "a" * 32
SAME_USER_TOKEN = "ghu_" + "b" * 32
OTHER_USER_TOKEN = "ghu_" + "c" * 32


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _body(
    user_id: int = 7,
    login: str = "octocat",
    *,
    client_id: str = CLIENT_ID,
) -> dict:
    return {
        "app": {"client_id": client_id, "name": "Bluethroat"},
        "user": {"id": user_id, "login": login},
        "token": "echoed-token-must-not-leak",
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_identity_cache()
    yield
    clear_identity_cache()


@pytest.fixture
def github_settings(settings: Settings) -> Settings:
    settings.github_client_id = CLIENT_ID
    settings.github_client_secret = SECRET
    return settings


@pytest.fixture
def github_client(github_settings: Settings, stores: Stores) -> TestClient:
    return TestClient(create_app(github_settings, stores))


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _install(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, _Response] | None = None,
) -> list[str]:
    calls: list[str] = []

    def fake(client_id: str, secret: str, token: str) -> _Response:
        assert client_id == CLIENT_ID
        assert secret == SECRET
        calls.append(token)
        if responses is None:
            return _Response(200, _body())
        return responses[token]

    monkeypatch.setattr("bluepaper.api.github_auth._check_token", fake)
    return calls


def test_github_token_creates_a_user_owned_conversion(
    github_client: TestClient,
    stores: Stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch)
    created = github_client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert created.status_code == 202
    conversion_id = created.json()["id"]
    record = stores.table.get(conversion_id)
    assert record is not None
    assert record.guest is False
    assert record.github_user_id == 7
    assert github_client.get(
        f"/v1/conversions/{conversion_id}",
        headers=_bearer(SAME_USER_TOKEN),
    ).status_code == 200
    assert calls == [TOKEN, SAME_USER_TOKEN]


def test_concurrency_limit_is_per_github_user(
    github_client: TestClient,
    github_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_settings.max_queue_depth = 10
    _install(
        monkeypatch,
        {
            TOKEN: _Response(200, _body()),
            OTHER_USER_TOKEN: _Response(200, _body(8, "hubot")),
        },
    )

    def post(token: str) -> int:
        return github_client.post(
            "/v1/conversions",
            headers=_bearer(token),
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        ).status_code

    assert [post(TOKEN) for _ in range(3)] == [202, 202, 429]
    assert post(OTHER_USER_TOKEN) == 202


def test_other_github_user_cannot_see_or_delete_conversion(
    github_client: TestClient,
    stores: Stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            TOKEN: _Response(200, _body()),
            OTHER_USER_TOKEN: _Response(200, _body(8, "hubot")),
        },
    )
    created = github_client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    ).json()
    path = f"/v1/conversions/{created['id']}"
    assert github_client.get(path, headers=_bearer(OTHER_USER_TOKEN)).status_code == 404
    assert github_client.delete(path, headers=_bearer(OTHER_USER_TOKEN)).status_code == 404
    assert stores.table.get(created["id"]) is not None
    # The operator API key retains administrative access.
    assert github_client.get(path, headers=auth()).status_code == 200


def test_github_token_cannot_open_an_api_key_conversion(
    github_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    created = github_client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    ).json()
    response = github_client.get(
        f"/v1/conversions/{created['id']}",
        headers=_bearer(),
    )
    assert response.status_code == 404


def test_valid_github_token_is_cached(
    github_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch)
    created = github_client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    ).json()
    assert github_client.get(
        f"/v1/conversions/{created['id']}",
        headers=_bearer(),
    ).status_code == 200
    assert calls == [TOKEN]


def test_wrong_app_token_is_rejected_without_leaking_secrets(
    github_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, {TOKEN: _Response(200, _body(client_id="Iv1.other"))})
    response = github_client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text
    assert SECRET not in response.text
    assert "echoed-token-must-not-leak" not in response.text
    assert TOKEN not in caplog.text
    assert SECRET not in caplog.text


def test_github_outage_is_503(
    github_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object) -> _Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("bluepaper.api.github_auth._check_token", fail)
    response = github_client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 503
    assert TOKEN not in response.text


def test_half_configured_github_auth_fails_closed_for_github_tokens(
    settings: Settings,
    stores: Stores,
) -> None:
    settings.github_client_id = CLIENT_ID
    client = TestClient(create_app(settings, stores))
    denied = client.post(
        "/v1/conversions",
        headers=_bearer(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert denied.status_code == 503
    allowed = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert allowed.status_code == 202


def test_token_check_uses_hardened_transport(
    github_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def post(self, url: str, **kwargs: object) -> _Response:
            captured["url"] = url
            captured.update(kwargs)
            return _Response(200, _body())

    monkeypatch.setattr("bluepaper.api.github_auth.httpx.Client", _Client)
    from bluepaper.api.github_auth import identify

    user = identify(TOKEN, github_settings)
    assert user.id == 7
    assert captured["url"] == f"https://api.github.com/applications/{CLIENT_ID}/token"
    assert captured["auth"] == (CLIENT_ID, SECRET)
    assert captured["json"] == {"access_token": TOKEN}
    assert captured["init"]["trust_env"] is False
    assert captured["init"]["follow_redirects"] is False
