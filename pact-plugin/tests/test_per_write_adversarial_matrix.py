"""
Location: pact-plugin/tests/test_per_write_adversarial_matrix.py
Summary: TEST-phase adversarial/boundary depth for the PER-WRITE mirror path
         (task_lifecycle_gate's open-task TaskUpdate leg + TaskCreate leg
         driving the task_metadata_snapshot substrate). The substrate's own
         boundary arithmetic is pinned in test_snapshot_adversarial_matrix.py;
         THIS file drives the same hostile classes through the SEAM — the
         evaluate_lifecycle entrypoint (in-process, REAL journal + REAL
         markers, no append_event spy except where a write FAILURE is the
         scenario) and the real hook subprocess.

         Matrix:
         - Cap-edge payloads through the per-write path (caps IMPORTED from
           the substrate, never re-declared): per-value cap/cap+1, a whole
           overlay (disk ∪ delta) over the payload cap, a 5MB jumbo value.
         - Marker-lookalike values as targeted-key data (provenance, not
           shape, decides marker identity end-to-end).
         - Hostile task_ids (traversal fragments, control chars, unicode)
           through the sanitized content-key claim path — marker containment.
         - Pathological metadata shapes: deep nesting (intact + serializer
           breaking), None-riddled deltas, non-dict deltas (incl. a LIST
           carrying a targeted-key string — the guard-first shape).
         - Exit-0 advisory contract: a substrate write failure compensates
           (unclaim) and never alters the advisory return; the mirror adds no
           advisory across the fire/skip boundary (identical advisories in a
           canonical vs a tmux frame).
         - leadSessionId staleness probe: the documented blast radius — a
           stale-mismatched config downgrades to a skipped emit (baseline
           durability; no crash, no marker), a stale-equal admit lands the
           event in THIS process's own resolvable journal (canonical-adjacent,
           never a silo — a silo additionally requires a false-resolving
           journal path, which the substrate writability precondition
           forecloses).
         - Completing-TaskUpdate disjointness boundary: a completing write
           carrying a targeted key routes to the completion seam ONLY —
           exactly one event, with the completion payload, never the
           per-write overlay.
         - Real-process depth: per-write dedup across process lifetimes,
           stale-config skip, hostile-task_id containment, and an oversized
           stdin frame (the outermost exit-0 boundary).
Used by: pytest (TEST-phase certification for the per-write mirror).
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.task_metadata_snapshot as tms  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from shared.agent_handoff_marker import sanitize_path_component  # noqa: E402
from shared.session_journal import read_events  # noqa: E402
from shared.task_metadata_snapshot import (  # noqa: E402
    PAYLOAD_CAP,
    PER_VALUE_CAP,
    _canonical_bytes,
)

TEAM = "pact-perwrite-adv"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-backend-coder"

LEAD_SID = "lead-session-0001"
TMUX_SID = "teammate-session-0002"

SCOPE = {"files": ["a.py"], "boundaries": "backend only"}


# =============================================================================
# In-process harness — REAL journal (read_events), REAL markers; no spy.
# =============================================================================
@pytest.fixture
def home(tmp_path, monkeypatch, pact_context):
    """Test-scoped HOME with a live session context: journal writes and
    marker claims land on real tmp disk; read_events resolves the same
    journal the seam wrote."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    pact_context(team_name=TEAM, session_id=LEAD_SID,
                 project_dir="/test/project")
    return tmp_path


def _seed_task(home, task_id, *, subject="scope: atomize sub-scope",
               owner="team-lead", status="in_progress", metadata=None):
    tasks_dir = home / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps({
            "id": task_id,
            "subject": subject,
            "owner": owner,
            "status": status,
            "metadata": metadata if metadata is not None else {},
        }),
        encoding="utf-8",
    )


def _seed_team_config(home, lead_session_id=LEAD_SID):
    team_dir = home / ".claude" / "teams" / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"leadSessionId": lead_session_id}), encoding="utf-8"
    )


def _open_write(task_id, metadata, *, agent_type=LEAD, session_id=LEAD_SID):
    return {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": metadata},
        "tool_response": {},
        "agent_type": agent_type,
        "session_id": session_id,
    }


def _snapshots():
    return read_events("task_metadata_snapshot")


def _marker_files(home):
    teams_root = home / ".claude" / "teams"
    if not teams_root.exists():
        return []
    return [
        p
        for p in teams_root.rglob("*")
        if p.is_file() and p.parent.name == tms.SNAPSHOT_MARKER_NAMESPACE
    ]


def _is_marker_shape(value):
    """Reader-side marker recognition (the harvest consumer's view)."""
    return isinstance(value, dict) and value.get("_truncated") is True


def _journal_line_bytes(home):
    journal = (home / ".claude" / "pact-sessions" / "project" / LEAD_SID
               / "session-journal.jsonl")
    return [
        line.encode("utf-8")
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestPerWriteCapEdges:
    """Exact cap arithmetic THROUGH the seam. Sizes are derived from the
    imported substrate constants with the substrate's own _canonical_bytes
    yardstick — a cap change re-derives the boundary; independent companion
    assertions (event count, marker shape, bounded line) keep the rows
    non-vacuous even though the yardstick is shared with the impl."""

    def _value_of_canonical_size(self, size):
        value = "a" * (size - 2)  # canonical str form is quoted: len = n + 2
        assert len(_canonical_bytes(value)) == size
        return value

    def test_targeted_value_exactly_at_cap_survives_verbatim(self, home):
        value = self._value_of_canonical_size(PER_VALUE_CAP)
        _seed_task(home, "40")
        advisories = tlg.evaluate_lifecycle(
            _open_write("40", {"teachback_submit": value})
        )
        assert isinstance(advisories, list)
        snaps = _snapshots()
        assert len(snaps) == 1
        assert snaps[0]["metadata"]["teachback_submit"] == value
        assert "truncated" not in snaps[0]

    def test_targeted_value_one_over_cap_truncation_marked(self, home):
        value = self._value_of_canonical_size(PER_VALUE_CAP + 1)
        _seed_task(home, "41")
        tlg.evaluate_lifecycle(
            _open_write("41", {"teachback_submit": value})
        )
        snaps = _snapshots()
        assert len(snaps) == 1
        marker = snaps[0]["metadata"]["teachback_submit"]
        assert _is_marker_shape(marker), (
            "one byte over PER_VALUE_CAP must surface as the truncation "
            "marker — key existence survives, value is bounded"
        )
        assert marker["original_bytes"] == PER_VALUE_CAP + 1
        assert snaps[0]["truncated"] is True

    def test_whole_overlay_over_payload_cap_bounded_marked(self, home):
        """disk ∪ delta over PAYLOAD_CAP: the overlay (not either half
        alone) is what the substrate bounds. Four 13KB disk siblings +
        a 15KB targeted delta ≈ 67KB > 64KB — the largest value (the
        targeted key itself) is evicted to a marker, and the emitted
        LINE stays near the cap."""
        disk_md = {f"k{i}": "x" * (13 * 1024) for i in range(4)}
        _seed_task(home, "42", metadata=disk_md)
        big_scope = "s" * (15 * 1024)
        tlg.evaluate_lifecycle(_open_write("42", {"scope_contract": big_scope}))
        snaps = _snapshots()
        assert len(snaps) == 1
        payload = snaps[0]["metadata"]
        assert _is_marker_shape(payload["scope_contract"]), (
            "largest-first eviction picks the 15KB targeted value"
        )
        assert payload["scope_contract"]["original_bytes"] == len(
            _canonical_bytes(big_scope)
        )
        for key in disk_md:
            assert payload[key] == disk_md[key]
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP
        lines = _journal_line_bytes(home)
        assert len(lines) == 1
        assert len(lines[0]) < PAYLOAD_CAP + 16 * 1024

    def test_jumbo_5mb_targeted_value_no_raise_bounded_line(self, home):
        _seed_task(home, "43")
        advisories = tlg.evaluate_lifecycle(
            _open_write("43", {"teachback_submit": "B" * (5 * 1024 * 1024)})
        )
        assert isinstance(advisories, list), "exit-0 contract: no raise"
        snaps = _snapshots()
        assert len(snaps) == 1
        marker = snaps[0]["metadata"]["teachback_submit"]
        assert _is_marker_shape(marker)
        assert marker["original_bytes"] == 5 * 1024 * 1024 + 2
        lines = _journal_line_bytes(home)
        assert len(lines) == 1
        assert len(lines[0]) < PAYLOAD_CAP + 16 * 1024, (
            "the raw 5MB value must never reach the journal line"
        )


class TestMarkerLookalikeThroughPerWrite:
    """Provenance-not-shape end-to-end: caller data that LOOKS like the
    substrate's truncation marker is ordinary data at the seam."""

    LOOKALIKE = {"_truncated": True, "original_bytes": 999999999,
                 "head": "attacker-controlled"}

    def test_small_lookalike_carried_verbatim_and_dedups(self, home):
        _seed_task(home, "44")
        payload = _open_write("44", {"scope_contract": dict(self.LOOKALIKE)})
        tlg.evaluate_lifecycle(payload)
        tlg.evaluate_lifecycle(payload)  # unchanged rewrite → dedup no-op
        snaps = _snapshots()
        assert len(snaps) == 1
        assert snaps[0]["metadata"]["scope_contract"] == self.LOOKALIKE, (
            "an under-cap lookalike is ordinary data — carried verbatim, "
            "never re-interpreted as OUR marker"
        )
        assert "truncated" not in snaps[0], (
            "nothing was truncated; a lookalike must not set the flag"
        )
        assert len(_marker_files(home)) == 1

    def test_oversized_lookalike_with_hostile_original_bytes_replaced(
            self, home):
        """The dangerous variant: a lookalike whose original_bytes is a
        hostile NON-INT and whose serialization is over the per-value cap.
        Shape-tested marker identity would let the hostile field reach the
        stage-3 int sort (TypeError inside the hermetic emit = silent
        event loss); provenance tracking replaces the whole value with OUR
        marker computed from ITS serialization."""
        hostile = {
            "_truncated": True,
            "original_bytes": "not-an-int",
            "head": "h" * (PER_VALUE_CAP),  # canonical size > PER_VALUE_CAP
        }
        _seed_task(home, "45")
        tlg.evaluate_lifecycle(_open_write("45", {"scope_contract": hostile}))
        snaps = _snapshots()
        assert len(snaps) == 1
        marker = snaps[0]["metadata"]["scope_contract"]
        assert _is_marker_shape(marker)
        assert marker["original_bytes"] == len(_canonical_bytes(hostile)), (
            "OUR marker, computed from the hostile value's serialization — "
            "an int by construction, not the attacker's string"
        )
        assert isinstance(marker["original_bytes"], int)
        assert snaps[0]["truncated"] is True


class TestHostileTaskIdsThroughClaimPath:
    """Traversal/control-char/unicode task_ids through the per-write leg.
    The seam adds NO sanitizer of its own — everything rides the substrate's
    sanitized content-key claim path. Independent companions to the shared
    sanitize_path_component oracle: no raise, marker CONTAINMENT inside the
    namespace dir, and event-count sanity."""

    HOSTILE_IDS = [
        "../../etc/passwd",
        "task\x00id\r\n",
        "täsk☃-unicode",
        "a/b\\c",
    ]

    def test_hostile_update_ids_no_raise_sanitized_contained(self, home):
        emitted = 0
        for task_id in self.HOSTILE_IDS:
            advisories = tlg.evaluate_lifecycle(
                _open_write(task_id, {"scope_contract": SCOPE})
            )
            assert isinstance(advisories, list), (
                f"exit-0 contract violated for task_id {task_id!r}"
            )
        snaps = _snapshots()
        emitted = len(snaps)
        assert emitted >= 1, "the hostile-id class must not kill the emit"
        sanitized = {sanitize_path_component(str(t))
                     for t in self.HOSTILE_IDS}
        for event in snaps:
            assert event["task_id"] in sanitized, (
                "the emitted task_id must be the sanitized form — raw "
                "traversal/control bytes never reach the journal"
            )
        self._assert_marker_containment(home)
        assert not (home / ".claude" / "etc").exists()
        assert not (home / "etc").exists()

    def test_hostile_create_id_sanitized_contained(self, home):
        _seed_task(home, "46")
        payload = {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": "scope: sub-scope",
                           "metadata": {"scope_contract": SCOPE}},
            "tool_response": {"task": {"id": "../../marker-escape"}},
            "agent_type": LEAD,
            "session_id": LEAD_SID,
        }
        advisories = tlg.evaluate_lifecycle(payload)
        assert isinstance(advisories, list)
        snaps = _snapshots()
        assert len(snaps) == 1
        assert snaps[0]["task_id"] == "marker-escape"
        self._assert_marker_containment(home)

    @staticmethod
    def _assert_marker_containment(home):
        teams_root = (home / ".claude" / "teams").resolve()
        for path in _marker_files(home):
            resolved = path.resolve()
            assert resolved.is_relative_to(teams_root), (
                f"marker escaped the teams root: {resolved}"
            )
            assert resolved.parent.name == tms.SNAPSHOT_MARKER_NAMESPACE


class TestPathologicalMetadataShapes:
    def test_deep_nesting_intact_through_seam(self, home):
        """300-deep nesting survives the overlay + serialization round-trip
        intact (no flattening, no silent drop)."""
        nested = "leaf"
        for _ in range(300):
            nested = {"d": nested}
        _seed_task(home, "47")
        tlg.evaluate_lifecycle(_open_write("47", {"scope_contract": nested}))
        snaps = _snapshots()
        assert len(snaps) == 1
        node = snaps[0]["metadata"]["scope_contract"]
        for _ in range(300):
            node = node["d"]
        assert node == "leaf"

    def test_serializer_breaking_nesting_hermetic_no_marker_poison(
            self, home):
        """A nesting deep enough to blow the JSON serializer (RecursionError
        inside _canonical_bytes) is swallowed by the hermetic emit BEFORE
        any marker claim: no raise, no event, no poisoned marker — and a
        later clean fire on the same task emits normally."""
        nested = "leaf"
        for _ in range(100_000):
            nested = {"d": nested}
        _seed_task(home, "48")
        advisories = tlg.evaluate_lifecycle(
            _open_write("48", {"scope_contract": nested})
        )
        assert isinstance(advisories, list), "exit-0 contract: no raise"
        assert _snapshots() == []
        assert _marker_files(home) == [], (
            "validate-before-claim: the failed build must claim nothing"
        )
        tlg.evaluate_lifecycle(_open_write("48", {"scope_contract": SCOPE}))
        assert len(_snapshots()) == 1, "recovery fire must not be suppressed"

    def test_none_riddled_delta_top_level_dropped_nested_preserved(
            self, home):
        """Top-level None values are the platform DELETE op (dropped from
        the overlay); None values NESTED inside a targeted value are data
        and must survive verbatim."""
        _seed_task(home, "49", metadata={"note": "existing",
                                         "worktree_path": "/old"})
        delta = {
            "scope_contract": {"a": None, "b": {"c": None}},
            "nesting_depth": None,
            "junk": None,
        }
        tlg.evaluate_lifecycle(_open_write("49", delta))
        snaps = _snapshots()
        assert len(snaps) == 1
        payload = snaps[0]["metadata"]
        assert payload["scope_contract"] == {"a": None, "b": {"c": None}}
        assert "nesting_depth" not in payload
        assert "junk" not in payload
        assert payload["note"] == "existing"
        assert payload["worktree_path"] == "/old"

    @pytest.mark.parametrize("bad_delta", [
        "scope_contract",            # a STRING that equals a targeted key
        ["scope_contract"],          # a LIST carrying a targeted-key string
        7,
        True,
    ], ids=["str", "list-with-targeted-token", "int", "bool"])
    def test_non_dict_delta_guard_first_no_emit_no_raise(
            self, home, bad_delta):
        """isinstance-dict guards run BEFORE the key scan: a non-dict delta
        never fires, even when iterating it would yield a targeted-key
        string (the list row — `any(k in KEYS for k in list)` would match
        without the guard)."""
        _seed_task(home, "50")
        advisories = tlg.evaluate_lifecycle(_open_write("50", bad_delta))
        assert isinstance(advisories, list)
        assert _snapshots() == []
        assert _marker_files(home) == []


class TestExitZeroAdvisoryNonInterference:
    def test_append_failure_compensates_and_preserves_advisories(
            self, home, monkeypatch):
        """A journal write failure inside the substrate must (a) roll back
        the content-key claim (compensating unclaim — a later valid fire
        can re-emit) and (b) leave the advisory return IDENTICAL to the
        success path."""
        _seed_task(home, "51")
        _seed_task(home, "52")
        ok_advisories = tlg.evaluate_lifecycle(
            _open_write("51", {"scope_contract": SCOPE})
        )
        assert len(_snapshots()) == 1
        markers_after_success = len(_marker_files(home))
        assert markers_after_success == 1

        def _raise(event):
            raise RuntimeError("journal write failed")

        monkeypatch.setattr(tms, "append_event", _raise)
        fail_advisories = tlg.evaluate_lifecycle(
            _open_write("52", {"scope_contract": {"other": "content"}})
        )
        assert fail_advisories == ok_advisories, (
            "a mirror failure must never alter the advisory output"
        )
        assert len(_marker_files(home)) == markers_after_success, (
            "the failed emit's claim must be compensated (unclaimed)"
        )

    def test_mirror_adds_no_advisory_across_fire_boundary(self, home):
        """The SAME teachback write (malformed variety_acknowledgment — a
        known write-time advisory trigger) evaluated in a canonical frame
        (mirror FIRES) and a tmux frame (mirror SKIPS) must return
        IDENTICAL, NON-EMPTY advisories: the mirror neither adds nor
        suppresses an advisory on either side of the fire boundary."""
        _seed_team_config(home)
        _seed_task(home, "53",
                   subject="backend-coder: TEACHBACK for adversarial",
                   owner="backend-coder")
        delta = {"teachback_submit": {
            "understanding": "u",
            "most_likely_wrong": "m",
            "least_confident_item": "l",
            "first_action": "f",
            "variety_acknowledgment": "free-text string — wrong shape",
        }}
        fired = tlg.evaluate_lifecycle(
            _open_write("53", copy.deepcopy(delta),
                        agent_type=TEAMMATE, session_id=LEAD_SID)
        )
        assert len(_snapshots()) == 1, "canonical frame must fire"
        skipped = tlg.evaluate_lifecycle(
            _open_write("53", copy.deepcopy(delta),
                        agent_type=TEAMMATE, session_id=TMUX_SID)
        )
        assert len(_snapshots()) == 1, "tmux frame must skip"
        assert fired == skipped
        assert fired, "the oracle advisory must be non-empty (else vacuous)"
        assert any(
            rule == "variety_acknowledgment_schema_invalid_at_write_time"
            for rule, _ in fired
        )


class TestLeadSessionIdStalenessProbe:
    """The topology compare's documented currency dependency: certify the
    blast radius is never worse than the shipped baseline."""

    def test_stale_mismatched_config_downgrades_to_skip(self, home):
        """An in-process teammate whose live session_id no longer matches a
        STALE leadSessionId (config not refreshed after a resume) is
        misclassified as tmux → SKIP. Blast radius: a skipped in-process
        emit — baseline durability (completion seams) — with no crash and
        no marker claim. This is the accepted direction; the durability
        loss is bounded, the marker namespace stays clean."""
        _seed_team_config(home, lead_session_id="stale-pre-resume-sid")
        _seed_task(home, "54", owner="backend-coder",
                   subject="backend-coder: TEACHBACK gate")
        advisories = tlg.evaluate_lifecycle(
            _open_write("54", {"teachback_submit": {"understanding": "u"}},
                        agent_type=TEAMMATE, session_id=LEAD_SID)
        )
        assert isinstance(advisories, list)
        assert _snapshots() == []
        assert _marker_files(home) == []

    def test_stale_equal_admit_lands_in_own_resolvable_journal(self, home):
        """The admit direction: whatever frame carries session_id ==
        leadSessionId is admitted, and its emit lands in the journal THIS
        process's own session context resolves — a readable,
        canonical-adjacent location, never an unreadable silo. (A true
        silo requires BOTH a stale-equal id AND a false-resolving journal
        path; with no resolvable path the substrate's writability
        precondition defers instead — pinned at the substrate level.)"""
        _seed_team_config(home, lead_session_id=LEAD_SID)
        _seed_task(home, "55", owner="backend-coder",
                   subject="backend-coder: TEACHBACK gate")
        tlg.evaluate_lifecycle(
            _open_write("55", {"teachback_submit": {"understanding": "u"}},
                        agent_type=TEAMMATE, session_id=LEAD_SID)
        )
        journal = (home / ".claude" / "pact-sessions" / "project" / LEAD_SID
                   / "session-journal.jsonl")
        assert journal.exists(), (
            "the admitted emit must land in the session-context-resolved "
            "journal — the location a canonical reader consults"
        )
        assert len(_snapshots()) == 1

    def test_config_drained_mid_arc_skips_no_crash(self, home):
        """teams/config.json deleted between dispatch and write (the drain
        class this feature exists to survive): topology unresolvable →
        fail-closed skip, no crash."""
        _seed_task(home, "56", owner="backend-coder",
                   subject="backend-coder: TEACHBACK gate")
        advisories = tlg.evaluate_lifecycle(
            _open_write("56", {"teachback_submit": {"understanding": "u"}},
                        agent_type=TEAMMATE, session_id=LEAD_SID)
        )
        assert isinstance(advisories, list)
        assert _snapshots() == []
        assert _marker_files(home) == []


class TestCompletingWriteDisjointnessBoundary:
    def test_completing_update_with_targeted_key_single_completion_event(
            self, home):
        """A COMPLETING TaskUpdate carrying a targeted key in its delta
        routes to the completion block ONLY. The tool_input delta and the
        tool_response final metadata deliberately DIFFER: if the per-write
        leg also fired, its overlay would be a second, distinct content
        key → TWO events. Exactly one event, carrying the completion
        payload, proves the status split routes rather than dedups."""
        _seed_task(home, "57", metadata={"scope_contract": SCOPE})
        final_md = {"scope_contract": SCOPE, "final_extra": "final"}
        payload = {
            "tool_name": "TaskUpdate",
            "tool_input": {
                "taskId": "57",
                "status": "completed",
                "metadata": {"scope_contract": SCOPE},
            },
            "tool_response": {"task": {
                "id": "57",
                "subject": "scope: atomize sub-scope",
                "owner": "team-lead",
                "metadata": final_md,
            }},
            "agent_type": LEAD,
            "session_id": LEAD_SID,
        }
        tlg.evaluate_lifecycle(payload)
        snaps = _snapshots()
        assert len(snaps) == 1, (
            "a completing targeted write is the completion seam's surface — "
            "a second event means the per-write leg fired across the "
            "status boundary"
        )
        assert snaps[0]["metadata"] == final_md


# =============================================================================
# Real-process depth — own copy of the subprocess harness (kept local so the
# CODE-phase subprocess file stays untouched; same env-only isolation, same
# ANTI-MOCK posture: nothing in-process is patched for these rows).
# =============================================================================
SUB_TEAM = "session-perwrite-adv"
SUB_SID = "dddddddd-4444-5555-6666-777777777777"
SUB_SLUG = "perwrite-adv-project"

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def _sub_seed_home(tmp_path):
    home = tmp_path / "home"
    session_dir = home / ".claude" / "pact-sessions" / SUB_SLUG / SUB_SID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": SUB_TEAM,
            "session_id": SUB_SID,
            "project_dir": f"/tmp/{SUB_SLUG}",
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    tasks_dir = home / ".claude" / "tasks" / SUB_TEAM
    tasks_dir.mkdir(parents=True)
    return home, session_dir, tasks_dir


def _sub_write_task(tasks_dir, task_id, *, status="in_progress",
                    metadata=None):
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps({
            "id": task_id,
            "owner": "backend-coder",
            "subject": "scope: sub-scope",
            "status": status,
            "metadata": metadata if metadata is not None else {},
        }),
        encoding="utf-8",
    )


def _sub_run_gate(stdin_text, home):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = f"/tmp/{SUB_SLUG}"
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / "task_lifecycle_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
        timeout=60,
    )


def _sub_open_write_frame(task_id, metadata, *,
                          agent_type="PACT:pact-orchestrator",
                          session_id=SUB_SID):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "agent_type": agent_type,
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": metadata},
        "tool_response": {},
    }


def _sub_snapshots(home):
    return [
        json.loads(line)
        for journal in home.rglob("*.jsonl")
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and json.loads(line).get("type") == "task_metadata_snapshot"
    ]


def _sub_marker_dir(home):
    return (home / ".claude" / "teams" / SUB_TEAM
            / tms.SNAPSHOT_MARKER_NAMESPACE)


class TestPerWriteSubprocessDepth:
    def test_unchanged_rewrite_dedups_across_process_lifetimes(
            self, tmp_path):
        """The dedup/churn acceptance criterion at the REAL seam: two full
        hook process runs of the identical targeted write — fresh
        interpreter each time, so ONLY the on-disk O_EXCL content-hash
        marker can carry the dedup — land exactly ONE journal event."""
        home, _session_dir, tasks_dir = _sub_seed_home(tmp_path)
        _sub_write_task(tasks_dir, "70")
        frame = json.dumps(
            _sub_open_write_frame("70", {"scope_contract": SCOPE})
        )
        first = _sub_run_gate(frame, home)
        second = _sub_run_gate(frame, home)
        assert first.returncode == 0 and second.returncode == 0
        assert len(_sub_snapshots(home)) == 1, (
            "an unchanged targeted rewrite must be a churn no-op across "
            "real process lifetimes"
        )
        marker_dir = _sub_marker_dir(home)
        assert marker_dir.exists() and len(list(marker_dir.iterdir())) == 1

    def test_stale_config_mismatch_real_process_skips(self, tmp_path):
        """Staleness probe through the real binary: a teammate frame whose
        live session_id mismatches the on-disk (stale) leadSessionId skips
        — zero snapshot events anywhere under HOME, zero markers, exit 0."""
        home, _session_dir, tasks_dir = _sub_seed_home(tmp_path)
        team_dir = home / ".claude" / "teams" / SUB_TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"leadSessionId": "stale-pre-resume-sid"}),
            encoding="utf-8",
        )
        _sub_write_task(tasks_dir, "71")
        result = _sub_run_gate(
            json.dumps(_sub_open_write_frame(
                "71", {"teachback_submit": {"understanding": "u"}},
                agent_type="pact-backend-coder",
            )),
            home,
        )
        assert result.returncode == 0, result.stderr
        assert _sub_snapshots(home) == []
        marker_dir = _sub_marker_dir(home)
        assert not marker_dir.exists() or not any(marker_dir.iterdir())

    def test_hostile_task_id_real_process_contained(self, tmp_path):
        """Traversal task_id through the real binary: exit 0, sanitized
        event id, every marker inside the namespace dir."""
        home, _session_dir, tasks_dir = _sub_seed_home(tmp_path)
        result = _sub_run_gate(
            json.dumps(_sub_open_write_frame(
                "../../marker-escape", {"scope_contract": SCOPE}
            )),
            home,
        )
        assert result.returncode == 0, result.stderr
        snaps = _sub_snapshots(home)
        assert len(snaps) == 1
        assert snaps[0]["task_id"] == "marker-escape"
        teams_root = (home / ".claude" / "teams").resolve()
        for path in teams_root.rglob("*"):
            if path.is_file() and path.suffix != ".json":
                assert path.resolve().is_relative_to(teams_root)
                assert path.parent.name == tms.SNAPSHOT_MARKER_NAMESPACE
        assert not (home / "etc").exists()
        assert not (home / ".claude" / "etc").exists()

    def test_oversized_stdin_frame_exit0_suppress_no_write(self, tmp_path):
        """The outermost exit-0 boundary: a stdin frame larger than the
        gate's stdin read cap truncates to malformed JSON → suppressOutput,
        exit 0, and NOTHING lands in any journal (no partial parse can
        reach the emit path)."""
        home, _session_dir, _tasks_dir = _sub_seed_home(tmp_path)
        frame = _sub_open_write_frame("72", {"scope_contract": SCOPE})
        frame["padding"] = "P" * (9 * 1024 * 1024)  # over the 8MB read cap
        result = _sub_run_gate(json.dumps(frame), home)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.strip())["suppressOutput"] is True
        assert _sub_snapshots(home) == []
