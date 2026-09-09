from __future__ import annotations

from bluepaper.config import Settings
from bluepaper.storage.base import Stores
from bluepaper.storage.memory import memory_stores


def build_stores(settings: Settings) -> Stores:
    if settings.storage_backend == "memory":
        return memory_stores()
    from bluepaper.storage.azure import azure_stores

    return azure_stores(
        account=settings.azure_storage_account,
        connection_string=settings.azure_storage_connection_string,
        blob_container=settings.azure_blob_container,
        table_name=settings.azure_table_name,
        queue_name=settings.azure_queue_name,
    )
