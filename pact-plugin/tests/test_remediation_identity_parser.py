"""
Location: pact-plugin/tests/test_remediation_identity_parser.py
Summary: Arms for the repository-identity parser (`project_identity.py`): which
         origin value is read and from which config, how a remote string is
         classified, how submodule and redirect shapes are recognised, and
         what the override accepts. Each arm builds real git repositories with
         fake origin URLs; no network is contacted.
Used by: pytest. Shares the real-git helpers in `fixtures/identity_git.py`.
"""

from __future__ import annotations

import ast
import itertools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    common_dir,
    make_repo,
    reason_dict_sites,
    run_git,
    seed_session_evidence,
    unread_reason_keys,
    write_common_dir_override,
)

from scripts import project_identity as pi

CANONICAL = pi.OUTCOME_CANONICAL
NO_PORTABLE = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE



def _git_directory(path):
    """A minimal folder git accepts as a git directory (HEAD, objects/,
    refs/): `find_git_marker` walks past a `.git` folder git would not."""
    (path / "objects").mkdir(parents=True)
    (path / "refs").mkdir()
    (path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

def _origin_id(repo: Path):
    """The (outcome, id) the classifier gives for what `read_origin` returns."""
    origin = pi.read_origin(repo / ".git")
    assert origin.state == pi.ORIGIN_PRESENT, origin
    return pi.classify_remote_origin(origin.value)


def _add_url(repo: Path, url: str) -> None:
    run_git("config", "--add", "remote.origin.url", url, cwd=repo)


class TestTheOriginIsTheUrlGitFetches:
    """The origin is the first `remote.origin.url` in the repository's own
    config. git fetches from the first of several urls; the last one is a
    push-only url. A first url that does not come from the repository's own
    config file declines."""

    def test_the_first_of_two_urls_wins(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@first.example:o/first.git")
        _add_url(repo, "git@second.example:o/second.git")

        assert _origin_id(repo) == (CANONICAL, "first.example/o/first")

    def test_a_url_set_only_through_an_include_declines(self, tmp_path):
        """An included file's url can differ between a repository's
        worktrees and branches, so it is no identity: it declines, with the
        override as the remedy."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        included = tmp_path / "included.cfg"
        included.write_text(
            '[remote "origin"]\n\turl = git@included.example:o/inc.git\n',
            encoding="utf-8",
        )
        run_git("config", "include.path", str(included), cwd=repo)

        assert pi.read_origin(repo / ".git") == pi.OriginRead(pi.ORIGIN_NOT_OWN_FILE, None)
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.decline_reason == pi.decline_record(
            "origin_unrepresentable", str(common_dir(repo) / "pact-project-id")
        )
        assert identity.detail == pi.NOT_OWN_FILE_ORIGIN_DETAIL

    def test_a_url_in_the_own_config_before_an_include_is_present(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@own.example:o/own.git")
        included = tmp_path / "included.cfg"
        included.write_text(
            '[remote "origin"]\n\turl = git@included.example:o/inc.git\n',
            encoding="utf-8",
        )
        run_git("config", "include.path", str(included), cwd=repo)

        assert _origin_id(repo) == (CANONICAL, "own.example/o/own")

    def test_the_own_config_is_matched_by_its_real_path(self, tmp_path, monkeypatch):
        """git names the file it read by the spelling of the git dir it was
        given; a spelling of the same file through a symlink is still the
        repository's own config."""
        import subprocess

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        link = tmp_path / "link"
        link.symlink_to(repo / ".git")
        answer = f"file:{repo / '.git' / 'config'}".encode() + b"\0git@own.example:o/own.git\0"

        def git_answers(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, answer, b"")

        monkeypatch.setattr(pi.subprocess, "run", git_answers)
        assert pi.read_origin(link) == pi.OriginRead(pi.ORIGIN_PRESENT, "git@own.example:o/own.git")

    def test_an_answer_without_an_origin_for_each_value_is_unavailable(self, tmp_path, monkeypatch):
        import subprocess

        assert_outside_repository(tmp_path)

        def git_answers(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, b"git@own.example:o/own.git\0", b"")

        monkeypatch.setattr(pi.subprocess, "run", git_answers)
        assert pi.read_origin(tmp_path) == pi.OriginRead(pi.ORIGIN_UNAVAILABLE, None)

    @pytest.mark.parametrize(
        "urls",
        [
            ["git@a.example:o/a.git", "", "git@b.example:o/b.git"],
            ["git@a.example:o/a.git", ""],
            ["", "git@b.example:o/b.git"],
            [""],
        ],
    )
    def test_a_url_list_holding_an_empty_value_declines(self, tmp_path, urls):
        """git 2.46 reads an empty value as clearing the urls before it, and
        earlier versions do not, so no url in such a list is the repository's
        identity on every machine. It declines, with the override as the
        remedy, and never falls back to the folder name."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        for url in urls:
            _add_url(repo, url)

        assert pi.read_origin(repo / ".git") == pi.OriginRead(pi.ORIGIN_EMPTY_ENTRY, None)
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.decline_reason == pi.decline_record(
            "origin_unrepresentable", str(common_dir(repo) / "pact-project-id")
        )
        assert identity.detail == pi.EMPTY_URL_ENTRY_DETAIL

    @pytest.mark.parametrize(
        "urls, expected",
        [
            (["   "], "repo"),
            (["   ", "git@a.example:o/a.git"], "repo"),
            (["git@a.example:o/a.git", "   "], "a.example/o/a"),
        ],
    )
    def test_a_whitespace_url_is_a_url_not_an_empty_entry(self, tmp_path, urls, expected):
        """Only an EMPTY value clears git's url list; a value of spaces is an
        ordinary value, so it never gets the empty entry's explanation. First
        in the list it is no portable origin (`classify_remote_origin`), so
        the folder name binds as for no origin at all; after a usable url it
        does not decide the id."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        for url in urls:
            _add_url(repo, url)

        assert pi.read_origin(repo / ".git").state == pi.ORIGIN_PRESENT
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id == expected
        assert identity.detail != pi.EMPTY_URL_ENTRY_DETAIL
        assert identity.decline_reason is None

    def test_an_unrepresentable_origin_decline_carries_no_detail(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="https://host/owner/re po")

        identity = pi.detect_id_for_root(repo, common_dir(repo))

        assert identity.decline_reason["reason"] == "origin_unrepresentable"
        assert identity.detail is None

    def test_a_value_holding_a_newline_is_read_as_one_value(self, tmp_path):
        """A line-oriented reader would take the text before the newline as a
        complete, valid remote."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as handle:
            handle.write(
                '[remote "origin"]\n'
                '\turl = "git@a.example:o/a.git\\ngit@evil.example:x/y"\n'
            )

        origin = pi.read_origin(repo / ".git")

        assert origin == pi.OriginRead(
            pi.ORIGIN_PRESENT, "git@a.example:o/a.git\ngit@evil.example:x/y"
        )


class TestMachineLocalConfigIsNotAnIdentityInput:
    """Global, system and environment config, and `config.worktree`, would
    give one repository different ids on different machines or worktrees."""

    def _origin_less_repo(self, tmp_path) -> Path:
        assert_outside_repository(tmp_path)
        return make_repo(tmp_path / "repo")

    def test_a_global_url_does_not_set_the_identity(self, tmp_path, monkeypatch):
        repo = self._origin_less_repo(tmp_path)
        global_config = tmp_path / "global.cfg"
        global_config.write_text(
            '[remote "origin"]\n\turl = git@global.example:o/g.git\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

        assert pi.read_origin(repo / ".git").state == pi.ORIGIN_ABSENT

    def test_environment_config_does_not_set_the_identity(self, tmp_path, monkeypatch):
        repo = self._origin_less_repo(tmp_path)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "git@env.example:o/e.git")
        monkeypatch.setenv(
            "GIT_CONFIG_PARAMETERS", "'remote.origin.url'='git@param.example:o/p.git'"
        )

        assert pi.read_origin(repo / ".git").state == pi.ORIGIN_ABSENT

    def test_an_exported_git_config_file_neither_sets_nor_breaks_the_identity(
        self, tmp_path, monkeypatch
    ):
        """`GIT_CONFIG` names a file for `git config` only; git fetch ignores
        it. Honouring it lets the environment choose the identity, and passing
        it to a `--local` read makes git refuse ("only one config file at a
        time"), which would decline every memory operation."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@local.example:o/l.git")
        other = tmp_path / "other.cfg"
        other.write_text(
            '[remote "origin"]\n\turl = git@other.example:o/x.git\n', encoding="utf-8"
        )
        monkeypatch.setenv("GIT_CONFIG", str(other))

        assert _origin_id(repo) == (CANONICAL, "local.example/o/l")

    def test_a_worktree_scoped_url_does_not_set_the_identity(self, tmp_path):
        repo = self._origin_less_repo(tmp_path)
        run_git("config", "extensions.worktreeConfig", "true", cwd=repo)
        run_git("config", "--worktree", "remote.origin.url",
                "git@worktree.example:o/w.git", cwd=repo)

        assert pi.read_origin(repo / ".git").state == pi.ORIGIN_ABSENT


class TestAUserLessScpRemoteFollowsGitsRule:
    """git reads a value as scp syntax when it contains `:` and no `/`
    precedes the first `:`; the user is optional. Only on Windows is a single
    letter followed by `:` a drive path instead (git's `has_dos_drive_prefix`)."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("github.com:owner/repo.git", "github.com/owner/repo"),
            ("ab:foo", "ab/foo"),
            ("c:foo", "c/foo"),
            ("C:repos/x", "c/repos/x"),
        ],
    )
    def test_a_user_less_host_path_canonicalises_on_posix(self, url, expected, monkeypatch):
        monkeypatch.setattr(pi.os, "name", "posix")
        assert pi.classify_remote_origin(url) == (CANONICAL, expected)

    @pytest.mark.parametrize("url", ["C:/repos/x", "c:foo", "C:\\work\\repo"])
    def test_a_drive_letter_is_a_local_path_on_windows(self, url, monkeypatch):
        monkeypatch.setattr(pi.os, "name", "nt")
        assert pi.classify_remote_origin(url) == (NO_PORTABLE, None)

    def test_a_two_letter_host_is_not_a_drive_on_windows(self, monkeypatch):
        monkeypatch.setattr(pi.os, "name", "nt")
        assert pi.classify_remote_origin("ab:foo") == (CANONICAL, "ab/foo")

    def test_a_drive_letter_with_a_user_is_an_scp_host_on_windows(self, monkeypatch):
        monkeypatch.setattr(pi.os, "name", "nt")
        assert pi.classify_remote_origin("git@c:foo") == (CANONICAL, "c/foo")

    @pytest.mark.parametrize("url", ["github.com:", "git@github.com:"])
    def test_an_empty_scp_path_declines(self, url):
        assert pi.classify_remote_origin(url) == (DECLINE, None)

    @pytest.mark.parametrize("url", ["x/y:z", "./a:b", "/abs/p:q"])
    def test_a_slash_before_the_colon_is_a_local_path(self, url):
        assert pi.classify_remote_origin(url) == (NO_PORTABLE, None)


class TestAControlCharacterInTheOriginDeclines:
    """A value still holding a control character after its one terminator is
    dropped is a present remote that cannot be represented; falling back to
    the folder name would silently mis-scope it."""

    def test_a_newline_between_two_remotes_declines(self):
        url = "git@a.example:o/a.git\ngit@evil.example:x/y\n"
        assert pi.classify_remote_origin(url) == (DECLINE, None)

    def test_the_newline_value_read_from_git_declines(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as handle:
            handle.write(
                '[remote "origin"]\n'
                '\turl = "git@a.example:o/a.git\\ngit@evil.example:x/y"\n'
            )
        assert pi.detect_id_for_root(repo, common_dir(repo)).project_id is None

    @pytest.mark.parametrize(
        "url",
        ["/srv/repo\nother", "../rel\tpath", "/srv/re\x7fpo", "/srv/re\x00po"],
    )
    def test_a_control_character_in_a_local_path_also_declines(self, url):
        """Documented: the value is not a path any repository uses, so it is
        refused visibly rather than read as a folder name."""
        assert pi.classify_remote_origin(url) == (DECLINE, None)

    def test_one_trailing_terminator_is_still_tolerated(self):
        assert pi.classify_remote_origin("https://host/o/r\n") == (CANONICAL, "host/o/r")
        assert pi.classify_remote_origin("/srv/repo\n") == (NO_PORTABLE, None)


class TestRelativeAndAbsoluteScpPathsShareAnId:
    def test_the_relative_absolute_and_url_spellings_are_one_id(self):
        """Kept equal on purpose. On a forge the leading slash is not
        meaningful, so splitting would give every forge's scp and https
        spellings two ids. The accepted cost: on a plain sshd host,
        `~login/srv/repo` and `/srv/repo` are two repositories that share
        this id if both are used against one store."""
        spellings = (
            "git@host:srv/repo",
            "git@host:/srv/repo",
            "ssh://git@host/srv/repo",
            "https://host/srv/repo",
        )
        assert {pi.classify_remote_origin(url) for url in spellings} == {
            (CANONICAL, "host/srv/repo")
        }


# ---------------------------------------------------------------------------
# Submodule admin-dir shapes at any depth
# ---------------------------------------------------------------------------

SUBMODULE_ORIGIN = "https://github.com/victim/lib.git"
SUBMODULE_ID = "github.com/victim/lib"


def _git_file_allowed(*args: str, cwd: Path) -> str:
    return run_git("-c", "protocol.file.allow=always", *args, cwd=cwd)


def _superproject_with_submodule(tmp_path: Path) -> Path:
    """`super` with a submodule at `vendor/lib`, whose admin dir is
    `super/.git/modules/vendor/lib` (a two-segment name). Its origin is the
    local source path until a test sets a network url."""
    assert_outside_repository(tmp_path)
    source = make_repo(tmp_path / "libsrc")
    superproject = make_repo(tmp_path / "super")
    _git_file_allowed("submodule", "add", "-q", str(source), "vendor/lib",
                      cwd=superproject)
    run_git("commit", "-qm", "add submodule", cwd=superproject)
    return superproject


def _superproject_worktree(superproject: Path, path: Path) -> Path:
    """A linked worktree of the superproject, with its submodule checked out
    under `<super>/.git/worktrees/<wt>/modules/vendor/lib`."""
    run_git("worktree", "add", "-q", str(path), "-b", path.name, cwd=superproject)
    _git_file_allowed("submodule", "update", "-q", "--init", cwd=path)
    return path


def _seed_relative_bucket(store: Path, bucket: str) -> None:
    from scripts.database import db_connection, ensure_initialized

    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, session_id, "
            "created_at, updated_at) VALUES ('seeded', 'seeded row', ?, 's', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (bucket,),
        )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f', 'src/a.py', ?)",
            (bucket,),
        )
        conn.commit()


def _memory_in(monkeypatch, checkout: Path, store: Path, declared: Path = None):
    from scripts import memory_api
    from scripts.memory_api import PACTMemory

    monkeypatch.chdir(checkout)
    if declared is None:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
    memory_api._LEGACY_SCOPE_CACHE.clear()
    return PACTMemory(session_id="s", db_path=store)


def _forged_checkout(tmp_path: Path, admin_dir: Path) -> Path:
    """A folder whose `.git` FILE points at somebody else's admin dir."""
    forged = tmp_path / "delivered" / "lib"
    forged.mkdir(parents=True)
    (forged / ".git").write_text(f"gitdir: {admin_dir}\n", encoding="utf-8")
    return forged


class TestASubmoduleInsideASuperprojectWorktreeIsASubmodule:
    @pytest.mark.parametrize("where", ["main", "worktree"])
    def test_a_local_origin_submodule_declines(self, tmp_path, monkeypatch, where):
        superproject = _superproject_with_submodule(tmp_path)
        root = superproject
        if where == "worktree":
            root = _superproject_worktree(superproject, tmp_path / "wt")
        memory = _memory_in(monkeypatch, root / "vendor" / "lib", tmp_path / "m.db")

        assert memory.project_id is None
        assert memory._decline_reason["reason"] == "submodule_checkout"

    @pytest.mark.parametrize("where", ["main", "worktree"])
    def test_a_canonical_submodule_reads_only_its_canonical_bucket(
        self, tmp_path, monkeypatch, where
    ):
        superproject = _superproject_with_submodule(tmp_path)
        root = superproject
        if where == "worktree":
            root = _superproject_worktree(superproject, tmp_path / "wt")
        checkout = root / "vendor" / "lib"
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN, cwd=checkout)
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, "vendor")
        # A recorded session of this checkout wrote the `vendor` bucket, so
        # the session-evidence gate WOULD admit it: only the submodule
        # suppression keeps the repository-chosen name out of the scope.
        seed_session_evidence(tmp_path / "home" / ".claude", checkout, "s")
        memory = _memory_in(monkeypatch, checkout, store)

        assert memory.project_id == SUBMODULE_ID
        assert tuple(memory._read_project_scopes()) == (SUBMODULE_ID,)
        assert memory.list(limit=10) == []


class TestARedirectAtANestedSubmoduleAdminDirIsRefused:
    def _network_submodule(self, tmp_path) -> Path:
        superproject = _superproject_with_submodule(tmp_path)
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN,
                cwd=superproject / "vendor" / "lib")
        return superproject

    def test_a_pointer_at_a_two_segment_admin_dir_declines(self, tmp_path, monkeypatch):
        superproject = self._network_submodule(tmp_path)
        forged = _forged_checkout(
            tmp_path, superproject / ".git" / "modules" / "vendor" / "lib"
        )

        assert pi._gitfile_redirect_detail(forged) is not None
        memory = _memory_in(monkeypatch, forged, tmp_path / "m.db")
        assert memory.project_id is None
        assert memory._decline_reason["reason"] == "gitfile_redirect"

    def test_a_pointer_at_a_superproject_worktree_admin_dir_declines(
        self, tmp_path, monkeypatch
    ):
        superproject = self._network_submodule(tmp_path)
        worktree = _superproject_worktree(superproject, tmp_path / "wt")
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN,
                cwd=worktree / "vendor" / "lib")
        forged = _forged_checkout(
            tmp_path,
            superproject / ".git" / "worktrees" / "wt" / "modules" / "vendor" / "lib",
        )

        assert pi._gitfile_redirect_detail(forged) is not None
        memory = _memory_in(monkeypatch, forged, tmp_path / "m.db")
        assert memory.project_id is None
        assert memory._decline_reason["reason"] == "gitfile_redirect"

    def test_a_pointer_at_a_one_segment_admin_dir_still_declines(self, tmp_path):
        """Control: the shape that was already recognised."""
        assert_outside_repository(tmp_path)
        source = make_repo(tmp_path / "solosrc")
        superproject = make_repo(tmp_path / "super")
        _git_file_allowed("submodule", "add", "-q", str(source), "solo",
                          cwd=superproject)
        forged = _forged_checkout(tmp_path, superproject / ".git" / "modules" / "solo")

        assert pi._gitfile_redirect_detail(forged) is not None

    @pytest.mark.parametrize("where", ["main", "worktree"])
    def test_the_genuine_slash_named_submodule_still_resolves(
        self, tmp_path, monkeypatch, where
    ):
        superproject = self._network_submodule(tmp_path)
        root = superproject
        if where == "worktree":
            root = _superproject_worktree(superproject, tmp_path / "wt")
            run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN,
                    cwd=root / "vendor" / "lib")
        checkout = root / "vendor" / "lib"

        assert pi._gitfile_redirect_detail(checkout) is None
        assert _memory_in(monkeypatch, checkout, tmp_path / "m.db").project_id == (
            SUBMODULE_ID
        )


class TestAWorktreeOfASubmodule:
    def _submodule_worktree(self, tmp_path) -> Path:
        superproject = _superproject_with_submodule(tmp_path)
        worktree = tmp_path / "libwt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "libwt",
                cwd=superproject / "vendor" / "lib")
        return worktree

    def test_its_gitfile_is_not_a_redirect(self, tmp_path):
        """Its admin dir `<super>/.git/modules/vendor/lib/worktrees/libwt`
        has the submodule shape, and its shared `core.worktree` names the
        submodule's main checkout. git's `commondir` mark tells it apart."""
        worktree = self._submodule_worktree(tmp_path)

        assert pi._gitfile_redirect_detail(worktree) is None

    def test_a_network_origin_worktree_canonicalises(self, tmp_path, monkeypatch):
        worktree = self._submodule_worktree(tmp_path)
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN, cwd=worktree)

        assert _memory_in(monkeypatch, worktree, tmp_path / "m.db").project_id == (
            SUBMODULE_ID
        )

    def test_a_local_origin_worktree_declines_instead_of_binding_its_folder(
        self, tmp_path, monkeypatch
    ):
        worktree = self._submodule_worktree(tmp_path)
        memory = _memory_in(monkeypatch, worktree, tmp_path / "m.db")

        assert memory.project_id is None
        assert memory._decline_reason["reason"] == "submodule_checkout"


class TestAnUnparseableScpRemoteStillDeclines:
    """A value git reads as scp but our grammar cannot split (more than one
    `@`, or a backslash) is a present network remote: it must DECLINE, never
    fall through to the folder name. The shared corpus is the set both
    remote parsers refuse."""

    @pytest.mark.parametrize("os_name", ["posix", "nt"])
    def test_every_shared_refusal_declines(self, monkeypatch, os_name):
        from test_remote_parser_agreement import SHARED_REFUSALS

        monkeypatch.setattr(pi.os, "name", os_name)
        outcomes = {url: pi.classify_remote_origin(url) for url, _rule in SHARED_REFUSALS}

        assert outcomes == {url: (DECLINE, None) for url, _rule in SHARED_REFUSALS}

    @pytest.mark.parametrize(
        "url",
        ["a@b@github.com:o/n", "git@a@b:owner/repo", "git@a@b@github.com:o/n",
         "host:owner\\repo", "a@b@host:"],
    )
    def test_a_multi_at_or_backslash_scp_value_declines(self, url):
        assert pi.classify_remote_origin(url) == (DECLINE, None)

    @pytest.mark.parametrize("url", ["/srv/a\\b:c", "..\\rel\\repo", "/srv/team@a@b"])
    def test_a_local_path_with_a_backslash_or_two_ats_stays_local(self, url):
        assert pi.classify_remote_origin(url) == (NO_PORTABLE, None)


class TestAPointerAtASubmoduleWorktreeAdminDirIsRefused:
    """A `.git` FILE naming a submodule's LINKED-WORKTREE admin dir makes git
    answer for the victim submodule (its common dir, its origin) although the
    asking folder is not one of its worktrees. Only git's worktree listing
    tells the two apart, as it does for a superproject's worktree."""

    def _victim(self, tmp_path):
        superproject = _superproject_with_submodule(tmp_path)
        submodule = superproject / "vendor" / "lib"
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN, cwd=submodule)
        worktree = tmp_path / "libwt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "libwt", cwd=submodule)
        admin = superproject / ".git" / "modules" / "vendor" / "lib"
        return worktree, admin

    def test_the_forged_pointer_binds_no_id_and_reads_nothing(
        self, tmp_path, monkeypatch
    ):
        from scripts.memory_api import _worktree_redirect

        _worktree, admin = self._victim(tmp_path)
        forged = _forged_checkout(tmp_path, admin / "worktrees" / "libwt")
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, SUBMODULE_ID)

        assert _worktree_redirect(forged, admin) is True
        memory = _memory_in(monkeypatch, forged, store)
        assert memory.project_id is None
        assert memory.list(limit=10) == []

    def test_the_genuine_worktree_still_resolves(self, tmp_path, monkeypatch):
        from scripts.memory_api import _worktree_redirect

        worktree, admin = self._victim(tmp_path)

        assert _worktree_redirect(worktree, admin) is False
        assert _memory_in(monkeypatch, worktree, tmp_path / "m.db").project_id == (
            SUBMODULE_ID
        )


# ---------------------------------------------------------------------------
# The repository override accepts canonical ids only
# ---------------------------------------------------------------------------

class TestTheOverrideAcceptsCanonicalIdsOnly:
    def _delivered(self, tmp_path, pin: str) -> Path:
        """A delivered checkout whose own `.git` carries an override pin."""
        assert_outside_repository(tmp_path)
        checkout = make_repo(
            tmp_path / "delivered" / "checkout",
            remote="https://github.com/evil/checkout.git",
        )
        write_common_dir_override(checkout, pin)
        return checkout

    def test_a_folder_name_pin_does_not_read_that_folder_bucket(
        self, tmp_path, monkeypatch
    ):
        checkout = self._delivered(tmp_path, "proj")
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, "proj")
        memory = _memory_in(monkeypatch, checkout, store)

        assert memory.project_id == "github.com/evil/checkout"
        assert "proj" not in tuple(memory._read_project_scopes())
        assert memory.list(limit=10) == []
        assert memory._override_ignored == {
            "path": str(common_dir(checkout) / "pact-project-id"),
            "reason": "not_canonical",
        }

    @pytest.mark.parametrize("pin", ["proj", "not canonical/x y", "host/"])
    def test_a_non_canonical_value_is_reported_and_skipped(self, tmp_path, pin):
        checkout = self._delivered(tmp_path, pin)

        identity = pi.detect_id_for_root(checkout, common_dir(checkout))

        assert identity.source == "origin remote"
        assert identity.override_ignored["reason"] == "not_canonical"

    def test_a_canonical_pin_still_binds(self, tmp_path):
        checkout = self._delivered(tmp_path, "github.com/me/pinned")

        identity = pi.detect_id_for_root(checkout, common_dir(checkout))

        assert (identity.project_id, identity.id_shape, identity.source) == (
            "github.com/me/pinned", "canonical", "override file"
        )
        assert identity.override_ignored is None


# ---------------------------------------------------------------------------
# One decline record
# ---------------------------------------------------------------------------

DOCUMENTED_DECLINE_REASONS = {
    "origin_unreadable",
    "origin_unrepresentable",
    "gitfile_redirect",
    "broken_git_marker",
    "no_working_tree",
    "submodule_checkout",
    "declaration_unresolved",
}


class TestOneDeclineRecord:
    def test_the_reason_set_is_the_seven_documented_causes(self):
        assert pi.DECLINE_REASONS == DOCUMENTED_DECLINE_REASONS

    def test_the_record_has_exactly_three_keys(self):
        assert pi.decline_record("submodule_checkout", "/r/.git/pact-project-id") == {
            "source": None,
            "reason": "submodule_checkout",
            "override_path": "/r/.git/pact-project-id",
        }
        assert pi.decline_record("origin_unreadable", None, "cwd") == {
            "source": "cwd", "reason": "origin_unreadable", "override_path": None,
        }

    @pytest.mark.parametrize("reason", ["repository_chosen_name", "", "Gitfile_Redirect"])
    def test_an_unknown_reason_is_refused(self, reason):
        with pytest.raises(ValueError):
            pi.decline_record(reason, None)

    def test_no_decline_dict_is_built_outside_the_record(self):
        """Every dict carrying a `reason` key, other than the `override_ignored`
        `{path, reason}` pair, must go through `decline_record`, so its shape
        and its reason set cannot drift. The sites are found in every form the
        code can build such a dict (`reason_dict_sites`), not only a literal."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(pi))
        builder = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "decline_record"
        )
        inside = frozenset(id(node) for node in ast.walk(builder))
        sites = reason_dict_sites(tree, inside)
        assert [line for line, has_path in sites if not has_path] == []
        # The override_ignored pairs are found, so the scan is reading the module.
        assert len(sites) >= 2, sites
        # A `reason` key in a form the scan cannot classify fails closed.
        assert unread_reason_keys(tree, inside) == []

    def test_a_reason_key_in_an_unread_form_fails_closed(self):
        """Live control for `unread_reason_keys`: each fixture line carries
        `unread` (a `reason` key the dict scan cannot classify) or `read` (a
        form the dict scan or a plain read accounts for)."""
        import ast
        import textwrap

        source = textwrap.dedent("""
            import collections
            import operator
            KEY = "reason"  # read

            class Keys:
                REASON = "reason"  # read

            def helper(reason=None):
                return reason

            def build(record, reason, rows):
                record["reason"], record["rung"] = reason, "git"  # unread
                collections.OrderedDict(reason=reason)  # unread
                operator.setitem(record, "reason", reason)  # unread
                record.__setitem__(KEY, reason)  # unread
                labels = ("reason", "path")  # unread
                for record[KEY] in rows:  # unread
                    pass
                record[Keys.REASON] = reason  # read
                record["reason"] = reason  # read
                record[KEY] += reason  # read
                value = record["reason"]  # read
                del record["reason"]  # read
                record.get("reason")  # read
                record.pop(KEY, None)  # read
                "reason" in record  # read
                dict([("reason", reason)])  # read
                dict(reason=reason)  # read
                helper(reason=reason)  # read
                return {Keys.REASON: reason}  # read
        """)
        expected = {
            number: line.rsplit("# ", 1)[1]
            for number, line in enumerate(source.splitlines(), start=1)
            if line.rstrip().endswith(("# unread", "# read"))
        }
        assert len(expected) == 20, expected
        found = set(unread_reason_keys(ast.parse(source)))
        assert {number: ("unread" if number in found else "read") for number in expected} == expected
        assert found <= set(expected), sorted(found - set(expected))
        # A class-attribute key is a site the dict scan reports, not an unread one.
        sites = {line for line, _has_path in reason_dict_sites(ast.parse(source))}
        assert {n for n, line in enumerate(source.splitlines(), 1) if "Keys.REASON" in line} <= sites

    def test_the_reason_scan_reads_every_form(self):
        """Live control for the scan above. Each line in the fixture carries
        the verdict it must reach: `decline` (a reason dict without a path),
        `pair` (a reason dict that also sets `path`) or `none`."""
        import ast
        import textwrap

        source = textwrap.dedent("""
            MODULE_LEVEL = {"reason": "x"}  # decline
            KEY = "reason"

            def build(base, reason, items, param_key):
                a = {"reason": reason, "rung": "git"}  # decline
                b = {**base, "reason": reason}  # decline
                c = dict(reason=reason, rung="git")  # decline
                d = dict([("reason", reason), ("rung", "git")])  # decline
                base["reason"] = reason  # decline
                base.update(reason=reason)  # decline
                base.setdefault("reason", reason)  # decline
                e = {"path": "p", "reason": reason}  # pair
                f = dict(path="p", reason=reason)  # pair
                g = {"reasons": reason}  # none
                h = ["reason", reason]  # none
                items.update(rung="git")  # none
                base.update([("reason", reason)])  # decline
                base[KEY] = reason  # decline
                key = "reason"
                base[key] = reason  # decline
                base[param_key] = reason  # none
                base.update([("rung", "git")])  # none
                inner = lambda: {KEY: "x"}  # decline
                listed = [{"reason": r} for r in items]  # decline

                def nested(store):
                    store[key] = "x"  # decline

            built = lambda: dict(reason="x")  # decline

            class Holder:
                SLOT = "reason"
                made = lambda: {"reason": "x"}  # decline
                kept = {SLOT: "x"}  # decline
                other = {"rung": "x"}  # none
        """)
        expected = {
            number: line.rsplit("# ", 1)[1]
            for number, line in enumerate(source.splitlines(), start=1)
            if line.rstrip().endswith(("# decline", "# pair", "# none"))
        }
        assert len(expected) == 25, expected
        found = {
            line: ("pair" if has_path else "decline")
            for line, has_path in reason_dict_sites(ast.parse(source))
        }
        assert {number: found.get(number, "none") for number in expected} == expected

    def test_a_real_decline_is_built_by_the_record(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="https://host/owner/re po")

        identity = pi.detect_id_for_root(repo, common_dir(repo))

        assert identity.decline_reason == pi.decline_record(
            "origin_unrepresentable", str(common_dir(repo) / "pact-project-id")
        )


# ---------------------------------------------------------------------------
# One git-discovery walk, with git's ceiling semantics
# ---------------------------------------------------------------------------

class TestGitDiscoveryHonoursACeilingAsGitDoes:
    """git still examines the starting directory when it is a
    `GIT_CEILING_DIRECTORIES` entry, and never examines a parent that is one
    (measured: `git -C <ceiling>` finds a repository there; `git -C
    <ceiling>/sub` does not)."""

    def test_a_marker_at_a_ceiling_start_directory_is_found(self, tmp_path, monkeypatch):
        (tmp_path / "top").mkdir()
        (tmp_path / "top" / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path / "top"))

        assert pi.find_git_marker(tmp_path / "top") == (tmp_path / "top" / ".git").resolve()

    def test_a_marker_in_a_ceiling_parent_is_not_found(self, tmp_path, monkeypatch):
        (tmp_path / "top" / "sub").mkdir(parents=True)
        (tmp_path / "top" / ".git").mkdir()
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path / "top"))

        assert pi.find_git_marker(tmp_path / "top" / "sub") is None

    def test_a_marker_above_with_no_ceiling_is_found(self, tmp_path, monkeypatch):
        (tmp_path / "top" / "sub").mkdir(parents=True)
        _git_directory(tmp_path / "top" / ".git")
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

        assert pi.find_git_marker(tmp_path / "top" / "sub") == (
            (tmp_path / "top" / ".git").resolve()
        )

    def test_a_fifo_commondir_refuses_without_blocking(self, tmp_path):
        """A `commondir` that is a FIFO is read by the bounded reader, which
        never opens it: the folder is not a git directory. RED WHEN a
        non-regular commondir is skipped (the folder then passes on its own
        objects/ and refs/) or read without the bound."""
        git_dir = tmp_path / ".git"
        _git_directory(git_dir)
        os.mkfifo(git_dir / "commondir")
        assert pi._is_git_directory(git_dir) is False

    def test_an_oversized_commondir_refuses(self, tmp_path):
        """A commondir past GIT_FILE_LIMIT is refused, not read whole."""
        from shared.git_helpers import GIT_FILE_LIMIT

        git_dir = tmp_path / ".git"
        _git_directory(git_dir)
        (git_dir / "commondir").write_bytes(b"." + b"/" * GIT_FILE_LIMIT)
        assert pi._is_git_directory(git_dir) is False

    @pytest.mark.parametrize("shape", ["{p}\n", "{p}\r\n", " {p}\n", "{p}\t\n", "{p} \n"])
    def test_commondir_whitespace_is_read_as_git_reads_it(self, tmp_path, shape):
        """A folder with HEAD whose commondir names the real common dir with
        whitespace around it: it is a git directory exactly when git accepts
        it (git drops only trailing CRs and LFs). RED WHEN the whole value is
        stripped, which accepts a leading space or a trailing tab or space
        that git keeps in the path."""
        import subprocess

        from shared.project_scope import git_env_without_location

        real = tmp_path / "real"
        subprocess.run(["git", "init", "-q", str(real)], check=True)
        probe = tmp_path / "probe"
        probe.mkdir()
        (probe / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (probe / "commondir").write_text(shape.format(p=real / ".git"), encoding="utf-8")
        git = subprocess.run(
            ["git", "--git-dir", str(probe), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, env=git_env_without_location(), check=False,
        )
        assert pi._is_git_directory(probe) is (git.returncode == 0), git.stderr

    def test_git_object_directory_does_not_make_git_and_the_walk_disagree(
        self, tmp_path, monkeypatch
    ):
        """A `.git` folder with HEAD and refs/ but no objects/, under an
        inherited GIT_OBJECT_DIRECTORY naming real objects: the walk skips the
        folder, and git run with the location-free environment refuses it
        too. RED WHEN GIT_OBJECT_DIRECTORY survives into that environment, so
        git accepts a folder the walk passed."""
        import subprocess

        from shared.project_scope import git_env_without_location

        real = tmp_path / "real"
        subprocess.run(["git", "init", "-q", str(real)], check=True)
        probe = tmp_path / "probe"
        (probe / ".git" / "refs").mkdir(parents=True)
        (probe / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(real / ".git" / "objects"))
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
        assert pi._is_git_directory(probe / ".git") is False
        env = git_env_without_location()
        assert "GIT_OBJECT_DIRECTORY" not in env
        answer = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--git-dir"],
            capture_output=True, text=True, env=env,
        )
        assert answer.returncode != 0, answer.stdout

    def test_a_dangling_marker_symlink_still_counts(self, tmp_path):
        (tmp_path / "top").mkdir()
        (tmp_path / "top" / ".git").symlink_to(tmp_path / "missing")

        assert pi.find_git_marker(tmp_path / "top") is not None

    def test_a_forged_pointer_at_a_ceiling_start_is_still_a_redirect(
        self, tmp_path, monkeypatch
    ):
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "victim", remote="https://github.com/v/p.git")
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(forged))

        assert pi._gitfile_redirect_detail(forged) is not None


# ---------------------------------------------------------------------------
# Percent-encoded and non-ASCII paths, `ssh+git`, Azure DevOps Services
# ---------------------------------------------------------------------------

NFC_CAFE = "caf\xe9"
NFD_CAFE = "cafe\u0301"


class TestPercentEncodingIsNormalisedNotDecoded:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://host/o/caf%C3%A9", "host/o/caf%C3%A9"),
            ("https://host/o/caf%c3%a9", "host/o/caf%C3%A9"),
            ("https://host/o/" + NFC_CAFE, "host/o/caf%C3%A9"),
            ("https://host/o/" + NFD_CAFE, "host/o/cafe%CC%81"),
            ("https://host/o/%41bc", "host/o/Abc"),
            ("https://host/o/%7Euser", "host/o/~user"),
            ("https://host/o/re%2Fpo", "host/o/re%2Fpo"),
            ("https://host/o/a%21b", "host/o/a%21b"),
            ("https://host/o/a!b", "host/o/a!b"),
            ("https://host/owner/r\xe9po", "host/owner/r%C3%A9po"),
            # scp is sent literally, so its `%` is an ordinary character.
            ("git@host:o/My%20Project.git", "host/o/My%2520Project"),
            ("ssh://git@host/o/My%20Project.git", "host/o/My%20Project"),
        ],
    )
    def test_a_segment_is_put_in_rfc3986_normal_form(self, url, expected):
        assert pi.classify_remote_origin(url) == (CANONICAL, expected)

    def test_nfc_and_nfd_spellings_stay_two_ids(self):
        nfc = pi.normalize_remote_url("https://host/o/" + NFC_CAFE)
        nfd = pi.normalize_remote_url("https://host/o/" + NFD_CAFE)
        assert nfc is not None and nfd is not None and nfc != nfd

    def test_an_encoded_slash_stays_inside_one_segment(self):
        assert pi.normalize_remote_url("https://host/o/re%2Fpo").count("/") == 2

    @pytest.mark.parametrize(
        "url",
        [
            "https://host/o/%2E%2E",
            "https://host/o/%2e",
            "https://host/o/re%ZZpo",
            "https://host/o/re%4",
            "https://host/o/re%",
            "https://host/o/re po",
            "https://host/o/re%20po ",
            "https://host/o/a@b",
        ],
    )
    def test_an_unrepresentable_segment_still_declines(self, url):
        assert pi.classify_remote_origin(url) == (DECLINE, None)

    def test_an_undecodable_byte_still_declines(self):
        assert pi.classify_remote_origin("https://host/o/re\udcffpo") == (DECLINE, None)

    @pytest.mark.parametrize(
        "value,accepted",
        [
            ("host/o/caf%C3%A9", True),
            ("host/o/My%20Project", True),
            ("host/o/caf%c3%a9", False),
            ("host/o/%G1", False),
            ("host/o/%4", False),
            ("host/o/100%", False),
        ],
    )
    def test_the_grammar_admits_only_uppercase_percent_triples(self, value, accepted):
        assert pi.is_canonical(value) is accepted


class TestTheSshPlusGitAlias:
    @pytest.mark.parametrize(
        "url",
        ["ssh+git://git@host/o/r", "ssh+git://git@host:22/o/r", "git+ssh://git@host/o/r"],
    )
    def test_it_is_the_same_remote_as_ssh(self, url):
        assert pi.classify_remote_origin(url) == (CANONICAL, "host/o/r")

    def test_a_non_default_port_is_kept(self):
        assert pi.normalize_remote_url("ssh+git://git@host:2222/o/r") == "host:2222/o/r"


AZURE_ID = "dev.azure.com/fabrikam/My%20Project/_git/repo"


class TestAzureDevOpsServicesSpellingsShareOneId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://dev.azure.com/fabrikam/My%20Project/_git/repo",
            "https://fabrikam@dev.azure.com/fabrikam/My%20Project/_git/repo",
            "git@ssh.dev.azure.com:v3/fabrikam/My%20Project/repo",
            "ssh://git@ssh.dev.azure.com/v3/fabrikam/My%20Project/repo",
            "https://fabrikam.visualstudio.com/My%20Project/_git/repo",
            "https://fabrikam.visualstudio.com/DefaultCollection/My%20Project/_git/repo",
            "fabrikam@vs-ssh.visualstudio.com:v3/fabrikam/My%20Project/repo",
        ],
    )
    def test_every_services_spelling_maps_to_one_id(self, url):
        assert pi.classify_remote_origin(url) == (CANONICAL, AZURE_ID)

    @pytest.mark.parametrize(
        "url",
        [
            "https://dev.azure.com/Fabrikam/My%20Project/_git/repo",
            "git@ssh.dev.azure.com:v3/FABRIKAM/My%20Project/repo",
            "https://Fabrikam.visualstudio.com/My%20Project/_git/repo",
        ],
    )
    def test_the_organisation_is_case_insensitive(self, url):
        assert pi.classify_remote_origin(url) == (CANONICAL, AZURE_ID)

    def test_project_and_repository_keep_their_case(self):
        assert pi.normalize_remote_url(
            "https://dev.azure.com/fabrikam/my%20project/_git/Repo"
        ) == "dev.azure.com/fabrikam/my%20project/_git/Repo"

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://tfs.example.com/Coll/P/_git/R", "tfs.example.com/Coll/P/_git/R"),
            ("ssh://git@ssh.dev.azure.com:2222/v3/o/p/r", "ssh.dev.azure.com:2222/v3/o/p/r"),
            ("git@ssh.dev.azure.com:v2/o/p/r", "ssh.dev.azure.com/v2/o/p/r"),
            ("https://dev.azure.com/o/p/r", "dev.azure.com/o/p/r"),
            ("https://a.b.visualstudio.com/p/_git/r", "a.b.visualstudio.com/p/_git/r"),
        ],
    )
    def test_any_other_shape_keeps_the_generic_id(self, url, expected):
        assert pi.normalize_remote_url(url) == expected


class TestARedirectDeclineNamesNoOverridePath:
    """The common dir git reports for a redirected `.git` file belongs to the
    repository it points at, so naming that repository's override file as the
    remedy would send the operator to re-pin the other repository."""

    def test_the_record_refuses_an_override_path_for_a_redirect(self):
        with pytest.raises(ValueError):
            pi.decline_record("gitfile_redirect", "/victim/.git/pact-project-id")
        assert pi.decline_record("gitfile_redirect", None)["override_path"] is None

    def test_other_reasons_still_carry_their_override_path(self):
        assert pi.decline_record("origin_unreadable", "/r/.git/pact-project-id")[
            "override_path"
        ] == "/r/.git/pact-project-id"

    def test_a_forged_pointer_declines_with_no_override_path(self, tmp_path):
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "victim", remote="https://github.com/v/p.git")
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")

        identity = pi.detect_id_for_root(forged, common_dir(victim), forged)

        assert identity.project_id is None
        assert identity.decline_reason == pi.decline_record("gitfile_redirect", None)


class TestCeilingEntriesAreParsedAsGitParsesThem:
    """Differential against real git: the walk stops exactly where git does.

    After the first EMPTY entry git keeps an entry as written, without
    resolving symlinks, and a relative entry is dropped."""

    @staticmethod
    def _git_finds_repo(start: Path) -> bool:
        import subprocess

        from shared.project_scope import git_env_without_location

        return subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--git-dir"],
            capture_output=True, timeout=30, env=git_env_without_location(),
        ).returncode == 0

    @pytest.mark.parametrize(
        "ceiling",
        ["{a}", "{link}", ":{link}", ":{a}", "{link}:", "a", "", ":", "{other}:{link}"],
    )
    def test_find_git_marker_agrees_with_git(self, tmp_path, monkeypatch, ceiling):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "a")
        (repo / "sub").mkdir()
        (tmp_path / "link").symlink_to(repo)
        (tmp_path / "other").mkdir()
        value = ceiling.format(
            a=repo, link=tmp_path / "link", other=tmp_path / "other"
        ).replace(":", os.pathsep)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", value)

        found = pi.find_git_marker(repo / "sub") is not None

        assert found == self._git_finds_repo(repo / "sub"), value

    def test_a_symlinked_entry_after_an_empty_one_does_not_stop_the_walk(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "a" / "sub").mkdir(parents=True)
        _git_directory(tmp_path / "a" / ".git")
        (tmp_path / "link").symlink_to(tmp_path / "a")
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", os.pathsep + str(tmp_path / "link"))

        assert pi.find_git_marker(tmp_path / "a" / "sub") is not None


class TestAPointerAtASeparateGitDirWorktreeAdminDirIsRefused:
    """A `--separate-git-dir` repository keeps its git dir outside the
    checkout (`vgit`, not `<checkout>/.git`), so a linked worktree's admin dir
    is `vgit/worktrees/<wt>`. A forged `.git` FILE naming that admin dir makes
    every git answer describe the victim, and only the victim's worktree
    listing shows the folder is not one of its worktrees."""

    VICTIM = "https://github.com/victim/repo.git"
    VICTIM_ID = "github.com/victim/repo"

    def _victim(self, tmp_path):
        assert_outside_repository(tmp_path)
        checkout = tmp_path / "vrepo"
        checkout.mkdir()
        run_git("init", "-q", f"--separate-git-dir={tmp_path / 'vgit'}", ".", cwd=checkout)
        run_git("config", "user.email", "t@example.invalid", cwd=checkout)
        run_git("config", "user.name", "t", cwd=checkout)
        (checkout / "README").write_text("seed\n", encoding="utf-8")
        run_git("add", "README", cwd=checkout)
        run_git("commit", "-qm", "seed", cwd=checkout)
        run_git("remote", "add", "origin", self.VICTIM, cwd=checkout)
        worktree = tmp_path / "vwt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "vwt", cwd=checkout)
        admin = tmp_path / "vgit" / "worktrees" / "vwt"
        assert (admin / "commondir").is_file()
        delivered = tmp_path / "dl" / "repo"
        (delivered / "sub").mkdir(parents=True)
        (delivered / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, self.VICTIM_ID)
        return worktree, delivered, store

    def _assert_refused(self, memory, store, monkeypatch, source):
        from scripts import memory_api
        from scripts.database import count_project_memories, db_connection
        from scripts.memory_api import PACTMemory, ProjectIdentityUnresolvedError

        assert memory.project_id is None
        assert memory._decline_reason == pi.decline_record("gitfile_redirect", None, source)
        assert tuple(memory._read_project_scopes()) == ()
        assert memory.list(limit=10) == []
        monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
        monkeypatch.setattr(PACTMemory, "_store_embedding", lambda self, *a, **k: None)
        with pytest.raises(ProjectIdentityUnresolvedError):
            memory.save({"context": "from the delivered checkout"}, sync_to_claude=False)
        with db_connection(store) as conn:
            assert count_project_memories(conn, self.VICTIM_ID) == 1

    @pytest.mark.parametrize("where", ["root", "subdirectory"])
    def test_the_delivered_checkout_binds_nothing(self, tmp_path, monkeypatch, where):
        _worktree, delivered, store = self._victim(tmp_path)
        cwd = delivered if where == "root" else delivered / "sub"
        memory = _memory_in(monkeypatch, cwd, store)

        self._assert_refused(memory, store, monkeypatch, "git root")

    @pytest.mark.parametrize("where", ["root", "subdirectory"])
    def test_a_declared_delivered_checkout_binds_nothing(
        self, tmp_path, monkeypatch, where
    ):
        _worktree, delivered, store = self._victim(tmp_path)
        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        declared = delivered if where == "root" else delivered / "sub"
        memory = _memory_in(monkeypatch, elsewhere, store, declared=declared)

        self._assert_refused(memory, store, monkeypatch, "CLAUDE_PROJECT_DIR")

    def test_a_ceiling_at_the_delivered_checkout_binds_nothing(self, tmp_path, monkeypatch):
        """`GIT_CEILING_DIRECTORIES` naming the delivered checkout, with the
        cwd below it, hides the checkout from git and from the marker walk, so
        the git-root rung finds nothing and resolution reaches the cwd-marker
        rung, whose project-root walk does not stop at a ceiling. That rung's
        own guard must refuse the pointer."""
        _worktree, delivered, store = self._victim(tmp_path)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(delivered))
        memory = _memory_in(monkeypatch, delivered / "sub", store)

        self._assert_refused(memory, store, monkeypatch, "cwd")

    @pytest.mark.parametrize("marker", ["forged", "broken"])
    def test_a_recorded_root_is_guarded_like_every_other_rung(
        self, tmp_path, monkeypatch, marker
    ):
        """A gone declaration resolves through the worktree identity record
        to the root the record check proves. That check already requires the
        root to be its own toplevel and its common dir's parent, so no record
        it accepts names a forged or broken checkout; the resolver's own guard
        on the recorded root is the defence if that shared check ever admits
        one. Forced here by making the record check return such a root."""
        from shared import project_scope

        _worktree, delivered, store = self._victim(tmp_path)
        root = delivered
        if marker == "broken":
            root = tmp_path / "broken"
            root.mkdir()
            (root / ".git").write_text(f"gitdir: {tmp_path / 'missing'}\n", encoding="utf-8")
        monkeypatch.setattr(
            project_scope, "recorded_main_root", lambda declared, identity: root
        )
        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        memory = _memory_in(
            monkeypatch, elsewhere, store, declared=tmp_path / "gone-worktree"
        )

        assert memory.project_id is None
        assert memory._decline_reason["reason"] == (
            "gitfile_redirect" if marker == "forged" else "broken_git_marker"
        )
        assert memory.list(limit=10) == []

    def test_the_genuine_worktree_still_resolves(self, tmp_path, monkeypatch):
        worktree, _delivered, store = self._victim(tmp_path)
        memory = _memory_in(monkeypatch, worktree, store)

        assert memory.project_id == self.VICTIM_ID
        assert memory._decline_reason is None

    BAD_ORIGIN = "https://bad host.example/victim/repo.git"

    # The rung each route reaches: the ceiling route hides the checkout from
    # the git-root rung and lands on the cwd-marker rung.
    RUNGS = {"cwd": "git root", "declared": "CLAUDE_PROJECT_DIR", "ceiling": "cwd"}

    @pytest.mark.parametrize("route", ["cwd", "declared", "ceiling"])
    def test_a_forgery_prints_no_path_into_the_victims_store(
        self, tmp_path, monkeypatch, caplog, route
    ):
        """The victim's own worktree, with an origin that cannot be
        represented, prints the override path inside the victim's store (its
        own git dir), and the store holds an unusable pin. The forged pointer
        must print neither path: not in the warning, the save refusal or the
        envelope. Catches the forged route reaching the override read (where
        the worktree-admin target is not a redirect to the resolver and its
        common dir is the victim's store) instead of being refused first."""
        import logging

        from scripts import memory_api
        from scripts.memory_api import PACTMemory

        worktree, delivered, store = self._victim(tmp_path)
        run_git("remote", "set-url", "origin", self.BAD_ORIGIN, cwd=worktree)
        victim_store = tmp_path / "vgit"
        (victim_store / "pact-project-id").write_text("victim-folder-name\n", encoding="utf-8")
        pin = str(victim_store / "pact-project-id")

        genuine = _memory_in(monkeypatch, worktree, store)
        assert genuine._decline_reason["override_path"] == pin, (
            "control: the genuine worktree offers its own store's pin"
        )

        elsewhere = tmp_path / "plain"
        elsewhere.mkdir()
        memory_api._LEGACY_SCOPE_CACHE.clear()
        with caplog.at_level(logging.WARNING):
            caplog.clear()
            if route == "declared":
                memory = _memory_in(monkeypatch, elsewhere, store, declared=delivered)
            elif route == "ceiling":
                monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(delivered))
                memory = _memory_in(monkeypatch, delivered / "sub", store)
            else:
                memory = _memory_in(monkeypatch, delivered, store)
            monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
            monkeypatch.setattr(PACTMemory, "_store_embedding", lambda self, *a, **k: None)
            with pytest.raises(Exception) as refused:
                memory.save({"context": "from the forged checkout"}, sync_to_claude=False)
        envelope = repr((memory._decline_reason, memory._override_ignored))
        texts = [record.getMessage() for record in caplog.records]
        texts += [str(refused.value), envelope]
        for text in texts:
            assert pin not in text and str(victim_store) not in text, text
        assert memory._decline_reason == pi.decline_record(
            "gitfile_redirect", None, self.RUNGS[route]
        )


class TestABrokenMarkerAboveACeilingDeclinesOnTheCwdRung:
    """A `.git` git cannot answer for, at a checkout root named by
    `GIT_CEILING_DIRECTORIES`, with the cwd below it: git and the marker walk
    both stop at the ceiling, so the git-root rung sees no marker, and the
    cwd-marker rung's project-root walk lands on the checkout. That rung's own
    guard must decline it as a broken marker instead of naming the folder.
    Without the ceiling the git-root rung declines the same checkout."""

    @pytest.mark.parametrize("ceiling", [True, False], ids=["ceiling", "no_ceiling"])
    @pytest.mark.parametrize("cause", ["dangling_gitfile", "git_fails"])
    def test_the_checkout_declines(self, tmp_path, monkeypatch, cause, ceiling):
        assert_outside_repository(tmp_path)
        top = tmp_path / "top"
        if cause == "dangling_gitfile":
            (top / "sub").mkdir(parents=True)
            (top / ".git").write_text(f"gitdir: {tmp_path / 'missing'}\n", encoding="utf-8")
        else:
            make_repo(top, remote="https://github.com/v/p.git")
            (top / "sub").mkdir()
            shim = tmp_path / "bin"
            shim.mkdir()
            (shim / "git").write_text("#!/bin/sh\nexit 128\n", encoding="utf-8")
            (shim / "git").chmod(0o755)
            monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
        if ceiling:
            monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(top))
        else:
            monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
        memory = _memory_in(monkeypatch, top / "sub", tmp_path / "m.db")

        assert memory.project_id is None
        assert memory._decline_reason == pi.decline_record(
            "broken_git_marker", None, "cwd" if ceiling else "git root"
        )
        assert memory.list(limit=10) == []


class TestAnEmptyOriginUrlValueAgainstRealGit:
    """Differential against this machine's git: which url `git fetch` really
    contacts, beside what the identity resolves. A fake ssh records the host
    and fails, so nothing is contacted."""

    @staticmethod
    def _fetch_target(repo: Path, tmp_path: Path):
        import subprocess

        log = tmp_path / "ssh.log"
        fake_ssh = tmp_path / "fake-ssh"
        fake_ssh.write_text(
            f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', encoding="utf-8"
        )
        fake_ssh.chmod(0o755)
        env = dict(os.environ, GIT_SSH_COMMAND=str(fake_ssh))
        env.pop("GIT_DIR", None)
        subprocess.run(
            ["git", "-C", str(repo), "fetch", "-q", "origin"],
            capture_output=True, timeout=30, env=env,
        )
        hosts = [
            word for line in (log.read_text().splitlines() if log.exists() else [])
            for word in line.split() if word.startswith("git@")
        ]
        return hosts[0] if hosts else None

    def _repo(self, tmp_path, urls):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        for url in urls:
            _add_url(repo, url)
        run_git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*",
                cwd=repo)
        return repo

    @staticmethod
    def _git_version() -> tuple:
        import subprocess

        words = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=30
        ).stdout.split()
        return tuple(int(part) for part in words[2].split(".")[:2])

    @pytest.mark.parametrize(
        "urls,before_2_46,from_2_46",
        [
            (["git@a.example:o/a.git", "", "git@b.example:o/b.git"],
             "git@a.example", "git@b.example"),
            (["git@a.example:o/a.git", ""], "git@a.example", None),
        ],
    )
    def test_git_fetches_a_url_the_identity_declines_to_choose(
        self, tmp_path, urls, before_2_46, from_2_46
    ):
        """The fetch target depends on the git version; the identity does
        not, because it declines on every version."""
        repo = self._repo(tmp_path, urls)

        fetched = self._fetch_target(repo, tmp_path)
        identity = pi.detect_id_for_root(repo, common_dir(repo))

        expected = before_2_46 if self._git_version() < (2, 46) else from_2_46
        assert fetched == expected
        assert identity.project_id is None
        assert identity.decline_reason["reason"] == "origin_unrepresentable"

    def test_the_control_without_an_empty_value_matches_git(self, tmp_path):
        repo = self._repo(tmp_path, ["git@a.example:o/a.git", "git@b.example:o/b.git"])

        assert self._fetch_target(repo, tmp_path) == "git@a.example"
        assert pi.detect_id_for_root(repo, common_dir(repo)).project_id == "a.example/o/a"


class TestHomeRelativeSpellingsFollowGitsParse:
    """Differential against git's own parse: a fake ssh records the path git
    sends (`git-upload-pack '<path>'`) for each spelling; nothing is contacted.

    On a plain sshd, a sent `~/P` and a sent `P` are one directory (the
    login's home). A sent `/P` also shares the id by the accepted rule that
    relative and absolute scp paths are one id. A sent `~user/P` is another
    home and must keep its own id. An scp path is sent literally, so scp
    `%7E/...` is a directory named `%7E`, not the home."""

    SPELLINGS = (
        "git@host:srv/repo",
        "git@host:~/srv/repo",
        "git@host:/srv/repo",
        "ssh://git@host/srv/repo",
        "ssh://git@host/~/srv/repo",
        "git+ssh://git@host/~/srv/repo",
        "ssh+git://git@host/~/srv/repo",
        "ssh://git@host/%7E/srv/repo",
        "git@host:%7E/srv/repo",
        "ssh://git@host/%257E/srv/repo",
        "ssh://git@host/~%2Fsrv/repo",
        "ssh://git@host/srv%2Frepo",
        "git@host:~user/srv/repo",
        "ssh://git@host/~user/srv/repo",
    )

    @staticmethod
    def _sent_paths(tmp_path):
        import subprocess

        log = tmp_path / "ssh.log"
        fake_ssh = tmp_path / "fake-ssh"
        fake_ssh.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', encoding="utf-8")
        fake_ssh.chmod(0o755)
        env = dict(os.environ, GIT_SSH_COMMAND=str(fake_ssh))
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
            env.pop(name, None)
        sent = {}
        for url in TestHomeRelativeSpellingsFollowGitsParse.SPELLINGS:
            log.write_text("", encoding="utf-8")
            subprocess.run(["git", "ls-remote", url], capture_output=True,
                           timeout=30, env=env, cwd=tmp_path)
            text = log.read_text(encoding="utf-8")
            marker = "git-upload-pack '"
            assert marker in text, (url, text)
            sent[url] = text.split(marker, 1)[1].split("'", 1)[0]
        return sent

    @staticmethod
    def _home_key(sent_path: str) -> str:
        if sent_path.startswith("~/"):
            return sent_path[2:]
        return sent_path.lstrip("/")

    def test_spellings_git_sends_to_one_directory_share_one_id(self, tmp_path):
        assert_outside_repository(tmp_path)
        sent = self._sent_paths(tmp_path)
        ids = {url: pi.normalize_remote_url(url) for url in self.SPELLINGS}
        assert None not in ids.values(), ids

        for left in self.SPELLINGS:
            for right in self.SPELLINGS:
                same_directory = self._home_key(sent[left]) == self._home_key(sent[right])
                assert (ids[left] == ids[right]) is same_directory, (
                    left, sent[left], ids[left], right, sent[right], ids[right]
                )

    def test_the_two_homes_are_both_present_in_the_measurement(self, tmp_path):
        """Control: the differential above compares two real groups, so a
        parser that merged everything or split everything would red it."""
        assert_outside_repository(tmp_path)
        sent = self._sent_paths(tmp_path)

        keys = {self._home_key(path) for path in sent.values()}
        assert keys == {"srv/repo", "~user/srv/repo", "%7E/srv/repo"}, sent
        assert sent["ssh://git@host/~%2Fsrv/repo"] == "~/srv/repo", sent


class TestPercentEscapesFollowGitsParse:
    """git decodes an ssh url's whole path, `%2F` included, and sends an scp
    path literally. A fake ssh records what git sends; nothing is contacted.
    Spellings git sends to one directory share an id, and others do not."""

    SPELLINGS = (
        "git@host:srv/%41bc",
        "ssh://git@host/srv/%2541bc",
        "git@host:srv/Abc",
        "ssh://git@host/srv/%41bc",
        "ssh://git@host/srv%2FAbc",
        "git@host:caf\u00e9/r",
        "ssh://git@host/caf%C3%A9/r",
        "git@host:caf%C3%A9/r",
    )

    def test_spellings_git_sends_to_one_directory_share_one_id(self, tmp_path):
        import subprocess

        assert_outside_repository(tmp_path)
        log = tmp_path / "ssh.log"
        fake_ssh = tmp_path / "fake-ssh"
        fake_ssh.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', encoding="utf-8")
        fake_ssh.chmod(0o755)
        env = dict(os.environ, GIT_SSH_COMMAND=str(fake_ssh))
        sent = {}
        for url in self.SPELLINGS:
            log.write_text("", encoding="utf-8")
            subprocess.run(["git", "ls-remote", url], capture_output=True,
                           timeout=30, env=env, cwd=tmp_path)
            text = log.read_text(encoding="utf-8")
            sent[url] = text.split("git-upload-pack '", 1)[1].split("'", 1)[0].lstrip("/")
        ids = {url: pi.normalize_remote_url(url) for url in self.SPELLINGS}
        assert None not in ids.values(), ids
        # Four directories: srv/%41bc, srv/Abc, caf\u00e9/r and a literal caf%C3%A9/r.
        assert len(set(sent.values())) == 4, sent

        for left in self.SPELLINGS:
            for right in self.SPELLINGS:
                assert (ids[left] == ids[right]) is (sent[left] == sent[right]), (
                    left, sent[left], ids[left], right, sent[right], ids[right]
                )


class TestAzureDevOpsKeepsItsDecodedScpPath:
    """Azure DevOps Services decodes the scp path on its server, so its scp
    `%20` is the same project as the https and ssh url spellings' `%20`. The
    literal-`%` scp rule applies to every other host."""

    AZURE_ID = "dev.azure.com/fabrikam/My%20Project/_git/repo"

    @pytest.mark.parametrize(
        "url",
        [
            "git@ssh.dev.azure.com:v3/fabrikam/My%20Project/repo",
            "ssh://git@ssh.dev.azure.com/v3/fabrikam/My%20Project/repo",
            "ssh://git@ssh.dev.azure.com:22/v3/Fabrikam/My%20Project/repo",
            "https://dev.azure.com/fabrikam/My%20Project/_git/repo",
            "https://fabrikam.visualstudio.com/My%20Project/_git/repo",
            "fabrikam@vs-ssh.visualstudio.com:v3/fabrikam/My%20Project/repo",
        ],
    )
    def test_every_azure_spelling_keeps_one_id(self, url):
        assert pi.normalize_remote_url(url) == self.AZURE_ID

    def test_a_raw_space_still_declines(self):
        assert pi.classify_remote_origin(
            "git@ssh.dev.azure.com:v3/fabrikam/My Project/repo"
        ) == (DECLINE, None)

    def test_the_same_path_on_another_ssh_host_is_literal(self):
        assert pi.normalize_remote_url("git@other.example:v3/o/My%20Project/repo") == (
            "other.example/v3/o/My%2520Project/repo"
        )


# ---------------------------------------------------------------------------
# A symlinked `.git` is judged by the same rule as a gitfile
# ---------------------------------------------------------------------------

class TestASymlinkedGitMarkerIsJudgedLikeAGitfile:
    """A `.git` SYMLINK reaches the same targets a `gitdir:` file can, and git
    answers for the target either way. Every symlink route (absolute,
    relative, a chain) is refused where the gitfile route is refused, and
    resolves where it resolves."""

    VICTIM = "https://github.com/victim/checkout.git"
    VICTIM_ID = "github.com/victim/checkout"

    @staticmethod
    def _link(dotgit: Path, target: Path, route: str) -> None:
        if route == "absolute":
            dotgit.symlink_to(target)
        elif route == "relative":
            dotgit.symlink_to(os.path.relpath(target, dotgit.parent))
        else:
            hop = dotgit.parent.parent / (dotgit.parent.name + "-hop")
            hop.symlink_to(target)
            dotgit.symlink_to(hop)

    def _delivered(self, tmp_path, target: Path, route: str) -> Path:
        delivered = tmp_path / "dl" / "proj"
        delivered.mkdir(parents=True)
        self._link(delivered / ".git", target, route)
        return delivered

    def _assert_refused(self, memory, store, monkeypatch, victim_id):
        from scripts import memory_api
        from scripts.database import count_project_memories, db_connection
        from scripts.memory_api import PACTMemory, ProjectIdentityUnresolvedError

        expected = pi.decline_record("gitfile_redirect", None, "git root")
        assert memory.project_id is None
        assert memory._decline_reason == expected
        assert memory.get_status()["decline_reason"] == expected
        assert tuple(memory._read_project_scopes()) == ()
        monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
        monkeypatch.setattr(PACTMemory, "_store_embedding", lambda self, *a, **k: None)
        with pytest.raises(ProjectIdentityUnresolvedError):
            memory.save({"context": "from the delivered checkout"}, sync_to_claude=False)
        with db_connection(store) as conn:
            assert count_project_memories(conn, victim_id) == 1

    @pytest.mark.parametrize("route", ["absolute", "relative", "chain"])
    def test_a_link_to_another_checkouts_git_dir_is_refused(
        self, tmp_path, monkeypatch, route
    ):
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "work" / "proj", remote=self.VICTIM)
        delivered = self._delivered(tmp_path, victim / ".git", route)
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, self.VICTIM_ID)

        assert pi._gitfile_redirect_detail(delivered) is not None
        memory = _memory_in(monkeypatch, delivered, store)
        self._assert_refused(memory, store, monkeypatch, self.VICTIM_ID)

    @pytest.mark.parametrize("route", ["absolute", "relative", "chain"])
    def test_a_link_to_a_submodule_admin_dir_is_refused(
        self, tmp_path, monkeypatch, route
    ):
        superproject = _superproject_with_submodule(tmp_path)
        run_git("remote", "set-url", "origin", SUBMODULE_ORIGIN,
                cwd=superproject / "vendor" / "lib")
        delivered = self._delivered(
            tmp_path, superproject / ".git" / "modules" / "vendor" / "lib", route
        )
        store = tmp_path / "m.db"
        _seed_relative_bucket(store, SUBMODULE_ID)

        assert pi._gitfile_redirect_detail(delivered) is not None
        memory = _memory_in(monkeypatch, delivered, store)
        self._assert_refused(memory, store, monkeypatch, SUBMODULE_ID)

    def test_a_link_to_a_bare_repository_is_refused_by_git(self, tmp_path, monkeypatch):
        """git itself finds no working tree here, for the gitfile and the
        symlink alike; the arm pins that refusal."""
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "work" / "proj", remote=self.VICTIM)
        bare = tmp_path / "bare.git"
        run_git("clone", "-q", "--bare", str(victim), str(bare), cwd=tmp_path)
        delivered = self._delivered(tmp_path, bare, "absolute")

        assert pi._gitfile_redirect_detail(delivered) is None
        memory = _memory_in(monkeypatch, delivered, tmp_path / "m.db")
        assert memory.project_id is None
        assert memory._decline_reason["reason"] == "no_working_tree"

    def test_a_moved_git_dir_not_named_git_still_resolves(self, tmp_path, monkeypatch):
        """A git dir relocated under another name (the shape of a
        `--separate-git-dir` store or an Android `.repo/projects` entry) is
        the accepted external-git-dir case, for the symlink as for the
        gitfile: it binds its own origin."""
        assert_outside_repository(tmp_path)
        checkout = tmp_path / "proj"
        checkout.mkdir()
        (tmp_path / "store").mkdir()
        run_git("init", "-q", f"--separate-git-dir={tmp_path / 'store' / 'proj.gitdir'}",
                ".", cwd=checkout)
        run_git("remote", "add", "origin", "https://github.com/me/moved.git", cwd=checkout)
        (checkout / ".git").unlink()
        (checkout / ".git").symlink_to(tmp_path / "store" / "proj.gitdir")

        assert pi._gitfile_redirect_detail(checkout) is None
        assert _memory_in(monkeypatch, checkout, tmp_path / "m.db").project_id == (
            "github.com/me/moved"
        )

    def test_a_symlinked_parent_folder_is_the_same_checkout(self, tmp_path, monkeypatch):
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "work" / "proj", remote=self.VICTIM)
        alias = tmp_path / "alias"
        alias.symlink_to(victim)

        assert pi._gitfile_redirect_detail(alias) is None
        assert _memory_in(monkeypatch, alias, tmp_path / "m.db").project_id == self.VICTIM_ID


class TestTheRedirectCheckRefusesWhatItCannotCheck:
    """Each exit of the linked-worktree check whose evidence git did not
    give refuses, naming what was missing; only a git dir equal to the
    common dir, and a git that answers there is no toplevel, pass it on."""

    def _linked(self, tmp_path):
        main = make_repo(tmp_path / "main")
        worktree = tmp_path / "wt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        return worktree, (main / ".git").resolve()

    def test_no_git_dir_answer_names_no_git_dir(self, tmp_path, monkeypatch):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        real = memory_api._git_rev_parse
        monkeypatch.setattr(
            memory_api, "_git_rev_parse",
            lambda start, *args: None if "--git-dir" in args else real(start, *args),
        )
        assert memory_api._worktree_redirect_cause(worktree, common) == memory_api.REDIRECT_NO_GIT_DIR

    def test_a_git_dir_that_will_not_resolve_is_unresolvable(self, tmp_path):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        assert memory_api._worktree_redirect_cause(
            worktree, common, git_dir=Path("bad\0dir")
        ) == memory_api.REDIRECT_UNRESOLVABLE

    def test_a_toplevel_that_will_not_resolve_is_unresolvable(self, tmp_path):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        assert memory_api._worktree_redirect_cause(
            worktree, common, toplevel=Path("bad\0top")
        ) == memory_api.REDIRECT_UNRESOLVABLE

    def test_no_listing_is_unlistable(self, tmp_path, monkeypatch):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        monkeypatch.setattr(memory_api.project_identity, "listed_worktree_records", lambda *a, **k: None)
        assert memory_api._worktree_redirect_cause(worktree, common) == memory_api.REDIRECT_UNLISTABLE

    def test_a_listing_that_cannot_be_read_whole_is_named(self, tmp_path, monkeypatch):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        monkeypatch.setattr(
            memory_api.project_identity, "listed_worktree_records",
            lambda *a, **k: memory_api.project_identity.UNREADABLE_LISTING,
        )
        assert memory_api._worktree_redirect_cause(worktree, common) == (
            memory_api.REDIRECT_UNREADABLE_LISTING
        )

    def test_the_listed_worktree_passes_and_the_unlisted_one_is_named(self, tmp_path, monkeypatch):
        """Controls: the real worktree passes; the same worktree missing
        from the listing is unlisted, not unlistable."""
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        worktree, common = self._linked(tmp_path)
        assert memory_api._worktree_redirect_cause(worktree, common) is None
        monkeypatch.setattr(memory_api.project_identity, "listed_worktree_records", lambda *a, **k: [])
        assert memory_api._worktree_redirect_cause(worktree, common) == memory_api.REDIRECT_UNLISTED



# `.git` file texts, each naming the store as git might or might not read it.
_GITFILE_FORMS = {
    "plain": "gitdir: {store}\n",
    "no space": "gitdir:{store}\n",
    "tab": "gitdir:\t{store}\n",
    "two spaces": "gitdir:  {store}\n",
    "crlf and blank lines": "gitdir: {store}\r\n\n\r\n",
    "no newline": "gitdir: {store}",
    "upper case": "GITDIR: {store}\n",
    "nothing after the prefix": "gitdir: \n",
}


class TestAGitFileIsReadAsGitReadsIt:
    """`gitfile_target` accepts exactly the `.git` files git accepts, and
    names the git dir git names. git's own answer is the reference: each
    form is written as a checkout's `.git` and git is asked for the git
    dir."""

    @pytest.mark.parametrize("form", sorted(_GITFILE_FORMS))
    def test_each_form_agrees_with_git(self, tmp_path, form):
        assert_outside_repository(tmp_path)
        store = tmp_path / "store"
        run_git("init", "-q", "--bare", str(store), cwd=tmp_path)
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        # Two spaces name " <store>", a path relative to the checkout: a
        # store is made there too, so git's answer turns on the reading,
        # not on a missing directory.
        run_git("init", "-q", "--bare", str(checkout / f" {store}"), cwd=tmp_path)
        text = _GITFILE_FORMS[form].format(store=store)
        (checkout / ".git").write_text(text, encoding="utf-8", newline="")
        answer = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        )
        target = pi.gitfile_target(text)
        if answer.returncode != 0:
            assert target is None, (form, target, answer.stderr)
        else:
            assert target is not None, (form, answer.stdout)
            resolved = Path(target) if Path(target).is_absolute() else checkout / target
            assert os.path.realpath(resolved) == os.path.realpath(answer.stdout[:-1]), form

    def test_a_path_holding_a_newline_is_read_whole(self):
        assert pi.gitfile_target("gitdir: /a\nb/store\n") == "/a\nb/store"


class TestTheStoreReadsTheGitFileWhole:
    """database.py's own `.git` and admin `gitdir` readers take the whole
    file, so a path holding a newline is not cut, and a `.git` file git
    refuses names nothing."""

    def test_a_gitfile_naming_a_path_with_a_newline(self, tmp_path):
        from scripts import database

        store = tmp_path / "st\nore"
        store.mkdir()
        marker = tmp_path / ".git"
        marker.write_text(f"gitdir: {store}\n", encoding="utf-8")
        assert database._gitfile_target(str(marker)) == os.path.realpath(store)

    def test_a_gitfile_git_refuses_names_nothing(self, tmp_path):
        from scripts import database

        store = tmp_path / "store"
        store.mkdir()
        marker = tmp_path / ".git"
        marker.write_text(f"gitdir:{store}\n", encoding="utf-8")
        assert database._gitfile_target(str(marker)) is None

    def test_an_admin_gitdir_naming_a_root_with_a_newline(self, tmp_path):
        from scripts import database

        root = tmp_path / "ro\not"
        root.mkdir()
        common = tmp_path / "common"
        admin = common / "worktrees" / "wt"
        admin.mkdir(parents=True)
        (admin / "gitdir").write_text(f"{root}/.git\n", encoding="utf-8")
        assert database._worktree_admin_of(str(root), str(common), "wt") is True

    def test_a_gitfile_larger_than_git_reads_names_nothing(self, tmp_path):
        """git refuses a `.git` file larger than 1 MiB."""
        from scripts import database

        assert_outside_repository(tmp_path)
        store = tmp_path / "store"
        run_git("init", "-q", "--bare", str(store), cwd=tmp_path)
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        marker = checkout / ".git"
        marker.write_text(f"gitdir: {store}" + "\n" * (1 << 20), encoding="utf-8")
        answer = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--git-dir"],
            capture_output=True, text=True, env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        )
        assert answer.returncode != 0  # control: git refuses it
        assert database._gitfile_target(str(marker)) is None
        assert pi.gitfile_target(marker.read_text(encoding="utf-8")) is None


class TestTheOwnFileOriginBinds:
    """The first url from the repository's own config file binds; urls
    from included files are skipped wherever they sit."""

    def test_an_included_url_before_the_own_one_is_skipped(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        included = tmp_path / "included.cfg"
        included.write_text('[remote "origin"]\n\turl = git@included.example:o/inc.git\n', encoding="utf-8")
        run_git("config", "include.path", str(included), cwd=repo)
        run_git("config", "--add", "remote.origin.url", "git@own.example:o/own.git", cwd=repo)
        assert pi.read_origin(repo / ".git") == pi.OriginRead(pi.ORIGIN_PRESENT, "git@own.example:o/own.git")

    def test_the_first_of_two_own_urls_binds_past_an_include(self, tmp_path, monkeypatch):
        """Pairs as git answers them: included, own, own."""
        import subprocess

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        own_file = f"file:{repo / '.git' / 'config'}".encode()
        answer = (
            b"file:/elsewhere/inc\0https://h/o/inc.git\0"
            + own_file + b"\0https://h/o/first.git\0" + own_file + b"\0https://h/o/second.git\0"
        )
        monkeypatch.setattr(
            pi.subprocess, "run", lambda command, **_k: subprocess.CompletedProcess(command, 0, answer, b"")
        )
        assert pi.read_origin(repo / ".git") == pi.OriginRead(pi.ORIGIN_PRESENT, "https://h/o/first.git")


class TestTheSubmoduleCheckRefusesWhatItCannotCheck:
    """A submodule's folder name is chosen by its superproject, so a
    basename is refused there. Each leg the check cannot examine refuses
    too, rather than letting the basename through: one arm per refusing
    exit, each reaching its exit by faking that one failure."""

    def _submodule(self, tmp_path):
        superproject = _superproject_with_submodule(tmp_path)
        submodule = superproject / "vendor" / "lib"
        return submodule, common_dir(submodule)

    def test_the_real_submodule_declines_its_basename(self, tmp_path):
        """Control: with every leg examined, the basename is refused."""
        submodule, common = self._submodule(tmp_path)
        identity = pi.detect_id_for_root(submodule, common)
        assert identity.project_id is None and identity.decline_reason["reason"] == "submodule_checkout"

    def test_a_dot_git_that_cannot_be_examined_counts_as_a_submodule(self, tmp_path, monkeypatch):
        submodule, common = self._submodule(tmp_path)
        real = Path.is_file
        marker = submodule / ".git"

        def is_file(self):
            if self == marker:
                raise OSError("cannot examine")
            return real(self)

        monkeypatch.setattr(Path, "is_file", is_file)
        assert pi._submodule_checkout(submodule, common, submodule) is True

    def test_no_git_dir_answer_declines_the_basename(self, tmp_path, monkeypatch):
        submodule, common = self._submodule(tmp_path)
        monkeypatch.setattr(pi, "git_dir_answer", lambda start: None)
        identity = pi.detect_id_for_root(submodule, common)
        assert identity.project_id is None and identity.decline_reason["reason"] == "submodule_checkout"

    def test_a_git_dir_answer_that_will_not_resolve_is_no_git_dir(self, tmp_path, monkeypatch):
        """`_resolved_git_dir` gives None for an answer that will not
        resolve, so this reaches the same exit as no answer at all."""
        submodule, common = self._submodule(tmp_path)
        monkeypatch.setattr(pi, "git_dir_answer", lambda start: "bad\0dir")
        assert pi._resolved_git_dir(submodule) is None
        identity = pi.detect_id_for_root(submodule, common)
        assert identity.project_id is None and identity.decline_reason["reason"] == "submodule_checkout"

    def test_a_common_dir_that_will_not_resolve_counts_as_a_submodule(self, tmp_path, monkeypatch):
        submodule, _common = self._submodule(tmp_path)
        monkeypatch.setattr(pi, "_submodule_admin_dir", lambda path: True)
        monkeypatch.setattr(pi, "_resolved_git_dir", lambda asked: Path("/somewhere"))
        assert pi._submodule_checkout(submodule, Path("bad\0common"), submodule) is True

    def test_real_git_reaches_the_non_refusing_git_dir_exit(self, tmp_path):
        """The same exit from real git, with nothing patched. A hand-written
        `.git` file names an admin dir whose `commondir` is the submodule's,
        so git answers with the submodule's common dir but a git dir that is
        neither that admin dir nor one of its `worktrees/<name>`."""
        superproject = _superproject_with_submodule(tmp_path)
        module_dir = superproject / ".git" / "modules" / "vendor" / "lib"
        admin = tmp_path / "fake" / "admin"
        admin.mkdir(parents=True)
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (admin / "HEAD").write_bytes((module_dir / "HEAD").read_bytes())
        (admin / "commondir").write_text(f"{module_dir}\n", encoding="utf-8")
        (checkout / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        (admin / "gitdir").write_text(f"{checkout / '.git'}\n", encoding="utf-8")
        answer = Path(run_git("rev-parse", "--git-common-dir", cwd=checkout))
        common = (answer if answer.is_absolute() else checkout / answer).resolve()
        assert common == module_dir.resolve()  # control: git reports the submodule's common dir
        assert pi._submodule_admin_dir(common)  # control: leg 1 holds
        assert pi._submodule_checkout(checkout, common, checkout) is False

    def test_a_git_dir_that_is_neither_the_admin_dir_nor_its_worktree_keeps_the_basename(
        self, tmp_path, monkeypatch
    ):
        """The one non-refusing exit after legs 1 and 3 hold: git names a git
        dir that is neither the admin dir nor one of its `worktrees/<name>`,
        so the check answers False and the basename ruling continues."""
        submodule, common = self._submodule(tmp_path)
        elsewhere = tmp_path / "elsewhere" / "git"
        elsewhere.mkdir(parents=True)
        monkeypatch.setattr(pi, "_resolved_git_dir", lambda asked: elsewhere.resolve())
        assert pi._submodule_checkout(submodule, common, submodule) is False


class TestTheCaseProbe:
    """`filesystem_case_insensitive` answers True only on a positive answer;
    every undeterminable state answers False, the no-fold direction."""

    def test_the_probe_agrees_with_an_independent_case_lookup(self, tmp_path):
        """On a case-sensitive filesystem (Linux CI) this answers False; on a
        folding one (macOS default) True. The oracle is a plain lookup of the
        other spelling, not the probe."""
        folder = tmp_path / "proj"
        folder.mkdir()
        folds = (tmp_path / "PROJ").exists()
        assert pi.filesystem_case_insensitive(folder) is folds

    def test_the_same_entry_under_the_other_spelling_answers_true(self, tmp_path, monkeypatch):
        """What a folding filesystem answers: the toggled name's `lstat` IS the
        root's own entry. Faked by answering the toggled name's `lstat` with
        the root's, since a case-sensitive filesystem cannot give one entry
        two spellings (a planted symlink is a different entry: see
        TestCaseProbeRefusesAPlantedSymlink)."""
        folder = tmp_path / "proj"
        folder.mkdir()
        toggled = tmp_path / "PROJ"
        real_lstat = os.lstat
        monkeypatch.setattr(
            pi.os, "lstat",
            lambda path, *args, **kwargs: real_lstat(folder if Path(path) == toggled else path, *args, **kwargs),
        )
        assert pi.filesystem_case_insensitive(folder) is True

    @pytest.mark.parametrize("name", ["123", "-_."])
    def test_a_basename_with_no_cased_character_answers_false(self, tmp_path, name):
        folder = tmp_path / name
        folder.mkdir()
        assert pi.filesystem_case_insensitive(folder) is False

    def test_a_missing_path_answers_false(self, tmp_path):
        assert pi.filesystem_case_insensitive(tmp_path / "gone") is False

    def test_no_root_answers_false(self):
        assert pi.filesystem_case_insensitive(None) is False


# A sparse file this large takes no disk, and reading it whole would take
# this much memory.
_HUGE = 64 << 20


def _sparse(path: Path, head: str) -> Path:
    """`path` holding `head` and then zeros up to _HUGE bytes, sparse."""
    with open(path, "wb") as handle:
        handle.write(head.encode())
        handle.truncate(_HUGE)
    return path


def _peak_bytes(read) -> tuple:
    """(what `read()` returns, the most memory it held at once)."""
    import tracemalloc

    tracemalloc.start()
    try:
        answer = read()
        return answer, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


class TestAnOversizedGitFileIsNotReadWhole:
    """Every reader of a `.git` file or a worktree admin `gitdir` file reads
    at most git's 1 MiB limit and treats a longer file as naming nothing;
    a planted file far larger than the limit is never held in memory."""

    LIMIT = 8 << 20

    def test_the_marker_reader(self, tmp_path):
        assert_outside_repository(tmp_path)
        (tmp_path / "store").mkdir()
        (tmp_path / ".git").write_text(f"gitdir: {tmp_path / 'store'}\n")
        assert pi._marker_target(tmp_path) == (  # control: a normal file names it
            (tmp_path / "store").resolve(), tmp_path,
        )
        _sparse(tmp_path / ".git", f"gitdir: {tmp_path / 'store'}\n")
        answer, peak = _peak_bytes(lambda: pi._marker_target(tmp_path))
        assert answer is None and peak < self.LIMIT, (answer, peak)

    def test_the_stores_gitfile_reader(self, tmp_path):
        from scripts import database

        (tmp_path / "store").mkdir()
        (tmp_path / ".git").write_text(f"gitdir: {tmp_path / 'store'}\n")
        assert database._gitfile_target(str(tmp_path / ".git")) == os.path.realpath(
            tmp_path / "store"
        )  # control: a normal file names it
        marker = _sparse(tmp_path / ".git", f"gitdir: {tmp_path / 'store'}\n")
        answer, peak = _peak_bytes(lambda: database._gitfile_target(str(marker)))
        assert answer is None and peak < self.LIMIT, (answer, peak)

    def test_the_stores_admin_gitdir_reader(self, tmp_path):
        from scripts import database

        root = tmp_path / "root"
        root.mkdir()
        admin = tmp_path / "common" / "worktrees" / "wt"
        admin.mkdir(parents=True)
        (admin / "gitdir").write_text(f"{root}/.git\n")
        assert database._worktree_admin_of(  # control: a normal file names it back
            str(root), str(tmp_path / "common"), "wt"
        ) is True
        _sparse(admin / "gitdir", f"{root}/.git\n")
        answer, peak = _peak_bytes(
            lambda: database._worktree_admin_of(str(root), str(tmp_path / "common"), "wt")
        )
        assert answer is False and peak < self.LIMIT, (answer, peak)

    def _gate_roots(self, tmp_path, oversized: str):
        """The read gate's roots for a main checkout and one linked worktree
        whose `.git` file ("dotgit") or admin `gitdir` file ("admin") is
        oversized, with the memory the gate held. The listing is git's own,
        taken before the file is made oversized, so it names the worktree."""
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        common = (main / ".git").resolve()
        worktree = tmp_path / "wt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        listing = memory_api.project_identity.worktree_records(["--git-dir", str(common)])
        assert memory_api.project_identity.verified_worktree_roots(main, common, listing) == [
            main.resolve(), worktree.resolve(),
        ]  # control: the worktree is a gate root while its files are whole
        if oversized == "dotgit":
            _sparse(worktree / ".git", f"gitdir: {common / 'worktrees' / 'wt'}\n")
        else:
            _sparse(common / "worktrees" / "wt" / "gitdir", f"{worktree / '.git'}\n")
        return main, _peak_bytes(lambda: memory_api.project_identity.verified_worktree_roots(main, common, listing))

    def test_the_read_gates_dotgit_reader(self, tmp_path):
        main, (roots, peak) = self._gate_roots(tmp_path, "dotgit")
        assert roots == [main.resolve()] and peak < self.LIMIT, (roots, peak)

    def test_the_read_gates_admin_gitdir_reader(self, tmp_path):
        main, (roots, peak) = self._gate_roots(tmp_path, "admin")
        assert roots == [main.resolve()] and peak < self.LIMIT, (roots, peak)


# The plugin's import roots, for a reader run in a child process.
_IMPORT_ROOTS = [
    str(Path(__file__).resolve().parents[1] / "skills" / "pact-memory"),
    str(Path(__file__).resolve().parents[1] / "hooks"),
]


def _in_child(code: str, *args: str) -> str:
    """What `code` prints, run in a child python with the plugin importable
    and `args` as `sys.argv[1:]`. A read that blocks fails the test when the
    child is killed after 20 s, instead of hanging the run."""
    import sys

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(_IMPORT_ROOTS))
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import sys\n" + code, *args],
            capture_output=True, text=True, timeout=20, env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the read blocked: the child was killed after 20 s")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _wait_until_waiting_in_open(proc):
    """Return once `proc` waits in a FIFO open for a reader. Linux names that
    wait `wait_for_partner` in /proc; where /proc is absent, wait a second."""
    import time

    wchan = Path(f"/proc/{proc.pid}/wchan")
    if not wchan.exists():
        time.sleep(1)
        return
    deadline = time.monotonic() + 30
    while True:
        assert proc.poll() is None, "the writer exited before its open waited"
        if wchan.read_text() == "wait_for_partner":
            return
        assert time.monotonic() < deadline, "the writer never waited in its open"
        time.sleep(0.01)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
class TestAGitFileThatIsNotARegularFileNamesNothing:
    """A `.git` or an admin `gitdir` that is a FIFO, a link to one, or a
    directory is read as naming nothing, as git reads only a regular file
    there, and no reader waits on a FIFO. A link to a regular gitfile is
    still read through, as git reads it."""

    @staticmethod
    def _kinds(path: Path, kind: str) -> Path:
        if kind == "fifo":
            os.mkfifo(path)
        elif kind == "link":
            os.mkfifo(path.parent / (path.name + ".fifo"))
            path.symlink_to(path.parent / (path.name + ".fifo"))
        else:
            path.mkdir()
        return path

    @pytest.mark.parametrize("kind", ["fifo", "link", "dir"])
    def test_the_reader(self, tmp_path, kind):
        """In the same child a regular file is read first (control), so the
        reader is shown to read, not to answer None for anything."""
        regular = tmp_path / "regular"
        regular.write_text("gitdir: /x\n")
        marker = self._kinds(tmp_path / ".git", kind)
        assert _in_child(
            "from scripts import project_identity as pi\n"
            "print(repr(pi.read_git_file(sys.argv[1])))\n"
            "print(repr(pi.read_git_file(sys.argv[2])))", str(regular), str(marker)
        ).splitlines() == [repr("gitdir: /x\n"), "None"]

    def test_the_reader_reads_a_regular_file_and_a_link_to_one(self, tmp_path):
        (tmp_path / "real").write_text("gitdir: /x\n")
        (tmp_path / ".git").symlink_to(tmp_path / "real")
        for path in (tmp_path / "real", tmp_path / ".git"):
            assert pi.read_git_file(path) == "gitdir: /x\n"

    def test_the_reader_never_opens_a_fifo(self, tmp_path):
        """A writer waiting on the FIFO is released only when a reader opens
        it, so a writer still waiting after the read shows it was not opened.
        Control: the same reader reads a regular file whole."""
        import sys

        (tmp_path / "regular").write_text("gitdir: /x\n")
        assert pi.read_git_file(tmp_path / "regular") == "gitdir: /x\n"
        fifo = self._kinds(tmp_path / ".git", "fifo")
        writer = subprocess.Popen(
            [sys.executable, "-c", "import sys; open(sys.argv[1], 'wb')", str(fifo)]
        )
        try:
            _wait_until_waiting_in_open(writer)
            assert writer.poll() is None
            assert pi.read_git_file(fifo) is None
            with pytest.raises(subprocess.TimeoutExpired):
                writer.wait(timeout=1)
        finally:
            writer.kill()
            writer.wait()

    def test_a_fifo_put_in_place_after_the_type_check_is_not_read(self, tmp_path):
        """The type is checked again on the open descriptor, and the open does
        not wait: here the first check is shown a regular file and the path
        is a FIFO, as when one is swapped in between the two. Control, in the
        same child and under the same patched `stat`: the regular file is
        read whole."""
        regular = tmp_path / "regular"
        regular.write_text("gitdir: /x\n")
        fifo = self._kinds(tmp_path / ".git", "fifo")
        assert _in_child(
            "import os\nfrom scripts import project_identity as pi\n"
            "real_stat = os.stat\n"
            "os.stat = lambda p, *a, **k: real_stat(sys.argv[2] if str(p) == sys.argv[1] else p, *a, **k)\n"
            "print(repr(pi.read_git_file(sys.argv[2])))\n"
            "print(repr(pi.read_git_file(sys.argv[1])))", str(fifo), str(regular)
        ).splitlines() == [repr("gitdir: /x\n"), "None"]

    def test_an_override_file_swapped_for_a_fifo_is_refused(self, tmp_path):
        """The override reader checks the open descriptor too, and its open
        does not wait: its lstat is shown a regular file while the path is a
        FIFO, as when one is swapped in between the two. Control, in the same
        child: a regular override file in another common dir is read."""
        regular = tmp_path / "regular"
        regular.write_text("github.com/me/p\n")
        common = tmp_path / "common"
        common.mkdir()
        whole = tmp_path / "whole"
        whole.mkdir()
        (whole / "pact-project-id").write_text("github.com/me/p\n")
        fifo = self._kinds(common / "pact-project-id", "fifo")
        assert _in_child(
            "import os\nfrom pathlib import Path\nfrom scripts import project_identity as pi\n"
            "real_lstat = os.lstat\n"
            "os.lstat = lambda p, *a, **k: real_lstat(sys.argv[2] if str(p) == sys.argv[1] else p, *a, **k)\n"
            "control = pi.read_override(Path(sys.argv[4]))\n"
            "print(control.reason is None and control.value is not None)\n"
            "print(pi.read_override(Path(sys.argv[3])).reason)",
            str(fifo), str(regular), str(common), str(whole),
        ).splitlines() == ["True", "not_regular"]

    @pytest.mark.parametrize("kind", ["fifo", "link"])
    def test_the_stores_gitfile_reader(self, tmp_path, kind):
        """Control first, in the same child: a regular gitfile names its
        target."""
        (tmp_path / "regular").mkdir()
        (tmp_path / "regular" / ".git").write_text(f"gitdir: {tmp_path / 'store'}\n")
        marker = self._kinds(tmp_path / ".git", kind)
        assert _in_child(
            "from scripts import database\n"
            "print(database._gitfile_target(sys.argv[1]))\n"
            "print(database._gitfile_target(sys.argv[2]))",
            str(tmp_path / "regular" / ".git"), str(marker),
        ).splitlines() == [os.path.realpath(tmp_path / "store"), "None"]

    @pytest.mark.parametrize("kind", ["fifo", "link"])
    def test_the_stores_admin_gitdir_reader(self, tmp_path, kind):
        """Control first, in the same child: an admin dir whose regular
        `gitdir` names the root back is the root's."""
        root = tmp_path / "root"
        root.mkdir()
        whole = tmp_path / "common" / "worktrees" / "whole"
        whole.mkdir(parents=True)
        (whole / "gitdir").write_text(f"{root / '.git'}\n")
        admin = tmp_path / "common" / "worktrees" / "wt"
        admin.mkdir(parents=True)
        self._kinds(admin / "gitdir", kind)
        assert _in_child(
            "from scripts import database\n"
            "print(database._worktree_admin_of(sys.argv[1], sys.argv[2], 'whole'))\n"
            "print(database._worktree_admin_of(sys.argv[1], sys.argv[2], 'wt'))",
            str(root), str(tmp_path / "common"),
        ).splitlines() == ["True", "False"]

    @pytest.mark.parametrize("kind", ["fifo", "link"])
    def test_the_read_gates_admin_gitdir_reader(self, tmp_path, kind):
        """A listed worktree whose admin `gitdir` is not a regular file is not
        a gate root (the listing is git's own, taken while it was one)."""
        import json

        from scripts import memory_api

        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        common = (main / ".git").resolve()
        worktree = tmp_path / "wt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        listing = memory_api.project_identity.worktree_records(["--git-dir", str(common)])
        assert memory_api.project_identity.verified_worktree_roots(main, common, listing) == [
            main.resolve(), worktree.resolve(),
        ]  # control: the worktree is a gate root while its admin file is whole
        (common / "worktrees" / "wt" / "gitdir").unlink()
        self._kinds(common / "worktrees" / "wt" / "gitdir", kind)
        assert _in_child(
            "import json\nfrom pathlib import Path\nfrom scripts import memory_api\n"
            "main, common, listing = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
            "print(memory_api.project_identity.verified_worktree_roots(Path(main), Path(common), listing))",
            str(main), str(common), json.dumps(listing),
        ) == str([main.resolve()])

    @pytest.mark.parametrize("kind", [
        "none",
        "fifo",
        pytest.param("link", id="link-refused-by-the-islink-check-before-any-read"),
    ])
    def test_a_legacy_row_below_such_a_marker_does_not_hang_the_read(
        self, tmp_path, kind
    ):
        """The legacy gate reaches the nested `.git` of every row it checks;
        a row under a folder whose `.git` is a FIFO is refused, not waited
        on. A `.git` that is a link is refused by the gate's islink check
        before any read; without that check the reader would refuse it too,
        so that parameter fails only when both are gone, and it is a control
        for the pair, not a test of the reader. Each run first shows, in its
        own child, that a
        row under a regular `.git` file naming the root's own submodule git
        dir is read and kept, so the reader is shown to read."""
        assert_outside_repository(tmp_path)
        root = make_repo(tmp_path / "proj", remote="https://example.com/me/proj.git")
        sub = root / "vendor" / "pkg"
        sub.mkdir(parents=True)
        (sub / "a.py").write_text("x\n")
        assert self._legacy_row_kept(tmp_path / "control", "own-submodule-file") == "True"
        assert self._legacy_row_kept(tmp_path, kind, root, sub) == str(kind == "none")

    @staticmethod
    def _legacy_row_kept(tmp_path, kind, root=None, sub=None) -> str:
        """Whether a legacy row under `sub` stays in the read scope, with
        `sub/.git` made as `kind`, read in a child process."""
        if root is None:
            root = make_repo(tmp_path / "proj", remote="https://example.com/me/proj.git")
            sub = root / "vendor" / "pkg"
            sub.mkdir(parents=True)
            (sub / "a.py").write_text("x\n")
        out = _in_child(
            "import os\nfrom pathlib import Path\n"
            "from scripts import memory_api\nfrom scripts.memory_api import PACTMemory\n"
            "root, sub, db, kind = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]\n"
            "memory_api.get_project_dir_from_session_record = lambda: ''\n"
            "os.environ.pop('CLAUDE_PROJECT_DIR', None)\n"
            "os.chdir(root)\n"
            "legacy = PACTMemory._detect_project_identity_with_legacy().legacy_basename\n"
            "PACTMemory(project_id=legacy, db_path=db).save({'context': 'legacy', 'goal': 'g'},"
            " files=[str(sub / 'a.py')], sync_to_claude=False)\n"
            "if kind == 'fifo':\n    os.mkfifo(sub / '.git')\n"
            "elif kind == 'link':\n    os.mkfifo(sub / 'f')\n    (sub / '.git').symlink_to(sub / 'f')\n"
            "elif kind == 'own-submodule-file':\n"
            "    (sub / '.git').write_text('gitdir: ' + str((root / '.git' / 'modules' / 'pkg').resolve()) + chr(10))\n"
            "print(legacy in PACTMemory(db_path=db)._read_project_scopes())",
            str(root), str(sub), str(tmp_path / "m.db"), kind,
        )
        # "none": with no `.git` below, the row is this project's. A FIFO or
        # a link is refused, the direction a check that cannot read the
        # marker takes.
        return out.splitlines()[-1]

    def test_a_link_to_a_gitfile_naming_another_checkouts_git_dir_is_refused(
        self, tmp_path
    ):
        """The reader follows a link as git's `stat` does, so a `.git` link
        to a regular gitfile is read through and its redirect refused."""
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "work" / "proj", remote="https://github.com/victim/p.git")
        (tmp_path / "pointer").write_text(f"gitdir: {victim / '.git'}\n")
        delivered = tmp_path / "dl"
        delivered.mkdir()
        (delivered / ".git").symlink_to(tmp_path / "pointer")
        assert pi._gitfile_redirect_detail(delivered) is not None


class TestAnIncludeOnlyOriginDeclinesOnEveryBranch:
    """An origin set only by an included file is no stable identity: an
    `includeIf "onbranch:..."` include applies on one branch of the main
    checkout and not on another, so the id would follow the branch. Every
    route declines it, on the include's branch and after the main checkout
    leaves it, rather than binding the folder name there."""

    @pytest.mark.parametrize("switch_branch", [False, True])
    def test_every_route_declines(self, tmp_path, switch_branch):
        from fixtures.identity_routes import ROUTES, SHAPES, _clear

        assert_outside_repository(tmp_path)
        answers = {}
        for index, (route, (run, _sources)) in enumerate(ROUTES.items()):
            if "forced" in route:
                continue
            base = tmp_path / f"cell{index}"
            base.mkdir()
            target = SHAPES["origin_from_onbranch_include"](base)
            if switch_branch:
                run_git("checkout", "-q", "-b", "elsewhere", cwd=base / "main")
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            with pytest.MonkeyPatch.context() as cell:
                _clear(cell)
                detected = run(cell, target, elsewhere)
            answers[route] = (
                detected.project_id, (detected.decline_reason or {}).get("reason")
            )
        wrong = {
            route: answer for route, answer in answers.items()
            if answer != (None, "origin_unrepresentable")
        }
        assert wrong == {}, f"an include-only origin bound or misdeclined: {wrong}"

    @staticmethod
    def _read(repo: Path):
        from scripts import project_identity as pi

        return pi.read_origin((repo / ".git").resolve())

    def test_an_include_for_other_settings_and_no_origin_declines(self, tmp_path):
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        (repo / ".git" / "extra").write_text("[user]\n\tname = x\n", encoding="utf-8")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as config:
            config.write("[Include]\n\tpath = extra\n")
        assert self._read(repo).state == pi.ORIGIN_INCLUDE_ONLY

    def test_control_no_include_and_no_origin_is_absent(self, tmp_path):
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        assert self._read(make_repo(tmp_path / "proj")).state == pi.ORIGIN_ABSENT

    def test_control_a_repository_with_no_config_file_is_absent(self, tmp_path):
        """No config file holds no include, so no origin there is absent."""
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        (repo / ".git" / "config").unlink()
        assert self._read(repo).state == pi.ORIGIN_ABSENT

    def test_a_config_too_large_to_check_declines(self, tmp_path):
        """A config file larger than the bounded reader takes cannot be
        checked for includes, so no origin there is not taken as absent."""
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as config:
            config.write(("# padding\n" * ((pi.GIT_FILE_LIMIT // 10) + 1)))
        assert self._read(repo).state == pi.ORIGIN_CONFIG_UNREAD


# The open flags Python defines on every platform ("available on Unix and
# Windows" in the `os.open` flag documentation). Every other `os.O_*` flag
# is missing on some platform the module runs on.
_UNIVERSAL_OPEN_FLAGS = frozenset(
    {"O_RDONLY", "O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_EXCL", "O_TRUNC"}
)


@pytest.fixture
def without_optional_open_flags(monkeypatch):
    """`os` with every open flag outside `_UNIVERSAL_OPEN_FLAGS` deleted, so
    a reader that names any platform-specific flag directly raises."""
    optional = [name for name in dir(os) if name.startswith("O_")]
    optional = [name for name in optional if name not in _UNIVERSAL_OPEN_FLAGS]
    assert optional, "control: this platform defines no optional open flag to delete"
    for name in optional:
        monkeypatch.delattr(os, name)
    assert sorted(n for n in dir(os) if n.startswith("O_")) == sorted(_UNIVERSAL_OPEN_FLAGS)


class TestEveryReaderOpensWithoutAnOptionalFlag:
    """Each guarded open takes a flag a platform may lack through getattr, so
    on such a platform the regular file is still read, not refused or raised
    on."""

    def test_the_git_file_reader(self, tmp_path, without_optional_open_flags):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        assert pi.read_git_file(tmp_path / ".git") == "gitdir: /x\n"

    def test_the_override_reader(self, tmp_path, without_optional_open_flags):
        (tmp_path / "pact-project-id").write_text("github.com/me/p\n")
        read = pi.read_override(tmp_path)
        assert (read.value, read.reason) == ("github.com/me/p", None)

    def test_the_user_setting_reader(self, tmp_path, without_optional_open_flags):
        (tmp_path / "config.json").write_text('{"project_id": "github.com/me/p"}')
        assert pi.read_user_setting(tmp_path / "config.json") == "github.com/me/p"

    def test_the_prune_ledger_reader(self, tmp_path, without_optional_open_flags):
        from scripts import memory_api

        ledger = tmp_path / "ledger"
        ledger.write_text('{"session_id": "s"}\n')
        assert memory_api._pruned_session_records(ledger) == [{"session_id": "s"}]

    def test_the_prune_ledger_writer(self, tmp_path, without_optional_open_flags):
        import session_end

        session_end._append_ledger_line(tmp_path, '{"session_id": "s"}\n')
        assert (tmp_path / session_end.PRUNE_LEDGER_NAME).read_text() == (
            '{"session_id": "s"}\n'
        )


class _OpenFlagReads(ast.NodeVisitor):
    """Every read of an `os.O_*` flag in one module, as (scope, at_import,
    flag, form, line). A default argument, a decorator and a class body run
    in the scope around them, so at import time when that scope is the
    module. `form` is "attribute" (`.O_X` on any object, `os` or not),
    "subscript" (`[...]["O_X"]`), "from-import" (`from os import O_X`),
    "getattr-default" (`getattr(..., "O_X", d)`), "getattr" (no default) or
    "getattr-computed" (a name built at run time, on `os` or a name bound
    to it)."""

    def __init__(self):
        self.scope = []
        self.os_names = {"os"}
        self.reads = []
        self.places = set()

    def _record(self, flag, form, node):
        self.places.add((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
        self.reads.append((
            ".".join(name for _kind, name in self.scope) or "<module>",
            all(kind == "class" for kind, _name in self.scope),
            flag, form, node.lineno,
        ))

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name == "os" and alias.asname:
                self.os_names.add(alias.asname)

    def visit_ImportFrom(self, node):
        if node.module == "os":
            for alias in node.names:
                if alias.name.startswith("O_") or alias.name == "*":
                    self._record(alias.name, "from-import", node)

    def _bind(self, targets, value):
        """A name bound to `os` (`_os = os`, `_os: Any = os`, `(_os := os)`)
        reads flags as `os` does."""
        if isinstance(value, ast.Name) and value.id in self.os_names:
            self.os_names.update(t.id for t in targets if isinstance(t, ast.Name))

    def visit_Assign(self, node):
        self._bind(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._bind([node.target], node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self._bind([node.target], node.value)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Any `.O_*` attribute, on whatever object: a flag read through a
        # name this visitor does not know is `os` is still a flag read.
        if node.attr.startswith("O_"):
            self._record(node.attr, "attribute", node)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        # `vars(os)["O_X"]` and the like read a flag by its name.
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.startswith("O_"):
            self._record(key.value, "subscript", node)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            name = node.args[1]
            on_os = isinstance(node.args[0], ast.Name) and node.args[0].id in self.os_names
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                if on_os:
                    self._record(None, "getattr-computed", node)
            elif name.value.startswith("O_"):
                form = "getattr-default" if len(node.args) == 3 else "getattr"
                self._record(name.value, form, node)
        self.generic_visit(node)

    def _enter(self, node, kind, name, body):
        for decorator in getattr(node, "decorator_list", []):
            self.visit(decorator)
        self.visit(node.args)
        if getattr(node, "returns", None) is not None:
            self.visit(node.returns)
        self.scope.append((kind, name))
        for statement in body:
            self.visit(statement)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._enter(node, "function", node.name, node.body)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._enter(node, "function", "<lambda>", [node.body])

    def visit_ClassDef(self, node):
        for expression in [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)]:
            self.visit(expression)
        self.scope.append(("class", node.name))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()


def _platform_claim_modules() -> dict:
    """The importable name and file of each module whose open-flag readers
    promise to run where an optional flag is missing."""
    from scripts import memory_api
    from shared import git_helpers
    import session_end

    return {
        "shared.git_helpers": Path(git_helpers.__file__),
        "scripts.project_identity": Path(pi.__file__),
        "scripts.memory_api": Path(memory_api.__file__),
        "session_end": Path(session_end.__file__),
    }


# Every function in those modules that reads an optional flag, and the test
# above that runs it with every optional flag deleted.
_RUN_WITHOUT_OPTIONAL_FLAGS = {
    ("scripts.project_identity", "read_override"):
        TestEveryReaderOpensWithoutAnOptionalFlag.test_the_override_reader,
    ("scripts.project_identity", "read_user_setting"):
        TestEveryReaderOpensWithoutAnOptionalFlag.test_the_user_setting_reader,
    ("shared.git_helpers", "read_git_file"):
        TestEveryReaderOpensWithoutAnOptionalFlag.test_the_git_file_reader,
    ("scripts.memory_api", "_pruned_session_records"):
        TestEveryReaderOpensWithoutAnOptionalFlag.test_the_prune_ledger_reader,
    ("session_end", "_open_locked_ledger"):
        TestEveryReaderOpensWithoutAnOptionalFlag.test_the_prune_ledger_writer,
}

# Run in a child: import the modules once with every flag, drop only those
# modules, delete every optional flag, and import them again. Their own
# import-time code then runs without the flags, and nothing else does.
_IMPORT_WITHOUT_OPTIONAL_FLAGS = r"""
import importlib, os, sys
names = sys.argv[1].split(",")
universal = set(sys.argv[2].split(","))
files = {os.path.realpath(path) for path in sys.argv[3:]}
for name in names:
    importlib.import_module(name)
for name, module in list(sys.modules.items()):
    if os.path.realpath(getattr(module, "__file__", None) or "-") in files:
        del sys.modules[name]
optional = [flag for flag in dir(os) if flag.startswith("O_") and flag not in universal]
assert optional, "no optional flag to delete"
for flag in optional:
    delattr(os, flag)
for name in names:
    importlib.import_module(name)
print("imported without", len(optional), "flags")
"""


class TestEveryOpenFlagReadInTheClaimingModulesIsPortable:
    """project_identity.py, memory_api.py and session_end.py promise that
    their open-flag readers run on a platform lacking an optional flag. Their
    every `os.O_*` read is found in the parse, module-level constants and
    default arguments included, and checked three ways: no read names an
    optional flag directly, each function reading one runs with every
    optional flag deleted, and each module imports with them deleted.

    The population is those three modules. state_file.py, working_memory.py,
    session_resume.py, the merge_guard hooks and session_init.py read
    optional flags directly, but they make no such promise and their readers
    are not changed alongside these, so they sit outside this census, not
    exempt from it."""

    @staticmethod
    def _reads() -> dict:
        found = {}
        for name, path in _platform_claim_modules().items():
            visitor = _OpenFlagReads()
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            found[name] = visitor.reads
        return found

    def test_no_read_names_an_optional_flag_directly(self):
        reads = self._reads()
        # control: each module's known guarded read is found
        assert all(any(r[3] == "getattr-default" for r in rs) for rs in reads.values()), reads
        direct = [
            (name, *read) for name, rs in reads.items() for read in rs
            if read[3] != "getattr-default"
            and not (read[3] == "attribute" and read[2] in _UNIVERSAL_OPEN_FLAGS)
        ]
        assert direct == [], direct

    @staticmethod
    def _read_outright():
        """Whether a node of a claiming module is a flag read this census
        records (`_OpenFlagReads.places`)."""
        places = {}
        for path in _platform_claim_modules().values():
            visitor = _OpenFlagReads()
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            places[path] = visitor.places

        def read_outright(path, node):
            return node is not None and (
                getattr(node, "lineno", None), getattr(node, "col_offset", None),
                getattr(node, "end_lineno", None), getattr(node, "end_col_offset", None),
            ) in places[path]
        return read_outright

    @staticmethod
    def _unread_reaches() -> tuple:
        """(forms, every use of one in the claiming modules, each place they
        can reach an `os.O_*` flag through a form this census does not
        read)."""
        from fixtures.code_readers import dynamic_access_forms, dynamic_reaches

        paths = list(_platform_claim_modules().values())
        read_outright = TestEveryOpenFlagReadInTheClaimingModulesIsPortable._read_outright()

        def reaches_flag(kind, value):
            if kind == "attribute":
                return value.startswith("O_")
            return kind == "code" and "O_" in value

        forms = dynamic_access_forms(paths)
        every_use = dynamic_reaches(paths, forms, lambda kind, value: True)
        return forms, every_use, dynamic_reaches(paths, forms, reaches_flag, read_outright)

    def test_no_flag_is_reached_through_an_unread_form(self):
        """FAIL CLOSED: a flag name handed to `hasattr`, `__getattribute__` or
        `eval`, a computed name on an object not known to be `os`, or a
        namespace (`vars(os)`, `os.__dict__`) is a read this census cannot
        see."""
        _forms, _every_use, unread = self._unread_reaches()
        assert unread == [], unread

    def test_the_unread_form_ban_sees_the_modules_and_spares_census_reads(self):
        """Control: the table names the forms, the guarded `getattr(os,
        "O_X", 0)` reads are among the uses found, and they and the
        `getattr` reads of other names are not counted as unread."""
        forms, every_use, unread = self._unread_reaches()
        assert {"builtins.getattr", "builtins.vars", ".__dict__", ".__getattribute__"} <= set(forms)
        guarded = [use for use in every_use if use[2].startswith("getattr(os, 'O_")]
        other = [use for use in every_use if use[2].startswith("getattr(") and "'O_" not in use[2]]
        assert {use[0] for use in guarded} == {"git_helpers.py", "project_identity.py", "memory_api.py", "session_end.py"}
        assert other, every_use
        assert not set(guarded + other) & set(unread)

    @staticmethod
    def _flag_strings(read_outright) -> list:
        from fixtures.code_readers import named_anchor_strings

        paths = list(_platform_claim_modules().values())
        return named_anchor_strings(paths, re.compile(r"O_[A-Z0-9_]+").fullmatch, read_outright)

    def test_no_string_names_a_flag_outside_a_census_read(self):
        """FAIL CLOSED: a string naming an `os.O_*` flag can reach it through
        a callable the form table does not list
        (`operator.methodcaller("__getattribute__", "O_X")`,
        `pkgutil.resolve_name("os:O_X")`) unless this census reads it."""
        assert self._flag_strings(self._read_outright()) == []

    def test_no_call_in_the_claiming_modules_has_a_call_result_as_its_callee(self):
        """FAIL CLOSED: a callee another call returned
        (`operator.methodcaller("__getattribute__", name)(os)`) can reach a
        flag by a name built at run time, which no census reads."""
        from fixtures.code_readers import call_result_callees

        assert call_result_callees(list(_platform_claim_modules().values())) == []

    def test_the_callee_reader_reads_every_binding_in_the_claiming_modules(self):
        from fixtures.code_readers import unread_bindings

        assert unread_bindings(list(_platform_claim_modules().values())) == []

    def test_the_flag_string_ban_spares_only_the_census_reads(self):
        """Control: with no read spared, the guarded `getattr(os, "O_X", 0)`
        names are found in each module, so the ban sees them."""
        found = self._flag_strings(lambda path, parent: False)
        assert {hit[0] for hit in found} == {"git_helpers.py", "project_identity.py", "memory_api.py", "session_end.py"}, found

    @pytest.mark.parametrize("source, named", [
        ("VALUE = 'O_NOFOLLOW'\n", True),
        ("VALUE = 'os.O_NOFOLLOW'\n", True),
        ("VALUE = 'os:O_NOFOLLOW'\n", True),
        ("VALUE = '{0.O_NOFOLLOW}'\n", False),
        ("VALUE = b'O_NOFOLLOW'\n", False),
        ("VALUE = 'O_NOFOLLOW: missing here'\n", False),
        ("VALUE = 'os.O_NOFOLLOW is read'\n", False),
        ("VALUE = 'Opens the file with os.O_NOFOLLOW'\n", False),
        ("VALUE = '.O_NOFOLLOW'\n", True),
        ("VALUE = 'the O_NOFOLLOW flag is optional'\n", False),
        ("VALUE = 'NO_O_NOFOLLOW'\n", False),
        ("VALUE = getattr(os, 'O_NOFOLLOW', 0)\n", True),
    ])
    def test_a_string_names_a_flag_only_as_a_whole_part(self, tmp_path, source, named):
        from fixtures.code_readers import named_anchor_strings

        path = tmp_path / "scope.py"
        path.write_text(source, encoding="utf-8")
        found = named_anchor_strings([path], re.compile(r"O_[A-Z0-9_]+").fullmatch)
        assert bool(found) is named, found

    def test_every_function_reading_an_optional_flag_runs_without_it(self):
        readers = {
            (name, read[0]) for name, rs in self._reads().items() for read in rs
            if not read[1] and read[2] not in _UNIVERSAL_OPEN_FLAGS
        }
        assert readers == set(_RUN_WITHOUT_OPTIONAL_FLAGS), sorted(
            readers ^ set(_RUN_WITHOUT_OPTIONAL_FLAGS)
        )

    def test_each_module_imports_without_an_optional_flag(self):
        modules = _platform_claim_modules()
        result = subprocess.run(
            [
                sys.executable, "-c", _IMPORT_WITHOUT_OPTIONAL_FLAGS,
                ",".join(modules), ",".join(sorted(_UNIVERSAL_OPEN_FLAGS)),
                *map(str, modules.values()),
            ],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p)),
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.startswith("imported without"), result.stdout


class TestAnOverrideFileSwappedForALinkIsRefused:
    """The override open does not follow a link: a symlink put in place
    after the lstat, here by showing the lstat a regular file while the path
    is a link to a file holding a canonical id, is unreadable, not bound."""

    def test_the_link_is_not_followed(self, tmp_path, monkeypatch):
        regular = tmp_path / "regular"
        regular.write_text("github.com/me/regular\n")
        target = tmp_path / "linked"
        target.write_text("github.com/linked/by-override\n")
        common = tmp_path / "common"
        common.mkdir()
        (common / "pact-project-id").symlink_to(target)
        real_lstat = os.lstat
        monkeypatch.setattr(
            pi.os, "lstat",
            lambda p, *a, **k: real_lstat(
                regular if str(p) == str(common / "pact-project-id") else p, *a, **k
            ),
        )
        read = pi.read_override(common)
        assert (read.value, read.reason) == (None, "unreadable"), read

    def test_control_a_regular_override_binds(self, tmp_path):
        (tmp_path / "pact-project-id").write_text("github.com/me/regular\n")
        assert pi.read_override(tmp_path).value == "github.com/me/regular"


class TestEveryOriginStateIsDocumented:
    """`OriginRead`'s docstring names each origin state the module defines,
    and `read_origin`'s names each state the no-origin answer can become."""

    @staticmethod
    def _states():
        return {
            getattr(pi, name) for name in dir(pi)
            if name.startswith("ORIGIN_") and name.isupper()
        }

    def test_the_state_list_names_every_state(self):
        doc = pi.OriginRead.__doc__
        missing = sorted(s for s in self._states() if f"`{s}`" not in doc)
        assert missing == [], missing

    def test_read_origin_names_every_no_origin_state(self):
        doc = " ".join(pi.read_origin.__doc__.split())
        for state in (pi.ORIGIN_ABSENT, pi.ORIGIN_INCLUDE_ONLY, pi.ORIGIN_CONFIG_UNREAD):
            assert state.upper() in doc, state


# Config section headers, each written into a repository's own config with
# `path = inc` (the file `inc` sets an origin). `{repo}` is the checkout.
_HEADER_FORMS = {
    "plain": "[include]\n\tpath = inc\n",
    "upper case": "[INCLUDE]\n\tpath = inc\n",
    "leading space": " [include]\n\tpath = inc\n",
    "leading tab": "\t[include]\n\tpath = inc\n",
    "leading carriage return": "\r[include]\n\tpath = inc\n",
    "space after the bracket": "[ include]\n\tpath = inc\n",
    "space before the bracket": "[include ]\n\tpath = inc\n",
    "form feed before": "\f[include]\n\tpath = inc\n",
    "vertical tab before": "\v[include]\n\tpath = inc\n",
    "key on the same line": "[include] path = inc\n",
    "after another header": "[gc] [include]\n\tpath = inc\n",
    "directly after another header": "[gc][include]\n\tpath = inc\n",
    "after another header and a tab": "[gc]\t[include]\n\tpath = inc\n",
    "CRLF lines": "[include]\r\n\tpath = inc\r\n",
    "includeIf": '[includeIf "gitdir:{repo}/"]\n\tpath = inc\n',
    "includeIf after a tab": '[includeIf\t"gitdir:{repo}/"]\n\tpath = inc\n',
    "includeIf after two spaces": '[includeIf  "gitdir:{repo}/"]\n\tpath = inc\n',
    "INCLUDEIF": '[INCLUDEIF "gitdir:{repo}/"]\n\tpath = inc\n',
    "dotted subsection": "[include.x]\n\tpath = inc\n",
    "quoted subsection": '[include "x"]\n\tpath = inc\n',
    "BOM at the start of the file": None,
    "another section": "[core]\n\tpath = inc\n",
    "a comment": "# [include]\n",
    "a longer name": "[includes]\n\tpath = inc\n",
    "a value": "[core]\n\tx = [include]\n",
}
# Headers chained on one line: two to four of them, `include` last, joined
# by each ASCII character in turn, so git decides which separators it reads
# a chain across. Braces are doubled for the `format` the arm applies.
_HEADER_FORMS.update({
    f"{count} headers joined by {ord(separator):#04x}": (
        separator.join(["[gc]", "[x]", "[y]"][: count - 1] + ["[include]"])
        .replace("{", "{{").replace("}", "}}")
        + "\n\tpath = inc\n"
    )
    for count in (2, 3, 4)
    for separator in map(chr, range(1, 128))
})
# A run of two and of three of each ASCII character between two headers,
# so a scan that steps over only one character before the next header is
# tried wherever git reads on.
_HEADER_FORMS.update({
    f"{length} of {ord(separator):#04x} between headers": (
        "[gc]" + separator * length + "[include]"
    ).replace("{", "{{").replace("}", "}}") + "\n\tpath = inc\n"
    for length in (2, 3)
    for separator in map(chr, range(1, 128))
})
# Each ASCII character between a section name and its quoted subsection,
# whose `]` inside the quotes hides the end of the header from a naive
# scan, then an include header.
_HEADER_FORMS.update({
    f"{ord(separator):#04x} before a quoted subsection": (
        "[remote" + separator + '"a]b"][include]'
    ).replace("{", "{{").replace("}", "}}") + "\n\tpath = inc\n"
    for separator in map(chr, range(1, 128))
})
# Longer runs of each ASCII character between two headers and between a
# section name and its quoted subsection, so a scan that steps over any
# fixed number of characters short of the run misses what git reads on.
_HEADER_FORMS.update({
    f"{length} of {ord(separator):#04x} {where}": (
        form.format(run=separator * length).replace("{", "{{").replace("}", "}}") + "\n\tpath = inc\n"
    )
    for length in (4, 100)
    for separator in map(chr, range(1, 128))
    for where, form in (
        ("between headers", "[gc]{run}[include]"),
        ("before a quoted subsection", '[remote{run}"a]b"][include]'),
    )
})
# Each ASCII character escaped by a backslash inside a quoted subsection,
# before a `]` and the closing quote, and directly before the closing quote,
# then an include header: git takes the escaped character as it is, so an
# escaped quote does not end the quotes and the next character is read.
_HEADER_FORMS.update({
    f"{ord(escaped):#04x} escaped {where} in a quoted subsection": (
        '[remote "a\\' + escaped + after + '][include]'
    ).replace("{", "{{").replace("}", "}}") + "\n\tpath = inc\n"
    for escaped in map(chr, range(1, 128))
    for where, after in (("before a bracket", ']b"'), ("before the closing quote", '"'))
})


def _scan_disagreement(repo, body):
    """None when the include scan agrees with git's own parse of `repo`'s
    config with `body` appended (`path = inc` naming a file that sets an
    origin; None puts an include after a BOM at the start instead), else
    (git's answer, the scan's). Where git refuses the file, the origin read
    must be unavailable."""
    git_dir = (repo / ".git").resolve()
    (git_dir / "inc").write_text('[remote "origin"]\n\turl = https://github.com/me/inc.git\n')
    config = git_dir / "config"
    original = config.read_text()
    if body is None:
        config.write_bytes(("\ufeff[include]\n\tpath = inc\n" + original).encode())
    else:
        config.write_bytes((original + body).encode())
    try:
        listed = subprocess.run(
            ["git", "config", "--file", str(config), "--list", "--name-only"],
            capture_output=True, text=True,
        )
        if listed.returncode != 0:
            state = pi.read_origin(git_dir).state
            return None if state == pi.ORIGIN_UNAVAILABLE else ("refused", state)
        git_says = any(
            name.split(".")[0] in ("include", "includeif") for name in listed.stdout.split()
        )
        scan = pi._holds_include_section(pi.read_git_file(config))
        return None if scan == git_says else (git_says, scan)
    finally:
        config.write_text(original)


@pytest.fixture(scope="module")
def header_joints(tmp_path_factory):
    """Every ASCII character git reads between two section headers and then
    still reads the second (`[gc]` + c + `[include]`), asked of git."""
    folder = tmp_path_factory.mktemp("joints")
    config = folder / "config"
    joints = []
    for code in range(1, 128):
        config.write_bytes(("[gc]" + chr(code) + "[include]\n\tpath = inc\n").encode())
        listed = subprocess.run(
            ["git", "config", "--file", str(config), "--list", "--name-only"],
            capture_output=True, text=True,
        )
        if listed.returncode == 0 and "include.path" in listed.stdout.split():
            joints.append(chr(code))
    return joints


# The longest header chain asked of git: a scan that stops after any
# number of headers up to it misses the include at the end of a chain.
_LONGEST_CHAIN = 1024


class TestTheIncludeScanReadsEveryJointAndChainAsGitDoes:
    """Every sequence of one to three characters git reads between two
    headers, and every chain of two to `_LONGEST_CHAIN` headers joined
    directly, `include` last: the scan agrees with git on each. The joints
    are asked of git (`header_joints`), not listed here."""

    def test_control_git_reads_some_joint_and_the_table_is_its_full_product(self, header_joints):
        assert header_joints
        assert len(_joint_bodies(header_joints)) == sum(len(header_joints) ** n for n in (1, 2, 3))

    def test_every_joint_of_up_to_three_characters(self, tmp_path, header_joints):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        wrong = {
            joint: found for joint, body in _joint_bodies(header_joints).items()
            if (found := _scan_disagreement(repo, body)) is not None
        }
        assert wrong == {}, wrong

    def test_every_chain_up_to_the_longest(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        wrong = {}
        for count in range(2, _LONGEST_CHAIN + 1):
            body = "".join([f"[s{index}]" for index in range(count - 1)] + ["[include]"]) + "\n\tpath = inc\n"
            found = _scan_disagreement(repo, body)
            if found is not None:
                wrong[count] = found
        assert wrong == {}, wrong


def _joint_bodies(joints) -> dict:
    """{joint: config body} for every sequence of one to three `joints`
    between a `[gc]` header and an include header."""
    return {
        "".join(joint): "[gc]" + "".join(joint) + "[include]\n\tpath = inc\n"
        for length in (1, 2, 3) for joint in itertools.product(joints, repeat=length)
    }


@pytest.fixture(scope="module")
def built_repo(tmp_path_factory):
    """One `make_repo` repository, which each header form copies to its own
    folder: a main checkout's `.git` names no path outside itself, so the
    copy is the same repository at a new path, and no form reads another's
    config or any answer cached for another's path."""
    folder = tmp_path_factory.mktemp("built")
    assert_outside_repository(folder)
    return make_repo(folder / "proj")


class TestTheIncludeScanReadsAHeaderAsGitDoes:
    """For each header form, git's own parse decides the answer. Where git
    reads the file, the scan finds an include section exactly when git lists
    a key in an `include` or `includeIf` section. Where git refuses the
    file, the origin read is unavailable, so it declines whatever the scan
    says."""

    @pytest.mark.parametrize("form", list(_HEADER_FORMS))
    def test_the_scan_agrees_with_git(self, tmp_path, form, built_repo):
        assert_outside_repository(tmp_path)
        repo = Path(shutil.copytree(built_repo, tmp_path / "proj", symlinks=True))
        body = _HEADER_FORMS[form]
        assert _scan_disagreement(repo, None if body is None else body.format(repo=repo.resolve())) is None

    def test_a_bom_before_an_onbranch_include_declines_off_the_branch(self, tmp_path):
        """The measured case: an origin set only through an `onbranch`
        include on the config's first line, after a BOM, with the main
        checkout on another branch."""
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "proj")
        git_dir = (repo / ".git").resolve()
        (git_dir / "inc").write_text('[remote "origin"]\n\turl = https://github.com/me/inc.git\n')
        branch = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
        config = git_dir / "config"
        config.write_bytes(
            (f'\ufeff[includeIf "onbranch:{branch}"]\n\tpath = inc\n' + config.read_text()).encode()
        )
        run_git("checkout", "-q", "-b", "elsewhere", cwd=repo)
        assert pi.read_origin(git_dir).state == pi.ORIGIN_INCLUDE_ONLY


class TestTheSubmoduleWorktreeCheckNamesItsCause:
    """`_admin_dir_refusal` answers None for a submodule git dir that records
    the asked checkout, and otherwise the detail of what it saw: an unset or
    different `core.worktree`, or one git could not read."""

    @staticmethod
    def _submodule(tmp_path):
        superproject = _superproject_with_submodule(tmp_path)
        checkout = superproject / "vendor" / "lib"
        return superproject / ".git" / "modules" / "vendor" / "lib", checkout

    def test_the_recorded_checkout_passes(self, tmp_path):
        admin, checkout = self._submodule(tmp_path)
        assert pi._admin_dir_refusal(admin, checkout) is None

    def test_another_checkout_is_named_as_such(self, tmp_path):
        admin, _checkout = self._submodule(tmp_path)
        (tmp_path / "elsewhere").mkdir()
        assert pi._admin_dir_refusal(admin, tmp_path / "elsewhere") == pi.FOREIGN_GIT_DIR_DETAIL

    def test_an_unset_core_worktree_accepts_the_checkout_git_names(self, tmp_path):
        """With `core.worktree` unset, the checkout git names as its top level
        is the admin dir's; another folder (no repository there) is not."""
        admin, checkout = self._submodule(tmp_path)
        run_git("--git-dir", str(admin), "config", "--unset", "core.worktree", cwd=tmp_path)
        assert pi._admin_dir_refusal(admin, checkout) is None
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert pi._admin_dir_refusal(admin, elsewhere) == pi.FOREIGN_GIT_DIR_DETAIL

    def test_a_core_worktree_git_cannot_read_is_named_as_unread(self, tmp_path, monkeypatch):
        admin, checkout = self._submodule(tmp_path)
        real = pi.subprocess.run

        def run(command, *args, **kwargs):
            if "core.worktree" in command:
                return subprocess.CompletedProcess(command, 128, b"", b"fatal: cannot read")
            return real(command, *args, **kwargs)

        monkeypatch.setattr(pi.subprocess, "run", run)
        assert pi._admin_dir_refusal(admin, checkout) == pi.CORE_WORKTREE_UNREAD_DETAIL

    @staticmethod
    def _caught_exceptions() -> list:
        """An instance of every exception class `_admin_dir_refusal` catches
        around its git call, read from its own `except` clauses."""
        import builtins
        import inspect

        tree = ast.parse(inspect.getsource(pi._admin_dir_refusal))
        tries = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Try) and any(
                isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "run"
                for statement in node.body for call in ast.walk(statement)
            )
        ]
        assert len(tries) == 1, tries  # control: the one try around the git call
        caught = []
        for handler in tries[0].handlers:
            if handler.type is None:
                continue
            names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            for name in names:
                owner = vars(pi) if isinstance(name, ast.Attribute) else vars(builtins)
                kind = (
                    getattr(owner[name.value.id], name.attr)
                    if isinstance(name, ast.Attribute) else owner[name.id]
                )
                caught.append(
                    kind(["git"], 5) if issubclass(kind, subprocess.SubprocessError) else kind("git")
                )
        return caught

    def test_every_exception_the_git_call_can_raise_is_named_as_unread(self, tmp_path, monkeypatch):
        """Each exception the `core.worktree` read catches, taken from the
        function's own `except` clauses, is named as unread, never as a
        recorded other checkout."""
        admin, checkout = self._submodule(tmp_path)
        real = pi.subprocess.run
        caught = self._caught_exceptions()
        assert any(isinstance(error, OSError) for error in caught), caught  # control
        wrong = {}
        for failure in caught:
            def run(command, *args, failure=failure, **kwargs):
                if "core.worktree" in command:
                    raise failure
                return real(command, *args, **kwargs)

            monkeypatch.setattr(pi.subprocess, "run", run)
            answer = pi._admin_dir_refusal(admin, checkout)
            if answer != pi.CORE_WORKTREE_UNREAD_DETAIL:
                wrong[type(failure).__name__] = answer
        assert wrong == {}, wrong

    def test_the_caught_set_covers_what_a_real_git_call_raises(self, tmp_path):
        """The arm above reads its population from the `except` clauses, so it
        cannot see a class dropped from them. This one makes real
        `subprocess.run` calls fail each way the git call can (an argument
        holding NUL, as a NUL-filled admin path gives; no such program; a
        program that cannot run; no answer in time) and requires every raised
        exception to be one the function catches."""
        caught = tuple(type(error) for error in self._caught_exceptions())
        (tmp_path / "not-a-program").mkdir()
        attempts = {
            "argument holding NUL": (["git", "--git-dir", "a\0b", "config"], 5),
            "no such program": ([str(tmp_path / "absent")], 5),
            "program cannot run": ([str(tmp_path / "not-a-program")], 5),
            "no answer in time": ([sys.executable, "-c", "import time; time.sleep(5)"], 0.05),
        }
        raised, uncaught = {}, {}
        for cause, (command, timeout) in attempts.items():
            try:
                subprocess.run(command, capture_output=True, timeout=timeout)
            except Exception as error:  # noqa: BLE001 - the raised class is the measurement
                raised[cause] = type(error).__name__
                if not isinstance(error, caught):
                    uncaught[cause] = type(error).__name__
        assert len(raised) == len(attempts), raised  # control: every attempt raised
        assert uncaught == {}, uncaught

    def test_every_exit_but_an_answer_is_named_as_unread(self, tmp_path, monkeypatch):
        """git answers the `core.worktree` read only by exiting 0 with the
        value, or 1 with no output (unset). Every other exit, a negative one
        for a git killed by a signal included, and an exit of 1 that printed
        something, is named as unread. The exits are every return code
        `subprocess` can report on this platform: `os.waitstatus_to_exitcode`
        (which `subprocess` applies to the wait status) over every 16-bit
        wait status."""
        admin, checkout = self._submodule(tmp_path)
        real = pi.subprocess.run
        codes = set()
        for status in range(1 << 16):
            try:
                codes.add(os.waitstatus_to_exitcode(status))
            except ValueError:
                continue
        assert {0, 1, 128, -9} <= codes, sorted(codes)  # control: exits and a signal
        outcomes = [(code, b"") for code in sorted(codes) if code not in (0, 1)]
        outcomes.append((1, b"../../../x\n"))
        wrong = {}
        for code, output in outcomes:
            def run(command, *args, code=code, output=output, **kwargs):
                if "core.worktree" in command:
                    return subprocess.CompletedProcess(command, code, output, b"")
                return real(command, *args, **kwargs)

            monkeypatch.setattr(pi.subprocess, "run", run)
            answer = pi._admin_dir_refusal(admin, checkout)
            if answer != pi.CORE_WORKTREE_UNREAD_DETAIL:
                wrong[(code, output)] = answer
        assert wrong == {}, wrong
