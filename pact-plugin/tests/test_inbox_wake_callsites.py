"""
Cross-file consistency invariants for the inbox-wake feature.

Pin the Skill("PACT:inbox-wake") + Teardown invocation in wrap-up.md
(the only command body that retains the callsite post-cycle-2; runs
after all tasks complete, so count is already 0 and the callsite is
both correct and useful as a hook-silent-fail safety net).

Negative assertions for imPACT.md and pause.md: these were removed in
F3-followup (Option C) because their unconditional Teardown destroyed
still-needed Monitors when other teammates remained active. The
PostToolUse 1→0 hook handles the lifecycle automatically. The
negative assertions here are a regression guard against future
re-introduction.

Charter §Wake Mechanism cross-ref pins ensure SKILL.md and charter
remain aligned on lead-only scope, F7 between-tool-calls scope, and
signal-not-content corollary.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = ROOT / "commands"
PROTOCOLS_DIR = ROOT / "protocols"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- Teardown invocations in command bodies ----------

def test_wrap_up_invokes_inbox_wake_teardown():
    """wrap-up.md retains the Teardown callsite — it fires after all
    tasks are completed, so the count is already 0 and the callsite is
    both correct and useful as a hook-silent-fail safety net."""
    text = _read(COMMANDS_DIR / "wrap-up.md")
    assert 'Skill("PACT:inbox-wake")' in text, (
        "wrap-up.md missing Skill slug invocation"
    )
    assert "Teardown" in text, (
        "wrap-up.md missing Teardown operation reference"
    )


def test_impact_md_does_not_invoke_inbox_wake():
    """F3-followup (Option C): imPACT.md's force-termination Teardown
    was removed because it destroyed still-needed Monitors when other
    teammates remained active. The PostToolUse 1→0 hook handles the
    lifecycle automatically. This negative assertion is a regression
    guard against future re-introduction."""
    text = _read(COMMANDS_DIR / "imPACT.md")
    assert 'Skill("PACT:inbox-wake")' not in text, (
        "imPACT.md must NOT invoke Skill(\"PACT:inbox-wake\") — "
        "unconditional Teardown destroys Monitors needed by remaining "
        "teammates. PostToolUse 1→0 hook handles lifecycle automatically."
    )


def test_pause_md_does_not_invoke_inbox_wake():
    """F3-followup (Option C): pause.md's pre-shutdown Teardown was
    removed for the same reason as imPACT.md — destroying Monitors
    needed by remaining teammates regresses the failure mode #591 was
    designed to fix."""
    text = _read(COMMANDS_DIR / "pause.md")
    assert 'Skill("PACT:inbox-wake")' not in text, (
        "pause.md must NOT invoke Skill(\"PACT:inbox-wake\") — "
        "unconditional Teardown destroys Monitors needed by remaining "
        "teammates. PostToolUse 1→0 hook handles lifecycle automatically."
    )


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
