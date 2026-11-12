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
    make_worktree,
    run_git,
)
from scripts.memory_api import PACTMemory

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
