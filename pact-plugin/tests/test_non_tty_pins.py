"""
Location: pact-plugin/tests/test_non_tty_pins.py
Summary: ENUMERATE-AND-ASSERT prose pins for the non-TTY harness support wave
(#1658/#1309): every `Agent(` dispatch site carries BOTH additions
(`run_in_background: false` + the `ToolSearch` select preamble), every
documented raw-read path site cites the realigned team-name source, the
collapsed wait wording and relay-fallback paragraphs are present where W2
landed them, and the pre-decided NO-TOUCH surfaces (the persona's Bash
watcher flag) survive byte-identical. The sites are enumerated DYNAMICALLY
from the tree, so a newly added command with an unamended dispatch site fails
the per-site assertion without anyone remembering to extend a list; the
counted baselines make additions deliberate (a census drift is a failure that
names itself, never a silent pass).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument).

Census provenance (measured at HEAD c86d89b0, 2026-10-21, re-derived
mechanically and reconciled against the W2 HANDOFF census):
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

# The two additions every dispatch site must carry. The ToolSearch literal
# carries the backslash-escaped quotes the templates embed inside the
# Agent(...) prompt parameter — matched as a fixed string, never a regex.
FOREGROUND_FLAG = "run_in_background: false"
TOOLSEARCH_LOAD = 'ToolSearch \\"select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage\\"'

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


class TestAgentDispatchSitesCarryBothAdditions:
    """Every dispatch site requests the foreground (tools fixed at spawn) AND
    teaches the deferred-tool load. Presence/shape pins, not line-%."""

    def _collect(self):
        per_file = {}
        for path in _AGENT_SURFACE_FILES:
            sites = _agent_dispatch_sites(path.read_text(encoding="utf-8"))
            if sites:
                per_file[_rel(path)] = sites
        return per_file

    def test_census_baseline_is_13_sites(self):
        per_file = self._collect()
        assert {k: len(v) for k, v in per_file.items()} == EXPECTED_AGENT_SITES

    def test_every_site_carries_the_foreground_flag(self):
        for rel, sites in self._collect().items():
            for lineno, block in sites:
                assert FOREGROUND_FLAG in block, f"{rel}:{lineno} lacks the flag"

    def test_every_site_teaches_the_toolsearch_load(self):
        for rel, sites in self._collect().items():
            for lineno, block in sites:
                assert TOOLSEARCH_LOAD in block, f"{rel}:{lineno} lacks the load line"


class TestNoTouchSurfacesSurvive:
    """Pre-decided dispositions (spec §6) pinned affirmatively."""

    def test_persona_bash_watcher_flag_survives_exactly_once(self):
        persona = (_PLUGIN_ROOT / "agents" / "pact-orchestrator.md").read_text(
            encoding="utf-8")
        assert persona.count("run_in_background: true") == 1
        # And it is NOT inside any dispatch site (different semantic).
        for _, block in _agent_dispatch_sites(persona):
            assert "run_in_background: true" not in block

    def test_hard_rule_branches_on_the_dispatch_flag_first(self):
        persona = (_PLUGIN_ROOT / "agents" / "pact-orchestrator.md").read_text(
            encoding="utf-8")
        assert (
            "First check the dispatch you made: if it lacked "
            "`run_in_background: false`" in persona
        )


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


class TestRelayFallbackParagraph:
    """Relay is documented (skill + protocol), tied to the residual case, and
    is not built as hook logic."""

    def test_agent_teams_relay_paragraph_present(self):
        skill = (_PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md").read_text(
            encoding="utf-8")
        assert "Relay fallback (Task tools absent despite a foreground dispatch)." in skill
        assert "sanctioned fallback" in skill

    def test_completion_authority_relay_paragraph_present(self):
        proto = (_PLUGIN_ROOT / "protocols" / "pact-completion-authority.md").read_text(
            encoding="utf-8")
        assert "Relay fallback (Task tools absent despite a foreground dispatch)." in proto
        assert "No hook suppression logic and no relay marker exist" in proto
