"""
Location: pact-plugin/tests/test_task_metadata_snapshot.py
Summary: Unit tests for the task_metadata_snapshot substrate's pure layer —
         dual-cap three-stage size-bounding semantics, determinism under
         insertion-order shuffles, UTF-8 boundary safety of truncation
         heads, hash-after-truncation, exclude-set application, and the
         read-only-input contract of build_snapshot_payload.
Used by: pytest (CODE-phase verification for the substrate module); the
         emit-routine seam behavior is covered by the seam suites, and the
         marker cross-namespace independence pair lives with the marker
         namespace tests.
"""

import copy
import json

import pytest

from shared.task_metadata_snapshot import (
    HEAD_BYTES,
    PAYLOAD_CAP,
    PER_VALUE_CAP,
    SNAPSHOT_EXCLUDE,
    _canonical_bytes,
    _is_marker,
    _utf8_safe_head,
    build_snapshot_payload,
    payload_hash8,
    snapshot_eligible,
)


def _size(value: object) -> int:
    return len(_canonical_bytes(value))


def _jumbo_str(target_canonical_bytes: int) -> str:
    """A plain-ASCII string whose canonical serialization is a bit over
    ``target_canonical_bytes`` (canonical form adds two quote bytes)."""
    return "x" * target_canonical_bytes


class TestCanonicalBytes:
    def test_insertion_order_independent(self):
        """sort_keys makes canonical bytes identical for any dict order."""
        a = {"b": 1, "a": {"y": 2, "x": 3}}
        b = {"a": {"x": 3, "y": 2}, "b": 1}
        assert _canonical_bytes(a) == _canonical_bytes(b)

    def test_compact_separators(self):
        assert _canonical_bytes({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'


class TestUtf8SafeHead:
    """The truncation-head cut must never bisect a multibyte character.

    With the pinned _canonical_bytes (json.dumps default ensure_ascii) the
    canonical form is pure ASCII, so these cases exercise the helper's
    defense-in-depth contract directly with synthetic multibyte bytes.
    """

    def test_ascii_cut_is_exact(self):
        assert _utf8_safe_head(b"abcdef", 4) == "abcd"

    @pytest.mark.parametrize("cut", [1, 2, 3])
    def test_multibyte_straddle_backs_off(self, cut):
        """Cutting inside a 4-byte character yields the empty prefix, not a
        decode error or replacement noise."""
        emoji = "\U0001f600".encode("utf-8")  # 4 bytes
        assert _utf8_safe_head(emoji, cut) == ""

    def test_multibyte_boundary_kept(self):
        two_chars = ("é" * 2).encode("utf-8")  # 2 × 2-byte chars
        assert _utf8_safe_head(two_chars, 3) == "é"
        assert _utf8_safe_head(two_chars, 4) == "éé"

    def test_limit_beyond_data_returns_all(self):
        assert _utf8_safe_head(b"ab", 10) == "ab"


class TestBuildSnapshotPayloadBasics:
    def test_handoff_excluded_everything_else_kept(self):
        metadata = {
            "handoff": {"produced": "p"},
            "teachback_submit": {"understanding": "u"},
            "variety": {"total": 7},
            "intentional_wait": {"reason": "awaiting_lead_completion"},
        }
        payload, truncated = build_snapshot_payload(metadata)
        assert "handoff" not in payload
        assert payload["teachback_submit"] == {"understanding": "u"}
        assert payload["variety"] == {"total": 7}
        assert payload["intentional_wait"] == {
            "reason": "awaiting_lead_completion"
        }
        assert truncated is False

    def test_exclude_set_is_handoff_only(self):
        """Pins the ratified minimal exclude set — a missed key is silent
        loss while a junk key is bounded bytes."""
        assert SNAPSHOT_EXCLUDE == frozenset({"handoff"})

    def test_handoff_only_metadata_yields_ineligible_payload(self):
        payload, truncated = build_snapshot_payload({"handoff": {"k": "v"}})
        assert payload == {}
        assert truncated is False
        assert snapshot_eligible(payload) is False

    def test_empty_metadata_ineligible(self):
        payload, _ = build_snapshot_payload({})
        assert snapshot_eligible(payload) is False

    def test_non_empty_payload_eligible(self):
        payload, _ = build_snapshot_payload({"variety": {"total": 7}})
        assert snapshot_eligible(payload) is True

    def test_input_not_mutated(self):
        """READ-ONLY contract: the input mapping (and its nested values)
        is byte-identical after the build, even when truncation fires."""
        metadata = {
            "handoff": {"produced": "p"},
            "big": _jumbo_str(PER_VALUE_CAP + 100),
            "nested": {"a": [1, 2, {"b": "c"}]},
        }
        before = copy.deepcopy(metadata)
        build_snapshot_payload(metadata)
        assert metadata == before


class TestPerValueCap:
    def test_over_cap_value_replaced_by_marker(self):
        big = _jumbo_str(PER_VALUE_CAP + 100)
        payload, truncated = build_snapshot_payload({"big": big, "small": 1})
        assert truncated is True
        assert payload["small"] == 1
        marker = payload["big"]
        assert _is_marker(marker)
        assert marker["original_bytes"] == _size(big)
        # Head is the first HEAD_BYTES of the canonical serialization —
        # a str field CONTAINING the serialized prefix (starts with the
        # opening quote of the canonical JSON string form).
        assert marker["head"] == _canonical_bytes(big)[:HEAD_BYTES].decode(
            "utf-8"
        )
        assert marker["head"].startswith('"')
        assert len(marker["head"]) == HEAD_BYTES

    def test_at_cap_value_kept(self):
        # Canonical form of a str adds 2 quote bytes: aim exactly at the cap.
        exact = "x" * (PER_VALUE_CAP - 2)
        assert _size(exact) == PER_VALUE_CAP
        payload, truncated = build_snapshot_payload({"exact": exact})
        assert payload["exact"] == exact
        assert truncated is False

    def test_marker_event_line_is_valid_json(self):
        big = _jumbo_str(PER_VALUE_CAP + 100)
        payload, _ = build_snapshot_payload({"big": big})
        line = json.dumps(payload)
        assert json.loads(line)["big"]["_truncated"] is True


class TestPayloadCapLargestFirst:
    def test_largest_untruncated_evicted_first(self):
        """Three values under the per-value cap whose sum exceeds the
        payload cap: eviction order is largest-first."""
        largest = _jumbo_str(PER_VALUE_CAP - 100)
        middle = _jumbo_str(PER_VALUE_CAP - 200)
        small = "keep-me"
        metadata = {}
        # Enough near-cap values to exceed PAYLOAD_CAP (64K / ~16K each → 5+).
        for index in range(4):
            metadata[f"mid{index}"] = middle
        metadata["z_largest"] = largest
        metadata["a_small"] = small
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert _size(payload) <= PAYLOAD_CAP
        # The single largest value was evicted (it is a marker now)...
        assert _is_marker(payload["z_largest"])
        # ...and the small value survived intact.
        assert payload["a_small"] == small

    def test_tie_broken_by_ascending_key(self):
        """Equal-size values: the lexicographically smallest key is evicted
        first (pinned tie-break)."""
        same = _jumbo_str(PER_VALUE_CAP - 100)
        metadata = {f"k{index}": same for index in range(5)}
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert _size(payload) <= PAYLOAD_CAP
        markers = [k for k, v in sorted(payload.items()) if _is_marker(v)]
        kept = [k for k, v in sorted(payload.items()) if not _is_marker(v)]
        # Eviction consumed a prefix of the ascending key order.
        assert markers == sorted(metadata)[: len(markers)]
        assert kept == sorted(metadata)[len(markers):]

    def test_stage1_markers_not_recandidates(self):
        """A value already marker-replaced by stage 1 is not evicted again —
        stage 2 candidates are un-truncated values only."""
        stage1_big = _jumbo_str(PER_VALUE_CAP * 5)
        near_cap = _jumbo_str(PER_VALUE_CAP - 100)
        metadata = {"jumbo": stage1_big}
        for index in range(5):
            metadata[f"mid{index}"] = near_cap
        payload, _ = build_snapshot_payload(metadata)
        marker = payload["jumbo"]
        assert _is_marker(marker)
        # Stage-1 marker retains its head (only stage 3a empties heads).
        assert marker["head"] != ""
        assert marker["original_bytes"] == _size(stage1_big)


class TestPathologicalFloor:
    def test_heads_emptied_descending_original_bytes(self):
        """So many over-cap values that all-markers-with-heads exceeds the
        payload cap: heads are emptied, biggest originals first."""
        # Each marker with head ≈ 1KB+overhead; need > 64K of markers → 70+.
        metadata = {
            f"key{index:03d}": _jumbo_str(PER_VALUE_CAP + 1000 + index)
            for index in range(70)
        }
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert _size(payload) <= PAYLOAD_CAP
        assert all(_is_marker(v) for v in payload.values())
        emptied = [k for k, v in payload.items() if v["head"] == ""]
        kept_heads = [k for k, v in payload.items() if v["head"] != ""]
        # Descending original_bytes = descending index here, so the emptied
        # set is a suffix of the index order and every emptied original is
        # >= every kept-head original.
        if emptied and kept_heads:
            min_emptied = min(payload[k]["original_bytes"] for k in emptied)
            max_kept = max(payload[k]["original_bytes"] for k in kept_heads)
            assert min_emptied >= max_kept

    def test_dropped_keys_floor_names_survive(self):
        """Beyond all-markers-empty-heads capacity: keys survive name-only
        in _dropped_keys — existence is never silently lost."""
        # Empty-head markers are ~60 bytes each; > 64K needs > ~1100 keys.
        metadata = {
            f"key{index:05d}": _jumbo_str(PER_VALUE_CAP + 100)
            for index in range(1500)
        }
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert _size(payload) <= PAYLOAD_CAP
        dropped = payload.get("_dropped_keys")
        assert isinstance(dropped, list) and dropped
        assert dropped == sorted(dropped)
        surviving = set(payload) - {"_dropped_keys"}
        # Every input key is accounted for: kept whole or named in the list.
        assert surviving | set(dropped) == set(metadata)
        # Kept keys are the ascending-order prefix survivors.
        assert all(
            kept < dropped[0] for kept in surviving
        ), "kept keys precede the first dropped key in ascending order"


class TestDeterminismAndHash:
    def test_insertion_order_shuffles_identical_payload_and_hash(self):
        """Identical mapping under any insertion order → byte-identical
        canonical payload and identical payload_hash8 (both with and
        without truncation in play)."""
        base = {
            "teachback_submit": {"understanding": "u", "first_action": "f"},
            "variety": {"total": 10},
            "big": _jumbo_str(PER_VALUE_CAP + 500),
            "analysis": ["a", "b", {"c": 1}],
        }
        orders = [
            dict(sorted(base.items())),
            dict(sorted(base.items(), reverse=True)),
            {
                "big": base["big"],
                "analysis": base["analysis"],
                "variety": base["variety"],
                "teachback_submit": base["teachback_submit"],
            },
        ]
        results = [build_snapshot_payload(order) for order in orders]
        canon = {_canonical_bytes(payload) for payload, _ in results}
        hashes = {payload_hash8(payload) for payload, _ in results}
        assert len(canon) == 1
        assert len(hashes) == 1
        assert all(truncated is True for _, truncated in results)

    def test_hash_after_truncation_not_raw_metadata(self):
        """The content key is computed on the TRUNCATED payload: two raw
        inputs that differ only beyond the truncation point collapse to
        the same emitted content and the same hash."""
        prefix = _jumbo_str(PER_VALUE_CAP + 5000)
        a = {"big": prefix + "AAAA"}
        b = {"big": prefix + "BBBB"}
        payload_a, _ = build_snapshot_payload(a)
        payload_b, _ = build_snapshot_payload(b)
        # Same length + same head → identical markers, despite raw diff.
        assert payload_a == payload_b
        assert payload_hash8(payload_a) == payload_hash8(payload_b)
        # And a genuinely different payload gets a different key.
        payload_c, _ = build_snapshot_payload({"big": prefix + "AAAAA"})
        assert payload_hash8(payload_c) != payload_hash8(payload_a)

    def test_hash8_shape(self):
        payload, _ = build_snapshot_payload({"variety": {"total": 7}})
        key = payload_hash8(payload)
        assert len(key) == 8
        assert all(ch in "0123456789abcdef" for ch in key)
