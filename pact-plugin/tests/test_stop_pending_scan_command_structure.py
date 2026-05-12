"""
Structural invariants for commands/stop-pending-scan.md.

Reads the .md file as text and asserts presence/shape properties.
Each P0 invariant assertion is paired with prose explaining the
editing-LLM regression it prevents.

Invariants verified here:
- INV-4 CronList exact-suffix-match filter discipline (teardown side)
- INV-5 CronDelete-by-extracted-8-char-ID extraction
- INV-11 Lead-Session Guard refuse-and-return from non-lead session
- Ignore-if-absent behavior (best-effort teardown)
- INV-1 byte-identical /PACT:scan-pending-tasks (cross-file pin —
  this file asserts the teardown-side filter target)
- Line-cap discipline
- Forbidden-token absence (no STATE_FILE / Monitor / armed_by_session_id)

Counter-test-by-revert scope: reverting pact-plugin/commands/stop-pending-scan.md
alone falsifies these tests.
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CMD_FILE = ROOT / "commands" / "stop-pending-scan.md"


@pytest.fixture(scope="module")
def cmd_text() -> str:
    return CMD_FILE.read_text(encoding="utf-8")


# ---------- File presence and frontmatter ----------

def test_command_file_exists():
    assert CMD_FILE.is_file(), (
        f"stop-pending-scan.md missing at {CMD_FILE} — INV-4/5/11 "
        f"cannot be verified without the source file."
    )


def test_frontmatter_has_description(cmd_text):
    """Frontmatter must include a description line — slash-command
    discoverability."""
    assert cmd_text.startswith("---\n")
    fm_end = cmd_text.index("\n---\n", 4)
    frontmatter = cmd_text[4:fm_end]
    assert "description:" in frontmatter


# ---------- Line-cap discipline ----------

def test_command_body_under_compaction_budget(cmd_text):
    """Compaction-safe body. Stop-pending-scan is a small command —
    keep under 250 lines so it stays within the per-skill compaction
    budget."""
    line_count = cmd_text.count("\n") + 1
    assert line_count <= 250, (
        f"stop-pending-scan.md is {line_count} lines; cap is 250 to "
        f"stay under the per-skill compaction budget."
    )


# ---------- Required sections ----------

@pytest.mark.parametrize("section", [
    "## Overview",
    "## When to Invoke",
    "## Operation",
    "## Lead-Session Guard",
    "## CronList Filter Discipline",
    "## ID Extraction Block",
    "## Failure Modes",
])
def test_required_section_present(cmd_text, section):
    """Each P0 audit-anchored section must be present."""
    assert section in cmd_text, (
        f"stop-pending-scan.md missing required section '{section}'"
    )


# ---------- INV-4: CronList suffix-match filter discipline (teardown side) ----------

def test_inv4_cronlist_uses_exact_equality_suffix_match(cmd_text):
    """INV-4 (teardown side): the lookup uses exact-equality match
    on the suffix after ': '. Substring/regex match opens false-
    positive deletion vectors (could delete /PACT:scan-pending-tasks-debug
    or similar)."""
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "exact-equality" in section.lower() or "== target_prompt" in section
    assert "/PACT:scan-pending-tasks" in section
    assert '": "' in section


def test_inv4_forbids_substring_and_regex_filter_in_audit(cmd_text):
    """INV-4 audit anchor must explicitly forbid substring and regex
    relaxation — removing the forbidding language silently relaxes
    the exact-equality contract."""
    section_start = cmd_text.find("\n## CronList Filter Discipline")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    for token in ("substring", "regex"):
        assert token in section.lower(), (
            f"INV-4 audit must explicitly mention '{token}' as a "
            f"forbidden relaxation on the teardown side."
        )


# ---------- INV-5: CronDelete-by-extracted-ID ----------

def test_inv5_id_extraction_block_present(cmd_text):
    """INV-5: cron IDs are platform-assigned (8-char hex), not
    caller-specifiable. The §ID Extraction Block documents the
    canonical extraction pattern."""
    section_start = cmd_text.find("\n## ID Extraction Block")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "CronDelete" in section, "§ID Extraction Block must show CronDelete call"
    # The 8-character extraction shape — either documented as 8-char
    # or via the first-token split pattern.
    assert "8" in section or "8-character" in section or "split(\" \", 1)" in section


def test_inv5_uses_whitespace_token_split_not_fixed_width(cmd_text):
    """INV-5: extraction via `split(" ", 1)[0]` (first whitespace-
    delimited token), NOT a fixed-width slice `match_line[:8]`. The
    whitespace-tokenization form survives format evolution; the
    fixed-width form silently breaks if platform extends ID length."""
    section_start = cmd_text.find("\n## ID Extraction Block")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert 'split(" "' in section or 'split(" ", 1)' in section, (
        "INV-5: ID extraction must use whitespace-token split, NOT a "
        "fixed-width slice. Fixed-width breaks silently on ID format change."
    )


def test_inv5_cron_delete_call_present_in_operation(cmd_text):
    """The §Operation section's step 4 must invoke CronDelete with
    the extracted ID."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    assert "CronDelete" in op_section


# ---------- INV-1: byte-identical prompt cross-file ----------

def test_inv1_filter_target_byte_identical_with_companion_commands(cmd_text):
    """INV-1 cross-file pin (teardown side): the filter target
    /PACT:scan-pending-tasks must be byte-identical with the
    start-pending-scan.md CronCreate prompt and the scan-pending-tasks.md
    frontmatter. Drift here causes teardown to silently fail to find
    the cron."""
    target = "/PACT:scan-pending-tasks"
    assert target in cmd_text, (
        f"stop-pending-scan.md missing canonical filter target {target}"
    )
    # Verify the companions also carry the same string (cross-file
    # contract).
    start_text = (ROOT / "commands" / "start-pending-scan.md").read_text(encoding="utf-8")
    scan_text = (ROOT / "commands" / "scan-pending-tasks.md").read_text(encoding="utf-8")
    assert target in start_text, "start-pending-scan.md drift"
    assert target in scan_text, "scan-pending-tasks.md drift"


# ---------- INV-11: Lead-Session Guard ----------

def test_inv11_lead_session_guard_section_present(cmd_text):
    """INV-11: refuse-and-return when invoked from non-lead session.
    Even though durable=false cron is session-scoped at the platform
    layer (Layer 2 invariant), the §Lead-Session Guard is Layer 1
    foot-gun protection against user-typed misinvocation."""
    assert "## Lead-Session Guard" in cmd_text


def test_inv11_lead_session_guard_compares_session_id_to_leadSessionId(cmd_text):
    """INV-11: the guard compares session_id to team_config.leadSessionId.
    No agent_type field on session-context — single source of truth
    is team_config.json."""
    section_start = cmd_text.find("\n## Lead-Session Guard")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "session_id" in section
    assert "leadSessionId" in section
    assert "team_config" in section or "team config" in section.lower()


def test_inv11_lead_session_guard_refuses_and_returns(cmd_text):
    """INV-11: guard refuses (not just warns) when invocation comes
    from non-lead session."""
    section_start = cmd_text.find("\n## Lead-Session Guard")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "refuse" in section.lower()
    assert "return" in section.lower()


def test_operation_step_zero_invokes_lead_session_guard(cmd_text):
    """The §Operation section's step 0 must invoke the Lead-Session
    Guard before step 1 (CronList)."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    assert "0." in op_section
    assert "Lead-session guard" in op_section or "lead-session guard" in op_section.lower()


# ---------- Ignore-if-absent behavior ----------

def test_ignore_if_absent_documented_in_failure_modes(cmd_text):
    """Teardown is best-effort. If CronList returns no match, the
    teardown is a no-op success — the cron was either never armed
    or already torn down. The §Failure Modes section documents this."""
    fm_start = cmd_text.find("## Failure Modes")
    fm_end = cmd_text.find("\n## ", fm_start + 1)
    fm_section = cmd_text[fm_start:fm_end] if fm_end > 0 else cmd_text[fm_start:]
    assert "absent" in fm_section.lower() or "not found" in fm_section.lower()
    assert "no-op" in fm_section.lower() or "success" in fm_section.lower()


def test_operation_handles_no_match_as_no_op_success(cmd_text):
    """§Operation step 3 must say no-op success when CronList returns
    no match. Reversing this to a hard-error would block sessions that
    didn't arm (e.g., wrap-up safety-net invocation when count was
    already 0 throughout the session)."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    assert "no-op" in op_section.lower()


# ---------- Forbidden-token absence ----------

@pytest.mark.parametrize("forbidden_slug", [
    'Skill("PACT:watch-inbox")',
    'Skill("PACT:unwatch-inbox")',
    'Skill("PACT:inbox-wake")',
    "TaskStop",
])
def test_forbidden_legacy_slug_invocation_absent(cmd_text, forbidden_slug):
    """Defense-in-depth: legacy Monitor-mechanism slug INVOCATIONS
    must not appear in the cron-mechanism teardown skill body. Audit
    prose may mention legacy tokens (STATE_FILE etc.) as 'replaces
    the Monitor-era X' for editing-LLM context — load-bearing prose.
    What is forbidden is the OPERATIONAL invocation form. TaskStop is
    also forbidden — the cron-mechanism teardown uses CronDelete,
    not TaskStop (the Monitor was a Task; the cron is not)."""
    assert forbidden_slug not in cmd_text, (
        f"stop-pending-scan.md contains forbidden legacy slug "
        f"invocation '{forbidden_slug}' — operational incomplete "
        f"migration from Monitor mechanism."
    )


# ---------- Cross-link discipline ----------

def test_references_section_links_to_companion_commands(cmd_text):
    """§References must link to both companion commands."""
    refs_start = cmd_text.find("## References")
    refs_section = cmd_text[refs_start:] if refs_start >= 0 else ""
    assert refs_start >= 0
    assert "start-pending-scan.md" in refs_section
    assert "scan-pending-tasks.md" in refs_section
    assert "@~/" not in refs_section
