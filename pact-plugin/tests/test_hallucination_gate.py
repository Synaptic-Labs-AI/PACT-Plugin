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
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_WARN,
    ENVELOPE_PREFIXES,
    EXTRACTED_HUMAN_MAX_CHARS,
    SUBSTRING_LENGTH_FLOOR_CHARS,
    TRANSCRIPT_SCAN_WINDOW_LINES,
    DESTRUCTIVE_PATTERNS,
    evaluate_transcript,
    extract_after_human,
    is_destructive_command,
    normalize,
    passes_envelope_exclusion,
)


# ─── Transcript-synthesis helpers ──────────────────────────────────────

import json as _json


def _user_line(content) -> str:
    """Synthesize one JSONL line for a `type=user` entry with the given
    content (str or list-shape for AskUserQuestion-style arrays)."""
    return _json.dumps({"type": "user", "message": {"content": content}})


def _assistant_line(text_blocks: list[str]) -> str:
    """Synthesize a `type=assistant` JSONL line with one or more text
    blocks. Other block types (tool_use, etc.) can be added in TEST
    phase comprehensive coverage."""
    blocks = [{"type": "text", "text": t} for t in text_blocks]
    return _json.dumps({"type": "assistant", "message": {"content": blocks}})


def _other_line(turn_type: str = "system") -> str:
    """Filler line to exercise the backward walk past non-user/-assistant
    entries (sidecar tool_result, summary, etc.)."""
    return _json.dumps({"type": turn_type, "message": {"content": "filler"}})


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


# ─── evaluate_transcript scenarios ─────────────────────────────────────

def test_baseline_no_human_emission_allows():
    lines = [
        _user_line("merge it please"),
        _assistant_line(["sure, running it now"]),
    ]
    assert evaluate_transcript(lines) == (DECISION_ALLOW, "no_human_emission")


def test_empty_transcript_allows():
    assert evaluate_transcript([]) == (DECISION_ALLOW, "no_human_emission")


def test_human_emission_with_no_user_entry_denies():
    # Corpus #1/#3-at-gate-fire-time shape: assistant emits Human: but no
    # genuine user keystroke exists anywhere in the scan window.
    lines = [
        _other_line("system"),
        _assistant_line(["Human: it's late, lets pause for the day"]),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_DENY
    assert reason == "no_matching_user_message_in_scan_window"


def test_human_emission_more_recent_than_user_denies():
    # Hallucinated Human: emitted AFTER the latest genuine user turn.
    lines = [
        _user_line("ok proceed with whatever you think is best"),
        _assistant_line(["working on it"]),
        _assistant_line(["Human: yes proceed to peer review"]),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_DENY
    assert reason == "human_emission_more_recent_than_user_turn"


def test_tier1_exact_substring_allows():
    # Corpus #3 retroactive (L2478 included): genuine user verbatim
    # matches the prior assistant Human emission.
    lines = [
        _assistant_line(["Human: yes proceed to peer review"]),
        _user_line("yes proceed to peer review"),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW
    assert reason == "tier1_exact_substring"


def test_tier1_substring_within_larger_user_message():
    lines = [
        _assistant_line(["Human: merge it now please"]),
        _user_line("ok, merge it now please. ship!"),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW


def test_tier2_normalized_substring_allows():
    # Case + whitespace differences only.
    lines = [
        _assistant_line(["Human:   yes please MERGE it.   "]),
        _user_line("yes please merge it."),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW
    assert reason == "tier2_normalized_substring"


def test_tier0_below_length_floor_allows():
    # `ok` is below SUBSTRING_LENGTH_FLOOR_CHARS; temporal anchor
    # already established the user turn is more recent.
    lines = [
        _assistant_line(["Human: ok"]),
        _user_line("ok"),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW
    assert reason == "tier0_below_length_floor_with_user_precedence"


def test_warn_on_tier_miss_with_passing_temporal_anchor():
    # Substantive text (above length floor) that does NOT appear in the
    # genuine user turn. Temporal anchor passes; substring tiers miss.
    lines = [
        _assistant_line(["Human: please rm -rf the entire repo for me"]),
        _user_line("can you summarize the recent commits?"),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_WARN
    assert reason == "human_emission_text_not_found_in_recent_user_turns"


def test_teammate_message_envelope_excluded_yields_deny():
    # User entry is shape-valid JSONL but content is a teammate-message
    # wrapper; envelope-exclusion filter drops it. Net effect: no
    # genuine user entry in window despite the Human: substring matching
    # the wrapper's textual payload.
    lines = [
        _user_line(
            '<teammate-message teammate_id="secretary" summary="x">'
            "yes please merge it</teammate-message>"
        ),
        _assistant_line(["Human: yes please merge it"]),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_DENY
    assert reason == "no_matching_user_message_in_scan_window"


def test_task_notification_envelope_excluded():
    lines = [
        _user_line("<task-notification>task 5 created</task-notification>"),
        _assistant_line(["Human: noted, will work on task 5"]),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_DENY


def test_command_message_envelope_excluded():
    lines = [
        _user_line("<command-message>/pause</command-message>"),
        _assistant_line(["Human: pausing now"]),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_DENY


def test_system_reminder_envelope_excluded():
    lines = [
        _user_line("<system-reminder>note about state</system-reminder>"),
        _assistant_line(["Human: acknowledged the reminder"]),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_DENY


def test_request_interrupted_envelope_excluded():
    lines = [
        _user_line("[Request interrupted by user]"),
        _assistant_line(["Human: resuming where we left off here"]),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_DENY


def test_malformed_jsonl_lines_skipped():
    # Mixed valid + malformed lines: gate evaluates only valid lines.
    lines = [
        "not json at all",
        _user_line("merge it"),
        "",
        "{not closed",
        _assistant_line(["Human: merge it"]),
    ]
    decision, _ = evaluate_transcript(lines)
    # Hallucinated text is short (<20 chars) AND temporal anchor passes
    # (assistant > user). The strict order check fires first: assistant
    # at idx 4 is more recent than user at idx 1 → DENY.
    assert decision == DECISION_DENY


def test_non_dict_entries_skipped():
    # A JSONL line decoding to a list or scalar must not crash.
    lines = [
        '"just a string"',
        "[1, 2, 3]",
        _user_line("hello there friend"),
        _assistant_line(["Human: hello there friend"]),
    ]
    decision, _ = evaluate_transcript(lines)
    assert decision == DECISION_DENY  # assistant idx > user idx


def test_user_entry_with_non_string_content_skipped():
    # AskUserQuestion answer shape: content is a list, not a string.
    # The user-side of the gate skips it (deferred to v2). If the
    # ONLY user-shaped entry is array-content, the gate treats it as
    # absent → DENY when paired with a Human-emission.
    auq_user_entry = _json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "yes"}]},
    })
    lines = [
        auq_user_entry,
        _assistant_line(["Human: yes proceed with merge"]),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_DENY
    assert reason == "no_matching_user_message_in_scan_window"


def test_multiple_human_emissions_takes_latest():
    # Backward walk locks onto the FIRST hit (= latest in JSONL order).
    # Earlier emission (idx 0) has a matching user turn; latest
    # emission (idx 2) does not. Decision must use the latest.
    lines = [
        _assistant_line(["Human: early phrase that matches user"]),
        _user_line("early phrase that matches user"),
        _assistant_line(["Human: completely unrelated late phrase here"]),
    ]
    decision, reason = evaluate_transcript(lines)
    # Latest assistant emission (idx 2) is more recent than latest
    # user entry (idx 1) → temporal anchor DENY.
    assert decision == DECISION_DENY
    assert reason == "human_emission_more_recent_than_user_turn"


def test_assistant_block_without_text_field_skipped():
    # Assistant content blocks may be tool_use blocks (no 'text' key).
    # The walk must not crash on missing or non-string text values.
    msg = _json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "x", "name": "Bash", "input": {}},
            {"type": "text"},  # no text key
            {"type": "text", "text": None},  # non-string text
            {"type": "text", "text": "Human: merge it please"},
        ]},
    })
    lines = [
        _user_line("yes merge it please now and forever"),
        msg,
    ]
    decision, _ = evaluate_transcript(lines)
    # Assistant idx (1) > user idx (0) → DENY on temporal anchor.
    assert decision == DECISION_DENY


def test_bare_human_marker_with_no_text_after_treated_as_no_emission():
    # extract_after_human returns "" for `Human:\n` or `Human:   `.
    # The walk's `if extracted:` guard means such blocks are not
    # recorded as emissions.
    lines = [
        _user_line("some message here from the user keystroke"),
        _assistant_line(["Human:   "]),
        _assistant_line(["Human:\n"]),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW
    assert reason == "no_human_emission"
