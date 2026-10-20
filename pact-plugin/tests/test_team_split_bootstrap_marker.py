"""
After a split, bootstrap completes once the secretary joins the live team.

The bootstrap marker is written only when the session's team has a secretary.
After a resume the secretary joins the team the platform is using, while the
recorded team is the pre-resume one, whose config is gone. When the census
could not pick the live team (another project's session was live beside it),
the marker writer looked for the secretary in the dead team and the marker
was never written, so the bootstrap gate kept denying for the rest of the
session.

bootstrap_marker_writer.main() runs a lead prompt in process over the real
path derivations (tests/fixtures/team_split.py).

Counter-tests, measured by source-only revert in a scratch copy (cardinality
of this file's 2-case run): restoring the census's count-before-corroborate
order -> 1 failed (the secretary is looked for in the dead team, no marker).
"""

import io
import json
from unittest.mock import patch

from fixtures.role_frames import captured_lead_userpromptsubmit_qualified
from fixtures.team_split import (
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    reset_context_module,
    saved_team_name,
    split_world,
)

_VERSION = "9.9.9"


def _world(tmp_path, monkeypatch, **kw):
    journal = ("task_metadata_snapshot", "session_end",
               {"type": "session_start", "session_id": OLD_SID,
                "project_dir": str(tmp_path / "project"), "source": "resume"})
    world = split_world(tmp_path, monkeypatch, journal=journal,
                        live_members=("secretary",), claude_md=True, **kw)
    (world.plugin_root / ".claude-plugin").mkdir(exist_ok=True)
    (world.plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": _VERSION}), encoding="utf-8")
    return world


def _prompt_then_marker(monkeypatch, world):
    import bootstrap_marker_writer
    from bootstrap_gate import is_marker_set
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir))
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        try:
            bootstrap_marker_writer.main()
        except SystemExit:
            pass
    reset_context_module()
    return is_marker_set(world.session_dir)


def test_the_marker_is_written_once_the_secretary_joins_the_live_team(
        tmp_path, monkeypatch):
    """The secretary is a member of the live team only; another project's
    session is live too. The first lead prompt realigns the record and
    writes the marker."""
    world = _world(tmp_path, monkeypatch, foreign="foreign-cwd")
    assert _prompt_then_marker(monkeypatch, world) is True
    assert saved_team_name(world) == NEW_TEAM


def test_no_marker_while_the_team_cannot_be_decided(tmp_path, monkeypatch):
    """Control: two live teams match, so the record stays on the dead team
    and no marker is written. The cell above passes only because the census
    picked the live team."""
    world = _world(tmp_path, monkeypatch, second_corroborated=True)
    assert _prompt_then_marker(monkeypatch, world) is False
    assert saved_team_name(world) == OLD_TEAM
