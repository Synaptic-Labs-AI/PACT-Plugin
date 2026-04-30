"""
Cross-file consistency invariants for the inbox-wake feature.

Pin the Skill("PACT:inbox-wake") + Teardown invocations across the
three command bodies that own session-end paths (wrap-up, pause,
imPACT) and the charter §Wake Mechanism cross-ref to the skill body.
F7 cross-file consistency: charter must echo "between tool calls,
not mid-tool" so SKILL.md and charter remain aligned.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = ROOT / "commands"
PROTOCOLS_DIR = ROOT / "protocols"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- Teardown invocations in command bodies ----------

@pytest.mark.parametrize("command_file", [
    "wrap-up.md",
    "pause.md",
    "imPACT.md",
])
def test_command_invokes_inbox_wake_teardown(command_file):
    text = _read(COMMANDS_DIR / command_file)
    assert 'Skill("PACT:inbox-wake")' in text, (
        f"{command_file} missing Skill slug invocation"
    )
    assert "Teardown" in text, (
        f"{command_file} missing Teardown operation reference"
    )


def test_impact_teardown_is_force_termination_scoped():
    """imPACT's Teardown is conditional on force-termination dropping
    the team's active count to zero — confirm the scope-limiting prose."""
    text = _read(COMMANDS_DIR / "imPACT.md")
    assert "force-termination" in text or "force termination" in text


# ---------- Charter cross-reference ----------

def test_charter_has_wake_mechanism_section():
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    assert "## Part I — Message Delivery Mechanics" in text
    assert "### Wake Mechanism" in text


def test_charter_links_to_skill_body_via_relative_path():
    """Slug-link form (relative-path markdown link), NOT @~/ pattern."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    # Find the Wake Mechanism section.
    section_start = text.find("### Wake Mechanism")
    assert section_start >= 0
    # The cross-ref must use relative path to the skill body.
    section = text[section_start:section_start + 5000]
    assert "../skills/inbox-wake/SKILL.md" in section
    # And must NOT use the deprecated @~/ pattern.
    assert "@~/" not in section


def test_charter_echoes_lead_only_scope():
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = text.find("### Wake Mechanism")
    section = text[section_start:section_start + 5000]
    # Lead-only narrowing must be explicit in the charter rule list.
    assert "Lead-only" in section or "lead-only" in section.lower()


def test_charter_echoes_f7_between_tool_calls_not_mid_tool():
    """F7 cross-file consistency: charter must carry the same scope
    claim as SKILL.md §Overview/§Failure Modes."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = text.find("### Wake Mechanism")
    section = text[section_start:section_start + 5000]
    assert "Between-tool-call" in section or "between tool calls" in section.lower()
    assert "not mid-tool" in section or "mid-call" in section.lower()


def test_charter_echoes_signal_not_content():
    """F7 stdout-discipline corollary at the charter surface — the wake
    is a signal, not a content channel."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = text.find("### Wake Mechanism")
    section = text[section_start:section_start + 5000]
    assert "Signal" in section or "signal" in section
