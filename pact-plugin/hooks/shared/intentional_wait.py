"""
Location: pact-plugin/hooks/shared/intentional_wait.py
Summary: `intentional_wait` metadata schema — the teammate-facing contract
         for signalling a legitimate wait on an in_progress task (teachback
         approval, inter-commit hold, peer reply, etc.). Also the canonical
         self-complete-exemption predicate for team-lead-side TaskGet inspection
         and audit tooling.
Used by: teammate-authored metadata on Task records, team-lead inspection, and
         audit tooling. The exemption predicate (is_self_complete_exempt)
         is NOT a hook — it is a pure function consumed by team-lead instructions
         and future audit consumers. See skills/pact-agent-teams/SKILL.md
         and agents/pact-orchestrator.md for the contract.

Contract: pure functions; never raise. Malformed flags fail loud — e.g.
`wait_stale` returns True on any parse error so a broken flag cannot
silently be interpreted as a fresh wait. is_self_complete_exempt defaults
to False on malformed input (conservative: never silently exempt).

Public surface:
- KNOWN_REASONS / KNOWN_RESOLVERS — vocabulary hints (free-form strings
  still accepted by validate_wait).
- DEFAULT_THRESHOLD_MINUTES — staleness horizon (30 min).
- SELF_COMPLETE_EXEMPT_AGENT_TYPES — agentType tokens whose owners may
  self-complete (memory-save, etc.). Companion predicate:
  is_self_complete_exempt. Resolution is via team-config lookup
  (member.agentType), NOT owner name — secretaries spawned under any
  name (e.g. session-secretary) reach the carve-out as long as their
  agentType is in this set.
- canonical_since() — ISO-8601 UTC timestamp helper for the `since` field.
- validate_wait(wait_metadata) — True iff the flag is well-formed.
- wait_stale(wait_metadata) — True iff the flag has aged past threshold.
- is_self_complete_exempt(task, team_name="", teams_dir=None) — True iff
  the task is exempt from the team-lead-only-completion rule (by
  team-config-resolved agentType, or signal-task pattern).
"""

from datetime import datetime, timezone
from typing import Any

from shared.pact_context import _iter_members


# Reason vocabulary — frozenset, not Enum, so teammates can include custom
# reasons when instrumentation hasn't caught up. validate_wait only checks
# non-empty string; reasons are for humans and audit logs.
KNOWN_REASONS = frozenset({
    "awaiting_teachback_approved",
    "awaiting_lead_commit",
    "awaiting_amendment_review",
    "awaiting_post_handoff_decision",
    "awaiting_peer_response",
    "awaiting_user_decision",
    "awaiting_blocker_resolution",
    "awaiting_lead_completion",
})

KNOWN_RESOLVERS = frozenset({"lead", "peer", "user", "external"})

DEFAULT_THRESHOLD_MINUTES = 30


# AgentType tokens whose owners may self-complete because the team-lead
# has no acceptance criteria to judge. Exemption is narrow by design: each
# entry must have a documented rationale.
#
# `pact-secretary` (memory-save tasks): purely internal bookkeeping.
#   The secretary writes a HANDOFF describing what was saved; the team-lead
#   has no acceptance criteria — judging memory-save quality is the
#   secretary's domain. Lead-as-gate would add 12-18 transitions per
#   session with zero quality signal.
#
# Resolution is by team-config agentType lookup (see
# `_is_exempt_agent_type` below + `_iter_members` upstream), NOT owner
# name. A secretary spawned under any name (`session-secretary`,
# `team-secretary`, etc.) still reaches the carve-out as long as the
# team-config records its agentType as `pact-secretary`.
#
# Auditor signal-tasks are NOT in this set — they self-complete via
# `metadata.completion_type == "signal"` + `metadata.type in {"blocker",
# "algedonic"}` (the inline-literal pattern at task_utils.py:184 /
# session_resume.py:525). Two distinct exemption surfaces:
# - SELF_COMPLETE_EXEMPT_AGENT_TYPES: by team-config agentType lookup.
# - signal-task pattern: by task metadata, applies to any agent.
SELF_COMPLETE_EXEMPT_AGENT_TYPES: frozenset = frozenset({
    "pact-secretary",
})


def _is_exempt_agent_type(
    owner: str,
    team_name: str,
    teams_dir: str | None = None,
) -> bool:
    """Return True iff the team-config member matching `owner` has an
    agentType in SELF_COMPLETE_EXEMPT_AGENT_TYPES.

    Fail-closed: returns False on every error path (empty owner, empty
    team_name, missing/malformed config, member not found, missing or
    empty agentType field, non-string inputs). Conservative posture —
    never silently exempt. This matches the existing predicate semantics
    on malformed input: a non-exempt teammate that gets falsely exempted
    via a corrupt config could self-complete tasks the lead needed to
    inspect, exactly the failure mode lead-only-completion authority
    defends against.

    Pure-after-I/O. Reads the team config via `_iter_members`, which is
    itself fail-open `[]` on every error path (missing file, OSError,
    JSONDecodeError, non-dict members, etc.). The fail-open of the
    upstream becomes fail-closed for this predicate because we require
    a positive match.

    NOT a hook predicate — pure helper for shared.intentional_wait.
    is_self_complete_exempt and shared.wake_lifecycle._lifecycle_relevant.
    Mirrors the auditor_reminder._team_has_auditor precedent.

    Args:
        owner: Task owner name (matched against member.name).
        team_name: Team name for config path. Empty string returns False.
        teams_dir: Override teams directory (for testing). When omitted,
                   _iter_members uses ``~/.claude/teams/``.
    """
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(team_name, str) or not team_name:
        return False
    for member in _iter_members(team_name, teams_dir):
        if member.get("name") == owner:
            agent_type = member.get("agentType")
            return (
                isinstance(agent_type, str)
                and agent_type in SELF_COMPLETE_EXEMPT_AGENT_TYPES
            )
    return False


def canonical_since() -> str:
    """
    Return the canonical ISO-8601 UTC `since` timestamp used by auto-set
    sites and the manual-set convention.

    Round-trip guarantee: the return value parses cleanly via the same
    `fromisoformat(s.replace("Z", "+00:00"))` path validate_wait uses.
    timespec="seconds" drops microseconds for stable equality across
    cross-system timestamps and avoids platform-specific formatting drift.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_wait(wait_metadata: Any) -> bool:
    """
    Return True iff wait_metadata is a well-formed intentional_wait dict.

    Required keys: reason (non-empty str), expected_resolver (non-empty str —
    KNOWN_RESOLVERS preferred but any non-empty string accepted), since
    (tz-aware ISO-8601 timestamp parseable by datetime.fromisoformat, with
    a trailing 'Z' accepted as UTC).

    Unknown keys are preserved (forward-compat).
    """
    if not isinstance(wait_metadata, dict):
        return False

    reason = wait_metadata.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False

    resolver = wait_metadata.get("expected_resolver")
    if not isinstance(resolver, str) or not resolver.strip():
        return False

    since = wait_metadata.get("since")
    if not isinstance(since, str):
        return False

    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False

    # Reject tz-naive timestamps. A naive timestamp from a non-UTC teammate
    # could be hours off real UTC, producing silently wrong age computations.
    # Instrumentation always emits tz-aware via canonical_since(), so a naive
    # value is always a teammate bug — surface it fast rather than paper over.
    if parsed.tzinfo is None:
        return False

    return True


def wait_stale(
    wait_metadata: Any,
    threshold_minutes: int = DEFAULT_THRESHOLD_MINUTES,
    _now: datetime | None = None,
) -> bool:
    """
    Return True iff the intentional_wait flag is stale (wait should be
    treated as expired; fresh SET needed to keep the semantic).

    Stale when:
    - wait_metadata fails validation (malformed flag → fail loud), OR
    - elapsed time since `since` is >= threshold_minutes.

    Future-dated `since` (clock drift or tampering) yields a negative age
    and is treated as NOT stale — conservative: don't treat a teammate
    whose clock skewed forward as stale. The threshold still re-triggers
    once wall-clock catches up.

    _now is for deterministic unit tests; production callers omit it.
    """
    if not validate_wait(wait_metadata):
        return True

    since = datetime.fromisoformat(wait_metadata["since"].replace("Z", "+00:00"))
    now = _now or datetime.now(timezone.utc)
    age_minutes = (now - since).total_seconds() / 60
    return age_minutes >= threshold_minutes


def is_self_complete_exempt(
    task: Any,
    team_name: str = "",
    teams_dir: str | None = None,
) -> bool:
    """
    Return True iff this task is exempt from the team-lead-only-completion rule.

    Two exemption surfaces (OR-combined):
    1. By team-config agentType: task.owner names a team member whose
       agentType is in SELF_COMPLETE_EXEMPT_AGENT_TYPES. Resolution
       happens via `_is_exempt_agent_type(owner, team_name, teams_dir)`,
       which reads the team config (harness-managed SSOT). Secretaries
       spawned under any name (`session-secretary`, `team-secretary`,
       etc.) reach this surface as long as their agentType matches.
       Requires `team_name`; with `team_name=""` (the default) this
       surface short-circuits to False (fail-closed).
    2. By signal-task metadata: task.metadata.completion_type == "signal" AND
       task.metadata.type in {"blocker", "algedonic"}. Mirrors the inline
       literal at agent_handoff_emitter.py / task_utils.py:184 /
       session_resume.py:525. Independent of `team_name`.

    Pure function; never raises on plain dicts (PACT task representation
    via json.loads); defaults to False on missing or malformed fields
    (conservative — never silently exempt).

    NOT a hook predicate. There is no hook reading this — the function is
    the canonical predicate for team-lead-side TaskGet inspection, audit tooling,
    and future consumers. Hooks must use the inline-literal mirror to avoid
    reintroducing livelock-capable hook surface.

    TRUST BOUNDARY: `owner` is teammate-writable via TaskUpdate(owner=...),
    BUT exemption now requires the team config to record that owner with
    a privileged agentType — the team config is harness-managed and never
    teammate-writable. The carve-out is bounded by both
    SELF_COMPLETE_EXEMPT_AGENT_TYPES being a curated short list
    (currently `pact-secretary` only) AND the team-config-as-SSOT
    requirement. A non-exempt teammate cannot self-promote by spoofing
    `owner`: the team-config lookup must independently confirm the
    privileged agentType. No in-plugin consumer reads this predicate at
    runtime — instructions and audit tooling are the defense. Future
    extensions adding new agentTypes to SELF_COMPLETE_EXEMPT_AGENT_TYPES
    must re-evaluate this boundary.

    Args:
        task: Task dict (from json.loads of a task record).
        team_name: Team name used for the agentType lookup. Empty string
                   (default) short-circuits surface 1 to False; surface 2
                   still applies.
        teams_dir: Override teams directory (for testing). Threaded to
                   `_iter_members`.
    """
    if not isinstance(task, dict):
        return False

    metadata = task.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False

    # Surface 1: owner's team-config agentType is in the exempt set.
    owner = task.get("owner")
    if isinstance(owner, str) and _is_exempt_agent_type(owner, team_name, teams_dir):
        return True

    # Surface 2: signal-task pattern (inline-literal mirror).
    if metadata.get("completion_type") == "signal":
        signal_type = metadata.get("type")
        if signal_type in ("blocker", "algedonic"):
            return True

    return False
