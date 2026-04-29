"""
Canonical-mirror invariant tests for the inbox-wake surface.

Covers:
  - Fixture file shape (first line is the literal start-sentinel H2).
  - Verify-script subprocess (the 35/35 PASS contract in CI).
  - Counter-test layer (mutate one byte, assert non-zero exit) per
    architect D9 + memory 3e665bc5 (PR #580 phantom-green lesson).
  - Verify-script call-list size guard (17 inbox-wake invocations:
    5 Monitor + 5 Cron + 5 WriteStateFile + 2 Teardown).
"""
import re
import subprocess

import pytest

from fixtures.inbox_wake import (
    FIXTURES_DIR, VERIFY_SCRIPT, _REPO_ROOT,
    MONITOR_START, MONITOR_END,
    CRON_START, CRON_END,
    STATE_START, STATE_END,
    TEARDOWN_START, TEARDOWN_END,
    _read, _between, _build_repo_subset, _run_verify,
)


class TestFixtureFileShape:
    """Each fixture file's first line is the literal start-sentinel H2.

    The awk extractor in verify-protocol-extracts.sh captures
    `[start_sentinel_line, end_sentinel_line)` (half-open). The captured
    body therefore INCLUDES the start sentinel; the fixture's first line
    must be that sentinel byte-for-byte. Earlier doc-spec drafts described
    the captured range as "between sentinels, NOT including either" —
    that abstraction was wrong on the start half. This test pins the
    corrected semantics so future fixture refreshes don't repeat the error.
    """

    @pytest.mark.parametrize("fixture,expected_first_line", [
        ("monitor-block.txt", MONITOR_START),
        ("cron-block.txt", CRON_START),
        ("teardown-block.txt", TEARDOWN_START),
    ])
    def test_first_line_is_start_sentinel(self, fixture, expected_first_line):
        text = _read(FIXTURES_DIR / fixture)
        first_line = text.split("\n", 1)[0]
        assert first_line == expected_first_line, (
            f"{fixture} first line is {first_line!r}, expected "
            f"{expected_first_line!r} — awk capture is half-open "
            "[start_line, end_line); first line MUST be the start sentinel"
        )


class TestVerifyScript:
    """The shell-side mirror invariant: `bash scripts/verify-protocol-extracts.sh`
    exits 0 (35/35 PASS: 18 protocol + 17 inbox-wake) when all canonical
    content is byte-equivalent to fixtures.
    """

    def test_verify_script_exists_and_executable(self):
        assert VERIFY_SCRIPT.exists(), f"verify script missing: {VERIFY_SCRIPT}"

    def test_verify_script_passes(self):
        result = subprocess.run(
            ["bash", str(VERIFY_SCRIPT)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"verify-protocol-extracts.sh exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        # Sanity check the summary line so the test fails loud if the
        # script's report shape changes.
        assert "VERIFICATION PASSED" in result.stdout


class TestVerifyScriptCounterTest:
    """Counter-test layer per architect D9 + memory 3e665bc5 (PR #580 lesson).

    A passing verify script proves the invariant holds; a counter-test
    proves the invariant is DISCRIMINATIVE — i.e., the script actually
    fails when content drifts. Without this, a script that returns 0
    unconditionally would silently mask all drift (phantom-green).

    Mutation is parametrized across all 17 verify-script invocations
    (5 Monitor + 5 Cron + 5 WriteStateFile + 2 Teardown) so a
    `verify_inbox_wake` failure mode that only triggers for one specific
    host-file invocation (e.g., a typo in the call list that resolves to
    a wrong fixture for one entry) cannot hide as phantom-green on the
    others. The 4 fixture-kind buckets (Monitor/Cron/State/Teardown)
    each define a single mutation target; we sweep every host file the
    verify-script enumerates.
    """

    def test_baseline_passes_on_copy(self, tmp_path):
        """Sanity precondition: the un-mutated copy passes. Catches bugs
        in the subset-copy mechanism itself (missing protocol files, etc.)
        before the mutation tests fire."""
        repo_root = _build_repo_subset(tmp_path)
        result = _run_verify(repo_root)
        assert result.returncode == 0, (
            f"baseline verify on copied subtree FAILED ({result.returncode}) "
            f"— subset-copy mechanism is broken\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    @pytest.mark.parametrize(
        "fixture_kind,host_file,start_sentinel,end_sentinel,mutation_target,mutation_replacement,expected_failure_marker",
        # 17 entries: per-host-file sweep across all verify-script
        # invocations. Mutation target is constant within a fixture kind
        # (canonical content is byte-equivalent across host files); only
        # host_file + expected_failure_marker vary. Failure marker stem
        # tracks the verify-script's output format
        # (`<FixtureKind> @ <host-stem>`).
        [
            # Monitor: 5 ARMING_FILES.
            ("monitor", "orchestrate.md", MONITOR_START, MONITOR_END,
             "Monitor(", "MONITOR(", "Monitor @ orchestrate"),
            ("monitor", "comPACT.md", MONITOR_START, MONITOR_END,
             "Monitor(", "MONITOR(", "Monitor @ comPACT"),
            ("monitor", "rePACT.md", MONITOR_START, MONITOR_END,
             "Monitor(", "MONITOR(", "Monitor @ rePACT"),
            ("monitor", "plan-mode.md", MONITOR_START, MONITOR_END,
             "Monitor(", "MONITOR(", "Monitor @ plan-mode"),
            ("monitor", "peer-review.md", MONITOR_START, MONITOR_END,
             "Monitor(", "MONITOR(", "Monitor @ peer-review"),
            # Cron: 5 ARMING_FILES.
            ("cron", "orchestrate.md", CRON_START, CRON_END,
             "CronCreate(", "CRONCREATE(", "Cron @ orchestrate"),
            ("cron", "comPACT.md", CRON_START, CRON_END,
             "CronCreate(", "CRONCREATE(", "Cron @ comPACT"),
            ("cron", "rePACT.md", CRON_START, CRON_END,
             "CronCreate(", "CRONCREATE(", "Cron @ rePACT"),
            ("cron", "plan-mode.md", CRON_START, CRON_END,
             "CronCreate(", "CRONCREATE(", "Cron @ plan-mode"),
            ("cron", "peer-review.md", CRON_START, CRON_END,
             "CronCreate(", "CRONCREATE(", "Cron @ peer-review"),
            # State: 5 ARMING_FILES.
            ("state", "orchestrate.md", STATE_START, STATE_END,
             "inbox-wake-state.json", "INBOX-WAKE-STATE.json",
             "WriteStateFile @ orchestrate"),
            ("state", "comPACT.md", STATE_START, STATE_END,
             "inbox-wake-state.json", "INBOX-WAKE-STATE.json",
             "WriteStateFile @ comPACT"),
            ("state", "rePACT.md", STATE_START, STATE_END,
             "inbox-wake-state.json", "INBOX-WAKE-STATE.json",
             "WriteStateFile @ rePACT"),
            ("state", "plan-mode.md", STATE_START, STATE_END,
             "inbox-wake-state.json", "INBOX-WAKE-STATE.json",
             "WriteStateFile @ plan-mode"),
            ("state", "peer-review.md", STATE_START, STATE_END,
             "inbox-wake-state.json", "INBOX-WAKE-STATE.json",
             "WriteStateFile @ peer-review"),
            # Teardown: 2 TEARDOWN_FILES.
            ("teardown", "wrap-up.md", TEARDOWN_START, TEARDOWN_END,
             "TaskStop", "TASKSTOP", "Teardown @ wrap-up"),
            ("teardown", "pause.md", TEARDOWN_START, TEARDOWN_END,
             "TaskStop", "TASKSTOP", "Teardown @ pause"),
        ],
        ids=[
            "monitor-orchestrate", "monitor-comPACT", "monitor-rePACT",
            "monitor-plan-mode", "monitor-peer-review",
            "cron-orchestrate", "cron-comPACT", "cron-rePACT",
            "cron-plan-mode", "cron-peer-review",
            "state-orchestrate", "state-comPACT", "state-rePACT",
            "state-plan-mode", "state-peer-review",
            "teardown-wrap-up", "teardown-pause",
        ],
    )
    def test_mutated_canonical_block_fails(
        self, tmp_path, fixture_kind, host_file,
        start_sentinel, end_sentinel,
        mutation_target, mutation_replacement,
        expected_failure_marker,
    ):
        """Mutate one byte inside the canonical block at a specific host
        file and assert the verify script exits non-zero with the failure
        attributed to THIS fixture's invocation. Per-fixture parametrization
        guards against per-fixture phantom-green (a verify_inbox_wake
        failure mode that only triggers for one fixture kind cannot hide
        on the others)."""
        repo_root = _build_repo_subset(tmp_path)
        target = repo_root / "pact-plugin" / "commands" / host_file
        text = target.read_text(encoding="utf-8")
        # Confirm the mutation site exists between the sentinels.
        between = _between(text, start_sentinel, end_sentinel)
        assert mutation_target in between, (
            f"test precondition broken: {mutation_target!r} is no longer "
            f"present inside {host_file}'s {fixture_kind} sentinel pair — "
            "mutation target needs updating"
        )
        # Mutate ONE occurrence inside the captured range only — the
        # mutation cannot accidentally hit prose outside the sentinels.
        mutated_between = between.replace(mutation_target, mutation_replacement, 1)
        new_text = (
            text[: text.index(start_sentinel) + len(start_sentinel)]
            + mutated_between
            + text[text.index(end_sentinel):]
        )
        assert new_text != text, "mutation produced no change"
        target.write_text(new_text, encoding="utf-8")

        result = _run_verify(repo_root)
        assert result.returncode != 0, (
            f"verify script returned 0 against a MUTATED {fixture_kind} "
            "canonical block — phantom-green: the script does not actually "
            "catch drift on this fixture\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        # The failure must reference THIS fixture's inbox-wake surface, not
        # just any protocol entry. Catches the case where the script fails
        # for the wrong reason (e.g., a protocol entry got corrupted in the
        # copy) or where the failure is attributed to a different fixture.
        assert expected_failure_marker in result.stdout, (
            f"verify script failed but not on the mutated {fixture_kind} "
            f"surface (expected {expected_failure_marker!r}) — subset-copy "
            "may have corrupted unrelated entries, or the failure landed "
            "on a different fixture's invocation"
        )


class TestVerifyScriptCallList:
    """The verify script's per-callsite call list must enumerate exactly 17
    inbox-wake entries: 5 Monitor + 5 Cron + 5 Write State File + 2 Teardown.
    Regression guard: if a future refactor drops a callsite from the call
    list, the verify script's PASS count drops silently from 35 to 34 but
    this test fails loud here.
    """

    def test_call_list_has_seventeen_inbox_wake_entries(self):
        text = _read(VERIFY_SCRIPT)
        # `verify_inbox_wake` is the twin function for inbox-wake entries.
        # Anchor to start-of-line + space + double-quote: matches actual
        # invocations (whose first arg is quoted, like `"$COMMANDS_DIR/...`)
        # and excludes the definition `verify_inbox_wake() {`. The previous
        # regex `^verify_inbox_wake ` (without trailing quote) would miss
        # an invocation that drops quotes around its first arg, but the
        # current shape is uniform — tighter anchor catches accidental
        # paste of unquoted-arg invocations.
        invocation_lines = re.findall(
            r'^verify_inbox_wake "[^"]+"\s+"[^"]+"\s+"[^"]+"\s+"[^"]+"\s+"[^"]+"\s*$',
            text,
            re.MULTILINE,
        )
        assert len(invocation_lines) == 17, (
            f"verify script has {len(invocation_lines)} verify_inbox_wake "
            "invocations matching the canonical 5-arg quoted shape, "
            "expected 17 (5 Monitor + 5 Cron + 5 Write State File + "
            "2 Teardown). A loose count via `^verify_inbox_wake ` may "
            "show more if a future refactor drops quotes around args; the "
            "5-arg-quoted regex catches that drift."
        )
