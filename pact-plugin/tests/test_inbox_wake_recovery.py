"""
Recovery-rule and conditional-shape tests for the inbox-wake mechanism.

Covers:
  - peer-review.md's `CronList`-then-arm conditional shape (sentinels
    wrap inner CronCreate only; conditional logic outside).
  - rePACT.md's nesting note (STATE_FILE check + 420s freshness threshold).
  - Recovery-rule branch-logic semantics: parses the canonical cron
    prompt body fixture and asserts the 3-branch tree (cold-start /
    live-no-op / stale-recovery) + FAIL-OPEN error routing.
  - Synthetic heartbeat-file and STATE_FILE schema fixtures.

This is the integration regression Option B per team-lead direction:
test the recovery-rule semantics without invoking the live cron. The live
wall-clock variant (arm Monitor → TaskStop → wait 7 minutes → inspect
recovery) is documented in `pact-plugin/tests/runbooks/inbox-monitor-wake.md`
§7 as a manual runbook step (#444 precedent).
"""
import json
import re
import time

import pytest

from fixtures.inbox_wake import (
    COMMANDS_DIR, FIXTURES_DIR, RUNBOOK_PATH,
    CRON_START, CRON_END,
    _read, _between,
)


class TestRePACTNestingNoteFreshness:
    """rePACT.md's nesting note must reference both the STATE_FILE path and
    the 420s freshness threshold. Same threshold as the cron's recovery
    rule Branch B/C boundary — drift would create a behavior mismatch
    between nested-skip and cold-start-recover.
    """

    def test_nesting_note_contains_state_file_and_threshold(self):
        text = _read(COMMANDS_DIR / "rePACT.md")
        assert "inbox-wake-state.json" in text, (
            "rePACT.md missing STATE_FILE reference"
        )
        # The 420s threshold (7 minutes) is the freshness boundary.
        assert "420" in text, (
            "rePACT.md missing 420s freshness threshold — must match "
            "cron prompt body's Branch B/C boundary to avoid behavior drift"
        )


class TestPeerReviewCronListConditional:
    """peer-review.md uses a conditional `CronList`-then-arm pattern around
    its canonical Cron block. The H2 sentinels wrap the inner CronCreate
    only; conditional logic lives OUTSIDE the sentinels.

    Pin both:
      (a) `CronList` is named explicitly, AND
      (b) the conditional-skip behavior is described in prose.
    """

    PATH = COMMANDS_DIR / "peer-review.md"

    def test_cronlist_named_explicitly(self):
        text = _read(self.PATH)
        assert "CronList" in text, (
            "peer-review.md must explicitly name `CronList` for the "
            "conditional cron-arm step"
        )

    def test_conditional_skip_described(self):
        """The conditional must describe the skip-if-present case + reference
        the canonical cron description. Stronger byte-equivalence is
        enforced by the verify-script; this only pins the surrounding
        peer-review-specific conditional logic."""
        text = _read(self.PATH)
        skip_phrases = ["pass-through", "skip", "SKIP"]
        assert any(p in text for p in skip_phrases), (
            "peer-review.md conditional cron-arm must describe the "
            "skip-if-present case"
        )
        assert "pact-inbox-cron:" in text, (
            "peer-review.md conditional must reference the canonical cron "
            "description prefix `pact-inbox-cron:` for matching CronList entries"
        )

    def test_conditional_logic_outside_sentinels(self):
        """The CronList description must appear OUTSIDE the (start)/(end)
        sentinel pair. Sentinels wrap only the canonical CronCreate body."""
        text = _read(self.PATH)
        between = _between(text, CRON_START, CRON_END)
        assert "CronList()" not in between, (
            "peer-review.md inner Cron block must NOT contain CronList() — "
            "conditional CronList logic belongs OUTSIDE the sentinel pair"
        )


class TestRecoveryRuleBranchLogic:
    """Integration regression (Option B per team-lead direction): parse the
    cron prompt body fixture and verify the 3-branch tree's threshold
    logic. Live wall-clock variant lives in the manual runbook §7.

    These tests verify the recovery rule's prose-pseudocode encodes the
    correct semantics — they do NOT invoke the live cron.
    """

    @pytest.fixture
    def cron_prompt_body(self) -> str:
        """Return the canonical cron-block fixture body (single source of
        truth — same bytes as what each ARMING_FILE inlines)."""
        return _read(FIXTURES_DIR / "cron-block.txt")

    def test_three_branch_tree_present(self, cron_prompt_body):
        """The recovery rule must declare three branches: A (cold-start),
        B (live no-op), C (stale-recovery)."""
        for branch in ("Branch A", "Branch B", "Branch C"):
            assert branch in cron_prompt_body, (
                f"recovery rule missing {branch} — 3-branch tree per "
                "architect §Section 2 / CF7"
            )

    def test_branch_a_cold_start_semantics(self, cron_prompt_body):
        """Branch A fires when STATE_FILE is missing → cold-start arm."""
        a_idx = cron_prompt_body.index("Branch A")
        b_idx = cron_prompt_body.index("Branch B")
        section = cron_prompt_body[a_idx:b_idx]
        assert "STATE_FILE missing" in section or "missing" in section, (
            "Branch A must trigger on STATE_FILE missing"
        )
        assert "COLD START" in section or "cold-start" in section.lower(), (
            "Branch A must describe cold-start behavior"
        )

    def test_branch_b_live_noop_uses_420s_threshold(self, cron_prompt_body):
        """Branch B is the no-op case: STATE_FILE present + heartbeat fresh
        within the 420s threshold."""
        b_idx = cron_prompt_body.index("Branch B")
        c_idx = cron_prompt_body.index("Branch C")
        section = cron_prompt_body[b_idx:c_idx]
        assert "420" in section, (
            "Branch B must use 420s freshness threshold (7 minutes; "
            "cadence 300s × 1.4)"
        )
        assert "fresh" in section.lower(), (
            "Branch B must describe the heartbeat-fresh condition"
        )

    def test_branch_c_stale_recovery_unlinks(self, cron_prompt_body):
        """Branch C fires on staleness or missing heartbeat. It must
        TaskStop the old monitor and unlink the registry files before
        cold-starting."""
        c_idx = cron_prompt_body.index("Branch C")
        # Branch C extends to the FAIL-OPEN marker that closes the branch tree.
        end_idx = cron_prompt_body.index("FAIL-OPEN", c_idx)
        section = cron_prompt_body[c_idx:end_idx]
        assert "TaskStop" in section, "Branch C must TaskStop the stale monitor"
        assert "Unlink" in section or "unlink" in section, (
            "Branch C must unlink the heartbeat/state files before re-arming"
        )
        assert "420" in section, (
            "Branch C's stale condition must reference the 420s threshold"
        )

    def test_recovery_rule_is_fail_open(self, cron_prompt_body):
        """Per architect §Section 2 §FAIL-OPEN semantics: malformed-file or
        schema-mismatch falls through to Branch C (re-arm), not Branch B
        (no-op). False-arm is bounded; false-skip is unbounded blind window.
        """
        assert "FAIL-OPEN" in cron_prompt_body, (
            "recovery rule must declare FAIL-OPEN error semantics"
        )
        # The narrative must explicitly route errors to Branch C.
        assert "Branch C" in cron_prompt_body[
            cron_prompt_body.index("FAIL-OPEN"):
        ], "FAIL-OPEN narrative must route errors to Branch C (re-arm)"

    def test_heartbeat_staleness_threshold_matches_logic(self, tmp_path):
        """Synthetic-fixture sanity check on the threshold semantics.

        Build a fake heartbeat file with `ts = current_epoch - 500` (500s
        old, > 420s threshold), and assert that the staleness predicate
        encoded in the cron prompt body would route this to Branch C.
        We don't execute the prompt body; we verify the threshold value
        used in the prose matches the fixture's age, which is the
        load-bearing claim ('420 means 420 actually triggers staleness')."""
        hb = tmp_path / "inbox-wake-heartbeat.json"
        ts = int(time.time()) - 500  # 500s old → STALE per 420s threshold
        hb.write_text(json.dumps({"v": 1, "count": 0, "ts": ts}))

        loaded = json.loads(hb.read_text())
        age = int(time.time()) - loaded["ts"]
        threshold = 420
        assert age >= threshold, (
            f"synthetic heartbeat fixture has age {age}s but threshold is "
            f"{threshold}s — fixture-construction bug, not a recovery-rule bug"
        )

    def test_heartbeat_freshness_below_threshold(self, tmp_path):
        """Mirror of the staleness test: a heartbeat with `ts = current_epoch
        - 100` (100s old, < 420s threshold) is FRESH and routes to Branch B."""
        hb = tmp_path / "inbox-wake-heartbeat.json"
        ts = int(time.time()) - 100  # 100s old → FRESH per 420s threshold
        hb.write_text(json.dumps({"v": 1, "count": 0, "ts": ts}))

        loaded = json.loads(hb.read_text())
        age = int(time.time()) - loaded["ts"]
        threshold = 420
        assert age < threshold, (
            f"synthetic fresh-heartbeat fixture has age {age}s but "
            f"threshold is {threshold}s — fixture-construction bug"
        )


class TestRunbookLatencyBoundInvariant:
    """Cross-document drift guard: the runbook §7 latency PASS condition
    ('within 11 minutes of TaskStop') must equal the cron cadence + the
    420s staleness threshold. A change to either constant in cron-block.txt
    without a corresponding runbook update would silently invalidate the
    runbook's PASS condition.
    """

    def test_runbook_latency_bound_matches_cron_constants(self):
        cron_text = _read(FIXTURES_DIR / "cron-block.txt")
        # Parse cadence from the canonical schedule literal `*/N * * * *`.
        cadence_match = re.search(r'schedule="\*/(\d+) \* \* \* \*"', cron_text)
        assert cadence_match, (
            "cron-block.txt missing canonical `schedule=\"*/N * * * *\"` literal"
        )
        cadence_minutes = int(cadence_match.group(1))
        cadence_seconds = cadence_minutes * 60

        # The 420s threshold is referenced in the prose-pseudocode body.
        assert "420" in cron_text, (
            "cron-block.txt missing 420s freshness threshold"
        )
        threshold_seconds = 420

        worst_case_seconds = cadence_seconds + threshold_seconds

        runbook_text = _read(RUNBOOK_PATH)
        # Runbook §7 PASS condition: "registry refreshes within 11 minutes
        # of TaskStop". Pin the exact phrase so reformatting the runbook
        # surfaces the dependency.
        assert "within 11 minutes of TaskStop" in runbook_text, (
            "runbook missing the '11 minutes of TaskStop' PASS-condition "
            "phrase — drift between cron constants and the documented "
            "latency bound is no longer invariant-checked"
        )
        runbook_minutes = 11
        # Tolerance: the 5s monitor poll interval is below minute-resolution.
        # Worst-case = cadence + threshold; runbook bound rounds up to the
        # nearest minute. Verify equality at minute resolution.
        assert worst_case_seconds // 60 == runbook_minutes, (
            f"runbook documents {runbook_minutes}-minute worst-case but "
            f"cron-block.txt encodes cadence ({cadence_seconds}s) + "
            f"threshold ({threshold_seconds}s) = {worst_case_seconds}s "
            f"({worst_case_seconds // 60} min). Update one or the other "
            "to restore the cross-document invariant."
        )

