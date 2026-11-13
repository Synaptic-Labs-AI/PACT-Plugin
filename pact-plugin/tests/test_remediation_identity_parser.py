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

import os
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    common_dir,
    make_repo,
    reason_dict_sites,
    run_git,
    seed_session_evidence,
    write_common_dir_override,
)

from scripts import project_identity as pi

CANONICAL = pi.OUTCOME_CANONICAL
NO_PORTABLE = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE


def _origin_id(repo: Path):
    """The (outcome, id) the classifier gives for what `read_origin` returns."""
    origin = pi.read_origin(repo)
    assert origin.state == pi.ORIGIN_PRESENT, origin
    return pi.classify_remote_origin(origin.value)


def _add_url(repo: Path, url: str) -> None:
    run_git("config", "--add", "remote.origin.url", url, cwd=repo)


class TestTheOriginIsTheUrlGitFetches:
    """The origin is the first `remote.origin.url` in the repository's own
    config, includes followed. git fetches from the first of several urls;
    the last one is a push-only url."""

    def test_the_first_of_two_urls_wins(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@first.example:o/first.git")
        _add_url(repo, "git@second.example:o/second.git")

        assert _origin_id(repo) == (CANONICAL, "first.example/o/first")

    def test_a_url_set_only_through_an_include_is_present(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo")
        included = tmp_path / "included.cfg"
        included.write_text(
            '[remote "origin"]\n\turl = git@included.example:o/inc.git\n',
            encoding="utf-8",
        )
        run_git("config", "include.path", str(included), cwd=repo)

        assert _origin_id(repo) == (CANONICAL, "included.example/o/inc")

    @pytest.mark.parametrize(
        "urls",
        [
            ["git@a.example:o/a.git", "", "git@b.example:o/b.git"],
            ["git@a.example:o/a.git", ""],
            ["", "git@b.example:o/b.git"],
            [""],
            ["git@a.example:o/a.git", "   "],
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

        assert pi.read_origin(repo) == pi.OriginRead(pi.ORIGIN_EMPTY_ENTRY, None)
        identity = pi.detect_id_for_root(repo, common_dir(repo))
        assert identity.project_id is None
        assert identity.decline_reason == pi.decline_record(
            "origin_unrepresentable", str(common_dir(repo) / "pact-project-id")
        )
        assert identity.detail == pi.EMPTY_URL_ENTRY_DETAIL

    def test_only_the_empty_entry_decline_carries_a_detail(self, tmp_path):
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

        origin = pi.read_origin(repo)

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

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT

    def test_environment_config_does_not_set_the_identity(self, tmp_path, monkeypatch):
        repo = self._origin_less_repo(tmp_path)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "git@env.example:o/e.git")
        monkeypatch.setenv(
            "GIT_CONFIG_PARAMETERS", "'remote.origin.url'='git@param.example:o/p.git'"
        )

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT

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

        assert pi.read_origin(repo).state == pi.ORIGIN_ABSENT


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

        assert pi._gitfile_points_at_foreign_gitdir(forged) is True
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

        assert pi._gitfile_points_at_foreign_gitdir(forged) is True
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

        assert pi._gitfile_points_at_foreign_gitdir(forged) is True

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

        assert pi._gitfile_points_at_foreign_gitdir(checkout) is False
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

        assert pi._gitfile_points_at_foreign_gitdir(worktree) is False

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

    def test_the_reason_scan_reads_every_form(self):
        """Live control for the scan above. Each line in the fixture carries
        the verdict it must reach: `decline` (a reason dict without a path),
        `pair` (a reason dict that also sets `path`) or `none`."""
        import ast
        import textwrap

        source = textwrap.dedent("""
            def build(base, reason, items):
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
        """)
        expected = {
            number: line.rsplit("# ", 1)[1]
            for number, line in enumerate(source.splitlines(), start=1)
            if line.rstrip().endswith(("# decline", "# pair", "# none"))
        }
        assert len(expected) == 12, expected
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
        (tmp_path / "top" / ".git").mkdir()
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

        assert pi.find_git_marker(tmp_path / "top" / "sub") == (
            (tmp_path / "top" / ".git").resolve()
        )

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

        assert pi._gitfile_points_at_foreign_gitdir(forged) is True


# ---------------------------------------------------------------------------
# Percent-encoded and non-ASCII paths, `ssh+git`, Azure DevOps Services
# ---------------------------------------------------------------------------

NFC_CAFE = "café"
NFD_CAFE = "café"


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
            ("https://host/owner/répo", "host/owner/r%C3%A9po"),
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
        (tmp_path / "a" / ".git").mkdir()
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

    def test_the_genuine_worktree_still_resolves(self, tmp_path, monkeypatch):
        worktree, _delivered, store = self._victim(tmp_path)
        memory = _memory_in(monkeypatch, worktree, store)

        assert memory.project_id == self.VICTIM_ID
        assert memory._decline_reason is None


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

        assert pi._gitfile_points_at_foreign_gitdir(delivered) is True
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

        assert pi._gitfile_points_at_foreign_gitdir(delivered) is True
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

        assert pi._gitfile_points_at_foreign_gitdir(delivered) is False
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

        assert pi._gitfile_points_at_foreign_gitdir(checkout) is False
        assert _memory_in(monkeypatch, checkout, tmp_path / "m.db").project_id == (
            "github.com/me/moved"
        )

    def test_a_symlinked_parent_folder_is_the_same_checkout(self, tmp_path, monkeypatch):
        assert_outside_repository(tmp_path)
        victim = make_repo(tmp_path / "work" / "proj", remote=self.VICTIM)
        alias = tmp_path / "alias"
        alias.symlink_to(victim)

        assert pi._gitfile_points_at_foreign_gitdir(alias) is False
        assert _memory_in(monkeypatch, alias, tmp_path / "m.db").project_id == self.VICTIM_ID
