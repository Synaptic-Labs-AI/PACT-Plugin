"""Repository-identity canonicaliser and origin reader.

Each arm names the defect it would catch. The classifier is the load-bearing
surface: it must keep THREE outcomes distinct (canonical / no_portable_origin /
decline). Collapsing decline into basename reintroduces the silent-mis-scope
defect; collapsing no_portable_origin into decline breaks non-git folders.

`git@host:repo` is CANONICAL and an unsupported network scheme DECLINES;
both are asserted here as ruled.
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
from scripts.database import CANONICAL_ID_MAX_LENGTH, is_canonical_id

from scripts import project_identity as pi

CANONICAL = pi.OUTCOME_CANONICAL
NO_PORTABLE = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE


# ---------------------------------------------------------------------------
# Normalization equivalence classes. Only ONE literal is pinned.
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
        # A set-of-one arm also passes when EVERY spelling returns None,
        # which is the shape of a canonicaliser that stopped working. Assert
        # the value is real before asserting the spellings agree.
        assert None not in outputs, (
            "at least one spelling failed to normalize, so the equality below "
            "would be measuring a dead canonicaliser"
        )
        assert len(outputs) == 1, f"spellings diverged: {outputs}"

    def test_one_home_relative_repository_spelled_five_ways_has_one_id(self):
        """Catches `~/` (the login's home) splitting from the scp-relative
        spelling git resolves in that same home."""
        urls = [
            "git@host:srv/repo.git",
            "git@host:~/srv/repo.git",
            "ssh://git@host/~/srv/repo.git",
            "git+ssh://git@host/~/srv/repo",
            "ssh+git://git@host/%7E/srv/repo",
        ]
        outputs = {pi.normalize_remote_url(url) for url in urls}
        assert outputs == {"host/srv/repo"}, outputs

    def test_another_users_home_is_never_stripped(self):
        """`~user/...` names another user's home, and is SourceHut's real
        repository path, so it keeps its `~user` segment in every spelling."""
        sourcehut = {
            pi.normalize_remote_url("git@git.sr.ht:~user/repo"),
            pi.normalize_remote_url("ssh://git@git.sr.ht/~user/repo"),
            pi.normalize_remote_url("https://git.sr.ht/~user/repo"),
        }
        assert sourcehut == {"git.sr.ht/~user/repo"}, sourcehut
        assert pi.normalize_remote_url("git@host:~user/repo") != (
            pi.normalize_remote_url("git@host:~/repo")
        )

    def test_a_tilde_with_no_home_expansion_is_kept(self):
        """Only the ssh family and scp go through a remote shell; https and
        git:// keep `~` as an ordinary segment, and the home itself stays."""
        assert pi.normalize_remote_url("https://host/~/x") == "host/~/x"
        assert pi.normalize_remote_url("git://host/~/x") == "host/~/x"
        assert pi.normalize_remote_url("git@host:~") == "host/~"
        assert pi.normalize_remote_url("ssh://git@host/~") == "host/~"

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
        """Catches the one-segment scp form being declined.

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
            "file:///srv/git/repo.git",
        ],
    )
    def test_local_forms_are_no_portable_origin(self, url):
        """Catches a local path being declined instead of falling back.

        `file://` and bare paths carry no network remote, so the designed
        basename fallback applies.
        """
        assert pi.classify_remote_origin(url) == (NO_PORTABLE, None)

    def test_a_windows_drive_path_is_no_portable_origin_on_windows(self, monkeypatch):
        """git reads `C:\\work\\repo` as a drive path only on Windows."""
        monkeypatch.setattr(pi.os, "name", "nt")
        assert pi.classify_remote_origin("C:\\work\\repo") == (NO_PORTABLE, None)

    def test_a_drive_shaped_value_with_a_backslash_declines_on_posix(self, monkeypatch):
        """Elsewhere git connects to host `C`, so the value is a network
        remote, and its backslash makes it unrepresentable."""
        monkeypatch.setattr(pi.os, "name", "posix")
        assert pi.classify_remote_origin("C:\\work\\repo") == (DECLINE, None)

    def test_a_user_less_scp_form_canonicalises(self):
        """git reads `host:path` as scp when no `/` precedes the first `:`,
        with or without a user, so both spellings are one repository."""
        assert pi.classify_remote_origin("github.com:owner/repo") == (
            CANONICAL,
            "github.com/owner/repo",
        )
        assert pi.classify_remote_origin("github.com:owner/repo") == (
            pi.classify_remote_origin("git@github.com:owner/repo")
        )

    def test_an_unsupported_network_scheme_declines(self):
        """Catches a basename fallback for a real remote we cannot
        represent."""
        assert pi.classify_remote_origin("ftp://host/owner/repo") == (DECLINE, None)

    @pytest.mark.parametrize(
        "url,rule",
        [
            ("https://host", "empty path"),
            ("https://host/", "empty path"),
            ("https://host/owner/../repo", "dot-dot segment"),
            ("https://host/owner/./repo", "dot segment"),
            ("https://host/owner//repo", "empty segment"),
            ("https://host/owner/repo#frag", "fragment character"),
            ("https://host/owner/repo?x=1", "query character"),
            ("https://host:0/owner/repo", "port zero"),
            ("https://host:65536/owner/repo", "port above range"),
            ("https://host:022/owner/repo", "leading-zero port"),
            ("https://host:abc/owner/repo", "non-numeric port"),
            ("https://host:12a/owner/repo", "digit-prefixed port"),
            ("https://host:-1/owner/repo", "negative port"),
            ("https://host/owner/re po", "interior space in a path segment"),
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

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://host/owner/re%2Fpo", "host/owner/re%2Fpo"),
            ("https://host/owner/répo", "host/owner/r%C3%A9po"),
        ],
    )
    def test_an_encoded_separator_and_a_non_ascii_path_keep_their_encoding(
        self, url, expected
    ):
        """An encoded `/` stays inside its segment, and a non-ASCII character
        becomes its UTF-8 `%XX` bytes, so the id stays printable ASCII."""
        assert pi.classify_remote_origin(url) == (CANONICAL, expected)

    def test_an_over_length_path_declines(self):
        """Catches a silently truncated id (two projects could collide)."""
        url = "https://host/owner/" + "a" * 250
        assert pi.classify_remote_origin(url) == (DECLINE, None)


# ---------------------------------------------------------------------------
# The canonical-id grammar (acceptance/rejection corpus)
# ---------------------------------------------------------------------------

class TestCanonicalGrammar:
    @pytest.mark.parametrize(
        "value",
        [
            "host/repo",
            "github.com/owner/repo",
            "github.com/Owner/Repo",
            "host:2222/owner/repo",
            "host:1/owner/repo",
            "host:65535/owner/repo",
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
        value = base + "a" * (CANONICAL_ID_MAX_LENGTH - len(base))
        assert len(value) == CANONICAL_ID_MAX_LENGTH
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
        the raw value, including any whitespace it is required to reject)."""
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
        """`git config --get-all` exits 1 when the key is unset; that is ABSENT."""
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
                returncode=0, stdout=b"https://host/owner/re\xffpo\x00"
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
        # SIX fields: `decline_reason` and `detail` are defaulted trailing
        # fields, so a caller can disclose WHY a root declined. A bare None
        # root has no named cause, so both stay None.
        assert pi.detect_id_for_root(None) == (
            None, None, "declined", None, None, None
        )

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
        """A broken repository must not fall back to a folder name."""
        repo = make_repo(tmp_path / "broken")
        monkeypatch.setattr(
            pi,
            "read_origin",
            lambda root: pi.OriginRead(pi.ORIGIN_UNAVAILABLE, None),
        )
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.source == "declined"

    def test_the_override_follows_the_identity_root(self, tmp_path, monkeypatch):
        """The override is read at the ROOT's common dir, not the
        process cwd's.

        The review corrected the original "rung 0 owns it" split: reading
        only the process repository silently ignored an operator pin in a
        DECLARED repository, and let a cwd pin retarget a declared session.
        This arm pins both halves: the root's own override wins, and a
        DIFFERENT repo's override (the cwd's) is not consulted.
        """
        root = make_repo(tmp_path / "checkout", remote="https://github.com/o/r.git")
        other = make_repo(tmp_path / "other", remote="https://github.com/o/other.git")
        write_common_dir_override(root, "override.example.com/pinned/id")
        write_common_dir_override(other, "override.example.com/cwd/id")
        monkeypatch.chdir(other)

        identity = pi.detect_id_for_root(root, common_dir(root))
        assert identity.project_id == "override.example.com/pinned/id"
        assert identity.source == "override file"

        # The cwd repo's pin does not reach a different root.
        plain = make_repo(tmp_path / "plain", remote="https://github.com/o/plain.git")
        identity = pi.detect_id_for_root(plain, common_dir(plain))
        assert identity.project_id == "github.com/o/plain"

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


# ---------------------------------------------------------------------------
# The accepted-scheme and default-port TABLES, entry by entry
# ---------------------------------------------------------------------------
#
# The equivalence classes above pin ONE spelling per behaviour, so a table
# entry can be deleted while every arm stays green: the surviving spellings
# still answer. These arms pin each entry the production tables claim to
# support, because narrowing either table is a silent behaviour change — an
# accepted transport starts DECLINING, or one endpoint splits into two ids.

class TestEveryAcceptedSchemeIsPinned:
    @pytest.mark.parametrize(
        "url",
        [
            "http://host/owner/repo",
            "git://host/owner/repo",
            "git+ssh://git@host/owner/repo",
        ],
    )
    def test_each_accepted_scheme_canonicalises(self, url):
        """RED WHEN its entry is removed from `_ACCEPTED_SCHEMES`.

        A dropped scheme turns a present network remote into a DECLINE, so a
        repository cloned over that transport silently stops resolving.
        """
        assert pi.normalize_remote_url(url) == "host/owner/repo"


class TestEveryDefaultPortIsPinned:
    @pytest.mark.parametrize(
        "url",
        [
            "http://host:80/owner/repo",
            "git://host:9418/owner/repo",
            "git+ssh://git@host:22/owner/repo",
        ],
    )
    def test_each_default_port_is_dropped(self, url):
        """RED WHEN its entry is removed from `_DEFAULT_PORTS`.

        A kept default port splits `host:80/owner/repo` from
        `host/owner/repo` — two ids for one endpoint, the split this change
        exists to remove.
        """
        assert pi.normalize_remote_url(url) == "host/owner/repo"