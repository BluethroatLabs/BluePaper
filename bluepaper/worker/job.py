from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from bluepaper.config import (
    ZERO_HITS_CAVEAT,
    Settings,
    original_blob_key,
    pdf_blob_key,
    report_blob_key,
)
from bluepaper.models import (
    ConversionOutcome,
    ConversionRecord,
    ConversionStatus,
    Hit,
    Report,
    utc_now,
)
from bluepaper.scan.scanner import ScanTimeout, scan_bytes
from bluepaper.storage.base import Stores

log = logging.getLogger("bluepaper.worker")

_CAPACITY_MARKERS = (
    "429",
    "toomanyrequests",
    "too many requests",
    "throttl",
    "quota exceeded",
    "insufficient capacity",
)
_DISK_MARKERS = ("diskimagenotfound", "disk image")


def public_conversion_error(exc: BaseException) -> str:
    """Stable, secret-free error for the conversion status row."""
    hay = f"{type(exc).__name__} {_azure_error_code(exc)} {exc}".lower()
    if any(marker in hay for marker in _DISK_MARKERS):
        return "conversion sandbox disk is not configured"
    if any(marker in hay for marker in _CAPACITY_MARKERS):
        return "conversion capacity is full"
    if type(exc).__name__ in {
        "HttpResponseError",
        "ServiceRequestError",
        "ServiceResponseError",
    }:
        return "conversion sandbox request failed"
    text = str(exc).strip().splitlines()[0][:240]
    return text or "conversion failed"


def _azure_error_code(exc: BaseException) -> str:
    error = getattr(exc, "error", None)
    code = getattr(error, "code", None) or getattr(exc, "code", None)
    return str(code or "")


class IsolationLike(Protocol):
    def convert(
        self,
        document: object,
        ocr_lang: str | None,
        progress_callback: Callable | None = None,
    ) -> None: ...


def get_isolation(settings: Settings) -> IsolationLike:
    if settings.isolation == "dummy":
        from bluepaper.isolation.dummy import InProcessDummy

        return InProcessDummy()
    from bluepaper.isolation.aca import AcaIsolationProvider

    return AcaIsolationProvider.from_settings(settings)


def process_one(
    settings: Settings,
    stores: Stores,
    isolation: IsolationLike | None = None,
) -> bool:
    isolation = isolation or get_isolation(settings)
    lease = stores.queue.lease(settings.visibility_timeout_seconds)
    if lease is None:
        return False

    record = stores.table.get(lease.conversion_id)
    if record is None:
        stores.queue.complete(lease)
        return True
    if record.status == ConversionStatus.cancelled:
        stores.queue.complete(lease)
        return True
    if lease.dequeue_count > settings.max_dequeues:
        record.status = ConversionStatus.failed
        record.error = "poisoned queue message"
        record.finished_at = utc_now()
        stores.table.update(record)
        stores.queue.complete(lease)
        return True

    record.status = ConversionStatus.running
    record.started_at = record.started_at or utc_now()
    record.stage = "doc_to_pixels"
    stores.table.update(record)

    original = stores.blobs.get(original_blob_key(record.sha256))
    if original is None:
        record.status = ConversionStatus.failed
        record.error = "original blob missing"
        record.finished_at = utc_now()
        stores.table.update(record)
        stores.queue.complete(lease)
        return True

    scan_hits: list[Hit] | None = None
    scan_version = "1.0.0"
    pdf_bytes: bytes | None = None
    pages: int | None = None
    conv_error: str | None = None

    def do_scan() -> None:
        nonlocal scan_hits, scan_version
        result = scan_bytes(original, timeout_seconds=settings.scan_timeout_seconds)
        scan_hits = result.hits
        scan_version = result.catalog_version

    def do_convert() -> None:
        nonlocal pdf_bytes, pages, conv_error
        latest = stores.table.get(record.id)
        if latest is not None and latest.status == ConversionStatus.cancelled:
            return
        pdf_bytes, pages = _convert_document(
            isolation,
            original,
            record.filename,
            record.ocr_lang,
            lambda _err, text, _pct: _update_stage(stores, record.id, text),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        scan_future = pool.submit(do_scan)
        conv_future = pool.submit(do_convert)
        try:
            scan_future.result(timeout=settings.scan_timeout_seconds + 5)
        except ScanTimeout:
            log.exception("scan timed out conversion_id=%s", record.id)
            scan_hits = None
        except Exception:
            log.exception("scan failed conversion_id=%s", record.id)
            scan_hits = None
        try:
            conv_future.result(timeout=settings.conversion_timeout_seconds)
        except Exception as exc:
            log.exception("conversion failed conversion_id=%s", record.id)
            conv_error = public_conversion_error(exc)

    latest = stores.table.get(record.id)
    if latest is not None and latest.status == ConversionStatus.cancelled:
        stores.queue.complete(lease)
        return True
    assert latest is not None
    record = latest

    if scan_hits is not None:
        report = _build_report(
            record,
            scan_hits,
            scan_version,
            pdf_bytes,
            pages,
            conv_error,
        )
        stores.blobs.put(
            report_blob_key(record.id),
            report.model_dump_json().encode("utf-8"),
            "application/json",
        )
        record.scan_completed = True

    if pdf_bytes is not None and conv_error is None:
        stores.blobs.put(pdf_blob_key(record.id), pdf_bytes, "application/pdf")
        record.status = ConversionStatus.succeeded
        record.stage = None
        record.error = None
    else:
        record.status = ConversionStatus.failed
        record.error = conv_error or "conversion failed"
    record.finished_at = utc_now()
    stores.table.update(record)
    stores.queue.complete(lease)
    return True


def _update_stage(stores: Stores, conversion_id: str, text: str) -> None:
    record = stores.table.get(conversion_id)
    if record is None or record.status != ConversionStatus.running:
        return
    if "to PDF" in text or "searchable PDF" in text:
        record.stage = "pixels_to_pdf"
        stores.table.update(record)


def _convert_document(
    isolation: IsolationLike,
    original: bytes,
    filename: str,
    ocr_lang: str | None,
    progress_callback: Callable | None,
) -> tuple[bytes, int]:
    from dangerzone.document import Document

    suffix = Path(filename).suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        dst = Path(tmp) / "output-safe.pdf"
        src.write_bytes(original)
        document = Document(str(src), str(dst))
        isolation.convert(document, ocr_lang, progress_callback)
        if not document.is_safe() or not dst.is_file():
            raise RuntimeError("conversion failed")
        pdf_bytes = dst.read_bytes()
        return pdf_bytes, _pdf_page_count(pdf_bytes)


def _pdf_page_count(pdf_bytes: bytes) -> int:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def _build_report(
    record: ConversionRecord,
    hits: list[Hit],
    catalog_version: str,
    pdf_bytes: bytes | None,
    pages: int | None,
    conv_error: str | None,
) -> Report:
    justified = len(hits) > 0
    if pdf_bytes is not None and conv_error is None:
        conversion = ConversionOutcome(
            status="succeeded",
            pages=pages,
            ocr_lang=record.ocr_lang,
            output_bytes=len(pdf_bytes),
        )
    else:
        conversion = ConversionOutcome(
            status="failed",
            pages=pages,
            ocr_lang=record.ocr_lang,
            output_bytes=None,
        )
    return Report(
        conversion_id=record.id,
        sha256=record.sha256,
        catalog_version=catalog_version,
        conversion_justified=justified,
        caveat=None if justified else ZERO_HITS_CAVEAT,
        hits=hits,
        conversion=conversion,
    )
