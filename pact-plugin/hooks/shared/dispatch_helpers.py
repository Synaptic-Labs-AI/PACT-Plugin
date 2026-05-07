#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/dispatch_helpers.py
Summary: Shared helpers for #662 dispatch_gate.py and task_lifecycle_gate.py.

Exposes:
  - is_registered_pact_specialist(subagent_type) — registry check (one
    of the dispatch_gate rules; rejects unregistered pact-* spawns)
  - has_task_assigned(team_name, name) — task-assigned check (one of
    the dispatch_gate rules; rejects spawn before TaskCreate)
  - trustworthy_actor_name(input_data) — actor resolution for the
    task_lifecycle_gate self-completion rule
  - SOLO_EXEMPT — research/exploration agents that bypass dispatch_gate
  - MARKER_SCHEMA_VERSION — bootstrap marker schema version

Module-load discipline (architect §5.6):
  Stdlib imports (json/sys/os) AND _emit_load_failure_deny defined BEFORE
  the wrapped try/except BaseException block. The wrapped block catches
  cross-package import failures (functools/re/pathlib/shared.pact_context)
  and emits a structured DENY with hookEventName=PreToolUse so any
  importer (dispatch_gate, task_lifecycle_gate) inherits the fail-closed
  contract automatically: if THIS module fails to load, the importer's
  own wrapped import block fires its own _emit_load_failure_deny via the
  re-raised BaseException.
"""

import json
import sys
import os
from typing import NoReturn


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-closed deny for module-load failure.

    Mirrors PR #660 ``merge_guard_pre._emit_load_failure_deny`` and the
    bootstrap_gate analogue at hooks/bootstrap_gate.py. Uses ONLY stdlib
    (json, sys) so it remains functional even when every wrapped import
    below fails. Audit anchor: hookEventName must be present in any deny
    output (#658 invariant).
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT dispatch_helpers {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (dispatch_helpers / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-closed wrapper around cross-package imports ─────────────────────
try:
    import functools
    from pathlib import Path
    import shared.pact_context as pact_context
    from shared.task_utils import iter_team_task_jsons
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Research/exploration agents that legitimately spawn WITHOUT name/team_name
# (per pinned-memory feedback_direct_agent_calls.md). These bypass the
# dispatch_gate entirely. The specialist registry stays simple — these are caught at
# step ① of evaluate_dispatch in dispatch_gate.py.
SOLO_EXEMPT = frozenset({"general-purpose", "Explore", "Plan"})

# Bootstrap marker schema version. Mirror constant from bootstrap_gate.py; bump
# here AND in bootstrap_gate.MARKER_SCHEMA_VERSION if marker JSON shape ever
# changes. Producer (commands/bootstrap.md) reads this same value.
MARKER_SCHEMA_VERSION = 1


# ─── specialist registry ───────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _specialist_registry() -> frozenset[str]:
    """Glob agents/pact-*.md once per hook subprocess.

    Hook subprocesses are short-lived (per-tool-call); the cache is rebuilt
    on every dispatch evaluation. Empty plugin_root or missing agents/
    directory → empty registry → every pact-* dispatch is DENIED
    (registry fail-closed). pact-orchestrator.md IS in the glob set;
    the "orchestrator is the persona, not a dispatchable specialist"
    semantic is enforced at a different layer (system-prompt --agent
    flag at session start), not at the registry.
    """
    plugin_root = pact_context.get_plugin_root()
    if not plugin_root:
        return frozenset()
    agents_dir = Path(plugin_root) / "agents"
    try:
        return frozenset(p.stem for p in agents_dir.glob("pact-*.md"))
    except OSError:
        return frozenset()


def is_registered_pact_specialist(subagent_type: str) -> bool:
    """True iff subagent_type matches a file at ``agents/pact-*.md``."""
    if not isinstance(subagent_type, str) or not subagent_type:
        return False
    return subagent_type in _specialist_registry()


# ─── task-assigned check ───────────────────────────────────────────────────

def has_task_assigned(team_name: str, name: str) -> bool:
    """True iff at least one task in ``team_name``'s task store has
    ``owner==name`` AND ``status in {"pending", "in_progress"}``.

    Delegates path construction and per-file reading to
    ``shared.task_utils.iter_team_task_jsons``, the single source of truth
    for per-team task iteration. That helper enforces the canonical
    ``~/.claude/tasks/{team_name}/*.json`` layout and applies path-traversal
    + symlink-escape defenses; centralizing the path here prevents the
    layout duplication that previously caused this gate to read the wrong
    directory. TaskList is a harness tool unavailable in subprocess
    context, so direct FS read via the helper is the only viable channel.
    """
    if not isinstance(team_name, str) or not team_name:
        return False
    if not isinstance(name, str) or not name:
        return False
    for data in iter_team_task_jsons(team_name):
        if data.get("owner") != name:
            continue
        if data.get("status") in ("pending", "in_progress"):
            return True
    return False


# ─── actor resolution (for the self-completion rule) ──────────────────────

def trustworthy_actor_name(input_data: dict) -> str | None:
    """Extract actor name from harness-trustworthy ``agent_id`` ONLY.

    Bypasses ``resolve_agent_name``'s Step 1 (agent_name) and Step 4
    (agent_type) fallbacks because they are not strong enough trust
    signals for the self-completion check (architect §5.3 / PREPARE
    §10).

    Trust contract: ``agent_id`` is harness-set and lives at the top
    level of the hook stdin JSON, not inside ``tool_input`` — a teammate
    cannot collide with it via crafted tool arguments. Format is
    ``"name@team_name"``.

    Returns the ``name`` portion, or None if ``agent_id`` is missing or
    malformed (no ``@``). Caller (task_lifecycle_gate self-completion
    rule) treats None as "actor unresolvable — DO NOT exempt from the
    self-completion advisory".

    Pure function: no FS, no I/O, no exceptions.
    """
    if not isinstance(input_data, dict):
        return None
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_id, str) or "@" not in agent_id:
        return None
    name, _, team = agent_id.partition("@")
    if not name or not team:
        return None
    return name
