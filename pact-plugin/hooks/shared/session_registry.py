"""
Location: pact-plugin/hooks/shared/session_registry.py
Summary: Self-registration registry mapping {session_id -> name@team} so a
         separate-process (tmux) teammate can recover its own friendly
         name@team — absent from tmux hook stdin — by self-looking-up its
         own session_id.
Used by: pact_context.resolve_agent_name (Step 3.5), session_init.py
         teammate-branch (lead-team resolution); written by a teammate's
         first-action register call (invoked in script mode by direct path,
         see pact-agent-teams skill On Start).

WHY THIS MODULE IS SELF-CONTAINED (zero ``shared.*`` imports):
    A teammate's first-action register call runs this module IN SCRIPT MODE
    by direct path from an arbitrary cwd:
        python3 <plugin_root>/hooks/shared/session_registry.py register --name 'n@t'
    In script mode Python puts the SCRIPT'S OWN directory (``hooks/shared/``)
    on sys.path, NOT ``hooks/``, so any ``from shared.X import ...`` would
    raise ModuleNotFoundError. Therefore this module imports nothing from
    ``shared.*`` — the sanitizer and the path-containment check are INLINED
    (not imported) so the direct-path invocation works cwd/PYTHONPATH-
    independent. (Precedent: session_journal.py is self-contained → its
    direct-path CLI just works.)

TRUST BOUNDARY (load-bearing): the registry value is SELF-ASSERTED by the
    teammate and therefore FORGEABLE. This registry is LABELING-ONLY. It
    MUST NOT feed any authority/trust check (e.g. trustworthy_actor_name /
    the self-completion gate). Feeding a self-asserted value to a
    "who-acted / is-allowed" predicate re-opens the confused-deputy hole
    that the harness-managed agent_id signal closes. members[]-validation on
    read blunts the forge vector for the labeling use, but does NOT make the
    value an authority signal.

Write path: single ``os.write`` of one JSONL line (<=512B, the portable
    PIPE_BUF bound) with O_APPEND and O_NOFOLLOW, no lock, last-wins-per-
    session_id on read. A single <=PIPE_BUF write is atomic under concurrent
    appenders, so no advisory lock is needed (verified: 0 tears / 32000
    concurrent 512B appends on APFS).

File location: ~/.claude/pact-sessions/.teammate-registry.jsonl
    GLOBAL, fixed, team-agnostic — under pact-sessions/ (PACT-owned), NEVER
    under teams/ (declared "shared, not PACT-owned"). It must be locatable
    with only the reader's OWN session_id; the value's @team carries the
    team datum a teammate cannot otherwise compute. This closes the
    bootstrap paradox.
Permissions: 0o600 (owner read/write only).

Fail-safe everywhere: register() is a no-op and never raises; resolve()
    returns None on any miss/error and never raises. Callers treat None /
    no-op as "use current behavior".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# GLOBAL fixed registry path — under pact-sessions/ (PACT-owned), team-agnostic.
# LOCKED: NEVER team-scoped. Locatable with only the reader's own session_id;
# the value's @team carries the team. Team-scoping would re-open the bootstrap
# paradox a tmux teammate cannot compute its lead's team to locate a team-scoped
# file).


def _config_root() -> Path:
    """Claude Code config/state root — INLINE copy of the canonical
    ``shared.paths.get_claude_config_dir()``.

    session_registry is a standalone-SCRIPT leaf (the team-registration CLI runs
    it as ``python session_registry.py``), so it MUST NOT import ``shared.*`` — a
    relative or ``shared.X`` import raises ImportError in __main__ script mode
    (module docstring + test_cli_runs_from_foreign_cwd_no_shared_import_error
    enforce this). This inline copy mirrors the established ``_is_safe_team_segment``
    dual-copy precedent in this same file.

    DRIFT-ANCHOR: keep byte-equivalent to ``shared.paths.get_claude_config_dir()``;
    a behavioral parity test enforces they never diverge. A1: sources
    CLAUDE_CONFIG_DIR from ``os.environ`` ONLY (never a teammate-writable
    artifact). Returns an UNRESOLVED path — the single ``.resolve()`` stays at the
    containment call site (``_is_under_pact_sessions``).

    ALL THREE config-root derivations in this module (the registry path, the
    ``_is_under_pact_sessions`` anchor, and the team-config path) MUST route
    through this one copy, or ``register()``'s ``_is_under_pact_sessions`` gate
    fail-closes under a non-default CLAUDE_CONFIG_DIR (silent name-recovery loss).
    """
    home = Path.home()
    raw = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not raw:
        return home / ".claude"
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def get_registry_path() -> Path:
    """The global teammate-registry path (config-dir-aware; resolved at call time)."""
    return _config_root() / "pact-sessions" / ".teammate-registry.jsonl"

# Portable single-write atomicity bound. POSIX guarantees os.write atomicity
# up to PIPE_BUF (512 bytes on macOS); a registry line is realistically
# ~90-120B, so this ceiling is generous. A line at or under this bound needs
# no flock — the OS writes it atomically even under concurrent O_APPEND.
_MAX_LINE_BYTES = 512


def _sanitize_agent_name(name: str) -> str:
    """Strip characters that could break out of the name@team value or the
    downstream PACT ROLE marker.

    INLINE COPY — char-class MUST stay byte-identical to
    ``peer_context._sanitize_agent_name`` (peer_context.py) so write/read
    parity holds with the rest of the system. A structural sanitizer-parity
    test asserts the two char-classes match; if you edit this, edit the other.

    Strips all C0 control chars (0x00-0x1F), DEL (0x7F), and the Unicode line
    terminators NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH SEPARATOR
    (U+2029) — these are recognized by str.splitlines() and LLM tokenizers, so
    a name containing one could inject a fake line into a marker template — then
    replaces close-paren ")" to stop early-closing a parenthetical role marker.
    Fallback for empty/None is "unknown".
    """
    if not name:
        return "unknown"
    sanitized = re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]", "_", name)
    return sanitized.replace(")", "_")


def _is_under_pact_sessions(path: Path) -> bool:
    """Return True iff ``path`` resolves to within the pact-sessions root.

    INLINE equivalent of ``session_init._validate_under_pact_sessions`` (can't
    import it — §self-containment). The registry path is a fixed constant, so
    this is mostly belt-and-suspenders, but it stays as defense-in-depth: it
    collapses ``..`` segments and follows symlinks via ``resolve(strict=False)``
    before the containment check, so a planted symlink/traversal at the registry
    path cannot redirect a read/write outside the tree. Uses Path comparison
    semantics (candidate == root or root in candidate.parents) to avoid the
    sibling-prefix collision class (``pact-sessions-evil`` vs ``pact-sessions``).
    Never raises — returns False on any error.
    """
    try:
        sessions_root = (_config_root() / "pact-sessions").resolve()
        candidate = path.resolve(strict=False)
        return candidate == sessions_root or sessions_root in candidate.parents
    except (TypeError, ValueError, OSError):
        return False


def register(name_at_team: str) -> None:
    """Write {own session_id -> sanitized name@team} to the registry.

    No-op + never raises on any failure. The session_id is self-acquired from
    ``$CLAUDE_CODE_SESSION_ID`` ONLY (this helper is a subprocess that inherits
    the teammate's env). If that var is absent (a future CC / non-CC context),
    this is a NO-OP — it never raises and never blocks the teammate.

    Args:
        name_at_team: the teammate's self-supplied "<name>@<team>" value. The
            name half is sanitized; the @team suffix is kept intact (team is
            config-validated on READ, not here).
    """
    try:
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not sid:
            return  # absent → no-op, never raise

        if not name_at_team or "@" not in name_at_team:
            return  # nothing resolvable to store → no-op

        # Sanitize the name half; keep @team intact for read-time validation.
        # Require BOTH halves non-empty — an empty name ("@team") or empty team
        # ("name@") has nothing meaningful to register, and writing a synthetic
        # "unknown@team" could shadow a real later registration via last-wins.
        name, _, team = name_at_team.partition("@")
        if not name or not team:
            return

        # IN-PROCESS SELF-GUARD (the runtime structural topology signal).
        # In in-process teammateMode every teammate shares the lead's process and
        # thus the lead's $CLAUDE_CODE_SESSION_ID, so own ``sid == leadSessionId``
        # and the registry key {session_id -> name@team} collapses many-to-one
        # (last-wins) — the write cannot recover the actor and is pure churn. In
        # tmux mode each teammate is a distinct process with a distinct sid, so
        # ``sid != leadSessionId`` and the write is meaningful. Skip ONLY on a
        # positive confirmed match; this is a liftable guard, not a removal of
        # registration logic. FAIL-OPEN: ``_read_lead_session_id`` returns "" on
        # any unknown/unreadable/malformed/unsafe-team topology, so we WRITE in
        # every uncertain case — over-writing in-process is a harmless collided
        # entry, but under-writing in tmux would blind name-recovery. Silent
        # no-op, matching register's other skip-paths (labeling-only; no notice).
        if sid == _read_lead_session_id(team):
            return  # confirmed in-process → skip the un-recoverable write

        value = _sanitize_agent_name(name) + "@" + team

        registry_path = get_registry_path()
        if not _is_under_pact_sessions(registry_path):
            return  # defense-in-depth: refuse to write outside the tree

        line = json.dumps(
            {"session_id": sid, "value": value},
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > _MAX_LINE_BYTES:
            return  # over the portable single-write atomicity bound → skip

        registry_path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW (POSIX) so a planted symlink at the registry path cannot
        # redirect the write; getattr fallback for platforms that lack it.
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow
        fd = os.open(str(registry_path), flags, 0o600)
        try:
            os.write(fd, encoded)  # single <=PIPE_BUF write → atomic, no lock
        finally:
            os.close(fd)
    except Exception:
        return  # fail-safe: swallow everything, never raise


def resolve(session_id: str) -> str | None:
    """Self-lookup the caller's OWN session_id → sanitized name@team.

    SELF-LOOKUP ONLY: the caller passes its OWN session_id (from
    get_session_id() / hook stdin). There is deliberately NO name-keyed lookup
    API — resolve takes a session_id, never a name — so a reader cannot scan
    for another agent's identity. This prevents cross-team forging.

    The resolved name is members[]-validated against the value's own @team
    config (mandatory even for the labeling use): a teammate that self-supplied
    a wrong name@team is rejected on read → caller falls back to current
    behavior. Sanitized on read as well as write (symmetric).

    Returns ``"<name>@<team>"`` on a verified hit, or None on any
    miss/error — missing file, unreadable, no matching line, no "@", name not a
    member of @team, or any exception. NEVER raises. Caller treats None as
    "use current behavior".
    """
    try:
        if not session_id:
            return None
        registry_path = get_registry_path()
        if not _is_under_pact_sessions(registry_path):
            return None

        # O_NOFOLLOW open for read so a planted symlink can't redirect us.
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(registry_path), os.O_RDONLY | nofollow)
        except OSError:
            return None  # missing / symlink (ELOOP) / unreadable → miss
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return None

        # Scan lines; keep the LAST line whose session_id matches (last-wins).
        matched_value: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # skip a torn/garbage line, never raise
            if isinstance(obj, dict) and obj.get("session_id") == session_id:
                value = obj.get("value")
                if isinstance(value, str):
                    matched_value = value

        if not matched_value or "@" not in matched_value:
            return None

        name, _, team = matched_value.partition("@")
        if not name or not team:
            return None

        if not _name_is_team_member(name, team):
            return None  # integrity: reject name ∉ the value's own @team members

        return _sanitize_agent_name(name) + "@" + team
    except Exception:
        return None  # fail-safe: any unexpected error → miss, never raise


def _is_safe_team_segment(team: str) -> bool:
    """Return True iff ``team`` is a single safe path component — usable to build
    a ``teams/<team>/config.json`` path without raising or escaping the teams root.

    The ``@team`` half of a registry value is SELF-ASSERTED and unsanitized, so a
    garbled/adversarial value could carry a NUL byte (``open``/``read_text``
    rejects it with ``ValueError: embedded null byte`` on every Python version), a
    path separator, or a ``..`` traversal that resolves into a real teams dir and
    is wrongly validated. Reject BEFORE building the path (not merely catching
    after): empty, any C0 control char / DEL / NUL, ``/`` or ``\\``, and the
    traversal segments ``.`` / ``..``. Never raises.

    Logic-parity with ``session_end._is_safe_team_segment`` — inlined here (not
    imported) to keep this module a self-contained leaf (see module docstring).
    """
    if not team:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in team):
        return False
    if "/" in team or "\\" in team:
        return False
    if team in (".", ".."):
        return False
    return True


def _name_is_team_member(name: str, team: str) -> bool:
    """Return True iff sanitized ``name`` is a member of ``team``'s config.

    Loads ~/.claude/teams/<team>/config.json and compares the sanitized
    candidate against the sanitized members[].name set. This is the integrity
    check that blunts the last-wins forge/overwrite vector for the labeling use.
    Never raises — returns False on missing config / non-JSON / an unsafe @team
    path segment / any error.
    """
    # Containment parity with the session_end prune: reject a traversal / NUL /
    # control @team as a single safe path segment BEFORE building the FS path,
    # not merely catching the error after the path is read.
    if not _is_safe_team_segment(team):
        return False
    try:
        config_path = _config_root() / "teams" / team / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return False
        members = config.get("members")
        if not isinstance(members, list):
            return False
        member_names = {
            _sanitize_agent_name(m["name"])
            for m in members
            if isinstance(m, dict) and isinstance(m.get("name"), str)
        }
        return _sanitize_agent_name(name) in member_names
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _read_lead_session_id(team: str) -> str:
    """Return the top-level ``leadSessionId`` from ``teams/<team>/config.json``,
    or ``""`` on any miss/error.

    Used by ``register()``'s in-process self-guard to compute the runtime
    structural topology signal ``own session_id == leadSessionId`` (in-process)
    vs ``!=`` (tmux). LOGIC-PARITY with ``task_claim_gate._read_lead_session_id``,
    INLINED here (not imported) to preserve this module's self-contained-leaf
    invariant (no ``shared.*`` imports — see module docstring). Reuses the exact
    ``_is_safe_team_segment`` guard + ``_config_root()`` config-read idiom that
    ``_name_is_team_member`` above already uses, so the read adds no new I/O
    machinery.

    Fail-safe: returns ``""`` on an unsafe @team segment, missing/unreadable
    config, malformed JSON, non-object top-level, or a missing/non-string key.
    An empty return routes ``register()`` to the fail-OPEN default (WRITE) — an
    unknown topology must never suppress a tmux registration. Never raises.
    """
    if not _is_safe_team_segment(team):
        return ""
    try:
        config_path = _config_root() / "teams" / team / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return ""
        lead_session_id = config.get("leadSessionId")
        return lead_session_id if isinstance(lead_session_id, str) else ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="PACT teammate session registry (self-registration)."
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True
    reg = subparsers.add_parser(
        "register", help="register own session_id -> name@team"
    )
    reg.add_argument(
        "--name", required=True, help="the teammate's own '<name>@<team>' value"
    )
    args = parser.parse_args()
    if args.command == "register":
        register(args.name)
