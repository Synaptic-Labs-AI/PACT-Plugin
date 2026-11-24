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
    def test_github_drops_one_git_as_other_hosts_do(self, url):
        github = pi.normalize_remote_url(url)
        gitlab = pi.normalize_remote_url(url.replace("github.com", "gitlab.com"))
        assert github == "github.com/o/foo.git"
        assert gitlab == "gitlab.com/o/foo.git"

    @pytest.mark.parametrize("url", ["https://github.com/o/Foo.GIT", "https://github.com/o/foo.git"])
    def test_control_a_single_suffix_of_any_case_is_dropped_on_github(self, url):
        assert pi.normalize_remote_url(url) == "github.com/o/foo"


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
