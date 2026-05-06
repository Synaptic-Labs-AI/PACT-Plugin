#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/dispatch_helpers.py
Summary: Shared helpers for #662 dispatch_gate.py and task_lifecycle_gate.py.

Exposes:
  - is_registered_pact_specialist(subagent_type) — F4 registry check
  - has_task_assigned(team_name, name) — F6 task-assigned check
  - trustworthy_actor_name(input_data) — F12 actor resolution
  - SOLO_EXEMPT — research/exploration agents that bypass dispatch_gate
  - F24_MARKER_VERSION — bootstrap marker schema version

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


# ─── F21: fail-closed wrapper around cross-package imports ─────────────────
try:
    import functools
    from pathlib import Path
    import shared.pact_context as pact_context
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Research/exploration agents that legitimately spawn WITHOUT name/team_name
# (per pinned-memory feedback_direct_agent_calls.md). These bypass the
# dispatch_gate entirely. F4 registry stays simple — these are caught at
# step ① of evaluate_dispatch in dispatch_gate.py.
SOLO_EXEMPT = frozenset({"general-purpose", "Explore", "Plan"})

# F24 marker schema version. Mirror constant from bootstrap_gate.py; bump
# here AND in bootstrap_gate.F24_MARKER_VERSION if marker JSON shape ever
# changes. Producer (commands/bootstrap.md) reads this same value.
F24_MARKER_VERSION = 1


# ─── F4 registry ───────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _specialist_registry() -> frozenset[str]:
    """Glob agents/pact-*.md once per hook subprocess.

    Hook subprocesses are short-lived (per-tool-call); the cache is rebuilt
    on every dispatch evaluation. Empty plugin_root or missing agents/
    directory → empty registry → every pact-* dispatch is DENIED (F4
    fail-closed). pact-orchestrator.md IS in the glob set; the
    "orchestrator is the persona, not a dispatchable specialist" semantic
    is enforced at a different layer (system-prompt --agent flag at
    session start), not at registry/F4.
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


# ─── F6 task-assigned check ────────────────────────────────────────────────

def has_task_assigned(team_name: str, name: str) -> bool:
    """True iff at least one task in ``team_name``'s task store has
    ``owner==name`` AND ``status in {"pending", "in_progress"}``.

    Reads ``~/.claude/tasks/{team_name}/*.json`` directly — the canonical
    task store per ``shared/task_utils.py`` (read_task_json,
    iter_team_tasks). TaskList is a harness tool unavailable in subprocess
    context — direct FS read is the only viable channel. Tolerant parsing:
    malformed JSON or missing fields → skip that file (False contribution).
    Path traversal is defended upstream (F3 regex on name; F5
    session-equality on team_name) — by the time this runs, both have
    passed validation.
    """
    if not isinstance(team_name, str) or not team_name:
        return False
    if not isinstance(name, str) or not name:
        return False
    tasks_dir = Path.home() / ".claude" / "tasks" / team_name
    try:
        task_files = list(tasks_dir.glob("*.json"))
    except OSError:
        return False
    for path in task_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("owner") != name:
            continue
        status = data.get("status")
        if status in ("pending", "in_progress"):
            return True
    return False


# ─── F12 actor resolution ──────────────────────────────────────────────────

def trustworthy_actor_name(input_data: dict) -> str | None:
    """Extract actor name from harness-trustworthy ``agent_id`` ONLY.

    Bypasses ``resolve_agent_name``'s Step 1 (agent_name) and Step 4
    (agent_type) fallbacks because they are not strong enough trust
    signals for the F12 self-completion check (architect §5.3 / PREPARE
    §10).

    Trust contract: ``agent_id`` is harness-set and lives at the top
    level of the hook stdin JSON, not inside ``tool_input`` — a teammate
    cannot collide with it via crafted tool arguments. Format is
    ``"name@team_name"``.

    Returns the ``name`` portion, or None if ``agent_id`` is missing or
    malformed (no ``@``). Caller (task_lifecycle_gate F12) treats None as
    "actor unresolvable — DO NOT exempt from F12".

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
