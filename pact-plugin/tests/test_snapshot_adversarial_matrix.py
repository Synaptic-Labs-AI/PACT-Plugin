"""
Location: pact-plugin/tests/test_snapshot_adversarial_matrix.py
Summary: TEST-phase adversarial/boundary depth for task_metadata_snapshot —
         exact cap boundaries (per-value and payload, computed against the
         imported substrate constants, never re-declared), the dual-cap
         interaction case (N same-size values each under the per-value cap
         whose total exceeds the payload cap), jumbo emit-level payloads
         over a REAL tmp journal, pathological-input characterizations
         (non-JSON-serializable values; NaN floats), the §6 task-id-reuse
         occupant join, and the substrate-level writability defer.

         Complements (never duplicates) the CODE-phase suites: the
         substrate unit file pins stage semantics with synthetic jumbos;
         THIS file pins the exact boundary arithmetic, the emit-level
         behavior of hostile inputs through the REAL append_event/
         read_events pair, and the reader-side join contract.

Harness: the L2 exemplar seam (test_agent_handoff_emitter_integration.py) —
Path.home -> tmp + the pact_context fixture; REAL journal writes and reads,
no append_event spy. Characterization tests are labeled as such in their
docstrings: they document CURRENT behavior for the pending-disposition
record, not an endorsed contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.agent_handoff_marker import occupant_hash  # noqa: E402
from shared.session_journal import read_events  # noqa: E402
from shared.task_metadata_snapshot import (  # noqa: E402
    PAYLOAD_CAP,
    PER_VALUE_CAP,
    _canonical_bytes,
    build_snapshot_payload,
    emit_task_metadata_snapshot,
    payload_hash8,
)

TEAM = "pact-advmatrix"
SID = "cccccccc-3333-4444-5555-666666666666"


def _is_marker_shape(value: object) -> bool:
    """Reader-side marker recognition (the harvest consumer's view)."""
    return isinstance(value, dict) and value.get("_truncated") is True


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Real tmp home + real session context: append_event/read_events
    resolve a REAL on-disk journal; markers land on real tmp disk."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project")
    return tmp_path


class TestPerValueCapExactBoundary:
    """cap-1 / cap / cap+1 on the canonical size of a single value.

    A str value's canonical form is the quoted string: len = n + 2. The
    triple is derived from the imported PER_VALUE_CAP, so a cap change
    re-derives the boundary instead of silently passing stale arithmetic.
    """

    def _value_of_canonical_size(self, size: int) -> str:
        value = "a" * (size - 2)
        assert len(_canonical_bytes(value)) == size
        return value

    def test_one_under_cap_kept_verbatim(self):
        value = self._value_of_canonical_size(PER_VALUE_CAP - 1)
        payload, truncated = build_snapshot_payload({"k": value})
        assert payload["k"] == value
        assert truncated is False

    def test_exactly_at_cap_kept_verbatim(self):
        value = self._value_of_canonical_size(PER_VALUE_CAP)
        payload, truncated = build_snapshot_payload({"k": value})
        assert payload["k"] == value
        assert truncated is False

    def test_one_over_cap_marked(self):
        value = self._value_of_canonical_size(PER_VALUE_CAP + 1)
        payload, truncated = build_snapshot_payload({"k": value})
        marker = payload["k"]
        assert _is_marker_shape(marker)
        assert marker["original_bytes"] == PER_VALUE_CAP + 1
        assert truncated is True


class TestPayloadCapExactBoundary:
    """Exact payload-cap arithmetic with every value under the per-value cap.

    Four fixed 13KB values + one pad value sized so the WHOLE canonical
    payload lands exactly at PAYLOAD_CAP (then +1). Sizes are measured with
    the substrate's own _canonical_bytes — the same yardstick stage 2 uses.
    """

    def _payload_at(self, total: int) -> dict:
        fixed = {f"k{i}": "x" * (13 * 1024) for i in range(4)}
        base = dict(fixed)
        base["pad"] = ""
        remainder = total - len(_canonical_bytes(base))
        assert remainder > 0, "fixed part must sit under the target"
        metadata = dict(fixed)
        metadata["pad"] = "p" * remainder
        assert len(_canonical_bytes(metadata)) == total
        for value in metadata.values():
            assert len(_canonical_bytes(value)) <= PER_VALUE_CAP
        return metadata

    def test_exactly_at_payload_cap_untouched(self):
        metadata = self._payload_at(PAYLOAD_CAP)
        payload, truncated = build_snapshot_payload(metadata)
        assert payload == metadata
        assert truncated is False

    def test_one_over_payload_cap_single_largest_first_eviction(self):
        metadata = self._payload_at(PAYLOAD_CAP + 1)
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        markers = [k for k, v in payload.items() if _is_marker_shape(v)]
        # The four 13KB values tie as largest (pad is smaller); ascending
        # lexicographic tie-break pins the victim to k0. One eviction
        # (~13KB -> ~1KB marker) lands the payload far back under cap.
        assert markers == ["k0"]
        assert payload["k0"]["original_bytes"] == len(
            _canonical_bytes(metadata["k0"])
        )
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP
        for key in ("k1", "k2", "k3", "pad"):
            assert payload[key] == metadata[key]


class TestDualCapInteraction:
    """The five-15KB case: each value under PER_VALUE_CAP, total over
    PAYLOAD_CAP — only the payload cap fires, with exact marker placement
    and insertion-order-independent bytes/hash."""

    FIVE = {k: k * (15 * 1024) for k in ("a", "b", "c", "d", "e")}

    def test_exactly_one_eviction_at_lexicographically_smallest_tie(self):
        payload, truncated = build_snapshot_payload(self.FIVE)
        assert truncated is True
        markers = [k for k, v in payload.items() if _is_marker_shape(v)]
        assert markers == ["a"], (
            "equal sizes tie -> ascending key order picks 'a'; exactly one "
            "eviction suffices (15KB value -> ~1KB marker)"
        )
        for key in ("b", "c", "d", "e"):
            assert payload[key] == self.FIVE[key]
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP

    def test_insertion_order_shuffles_byte_identical_and_same_hash(self):
        orders = [
            ("a", "b", "c", "d", "e"),
            ("e", "d", "c", "b", "a"),
            ("c", "a", "e", "b", "d"),
        ]
        results = []
        for order in orders:
            metadata = {k: self.FIVE[k] for k in order}
            payload, _ = build_snapshot_payload(metadata)
            results.append(
                (_canonical_bytes(payload), payload_hash8(payload))
            )
        assert len({r[0] for r in results}) == 1, "byte-identical payloads"
        assert len({r[1] for r in results}) == 1, "identical content keys"

    def test_distinct_sizes_evict_strictly_largest_not_key_order(self):
        # Non-tie control: 'z' holds the largest value, so largest-first
        # must pick 'z' even though it is lexicographically last — proving
        # the size criterion dominates and the key order is only the
        # tie-break.
        metadata = {
            "a": "a" * (12 * 1024),
            "m": "m" * (13 * 1024),
            "z": "z" * (15 * 1024),
            "q": "q" * (14 * 1024),
            "f": "f" * (12 * 1024),
        }
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        markers = [k for k, v in payload.items() if _is_marker_shape(v)]
        assert markers == ["z"]


class TestJumboEmitLevel:
    """A multi-MB value through the ONE emit routine over a REAL journal:
    never raises, lands exactly one bounded, strictly-valid JSONL line."""

    def test_5mb_value_emits_bounded_valid_event(self, live_env):
        emit_task_metadata_snapshot(
            TEAM, "77", "jumbo", "owner-x",
            {"blob": "B" * (5 * 1024 * 1024), "note": "small sibling"},
        )
        events = read_events("task_metadata_snapshot")
        assert len(events) == 1
        event = events[0]
        assert event["truncated"] is True
        assert _is_marker_shape(event["metadata"]["blob"])
        assert event["metadata"]["blob"]["original_bytes"] == (
            5 * 1024 * 1024 + 2
        )
        assert event["metadata"]["note"] == "small sibling"

        journal = (live_env / ".claude" / "pact-sessions" / "project" / SID
                   / "session-journal.jsonl")
        lines = [ln for ln in
                 journal.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 1
        assert len(lines[0].encode("utf-8")) < PAYLOAD_CAP + 16 * 1024, (
            "the event line must stay near the payload cap, never carry "
            "the raw 5MB value"
        )
        # Strict JSON validity of the line (reject NaN/Infinity tokens).
        json.loads(
            lines[0],
            parse_constant=lambda c: (_ for _ in ()).throw(
                ValueError(f"non-strict JSON constant {c}")
            ),
        )


class TestPathologicalInputCharacterization:
    """CHARACTERIZATION (not endorsement): current behavior of inputs that
    the platform's JSON layer cannot produce but a direct Python caller
    could. Documented for the pending-disposition record; if a disposition
    ruling changes the semantics, update these alongside it."""

    def test_non_serializable_value_no_event_no_marker_poison(
            self, live_env):
        """A non-JSON-serializable value (set) raises TypeError inside
        _canonical_bytes DURING payload build — before any marker claim —
        and the hermetic emit swallows it. CHARACTERIZATION: the snapshot
        is silently dropped (whole event, including serializable siblings),
        but no marker is claimed, so nothing is poisoned: a later fire with
        clean metadata emits normally."""
        emit_task_metadata_snapshot(
            TEAM, "88", "subj", "own",
            {"bad": {1, 2, 3}, "good": "sibling"},
        )
        assert read_events("task_metadata_snapshot") == []
        marker_dir = (live_env / ".claude" / "teams" / TEAM
                      / ".task_metadata_snapshot_emitted")
        assert not marker_dir.exists() or not any(marker_dir.iterdir()), (
            "validate-before-claim: the failed build must not claim"
        )
        # Recovery leg: same task, clean metadata -> emits.
        emit_task_metadata_snapshot(TEAM, "88", "subj", "own",
                                    {"good": "sibling"})
        assert len(read_events("task_metadata_snapshot")) == 1

    def test_nan_float_writes_spec_invalid_line_python_reader_tolerates(
            self, live_env):
        """CHARACTERIZATION of the NaN wrinkle (reported in the TEST-phase
        HANDOFF): json.dumps defaults to allow_nan=True, so a float('nan')
        metadata value survives the build and append_event writes a line
        containing the bare NaN token — which is NOT spec-valid JSON.
        Python's own read_events tolerates it (parse_constant default),
        so the journal file is not poisoned for in-repo readers, but any
        strict consumer (jq, non-Python tooling) rejects that line.
        Unreachable via the platform (JSON cannot carry NaN); reachable
        only by a direct Python caller."""
        emit_task_metadata_snapshot(
            TEAM, "99", "subj", "own", {"x": float("nan"), "y": "ok"},
        )
        events = read_events("task_metadata_snapshot")
        assert len(events) == 1, "Python-side read tolerates the line"
        assert events[0]["metadata"]["y"] == "ok"

        journal = (live_env / ".claude" / "pact-sessions" / "project" / SID
                   / "session-journal.jsonl")
        line = journal.read_text(encoding="utf-8").splitlines()[0]
        assert "NaN" in line, "the bare token is on disk"
        with pytest.raises(ValueError):
            json.loads(
                line,
                parse_constant=lambda c: (_ for _ in ()).throw(
                    ValueError(f"strict reject {c}")
                ),
            )


class TestTaskIdReuseOccupantJoin:
    """§6 ruling: the occupant field must discriminate a reused task_id —
    a reader holding (owner, subject) selects ITS snapshot, not the other
    arc's, via the shared occupant_hash SSOT."""

    def test_same_task_id_two_occupants_join_selects_each(self, live_env):
        emit_task_metadata_snapshot(
            TEAM, "5", "arc-1: design", "architect",
            {"variety": {"total": 6}},
        )
        emit_task_metadata_snapshot(
            TEAM, "5", "arc-2: implement", "backend-coder",
            {"variety": {"total": 9}},
        )
        events = read_events("task_metadata_snapshot")
        assert len(events) == 2, "different occupants -> both events"

        arc1 = [e for e in events
                if e["occupant"] == occupant_hash("architect",
                                                  "arc-1: design")]
        arc2 = [e for e in events
                if e["occupant"] == occupant_hash("backend-coder",
                                                  "arc-2: implement")]
        assert len(arc1) == 1 and arc1[0]["metadata"]["variety"]["total"] == 6
        assert len(arc2) == 1 and arc2[0]["metadata"]["variety"]["total"] == 9

    def test_ownerless_occupant_discriminates_via_subject(self, live_env):
        emit_task_metadata_snapshot(
            TEAM, "6", "signal: halt build", None, {"type": "blocker",
                                                    "ctx": "one"},
        )
        emit_task_metadata_snapshot(
            TEAM, "6", "signal: halt deploy", None, {"type": "blocker",
                                                     "ctx": "two"},
        )
        events = read_events("task_metadata_snapshot")
        assert len(events) == 2
        halt_build = [e for e in events
                      if e["occupant"] == occupant_hash("",
                                                        "signal: halt build")]
        assert len(halt_build) == 1
        assert halt_build[0]["metadata"]["ctx"] == "one"
        assert "owner" not in halt_build[0]


class TestWritabilityDeferSubstrate:
    """The substrate's own defer leg with NO session context at all (the
    unpersisted-frame topology): no journal path -> no event, no claim —
    DEFER-not-poison at the lowest level, complementing the coder's seam-C
    stdin-frame test."""

    def test_no_context_no_event_no_marker_claim(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # No pact_context fixture write: get_journal_path resolves "".
        emit_task_metadata_snapshot(
            TEAM, "11", "subj", "own", {"k": "v"},
        )
        marker_dir = (tmp_path / ".claude" / "teams" / TEAM
                      / ".task_metadata_snapshot_emitted")
        assert not marker_dir.exists(), (
            "an unwritable frame must claim NOTHING (a claim here would "
            "permanently suppress the writable seam's later emit)"
        )
