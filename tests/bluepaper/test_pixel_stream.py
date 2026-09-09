from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
import pytest
from dangerzone import conversion_errors as errors
from dangerzone.document import Document

from bluepaper.isolation.dummy import InProcessDummy, dummy_pixel_protocol


class _RecordingStdin:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def close(self) -> None:
        return


def _document(tmp_path: Path) -> Document:
    src = tmp_path / "in.pdf"
    dst = tmp_path / "out-safe.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    return Document(str(src), str(dst))


def test_convert_from_pixel_stream_writes_pdf(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    provider.convert_from_pixel_stream(
        doc, None, BytesIO(dummy_pixel_protocol(pages=3, width=8, height=6))
    )
    output = Path(doc.output_filename)
    assert output.is_file()
    assert output.read_bytes().startswith(b"%PDF")
    with fitz.open(output) as pdf:
        assert pdf.page_count == 3


def test_convert_with_proc_delegates_to_pixel_stream(tmp_path: Path) -> None:
    src = tmp_path / "in.bin"
    dst = tmp_path / "out-safe.pdf"
    payload = b"untrusted-original"
    src.write_bytes(payload)
    doc = Document(str(src), str(dst))

    class FakeProc:
        def __init__(self) -> None:
            self.stdin = _RecordingStdin()
            self.stdout = BytesIO(dummy_pixel_protocol(pages=1))

    proc = FakeProc()
    provider = InProcessDummy()
    provider.progress_callback = None
    provider.convert_with_proc(doc, None, proc)  # type: ignore[arg-type]
    assert bytes(proc.stdin.written) == payload
    assert Path(doc.output_filename).read_bytes().startswith(b"%PDF")


def test_convert_from_pixel_stream_reports_progress(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    seen: list[tuple[bool, str, float]] = []
    provider = InProcessDummy()
    provider.progress_callback = lambda error, text, pct: seen.append(
        (error, text, pct)
    )
    provider.convert_from_pixel_stream(
        doc, None, BytesIO(dummy_pixel_protocol(pages=2))
    )
    assert any("Converted page 1/2 to PDF" in text for _, text, _ in seen)
    assert seen[-1] == (False, "Successfully converted document", 100)


def test_convert_from_pixel_stream_rejects_zero_pages(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    with pytest.raises(errors.MaxPagesException):
        provider.convert_from_pixel_stream(
            doc, None, BytesIO((0).to_bytes(errors.INT_BYTES, "big"))
        )


def test_convert_from_pixel_stream_rejects_too_many_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dangerzone.conversion_errors.MAX_PAGES", 1)
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    with pytest.raises(errors.MaxPagesException):
        provider.convert_from_pixel_stream(
            doc, None, BytesIO(dummy_pixel_protocol(pages=2))
        )


def test_convert_from_pixel_stream_rejects_bad_width(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    too_wide = (
        (1).to_bytes(errors.INT_BYTES, "big")
        + (errors.MAX_PAGE_WIDTH + 1).to_bytes(errors.INT_BYTES, "big")
        + (9).to_bytes(errors.INT_BYTES, "big")
    )
    with pytest.raises(errors.MaxPageWidthException):
        provider.convert_from_pixel_stream(doc, None, BytesIO(too_wide))


def test_convert_from_pixel_stream_rejects_bad_height(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    too_tall = (
        (1).to_bytes(errors.INT_BYTES, "big")
        + (9).to_bytes(errors.INT_BYTES, "big")
        + (errors.MAX_PAGE_HEIGHT + 1).to_bytes(errors.INT_BYTES, "big")
    )
    with pytest.raises(errors.MaxPageHeightException):
        provider.convert_from_pixel_stream(doc, None, BytesIO(too_tall))


def test_convert_from_pixel_stream_rejects_truncated_pixels(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    provider = InProcessDummy()
    provider.progress_callback = None
    truncated = (
        (1).to_bytes(errors.INT_BYTES, "big")
        + (9).to_bytes(errors.INT_BYTES, "big")
        + (9).to_bytes(errors.INT_BYTES, "big")
        + b"short"
    )
    with pytest.raises(errors.ConverterProcException):
        provider.convert_from_pixel_stream(doc, None, BytesIO(truncated))


def test_in_process_dummy_convert_marks_safe(tmp_path: Path) -> None:
    doc = _document(tmp_path)
    InProcessDummy().convert(doc, None)
    assert doc.is_safe()
    assert Path(doc.output_filename).is_file()


def test_in_process_dummy_convert_marks_failed_on_bad_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bluepaper.isolation.dummy.dummy_pixel_protocol", lambda *args, **kwargs: b""
    )
    doc = _document(tmp_path)
    with pytest.raises(errors.ConverterProcException):
        InProcessDummy().convert(doc, None)
    assert doc.is_failed()


def test_in_process_dummy_is_not_a_subprocess() -> None:
    provider = InProcessDummy()
    assert provider.requires_install() is False
    assert provider.get_max_parallel_conversions() == 1
    with pytest.raises(NotImplementedError):
        provider.start_doc_to_pixels_proc(Document())
