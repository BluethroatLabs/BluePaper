from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CATALOG_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
ZERO_HITS_CAVEAT = (
    "No obvious indicators were found; absence of patterns is not a malware "
    "verdict. Conversion still rebuilt the document from pixels."
)

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".docm",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".odg",
        ".hwp",
        ".hwpx",
        ".epub",
        ".jpg",
        ".jpeg",
        ".gif",
        ".png",
        ".svg",
        ".bmp",
        ".pnm",
        ".pbm",
        ".ppm",
        ".tif",
        ".tiff",
    }
)


def original_blob_key(sha256: str) -> str:
    return f"originals/{sha256}"


def pdf_blob_key(conversion_id: str) -> str:
    return f"conversions/{conversion_id}/safe.pdf"


def report_blob_key(conversion_id: str) -> str:
    return f"conversions/{conversion_id}/report.json"


def extension_of(filename: str | None) -> str:
    if not filename:
        return ""
    return Path(filename).suffix.lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BLUEPAPER_",
        env_file=".env",
        extra="ignore",
    )

    api_key: str = Field(min_length=1)
    max_upload_bytes: int = 32 * 1024 * 1024
    max_concurrent_jobs: int = 4
    max_queue_depth: int = 100
    max_dequeues: int = 5
    max_pixel_bytes: int = 512 * 1024 * 1024
    scan_timeout_seconds: float = 30.0
    conversion_timeout_seconds: float = 600.0
    visibility_timeout_seconds: int = 900
    worker_poll_seconds: float = 1.0
    host: str = "0.0.0.0"
    port: int = 8080
    storage_backend: Literal["memory", "azure"] = "memory"
    isolation: Literal["dummy", "aca"] = "dummy"
    azure_storage_account: str | None = None
    azure_storage_connection_string: str | None = None
    azure_blob_container: str = "bluepaper"
    azure_table_name: str = "conversions"
    azure_queue_name: str = "conversions"
    azure_subscription_id: str | None = None
    azure_resource_group: str | None = None
    azure_region: str = "eastus2"
    sandbox_group: str | None = None
    sandbox_disk_id: str | None = None
    source_url: str = "https://github.com/BluethroatLabs/BluePaper"
    source_commit: str | None = None
    turnstile_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TURNSTILE_SECRET", "BLUEPAPER_TURNSTILE_SECRET"),
    )
    turnstile_hostnames: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TURNSTILE_HOSTNAMES", "BLUEPAPER_TURNSTILE_HOSTNAMES"
        ),
    )
