"""Location: pact-plugin/visualizer/demo.py
Summary: The scripted training simulation behind GET /api/demo. A deterministic
         run through all four PACT phases that exercises every visual: nine
         agents, dispatches and dispatch decisions, teachbacks, four six-field
         handoffs, commits, review + findings + remediation, two gate
         advisories and one gate-health failure, an algedonic alert and a
         blocker, checkpoints, a compaction, a team realign, telemetry loss and
         a wrap-up MERGED session_end.
Used by: visualizer.server (/api/demo), tests.

The demo is built by feeding a scripted journal through the SAME
model.derive_model pipeline as a real session, so there is exactly one
derivation path: a demo-only shape cannot drift from the real shape. Only the
base clock is injectable; every other value is fixed, so repeated calls return
the same model (the contract's "deterministic except base time").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import journal, model, roster
from .settings import DEMO_ETAG

_DEMO_LEAD_ADDRESS = ""
_BASE_TIME = datetime(2026, 11, 2, 23, 0, 0, tzinfo=timezone.utc)

_DEMO_SOURCE: dict[str, Any] = {
    "kind": "demo",
    "root": "demo",
    "slug": "training-simulation",
    "session_id": "demo",
    "journal": "",
    "mtime": 0,
    "size": 0,
}

_AGENT_SEEDS: tuple[roster.MemberSeed, ...] = (
    roster.MemberSeed(
        name="team-lead",
        role="lead",
        agent_type="team-lead",
        color="yellow",
        model="claude-opus-4-8",
        kind="lead",
        source="demo",
    ),
    roster.MemberSeed(
        name="preparer",
        role="preparer",
        agent_type="pact-preparer",
        color="green",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="secretary",
        role="secretary",
        agent_type="pact-secretary",
        color="red",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="architect",
        role="architect",
        agent_type="pact-architect",
        color="blue",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="auditor",
        role="auditor",
        agent_type="pact-auditor",
        color="purple",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="backend-coder",
        role="backend-coder",
        agent_type="pact-backend-coder",
        color="purple",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="frontend-coder",
        role="frontend-coder",
        agent_type="pact-frontend-coder",
        color="pink",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="test-engineer",
        role="test-engineer",
        agent_type="pact-test-engineer",
        color="orange",
        model="claude-opus-4-8",
        source="demo",
    ),
    roster.MemberSeed(
        name="security-engineer",
        role="security-engineer",
        agent_type="pact-security-engineer",
        color="red",
        model="claude-opus-4-8",
        source="demo",
    ),
)

# Task seeds mirror the exemplar's task board. The derivation enriches them
# from the event stream (owners, statuses, teachback/handoff flags), so these
# are the starting state, not a replacement for the events.
_TASK_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "id": "1",
        "subject": "Build JWT authentication",
        "owner": "team-lead",
        "phase": "TEST",
        "status": "completed",
        "kind": "feature",
        "variety": {"novelty": 3, "scope": 3, "uncertainty": 2, "risk": 2, "total": 10},
        "updated_at": "2026-11-02T23:42:00Z",
        "source": "derived",
    },
    {
        "id": "2",
        "subject": "preparer: research JWT auth",
        "owner": "preparer",
        "phase": "PREPARE",
        "status": "handoff",
        "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
        "teachback": True,
        "handoff": True,
        "updated_at": "2026-11-02T23:06:00Z",
        "source": "derived",
    },
    {
        "id": "4",
        "subject": "architect: design the auth flow",
        "owner": "architect",
        "phase": "ARCHITECT",
        "status": "handoff",
        "variety": {"novelty": 2, "scope": 3, "uncertainty": 2, "risk": 2, "total": 9},
        "teachback": True,
        "handoff": True,
        "updated_at": "2026-11-02T23:14:00Z",
        "source": "derived",
    },
    {
        "id": "6",
        "subject": "backend-coder: implement auth service",
        "owner": "backend-coder",
        "phase": "CODE",
        "status": "handoff",
        "variety": {"novelty": 1, "scope": 3, "uncertainty": 2, "risk": 3, "total": 9},
        "teachback": True,
        "handoff": True,
        "updated_at": "2026-11-02T23:24:00Z",
        "source": "derived",
    },
    {
        "id": "7",
        "subject": "frontend-coder: build auth UI",
        "owner": "frontend-coder",
        "phase": "CODE",
        "status": "handoff",
        "variety": {"novelty": 1, "scope": 2, "uncertainty": 2, "risk": 1, "total": 6},
        "teachback": True,
        "handoff": True,
        "updated_at": "2026-11-02T23:25:00Z",
        "source": "derived",
    },
    {
        "id": "8",
        "subject": "test-engineer: verify auth flows",
        "owner": "test-engineer",
        "phase": "TEST",
        "status": "active",
        "variety": {"novelty": 1, "scope": 3, "uncertainty": 2, "risk": 2, "total": 8},
        "teachback": True,
        "updated_at": "2026-11-02T23:35:00Z",
        "source": "derived",
    },
    {
        "id": "9",
        "subject": "security-engineer: adversarial auth review",
        "owner": "security-engineer",
        "phase": "TEST",
        "status": "active",
        "variety": {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 4, "total": 10},
        "teachback": True,
        "handoff": True,
        "updated_at": "2026-11-02T23:34:00Z",
        "source": "derived",
    },
    {
        "id": "10",
        "subject": "backend-coder: fix refresh rotation",
        "owner": "backend-coder",
        "phase": "TEST",
        "status": "completed",
        "variety": {"novelty": 1, "scope": 1, "uncertainty": 1, "risk": 3, "total": 6},
        "updated_at": "2026-11-02T23:33:00Z",
        "source": "derived",
    },
)

_HANDOFF_PREPARE = {
    "produced": "docs/preparation/jwt-research.md",
    "decisions": "Short-lived access tokens with rotating refresh tokens.",
    "reasoning_chain": "Stateless auth keeps the API horizontally scalable, so a refresh path is required.",
    "uncertainty": "[MEDIUM] Clock skew on refresh expiry; [LOW] library choice is reversible.",
    "integration": "AuthService, token middleware, and the users table.",
    "open_questions": "Should refresh tokens be rotated on every use?",
}

_HANDOFF_ARCHITECT = {
    "produced": "docs/architecture/auth-flow.md",
    "decisions": "Three service boundaries: AuthService, TokenStore, Middleware.",
    "reasoning_chain": "TokenStore isolates rotation so AuthService stays pure.",
    "uncertainty": "[LOW] middleware ordering assumes JSON body parsing first.",
    "integration": "Router and error middleware.",
    "open_questions": "Whether to cache public keys.",
}

_HANDOFF_BACKEND = {
    "produced": "src/auth/service.ts, token-store.ts, middleware.ts",
    "decisions": "Refresh tokens rotate on use; reuse is treated as theft.",
    "reasoning_chain": "Rotation gives revocation without server-side session lookup.",
    "uncertainty": "[HIGH] concurrent refresh requests may race; cover with a test.",
    "integration": "users table, router, error middleware.",
    "open_questions": "Should logout revoke all device tokens?",
}

_HANDOFF_FRONTEND = {
    "produced": "src/ui/auth/*.tsx",
    "decisions": "Optimistic session guard with a silent refresh attempt.",
    "reasoning_chain": "Avoids a flash of unauthenticated UI on reload.",
    "uncertainty": "[MEDIUM] offline behavior is untested.",
    "integration": "App shell and route guard.",
    "open_questions": "None",
}

_CHECKPOINT_VARIETY = {"novelty": 3, "scope": 3, "uncertainty": 2, "risk": 2, "total": 10}


def _scripted_events(base: datetime) -> list[dict[str, Any]]:
    """The demo's journal, in file order, with ts derived from the base clock."""
    events: list[dict[str, Any]] = []

    def add(seconds: int, event_type: str, **fields: Any) -> None:
        stamp = base + timedelta(seconds=seconds)
        events.append(
            {
                "v": 1,
                "type": event_type,
                "ts": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                **fields,
            }
        )

    # --- PREPARE ---------------------------------------------------------
    add(
        0,
        "session_start",
        session_id="demo",
        project_dir="/Users/demo/Sites/pact-demo",
        team="session-demo01",
        source="startup",
    )
    add(0, "phase_transition", phase="PREPARE", status="started")
    add(
        30,
        "variety_assessed",
        task_id="1",
        variety={"novelty": 3, "scope": 3, "uncertainty": 2, "risk": 2, "total": 10},
    )
    add(
        60,
        "agent_dispatch",
        agent="preparer",
        task_id="2",
        phase="PREPARE",
        scope=["research JWT auth"],
    )
    add(
        60,
        "dispatch_decision",
        decision="ALLOW",
        rule="dispatch_gate",
        name="preparer",
        team_name="session-demo01",
        resolved_team="session-demo01",
    )
    add(
        120,
        "agent_dispatch",
        agent="secretary",
        task_id="3",
        phase="PREPARE",
        scope=["record the plan"],
    )
    add(
        150,
        "variety_assessed",
        task_id="2",
        scope="dispatch",
        variety={"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
    )
    add(
        180,
        "teachback_ack",
        agent="preparer",
        task_id="2",
        rationale_articulates_this_dispatch="yes",
    )
    add(
        240,
        "task_metadata_snapshot",
        task_id="2",
        subject="preparer: research JWT auth",
        owner="preparer",
        metadata={
            "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
            "teachback_submit": {"understanding": "rotate refresh tokens"},
        },
    )
    add(
        300,
        "checkpoint",
        phase="PREPARE",
        completed_phases=["PREPARE"],
        active_agents=[
            {"name": "preparer", "task_id": "2", "status": "completed"},
            {"name": "secretary", "task_id": "3", "status": "completed"},
        ],
        variety=_CHECKPOINT_VARIETY,
    )
    add(
        330,
        "agent_handoff",
        agent="preparer",
        task_id="2",
        task_subject="preparer: research JWT auth",
        handoff=_HANDOFF_PREPARE,
    )
    add(
        360,
        "commit",
        sha="94c2da8",
        message="Add PREPARE research",
        phase="PREPARE",
        detail="docs/preparation/jwt-research.md",
    )
    add(420, "phase_transition", phase="PREPARE", status="completed")

    # --- ARCHITECT -------------------------------------------------------
    add(480, "phase_transition", phase="ARCHITECT", status="started")
    add(
        540,
        "agent_dispatch",
        agent="architect",
        task_id="4",
        phase="ARCHITECT",
        scope=["design the auth flow"],
    )
    add(
        540,
        "agent_dispatch",
        agent="auditor",
        task_id="5",
        phase="ARCHITECT",
        scope=["observe quality"],
    )
    add(
        570,
        "variety_assessed",
        task_id="4",
        scope="dispatch",
        variety={"novelty": 2, "scope": 3, "uncertainty": 2, "risk": 2, "total": 9},
    )
    add(
        600,
        "teachback_ack",
        agent="architect",
        task_id="4",
        rationale_articulates_this_dispatch="yes",
    )
    add(
        720,
        "agent_handoff",
        agent="architect",
        task_id="4",
        task_subject="architect: design the auth flow",
        handoff=_HANDOFF_ARCHITECT,
    )
    add(
        780,
        "checkpoint",
        phase="ARCHITECT",
        completed_phases=["PREPARE", "ARCHITECT"],
        active_agents=[
            {"name": "architect", "task_id": "4", "status": "completed"},
            {"name": "auditor", "task_id": "5", "status": "active"},
        ],
        variety=_CHECKPOINT_VARIETY,
    )
    add(840, "phase_transition", phase="ARCHITECT", status="completed")

    # --- CODE ------------------------------------------------------------
    add(900, "phase_transition", phase="CODE", status="started")
    add(
        960,
        "agent_dispatch",
        agent="backend-coder",
        task_id="6",
        phase="CODE",
        scope=["implement auth service"],
    )
    add(
        960,
        "agent_dispatch",
        agent="frontend-coder",
        task_id="7",
        phase="CODE",
        scope=["build auth UI"],
    )
    add(
        990,
        "dispatch_decision",
        decision="ALLOW",
        rule="dispatch_gate",
        name="backend-coder",
        team_name="session-demo01",
        resolved_team="session-demo01",
    )
    add(
        1020,
        "variety_assessed",
        task_id="6",
        scope="dispatch",
        variety={"novelty": 1, "scope": 3, "uncertainty": 2, "risk": 3, "total": 9},
    )
    add(
        1020,
        "variety_assessed",
        task_id="7",
        scope="dispatch",
        variety={"novelty": 1, "scope": 2, "uncertainty": 2, "risk": 1, "total": 6},
    )
    add(
        1050,
        "teachback_ack",
        agent="backend-coder",
        task_id="6",
        rationale_articulates_this_dispatch="yes",
    )
    add(
        1080,
        "teachback_ack",
        agent="frontend-coder",
        task_id="7",
        rationale_articulates_this_dispatch="concern",
        concern="refresh race may need a test",
    )
    add(
        1140,
        "commit",
        sha="1a2b3c4",
        message="Add AuthService and token store",
        phase="CODE",
    )
    add(
        1200,
        "commit",
        sha="5d6e7f8",
        message="Add login and refresh endpoints",
        phase="CODE",
    )
    add(
        1260,
        "review_finding",
        severity="suggestion",
        finding="Duplicated cookie options in login and refresh handlers.",
        reviewer="auditor",
        task_id="",
    )
    add(
        1320,
        "commit",
        sha="9a0b1c2",
        message="Add auth client and session guard",
        phase="CODE",
    )
    add(
        1380,
        "agent_handoff",
        agent="backend-coder",
        task_id="6",
        task_subject="backend-coder: implement auth service",
        handoff=_HANDOFF_BACKEND,
    )
    add(
        1440,
        "agent_handoff",
        agent="frontend-coder",
        task_id="7",
        task_subject="frontend-coder: build auth UI",
        handoff=_HANDOFF_FRONTEND,
    )
    add(
        1500,
        "checkpoint",
        phase="CODE",
        completed_phases=["PREPARE", "ARCHITECT", "CODE"],
        active_agents=[
            {"name": "backend-coder", "task_id": "6", "status": "completed"},
            {"name": "frontend-coder", "task_id": "7", "status": "completed"},
        ],
        variety=_CHECKPOINT_VARIETY,
    )
    add(1560, "phase_transition", phase="CODE", status="completed")

    # --- TEST ------------------------------------------------------------
    add(1620, "phase_transition", phase="TEST", status="started")
    add(
        1680,
        "agent_dispatch",
        agent="test-engineer",
        task_id="8",
        phase="TEST",
        scope=["verify auth flows"],
    )
    add(
        1680,
        "agent_dispatch",
        agent="security-engineer",
        task_id="9",
        phase="TEST",
        scope=["adversarial auth review"],
    )
    add(
        1710,
        "variety_assessed",
        task_id="8",
        scope="dispatch",
        variety={"novelty": 1, "scope": 3, "uncertainty": 2, "risk": 2, "total": 8},
    )
    add(
        1710,
        "variety_assessed",
        task_id="9",
        scope="dispatch",
        variety={"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 4, "total": 10},
    )
    add(
        1740,
        "teachback_ack",
        agent="test-engineer",
        task_id="8",
        rationale_articulates_this_dispatch="yes",
    )
    add(
        1800,
        "review_finding",
        severity="blocking",
        finding="A replayed refresh token is accepted by the token store.",
        reviewer="security-engineer",
        task_id="",
    )
    add(
        1860,
        "algedonic_signal",
        level="alert",
        category="SECURITY",
        summary="Refresh-token replay accepted; fix before merge.",
        agent="security-engineer",
    )
    add(
        1860,
        "blocker_raised",
        phase="TEST",
        detail="Rotation reuse detection missing.",
        agent="security-engineer",
    )
    add(
        1920,
        "remediation",
        cycle=1,
        items=["refresh reuse detection"],
        fixer="backend-coder",
        task_id="10",
    )
    add(
        1980,
        "commit",
        sha="2b3c4d5",
        message="Fix refresh reuse detection",
        phase="TEST",
    )
    add(
        2040,
        "review_finding",
        severity="suggestion",
        finding="Add a 30s clock-skew test case.",
        reviewer="test-engineer",
        task_id="",
    )
    add(
        2100,
        "commit",
        sha="3c4d5e6",
        message="Add auth integration tests",
        phase="TEST",
    )
    add(
        2160,
        "review_dispatch",
        pr_number=42,
        pr_url="https://example.invalid/pull/42",
        reviewers=[
            "architect-reviewer",
            "test-engineer-reviewer",
            "security-engineer-reviewer",
        ],
    )
    add(
        2220,
        "pr_ready",
        pr_number=42,
        pr_url="https://example.invalid/pull/42",
        commits=6,
    )
    add(2280, "compaction_attributed", verdict="lead", basis="content")
    add(
        2340,
        "checkpoint",
        phase="TEST",
        completed_phases=["PREPARE", "ARCHITECT", "CODE", "TEST"],
        active_agents=[],
        variety=_CHECKPOINT_VARIETY,
    )
    add(2400, "phase_transition", phase="TEST", status="completed")
    add(
        2460,
        "team_realigned",
        **{"from": "session-demo01", "to": "session-demo01-renamed"},
    )
    add(
        2520,
        "lifecycle_decision",
        tool_name="TaskUpdate",
        advisories_count=1,
        rules=["reasoning_reconstruction_band_unresolvable"],
        advisories=["Reasoning reconstruction band could not be resolved."],
        verdict="advisory",
    )
    add(
        2520,
        "lifecycle_decision",
        tool_name="TaskCreate",
        advisories_count=0,
        rules=[],
        advisories=[],
        verdict="allow",
    )
    add(
        2580,
        "lifecycle_decision",
        tool_name="TaskUpdate",
        advisories_count=1,
        rules=["variety_missing_on_dispatch_task"],
        advisories=["A dispatch task is missing its variety stamp."],
        verdict="advisory",
    )
    add(
        2640,
        "gate_health",
        hook="task_lifecycle_gate",
        status="failed",
        stage="runtime",
        error="AttributeError: 'str' object has no attribute 'get'",
    )
    add(
        2700,
        "journal_emit_skipped",
        skipped_type="dispatch_site",
        cause="schema",
    )
    add(
        2760,
        "session_end",
        source="wrap-up",
        pr_state="MERGED",
        pr_number=42,
    )
    return events


def _raw_events(scripted: list[dict[str, Any]]) -> list[journal.RawEvent]:
    """Wrap scripted payloads as RawEvents with file-order seq values."""
    return [
        journal.RawEvent(seq=index, data=payload)
        for index, payload in enumerate(scripted)
    ]


def build_demo_model(base_time: datetime | None = None) -> dict:
    """Build the deterministic demo model.

    Location: demo.build_demo_model.
    Summary: Scripts the journal relative to ``base_time`` (default
             2026-11-02T23:00:00Z), derives it through model.derive_model, and
             pins source.kind="demo", source.root="demo" and etag="demo-v1".
             All timestamps shift with base_time; nothing else changes.
    Used by: server (/api/demo), tests.
    """
    base = base_time if base_time is not None else _BASE_TIME
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)
    scripted = _scripted_events(base)
    events = _raw_events(scripted)
    end_ts = events[-1].data["ts"] if events else ""
    started_ts = events[0].data["ts"] if events else ""
    started_dt = model.parse_ts(started_ts)
    ended_dt = model.parse_ts(end_ts)
    duration = 0
    if started_dt is not None and ended_dt is not None:
        duration = max(0, int((ended_dt - started_dt).total_seconds()))
    team_info = roster.TeamInfo(name="session-demo01")
    meta_seed = {
        "project": "PACT-Demo",
        "project_dir": "/Users/demo/Sites/pact-demo",
        "team": "session-demo01",
        "started_at": started_ts,
        "ended_at": end_ts,
        "status": "ended",
        "duration_s": duration,
        "run_kind": "orchestrate",
        "cloud": False,
        "lead_address": _DEMO_LEAD_ADDRESS,
        "event_count": len(events),
    }
    demo_model = model.derive_model(
        events,
        source=_DEMO_SOURCE,
        etag=DEMO_ETAG,
        root_id="demo",
        slug="training-simulation",
        session_id="demo",
        team_info=team_info,
        session_context={"project_dir": "/Users/demo/Sites/pact-demo", "team_name": "session-demo01"},
        warnings=[],
        meta_seed=meta_seed,
        agent_seeds=_AGENT_SEEDS,
        task_seeds=_TASK_SEEDS,
        event_count=len(events),
        now=ended_dt,
        mtime=base.timestamp(),
    )
    return demo_model
