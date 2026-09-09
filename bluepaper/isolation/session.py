from __future__ import annotations

from typing import Protocol


class ExecResult(Protocol):
    exit_code: int
    stdout: bytes | str | None
    stderr: bytes | str | None


class SandboxSession(Protocol):
    def write_file(self, path: str, data: bytes | str) -> None: ...

    def read_file(self, path: str) -> bytes | str: ...

    def stat_file(self, path: str) -> object: ...

    def mkdir(self, path: str) -> None: ...

    def exec(self, command: str) -> ExecResult: ...

    def delete(self) -> None: ...


CONVERT_WRAPPER = '''\
import runpy
import sys

sys.stdin = open("/tmp/bluepaper/input.bin", "rb")
sys.stdout = open("/tmp/bluepaper/pixels.bin", "wb")
runpy.run_module("dangerzone.conversion.doc_to_pixels", run_name="__main__")
'''
