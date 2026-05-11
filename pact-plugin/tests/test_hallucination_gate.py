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
    transcript = _make_transcript(tmp_path, [
        _assistant_line(["Human: completely unrelated paraphrase here please"]),
        _user_line("can you summarize the recent commits for me"),
    ])
    env = _bash_envelope(
        "git push --force origin main",
        transcript_path=transcript,
    )
    code, out, _err = _run_hook(env)
    assert code == 0
    payload = _json.loads(out)
    hsp = payload["hookSpecificOutput"]
    assert "additionalContext" in hsp
    assert "human_emission_text_not_found_in_recent_user_turns" in hsp["additionalContext"]


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
