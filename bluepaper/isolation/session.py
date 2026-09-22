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


# The ghcr.io/freedomofpress/dangerzone/v1 disk is an outer image whose /usr
# points at an inner rootfs. The converter package is not on sys.path, and
# entrypoint.py only sets PYTHONPATH when it launches gVisor. ACA already
# isolates the sandbox, so the wrapper imports the package in place.
# doc_to_pixels reads sys.stdin.buffer and writes sys.stdout.buffer, so the
# files have to be the real standard streams (dup2), not replacement objects.
CONVERT_WRAPPER = '''\
import os
import runpy
import sys

for path in (
    "/opt/dangerzone",
    "/home/dangerzone/dangerzone-image/rootfs/opt/dangerzone",
):
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)

stdin = os.open("/tmp/bluepaper/input.bin", os.O_RDONLY)
os.dup2(stdin, 0)
os.close(stdin)
stdout = os.open(
    "/tmp/bluepaper/pixels.bin",
    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    0o644,
)
os.dup2(stdout, 1)
os.close(stdout)

if hasattr(os, "geteuid") and os.geteuid() == 0 and os.path.isdir("/home/dangerzone"):
    os.environ["HOME"] = "/home/dangerzone"
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)

runpy.run_module("dangerzone.conversion.doc_to_pixels", run_name="__main__")
'''
