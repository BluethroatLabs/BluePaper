from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from bluepaper.config import CATALOG_VERSION, SCHEMA_VERSION


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConversionStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class ConversionRecord:
    id: str
    status: ConversionStatus
    sha256: str
    nbytes: int
    created_at: str
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    ocr_lang: str | None = None
    scan_completed: bool = False
    filename: str = "upload.bin"
    # True when the caller queued with Turnstile and no API key. The conversion
    # id is then the capability for status, report, PDF, and delete.
    guest: bool = False

    def to_entity(self) -> dict[str, Any]:
        return {
            "PartitionKey": "cnv",
            "RowKey": self.id,
            "Status": self.status.value,
            "Sha256": self.sha256,
            "NBytes": self.nbytes,
            "CreatedAt": self.created_at,
            "Stage": self.stage or "",
            "StartedAt": self.started_at or "",
            "FinishedAt": self.finished_at or "",
            "Error": self.error or "",
            "OcrLang": self.ocr_lang or "",
            "ScanCompleted": self.scan_completed,
            "Filename": self.filename,
            "Guest": self.guest,
        }

    @classmethod
    def from_entity(cls, entity: dict[str, Any]) -> ConversionRecord:
        status = ConversionStatus(str(entity["Status"]))
        return cls(
            id=str(entity["RowKey"]),
            status=status,
            sha256=str(entity["Sha256"]),
            nbytes=int(entity["NBytes"]),
            created_at=str(entity["CreatedAt"]),
            stage=str(entity.get("Stage") or "") or None,
            started_at=str(entity.get("StartedAt") or "") or None,
            finished_at=str(entity.get("FinishedAt") or "") or None,
            error=str(entity.get("Error") or "") or None,
            ocr_lang=str(entity.get("OcrLang") or "") or None,
            scan_completed=bool(entity.get("ScanCompleted", False)),
            filename=str(entity.get("Filename") or "upload.bin"),
            guest=bool(entity.get("Guest", False)),
        )


class AcceptedResponse(BaseModel):
    id: str
    status: ConversionStatus
    sha256: str
    bytes: int
    created_at: str


class StatusResponse(BaseModel):
    id: str
    status: ConversionStatus
    sha256: str
    stage: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class Hit(BaseModel):
    id: str
    count: int
    first_offset: int | None = None


class ConversionOutcome(BaseModel):
    status: str
    pages: int | None = None
    ocr_lang: str | None = None
    output_bytes: int | None = None


class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    conversion_id: str
    sha256: str
    catalog_version: str = CATALOG_VERSION
    conversion_justified: bool
    caveat: str | None = None
    hits: list[Hit] = Field(default_factory=list)
    conversion: ConversionOutcome | None = None


class SourceResponse(BaseModel):
    license: str = "AGPL-3.0"
    source_url: str
    commit: str | None = None


class HealthResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    detail: str


class ScanResult:
    __slots__ = ("hits", "catalog_version", "timed_out")

    def __init__(
        self,
        hits: list[Hit],
        catalog_version: str = CATALOG_VERSION,
        timed_out: bool = False,
    ) -> None:
        self.hits = hits
        self.catalog_version = catalog_version
        self.timed_out = timed_out


@dataclass
class QueueLease:
    conversion_id: str
    pop_receipt: str
    dequeue_count: int
    raw: Any = field(default=None, repr=False)
