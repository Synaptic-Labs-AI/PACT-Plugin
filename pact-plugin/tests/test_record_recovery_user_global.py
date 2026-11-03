"""
Location: pact-plugin/tests/test_record_recovery_user_global.py
Summary: Pins that a session-record recovery never hands back a root whose
         canonical document is the user-global CLAUDE.md. Layout: $HOME is a
         git checkout, the config root is $HOME/.claude holding the global
         file, a linked worktree at $HOME/.worktrees/wt is recorded for the
         session and then removed, and the session's project dir is the
         removed path. The record verifies and its checkout ($HOME) contains
         the anchor, so without the check the recovery returns $HOME and
         session start writes the global file.
         The containment guard (`stays_in_declared_project`) must agree: its
         record branch and its worktree-listing admit never let a resolution
         into that root through.
Used by: pytest. Exercises hooks/shared/project_scope.py
         (`session_memory_root`, `stays_in_declared_project`,
         `_record_worktree_identity`) in process, and hooks/session_init.py as
         a SessionStart subprocess.

Every home is tmp_path/"home" (the suite's Path.home()); git runs with no
global or system config, and no checkout here has a remote.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import project_scope
from shared.project_scope import (
    RECORD_CONTAINED,
    ROOT_CAUSE_MISSING,
    ROOT_CAUSE_USER_GLOBAL,
    WORKTREE_IDENTITY_FILE,
    memory_root_of,
    record_resolution,
    session_memory_root,
    stays_in_declared_project,
    unknown_root_reason,
)

LEAD = "PACT:pact-orchestrator"
SESSION_ID = "abababab-1111-4222-8333-444444444444"
_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session_init.py"


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    )


class _RemovedWorktree:
    """A recorded, then removed, worktree of the checkout at `main`."""

    def __init__(self, tmp_path: Path, monkeypatch, main: Path):
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                    "GIT_CEILING_DIRECTORIES"):
            monkeypatch.delenv(key, raising=False)
        self.home = Path.home()
        self.config = self.home / ".claude"
        self.config.mkdir(parents=True)
        self.global_md = self.config / "CLAUDE.md"
        self.global_md.write_text("# user-global\n", encoding="utf-8")
        self.before = self.global_md.read_bytes()
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.config))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION_ID)

        self.main = main
        main.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", ".", cwd=main)
        _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=main)
        self.worktree = main / ".worktrees" / "wt"
        _git("worktree", "add", "-q", str(self.worktree), "-b", "wt", cwd=main)
        project_scope._record_worktree_identity(SESSION_ID, str(self.worktree))
        records = list(self.config.glob(f"pact-sessions/*/{SESSION_ID}/{WORKTREE_IDENTITY_FILE}"))
        assert len(records) == 1, "FIXTURE INVALID: no worktree record was written"
        _git("worktree", "remove", "--force", str(self.worktree), cwd=main)
        assert not self.worktree.exists()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.worktree))
        monkeypatch.chdir(main)
        monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
        reader = project_scope.get_worktree_identity_from_session_record

        def read_the_record():
            # The reader refuses inside pytest; lift that for this one call.
            saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                return reader()
            finally:
                if saved is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = saved

        monkeypatch.setattr(
            project_scope, "get_worktree_identity_from_session_record", read_the_record
        )
        assert read_the_record(), "FIXTURE INVALID: the record does not read back"

    @property
    def reason(self) -> str:
        return unknown_root_reason(ROOT_CAUSE_USER_GLOBAL, self.worktree)

    def assert_global_untouched(self):
        assert self.global_md.read_bytes() == self.before, "the user-global CLAUDE.md changed"


@pytest.fixture
def home_worktree(tmp_path, monkeypatch):
    return _RemovedWorktree(tmp_path, monkeypatch, Path.home())


def test_a_recovery_landing_on_a_dotfiles_home_is_user_global(home_worktree):
    """The classifier alone says MISSING and the record recovers $HOME, so the
    refusal comes from the recovery check and not from an earlier one."""
    assert memory_root_of(home_worktree.worktree) == (None, ROOT_CAUSE_MISSING)
    identity = project_scope.get_worktree_identity_from_session_record()
    assert record_resolution(home_worktree.worktree, identity) == (
        RECORD_CONTAINED, home_worktree.main.resolve()
    ), "FIXTURE INVALID: the recovery path is not reached"

    session = session_memory_root()

    assert (session.root, session.cause) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_session_start_after_the_recovery_leaves_the_global_file_alone(home_worktree):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("GIT_", "PYTEST_"))
    }
    stdin = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    result = subprocess.run(
        [sys.executable, str(_HOOK)], input=stdin, cwd=str(home_worktree.main),
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-500:]
    output = json.loads(result.stdout)
    context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    system_message = output.get("systemMessage", "")

    home_worktree.assert_global_untouched()
    assert f"Project CLAUDE.md skipped: {home_worktree.reason}" in system_message, (
        system_message[-600:]
    )
    assert f"Project memory file unavailable ({home_worktree.reason})." in context, (
        context[-600:]
    )


def test_a_recovery_landing_on_a_project_checkout_still_recovers_it(tmp_path, monkeypatch):
    """Control: the same layout under a main checkout that is not $HOME."""
    world = _RemovedWorktree(tmp_path, monkeypatch, tmp_path / "main")

    session = session_memory_root()

    assert (session.root, session.cause) == (world.main.resolve(), None)


@pytest.mark.parametrize("document", [".claude/CLAUDE.md", "CLAUDE.md"])
def test_the_guard_refuses_a_contained_record_landing_on_a_dotfiles_home(
    home_worktree, document
):
    """The record verifies and contains the declaration, so the guard's
    RECORD_CONTAINED branch is reached; it must still refuse $HOME, for the
    global document and for the legacy one beside it."""
    identity = project_scope.get_worktree_identity_from_session_record()
    status, recovered = record_resolution(home_worktree.worktree, identity)
    assert status == RECORD_CONTAINED and recovered == home_worktree.main.resolve(), (
        "FIXTURE INVALID: the record branch is not reached"
    )

    assert stays_in_declared_project(
        home_worktree.worktree, home_worktree.main, home_worktree.main / document,
        identity,
    ) is False


def test_the_guard_still_admits_a_contained_record_landing_on_a_project_checkout(
    tmp_path, monkeypatch
):
    """Control: the record branch admits main when main is not $HOME."""
    world = _RemovedWorktree(tmp_path, monkeypatch, tmp_path / "main")
    identity = project_scope.get_worktree_identity_from_session_record()
    assert record_resolution(world.worktree, identity)[0] == RECORD_CONTAINED

    assert stays_in_declared_project(
        world.worktree, world.main, world.main / ".claude" / "CLAUDE.md", identity,
    ) is True


def test_the_guard_refuses_a_listed_worktree_landing_on_a_dotfiles_home(
    tmp_path, monkeypatch
):
    """A worktree moved away while its home repository still lists it: the
    listing proves identity, and the guard must still refuse $HOME."""
    world = _RemovedWorktree(tmp_path, monkeypatch, Path.home())
    moved = world.main / ".worktrees" / "listed"
    _git("worktree", "add", "-q", str(moved), "-b", "listed", cwd=world.main)
    moved.rename(tmp_path / "moved-away")
    assert moved.resolve() in project_scope._listed_worktrees(world.main.resolve()), (
        "FIXTURE INVALID: the listing branch is not reached"
    )

    assert stays_in_declared_project(
        moved, world.main, world.main / ".claude" / "CLAUDE.md", {},
    ) is False
