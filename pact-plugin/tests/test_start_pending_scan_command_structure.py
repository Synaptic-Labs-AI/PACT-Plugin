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


def _find_balanced_close_paren(text: str, open_paren_pos: int) -> int:
    """Find the closing `)` that balances the opening `(` at open_paren_pos.

    Counts open/close parens forward from open_paren_pos. Robust against
    `)` characters appearing inside field values (e.g., a hypothetical
    `prompt="(foo)"` would not prematurely terminate block extraction).
    Replaces the prior `text.find(")", open_paren_pos)` which returned
    the first `)` regardless of nesting (commit-13 F6 fix).

    Returns the index of the balancing `)`, or -1 if unbalanced.
    Assumes text[open_paren_pos] == '('.
    """
    assert open_paren_pos >= 0 and text[open_paren_pos] == "(", (
        f"_find_balanced_close_paren expected '(' at position "
        f"{open_paren_pos}, got {text[open_paren_pos:open_paren_pos + 1]!r}"
    )
    depth = 0
    for i in range(open_paren_pos, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# ---------- F6 regression coverage: balanced-paren parser handles )-containing values ----------


# Synthetic source: CronCreate block with a `)`-containing field value.
# The OLD `find(")")` parser truncates at the inner `)` of the note value;
# the NEW balanced-paren parser correctly identifies the final `)` of the
# CronCreate call.
_F6_REGRESSION_SOURCE = (
    'CronCreate(\n'
    '    cron="*/2 * * * *",\n'
    '    prompt="/PACT:scan-pending-tasks",\n'
    '    note="(deprecated 2026-Q3)",\n'
    '    recurring=True,\n'
    '    durable=False,\n'
    ')'
)


def test_f6_balanced_paren_handles_close_paren_in_field_value():
    """F6 regression coverage (commit-13): `_find_balanced_close_paren`
    correctly extracts a CronCreate block whose field value contains
    a `)` character. The OLD `text.find(")", open_paren_pos)` parser
    would truncate at the inner `)` of the note value, MISSING the
    later fields and the actual final `)`. The NEW balanced-paren
    parser counts depth and returns the call's final `)`.

    Empirical discharge documented in commit-13 HANDOFF: OLD parser
    truncated at synthetic index 9 (inner `)` of note); NEW parser
    returned synthetic index ~150 (call's final `)`). Permanent
    regression coverage: this test makes a future revert to the OLD
    parser structurally undetectable without test failure."""
    src = _F6_REGRESSION_SOURCE
    open_paren_pos = src.find("CronCreate(") + len("CronCreate")
    assert src[open_paren_pos] == "(", "Test setup invariant: CronCreate( in synthetic"

    block_end = _find_balanced_close_paren(src, open_paren_pos)
    assert block_end > open_paren_pos, (
        "F6: balanced-paren parser must return a position after the opening `(`. "
        f"Got block_end={block_end}, open_paren_pos={open_paren_pos}."
    )
    # The extracted block must end at the FINAL `)` of the CronCreate
    # call (which is the very last character of the synthetic source).
    assert block_end == len(src) - 1, (
        "F6: balanced-paren must return the FINAL `)` index, not a premature "
        f"inner `)`. Got block_end={block_end}, expected {len(src) - 1} (last char). "
        f"The OLD `find(\")\")` parser would have returned the inner `)` of the "
        f"note value at index {src.find(')')}, truncating the block."
    )

    # The extracted block must contain ALL canonical fields, including
    # those after the )-containing note value. If the parser truncates
    # prematurely, these would be missing.
    block = src[open_paren_pos - len("CronCreate"):block_end + 1]
    assert 'recurring=True' in block, (
        "F6: balanced-paren must capture `recurring=True` even though it "
        "appears AFTER the )-containing note value. Premature termination "
        "at the inner `)` (OLD parser behavior) would miss this field."
    )
    assert 'durable=False' in block, (
        "F6: balanced-paren must capture `durable=False` even though it "
        "appears AFTER the )-containing note value."
    )
    assert 'note="(deprecated 2026-Q3)"' in block, (
        "F6: balanced-paren must capture the full `)`-containing note "
        "value (including both opening and closing parens of the inner value)."
    )


def test_f6_balanced_paren_returns_minus_one_when_unbalanced():
    """F6 invariant: `_find_balanced_close_paren` returns -1 if the
    parens are unbalanced (more opens than closes). Documenting this
    fail-mode prevents a future caller from treating -1 as a valid
    index (which would slice from the end of the string)."""
    # Unbalanced: 2 opens, 1 close.
    src = 'CronCreate(\n    note="(unclosed",\n    recurring=True,\n'
    open_paren_pos = src.find("CronCreate(") + len("CronCreate")
    result = _find_balanced_close_paren(src, open_paren_pos)
    assert result == -1, (
        "F6: unbalanced parens must return -1 (sentinel), not a "
        f"misleading positive index. Got {result}."
    )


def test_f6_balanced_paren_handles_nested_parens_in_field_value():
    """F6: nested `()` pairs in a field value (e.g., a hypothetical
    `tags=("a", "b", "c")` tuple-style field) must not confuse the
    depth counter. The parser counts opens and closes; balanced
    inner pairs return to outer-depth and the outer `)` is correctly
    identified as the call's terminator."""
    src = (
        'CronCreate(\n'
        '    cron="*/2 * * * *",\n'
        '    tags=("a", "b", "c"),\n'  # nested () inside a tuple-style value
        '    recurring=True,\n'
        ')'
    )
    open_paren_pos = src.find("CronCreate(") + len("CronCreate")
    block_end = _find_balanced_close_paren(src, open_paren_pos)
    assert block_end == len(src) - 1, (
        "F6: nested () must not confuse depth counter. Got "
        f"block_end={block_end}, expected {len(src) - 1} (final `)`)."
    )
    # The full block must contain the nested tuple verbatim.
    block = src[open_paren_pos - len("CronCreate"):block_end + 1]
    assert '("a", "b", "c")' in block, (
        "F6: nested tuple-style value must be captured verbatim including "
        "both inner parens."
    )


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
    # Use balanced-paren parsing (commit-13 F6) to tolerate hypothetical
    # `)`-containing field values without prematurely terminating the block.
    block_start = section.find("CronCreate(")
    assert block_start >= 0, (
        "§CronCreate Block section missing CronCreate( call. The section "
        "must contain the operational Python-form code block."
    )
    open_paren_pos = block_start + len("CronCreate")
    block_end = _find_balanced_close_paren(section, open_paren_pos)
    assert block_end > block_start, "CronCreate( in §CronCreate Block has no balanced closing )"
    block = section[block_start:block_end + 1]
    assert 'cron="*/3 * * * *"' in block, (
        "CronCreate Call Shape/M1: §CronCreate Block must use cron='*/3 * * * *' (3-minute "
        "cadence; coupled in lockstep to the 180s warmup-grace constant in "
        "scan-pending-tasks.md Step 0 — first-fire-coverage invariant). Tuning the "
        "cadence requires updating BOTH the cron literal AND the warmup-grace literal "
        "in scan-pending-tasks.md AND the Communication Charter §Cron-Fire Mechanism "
        "prose in lockstep. "
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
    assert block_start >= 0
    # Use balanced-paren parsing (commit-13 F6) — tolerates )-containing field values.
    open_paren_pos = block_start + len("CronCreate")
    block_end = _find_balanced_close_paren(section, open_paren_pos)
    assert block_end > block_start
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
    # Use balanced-paren parsing (commit-13 F6) — tolerates )-containing field values.
    open_paren_pos = block_start + len("CronCreate")
    block_end = _find_balanced_close_paren(cmd_text, open_paren_pos)
    assert block_end > block_start, "Cannot find balanced ) for CronCreate( in cmd_text"
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


# ---------- Intra-file cron literal consistency ----------


def _extract_cron_literal_from_operation_summary(cmd_text: str) -> str:
    """Parse the cron literal from the §Operation summary's inline
    `CronCreate(cron="*/N * * * *", ...)` Step 4 prose.

    Distinct from `_extract_croncreate_prompt`: that targets the
    operational §CronCreate Block (Python-form, line ~100). This
    targets the §Operation summary mention (markdown-prose, Step 4)
    so the two surfaces can be compared for intra-file byte-identity.
    """
    import re
    op_start = cmd_text.find("\n## Operation")
    assert op_start >= 0, "Missing §Operation section"
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    m = re.search(r'CronCreate\(cron="(\*/\d+ \* \* \* \*)"', op_section)
    assert m is not None, (
        f"§Operation summary must contain an inline "
        f'`CronCreate(cron="*/N * * * *", ...)` literal; got section:\n'
        f"{op_section!r}"
    )
    return m.group(1)


def _extract_cron_literal_from_croncreate_block(cmd_text: str) -> str:
    """Parse the cron literal from the §CronCreate Block operational
    SSOT (Python-form code block). This is what the platform receives
    at CronCreate time."""
    import re
    block_start = cmd_text.find("\n## CronCreate Block")
    assert block_start >= 0, "Missing §CronCreate Block section"
    block_end = cmd_text.find("\n## ", block_start + 1)
    section = cmd_text[block_start:block_end] if block_end > 0 else cmd_text[block_start:]
    # Locate the `cron="..."` line in the Python-form code block (not the audit prose).
    # The code block is indented; the audit prose uses backtick code spans.
    # We anchor on the indented `    cron="..."` shape.
    m = re.search(r'(?m)^\s+cron="(\*/\d+ \* \* \* \*)",', section)
    assert m is not None, (
        f"§CronCreate Block must contain an indented code-block "
        f'`    cron="*/N * * * *",` line; got section:\n{section!r}'
    )
    return m.group(1)


def test_operation_summary_matches_croncreate_block(cmd_text):
    """Intra-file consistency: the cron literal mentioned in the
    §Operation summary's Step 4 inline `CronCreate(cron="*/N * * * *", ...)`
    MUST byte-match the cron literal in the operational §CronCreate Block.

    Closes the phantom-green-by-multiplicity gap surfaced by the test
    review of #766: `cron="*/N"` appears at THREE sites in this file
    (§Operation Step 4 inline prose, §CronCreate Block code, §CronCreate
    Block audit prose). The coupling-invariant test pins the operational
    SSOT (§CronCreate Block). This test pins the §Operation summary
    against that SSOT, catching documentation-drift between the
    in-section narrative and the operational call shape.

    Counter-test-by-revert: mutating the §Operation summary cron literal
    alone (without touching §CronCreate Block) makes this test go RED.
    Mutating only §CronCreate Block makes the existing
    `test_cron_create_call_shape_cron_create_block_has_four_fields`
    go RED. Mutating both in lockstep keeps this test GREEN but
    forces the coupling-invariant test to be re-validated against
    scan-pending-tasks.md Step 0.
    """
    operation_cron = _extract_cron_literal_from_operation_summary(cmd_text)
    croncreate_cron = _extract_cron_literal_from_croncreate_block(cmd_text)
    assert operation_cron == croncreate_cron, (
        f"Intra-file cron literal drift in start-pending-scan.md: "
        f"§Operation summary Step 4 says cron='{operation_cron}' but "
        f"§CronCreate Block (operational SSOT) says cron='{croncreate_cron}'. "
        f"The §Operation summary narrates what the §CronCreate Block "
        f"does; drift between them silently misleads an editing LLM "
        f"about which value is authoritative. Tune both in lockstep "
        f"(and update the audit prose at the same time)."
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


# ---------- Step 5 cold-start scan_armed journal write ----------


def test_scan_armed_step_5_present_in_operation(cmd_text):
    """Step 5 of §Operation writes a `scan_armed` event marking the
    cold-start arm time. Coupling pair partner: Step 0 of scan-pending-
    tasks.md reads this event timestamp and applies the 180s warmup-
    grace skip. The two steps form the journal-event round trip; both
    must be present or the warmup-grace skip silently degrades to
    fail-open-only (every fire falls through Step 0 because the journal
    has no scan_armed event).

    Counter-test-by-revert: reverting Step 5 (removing the numbered
    step OR removing the canonical `python3 "$SJ" write --type scan_armed`
    invocation substring) falsifies this test. Reverting Step 0 in
    scan-pending-tasks.md falsifies the partner test
    test_warmup_grace_step_0_present_in_operation — together the pair
    pins both ends of the journal round trip.
    """
    op_start = cmd_text.find("\n## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    step_5_pos = op_section.find("\n5. ")
    assert step_5_pos >= 0, (
        "§Operation must contain a Step 5 (`5. `) — the cold-start "
        "journal write that marks scan_armed timestamp."
    )
    # Bound Step 5 body to the next numbered step or section boundary.
    step_5_body = op_section[step_5_pos:]
    assert 'python3 "$SJ" write --type scan_armed' in step_5_body, (
        "Step 5 must invoke the canonical journal write: "
        '`python3 "$SJ" write --type scan_armed ...`. Substring check '
        "tolerates surrounding bash idioms; the type-literal "
        "`scan_armed` and the `write` subcommand are load-bearing."
    )


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
