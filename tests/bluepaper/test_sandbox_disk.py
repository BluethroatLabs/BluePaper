from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from typing import ClassVar

import pytest

from dev_scripts import ensure_sandbox_disk as disk

IMAGE = "ghcr.io/freedomofpress/dangerzone/v1:latest"
OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
ARGS = [
    "--subscription",
    "sub",
    "--resource-group",
    "rg",
    "--region",
    "eastus2",
    "--sandbox-group",
    "group",
    "--name",
    "dangerzone-doc-to-pixels",
    "--image",
    IMAGE,
]


def test_split_image_reads_registry_repository_and_tag() -> None:
    assert disk.split_image(IMAGE) == (
        "ghcr.io",
        "freedomofpress/dangerzone/v1",
        "latest",
    )
    assert disk.split_image("localhost:5000/dz") == ("localhost:5000", "dz", "latest")
    assert disk.split_image(f"ghcr.io/org/dz:1.2@{OLD}") == ("ghcr.io", "org/dz", OLD)


@pytest.mark.parametrize("image", ["dangerzone:latest", "org/dz", "ghcr.io/dz@md5:1"])
def test_split_image_rejects_what_it_cannot_pin(image: str) -> None:
    with pytest.raises(ValueError):
        disk.split_image(image)


def test_pinned_names_follow_the_digest() -> None:
    assert (
        disk.pinned_image(IMAGE, NEW) == f"ghcr.io/freedomofpress/dangerzone/v1@{NEW}"
    )
    assert disk.pinned_disk_name("dz", NEW) == "dz-bbbbbbbbbbbb"
    assert disk.pinned_disk_name("dz", OLD) != disk.pinned_disk_name("dz", NEW)


def test_resolve_digest_keeps_a_digest_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(_request: urllib.request.Request) -> None:
        raise AssertionError("a pinned image needs no registry call")

    monkeypatch.setattr(disk, "_open", no_network)
    assert disk.resolve_digest(f"ghcr.io/org/dz@{OLD}") == OLD


def _unauthorized(url: str) -> urllib.error.HTTPError:
    headers = Message()
    headers["WWW-Authenticate"] = (
        'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
        'scope="repository:freedomofpress/dangerzone/v1:pull"'
    )
    return urllib.error.HTTPError(url, 401, "Unauthorized", headers, None)


def test_resolve_digest_uses_an_anonymous_pull_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def fake_open(request: urllib.request.Request) -> tuple[dict[str, str], bytes]:
        url = request.full_url
        auth = request.get_header("Authorization")
        seen.append((request.get_method(), url, auth))
        if url.startswith("https://ghcr.io/token"):
            return {}, json.dumps({"token": "anon"}).encode()
        if auth != "Bearer anon":
            raise _unauthorized(url)
        return {"docker-content-digest": NEW}, b""

    monkeypatch.setattr(disk, "_open", fake_open)

    assert disk.resolve_digest(IMAGE) == NEW
    manifest = "https://ghcr.io/v2/freedomofpress/dangerzone/v1/manifests/latest"
    assert seen[0] == ("HEAD", manifest, None)
    assert seen[1][0] == "GET"
    assert "scope=repository%3Afreedomofpress%2Fdangerzone%2Fv1%3Apull" in seen[1][1]
    assert seen[2] == ("HEAD", manifest, "Bearer anon")


def test_resolve_digest_fails_without_a_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk, "_open", lambda _request: ({}, b""))
    with pytest.raises(RuntimeError, match="no digest"):
        disk.resolve_digest(IMAGE)


def test_pull_token_refuses_a_plain_http_realm() -> None:
    with pytest.raises(RuntimeError, match="unsupported login"):
        disk._pull_token('Bearer realm="http://example.test/token"')


class FakePlane:
    """Stands in for the sandbox data plane. Class state survives ``main``."""

    disks: ClassVar[list[dict]] = []
    created: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, _endpoint: str, _group_path: str) -> None:
        pass

    def list_disks(self) -> list[dict]:
        return list(self.disks)

    def create_disk(self, name: str, image: str) -> dict:
        self.created.append((name, image))
        return {
            "id": f"id-{name}",
            "labels": {"name": name},
            "status": {"state": "Ready"},
        }


@pytest.fixture
def plane(monkeypatch: pytest.MonkeyPatch) -> type[FakePlane]:
    FakePlane.disks = []
    FakePlane.created = []
    monkeypatch.setattr(disk, "DataPlane", FakePlane)
    return FakePlane


def _ready(name: str) -> dict:
    return {"id": f"id-{name}", "labels": {"name": name}, "status": {"state": "Ready"}}


def test_main_reuses_the_disk_of_the_current_digest(
    monkeypatch: pytest.MonkeyPatch,
    plane: type[FakePlane],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(disk, "resolve_digest", lambda _image: NEW)
    plane.disks = [_ready("dangerzone-doc-to-pixels-bbbbbbbbbbbb")]

    disk.main(ARGS)

    assert plane.created == []
    assert capsys.readouterr().out.strip() == "id-dangerzone-doc-to-pixels-bbbbbbbbbbbb"


def test_main_bakes_again_when_upstream_publishes(
    monkeypatch: pytest.MonkeyPatch,
    plane: type[FakePlane],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(disk, "resolve_digest", lambda _image: NEW)
    # One disk from before disks were named by digest, one from an older image.
    plane.disks = [
        _ready("dangerzone-doc-to-pixels"),
        _ready("dangerzone-doc-to-pixels-aaaaaaaaaaaa"),
        _ready("unrelated"),
    ]

    disk.main(ARGS)

    assert plane.created == [
        (
            "dangerzone-doc-to-pixels-bbbbbbbbbbbb",
            f"ghcr.io/freedomofpress/dangerzone/v1@{NEW}",
        )
    ]
    captured = capsys.readouterr()
    assert captured.out.strip() == "id-dangerzone-doc-to-pixels-bbbbbbbbbbbb"
    assert "disk dangerzone-doc-to-pixels was baked" in captured.err
    assert "disk dangerzone-doc-to-pixels-aaaaaaaaaaaa was baked" in captured.err
    assert "unrelated" not in captured.err
