"""Resolver contracts: one decline record, one guard, and what each rung binds.

Each class names the behaviour it pins and the defect it would catch. The
fixtures are REAL repositories built from a fresh `git init` with a fake
origin URL: the seam under test is what git answers, not what a mock says.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    make_separate_git_dir_repo,
    make_worktree,
    run_git,
    write_common_dir_override,
)
from scripts.memory_api import PACTMemory
from shared import backlog

from scripts import memory_api

MEMORY_API = Path(memory_api.__file__)


@pytest.fixture
def identity_env(monkeypatch, tmp_path):
    """A tmp root outside any repository, with no ambient declaration."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


def _module_tree() -> ast.Module:
    return ast.parse(MEMORY_API.read_text(encoding="utf-8"))


class TestOneDeclineRecord:
    """Every decline in the resolver is built by one builder.

    Catches a rung that hand-rolls its own decline dict or record. That is how
    one rung's guards and disclosure drift from another's: a fix applied to
    the builder never reaches the copy.
    """

    # A SUPPRESSED legacy candidate is not a declined identity, so its record
    # has its own single builder and is the one `reason` dict allowed here.
    SUPPRESSION_BUILDER = "_suppression_record"

    def test_no_decline_dict_literal_remains(self):
        tree = _module_tree()
        exempt = set()
        for function in ast.walk(tree):
            if (
                isinstance(function, ast.FunctionDef)
                and function.name == self.SUPPRESSION_BUILDER
            ):
                exempt.update(id(node) for node in ast.walk(function))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict) or id(node) in exempt:
                continue
            keys = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if "reason" in keys:
                offenders.append(node.lineno)
        assert offenders == [], (
            f"memory_api.py builds a decline dict by hand at lines {offenders}; "
            "build it with project_identity.decline_record through _decline"
        )

    def test_every_detected_identity_is_built_by_keyword(self):
        positional = [
            node.lineno
            for node in ast.walk(_module_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_DetectedIdentity"
            and node.args
        ]
        assert positional == [], (
            f"_DetectedIdentity is built positionally at lines {positional}; "
            "every field must be passed by keyword"
        )

    def test_the_suppression_record_has_one_builder(self):
        """The exemption above names a function that exists and is used."""
        tree = _module_tree()
        builders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == self.SUPPRESSION_BUILDER
        ]
        assert len(builders) == 1
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == self.SUPPRESSION_BUILDER
        ]
        assert calls

    def test_the_scan_sees_the_record_constructor(self):
        """Control: the scan is live, so a zero above is not an empty walk."""
        calls = [
            node
            for node in ast.walk(_module_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_DetectedIdentity"
        ]
        assert len(calls) >= 3


class TestTheGitRungNamesARedirect:
    """A forged worktree pointer reached through the cwd is a REDIRECT.

    Catches the git rung reporting a `.git` file that names another
    repository's worktree admin dir as a bare repository: the operator is
    then sent to look for a missing working tree instead of at the forged
    marker.
    """

    def _victim(self, root: Path) -> Path:
        victim = make_repo(root / "victim", remote="https://github.com/victim/secret-lib.git")
        make_worktree(victim, root / "victim-wt")
        return victim

    def test_a_forged_worktree_pointer_declines_as_a_redirect(
        self, identity_env, monkeypatch, caplog
    ):
        victim = self._victim(identity_env)
        forged = identity_env / "forged"
        forged.mkdir()
        (forged / ".git").write_text(
            f"gitdir: {victim / '.git' / 'worktrees' / 'victim-wt'}\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(forged)
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id is None
        assert detected.decline_reason["reason"] == "gitfile_redirect"
        assert detected.decline_reason["source"] == "git root"
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "no working tree" not in messages and "bare" not in messages

    def test_a_forged_submodule_worktree_pointer_declines_as_a_redirect(
        self, identity_env, monkeypatch
    ):
        """The same refusal for a pointer at a SUBMODULE's worktree admin dir."""
        source = make_repo(identity_env / "subsrc")
        superproject = make_repo(identity_env / "super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        lib = superproject / "vendor" / "lib"
        run_git("remote", "set-url", "origin", "https://github.com/victim/lib.git", cwd=lib)
        make_worktree(lib, identity_env / "libwt")
        admin = superproject / ".git" / "modules" / "vendor" / "lib" / "worktrees" / "libwt"
        assert admin.is_dir()
        forged = identity_env / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        monkeypatch.chdir(forged)
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id is None
        assert detected.decline_reason["reason"] == "gitfile_redirect"

    def test_a_bare_repository_still_has_no_working_tree(
        self, identity_env, monkeypatch
    ):
        """Control: the redirect arm does not swallow the real bare case."""
        bare = identity_env / "bare.git"
        run_git("init", "-q", "--bare", str(bare), cwd=identity_env)
        monkeypatch.chdir(bare)
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id is None
        assert detected.decline_reason["reason"] == "no_working_tree"


class TestALiveDeclinedDeclarationEndsResolution:
    """A declared repository that declines is the answer, not a fall-through.

    Catches the resolver handing a declared session to the process's OWN
    repository when the declared one refuses: the session's memories would be
    filed under a project nobody declared. Only a GONE declaration falls
    through to the git root.
    """

    ELSEWHERE_PIN = "gitlab.example.com/elsewhere/pinned"

    def _elsewhere(self, root: Path, monkeypatch) -> Path:
        """An unrelated repository with its own pin, holding the cwd."""
        elsewhere = make_repo(root / "elsewhere", remote="https://github.com/o/e.git")
        write_common_dir_override(elsewhere, self.ELSEWHERE_PIN)
        monkeypatch.chdir(elsewhere)
        return elsewhere

    def _assert_declines(self, detected, reason: str) -> None:
        assert detected.project_id is None, (
            f"the declared session bound {detected.project_id!r}"
        )
        assert detected.project_id != self.ELSEWHERE_PIN
        assert detected.source == "unresolved"
        assert detected.decline_reason["reason"] == reason
        assert detected.decline_reason["source"] == "CLAUDE_PROJECT_DIR"

    def test_an_unrepresentable_origin_declines(self, identity_env, monkeypatch):
        # `%ZZ` is not a hex escape, so no percent rule can represent it.
        declared = make_repo(
            identity_env / "declared", remote="https://host.example/own%ZZr/repo"
        )
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
        self._assert_declines(
            PACTMemory._detect_project_identity_with_legacy(),
            "origin_unrepresentable",
        )

    def test_a_submodule_checkout_declines(self, identity_env, monkeypatch):
        source = make_repo(identity_env / "submodule-source")
        superproject = make_repo(identity_env / "super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(superproject / "vendor" / "lib"))
        self._assert_declines(
            PACTMemory._detect_project_identity_with_legacy(),
            "submodule_checkout",
        )

    def test_a_redirected_git_marker_declines(self, identity_env, monkeypatch):
        declared = make_separate_git_dir_repo(
            identity_env / "declared", identity_env / "store" / ".git"
        )
        run_git("remote", "add", "origin", "https://github.com/o/d.git", cwd=declared)
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
        self._assert_declines(
            PACTMemory._detect_project_identity_with_legacy(),
            "gitfile_redirect",
        )

    def test_a_bare_repository_declines(self, identity_env, monkeypatch):
        bare = identity_env / "declared.git"
        run_git("init", "-q", "--bare", str(bare), cwd=identity_env)
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(bare))
        self._assert_declines(
            PACTMemory._detect_project_identity_with_legacy(),
            "no_working_tree",
        )

    def test_a_broken_git_marker_declines(self, identity_env, monkeypatch):
        declared = identity_env / "declared"
        declared.mkdir()
        (declared / ".git").write_text("gitdir: /nonexistent/admin\n", encoding="utf-8")
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
        self._assert_declines(
            PACTMemory._detect_project_identity_with_legacy(),
            "broken_git_marker",
        )

    def test_a_gone_declaration_still_falls_through(self, identity_env, monkeypatch):
        """Control: the precedence the git root was designed for is kept."""
        self._elsewhere(identity_env, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(identity_env / "gone"))
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id == self.ELSEWHERE_PIN


# ---------------------------------------------------------------------------
# The backlog file name is the PRE-CHANGE value, including its refusals
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def name_rig(tmp_path_factory):
    """One repository per rung, built once. Fresh `git init`, fake origins."""
    root = tmp_path_factory.mktemp("prechange", numbered=False)
    assert_outside_repository(root)
    rig = {"root": root}
    repo = make_repo(root / "repo", remote="https://github.com/o/Repo.git")
    (repo / "sub" / "deep").mkdir(parents=True)
    rig["repo"], rig["repo_deep"] = repo, repo / "sub" / "deep"
    rig["repo_wt"] = make_worktree(repo, root / "repo-wt")
    rig["sep"] = make_separate_git_dir_repo(root / "sep", root / "ext" / "sep.git")
    source = make_repo(root / "subsrc")
    superproject = make_repo(root / "super")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
        str(source), "vendor/lib", cwd=superproject,
    )
    rig["submodule"] = superproject / "vendor" / "lib"
    rig["plain"] = root / "plain"
    (rig["plain"] / ".claude").mkdir(parents=True)
    rig["bare"] = root / "bare.git"
    run_git("init", "-q", "--bare", str(rig["bare"]), cwd=root)
    rig["broken"] = root / "broken"
    rig["broken"].mkdir()
    (rig["broken"] / ".git").write_text("gitdir: /nonexistent/admin\n", encoding="utf-8")
    victim = make_repo(root / "victim")
    make_worktree(victim, root / "victim-wt")
    rig["forged"] = root / "forged"
    rig["forged"].mkdir()
    (rig["forged"] / ".git").write_text(
        f"gitdir: {victim / '.git' / 'worktrees' / 'victim-wt'}\n", encoding="utf-8"
    )
    rig["unrep"] = make_repo(root / "unrep", remote="https://host.example/own%ZZr/repo")
    rig["nomark"] = root / "nomark" / "x"
    rig["nomark"].mkdir(parents=True)
    rig["home"] = root / "home"
    (rig["home"] / "notes").mkdir(parents=True)
    (rig["home"] / ".claude").mkdir()
    rig["gone"] = root / "gone"
    return rig


# (case, cwd key, declared key, record key, the pre-change value). Each
# expected value is a LITERAL, measured from the pre-change resolver on this
# exact rig; it is never re-derived by the code under test.
PRE_CHANGE_CASES = [
    ("declared repo root", "nomark", "repo", None, "repo"),
    ("declared repo subdir", "nomark", "repo_deep", None, "repo"),
    ("declared linked worktree", "nomark", "repo_wt", None, "repo"),
    ("declared separate git dir", "nomark", "sep", None, "ext"),
    ("declared submodule", "nomark", "submodule", None, "vendor"),
    ("declared non-git dir", "nomark", "plain", None, "plain"),
    ("declared unrepresentable", "nomark", "unrep", None, "unrep"),
    ("declared gone, cwd in a repo", "repo", "gone", None, "repo"),
    ("declared gone, cwd non-git", "nomark", "gone", None, None),
    ("declared gone, cwd broken", "broken", "gone", None, None),
    ("record repo subdir", "nomark", None, "repo_deep", "repo"),
    ("git repo subdir", "repo_deep", None, None, "repo"),
    ("git linked worktree", "repo_wt", None, None, "repo"),
    ("git separate git dir", "sep", None, None, "ext"),
    ("git submodule", "submodule", None, None, "vendor"),
    ("git bare", "bare", None, None, "prechange"),
    ("git forged worktree pointer", "forged", None, None, "victim"),
    ("git unrepresentable", "unrep", None, None, "unrep"),
    ("broken marker, no declaration", "broken", None, None, "broken"),
    ("cwd non-git marker", "plain", None, None, "plain"),
    ("HOME landing", "home_notes", None, None, "home"),
    ("no marker, no repo", "nomark", None, None, "x"),
]


class TestThePreChangeName:
    """`_detect_project_basename` returns what the pre-change resolver did.

    Catches a backlog file renamed by the upgrade (a submodule's `vendor.json`
    becoming `lib.json`, a redirected checkout named after itself) and a
    writer that writes where the pre-change resolver refused, under a name
    nobody reads back.
    """

    @staticmethod
    def _place(rig, monkeypatch, cwd, declared, record):
        monkeypatch.setenv("HOME", str(rig["home"]))
        paths = dict(rig, home_notes=rig["home"] / "notes")
        monkeypatch.chdir(paths[cwd])
        if declared is None:
            monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(paths[declared]))
        monkeypatch.setattr(
            memory_api,
            "get_project_dir_from_session_record",
            lambda: str(paths[record]) if record else "",
        )

    @pytest.mark.parametrize(
        "case, cwd, declared, record, expected",
        PRE_CHANGE_CASES,
        ids=[case[0] for case in PRE_CHANGE_CASES],
    )
    def test_the_name_matches_the_pre_change_resolver(
        self, name_rig, monkeypatch, case, cwd, declared, record, expected
    ):
        self._place(name_rig, monkeypatch, cwd, declared, record)
        assert PACTMemory._detect_project_basename() == expected

    def test_the_user_setting_rung_keeps_the_folder_name(
        self, name_rig, monkeypatch, tmp_path
    ):
        self._place(name_rig, monkeypatch, "nomark", None, None)
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(tmp_path))
        (tmp_path / "config.json").write_text(
            '{"project_id": "user/scope"}', encoding="utf-8"
        )
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.source == "user setting"
        assert PACTMemory._detect_project_basename() == "x"

    def test_a_deleted_cwd_has_no_name(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        monkeypatch.chdir(doomed)
        doomed.rmdir()
        assert PACTMemory._detect_project_basename() is None

    def test_the_real_writer_refuses_where_the_pre_change_resolver_did(
        self, name_rig, monkeypatch, tmp_path
    ):
        """A gone declaration with a non-git cwd: no name, no file."""
        self._place(name_rig, monkeypatch, "plain", "gone", None)
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        with pytest.raises(backlog.BacklogWriteError):
            backlog.store_path(backlog_dir=backlog_dir)
        assert list(backlog_dir.iterdir()) == []

    def test_a_submodule_keeps_its_pre_change_file_name(
        self, name_rig, monkeypatch, tmp_path
    ):
        self._place(name_rig, monkeypatch, "submodule", None, None)
        assert backlog.store_path(backlog_dir=tmp_path).name == "vendor.json"

    def test_a_broken_marker_names_the_marker_directory(
        self, name_rig, monkeypatch, tmp_path
    ):
        self._place(name_rig, monkeypatch, "broken", None, None)
        assert backlog.store_path(backlog_dir=tmp_path).name == "broken.json"

    def test_two_store_paths_run_one_detection(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path / "once")
        monkeypatch.chdir(repo)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        calls = []
        real = PACTMemory._detect_project_identity_with_legacy

        def counting():
            calls.append(1)
            return real()

        monkeypatch.setattr(
            PACTMemory, "_detect_project_identity_with_legacy", staticmethod(counting)
        )
        first = backlog.store_path(backlog_dir=tmp_path)
        second = backlog.store_path(backlog_dir=tmp_path)
        assert first == second and first.name == "once.json"
        assert len(calls) == 1


class TestAHomeRepositoryIsAHomeLanding:
    """A home directory that is a git repository does not lend its id.

    Catches every non-repository directory under a dotfiles HOME being filed
    under `github.com/me/dotfiles` with no warning, the HOME landing the cwd
    rung already refuses to canonicalise, reached through the git rung.
    """

    def test_a_dotfiles_home_warns_and_keeps_the_folder_name(
        self, identity_env, monkeypatch, caplog
    ):
        # `Path.home()` is redirected to a per-test directory by the suite.
        home = make_repo(Path.home().resolve(), remote="https://github.com/me/dotfiles.git")
        (home / "notes").mkdir()
        monkeypatch.chdir(home / "notes")
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id == home.name
        assert detected.source == "git root"
        assert any("HOME directory" in record.getMessage() for record in caplog.records)

    def test_a_repository_below_home_still_canonicalises(
        self, identity_env, monkeypatch
    ):
        """Control: only the home landing itself is refused."""
        home = make_repo(Path.home().resolve(), remote="https://github.com/me/dotfiles.git")
        project = make_repo(home / "src" / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.chdir(project)
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id == "github.com/o/proj"


class TestACeilingStartDirectoryIsExamined:
    """A broken `.git` marker AT a ceiling start directory still declines.

    Catches the resolver's discovery walk skipping a start directory that is
    a `GIT_CEILING_DIRECTORIES` entry. git examines it, so a broken marker
    there is a broken repository, not an umbrella folder named after itself.
    """

    def _broken(self, root):
        broken = root / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nonexistent/admin\n", encoding="utf-8")
        return broken

    def test_a_broken_marker_at_the_ceiling_start_declines(
        self, identity_env, monkeypatch
    ):
        broken = self._broken(identity_env)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(broken))
        monkeypatch.chdir(broken)
        detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id is None
        assert detected.decline_reason["reason"] == "broken_git_marker"

    def test_a_marker_in_a_ceiling_parent_is_not_found(
        self, identity_env, monkeypatch
    ):
        broken = self._broken(identity_env)
        (broken / "sub").mkdir()
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(broken))
        assert memory_api._unanswerable_repository(broken / "sub") is None
        # Control: the same marker is found from its own directory.
        assert memory_api._unanswerable_repository(broken) == broken / ".git"


class TestADeclaredHomeIsAHomeLanding:
    """A DECLARED home directory is a HOME landing too, repository or not.

    Catches the dominant route into HOME: Claude Code sets CLAUDE_PROJECT_DIR
    to the launch directory, so a session started in a dotfiles home would
    bind `github.com/me/dotfiles` with no warning, and a plain home would be
    filed under its folder name silently.
    """

    @staticmethod
    def _home(dotfiles):
        # `Path.home()` is redirected to a per-test directory by the suite.
        home = Path.home().resolve()
        if dotfiles:
            make_repo(home, remote="https://github.com/me/dotfiles.git")
        else:
            home.mkdir(parents=True, exist_ok=True)
        (home / "notes").mkdir(exist_ok=True)
        return home

    def _assert_home_landing(self, detected, home, caplog):
        assert detected.project_id == home.name
        assert detected.source == "CLAUDE_PROJECT_DIR"
        assert detected.pre_change_name == home.name
        assert any("HOME directory" in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize("cwd", ["home", "home/notes"])
    def test_a_declared_dotfiles_home(self, identity_env, monkeypatch, caplog, cwd):
        home = self._home(dotfiles=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(home))
        monkeypatch.chdir(home if cwd == "home" else home / "notes")
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        self._assert_home_landing(detected, home, caplog)

    def test_a_declared_plain_home(self, identity_env, monkeypatch, caplog):
        home = self._home(dotfiles=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(home))
        monkeypatch.chdir(home)
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        self._assert_home_landing(detected, home, caplog)

    def test_a_declared_subdirectory_of_a_dotfiles_home(
        self, identity_env, monkeypatch, caplog
    ):
        home = self._home(dotfiles=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(home / "notes"))
        monkeypatch.chdir(home / "notes")
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id == home.name
        assert any("HOME directory" in record.getMessage() for record in caplog.records)

    def test_a_declared_repository_below_home_still_canonicalises(
        self, identity_env, monkeypatch
    ):
        """Control: only the home landing itself is refused."""
        home = self._home(dotfiles=True)
        project = make_repo(home / "src" / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        monkeypatch.chdir(project)
        assert PACTMemory._detect_project_identity_with_legacy().project_id == "github.com/o/proj"
