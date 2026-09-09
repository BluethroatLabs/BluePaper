from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO

from dangerzone.document import Document
from dangerzone.isolation_provider.base import IsolationProvider

from bluepaper.config import Settings
from bluepaper.isolation.session import CONVERT_WRAPPER, SandboxSession

log = logging.getLogger("bluepaper.isolation.aca")

_INPUT = "/tmp/bluepaper/input.bin"
_PIXELS = "/tmp/bluepaper/pixels.bin"
_WRAPPER = "/tmp/bluepaper/run_convert.py"
_PYTHON = "/usr/bin/python3"


class AcaIsolationProvider(IsolationProvider):
    """One disposable ACA Sandbox per conversion. Never snapshot after a document."""

    def __init__(
        self,
        create_session: Callable[[], SandboxSession],
        max_pixel_bytes: int,
        debug: bool = False,
    ) -> None:
        super().__init__(debug=debug)
        self._create_session = create_session
        self.max_pixel_bytes = max_pixel_bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> AcaIsolationProvider:
        return cls(
            create_session=lambda: _connect_and_create(settings),
            max_pixel_bytes=settings.max_pixel_bytes,
        )

    @staticmethod
    def requires_install() -> bool:
        return False

    def get_max_parallel_conversions(self) -> int:
        return 1

    def start_doc_to_pixels_proc(self, document: Document) -> subprocess.Popen:
        raise NotImplementedError("ACA sandboxes are not local processes")

    def terminate_doc_to_pixels_proc(
        self, document: Document, p: subprocess.Popen
    ) -> None:
        return

    def convert(
        self,
        document: Document,
        ocr_lang: str | None,
        progress_callback: Callable | None = None,
    ) -> None:
        self.progress_callback = progress_callback
        document.mark_as_converting()
        sandbox: SandboxSession | None = None
        try:
            sandbox = self._create_session()
            self._doc_to_pixels(sandbox, document)
            with self._open_pixels(sandbox) as stream:
                self.convert_from_pixel_stream(document, ocr_lang, stream)
            document.mark_as_safe()
        except Exception:
            log.exception("ACA conversion failed for %s", document.id)
            document.mark_as_failed()
            raise
        finally:
            if sandbox is not None:
                try:
                    sandbox.delete()
                except Exception:
                    log.exception("failed to delete ACA sandbox")

    def _doc_to_pixels(self, sandbox: SandboxSession, document: Document) -> None:
        original = Path(document.input_filename).read_bytes()
        try:
            sandbox.mkdir("/tmp/bluepaper")
        except Exception:
            sandbox.exec("mkdir -p /tmp/bluepaper")
        sandbox.write_file(_INPUT, original)
        sandbox.write_file(_WRAPPER, CONVERT_WRAPPER)
        result = sandbox.exec(f"{_PYTHON} {_WRAPPER}")
        if result.exit_code not in (0, None):
            raise RuntimeError(
                f"doc_to_pixels exited with {result.exit_code}"
            )

    def _open_pixels(self, sandbox: SandboxSession) -> IO[bytes]:
        info = sandbox.stat_file(_PIXELS)
        size = int(getattr(info, "size", 0) or 0)
        if size <= 0:
            raise RuntimeError("sandbox produced no pixel output")
        if size > self.max_pixel_bytes:
            raise RuntimeError("pixel output exceeds budget")
        payload = sandbox.read_file(_PIXELS)
        if isinstance(payload, str):
            payload = payload.encode("latin1")
        tmp = tempfile.SpooledTemporaryFile(max_size=min(size, 32 * 1024 * 1024))
        tmp.write(payload)
        tmp.seek(0)
        return tmp


def _connect_and_create(settings: Settings) -> SandboxSession:
    if not settings.azure_subscription_id or not settings.azure_resource_group:
        raise RuntimeError("Azure subscription and resource group are required")
    if not settings.sandbox_group:
        raise RuntimeError("BLUEPAPER_SANDBOX_GROUP is required")

    from azure.identity import DefaultAzureCredential

    try:
        from azure.containerapps.sandbox import (  # type: ignore[import-untyped]
            EgressPolicy,
            SandboxGroupClient,
            endpoint_for_region,
        )
    except ImportError as exc:  # pragma: no cover - optional at test time
        raise RuntimeError("azure-containerapps-sandbox is not installed") from exc

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(settings.azure_region),
        credential,
        subscription_id=settings.azure_subscription_id,
        resource_group=settings.azure_resource_group,
        sandbox_group=settings.sandbox_group,
    )
    egress = _deny_all_egress(EgressPolicy)
    create_kwargs: dict = {
        "cpu": "2000m",
        "memory": "4096Mi",
        "egress_policy": egress,
        "labels": {"bluepaper": "conversion"},
    }
    if settings.sandbox_disk_id:
        create_kwargs["disk_id"] = settings.sandbox_disk_id
    else:
        create_kwargs["disk"] = "python"
    sandbox = client.begin_create_sandbox(**create_kwargs).result()
    return sandbox  # type: ignore[no-any-return]


def _deny_all_egress(egress_policy_cls: type) -> object:
    try:
        return egress_policy_cls(
            default_action="Deny",
            traffic_inspection="Full",
        )
    except TypeError:
        return egress_policy_cls(default_action="Deny")
