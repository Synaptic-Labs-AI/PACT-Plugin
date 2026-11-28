"""
Location: pact-plugin/tests/test_identity_text_and_pin.py
Summary: Three small identity rules: the unproven warning for a bucket with a
foreign row reads as one sentence (one "so"), a github.com remote drops one
`.git` as every other host does, and the local pin's host is one no remote
can produce.
Used by: the test suite; no production code imports it.
"""

import logging

import pytest

from scripts import memory_api
from scripts import project_identity as pi


class TestTheForeignRowWarning:
    @pytest.mark.parametrize("contained", [0, 2])
    def test_the_rendered_warning_has_one_so(self, caplog, contained):
        cause = memory_api._unproven_cause(
            "foreign_rows", {"foreign_rows": 1, "contained_rows": contained}
        )
        key = ("/s.db", "widget")
        memory_api._UNPROVEN_WARNED.discard(key)
        with caplog.at_level(logging.WARNING, logger=memory_api.logger.name):
            memory_api._warn_legacy_unproven("/s.db", "widget", "github.com/o/widget", 1, 3, cause)
        memory_api._UNPROVEN_WARNED.discard(key)
        text = " ".join(r.getMessage() for r in caplog.records if r.name == memory_api.logger.name)
        assert "1 of its file rows lie in another checkout" in text, text
        assert text.count(", so ") == 1, text


class TestOneGitSuffixPerRemote:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/o/foo.git.git",
            "git@github.com:o/foo.git.git",
        ],
    )
    def test_github_declines_a_name_still_ending_in_git(self, url):
        """GitHub does not serve `repo.git.git` as `repo` (measured with
        `git ls-remote <url> HEAD`, git 2.43: https://github.com/git/git
        answers its HEAD, https://github.com/git/git.git.git answers "could
        not read Username", GitHub's reply for a repository it does not
        serve), and no GitHub repository name ends in `.git`, so it reaches
        nothing and declines. Other hosts drop one `.git`. RED WHEN a
        github.com name ending in `.git` binds an id."""
        github = pi.normalize_remote_url(url)
        gitlab = pi.normalize_remote_url(url.replace("github.com", "gitlab.com"))
        assert github is None
        assert gitlab == "gitlab.com/o/foo.git"

    def test_control_a_literal_suffix_is_dropped_on_github(self):
        assert pi.normalize_remote_url("https://github.com/o/foo.git") == "github.com/o/foo"

    def test_a_capitalised_suffix_declines_on_github(self):
        """GitHub does not serve `repo.GIT` as `repo` (measured with `git
        ls-remote <url> HEAD`, git 2.43: https://github.com/git/git.GIT
        answers "could not read Username"), and no repository there is named
        `repo.git`, so it declines. RED WHEN `.GIT` binds an id."""
        assert pi.normalize_remote_url("https://github.com/o/Foo.GIT") is None


class TestNoRemoteDerivesAnInvalidHost:
    @pytest.mark.parametrize(
        "url",
        [
            "git@local.invalid:0123456789ab/x",
            "https://local.invalid/0123456789ab/x",
            "https://LOCAL.Invalid:8443/0123456789ab/x",
            "ssh://git@invalid/o/r",
        ],
    )
    def test_a_remote_in_the_invalid_namespace_declines(self, url):
        assert pi.normalize_remote_url(url) is None
        assert pi.classify_remote_origin(url)[0] == pi.OUTCOME_DECLINE

    @pytest.mark.parametrize("url", ["https://invalidx.com/o/r", "https://h.invalidate/o/r"])
    def test_control_a_host_merely_spelled_like_it_is_kept(self, url):
        assert pi.normalize_remote_url(url) is not None

    def test_control_a_local_pin_is_still_canonical(self):
        # A user-written pin in the reserved namespace stays a valid id.
        assert pi.is_canonical("local.invalid/0123456789ab/x")


@pytest.mark.parametrize("owner, ok", [
    ("acme", True), ("a-b", True), ("Acme-Widget9", True), ("a" * 39, True),
    ("o-", True), ("a--b", True),
    ("octocat_acme", True), ("a_b", True),
    ("-o", False), ("_o", False), ("a.b", False), ("a~b", False), ("a" * 40, False),
])
def test_a_github_owner_follows_githubs_owner_rule(owner, ok):
    """GitHub owner names hold only letters, digits, hyphens and
    underscores, starting with a letter or digit, at most 39 characters
    (GitHub docs, "Username considerations for external authentication"); a
    trailing or doubled hyphen (older accounts) and an underscore (an
    Enterprise Managed User's _shortcode suffix) are accepted. A remote naming another owner declines, a pin holding
    it is refused and the backlog reads no slug, so the three agree. RED
    WHEN `github.com/-o/r` or a 40-character owner gets an id."""
    from shared import backlog
    from scripts.database import pin_file_value

    url = f"https://github.com/{owner}/r"
    expected = f"github.com/{owner.lower()}/r" if ok else None
    assert pi.normalize_remote_url(url) == expected
    assert (backlog._slug_from_remote_url(url) is not None) is ok
    assert pin_file_value(f"github.com/{owner}/r\n".encode())[0] == expected
    assert pi.normalize_remote_url(f"https://gitlab.com/{owner}/r") is not None
