"""
L2 non-mocked integration test for the #926 dispatch-unblock keystone (C2).

Proves the dispatch gate resolves an owned task under a NON-DEFAULT
CLAUDE_CONFIG_DIR, and that the re-anchored symlink-escape defense still
rejects a safe-named team dir that escapes the relocated tasks base.

NON-MOCKED at the resolver seam (the whole point — a mocked seam is a
vacuous green): drives get_claude_config_dir() via the live process globals
(monkeypatch.setenv + Path.home redirect), never via DI injection.

ANTI-VACUITY: the task is written ONLY under $CLAUDE_CONFIG_DIR/tasks, and
Path.home() is redirected to a SEPARATE empty temp dir. So a source-revert of
the resolver call (back to Path.home()/".claude"/"tasks") reads the empty home
-> has_task_assigned returns False -> these tests FAIL BEHAVIORALLY (not
ImportError — no post-fix-only symbol is referenced here, only the production
has_task_assigned entrypoint).

Mode-independence: the resolver keys on $CLAUDE_CONFIG_DIR (env), NOT on
session topology, so the in-process and tmux teammateModes resolve identically;
one env-driven pair covers both. The comprehensive both-modes 3-layer suite is
the TEST phase's work — this is the keystone's self-proof.
"""
import json
import os
from pathlib import Path

import pytest

from shared.dispatch_helpers import has_task_assigned


def _write_task(tasks_dir: Path, team: str, task_id: str, owner: str, status: str) -> None:
    team_dir = tasks_dir / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, "owner": owner, "status": status}),
        encoding="utf-8",
    )


@pytest.fixture
def relocated_config(tmp_path, monkeypatch):
    """A non-default CLAUDE_CONFIG_DIR + an empty redirected home.

    The redirected (empty) home is the anti-vacuity lever: if the resolver
    call is reverted, the production code reads home/".claude"/"tasks" — which
    is empty here — and the positive assertion flips to False.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def test_has_task_assigned_resolves_under_relocated_config(relocated_config):
    # The bug: under a non-default CLAUDE_CONFIG_DIR the platform writes the
    # task here, but pre-fix hooks read ~/.claude and miss it (rule 8 -> False).
    team = "pact-relocated-team"
    owner = "devops-engineer"
    _write_task(relocated_config / "tasks", team, "5", owner, "in_progress")
    assert has_task_assigned(team, owner) is True


def test_pending_status_also_resolves(relocated_config):
    team = "pact-relocated-team"
    owner = "devops-engineer"
    _write_task(relocated_config / "tasks", team, "7", owner, "pending")
    assert has_task_assigned(team, owner) is True


def test_unowned_task_under_relocated_config_not_assigned(relocated_config):
    team = "pact-relocated-team"
    _write_task(relocated_config / "tasks", team, "5", "someone-else", "in_progress")
    assert has_task_assigned(team, "devops-engineer") is False


def test_symlink_escape_rejected_under_relocated_base(relocated_config, tmp_path):
    # A safe-NAMED team dir that is a symlink escaping the relocated tasks base
    # must be rejected by the re-anchored relative_to containment defense — the
    # ROOT moved, but the guard's resolve()+relative_to logic is byte-identical.
    team = "pact-escape-team"
    owner = "devops-engineer"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "9.json").write_text(
        json.dumps({"id": "9", "owner": owner, "status": "pending"}),
        encoding="utf-8",
    )
    tasks_dir = relocated_config / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, tasks_dir / team)  # team dir escapes the base
    assert has_task_assigned(team, owner) is False
