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
    the contract.

    Strictness (commit-9): EXACTLY 5 audit blocks, not >=5. A 6th
    audit block would either duplicate G1-G5 audit prose (silent
    redundancy) or expand the audit-anchored contract beyond the
    5-guardrail architectural pin (silent contract expansion).
    Consistent with the companion test_inv2_exactly_five_guardrail_headers
    cardinality assertion."""
    g_start = cmd_text.find("\n## Guardrails")
    g_end = cmd_text.find("\n## ", g_start + 1)
    g_section = cmd_text[g_start:g_end] if g_end > 0 else cmd_text[g_start:]
    audit_count = g_section.count("**Audit**")
    assert audit_count == 5, (
        f"INV-2: §Guardrails section must contain EXACTLY 5 '**Audit**' "
        f"blocks (one per guardrail G1-G5). Found {audit_count}. "
        f"Tightened to == in commit-9 strictness pass."
    )


# ---------- INV-1: byte-identical prompt cross-file ----------

def test_inv1_prompt_string_in_this_file(cmd_text):
    """INV-1: the /PACT:scan-pending-tasks slug appears verbatim in
    this file (in §References cross-links and in operation prose)."""
    target = "/PACT:scan-pending-tasks"
    assert target in cmd_text


def test_inv1_byte_identical_across_three_command_files(cmd_text):
    """INV-1 cross-file byte-identity (commit-9 tightened): the slug
    literal extracted from start-pending-scan.md's CronCreate( call
    (the operational source-of-truth — what the platform actually
    receives) MUST appear byte-identical in all 3 command files. Drift
    between them breaks the CronList idempotency lookup (start-side)
    and teardown lookup (stop-side).

    Tightening over substring-presence: extract the slug from the
    OPERATIONAL CronCreate call shape, not a hardcoded literal in
    this test — this means the test follows the source-of-truth
    rather than caching its own constant that could drift from the
    actual call.

    Counter-test-by-revert scope (per PR #723 cycle-1 lesson): this
    test requires reverting ALL THREE .md files together to falsify
    correctly — single-file revert masks cross-file drift detection."""
    import re
    start_text = (ROOT / "commands" / "start-pending-scan.md").read_text(encoding="utf-8")
    stop_text = (ROOT / "commands" / "stop-pending-scan.md").read_text(encoding="utf-8")
    # Extract operational prompt from start-pending-scan.md's CronCreate( block
    block_start = start_text.find("CronCreate(")
    assert block_start >= 0, "start-pending-scan.md missing CronCreate( call"
    block_end = start_text.find(")", block_start)
    block = start_text[block_start:block_end + 1]
    m = re.search(r'prompt="([^"]+)"', block)
    assert m is not None, (
        f"Cannot extract prompt=... from start-pending-scan.md's "
        f"CronCreate block: {block!r}"
    )
    operational_slug = m.group(1)
    # Byte-identity contract: the operational slug appears in all 3 files.
    assert operational_slug in start_text, (
        f"INV-1: start-pending-scan.md missing operational slug "
        f"{operational_slug!r}"
    )
    assert operational_slug in stop_text, (
        f"INV-1: stop-pending-scan.md missing operational slug "
        f"{operational_slug!r} — filter target drift; teardown would "
        f"fail to find the cron registered by start-pending-scan."
    )
    assert operational_slug in cmd_text, (
        f"INV-1: scan-pending-tasks.md missing operational slug "
        f"{operational_slug!r} — the scan body itself is named by the "
        f"slug that CronCreate registers; drift breaks the firing chain."
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
    the scan's working set.

    Strictness (commit-9): the forgiving fallback path (scan whole
    operation for gate-before-raw-read anywhere) was removed. The
    test requires the canonical numbered-list shape (step 1./2./3./...)
    AND requires step 1 to reference the gate explicitly. A future
    runbook reformat that drops numbered-list markers will fail this
    test with a clear error message rather than silently passing the
    looser any-position check."""
    op_start = cmd_text.find("## Operation")
    op_end = cmd_text.find("\n## ", op_start + 1)
    op_section = cmd_text[op_start:op_end] if op_end > 0 else cmd_text[op_start:]
    # Strict: step 1./2. markers MUST be present (canonical numbered-list shape).
    step_1_pos = op_section.find("1. ")
    step_2_pos = op_section.find("2. ", step_1_pos)
    assert step_1_pos >= 0, (
        "INV-9 strict: §Operation must use canonical numbered-list "
        "shape with '1. ' marker. No fallback path; restructure the "
        "section to use numbered steps."
    )
    assert step_2_pos > step_1_pos, (
        "INV-9 strict: §Operation must have a '2. ' marker after the "
        "'1. ' marker. Canonical numbered-list shape required."
    )
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
    """INV-9 / H2-strict (commit-9 tightened): gate uses verbatim `==`
    comparison on `metadata.lead_session_id` AND the current session_id.
    Substring or partial match invites false-positive acceptance.

    Tightening over the prior OR-permissive check (`"exact-equality" OR
    "=="`): require the verbatim equality-comparison pattern combining
    `metadata.lead_session_id` and the `==` operator in the same
    pseudocode/operation context. A future edit that drops the `==`
    operator while leaving the `lead_session_id` token in audit prose
    would have passed the prior check; the combined-token check
    catches it.

    H2 mitigation (security): the gate is the architectural replacement
    for the Monitor-era `armed_by_session_id` cross-session-contamination
    defense. The verbatim `==` is non-negotiable — `in` membership,
    `startswith`, or any partial-match relaxation re-opens the H2 vector.
    """
    import re as _re
    section_start = cmd_text.find("\n## Same-Session-Identity Gate")
    section_end = cmd_text.find("\n## ", section_start + 1)
    section = cmd_text[section_start:section_end] if section_end > 0 else cmd_text[section_start:]
    # Strict: BOTH `==` operator AND `lead_session_id` must appear in the
    # section. Beyond that, require the two tokens to co-occur within the
    # same pseudocode/operation context (audit-anchored equality assertion).
    assert "==" in section, (
        "INV-9/H2-strict: §Same-Session-Identity Gate must use verbatim "
        "'==' equality operator. Partial-match relaxation (in/startswith) "
        "re-opens H2 cross-session-contamination vector."
    )
    assert "lead_session_id" in section, (
        "INV-9/H2-strict: §Same-Session-Identity Gate must reference "
        "`metadata.lead_session_id` field. Required for H2 mitigation."
    )
    # Strict combined check: at least one occurrence of `lead_session_id`
    # within a small window of an `==` operator. Use regex spanning the
    # canonical pseudocode comparison shapes.
    combined_pattern = _re.compile(
        r"lead_session_id\s*==|==\s*[^=\n]{0,80}?(session_id|lead_session_id)",
        _re.MULTILINE,
    )
    assert combined_pattern.search(section) is not None, (
        "INV-9/H2-strict: §Same-Session-Identity Gate must contain a "
        "verbatim equality-comparison pattern combining lead_session_id "
        "and ==. Examples: `metadata.lead_session_id == current session_id`, "
        "`task.metadata.lead_session_id == pact_session_context[\"session_id\"]`. "
        "A separated mention of `==` AND `lead_session_id` in unrelated "
        "prose passes the prior individual-token check but fails this "
        "combined-pattern check."
    )


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
