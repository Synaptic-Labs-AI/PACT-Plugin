"""
Location: pact-plugin/tests/test_turn_end_jobs.py
Summary: Which running background_tasks entries count as jobs for each role
         (hooks/shared/turn_end_jobs.py).
Used by: the pact-plugin test suite.
"""

import ast
from pathlib import Path

import pytest

from shared import turn_end_jobs as jobs

SOURCE = Path(__file__).resolve().parent.parent / "hooks" / "shared" / "turn_end_jobs.py"

# Every label the platform's background_tasks builder produces.
EVERY_LABEL = (
    "shell", "subagent", "monitor", "workflow", "MCP task",
    "teammate", "dream", "auto-mode scan", "cloud session",
)
NEVER_COUNTED = frozenset({"teammate", "dream", "auto-mode scan", "cloud session"})

# Labels captured live in the probe window's background_tasks frames.
CAPTURED_LABELS = ("shell", "subagent", "teammate")

EXPECTED = {
    "lead": {"shell"},
    "separate-process teammate": {"shell", "subagent", "monitor", "workflow", "MCP task"},
    "in-process teammate": {"shell"},
}
ROLE_SETS = {
    "lead": jobs.LEAD_JOB_TYPES,
    "separate-process teammate": jobs.OWN_PROCESS_JOB_TYPES,
    "in-process teammate": jobs.IN_PROCESS_TEAMMATE_JOB_TYPES,
}


def frame_with(labels) -> dict:
    return {"background_tasks": [
        {"id": f"b-{label}", "type": label, "status": "running"} for label in labels
    ]}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_each_role_counts_exactly_its_allowlist(role):
    frame = frame_with(EVERY_LABEL + ("future-type", "local_bash"))
    counted = {e["type"] for e in jobs.running_jobs(frame, ROLE_SETS[role])}
    assert counted == EXPECTED[role]


def test_the_union_is_every_role_set():
    assert jobs.ANY_JOB_TYPES == set().union(*EXPECTED.values())
    frame = frame_with(EVERY_LABEL)
    assert {e["type"] for e in jobs.running_jobs(frame)} == jobs.ANY_JOB_TYPES


def test_a_type_the_platform_adds_later_is_not_counted():
    frame = frame_with(("future-type", "shell-v2", "local_bash"))
    for role_set in (*ROLE_SETS.values(), jobs.ANY_JOB_TYPES):
        assert jobs.running_jobs(frame, role_set) == []


def test_non_running_or_idless_entries_never_count():
    frame = {"background_tasks": [
        {"id": "b1", "type": "shell", "status": "completed"},
        {"id": "", "type": "shell", "status": "running"},
        {"id": 3, "type": "shell", "status": "running"},
        {"type": "shell", "status": "running"},
        "b1",
        None,
    ]}
    assert jobs.running_jobs(frame) == []
    for not_a_frame in ({}, {"background_tasks": None}, {"background_tasks": "shell"}, None, []):
        assert jobs.running_jobs(not_a_frame) == []


def test_the_module_imports_nothing():
    """Nothing but the __future__ directive the py39 compatibility rule
    requires. Any real import would reach the Stop hook's fast path, which
    loads this file by path precisely to avoid importing `shared`."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imports = [
        node.module if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert imports == ["__future__"]


def test_captured_frame_labels_are_all_classified():
    unclassified = [
        label for label in CAPTURED_LABELS
        if label not in jobs.ANY_JOB_TYPES and label not in NEVER_COUNTED
    ]
    assert unclassified == []
    assert NEVER_COUNTED.isdisjoint(jobs.ANY_JOB_TYPES)
