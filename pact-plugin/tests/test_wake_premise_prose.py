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

import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _shared_tracked_files

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


def _tracked_markdown(directory):
    """Tracked markdown under one agent-loaded directory.

    The population is the git index, so an untracked scratch note cannot
    redden an absence pin; an install that genuinely omits the directory skips
    by name instead of erroring.
    """
    try:
        files = _shared_tracked_files(
            PLUGIN_ROOT.parent, f"{PLUGIN_ROOT.name}/{directory}")
    except AssertionError:
        if PLUGIN_ROOT.name == "pact-plugin":
            raise
        pytest.skip(
            f"installed layout {PLUGIN_ROOT} ships no {directory!r}; "
            "the wake-premise pins are about the dev checkout")
    return [path for path in files if path.suffix == ".md"]


def _agent_loaded_markdown():
    return sorted(
        path
        for directory in AGENT_LOADED_DIRS
        for path in _tracked_markdown(directory)
    )


def test_the_sweep_reads_every_scoped_surface():
    """Denominator guard: a sweep that missed the edited files would pass the
    absence arm without reading them."""
    names = {p.relative_to(PLUGIN_ROOT).as_posix() for p in _agent_loaded_markdown()}
    missing = SCOPED_SURFACES - names
    assert not missing, f"the sweep did not read {sorted(missing)}"


def test_untracked_prose_cannot_join_the_absence_sweep(tmp_path, monkeypatch):
    """The sweep reads the index; an untracked note cannot redden it.

    Reverting ``_tracked_markdown`` to ``(PLUGIN_ROOT / directory).rglob("*.md")``
    keeps the sweep green; this arm drives the absence pin and reddens naming
    the untracked file. The seeded scoped surfaces are the positive control.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    for relative in ("agents/pact-auditor.md", "agents/pact-orchestrator.md",
                     "skills/pact-agent-teams/SKILL.md",
                     "skills/pact-teachback/SKILL.md",
                     "protocols/pact-protocols.md", "commands/orchestrate.md"):
        path = plugin / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("clean surface\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)

    assert SCOPED_SURFACES <= {
        p.relative_to(PLUGIN_ROOT).as_posix() for p in _agent_loaded_markdown()
    }, "positive control: the seeded scoped surfaces are missing from the sweep"
    probe = plugin / "commands" / "zzprobe.md"
    probe.write_text("A note that no tool will wake you.\n", encoding="utf-8")

    test_no_agent_loaded_prose_says_every_teammate_is_never_woken(
        "no tool will wake you")


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
