#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/agent_handoff_marker.py
Summary: SSOT for agent_handoff emission — the shape-agnostic emit-eligibility
         atom (is_signal_task) + occupant-identity marker key derivation +
         the handoff CONTENT-key derivation (handoff_content_key) + the
         composite claim/rollback twins (handoff_already_emitted,
         handoff_unclaim) +
         the O_EXCL test-and-set (already_emitted) + the compensating-unclaim
         rollback (unclaim, #901) that removes a marker whose journal write
         failed, both resolving the marker path via one SSOT derivation.
         It also carries the reader-side reconciliation helpers the harvest
         skill calls instead of keeping a second prose copy: the emit-side
         subject sentinel (emit_side_subject), the harvest's disk identity
         (disk_identity), the task-file-preferred union
         (union_preferring_task_file), and the OLD-FORMAT classification and
         routing (mixed_old_format_event, old_format_destination).
         Imported by BOTH agent_handoff emit paths
         so they derive the SAME emit decision and the SAME marker key and
         dedup to one agent_handoff event for each distinct (team, task_id,
         occupant, handoff content):
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

Revised handoff → one extra emit, and this is a DELIBERATE CHANGE of an
earlier property. The marker filename is f"{task_id}-{occupant}-{content}",
where content comes from handoff_content_key. The occupant term STAYS,
because it is the guard against a task_id reused by a different occupant.
The content term is ADDED and is not a substitute for it. The sibling
snapshot family passes a content key through the `occupant` parameter and
keeps its occupant hash in an event field only, so a literal mirror of that
shape would DROP the reuse guard from this family.

WHAT THIS REPLACED: the key held no content term, so the first emit for an
occupant claimed the marker and each later REVISION of the same handoff was
suppressed for the lifespan of the team. A suppressed revision reached no
carrier and left no record that it had been suppressed, so a reader cannot
tell a suppressed revision from a revision nobody wrote. The two states
agree in bytes on the journal, which is why the repair acts at the WRITE.

FAIL DIRECTION, AND IT IS REASONED RATHER THAN MEASURED. If the two emit
paths ever observe DIFFERENT handoff mappings for one task, this key gives
TWO events where the occupant-only key gave one. That direction is chosen.
A duplicate resolves by latest timestamp and a suppression does not resolve
at all, and a suppressed emit leaves no record of what it would have
carried, so the journals cannot report how frequently the two disagree.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
from pathlib import Path
from typing import NamedTuple

from .canonical_json import canonical_bytes
from .handoff_schema import (
    HANDOFF_LEGACY_ALIASES,
    HANDOFF_REQUIRED_FIELDS,
    resolve_handoff_field,
)
from .paths import get_claude_config_dir

# Hex chars of the SHA-256 digest retained for the occupant component of the
# marker key. 16 hex chars = 64 bits — collision-negligible for the tiny
# per-(team, task_id) occupant namespace (a handful of occupants per task at
# most), while keeping the marker filename short.
_OCCUPANT_HASH_LEN = 16

# Hex chars of the SHA-256 digest retained for the CONTENT component of the
# marker key. 8 hex chars = 32 bits, the same width the sibling snapshot
# family uses for payload_hash8, and the two are kept equal deliberately:
# one width for one purpose across the two families that share this
# substrate. The namespace it discriminates is the distinct handoff contents
# of ONE (team, task_id, occupant), which is a handful of revisions at most.
_CONTENT_HASH_LEN = 8

# Content term substituted when the handoff cannot be serialized. NOT hex, so
# it cannot collide with a digest of any input. See handoff_content_key for
# why a total function is required here rather than a raise.
_UNSERIALIZABLE_CONTENT_KEY = "unserializable"

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

    PAIRS WITH — but is not identical to — the regex inside
    task_utils.read_task_json, so the status-read site and the marker-write
    site both strip path traversal. The patterns differ: this one is strictly
    STRICTER, additionally stripping C0 control characters (\x00-\x1f), which
    the read-side strip leaves intact. Read "paired", not "mirrored": the
    symmetry holds on the traversal characters only. Without this, an
    attacker-crafted task_id / team_name that happens to sanitize (in
    read_task_json) into a matching existing completed-task file could still
    carry raw "../" fragments into the marker-path join.

    Strips path-traversal primitives (`/`, `\\`, `..`) and C0 control
    characters (NUL, CR/LF, and the 0x00-0x1f range) at the producer
    boundary. Control-char stripping defends against log-injection and
    embedded-newline attacks on values that flow into filesystem paths.

    THIS IS A DENYLIST AND A DENYLIST OVER UNBOUNDED INPUT IS NOT A SECURITY
    BOUNDARY. What it does buy is stated below, and what it does NOT buy is
    stated with it, because the two are easy to conflate and the conflation
    ships a false assurance.

    WHAT IT DOES NOT BUY: completeness. MEASURED, each of these survives into
    the filename intact, and NONE of them traverses on this filesystem: the
    C1 control U+0085, the separators U+2028 and U+2029, U+2024 ONE DOT
    LEADER, the fullwidth solidus U+FF0F, and U+202E RIGHT-TO-LEFT OVERRIDE.
    The single consequence measured for any of them is a filename that
    RENDERS deceptively in a terminal listing. That is cosmetic. Traversal
    itself is refused, measured across 8 arms with zero escapes, and the
    refusal comes from the containment re-check and the pinned dir_fd in
    _resolve_marker_target rather than from this character set. SO DO NOT
    EXTEND THIS SET AND CALL THE SANITIZER SAFE, and do not read a longer set
    as a stronger boundary.

    WHAT THE REAL DEFECT IS, AND IT GETS WORSE AS THE SET GROWS. THIS
    FUNCTION IS MANY-TO-ONE. MEASURED: 'a/b' and 'ab' both give 'ab', and
    'x..y' and 'xy' both give 'xy'. So two DIFFERENT task ids that sanitize
    alike produce ONE marker filename and COLLIDE, and the second one is
    suppressed as a duplicate of the first. Each character added to the strip
    set maps more inputs onto fewer outputs and WIDENS that collision. An
    escape (percent-encoding the removed bytes rather than deleting them)
    would be the construction that closes it, and that is a key-format change
    for the whole family rather than an edit here.

    🔴 IF YOU BUILD THAT ESCAPE, ENCODE THE ESCAPE CHARACTER ITSELF FIRST.
    The sentence above is incomplete without this clause, and an implementer
    who follows it word for word REPRODUCES the collision it closes. MEASURED,
    encoding '/' and '..' alone: 'a/b' gives 'a%2Fb', and the LITERAL INPUT
    'a%2Fb' also gives 'a%2Fb', so the two collide. 'x..y' collides with
    'x%2E%2Ey' the same way. Encode '%' as '%25' BEFORE the other characters
    and the pair separates: 'a/b' gives 'a%2Fb' while 'a%2Fb' gives
    'a%252Fb'. AN ESCAPE SCHEME THAT DOES NOT ESCAPE ITS OWN ESCAPE
    CHARACTER IS INJECTIVE IN APPEARANCE AND MANY-TO-ONE IN OPERATION,
    which is the property this whole paragraph is about.
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
    - The SAME occupant across re-fires / sessions → the same occupant term.

    A subject that is re-scoped mid-lifespan changes the key → one extra emit.
    This biases to HANDOFF preservation, never loss — the intended trade-off.

    THE FIRE-ONCE PROPERTY CHANGED AND THE CHANGE IS DELIBERATE. This
    docstring recorded a fire-once-across-lifespan dedup: one occupant gave
    one agent_handoff event for the full team lifespan, so a standing task
    that spans sessions emitted once. The marker key now composes this
    occupant term with a CONTENT term (handoff_content_key), so that task
    emits one event for each DISTINCT handoff content, and a reader resolves
    the group by latest timestamp. That is the intent of the change: a
    post-completion HANDOFF revision was suppressed and left no record.

    THIS FUNCTION IS UNCHANGED. The property named above belongs to the KEY
    that uses this hash, and not to the hash. The occupant term is still the
    task-id-reuse guard and it is still derived here alone.

    Stable across processes: hashlib, NOT the builtin hash(). CPython salts
    str hashing with PYTHONHASHSEED, so builtin hash() would differ across
    processes/fires and break the cross-process O_EXCL dedup. The hex digest
    is path-safe by construction (no separators / traversal fragments).
    """
    payload = f"{teammate_name}\x00{task_subject}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_OCCUPANT_HASH_LEN]


def handoff_content_key(handoff: object) -> str:
    """Return the CONTENT term of the agent_handoff marker key.

    A sha256 prefix of the canonical bytes of the handoff mapping, at the
    same width the sibling snapshot family uses for payload_hash8. Two emits
    that carry the same handoff content derive the same term and dedup to one
    event. A REVISED handoff derives a different term and emits again.

    Serialization comes from shared.canonical_json, which is also what the
    snapshot family hashes through. A second json.dumps here would be two
    serializations that must agree with nothing that makes them agree, which
    is the divergence class this module exists to prevent one layer up.

    canonical_bytes sorts keys, and that property is load-bearing HERE rather
    than merely convenient: b1 reads the handoff from the task file and b2
    receives it from a caller, so a term that moved with insertion order
    would split the two paths on content they agree about, and every such
    split costs a duplicate event.

    HASH THE VALUE YOU ARE ABOUT TO WRITE. Callers must pass the SAME mapping
    they hand to make_event, and never a re-read of disk. That is what makes
    the term a function of the emitted event rather than of a disk state
    observed at a different moment.

    TOTAL, AND THE HANDLER IS WIDE BECAUSE A NARROW ONE WAS NOT TOTAL.
    The fail-open direction is deliberate: canonical_bytes raises on a value
    it cannot serialize, and BOTH emit paths wrap their whole body in
    `except Exception: pass`, so a raise that escapes THIS function makes the
    event SILENTLY VANISH from the journal, which is the loss direction this
    module is biased against. A value that cannot serialize therefore falls
    back to a constant term: that task reverts to the occupant-only dedup
    this term otherwise extends, which emits the first fire rather than
    dropping it, and is no worse than the behavior before the content term
    existed.

    THE HANDLER CATCHES Exception, NOT A NAMED PAIR, AND THAT WIDTH IS THE
    WHOLE POINT. A `(TypeError, ValueError)` handler stood here and was NOT
    total. MEASURED: a deeply nested mapping makes canonical_bytes raise
    RecursionError, which inherits RuntimeError, so it escaped that pair,
    reached the emit path's bare handler, and the event vanished with no
    record. The door is wider than the room: json.loads PARSES a deeply
    nested object without error, so such a value ARRIVES through the
    JSON-parsed metadata both emit paths receive and fails only at the hash.
    THE DEPTH THAT REACHES THE RAISE CARRIES THREE PARAMETERS, AND THE
    INTERPRETER VERSION IS NOT ONE OF THEM. MEASURED BY BISECTION: the
    THREAD STACK is the axis (about 65000 deep on the main thread at its
    default stack, about 1000 to 2500 deep on a 512 KiB thread stack), and
    sys.getrecursionlimit() predicts none of those. The CONTAINER TYPE is
    the third: a 100000-deep MAPPING raises where a 100000-deep LIST does
    not. So do not read this raise as reachable only by an absurd payload.
    On a 512 KiB stack about 6 KiB of JSON reaches it, which is ordinary.
    So do NOT narrow this handler back to a named set. A serialization
    failure this function does not absorb is a silent journal loss, in a
    module of which the purpose is that a loss must be MARKED and not silent.
    """
    try:
        canonical = canonical_bytes(handoff)
    except Exception:
        return _UNSERIALIZABLE_CONTENT_KEY
    return hashlib.sha256(canonical).hexdigest()[:_CONTENT_HASH_LEN]


def emit_side_subject(subject: object) -> str:
    """Both emit paths' sentinel substitution. "" / whitespace -> "(no subject)".

    The EXECUTABLE copy of the substitution both emit paths apply BEFORE they
    hash: agent_handoff_emitter branches on the same `subject and
    str(subject).strip()` test, and task_lifecycle_gate replaces an empty or
    whitespace-only subject the same way. The harvest's disk identity must
    hash the SUBSTITUTED value, because that is what the emit hashed; without
    this mirror, a substituted task's identity can never equal its group and
    the union does not run on the tasks it exists to protect.

    The value is returned UNCHANGED when present (not str-coerced), matching
    both emit paths: occupant_hash stringifies it in its own f-string.
    """
    return subject if (subject and str(subject).strip()) else "(no subject)"


def disk_identity(owner: str, subject: object) -> str:
    """occupant_hash(owner, emit_side_subject(subject)) — the harvest's disk identity.

    The harvest computes a task file's group identity from the RECORD
    (owner, subject), while the emit computed it from the value it had at emit
    time. Routing both through this one function is what keeps the two in
    agreement on the sentinel substitution.
    """
    return occupant_hash(owner, emit_side_subject(subject))


# The values that count as EMPTY for the union below. Test MEMBERSHIP, not
# truthiness: `not disk_value` is also TRUE for `0` and for `False`, so a
# task-file field that deliberately holds one of those would keep the journal
# value and discard the disk value. KEEP `None` IN THIS SET. Drop it and the
# narrowing itself opens the erasure it was written to close: a field
# explicitly holding null would count as CONTENT and overwrite the journal
# copy with null.
_EMPTY_VALUES = (None, "", [], {})

_REQUIRED = frozenset(HANDOFF_REQUIRED_FIELDS)

# Longest raw value allowed into a report line. Reports name a task id and a
# field name; neither is legitimately long, and an unbounded value is a
# line-swallowing vector.
_REPORT_VALUE_MAX = 128


def report_safe(value: object, limit: int = _REPORT_VALUE_MAX) -> str:
    """Make a value safe to interpolate into a ONE-LINE report (CWE-117).

    Report lines are read by an agent that may act on them, and field names
    come from the task file and the append-only journal — both outside this
    module's control. A value would forge or split a report line at ANY
    boundary the consumer treats as a line break, so this strips BOTH sets:
      - C0 controls and DEL (\\x00-\\x1f, \\x7f), which covers CR and LF; and
      - every boundary `str.splitlines()` recognises that C0 does not cover:
        NEL U+0085, LINE SEPARATOR U+2028 and PARAGRAPH SEPARATOR U+2029.
    An ASCII-only class leaves the last three, and a renderer that splits on
    `splitlines()` — Python's own, and many log consumers — would still see a
    forged line. Removing them via `splitlines()` rather than a third
    hand-written class keeps the set correct if Python's notion widens. The
    result is length-capped so one value cannot swallow the line.
    """
    without_controls = re.sub(r"[\x00-\x1f\x7f]", "", str(value))
    return "".join(without_controls.splitlines())[:limit]


def _identity(raw_key: str) -> str:
    """Canonical identity of one raw key.

    NORMALIZATION RUNS BEFORE ANY SET MATH. `key_decisions` is the legacy
    spelling of `decisions`; a required-set comparison over RAW keys would
    leave a renamed field outside the required set and classify a complete
    revision as a partial one, reviving the field the revision cleared.
    Measured: the rename/add fixture routes correctly only in this order.
    """
    return HANDOFF_LEGACY_ALIASES.get(raw_key, raw_key)


def _field_map(handoff: dict) -> dict:
    """canonical identity -> (raw key, value) for one copy.

    If a copy carries BOTH spellings of a field, the canonical spelling wins,
    matching resolve_handoff_field's canonical-first, alias-fallback order.
    """
    fields: dict[str, tuple[str, object]] = {}
    for raw_key, value in handoff.items():
        identity = _identity(raw_key)
        current = fields.get(identity)
        if current is None or raw_key == identity:
            fields[identity] = (raw_key, value)
    return fields


def _required_set(fields: dict) -> set:
    """The REQUIRED canonical fields one copy carries.

    Scoped to HANDOFF_REQUIRED_FIELDS, never to every key: `reasoning_chain`
    is recommended, so a legal revision may omit it, and journal copies carry
    non-canonical extras. A full-key comparison calls both cases partial and
    restores the cleared field.
    """
    return {identity for identity in fields if identity in _REQUIRED}


def union_preferring_task_file(
    journal_handoff: dict, disk_handoff: dict, task_id: str
) -> tuple[dict, list[str]]:
    """Merge the two HANDOFF copies by their REQUIRED key sets.

    The task file wins each conflict, apart from the two states below. Both
    states are decided by the two copies' REQUIRED key sets, both are
    reported with one line per field, and neither is decided by an empty or
    missing value alone. Keep each field that is in one copy alone, except a
    REQUIRED field the task file lacks in the incomparable state, which is
    dropped and reported.

    Let `J` be the journal copy's required key set and `D` the task file's.
    EQUALITY COUNTS AS COVERING: 130 of 130 observed local pairs are equal,
    and a strict-superset test misroutes every one.

    State A — field PRESENT AND EMPTY on the task file, content in the
    journal:
      - D covers J (equality included) -> keep the EMPTY value.
      - D is a proper subset of J     -> keep the JOURNAL value.
      - incomparable                  -> keep the EMPTY value.

    State B — field PRESENT IN THE JOURNAL and ABSENT from the task file:
      - REQUIRED, D a proper subset of J -> keep the journal value.
      - REQUIRED, incomparable           -> DROP it (the only branch that
                                            drops journal content).
      - NOT required                     -> keep it (union default).

    THE RESIDUAL: a deliberate complete removal of a REQUIRED key and an
    accidental partial re-write both leave a proper subset, so the
    proper-subset branch restores in both and reports that it did. Key sets
    cannot separate them; the report is what makes the restore auditable.

    Pure; no I/O; returns a fresh dict and one report line per divergent
    field. Both inputs are left unmutated.
    """
    merged = dict(journal_handoff)
    reports: list[str] = []
    report_task = report_safe(task_id)

    journal_fields = _field_map(journal_handoff)
    disk_fields = _field_map(disk_handoff)
    j_req = _required_set(journal_fields)
    d_req = _required_set(disk_fields)

    def _drop_journal_identity(identity: str) -> None:
        """Remove EVERY journal raw key whose canonical identity is `identity`.

        A journal copy may carry BOTH the canonical and an alias spelling of
        one field. `_field_map` collapses them for the set math, but `merged`
        starts as a copy of the RAW journal mapping, so dropping only the one
        spelling `_field_map` selected would leave the other in the output and
        defeat the decision below: a reader resolving aliases still finds the
        value that was just decided against.
        """
        for raw_key in journal_handoff:
            if _identity(raw_key) == identity:
                merged.pop(raw_key, None)

    def _take_disk(identity: str, disk_value: object) -> None:
        disk_key, _ = disk_fields[identity]
        _drop_journal_identity(identity)
        merged[disk_key] = disk_value

    # State A: every field the task file carries.
    for identity, (disk_key, disk_value) in disk_fields.items():
        report_field = report_safe(identity)
        journal_present = identity in journal_fields
        journal_value = (
            resolve_handoff_field(journal_handoff, identity)
            if journal_present else None
        )
        if (
            disk_value in _EMPTY_VALUES
            and journal_present
            and journal_value
        ):
            if d_req >= j_req:  # covering, EQUALITY INCLUDED
                reports.append(
                    f"handoff field kept empty for task {report_task}: field "
                    f"{report_field} present and empty; the task-file "
                    f"required key set covers the journal copy"
                )
                _take_disk(identity, disk_value)
            elif d_req < j_req:  # proper subset
                reports.append(
                    f"handoff field restored from the journal for task "
                    f"{report_task}: field {report_field} present and empty; "
                    f"the task-file required key set is a proper subset of "
                    f"the journal copy"
                )
                # keep the journal value: merged already holds it
            else:  # incomparable
                reports.append(
                    f"handoff field undecidable for task {report_task}: "
                    f"field {report_field} present and empty; the required "
                    f"key sets are incomparable"
                )
                _take_disk(identity, disk_value)
        else:
            _take_disk(identity, disk_value)

    # State B: fields only the journal carries.
    for identity in journal_fields:
        if identity in disk_fields:
            continue
        report_field = report_safe(identity)
        if identity in _REQUIRED:
            if d_req < j_req:
                reports.append(
                    f"handoff field restored from the journal for task "
                    f"{report_task}: required field {report_field} absent "
                    f"from the task file; the task-file required key set is "
                    f"a proper subset of the journal copy"
                )
                # keep it
            else:  # incomparable; covering is impossible here
                reports.append(
                    f"handoff field undecidable for task {report_task}: "
                    f"required field {report_field} absent from the task "
                    f"file; the required key sets are incomparable"
                )
                _drop_journal_identity(identity)
        else:
            reports.append(
                f"handoff field kept from the journal for task {report_task}: "
                f"field {report_field} absent from the task file and not a "
                f"required field; the required key sets do not decide it"
            )
            # keep it: the union default for a non-required field

    return merged, reports


OLD_FORMAT_MIXED = "mixed"          # content is another store's; discard + report
OLD_FORMAT_OWNED = "owned_by"       # content AND identity match a named store
OLD_FORMAT_UNMATCHED = "unmatched"  # no named store carries the content


class OldFormatOutcome(NamedTuple):
    """Structured result of classifying one OLD-FORMAT journal event.

    `state` is one of the three OLD_FORMAT_* constants. `team` names the store
    that owns a MIXED event's content or an OWNED event outright, and is None
    for UNMATCHED. `report` is the skill's report line for a MIXED event and
    None otherwise.
    """

    state: str
    team: str | None = None
    report: str | None = None


def old_format_destination(outcome: OldFormatOutcome, this_team: str) -> str:
    """Where an OLD-FORMAT event belongs, given its classification.

    "mixed"  — discard it and report `outcome.report`.
    "from"   — it is `outcome.team`'s work unit: process it under that team's
               `## team={team}` section against that team's store, never
               unioned with this team's task file.
    "this"   — this team's work unit (OWNED_BY this team, or UNMATCHED).
               UNMATCHED carries `outcome.report`, the unavailable-check line
               the caller MUST emit; OWNED_BY(this_team) carries none.

    A caller that routes every non-MIXED outcome to this team misroutes a
    realigned earlier team's HANDOFF, which is the population the old-format
    rule exists to protect. Pure; never raises.
    """
    if outcome.state == OLD_FORMAT_MIXED:
        return "mixed"
    if outcome.state == OLD_FORMAT_OWNED and outcome.team != this_team:
        return "from"
    return "this"


def mixed_old_format_event(
    task_id: str,
    event_agent: object,
    event_subject: object,
    event_handoff: object,
    candidate_tasks: list[dict],
) -> OldFormatOutcome:
    """Classify an OLD-FORMAT journal event against the stores whose NAMES
    the harvest holds.

    An OLD-FORMAT event carries no `team` field (every event written before
    that field existed), so its `(agent, task_subject)` identity alone cannot
    prove it belongs to this team. `candidate_tasks` are the PARSED task files
    of the stores whose names the harvest holds — this team's store and each
    `team_realigned` `from` store — as `{"team": <store name>, "task": <dict>}`.
    `task_id` is the id being resolved; it names the task in EVERY report, so
    the caller never has to compose one.

    Three outcomes, because the caller must ROUTE the event, not merely decide
    whether to discard it:
      - OLD_FORMAT_OWNED: one store's handoff content key equals the event's
        AND that store's `(owner, subject)` identity equals the event's
        `(agent, task_subject)` identity. The event is that store's work unit;
        `team` names it, and the caller routes a non-current team to its own
        section. Checked across ALL candidates BEFORE any MIXED report, so a
        later coincidental content match cannot override an identity match.
      - OLD_FORMAT_MIXED: a store's content key matches while its identity
        does not. The content came from a store whose identity the event does
        not carry; discard it and report `report`.
      - OLD_FORMAT_UNMATCHED: no named store carries the content. Keep the
        identity match as this team's, and report the unavailable check —
        a store no realignment event names is out of reach, and guessing is
        worse than reporting the bound. The report travels ON THE OUTCOME
        because this state is the COMMON path (every pre-field event whose
        content matches no named store), and a caller that had to invent the
        line would be free to choose silently instead.

    A non-dict / empty event handoff is UNMATCHED: there is no content to
    compare. Pure; no I/O; never raises.
    """
    unmatched = OldFormatOutcome(
        OLD_FORMAT_UNMATCHED,
        report=(
            f"handoff cross-team check unavailable for task "
            f"{report_safe(task_id)}: no other named store carries the "
            f"identity"
        ),
    )
    if not isinstance(event_handoff, dict) or not event_handoff:
        return unmatched
    event_content = handoff_content_key(event_handoff)
    event_identity = disk_identity(
        event_agent if isinstance(event_agent, str) else "", event_subject
    )
    content_matches: list[tuple[str, dict]] = []
    for candidate in candidate_tasks or ():
        if not isinstance(candidate, dict):
            continue
        team = candidate.get("team")
        task = candidate.get("task")
        if not isinstance(task, dict):
            continue
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            continue
        task_handoff = metadata.get("handoff")
        if not isinstance(task_handoff, dict) or not task_handoff:
            continue
        if handoff_content_key(task_handoff) != event_content:
            continue
        owner = task.get("owner")
        file_identity = disk_identity(
            owner if isinstance(owner, str) else "", task.get("subject")
        )
        if file_identity == event_identity:
            return OldFormatOutcome(OLD_FORMAT_OWNED, team=team)
        content_matches.append((team, task))
    if not content_matches:
        return unmatched
    team, _ = content_matches[0]
    report_task = report_safe(task_id)
    report_team = report_safe(team)
    return OldFormatOutcome(
        OLD_FORMAT_MIXED,
        team=team,
        report=(
            f"handoff identity mismatch for task {report_task}: journal "
            f"content equals team {report_team} task {report_task}, whose "
            f"identity it does not carry"
        ),
    )


def _handoff_marker_discriminator(occupant: str, content_key: str) -> str:
    """Compose the occupant term and the content term into one discriminator.

    SSOT for the composition, for the same cause _resolve_marker_target is
    the SSOT for the path: handoff_already_emitted (the CLAIM) and
    handoff_unclaim (the ROLLBACK) both derive their key HERE, so the two can
    never disagree about what was claimed. A composition open-coded at two
    sites is the parallel-path-rot class this module closed before.

    COMPOSES, and does not substitute. Dropping the occupant term here would
    regress the task-id-reuse collision that term exists to guard.
    """
    return f"{occupant}-{content_key}"


def handoff_already_emitted(
    team_name: str, task_id: str, occupant: str, content_key: str
) -> bool:
    """Test-and-set the agent_handoff marker on (occupant, content).

    Thin wrapper over already_emitted that HARD-BINDS the composition of the
    two key terms. The two emit paths call ONLY this wrapper and its
    handoff_unclaim twin, never the raw marker functions: a site that
    composed the key itself could claim under one discriminator and no-op
    unclaim against another, leaving a marker that suppresses every later
    fire for that key. The wrapper makes that impossible by construction.

    Returns True iff a prior fire for the same (team, task_id, occupant,
    handoff content) claimed the marker, so the caller suppresses its write.
    Fail-open posture and the O_EXCL contract are unchanged; see
    already_emitted.
    """
    return already_emitted(
        team_name,
        task_id,
        _handoff_marker_discriminator(occupant, content_key),
    )


def handoff_unclaim(
    team_name: str, task_id: str, occupant: str, content_key: str
) -> None:
    """Compensating rollback for a claim whose journal write failed.

    Hard-bound twin of handoff_already_emitted: same composition site, same
    resolver SSOT underneath, so the rollback can never target a key that
    diverges from the claim.

    Caller contract is inherited from unclaim: invoke ONLY when this process
    OWNS the marker, meaning handoff_already_emitted returned False on THIS
    fire.
    """
    unclaim(
        team_name,
        task_id,
        _handoff_marker_discriminator(occupant, content_key),
    )


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

    Kept task-scoped (not session-scoped) so the dedup survives pause/resume:
    a standing task that spans sessions emits one agent_handoff event for
    each DISTINCT handoff content across the full team lifespan, rather than
    one for each session. (This sentence recorded a fire-once-across-lifespan
    dedup before the marker key gained its content term; see the module
    docstring for the change and its cause.)
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
    # Coerce task_id to str BEFORE sanitizing. sanitize_path_component is a
    # bare re.sub, which RAISES TypeError on a non-str input. Marker callers
    # sit inside emit paths that wrap their whole body in
    # `except Exception: pass`, so such a raise is SWALLOWED: the emit is
    # skipped and the event silently vanishes from the journal instead of
    # failing loudly. A consumer counting those events then measures a
    # population smaller than the one that actually occurred, with no signal
    # that anything was lost. Normalising HERE — the single resolver that
    # BOTH already_emitted() and unclaim() route through — covers every
    # marker entry point by construction, so a claim and its compensating
    # rollback can never derive different keys from the same input.
    #
    # Deliberately NOT placed inside sanitize_path_component, and the
    # decisive reason is that doing so would buy NOTHING. Of the three
    # components this resolver sanitizes, occupant is a hex digest from
    # occupant_hash() (str by construction) and team_name reaches .lower()
    # BELOW, before any sanitize call — so a coercion inside the sanitizer is
    # unreachable for team_name and pointless for occupant. It would cover
    # exactly the one component covered here, while widening the change to
    # every other caller of that shared helper: identical coverage, wider
    # exposure.
    #
    # A second, independent reason: this sanitizer and the read-side strip in
    # task_utils.read_task_json BOTH currently reject a non-str (both are a
    # bare re.sub). Coercing only this one would leave the read side raising
    # on an input the write side had begun accepting. The two are NOT the
    # same pattern — this one is strictly stricter, also removing C0 controls
    # where the read side removes traversal characters only — so they are
    # paired on the reject-non-str behaviour, not mirrored generally. Settle
    # that by EXECUTING both patterns; neither docstring is evidence for it.
    #
    # Coercion inside a path sanitizer is also the fail-open direction for a
    # primitive whose whole job is constraining what reaches a filesystem
    # join.
    #
    # task_id ONLY — the other two were assessed, not skipped. A non-str
    # team_name still raises AttributeError at .lower(): a different
    # exception, from a different line, than the TypeError this coercion
    # prevents. Exactly ONE component is normalised here, not all three.
    task_id = sanitize_path_component(str(task_id))
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
    # /teams/foobar prefix-collision) is the robust containment test, and that
    # prefix-collision reason is the ONLY reason it is preferred here.
    # This was previously also justified as commonpath over Path.is_relative_to
    # "because pyproject pins requires-python >=3.7 and is_relative_to is 3.9+".
    # That argument is INVALID and is recorded here so it is not re-derived.
    # The declaration in pyproject.toml does not govern this. Nothing on the
    # install path consults requires-python, so the declaration is inert and
    # its value decides nothing here. The ENFORCED floor governs: 3.12 (the
    # CI cell that runs the suite, the py312 lint target, and the 3.12 AST
    # gate). The declaration names that same floor, which only strengthens
    # this. Note the floor is a SUPPORT BOUNDARY, not a runtime guard: hooks
    # launch on whatever bare python3 resolves to, and a host below 3.12 is
    # out of support rather than protected. is_relative_to (3.9+) is
    # available at the operative floor and rejects the prefix collision too,
    # so either primitive would be correct -- the choice is not
    # load-bearing and only the stated reason was wrong.
    # On breach OR any resolution error: fail-open emit (return None) WITHOUT
    # writing a marker at the escaped path — consistent with the is_symlink()
    # pre-check's fail-open posture.
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

    The `occupant` parameter is an OPAQUE sanitized string to this function:
    it is whatever discriminator the calling family derives. The agent_handoff
    family passes a COMPOSITE of its occupant term and its handoff content
    term, built by handoff_already_emitted / handoff_unclaim; the sibling
    snapshot family passes a content key alone; and the dispatch-site family
    passes a FIXED LITERAL, discriminating on neither term. Three families,
    three shapes — a reader counting the shapes this parameter takes will not
    find them all here as new families are added, so treat the list as the
    families that exist today and the sentence above as the contract. Callers
    in EVERY family MUST go through their own hard-bound wrapper pair rather
    than call this directly, so a claim and its rollback cannot derive
    different keys.

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

    THE FILENAME CEILING, AND IT IS RECORDED FOR A CHANGE NOBODY HAS MADE
    YET. ENAMETOOLONG is an OSError that is NOT EEXIST, so it takes the
    fail-open arm above. That arm is correct for a transient error and it is
    the wrong shape for a LENGTH: a length does not vary between fires, so
    this function returns False on EVERY fire for that key, and dedup is lost
    for it PERMANENTLY rather than degraded once. Each fire then appends a
    fresh journal event of up to PAYLOAD_CAP bytes.
    MEASURED, exact boundary, re-derived on this filesystem: NAME_MAX is 255.
    The handoff composite overhead is 26 characters (a hyphen, 16 hex of
    occupant, a hyphen, 8 hex of content), so a task_id of 229 characters
    creates and 230 gives ENAMETOOLONG.
    INERT TODAY, and that is the point of writing it down rather than acting
    on it: ordinary task ids are small integers and team names are about 16
    characters, so nothing approaches the ceiling. THE NUMBER 229 IS THE
    THING WORTH KEEPING, for a future change that makes task_id a UUID, a
    slug, or a composite. Such a change needs a length check here, and the
    check has to be a REFUSAL rather than a fail-open, because fail-open is
    what makes this permanent.

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
