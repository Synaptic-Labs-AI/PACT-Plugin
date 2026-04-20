"""
Drift test for orchestrate.md TaskCreate metadata propagation (#401 Commit #9).

At each of the 4 active agent-dispatch TaskCreate sites (preparer, architect,
coder, test-engineer), the TaskCreate invocation MUST include metadata with
`variety` and `required_scope_items` keys. Carve-out sites (auditor with
completion_type=signal, secretary with no metadata) are explicitly excluded.

Any drift from this contract — e.g., a future edit that drops `metadata=`
from a dispatch site, or a new site that forgets the keys — will produce
agent tasks that fail task_schema_validator.py at TaskCreated time.
"""
from pathlib import Path

import pytest

ORCHESTRATE_MD = Path(__file__).parent.parent / "commands" / "orchestrate.md"

# Active dispatch sites — each requires metadata with variety + required_scope_items
_ACTIVE_AGENT_SUBJECTS = (
    'subject="preparer: research {feature}"',
    'subject="architect: design {feature}"',
    'subject="{coder-type}: implement {scope}"',
    'subject="test-engineer: test {feature}"',
)

# Carve-out sites — MUST NOT have variety/required_scope_items (signal task)
_CARVEOUT_SUBJECTS = (
    'subject="auditor: concurrent quality observation"',
)


def _read() -> str:
    return ORCHESTRATE_MD.read_text()


def _find_taskcreate_line(text: str, subject_marker: str) -> str:
    """Return the TaskCreate(...) line containing subject_marker. Fails the test
    if absent or multiple hits (subject markers are expected to be unique).
    """
    hits = [line for line in text.splitlines() if subject_marker in line and "TaskCreate(" in line]
    assert len(hits) == 1, (
        f"Expected exactly 1 TaskCreate line with {subject_marker!r}; found {len(hits)}"
    )
    return hits[0]


@pytest.mark.parametrize("subject_marker", _ACTIVE_AGENT_SUBJECTS)
def test_active_dispatch_site_has_variety_metadata(subject_marker: str) -> None:
    text = _read()
    line = _find_taskcreate_line(text, subject_marker)
    assert "metadata=" in line, (
        f"Active dispatch site for {subject_marker!r} is missing metadata= kwarg. "
        f"Line: {line}"
    )
    assert '"variety"' in line, (
        f"Active dispatch site for {subject_marker!r} is missing 'variety' key. "
        f"Line: {line}"
    )
    assert '"required_scope_items"' in line, (
        f"Active dispatch site for {subject_marker!r} is missing 'required_scope_items' key. "
        f"Line: {line}"
    )
    assert '"phase"' in line, (
        f"Active dispatch site for {subject_marker!r} is missing 'phase' key. "
        f"Line: {line}"
    )
    for dim in ("novelty", "scope", "uncertainty", "risk", "total"):
        assert f'"{dim}"' in line, (
            f"Active dispatch site for {subject_marker!r} is missing variety dimension "
            f"{dim!r}. Line: {line}"
        )


@pytest.mark.parametrize("subject_marker", _CARVEOUT_SUBJECTS)
def test_carveout_dispatch_site_omits_variety_metadata(subject_marker: str) -> None:
    text = _read()
    line = _find_taskcreate_line(text, subject_marker)
    assert '"variety"' not in line, (
        f"Carve-out dispatch site for {subject_marker!r} must NOT include 'variety' "
        f"(signal tasks bypass the gate by predicate). Line: {line}"
    )
    assert '"required_scope_items"' not in line, (
        f"Carve-out dispatch site for {subject_marker!r} must NOT include "
        f"'required_scope_items' (signal tasks bypass the gate by predicate). "
        f"Line: {line}"
    )


def test_variety_scoring_preamble_section_present() -> None:
    text = _read()
    assert "## Per-Agent Variety Scoring" in text, (
        "orchestrate.md missing the Per-Agent Variety Scoring preamble section. "
        "This section explains the dispatch-site metadata contract."
    )
    assert "Imperative-with-Explanation Framing" in text, (
        "orchestrate.md missing the Imperative-with-Explanation Framing subsection. "
        "Q5 Phase 1 framing is required to keep the ritual floor in place."
    )
