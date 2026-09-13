"""
Location: pact-plugin/tests/test_removed_worktree_record.py
Summary: Arms for the worktree identity record. session_init writes
         `worktree-identity.json` into the session's own folder, for every role,
         when the session starts inside a linked worktree; the working-memory
         write guard reads it back, and `stays_in_declared_project` lets it
         decide a declaration that no longer exists.
Used by/with:
- hooks/session_init.py: `_record_worktree_identity`, reached through `main()`.
- skills/pact-memory/scripts/pact_session.py:
  `get_worktree_identity_from_session_record`.
- skills/pact-memory/scripts/working_memory.py:
  `_refuse_ambient_sync_on_declared_scope_escape` and `sync_to_claude_md`.
- hooks/shared/project_scope.py: `stays_in_declared_project`.

Every repository lives under tmp_path, and the autouse conftest redirect puts
the config root there too. Arms that read the record delete
PYTEST_CURRENT_TEST in the test body, after asserting the home redirect holds,
the way the session-record tests do.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import teammate_frame
from scripts import pact_session
from scripts.working_memory import (
    AmbientSyncRefused,
    SyncResult,
    _refuse_ambient_sync_on_declared_scope_escape,
)
from shared.pact_context import _build_session_path, project_slug
from shared.paths import get_claude_config_dir
import shared.project_scope as project_scope

SID = "aabb1122-0000-0000-0000-00000000c0de"
SEED = "# Project Memory\n\n## Retrieved Context\n\n## Working Memory\n"


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30, check=True,
    )


def _repo(path: Path, document: bool = False) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", ".", cwd=path)
    (path / "README").write_text("seed\n")
    _git("add", "README", cwd=path)
    _git("commit", "-qm", "seed", cwd=path)
    if document:
        _document(path)
    return path


def _document(directory: Path) -> Path:
    target = directory / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SEED)
    return target


def _worktree(main: Path, path: Path) -> Path:
    _git("worktree", "add", "-q", str(path), "-b", path.name, cwd=main)
    return path


def _remove_worktree(main: Path, path: Path) -> None:
    """`git worktree remove` run against the owning repository, from outside both."""
    _git("-C", str(main), "worktree", "remove", "--force", str(path), cwd=main.parent)
    assert not path.exists()


def _rev_parse(directory: Path, flag: str) -> str:
    out = _git("rev-parse", flag, cwd=directory).stdout.strip()
    return os.path.realpath(os.path.join(str(directory), out))


def _identity_for(declared: Path, **override) -> dict:
    """The record the design specifies, derived here from git, not from the writer."""
    return {
        "session_id": SID,
        "declared": os.path.realpath(declared),
        "worktree": _rev_parse(declared, "--show-toplevel"),
        "common_dir": _rev_parse(declared, "--git-common-dir"),
        **override,
    }


def _record_path(project_dir: Path) -> Path:
    return _build_session_path(project_slug(str(project_dir)), SID) / "worktree-identity.json"


def _plant(project_dir: Path, identity: dict) -> Path:
    target = _record_path(project_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(identity))
    return target


@pytest.fixture
def layout_root(tmp_path, monkeypatch):
    probe = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=30)
    assert probe.returncode != 0, f"tmp_path sits inside a repository: {probe.stdout}"
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "layout"
    root.mkdir()
    return root


def _enable_record_reading(monkeypatch) -> None:
    real_home = Path(os.path.expanduser("~")).resolve()
    assert Path.home().resolve() != real_home, (
        "REFUSING to read records: Path.home() is the real home, so the glob "
        "would search the operator's live session folders"
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)


def _guard(monkeypatch, declared: Path, resolved_root: Path) -> None:
    """Run the write guard for an ambient resolution into `resolved_root`."""
    _enable_record_reading(monkeypatch)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
    _refuse_ambient_sync_on_declared_scope_escape(
        None, None, resolved_root, resolved_root / ".claude" / "CLAUDE.md"
    )


def _admitted(monkeypatch, declared: Path, resolved_root: Path) -> bool:
    try:
        _guard(monkeypatch, declared, resolved_root)
    except AmbientSyncRefused:
        return False
    return True


def _sibling_worktree_removed(root: Path):
    """R holds the document; W is `git worktree add ../W`, then removed from outside."""
    main = _repo(root / "R", document=True)
    worktree = _worktree(main, root / "W")
    identity = _identity_for(worktree)
    _remove_worktree(main, worktree)
    return main, worktree, identity


def _worktree_under_main_removed(root: Path):
    """A worktree inside its own repository's tree: the ancestor walk admits it."""
    main = _repo(root / "R", document=True)
    worktree = _worktree(main, main / ".worktrees" / "x")
    identity = _identity_for(worktree)
    _remove_worktree(main, worktree)
    return main, worktree, identity


# ---------------------------------------------------------------------------
# The guard admits a removed worktree its own session recorded.
# ---------------------------------------------------------------------------

class TestARecordAdmitsTheRemovedWorktreeItNames:

    def test_a_sibling_worktree_removed_from_outside_still_admits_its_own_project(
        self, layout_root, monkeypatch
    ):
        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, identity)

        _guard(monkeypatch, worktree, main)

    def test_a_worktree_subdirectory_session_removed_still_admits_its_project(
        self, layout_root, monkeypatch
    ):
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, layout_root / "W")
        subdirectory = worktree / "sub"
        subdirectory.mkdir()
        _plant(subdirectory, _identity_for(subdirectory))
        _remove_worktree(main, worktree)

        _guard(monkeypatch, subdirectory, main)

    def test_the_sync_writes_into_the_recorded_project_after_the_worktree_is_removed(
        self, layout_root, monkeypatch
    ):
        """End to end through `sync_to_claude_md`, so the guard is reached from
        the entry point and not only called directly."""
        import scripts.working_memory as wm

        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, identity)
        document = main / ".claude" / "CLAUDE.md"
        before = document.read_bytes()
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(main)

        result = wm.sync_to_claude_md(
            {"context": "record-arm", "goal": "record-arm"}, memory_id="0" * 32
        )

        assert result == SyncResult(SyncResult.WROTE), result
        assert document.read_bytes() != before


# ---------------------------------------------------------------------------
# A record decides only for its exact declaration, and only into its repository.
# ---------------------------------------------------------------------------

class TestARecordDecidesOnlyForItsDeclaration:

    def test_an_identity_naming_another_repository_is_refused(
        self, layout_root, monkeypatch
    ):
        """The layout the ancestor walk admits without a record, so the refusal
        can only come from the record."""
        main, worktree, identity = _worktree_under_main_removed(layout_root)
        other = _repo(layout_root / "other")
        _plant(worktree, dict(identity, common_dir=os.path.realpath(other / ".git")))

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, worktree, main)

    def test_a_subdirectory_recorded_under_a_nested_repository_is_refused_in_the_enclosing_one(
        self, layout_root, monkeypatch
    ):
        """W/inner is a worktree of an independent repository N nested in M's
        worktree W. Without a record, W/inner/sub's nearest ancestor W lies in M,
        so a resolution into M is admitted; the record names N."""
        enclosing = _repo(layout_root / "M", document=True)
        outer = _worktree(enclosing, layout_root / "W")
        nested = _repo(layout_root / "N")
        inner = _worktree(nested, outer / "inner")
        subdirectory = inner / "sub"
        subdirectory.mkdir()
        _plant(subdirectory, _identity_for(subdirectory))
        _remove_worktree(nested, inner)

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, subdirectory, enclosing)

    def test_a_record_from_another_session_is_refused(self, layout_root, monkeypatch):
        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, dict(identity, session_id="another-session"))

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, worktree, main)

    def test_a_resolved_root_that_is_not_a_checkout_root_is_refused(
        self, layout_root, monkeypatch
    ):
        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, identity)
        below_the_root = main / "docs"
        _document(below_the_root)

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, worktree, below_the_root)

    def test_a_matching_record_whose_git_check_errors_is_refused(
        self, layout_root, monkeypatch
    ):
        """The ancestor walk would admit this layout, so a refusal when git
        cannot answer for the resolved root shows the record does not fall
        through to it."""
        main, worktree, identity = _worktree_under_main_removed(layout_root)
        _plant(worktree, identity)
        assert _admitted(monkeypatch, worktree, main), "control: the matching record admits"

        real = project_scope._rev_parse_path
        resolved = main.resolve()
        monkeypatch.setattr(
            project_scope, "_rev_parse_path",
            lambda directory, flag: None if Path(directory).resolve() == resolved
            else real(directory, flag),
        )

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, worktree, main)

    @pytest.mark.parametrize("layout", ["sibling_removed", "under_main_removed"])
    def test_a_record_for_another_declaration_does_not_change_the_verdict(
        self, layout_root, monkeypatch, layout
    ):
        build = {
            "sibling_removed": _sibling_worktree_removed,
            "under_main_removed": _worktree_under_main_removed,
        }[layout]
        main, worktree, identity = build(layout_root)
        record = _plant(worktree, dict(identity, declared=str(layout_root / "elsewhere")))

        with_record = _admitted(monkeypatch, worktree, main)
        record.unlink()
        without_record = _admitted(monkeypatch, worktree, main)

        assert with_record == without_record
        # The two layouts span both verdicts, so the equality is not one answer twice.
        assert without_record is (layout == "under_main_removed")


# ---------------------------------------------------------------------------
# The reader.
# ---------------------------------------------------------------------------

class TestTheRecordReader:

    def test_a_valid_record_is_returned(self, layout_root, monkeypatch):
        identity = {"session_id": SID, "declared": "/a", "worktree": "/b", "common_dir": "/c"}
        _plant(layout_root, identity)
        _enable_record_reading(monkeypatch)

        assert pact_session.get_worktree_identity_from_session_record() == identity

    @pytest.mark.parametrize("body", [
        {"session_id": "another-session", "declared": "/a", "worktree": "/b", "common_dir": "/c"},
        {"session_id": SID, "declared": "a", "worktree": "/b", "common_dir": "/c"},
        {"session_id": SID, "declared": "/a", "worktree": "/b"},
        {"session_id": SID, "declared": "/a", "worktree": 7, "common_dir": "/c"},
        "{not json",
    ], ids=["other-session", "relative-path", "missing-field", "non-string", "corrupt"])
    def test_an_invalid_record_reads_as_none(self, layout_root, monkeypatch, body):
        target = _record_path(layout_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body if isinstance(body, str) else json.dumps(body))
        _enable_record_reading(monkeypatch)

        assert pact_session.get_worktree_identity_from_session_record() == {}

    def test_a_test_process_reads_no_record(self, layout_root, monkeypatch):
        identity = {"session_id": SID, "declared": "/a", "worktree": "/b", "common_dir": "/c"}
        _plant(layout_root, identity)
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test (call)")

        assert pact_session.get_worktree_identity_from_session_record() == {}


# ---------------------------------------------------------------------------
# The writer, through session_init.main().
# ---------------------------------------------------------------------------

def _claude_md_state(root: Path) -> dict:
    real_home = Path(os.path.expanduser("~"))
    watched = [real_home / ".claude" / "CLAUDE.md", real_home / "CLAUDE.md"]
    state: dict = {str(p): p.read_bytes() for p in root.rglob("CLAUDE.md")}
    state.update({str(p): p.stat().st_mtime_ns for p in watched if p.exists()})
    return state


def _start_session(monkeypatch, tmp_path, project_dir: Path) -> None:
    """Run session_init.main() for a teammate frame, with every CLAUDE.md writer patched out."""
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    stdin = json.dumps({"session_id": SID, "source": "startup", **teammate_frame()})
    before = _claude_md_state(tmp_path)
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.build_context_cache", return_value=(Path("/tmp/ctx.json"), {})), \
         patch("session_init.persist_context", return_value=None), \
         patch("session_init.append_event"), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin)), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0
    assert _claude_md_state(tmp_path) == before, "session start changed a CLAUDE.md"


class TestSessionStartWritesTheRecord:

    def test_session_start_writes_the_identity_for_a_teammate_frame(
        self, layout_root, monkeypatch, tmp_path
    ):
        main_repo = _repo(layout_root / "R")
        worktree = _worktree(main_repo, layout_root / "W")

        _start_session(monkeypatch, tmp_path, worktree)

        record = _record_path(worktree)
        assert record.is_file(), f"no identity record at {record}"
        assert json.loads(record.read_text()) == {
            "session_id": SID,
            "declared": os.path.realpath(worktree),
            "worktree": os.path.realpath(worktree),
            "common_dir": os.path.realpath(main_repo / ".git"),
        }

    def test_session_start_in_a_worktree_subdirectory_records_the_subdirectory(
        self, layout_root, monkeypatch, tmp_path
    ):
        main_repo = _repo(layout_root / "R")
        worktree = _worktree(main_repo, layout_root / "W")
        subdirectory = worktree / "sub"
        subdirectory.mkdir()

        _start_session(monkeypatch, tmp_path, subdirectory)

        written = json.loads(_record_path(subdirectory).read_text())
        assert written["declared"] == os.path.realpath(subdirectory)
        assert written["worktree"] == os.path.realpath(worktree)

    def test_session_start_in_a_main_checkout_writes_no_identity(
        self, layout_root, monkeypatch, tmp_path
    ):
        main_repo = _repo(layout_root / "R")

        _start_session(monkeypatch, tmp_path, main_repo)

        assert not _record_path(main_repo).exists()
        sessions = get_claude_config_dir() / "pact-sessions"
        assert list(sessions.rglob("worktree-identity.json")) == []

    def test_the_identity_record_is_written_through_state_file(
        self, layout_root, monkeypatch, tmp_path
    ):
        main_repo = _repo(layout_root / "R")
        worktree = _worktree(main_repo, layout_root / "W")

        _start_session(monkeypatch, tmp_path, worktree)

        record = _record_path(worktree)
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
        assert list(record.parent.glob("*.tmp")) == []

    def test_the_record_session_start_writes_is_the_one_the_guard_reads(
        self, layout_root, monkeypatch, tmp_path
    ):
        main_repo = _repo(layout_root / "R", document=True)
        worktree = _worktree(main_repo, layout_root / "W")
        _start_session(monkeypatch, tmp_path, worktree)
        _remove_worktree(main_repo, worktree)

        _guard(monkeypatch, worktree, main_repo)
