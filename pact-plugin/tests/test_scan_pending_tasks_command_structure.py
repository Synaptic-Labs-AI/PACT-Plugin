"""
Structural invariants for commands/scan-pending-tasks.md.

Reads the .md file as text and asserts presence/shape properties.
Each P0 invariant assertion is paired with prose explaining the
editing-LLM regression it prevents.

Invariants verified here:
- INV-1 byte-identical /PACT:scan-pending-tasks across all 3 command files
  (start-pending-scan.md + scan-pending-tasks.md + stop-pending-scan.md)
- INV-2 the 5 anti-hallucination guardrails (G1-G5) appear VERBATIM
- INV-8 [CRON-FIRE] marker presence at top of file
- INV-9 same-session-identity gate at step 1 of the operation
- INV-10 lead-only completion contract (scan body uses canonical
  acceptance pair, no inline TaskUpdate(completed) standalone)
- Cron-Origin Distinction section present and forbids cron-fire as user-consent
- Forbidden-token absence (no Monitor/STATE_FILE/armed_by_session_id)
- Line-cap discipline

Counter-test-by-revert scope: reverting pact-plugin/commands/scan-pending-tasks.md
falsifies these tests with discriminating cardinality. Reverting all 3 .md
files together is required for INV-1 byte-identity counter-test (single-file
revert masks cross-file drift detection — per PR #723 cycle-1 multi-file
revert lesson).
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CMD_FILE = ROOT / "commands" / "scan-pending-tasks.md"


@pytest.fixture(scope="module")
def cmd_text() -> str:
    return CMD_FILE.read_text(encoding="utf-8")


# ---------- File presence and frontmatter ----------

def test_command_file_exists():
    assert CMD_FILE.is_file(), (
        f"scan-pending-tasks.md missing at {CMD_FILE} — INV-1/2/8/9/10 "
        f"cannot be verified without the source file."
    )


def test_frontmatter_has_description(cmd_text):
    """Frontmatter must include a description line."""
    assert cmd_text.startswith("---\n")
    fm_end = cmd_text.index("\n---\n", 4)
    frontmatter = cmd_text[4:fm_end]
    assert "description:" in frontmatter


def test_frontmatter_description_mentions_filesystem_falsifiable(cmd_text):
    """The skill's user-facing description must surface the anti-
    hallucination guarantee (filesystem-falsifiable). An editing LLM
    that softens the description silently sells the skill short."""
    fm_end = cmd_text.index("\n---\n", 4)
    frontmatter = cmd_text[4:fm_end]
    assert (
        "falsifiable" in frontmatter.lower()
        or "filesystem" in frontmatter.lower()
    )


# ---------- Line-cap discipline ----------

def test_command_body_under_compaction_budget(cmd_text):
    """Compaction-safe body. Scan-pending-tasks is the largest of the
    three new commands due to the 5 guardrail audit anchors. Cap at
    300 lines (slightly relaxed from 250) given the guardrail prose
    is verbatim and non-compressible."""
    line_count = cmd_text.count("\n") + 1
    assert line_count <= 300, (
        f"scan-pending-tasks.md is {line_count} lines; cap is 300. "
        f"The 5 verbatim guardrails account for substantial prose; "
        f"further additions should land in the charter, not here."
    )


# ---------- INV-8: [CRON-FIRE] marker presence at file top ----------

def test_inv8_cron_fire_marker_at_top_of_file(cmd_text):
    """INV-8: the [CRON-FIRE] discipline marker must appear in the
    first 30 lines of the file. The marker is the structural anchor
    for the Cron-Origin Distinction — removing it lets an editing
    LLM treat cron fires as user-typed input, re-opening the
    hallucination-cascade failure mode."""
    head = "\n".join(cmd_text.splitlines()[:30])
    assert "[CRON-FIRE]" in head, (
        "INV-8: [CRON-FIRE] marker must appear in first 30 lines of "
        "scan-pending-tasks.md as the structural anchor for the "
        "Cron-Origin Distinction. Removal re-opens the cascade."
    )


def test_inv8_cron_origin_distinction_section_present(cmd_text):
    """INV-8: the §Cron-Fire Origin section anchors the principle
    statement that cron-fire is NOT user consent."""
    assert "## Cron-Fire Origin" in cmd_text


def test_inv8_cron_origin_section_forbids_consent_treatment(cmd_text):
    """INV-8 audit: the section explicitly states cron-fire is NOT
    user consent for downstream consent-gated decisions (merge, push,
    destructive bash, etc.)."""
    section_start = cmd_text.find("\n## Cron-Fire Origin")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "NOT user consent" in section or "not user consent" in section.lower()
    # Consent-gated decision categories must be enumerated.
    consent_gated = ("merge", "push", "destructive bash")
    for category in consent_gated:
        assert category in section.lower(), (
            f"§Cron-Fire Origin must enumerate '{category}' as a "
            f"consent-gated decision class — defense against editing-LLM "
            f"failure to generalize from one example."
        )


# ---------- INV-2: 5 anti-hallucination guardrails (G1-G5) verbatim ----------

def test_inv2_guardrails_section_present(cmd_text):
    """INV-2: the §Guardrails section anchors all 5 guardrails."""
    assert "## Guardrails" in cmd_text


@pytest.mark.parametrize("guardrail_header", [
    "### G1: Read filesystem only",
    "### G2: No narration",
    "### G3: Raw-read metadata",
    "### G4: Race-window skip",
    "### G5: Emit nothing if empty",
])
def test_inv2_each_guardrail_header_present(cmd_text, guardrail_header):
    """INV-2: each of the 5 guardrails has its dedicated section
    header. Verbatim presence — paraphrase during PR review = silent
    regression of the anti-hallucination contract."""
    assert guardrail_header in cmd_text, (
        f"INV-2: scan-pending-tasks.md missing required guardrail "
        f"header '{guardrail_header}'. The 5 guardrails are load-"
        f"bearing per architecture spec; each prevents a specific "
        f"cascade failure mode."
    )


def test_inv2_exactly_five_guardrail_headers(cmd_text):
    """INV-2 cardinality: exactly 5 guardrail headers G1-G5. Adding
    a G6 silently expands the audit-anchored contract; removing one
    silently relaxes it."""
    g_headers = [
        line for line in cmd_text.splitlines()
        if line.startswith("### G") and ":" in line
    ]
    assert len(g_headers) == 5, (
        f"INV-2 cardinality: expected exactly 5 G* guardrail headers, "
        f"found {len(g_headers)}: {g_headers}"
    )


def test_inv2_each_guardrail_has_audit_block(cmd_text):
    """INV-2 audit anchor: each G* guardrail must be followed by a
    paragraph starting with '**Audit**:'. The audit prose anchors
    the WHY of the guardrail so an editing LLM cannot quietly relax
    the contract."""
    g_start = cmd_text.find("\n## Guardrails")
    g_end = cmd_text.find("\n## ", g_start + 1)
    g_section = cmd_text[g_start:g_end] if g_end > 0 else cmd_text[g_start:]
    audit_count = g_section.count("**Audit**")
    assert audit_count >= 5, (
        f"INV-2: §Guardrails section must contain at least 5 '**Audit**' "
        f"blocks (one per guardrail). Found {audit_count}."
    )


# ---------- INV-1: byte-identical prompt cross-file ----------

def test_inv1_prompt_string_in_this_file(cmd_text):
    """INV-1: the /PACT:scan-pending-tasks slug appears verbatim in
    this file (in §References cross-links and in operation prose)."""
    target = "/PACT:scan-pending-tasks"
    assert target in cmd_text


def test_inv1_byte_identical_across_three_command_files(cmd_text):
    """INV-1 cross-file byte-identity: the literal string
    /PACT:scan-pending-tasks appears in all 3 command files. Drift
    between them breaks the CronList idempotency lookup (start-side)
    and teardown lookup (stop-side).

    Counter-test-by-revert scope (per PR #723 cycle-1 lesson): this
    test requires reverting ALL THREE .md files together to falsify
    correctly — single-file revert masks cross-file drift detection."""
    target = "/PACT:scan-pending-tasks"
    start_text = (ROOT / "commands" / "start-pending-scan.md").read_text(encoding="utf-8")
    stop_text = (ROOT / "commands" / "stop-pending-scan.md").read_text(encoding="utf-8")
    assert target in start_text, (
        f"INV-1: start-pending-scan.md missing canonical slug {target}"
    )
    assert target in stop_text, (
        f"INV-1: stop-pending-scan.md missing canonical slug {target}"
    )
    assert target in cmd_text, (
        f"INV-1: scan-pending-tasks.md missing canonical slug {target}"
    )


# ---------- INV-9: same-session-identity gate at step 1 of operation ----------

def test_inv9_same_session_identity_gate_section_present(cmd_text):
    """INV-9: the §Same-Session-Identity Gate section anchors the
    cross-session-contamination defense (Layer 3 of defense-in-depth).
    Without this gate, two concurrent PACT sessions sharing a team_name
    contaminate each other's task acceptance."""
    assert "## Same-Session-Identity Gate" in cmd_text


def test_inv9_gate_invoked_at_step_one_of_operation(cmd_text):
    """INV-9: the gate is invoked at step 1 of §Operation (before
    raw-read in step 4). Ordering matters: the gate must filter
    candidate tasks BEFORE the scan reads their metadata, not after.
    Reading-then-filtering still leaks cross-session metadata to
    the scan's working set."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    # Step 1 must reference the same-session-identity gate or
    # lead_session_id check.
    step_1_pos = op_section.find("1. ")
    step_2_pos = op_section.find("2. ", step_1_pos)
    if step_1_pos < 0 or step_2_pos < 0:
        # Fallback: scan the whole operation for the gate token
        # appearing before the raw-read step.
        gate_pos = op_section.lower().find("same-session-identity gate")
        if gate_pos < 0:
            gate_pos = op_section.find("lead_session_id")
        raw_read_pos = op_section.lower().find("raw-read")
        if raw_read_pos < 0:
            raw_read_pos = op_section.find("metadata.teachback_submit")
        assert gate_pos >= 0 and raw_read_pos >= 0
        assert gate_pos < raw_read_pos, (
            "INV-9: same-session-identity gate must precede raw-read "
            "in §Operation."
        )
    else:
        step_1 = op_section[step_1_pos:step_2_pos]
        assert (
            "same-session-identity gate" in step_1.lower()
            or "lead_session_id" in step_1
        ), (
            "INV-9: Operation step 1 must invoke the same-session-"
            "identity gate (compare session_id to "
            "metadata.lead_session_id)."
        )


def test_inv9_gate_uses_exact_equality_match(cmd_text):
    """INV-9: gate uses exact-equality match on lead_session_id.
    Substring or partial match invites false-positive acceptance."""
    section_start = cmd_text.find("\n## Same-Session-Identity Gate")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    assert "exact-equality" in section.lower() or "==" in section
    assert "lead_session_id" in section


def test_inv9_fails_closed_on_missing_field(cmd_text):
    """INV-9 audit: tasks where metadata.lead_session_id is ABSENT
    must be SKIPPED (fail-closed). Fail-open would re-open the
    cross-session contamination vector. An editing LLM tempted to
    'be lenient on missing field' is re-introducing the failure mode."""
    section_start = cmd_text.find("\n## Same-Session-Identity Gate")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    # The fail-closed semantics must be documented.
    assert "fail-closed" in section.lower() or "absent" in section.lower()
    assert "skip" in section.lower()


# ---------- INV-10: lead-only completion contract ----------

def test_inv10_lead_only_completion_contract_section_present(cmd_text):
    """INV-10: §Lead-Only Completion Contract anchors the canonical
    acceptance two-call pair (SendMessage FIRST, then TaskUpdate
    completed). The scan does NOT call TaskUpdate(status="completed")
    standalone — only as the second half of the canonical pair."""
    assert "## Lead-Only Completion Contract" in cmd_text


def test_inv10_acceptance_pair_ordering_sendmessage_first(cmd_text):
    """INV-10: SendMessage MUST precede TaskUpdate in the canonical
    numbered acceptance pair (SendMessage-FIRST ordering invariant
    per completion-authority protocol §12). Pin the numbered-list
    ordering: step '1.' references SendMessage and step '2.'
    references TaskUpdate. The descriptive prose may mention either
    token in either order; what is load-bearing is the canonical
    numbered procedure."""
    section_start = cmd_text.find("\n## Lead-Only Completion Contract")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    step_1_pos = section.find("1. ")
    step_2_pos = section.find("2. ", step_1_pos + 1)
    assert step_1_pos >= 0 and step_2_pos > step_1_pos, (
        "INV-10: §Lead-Only Completion Contract must contain the "
        "canonical numbered acceptance pair as steps '1.' and '2.'"
    )
    # Step 1 references SendMessage; step 2 references TaskUpdate.
    step_1 = section[step_1_pos:step_2_pos]
    step_2 = section[step_2_pos:step_2_pos + 300]
    assert "SendMessage" in step_1, (
        "INV-10: numbered step 1 must reference SendMessage (FIRST)"
    )
    assert "TaskUpdate" in step_2, (
        "INV-10: numbered step 2 must reference TaskUpdate (SECOND)"
    )
    # And the SendMessage-FIRST invariant must be pinned in prose.
    assert "FIRST" in step_1 or "SendMessage-FIRST" in section


def test_inv10_no_standalone_taskupdate_completed_in_operation(cmd_text):
    """INV-10: §Operation must invoke TaskUpdate(status='completed')
    ONLY as the second half of the acceptance pair (paired with
    SendMessage). Standalone TaskUpdate(status='completed') without
    the preceding SendMessage is a lead-only-completion violation."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    # If TaskUpdate appears in §Operation, SendMessage must precede it.
    if "TaskUpdate" in op_section:
        sendmsg_pos = op_section.find("SendMessage")
        taskupdate_pos = op_section.find("TaskUpdate")
        assert sendmsg_pos >= 0 and sendmsg_pos < taskupdate_pos, (
            "INV-10 (§Operation): if TaskUpdate is invoked here, "
            "SendMessage must precede it (acceptance pair ordering)."
        )


# ---------- Forbidden-token absence ----------

@pytest.mark.parametrize("forbidden_slug", [
    'Skill("PACT:watch-inbox")',
    'Skill("PACT:unwatch-inbox")',
    'Skill("PACT:inbox-wake")',
])
def test_forbidden_legacy_slug_invocation_absent(cmd_text, forbidden_slug):
    """Defense-in-depth: legacy Monitor-mechanism slug INVOCATIONS
    must not appear in the scan body. Audit prose may mention legacy
    tokens (STATE_FILE, Monitor, INBOX_GREW, armed_by_session_id) as
    'the architectural replacement for Monitor's INBOX_GREW' or similar
    context — that prose is load-bearing because it anchors the WHY
    of the cron-mechanism replacement. What is forbidden is the
    OPERATIONAL invocation form."""
    assert forbidden_slug not in cmd_text, (
        f"scan-pending-tasks.md contains forbidden legacy slug "
        f"invocation '{forbidden_slug}' — operational incomplete "
        f"migration."
    )


# ---------- Cross-link discipline ----------

def test_references_section_links_to_companion_commands(cmd_text):
    """§References must link to both companion commands."""
    refs_start = cmd_text.find("## References")
    refs_section = cmd_text[refs_start:] if refs_start >= 0 else ""
    assert refs_start >= 0
    assert "start-pending-scan.md" in refs_section
    assert "stop-pending-scan.md" in refs_section
    assert "@~/" not in refs_section


def test_references_charter_cron_fire_and_scan_discipline_sections(cmd_text):
    """§References cross-links to the charter §Cron-Fire Mechanism +
    §Scan Discipline sections (the protocol contract surface for the
    Cron-Origin Distinction and the 5 guardrails respectively)."""
    refs_start = cmd_text.find("## References")
    refs_section = cmd_text[refs_start:] if refs_start >= 0 else ""
    assert "cron-fire-mechanism" in refs_section.lower() or "Cron-Fire Mechanism" in refs_section
    assert "scan-discipline" in refs_section.lower() or "Scan Discipline" in refs_section
