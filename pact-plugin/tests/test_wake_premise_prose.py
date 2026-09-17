"""
Absence pin: no agent-loaded instruction file says that a teammate is never
woken by its own background completion.

A teammate running in its own process is woken by its own completion notice;
an in-process teammate is not. The instruction surfaces scope the not-woken
statement to in-process teammates. Each phrase below stated it for every
teammate, so none may return to a file an agent loads.

Matching collapses whitespace, so a phrase wrapped across prose lines is still
found.
"""

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AGENT_LOADED_DIRS = ("agents", "skills", "protocols", "commands")

UNIVERSAL_NOT_WOKEN_PHRASES = (
    "a teammate's background-task notification is wake-on-read",
    "no tool will wake you",
    "no way to bring **yourself** back",
)

SCOPED_SURFACES = {
    "agents/pact-auditor.md",
    "agents/pact-orchestrator.md",
    "skills/pact-agent-teams/SKILL.md",
    "skills/pact-teachback/SKILL.md",
}


def _agent_loaded_markdown():
    return sorted(
        path
        for directory in AGENT_LOADED_DIRS
        for path in (PLUGIN_ROOT / directory).rglob("*.md")
    )


def test_the_sweep_reads_every_scoped_surface():
    """Denominator guard: a sweep that missed the edited files would pass the
    absence arm without reading them."""
    names = {p.relative_to(PLUGIN_ROOT).as_posix() for p in _agent_loaded_markdown()}
    missing = SCOPED_SURFACES - names
    assert not missing, f"the sweep did not read {sorted(missing)}"


@pytest.mark.parametrize("phrase", UNIVERSAL_NOT_WOKEN_PHRASES)
def test_no_agent_loaded_prose_says_every_teammate_is_never_woken(phrase):
    hits = [
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in _agent_loaded_markdown()
        if phrase in " ".join(p.read_text(encoding="utf-8").split())
    ]
    assert hits == [], (
        f"{hits} say {phrase!r}, which states that no teammate is woken by its "
        "own background completion. A teammate in its own process is woken; "
        "scope the statement to an in-process teammate."
    )
