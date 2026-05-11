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
        # Known-corpus angle-bracket envelopes
        ("<teammate-message teammate_id=\"x\">payload</teammate-message>", False),
        ("<task-notification>foo</task-notification>", False),
        ("<command-message>/pause</command-message>", False),
        ("<system-reminder>note</system-reminder>", False),
        # Legacy non-angle-bracket envelope
        ("[Request interrupted by user]", False),
        # Novel / future angle-bracket wrappers — must be rejected by the
        # categorical angle-bracket heuristic without explicit prefix
        # enumeration (defense against deny-by-omission).
        ("<inbox-message>foo</inbox-message>", False),
        ("<wake-signal>bar</wake-signal>", False),
        ("<event>x</event>", False),
        ("<user-prompt-submit-hook>x</user-prompt-submit-hook>", False),
        ("<future-platform-tag>x</future-platform-tag>", False),
        ("<arbitrary-novel-wrapper>x</arbitrary-novel-wrapper>", False),
        # lstrip handles indented envelopes
        ("   <teammate-message id=\"x\">", False),
        ("\t<system-reminder>x</system-reminder>", False),
        ("   <inbox-message>x</inbox-message>", False),
        # Mid-string angle brackets must NOT trigger rejection
        ("the user said '<teammate-message' is the prefix", True),
        ("about <system-reminder> tags in general", True),
        ("proceed with the merge please <details>", True),
        # Edge cases
        ("", True),  # empty content is shape-allowed (caller handles emptiness)
        (None, False),
        (123, False),
    ],
)
def test_passes_envelope_exclusion(content, expected):
    assert passes_envelope_exclusion(content) is expected


def test_envelope_filter_is_categorical_not_curated():
    # The load-bearing filter must be the angle-bracket-shape heuristic,
    # NOT the curated 5-entry prefix list. Pin the architectural choice
    # so a future refactor cannot silently revert to deny-by-omission.
    # Sample a wrapper NOT in ENVELOPE_PREFIXES — it must still be
    # rejected.
    novel_wrapper = "<deliberately-not-in-curated-list>x</deliberately-not-in-curated-list>"
    assert not any(novel_wrapper.startswith(p) for p in ENVELOPE_PREFIXES), (
        "test premise: chosen wrapper must NOT appear in the curated list"
    )
    assert passes_envelope_exclusion(novel_wrapper) is False, (
        "categorical angle-bracket heuristic must reject any <*>-shaped "
        "wrapper, including ones absent from the curated ENVELOPE_PREFIXES"
    )


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
        # POSIX `rm -R` is a documented synonym for `-r`. Uppercase and
        # mixed-case recursion/force flags must be caught — lowercase-only
        # literals previously admitted these as bypass shapes.
        "rm -Rf /tmp/junk",
        "rm -RF /tmp/junk",
        "rm -RFv /tmp/junk",
        "rm -fR /tmp/junk",
        "rm -fRv /tmp/junk",
        "rm -Fr /tmp/junk",
        "rm -FR /tmp/junk",
        "rm -R /tmp/junk -f",
        "rm -F /tmp/junk -R",
        "rm -r /tmp/junk -F",
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
        "rm -R foo",  # recursion alone (no force) — out of gate scope
        "rm -F foo",  # force alone (no recursion) — out of gate scope
        "rm -Iv foo",  # interactive verbose, no recursion+force
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


def test_temporal_anchor_boundary_user_immediately_after_emission():
    # Boundary pin for the strict-> temporal-anchor check
    # (`last_assistant_human_idx > last_user_directive_idx`). Literal
    # idx-tie is impossible (different `type` per JSONL line); the
    # closest non-tied case is `assistant_idx == user_idx - 1` (the
    # user message is the immediately-next line after the assistant
    # emission). Expected: ALLOW because user is more recent than
    # emission (user_idx > assistant_idx by 1).
    lines = [
        _assistant_line(["Human: please merge the open PR right now"]),
        _user_line("please merge the open PR right now"),
    ]
    decision, _reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW, (
        "strict-> check at idx-adjacency must return ALLOW: user (idx 1) "
        "is more recent than assistant Human emission (idx 0); the "
        "predicate is `assistant_idx > user_idx` so 0 > 1 is False → "
        "fall through to substring tiers → tier-1 match → ALLOW."
    )


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


# ─── main() wiring (subprocess) ────────────────────────────────────────

import subprocess


HOOK_PATH = Path(__file__).parent.parent / "hooks" / "hallucination_gate.py"


def _run_hook(stdin_payload: str | dict) -> tuple[int, str, str]:
    """Run the hook as a subprocess with the given stdin payload.

    Returns (exit_code, stdout, stderr). If `stdin_payload` is a dict,
    it is JSON-serialized; otherwise it is sent verbatim (for
    malformed-stdin tests).
    """
    if isinstance(stdin_payload, dict):
        stdin_payload = _json.dumps(stdin_payload)
    proc = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _make_transcript(tmp_path, lines: list[str]) -> str:
    """Write JSONL lines to a tmp file and return its path string."""
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _bash_envelope(
    command: str,
    transcript_path: str = "",
    cwd: str = "/tmp",
    session_id: str = "test-session-xyz",
    extra: dict | None = None,
) -> dict:
    """Build a PreToolUse envelope shaped like Claude Code's stdin."""
    env = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": transcript_path,
        "cwd": cwd,
        "session_id": session_id,
    }
    if extra:
        env.update(extra)
    return env


def test_main_malformed_stdin_allows():
    code, out, _err = _run_hook("not json at all")
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_non_dict_stdin_allows():
    code, out, _err = _run_hook("[1, 2, 3]")
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_non_bash_tool_allows():
    env = _bash_envelope("rm -rf /tmp/junk")
    env["tool_name"] = "Edit"
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_missing_command_allows():
    env = _bash_envelope("")
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_non_destructive_command_allows():
    env = _bash_envelope("ls -la")
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_missing_transcript_path_allows(tmp_path):
    # Destructive command but no transcript_path → fail-OPEN ALLOW.
    env = _bash_envelope("git push --force origin main")
    env["transcript_path"] = ""
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_nonexistent_transcript_path_allows():
    env = _bash_envelope(
        "gh pr merge 705",
        transcript_path="/nonexistent/path/to/transcript.jsonl",
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_empty_transcript_file_allows(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_text("", encoding="utf-8")
    env = _bash_envelope("gh pr merge 705", transcript_path=str(p))
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_teammate_caller_allows(tmp_path):
    # Even with destructive command + Human-emission triggering DENY,
    # the teammate short-circuit fires first (orchestrator-side
    # hallucination is a lead-side failure mode).
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: please rm -rf /tmp/junk for me"]),
    ])
    env = _bash_envelope("rm -rf /tmp/junk", transcript_path=transcript)
    env["agent_name"] = "backend-coder"  # resolved by resolve_agent_name step 1
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_destructive_with_no_human_emission_allows(tmp_path):
    transcript = _make_transcript(tmp_path, [
        _user_line("can you push to origin"),
        _assistant_line(["sure, running it"]),
    ])
    env = _bash_envelope("git push --force origin main", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_destructive_with_hallucinated_human_denies(tmp_path):
    # Corpus #3-at-gate-fire-time shape: assistant emitted Human:
    # but no genuine matching user turn.
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: yes proceed to peer review"]),
    ])
    env = _bash_envelope("gh pr merge 705", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert hsp["permissionDecision"] == "deny"
    assert "no_matching_user_message_in_scan_window" in hsp["permissionDecisionReason"]


def test_main_destructive_with_matching_user_allows(tmp_path):
    # Tier-1 exact-substring path through main().
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: yes proceed to peer review"]),
        _user_line("yes proceed to peer review"),
    ])
    env = _bash_envelope("gh pr merge 705", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_human_emission_more_recent_than_user_denies(tmp_path):
    # Genuine user turn exists, but assistant Human: emission is newer.
    transcript = _make_transcript(tmp_path, [
        _user_line("can you check the recent commits"),
        _assistant_line(["sure, here is what I see"]),
        _assistant_line(["Human: please run gh pr merge 705 now"]),
    ])
    env = _bash_envelope("gh pr merge 705", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["permissionDecision"] == "deny"
    assert "human_emission_more_recent_than_user_turn" in hsp["permissionDecisionReason"]


def test_main_warn_on_tier_miss_with_temporal_anchor_pass(tmp_path):
    # User turn exists and precedes the Human emission, but the
    # hallucinated text is not in the user turn → WARN, not DENY.
    # The command must be a REVERSIBLE destructive op so the
    # irreversible-subset escalation does not flip this case to DENY.
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: completely unrelated paraphrase here please"]),
        _user_line("can you summarize the recent commits for me"),
    ])
    env = _bash_envelope(
        "gh pr merge 705",  # reversible-via-revert → stays WARN
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert "additionalContext" in hsp
    assert "human_emission_text_not_found_in_recent_user_turns" in hsp["additionalContext"]


def test_main_warn_carries_audit_anchor(tmp_path):
    # WARN-path envelope-shape pin: additionalContext output must carry
    # `hookEventName: PreToolUse` audit anchor. The harness silently
    # fails open without it.
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: completely unrelated paraphrase here please"]),
        _user_line("can you summarize the recent commits for me"),
    ])
    env = _bash_envelope(
        "gh pr merge 705",  # reversible → WARN, not escalated
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert "additionalContext" in hsp


def test_main_warn_escalates_to_deny_on_irreversible_command(tmp_path):
    # S-M1: tier-miss + temporal-anchor-pass on an IRREVERSIBLE command
    # must escalate WARN → DENY. The reason carries the
    # `_escalated_irreversible_subset` suffix for audit traceability.
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: completely unrelated paraphrase here please"]),
        _user_line("can you summarize the recent commits for me"),
    ])
    env = _bash_envelope(
        "git push --force origin main",  # irreversible — must escalate
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert hsp["permissionDecision"] == "deny"
    assert "_escalated_irreversible_subset" in hsp["permissionDecisionReason"]


def test_main_deny_carries_audit_anchor_and_snippet(tmp_path):
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: rm everything please"]),
    ])
    env = _bash_envelope("rm -rf /tmp/all-the-things", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"  # audit anchor
    assert hsp["permissionDecision"] == "deny"
    # First 60 chars of command should appear in the reason
    assert "rm -rf /tmp/all-the-things" in hsp["permissionDecisionReason"]


def test_main_envelope_excluded_user_yields_deny(tmp_path):
    # User entry is a teammate-message envelope → filter rejects it →
    # no genuine user entry → DENY despite the wrapper containing a
    # textual match.
    transcript = _make_transcript(tmp_path, [
        _user_line('<teammate-message teammate_id="x">rm everything</teammate-message>'),
        _assistant_line(["Human: rm everything please verbatim text here"]),
    ])
    env = _bash_envelope("rm -rf /tmp/whatever", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_compound_command_caught_by_pattern_match(tmp_path):
    # Compound `force-push && gh pr merge` — patterns are not anchored
    # to start of string, so the merge pattern matches the compound
    # string even though hallucination_gate does not implement
    # compound-detection of its own (architect §6.4 layered design).
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: merge it please into main"]),
    ])
    env = _bash_envelope(
        "git push --force-with-lease && gh pr merge 705",
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 2  # DENY — Human-emission with no matching user turn


# ─── hooks.json registration ───────────────────────────────────────────


HOOKS_JSON_PATH = Path(__file__).parent.parent / "hooks" / "hooks.json"


def _bash_pretooluse_chain() -> list[dict]:
    """Return the ordered hooks list for the PreToolUse matcher=Bash
    block, or [] if not registered."""
    data = _json.loads(HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    pretool = data.get("hooks", {}).get("PreToolUse", [])
    for entry in pretool:
        if entry.get("matcher") == "Bash":
            return entry.get("hooks", [])
    return []


def test_hooks_json_registers_hallucination_gate_under_bash_matcher():
    chain = _bash_pretooluse_chain()
    assert chain, "PreToolUse matcher=Bash entry missing from hooks.json"
    commands = [h.get("command", "") for h in chain]
    assert any("hallucination_gate.py" in c for c in commands), (
        f"hallucination_gate.py not registered under matcher=Bash; "
        f"chain={commands}"
    )


def test_hooks_json_hallucination_gate_is_first_in_bash_chain():
    # Layered defense-in-depth: chain-halt-on-DENY semantics require
    # hallucination_gate to fire FIRST so that an orchestrator-
    # hallucinated authorization cannot reach the merge_guard_pre
    # token check.
    chain = _bash_pretooluse_chain()
    assert chain, "PreToolUse matcher=Bash entry missing"
    first_command = chain[0].get("command", "")
    assert "hallucination_gate.py" in first_command, (
        f"hallucination_gate must be first in matcher=Bash chain "
        f"(layered defense ordering); got first={first_command!r}"
    )


def test_hooks_json_bash_chain_preserves_companion_gates():
    # Commit 5 must NOT drop or reorder the existing companion entries
    # (git_commit_check, merge_guard_pre). Pin their continued presence
    # against accidental deletion.
    chain = _bash_pretooluse_chain()
    commands = [h.get("command", "") for h in chain]
    assert any("git_commit_check.py" in c for c in commands), (
        "git_commit_check.py was dropped from matcher=Bash chain"
    )
    assert any("merge_guard_pre.py" in c for c in commands), (
        "merge_guard_pre.py was dropped from matcher=Bash chain"
    )


def test_hooks_json_bash_chain_ordering_invariant():
    # hallucination_gate FIRST, then git_commit_check, then
    # merge_guard_pre. Pin the relative ordering.
    chain = _bash_pretooluse_chain()
    commands = [h.get("command", "") for h in chain]
    idx_hg = next(i for i, c in enumerate(commands) if "hallucination_gate.py" in c)
    idx_gc = next(i for i, c in enumerate(commands) if "git_commit_check.py" in c)
    idx_mg = next(i for i, c in enumerate(commands) if "merge_guard_pre.py" in c)
    assert idx_hg < idx_gc < idx_mg, (
        f"hooks.json matcher=Bash ordering invariant violated: "
        f"hallucination_gate@{idx_hg} git_commit_check@{idx_gc} "
        f"merge_guard_pre@{idx_mg}"
    )


def test_hooks_json_remains_valid_after_registration():
    # JSON parse + top-level shape sanity (defends against trailing-
    # comma / bracket-mismatch regressions introduced by editing the
    # registration block).
    data = _json.loads(HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]


# ─── Fixture corpus (captured-from-production + synthetic) ─────────────

FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "sample_transcripts"
    / "hallucination_gate"
)


@pytest.mark.parametrize(
    "fixture_name,expected_decision,expected_reason",
    [
        # Captured-from-production slices (session 95e44763)
        (
            "fixture_hallucination_pause.jsonl",
            DECISION_DENY,
            "no_matching_user_message_in_scan_window",
        ),
        (
            "fixture_hallucination_wrapup.jsonl",
            DECISION_DENY,
            "no_matching_user_message_in_scan_window",
        ),
        (
            "fixture_hallucination_peerreview_before_genuine.jsonl",
            DECISION_DENY,
            "human_emission_more_recent_than_user_turn",
        ),
        (
            "fixture_hallucination_peerreview_after_genuine.jsonl",
            DECISION_WARN,
            "human_emission_text_not_found_in_recent_user_turns",
        ),
        (
            "fixture_hallucination_peerreview_tier1_clean.jsonl",
            DECISION_ALLOW,
            "tier1_exact_substring",
        ),
        # Synthetic envelope-exclusion + baseline + malformed
        (
            "fixture_envelope_exclusion_teammate.jsonl",
            DECISION_DENY,
            "no_matching_user_message_in_scan_window",
        ),
        (
            "fixture_envelope_exclusion_task_notification.jsonl",
            DECISION_DENY,
            "no_matching_user_message_in_scan_window",
        ),
        (
            "fixture_benign_no_human_emission.jsonl",
            DECISION_ALLOW,
            "no_human_emission",
        ),
        (
            "fixture_malformed_jsonl.jsonl",
            DECISION_DENY,
            "human_emission_more_recent_than_user_turn",
        ),
    ],
)
def test_fixture_corpus_evaluations(fixture_name, expected_decision, expected_reason):
    path = FIXTURE_DIR / fixture_name
    assert path.exists(), f"missing fixture: {path}"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    decision, reason = evaluate_transcript(lines)
    assert (decision, reason) == (expected_decision, expected_reason), (
        f"{fixture_name}: got ({decision!r}, {reason!r}), "
        f"expected ({expected_decision!r}, {expected_reason!r})"
    )


def test_fixture_corpus_directory_exists():
    assert FIXTURE_DIR.is_dir(), (
        f"hallucination_gate fixture directory missing: {FIXTURE_DIR}"
    )


def test_fixture_corpus_minimum_cardinality():
    # 5 captured + 4 synthetic = 9 fixtures. Floor against accidental
    # deletion of captured-from-production data.
    fixtures = list(FIXTURE_DIR.glob("fixture_*.jsonl"))
    assert len(fixtures) >= 8, (
        f"hallucination_gate fixture count below floor: {len(fixtures)}"
    )


# ─── Architect scenario gap closure ────────────────────────────────────
# The architect spec enumerates 24 mandatory scenarios. Most have direct
# coverage above; the cases below close the remaining gaps and pin
# behavioral properties the comprehensive TEST phase guarantees.


def test_pattern_compile_failure_emits_audit_anchored_deny():
    # Architect scenario: module-load / pattern-compile failure produces
    # DENY with hookEventName='PreToolUse' audit anchor. Cannot import-
    # break the module from inside the test process without a subprocess
    # contortion, so exercise _emit_load_failure_deny directly via the
    # subprocess-friendly path: send a malformed envelope through a
    # subprocess with a forced-broken sys.path that masks the shared
    # pact_context import. The fail-CLOSED handler must still produce a
    # structured deny on stdout — empty stdout would silently fail open.
    import os

    proc = subprocess.run(
        [
            "python3",
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {str(HOOK_PATH.parent)!r}); "
                "import hallucination_gate as hg; "
                "hg._emit_load_failure_deny('module load', "
                "RuntimeError('synthetic regex compile failure'))"
            ),
        ],
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=10,
    )
    # SACROSANCT contract: nonzero exit code (sys.exit(2)) + structured
    # deny on stdout with hookEventName audit anchor.
    assert proc.returncode == 2
    payload = _json.loads(proc.stdout)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert hsp["permissionDecision"] == "deny"
    assert "module load" in hsp["permissionDecisionReason"]
    assert "synthetic regex compile failure" in hsp["permissionDecisionReason"]


def test_runtime_exception_fail_closes_via_load_failure_deny():
    # Architect scenario (companion to T9): an uncaught runtime exception
    # inside main() must fail-CLOSED with hookEventName audit anchor.
    # Exercise via _emit_load_failure_deny stage='runtime'.
    import os

    proc = subprocess.run(
        [
            "python3",
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {str(HOOK_PATH.parent)!r}); "
                "import hallucination_gate as hg; "
                "hg._emit_load_failure_deny('runtime', "
                "ValueError('synthetic runtime gate exception'))"
            ),
        ],
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 2
    payload = _json.loads(proc.stdout)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert hsp["permissionDecision"] == "deny"
    assert "runtime" in hsp["permissionDecisionReason"]


def test_main_heredoc_describing_destructive_op_allows(tmp_path):
    # Architect scenario: `gh pr merge 705` inside a `git commit -m`
    # heredoc must NOT trip the gate (the strip pipeline removes the
    # quoted message body before destructive-pattern scan).
    # Pin at the main()-entry level so the wiring is exercised end-to-end.
    transcript = _make_transcript(tmp_path, [
        _user_line("commit the staging area please"),
        _assistant_line(["sure, running git commit"]),
    ])
    env = _bash_envelope(
        "git commit -m 'describes gh pr merge 705 in the message body'",
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_main_gh_issue_create_with_hallucinated_priming_denies(tmp_path):
    # Architect scenario (#684 corollary): hallucinated `Human: please
    # file an issue` priming + `gh issue create` op must DENY.
    transcript = _make_transcript(tmp_path, [
        _other_line("system"),
        _assistant_line(["Human: please file an issue for this bug"]),
    ])
    env = _bash_envelope(
        "gh issue create -t 'Bug report' -b 'details'",
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert hsp["hookEventName"] == "PreToolUse"
    assert hsp["permissionDecision"] == "deny"
    assert "no_matching_user_message_in_scan_window" in hsp["permissionDecisionReason"]


def test_assistant_explanatory_mention_of_past_human_produces_deny():
    # Scenario: genuine user `merge it` at idx 0; assistant later
    # emits explanatory prose quoting the prior Human: directive. The
    # strict-> temporal-anchor check fires first: assistant idx >
    # user idx → DENY. v1 design accepts this: the gate cannot
    # distinguish quoted-explanation-of-prior-directive from fresh
    # hallucination; conservative behavior is to DENY both.
    #
    # The synthetic transcript uses the same quotation pattern as the
    # captured `peerreview_after_genuine` fixture slice: assistant
    # emits prose containing both the literal `Human:` marker and a
    # suffix that does NOT verbatim-match the earlier user turn (the
    # explanatory suffix is added).
    lines = [
        _user_line("merge it"),
        _assistant_line([
            "The orchestrator hallucinated `Human: merge it` — generated "
            "by me. Identical pattern: I ask a question, then hallucinate "
            "the answer."
        ]),
    ]
    decision, _reason = evaluate_transcript(lines)
    # If a future change wants to recognize explanatory quotations
    # (substring-of-prior-user-turn check), this test will flip and
    # require deliberate update.
    assert decision == DECISION_DENY


# ─── YELLOW item pins ──────────────────────────────────────────────────
# Three behaviors flagged during CODE phase that the architect spec
# describes by intent rather than implementation specifics. Pin the
# current implementation against silent regression.


def test_yellow_a_non_pact_session_with_no_transcript_allows(tmp_path):
    # Effect-equivalent ALLOW for a non-PACT session. The implementation
    # does not have an explicit "non-PACT session" short-circuit but
    # achieves the same outcome via: (1) resolve_agent_name returns ""
    # when no agent_name/agent_id/agent_type → does NOT short-circuit,
    # (2) transcript fail-OPEN ALLOW when transcript_path is missing
    # or unreadable. Pin the composite behavior.
    env = _bash_envelope("gh pr merge 705", transcript_path="")
    # Strip every PACT identifier — no agent_name, agent_id, agent_type.
    env.pop("agent_name", None)
    env.pop("agent_id", None)
    env.pop("agent_type", None)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_yellow_a_non_pact_session_with_empty_transcript_allows(tmp_path):
    # Second leg of non-PACT-session coverage: transcript file exists
    # but is empty → _read_last_n_lines returns [] → fail-OPEN ALLOW.
    p = tmp_path / "transcript.jsonl"
    p.write_text("", encoding="utf-8")
    env = _bash_envelope("rm -rf /tmp/junk", transcript_path=str(p))
    env.pop("agent_name", None)
    env.pop("agent_id", None)
    env.pop("agent_type", None)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_yellow_b_read_last_n_lines_large_file_chunk_branch(tmp_path):
    # _read_last_n_lines has a small-file branch (read+slice) and a
    # large-file branch (reverse-seek in 8KB chunks). The threshold is
    # 10MB. Synthesize a file just over the threshold and verify the
    # chunk-seek branch returns the last N lines correctly.
    from hallucination_gate import _read_last_n_lines, _LARGE_FILE_THRESHOLD_BYTES

    p = tmp_path / "large_transcript.jsonl"
    # Build a file just past the threshold by repeating a unique line
    # with a stable index marker. ~80 bytes per line × 140_000 lines
    # = ~11.2MB, comfortably past the 10MB threshold.
    line_template = (
        '{"type":"user","message":{"content":"line %d padding xxxxxxxxxxxxxxxxx"}}\n'
    )
    chunk = "".join(line_template % i for i in range(140_000))
    p.write_text(chunk, encoding="utf-8")
    assert p.stat().st_size > _LARGE_FILE_THRESHOLD_BYTES, (
        "fixture sizing must exceed large-file threshold to exercise "
        "the chunk-seek branch"
    )

    tail = _read_last_n_lines(p, 5)
    assert len(tail) == 5
    # Newest 5 lines must be 139_995..139_999 in order (newest at end).
    for offset, line in enumerate(tail):
        expected_idx = 140_000 - 5 + offset
        assert f"line {expected_idx}" in line, (
            f"chunk-seek branch returned wrong line at offset {offset}: "
            f"got {line!r}, expected to contain 'line {expected_idx}'"
        )


def test_yellow_b_read_last_n_lines_returns_empty_on_io_error(tmp_path):
    # The error-swallowing branch returns [] on any I/O error. Pass a
    # non-existent path — stat() raises FileNotFoundError → caught →
    # returns []. Callers treat empty as fail-OPEN ALLOW.
    from hallucination_gate import _read_last_n_lines

    missing = tmp_path / "does_not_exist.jsonl"
    assert _read_last_n_lines(missing, 10) == []


def test_yellow_c_deny_snippet_shows_bash_command_not_human_text(tmp_path):
    # DENY message snippet shows the Bash command (operator-readable)
    # rather than the assistant Human: text (diagnostic-readable). Pin
    # the operator-readable choice — if a future refactor wants to flip
    # to the diagnostic-readable form, this test must be updated
    # deliberately.
    distinctive_command = "rm -rf /tmp/unique-yellow-c-marker-path"
    distinctive_human_text = "alpha-beta-gamma-distinctive-human-marker"
    transcript = _make_transcript(tmp_path, [
        _assistant_line([f"Human: {distinctive_human_text} please proceed"]),
    ])
    env = _bash_envelope(distinctive_command, transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 2
    payload = _json.loads(out)
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    # The Bash command snippet appears in the message…
    assert distinctive_command in reason, (
        f"DENY message should embed the Bash command (operator-readable). "
        f"Got: {reason!r}"
    )
    # …and the assistant Human: text does NOT.
    assert distinctive_human_text not in reason, (
        f"DENY message should NOT embed the assistant Human: text "
        f"(implementation choice favors operator-readability). "
        f"Got: {reason!r}"
    )


# ─── Tag-push regex variant coverage ───────────────────────────────────
# Architect Q7 (tag-push regex refinement) was closed during CODE phase
# with the narrowed pattern set in DESTRUCTIVE_PATTERNS. Pin both the
# positive (catches tag pushes) and negative (does NOT catch ordinary
# branch pushes) cases against silent regex regression.


@pytest.mark.parametrize(
    "command",
    [
        # Explicit tag refspec
        "git push origin refs/tags/foo",
        "git push origin refs/tags/v1.2.3",
        "git push origin refs/tags/release-2026-04-01",
        # Semver-shaped positional — supports dotted (v1.2.3) AND dotless
        # (v1, 2) tag forms. The trailing `(?![\w-])` post-anchor rejects
        # branch-suffix forms; see test_tag_push_regex_dotless_v_prefix_caught
        # for the dotless coverage and the negative-list parametrize for
        # the rejected branch-with-digit-suffix forms.
        "git push origin v1.2.3",
        "git push origin v1.2.3-rc.1",
        "git push origin 1.2",
        "git push origin 1.2.3",
        # --tags flag — supports both first-position (push --tags) and
        # after-remote (push origin --tags) shapes via the (?:\S+\s+)*
        # flag-walk idiom. See test_tag_push_regex_tags_flag_after_remote_caught.
        "git push --tags",
        # Global flags between `git` and `push`
        "git -C /repo push origin v1.2.3",
        "git --git-dir=/r push origin refs/tags/foo",
    ],
)
def test_tag_push_regex_catches_tag_variants(command):
    assert is_destructive_command(command), (
        f"tag-push variant escaped DESTRUCTIVE_PATTERNS: {command!r}"
    )


@pytest.mark.parametrize(
    "command",
    [
        # Dotless v-prefix variants
        "git push origin v1",
        "git push origin v2",
        "git push origin v42",
        # Dotless plain-digit variants (single-token version refs)
        "git push origin 2",
        "git push origin 1024",
        # Global flags between `git` and `push` with dotless positional
        "git -C /repo push origin v1",
    ],
)
def test_tag_push_regex_dotless_v_prefix_caught(command):
    # Closed narrowing gap: the semver-shape regex now uses
    # `v?\d+(?:\.\d+)*(?![\w-])` (zero-or-more decimal groups + strict
    # post-anchor). Single-token tag-name positionals like `v1`, `v2`,
    # `2`, `1024` ARE caught as tag pushes. Paired negative parametrize
    # below pins the branch-suffix forms that the strict post-anchor
    # excludes.
    assert is_destructive_command(command), (
        f"dotless tag-push form escaped DESTRUCTIVE_PATTERNS: {command!r}"
    )


@pytest.mark.parametrize(
    "command",
    [
        # --tags after a remote positional
        "git push origin --tags",
        "git push upstream --tags",
        # --tags after flag-walk-shaped tokens
        "git push -v origin --tags",
        "git push --no-verify origin --tags",
        # --tags with an additional trailing positional
        "git push origin --tags v1.0",
        # Global flags between `git` and `push`
        "git -C /repo push origin --tags",
    ],
)
def test_tag_push_regex_tags_flag_after_remote_caught(command):
    # Closed narrowing gap: the --tags regex now uses
    # `push\s+(?:\S+\s+)*--tags\b` (flag-walk-shaped wildcard before
    # --tags). The common `git push origin --tags` form IS caught.
    assert is_destructive_command(command), (
        f"--tags-after-remote form escaped DESTRUCTIVE_PATTERNS: {command!r}"
    )


@pytest.mark.parametrize(
    "command",
    [
        # Ordinary branch pushes — must NOT trip the tag-push regex.
        "git push origin feature-x",
        "git push origin feature/my-work",
        "git push origin bugfix/issue-705",
        "git push origin main",  # main not gated by hallucination_gate
        "git push origin master",
        "git push origin develop",
        # Branch names containing digits but not semver-shaped
        "git push origin release-2026",
        "git push origin feature-v1-deprecated",
        # Branch names with v-prefix but not semver (alphabetic suffix)
        "git push origin vendor-update",
        "git push origin victory-lap",
        # Branch names with a digit-then-hyphen prefix — the strict
        # `(?![\w-])` post-anchor MUST reject these. A plain `\b` would
        # incorrectly admit them (digit→hyphen is a word-boundary), so
        # these pin the load-bearing post-anchor against regression.
        "git push origin 2-branch",
        "git push origin 1024-stuff",
        "git push origin v1-branch",
        # `git push` alone (no positional)
        "git push",
        "git push origin",
    ],
)
def test_tag_push_regex_excludes_branch_pushes(command):
    assert not is_destructive_command(command), (
        f"branch push incorrectly flagged as tag push: {command!r}"
    )


# ─── Edge cases beyond architect scenarios ─────────────────────────────
# Production-reality edge cases the architect did not specify. Highest-
# leverage class: boundary tests on the three named tunable constants
# (TRANSCRIPT_SCAN_WINDOW_LINES=500, SUBSTRING_LENGTH_FLOOR_CHARS=20,
# EXTRACTED_HUMAN_MAX_CHARS=200) and corpus-realistic transcript shapes.


def test_edge_human_emission_at_exact_scan_window_boundary(tmp_path):
    # Synthesize a transcript with Human: emission AT the line-500 scan-
    # window boundary. _read_last_n_lines returns last N lines, so the
    # boundary line is included in the scanned window.
    lines = []
    # Pad with 499 non-relevant assistant lines (no Human:)
    for i in range(499):
        lines.append(_assistant_line([f"benign assistant turn {i}"]))
    # Insert the Human: emission as the final (newest) line — index 499
    # in the slice → on the scan-window boundary.
    lines.append(_assistant_line(["Human: completely unauthorized destructive request"]))

    transcript = _make_transcript(tmp_path, lines)
    env = _bash_envelope("rm -rf /tmp/junk", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    # Last 500 lines include the Human: emission → DENY (no matching
    # user turn in window).
    assert code == 2
    payload = _json.loads(out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edge_human_emission_just_outside_scan_window_silently_allowed(tmp_path):
    # Emission OLDER than the scan window must not be seen → ALLOW.
    # Pin the windowed-scan property: emissions beyond the window
    # silently allow.
    lines = []
    # The oldest line is the Human: emission; it should be trimmed.
    lines.append(_assistant_line(["Human: destructive request out of window"]))
    # 510 newer lines push it past the window (window=500).
    for i in range(510):
        lines.append(_assistant_line([f"benign assistant turn {i}"]))

    transcript = _make_transcript(tmp_path, lines)
    env = _bash_envelope("rm -rf /tmp/junk", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_edge_extracted_human_truncation_at_exact_200_char_boundary():
    # EXTRACTED_HUMAN_MAX_CHARS=200. Verify slice is exactly 200 chars
    # when the post-Human: text is longer. Off-by-one would surface as
    # 199 or 201.
    boundary_text = "x" * EXTRACTED_HUMAN_MAX_CHARS
    one_over = "x" * (EXTRACTED_HUMAN_MAX_CHARS + 1)
    one_under = "x" * (EXTRACTED_HUMAN_MAX_CHARS - 1)

    assert extract_after_human(f"Human: {boundary_text}") == boundary_text
    assert extract_after_human(f"Human: {one_over}") == "x" * EXTRACTED_HUMAN_MAX_CHARS
    assert extract_after_human(f"Human: {one_under}") == one_under


def test_edge_substring_length_floor_exact_boundary():
    # SUBSTRING_LENGTH_FLOOR_CHARS=20. The check uses `<` so a 20-char
    # hallucinated string is NOT below the floor and proceeds to the
    # tier-1/2 substring compare. Pin the strict-less-than semantic.
    exact_floor = "x" * SUBSTRING_LENGTH_FLOOR_CHARS  # 20 chars
    below_floor = "x" * (SUBSTRING_LENGTH_FLOOR_CHARS - 1)  # 19 chars

    # 19-char emission with a (different) user turn → tier-0 short-circuit
    # ALLOW (below floor).
    lines = [
        _assistant_line([f"Human: {below_floor}"]),
        _user_line("yes ok go ahead with whatever you decided"),
    ]
    decision, reason = evaluate_transcript(lines)
    assert decision == DECISION_ALLOW
    assert reason == "tier0_below_length_floor_with_user_precedence"

    # 20-char emission with a non-matching user turn → tier-1 miss,
    # tier-2 miss → WARN (does not short-circuit on length).
    lines2 = [
        _assistant_line([f"Human: {exact_floor}"]),
        _user_line("completely unrelated user turn that does not match"),
    ]
    decision2, reason2 = evaluate_transcript(lines2)
    assert decision2 == DECISION_WARN
    assert reason2 == "human_emission_text_not_found_in_recent_user_turns"


def test_edge_nfkc_unicode_variants_in_tier2(tmp_path):
    # Tier-2 normalization is lower() + whitespace-collapse only — it
    # does NOT NFKC-normalize. Pin that behavior: fullwidth or composed-
    # variant Unicode in the hallucinated text does NOT match an ASCII
    # user turn, even though human readers see them as equivalent.
    # This documents the v1 limitation; if a future refactor adds NFKC
    # this test must flip.
    fullwidth_ascii = "ｙｅｓ ｐｒｏｃｅｅｄ ｗｉｔｈ ｍｅｒｇｅ"  # NFKC → "yes proceed with merge"
    plain_ascii = "yes proceed with merge"
    # normalize() is case-fold + whitespace-collapse only.
    assert normalize(fullwidth_ascii) != normalize(plain_ascii)
    # Therefore tier-2 substring miss.
    assert normalize(fullwidth_ascii) not in normalize(plain_ascii)
    assert normalize(plain_ascii) not in normalize(fullwidth_ascii)


def test_edge_assistant_block_with_embedded_tool_use_then_text():
    # Assistant content blocks may interleave tool_use and text blocks.
    # The walk must skip non-text blocks and still find the Human:
    # substring in the text block.
    msg = _json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "x", "name": "Bash",
             "input": {"command": "ls"}},
            {"type": "tool_use", "id": "y", "name": "Read",
             "input": {"file_path": "/etc/passwd"}},
            {"type": "text", "text": "Human: please run gh pr merge 705"},
        ]},
    })
    lines = [
        _user_line("an earlier unrelated user keystroke from long ago"),
        msg,
    ]
    decision, _reason = evaluate_transcript(lines)
    # Temporal anchor: assistant idx (1) > user idx (0) → DENY.
    assert decision == DECISION_DENY


def test_edge_malformed_jsonl_interleaved_with_valid(tmp_path):
    # Bond-stress on the json.JSONDecodeError continue branch: 5
    # malformed lines interleaved with valid ones. The walk must skip
    # all malformed lines without crashing AND extract the correct
    # decision from the valid lines.
    lines = [
        "not json",
        _user_line("yes proceed with the merge as planned today"),
        "{malformed",
        '"a bare string"',
        _assistant_line(["Human: yes proceed with the merge as planned today"]),
        "{another: malformed",
        "",
        "[]",
    ]
    decision, reason = evaluate_transcript(lines)
    # User idx (1) < assistant idx (4) → assistant emission more recent
    # than user → DENY (temporal anchor).
    assert decision == DECISION_DENY
    assert reason == "human_emission_more_recent_than_user_turn"


def test_edge_compound_command_chained_with_pipes_and_subshells(tmp_path):
    # Production reality: destructive commands often appear in compound
    # chains. The regex `search` (not `match`) catches the destructive
    # pattern anywhere in the stripped command string.
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: please do all the cleanup steps for me"]),
    ])
    compound_chains = [
        # && chain
        "echo 'starting' && rm -rf /tmp/junk && echo 'done'",
        # ; sequence
        "git status; git push --force origin main; git status",
        # | pipe (rare for destructive ops but possible)
        "echo y | gh pr merge 705",
        # $(subshell) — destructive op inside a subshell
        "echo $(gh release create v1.2.3)",
    ]
    for command in compound_chains:
        env = _bash_envelope(command, transcript_path=transcript)
        code, _out, _err = _run_hook(env)
        assert code == 2, (
            f"compound command not denied: {command!r} (code={code})"
        )


def test_edge_race_open_file_unlinked_between_exists_and_read(tmp_path):
    # Production race: transcript_path exists at the `path.exists()`
    # check but is unlinked before _read_last_n_lines opens it. The
    # I/O error must be swallowed → fail-OPEN ALLOW.
    from hallucination_gate import _read_last_n_lines

    p = tmp_path / "race.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    # Simulate the race by passing a Path whose stat will raise. The
    # easiest reliable simulation: unlink the file, then call _read.
    p.unlink()
    assert _read_last_n_lines(p, 10) == []


# ─── Negative tests (false-positive resistance) ────────────────────────
# Legitimate user-authorized destructive operations MUST flow through.
# False-positive cost is high — operators rely on these commands daily.


def test_negative_user_typed_rm_rf_node_modules_allows(tmp_path):
    # Real user typed `rm -rf node_modules` — tier-1 exact substring
    # match against the latest user turn passes the gate.
    transcript = _make_transcript(tmp_path, [
        _other_line("system"),
        _assistant_line(["Human: rm -rf node_modules"]),
        _user_line("rm -rf node_modules"),
    ])
    env = _bash_envelope("rm -rf node_modules", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_negative_user_typed_git_reset_hard_allows(tmp_path):
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: git reset --hard HEAD~1"]),
        _user_line("git reset --hard HEAD~1"),
    ])
    env = _bash_envelope("git reset --hard HEAD~1", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_negative_user_typed_gh_pr_merge_allows(tmp_path):
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: gh pr merge 705"]),
        _user_line("gh pr merge 705"),
    ])
    env = _bash_envelope("gh pr merge 705", transcript_path=transcript)
    code, out, _err = _run_hook(env)
    assert code == 0
    assert _json.loads(out) == {"suppressOutput": True}


def test_negative_user_typed_destructive_with_intervening_assistant_prose(tmp_path):
    # Real flow: user types command, assistant explains, assistant runs.
    # The user keystroke must remain the anchor; intervening assistant
    # prose (without Human:) does not flip the gate.
    transcript = _make_transcript(tmp_path, [
        _user_line("git push --force-with-lease origin feature-x"),
        _assistant_line(["sure, let me run that for you now"]),
        _assistant_line(["here is the dry-run output first"]),
    ])
    # Hallucination_gate fires before merge_guard; the command itself
    # is force-push without --force, which IS in DESTRUCTIVE_PATTERNS
    # for the `--force(?!-with-lease)` pattern. Use the --force-with-
    # lease form to confirm the gate ALLOWs (pattern uses negative
    # lookahead).
    env = _bash_envelope(
        "git push --force-with-lease origin feature-x",
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    # The command is NOT destructive per the regex set (force-with-
    # lease is excluded), so we exit at the is_destructive_command
    # short-circuit.
    assert _json.loads(out) == {"suppressOutput": True}


# ─── passes_envelope_exclusion predicate-identity pins ─────────────────
# These tests pin the CURRENT (correct) state of `passes_envelope_
# exclusion` against three failure modes: an always-True predicate
# (would silently admit every envelope as a genuine user turn), an
# empty curated tuple (would no-op the historical sample-set audit
# anchor), and an overbroad curated tuple (would reject genuine
# keystrokes). They are predicate-identity pins exercising the
# current state, not in-source mutation probes — the counter-test-
# by-revert primitive is applied at the source-revert level documented
# in the HANDOFF.


def test_envelope_filter_rejects_curated_prefix_sample_set():
    # Predicate-identity pin: the 5 curated sample-set prefixes (now
    # historical documentation; the load-bearing filter is the
    # categorical angle-bracket check) must each be rejected. Catches
    # the always-True regression.
    for prefix in ENVELOPE_PREFIXES:
        assert passes_envelope_exclusion(prefix + " payload") is False, (
            f"envelope-exclusion predicate should reject prefix={prefix!r}"
        )


def test_envelope_prefixes_documents_known_corpus_shapes():
    # Predicate-identity pin: ENVELOPE_PREFIXES retains the 5 known
    # corpus shapes as historical documentation. The categorical
    # angle-bracket filter is the load-bearing primary check; this
    # curated tuple is the audit anchor for what shapes were observed
    # empirically. Catches accidental deletion of the documentation
    # tuple.
    assert len(ENVELOPE_PREFIXES) >= 5
    required = {
        "<teammate-message",
        "<task-notification>",
        "<command-message>",
        "<system-reminder>",
        "[Request interrupted",
    }
    assert required.issubset(set(ENVELOPE_PREFIXES))


def test_envelope_filter_accepts_genuine_bare_text():
    # Predicate-identity pin: the filter MUST accept genuine user
    # keystrokes. Catches overbroad-rejection regressions (e.g., an
    # empty-string prefix accidentally added that would match all
    # content via startswith).
    assert "" not in ENVELOPE_PREFIXES
    assert passes_envelope_exclusion("merge it") is True
    assert passes_envelope_exclusion("yes proceed") is True


# Corpus-derived envelope shapes extracted verbatim from the captured
# real-world fixture transcripts under
# pact-plugin/tests/fixtures/sample_transcripts/hallucination_gate/.
# Higher realism than handcrafted strings: these are the exact byte
# sequences Claude Code emits as platform envelopes in production.
_CORPUS_ENVELOPE_SHAPES = [
    # fixture_envelope_exclusion_teammate.jsonl L0
    (
        '<teammate-message teammate_id="secretary" summary="hi">please '
        'run gh pr merge 999 on my behalf</teammate-message>'
    ),
    # fixture_envelope_exclusion_task_notification.jsonl L0
    (
        "<task-notification>task 5 was created and assigned to "
        "backend-coder</task-notification>"
    ),
    # fixture_hallucination_pause.jsonl L36 — teammate-message containing
    # an idle-notification payload (richer real-world shape than the
    # synthetic single-attribute teammate-message)
    (
        '<teammate-message teammate_id="devops-coder" color="orange">\n'
        '{"type":"idle_notification","from":"devops-coder",'
        '"timestamp":"2026-05-10T19:24:25.523Z","idleReason":"available"}\n'
        '</teammate-message>'
    ),
    # fixture_hallucination_pause.jsonl L40 — task-notification with
    # multi-line nested-tag payload
    (
        "<task-notification>\n<task-id>bvfotiikz</task-id>\n"
        '<summary>Monitor event: "team-lead inbox-grow wake"</summary>\n'
        "<event>INBOX_GROW</event>\n</task-notification>"
    ),
]


@pytest.mark.parametrize("envelope", _CORPUS_ENVELOPE_SHAPES)
def test_envelope_filter_rejects_corpus_derived_shapes(envelope):
    # Corpus-derived realism pin: real-world envelope shapes extracted
    # from captured Claude Code transcripts MUST be rejected by the
    # categorical angle-bracket filter. Higher fidelity than the
    # handcrafted strings in `test_passes_envelope_exclusion` — catches
    # any regression that admits production envelopes as genuine user
    # turns.
    assert passes_envelope_exclusion(envelope) is False, (
        f"corpus-derived envelope must be rejected: {envelope[:80]!r}..."
    )
