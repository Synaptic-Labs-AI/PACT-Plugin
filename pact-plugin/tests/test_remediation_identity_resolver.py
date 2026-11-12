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
    seed_session_evidence,
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
    def _gate(store, bucket, roots):
        from scripts.database import db_connection, legacy_candidate_plausibility

        with db_connection(store) as conn:
            return legacy_candidate_plausibility(conn, bucket, roots)

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
        outcome, detail = self._gate(store, "super", [superproject])
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
