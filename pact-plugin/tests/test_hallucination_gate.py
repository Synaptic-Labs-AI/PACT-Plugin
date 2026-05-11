"""Tests for hallucination_gate pure-function predicates.

CODE-phase smoke tests covering the building blocks:
- passes_envelope_exclusion (allowlist-style filter)
- extract_after_human (Human: substring extraction with bound)
- normalize (case-fold + whitespace-collapse)
- is_destructive_command (regex set against stripped command)

Comprehensive scenario coverage (T1-T24 from the architecture spec)
lands in TEST phase; this file pins the predicate-level invariants
that the temporal-anchor algorithm composes from.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from hallucination_gate import (  # noqa: E402
    ENVELOPE_PREFIXES,
    EXTRACTED_HUMAN_MAX_CHARS,
    SUBSTRING_LENGTH_FLOOR_CHARS,
    TRANSCRIPT_SCAN_WINDOW_LINES,
    DESTRUCTIVE_PATTERNS,
    extract_after_human,
    is_destructive_command,
    normalize,
    passes_envelope_exclusion,
)


# ─── Tunable-constant sanity ──────────────────────────────────────────

def test_scan_window_covers_corpus_gaps():
    # Corpus gap distribution: 22 / 196 / no-counterpart. 500 covers
    # both observed gaps with headroom.
    assert TRANSCRIPT_SCAN_WINDOW_LINES >= 200


def test_substring_floor_excludes_short_tokens():
    # Discriminates `Human: ok` (2 chars) from a real directive.
    assert SUBSTRING_LENGTH_FLOOR_CHARS >= 4
    assert SUBSTRING_LENGTH_FLOOR_CHARS <= 64


def test_extract_max_bounds_compare_cost():
    assert EXTRACTED_HUMAN_MAX_CHARS >= 80


# ─── passes_envelope_exclusion ─────────────────────────────────────────

@pytest.mark.parametrize(
    "content,expected",
    [
        ("merge it", True),
        ("yes proceed", True),
        ("  leading whitespace then text", True),
        # All 5 platform-injected envelopes
        ("<teammate-message teammate_id=\"x\">payload</teammate-message>", False),
        ("<task-notification>foo</task-notification>", False),
        ("<command-message>/pause</command-message>", False),
        ("<system-reminder>note</system-reminder>", False),
        ("[Request interrupted by user]", False),
        # lstrip handles indented envelopes
        ("   <teammate-message id=\"x\">", False),
        ("\t<system-reminder>x</system-reminder>", False),
        # Substring-anywhere would over-exclude legitimate quotes
        ("the user said '<teammate-message' is the prefix", True),
        ("about <system-reminder> tags in general", True),
        # Edge cases
        ("", True),  # empty content is shape-allowed (caller handles emptiness)
        (None, False),
        (123, False),
    ],
)
def test_passes_envelope_exclusion(content, expected):
    assert passes_envelope_exclusion(content) is expected


def test_envelope_prefixes_count_matches_design():
    # 5 prefixes per architect §5; if this drifts the temporal-anchor
    # walk loses cases. Pinned to catch accidental additions/removals.
    assert len(ENVELOPE_PREFIXES) == 5


# ─── extract_after_human ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Human: yes proceed to peer review", "yes proceed to peer review"),
        ("Human:   leading spaces stripped", "leading spaces stripped"),
        ("Human: first line\nsecond line ignored", "first line"),
        # Bare `Human:` with no follow-up
        ("Human:", ""),
        ("Human:   \n", ""),
        # No marker at all
        ("Assistant prose with no marker", ""),
        ("", ""),
        # Multi-occurrence: take the LAST
        ("Human: first\n...prose...\nHuman: last directive", "last directive"),
        # Non-string input
        (None, ""),
        (42, ""),
    ],
)
def test_extract_after_human(text, expected):
    assert extract_after_human(text) == expected


def test_extract_after_human_bounded_to_max_chars():
    long_text = "Human: " + ("x" * 500)
    out = extract_after_human(long_text)
    assert len(out) == EXTRACTED_HUMAN_MAX_CHARS
    assert out == "x" * EXTRACTED_HUMAN_MAX_CHARS


def test_extract_after_human_preserves_internal_spacing():
    # Substring tier-1 needs verbatim match; do NOT collapse interior whitespace.
    assert extract_after_human("Human: foo  bar") == "foo  bar"


# ─── normalize ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Yes Please Merge It", "yes please merge it"),
        ("  Yes\tplease   MERGE  it.\n", "yes please merge it."),
        ("ALL CAPS", "all caps"),
        ("multi\nline\ntext", "multi line text"),
        ("", ""),
        ("single", "single"),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_enables_tier2_substring_match():
    hallucinated = "  yes please MERGE it.   "
    user_text = "ok, yes please merge it. ship it!"
    assert normalize(hallucinated) in normalize(user_text)


# ─── is_destructive_command ────────────────────────────────────────────

@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 705",
        "gh pr merge 705 --admin",
        "gh --repo foo/bar pr merge 705",
        "gh pr close 705 --delete-branch",
        "git push origin main --force",
        "git push -f origin main",
        "git push origin -f main",
        "git branch -D feature-x",
        "rm -rf /tmp/junk",
        "rm -fr /tmp/junk",
        "rm -rfv /tmp/junk",
        "rm -r /tmp/junk -f",
        "gh issue create -t Test -b Body",
        "gh release create v1.2.3",
        "gh release delete v1.2.3",
        "git push --tags",
        "git push origin refs/tags/v1.2.3",
        "git push origin v1.2.3",
        "git push origin v4.1.7",
        "git reset --hard HEAD~1",
        "git rebase main",
        "git rebase -i HEAD~3",
        "git rebase --onto main feature topic",
        "git tag -d v1.2.3",
        "git push origin :refs/heads/feature-x",
        "git push origin --delete feature-x",
    ],
)
def test_is_destructive_command_positive(command):
    assert is_destructive_command(command), f"expected DESTRUCTIVE: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "git log --oneline",
        "git diff --stat",
        "git push origin feature-x",  # branch push, not tag/main
        "git push origin feature/my-work",  # branch push w/ slash
        "gh pr view 705",
        "gh pr list",
        "gh issue list",
        "gh issue close 705",  # close ≠ create; excluded per architect
        "gh pr comment 705 -b 'hi'",  # excluded per architect
        "git clean -fd",  # excluded per architect (recoverable)
        "rm foo.txt",  # no -r/-f
        "rm -i foo.txt",  # interactive, not force
        "git commit -m 'gh pr merge 705 in body'",  # commit-msg stripped
        "echo 'rm -rf /tmp'",  # echo-quoted, stripped
        "",
    ],
)
def test_is_destructive_command_negative(command):
    assert not is_destructive_command(command), f"expected BENIGN: {command!r}"


def test_is_destructive_command_handles_non_string():
    assert is_destructive_command(None) is False
    assert is_destructive_command(42) is False


def test_is_destructive_command_normalizes_line_continuations():
    cmd = "gh pr \\\n  merge 705"
    assert is_destructive_command(cmd)


def test_destructive_patterns_compiled():
    # All entries must be compiled regex objects (the module-level
    # try/except converts compile failure into fail-CLOSED deny).
    for pat in DESTRUCTIVE_PATTERNS:
        assert hasattr(pat, "search"), f"non-regex entry in DESTRUCTIVE_PATTERNS: {pat!r}"


def test_destructive_patterns_minimum_cardinality():
    # 9 merge_guard overlap + 6 rm + 3 gh-artifact + 3 tag + 4 history = 25
    # The bound is a floor against accidental deletion, not an exact count.
    assert len(DESTRUCTIVE_PATTERNS) >= 20
