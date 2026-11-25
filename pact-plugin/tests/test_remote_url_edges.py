"""
Location: pact-plugin/tests/test_remote_url_edges.py
Summary: Remote url spellings that must give the same id as their plain form:
a percent-encoded `.git` suffix, an Azure DevOps collection name in any case
and an empty port; a host with an empty label, which declines; and a config
text whose `[include]` line continues a value, which git reads as part of
that value.
Used by: the test suite; no production code imports it.
"""

import pytest

from scripts import project_identity as pi


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/o/r%2Egit", "github.com/o/r"),
        ("https://github.com/o/r%2egit/", "github.com/o/r"),
        ("https://h.example/o/R%2E%67%69%74", "h.example/o/R"),
        ("https://h.example/o/%2Egit", "h.example/o"),
        # scp syntax sends `%2E` to the server as written, so it names a
        # repository called `r%2Egit`, not `r.git`, and keeps its suffix.
        ("git@h.example:o/r%2Egit", "h.example/o/r%252Egit"),
    ],
)
def test_an_encoded_git_suffix_is_dropped_as_the_plain_one_is(url, expected):
    assert pi.normalize_remote_url(url) == expected


@pytest.mark.parametrize("collection", ["DefaultCollection", "defaultcollection", "DEFAULTCOLLECTION"])
def test_the_azure_collection_name_matches_in_any_case(collection):
    url = f"https://acme.visualstudio.com/{collection}/P/_git/R"
    assert pi.normalize_remote_url(url) == "dev.azure.com/acme/P/_git/R"


@pytest.mark.parametrize(
    "text, expected",
    [
        ('[core]\n\tk = a \\\n[include]\n\tpath = x\n', False),
        ('[core]\n\tk = a ; note \\\n[include]\n\tpath = x\n', True),
        ('[core]\n\tk = a \\\\\n[include]\n\tpath = x\n', True),
        ('[core] k = a \\\n[include]\n\tpath = x\n', False),
        ('[core]\n; comment \\\n[include]\n\tpath = x\n', True),
    ],
)
def test_a_line_that_continues_a_value_is_no_section_header(text, expected):
    """Each expected answer is git's own (`git config --file --list`, git
    2.43): a value ending in an unescaped backslash takes the next line, so
    an `[include]` there is part of the value, not a header."""
    assert pi._holds_include_section(text) is expected


@pytest.mark.parametrize(
    ("ssh_url", "scp_url"),
    [
        ("ssh://git@h.example/srv/a%2Bb", "git@h.example:srv/a+b"),
        ("ssh://h.example/srv/a%21%24%26%27%28%29%2A%2C%3B%3D", "h.example:srv/a!$&'()*,;="),
        ("git+ssh://h.example/o/r%2Bs", "h.example:o/r+s"),
        ("ssh+git://h.example/o/r%2Bs", "h.example:o/r+s"),
        ("ssh://h.example/o/%C3%A9", "h.example:o/é"),
        ("ssh://h.example/o/a%2525b", "h.example:o/a%25b"),
    ],
)
def test_an_ssh_url_path_is_decoded_as_git_decodes_it(ssh_url, scp_url):
    """git decodes an ssh url's whole path before it sends it (measured with
    a logging GIT_SSH: `ssh://h/srv/a%2Bb` sends `/srv/a+b`), and sends an
    scp path as written. RED WHEN only `%2F` is decoded: the two spellings
    of one repository get two ids."""
    assert pi.normalize_remote_url(ssh_url) == pi.normalize_remote_url(scp_url) is not None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ssh://h.example/o/My%20Project", "h.example/o/My%20Project"),
        ("ssh://h.example/o/%41b%25c", "h.example/o/Ab%25c"),
        ("https://h.example/o/r%2Bs", "h.example/o/r%2Bs"),
    ],
)
def test_an_escape_a_segment_needs_survives_the_decode(url, expected):
    """A decoded character a segment may not hold raw is escaped again, and
    a scheme other than ssh keeps its escapes."""
    assert pi.normalize_remote_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/O/Caf%C3%A9", "github.com/o/caf%C3%A9"),
        ("https://github.com/o/café", "github.com/o/caf%C3%A9"),
        ("https://github.com/O/R.GIT", "github.com/o/r"),
        ("git@github.com:O/R.Git", "github.com/o/r"),
        ("https://h.example/o/r.GIT", "h.example/o/r.GIT"),
        ("https://h.example/o/r.git", "h.example/o/r"),
    ],
)
def test_github_case_folding_keeps_escapes_and_only_dot_git_is_a_suffix(url, expected):
    """GitHub's path is lower-cased outside its percent escapes, which stay
    upper-case as the id grammar requires; elsewhere only a lower-case
    `.git` is dropped, because git looks up exactly `<path>.git`. RED WHEN
    an escape is lower-cased (a non-ASCII GitHub path declines) or `.GIT`
    is dropped on a case-sensitive server."""
    assert pi.normalize_remote_url(url) == expected


def test_the_backlog_parser_reads_an_ssh_plus_git_remote():
    """The two parsers share one scheme set."""
    from shared import backlog

    assert backlog._slug_from_remote_url("ssh+git://git@github.com/o/r.git") == ("o", "r")
    assert backlog._REMOTE_SCHEMES >= {"ssh", "git+ssh", "ssh+git"}


@pytest.mark.parametrize(
    "url, expected",
    [
        ("ssh://git@[2001:db8::1]/o/r.git", "[2001:db8::1]/o/r"),
        ("ssh://git@[2001:DB8:0::1]:22/o/r.git", "[2001:db8::1]/o/r"),
        ("ssh://git@[2001:db8::1]:2222/o/r.git", "[2001:db8::1]:2222/o/r"),
        ("https://[2001:db8::1]/o/r", "[2001:db8::1]/o/r"),
        ("https://[2001:db8::1]:8443/o/r", "[2001:db8::1]:8443/o/r"),
        ("git@[2001:db8::1]:o/r.git", "[2001:db8::1]/o/r"),
    ],
)
def test_a_bracketed_ipv6_host_is_one_canonical_id(url, expected):
    """An IPv6 literal keeps its brackets and takes its one compressed
    spelling, so every spelling of one address names one id; a default port
    is dropped as for a name."""
    assert pi.normalize_remote_url(url) == expected
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_CANONICAL
    assert pi.is_canonical(expected)


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@[2001:db8::1/o/r",
        "ssh://git@[zz::1]/o/r",
        "ssh://git@[fe80::1%eth0]/o/r",
        "https://[2001:db8::1]x/o/r",
        "https://[2001:db8::1]:0/o/r",
    ],
)
def test_a_malformed_bracketed_host_declines(url):
    assert pi.normalize_remote_url(url) is None
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_DECLINE


@pytest.mark.parametrize("value", ["[2001:DB8::1]/o/r", "[2001:db8:0::1]/o/r", "[zz::1]/o/r"])
def test_only_the_compressed_spelling_is_a_canonical_id(value):
    assert not pi.is_canonical(value)


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@[::ffff:192.0.2.1]/o/r.git",
        "https://[::ffff:c000:201]:8443/o/r",
        "git@[::ffff:192.0.2.1]:o/r.git",
    ],
)
def test_an_ipv4_mapped_literal_declines(url):
    """An IPv4-mapped literal names an IPv4 host that also has a plain
    spelling, so it would give one server two ids: it declines."""
    assert pi.normalize_remote_url(url) is None
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_DECLINE


@pytest.mark.parametrize("value", ["[::ffff:c000:201]/o/r", "[::ffff:192.0.2.1]/o/r"])
def test_an_ipv4_mapped_id_is_not_canonical(value):
    """Neither spelling, including the compressed one `ipaddress` prints."""
    assert not pi.is_canonical(value)

@pytest.mark.parametrize(
    "url, expected",
    [
        ("ssh://git@example.com:/o/r.git", "example.com/o/r"),
        ("https://example.com:/o/r", "example.com/o/r"),
        ("ssh://git@[2001:db8::1]:/o/r", "[2001:db8::1]/o/r"),
    ],
)
def test_an_empty_port_is_the_default_port(url, expected):
    """git passes no port for `ssh://h:/o/r`, the same as `ssh://h/o/r`, so
    an empty port names the same server and the same id."""
    assert pi.normalize_remote_url(url) == expected
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_CANONICAL


@pytest.mark.parametrize(
    "url", ["https://a..b/o/r", "ssh://git@a..b/o/r", "git@a..b:o/r", "https://a.b..c:8443/o/r"]
)
def test_a_host_with_an_empty_label_declines(url):
    """The host check refuses it on its own, before the id grammar does."""
    assert pi._valid_host(url.split("//")[-1].split("@")[-1].split(":")[0].split("/")[0]) is False
    assert pi.normalize_remote_url(url) is None
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_DECLINE


@pytest.mark.parametrize("value", ["a..b/o/r", "a.b..c:8443/o/r", "a...b/o/r"])
def test_an_id_with_an_empty_host_label_is_not_canonical(value):
    assert not pi.is_canonical(value)


@pytest.mark.parametrize("value", ["a.b/o/r", "a-.b/o/r", "a.b.c:8443/o/r", "a/o/r"])
def test_control_a_host_with_no_empty_label_is_canonical(value):
    assert pi.is_canonical(value)


@pytest.mark.parametrize("url", [
    "ssh://git@ssh.github.com:443/Acme/Widget.git",
    "ssh://git@ssh.github.com/Acme/Widget.git",
    "git@ssh.github.com:Acme/Widget.git",
])
def test_githubs_ssh_over_443_host_is_github(url):
    """GitHub's SSH-over-443 endpoint is github.com, as the backlog's remote
    parser already reads it: both name the same repository."""
    from shared import backlog

    assert pi.normalize_remote_url(url) == "github.com/acme/widget"
    owner, name = backlog._slug_from_remote_url(url)
    assert f"github.com/{owner}/{name}".lower() == pi.normalize_remote_url(url)


@pytest.mark.parametrize("url", ["https://ssh.github.com/Acme/Widget", "http://ssh.github.com/acme/widget"])
def test_the_ssh_443_fold_is_for_ssh_only(url):
    """GitHub's ssh.github.com serves ssh; an https remote there is another
    endpoint: it keeps its own id, and the backlog's remote parser reads no
    github.com slug from it either, so the two parsers agree."""
    from shared import backlog

    assert pi.normalize_remote_url(url).startswith("ssh.github.com/")
    assert backlog._slug_from_remote_url(url) is None


@pytest.mark.parametrize("url", ["https://www.github.com/Acme/Widget.git", "http://www.github.com/acme/widget"])
def test_githubs_www_host_is_github(url):
    """GitHub redirects www.github.com to github.com: one repository, one id,
    as the backlog's remote parser reads it."""
    from shared import backlog

    assert pi.normalize_remote_url(url) == "github.com/acme/widget"
    owner, name = backlog._slug_from_remote_url(url)
    assert f"github.com/{owner}/{name}".lower() == pi.normalize_remote_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/owner/.git",
    "https://github.com/owner",
    "git@github.com:owner.git",
    "https://www.github.com/owner",
])
def test_a_one_segment_github_path_declines(url):
    """A github.com id is always owner/repo; a one-segment path names none."""
    assert pi.normalize_remote_url(url) is None
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_DECLINE


def test_control_a_non_bare_repository_git_dir_on_another_host_keeps_its_id():
    assert pi.normalize_remote_url("git@host:srv/repo/.git") == "host/srv/repo"


@pytest.mark.parametrize("url, expected", [
    ("git@gh_work:o/r.git", "gh_work/o/r"),
    ("ssh://git@gh_work/o/r", "gh_work/o/r"),
    ("gh_work:o/r", "gh_work/o/r"),
])
def test_an_ssh_alias_with_an_underscore_is_a_host(url, expected):
    """ssh resolves an ssh_config alias such as `gh_work`, as it resolves a
    hyphenated one, so an ssh or scp remote naming it has an id."""
    assert pi.normalize_remote_url(url) == expected
    assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_CANONICAL
    assert pi.is_canonical(expected)


@pytest.mark.parametrize("url", ["https://gh_work/o/r", "git@_gh:o/r", "git@gh_:o/r"])
def test_an_underscore_host_declines_outside_ssh_or_at_an_end(url):
    assert pi.normalize_remote_url(url) is None


def test_control_another_port_on_that_host_keeps_its_own_id():
    assert pi.normalize_remote_url("ssh://git@ssh.github.com:2222/Acme/Widget") == "ssh.github.com:2222/Acme/Widget"


HELPERS = [
    "codecommit::us-east-1://my-repo",
    "codecommit://my-repo",
    "persistent-https://source.example/o/r",
    "sso://source.example/o/r",
    "keybase://private/team/repo",
]


@pytest.mark.parametrize("url", HELPERS)
def test_a_remote_helper_transport_has_no_portable_origin(url):
    """A remote-helper transport has no portable id: it takes the folder
    name, as a repository with no remote does, rather than declining."""
    assert pi.normalize_remote_url(url) is None
    assert pi.classify_remote_origin(url) == (pi.OUTCOME_NO_PORTABLE_ORIGIN, None)


@pytest.mark.parametrize("url, outcome", [
    ("ftp://host.example/o/r", "decline"),
    ("rsync://host.example/o/r", "decline"),
    ("git@[::1]:o/r", "canonical"),
    ("ssh://git@[2001:db8::1]/o/r", "canonical"),
    ("https://[::1]/o/r", "canonical"),
    ("codecommit::\x01x", "decline"),
])
def test_control_other_schemes_and_ipv6_classify_as_before(url, outcome):
    assert pi.classify_remote_origin(url)[0] == outcome


def test_a_remote_helper_origin_binds_and_discloses_its_folder_name(tmp_path, monkeypatch, caplog):
    """The repository binds its folder name, `project-id show` discloses the
    source as a remote helper, and one warning names the remedy without the
    url (a helper address can carry an account id or a token)."""
    import logging

    from fixtures.identity_git import assert_outside_repository, make_repo
    from scripts import memory_api
    from scripts.memory_api import PACTMemory

    assert_outside_repository(tmp_path)
    secret = "codecommit::us-east-1://SECRETTOKEN@my-repo"
    repo = make_repo(tmp_path / "work" / "proj", remote=secret)
    identity = pi.detect_id_for_root(repo, repo / ".git", asked_root=repo)
    assert (identity.project_id, identity.id_shape, identity.detail) == ("proj", "basename", pi.REMOTE_HELPER_DETAIL)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(memory_api, "_REMOTE_HELPER_WARNED", set())
    with caplog.at_level(logging.WARNING):
        memory = PACTMemory(session_id="s", db_path=tmp_path / "m.db")
        PACTMemory(session_id="s", db_path=tmp_path / "m.db")
    assert (memory.project_id, memory._project_id_source) == ("proj", "remote helper")
    warned = [r.getMessage() for r in caplog.records if "remote-helper transport" in r.getMessage()]
    assert len(warned) == 1, warned
    assert "pact-project-id" in warned[0] and "SECRETTOKEN" not in caplog.text


def _present_store(path):
    from scripts.database import db_connection, ensure_initialized

    with db_connection(path) as conn:
        ensure_initialized(conn)


def test_project_id_show_discloses_a_remote_helper_origin(tmp_path, monkeypatch, capfd):
    """`project-id show` names the remote-helper source and says the bucket is
    shared by folder name and that a pin isolates it; never the url."""
    import json

    from fixtures.identity_git import assert_outside_repository, make_repo
    from scripts.cli import main

    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "work" / "proj", remote="codecommit::us-east-1://SECRETTOKEN@my-repo")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(repo)
    _present_store(tmp_path / "m.db")
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(tmp_path / "m.db")])
    captured = capfd.readouterr()
    assert exit_info.value.code == 0, captured.err
    shown = json.loads(captured.out)["result"]
    assert (shown["project_id"], shown["id_shape"], shown["source"]) == ("proj", "basename", "remote helper")
    assert shown["identity_detail"] == pi.REMOTE_HELPER_DETAIL
    assert "shared" in shown["identity_detail"] and "pact-project-id" in shown["identity_detail"]
    assert "SECRETTOKEN" not in captured.out + captured.err


def test_control_show_has_no_identity_detail_for_a_canonical_origin(tmp_path, monkeypatch, capfd):
    import json

    from fixtures.identity_git import assert_outside_repository, make_repo
    from scripts.cli import main

    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "work" / "proj", remote="https://github.com/acme/proj.git")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(repo)
    _present_store(tmp_path / "m.db")
    with pytest.raises(SystemExit):
        main(["project-id", "show", "--db-path", str(tmp_path / "m.db")])
    assert json.loads(capfd.readouterr().out)["result"]["identity_detail"] is None
