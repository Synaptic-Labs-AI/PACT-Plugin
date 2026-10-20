"""
A same-session resume of a session that did nothing reaches the claim behind it.

The claim walk starts at the directory the CLAUDE.md session block names. On a
resume of the same session that is this session's own directory, which by the
time the claim is read already holds this launch's session_start. When the
session did nothing else (it was opened and closed without a turn, then
resumed), every event in its journal is one a zero-turn launch writes, so the
walk passes it and reaches the predecessor it recorded on its first launch.
A claim waiting there surfaces. When the session did work, the walk stops at
it.

session_init.main() runs a resume in process over the real path derivations;
the earlier session's journal is a real file.

Counter-tests, measured by source-only revert in a scratch copy (cardinality
of this file's 2-case run): reading only the named directory (no walk) -> 1
failed (the idle-resume cell surfaces nothing).
"""

import io
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fixtures.role_frames import captured_compaction_lead_sessionstart
from fixtures.team_split import (
    OLD_TEAM,
    read_journal,
    reset_context_module,
    seed_plugin,
    write_journal,
    write_session_block,
)

import session_init

_FEATURE = "the refreshed feature"
_PRIOR = "00000000-1111-4222-8333-444455556666"
_OWN = "00000001-1111-4222-8333-444455556666"


def _setup(tmp_path, monkeypatch, own_extra=()):
    import shared.pact_context as ctx
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    plugin = seed_plugin(tmp_path / "plugin")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.chdir(project)
    slug = ctx.project_slug(str(project))
    prior, own = ctx._build_session_path(slug, _PRIOR), ctx._build_session_path(slug, _OWN)
    write_journal(prior, [
        {"type": "session_start", "session_id": _PRIOR,
         "project_dir": str(project), "source": "startup"},
        "task_metadata_snapshot",
        {"type": "session_refreshed", "ts": datetime.now(timezone.utc).isoformat(),
         "consolidation_completed": True, "halt_active": False,
         "feature_subject": _FEATURE},
        "session_end"])
    write_journal(own, [
        {"type": "session_start", "session_id": _OWN,
         "project_dir": str(project), "source": "startup",
         "prev_session_dir": str(prior)},
        "session_resumption_surfaced", *own_extra, "session_end",
        "cleanup_summary"])
    write_session_block(_OWN, OLD_TEAM, own, plugin)
    return project, own


def _resume(project):
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=_OWN, cwd=str(project), source="resume")
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit):
            session_init.main()
    reset_context_module()
    return json.loads(out.getvalue()).get("hookSpecificOutput", {}).get(
        "additionalContext", "")


def _surfaced(own):
    return [e for e in read_journal(own)
            if e.get("type") == "session_resumption_surfaced"]


def test_resuming_an_idle_session_surfaces_its_predecessors_claim(tmp_path,
                                                                  monkeypatch):
    project, own = _setup(tmp_path, monkeypatch)
    context = _resume(project)
    assert _FEATURE in context
    assert len(_surfaced(own)) == 2  # the first launch's, and this resume's


def test_resuming_a_session_that_did_work_stops_at_it(tmp_path, monkeypatch):
    """Control: the same session did work (a task event) before it was
    resumed, so the walk stops at it and the older claim stays behind."""
    project, own = _setup(tmp_path, monkeypatch,
                          own_extra=("task_metadata_snapshot",))
    context = _resume(project)
    assert _FEATURE not in context
    assert len(_surfaced(own)) == 1  # only the first launch's
