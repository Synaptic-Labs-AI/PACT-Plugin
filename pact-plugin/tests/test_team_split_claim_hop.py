"""
A resumption claim survives sessions that did nothing.

A claim (a pause or a refresh) is read from the previous session's journal.
When that previous session was a launch that did nothing (opened and closed
with no turn), the claim behind it was lost. Each session now records its
predecessor (``session_start.prev_session_dir``), and the claim reader walks
back past predecessors that did nothing, at most three sessions, stopping at
the first session that did anything. "Did nothing" means every event in its
journal is one a zero-turn launch writes; any other event stops the walk,
including the consumption a resumed claim leaves in the session that resumed
it, so a spent claim is never found again.

session_init.main() runs a new session's startup in process over the real
path derivations; the journals of the earlier sessions are real files, and the
new session's marker is read back from its own journal.

Counter-tests, measured by source-only revert of each mechanism in a scratch
copy (cardinality of this file's 14-case run): reading only the immediate
predecessor (no walk) -> 3 failed; counting a consumption event as idle -> 1
failed; the decision wording's work-event list in place of the idle-launch
list -> 2 failed (commit, consolidation); raising the hop cap -> 1 failed;
skipping the pact-sessions validation of a recorded predecessor -> 1 failed;
not recording the predecessor, or recording the session itself, in either
session_start writer -> 1 failed each; following the FIRST recorded
predecessor instead of the last -> 1 failed. Dropping the cycle guard is
EQUIVALENT (all pass): the hop cap already bounds the walk and every session
in a cycle did nothing, so the cycle cell is a no-raise control.
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


def _sid(n):
    return f"{n:08x}-1111-4222-8333-444455556666"


class Chain:
    """Sessions S0..Sn of one project. S0 holds an unspent refresh claim and
    ended; each later session records its predecessor."""

    def __init__(self, tmp_path, monkeypatch):
        import shared.pact_context as ctx_module
        self.ctx = ctx_module
        self.project = tmp_path / "project"
        (self.project / ".claude").mkdir(parents=True, exist_ok=True)
        self.plugin = seed_plugin(tmp_path / "plugin")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.project))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(self.plugin))
        monkeypatch.chdir(self.project)
        self.claim_ts = datetime.now(timezone.utc).isoformat()

    def dir(self, n):
        return self.ctx._build_session_path(
            self.ctx.project_slug(str(self.project)), _sid(n))

    def start_event(self, n, prev=None):
        event = {"type": "session_start", "session_id": _sid(n),
                 "project_dir": str(self.project), "source": "startup"}
        if prev is not None:
            event["prev_session_dir"] = str(prev)
        return event

    def claim_session(self, n=0):
        write_journal(self.dir(n), [
            self.start_event(n),
            "task_metadata_snapshot",
            {"type": "session_refreshed", "ts": self.claim_ts,
             "consolidation_completed": True, "halt_active": False,
             "feature_subject": _FEATURE},
            "session_end",
        ])

    def idle_session(self, n, prev, extra=()):
        """A launch that did nothing: the four event types a zero-turn launch
        writes, plus ``extra``."""
        events = [self.start_event(n, prev=prev), "session_resumption_surfaced"]
        events += list(extra)
        events += ["session_end", "cleanup_summary"]
        write_journal(self.dir(n), events)

    def start(self, n, previous):
        """Session ``n`` starts with the CLAUDE.md block naming ``previous``.
        Returns (additionalContext, events in session n's journal)."""
        write_session_block(_sid(previous), OLD_TEAM, self.dir(previous),
                            self.plugin)
        frame = captured_compaction_lead_sessionstart()
        frame.update(session_id=_sid(n), cwd=str(self.project), source="startup")
        reset_context_module()
        with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            with pytest.raises(SystemExit) as exc:
                session_init.main()
        assert exc.value.code == 0
        reset_context_module()
        context = json.loads(out.getvalue()).get("hookSpecificOutput", {}).get(
            "additionalContext", "")
        return context, read_journal(self.dir(n))


def _surfaced(events):
    return [e for e in events if e.get("type") == "session_resumption_surfaced"]


def test_a_claim_behind_a_session_that_did_nothing_surfaces(tmp_path,
                                                            monkeypatch):
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    chain.idle_session(1, prev=chain.dir(0))
    context, events = chain.start(2, previous=1)
    assert _FEATURE in context
    assert len(_surfaced(events)) == 1


def test_the_session_that_resumed_a_claim_stops_the_walk(tmp_path, monkeypatch):
    """The session after the claim resumed it and recorded the consumption
    there: the claim is spent and must not surface again."""
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    chain.idle_session(1, prev=chain.dir(0), extra=[
        {"type": "session_refresh_consumed", "refresh_ts": chain.claim_ts}])
    context, events = chain.start(2, previous=1)
    assert _FEATURE not in context
    assert _surfaced(events) == []


@pytest.mark.parametrize("work_event", [
    {"type": "commit", "sha": "abc1234", "message": "m", "phase": "CODE"},
    "session_consolidated",
    "task_metadata_snapshot",
], ids=["commit", "consolidation", "task-event"])
def test_a_session_that_did_anything_stops_the_walk(tmp_path, monkeypatch,
                                                    work_event):
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    chain.idle_session(1, prev=chain.dir(0), extra=[work_event])
    context, events = chain.start(2, previous=1)
    assert _FEATURE not in context
    assert _surfaced(events) == []


@pytest.mark.parametrize("idle_count,found", [(3, True), (4, False)])
def test_the_walk_passes_at_most_three_sessions_that_did_nothing(
        tmp_path, monkeypatch, idle_count, found):
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    for n in range(1, idle_count + 1):
        chain.idle_session(n, prev=chain.dir(n - 1))
    context, events = chain.start(idle_count + 1, previous=idle_count)
    assert (_FEATURE in context) is found
    assert len(_surfaced(events)) == (1 if found else 0)


def test_a_predecessor_cycle_ends_the_walk(tmp_path, monkeypatch):
    """S1 names S2 and S2 names S1: the walk ends without raising and nothing
    surfaces. A control: every session the cycle visits did nothing, so no
    variant of the walk that stays within the hop cap can surface a claim."""
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    chain.idle_session(1, prev=chain.dir(2))
    chain.idle_session(2, prev=chain.dir(1))
    context, events = chain.start(3, previous=2)
    assert _FEATURE not in context
    assert _surfaced(events) == []


def test_a_predecessor_outside_the_sessions_tree_is_never_read(tmp_path,
                                                                monkeypatch):
    """A recorded predecessor outside pact-sessions holding a claim: the walk
    refuses it and nothing surfaces."""
    chain = Chain(tmp_path, monkeypatch)
    outside = tmp_path / "outside" / _sid(0)
    write_journal(outside, [
        chain.start_event(0), "task_metadata_snapshot",
        {"type": "session_refreshed", "ts": chain.claim_ts,
         "consolidation_completed": True, "halt_active": False,
         "feature_subject": _FEATURE},
        "session_end"])
    chain.idle_session(1, prev=outside)
    context, events = chain.start(2, previous=1)
    assert _FEATURE not in context
    assert _surfaced(events) == []


def _own_start(events):
    starts = [e for e in events if e.get("type") == "session_start"]
    assert starts, "precondition: the session journaled its start"
    return starts[-1]


def test_a_new_session_records_its_predecessor(tmp_path, monkeypatch):
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    _, events = chain.start(1, previous=0)
    assert _own_start(events).get("prev_session_dir") == str(chain.dir(0))


def test_a_session_never_records_itself_as_its_predecessor(tmp_path,
                                                           monkeypatch):
    """A same-session resume: the CLAUDE.md block names this session's own
    dir, so no predecessor is recorded."""
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    _, events = chain.start(0, previous=0)
    assert "prev_session_dir" not in _own_start(events)


@pytest.mark.parametrize("previous,recorded", [(0, True), (1, False)],
                         ids=["another-session", "own-session"])
def test_the_prompt_gate_records_the_predecessor_the_same_way(
        tmp_path, monkeypatch, previous, recorded):
    """The prompt gate also writes a session_start (when SessionStart did
    not). It records the predecessor only when that is another session."""
    import bootstrap_prompt_gate
    chain = Chain(tmp_path, monkeypatch)
    chain.claim_session(0)
    own = chain.dir(1)
    own.mkdir(parents=True, exist_ok=True)
    reset_context_module()
    assert bootstrap_prompt_gate._append_session_start(
        {"session_id": _sid(1)}, str(own), str(chain.dir(previous)))
    reset_context_module()
    start = _own_start(read_journal(own))
    assert ("prev_session_dir" in start) is recorded
    if recorded:
        assert start["prev_session_dir"] == str(chain.dir(0))


def test_the_walk_follows_the_last_recorded_predecessor(tmp_path, monkeypatch):
    """An idle session started twice and recorded two predecessors: first A,
    which did work and holds no claim, then B, which holds the unspent claim.
    The walk follows the later record, B, and the claim surfaces."""
    chain = Chain(tmp_path, monkeypatch)
    write_journal(chain.dir(0), [chain.start_event(0), "task_metadata_snapshot",
                                 "session_end"])
    chain.claim_session(1)
    write_journal(chain.dir(2), [
        chain.start_event(2, prev=chain.dir(0)),
        chain.start_event(2, prev=chain.dir(1)),
        "session_resumption_surfaced", "session_end", "cleanup_summary"])
    context, events = chain.start(3, previous=2)
    assert _FEATURE in context
    assert len(_surfaced(events)) == 1
