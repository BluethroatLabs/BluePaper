from __future__ import annotations

from typing import Any

from bluepaper.models import ConversionRecord, QueueLease
from bluepaper.storage.base import Stores


class AzureBlobStore:
    def __init__(self, client: Any, container: str) -> None:
        self._container = client.get_container_client(container)
        try:
            self._container.create_container()
        except Exception:
            pass

    def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self._container.upload_blob(
            name=key,
            data=data,
            overwrite=True,
            content_settings=_content_settings(content_type),
        )

    def get(self, key: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._container.get_blob_client(key)
        try:
            return blob.download_blob().readall()
        except ResourceNotFoundError:
            return None

    def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._container.get_blob_client(key)
        try:
            blob.delete_blob()
        except ResourceNotFoundError:
            return


class AzureTableStore:
    def __init__(self, service: Any, table_name: str) -> None:
        self._table = service.create_table_if_not_exists(table_name)

    def create(self, record: ConversionRecord) -> None:
        self._table.create_entity(record.to_entity())

    def get(self, conversion_id: str) -> ConversionRecord | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = self._table.get_entity("cnv", conversion_id)
        except ResourceNotFoundError:
            return None
        return ConversionRecord.from_entity(dict(entity))

    def update(self, record: ConversionRecord) -> None:
        self._table.upsert_entity(record.to_entity())

    def delete(self, conversion_id: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._table.delete_entity("cnv", conversion_id)
        except ResourceNotFoundError:
            return

    def count_by_status(self, status: str) -> int:
        count = 0
        for entity in self._table.query_entities(f"Status eq '{status}'"):
            count += 1
        return count


class AzureQueue:
    def __init__(self, service: Any, queue_name: str) -> None:
        self._queue = service.get_queue_client(queue_name)
        try:
            self._queue.create_queue()
        except Exception:
            pass

    def enqueue(self, conversion_id: str) -> None:
        self._queue.send_message(conversion_id)

    def lease(self, visibility_timeout: int) -> QueueLease | None:
        messages = self._queue.receive_messages(
            max_messages=1,
            visibility_timeout=visibility_timeout,
        )
        for message in messages:
            return QueueLease(
                conversion_id=message.content,
                pop_receipt=message.pop_receipt,
                dequeue_count=int(message.dequeue_count or 1),
                raw=message,
            )
        return None

    def complete(self, lease: QueueLease) -> None:
        if lease.raw is None:
            return
        self._queue.delete_message(lease.raw)

    def depth(self) -> int:
        props = self._queue.get_queue_properties()
        return int(getattr(props, "approximate_message_count", 0) or 0)


def azure_stores(
    *,
    account: str | None,
    connection_string: str | None,
    blob_container: str,
    table_name: str,
    queue_name: str,
) -> Stores:
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueServiceClient

    if connection_string:
        blobs = BlobServiceClient.from_connection_string(connection_string)
        tables = TableServiceClient.from_connection_string(connection_string)
        queues = QueueServiceClient.from_connection_string(connection_string)
    else:
        if not account:
            raise ValueError(
                "BLUEPAPER_AZURE_STORAGE_ACCOUNT or "
                "BLUEPAPER_AZURE_STORAGE_CONNECTION_STRING is required"
            )
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        blobs = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=credential,
        )
        tables = TableServiceClient(
            endpoint=f"https://{account}.table.core.windows.net",
            credential=credential,
        )
        queues = QueueServiceClient(
            account_url=f"https://{account}.queue.core.windows.net",
            credential=credential,
        )

    return Stores(
        AzureBlobStore(blobs, blob_container),
        AzureTableStore(tables, table_name),
        AzureQueue(queues, queue_name),
    )


def _content_settings(content_type: str) -> Any:
    from azure.storage.blob import ContentSettings

    return ContentSettings(content_type=content_type)
