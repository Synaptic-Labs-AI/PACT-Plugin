"""
Cross-file consistency invariants for the cron-based pending-scan mechanism.

The pending-scan mechanism replaces the prior Monitor inbox-wake feature
with three skill files:
- start-pending-scan (Arm role) at pact-plugin/commands/start-pending-scan.md
- scan-pending-tasks (Fire body) at pact-plugin/commands/scan-pending-tasks.md
- stop-pending-scan (Teardown role) at pact-plugin/commands/stop-pending-scan.md

Pin the Skill("PACT:stop-pending-scan") invocation in wrap-up.md (the only
command body that retains the callsite; runs after all tasks complete, so
count is already 0 and the callsite is both correct and useful as a
hook-silent-fail safety net).

Negative assertions for imPACT.md and pause.md: their callsites were
removed because their unconditional Teardown destroyed still-needed
scan crons when other teammates remained active. The PostToolUse 1→0
hook handles the lifecycle automatically. The negative assertions here
forbid all three new slugs (start/scan/stop-pending-scan) as
defense-in-depth against future re-introduction.

Charter cross-ref pins ensure command bodies and charter §Cron-Fire
Mechanism + §Scan Discipline sections remain aligned on lead-only
scope, between-tool-call scope, and signal-not-content corollary.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = ROOT / "commands"
PROTOCOLS_DIR = ROOT / "protocols"

_FORBIDDEN_SLUGS = (
    'Skill("PACT:start-pending-scan")',
    'Skill("PACT:stop-pending-scan")',
    'Skill("PACT:scan-pending-tasks")',
)

_LEGACY_SLUGS = (
    'Skill("PACT:inbox-wake")',
    'Skill("PACT:watch-inbox")',
    'Skill("PACT:unwatch-inbox")',
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- Teardown invocation in wrap-up.md ----------

def test_wrap_up_invokes_stop_pending_scan():
    """wrap-up.md retains the stop-pending-scan callsite — it fires
    after all tasks are completed, so the count is already 0 and the
    callsite is both correct and useful as a hook-silent-fail safety
    net."""
    text = _read(COMMANDS_DIR / "wrap-up.md")
    assert 'Skill("PACT:stop-pending-scan")' in text, (
        "wrap-up.md missing stop-pending-scan slug invocation"
    )


# ---------- Negative assertions for imPACT.md and pause.md ----------

def test_impact_md_does_not_invoke_any_pending_scan_slug():
    """imPACT.md's force-termination Teardown was removed because it
    destroyed still-needed scan crons when other teammates remained
    active. The PostToolUse 1→0 hook handles the lifecycle
    automatically. Negative assertion forbids all three pending-scan
    slugs as defense-in-depth against future re-introduction."""
    text = _read(COMMANDS_DIR / "imPACT.md")
    for slug in _FORBIDDEN_SLUGS:
        assert slug not in text, (
            f"imPACT.md must NOT invoke {slug} — unconditional callsite "
            f"destroys/re-arms scan crons needed by remaining teammates. "
            f"PostToolUse hook handles lifecycle automatically."
        )


def test_pause_md_does_not_invoke_any_pending_scan_slug():
    """pause.md's pre-shutdown callsite was removed for the same reason
    as imPACT.md. Defense-in-depth — forbid all three slugs."""
    text = _read(COMMANDS_DIR / "pause.md")
    for slug in _FORBIDDEN_SLUGS:
        assert slug not in text, (
            f"pause.md must NOT invoke {slug} — unconditional callsite "
            f"destroys/re-arms scan crons needed by remaining teammates."
        )


# ---------- Legacy slug absence (defense-in-depth) ----------

def test_no_legacy_inbox_wake_slugs_in_commands_dir():
    """The legacy Monitor inbox-wake slug family is fully retired.
    Any reintroduction is a regression — pin defense-in-depth across
    the entire commands/ directory."""
    for md_file in COMMANDS_DIR.glob("*.md"):
        text = _read(md_file)
        for slug in _LEGACY_SLUGS:
            assert slug not in text, (
                f"{md_file.name} contains legacy slug {slug} — the "
                f"Monitor inbox-wake mechanism is fully retired under "
                f"the cron-based pending-scan replacement."
            )


# ---------- Charter cross-reference ----------

def _find_cron_charter_section(text: str) -> int:
    """Locate the new charter section (either §Cron-Fire Mechanism or
    §Scan Discipline). The charter restructure splits the old §Wake
    Mechanism section; either header is acceptable as the anchor."""
    idx = text.find("### Cron-Fire Mechanism")
    if idx < 0:
        idx = text.find("### Scan Discipline")
    return idx


def test_charter_has_cron_fire_mechanism_section():
    """Charter §Wake Mechanism is restructured into §Cron-Fire
    Mechanism + §Scan Discipline."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    assert "## Part I — Message Delivery Mechanics" in text
    assert _find_cron_charter_section(text) >= 0, (
        "Charter missing cron-fire section anchor (expected either "
        "'### Cron-Fire Mechanism' or '### Scan Discipline')"
    )


def test_charter_links_to_command_trio_via_relative_paths():
    """Slug-link form (relative-path markdown link), NOT @~/ pattern.
    Charter cross-ref points at the new command trio, not the legacy
    Monitor command pair."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = _find_cron_charter_section(text)
    assert section_start >= 0
    section = text[section_start:section_start + 5000]
    # Charter must reference at least one of the three new commands.
    assert (
        "../commands/start-pending-scan.md" in section
        or "../commands/scan-pending-tasks.md" in section
        or "../commands/stop-pending-scan.md" in section
    )
    # And must NOT use the deprecated @~/ pattern, nor link to the
    # legacy command files that no longer exist.
    assert "@~/" not in section
    assert "../commands/watch-inbox.md" not in section
    assert "../commands/unwatch-inbox.md" not in section


def test_charter_echoes_lead_only_scope():
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = _find_cron_charter_section(text)
    assert section_start >= 0
    section = text[section_start:section_start + 5000]
    # Lead-only narrowing must be explicit in the charter rule list.
    assert "Lead-only" in section or "lead-only" in section.lower()


def test_charter_echoes_between_tool_calls_not_mid_tool():
    """Cross-file consistency: charter must carry the same between-
    tool-call scope claim as the scan-pending-tasks.md command body."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = _find_cron_charter_section(text)
    assert section_start >= 0
    section = text[section_start:section_start + 5000]
    assert "Between-tool-call" in section or "between tool calls" in section.lower()
    assert "not mid-tool" in section or "mid-call" in section.lower()


def test_charter_echoes_signal_not_content():
    """Cron-fire signal-not-content corollary at the charter surface —
    the cron fire is a wake signal, not a content channel; the action
    body reads filesystem state for the canonical artifacts."""
    text = _read(PROTOCOLS_DIR / "pact-communication-charter.md")
    section_start = _find_cron_charter_section(text)
    assert section_start >= 0
    section = text[section_start:section_start + 5000]
    assert "Signal" in section or "signal" in section
