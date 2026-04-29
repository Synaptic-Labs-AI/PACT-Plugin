"""
Canonical-mirror invariant tests for the inbox-wake surface.

Covers:
  - Fixture file shape (first line is the literal start-sentinel H2).
  - Verify-script subprocess (the 30/30 PASS contract in CI).
  - Counter-test layer (mutate one byte, assert non-zero exit) per
    architect D9 + memory 3e665bc5 (PR #580 phantom-green lesson).
  - Verify-script call-list size guard (12 inbox-wake invocations).
"""
import re
import subprocess

import pytest

from fixtures.inbox_wake import (
    FIXTURES_DIR, VERIFY_SCRIPT, _REPO_ROOT,
    MONITOR_START, CRON_START, TEARDOWN_START, MONITOR_END,
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
    exits 0 (30/30 PASS) when all canonical content is byte-equivalent to
    fixtures.
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

    Single-file mutation is sufficient per architect §Section 6 LOW.
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

    def test_mutated_canonical_block_fails(self, tmp_path):
        """Mutate one byte inside orchestrate.md's canonical Monitor block
        and assert the verify script exits non-zero. Targets the literal
        `Monitor(` token (case change) — guaranteed to land between
        sentinels (it's part of the canonical fixture body)."""
        repo_root = _build_repo_subset(tmp_path)
        target = repo_root / "pact-plugin" / "commands" / "orchestrate.md"
        text = target.read_text(encoding="utf-8")
        # Confirm the mutation site exists between Monitor sentinels.
        between = _between(text, MONITOR_START, MONITOR_END)
        assert "Monitor(" in between, (
            "test precondition broken: 'Monitor(' is no longer present "
            "inside orchestrate.md's Monitor sentinel pair — mutation "
            "target needs updating"
        )
        # Mutate ONE occurrence (the first one inside the canonical block).
        # We rebuild the file: replace inside the captured range only, so
        # the mutation can never accidentally hit prose outside the sentinels.
        mutated_between = between.replace("Monitor(", "MONITOR(", 1)
        new_text = (
            text[: text.index(MONITOR_START) + len(MONITOR_START)]
            + mutated_between
            + text[text.index(MONITOR_END):]
        )
        assert new_text != text, "mutation produced no change"
        target.write_text(new_text, encoding="utf-8")

        result = _run_verify(repo_root)
        assert result.returncode != 0, (
            "verify script returned 0 against a MUTATED canonical block — "
            "phantom-green: the script does not actually catch drift\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        # The failure must reference the inbox-wake surface, not just any
        # protocol entry. Catches the case where the script fails for the
        # wrong reason (e.g., a protocol entry got corrupted in the copy).
        assert "Monitor @ orchestrate" in result.stdout, (
            "verify script failed but not on the mutated surface — "
            "subset-copy may have corrupted unrelated entries"
        )


class TestVerifyScriptCallList:
    """The verify script's per-callsite call list must enumerate exactly 12
    inbox-wake entries: 5 Monitor + 5 Cron + 2 Teardown. Regression guard:
    if a future refactor drops a callsite from the call list, the verify
    script's PASS count drops silently from 30 to 29 but this test fails
    loud here.
    """

    def test_call_list_has_twelve_inbox_wake_entries(self):
        text = _read(VERIFY_SCRIPT)
        # `verify_inbox_wake` is the twin function for inbox-wake entries.
        # Count its invocations in the call list (excluding the function
        # definition itself, which uses the name as `verify_inbox_wake()`).
        invocation_pattern = re.compile(r"^verify_inbox_wake ", re.MULTILINE)
        invocations = invocation_pattern.findall(text)
        assert len(invocations) == 12, (
            f"verify script has {len(invocations)} verify_inbox_wake "
            "invocations, expected 12 (5 Monitor + 5 Cron + 2 Teardown)"
        )
