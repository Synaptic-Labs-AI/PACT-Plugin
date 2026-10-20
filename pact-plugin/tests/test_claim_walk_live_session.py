"""
The resume-claim walk passes only sessions that did nothing and have ended.

shared.session_resume._claim_dir walks from the previous session back past
sessions that did nothing, to reach a pause or refresh claim behind them. A
concurrent session that has only just launched has done nothing yet, and the
claim behind it is that session's to surface, so the walk passes another
session's dir only once it has ended. This session's own dir needs no end:
on a resume it is the dir the walk starts from.

A journal without a session_start records no launch, so it is not a session
that did nothing, and the walk stops there.

Counter-tests, measured by source-only mutation of session_resume.py
(failures in this file's run):
  drop the ended check -> 3 failed
  drop the own-dir exemption -> 1 failed
  drop the session_start requirement -> 1 failed
"""

from datetime import datetime, timezone

from fixtures.team_split import seed_plugin, write_journal

from shared import session_resume


def _sid(n):
    return f"{n:08x}-2222-4333-8444-555566667777"


class Sessions:
    def __init__(self, tmp_path, monkeypatch):
        import shared.pact_context as ctx_module
        from pathlib import Path
        self.ctx = ctx_module
        self.project = tmp_path / "project"
        (self.project / ".claude").mkdir(parents=True, exist_ok=True)
        seed_plugin(tmp_path / "plugin")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.project))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self.ts = datetime.now(timezone.utc).isoformat()

    def dir(self, n):
        return str(self.ctx._build_session_path(
            self.ctx.project_slug(str(self.project)), _sid(n)))

    def start(self, n, prev=None):
        event = {"type": "session_start", "session_id": _sid(n),
                 "project_dir": str(self.project), "source": "startup"}
        if prev is not None:
            event["prev_session_dir"] = self.dir(prev)
        return event

    def claim(self, n):
        from pathlib import Path
        write_journal(Path(self.dir(n)), [
            self.start(n), "task_metadata_snapshot",
            {"type": "session_refreshed", "ts": self.ts,
             "consolidation_completed": True, "halt_active": False,
             "feature_subject": "the feature"},
            "session_end",
        ])

    def write(self, n, events):
        from pathlib import Path
        write_journal(Path(self.dir(n)), events)


def test_a_live_session_that_has_only_launched_stops_the_walk(
        tmp_path, monkeypatch):
    s = Sessions(tmp_path, monkeypatch)
    s.claim(0)
    s.write(1, [s.start(1, prev=0), "session_resumption_surfaced"])
    assert session_resume._claim_dir(s.dir(1)) == s.dir(1)


def test_an_ended_session_that_did_nothing_is_walked_past(tmp_path, monkeypatch):
    """Control for the cell above: the same session, ended."""
    s = Sessions(tmp_path, monkeypatch)
    s.claim(0)
    s.write(1, [s.start(1, prev=0), "session_resumption_surfaced",
                "session_end", "cleanup_summary"])
    assert session_resume._claim_dir(s.dir(1)) == s.dir(0)


def test_this_sessions_own_dir_is_walked_past_without_an_end(
        tmp_path, monkeypatch):
    """A resumed session that did nothing walks to its predecessor's claim
    even though its own journal has just started again."""
    s = Sessions(tmp_path, monkeypatch)
    s.claim(0)
    s.write(1, [s.start(1, prev=0), "session_end", s.start(1)])
    assert session_resume._claim_dir(s.dir(1), own_session_dir=s.dir(1)) == s.dir(0)
    assert session_resume._claim_dir(s.dir(1)) == s.dir(1)


def test_a_live_concurrent_session_never_surfaces_the_claim_behind_it(
        tmp_path, monkeypatch):
    """Through the public resolver: a second lead whose previous session is a
    live, just-launched session sees no claim."""
    s = Sessions(tmp_path, monkeypatch)
    s.claim(0)
    s.write(1, [s.start(1, prev=0), "session_resumption_surfaced"])
    assert session_resume.check_resume_state(s.dir(1), launch=True) is None
    s.write(1, [s.start(1, prev=0), "session_resumption_surfaced",
                "session_end", "cleanup_summary"])
    assert session_resume.check_resume_state(s.dir(1), launch=True)


def test_a_journal_without_a_session_start_stops_the_walk(tmp_path, monkeypatch):
    s = Sessions(tmp_path, monkeypatch)
    s.claim(0)
    s.write(1, [{"type": "session_start", "session_id": _sid(1),
                 "project_dir": str(s.project), "source": "startup",
                 "prev_session_dir": s.dir(0)}])
    s.write(2, ["session_end", "cleanup_summary"])
    # The second session never recorded its launch, so it names no
    # predecessor and is not a session that did nothing.
    assert session_resume._session_did_nothing(s.dir(2)) is False
    assert session_resume._session_did_nothing(s.dir(1)) is True
    assert session_resume._claim_dir(s.dir(2)) == s.dir(2)
