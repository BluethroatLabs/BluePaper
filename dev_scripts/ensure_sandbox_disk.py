#!/usr/bin/env python3
"""Bake a sandbox disk image for the current upstream image and print its id.

The image tag is resolved to its digest first. The disk is named after that
digest and baked from it, so a disk is reused only while upstream has not
published a new image. A reused disk would otherwise keep the packages it was
first baked with, and miss every later security update of the converter.

Uses the Azure CLI for a data-plane token. The ``aca`` CLI is not required.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("bluepaper.disk")

API_VERSION = "2026-02-01-preview"
TOKEN_RESOURCE = "https://dynamicsessions.io"
READY = {"ready", "succeeded"}
FAILED = {"failed"}

MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CHALLENGE_PARAM = re.compile(r'(\w+)="([^"]*)"')


def split_image(image: str) -> tuple[str, str, str]:
    """Split ``registry/repository[:tag][@digest]`` into its three parts.

    The last part is the digest when the reference has one, else the tag.
    """
    name, _, digest = image.partition("@")
    registry, slash, repository = name.partition("/")
    if not slash or not repository or not ("." in registry or ":" in registry):
        raise ValueError(f"image must name its registry: {image}")
    tag = "latest"
    if ":" in repository:
        repository, _, tag = repository.rpartition(":")
    if digest:
        if not _DIGEST.fullmatch(digest):
            raise ValueError(f"unsupported image digest: {image}")
        return registry, repository, digest
    if not repository or not tag:
        raise ValueError(f"malformed image reference: {image}")
    return registry, repository, tag


def _open(request: urllib.request.Request) -> tuple[dict[str, str], bytes]:
    """Lower-cased response headers and body. Tests replace this."""
    with urllib.request.urlopen(request, timeout=60) as response:
        headers = {key.lower(): value for key, value in response.headers.items()}
        return headers, response.read()


def _pull_token(challenge: str) -> str:
    """Anonymous pull token for a ``WWW-Authenticate: Bearer`` challenge."""
    scheme, _, rest = challenge.partition(" ")
    params = dict(_CHALLENGE_PARAM.findall(rest))
    realm = params.pop("realm", "")
    if scheme.lower() != "bearer" or not realm.startswith("https://"):
        raise RuntimeError("registry asked for an unsupported login")
    url = f"{realm}?{urllib.parse.urlencode(params)}"
    _headers, body = _open(urllib.request.Request(url))
    data = json.loads(body.decode("utf-8"))
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError("registry did not return a pull token")
    return str(token)


def resolve_digest(image: str) -> str:
    """The ``sha256:…`` digest that ``image`` points at right now."""
    registry, repository, reference = split_image(image)
    if _DIGEST.fullmatch(reference):
        return reference
    url = f"https://{registry}/v2/{repository}/manifests/{reference}"

    def head(token: str | None) -> dict[str, str]:
        request = urllib.request.Request(url, method="HEAD")
        request.add_header("Accept", MANIFEST_TYPES)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        headers, _body = _open(request)
        return headers

    try:
        headers = head(None)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise RuntimeError(f"could not resolve {image}: HTTP {exc.code}") from exc
        token = _pull_token(exc.headers.get("WWW-Authenticate", ""))
        try:
            headers = head(token)
        except urllib.error.HTTPError as denied:
            raise RuntimeError(
                f"could not resolve {image}: HTTP {denied.code}"
            ) from denied
    digest = headers.get("docker-content-digest", "")
    if not _DIGEST.fullmatch(digest):
        raise RuntimeError(f"registry returned no digest for {image}")
    return digest


def pinned_image(image: str, digest: str) -> str:
    registry, repository, _reference = split_image(image)
    return f"{registry}/{repository}@{digest}"


def pinned_disk_name(name: str, digest: str) -> str:
    return f"{name}-{digest.removeprefix('sha256:')[:12]}"


def superseded_disks(items: list[dict], name: str, current: str) -> list[str]:
    """Names of disks baked under ``name`` for an older image."""
    names = {disk_name(item) for item in items}
    return sorted(
        found
        for found in names
        if found != current and (found == name or found.startswith(f"{name}-"))
    )


def disk_name(item: dict) -> str:
    labels = item.get("labels") or {}
    return str(item.get("name") or labels.get("name") or "")


def disk_items(data: object) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        value = data.get("value")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if data.get("id"):
            return [data]
    return []


def find_disk(items: list[dict], name: str) -> dict | None:
    for item in items:
        if disk_name(item) == name and item.get("id"):
            return item
    return None


def disk_state(item: dict) -> str:
    status = item.get("status") or {}
    return str(status.get("state") or "")


class DataPlane:
    def __init__(self, endpoint: str, group_path: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.group_path = group_path
        self._token = ""

    def _token_value(self) -> str:
        if not self._token:
            self._token = subprocess.check_output(
                [
                    "az",
                    "account",
                    "get-access-token",
                    "--resource",
                    TOKEN_RESOURCE,
                    "--query",
                    "accessToken",
                    "-o",
                    "tsv",
                ],
                text=True,
            ).strip()
        return self._token

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        timeout: float = 60,
    ) -> object:
        url = f"{self.endpoint}{path}"
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api-version={API_VERSION}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self._token_value()}")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def list_disks(self) -> list[dict]:
        path = f"{self.group_path}/diskimages"
        items: list[dict] = []
        while path:
            data = self.request("GET", path)
            items.extend(disk_items(data))
            if isinstance(data, dict) and data.get("nextLink"):
                path = urllib.parse.urlparse(str(data["nextLink"])).path
            else:
                path = ""
        return items

    def create_disk(self, name: str, image: str, timeout: float = 600) -> dict:
        """Bake a disk. The service answers only once the bake has finished,
        which took 75 seconds for the Dangerzone image, so this call needs
        the bake's time budget and not that of an ordinary request."""
        created = self.request(
            "PUT",
            f"{self.group_path}/diskimages",
            {"image": {"base": image}, "labels": {"name": name}},
            timeout=timeout,
        )
        if not isinstance(created, dict) or not created.get("id"):
            raise RuntimeError("disk create did not return an id")
        return created

    def get_disk(self, disk_id: str) -> dict:
        item = self.request("GET", f"{self.group_path}/diskimages/{disk_id}")
        if not isinstance(item, dict):
            raise TypeError("disk get returned an unexpected payload")
        return item


def wait_until_ready(plane: DataPlane, disk_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        item = plane.get_disk(disk_id)
        state = disk_state(item).lower()
        if state in READY:
            return
        if state in FAILED:
            status = item.get("status") or {}
            message = status.get("errorMessage") or status.get("message")
            raise RuntimeError(str(message or "disk build failed"))
        if time.monotonic() >= deadline:
            raise RuntimeError(f"disk {disk_id} was not ready within {int(timeout)}s")
        print(f"disk {disk_id} is {state or 'pending'}", file=sys.stderr)
        time.sleep(5)


def bake(plane: DataPlane, name: str, image: str, timeout: float) -> dict:
    """Bake ``image`` into a disk called ``name`` and return the disk.

    When the create call outlasts ``timeout``, the bake may still finish on
    the service. Sending the call again could bake the same image twice, so
    this looks once for the disk before giving up.
    """
    try:
        return plane.create_disk(name, image, timeout=timeout)
    except TimeoutError:
        found = find_disk(plane.list_disks(), name)
        if found is not None:
            return found
        raise RuntimeError(
            f"the sandbox service did not finish baking {name} within "
            f"{int(timeout)}s; run this again, or pass a longer --timeout"
        ) from None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--sandbox-group", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    plane = DataPlane(
        f"https://management.{args.region}.azuredevcompute.io",
        (
            f"/subscriptions/{args.subscription}"
            f"/resourceGroups/{args.resource_group}"
            f"/sandboxGroups/{args.sandbox_group}"
        ),
    )
    digest = resolve_digest(args.image)
    name = pinned_disk_name(args.name, digest)
    image = pinned_image(args.image, digest)
    disks = plane.list_disks()
    existing = find_disk(disks, name)
    if existing is None:
        print(f"baking {name} from {image}", file=sys.stderr)
        existing = bake(plane, name, image, args.timeout)
    else:
        print(f"reusing {name} ({args.image} has not changed)", file=sys.stderr)
    for old in superseded_disks(disks, args.name, name):
        print(
            f"disk {old} was baked from an older image; delete it once the "
            "worker uses the new disk and no conversion is running on the old one",
            file=sys.stderr,
        )
    disk_id = str(existing["id"])
    if disk_state(existing).lower() not in READY:
        wait_until_ready(plane, disk_id, args.timeout)
    print(disk_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except Exception:
        log.exception("disk setup failed")
        sys.exit(1)
