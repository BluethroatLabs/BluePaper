from __future__ import annotations

import hashlib
from typing import Annotated, Any
from urllib.parse import quote, unquote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response

from bluepaper.api.auth import (
    Principal,
    authenticate,
    get_settings,
    unauthorized,
)
from bluepaper.api.turnstile import client_ip, verify_turnstile
from bluepaper.config import (
    SUPPORTED_EXTENSIONS,
    Settings,
    deployed_source_revision,
    extension_of,
    original_blob_key,
    pdf_blob_key,
    report_blob_key,
    safe_pdf_name,
)
from bluepaper.ids import new_conversion_id
from bluepaper.models import (
    AcceptedResponse,
    ConversionRecord,
    ConversionStatus,
    ErrorResponse,
    Report,
    SourceResponse,
    StatusResponse,
    normalize_failed_report,
    utc_now,
)
from bluepaper.ocr import is_supported_ocr_lang
from bluepaper.storage.base import Stores

router = APIRouter(prefix="/v1")

_ErrorSpec = dict[int | str, dict[str, Any]]

ERROR_401: _ErrorSpec = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": "Missing or invalid API key or GitHub token",
    }
}
ERROR_404: _ErrorSpec = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Conversion not found",
    }
}


def get_stores(request: Request) -> Stores:
    return request.app.state.stores


Authentication = Annotated[Principal | None, Depends(authenticate)]


async def gate_queue(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Authentication,
) -> None:
    """API key, GitHub user, or Turnstile challenge for a browser guest."""
    if principal is not None:
        request.state.guest = False
        request.state.principal = principal
        return
    secret = (settings.turnstile_secret or "").strip()
    if not secret:
        raise unauthorized()
    token: str | None = None
    form = await request.form()
    raw = form.get("cf-turnstile-response")
    if isinstance(raw, str):
        token = raw
    verify_turnstile(token, client_ip(request), settings)
    request.state.guest = True


def _visible_record(
    stores: Stores,
    conversion_id: str,
    principal: Principal | None,
) -> ConversionRecord:
    record = stores.table.get(conversion_id)
    if principal is not None and principal.kind == "api_key":
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="conversion not found",
            )
        return record
    if record is not None and record.guest:
        return record
    if principal is not None and principal.kind == "github":
        if record is not None and record.github_user_id == principal.github_user_id:
            return record
        # Do not reveal another GitHub user's conversion id.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversion not found",
        )
    raise unauthorized()


@router.get(
    "/source",
    response_model=SourceResponse,
    tags=["meta"],
    summary="AGPL corresponding source",
)
def source(settings: Annotated[Settings, Depends(get_settings)]) -> SourceResponse:
    return SourceResponse(
        source_url=settings.source_url,
        commit=deployed_source_revision(settings),
    )


@router.post(
    "/conversions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedResponse,
    tags=["conversions"],
    summary="Queue a conversion",
    responses={
        **ERROR_401,
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Unsupported ocr_lang",
        },
        413: {"model": ErrorResponse, "description": "File too large"},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorResponse,
            "description": "Unsupported file type",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "model": ErrorResponse,
            "description": "Concurrency limit",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Turnstile verification failed",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Queue saturated",
        },
    },
    dependencies=[Depends(gate_queue)],
)
async def create_conversion(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    stores: Annotated[Stores, Depends(get_stores)],
    file: Annotated[UploadFile, File()],
    ocr_lang: Annotated[str | None, Form()] = None,
    cf_turnstile_response: Annotated[
        str | None,
        Form(
            alias="cf-turnstile-response",
            description=(
                "Cloudflare Turnstile token. Required when the integrator "
                "API key or GitHub token is omitted."
            ),
        ),
    ] = None,
) -> AcceptedResponse:
    # The token is checked in gate_queue. The parameter stays so OpenAPI
    # documents the multipart field.
    del cf_turnstile_response
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
        guest=bool(getattr(request.state, "guest", False)),
        github_user_id=(
            getattr(getattr(request.state, "principal", None), "github_user_id", None)
        ),
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


@router.get(
    "/conversions/{conversion_id}",
    response_model=StatusResponse,
    tags=["conversions"],
    summary="Get conversion status",
    responses={**ERROR_401, **ERROR_404},
)
def get_conversion(
    conversion_id: str,
    principal: Authentication,
    stores: Annotated[Stores, Depends(get_stores)],
) -> StatusResponse:
    record = _visible_record(stores, conversion_id, principal)
    return _status_response(record)


@router.get(
    "/conversions/{conversion_id}/report",
    tags=["conversions"],
    summary="Get regexp report",
    response_class=Response,
    responses={
        200: {"model": Report, "description": "Regexp hits on the original bytes"},
        **ERROR_401,
        **ERROR_404,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Report not available yet",
        },
    },
)
def get_report(
    conversion_id: str,
    principal: Authentication,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _visible_record(stores, conversion_id, principal)
    if record.status == ConversionStatus.succeeded or (
        record.status == ConversionStatus.failed and record.scan_completed
    ):
        payload = stores.blobs.get(report_blob_key(conversion_id))
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="report not available",
            )
        if record.status != ConversionStatus.succeeded:
            payload = normalize_failed_report(payload)
        return Response(content=payload, media_type="application/json")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="report not available",
    )


@router.get(
    "/conversions/{conversion_id}/pdf",
    tags=["conversions"],
    summary="Download sanitized PDF",
    description=(
        "PDF rebuilt from pixels. Content-Disposition names the file "
        "`{original stem}-safe.pdf`."
    ),
    response_class=Response,
    responses={
        200: {
            "description": "PDF rebuilt from pixels",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        **ERROR_401,
        **ERROR_404,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "PDF not available yet",
        },
    },
)
def get_pdf(
    conversion_id: str,
    principal: Authentication,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _visible_record(stores, conversion_id, principal)
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
    name = safe_pdf_name(record.filename)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": _pdf_content_disposition(name)},
    )


@router.delete(
    "/conversions/{conversion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversions"],
    summary="Cancel or delete a conversion",
    responses={**ERROR_401, **ERROR_404},
)
def delete_conversion(
    conversion_id: str,
    principal: Authentication,
    stores: Annotated[Stores, Depends(get_stores)],
) -> Response:
    record = _visible_record(stores, conversion_id, principal)
    if record.status in (ConversionStatus.queued, ConversionStatus.running):
        record.status = ConversionStatus.cancelled
        record.finished_at = utc_now()
        stores.table.update(record)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    stores.blobs.delete(pdf_blob_key(conversion_id))
    stores.blobs.delete(report_blob_key(conversion_id))
    stores.table.delete(conversion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _pdf_content_disposition(filename: str) -> str:
    # Multipart upload can leave a quote already percent-encoded. Decode once,
    # then encode the extended filename once (RFC 5987).
    name = unquote(filename)
    ascii_name = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name
    )
    if not ascii_name.endswith(".pdf"):
        ascii_name = "document-safe.pdf"
    encoded = quote(name, safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


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
