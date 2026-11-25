from __future__ import annotations

import json
import sys
from pathlib import Path

from tasks import validate


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_validator_accepts_non_vacuous_task(tmp_path: Path) -> None:
    task = tmp_path / "calc"
    _write(task / "fixture" / "calc.py", "def value():\n    return 1\n")
    _write(task / "fixture" / "tests" / "test_calc.py", "from calc import value\n\ndef test_seed():\n    assert isinstance(value(), int)\n")
    _write(task / "hidden_tests" / "test_hidden.py", "from calc import value\n\ndef test_fix():\n    assert value() == 2\n")
    _write(
        task / "gold.patch",
        "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n",
    )
    _write(task / "prompt.md", "Make value return 2. Work autonomously.\n")
    _write(task / "rubric.md", "Score 0-5.\n")
    (task / "task.json").write_text(
        json.dumps(
            {
                "schema": "pact-bench/task/1",
                "id": "calc",
                "title": "Calc",
                "class": "low",
                "variety": 5,
                "revision": 1,
                "timeout_seconds": 60,
                "max_turns": 5,
                "prompt": "prompt.md",
                "fixture": "fixture",
                "hidden_tests": "hidden_tests",
                "gold_patch": "gold.patch",
                "rubric": "rubric.md",
            }
        ),
        encoding="utf-8",
    )
    result = validate.validate_task(task, sys.executable)
    assert result["hidden_before"] == "fail"
    assert result["hidden_after"] == "pass"
    assert result["seed_after"] == "pass"
