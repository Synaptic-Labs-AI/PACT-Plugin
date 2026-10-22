"""
Location: pact-plugin/tests/test_non_tty_parity.py
Summary: P7 parity — ONE team-name value across every surface that names the
team, on the config-less UUID-keyed substrate (#1658's observed split: the
context said the full UUID while the SessionStart prose, the CLAUDE.md
``- Team:`` line and the journal said ``session-<id8>``, and raw-read paths
built from the prose missed). Three surfaces, one name, no resolver patched:
  1. the realignment resolver adopts the live UUID store (branch-2 witness),
  2. the CLAUDE.md ``- Team:`` line written from that resolved name matches,
  3. the journal's ``dispatch_decision`` carries the resolver-resolved name in
     ``resolved_team`` — next to the LITERAL ``Agent()`` param when the two
     diverge, which is the diagnostic's entire purpose.
Composes with (does not duplicate) test_team_split_lead_session.py, which
covers the #1659 resume/clear realignment world; this file's cells are the
non-TTY substrate ones.
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument).
"""

from __future__ import annotations

import shutil

import pytest

import dispatch_gate
from shared.pact_context import _resolve_aligned_team_name
from shared.session_journal import read_events
from shared.session_resume import update_session_info
from shared.task_utils import resolve_task_dir

from test_missed_wake_scan_integration import (  # noqa: F401 (fixture re-export)
    live_task_env,
)

SHORT_NAME = "session-1a2b3c4d"
FULL_UUID = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"


@pytest.fixture
def non_tty_env(live_task_env, tmp_path, monkeypatch):
    """The #1658 substrate: config-less UUID-keyed stores, persisted context
    still naming the short name, and a real project dir for the CLAUDE.md
    writer (update_session_info keys on CLAUDE_PROJECT_DIR)."""
    live_task_env.configure(team_name=SHORT_NAME, session_id=FULL_UUID,
                            project_dir=str(tmp_path / "project"))
    for stale_dir in (
        live_task_env.tasks_root / SHORT_NAME,
        live_task_env.home / ".claude" / "teams" / SHORT_NAME,
    ):
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    uuid_team = live_task_env.home / ".claude" / "teams" / FULL_UUID
    (uuid_team / "inboxes").mkdir(parents=True, exist_ok=True)
    # The UUID-keyed TASK store the resolver probes for (branch-2's witness is
    # the teams dir; resolve_task_dir's existence probe is the tasks dir).
    live_task_env.dir_for(FULL_UUID).mkdir(parents=True, exist_ok=True)
    return live_task_env


class TestOneNameAcrossSurfaces:
    def test_realignment_resolver_adopts_the_uuid_store(self, non_tty_env):
        """Surface 1: branch-2's config-less full-UUID witness — the team dir
        with inboxes but no config.json, keyed by the session UUID — is THIS
        session's store, so the aligned name is the UUID, not the persisted
        short name."""
        aligned = _resolve_aligned_team_name(FULL_UUID)
        assert aligned == FULL_UUID, (
            "the realignment must adopt the config-less UUID-keyed store; "
            f"got {aligned!r}"
        )

    def test_claude_md_team_line_matches_the_resolved_name(self, non_tty_env):
        """Surface 2: the CLAUDE.md ``- Team:`` line written from the resolved
        name carries the same value the resolver returns — the surface a
        resumed lead reads first."""
        aligned = _resolve_aligned_team_name(FULL_UUID)
        assert resolve_task_dir() is not None, "precondition: store resolves"
        update_session_info(FULL_UUID, aligned, session_dir="/sess",
                            plugin_root="/root")
        # The writer's preferred target is $project/.claude/CLAUDE.md
        # (resolve_project_claude_md_path creates at the preferred path when
        # neither location exists).
        claude_md = (non_tty_env.home / "project" / ".claude" / "CLAUDE.md")
        assert claude_md.is_file(), "the writer must produce the project CLAUDE.md"
        team_lines = [
            line for line in claude_md.read_text(encoding="utf-8").split("\n")
            if line.startswith("- Team:")
        ]
        assert len(team_lines) == 1
        assert FULL_UUID in team_lines[0]

    def test_journal_resolved_team_matches_and_divergence_is_visible(
            self, non_tty_env):
        """Surface 3: the dispatch_decision journal event carries the
        RESOLVED store name in ``resolved_team``; when the literal ``Agent()``
        param names the stale short name, BOTH are journaled — the divergence
        is the diagnostic's reason to exist, so it must be observable, not
        silently corrected away."""
        resolved = resolve_task_dir()
        assert resolved is not None and resolved.name == FULL_UUID
        dispatch_gate._journal_decision(
            "allow", None, None,
            {"team_name": SHORT_NAME, "prompt": "spawn", "name": "preparer"},
        )
        events = read_events("dispatch_decision")
        assert len(events) == 1
        ev = events[0]
        assert ev["team_name"] == SHORT_NAME, "the literal param is kept verbatim"
        assert ev["resolved_team"] == FULL_UUID, (
            "the resolved name must sit beside the literal — the #1658 name "
            "split made visible post-mortem"
        )

    def test_all_three_surfaces_agree_on_one_value(self, non_tty_env):
        """The P7 observable end-to-end: realignment, CLAUDE.md line input and
        the journal diagnostic all reduce to ONE team-name value on this
        substrate."""
        aligned = _resolve_aligned_team_name(FULL_UUID)
        resolved = resolve_task_dir()
        assert resolved is not None
        assert {aligned, resolved.name} == {FULL_UUID}
