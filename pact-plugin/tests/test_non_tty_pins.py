"""
Location: pact-plugin/tests/test_non_tty_pins.py
Summary: ENUMERATE-AND-ASSERT pins for the current Agent Team dispatch
contract (#1673) and preserved non-TTY evidence (#1658/#1309). Every current
Agent(...) dispatch site is named, team-scoped, ToolSearch-enabled, and free
of legacy Agent execution-mode parameters. Ordered A–H recovery guidance is
pinned by branch-specific markers inside stable structural regions. Exactly
one operational legacy note lives in the plugin README; the root README and
current probe arms carry none. Dated runbook evidence and the persona's Bash
watcher flag remain intact. Sites are enumerated DYNAMICALLY from the tree,
and the counted baselines make additions deliberate rather than silently
passing an empty enumeration.
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument).

Census provenance (measured at HEAD 169c6907, 2026-10-29, re-derived
mechanically against the approved plan and PREPARE census):
  Agent dispatch sites: 13 total — agents/pact-orchestrator.md 1,
    commands/bootstrap.md 1, commands/comPACT.md 2, commands/orchestrate.md 6,
    commands/peer-review.md 1, commands/plan-mode.md 1, commands/rePACT.md 1.
  Doc-path sites: 13 tasks/{team_name} sites across 8 non-SSOT files
    (persona 2, orchestrate 1, teachback SKILL 1, agent-teams SKILL 3,
    state-recovery 1, scope-phases 1, completion-authority 3, scope-contract 1)
    + 6 SSOT mirror sites in protocols/pact-protocols.md + 2 teams/{team_name}
    sites (persona, bootstrap). W2's census recorded "13 sites (9 files)";
    the site counts agree (13/6/2), the file count differs by one (8 measured
    here vs 9 recorded there) — flagged in the TEST-phase HANDOFF, pinned at
    the measured number.
"""

from __future__ import annotations

import re
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _TESTS_DIR.parent

# LLM-loaded surfaces the census enumerates (agents, commands, skills,
# protocols). The SSOT (pact-protocols.md) is enumerated SEPARATELY from its
# extracts so per-class counts stay legible.
_AGENT_SURFACE_FILES = [
    _PLUGIN_ROOT / "agents" / "pact-orchestrator.md",
    *sorted((_PLUGIN_ROOT / "commands").glob("*.md")),
]
_DOC_SURFACE_FILES = [
    *_AGENT_SURFACE_FILES,
    *sorted((_PLUGIN_ROOT / "skills").glob("*/SKILL.md")),
    *sorted((_PLUGIN_ROOT / "protocols").glob("*.md")),
]
_SSOT = _PLUGIN_ROOT / "protocols" / "pact-protocols.md"
_REPO_ROOT = _PLUGIN_ROOT.parent
_PERSONA = _PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
_PLUGIN_README = _PLUGIN_ROOT / "README.md"
_ROOT_README = _REPO_ROOT / "README.md"
_AGENT_TEAMS_SKILL = _PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"
_COMPLETION_AUTHORITY = _PLUGIN_ROOT / "protocols" / "pact-completion-authority.md"
_RUNBOOK = _TESTS_DIR / "runbooks" / "non-tty-harness-probe.md"

# The ToolSearch literal carries backslash-escaped quotes inside Agent(...)
# prompt parameters; documentation regions use the unescaped form.
TOOLSEARCH_LOAD = 'ToolSearch \\"select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage\\"'
TOOLSEARCH_LITERAL = 'ToolSearch "select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage"'
LEGACY_WORKAROUND = "run_in_background: false"
_AGENT_PARAMETER = re.compile(r"(?<![\w-])(?:run_in_background|background)\s*[:=]")
_AGENT_BACKGROUND_PARAMETER = re.compile(r"(?<![\w-])background\s*[:=]")
_LEGACY_NOTE_HEADING = "### Legacy non-TTY compatibility note"

# Measured per-file baseline of Agent dispatch sites (13 total).
EXPECTED_AGENT_SITES = {
    "agents/pact-orchestrator.md": 1,
    "commands/bootstrap.md": 1,
    "commands/comPACT.md": 2,
    "commands/orchestrate.md": 6,
    "commands/peer-review.md": 1,
    "commands/plan-mode.md": 1,
    "commands/rePACT.md": 1,
}

# Each branch has a stable heading and a positive control from its own contract,
# not a whole-paragraph snapshot shared by every documentation surface.
_RECOVERY_BRANCHES = (
    ("A. **Load deferred tools.**", TOOLSEARCH_LITERAL),
    ("B. **Check the lead session.**", "start the required new session"),
    ("C. **Compare the call with the live Agent schema.**", "non-interactive `-p`"),
    ("D. **Confirm named-teammate creation and addressability.**", "ordinary subagent"),
    ("E. **Apply display-mode inheritance.**", "`--settings`"),
    ("F. **Use the legacy-only branch only after A–E.**", "single operational legacy note"),
    ("G. **Relay residual structured content.**", "`SendMessage` reaches `team-lead` or `main`"),
    ("H. **Stop on messaging failure.**", "team-formation/messaging"),
)

# Actor-local controls parallel to _RECOVERY_BRANCHES. Each pair is
# (required markers, role-confusion markers forbidden in that branch).
_RECOVERY_ROLE_CONTROLS = (
    ((
        "The actor missing Task tools runs",
        "the teammate reports the exact missing tools and load result",
        "then stops implementation work and idles",
        "The lead's own successful ToolSearch proves only the lead's tool surface, never the teammate's.",
    ), ()),
    ((
        "The lead checks its own four Task tools",
        "the lead fixes the session opt-in/settings and must start the required new session",
        "the teammate does not restart the session.",
    ), ("the teammate must restart the session", "the teammate restarts the session")),
    ((
        "The lead compares its dispatch with the live schema",
        "The lead corrects `Task(...)`",
        "the teammate does not rewrite the lead's call.",
    ), ("the teammate rewrites the lead's call", "the teammate must rewrite the lead's call")),
    ((
        "The lead uses platform spawn, roster, and messaging evidence",
        "the lead corrects team enablement/registration instead.",
    ), ()),
    ((
        "The lead checks the actual teammate display mode",
        "the lead checks `--tools`/deny rules and environment forwarding through `--settings`",
        "then corrects launch configuration before replacement; the teammate does not change it.",
    ), ("the teammate changes launch configuration", "the teammate must change launch configuration")),
    ((
        "only the lead follows the single operational legacy note",
        "the teammate never applies legacy dispatch.",
    ), ("the teammate applies legacy dispatch", "the teammate must apply legacy dispatch")),
    (("the teammate relays the teachback/HANDOFF content and the lead writes metadata.",), ()),
    ((
        "the teammate cannot relay and stops/idles.",
        "The lead reports the team-formation/messaging blocker",
    ), ()),
)

_ROOT_ROLE_MARKERS = (
    "the teammate may load deferred tools",
    "it reports exact evidence and stops implementation work while the lead takes over",
    "The lead must not treat its own successful tool load as the teammate's",
    "The lead must check the lead session",
    "compare the dispatch with the live Agent schema",
    "confirm registration and addressability",
    "then apply display-mode inheritance",
    "Only the lead may use the guarded legacy branch",
    "the teammate relays the structured content for the lead to write",
    "the teammate stops and the lead reports the team-formation/messaging blocker",
)

_STALE_SCHEMA_CLAIMS = (
    "Prose says a parameter is not available, parameter list shows it: PASS IT.",
    "Dispatch template and parameter list disagree: that is a FINDING. Report it. Do not select one.",
)

_STALE_FIRST_SPAWN_CLAIMS = (
    "the dispatch was malformed",
    "corrects the dispatch shape, and re-spawns",
)

# The realigned-name citation every documented raw-read path site must carry
# within +/-3 lines (same line, or the sentence that explains the segment).
CITATION = "pact-session-context.json"
_CITATION_WINDOW = 3

# Sites deliberately covered-adjacent (W2 disposition, lead-accepted): the
# Read-Trigger Precondition's ordering point re-embeds the same cat command
# whose citation sits 11 lines above in the same section (completion-authority
# extract + its byte-mirrored SSOT region). Keyed by 0-based per-file
# occurrence index of tasks/{team_name}, so line drift does not rot the pin.
_TASKS_PATH = re.compile(r"tasks/\{team_name\}")
_TEAMS_PATH = re.compile(r"teams/\{team_name\}")
_COVERED_ADJACENT = {
    ("protocols/pact-completion-authority.md", 2),
    ("protocols/pact-protocols.md", 4),
}


def _rel(path: Path) -> str:
    return str(path.relative_to(_PLUGIN_ROOT))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    """Return a non-empty structural region; missing delimiters fail loudly."""
    assert text.count(start) == 1, f"expected one {start!r} delimiter"
    after_start = text.split(start, 1)[1]
    assert end in after_start, f"missing {end!r} after {start!r}"
    section = after_start.split(end, 1)[0]
    assert section.strip(), f"empty region between {start!r} and {end!r}"
    return section


def _current_recovery_regions() -> dict[str, str]:
    return {
        "persona": _section(
            _read(_PERSONA),
            "#### First-spawn verification (HARD-RULE)",
            "#### Hook WARN signals are STOP signals",
        ),
        "plugin README": _section(
            _read(_PLUGIN_README),
            "### Current Agent Team dispatch recovery",
            _LEGACY_NOTE_HEADING,
        ),
        "root README": _section(
            _read(_ROOT_README),
            "#### If specialist agents will not spawn",
            "### Optional dependencies",
        ),
        "agent-teams skill": _section(
            _read(_AGENT_TEAMS_SKILL), "## Dispatch recovery", "## Progress Reporting"
        ),
        "completion authority": _section(
            _read(_COMPLETION_AUTHORITY), "### Dispatch recovery", "### Acceptance —"
        ),
        "protocol SSOT": _section(
            _read(_SSOT), "### Dispatch recovery", "### Acceptance —"
        ),
        "runbook diagnostics": _section(
            _read(_RUNBOOK),
            "## §3 Current Agent Team dispatch diagnostics",
            "## §4 Historical evidence",
        ),
    }


def _recovery_branch_slices(region: str) -> dict[str, str]:
    positions = []
    for heading, _ in _RECOVERY_BRANCHES:
        assert region.count(heading) == 1, f"recovery region lacks {heading}"
        positions.append(region.index(heading))
    assert positions == sorted(positions), "recovery branches are out of order"
    slices = {}
    for index, (heading, _) in enumerate(_RECOVERY_BRANCHES):
        start = positions[index] + len(heading)
        end = positions[index + 1] if index + 1 < len(positions) else len(region)
        slices[heading] = region[start:end]
    return slices


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _root_first_spawn_summary(text: str) -> str:
    return _section(
        text,
        "**First-spawn HARD-STOP verification**",
        "**Structural verification discipline**",
    )


def _runbook_arms() -> dict[str, str]:
    text = _read(_RUNBOOK)
    return {
        "ARM 1": _section(text, "## §1 ARM 1", "## §2 ARM 2"),
        "ARM 2": _section(
            text, "## §2 ARM 2", "## §3 Current Agent Team dispatch diagnostics"
        ),
    }


def _agent_dispatch_sites(text: str) -> list[tuple[int, str]]:
    """Enumerate Agent(...) DISPATCH FORM sites: a line that is exactly
    `Agent(` opening a paren-balanced block, or an inline `Agent(name="..."`
    call. Prose mentions (`Agent()`, `Agent(subagent_type=...)`, the elided
    `Agent(name=...,` shape in the MANDATORY blockquote, the HARD-RULE block)
    match neither shape and are excluded by construction — the inline matcher
    requires a QUOTED argument value, which only a real call site carries."""
    sites: list[tuple[int, str]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "Agent(":
            depth = line.count("(") - line.count(")")
            start = i
            while depth > 0 and i + 1 < len(lines):
                i += 1
                depth += lines[i].count("(") - lines[i].count(")")
            sites.append((start + 1, "\n".join(lines[start : i + 1])))
        elif 'Agent(name="' in line:
            sites.append((i + 1, line))
        i += 1
    return sites


def _sites_with_citation(lines: list[str], pattern: re.Pattern) -> list[tuple[int, bool]]:
    """Per occurrence of the path pattern: (1-based line, cited-within-window)."""
    out: list[tuple[int, bool]] = []
    for n, line in enumerate(lines, start=1):
        if not pattern.search(line):
            continue
        lo, hi = max(0, n - 1 - _CITATION_WINDOW), min(len(lines), n + _CITATION_WINDOW)
        out.append((n, any(CITATION in lines[k] for k in range(lo, hi))))
    return out


class TestCurrentAgentDispatchContract:
    """Current Agent Team calls are named, flagless, and ToolSearch-enabled."""

    def _collect(self):
        per_file = {}
        for path in _AGENT_SURFACE_FILES:
            sites = _agent_dispatch_sites(_read(path))
            if sites:
                per_file[_rel(path)] = sites
        return per_file

    def test_census_baseline_is_13_sites(self):
        per_file = self._collect()
        assert {k: len(v) for k, v in per_file.items()} == EXPECTED_AGENT_SITES

    def test_inline_and_multiline_sites_are_both_scanned(self):
        blocks = [
            block for sites in self._collect().values() for _, block in sites
        ]
        assert any("\n" not in block for block in blocks)
        assert any("\n" in block for block in blocks)

    def test_every_site_omits_legacy_agent_parameters(self):
        for rel, sites in self._collect().items():
            for lineno, block in sites:
                assert not _AGENT_PARAMETER.search(block), (
                    f"{rel}:{lineno} carries an Agent execution-mode parameter"
                )

    def test_every_site_is_named_and_team_scoped(self):
        for rel, sites in self._collect().items():
            for lineno, block in sites:
                assert re.search(r"(?<![\w-])name=", block), (
                    f"{rel}:{lineno} lacks an explicit name parameter"
                )
                assert "team_name=" in block, f"{rel}:{lineno} lacks team_name"

    def test_every_site_teaches_the_toolsearch_load(self):
        for rel, sites in self._collect().items():
            for lineno, block in sites:
                assert TOOLSEARCH_LOAD in block, f"{rel}:{lineno} lacks the load line"

    def test_mandatory_agent_template_is_named_team_scoped_and_flagless(self):
        region = _section(
            _read(_PERSONA), "## 11. Agent Teams Dispatch", "**Teachback-Gated Dispatch**"
        )
        assert "Specialists are spawned as teammates via `Agent(" in region
        for marker in (
            "name=...",
            'team_name="{team_name}"',
            "subagent_type=...",
            "description=...",
            "prompt=...",
        ):
            assert marker in region, f"MANDATORY Agent template lacks {marker!r}"
        assert not _AGENT_PARAMETER.search(region), (
            "MANDATORY Agent template carries an execution-mode parameter"
        )

    def test_schema_disagreement_uses_contract_not_mechanical_key_selection(self):
        region = _section(
            _read(_PERSONA), "#### Which signal governs a tool call", "#### First-spawn verification"
        )
        for marker in (
            "parameter is expressible, not required",
            "never pass it merely because it appears in the schema",
            "FINDING to diagnose, not a prohibition on choosing",
            "follow the current named flagless contract",
            "ordered legacy branch only when every guard matches",
            "schema presence alone selects neither",
        ):
            assert marker in region, f"schema-selection contract lacks {marker!r}"
        for stale in _STALE_SCHEMA_CLAIMS:
            assert stale not in region, f"schema-selection contract retains {stale!r}"


class TestNoTouchSurfacesSurvive:
    """Pre-decided dispositions (spec §6) pinned affirmatively."""

    def test_persona_bash_watcher_flag_survives_exactly_once(self):
        persona = _read(_PERSONA)
        assert persona.count("run_in_background: true") == 1
        # And it is NOT inside any dispatch site (different semantic).
        for _, block in _agent_dispatch_sites(persona):
            assert "run_in_background: true" not in block


class TestCurrentRecoveryContract:
    """Every recovery surface carries the same ordered, diagnosable tree."""

    def test_branch_markers_are_present_and_ordered(self):
        for name, region in _current_recovery_regions().items():
            branches = _recovery_branch_slices(region)
            for heading, control in _RECOVERY_BRANCHES:
                assert control in branches[heading], (
                    f"{name} {heading} lacks its own control {control!r}"
                )

    def test_recovery_roles_are_actor_local(self):
        for name, region in _current_recovery_regions().items():
            branches = _recovery_branch_slices(region)
            for (heading, _), (required, forbidden) in zip(
                _RECOVERY_BRANCHES, _RECOVERY_ROLE_CONTROLS
            ):
                branch = _collapsed(branches[heading])
                for marker in required:
                    assert marker in branch, f"{name} {heading} lacks role marker {marker!r}"
                for marker in forbidden:
                    assert marker not in branch, f"{name} {heading} has role confusion {marker!r}"

    def test_root_first_spawn_summary_uses_ordered_recovery(self):
        summary = _root_first_spawn_summary(_read(_ROOT_README))
        ordered = (
            "load deferred tools",
            "check the lead session",
            "live Agent schema",
            "named, interactive teammate",
            "registration and addressability",
            "display-mode inheritance",
            "guarded legacy branch",
            "`SendMessage` reaches the lead",
            "messaging also fails",
        )
        positions = []
        for marker in ordered:
            assert marker in summary, f"first-spawn summary lacks {marker!r}"
            positions.append(summary.index(marker))
        assert positions == sorted(positions), "first-spawn recovery is out of order"
        for stale in _STALE_FIRST_SPAWN_CLAIMS:
            assert stale not in summary, f"first-spawn summary retains {stale!r}"

    def test_root_first_spawn_summary_assigns_actor_roles(self):
        summary = _collapsed(_root_first_spawn_summary(_read(_ROOT_README)))
        positions = []
        for marker in _ROOT_ROLE_MARKERS:
            assert marker in summary, f"first-spawn summary lacks role marker {marker!r}"
            positions.append(summary.index(marker))
        assert positions == sorted(positions), "first-spawn actor roles are out of order"
        for _, forbidden in _RECOVERY_ROLE_CONTROLS:
            for marker in forbidden:
                assert marker not in summary, f"first-spawn summary has role confusion {marker!r}"

    def test_current_regions_exclude_legacy_keys_and_flag_respawn(self):
        for name, region in _current_recovery_regions().items():
            assert "run_in_background" not in region, f"{name} retains a legacy key"
            assert not _AGENT_BACKGROUND_PARAMETER.search(region), (
                f"{name} permits an Agent background replacement"
            )
            assert "with the flag" not in region.lower(), (
                f"{name} retains flag-first respawn guidance"
            )


class TestLegacyBoundaries:
    """The only operational exception is ordered, scoped, and non-current."""

    def test_plugin_legacy_note_has_the_ordered_guard(self):
        note = _section(_read(_PLUGIN_README), _LEGACY_NOTE_HEADING, "## What You Get")
        ordered = (
            "1. Confirm that the runtime is an older non-TTY environment",
            "2. Complete current recovery branches A–E.",
            "3. Confirm that the live Agent schema exposes `run_in_background`.",
            f"4. Only then, the measured #1658 `{LEGACY_WORKAROUND}` workaround MAY be used.",
            "5. Verify the result with a post-spawn `TaskList` check",
        )
        positions = []
        for marker in ordered:
            assert marker in note, f"legacy guard lacks {marker!r}"
            positions.append(note.index(marker))
        assert positions == sorted(positions)
        assert "full teachback → work → HANDOFF → completion ceremony" in note
        assert "expressibility, not necessity" in note
        assert "not current dispatch guidance" in note
        assert "neither satisfies this branch nor proves lifecycle support or justifies respawn" in note
        assert note.count(LEGACY_WORKAROUND) == 1
        assert not _AGENT_BACKGROUND_PARAMETER.search(note)

    def test_plugin_readme_outside_legacy_note_has_no_agent_key(self):
        text = _read(_PLUGIN_README)
        _section(text, _LEGACY_NOTE_HEADING, "## What You Get")
        current = text.split(_LEGACY_NOTE_HEADING, 1)[0] + text.split("## What You Get", 1)[1]
        assert "run_in_background" not in current
        assert not _AGENT_BACKGROUND_PARAMETER.search(current)

    def test_root_readme_has_no_legacy_region_or_agent_key(self):
        root = _read(_ROOT_README)
        assert _LEGACY_NOTE_HEADING not in root
        assert "expressibility, not necessity" not in root
        assert "run_in_background" not in root
        assert not _AGENT_BACKGROUND_PARAMETER.search(root)

    def test_runbook_history_is_dated_and_not_current_guidance(self):
        history = _section(
            _read(_RUNBOOK), "## §4 Historical evidence", "## §5 Desktop arm"
        )
        for marker in (
            "#1658",
            "2.1.303",
            LEGACY_WORKAROUND,
            "not current dispatch guidance",
        ):
            assert marker in history, f"historical region lacks {marker!r}"
        assert not _AGENT_BACKGROUND_PARAMETER.search(history)


class TestCurrentProbeArms:
    """Current ARM slices are flagless and retain their positive controls."""

    def test_current_arms_omit_legacy_parameters_and_keep_controls(self):
        for name, arm in _runbook_arms().items():
            assert "run_in_background" not in arm, f"{name} retains a legacy key"
            assert not _AGENT_BACKGROUND_PARAMETER.search(arm), (
                f"{name} permits an Agent background replacement"
            )
            for marker in (
                TOOLSEARCH_LITERAL,
                "TaskList",
                "`name`",
                "`team_name`",
                "live Agent schema",
                "claude --version",
                "matched positive control",
            ):
                assert marker in arm, f"{name} lacks control {marker!r}"

    def test_cloud_arm_keeps_the_full_ceremony(self):
        arm = _runbook_arms()["ARM 2"]
        for marker in ("full teachback", "`metadata.handoff`", "lead accepts and completes"):
            assert marker in arm, f"ARM 2 lacks ceremony marker {marker!r}"


class TestDocPathSitesCiteTheRealignedName:
    """Each documented raw-read path names its team-name source: the session
    context, not the prose-minted short name."""

    def test_non_ssot_tasks_path_sites(self):
        total = 0
        for path in _DOC_SURFACE_FILES:
            if path == _SSOT:
                continue
            lines = path.read_text(encoding="utf-8").split("\n")
            for idx, (lineno, cited) in enumerate(
                _sites_with_citation(lines, _TASKS_PATH)
            ):
                total += 1
                assert cited or (_rel(path), idx) in _COVERED_ADJACENT, (
                    f"{_rel(path)}:{lineno} (tasks-site #{idx}) cites no name source"
                )
        assert total == 13

    def test_ssot_mirror_sites(self):
        lines = _SSOT.read_text(encoding="utf-8").split("\n")
        sites = _sites_with_citation(lines, _TASKS_PATH)
        assert len(sites) == 6
        for idx, (lineno, cited) in enumerate(sites):
            assert cited or (_rel(_SSOT), idx) in _COVERED_ADJACENT, (
                f"{_rel(_SSOT)}:{lineno} (mirror-site #{idx}) cites no name source"
            )

    def test_teams_path_sites(self):
        # Keyed by per-file occurrence INDEX, not line number (the same
        # discipline as _COVERED_ADJACENT), so prose drift above a site does
        # not rot the pin — only a site's appearance or citation can.
        found = []
        for path in _DOC_SURFACE_FILES:
            lines = path.read_text(encoding="utf-8").split("\n")
            for idx, (lineno, cited) in enumerate(
                _sites_with_citation(lines, _TEAMS_PATH)
            ):
                found.append((_rel(path), idx))
                assert cited, (
                    f"{_rel(path)}:{lineno} (teams-site #{idx}) cites no "
                    "name source"
                )
        assert found == [
            ("agents/pact-orchestrator.md", 0),
            ("commands/bootstrap.md", 0),
        ]


class TestCollapsedWaitWording:
    """Harness-agnostic idle semantics: ending the turn IS idling, the
    main-fallback covers the unreachable lead, the relay is the sanctioned
    escape for the one case prevention cannot reach."""

    def test_every_command_task_a_tail_ends_the_turn(self):
        tails = []
        for path in sorted((_PLUGIN_ROOT / "commands").glob("*.md")):
            for n, line in enumerate(
                path.read_text(encoding="utf-8").split("\n"), start=1
            ):
                if "intentional_wait{reason=awaiting_lead_completion" in line:
                    tails.append((_rel(path), n, "End the turn (idle)." in line))
        assert len(tails) == 6
        for rel, n, ends in tails:
            assert ends, f"{rel}:{n} Task A tail does not end the turn"

    def test_skills_carry_the_collapsed_form_three_times(self):
        hits = 0
        for path in sorted((_PLUGIN_ROOT / "skills").glob("*/SKILL.md")):
            hits += path.read_text(encoding="utf-8").count("End the turn (idle)")
        assert hits == 3  # agent-teams canonical bullet + rejection tail, teachback post-store

    def test_agent_teams_canonical_bullet_has_full_collapsed_form(self):
        skill = (_PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md").read_text(
            encoding="utf-8")
        bullet = next(
            line for line in skill.split("\n") if "Awaiting lead completion?" in line
        )
        assert "if `team-lead` is unreachable, send it to `main`" in bullet
        assert "END THE TURN (idle)" in bullet
        assert "ending the turn IS idling" in bullet

    def test_teachback_notify_note_carries_main_fallback(self):
        skill = (_PLUGIN_ROOT / "skills" / "pact-teachback" / "SKILL.md").read_text(
            encoding="utf-8")
        assert "If `team-lead` is unreachable, send the notify to `main`." in skill
