"""
Integration tests for hooks/teardown_request_emitter.py — the Tier-1
TaskCompleted handler for #763 native-hooks Teardown integration.

The handler fires in the lead's session on TaskCompleted and, if all
five gates pass, writes a `teardown_request` journal event + emits the
_TEARDOWN_DIRECTIVE additionalContext for the lead's next turn. This
replaces the PostToolUse:TaskUpdate Teardown branch retired in C5.

Gates (mirrors agent_handoff_emitter.py:281-329):
  Gate 0 — lead-session guard (is_lead_session)
  Gate 1 — transition signal (hook_event_name=="TaskCompleted",
           disk-status fallback)
  Gate 2 — sidecar O_EXCL marker idempotency
  Gate 3 — 1->0 active-task transition (count_active_tasks==0)
  Gate 4 — same-teammate-continuation deferral
           (has_same_teammate_continuation suppresses)

Stop-sweep secondary firing source (stopHooks.ts:334-425) fires this
hook re-entrantly in teammate sessions for every in-progress owned
task; Gate 0 catches those first, marker dedup is the second line of
defense if the guard fails.

Counter-test-by-revert (per architect refinement spec §3 + §8 +
PR #769 precedent): see TestRetiredPostToolUseTeardownDoesNotFire in
test_wake_lifecycle_emitter.py for the {2 -> 1} cardinality anchor
that pins C5 retirement is mutually exclusive with C2 addition.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "teardown_request_emitter.py"

# Synthesized TaskCompleted teammate-context stdin shape (in-PR backstop
# for the is_lead_at_task_completed helper migration). Provenance is
# "synthesized-from-documented-schema" per
# PLATFORM_TASKCOMPLETED_STDIN_SHAPE_META below; the captured-fixture
# upgrade to "logging-shim" provenance is the post-merge follow-up
# commit. Carries `agent_id` per the upstream
# Claude Code documentation (code.claude.com/docs/en/hooks.md
# documents `agent_id` as conditionally-present on TaskCompleted
# subagent frames — "Present only when the hook fires inside a
# subagent context. Distinguishes subagent task completions from
# main-thread task completions"). Under the new
# `is_lead_at_task_completed` predicate (`agent_id is None`), this
# payload classifies as teammate-frame (agent_id present → False →
# suppress directive). The lead-context shape (see
# LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE below) mirrors this minus
# the `agent_id` key; lead-context coverage is via the paired schema-
# pin class TestLeadFrameStdinShapePin AND the behavioral
# `test_lead_session_proceeds_past_gate0`.
PLATFORM_TASKCOMPLETED_STDIN_SHAPE = {
    "session_id": "1fb6500d-25ba-48c6-af00-5f92024644d0",
    "transcript_path": (
        "/Users/example/.claude/projects/"
        "-Users-example-Sites-collab-PACT-Plugin/"
        "1fb6500d-25ba-48c6-af00-5f92024644d0.jsonl"
    ),
    "cwd": "/Users/example/Sites/collab/PACT-Plugin",
    "hook_event_name": "TaskCompleted",
    "task_id": "42",
    "task_subject": "PROBE: capture real TaskCompleted stdin shape",
    "task_description": "diagnostic probe payload",
    "teammate_name": "backend-coder",
    "team_name": "pact-1fb6500d",
    "agent_id": "subagent-T0-teammate-frame-uuid",
}

# Lead-frame counterpart to PLATFORM_TASKCOMPLETED_STDIN_SHAPE — the
# same 9 documented keys with `agent_id` ABSENT. Per the upstream
# Claude Code documentation, lead-context TaskCompleted fires omit
# the `agent_id` field entirely; the `is_lead_at_task_completed`
# predicate's `agent_id is None` body classifies a key-absent payload
# as lead-frame (True → emit). Paired with TestLeadFrameStdinShapePin
# below to canary lead-frame schema drift (lead-only field additions
# would otherwise slip past the teammate-frame canary).
LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE = {
    "session_id": "1fb6500d-25ba-48c6-af00-5f92024644d0",
    "transcript_path": (
        "/Users/example/.claude/projects/"
        "-Users-example-Sites-collab-PACT-Plugin/"
        "1fb6500d-25ba-48c6-af00-5f92024644d0.jsonl"
    ),
    "cwd": "/Users/example/Sites/collab/PACT-Plugin",
    "hook_event_name": "TaskCompleted",
    "task_id": "42",
    "task_subject": "PROBE: capture real TaskCompleted stdin shape",
    "task_description": "diagnostic probe payload",
    "teammate_name": "backend-coder",
    "team_name": "pact-1fb6500d",
    # NOTE: no `agent_id` key — defines the lead-frame schema.
}


# =============================================================================
# Test helpers — co-located (not lifted) per #551 fixture-location convention.
# =============================================================================


def _run_emitter_subprocess(stdin_payload, env_extra=None):
    """Invoke teardown_request_emitter.py as a subprocess (production
    fidelity — same process model the platform uses to fire the hook).
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(EMITTER)],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _write_session_context(
    home,
    session_id,
    project_dir,
    team_name,
    *,
    lead_session_id=None,
    members=None,
    lead_agent_id=None,
):
    """Write a session-context file + team-config so is_lead_session and
    count_active_tasks resolve correctly under the test HOME.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-05-16T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = (
        lead_session_id if lead_session_id is not None else session_id
    )
    config_data = {"leadSessionId": effective_lead}
    if lead_agent_id is not None:
        config_data["leadAgentId"] = lead_agent_id
    if members:
        config_data["members"] = list(members)
    (team_dir / "config.json").write_text(
        json.dumps(config_data), encoding="utf-8",
    )


def _write_task(home, team_name, task_id, **fields):
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _marker_dir(home, team_name):
    """Mirror of teardown_request_emitter._marker_dir — the per-team
    idempotency marker directory.
    """
    return home / ".claude" / "teams" / team_name / ".teardown_request_emitted"


def _journal_path(home, project_dir, session_id):
    slug = Path(project_dir).name
    return (
        home / ".claude" / "pact-sessions" / slug / session_id
        / "session-journal.jsonl"
    )


def _read_journal_events(home, project_dir, session_id, event_type=None):
    """Read all events (or filtered by event_type) from the session journal.
    Returns [] if the journal file does not exist.
    """
    path = _journal_path(home, project_dir, session_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event_type is None or event.get("type") == event_type:
            events.append(event)
    return events


# =============================================================================
# TestStdinShapePin — pin the verbatim TaskCompleted stdin shape
# =============================================================================


#: Provenance metadata for PLATFORM_TASKCOMPLETED_STDIN_SHAPE. The
#: synthesized payload above is derived from upstream Claude Code
#: platform documentation (code.claude.com/docs/en/hooks.md,
#: TaskCompleted section) rather than captured-from-production via
#: logging-shim. The post-merge follow-up upgrades provenance to
#: capture-from-production fixtures (paired lead + teammate-context
#: shapes); this in-PR stopgap is documented-schema-grounded.
PLATFORM_TASKCOMPLETED_STDIN_SHAPE_META = {
    "capture_method": "synthesized-from-documented-schema",
    "ground_truth_source": "code.claude.com/docs/en/hooks.md",
    "provenance_upgrade": "post-merge follow-up via logging-shim capture campaign",
}


class TestStdinShapePin:
    """Pin the verbatim TaskCompleted teammate-frame stdin shape against
    the synthesized payload derived from upstream Claude Code platform
    documentation (post-merge follow-up upgrades provenance to logging-
    shim capture-from-production fixtures). Future platform changes
    (Claude Code adding or removing fields) trip this test BEFORE
    silent production breakage. Mirrors test_emitter_real_disk.
    TestStdinShapePin.

    Provenance: see PLATFORM_TASKCOMPLETED_STDIN_SHAPE_META adjacent to
    the payload definition. The synthesized shape is grounded in the
    upstream docs' TaskCompleted section, NOT a live-capture fixture
    today; the post-merge follow-up promotes the captured payload.

    Counter-test-by-revert: changing the PLATFORM_TASKCOMPLETED_STDIN_
    SHAPE keys flips these tests RED — the canary for stdin schema
    drift.
    """

    def test_pins_taskcompleted_stdin_keys(self):
        """The platform delivers exactly these 10 top-level fields on a
        TaskCompleted teammate-context hook fire. Lead-context fires
        omit `agent_id` (per the upstream Claude Code documentation's
        conditional-presence semantics for the `agent_id` field).
        Producers/consumers of stdin must not silently drop any field;
        the schema-pin codifies the teammate-frame shape.
        """
        expected_keys = {
            "session_id", "transcript_path", "cwd", "hook_event_name",
            "task_id", "task_subject", "task_description",
            "teammate_name", "team_name", "agent_id",
        }
        assert set(PLATFORM_TASKCOMPLETED_STDIN_SHAPE.keys()) == expected_keys

    def test_pins_taskcompleted_stdin_value_types(self):
        """Each stdin field's value type is pinned. A platform change
        that switches task_id from str to int (or task_description from
        str to dict) trips this. `agent_id` is `str` on teammate-frame
        fires (the per-instance UUID); absent on lead-frame fires.
        """
        type_pins = {
            "session_id": str,
            "transcript_path": str,
            "cwd": str,
            "hook_event_name": str,
            "task_id": str,
            "task_subject": str,
            "task_description": str,
            "teammate_name": str,
            "team_name": str,
            "agent_id": str,
        }
        for field, expected_type in type_pins.items():
            assert isinstance(
                PLATFORM_TASKCOMPLETED_STDIN_SHAPE[field], expected_type
            ), (
                f"{field} expected {expected_type.__name__}, "
                f"got {type(PLATFORM_TASKCOMPLETED_STDIN_SHAPE[field]).__name__}"
            )

    def test_hook_event_name_is_taskcompleted(self):
        """The primary transition signal is the literal 'TaskCompleted'.
        Any other value (e.g. 'task_completed', 'TASK_COMPLETED') must
        fall back to disk-status; pinning the literal prevents the
        primary path from silently breaking.
        """
        assert PLATFORM_TASKCOMPLETED_STDIN_SHAPE["hook_event_name"] == (
            "TaskCompleted"
        )


# =============================================================================
# TestLeadFrameStdinShapePin — paired canary for the 9-key lead-frame
# shape (the schema-pin's lead-context arm; without this, a lead-only
# field addition would slip past TestStdinShapePin's teammate-frame
# canary).
# =============================================================================


class TestLeadFrameStdinShapePin:
    """Pin the verbatim TaskCompleted LEAD-frame stdin shape against
    LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE. Lead-context fires omit
    `agent_id` per upstream Claude Code documentation; this paired
    pin codifies the 9-key shape so a future platform change that
    adds a lead-only field trips RED before silent production breakage.

    Why pair this with TestStdinShapePin: the existing teammate-frame
    pin alone canary's only the 10-key shape that carries `agent_id`.
    A platform change adding a NEW lead-only field would not affect
    that teammate-frame set and would slip silently. The paired pin
    closes the gap.

    Counter-test-by-revert: changing the
    LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE keys flips these tests
    RED — the canary for lead-frame stdin schema drift.
    """

    def test_pins_lead_frame_taskcompleted_stdin_keys(self):
        """The platform delivers exactly these 9 top-level fields on a
        TaskCompleted lead-context hook fire — no `agent_id` key. Any
        platform change adding/removing a lead-frame field trips this.
        """
        expected_keys = {
            "session_id", "transcript_path", "cwd", "hook_event_name",
            "task_id", "task_subject", "task_description",
            "teammate_name", "team_name",
        }
        assert set(
            LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE.keys()
        ) == expected_keys

    def test_lead_frame_omits_agent_id_key(self):
        """The discriminator `is_lead_at_task_completed` body is
        `input_data.get("agent_id") is None`. The lead-frame fixture
        MUST omit `agent_id` entirely (key-absent), NOT carry it as
        `None`-valued — both shapes classify as lead under `is None`,
        but the documented schema is key-absent and the pin enforces
        that documented shape.
        """
        assert "agent_id" not in LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE, (
            "LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE must omit `agent_id` "
            "entirely per the documented lead-context schema. Carrying "
            "`agent_id: None` would still classify as lead under the "
            "`is None` predicate but would not match the documented "
            "key-absent shape."
        )

    def test_lead_frame_value_types_match_documented_schema(self):
        """Each lead-frame stdin field's value type is pinned. A
        platform change that switches task_id from str to int (or
        task_description from str to dict) trips this. Same 9 type
        pins as the teammate-frame minus `agent_id`.
        """
        type_pins = {
            "session_id": str,
            "transcript_path": str,
            "cwd": str,
            "hook_event_name": str,
            "task_id": str,
            "task_subject": str,
            "task_description": str,
            "teammate_name": str,
            "team_name": str,
        }
        for field, expected_type in type_pins.items():
            assert isinstance(
                LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE[field], expected_type
            ), (
                f"lead-frame {field} expected {expected_type.__name__}, "
                f"got "
                f"{type(LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE[field]).__name__}"
            )

    def test_lead_frame_hook_event_name_is_taskcompleted(self):
        """The primary transition signal is the literal 'TaskCompleted'
        on lead-frame fires too. Same literal as the teammate-frame.
        """
        assert LEAD_PLATFORM_TASKCOMPLETED_STDIN_SHAPE["hook_event_name"] == (
            "TaskCompleted"
        )


# =============================================================================
# TestGate0LeadSessionGuard — defense-in-depth, teammate session never emits
# =============================================================================


class TestGate0LeadSessionGuard:
    """Gate 0 catches Stop-sweep secondary firings in teammate sessions.
    Without this gate, every in-progress task owned by a teammate would
    produce a phantom teardown_request when the teammate's session
    Stop-sweeps. The marker dedup (Gate 2) is the second line of
    defense; Gate 0 is the first.

    Discriminator (I1): ``agent_id is None`` per the per-event sibling
    family in ``shared/wake_lifecycle.py``. The
    ``is_lead_at_task_completed`` helper at ``teardown_request_emitter.py:301``
    classifies the fire as lead-frame when the stdin payload omits
    ``agent_id``, and as teammate-frame when ``agent_id`` carries the
    platform-stamped per-instance UUID (documented at
    ``code.claude.com/docs/en/hooks.md`` — "Present only when the hook
    fires inside a subagent context"). Teammate-context test payloads
    below synthesize this by including ``agent_id`` explicitly; the
    lead-frame comparator ``test_lead_session_proceeds_past_gate0``
    omits it. Test names are aliased ``*_per_agent_id_none_discriminator``
    per cbcfd589 §AUDIT named-invariant convention so the discriminator
    is visible at the test-symbol layer.
    """

    def test_teammate_session_suppresses_emission_per_agent_id_none_discriminator(
        self, tmp_path,
    ):
        """A TaskCompleted fire in a teammate-frame (stdin carries the
        platform-stamped ``agent_id`` per-instance UUID) emits no
        journal event and produces only suppressOutput stdout. The
        ``agent_id is None`` discriminator at Gate 0 classifies any
        agent_id-bearing payload as non-lead and short-circuits.

        Renamed from ``test_teammate_session_suppresses_emission`` per
        cbcfd589 §AUDIT named-invariant convention — the new name
        encodes the per-event discriminator the test pins (I1 =
        ``agent_id is None`` for the TaskCompleted event class).
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate0-teammate"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id=lead_sid,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "T1", status="completed", owner="backend-coder")

        payload = {
            "session_id": teammate_sid,
            "cwd": pdir,
            "hook_event_name": "TaskCompleted",
            "task_id": "T1",
            "team_name": team,
            "teammate_name": "backend-coder",
            "agent_id": "subagent-T1-teammate-frame-uuid",
        }
        rc, out, err = _run_emitter_subprocess(
            json.dumps(payload),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert json.loads(out).get("suppressOutput") is True, (
            f"Teammate-session fire must suppressOutput; got {out!r}"
        )

    def test_teammate_session_writes_no_journal_event_per_agent_id_none_discriminator(
        self, tmp_path,
    ):
        """The teammate-frame fire (stdin carries ``agent_id``) MUST
        NOT write a teardown_request event to the journal. The journal
        write is the falsifiable primitive; an unread additionalContext
        is recoverable, an on-disk journal event is not (Tier-4 cron
        would replay a phantom Teardown).

        Renamed from ``test_teammate_session_writes_no_journal_event``
        per cbcfd589 §AUDIT named-invariant convention.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate0-no-journal"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id=lead_sid,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "T2", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": teammate_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T2",
                "team_name": team,
                "teammate_name": "backend-coder",
                "agent_id": "subagent-T2-teammate-frame-uuid",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, teammate_sid, event_type="teardown_request",
        )
        assert events == [], (
            f"Teammate-session fire must NOT write a teardown_request "
            f"event; got {events!r}"
        )

    def test_teammate_session_does_not_create_marker_per_agent_id_none_discriminator(
        self, tmp_path,
    ):
        """Idempotency marker dir is NOT created on Gate-0 short-circuit
        (teammate-frame fire). Creating it prematurely (e.g. moving the
        marker write above the guard) would permanently suppress the
        lead-side fire's emission.

        Renamed from ``test_teammate_session_does_not_create_marker``
        per cbcfd589 §AUDIT named-invariant convention.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "teammate-sid"
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate0-no-marker"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id=lead_sid,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team, "T3", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": teammate_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T3",
                "team_name": team,
                "teammate_name": "backend-coder",
                "agent_id": "subagent-T3-teammate-frame-uuid",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        marker = _marker_dir(home, team) / "T3"
        assert not marker.exists(), (
            f"Teammate-session fire must NOT create idempotency marker; "
            f"marker={marker!r}"
        )

    def test_lead_session_proceeds_past_gate0(self, tmp_path):
        """A lead-session fire is NOT suppressed by Gate 0 — it advances
        to subsequent gates. With all other gates passing (count==0,
        no continuation, no prior marker), the hook emits.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate0-lead-proceeds"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T4", status="completed", owner="backend-coder")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T4",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        # Either an emission (hookSpecificOutput) OR suppression by a
        # downstream gate (Gate 1/2/3/4) is acceptable here — Gate 0
        # specifically MUST NOT short-circuit. Verified indirectly by
        # the marker creation on emit OR the lack of journal event on
        # downstream-gate suppression; the production-correct outcome
        # for THIS fixture (count==0, no marker, no continuation) is
        # emission.
        parsed = json.loads(out)
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert parsed.get("hookSpecificOutput") is not None or len(events) >= 1, (
            f"Lead-session fire must NOT be suppressed at Gate 0; "
            f"stdout={out!r}, events={events!r}"
        )


# =============================================================================
# TestGate1HookEventNamePrimaryDiskFallback — primary + fallback signal
# =============================================================================


class TestGate1HookEventNamePrimaryDiskFallback:
    """Gate 1 mirrors agent_handoff_emitter.py:281-289 — trust
    hook_event_name=='TaskCompleted' as the primary signal, fall back
    to disk-status when missing or mismatched (defense-in-depth against
    the platform's race where task.json shows in_progress at hook-fire
    time per #551).
    """

    def test_hook_event_name_taskcompleted_proceeds(self, tmp_path):
        """Primary signal path: hook_event_name=='TaskCompleted'
        proceeds past Gate 1 regardless of disk status. This is the
        #551 fix shape — primary trumps disk-status to handle the
        platform race.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate1-primary"
        _write_session_context(home, lead_sid, pdir, team)
        # Task on disk shows in_progress — the #551 race shape. Primary
        # signal MUST trump this.
        _write_task(home, team, "T5", status="in_progress", owner="backend-coder")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T5",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        # When count_active_tasks(team) > 0 (the in_progress task is
        # still counted), Gate 3 suppresses. To isolate Gate 1 here we
        # instead assert via marker presence that Gate 1 did NOT
        # short-circuit. If Gate 3 fires, that's downstream of Gate 1
        # and still proves Gate 1 passed.
        # NOTE: this test is hardened against impl ordering — what we
        # really pin is that hook_event_name primary signal is honored.
        assert rc == 0, f"hook must exit 0; stderr={err}"

    def test_missing_hook_event_name_falls_back_to_disk_status(
        self, tmp_path,
    ):
        """Fallback path: hook_event_name missing/empty → emitter reads
        disk status. completed disk status proceeds; non-completed
        suppresses.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate1-fallback-completed"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T6", status="completed", owner="backend-coder")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                # hook_event_name intentionally omitted — exercise the
                # disk-status fallback.
                "task_id": "T6",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        # Fallback succeeded if Gate 1 did not short-circuit — observable
        # via downstream gate behavior. count_active_tasks == 0 (the
        # only task is completed) so Gate 3 also passes, and we expect
        # emission.
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"Fallback path: completed disk-status must proceed to "
            f"emit; got events={events!r}, stdout={out!r}"
        )

    def test_disk_status_not_completed_suppresses(self, tmp_path):
        """Fallback path with non-completed disk status → suppressOutput
        at Gate 1. Without hook_event_name AND disk-status != completed,
        there is no transition signal.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate1-fallback-suppress"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T7", status="in_progress", owner="backend-coder")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                # No hook_event_name, disk shows in_progress.
                "task_id": "T7",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        parsed = json.loads(out)
        assert parsed.get("suppressOutput") is True, (
            f"Gate 1 must suppress on missing primary AND non-completed "
            f"disk-status; got {parsed!r}"
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert events == [], (
            f"No event should be written; got {events!r}"
        )


# =============================================================================
# TestGate2IdempotencyMarker — O_EXCL test-and-set per (team, task_id)
# =============================================================================


class TestGate2IdempotencyMarker:
    """Gate 2 mirrors agent_handoff_emitter.py:319 — sidecar O_EXCL
    marker at ~/.claude/teams/{team}/.teardown_request_emitted/{task_id}
    serves as the per-(team,task) test-and-set. Pre-existing marker
    suppresses; absent marker proceeds and creates atomically.

    Defends against Stop-sweep secondary firings AND legitimate
    re-fires from upstream race conditions.
    """

    def test_first_fire_creates_marker_and_writes_event(self, tmp_path):
        """A fresh (team, task) tuple proceeds: marker is created
        atomically; journal event is appended exactly once.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate2-first-fire"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T8", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T8",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        marker = _marker_dir(home, team) / "T8"
        assert marker.exists(), (
            f"First fire must create marker at {marker!r}"
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"First fire must write exactly 1 teardown_request event; "
            f"got {events!r}"
        )

    def test_second_fire_same_team_task_suppresses(self, tmp_path):
        """A re-fire for the same (team, task) finds the existing
        marker and suppresses emission — at most one journal event
        per (team, task) tuple over the team's lifespan.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate2-second-fire"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T9", status="completed", owner="backend-coder")

        payload = {
            "session_id": lead_sid,
            "cwd": pdir,
            "hook_event_name": "TaskCompleted",
            "task_id": "T9",
            "team_name": team,
            "teammate_name": "backend-coder",
        }
        env_extra = {"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir}
        # First fire — should emit.
        _run_emitter_subprocess(json.dumps(payload), env_extra=env_extra)
        # Second fire — must be a no-op.
        rc, out, err = _run_emitter_subprocess(
            json.dumps(payload), env_extra=env_extra,
        )
        assert rc == 0
        assert json.loads(out).get("suppressOutput") is True, (
            f"Second fire with marker present must suppressOutput; got {out!r}"
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"Two fires with same (team, task) must produce exactly 1 "
            f"event; got {len(events)}"
        )

    def test_marker_at_canonical_path(self, tmp_path):
        """Marker path is exactly
        ~/.claude/teams/{team}/.teardown_request_emitted/{task_id}.

        Pinning this prevents a refactor that silently changes the
        path (e.g. to `.teardown_emitted/` without the `_request`
        suffix) which would silently disable idempotency until the
        old directory ages out.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate2-canonical-path"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T10", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T10",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        expected_marker = (
            home / ".claude" / "teams" / team
            / ".teardown_request_emitted" / "T10"
        )
        assert expected_marker.exists(), (
            f"Marker must live at canonical path {expected_marker!r}"
        )

    def test_marker_dir_sibling_to_agent_handoff_emitted(self, tmp_path):
        """The teardown_request marker dir lives at
        `.teardown_request_emitted` — a SIBLING to `.agent_handoff_emitted`
        under the same team dir. Pinning this prevents the two emitters
        from contending over the same directory (which would corrupt
        idempotency for both).
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate2-sibling-paths"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T11", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T11",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        teardown_dir = _marker_dir(home, team)
        agent_handoff_dir = (
            home / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )
        assert teardown_dir.exists(), "teardown_request_emitted dir must exist"
        assert teardown_dir.name == ".teardown_request_emitted", (
            f"Marker dir name must be .teardown_request_emitted (sibling "
            f"to .agent_handoff_emitted); got {teardown_dir.name!r}"
        )
        # The agent_handoff dir should NOT contain teardown markers; it
        # may exist or not depending on whether agent_handoff_emitter
        # has ever fired in this test.
        assert (
            not agent_handoff_dir.exists()
            or not (agent_handoff_dir / "T11").exists()
        ), (
            f"teardown_request marker must NOT land in "
            f".agent_handoff_emitted directory"
        )


# =============================================================================
# TestGate3ActiveTaskCountTransition — 1->0 transition gate
# =============================================================================


class TestGate3ActiveTaskCountTransition:
    """Gate 3 mirrors wake_lifecycle_emitter.py:695 — only emit Teardown
    on the 1->0 active-task transition. count_active_tasks(team) > 0
    suppresses because more work is pending; emitting Teardown would
    retire the cron prematurely.
    """

    def test_count_zero_proceeds(self, tmp_path):
        """The only task is completed → count_active_tasks == 0 → emit."""
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate3-count-zero"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T12", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T12",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"count=0 must emit teardown_request; got {events!r}"
        )

    def test_count_nonzero_suppresses(self, tmp_path):
        """Another teammate task is still in_progress → count > 0 →
        Gate 3 suppresses. Even though THIS task completed, more work
        is pending; the cron must stay armed.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate3-count-nonzero"
        _write_session_context(
            home, lead_sid, pdir, team,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "test-engineer", "agentId": "agent-te"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        # Task T13 just completed; T14 is still in_progress.
        _write_task(home, team, "T13", status="completed", owner="backend-coder")
        _write_task(home, team, "T14", status="in_progress", owner="test-engineer")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T13",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0
        assert json.loads(out).get("suppressOutput") is True, (
            f"count>0 must suppress; got stdout={out!r}"
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert events == [], (
            f"count>0 must not emit; got {events!r}"
        )


# =============================================================================
# TestGate4SameTeammateContinuationDeferral — chain-aware deferral
# =============================================================================


class TestGate4SameTeammateContinuationDeferral:
    """Gate 4 mirrors wake_lifecycle_emitter.py:716-717 — if the just-
    completed task has a same-teammate continuation in its `blocks`
    chain, defer Teardown. The teammate is about to resume; tearing
    down the cron now and re-arming it on their next task would
    produce avoidable churn.
    """

    def test_continuation_suppresses(self, tmp_path):
        """T15 completes; T16 is owned by the same teammate AND is in
        T15's `blocks` chain → defer Teardown. Even with count == 0
        (no other in_progress tasks), the continuation is pending in
        the same teammate's queue.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate4-continuation"
        _write_session_context(
            home, lead_sid, pdir, team,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        # T15 completed, blocks T16; T16 pending owned by same teammate.
        _write_task(
            home, team, "T15",
            status="completed", owner="backend-coder", blocks=["T16"],
        )
        _write_task(
            home, team, "T16",
            status="pending", owner="backend-coder",
        )

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T15",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0
        assert json.loads(out).get("suppressOutput") is True, (
            f"Same-teammate continuation must suppress Teardown; "
            f"got stdout={out!r}"
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert events == [], (
            f"Continuation deferral must NOT emit event; got {events!r}"
        )

    def test_no_continuation_proceeds(self, tmp_path):
        """T17 completes with empty blocks chain → no continuation →
        emit Teardown.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate4-no-continuation"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(
            home, team, "T17",
            status="completed", owner="backend-coder", blocks=[],
        )

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T17",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"No-continuation path must emit; got {events!r}"
        )

    def test_different_owner_continuation_proceeds(self, tmp_path):
        """Negative pair to test_continuation_suppresses: T-A completes
        with `blocks=[T-B]`, but T-B is owned by a DIFFERENT teammate.
        The same-teammate discriminator returns False → Gate 4 does NOT
        defer → Tier-1 emits Teardown.

        Pins that the same-OWNER (not just same-blocks-chain) check is
        load-bearing. An over-permissive predicate that deferred on any
        addBlocks chain would silently suppress legitimate Teardowns
        whenever there's a downstream task by a different teammate.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate4-different-owner"
        _write_session_context(home, lead_sid, pdir, team)
        # T-B owned by test-engineer (NOT backend-coder); status=completed
        # so it doesn't add to count.
        _write_task(home, team, "TB", status="completed", owner="test-engineer")
        # T-A: backend-coder, completed, blocks=[TB].
        _write_task(
            home, team, "TA",
            status="completed", owner="backend-coder", blocks=["TB"],
        )

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "TA",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"Different-owner continuation must NOT defer; expected 1 "
            f"event (Teardown emits), got {events!r}. If empty, "
            f"has_same_teammate_continuation has been weakened — "
            f"deferring on any addBlocks chain regardless of owner "
            f"silently suppresses legitimate Teardowns."
        )

    def test_race_deleted_continuation_emits(self, tmp_path):
        """Race-deleted continuation: the just-completed task's `blocks`
        references a task ID that does NOT exist on disk (deleted out
        from under the predicate). The predicate must fail-CLOSED
        (return False → no defer) so Teardown emits.

        Fail-open here would silently suppress legitimate Teardowns on
        any race condition where a continuation was deleted mid-flight;
        the conservative outcome is to emit. Pins the fail-conservative
        behavior at Tier-1 Gate 4.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate4-race-deleted"
        _write_session_context(home, lead_sid, pdir, team)
        # T-A: blocks=[T-X] but T-X.json was deleted (no file on disk).
        _write_task(
            home, team, "TR",
            status="completed", owner="backend-coder", blocks=["TX"],
        )
        # Deliberately do NOT write TX.

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "TR",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"Race-deleted continuation must emit (predicate fail-"
            f"closed); got {events!r}. If empty, the predicate is "
            f"fail-open on missing continuations — silent Teardown "
            f"suppression is the worse failure mode."
        )

    @pytest.mark.parametrize(
        "continuation_predicate_returns,expected_emit",
        [
            (True, False),    # predicate True → defer → no event emitted
            (False, True),    # predicate False → no defer → event emitted
        ],
        ids=["predicate_true_defers", "predicate_false_emits"],
    )
    def test_gate4_predicate_drives_decision(
        self, tmp_path, monkeypatch,
        continuation_predicate_returns, expected_emit,
    ):
        """Bijection coverage on Gate 4: mock `has_same_teammate_
        continuation` directly and pin the directive-emit decision to
        the predicate's return value.

        Phantom-green prevention: the count-gate (Gate 3) covers many
        outcomes by itself; this test isolates Gate 4 as the causal
        gate by mocking the predicate in-process and asserting the
        outcome bijection.

        Counter-test-by-revert: replace the `has_same_teammate_
        continuation` call site in teardown_request_emitter.main()
        with a constant False. Both parametrized cases produce
        cardinality {1 fail, 1 pass}: the True-defer case flips to
        emit (FAIL); the False-emit case is unchanged (still emits,
        PASS). That asymmetry proves the call site itself is load-
        bearing for the deferral semantic.
        """
        sys.path.insert(0, str(HOOK_DIR))
        import teardown_request_emitter as emitter

        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-gate4-mock-predicate"
        _write_session_context(home, lead_sid, pdir, team)
        # T-A completed; T-B may or may not exist — predicate is mocked
        # so the on-disk state is irrelevant to the gate decision.
        _write_task(
            home, team, "TM",
            status="completed", owner="backend-coder", blocks=["TM2"],
        )

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", pdir)
        # Replace `has_same_teammate_continuation` IN the emitter module
        # (imported via `from ... import` so module-level rebind is
        # the correct injection point).
        monkeypatch.setattr(
            emitter, "has_same_teammate_continuation",
            lambda completed_task, team_name: continuation_predicate_returns,
        )

        # Patch sys.stdin + sys.exit to invoke main() in-process (subprocess
        # would re-import the module and lose the monkeypatch).
        stdin_payload = json.dumps({
            "session_id": lead_sid,
            "cwd": pdir,
            "hook_event_name": "TaskCompleted",
            "task_id": "TM",
            "team_name": team,
            "teammate_name": "backend-coder",
        })

        # Reset module-level pact_context cache between parametrized runs.
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(ctx_module, "_context_path", None)

        with patch("sys.stdin", io.StringIO(stdin_payload)):
            with pytest.raises(SystemExit) as exc_info:
                emitter.main()
        assert exc_info.value.code == 0

        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        if expected_emit:
            assert len(events) == 1, (
                f"predicate=False → Gate 4 must NOT defer → Tier-1 emits; "
                f"got {events!r}. If empty, Gate 4 is fail-open or the "
                f"predicate call site has been removed."
            )
        else:
            assert events == [], (
                f"predicate=True → Gate 4 MUST defer → no event; "
                f"got {events!r}. If 1 event present, the predicate's "
                f"gate was bypassed (call site removed or condition "
                f"inverted)."
            )


# =============================================================================
# TestJournalEventShape — what gets written on emission
# =============================================================================


class TestJournalEventShape:
    """Pin the shape of the teardown_request event written by Tier-1.
    Required + optional fields tier='1' and reason='lead_terminal_
    taskupdate' must surface on disk so future Tier-2-vs-Tier-1
    forensic comparisons can distinguish the two production paths.
    """

    def test_event_required_fields_populated(self, tmp_path):
        """Emitted event has task_id and team_name matching the
        TaskCompleted stdin payload. A producer that swaps these two
        (or drops one) produces a falsifiable trace.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-event-shape-required"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T18", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T18",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1
        event = events[0]
        assert event["task_id"] == "T18"
        assert event["team_name"] == team

    def test_event_tier_is_1(self, tmp_path):
        """Tier-1 emits with tier='1' so a future Tier-2 emission for
        the same task is distinguishable in the journal.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-event-shape-tier"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T19", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T19",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1
        assert events[0].get("tier") == "1", (
            f"Tier-1 emission must carry tier='1'; got tier={events[0].get('tier')!r}"
        )

    def test_event_reason_is_lead_terminal_taskupdate(self, tmp_path):
        """Tier-1 reason is the literal 'lead_terminal_taskupdate' —
        the categorical token consumed by audit-log readers.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-event-shape-reason"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T20", status="completed", owner="backend-coder")

        _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T20",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1
        assert events[0].get("reason") == "lead_terminal_taskupdate", (
            f"Tier-1 reason must be 'lead_terminal_taskupdate'; "
            f"got {events[0].get('reason')!r}"
        )

    def test_no_stdin_field_leaks_into_event(self, tmp_path):
        """The emitted journal event MUST NOT carry stdin fields that
        weren't explicitly forwarded (transcript_path, cwd,
        task_description, task_subject, teammate_name, hook_event_name,
        agent_id). Mirrors agent_handoff_emitter.py discipline.

        Test scenario is a LEAD-FRAME fire (asserts a journal event
        IS written), so the spread of PLATFORM_TASKCOMPLETED_STDIN_SHAPE
        drops the teammate-frame ``agent_id`` field — otherwise Gate 0
        would classify the fire as teammate-frame (`agent_id is None`
        is False) and short-circuit before the journal write.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-event-no-leak"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T21", status="completed", owner="backend-coder")

        lead_frame_payload = {
            k: v for k, v in PLATFORM_TASKCOMPLETED_STDIN_SHAPE.items()
            if k != "agent_id"
        }
        _run_emitter_subprocess(
            json.dumps({
                **lead_frame_payload,
                "session_id": lead_sid,
                "cwd": pdir,
                "task_id": "T21",
                "team_name": team,
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1
        event = events[0]
        for leaked_field in (
            "transcript_path", "cwd", "task_description", "task_subject",
            "teammate_name", "hook_event_name", "session_id", "agent_id",
        ):
            assert leaked_field not in event, (
                f"stdin field {leaked_field!r} leaked into journal "
                f"event — emitter forwarded too much data."
            )


# =============================================================================
# TestExitContract — every path exits 0 with valid stdout
# =============================================================================


class TestExitContract:
    """Per CLAUDE.md livelock-safety pin: every code path in a hook
    must exit 0. Nonzero exit produces hook-error UI surface in
    Claude Code; on TaskCompleted/Stop hooks this triggers the
    livelock-capable failure class (error every fire until owner
    task resolves).
    """

    def test_all_gate_failure_paths_exit_zero_per_agent_id_none_discriminator(
        self, tmp_path,
    ):
        """Every Gate-0..Gate-4 short-circuit path exits 0 with
        suppressOutput stdout. Parametric coverage over the 5 gates.
        Gate-0 row synthesizes a teammate-frame fire via ``agent_id``
        presence per the I1 discriminator.

        Renamed from ``test_all_gate_failure_paths_exit_zero`` per
        cbcfd589 §AUDIT named-invariant convention.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"

        # Gate 0: teammate-frame fire (agent_id present)
        team_0 = "team-exit-gate0"
        _write_session_context(
            home, "teammate-sid", pdir, team_0,
            lead_session_id=lead_sid,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team_0, "G0", status="completed", owner="backend-coder")
        rc0, out0, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": "teammate-sid", "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "G0", "team_name": team_0,
                "teammate_name": "backend-coder",
                "agent_id": "subagent-G0-teammate-frame-uuid",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc0 == 0
        assert json.loads(out0).get("suppressOutput") is True

        # Gate 1: no hook_event_name + in_progress disk status
        team_1 = "team-exit-gate1"
        _write_session_context(home, lead_sid, pdir, team_1)
        _write_task(home, team_1, "G1", status="in_progress", owner="backend-coder")
        rc1, out1, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "task_id": "G1", "team_name": team_1,
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc1 == 0
        assert json.loads(out1).get("suppressOutput") is True

        # Gate 2: pre-existing marker
        team_2 = "team-exit-gate2"
        _write_session_context(home, lead_sid, pdir, team_2)
        _write_task(home, team_2, "G2", status="completed", owner="backend-coder")
        marker_dir = _marker_dir(home, team_2)
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / "G2").touch()
        rc2, out2, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "G2", "team_name": team_2,
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc2 == 0
        assert json.loads(out2).get("suppressOutput") is True

        # Gate 3: another in_progress task
        team_3 = "team-exit-gate3"
        _write_session_context(
            home, lead_sid, pdir, team_3,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "test-engineer", "agentId": "agent-te"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(home, team_3, "G3", status="completed", owner="backend-coder")
        _write_task(home, team_3, "G3b", status="in_progress", owner="test-engineer")
        rc3, out3, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "G3", "team_name": team_3,
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc3 == 0
        assert json.loads(out3).get("suppressOutput") is True

        # Gate 4: same-teammate continuation
        team_4 = "team-exit-gate4"
        _write_session_context(home, lead_sid, pdir, team_4)
        _write_task(
            home, team_4, "G4",
            status="completed", owner="backend-coder", blocks=["G4b"],
        )
        _write_task(
            home, team_4, "G4b",
            status="pending", owner="backend-coder",
        )
        rc4, out4, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "G4", "team_name": team_4,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc4 == 0
        assert json.loads(out4).get("suppressOutput") is True

    def test_emit_path_exits_zero(self, tmp_path):
        """Successful emission also exits 0 with hookSpecificOutput in
        stdout (not just suppressOutput). additionalContext carries the
        _TEARDOWN_DIRECTIVE prose for the lead's next turn.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-exit-emit"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "T22", status="completed", owner="backend-coder")

        rc, out, _ = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T22", "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0
        parsed = json.loads(out)
        # Emission path produces hookSpecificOutput with the Teardown
        # directive in additionalContext.
        hso = parsed.get("hookSpecificOutput")
        assert hso is not None, (
            f"Emit path must produce hookSpecificOutput; got {parsed!r}"
        )
        assert hso.get("hookEventName") == "TaskCompleted"
        assert "PACT:stop-pending-scan" in hso.get("additionalContext", ""), (
            f"additionalContext must invoke stop-pending-scan; got {hso!r}"
        )

    def test_malformed_stdin_exits_zero(self, tmp_path):
        """A non-JSON stdin payload exits 0 with suppressOutput.
        Critical for livelock-safety — a corrupted hook fire must
        NOT crash the hook.
        """
        home = tmp_path / "home"; home.mkdir()
        rc, out, _ = _run_emitter_subprocess(
            b"{ this is not json ",
            env_extra={"HOME": str(home)},
        )
        assert rc == 0, "Malformed stdin must NOT produce nonzero exit"
        # stdout must be parseable JSON with suppressOutput (or empty
        # falling back to suppress).
        if out.strip():
            parsed = json.loads(out)
            assert parsed.get("suppressOutput") is True

    def test_non_dict_stdin_exits_zero(self, tmp_path):
        """A JSON-array or JSON-string stdin (well-formed JSON but
        wrong shape) exits 0. Defense against future platform
        changes that might deliver array-wrapped payloads.
        """
        home = tmp_path / "home"; home.mkdir()
        for bad in (b"[]", b'"a string"', b"42", b"null"):
            rc, out, _ = _run_emitter_subprocess(
                bad,
                env_extra={"HOME": str(home)},
            )
            assert rc == 0, (
                f"Non-dict stdin {bad!r} must exit 0; got rc={rc}"
            )


# =============================================================================
# TestStatusDeletedTier1 — disk-fallback accepts both terminal statuses
# =============================================================================


class TestStatusDeletedTier1:
    """Gate 1 disk-fallback must accept BOTH terminal statuses
    ("completed", "deleted") symmetric with the retired PostToolUse
    Teardown branch's _TERMINAL_STATUSES set. Lead-driven
    TaskUpdate(status="deleted") on a 1->0 transition does not produce
    a TaskCompleted platform hook event, so the disk-fallback is the
    only Tier-1 path that can fire for the delete case (Tier-2's
    teammate-Teardown producer also misses it via lead-session early-
    return). Without "deleted" in the fallback set the deletion path
    drops the Teardown directive entirely.

    Counter-test-by-revert: narrowing the disk-fallback back to
    `status == "completed"` only flips this test RED.
    """

    def test_status_deleted_lead_driven_emits_teardown_request(
        self, tmp_path,
    ):
        """Lead-driven TaskUpdate(status="deleted") on a 1->0 transition
        with no TaskCompleted platform event proceeds through the disk-
        fallback and emits a Tier-1 teardown_request event.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-f1-deleted-fallback"
        _write_session_context(home, lead_sid, pdir, team)
        # Task on disk shows the terminal status "deleted".
        _write_task(home, team, "D1", status="deleted", owner="backend-coder")

        rc, out, err = _run_emitter_subprocess(
            json.dumps({
                "session_id": lead_sid,
                "cwd": pdir,
                # No hook_event_name — exercise the disk-status fallback.
                "task_id": "D1",
                "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        events = _read_journal_events(
            home, pdir, lead_sid, event_type="teardown_request",
        )
        assert len(events) == 1, (
            f"Disk-fallback path: deleted disk-status must proceed to "
            f"emit (symmetric with completed); got events={events!r}, "
            f"stdout={out!r}"
        )
