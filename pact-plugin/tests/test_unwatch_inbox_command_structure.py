"""
Structural invariants for pact-plugin/commands/unwatch-inbox.md (Teardown role).

File-parsing assertions only — no command execution. Pin section
presence, F6 'ignoring not-found errors' literal, best-effort framing,
Teardown ordering load-bearing, missing_ok=True usage, and the
cross-link to watch-inbox.

Cycle 4 audit allocation per Task #52:
- F6 (TaskStop tolerates not-found) → unwatch-inbox
- best-effort framing → unwatch-inbox
- 'wake mechanism is opportunistic' brief mention → unwatch-inbox
"""

from pathlib import Path

import pytest

CMD_PATH = (
    Path(__file__).resolve().parent.parent
    / "commands"
    / "unwatch-inbox.md"
)


@pytest.fixture(scope="module")
def cmd_text() -> str:
    return CMD_PATH.read_text(encoding="utf-8")


def _section_body(text: str, header: str) -> str:
    """Return the body between `header` and the next same-or-higher level header."""
    lines = text.splitlines()
    level = len(header) - len(header.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        line = lines[j].strip()
        if line.startswith("#"):
            this_level = len(line) - len(line.lstrip("#"))
            if this_level <= level:
                end = j
                break
    return "\n".join(lines[start:end])


# ---------- Frontmatter / file-level invariants ----------

def test_command_file_exists():
    assert CMD_PATH.exists()


def test_frontmatter_has_description(cmd_text):
    """Slash-commands use filename as identity (no `name:` field).
    Frontmatter must carry at least a description."""
    assert cmd_text.startswith("---\n")
    head, _, _ = cmd_text[4:].partition("\n---\n")
    assert "description:" in head
    assert "inbox-watch" in head or "unwatch-inbox" in head or "Tear down" in head


def test_command_body_under_compaction_budget(cmd_text):
    # ~50L target per Task #52; 100-line ceiling for headroom.
    line_count = len(cmd_text.splitlines())
    assert line_count <= 100, (
        f"unwatch-inbox.md has {line_count} lines, exceeds 100 cap"
    )


# ---------- Section-presence invariants ----------

REQUIRED_SECTIONS = [
    "## Overview",
    "## When to Invoke",
    "## Operation",
    "## Teardown Block",
    "## Failure Modes",
    "## Verification",
    "## References",
]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_section_present(cmd_text, section):
    assert any(line.strip() == section for line in cmd_text.splitlines()), (
        f"missing required section header: {section}"
    )


def test_no_arm_or_recovery_subsection(cmd_text):
    """Cycle 4: command IS the operation, no Arm/Recovery sub-sections.
    unwatch-inbox is the Teardown command; Arm logic lives in
    watch-inbox.md."""
    body = _section_body(cmd_text, "## Operation")
    assert "### Arm" not in body
    assert "### Recovery" not in body


# ---------- F6: TaskStop tolerance literal ----------

def test_f6_teardown_block_contains_ignoring_not_found(cmd_text):
    """F6 invariant: TaskStop tolerates not-found errors. The literal
    phrase 'ignoring not-found errors' (or the close substitute
    'tolerate not-found') must appear in the Teardown Block."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert (
        "ignoring not-found" in teardown
        or "tolerate not-found" in teardown
    ), (
        "Teardown Block must carry the F6 tolerance phrase. Without it, "
        "an editing LLM 'tightening up error handling' will silently "
        "restore crash-on-stale-ID."
    )


# ---------- Best-effort framing ----------

def test_best_effort_framing_in_overview_or_teardown(cmd_text):
    """The 'best-effort' framing prevents an editing LLM from converting
    Teardown into a strict-required-success operation."""
    full = cmd_text.lower()
    assert "best-effort" in full


def test_opportunistic_wake_rationale_present(cmd_text):
    """Brief 'wake mechanism is opportunistic' rationale anchors why
    Teardown failures are tolerable. Per Task #52: brief mention here;
    main treatment lives in watch-inbox."""
    full = cmd_text.lower()
    assert (
        "opportunistic" in full
        or "no harm done" in full
        or "tolerable" in full
    )


# ---------- Teardown ordering load-bearing ----------

def test_teardown_block_orders_taskstop_before_unlink(cmd_text):
    """Order is load-bearing: TaskStop must precede STATE_FILE unlink.
    Inverse ordering would leave a brief window where a STATE_FILE-less
    Monitor still runs but Arm sees no STATE_FILE and re-arms — orphan."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    stop_idx = teardown.find("TaskStop")
    # Match either "Unlink STATE_FILE" prose or the canonical
    # `Path.unlink(STATE_FILE, missing_ok=True)` form.
    unlink_idx = teardown.find("Path.unlink")
    if unlink_idx == -1:
        unlink_idx = teardown.find("Unlink STATE_FILE")
    assert stop_idx >= 0, "TaskStop reference missing from §Teardown Block"
    assert unlink_idx >= 0, "STATE_FILE unlink reference missing from §Teardown Block"
    assert stop_idx < unlink_idx, (
        "Teardown ordering inverted — TaskStop must precede unlink"
    )


def test_teardown_uses_missing_ok(cmd_text):
    """Path.unlink missing_ok=True is the load-bearing flag — without
    it, the unlink call raises FileNotFoundError when STATE_FILE was
    already removed by an earlier Teardown."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "missing_ok=True" in teardown


# ---------- Failure Modes coverage ----------

@pytest.mark.parametrize("entry_substring", [
    # Either F6-anchored failure mode form is acceptable; an editor may
    # title it differently but the Monitor-died-mid-session concept must
    # appear in §Failure Modes.
    "Monitor",
])
def test_failure_modes_mentions_monitor_died_silently(cmd_text, entry_substring):
    fm = _section_body(cmd_text, "## Failure Modes")
    assert entry_substring in fm


# ---------- Cross-link to watch-inbox ----------

def test_references_section_links_to_watch_inbox(cmd_text):
    refs = _section_body(cmd_text, "## References")
    assert "watch-inbox" in refs
