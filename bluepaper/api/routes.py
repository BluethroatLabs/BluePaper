from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from bluepaper.api.auth import get_settings, require_api_key
from bluepaper.config import (
    SUPPORTED_EXTENSIONS,
    Settings,
    extension_of,
    original_blob_key,
    pdf_blob_key,
    report_blob_key,
)
from bluepaper.ids import new_conversion_id
from bluepaper.models import (
    AcceptedResponse,
    ConversionRecord,
    ConversionStatus,
    SourceResponse,
    StatusResponse,
    utc_now,
)
from bluepaper.ocr import is_supported_ocr_lang
from bluepaper.storage.base import Stores

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


def get_stores(request: Request) -> Stores:
    return request.app.state.stores  # type: ignore[no-any-return]


@router.get("/source", response_model=SourceResponse)
def source(settings: Annotated[Settings, Depends(get_settings)]) -> SourceResponse:
    return SourceResponse(
        source_url=settings.source_url,
        commit=settings.source_commit,
    )


@router.post("/conversions", status_code=status.HTTP_202_ACCEPTED)
async def create_conversion(
    settings: Annotated[Settings, Depends(get_settings)],
    stores: Annotated[Stores, Depends(get_stores)],
    file: Annotated[UploadFile, File()],
    ocr_lang: Annotated[str | None, Form()] = None,
) -> AcceptedResponse:
    filename = file.filename or "upload.bin"
    ext = extension_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="unsupported file type",
        )
    if ocr_lang and not is_supported_ocr_lang(ocr_lang):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported ocr_lang",
        )
    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail="file too large",
        )
    if stores.queue.depth() >= settings.max_queue_depth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="queue saturated",
        )
    if stores.table.count_by_status(ConversionStatus.running.value) >= (
        settings.max_concurrent_jobs
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="concurrency limit",
        )

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail="file too large",
        )

    digest = hashlib.sha256(data).hexdigest()
    conversion_id = new_conversion_id()
    created_at = utc_now()
    stores.blobs.put(original_blob_key(digest), data, "application/octet-stream")
    record = ConversionRecord(
        id=conversion_id,
        status=ConversionStatus.queued,
        sha256=digest,
        nbytes=len(data),
        created_at=created_at,
        ocr_lang=ocr_lang,
        filename=filename,
    )
    stores.table.create(record)
    stores.queue.enqueue(conversion_id)
    return AcceptedResponse(
        id=conversion_id,
        status=ConversionStatus.queued,
        sha256=digest,
        bytes=len(data),
        created_at=created_at,
    )


@router.get("/conversions/{conversion_id}", response_model=StatusResponse)
def get_conversion(
    conversion_id: str,
    stores: Annotated[Stores, Depends(get_stores)],
) -> StatusResponse:
    record = _require_record(stores, conversion_id)
    return _status_response(record)


@router.get("/conversions/{conversion_id}/report")
def get_report(
    conversion_id: str,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _require_record(stores, conversion_id)
    if record.status == ConversionStatus.succeeded or (
        record.status == ConversionStatus.failed and record.scan_completed
    ):
        payload = stores.blobs.get(report_blob_key(conversion_id))
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="report not available",
            )
        return Response(content=payload, media_type="application/json")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="report not available",
    )


@router.get("/conversions/{conversion_id}/pdf")
def get_pdf(
    conversion_id: str,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _require_record(stores, conversion_id)
    if record.status != ConversionStatus.succeeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pdf not available",
        )
    payload = stores.blobs.get(pdf_blob_key(conversion_id))
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pdf not available",
        )
    return Response(content=payload, media_type="application/pdf")


@router.delete("/conversions/{conversion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversion(
    conversion_id: str,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _require_record(stores, conversion_id)
    if record.status in (ConversionStatus.queued, ConversionStatus.running):
        record.status = ConversionStatus.cancelled
        record.finished_at = utc_now()
        stores.table.update(record)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    stores.blobs.delete(pdf_blob_key(conversion_id))
    stores.blobs.delete(report_blob_key(conversion_id))
    stores.table.delete(conversion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_record(stores: Stores, conversion_id: str) -> ConversionRecord:
    record = stores.table.get(conversion_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversion not found",
        )
    return record


def _status_response(record: ConversionRecord) -> StatusResponse:
    return StatusResponse(
        id=record.id,
        status=record.status,
        sha256=record.sha256,
        stage=record.stage,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
    )
