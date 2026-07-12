#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/task_metadata_snapshot.py
Summary: SSOT substrate for task_metadata_snapshot journal emission — the
         GC-immune mirror of non-handoff task metadata (the N-key
         generalization of the single-key dispatch_variety / teachback_ack
         mirrors). Owns the exclude set, the dual-cap three-stage
         size-bounding, the content-key hash, the hard-bound marker-namespace
         wrappers, and the ONE emit routine shared verbatim by every seam.
Used by: task_lifecycle_gate.py (lead-completion seam + post-completion
         backstop seam), agent_handoff_emitter.py (teammate-frame
         TaskCompleted seam), and the substrate unit-test suite.

Why one substrate: the agent_handoff emit paths drifted-by-construction risk
(divergent marker keys across two files) was closed by centralizing the
marker atoms in shared/agent_handoff_marker.py. The snapshot inherits that
lesson structurally: all three seams call emit_task_metadata_snapshot() and
nothing snapshot-specific lives in the hooks beyond the thin hermetic call.

Eligibility (deliberately DIFFERENT from the agent_handoff chain — do not
share or wrap that predicate): non-empty post-SNAPSHOT_EXCLUDE payload AND
journal writable. No signal-task gate (blocker/algedonic HALT context is
peak durability value; the agent_handoff suppression's basis is reader
purity of THAT event type and does not transfer), no teachback-subject gate
(teachback_submit / rejection siblings are load-bearing), no owner
requirement (signal tasks may be ownerless).

Supersession: multiple snapshots per task are legal — a changed payload
after completion re-emits under a new content key; an unchanged payload
never re-emits (the content-keyed O_EXCL marker dedups across all seams).
Readers take latest-ts within the (task_id, occupant) group; the occupant
field is the task-id-reuse discriminator (platform reuses task ids across
arcs within one team).

Size-bounding invariant: key EXISTENCE is never silently lost — worst case
a key survives name-only in the payload's top-level "_dropped_keys" list.
Determinism contract: identical input mapping under ANY insertion order
produces byte-identical canonical payload bytes and therefore an identical
payload_hash8 (sizing, truncation heads, and hashing all flow through the
single _canonical_bytes serialization).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .agent_handoff_marker import (
    already_emitted,
    occupant_hash,
    sanitize_path_component,
    unclaim,
)
from .session_journal import append_event, get_journal_path, make_event

# Keys never mirrored: only entries with equivalent-or-better journal
# coverage of the FULL value. metadata.handoff is journaled verbatim as its
# own agent_handoff event; everything else (teachback_submit, variety incl.
# rationales, ad-hoc analysis keys, lifecycle flags) is mirrored — a missed
# key is silent institutional loss while a junk key is bounded bytes, so the
# exclude set stays minimal by design.
SNAPSHOT_EXCLUDE: frozenset[str] = frozenset({"handoff"})

# Keys whose WRITE (TaskCreate/TaskUpdate delta) triggers an immediate
# whole-payload mirror — the open-task-consumed class that a status-blind
# whole-store drain destroys while load-bearing (mid-arc readers consume
# these via TaskGet while the task is still open). The fire predicate is
# targeted-key-in-delta; the payload is always the full overlay (never a
# projection), so cross-seam content-hash dedup stays coherent with the
# completion-time seams. Disjoint from SNAPSHOT_EXCLUDE by test-pinned
# invariant. NOT here: "variety" (already per-write-mirrored at its write
# point by the dispatch_variety emit); "intentional_wait" (rides along in
# the overlay; a wait-only write is not load-bearing alone).
#
# The audit pair is targeted for the same reasons as teachback_submit:
# audit_summary (the auditor's live verdict) and audit_summary_authored
# (the overwrite-protection mirror of the authored verdict) are consumed
# by the lead at CODE-phase close while the audited task is still OPEN,
# so a status-blind whole-store drain destroys them exactly while
# load-bearing (a prior drain lost live audit verdicts mid-arc). Both are
# teammate-written-class keys (the auditor writes from a teammate frame),
# so they carry the same canonical-frame tmux asymmetry as
# teachback_submit: in-process frames mirror per-write; a tmux teammate
# frame defers to the completion seams. audit_summary_authored rides the
# overlay whenever audit_summary fires (the gate's own writeback lands
# before the leg's disk read), but stays targeted in its own right so a
# direct write of the mirror alone is durability-covered too.
PER_WRITE_MIRROR_KEYS: frozenset[str] = frozenset({
    "scope_contract",
    "nesting_depth",
    "worktree_path",
    "teachback_submit",
    "teachback_rejection",
    "audit_summary",
    "audit_summary_authored",
})

# Size caps on the canonical serialization (see _canonical_bytes). Empirics
# over the full journal/task-file population found the largest real value at
# ~10 KB and no journal event ever ≥ 32 KB, so both caps are anomaly paths:
# generous enough to never truncate observed real payloads, bounded enough
# to protect journal growth and the read path's tail-window scan.
PER_VALUE_CAP: int = 16 * 1024
PAYLOAD_CAP: int = 64 * 1024
HEAD_BYTES: int = 1024

# O_EXCL marker directory for snapshot dedup — a SEPARATE namespace from the
# agent_handoff marker dir so the two event families can never suppress each
# other. Module constant, never input-derived.
SNAPSHOT_MARKER_NAMESPACE: str = ".task_metadata_snapshot_emitted"

# Marker-dict key set used to recognize truncation markers this module
# itself produced (stage-2 candidate filtering + stage-3 head emptying).
_MARKER_KEYS = frozenset({"_truncated", "original_bytes", "head"})


def _canonical_bytes(value: object) -> bytes:
    """THE single serialization for sizing, truncation heads, and hashing.

    sort_keys makes the byte form insertion-order-independent, which is what
    grounds the determinism contract (identical mapping → identical hash).
    Raises TypeError on non-JSON-serializable input; callers on the emit
    path are hermetic, and task metadata is JSON-safe by construction
    (it arrives through TaskUpdate's JSON payload).
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _utf8_safe_head(data: bytes, limit: int) -> str:
    """Return the longest decodable prefix of ``data[:limit]`` as str.

    Cutting a byte string at an arbitrary offset can bisect a multibyte
    UTF-8 sequence; decoding the raw slice would then raise (or, with
    errors="replace", inject U+FFFD noise into the marker head). Backing
    off to the nearest character boundary keeps the head a clean str so
    the event line itself is always valid JSON (JSONL-poisoning guard).
    With the pinned _canonical_bytes (json.dumps default ensure_ascii)
    the canonical form is pure ASCII and the back-off is a no-op; the
    boundary guard is defense-in-depth should the serialization ever
    carry raw multibyte bytes.
    """
    head = data[:limit]
    for _ in range(4):  # a UTF-8 sequence is at most 4 bytes
        try:
            return head.decode("utf-8")
        except UnicodeDecodeError:
            head = head[:-1]
    return head.decode("utf-8", errors="ignore")


def _truncation_marker(canonical: bytes) -> dict:
    """Build the marker that replaces an over-cap value.

    ``head`` is a *string field containing* the first HEAD_BYTES of the
    value's canonical serialization (cut at a UTF-8 character boundary),
    so the marker — and therefore the whole event line — is always valid
    JSON regardless of what the original value held.
    """
    return {
        "_truncated": True,
        "original_bytes": len(canonical),
        "head": _utf8_safe_head(canonical, HEAD_BYTES),
    }


def _is_marker(value: object) -> bool:
    """True iff ``value`` has the truncation-marker SHAPE.

    Shape recognition is a TEST/reader oracle only — production marker
    identity inside build_snapshot_payload is provenance-tracked (the
    local set of keys that run truncated), never shape-tested, so a
    caller-supplied lookalike can never be treated as a marker.
    """
    return (
        isinstance(value, dict)
        and set(value.keys()) == set(_MARKER_KEYS)
        and value.get("_truncated") is True
    )


def build_snapshot_payload(
    task_metadata: Mapping[str, object],
) -> tuple[dict, bool]:
    """Return ``(payload, truncated)`` — the size-bounded mirror payload.

    READ-ONLY on input: never mutates ``task_metadata`` or any value inside
    it — a new dict is built, and no value object is ever written in place:
    stage-3a head emptying REBUILDS the marker (``{**marker, "head": ""}``)
    instead of assigning into it, so even a caller-supplied value that is
    already exactly marker-shaped (inserted by reference at stage 1) is
    never written through. This is load-bearing: at the seams the same
    metadata object feeds the handoff emit path, and mutating shared state
    from an "additive" pass is the known silent-regression class on this
    pipeline.

    Dual-cap three-stage semantics (all sizes = len(_canonical_bytes(x))):

    1. Per-value: each non-excluded value over PER_VALUE_CAP is replaced by
       the truncation marker (see _truncation_marker).
    2. Payload: while the whole payload exceeds PAYLOAD_CAP, the LARGEST
       remaining not-yet-truncated value is replaced by the marker; ties
       broken by ascending lexicographic key order (pinned). Markers count
       toward the total; keys this run already truncated are not
       re-candidates.
    3. Pathological floor: if the payload still exceeds the cap, set head
       to "" across this run's markers in descending original_bytes order
       (same tie-break); if STILL over, keep whole keys in ascending key
       order while they fit and record the remainder name-only in a
       top-level "_dropped_keys" list (ascending). Key existence is never
       silently lost.

    Marker identity is PROVENANCE-tracked, never shape-tested: a local set
    records which keys THIS run truncated, and stages 2/3 consult that set
    only. An input value that merely looks marker-shaped is ordinary
    caller data — a normal eviction candidate (replaced by OUR marker
    computed from its serialization) and never sorted by stage 3, whose
    original_bytes sort therefore only ever sees ints (total by
    construction).
    """
    payload: dict = {}
    truncated = False
    # Provenance set — the keys whose payload slot THIS run replaced with a
    # truncation marker. Marker identity is provenance-tracked, never
    # shape-tested: an input value that merely LOOKS marker-shaped is
    # ordinary caller data (a normal stage-2 eviction candidate, replaced
    # by OUR marker computed from ITS serialization), and stage 3a sorts
    # only keys in this set — whose markers carry int original_bytes by
    # construction, making the sort total (a caller lookalike's arbitrary
    # original_bytes value can never reach it).
    marker_keys: set[str] = set()

    # Stage 1 — per-value cap. Iteration over sorted keys makes the stage
    # order-independent by construction (the output would be equivalent
    # anyway — canonical bytes sort keys — but sorted iteration keeps every
    # intermediate deterministic too).
    for key in sorted(task_metadata):
        if key in SNAPSHOT_EXCLUDE:
            continue
        value = task_metadata[key]
        canonical = _canonical_bytes(value)
        if len(canonical) > PER_VALUE_CAP:
            payload[key] = _truncation_marker(canonical)
            marker_keys.add(key)
            truncated = True
        else:
            payload[key] = value

    # Stage 2 — payload cap: evict largest not-yet-truncated values first.
    # Candidacy is provenance-based (key not in marker_keys), so a caller
    # lookalike is evicted like any ordinary value.
    while len(_canonical_bytes(payload)) > PAYLOAD_CAP:
        candidates = {
            key: len(_canonical_bytes(value))
            for key, value in payload.items()
            if key not in marker_keys
        }
        if not candidates:
            break
        largest = max(candidates.values())
        key = min(k for k, size in candidates.items() if size == largest)
        payload[key] = _truncation_marker(_canonical_bytes(payload[key]))
        marker_keys.add(key)
        truncated = True

    # Stage 3a — empty this run's marker heads, biggest originals first.
    # Provenance-scoped: iterates marker_keys only, so every sorted
    # original_bytes is an int by construction and no caller object is ever
    # a target. REBUILD the marker rather than assigning into it — the
    # structural read-only guarantee (no in-place writes to any payload
    # value, regardless of provenance) is what keeps the caller's metadata
    # dict safe even if a future edit widens what reaches this loop; the
    # seams feed the same object to the handoff path.
    if len(_canonical_bytes(payload)) > PAYLOAD_CAP:
        by_size_desc = sorted(
            marker_keys,
            key=lambda k: (-payload[k]["original_bytes"], k),
        )
        for key in by_size_desc:
            if len(_canonical_bytes(payload)) <= PAYLOAD_CAP:
                break
            if payload[key]["head"]:
                payload[key] = {**payload[key], "head": ""}
                truncated = True

    # Stage 3b — name-only survival: greedily keep whole keys in ascending
    # order; everything else is recorded in _dropped_keys. The trial size
    # conservatively charges the not-yet-decided remainder to _dropped_keys
    # so the greedy pass can never overshoot the cap mid-iteration.
    if len(_canonical_bytes(payload)) > PAYLOAD_CAP:
        ordered = sorted(payload)
        kept: dict = {}
        dropped: list[str] = []
        for index, key in enumerate(ordered):
            trial = dict(kept)
            trial[key] = payload[key]
            trial["_dropped_keys"] = sorted(dropped + ordered[index + 1:])
            if len(_canonical_bytes(trial)) <= PAYLOAD_CAP:
                kept[key] = payload[key]
            else:
                dropped.append(key)
        payload = kept
        if dropped:
            payload["_dropped_keys"] = sorted(dropped)
        truncated = True

    return payload, truncated


def payload_hash8(payload: dict) -> str:
    """Content key: sha256 of the canonical payload bytes, first 8 hex chars.

    Computed AFTER truncation (the caller hashes the built payload, never
    the raw metadata) so the key is stable for a given emitted content —
    truncation nondeterminism cannot churn the dedup key because there is
    none: build_snapshot_payload is deterministic.
    """
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()[:8]


def snapshot_eligible(payload: dict) -> bool:
    """True iff the payload is non-empty.

    (Writability is checked inside emit_task_metadata_snapshot — eligibility
    is payload-presence only. A handoff-only or empty metadata emits
    nothing: agent_handoff already covers handoff.)
    """
    return bool(payload)


def snapshot_already_emitted(
    team_name: str, task_id: str, content_key: str
) -> bool:
    """Test-and-set the snapshot marker — hard-bound to the snapshot namespace.

    Thin wrapper over agent_handoff_marker.already_emitted that HARD-BINDS
    namespace=SNAPSHOT_MARKER_NAMESPACE. Seam code and this module's emit
    routine call ONLY this wrapper (and snapshot_unclaim) — never the raw
    marker functions: a forgotten namespace arg would claim in one dir and
    no-op-unclaim against the other, leaving a poisoned marker; the wrapper
    makes that impossible by construction.
    """
    return already_emitted(
        team_name, task_id, content_key, namespace=SNAPSHOT_MARKER_NAMESPACE
    )


def snapshot_unclaim(team_name: str, task_id: str, content_key: str) -> None:
    """Compensating rollback for a claim whose journal write failed.

    Hard-bound twin of snapshot_already_emitted — same namespace constant,
    same resolver SSOT underneath, so the claim and the rollback can never
    reference divergent paths.
    """
    unclaim(
        team_name, task_id, content_key, namespace=SNAPSHOT_MARKER_NAMESPACE
    )


def emit_task_metadata_snapshot(
    team_name: str,
    task_id: str,
    subject: object,
    owner: object,
    task_metadata: Mapping[str, object] | None,
) -> None:
    """The ONE emit routine, shared verbatim by every seam. Never raises.

    Sequence (each early return is a clean no-emit):
      1. metadata guard — None → {}; non-mapping → return.
      2. build payload; empty post-exclude payload → return.
      3. validate-before-claim: sentinel-substitute a degenerate subject,
         normalize a degenerate owner to None — every emitted field is
         schema-valid BEFORE any marker claim (a claim-then-schema-reject
         would poison the marker for later valid fires).
      4. writability precondition — an unresolvable frame (e.g. an
         unpersisted tmux teammate context) DEFERS to a writable seam
         instead of claiming: no journal path → no claim, no write.
      5. content-key dedup claim (O_EXCL, snapshot namespace).
      6. occupant discriminator via the shared occupant_hash SSOT.
      7. append the event (owner/task_type/truncated only when present).
      8. compensating unclaim on a failed write so a later valid fire can
         re-emit instead of being permanently suppressed.
    """
    try:
        metadata = task_metadata or {}
        if not isinstance(metadata, Mapping):
            return

        payload, truncated = build_snapshot_payload(metadata)
        if not snapshot_eligible(payload):
            return

        if not isinstance(subject, str) or not subject.strip():
            subject = "(no subject)"
        if not isinstance(owner, str) or not owner.strip():
            owner = None

        if not get_journal_path():
            return

        task_id = sanitize_path_component(str(task_id))
        content_key = payload_hash8(payload)
        if snapshot_already_emitted(team_name, task_id, content_key):
            return

        occupant = occupant_hash(owner or "", subject)

        optional: dict = {}
        if owner:
            optional["owner"] = owner
        # Read from the ORIGINAL metadata — the payload copy may have been
        # truncation-marked (never in practice for a short type string, but
        # the original is the semantic source either way).
        task_type = metadata.get("type")
        if isinstance(task_type, str) and task_type.strip():
            optional["task_type"] = task_type
        if truncated:
            optional["truncated"] = True

        try:
            written = append_event(
                make_event(
                    "task_metadata_snapshot",
                    task_id=task_id,
                    metadata=payload,
                    subject=subject,
                    occupant=occupant,
                    **optional,
                )
            )
        except Exception:
            written = False
        if not written:
            snapshot_unclaim(team_name, task_id, content_key)
    except Exception:
        # Hermetic: a snapshot failure must never affect the host hook's
        # contract (exit-0 suppressOutput, advisory evaluation, or the
        # handoff emit path).
        pass
