from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bluepaper.api.routes import router
from bluepaper.api.site import PAGES_DIR, mount_site, render_layout
from bluepaper.config import Settings
from bluepaper.logging import configure_logging
from bluepaper.models import HealthResponse
from bluepaper.storage import build_stores
from bluepaper.storage.base import Stores

log = logging.getLogger("bluepaper.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Status, report, and PDF are bearer-or-capability-id protected documents.
_DOCUMENT_GET = re.compile(r"^/v1/conversions/[^/]+(?:/(?:report|pdf))?$")
# Room for multipart boundaries, part headers and the small text fields.
_FORM_OVERHEAD = 64 * 1024


class _BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over ``limit`` bytes before they are stored.

    A declared Content-Length over the limit is refused unread. A body that
    streams past it (chunked, or a false Content-Length) is cut off there.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.limit:
            await _too_large(scope, receive, send)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            await _too_large(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        {"detail": "file too large"},
        status_code=413,
        headers={"Connection": "close"},
    )
    await response(scope, receive, send)


def _openapi(application: FastAPI) -> dict:
    if application.openapi_schema:
        return application.openapi_schema
    schema = get_openapi(
        title=application.title,
        version=application.version,
        openapi_version=application.openapi_version,
        description=application.description,
        routes=application.routes,
        tags=application.openapi_tags,
        license_info=application.license_info,
    )
    for path, item in schema.get("paths", {}).items():
        if not path.startswith("/v1/conversions"):
            continue
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            security = operation.get("security")
            if not security:
                continue
            scheme = next(iter(security[0]))
            operation["security"] = [{scheme: []}, {}]
    application.openapi_schema = schema
    return schema


OPENAPI_DESCRIPTION = """
Upload an untrusted document and receive a PDF rebuilt from pixels, plus a
regexp report on the original bytes.

Safety comes from destruction, not detection. Zero regexp hits is not "clean."

Integrators send `Authorization: Bearer <api-key>` on conversion routes.
The Bluethroat CLI sends its GitHub App user token instead; those conversions
are visible only to that GitHub user (or the operator API key). The console at
`/` sends neither. `POST /v1/conversions` accepts a completed Turnstile token in
the `CF-Turnstile-Response` header instead, and that conversion id then
authorizes status, report, PDF, and delete.
Key-created conversions still require the API key.
`GET /v1/source`, the site pages (`/`, `/about`, `/how-it-works`, `/for-agents`), `/ui`, `/robots.txt`, `/sitemap.xml`, `/llms.txt`,
`/healthz`, `/openapi.json`, `/docs`, and `/redoc` are unauthenticated.
""".strip()


def create_app(
    settings: Settings | None = None,
    stores: Stores | None = None,
) -> FastAPI:
    settings = settings or Settings()
    stores = stores or build_stores(settings)
    application = FastAPI(
        title="BluePaper",
        version="0.1.0",
        description=OPENAPI_DESCRIPTION,
        license_info={
            "name": "AGPL-3.0",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        openapi_tags=[
            {
                "name": "conversions",
                "description": "Queue, inspect, and fetch conversion artifacts.",
            },
            {
                "name": "meta",
                "description": "Health and AGPL corresponding source.",
            },
        ],
        swagger_ui_parameters={"persistAuthorization": True},
    )
    application.openapi = lambda: _openapi(application)  # type: ignore[method-assign]
    application.state.settings = settings
    application.state.stores = stores
    application.include_router(router)

    @application.middleware("http")
    async def no_store_conversion_documents(
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        response = await call_next(request)
        if request.method == "GET" and _DOCUMENT_GET.match(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Added last, so it is the outermost app middleware and sees the body first.
    application.add_middleware(
        BodyLimit, limit=settings.max_upload_bytes + _FORM_OVERHEAD
    )

    @application.get("/healthz", response_model=HealthResponse, tags=["meta"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    if PAGES_DIR.is_dir():
        mount_site(application, settings.public_url)

    if WEB_DIR.is_dir():
        legal_template = WEB_DIR / "legal.html"
        legal_pages = {
            "privacy": (
                "Privacy",
                "How BluePaper handles uploaded documents and conversion artifacts.",
            ),
            "terms": ("Terms", "Terms of use and limitations for BluePaper."),
            "support": ("Support", "How to report BluePaper issues and get help."),
        }

        @application.get(
            "/privacy", include_in_schema=False, response_class=HTMLResponse
        )
        @application.get("/terms", include_in_schema=False, response_class=HTMLResponse)
        @application.get(
            "/support", include_in_schema=False, response_class=HTMLResponse
        )
        def frontend_legal(request: Request) -> HTMLResponse:
            page_name = request.url.path.lstrip("/")
            title, description = legal_pages[page_name]
            body = (WEB_DIR / "legal" / f"{page_name}.html").read_text()
            html = (
                render_layout(legal_template.read_text(), name="legal.html")
                .replace("{{TITLE}}", title)
                .replace("{{DESCRIPTION}}", description)
                .replace("{{BODY}}", body)
            )
            return HTMLResponse(html)

        application.mount("/ui", StaticFiles(directory=WEB_DIR), name="ui")

    return application


def start_embedded_dummy_worker(settings: Settings, stores: Stores) -> None:
    """Complete local conversions when isolation is dummy (not for production)."""
    if settings.isolation != "dummy":
        return

    from bluepaper.worker.job import get_isolation, process_one

    isolation = get_isolation(settings)

    def loop() -> None:
        while True:
            processed = False
            try:
                processed = process_one(settings, stores, isolation)
            except Exception:
                log.exception("embedded dummy worker error")
            if not processed:
                time.sleep(settings.worker_poll_seconds)

    threading.Thread(target=loop, name="bluepaper-dummy-worker", daemon=True).start()
    log.warning("embedded dummy worker started; dummy isolation is not for production")


def write_openapi(path: str | os.PathLike[str] = "docs/openapi.json") -> None:
    from bluepaper.storage.memory import memory_stores

    settings = Settings(api_key="dev", storage_backend="memory", isolation="dummy")
    spec = create_app(settings, memory_stores()).openapi()
    Path(path).write_text(json.dumps(spec, indent=2) + "\n")


def run() -> None:
    configure_logging()
    import uvicorn

    from bluepaper.api.github_auth import configured as github_configured
    from bluepaper.api.github_auth import misconfigured as github_misconfigured

    settings = Settings()
    if github_misconfigured(settings):
        log.warning(
            "GitHub CLI auth is misconfigured; set both "
            "BLUEPAPER_GITHUB_CLIENT_ID and BLUEPAPER_GITHUB_CLIENT_SECRET"
        )
    else:
        log.info(
            "GitHub CLI auth: %s",
            "on" if github_configured(settings) else "off",
        )
    application = create_app(settings)
    start_embedded_dummy_worker(settings, application.state.stores)
    port = int(os.environ.get("PORT", settings.port))
    log.info("starting BluePaper API")
    uvicorn.run(application, host=settings.host, port=port)
