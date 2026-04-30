"""
Structural invariants for pact-plugin/commands/watch-inbox.md (Arm role).

File-parsing assertions only — no command execution. Pin section
presence, alarm-clock framing, F1/F7 invariants, 30/60/120 timing
constants and dual-ratio audit anchors, state-machine edge tokens,
WriteStateFile schema, lead-only narrowing, and negative invariants
(no Cron, no Recovery, no symmetric per-agent tokens).

Cycle 4 audit allocation per Task #52:
- alarm-clock framing → watch-inbox (governs Monitor's behavior)
- 30/60/120 design ratios + dual-ratio audit → watch-inbox
- F1 (single-file inbox), F7 (stdout discipline) → watch-inbox
- bidirectional drift warnings → watch-inbox
- F6 (TaskStop tolerates not-found) → unwatch-inbox (separate file)
"""

from pathlib import Path

import pytest

CMD_PATH = (
    Path(__file__).resolve().parent.parent
    / "commands"
    / "watch-inbox.md"
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
    assert "inbox-watch" in head or "watch-inbox" in head


def test_command_body_under_compaction_budget(cmd_text):
    # ~120L target per Task #52; 200-line ceiling allows headroom for
    # future revisions while preventing unbounded growth.
    line_count = len(cmd_text.splitlines())
    assert line_count <= 200, (
        f"watch-inbox.md has {line_count} lines, exceeds 200 cap"
    )


# ---------- Section-presence invariants ----------

REQUIRED_SECTIONS = [
    "## Overview",
    "## When to Invoke",
    "## Operation",
    "## Monitor Block",
    "## WriteStateFile Block",
    "## Failure Modes",
    "## Verification",
    "## References",
]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_section_present(cmd_text, section):
    assert any(line.strip() == section for line in cmd_text.splitlines()), (
        f"missing required section header: {section}"
    )


def test_no_separate_arm_or_teardown_subsection(cmd_text):
    """Cycle 4: command IS the operation, no Arm/Teardown sub-headers
    inside ## Operation. Per Task #52: 'no `## Operations` sub-section
    listing Arm/Teardown'."""
    body = _section_body(cmd_text, "## Operation")
    assert "### Arm" not in body
    assert "### Teardown" not in body
    # Recovery never reintroduced — D1 fence.
    assert "### Recovery" not in body


# ---------- Negative invariants (D1 fence) ----------

@pytest.mark.parametrize("forbidden", [
    "## Cron Block",
    "## Wake-State-Check Algorithm",
    "## Per-Branch Action Sequences",
    "## Recovery",
    "_WAKE_ARM_TEMPLATE",
    "{agent_name}",
])
def test_forbidden_token_absent(cmd_text, forbidden):
    assert forbidden not in cmd_text, (
        f"forbidden token reintroduced: {forbidden}"
    )


# ---------- F1: single-file inbox + wc -c byte-grow ----------

def test_f1_single_file_inbox_path_hardcoded(cmd_text):
    monitor = _section_body(cmd_text, "## Monitor Block")
    assert "inboxes/team-lead.json" in monitor
    assert "wc -c" in monitor


def test_f1_single_file_inbox_phrase(cmd_text):
    """Narrative anchor must mark the inbox as a single JSON file (not
    a directory)."""
    assert "single JSON file" in cmd_text
    overview = _section_body(cmd_text, "## Overview")
    assert "team-lead.json" in overview


# ---------- F7: stdout discipline + between-tool-call scope ----------

def test_f7_between_tool_calls_not_mid_tool(cmd_text):
    overview = _section_body(cmd_text, "## Overview")
    assert "between tool calls" in overview
    assert "not mid-tool" in overview or "instant interrupt anywhere" in overview


def test_f7_stdout_discipline_in_monitor_block(cmd_text):
    monitor = _section_body(cmd_text, "## Monitor Block")
    assert "Stdout discipline" in monitor
    assert "INBOX_GREW" in monitor
    assert ">&2" in monitor


def test_long_single_tool_failure_mode_with_empirical_anchor(cmd_text):
    fm = _section_body(cmd_text, "## Failure Modes")
    assert "Long single-tool calls block wake delivery" in fm
    # Empirical timing anchor — without it the scope claim could be
    # silently edited away.
    assert (
        "2026-04-30" in fm
        and "00:01:34" in fm
        and "00:02:23" in fm
    )


# ---------- Alarm-clock framing (audit-anchor invariant) ----------

def test_alarm_clock_paragraph_present(cmd_text):
    overview = _section_body(cmd_text, "## Overview")
    assert "Monitor is an alarm clock, not a mailbox" in overview


def test_alarm_clock_no_narration_clause(cmd_text):
    overview = _section_body(cmd_text, "## Overview")
    assert (
        "without emitting acknowledgment text" in overview
        or "no narration" in overview.lower()
        or "return to idle" in overview
    )


# ---------- 30/60/120 timing fences ----------

@pytest.mark.parametrize("token", [
    "POLL=30",
    "QUIET_REQUIRED=60",
    "MAX_DELAY=120",
])
def test_timing_constant_present(cmd_text, token):
    monitor = _section_body(cmd_text, "## Monitor Block")
    assert token in monitor


def test_audit_documents_quiet_equals_two_times_poll(cmd_text):
    """Pin the QUIET = 2*POLL design rationale anchor in the Monitor
    Block audit annotation. Robust substring fallback set."""
    monitor = _section_body(cmd_text, "## Monitor Block")
    candidates = [
        "two consecutive quiet poll cycles",
        "two quiet poll cycles",
        "QUIET = 2*POLL",
        "QUIET = 2 * POLL",
        "QUIET_REQUIRED = 2*POLL",
        "QUIET_REQUIRED = 2 * POLL",
        "2 × POLL",
    ]
    assert any(c in monitor for c in candidates), (
        "Monitor Block audit must anchor QUIET = 2*POLL design choice. "
        f"None of {candidates} found in §Monitor Block."
    )


def test_audit_documents_max_delay_ratio_to_quiet(cmd_text):
    """Pin the MAX_DELAY = 2*QUIET (= 4*POLL) design-choice anchor.
    Robust substring fallback set."""
    monitor = _section_body(cmd_text, "## Monitor Block")
    candidates = [
        "MAX_DELAY = 2*QUIET",
        "MAX_DELAY = 2 * QUIET",
        "MAX_DELAY = 4*POLL",
        "MAX_DELAY = 4 * POLL",
        "twice QUIET",
        "2 × QUIET",
        "4 × POLL",
        "two QUIET",
    ]
    assert any(c in monitor for c in candidates), (
        "Monitor Block audit must anchor MAX_DELAY = 2*QUIET (= 4*POLL) "
        f"design choice. None of {candidates} found in §Monitor Block."
    )


# ---------- State machine edge tokens ----------

@pytest.mark.parametrize("edge", ["FIRST_GROW", "LAST_GROW", "MAX_DELAY"])
def test_state_machine_edge_token_present(cmd_text, edge):
    monitor = _section_body(cmd_text, "## Monitor Block")
    assert edge in monitor


def test_state_machine_states_present(cmd_text):
    monitor = _section_body(cmd_text, "## Monitor Block")
    assert "PENDING" in monitor
    assert "GROWING" in monitor


# ---------- WriteStateFile schema (lead-only fence) ----------

def test_writestate_block_has_three_fields(cmd_text):
    block = _section_body(cmd_text, "## WriteStateFile Block")
    for field in ("v", "monitor_task_id", "armed_at"):
        assert f'"{field}"' in block, f"missing schema field: {field}"


def test_writestate_block_no_watchdog_tokens(cmd_text):
    block = _section_body(cmd_text, "## WriteStateFile Block")
    assert "cron_job_id" not in block
    assert "heartbeat" not in block


def test_writestate_path_is_lead_only_fixed(cmd_text):
    block = _section_body(cmd_text, "## WriteStateFile Block")
    assert "inbox-wake-state.json" in block
    assert "{agent-name}" not in block


# ---------- Lead-only narrowing ----------

def test_lead_only_scope_in_overview(cmd_text):
    overview = _section_body(cmd_text, "## Overview")
    assert "Single-Monitor model" in overview
    assert "Lead-only" in cmd_text or "lead-only" in cmd_text.lower()


# ---------- Failure Modes coverage ----------

@pytest.mark.parametrize("entry", [
    "Silent Monitor death",
    "Long single-tool calls block wake delivery",
    "Wake-fire inflation under bursty traffic",
    "Malformed STATE_FILE",
])
def test_failure_modes_entry_present(cmd_text, entry):
    fm = _section_body(cmd_text, "## Failure Modes")
    assert entry in fm


# ---------- Cross-link to unwatch-inbox ----------

def test_references_section_links_to_unwatch_inbox(cmd_text):
    refs = _section_body(cmd_text, "## References")
    assert "unwatch-inbox" in refs
