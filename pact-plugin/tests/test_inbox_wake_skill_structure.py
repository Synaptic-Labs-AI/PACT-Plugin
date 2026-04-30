"""
Structural-pattern tests for pact-plugin/skills/inbox-wake/SKILL.md.

The skill body is the agent-execution surface for the wake mechanism. These
tests are semantic-anchor checks: they parse the skill body and assert
load-bearing tokens, sections, phrases, and explicit absences (negative
invariants). They do NOT execute the skill or simulate agent behavior —
the dogfood runbook covers behavioral verification.

Phantom-green mitigation: assertions match short semantic anchors
(operation names, file names, threshold tokens, atomic-rename keywords),
not full sentences that an editing LLM could inadvertently rewrite without
breaking meaning.

Negative invariants are load-bearing: they fence against architectural
drift (re-introduction of cron/watchdog/Recovery branches that PREPARE §C
falsified).
"""
import re
from pathlib import Path

import pytest


SKILL_BODY_PATH = (
    Path(__file__).parent.parent / "skills" / "inbox-wake" / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_body() -> str:
    return SKILL_BODY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_body_lines(skill_body: str) -> list[str]:
    return skill_body.splitlines()


class TestSkillBodyFile:
    """Skill body file existence + frontmatter."""

    def test_skill_body_file_exists(self):
        assert SKILL_BODY_PATH.is_file(), (
            f"Skill body must exist at {SKILL_BODY_PATH.relative_to(SKILL_BODY_PATH.parents[3])}"
        )

    def test_frontmatter_has_name_inbox_wake(self, skill_body: str):
        # Frontmatter is the first --- delimited block.
        match = re.match(r"^---\n(.*?)\n---\n", skill_body, re.DOTALL)
        assert match is not None, "Skill body must open with YAML frontmatter"
        frontmatter = match.group(1)
        assert re.search(r"^name:\s*inbox-wake\s*$", frontmatter, re.MULTILINE), (
            "Frontmatter must declare name: inbox-wake"
        )

    def test_frontmatter_has_non_empty_description(self, skill_body: str):
        match = re.match(r"^---\n(.*?)\n---\n", skill_body, re.DOTALL)
        assert match is not None
        frontmatter = match.group(1)
        # description: may be a single-line value or a YAML block scalar (|).
        # Accept either; require non-whitespace content after "description:".
        assert re.search(r"^description:\s*(\S|\|)", frontmatter, re.MULTILINE), (
            "Frontmatter must declare a non-empty description"
        )


class TestCompactionLineBudget:
    """#594 compaction-restoration ceiling for skill bodies."""

    def test_skill_body_within_compaction_ceiling(self, skill_body_lines: list[str]):
        # Per #444 four-tier durability model: Tier 1 inline-skill restoration
        # caps at ~292 lines. Going over silently sheds content on compaction.
        assert len(skill_body_lines) <= 292, (
            f"Skill body has {len(skill_body_lines)} lines; ceiling is 292"
        )


class TestRequiredSectionsPresent:
    """All canonical D1 sections present per architect §5."""

    REQUIRED_HEADERS = [
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

    @pytest.mark.parametrize("header", REQUIRED_HEADERS)
    def test_required_section_present(self, header: str, skill_body_lines: list[str]):
        assert header in skill_body_lines, (
            f"Required section header missing: {header!r}"
        )


class TestNegativeInvariants:
    """Sections that MUST NOT appear — D1 architectural fence.

    PREPARE §C falsified the cron+Monitor self-defeating loop. Re-introducing
    any of these sections silently restores the killed-Monitor failure mode.
    """

    FORBIDDEN_HEADERS = [
        "## Cron Block",
        "## Wake-State-Check Algorithm",
        "## Per-Branch Action Sequences",
        "## Recovery",
        "### Recovery",
    ]

    @pytest.mark.parametrize("header", FORBIDDEN_HEADERS)
    def test_forbidden_section_absent(self, header: str, skill_body_lines: list[str]):
        assert header not in skill_body_lines, (
            f"Forbidden section reintroduced: {header!r} — D1 deliberately drops "
            "the watchdog layer per PREPARE §C kill-mechanism finding"
        )

    def test_no_cron_job_id_as_schema_field(self, skill_body: str):
        # STATE_FILE schema is intentionally minimal (3 fields). cron_job_id
        # was a rev-3 concept dropped with cron. The token may appear in
        # anti-rule prose ("do not re-add cron_job_id"); the negative invariant
        # is the SCHEMA shape — check for JSON-style field declarations.
        for shape in ('"cron_job_id":', "'cron_job_id':", "cron_job_id ="):
            assert shape not in skill_body, (
                f"cron_job_id reintroduced as schema field via {shape!r} — D1 has no watchdog"
            )

    def test_no_heartbeat_field_in_schema(self, skill_body: str):
        # HB_FILE / heartbeat field were rev-3 concepts dropped with cron.
        # The literal token "heartbeat" may appear ONLY in the schema-minimality
        # rationale or anti-rule prose, not as a written/read field. Assert no
        # schema-field reintroduction by checking common field-token shapes.
        for token in ('"heartbeat":', "'heartbeat':", "heartbeat ="):
            assert token not in skill_body, (
                f"Heartbeat reintroduced as schema field via {token!r} — D1 has no heartbeat"
            )


class TestOperationsExactlyArmAndTeardown:
    """`## Operations` enumerates exactly Arm and Teardown — no Recovery."""

    def test_arm_subsection_present(self, skill_body: str):
        assert re.search(r"^###\s+Arm\b", skill_body, re.MULTILINE), (
            "## Operations must contain ### Arm subsection"
        )

    def test_teardown_subsection_present(self, skill_body: str):
        assert re.search(r"^###\s+Teardown\b", skill_body, re.MULTILINE), (
            "## Operations must contain ### Teardown subsection"
        )

    def test_no_recovery_subsection(self, skill_body: str):
        assert not re.search(r"^###\s+Recovery\b", skill_body, re.MULTILINE), (
            "Recovery operation reintroduced — D1 has only Arm and Teardown"
        )


class TestAlarmClockFraming:
    """Both alarm-clock paragraphs are non-negotiable in `## Overview`.

    First paragraph: signal-not-content scope (prevents "lead parses wake stdout").
    Second paragraph: between-tool-call scope (prevents "expects mid-tool interrupt").
    """

    def test_alarm_clock_paragraph_present(self, skill_body: str):
        assert "Monitor is an alarm clock, not a mailbox" in skill_body, (
            "First alarm-clock paragraph (signal-not-content) missing from Overview"
        )

    def test_between_tool_calls_paragraph_present(self, skill_body: str):
        # The paragraph carries the scope claim. Anchor on the load-bearing
        # phrase pair: "between tool calls" + "not mid-tool".
        assert "between tool calls within a turn" in skill_body, (
            "Second alarm-clock paragraph (between-tool-calls scope) missing"
        )
        assert "not mid-tool" in skill_body, (
            "Second alarm-clock paragraph must explicitly state 'not mid-tool'"
        )


class TestTeardownF6Tolerance:
    """`## Teardown Block` contains the F6 tolerance phrasing.

    Removing 'ignoring not-found errors' silently restores crash-on-stale-ID
    when TaskStop runs against a Monitor that died silently mid-session.
    """

    def test_ignoring_not_found_errors_phrase(self, skill_body: str):
        assert "ignoring not-found errors" in skill_body, (
            "Teardown Block must contain literal phrase 'ignoring not-found errors' "
            "(F6 tolerance invariant per PREPARE)"
        )


class TestMonitorBlockStdoutDiscipline:
    """`## Monitor Block` distinguishes turn-firing stdout from non-firing channels."""

    def test_inbox_grew_token_present(self, skill_body: str):
        assert "INBOX_GREW" in skill_body, (
            "Monitor Block must reference the INBOX_GREW stdout token"
        )

    def test_stderr_non_firing_anchor(self, skill_body: str):
        # Anchor on any of the canonical stderr-non-firing phrasings — the
        # invariant is "stdout fires turns, stderr does not." Phantom-green
        # mitigation: accept multiple canonical phrasings.
        anchors = [">&2", "stderr does not turn-fire", "diagnostic and lifecycle output goes to"]
        assert any(a in skill_body.lower() for a in [a.lower() for a in anchors]), (
            "Monitor Block must distinguish turn-firing stdout from non-firing stderr"
        )


class TestMonitorBlockSingleFileInbox:
    """F1 invariant: inbox is single JSON file, byte-grow via wc -c.

    Path is parametric (`{agent_name}`), NOT hardcoded `team-lead.json` —
    the same Monitor body is used for both lead and teammate sessions.
    """

    def test_wc_byte_grow_detection(self, skill_body: str):
        assert "wc -c" in skill_body, (
            "Monitor Block must use `wc -c` for byte-grow detection (F1 invariant)"
        )

    def test_inbox_path_is_parametric_with_agent_name(self, skill_body: str):
        # The Monitor block must carry the parametric path token, not the
        # rev-3 hardcoded lead path. Symmetric scope (§15) requires this.
        assert "inboxes/{agent_name}.json" in skill_body, (
            "Inbox path must interpolate {agent_name} (covers both lead and teammate)"
        )


class TestStateFileSchemaThreeFields:
    """`## WriteStateFile Block` schema: exactly v, monitor_task_id, armed_at."""

    def test_v_field_present(self, skill_body: str):
        assert '"v":' in skill_body or "'v':" in skill_body, (
            "WriteStateFile Block schema must declare v field"
        )

    def test_monitor_task_id_field_present(self, skill_body: str):
        assert "monitor_task_id" in skill_body, (
            "WriteStateFile Block schema must declare monitor_task_id field"
        )

    def test_armed_at_field_present(self, skill_body: str):
        assert "armed_at" in skill_body, (
            "WriteStateFile Block schema must declare armed_at field"
        )

    def test_per_agent_state_filename_token(self, skill_body: str):
        # Per-agent suffix lives in the FILENAME, not the schema.
        assert "inbox-wake-state-{agent_name}.json" in skill_body, (
            "STATE_FILE filename must interpolate {agent_name} (per-agent suffix)"
        )


class TestLongToolEmpiricalAnchor:
    """`## Failure Modes` contains §12.b empirical-timing tokens.

    The empirical anchor (00:01:34Z send / INBOX_GREW fired during sleep /
    00:02:23Z tool return) makes the scope claim observable, not just asserted.
    Without these tokens, an editing LLM could remove the scope claim and
    silently overpromise mid-tool interrupt.
    """

    def test_long_tool_failure_mode_header_present(self, skill_body: str):
        # Anchor on the section's distinguishing phrase, not the full sentence.
        assert "Long single-tool calls block wake delivery" in skill_body, (
            "Failure Modes must contain the long-single-tool-blocks-wake entry"
        )

    def test_empirical_timing_tokens_present(self, skill_body: str):
        # Both peer-send and tool-return timestamps anchor the empirical claim.
        assert "00:01:34Z" in skill_body, (
            "Empirical timing token 00:01:34Z (peer send) must appear"
        )
        assert "00:02:23Z" in skill_body, (
            "Empirical timing token 00:02:23Z (tool return) must appear"
        )


class TestAtomicRenameWritePattern:
    """Atomic-rename token presence in WriteStateFile Block prose/pseudocode."""

    def test_atomic_rename_token(self, skill_body: str):
        # `os.replace` or "atomic rename" or `.tmp` + rename — anchor on any.
        anchors = ["os.replace", "atomic rename", "atomic-rename"]
        assert any(a in skill_body for a in anchors), (
            "WriteStateFile Block must use atomic-rename pattern (os.replace / atomic rename)"
        )


class TestSymmetricScopeArmCoversLeadAndTeammate:
    """Arm prose explicitly states symmetric scope: BOTH lead and teammate.

    Per architect §15: one skill, two invocation sites. The `## Operations`
    Arm subsection must mention both roles so an editing LLM cannot accidentally
    fork the skill into lead-only or teammate-only.
    """

    def test_arm_subsection_mentions_lead_and_teammate(self, skill_body: str):
        # Slice the Arm subsection. Anchor on the role tokens.
        match = re.search(
            r"^###\s+Arm\b(.*?)(?=^###\s+|^##\s+)",
            skill_body,
            re.MULTILINE | re.DOTALL,
        )
        assert match is not None, "### Arm subsection must be present"
        arm_section = match.group(1).lower()
        assert "team-lead" in arm_section or "lead" in arm_section, (
            "Arm subsection must reference the lead role (symmetric scope)"
        )
        assert "teammate" in arm_section, (
            "Arm subsection must reference the teammate role (symmetric scope)"
        )
