"""
Drift test for variety-scoring metadata across command dispatch sites (#401 Commit #10).

Five command files dispatch agent tasks that must include `metadata.variety`
+ `metadata.required_scope_items` at TaskCreate time to satisfy the schema
validator (#5) and gate (#7) introduced by #401:

  comPACT.md       (concurrent + single specialist)
  rePACT.md        (nested cycle specialist)
  plan-mode.md     (consultant dispatch)
  peer-review.md   (reviewer dispatch)
  imPACT.md        (resolution / retry dispatch)

Each file's active agent-dispatch TaskCreate line — identified by subject
marker — must carry the three keys. A future edit that drops them will
break the schema validator at TaskCreated time.
"""
from pathlib import Path

import pytest

COMMANDS_DIR = Path(__file__).parent.parent / "commands"

# (filename, subject_marker) — at least one concrete TaskCreate in each file
# must carry the full metadata shape. imPACT.md is prose-heavy; its retry-phase
# dispatch carries the reference via a per-phase metadata example.
_ACTIVE_DISPATCH_SITES = [
    ("comPACT.md", 'subject="{specialist}: {sub-task}"'),
    ("comPACT.md", 'subject="{specialist}: {task}"'),
    ("rePACT.md", 'subject="{scope-prefixed-name}: implement {sub-task}"'),
    ("plan-mode.md", 'subject="{specialist}: plan consultation for {feature}"'),
    ("peer-review.md", 'subject="{reviewer-type}: review {feature}"'),
]

# Files expected to carry at least the metadata= kwarg signature somewhere.
# imPACT.md guidance is prose-framed, but must include the metadata contract
# explicitly.
_FILES_REFERENCING_METADATA = [
    "comPACT.md",
    "rePACT.md",
    "plan-mode.md",
    "peer-review.md",
    "imPACT.md",
]


def _read(filename: str) -> str:
    return (COMMANDS_DIR / filename).read_text()


def _find_taskcreate_line(text: str, subject_marker: str) -> str:
    hits = [
        line for line in text.splitlines()
        if subject_marker in line and "TaskCreate(" in line
    ]
    assert len(hits) == 1, (
        f"Expected exactly 1 TaskCreate line with {subject_marker!r}; found {len(hits)}"
    )
    return hits[0]


@pytest.mark.parametrize("filename,subject_marker", _ACTIVE_DISPATCH_SITES)
def test_active_dispatch_site_has_variety_metadata(
    filename: str, subject_marker: str
) -> None:
    text = _read(filename)
    line = _find_taskcreate_line(text, subject_marker)
    assert "metadata=" in line, (
        f"{filename}: active dispatch site for {subject_marker!r} missing "
        f"metadata= kwarg. Line: {line}"
    )
    assert '"variety"' in line, (
        f"{filename}: active dispatch site for {subject_marker!r} missing "
        f"'variety' key. Line: {line}"
    )
    assert '"required_scope_items"' in line, (
        f"{filename}: active dispatch site for {subject_marker!r} missing "
        f"'required_scope_items' key. Line: {line}"
    )
    assert '"phase"' in line, (
        f"{filename}: active dispatch site for {subject_marker!r} missing "
        f"'phase' key. Line: {line}"
    )
    for dim in ("novelty", "scope", "uncertainty", "risk", "total"):
        assert f'"{dim}"' in line, (
            f"{filename}: active dispatch site for {subject_marker!r} missing "
            f"variety dimension {dim!r}. Line: {line}"
        )


@pytest.mark.parametrize("filename", _FILES_REFERENCING_METADATA)
def test_file_references_variety_scoring_contract(filename: str) -> None:
    """Each command file involved in agent dispatch must carry at least one
    reference to the dispatch-time variety-scoring contract — either an inline
    metadata= block, or a link to orchestrate.md Per-Agent Variety Scoring
    for prose-heavy files.
    """
    text = _read(filename)
    has_inline_metadata = '"variety"' in text and '"required_scope_items"' in text
    has_contract_reference = (
        "per-agent-variety-scoring" in text.lower()
        or "Per-Agent Variety Scoring" in text
    )
    assert has_inline_metadata or has_contract_reference, (
        f"{filename}: no reference to the Per-Agent Variety Scoring contract. "
        f"Expected either an inline metadata= example or a link to "
        f"orchestrate.md#per-agent-variety-scoring-dispatch-time."
    )


def test_impact_md_prose_references_metadata_shape() -> None:
    """imPACT.md guidance for resolution and retry dispatches must spell out
    the variety metadata shape so the orchestrator doesn't forget it in the
    triage path.
    """
    text = _read("imPACT.md")
    assert '"variety"' in text, (
        "imPACT.md: resolution/retry dispatch prose missing the 'variety' "
        "metadata example. The triage path dispatches agent tasks just like "
        "orchestrate.md — without this guidance, schema_validator.py rejects "
        "triage dispatches."
    )
    assert '"required_scope_items"' in text, (
        "imPACT.md: resolution/retry dispatch prose missing the "
        "'required_scope_items' metadata example."
    )
