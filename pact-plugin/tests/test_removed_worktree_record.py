"""
Location: pact-plugin/tests/test_removed_worktree_record.py
Summary: Arms for the worktree identity record. session_init writes
         `worktree-identity.json` into the session's own folder, for lead and
         teammate frames, when the session starts inside a linked worktree; the
         working-memory write guard reads it back, and
         `stays_in_declared_project` lets it decide a declaration that no longer
         exists.
Used by/with:
- hooks/shared/project_scope.py: `_record_worktree_identity`, reached through
  session_init's `main()`.
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
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.project_dir import write_session_context
from fixtures.role_frames import teammate_frame
from scripts import database as scripts_database
from scripts import memory_api, pact_session
from scripts.memory_api import (
    PACTMemory,
    ProjectIdentityUnresolvedError,
    ProjectScopeDisagreementError,
)
from scripts.database import (
    db_connection,
    ensure_initialized,
    get_memory,
    list_memories,
    search_memories_by_text,
)
from scripts.database import PrefixTooShortError
from scripts.config import store_scope
from scripts.graph import add_file_relation, get_file_id, track_file
import scripts.embedding_catchup as embedding_catchup
import scripts.search as memory_search
from scripts.working_memory import (
    AmbientSyncRefused,
    SyncResult,
    _refuse_ambient_sync_on_declared_scope_escape,
)
from shared.pact_context import _build_session_path, project_slug
from shared.paths import get_claude_config_dir
import shared.project_scope as project_scope
from helpers import known_root  # noqa: E402

# claude_md_ambient (module-wide): the worktree-identity escape-guard
# exemption is this module's subject; it needs the ambient resolution the
# suite-wide redirect would remove. See tests/conftest.py.
pytestmark = pytest.mark.claude_md_ambient

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
    # Reset the discovery cache: it is module-level, and a parametrized arm
    # would otherwise read the PREVIOUS parameter's session record.
    monkeypatch.setattr(
        project_scope, "_discovered_record", project_scope._DISCOVERY_UNSET
    )


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

    def test_an_out_of_tree_worktree_is_not_resolved_from_the_record(
        self, layout_root, monkeypatch
    ):
        """SUPPORTED-SHAPE NARROWING, not a regression. The declared worktree
        lives OUTSIDE the recorded checkout, so the record cannot prove it
        belongs to that repository -- and after `git worktree prune` a genuine
        out-of-tree worktree is indistinguishable from a forged redirect. The
        record is therefore not trusted, the guard falls through, and it
        refuses. PACT's own `$REPO/.worktrees/{branch}` layout is unaffected.
        """
        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, identity)

        with pytest.raises(AmbientSyncRefused):
            _guard(monkeypatch, worktree, main)

    def test_a_worktree_subdirectory_session_removed_still_admits_its_project(
        self, layout_root, monkeypatch
    ):
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
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

        main, worktree, identity = _worktree_under_main_removed(layout_root)
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

    def test_an_identity_naming_another_repository_admits_only_the_containing_one(
        self, layout_root, monkeypatch
    ):
        """A record naming repository B must never admit B. With containment it
        is not trusted at all, so a resolution into B is refused and a
        resolution into the declaration's own checkout is admitted by the
        ancestor rule -- not by the record."""
        main, worktree, identity = _worktree_under_main_removed(layout_root)
        other = _repo(layout_root / "other")
        _plant(worktree, dict(identity, common_dir=os.path.realpath(other / ".git")))

        assert _admitted(monkeypatch, worktree, other) is False
        assert _admitted(monkeypatch, worktree, main) is True

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

        # The record names N, which does not contain the declaration: it must
        # never admit N. The enclosing repository is admitted by the ancestor
        # rule, without the record's help.
        assert _admitted(monkeypatch, subdirectory, nested) is False
        assert _admitted(monkeypatch, subdirectory, enclosing) is True

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
        worktree = _worktree(main_repo, main_repo / ".worktrees" / "W")
        _start_session(monkeypatch, tmp_path, worktree)
        _remove_worktree(main_repo, worktree)

        _guard(monkeypatch, worktree, main_repo)


# ---------------------------------------------------------------------------
# archive_pin judges the same layouts through the same record.
# ---------------------------------------------------------------------------

class TestArchivePinReadsTheRecordToo:
    """archive_pin.resolve_claude_md calls the same scope check as the memory
    sync, so it takes the same record and reaches the same verdict."""

    def test_archive_pin_resolves_the_recorded_project_after_the_sibling_worktree_is_removed(
        self, layout_root, monkeypatch
    ):
        import archive_pin

        main, worktree, identity = _worktree_under_main_removed(layout_root)
        _plant(worktree, identity)
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(main)

        path, _base = archive_pin.resolve_claude_md()

        assert Path(path).resolve() == (main / ".claude" / "CLAUDE.md").resolve()

    def test_archive_pin_never_resolves_a_record_naming_another_repository(
        self, layout_root, monkeypatch
    ):
        """Never B. The record is not trusted and there is no cwd rung, so
        the archive refuses with the missing cause rather than resolving any
        document -- neither the other repository's nor, through the cwd,
        main's."""
        import archive_pin

        main, worktree, identity = _worktree_under_main_removed(layout_root)
        other = _repo(layout_root / "other", document=True)
        _plant(worktree, dict(identity, common_dir=os.path.realpath(other / ".git")))
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(main)

        with pytest.raises(archive_pin._Unevaluable) as excinfo:
            archive_pin.resolve_claude_md()

        reason = excinfo.value.reason
        assert "no longer exists" in reason, reason
        assert str(other) not in reason, reason


# ---------------------------------------------------------------------------
# #114: the record resolves all THREE call sites after `git worktree prune`
# ---------------------------------------------------------------------------

def _pruned_worktree(root: Path):
    """R holds the document; W is a linked worktree removed by rmtree+prune.

    `git worktree prune` deletes the listing entry AND the admin dir, and it
    is the normal cleanup path. A record verified against the worktree LISTING
    would be rejected here, which is the shape this class exists for.
    """
    main = _repo(root / "R", document=True)
    worktree = _worktree(main, main / ".worktrees" / "feature")
    identity = _identity_for(worktree)
    shutil.rmtree(worktree)
    _git("worktree", "prune", cwd=main)
    listing = _git("worktree", "list", "--porcelain", cwd=main).stdout
    assert str(worktree) not in listing, "the prune left the worktree listed"
    return main, worktree, identity


def _outside_the_repository(root: Path) -> Path:
    """A cwd that is not in any checkout, so the fall-through cannot answer."""
    path = root / "elsewhere"
    path.mkdir()
    probe = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode != 0, probe.stdout
    return path


class TestTheRecordResolvesAllThreeSites:
    """One prune shape, three call sites, the record as the only answer.

    The cwd is OUTSIDE the repository in every arm, so the #113 fall-through
    cannot resolve MAIN. What is measured is the record fallback itself.
    """

    def test_the_archive_resolves_main(self, tmp_path, monkeypatch):
        import archive_pin

        main, removed, identity = _pruned_worktree(tmp_path / "layout")
        _plant(removed, identity)
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(_outside_the_repository(tmp_path))

        path, base = archive_pin.resolve_claude_md()

        assert Path(path).resolve() == (main / ".claude" / "CLAUDE.md").resolve()
        assert Path(base).resolve() == main.resolve()

    def test_the_sync_writes_main(self, tmp_path, monkeypatch):
        from scripts import working_memory as wm

        main, removed, identity = _pruned_worktree(tmp_path / "layout")
        _plant(removed, identity)
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(_outside_the_repository(tmp_path))

        result = wm.sync_to_claude_md(
            {"context": "record-arm", "goal": "record-arm"}, memory_id="0" * 32
        )

        assert result == wm.SyncResult(wm.SyncResult.WROTE), result
        assert "record-arm" in (main / ".claude" / "CLAUDE.md").read_text()

    def test_the_pin_cap_gate_is_live_on_mains_document(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import gate_target

        main, removed, identity = _pruned_worktree(tmp_path / "layout")
        _plant(removed, identity)
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(_outside_the_repository(tmp_path))

        target = gate_target(str(main / ".claude" / "CLAUDE.md"))

        assert target is not None, "the pin cap is DARK on the removed-worktree shape"
        assert target.target.resolve() == (main / ".claude" / "CLAUDE.md").resolve()


class TestTheRecordCannotBeTrusted:
    def test_an_absent_record_names_the_removed_worktree(self, tmp_path, monkeypatch):
        """No record: the archive refuses, and the message says the declared
        directory is gone rather than describing a git failure."""
        import archive_pin

        _main, removed, _identity = _pruned_worktree(tmp_path / "layout")
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(_outside_the_repository(tmp_path))

        with pytest.raises(archive_pin._Unevaluable) as excinfo:
            archive_pin.resolve_claude_md()

        assert "no longer exists" in excinfo.value.reason, excinfo.value.reason
        assert "session record" in excinfo.value.reason, excinfo.value.reason

    def test_a_forged_record_refuses_and_never_redirects(self, tmp_path, monkeypatch):
        """A record naming this declaration whose common dir is not a checkout
        verifies nothing: the archive refuses with the missing cause and
        names the record, never falling through to the cwd's checkout."""
        import archive_pin

        main, removed, identity = _pruned_worktree(tmp_path / "layout")
        not_a_checkout = tmp_path / "not-a-checkout"
        not_a_checkout.mkdir()
        _plant(
            removed,
            dict(identity, common_dir=str(not_a_checkout / ".git")),
        )
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(main)

        with pytest.raises(archive_pin._Unevaluable) as excinfo:
            archive_pin.resolve_claude_md()

        assert "did not verify" in excinfo.value.reason, excinfo.value.reason

    def test_the_pin_gate_advisory_names_the_removed_worktree(
        self, tmp_path, monkeypatch
    ):
        from pin_caps_gate import _gate

        main, removed, _identity = _pruned_worktree(tmp_path / "layout")
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(main)

        decision = _gate({
            "tool_name": "Write",
            "agent_type": "pact-orchestrator",
            "tool_input": {
                "file_path": str(main / ".claude" / "CLAUDE.md"),
                "content": "# notes\n",
            },
        })

        assert decision is not None
        assert decision.verdict == "ALLOW_ADVISORY"
        assert "no longer exists" in decision.reason, decision.reason
        assert "session record" in decision.reason, decision.reason

    def test_the_save_admits_the_resolved_main_end_to_end(
        self, tmp_path, monkeypatch, memory_store
    ):
        """THE RUNTIME LANE'S REFUSAL, driven through the same child the
        archive launches: the child's CLAUDE_PROJECT_DIR is pinned to MAIN
        while the session record names the removed worktree. Resolution alone
        is not the claim; the SAVE must not answer SCOPE_DISAGREEMENT."""
        import archive_pin

        main, removed, identity = _pruned_worktree(tmp_path / "layout")
        # THE CHILD NEEDS A CONFIG ROOT IT CAN SEE. The autouse redirect
        # patches `Path.home` IN THIS PROCESS only; a spawned child reads the
        # operator's real home unless CLAUDE_CONFIG_DIR names the sandbox. With
        # the default redirect the child found no record and this arm passed
        # without exercising the admission at all.
        config_root = tmp_path / "config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_root))
        session_dir = _record_path(removed).parent
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "session_id": SID,
            "project_dir": str(removed),
        }))
        (session_dir / "worktree-identity.json").write_text(json.dumps(identity))
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(removed))
        monkeypatch.chdir(_outside_the_repository(tmp_path))

        _path, base = archive_pin.resolve_claude_md()
        assert Path(base).resolve() == main.resolve()

        rc, _stdout, stderr = archive_pin._run_memory_cli(
            ["save", "--stdin"],
            db_path=str(memory_store("mem.db")),
            stdin_data=json.dumps(
                {"context": "record-arm", "goal": "record-arm"}
            ),
            cwd=base,
        )

        assert rc == 0, (rc, stderr)
        assert "SCOPE_DISAGREEMENT" not in stderr, stderr
        assert "disagrees with this session" not in stderr, stderr


# ---------------------------------------------------------------------------
# The redirect: a record naming another repository must never be trusted
# ---------------------------------------------------------------------------

class TestARecordNamingAnotherRepositoryRedirectsNothing:
    """The record names a REAL repository, its candidate self-reports, and it
    does not contain the declaration. Before containment every read surface
    trusted it; these arms pin "never B" from three working directories."""

    @pytest.mark.parametrize("where", ["repo_a", "repo_b", "outside"])
    def test_no_surface_resolves_repository_b(self, layout_root, monkeypatch, where):
        import archive_pin
        import scripts.working_memory as wm
        from shared.project_scope import session_memory_root
        from helpers import known_root

        main, worktree, identity = _worktree_under_main_removed(layout_root)
        other = _repo(layout_root / "other", document=True)
        _plant(worktree, dict(identity, common_dir=os.path.realpath(other / ".git")))
        session_dir = _record_path(worktree).parent
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "session_id": SID,
            "project_dir": str(worktree),
        }))
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))

        if where == "repo_a":
            cwd = main
        elif where == "repo_b":
            cwd = other
        else:
            cwd = tmp_path_for_outside(layout_root)
        monkeypatch.chdir(cwd)
        other_doc = (other / ".claude" / "CLAUDE.md").resolve()
        before_other = other_doc.read_bytes()

        # Synchronous surfaces never hand back B.
        assert session_memory_root().root != other.resolve()
        assert known_root(str(other)) is not None  # B is real
        try:
            resolved, _base = archive_pin.resolve_claude_md()
        except archive_pin._Unevaluable:
            resolved = None
        assert resolved is None or Path(resolved).resolve() != other_doc

        try:
            wm.sync_to_claude_md(
                {"context": "redirect", "goal": "redirect"},
                memory_id="0" * 32,
            )
        except wm.AmbientSyncRefused:
            pass
        assert other_doc.read_bytes() == before_other, "sync wrote repository B"

        # The save side: env naming B must disagree with the record.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
        assert pact_session.env_record_project_dir_disagreement() is not None


def tmp_path_for_outside(layout_root: Path) -> Path:
    path = layout_root / "outside"
    path.mkdir(exist_ok=True)
    probe = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode != 0, probe.stdout
    return path


class TestTheRecordIsVerifiedWhenItIsWritten:
    """WRITE-TIME VERIFICATION. The relationship is provable while the
    declaration exists; `git worktree prune` removes the evidence, so a forged
    record must be refused at the only moment it can be caught."""

    def test_a_forged_gitfile_does_not_get_a_record(self, layout_root, monkeypatch):
        from shared.project_scope import (
            _record_worktree_identity,
        )

        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        admin = next((main / ".git" / "worktrees").iterdir())
        forged = layout_root / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {admin}\n")

        _record_worktree_identity(SID, str(forged))

        assert not _record_path(forged).exists(), (
            "a record was written for a directory git does not list"
        )
        assert known_root(str(forged)) is None, (
            "the forged directory became a known root"
        )
        assert worktree.exists()

    def test_a_genuine_in_tree_worktree_writes_the_record_it_reads_back(
        self, layout_root, monkeypatch
    ):
        from shared.project_scope import (
            RECORD_CONTAINED,
            _record_worktree_identity,
            get_worktree_identity_from_session_record,
            record_resolution,
        )

        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _record_worktree_identity(SID, str(worktree))
        assert _record_path(worktree).exists(), "no record written"

        _remove_worktree(main, worktree)
        _enable_record_reading(monkeypatch)

        identity = get_worktree_identity_from_session_record()
        status, root = record_resolution(worktree, identity)
        assert status == RECORD_CONTAINED, (status, identity)
        assert root.resolve() == main.resolve()


class TestAnOutOfTreePrunedWorktreeAfterContainment:
    def test_every_surface_refuses_and_the_gate_still_reports(
        self, layout_root, monkeypatch
    ):
        """The narrowing, stated on each surface: the record cannot prove an
        out-of-tree worktree belonged to the repository, so nothing is trusted
        and each surface either refuses or reports. The gate must REPORT, not
        fall silent."""
        import archive_pin
        import scripts.working_memory as wm
        from pin_caps_gate import _gate

        main, worktree, identity = _sibling_worktree_removed(layout_root)
        _plant(worktree, identity)
        session_dir = _record_path(worktree).parent
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "session_id": SID,
            "project_dir": str(worktree),
        }))
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(main)

        with pytest.raises(archive_pin._Unevaluable):
            archive_pin.resolve_claude_md()
        # The sync reports the missing root rather than resolving anything,
        # so the escape guard never has a resolution to refuse.
        result = wm.sync_to_claude_md(
            {"context": "oot", "goal": "oot"}, memory_id="0" * 32
        )
        assert result == wm.SyncResult(wm.SyncResult.ROOT_UNKNOWN), result
        decision = _gate({
            "tool_name": "Write",
            "agent_type": "pact-orchestrator",
            "tool_input": {
                "file_path": str(main / ".claude" / "CLAUDE.md"),
                "content": "# notes\n",
            },
        })
        assert decision is not None, "the gate went dark and said nothing"
        assert decision.verdict == "ALLOW_ADVISORY"
        assert "no longer exists" in decision.reason, decision.reason

        # And the save side refuses the retarget it cannot prove.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(main))
        assert pact_session.env_record_project_dir_disagreement() is not None


# ---------------------------------------------------------------------------
# #1688 — project IDENTITY through the record after the worktree is removed.
#
# The arms above prove the record decides a WRITE TARGET. These prove it
# decides the project's IDENTITY: which store key a save files under and which
# rows a later sync selects. I1 and I2 are a matched pair; the pair is the
# control, and each arm alone would prove nothing.
# ---------------------------------------------------------------------------

_TOKEN = "REMOVED-WORKTREE-ROW-TOKEN"


def _row_counts(db_path: Path) -> dict:
    with db_connection(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("memories", "files")
        }


def _null_project_rows(db_path: Path, table: str) -> int:
    with db_connection(db_path) as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id IS NULL"
        ).fetchone()[0]


class TestTheRecordDecidesProjectIdentityAfterRemoval:
    """I1, I2, I1e2e and I1b: the acceptance arms."""

    def test_i1_a_save_and_a_later_sync_after_removal_select_the_same_rows(
        self, layout_root, monkeypatch
    ):
        """Acceptance criterion (a), asserted at the store, not the seam."""
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _plant(worktree, _identity_for(worktree))
        _enable_record_reading(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        # Strategy 2 runs from the PROCESS cwd even on the decline arm; a
        # non-repo cwd keeps it from answering for this fixture.
        monkeypatch.chdir(layout_root)

        planted = pact_session.get_worktree_identity_from_session_record()
        assert planted.get("declared") == os.path.realpath(worktree), (
            "the record the fix reads was not live; every assertion below "
            "would be measuring something other than the mechanism"
        )

        db_path = layout_root / "store.db"
        live = PACTMemory(session_id="s", db_path=db_path)
        assert live.project_id == "R"
        memory_id = live.save({"context": _TOKEN}, sync_to_claude=False)
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        assert row is not None and row[0] == "R", (
            "the live save did not key the row to the repository"
        )

        _remove_worktree(main, worktree)
        assert not worktree.exists()

        after = PACTMemory(session_id="s", db_path=db_path)
        assert after.project_id == "R", (
            "re-derivation fell back to the worktree folder name after removal"
        )
        assert after._project_id_source == "worktree identity record"

        written = after.sync(claude_md_root=main)
        assert written == [memory_id], (
            "the post-removal sync did not select the row saved while the "
            "worktree was live"
        )
        assert after.last_sync_status == SyncResult.WROTE
        assert _TOKEN in (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")

    def test_i2_without_a_record_the_same_fixture_declines(
        self, layout_root, monkeypatch
    ):
        """The matched negative control: the RECORD decides, not the removal."""
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        identity = _identity_for(worktree)
        _enable_record_reading(monkeypatch)
        # LIVE AND EMPTY, PROVEN. A guard-blocked reader returns {} even with a
        # valid record planted, so plant one, read it back, remove it, and only
        # then assert the empty state; the read-back is what separates the two.
        record_path = _plant(worktree, identity)
        assert pact_session.get_worktree_identity_from_session_record().get(
            "declared"
        ) == os.path.realpath(worktree), (
            "the reader is blocked; the empty assertion below would prove nothing"
        )
        record_path.unlink()
        assert pact_session.get_worktree_identity_from_session_record() == {}
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(layout_root)

        db_path = layout_root / "store.db"
        live = PACTMemory(session_id="s", db_path=db_path)
        assert live.project_id == "R", "the live half must match I1's fixture"
        live.save({"context": _TOKEN}, sync_to_claude=False)
        before = _row_counts(db_path)

        _remove_worktree(main, worktree)

        after = PACTMemory(session_id="s", db_path=db_path)
        assert after.project_id is None
        assert after._project_id_source == "unresolved"
        with pytest.raises(ProjectIdentityUnresolvedError):
            after.save({"context": "MUST-NOT-LAND"}, sync_to_claude=False)
        assert after.last_sync_status == SyncResult.REFUSED
        assert _row_counts(db_path) == before
        assert _null_project_rows(db_path, "memories") == 0
        assert _null_project_rows(db_path, "files") == 0
        assert after.sync(claude_md_root=main) == []
        assert after.last_sync_status == SyncResult.EMPTY

    def test_i1e2e_the_reader_trusts_a_record_the_REAL_WRITER_produced(
        self, layout_root, monkeypatch
    ):
        """The acceptance arm: drive the writer, not a hand-planted record.

        A hand-planted record proves the READER accepts records shaped the way
        this test writes them. The property the fix rests on is that the
        record was write-time verified, and only the real writer proves the
        writer and the reader have not drifted apart.
        """
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _enable_record_reading(monkeypatch)

        project_scope._record_worktree_identity(SID, str(worktree))
        record = pact_session.get_worktree_identity_from_session_record()
        assert record, "the real writer produced no record for a live linked worktree"
        assert record.get("declared") == os.path.realpath(worktree)
        assert record.get("common_dir")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(layout_root)
        _remove_worktree(main, worktree)

        memory = PACTMemory(session_id="s", db_path=layout_root / "e2e.db")
        assert memory.project_id == "R"
        assert memory._project_id_source == "worktree identity record"

    def test_i1b_a_separate_git_dir_record_never_names_the_container(
        self, layout_root, monkeypatch
    ):
        """I1b: the common dir's parent is NOT a checkout in this layout.

        P0 and unconditional. A fix that reduces the recovered root to
        `common_dir.parent` would name `gitdirs` here, reintroducing the class
        a prior security fix rejected.
        """
        gitdirs = layout_root / "gitdirs"
        gitdirs.mkdir()
        sep = gitdirs / "sep.git"
        main = layout_root / "work"
        main.mkdir()
        _git("init", "-q", f"--separate-git-dir={sep}", str(main), cwd=layout_root)
        (main / "README").write_text("seed\n")
        _git("add", "README", cwd=main)
        _git("commit", "-qm", "seed", cwd=main)

        worktree = _worktree(main, main / ".worktrees" / "W")
        identity = _identity_for(worktree)
        _plant(worktree, identity)
        _enable_record_reading(monkeypatch)
        assert pact_session.get_worktree_identity_from_session_record()

        # The structural reason the record must not be trusted here.
        assert project_scope.recorded_main_root(Path(worktree), identity) is None

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(layout_root)
        _remove_worktree(main, worktree)

        memory = PACTMemory(session_id="s", db_path=layout_root / "sep.db")
        assert memory.project_id is None
        assert memory._project_id_source == "unresolved"
        assert memory.project_id != "gitdirs", (
            "derived the common-dir CONTAINER as the project identity"
        )
        assert memory.project_id != "W", (
            "derived the worktree folder name as the project identity"
        )


class TestUnresolvedIdentityRefusesWrites:
    """W1: a None identity must refuse, never write a NULL-keyed row."""

    def _prepopulated(self, tmp_path) -> Path:
        db_path = tmp_path / "store.db"
        seed = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        seed_id = seed.save({"context": "SEED"}, sync_to_claude=False)
        # THE PREMISE OF EVERY ASSERTION BELOW: the store is NOT empty. A
        # silent no-write makes `counts == before` (0 == 0) and both NULL
        # checks (0) vacuous, which is the failure class this control exists
        # to prevent. Assert the seed's own row, not merely that save returned.
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (seed_id,)
            ).fetchone()
        assert row is not None and row[0] == "other-proj-a", (
            "the seed row did not land; W1's count comparisons would prove "
            "nothing"
        )
        return db_path

    def test_w1_a_refused_save_writes_no_null_row(self, tmp_path, monkeypatch):
        db_path = self._prepopulated(tmp_path)
        before = _row_counts(db_path)
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None

        with pytest.raises(ProjectIdentityUnresolvedError) as excinfo:
            memory.save({"context": "MUST-NOT-LAND"}, sync_to_claude=False)

        assert isinstance(excinfo.value, ProjectScopeDisagreementError), (
            "the subclass must keep existing except-sites working"
        )
        assert memory.last_sync_status == SyncResult.REFUSED
        assert _row_counts(db_path) == before, "a refused save changed the store"
        assert _null_project_rows(db_path, "memories") == 0
        assert _null_project_rows(db_path, "files") == 0

    def test_w1_the_refusal_names_the_cause_and_is_not_a_disagreement(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "store.db"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "gone"))
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)

        with pytest.raises(ProjectIdentityUnresolvedError) as excinfo:
            memory.save({"context": "x"}, sync_to_claude=False)

        text = str(excinfo.value)
        assert "no project identity could be resolved" in text
        assert "Nothing was written" in text
        assert "disagree" not in text.lower(), (
            "the refusal reads as the env/record disagreement, which has a "
            "different cause and a different remedy"
        )

    def test_w1_the_declined_state_is_reachable_from_a_deleted_cwd(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: the real wrap-up shape, not only a stubbed detector."""
        cwd = tmp_path / "gone"
        cwd.mkdir()
        db_path = tmp_path / "e2e.db"
        monkeypatch.chdir(cwd)
        cwd.rmdir()
        try:
            memory = PACTMemory(session_id="s", db_path=db_path)
            assert memory.project_id is None
            assert memory._project_id_source == "unresolved"
            with pytest.raises(ProjectIdentityUnresolvedError):
                memory.save({"context": "x"}, sync_to_claude=False)
        finally:
            os.chdir(tmp_path)

    def test_a_payload_supplied_project_still_saves(self, tmp_path, monkeypatch):
        """The guard must not over-block the documented remedy.

        A caller-supplied project_id is a warrant and produces a non-NULL row,
        so refusing it would block the obvious remedy in the very state the
        guard exists for.
        """
        db_path = tmp_path / "payload.db"
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None

        linked = tmp_path / "explicit.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory_id = memory.save(
            {"context": "EXPLICIT", "project_id": "explicitly-elsewhere"},
            files=[str(linked)],
            sync_to_claude=False,
        )

        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            # F1: the payload warrant must key the FILE row too. The save
            # links files with the INSTANCE identity, which is None here, so
            # asserting only the memories row left a NULL-keyed files row
            # unnoticed by both this arm and the suite.
            file_row = conn.execute(
                "SELECT project_id FROM files WHERE path LIKE ?",
                (f"%/{linked.name}",),
            ).fetchone()
            null_files = conn.execute(
                "SELECT COUNT(*) FROM files WHERE project_id IS NULL"
            ).fetchone()[0]
        assert row is not None and row[0] == "explicitly-elsewhere"
        assert file_row is not None, "the save did not link the file row"
        assert file_row[0] == "explicitly-elsewhere", (
            "the file row was keyed by the instance identity (None), not by "
            "the payload warrant"
        )
        assert null_files == 0, "a NULL-keyed file row survived the save"
        assert memory.last_project_scope["source"] == "supplied"

    def test_f2_an_empty_string_payload_project_is_rejected(
        self, tmp_path, monkeypatch
    ):
        """An empty id is invalid input, never an auto-detect request."""
        db_path = tmp_path / "empty-id.db"
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None

        with pytest.raises(ValueError) as excinfo:
            memory.save(
                {"context": "EMPTY-ID", "project_id": ""}, sync_to_claude=False
            )

        assert str(excinfo.value).startswith(
            "project_id, when supplied, must be a non-empty string without "
            "control characters (U+0000-U+001F, U+007F-U+009F) or lone "
            "surrogates (U+D800-U+DFFF); "
        )
        assert not db_path.exists(), (
            "the empty-payload refusal created the store; it must precede "
            "_ensure_ready()"
        )

    def test_f2_a_resolved_instance_rejects_an_empty_payload_id(self, tmp_path):
        """The validation also fires when the instance has a valid identity."""
        db_path = tmp_path / "resolved-empty-id.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        assert memory.project_id == "detected-b"

        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            memory.save(
                {"context": "RESOLVED-EMPTY-ID", "project_id": ""},
                files=[str(linked)],
                sync_to_claude=False,
            )

        assert str(excinfo.value).startswith(
            "project_id, when supplied, must be a non-empty string without "
            "control characters (U+0000-U+001F, U+007F-U+009F) or lone "
            "surrogates (U+D800-U+DFFF); "
        )
        assert not db_path.exists(), (
            "the empty-payload refusal created the store; it must precede "
            "_ensure_ready()"
        )

    def test_f2_a_NULL_payload_project_is_omission_not_an_empty_id(
        self, tmp_path
    ):
        """The boundary the falsy-payload guard must not cross.

        JSON null means "omit and auto-detect". The guard rejects `""` but
        must leave `None` alone, so a resolved instance fills the row from its
        own identity. Getting this wrong in either direction is a bug: a
        rejected None breaks every caller that explicitly passes null, and a
        defaulted `""` writes an unaddressable row.
        """
        db_path = tmp_path / "null-id.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        assert memory.project_id == "detected-b"

        memory_id = memory.save(
            {"context": "NULL-ID", "project_id": None}, sync_to_claude=False
        )

        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        assert row is not None and row[0] == "detected-b", (
            "a None payload project was not treated as omission; it should "
            "default to the instance's detected id, not be refused and not "
            "written empty"
        )

    def test_f2_an_integer_payload_project_is_rejected_by_type(self, tmp_path):
        """The type half of the payload guard (cold review).

        `not payload_project` alone accepts any TRUTHY value, so 123 passed
        the falsy guard and was stored, while the refusal message promised a
        non-empty STRING. Project ids are strings everywhere else in this
        codebase. This arm asserts the message and the channel, and that the
        refusal precedes store creation.
        """
        db_path = tmp_path / "int-id.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        assert memory.project_id == "detected-b"

        with pytest.raises(ValueError) as excinfo:
            memory.save(
                {"context": "INT-ID", "project_id": 123}, sync_to_claude=False
            )

        assert str(excinfo.value).startswith(
            "project_id, when supplied, must be a non-empty string without "
            "control characters (U+0000-U+001F, U+007F-U+009F) or lone "
            "surrogates (U+D800-U+DFFF); "
        ), f"refused for a different reason: {str(excinfo.value)!r}"
        assert not db_path.exists(), (
            "the integer-payload refusal created the store; the guard must "
            "precede _ensure_ready()"
        )

    @pytest.mark.parametrize(
        "project_id",
        [
            "bad\x00id",
            "bad\nid",
            "bad\tid",
            "bad\x1fid",
            "bad\x7fid",
            "bad\x85id",
            "bad\x9fid",
            "bad\ud800id",
        ],
    )
    def test_f2_control_characters_in_payload_project_are_rejected(
        self, tmp_path, project_id
    ):
        """C0, DEL, C1 controls and lone surrogates are invalid before any
        store work. The surrogate cell pins the clause that keeps an
        unencodable value (`json.loads` accepts unpaired \\ud800 escapes)
        off the utf-8 TEXT binding, where it would die as an unhandled
        UnicodeEncodeError instead of this ValueError."""
        db_path = tmp_path / "control-id.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")

        with pytest.raises(ValueError) as excinfo:
            memory.save(
                {"context": "CONTROL-ID", "project_id": project_id},
                files=[str(linked)],
                sync_to_claude=False,
            )

        assert "without control characters (U+0000-U+001F, U+007F-U+009F)" in str(
            excinfo.value
        )
        assert not db_path.exists(), (
            "an invalid payload created the store, so memory/file row absence "
            "was not established before _ensure_ready()"
        )

    @pytest.mark.parametrize(
        "session_id",
        ["", 123, "bad\x00id", "bad\x7fid", "bad\x9fid", "bad\ud800id"],
    )
    def test_f2_a_supplied_payload_session_id_must_be_usable(
        self, tmp_path, session_id
    ):
        """Save-side session_id, cycle 2: same rule, same verb.

        A session_id SOMEONE ACTUALLY SENT must be usable — empty, non-str,
        control-char and lone-surrogate values are refused before any store
        work, through the same `_unusable_identifier` rule the project_id
        guards and the update guard use. Null is NOT in this sweep: on save
        a null session_id means "default me", exactly as a null project_id
        means "auto-detect me" (pinned by the NULL-omission arm above).
        """
        db_path = tmp_path / "session-id.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)

        with pytest.raises(ValueError) as excinfo:
            memory.save(
                {"context": "SESSION-ID", "session_id": session_id},
                sync_to_claude=False,
            )

        assert str(excinfo.value).startswith(
            "session_id, when supplied, must be a non-empty string without "
        ), f"refused for a different reason: {str(excinfo.value)!r}"
        assert not db_path.exists(), (
            "an invalid payload session_id created the store; the guard must "
            "precede _ensure_ready()"
        )

    def test_f2_a_NULL_payload_session_id_is_omission_not_a_refusal(
        self, tmp_path
    ):
        """The save-side null boundary, pinned: null means "default me".

        The update guard rejects an explicit null session_id (there it is
        the hazard — scalar-replace to NULL); this arm holds save() to the
        OPPOSITE boundary so the two verbs cannot silently converge.
        """
        db_path = tmp_path / "null-session.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)

        memory_id = memory.save(
            {"context": "NULL-SESSION", "session_id": None}, sync_to_claude=False
        )

        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT session_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        assert row is not None and row[0] == "s", (
            "a None payload session_id was not treated as omission; it "
            "should default to the instance's session id, not be refused"
        )


class TestUpdatePayloadProjectIdGuard:
    """An update payload cannot de-key or re-key a row (#1688 close-out).

    `update_memory` scalar-replaces `project_id`, and the CLI passes caller
    JSON through verbatim (cmd_update), so `{"project_id": null}` could write
    a NULL key no reader can address. Mirrors the save() F2 family with the
    OPPOSITE null boundary: on update, explicit null IS the hazard and
    omission is the no-op, so the guard keys on presence, not truthiness.
    """

    @staticmethod
    def _row_project(db_path: Path, memory_id: str):
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return row[0] if row else None

    @pytest.mark.parametrize("field", ["project_id", "session_id"])
    @pytest.mark.parametrize(
        "bad",
        [
            None, "", 123,
            "bad\x00id", "bad\nid", "bad\tid", "bad\x1fid",
            "bad\x7fid", "bad\x85id", "bad\x9fid",
            "bad\ud800id",
        ],
    )
    def test_rejects_a_dekeying_or_mis_keying_payload(
        self, tmp_path, field, bad
    ):
        """Null (the de-key), empty, non-str, control-char and lone-surrogate
        values refuse, for BOTH scalar identifiers.

        The control sweep mirrors the save-side F2 family's seven values:
        the update guard's C1/DEL clause is pinned by cells that fail
        without it, not only by save()'s arms. The surrogate cell pins the
        clause that keeps an unencodable value off the utf-8 TEXT binding.
        session_id rides the same guard: `update_memory` scalar-replaces it
        and the CLI passes it through verbatim on the same path.
        """
        db_path = tmp_path / "update-guard.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "UPDATE-GUARD"}, sync_to_claude=False)

        with pytest.raises(ValueError) as excinfo:
            memory.update(memory_id, {field: bad})

        assert str(excinfo.value).startswith(
            f"{field}, when supplied to update, must be a non-empty string "
        ), f"refused for a different reason: {str(excinfo.value)!r}"
        assert self._row_project(db_path, memory_id) == "detected-b", (
            "a refused payload changed the row; the guard must precede the "
            "store write"
        )

    def test_omission_proceeds_with_no_identity_requirement(
        self, tmp_path, monkeypatch
    ):
        """An update not touching project_id needs no identity (subset guard).

        THE INSTANCE MUST HAVE NO IDENTITY, and the first version of this arm
        did not give it one: it built a RESOLVED instance, so it could not
        detect the regression its name describes — adding an identity
        requirement to update() left it green (measured). The row is therefore
        created by a resolved instance and the UPDATE is issued by an
        unresolved one.
        """
        db_path = tmp_path / "update-omission.db"
        seed = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = seed.save({"context": "UPDATE-OMISSION"}, sync_to_claude=False)

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None, (
            "the fixture resolved an identity, so the update below would not "
            "exercise the identity-free path"
        )

        resolved = memory.update(memory_id, {"context": "UPDATE-OMISSION-2"})

        assert resolved == memory_id
        assert self._row_project(db_path, memory_id) == "detected-b"

    def test_a_valid_string_rekeys_the_row(self, tmp_path):
        """An explicit non-empty string is a warranted rekey, still accepted."""
        db_path = tmp_path / "update-warrant.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "UPDATE-WARRANT"}, sync_to_claude=False)

        resolved = memory.update(memory_id, {"project_id": "warranted-c"})

        assert resolved == memory_id
        assert self._row_project(db_path, memory_id) == "warranted-c"

    def test_a_warranted_rekey_cascades_to_every_project_keyed_row(
        self, tmp_path
    ):
        """C-S1: the re-keyed row stays addressable from its new project.

        files rows are keyed (path, project_id) and vec_memories partitions
        by project_id, so a re-key that moved only the memories row would
        strand the file link and the vector in the old project: the row
        would vanish from search_by_file under the new key while remaining
        findable under the old one. The pre-rekey assertion proves the
        fixture live so the moved-state assertions cannot pass vacuously.
        """
        db_path = tmp_path / "cascade.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        old = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = old.save(
            {"context": "CASCADE"}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in old.search_by_file(str(linked))] == [memory_id], (
            "the live fixture did not link the file; the cascade assertions "
            "below would prove nothing"
        )

        assert old.update(memory_id, {"project_id": "warranted-c"}) == memory_id

        new = PACTMemory(project_id="warranted-c", session_id="s", db_path=db_path)
        old_view = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path
        )
        assert [m.id for m in new.search_by_file(str(linked))] == [memory_id], (
            "the re-keyed row is not addressable from its new project"
        )
        assert [m.id for m in old_view.search_by_file(str(linked))] == [], (
            "the re-keyed row is still addressable from its old project"
        )

        # The vec partition followed: no vector remains under the old key.
        # The assertion is UNCONDITIONAL. It used to vanish when sqlite_vec
        # would not load, on the reasoning that a process which cannot embed
        # never wrote a vector — false for a shared store, where another
        # process wrote one and this one merely cannot read it. A store with
        # a vec0 table that this process cannot open is an environment that
        # failed to measure the claim, so it fails loudly instead of
        # reporting a pass. The degraded case has its own arm, with a
        # faithful simulation, in
        # TestAVectorHitIsConfirmedAgainstTheStoredProject.
        with db_connection(db_path) as conn:
            has_vec = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='vec_memories'"
            ).fetchone() is not None
            assert has_vec, (
                "the fixture's store has no vector table, so the partition "
                "claim below was not measured"
            )
            import sqlite_vec

            sqlite_vec.load(conn)
            stale = conn.execute(
                "SELECT COUNT(*) FROM vec_memories "
                "WHERE memory_id = ? AND project_id = ?",
                (memory_id, "detected-b"),
            ).fetchone()[0]
            assert stale == 0, (
                "a vector remains in the old project's partition"
            )

    def test_a_rekeys_links_survive_an_embedding_fault(
        self, tmp_path, monkeypatch
    ):
        """Cycle 4 (Bugbot B1): the links commit BEFORE the embedding call.

        _store_embedding's fault path rolls the connection back. Link
        writes left in that transaction would be undone with it, leaving
        the row addressable only under its OLD key — the state the
        cascade exists to retire. save() commits its links before
        embedding for the same reason; this pins the same ordering on the
        re-key path.

        THE MIGRATION COMMIT IS NEUTRALIZED because it defends this
        ordering only incidentally: `get_memory` -> `ensure_initialized`
        -> `_migrate_ct_fields` ends in `conn.commit()`, which flushes the
        pending link writes as a side effect BEFORE the embedding call.
        Without this isolation the arm passes with OR without the
        cascade's own commit (measured), proving nothing. The status
        assert proves the fault stub ran, so the surviving-links assert
        cannot pass for the wrong reason.
        """
        db_path = tmp_path / "fault.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        old = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = old.save(
            {"context": "FAULT"}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in old.search_by_file(str(linked))] == [memory_id], (
            "the live fixture did not link the file; the fault arm below "
            "would prove nothing"
        )

        monkeypatch.setattr(
            scripts_database, "_migrate_ct_fields", lambda conn: None
        )

        def _rollback_fault(conn, memory_id, memory):
            conn.rollback()
            return "fault"

        monkeypatch.setattr(
            type(old), "_store_embedding", staticmethod(_rollback_fault)
        )
        assert old.update(memory_id, {"project_id": "warranted-c"}) == memory_id
        assert old.last_embedding_status == "fault", (
            "the fault stub never ran; the surviving-links assertion was "
            "not exercising the seam"
        )

        new = PACTMemory(project_id="warranted-c", session_id="s", db_path=db_path)
        assert [m.id for m in new.search_by_file(str(linked))] == [memory_id], (
            "the re-pointed links were rolled back with the embedding fault"
        )

    def test_a_faulted_content_reembed_does_not_skip_the_cascade_reembed(
        self, tmp_path, monkeypatch
    ):
        """Cycle 5 (Bugbot B2): vector_current means the vector IS current.

        The content path's re-embed can FAULT — its rollback restores the
        original vector and nothing is written — and that must not read as
        'already stored under the new key' to the cascade. A counting
        fault stub pins the skip condition from both sides: a combined
        content+rekey update stores TWICE (content path, then cascade,
        because the fault left the vector not-current) and a content-only
        update stores ONCE (no re-key, no cascade) — so the cascade
        re-store fires on fault and only on fault-supplemented skips.
        """
        calls: list = []

        def _counting_fault(conn, memory_id, memory):
            calls.append(memory_id)
            conn.rollback()
            return "fault"

        monkeypatch.setattr(
            PACTMemory, "_store_embedding", staticmethod(_counting_fault)
        )

        # Combined: content fields AND a warranted re-key in one update.
        db_path = tmp_path / "skip.db"
        combined = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path
        )
        memory_id = combined.save(
            {"context": "SKIP-SEED"}, sync_to_claude=False
        )
        calls.clear()
        assert combined.update(
            memory_id, {"context": "SKIP-CHANGED", "project_id": "warranted-c"}
        ) == memory_id
        assert len(calls) == 2, (
            f"a faulted content re-embed must not skip the cascade "
            f"re-embed; store calls: {len(calls)}"
        )
        assert combined.last_embedding_status == "fault"

        # Content-only: the same fault, but no re-key means no cascade and
        # exactly the one content-path call.
        db_path2 = tmp_path / "skip2.db"
        content_only = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path2
        )
        memory_id2 = content_only.save(
            {"context": "SKIP2-SEED"}, sync_to_claude=False
        )
        calls.clear()
        assert content_only.update(
            memory_id2, {"context": "SKIP2-CHANGED"}
        ) == memory_id2
        assert len(calls) == 1, (
            f"no re-key means no cascade re-embed; store calls: {len(calls)}"
        )
        assert content_only.last_embedding_status == "fault"

    def test_a_disagreement_refusal_precedes_the_payload_guard(
        self, tmp_path, monkeypatch
    ):
        """F3: ordering pin. The disagreement guard runs first, so a session
        that disagrees refuses on the disagreement whatever the payload
        carries. Flipping the order would change the CLI envelope channel
        (SCOPE_DISAGREEMENT vs ValueError exit 2) with no other symptom."""
        db_path = tmp_path / "ordering.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ORDERING"}, sync_to_claude=False)

        monkeypatch.setattr(
            memory_api,
            "env_record_project_dir_disagreement",
            lambda: ("/env/project", "/record/project"),
        )
        with pytest.raises(ProjectScopeDisagreementError):
            memory.update(memory_id, {"project_id": None})

    def test_a_refusal_precedes_ensure_ready(self, tmp_path, monkeypatch):
        """F2: a refused payload does no store work.

        `_ensure_ready` may install dependencies, migrate the store and run
        the embedding catch-up before the refusal, so its position is a
        contract the CHANGELOG states and nothing pinned. Both refusals are
        checked: the mapping guard's, and the identity loop's.
        """
        db_path = tmp_path / "ensure-ready.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ENSURE-READY"}, sync_to_claude=False)

        called = []
        monkeypatch.setattr(memory_api, "_ensure_ready", lambda: called.append(True))

        with pytest.raises(ValueError, match="must be an object mapping"):
            memory.update(memory_id, ["not", "a", "mapping"])
        with pytest.raises(ValueError, match="when supplied to update"):
            memory.update(memory_id, {"project_id": None})

        assert called == [], (
            "a refused payload reached _ensure_ready, which may install, "
            "migrate and catch up before the refusal lands"
        )

    def test_a_payload_refusal_precedes_the_prefix_check(
        self, tmp_path
    ):
        """F3, the other seam: payload validation precedes id resolution.

        The control half proves a short prefix alone takes the
        PrefixTooShortError path, so the payload half — same short prefix,
        bad payload — discriminating ValueError is the guard firing FIRST,
        not the prefix check being unreachable.
        """
        db_path = tmp_path / "prefix-ordering.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory.save({"context": "PREFIX-ORDERING"}, sync_to_claude=False)

        with pytest.raises(PrefixTooShortError):
            memory.update("ab", {"context": "control half"})

        with pytest.raises(ValueError) as excinfo:
            memory.update("ab", {"project_id": None})
        assert str(excinfo.value).startswith(
            "project_id, when supplied to update, must be a non-empty string "
        )


class TestUnresolvedIdentityScopesReads:
    """R1: None means NO records, never every project's records."""

    def _seed(self, db_path: Path):
        memory = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        linked = db_path.parent / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        target = db_path.parent / "target.py"
        target.write_text("y = 2\n", encoding="utf-8")
        memory_id = memory.save(
            {"context": _TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        with db_connection(db_path) as conn:
            add_file_relation(
                conn, str(linked), str(target), "imports", project_id="other-proj-a"
            )
        return memory_id, linked

    def _unresolved(self, monkeypatch, db_path: Path) -> PACTMemory:
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_id_with_source",
            staticmethod(lambda: (None, "unresolved")),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None
        return memory

    def test_r1_control_a_scoped_instance_finds_the_seeded_rows(self, tmp_path):
        """The positive control the other R1 arms rest on.

        Without it an arm can pass because the fixture has nothing findable —
        the same vacuous-pass class as I2.
        """
        db_path = tmp_path / "store.db"
        memory_id, linked = self._seed(db_path)

        scoped = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        assert [m.id for m in scoped.list()] == [memory_id]
        assert [m.id for m in scoped.search(_TOKEN, sync_to_claude=False)] == [memory_id]
        assert [m.id for m in scoped.search_by_file(str(linked))] == [memory_id]
        status = scoped.get_status()
        assert status["memory_count"] == 1
        assert all(
            status["graph_stats"][key] > 0
            for key in ("files", "memories", "memory_file_links", "file_relations")
        ), status["graph_stats"]

    def test_r1_an_unresolved_identity_returns_no_records_from_any_surface(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "store.db"
        self._seed(db_path)
        empty = self._unresolved(monkeypatch, db_path)

        assert empty.list() == []
        assert empty.search(_TOKEN, sync_to_claude=False) == []
        assert empty.search_by_file(str(db_path.parent / "linked.py")) == []

        status = empty.get_status()
        assert status["project_id"] is None
        assert status["memory_count"] == 0
        assert status["graph_stats"] == {
            "files": 0,
            "memories": 0,
            "memory_file_links": 0,
            "file_relations": 0,
        }, status["graph_stats"]

        scoped = PACTMemory(project_id="other-proj-a", session_id="s", db_path=db_path)
        assert set(status) == set(scoped.get_status()), (
            "the zeroed envelope hides a key the populated one carries"
        )

        # The store really holds another project's rows, so [] above is the
        # guard's doing rather than an empty store.
        with db_connection(db_path) as conn:
            assert list_memories(conn, project_id=None)
            assert search_memories_by_text(conn, _TOKEN, project_id=None)

    def test_r1c_the_file_search_would_surface_a_NULL_keyed_row_unguarded(
        self, tmp_path, monkeypatch
    ):
        """`search_by_file` has a different causal shape: NULL, not all."""
        db_path = tmp_path / "store.db"
        linked = db_path.parent / "linked.py"
        self._seed(db_path)
        with db_connection(db_path) as conn:
            file_id = get_file_id(conn, str(linked), "other-proj-a")
            assert file_id
            conn.execute("UPDATE files SET project_id = NULL WHERE id = ?", (file_id,))
            conn.commit()
        with db_connection(db_path) as conn:
            assert get_file_id(conn, str(linked), None) == file_id, (
                "the fixture does not exercise the NULL-keyed shape"
            )

        empty = self._unresolved(monkeypatch, db_path)
        assert empty.search_by_file(str(linked)) == []

    def test_unresolved_search_clears_the_previous_sync_status(
        self, tmp_path, monkeypatch
    ):
        """The identity early return must not inherit a save's `wrote`."""
        empty = self._unresolved(monkeypatch, tmp_path / "store.db")
        with patch(
            "scripts.memory_api.sync_to_claude_md",
            return_value=SyncResult(SyncResult.WROTE),
        ):
            empty.save({"context": "PREVIOUS", "project_id": "chosen"})
        assert empty.last_sync_status == SyncResult.WROTE

        assert empty.search("NO-RECORDS") == []
        assert empty.last_sync_status is None

    def test_empty_result_search_clears_the_previous_sync_status(self, tmp_path):
        """An ordinary no-result search also attempts no sync."""
        memory = PACTMemory(
            project_id="detected-b", session_id="s", db_path=tmp_path / "store.db"
        )
        with patch(
            "scripts.memory_api.sync_to_claude_md",
            return_value=SyncResult(SyncResult.WROTE),
        ):
            memory.save({"context": "PREVIOUS"})
        assert memory.last_sync_status == SyncResult.WROTE

        with patch("scripts.memory_api.graph_enhanced_search", return_value=[]):
            assert memory.search("NO-MATCH") == []
        assert memory.last_sync_status is None


class TestIssueCriterionALiteralShape:
    """Issue #1688 criterion (a) in the shape it actually names.

    Every other arm reaches the store through CLAUDE_PROJECT_DIR, or reaches
    the session-record rung only up to resolution. This one uses the SESSION
    RECORD as the declaration, leaves the env var unset, and syncs AMBIENTLY —
    the production shape that rendered the Working Memory block empty.
    """

    def test_criterion_a_session_record_declaration_end_to_end(
        self, layout_root, monkeypatch
    ):
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _enable_record_reading(monkeypatch)
        # The DECLARATION under test is the session context record, written
        # while the worktree is live. The identity record is produced by the
        # REAL writer, so the writer and the reader are exercised together.
        write_session_context(Path.home() / ".claude", SID, worktree)
        project_scope._record_worktree_identity(SID, str(worktree))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(main)

        # Positive control: both records were READ, and the declaration the
        # detector will use is the live worktree.
        assert pact_session.get_project_dir_from_session_record() == str(worktree)
        identity = pact_session.get_worktree_identity_from_session_record()
        assert identity.get("declared") == os.path.realpath(worktree)

        db_path = layout_root / "store.db"
        live = PACTMemory(session_id="s", db_path=db_path)
        assert live.project_id == "R", (
            "the session-record declaration did not resolve to the repository"
        )
        memory_id = live.save({"context": _TOKEN}, sync_to_claude=False)
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        assert row is not None and row[0] == "R"

        _remove_worktree(main, worktree)

        after = PACTMemory(session_id="s", db_path=db_path)
        assert after.project_id == "R"
        assert after._project_id_source == "worktree identity record"

        # AMBIENT: no claude_md_root warrant. This is the call whose empty
        # projection was the issue's symptom.
        written = after.sync()
        assert after.last_sync_status == SyncResult.WROTE, (
            "the ambient sync did not write; status was "
            f"{after.last_sync_status!r}. The scope guard refusing here is a "
            "finding, not a reason to fall back to the warrant."
        )
        assert written == [memory_id], (
            "the ambient sync did not select the row saved while the "
            "worktree was live"
        )
        assert _TOKEN in (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")

    def test_the_session_record_decline_is_loud(
        self, layout_root, monkeypatch, caplog
    ):
        """Criterion (b)'s session-record variant: the warning names the path.

        The value assertion lives in test_project_dir_resolution.py; this arm
        captures the WARNING for the SESSION-RECORD declaration, which that
        sweep asserts only by value.
        """
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _enable_record_reading(monkeypatch)
        write_session_context(Path.home() / ".claude", SID, worktree)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        _remove_worktree(main, worktree)
        # Strategy 2 runs from the process cwd even on the decline arm.
        monkeypatch.chdir(layout_root)

        with caplog.at_level(logging.WARNING):
            assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")

        messages = " ".join(r.message for r in caplog.records)
        assert str(worktree) in messages, (
            "the session-record decline did not name the path it could not "
            f"verify; captured: {messages!r}"
        )
        assert "could not be resolved" in messages

    def test_the_session_record_decline_reads_empty_not_whole_store(
        self, layout_root, monkeypatch
    ):
        """LIVE AND EMPTY, PROVEN: the row exists, the declined read finds none.

        The loudness half of the decline is pinned above; the empty-read half
        is pinned in TestUnresolvedIdentityScopesReads, but that arm reaches
        the unresolved identity through a patched detector. This one reaches
        it through the production seam the issue names: the session record
        declares a worktree that has been removed, no identity record answers
        (the writer is deliberately NOT invoked), and the store provably holds
        the row saved while the worktree was live. A read returning any row
        here would be reading across the store on a None identity; an empty
        store would pass vacuously, which is why the row is proven live first.
        Every read surface is asserted (C-N2): list, search, search_by_file
        and get_status's project-scoped counters all decline together.
        """
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _enable_record_reading(monkeypatch)
        write_session_context(Path.home() / ".claude", SID, worktree)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(main)

        db_path = layout_root / "decline-store.db"
        live = PACTMemory(session_id="s", db_path=db_path)
        assert live.project_id == "R", (
            "the live session-record declaration did not resolve to the repo"
        )
        linked = main / "README"
        memory_id = live.save(
            {"context": _TOKEN}, files=[str(linked)], sync_to_claude=False
        )

        # LIVE: the row is in the store under the repository's key.
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        assert row is not None and row[0] == "R"

        # THE FILE ROW IS MADE NULL-KEYED, WHICH IS THE SHAPE THE HAZARD NEEDS.
        # A row keyed "R" matches nothing under a None filter, so asserting []
        # against it would be vacuous. R1c builds the same shape; the control
        # below proves the unguarded read WOULD surface this memory.
        with db_connection(db_path) as conn:
            file_id = get_file_id(conn, str(linked), "R")
            assert file_id, "the live save did not link the file row"
            conn.execute(
                "UPDATE files SET project_id = NULL WHERE id = ?", (file_id,)
            )
            conn.commit()
        with db_connection(db_path) as conn:
            assert get_file_id(conn, str(linked), None) == file_id, (
                "the fixture does not exercise the NULL-keyed shape"
            )

        # No identity record exists for this session: after removal, nothing
        # can answer for the dead declaration.
        assert not _record_path(worktree).exists()

        _remove_worktree(main, worktree)
        monkeypatch.chdir(layout_root)

        gone = PACTMemory(session_id="s", db_path=db_path)
        assert gone.project_id is None
        assert gone.list(limit=50) == [], (
            "a declined identity read rows from the store instead of none"
        )
        # C-N2: the SAME decline holds on every read surface, not list alone.
        # search would drop its project filter on None (whole-store read),
        # search_by_file would match the shared NULL bucket, and get_status
        # would report whole-store counts as this session's.
        assert gone.search(_TOKEN, sync_to_claude=False) == []
        assert gone.search_by_file(str(linked)) == [], (
            "the declined identity surfaced the memory through the shared "
            "NULL-keyed file row"
        )
        status = gone.get_status()
        assert status["project_id"] is None
        assert status["memory_count"] == 0
        assert status["graph_stats"] == {
            "files": 0,
            "memories": 0,
            "memory_file_links": 0,
            "file_relations": 0,
        }, status["graph_stats"]

    def test_a_corrupt_identity_record_after_removal_declines(
        self, layout_root, monkeypatch, caplog
    ):
        """The corrupt branch, exercised AFTER removal.

        The reader returns {} for a corrupt record exactly as it does for an
        absent one, so this arm first proves the record PATH is the reader's
        (a valid record is read back), then corrupts it. Without the first
        step an unreadable file at the wrong path would pass identically.
        """
        main = _repo(layout_root / "R", document=True)
        worktree = _worktree(main, main / ".worktrees" / "W")
        _plant(worktree, _identity_for(worktree))
        _enable_record_reading(monkeypatch)
        assert pact_session.get_worktree_identity_from_session_record(), (
            "the valid record was not read; the corrupt step would prove nothing"
        )

        record_path = _record_path(worktree)
        record_path.write_text("{not json", encoding="utf-8")
        assert pact_session.get_worktree_identity_from_session_record() == {}, (
            "the corrupt record was not rejected at the reader"
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(layout_root)
        _remove_worktree(main, worktree)

        with caplog.at_level(logging.WARNING):
            assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")
        messages = " ".join(r.message for r in caplog.records)
        assert str(worktree) in messages


# ---------------------------------------------------------------------------
# The shared record predicate is TOTAL (NUL class)
# ---------------------------------------------------------------------------

def test_a_nul_bearing_declaration_refuses_rather_than_raising(tmp_path):
    """A declaration that cannot be canonicalised is REFUSED, not fatal.

    A corrupt session record can carry an embedded NUL, which makes
    `os.path.realpath` and `Path.resolve` raise ValueError. The
    working-memory sync guard passes such a declaration to
    `stays_in_declared_project` (working_memory.py:1944-1954), so before this
    guard the sync guard CRASHED instead of refusing. Fail-safe here is
    False: a path that cannot be resolved cannot be shown to stay in the
    declared project.
    """
    declared = Path("/tmp/gone" + "\x00" + "tail")
    assert (
        project_scope.stays_in_declared_project(
            declared, tmp_path, tmp_path / "CLAUDE.md"
        )
        is False
    ), "a NUL-bearing declaration must refuse, never raise"


def test_a_nul_bearing_declaration_does_not_name_a_session_record():
    """The shared predicate is TOTAL: malformed declaration -> False.

    `_record_names_declaration` is the origin `recorded_main_root`,
    `record_resolution` and `session_memory_root` all reach; a raise here
    escaped the resolver and the root classifier before the guard.
    """
    identity = {
        "declared": "/tmp/gone",
        "common_dir": "/tmp/x/.git",
        "worktree": "/tmp/gone",
    }
    assert (
        project_scope._record_names_declaration(
            identity, Path("/tmp/gone" + "\x00" + "tail")
        )
        is False
    ), "a malformed declaration must not match a recorded one"


def test_declaration_predicate_accepts_an_existing_file(tmp_path):
    """`exists()`, not `is_dir()`: a live non-directory stays on the live arm."""
    declared = tmp_path / "declaration"
    declared.write_text("not a directory\n", encoding="utf-8")
    assert memory_api._declared_path_exists(str(declared)) is True


def test_declaration_predicate_declines_a_broken_symlink(tmp_path):
    """A symlink whose target is gone does not count as a live declaration."""
    declared = tmp_path / "declaration"
    declared.symlink_to(tmp_path / "missing-target")
    assert memory_api._declared_path_exists(str(declared)) is False


# ---------------------------------------------------------------------------
# #1688 remediation: a re-key's cross-project reads are closed at every surface
#
# Every arm below shares one shape: a memory IS stored under one project while
# a project-keyed surface still carries another, and the assertion is that the
# read the other project issues does not hand the memory back. Each has a
# matched control on the SAME fixture, because "returns []" is also what a
# missing row returns.
# ---------------------------------------------------------------------------

_REKEY_TOKEN = "REKEY-CROSS-PROJECT-TOKEN"


def _vector_partition(db_path: Path, memory_id: str):
    """The project whose vec0 partition holds this memory, or None."""
    with db_connection(db_path) as conn:
        ensure_initialized(conn)
        row = conn.execute(
            "SELECT project_id FROM vec_memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    return row[0] if row else None


def _saved_with_vector(db_path: Path, project: str) -> str:
    """Save one memory under `project` and prove it stored a real vector.

    The precondition is asserted rather than assumed: an environment without
    an embedding backend would leave every arm below measuring fixture state.
    """
    memory_id = PACTMemory(
        project_id=project, session_id="s", db_path=db_path
    ).save({"context": _REKEY_TOKEN}, sync_to_claude=False)
    partition = _vector_partition(db_path, memory_id)
    assert partition == project, (
        f"the fixture stored no vector under {project!r} (found {partition!r}); "
        "this arm would measure the fixture, not the guard"
    )
    return memory_id


def _degrade(monkeypatch) -> None:
    """Make this process unable to open the vector table, faithfully.

    THE FLAG ALONE IS NOT THE CONDITION. `_drop_existing_vector` imports
    sqlite_vec and loads it without consulting SQLITE_EXTENSIONS_ENABLED, so a
    fixture that patches only the flag still reaches the table and deletes the
    row — three arms measured green for that reason. A process that cannot
    embed cannot load the extension either, so both terms are broken here.
    """
    import sqlite_vec

    def _cannot_load(conn):
        raise RuntimeError("simulated: sqlite-vec cannot load in this process")

    monkeypatch.setattr(sqlite_vec, "load", _cannot_load)
    for module in (memory_api, memory_search, embedding_catchup):
        monkeypatch.setattr(module, "SQLITE_EXTENSIONS_ENABLED", False)


class TestSearchByFileConfirmsTheStoredProject:
    def test_a_stale_link_does_not_serve_another_projects_memory(self, tmp_path):
        """The graph lookup scopes the FILE; the row's own scope decides."""
        db_path = tmp_path / "stale-link.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory_id = PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).save(
            {"context": _REKEY_TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            for project in ("projA", None):
                file_id = track_file(conn, str(linked), project)
                conn.execute(
                    "INSERT OR IGNORE INTO memory_files "
                    "(memory_id, file_id, relationship) VALUES (?, ?, 'modified')",
                    (memory_id, file_id),
                )
            conn.commit()

        stale = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        assert stale.search_by_file(str(linked)) == [], (
            "a stale link on projA's file row served a projB memory to projA"
        )
        # CONTROLS on the same fixture: the memory IS reachable when the
        # filter matches and when no filter is applied, so the [] above is the
        # confirmation's doing rather than a missing row.
        live = PACTMemory(project_id="projB", session_id="s", db_path=db_path)
        assert [m.id for m in live.search_by_file(str(linked))] == [memory_id]
        with store_scope(db_path):
            assert [
                m.id for m in memory_search.search_by_file(str(linked))
            ] == [memory_id], "the unscoped read lost its whole-store meaning"


class TestAVectorHitIsConfirmedAgainstTheStoredProject:
    def test_a_degraded_rekey_leaves_the_row_and_every_read_confirms_it(
        self, tmp_path, monkeypatch
    ):
        """One fixture, three claims: the choke point, the join, the repair."""
        db_path = tmp_path / "degraded.db"
        memory_id = _saved_with_vector(db_path, "projA")

        with monkeypatch.context() as degraded:
            _degrade(degraded)
            assert PACTMemory(
                project_id="projB", session_id="s", db_path=db_path
            ).update(memory_id, {"project_id": "projB"}) == memory_id

        # THE SIMULATION WAS FAITHFUL, and this is the measurement that says
        # so: a process that could still load the extension would have deleted
        # the old row on the cascade's fault path. Patching the flag alone
        # leaves it reachable, which is why that fixture proves nothing.
        assert _vector_partition(db_path, memory_id) == "projA", (
            "the degraded re-key dropped or moved the vector row, so this "
            "fixture did not exercise the no-vector-store path"
        )

        with store_scope(db_path):
            with db_connection(db_path) as conn:
                ensure_initialized(conn)
                assert memory_search.vector_search(
                    conn, _REKEY_TOKEN, "projA"
                ) == [], (
                    "a stranded vector hit was served to the project the "
                    "memory was re-keyed out of"
                )
                assert [
                    hit for hit, _distance in memory_search.vector_search(
                        conn, _REKEY_TOKEN, None
                    )
                ] == [memory_id], "the unscoped read lost its whole-store meaning"
            unembedded = embedding_catchup.get_unembedded_memories()
            assert unembedded.answerable, unembedded.reason
            assert list(unembedded) == [memory_id], (
                "the partition-aware join did not report the stranded row"
            )
            assert embedding_catchup.embed_single_memory(memory_id) is True
        assert _vector_partition(db_path, memory_id) == "projB", (
            "the repair did not move the vector into the memory's partition"
        )


class TestAFaultedCascadeDropsTheReachableOldRow:
    def test_a_faulted_cascade_leaves_a_missing_row_the_catchup_repairs(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "fault.db"
        memory_id = _saved_with_vector(db_path, "projA")

        def _fault(conn, mid, memory):
            # The real fault path rolls its own replace back, so the original
            # row is restored; this stub leaves the same end state.
            conn.rollback()
            return "fault"

        monkeypatch.setattr(PACTMemory, "_store_embedding", staticmethod(_fault))
        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        assert _vector_partition(db_path, memory_id) is None, (
            "a faulted cascade stranded the reachable old-partition row, "
            "which the catch-up cannot see as unembedded"
        )
        with store_scope(db_path):
            unembedded = embedding_catchup.get_unembedded_memories()
            assert unembedded.answerable, unembedded.reason
            assert list(unembedded) == [memory_id], (
                "the catch-up did not report a memory whose vector was dropped"
            )


class TestAnInterruptedCascadeIsFinishedByReissuing:
    def test_the_reissued_update_repairs_and_a_satisfied_one_stores_nothing(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "interrupted.db"
        first = tmp_path / "first.py"
        second = tmp_path / "second.py"
        first.write_text("x = 1\n", encoding="utf-8")
        second.write_text("y = 2\n", encoding="utf-8")
        memory_id = PACTMemory(
            project_id="projA", session_id="s", db_path=db_path
        ).save(
            {"context": _REKEY_TOKEN},
            files=[str(first), str(second)],
            sync_to_claude=False,
        )
        assert _vector_partition(db_path, memory_id) == "projA"

        real_track = memory_api.track_file
        seen = {"n": 0}

        def _raise_on_second(conn, path, project_id=None):
            seen["n"] += 1
            if seen["n"] == 2:
                raise RuntimeError("simulated interruption")
            return real_track(conn, path, project_id)

        monkeypatch.setattr(memory_api, "track_file", _raise_on_second)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            PACTMemory(
                project_id="projB", session_id="s", db_path=db_path
            ).update(memory_id, {"project_id": "projB"})
        monkeypatch.setattr(memory_api, "track_file", real_track)

        # The memories row moved and was committed before the cascade ran, so
        # the re-issued update sees an already-re-keyed row.
        with db_connection(db_path) as conn:
            assert get_memory(conn, memory_id)["project_id"] == "projB"

        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        with db_connection(db_path) as conn:
            stale = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS NOT ?",
                (memory_id, "projB"),
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS ?",
                (memory_id, "projB"),
            ).fetchone()[0]
        assert (stale, fresh) == (0, 2), (
            f"the re-issued update did not finish the cascade: {stale} old, "
            f"{fresh} new"
        )
        assert _vector_partition(db_path, memory_id) == "projB"

        # THIRD UPDATE: the invariant is satisfied. Zero store calls is the
        # claim, but a zero is also what an update that never reached the
        # cascade produces, so the probes that decide it are spied too.
        probes = []
        real_files = PACTMemory._linked_file_partition_is_stale
        real_vector = PACTMemory._vector_partition_is_stale
        monkeypatch.setattr(
            PACTMemory, "_linked_file_partition_is_stale",
            staticmethod(
                lambda conn, mid, target: (
                    probes.append("files"), real_files(conn, mid, target)
                )[1]
            ),
        )
        monkeypatch.setattr(
            PACTMemory, "_vector_partition_is_stale",
            staticmethod(
                lambda conn, mid, target: (
                    probes.append("vector"), real_vector(conn, mid, target)
                )[1]
            ),
        )
        stores = []
        monkeypatch.setattr(
            PACTMemory, "_store_embedding",
            staticmethod(lambda conn, mid, memory: stores.append(mid) or None),
        )
        # THE CASCADE ITSELF IS COUNTED, not only the store it calls. A
        # cascade entered and doing nothing produces zero store calls too, so
        # the zero below means "not entered", which is the claim.
        cascades = []
        real_cascade = PACTMemory._cascade_rekey

        def _spy_cascade(self, conn, mid, target, *, vector_current):
            cascades.append((mid, target, vector_current))
            return real_cascade(
                self, conn, mid, target, vector_current=vector_current
            )

        monkeypatch.setattr(PACTMemory, "_cascade_rekey", _spy_cascade)

        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id
        assert cascades == [], "a satisfied invariant still entered the cascade"
        assert stores == [], "a satisfied invariant still re-stored the vector"
        assert probes == ["files", "vector"], (
            "the third update never evaluated the invariant, so its zero "
            f"store calls prove nothing; probes={probes!r}"
        )


class TestConstructorValidatesSuppliedIdentifiers:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"project_id": "bad\x00id"},
            {"project_id": "bad\ud800id"},
            {"session_id": "bad\x7fid"},
            {"session_id": 123},
        ],
    )
    def test_a_supplied_unusable_identifier_is_refused(self, tmp_path, kwargs):
        db_path = tmp_path / "constructor.db"
        with pytest.raises(ValueError, match="when supplied"):
            PACTMemory(db_path=db_path, **kwargs)
        assert not db_path.exists(), (
            "the constructor refusal performed store work"
        )

    def test_a_falsy_supplied_identifier_still_auto_detects(
        self, tmp_path, monkeypatch
    ):
        """The guard sits INSIDE the truthy branch, and this is its boundary."""
        monkeypatch.setattr(
            PACTMemory, "_detect_project_id_with_source",
            staticmethod(lambda: ("detected-proj", "cwd")),
        )
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id",
            staticmethod(lambda: "detected-session"),
        )
        memory = PACTMemory(project_id="", session_id="", db_path=tmp_path / "x.db")
        assert memory.project_id == "detected-proj"
        assert memory._project_id_source == "cwd"
        assert memory.session_id == "detected-session"

    def test_a_detected_unusable_session_id_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id", staticmethod(lambda: "bad\nid")
        )
        with pytest.raises(ValueError, match="detected from the session context"):
            PACTMemory(project_id="ok-proj", db_path=tmp_path / "x.db")


class TestANonMappingUpdatePayload:
    @pytest.mark.parametrize("payload", [["x"], "x", 5, None, b"x"])
    def test_is_refused_as_invalid_input(self, tmp_path, payload):
        db_path = tmp_path / "non-mapping.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "NON-MAPPING"}, sync_to_claude=False
        )
        with pytest.raises(ValueError, match="must be an object mapping"):
            memory.update(memory_id, payload)
        assert memory.get(memory_id) is not None, (
            "the refusal destroyed or removed the row"
        )


class TestTheSuccessPathDoesNotRestoreTheVector:
    def test_a_content_change_and_a_rekey_store_the_vector_once(
        self, tmp_path, monkeypatch
    ):
        """F4: the skip branch `vector_current` exists for is a real no-op.

        The fixture has NO vector row: a missing row is deliberately not
        stale, so the content path's successful store is what makes the
        cascade skip. With a real row left under the old project the
        partition would legitimately be stale and the cascade must re-store.
        """
        db_path = tmp_path / "once.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ONCE-SEED"}, sync_to_claude=False)
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            conn.execute(
                "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
            )
            conn.commit()

        stores = []
        monkeypatch.setattr(
            PACTMemory, "_store_embedding",
            staticmethod(lambda conn, mid, data: stores.append(mid) or None),
        )
        assert memory.update(
            memory_id, {"context": "ONCE-CHANGED", "project_id": "projB"}
        ) == memory_id
        assert len(stores) == 1, (
            "the cascade re-stored a vector the content path already stored "
            f"under the new key; stores={stores!r}"
        )
