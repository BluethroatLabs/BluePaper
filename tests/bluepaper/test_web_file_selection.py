from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("web_file_selection.test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_rejected_file_clears_previous_selection() -> None:
    completed = subprocess.run(
        ["node", str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
