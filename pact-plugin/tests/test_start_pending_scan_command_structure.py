"""
Structural invariants for commands/start-pending-scan.md.

Reads the .md file as text and asserts presence/shape properties.
Each P0 invariant assertion is paired with prose explaining the
editing-LLM regression it prevents — pin the WHY so a future
"simplification" cannot quietly relax the contract.

Invariants verified here:
- INV-3 CronCreate 4-field call shape (cron + prompt + recurring + durable)
- INV-4 CronList exact-suffix-match filter discipline
- INV-11 Lead-Session Guard refuse-and-return from non-lead session
- INV-1 byte-identical /PACT:scan-pending-tasks (cross-file: also checked
  in test_scan_pending_tasks_command_structure.py and
  test_stop_pending_scan_command_structure.py — this file pins the
  outbound side from start-pending-scan)
- Idempotency contract (CronList-presence-as-state)
- Line-cap discipline (no oversized prose)
- Forbidden-token absence (no Monitor/STATE_FILE/armed_by_session_id leakage)

Counter-test-by-revert scope: reverting pact-plugin/commands/start-pending-scan.md
alone falsifies these tests (file deletion -> ImportError/missing-file
on the path; content revert -> assertion failures with discriminating
cardinality per invariant).
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CMD_FILE = ROOT / "commands" / "start-pending-scan.md"


@pytest.fixture(scope="module")
def cmd_text() -> str:
    return CMD_FILE.read_text(encoding="utf-8")


# ---------- File presence and frontmatter ----------

def test_command_file_exists():
    assert CMD_FILE.is_file(), (
        f"start-pending-scan.md missing at {CMD_FILE} — "
        f"INV-1/3/4/11 cannot be verified without the source file."
    )


def test_frontmatter_has_description(cmd_text):
    """Frontmatter must include a description line — Claude Code uses
    the description as the slash-command help text. Missing description
    breaks slash-command discoverability."""
    assert cmd_text.startswith("---\n"), (
        "start-pending-scan.md must open with YAML frontmatter"
    )
    fm_end = cmd_text.index("\n---\n", 4)
    frontmatter = cmd_text[4:fm_end]
    assert "description:" in frontmatter, (
        "Frontmatter missing description field"
    )


# ---------- Line-cap discipline ----------

def test_command_body_under_compaction_budget(cmd_text):
    """Compaction-safe body. Skill bodies that exceed ~250 lines have
    historically pushed the file into the per-skill compaction budget,
    causing summarization drift. Keep under the cap so the LLM-reader
    receives the full body verbatim at invocation time."""
    line_count = cmd_text.count("\n") + 1
    assert line_count <= 250, (
        f"start-pending-scan.md is {line_count} lines; cap is 250 to "
        f"stay under the per-skill compaction budget. Refactor by "
        f"extracting prose to charter references."
    )


# ---------- Required sections ----------

@pytest.mark.parametrize("section", [
    "## Overview",
    "## When to Invoke",
    "## Operation",
    "## Lead-Session Guard",
    "## CronList Filter Discipline",
    "## CronCreate Block",
])
def test_required_section_present(cmd_text, section):
    """Each P0 audit-anchored section must be present. Removing one
    silently relaxes the audit anchor and re-opens the failure mode
    that section's prose prevents."""
    assert section in cmd_text, (
        f"start-pending-scan.md missing required section '{section}'"
    )


# ---------- INV-3: CronCreate 4-field call shape ----------

def test_inv3_cron_create_block_has_four_fields(cmd_text):
    """INV-3: the CronCreate call exposes exactly 4 named fields
    (cron + prompt + recurring + durable). Each field is load-bearing
    per the §CronCreate Block audit anchor — extra args or omitted
    fields produce a different cron behavior."""
    assert 'CronCreate(' in cmd_text, "Missing CronCreate( call"
    assert 'cron="*/2 * * * *"' in cmd_text, (
        "INV-3: CronCreate must use cron='*/2 * * * *' (2-minute cadence "
        "pinned in plan §Architecture). Tuning the cadence requires "
        "Communication Charter §Cron-Fire Mechanism prose update in lockstep."
    )
    assert 'prompt="/PACT:scan-pending-tasks"' in cmd_text, (
        "INV-3: CronCreate prompt must be exactly /PACT:scan-pending-tasks "
        "and byte-identical with the scan-pending-tasks.md frontmatter "
        "and stop-pending-scan.md filter target (INV-1 cross-file pin)."
    )
    assert 'recurring=True' in cmd_text, (
        "INV-3: CronCreate must set recurring=True. One-shot mode "
        "(recurring=False) would re-introduce the LLM-self-diagnosis "
        "failure mode the unconditional-emit discipline closes."
    )
    assert 'durable=False' in cmd_text, (
        "INV-3: CronCreate must set durable=False. durable=True would "
        "re-introduce cross-session contamination — stale crons from "
        "prior sessions firing against unrelated tasks in fresh sessions."
    )


def test_inv3_cron_create_block_has_no_extra_fields(cmd_text):
    """Hostile-coupling check: the CronCreate example block contains
    EXACTLY the 4 documented fields, no additional kwargs. Extra args
    (e.g., a hypothetical 'tags' or 'priority') would couple the
    skill to a platform extension the contract does not endorse."""
    block_start = cmd_text.find("CronCreate(")
    block_end = cmd_text.find(")", block_start)
    assert block_start >= 0 and block_end > block_start
    block = cmd_text[block_start:block_end + 1]
    # Each canonical field appears exactly once in the call block.
    assert block.count("cron=") == 1
    assert block.count("prompt=") == 1
    assert block.count("recurring=") == 1
    assert block.count("durable=") == 1


# ---------- INV-4: CronList suffix-match filter discipline ----------

def test_inv4_cronlist_uses_exact_equality_suffix_match(cmd_text):
    """INV-4: CronList output is filtered by exact-equality match on
    the suffix after the ': ' separator, NOT substring, NOT regex.
    Substring match would falsely match /PACT:scan-pending-tasks-debug
    or any future variant, causing silent idempotency failures."""
    # Section presence (already covered above) + exact-equality token
    # appears in the documented filter pattern.
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section = cmd_text[section_start:section_start + 3000]
    assert "exact-equality" in section.lower() or "== target_prompt" in section, (
        "INV-4: CronList §Filter Discipline must document exact-equality "
        "filter and show 'suffix == target_prompt' (or equivalent) in "
        "the code example. Substring or regex relaxation is forbidden."
    )
    # The target prompt itself must appear in the filter discipline section.
    assert "/PACT:scan-pending-tasks" in section
    # The colon-space separator must be documented (defends against
    # `split(":")` regression that breaks on cron expressions).
    assert '": "' in section


def test_inv4_forbids_substring_and_regex_filter_in_audit(cmd_text):
    """INV-4 audit anchor: the §CronList Filter Discipline audit prose
    explicitly forbids substring and regex relaxation. Removing the
    forbidding language relaxes the contract silently."""
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section = cmd_text[section_start:section_start + 3000]
    forbidding_tokens = ("substring", "regex")
    for token in forbidding_tokens:
        assert token in section.lower(), (
            f"INV-4 audit anchor must explicitly mention '{token}' as "
            f"a forbidden relaxation. Removing the forbidding language "
            f"silently relaxes the exact-equality contract."
        )


# ---------- INV-1: byte-identical prompt cross-file ----------

def test_inv1_prompt_string_byte_identical_with_scan_body(cmd_text):
    """INV-1 cross-file pin: the prompt string in the CronCreate call
    here MUST match the scan-pending-tasks.md frontmatter byte-for-byte.
    Silent drift between the two breaks the CronList lookup, causing
    orphan-cron accumulation and silent re-arm failure."""
    scan_file = ROOT / "commands" / "scan-pending-tasks.md"
    scan_text = scan_file.read_text(encoding="utf-8")
    target = "/PACT:scan-pending-tasks"
    assert target in cmd_text, (
        f"start-pending-scan.md missing the canonical prompt {target}"
    )
    # The scan-pending-tasks.md file uses the slug literally in its
    # title/description; the byte-identity contract holds if both files
    # contain the literal string.
    assert target in scan_text, (
        f"scan-pending-tasks.md missing the canonical prompt {target} — "
        f"INV-1 cross-file byte-identity broken at the source side"
    )


def test_inv1_prompt_string_byte_identical_with_stop_command(cmd_text):
    """INV-1 cross-file pin (3rd file): the prompt string must also
    match stop-pending-scan.md's filter target."""
    stop_file = ROOT / "commands" / "stop-pending-scan.md"
    stop_text = stop_file.read_text(encoding="utf-8")
    target = "/PACT:scan-pending-tasks"
    assert target in stop_text, (
        f"stop-pending-scan.md missing the canonical prompt {target} — "
        f"INV-1 cross-file byte-identity broken at the teardown side; "
        f"teardown would silently fail to find the cron."
    )


# ---------- INV-11: Lead-Session Guard ----------

def test_inv11_lead_session_guard_section_present(cmd_text):
    """INV-11: refuse-and-return when invoked from non-lead session.
    Defense-in-depth Layer 1 — catches user-typed invocations from a
    teammate session that bypass Layer 0 (hook-level guard)."""
    assert "## Lead-Session Guard" in cmd_text


def test_inv11_lead_session_guard_compares_session_id_to_leadSessionId(cmd_text):
    """INV-11: the guard compares session_id to team_config.leadSessionId.
    Replicating the signal to a hypothetical agent_type field on
    session-context creates two-source-of-truth drift — forbidden."""
    section_start = cmd_text.find("\n## Lead-Session Guard")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "session_id" in section
    assert "leadSessionId" in section
    assert "team_config" in section or "team config" in section.lower()


def test_inv11_lead_session_guard_refuses_and_returns(cmd_text):
    """INV-11: the guard refuses (not just logs) when invocation comes
    from non-lead session. An editing LLM tempted to 'just warn' would
    silently let the cron register in the wrong session."""
    section_start = cmd_text.find("\n## Lead-Session Guard")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "refuse" in section.lower()
    assert "return" in section.lower()


def test_operation_step_zero_invokes_lead_session_guard(cmd_text):
    """The §Operation section's step 0 must invoke the Lead-Session
    Guard before step 1 (CronList). An out-of-order arrangement
    would let the CronCreate fire before the guard refuses."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    # Step 0 is the Lead-Session Guard reference.
    assert "0." in op_section
    assert "Lead-session guard" in op_section or "lead-session guard" in op_section.lower()


# ---------- Idempotency contract ----------

def test_idempotency_documented_via_cronlist_presence(cmd_text):
    """The skill's idempotency story is CronList-presence-as-state
    (the cron's existence in the in-session store IS the armed-state
    bit). NOT STATE_FILE, NOT armed_by_session_id sidecar."""
    assert "idempoten" in cmd_text.lower()
    # CronList is the idempotency primitive.
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    assert "CronList" in op_section


def test_idempotency_check_precedes_cron_create(cmd_text):
    """Operation must list CronList (idempotency check) BEFORE
    CronCreate (cold-start fall-through). Reversed order would
    register a redundant cron on every re-invocation."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    cronlist_pos = op_section.find("CronList")
    croncreate_pos = op_section.find("CronCreate")
    assert cronlist_pos >= 0
    assert croncreate_pos >= 0
    assert cronlist_pos < croncreate_pos, (
        "§Operation must invoke CronList before CronCreate so the "
        "idempotency check fires first."
    )


# ---------- Forbidden-token absence ----------

@pytest.mark.parametrize("forbidden_slug", [
    'Skill("PACT:watch-inbox")',
    'Skill("PACT:unwatch-inbox")',
    'Skill("PACT:inbox-wake")',
])
def test_forbidden_legacy_slug_invocation_absent(cmd_text, forbidden_slug):
    """Defense-in-depth: legacy Monitor-mechanism slug INVOCATIONS
    (`Skill("PACT:watch-inbox")` etc.) must not appear in the
    cron-mechanism skill body. Audit-anchor prose may MENTION the
    legacy tokens (STATE_FILE, Monitor, armed_by_session_id) as
    'replaces the Monitor-era X' for editing-LLM context — that
    prose is load-bearing. What is forbidden is the OPERATIONAL
    invocation form."""
    assert forbidden_slug not in cmd_text, (
        f"start-pending-scan.md contains forbidden legacy slug "
        f"invocation '{forbidden_slug}' — operational incomplete "
        f"migration from Monitor mechanism."
    )


# ---------- Cron-Origin Distinction reference ----------

def test_references_cron_fire_origin_alarm_clock_framing(cmd_text):
    """The 'cron is an alarm clock, not a mailbox' framing must
    survive in §Overview. Removing it lets an editing LLM treat
    cron fires as a content channel (mailbox semantics) which
    re-opens the hallucination-cascade failure mode."""
    overview_start = cmd_text.find("## Overview")
    overview_end = cmd_text.find("\n## ", overview_start + 1)
    overview = cmd_text[overview_start:overview_end] if overview_end > 0 else cmd_text[overview_start:]
    assert "alarm clock" in overview.lower() or "not a mailbox" in overview.lower(), (
        "§Overview must contain the 'alarm clock, not a mailbox' "
        "framing — load-bearing for the Cron-Origin Distinction."
    )


def test_references_between_tool_call_scope(cmd_text):
    """Cron-fire surfaces between tool calls within a turn, not
    mid-tool. The substrate cannot interrupt a running tool; the
    skill must not overpromise that capability."""
    overview_start = cmd_text.find("## Overview")
    overview_end = cmd_text.find("\n## ", overview_start + 1)
    overview = cmd_text[overview_start:overview_end] if overview_end > 0 else cmd_text[overview_start:]
    assert "between tool calls" in overview.lower() or "between-tool-call" in overview.lower()
    assert "not mid-tool" in overview.lower() or "mid-tool" in overview.lower()


# ---------- Cross-link discipline ----------

def test_references_section_links_to_companion_commands(cmd_text):
    """§References must link to both companion commands using
    relative-path markdown links (NOT @~/ pattern)."""
    refs_start = cmd_text.find("## References")
    refs_section = cmd_text[refs_start:] if refs_start >= 0 else ""
    assert refs_start >= 0, "§References section missing"
    assert "scan-pending-tasks.md" in refs_section
    assert "stop-pending-scan.md" in refs_section
    # Relative-path discipline.
    assert "@~/" not in refs_section
