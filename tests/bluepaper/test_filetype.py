"""Declared extension versus magic bytes.

The shape under test is the one from CVE-2026-125396 ("Upload2Shell"): a file
carrying a trusted extension whose bytes are PostScript. BluePaper is not
affected by that Ghostscript bug — it renders with PyMuPDF — so these tests
assert the *reporting* contract, not a block: a mismatch is surfaced and the
conversion still runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bluepaper.config import (
    SUPPORTED_EXTENSIONS,
    TYPE_MATCH,
    TYPE_MISMATCH,
    TYPE_UNRECOGNISED,
    ZERO_HITS_CAVEAT,
    Settings,
    caveat_for,
)
from bluepaper.filetype import (
    BMP,
    GIF,
    JPEG,
    NETPBM,
    OLE2,
    PDF,
    PNG,
    POSTSCRIPT,
    PREFIX_BYTES,
    RTF,
    SVG,
    TIFF,
    ZIP,
    check_declared_type,
    detect_family,
    expected_families,
)
from bluepaper.models import Report, TypeCheck, normalize_failed_report
from bluepaper.storage.base import Stores
from bluepaper.worker.job import process_one
from tests.bluepaper.conftest import auth

# Plain PostScript. Nothing exploitable here — the file *type* is the point.
POSTSCRIPT_BYTES = (
    b"%!PS-Adobe-3.0\n"
    b"%%BoundingBox: 0 0 10 10\n"
    b"/Times-Roman findfont 12 scalefont setfont\n"
    b"showpage\n"
)
PDF_BYTES = b"%PDF-1.4\nplain text document\n"


# --------------------------------------------------------------------------
# detect_family
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "family"),
    [
        (PDF_BYTES, PDF),
        (POSTSCRIPT_BYTES, POSTSCRIPT),
        (b"\xc5\xd0\xd3\xc6\x00\x00", POSTSCRIPT),  # binary/DOS EPS wrapper
        (b"PK\x03\x04\x14\x00", ZIP),
        (b"PK\x05\x06", ZIP),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", OLE2),
        (b"{\\rtf1\\ansi\\deff0", RTF),
        (b"\x89PNG\r\n\x1a\n", PNG),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", JPEG),
        (b"GIF89a\x01\x00", GIF),
        (b"GIF87a\x01\x00", GIF),
        (b"BM\x8a\x00", BMP),
        (b"II*\x00\x08", TIFF),
        (b"MM\x00*\x00", TIFF),
        (b"P6\n4 4\n255\n", NETPBM),
        (b"P1 1 1\n0\n", NETPBM),
        (b'<?xml version="1.0"?>\n<svg xmlns="..."/>', SVG),
        (b"<svg xmlns='...'></svg>", SVG),
        (b"\xef\xbb\xbf  \n<svg/>", SVG),  # BOM plus leading whitespace
    ],
)
def test_detect_family_recognises_signatures(data: bytes, family: str) -> None:
    assert detect_family(data) == family


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"%P",
        b"totally unremarkable text",
        b"P9\n",  # not a Netpbm magic number
        b"\x00" * (PREFIX_BYTES + 8) + b"%PDF",  # signature past the prefix
    ],
)
def test_detect_family_is_quiet_when_unsure(data: bytes) -> None:
    assert detect_family(data) is None


def test_detect_family_reads_only_the_head() -> None:
    """A huge upload is classified from its prefix, not walked."""
    assert detect_family(PDF_BYTES + b"\x00" * (8 * 1024 * 1024)) == PDF


# --------------------------------------------------------------------------
# check_declared_type
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (PDF_BYTES, "invoice.pdf"),
        (PDF_BYTES, "INVOICE.PDF"),  # extension case is normalised
        (b"\xff\xd8\xff\xe0JFIF", "photo.jpg"),
        (b"\xff\xd8\xff\xe0JFIF", "photo.jpeg"),
        (b"PK\x03\x04", "report.docx"),
        (b"PK\x03\x04", "book.epub"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "legacy.doc"),
        (b"P6\n1 1\n255\n", "img.ppm"),
        (b"<svg/>", "icon.svg"),
    ],
)
def test_matching_bytes_report_a_match(data: bytes, filename: str) -> None:
    check = check_declared_type(data, filename)
    assert check.status == TYPE_MATCH
    assert check.caveat is None


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        # OOXML and RTF under a legacy Office extension are common and benign.
        (b"PK\x03\x04", "memo.doc"),
        (b"{\\rtf1\\ansi", "memo.doc"),
        (b"PK\x03\x04", "sheet.xls"),
        (b"PK\x03\x04", "deck.ppt"),
    ],
)
def test_legacy_office_tolerates_modern_bytes(data: bytes, filename: str) -> None:
    assert check_declared_type(data, filename).status == TYPE_MATCH


@pytest.mark.parametrize(
    "filename",
    ["invoice.pdf", "photo.jpg", "scan.png", "page.tiff", "book.epub"],
)
def test_postscript_under_a_trusted_extension_is_a_mismatch(filename: str) -> None:
    """The advisory's precondition: trusted extension, PostScript bytes."""
    check = check_declared_type(POSTSCRIPT_BYTES, filename)
    assert check.status == TYPE_MISMATCH
    assert check.detected_family == POSTSCRIPT
    assert check.caveat is not None
    assert "PostScript/EPS" in check.caveat
    assert check.declared_extension in check.caveat


def test_mismatch_names_both_sides() -> None:
    check = check_declared_type(b"\x89PNG\r\n\x1a\n", "icon.svg")
    assert check.status == TYPE_MISMATCH
    assert check.declared_family == SVG
    assert check.detected_family == PNG
    assert check.caveat is not None
    assert "a PNG image" in check.caveat
    assert ".svg" in check.caveat


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (b"unknown bytes entirely", "thing.pdf"),  # bytes we cannot label
        (b"", "empty.pdf"),
        (PDF_BYTES, "noextension"),  # nothing declared to compare against
        (PDF_BYTES, ""),
    ],
)
def test_unrecognised_is_never_a_mismatch(data: bytes, filename: str) -> None:
    check = check_declared_type(data, filename)
    assert check.status == TYPE_UNRECOGNISED
    assert check.caveat is None


def test_every_supported_extension_has_an_expectation() -> None:
    """A type we accept but have no expectation for would silently no-op."""
    missing = sorted(ext for ext in SUPPORTED_EXTENSIONS if not expected_families(ext))
    assert missing == []


# --------------------------------------------------------------------------
# Caveat composition
# --------------------------------------------------------------------------
def test_caveat_for_appends_the_mismatch_sentence() -> None:
    plain = caveat_for(succeeded=True, hit_count=0)
    with_mismatch = caveat_for(succeeded=True, hit_count=0, mismatch="Bytes differ.")
    assert plain == ZERO_HITS_CAVEAT
    assert with_mismatch == f"{ZERO_HITS_CAVEAT} Bytes differ."


def test_failed_report_keeps_the_mismatch_sentence() -> None:
    """Normalising a failed report must not drop the type finding."""
    check = check_declared_type(POSTSCRIPT_BYTES, "invoice.pdf")
    report = Report(
        conversion_id="c1",
        sha256="0" * 64,
        conversion_justified=True,
        caveat="stale success claim",
        type_check=check,
    )
    normalized = Report.model_validate_json(
        normalize_failed_report(report.model_dump_json().encode("utf-8"))
    )
    assert normalized.conversion_justified is False
    assert normalized.caveat is not None
    assert "PostScript/EPS" in normalized.caveat
    assert normalized.type_check is not None
    assert normalized.type_check.status == TYPE_MISMATCH


def test_report_without_a_type_check_round_trips() -> None:
    """Reports written before this field existed still normalise."""
    payload = (
        b'{"conversion_id":"c1","sha256":"' + b"0" * 64 + b'",'
        b'"conversion_justified":true,"hits":[]}'
    )
    normalized = Report.model_validate_json(normalize_failed_report(payload))
    assert normalized.type_check is None
    assert normalized.caveat is not None


def test_type_check_defaults_to_unrecognised() -> None:
    assert TypeCheck(declared_extension=".pdf").status == TYPE_UNRECOGNISED


# --------------------------------------------------------------------------
# End to end through the worker
# --------------------------------------------------------------------------
def _submit(client: TestClient, data: bytes, name: str) -> str:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": (name, data, "application/octet-stream")},
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_mismatch_is_reported_and_conversion_still_runs(
    client: TestClient, stores: Stores, settings: Settings
) -> None:
    conversion_id = _submit(client, POSTSCRIPT_BYTES, "invoice.pdf")
    assert process_one(settings, stores) is True

    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth())
    assert status.json()["status"] == "succeeded"

    report = client.get(
        f"/v1/conversions/{conversion_id}/report", headers=auth()
    ).json()
    assert report["type_check"]["status"] == TYPE_MISMATCH
    assert report["type_check"]["detected_family"] == POSTSCRIPT
    assert report["type_check"]["declared_extension"] == ".pdf"
    assert "PostScript/EPS" in report["caveat"]


def test_matching_upload_adds_no_caveat_sentence(
    client: TestClient, stores: Stores, settings: Settings
) -> None:
    conversion_id = _submit(client, PDF_BYTES, "invoice.pdf")
    assert process_one(settings, stores) is True

    report = client.get(
        f"/v1/conversions/{conversion_id}/report", headers=auth()
    ).json()
    assert report["type_check"]["status"] == TYPE_MATCH
    assert report["caveat"] == ZERO_HITS_CAVEAT
