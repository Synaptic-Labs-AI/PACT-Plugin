"""
Tests for hooks/shared/session_state.py — single-session state summarizer
that replaces task_scanner.py (#411 root cause).

Covers the 18-test matrix from docs/architecture/journal-based-task-scanner.md
§3.5 + 3 explicit regression guards for #411 and #412 Fix A:
- iteration-order independence (filesystem order must not influence output)
- no-foreign-session-read (a second session's journal in the same parent
  must not leak into the summary)
- no-cross-team-scan (a sibling team's config.json must not surface members)

Every test drives the public API via explicit args (session_dir, team_name,
tasks_base_dir, teams_base_dir) — no reliance on pact_context. The
underlying read path is real file I/O (synthetic journal.jsonl + tmp
~/.claude/{teams,tasks}/{team_name}/ fixtures), per the HIGH-risk-tier
constraint of using real fixtures rather than mocks.

Risk tier: HIGH (regression guards directly encode the #411 / #412 bug
invariants; they are load-bearing).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.session_journal import make_event
from shared.session_state import (
    _derive_feature_from_journal,
    _derive_phase_from_journal,
    _derive_variety_from_journal,
    _read_task_counts,
    _read_team_members,
    _default_state,
    summarize_session_state,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_journal(session_dir: Path, events: list[dict]) -> Path:
    """
    Write a list of journal events as JSONL to {session_dir}/session-journal.jsonl.

    Creates the session_dir if it does not exist. Returns the journal path.
    Events are written in the order given (supports iteration-order testing
    by letting callers shuffle timestamps vs line order).
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    journal = session_dir / "session-journal.jsonl"
    with journal.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return journal


def _write_team_config(
    teams_base: Path,
    team_name: str,
    members: list[str],
) -> Path:
    """
    Write ~/.claude/teams/{team_name}/config.json shape: members is a list
    of dicts each with {"name": <str>}. Returns the config path.
    """
    team_dir = teams_base / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config_path = team_dir / "config.json"
    config_path.write_text(
        json.dumps({
            "name": team_name,
            "members": [{"name": n} for n in members],
        }),
        encoding="utf-8",
    )
    return config_path


def _write_task(
    tasks_base: Path,
    team_name: str,
    task_id: str,
    status: str,
    subject: str = "",
    metadata: dict | None = None,
) -> Path:
    """
    Write ~/.claude/tasks/{team_name}/{task_id}.json with the canonical
    task shape. Returns the path.
    """
    team_dir = tasks_base / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    task_path = team_dir / f"{task_id}.json"
    task_path.write_text(
        json.dumps({
            "id": task_id,
            "subject": subject,
            "status": status,
            "metadata": metadata or {},
        }),
        encoding="utf-8",
    )
    return task_path


# ---------------------------------------------------------------------------
# Matrix row 1-6: TestJournalFields — journal-sourced fields only
# ---------------------------------------------------------------------------


class TestJournalFields:
    """Tests for the four journal-sourced fields:
    feature_subject, feature_id, current_phase, variety_score."""

    def test_happy_path_all_journal_fields(self, tmp_path):
        """Row 1: variety + dispatch + phase events populate all journal-sourced fields."""
        session_dir = tmp_path / "session-abc"
        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="5",
                       variety={"score": 7, "level": "MEDIUM"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("phase_transition", phase="PREPARE", status="completed",
                       ts="2026-04-14T00:00:03Z"),
            make_event("phase_transition", phase="ARCHITECT", status="started",
                       ts="2026-04-14T00:00:04Z"),
            make_event("agent_dispatch", agent="architect", task_id="9",
                       phase="ARCHITECT", ts="2026-04-14T00:00:05Z"),
            make_event("agent_handoff", agent="architect", task_id="5",
                       task_subject="Build auth flow",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:06Z"),
        ])

        # Call with explicit empty team_name to isolate journal-only fields
        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "no-tasks"),
            teams_base_dir=str(tmp_path / "no-teams"),
        )

        assert result["current_phase"] == "ARCHITECT"
        assert result["feature_id"] == "5"
        assert result["feature_subject"] == "Build auth flow"
        assert result["variety_score"] == {"score": 7, "level": "MEDIUM"}

    def test_empty_journal_defaults_fail_open(self, tmp_path):
        """Row 2: empty events list → all journal fields fall back to None."""
        session_dir = tmp_path / "session-empty"
        _write_journal(session_dir, [])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "nx"),
            teams_base_dir=str(tmp_path / "nx"),
        )

        assert result["feature_id"] is None
        assert result["feature_subject"] is None
        assert result["current_phase"] is None
        assert result["variety_score"] is None

    def test_phase_started_then_completed_returns_none(self, tmp_path):
        """Row 3: phase started + completed → current_phase is None (completed wins at tie)."""
        session_dir = tmp_path / "session-phase"
        _write_journal(session_dir, [
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="CODE", status="completed",
                       ts="2026-04-14T00:00:02Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        assert result["current_phase"] is None

    def test_multiple_phases_latest_uncompleted_wins(self, tmp_path):
        """Row 4: several phase_transitions; latest-started-uncompleted wins."""
        session_dir = tmp_path / "session-multi"
        _write_journal(session_dir, [
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="PREPARE", status="completed",
                       ts="2026-04-14T00:00:02Z"),
            make_event("phase_transition", phase="ARCHITECT", status="started",
                       ts="2026-04-14T00:00:03Z"),
            make_event("phase_transition", phase="ARCHITECT", status="completed",
                       ts="2026-04-14T00:00:04Z"),
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:00:05Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        assert result["current_phase"] == "CODE"

    def test_malformed_journal_line_skipped(self, tmp_path):
        """Row 5: one bad line → skipped; other events intact. Fail-open per-line."""
        session_dir = tmp_path / "session-bad-line"
        session_dir.mkdir(parents=True)
        journal = session_dir / "session-journal.jsonl"
        # Mix of valid and malformed lines
        good_event = make_event("variety_assessed", task_id="7",
                                variety={"score": 3},
                                ts="2026-04-14T00:00:01Z")
        journal.write_text(
            json.dumps(good_event) + "\n"
            + "{ this is garbage not json\n"
            + json.dumps(make_event("phase_transition", phase="TEST",
                                    status="started",
                                    ts="2026-04-14T00:00:02Z")) + "\n",
            encoding="utf-8",
        )

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        assert result["feature_id"] == "7"
        assert result["current_phase"] == "TEST"
        # Bad line was silently skipped — no raise, all good events present

    def test_missing_journal_file_defaults(self, tmp_path):
        """Row 6: journal file missing → all journal fields fall back. No raise."""
        missing = tmp_path / "session-does-not-exist"
        # Deliberately do NOT create the dir or file

        result = summarize_session_state(
            session_dir=str(missing),
            team_name="",
        )

        assert result["feature_id"] is None
        assert result["feature_subject"] is None
        assert result["current_phase"] is None
        assert result["variety_score"] is None


# ---------------------------------------------------------------------------
# Matrix row 7-9: TestTeamMembers — _read_team_members exercises
# ---------------------------------------------------------------------------


class TestTeamMembers:
    """Tests for _read_team_members — reads
    ~/.claude/teams/{team_name}/config.json."""

    def test_members_list(self, tmp_path):
        """Row 7: config.json members → teammates list in order."""
        teams = tmp_path / "teams"
        _write_team_config(teams, "pact-test", ["coder", "tester", "architect"])

        names = _read_team_members("pact-test", teams_base_dir=str(teams))

        assert names == ["coder", "tester", "architect"]

    def test_config_missing_empty_list(self, tmp_path):
        """Row 8: config.json missing → []. Fail-open."""
        teams = tmp_path / "teams"
        # Do not create any config
        assert _read_team_members("pact-ghost", teams_base_dir=str(teams)) == []

    def test_config_malformed_empty_list(self, tmp_path):
        """Row 9: malformed JSON → []. Fail-open."""
        teams = tmp_path / "teams"
        team_dir = teams / "pact-bad"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            "{ not valid json", encoding="utf-8"
        )

        assert _read_team_members("pact-bad", teams_base_dir=str(teams)) == []

    def test_empty_team_name_returns_empty(self, tmp_path):
        """Extra guard: empty team_name short-circuits (no disk read attempt)."""
        teams = tmp_path / "teams"
        assert _read_team_members("", teams_base_dir=str(teams)) == []

    def test_non_list_members_field_empty(self, tmp_path):
        """Extra guard: config.json with members != list → []."""
        teams = tmp_path / "teams"
        team_dir = teams / "pact-wrong-shape"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"name": "pact-wrong-shape", "members": "not-a-list"}),
            encoding="utf-8",
        )

        assert _read_team_members(
            "pact-wrong-shape", teams_base_dir=str(teams)
        ) == []

    def test_member_dict_without_name_skipped(self, tmp_path):
        """Extra guard: member dicts missing 'name' are skipped, not raised."""
        teams = tmp_path / "teams"
        team_dir = teams / "pact-partial"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({
                "name": "pact-partial",
                "members": [
                    {"name": "good"},
                    {"wrong_key": "x"},
                    {"name": ""},
                    {"name": "also-good"},
                ],
            }),
            encoding="utf-8",
        )

        assert _read_team_members(
            "pact-partial", teams_base_dir=str(teams)
        ) == ["good", "also-good"]


# ---------------------------------------------------------------------------
# Matrix row 10-12: TestTaskCounts — _read_task_counts exercises
# ---------------------------------------------------------------------------


class TestTaskCounts:
    """Tests for _read_task_counts — reads
    ~/.claude/tasks/{team_name}/*.json and counts by status."""

    def test_mixed_status_counts(self, tmp_path):
        """Row 10: N tasks with mixed statuses → counts sum to total."""
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "completed", "Task 1")
        _write_task(tasks, "pact-test", "2", "completed", "Task 2")
        _write_task(tasks, "pact-test", "3", "in_progress", "Task 3")
        _write_task(tasks, "pact-test", "4", "pending", "Task 4")
        _write_task(tasks, "pact-test", "5", "pending", "Task 5")
        _write_task(tasks, "pact-test", "6", "pending", "Task 6")

        counts = _read_task_counts("pact-test", tasks_base_dir=str(tasks))

        assert counts["completed"] == 2
        assert counts["in_progress"] == 1
        assert counts["pending"] == 3
        assert counts["total"] == 6

    def test_empty_tasks_dir_all_zero(self, tmp_path):
        """Row 11: empty tasks dir → all zero."""
        tasks = tmp_path / "tasks"
        # Create the team dir but no task files
        (tasks / "pact-empty").mkdir(parents=True)

        counts = _read_task_counts("pact-empty", tasks_base_dir=str(tasks))

        assert counts == {"completed": 0, "in_progress": 0, "pending": 0,
                          "total": 0}

    def test_missing_team_dir_all_zero(self, tmp_path):
        """Row 11b: team dir missing entirely → all zero."""
        tasks = tmp_path / "tasks"
        # Don't create anything
        counts = _read_task_counts("pact-ghost", tasks_base_dir=str(tasks))
        assert counts == {"completed": 0, "in_progress": 0, "pending": 0,
                          "total": 0}

    def test_malformed_task_json_skipped(self, tmp_path):
        """Row 12: malformed JSON on one task → skipped; others counted."""
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "in_progress", "Good task")
        team_dir = tasks / "pact-test"
        (team_dir / "bad.json").write_text("{ not json", encoding="utf-8")
        _write_task(tasks, "pact-test", "2", "completed", "Another good task")

        counts = _read_task_counts("pact-test", tasks_base_dir=str(tasks))

        assert counts["completed"] == 1
        assert counts["in_progress"] == 1
        assert counts["total"] == 2

    def test_empty_team_name_returns_zero_dict(self, tmp_path):
        """Extra guard: empty team_name short-circuits (no disk read attempt)."""
        tasks = tmp_path / "tasks"
        counts = _read_task_counts("", tasks_base_dir=str(tasks))
        assert counts == {"completed": 0, "in_progress": 0, "pending": 0,
                          "total": 0}

    def test_unknown_status_not_counted_but_totaled(self, tmp_path):
        """Extra guard: task with non-canonical status contributes to total
        but not to any bucket — mirrors task_scanner.py behavior."""
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "in_progress", "A")
        _write_task(tasks, "pact-test", "2", "weird-unknown-status", "B")

        counts = _read_task_counts("pact-test", tasks_base_dir=str(tasks))

        # total counts EVERY json file; bucket counts only recognized statuses
        assert counts["total"] == 2
        assert counts["in_progress"] == 1
        assert counts["completed"] == 0
        assert counts["pending"] == 0


# ---------------------------------------------------------------------------
# Matrix row 13-15: TestSummarize — full integration contract
# ---------------------------------------------------------------------------


class TestSummarize:
    """Tests for summarize_session_state — the full 10-key contract."""

    def test_full_integration_dict_shape(self, tmp_path):
        """Row 13: all inputs populated → dict has all 10 keys with correct types."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="1",
                       variety={"score": 5, "level": "MEDIUM"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("agent_handoff", agent="coder", task_id="1",
                       task_subject="Build dashboard",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:03Z"),
        ])
        _write_team_config(teams, "pact-test", ["coder", "tester"])
        _write_task(tasks, "pact-test", "1", "in_progress", "Build dashboard")
        _write_task(tasks, "pact-test", "2", "pending", "Something else")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # 10 keys exactly, nothing missing, nothing extra
        assert set(result.keys()) == {
            "completed", "in_progress", "pending", "total",
            "feature_subject", "feature_id", "current_phase",
            "variety_score", "teammates", "team_names",
        }
        # Types
        assert isinstance(result["completed"], int)
        assert isinstance(result["in_progress"], int)
        assert isinstance(result["pending"], int)
        assert isinstance(result["total"], int)
        assert isinstance(result["feature_subject"], str)
        assert isinstance(result["feature_id"], str)
        assert isinstance(result["current_phase"], str)
        assert isinstance(result["variety_score"], dict)
        assert isinstance(result["teammates"], list)
        assert isinstance(result["team_names"], list)
        # Values
        assert result["completed"] == 0
        assert result["in_progress"] == 1
        assert result["pending"] == 1
        assert result["total"] == 2
        assert result["feature_subject"] == "Build dashboard"
        assert result["feature_id"] == "1"
        assert result["current_phase"] == "CODE"
        assert result["variety_score"] == {"score": 5, "level": "MEDIUM"}
        assert result["teammates"] == ["coder", "tester"]
        assert result["team_names"] == ["pact-test"]

    def test_team_name_empty_fails_open_disk_fields(self, tmp_path):
        """Row 14: team_name empty → disk fields default; journal fields still populated."""
        session_dir = tmp_path / "session"
        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="99",
                       variety={"score": 2},
                       ts="2026-04-14T00:00:01Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "nx"),
            teams_base_dir=str(tmp_path / "nx"),
        )

        assert result["feature_id"] == "99"
        assert result["variety_score"] == {"score": 2}
        assert result["completed"] == 0
        assert result["in_progress"] == 0
        assert result["pending"] == 0
        assert result["total"] == 0
        assert result["teammates"] == []
        assert result["team_names"] == []

    def test_explicit_args_override_home_defaults(self, tmp_path):
        """Row 15: explicit args reach the tmp path, not ~/.claude/.

        The test infrastructure uses only tmp_path — if the module resolved
        ~/.claude/teams/{team_name}/ instead of teams_base_dir, the tmp
        members list would NOT surface. Proves the arg is wired through."""
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"
        # A signature name unlikely to exist in real ~/.claude/
        signature = ["signature-member-xyz-424242"]
        _write_team_config(teams, "pact-explicit-arg-test", signature)
        _write_task(tasks, "pact-explicit-arg-test", "1", "pending", "x")

        result = summarize_session_state(
            session_dir="",
            team_name="pact-explicit-arg-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["teammates"] == signature
        assert result["total"] == 1
        assert result["team_names"] == ["pact-explicit-arg-test"]

    def test_never_raises_on_everything_missing(self):
        """Extra guard: all inputs bad → returns defaults dict, no raise."""
        # Deliberately use paths that do not exist and have no parent
        result = summarize_session_state(
            session_dir="/nonexistent/path/that/cannot/exist/qqq",
            team_name="ghost-team",
            tasks_base_dir="/nonexistent/tasks/qqq",
            teams_base_dir="/nonexistent/teams/qqq",
        )

        assert result == {
            "completed": 0, "in_progress": 0, "pending": 0, "total": 0,
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
            "teammates": [],
            "team_names": ["ghost-team"],
        }

    def test_empty_session_dir_journal_fields_default(self, tmp_path):
        """Empty session_dir → journal fields default; disk fields honored."""
        teams = tmp_path / "teams"
        _write_team_config(teams, "pact-empty-sd", ["m1"])
        result = summarize_session_state(
            session_dir="",
            team_name="pact-empty-sd",
            tasks_base_dir=str(tmp_path / "nx"),
            teams_base_dir=str(teams),
        )
        assert result["feature_id"] is None
        assert result["current_phase"] is None
        assert result["teammates"] == ["m1"]


# ---------------------------------------------------------------------------
# Matrix row 16: TestIterationOrder — REGRESSION GUARD #1
# ---------------------------------------------------------------------------


class TestIterationOrderIndependence:
    """REGRESSION GUARD #1: feature_id derivation must depend on event
    timestamps, NOT on line order or filesystem iteration order.

    Old task_scanner.analyze_task_state used filesystem iterdir() which
    is nondeterministic across platforms. The journal-based derivation
    must be ts-ordered and platform-independent."""

    def test_dispatch_events_line_order_does_not_shadow_ts_order(self, tmp_path):
        """Line order 1→2→3 but ts order 2→1→3.

        Expected: feature_id corresponds to the chronologically-earliest
        timestamp (ts="2026-04-14T00:00:50Z"), NOT the line-first event."""
        session_dir = tmp_path / "session-order"
        _write_journal(session_dir, [
            # Line 1, ts=100 — written first but NOT earliest
            make_event("agent_dispatch", agent="a1", task_id="LINE1",
                       phase="CODE",
                       ts="2026-04-14T00:01:40Z"),
            # Line 2, ts=50 — chronologically earliest; should win
            make_event("agent_dispatch", agent="a2", task_id="LINE2",
                       phase="CODE",
                       ts="2026-04-14T00:00:50Z"),
            # Line 3, ts=200
            make_event("agent_dispatch", agent="a3", task_id="LINE3",
                       phase="CODE",
                       ts="2026-04-14T00:03:20Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        # The fallback uses chronologically-first agent_dispatch.task_id
        # (when no variety_assessed is present). LINE2 has the earliest ts.
        assert result["feature_id"] == "LINE2", (
            f"Expected feature_id='LINE2' (earliest ts), got "
            f"{result['feature_id']!r}. File-order-based derivation would "
            f"return 'LINE1' — this test guards against that regression."
        )

    def test_phase_transition_order_invariance(self, tmp_path):
        """Shuffled line order for phases → latest-ts-started still wins.

        Write phase events out of line order; verify current_phase is the
        latest-ts-started phase regardless of line position."""
        session_dir = tmp_path / "session-phase-order"
        _write_journal(session_dir, [
            # Out-of-line-order deliberately
            make_event("phase_transition", phase="TEST", status="started",
                       ts="2026-04-14T00:03:00Z"),  # LATEST — should win
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:01:00Z"),
            make_event("phase_transition", phase="PREPARE", status="completed",
                       ts="2026-04-14T00:01:30Z"),
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:02:00Z"),
            make_event("phase_transition", phase="CODE", status="completed",
                       ts="2026-04-14T00:02:30Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        assert result["current_phase"] == "TEST"


# ---------------------------------------------------------------------------
# Matrix row 17: TestNoJournalCrossSession — REGRESSION GUARD #2
# ---------------------------------------------------------------------------


class TestNoForeignSessionRead:
    """REGRESSION GUARD #2: the summarizer must only read THIS session's
    journal. A neighbor session's journal (same parent dir) must NOT leak.

    This guards against a regression of #411's root cause: the old
    task_scanner iterated across the parent and surfaced phantom state
    from unrelated sessions."""

    def test_foreign_session_journal_not_read(self, tmp_path):
        """Create two sessions under a common parent — pass session_dir=B;
        assert none of A's events surface."""
        common = tmp_path / "pact-sessions"
        session_a = common / "session-A"
        session_b = common / "session-B"

        # Session A — feature_id "AAA", phase "PREPARE" (distinct values)
        _write_journal(session_a, [
            make_event("variety_assessed", task_id="AAA",
                       variety={"score": 9, "level": "FOREIGN"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("agent_handoff", agent="foreign-agent", task_id="AAA",
                       task_subject="FOREIGN SUBJECT that must not leak",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:03Z"),
        ])

        # Session B — feature_id "BBB", phase "TEST"
        _write_journal(session_b, [
            make_event("variety_assessed", task_id="BBB",
                       variety={"score": 2, "level": "LOCAL"},
                       ts="2026-04-14T00:00:04Z"),
            make_event("phase_transition", phase="TEST", status="started",
                       ts="2026-04-14T00:00:05Z"),
            make_event("agent_handoff", agent="local-agent", task_id="BBB",
                       task_subject="LOCAL SUBJECT",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:06Z"),
        ])

        # Query session B
        result = summarize_session_state(
            session_dir=str(session_b),
            team_name="",
        )

        # None of session A's data leaks
        assert result["feature_id"] == "BBB"
        assert result["feature_subject"] == "LOCAL SUBJECT"
        assert result["current_phase"] == "TEST"
        assert result["variety_score"] == {"score": 2, "level": "LOCAL"}
        # The FOREIGN values must never appear
        assert "AAA" not in (result.get("feature_id") or "")
        assert "FOREIGN" not in json.dumps(result)
        assert "foreign" not in json.dumps(result).lower()


# ---------------------------------------------------------------------------
# Matrix row 18: TestNoCrossTeamScan — REGRESSION GUARD #3
# ---------------------------------------------------------------------------


class TestNoCrossTeamScan:
    """REGRESSION GUARD #3: #411 root cause. task_scanner.scan_team_members
    did ~/.claude/teams/*.iterdir(), surfacing phantom members from
    OTHER teams into the current session's state.

    The new _read_team_members must ONLY read
    ~/.claude/teams/{team_name}/config.json — never the parent
    directory — so a sibling team's members stay invisible."""

    def test_sibling_team_members_do_not_leak(self, tmp_path):
        """Two teams under tmp_teams/: pact-test (empty members) and
        pact-ghost (phantom members). Query pact-test → teammates=[]."""
        teams = tmp_path / "teams"

        # Our team — empty members
        _write_team_config(teams, "pact-test", [])

        # Ghost team — phantom members that must never surface
        _write_team_config(teams, "pact-ghost", [
            "ghost-phantom-1", "ghost-phantom-2", "ghost-phantom-3"
        ])

        # Tasks similarly segregated
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "in_progress", "Real task")
        _write_task(tasks, "pact-ghost", "99", "completed", "Ghost task")
        _write_task(tasks, "pact-ghost", "100", "in_progress", "Another ghost")

        result = summarize_session_state(
            session_dir="",
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # Our team's reality — empty members + single task
        assert result["teammates"] == []
        assert result["team_names"] == ["pact-test"]
        assert result["total"] == 1
        assert result["in_progress"] == 1
        assert result["completed"] == 0

        # NO ghost data leaks
        raw = json.dumps(result)
        assert "ghost-phantom" not in raw
        assert "pact-ghost" not in raw
        assert "99" not in raw
        assert "100" not in raw

    def test_sibling_team_subject_does_not_leak_into_feature_subject(
        self, tmp_path,
    ):
        """The disk fallback for feature_subject must only read
        tasks/{team_name}/{feature_id}.json — NOT scan a sibling team.

        Write a journal pointing at feature_id=42 without a handoff
        (forcing disk fallback). Put task 42 ONLY in the ghost team.
        Assert feature_subject stays None."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            # Dispatch with no handoff → no journal subject → disk fallback
            make_event("agent_dispatch", agent="coder", task_id="42",
                       phase="CODE",
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", [])
        # Task 42 exists ONLY in the ghost team
        _write_task(tasks, "pact-ghost", "42", "in_progress",
                    "PHANTOM FEATURE SUBJECT")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # The journal identified feature_id=42, but the disk fallback is
        # scoped to pact-test/42.json (which does not exist) — so the
        # phantom subject from pact-ghost/42.json must NOT leak.
        assert result["feature_id"] == "42"
        assert result["feature_subject"] is None
        assert "PHANTOM" not in json.dumps(result)


# ---------------------------------------------------------------------------
# Private helpers — direct unit coverage
# ---------------------------------------------------------------------------


class TestDefaultState:
    """Tests for _default_state — used both as accumulator and fail-open return."""

    def test_default_state_shape(self):
        result = _default_state("pact-x")
        assert set(result.keys()) == {
            "completed", "in_progress", "pending", "total",
            "feature_subject", "feature_id", "current_phase",
            "variety_score", "teammates", "team_names",
        }
        assert result["team_names"] == ["pact-x"]
        assert result["teammates"] == []
        assert result["completed"] == 0
        assert result["feature_id"] is None

    def test_default_state_empty_team_name(self):
        result = _default_state("")
        assert result["team_names"] == []


class TestDerivePhaseFromJournal:
    """Direct tests for _derive_phase_from_journal beyond integration."""

    def test_no_events(self):
        assert _derive_phase_from_journal([]) is None

    def test_non_phase_events_ignored(self):
        events = [
            make_event("variety_assessed", task_id="1",
                       variety={"score": 1}, ts="2026-04-14T00:00:01Z"),
            make_event("agent_dispatch", agent="c", task_id="1", phase="CODE",
                       ts="2026-04-14T00:00:02Z"),
        ]
        assert _derive_phase_from_journal(events) is None

    def test_malformed_phase_entry_skipped(self):
        """A phase event missing 'phase' or with wrong type is ignored."""
        events = [
            # Missing phase name → skipped by isinstance guard
            {"type": "phase_transition", "status": "started",
             "ts": "2026-04-14T00:00:01Z"},
            # Wrong-type phase → skipped
            {"type": "phase_transition", "phase": 42, "status": "started",
             "ts": "2026-04-14T00:00:02Z"},
            # Valid entry
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:00:03Z"),
        ]
        assert _derive_phase_from_journal(events) == "CODE"


class TestDeriveFeatureFromJournal:
    """Direct tests for _derive_feature_from_journal edge cases."""

    def test_variety_preferred_over_dispatch(self):
        """variety_assessed wins over agent_dispatch for feature_id."""
        events = [
            make_event("agent_dispatch", agent="c", task_id="DISPATCH-ID",
                       phase="CODE", ts="2026-04-14T00:00:01Z"),
            make_event("variety_assessed", task_id="VARIETY-ID",
                       variety={"score": 1},
                       ts="2026-04-14T00:00:02Z"),
        ]
        feature_id, _ = _derive_feature_from_journal(events)
        assert feature_id == "VARIETY-ID"

    def test_handoff_subject_matched_by_task_id(self):
        """Handoff subject only applies when task_id matches feature_id."""
        events = [
            make_event("variety_assessed", task_id="F-1",
                       variety={"score": 1}, ts="2026-04-14T00:00:01Z"),
            # Unrelated handoff for a different task
            make_event("agent_handoff", agent="a", task_id="OTHER-99",
                       task_subject="UNRELATED",
                       handoff={}, ts="2026-04-14T00:00:02Z"),
            # Matching handoff
            make_event("agent_handoff", agent="b", task_id="F-1",
                       task_subject="CORRECT",
                       handoff={}, ts="2026-04-14T00:00:03Z"),
        ]
        feature_id, subject = _derive_feature_from_journal(events)
        assert feature_id == "F-1"
        assert subject == "CORRECT"

    def test_feature_id_with_no_handoff_yields_none_subject(self):
        """feature_id present but no matching handoff → subject is None
        (so the disk fallback can take over)."""
        events = [
            make_event("variety_assessed", task_id="F-NO-HANDOFF",
                       variety={"score": 1},
                       ts="2026-04-14T00:00:01Z"),
        ]
        feature_id, subject = _derive_feature_from_journal(events)
        assert feature_id == "F-NO-HANDOFF"
        assert subject is None

    def test_invalid_task_id_in_variety_falls_back(self):
        """variety_assessed with empty/non-str task_id → fall back to dispatch."""
        events = [
            {"type": "variety_assessed", "task_id": "",
             "variety": {"score": 1}, "v": 1,
             "ts": "2026-04-14T00:00:01Z"},
            make_event("agent_dispatch", agent="c", task_id="REAL-ID",
                       phase="CODE", ts="2026-04-14T00:00:02Z"),
        ]
        feature_id, _ = _derive_feature_from_journal(events)
        assert feature_id == "REAL-ID"


class TestDeriveVarietyFromJournal:
    """Direct tests for _derive_variety_from_journal."""

    def test_no_variety_events_returns_none(self):
        assert _derive_variety_from_journal([]) is None

    def test_first_variety_event_wins(self):
        """The first (chronologically-earliest) variety_assessed wins."""
        events = [
            make_event("variety_assessed", task_id="1",
                       variety={"first": True},
                       ts="2026-04-14T00:00:01Z"),
            make_event("variety_assessed", task_id="2",
                       variety={"second": True},
                       ts="2026-04-14T00:00:02Z"),
        ]
        assert _derive_variety_from_journal(events) == {"first": True}


# ---------------------------------------------------------------------------
# Full behavior: feature_subject disk fallback
# ---------------------------------------------------------------------------


class TestFeatureSubjectDiskFallback:
    """Tests for the disk-read fallback for feature_subject when the
    journal identifies feature_id but no matching agent_handoff exists."""

    def test_disk_fallback_reads_task_subject(self, tmp_path):
        """variety_assessed without handoff → disk fallback supplies subject."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="17",
                       variety={"score": 4},
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", ["coder"])
        _write_task(tasks, "pact-test", "17", "in_progress",
                    "Fallback subject from disk")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["feature_id"] == "17"
        assert result["feature_subject"] == "Fallback subject from disk"

    def test_disk_fallback_filters_system_prefixes(self, tmp_path):
        """Disk fallback must skip task subjects starting with system
        prefixes (Phase:, BLOCKER:, ALERT:, HALT:). Regression guard."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="42",
                       variety={"score": 4},
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", [])
        # Task 42 has a Phase: prefix subject — must NOT be used as feature
        _write_task(tasks, "pact-test", "42", "in_progress",
                    "Phase: CODE")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["feature_id"] == "42"
        # Phase:-prefixed subject is NOT accepted as feature_subject
        assert result["feature_subject"] is None

    def test_journal_handoff_preferred_over_disk(self, tmp_path):
        """When both journal handoff AND disk task exist, journal wins."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="8",
                       variety={"score": 4},
                       ts="2026-04-14T00:00:01Z"),
            make_event("agent_handoff", agent="a", task_id="8",
                       task_subject="JOURNAL WINS",
                       handoff={}, ts="2026-04-14T00:00:02Z"),
        ])
        _write_team_config(teams, "pact-test", [])
        _write_task(tasks, "pact-test", "8", "in_progress",
                    "disk subject (ignored)")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["feature_subject"] == "JOURNAL WINS"
