"""
Which file the pin-cap gate checks, and a Write that creates it.

The gate checks the project CLAUDE.md the resolver returns once the change
exists (`claude_md_manager.gate_target`), and compares the change with the file
the resolver returns before it, or with empty text when none resolves. Every
row drives the shipped `pin_caps_gate.py` as a subprocess on a real PreToolUse
frame, against real files in tmp_path, with CLAUDE_PROJECT_DIR and the working
directory set to the project. The unit rows below pin `same_path`, the
resolver's `assume_present` and `gate_target`'s refusals to gate.

A path the gate is unsure of is not gated, so it is allowed.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shared.claude_md_manager import MEMORY_END_MARKER, MEMORY_START_MARKER
from test_pin_caps_gate import SNIPPET, _edit, _frame, _outcome, _pins, _run_hook, _write  # noqa: E402 — sibling harness reuse

NEW_PIN = "<!-- pinned: 2026-04-21 -->\n### New\nbody\n\n## Working Memory"


def _gate(project, target, call):
    tool, tool_input = call
    return _outcome(_run_hook(project, _frame(target, tool, tool_input)))


def _case_insensitive_volume(directory):
    probe = directory / "CaseProbe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "CASEPROBE").exists()
    finally:
        probe.unlink()


def _skip_unless_case_insensitive(directory):
    if not _case_insensitive_volume(directory):
        pytest.skip("the tmp volume matches names with case, so a case-variant spelling names another file")


def _without_memory_markers(text):
    return "".join(line for line in text.splitlines(keepends=True)
                   if line.strip() not in (MEMORY_START_MARKER, MEMORY_END_MARKER))


# ---------------------------------------------------------------------------
# Which file is gated, with the text before named
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("location", [".claude/CLAUDE.md", "CLAUDE.md"])
@pytest.mark.parametrize("pins, expected", [(13, "deny"), (12, "allow")])
def test_both_files_absent_a_write_is_counted_by_its_own_pins(tmp_path, location, pins, expected):
    assert _gate(tmp_path, tmp_path / location, _write(_pins(pins))) == expected


@pytest.mark.parametrize("pins, expected", [(13, "allow"), (14, "deny")])
def test_a_write_to_dot_claude_is_compared_with_the_legacy_file_that_resolves(tmp_path, pins, expected):
    (tmp_path / "CLAUDE.md").write_text(_pins(13), encoding="utf-8")
    assert _gate(tmp_path, tmp_path / ".claude" / "CLAUDE.md", _write(_pins(pins))) == expected


def test_a_write_to_dot_claude_adding_pins_to_a_small_legacy_file_is_denied(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(_pins(5), encoding="utf-8")
    assert _gate(tmp_path, tmp_path / ".claude" / "CLAUDE.md", _write(_pins(13))) == "deny"


def test_a_write_to_the_legacy_file_while_dot_claude_resolves_is_not_gated(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_pins(3), encoding="utf-8")
    assert _gate(tmp_path, tmp_path / "CLAUDE.md", _write(_pins(20))) == "allow"


def _git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, timeout=60)


@pytest.mark.parametrize("pins, expected", [(13, "allow"), (14, "deny")])
def test_a_worktree_write_is_compared_with_the_repository_roots_file(tmp_path, pins, expected):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo, worktree = tmp_path / "repo", tmp_path / "worktree"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "worktree", "add", "-q", str(worktree))
    (repo / ".claude").mkdir()  # untracked, so the worktree has no CLAUDE.md of its own
    (repo / ".claude" / "CLAUDE.md").write_text(_pins(13), encoding="utf-8")
    assert not (worktree / ".claude").exists()
    assert _gate(worktree, worktree / ".claude" / "CLAUDE.md", _write(_pins(pins))) == expected


@pytest.mark.parametrize("call", [_write(_pins(14)), _edit("## Working Memory", NEW_PIN)], ids=["Write", "Edit"])
def test_a_change_through_a_case_variant_spelling_is_gated(tmp_path, call):
    _skip_unless_case_insensitive(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_pins(13), encoding="utf-8")
    assert _gate(tmp_path, tmp_path / ".claude" / "claude.md", call) == "deny"


@pytest.mark.parametrize("dot_claude_exists", [True, False])
def test_a_first_write_through_a_case_variant_spelling_is_gated(tmp_path, dot_claude_exists):
    _skip_unless_case_insensitive(tmp_path)
    if dot_claude_exists:
        (tmp_path / ".claude").mkdir()
    assert _gate(tmp_path, tmp_path / ".claude" / "claude.md", _write(_pins(13))) == "deny"


@pytest.mark.parametrize("relative", ["CLAUDE.md", ".claude/CLAUDE.md"])
def test_a_relative_file_path_is_not_gated(tmp_path, relative):
    assert _gate(tmp_path, Path(relative), _write(_pins(13))) == "allow"


@pytest.mark.parametrize("pins", [12, 13])
def test_a_write_to_dot_claude_over_an_unreadable_legacy_file_is_allowed_with_an_advisory(tmp_path, pins):
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so EACCES cannot be produced")
    legacy = tmp_path / "CLAUDE.md"
    legacy.write_text(_pins(13), encoding="utf-8")
    legacy.chmod(0o000)
    try:
        assert _gate(tmp_path, tmp_path / ".claude" / "CLAUDE.md", _write(_pins(pins))) == "advisory"
    finally:
        legacy.chmod(0o644)


# ---------------------------------------------------------------------------
# A first Write: nothing resolves before it, so its own pins are counted
# ---------------------------------------------------------------------------

STRAY_PAIR = {5: "xxxx\n```", 6: "```\nxxxx"}  # pin 6's heading sits inside the pair
NOT_LOCATED = "# notes\n\n```\nunclosed\n\n"

FIRST_WRITES = [
    ("12 pins", _pins(12), "allow"),
    ("13 pins", _pins(13), "deny"),
    ("12 pins and a snippet holding two ### lines", _pins(12, {3: SNIPPET}), "allow"),
    ("13 pins and a snippet holding two ### lines", _pins(13, {3: SNIPPET}), "deny"),
    ("13 pins with one hidden by a stray fence pair", _pins(13, STRAY_PAIR), "allow"),
    ("the Pinned section past an unclosed fence, 20 pins", NOT_LOCATED + _pins(20), "advisory"),
    ("the Pinned section past an unclosed fence, 3 pins", NOT_LOCATED + _pins(3), "advisory"),
    ("13 pins with CRLF line endings", _pins(13).replace("\n", "\r\n"), "deny"),
    ("13 pins after a byte order mark", "﻿" + _pins(13), "deny"),
]


@pytest.mark.parametrize("name, content, expected", FIRST_WRITES, ids=[row[0] for row in FIRST_WRITES])
def test_a_first_write(tmp_path, name, content, expected):
    assert _gate(tmp_path, tmp_path / ".claude" / "CLAUDE.md", _write(content)) == expected


def test_the_not_located_advisory_names_the_line_it_could_not_read(tmp_path):
    """The advisory names the row where the uncertain region starts, so the
    user can find the unclosed fence."""
    result = _run_hook(tmp_path, _frame(tmp_path / ".claude" / "CLAUDE.md", *_write(NOT_LOCATED + _pins(20))))
    assert _outcome(result) == "advisory"
    assert "line 3" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_both_memory_markers_removed_allows_with_the_advisory_and_keeps_allowing(tmp_path):
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir()
    claude_md.write_text(_pins(13), encoding="utf-8")
    assert _gate(tmp_path, claude_md, _write(_without_memory_markers(_pins(18)))) == "advisory"
    claude_md.write_text(_without_memory_markers(_pins(18)), encoding="utf-8")
    assert _gate(tmp_path, claude_md, _write(_without_memory_markers(_pins(19)))) == "advisory"


# ---------------------------------------------------------------------------
# A writer holding the CLAUDE.md lock neither delays nor decides the gate
# ---------------------------------------------------------------------------

_HOLD_LOCK = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('held', flush=True)\n"
    "sys.stdin.read()\n"
)


def test_a_held_writer_lock_neither_delays_nor_decides_the_gate(tmp_path):
    """A child holds the writers' sidecar lock, with their own fcntl.flock,
    for the whole row. The gate reads without it: a rename at 13 pins is
    allowed and a pin added is denied, both well inside the writers' 5 s lock
    timeout, which a gate waiting on the lock would reach."""
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir()
    claude_md.write_text(_pins(13), encoding="utf-8")
    sidecar = claude_md.parent.resolve() / f".{claude_md.name}.lock"
    holder = subprocess.Popen([sys.executable, "-c", _HOLD_LOCK, str(sidecar)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdin is not None and holder.stdout is not None
    try:
        assert holder.stdout.readline().strip() == "held"
        start = time.monotonic()
        assert _gate(tmp_path, claude_md, _write(_pins(13).replace("### Pin5\n", "### Pin5 renamed\n"))) == "allow"
        assert _gate(tmp_path, claude_md, _write(_pins(14))) == "deny"
        assert time.monotonic() - start < 5.0
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)


# ---------------------------------------------------------------------------
# Unit rows: same_path, assume_present, gate_target
# ---------------------------------------------------------------------------

def test_same_path_reads_one_file_through_a_symlinked_directory_as_the_same(tmp_path):
    from staleness import same_path

    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert same_path(tmp_path / "link" / "CLAUDE.md", tmp_path / "real" / "CLAUDE.md")
    (tmp_path / "real" / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert same_path(tmp_path / "link" / "CLAUDE.md", tmp_path / "real" / "CLAUDE.md")


def test_same_path_reads_different_names_and_an_existing_beside_a_missing_file_as_different(tmp_path):
    from staleness import same_path

    assert not same_path(tmp_path / ".claude" / "CLAUDE.md", tmp_path / "CLAUDE.md")
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert not same_path(tmp_path / "CLAUDE.md", tmp_path / "other" / "CLAUDE.md")


def test_same_path_folds_case_only_where_the_volume_does(tmp_path):
    from staleness import same_path

    folded = same_path(tmp_path / ".claude" / "claude.md", tmp_path / ".claude" / "CLAUDE.md")
    assert folded is _case_insensitive_volume(tmp_path)


def test_same_path_compares_exactly_under_a_directory_whose_name_has_no_cased_letters(tmp_path):
    from staleness import same_path

    _skip_unless_case_insensitive(tmp_path)
    (tmp_path / "2026").mkdir()
    assert not same_path(tmp_path / "2026" / "claude.md", tmp_path / "2026" / "CLAUDE.md")


def test_same_path_never_raises(tmp_path, monkeypatch):
    import staleness

    assert staleness.same_path(tmp_path / "a\0b", tmp_path / "CLAUDE.md") is False

    def _denied(a, b):
        raise PermissionError("denied")

    monkeypatch.setattr(staleness.os.path, "samefile", _denied)
    assert staleness.same_path(tmp_path, tmp_path) is False


def test_assume_present_counts_a_missing_candidate_only_when_asked(tmp_path):
    from staleness import _find_existing_claude_md

    target = tmp_path / ".claude" / "CLAUDE.md"
    assert _find_existing_claude_md(tmp_path) is None
    assert _find_existing_claude_md(tmp_path, assume_present=target) == target
    assert _find_existing_claude_md(tmp_path, assume_present=tmp_path / "CLAUDE.md") == tmp_path / "CLAUDE.md"
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    # an existing legacy file does not outrank the preferred location counted as present
    assert _find_existing_claude_md(tmp_path, assume_present=target) == target


@pytest.mark.parametrize("file_path", ["", "CLAUDE.md", ".claude/CLAUDE.md", "/abs/notes.md", None, 7])
def test_gate_target_gates_no_relative_empty_or_other_named_path(tmp_path, monkeypatch, file_path):
    from helpers import point_resolver_at
    from shared.claude_md_manager import gate_target

    point_resolver_at(monkeypatch, tmp_path)
    assert gate_target(file_path) is None


def test_gate_target_names_the_target_the_file_before_and_the_base(tmp_path, monkeypatch):
    from helpers import point_resolver_at
    from shared.claude_md_manager import gate_target

    point_resolver_at(monkeypatch, tmp_path)
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    target = tmp_path / ".claude" / "CLAUDE.md"
    assert gate_target(str(target)) == (target, tmp_path / "CLAUDE.md", tmp_path)


def test_gate_target_returns_none_when_the_resolver_fails(tmp_path, monkeypatch):
    import staleness
    from helpers import point_resolver_at
    from shared.claude_md_manager import gate_target

    point_resolver_at(monkeypatch, tmp_path)

    def _fails(assume_present=None):
        raise OSError("resolver failed")

    monkeypatch.setattr(staleness, "_resolve_project_claude_md_with_base", _fails)
    assert gate_target(str(tmp_path / ".claude" / "CLAUDE.md")) is None
