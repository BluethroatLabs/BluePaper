from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from bluepaper.config import Settings
from bluepaper.models import ConversionRecord, ConversionStatus, QueueLease, utc_now
from bluepaper.storage import build_stores
from bluepaper.storage.azure import AzureBlobStore, AzureQueue, AzureTableStore
from bluepaper.storage.base import Stores
from bluepaper.storage.memory import memory_stores


def _record(
    conversion_id: str, status: ConversionStatus = ConversionStatus.queued
) -> ConversionRecord:
    return ConversionRecord(
        id=conversion_id,
        status=status,
        sha256="a" * 64,
        nbytes=4,
        created_at=utc_now(),
        filename="doc.pdf",
    )


def test_build_stores_memory(settings: Settings) -> None:
    stores = build_stores(settings)
    stores.blobs.put("k", b"v")
    assert stores.blobs.get("k") == b"v"
    stores.blobs.delete("k")
    assert stores.blobs.get("k") is None


def test_build_stores_azure_dispatches(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = memory_stores()
    captured: dict[str, object] = {}

    def fake_azure_stores(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("bluepaper.storage.azure.azure_stores", fake_azure_stores)
    settings.storage_backend = "azure"
    settings.azure_storage_account = "acct"
    settings.azure_blob_container = "papers"
    assert build_stores(settings) is sentinel
    assert captured["account"] == "acct"
    assert captured["blob_container"] == "papers"


def test_memory_table_entity_roundtrip(stores: Stores) -> None:
    record = ConversionRecord(
        id="cnv_round",
        status=ConversionStatus.succeeded,
        sha256="b" * 64,
        nbytes=8,
        created_at="2026-01-01T00:00:00Z",
        stage=None,
        started_at=None,
        finished_at="2026-01-01T00:00:01Z",
        error=None,
        ocr_lang=None,
        scan_completed=True,
        filename="memo.docx",
    )
    stores.table.create(record)
    got = stores.table.get("cnv_round")
    assert got is not None
    assert got.stage is None
    assert got.ocr_lang is None
    assert got.error is None
    assert got.scan_completed is True
    assert got.filename == "memo.docx"
    assert got.status == ConversionStatus.succeeded
    stores.table.delete("cnv_round")
    assert stores.table.get("cnv_round") is None


def test_memory_count_by_status(stores: Stores) -> None:
    stores.table.create(_record("cnv_q1"))
    stores.table.create(_record("cnv_r1", ConversionStatus.running))
    stores.table.create(_record("cnv_r2", ConversionStatus.running))
    assert stores.table.count_by_status("queued") == 1
    assert stores.table.count_by_status("running") == 2
    assert stores.table.count_by_status("failed") == 0


def test_memory_count_active_is_per_owner(stores: Stores) -> None:
    rows = [
        ("cnv_a1", ConversionStatus.queued, "github:1"),
        ("cnv_a2", ConversionStatus.running, "github:1"),
        ("cnv_a3", ConversionStatus.succeeded, "github:1"),
        ("cnv_a4", ConversionStatus.failed, "github:1"),
        ("cnv_b1", ConversionStatus.running, "github:2"),
        ("cnv_n1", ConversionStatus.running, None),
    ]
    for conversion_id, status, owner in rows:
        record = _record(conversion_id, status)
        record.owner = owner
        stores.table.create(record)
    assert stores.table.count_active("github:1") == 2
    assert stores.table.count_active("github:2") == 1
    assert stores.table.count_active("github:3") == 0


def test_memory_queue_lease_hides_message(stores: Stores) -> None:
    stores.queue.enqueue("cnv_a")
    lease = stores.queue.lease(60)
    assert lease is not None
    assert lease.conversion_id == "cnv_a"
    assert stores.queue.lease(60) is None
    assert stores.queue.depth() == 0
    stores.queue.complete(lease)
    assert stores.queue.lease(1) is None


def test_memory_queue_wrong_receipt_does_not_complete(stores: Stores) -> None:
    stores.queue.enqueue("cnv_a")
    lease = stores.queue.lease(0)
    assert lease is not None
    stores.queue.complete(
        QueueLease(
            conversion_id=lease.conversion_id,
            pop_receipt="not-the-receipt",
            dequeue_count=lease.dequeue_count,
        )
    )
    again = stores.queue.lease(1)
    assert again is not None
    assert again.conversion_id == "cnv_a"
    assert again.dequeue_count == lease.dequeue_count + 1


def test_memory_blob_delete_missing_is_ok(stores: Stores) -> None:
    stores.blobs.delete("no-such-key")
    assert stores.blobs.get("no-such-key") is None


class _FakeBlobClient:
    def __init__(
        self, store: dict[str, bytes], key: str, missing_exc: type[Exception]
    ) -> None:
        self._store = store
        self._key = key
        self._missing_exc = missing_exc

    def download_blob(self) -> SimpleNamespace:
        if self._key not in self._store:
            raise self._missing_exc("missing")
        return SimpleNamespace(readall=lambda: self._store[self._key])

    def delete_blob(self) -> None:
        if self._key not in self._store:
            raise self._missing_exc("missing")
        del self._store[self._key]


class _FakeContainer:
    def __init__(self, missing_exc: type[Exception], fail_create: bool = False) -> None:
        self.blobs: dict[str, bytes] = {}
        self.missing_exc = missing_exc
        self.fail_create = fail_create
        self.uploads: list[str] = []

    def create_container(self) -> None:
        if self.fail_create:
            from azure.core.exceptions import ResourceExistsError

            raise ResourceExistsError("already exists")

    def upload_blob(
        self,
        name: str,
        data: bytes,
        overwrite: bool = True,
        content_settings: object = None,
    ) -> None:
        self.blobs[name] = data
        self.uploads.append(name)

    def get_blob_client(self, key: str) -> _FakeBlobClient:
        return _FakeBlobClient(self.blobs, key, self.missing_exc)


class _FakeTable:
    def __init__(self, missing_exc: type[Exception]) -> None:
        self.rows: dict[str, dict] = {}
        self.missing_exc = missing_exc
        self.queries: list[str] = []

    def create_entity(self, entity: dict) -> None:
        self.rows[str(entity["RowKey"])] = dict(entity)

    def get_entity(self, partition: str, row_key: str) -> dict:
        if row_key not in self.rows:
            raise self.missing_exc("missing")
        return self.rows[row_key]

    def upsert_entity(self, entity: dict) -> None:
        self.rows[str(entity["RowKey"])] = dict(entity)

    def delete_entity(self, partition: str, row_key: str) -> None:
        if row_key not in self.rows:
            raise self.missing_exc("missing")
        del self.rows[row_key]

    def query_entities(self, query: str) -> object:
        self.queries.append(query)
        values = query.split("'")[1::2]
        if query.startswith("Owner eq "):
            owner, statuses = values[0].replace("''", "'"), set(values[1:])
            for entity in self.rows.values():
                if entity.get("Owner") == owner and entity["Status"] in statuses:
                    yield entity
            return
        for entity in self.rows.values():
            if entity["Status"] == values[0]:
                yield entity


class _FakeQueue:
    def __init__(self) -> None:
        self.messages: list[SimpleNamespace] = []
        self.deleted: list[object] = []

    def create_queue(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        raise ResourceExistsError("exists")

    def send_message(self, conversion_id: str) -> None:
        self.messages.append(
            SimpleNamespace(
                content=conversion_id,
                pop_receipt="rcpt",
                dequeue_count=1,
            )
        )

    def receive_messages(
        self, max_messages: int, visibility_timeout: int
    ) -> list[SimpleNamespace]:
        if not self.messages:
            return []
        return [self.messages.pop(0)]

    def delete_message(self, raw: object) -> None:
        self.deleted.append(raw)

    def get_queue_properties(self) -> SimpleNamespace:
        return SimpleNamespace(approximate_message_count=len(self.messages))


@pytest.fixture
def missing_exc(monkeypatch: pytest.MonkeyPatch) -> type[Exception]:
    import sys
    import types

    existing = sys.modules.get("azure.core.exceptions")
    if existing is not None and hasattr(existing, "ResourceNotFoundError"):
        return existing.ResourceNotFoundError

    class ResourceNotFoundError(Exception):
        pass

    class ResourceExistsError(Exception):
        pass

    exc_mod = types.ModuleType("azure.core.exceptions")
    exc_mod.ResourceNotFoundError = ResourceNotFoundError  # type: ignore[attr-defined]
    exc_mod.ResourceExistsError = ResourceExistsError  # type: ignore[attr-defined]
    azure_mod = sys.modules.get("azure") or types.ModuleType("azure")
    core_mod = sys.modules.get("azure.core") or types.ModuleType("azure.core")
    monkeypatch.setitem(sys.modules, "azure", azure_mod)
    monkeypatch.setitem(sys.modules, "azure.core", core_mod)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", exc_mod)
    return ResourceNotFoundError


def test_azure_blob_roundtrip(
    missing_exc: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bluepaper.storage.azure._content_settings", lambda _ct: None)
    container = _FakeContainer(missing_exc, fail_create=True)
    store = AzureBlobStore(
        SimpleNamespace(get_container_client=lambda _n: container), "c"
    )
    store.put("k", b"hello", "text/plain")
    assert store.get("k") == b"hello"
    assert store.get("missing") is None
    store.delete("missing")
    store.delete("k")
    assert store.get("k") is None


def test_azure_table_roundtrip(missing_exc: type[Exception]) -> None:
    table = _FakeTable(missing_exc)
    store = AzureTableStore(
        SimpleNamespace(create_table_if_not_exists=lambda _n: table), "t"
    )
    record = _record("cnv_az")
    store.create(record)
    got = store.get("cnv_az")
    assert got is not None
    assert got.id == "cnv_az"
    assert store.get("missing") is None
    record.status = ConversionStatus.running
    store.update(record)
    updated = store.get("cnv_az")
    assert updated is not None
    assert updated.status == ConversionStatus.running
    assert store.count_by_status("running") == 1
    assert store.count_active("github:9") == 0
    record.owner = "github:9"
    store.update(record)
    assert store.count_active("github:9") == 1
    assert table.queries[-1] == (
        "Owner eq 'github:9' and (Status eq 'queued' or Status eq 'running')"
    )
    store.delete("missing")
    store.delete("cnv_az")
    assert store.get("cnv_az") is None


def test_azure_queue_lease_and_complete() -> None:
    backend = _FakeQueue()
    queue = AzureQueue(SimpleNamespace(get_queue_client=lambda _n: backend), "q")
    queue.enqueue("cnv_q")
    assert queue.depth() == 1
    lease = queue.lease(30)
    assert lease is not None
    assert lease.conversion_id == "cnv_q"
    assert queue.lease(30) is None
    queue.complete(lease)
    assert backend.deleted == [lease.raw]
    empty = QueueLease(conversion_id="x", pop_receipt="y", dequeue_count=1)
    queue.complete(empty)


def test_memory_queue_extend_keeps_message_hidden(stores: Stores) -> None:
    stores.queue.enqueue("cnv_ext")
    lease = stores.queue.lease(1)
    assert lease is not None
    renewed = stores.queue.extend(lease, 60)
    assert renewed is not None
    assert renewed.pop_receipt != lease.pop_receipt
    assert renewed.dequeue_count == lease.dequeue_count
    time.sleep(1.1)
    assert stores.queue.lease(1) is None
    # The old receipt is stale; only the renewed lease completes the message.
    stores.queue.complete(lease)
    assert stores.queue.extend(lease, 60) is None
    stores.queue.complete(renewed)
    assert stores.queue.extend(renewed, 60) is None


def test_memory_queue_extend_after_expiry_is_lost(stores: Stores) -> None:
    stores.queue.enqueue("cnv_exp")
    lease = stores.queue.lease(0)
    assert lease is not None
    assert stores.queue.extend(lease, 60) is None


def test_azure_queue_extend(missing_exc: type[Exception]) -> None:
    backend = _FakeQueue()
    updated = SimpleNamespace(content="cnv_q", pop_receipt="rcpt2", dequeue_count=1)
    calls: list[tuple[object, int]] = []

    def update_message(raw: object, *, visibility_timeout: int) -> SimpleNamespace:
        calls.append((raw, visibility_timeout))
        if raw is updated:
            raise missing_exc("gone")
        return updated

    backend.update_message = update_message  # type: ignore[attr-defined]
    queue = AzureQueue(SimpleNamespace(get_queue_client=lambda _n: backend), "q")
    queue.enqueue("cnv_q")
    lease = queue.lease(30)
    assert lease is not None
    renewed = queue.extend(lease, 90)
    assert renewed is not None
    assert renewed.pop_receipt == "rcpt2"
    assert renewed.raw is updated
    assert calls == [(lease.raw, 90)]
    assert queue.extend(renewed, 90) is None
