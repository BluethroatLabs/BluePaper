from __future__ import annotations

import json
import time
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path
from typing import Any

from bluepaper.config import CATALOG_VERSION
from bluepaper.models import Hit, ScanResult
from bluepaper.scan.matcher import AhoCorasick

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_CATALOG_PATH = Path(__file__).with_name("catalog_1.0.0.json")
# PDF name tokens end at whitespace or a delimiter (ISO 32000).
_NAME_END = frozenset(
    b"\x00\t\n\x0c\r ()<>[]{}/%",
)


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


def _hex_nibble(byte: int) -> int | None:
    if 48 <= byte <= 57:
        return byte - 48
    if 65 <= byte <= 70:
        return byte - 55
    if 97 <= byte <= 102:
        return byte - 87
    return None


def expand_pdf_name_escapes(data: bytes) -> tuple[bytes, list[int]]:
    """Expand ``#HH`` escapes inside PDF names.

    ``/Java#53cript`` is the name ``/JavaScript``. The returned emit indexes
    are positions in the expanded buffer where a three-byte escape became one
    byte, so match offsets can be mapped back to the original file. Streams
    are not decompressed.
    """
    if b"#" not in data:
        return data, []
    out = bytearray()
    emits: list[int] = []
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        out.append(byte)
        index += 1
        if byte != 0x2F:
            continue
        while index < length and data[index] not in _NAME_END:
            if (
                data[index] == 0x23
                and index + 2 < length
                and (high := _hex_nibble(data[index + 1])) is not None
                and (low := _hex_nibble(data[index + 2])) is not None
            ):
                emits.append(len(out))
                out.append((high << 4) | low)
                index += 3
                continue
            out.append(data[index])
            index += 1
    if not emits:
        return data, []
    return bytes(out), emits


def _original_offset(norm_index: int, emits: list[int]) -> int:
    """Map an expanded-buffer index back to the original file."""
    return norm_index + 2 * bisect_left(emits, norm_index)


def scan_bytes(data: bytes, *, timeout_seconds: float = 30.0) -> ScanResult:
    deadline = time.monotonic() + timeout_seconds
    if time.monotonic() > deadline:
        raise ScanTimeout()

    catalog = load_catalog()
    version = str(catalog.get("version") or CATALOG_VERSION)
    normalized, emits = expand_pdf_name_escapes(data)
    counts = _automaton().find(normalized)
    if emits:
        counts = {
            pattern_id: (count, _original_offset(first, emits))
            for pattern_id, (count, first) in counts.items()
        }

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
