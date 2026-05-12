"""
Structural invariants for commands/start-pending-scan.md.

Reads the .md file as text and asserts presence/shape properties.
Each P0 invariant assertion is paired with prose explaining the
editing-LLM regression it prevents — pin the WHY so a future
"simplification" cannot quietly relax the contract.

Invariants verified here:
- CronCreate Call Shape: (cron + prompt + recurring + durable)
- CronList Suffix-Match Strictness: discipline
- Lead-Session Guard at Command Entry Lead-Session Guard refuse-and-return from non-lead session
- Cross-Skill Prompt-String Byte-Identity: /PACT:scan-pending-tasks (cross-file: also checked
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
        f"Cross-Skill Prompt-String Byte-Identity/3/4/11 cannot be verified without the source file."
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


# ---------- CronCreate Call Shape: CronCreate 4-field call shape ----------

def test_cron_create_call_shape_cron_create_block_has_four_fields(cmd_text):
    """CronCreate Call Shape / M1 (commit-9 tightened to §CronCreate Block scope): the
    CronCreate call in the §CronCreate Block exposes exactly 4 named
    fields (cron + prompt + recurring + durable) with VERBATIM Python-form
    values. Each field is load-bearing per the §CronCreate Block audit
    anchor — extra args or omitted fields produce a different cron
    behavior.

    Tightening (M1): previously the verbatim-value checks used `in cmd_text`
    (substring-anywhere-in-file). M1 scopes them to the §CronCreate Block
    Python-form code (the operational source-of-truth that the platform
    receives), NOT the §Operation inline markdown-prose summary which
    uses lowercase `true`/`false` to read naturally in prose. A hostile
    edit that breaks the actual Python-form CronCreate call but leaves
    audit prose intact would pass the prior check; the in-section scope
    check catches it.

    Note: the file may contain TWO `CronCreate(` occurrences — the
    markdown-prose inline example at §Operation (lowercase true/false)
    and the Python-form code block at §CronCreate Block (capitalized
    True/False). We target the §CronCreate Block section because that
    is the operational source-of-truth."""
    assert 'CronCreate(' in cmd_text, "Missing CronCreate( call"
    # Locate the §CronCreate Block section (the Python-form code block).
    section_start = cmd_text.find("\n## CronCreate Block")
    assert section_start >= 0, (
        "Missing §CronCreate Block section — expected the operational "
        "Python-form code block to be anchored under this section heading."
    )
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    # Extract the CronCreate( ... ) block WITHIN the §CronCreate Block section.
    block_start = section.find("CronCreate(")
    assert block_start >= 0, (
        "§CronCreate Block section missing CronCreate( call. The section "
        "must contain the operational Python-form code block."
    )
    block_end = section.find(")", block_start)
    assert block_end > block_start, "CronCreate( in §CronCreate Block has no closing )"
    block = section[block_start:block_end + 1]
    assert 'cron="*/2 * * * *"' in block, (
        "CronCreate Call Shape/M1: §CronCreate Block must use cron='*/2 * * * *' (2-minute "
        "cadence pinned in plan §Architecture). Tuning the cadence requires "
        "Communication Charter §Cron-Fire Mechanism prose update in lockstep. "
        f"§CronCreate Block contents: {block!r}"
    )
    assert 'prompt="/PACT:scan-pending-tasks"' in block, (
        "CronCreate Call Shape/M1: §CronCreate Block must use prompt='/PACT:scan-pending-tasks' "
        "verbatim. The string must be byte-identical with scan-pending-tasks.md "
        "frontmatter and stop-pending-scan.md filter target (Cross-Skill Prompt-String Byte-Identity cross-file pin). "
        f"§CronCreate Block contents: {block!r}"
    )
    assert 'recurring=True' in block, (
        "CronCreate Call Shape/M1: §CronCreate Block must set recurring=True (Python-form, "
        "capitalized). One-shot mode (recurring=False) would re-introduce the "
        "LLM-self-diagnosis failure mode the unconditional-emit discipline closes. "
        f"§CronCreate Block contents: {block!r}"
    )
    assert 'durable=False' in block, (
        "CronCreate Call Shape/M1: §CronCreate Block must set durable=False (Python-form, "
        "capitalized). durable=True would re-introduce cross-session contamination — "
        "stale crons from prior sessions firing against unrelated tasks in fresh sessions. "
        f"§CronCreate Block contents: {block!r}"
    )


def test_cron_create_call_shape_cron_create_block_has_no_extra_fields(cmd_text):
    """Hostile-coupling check (commit-9 tightened to §CronCreate Block
    scope): the §CronCreate Block code contains EXACTLY the 4 documented
    fields, no additional kwargs. Extra args (e.g., a hypothetical
    'tags' or 'priority') would couple the skill to a platform extension
    the contract does not endorse.

    Tightening: extract from §CronCreate Block section, not the first
    CronCreate( in the file (which is the §Operation inline summary
    using lowercase markdown-prose form)."""
    section_start = cmd_text.find("\n## CronCreate Block")
    assert section_start >= 0, "Missing §CronCreate Block section"
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    block_start = section.find("CronCreate(")
    block_end = section.find(")", block_start)
    assert block_start >= 0 and block_end > block_start
    block = section[block_start:block_end + 1]
    # Each canonical field appears exactly once in the call block.
    assert block.count("cron=") == 1
    assert block.count("prompt=") == 1
    assert block.count("recurring=") == 1
    assert block.count("durable=") == 1


# ---------- CronList Suffix-Match Strictness: CronList suffix-match filter discipline ----------

def test_cron_list_suffix_match_strictness_cronlist_uses_exact_equality_suffix_match(cmd_text):
    """CronList Suffix-Match Strictness: CronList output is filtered by exact-equality match on
    the suffix after the ': ' separator, NOT substring, NOT regex.
    Substring match would falsely match /PACT:scan-pending-tasks-debug
    or any future variant, causing silent idempotency failures."""
    # Section presence (already covered above) + exact-equality token
    # appears in the documented filter pattern.
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section = cmd_text[section_start:section_start + 3000]
    assert "exact-equality" in section.lower() or "== target_prompt" in section, (
        "CronList Suffix-Match Strictness: CronList §Filter Discipline must document exact-equality "
        "filter and show 'suffix == target_prompt' (or equivalent) in "
        "the code example. Substring or regex relaxation is forbidden."
    )
    # The target prompt itself must appear in the filter discipline section.
    assert "/PACT:scan-pending-tasks" in section
    # The colon-space separator must be documented (defends against
    # `split(":")` regression that breaks on cron expressions).
    assert '": "' in section


def test_cron_list_suffix_match_strictness_forbids_substring_and_regex_filter_in_audit(cmd_text):
    """CronList Suffix-Match Strictness audit anchor: the §CronList Filter Discipline audit prose
    explicitly forbids substring and regex relaxation. Removing the
    forbidding language relaxes the contract silently."""
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section = cmd_text[section_start:section_start + 3000]
    forbidding_tokens = ("substring", "regex")
    for token in forbidding_tokens:
        assert token in section.lower(), (
            f"CronList Suffix-Match Strictness audit anchor must explicitly mention '{token}' as "
            f"a forbidden relaxation. Removing the forbidding language "
            f"silently relaxes the exact-equality contract."
        )


# ---------- Cross-Skill Prompt-String Byte-Identity: byte-identical prompt cross-file ----------

def _extract_croncreate_prompt(cmd_text: str) -> str:
    """Extract the operational prompt-string literal from start-pending-scan.md's
    CronCreate( block. This is the source-of-truth: it's the string the
    platform actually receives when the skill executes.

    Returns the raw quoted form (e.g., '"/PACT:scan-pending-tasks"') so the
    cross-file byte-identity check compares the EXACT call-shape literal,
    not a substring within any prose mention of the slug.
    """
    import re
    block_start = cmd_text.find("CronCreate(")
    assert block_start >= 0, "Missing CronCreate( in start-pending-scan.md"
    block_end = cmd_text.find(")", block_start)
    block = cmd_text[block_start:block_end + 1]
    m = re.search(r'prompt=("[^"]+")', block)
    assert m is not None, (
        f"Cannot extract prompt=... from CronCreate block: {block!r}"
    )
    return m.group(1)


def test_cross_skill_prompt_string_byte_identity_prompt_string_byte_identical_with_scan_body(cmd_text):
    """Cross-Skill Prompt-String Byte-Identity cross-file byte-identity (commit-9 tightened): the prompt
    literal in start-pending-scan.md's CronCreate( call MUST appear
    byte-identical in scan-pending-tasks.md. Tightening over substring-
    presence: the literal form `"/PACT:scan-pending-tasks"` (quoted)
    must appear in both files, not just the unquoted slug. This
    discriminates the operational call-shape from prose-mentions
    of the slug in audit anchors."""
    operational_prompt = _extract_croncreate_prompt(cmd_text)
    # operational_prompt is the quoted form, e.g. '"/PACT:scan-pending-tasks"'
    # The unquoted slug (without quotes) is what appears in scan-pending-tasks.md
    # frontmatter and prose; the quoted form appears only in CronCreate-shaped
    # code blocks. Byte-identity contract: both files contain the SAME
    # unquoted slug literal, AND the quoted form appears in start-pending-scan.md
    # (verified by extracting it).
    unquoted_slug = operational_prompt.strip('"')
    scan_file = ROOT / "commands" / "scan-pending-tasks.md"
    scan_text = scan_file.read_text(encoding="utf-8")
    assert unquoted_slug in scan_text, (
        f"scan-pending-tasks.md missing the canonical slug {unquoted_slug!r} — "
        f"Cross-Skill Prompt-String Byte-Identity cross-file byte-identity broken at the source side. "
        f"start-pending-scan.md's CronCreate uses prompt={operational_prompt}; "
        f"scan-pending-tasks.md must contain the same slug literal."
    )


def test_cross_skill_prompt_string_byte_identity_prompt_string_byte_identical_with_stop_command(cmd_text):
    """Cross-Skill Prompt-String Byte-Identity cross-file byte-identity (commit-9 tightened, 3rd file):
    the operational prompt literal must also appear in stop-pending-scan.md
    (where it's the filter target for CronList lookup)."""
    operational_prompt = _extract_croncreate_prompt(cmd_text)
    unquoted_slug = operational_prompt.strip('"')
    stop_file = ROOT / "commands" / "stop-pending-scan.md"
    stop_text = stop_file.read_text(encoding="utf-8")
    assert unquoted_slug in stop_text, (
        f"stop-pending-scan.md missing the canonical slug {unquoted_slug!r} — "
        f"Cross-Skill Prompt-String Byte-Identity cross-file byte-identity broken at the teardown side; "
        f"teardown would silently fail to find the cron registered by "
        f"start-pending-scan.md's CronCreate(prompt={operational_prompt})."
    )


# ---------- Lead-Session Guard at Command Entry: Lead-Session Guard ----------

def test_lead_session_guard_at_command_entry_lead_session_guard_section_present(cmd_text):
    """Lead-Session Guard at Command Entry: refuse-and-return when invoked from non-lead session.
    Defense-in-depth Layer 1 — catches user-typed invocations from a
    teammate session that bypass Layer 0 (hook-level guard)."""
    assert "## Lead-Session Guard" in cmd_text


def test_lead_session_guard_at_command_entry_lead_session_guard_compares_session_id_to_leadSessionId(cmd_text):
    """Lead-Session Guard at Command Entry: the guard compares session_id to team_config.leadSessionId.
    Replicating the signal to a hypothetical agent_type field on
    session-context creates two-source-of-truth drift — forbidden."""
    section_start = cmd_text.find("\n## Lead-Session Guard")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "session_id" in section
    assert "leadSessionId" in section
    assert "team_config" in section or "team config" in section.lower()


def test_lead_session_guard_at_command_entry_lead_session_guard_refuses_and_returns(cmd_text):
    """Lead-Session Guard at Command Entry: the guard refuses (not just logs) when invocation comes
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
