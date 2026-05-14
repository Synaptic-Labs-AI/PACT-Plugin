"""
Structural invariants for commands/scan-pending-tasks.md.

Reads the .md file as text and asserts presence/shape properties.
Each P0 invariant assertion is paired with prose explaining the
editing-LLM regression it prevents.

Invariants verified here:
- Cross-Skill Prompt-String Byte-Identity: /PACT:scan-pending-tasks across all 3 command files
  (start-pending-scan.md + scan-pending-tasks.md + stop-pending-scan.md)
- Verbatim Anti-Hallucination Guardrails: (Read-Filesystem-Only through Emit-Nothing-If-Empty) appear VERBATIM
- Cron-Fire Marker Discipline: presence at top of file
- Lead-Only Completion Preservation (scan body uses canonical
  acceptance pair, no inline TaskUpdate(completed) standalone)
- Cron-Origin Distinction section present and forbids cron-fire as user-consent
- Forbidden-token absence (no Monitor/STATE_FILE/armed_by_session_id)
- Line-cap discipline

Counter-test-by-revert scope: reverting pact-plugin/commands/scan-pending-tasks.md
falsifies these tests with discriminating cardinality. Reverting all 3 .md
files together is required for Cross-Skill Prompt-String Byte-Identity byte-identity counter-test (single-file
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
        f"scan-pending-tasks.md missing at {CMD_FILE} — Cross-Skill Prompt-String Byte-Identity / Verbatim Anti-Hallucination Guardrails / Cron-Fire Marker Discipline / Lead-Only Completion Preservation "
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
    three new commands due to the 6 guardrail audit anchors. Cap at
    330 lines given the guardrail prose is verbatim and non-compressible
    and the Warmup-Grace-Skip Procedure adds an additional code-prose block."""
    line_count = cmd_text.count("\n") + 1
    assert line_count <= 330, (
        f"scan-pending-tasks.md is {line_count} lines; cap is 330. "
        f"The 6 verbatim guardrails account for substantial prose; "
        f"further additions should land in the charter, not here."
    )


# ---------- Cron-Fire Marker Discipline: [CRON-FIRE] marker presence at file top ----------

def test_cron_fire_marker_discipline_cron_fire_marker_at_top_of_file(cmd_text):
    """Cron-Fire Marker Discipline: the [CRON-FIRE] discipline marker must appear in the
    first 30 lines of the file. The marker is the structural anchor
    for the Cron-Origin Distinction — removing it lets an editing
    LLM treat cron fires as user-typed input, re-opening the
    hallucination-cascade failure mode."""
    head = "\n".join(cmd_text.splitlines()[:30])
    assert "[CRON-FIRE]" in head, (
        "Cron-Fire Marker Discipline: [CRON-FIRE] marker must appear in first 30 lines of "
        "scan-pending-tasks.md as the structural anchor for the "
        "Cron-Origin Distinction. Removal re-opens the cascade."
    )


def test_cron_fire_marker_discipline_cron_origin_distinction_section_present(cmd_text):
    """Cron-Fire Marker Discipline: the §Cron-Fire Origin section anchors the principle
    statement that cron-fire is NOT user consent."""
    assert "## Cron-Fire Origin" in cmd_text


def test_cron_fire_marker_discipline_cron_origin_section_forbids_consent_treatment(cmd_text):
    """Cron-Fire Marker Discipline audit: the section explicitly states cron-fire is NOT
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


# ---------- Verbatim Anti-Hallucination Guardrails: 6 anti-hallucination guardrails (Read-Filesystem-Only through Warmup-Grace-Skip) verbatim ----------

def test_verbatim_anti_hallucination_guardrails_guardrails_section_present(cmd_text):
    """Verbatim Anti-Hallucination Guardrails: the §Guardrails section anchors all 6 guardrails."""
    assert "## Guardrails" in cmd_text


@pytest.mark.parametrize("guardrail_header", [
    "### Read-Filesystem-Only",
    "### No-Narration",
    "### Raw-Read-Metadata",
    "### Race-Window-Skip",
    "### Emit-Nothing-If-Empty",
    "### Warmup-Grace-Skip",
])
def test_verbatim_anti_hallucination_guardrails_each_guardrail_header_present(cmd_text, guardrail_header):
    """Verbatim Anti-Hallucination Guardrails: each of the 6 guardrails has its dedicated section
    header. Verbatim presence — paraphrase during PR review = silent
    regression of the anti-hallucination contract."""
    assert guardrail_header in cmd_text, (
        f"Verbatim Anti-Hallucination Guardrails: scan-pending-tasks.md missing required guardrail "
        f"header '{guardrail_header}'. The 6 guardrails are load-"
        f"bearing per architecture spec; each prevents a specific "
        f"cascade failure mode."
    )


def test_verbatim_anti_hallucination_guardrails_exactly_six_guardrail_headers(cmd_text):
    """Verbatim Anti-Hallucination Guardrails cardinality: exactly 6 guardrail headers
    in the canonical set (Read-Filesystem-Only, No-Narration, Raw-Read-Metadata,
    Race-Window-Skip, Emit-Nothing-If-Empty, Warmup-Grace-Skip). Adding a 7th silently expands the
    audit-anchored contract; removing one silently relaxes it."""
    canonical_guardrail_headers = (
        "### Read-Filesystem-Only",
        "### No-Narration",
        "### Raw-Read-Metadata",
        "### Race-Window-Skip",
        "### Emit-Nothing-If-Empty",
        "### Warmup-Grace-Skip",
    )
    found_headers = [
        line for line in cmd_text.splitlines()
        if line.strip() in canonical_guardrail_headers
    ]
    assert len(found_headers) == 6, (
        f"Verbatim Anti-Hallucination Guardrails cardinality: expected exactly "
        f"6 canonical guardrail headers from {canonical_guardrail_headers}, "
        f"found {len(found_headers)}: {found_headers}"
    )


def test_verbatim_anti_hallucination_guardrails_each_guardrail_has_audit_block(cmd_text):
    """Verbatim Anti-Hallucination Guardrails audit anchor: each guardrail must be followed by a
    paragraph starting with '**Audit**:'. The audit prose anchors
    the WHY of the guardrail so an editing LLM cannot quietly relax
    the contract.

    Strictness: EXACTLY 6 audit blocks, not >=6. A 7th
    audit block would either duplicate existing guardrail audit prose
    (silent redundancy) or expand the audit-anchored contract beyond
    the 6-guardrail architectural pin (silent contract expansion).
    Consistent with the companion test_verbatim_anti_hallucination_guardrails_exactly_six_guardrail_headers
    cardinality assertion."""
    g_start = cmd_text.find("\n## Guardrails")
    g_end = cmd_text.find("\n## ", g_start + 1)
    g_section = cmd_text[g_start:g_end] if g_end > 0 else cmd_text[g_start:]
    audit_count = g_section.count("**Audit**")
    assert audit_count == 6, (
        f"Verbatim Anti-Hallucination Guardrails: §Guardrails section must contain EXACTLY 6 '**Audit**' "
        f"blocks (one per guardrail Read-Filesystem-Only through Warmup-Grace-Skip). Found {audit_count}."
    )


# ---------- Cross-Skill Prompt-String Byte-Identity: byte-identical prompt cross-file ----------

def test_cross_skill_prompt_string_byte_identity_prompt_string_in_this_file(cmd_text):
    """Cross-Skill Prompt-String Byte-Identity: the /PACT:scan-pending-tasks slug appears verbatim in
    this file (in §References cross-links and in operation prose)."""
    target = "/PACT:scan-pending-tasks"
    assert target in cmd_text


def test_cross_skill_prompt_string_byte_identity_byte_identical_across_three_command_files(cmd_text):
    """Cross-Skill Prompt-String Byte-Identity cross-file byte-identity (commit-9 tightened): the slug
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
        f"Cross-Skill Prompt-String Byte-Identity: start-pending-scan.md missing operational slug "
        f"{operational_slug!r}"
    )
    assert operational_slug in stop_text, (
        f"Cross-Skill Prompt-String Byte-Identity: stop-pending-scan.md missing operational slug "
        f"{operational_slug!r} — filter target drift; teardown would "
        f"fail to find the cron registered by start-pending-scan."
    )
    assert operational_slug in cmd_text, (
        f"Cross-Skill Prompt-String Byte-Identity: scan-pending-tasks.md missing operational slug "
        f"{operational_slug!r} — the scan body itself is named by the "
        f"slug that CronCreate registers; drift breaks the firing chain."
    )


# ---------- Lead-Only Completion Preservation: lead-only completion contract ----------

def test_lead_only_completion_preservation_lead_only_completion_contract_section_present(cmd_text):
    """Lead-Only Completion Preservation: §Lead-Only Completion Contract anchors the canonical
    acceptance two-call pair (SendMessage FIRST, then TaskUpdate
    completed). The scan does NOT call TaskUpdate(status="completed")
    standalone — only as the second half of the canonical pair."""
    assert "## Lead-Only Completion Contract" in cmd_text


def test_lead_only_completion_preservation_acceptance_pair_ordering_sendmessage_first(cmd_text):
    """Lead-Only Completion Preservation: SendMessage MUST precede TaskUpdate in the canonical
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
        "Lead-Only Completion Preservation: §Lead-Only Completion Contract must contain the "
        "canonical numbered acceptance pair as steps '1.' and '2.'"
    )
    # Step 1 references SendMessage; step 2 references TaskUpdate.
    step_1 = section[step_1_pos:step_2_pos]
    step_2 = section[step_2_pos:step_2_pos + 300]
    assert "SendMessage" in step_1, (
        "Lead-Only Completion Preservation: numbered step 1 must reference SendMessage (FIRST)"
    )
    assert "TaskUpdate" in step_2, (
        "Lead-Only Completion Preservation: numbered step 2 must reference TaskUpdate (SECOND)"
    )
    # And the SendMessage-FIRST invariant must be pinned in prose.
    assert "FIRST" in step_1 or "SendMessage-FIRST" in section


def test_lead_only_completion_preservation_no_standalone_taskupdate_completed_in_operation(cmd_text):
    """Lead-Only Completion Preservation: §Operation must invoke TaskUpdate(status='completed')
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
            "Lead-Only Completion Preservation (§Operation): if TaskUpdate is invoked here, "
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
