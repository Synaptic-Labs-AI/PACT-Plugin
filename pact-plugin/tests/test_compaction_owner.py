"""Tests for hooks/shared/compaction_owner.py.

An in-process teammate's compaction frames are lead-shaped, so the verdict comes
from the transcripts. Every arm builds a temporary projects/ tree under the
config root the autouse fixture redirects to, and drives the predicate with a
fake clock: `now` is read once at entry, `monotonic` advances only when `sleep`
is called, and a callback can append records on the Nth sleep.
"""

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures.role_frames import (
    captured_compaction_lead_postcompact,
    captured_compaction_lead_precompact,
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_postcompact,
    captured_compaction_teammate_precompact,
    captured_compaction_teammate_sessionstart,
)
from shared import compaction_owner as co

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
ENTRY = datetime(2026, 9, 14, 1, 24, 47, tzinfo=timezone.utc)
SID = "4ec31948-bbe5-4ef4-841c-631d1ef31e61"
BODY = (
    "1. Primary Request and Intent: read three short text files in a scratch project "
    "and report their line counts to the team-lead.\n2. Key Technical Concepts: reading "
    "files and counting lines.\n3. Current Work: the counts were gathered and sent.\n"
    "4. Pending Tasks: none."
)
SUMMARY = f"<analysis>\nWorking notes that are not kept.\n</analysis>\n\n<summary>\n{BODY}\n</summary>"


def _iso(moment):
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Clock:
    """now() is fixed at ENTRY; monotonic() advances only through sleep()."""

    def __init__(self, on_sleep=None, limit=200):
        self.t = 1000.0
        self.sleeps = 0
        self.slept = 0.0
        self.on_sleep = on_sleep or {}
        self.limit = limit

    def now(self):
        return ENTRY

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > self.limit:
            raise AssertionError("the predicate did not stop polling")
        self.slept += seconds
        self.t += seconds
        action = self.on_sleep.get(self.sleeps)
        if action:
            action()


class Tree:
    """projects/<slug>/<sid>.jsonl and <sid>/subagents/agent-*.jsonl under a root."""

    def __init__(self, root):
        self.project = root / "projects" / "-scratch-cmp-lead"
        self.subagents = self.project / SID / "subagents"
        self.subagents.mkdir(parents=True)
        self.lead = self.project / f"{SID}.jsonl"
        self.teammate = self.subagents / "agent-acmp-probe-0123456789abcdef.jsonl"
        for path in (self.lead, self.teammate):
            self.append(path, {"type": "user", "message": {"role": "user", "content": "earlier turn"},
                               "timestamp": _iso(ENTRY - timedelta(hours=1))})

    @staticmethod
    def append(path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def boundary(self, path, at=ENTRY):
        self.append(path, {"type": "system", "subtype": "compact_boundary",
                           "content": "Conversation compacted", "timestamp": _iso(at)})

    def summary(self, path, at=ENTRY, body=BODY):
        self.append(path, {"type": "user", "isCompactSummary": True, "timestamp": _iso(at),
                           "message": {"role": "user",
                                       "content": "This session is being continued from a previous conversation.\n" + body}})

    def other_message(self, path, at=ENTRY):
        self.append(path, {"type": "user", "timestamp": _iso(at),
                           "message": {"role": "user", "content": "a message from the secretary"}})

    def frame(self, event, summary=SUMMARY):
        frame = {"agent_type": "PACT:pact-orchestrator", "session_id": SID, "transcript_path": str(self.lead)}
        if event == "PostCompact":
            frame.update(hook_event_name="PostCompact", compact_summary=summary, trigger="auto")
        else:
            frame.update(hook_event_name="SessionStart", source="compact")
        return frame


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path / ".claude")


def attribute(frame, clock):
    return co.attribute_compaction(frame, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)


# --------------------------------------------------------------------------
# PostCompact: attribution by content
# --------------------------------------------------------------------------


def test_postcompact_teammate_content_match(tree):
    tree.boundary(tree.teammate)
    tree.summary(tree.teammate)
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.TEAMMATE, co.CONTENT)


def test_postcompact_lead_content_match(tree):
    tree.boundary(tree.lead)
    tree.summary(tree.lead)
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.LEAD, co.CONTENT)


@pytest.mark.parametrize("body_side, expected", [("teammate", co.TEAMMATE), ("lead", co.LEAD)])
def test_both_boundaries_fresh_the_body_decides(tree, body_side, expected):
    tree.boundary(tree.lead)
    tree.boundary(tree.teammate)
    tree.summary(tree.teammate if body_side == "teammate" else tree.lead)
    tree.other_message(tree.lead if body_side == "teammate" else tree.teammate)
    assert attribute(tree.frame("PostCompact"), Clock()) == (expected, co.CONTENT)


def test_a_record_that_appears_after_two_sleeps_is_found(tree):
    def write():
        tree.boundary(tree.teammate)
        tree.summary(tree.teammate)

    clock = Clock(on_sleep={2: write})
    assert attribute(tree.frame("PostCompact"), clock) == (co.TEAMMATE, co.CONTENT)
    assert clock.sleeps == 2


def test_the_analysis_block_is_not_part_of_the_match(tree):
    tree.boundary(tree.teammate)
    tree.summary(tree.teammate)  # the transcript holds the <summary> body only
    assert "<analysis>" in SUMMARY
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.TEAMMATE, co.CONTENT)


def test_a_raw_body_with_a_newline_run_matches_its_rendered_record(tree):
    """The transcript renders a summary as "Summary:" plus the body stripped, with
    runs of newlines collapsed. A raw body with a three-newline run and edge
    whitespace inside its tags still matches that record."""
    raw_body = "\n\n  " + BODY.replace("\n2.", "\n\n\n2.") + "\n\n\n"
    summary = f"<analysis>\nnotes\n</analysis>\n\n<summary>{raw_body}</summary>"
    rendered = "Summary:\n" + re.sub(r"\n\n+", "\n\n", raw_body.strip())
    assert raw_body.strip() not in rendered, "the raw body must differ from its rendered form"
    tree.boundary(tree.teammate)
    tree.append(tree.teammate, {"type": "user", "isCompactSummary": True, "timestamp": _iso(ENTRY),
                                "message": {"role": "user",
                                            "content": "This session is being continued.\n\n" + rendered}})
    assert attribute(tree.frame("PostCompact", summary=summary), Clock()) == (co.TEAMMATE, co.CONTENT)


def test_the_match_ignores_order_and_the_summary_timestamp(tree):
    """The lead capture's shape: the boundary, then another agent's message,
    then the summary record, stamped 0.72 s BEFORE its boundary."""
    tree.boundary(tree.teammate, at=ENTRY)
    tree.other_message(tree.teammate, at=ENTRY + timedelta(milliseconds=30))
    tree.summary(tree.teammate, at=ENTRY - timedelta(milliseconds=720))
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.TEAMMATE, co.CONTENT)


def test_a_summary_stamped_outside_the_window_still_matches_a_fresh_boundary(tree):
    tree.summary(tree.teammate, at=ENTRY - timedelta(seconds=co.BACK_S + 7))
    tree.boundary(tree.teammate, at=ENTRY)
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.TEAMMATE, co.CONTENT)


@pytest.mark.parametrize("summary", [
    "no summary block at all",
    "<analysis>notes</analysis><summary>too short to match</summary>",
])
def test_no_usable_summary_falls_back_to_timing(tree, summary):
    tree.boundary(tree.teammate)
    tree.summary(tree.teammate, body="too short to match")
    clock = Clock()
    assert attribute(tree.frame("PostCompact", summary=summary), clock) == (co.TEAMMATE, co.TIMING)
    assert clock.slept >= co.T_POSTCOMPACT


def test_postcompact_with_no_match_ends_at_its_deadline(tree):
    clock = Clock()
    assert attribute(tree.frame("PostCompact"), clock) == (co.UNKNOWN, co.DEADLINE)
    assert co.T_POSTCOMPACT <= clock.slept <= co.T_POSTCOMPACT + co.POLL_S


# --------------------------------------------------------------------------
# SessionStart(compact): attribution by timing
# --------------------------------------------------------------------------


def test_sessionstart_teammate_at_the_lead_guard(tree):
    tree.boundary(tree.teammate)
    clock = Clock()
    assert attribute(tree.frame("SessionStart"), clock) == (co.TEAMMATE, co.TIMING)
    assert co.LEAD_GUARD_S <= clock.slept <= co.LEAD_GUARD_S + co.POLL_S


def test_a_lead_boundary_that_arrives_within_the_guard_wins(tree):
    tree.boundary(tree.teammate)
    clock = Clock(on_sleep={4: lambda: tree.boundary(tree.lead)})
    assert attribute(tree.frame("SessionStart"), clock) == (co.LEAD, co.TIMING)
    assert clock.slept == pytest.approx(1.0)


def test_both_boundaries_fresh_is_the_lead(tree):
    tree.boundary(tree.teammate)
    tree.boundary(tree.lead)
    clock = Clock()
    assert attribute(tree.frame("SessionStart"), clock) == (co.LEAD, co.TIMING)
    assert clock.sleeps == 0


def test_boundaries_older_than_the_window_are_not_a_signal(tree):
    tree.boundary(tree.teammate, at=ENTRY - timedelta(seconds=co.BACK_S + 1))
    assert attribute(tree.frame("SessionStart"), Clock()) == (co.UNKNOWN, co.DEADLINE)


@pytest.mark.parametrize("event, deadline", [("SessionStart", co.T_SESSIONSTART), ("PostCompact", co.T_POSTCOMPACT)])
def test_no_signal_stops_at_the_event_deadline(tree, event, deadline):
    clock = Clock()
    assert attribute(tree.frame(event), clock) == (co.UNKNOWN, co.DEADLINE)
    assert clock.slept <= deadline + co.POLL_S


# --------------------------------------------------------------------------
# Zero-sleep unknown, and frames that do not qualify
# --------------------------------------------------------------------------


def _outside_tree(tmp_path):
    outside = Tree(tmp_path / "elsewhere")
    outside.boundary(outside.teammate)
    return outside


@pytest.mark.parametrize("case", [
    "no_transcript_path", "session_id_not_a_string", "unsafe_session_id",
    "outside_projects", "lead_transcript_missing", "subagents_missing",
])
def test_each_zero_sleep_short_circuit(tree, tmp_path, case):
    frame = tree.frame("SessionStart")
    tree.boundary(tree.teammate)
    if case == "no_transcript_path":
        del frame["transcript_path"]
    elif case == "session_id_not_a_string":
        frame["session_id"] = 42
    elif case == "unsafe_session_id":
        frame["session_id"] = "../escape"
    elif case == "outside_projects":
        frame["transcript_path"] = str(_outside_tree(tmp_path).lead)
    elif case == "lead_transcript_missing":
        frame["transcript_path"] = str(tree.project / "absent.jsonl")
        (tree.project / "absent" / "subagents").mkdir(parents=True)
        frame["session_id"] = "absent"
    elif case == "subagents_missing":
        frame["session_id"] = "other-session"
        frame["transcript_path"] = str(tree.lead)
    clock = Clock()
    assert attribute(frame, clock) == (co.UNKNOWN, co.NO_SIGNAL)
    assert clock.sleeps == 0


@pytest.mark.parametrize("frame", [
    captured_compaction_teammate_precompact(),
    {"hook_event_name": "SessionStart", "source": "startup"},
    {"hook_event_name": "SessionStart", "source": "resume"},
    {"hook_event_name": "SessionStart", "source": "clear"},
    {"hook_event_name": "UserPromptSubmit"},
    ["not", "a", "dict"],
])
def test_a_frame_that_does_not_qualify_is_unknown_without_reading(tree, frame):
    if isinstance(frame, dict):
        frame = {**frame, "session_id": SID, "transcript_path": str(tree.lead)}
    tree.boundary(tree.teammate)
    clock = Clock()
    assert attribute(frame, clock) == (co.UNKNOWN, co.NO_SIGNAL)
    assert clock.sleeps == 0


def test_a_boundary_a_megabyte_before_the_end_is_still_read(tree):
    tree.boundary(tree.teammate, at=ENTRY - timedelta(seconds=1))
    tree.summary(tree.teammate, at=ENTRY - timedelta(seconds=1))
    filler = {"type": "assistant", "timestamp": _iso(ENTRY), "message": {"content": "x" * 1000}}
    with open(tree.teammate, "a", encoding="utf-8") as handle:
        for _ in range(1100):
            handle.write(json.dumps(filler) + "\n")
    assert tree.teammate.stat().st_size > 1024 * 1024
    assert attribute(tree.frame("PostCompact"), Clock()) == (co.TEAMMATE, co.CONTENT)


def test_a_raise_inside_the_predicate_is_unknown(tree, monkeypatch):
    tree.boundary(tree.teammate)
    monkeypatch.setattr(co, "_locate", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert attribute(tree.frame("SessionStart"), Clock()) == (co.UNKNOWN, co.NO_SIGNAL)


def test_teammate_compaction_is_the_teammate_verdict(tree):
    tree.boundary(tree.teammate)
    tree.summary(tree.teammate)
    clock = Clock()
    assert co.teammate_compaction(tree.frame("PostCompact"), now=clock.now,
                                  monotonic=clock.monotonic, sleep=clock.sleep) is True
    tree_lead_frame = tree.frame("SessionStart")
    tree.boundary(tree.lead)
    assert co.teammate_compaction(tree_lead_frame, now=clock.now,
                                  monotonic=clock.monotonic, sleep=clock.sleep) is False


# --------------------------------------------------------------------------
# The journal event
# --------------------------------------------------------------------------


def test_compaction_attributed_is_a_registered_event():
    from shared.session_journal import _validate_event_schema, make_event

    assert _validate_event_schema(make_event("compaction_attributed", verdict="teammate", basis="content"))[0]
    assert not _validate_event_schema(make_event("compaction_attributed", basis="content"))[0]
    assert not _validate_event_schema(make_event("compaction_attributed", verdict="teammate"))[0]


# --------------------------------------------------------------------------
# Captured frames
# --------------------------------------------------------------------------


def _bind(frame, tree):
    return {**frame, "transcript_path": str(tree.lead)}


def test_captured_teammate_frames_over_a_teammate_layout(tree):
    tree.boundary(tree.teammate)
    body = co._summary_body(captured_compaction_teammate_postcompact()["compact_summary"])
    tree.summary(tree.teammate, body=body)
    assert attribute(_bind(captured_compaction_teammate_postcompact(), tree), Clock()) == (co.TEAMMATE, co.CONTENT)
    assert attribute(_bind(captured_compaction_teammate_sessionstart(), tree), Clock()) == (co.TEAMMATE, co.TIMING)


def test_captured_lead_frames_over_the_lead_capture_order(tree):
    """The lead's order: its boundary, a secretary message, then the summary stamped earlier."""
    body = co._summary_body(captured_compaction_lead_postcompact()["compact_summary"])
    tree.boundary(tree.lead, at=ENTRY)
    tree.other_message(tree.lead, at=ENTRY + timedelta(milliseconds=29))
    tree.summary(tree.lead, at=ENTRY - timedelta(milliseconds=717), body=body)
    assert attribute(_bind(captured_compaction_lead_postcompact(), tree), Clock()) == (co.LEAD, co.CONTENT)
    assert attribute(_bind(captured_compaction_lead_sessionstart(), tree), Clock()) == (co.LEAD, co.TIMING)


@pytest.mark.parametrize("teammate, lead", [
    (captured_compaction_teammate_precompact, captured_compaction_lead_precompact),
    (captured_compaction_teammate_sessionstart, captured_compaction_lead_sessionstart),
    (captured_compaction_teammate_postcompact, captured_compaction_lead_postcompact),
])
def test_teammate_and_lead_compaction_frames_share_one_key_set(teammate, lead):
    assert set(teammate()) == set(lead())
    assert teammate()["agent_type"] == lead()["agent_type"]
    assert not {"agent_id", "agent_name", "agent_transcript_path"} & set(teammate())


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

_PERMITTED_KEYS = {"hook_event_name", "source", "session_id", "transcript_path", "compact_summary"}


def test_the_predicate_reads_only_the_permitted_frame_keys():
    tree = ast.parse((HOOKS / "shared" / "compaction_owner.py").read_text(encoding="utf-8"))
    read = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "frame"):
            read.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "frame"):
            read.add(getattr(node.slice, "value", None))
    assert read and read <= _PERMITTED_KEYS, sorted(read)


def _first_call_lines(func, names):
    lines = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if called in names:
                lines.setdefault(called, node.lineno)
                lines[called] = min(lines[called], node.lineno)
    return lines


def test_session_init_gates_before_every_write():
    tree = ast.parse((HOOKS / "session_init.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    writes = {"_persist_project_dir_env", "_adopt_old_slug_session_dir", "persist_context",
              "append_event", "update_session_info"}
    lines = _first_call_lines(main, writes | {"teammate_compaction"})
    assert "teammate_compaction" in lines, "session_init.main never asks who compacted"
    assert writes <= set(lines), sorted(writes - set(lines))
    later = {name: line for name, line in lines.items() if name in writes and line < lines["teammate_compaction"]}
    assert not later, f"these writes run before the teammate gate: {later}"


@pytest.mark.parametrize("hook", ["postcompact_archive.py", "session_init.py", "missed_wake_scan.py"])
def test_consumers_call_the_predicate_through_the_module(hook):
    """A test patch on shared.compaction_owner reaches the call, and no by-value
    copy of the predicate exists for conftest's import guard to track."""
    tree = ast.parse((HOOKS / hook).read_text(encoding="utf-8"))
    module_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "shared"
                      and any(a.name == "compaction_owner" for a in n.names)]
    by_value = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "shared.compaction_owner"]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "compaction_owner"]
    assert module_imports and not by_value and calls
