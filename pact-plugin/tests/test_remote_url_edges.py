"""
Location: pact-plugin/tests/test_remote_url_edges.py
Summary: Remote url spellings that must give the same id as their plain form:
a percent-encoded `.git` suffix and an Azure DevOps collection name in any
case; and a config text whose `[include]` line continues a value, which git
reads as part of that value.
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
