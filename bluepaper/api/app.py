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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from bluepaper.api.routes import router
from bluepaper.config import Settings
from bluepaper.models import HealthResponse
from bluepaper.storage import build_stores
from bluepaper.storage.base import Stores

log = logging.getLogger("bluepaper.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Status, report, and PDF are bearer-or-id protected documents.
_DOCUMENT_GET = re.compile(r"^/v1/conversions/[^/]+(?:/(?:report|pdf))?$")


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
The console at `/` does not. `POST /v1/conversions` accepts a completed
Turnstile token instead, and that conversion id then authorizes status,
report, PDF, and delete. Key-created conversions still require the API key.
`GET /v1/source`, `/`, `/ui`, `/healthz`, `/openapi.json`, `/docs`, and
`/redoc` are unauthenticated.
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

    @application.get("/healthz", response_model=HealthResponse, tags=["meta"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    if WEB_DIR.is_dir():
        index = WEB_DIR / "index.html"
        legal_template = WEB_DIR / "legal.html"
        legal_pages = {
            "privacy": ("Privacy", "How BluePaper handles uploaded documents and conversion artifacts."),
            "terms": ("Terms", "Terms of use and limitations for BluePaper."),
            "support": ("Support", "How to report BluePaper issues and get help."),
        }

        @application.get("/", include_in_schema=False)
        def frontend_index() -> FileResponse:
            return FileResponse(index, media_type="text/html")

        @application.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
        @application.get("/terms", include_in_schema=False, response_class=HTMLResponse)
        @application.get("/support", include_in_schema=False, response_class=HTMLResponse)
        def frontend_legal(request: Request) -> HTMLResponse:
            page_name = request.url.path.lstrip("/")
            title, description = legal_pages[page_name]
            body = (WEB_DIR / "legal" / f"{page_name}.html").read_text()
            html = (
                legal_template.read_text()
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    settings = Settings()
    application = create_app(settings)
    start_embedded_dummy_worker(settings, application.state.stores)
    port = int(os.environ.get("PORT", settings.port))
    log.info("starting BluePaper API")
    uvicorn.run(application, host=settings.host, port=port)
