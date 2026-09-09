from __future__ import annotations

from typing import Protocol

from bluepaper.models import ConversionRecord, QueueLease


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


class TableStore(Protocol):
    def create(self, record: ConversionRecord) -> None: ...

    def get(self, conversion_id: str) -> ConversionRecord | None: ...

    def update(self, record: ConversionRecord) -> None: ...

    def delete(self, conversion_id: str) -> None: ...

    def count_by_status(self, status: str) -> int: ...


class JobQueue(Protocol):
    def enqueue(self, conversion_id: str) -> None: ...

    def lease(self, visibility_timeout: int) -> QueueLease | None: ...

    def complete(self, lease: QueueLease) -> None: ...

    def depth(self) -> int: ...


class Stores:
    def __init__(self, blobs: BlobStore, table: TableStore, queue: JobQueue) -> None:
        self.blobs = blobs
        self.table = table
        self.queue = queue
