from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from bluepaper.config import CATALOG_VERSION
from bluepaper.models import Hit, ScanResult
from bluepaper.scan.matcher import AhoCorasick

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_CATALOG_PATH = Path(__file__).with_name("catalog_1.0.0.json")


class ScanTimeout(Exception):
    """Wall-clock cap for the byte scan (ReDoS / exhaustion)."""


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _automaton() -> AhoCorasick:
    catalog = load_catalog()
    patterns: list[tuple[str, bytes]] = []
    for entry in catalog["patterns"]:
        kind = entry.get("kind")
        if kind:
            continue
        pattern_id = str(entry["id"])
        if "hex" in entry:
            patterns.append((pattern_id, bytes.fromhex(str(entry["hex"]))))
            continue
        for literal in entry.get("literals") or []:
            patterns.append((pattern_id, str(literal).encode("utf-8")))
    return AhoCorasick(patterns)


def scan_bytes(data: bytes, *, timeout_seconds: float = 30.0) -> ScanResult:
    deadline = time.monotonic() + timeout_seconds
    if time.monotonic() > deadline:
        raise ScanTimeout()

    catalog = load_catalog()
    version = str(catalog.get("version") or CATALOG_VERSION)
    counts = _automaton().find(data)

    if time.monotonic() > deadline:
        raise ScanTimeout()

    specials = _special_hits(data, catalog["patterns"])
    merged: dict[str, tuple[int, int]] = dict(counts)
    for pattern_id, (count, first) in specials.items():
        prev = merged.get(pattern_id)
        if prev is None:
            merged[pattern_id] = (count, first)
        else:
            merged[pattern_id] = (prev[0] + count, min(prev[1], first))

    hits = [
        Hit(id=pattern_id, count=count, first_offset=first)
        for pattern_id, (count, first) in sorted(merged.items())
        if count > 0
    ]
    return ScanResult(hits=hits, catalog_version=version, timed_out=False)


def _special_hits(
    data: bytes, patterns: list[dict[str, Any]]
) -> dict[str, tuple[int, int]]:
    hits: dict[str, tuple[int, int]] = {}
    kinds = {str(entry["id"]): entry.get("kind") for entry in patterns}
    for pattern_id, kind in kinds.items():
        if kind == "pdf_not_at_zero":
            offset = data.find(_PDF_MAGIC)
            if offset > 0:
                hits[pattern_id] = (1, offset)
        elif kind == "zip_and_pdf":
            zip_at = data.find(_ZIP_MAGIC)
            pdf_at = data.find(_PDF_MAGIC)
            if zip_at >= 0 and pdf_at >= 0:
                hits[pattern_id] = (1, min(zip_at, pdf_at))
    return hits
