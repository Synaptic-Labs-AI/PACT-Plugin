"""
Tests for shared.intentional_wait — validate_wait, wait_stale, canonical_since.

Coverage targets:
- validate_wait: non-dict inputs, missing/empty required keys, malformed since,
  tz-naive since (must reject), unknown keys (forward-compat), trailing Z vs +00:00,
  non-UTC offsets.
- wait_stale: fresh / stale / boundary / missing / malformed / future-dated,
  custom threshold, non-UTC offset age parity.
- canonical_since: shape and round-trip through validate_wait + wait_stale.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# --- helpers ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _fresh_wait(**overrides):
    payload = {
        "reason": "awaiting_teachback_approved",
        "expected_resolver": "lead",
        "since": _iso(datetime.now(timezone.utc)),
    }
    payload.update(overrides)
    return payload


# --- validate_wait ---------------------------------------------------------

class TestValidateWait:
    def test_fresh_payload_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait()) is True

    def test_none_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(None) is False

    def test_non_dict_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait([]) is False
        assert validate_wait("string") is False
        assert validate_wait(42) is False

    def test_missing_reason_rejected(self):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait()
        del payload["reason"]
        assert validate_wait(payload) is False

    def test_empty_reason_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(reason="")) is False
        assert validate_wait(_fresh_wait(reason="   ")) is False

    def test_non_string_reason_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(reason=42)) is False

    def test_missing_resolver_rejected(self):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait()
        del payload["expected_resolver"]
        assert validate_wait(payload) is False

    def test_empty_resolver_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(expected_resolver="")) is False

    def test_custom_resolver_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(expected_resolver="custom-orchestrator")) is True

    def test_missing_since_rejected(self):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait()
        del payload["since"]
        assert validate_wait(payload) is False

    def test_non_string_since_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since=1234567890)) is False

    def test_unparseable_since_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since="yesterday")) is False
        assert validate_wait(_fresh_wait(since="2026-13-99")) is False

    def test_tz_naive_since_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since="2026-04-21T15:30:00")) is False

    def test_trailing_z_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since="2026-04-21T15:30:00Z")) is True

    def test_plus_00_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since="2026-04-21T15:30:00+00:00")) is True

    def test_non_utc_offset_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(since="2026-04-21T15:30:00-04:00")) is True

    def test_unknown_keys_preserved(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait(correlation_id="abc", peer_name="architect")) is True


# --- wait_stale ------------------------------------------------------------

class TestWaitStale:
    def test_fresh_is_not_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(_fresh_wait()) is False

    def test_none_is_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(None) is True

    def test_malformed_is_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale({"reason": "x"}) is True
        assert wait_stale({"foo": "bar"}) is True

    def test_unparseable_since_is_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(_fresh_wait(since="not a date")) is True

    def test_tz_naive_since_is_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(_fresh_wait(since="2026-04-21T15:30:00")) is True

    def test_over_threshold_is_stale(self):
        from shared.intentional_wait import wait_stale

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        since = now - timedelta(minutes=31)
        payload = _fresh_wait(since=_iso(since))
        assert wait_stale(payload, _now=now) is True

    def test_under_threshold_is_not_stale(self):
        from shared.intentional_wait import wait_stale

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        since = now - timedelta(minutes=29)
        payload = _fresh_wait(since=_iso(since))
        assert wait_stale(payload, _now=now) is False

    def test_boundary_exactly_at_threshold_is_stale(self):
        from shared.intentional_wait import wait_stale

        # >= comparison — exactly-threshold is stale
        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        since = now - timedelta(minutes=30)
        payload = _fresh_wait(since=_iso(since))
        assert wait_stale(payload, _now=now) is True

    def test_future_since_is_not_stale(self):
        from shared.intentional_wait import wait_stale

        # Clock drift / tampering: future-dated since → negative age → not stale
        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        since = now + timedelta(hours=2)
        payload = _fresh_wait(since=_iso(since))
        assert wait_stale(payload, _now=now) is False

    def test_custom_threshold_override(self):
        from shared.intentional_wait import wait_stale

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        since = now - timedelta(minutes=10)
        payload = _fresh_wait(since=_iso(since))
        # 10 min elapsed; threshold=5 → stale; threshold=15 → fresh
        assert wait_stale(payload, threshold_minutes=5, _now=now) is True
        assert wait_stale(payload, threshold_minutes=15, _now=now) is False

    def test_non_utc_offset_age_parity(self):
        from shared.intentional_wait import wait_stale

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        # 15 min ago in UTC, expressed as -04:00 offset wall-clock
        utc_since = now - timedelta(minutes=15)
        offset_str = utc_since.astimezone(timezone(timedelta(hours=-4))).isoformat(timespec="seconds")
        payload = _fresh_wait(since=offset_str)
        # 15 min < 30 min default → not stale; age computation must normalize tz
        assert wait_stale(payload, _now=now) is False


# --- canonical_since -------------------------------------------------------

class TestCanonicalSince:
    def test_returns_string(self):
        from shared.intentional_wait import canonical_since

        assert isinstance(canonical_since(), str)

    def test_is_tz_aware_iso(self):
        from shared.intentional_wait import canonical_since

        value = canonical_since()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_seconds_precision_no_microseconds(self):
        from shared.intentional_wait import canonical_since

        value = canonical_since()
        assert "." not in value  # timespec="seconds" drops microseconds

    def test_round_trips_through_validate_wait(self):
        from shared.intentional_wait import canonical_since, validate_wait

        payload = {
            "reason": "awaiting_teachback_approved",
            "expected_resolver": "lead",
            "since": canonical_since(),
        }
        assert validate_wait(payload) is True

    def test_round_trips_through_wait_stale_as_fresh(self):
        """First-tick defense: auto-set payload must NOT be stale at emission."""
        from shared.intentional_wait import canonical_since, wait_stale

        payload = {
            "reason": "awaiting_teachback_approved",
            "expected_resolver": "lead",
            "since": canonical_since(),
        }
        assert wait_stale(payload) is False


# --- module constants ------------------------------------------------------

class TestModuleConstants:
    def test_default_threshold_is_30(self):
        from shared.intentional_wait import DEFAULT_THRESHOLD_MINUTES

        assert DEFAULT_THRESHOLD_MINUTES == 30

    def test_known_reasons_is_frozenset(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert isinstance(KNOWN_REASONS, frozenset)
        assert "awaiting_teachback_approved" in KNOWN_REASONS

    def test_known_resolvers_is_frozenset(self):
        from shared.intentional_wait import KNOWN_RESOLVERS

        assert isinstance(KNOWN_RESOLVERS, frozenset)
        assert {"lead", "peer", "user", "external"} <= KNOWN_RESOLVERS

    def test_reexports_from_shared_package(self):
        from shared import (
            canonical_since,
            validate_wait,
            wait_stale,
            DEFAULT_THRESHOLD_MINUTES,
            KNOWN_REASONS,
            KNOWN_RESOLVERS,
        )
        assert DEFAULT_THRESHOLD_MINUTES == 30
