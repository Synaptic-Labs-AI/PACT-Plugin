#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/agent_handoff_marker.py
Summary: SSOT for agent_handoff emission — the shape-agnostic emit-eligibility
         atom (is_signal_task) + occupant-identity marker key derivation +
         the O_EXCL test-and-set (already_emitted) + the compensating-unclaim
         rollback (unclaim, #901) that removes a marker whose journal write
         failed, both resolving the marker path via one SSOT derivation.
         Imported by BOTH agent_handoff emit paths
         so they derive the SAME emit decision and the SAME marker key and
         dedup to exactly one agent_handoff event per (team, task_id,
         occupant):
           - agent_handoff_emitter.py (TaskCompleted; the platform Stop-sweep
             dispatches this on every matching owner — "b1")
           - task_lifecycle_gate.py   (the lead's TaskUpdate(completed)
             acceptance-commit; closes the #869 never-fires gap — "b2")
Used by: agent_handoff_emitter.py, task_lifecycle_gate.py, and the emitter
         idempotency / path-sanitization test suites.

Coupling rationale: this module is the SSOT for the marker key + test-and-set.
The two emit paths MUST import the same occupant_hash() + already_emitted()
so their marker filenames align byte-for-byte. Divergent key derivation
across two paths is EXACTLY the #887 stale-marker bug class — a single
source of truth makes alignment structural rather than a convention two
files must each remember to honor.

Occupant identity: the marker filename is f"{task_id}-{occupant_hash}" where
occupant_hash = a STABLE hash of (teammate_name + task_subject). "Stable"
means hashlib — NOT the builtin hash(): CPython salts str hashing with
PYTHONHASHSEED, so builtin hash() differs across processes/fires and would
defeat the cross-process O_EXCL dedup the marker exists to provide.

Re-scoped subject → one extra emit: because the subject is part of the key,
a task whose subject changes mid-lifespan emits one additional agent_handoff
event. This biases to HANDOFF preservation, never loss — the intended
trade-off (see occupant_hash).
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
from pathlib import Path

from .paths import get_claude_config_dir

# Hex chars of the SHA-256 digest retained for the occupant component of the
# marker key. 16 hex chars = 64 bits — collision-negligible for the tiny
# per-(team, task_id) occupant namespace (a handful of occupants per task at
# most), while keeping the marker filename short.
_OCCUPANT_HASH_LEN = 16

# Default O_EXCL marker directory name — the agent_handoff event family's
# namespace. Keyword-only `namespace` parameters below default to this so
# every pre-existing caller resolves a byte-identical marker path; a sibling
# event family (task_metadata_snapshot) passes its own module-constant
# namespace through hard-bound wrappers so the two families' dedup markers
# can never suppress each other. Namespace values are module constants,
# never input-derived.
_DEFAULT_MARKER_NAMESPACE = ".agent_handoff_emitted"

# Signal-task types — tasks that MUST NOT emit a phantom agent_handoff event
# (a blocker/algedonic completion is a control signal, not a HANDOFF; emitting
# would pollute read_events("agent_handoff") + mis-route secretary harvest).
# Matches the `task_type in ("blocker", "algedonic")` check inside
# task_utils.find_blockers and the session_resume convention. Extracted here
# (per the agent_handoff_emitter forward-anchor) now that there is a 2nd
# consumer: the #869 lead-side emit in task_lifecycle_gate.py shares this
# exact emit-eligibility atom. (A future cleanup could promote this to
# task_utils to also dedup find_blockers' inline literal — out of scope here.)
SIGNAL_TASK_TYPES = ("blocker", "algedonic")


def is_signal_task(task_metadata: object) -> bool:
    """Return True iff the task is a signal task (blocker/algedonic) that must
    be excluded from agent_handoff emission.

    Pure; never raises. A non-dict metadata (None, missing, malformed) is
    not a signal task → returns False (the emit-eligibility default; the
    handoff-presence gate independently suppresses no-handoff tasks).
    """
    if not isinstance(task_metadata, dict):
        return False
    return task_metadata.get("type") in SIGNAL_TASK_TYPES


def sanitize_path_component(value: str) -> str:
    """
    Strip path-traversal fragments + C0 control chars from a value destined
    for filesystem joins.

    Mirrors the regex used inside task_utils.read_task_json so the status-read
    site and the marker-write site apply symmetric sanitization. Without this,
    an attacker-crafted task_id / team_name that happens to sanitize (in
    read_task_json) into a matching existing completed-task file could still
    carry raw "../" fragments into the marker-path join.

    Strips path-traversal primitives (`/`, `\\`, `..`) and C0 control
    characters (NUL, CR/LF, and the 0x00-0x1f range) at the producer
    boundary. Control-char stripping defends against log-injection and
    embedded-newline attacks on values that flow into filesystem paths.
    """
    return re.sub(r"[/\\\x00-\x1f]|\.\.", "", value)


def occupant_hash(teammate_name: str, task_subject: str) -> str:
    """
    Return a stable occupant-identity hash for the marker key.

    The "occupant" of a task is (teammate_name, task_subject). Keying the
    marker on the occupant — rather than on task_id alone — is the #887 fix:

    - A reused task_id under a DIFFERENT occupant → different key → the new
      occupant's HANDOFF is NOT falsely suppressed by a stale marker left by
      a prior occupant of the same task_id (the team-name-reuse collision).
    - The SAME occupant across re-fires / sessions → same key → the deliberate
      standing-task fire-once-across-lifespan dedup is preserved (see the
      _marker_dir docstring): a secretary standing task that spans sessions
      still emits its agent_handoff exactly once.

    A subject that is re-scoped mid-lifespan changes the key → one extra emit.
    This biases to HANDOFF preservation, never loss — the intended trade-off.

    Stable across processes: hashlib, NOT the builtin hash(). CPython salts
    str hashing with PYTHONHASHSEED, so builtin hash() would differ across
    processes/fires and break the cross-process O_EXCL dedup. The hex digest
    is path-safe by construction (no separators / traversal fragments).
    """
    payload = f"{teammate_name}\x00{task_subject}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_OCCUPANT_HASH_LEN]


def _marker_dir(
    team_name: str, *, namespace: str = _DEFAULT_MARKER_NAMESPACE
) -> Path:
    """
    Return the per-team marker directory path for one event-family namespace.

    Lives under ~/.claude/teams/{team}/{namespace}/ (default
    .agent_handoff_emitted) — a sibling to the team's inboxes/ and
    config.json. session_end.py's team reaper removes the whole team
    directory (shutil.rmtree), so every namespace's marker dir is cleaned
    up automatically when the team ages out.

    Kept task-scoped (not session-scoped) so fire-once semantics survive
    pause/resume: a secretary standing task that spans sessions must emit
    its agent_handoff event exactly once across the whole team lifespan.
    """
    return get_claude_config_dir() / "teams" / team_name / namespace


def _resolve_marker_target(
    team_name: str,
    task_id: str,
    occupant: str,
    *,
    namespace: str = _DEFAULT_MARKER_NAMESPACE,
    root_dir: "str | Path | None" = None,
) -> "tuple[int | None, str | None]":
    """
    Sanitize + validate + pin the marker directory; return the open directory
    descriptor and the marker filename for the O_EXCL test-and-set.

    SSOT for the marker path derivation (#901): BOTH already_emitted() (the
    optimistic CLAIM) and unclaim() (the compensating ROLLBACK) resolve the
    marker target through THIS one function, so the claim and the rollback can
    never reference divergent paths. Divergent path-reconstruction across two
    sites is the #877/#878 parallel-path-rot class — a single derivation makes
    alignment structural rather than a convention two sites must each remember.

    root_dir (keyword-only, default None) is a caller-owned root override:
    when provided, team_name is IGNORED (the caller owns root policy) and the
    marker dir becomes {root_dir}/{namespace} with root_dir itself as the
    containment base. A falsy or non-absolute root_dir is treated as "no
    valid target" (same fail-open signal as a degenerate team). The default
    None keeps the team-scoped derivation byte-identical to the pre-override
    behavior. Only the (marker_dir, containment base) selection branches —
    the guard/pin flow below is single-copy for both roots, so the override
    cannot diverge from the team path's symlink/TOCTOU/dir_fd hazards.

    Returns (dir_fd, filename) on success — the caller MUST os.close(dir_fd).
    Returns (None, None) on any degenerate-key / symlink / containment /
    resolution failure; the caller fail-opens (already_emitted → emit;
    unclaim → no-op).

    The returned dir_fd pins marker_dir by descriptor (opened with
    O_DIRECTORY|O_NOFOLLOW) so the caller's create/unlink, performed RELATIVE
    to the fd, cannot be redirected through a symlinked directory swapped in
    after this resolution.
    """
    task_id = sanitize_path_component(task_id)
    occupant = sanitize_path_component(occupant)

    # Degenerate post-sanitization values collapse the marker path onto an
    # existing directory (see the team guard below for the dir-collapse
    # shape). For the filename, the composite key f"{task_id}-{occupant}" no
    # longer collapses on a degenerate task_id alone ("." → ".-<hash>", a
    # normal file) — but the task_id guard is retained for defense +
    # behavioral parity with the pre-occupant key, and a missing occupant
    # (only possible if a caller bypasses occupant_hash()) is treated as "no
    # valid key". These key guards apply under EITHER root. In every
    # degenerate case: signal "no valid target" so the caller fail-opens
    # (already_emitted emits rather than suppresses; unclaim no-ops).
    if not task_id or task_id in (".", "..") or not occupant:
        return None, None

    if root_dir is None:
        # Centralized normalization (SSOT): case-fold + sanitize team_name
        # HERE so every caller derives a BYTE-IDENTICAL marker dir regardless
        # of what it passed in — b1 (agent_handoff_emitter) pre-lowercases
        # for its read_task_json path, b2 (task_lifecycle_gate) does not;
        # folding .lower() in here makes the two structurally identical and
        # closes the dormant case-drift (the #887 divergence shape one level
        # up). Idempotent w.r.t. b1's external .lower(). The normalized value
        # flows into BOTH _marker_dir() and the TOCTOU containment base,
        # keeping the containment check consistent.
        #
        # Degenerate team values collapse the marker path onto an existing
        # directory:
        #   _marker_dir(".")  → .../teams/./.agent_handoff_emitted
        #   _marker_dir("..") → .../teams/../.agent_handoff_emitted
        team_name = sanitize_path_component(team_name.lower())
        if not team_name or team_name in (".", ".."):
            return None, None
        marker_dir = _marker_dir(team_name, namespace=namespace)
        base = get_claude_config_dir() / "teams" / team_name
    else:
        # Caller-owned root: team_name is ignored (root policy lives with
        # the caller). A falsy or relative root cannot anchor a containment
        # check → no valid target, fail-open.
        if not root_dir or not Path(root_dir).is_absolute():
            return None, None
        base = Path(root_dir)
        # Pre-create the base itself at 0o700 when absent: the shared mkdir
        # below applies mode= to the FINAL component (the namespace dir)
        # only, so parents it creates get umask-default permissions — a
        # claim that materializes a missing base (e.g. a session dir the
        # journal writer has not created yet) would otherwise leave it
        # looser than every other creator, which mkdirs the base as its own
        # 0o700 leaf. exist_ok keeps an already-present base's mode
        # untouched; on failure, refuse the target (fail-open emit),
        # consistent with the shared mkdir's OSError posture below.
        try:
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return None, None
        marker_dir = base / namespace

    # Symlink-containment pre-check: if marker_dir already exists as a
    # symlink, refuse to use it (a pre-planted symlink could redirect marker
    # creation outside the containment base). Fail-open emit rather than risk
    # writing to an attacker-controlled location.
    if marker_dir.is_symlink():
        return None, None
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        # Directory creation failed; fall back to fail-open (emit).
        return None, None

    # TOCTOU containment re-check (closes the window between the is_symlink()
    # pre-check above and this mkdir): a symlink race could swap marker_dir
    # to a directory OUTSIDE the containment base between the two operations,
    # and mkdir(exist_ok=True) silently follows an existing symlink.
    # Re-resolve both paths and verify marker_dir is still contained within
    # the base. commonpath (not str.startswith — defeats the /teams/foo vs
    # /teams/foobar prefix-collision) is the robust containment test; chosen
    # over Path.is_relative_to because pyproject pins requires-python >=3.7
    # and is_relative_to is 3.9+. On breach OR any resolution error: fail-open
    # emit (return None) WITHOUT writing a marker at the escaped path —
    # consistent with the is_symlink() pre-check's fail-open posture.
    try:
        real_marker = os.path.realpath(marker_dir)
        real_base = os.path.realpath(base)
        if os.path.commonpath([real_marker, real_base]) != real_base:
            return None, None
    except (OSError, ValueError):
        return None, None

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    # Intermediate-dir TOCTOU close-out. O_NOFOLLOW on the marker FILE guards
    # only the FINAL path component — between the containment re-check above
    # and the create, marker_dir itself could be swapped to a symlink, and a
    # full-path os.open would then write THROUGH the symlinked DIRECTORY.
    # Defense: pin marker_dir as a dir_fd opened with O_DIRECTORY|O_NOFOLLOW
    # (a symlinked marker_dir makes THIS open fail → fail-open), then create
    # the marker RELATIVE to that pinned fd so the directory identity is held
    # by the descriptor, not re-resolved by path. The file open keeps
    # O_CREAT|O_EXCL|O_NOFOLLOW for the final-component guard + atomic
    # test-and-set. getattr fallbacks to 0 graceful-degrade where the dir
    # flags are absent (the relative-to-dir_fd create still pins the dir);
    # assumes POSIX openat (os.open dir_fd=), consistent with the existing
    # O_NOFOLLOW reliance.
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    try:
        dir_fd = os.open(str(marker_dir), dir_flags)
    except OSError:
        # Symlinked / vanished marker_dir → fail-open emit, no marker write.
        return None, None
    return dir_fd, f"{task_id}-{occupant}"


def already_emitted(
    team_name: str,
    task_id: str,
    occupant: str,
    *,
    namespace: str = _DEFAULT_MARKER_NAMESPACE,
    root_dir: "str | Path | None" = None,
) -> bool:
    """
    Test-and-set the per-(team, task_id, occupant) marker.

    root_dir is a caller-policy root override (see _resolve_marker_target),
    resolved through the same SSOT as unclaim() so the claim and the rollback
    can never diverge.

    Returns True iff a prior fire for the same key already created the marker
    (caller should suppress the journal write). Returns False on fresh fires —
    the marker is created as a side-effect of this call, making the
    test-and-set atomic at the kernel level (O_CREAT | O_EXCL).

    Marker filename: f"{task_id}-{occupant}" (occupant-identity keyed, #887).

    Inputs are sanitized internally (idempotent for already-clean callers like
    the emitter, which pre-sanitizes task_id/team_name for its read_task_json
    path) so a caller that has NOT pre-sanitized (the #869 lead-side gate)
    cannot drive raw traversal fragments into the marker join. The
    sanitization + containment + dir_fd pinning is shared with unclaim() via
    _resolve_marker_target() so the CLAIM and the compensating ROLLBACK can
    never diverge (#901).

    Fail-open: on any OSError other than EEXIST (permission denied, ENOSPC,
    filesystem race), returns False so the caller emits the event anyway.
    Data-integrity (preserving the HANDOFF in the journal) outweighs
    duplication-prevention when the marker subsystem itself breaks; worst case
    the caller falls back to per-fire emission for this one task.

    Graceful-degrade caveat: a pre-existing non-symlink file at the marker
    path (manually created, or stale state surviving an unclean cleanup) also
    returns True via EEXIST and suppresses emission permanently for that key —
    the O_EXCL test cannot distinguish "prior fire owns it" from "external
    file was placed here." Acceptable trade-off versus read-the-file-to-verify
    (which races and complicates the atomic test-and-set). The #901
    compensating-unclaim closes the OTHER suppression source (a marker this
    process claimed but whose journal write then failed) — see unclaim().
    """
    dir_fd, filename = _resolve_marker_target(
        team_name, task_id, occupant, namespace=namespace, root_dir=root_dir
    )
    if dir_fd is None:
        # Degenerate key / symlink-guarded / unresolvable → fail-open emit.
        return False
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=dir_fd,
        )
        os.close(fd)
        return False  # we created it; proceed with emit
    except OSError as e:
        if e.errno == errno.EEXIST:
            return True  # prior fire owns the marker; suppress
        return False  # any other error (incl. ELOOP) → fail-open, emit anyway
    finally:
        os.close(dir_fd)


def unclaim(
    team_name: str,
    task_id: str,
    occupant: str,
    *,
    namespace: str = _DEFAULT_MARKER_NAMESPACE,
    root_dir: "str | Path | None" = None,
) -> None:
    """
    Compensating rollback (#901, R1) — remove the marker THIS process just
    created when the subsequent journal write FAILED (append_event returned
    False or raised). Without it, the optimistic O_EXCL claim is poisoned:
    the marker exists but no journal entry was written, so already_emitted()
    suppresses every later fire for the key forever (the silent-permanent-loss
    residual the writability gate only narrowed, not closed).

    root_dir is a caller-policy root override (see _resolve_marker_target),
    resolved through the same SSOT as already_emitted() so the rollback can
    never target a root that diverges from the claim.

    Caller contract: invoke ONLY when this process OWNS the marker — i.e.
    already_emitted() returned False on THIS fire (a fresh O_EXCL create).
    Calling it after a True (prior-fire-owns) result could remove a marker a
    different fire legitimately owns; the b1/b2 call sites honor this by
    unclaiming exclusively on the not-already-emitted → write-failed path.

    Resolves the marker target through the SAME _resolve_marker_target() SSOT
    as already_emitted(), so the unlink can never target a path that diverges
    from the claim, and removes the marker RELATIVE to the pinned dir_fd
    (mirrors the create's symlink-safety: directory identity held by the
    descriptor, not re-resolved by path).

    Fail-SAFE: any error (including the marker already gone) is swallowed.
    Worst case the marker persists and behavior reverts to today's (a poisoned
    marker) — strictly no worse than not having the rollback. Never raises.
    """
    dir_fd, filename = _resolve_marker_target(
        team_name, task_id, occupant, namespace=namespace, root_dir=root_dir
    )
    if dir_fd is None:
        return
    try:
        os.unlink(filename, dir_fd=dir_fd)
    except OSError:
        # Marker already removed, vanished dir, or permission — best-effort.
        pass
    finally:
        os.close(dir_fd)
