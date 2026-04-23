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
        # Top-level re-exports are the minimal public API: the two predicates
        # consumers need. Vocabulary + format helpers stay module-only to keep
        # the shared package namespace small.
        from shared import should_silence_stall_nag, wait_stale
        from shared.intentional_wait import (
            canonical_since,
            validate_wait,
            DEFAULT_THRESHOLD_MINUTES,
            KNOWN_REASONS,
            KNOWN_RESOLVERS,
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


# --- is_signal_task -------------------------------------------------------

class TestIsSignalTask:
    def test_blocker_is_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({"metadata": {"type": "blocker"}}) is True

    def test_algedonic_is_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({"metadata": {"type": "algedonic"}}) is True

    def test_other_type_is_not_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({"metadata": {"type": "handoff"}}) is False
        assert is_signal_task({"metadata": {"type": "approval"}}) is False

    def test_missing_type_is_not_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({"metadata": {}}) is False

    def test_missing_metadata_is_not_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({}) is False

    def test_non_dict_is_not_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task(None) is False
        assert is_signal_task("blocker") is False
        assert is_signal_task(42) is False
        assert is_signal_task([]) is False

    def test_non_dict_metadata_is_not_signal(self):
        from shared.intentional_wait import is_signal_task

        assert is_signal_task({"metadata": None}) is False
        assert is_signal_task({"metadata": "blocker"}) is False


# --- should_silence_stall_nag --------------------------------------------

class TestShouldSilenceStallNag:
    def test_signal_task_is_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag({"metadata": {"type": "blocker"}}) is True
        assert should_silence_stall_nag({"metadata": {"type": "algedonic"}}) is True

    def test_stalled_is_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag({"metadata": {"stalled": True}}) is True

    def test_fresh_intentional_wait_is_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag(
            {"metadata": {"intentional_wait": _fresh_wait()}}
        ) is True

    def test_stale_intentional_wait_is_not_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        stale_wait = _fresh_wait(
            since=_iso(now - timedelta(minutes=31)),
        )
        assert should_silence_stall_nag(
            {"metadata": {"intentional_wait": stale_wait}}, _now=now
        ) is False

    def test_malformed_intentional_wait_is_not_silenced(self):
        # A malformed flag fails loud: wait_stale returns True → no silence
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag(
            {"metadata": {"intentional_wait": {"reason": "x"}}}
        ) is False

    def test_empty_metadata_is_not_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag({"metadata": {}}) is False

    def test_missing_metadata_is_not_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag({}) is False

    def test_non_dict_is_not_silenced(self):
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag(None) is False
        assert should_silence_stall_nag("stalled") is False
        assert should_silence_stall_nag([]) is False

    def test_signal_takes_precedence_over_everything(self):
        """Signal-task silences even if stalled=False and no intentional_wait."""
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag(
            {"metadata": {"type": "blocker", "stalled": False}}
        ) is True

    def test_multiple_triggers_still_silences(self):
        """Any one trigger is sufficient; multiple are fine."""
        from shared.intentional_wait import should_silence_stall_nag

        assert should_silence_stall_nag(
            {"metadata": {"type": "algedonic", "stalled": True,
                          "intentional_wait": _fresh_wait()}}
        ) is True

    def test_custom_threshold_respected(self):
        from shared.intentional_wait import should_silence_stall_nag

        now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
        wait = _fresh_wait(since=_iso(now - timedelta(minutes=10)))
        # 10 min elapsed; threshold=5 → stale → not silenced;
        # threshold=15 → fresh → silenced
        assert should_silence_stall_nag(
            {"metadata": {"intentional_wait": wait}},
            threshold_minutes=5, _now=now,
        ) is False
        assert should_silence_stall_nag(
            {"metadata": {"intentional_wait": wait}},
            threshold_minutes=15, _now=now,
        ) is True


# --- structural drift-pin: signal-task literal lives in exactly one place --

class TestSignalTaskLiteralPin:
    """Structural guardrail: the `("blocker", "algedonic")` tuple-literal
    (and its private module constant) must live only in
    shared/intentional_wait.py. If a consumer file reintroduces the literal
    inline, the cross-hook silencer asymmetry can silently return.

    #538 C2a scope: the test's `refactored_files` list was scoped to three
    pre-#538 hooks (detect_stall, _scan_owned_tasks, handoff_gate). Two of
    those — teammate_completion_gate.py and handoff_gate.py — are deleted
    in C2a; agent_handoff_emitter.py replaces handoff_gate.py but
    intentionally holds the inline literal (matches task_utils.py:184 +
    session_resume.py:525 convention). C3 deletes this class entirely
    when is_signal_task itself is removed.
    """

    def test_signal_task_literal_lives_in_helper_only(self):
        import re
        from pathlib import Path

        hooks_root = Path(__file__).parent.parent / "hooks"
        pattern = re.compile(
            r"""\(\s*["']blocker["']\s*,\s*["']algedonic["']\s*\)"""
        )

        # Post-C2a scope: only teammate_idle.py survives as a caller of
        # the helper. agent_handoff_emitter.py intentionally inlines the
        # literal (not in scope for this pin).
        refactored_files = [
            hooks_root / "teammate_idle.py",
        ]

        offenders = []
        for path in refactored_files:
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{line_no}: {line.strip()}")

        assert not offenders, (
            "Signal-task literal tuple must not appear in refactored hook files. "
            "Use shared.intentional_wait.is_signal_task instead.\n"
            + "\n".join(offenders)
        )

    def test_signal_task_literal_present_in_helper_module(self):
        """Complement: assert the literal IS present in the helper — a
        pure removal test could pass after deletion of the helper logic.

        Style coupling (intentional): the regex matches the parenthesized
        tuple form `("blocker", "algedonic")`. If a future refactor replaces
        the tuple with a frozenset, set literal, or Enum, this test will
        flip RED despite semantically identical behavior. When changing the
        sentinel form, update this test's regex in the same commit —
        mechanically pinning the tuple IS the invariant, not the exact
        syntax.
        """
        from pathlib import Path
        import re

        helper = Path(__file__).parent.parent / "hooks" / "shared" / "intentional_wait.py"
        text = helper.read_text(encoding="utf-8")
        pattern = re.compile(
            r"""\(\s*["']blocker["']\s*,\s*["']algedonic["']\s*\)"""
        )
        matches = pattern.findall(text)
        assert len(matches) >= 1, (
            "Expected the signal-task literal tuple in shared/intentional_wait.py "
            "(as _SIGNAL_TASK_TYPES); found zero."
        )
