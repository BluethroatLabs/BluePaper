from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from bluepaper.api.routes import router
from bluepaper.config import Settings
from bluepaper.models import HealthResponse
from bluepaper.storage import build_stores
from bluepaper.storage.base import Stores

log = logging.getLogger("bluepaper.api")

OPENAPI_DESCRIPTION = """
Upload an untrusted document and receive a PDF rebuilt from pixels, plus a
regexp report on the original bytes.

Safety comes from destruction, not detection. Zero regexp hits is not "clean."

`/v1` routes require `Authorization: Bearer <api-key>`. `/healthz`,
`/openapi.json`, `/docs`, and `/redoc` are unauthenticated.
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
    application.state.settings = settings
    application.state.stores = stores
    application.include_router(router)

    @application.get("/healthz", response_model=HealthResponse, tags=["meta"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    return application


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
    port = int(os.environ.get("PORT", settings.port))
    log.info("starting BluePaper API")
    uvicorn.run(application, host=settings.host, port=port)
