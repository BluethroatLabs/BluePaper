#!/usr/bin/env python3
"""Reuse or bake a sandbox disk image and print its id.

Uses the Azure CLI for a data-plane token. The ``aca`` CLI is not required.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = "2026-02-01-preview"
TOKEN_RESOURCE = "https://dynamicsessions.io"
READY = {"ready", "succeeded"}
FAILED = {"failed"}


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

    def request(self, method: str, path: str, body: dict | None = None) -> object:
        url = f"{self.endpoint}{path}"
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api-version={API_VERSION}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self._token_value()}")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
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

    def create_disk(self, name: str, image: str) -> dict:
        created = self.request(
            "PUT",
            f"{self.group_path}/diskimages",
            {"image": {"base": image}, "labels": {"name": name}},
        )
        if not isinstance(created, dict) or not created.get("id"):
            raise RuntimeError("disk create did not return an id")
        return created

    def get_disk(self, disk_id: str) -> dict:
        item = self.request("GET", f"{self.group_path}/diskimages/{disk_id}")
        if not isinstance(item, dict):
            raise RuntimeError("disk get returned an unexpected payload")
        return item


def wait_until_ready(plane: DataPlane, disk_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        item = plane.get_disk(disk_id)
        state = disk_state(item).lower()
        if state in READY:
            return
        if state in FAILED:
            message = (item.get("status") or {}).get("message") or "disk build failed"
            raise RuntimeError(str(message))
        if time.monotonic() >= deadline:
            raise RuntimeError(f"disk {disk_id} was not ready within {int(timeout)}s")
        print(f"disk {disk_id} is {state or 'pending'}", file=sys.stderr)
        time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--sandbox-group", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    plane = DataPlane(
        f"https://management.{args.region}.azuredevcompute.io",
        (
            f"/subscriptions/{args.subscription}"
            f"/resourceGroups/{args.resource_group}"
            f"/sandboxGroups/{args.sandbox_group}"
        ),
    )
    existing = find_disk(plane.list_disks(), args.name)
    if existing is None:
        print(f"baking {args.name} from {args.image}", file=sys.stderr)
        existing = plane.create_disk(args.name, args.image)
    else:
        print(f"reusing {args.name}", file=sys.stderr)
    disk_id = str(existing["id"])
    if disk_state(existing).lower() not in READY:
        wait_until_ready(plane, disk_id, args.timeout)
    print(disk_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
