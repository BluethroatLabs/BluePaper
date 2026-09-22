from __future__ import annotations

from bluepaper.config import (
    extension_of,
    original_blob_key,
    pdf_blob_key,
    report_blob_key,
    safe_pdf_name,
)
from bluepaper.models import ConversionRecord, ConversionStatus, utc_now
from bluepaper.ocr import is_supported_ocr_lang, ocr_language_codes


def test_extension_of_normalizes_suffix() -> None:
    assert extension_of("Memo.PDF") == ".pdf"
    assert extension_of("photo.JPEG") == ".jpeg"
    assert extension_of(None) == ""
    assert extension_of("") == ""
    assert extension_of("no-suffix") == ""


def test_safe_pdf_name_uses_original_stem() -> None:
    assert safe_pdf_name("doc.pdf") == "doc-safe.pdf"
    assert safe_pdf_name("Quarterly Report.docx") == "Quarterly Report-safe.pdf"
    assert safe_pdf_name("archive.tar.gz") == "archive.tar-safe.pdf"
    assert safe_pdf_name(r"..\secret\passwd.pdf") == "passwd-safe.pdf"
    assert safe_pdf_name("café.pdf") == "café-safe.pdf"
    assert safe_pdf_name("weird:name?.pdf") == "weird_name-safe.pdf"
    assert safe_pdf_name(None) == "document-safe.pdf"
    assert safe_pdf_name("...") == "document-safe.pdf"
    assert safe_pdf_name(f"{'a' * 300}.pdf") == f"{'a' * 180}-safe.pdf"


def test_blob_keys() -> None:
    assert original_blob_key("abc") == "originals/abc"
    assert pdf_blob_key("cnv_1") == "conversions/cnv_1/safe.pdf"
    assert report_blob_key("cnv_1") == "conversions/cnv_1/report.json"


def test_ocr_language_eng_is_supported() -> None:
    codes = ocr_language_codes()
    assert "eng" in codes
    assert is_supported_ocr_lang("eng")
    assert not is_supported_ocr_lang("not-a-lang")


def test_utc_now_is_zulu() -> None:
    stamp = utc_now()
    assert stamp.endswith("Z")
    assert "T" in stamp


def test_record_entity_roundtrip_empty_optionals() -> None:
    record = ConversionRecord(
        id="cnv_entity",
        status=ConversionStatus.queued,
        sha256="d" * 64,
        nbytes=1,
        created_at=utc_now(),
    )
    restored = ConversionRecord.from_entity(record.to_entity())
    assert restored.stage is None
    assert restored.started_at is None
    assert restored.finished_at is None
    assert restored.error is None
    assert restored.ocr_lang is None
    assert restored.scan_completed is False
    assert restored.filename == "upload.bin"
    assert restored.guest is False
    assert restored.status == ConversionStatus.queued
    record.guest = True
    assert ConversionRecord.from_entity(record.to_entity()).guest is True
