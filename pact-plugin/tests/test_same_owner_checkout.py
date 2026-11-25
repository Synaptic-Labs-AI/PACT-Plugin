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
    # Every origin url is read: an empty or unreadable later one refuses the
    # file, as read_origin declines an empty entry anywhere in the list.
    ('[remote "origin"]\n\turl = https://h/o/r\n\turl =\n', None),
    ('[remote "origin"]\n\turl = https://h/o/r\n\turl\n', None),
    ('[remote "origin"]\n\turl = https://h/o/r\n[remote "origin"]\n\turl = "q"\n', None),
    ('[remote "origin"]\n\turl = https://h/o/r\n[include]\n\tpath = x\n', None),
    ('[remote.origin]\n\turl = https://h/e/v\n[remote "origin"]\n\turl = https://h/o/r\n', None),
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


@pytest.mark.parametrize("pin", [
    ID + "\r\n",
    "# pinned by hand\n" + ID + "\n",
    "  " + ID + "  \n",
    "\ufeff" + ID + "\n",
    "\n\n" + ID,
])
def test_a_pin_is_read_as_the_resolver_reads_it(tmp_path, pin):
    """The pin's text rule is the resolver's (`pin_file_value`): CRLF, a
    comment line first, surrounding spaces and a BOM all still name the id."""
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote="https://github.com/acme/other.git")
    (clone / ".git" / "pact-project-id").write_bytes(pin.encode("utf-8"))
    assert checkout_is_same_owner(str(clone), ID, URL) is True


def test_a_rejected_pin_falls_back_to_the_url_as_the_resolver_does(tmp_path):
    assert_outside_repository(tmp_path)
    clone = make_repo(tmp_path / "b" / "proj", remote=URL)
    (clone / ".git" / "pact-project-id").write_text("not-canonical\n", encoding="utf-8")
    assert checkout_is_same_owner(str(clone), ID, URL) is True


def test_a_rejected_own_pin_keeps_url_matching(tmp_path, monkeypatch):
    """This repository's pin file is present but rejected, so its id comes
    from its origin: siblings are still matched by url."""
    from scripts.memory_api import PACTMemory

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "a" / "proj", remote=URL)
    (repo / ".git" / "pact-project-id").write_text("not-canonical\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    memory = PACTMemory(session_id="s", db_path=tmp_path / "m.db")
    assert memory.project_id == ID, "precondition: the rejected pin is ignored"
    assert memory._own_identity(repo / ".git") == (ID, URL)


def test_the_pin_size_cap_is_the_resolvers(tmp_path):
    from scripts import database
    from scripts import project_identity

    assert database.PIN_FILE_MAX_BYTES == project_identity._MAX_OVERRIDE_BYTES

def _git(repo, *args):
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.mark.parametrize(
    "arrange", ["one", "two", "empty_later", "valueless_later", "include", "dotted_first", "none"]
)
def test_the_file_reader_never_answers_where_read_origin_declines(tmp_path, arrange):
    """Parity with the resolver's own read (git config --get-all): whenever
    the file reader gives a url, read_origin gives the same first url; where
    read_origin declines (an empty entry, a valueless one, no origin) the file
    reader gives none, and it also gives none for any include, which it
    cannot follow (the safe direction)."""
    from scripts import project_identity as pi

    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "r" / "proj", remote=None if arrange == "none" else URL)
    config = repo / ".git" / "config"
    if arrange == "two":
        _git(repo, "config", "--add", "remote.origin.url", "https://github.com/acme/proj")
    elif arrange == "empty_later":
        _git(repo, "config", "--add", "remote.origin.url", "")
    elif arrange == "valueless_later":
        with open(config, "a", encoding="utf-8") as handle:
            handle.write('[remote "origin"]\n\turl\n')
    elif arrange == "dotted_first":
        # git reads the deprecated `[remote.origin]` as remote.origin, so its
        # url comes FIRST and read_origin binds it, not the url after it.
        text = config.read_text(encoding="utf-8")
        config.write_text('[remote.origin]\n\turl = https://h/evil/repo\n' + text, encoding="utf-8")
    elif arrange == "include":
        (tmp_path / "extra.config").write_text('[remote "origin"]\n\turl = https://h/x/y\n', encoding="utf-8")
        _git(repo, "config", "include.path", str(tmp_path / "extra.config"))
    origin = pi.read_origin(repo / ".git")
    found = origin_url_in_config(config)
    if found is not None:
        assert origin.value == found, (arrange, origin, found)
    if arrange in ("one", "two"):
        assert found == URL
    else:
        assert found is None, (arrange, origin)
