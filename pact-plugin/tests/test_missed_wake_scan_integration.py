"""
Location: pact-plugin/tests/test_missed_wake_scan_integration.py
Summary: NON-MOCKED integration coverage for the get_task_list team-dir
         resolution fix — the one seam EVERY prior test stubbed, which let the
         missed-wake surfacer ship INERT through a fully-green unit suite. The
         sibling unit file (test_missed_wake_scan.py) pins the pure predicate /
         build / emit behavior with a mocked journal + synthetic task lists;
         THIS file drives the REAL get_team_name -> team-dir -> glob resolution
         end-to-end so a re-inert resolver can never again pass green.

Used by: the pact-plugin test suite (standing both-modes / inert-class merge gate).

================================ ANTI-MOCK INVARIANT ===========================
The tests here MUST NOT monkeypatch get_task_list / iter_team_task_jsons /
find_stale_missed_wakes. The real get_team_name -> team-dir -> glob resolution
IS the seam under test — mocking it reproduces the exact gap that shipped the
surfacer inert (a green suite over a dead feature). The ONLY test doubles
permitted are filesystem redirection (Path.home + the additive tasks_base_dir
seam) and the deterministic `since` offset. If a future edit stubs the
resolver here, it has re-opened the bug.

============================ NON-VACUITY (un-mock-then-revert) =================
The two disciplines are SEQUENTIAL, not redundant:
  1. UN-MOCK the seam (these tests already do) -> closes the integration-seam
     gap. (A revert on a still-MOCKED test catches NOTHING — that is the exact
     trap that shipped the surfacer inert through ~50 mocked tests.)
  2. THEN source-only revert the resolver fix and confirm the gate tests FAIL.

Procedure (run from the worktree pact-plugin/ dir):
    git checkout <fix-sha>^ -- hooks/shared/task_utils.py   # restore pre-fix resolver
    python -m pytest tests/test_missed_wake_scan_integration.py -k non_vacuity_gate
    #   EXPECTED: FAIL — pre-fix arg-less get_task_list() reads
    #   ~/.claude/tasks/{session_id}/ (absent under Agent Teams) -> None ->
    #   run_surface returns None / no journal event.
    git checkout <fix-sha> -- hooks/shared/task_utils.py     # restore the fix
    python -m pytest tests/test_missed_wake_scan_integration.py # all green again

Expected cardinality on the source-revert: the NON-VACUITY GATE SET
(TestRunSurfaceEndToEnd + TestForensicEmitRealJournal + the teammate_idle
smoke) FAILS; they drive the ARG-LESS get_task_list() via a Path.home redirect
and reference NO post-fix-only symbol, so the revert is a clean behavioral
failure (None), not a TypeError artifact. The tasks_base_dir-seam tests
(TestResolverMatrix, TestEmptyTeamDirInvariant, TestSoloBranchResolver) are
post-fix-coupled BY CONSTRUCTION (the param did not exist pre-fix) and are NOT
part of the revert set — the arg-less gate tests carry the non-vacuity proof.

assertion-vacuity = "right path, toothless check" -> caught by revert/mutation.
integration-seam gap (this case) = "real check, WRONG path — the mock bypassed
the broken wiring" -> caught by UN-MOCKING the seam, not by revert alone.
================================================================================
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import missed_wake_scan as mw  # noqa: E402
import teammate_idle  # noqa: E402
from shared import task_utils  # noqa: E402
from shared.session_journal import read_events  # noqa: E402
from fixtures.role_frames import (  # noqa: E402
    captured_lead_userpromptsubmit_qualified,
    captured_plain_userpromptsubmit,
    captured_teammate_sessionstart,
)

# A valid Agent-Teams team name (pact-<slug>; lowercased by get_team_name,
# hyphen-safe per is_safe_path_component — mirrors the real pact-<uuid8> shape).
TEAM = "pact-testteam"
# Models the in-process topology: the lead's own session_id (== leadSessionId).
LEAD_SID = "aaaaaaaa-1111-2222-3333-444444444444"
# Models the tmux topology: a teammate session_id that != leadSessionId.
OTHER_SID = "bbbbbbbb-5555-6666-7777-888888888888"
PROJECT_DIR = "/test/project"

STALE = 60   # minutes past -> beyond the 30-min staleness threshold
FRESH = 5    # minutes past -> below the threshold


def _since(minutes_ago: int) -> str:
    """ISO-8601 UTC `since` at a fixed offset from real now (positive = past)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _write_task(
    tasks_dir: Path,
    task_id: str = "42",
    owner: str = "test-engineer",
    subject: str = "do the thing",
    status: str = "in_progress",
    reason: str = "awaiting_lead_completion",
    minutes_ago: int = STALE,
    with_wait: bool = True,
) -> dict:
    """Write a REAL task JSON file into `tasks_dir` (created if absent). Mirrors
    the platform's task-file shape so the real resolver parses it verbatim."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta: dict = {}
    if with_wait:
        meta["intentional_wait"] = {
            "reason": reason,
            "expected_resolver": "lead",
            "since": _since(minutes_ago),
        }
    task = {
        "id": task_id,
        "owner": owner,
        "subject": subject,
        "status": status,
        "metadata": meta,
    }
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")
    return task


class _LiveEnv:
    """Handle returned by the live_task_env fixture. Exposes the redirected
    tasks root + a `configure` to (re)point the pact_context, with NO resolver
    mocked."""

    def __init__(self, home: Path, pact_context_factory):
        self.home = home
        self.tasks_root = home / ".claude" / "tasks"
        self._pact_context = pact_context_factory

    def configure(self, team_name: str = TEAM, session_id: str = LEAD_SID,
                  project_dir: str = PROJECT_DIR) -> None:
        """Point the real pact_context at this team/session. get_team_name() /
        get_session_id() resolve from here; the autouse reset clears the cache
        so a re-configure mid-test re-reads."""
        self._pact_context(team_name=team_name, session_id=session_id,
                           project_dir=project_dir)

    def dir_for(self, name: str) -> Path:
        return self.tasks_root / name


@pytest.fixture
def live_task_env(tmp_path, monkeypatch, pact_context):
    """Redirect Path.home -> a tmp home and expose a real-resolver task env.

    Path.home is the ONLY task-storage indirection production has (the arg-less
    get_task_list() / iter_team_task_jsons() resolve ~/.claude/tasks via
    Path.home, and the journal resolves ~/.claude/pact-sessions via Path.home),
    so redirecting it lets run_surface exercise the REAL resolver end-to-end
    with no stub. The conftest `pact_context` factory configures team_name /
    session_id / project_dir (patches _context_path directly, independent of
    Path.home). Defaults to a TEAM session; call .configure(...) to vary it.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = _LiveEnv(tmp_path, pact_context)
    env.configure()  # default team session
    return env


# ===========================================================================
# IT-1 — run_surface END-TO-END through the REAL team-dir resolver
#   NON-VACUITY GATE. Path.home redirect; arg-less get_task_list(); NO stub.
# ===========================================================================
class TestRunSurfaceEndToEnd:
    """The test that would have caught the inert surfacer: a real team-dir on
    disk -> run_surface drives the real get_team_name -> team-dir -> glob
    resolution and surfaces. FAILS against the pre-fix resolver (source-revert)
    because the arg-less get_task_list() reads the absent {session_id} dir."""

    def test_non_vacuity_gate_lead_stale_team_task_surfaces(self, live_task_env):
        _write_task(live_task_env.dir_for(TEAM), task_id="7", owner="architect",
                    subject="design X", minutes_ago=STALE)
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None, (
            "run_surface MUST surface a stale awaiting_lead_completion task that "
            "lives in the REAL team dir — None here is the inert-surfacer bug"
        )
        assert "missed-wake" in out.lower()
        assert "wake-SendMessage" in out, "must name the corrective action"
        assert "#7" in out and "architect" in out and "design X" in out

    def test_non_lead_frame_no_surface_even_with_stale_team_task(self, live_task_env):
        # is_lead gate holds end-to-end: a plain frame no-ops regardless of a
        # real stale team task (the resolver is reached only past the gate).
        _write_task(live_task_env.dir_for(TEAM), task_id="7")
        assert mw.run_surface(captured_plain_userpromptsubmit()) is None
        assert mw.run_surface(captured_teammate_sessionstart()) is None

    def test_fresh_team_task_below_threshold_no_surface(self, live_task_env):
        # Real resolver returns the task, but it is not yet stale -> no surface.
        _write_task(live_task_env.dir_for(TEAM), task_id="7", minutes_ago=FRESH)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None

    def test_no_team_tasks_no_surface(self, live_task_env):
        # Team dir exists but empty -> get_task_list None -> run_surface None.
        live_task_env.dir_for(TEAM).mkdir(parents=True, exist_ok=True)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None


# ===========================================================================
# IT-2 — forensic emit against the REAL session journal
#   NON-VACUITY GATE. Real append_event -> real read_events (no mock).
# ===========================================================================
class TestForensicEmitRealJournal:
    """run_surface writes a real `missed_wake` event to the real
    session-journal.jsonl (append_event mkdir -p's the session dir under the
    redirected home). Asserts exactly one event, with the real schema, read
    back via the real read_events. Pre-fix -> 0 events (resolver None)."""

    def test_non_vacuity_gate_emits_exactly_one_missed_wake_event(self, live_task_env):
        _write_task(live_task_env.dir_for(TEAM), task_id="7", owner="architect",
                    subject="design X", minutes_ago=STALE)
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None, "precondition: the surface fired"
        events = read_events("missed_wake")
        assert len(events) == 1, (
            "exactly one forensic missed_wake event must land in the REAL "
            "journal; got %d" % len(events)
        )
        ev = events[0]
        assert ev["type"] == "missed_wake"
        assert ev["task_id"] == "7" and ev["agent"] == "architect"
        assert ev["reason"] == "awaiting_lead_completion"
        assert ev["task_subject"] == "design X"

    def test_re_fire_while_stale_re_shows_but_no_second_event(self, live_task_env):
        # PERSISTENT-while-stale surface + once-per-(task,since) forensic dedup,
        # both driven through the REAL journal across two fires.
        _write_task(live_task_env.dir_for(TEAM), task_id="7", minutes_ago=STALE)
        out1 = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        out2 = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out1 is not None and out2 is not None, "surface re-shows every fire while stale"
        assert len(read_events("missed_wake")) == 1, (
            "the forensic emit is once-per-(task_id,since) — the 2nd fire's "
            "journal-read dedup suppresses a 2nd event"
        )

    def test_auto_clears_when_task_resolved(self, live_task_env):
        # Stale -> surfaces + 1 event; then resolve (status flips) -> re-scan of
        # the REAL dir finds nothing -> None, no new event. THE auto-clear.
        team_dir = live_task_env.dir_for(TEAM)
        _write_task(team_dir, task_id="7", minutes_ago=STALE)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is not None
        # Resolve the task in the real dir (overwrite the file, status completed).
        _write_task(team_dir, task_id="7", status="completed", with_wait=False)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None
        assert len(read_events("missed_wake")) == 1, "no new event after resolve"


# ===========================================================================
# IT-3 — 3-axis resolver matrix {mode} x {CLAUDE_CODE_TASK_LIST_ID}
#   Uses the additive tasks_base_dir seam at the RESOLVER level (§5.3).
# ===========================================================================
class TestResolverMatrix:
    """Design A (team-first additive): in a TEAM session team_name ALWAYS wins,
    so get_task_list returns the team dir's tasks regardless of session_id
    (mode-independence) AND regardless of CLAUDE_CODE_TASK_LIST_ID (it is
    consulted ONLY in the solo branch).

    Mask/unmask map (documented; the broken-code column is NOT executed — the
    tasks_base_dir param did not exist pre-fix):
        broken code fired ONLY at (CLAUDE_CODE_TASK_LIST_ID == TEAM) — every
        other cell read the absent {session_id} dir and returned None (inert).
        the FIX fires in ALL six team cells (team-first); TASK_LIST_ID is a
        no-op in a team session.
    """

    @pytest.mark.parametrize("session_id", [LEAD_SID, OTHER_SID],
                             ids=["in_process", "tmux"])
    @pytest.mark.parametrize("task_list_id", [None, TEAM, LEAD_SID],
                             ids=["TASK_LIST_ID_unset", "TASK_LIST_ID_eq_TEAM",
                                  "TASK_LIST_ID_eq_SID"])
    def test_team_session_always_resolves_team_dir(
        self, live_task_env, monkeypatch, session_id, task_list_id,
    ):
        live_task_env.configure(team_name=TEAM, session_id=session_id)
        # Real team task on disk.
        _write_task(live_task_env.dir_for(TEAM), task_id="7", minutes_ago=STALE)
        # A DECOY populated session_id dir — team-first must ignore it.
        _write_task(live_task_env.dir_for(session_id), task_id="999",
                    owner="decoy", minutes_ago=STALE)
        if task_list_id is None:
            monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", task_list_id)
        tasks = task_utils.get_task_list(tasks_base_dir=str(live_task_env.tasks_root))
        assert tasks is not None, "team session must resolve the team dir in every cell"
        ids = {t["id"] for t in tasks}
        assert ids == {"7"}, (
            "team-first wins: only the team dir's task; the decoy session_id "
            "task (#999) and TASK_LIST_ID=%r must NOT leak in. got %r"
            % (task_list_id, ids)
        )


# ===========================================================================
# IT-4 — empty-team-dir invariant (R4 correctness): NO fall-through
#   Uses the tasks_base_dir seam at the RESOLVER level.
# ===========================================================================
class TestEmptyTeamDirInvariant:
    """A team session (team_name truthy) with an empty/absent team dir returns
    None — it MUST NOT fall through to a populated foreign session_id /
    TASK_LIST_ID dir. The branch key is 'is this a team session?', not 'did the
    team dir have tasks?'. (Structurally guaranteed by the return INSIDE the
    `if team_name:` block; this formalizes it as a committed test.)"""

    def test_empty_team_dir_returns_none_not_foreign_session(self, live_task_env):
        # Team dir EXISTS but empty.
        live_task_env.dir_for(TEAM).mkdir(parents=True, exist_ok=True)
        # POPULATED foreign session_id dir that a fall-through would wrongly read.
        _write_task(live_task_env.dir_for(LEAD_SID), task_id="999", owner="foreign")
        tasks = task_utils.get_task_list(tasks_base_dir=str(live_task_env.tasks_root))
        assert tasks is None, (
            "empty team dir -> None; MUST NOT fall through to the populated "
            "foreign session_id dir (got %r)" % (tasks,)
        )

    def test_absent_team_dir_returns_none_not_foreign_session(self, live_task_env, monkeypatch):
        # Team dir ABSENT entirely; foreign session_id dir AND a TASK_LIST_ID
        # dir both populated -> still None (no fall-through on either tier).
        _write_task(live_task_env.dir_for(LEAD_SID), task_id="999", owner="foreign")
        _write_task(live_task_env.dir_for("env-pointer-dir"), task_id="888", owner="envdecoy")
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "env-pointer-dir")
        tasks = task_utils.get_task_list(tasks_base_dir=str(live_task_env.tasks_root))
        assert tasks is None, (
            "absent team dir -> None; neither the foreign session_id dir nor the "
            "CLAUDE_CODE_TASK_LIST_ID dir may be read in a team session"
        )


# ===========================================================================
# IT-5 — solo branch (team_name == "") resolver-tier behavior
#   Asserts ONLY resolver behavior; does NOT assert any caller consumes solo
#   tasks (per §5.3 — the invariant means no caller does; that would be dead).
# ===========================================================================
class TestSoloBranchResolver:
    """When team_name=='' the existing solo body is preserved verbatim:
    CLAUDE_CODE_TASK_LIST_ID-or-session_id keys the dir. TASK_LIST_ID matters
    ONLY here. Resolver-tier assertions only."""

    def test_solo_resolves_by_session_id_when_no_env(self, live_task_env, monkeypatch):
        live_task_env.configure(team_name="", session_id=LEAD_SID)
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        _write_task(live_task_env.dir_for(LEAD_SID), task_id="5", owner="solo")
        tasks = task_utils.get_task_list(tasks_base_dir=str(live_task_env.tasks_root))
        assert tasks is not None and {t["id"] for t in tasks} == {"5"}

    def test_solo_task_list_id_overrides_session_id(self, live_task_env, monkeypatch):
        live_task_env.configure(team_name="", session_id=LEAD_SID)
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "multi-session-pointer")
        _write_task(live_task_env.dir_for("multi-session-pointer"), task_id="9", owner="ptr")
        _write_task(live_task_env.dir_for(LEAD_SID), task_id="5", owner="solo")
        tasks = task_utils.get_task_list(tasks_base_dir=str(live_task_env.tasks_root))
        assert tasks is not None and {t["id"] for t in tasks} == {"9"}, (
            "in a SOLO session CLAUDE_CODE_TASK_LIST_ID takes precedence over session_id"
        )


# ===========================================================================
# IT-6 — teammate_idle 2nd-bug smoke (the GLOBAL fix repairs it for free)
#   NON-VACUITY GATE (arg-less get_task_list via the shared resolver).
# ===========================================================================
class TestTeammateIdleResolvesTeamTasks:
    """teammate_idle.main() guards `if not tasks: exit` at the arg-less
    get_task_list() call (:316). Pre-fix that returned None under Agent Teams,
    so the whole hook no-op'd (zombie-cleanup + auto-shutdown nudge dead).
    Post-fix the arg-less call resolves the team dir, so the guard is passed.
    This is a SMOKE on the shared resolver as teammate_idle invokes it."""

    def test_non_vacuity_gate_teammate_idle_arg_less_resolves_team_tasks(self, live_task_env):
        _write_task(live_task_env.dir_for(TEAM), task_id="3", owner="backend",
                    status="in_progress", with_wait=False)
        # teammate_idle calls the shared get_task_list() arg-less (:316).
        tasks = teammate_idle.get_task_list()
        assert tasks is not None, (
            "teammate_idle's arg-less get_task_list() must resolve the team dir "
            "post-fix — None here is the 2nd inert bug (idle-cleanup never runs)"
        )
        assert {t["id"] for t in tasks} == {"3"}
