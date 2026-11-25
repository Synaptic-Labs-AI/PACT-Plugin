"""
Location: pact-plugin/tests/test_same_owner_checkout.py
Summary: The files-only reads behind the same-owner rule: the strict
`origin_url_in_config` parser, and `checkout_is_same_owner`, which judges a
store-named checkout with no git and fails closed on every form it does not
read.
Used by: pytest; exercises scripts.database on real files and real clones.
"""

from __future__ import annotations

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo, make_worktree

from scripts.database import checkout_is_same_owner, origin_url_in_config

URL = "git@github.com:acme/proj.git"
ID = "github.com/acme/proj"


@pytest.mark.parametrize("text, expected", [
    ('[core]\n\tbare = false\n[remote "origin"]\n\turl = git@h:o/r.git\n', "git@h:o/r.git"),
    ('[remote "origin"]\n\tURL=https://h/o/r\n\turl = second\n', "https://h/o/r"),
    ('[remote "upstream"]\n\turl = https://h/o/r\n', None),
    ('[remote.origin]\n\turl = https://h/o/r\n', None),
    ('[include]\n\tpath = x\n[remote "origin"]\n\turl = https://h/o/r\n', None),
    ('[includeIf "gitdir:/x/"]\n\tpath = x\n[remote "origin"]\n\turl = https://h/o/r\n', None),
    ('[remote "origin"]\n\turl = "https://h/o/r"\n', None),
    ('[remote "origin"]\n\turl = https://h/o/r # note\n', None),
    ('[remote "origin"]\n\turl = https://h/o/r\\\n', None),
    ("", None),
])
def test_the_strict_origin_reader(tmp_path, text, expected):
    config = tmp_path / "config"
    config.write_text(text, encoding="utf-8")
    assert origin_url_in_config(config) == expected


def test_a_fifo_config_is_not_read(tmp_path):
    import os

    os.mkfifo(tmp_path / "config")
    assert origin_url_in_config(tmp_path / "config") is None


def test_a_plain_clone_with_the_same_url_is_the_same_owner(tmp_path):
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote=URL)
    assert checkout_is_same_owner(str(clone), ID, URL) is True


def test_another_url_spelling_stays_a_rival(tmp_path):
    """Fail closed: no normalization runs on a store-named checkout."""
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote="https://github.com/acme/proj.git")
    assert checkout_is_same_owner(str(clone), ID, URL) is False


def test_a_matching_pin_is_the_same_owner_and_a_different_one_is_not(tmp_path):
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote=URL)
    (clone / ".git" / "pact-project-id").write_text(ID + "\n", encoding="utf-8")
    assert checkout_is_same_owner(str(clone), ID, None) is True
    (clone / ".git" / "pact-project-id").write_text("github.com/acme/other\n", encoding="utf-8")
    assert checkout_is_same_owner(str(clone), ID, URL) is False


def test_a_linked_worktree_and_a_plain_folder_are_not_judged(tmp_path):
    assert_outside_repository(tmp_path)
    main = make_repo(tmp_path / "b" / "proj", remote=URL)
    worktree = make_worktree(main, tmp_path / "wt" / "proj")
    folder = tmp_path / "plain" / "proj"
    folder.mkdir(parents=True)
    assert checkout_is_same_owner(str(worktree), ID, URL) is False
    assert checkout_is_same_owner(str(folder), ID, URL) is False


def test_a_session_with_no_url_matches_no_unpinned_clone(tmp_path):
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote=URL)
    assert checkout_is_same_owner(str(clone), ID, None) is False
