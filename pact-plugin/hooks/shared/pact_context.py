"""
Location: pact-plugin/hooks/shared/pact_context.py

Shared session context module for PACT hooks.

Provides session identity (team_name, session_id, project_dir, plugin_root)
and agent name resolution for all hooks. Context is written once at SessionStart
by session_init.py and read by subsequent hooks via init() + accessors.

See: docs/architecture/pact-context-module.md for full design rationale.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .session_state import SESSION_ID_CONTROL_CHARS_RE
# One-directional import: session_registry is a self-contained leaf (imports
# nothing from shared.*), so this introduces NO circular import. Used by
# resolve_agent_name Step 3.5 to recover a tmux teammate's friendly name.
from .session_registry import resolve as _registry_resolve
from .paths import get_claude_config_dir

# Slug sanitizer: collapse any character outside the safe-path-component
# allowlist into "_". The slug derives from CLAUDE_PROJECT_DIR's basename
# and flows into shell-quoted command bodies (bootstrap.md's `mkdir -p
# "<path>" && touch "<path>/bootstrap-complete"` interpolation), so a
# project-dir basename containing shell metacharacters (`"`, `$`, backtick,
# `;`, `&&`, `|`) would shell-inject without producer-side sanitization.
# S3 (security-engineer-review) defense: producer-side sanitize-substitute
# before the slug ever reaches the path tree. Sibling defense for session_id
# is the SESSION_ID_CONTROL_CHARS_RE strip applied below in init().
_UNSAFE_SLUG_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")

# Session-scoped context file path, set by init().
# When None, get_pact_context() returns _EMPTY_CONTEXT (no file to read).
# Note: pact_session.py (in skills/pact-memory/scripts/) mirrors this logic
# with a dynamic _context_file_path() function because skill scripts can't
# import from hooks/shared/.
_context_path: Path | None = None

# Module-level cache: populated on first get_pact_context() call.
# Safe because the context file is write-once and each hook invocation
# is a fresh Python process (new module state = clean cache).
_cache: dict | None = None

# Default context dict returned on any error
_EMPTY_CONTEXT = {
    "team_name": "",
    "session_id": "",
    "project_dir": "",
    "plugin_root": "",
    "started_at": "",
}


def reset_for_tests() -> None:
    """Reset this module's mutable session-context state to its import-time
    default. Public test-isolation hook.

    ``pact_context`` memoizes the resolved session context in two
    module-level globals — ``_cache`` (the parsed context dict) and
    ``_context_path`` (the resolved context-file path). Both are populated
    lazily on first read and persist for the life of the process. That is
    correct in production (one session per process) but leaks across tests,
    which reuse a single process: a test that populates the cache with a
    session bleeds it into every later test. The pytest autouse fixture in
    ``tests/conftest.py`` calls this before AND after every test to guarantee
    cross-test isolation.

    Co-located with the state it resets ON PURPOSE: a future rename of
    ``_cache`` / ``_context_path`` must update THIS function in the same
    module, instead of silently turning an external direct-assignment reset
    into a no-op. Pure, no args, idempotent. Resets ONLY the mutable
    cache/path globals — ``_EMPTY_CONTEXT`` and the ``TOKEN_*`` / config
    constants are immutable defaults and are not touched. ADDITIVE: production
    caching behavior and all existing callers are unchanged; this is invoked
    only by tests.
    """
    global _cache, _context_path
    _cache = None
    _context_path = None


def _build_session_path(slug: str, session_id: str) -> Path:
    """Build the session-scoped directory path.

    Canonical path: ~/.claude/pact-sessions/{slug}/{session_id}/

    Used by init(), get_session_dir(), and write_context() to avoid
    duplicating path construction logic.

    Path traversal guard: resolves the constructed path and verifies it
    stays under ~/.claude/pact-sessions/ using Path.parents containment
    (immune to sibling-prefix collisions by design — matches
    session_init._validate_under_pact_sessions). A malicious session_id
    like "../../etc" would resolve outside the expected tree — fall back
    to a sanitized basename. Fail-closed: if the validation itself
    raises, return a slug-only path (no session_id component).

    S3 defense (security-engineer-review): the slug derives from
    CLAUDE_PROJECT_DIR's basename and ends up interpolated into a
    shell-quoted command body in commands/bootstrap.md. Sanitize at the
    producer (here) so any non-allowlist character (shell metachars,
    control chars, whitespace) is collapsed to "_" before the slug
    reaches any downstream consumer. Sanitize-substitute (NOT reject)
    so sessions with unusual project-dir names still proceed.
    """
    safe_slug = _UNSAFE_SLUG_CHARS_RE.sub("_", slug) if slug else slug
    sessions_root = get_claude_config_dir() / "pact-sessions"
    candidate = sessions_root / safe_slug / session_id
    try:
        sessions_root_resolved = sessions_root.resolve()
        resolved = candidate.resolve(strict=False)
        if resolved == sessions_root_resolved or sessions_root_resolved in resolved.parents:
            return candidate
        basename = Path(session_id).name
        if basename in ("", ".", "..") or "/" in basename:
            candidate = sessions_root / safe_slug
        else:
            candidate = sessions_root / safe_slug / basename
    except (OSError, ValueError):
        candidate = sessions_root / safe_slug
    return candidate


def _get_context_file_path() -> Path | None:
    """Return the session-scoped context file path, or None if init() not called.

    When None, get_pact_context() returns _EMPTY_CONTEXT without attempting
    any file I/O. There is no fallback to a global path — all reads require
    a session-scoped path established by init().
    """
    return _context_path


def is_initialized() -> bool:
    """Return True iff init() (or write_context()) has set _context_path.

    Used by callers (notably session_journal's implicit API) to detect the
    "hook ran before pact_context was initialized" failure mode without
    coupling to the private module attribute. False means subsequent
    reads/writes derived from session context will silently fail-open
    (empty list, None, False) and the caller may want to take an alternate
    path.
    """
    return _context_path is not None


def init(input_data: dict) -> None:
    """
    Initialize the context module with session-scoped path.

    Must be called by each hook after parsing stdin JSON. Extracts session_id
    from input_data and CLAUDE_PROJECT_DIR from environment to construct the
    session-scoped context file path:
        ~/.claude/pact-sessions/{project-slug}/{session-id}/pact-session-context.json

    Where project-slug is Path(project_dir).name (e.g., "PACT-Plugin").

    If session_id or project_dir is unavailable, leaves _context_path as None.
    Readers will return _EMPTY_CONTEXT without attempting any file I/O.

    No-op if _context_path is already set (e.g., by a test fixture or a prior
    init() call within the same process).

    Args:
        input_data: Parsed stdin JSON from the hook
    """
    global _context_path, _cache

    # Skip if already initialized (test fixtures pre-set _context_path)
    if _context_path is not None:
        return

    session_id = ""
    raw_id = input_data.get("session_id")
    if raw_id:
        # Apply the SAME allowlist-substitute regex as the slug producer
        # (one site below) so session_id and slug share one safe-path-
        # component contract. Symmetric defense per memory
        # patterns_symmetric_sanitization.md: every interpolation sink
        # shares the same allowlist regex `[^A-Za-z0-9_-]`, so asymmetric
        # strip sets across sinks cannot become an attacker entry point.
        # session_id reaches the disclosed PACT_SESSION_DIR= path
        # interpolated into bootstrap.md's shell command body, so shell
        # metacharacters (`$`, backtick, `;`, `(`, `)`, etc.) MUST be
        # substituted, not just control chars stripped.
        # Sanitize-substitute (NOT reject) so malformed stdin doesn't
        # crash the hook; cleaned id forms a single segment.
        session_id = _UNSAFE_SLUG_CHARS_RE.sub("_", str(raw_id))

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    if session_id and project_dir:
        slug = Path(project_dir).name
        _context_path = (
            _build_session_path(slug, session_id) / "pact-session-context.json"
        )
        # Clear cache so subsequent reads use the new path
        _cache = None
    # else: leave _context_path as None — readers return _EMPTY_CONTEXT


def get_pact_context() -> dict:
    """
    Read session context from the context file.

    Returns dict with keys: team_name, session_id, project_dir, plugin_root, started_at.
    All values are strings. Returns empty strings for all keys on any error
    (file missing, malformed JSON, permission denied).

    Caching: Result is cached in a module-level variable after first read.
    The file is write-once/read-many, so caching is safe within a single
    hook process lifetime.
    """
    global _cache
    if _cache is not None:
        return _cache

    ctx_path = _get_context_file_path()
    if ctx_path is None:
        # init() was not called or session_id/project_dir unavailable —
        # no file to read. Return empty context without logging (this is
        # normal for hooks that run before session_init writes the file).
        _cache = dict(_EMPTY_CONTEXT)
        return _cache

    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        _cache = {
            "team_name": str(data.get("team_name", "")),
            "session_id": str(data.get("session_id", "")),
            "project_dir": str(data.get("project_dir", "")),
            "plugin_root": str(data.get("plugin_root", "")),
            "started_at": str(data.get("started_at", "")),
        }
        return _cache
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        print(
            f"pact_context: could not read context file: {e}",
            file=sys.stderr,
        )
        _cache = dict(_EMPTY_CONTEXT)
        return _cache


def get_team_name() -> str:
    """Convenience: return team_name from context, lowercased. Empty string on error."""
    return get_pact_context().get("team_name", "").lower()


def get_session_id() -> str:
    """Convenience: return session_id from context. Empty string on error."""
    return get_pact_context().get("session_id", "")


def get_project_dir() -> str:
    """Convenience: return project_dir from context. Empty string on error."""
    return get_pact_context().get("project_dir", "")


def get_session_dir() -> str:
    """Return the session-scoped directory path, or '' if unavailable.

    Constructs: ~/.claude/pact-sessions/{slug}/{session_id}/

    Uses get_session_id() and get_project_dir() from the cached context.
    Returns "" if either is unavailable.

    The returned path may not exist on disk — callers must create it
    (mkdir -p) before writing files.
    """
    session_id = get_session_id()
    project_dir = get_project_dir()
    if not session_id or not project_dir:
        return ""
    slug = Path(project_dir).name
    return str(_build_session_path(slug, session_id))


def get_plugin_root() -> str:
    """Convenience: return plugin_root from context. Falls back to the
    CLAUDE_PLUGIN_ROOT env var (exported into every hook process by the
    harness) when the context-file value is empty or the file is missing.

    Fallback-AFTER-file-read, never a replacement: the file value wins
    whenever it is non-empty, and the uniform ``or`` covers both the
    file-missing and field-empty cases. If the platform ever regresses
    the env export, behavior degrades to the historical file-only read
    rather than introducing a new failure mode. Empty string when both
    sources are unavailable.
    """
    return get_pact_context().get("plugin_root", "") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT", ""
    )


def generate_team_name(input_data: dict) -> str:
    """
    Generate a session-unique PACT team name.

    Uses the first 8 characters of the session_id from the SessionStart hook
    stdin JSON to create a unique team name like "pact-0001639f". Falls back
    to a random 8-character hex suffix if session_id is not in stdin.

    Args:
        input_data: Parsed JSON from stdin (SessionStart hook input)

    Returns:
        Team name string like "pact-0001639f"
    """
    # INVARIANT: all team directory names MUST be produced by this
    # function. Output is lowercase ASCII hex ([a-f0-9-]) prefixed with
    # "pact-" — the session_end reaper's exact-match skip predicate
    # (cleanup_old_teams) and the union skip-set for cleanup_old_tasks
    # rely on this shape. A writer that creates ~/.claude/teams/ dirs
    # with characters outside this charset (uppercase, unicode,
    # separators) could bypass the skip predicate and be reaped on the
    # NEXT session_end.
    raw_id = input_data.get("session_id")
    session_id = str(raw_id) if raw_id else ""
    if session_id:
        suffix = re.sub(r"[^a-f0-9-]", "", session_id[:8]) or secrets.token_hex(4)
    else:
        suffix = secrets.token_hex(4)
    return f"pact-{suffix}"


def _is_unknown_or_missing_session(raw_id: object) -> bool:
    """Return True if the session_id is missing, blank, a sentinel, or contains control chars.

    Single canonical predicate for the malformed-stdin gate. Three call
    sites consult this helper so the gates can never drift: the persistence
    call sites at the top of session_init's main() (build_context_cache +
    persist_context + append_event), the CLAUDE.md write at session_init
    step 5b, and the self-heal gate in heal_context_if_missing() below
    (a missing/sentinel id would make generate_team_name go RANDOM and
    create an unreapable session dir). Drift previously allowed
    three corruption classes:

    * Whitespace-only ids (e.g. `"   "`) were truthy and bypassed
      `not raw_id`, leaking through to the context-persist path
      (build_context_cache resolves it, persist_context mkdir's it) as a
      literal directory name.
    * An attacker-supplied `"unknown-foo"` value passed `not raw_id` because
      the string is non-empty, then later passed `startswith("unknown")`
      and was written into CLAUDE.md anyway via a different code path.
    * A session_id containing C0 control characters (newline, CR, NUL,
      etc.) passed all existing non-empty/non-sentinel checks but, when
      interpolated into ``f"- Resume: `claude --resume {session_id}`"``
      by update_session_info, could inject a fake CLAUDE.md line via
      embedded newlines. The unified helper strips C0 controls to close
      this injection path at the session_id entry point.

    The unified helper rejects all of: None, non-strings, empty strings,
    whitespace-only strings, any string already shaped like the
    `unknown-*` sentinel, and any string containing C0 control characters
    or DEL.
    """
    if not raw_id:
        return True
    if not isinstance(raw_id, str):
        return True
    stripped = raw_id.strip()
    if not stripped:
        return True
    if SESSION_ID_CONTROL_CHARS_RE.search(raw_id):
        return True
    return stripped.startswith("unknown-")


def resolve_agent_name(
    input_data: dict,
    team_name: str | None = None,
    teams_dir: str | None = None,
) -> str:
    """
    Resolve the human-readable agent name from hook stdin JSON.

    Resolution chain:
    1. input_data["agent_name"] — if present, use directly
    2. input_data["agent_id"] string split — if contains "@", split and
       return the name part (format: "name@team_name")
    3. input_data["agent_id"] → lookup in team config members array
    3.5. input_data["session_id"] → self-registration registry self-lookup,
       split "@" and return the name part (recovers a tmux teammate's name
       that is absent from hook stdin; fail-safe, falls through on miss)
    4. input_data["agent_type"] → strip "pact-" prefix as fallback name
    5. "" — unknown agent (main process, non-PACT context)

    Args:
        input_data: Parsed stdin JSON from the hook
        team_name: Override team name (defaults to get_team_name())
        teams_dir: Override teams directory path (for testing)

    Returns:
        Agent name string, or "" if unresolvable
    """
    # Step 1: direct agent_name field
    agent_name = input_data.get("agent_name")
    if agent_name:
        return str(agent_name)

    # Step 2: agent_id string split (common case — avoids file I/O)
    agent_id = input_data.get("agent_id")
    if agent_id and "@" in str(agent_id):
        return str(agent_id).split("@")[0]

    # Step 3: agent_id → team config lookup (fallback for non-@ formats)
    if agent_id:
        resolved_team = team_name if team_name else get_team_name()
        if resolved_team:
            name = _lookup_agent_in_team_config(
                str(agent_id), resolved_team, teams_dir
            )
            if name:
                return name

    # Step 3.5: own session_id → self-registration registry. Recovers a tmux
    # teammate's friendly name@team, which is ABSENT from tmux hook stdin (no
    # agent_name / agent_id), by self-looking-up its OWN session_id. Gated
    # behind Steps 1-3 (all early returns / the in-process common case never
    # reaches here), so this file read fires ONLY for the tmux-degraded frame
    # that needs it. resolve() is fail-safe (None on any miss/error, never
    # raises); on None we fall through to Step 4 (current behavior). The value
    # is name@team — return the name half, matching Step 2's split("@")[0] shape.
    session_id = input_data.get("session_id")
    if session_id:
        resolved = _registry_resolve(str(session_id))
        if resolved and "@" in resolved:
            return resolved.split("@")[0]

    # Step 4: agent_type → strip "pact-" prefix
    agent_type = input_data.get("agent_type")
    if agent_type:
        type_str = str(agent_type)
        if type_str.startswith("pact-"):
            return type_str[len("pact-"):]
        return type_str

    # Step 5: unresolvable
    return ""


# Lead agent_type spellings — the single source of truth for is_lead /
# classify_session_role. Both forms the harness can stamp when the
# orchestrator is launched: the qualified `--agent PACT:pact-orchestrator`
# and the unqualified `--agent pact-orchestrator`. Case-SENSITIVE exact
# match (a mixed-case spelling is NOT a lead). Deliberately a 2-element
# literal, NOT derived from _specialist_registry(): pact-orchestrator.md
# lives in agents/, so a registry-derived set would both conflate the
# orchestrator with a specialist AND miss the qualified `PACT:` spelling.
LEAD_AGENT_TYPES = frozenset({"PACT:pact-orchestrator", "pact-orchestrator"})


def is_lead(input_data: dict) -> bool:
    """Return True iff this hook frame belongs to the PACT team-lead.

    Reads the TOP-LEVEL ``agent_type`` field DIRECTLY (not via
    ``resolve_agent_name``) and tests membership in ``LEAD_AGENT_TYPES``.
    Reading ``agent_type`` directly drops ``resolve_agent_name``'s Step-4
    prefix-strip ambiguity and the ``agent_id`` resolution surface entirely
    out of the role decision: the lead/teammate question reduces to one
    dict lookup on one harness-set field.

    PURE: reads ONLY ``agent_type``. Never reads ``tool_input``,
    ``agent_id``, environment variables, or team config — purity is a
    tested assertion (a future author must not smuggle other signals in).

    TOTAL (given a dict): never raises when ``input_data`` is a dict. The
    membership test is guarded by an ``isinstance(..., str)`` check because
    ``x in frozenset`` raises ``TypeError`` for an unhashable ``x`` (a malformed
    ``agent_type`` that is a list/dict) — and a non-string ``agent_type`` is
    definitionally not a lead spelling anyway, so it short-circuits to False.
    ``dict.get`` on a non-dict input would still raise, so callers that may pass
    a non-dict must guard upstream; in practice every hook parses stdin into a
    dict before calling. Totality preserves each gate's existing exception
    posture (``bootstrap_gate`` fail-CLOSED; the pin gates fail-OPEN) — a raising
    predicate would change that per-gate fail semantics. (We deliberately do NOT
    add an ``isinstance(input_data, dict)`` guard: it would change those per-gate
    postures, which rely on a non-dict stdin raising through to each gate's own
    try/except.)

    COORDINATION CONTROL, NOT A SECURITY BOUNDARY. This predicate decides
    *coordination* (which frame performs lead-only writes / drives the
    bootstrap gate), not *authorization*. Lead, teammate, and plain frames
    all run as the same OS user, so there is no privilege boundary to
    breach here. ``agent_type`` is harness-spawn-set from process context
    and is NOT reflectable from untrusted request content (prompt text,
    file-under-review, tool arguments cannot forge it) — but a future
    author must NOT hang an access-control decision on this function.

    EMPIRICAL PROVENANCE. ``agent_type`` is the UNIVERSAL role discriminator:
    it is the field that carries the lead/teammate signal on every hook event
    where this predicate is READ — SessionStart, UserPromptSubmit, PreToolUse,
    PostToolUse (including the ``TaskCreate`` / ``TaskUpdate``-matched frames),
    and PostCompact. The signal is VALUE-MEMBERSHIP, not field-presence: a lead
    stamps one of the two ``LEAD_AGENT_TYPES`` spellings, a teammate stamps its
    specialist value (e.g. ``pact-architect``), and a plain / non-PACT primary
    frame omits the field entirely. ``agent_id`` / ``agent_name`` / ``team_name``
    are ABSENT on tmux frames — a predicate keyed on any of those would mis-
    resolve, which is exactly why this one keys on ``agent_type`` alone.

    Capture scope (Claude Code 2.1.167). Verbatim tmux stdin was captured for
    SessionStart, UserPromptSubmit, PostToolUse, and TaskCompleted — note
    TaskCompleted was captured for the #917 emit-path, NOT because is_lead is
    read there (it is not; the TaskCompleted hook gates on ``team_name`` +
    journal writability, not this predicate). PreToolUse and PostCompact were
    NOT separately captured: their ``agent_type`` shape is inferred from the
    uniform harness-stamping the captured frames establish (PostCompact has only
    a synthesized-from-matrix builder; PreToolUse has no frame). The captured
    frames live in ``tests/fixtures/role_frames.py`` (the ``captured_*``
    accessors); the per-event truth table — which rows are captured vs inferred
    — is in ``hooks/shared/HOOK_STDIN_DISCRIMINATORS.md``.

    Args:
        input_data: Parsed stdin JSON from the hook.

    Returns:
        True iff ``input_data["agent_type"]`` is one of LEAD_AGENT_TYPES.
    """
    agent_type = input_data.get("agent_type")
    # isinstance guard keeps the predicate TOTAL: `x in frozenset` raises
    # TypeError for an unhashable x (list/dict). A non-string agent_type is
    # not a lead spelling, so short-circuit to False.
    return isinstance(agent_type, str) and agent_type in LEAD_AGENT_TYPES


def classify_session_role(input_data: dict) -> str:
    """Classify the hook frame's session role as a 3-way value.

    A bare ``is_lead`` boolean cannot separate "teammate" from "neither"
    (a non-PACT / no-``--agent`` primary frame). The startup warning in
    session_init needs that distinction — it fires ONLY for the "unknown"
    role — so this companion classifier reads the same ``agent_type`` field
    and the same ``LEAD_AGENT_TYPES`` SSOT as ``is_lead``.

        lead     := agent_type in LEAD_AGENT_TYPES
        teammate := agent_type present (truthy) and not in LEAD_AGENT_TYPES
        unknown  := agent_type absent (None / missing / empty)

    PURE / TOTAL on the same contract as ``is_lead``. Same coordination-not-
    security caveat applies.

    Args:
        input_data: Parsed stdin JSON from the hook.

    Returns:
        One of ``"lead"``, ``"teammate"``, ``"unknown"``.
    """
    # Delegate the lead test to is_lead so there is a SINGLE expression of
    # "what lead means" (DRY) — a future change to the lead predicate (e.g.
    # normalization) then lands in one place. is_lead carries the isinstance
    # guard that keeps the membership test TOTAL for an unhashable agent_type.
    if is_lead(input_data):
        return "lead"
    if input_data.get("agent_type"):
        return "teammate"
    return "unknown"


def _iter_members(
    team_name: str,
    teams_dir: str | None = None,
) -> list[dict]:
    """Read and validate the members[] list from a team config file.

    Returns a list of dict members from
    ``~/.claude/teams/{team_name}/config.json``, with non-dict entries
    filtered out so callers can safely apply ``member.get(...)`` predicates
    without per-call ``isinstance`` guards.

    Returns ``[]`` silently on any of:
        - empty team_name
        - missing config file (FileNotFoundError)
        - I/O error (OSError, including PermissionError)
        - malformed JSON (json.JSONDecodeError, ValueError)
        - non-object top-level JSON (AttributeError on .get())
        - missing or non-list ``members`` key
        - any unexpected TypeError during validation

    Silent-on-error is intentional: callers (writer's
    ``_team_has_secretary``, lookup's ``_lookup_agent_in_team_config``)
    use the empty result as the "team config not usable" signal and
    own their own user-visible advisory if any. This consolidates the
    JSON-shape validation that previously lived inline in two places.

    Args:
        team_name: Team name for config path. Empty string returns [].
        teams_dir: Override teams directory (for testing).
    """
    if not team_name:
        return []
    if teams_dir:
        config_path = Path(teams_dir) / team_name / "config.json"
    else:
        config_path = (
            get_claude_config_dir() / "teams" / team_name / "config.json"
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        members = data.get("members")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return []
    if not isinstance(members, list):
        return []
    return [m for m in members if isinstance(m, dict)]


def _lookup_agent_in_team_config(
    agent_id: str,
    team_name: str,
    teams_dir: str | None = None,
) -> str:
    """
    Look up agent name from team config file by agent id.

    Scans ``_iter_members(team_name, teams_dir)`` for an entry where
    ``member["id"] == agent_id`` and returns its name.

    Args:
        agent_id: The agent UUID to look up
        team_name: Team name for config path
        teams_dir: Override teams directory (for testing)

    Returns:
        Agent name if found, empty string otherwise
    """
    for member in _iter_members(team_name, teams_dir):
        if member.get("id") == agent_id:
            return str(member.get("name", ""))
    return ""


def build_context_cache(
    team_name: str,
    session_id: str,
    project_dir: str,
    plugin_root: str = "",
) -> tuple[Path, dict] | None:
    """Build the session context dict + path and populate the in-process cache.

    PURE of disk I/O: this is the cache-half of the session-context write. It
    builds the ``context`` dict, resolves the session-scoped ``target`` path,
    and populates the module-level ``_cache`` / ``_context_path`` so same-process
    readers (``get_session_dir()`` and ``append_event()``'s implicit
    path-resolution) work immediately — but it NEVER touches disk.

    Returns ``(target, context)`` so a caller can pass them straight to
    ``persist_context()``; returns ``None`` when the session-scoped path cannot
    be computed (missing ``session_id`` / ``project_dir``), preserving the
    historical skip-write behavior (readers fall back to ``_EMPTY_CONTEXT``).

    CACHE OWNERSHIP (#877, the disk/cache seam): this function is the SOLE owner
    of ``_cache`` / ``_context_path``. The cache is the PROCESS'S OWN working
    context — populated UNCONDITIONALLY for every frame (lead, teammate, plain),
    independent of whether the disk file is ever persisted. Disk persistence is
    a separate, ``is_lead``-gated best-effort side-effect (``persist_context``)
    for OTHER processes to read; a non-lead frame builds+caches and never
    persists, and a lead frame whose ``persist_context`` later raises STILL has
    its correct in-memory context (it is NOT unset on persist failure). This
    uniform rule replaced the old ``write_disk`` flag — see ``persist_context``.

    Args:
        team_name: The generated team name (e.g., "pact-0001639f")
        session_id: Session ID from stdin JSON or env var
        project_dir: CLAUDE_PROJECT_DIR value
        plugin_root: CLAUDE_PLUGIN_ROOT value (path to installed plugin directory)

    Returns:
        ``(target, context)`` on success, or ``None`` if the path is uncomputable.
    """
    global _context_path, _cache

    context = {
        "team_name": team_name,
        "session_id": session_id,
        "project_dir": project_dir,
        "plugin_root": plugin_root,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Use _context_path if already set (from init() or test fixture),
    # otherwise compute from session_id and project_dir.
    if _context_path is not None:
        target = _context_path
    elif session_id and project_dir:
        slug = Path(project_dir).name
        target = (
            _build_session_path(slug, session_id) / "pact-session-context.json"
        )
    else:
        # Cannot compute session-scoped path — skip.
        # Readers fall back to empty context via _EMPTY_CONTEXT.
        print(
            "pact_context: skipping write — session_id or project_dir unavailable",
            file=sys.stderr,
        )
        return None

    # Populate the in-process cache UNCONDITIONALLY (Option A). The cache is the
    # process's own working truth; disk persistence is an independent side-effect.
    _context_path = target
    _cache = context
    return target, context


def persist_context(target: Path, context: dict) -> None:
    """Atomically write the session context to disk. The impure half of the seam.

    Writes ``context`` to ``target`` via a temp file + ``os.rename`` (crash-safe
    atomic write), 0o600 permissions. Called ONLY for a lead frame (the on-disk
    session-context file is a lead-only artifact a teammate/plain frame must NOT
    clobber — #877). Fail-open: any error is logged and swallowed.

    DOES NOT touch ``_cache`` / ``_context_path`` — ``build_context_cache`` is the
    sole owner of cache state (Option A). A persist failure therefore leaves the
    process's in-memory context intact (correct values), rather than unsetting it
    and degrading the lead to empty strings. ``target`` / ``context`` are exactly
    the pair returned by ``build_context_cache``.

    Args:
        target: The resolved session-context file path (from build_context_cache).
        context: The context dict to serialize (from build_context_cache).
    """
    context_dir = target.parent
    try:
        context_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Write to temp file in the same directory (required for atomic rename)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(context_dir),
            prefix=".pact-session-context-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(context, f)
            os.chmod(tmp_path, 0o600)
            os.rename(tmp_path, str(target))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(
            f"pact_context: could not write context file: {e}",
            file=sys.stderr,
        )


def write_context(
    team_name: str,
    session_id: str,
    project_dir: str,
    plugin_root: str = "",
) -> None:
    """
    Write the session context file (full op: build + cache + persist to disk).

    Computes the session-scoped path from session_id and project_dir:
        ~/.claude/pact-sessions/{project-slug}/{session-id}/pact-session-context.json
    Requires session_id and project_dir — returns without writing if either
    is missing (the fail-open read behavior handles the no-file case).

    Uses atomic write (write to temp file, then os.rename) for crash safety.
    File permissions: 0o600 (user-only read/write).

    Also populates ``_cache`` / ``_context_path`` so subsequent reads in the same
    process use the correct path (relevant for callers that read context after
    writing).

    SEAM (#877): this is the thin composition of the two halves —
    ``build_context_cache`` (pure: build dict + path + populate cache) followed by
    ``persist_context`` (impure: disk write). ``session_init`` does NOT call this
    full op; it composes the two halves directly so it can populate the cache for
    EVERY frame while gating the disk persist on ``is_lead`` (a teammate/plain
    frame must not clobber the lead's on-disk file). Every OTHER caller wants the
    full build+cache+persist and keeps this unchanged public contract.

    Args:
        team_name: The generated team name (e.g., "pact-0001639f")
        session_id: Session ID from stdin JSON or env var
        project_dir: CLAUDE_PROJECT_DIR value
        plugin_root: CLAUDE_PLUGIN_ROOT value (path to installed plugin directory)
    """
    result = build_context_cache(team_name, session_id, project_dir, plugin_root)
    if result is not None:
        persist_context(*result)


def describe_context_failure() -> str:
    """One-line diagnosis of WHY session context is empty, for embedding in
    consumer deny messages (e.g. dispatch_gate). Returns '' when context is
    healthy (file present and readable). Cases:

      - _context_path is None → 'session context underivable (no session_id
        in hook stdin or CLAUDE_PROJECT_DIR unset)'
      - path set, file absent → 'context file not found: {path} — ...'
        naming the derived path, the likely session_init root cause, and
        the two recovery actions (self-heal on next prompt / /PACT:bootstrap)
      - path set, file present (readable or not) → '' (not a missing-context
        failure; read errors are already stderr-logged by get_pact_context)

    Deliberately NOT auto-injected into get_pact_context(), which must stay
    silent on file-absent (normal for pre-session_init hooks and non-PACT
    sessions). TOTAL: no exceptions escape — an OSError from exists() maps
    to the file-absent arm (an unstattable path is not a healthy context).
    """
    if _context_path is None:
        return (
            "session context underivable (no session_id in hook stdin or "
            "CLAUDE_PROJECT_DIR unset)"
        )
    try:
        file_present = _context_path.exists()
    except OSError:
        file_present = False
    if not file_present:
        return (
            f"context file not found: {_context_path} — session_init may "
            "have failed at SessionStart; submit any message to trigger "
            "self-heal, or run /PACT:bootstrap"
        )
    return ""


def heal_context_if_missing(input_data: dict) -> bool:
    """Re-create a missing pact-session-context.json from the same inputs
    session_init would use (stdin session_id, CLAUDE_PROJECT_DIR,
    CLAUDE_PLUGIN_ROOT). Lead frames only (#877). Returns True iff healed.

    Fires ONLY when ALL hold:
      1. init(input_data) derived a path (_context_path is not None)
      2. the file is ABSENT on disk — a present-but-malformed file is NOT
         clobbered (different failure class, preserve the evidence; read
         errors are already stderr-logged by get_pact_context)
      3. is_lead(input_data) — a teammate/plain frame must never create
         or clobber the lead's on-disk file (#877). UserPromptSubmit has
         no teammate fire path; the residual non-lead frame is a plain
         session (agent_type absent) → no heal, exactly as intended
      4. NOT _is_unknown_or_missing_session(input_data.get("session_id"))
         — a missing/sentinel id would make generate_team_name go RANDOM
         (fabricated team name) and create an unreapable session dir;
         mirrors session_init's session_id_was_missing persist gate

    Heal = write_context(generate_team_name(input_data), str(raw_id),
    CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_ROOT) — the existing
    build+cache+persist seam: atomic write (mkstemp+rename), 0o600, and
    build_context_cache resets _cache so THIS process's subsequent
    get_team_name()/get_session_dir() calls see the healed values.

    Content parity with session_init: session_id is persisted as
    str(raw_id) — RAW, exactly as session_init's main() persists it
    (init() sanitizes only the PATH segment, not the stored value).
    started_at is heal time, not session start — its consumers are
    journal-naming/display only (cosmetic), documented trade-off.

    Two-healer race (bootstrap_marker_writer + bootstrap_prompt_gate fire
    on the same prompt; the platform runs same-event hooks in parallel):
    persist_context is atomic (mkstemp + rename), and both healers compute
    IDENTICAL content except started_at → last-writer-wins with equivalent
    content. Benign.

    TOTAL: never raises — any internal exception is swallowed to a stderr
    line and False (both callers are fail-open hooks; the heal must not
    convert a degraded session into a crashed hook).

    Args:
        input_data: Parsed stdin JSON from the hook (UserPromptSubmit frame).

    Returns:
        True iff the context file was absent and is now healed on disk.
    """
    try:
        if _context_path is None:
            return False
        if _context_path.exists():
            return False
        if not is_lead(input_data):
            return False
        raw_id = input_data.get("session_id")
        if _is_unknown_or_missing_session(raw_id):
            return False
        write_context(
            generate_team_name(input_data),
            str(raw_id),
            os.environ.get("CLAUDE_PROJECT_DIR", ""),
            os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
        )
        # write_context's persist half is fail-open (errors swallowed to
        # stderr) — verify on disk so the contract "True iff healed" holds.
        return _context_path.exists()
    except Exception as e:
        print(f"pact_context: self-heal failed: {e}", file=sys.stderr)
        return False
