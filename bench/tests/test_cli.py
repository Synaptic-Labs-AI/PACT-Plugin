from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]


def test_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, str(BENCH / "pact-bench"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "pact-bench prepare" in proc.stdout
