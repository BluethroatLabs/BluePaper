from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from dangerzone.document import Document

from bluepaper.isolation.aca import (
    AcaIsolationProvider,
    _connect_and_create,
    _deny_all_egress,
)
from bluepaper.isolation.dummy import dummy_pixel_protocol
from bluepaper.isolation.session import CONVERT_WRAPPER


class FakeSandbox:
    def __init__(
        self,
        *,
        fail_exec: bool = False,
        huge_pixels: bool = False,
        fail_mkdir: bool = False,
        fail_delete: bool = False,
        empty_pixels: bool = False,
        str_payload: bool = False,
        success_exit_code: int | None = 0,
    ) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted = False
        self.snapshots = 0
        self.commands: list[str] = []
        self.fail_exec = fail_exec
        self.huge_pixels = huge_pixels
        self.fail_mkdir = fail_mkdir
        self.fail_delete = fail_delete
        self.empty_pixels = empty_pixels
        self.str_payload = str_payload
        self.success_exit_code = success_exit_code
        self.exit_code: int | None = 0
        self.stdout = b""
        self.stderr = b""

    def write_file(self, path: str, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[path] = data

    def read_file(self, path: str) -> bytes | str:
        data = self.files[path]
        if self.str_payload:
            return data.decode("latin1")
        return data

    def stat_file(self, path: str) -> SimpleNamespace:
        data = self.files.get(path, b"")
        return SimpleNamespace(size=len(data))

    def mkdir(self, path: str) -> None:
        if self.fail_mkdir:
            raise RuntimeError("mkdir not supported")
        self.files.setdefault(path.rstrip("/") + "/.keep", b"")

    def exec(self, command: str) -> FakeSandbox:
        self.commands.append(command)
        if self.fail_exec:
            self.exit_code = 99
            return self
        if "run_convert.py" in command:
            if self.empty_pixels:
                self.exit_code = self.success_exit_code
                return self
            if self.huge_pixels:
                self.files["/tmp/bluepaper/pixels.bin"] = b"x" * (1024 * 1024)
            else:
                self.files["/tmp/bluepaper/pixels.bin"] = dummy_pixel_protocol()
            self.exit_code = self.success_exit_code
        return self

    def create_snapshot(self, name: str) -> None:
        self.snapshots += 1

    def delete(self) -> None:
        self.deleted = True
        if self.fail_delete:
            raise RuntimeError("delete failed")


def test_aca_deletes_sandbox_and_never_snapshots(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024 * 1024)
    src = tmp_path / "in.pdf"
    dst = tmp_path / "out-safe.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    doc = Document(str(src), str(dst))
    provider.convert(doc, None)
    assert doc.is_safe()
    assert sandbox.deleted is True
    assert sandbox.snapshots == 0
    assert CONVERT_WRAPPER.encode("utf-8") in sandbox.files["/tmp/bluepaper/run_convert.py"]
    assert dst.is_file()
    assert dst.read_bytes().startswith(b"%PDF")


def test_aca_delete_on_failure(tmp_path: Path) -> None:
    sandbox = FakeSandbox(fail_exec=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024)
    src = tmp_path / "in.pdf"
    dst = tmp_path / "out-safe.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    doc = Document(str(src), str(dst))
    with pytest.raises(RuntimeError):
        provider.convert(doc, None)
    assert sandbox.deleted is True
    assert doc.is_failed()


def test_aca_pixel_budget(tmp_path: Path) -> None:
    sandbox = FakeSandbox(huge_pixels=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=16)
    src = tmp_path / "in.pdf"
    dst = tmp_path / "out-safe.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    doc = Document(str(src), str(dst))
    with pytest.raises(RuntimeError, match="pixel output exceeds budget"):
        provider.convert(doc, None)
    assert sandbox.deleted is True


def _pdf_doc(tmp_path: Path) -> Document:
    src = tmp_path / "in.pdf"
    dst = tmp_path / "out-safe.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    return Document(str(src), str(dst))


def test_aca_mkdir_fallback_and_none_exit_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox(fail_mkdir=True, success_exit_code=None)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024 * 1024)
    provider.convert(_pdf_doc(tmp_path), None)
    assert any("mkdir -p /tmp/bluepaper" in cmd for cmd in sandbox.commands)
    assert sandbox.deleted is True


def test_aca_accepts_string_pixel_payload(tmp_path: Path) -> None:
    sandbox = FakeSandbox(str_payload=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024 * 1024)
    doc = _pdf_doc(tmp_path)
    provider.convert(doc, None)
    assert doc.is_safe()
    assert Path(doc.output_filename).read_bytes().startswith(b"%PDF")


def test_aca_empty_pixels(tmp_path: Path) -> None:
    sandbox = FakeSandbox(empty_pixels=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024)
    with pytest.raises(RuntimeError, match="no pixel output"):
        provider.convert(_pdf_doc(tmp_path), None)
    assert sandbox.deleted is True


def test_aca_delete_failure_does_not_mask_success(tmp_path: Path) -> None:
    sandbox = FakeSandbox(fail_delete=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024 * 1024)
    doc = _pdf_doc(tmp_path)
    provider.convert(doc, None)
    assert doc.is_safe()
    assert sandbox.deleted is True


def test_aca_delete_failure_does_not_mask_convert_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(fail_exec=True, fail_delete=True)
    provider = AcaIsolationProvider(lambda: sandbox, max_pixel_bytes=1024)
    with pytest.raises(RuntimeError, match="exited with 99"):
        provider.convert(_pdf_doc(tmp_path), None)
    assert sandbox.deleted is True


def test_aca_create_session_failure_marks_failed(tmp_path: Path) -> None:
    def explode() -> FakeSandbox:
        raise RuntimeError("no sandbox")

    provider = AcaIsolationProvider(explode, max_pixel_bytes=1024)
    doc = _pdf_doc(tmp_path)
    with pytest.raises(RuntimeError, match="no sandbox"):
        provider.convert(doc, None)
    assert doc.is_failed()


def test_aca_is_not_a_local_process() -> None:
    provider = AcaIsolationProvider(lambda: FakeSandbox(), max_pixel_bytes=1)
    assert provider.requires_install() is False
    assert provider.get_max_parallel_conversions() == 1
    with pytest.raises(NotImplementedError, match="not local processes"):
        provider.start_doc_to_pixels_proc(Document())
    provider.terminate_doc_to_pixels_proc(Document(), None)  # type: ignore[arg-type]


def test_aca_from_settings_uses_pixel_budget(settings) -> None:
    settings.max_pixel_bytes = 12345
    provider = AcaIsolationProvider.from_settings(settings)
    assert provider.max_pixel_bytes == 12345


def test_connect_requires_subscription_and_group(settings) -> None:
    with pytest.raises(RuntimeError, match="subscription"):
        _connect_and_create(settings)
    settings.azure_subscription_id = "sub"
    settings.azure_resource_group = "rg"
    settings.sandbox_group = None
    with pytest.raises(RuntimeError, match="SANDBOX_GROUP"):
        _connect_and_create(settings)
    settings.sandbox_group = "bluepaper-sandboxes"
    with pytest.raises(RuntimeError, match="SANDBOX_DISK"):
        _connect_and_create(settings)


def test_deny_all_egress_prefers_full_inspection() -> None:
    class FullPolicy:
        def __init__(self, default_action: str, traffic_inspection: str) -> None:
            self.default_action = default_action
            self.traffic_inspection = traffic_inspection

    policy = _deny_all_egress(FullPolicy)
    assert policy.default_action == "Deny"  # type: ignore[union-attr]
    assert policy.traffic_inspection == "Full"  # type: ignore[union-attr]


def test_deny_all_egress_falls_back_without_inspection() -> None:
    class DenyOnly:
        def __init__(self, default_action: str) -> None:
            self.default_action = default_action

    policy = _deny_all_egress(DenyOnly)
    assert policy.default_action == "Deny"  # type: ignore[union-attr]


def test_convert_wrapper_rewires_stdio() -> None:
    assert "dangerzone.conversion.doc_to_pixels" in CONVERT_WRAPPER
    assert "/tmp/bluepaper/input.bin" in CONVERT_WRAPPER
    assert "/tmp/bluepaper/pixels.bin" in CONVERT_WRAPPER
