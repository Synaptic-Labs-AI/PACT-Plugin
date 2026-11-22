"""Resolver contracts: one decline record, one guard, and what each rung binds.

Each class names the behaviour it pins and the defect it would catch. The
fixtures are REAL repositories built from a fresh `git init` with a fake
origin URL: the seam under test is what git answers, not what a mock says.
"""

from __future__ import annotations

import ast
import collections
import functools
import itertools
import logging
import re
import subprocess
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    make_separate_git_dir_repo,
    make_worktree,
    positional_constructions,
    reason_dict_sites,
    run_git,
    seed_session_evidence,
    start_after_description,
    unread_class_references,
    unread_reason_keys,
    write_common_dir_override,
)
from scripts.memory_api import PACTMemory
from shared import backlog

from scripts import memory_api

MEMORY_API = Path(memory_api.__file__)
CREATED_AT = "2026-01-01T00:00:00+00:00"


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
        """No dict carrying a `reason` key is built outside the suppression
        builder, in any form the code can build one (`reason_dict_sites`)."""
        tree = _module_tree()
        exempt = set()
        for function in ast.walk(tree):
            if (
                isinstance(function, ast.FunctionDef)
                and function.name == self.SUPPRESSION_BUILDER
            ):
                exempt.update(id(node) for node in ast.walk(function))
        offenders = [line for line, _has_path in reason_dict_sites(tree, frozenset(exempt))]
        assert offenders == [], (
            f"memory_api.py builds a decline dict by hand at lines {offenders}; "
            "build it with project_identity.decline_record through _decline"
        )
        unread = unread_reason_keys(tree, frozenset(exempt))
        assert unread == [], (
            f"memory_api.py uses a `reason` key at lines {unread} in a form the "
            "scan cannot classify; build the decline with decline_record"
        )
        # The suppression builder's own dict is found when not exempt, so
        # the scan is reading the module.
        assert reason_dict_sites(tree), "the scan found no reason dict at all"

    def test_every_detected_identity_is_built_by_keyword(self):
        """Every `_DetectedIdentity` is built with its fields by keyword, in
        whatever form the code reaches the class (`positional_constructions`:
        a bare name, an attribute, an alias, `_make`, `functools.partial`)."""
        positional = positional_constructions(_module_tree(), "_DetectedIdentity")
        assert positional == [], (
            f"_DetectedIdentity is built positionally at lines {positional}; "
            "every field must be passed by keyword"
        )
        unread = unread_class_references(_module_tree(), "_DetectedIdentity")
        assert unread == [], (
            f"_DetectedIdentity is used as a value at lines {unread}, where the "
            "scan cannot see how it is built; call it with every field by keyword"
        )

    def test_the_construction_scan_reads_every_form(self):
        """Live control for the scan above: each fixture line carries the
        verdict it must reach, `positional` or `keyword`."""
        import textwrap

        source = textwrap.dedent("""
            import functools
            import functools as ft
            from functools import partial
            from scripts.memory_api import _DetectedIdentity as DI

            _DetectedIdentity("module", source="level")  # positional

            def build(fields, module):
                _DetectedIdentity("a", source="b")  # positional
                _DetectedIdentity(*fields)  # positional
                module._DetectedIdentity("a")  # positional
                alias = _DetectedIdentity
                again = alias
                again("a")  # positional
                _DetectedIdentity._make(fields)  # positional
                alias._make(fields)  # positional
                partial(_DetectedIdentity, "a")  # positional
                functools.partial(module._DetectedIdentity, "a")  # positional
                _DetectedIdentity(project_id="a", source="b")  # keyword
                module._DetectedIdentity(project_id="a")  # keyword
                partial(_DetectedIdentity, project_id="a")  # keyword
                _OtherRecord("a")  # keyword
                functools.partial(_DetectedIdentity)("a")  # positional
                functools.partial(_DetectedIdentity)(project_id="a")  # keyword
                ft.partial(_DetectedIdentity, "a")  # positional
                DI("a")  # positional
                DI(project_id="a")  # keyword

            class _DetectedIdentity(NamedTuple):
                project_id: str

                @classmethod
                def make(cls, value):
                    return cls(value)  # positional

                @classmethod
                def make_named(cls, value):
                    return cls(project_id=value)  # keyword

                @classmethod
                def remake(cls, fields):
                    return cls._make(fields)  # positional

                def copy(self):
                    return type(self)(*self)  # positional

                def clone(self):
                    return self.__class__("a")  # positional

                def keyed(self):
                    return self.__class__(project_id="a")  # keyword

                @staticmethod
                def unrelated(cls):
                    return cls("a")  # keyword

            class Narrowed(_DetectedIdentity):
                @classmethod
                def make(klass, value):
                    return klass(value)  # positional

            Narrowed("a")  # positional

            class Other:
                @classmethod
                def make(cls, value):
                    return cls(value)  # keyword

                def copy(self):
                    return type(self)("a")  # keyword
        """)
        expected = {
            number: line.rsplit("# ", 1)[1]
            for number, line in enumerate(source.splitlines(), start=1)
            if line.rstrip().endswith(("# positional", "# keyword"))
        }
        assert len(expected) == 29, expected
        found = set(positional_constructions(ast.parse(source), "_DetectedIdentity"))
        assert {
            number: ("positional" if number in found else "keyword") for number in expected
        } == expected

    def test_a_class_used_as_a_value_fails_closed(self):
        """Live control for `unread_class_references`: each fixture line
        carries `unread` (the class used where the construction scan cannot
        see how it is built) or `read` (a form that scan reads, or no
        construction at all)."""
        import textwrap

        source = textwrap.dedent("""
            import functools
            import itertools
            from typing import Optional

            def build(ids, rows, flag, record: "_DetectedIdentity", other: Optional[_DetectedIdentity]) -> _DetectedIdentity:
                list(map(_DetectedIdentity, ids))  # unread
                list(itertools.starmap(_DetectedIdentity, rows))  # unread
                (_DetectedIdentity if flag else None)("a")  # unread
                factories = [_DetectedIdentity]  # unread
                table = {"d": _DetectedIdentity}  # unread
                list(map(_DetectedIdentity._make, rows))  # unread
                _DetectedIdentity(project_id="a")  # read
                alias = _DetectedIdentity  # read
                functools.partial(_DetectedIdentity, project_id="a")  # read
                isinstance(record, _DetectedIdentity)  # read
                isinstance(record, (_DetectedIdentity, str))  # read
                _DetectedIdentity._fields  # read
                _DetectedIdentity._make(rows)  # read
                return _DetectedIdentity  # unread

            class Narrowed(_DetectedIdentity):  # read
                @classmethod
                def every(cls, rows):
                    return list(map(cls, rows))  # unread

                def copy(self):
                    return [type(self)]  # unread
        """)
        expected = {
            number: line.rsplit("# ", 1)[1]
            for number, line in enumerate(source.splitlines(), start=1)
            if line.rstrip().endswith(("# unread", "# read"))
        }
        assert len(expected) == 17, expected
        found = set(unread_class_references(ast.parse(source), "_DetectedIdentity"))
        assert {number: ("unread" if number in found else "read") for number in expected} == expected
        assert found <= set(expected), sorted(found - set(expected))

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



def _populated_store(path: Path) -> Path:
    """Rows in a named bucket and in the NULL bucket, one file linked to each."""
    from scripts.database import db_connection, ensure_initialized

    with db_connection(path) as conn:
        ensure_initialized(conn)
        for memory_id, project in (("aaaa1111", "github.com/o/a"), ("bbbb2222", None)):
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'populated', ?, 's', ?, ?)",
                (memory_id, project, CREATED_AT, CREATED_AT),
            )
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                ("file-" + memory_id, "/abs/shared.py", project),
            )
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
                (memory_id, "file-" + memory_id),
            )
        conn.commit()
    return path


class TestTheReadScopeIsNeverNone:
    """No identity reads as the EMPTY scope, never as the whole store.

    Catches `_read_project_scopes` returning None: None is the library's
    whole-store value, so one caller that forgets its pre-check would read
    every project's memories for a session that has no identity at all.
    """

    def test_no_identity_is_the_empty_scope(self, identity_env, monkeypatch):
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: memory_api._DetectedIdentity(
                    project_id=None, source="unresolved", legacy_basename=None,
                    override_ignored=None, identity_root=None,
                )
            ),
        )
        memory = PACTMemory(session_id="s", db_path=identity_env / "absent.db")
        assert memory._read_project_scopes() == ()

    def test_every_seam_reads_no_rows_for_the_empty_scope(self, identity_env):
        from scripts.config import store_scope
        from scripts.database import (
            db_connection,
            get_memory_count,
            list_memories,
            resolve_memory_id_prefix,
        )
        from scripts.graph import get_graph_stats
        from scripts.search import search_by_file, vector_search

        store = _populated_store(identity_env / "populated.db")
        with db_connection(store) as conn:
            # Controls: the whole-store reads see both rows.
            assert get_memory_count(conn, None) == 2
            assert len(list_memories(conn, None)) == 2
            assert resolve_memory_id_prefix(conn, "aaaa111") == "aaaa1111"
            assert list_memories(conn, ()) == []
            assert get_memory_count(conn, ()) == 0
            assert resolve_memory_id_prefix(conn, "aaaa111", project_id=()) is None
            assert not any(get_graph_stats(conn, ()).values())
            assert vector_search(conn, "populated", ()) == []
        with store_scope(store):
            assert search_by_file("/abs/shared.py", ()) == []


# ---------------------------------------------------------------------------
# legacy_scope: one key set, and admission on session evidence
# ---------------------------------------------------------------------------

def _seed(store: Path, bucket: str, *, session_id: str = "legacy-session",
          path: str = "src/relative.py", memory_id: str = "legacy0001") -> None:
    """One memory and one file row in `bucket`, written by `session_id`."""
    from scripts.database import db_connection, ensure_initialized

    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES (?, 'legacy memory', ?, ?, ?, ?)",
            (memory_id, bucket, session_id, CREATED_AT, CREATED_AT),
        )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("file-" + memory_id, path, bucket),
        )
        conn.commit()


def _config_root(tmp_path: Path) -> Path:
    """The per-test config root the suite's isolation fixture sandboxes."""
    return tmp_path / "home" / ".claude"


def _fresh_scope_state() -> None:
    """Clear the per-process read-scope cache and the once-only notice memos."""
    for name in (
        "_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_CASE_VARIANT_WARNED",
        "_UNPROVEN_WARNED", "_SUBMODULE_SUPPRESSED",
    ):
        memo = getattr(memory_api, name, None)
        if memo is not None:
            memo.clear()


REMOTE = "https://github.com/o/proj.git"
CANONICAL = "github.com/o/proj"


class TestSessionEvidenceAdmitsAnUnverifiedBucket:
    """An unverified legacy bucket is read only on a recorded session's word.

    Catches a delivered checkout that shares a folder name with one of the
    user's projects reading that project's pre-upgrade memories: a bucket
    whose file rows carry no location cannot be told apart by name alone.
    """

    def _checkout(self, identity_env, monkeypatch, name="proj"):
        _fresh_scope_state()
        repo = make_repo(identity_env / name, remote=REMOTE)
        monkeypatch.chdir(repo)
        return repo

    def test_a_recorded_session_from_this_root_admits_it(
        self, identity_env, monkeypatch
    ):
        repo = self._checkout(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj")
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL, "proj")
        state = memory._legacy_scope_state
        assert state["included"] and state["included_unverified"]
        assert state["session_evidence"] is True and not state["unproven"]

    def test_no_recorded_session_leaves_it_unproven_and_unread(
        self, identity_env, monkeypatch, caplog
    ):
        self._checkout(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj")
        memory = PACTMemory(session_id="s", db_path=store)
        with caplog.at_level(logging.WARNING):
            assert memory._read_project_scopes() == (CANONICAL,)
        state = memory._legacy_scope_state
        assert state["unproven"] and not state["included"] and not state["excluded"]
        assert state["session_evidence"] is False
        notices = [
            record.getMessage() for record in caplog.records
            if "no recorded session" in record.getMessage()
        ]
        assert len(notices) == 1 and "project-id rekey" in notices[0]

    def test_a_scope_read_outside_a_pass_is_not_served_inside_one(
        self, identity_env, monkeypatch
    ):
        """The read-scope cache is written only inside a detection pass. A
        read outside one leaves nothing behind, so a session context file
        that appears afterwards is read by the next read inside a pass, and
        the bucket it vouches for is admitted.

        RED WHEN a read outside a pass also writes the cache: the pass then
        serves the scope computed before the context file existed."""
        repo = self._checkout(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj")
        assert PACTMemory(session_id="s", db_path=store)._read_project_scopes() == (CANONICAL,)
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        with memory_api.detection_pass():
            assert PACTMemory(session_id="s", db_path=store)._read_project_scopes() == (CANONICAL, "proj")

    def test_a_session_recorded_for_a_same_named_folder_is_no_evidence(
        self, identity_env, monkeypatch
    ):
        self._checkout(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj")
        # The delivered-checkout shape: same basename, different directory.
        elsewhere = identity_env / "delivered" / "proj"
        elsewhere.mkdir(parents=True)
        seed_session_evidence(_config_root(identity_env), elsewhere, "legacy-session")
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["unproven"]

    def test_a_session_that_did_not_write_the_bucket_is_no_evidence(
        self, identity_env, monkeypatch
    ):
        repo = self._checkout(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj", session_id="writer")
        seed_session_evidence(_config_root(identity_env), repo, "someone-else")
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["unproven"]


class TestTheSessionMustPostdateTheRepository:
    """A recorded session is evidence only if it started after this
    repository's `description` was created, through its live context or,
    once the folder is reaped, its prune-ledger line.

    Catches a repository re-cloned, restored or copied at a deleted
    checkout's path inheriting that checkout's legacy bucket through the
    session records the old checkout left behind.
    """

    HOUR = 3600

    @staticmethod
    def _iso(offset_seconds: float) -> str:
        from datetime import datetime, timedelta, timezone

        return (
            datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
        ).isoformat()

    def _checkout(self, identity_env, monkeypatch):
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        _seed(identity_env / "store.db", "proj")
        return repo

    @staticmethod
    def _state(identity_env):
        memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
        scopes = memory._read_project_scopes()
        return scopes, memory._legacy_scope_state

    @staticmethod
    def _reap(context: Path) -> None:
        """Record the folder in the prune ledger with the real writer, then
        remove it, as the session reaper does."""
        import shutil

        import session_end

        session_dir = context.parent
        session_end._record_pruned_session(session_dir.parent, session_dir)
        shutil.rmtree(session_dir)

    @staticmethod
    def _ledger(context: Path) -> Path:
        from shared.pact_context import PRUNE_LEDGER_NAME

        return context.parent.parent / PRUNE_LEDGER_NAME

    @staticmethod
    def _reclone(repo: Path, monkeypatch) -> None:
        """Delete the checkout and create a new one at the same path. The
        working directory leaves first, or it would name the deleted one."""
        import shutil

        monkeypatch.chdir(repo.parent)
        shutil.rmtree(repo)
        make_repo(repo, remote=REMOTE)
        monkeypatch.chdir(repo)

    def test_a_session_after_the_description_is_evidence(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL, "proj") and state["session_evidence"] is True

    def test_a_session_before_the_description_is_no_evidence(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        seed_session_evidence(
            _config_root(identity_env), repo, "legacy-session",
            started_at=self._iso(-self.HOUR),
        )
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_recloned_repository_does_not_inherit_its_sessions(
        self, identity_env, monkeypatch, caplog
    ):
        repo = self._checkout(identity_env, monkeypatch)
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reclone(repo, monkeypatch)
        with caplog.at_level(logging.WARNING):
            scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state
        notices = [
            r.getMessage() for r in caplog.records
            if "no recorded session" in r.getMessage()
        ]
        assert len(notices) == 1 and "project-id rekey" in notices[0], notices

    def test_a_naive_start_is_no_evidence(self, identity_env, monkeypatch):
        """A day ahead and zone-less: read as local time it would pass."""
        from datetime import datetime, timedelta

        repo = self._checkout(identity_env, monkeypatch)
        naive = (datetime.now() + timedelta(days=1)).isoformat()
        seed_session_evidence(
            _config_root(identity_env), repo, "legacy-session", started_at=naive
        )
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_an_unparseable_start_is_no_evidence(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        seed_session_evidence(
            _config_root(identity_env), repo, "legacy-session", started_at="yesterday"
        )
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_symlinked_description_is_no_evidence(self, identity_env, monkeypatch):
        """The link points at a file older than the session, so following it
        would pass; `lstat` sees a link and refuses it."""
        repo = self._checkout(identity_env, monkeypatch)
        old = identity_env / "old-description"
        old.write_text("old\n", encoding="utf-8")
        description = repo / ".git" / "description"
        description.unlink()
        description.symlink_to(old)
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_repository_with_no_description_is_unproven(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        (repo / ".git" / "description").unlink()
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_linked_worktree_session_dates_against_the_main_description(
        self, identity_env, monkeypatch
    ):
        """The worktree's `.git` is a file; the description read is the MAIN
        repository's, shared by every worktree."""
        _fresh_scope_state()
        main = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(main, identity_env / "wt")
        _seed(identity_env / "store.db", "proj")
        seed_session_evidence(_config_root(identity_env), worktree, "legacy-session")
        monkeypatch.chdir(worktree)
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL, "proj") and state["session_evidence"] is True, state

    def test_a_separate_git_dir_dates_against_its_own_description(
        self, identity_env, monkeypatch
    ):
        """The checkout's `.git` is a file naming a git dir elsewhere; the
        description read is the one in that common dir. The git dir's parent
        is named like the checkout, because the legacy candidate is derived
        from it."""
        _fresh_scope_state()
        repo = make_separate_git_dir_repo(
            identity_env / "proj", identity_env / "gitdirs" / "proj" / "git"
        )
        run_git("remote", "add", "origin", REMOTE, cwd=repo)
        assert (repo / ".git").is_file()
        _seed(identity_env / "store.db", "proj")
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        monkeypatch.chdir(repo)
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL, "proj") and state["session_evidence"] is True, state

    def test_a_ledger_line_stands_in_for_a_reaped_folder(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reap(context)
        assert not context.parent.exists() and self._ledger(context).is_file()
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL, "proj") and state["session_evidence"] is True, state

    def test_a_ledger_line_from_before_a_reclone_is_no_evidence(
        self, identity_env, monkeypatch
    ):
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reap(context)
        self._reclone(repo, monkeypatch)
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_the_ledger_is_read_only_when_the_folder_is_gone(
        self, identity_env, monkeypatch
    ):
        """A live folder whose context fails is not rescued by a ledger line
        that would pass."""
        import json

        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(
            _config_root(identity_env), repo, "legacy-session",
            started_at=self._iso(-self.HOUR),
        )
        line = {"session_id": "legacy-session", "project_dir": str(repo),
                "started_at": start_after_description(repo)}
        self._ledger(context).write_text(json.dumps(line) + "\n", encoding="utf-8")
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_ledger_line_for_another_session_is_no_evidence(
        self, identity_env, monkeypatch
    ):
        """The bucket was written by `legacy-session`; a reaped folder of a
        different session from this repository proves nothing about it."""
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "someone-else")
        self._reap(context)
        gone = context.parent.parent / "legacy-session"
        assert not gone.exists() and self._ledger(context).is_file()
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_malformed_ledger_lines_are_skipped(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reap(context)
        ledger = self._ledger(context)
        good = ledger.read_text(encoding="utf-8")
        ledger.write_text(
            "not json\n[1, 2]\n{\"session_id\": \n" + good + "\x00\n",
            encoding="utf-8",
        )
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL, "proj") and state["session_evidence"] is True, state

    def test_a_symlinked_ledger_is_not_read(self, identity_env, monkeypatch):
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reap(context)
        ledger = self._ledger(context)
        real = identity_env / "real-ledger.jsonl"
        ledger.rename(real)
        ledger.symlink_to(real)
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state

    def test_a_line_beyond_the_tail_bound_is_not_read(self, identity_env, monkeypatch):
        """Only the end of the ledger is read; an older line reads unproven."""
        repo = self._checkout(identity_env, monkeypatch)
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        self._reap(context)
        ledger = self._ledger(context)
        filler = '{"session_id": "other", "project_dir": "/nowhere", "started_at": "x"}\n'
        count = memory_api._PRUNE_LEDGER_TAIL_BYTES // len(filler) + 10
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(filler * count)
        scopes, state = self._state(identity_env)
        assert scopes == (CANONICAL,) and state["unproven"], state


class TestTheReadScopeCacheEndsWithItsPass:
    """A read scope is cached for one detection pass only: a later pass, the
    next command, computes it afresh, so it sees an input of its gate or its
    session evidence that changed in between (a linked worktree or its
    links, a session's context file or folder, a prune ledger, the
    repository's `description`, or the config root that names the session
    folders), and none of them needs a key of its own.

    Catches a cached admission (or refusal) of an unverified legacy bucket
    outliving the command that decided it.
    """

    @staticmethod
    def _scopes(identity_env):
        """The read scope one command reads: a construction and a read in
        one pass."""
        with memory_api.detection_pass():
            return PACTMemory(session_id="s", db_path=identity_env / "store.db")._read_project_scopes()

    @staticmethod
    def _context_created(identity_env, repo, context):
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")

    @staticmethod
    def _context_rewritten(identity_env, repo, context):
        import json

        record = json.loads(context.read_text(encoding="utf-8"))
        elsewhere = identity_env / "elsewhere"
        elsewhere.mkdir()
        record["project_dir"] = str(elsewhere)
        context.write_text(json.dumps(record), encoding="utf-8")

    @staticmethod
    def _folder_removed(identity_env, repo, context):
        import shutil

        shutil.rmtree(context.parent)

    @staticmethod
    def _ledger_added(identity_env, repo, context):
        """Append the reaped session's line to a ledger that already
        exists, so only the ledger changes."""
        import json

        from shared.pact_context import PRUNE_LEDGER_NAME

        record = json.loads((identity_env / "held.json").read_text(encoding="utf-8"))
        with (context.parent.parent / PRUNE_LEDGER_NAME).open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record) + "\n")

    @staticmethod
    def _description_recreated(identity_env, repo, context):
        description = repo / ".git" / "description"
        text = description.read_text(encoding="utf-8")
        description.unlink()
        description.write_text(text, encoding="utf-8")

    @staticmethod
    def _config_root_moved(identity_env, repo, context):
        import os

        # The test's monkeypatch set this variable, so its teardown restores it.
        other = identity_env / "other-config"
        other.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(other)

    @pytest.mark.parametrize("change, admitted_before", [
        ("_context_created", False),
        ("_context_rewritten", True),
        ("_folder_removed", True),
        ("_ledger_added", False),
        ("_description_recreated", True),
        ("_config_root_moved", True),
    ], ids=[
        "a context file created", "a context file naming another folder", "the session folder removed",
        "a prune ledger line added", "the description recreated", "the config root moved",
    ])
    def test_a_changed_evidence_input_is_read_again(self, identity_env, monkeypatch, change, admitted_before):
        import shutil

        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        _seed(identity_env / "store.db", "proj")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_config_root(identity_env)))
        context = seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        if not admitted_before:
            # The session is absent at the first read. For `_ledger_added` its
            # record is kept for the ledger line, and the ledger already holds
            # another session's line.
            if change == "_ledger_added":
                import json

                from shared.pact_context import PRUNE_LEDGER_NAME

                recorded = json.loads(context.read_text(encoding="utf-8"))
                line = {"session_id": "legacy-session", "project_dir": recorded["project_dir"],
                        "started_at": recorded["started_at"]}
                (identity_env / "held.json").write_text(json.dumps(line), encoding="utf-8")
                other = {"session_id": "other", "project_dir": "/nowhere", "started_at": recorded["started_at"]}
                (context.parent.parent / PRUNE_LEDGER_NAME).write_text(json.dumps(other) + "\n", encoding="utf-8")
            shutil.rmtree(context.parent)
        first = self._scopes(identity_env)
        assert first == ((CANONICAL, "proj") if admitted_before else (CANONICAL,)), first
        getattr(self, change)(identity_env, repo, context)
        second = self._scopes(identity_env)
        _fresh_scope_state()
        assert second == self._scopes(identity_env) == ((CANONICAL,) if admitted_before else (CANONICAL, "proj"))

    def test_a_worktree_added_after_a_read_is_seen(self, identity_env, monkeypatch):
        """A linked worktree added after a read that excluded the bucket (its
        absolute row lies under the worktree's path) widens the gate on the
        next read: the gate roots are stamped too."""
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        worktree = identity_env / "wt"
        _seed(identity_env / "store.db", "proj", path=str(worktree / "a.py"))
        assert self._scopes(identity_env) == (CANONICAL,)
        run_git("worktree", "add", "-q", str(worktree), cwd=repo)
        second = self._scopes(identity_env)
        _fresh_scope_state()
        assert second == self._scopes(identity_env) == (CANONICAL, "proj")

    @pytest.mark.parametrize("unlink", ["the worktree's .git", "the admin dir's gitdir"])
    def test_a_worktree_link_broken_after_a_read_is_seen(self, identity_env, monkeypatch, unlink):
        """A listed worktree whose link breaks after a read that admitted the
        bucket through it (its `.git` file removed, or its admin dir's
        `gitdir` pointed elsewhere) narrows the gate on the next read."""
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        worktree = identity_env / "wt"
        run_git("worktree", "add", "-q", str(worktree), cwd=repo)
        _seed(identity_env / "store.db", "proj", path=str(worktree / "a.py"))
        assert self._scopes(identity_env) == (CANONICAL, "proj")
        if unlink == "the worktree's .git":
            (worktree / ".git").unlink()
        else:
            (repo / ".git" / "worktrees" / "wt" / "gitdir").write_text(
                str(identity_env / "elsewhere" / ".git") + "\n", encoding="utf-8")
        second = self._scopes(identity_env)
        _fresh_scope_state()
        assert second == self._scopes(identity_env) == (CANONICAL,)

    def test_the_cache_serves_one_pass_and_no_other(self, identity_env, monkeypatch):
        """Two reads in one pass decide the scope once; a second pass, and
        each read outside any pass, decides it again; and the cache is empty
        once the pass ends."""
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        _seed(identity_env / "store.db", "proj")
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        calls = []
        real = memory_api._legacy_session_evidence
        monkeypatch.setattr(
            memory_api, "_legacy_session_evidence",
            lambda *args, **kwargs: calls.append(1) or real(*args, **kwargs),
        )
        with memory_api.detection_pass():
            memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
            assert memory._read_project_scopes() == memory._read_project_scopes() == (CANONICAL, "proj")
        assert len(calls) == 1 and len(memory_api._LEGACY_SCOPE_CACHE) == 0, calls
        assert self._scopes(identity_env) == (CANONICAL, "proj")
        assert len(calls) == 2, calls
        memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
        assert memory._read_project_scopes() == memory._read_project_scopes() == (CANONICAL, "proj")
        assert len(calls) == 4, calls


class TestLegacyScopeHasOneKeySet:
    """`legacy_scope` carries the same keys in every state.

    Catches a branch that builds its own dict and drops a key, which turns
    "absent" into a second meaning of "false" for every envelope consumer.
    """

    def _state(self, memory):
        memory._read_project_scopes()
        return memory._legacy_scope_state

    def test_every_state_carries_the_fixed_keys(self, identity_env, monkeypatch):
        keys = memory_api.LEGACY_SCOPE_KEYS
        states = {}

        def checkout(name, remote=REMOTE):
            _fresh_scope_state()
            repo = make_repo(identity_env / name, remote=remote)
            monkeypatch.chdir(repo)
            return repo

        repo = checkout("nocand")
        states["no candidate"] = self._state(
            PACTMemory(session_id="s", db_path=identity_env / "empty.db")
        )
        _seed(identity_env / "empty.db", "unrelated")
        states["no candidate, populated"] = self._state(
            PACTMemory(session_id="s", db_path=identity_env / "empty.db")
        )

        repo = checkout("ver")
        _seed(identity_env / "ver.db", "ver", path=str(repo / "a.py"))
        states["verified"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "ver.db"))

        repo = checkout("unv")
        _seed(identity_env / "unv.db", "unv")
        seed_session_evidence(_config_root(identity_env), repo, "legacy-session")
        states["included_unverified"] = self._state(
            PACTMemory(session_id="s", db_path=identity_env / "unv.db")
        )

        checkout("unp")
        _seed(identity_env / "unp.db", "unp")
        states["unproven"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "unp.db"))

        checkout("exc")
        _seed(identity_env / "exc.db", "exc", path="/elsewhere/x.py")
        states["excluded"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "exc.db"))

        checkout("case")
        _seed(identity_env / "case.db", "CASE")
        states["case_variant"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "case.db"))

        checkout("deg")
        (identity_env / "deg.db").write_bytes(b"")
        states["degraded"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "deg.db"))

        source = make_repo(identity_env / "subsrc")
        superproject = make_repo(identity_env / "super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        lib = superproject / "vendor" / "lib"
        run_git("remote", "set-url", "origin", "https://github.com/o/lib.git", cwd=lib)
        _fresh_scope_state()
        monkeypatch.chdir(lib)
        states["suppressed"] = self._state(PACTMemory(session_id="s", db_path=identity_env / "empty.db"))

        # The absent-store state has no legacy record at all; the rest must
        # each be a full record. Every named state was actually reached.
        assert states["no candidate"] is None
        del states["no candidate"]
        assert states["unproven"]["unproven"]
        assert states["excluded"]["excluded"]
        assert states["case_variant"]["case_variant"] == ["CASE"]
        assert states["degraded"]["degraded"] == "no schema"
        assert states["suppressed"]["suppressed"]["reason"] == "repository_chosen_name"
        for name, state in states.items():
            assert set(state) == keys, (name, sorted(set(state) ^ keys))
            if state["candidate"] is not None:
                assert [state["included"], state["excluded"], state["unproven"]].count(True) == 1, name


class TestTheSubmoduleNoticeHasSomethingToSay:
    """The suppression notice fires only when the suppressed bucket has rows.

    Catches a warning on every command in every submodule, whether or not
    anything was hidden: a notice with nothing behind it is one readers
    learn to skip, so the real one is skipped too.
    """

    def _submodule(self, root, origin):
        source = make_repo(root / "subsrc")
        superproject = make_repo(root / "super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        lib = superproject / "vendor" / "lib"
        if origin is not None:
            run_git("remote", "set-url", "origin", origin, cwd=lib)
        return lib

    @staticmethod
    def _notices(caplog):
        return [
            record.getMessage() for record in caplog.records
            if "is a submodule" in record.getMessage()
            or "submodule" in record.getMessage().split(",")[0]
        ]

    def test_an_empty_store_gets_no_suppression_notice(
        self, identity_env, monkeypatch, caplog
    ):
        lib = self._submodule(identity_env, "https://github.com/o/lib.git")
        _fresh_scope_state()
        monkeypatch.chdir(lib)
        with caplog.at_level(logging.WARNING):
            memory = PACTMemory(session_id="s", db_path=identity_env / "absent.db")
            memory._read_project_scopes()
        assert self._notices(caplog) == []

    def test_a_populated_bucket_gets_exactly_one_notice_with_its_count(
        self, identity_env, monkeypatch, caplog
    ):
        lib = self._submodule(identity_env, "https://github.com/o/lib.git")
        store = identity_env / "store.db"
        _seed(store, "vendor", memory_id="vendor0001")
        _seed(store, "vendor", memory_id="vendor0002", path="src/other.py")
        _fresh_scope_state()
        monkeypatch.chdir(lib)
        with caplog.at_level(logging.WARNING):
            memory = PACTMemory(session_id="s", db_path=store)
            memory._read_project_scopes()
            memory._read_project_scopes()
        notices = self._notices(caplog)
        assert len(notices) == 1 and "2 memories" in notices[0]
        assert memory._legacy_scope_state["suppressed"]["memory_count"] == 2

    def test_a_declined_submodule_logs_one_decline_line(
        self, identity_env, monkeypatch, caplog
    ):
        lib = self._submodule(identity_env, None)
        _fresh_scope_state()
        monkeypatch.chdir(lib)
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.decline_reason["reason"] == "submodule_checkout"
        assert len(self._notices(caplog)) == 1


class TestNoticesQuoteWhatTheyLog:
    """A path or command in a legacy notice is rendered with repr.

    Catches a directory name carrying a newline forging a second log line:
    the notice is read by operators and agents as trusted output.
    """

    FORGED = "WARNING: forged"

    def test_a_newline_in_a_path_cannot_start_a_log_line(
        self, identity_env, monkeypatch, caplog
    ):
        parent = identity_env / ("evil\n" + self.FORGED)
        parent.mkdir()
        for name, path in (("exc", "/elsewhere/x.py"), ("unp", "src/relative.py")):
            _fresh_scope_state()
            repo = make_repo(parent / name, remote=f"https://github.com/o/{name}.git")
            monkeypatch.chdir(repo)
            store = parent / f"{name}.db"
            _seed(store, name, path=path)
            with caplog.at_level(logging.WARNING):
                PACTMemory(session_id="s", db_path=store)._read_project_scopes()
        messages = [record.getMessage() for record in caplog.records]
        lines = [line for message in messages for line in message.splitlines()]
        assert not any(line.startswith(self.FORGED) for line in lines)
        # Control: both notices fired, so the zero above is not silence.
        assert any("legacy project bucket 'exc'" in m for m in messages)
        assert any("legacy project bucket 'unp'" in m for m in messages)


class TestTheUserSettingIsUserScoped:
    """The user-scope `project_id` setting is read from the USER's directory.

    Catches a setting read beside whatever store a command names: a
    `--db-path` pointing at a scratch store would then change the identity a
    session resolves.
    """

    def _umbrella(self, root, monkeypatch):
        umbrella = root / "workspace"
        umbrella.mkdir()
        monkeypatch.chdir(umbrella)
        return umbrella

    def test_the_setting_in_the_user_directory_answers(
        self, identity_env, monkeypatch
    ):
        from scripts.config import store_scope

        self._umbrella(identity_env, monkeypatch)
        user_dir = identity_env / "user-memory"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(
            '{"project_id": "user.example.com/me/pinned"}', encoding="utf-8"
        )
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(user_dir))
        store_dir = identity_env / "elsewhere"
        store_dir.mkdir()
        with store_scope(store_dir / "memory.db"):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id == "user.example.com/me/pinned"
        assert detected.source == "user setting"

    def test_a_setting_beside_a_named_store_does_not(self, identity_env, monkeypatch):
        from scripts.config import store_scope

        self._umbrella(identity_env, monkeypatch)
        user_dir = identity_env / "user-memory"
        user_dir.mkdir()
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(user_dir))
        store_dir = identity_env / "scratch-store"
        store_dir.mkdir()
        (store_dir / "config.json").write_text(
            '{"project_id": "scratch.example.com/x/y"}', encoding="utf-8"
        )
        with store_scope(store_dir / "memory.db"):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.project_id != "scratch.example.com/x/y"
        assert detected.source == "cwd"


class TestOneDetectionAsksGitEachQuestionOnce:
    """A detection, and a save after it, ask git each question once.

    Catches identical git subprocesses repeated within one resolution: the
    divergence check re-resolving the root the resolver just resolved, and
    the worktree check re-asking for answers its caller already holds. A
    `-C` naming the working directory is the same question as no `-C`.
    """

    @staticmethod
    def _spy(monkeypatch):
        import os
        import subprocess

        calls = []
        real = subprocess.run

        def recording(argv, *args, **kwargs):
            normalised = list(argv)
            if normalised[:1] == ["git"] and normalised[1:2] == ["-C"]:
                try:
                    if os.path.realpath(normalised[2]) == os.path.realpath(os.getcwd()):
                        normalised = ["git"] + normalised[3:]
                except OSError:
                    pass
            if normalised[:1] == ["git"]:
                calls.append(tuple(normalised))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", recording)
        return calls

    @staticmethod
    def _repeats(calls):
        from collections import Counter

        return {argv: n for argv, n in Counter(calls).items() if n > 1}

    def test_a_linked_worktree_detection(self, identity_env, monkeypatch):
        main = make_repo(identity_env / "main", remote=REMOTE)
        worktree = make_worktree(main, identity_env / "wt")
        monkeypatch.chdir(worktree)
        calls = self._spy(monkeypatch)
        assert PACTMemory._detect_project_identity_with_legacy().project_id == CANONICAL
        assert calls and self._repeats(calls) == {}

    def test_a_save_from_inside_the_repository(self, identity_env, monkeypatch):
        from unittest.mock import patch

        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        calls = self._spy(monkeypatch)
        with patch("scripts.memory_api._ensure_ready"):
            memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
            memory.save({"context": "x"}, sync_to_claude=False)
        assert calls and self._repeats(calls) == {}


class TestTheDivergenceCheckFollowsTheWorkingDirectory:
    """A save after a change of directory compares against the new directory.

    Catches the cwd repository cached at construction being reused after the
    process moved into another repository: the save is filed under the first
    repository's id from inside the second, and the divergence warning that
    names exactly that is lost.
    """

    OTHER = "https://github.com/o/other.git"

    def _memory(self, identity_env):
        return PACTMemory(session_id="s", db_path=identity_env / "store.db")

    def test_a_save_after_moving_into_another_repository_warns(
        self, identity_env, monkeypatch, caplog
    ):
        from unittest.mock import patch

        _fresh_scope_state()
        first = make_repo(identity_env / "proj", remote=REMOTE)
        second = make_repo(identity_env / "other", remote=self.OTHER)
        monkeypatch.chdir(first)
        with patch("scripts.memory_api._ensure_ready"):
            memory = self._memory(identity_env)
            assert memory.project_id == CANONICAL
            monkeypatch.chdir(second)
            caplog.clear()
            memory.save({"context": "x"}, sync_to_claude=False)
        warnings = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith("location divergence")
        ]
        assert len(warnings) == 1, caplog.text
        assert "github.com/o/other" in warnings[0]
        assert memory._last_project_scope["location_divergence"] is True

    def test_an_answer_cached_before_the_move_is_not_reused_after_it(
        self, identity_env, monkeypatch, caplog
    ):
        """The first repository's identity is CACHED by a check made there;
        after the move both cached answers, root and id, must be dropped."""
        from unittest.mock import patch

        _fresh_scope_state()
        first = make_repo(identity_env / "proj", remote=REMOTE)
        second = make_repo(identity_env / "other", remote=self.OTHER)
        monkeypatch.chdir(first)
        with patch("scripts.memory_api._ensure_ready"):
            memory = self._memory(identity_env)
            assert memory._location_divergence_warning(CANONICAL) is None
            assert memory._cwd_repo_id == CANONICAL, "precondition: id cached"
            monkeypatch.chdir(second)
            caplog.clear()
            memory.save({"context": "x"}, sync_to_claude=False)
        assert memory._last_project_scope["location_divergence"] is True, caplog.text

    def test_without_a_move_the_check_asks_git_nothing(
        self, identity_env, monkeypatch, caplog
    ):
        """Control: the cached answers stand, so the check costs no git call
        and finds no divergence."""
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        memory = self._memory(identity_env)
        calls = TestOneDetectionAsksGitEachQuestionOnce._spy(monkeypatch)
        assert memory._location_divergence_warning(CANONICAL) is None
        assert calls == []


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

    def test_a_broken_marker_decline_names_where_the_marker_is(self, identity_env, monkeypatch, caplog):
        """From a folder below a broken `.git` marker, with nothing declared,
        the decline names the marker's path, which is in a parent."""
        broken = self._broken(identity_env)
        (broken / "sub").mkdir()
        monkeypatch.chdir(broken / "sub")
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        warnings = [r.getMessage() for r in caplog.records if "so no project id was derived" in r.getMessage()]

        assert (detected.decline_reason["reason"], len(warnings)) == ("broken_git_marker", 1), warnings
        assert f"the `.git` marker is {str(broken / '.git')!r}" in warnings[0], warnings

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


class TestAnUnpinnableDeclineOffersNoPin:
    """A decline with no override path never prints a pin as its remedy.

    Catches a warning that falls back to "write an id to
    <git-common-dir>/pact-project-id" for a cause no pin can fix: the
    operator writes a file that changes nothing, or one that belongs to
    another repository.
    """

    def _rig(self, identity_env, monkeypatch, reason):
        if reason == "broken_git_marker":
            broken = identity_env / "broken"
            broken.mkdir()
            (broken / ".git").write_text("gitdir: /nonexistent/admin\n", encoding="utf-8")
            monkeypatch.chdir(broken)
        elif reason == "no_working_tree":
            bare = identity_env / "bare.git"
            run_git("init", "-q", "--bare", str(bare), cwd=identity_env)
            monkeypatch.chdir(bare)
        elif reason == "declaration_unresolved":
            plain = identity_env / "plain"
            plain.mkdir()
            monkeypatch.chdir(plain)
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(identity_env / "gone"))
        else:
            victim = make_repo(
                identity_env / "victim", remote="https://github.com/victim/secret.git"
            )
            make_worktree(victim, identity_env / "victim-wt")
            forged = identity_env / "forged"
            forged.mkdir()
            (forged / ".git").write_text(
                f"gitdir: {victim / '.git' / 'worktrees' / 'victim-wt'}\n",
                encoding="utf-8",
            )
            monkeypatch.chdir(forged)

    @pytest.mark.parametrize(
        "reason",
        ["broken_git_marker", "no_working_tree", "declaration_unresolved", "gitfile_redirect"],
    )
    def test_no_pin_is_offered(self, identity_env, monkeypatch, caplog, reason):
        self._rig(identity_env, monkeypatch, reason)
        with caplog.at_level(logging.WARNING):
            detected = PACTMemory._detect_project_identity_with_legacy()
        assert detected.decline_reason["reason"] == reason
        assert detected.decline_reason["override_path"] is None
        messages = [record.getMessage() for record in caplog.records]
        assert messages, "no decline warning was logged"
        for message in messages:
            assert "pact-project-id" not in message, message
            assert "<git-common-dir>" not in message, message


# ---------------------------------------------------------------------------
# A delivered checkout can neither create nor forge legacy-admission evidence
# ---------------------------------------------------------------------------

LEGACY_ID = "a" * 32


def _bucket_counts(store: Path) -> dict:
    from scripts.database import db_connection

    with db_connection(store) as conn:
        return dict(
            conn.execute(
                "SELECT COALESCE(project_id, ''), COUNT(*) FROM memories GROUP BY 1"
            ).fetchall()
        )


class TestACanonicalSessionWritesNoFolderNameBucket:
    """A repository session cannot file into, or relabel, a folder-name bucket.

    Catches the evidence manufacture: a delivered checkout under the user's
    folder name saves one memory with `project_id` set to that name, and its
    next session finds its own session id in the user's legacy bucket and is
    admitted to read it.
    """

    def _repo(self, identity_env, monkeypatch, name="proj", remote=REMOTE):
        _fresh_scope_state()
        repo = make_repo(identity_env / name, remote=remote)
        monkeypatch.chdir(repo)
        return repo

    def test_a_save_into_the_legacy_bucket_is_refused_and_writes_nothing(
        self, identity_env, monkeypatch, capfd
    ):
        import json
        from scripts.cli import main

        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj", session_id="user-session")
        before = _bucket_counts(store)
        with pytest.raises(SystemExit) as exit_info:
            main([
                "save", json.dumps({"context": "x", "project_id": "proj"}),
                "--db-path", str(store),
            ])
        assert exit_info.value.code == 2
        assert "folder-name bucket" in capfd.readouterr().err
        assert _bucket_counts(store) == before

    def test_the_refused_save_leaves_the_bucket_unproven(
        self, identity_env, monkeypatch
    ):
        """The auditor's rig end to end: the next session is still not admitted."""
        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj", session_id="user-session")
        with pytest.raises(ValueError):
            PACTMemory(session_id="attacker", db_path=store).save(
                {"context": "x", "project_id": "proj"}, sync_to_claude=False
            )
        _fresh_scope_state()
        memory = PACTMemory(session_id="attacker", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["unproven"] is True

    def test_an_update_into_the_legacy_bucket_is_refused(self, identity_env, monkeypatch):
        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        memory = PACTMemory(session_id="s", db_path=store)
        memory_id = memory.save({"context": "mine"}, sync_to_claude=False)
        with pytest.raises(ValueError, match="folder-name bucket"):
            memory.update(memory_id, {"project_id": "proj"})
        assert _bucket_counts(store) == {CANONICAL: 1}

    def test_relabelling_a_legacy_rows_session_is_refused(
        self, identity_env, monkeypatch
    ):
        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        # A FULL 32-character id resolves by id, outside any read scope.
        _seed(store, "proj", session_id="user-session", memory_id=LEGACY_ID)
        memory = PACTMemory(session_id="attacker", db_path=store)
        with pytest.raises(ValueError, match="folder-name bucket"):
            memory.update(LEGACY_ID, {"session_id": "attacker"})
        from scripts.database import db_connection

        with db_connection(store) as conn:
            assert conn.execute(
                "SELECT session_id FROM memories WHERE id = ?", (LEGACY_ID,)
            ).fetchone()[0] == "user-session"

    @pytest.mark.parametrize("argv_tail", [
        ["save", '{"context": "x", "project_id": "proj"}'],
        ["update", LEGACY_ID, '{"session_id": "attacker"}'],
        ["update", "MINE", '{"project_id": "proj"}'],
    ], ids=["save", "relabel", "update"])
    def test_the_rendered_refusal_carries_no_escaped_angle_bracket(
        self, identity_env, monkeypatch, capfd, argv_tail
    ):
        """The envelope escaper renders `<` as `<U+003C>`, so a placeholder
        written with angle brackets reaches the operator mangled."""
        from scripts.cli import main

        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        _seed(store, "proj", session_id="user-session", memory_id=LEGACY_ID)
        if "MINE" in argv_tail:
            mine = PACTMemory(session_id="s", db_path=store).save(
                {"context": "mine"}, sync_to_claude=False
            )
            argv_tail = [mine if arg == "MINE" else arg for arg in argv_tail]
        capfd.readouterr()
        with pytest.raises(SystemExit) as exit_info:
            main([*argv_tail, "--db-path", str(store)])
        captured = capfd.readouterr()
        rendered = captured.out + captured.err
        assert exit_info.value.code == 2, rendered
        assert "--to=host/owner/repo" in rendered, rendered
        assert "U+003C" not in rendered, rendered

    def test_a_content_update_of_a_legacy_row_still_works(
        self, identity_env, monkeypatch
    ):
        self._repo(identity_env, monkeypatch)
        store = identity_env / "store.db"
        # A FULL 32-character id resolves by id, outside any read scope.
        _seed(store, "proj", session_id="user-session", memory_id=LEGACY_ID)
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory.update(LEGACY_ID, {"context": "edited"}) is not None

    def test_a_no_origin_repository_still_writes_its_folder_name_bucket(
        self, identity_env, monkeypatch
    ):
        """Control: a basename-identity session keeps the pre-change behaviour."""
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj")
        monkeypatch.chdir(repo)
        store = identity_env / "store.db"
        memory = PACTMemory(session_id="s", db_path=store)
        memory.save({"context": "x", "project_id": "proj"}, sync_to_claude=False)
        assert _bucket_counts(store) == {"proj": 1}


class TestAnAncestorRootDoesNotClaimANestedCheckout:
    """A row belongs to the checkout nearest to it, not to every root above it.

    Catches an identity root that is an ANCESTOR of another checkout counting
    that checkout's absolute rows as contained: a repository unpacked into the
    parent folder of the user's checkout verified, and read, its legacy bucket.
    """

    def _outer_over_victim(self, root):
        """The unpacked repository's `.git` points at a git dir named `proj`,
        so its legacy candidate is the victim's folder name."""
        outer = root / "work"
        make_separate_git_dir_repo(outer, outer / "evil" / "proj" / "gd")
        run_git("remote", "add", "origin", REMOTE, cwd=outer)
        victim = make_repo(outer / "proj", remote="https://github.com/me/proj.git")
        return outer, victim

    def test_the_nested_checkouts_rows_are_not_contained(self, identity_env, monkeypatch):
        _fresh_scope_state()
        outer, victim = self._outer_over_victim(identity_env)
        (victim / "src").mkdir()
        store = identity_env / "store.db"
        _seed(store, "proj", path=str(victim / "src" / "a.py"))
        monkeypatch.chdir(outer)
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._legacy_project_basename == "proj", "precondition: the victim's name"
        assert memory._read_project_scopes() == (CANONICAL,)
        state = memory._legacy_scope_state
        assert state["verified"] is False and state["included"] is False

    @staticmethod
    def _gate(store, bucket, roots, **kwargs):
        from scripts.database import db_connection, legacy_candidate_plausibility

        with db_connection(store) as conn:
            return legacy_candidate_plausibility(conn, bucket, roots, **kwargs)

    def test_a_registered_submodules_rows_stay_the_superprojects(self, identity_env):
        """A superproject session that saved a memory naming a file inside its
        own submodule: that row is the superproject's, so its bucket still
        verifies. Catches the nested-checkout rule excluding a genuine
        superproject bucket whose rows all sit in submodules."""
        from scripts.database import LEGACY_PLAUSIBLE_VERIFIED

        source = make_repo(identity_env / "subsrc")
        superproject = make_repo(identity_env / "super", remote=REMOTE)
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        store = identity_env / "store.db"
        _seed(store, "super", path=str(superproject / "vendor" / "lib" / "x.py"))
        common = run_git(
            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=superproject
        )
        outcome, detail = self._gate(store, "super", [superproject], root_common=common)
        assert outcome == LEGACY_PLAUSIBLE_VERIFIED, detail

    def test_a_nested_gitfile_naming_a_foreign_git_dir_is_a_separate_checkout(
        self, identity_env
    ):
        """A `.git` FILE that names a git dir outside the root's modules (a
        linked worktree of another repository, say) still ends the root's
        claim: only the root's own submodules are exempt."""
        from scripts.database import LEGACY_PLAUSIBLE_EXCLUDED

        other = make_repo(identity_env / "other", remote="https://github.com/o/other.git")
        outer = make_repo(identity_env / "outer", remote=REMOTE)
        worktree = make_worktree(other, outer / "proj")
        assert (worktree / ".git").is_file()
        store = identity_env / "store.db"
        _seed(store, "outer", path=str(worktree / "x.py"))
        outcome, detail = self._gate(store, "outer", [outer])
        assert outcome == LEGACY_PLAUSIBLE_EXCLUDED, detail

    def test_a_row_in_the_roots_own_tree_still_verifies(self, identity_env):
        """Control: a nested plain directory (no `.git`) is still the root's."""
        from scripts.database import LEGACY_PLAUSIBLE_VERIFIED

        repo = make_repo(identity_env / "proj", remote=REMOTE)
        (repo / "a" / "b").mkdir(parents=True)
        store = identity_env / "store.db"
        _seed(store, "proj", path=str(repo / "a" / "b" / "c.py"))
        outcome, detail = self._gate(store, "proj", [repo])
        assert outcome == LEGACY_PLAUSIBLE_VERIFIED, detail


class TestTheScopeSaysWhatDegradesAndWhatRaises:
    """`_read_project_scopes` degrades only for a store with no schema, and
    raises for a present store it cannot open.

    Catches the disclosure calling an unreadable store "degraded" (the
    caller would then read the canonical scope alone and hide every legacy
    row the store holds), and comments that describe a decline record the
    code no longer builds.
    """

    def test_a_schema_less_store_degrades_and_says_why(self, identity_env, monkeypatch, caplog):
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        store = identity_env / "store.db"
        sqlite3_module = memory_api.sqlite3
        sqlite3_module.connect(str(store)).close()
        monkeypatch.chdir(repo)
        memory = PACTMemory(session_id="s", db_path=store)
        caplog.clear()
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["degraded"] == "no schema"
        warnings = [r.getMessage() for r in caplog.records if "could not be checked" in r.getMessage()]
        assert len(warnings) == 1 and "has no schema yet" in warnings[0], warnings
        assert "unreadable" not in warnings[0]

    def test_a_present_store_that_is_not_a_database_raises(self, identity_env, monkeypatch):
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        store = identity_env / "store.db"
        store.write_bytes(b"not a sqlite store, " * 8)
        monkeypatch.chdir(repo)
        memory = PACTMemory(session_id="s", db_path=store)
        with pytest.raises(memory_api.StoreFaultError):
            memory._read_project_scopes()

    def test_the_source_describes_the_decline_record_it_builds(self):
        source = Path(memory_api.__file__).read_text(encoding="utf-8")
        assert "{reason, override}" not in source
        doc = PACTMemory._read_project_scopes.__doc__
        assert "StoreFaultError" in doc and "uninitialized or unreadable store" not in doc


class TestTheWorkingMemoryCommentNamesWhenTheFolderNameKeys:
    """The managed comment names every way the projection is keyed, and the
    declines.

    Catches the comment telling a reader that any checkout without a
    parseable origin falls back to its folder name ("otherwise by folder
    name"), when the override file keys it canonically, a submodule with a
    local-path origin declines, and a broken, redirected or bare `.git`
    declines with nothing written.
    """

    CLAUSES = (
        "from the repository's override file or its origin remote",
        "when there is no override file",
        "except in a submodule",
        "including a broken, redirected or bare .git",
        "the identity declines and nothing is written",
        "by the project_id in the memory config.json when no project directory "
        "is declared and no repository is found",
        "or when git cannot see a home-directory checkout",
        "by folder name for a checkout that is the home directory",
    )

    @staticmethod
    def _copies():
        from scripts.working_memory import WORKING_MEMORY_COMMENT as script_copy
        from shared.claude_md_manager import WORKING_MEMORY_COMMENT as hook_copy

        return (script_copy, hook_copy)

    @pytest.mark.parametrize("clause", CLAUSES)
    def test_both_copies_carry_the_clause(self, clause):
        for copy in self._copies():
            assert clause in copy, copy

    def test_neither_copy_keeps_the_old_fallback(self):
        for copy in self._copies():
            assert "otherwise by folder name" not in copy, copy


# ---------------------------------------------------------------------------
# Update ordering, the evidence scan's cost, and prose
# ---------------------------------------------------------------------------

def _insert_row(store: Path, memory_id: str, project_id) -> None:
    from scripts.database import db_connection, ensure_initialized

    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES (?, 'row', ?, 'old', ?, ?)",
            (memory_id, project_id, CREATED_AT, CREATED_AT),
        )
        conn.commit()


class TestUpdateFindsTheRowBeforeItRefuses:
    """`update` reports a missing row as not found before any ingress
    refusal, and the folder-name refusal covers folder-name buckets only.

    Catches a full id that resolves without a query reaching the refusal
    first (a missing row refused with a folder-name message, exit 2 instead
    of not found), and a NULL-project row refused as if it were a
    folder-name bucket.
    """

    MISSING = "d" * 32

    def _memory(self, identity_env, monkeypatch, rows=()):
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        store = identity_env / "store.db"
        for memory_id, project_id in rows:
            _insert_row(store, memory_id, project_id)
        return PACTMemory(session_id="s", db_path=store), store

    @pytest.mark.parametrize(
        "updates", [{"session_id": "new"}, {"project_id": "proj"}],
        ids=["session_id", "folder-name project_id"],
    )
    def test_a_missing_row_is_not_found_not_refused(
        self, identity_env, monkeypatch, updates
    ):
        memory, _store = self._memory(identity_env, monkeypatch)
        assert memory.project_id == CANONICAL
        assert memory.update(self.MISSING, updates) is None

    def test_a_null_project_rows_session_can_change(self, identity_env, monkeypatch):
        from scripts.database import db_connection, get_memory

        row = "b" * 32
        memory, store = self._memory(identity_env, monkeypatch, [(row, None)])
        assert memory.update(row, {"session_id": "new"}) == row
        with db_connection(store) as conn:
            assert get_memory(conn, row)["session_id"] == "new"

    def test_a_folder_name_rows_session_is_still_refused(
        self, identity_env, monkeypatch
    ):
        row = "a" * 32
        memory, _store = self._memory(identity_env, monkeypatch, [(row, "proj")])
        with pytest.raises(ValueError, match="folder-name bucket 'proj'"):
            memory.update(row, {"session_id": "new"})

    def test_filing_an_existing_row_into_a_folder_name_is_still_refused(
        self, identity_env, monkeypatch
    ):
        row = "c" * 32
        memory, _store = self._memory(identity_env, monkeypatch, [(row, CANONICAL)])
        with pytest.raises(ValueError, match="update to project_id 'proj'"):
            memory.update(row, {"project_id": "proj"})


class TestTheEvidenceCheckScansEachRootOnce:
    """The session-evidence check lists each root's session folder once per
    command and parses an unchanged prune ledger once per process.

    Catches the evidence check building and resolving a session path for
    every writing session, and re-reading the ledger on every read while a
    bucket stays unproven (measured 0.41 s at 1,000 sessions x 5,000 ledger
    lines).
    """

    SESSIONS = 40

    def _rig(self, identity_env, monkeypatch):
        from scripts.database import db_connection, ensure_initialized
        from shared.pact_context import PRUNE_LEDGER_NAME, reconstruct_session_dir

        _fresh_scope_state()
        getattr(memory_api, "_PRUNED_SESSION_INDEX", {}).clear()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        store = identity_env / "store.db"
        with db_connection(store) as conn:
            ensure_initialized(conn)
            conn.executemany(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'x', 'proj', ?, ?, ?)",
                [(f"{i:032x}", f"sess-{i}", CREATED_AT, CREATED_AT)
                 for i in range(self.SESSIONS)],
            )
            conn.commit()
        slug_dir = Path(reconstruct_session_dir(str(repo), "sess-0")).parent
        slug_dir.mkdir(parents=True, exist_ok=True)
        ledger = slug_dir / PRUNE_LEDGER_NAME
        ledger.write_text(
            '{"session_id": "other", "project_dir": "/nowhere", '
            '"started_at": "2026-01-01T00:00:00+00:00"}\n',
            encoding="utf-8",
        )
        return repo, store, slug_dir, ledger

    def test_one_session_path_is_built_per_root_not_per_session(
        self, identity_env, monkeypatch
    ):
        import shared.pact_context as pact_context

        _repo, store, _slug, _ledger = self._rig(identity_env, monkeypatch)
        calls = []
        real = pact_context.reconstruct_session_dir

        def spy(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(pact_context, "reconstruct_session_dir", spy)
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["unproven"]
        assert 1 <= len(calls) <= 2, f"{len(calls)} session paths for {self.SESSIONS} sessions"

    def test_an_unchanged_ledger_is_parsed_once_per_process(
        self, identity_env, monkeypatch
    ):
        _repo, store, _slug, ledger = self._rig(identity_env, monkeypatch)
        parses = []
        real = memory_api._pruned_session_records

        def spy(path):
            parses.append(path)
            return real(path)

        monkeypatch.setattr(memory_api, "_pruned_session_records", spy)
        for _ in range(2):
            _fresh_scope_state()
            PACTMemory(session_id="s", db_path=store)._read_project_scopes()
        assert len(parses) == 1, parses
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write('{"session_id": "late", "project_dir": "/x", "started_at": "y"}\n')
        _fresh_scope_state()
        PACTMemory(session_id="s", db_path=store)._read_project_scopes()
        assert len(parses) == 2, "an appended line must be seen on the next read"

    def test_each_session_folder_is_listed_once_per_pass(
        self, identity_env, monkeypatch
    ):
        """Repeated reads and instances in one detection pass (one command)
        list the root's session folder exactly once: never zero (each session
        checked on its own) and never again for an unchanged store. The next
        pass lists it again."""
        import os

        _repo, store, slug_dir, _ledger = self._rig(identity_env, monkeypatch)
        real = os.listdir
        listed = []

        def spy(path="."):
            listed.append(os.path.realpath(str(path)))
            return real(path)

        monkeypatch.setattr(memory_api.os, "listdir", spy)
        with memory_api.detection_pass():
            for _ in range(3):
                memory = PACTMemory(session_id="s", db_path=store)
                for _ in range(3):
                    memory._read_project_scopes()
        assert listed == [os.path.realpath(str(slug_dir))], listed
        with memory_api.detection_pass():
            PACTMemory(session_id="s", db_path=store)._read_project_scopes()
        assert listed == [os.path.realpath(str(slug_dir))] * 2, listed

    def test_a_present_entry_is_a_live_folder_not_a_reaped_one(
        self, identity_env, monkeypatch
    ):
        """An entry named like the session that is not a readable session
        folder is no evidence, and the ledger does not stand in for it; with
        the entry gone, the same ledger line is evidence. This is the
        per-session existence test the one listing replaced."""
        import json

        repo, store, slug_dir, ledger = self._rig(identity_env, monkeypatch)
        good = {"session_id": "sess-0", "project_dir": str(repo),
                "started_at": start_after_description(repo)}
        ledger.write_text(json.dumps(good) + "\n", encoding="utf-8")
        (slug_dir / "sess-0").write_text("not a folder", encoding="utf-8")
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        assert memory._legacy_scope_state["unproven"]
        (slug_dir / "sess-0").unlink()
        _fresh_scope_state()
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL, "proj")


class TestOneConstructionAsksGitEachQuestionOnce:
    """A construction and its first legacy read never run the same git
    command twice.

    Catches the detection pass and the first read each running
    `git rev-parse --git-common-dir` for the identity root: repeated
    identical subprocess work in one process. These arms run a plain
    repository on the declared and cwd routes; the same property over every
    own layout and every real route is pinned by test_identity_rung_parity.py's
    TestTheDocumentedGitCallFloor.
    """

    @pytest.mark.parametrize("route", ["declared", "cwd"])
    def test_no_git_command_repeats(self, identity_env, monkeypatch, route):
        import collections
        import subprocess

        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        if route == "declared":
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        store = identity_env / "store.db"
        _seed(store, "proj")
        calls = []
        real = subprocess.run

        def counting(argv, *args, **kwargs):
            if argv and Path(str(argv[0])).name == "git":
                calls.append((tuple(map(str, argv)), str(kwargs.get("cwd") or Path.cwd())))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting)
        memory = PACTMemory(session_id="s", db_path=store)
        memory._read_project_scopes()
        repeats = {call: n for call, n in collections.Counter(calls).items() if n > 1}
        assert not repeats, repeats
        common = [call for call in calls if "--git-common-dir" in call[0]]
        assert len(common) == 1, common

    def test_the_first_read_reuses_the_detection_worktree_listing(
        self, identity_env, monkeypatch
    ):
        """A linked worktree declared by CLAUDE_PROJECT_DIR: detection lists
        the main checkout's worktrees to check the pointer, and the first
        legacy read's gate takes that listing instead of running the same
        `worktree list` again. A later read of the same instance lists afresh."""
        import collections
        import subprocess

        _fresh_scope_state()
        main = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(main, identity_env / "wt")
        monkeypatch.chdir(identity_env)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        store = identity_env / "store.db"
        _seed(store, "proj")
        calls = []
        real = subprocess.run

        def counting(argv, *args, **kwargs):
            if argv and Path(str(argv[0])).name == "git":
                calls.append((tuple(map(str, argv)), str(kwargs.get("cwd") or Path.cwd())))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting)
        memory = PACTMemory(session_id="s", db_path=store)
        detection = list(calls)
        memory._read_project_scopes()
        listings = [call for call in calls if "worktree" in call[0]]
        assert len(listings) == 1 and listings == [c for c in detection if "worktree" in c[0]], calls
        assert memory._legacy_scope_state is not None  # control: the gate ran
        repeats = {call: n for call, n in collections.Counter(calls).items() if n > 1}
        assert not repeats, repeats
        _fresh_scope_state()
        calls.clear()
        memory._read_project_scopes()
        assert [call for call in calls if "worktree" in call[0]] == listings, calls

    def test_a_later_construction_asks_again(self, identity_env, monkeypatch):
        """The detection pass's answers belong to one construction."""
        assert memory_api._DETECTION_COMMON_DIRS.get() is None
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        PACTMemory(session_id="s", db_path=identity_env / "store.db")
        assert memory_api._DETECTION_COMMON_DIRS.get() is None

    def test_a_construction_takes_its_answers_from_detection_pass(self, identity_env, monkeypatch):
        """A construction detects inside one `detection_pass`, the only setup
        of the pass memos: the detection reads exactly the memos that pass
        set, and the instance keeps that pass's own common dirs and listings,
        not copies, for its first legacy read."""
        import contextlib

        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        real_pass = memory_api.detection_pass
        detect = PACTMemory._detect_project_identity_with_legacy
        passes, seen = [], []

        @contextlib.contextmanager
        def recording():
            with real_pass() as memos:
                passes.append(memos)
                yield memos

        def watching():
            seen.append(tuple(id(var.get()) for var in (
                memory_api._DETECTION_COMMON_DIRS, memory_api.project_identity.DETECTION_GIT_DIRS,
                memory_api._DETECTION_ANSWERS, memory_api._DETECTION_LISTINGS,
            )))
            return detect()

        monkeypatch.setattr(memory_api, "detection_pass", recording)
        monkeypatch.setattr(PACTMemory, "_detect_project_identity_with_legacy", staticmethod(watching))
        memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")

        memos = passes[0] if len(passes) == 1 else {}
        names = ("common_dirs", "git_dirs", "answers", "listings")
        assert (len(passes), seen) == (1, [tuple(id(memos.get(name)) for name in names)]), (passes, seen)
        assert (memory._detection_common_dirs is memos["common_dirs"], memory._detection_listings is memos["listings"]) \
            == (True, True)

    def test_a_construction_inside_a_pass_joins_it(self, identity_env, monkeypatch):
        """A construction inside a caller's pass (a backlog command) joins
        it: no git command runs twice across the caller's detection, the
        construction and its first legacy read, and the caller's pass keeps
        every answer it held, though that read takes the answers it reuses.
        A linked worktree declared by CLAUDE_PROJECT_DIR, so the pass holds
        a common dir and a worktree listing."""
        import collections
        import subprocess

        _fresh_scope_state()
        main = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(main, identity_env / "wt")
        monkeypatch.chdir(identity_env)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        store = identity_env / "store.db"
        _seed(store, "proj")
        calls = []
        real = subprocess.run

        def counting(argv, *args, **kwargs):
            if argv and Path(str(argv[0])).name == "git":
                calls.append((tuple(map(str, argv)), str(kwargs.get("cwd") or Path.cwd())))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting)
        with memory_api.detection_pass() as outer:
            PACTMemory._detect_project_identity_with_legacy()
            held = {name: dict(memo) for name, memo in outer.items()}
            memory = PACTMemory(session_id="s", db_path=store)
            memory._read_project_scopes()
            kept = {name: dict(memo) for name, memo in outer.items()}

        repeats = {call: n for call, n in collections.Counter(calls).items() if n > 1}
        lost = {name: sorted(set(held[name]) - set(kept[name])) for name in held}
        assert memory._legacy_scope_state is not None  # control: the gate ran
        assert (held["common_dirs"] != {}, held["listings"] != {}) == (True, True), held  # control: answers held
        assert (repeats, lost) == ({}, {name: [] for name in held}), calls

    def test_a_construction_joined_on_the_user_setting_leaves_the_pass_its_common_dirs(
        self, identity_env, monkeypatch
    ):
        """A construction inside a caller's pass whose identity records no
        common dir (the user setting, from a folder outside any repository)
        takes the folder's common-dir answer for its first legacy read from a
        copy, so the caller's pass keeps every answer it held. The control
        shows the route, that the pass held the folder's answer, and that the
        legacy read ran."""
        import os

        from scripts.database import create_memory, db_connection

        _fresh_scope_state()
        memory_dir = identity_env / "mem"
        memory_dir.mkdir()
        (memory_dir / "config.json").write_text('{"project_id": "user.example.com/me/pinned"}', encoding="utf-8")
        folder = identity_env / "my-workspace"
        folder.mkdir()
        (folder / "CLAUDE.md").write_text("x", encoding="utf-8")
        store = identity_env / "store" / "memory.db"
        store.parent.mkdir()
        with db_connection(str(store)) as conn:
            create_memory(conn, {"project_id": "my-workspace", "context": "legacy row", "session_id": "s0"})
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        monkeypatch.chdir(folder)
        monkeypatch.setattr(memory_api, "get_project_dir_from_session_record", lambda: None)

        with memory_api.detection_pass() as outer:
            memory = PACTMemory(db_path=store)
            held = dict(outer["common_dirs"])
            memory.list(limit=10)
            kept = dict(outer["common_dirs"])

        assert (memory._project_id_source, memory._project_common_dir, os.path.realpath(folder) in held,
                memory._legacy_scope_state is not None) == ("user setting", None, True, True)  # control
        assert kept == held

    def test_one_pass_keeps_a_roots_identity_per_directory_asked_from(self, identity_env):
        """A pass keeps a root's identity for each directory it was asked
        from: in one pass, a submodule checkout asked from a folder whose
        `.git` file names the submodule's git dir declines, and the same root
        asked from its own checkout reads its origin. The control shows the
        root and common dir the folder resolves to are the submodule's.

        Real repos, real git."""
        import subprocess

        _fresh_scope_state()
        victim = make_repo(identity_env / "victimlib", remote="https://github.com/victim/secret-lib")
        superproject = make_repo(identity_env / "super", remote="https://github.com/owner/super")
        subprocess.run(["git", "-C", str(superproject), "-c", "protocol.file.allow=always", "submodule", "add",
                        "-q", str(victim), "submod"], check=True, capture_output=True)
        submodule = (superproject / "submod").resolve()
        subprocess.run(["git", "-C", str(submodule), "remote", "set-url", "origin",
                        "https://github.com/victim/secret-lib"], check=True, capture_output=True)
        forged = identity_env / "forged-sub"
        forged.mkdir()
        admin = superproject / ".git" / "modules" / "submod"
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")

        with memory_api.detection_pass():
            root = memory_api.main_repo_root(str(forged))
            common = memory_api.main_repo_common_dir(str(forged))
            answers = [
                PACTMemory._identity_at_root(Path(root), common, asked_root=asked, source="probe", label="probe")
                for asked in (forged, submodule)
            ]

        assert (Path(root).resolve(), Path(common).resolve()) == (submodule, admin.resolve())  # control
        assert [answer.project_id for answer in answers] == [None, "github.com/victim/secret-lib"]

    def test_leaving_a_detection_pass_restores_the_memos_it_found(self):
        """Leaving a detection pass, at the end of its block or by a raise
        in it, sets each pass memo back to what it held on entry: nothing
        outside every pass. A pass opened inside another joins it: inside
        and after it the memos are the outer pass's own. So no answer
        outlives the outermost pass that asked for it, whatever ran
        before."""
        import contextvars

        variables = (
            memory_api._DETECTION_COMMON_DIRS, memory_api.project_identity.DETECTION_GIT_DIRS,
            memory_api._DETECTION_ANSWERS, memory_api._DETECTION_LISTINGS,
        )

        def held():
            return [var.get() for var in variables]

        def raising_pass():
            inside = None
            try:
                with memory_api.detection_pass():
                    inside = held()
                    raise RuntimeError("the block raised")
            except RuntimeError:
                pass
            return inside

        def passes():
            outside = held()
            with memory_api.detection_pass():
                outer = held()
                with memory_api.detection_pass():
                    inner = held()
                after_inner = held()
                inner_raise = raising_pass()
                after_inner_raise = held()
            after_outer = held()
            raising_pass()
            return outside, outer, (inner, after_inner, inner_raise, after_inner_raise), after_outer, held()

        # A fresh context, so what earlier code left in the memos cannot
        # decide this arm.
        outside, outer, nested, after_outer, after_outer_raise = contextvars.Context().run(passes)

        assert (outside, [type(memo) for memo in outer]) == ([None] * 4, [dict] * 4)  # control
        assert (
            [[now is then for now, then in zip(memos, outer)] for memos in nested],
            after_outer, after_outer_raise,
        ) == ([[True] * 4] * 4, [None] * 4, [None] * 4)


class TestTheGateProseMatchesTheCode:
    """Docstrings and help text state the current admission and rekey rules.

    Catches prose that still says an unverified bucket is included, that an
    excluded bucket prints a rekey command, that a defaulted `--to` needs
    only a non-excluded bucket, or that detection falls back to the folder
    name after the git root.
    """

    def test_the_database_docstrings(self):
        from scripts import database

        gate = database.legacy_candidate_plausibility.__doc__
        assert "reads the bucket only\n                       on session evidence" in gate
        assert "the caller INCLUDES the bucket" not in gate
        assert "counts and no rekey command" in gate
        assert "counts and the rekey command" not in gate
        assert "`description` file was created" in database.bucket_session_ids.__doc__

    def test_the_cli_help_and_docstring(self):
        from scripts import cli

        source = Path(cli.__file__).read_text(encoding="utf-8")
        assert "bucket and it is neither excluded nor unproven. Run --apply only" in source
        assert "bucket and it is not excluded. Run --apply" not in source
        assert "only when that bucket is " in source and "neither excluded nor unproven." in source
        assert "own legacy candidate that is neither excluded nor\n    unproven" in cli._project_id_rekey.__doc__
        assert "every populated bucket with its rekey command." not in source

    def test_the_memory_api_docstrings(self):
        init = PACTMemory.__init__.__doc__
        assert "the user-scope setting (only\n                        with no declaration" in init
        assert "git repo root, or current working" not in init
        # The counts themselves are measured against this table by
        # test_identity_rung_parity.py's TestTheDocumentedGitCallFloor.
        assert "Measured git subprocesses per detection, by layout" in init
        scopes = PACTMemory._read_project_scopes.__doc__
        assert "An excluded or\n        unproven bucket is not read." in scopes
        assert "the plausibility gate does not exclude it." not in scopes
        assert "the inclusion is disclosed\n    as unverified" not in MEMORY_API.read_text(encoding="utf-8")

    def test_the_redirect_remedy_names_no_pin(self):
        """A forged `.git` pointer looks exactly like a user's own
        `--separate-git-dir` layout, so the printed remedy names no override
        path and asks no reader to write one: pinning one's own layout is the
        user's decision, described in the memory skill."""
        remedy = memory_api._REDIRECT_REMEDY
        assert "pact-project-id" not in remedy
        assert "write" not in remedy.lower()
        assert "user decision: see the pact-memory skill" in remedy

class TestNoPrintedPinPathInAnotherRepository:
    """No warning, refusal or decline record prints an override path that
    lies in a git directory the checkout points at.

    Catches a remedy whose safe use depends on an ownership judgement the
    reader cannot make: a forged `.git` pointer and a user's own
    `--separate-git-dir` store are the same shape, so a printed path there
    can lead an agent to re-pin a repository it does not own. A checkout
    whose `.git` is its own directory keeps its pin offer.
    """

    BAD_REMOTE = "https://bad host.example/x/y.git"

    def _messages(self, identity_env, monkeypatch, asked, caplog):
        _fresh_scope_state()
        monkeypatch.chdir(asked)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(asked))
        with caplog.at_level(logging.WARNING):
            memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
            try:
                memory.save({"context": "x"})
                refusal = ""
            except Exception as exc:
                refusal = str(exc)
        texts = [record.getMessage() for record in caplog.records] + [refusal]
        return memory, texts

    def test_a_forged_redirect_prints_no_path_into_the_victim(
        self, identity_env, monkeypatch, caplog
    ):
        victim = make_repo(identity_env / "victim", remote=REMOTE)
        (victim / ".git" / "pact-project-id").write_text("not-canonical\n", encoding="utf-8")
        asked = identity_env / "forged"
        asked.mkdir()
        (asked / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        memory, texts = self._messages(identity_env, monkeypatch, asked, caplog)
        assert memory._decline_reason["reason"] == "gitfile_redirect"
        assert memory._decline_reason["override_path"] is None
        # The victim's pin is unusable: its REASON is reported, its path is not.
        assert memory._override_ignored == {"path": None, "reason": "not_canonical"}
        for text in texts:
            assert "pact-project-id" not in text, text
            assert str(victim) not in text, text

    def test_an_own_separate_git_dir_layout_prints_no_path_either(
        self, identity_env, monkeypatch, caplog
    ):
        """The own-layout route reads the same as the forged one."""
        asked = make_separate_git_dir_repo(
            identity_env / "proj", identity_env / "stores" / "proj" / ".git"
        )
        run_git("remote", "add", "origin", REMOTE, cwd=asked)
        memory, texts = self._messages(identity_env, monkeypatch, asked, caplog)
        assert memory._decline_reason["reason"] == "gitfile_redirect"
        for text in texts:
            assert "pact-project-id" not in text, text

    def test_an_external_git_dir_with_a_bad_origin_names_the_skill_not_a_path(
        self, identity_env, monkeypatch, caplog
    ):
        asked = make_separate_git_dir_repo(
            identity_env / "proj", identity_env / "stores" / "store.git"
        )
        run_git("remote", "add", "origin", self.BAD_REMOTE, cwd=asked)
        memory, texts = self._messages(identity_env, monkeypatch, asked, caplog)
        assert memory._decline_reason["reason"] == "origin_unrepresentable"
        assert memory._decline_reason["override_path"] is None
        for text in texts:
            assert "pact-project-id" not in text, text
        assert any("pinning it is a user decision" in text for text in texts), texts

    def test_a_checkouts_own_git_dir_keeps_its_pin_offer(
        self, identity_env, monkeypatch, caplog
    ):
        """Control: a `.git` directory at the root is the checkout's own, so
        its override path is still printed as the remedy."""
        asked = make_repo(identity_env / "proj", remote=self.BAD_REMOTE)
        memory, texts = self._messages(identity_env, monkeypatch, asked, caplog)
        own = str(asked / ".git" / "pact-project-id")
        assert memory._decline_reason["override_path"] == own
        assert any(own in text for text in texts), texts

    def test_a_linked_worktree_keeps_its_pin_offer(
        self, identity_env, monkeypatch, caplog
    ):
        """Control: a linked worktree's `.git` file points at its own
        repository's admin dir, so the shared override path is printed."""
        main = make_repo(identity_env / "main", remote=self.BAD_REMOTE)
        worktree = make_worktree(main, identity_env / "wt")
        memory, texts = self._messages(identity_env, monkeypatch, worktree, caplog)
        assert memory._decline_reason["override_path"] is not None
        assert any("pact-project-id" in text for text in texts), texts


class TestAWithheldPinPathStillReportsItsReason:
    """Wherever the override path is withheld, an unusable pin file there is
    still reported by its reason.

    Catches a disclosure dropped along with the value it was withholding:
    the path is held back on the redirect route and on every outcome of the
    external-git-dir route, and an unusable pin there was then ignored with
    no warning and no `override_ignored` field. The routes are enumerated
    from `project_identity`'s own origin states and outcomes, so a state
    added there without a case here fails the coverage arm.
    """

    PINS = {"folder-name": "sep\n", "empty": "", "symlink": None}

    @staticmethod
    def _origin_cases():
        """One `OriginRead` per origin state, and per classifier outcome for
        a present origin, keyed by (state, outcome)."""
        from scripts import project_identity as pi

        return {
            (pi.ORIGIN_ABSENT, None): pi.OriginRead(pi.ORIGIN_ABSENT, None),
            (pi.ORIGIN_UNAVAILABLE, None): pi.OriginRead(pi.ORIGIN_UNAVAILABLE, None),
            (pi.ORIGIN_EMPTY_ENTRY, None): pi.OriginRead(pi.ORIGIN_EMPTY_ENTRY, None),
            (pi.ORIGIN_NOT_OWN_FILE, None): pi.OriginRead(pi.ORIGIN_NOT_OWN_FILE, None),
            (pi.ORIGIN_INCLUDE_ONLY, None): pi.OriginRead(pi.ORIGIN_INCLUDE_ONLY, None),
            (pi.ORIGIN_CONFIG_UNREAD, None): pi.OriginRead(pi.ORIGIN_CONFIG_UNREAD, None),
            (pi.ORIGIN_PRESENT, pi.OUTCOME_CANONICAL): pi.OriginRead(
                pi.ORIGIN_PRESENT, "https://github.com/o/sep.git"
            ),
            (pi.ORIGIN_PRESENT, pi.OUTCOME_NO_PORTABLE_ORIGIN): pi.OriginRead(
                pi.ORIGIN_PRESENT, "/srv/local/sep.git"
            ),
            (pi.ORIGIN_PRESENT, pi.OUTCOME_DECLINE): pi.OriginRead(
                pi.ORIGIN_PRESENT, "https://bad host.example/x/y.git"
            ),
        }

    def test_every_origin_state_and_outcome_has_a_case(self):
        from scripts import project_identity as pi

        states = {getattr(pi, n) for n in dir(pi) if n.startswith("ORIGIN_") and n.isupper()}
        outcomes = {getattr(pi, n) for n in dir(pi) if n.startswith("OUTCOME_") and n.isupper()}
        cases = self._origin_cases()
        assert {state for state, _ in cases} == states
        assert {outcome for state, outcome in cases if state == pi.ORIGIN_PRESENT} == outcomes
        for (state, outcome), read in cases.items():
            if outcome is not None:
                assert pi.classify_remote_origin(read.value)[0] == outcome, read

    def _pin(self, git_dir: Path, kind: str, identity_env: Path) -> None:
        pin = git_dir / "pact-project-id"
        if kind == "symlink":
            target = identity_env / "elsewhere-pin"
            target.write_text("github.com/o/sep\n", encoding="utf-8")
            pin.symlink_to(target)
        else:
            pin.write_text(self.PINS[kind], encoding="utf-8")

    def _external(self, identity_env):
        repo = make_separate_git_dir_repo(
            identity_env / "sep", identity_env / "stores" / "sep.git"
        )
        return repo, identity_env / "stores" / "sep.git"

    def _assert_reported(self, identity, git_dir, caplog):
        from scripts import project_identity as pi

        expected = pi.read_override(git_dir).reason
        assert expected is not None, "the rig's pin must be unusable"
        assert identity.override_ignored == {"path": None, "reason": expected}
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            PACTMemory._warn_override_ignored(identity)
        texts = [record.getMessage() for record in caplog.records]
        assert any(expected in text for text in texts), texts
        for text in texts:
            assert "pact-project-id" not in text and str(git_dir) not in text, text

    @pytest.mark.parametrize("pin", sorted(PINS))
    @pytest.mark.parametrize(
        "case", sorted(_origin_cases.__func__().keys(), key=str), ids=str
    )
    def test_the_external_route_reports_the_reason(
        self, identity_env, monkeypatch, caplog, case, pin
    ):
        from scripts import project_identity as pi

        repo, git_dir = self._external(identity_env)
        self._pin(git_dir, pin, identity_env)
        read = self._origin_cases()[case]
        monkeypatch.setattr(pi, "read_origin", lambda root: read)
        identity = pi.detect_id_for_root(repo, git_dir, asked_root=repo)
        self._assert_reported(identity, git_dir, caplog)

    @pytest.mark.parametrize("pin", sorted(PINS))
    def test_the_redirect_route_reports_the_reason(
        self, identity_env, caplog, pin
    ):
        from scripts import project_identity as pi

        victim = make_repo(identity_env / "victim", remote=REMOTE)
        self._pin(victim / ".git", pin, identity_env)
        asked = identity_env / "forged"
        asked.mkdir()
        (asked / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        identity = pi.detect_id_for_root(asked, victim / ".git", asked_root=asked)
        assert identity.decline_reason["reason"] == "gitfile_redirect"
        self._assert_reported(identity, victim / ".git", caplog)

    def test_show_status_and_the_save_refusal_carry_the_reason(
        self, identity_env, monkeypatch, caplog
    ):
        """End to end, on a redirect: the envelope field `show` and `status`
        print, and the save refusal's sentence, carry the reason and no path."""
        _fresh_scope_state()
        victim = make_repo(identity_env / "victim", remote=REMOTE)
        (victim / ".git" / "pact-project-id").write_text("sep\n", encoding="utf-8")
        asked = identity_env / "forged"
        asked.mkdir()
        (asked / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        monkeypatch.chdir(asked)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(asked))
        memory = PACTMemory(session_id="s", db_path=identity_env / "store.db")
        assert memory._override_ignored == {"path": None, "reason": "not_canonical"}
        with pytest.raises(Exception) as refused:
            memory.save({"context": "x"})
        message = str(refused.value)
        assert "present but unusable (not_canonical)" in message, message
        assert "pact-project-id" not in message and str(victim) not in message


class TestTheGateTrustsOnlyLinkedWorktrees:
    """A worktree widens the gate only when it links back to the repository.

    Catches a delivered `.git` naming the victim's checkout in
    `.git/worktrees/<x>/gitdir`: that path became a gate root, and the
    victim's absolute file rows then VERIFIED its bucket with no evidence.
    """

    def test_a_forged_worktree_entry_adds_no_root(self, identity_env, monkeypatch):
        _fresh_scope_state()
        # The victim is a real checkout, so git lists the forged entry as live.
        victim = make_repo(identity_env / "victim" / "proj", remote="https://github.com/v/proj.git")
        delivered = make_repo(identity_env / "delivered" / "proj", remote=REMOTE)
        admin = delivered / ".git" / "worktrees" / "x"
        admin.mkdir(parents=True)
        (admin / "gitdir").write_text(f"{victim / '.git'}\n", encoding="utf-8")
        (admin / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        store = identity_env / "store.db"
        _seed(store, "proj", path=str(victim / "secret.py"))
        monkeypatch.chdir(delivered)
        assert memory_api._verified_worktree_roots(
            delivered, delivered / ".git"
        ) == [delivered.resolve()]
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        state = memory._legacy_scope_state
        assert state["verified"] is False and state["included"] is False

    def test_a_forged_entry_naming_another_repositorys_worktree_adds_no_root(
        self, identity_env, monkeypatch
    ):
        """The victim is a LINKED worktree, so its `.git` is a file and the
        forged entry passes the is-a-file check; only the requirement that
        the file point into THIS repository's worktrees dir refuses it."""
        _fresh_scope_state()
        victim_main = make_repo(identity_env / "victim" / "main", remote="https://github.com/v/proj.git")
        victim = make_worktree(victim_main, identity_env / "victim" / "proj")
        assert (victim / ".git").is_file()
        delivered = make_repo(identity_env / "delivered" / "proj", remote=REMOTE)
        admin = delivered / ".git" / "worktrees" / "x"
        admin.mkdir(parents=True)
        (admin / "gitdir").write_text(f"{victim / '.git'}\n", encoding="utf-8")
        (admin / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        store = identity_env / "store.db"
        _seed(store, "proj", path=str(victim / "secret.py"))
        monkeypatch.chdir(delivered)
        assert memory_api._verified_worktree_roots(
            delivered, delivered / ".git"
        ) == [delivered.resolve()]
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL,)
        state = memory._legacy_scope_state
        assert state["verified"] is False and state["included"] is False

    def test_an_entry_whose_admin_dir_does_not_name_it_back_adds_no_root(
        self, identity_env
    ):
        """W's `.git` points into this repository's worktrees dir, but that
        admin dir names another checkout: only the back-link refuses W."""
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(repo, identity_env / "proj-wt")
        admin = Path(
            (worktree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip()
        )
        (admin / "gitdir").write_text(
            f"{identity_env / 'elsewhere' / '.git'}\n", encoding="utf-8"
        )
        forged = repo / ".git" / "worktrees" / "x"
        forged.mkdir()
        (forged / "gitdir").write_text(f"{worktree / '.git'}\n", encoding="utf-8")
        (forged / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (forged / "commondir").write_text("../..\n", encoding="utf-8")
        assert memory_api._verified_worktree_roots(repo, repo / ".git") == [
            repo.resolve()
        ]

    def test_a_genuine_worktree_still_widens_the_gate(self, identity_env, monkeypatch):
        _fresh_scope_state()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(repo, identity_env / "proj-wt")
        store = identity_env / "store.db"
        _seed(store, "proj", path=str(worktree / "a.py"))
        monkeypatch.chdir(repo)
        assert worktree.resolve() in memory_api._verified_worktree_roots(
            repo, repo / ".git"
        )
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL, "proj")
        assert memory._legacy_scope_state["verified"] is True

    def test_a_pruned_worktree_adds_no_root(self, identity_env, monkeypatch):
        import shutil

        repo = make_repo(identity_env / "proj", remote=REMOTE)
        worktree = make_worktree(repo, identity_env / "proj-wt")
        shutil.rmtree(worktree)
        assert memory_api._verified_worktree_roots(repo, repo / ".git") == [
            repo.resolve()
        ]


class TestTheLegacyProbeLetsTheCallersErrorThrough:
    """An error raised inside the probe's block is the caller's, unchanged.

    Catches a yield inside the probe's `try`: the caller's OperationalError is
    thrown in there, caught by the probe's own handler, and replaced.
    """

    def test_a_callers_operational_error_propagates(self, identity_env):
        # THE STORE'S OWN sqlite module (pysqlite3 when installed): a stdlib
        # OperationalError is a different class and would never reach the
        # probe's handler at all.
        sqlite3 = memory_api.sqlite3

        store = identity_env / "zero.db"
        store.write_bytes(b"")
        # Shaped like the probe's own degraded-store error, so a handler that
        # wrongly catches it would treat it as the probe's and yield again.
        callers = "attempt to write a readonly database (caller)"
        with pytest.raises(sqlite3.OperationalError, match=r"\(caller\)$"):
            with memory_api._legacy_probe_context(str(store), None) as (conn, reason):
                assert conn is None and reason == "no schema"
                raise sqlite3.OperationalError(callers)


# A `git` that answers every command as the real one does, except `worktree
# list`: with REJECT_Z it rejects `-z` as git before 2.36 does (its `list`
# options carry no 'z', so parse_options prints "unknown switch `z'", then
# the usage, shortened here, and exits 129); with FAIL_LISTING every `worktree list`
# fails as git does on a repository it cannot read; with EMPTY_LISTING every
# `worktree list` prints nothing and exits 0; with
# FAIL_ABSOLUTE_GIT_DIR every `rev-parse --absolute-git-dir` fails; with
# FAIL_GIT_DIR every `rev-parse --git-dir` fails (the global `--git-dir <dir>`
# option before a command is answered); with FAIL_CORE_WORKTREE every read of
# `core.worktree` fails. With
# ECHO followed by
# a `rev-parse` option, it answers as a git that predates that option:
# rev-parse prints an option it does not know back as an argument, in its
# place, and answers the rest.
_GIT_WRAPPER = """#!/bin/sh
case "{mode}" in
  ECHO*)
    option="{mode}"
    option="${{option#ECHO}}"
    echoed=0
    for argument in "$@"; do
      shift
      if [ "$argument" = "$option" ]; then echoed=1; else set -- "$@" "$argument"; fi
    done
    if [ "$echoed" = 1 ]; then
      printf '%s\\n' "$option"
    fi
    ;;
esac
worktree=0; listing=0; nul=0
for argument in "$@"; do
  case "$argument" in
    worktree) worktree=1 ;;
    list) [ "$worktree" = 1 ] && listing=1 ;;
    -z) [ "$listing" = 1 ] && nul=1 ;;
  esac
done
if [ "{mode}" = FAIL_CORE_WORKTREE ]; then
  for argument in "$@"; do
    if [ "$argument" = core.worktree ]; then
      echo "fatal: cannot read" >&2
      exit 128
    fi
  done
fi
if [ "{mode}" = FAIL_GIT_DIR ]; then
  revparse=0
  for argument in "$@"; do
    case "$argument" in
      rev-parse) revparse=1 ;;
      --git-dir) if [ "$revparse" = 1 ]; then echo "fatal: cannot answer" >&2; exit 128; fi ;;
    esac
  done
fi
if [ "{mode}" = FAIL_ABSOLUTE_GIT_DIR ]; then
  for argument in "$@"; do
    if [ "$argument" = --absolute-git-dir ]; then
      echo "fatal: cannot answer" >&2
      exit 128
    fi
  done
fi
if [ "$listing" = 1 ] && [ "{mode}" = FAIL_LISTING ]; then
  echo "fatal: not a git repository: '.'" >&2
  exit 128
fi
if [ "$listing" = 1 ] && [ "{mode}" = EMPTY_LISTING ]; then
  exit 0
fi
if [ "$nul" = 1 ] && [ "{mode}" = REJECT_Z ]; then
  echo "error: unknown switch \\`z'" >&2
  echo "usage: git worktree list [<options>]" >&2
  echo "" >&2
  echo "    --porcelain           machine-readable output" >&2
  exit 129
fi
exec "{git}" "$@"
"""


def _git_on_path(monkeypatch, folder: Path, mode: str) -> None:
    """Put a `git` wrapper of `mode` first on PATH."""
    import shutil
    import stat

    real = shutil.which("git")
    assert real is not None
    folder.mkdir()
    wrapper = folder / "git"
    wrapper.write_text(_GIT_WRAPPER.format(mode=mode, git=real), encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    import os

    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def _route_answers(tmp_path, shape: str, mode: str) -> dict:
    """{route: (project id, decline reason, decline detail)} for `shape` on
    every route that is not forced, with the `git` wrapper of `mode`."""
    from fixtures.identity_routes import ROUTES, SHAPES, _clear

    tmp_path.mkdir(parents=True, exist_ok=True)

    answers = {}
    for index, (route, (run, _sources)) in enumerate(ROUTES.items()):
        if "forced" in route:
            continue
        base = tmp_path / f"cell{index}"
        base.mkdir()
        target = SHAPES[shape](base)
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        with pytest.MonkeyPatch.context() as cell:
            _clear(cell)
            _git_on_path(cell, base / "bin", mode)
            detected = run(cell, target, elsewhere)
        reason = (detected.decline_reason or {}).get("reason")
        answers[route] = (detected.project_id, reason, detected.decline_detail)
    return answers


class TestTheWorktreeCheckRefusesWithoutAListing:
    """The forged-worktree check reads git's worktree listing; a git that
    cannot give one never lets the pointer through."""

    def test_a_git_that_rejects_the_nul_listing_still_refuses_a_forged_pointer(self, tmp_path):
        """Before 2.36 git rejects `worktree list -z`; the listing is read
        without it, and the forged pointer declines on every route."""
        from fixtures.identity_routes import VICTIM_ID

        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, "forged_worktree_admin", "REJECT_Z")
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] == VICTIM_ID or answer[1] != "gitfile_redirect"
        }
        assert wrong == {}, f"a forged worktree pointer passed on an older git: {wrong}"

    def test_a_git_that_rejects_the_nul_listing_still_binds_a_real_worktree(self, tmp_path):
        """The listing without `-z` lists the real linked worktree, so it
        binds its own origin on every route."""
        from fixtures.identity_routes import OWN
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        own = pi.normalize_remote_url(OWN)
        answers = _route_answers(tmp_path, "control_own_linked_worktree", "REJECT_Z")
        wrong = {route: answer for route, answer in answers.items() if answer[0] != own}
        assert wrong == {}, f"a real worktree did not bind on an older git: {wrong}"

    def test_no_listing_refuses_and_says_git_could_not_list(self, tmp_path):
        """When git cannot list the worktrees at all, a linked-worktree
        pointer declines on every route, forged or real, and the detail says
        the listing could not be had rather than that a redirect was found."""
        from scripts import memory_api

        unlistable = memory_api._REDIRECT_CAUSE_DETAILS[memory_api.REDIRECT_UNLISTABLE]
        assert_outside_repository(tmp_path)
        for shape in ("forged_worktree_admin", "control_own_linked_worktree"):
            answers = _route_answers(tmp_path / shape, shape, "FAIL_LISTING")
            wrong = {
                route: answer for route, answer in answers.items()
                if answer[0] is not None or answer[1] != "gitfile_redirect"
                or answer[2] != unlistable
            }
            assert wrong == {}, f"{shape} passed or misreported without a listing: {wrong}"

    def test_a_path_the_listing_without_nul_cannot_hold_whole_refuses(self, tmp_path):
        """Without `-z` git prints a worktree path unquoted, one field per
        line, so a worktree whose folder holds a newline cannot be read
        whole: the listing is unreadable and the worktree declines on every
        route, saying git listed the worktrees but a path could not be read
        whole."""
        from scripts import memory_api

        unreadable = memory_api._REDIRECT_CAUSE_DETAILS[memory_api.REDIRECT_UNREADABLE_LISTING]
        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, "control_path_worktree_holding_a_newline", "REJECT_Z")
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] is not None or answer[1] != "gitfile_redirect"
            or answer[2] != unreadable
        }
        assert wrong == {}, f"a newline-holding worktree was read from a listing without NUL: {wrong}"


class TestTheOwnOriginBindsWhateverAnIncludeBeforeItSays:
    """An `includeIf onbranch:` include placed before the repository's own
    `remote.origin.url` is skipped: the own value binds on every route,
    whichever branch the main checkout is on, so switching branch does not
    move the id."""

    @pytest.mark.parametrize("switch_branch", [False, True])
    def test_every_route_binds_the_own_origin(self, tmp_path, switch_branch):
        from fixtures.identity_routes import OWN, ROUTES, SHAPES, _clear
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        own = pi.normalize_remote_url(OWN)
        answers = {}
        for index, (route, (run, _sources)) in enumerate(ROUTES.items()):
            if "forced" in route:
                continue
            base = tmp_path / f"cell{index}"
            base.mkdir()
            target = SHAPES["control_origin_after_an_onbranch_include"](base)
            if switch_branch:
                run_git("checkout", "-q", "-b", "elsewhere", cwd=base / "main")
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            with pytest.MonkeyPatch.context() as cell:
                _clear(cell)
                answers[route] = run(cell, target, elsewhere).project_id
        wrong = {route: answer for route, answer in answers.items() if answer != own}
        assert wrong == {}, f"an include before the own origin moved the id: {wrong}"



class TestAnOldGitsEchoedOptionIsNoAnswer:
    """A git that predates `rev-parse --absolute-git-dir` (2.13) or
    `--git-common-dir` (2.5) prints the option back as if it were an
    argument. Such an answer is no answer: it is never read as a path."""

    @pytest.mark.parametrize("shape", [
        "control_own_checkout",
        pytest.param("control_own_linked_worktree", id="control-linked_worktree"),
        pytest.param("control_own_submodule", id="control-submodule"),
    ])
    def test_an_echoed_git_dir_binds_no_folder_name(self, tmp_path, shape):
        """Each route binds the repository's own origin or declines; the
        cwd-marker routes, the ones that ask for the absolute git dir, never
        read the echo as a git dir that git looked past. The checkout is the
        case the echo broke; the linked worktree and the submodule bound
        their own id even then, and are controls."""
        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, shape, "ECHO--absolute-git-dir")
        canonical = {
            answer[0] for answer in _route_answers(tmp_path / "real", shape, "PASS").values()
        }
        assert len(canonical) == 1 and None not in canonical  # control: git binds one id
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] is not None and answer[0] not in canonical
        }
        assert wrong == {}, f"an echoed --absolute-git-dir was read as a git dir: {wrong}"

    @pytest.mark.parametrize("shape", ["control_own_checkout", "control_own_linked_worktree"])
    def test_an_echoed_common_dir_is_a_marker_git_cannot_answer_for(self, tmp_path, shape):
        """No common dir is read from the echo, so every route declines the
        checkout as a marker git could not answer for, never as a redirect."""
        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, shape, "ECHO--git-common-dir")
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] is not None or answer[1] != "broken_git_marker"
        }
        assert wrong == {}, f"an echoed --git-common-dir was read as a path: {wrong}"

    @pytest.mark.parametrize(
        ("option", "ask"),
        [
            ("--absolute-git-dir", lambda m, root: m._ask_git_dir_and_toplevel(str(root))),
            ("--git-common-dir", lambda m, root: m.main_repo_common_dir(str(root))),
            ("--show-toplevel", lambda m, root: m._ask_rev_parse(str(root), ("--show-toplevel",))),
        ],
    )
    def test_each_reader_takes_an_echo_as_no_answer(self, tmp_path, monkeypatch, option, ask):
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        repo = make_repo(tmp_path / "repo", remote=REMOTE)
        assert ask(memory_api, repo) not in (None, (None, None))  # control: real git answers
        _git_on_path(monkeypatch, tmp_path / "bin", f"ECHO{option}")
        assert ask(memory_api, repo) in (None, (None, None))


class TestTheCwdMarkerDeclinesAMarkerGitCannotAnswerFor:
    """On the cwd-marker rung a repository git gives no git dir for (git
    failed, or printed back `--absolute-git-dir` because it predates it)
    declines as a marker git could not answer for; a bare repository, for
    which git gives a git dir and no top level, still declines as having no
    working tree."""

    @staticmethod
    def _cwd_routes(answers):
        return {route: answer for route, answer in answers.items() if route.startswith("cwd marker")}

    @pytest.mark.parametrize("mode", ["ECHO--absolute-git-dir", "FAIL_ABSOLUTE_GIT_DIR"])
    def test_no_git_dir_declines_as_a_broken_marker(self, tmp_path, mode):
        assert_outside_repository(tmp_path)
        answers = self._cwd_routes(_route_answers(tmp_path, "control_own_checkout", mode))
        assert answers, "control: the cwd-marker routes ran"
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] is not None or answer[1] != "broken_git_marker"
        }
        assert wrong == {}, f"a marker git gave no git dir for: {wrong}"

    def test_no_git_dir_names_where_the_marker_is(self, tmp_path, caplog):
        """The cwd-marker rung's decline for a marker git gave no git dir for
        names that marker's path."""
        from fixtures.identity_routes import ROUTES, SHAPES, _clear

        assert_outside_repository(tmp_path)
        run, _sources = ROUTES["cwd marker"]
        target = SHAPES["control_own_checkout"](tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with pytest.MonkeyPatch.context() as cell, caplog.at_level(logging.WARNING):
            _clear(cell)
            _git_on_path(cell, tmp_path / "bin", "ECHO--absolute-git-dir")
            detected = run(cell, target, elsewhere)
        warnings = [r.getMessage() for r in caplog.records if "so no project id was derived" in r.getMessage()]
        named = f"the `.git` marker is {str(Path(target) / '.git')!r}"

        assert ((detected.decline_reason or {}).get("reason"), len(warnings)) == ("broken_git_marker", 1), warnings
        assert "gave no git dir" in warnings[0] and named in warnings[0], warnings

    def test_a_bare_repository_still_has_no_working_tree(self, tmp_path):
        assert_outside_repository(tmp_path)
        answers = self._cwd_routes(_route_answers(tmp_path, "bare_target", "PASS"))
        assert answers and {answer[1] for answer in answers.values()} == {"no_working_tree"}, answers


# The expression classes the walk reads outright. Every other expression
# with a passing child is evaluated by `_gives_back_an_answer`, and one that
# can give an answer back fails the walk's ban.
#
# The expression's value can be one of these children, unchanged:
_ANSWER_CHILDREN = {
    "BoolOp": lambda node: node.values,  # `a or b` and `a and b` give an operand
    "NamedExpr": lambda node: [node.value],  # `(x := a)` gives `a`
    "IfExp": lambda node: [node.body, node.orelse],  # either branch
    "Await": lambda node: [node.value],  # awaiting a reader's call gives its answer
}
# Read by the walk itself:
_ANSWER_BASES = {
    "Name": "`UNREADABLE_LISTING`, or a name bound to an answer",
    "Call": "a call to a reader, or a call handed a lambda (by position or keyword) whose body passes one",
}


def _expression_children(node) -> list:
    """The expressions `node` evaluates directly: its expression children,
    and those under a part that is no expression of its own (a
    comprehension's `iter` and `ifs`, a keyword's value, a default)."""
    found, parts = [], list(ast.iter_child_nodes(node))
    while parts:
        part = parts.pop(0)
        if isinstance(part, ast.expr):
            found.append(part)
        elif not isinstance(part, (ast.expr_context, ast.operator, ast.boolop, ast.unaryop, ast.cmpop)):
            parts.extend(ast.iter_child_nodes(part))
    return found


def _holds(value, answer, depth=4) -> bool:
    """Whether `answer` can be had back from `value`: it is `value`, or it
    comes out of iterating it, of a mapping's keys and values, of calling
    it with no argument, or, for an object that is none of those (a
    slice), of what the interpreter records it refers to."""
    import gc
    import itertools

    if value is answer:
        return True
    if depth == 0 or isinstance(value, (str, bytes, type, type(ast))):
        return False
    try:
        if isinstance(value, dict):
            parts = [*value.keys(), *value.values()]
        elif callable(value):
            parts = [value()]
        elif hasattr(value, "__iter__"):
            parts = list(itertools.islice(iter(value), 100))
        else:
            parts = gc.get_referents(value)
    except Exception:
        return False
    return any(_holds(part, answer, depth - 1) for part in parts)


class _Adversary:
    """A value for a name an evaluated expression cannot read: the worst
    the name could hold. It keeps everything it is handed (in `handed`,
    shared by every adversary of one evaluation), since an unknown object
    may store it. Called, it gives back its first argument; compared or
    combined, the other operand; indexed or asked for an attribute,
    itself; and it is true and iterates to itself."""

    def __init__(self, handed):
        object.__setattr__(self, "handed", handed)

    def __call__(self, *args, **kwargs):
        self.handed.extend([*args, *kwargs.values()])
        return args[0] if args else self

    def __setattr__(self, name, value):
        self.handed.append(value)

    def __setitem__(self, key, value):
        self.handed.extend([key, value])

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __iter__(self):
        return iter([self])

    def __bool__(self):
        return True


def _arm_the_adversary():
    """Give `_Adversary` every operator the `operator` module names that the
    builtin types implement: one taking an operand gives back the operand,
    one taking none gives back itself."""
    import inspect
    import operator

    implemented = set().union(*(dir(t) for t in (int, set, str, list, dict)))
    for function_name in operator.__all__:
        try:
            arity = len(inspect.signature(getattr(operator, function_name)).parameters)
        except (TypeError, ValueError):
            continue
        base = function_name.rstrip("_")
        names = [f"__{base}__", f"__r{base}__"] if arity >= 2 else [f"__{base}__"]
        for name in names:
            if name in implemented and name not in vars(_Adversary):
                setattr(_Adversary, name, _binary if arity >= 2 else (lambda self: self))


def _binary(self, other, *rest):
    """An adversary's operator: it keeps the operand and gives it back."""
    self.handed.append(other)
    return other


_arm_the_adversary()


# The builtins the evaluation binds to the real object: pure functions of
# their arguments, with no effect outside the call. Any other builtin an
# expression names (`open`, `exec`, `eval`, `compile`, `input`, `print`,
# `breakpoint`, `setattr`, ...) is an adversary like any unknown name, so
# the evaluation never runs it and its verdict fails closed.
_PURE_BUILTINS = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "bytes", "chr", "divmod",
    "enumerate", "filter", "float", "frozenset", "hex", "int", "isinstance",
    "issubclass", "len", "list", "map", "max", "min", "oct", "ord", "range",
    "repr", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip",
})


def _gives_back_an_answer(node, passing, parents, constants, sentinel_values, builtin_names=frozenset()) -> bool:
    """Whether the expression `node`, whose children `passing` pass an
    answer, can give one back. The nearest expression that compiles on its
    own (a formatted value's string, a starred item's container) is
    evaluated with each passing child replaced by each answer a reader
    gives (each sentinel string and a record list), every other name being
    a module string constant (`constants`) or a pure builtin
    (`_PURE_BUILTINS`) the module does not shadow (`builtin_names`). FAIL
    CLOSED: an expression with any other name, or none that compiles, can."""
    builtin_names = frozenset(builtin_names) & _PURE_BUILTINS
    held = {id(child) for child in passing}
    while True:
        copy = ast.parse("x", mode="eval")
        copy.body = _replace_passing(node, held)
        names = {
            name.id for name in ast.walk(copy.body)
            if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
        } - {"__answer__"} - _comprehension_and_lambda_names(copy.body)
        unknown = names - set(constants) - set(builtin_names)
        try:
            code = compile(ast.fix_missing_locations(copy), "<held>", "eval")
            break
        except SyntaxError:
            node = parents.get(id(node))
            if not isinstance(node, ast.expr):
                return True
    import builtins

    names_bound = {name: getattr(builtins, name) for name in builtin_names}
    for answer in [*sentinel_values, _ANSWER_SAMPLES[1]]:
        handed = []
        adversaries = {name: _Adversary(handed) for name in unknown}
        try:
            result = eval(code, {"__builtins__": {}, **names_bound, **constants, **adversaries, "__answer__": answer})
        except Exception:
            result = None
        if _holds(result, answer) or any(_holds(value, answer) for value in handed):
            return True
    return False


@pytest.mark.parametrize("name", ["open", "exec", "input", "breakpoint", "print", "delattr"])
def test_the_answer_eval_runs_no_builtin_with_an_effect(monkeypatch, name):
    """An expression that hands a passing value to a builtin with an effect
    outside the call is judged without running that builtin: the name is an
    adversary, so the verdict fails closed (it can give the answer back)
    and the real builtin is never called. Control: a pure builtin (`len`)
    is still evaluated, so an expression that only measures the answer
    gives none back.

    RED WHEN the evaluation binds a builtin with an effect to the real
    object, or stops evaluating the pure ones."""
    import builtins

    calls = []
    monkeypatch.setattr(builtins, name, lambda *args, **kwargs: calls.append(args))
    every = frozenset(n for n in dir(builtins) if not n.startswith("_"))

    def verdict(source):
        node = ast.parse(source, mode="eval").body
        parents = {id(child): parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
        passing = [n for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "answer"]
        return _gives_back_an_answer(node, passing, parents, {}, ["unreadable"], every)

    assert verdict(f"{name}(answer)") is True
    assert calls == []
    assert verdict("len(answer)") is False  # control


def _replace_passing(node, held):
    """A copy of `node` with each child in `held` (by id) replaced by the
    name `__answer__`."""
    if id(node) in held:
        return ast.copy_location(ast.Name(id="__answer__", ctx=ast.Load()), node)
    copy = type(node)()
    for field, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            value = _replace_passing(value, held)
        elif isinstance(value, list):
            value = [_replace_passing(v, held) if isinstance(v, ast.AST) else v for v in value]
        setattr(copy, field, value)
    for attribute in node._attributes:
        if hasattr(node, attribute):
            setattr(copy, attribute, getattr(node, attribute))
    return copy


def _comprehension_and_lambda_names(node) -> set:
    """The names a comprehension target or a lambda parameter binds inside
    `node`."""
    names = set()
    for part in ast.walk(node):
        if isinstance(part, ast.comprehension):
            names |= {n.id for n in ast.walk(part.target) if isinstance(n, ast.Name)}
        elif isinstance(part, ast.Lambda):
            names |= {a.arg for a in ast.walk(part.args) if isinstance(a, ast.arg)}
    return names


def _grammar_expression_classes() -> set:
    """Every expression class the compiler builds: each subclass of
    `ast.expr` that the compiler's own module (`_ast`) defines. `ast` keeps
    deprecated expression classes in Python, which the parser never
    produces."""
    import _ast

    return {
        node.__name__ for node in ast.expr.__subclasses__()
        if vars(_ast).get(node.__name__) is node
    }


# Calls whose value is one of their arguments, unchanged, by the callee's
# last name and the argument's position.
_ARGUMENT_PASSERS = {
    "cast": 1,  # typing.cast(T, value) returns value
    "setdefault": 1,  # d.setdefault(key, default) can return default
}

# Every class that binds a target from a value, read by the walk: a name
# target binds a passing value, a subscripted name holds it, and any other
# target, an augmented assignment and a comprehension fail the walk's ban.
# The census compares this with the compiler's classes that have a target
# field and a value to bind it from.
_BINDINGS_READ = {"Assign", "AnnAssign", "AugAssign", "NamedExpr", "For", "AsyncFor", "withitem", "comprehension"}


# Every match pattern class that captures a name, read by the walk: each
# name a case of a passing subject captures may hold the answer. The census
# compares this with the compiler's pattern classes that name a capture.
_CAPTURES_READ = {"MatchAs", "MatchStar", "MatchMapping"}


def _capture_classes() -> set:
    """The compiler's pattern classes (`_ast`) with a field naming the name
    a match binds (`name`, or a mapping's `rest`)."""
    import _ast

    return {
        name for name, node in vars(_ast).items()
        if isinstance(node, type) and issubclass(node, ast.pattern) and {"name", "rest"} & set(node._fields)
    }


def _target_classes() -> set:
    """The compiler's classes (`_ast`) with a target field and a field
    besides it, which binds the target from a value."""
    import _ast

    targets = {"target", "targets", "optional_vars"}
    return {
        name for name, node in vars(_ast).items()
        if isinstance(node, type) and issubclass(node, ast.AST)
        and targets & set(getattr(node, "_fields", ()))
        and set(getattr(node, "_fields", ())) - targets
    }


def _listing_sentinels(trees: "dict | None" = None) -> set:
    """Every module-level string constant (`module_string_constants`, read
    from every binding form) a listing module's `_worktree_records` returns
    bare, where the function binds no local of that name: the answers that
    are no record list."""
    from test_identity_rung_parity import _reader_for, module_string_constants

    sentinels = set()
    for tree in (_listing_answer_modules() if trees is None else trees).values():
        constants, reader = module_string_constants(tree), _reader_for(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_worktree_records":
                sentinels |= {
                    ret.value.id for ret in ast.walk(node)
                    if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)
                    and ret.value.id in constants and (id(node), ret.value.id) not in reader.sources
                }
    return sentinels


# The value an unpacking the walk does not pair binds to every target name.
_ANY_ANSWER = "<an answer>"

# One answer of each type a reader gives: a sentinel string and a record list.
_ANSWER_SAMPLES = ("unreadable", [["worktree /a", "HEAD 0"]])


def _enter_gives_back_an_answer() -> bool:
    """Whether `with answer as name` can bind `name` to an answer: what
    entering each sample answer gives back."""
    for answer in _ANSWER_SAMPLES:
        try:
            entered = type(answer).__enter__(answer)
        except Exception:
            continue
        if _holds(entered, answer):
            return True
    return False


def _is_list_test(test, name) -> bool:
    """Whether `test` is exactly `isinstance(<name>, list)`."""
    return (
        isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "isinstance"
        and not test.keywords and len(test.args) == 2
        and isinstance(test.args[0], ast.Name) and test.args[0].id == name
        and isinstance(test.args[1], ast.Name) and test.args[1].id == "list"
    )


def _narrowed_to_a_list(store, name, function, parents) -> bool:
    """Whether `name`, the value `store` puts into a container, can only be
    a record list there: in the same function the store sits in the body of
    `if isinstance(name, list):`, or follows, in a block enclosing it, an
    `if not isinstance(name, list):` with no else whose body ends in a
    return or raise; and nothing binds `name` again between that test and
    the store."""
    rebinds = [
        node.lineno for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
    ]

    def clear_between(start, end):
        return not any(start < line <= end for line in rebinds)

    child = store
    while child is not function:
        parent = parents.get(id(child))
        if parent is None:
            return False
        if isinstance(parent, ast.If) and any(s is child for s in parent.body) and _is_list_test(parent.test, name):
            return clear_between(parent.test.end_lineno, store.lineno)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if not isinstance(block, list):
                continue
            index = next((i for i, s in enumerate(block) if s is child), None)
            if index is None:
                continue
            for sibling in block[:index]:
                if (
                    isinstance(sibling, ast.If) and not sibling.orelse
                    and isinstance(sibling.test, ast.UnaryOp) and isinstance(sibling.test.op, ast.Not)
                    and _is_list_test(sibling.test.operand, name)
                    and isinstance(sibling.body[-1], (ast.Return, ast.Raise))
                    and clear_between(sibling.end_lineno, store.lineno)
                ):
                    return True
        child = parent
    return False


def _walk_answers(trees: dict, sentinels: "set | None" = None, held: "list | None" = None) -> tuple:
    """For `trees` ({module: tree}), every function that can return a
    worktree-listing reader's answer unchanged, as {(module, name): def},
    and every call to a function outside the listing modules that is
    handed a passing value, as {callee name: (module, function)}. `held`,
    when given, receives (module, function, node, passing children,
    parents, unread) for each place the walk does not read outright: an
    expression of another class a passing value is evaluated in, or a call
    to a function outside the listing modules handed one (both evaluated by
    `_gives_back_an_answer`); and, with `unread` true, a passing value
    stored into a subscript or attribute target or added with an augmented
    assignment, one handed to a listing-module function where no
    parameter can be named, and a `global` or `nonlocal` statement naming a
    name bound to a passing value.

    An expression passes an answer when it is one of `sentinels` (by
    default `_listing_sentinels()`); a name bound to a passing value (by
    assignment, annotated assignment, `:=`, position in a tuple assignment,
    a `for` over a literal container, a `with` whose entered answer gives
    it back, a `match` capture of a passing subject (`_CAPTURES_READ`), or a
    parameter some call hands one; `_BINDINGS_READ`); an answer imported
    under another name or read as a module attribute; a call
    to a found function by name in its own module or by attribute in any;
    an `_ARGUMENT_PASSERS` call whose argument passes; a call handed a
    lambda whose body passes; or one of the `_ANSWER_CHILDREN` children.
    Followed to a fixed point, so every wrapper of a reader is found however
    the functions are ordered.

    ADVERSARIAL-ONLY, not read (no good-faith edit writes it to pass an
    answer on): a decorator that replaces a def with an answer
    (`@lambda f: reader()`), since a decorator that discards the function it
    decorates turns a definition into a value only to hide it."""
    answers = (_listing_sentinels() if sentinels is None else set(sentinels)) | {_ANY_ANSWER}
    # an answer imported under another name is that answer
    answers |= {
        alias.asname for tree in trees.values() for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names if alias.asname and alias.name in answers
    }
    functions = {
        (module, node.name): node
        for module, tree in trees.items() for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found = set()
    seeds = collections.defaultdict(set)  # parameters some call hands a passing value

    def callee(call):
        return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)

    def is_reader_call(call, module):
        if isinstance(call.func, ast.Name):
            return (module, call.func.id) in found
        return isinstance(call.func, ast.Attribute) and any(name == call.func.attr for _m, name in found)

    def defs_called(call, module):
        """The listing modules' functions `call` can reach: by name in its
        own module, or by attribute any function of that name."""
        if isinstance(call.func, ast.Name):
            return [(module, call.func.id)] if (module, call.func.id) in functions else []
        if isinstance(call.func, ast.Attribute):
            return [key for key in functions if key[1] == call.func.attr]
        return []

    def handed_to(call, key):
        """[(parameter or None, argument)] for each argument `call` hands
        the function `key`; None where no parameter can be named."""
        arguments = functions[key].args
        positional = [a.arg for a in [*arguments.posonlyargs, *arguments.args]]
        if isinstance(call.func, ast.Attribute) and positional[:1] in (["self"], ["cls"]):
            positional = positional[1:]
        named = set(positional) | {a.arg for a in arguments.kwonlyargs}
        pairs = []
        for index, argument in enumerate(call.args):
            if isinstance(argument, ast.Starred):
                pairs.append((None, argument.value))
            elif index < len(positional):
                pairs.append((positional[index], argument))
            else:
                pairs.append((arguments.vararg.arg if arguments.vararg else None, argument))
        for keyword in call.keywords:
            if keyword.arg in named:
                pairs.append((keyword.arg, keyword.value))
            else:
                pairs.append((arguments.kwarg.arg if arguments.kwarg and keyword.arg else None, keyword.value))
        return pairs

    def passes(value, bound, module):
        if isinstance(value, ast.Name):
            return value.id in answers or value.id in bound
        if isinstance(value, ast.Attribute) and value.attr in answers:
            return True  # an answer read from the module that holds it
        if isinstance(value, ast.Call):
            if is_reader_call(value, module):
                return True
            position = _ARGUMENT_PASSERS.get(callee(value))
            if position is not None and len(value.args) > position:
                if passes(value.args[position], bound, module):
                    return True
            handed = [*value.args, *(keyword.value for keyword in value.keywords)]
            return any(isinstance(arg, ast.Lambda) and passes(arg.body, bound, module) for arg in handed)
        children = _ANSWER_CHILDREN.get(type(value).__name__)
        return children is not None and any(passes(child, bound, module) for child in children(value))

    def bindings(function, bound, module):
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                pairs = [(target, node.value) for target in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
                pairs = [(node.target, element) for element in node.iter.elts if not isinstance(element, ast.Starred)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                if passes(node.context_expr, bound, module) and _enter_gives_back_an_answer():
                    yield from (n.id for n in ast.walk(node.optional_vars) if isinstance(n, ast.Name))
                continue
            elif isinstance(node, ast.Match):
                if passes(node.subject, bound, module):
                    # a capture holds the subject or a part of it, so every
                    # name a case captures may hold the answer
                    yield from (
                        name for case in node.cases for pattern in ast.walk(case.pattern)
                        for name in (getattr(pattern, "name", None), getattr(pattern, "rest", None)) if name
                    )
                continue
            else:
                continue
            for target, value in pairs:
                if value is None:
                    continue
                if isinstance(target, (ast.Tuple, ast.List)):
                    if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts) and not any(
                        isinstance(e, ast.Starred) for e in [*target.elts, *value.elts]
                    ):
                        pairs_inner = zip(target.elts, value.elts)
                    elif passes(value, bound, module) or any(
                        passes(e, bound, module) for e in getattr(value, "elts", [])
                    ):
                        # an unpacking this walk does not pair: every name may hold it
                        pairs_inner = [(t, ast.Name(id=_ANY_ANSWER)) for t in target.elts]
                    else:
                        continue
                    for inner_target, inner_value in pairs_inner:
                        if isinstance(inner_target, ast.Starred):
                            inner_target = inner_target.value
                        if isinstance(inner_target, ast.Name) and passes(inner_value, bound, module):
                            yield inner_target.id
                elif isinstance(target, ast.Name) and passes(value, bound, module):
                    yield target.id

    def bound_names(function, key):
        bound = set(seeds[key])
        while True:
            more = set(bindings(function, bound, key[0])) - bound
            if not more:
                return bound
            bound |= more

    changed = True
    while changed:
        changed = False
        for key, function in functions.items():
            module = key[0]
            bound = bound_names(function, key)
            if key not in found and any(
                isinstance(node, ast.Return) and node.value is not None and passes(node.value, bound, module)
                for node in ast.walk(function)
            ):
                found.add(key)
                changed = True
            for call in ast.walk(function):
                if not isinstance(call, ast.Call):
                    continue
                for target in defs_called(call, module):
                    for parameter, argument in handed_to(call, target):
                        if parameter is not None and parameter not in seeds[target] and passes(
                            argument, bound, module
                        ):
                            seeds[target].add(parameter)
                            changed = True

    taking = {}
    if held is None:
        held = []
    for key, function in functions.items():
        module, name = key
        bound = bound_names(function, key)
        parents = {id(child): node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(function):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                # A name bound to an answer and declared global or nonlocal
                # hands the answer to another function's scope, which the walk
                # does not follow.
                if set(node.names) & bound:
                    held.append((module, name, node, [], parents, True))
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                stored = isinstance(node, ast.AugAssign) or any(
                    isinstance(part, (ast.Subscript, ast.Attribute))
                    for target in targets for part in [target, *getattr(target, "elts", [])]
                )
                if (
                    stored and node.value is not None and passes(node.value, bound, module)
                    and not (
                        isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Name)
                        and _narrowed_to_a_list(node, node.value.id, function, parents)
                    )
                ):
                    held.append((module, name, node, [node.value], parents, True))
                continue
            if isinstance(node, ast.Call):
                if is_reader_call(node, module):
                    continue
                handed = [*node.args, *(keyword.value for keyword in node.keywords)]
                passing = [arg for arg in handed if not isinstance(arg, ast.Lambda) and passes(arg, bound, module)]
                if not passing:
                    continue
                targets = defs_called(node, module)
                if targets:
                    if any(
                        parameter is None and passes(argument, bound, module)
                        for target in targets for parameter, argument in handed_to(node, target)
                    ):
                        held.append((module, name, node, passing, parents, True))
                    continue
                taking.setdefault(callee(node), (module, name))
                held.append((module, name, node, passing, parents, False))
                continue
            if isinstance(node, ast.expr) and type(node).__name__ not in {*_ANSWER_CHILDREN, *_ANSWER_BASES}:
                handed_lambda = isinstance(node, ast.Lambda) and (
                    isinstance(parents.get(id(node)), ast.Call) and node in parents[id(node)].args
                    or isinstance(parents.get(id(node)), ast.keyword)
                )
                passing = [child for child in _expression_children(node) if passes(child, bound, module)]
                if passing and not handed_lambda:
                    held.append((module, name, node, passing, parents, False))
    return {key: functions[key] for key in found}, taking


def _answer_passers(tree) -> dict:
    """{name: def} for every function in one `tree` that can return a
    listing reader's answer unchanged (`_walk_answers`)."""
    passers, _taking = _walk_answers({"": tree})
    return {name: node for (_module, name), node in passers.items()}


def _listing_answer_modules() -> dict:
    """{path relative to the plugin root: tree} for the two modules that read
    or pass a worktree listing: the memory skill's and the backlog's. Keyed by
    path, not file name, so two modules of one name could not hide each
    other.

    Each tree is parsed once per process for each text its file holds
    (`_parsed_source`), so the binding reader built on it
    (`test_identity_rung_parity._reader_for`, cached by tree) is built
    once too. No reader edits a tree it is given."""
    plugin = Path(__file__).resolve().parent.parent
    return {
        Path(module.__file__).resolve().relative_to(plugin).as_posix():
            _parsed_source(Path(module.__file__).read_text(encoding="utf-8"))
        for module in (memory_api, backlog)
    }


@functools.lru_cache(maxsize=None)
def _parsed_source(text: str):
    """The tree of `text`, one per text, so equal source gives the same tree."""
    return ast.parse(text)


def _string_constants(tree) -> dict:
    """{name: value} for every module-level string constant in `tree`, read
    from every binding form (`module_string_constants`)."""
    from test_identity_rung_parity import module_string_constants

    return module_string_constants(tree)


def _held_answers(trees: dict) -> tuple:
    """(every expression in `trees` the walk does not read outright that a
    passing value is evaluated in, those that can give an answer back), each
    as (module, function, line, source)."""
    held = []
    _walk_answers(trees, held=held)
    constants, readers_constants = {}, {}
    for tree in trees.values():
        constants.update(_string_constants(tree))
    for tree in _listing_answer_modules().values():
        readers_constants.update(_string_constants(tree))
    sentinel_values = [readers_constants[name] for name in sorted(_listing_sentinels())]
    import builtins

    from test_identity_rung_parity import _reader_for

    # every name a module binds at module level, by any binding form
    shadowed = {name for tree in trees.values() for key, name in _reader_for(tree).sources if key is None}
    builtin_names = frozenset(name for name in dir(builtins) if not name.startswith("_")) - shadowed
    every, giving = [], []
    for module, function, node, passing, parents, unread in held:
        place = (module, function, node.lineno, ast.unparse(node))
        every.append(place)
        if unread or _gives_back_an_answer(node, passing, parents, constants, sentinel_values, builtin_names):
            giving.append(place)
    return every, giving


# One function body per way an answer reaches a return: each child the
# `_ANSWER_CHILDREN` table names, each base, and each binding route.
_PASSING_FORMS = {
    "BoolOp": "return reader() or None",
    "BoolOp, a later operand": "return cached or reader()",
    "NamedExpr": "return (records := reader())",
    "IfExp": "return None if cached else reader()",
    "Await": "return await reader()",
    "Name": "return UNREADABLE_LISTING",
    "Call": "return reader()",
    "Call, a lambda by position": "return retry(lambda: reader())",
    "Call, a lambda by keyword": "return retry(task=lambda: reader() or None)",
    "bound by assignment": "records = reader()\n    return records",
    "bound by :=": "if (records := reader()) is None:\n        pass\n    return records",
    "bound through another name": "first = reader()\n    second = first\n    return second",
    "bound by annotated assignment": "records: list = reader()\n    return records",
    "bound by position in a tuple": "records, other = reader(), cached\n    return records",
    "bound by an unpacking not paired": "records, *rest = [reader(), cached]\n    return records",
    "bound by for over a literal container": "for records in (cached, reader()):\n        pass\n    return records",
    "bound by async for over a literal container": "async for records in [reader()]:\n        return records",
    "Call, a reader by attribute": "return module.reader()",
    "Call, cast": "return cast(list, reader())",
    "Call, setdefault": "return found.setdefault(cached, reader())",
}


class TestTheListingAnswerWalkFollowsEveryForm:
    """The walk that finds the functions passing a listing reader's answer
    places every expression class of the grammar, and finds a wrapper
    written in each form that passes the answer."""

    def test_the_classes_read_outright_are_grammar_classes_placed_once(self):
        assert not set(_ANSWER_CHILDREN) & set(_ANSWER_BASES)
        assert set(_ANSWER_CHILDREN) | set(_ANSWER_BASES) <= _grammar_expression_classes()

    def test_no_listing_module_holds_an_answer_in_an_unread_form(self):
        """FAIL CLOSED: an answer stored in a container, passed through a
        comprehension or generator, bound in a lambda, or held by any other
        expression the walk does not read, which can give it back, is a
        wrapper the walk cannot see."""
        _every, giving = _held_answers(_listing_answer_modules())
        assert giving == [], giving

    def test_control_the_ban_sees_the_held_answers_and_spares_the_comparisons(self):
        """Control: the listing modules' comparisons of an answer with a
        sentinel and their slices of a record list are found and evaluated,
        and none gives an answer back; a container of an answer does."""
        every, giving = _held_answers(_listing_answer_modules())
        sources = {place[3] for place in every}
        assert {"records == UNREADABLE_LISTING", "records[1:]"} <= sources, sorted(sources)
        assert not giving
        source = (
            "def reader():\n    return UNREADABLE_LISTING\n\n\n"
            "def wrapper():\n    box = [reader()]\n    return box\n"
        )
        tree = ast.parse("UNREADABLE_LISTING = 'unreadable'\n" + source)
        assert [place[3] for place in _held_answers({"m": tree})[1]] == ["[reader()]"]

    def test_every_passing_child_has_a_form(self):
        assert set(_ANSWER_CHILDREN) | set(_ANSWER_BASES) <= set(_PASSING_FORMS)

    @pytest.mark.parametrize("form", sorted(_PASSING_FORMS))
    def test_a_wrapper_in_each_form_is_found(self, form):
        source = (
            "def reader():\n    return UNREADABLE_LISTING\n\n\n"
            f"async def wrapper(cached):\n    {_PASSING_FORMS[form]}\n"
        )
        assert set(_answer_passers(ast.parse(source))) == {"reader", "wrapper"}, source

    def test_control_a_transformed_answer_is_not_passed(self):
        source = (
            "def reader():\n    return UNREADABLE_LISTING\n\n\n"
            "def wrapper(cached):\n    records = len(reader())\n    return [records, reader()]\n"
        )
        assert set(_answer_passers(ast.parse(source))) == {"reader"}

    def test_the_answers_that_are_no_record_list_are_read_from_the_readers(self):
        sentinels = _listing_sentinels()
        assert "UNREADABLE_LISTING" in sentinels, sentinels  # control
        assert sentinels <= set(vars(memory_api)), sentinels - set(vars(memory_api))

    def test_the_answers_are_every_string_the_readers_return_bare(self):
        """Control: the names `_worktree_records` returns bare whose value in
        the imported module is a string are exactly the answers read from
        the source, so a sentinel bound by a form the table cannot read is
        missed loudly."""
        returned = set()
        for module in (memory_api, backlog):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "_worktree_records":
                    returned |= {
                        ret.value.id for ret in ast.walk(node)
                        if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)
                        and isinstance(getattr(module, ret.value.id, None), str)
                    }
        assert {"UNREADABLE_LISTING", "EMPTY_LISTING"} <= returned, returned
        assert _listing_sentinels() == returned, _listing_sentinels() ^ returned

    @pytest.mark.parametrize("source, sentinels", [
        ("EMPTY_LISTING = 'empty'\ndef _worktree_records():\n    return EMPTY_LISTING\n", {"EMPTY_LISTING"}),
        ("EMPTY_LISTING: Final = 'empty'\ndef _worktree_records():\n    return EMPTY_LISTING\n", {"EMPTY_LISTING"}),
        ("EMPTY_LISTING = 'empty'\nEMPTY_LISTING += 'x'\ndef _worktree_records():\n    return EMPTY_LISTING\n", set()),
        ("EMPTY_LISTING = 'empty'\ndef _worktree_records():\n    EMPTY_LISTING = g()\n    return EMPTY_LISTING\n",
         set()),
        ("def _worktree_records(records):\n    return records\n", set()),
    ])
    def test_an_answer_is_a_constant_by_every_binding_form(self, source, sentinels):
        assert _listing_sentinels({"m": ast.parse(source)}) == sentinels

    @pytest.mark.parametrize("sentinel", sorted(_listing_sentinels()))
    def test_a_function_returning_each_answer_alone_is_found(self, sentinel):
        source = f"def wrapper():\n    return {sentinel}\n"
        assert set(_answer_passers(ast.parse(source))) == {"wrapper"}, source

    def test_a_wrapper_defined_before_what_it_wraps_is_found(self):
        """Each wrapper is defined before the function it calls, so one pass
        in source order finds only the reader; the walk must repeat until
        nothing new is found."""
        source = (
            "def outer():\n    return middle()\n\n\n"
            "def middle():\n    return reader()\n\n\n"
            "def reader():\n    return UNREADABLE_LISTING\n"
        )
        assert set(_answer_passers(ast.parse(source))) == {"outer", "middle", "reader"}

    def test_a_reader_called_by_attribute_from_another_module_is_followed(self):
        trees = {
            "skill": ast.parse("def reader():\n    return UNREADABLE_LISTING\n"),
            "user": ast.parse("def wrapper():\n    return skill.reader()\n"),
        }
        passers, _taking = _walk_answers(trees)
        assert set(passers) == {("skill", "reader"), ("user", "wrapper")}, passers

    def test_every_binding_class_is_read(self):
        assert {"Assign", "withitem"} <= _target_classes() and "Delete" not in _target_classes()  # control
        assert _BINDINGS_READ == _target_classes(), _target_classes() ^ _BINDINGS_READ

    def test_every_capture_class_is_read(self):
        assert "MatchAs" in _capture_classes() and "MatchValue" not in _capture_classes()  # control
        assert _CAPTURES_READ == _capture_classes(), _capture_classes() ^ _CAPTURES_READ

    @pytest.mark.parametrize("trees, wrapper", [
        ({"skill": "def reader():\n    return UNREADABLE_LISTING\n",
          "user": "def wrapper():\n    match skill.reader():\n        case got:\n            return got\n"}, True),
        ({"skill": "def reader():\n    return UNREADABLE_LISTING\n",
          "user": "def wrapper():\n    match skill.reader():\n        case {**got}:\n            return got\n"}, True),
        ({"skill": "UNREADABLE_LISTING = 'unreadable'\n",
          "user": "from skill import UNREADABLE_LISTING as gone\ndef wrapper():\n    return gone\n"}, True),
        ({"skill": "UNREADABLE_LISTING = 'unreadable'\n",
          "user": "import skill\ndef wrapper():\n    return skill.UNREADABLE_LISTING\n"}, True),
        ({"skill": "def reader():\n    return UNREADABLE_LISTING\n",
          "user": "def wrapper():\n    match skill.reader():\n        case str():\n            return 'text'\n"}, False),
    ])
    def test_an_answer_held_by_a_capture_or_alias_is_followed(self, trees, wrapper):
        passers, _taking = _walk_answers({name: ast.parse(source) for name, source in trees.items()})
        assert (("user", "wrapper") in passers) is wrapper, passers

    @pytest.mark.parametrize("source, texts, resolved", [
        ("def f():\n    detail = 'a'\n    return detail\n", {"a"}, True),
        ("DETAIL = 'a'\ndef f():\n    return DETAIL\n", {"a"}, True),
        ("TABLE = {'k': 'a'}\ndef f(k):\n    return TABLE[k]\n", {"a"}, True),
        ("def f():\n    detail = 'a'\n    detail += 'b'\n    return detail\n", {"a"}, False),
        ("def f(c):\n    detail = 'a'\n    for detail in c():\n        pass\n    return detail\n", {"a"}, False),
        ("def f(c):\n    detail = 'a'\n    with c as detail:\n        return detail\n", {"a"}, False),
        ("def f(c):\n    detail = 'a'\n    match c:\n        case [detail]:\n            pass\n    return detail\n", {"a"}, False),
        ("DETAIL = 'a'\nDETAIL += 'b'\ndef f():\n    return DETAIL\n", {"a"}, False),
        ("def f(detail):\n    if detail:\n        detail = 'a'\n    return detail\n", {"a"}, False),
        ("def f():\n    detail = 'a'\n    match 'b':\n        case [detail]:\n            pass\n    return detail\n", {"a", "b"}, False),
        # a table filled after its literal holds what the filling put in
        ("TABLE = {'k': 'a'}\nTABLE['j'] = 'b'\ndef f(k):\n    return TABLE[k]\n", set(), False),
        ("TABLE = {'k': 'a'}\nTABLE.update(j='b')\ndef f(k):\n    return TABLE[k]\n", set(), False),
        ("TABLE = {'k': 'a'}\nTABLE.setdefault('j', 'b')\ndef f(k):\n    return TABLE[k]\n", set(), False),
        ("TABLE = {'k': 'a'}\nTABLE |= {'j': 'b'}\ndef f(k):\n    return TABLE[k]\n", set(), False),
        ("TABLE = {'k': 'a'}\ndef f(k):\n    return TABLE[k]\ndef add():\n    TABLE['j'] = 'b'\n", set(), False),
        ("TABLE = {'k': 'a'}\ndef f(k):\n    if k in TABLE:\n        return TABLE[k]\n    return 'a'\n", {"a"}, True),
    ])
    def test_a_detail_name_is_read_from_every_binding(self, source, texts, resolved):
        """A detail name is followed only when every binding of it, by any
        binding form, is an assignment; any other binding is left in
        `unresolved`, so no text it can hold goes unchecked."""
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        returned = next(n.value for n in ast.walk(function) if isinstance(n, ast.Return))
        found, unresolved = _detail_values(returned, function, tree)
        assert found <= texts and (not unresolved) is resolved, (found, unresolved)

    def test_each_call_handed_an_answer_is_followed_or_evaluated(self):
        """Control: in the listing modules a call handed an answer is a
        listing-module function the walk follows into, or is evaluated
        (`isinstance` gives a bool); none is excused by name."""
        _passers, taking = _walk_answers(_listing_answer_modules())
        assert set(taking) == {"isinstance"}, taking
        every, giving = _held_answers(_listing_answer_modules())
        assert any(place[3].startswith("isinstance(") for place in every)
        assert not giving

    def test_control_a_call_to_an_unknown_function_taking_an_answer_fails(self):
        source = (
            "UNREADABLE_LISTING = 'unreadable'\n"
            "def reader():\n    return UNREADABLE_LISTING\n\n\n"
            "def wrapper():\n    return describe(reader())\n"
        )
        _passers, taking = _walk_answers({"m": ast.parse(source)})
        assert set(taking) == {"describe"}, taking
        assert [place[3] for place in _held_answers({"m": ast.parse(source)})[1]] == ["describe(reader())"]

    @pytest.mark.parametrize("body, found", [
        ("def keep(records):\n    return records\n\n\ndef wrapper():\n    return keep(reader())\n",
         {"reader", "keep", "wrapper"}),
        ("def keep(*, listing):\n    return listing\n\n\ndef wrapper():\n    return keep(listing=reader())\n",
         {"reader", "keep", "wrapper"}),
        ("def check(records):\n    return records == UNREADABLE_LISTING\n\n\ndef wrapper():\n"
         "    return check(reader())\n", {"reader"}),
    ])
    def test_an_answer_handed_as_an_argument_is_followed(self, body, found):
        source = "UNREADABLE_LISTING = 'unreadable'\ndef reader():\n    return UNREADABLE_LISTING\n\n\n" + body
        assert set(_answer_passers(ast.parse(source))) == found, source

    @pytest.mark.parametrize("body", [
        "def wrapper(holder):\n    holder.records = reader()\n    return holder\n",
        "def wrapper(rows):\n    rows[0][1] = reader()\n    return rows\n",
        "def wrapper():\n    box = {}\n    box['k'] = reader()\n    return box['k']\n",
        "def wrapper(total):\n    total += reader()\n    return total\n",
        "def wrapper(box):\n    box.update(k=reader())\n",
        "def wrapper(box):\n    return box.setdefault('k', reader())\n",
        "def wrapper(box):\n    box.append(reader())\n",
        "def wrapper(holder):\n    first, holder.records = None, reader()\n",
        # a list test that does not make the stored name a record list
        "def wrapper(box):\n    records = reader()\n    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if isinstance(records, list):\n        return records\n"
        "    box['k'] = records\n",
        "def wrapper(box, other):\n    records = reader()\n    if not isinstance(other, list):\n"
        "        return records\n    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    box['k'] = records\n    if not isinstance(records, list):\n"
        "        return records\n",
        "def wrapper(box, flag):\n    records = reader()\n    if flag:\n        if not isinstance(records, list):\n"
        "            return records\n    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if not isinstance(records, list):\n        pass\n"
        "    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if not isinstance(records, list):\n        return records\n"
        "    records = reader()\n    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if not isinstance(records, (list, str)):\n        return records\n"
        "    box['k'] = records\n",
    ])
    def test_a_store_of_an_answer_fails_the_ban(self, body):
        source = "UNREADABLE_LISTING = 'unreadable'\ndef reader():\n    return UNREADABLE_LISTING\n\n\n" + body
        assert _held_answers({"m": ast.parse(source)})[1], source

    @pytest.mark.parametrize("body", [
        "def wrapper(box):\n    records = reader()\n    if not isinstance(records, list):\n        return records\n"
        "    box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if not isinstance(records, list):\n"
        "        raise ValueError('no record list')\n    if box is not None:\n        box['k'] = records\n",
        "def wrapper(box):\n    records = reader()\n    if isinstance(records, list):\n        box['k'] = records\n",
    ])
    def test_a_store_of_a_value_a_list_test_has_narrowed_is_read(self, body):
        """Stored where an `isinstance(<name>, list)` test dominates it, the
        value can only be a record list, never a sentinel string."""
        source = "UNREADABLE_LISTING = 'unreadable'\ndef reader():\n    return UNREADABLE_LISTING\n\n\n" + body
        assert _held_answers({"m": ast.parse(source)})[1] == [], source

    @pytest.mark.parametrize("body", [
        "def fill():\n    global RECORDS\n    RECORDS = reader()\n\n\ndef wrapper():\n    fill()\n    return RECORDS\n",
        "def wrapper():\n    records = None\n\n    def fill():\n        nonlocal records\n        records = reader()\n"
        "    fill()\n    return records\n",
        "def wrapper():\n    records = reader()\n\n    def give():\n        nonlocal records\n        return records\n"
        "    return give()\n",
    ])
    def test_a_global_or_nonlocal_answer_fails_the_ban(self, body):
        """A name that holds an answer, declared global or nonlocal, hands
        the answer to a scope the walk does not follow."""
        source = "UNREADABLE_LISTING = 'unreadable'\ndef reader():\n    return UNREADABLE_LISTING\n\n\n" + body
        giving = _held_answers({"m": ast.parse(source)})[1]
        assert [place for place in giving if place[3].startswith(("global ", "nonlocal "))], source

    def test_a_global_name_that_holds_no_answer_is_read(self):
        source = (
            "UNREADABLE_LISTING = 'unreadable'\ndef reader():\n    return UNREADABLE_LISTING\n\n\n"
            "def count():\n    global CALLS\n    CALLS = 1\n    records = reader()\n    return records\n"
        )
        assert _held_answers({"m": ast.parse(source)})[1] == [], source


def _listing_readers() -> dict:
    """{module name: reader} for every module that reads the worktree
    listing itself (has a `_worktree_records`)."""
    return {
        module.__name__: module._worktree_records
        for module in (memory_api, backlog) if hasattr(module, "_worktree_records")
    }


class TestAnUnknownZFieldIsMatchedByItsWholeKey:
    """With `-z`, a field whose key is not one of `_PORCELAIN_ORDER`'s is
    skipped, and the key is compared whole and as written. Every composition
    of the three ways a key can be near a known one is built from the table
    itself: each prefix of a known key (truncation, the whole key included),
    with or without one more character (extension), in every pattern of
    upper and lower case (case change): `lock`, `LOCK`, `Lockedx`, `hEAD`.
    Each that is not a known key is unknown, and the listing must read
    exactly as it does without that field."""

    @staticmethod
    def _keys():
        known = {key.rstrip() for key, _rank in memory_api._PORCELAIN_ORDER}
        built = {
            "".join(letters)
            for key in known for end in range(1, len(key) + 1) for extension in ("", "x")
            for letters in itertools.product(*({c.lower(), c.upper()} for c in key[:end] + extension))
        }
        return sorted(built - known), known

    def _read(self, monkeypatch, reader, listing):
        monkeypatch.setattr(
            subprocess, "run",
            lambda command, *a, **k: subprocess.CompletedProcess(command, 0, listing, b""),
        )
        return reader(["-C", "/nowhere"])

    def test_control_the_built_keys_are_near_known_keys_and_hold_each_composition(self):
        extended, known = self._keys()
        assert known and extended and not set(extended) & known, extended
        assert all(
            any(
                k.lower().startswith(key.lower())
                or key.lower().endswith("x") and k.lower().startswith(key.lower()[:-1])
                for k in known
            )
            for key in extended
        ), extended
        compositions = (
            {key[:-1] for key in known} | {key[:-1].swapcase() for key in known}
            | {key.swapcase() for key in known} | {key + "X" for key in known}
            | {key[:1].swapcase() + key[1:] for key in known}
        ) - known
        assert compositions <= set(extended), compositions - set(extended)

    @pytest.mark.parametrize("position", ["after HEAD", "last"])
    def test_a_key_extending_a_known_one_is_skipped(self, monkeypatch, position):
        readers = _listing_readers()
        assert readers, "control: some module reads the listing"
        extended, _known = self._keys()
        base = [b"worktree /p", b"HEAD 0123", b"branch refs/heads/m"]
        wrong = {}
        for name, reader in readers.items():
            expected = self._read(monkeypatch, reader, b"\0".join(base) + b"\0\0")
            assert isinstance(expected, list) and expected, (name, expected)  # control
            for key in extended:
                field = f"{key} v".encode()
                fields = base[:2] + [field] + base[2:] if position == "after HEAD" else base + [field]
                answer = self._read(monkeypatch, reader, b"\0".join(fields) + b"\0\0")
                if answer != expected:
                    wrong[(name, key)] = answer
        assert wrong == {}, wrong

    def test_a_key_a_known_one_starts_with_changes_no_answer_end_to_end(self, monkeypatch):
        """End to end: a `lock` field after HEAD, read as the known `locked`,
        would make the record's fields out of git's order and the listing
        unreadable; read as unknown, the listing is the same records."""
        readers = _listing_readers()
        assert readers, "control: some module reads the listing"
        base = [b"worktree /p", b"HEAD 0123", b"branch refs/heads/m"]
        for name, reader in readers.items():
            expected = self._read(monkeypatch, reader, b"\0".join(base) + b"\0\0")
            fields = base[:2] + [b"lock v"] + base[2:]
            answer = self._read(monkeypatch, reader, b"\0".join(fields) + b"\0\0")
            assert answer == expected and isinstance(answer, list), (name, answer)


def _union_admits_str(annotation) -> bool:
    """Whether the return annotation `annotation` admits `str` as one of its
    union members (`str`, `X | str`, `Optional[X | str]`, `Union[X, str]`,
    a string holding any of them). A member nested inside another type
    (`List[str]`) is not one."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        annotation = ast.parse(annotation.value, mode="eval").body
    if isinstance(annotation, ast.Name):
        return annotation.id == "str"
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _union_admits_str(annotation.left) or _union_admits_str(annotation.right)
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        members = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        if annotation.value.id in ("Optional", "Union"):
            return any(_union_admits_str(member) for member in members)
    return False


# Every clause a declined identity prints, on every print path
# (`_printed_identity_messages`), and why it only describes or asks for a
# read. A clause not listed here fails the census, whatever its wording or
# spelling, so advice to change a marker cannot pass by a verb, voice,
# pronoun or spelling no list foresaw. `X` is a value a rung fills in;
# `<reason>` is an override reason.
_OBSERVED = "reports what was observed"
_DETAIL = "part of a rung detail: the rung and the path it read"
_DESCRIBED_PRINTED_CLAUSES = {
    "A `git init --separate-git-dir` layout of your own is a user decision":
        "names the user's own layout as theirs to decide",
    "A full 32-character id still resolves": "says which lookups still work",
    "A linked worktree you moved is re-registered by running `git worktree repair` in it":
        "names git's own repair command, run by the user",
    "An override file at '/home/u/repo/.git/pact-project-id' was present but unusable (<reason>)":
        _OBSERVED,
    "An override file in the git directory this checkout's `.git` points at was present "
    "but unusable (<reason>)": _OBSERVED,
    "Check that git can answer for this checkout (`git rev-parse --git-dir`, "
    "`git worktree list`), or run from the repository the `.git` points at":
        "asks for two read-only git answers, or for a run somewhere else",
    "Check that git can read the repository's config here": "asks for a read",
    "Check that git is installed and can read the repository here": "asks for a read",
    "No project directory was declared (CLAUDE_PROJECT_DIR is unset and the session "
    "record names none), and no repository answered": _OBSERVED,
    "Nothing was written": _OBSERVED,
    "Otherwise, `git rev-parse --show-toplevel` run in the declined directory prints "
    "git's own reason": "names a read-only git command",
    "Pinning that directory is a user decision": "names the decision as the user's",
    "Run from a checkout with a working tree": "asks for a run somewhere else",
    "Run from inside the project directory, or set CLAUDE_PROJECT_DIR to an existing "
    "project root": "asks for a run somewhere else or a declaration",
    "Run the `project-id show` verb to inspect the detected identity": "names a read-only verb",
    "Set CLAUDE_PROJECT_DIR to an existing project root": "asks for a declaration",
    "The cwd rung declined": _OBSERVED,
    "Write an operator-chosen canonical id to '/home/u/repo/.git/pact-project-id', "
    "or set CLAUDE_PROJECT_DIR to another project root":
        "names a file to create inside the git directory; the marker is unchanged",
    "X does not exist and no verified worktree identity record names it": _DETAIL,
    "X gave no git dir": _DETAIL,
    "X gave no git dir)": _DETAIL,
    "X is a bare repository, or git cannot describe its working tree)": _DETAIL,
    "X": _DETAIL,
    "X)": _DETAIL,
    "`git config --get remote.origin.url` run here prints git's own reason":
        "names a read-only git command",
    "`git rev-parse --git-dir` run here prints git's own reason": "names a read-only git command",
    "a bare repository has none": _OBSERVED,
    "a memory-id prefix was refused rather than listing matches from every project": _OBSERVED,
    "a path git gave for it would not resolve": _OBSERVED,
    "git could not list that repository's worktrees": _OBSERVED,
    "git could not read the repository's origin remote": _OBSERVED,
    "git does not confirm this checkout as a worktree of the repository git reports for it":
        _OBSERVED,
    "git does not confirm this checkout as a worktree of the repository git reports for it (X":
        _OBSERVED,
    "git gave no git directory for it": _OBSERVED,
    "git gives no working tree for the repository": _OBSERVED,
    "git gives no working tree for the repository (X": _OBSERVED,
    "git gives no working tree for the repository (declared via X": _OBSERVED,
    "git gives no working tree for the repository (the process's repository is bare, or "
    "git cannot describe its working tree)": _OBSERVED,
    "git listed that repository's worktrees, but the listing holds no record, or an "
    "empty one": _OBSERVED,
    "git listed that repository's worktrees, but the listing's records are not git's "
    "fields in git's order": _OBSERVED,
    "git lists the worktree entry its `.git` names at another path, which git marks "
    "prunable, and not at this checkout": "reports what git's worktree listing shows",
    "git reports a git directory for it other than the repository's common directory, "
    "and that repository's worktree list does not hold it": _OBSERVED,
    "ignoring an unusable project override (<reason>) in the git directory this "
    "checkout's `.git` points at": _OBSERVED,
    "ignoring the project override at '/home/u/repo/.git/pact-project-id' (<reason>)":
        _OBSERVED,
    "its `.git` names a submodule's git directory, and git could not read that "
    "directory's core.worktree": "reports what the marker names and that git could not answer",
    "its `.git` names another directory called `.git`, or a submodule's git directory "
    "that does not record it as that submodule's working tree": "reports what the marker names",
    "its folder name is chosen by the superproject's .gitmodules": _OBSERVED,
    "no name was derived from the path)": _DETAIL,
    "no project id was derived": _OBSERVED,
    "no project identity could be resolved": _OBSERVED,
    "no single project id can be read from the repository's origin": _OBSERVED,
    "resolution continues without it": _OBSERVED,
    "see the pact-memory skill": "points at the skill",
    "the checkout is a submodule": _OBSERVED,
    "the declared directory could not be resolved": _OBSERVED,
    "the declared directory could not be resolved (declared via X": _OBSERVED,
    "the directory is at or below a `.git` marker git could not answer for":
        "reports that the marker is there, at or above the directory, and git did not answer",
    "the directory is at or below a `.git` marker git could not answer for (X": _OBSERVED,
    "the directory is at or below a `.git` marker git could not answer for (cwd": _OBSERVED,
    "the directory is at or below a `.git` marker git could not answer for (the working "
    "directory is inside that work tree": _OBSERVED,
    "the `.git` marker is X)": "names where the marker is",
    "the repository at '/home/u/repo' has an origin remote that could not be read or "
    "represented as a project id": _OBSERVED,
    "the user runs that, not an agent": "says who runs the repair",
    "the write was refused rather than filed under an unknown project": _OBSERVED,
    # The clauses of the details a resolved identity carries
    # (`_printer_details`).
    "its remote.origin.url list holds an empty value": _OBSERVED,
    "the url git uses depends on the git version": "says why the origin is not read",
    "remove the empty value":
        "asks the user to remove the empty value from their own origin url list; "
        "it changes no `.git` file or directory",
    "no remote.origin.url comes from the repository's own config file, and a url from an "
    "included file can differ between worktrees and branches": _OBSERVED,
    "the repository's own config file sets no remote.origin.url but holds an include "
    "section, and an included file can set the origin on one branch or worktree and not "
    "on another": _OBSERVED,
    "the repository's own config file could not be read to check it for include sections":
        _OBSERVED,
    "this checkout's git directory lies outside it": _OBSERVED,
    "pinning it is a user decision": "names the decision as the user's",
}


def _clauses(sentence: str) -> list:
    """`sentence` split at `; `, `: ` and `, so ` outside backticks, each
    part without its closing stop."""
    parts, start, quoted, index = [], 0, False, 0
    while index < len(sentence):
        if sentence[index] == "`":
            quoted = not quoted
        elif not quoted:
            separator = next((s for s in ("; ", ": ", ", so ") if sentence.startswith(s, index)), None)
            if separator:
                parts.append(sentence[start:index])
                start = index + len(separator)
                index = start
                continue
        index += 1
    parts.append(sentence[start:])
    return [part.strip().rstrip(".!?").strip() for part in parts if part.strip()]


def _sentences(text: str) -> list:
    """`text` split after every `.`, `!` or `?` followed by a space or the
    end, outside backticks. No capital letter is required after the stop,
    so a sentence opening with a backtick or a lower-case word, and an
    abbreviation such as `e.g.`, only split more finely."""
    text = " ".join(text.split())
    sentences, start, quoted = [], 0, False
    for index, char in enumerate(text):
        if char == "`":
            quoted = not quoted
        elif char in ".!?" and not quoted and (index + 1 == len(text) or text[index + 1] == " "):
            sentences.append(text[start:index + 1].strip())
            start = index + 1
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return sentences


def _rendered(node, constants=None) -> "str | None":
    """The text a string expression prints, each formatted value shown as
    `X`, except a plain module string constant in `constants` (name: text),
    which prints its text; None for any other expression."""
    constants = constants or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant)
            else constants[part.value.id]
            if isinstance(part.value, ast.Name) and part.value.id in constants
            and part.conversion == -1 and part.format_spec is None
            else "X"
            for part in node.values
        )
    return None


def _detail_alternatives(node) -> list:
    """The expressions a detail can be: each operand of an `or` and each
    branch of a conditional expression, followed through both."""
    if isinstance(node, ast.BoolOp):
        return [leaf for value in node.values for leaf in _detail_alternatives(value)]
    if isinstance(node, ast.IfExp):
        return [*_detail_alternatives(node.body), *_detail_alternatives(node.orelse)]
    return [node]


def _rung_details() -> tuple:
    """((reason, detail), ...) for every rung detail a decline prints, read
    from memory_api's source: each `detail=` of a `_decline` call, with the
    call's reason, and each `no_working_tree_detail=` (the
    `no_working_tree` decline's wording). A reason read from the root
    guard's arm chooser is each reason it returns (`chooser_elements`), and
    a detail is each operand of an `or` and each branch of a conditional
    (`_detail_alternatives`); a formatted module string constant
    (`module_string_constants`) prints its text. Also every such expression
    that is not a string, as (line, source), so a form this reader cannot
    render is named, never skipped."""
    from test_identity_rung_parity import chooser_elements, module_string_constants

    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    constants = module_string_constants(tree)
    pairs, unrendered = set(), []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        callee = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
        for keyword in call.keywords:
            if keyword.arg == "no_working_tree_detail":
                reasons = {"no_working_tree"}
            elif keyword.arg == "detail" and callee == "_decline":
                first = call.args[1] if len(call.args) > 1 else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    reasons = {first.value}
                else:
                    reasons, more = chooser_elements(tree, first) if first is not None else (set(), ["no reason"])
                    unrendered += more
            else:
                continue
            for value in _detail_alternatives(keyword.value):
                text = _rendered(value, constants)
                if text is None or not reasons:
                    unrendered.append(ast.unparse(value))
                else:
                    pairs |= {(reason, text) for reason in reasons}
    return pairs, unrendered


# A rung detail expression that is not a string, and why it adds no
# wording of its own.
_PASSED_THROUGH_DETAILS = {
    "no_working_tree_detail": "the parameter carrying a caller's `no_working_tree_detail=`, each read above",
}


def _detail_values(expression, function, module_tree, seen=frozenset()) -> tuple:
    """(texts, unresolved) for a detail expression in `function` of
    `module_tree`: every string it can hold, followed through conditional
    expressions, f-strings, calls to the module's own functions (their
    returns), and names and dict lookups whose every binding, by each
    binding form the compiler defines (the rung parity `_CalleeReader`), is
    an assignment, a table only when nothing changes it after its literal
    (`_changed_containers`). Any other form, a name with any other binding
    or a table filled later included, is returned in `unresolved` as source
    text."""
    from test_identity_rung_parity import _reader_for

    reader = _reader_for(module_tree)

    def held(name):
        return reader.held(name.id, reader.scope.get(id(name)))

    functions = {
        node.name: node for node in module_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def values(node, scope, seen):
        if isinstance(node, ast.Constant):
            return ({node.value} - {None}, []) if node.value is None or isinstance(node.value, str) else (set(), [ast.unparse(node)])
        if isinstance(node, ast.IfExp):
            parts = [values(node.body, scope, seen), values(node.orelse, scope, seen)]
            return set().union(*(p[0] for p in parts)), [u for p in parts for u in p[1]]
        if isinstance(node, ast.JoinedStr):
            texts, unresolved = {""}, []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    texts = {text + part.value for text in texts}
                    continue
                inner, more = values(part.value, scope, seen)
                unresolved += more
                texts = {text + value for text in texts for value in inner}
            return texts, unresolved
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            tables = held(node.value)
            if tables and all(kind == "value" and isinstance(table, ast.Dict) for kind, table in tables) and not (
                reader.changes(node.value.id, reader.scope.get(id(node.value)))  # filled after its literal
            ):
                parts = [values(value, scope, seen) for _kind, table in tables for value in table.values]
                return set().union(*(p[0] for p in parts)), [u for p in parts for u in p[1]]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            if node.func.id in seen:
                return set(), []
            called = functions[node.func.id]
            parts = [
                values(ret.value, called, seen | {node.func.id})
                for ret in ast.walk(called) if isinstance(ret, ast.Return) and ret.value is not None
            ]
            return set().union(set(), *(p[0] for p in parts)), [u for p in parts for u in p[1]]
        if isinstance(node, ast.Name):
            token = (node.id, id(reader.scope.get(id(node))))
            if token in seen:
                return set(), []
            sources = held(node)
            if sources and all(kind == "value" for kind, _value in sources):
                parts = [values(value, reader.scope.get(id(value)), seen | {token}) for _kind, value in sources]
                return set().union(*(p[0] for p in parts)), [u for p in parts for u in p[1]]
        return set(), [ast.unparse(node)]

    return values(expression, function, seen)


# The fields and parameters that carry a decline's detail to the warning and
# refusal printers.
_DETAIL_CARRIERS = {"cause_detail", "decline_detail", "_decline_detail"}


def _printer_details() -> tuple:
    """(texts, unresolved) for every detail the decline printers can append,
    read from the code. In memory_api, every value bound to a carrier (a
    `cause_detail` or `decline_detail` argument, by keyword or position, and
    every `_decline_detail` assignment) is followed with `_detail_values`;
    a carrier passed on from a carrier is skipped, and a value read from a
    resolved identity's `detail` is every `detail` a `RootIdentity` is built
    with in project_identity, followed the same way. Every form left over
    is in `unresolved` as (module, source)."""
    from scripts import project_identity as pi

    trees = {
        name: ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for name, module in (("memory_api", memory_api), ("project_identity", pi))
    }
    texts, unresolved = set(), []

    def take(module, expression, function):
        found, more = _detail_values(expression, function, trees[module])
        texts.update(found)
        unresolved.extend((module, source) for source in more)

    for function in ast.walk(trees["project_identity"]):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "RootIdentity":
                bound = [k.value for k in call.keywords if k.arg == "detail"] + call.args[5:6]
                for expression in bound:
                    take("project_identity", expression, function)

    tree = trees["memory_api"]
    signatures = {
        node.name: [a.arg for a in [*node.args.posonlyargs, *node.args.args]]
        for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own = {a.arg for a in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
        bound = []
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                bound += [k.value for k in node.keywords if k.arg in _DETAIL_CARRIERS]
                names = signatures.get(callee, [])
                offset = 1 if names[:1] in (["self"], ["cls"]) and isinstance(node.func, ast.Attribute) else 0
                bound += [
                    arg for index, arg in enumerate(node.args)
                    if index + offset < len(names) and names[index + offset] in _DETAIL_CARRIERS
                ]
            elif isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Attribute) and t.attr in _DETAIL_CARRIERS for t in node.targets):
                    bound.append(node.value)
        for expression in bound:
            if isinstance(expression, ast.Name) and expression.id in own and expression.id in _DETAIL_CARRIERS:
                continue  # passed on: read where the caller binds it
            field = (
                expression.attr if isinstance(expression, ast.Attribute)
                else expression.args[1].value
                if isinstance(expression, ast.Call) and getattr(expression.func, "id", None) == "getattr"
                and len(expression.args) >= 2 and isinstance(expression.args[1], ast.Constant)
                else None
            )
            if field in _DETAIL_CARRIERS:
                continue  # a carrier field: read where it is bound
            if field == "detail":
                continue  # a resolved identity's detail: every RootIdentity detail, read above
            take("memory_api", expression, function)
    return texts, unresolved


def _override_reasons() -> set:
    """Every reason an override read can give, read from project_identity's
    source: the third argument of each `OverrideRead(...)` that is a
    string."""
    from scripts import project_identity as pi

    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    return {
        call.args[2].value for call in ast.walk(tree)
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "OverrideRead"
        and len(call.args) == 3 and isinstance(call.args[2], ast.Constant)
        and isinstance(call.args[2].value, str)
    }


def _refusal_openings() -> list:
    """What each unresolved-identity refusal says it refused: the default
    of `_unresolved_identity_message`'s parameter, and every string a call
    passes to it."""
    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    openings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_unresolved_identity_message":
            openings += [ast.literal_eval(d) for d in node.args.defaults]
        if (
            isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_unresolved_identity_message"
            and node.args
        ):
            openings.append(ast.literal_eval(node.args[0]))
    return openings


def _printed_identity_messages(monkeypatch, caplog) -> list:
    """Every message a declined identity prints, on every print path:

    - the decline warning and every unresolved-identity refusal (the
      refused write and each other opening the module passes), for every
      decline cause the module names and for none, each with no override
      path and with one inside a `.git` directory, and with no refusal
      detail and with each detail the printers can append
      (`_printer_details`, read from the code);
    - the decline warning with each rung's own detail, for that rung's
      cause;
    - the ignored-override warning and every refusal that names the
      ignored override, for every reason an override read gives, with the
      path and with the path withheld."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(memory_api, "get_project_dir_from_session_record", lambda: None)
    openings = _refusal_openings()
    messages = []

    def refusals(record, ignored, detail):
        instance = PACTMemory.__new__(PACTMemory)
        instance._decline_reason = record
        instance._override_ignored = ignored
        instance._decline_detail = detail
        return [instance._unresolved_identity_message(opening) for opening in openings]

    pairs, _unrendered = _rung_details()
    for reason, rung_detail in sorted(pairs):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            memory_api._warn_declined(
                {"reason": reason, "source": "cwd", "override_path": None},
                root="/home/u/repo", detail=rung_detail,
            )
        messages += [entry.getMessage() for entry in caplog.records]
    for reason in sorted(_override_reasons()):
        for path in ("/home/u/repo/.git/pact-project-id", None):
            ignored = {"path": path, "reason": reason}
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                PACTMemory._warn_override_ignored(type("Identity", (), {"override_ignored": ignored}))
            messages += [entry.getMessage() for entry in caplog.records]
            for token in [*sorted(memory_api._DECLINE_CAUSES), None]:
                record = {"reason": token, "source": "cwd", "override_path": None} if token else None
                messages += refusals(record, ignored, None)

    details = [None, *sorted(_printer_details()[0])]
    for token in [*sorted(memory_api._DECLINE_CAUSES), None]:
        for override_path in (None, "/home/u/repo/.git/pact-project-id"):
            for detail in details:
                record = {"reason": token, "source": "cwd", "override_path": override_path}
                caplog.clear()
                with caplog.at_level(logging.WARNING):
                    memory_api._warn_declined(record if token else None, root="/home/u/repo", cause_detail=detail)
                messages += [entry.getMessage() for entry in caplog.records]
                messages += refusals(record if token else None, None, detail)
    return messages


def _printed_clauses(messages) -> set:
    """Every clause of every message, an override reason shown as
    `<reason>` (the reasons are `_override_reasons()`, data rather than
    wording)."""
    reasons = _override_reasons()
    clauses = set()
    for message in messages:
        for sentence in _sentences(message):
            for clause in _clauses(sentence):
                for reason in reasons:
                    clause = clause.replace(f"({reason})", "(<reason>)")
                clauses.add(clause)
    return clauses


def _moved_linked_worktree(base: Path) -> Path:
    """A linked worktree moved with `mv`: git still lists its entry at the
    old path, which git marks prunable, and its `.git` names that entry."""
    main = make_repo(base / "main", remote="https://github.com/me/moved.git")
    make_worktree(main, base / "linked", "wt")
    (base / "linked").rename(base / "target")
    return base / "target"


def _copied_linked_worktree(base: Path) -> Path:
    """A copy of a live linked worktree: git lists the original, not
    prunable, and the copy's `.git` names the original's entry."""
    import shutil

    main = make_repo(base / "main", remote="https://github.com/me/copied.git")
    original = make_worktree(main, base / "linked", "wt")
    shutil.copytree(original, base / "target", symlinks=True)
    return base / "target"


# Layouts only this module's refusal arms build: {shape name: builder}.
LOCAL_SHAPES = {
    "moved_linked_worktree": _moved_linked_worktree,
    "copied_linked_worktree": _copied_linked_worktree,
}

# How a linked worktree comes to carry each field git prints in its listing
# record (`memory_api._PORCELAIN_ORDER`): the git commands run after `git
# worktree add`, and whether the worktree is added detached.
_RECORD_FIELD_SETUP = {
    "branch ": ((), False),
    "detached": ((), True),
    "locked": ((("worktree", "lock"),), False),
    "locked ": ((("worktree", "lock", "--reason", "on a drive"),), False),
}
# The fields no linked-worktree state sets: every record has `worktree` and
# `HEAD`, `bare` is the main repository's alone, and `prunable` is git's own
# mark on the entry this arm reads, not a state set up beforehand.
_RECORD_FIELDS_NOT_SET_UP = {"worktree ", "HEAD ", "bare", "prunable "}


def _relocated_worktree(field: str, how: str):
    """A builder for a linked worktree carrying `field`, then moved with `mv`
    or copied, and git's own answer to whether it marks the entry prunable."""
    import shutil

    commands, detached = _RECORD_FIELD_SETUP[field]
    marked = {}

    def build(base: Path) -> Path:
        main = make_repo(base / "main", remote="https://github.com/me/relocated.git")
        linked = base / "linked"
        if detached:
            run_git("worktree", "add", "-q", "--detach", str(linked), cwd=main)
        else:
            make_worktree(main, linked, "wt")
        for command in commands:
            run_git(*command, str(linked), cwd=main)
        if how == "moved":
            linked.rename(base / "target")
        else:
            shutil.copytree(linked, base / "target", symlinks=True)
        listing = subprocess.run(
            ["git", "-C", str(main), "worktree", "list", "--porcelain", "-z"],
            capture_output=True, check=True,
        ).stdout.decode()
        records = [record.split("\0") for record in listing.split("\0\0") if record]
        entry = [r for r in records if r[0] == f"worktree {linked.resolve()}"]
        marked["prunable"] = bool(entry) and any(f.startswith("prunable ") for f in entry[0])
        return base / "target"

    return build, marked


class TestARelocatedWorktreeIsMovedOnlyWhereGitSaysSo:
    """A worktree whose `.git` names an entry git lists elsewhere is named
    moved exactly when git's listing marks that entry prunable, on every
    route, with `-z` and on a git that rejects it. The layouts cover every
    field git prints for a linked worktree, each moved with `mv` and copied,
    so neither a missing folder nor any other field stands in for git's
    mark."""

    def test_every_record_field_is_set_up_or_named(self):
        from scripts import memory_api

        fields = {key for key, _place in memory_api._PORCELAIN_ORDER}
        assert fields == set(_RECORD_FIELD_SETUP) | _RECORD_FIELDS_NOT_SET_UP, fields

    def test_control_git_marks_some_moved_entries_and_not_others(self, tmp_path):
        """Control: among the moved layouts git marks some entries prunable
        and leaves others unmarked, so the arm below checks both answers."""
        marks = set()
        for index, field in enumerate(sorted(_RECORD_FIELD_SETUP)):
            build, marked = _relocated_worktree(field, "moved")
            base = tmp_path / str(index)
            base.mkdir()
            build(base)
            marks.add(marked["prunable"])
        assert marks == {True, False}, marks

    @pytest.mark.parametrize("mode", ["PASS", "REJECT_Z"], ids=["git lists with -z", "git rejects -z"])
    @pytest.mark.parametrize("how", ["moved", "copied"])
    @pytest.mark.parametrize("field", sorted(_RECORD_FIELD_SETUP))
    def test_moved_exactly_when_git_marks_it_prunable(self, tmp_path, monkeypatch, field, how, mode):
        from fixtures.identity_routes import SHAPES, _with_sub
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        build, marked = _relocated_worktree(field, how)
        monkeypatch.setitem(SHAPES, "relocated_worktree", _with_sub(build))
        answers = _route_answers(tmp_path / "cells", "relocated_worktree", mode)
        expected = memory_api._REDIRECT_CAUSE_DETAILS[
            memory_api.REDIRECT_MOVED if marked["prunable"] else memory_api.REDIRECT_UNLISTED
        ]
        assert answers, "control: the routes ran"
        wrong = {route: answer for route, answer in answers.items() if answer[2] != expected}
        assert wrong == {}, (field, how, marked, wrong)


class TestARedirectRefusalNamesWhatWasObserved:
    """Every `gitfile_redirect` refusal carries the detail of the one cause
    that reached it, and no sentence of the refusal claims more: the cause
    sentence names no other repository's git directory, and the remedy
    advises no change to a `.git` and names the user as the one who runs a
    repair or decides on a pin."""

    # What each fake makes git (or the filesystem) show, in the words a
    # refusal reaching it must use. Pinned here, not read from the detail
    # constants, so a detail swapped for another cause's sentence fails.
    OBSERVED = {
        "foreign": "names another directory called `.git`",
        "unlisted": "worktree list does not hold it",
        "unlistable": "could not list",
        "no_git_dir": "no git directory",
        "unresolvable": "would not resolve",
        "unreadable_listing": "not git's fields in git's order",
        "empty_listing": "holds no record, or an empty one",
        "core_worktree_unread": "could not read that directory's core.worktree",
        "moved": "which git marks prunable",
    }

    # (shape, fake, what was observed): every route that refuses must name it.
    CASES = [
        *[(shape, "PASS", "foreign") for shape in (
            "foreign_gitdir", "relative_foreign_gitdir", "symlinked_git",
            "forged_submodule_admin", "symlinked_submodule_admin",
        )],
        *[(shape, "PASS", "unlisted") for shape in (
            "forged_worktree_admin", "forged_separate_store_worktree",
            "forged_worktree_config_dotgit",
        )],
        ("control_own_linked_worktree", "FAIL_LISTING", "unlistable"),
        ("control_own_linked_worktree", "EMPTY_LISTING", "empty_listing"),
        ("control_own_checkout", "FAIL_GIT_DIR", "no_git_dir"),
        ("control_own_linked_worktree", "LOOP_GIT_DIR", "unresolvable"),
        ("control_path_worktree_holding_a_newline", "REJECT_Z", "unreadable_listing"),
        ("control_own_submodule", "FAIL_CORE_WORKTREE", "core_worktree_unread"),
        ("moved_linked_worktree", "PASS", "moved"),
        ("copied_linked_worktree", "PASS", "unlisted"),
    ]

    @staticmethod
    def _details() -> list:
        """Every refusal detail a `gitfile_redirect` can carry."""
        from scripts import memory_api
        from scripts import project_identity as pi

        return [
            *memory_api._REDIRECT_CAUSE_DETAILS.values(),
            pi.FOREIGN_GIT_DIR_DETAIL, pi.CORE_WORKTREE_UNREAD_DETAIL,
        ]

    @pytest.mark.parametrize("shape,fake,key", CASES)
    def test_each_refusal_names_its_own_cause(self, tmp_path, monkeypatch, shape, fake, key):
        from fixtures.identity_routes import SHAPES, _with_sub
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        if shape not in SHAPES:
            monkeypatch.setitem(SHAPES, shape, _with_sub(LOCAL_SHAPES[shape]))
        mode = fake
        if fake == "LOOP_GIT_DIR":
            # git's `--git-dir` answer names a symlink loop, so it will not resolve.
            (tmp_path / "loop-a").symlink_to(tmp_path / "loop-b")
            (tmp_path / "loop-b").symlink_to(tmp_path / "loop-a")
            real = memory_api._git_rev_parse
            monkeypatch.setattr(
                memory_api, "_git_rev_parse",
                lambda start, *args: str(tmp_path / "loop-a")
                if args == ("--git-dir",) else real(start, *args),
            )
            mode = "PASS"
        answers = _route_answers(tmp_path / "cells", shape, mode)
        refused = {route: a for route, a in answers.items() if a[1] == "gitfile_redirect"}
        assert refused, f"control: {shape} refused on some route ({answers})"
        wrong = {route: a for route, a in refused.items() if self.OBSERVED[key] not in (a[2] or "")}
        assert wrong == {}, f"{shape} under {fake} did not say {self.OBSERVED[key]!r}: {wrong}"

    def test_each_detail_names_one_observation(self):
        """The details are distinct, and each holds exactly one of the
        observed phrases, so no refusal can name two causes or another
        cause's evidence."""
        details = self._details()
        assert len(set(details)) == len(details), details
        for detail in details:
            held = [key for key, phrase in self.OBSERVED.items() if phrase in detail]
            assert len(held) == 1, (detail, held)
        for key, phrase in self.OBSERVED.items():
            assert sum(phrase in detail for detail in details) == 1, (key, phrase)

    def test_the_cause_docstring_names_every_redirect_cause(self):
        from scripts import memory_api

        causes = {
            name for name in dir(memory_api)
            if name.startswith("REDIRECT_") and name.isupper()
        }
        assert causes == {
            f"REDIRECT_{key.upper()}" for key in memory_api._REDIRECT_CAUSE_DETAILS
        }, causes
        doc = memory_api._worktree_redirect_cause.__doc__
        missing = sorted(name for name in causes if f"`{name}`" not in doc)
        assert missing == [], missing

    def test_every_function_passing_a_listing_answer_names_each_non_record_answer(self):
        """Every function in the memory skill or the backlog that can return
        the listing reader's answer unchanged (`_walk_answers`, derived from
        the code) admits a string in its return annotation and, where it has
        a docstring, names there every answer that is no record list
        (`_listing_sentinels`, read from the readers)."""
        passers, _taking = _walk_answers(_listing_answer_modules())
        assert ("skills/pact-memory/scripts/memory_api.py", "_worktree_records") in passers, sorted(passers)  # control
        named = [f"`{sentinel}`" for sentinel in sorted(_listing_sentinels())]
        wrong = {
            key: (ast.unparse(function.returns) if function.returns else None, ast.get_docstring(function))
            for key, function in passers.items()
            if function.returns is None or not _union_admits_str(function.returns)
            or not all(name in (ast.get_docstring(function) or "".join(named)) for name in named)
        }
        assert wrong == {}, wrong

    def test_the_cause_sentence_claims_no_other_repository(self):
        from scripts import memory_api

        cause = memory_api._DECLINE_CAUSES["gitfile_redirect"]
        assert "another repository" not in cause, cause
        details = list(memory_api._REDIRECT_CAUSE_DETAILS.values())
        assert not [d for d in details if "another repository" in d], details

    def test_every_printed_clause_is_a_described_one(self, monkeypatch, caplog):
        """A moved worktree and a separate-git-dir checkout are layouts git
        made, and an unreadable repository is not a broken one, so no
        message a declined identity prints, on either the warning or the
        refused write, may advise any change to a `.git`. Every printed
        clause, on every print path, must equal one
        `_DESCRIBED_PRINTED_CLAUSES` entry, and every entry must still be
        printed. Controls: every detail source the printers read is followed
        to its text (`_printer_details`), the origin details among them;
        every remedy the module defines (each module-level name holding
        REMEDY) appears in a message read; and every rung detail that is not
        a string is a named pass-through."""
        from scripts import project_identity as pi

        details, unresolved = _printer_details()
        assert unresolved == [], unresolved  # every detail source is followed to its text
        assert pi.EMPTY_URL_ENTRY_DETAIL in details, sorted(details)  # control: origin details are read
        messages = [" ".join(message.split()) for message in _printed_identity_messages(monkeypatch, caplog)]
        remedies = [
            text
            for name, value in vars(memory_api).items() if "REMEDY" in name or "REMEDIES" in name
            for text in (value.values() if isinstance(value, dict) else [value])
        ]
        assert len(remedies) >= 2, remedies  # control: the module still names its remedies
        unprinted = [r for r in remedies if not any(" ".join(r.split()) in m for m in messages)]
        assert unprinted == [], unprinted  # control: each remedy is in the population read
        _pairs, unrendered = _rung_details()
        assert set(unrendered) == set(_PASSED_THROUGH_DETAILS), unrendered
        printed = _printed_clauses(messages)
        described = set(_DESCRIBED_PRINTED_CLAUSES)
        assert printed - described == set(), sorted(printed - described)
        assert described - printed == set(), sorted(described - printed)

    @pytest.mark.parametrize(
        "added",
        [
            "Rename its .git file to .git.bak.",
            "The `.git` file can be trashed.",
            "Deletion of the `.git` file clears this.",
            "Run `find . -maxdepth 1 -name .git -delete` here.",
            "Its `.git` names a submodule's git directory; replace it.",
            "Check that git can answer for this checkout, or run from the repository the `.git` points at.",
            "Delete the gitfile.",
            "Move the .GIT folder aside.",
            "Its marker names another directory; delete it.",
        ],
    )
    def test_a_clause_not_described_fails_the_census(self, added):
        """A sentence in any wording or spelling, added to a printed message,
        carries a clause the census does not hold, so the census fails; a
        near copy of a described clause fails too."""
        message = memory_api._REDIRECT_REMEDY + " " + added
        assert _printed_clauses([message]) - set(_DESCRIBED_PRINTED_CLAUSES), added

    def test_control_the_described_remedy_passes_the_census(self):
        assert _printed_clauses([memory_api._REDIRECT_MOVED_REMEDY]) <= set(_DESCRIBED_PRINTED_CLAUSES)

    def test_the_redirect_remedy_names_the_users_own_layouts_as_theirs(self):
        """The remedy's sentences: the first asks only for read-only git
        answers or a run elsewhere; the one naming `git worktree repair`,
        printed only in the moved worktree's remedy, says the user runs it,
        not an agent; the one naming a separate git dir calls it the user's
        decision; and no sentence advises any change to a `.git` file or
        directory."""
        from scripts import memory_api

        base = _sentences(memory_api._REDIRECT_REMEDY)
        moved = _sentences(memory_api._REDIRECT_MOVED_REMEDY)
        assert moved[:len(base)] == base, (base, moved)  # the moved remedy adds to the base
        sentences = moved
        commands = [re.findall(r"`(git [^`]*)`", sentence) for sentence in sentences]
        assert commands[0] == ["git rev-parse --git-dir", "git worktree list"], sentences[0]
        repair = [s for s, found in zip(sentences, commands) if "git worktree repair" in found]
        assert len(repair) == 1 and "the user runs that, not an agent" in repair[0], sentences
        assert repair[0] not in base, base  # repair is advised only where git shows the move
        separate = [s for s in sentences if "--separate-git-dir" in s]
        assert len(separate) == 1 and "user decision" in separate[0], sentences
        for sentence, found in zip(sentences, commands):
            assert set(found) <= {
                "git rev-parse --git-dir", "git worktree list", "git worktree repair",
                "git init --separate-git-dir",
            }, sentence
            if "`.git`" in sentence:
                assert "run from the repository the `.git` points at" in sentence, sentence


class TestNoRouteBindsWithoutAGitDir:
    """The forged-worktree check needs git's `--git-dir` answer to know
    whether it applies; a git that gives none, or one that will not resolve,
    declines on every route instead of binding what the other answers say."""

    @pytest.mark.parametrize("shape", ["forged_worktree_admin", "control_own_checkout"])
    def test_every_route_declines_when_git_gives_no_git_dir(self, tmp_path, shape):
        from scripts import memory_api

        no_git_dir = memory_api._REDIRECT_CAUSE_DETAILS[memory_api.REDIRECT_NO_GIT_DIR]
        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, shape, "FAIL_GIT_DIR")
        wrong = {
            route: answer for route, answer in answers.items()
            if answer[0] is not None or answer[1] != "gitfile_redirect"
            or answer[2] != no_git_dir
        }
        assert wrong == {}, f"{shape} bound or misreported without a git dir: {wrong}"

    def test_control_the_same_checkout_binds_when_git_answers(self, tmp_path):
        from fixtures.identity_routes import OWN
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, "control_own_checkout", "PASS")
        own = pi.normalize_remote_url(OWN)
        assert {answer[0] for answer in answers.values()} == {own}, answers

    def test_a_git_dir_that_will_not_resolve_is_no_root(self, tmp_path, monkeypatch):
        """A `--git-dir` answer that names a symlink loop resolves to
        nothing, so the root lookup gives no root and the guard names the
        path that would not resolve."""
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        checkout = make_repo(tmp_path / "proj")
        (tmp_path / "loop-a").symlink_to(tmp_path / "loop-b")
        (tmp_path / "loop-b").symlink_to(tmp_path / "loop-a")
        real = memory_api._git_rev_parse
        monkeypatch.setattr(
            memory_api, "_git_rev_parse",
            lambda start, *args: str(tmp_path / "loop-a")
            if args == ("--git-dir",) else real(start, *args),
        )
        common = memory_api.main_repo_common_dir(str(checkout))
        assert common is not None  # control: git answers the common dir
        assert memory_api.main_repo_root(str(checkout), common) is None
        assert memory_api._worktree_redirect_cause(checkout, common) == (
            memory_api.REDIRECT_UNRESOLVABLE
        )

    def test_a_top_level_that_will_not_resolve_is_unresolvable(self, tmp_path):
        """The listing check compares the checkout's top level with git's
        listed worktrees; a top level that names a symlink loop resolves to
        nothing, so the check names a path that would not resolve rather than
        answering from a comparison it could not make."""
        from scripts import memory_api

        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree = tmp_path / "wt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        common = (main / ".git").resolve()
        assert memory_api._worktree_redirect_cause(worktree, common) is None  # control: listed
        (tmp_path / "loop-a").symlink_to(tmp_path / "loop-b")
        (tmp_path / "loop-b").symlink_to(tmp_path / "loop-a")
        assert memory_api._worktree_redirect_cause(
            worktree, common, toplevel=tmp_path / "loop-a"
        ) == memory_api.REDIRECT_UNRESOLVABLE


class TestAnUnreadableCoreWorktreeIsNamed:
    """A `.git` naming a submodule's git dir is checked against that git
    dir's `core.worktree`. When git cannot read it, the refusal says so,
    not that the git dir records another working tree."""

    def test_a_real_submodule_declines_naming_the_unread_core_worktree(self, tmp_path):
        from scripts import project_identity as pi

        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, "control_own_submodule", "FAIL_CORE_WORKTREE")
        refused = {route: a for route, a in answers.items() if a[1] == "gitfile_redirect"}
        assert refused, f"control: the submodule refused on some route ({answers})"
        wrong = {
            route: a for route, a in refused.items() if a[2] != pi.CORE_WORKTREE_UNREAD_DETAIL
        }
        assert wrong == {}, wrong

    def test_control_the_same_submodule_binds_when_git_reads_it(self, tmp_path):
        assert_outside_repository(tmp_path)
        answers = _route_answers(tmp_path, "control_own_submodule", "PASS")
        assert not [a for a in answers.values() if a[1] == "gitfile_redirect"], answers


def _git_dir_layouts(root: Path) -> dict:
    """Each start inside a git dir, and the separate-git-dir linked worktree:
    {name: (start, expected checkout or None)}. Real repositories with fake
    origins; nothing is fetched."""
    main = make_repo(root / "main", remote="https://example.invalid/o/main.git")
    make_worktree(main, root / "wt")
    hub = root / "hub.git"
    run_git("clone", "-q", "--bare", str(main), str(hub), cwd=root)
    sep = make_separate_git_dir_repo(root / "sep", root / "store" / "repo.git")
    run_git("remote", "add", "origin", "https://example.invalid/o/sep.git", cwd=sep)
    make_worktree(sep, root / "sepwt")
    other = root / "other"
    other.mkdir()
    twin = make_separate_git_dir_repo(root / "twin", other / ".git")
    run_git("remote", "add", "origin", "https://example.invalid/o/twin.git", cwd=twin)
    superproject = make_repo(root / "super", remote="https://example.invalid/o/super.git")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(main), "sub", cwd=superproject)
    forged = root / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {root / 'store' / 'repo.git' / 'worktrees' / 'sepwt'}\n",
                                 encoding="utf-8")
    return {
        "main/.git": (main / ".git", main),
        "main/.git/refs": (main / ".git" / "refs", main),
        "a linked worktree's admin dir": (main / ".git" / "worktrees" / "wt", root / "wt"),
        "the twin's other/.git": (other / ".git", other),
        "the bare hub": (hub, None),
        "a separate-git-dir store": (root / "store" / "repo.git", None),
        "a submodule's git dir": (superproject / ".git" / "modules" / "sub", None),
        "a separate-git-dir linked worktree": (root / "sepwt", None),
        "a .git file naming that worktree's admin dir": (forged, None),
    }


class TestAStartInsideAGitDirIsItsCheckout:
    """A start inside a non-bare checkout's git dir resolves to that checkout
    for the memory skill and for the project-memory root alike; a git dir with
    no checkout git can name declines, and the project memory never lives
    inside `.git`. A separate-git-dir repository's linked worktree, whose main
    checkout git cannot name, is its own project root; a `.git` file naming
    that worktree's admin dir is not.

    Real repositories and git."""

    def test_each_start_names_its_checkout_or_declines(self, identity_env, monkeypatch):
        from shared import project_scope

        layouts = _git_dir_layouts(identity_env)
        answers, expected = {}, {}
        for name, (start, checkout) in layouts.items():
            _fresh_scope_state()
            monkeypatch.chdir(start)
            mapped = project_scope.git_dir_checkout(start)
            detected = PACTMemory._detect_project_identity_with_legacy()
            root, cause = project_scope.memory_root_of(start)
            answers[name] = (mapped, detected.project_id, detected.decline_reason and detected.decline_reason["reason"],
                             root, cause)
        main_id, sep_id = "example.invalid/o/main", "example.invalid/o/sep"
        main, unverified = identity_env / "main", project_scope.ROOT_CAUSE_UNVERIFIED
        expected = {
            "main/.git": ((True, main.resolve()), main_id, None, main.resolve(), None),
            "main/.git/refs": ((True, main.resolve()), main_id, None, main.resolve(), None),
            "a linked worktree's admin dir": ((True, (identity_env / "wt").resolve()), main_id, None,
                                              main.resolve(), None),
            "the twin's other/.git": ((True, (identity_env / "other").resolve()), "example.invalid/o/twin", None,
                                      (identity_env / "other").resolve(), None),
            "the bare hub": ((True, None), None, "no_working_tree", None, unverified),
            "a separate-git-dir store": ((True, None), None, "no_working_tree", None, unverified),
            "a submodule's git dir": ((True, None), None, "submodule_checkout", None, unverified),
            "a separate-git-dir linked worktree": ((False, None), sep_id, None,
                                                   (identity_env / "sepwt").resolve(), None),
            "a .git file naming that worktree's admin dir": ((False, None), None, "gitfile_redirect", None,
                                                             unverified),
        }
        assert answers == expected

    def test_a_declared_start_names_its_checkout_or_declines(self, identity_env, monkeypatch):
        """The declared rung maps a CLAUDE_PROJECT_DIR inside a git dir the
        same way, from a working directory outside any repository."""
        layouts = _git_dir_layouts(identity_env)
        outside = identity_env / "outside"
        outside.mkdir()
        answers = {}
        for name, (start, _checkout) in layouts.items():
            _fresh_scope_state()
            monkeypatch.chdir(outside)
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(start))
            detected = PACTMemory._detect_project_identity_with_legacy()
            answers[name] = (detected.project_id,
                             detected.decline_reason and detected.decline_reason["reason"])
        main_id = "example.invalid/o/main"
        assert answers == {
            "main/.git": (main_id, None),
            "main/.git/refs": (main_id, None),
            "a linked worktree's admin dir": (main_id, None),
            "the twin's other/.git": ("example.invalid/o/twin", None),
            "the bare hub": (None, "no_working_tree"),
            "a separate-git-dir store": (None, "no_working_tree"),
            "a submodule's git dir": (None, "submodule_checkout"),
            "a separate-git-dir linked worktree": ("example.invalid/o/sep", None),
            "a .git file naming that worktree's admin dir": (None, "gitfile_redirect"),
        }
