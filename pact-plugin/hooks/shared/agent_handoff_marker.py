#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/agent_handoff_marker.py
Summary: SSOT for agent_handoff emission — the shape-agnostic emit-eligibility
         atom (is_signal_task) + occupant-identity marker key derivation +
         the O_EXCL test-and-set. Imported by BOTH agent_handoff emit paths
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

import errno
import hashlib
import os
import re
from pathlib import Path

# Hex chars of the SHA-256 digest retained for the occupant component of the
# marker key. 16 hex chars = 64 bits — collision-negligible for the tiny
# per-(team, task_id) occupant namespace (a handful of occupants per task at
# most), while keeping the marker filename short.
_OCCUPANT_HASH_LEN = 16

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


def _marker_dir(team_name: str) -> Path:
    """
    Return the per-team marker directory path.

    Lives under ~/.claude/teams/{team}/.agent_handoff_emitted/ — a sibling
    to the team's inboxes/ and config.json. session_end.py's team reaper
    removes the whole team directory (shutil.rmtree), so the marker dir is
    cleaned up automatically when the team ages out.

    Kept task-scoped (not session-scoped) so fire-once semantics survive
    pause/resume: a secretary standing task that spans sessions must emit
    its agent_handoff event exactly once across the whole team lifespan.
    """
    return Path.home() / ".claude" / "teams" / team_name / ".agent_handoff_emitted"


def already_emitted(team_name: str, task_id: str, occupant: str) -> bool:
    """
    Test-and-set the per-(team, task_id, occupant) marker.

    Returns True iff a prior fire for the same key already created the marker
    (caller should suppress the journal write). Returns False on fresh fires —
    the marker is created as a side-effect of this call, making the
    test-and-set atomic at the kernel level (O_CREAT | O_EXCL).

    Marker filename: f"{task_id}-{occupant}" (occupant-identity keyed, #887).

    Inputs are sanitized internally (idempotent for already-clean callers like
    the emitter, which pre-sanitizes task_id/team_name for its read_task_json
    path) so a caller that has NOT pre-sanitized (the #869 lead-side gate)
    cannot drive raw traversal fragments into the marker join.

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
    (which races and complicates the atomic test-and-set).
    """
    # Centralized normalization (SSOT): case-fold + sanitize team_name HERE so
    # every caller derives a BYTE-IDENTICAL marker dir regardless of what it
    # passed in — b1 (agent_handoff_emitter) pre-lowercases for its
    # read_task_json path, b2 (task_lifecycle_gate) does not; folding .lower()
    # in here makes the two structurally identical and closes the dormant
    # case-drift (the #887 divergence shape one level up). Idempotent w.r.t.
    # b1's external .lower(). The normalized value flows into BOTH _marker_dir()
    # and the TOCTOU team_base below, keeping the containment check consistent.
    team_name = sanitize_path_component(team_name.lower())
    task_id = sanitize_path_component(task_id)
    occupant = sanitize_path_component(occupant)

    # Degenerate post-sanitization values collapse the marker path onto an
    # existing directory:
    #   _marker_dir(".")  → .../teams/./.agent_handoff_emitted
    #   _marker_dir("..") → .../teams/../.agent_handoff_emitted
    # For the filename, the composite key f"{task_id}-{occupant}" no longer
    # collapses on a degenerate task_id alone ("." → ".-<hash>", a normal
    # file) — but the task_id guard is retained for defense + behavioral
    # parity with the pre-occupant key, and a missing occupant (only possible
    # if a caller bypasses occupant_hash()) is treated as "no valid key".
    # In every degenerate case: emit rather than suppress (accept the rare
    # duplication risk over silent event loss).
    if (
        not team_name
        or team_name in (".", "..")
        or not task_id
        or task_id in (".", "..")
        or not occupant
    ):
        return False

    marker_dir = _marker_dir(team_name)
    # Symlink-containment pre-check: if marker_dir already exists as a
    # symlink, refuse to use it (a pre-planted symlink could redirect marker
    # creation outside the team directory). Fail-open emit rather than risk
    # writing to an attacker-controlled location.
    if marker_dir.is_symlink():
        return False
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        # Directory creation failed; fall back to fail-open (emit).
        return False

    # TOCTOU containment re-check (closes the window between the is_symlink()
    # pre-check above and this mkdir): a symlink race could swap marker_dir
    # to a directory OUTSIDE the team base between the two operations, and
    # mkdir(exist_ok=True) silently follows an existing symlink. Re-resolve
    # both paths and verify marker_dir is still contained within the team
    # base. commonpath (not str.startswith — defeats the /teams/foo vs
    # /teams/foobar prefix-collision) is the robust containment test; chosen
    # over Path.is_relative_to because pyproject pins requires-python >=3.7
    # and is_relative_to is 3.9+. On breach OR any resolution error: fail-open
    # emit (return False) WITHOUT writing a marker at the escaped path —
    # consistent with the is_symlink() pre-check's fail-open posture.
    team_base = Path.home() / ".claude" / "teams" / team_name
    try:
        real_marker = os.path.realpath(marker_dir)
        real_base = os.path.realpath(team_base)
        if os.path.commonpath([real_marker, real_base]) != real_base:
            return False
    except (OSError, ValueError):
        return False

    marker_path = marker_dir / f"{task_id}-{occupant}"
    # O_NOFOLLOW defends against a pre-planted symlink at the marker path.
    # POSIX O_CREAT|O_EXCL already refuses to follow a trailing symlink;
    # O_NOFOLLOW is defense-in-depth against any future flag-combination
    # divergence and against intermediate-symlink variants. getattr
    # graceful-degrades on platforms that lack the flag.
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(
            str(marker_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
        )
        os.close(fd)
        return False  # we created it; proceed with emit
    except OSError as e:
        if e.errno == errno.EEXIST:
            return True  # prior fire owns the marker; suppress
        return False  # any other error (incl. ELOOP) → fail-open, emit anyway
