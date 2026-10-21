"""
Location: pact-plugin/tests/test_non_tty_seam_integration.py
Summary: NON-MOCKED seam tests for the resolver migration on the CONFIG-LESS
UUID-KEYED substrate — the store shape cloud (#1658) and Desktop (#1309)
actually produce: tasks live under ~/.claude/tasks/<full-session-uuid>/ while
the persisted context still names session-<id8>, and no config.json ever
forms. Every pre-migration consumer that read the RAW persisted name addressed
an absent store on this substrate; each test here drives a real hook over the
REAL resolution (Path.home redirect + real context file + real store — the
anti-mock invariant of test_missed_wake_scan_integration.py, extended to the
non-TTY shape that file's TEAM cells do not cover).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument).

Cells:
  missed_wake_scan — a stale awaiting_lead_completion wait in the UUID store
    MUST surface (pre-#1020 the raw read missed the store and the missed wake
    was itself missed); sparse-prompt cadence (waits hours old, the cloud
    rhythm) still surfaces exactly-once forensically; fresh waits, non-wait
    tasks, non-lead frames and empty stores stay silent; the BOTH-DISAGREE
    substrate fails closed at the hook (suppress, never a false alarm).
  agent_handoff_emitter — a completed task with a handoff in the UUID store
    emits the journal agent_handoff event and lands its O_EXCL marker under
    teams/<uuid>/ (the #1658 "silent HANDOFF-harvest loss" cell).
  handoff_ordering_gate — the gate resolves the task through the seam on the
    UUID substrate: a valid on-disk handoff produces NO warn; a completed
    task WITHOUT one still warns (positive control, so the no-warn cell is
    not vacuously green).
  bootstrap_marker_writer — a real lead fire on the config-less substrate
    lands harness=non_tty through the REAL resolver and REAL stamp write
    (Writer-2 composition; the W1 unit cells mock all three externals).
"""

from __future__ import annotations

import io
import json
import shutil

import pytest

import missed_wake_scan as mw
import agent_handoff_emitter as emitter
import handoff_ordering_gate as gate
from shared.session_journal import read_events

# Reuse the exemplar's live environment and real task writer verbatim — the
# substrate here is the same machinery pointed at the non-TTY shape.
from test_missed_wake_scan_integration import (  # noqa: F401 (fixture re-export)
    FRESH,
    STALE,
    _write_task,
    live_task_env,
)
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified

# The non-TTY shape from the issues: persisted/minted short name vs the
# config-less UUID-keyed stores the platform actually writes.
SHORT_NAME = "session-1a2b3c4d"
FULL_UUID = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"

# Sparse cloud prompting: the lead's turns are hours apart, so a legitimate
# wait is routinely far past the 30-min threshold when the next prompt lands.
SPARSE = 3 * 60  # minutes

VALID_HANDOFF = {
    "produced": ["docs/x.md"],
    "decisions": ["resolver seam"],
    "reasoning_chain": "raw read missed the UUID store",
    "uncertainty": "none",
    "integration": "staged",
    "open_questions": [],
}

LEAD = "PACT:pact-orchestrator"


@pytest.fixture
def non_tty_env(live_task_env):
    """Re-point the live env at the config-less non-TTY substrate.

    Context keeps the SHORT name (the stale persisted value the raw reads
    used); the ONLY store is UUID-keyed; the auto-materialized short-name
    dirs are removed so existence probing cannot find them; teams/<uuid>/
    carries inboxes only (config.json never forms on this substrate)."""
    live_task_env.configure(team_name=SHORT_NAME, session_id=FULL_UUID)
    for stale_dir in (
        live_task_env.tasks_root / SHORT_NAME,
        live_task_env.home / ".claude" / "teams" / SHORT_NAME,
    ):
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)
    uuid_team = live_task_env.home / ".claude" / "teams" / FULL_UUID
    (uuid_team / "inboxes").mkdir(parents=True, exist_ok=True)
    return live_task_env


class TestMissedWakeScanNonTtySubstrate:
    def test_uuid_keyed_stale_wait_surfaces(self, non_tty_env):
        """THE non-vacuity cell: pre-migration the raw persisted read addressed
        tasks/session-<id8>/ (absent) -> get_task_list None -> the missed wake
        was itself missed. The resolver must carry the UUID store to the
        surfacer."""
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="7", owner="architect",
                    subject="design X", minutes_ago=STALE)
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None, (
            "a stale wait in the UUID-keyed store MUST surface — None here is "
            "the missed-wake-missed bug on the non-TTY substrate"
        )
        assert "#7" in out and "architect" in out

    def test_sparse_cadence_surfaces_with_exactly_one_forensic_event(
            self, non_tty_env):
        """Sparse cloud prompting is not a false-alarm generator: a 3h-old wait
        surfaces persistently while stale, and the forensic emit stays
        once-per-(task,since) across re-fires against the real journal."""
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="8",
                    minutes_ago=SPARSE)
        out1 = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        out2 = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out1 is not None and out2 is not None
        events = read_events("missed_wake")
        assert len(events) == 1, (
            "sparse cadence must not multiply forensic events; got %d" % len(events)
        )

    def test_fresh_wait_below_threshold_no_false_alarm(self, non_tty_env):
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="9", minutes_ago=FRESH)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None

    def test_non_wait_task_stays_silent(self, non_tty_env):
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="10", with_wait=False)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None

    def test_non_lead_frame_stays_silent(self, non_tty_env):
        from fixtures.role_frames import (
            captured_plain_userpromptsubmit,
            captured_teammate_sessionstart,
        )
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="11", minutes_ago=STALE)
        assert mw.run_surface(captured_plain_userpromptsubmit()) is None
        assert mw.run_surface(captured_teammate_sessionstart()) is None

    def test_empty_uuid_store_stays_silent(self, non_tty_env):
        non_tty_env.dir_for(FULL_UUID).mkdir(parents=True, exist_ok=True)
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is None

    def test_both_disagree_substrate_fires_no_false_silence_note(self, non_tty_env):
        """Boundary documentation cell (not the fail-closed leg): this hook
        consumes get_task_list(), which resolves by the ALIGNED name — the
        ambiguity cell lives in resolve_task_dir consumers (the emitter /
        ordering gate, covered below). When the aligned name adopts the UUID
        (a corroborating config), the surfacer still reaches the UUID store
        through the explicit-name path, so a lingering short-name store does
        not silence the alarm."""
        from fixtures.team_split import seed_team

        seed_team(
            non_tty_env.home / ".claude",
            FULL_UUID,
            lead_sid=FULL_UUID,
            corroborate=False,
            store=False,
        )
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="13", minutes_ago=STALE)
        _write_task(non_tty_env.dir_for(SHORT_NAME), task_id="99", minutes_ago=STALE)
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None and "#13" in out
        assert "#99" not in out, "the stale lingering store must not be surfaced"


class TestHandoffEmitNonTtySubstrate:
    def _stdin(self, task_id: str) -> io.StringIO:
        return io.StringIO(json.dumps({
            "session_id": FULL_UUID,
            "hook_event_name": "TaskCompleted",
            "task_id": task_id,
            "task_subject": "implement via seam",
            "teammate_name": "backend-coder",
            "team_name": SHORT_NAME,  # deprecated stdin field: inert by design
        }))

    def test_handoff_emit_lands_in_uuid_store(self, non_tty_env, monkeypatch):
        """The #1658 silent HANDOFF-harvest loss cell: on the config-less
        substrate the emit must reach the REAL journal and plant its O_EXCL
        dedup marker under teams/<uuid>/ — the raw persisted name sent both to
        an absent store before the seam."""
        task = _write_task(non_tty_env.dir_for(FULL_UUID), task_id="42",
                           owner="backend-coder", status="completed",
                           with_wait=False)
        task["metadata"]["handoff"] = VALID_HANDOFF
        (non_tty_env.dir_for(FULL_UUID) / "42.json").write_text(
            json.dumps(task), encoding="utf-8")
        monkeypatch.setattr("sys.stdin", self._stdin("42"))
        with pytest.raises(SystemExit) as exit_info:
            emitter.main()
        assert exit_info.value.code == 0
        events = read_events("agent_handoff")
        assert len(events) == 1, (
            "the handoff must reach the real journal from the UUID-keyed "
            "store; got %d events" % len(events)
        )
        assert events[0]["task_id"] == "42"
        marker_ns = (non_tty_env.home / ".claude" / "teams" / FULL_UUID
                     / ".agent_handoff_emitted")
        assert marker_ns.is_dir() and any(marker_ns.iterdir()), (
            "the O_EXCL marker must live under teams/<uuid>/ on this substrate"
        )

    def test_ambiguous_substrate_suppresses_the_emit(self, non_tty_env, monkeypatch):
        """The fail-closed leg AT a consumer: on the BOTH-DISAGREE substrate
        (aligned adopts the UUID via a corroborating config, persisted still
        says the short name, both stores exist) resolve_task_dir returns None,
        the emitter fails open with an empty name, and NOTHING is emitted or
        marked — never a pick of either store. BOTH stores carry a
        handoff-bearing completed task 43, so this cell is INDEPENDENTLY
        discriminating: a raw-persisted-name regression (the pre-#1020 read)
        would find the SHORT_NAME task's handoff and emit from the STALE
        store, reddening here — not only via the lands-in-UUID-store cell."""
        from fixtures.team_split import seed_team

        seed_team(
            non_tty_env.home / ".claude",
            FULL_UUID,
            lead_sid=FULL_UUID,
            corroborate=False,
            store=False,
        )
        for store in (non_tty_env.dir_for(FULL_UUID),
                      non_tty_env.dir_for(SHORT_NAME)):
            task = _write_task(store, task_id="43", owner="backend-coder",
                               status="completed", with_wait=False)
            task["metadata"]["handoff"] = VALID_HANDOFF
            (store / "43.json").write_text(json.dumps(task), encoding="utf-8")
        monkeypatch.setattr("sys.stdin", self._stdin("43"))
        with pytest.raises(SystemExit) as exit_info:
            emitter.main()
        assert exit_info.value.code == 0
        assert read_events("agent_handoff") == []
        for team in (FULL_UUID, SHORT_NAME):
            marker_ns = (non_tty_env.home / ".claude" / "teams" / team
                         / ".agent_handoff_emitted")
            assert not marker_ns.is_dir() or not any(marker_ns.iterdir()), (
                f"no marker may be planted under teams/{team}/ on the "
                "ambiguous substrate"
            )


class TestOrderingGateNonTtySubstrate:
    def _complete_update(self, task_id: str, agent_type: str = LEAD) -> dict:
        return {
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": task_id, "status": "completed"},
            "agent_type": agent_type,
        }

    def test_valid_handoff_in_uuid_store_no_warn(self, non_tty_env):
        task = _write_task(non_tty_env.dir_for(FULL_UUID), task_id="A1",
                           status="completed", with_wait=False)
        task["metadata"]["handoff"] = VALID_HANDOFF
        (non_tty_env.dir_for(FULL_UUID) / "A1.json").write_text(
            json.dumps(task), encoding="utf-8")
        assert gate._evaluate(self._complete_update("A1")) is None, (
            "the gate must resolve the task through the seam on the UUID "
            "substrate — a warn here is the raw-read miss firing a FALSE "
            "advisory at the lead"
        )

    def test_missing_handoff_in_uuid_store_still_warns(self, non_tty_env):
        """Positive control for the no-warn cell: the same substrate, the same
        resolution, only the handoff absent — the warn MUST fire, so the
        no-warn cell above is proven discriminating rather than vacuous."""
        _write_task(non_tty_env.dir_for(FULL_UUID), task_id="A2",
                    status="completed", with_wait=False)
        assert gate._evaluate(self._complete_update("A2")) is not None


class TestBootstrapMarkerWriterStampIntegration:
    """Writer-2 composition, NON-MOCKED end-to-end: a real
    bootstrap_marker_writer fire on the config-less UUID substrate refines
    the harness stamp through the REAL aligned-name resolver, the REAL
    team-dir resolution and the REAL lead-gated stamp write. The W1 unit
    cells (TestWriterTwoSubstrate in test_non_tty_w1.py) mock all three
    externals; this cell closes that composition gap in CI. Measured
    counter-tests: removing the _refine_harness_stamp CALL from
    _try_write_marker reddens exactly this cell (the units stay green —
    they call the function directly); neutering the function body reddens
    this cell plus the two mocked W1 leg cells."""

    def test_config_less_fire_stamps_the_real_context(
            self, non_tty_env, monkeypatch):
        # The tasks-side UUID store the resolver's existence probe reads
        # (branch-2's teams-side witness already exists via non_tty_env).
        non_tty_env.dir_for(FULL_UUID).mkdir(parents=True, exist_ok=True)
        # A secretary JOIN witnessed the config-less way: the inbox file,
        # the same witness the marker precondition consumes.
        (non_tty_env.home / ".claude" / "teams" / FULL_UUID / "inboxes"
         / "secretary.json").write_text("{}", encoding="utf-8")
        import bootstrap_marker_writer as bmw
        import shared.pact_context as pact_context
        assert json.loads(
            pact_context._context_path.read_text(encoding="utf-8")
        ).get("harness", "") == ""  # precondition: unstamped
        frame = captured_lead_userpromptsubmit_qualified()
        frame.update(session_id=FULL_UUID, cwd=str(non_tty_env.home))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(frame)))
        with pytest.raises(SystemExit) as exit_info:
            bmw.main()
        assert exit_info.value.code == 0
        recorded = json.loads(
            pact_context._context_path.read_text(encoding="utf-8"))
        assert recorded["harness"] == "non_tty", (
            "a real lead fire on the config-less substrate must land the "
            "stamp through the real resolver and the real stamp write"
        )
        assert "team_name" in recorded  # surgical: the write kept the record


class TestResolverBranchCensus:
    """Branch-census cells: the resolver-family and transcript-scan branches
    the W1 unit battery leaves unhit (non-str persisted name, non-str
    session-id candidate, garbage transcript lines, non-dict records,
    empty-string entrypoint, scan-depth exhaustion) — six cells, each
    mapping to one census row, so the >=90% branch claim on the resolver
    family is a measured mapping, not an estimate. The four branches NO
    test hits — the census residuals (two resolve_* outer excepts, the
    loop-internal unsafe-candidate continue, the missing-session
    transcript-skip) — are named with their no-real-input justification in
    the TEST-phase HANDOFF, not here."""

    def test_non_str_persisted_team_name_is_treated_as_empty(
            self, tmp_path, monkeypatch):
        import shared.pact_context as pact_context
        from shared.task_utils import resolve_task_dir

        monkeypatch.setattr(pact_context, "get_team_name", lambda: "")
        monkeypatch.setattr(
            pact_context, "get_pact_context",
            lambda: {"team_name": 42, "session_id": FULL_UUID})
        base = tmp_path / "tasks"
        (base / FULL_UUID).mkdir(parents=True)
        got = resolve_task_dir(tasks_base_dir=str(base))
        assert got is not None and got.name == FULL_UUID, (
            "a non-str persisted name must degrade to '' and fall to the "
            "session-id candidate, never raise or str-coerce"
        )

    def test_non_str_session_id_candidate_is_skipped(self, tmp_path, monkeypatch):
        import shared.pact_context as pact_context
        from shared.task_utils import resolve_task_dir

        monkeypatch.setattr(pact_context, "get_team_name", lambda: "")
        monkeypatch.setattr(pact_context, "get_pact_context", lambda: {})
        monkeypatch.setattr(pact_context, "get_session_id", lambda: None)
        base = tmp_path / "tasks"
        base.mkdir()
        assert resolve_task_dir(tasks_base_dir=str(base)) is None

    def test_transcript_scan_skips_garbage_and_non_dict_lines(self, tmp_path):
        from session_init import _read_transcript_entrypoint

        path = tmp_path / "session.jsonl"
        path.write_text(
            "this is not json\n"
            '"a bare json string line"\n'
            + json.dumps({"type": "user", "entrypoint": "remote"}) + "\n",
            encoding="utf-8",
        )
        assert _read_transcript_entrypoint(str(path)) == "remote", (
            "malformed JSON lines and non-dict records are skipped, and the "
            "scan still finds the first str entrypoint"
        )

    def test_transcript_scan_all_garbage_yields_empty(self, tmp_path):
        from session_init import _read_transcript_entrypoint

        path = tmp_path / "session.jsonl"
        path.write_text("nope\n[1, 2, 3]\n", encoding="utf-8")
        assert _read_transcript_entrypoint(str(path)) == ""

    def test_empty_string_entrypoint_value_is_ignored(self, tmp_path):
        from session_init import _read_transcript_entrypoint

        path = tmp_path / "session.jsonl"
        path.write_text(
            json.dumps({"type": "user", "entrypoint": ""}) + "\n"
            + json.dumps({"type": "user", "entrypoint": "remote"}) + "\n",
            encoding="utf-8",
        )
        assert _read_transcript_entrypoint(str(path)) == "remote", (
            "an empty-string entrypoint is not a reading — the scan continues"
        )

    def test_scan_depth_exhaustion_yields_empty(self, tmp_path):
        from session_init import (
            _TRANSCRIPT_ENTRYPOINT_SCAN_LINES,
            _read_transcript_entrypoint,
        )

        path = tmp_path / "session.jsonl"
        # Entryless valid lines BEYOND the bounded scan depth, then the value:
        # the bounded read must stop before it.
        lines = [
            json.dumps({"type": "user", "n": n}) for n
            in range(_TRANSCRIPT_ENTRYPOINT_SCAN_LINES + 5)
        ] + [json.dumps({"type": "user", "entrypoint": "remote"})]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert _read_transcript_entrypoint(str(path)) == ""
