"""
Predicate-isolation tests for
wake_lifecycle_emitter._is_pending_to_in_progress_transition.

Single-source probe of `tool_input.status == 'in_progress'` per the
empirical fixture constraint at
fixtures/wake_lifecycle/task_update_production_shape.json (FLAT
tool_response, no statusChange.from). Mirrors the shape of
test_has_same_teammate_continuation.py — predicate isolation only;
integration with _decide_directive lives in
test_wake_lifecycle_teachback_rearm.py.

Counter-test-by-revert (manual / runbook-documented): cp-bak the
emitter, `git checkout HEAD~1 -- pact-plugin/hooks/wake_lifecycle_emitter.py`,
run this module — expect cardinality {7 fail + 1 collection error if
the helper symbol is gone}. See
pact-plugin/tests/runbooks/wake-lifecycle-teachback-rearm.md.
"""

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"


class TestIsPendingToInProgressTransition:
    """Predicate-isolation tests for the new
    _is_pending_to_in_progress_transition helper. Single-source probe of
    `tool_input.status == 'in_progress'` per the empirical fixture
    constraint at fixtures/wake_lifecycle/task_update_production_shape.json
    (FLAT tool_response, no statusChange.from)."""

    @staticmethod
    def _predicate():
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        return emitter._is_pending_to_in_progress_transition

    def test_in_progress_status_returns_true(self):
        pred = self._predicate()
        assert pred({
            "tool_input": {"taskId": "1", "status": "in_progress"},
            "tool_response": {"id": "1"},
        }) is True

    def test_completed_status_returns_false(self):
        pred = self._predicate()
        assert pred({
            "tool_input": {"taskId": "1", "status": "completed"},
            "tool_response": {"id": "1"},
        }) is False

    def test_deleted_status_returns_false(self):
        pred = self._predicate()
        assert pred({
            "tool_input": {"taskId": "1", "status": "deleted"},
            "tool_response": {"id": "1"},
        }) is False

    def test_pending_status_returns_false(self):
        pred = self._predicate()
        assert pred({
            "tool_input": {"taskId": "1", "status": "pending"},
            "tool_response": {"id": "1"},
        }) is False

    def test_metadata_only_returns_false(self):
        """Critical for cheap-predicate-first ordering: a TaskUpdate with
        no status field (e.g., metadata-only handoff write) must return
        False so count_active_tasks is not invoked unnecessarily."""
        pred = self._predicate()
        assert pred({
            "tool_input": {"taskId": "1", "owner": "y"},
            "tool_response": {"id": "1"},
        }) is False

    def test_missing_tool_input_returns_false(self):
        pred = self._predicate()
        assert pred({"tool_response": {"id": "1"}}) is False

    def test_non_dict_tool_input_returns_false(self):
        pred = self._predicate()
        assert pred({
            "tool_input": "not-a-dict",
            "tool_response": {"id": "1"},
        }) is False


# ---------- Production-shape constraint preservation ----------


def test_in_progress_predicate_does_not_consume_statusChange_from():
    """Production-shape constraint anchor: the FLAT TaskUpdate fixture
    (tests/fixtures/wake_lifecycle/task_update_production_shape.json)
    has NO statusChange.from field — production traffic does not
    surface it. The new predicate MUST be single-source on
    tool_input.status; adding a tool_response.statusChange.from probe
    would be a regression vector (a future platform change to make
    tool_input.status optional would silently fall through to a
    redundant probe and mask the breakage).

    Detection: assert the source code of
    _is_pending_to_in_progress_transition does NOT reference
    statusChange.from. Pin the production-shape constraint structurally
    so a future LLM cannot 'simplify by adding a fallback' and break it.
    """
    src_path = HOOK_DIR / "wake_lifecycle_emitter.py"
    src = src_path.read_text(encoding="utf-8")

    # Locate the predicate function body.
    start = src.find("def _is_pending_to_in_progress_transition(")
    assert start != -1, (
        "Predicate _is_pending_to_in_progress_transition not found in "
        f"{src_path}; the Bug B fix may have been reverted."
    )
    # Body extends to the next top-level def (rough but sufficient).
    body_end = src.find("\ndef ", start + 1)
    body = src[start:body_end] if body_end != -1 else src[start:]

    # Strip docstrings + comments before checking — the rule is "no
    # statusChange CONSUMPTION (probe) in code", not "no docstring
    # mention" (the docstring may legitimately explain WHY the probe
    # was rejected).
    in_docstring = False
    code_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) > 6:
            continue  # single-line docstring
        if stripped.startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith('#'):
            continue
        code_lines.append(line)
    code_body = "\n".join(code_lines)

    assert "statusChange" not in code_body, (
        "_is_pending_to_in_progress_transition CODE BODY references "
        "`statusChange` — production fixture "
        "tests/fixtures/wake_lifecycle/task_update_production_shape.json "
        "has NO statusChange field; the predicate must be single-source "
        "on tool_input.status only. See the architect HANDOFF "
        "design_artifacts_v3 EMPIRICAL FALSIFICATION entry. (Docstring "
        "mentions are acceptable; this assertion strips comments and "
        "docstrings before checking.)"
    )
