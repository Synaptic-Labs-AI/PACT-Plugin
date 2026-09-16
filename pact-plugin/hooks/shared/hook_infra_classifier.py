"""
Hook-Infra Seam Classifier (seam-test requirement SSOT)

Location: pact-plugin/hooks/shared/hook_infra_classifier.py

Summary: Single source of truth for the seam-dependent-hook enumeration that the
non-mocked seam-integration-test requirement references. Exposes the PRIMARY
path signal (touches the hooks/ tree), the SECONDARY seam signal (touches a
seam-dependent hook or a helper module it TRANSITIVELY imports), the L2/L3 hook
tiers, and classify_diff() returning a Classification. The classifier is pure
and side-effect free (no filesystem I/O, no subprocess) — a plugin-internal
data/CI module, NOT a registered runtime hook, so it imposes no consumer cost.

Used by:
- tests/test_hook_infra_classifier.py — the CI meta-tests; import the seam
  sets + the per-hook helper closure to (a) assert the seam<->test-presence
  mapping and (b) re-derive the closure from the live import graph and pin it
  against this module so the precomputed literal cannot drift, and exercise
  classify_diff()'s PRIMARY/SECONDARY signals.
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
    (session_registry is the identity-resolution seam — reached by every
    pact_context importer via pact_context's `from .session_registry import`;
    a regex deriver that skips relative edges under-attributes it to its direct
    importers only)
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
    # merge_guard_pre/post: the on-disk authorization token is the canonical
    # post(mint)->pre(read) integration seam this classifier exists to catch; a
    # guard-specific seam regression must not ship inert. They DENY via exit(2)
    # (fail-LOUD) -> L2-only, never L3 (no mode-divergent signal -> no both-modes
    # matrix). KD-10.
    "merge_guard_pre", "merge_guard_post",
    # track_files JOINS with Layer 1 of the background-work registry: it now
    # reads the team task store and writes
    # ~/.claude/teams/<team>/background_work.json — task-dir resolution AND
    # team config, so it meets the criterion above outright.
    "track_files",
    # wait_filler_gate: the background-launch advisory reads team config and the
    # session registry to tell a teammate from an Agent-tool subagent. That path
    # never denies and fails open, so it is L2-only.
    "wait_filler_gate",
    # stop_background_gate: the Stop turn-end gate resolves the team from the
    # session context or the session registry, reads the task store and the
    # background-work registry, and writes a told-once file and a journal
    # trace. Its block fails silent on a broken seam: see L3_LIVE_PROBE_HOOKS.
    "stop_background_gate",
    # postcompact_archive: it stages the session's compaction summary and
    # settles earlier ones, reading the platform's transcripts to decide whose
    # compaction each was and journaling the verdict. Its L2 test runs the real
    # hooks over a temporary projects tree.
    "postcompact_archive",
})

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
#
# stop_background_gate and validate_handoff joined with the turn-end gate. Each
# prints a block refusing a turn end over unacknowledged background work, and
# decides it by resolving the team, the task store and the background-work
# registry (validate_handoff also reads the platform's subagent metadata). On a
# broken seam the role resolves to nothing and the stop is allowed with no
# error, so the block silently never fires. validate_handoff was promoted from
# L3_CANDIDATE_HOOKS for that reason.
#
# session_init, postcompact_archive and bootstrap_gate settle staged compaction
# summaries by reading the platform's transcripts. On a broken seam, such as a
# changed transcript layout, every staged summary expires unmatched: it is
# journaled as {unknown, expired}, but the lead's own summary is then parked as
# unattributed and never becomes compact-summary.txt.
L3_LIVE_PROBE_HOOKS: frozenset[str] = frozenset({
    "missed_wake_scan", "teammate_idle", "agent_handoff_emitter",
    "task_lifecycle_gate", "stop_background_gate", "validate_handoff",
    "session_init", "postcompact_archive", "bootstrap_gate",
})

# Seam-dependent hooks ASSESSED in the CODE-phase fails-silent check and HELD at
# L2-only (no consequential silent no-op meeting the L3 bar). Retained as a
# record + a watch-list (promote on a future incident showing a silent loss):
#   - file_tracker:    a broken get_team_name seam degrades file-edit drift
#                      ATTRIBUTION DATA (recoverable), not a coordination alarm.
#   - peer_inject:     a silent peer-context injection failure is consequential
#                      but more VISIBLE (the spawned subagent misbehaves), so it
#                      does not meet the silent-inert bar; watch-candidate.
# (validate_handoff was held here until its turn-end background block made it
#  fail silently on a broken seam; it is now in L3_LIVE_PROBE_HOOKS.)
L3_CANDIDATE_HOOKS: frozenset[str] = frozenset({
    "file_tracker", "peer_inject",
})  # assessed, held at L2-only

# dispatch_gate is fail-CLOSED (its decision-domain uncertainty path is exit(2)
# DENY, and it makes no get_task_list call) -> it fails LOUD, never silent-inert
# -> L2-only, never L3. (CODE-confirmed: its exit(0) paths are input-side
# fail-open + legitimate ALLOW, not seam-error.) bootstrap_gate's DECISION fails
# loud the same way, but its compaction-summary settle seat fails SILENT: on a
# broken seam the lead's summary is parked and nothing errors. That seat is why
# bootstrap_gate is in L3_LIVE_PROBE_HOOKS.


# ─── Transitive helper import closure (authoritative SSOT data) ─────────────

# Per-seam-hook FULL-TRANSITIVE helper import closure: for each seam hook, the
# set of helper modules (top-level hooks/ helpers AND hooks/shared/ helpers,
# EXCLUDING the seam hooks themselves) it reaches via the import graph. Derived
# via AST following ABSOLUTE + RELATIVE (`from .X`) + function-level imports
# (NOT regex — regex silently skips relative edges, e.g. pact_context's
# `from .session_registry import resolve`, which under-attributes session_registry
# to its direct importers among the seam hooks instead of every pact_context
# importer). The
# meta-test re-derives the same way (AST, relative-following) and asserts
# equality so this literal cannot drift. An edit to any helper in a hook's
# closure can change that hook's behavior -> the edit is SECONDARY.
#
# `paths` (shared/paths.py) is the CLAUDE_CONFIG_DIR / config-dir SSOT resolver
# added by the config-dir refactor; it is now reached by every hook in
# SEAM_DEPENDENT_HOOKS (validate_handoff was the last holdout until its
# degrade-path journal telemetry pulled in pact_context/session_journal)
# because the path-consuming
# shared helpers (constants, pact_context, session_state, task_utils, ... via
# `from .paths import get_claude_config_dir`) sit in every closure. It is a
# genuine path-seam resolver -> a legitimate SECONDARY helper (the C6-A oracle
# caught its arrival as designed; this literal was regenerated from the live
# derivation).
_SEAM_HOOK_HELPER_CLOSURE: dict[str, frozenset[str]] = {
    "missed_wake_scan": frozenset({
        "background_launch", "background_work",
        "constants", "intentional_wait", "pact_context",
        "paths",
        "session_journal", "session_registry", "session_state", "state_file",
        "task_utils",
    }),  # state_file reached via background_work's state-file reads and writes.
    "teammate_idle": frozenset({
        "background_launch", "background_work",
        "constants", "error_output", "intentional_wait",
        "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "state_file", "task_utils",
    }),
    "track_files": frozenset({
        "background_launch", "background_work",
        "claude_md_manager", "constants", "error_output",
        "failure_cause", "git_helpers", "intentional_wait", "pact_context", "paths",
        "pin_caps", "project_scope", "session_journal", "session_registry", "session_state",
        "staleness", "state_file", "task_utils",
    }),  # regenerated from the live derivation, not hand-listed: the Layer 1
         # fold adds background_work + intentional_wait, and the rest were
         # already reached through the pin-staleness clear this hook carries.
    "agent_handoff_emitter": frozenset({
        "agent_handoff_marker", "canonical_json", "constants",
        "pact_context", "paths",
        "session_journal", "session_registry", "session_state",
        "task_metadata_snapshot", "task_utils",
    }),  # task_metadata_snapshot reached via the teammate-frame snapshot
         # seam (emit_task_metadata_snapshot); its own transitive edges
         # (agent_handoff_marker, session_journal) were already here.
    "session_init": frozenset({
        "backlog_store",
        "claude_md_manager", "compaction_owner", "constants", "dispatch_helpers", "failure_cause",
        "failure_log", "git_helpers", "handoff_schema", "marker_schema",
        "merge_guard_common", "pact_config", "pact_context", "paths",
        "peer_context", "pin_caps", "plugin_manifest", "project_scope",
        "session_journal", "session_registry", "session_resume",
        "session_state", "staleness", "state_file", "symlinks", "task_utils", "teammate_mode",
    }),  # backlog_store reached via `from shared import backlog_store`, an edge
         # the oracle resolves since it reads modules named in the import alias.
         # pact_config reached via the SessionStart runtime-config injection
         # (session_init -> shared.pact_config.llm_options); stdlib-only, so it
         # adds no further transitive shared edges.
         # top-level helpers (pin_caps, staleness) reached here:
         # session_init -> staleness -> pin_caps.
         # `pin_staleness_gate` LEFT this closure when the pin-staleness marker
         # name moved to `shared.constants`. session_init takes that name from
         # there, so a SessionStart no longer loads a fail-closed PreToolUse
         # gate to read one string.
         # marker_schema reached via the function-level bootstrap_gate import
         # that checks the bootstrap marker on a compaction. bootstrap_gate is
         # itself a seam hook, so it is not listed here; only its helper is.
         # That import runs on the compact branch only, with its output
         # captured, and a failure to load reads as no marker.
    "session_end": frozenset({
        "constants", "error_output", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "task_utils",
    }),
    "postcompact_archive": frozenset({
        "compaction_owner", "constants", "error_output", "pact_context", "paths",
        "session_journal", "session_registry", "session_state",
    }),  # compaction_owner reached via staging and settling the summary;
         # session_journal via compaction_owner's compaction_attributed event.
    "dispatch_gate": frozenset({
        "background_launch", "background_work", "constants",
        "dispatch_helpers", "intentional_wait", "pact_config", "pact_context",
        "paths", "session_journal", "session_registry", "session_state",
        "stale_session", "state_file", "task_utils",
    }),  # pact_config reached here via the *_MODE resolver edge
         # (dispatch_gate -> shared.pact_config.get_enum for
         # PACT_DISPATCH_INLINE_MISSION_MODE); pact_config is stdlib-only, so it
         # adds no further transitive shared edges.
         # stale_session reached here via the deny-message self-diagnosis
         # (dispatch_gate -> shared.stale_session.detect_stale_session_block);
         # its own transitive pact_context edge was already in this closure.
         # background_work reached via rule ⑥'s registered-teammate check
         # (dispatch_gate -> shared.background_work.frame_team_and_name, imported
         # inside a function); background_launch, intentional_wait and
         # state_file are its transitive edges.
    "task_lifecycle_gate": frozenset({
        "agent_handoff_marker", "canonical_json", "constants",
        "dispatch_helpers", "handoff_schema",
        "intentional_wait", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "task_metadata_snapshot",
        "task_utils", "teachback_schema", "tool_response", "variety_scorer",
        "background_launch", "background_work", "state_file",
    }),  # task_metadata_snapshot reached via the lead-completion +
         # post-completion-backstop snapshot seams; its transitive edges
         # were already in this closure.
         # handoff_schema reached via the HANDOFF schema advisories (write-time
         # + completion-time); it is a pure stdlib-free leaf, so it adds no
         # further transitive edges.
    "bootstrap_gate": frozenset({
        "compaction_owner", "constants", "marker_schema", "pact_context",
        "paths", "session_journal", "session_registry",
        "session_state",
    }),  # compaction_owner reached via the function-level import that settles
         # staged compaction summaries before a Read or Bash names one.
         # #1023 SHRANK this closure: the carve-out's binding 5 no longer
         # imports bootstrap_marker_writer (it reads the gate-local
         # _secretary_in_members JOIN witness via the already-top-level
         # pact_context._iter_members), so bootstrap_gate no longer reaches
         # bootstrap_marker_writer's transitive closure. The former extras
         # claude_md_manager / session_resume / staleness / pin_caps were
         # reachable ONLY through that deleted edge (bootstrap_gate ->
         # bootstrap_marker_writer -> session_resume (update_session_info) +
         # claude_md_manager (resolve_project_claude_md_path); session_resume ->
         # staleness -> pin_caps) and are now gone from this closure.
         # bootstrap_marker_writer's OWN closure (below) is unchanged.
    "bootstrap_marker_writer": frozenset({
        "compaction_owner", "claude_md_manager", "constants", "failure_cause", "git_helpers", "handoff_schema",
        "marker_schema",
        "pact_context", "paths", "pin_caps", "project_scope", "session_journal",
        "session_registry", "session_resume", "session_state", "staleness",
    }),  # handoff_schema reached TRANSITIVELY, via session_resume's
         # resolve_handoff_field on the resume-brief decision summary — this
         # hook does not import it directly. It is a pure stdlib-free leaf, so
         # it adds no further transitive edges.  # failure_cause reached through claude_md_manager / session_resume /
         # staleness / symlinks: it renders a caught exception as a
         # closed-vocabulary cause token for every routed status producer.  # claude_md_manager / session_resume / staleness / pin_caps reached
         # here because #989's write-back self-heal added
         # resolve_project_claude_md_path (claude_md_manager) + update_session_info
         # (session_resume) imports; session_resume -> staleness -> pin_caps. This
         # is the SOURCE edge that also grows bootstrap_gate's closure (which
         # imports bootstrap_marker_writer).
    "file_tracker": frozenset({
        "background_launch", "background_work", "constants", "intentional_wait",
        "pact_context", "paths", "session_journal", "session_registry",
        "session_state", "state_file", "task_utils",
    }),
    "peer_inject": frozenset({
        "background_launch", "background_work", "constants", "intentional_wait",
        "pact_context", "paths", "peer_context", "plugin_manifest",
        "session_journal", "session_registry", "session_state", "state_file",
        "task_utils",
    }),
    "validate_handoff": frozenset({
        "background_launch", "background_work", "constants", "error_output",
        "intentional_wait", "pact_context", "paths", "session_journal",
        "session_registry", "session_state", "state_file", "task_utils",
        "turn_end_gate", "turn_end_jobs",
    }),  # regenerated from the live derivation: the SubagentStop background
         # check imports turn_end_gate, which reaches background_work and
         # state_file and their helpers.
    "stop_background_gate": frozenset({
        "background_launch", "background_work", "constants", "intentional_wait",
        "pact_context", "paths", "session_journal", "session_registry",
        "session_state", "state_file", "task_utils", "turn_end_gate",
        "turn_end_jobs",
    }),  # regenerated from the live derivation.
    "merge_guard_pre": frozenset({
        "constants", "merge_guard_common", "pact_context", "paths",
        "session_journal", "session_registry", "session_state",
    }),  # both merge guards reach merge_guard_common (the shared token SSOT)
         # + the path/session helper spine; regenerated from the live AST
         # derivation (test_hook_infra_classifier oracle), KD-10.
    "merge_guard_post": frozenset({
        "constants", "error_output", "merge_guard_common", "pact_context",
        "paths", "session_journal", "session_registry", "session_state",
        "tool_response",
    }),  # post additionally reaches error_output (fail-loud alert) and
         # tool_response (the canonical-envelope extractor).
    "wait_filler_gate": frozenset({
        "background_launch", "background_work", "constants", "intentional_wait",
        "pact_context", "paths", "session_journal", "session_registry",
        "session_state", "state_file", "task_utils"
    }),
}

# Every helper module (top-level OR shared) transitively reachable from at least
# one seam hook. An edit to one of these is SECONDARY (could change a seam hook's
# behavior). A hooks/ file NOT in this set and not itself a seam hook (e.g. the
# pure shared helpers gh_helpers / git_helpers / variety_divergence, or a
# non-seam registered hook, or hooks.json) trips PRIMARY only -> the auditable
# waiver path.
SEAM_READING_HELPERS: frozenset[str] = frozenset().union(
    *_SEAM_HOOK_HELPER_CLOSURE.values()
)


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
    """Classify a list of repo-relative changed paths: PRIMARY (touches the
    hooks/ tree) + the implicated seam-dependent hooks (SECONDARY)."""
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
