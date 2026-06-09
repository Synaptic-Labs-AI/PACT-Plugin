"""
Hook-Infra Classifier (live-probe gate SSOT)

Location: pact-plugin/hooks/shared/hook_infra_classifier.py

Summary: Single source of truth for classifying a changed-file set as a
"hook-infra" change for the live-probe gate. Exposes the PRIMARY path signal
(touches the hooks/ tree), the SECONDARY seam signal (touches a seam-dependent
hook or a helper module it TRANSITIVELY imports), the L2/L3 hook tiers, and
classify_diff() returning a Classification. The classifier is pure and
side-effect free (no filesystem I/O, no subprocess) so it can be imported on
the PreToolUse hook path without cost.

Used by:
- live_probe_gate.py — the locus-b PreToolUse advisory; calls classify_diff to
  decide whether to WARN.
- tests/test_live_probe_gate_structure.py — the CI meta-tests; import the seam
  sets + the per-hook helper closure to (a) assert the seam<->test-presence
  mapping and (b) re-derive the closure from the live import graph and pin it
  against this module so the precomputed literal cannot drift.
- the reviewer-facing live-probe template — QUOTES this module as the source of
  truth, never a restated list.

Why the transitive closure is PRECOMPUTED (a static literal) rather than walked
at runtime: the classifier must stay import-light and side-effect free for the
hook path. The companion meta-test re-derives the closure from the live import
graph and asserts equality, so drift between this literal and the real import
graph is caught at test time, not silently in production.

The closure is FULL-TRANSITIVE, derived via AST over the live import graph
following ABSOLUTE and RELATIVE (`from .X import` / `from . import X`) and
function-level imports, covering EVERY hooks/ module (top-level helper modules
AND hooks/shared/ helpers) — not direct-only, not shared-only, not
absolute-only: a seam hook may reach a helper via a multi-hop and/or relative
chain. Three real multi-hop cases this catches:
  - task_lifecycle_gate -> teachback_schema -> variety_scorer   (shared 2-hop)
  - session_init -> staleness -> pin_caps                       (top-level 2-hop)
  - <every pact_context importer> -> pact_context -(relative)-> session_registry
    (session_registry is the identity-resolution seam — reached by 11 hooks via
    pact_context's `from .session_registry import`; a regex deriver that skips
    relative edges under-attributes it to just the 2 direct importers)
A direct-only, shared-only, OR absolute-only map would MISS these — recreating a
miniature inert-ship false-negative at the classifier layer. The asymmetry
favors closure: a false positive costs one L2 test; a false negative is the
inert-ship class. The companion meta-test's oracle MUST also be AST
relative-following, or it reproduces the blind spot.
"""

from __future__ import annotations

from dataclasses import dataclass


# ─── Seam membership SSOT ───────────────────────────────────────────────────

# Every hook whose value depends on an integration seam (task-dir resolution,
# team config, real journal/inbox, or a shared resolver). Each requires an L2
# non-mocked integration test.
SEAM_DEPENDENT_HOOKS: frozenset[str] = frozenset({
    "missed_wake_scan", "teammate_idle", "agent_handoff_emitter",
    "session_init", "session_end", "dispatch_gate", "task_lifecycle_gate",
    "bootstrap_gate", "bootstrap_marker_writer", "file_tracker",
    "peer_inject", "validate_handoff",
})  # 12

# Hooks confirmed to FAIL SILENTLY on a broken seam (a consequential effect that
# should fire simply does not, with no error) -> they additionally require an L3
# live-probe (a real-process firing observation under both teammateModes),
# because a non-mocked L2 test alone cannot certify a timing/mode-sensitive
# emit fires in a running process.
#
# 3 originally confirmed: missed_wake_scan + teammate_idle (the inert
# missed-wake alarms) + agent_handoff_emitter (the b1 TaskCompleted emit).
# task_lifecycle_gate PROMOTED from candidate by the CODE-phase fails-silent
# check: it carries the b2 lead-side agent_handoff emit (sibling of the b1
# emit already in this set) plus the lifecycle_decision journal event; on a
# broken team_name/task-read seam, read_task_json returns None -> the gate
# returns False -> that emit silently no-ops. The b2 emit is teammateMode/
# timing-sensitive, the residual gap an L2 test cannot close.
L3_LIVE_PROBE_HOOKS: frozenset[str] = frozenset({
    "missed_wake_scan", "teammate_idle", "agent_handoff_emitter",
    "task_lifecycle_gate",
})  # 4

# Seam-dependent hooks ASSESSED in the CODE-phase fails-silent check and HELD at
# L2-only (no consequential silent no-op meeting the L3 bar). Retained as a
# record + a watch-list (promote on a future incident showing a silent loss):
#   - file_tracker:    a broken get_team_name seam degrades file-edit drift
#                      ATTRIBUTION DATA (recoverable), not a coordination alarm.
#   - peer_inject:     a silent peer-context injection failure is consequential
#                      but more VISIBLE (the spawned subagent misbehaves), so it
#                      does not meet the silent-inert bar; watch-candidate.
#   - validate_handoff: reads ONLY stdin (no disk/task/journal seam) -> cannot
#                      go inert the inert-ship way; its L2 test is a stdin-contract test.
L3_CANDIDATE_HOOKS: frozenset[str] = frozenset({
    "file_tracker", "peer_inject", "validate_handoff",
})  # 3 — assessed, held at L2-only

# dispatch_gate + bootstrap_gate are fail-CLOSED (their decision-domain
# uncertainty path is exit(2) DENY, and they make no get_task_list call) -> they
# fail LOUD, never silent-inert -> L2-only, never L3. (CODE-confirmed: their
# exit(0) paths are input-side fail-open + legitimate ALLOW, not seam-error.)
#
# live_probe_gate (the locus-b advisory itself) is DELIBERATELY excluded from
# SEAM_DEPENDENT_HOOKS even though it meets the seam predicate (its WARN depends
# on a runtime-resolved git root / marker / RUNBOOK_RUN_DATES path, and it
# silent-allows on any resolution failure). The exclusion is intentional, not an
# oversight: its silent-failure is BENIGN -- it merely stops warning and the gate
# degrades to pin-only enforcement (never a false "checked & clear"), so it needs
# no inert-alarm L2/L3 treatment (parallel to file_tracker's benign-degrade
# L2-only reasoning above). It is covered this cycle by
# test_live_probe_gate_structure.py + the dogfood probe; a maintainer editing it
# should preserve this rationale.


# ─── Transitive helper import closure (authoritative SSOT data) ─────────────

# Per-seam-hook FULL-TRANSITIVE helper import closure: for each seam hook, the
# set of helper modules (top-level hooks/ helpers AND hooks/shared/ helpers,
# EXCLUDING the seam hooks themselves) it reaches via the import graph. Derived
# via AST following ABSOLUTE + RELATIVE (`from .X`) + function-level imports
# (NOT regex — regex silently skips relative edges, e.g. pact_context's
# `from .session_registry import resolve`, which under-attributes session_registry
# to its 2 direct importers instead of all 11 pact_context importers). The
# meta-test re-derives the same way (AST, relative-following) and asserts
# equality so this literal cannot drift. An edit to any helper in a hook's
# closure can change that hook's behavior -> the edit is SECONDARY.
#
# `paths` (shared/paths.py) is the CLAUDE_CONFIG_DIR / config-dir SSOT resolver
# added by the config-dir refactor; it is now reached by 11 of the 12 seam hooks
# (all but validate_handoff) because the path-consuming shared helpers
# (constants, pact_context, session_state, task_utils, ... via `from .paths
# import get_claude_config_dir`) sit in nearly every closure. It is a genuine
# path-seam resolver -> a legitimate SECONDARY helper (the C6-A oracle caught its
# arrival as designed; this literal was regenerated from the live derivation).
_SEAM_HOOK_HELPER_CLOSURE: dict[str, frozenset[str]] = {
    "missed_wake_scan": frozenset({
        "constants", "intentional_wait", "pact_context", "paths",
        "session_journal", "session_registry", "session_state", "task_utils",
    }),
    "teammate_idle": frozenset({
        "constants", "error_output", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "task_utils",
    }),
    "agent_handoff_emitter": frozenset({
        "agent_handoff_marker", "constants", "pact_context", "paths",
        "session_journal", "session_registry", "session_state", "task_utils",
    }),
    "session_init": frozenset({
        "claude_md_manager", "constants", "dispatch_helpers", "failure_log",
        "merge_guard_common", "pact_context", "paths", "peer_context",
        "pin_caps", "pin_staleness_gate", "plugin_manifest", "session_journal",
        "session_registry", "session_resume", "session_state", "staleness",
        "symlinks", "task_utils", "teammate_mode",
    }),  # top-level helpers (pin_caps, staleness, pin_staleness_gate) reached
         # here: session_init -> staleness -> pin_caps; session_init ->
         # pin_staleness_gate -> pin_caps.
    "session_end": frozenset({
        "constants", "error_output", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "task_utils",
    }),
    "dispatch_gate": frozenset({
        "constants", "dispatch_helpers", "pact_context", "paths",
        "session_journal", "session_registry", "session_state", "task_utils",
    }),
    "task_lifecycle_gate": frozenset({
        "agent_handoff_marker", "constants", "dispatch_helpers",
        "intentional_wait", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "task_utils", "teachback_schema",
        "tool_response", "variety_scorer",
    }),
    "bootstrap_gate": frozenset({
        "constants", "marker_schema", "pact_context", "paths",
        "session_journal", "session_registry", "session_state",
    }),
    "bootstrap_marker_writer": frozenset({
        "constants", "marker_schema", "pact_context", "paths",
        "session_journal", "session_registry", "session_state",
    }),
    "file_tracker": frozenset({
        "constants", "pact_context", "paths", "session_journal",
        "session_registry", "session_state",
    }),
    "peer_inject": frozenset({
        "constants", "pact_context", "paths", "peer_context", "plugin_manifest",
        "session_journal", "session_registry", "session_state",
    }),
    "validate_handoff": frozenset({"error_output"}),
}

# Every helper module (top-level OR shared) transitively reachable from at least
# one seam hook. An edit to one of these is SECONDARY (could change a seam hook's
# behavior). A hooks/ file NOT in this set and not itself a seam hook (e.g. the
# pure shared helpers gh_helpers / git_helpers / variety_divergence, or a
# non-seam registered hook, or hooks.json) trips PRIMARY only -> the auditable
# waiver path.
SEAM_READING_HELPERS: frozenset[str] = frozenset().union(
    *_SEAM_HOOK_HELPER_CLOSURE.values()
)  # 26 (23 shared helpers — incl. paths, the config-dir SSOT — + 3 top-level)


# ─── Path predicates ────────────────────────────────────────────────────────

def _norm(path: str) -> str:
    """Normalize a repo-relative path to forward slashes."""
    return path.replace("\\", "/")


def _module_name(path: str) -> str | None:
    """Map a hooks/ .py path to its module stem; None for non-.py (e.g. hooks.json).

    'pact-plugin/hooks/missed_wake_scan.py'   -> 'missed_wake_scan'
    'pact-plugin/hooks/shared/task_utils.py'  -> 'task_utils'
    'pact-plugin/hooks/pin_caps.py'           -> 'pin_caps'
    'pact-plugin/hooks/hooks.json'            -> None
    """
    p = _norm(path)
    if not p.endswith(".py"):
        return None
    return p.rsplit("/", 1)[-1][:-len(".py")]


def is_hook_infra_path(path: str) -> bool:
    """PRIMARY signal. True iff `path` is under the hooks/ tree (including
    hooks/shared/) and is a .py file, or is a hooks.json. `path` is
    repo-relative (e.g. 'pact-plugin/hooks/...'). The leading-slash guard makes
    a path that STARTS with 'hooks/' match too."""
    p = _norm(path)
    in_hooks_tree = "/hooks/" in f"/{p}"
    return in_hooks_tree and (p.endswith(".py") or p.endswith("/hooks.json"))


def _is_under_hooks_tree(path: str) -> bool:
    return "/hooks/" in f"/{_norm(path)}"


def _is_shared_path(path: str) -> bool:
    return "/hooks/shared/" in f"/{_norm(path)}"


def _implicated_seam_hooks(path: str) -> frozenset[str]:
    """The seam hooks implicated by a single changed path: the hook itself if it
    is a seam-dependent hook file; the seam hooks that TRANSITIVELY import it if
    it is a helper module (top-level OR shared). Empty for non-seam paths
    (hooks.json, a new unimported hook, a pure helper, a non-hooks path)."""
    p = _norm(path)
    mod = _module_name(p)
    if mod is None or not _is_under_hooks_tree(p):
        return frozenset()
    # A seam hook file is a TOP-LEVEL hooks/ module (not under shared/).
    if mod in SEAM_DEPENDENT_HOOKS and not _is_shared_path(p):
        return frozenset({mod})
    # A helper (top-level or shared) reachable from one or more seam hooks.
    if mod in SEAM_READING_HELPERS:
        return frozenset(
            hook for hook, closure in _SEAM_HOOK_HELPER_CLOSURE.items()
            if mod in closure
        )
    return frozenset()


def reads_seam(path: str) -> bool:
    """SECONDARY signal. True iff `path` is a seam-dependent hook OR a helper
    module (top-level or shared) transitively imported by one."""
    return bool(_implicated_seam_hooks(path))


# ─── Classification ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Classification:
    """The hook-infra classification of a changed-file set.

    primary:         the diff touches the hooks/ tree (incl. hooks/shared/) or
                     hooks.json -> raises the cheap WARN signal.
    secondary:       the diff touches a seam-dependent hook (or a helper it
                     transitively imports) -> gates the expensive L2/L3
                     requirements.
    waiver_required: primary and not secondary -> the operator logs an auditable
                     WAIVER row (never a silent pass); the non-vacuity-on-the-
                     quiet-side evidence that any future WARN->BLOCK promotion
                     depends on.
    seam_hooks:      the seam hooks implicated by the diff (directly, or
                     transitively via a touched helper) -> scopes which hooks
                     need L2 tests / L3 live-probes.
    """
    primary: bool
    secondary: bool
    waiver_required: bool
    seam_hooks: frozenset[str]


def classify_diff(changed_paths: list[str]) -> Classification:
    """Classify a list of repo-relative changed paths for the live-probe gate."""
    primary = any(is_hook_infra_path(p) for p in changed_paths)
    seam_hooks: set[str] = set()
    for path in changed_paths:
        seam_hooks |= _implicated_seam_hooks(path)
    secondary = bool(seam_hooks)
    return Classification(
        primary=primary,
        secondary=secondary,
        waiver_required=primary and not secondary,
        seam_hooks=frozenset(seam_hooks),
    )
