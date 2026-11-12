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

from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    common_dir,
    make_repo,
    run_git,
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

    def test_an_empty_value_clears_the_urls_before_it(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@old.example:o/old.git")
        _add_url(repo, "")
        _add_url(repo, "git@new.example:o/new.git")

        assert _origin_id(repo) == (CANONICAL, "new.example/o/new")

    def test_an_empty_value_with_nothing_after_it_is_absent(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="git@old.example:o/old.git")
        _add_url(repo, "")

        assert pi.read_origin(repo) == pi.OriginRead(pi.ORIGIN_ABSENT, None)

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


def _memory_in(monkeypatch, checkout: Path, store: Path):
    from scripts import memory_api
    from scripts.memory_api import PACTMemory

    monkeypatch.chdir(checkout)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
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
        """Every dict literal carrying a `reason` key, other than the
        `override_ignored` `{path, reason}` pair, must go through
        `decline_record`, so its shape and its reason set cannot drift."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(pi))
        builder = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "decline_record"
        )
        inside = {id(node) for node in ast.walk(builder)}
        literals = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Dict) and id(node) not in inside
            and any(isinstance(k, ast.Constant) and k.value == "reason" for k in node.keys)
            and not any(isinstance(k, ast.Constant) and k.value == "path" for k in node.keys)
        ]
        assert literals == []

    def test_a_real_decline_is_built_by_the_record(self, tmp_path):
        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote="https://host/owner/re po")

        identity = pi.detect_id_for_root(repo, common_dir(repo))

        assert identity.decline_reason == pi.decline_record(
            "origin_unrepresentable", str(common_dir(repo) / "pact-project-id")
        )
