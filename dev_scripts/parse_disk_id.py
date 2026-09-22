#!/usr/bin/env python3
"""Print a sandbox disk id from `aca sandboxgroup disk` JSON.

Usage: parse_disk_id.py <name> <list|create>
Reads JSON on stdin. list selects the named disk. create prints the id
from a single create response.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    want = sys.argv[1]
    mode = sys.argv[2]
    raw = sys.stdin.read().strip()
    if not raw:
        return
    data = json.loads(raw)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("value"), list):
        items = data["value"]
    elif isinstance(data, dict) and data.get("id"):
        items = [data]
    else:
        items = []

    if mode == "create" and len(items) == 1 and items[0].get("id"):
        print(items[0]["id"])
        return
    for item in items:
        labels = item.get("labels") or {}
        name = item.get("name") or labels.get("name") or ""
        if name == want and item.get("id"):
            print(item["id"])
            return


if __name__ == "__main__":
    main()
