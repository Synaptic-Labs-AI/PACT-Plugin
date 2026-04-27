"""
Smoke tests for agent_handoff_emitter.py — #551 race-shape regression
guards.

Pins Option B's primary signal (``hook_event_name="TaskCompleted"``)
against the empirical race shape where disk shows ``status=in_progress``
because the platform fired TaskCompleted before persisting the status
flip. Also pins the Option E handoff-presence gate against the B1
phantom-fire-revert sequence.
"""
import pytest

from conftest import VALID_HANDOFF, _run_main


class TestRaceShapeRegression:
    """#551 root-cause regression guard. Platform fires TaskCompleted with
    `hook_event_name="TaskCompleted"` BEFORE persisting status="completed"
    to disk. Pre-Option-B, the (then-primary) disk-status gate read
    status="in_progress", aborted, and the journal write was never reached
    (3/3 PREPARE-phase probes confirmed; 0/51 cumulative production loss).

    Under Option B, hook_event_name="TaskCompleted" is the PRIMARY
    transition signal — disk-status is fallback only when stdin lacks
    hook_event_name. The journal write succeeds despite the on-disk
    status mismatch.

    Parametrized across two race shapes:
      (a) v3.19.2 race — disk shows in_progress because platform write
          hasn't persisted yet; this is the empirically-confirmed shape
          producing 0/51.
      (b) phantom-fire-revert — disk shows in_progress because the
          TaskUpdate was metadata-only (memory `21b4576b` documents 200+
          such fires pre-#538). Under Option B this also emits one event,
          then the `_already_emitted` O_EXCL marker suppresses any
          subsequent fires for the same (team, task_id).

    BOTH cases must produce exactly one append_event call. The marker
    persists either way, so subsequent fires for the same task are
    suppressed regardless of which race shape produced the first fire.
    """

    @pytest.mark.parametrize(
        "race_kind,disk_status,disk_metadata",
        [
            # (a) v3.19.2 race — platform fires TaskCompleted BEFORE
            #     persisting status=completed to disk, but the teammate
            #     has already stored metadata.handoff (the same TaskUpdate
            #     that flips status carries handoff in its metadata write).
            #     Disk shows status=in_progress; handoff is on disk.
            #     Option E gate passes (handoff present); hook_event_name
            #     primary signal triggers emission despite stale status.
            (
                "v3_19_2_race_pre_persist",
                "in_progress",
                {"handoff": VALID_HANDOFF},
            ),
            # (b) phantom-fire-revert — completion already happened;
            #     the disk reflects it (status=completed, handoff stored);
            #     and a follow-up TaskCompleted fires (e.g., from
            #     stopHooks.ts re-dispatch). Both signals positive,
            #     handoff present, marker dedup absorbs duplicate fires
            #     (covered by TestIdempotency).
            (
                "phantom_fire_revert_metadata_only",
                "in_progress",
                {"handoff": VALID_HANDOFF},
            ),
        ],
    )
    def test_hook_event_name_primary_signal_emits_despite_disk_in_progress(
        self, race_kind, disk_status, disk_metadata, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload={
                "hook_event_name": "TaskCompleted",
                "task_id": "race-probe",
                "task_subject": f"race-shape probe: {race_kind}",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": disk_status,
                "owner": "probe-agent",
                "metadata": disk_metadata,
            },
            append_calls=calls,
        )
        assert exit_code == 0
        assert len(calls) == 1, (
            f"#551 regression: race-shape {race_kind!r} (disk status="
            f"{disk_status!r}) should emit when hook_event_name="
            f"'TaskCompleted' is present. Pre-Option-B, the disk-status "
            f"gate aborted before reaching append_event — that is the "
            f"0/51 cumulative production loss this test pins."
        )
        assert calls[0]["agent"] == "probe-agent"
        assert calls[0]["task_id"] == "race-probe"

    def test_handoff_presence_gate_two_fire_sequence_real_revert(
        self, tmp_path, monkeypatch
    ):
        """Phantom-fire-revert realistic two-fire sequence under Option E
        handoff-presence gate. This pins the B1 fix from PR #563 review
        (review-architect): under platform revert, the FIRST fire arrives
        BEFORE the teammate has stored metadata.handoff (the fire is for
        a metadata-only TaskUpdate like briefing_delivered=true). The
        emitter MUST suppress that fire WITHOUT consuming the marker —
        otherwise the LATER genuine completion (with full handoff) gets
        suppressed by an empty-content marker, producing 51 empty journal
        entries instead of 51 substantive ones.

        Sequence:
          Fire 1: metadata={"briefing_delivered": True}, no handoff
                  → handoff-presence gate suppresses, NO marker, NO event.
          Fire 2: metadata={"handoff": VALID_HANDOFF, "briefing_delivered": True}
                  → handoff present, marker claimed, ONE event with full
                    handoff content lands in journal.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        payload = {
            "hook_event_name": "TaskCompleted",
            "task_id": "two-fire-revert",
            "task_subject": "two-fire revert sequence probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }

        # Fire 1: early metadata-only TaskUpdate fires TaskCompleted under
        # platform revert. Disk shows status=in_progress AND no handoff
        # key in metadata. Option E gate must suppress emission AND skip
        # marker creation.
        _run_main(
            payload,
            task_data={
                "status": "in_progress",
                "owner": "probe-agent",
                "metadata": {"briefing_delivered": True},  # NO handoff
            },
            append_calls=calls,
        )
        assert calls == [], (
            "Fire 1: handoff-presence gate failed to suppress an early "
            "metadata-only fire (no handoff key on disk). The B1 trace "
            "(review-architect, PR #563) would resurface — empty-content "
            "marker would suppress the later genuine completion."
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / "two-fire-revert"
        )
        assert not marker.exists(), (
            "Fire 1: marker MUST NOT be created when handoff is absent. "
            "If marker exists here, the genuine completion's later fire "
            "will hit EEXIST and silently drop the substantive HANDOFF — "
            "the exact B1 failure mode."
        )

        # Fire 2: genuine completion. Teammate has now stored
        # metadata.handoff; status flipped to completed. Option E gate
        # passes (handoff present), marker is claimed, journal write
        # produces the substantive entry.
        _run_main(
            payload,
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {
                    "handoff": VALID_HANDOFF,
                    "briefing_delivered": True,
                },
            },
            append_calls=calls,
        )
        assert len(calls) == 1, (
            "Fire 2: genuine completion failed to emit. Either the "
            "handoff-presence gate is over-suppressing (rejected a "
            "valid completion) or the gate ordering is wrong relative "
            "to the marker check."
        )
        assert calls[0]["handoff"] == VALID_HANDOFF, (
            "Fire 2: journal entry has empty/incorrect handoff. The "
            "gate suppressed Fire 1 correctly but the marker subsystem "
            "or append_event flow lost the handoff content."
        )
        assert marker.exists(), (
            "Fire 2: marker MUST be created on the genuine completion. "
            "Subsequent fires for the same (team, task_id) need it for "
            "dedup."
        )

    def test_handoff_presence_gate_suppresses_all_metadata_only_fires(
        self, tmp_path, monkeypatch
    ):
        """Worst-case: 5 sequential metadata-only fires (all without
        handoff stored). All must suppress; marker MUST NOT be created.
        This pins the property that no number of phantom fires can
        consume the marker prematurely.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        payload = {
            "hook_event_name": "TaskCompleted",
            "task_id": "no-handoff-storm",
            "task_subject": "metadata-only storm probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }
        task_data = {
            "status": "in_progress",
            "owner": "probe-agent",
            "metadata": {"briefing_delivered": True},  # never has handoff
        }
        for _ in range(5):
            _run_main(payload, task_data, calls)
        assert calls == [], (
            "metadata-only storm produced phantom journal events. The "
            "Option E handoff-presence gate is the load-bearing defense "
            "against B1; if any of the 5 fires emitted, the marker "
            "would be consumed with empty content."
        )
        marker = (
            tmp_path / ".claude" / "teams" / "pact-test"
            / ".agent_handoff_emitted" / "no-handoff-storm"
        )
        assert not marker.exists(), (
            "marker created during a metadata-only storm — B1 root "
            "cause. The genuine completion's later fire would be "
            "silently dropped."
        )

    def test_disk_status_fallback_when_hook_event_name_absent(
        self, tmp_path, monkeypatch
    ):
        """Forward-compat: stdin without hook_event_name should fall back
        to the disk-status gate. This preserves correctness if a future
        platform shape omits the field, AND it pins the fallback path so
        a future refactor cannot silently delete it.

        With status=in_progress on disk AND no hook_event_name in stdin,
        the fallback gate aborts and no event is written.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                # hook_event_name intentionally absent
                "task_id": "no-event-name",
                "task_subject": "stdin lacks hook_event_name",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "in_progress",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert calls == [], (
            "fallback disk-status gate failed to fire when hook_event_name "
            "absent. The Option B fallback path is load-bearing for forward "
            "compatibility — do not delete it without a replacement."
        )

    def test_disk_status_fallback_emits_when_disk_completed(
        self, tmp_path, monkeypatch
    ):
        """Symmetric pair: stdin without hook_event_name AND disk shows
        status=completed → fallback gate passes → event emits. This is
        the path the suite's mocked-read tests exercise (none pass
        hook_event_name), so this test confirms their happy-path
        semantics still hold.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        _run_main(
            stdin_payload={
                # hook_event_name intentionally absent
                "task_id": "fallback-happy",
                "task_subject": "fallback path happy case",
                "teammate_name": "probe-agent",
                "team_name": "pact-test",
            },
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1

