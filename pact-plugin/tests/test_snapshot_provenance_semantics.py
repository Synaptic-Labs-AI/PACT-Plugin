"""
Location: pact-plugin/tests/test_snapshot_provenance_semantics.py
Summary: Assertion suite for the PROVENANCE-tracked marker-identity ruling
         in build_snapshot_payload (freeze doc §3): marker identity is a
         local this-run set, never a shape test. A caller value that merely
         LOOKS marker-shaped is ordinary data — a normal stage-2 eviction
         candidate re-markered with its TRUE sizes — and stage 3's
         original_bytes sort only ever sees this run's own int-bearing
         markers (total by construction). Extends the YELLOW-1 adversarial
         class (caller-owned marker-shaped input) from stage 3a to every
         stage, and pins the totality invariant: every input key survives
         in the payload or in _dropped_keys.

============================ NON-VACUITY ========================================
The lookalike-eviction assertions are coupled to the provenance mechanism by
a behavioral discriminator, not by presence: under the superseded
shape-tested candidacy, a lookalike was eviction-EXEMPT and its bogus
original_bytes survived verbatim (and a non-int original_bytes raised
TypeError inside the hermetic emit = silently dropped snapshot). Reverting
the provenance commit flips test_lookalike_is_evicted_with_true_sizes,
test_all_lookalike_payload_*, and test_non_int_original_bytes_* to red.
================================================================================
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.session_journal import read_events  # noqa: E402
from shared.task_metadata_snapshot import (  # noqa: E402
    PAYLOAD_CAP,
    PER_VALUE_CAP,
    _canonical_bytes,
    build_snapshot_payload,
    emit_task_metadata_snapshot,
)

TEAM = "pact-provenance"
SID = "dddddddd-4444-5555-6666-777777777777"


def _lookalike(head_chars: int, original_bytes: object = 999_999_999) -> dict:
    """A caller value with EXACTLY the marker shape but caller-owned
    content: a bogus original_bytes and an arbitrary-size head."""
    return {
        "_truncated": True,
        "original_bytes": original_bytes,
        "head": "L" * head_chars,
    }


def _marker_shaped(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value.keys()) == {"_truncated", "original_bytes", "head"}
        and value.get("_truncated") is True
    )


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project")
    return tmp_path


class TestLookalikeIsOrdinaryValue:
    def test_lookalike_untouched_when_no_cap_pressure(self):
        # Ordinary-data semantics cut both ways: with no cap in play a
        # lookalike passes through VERBATIM (bogus original_bytes and all),
        # exactly like any other value.
        look = _lookalike(64)
        payload, truncated = build_snapshot_payload({"look": look, "k": "v"})
        assert payload["look"] == look
        assert truncated is False

    def test_lookalike_is_evicted_with_true_sizes(self):
        # The behavioral discriminator vs shape-tested candidacy: the
        # lookalike is the LARGEST value in an over-payload-cap mapping, so
        # largest-first eviction must pick IT — and re-marker it with sizes
        # computed from ITS serialization, discarding the bogus
        # original_bytes. (Shape-tested candidacy left it exempt and evicted
        # the next-largest real value instead.)
        look = _lookalike(15 * 1024)
        look_size = len(_canonical_bytes(look))
        metadata = {
            "look": look,
            "b": "b" * (13 * 1024),
            "c": "c" * (13 * 1024),
            "d": "d" * (13 * 1024),
            "e": "e" * (13 * 1024),
        }
        assert len(_canonical_bytes(metadata)) > PAYLOAD_CAP
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        evicted = payload["look"]
        assert _marker_shaped(evicted)
        assert evicted["original_bytes"] == look_size, (
            "OUR marker must carry the lookalike's TRUE canonical size, "
            "not its caller-authored 999999999"
        )
        assert evicted["original_bytes"] != 999_999_999
        # head = prefix of the LOOKALIKE's canonical serialization.
        assert evicted["head"] == (
            _canonical_bytes(look)[:1024].decode("utf-8")
        )
        for key in ("b", "c", "d", "e"):
            assert payload[key] == metadata[key]

    def test_lookalike_over_per_value_cap_marked_at_stage1(self):
        look = _lookalike(20 * 1024)  # canonical > PER_VALUE_CAP
        look_size = len(_canonical_bytes(look))
        assert look_size > PER_VALUE_CAP
        payload, truncated = build_snapshot_payload({"look": look})
        assert truncated is True
        assert _marker_shaped(payload["look"])
        assert payload["look"]["original_bytes"] == look_size

    def test_nested_marker_shape_is_never_consulted(self):
        # Marker shape NESTED inside a value is plain data at every stage:
        # small -> verbatim; the whole OUTER value over-cap -> one OUR
        # marker for the outer value.
        small = {"inner": _lookalike(32)}
        payload, truncated = build_snapshot_payload({"n": small})
        assert payload["n"] == small
        assert truncated is False

        big = {"inner": _lookalike(20 * 1024)}
        payload, truncated = build_snapshot_payload({"n": big})
        assert _marker_shaped(payload["n"])
        assert payload["n"]["original_bytes"] == len(_canonical_bytes(big))
        assert truncated is True


class TestStage3TotalityUnderLookalikes:
    def test_all_lookalike_payload_over_cap_builds_without_error(self):
        # Under shape-tested candidacy an all-lookalike over-cap payload had
        # ZERO stage-2 candidates (all exempt) and stage 3a sorted the
        # lookalikes' caller-owned original_bytes. Under provenance they are
        # ordinary values: evicted normally, and stage 3 sees only this
        # run's own markers.
        metadata = {
            f"k{i}": _lookalike(15 * 1024, original_bytes=999_999_999 - i)
            for i in range(5)
        }
        assert len(_canonical_bytes(metadata)) > PAYLOAD_CAP
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP
        assert set(payload) == set(metadata)

    def test_non_int_original_bytes_lookalike_builds_and_emits(
            self, live_env):
        # FOCUS assertion (ruled in-arc): a lookalike whose original_bytes
        # is NOT an int, inside a >PAYLOAD_CAP mapping, must neither raise
        # in the builder nor silently drop the snapshot at the emit level.
        # (Pre-provenance: stage 3a's sort hit TypeError on the str, the
        # hermetic emit swallowed it, and the snapshot vanished.)
        metadata = {
            f"k{i}": _lookalike(15 * 1024, original_bytes="NOT-AN-INT")
            for i in range(5)
        }
        assert len(_canonical_bytes(metadata)) > PAYLOAD_CAP

        payload, truncated = build_snapshot_payload(metadata)  # no raise
        assert truncated is True
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP

        emit_task_metadata_snapshot(TEAM, "31", "subj", "own", metadata)
        events = read_events("task_metadata_snapshot")
        assert len(events) == 1, (
            "the snapshot must EMIT (no drop): a TypeError inside the "
            "hermetic guard would silently destroy the mirror this feature "
            "exists to provide"
        )
        assert events[0]["truncated"] is True

    def test_mixed_lookalike_and_real_values_deep_pressure_totality(self):
        # Drive through stages 2 + 3a + 3b with lookalikes in the mix
        # (non-int original_bytes included): every input key must survive
        # in the payload or by name in _dropped_keys — never silently lost.
        metadata: dict = {
            f"big{i:04d}": "x" * (17 * 1024) for i in range(1400)
        }
        metadata.update({
            f"look{i:04d}": _lookalike(15 * 1024,
                                       original_bytes="NOT-AN-INT")
            for i in range(100)
        })
        metadata["small"] = "tiny"
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        assert len(_canonical_bytes(payload)) <= PAYLOAD_CAP

        dropped = payload.get("_dropped_keys", [])
        surviving = (set(payload) - {"_dropped_keys"}) | set(dropped)
        assert surviving == set(metadata), (
            "totality: every input key in payload or _dropped_keys"
        )
        assert dropped == sorted(dropped)
        # The whole event line the payload would produce stays strict JSON.
        json.loads(json.dumps({"metadata": payload}))


class TestReadOnlyInputAcrossStages:
    """YELLOW-1 class extension: the caller's metadata (fed to the handoff
    path at the seams) must be deep-intact after builds that traverse each
    stage — including stage-2 eviction OF a lookalike and the 3b floor."""

    def test_stage2_lookalike_eviction_leaves_caller_intact(self):
        metadata = {
            "look": _lookalike(15 * 1024),
            "b": "b" * (13 * 1024),
            "c": "c" * (13 * 1024),
            "d": "d" * (13 * 1024),
            "e": "e" * (13 * 1024),
        }
        before = copy.deepcopy(metadata)
        build_snapshot_payload(metadata)
        assert metadata == before

    def test_stage3_floor_with_lookalikes_leaves_caller_intact(self):
        metadata: dict = {
            f"big{i:04d}": "x" * (17 * 1024) for i in range(1400)
        }
        metadata["look"] = _lookalike(15 * 1024,
                                      original_bytes="NOT-AN-INT")
        before = copy.deepcopy(metadata)
        build_snapshot_payload(metadata)
        assert metadata == before


class TestDroppedKeysNameCollisionCharacterization:
    """CHARACTERIZATION (not endorsement — reported in the TEST-phase
    HANDOFF): "_dropped_keys" is a reserved name only on the OUTPUT side.
    A caller metadata key literally named "_dropped_keys" passes through
    verbatim under no pressure (reader ambiguity: indistinguishable from a
    builder-authored list), and when the stage-3b floor fires, the
    builder's own list CLOBBERS the caller's value — the caller content is
    lost with no marker and no drop record. Unreachable in practice (needs
    >1200 keys post-head-emptying AND that exact key name); documented so a
    disposition ruling can cite observed behavior."""

    def test_no_pressure_caller_dropped_keys_passes_through(self):
        payload, truncated = build_snapshot_payload(
            {"_dropped_keys": ["caller", "data"], "k": "v"}
        )
        assert payload["_dropped_keys"] == ["caller", "data"]
        assert truncated is False

    def test_floor_clobbers_caller_dropped_keys_value(self):
        metadata: dict = {
            f"key{i:04d}": "x" * (17 * 1024) for i in range(1500)
        }
        metadata["_dropped_keys"] = ["caller", "data"]
        payload, truncated = build_snapshot_payload(metadata)
        assert truncated is True
        # Current behavior: the slot holds the BUILDER's drop list; the
        # caller's ["caller", "data"] is gone without a trace.
        assert payload["_dropped_keys"] != ["caller", "data"]
        assert "caller" not in payload["_dropped_keys"]
        assert all(k.startswith("key") for k in payload["_dropped_keys"])
