from __future__ import annotations

import subprocess
from collections.abc import Callable
from io import BytesIO

from dangerzone.conversion_errors import INT_BYTES
from dangerzone.document import Document
from dangerzone.isolation_provider.base import IsolationProvider


def dummy_pixel_protocol(pages: int = 2, width: int = 9, height: int = 9) -> bytes:
    out = pages.to_bytes(INT_BYTES, "big", signed=False)
    for _ in range(pages):
        out += width.to_bytes(INT_BYTES, "big", signed=False)
        out += height.to_bytes(INT_BYTES, "big", signed=False)
        out += width * height * 3 * b"A"
    return out


class InProcessDummy(IsolationProvider):
    """Trusted-plane dummy: no subprocess, so stdout cannot pollute the pixel protocol."""

    @staticmethod
    def requires_install() -> bool:
        return False

    def get_max_parallel_conversions(self) -> int:
        return 1

    def start_doc_to_pixels_proc(self, document: Document) -> subprocess.Popen:
        raise NotImplementedError("in-process dummy does not spawn a converter")

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
        try:
            self.convert_from_pixel_stream(
                document, ocr_lang, BytesIO(dummy_pixel_protocol())
            )
            document.mark_as_safe()
        except Exception:
            document.mark_as_failed()
            raise
