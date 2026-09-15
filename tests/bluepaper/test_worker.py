import threading
from pathlib import Path

from bluepaper.config import ZERO_HITS_CAVEAT, original_blob_key, report_blob_key
from bluepaper.isolation.aca import AcaIsolationProvider
from bluepaper.isolation.dummy import InProcessDummy
from bluepaper.models import ConversionRecord, ConversionStatus, utc_now
from bluepaper.scan.scanner import ScanTimeout
from bluepaper.worker.job import (
    _update_stage,
    get_isolation,
    process_one,
    public_conversion_error,
)
from tests.bluepaper.conftest import auth


class FailingIsolation:
    def convert(self, document, ocr_lang, progress_callback=None) -> None:
        document.mark_as_converting()
        document.mark_as_failed()


def _submit(client, data: bytes, name: str = "doc.pdf") -> str:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": (name, data, "application/pdf")},
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_dummy_convert_success_without_hits(client, stores, settings) -> None:
    data = b"%PDF-1.4\nplain text document\n"
    conversion_id = _submit(client, data)
    assert process_one(settings, stores) is True

    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth())
    assert status.json()["status"] == ConversionStatus.succeeded.value

    report = client.get(f"/v1/conversions/{conversion_id}/report", headers=auth())
    assert report.status_code == 200
    body = report.json()
    assert body["conversion_justified"] is False
    assert body["caveat"] == ZERO_HITS_CAVEAT
    assert body["conversion"]["status"] == "succeeded"
    assert body["conversion"]["pages"] >= 1
    assert body["catalog_version"] == "1.0.0"

    pdf = client.get(f"/v1/conversions/{conversion_id}/pdf", headers=auth())
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_dummy_convert_justified_when_javascript_present(
    client, stores, settings
) -> None:
    data = b"%PDF-1.4\n<< /OpenAction 1 0 R /JavaScript 2 0 R >>\n"
    conversion_id = _submit(client, data)
    assert process_one(settings, stores) is True
    body = client.get(
        f"/v1/conversions/{conversion_id}/report", headers=auth()
    ).json()
    assert body["conversion_justified"] is True
    assert body["caveat"] is None
    hit_ids = {hit["id"] for hit in body["hits"]}
    assert "pdf.javascript" in hit_ids


def test_failed_convert_still_returns_report(client, stores, settings) -> None:
    data = b"%PDF-1.4\n/Launch /JavaScript\n"
    conversion_id = _submit(client, data)
    assert process_one(settings, stores, FailingIsolation()) is True

    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.failed.value
    assert status["error"]

    report = client.get(f"/v1/conversions/{conversion_id}/report", headers=auth())
    assert report.status_code == 200
    body = report.json()
    assert body["conversion_justified"] is True
    assert body["conversion"]["status"] == "failed"

    pdf = client.get(f"/v1/conversions/{conversion_id}/pdf", headers=auth())
    assert pdf.status_code == 409


def test_cancel_queued_job(client, stores, settings, sample_pdf: str) -> None:
    data = Path(sample_pdf).read_bytes()
    conversion_id = _submit(client, data, "sample.pdf")
    deleted = client.delete(f"/v1/conversions/{conversion_id}", headers=auth())
    assert deleted.status_code == 204
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.cancelled.value
    assert process_one(settings, stores) is True
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.cancelled.value


def test_delete_succeeded_removes_row(client, stores, settings) -> None:
    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    assert process_one(settings, stores) is True
    deleted = client.delete(f"/v1/conversions/{conversion_id}", headers=auth())
    assert deleted.status_code == 204
    assert (
        client.get(f"/v1/conversions/{conversion_id}", headers=auth()).status_code
        == 404
    )


def test_empty_queue_returns_false(settings, stores) -> None:
    assert process_one(settings, stores) is False


def test_missing_record_completes_lease(settings, stores) -> None:
    stores.queue.enqueue("cnv_missing")
    assert process_one(settings, stores) is True
    assert stores.queue.lease(1) is None


def test_poisoned_queue_message(client, stores, settings) -> None:
    settings.max_dequeues = 0
    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    assert process_one(settings, stores) is True
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.failed.value
    assert status["error"] == "poisoned queue message"


def test_missing_original_blob(client, stores, settings) -> None:
    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    record = stores.table.get(conversion_id)
    assert record is not None
    stores.blobs.delete(original_blob_key(record.sha256))
    assert process_one(settings, stores) is True
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.failed.value
    assert status["error"] == "original blob missing"


def test_scan_timeout_still_converts(
    client, stores, settings, monkeypatch
) -> None:
    def boom(*args, **kwargs):
        raise ScanTimeout()

    monkeypatch.setattr("bluepaper.worker.job.scan_bytes", boom)
    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    assert process_one(settings, stores) is True
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.succeeded.value
    report = client.get(f"/v1/conversions/{conversion_id}/report", headers=auth())
    assert report.status_code == 409
    pdf = client.get(f"/v1/conversions/{conversion_id}/pdf", headers=auth())
    assert pdf.status_code == 200


def test_public_conversion_error_hides_azure_class_names() -> None:
    class HttpResponseError(Exception):
        pass

    disk = HttpResponseError(
        "(DiskImageNotFound) Disk image with name 'python' not found"
    )
    disk.error = type("E", (), {"code": "DiskImageNotFound"})()
    assert public_conversion_error(disk) == "conversion sandbox disk is not configured"

    busy = HttpResponseError("429 TooManyRequests")
    assert public_conversion_error(busy) == "conversion capacity is full"

    other = HttpResponseError("backend 500")
    assert public_conversion_error(other) == "conversion sandbox request failed"
    assert public_conversion_error(ValueError("pages overflow")) == "pages overflow"


def test_isolation_exception_name_is_recorded(client, stores, settings) -> None:
    class BoomIsolation:
        def convert(self, document, ocr_lang, progress_callback=None) -> None:
            raise ValueError("sandbox exploded")

    conversion_id = _submit(client, b"%PDF-1.4\n/JavaScript\n")
    assert process_one(settings, stores, BoomIsolation()) is True
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.failed.value
    assert status["error"] == "sandbox exploded"


def test_cancel_during_convert_keeps_cancelled(
    client, stores, settings
) -> None:
    class BlockingIsolation:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def convert(self, document, ocr_lang, progress_callback=None) -> None:
            document.mark_as_converting()
            self.started.set()
            assert self.release.wait(timeout=5)

    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    isolation = BlockingIsolation()
    worker = threading.Thread(
        target=process_one, args=(settings, stores, isolation), daemon=True
    )
    worker.start()
    assert isolation.started.wait(timeout=5)
    deleted = client.delete(f"/v1/conversions/{conversion_id}", headers=auth())
    assert deleted.status_code == 204
    isolation.release.set()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    status = client.get(f"/v1/conversions/{conversion_id}", headers=auth()).json()
    assert status["status"] == ConversionStatus.cancelled.value


def test_get_isolation_dummy_and_aca(settings) -> None:
    settings.isolation = "dummy"
    assert isinstance(get_isolation(settings), InProcessDummy)
    settings.isolation = "aca"
    provider = get_isolation(settings)
    assert isinstance(provider, AcaIsolationProvider)
    assert provider.max_pixel_bytes == settings.max_pixel_bytes


def test_update_stage_pixels_to_pdf(stores) -> None:
    record = ConversionRecord(
        id="cnv_stage",
        status=ConversionStatus.running,
        sha256="a" * 64,
        nbytes=1,
        created_at=utc_now(),
    )
    stores.table.create(record)
    _update_stage(stores, record.id, "Converted page 1/2 to PDF")
    assert stores.table.get(record.id).stage == "pixels_to_pdf"
    _update_stage(stores, record.id, "Converted page 1/2 to searchable PDF")
    assert stores.table.get(record.id).stage == "pixels_to_pdf"


def test_update_stage_ignores_non_running(stores) -> None:
    record = ConversionRecord(
        id="cnv_queued_stage",
        status=ConversionStatus.queued,
        sha256="b" * 64,
        nbytes=1,
        created_at=utc_now(),
    )
    stores.table.create(record)
    _update_stage(stores, record.id, "Converted page 1/2 to PDF")
    assert stores.table.get(record.id).stage is None


def test_report_missing_blob_is_409(client, stores, settings) -> None:
    conversion_id = _submit(client, b"%PDF-1.4\nplain text document\n")
    assert process_one(settings, stores) is True
    stores.blobs.delete(report_blob_key(conversion_id))
    response = client.get(
        f"/v1/conversions/{conversion_id}/report", headers=auth()
    )
    assert response.status_code == 409
