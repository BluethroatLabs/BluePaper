"""Declared file type versus actual magic bytes.

Defense in depth, and only that. BluePaper's security boundary is the
disposable, egress-denied sandbox (ARCHITECTURE.md §3): the converters are
assumed to be exploitable, so nothing here is load-bearing for isolation and
nothing here decides whether a conversion is safe.

What the check buys is a *report*. The advisory class it answers to is
"trusted extension, hostile bytes": CVE-2026-125396 is a use-after-free in
Ghostscript's Type 1 font loader whose precondition is a file carrying a
trusted extension (``.pdf``, ``.jpg``) whose bytes are PostScript, reaching a
renderer that delegates to Ghostscript. BluePaper is not affected — Dangerzone
replaced the Ghostscript-era pipeline (``ps2pdf``) with PyMuPDF, and no
Ghostscript, Pillow, ImageMagick, or libvips is in the tree — but the
*precondition* was invisible here: nothing compared the name against the
bytes, so a caller who uploaded ``invoice.pdf`` containing PostScript was
never told. Now they are, and the next renderer CVE in this class does not
depend on the sandbox being perfect to be noticed.

The check is deliberately shallow, matching the byte scan's posture
(ARCHITECTURE.md §4): magic bytes only, a fixed-length prefix, no parsing, no
decompression, no new dependency. It reads at most :data:`PREFIX_BYTES` and
compares two labels, so it cannot time out or be driven into quadratic
behaviour — resource exhaustion is in the threat model.

A mismatch never refuses a conversion. Callers send BluePaper documents
precisely *because* they do not trust them, and mislabelled files are common
and usually innocent: a ``.doc`` that is really RTF, OOXML saved under a
legacy extension. Refusing would deny the service its purpose, so a mismatch
is recorded and surfaced in the report's caveat instead.
"""

from __future__ import annotations

from bluepaper.config import (
    TYPE_MATCH,
    TYPE_MISMATCH,
    TYPE_UNRECOGNISED,
    extension_of,
)
from bluepaper.models import TypeCheck

#: Bytes read from the head of an upload. The longest signature is 8 bytes;
#: the slack lets a byte-order mark and a leading whitespace run be skipped
#: for the text formats without a second look at the data.
PREFIX_BYTES = 64

PDF = "pdf"
POSTSCRIPT = "postscript"
ZIP = "zip"
OLE2 = "ole2"
RTF = "rtf"
PNG = "png"
JPEG = "jpeg"
GIF = "gif"
BMP = "bmp"
TIFF = "tiff"
NETPBM = "netpbm"
SVG = "svg"

#: ``(family, signature)`` at offset 0, longest signature first where two
#: could both match a prefix.
_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    (PNG, b"\x89PNG\r\n\x1a\n"),
    (OLE2, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    # DOS/binary EPS wrapper: a PostScript program behind a 4-byte header.
    (POSTSCRIPT, b"\xc5\xd0\xd3\xc6"),
    (PDF, b"%PDF"),
    (POSTSCRIPT, b"%!PS"),
    (ZIP, b"PK\x03\x04"),
    (ZIP, b"PK\x05\x06"),
    (ZIP, b"PK\x07\x08"),
    (GIF, b"GIF87a"),
    (GIF, b"GIF89a"),
    (TIFF, b"II*\x00"),
    (TIFF, b"MM\x00*"),
    (JPEG, b"\xff\xd8\xff"),
    (RTF, b"{\\rtf"),
    (BMP, b"BM"),
)

#: Byte-order marks skipped before looking for an XML/SVG opening tag.
_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_XML_STARTS = (b"<?xml", b"<svg", b"<!doctype svg")

#: Declared extension -> the families whose bytes are consistent with it.
#:
#: The legacy Office extensions accept ``zip`` as well as ``ole2``: saving
#: OOXML under ``.doc`` / ``.xls`` / ``.ppt`` is common, LibreOffice opens it,
#: and flagging it would make the mismatch signal noise. ``.doc`` also accepts
#: RTF for the same reason.
_EXPECTED: dict[str, frozenset[str]] = {
    ".pdf": frozenset({PDF}),
    ".docx": frozenset({ZIP}),
    ".docm": frozenset({ZIP}),
    ".xlsx": frozenset({ZIP}),
    ".pptx": frozenset({ZIP}),
    ".odt": frozenset({ZIP}),
    ".ods": frozenset({ZIP}),
    ".odp": frozenset({ZIP}),
    ".odg": frozenset({ZIP}),
    ".hwpx": frozenset({ZIP}),
    ".epub": frozenset({ZIP}),
    ".doc": frozenset({OLE2, ZIP, RTF}),
    ".xls": frozenset({OLE2, ZIP}),
    ".ppt": frozenset({OLE2, ZIP}),
    ".hwp": frozenset({OLE2}),
    ".png": frozenset({PNG}),
    ".jpg": frozenset({JPEG}),
    ".jpeg": frozenset({JPEG}),
    ".gif": frozenset({GIF}),
    ".bmp": frozenset({BMP}),
    ".tif": frozenset({TIFF}),
    ".tiff": frozenset({TIFF}),
    ".pnm": frozenset({NETPBM}),
    ".pbm": frozenset({NETPBM}),
    ".ppm": frozenset({NETPBM}),
    ".svg": frozenset({SVG}),
}

_NETPBM_DIGITS = frozenset(b"123456")
_WHITESPACE = frozenset(b" \t\r\n")


def _is_netpbm(head: bytes) -> bool:
    """``P1``-``P6`` followed by whitespace (the Netpbm header)."""
    return (
        len(head) >= 3
        and head[0] == 0x50  # "P"
        and head[1] in _NETPBM_DIGITS
        and head[2] in _WHITESPACE
    )


def _is_xml(head: bytes) -> bool:
    text = head
    for bom in _BOMS:
        if text.startswith(bom):
            text = text[len(bom) :]
            break
    lowered = text.lstrip(bytes(_WHITESPACE)).lower()
    return any(lowered.startswith(start) for start in _XML_STARTS)


def detect_family(data: bytes) -> str | None:
    """The family of ``data`` from its magic bytes, or ``None`` if unknown.

    ``None`` is the common case for formats this table does not cover and for
    truncated uploads. It is never reported as a mismatch.
    """
    head = bytes(data[:PREFIX_BYTES])
    for family, signature in _SIGNATURES:
        if head.startswith(signature):
            return family
    if _is_netpbm(head):
        return NETPBM
    if _is_xml(head):
        return SVG
    return None


def expected_families(extension: str) -> frozenset[str]:
    """Families consistent with ``extension``; empty when it is unknown."""
    return _EXPECTED.get(extension, frozenset())


def check_declared_type(data: bytes, filename: str | None) -> TypeCheck:
    """Compare ``filename``'s extension against ``data``'s magic bytes.

    The result is informational: a ``TYPE_MISMATCH`` status is surfaced in the
    report caveat and logged, and never blocks a conversion.
    """
    extension = extension_of(filename)
    expected = expected_families(extension)
    detected = detect_family(data)
    declared = next(iter(expected)) if len(expected) == 1 else None

    if detected is None or not expected:
        # Nothing to compare: either the bytes match no signature we know, or
        # the extension is not one we have an expectation for.
        status = TYPE_UNRECOGNISED
    elif detected in expected:
        status = TYPE_MATCH
    else:
        status = TYPE_MISMATCH

    return TypeCheck(
        declared_extension=extension,
        declared_family=declared,
        detected_family=detected,
        status=status,
    )
