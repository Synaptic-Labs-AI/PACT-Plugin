"""
Tests for shared.intentional_wait — validate_wait, wait_stale, canonical_since,
SELF_COMPLETE_EXEMPT_AGENT_TYPES, _is_exempt_agent_type, is_self_complete_exempt.

Coverage targets:
- validate_wait: non-dict inputs, missing/empty required keys, malformed since,
  tz-naive since (must reject), unknown keys (forward-compat), trailing Z vs +00:00,
  non-UTC offsets.
- wait_stale: fresh / stale / boundary / missing / malformed / future-dated,
  custom threshold, non-UTC offset age parity.
- canonical_since: shape and round-trip through validate_wait + wait_stale.
- SELF_COMPLETE_EXEMPT_AGENT_TYPES: shape, immutability, expected membership.
- _is_exempt_agent_type: positive match by team-config agentType lookup,
  fail-closed on every error path.
- is_self_complete_exempt: surface 1 (team-config agentType, requires
  team_name) and surface 2 (signal-task pattern, independent of team_name).
"""
import json
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


def _write_team_config(teams_dir, team_name, members):
    """Mirror of the auditor_reminder fixture pattern. Writes a minimal
    team config with the given members[] list and returns the teams_dir
    path as a string for passing to _is_exempt_agent_type / is_self_complete_exempt.
    """
    team_dir = Path(teams_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team_name, "members": members}),
        encoding="utf-8",
    )
    return str(teams_dir)


@pytest.fixture
def teams_dir(tmp_path):
    """Per-test temp teams directory; mirrors test_auditor_reminder.teams_dir."""
    d = tmp_path / "teams"
    d.mkdir()
    return str(d)


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


class TestSelfCompleteExemptAgentTypesConstant:
    def test_is_frozenset(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        assert isinstance(SELF_COMPLETE_EXEMPT_AGENT_TYPES, frozenset)

    def test_contains_pact_secretary(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        # Single canonical agentType — `secretary` was a name-alias and is
        # no longer canonical; the carve-out keys on team-config agentType.
        assert "pact-secretary" in SELF_COMPLETE_EXEMPT_AGENT_TYPES

    def test_does_not_contain_auditor(self):
        # Auditor exemption is signal-task pattern, NOT agentType.
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        assert "auditor" not in SELF_COMPLETE_EXEMPT_AGENT_TYPES
        assert "pact-auditor" not in SELF_COMPLETE_EXEMPT_AGENT_TYPES


class TestIsExemptAgentType:
    """Direct unit tests on _is_exempt_agent_type — the shared helper that
    backs both is_self_complete_exempt (surface 1) and
    wake_lifecycle._lifecycle_relevant (carve-out 2).
    """

    def test_owner_with_pact_secretary_agenttype_is_exempt(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type("session-secretary", "test-team", teams_dir) is True

    def test_owner_with_arbitrary_spawn_name_is_exempt(self, teams_dir):
        # Spawn-name freedom: any name reaches the carve-out as long as
        # the team config records its agentType. This is the central
        # behavioral change of #682.
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "secretary-from-mars", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type("secretary-from-mars", "test-team", teams_dir) is True

    def test_owner_with_non_secretary_agenttype_not_exempt(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
        ])
        assert _is_exempt_agent_type("backend-coder-1", "test-team", teams_dir) is False

    def test_owner_not_in_team_config_not_exempt(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        # Different owner — no member match, fail-closed.
        assert _is_exempt_agent_type("ghost-agent", "test-team", teams_dir) is False

    def test_empty_team_name_returns_false(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        # Fail-closed when team_name is empty — surface 1 cannot resolve.
        assert _is_exempt_agent_type("session-secretary", "", teams_dir) is False

    def test_empty_owner_returns_false(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type("", "test-team", teams_dir) is False

    def test_non_string_owner_returns_false(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        for bad in (None, 42, [], {}, True):
            assert _is_exempt_agent_type(bad, "test-team", teams_dir) is False  # type: ignore[arg-type]

    def test_non_string_team_name_returns_false(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        for bad in (None, 42, [], {}, True):
            assert _is_exempt_agent_type("session-secretary", bad, teams_dir) is False  # type: ignore[arg-type]

    def test_missing_team_config_fails_closed(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        # No config written — _iter_members returns []; no match; fail-closed.
        assert _is_exempt_agent_type("session-secretary", "ghost-team", teams_dir) is False

    def test_malformed_team_config_fails_closed(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        team_dir = Path(teams_dir) / "bad-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not valid json {{{", encoding="utf-8")
        assert _is_exempt_agent_type("session-secretary", "bad-team", teams_dir) is False

    def test_missing_agenttype_field_fails_closed(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        # Member entry without agentType key — fail-closed.
        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary"},
        ])
        assert _is_exempt_agent_type("session-secretary", "test-team", teams_dir) is False

    def test_empty_agenttype_value_fails_closed(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        # agentType="" is not in SELF_COMPLETE_EXEMPT_AGENT_TYPES → False.
        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": ""},
        ])
        assert _is_exempt_agent_type("session-secretary", "test-team", teams_dir) is False

    def test_non_string_agenttype_fails_closed(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": 42},
        ])
        assert _is_exempt_agent_type("session-secretary", "test-team", teams_dir) is False


class TestIsSelfCompleteExempt:
    def test_owner_with_pact_secretary_agenttype_is_exempt(self, teams_dir):
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_secretary_spawn_name_freedom(self, teams_dir):
        # The behavioral change of #682: spawn name no longer determines
        # exemption — agentType in team config does.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "team-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "team-secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_backend_coder_is_not_exempt(self, teams_dir):
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
        ])
        task = {"owner": "backend-coder-1", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is False

    def test_owner_named_secretary_without_agenttype_not_exempt(self, teams_dir):
        # Critical behavioral change: a teammate spoofing owner="secretary"
        # cannot self-promote without the team config recording the
        # privileged agentType. Fail-closed on missing-from-config.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
        ])
        task = {"owner": "secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is False

    def test_missing_team_name_falls_through_surface_1(self, teams_dir):
        # team_name="" short-circuits surface 1 to False (fail-closed),
        # but surface 2 (signal-task) still evaluates.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": {}}
        assert is_self_complete_exempt(task) is False  # team_name="" default
        # Surface 2 still works without team_name.
        sig = {"owner": "anyone", "metadata": {"completion_type": "signal", "type": "blocker"}}
        assert is_self_complete_exempt(sig) is True

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

    def test_metadata_none_with_exempt_agenttype_still_exempt(self, teams_dir):
        from shared.intentional_wait import is_self_complete_exempt

        # Surface 1 (team-config agentType) still triggers even with
        # metadata=None — the predicate guards against non-dict metadata
        # before the agentType lookup.
        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": None}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_self_completion_with_corrupted_metadata_for_secretary(self, teams_dir):
        """Pin the asymmetric carve-out hoist behavior between
        is_self_complete_exempt and _lifecycle_relevant.

        The two predicates handle "secretary task with corrupted (non-dict
        truthy) metadata" differently because they hoist the agentType
        carve-out at different points relative to the metadata-shape gate:

        - `_lifecycle_relevant` (wake_lifecycle.py): hoists agentType
          check ABOVE the metadata-shape gate. A secretary task with
          `metadata="garbage"` returns False (carve-out applied → not
          counted toward wake-arming). Correct behavior.

        - `is_self_complete_exempt` (intentional_wait.py): metadata-shape
          gate fires FIRST. Same input returns False (NOT exempt → the
          self_completion advisory will fire). False-positive: the
          advisory is noisy for a secretary task.

        Operationally: a secretary task with corrupted metadata is rare
        (metadata is harness-written; corruption requires explicit
        teammate action or disk corruption). The asymmetry leans
        CONSERVATIVE in is_self_complete_exempt (false-positive advisory
        is noisy, not unsafe) and the advisory is non-blocking.

        This test PINS the current asymmetric behavior so a future
        predicate refactor (e.g., aligning the hoist to mirror
        _lifecycle_relevant) cannot silently change it without updating
        this test alongside the refactor. If the hoist is later aligned,
        update this test's expected result from False to True and add a
        cross-reference to the alignment commit.

        Code-side hoist alignment is a separate consideration outside
        #682's scope and tracked via SendMessage to review-backend-coder
        rather than implemented here.
        """
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        # Non-dict-truthy metadata: bypasses the `metadata = ... or {}`
        # coalescion (because "garbage" is truthy) but fails the
        # subsequent isinstance(metadata, dict) gate. The agentType
        # lookup is never reached.
        task = {"owner": "session-secretary", "metadata": "garbage"}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is False, (
            "Pinned current behavior: is_self_complete_exempt returns False "
            "(NOT exempt) for a secretary task with non-dict-truthy "
            "metadata, because the metadata-shape gate fires before the "
            "agentType lookup. If a future refactor aligns the hoist to "
            "mirror _lifecycle_relevant (agentType check ABOVE metadata "
            "gate), update this test's expected result and add a "
            "cross-reference to the alignment commit."
        )
        # Confirm the asymmetry is real by comparing with the sibling
        # predicate _lifecycle_relevant on identical input.
        import shared.wake_lifecycle as wl
        # _lifecycle_relevant requires Path.home()-rooted teams_dir to
        # find the team config. Use a minimal monkeypatch-free approach
        # by construct the full path lookup. Skip: the asymmetry pin
        # via is_self_complete_exempt alone is sufficient regression
        # coverage; cross-predicate equivalence is a separate concern
        # belonging to wake_lifecycle's own test suite. The wake-side
        # behavior is already pinned by
        # test_lifecycle_relevant_exempt_agenttype_with_corrupted_metadata
        # in test_inbox_wake_lifecycle_helper.py.
        _ = wl  # keep import to make divergence-target explicit in test source

    def test_owner_none_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # owner=None is not a string → isinstance check fails → no exemption.
        assert is_self_complete_exempt({"owner": None, "metadata": {}}) is False

    def test_owner_empty_string_returns_false(self, teams_dir):
        from shared.intentional_wait import is_self_complete_exempt

        # Empty string fails the owner-shape check inside _is_exempt_agent_type.
        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is False


class TestIsSelfCompleteExemptDualCarveOutIndependence:
    """Both exemption surfaces must work independently AND together.

    Surface 1: SELF_COMPLETE_EXEMPT_AGENT_TYPES membership via team-config
               agentType lookup (requires team_name).
    Surface 2: signal-task pattern (completion_type=signal + type in
               {blocker, algedonic}). Independent of team_name.

    Reverting EITHER surface in production must surface as independent test failures.
    """

    def test_only_signal_task_path_no_exempt_agenttype(self, teams_dir):
        # Auditor signal-task: agentType NOT exempt, but signal pattern exempts.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "pact-auditor", "agentType": "pact-auditor"},
        ])
        task = {
            "owner": "pact-auditor",
            "metadata": {"completion_type": "signal", "type": "algedonic"},
        }
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_only_exempt_agenttype_path_no_signal_task(self, teams_dir):
        # Secretary memory-save: agentType in exempt set, no signal-task metadata.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": {"completion_type": "regular"}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_both_paths_match_still_exempt(self, teams_dir):
        # Defense-in-depth: secretary on a signal-task is exempt via either surface.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {
            "owner": "session-secretary",
            "metadata": {"completion_type": "signal", "type": "blocker"},
        }
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_neither_path_matches_not_exempt(self, teams_dir):
        # Backend-coder doing regular work is NOT exempt via either surface.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
        ])
        task = {
            "owner": "backend-coder-1",
            "metadata": {"completion_type": "regular", "type": "feature"},
        }
        assert is_self_complete_exempt(task, "test-team", teams_dir) is False


class TestSelfCompleteExemptAgentTypesImmutability:
    """frozenset chosen specifically to prevent accidental mutation; pin that."""

    def test_add_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENT_TYPES.add("new-agent-type")

    def test_remove_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENT_TYPES.remove("pact-secretary")

    def test_clear_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENT_TYPES.clear()

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


class TestIsExemptAgentTypeDefaultTeamsDir:
    """teams_dir=None default-path coverage: when the override is omitted,
    `_iter_members` must resolve via Path.home()/.claude/teams/. The 12-case
    TestIsExemptAgentType class above passes teams_dir explicitly; this
    class exercises the production default path so a future regression that
    bypasses `_iter_members` (e.g. inlines its own path) fails loudly.
    """

    def test_default_teams_dir_resolves_via_path_home(self, tmp_path, monkeypatch):
        from shared.intentional_wait import _is_exempt_agent_type

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team_dir = tmp_path / ".claude" / "teams" / "default-path-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": "default-path-team", "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )
        # teams_dir omitted → exercises _iter_members default branch.
        assert _is_exempt_agent_type(
            "session-secretary", "default-path-team"
        ) is True

    def test_default_teams_dir_missing_config_fails_closed(
        self, tmp_path, monkeypatch
    ):
        from shared.intentional_wait import _is_exempt_agent_type

        # Path.home() points to tmp_path with no .claude/teams set up.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Default-path resolution must fail-closed without raising.
        assert _is_exempt_agent_type(
            "session-secretary", "ghost-team"
        ) is False

    def test_is_self_complete_exempt_default_teams_dir(
        self, tmp_path, monkeypatch
    ):
        from shared.intentional_wait import is_self_complete_exempt

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team_dir = tmp_path / ".claude" / "teams" / "default-path-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": "default-path-team", "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )
        # Both teams_dir omitted; `team_name` provided.
        task = {"owner": "session-secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "default-path-team") is True


class TestIsExemptAgentTypeMixedTeamConfig:
    """Multi-member team config: the predicate must match BY name AND verify
    THAT member's agentType. A team with both secretary and non-secretary
    members must not exempt the non-secretary owner just because the team
    config records an exempt agentType somewhere.
    """

    def test_only_matching_member_drives_exemption(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "mixed-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
            {"name": "test-engineer-1", "agentType": "pact-test-engineer"},
        ])
        assert _is_exempt_agent_type(
            "session-secretary", "mixed-team", teams_dir
        ) is True
        assert _is_exempt_agent_type(
            "backend-coder-1", "mixed-team", teams_dir
        ) is False
        assert _is_exempt_agent_type(
            "test-engineer-1", "mixed-team", teams_dir
        ) is False

    def test_first_member_match_wins_when_duplicate_names(self, teams_dir):
        # Pathological config (should never happen in production, but pin
        # the defensive behavior): if two members share a name, the first
        # match short-circuits via _iter_members iteration order. Pin
        # current behavior so a future _iter_members refactor surfaces
        # any iteration-order change.
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "dup-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
            {"name": "session-secretary", "agentType": "pact-backend-coder"},
        ])
        # First match wins — first member has exempt agentType.
        assert _is_exempt_agent_type(
            "session-secretary", "dup-team", teams_dir
        ) is True

    def test_silent_first_wins_symmetric_under_reverse_order(self, teams_dir):
        """Silent-first-wins adversarial counter-test (TE-M3).

        Sibling of test_first_member_match_wins_when_duplicate_names.
        That test pins the secretary-first ordering produces True. This
        test pins the SYMMETRIC case: with the SAME pair of duplicate
        names but with the non-secretary entry FIRST, the predicate
        returns False — proving the behavior is genuinely
        first-match-wins, not "any-match-wins".

        Without this counter-test, an alternative-but-incorrect
        implementation that returned True if ANY duplicate matched the
        exempt set would pass the secretary-first test and silently
        permit non-secretary teammates to self-complete by colliding on
        a privileged name. The pair of tests together pin the
        order-sensitivity of `_iter_members` iteration as a
        load-bearing invariant.

        Pathological config (should never happen in production); this
        pins defensive behavior so a future _iter_members refactor that
        normalizes/dedupes member entries (e.g., a `set()` collapse)
        surfaces as an explicit test failure rather than silent
        privilege drift.
        """
        from shared.intentional_wait import _is_exempt_agent_type

        # Reverse order: non-secretary first, secretary second. Same
        # name on both. First match wins → predicate returns False
        # (first member's agentType is NOT in the exempt set).
        _write_team_config(teams_dir, "dup-team-reversed", [
            {"name": "session-secretary", "agentType": "pact-backend-coder"},
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type(
            "session-secretary", "dup-team-reversed", teams_dir
        ) is False, (
            "Reverse-order duplicate-name config should resolve to the "
            "FIRST member's agentType (pact-backend-coder, NOT exempt). "
            "If this returns True, the predicate is doing 'any-match-wins' "
            "rather than 'first-match-wins' — a privilege-drift defect."
        )

    def test_count_active_tasks_isolates_per_member(self, teams_dir, tmp_path, monkeypatch):
        # POST-EMPTY-CARVE-OUT: in a multi-member team with one secretary
        # and one non-secretary teammate, two simultaneous in_progress
        # tasks (one each) must now produce count=2 because
        # WAKE_EXCLUDED_AGENT_TYPES is empty (secretary tasks count).
        # Pre-empty this test asserted count=1 (secretary excluded);
        # post-empty both members' tasks count toward the active tally.
        import shared.wake_lifecycle as wl

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "multi-task-team"
        team_dir = tmp_path / ".claude" / "teams" / team
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": team, "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
                {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
            ]}),
            encoding="utf-8",
        )
        tasks_dir = tmp_path / ".claude" / "tasks" / team
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "1.json").write_text(json.dumps(
            {"id": "1", "status": "in_progress", "owner": "session-secretary"}
        ))
        (tasks_dir / "2.json").write_text(json.dumps(
            {"id": "2", "status": "in_progress", "owner": "backend-coder-1"}
        ))
        # Both tasks count post-empty (secretary no longer wake-excluded).
        assert wl.count_active_tasks(team) == 2


class TestMultipleSecretaryTasksAllCountPostEmptyCarveOut:
    """POST-EMPTY-CARVE-OUT: cross-task interaction. A team running
    multiple parallel memory-save tasks owned by the same secretary
    teammate (e.g., pre-CODE harvest + post-CODE harvest in the same
    wave) all COUNT toward the wake-mechanism active tally because
    WAKE_EXCLUDED_AGENT_TYPES is empty.

    Pre-empty: this class asserted secretary tasks were all exempt
    (count == 0 regardless of how many secretary tasks). Post-empty:
    each secretary task counts; the count gate handles the Bug A
    secretary-window scenario at the count layer rather than the
    per-owner carve-out layer.

    Pins that the predicate is stateless across task iteration —
    the post-empty count == N for N parallel secretary tasks."""

    def test_multiple_secretary_tasks_all_count_post_empty(self, tmp_path, monkeypatch):
        import shared.wake_lifecycle as wl

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "multi-sec-team"
        team_dir = tmp_path / ".claude" / "teams" / team
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": team, "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )
        tasks_dir = tmp_path / ".claude" / "tasks" / team
        tasks_dir.mkdir(parents=True)
        # Three concurrent secretary memory-save tasks.
        for tid in ("harvest-1", "harvest-2", "harvest-3"):
            (tasks_dir / f"{tid}.json").write_text(json.dumps(
                {"id": tid, "status": "in_progress", "owner": "session-secretary"}
            ))
        # All three count post-empty (predicate is stateless across iteration).
        assert wl.count_active_tasks(team) == 3


class TestDocSurfaceStalenessSweep:
    """Doc-staleness regression pin (lead-approved per #682 TEST phase).

    The OLD constant name `SELF_COMPLETE_EXEMPT_AGENTS` (sans `_TYPES`
    suffix) must NOT appear in any agent-facing doc surface
    (agents/, commands/, protocols/) AS A LIVE INSTRUCTIONAL REFERENCE.
    Manual sweep via `rg "SELF_COMPLETE_EXEMPT_AGENTS\\b" pact-plugin/`
    was the architect's risk-mitigation; this test converts it to
    automated regression coverage.

    Three context exclusions are honored so legitimate deprecation
    notices and historical references don't false-positive:

    1. Markdown blockquote deprecation marker — a line beginning with
       `> Deprecated:` (with surrounding whitespace tolerated).
       Documents intentional historical references like
       `> Deprecated: see SELF_COMPLETE_EXEMPT_AGENTS in v3.x`.
    2. HTML comment with `old name:` prefix — anywhere in the line.
       Allows `<!-- old name: SELF_COMPLETE_EXEMPT_AGENTS -->` style
       cross-reference annotations.
    3. Inside fenced code blocks — content between ```...``` fences
       (any language hint). Snippets quoting old code are documentation,
       not live instruction.

    Counter-tested by both directions:
    - `test_genuine_staleness_pattern_caught` — bare-prose mention DOES fire.
    - `test_deprecation_blockquote_skipped` — `> Deprecated:` line does NOT.
    - `test_old_name_html_comment_skipped` — `<!-- old name: ... -->` does NOT.
    - `test_fenced_code_block_skipped` — content inside ```...``` does NOT.

    The single permitted reference outside doc surfaces is the
    negative-assertion in test_inbox_wake_lifecycle_helper.py:48
    (`assert "SELF_COMPLETE_EXEMPT_AGENTS" not in src`), which is itself
    a guard against the old name reappearing in wake_lifecycle.py.

    If a future refactor genuinely needs to rename
    SELF_COMPLETE_EXEMPT_AGENT_TYPES, update this test alongside the
    rename.
    """
    import re

    OLD_NAME_PATTERN = re.compile(r"\bSELF_COMPLETE_EXEMPT_AGENTS\b")
    # `> Deprecated:` blockquote marker, leading whitespace tolerated.
    DEPRECATION_BLOCKQUOTE_PATTERN = re.compile(r"^\s*>\s*Deprecated:")
    # `<!-- old name:` HTML comment marker (case-insensitive on `old name`),
    # may appear anywhere in the line; handles `<!--old name:` (no space) too.
    OLD_NAME_COMMENT_PATTERN = re.compile(r"<!--\s*old name:", re.IGNORECASE)
    # Markdown fenced-code-block delimiter — three+ backticks at line start
    # (after optional whitespace), with optional language hint.
    FENCE_PATTERN = re.compile(r"^\s*```")

    def _doc_root(self) -> Path:
        # tests/ → pact-plugin/
        return Path(__file__).resolve().parent.parent

    def _skipped_line(self, line: str, in_fence: bool) -> bool:
        """Return True iff this line should be skipped for staleness
        detection — fenced-code-block content, deprecation blockquote,
        or HTML `old name:` cross-reference comment.
        """
        if in_fence:
            return True
        if self.DEPRECATION_BLOCKQUOTE_PATTERN.search(line):
            return True
        if self.OLD_NAME_COMMENT_PATTERN.search(line):
            return True
        return False

    @pytest.mark.parametrize("subdir", ["agents", "commands", "protocols"])
    def test_no_old_name_in_doc_surface(self, subdir):
        root = self._doc_root() / subdir
        assert root.is_dir(), f"Expected doc directory {root} to exist"
        violations = []
        for md_path in root.rglob("*.md"):
            content = md_path.read_text(encoding="utf-8")
            in_fence = False
            for line_no, line in enumerate(content.splitlines(), start=1):
                # Track fenced-code-block state. A line that IS a fence
                # delimiter toggles the state; that line itself is treated
                # as inside-fence (snippets like ```python with the OLD
                # constant in the same line shouldn't false-positive).
                if self.FENCE_PATTERN.search(line):
                    in_fence = not in_fence
                    continue
                if self._skipped_line(line, in_fence):
                    continue
                if self.OLD_NAME_PATTERN.search(line):
                    violations.append(f"{md_path.relative_to(root.parent)}:{line_no}: {line.strip()}")
        assert not violations, (
            "Stale `SELF_COMPLETE_EXEMPT_AGENTS` references found in doc "
            "surface — must be retargeted to `SELF_COMPLETE_EXEMPT_AGENT_TYPES`:\n"
            + "\n".join(violations)
        )

    # ---- B1 narrowed-regex counter-tests ----

    def _scan_lines(self, lines: list[str]) -> list[int]:
        """Replicate the production scan logic on an in-memory line list.
        Returns the 1-indexed line numbers that would be flagged as
        violations (i.e., live OLD-name references not skipped by a
        context-exclusion rule).
        """
        violations: list[int] = []
        in_fence = False
        for line_no, line in enumerate(lines, start=1):
            if self.FENCE_PATTERN.search(line):
                in_fence = not in_fence
                continue
            if self._skipped_line(line, in_fence):
                continue
            if self.OLD_NAME_PATTERN.search(line):
                violations.append(line_no)
        return violations

    def test_genuine_staleness_pattern_caught(self):
        # Bare prose mention of OLD constant in a plain doc line MUST fire.
        lines = [
            "## Self-completion carve-out",
            "",
            "Owners listed in SELF_COMPLETE_EXEMPT_AGENTS bypass the advisory.",
        ]
        assert self._scan_lines(lines) == [3]

    def test_deprecation_blockquote_skipped(self):
        # `> Deprecated:` blockquote referencing the OLD name is a
        # legitimate historical pointer — must NOT fire.
        lines = [
            "## Self-completion carve-out",
            "",
            "> Deprecated: SELF_COMPLETE_EXEMPT_AGENTS (v3.x). See "
            "SELF_COMPLETE_EXEMPT_AGENT_TYPES below.",
            "",
            "Carve-out is keyed on agentType.",
        ]
        assert self._scan_lines(lines) == []

    def test_old_name_html_comment_skipped(self):
        # `<!-- old name: ... -->` HTML cross-reference annotation is
        # legitimate — must NOT fire.
        lines = [
            "## Self-completion carve-out",
            "",
            "<!-- old name: SELF_COMPLETE_EXEMPT_AGENTS, retired in #682 -->",
            "",
            "Carve-out is keyed on agentType.",
        ]
        assert self._scan_lines(lines) == []

    def test_fenced_code_block_skipped(self):
        # Content inside ```...``` fences quoting old code is
        # documentation, not live instruction — must NOT fire.
        lines = [
            "## Migration",
            "",
            "Pre-#682 the carve-out was keyed on owner name:",
            "",
            "```python",
            "if owner in SELF_COMPLETE_EXEMPT_AGENTS:",
            "    return True",
            "```",
            "",
            "Post-#682 it keys on team-config agentType.",
        ]
        assert self._scan_lines(lines) == []

    def test_fence_state_toggles_correctly(self):
        # Two separate fenced blocks; OLD name only inside the first.
        # No live mention before/between/after — must NOT fire.
        lines = [
            "```",
            "SELF_COMPLETE_EXEMPT_AGENTS",
            "```",
            "Outside.",
            "```",
            "another snippet",
            "```",
        ]
        assert self._scan_lines(lines) == []

    def test_live_mention_after_closed_fence_caught(self):
        # If a fenced block closes and a later line has a bare mention,
        # the fence-state must be False by then; the mention MUST fire.
        lines = [
            "```",
            "old code",
            "```",
            "Live: SELF_COMPLETE_EXEMPT_AGENTS still mentioned in prose.",
        ]
        assert self._scan_lines(lines) == [4]

    def test_skip_predicates_do_not_mask_new_name_baseline(self):
        # Sanity: a doc line containing ONLY the NEW name (no exclusion
        # context) must not be flagged. Guards against an inverted
        # predicate accidentally suppressing the NEW name.
        lines = [
            "Reference SELF_COMPLETE_EXEMPT_AGENT_TYPES in shared module.",
        ]
        assert self._scan_lines(lines) == []

    def test_new_name_present_in_at_least_one_canonical_doc(self):
        # Counter-pin: at least one of the 5 known doc surfaces must
        # mention the new constant name. Guards against a refactor that
        # silently drops the doc references entirely.
        root = self._doc_root()
        canonical_surfaces = [
            root / "agents" / "pact-orchestrator.md",
            root / "agents" / "pact-secretary.md",
            root / "commands" / "comPACT.md",
            root / "commands" / "orchestrate.md",
            root / "protocols" / "pact-completion-authority.md",
        ]
        new_name = "SELF_COMPLETE_EXEMPT_AGENT_TYPES"
        hits = [
            p for p in canonical_surfaces
            if p.is_file() and new_name in p.read_text(encoding="utf-8")
        ]
        assert hits, (
            f"None of the 5 canonical doc surfaces references "
            f"`{new_name}`; doc-surface drift suspected."
        )
