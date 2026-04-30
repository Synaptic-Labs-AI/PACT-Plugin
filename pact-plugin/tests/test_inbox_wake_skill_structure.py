"""
Structural invariants for pact-plugin/skills/inbox-wake/SKILL.md.

File-parsing assertions only — no skill execution. Pin section presence,
load-bearing literal phrases (F1/F6/F7), the alarm-clock framing,
20/20/60 timing constants, lead-only narrowing, and negative invariants
(no Cron, no Recovery, no symmetric per-agent tokens).
"""

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "inbox-wake"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


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

def test_skill_file_exists():
    assert SKILL_PATH.exists()


def test_frontmatter_has_name_and_description(skill_text):
    assert skill_text.startswith("---\n")
    head, _, _ = skill_text[4:].partition("\n---\n")
    assert "name: inbox-wake" in head
    assert "description:" in head


def test_skill_body_under_compaction_budget(skill_text):
    # Tier-1 compaction restoration ceiling per #594. Allows headroom for
    # future revisions while preventing unbounded skill-body growth.
    line_count = len(skill_text.splitlines())
    assert line_count <= 292, f"skill body has {line_count} lines, exceeds 292 cap"


# ---------- Section-presence invariants ----------

REQUIRED_SECTIONS = [
    "# Inbox-Wake Skill",
    "## Overview",
    "## When to Invoke",
    "## Operations",
    "## Monitor Block",
    "## WriteStateFile Block",
    "## Teardown Block",
    "## Failure Modes",
    "## Verification",
    "## References",
]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_section_present(skill_text, section):
    assert any(line.strip() == section for line in skill_text.splitlines()), (
        f"missing required section header: {section}"
    )


def test_operations_enumerates_arm_and_teardown(skill_text):
    body = _section_body(skill_text, "## Operations")
    assert "### Arm" in body
    assert "### Teardown" in body
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
def test_forbidden_token_absent(skill_text, forbidden):
    assert forbidden not in skill_text, (
        f"forbidden token reintroduced: {forbidden}"
    )


# ---------- F1: single-file inbox + wc -c byte-grow ----------

def test_f1_single_file_inbox_path_hardcoded(skill_text):
    monitor = _section_body(skill_text, "## Monitor Block")
    # Lead-only: inbox path is a fixed single JSON file.
    assert "inboxes/team-lead.json" in monitor
    # Byte-grow detection, NOT directory inotify.
    assert "wc -c" in monitor


def test_f1_single_file_inbox_phrase_in_overview(skill_text):
    # The narrative anchor must mark the inbox as a single JSON file (not
    # a directory). Two anchor sites: §Overview narrative and Monitor
    # Block discipline bullet.
    assert "single JSON file" in skill_text
    overview = _section_body(skill_text, "## Overview")
    assert "team-lead.json" in overview


# ---------- F6: TaskStop tolerance literal ----------

def test_f6_teardown_block_contains_ignoring_not_found(skill_text):
    teardown = _section_body(skill_text, "## Teardown Block")
    assert "ignoring not-found" in teardown


# ---------- F7: stdout discipline + between-tool-call scope ----------

def test_f7_between_tool_calls_not_mid_tool(skill_text):
    overview = _section_body(skill_text, "## Overview")
    # Both literal substrings of the alarm-clock paragraph must remain.
    assert "between tool calls" in overview
    assert "not mid-tool" in overview or "instant interrupt anywhere" in overview


def test_f7_stdout_discipline_in_monitor_block(skill_text):
    monitor = _section_body(skill_text, "## Monitor Block")
    # Stdout fires turns; only INBOX_GREW lines emit.
    assert "Stdout discipline" in monitor
    assert "INBOX_GREW" in monitor
    # Errors must go to stderr, which does not turn-fire.
    assert ">&2" in monitor


def test_long_single_tool_failure_mode_with_empirical_anchor(skill_text):
    fm = _section_body(skill_text, "## Failure Modes")
    assert "Long single-tool calls block wake delivery" in fm
    # Empirical timing anchor — without it the scope claim could be edited
    # away without leaving evidence in the doc.
    assert (
        "2026-04-30" in fm
        and "00:01:34" in fm
        and "00:02:23" in fm
    )


# ---------- Alarm-clock framing (audit-anchor invariant) ----------

def test_alarm_clock_paragraph_present(skill_text):
    overview = _section_body(skill_text, "## Overview")
    assert "Monitor is an alarm clock, not a mailbox" in overview


def test_alarm_clock_no_narration_clause(skill_text):
    # Empirically observed: the lead emits "(Alarm.)" / "(Idle ping.)" if
    # the skill body does not explicitly forbid acknowledgment text.
    overview = _section_body(skill_text, "## Overview")
    assert (
        "without emitting acknowledgment text" in overview
        or "no narration" in overview.lower()
        or "return to idle" in overview
    )


# ---------- 20/20/60 timing fences ----------

@pytest.mark.parametrize("token", [
    "POLL=20",
    "QUIET_REQUIRED=20",
    "MAX_DELAY=60",
])
def test_timing_constant_present(skill_text, token):
    monitor = _section_body(skill_text, "## Monitor Block")
    assert token in monitor


# ---------- State machine edge tokens ----------

@pytest.mark.parametrize("edge", ["FIRST_GROW", "LAST_GROW", "MAX_DELAY"])
def test_state_machine_edge_token_present(skill_text, edge):
    monitor = _section_body(skill_text, "## Monitor Block")
    assert edge in monitor


def test_state_machine_states_present(skill_text):
    monitor = _section_body(skill_text, "## Monitor Block")
    # 2-state machine per Wave 1 YELLOW#1: PENDING + GROWING. Token
    # presence only — no AST-level state-cardinality assertion.
    assert "PENDING" in monitor
    assert "GROWING" in monitor


# ---------- WriteStateFile schema (lead-only fence) ----------

def test_writestate_block_has_three_fields(skill_text):
    block = _section_body(skill_text, "## WriteStateFile Block")
    for field in ("v", "monitor_task_id", "armed_at"):
        assert f'"{field}"' in block, f"missing schema field: {field}"


def test_writestate_block_no_watchdog_tokens(skill_text):
    block = _section_body(skill_text, "## WriteStateFile Block")
    # YELLOW#3: only Cron Block / Recovery / {agent_name} /
    # _WAKE_ARM_TEMPLATE are forbidden. cron_job_id and heartbeat are
    # NOT plan-listed forbidden tokens; check for them defensively
    # because the audit prose mentions them by name.
    assert "cron_job_id" not in block
    assert "heartbeat" not in block


def test_writestate_path_is_lead_only_fixed(skill_text):
    block = _section_body(skill_text, "## WriteStateFile Block")
    assert "inbox-wake-state.json" in block
    # No per-agent suffix template under lead-only.
    assert "{agent-name}" not in block


# ---------- Teardown ordering load-bearing ----------

def test_teardown_block_orders_taskstop_before_unlink(skill_text):
    teardown = _section_body(skill_text, "## Teardown Block")
    stop_idx = teardown.find("TaskStop")
    unlink_idx = teardown.find("Unlink STATE_FILE")
    assert stop_idx >= 0 and unlink_idx >= 0
    assert stop_idx < unlink_idx, (
        "Teardown ordering inverted — TaskStop must precede unlink"
    )


def test_teardown_uses_missing_ok(skill_text):
    teardown = _section_body(skill_text, "## Teardown Block")
    assert "missing_ok=True" in teardown


# ---------- Lead-only narrowing ----------

def test_lead_only_scope_in_overview(skill_text):
    overview = _section_body(skill_text, "## Overview")
    # Single Monitor per session; no symmetric scope language.
    assert "Single-Monitor model" in overview
    assert "Lead-only" in skill_text or "lead-only" in skill_text.lower()


# ---------- Failure Modes coverage ----------

@pytest.mark.parametrize("entry", [
    "Malformed STATE_FILE",
    "Schema-version mismatch",
    "Silent Monitor death",
    "Long single-tool calls block wake delivery",
    "Wake-fire inflation under bursty traffic",
    "Concurrent re-arm",
])
def test_failure_modes_entry_present(skill_text, entry):
    fm = _section_body(skill_text, "## Failure Modes")
    assert entry in fm
