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


@pytest.mark.parametrize("pin, expected", [
    ("github.com/Acme/Widget", "github.com/acme/widget"),
    ("www.github.com/Acme/Widget", "github.com/acme/widget"),
    ("github.com:443/Acme/Caf%C3%A9", "github.com:443/acme/caf%C3%A9"),
    ("github.com:443/Acme/Widget", "github.com:443/acme/widget"),
    ("ssh.github.com:443/Acme/Widget", "ssh.github.com:443/Acme/Widget"),
    ("www.github.com:8443/Acme/Widget", "www.github.com:8443/Acme/Widget"),
    ("gitlab.com/Acme/Widget", "gitlab.com/Acme/Widget"),
    # A host is case-insensitive in a pin as in a remote.
    ("GitHub.com/Acme/Widget", "github.com/acme/widget"),
    ("WWW.GitHub.com/Acme/Widget", "github.com/acme/widget"),
    ("GITHUB.COM:8443/Acme/Widget", "github.com:8443/acme/widget"),
    ("GitLab.com/Acme/Widget", "gitlab.com/Acme/Widget"),
])
def test_a_pin_takes_githubs_spelling_rules(pin, expected):
    """A github.com or port-less www.github.com pin is folded as its remote
    would be (a ported github.com path is lower-cased too); a ported www or
    another host is kept as written. The resolver and the sibling check share
    this rule."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    assert pin_file_value((pin + "\n").encode()) == (expected, None)
    if expected.startswith("github.com/"):
        assert pi.normalize_remote_url("https://" + pin) == expected


@pytest.mark.parametrize("pin", [
    "github.com/acme",
    "www.github.com/acme",
    "github.com/a/b/c",
    "www.github.com/a/b/c",
    "github.com/o~x/r",
    "github.com/o/r~",
    "github.com/o/r+x",
    "github.com/o/r%2Fx",
    "github.com/acme/caf%C3%A9",
    "www.github.com/acme/caf%C3%A9",
])
def test_a_github_pin_that_is_not_owner_repo_of_github_names_is_refused(pin):
    """A port-less github.com pin is refused exactly where a remote naming it
    declines: not exactly owner/repo, or a decoded name GitHub does not allow.
    RED WHEN a pin binds an id no remote derives."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    assert pin_file_value((pin + "\n").encode()) == (None, "not_canonical")
    assert pi.normalize_remote_url("https://" + pin) is None


@pytest.mark.parametrize("pin", [
    "github.com/o/%41b",
    "github.com/O/%41B",
    "github.com/o/a%62",
    "github.com/%6F/r",
    "github.com/O/R%2Dx",
    "github.com/o/r%5Fx",
    "github.com/o/r%2Ex",
    "www.github.com/O/%41b",
])
def test_an_escaped_github_pin_is_the_id_its_remote_gives(pin):
    """A port-less github.com pin is decoded and folded by the remote's own
    step (`database.github_repo_path`), so every escaped spelling of a
    repository is the one id a remote naming it gives. RED WHEN a pin keeps
    an escape its remote decodes, binding an id no remote derives."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    remote = pi.normalize_remote_url("https://" + pin)
    assert remote is not None and "%" not in remote, remote
    assert pin_file_value((pin + "\n").encode()) == (remote, None)


@pytest.mark.parametrize("pin", [
    "github.com:8443/O/%41b",
    "github.com:8443/O/R%2Dx",
    "github.com:8443/o/Caf%C3%A9",
    "www.github.com:8443/O/%41b",
    "h.example/o/%41b",
    "h.example:8443/%7Euser/r",
    # Lower-case hex of an unreserved character, decoded as a remote's is.
    "h.example/o/r%6a",
    "h.example/o/r%2e",
    "github.com:8443/O/R%2dx",
    "github.com/O/R%2dx",
])
def test_an_escaped_ported_or_other_host_pin_is_the_id_its_remote_gives(pin):
    """Every pin's path escapes take the remote's own spelling
    (`database.canonical_escape`): an escaped unreserved character is
    decoded, any other escape is upper-cased. RED WHEN a ported github.com or
    another host's pin keeps an escape its remote decodes."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    remote = pi.normalize_remote_url("https://" + pin)
    assert remote is not None, pin
    assert pin_file_value((pin + "\n").encode()) == (remote, None)


def test_the_remote_and_the_pin_share_one_unreserved_set():
    """The remote grammar keeps its own raw-character set for parsing; the
    escape rule both read is database's. RED WHEN the two sets drift."""
    from scripts import project_identity as pi
    from scripts.database import UNRESERVED_CHARS

    assert pi._UNRESERVED_CHARS == UNRESERVED_CHARS


def test_a_mixed_case_pin_and_its_lower_case_origin_are_one_owner(tmp_path):
    """The cold review's layout: clone B pinned to github.com/Acme/Widget and
    clone A on origin github.com/acme/widget have one id, so each is the
    other's same owner."""
    from scripts import project_identity as pi

    assert_outside_repository(tmp_path)
    a = make_repo(tmp_path / "a" / "widget", remote="https://github.com/acme/widget.git")
    b = make_repo(tmp_path / "b" / "widget", remote="https://github.com/acme/widget.git")
    (b / ".git" / "pact-project-id").write_text("github.com/Acme/Widget\n", encoding="utf-8")
    assert pi.read_override(b / ".git").value == "github.com/acme/widget"
    assert pi.detect_id_for_root(a, a / ".git", asked_root=a).project_id == "github.com/acme/widget"
    assert checkout_is_same_owner(str(b), "github.com/acme/widget", "https://github.com/acme/widget.git") is True


@pytest.mark.parametrize("pin, expected", [
    ("github.com/Acme/Widget.git", ("github.com/acme/widget", None)),
    ("github.com/acme/widget.GIT", ("github.com/acme/widget", None)),
    ("github.com/acme/widget.git.git", (None, "not_canonical")),
    ("github.com/acme/.git", (None, "not_canonical")),
    ("github.com:8443/acme/widget.git", ("github.com:8443/acme/widget.git", None)),
    ("gitlab.com/acme/widget.git", ("gitlab.com/acme/widget.git", None)),
])
def test_a_github_pin_pasted_with_its_dot_git_names_the_remotes_id(pin, expected):
    """GitHub removes a trailing .git from a repository's name when it is
    created, so no port-less github.com repository ends in .git: a pin pasted
    from the remote url drops one .git, in any case, and names the id the
    remote gives; one that still ends in .git names nothing. A ported
    github.com pin and other hosts keep it. RED WHEN the pasted pin keeps the
    suffix and splits its checkout from every clone."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    assert pin_file_value((pin + "\n").encode()) == expected
    if expected[0] == "github.com/acme/widget":
        assert pi.normalize_remote_url("https://github.com/acme/widget.git") == expected[0]


@pytest.mark.parametrize("raw", [
    b"github.com/a/b\x1cjunk\n",
    b"github.com/a/b\x0b\n",
    b"github.com/a/b\x0c\n",
    b"github.com/a/b\xc2\x85x\n",
    b"github.com/a/b\x1c\n",
])
def test_a_pin_line_holding_a_control_character_is_refused(raw):
    """A pin line ends at \\n only. splitlines() would also break at \\x0b,
    \\x0c, \\x1c-\\x1e and \\x85, cutting the control character off a
    candidate that then reads as a clean id. RED WHEN such a pin binds."""
    from scripts.database import pin_file_value

    assert pin_file_value(raw) == (None, "control_chars")


@pytest.mark.parametrize("raw", [b"github.com/a/b\r\n", b"  github.com/a/b \n", b"# c\n\n\tgithub.com/a/b\t\r\n"])
def test_a_pin_keeps_its_spaces_crlf_and_comment_lines(raw):
    """Control: surrounding spaces and tabs, CRLF, blank and # lines still
    read as before."""
    from scripts.database import pin_file_value

    assert pin_file_value(raw) == ("github.com/a/b", None)


@pytest.mark.parametrize("pin", ["h.example/o/r%c3", "h.example/o/r%2f", "h.example/o/%2e%2e"])
def test_a_pin_keeps_refusing_a_lower_case_escape_of_any_other_byte(pin):
    """Only an escaped unreserved character is decoded before the canonical
    check; a lower-case escape of any other byte stays as written and is
    refused, as ruled, and a decoded '..' is still traversal."""
    from scripts.database import pin_file_value

    assert pin_file_value((pin + "\n").encode()) == (None, "not_canonical")


@pytest.mark.parametrize("spelling", ["widget.GIT", "widget.Git", "widget.git.git", "widget%2Egit"])
def test_a_github_remote_whose_name_still_ends_in_dot_git_declines(spelling):
    """No GitHub repository name ends in .git, so a github.com remote whose
    name still does after its one .git is dropped reaches nothing and
    declines, in the memory id and the backlog. RED WHEN it binds an id."""
    from scripts import project_identity as pi

    assert pi.normalize_remote_url(f"https://github.com/acme/{spelling}") is None
    assert pi.normalize_remote_url(f"git@github.com:acme/{spelling}") is None
    assert pi.normalize_remote_url("https://github.com/acme/widget.git") == "github.com/acme/widget"


def test_a_pasted_dot_git_pin_is_the_checkouts_id_with_no_detail(tmp_path, monkeypatch, capsys):
    """`project-id show` reports the pasted pin as the remote's id, with no
    identity_detail. RED WHEN the pin's .git reaches the id."""
    import json

    from scripts.cli import main
    from scripts.database import db_connection, ensure_initialized

    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "widget", remote="https://github.com/acme/widget.git")
    (repo / ".git" / "pact-project-id").write_text("github.com/Acme/Widget.git\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    with db_connection(tmp_path / "store.db") as conn:
        ensure_initialized(conn)
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(tmp_path / "store.db")])
    assert exit_info.value.code == 0
    shown = json.loads(capsys.readouterr().out)["result"]
    assert shown["source"] == "override file", shown
    assert shown["project_id"] == "github.com/acme/widget", shown
    assert shown["identity_detail"] is None, shown


@pytest.mark.parametrize("url, pin", [
    ("https://github.com:8443/O/N", "github.com:8443/O/N"),
    ("ssh://git@github.com:443/O/N", "github.com:443/O/N"),
    ("https://github.com:8443/O", "github.com:8443/O"),
    ("https://www.github.com:8443/O/N", "www.github.com:8443/O/N"),
    ("http://ssh.github.com:443/O/N", "ssh.github.com:443/O/N"),
    ("ssh://git@ssh.github.com:2222/O/N", "ssh.github.com:2222/O/N"),
])
def test_a_ported_pin_copied_from_its_url_names_the_remotes_id(url, pin):
    """A pin written as the url's host, port and path names the id that url's
    remote gets: one GitHub rule for pins and remotes."""
    from scripts import project_identity as pi
    from scripts.database import pin_file_value

    assert pin_file_value((pin + "\n").encode()) == (pi.normalize_remote_url(url), None)
