"""
Direct unit tests for the extracted SSOT detector shared/stale_session.py.

detect_stale_session_block compares this frame's live stdin session_id against
the project CLAUDE.md '- Resume:' line id and returns an advisory warning string
on mismatch, else None. Both consumers (bootstrap_prompt_gate via additionalContext,
dispatch_gate via the deny-message augmentation) read this one implementation.

Historically this logic lived in bootstrap_prompt_gate.py and its branch coverage
rode that module's test file via an aliased import; PR-cycle review (F3) flagged
that the SSOT module deserves its own direct test file so the coupling cannot rot
if a future consumer un-aliases or the import graph changes. This file pins the
detector's contract DIRECTLY:

  - the 5 documented None-returning branches (bad/missing session_id; PROJECT_DIR
    unset; neither CLAUDE.md exists; read raises OSError/UnicodeDecodeError; no
    Resume line; recorded==actual healthy),
  - the two-path .claude/CLAUDE.md-preferred precedence (parity with
    resolve_project_claude_md_path),
  - the recorded-id regex shape,
  - the positive mismatch case (returns the warning naming recorded + actual).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.stale_session import (  # noqa: E402 — sys.path insert above
    detect_stale_session_block,
    _RESUME_LINE_RE,
)

_LIVE_ID = "11111111-2222-4000-8000-000000000000"
_RECORDED_DIFFERENT = "99999999-8888-4000-8000-000000000000"


def _write_claude_md(dir_path: Path, recorded_id, *, legacy=False):
    """Write a CLAUDE.md with a Resume line carrying ``recorded_id``.

    legacy=False → ``<dir>/.claude/CLAUDE.md`` (the preferred path).
    legacy=True  → ``<dir>/CLAUDE.md`` (the legacy fallback path).
    """
    if legacy:
        target = dir_path / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = dir_path / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Project\n\n## Current Session\n"
        f"- Resume: `claude --resume {recorded_id}`\n"
        "- Team: `session-deadbeef`\n",
        encoding="utf-8",
    )
    return target


# =============================================================================
# Positive: a recorded != actual mismatch returns the warning
# =============================================================================


def test_mismatch_returns_warning_naming_recorded_and_actual(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    result = detect_stale_session_block({"session_id": _LIVE_ID})

    assert result is not None
    assert _RECORDED_DIFFERENT in result, "warning names the recorded id"
    assert _LIVE_ID in result, "warning names the actual (live) id"
    assert "stale session block" in result


# =============================================================================
# None-branch 5: recorded == actual (healthy resume)
# =============================================================================


def test_healthy_recorded_equals_actual_returns_none(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _LIVE_ID)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# None-branch 1: missing / invalid stdin session_id
# =============================================================================


@pytest.mark.parametrize(
    "bad_id",
    [None, "", "   ", "unknown-abcd1234", "good\nbad", 12345],
    ids=["none", "empty", "whitespace", "unknown_sentinel", "control_char", "non_string"],
)
def test_bad_session_id_returns_none(bad_id, tmp_path, monkeypatch):
    """A missing/blank/sentinel/control-char/non-string id → None: nothing
    trustworthy to compare, and an unvalidated id must never be interpolated
    into the warning. CLAUDE.md records a DIFFERENT id, so a naive compare
    would 'mismatch' — the predicate gate must suppress it."""
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": bad_id}) is None


def test_missing_session_id_key_returns_none(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({}) is None


# =============================================================================
# None-branch 2: CLAUDE_PROJECT_DIR unset
# =============================================================================


def test_project_dir_unset_returns_none(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# None-branch 3: neither CLAUDE.md exists
# =============================================================================


def test_no_claude_md_anywhere_returns_none(tmp_path, monkeypatch):
    """Empty project dir — neither .claude/CLAUDE.md nor ./CLAUDE.md (the
    worktree/gitignored case). content stays None → None."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()

    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# None-branch 3b: read raises UnicodeDecodeError (non-UTF-8 CLAUDE.md)
# =============================================================================


def test_non_utf8_claude_md_returns_none(tmp_path, monkeypatch):
    """A corrupted/non-UTF-8 CLAUDE.md (the partial-write this detector exists
    to flag) raises UnicodeDecodeError on read_text → swallowed → None. The
    helper is advisory; its failure budget is 'no warning', never a raise that
    would suppress a consumer's whole injection."""
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # 0x80 is an invalid UTF-8 start byte → read_text(encoding='utf-8') raises.
    target.write_bytes(b"# Project\n- Resume: `claude --resume \x80\x81`\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# None-branch 4: no Resume line matches the regex
# =============================================================================


def test_no_resume_line_returns_none(tmp_path, monkeypatch):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Project\n\nNo session block here.\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# Two-path precedence: .claude/CLAUDE.md is preferred over ./CLAUDE.md
# =============================================================================


def test_preferred_claude_md_wins_over_legacy(tmp_path, monkeypatch):
    """When BOTH .claude/CLAUDE.md and ./CLAUDE.md exist, the preferred
    .claude/CLAUDE.md is read. Construct a mismatch where the two files
    disagree: preferred records the LIVE id (→ healthy → None), legacy records
    a DIFFERENT id (→ would mismatch). Result None proves preferred won."""
    _write_claude_md(tmp_path, _LIVE_ID, legacy=False)        # preferred: healthy
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT, legacy=True)  # legacy: stale
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Preferred (.claude/CLAUDE.md) records the live id → healthy → None.
    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


def test_legacy_claude_md_used_when_preferred_absent(tmp_path, monkeypatch):
    """When only ./CLAUDE.md exists (no .claude/CLAUDE.md), the legacy path is
    read and a mismatch there IS detected."""
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT, legacy=True)
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    result = detect_stale_session_block({"session_id": _LIVE_ID})
    assert result is not None
    assert _RECORDED_DIFFERENT in result


# =============================================================================
# Recorded-id regex shape
# =============================================================================


@pytest.mark.parametrize(
    "line,expected",
    [
        ("- Resume: `claude --resume abc123-def`", "abc123-def"),
        ("- Resume:   `claude --resume deadbeef`", "deadbeef"),  # extra spaces
        ("- Resume: `claude --resume 11111111-2222-4000-8000-000000000000`",
         "11111111-2222-4000-8000-000000000000"),
    ],
    ids=["hex_dash", "extra_spaces", "full_uuid"],
)
def test_resume_line_regex_extracts_hex_id(line, expected):
    m = _RESUME_LINE_RE.search(line)
    assert m is not None
    assert m.group(1) == expected


@pytest.mark.parametrize(
    "line",
    [
        "- Resume: claude --resume abc123",       # no backticks
        "- Resume: `claude --resume ABC123`",     # uppercase not in [0-9a-f-]
        "- Resyme: `claude --resume abc123`",     # mistyped label
        "random text with no resume",
    ],
    ids=["no_backticks", "uppercase", "mistyped_label", "no_match"],
)
def test_resume_line_regex_rejects_malformed(line):
    assert _RESUME_LINE_RE.search(line) is None
