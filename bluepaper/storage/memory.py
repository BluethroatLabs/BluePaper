from __future__ import annotations

import threading
import time
import uuid

from bluepaper.models import ConversionRecord, ConversionStatus, QueueLease
from bluepaper.storage.base import Stores


class MemoryBlobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, bytes] = {}

    def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        with self._lock:
            self._data[key] = data

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class MemoryTableStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, ConversionRecord] = {}

    def create(self, record: ConversionRecord) -> None:
        with self._lock:
            self._rows[record.id] = record

    def get(self, conversion_id: str) -> ConversionRecord | None:
        with self._lock:
            row = self._rows.get(conversion_id)
            if row is None:
                return None
            return ConversionRecord.from_entity(row.to_entity())

    def update(self, record: ConversionRecord) -> None:
        with self._lock:
            self._rows[record.id] = record

    def delete(self, conversion_id: str) -> None:
        with self._lock:
            self._rows.pop(conversion_id, None)

    def count_by_status(self, status: str) -> int:
        with self._lock:
            return sum(1 for row in self._rows.values() if row.status.value == status)


class MemoryQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[_QueueItem] = []

    def enqueue(self, conversion_id: str) -> None:
        with self._lock:
            self._items.append(
                _QueueItem(
                    conversion_id=conversion_id,
                    visible_at=0.0,
                    dequeue_count=0,
                    pop_receipt="",
                )
            )

    def lease(self, visibility_timeout: int) -> QueueLease | None:
        now = time.time()
        with self._lock:
            for item in self._items:
                if item.visible_at <= now:
                    item.dequeue_count += 1
                    item.visible_at = now + visibility_timeout
                    item.pop_receipt = uuid.uuid4().hex
                    return QueueLease(
                        conversion_id=item.conversion_id,
                        pop_receipt=item.pop_receipt,
                        dequeue_count=item.dequeue_count,
                    )
        return None

    def complete(self, lease: QueueLease) -> None:
        with self._lock:
            self._items = [
                item
                for item in self._items
                if not (
                    item.conversion_id == lease.conversion_id
                    and item.pop_receipt == lease.pop_receipt
                )
            ]

    def depth(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for item in self._items if item.visible_at <= now)


class _QueueItem:
    __slots__ = ("conversion_id", "visible_at", "dequeue_count", "pop_receipt")

    def __init__(
        self,
        conversion_id: str,
        visible_at: float,
        dequeue_count: int,
        pop_receipt: str,
    ) -> None:
        self.conversion_id = conversion_id
        self.visible_at = visible_at
        self.dequeue_count = dequeue_count
        self.pop_receipt = pop_receipt


def memory_stores() -> Stores:
    return Stores(MemoryBlobStore(), MemoryTableStore(), MemoryQueue())


# Used by API 429 tests that need to inspect running rows after mutation.
def _status_running() -> str:
    return ConversionStatus.running.value
