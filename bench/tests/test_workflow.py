from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "bench-harness.yml"


def test_workflow_is_read_only_and_hermetic() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text
    assert "bash bench/run_hermetic.sh" in text
    assert text.count("python scripts/verify_ci_environment.py") == 2
    assert "if: always()" in text
    assert not re.search(r"secrets\.|ANTHROPIC|OPENAI|claude\s+-p|claude plugin eval", text)
    uses = re.findall(r"uses: [^@\s]+@([0-9a-f]{40})", text)
    assert len(uses) == 2


def test_workflow_is_path_triggered() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '"bench/**"' in text
    assert "pull_request:" in text
