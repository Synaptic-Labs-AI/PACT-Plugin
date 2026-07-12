"""
Location: pact-plugin/tests/test_task_id_sanitization_parity.py
Summary: Cross-family task_id sanitization parity (#1165) — every journal
         emit family (b1 agent_handoff, lead-side b2 agent_handoff, and the
         task_metadata_snapshot seams A/B/C) must stamp the IDENTICAL
         sanitized task_id form for identical pathological input, and the
         sanitize helper must stay the single shared SSOT binding (no
         inline duplicates). Guards the reader-side cross-family join
         (harvest SKILL.md filters snapshots by the handoff event's
         task_id) and the shared O_EXCL marker key (a form split between
         b1 and b2 would miss dedup and double-emit).
Used by: pytest (CODE-phase verification for the sanitization intake
         alignment; matrix depth is TEST phase work).
"""

import json
from pathlib import Path

import pytest

import shared.agent_handoff_marker as ahm
import shared.task_metadata_snapshot as tms
import task_lifecycle_gate as tlg
from fixtures.emitter import VALID_HANDOFF, _run_main

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"

# Path traversal + separator + C0 control in one input. Its sanitized form
# is stable under repeated application, so single-pass (b1/b2 intake, the
# substrate's internal pass at seams A/B) and double-pass (seam C: emitter
# intake + substrate) call chains must all converge on the same form.
PATHOLOGICAL_TASK_ID = "../7\x00x"


@pytest.fixture
def snapshot_events(monkeypatch):
    """Spy on the SUBSTRATE's append binding (seams A/B/C route snapshot
    writes through shared.task_metadata_snapshot)."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tms, "append_event", _spy)
    return events


@pytest.fixture
def gate_events(monkeypatch):
    """Spy on the GATE's append binding (the lead-side b2 agent_handoff)."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tlg, "append_event", _spy)
    return events


def _seed_task(tmp_path, team, task_id, **fields):
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestTaskIdSanitizationParity:
    def test_all_emit_families_identical_task_id_form(
        self, tmp_path, monkeypatch, pact_context, snapshot_events,
        gate_events,
    ):
        """#1165 acceptance: b1 agent_handoff, b2 lead-side agent_handoff,
        and snapshot seams A, B, and C all stamp the identical sanitized
        task_id for the same pathological raw input. Per-leg metadata
        contents and owners differ so the content-keyed snapshot dedup and
        the occupant-keyed handoff dedup cannot collapse legs into each
        other — five independent emits, one task_id form."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id="s1")
        expected = ahm.sanitize_path_component(str(PATHOLOGICAL_TASK_ID))
        assert expected == "7x"  # self-check: the exemplar sanitizes cleanly

        # Legs 1+2 — gate completion frame: b2 agent_handoff + seam A
        # snapshot fire from ONE evaluate_lifecycle call.
        tlg.evaluate_lifecycle({
            "tool_name": "TaskUpdate",
            "tool_input": {
                "taskId": PATHOLOGICAL_TASK_ID,
                "status": "completed",
            },
            "tool_response": {"task": {
                "id": PATHOLOGICAL_TASK_ID,
                "subject": "devops: parity probe seam a",
                "owner": "devops",
                "metadata": {
                    "variety": {"total": 7},
                    "handoff": VALID_HANDOFF,
                },
            }},
            "agent_type": LEAD,
        })
        b2_handoffs = [
            e for e in gate_events if e.get("type") == "agent_handoff"
        ]
        seam_a_snaps = [
            e for e in snapshot_events
            if e.get("type") == "task_metadata_snapshot"
        ]
        assert len(b2_handoffs) == 1
        assert len(seam_a_snaps) == 1

        # Leg 3 — seam B backstop: metadata-only write on an already-
        # completed disk task. This leg uses the NUL-FREE traversal exemplar:
        # seam B requires a resolvable disk task file, and read_task_json's
        # path sanitization strips separators/dotdot but not NUL, so a
        # NUL-bearing id targets a filesystem-invalid filename and fail-opens
        # to {} — the backstop is unreachable for NUL ids by construction.
        # Both exemplars sanitize to the same expected form, so the
        # cross-family identity assertion below is unaffected.
        seam_b_raw = "../7x"
        assert ahm.sanitize_path_component(seam_b_raw) == expected
        _seed_task(
            tmp_path,
            TEAM,
            expected,
            subject="devops: parity probe seam b",
            owner="devops",
            status="completed",
            metadata={"variety": {"total": 5}},
        )
        tlg.evaluate_lifecycle({
            "tool_name": "TaskUpdate",
            "tool_input": {
                "taskId": seam_b_raw,
                "metadata": {"r2_verification": {"verified": True}},
            },
            "tool_response": {},
            "agent_type": LEAD,
        })
        seam_b_snaps = [
            e for e in snapshot_events
            if e.get("type") == "task_metadata_snapshot"
        ][1:]
        assert len(seam_b_snaps) == 1

        # Legs 4+5 — b1 emitter frame: b1 agent_handoff + seam C snapshot
        # from one main() run. Different owner → different occupant, so the
        # b1 marker cannot collide with b2's claim above.
        monkeypatch.setattr(
            tms, "get_journal_path",
            lambda: "/pact-test-session/session-journal.jsonl",
        )
        b1_calls: list[dict] = []
        exit_code = _run_main(
            stdin_payload={
                "task_id": PATHOLOGICAL_TASK_ID,
                "task_subject": "backend-coder: parity probe seam c",
                "teammate_name": "backend-coder",
                "team_name": TEAM,
                "hook_event_name": "TaskCompleted",
            },
            task_data={
                "status": "completed",
                "owner": "backend-coder",
                "metadata": {
                    "teachback_submit": {"understanding": "u"},
                    "handoff": VALID_HANDOFF,
                },
            },
            append_calls=b1_calls,
        )
        assert exit_code == 0
        b1_handoffs = [
            e for e in b1_calls if e.get("type") == "agent_handoff"
        ]
        seam_c_snaps = [
            e for e in snapshot_events
            if e.get("type") == "task_metadata_snapshot"
        ][2:]
        assert len(b1_handoffs) == 1
        assert len(seam_c_snaps) == 1

        forms = {
            "b1_agent_handoff": b1_handoffs[0]["task_id"],
            "b2_agent_handoff": b2_handoffs[0]["task_id"],
            "seam_a_snapshot": seam_a_snaps[0]["task_id"],
            "seam_b_snapshot": seam_b_snaps[0]["task_id"],
            "seam_c_snapshot": seam_c_snaps[0]["task_id"],
        }
        assert set(forms.values()) == {expected}, forms

    def test_sanitize_helper_is_single_ssot_binding(self):
        """Every emit-path module binds THE shared sanitize function object
        from shared.agent_handoff_marker — an inline reimplementation (a
        module-local regex copy) would break this identity and reopen the
        cross-family drift class."""
        from agent_handoff_emitter import (
            sanitize_path_component as emitter_binding,
        )

        assert tlg.sanitize_path_component is ahm.sanitize_path_component
        assert emitter_binding is ahm.sanitize_path_component
        assert tms.sanitize_path_component is ahm.sanitize_path_component
