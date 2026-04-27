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
        # Top-level re-export: only the staleness predicate is on the
        # shared package's public API. Vocabulary + format helpers stay
        # module-only to keep the shared package namespace small.
        from shared import wait_stale  # noqa: F401
        from shared.intentional_wait import (
            canonical_since,  # noqa: F401
            validate_wait,  # noqa: F401
            DEFAULT_THRESHOLD_MINUTES,
            KNOWN_REASONS,  # noqa: F401
            KNOWN_RESOLVERS,  # noqa: F401
        )
        assert DEFAULT_THRESHOLD_MINUTES == 30


# --- prose-vs-code drift pin ----------------------------------------------

class TestSkillMdProseSnippetConformance:
    """Drift-pin: the SKILL.md prose snippet for `since` must stay in lockstep
    with code semantics. If prose says "use datetime.now(timezone.utc).isoformat(
    timespec='seconds')" but validate_wait later rejects that output (or
    wait_stale classifies it stale), teammates will follow the prose and hit
    silent nag-resume. Execute the prose snippet verbatim and assert.
    """

    def test_prose_snippet_output_is_fresh_and_valid(self):
        from shared.intentional_wait import validate_wait, wait_stale

        # Verbatim the SKILL.md "Intentional Waiting" prose snippet:
        since_value = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "reason": "awaiting_teachback_approved",
            "expected_resolver": "lead",
            "since": since_value,
        }
        assert validate_wait(payload) is True
        assert wait_stale(payload) is False


# --- self-complete exemption -----------------------------------------------

class TestKnownReasonsCompletionAddition:
    def test_awaiting_lead_completion_in_known_reasons(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert "awaiting_lead_completion" in KNOWN_REASONS

    def test_awaiting_lead_completion_passes_validate_wait(self):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait(reason="awaiting_lead_completion")
        assert validate_wait(payload) is True


class TestSelfCompleteExemptAgentsConstant:
    def test_is_frozenset(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        assert isinstance(SELF_COMPLETE_EXEMPT_AGENTS, frozenset)

    def test_contains_secretary_aliases(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        assert "pact-secretary" in SELF_COMPLETE_EXEMPT_AGENTS
        assert "secretary" in SELF_COMPLETE_EXEMPT_AGENTS

    def test_does_not_contain_auditor(self):
        # Auditor exemption is signal-task pattern, NOT agent-type.
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        assert "auditor" not in SELF_COMPLETE_EXEMPT_AGENTS
        assert "pact-auditor" not in SELF_COMPLETE_EXEMPT_AGENTS


class TestIsSelfCompleteExempt:
    def test_secretary_owner_is_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        assert is_self_complete_exempt({"owner": "secretary", "metadata": {}}) is True
        assert is_self_complete_exempt({"owner": "pact-secretary", "metadata": {}}) is True

    def test_dispatch_agent_metadata_is_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        task = {"owner": "secretary-3", "metadata": {"dispatch_agent": "pact-secretary"}}
        assert is_self_complete_exempt(task) is True

    def test_backend_coder_is_not_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        assert is_self_complete_exempt({"owner": "backend-coder-1", "metadata": {}}) is False

    def test_signal_task_blocker_is_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "auditor-1",
            "metadata": {"completion_type": "signal", "type": "blocker"},
        }
        assert is_self_complete_exempt(task) is True

    def test_signal_task_algedonic_is_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "any-agent",
            "metadata": {"completion_type": "signal", "type": "algedonic"},
        }
        assert is_self_complete_exempt(task) is True

    def test_signal_task_other_type_is_not_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "any-agent",
            "metadata": {"completion_type": "signal", "type": "progress"},
        }
        assert is_self_complete_exempt(task) is False

    def test_completion_type_without_signal_marker_not_exempt(self):
        # type=blocker alone (without completion_type=signal) is NOT a signal-task.
        from shared.intentional_wait import is_self_complete_exempt

        task = {"owner": "any-agent", "metadata": {"type": "blocker"}}
        assert is_self_complete_exempt(task) is False

    def test_malformed_input_no_raise_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        assert is_self_complete_exempt(None) is False
        assert is_self_complete_exempt("not a dict") is False
        assert is_self_complete_exempt([]) is False
        assert is_self_complete_exempt({}) is False
        assert is_self_complete_exempt({"metadata": "not a dict"}) is False
        assert is_self_complete_exempt({"owner": 42, "metadata": {}}) is False


class TestIsSelfCompleteExemptMalformedTaskShapes:
    """Edge cases for malformed task dicts — defensive defaults must hold."""

    def test_no_metadata_key_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # `metadata` key absent entirely. owner is non-exempt.
        assert is_self_complete_exempt({"owner": "backend-coder"}) is False

    def test_metadata_explicit_none_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # metadata=None coalesces to {} via `metadata = task.get("metadata") or {}`.
        # Owner is non-exempt → returns False.
        assert is_self_complete_exempt({"owner": "backend-coder", "metadata": None}) is False

    def test_metadata_none_with_secretary_owner_still_exempt(self):
        from shared.intentional_wait import is_self_complete_exempt

        # Surface 1 (owner) still triggers even with metadata=None.
        assert is_self_complete_exempt({"owner": "secretary", "metadata": None}) is True

    def test_owner_none_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # owner=None is not a string → isinstance check fails → no exemption.
        assert is_self_complete_exempt({"owner": None, "metadata": {}}) is False

    def test_owner_empty_string_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # Empty string is not in SELF_COMPLETE_EXEMPT_AGENTS → no exemption.
        assert is_self_complete_exempt({"owner": "", "metadata": {}}) is False

    def test_dispatch_agent_none_falls_through_to_owner(self):
        from shared.intentional_wait import is_self_complete_exempt

        # dispatch_agent=None → not str → skip; owner=secretary → exempt.
        task = {"owner": "secretary", "metadata": {"dispatch_agent": None}}
        assert is_self_complete_exempt(task) is True

    def test_dispatch_agent_non_str_falls_through_to_owner(self):
        from shared.intentional_wait import is_self_complete_exempt

        # dispatch_agent=int → not str → skip; owner=secretary → exempt.
        task = {"owner": "secretary", "metadata": {"dispatch_agent": 42}}
        assert is_self_complete_exempt(task) is True

    def test_dispatch_agent_empty_string_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # Empty string is not in SELF_COMPLETE_EXEMPT_AGENTS; non-exempt owner.
        task = {"owner": "backend-coder", "metadata": {"dispatch_agent": ""}}
        assert is_self_complete_exempt(task) is False


class TestIsSelfCompleteExemptDualCarveOutIndependence:
    """Both exemption surfaces must work independently AND together.

    Surface 1: SELF_COMPLETE_EXEMPT_AGENTS membership (by agent type).
    Surface 2: signal-task pattern (completion_type=signal + type in {blocker, algedonic}).

    Reverting EITHER surface in production must surface as independent test failures.
    """

    def test_only_signal_task_path_no_exempt_agent(self):
        # Auditor signal-task: agent NOT in exempt set, but signal pattern exempts.
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "pact-auditor",
            "metadata": {"completion_type": "signal", "type": "algedonic"},
        }
        assert is_self_complete_exempt(task) is True

    def test_only_exempt_agent_path_no_signal_task(self):
        # Secretary memory-save: agent in exempt set, no signal-task metadata.
        from shared.intentional_wait import is_self_complete_exempt

        task = {"owner": "secretary", "metadata": {"completion_type": "regular"}}
        assert is_self_complete_exempt(task) is True

    def test_both_paths_match_still_exempt(self):
        # Defense-in-depth: secretary on a signal-task is exempt via either surface.
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "secretary",
            "metadata": {"completion_type": "signal", "type": "blocker"},
        }
        assert is_self_complete_exempt(task) is True

    def test_neither_path_matches_not_exempt(self):
        # Backend-coder doing regular work is NOT exempt via either surface.
        from shared.intentional_wait import is_self_complete_exempt

        task = {
            "owner": "backend-coder",
            "metadata": {"completion_type": "regular", "type": "feature"},
        }
        assert is_self_complete_exempt(task) is False


class TestSelfCompleteExemptAgentsImmutability:
    """frozenset chosen specifically to prevent accidental mutation; pin that."""

    def test_add_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENTS.add("new-agent")

    def test_remove_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENTS.remove("secretary")

    def test_clear_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENTS.clear()

    def test_known_reasons_immutable(self):
        # KNOWN_REASONS must also be frozen — same accidental-mutation concern.
        from shared.intentional_wait import KNOWN_REASONS

        with pytest.raises(AttributeError):
            KNOWN_REASONS.add("awaiting_something_new")


class TestKnownReasonsLiteralRegressionGuard:
    """Pin the exact set of known reasons. Any silent removal/rename must fail loudly.

    This is a documentation-in-code test: the contract published to teammates
    via the pact-agent-teams skill names these strings. Silent renaming would
    break in-flight teammate metadata writes without surfacing a build error.
    """

    EXPECTED_REASONS = {
        "awaiting_teachback_approved",
        "awaiting_lead_commit",
        "awaiting_amendment_review",
        "awaiting_post_handoff_decision",
        "awaiting_peer_response",
        "awaiting_user_decision",
        "awaiting_blocker_resolution",
        "awaiting_lead_completion",
    }

    def test_exact_set(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert set(KNOWN_REASONS) == self.EXPECTED_REASONS

    @pytest.mark.parametrize("reason", sorted(EXPECTED_REASONS))
    def test_each_reason_validates(self, reason):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait(reason=reason)
        assert validate_wait(payload) is True

    def test_awaiting_lead_completion_is_present(self):
        # Specifically pin the new addition — guards against revert.
        from shared.intentional_wait import KNOWN_REASONS

        assert "awaiting_lead_completion" in KNOWN_REASONS


