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
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visualizer import classify, discovery, journal, model
from visualizer.settings import (
    ORCHESTRATION_MARKER_TYPES,
    SUMMARY_TAIL_BYTES,
    RootSpec,
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
    return model.build_sessions_list(roots_for(root), **filters)


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


class TestHealthDemoShape:
    """`demo` is the launch MODE; `demo_available` is the capability."""

    def _health(self, tmp_path: Path, **kwargs) -> dict:
        root = make_root(tmp_path)
        return model.build_health(
            roots_for(root),
            pid=4242,
            instance_id="deadbeef",
            uptime_s=1.5,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            **kwargs,
        )

    def test_real_launch_does_not_claim_the_demo_mode(self, tmp_path):
        payload = self._health(tmp_path)
        assert payload["demo"] is False
        assert payload["demo_available"] is True

    def test_demo_launch_reports_the_demo_mode(self, tmp_path):
        payload = self._health(tmp_path, demo_mode=True)
        assert payload["demo"] is True
        assert payload["demo_available"] is True

    def test_the_capability_field_is_the_only_addition(self, tmp_path):
        payload = self._health(tmp_path)
        assert {"demo", "demo_available"} <= set(payload)
        assert not {"demo_mode", "is_demo", "mode"} & set(payload)


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
