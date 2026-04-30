"""
Structural-pattern tests for Teardown invocation callsites.

Per architect §3 + §11: the Teardown operation is invoked from four
callsites (3 lead-side commands + 1 teammate-side skill section). One
command (/imPACT) is the explicit NEGATIVE invariant — none of imPACT's
six outcomes warrant lead-side teardown (continue work, or escalate to
user), so the absence of an invocation is by design and must be fenced
against accidental re-introduction.

Phantom-green mitigation: assertions match short semantic anchors
(skill slug + operation token), not full sentences.
"""
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
PACT_AGENT_TEAMS_SKILL = (
    PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"
)

WRAP_UP_PATH = COMMANDS_DIR / "wrap-up.md"
PAUSE_PATH = COMMANDS_DIR / "pause.md"
IMPACT_PATH = COMMANDS_DIR / "imPACT.md"

WAKE_SKILL_SLUG = 'Skill("PACT:inbox-wake")'
TEARDOWN_TOKEN = "Teardown"


def _read(path: Path) -> str:
    assert path.is_file(), f"Required callsite file missing: {path}"
    return path.read_text(encoding="utf-8")


class TestLeadSideCallsitesPresent:
    """/wrap-up and /pause must invoke the wake-skill Teardown.

    These commands tear down the lead session cleanly, so the lead's Monitor
    is stopped and the registry sidecar removed before the session exits.
    """

    @pytest.mark.parametrize(
        "command_path",
        [WRAP_UP_PATH, PAUSE_PATH],
        ids=lambda p: p.name,
    )
    def test_lead_command_invokes_wake_teardown(self, command_path: Path):
        body = _read(command_path)
        assert WAKE_SKILL_SLUG in body, (
            f"{command_path.name} must invoke {WAKE_SKILL_SLUG}"
        )
        assert TEARDOWN_TOKEN in body, (
            f"{command_path.name} must reference the Teardown operation"
        )


class TestImpactNegativeInvariant:
    """/imPACT must NOT invoke the wake-skill Teardown.

    Architectural fence: imPACT outcomes are continue-work or escalate-to-user
    (no session shutdown). Re-introducing a Teardown call would prematurely
    stop the Monitor while the lead is still active. This negative invariant
    catches any accidental copy-paste from /wrap-up or /pause.
    """

    def test_impact_does_not_invoke_wake_skill(self):
        body = _read(IMPACT_PATH)
        assert WAKE_SKILL_SLUG not in body, (
            f"{IMPACT_PATH.name} must NOT invoke {WAKE_SKILL_SLUG} — "
            "imPACT does not shut down the session, so Teardown would prematurely "
            "stop the Monitor while the lead is still active"
        )


class TestTeammateSideShutdownInvocation:
    """pact-agent-teams §Shutdown must instruct teammates to Teardown before approving.

    Per architect §15.3: the agent-side Teardown is the ONLY mechanism that
    can call TaskStop on the teammate's Monitor (hooks cannot reach
    agent-runtime tools). The shutdown_request flow is the natural insertion
    point — there is no equivalent of /wrap-up for teammates.
    """

    def test_pact_agent_teams_skill_exists(self):
        assert PACT_AGENT_TEAMS_SKILL.is_file(), (
            f"pact-agent-teams skill body missing at {PACT_AGENT_TEAMS_SKILL}"
        )

    def test_shutdown_section_invokes_wake_teardown(self):
        body = _read(PACT_AGENT_TEAMS_SKILL)
        # Slice out the ## Shutdown section. Anchor on the header.
        assert "## Shutdown" in body, (
            "pact-agent-teams must contain a ## Shutdown section"
        )
        shutdown_idx = body.index("## Shutdown")
        shutdown_section = body[shutdown_idx:]
        # Bound the section to the next top-level header if present.
        for next_header in ("\n## ", "\n# "):
            next_idx = shutdown_section.find(next_header, len("## Shutdown"))
            if next_idx > 0:
                shutdown_section = shutdown_section[:next_idx]
                break
        assert WAKE_SKILL_SLUG in shutdown_section, (
            "## Shutdown must invoke Skill(\"PACT:inbox-wake\")"
        )
        assert TEARDOWN_TOKEN in shutdown_section, (
            "## Shutdown must reference the Teardown operation"
        )

    def test_shutdown_section_uses_before_approving_timing(self):
        """Timing prose anchors the agent-vs-hook capability asymmetry.

        The Teardown must run BEFORE the teammate approves shutdown_request —
        once approved, the agent's process terminates and TaskStop is no
        longer reachable. Per architect §15.3 audit annotation.
        """
        body = _read(PACT_AGENT_TEAMS_SKILL)
        shutdown_idx = body.index("## Shutdown")
        shutdown_section = body[shutdown_idx:]
        for next_header in ("\n## ", "\n# "):
            next_idx = shutdown_section.find(next_header, len("## Shutdown"))
            if next_idx > 0:
                shutdown_section = shutdown_section[:next_idx]
                break
        assert "before approving" in shutdown_section.lower(), (
            "## Shutdown must specify the Teardown runs BEFORE approving "
            "shutdown_request — TaskStop becomes unreachable after process termination"
        )
