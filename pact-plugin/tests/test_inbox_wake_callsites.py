"""
Cross-file consistency invariants for the inbox-wake feature.

Cycle 4 migrated the inbox-wake skill into a command-pair:
- watch-inbox (Arm role) at pact-plugin/commands/watch-inbox.md
- unwatch-inbox (Teardown role) at pact-plugin/commands/unwatch-inbox.md

Pin the Skill("PACT:unwatch-inbox") invocation in wrap-up.md (the only
command body that retains the callsite post-cycle-2; runs after all
tasks complete, so count is already 0 and the callsite is both correct
and useful as a hook-silent-fail safety net).

Negative assertions for imPACT.md and pause.md: their callsites were
removed in F3-followup (Option C) because their unconditional Teardown
destroyed still-needed Monitors when other teammates remained active.
The PostToolUse 1→0 hook handles the lifecycle automatically. The
negative assertions here forbid all THREE slugs (legacy inbox-wake +
new watch-inbox + unwatch-inbox) as defense-in-depth against future
re-introduction.

Charter §Wake Mechanism cross-ref pins ensure command bodies and
charter remain aligned on lead-only scope, F7 between-tool-calls
scope, and signal-not-content corollary.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = ROOT / "commands"
PROTOCOLS_DIR = ROOT / "protocols"

_FORBIDDEN_SLUGS = (
    'Skill("PACT:inbox-wake")',
    'Skill("PACT:watch-inbox")',
    'Skill("PACT:unwatch-inbox")',
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- Teardown invocation in wrap-up.md ----------

def test_wrap_up_invokes_unwatch_inbox():
    """wrap-up.md retains the unwatch-inbox callsite — it fires after
    all tasks are completed, so the count is already 0 and the callsite
    is both correct and useful as a hook-silent-fail safety net."""
    text = _read(COMMANDS_DIR / "wrap-up.md")
    assert 'Skill("PACT:unwatch-inbox")' in text, (
        "wrap-up.md missing unwatch-inbox slug invocation"
    )


# ---------- Negative assertions for imPACT.md and pause.md ----------

def test_impact_md_does_not_invoke_any_inbox_slug():
    """F3-followup (Option C) + cycle 4: imPACT.md's force-termination
    Teardown was removed because it destroyed still-needed Monitors
    when other teammates remained active. The PostToolUse 1→0 hook
    handles the lifecycle automatically. Negative assertion forbids
    ALL three slugs (legacy inbox-wake + watch-inbox + unwatch-inbox)
    as defense-in-depth against future re-introduction."""
    text = _read(COMMANDS_DIR / "imPACT.md")
    for slug in _FORBIDDEN_SLUGS:
        assert slug not in text, (
            f"imPACT.md must NOT invoke {slug} — unconditional callsite "
            f"destroys/re-arms Monitors needed by remaining teammates. "
            f"PostToolUse hook handles lifecycle automatically."
        )


def test_pause_md_does_not_invoke_any_inbox_slug():
    """F3-followup (Option C) + cycle 4: pause.md's pre-shutdown
    callsite was removed for the same reason as imPACT.md.
    Defense-in-depth — forbid all three slugs."""
    text = _read(COMMANDS_DIR / "pause.md")
    for slug in _FORBIDDEN_SLUGS:
        assert slug not in text, (
            f"pause.md must NOT invoke {slug} — unconditional callsite "
            f"destroys/re-arms Monitors needed by remaining teammates."
        )


# ---------- Charter cross-reference ----------

def test_charter_has_wake_mechanism_section():
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    assert "## Part I — Message Delivery Mechanics" in text
    assert "### Wake Mechanism" in text


def test_charter_links_to_command_pair_via_relative_paths():
    """Slug-link form (relative-path markdown link), NOT @~/ pattern.
    Cycle 4: charter cross-ref now points at the command pair, not the
    legacy single skill body."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = text.find("### Wake Mechanism")
    assert section_start >= 0
    section = text[section_start:section_start + 5000]
    # Charter must reference both new commands (or at minimum the
    # primary watch-inbox command — robust fallback if charter author
    # links only the primary).
    assert (
        "../commands/watch-inbox.md" in section
        or "../commands/unwatch-inbox.md" in section
    )
    # And must NOT use the deprecated @~/ pattern, nor link to the
    # legacy skill body that no longer exists.
    assert "@~/" not in section
    assert "../skills/inbox-wake/SKILL.md" not in section


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
