"""Repository-identity canonicaliser and origin reader (P0 matrix A0-A13).

Each arm names the defect it would catch. The classifier is the load-bearing
surface: it must keep THREE outcomes distinct (canonical / no_portable_origin /
decline). Collapsing decline into basename reintroduces the silent-mis-scope
defect; collapsing no_portable_origin into decline breaks non-git folders.

Corpus rows U45 (`git@host:repo` is CANONICAL) and U58 (an unsupported
network scheme is DECLINE) are superseded by ruling and asserted here as
ruled, not as the older corpus said.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    common_dir,
    make_repo,
    make_worktree,
    write_common_dir_override,
)
from scripts.database import is_canonical_id

from scripts import project_identity as pi

CANONICAL = pi.OUTCOME_CANONICAL
NO_PORTABLE = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE


# ---------------------------------------------------------------------------
# A8-A13: normalization equivalence classes. Only ONE literal is pinned.
# ---------------------------------------------------------------------------

class TestNormalizationEquivalence:
    """Equivalence is asserted BETWEEN outputs, so a format change is one edit."""

    def test_one_endpoint_spelled_seven_ways_has_one_id(self):
        """Catches a parser that only handles one transport spelling.

        If SSH and HTTPS of the same endpoint disagree, one repository splits
        into two buckets across machines — the reported incident.
        """
        urls = [
            "git@github.com:owner/repo.git",
            "https://github.com/owner/repo.git",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo/",
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@github.com:22/owner/repo.git",
            "https://user@github.com/owner/repo.git",
        ]
        outputs = {pi.normalize_remote_url(url) for url in urls}
        assert len(outputs) == 1, f"spellings diverged: {outputs}"

    def test_the_literal_format_is_pinned_in_exactly_one_place(self):
        """The user-visible shape, pinned once so a format change is one edit.

        Every other arm compares outputs; this one is the single literal.
        """
        assert (
            pi.normalize_remote_url("git@github.com:Org/Repo.git")
            == "github.com/Org/Repo"
        )

    def test_host_case_is_folded_and_path_case_is_preserved(self):
        """Catches a whole-string lower() and a missing host lower().

        Hosts are case-insensitive; paths are not (a case-sensitive host can
        hold two distinct repositories differing only by case).
        """
        assert pi.normalize_remote_url("https://GitHub.COM/owner/repo") == (
            "github.com/owner/repo"
        )
        preserved = pi.normalize_remote_url("https://github.com/Owner/Repo")
        assert preserved == "github.com/Owner/Repo"
        assert preserved != pi.normalize_remote_url("https://github.com/owner/repo")

    def test_one_git_suffix_is_stripped(self):
        """Catches a loop that strips `.git` repeatedly."""
        assert (
            pi.normalize_remote_url("https://host/owner/repo.git.git")
            == "host/owner/repo.git"
        )

    def test_default_ports_are_dropped_and_non_default_ports_preserved(self):
        """Catches a parser that drops every port (merging two servers) or none.

        A dropped non-default port silently merges two self-hosted servers on
        one host; a kept default port splits two spellings of one endpoint.
        """
        assert pi.normalize_remote_url("https://host:443/owner/repo") == (
            "host/owner/repo"
        )
        assert pi.normalize_remote_url("ssh://git@host:22/owner/repo") == (
            "host/owner/repo"
        )
        assert pi.normalize_remote_url("ssh://git@host:2222/owner/repo") == (
            "host:2222/owner/repo"
        )

    def test_userinfo_is_stripped_from_the_id(self):
        """Catches a credential copied into the stored id (and every log line).

        CI routinely uses `x-access-token:<token>@`; the token must not reach
        the id.
        """
        identifier = pi.normalize_remote_url(
            "https://x-access-token:SECRET-TOKEN@github.com/owner/repo.git"
        )
        assert identifier == "github.com/owner/repo"
        assert "SECRET-TOKEN" not in identifier

    def test_the_scp_one_segment_form_is_canonical_not_a_decline(self):
        """U45, superseded to CANONICAL. Catches the old corpus ruling.

        `git@host:repo` and `https://host/repo` name one endpoint; declining
        one spelling splits the repository.
        """
        assert pi.classify_remote_origin("git@host:repo") == (CANONICAL, "host/repo")
        assert pi.classify_remote_origin("https://host/repo") == (
            CANONICAL,
            "host/repo",
        )

    def test_a_trailing_newline_is_the_only_stripped_terminator(self):
        """Catches a broad .strip() that repairs a remote git should reject.

        git writes one newline; a trailing space must DECLINE visibly rather
        than be silently accepted.
        """
        assert pi.normalize_remote_url("https://host/owner/repo\n") == (
            "host/owner/repo"
        )
        assert pi.classify_remote_origin("https://host/owner/repo ") == (DECLINE, None)

    @pytest.mark.parametrize(
        "value",
        [None, 123, b"https://host/owner/repo", ["https://host/o/r"]],
    )
    def test_normalize_is_total_for_non_strings(self, value):
        """Catches a parser that raises on a session-record value of any type."""
        assert pi.normalize_remote_url(value) is None


# ---------------------------------------------------------------------------
# classifier: three outcomes, never two
# ---------------------------------------------------------------------------

class TestClassifierOutcomes:
    def test_empty_and_whitespace_are_no_portable_origin(self):
        """Catches an empty origin being treated as a decline (which would
        break every repository without a remote)."""
        assert pi.classify_remote_origin(None) == (NO_PORTABLE, None)
        assert pi.classify_remote_origin("") == (NO_PORTABLE, None)
        assert pi.classify_remote_origin("   \n") == (NO_PORTABLE, None)

    @pytest.mark.parametrize(
        "url",
        [
            "/abs/path/to/repo",
            "../relative/repo",
            "C:\\work\\repo",
            "file:///srv/git/repo.git",
        ],
    )
    def test_local_forms_are_no_portable_origin(self, url):
        """Catches a local path being declined instead of falling back.

        `file://` and bare paths carry no network remote, so the designed
        basename fallback applies.
        """
        assert pi.classify_remote_origin(url) == (NO_PORTABLE, None)

    def test_an_unsupported_network_scheme_declines(self):
        """U58, superseded to DECLINE. Catches a basename fallback for a real
        remote we cannot represent — the defect this ticket removes."""
        assert pi.classify_remote_origin("ftp://host/owner/repo") == (DECLINE, None)

    @pytest.mark.parametrize(
        "url,rule",
        [
            ("https://host", "empty path"),
            ("https://host/", "empty path"),
            ("https://host/owner/../repo", "dot-dot segment"),
            ("https://host/owner/./repo", "dot segment"),
            ("https://host/owner//repo", "empty segment"),
            ("https://host/owner/re%2Fpo", "encoded separator"),
            ("https://host/owner/repo#frag", "fragment character"),
            ("https://host/owner/repo?x=1", "query character"),
            ("https://host/owner/répo", "non-ASCII path"),
            ("https://host:0/owner/repo", "port zero"),
            ("https://host:65536/owner/repo", "port above range"),
            ("https://host:022/owner/repo", "leading-zero port"),
            ("https://evil.com\\@github.com/o/n", "backslash ambiguity"),
            ("https://a@b@host/owner/repo", "more than one @"),
            ("\ufeffhttps://host/owner/repo", "BOM"),
            ("https://-host/owner/repo", "host leading hyphen"),
            ("https://host_/owner/repo", "underscore in host"),
            ("https://host./owner/repo", "trailing dot in host"),
        ],
    )
    def test_a_present_network_remote_that_cannot_be_represented_declines(
        self, url, rule
    ):
        """Catches each refusal collapsing into a basename. Each row names the
        rule; the override is the operator's remedy."""
        assert pi.classify_remote_origin(url) == (DECLINE, None), rule

    def test_an_over_length_path_declines(self):
        """Catches a silently truncated id (two projects could collide)."""
        url = "https://host/owner/" + "a" * 250
        assert pi.classify_remote_origin(url) == (DECLINE, None)


# ---------------------------------------------------------------------------
# R1: the canonical-id grammar (acceptance/rejection corpus)
# ---------------------------------------------------------------------------

class TestCanonicalGrammar:
    @pytest.mark.parametrize(
        "value",
        [
            "host/repo",
            "github.com/owner/repo",
            "github.com/Owner/Repo",
            "host:2222/owner/repo",
            "gitlab.example.com/group/subgroup/repo",
        ],
    )
    def test_accepts_canonical_shapes(self, value):
        assert is_canonical_id(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "PACT-prompt",
            "host",
            "host/",
            "/owner/repo",
            "host/owner/../repo",
            "host/owner/./repo",
            "host//repo",
            "host/owner/repo\n",
            "host/owner/repo ",
            "host:0/owner/repo",
            "host:65536/owner/repo",
            "host:022/owner/repo",
            "host/owner/répo",
            None,
            7,
        ],
    )
    def test_rejects_non_canonical_shapes(self, value):
        """Catches `$`-anchored matching (which accepts a trailing newline)
        and a basename leaking into the canonical namespace."""
        assert is_canonical_id(value) is False

    def test_a_max_length_id_is_accepted_and_one_character_more_is_not(self):
        """The boundary, not just 'long values fail'."""
        base = "h/"
        value = base + "a" * (pi._MAX_ID_LENGTH - len(base))
        assert len(value) == pi._MAX_ID_LENGTH
        assert is_canonical_id(value) is True
        assert is_canonical_id(value + "a") is False


# ---------------------------------------------------------------------------
# OriginRead tri-state
# ---------------------------------------------------------------------------

class TestReadOrigin:
    def test_absent_origin_is_absent_not_unavailable(self, tmp_path):
        """Catches the collapse that makes a no-remote repo decline."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        origin = pi.read_origin(repo)
        assert origin.state == pi.ORIGIN_ABSENT
        assert origin.value is None

    def test_a_configured_origin_is_returned_raw(self, tmp_path):
        """Catches normalization inside the reader (the canonicaliser must see
        the raw value, including the trailing newline it is required to
        tolerate)."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="https://github.com/o/r.git")
        origin = pi.read_origin(repo)
        assert origin.state == pi.ORIGIN_PRESENT
        assert origin.value is not None
        assert origin.value.strip() == "https://github.com/o/r.git"

    def test_missing_git_is_unavailable(self, tmp_path, monkeypatch):
        """Catches an absent-reader fallback when git cannot run at all."""
        repo = make_repo(tmp_path / "repo")
        monkeypatch.setattr(
            pi.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
        )
        assert pi.read_origin(repo).state == pi.ORIGIN_UNAVAILABLE

    def test_a_git_error_is_unavailable(self, tmp_path, monkeypatch):
        """A non-1 exit is 'cannot answer', never 'no origin'."""
        repo = make_repo(tmp_path / "repo")
        monkeypatch.setattr(
            pi.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=128, stdout=b""),
        )
        assert pi.read_origin(repo).state == pi.ORIGIN_UNAVAILABLE

    def test_exit_one_with_no_value_is_absent(self, tmp_path, monkeypatch):
        """`git config --get` exits 1 when the key is unset; that is ABSENT."""
        repo = make_repo(tmp_path / "repo")
        monkeypatch.setattr(
            pi.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""),
        )
        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT

    def test_a_non_utf8_origin_does_not_crash_and_declines(self, tmp_path, monkeypatch):
        """Catches locale decoding escaping the reader (UnicodeDecodeError)."""
        repo = make_repo(tmp_path / "repo")
        monkeypatch.setattr(
            pi.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=0, stdout=b"https://host/owner/re\xffpo\n"
            ),
        )
        origin = pi.read_origin(repo)
        assert origin.state == pi.ORIGIN_PRESENT
        assert pi.classify_remote_origin(origin.value) == (DECLINE, None)


# ---------------------------------------------------------------------------
# detect_id_for_root: the five root outcomes
# ---------------------------------------------------------------------------

class TestDetectIdForRoot:
    def test_none_root_declines(self):
        assert pi.detect_id_for_root(None) == (None, None, "declined", None)

    def test_no_common_dir_is_the_designed_basename_path(self, tmp_path):
        """Catches treating a non-git umbrella as a broken repository."""
        root = tmp_path / "umbrella"
        root.mkdir()
        identity = pi.detect_id_for_root(root, None)
        assert identity.project_id == "umbrella"
        assert identity.id_shape == "basename"
        assert identity.source == "basename"

    def test_a_repo_without_a_remote_uses_the_basename(self, tmp_path):
        repo = make_repo(tmp_path / "plain-repo")
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id == "plain-repo"
        assert identity.id_shape == "basename"

    def test_a_canonical_origin_is_the_id(self, tmp_path):
        repo = make_repo(tmp_path / "checkout", remote="https://github.com/o/r.git")
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id == "github.com/o/r"
        assert identity.id_shape == "canonical"
        assert identity.source == "origin remote"

    def test_a_local_origin_falls_back_to_basename(self, tmp_path):
        repo = make_repo(tmp_path / "local-repo", remote="file:///srv/git/r.git")
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id == "local-repo"
        assert identity.id_shape == "basename"

    def test_an_unrepresentable_origin_declines(self, tmp_path):
        repo = make_repo(tmp_path / "bad-remote", remote="ftp://host/o/r.git")
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.source == "declined"

    def test_an_unavailable_origin_declines(self, tmp_path, monkeypatch):
        """#1699: a broken repository must not fall back to a folder name."""
        repo = make_repo(tmp_path / "broken")
        monkeypatch.setattr(
            pi,
            "read_origin",
            lambda root: pi.OriginRead(pi.ORIGIN_UNAVAILABLE, None),
        )
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.source == "declined"

    def test_the_override_is_not_read_here(self, tmp_path):
        """Rung 0 owns the override; reading it twice would let two reads
        disagree. Catches a second override read inside root detection."""
        repo = make_repo(tmp_path / "checkout", remote="https://github.com/o/r.git")
        write_common_dir_override(repo, "override.example.com/pinned/id")
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id == "github.com/o/r"

    def test_a_worktree_resolves_the_main_repo_identity(self, tmp_path):
        """Catches a worktree splitting from its main checkout."""
        main = make_repo(tmp_path / "main", remote="https://github.com/o/r.git")
        worktree = make_worktree(main, tmp_path / "wt")
        identity = pi.detect_id_for_root(worktree, common_dir(worktree))
        assert identity.project_id == "github.com/o/r"


class TestUserinfoNeverAppearsInLogs:
    def test_classification_logs_nothing(self, caplog):
        """Catches a debug line that prints the raw remote (a CI token)."""
        with caplog.at_level(logging.DEBUG):
            pi.classify_remote_origin(
                "https://x-access-token:SECRET-TOKEN@github.com/o/r.git"
            )
        assert "SECRET-TOKEN" not in caplog.text