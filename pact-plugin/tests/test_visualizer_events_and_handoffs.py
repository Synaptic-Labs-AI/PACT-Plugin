"""Location: pact-plugin/tests/test_visualizer_events_and_handoffs.py
Summary: Regression tests for the backend review findings on the PACT Mission
         Control visualizer: raw-payload handoff completeness, the windowed
         backward scan's boundary handling, the `task_created` board source,
         the exact `--project` filter, the constrained project label, the
         single run-kind rule shared by the list and the model, and per-agent
         commit attribution.
Used by: CI (`pytest tests/`), the visualizer's own test suite.

Every fixture here is constructed: journals are written byte-for-byte so a
window boundary lands at a chosen offset, and no test reads the operator's
real ~/.claude.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import visualizer_census as census

from visualizer import classify, discovery, journal, model
from visualizer.settings import (
    ORCHESTRATION_MARKER_TYPES,
    SUMMARY_TAIL_BYTES,
    RootSpec,
    normalize_variety,
)

STAMP = "2026-01-01T00:00:00Z"


# --- fixtures ---------------------------------------------------------------


def make_root(tmp_path: Path, name: str = "root") -> Path:
    root = tmp_path / name
    (root / "pact-sessions").mkdir(parents=True)
    return root


def roots_for(root: Path) -> list[RootSpec]:
    return [RootSpec(id="~/.claude", label=".claude", path=root, default=True)]


def write_journal(
    root: Path, slug: str, session_id: str, events: list[dict]
) -> Path:
    session_dir = root / "pact-sessions" / slug / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session-journal.jsonl"
    body = "\n".join(json.dumps(event) for event in events)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def build_payload(root: Path, slug: str, session_id: str) -> dict:
    ref = discovery.find_session(roots_for(root), slug, session_id)
    assert ref is not None, "fixture journal was not discovered"
    return model.build_model(ref)


def list_payload(root: Path, **filters) -> dict:
    """The session list with trivial sessions shown: these fixtures are small."""
    return model.build_sessions_list(roots_for(root), show_all=True, **filters)


def handoff_event(task_id: str, handoff: object) -> dict:
    return {
        "v": 1,
        "type": "agent_handoff",
        "ts": STAMP,
        "agent": "preparer",
        "task_id": task_id,
        "task_subject": f"task {task_id}",
        "handoff": handoff,
    }


# --- 1. handoff completeness is decided on the RAW payload ------------------


class TestHandoffCompletenessRaw:
    """The badge mirrors the SSOT's presence-only rule, not the display text."""

    @pytest.mark.parametrize("empty", ["", [], {}, None])
    @pytest.mark.parametrize("field", sorted(classify.HANDOFF_REQUIRED_FIELDS))
    def test_present_but_empty_required_field_is_complete(self, field, empty):
        payload = {name: "x" for name in classify.HANDOFF_REQUIRED_FIELDS}
        payload[field] = empty
        assert classify.handoff_schema_complete_raw(payload) is True

    @pytest.mark.parametrize("field", sorted(classify.HANDOFF_REQUIRED_FIELDS))
    def test_absent_required_field_is_incomplete(self, field):
        payload = {name: "x" for name in classify.HANDOFF_REQUIRED_FIELDS if name != field}
        assert classify.handoff_schema_complete_raw(payload) is False

    def test_missing_recommended_reasoning_chain_is_complete(self):
        payload = {name: "x" for name in classify.HANDOFF_REQUIRED_FIELDS}
        assert "reasoning_chain" not in payload
        assert classify.handoff_schema_complete_raw(payload) is True

    def test_alias_only_record_is_incomplete(self):
        """The SSOT validator reports an alias-only record as missing its key."""
        payload = {
            "produced": "p",
            "key_decisions": "d",
            "areas_of_uncertainty": "u",
            "integration_points": "i",
            "open_questions": "o",
        }
        assert classify.handoff_schema_complete_raw(payload) is False

    def test_non_mapping_payload_is_incomplete(self):
        assert classify.handoff_schema_complete_raw(None) is False
        assert classify.handoff_schema_complete_raw(["produced"]) is False

    def test_badge_uses_the_raw_payload_while_fields_still_render_aliases(self, tmp_path):
        """End to end: present-but-empty is complete; alias-only is not."""
        root = make_root(tmp_path)
        empty_strings = {name: "" for name in classify.HANDOFF_REQUIRED_FIELDS}
        alias_only = {
            "produced": "p",
            "key_decisions": "legacy decisions",
            "uncertainty": "u",
            "integration": "i",
            "open_questions": "o",
        }
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP, "project_dir": "/w/proj"},
                handoff_event("1", empty_strings),
                handoff_event("2", alias_only),
            ],
        )
        payload = build_payload(root, "proj", "sess")
        complete = {row["task_id"]: row for row in payload["handoffs"]}
        assert complete["1"]["schema_complete"] is True
        assert complete["1"]["fields"]["open_questions"] is None  # display still a dash
        assert complete["2"]["schema_complete"] is False
        # Display behaviour is unchanged: the alias still fills the row.
        assert complete["2"]["fields"]["decisions"] == "legacy decisions"


# --- 2. the backward scan carries the straddling line -----------------------

_LINE_SHELL_PREFIX = '{"v":1,"type":"note","pad":"'
_LINE_SHELL_SUFFIX = '"}\n'


def filler_line(total_bytes: int) -> bytes:
    """One valid note line of exactly total_bytes, newline included."""
    pad = total_bytes - len(_LINE_SHELL_PREFIX) - len(_LINE_SHELL_SUFFIX)
    assert pad >= 0
    line = (_LINE_SHELL_PREFIX + "x" * pad + _LINE_SHELL_SUFFIX).encode("utf-8")
    assert len(line) == total_bytes
    return line


def filler_bytes(total_bytes: int, line_size: int = 128) -> bytes:
    """Exactly total_bytes of parseable filler lines."""
    remainder = total_bytes % line_size
    first_len = remainder if remainder >= 64 else remainder + line_size
    count = (total_bytes - first_len) // line_size
    data = filler_line(first_len) + filler_line(line_size) * count
    assert len(data) == total_bytes
    return data


def marker_bytes(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"


def straddling_journal(
    path: Path, marker: dict, boundary_offset_from_marker_end: int
) -> int:
    """Write prefix + marker + suffix so a window boundary cuts the marker.

    ``boundary_offset_from_marker_end`` is how far the scan's window boundary
    sits from the marker's final byte; a negative value puts the boundary
    INSIDE the marker (the straddle), a positive value past its end (control).
    """
    encoded = marker_bytes(marker)
    suffix_len = SUMMARY_TAIL_BYTES - boundary_offset_from_marker_end
    prefix = filler_bytes(2 * SUMMARY_TAIL_BYTES)
    data = prefix + encoded + filler_bytes(suffix_len)
    path.write_bytes(data)
    return len(data)


class TestBackwardScanBoundary:
    """A line cut by a window boundary must still be read whole."""

    def test_phase_transition_straddling_a_window_boundary_is_found(self, tmp_path):
        path = tmp_path / "straddle.jsonl"
        marker = {"v": 1, "type": "phase_transition", "ts": STAMP, "phase": "TEST"}
        total = straddling_journal(path, marker, boundary_offset_from_marker_end=10)
        found = journal._scan_back(path, total, {"phase_transition"})
        assert found["phase_transition"]["phase"] == "TEST"

    def test_control_marker_fully_inside_a_window_is_found(self, tmp_path):
        path = tmp_path / "control.jsonl"
        marker = {"v": 1, "type": "phase_transition", "ts": STAMP, "phase": "TEST"}
        total = straddling_journal(path, marker, boundary_offset_from_marker_end=-200)
        found = journal._scan_back(path, total, {"phase_transition"})
        assert found["phase_transition"]["phase"] == "TEST"

    def test_straddling_orchestration_marker_sets_run_kind(self, tmp_path):
        path = tmp_path / "straddle-marker.jsonl"
        marker = {
            "v": 1,
            "type": "agent_handoff",
            "ts": STAMP,
            "agent": "preparer",
            "task_id": "1",
            "handoff": {"produced": "p"},
        }
        total = straddling_journal(path, marker, boundary_offset_from_marker_end=10)
        assert total > SUMMARY_TAIL_BYTES
        summary = journal.summarize_journal(
            path, now=datetime(2026, 1, 1, tzinfo=UTC)
        )
        assert summary.run_kind == "orchestrate"

    def test_journal_smaller_than_one_window_is_still_scanned(self, tmp_path):
        path = tmp_path / "small.jsonl"
        events = [
            {"v": 1, "type": "session_start", "ts": STAMP},
            {"v": 1, "type": "phase_transition", "ts": STAMP, "phase": "CODE"},
            {"v": 1, "type": "session_end", "ts": STAMP},
        ]
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        summary = journal.summarize_journal(path, now=datetime(2026, 1, 1, tzinfo=UTC))
        assert summary.last_phase == "CODE"
        assert summary.status == "ended"

    @pytest.mark.parametrize("windows_before", [1, 2, 3, 4])
    def test_marker_aligned_exactly_on_a_window_boundary_is_found(
        self, tmp_path, windows_before
    ):
        """A whole line starting exactly ON a boundary is not a fragment.

        Stripping it as if it were carries it out of the scan; at the
        outermost window there is no earlier window left to carry it into, so
        an aligned marker sitting at the budget edge vanished. The one-byte
        look-behind is what tells the two cases apart.
        """
        path = tmp_path / f"aligned-{windows_before}.jsonl"
        marker = {"v": 1, "type": "phase_transition", "ts": STAMP, "phase": "TEST"}
        encoded = json.dumps(marker, separators=(",", ":")).encode("utf-8") + b"\n"
        prefix = filler_bytes(128)  # one whole line, so the window start is > 0
        suffix = filler_bytes(windows_before * SUMMARY_TAIL_BYTES - len(encoded))
        path.write_bytes(prefix + encoded + suffix)
        size = len(prefix) + len(encoded) + len(suffix)
        assert size - windows_before * SUMMARY_TAIL_BYTES == len(prefix)
        found = journal._scan_back(path, size, {"phase_transition"})
        assert found["phase_transition"]["phase"] == "TEST"

    def test_marker_older_than_the_budget_stays_out_of_reach(self, tmp_path):
        """The 512KB budget still bounds the scan: older means invisible."""
        path = tmp_path / "beyond.jsonl"
        marker = marker_bytes({"v": 1, "type": "phase_transition", "ts": STAMP,
                               "phase": "TEST"})
        data = marker + filler_bytes(4 * SUMMARY_TAIL_BYTES + 4096)
        path.write_bytes(data)
        assert journal._scan_back(path, len(data), {"phase_transition"}) == {}


# --- 3. task_created keeps the board after the task store is reaped ---------


class TestTaskCreated:
    def test_task_created_is_classified_and_visual(self):
        raw = journal.RawEvent(
            seq=0,
            data={"v": 1, "type": "task_created", "ts": STAMP, "task_id": "7"},
        )
        event = classify.classify_event(raw)
        assert event is not None
        assert event["category"] == "task"
        assert classify.is_visual_type("task_created") is True
        assert classify.is_visual_type("handoff_emit_suppressed") is True

    def test_task_created_seeds_the_board_without_a_store(self, tmp_path):
        """The pin uses the shape the ONLY writer emits: task_id + optional
        team/subject. owner and phase are not written by any producer, so the
        branch must not read them (a fixture that invents them proves
        nothing)."""
        root = make_root(tmp_path)
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP, "project_dir": "/w/proj"},
                {
                    "v": 1,
                    "type": "task_created",
                    "ts": STAMP,
                    "task_id": "12",
                    "team": "session-abc12345",
                    "subject": "review the parser",
                },
            ],
        )
        payload = build_payload(root, "proj", "sess")
        assert len(payload["tasks"]) == 1
        task = payload["tasks"][0]
        assert task["id"] == "12"
        assert task["subject"] == "review the parser"
        assert task["status"] == "pending"
        assert task["owner"] == ""
        assert task["phase"] == ""

    def test_every_classified_type_is_registered_in_the_journal_ssot(self):
        """Derive the journal's type set from its own dicts, never restate it.

        A NEW registered type that the visualizer cannot classify would drop
        out of the feed with only a counted warning, so the direction that
        matters is registry -> visualizer. (The visualizer renders a few
        writer types the registry does not define; that is expected.)
        """
        from shared.session_journal import (
            _OPTIONAL_FIELDS_BY_TYPE,
            _REQUIRED_FIELDS_BY_TYPE,
        )

        registered = set(_REQUIRED_FIELDS_BY_TYPE) | set(_OPTIONAL_FIELDS_BY_TYPE)
        assert registered, "the journal registry parsed empty; the import is wrong"
        unclassified = registered - set(classify.EVENT_TYPE_INFO)
        assert not unclassified, (
            "journal event types the visualizer cannot classify: "
            f"{sorted(unclassified)}"
        )
        assert {"task_created", "handoff_emit_suppressed"} <= registered


# --- 4 & 7. the --project filter and the constrained project label ---------


def project_session(root: Path, slug: str, project_dir: str) -> None:
    write_journal(
        root,
        slug,
        "sess",
        [{"v": 1, "type": "session_start", "ts": STAMP, "project_dir": project_dir}],
    )


class TestProjectFilter:
    def test_project_filter_is_exact_not_a_substring(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "alpha", "/work/PACT-prompt")
        project_session(root, "beta", "/work/my-app")
        assert len(list_payload(root)["sessions"]) == 2
        assert list_payload(root, project_filter="PACT")["sessions"] == []

    @pytest.mark.parametrize(
        ("wanted", "expected_project"),
        [
            ("PACT-prompt", "PACT-prompt"),
            ("pact-PROMPT", "PACT-prompt"),
            ("my-app", "my-app"),
            ("MY-APP", "my-app"),
        ],
    )
    def test_exact_match_is_case_insensitive(self, tmp_path, wanted, expected_project):
        root = make_root(tmp_path)
        project_session(root, "alpha", "/work/PACT-prompt")
        project_session(root, "beta", "/work/my-app")
        payload = list_payload(root, project_filter=wanted)
        assert [row["project"] for row in payload["sessions"]] == [expected_project]

    def test_partial_name_does_not_match_a_longer_project(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "beta", "/work/my-app")
        assert list_payload(root, project_filter="app")["sessions"] == []

    def test_filter_matches_the_slug_when_it_differs_from_the_project(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "slug-name", "/work/other-dir")
        assert len(list_payload(root, project_filter="slug-name")["sessions"]) == 1

    @pytest.mark.parametrize("wanted", ["My App", "My_App", "my app", "my_app"])
    def test_raw_basename_query_finds_the_sanitized_label(self, tmp_path, wanted):
        """commands/visualize.md passes the RAW basename; it must still match.

        The label on the wire is sanitised to [A-Za-z0-9._-], so a directory
        named "My App" is listed as "My_App". Matching the exact rule only
        against the emitted label would make the documented launch path return
        an empty list with no error.
        """
        root = make_root(tmp_path)
        project_session(root, "unsafe", "/work/My App")
        payload = list_payload(root, project_filter=wanted)
        assert [row["project"] for row in payload["sessions"]] == ["My_App"]

    def test_absent_project_still_returns_nothing(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "alpha", "/work/PACT-prompt")
        assert list_payload(root, project_filter="nowhere")["sessions"] == []


class TestProjectLabelConstraint:
    def test_label_is_constrained_and_marked(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "unsafe", "/work/My App <b>")
        payload = list_payload(root)
        assert len(payload["sessions"]) == 1, "a session was dropped, not marked"
        row = payload["sessions"][0]
        assert re.fullmatch(r"[A-Za-z0-9._-]+", row["project"])
        assert row["project"] == "My_App__b_"
        assert row["project_altered"] is True

    def test_conforming_label_carries_no_marker(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "clean", "/work/PACT-prompt")
        row = list_payload(root)["sessions"][0]
        assert row["project"] == "PACT-prompt"
        assert "project_altered" not in row

    def test_model_meta_uses_the_same_constrained_label(self, tmp_path):
        """The run header/About/stage read meta.project, not the list entry."""
        root = make_root(tmp_path)
        project_session(root, "unsafe", "/work/My App <b>")
        meta = build_payload(root, "unsafe", "sess")["meta"]
        assert meta["project"] == "My_App__b_"
        assert meta["project_altered"] is True

    def test_model_meta_marks_nothing_when_the_label_conforms(self, tmp_path):
        root = make_root(tmp_path)
        project_session(root, "clean", "/work/PACT-prompt")
        meta = build_payload(root, "clean", "sess")["meta"]
        assert meta["project"] == "PACT-prompt"
        assert "project_altered" not in meta


# --- 5. one run-kind rule for the list and the model ------------------------


class TestRunKindAgreement:
    def test_team_directory_alone_does_not_promote_the_run(self, tmp_path):
        root = make_root(tmp_path)
        team_dir = root / "teams" / "session-abc12345"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({"members": []}))
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP,
                 "team": "session-abc12345"},
                {"v": 1, "type": "session_end", "ts": STAMP},
            ],
        )
        assert list_payload(root)["sessions"][0]["run_kind"] == "session"
        assert build_payload(root, "proj", "sess")["meta"]["run_kind"] == "session"

    def test_marker_makes_both_views_orchestrate(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP},
                {"v": 1, "type": "phase_transition", "ts": STAMP, "phase": "CODE"},
                {"v": 1, "type": "session_end", "ts": STAMP},
            ],
        )
        assert list_payload(root)["sessions"][0]["run_kind"] == "orchestrate"
        assert build_payload(root, "proj", "sess")["meta"]["run_kind"] == "orchestrate"

    def test_session_end_alone_marks_no_kind(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP},
                {"v": 1, "type": "session_end", "ts": STAMP},
            ],
        )
        assert "session_end" not in ORCHESTRATION_MARKER_TYPES
        assert list_payload(root)["sessions"][0]["run_kind"] == "session"


# --- 6. commit attribution counts agent-carrying commits too ----------------


class TestHealthIdentity:
    """Health names the build: the plugin's release and its root."""

    def _health(self, tmp_path: Path) -> dict:
        root = make_root(tmp_path)
        return model.build_health(
            roots_for(root),
            pid=4242,
            instance_id="deadbeef",
            uptime_s=1.5,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_health_carries_the_build_identity_and_no_launch_mode(self, tmp_path):
        payload = self._health(tmp_path)
        plugin_root = Path(__file__).resolve().parent.parent
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())
        assert payload["version"] == manifest["version"]
        assert payload["plugin_root"] == str(plugin_root)
        assert payload["demo_available"] is True
        assert not {"demo", "demo_mode", "is_demo", "mode"} & set(payload)


class TestSelectionMatcher:
    """One matcher serves the URL selection and --list: exact names first,
    case-insensitive only when every exact tier misses."""

    def _rows(self, tmp_path: Path, specs: list[tuple[str, str, str]]) -> Path:
        root = make_root(tmp_path)
        for slug, session_id, project_dir in specs:
            write_journal(root, slug, session_id, [
                census.session_start(STAMP, session_id=session_id, project_dir=project_dir),
            ])
        return root

    def _ids(self, root: Path, **filters) -> list[str]:
        return sorted(row["session_id"] for row in list_payload(root, **filters)["sessions"])

    def test_an_exact_name_beats_a_case_folded_one(self, tmp_path):
        root = self._rows(tmp_path, [("app", "sess-lower", "/w/app"),
                                     ("App", "sess-upper", "/w/App")])
        assert self._ids(root, project_filter="app") == ["sess-lower"]
        assert self._ids(root, project_filter="App") == ["sess-upper"]
        assert self._ids(root, project_filter="APP") == ["sess-lower", "sess-upper"]

    def test_the_slug_tier_wins_over_the_label_tier(self, tmp_path):
        root = self._rows(tmp_path, [("alpha", "sess-label", "/w/beta"),
                                     ("beta", "sess-slug", "/w/other")])
        assert self._ids(root, project_filter="beta") == ["sess-slug"]

    def test_a_session_id_matches_exactly_or_by_a_long_prefix(self, tmp_path):
        root = self._rows(tmp_path, [("p", "0123456789abcdef", "/w/p"),
                                     ("p", "0123456700000000", "/w/p")])
        assert self._ids(root, session_filter="0123456789abcdef") == ["0123456789abcdef"]
        assert self._ids(root, session_filter="01234567") == [
            "0123456700000000", "0123456789abcdef"
        ]
        assert self._ids(root, session_filter="012345678") == ["0123456789abcdef"]
        assert self._ids(root, session_filter="0123456") == []


class TestCommitAttribution:
    def test_commit_with_its_own_agent_is_counted(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP},
                {"v": 1, "type": "agent_dispatch", "ts": STAMP, "agent": "backend-coder",
                 "task_id": "1", "phase": "CODE"},
                {"v": 1, "type": "commit", "ts": STAMP, "sha": "abc1234",
                 "message": "m", "phase": "CODE", "agent": "qa-engineer"},
            ],
        )
        agents = {row["name"]: row for row in build_payload(root, "proj", "sess")["agents"]}
        assert agents["qa-engineer"]["stats"]["commits"] == 1
        assert agents["backend-coder"]["stats"]["commits"] == 0

    def test_agent_less_commit_uses_the_same_phase_dispatch(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(
            root,
            "proj",
            "sess",
            [
                {"v": 1, "type": "session_start", "ts": STAMP},
                {"v": 1, "type": "agent_dispatch", "ts": STAMP, "agent": "backend-coder",
                 "task_id": "1", "phase": "CODE"},
                {"v": 1, "type": "commit", "ts": STAMP, "sha": "abc1234",
                 "message": "m", "phase": "CODE"},
            ],
        )
        agents = {row["name"]: row for row in build_payload(root, "proj", "sess")["agents"]}
        assert agents["backend-coder"]["stats"]["commits"] == 1


# --- run status: the newest lifecycle event plus an activity clock ----------


_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_LIVE_NOW = _T0.replace(minute=1)
_IDLE_NOW = _T0.replace(hour=13)


def _ts(second: int) -> str:
    return _T0.replace(second=second).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pin_mtime(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def _lifecycle_journal(root: Path, kinds: list[str]) -> Path:
    events = []
    for index, kind in enumerate(kinds):
        ts = _ts(index)
        if kind.startswith("start"):
            source = kind.partition(":")[2] or "startup"
            events.append(census.session_start(ts, source=source))
        elif kind == "end":
            events.append(census.bare_session_end(ts))
        elif kind == "wrapup":
            events.append(census.wrapup_end(ts))
        elif kind == "cleanup":
            events.append(census.cleanup_summary(ts))
        elif kind == "paused":
            events.append(census.session_paused(ts))
        elif kind == "refreshed":
            events.append(census.session_refreshed(ts))
        elif kind == "work":
            events.append(census.phase_transition(ts, "CODE", "started"))
        else:  # pragma: no cover - a typo in a case table
            raise AssertionError(kind)
    path = write_journal(root, "proj", "sess", events)
    _pin_mtime(path, _T0)
    return path


def _both_views(root: Path, now: datetime) -> tuple[tuple, tuple]:
    ref = discovery.find_session(roots_for(root), "proj", "sess")
    meta = model.build_model(ref, now=now)["meta"]
    row = model.build_sessions_list(roots_for(root), now=now, show_all=True)["sessions"][0]
    return (meta["status"], meta["ended_by"]), (row["status"], row["ended_by"])


_ENDED = ("ended", "session_end")
_CLOCK = {_LIVE_NOW: ("live", None), _IDLE_NOW: ("ended", "idle")}

# Lifecycle orders seen in real journals, plus the trailer and pause cases.
_LIFECYCLE_CASES = {
    "start-end": (["start", "work", "end"], _ENDED),
    "start-only": (["start", "work"], None),
    "two-ends": (["start", "work", "end", "end"], _ENDED),
    "resumed-no-end": (["start", "work", "end", "start:resume"], None),
    "compacted-then-ended": (["start", "end", "start:compact", "work", "end"], _ENDED),
    "end-without-start": (["end"], _ENDED),
    "end-then-cleanup": (["start", "work", "end", "cleanup"], _ENDED),
    "end-then-more-work": (["start", "end", "work"], None),
    "wrapup": (["start", "work", "wrapup", "end", "cleanup"], _ENDED),
    "paused": (["start", "work", "paused", "end"], ("paused", "session_end")),
    "refreshed-after-pause": (
        ["start", "paused", "refreshed", "end"], ("refreshed", "session_end")
    ),
    "pause-before-resume": (
        ["start", "paused", "end", "start:resume", "work", "end"], _ENDED
    ),
}


class TestRunStatus:
    @pytest.mark.parametrize("now", [_LIVE_NOW, _IDLE_NOW], ids=["recent", "stale"])
    @pytest.mark.parametrize("case", sorted(_LIFECYCLE_CASES))
    def test_model_and_list_agree_on_every_lifecycle_order(self, tmp_path, case, now):
        kinds, expected = _LIFECYCLE_CASES[case]
        root = make_root(tmp_path)
        _lifecycle_journal(root, kinds)
        expected = expected or _CLOCK[now]
        model_view, list_view = _both_views(root, now)
        assert model_view == expected, case
        assert list_view == expected, case

    def test_the_live_window_is_inclusive(self):
        lifecycle = journal.lifecycle_of_events([{"type": "session_start"}])
        edge = journal.LIVE_WINDOW_S
        assert journal.run_status(lifecycle, 1000.0, 1000.0 + edge) == ("live", None)
        assert journal.run_status(lifecycle, 1000.0, 1000.0 + edge + 1) == ("ended", "idle")

    def test_a_task_store_write_keeps_a_quiet_journal_live(self, tmp_path):
        """The activity clock reads the run's task files, not only the journal."""
        config_root = make_root(tmp_path)
        _lifecycle_journal(config_root, ["start", "work"])
        task = census.write_task_file(
            config_root, "session-abc12345", "3", subject="backend-coder: build", status="in_progress"
        )
        _pin_mtime(task.parent, _T0)
        # Control: with the task file as old as the journal the run is idle.
        _pin_mtime(task, _T0)
        quiet = _IDLE_NOW.replace(minute=59, second=30)
        assert _both_views(config_root, quiet) == (("ended", "idle"), ("ended", "idle"))
        _pin_mtime(task, _IDLE_NOW.replace(minute=59))
        model_view, list_view = _both_views(config_root, quiet)
        assert model_view == ("live", None)
        assert list_view == ("live", None)
        assert model.build_model(
            discovery.find_session(roots_for(config_root), "proj", "sess"),
            now=_IDLE_NOW.replace(minute=59, second=30),
        )["meta"]["activity_at"] == _IDLE_NOW.replace(minute=59).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_an_end_beyond_the_scan_budget_still_agrees(self, tmp_path):
        """A session_end farther from EOF than the summary can scan has work
        after it, so both views fall back to the clock."""
        root = make_root(tmp_path)
        events = [census.session_start(_ts(0)), census.bare_session_end(_ts(1))]
        filler = census.event("lifecycle_decision", _ts(2), tool_name="X", advisories_count=0,
                              rules=[], advisories=[], verdict="allow", pad="x" * 400)
        events += [filler] * ((4 * SUMMARY_TAIL_BYTES) // 480 + 50)
        path = write_journal(root, "proj", "sess", events)
        _pin_mtime(path, _T0)
        model_view, list_view = _both_views(root, _IDLE_NOW)
        assert model_view == list_view == ("ended", "idle")


# --- tasks: identity by id plus occupant, a closed status set, alerts, gates --

_TEAM = "session-abc12345"


def _t(second: int) -> str:
    return f"2026-01-01T00:00:{second:02d}Z"


def _task_payload(root: Path, events: list[dict]) -> dict:
    write_journal(root, "proj", "sess", [census.session_start(_t(0), team=_TEAM)] + events)
    return build_payload(root, "proj", "sess")


def _by_key(payload: dict) -> dict:
    return {task["key"]: task for task in payload["tasks"]}


class TestTaskIdentity:
    """A card is a subject segment of a task id: a store drain reuses ids,
    and a new subject after a restart is a new task. The occupant hash also
    changes when a task gets its owner, which must never split a card."""

    def test_an_owner_assignment_on_the_same_subject_keeps_one_card(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "3", subject="architect: design", occupant="o-unowned"),
            census.task_snapshot(_t(2), "3", subject="architect: design", occupant="o-owned",
                                 owner="architect"),
            census.agent_dispatch(_t(3), "architect", "3", "ARCHITECT"),
        ])
        cards = _by_key(payload)
        assert list(cards) == ["3:o-unowned"]
        assert cards["3:o-unowned"]["status"] == "active"

    def test_a_new_subject_after_a_restart_is_a_second_card(self, tmp_path):
        """The restart is the drain boundary: a record after it, before the
        new subject's first snapshot, already belongs to the new task."""
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "3", subject="architect: design", occupant="occA",
                                 owner="architect"),
            census.session_start(_t(2), source="resume", team=_TEAM),
            census.agent_dispatch(_t(3), "backend-coder", "3", "CODE"),
            census.task_snapshot(_t(4), "3", subject="backend-coder: build", occupant="occB",
                                 owner="backend-coder"),
        ])
        cards = _by_key(payload)
        assert set(cards) == {"3:occA", "3:occB"}
        assert cards["3:occA"]["subject"] == "architect: design"
        assert cards["3:occA"]["status"] == "pending"
        assert cards["3:occB"]["subject"] == "backend-coder: build"
        assert cards["3:occB"]["status"] == "active"

    def test_without_a_restart_the_new_card_starts_at_its_first_snapshot(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "3", subject="architect: design", occupant="occA"),
            census.agent_dispatch(_t(2), "architect", "3", "ARCHITECT"),
            census.task_snapshot(_t(3), "3", subject="backend-coder: build", occupant="occB"),
            census.agent_handoff(_t(4), "backend-coder", "3", "backend-coder: build",
                                 census.full_handoff()),
        ])
        cards = _by_key(payload)
        assert cards["3:occA"]["status"] == "active"
        assert cards["3:occB"]["status"] == "handoff"
        assert [row["task_key"] for row in payload["handoffs"]] == ["3:occB"]

    def test_a_record_before_the_first_snapshot_joins_the_first_card(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.event("task_created", _t(1), task_id="4", team=_TEAM, subject="qa: verify"),
            census.agent_dispatch(_t(2), "qa-engineer", "4", "TEST"),
            census.task_snapshot(_t(3), "4", subject="qa: verify", occupant="occQ"),
        ])
        cards = _by_key(payload)
        assert list(cards) == ["4:occQ"]
        assert cards["4:occQ"]["status"] == "active"

    def test_the_live_task_file_joins_the_newest_card(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "3", subject="backend-coder: build",
                               status="completed")
        payload = _task_payload(root, [
            census.task_snapshot(_t(1), "3", subject="architect: design", occupant="occA"),
            census.session_start(_t(2), source="resume", team=_TEAM),
            census.task_snapshot(_t(3), "3", subject="backend-coder: build", occupant="occB"),
        ])
        cards = _by_key(payload)
        assert cards["3:occB"]["status"] == "completed"
        assert cards["3:occA"]["status"] == "pending"

    def test_a_blank_subject_snapshot_is_no_change_of_task(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "7", subject="architect: design", occupant="occA"),
            census.task_snapshot(_t(2), "7", subject="", occupant="occBlank"),
            census.agent_dispatch(_t(3), "architect", "7", "ARCHITECT"),
        ])
        cards = _by_key(payload)
        assert list(cards) == ["7:occA"]
        assert cards["7:occA"]["subject"] == "architect: design"
        assert cards["7:occA"]["status"] == "active"

    def test_a_subject_that_returns_gets_a_distinct_key(self, tmp_path):
        """Subjects A, B, A on one id are three cards; the third would reuse
        the first card's key, so it is suffixed with its segment index."""
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "6", subject="A", occupant="occX"),
            census.task_snapshot(_t(2), "6", subject="B", occupant="occY"),
            census.task_snapshot(_t(3), "6", subject="A", occupant="occX"),
        ])
        keys = [task["key"] for task in payload["tasks"]]
        assert len(keys) == len(set(keys)) == 3
        assert keys[:2] == ["6:occX", "6:occY"]
        assert keys[2].startswith("6:occX:")

    def test_an_id_with_no_snapshot_at_all_has_the_empty_occupant(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.event("task_created", _t(1), task_id="5", team=_TEAM, subject="qa: verify"),
            census.agent_dispatch(_t(2), "qa-engineer", "5", "TEST"),
        ])
        assert list(_by_key(payload)) == ["5:"]

    def test_cards_are_ordered_by_numeric_id(self, tmp_path):
        root = make_root(tmp_path)
        for task_id in ("10", "2", "9"):
            census.write_task_file(root, _TEAM, task_id, subject=f"task {task_id}")
        payload = _task_payload(root, [])
        assert [task["id"] for task in payload["tasks"]] == ["2", "9", "10"]

    def test_the_task_cap_keeps_the_newest(self, tmp_path, monkeypatch):
        root = make_root(tmp_path)
        for task_id in ("1", "2", "10", "11"):
            census.write_task_file(root, _TEAM, task_id, subject=f"task {task_id}")
        monkeypatch.setattr(model, "MAX_TASKS", 2)
        payload = _task_payload(root, [])
        assert [task["id"] for task in payload["tasks"]] == ["10", "11"]


    def test_the_cap_never_drops_a_signal_task(self, tmp_path, monkeypatch):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "1", subject="blocked", metadata={"type": "blocker"})
        for task_id in ("2", "10", "11"):
            census.write_task_file(root, _TEAM, task_id, subject=f"task {task_id}")
        monkeypatch.setattr(model, "MAX_TASKS", 2)
        ids = [task["id"] for task in _task_payload(root, [])["tasks"]]
        assert "1" in ids
        assert "11" in ids


class TestTaskStatus:
    _CLOSED = {"pending", "active", "handoff", "completed"}

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [("pending", "pending"), ("in_progress", "active"), ("completed", "completed")],
    )
    def test_platform_statuses_map_into_the_closed_set(self, tmp_path, platform, expected):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "5", subject="s", status=platform)
        assert _by_key(_task_payload(root, []))["5:"]["status"] == expected

    def test_a_deleted_task_has_no_card(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "5", subject="s", status="deleted")
        assert _task_payload(root, [])["tasks"] == []

    def test_journal_evidence_raises_but_never_lowers_status(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "6", subject="s", status="completed")
        census.write_task_file(root, _TEAM, "7", subject="s", status="pending")
        payload = _task_payload(root, [
            census.agent_dispatch(_t(1), "preparer", "6", "PREPARE"),
            census.agent_dispatch(_t(2), "preparer", "7", "PREPARE"),
        ])
        cards = _by_key(payload)
        assert cards["6:"]["status"] == "completed"
        assert cards["7:"]["status"] == "active"
        assert {task["status"] for task in payload["tasks"]} <= self._CLOSED

    def test_free_text_checkpoint_status_is_a_note_not_a_task_status(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.event("task_created", _t(1), task_id="8", team=_TEAM, subject="s"),
            census.event("checkpoint", _t(2), phase="CODE", completed_phases=[],
                         pending_phases=["TEST"], safe_to_retry=True, variety=None,
                         active_agents=[{"name": "auditor", "status": "observing a2ea5a0",
                                         "task_id": "8"}]),
        ])
        assert _by_key(payload)["8:"]["status"] == "pending"
        agent = payload["checkpoints"][0]["active_agents"][0]
        assert agent["note"] == "observing a2ea5a0"
        assert "status" not in agent


class TestAlertsFromSignalTasks:
    def test_an_algedonic_task_is_an_alert_in_the_contract_shape(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(
            root, _TEAM, "12", subject="HALT: credential in fixture",
            metadata={"type": "algedonic", "level": "HALT", "category": "META-BLOCK",
                      "resolution": {"by": "lead", "note": "rotated"}},
        )
        alerts = _task_payload(root, [])["alerts"]
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["kind"] == "algedonic"
        assert alert["level"] == "halt"
        assert alert["category"] == "META-BLOCK"
        assert alert["category_key"] == "meta-block"
        assert alert["summary"] == "HALT: credential in fixture"
        assert alert["task_id"] == "12"
        assert alert["task_key"] == "12:"
        assert alert["resolved"] is True
        assert json.loads(alert["resolution"]) == {"by": "lead", "note": "rotated"}

    def test_a_blocker_defaults_to_alert_level_and_resolves_on_completion(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "13", subject="blocked on env", status="completed",
                               metadata={"type": "blocker", "category": "test-environment"})
        alert = _task_payload(root, [])["alerts"][0]
        assert alert["kind"] == "blocker"
        assert alert["level"] == "alert"
        assert alert["resolved"] is True
        assert alert["resolution"] is None

    def test_a_drained_signal_task_is_found_in_its_snapshot(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(5), "14", subject="memory cli broken", occupant="o14",
                                 task_type="blocker",
                                 metadata={"type": "blocker", "category": "memory-cli"}),
        ])
        alert = payload["alerts"][0]
        assert alert["task_key"] == "14:o14"
        assert alert["category_key"] == "memory-cli"
        assert alert["resolved"] is False
        assert alert["ts"] == _t(5)
        assert isinstance(alert["seq"], int)

    def test_a_journal_signal_event_maps_to_the_same_shape(self, tmp_path):
        """algedonic_signal has no writer today; it is accepted forward-
        compatibly and must come out in the task alerts' shape."""
        payload = _task_payload(make_root(tmp_path), [
            census.event("algedonic_signal", _t(6), level="HALT", category="Security",
                         summary="token in a log"),
        ])
        alert = payload["alerts"][0]
        assert alert["kind"] == "algedonic"
        assert alert["level"] == "halt"
        assert alert["category"] == "Security"
        assert alert["category_key"] == "security"
        assert alert["summary"] == "token in a log"

    def test_an_ordinary_task_is_not_an_alert(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "15", subject="feature", metadata={"type": "feature"})
        assert _task_payload(root, [])["alerts"] == []


class TestTeachbackGate:
    """A teachback_ack is the lead's acceptance: the hook writes it when the
    lead completes the gate. It completes its own card and never the work."""

    def test_the_ack_completes_its_gate_and_leaves_the_work_alone(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "20", subject="test-engineer: TEACHBACK for review",
                               owner="test-engineer", blocks=["21"])
        census.write_task_file(root, _TEAM, "21", subject="test-engineer: review",
                               owner="test-engineer")
        payload = _task_payload(root, [census.teachback_ack(_t(1), "20")])
        cards = _by_key(payload)
        assert cards["20:"]["kind"] == "gate"
        assert cards["20:"]["status"] == "completed"
        assert cards["21:"]["teachback"] is True
        assert cards["21:"]["status"] == "pending"

    def test_an_ack_makes_a_gate_even_without_a_teachback_subject(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [census.teachback_ack(_t(1), "30")])
        assert [(task["key"], task["kind"], task["status"]) for task in payload["tasks"]] == [
            ("30:", "gate", "completed")
        ]

    def test_an_ack_on_a_signal_task_keeps_its_signal_kind(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "31", subject="blocked on env",
                               metadata={"type": "blocker", "category": "test-environment"})
        payload = _task_payload(root, [census.teachback_ack(_t(1), "31")])
        assert _by_key(payload)["31:"]["kind"] == "blocker"
        assert len(payload["alerts"]) == 1

    def test_an_ack_after_a_signal_snapshot_keeps_the_signal_kind(self, tmp_path):
        """The journal order: the snapshot names the blocker, then the ack."""
        payload = _task_payload(make_root(tmp_path), [
            census.task_snapshot(_t(1), "33", subject="blocked on env", occupant="o33",
                                 task_type="blocker", metadata={"type": "blocker"}),
            census.teachback_ack(_t(2), "33"),
        ])
        assert _by_key(payload)["33:o33"]["kind"] == "blocker"

    def test_a_teachback_subject_alone_is_a_gate(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "32", subject="architect: TEACHBACK for design")
        card = _by_key(_task_payload(root, []))["32:"]
        assert card["kind"] == "gate"
        assert card["status"] == "pending"

class TestTeachbackAckAgent:
    """The ack carries no agent; the viewer credits the gate task's owner."""

    def test_the_ack_is_credited_to_the_gate_owner(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "20", subject="test-engineer: TEACHBACK for review",
                               owner="test-engineer")
        payload = _task_payload(root, [census.teachback_ack(_t(1), "20")])
        ack = [event for event in payload["events"] if event["type"] == "teachback_ack"]
        assert [event["agent"] for event in ack] == ["test-engineer"]

    def test_an_ack_for_an_unknown_gate_names_no_agent(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [census.teachback_ack(_t(1), "99")])
        ack = [event for event in payload["events"] if event["type"] == "teachback_ack"]
        assert [event["agent"] for event in ack] == [None]


# --- events carry the agent and phase family the stage needs -----------------


def _classified(event: dict) -> dict:
    result = classify.classify_event(
        journal.RawEvent(seq=1, data=event), {"project": "P", "team": _TEAM, "cloud": False}
    )
    assert result is not None, event["type"]
    return result


def _events_of(payload: dict, event_type: str) -> list[dict]:
    return [event for event in payload["events"] if event["type"] == event_type]


class TestPhaseFamily:
    @pytest.mark.parametrize(
        ("phase", "family"),
        [
            ("PREPARE", "PREPARE"), ("PLAN", "PREPARE"), ("INVESTIGATE", "PREPARE"),
            ("ARCHITECT", "ARCHITECT"),
            ("CODE", "CODE"), ("code", "CODE"), ("EXECUTE", "CODE"),
            ("OPERATOR-DEV", "CODE"), ("OPERATOR_DEV", "CODE"), ("deploy", "CODE"),
            ("TEST", "TEST"), ("test", "TEST"), ("QA", "TEST"), ("VERIFY", "TEST"),
            ("REVIEW", "REVIEW"), ("REVIEW-REMEDIATION", "REVIEW"),
            ("REVIEW_REMEDIATION", "REVIEW"), ("peer-review", "REVIEW"),
            ("ATOMIZE", "OTHER"), ("", "OTHER"), (None, "OTHER"),
        ],
    )
    def test_each_real_phase_maps_to_its_room(self, phase, family):
        assert model.phase_family(phase) == family

    def test_an_event_without_a_phase_takes_the_current_one(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.review_finding(_t(1), "minor", "early", "reviewer-a", "9"),
            census.phase_transition(_t(2), "peer-review", "started"),
            census.review_finding(_t(3), "minor", "late", "reviewer-a", "9"),
            census.agent_dispatch(_t(4), "backend-coder", "10", "CODE"),
        ])
        findings = _events_of(payload, "review_finding")
        assert [event["phase_family"] for event in findings] == ["OTHER", "REVIEW"]
        assert _events_of(payload, "agent_dispatch")[0]["phase_family"] == "CODE"


class TestEventAgents:
    def test_a_finding_is_credited_to_its_reviewer(self):
        event = _classified(census.review_finding(_t(1), "blocking", "x", "cold-reviewer", "9"))
        assert event["agent"] == "cold-reviewer"

    def test_a_dispatch_decision_is_credited_to_the_spawned_name(self):
        assert _classified(census.dispatch_decision(_t(1), "architect"))["agent"] == "architect"
        assert _classified(census.dispatch_decision(_t(1), None))["agent"] is None

    def test_a_review_dispatch_lists_its_reviewers(self):
        event = _classified(census.review_dispatch(_t(1), ["reviewer-a", "cold-reviewer"]))
        assert event["agents"] == ["reviewer-a", "cold-reviewer"]

    def test_every_reviewer_seen_joins_the_crew(self, tmp_path):
        payload = _task_payload(make_root(tmp_path), [
            census.review_dispatch(_t(1), ["reviewer-a", "cold-reviewer"]),
            census.review_finding(_t(2), "minor", "x", "cursor-bugbot", "9"),
        ])
        names = {agent["name"] for agent in payload["agents"]}
        assert {"reviewer-a", "cold-reviewer", "cursor-bugbot"} <= names

    def test_a_dispatch_site_names_its_task_and_its_owner(self, tmp_path):
        root = make_root(tmp_path)
        census.write_task_file(root, _TEAM, "12", subject="backend-coder: build",
                               owner="backend-coder")
        payload = _task_payload(root, [
            census.event("dispatch_site", _t(1), task_id="12",
                         variety={"novelty": 1, "scope": 2, "uncertainty": 3, "risk": 3,
                                  "total": 9}),
            census.event("dispatch_site", _t(2), task_id="77"),
        ])
        known, unknown = _events_of(payload, "dispatch_site")
        assert known["title"] == "Dispatch site · task #12"
        assert known["detail"] == "variety 9"
        assert known["agent"] == "backend-coder"
        assert unknown["agent"] is None


class TestSessionEndKinds:
    def test_a_bare_session_end_is_a_muted_process_exit(self):
        event = _classified(census.bare_session_end(_t(1)))
        assert (event["category"], event["icon"], event["severity"]) == (
            "session", "session", "muted"
        )
        assert event["title"] == "Session process ended"
        assert not event.get("wrapup")

    @pytest.mark.parametrize("state", ["MERGED", "OPEN", "NONE"])
    def test_a_wrapup_record_is_a_pr_mark(self, state):
        event = _classified(census.wrapup_end(
            _t(1), pr_state=state, pr_number=None if state == "NONE" else 7
        ))
        assert (event["category"], event["icon"]) == ("pr", "pr")
        assert event["wrapup"] is True

    def test_a_wrapup_without_a_pr_says_so(self):
        event = _classified(census.wrapup_end(_t(1), pr_state="UNKNOWN", pr_number=None))
        assert "UNKNOWN" not in event["detail"]
        assert "no PR" in event["detail"]


class TestRealWriterFields:
    def test_a_commit_shows_its_note(self):
        event = _classified(census.event("commit", _t(1), sha="abc1234", message="m",
                                         phase="CODE", note="squashed review fixes"))
        assert event["detail"] == "squashed review fixes"

    def test_pause_and_refresh_show_their_worktree_fields(self):
        paused = _classified(census.session_paused(_t(1)))
        refreshed = _classified(census.session_refreshed(_t(1)))
        assert "/work/.worktrees/feature" in paused["detail"]
        assert "/work/.worktrees/feature" in refreshed["detail"]

    def test_only_scope_dispatch_makes_a_dispatch_variety_title(self):
        variety = {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 2, "total": 8}
        feature = _classified(census.event("variety_assessed", _t(1), task_id="1",
                                           variety=variety))
        dispatch = _classified(census.event("variety_assessed", _t(1), task_id="1",
                                            scope="dispatch", variety=variety))
        assert not feature["title"].startswith("Dispatch variety")
        assert dispatch["title"].startswith("Dispatch variety")

    def test_an_old_score_shaped_dimension_is_read(self):
        assert normalize_variety({"novelty": {"score": 3, "rationale": "x"},
                                   "scope": {"score": 2, "rationale": "y"}}) == {
            "novelty": 3, "scope": 2, "uncertainty": 0, "risk": 0, "total": 5,
        }

    def test_a_pin_marker_write_is_classified(self):
        event = _classified(census.event("pin_marker_write", _t(1), route="skill",
                                         command="pin", outcome="written"))
        assert event["category"] == "memory"

    def test_a_skipped_prune_has_no_heading_and_names_its_outcome(self):
        event = _classified(census.event("pin_prune_skipped", _t(1), outcome="skipped",
                                         pin_count=3, age_distribution={}, reason="none old"))
        assert "skipped" in event["title"]
        assert "None" not in event["title"]

    def test_a_warn_gate_decision_is_a_warning(self):
        assert _classified(census.dispatch_decision(_t(1), "a", "WARN"))["severity"] == "warning"
        assert _classified(census.dispatch_decision(_t(1), "a", "DENY"))["severity"] != "success"
