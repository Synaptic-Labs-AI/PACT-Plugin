from __future__ import annotations

import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]


def test_plan_json_files_are_valid() -> None:
    for path in sorted((BENCH / "plans").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema"] == "pact-bench/plan/1"
        assert data["id"]
        assert isinstance(data["external"], list)
