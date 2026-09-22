from pathlib import Path

from bluepaper.models import ConversionRecord, ConversionStatus, utc_now
from tests.bluepaper.conftest import auth


def test_healthz_needs_no_auth(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200


def test_openapi_is_public_and_documents_bearer_auth(client) -> None:
    docs = client.get("/docs")
    assert docs.status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    body = spec.json()
    assert body["info"]["title"] == "BluePaper"
    paths = body["paths"]
    for path in (
        "/healthz",
        "/v1/source",
        "/v1/conversions",
        "/v1/conversions/{conversion_id}",
        "/v1/conversions/{conversion_id}/report",
        "/v1/conversions/{conversion_id}/pdf",
    ):
        assert path in paths
    schemes = body["components"]["securitySchemes"]
    assert any(
        scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
        for scheme in schemes.values()
    )
    post = paths["/v1/conversions"]["post"]
    assert post.get("security") or body.get("security")
    assert "security" not in paths["/healthz"]["get"]


def test_missing_key_is_401(client) -> None:
    response = client.post("/v1/conversions")
    assert response.status_code == 401


def test_wrong_key_is_401(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth("nope"),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 401


def test_unsupported_type_is_415(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_too_large_is_413(client, settings, stores) -> None:
    settings.max_upload_bytes = 8
    from fastapi.testclient import TestClient

    from bluepaper.api.app import create_app

    tiny = TestClient(create_app(settings, stores))
    response = tiny.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4 too-big", "application/pdf")},
    )
    assert response.status_code == 413


def test_submit_returns_202(client, sample_pdf: str) -> None:
    data = Path(sample_pdf).read_bytes()
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("sample.pdf", data, "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["id"].startswith("cnv_")
    assert len(body["sha256"]) == 64
    assert body["bytes"] == len(data)

    status = client.get(f"/v1/conversions/{body['id']}", headers=auth())
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


def test_unknown_conversion_is_404(client) -> None:
    response = client.get("/v1/conversions/cnv_01J8Z3K4N5P6Q7R8S9T0", headers=auth())
    assert response.status_code == 404


def test_queue_saturated_is_503(client, stores, settings) -> None:
    for i in range(settings.max_queue_depth):
        stores.queue.enqueue(f"cnv_filler_{i}")
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 503


def test_concurrency_limit_is_429(client, stores) -> None:
    for i in range(2):
        stores.table.create(
            ConversionRecord(
                id=f"cnv_running_{i}",
                status=ConversionStatus.running,
                sha256="a" * 64,
                nbytes=1,
                created_at=utc_now(),
            )
        )
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 429


def test_source_requires_auth(client) -> None:
    assert client.get("/v1/source").status_code == 401
    response = client.get("/v1/source", headers=auth())
    assert response.status_code == 200
    assert response.json()["license"] == "AGPL-3.0"


def test_report_not_ready_is_409(client, sample_pdf: str) -> None:
    data = Path(sample_pdf).read_bytes()
    created = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("sample.pdf", data, "application/pdf")},
    ).json()
    response = client.get(f"/v1/conversions/{created['id']}/report", headers=auth())
    assert response.status_code == 409


def test_pdf_not_ready_is_409(client, sample_pdf: str) -> None:
    data = Path(sample_pdf).read_bytes()
    created = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("sample.pdf", data, "application/pdf")},
    ).json()
    response = client.get(f"/v1/conversions/{created['id']}/pdf", headers=auth())
    assert response.status_code == 409


def test_pdf_missing_blob_is_409(client, stores, settings) -> None:
    from bluepaper.config import pdf_blob_key
    from bluepaper.worker.job import process_one

    created = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4\nplain\n", "application/pdf")},
    ).json()
    assert process_one(settings, stores) is True
    stores.blobs.delete(pdf_blob_key(created["id"]))
    response = client.get(f"/v1/conversions/{created['id']}/pdf", headers=auth())
    assert response.status_code == 409


def test_malformed_authorization_is_401(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers={"Authorization": "Basic test-key"},
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 401


def test_unsupported_ocr_lang_is_400(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        data={"ocr_lang": "not-a-tess-lang"},
    )
    assert response.status_code == 400


def test_supported_ocr_lang_is_accepted(client, stores) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        data={"ocr_lang": "eng"},
    )
    assert response.status_code == 202
    record = stores.table.get(response.json()["id"])
    assert record is not None
    assert record.ocr_lang == "eng"


def test_office_extension_is_accepted(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={
            "file": (
                "memo.DOCX",
                b"PK\x03\x04not-really-office",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 202


def test_uppercase_pdf_extension_is_accepted(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("Scan.PDF", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 202


def test_filename_without_supported_extension_is_415(client) -> None:
    response = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("README", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 415


def test_delete_unknown_is_404(client) -> None:
    response = client.delete(
        "/v1/conversions/cnv_01J8Z3K4N5P6Q7R8S9T0", headers=auth()
    )
    assert response.status_code == 404


def test_delete_running_marks_cancelled(client, stores) -> None:
    stores.table.create(
        ConversionRecord(
            id="cnv_running_cancel",
            status=ConversionStatus.running,
            sha256="c" * 64,
            nbytes=1,
            created_at=utc_now(),
        )
    )
    response = client.delete(
        "/v1/conversions/cnv_running_cancel", headers=auth()
    )
    assert response.status_code == 204
    record = stores.table.get("cnv_running_cancel")
    assert record is not None
    assert record.status == ConversionStatus.cancelled


def test_delete_cancelled_removes_row(client, sample_pdf: str) -> None:
    data = Path(sample_pdf).read_bytes()
    created = client.post(
        "/v1/conversions",
        headers=auth(),
        files={"file": ("sample.pdf", data, "application/pdf")},
    ).json()
    first = client.delete(f"/v1/conversions/{created['id']}", headers=auth())
    assert first.status_code == 204
    second = client.delete(f"/v1/conversions/{created['id']}", headers=auth())
    assert second.status_code == 204
    assert (
        client.get(f"/v1/conversions/{created['id']}", headers=auth()).status_code
        == 404
    )


def test_source_includes_commit(settings, stores) -> None:
    from fastapi.testclient import TestClient

    from bluepaper.api.app import create_app

    settings.source_commit = "abc123"
    app = TestClient(create_app(settings, stores))
    response = app.get("/v1/source", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["commit"] == "abc123"
    assert body["license"] == "AGPL-3.0"
    assert "github.com" in body["source_url"]


def test_healthz_body(client) -> None:
    response = client.get("/healthz")
    assert response.json() == {"status": "ok"}


def test_frontend_is_public(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"BluePaper" in response.content
    assert b"Safety comes from" in response.content


def test_frontend_assets_are_public(client) -> None:
    css = client.get("/ui/styles.css")
    js = client.get("/ui/app.js")
    icon = client.get("/ui/favicon.svg")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert icon.status_code == 200
    assert b"--background" in css.content
    assert b"/v1/conversions" in js.content


def test_frontend_is_not_in_openapi(client) -> None:
    spec = client.get("/openapi.json").json()
    assert "/" not in spec["paths"]
    assert "/ui" not in spec["paths"]


def test_report_and_pdf_unknown_are_404(client) -> None:
    missing = "cnv_01J8Z3K4N5P6Q7R8S9T0"
    assert (
        client.get(f"/v1/conversions/{missing}/report", headers=auth()).status_code
        == 404
    )
    assert (
        client.get(f"/v1/conversions/{missing}/pdf", headers=auth()).status_code
        == 404
    )
