"""
Location: pact-plugin/tests/test_merge_guard_cd_prefix.py
Summary: An approval of `cd <dir> && <op>` covers `<op>` run from <dir>.
Used by: pytest.

The harness drops a leading `cd <session cwd> &&` from a Bash command before a
hook sees it, so an approval that binds the command's text must also cover what
is left when every dropped step lands in the directory the command runs in.
Steps whose directory resolves elsewhere, or cannot be resolved without
expanding the shell, keep the exact match.
"""

from __future__ import annotations

import os

import pytest
from merge_guard_post import _retire_exact_text_token, write_token
from merge_guard_pre import check_merge_authorization
from shared.merge_guard_common import exact_text_matches, set_command_directory

_OP = "git push --" + "force origin feat/x"


@pytest.fixture
def dirs(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    link = tmp_path / "link"
    link.symlink_to(here)
    (here / "self").symlink_to(here)
    return {"here": str(here), "other": str(other), "link": str(link)}


@pytest.mark.parametrize("approved", [
    "cd {here} && {op}",
    "cd {here}/ && {op}",
    'cd "{here}" && {op}',
    "cd '{here}' && {op}",
    "cd {link} && {op}",
    "cd {here}; {op}",
    "cd . && {op}",
    "cd {here} && cd {here} && {op}",
    "cd {here} && cd . && {op}",
], ids=["plain", "trailing-slash", "double-quoted", "single-quoted", "symlink-to-cwd", "semicolon", "dot",
        "two-steps", "two-steps-dot"])
def test_a_cd_to_the_cwd_before_the_op_is_covered(dirs, approved):
    text = approved.format(op=_OP, **dirs)
    assert exact_text_matches(text, _OP, dirs["here"])
    assert exact_text_matches(text, _OP, dirs["link"])


def test_a_shorter_suffix_of_the_chain_is_covered(dirs):
    text = "cd {here} && cd {here} && {op}".format(op=_OP, **dirs)
    assert exact_text_matches(text, "cd {here} && {op}".format(op=_OP, **dirs), dirs["here"])


@pytest.mark.parametrize("approved,executed", [
    ("cd {other} && {op}", "{op}"),
    ("cd $HOME && {op}", "{op}"),
    ('cd "$PWD" && {op}', "{op}"),
    ("cd - && {op}", "{op}"),
    ("cd self && {op}", "{op}"),
    ("cd {here} || {op}", "{op}"),
    ("cd {here} && {op}", "{op} --tags"),
    ("cd {here} && {op}", "cd {other} && {op}"),
    ("cd {here} && cd {other} && {op}", "{op}"),
], ids=["other-dir", "variable", "pwd-variable", "previous-dir", "cdpath-name", "or-separator",
        "different-op-text", "different-cd", "second-step-elsewhere"])
def test_anything_else_keeps_the_exact_match(dirs, approved, executed):
    text, command = approved.format(op=_OP, **dirs), executed.format(op=_OP, **dirs)
    assert not exact_text_matches(text, command, dirs["here"])


def test_without_a_cwd_only_the_exact_text_matches(dirs):
    text = "cd {here} && {op}".format(op=_OP, **dirs)
    assert not exact_text_matches(text, _OP, None)
    assert exact_text_matches(text, text, None)


def _approve(token_dir, approved):
    from shared.merge_guard_common import extract_command_context, normalize_command_text

    ctx = extract_command_context(_OP)
    assert write_token({**ctx, "command_text": normalize_command_text(approved)}, token_dir=token_dir)


@pytest.mark.parametrize("cwd_key,allowed", [("here", True), ("link", True), ("other", False)])
def test_the_guard_runs_the_stripped_command_only_from_the_approved_dir(dirs, tmp_path, cwd_key, allowed):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    _approve(token_dir, "cd {here} && {op}".format(op=_OP, **dirs))
    set_command_directory(dirs[cwd_key])
    try:
        assert (check_merge_authorization(_OP, token_dir=token_dir) is None) is allowed
    finally:
        set_command_directory(None)


@pytest.mark.parametrize("cwd_key,retired", [("here", True), ("other", False)])
def test_the_stripped_command_retires_its_approval_only_from_the_approved_dir(dirs, tmp_path, cwd_key, retired):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    _approve(token_dir, "cd {here} && {op}".format(op=_OP, **dirs))
    response = {"stdout": "", "stderr": "To github.com:o/r.git\n + 1a2b...5d6e feat/x -> feat/x (forced update)",
                "interrupted": False}
    _retire_exact_text_token(_OP, dirs[cwd_key], token_dir=token_dir, stdout_text="", tool_response=response)
    assert bool(list(token_dir.glob("merge-authorized-*.consumed"))) is retired
    assert os.path.isdir(token_dir)
