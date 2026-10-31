from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Protocol, Self

from bluepaper.config import (
    Settings,
    caveat_for,
    original_blob_key,
    pdf_blob_key,
    report_blob_key,
)
from bluepaper.isolation.stop import StopSignal
from bluepaper.models import (
    ConversionOutcome,
    ConversionRecord,
    ConversionStatus,
    Hit,
    QueueLease,
    Report,
    ScanResult,
    utc_now,
)
from bluepaper.scan.scanner import ScanTimeout, scan_bytes
from bluepaper.storage.base import Stores
from dangerzone.document import Document

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
CONVERSION_TIMEOUT_ERROR = "conversion ran past its time budget"


def public_conversion_error(exc: BaseException) -> str:
    """Stable, secret-free error for the conversion status row."""
    if isinstance(exc, (TimeoutError, FutureTimeout)):
        return CONVERSION_TIMEOUT_ERROR
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
    # An exception with no message (``TimeoutError()``) has no first line.
    lines = str(exc).strip().splitlines()
    return lines[0][:240] if lines else "conversion failed"


def _azure_error_code(exc: BaseException) -> str:
    error = getattr(exc, "error", None)
    code = getattr(error, "code", None) or getattr(exc, "code", None)
    return str(code or "")


class IsolationLike(Protocol):
    def convert(
        self,
        document: Document,
        ocr_lang: str | None,
        progress_callback: Callable | None = None,
        stop: StopSignal | None = None,
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
    # Bind a non-optional name. Nested workers do not see the narrowing above.
    current = record
    if current.status in (
        ConversionStatus.cancelled,
        ConversionStatus.failed,
        ConversionStatus.succeeded,
    ):
        stores.queue.complete(lease)
        return True
    if lease.dequeue_count > settings.max_dequeues:
        current.status = ConversionStatus.failed
        current.error = "poisoned queue message"
        current.finished_at = utc_now()
        stores.table.update(current)
        stores.queue.complete(lease)
        return True

    with _LeaseKeeper(stores, lease, settings.visibility_timeout_seconds) as keeper:
        current.status = ConversionStatus.running
        current.started_at = current.started_at or utc_now()
        current.stage = "doc_to_pixels"
        stores.table.update(current)

        try:
            _run(settings, stores, isolation, current)
        except Exception:
            # A crash must not leave the row ``running`` with no worker on it.
            # It would count against its owner's limit until the lease ran out.
            _release_crashed(settings, stores, keeper.finish(), current.id)
            raise
        stores.queue.complete(keeper.finish())
    return True


class _LeaseKeeper:
    """Renews the queue lease while a job runs.

    Without it, a job that outlives ``visibility_timeout_seconds`` makes the
    message visible again and a second worker converts the same upload.
    """

    def __init__(self, stores: Stores, lease: QueueLease, visibility_timeout: int):
        self._queue = stores.queue
        self._lease = lease
        self._timeout = visibility_timeout
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._renew_until_done,
            name=f"lease-{lease.conversion_id}",
            daemon=True,
        )

    def finish(self) -> QueueLease:
        """Stop renewing and return the current lease, to complete it."""
        self._done.set()
        if self._thread.is_alive():
            self._thread.join()
        return self._lease

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.finish()

    def _renew_until_done(self) -> None:
        # A third of the timeout leaves two more tries before the lease lapses.
        interval = max(self._timeout / 3, 0.1)
        while not self._done.wait(interval):
            try:
                renewed = self._queue.extend(self._lease, self._timeout)
            except Exception:
                log.exception(
                    "lease renewal failed conversion_id=%s", self._lease.conversion_id
                )
                continue
            if renewed is None:
                log.warning("lease lost conversion_id=%s", self._lease.conversion_id)
                return
            self._lease = renewed


def _run(
    settings: Settings,
    stores: Stores,
    isolation: IsolationLike,
    current: ConversionRecord,
) -> None:
    original = stores.blobs.get(original_blob_key(current.sha256))
    if original is None:
        current.status = ConversionStatus.failed
        current.error = "original blob missing"
        current.finished_at = utc_now()
        stores.table.update(current)
        return
    source = original

    scan_hits: list[Hit] | None = None
    scan_version = "1.0.0"
    pdf_bytes: bytes | None = None
    pages: int | None = None
    conv_error: str | None = None
    # Set once this job stops waiting. It deletes the sandbox and kills OCR
    # workers. A conversion still winding down may report progress, and must
    # not write ``running`` back.
    stop = StopSignal()

    def on_progress(_err: object, text: str, _pct: object) -> None:
        if not stop.is_set():
            _update_stage(stores, current.id, text)

    def do_scan() -> ScanResult:
        return scan_bytes(source, timeout_seconds=settings.scan_timeout_seconds)

    def do_convert() -> tuple[bytes, int] | None:
        latest = stores.table.get(current.id)
        if latest is not None and latest.status == ConversionStatus.cancelled:
            return None
        return _convert_document(
            isolation, source, current.filename, current.ocr_lang, on_progress, stop
        )

    # Not a ``with`` block: leaving one waits for every thread. A conversion
    # that ignores ``stop`` must not hold this job (and the row) forever.
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        scan_future = pool.submit(do_scan)
        conv_future = pool.submit(do_convert)
        try:
            scanned = scan_future.result(timeout=settings.scan_timeout_seconds + 5)
            scan_hits = scanned.hits
            scan_version = scanned.catalog_version
        except ScanTimeout:
            log.exception("scan timed out conversion_id=%s", current.id)
        except Exception:
            log.exception("scan failed conversion_id=%s", current.id)
        try:
            converted = conv_future.result(timeout=settings.conversion_timeout_seconds)
            if converted is not None:
                pdf_bytes, pages = converted
        except FutureTimeout:
            log.warning(
                "conversion ran past %.0fs conversion_id=%s",
                settings.conversion_timeout_seconds,
                current.id,
            )
            conv_error = CONVERSION_TIMEOUT_ERROR
        except Exception as exc:
            log.exception("conversion failed conversion_id=%s", current.id)
            conv_error = public_conversion_error(exc)
    finally:
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)

    latest = stores.table.get(current.id)
    if latest is None or latest.status == ConversionStatus.cancelled:
        return
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


def _release_crashed(
    settings: Settings,
    stores: Stores,
    lease: QueueLease,
    conversion_id: str,
) -> None:
    """Take a crashed job's row out of ``running``. Never raises over the crash.

    The row goes back to ``queued`` for the redelivery, or to ``failed`` on the
    last delivery the poison check would allow.
    """
    try:
        record = stores.table.get(conversion_id)
        if record is None or record.status != ConversionStatus.running:
            return
        record.stage = None
        if lease.dequeue_count >= settings.max_dequeues:
            record.status = ConversionStatus.failed
            record.error = "conversion worker crashed"
            record.finished_at = utc_now()
            stores.table.update(record)
            stores.queue.complete(lease)
            return
        record.status = ConversionStatus.queued
        stores.table.update(record)
    except Exception:
        log.exception("could not release conversion_id=%s after a crash", conversion_id)


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
    stop: StopSignal,
) -> tuple[bytes, int]:
    suffix = Path(filename).suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        dst = Path(tmp) / "output-safe.pdf"
        src.write_bytes(original)
        document = Document(str(src), str(dst))
        isolation.convert(document, ocr_lang, progress_callback, stop=stop)
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
    if pdf_bytes is not None and conv_error is None:
        conversion = ConversionOutcome(
            status="succeeded",
            pages=pages,
            ocr_lang=record.ocr_lang,
            output_bytes=len(pdf_bytes),
        )
        # Non-empty hits mean the byte scan matched. They do not prove an
        # active construct was present.
        justified = len(hits) > 0
        caveat = caveat_for(succeeded=True, hit_count=len(hits))
    else:
        conversion = ConversionOutcome(
            status="failed",
            pages=pages,
            ocr_lang=record.ocr_lang,
            output_bytes=None,
        )
        justified = False
        caveat = caveat_for(succeeded=False, hit_count=len(hits))
    return Report(
        conversion_id=record.id,
        sha256=record.sha256,
        catalog_version=catalog_version,
        conversion_justified=justified,
        caveat=caveat,
        hits=hits,
        conversion=conversion,
    )
