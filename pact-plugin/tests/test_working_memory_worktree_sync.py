"""
Tests for syncing Working Memory to the display CLAUDE.md a session actually reads.

A session rooted in a git worktree reads and is shown the WORKTREE's
.claude/CLAUDE.md (the file session_init/session_resume create), but the
database that backs memory search is keyed to the MAIN repository so every
worktree session of a project shares one history. These two targets must be
resolved independently: the display write follows the worktree, the database
key follows the main repo.

This module exercises three surfaces with real, unmocked git repositories and
worktrees wherever feasible:

  * the display-target resolver, _resolve_display_claude_md_path
  * the project-id detector's env branch, _detect_project_id
  * the end-to-end save(), which must write the worktree display file AND store
    the main-repo project_id on the database row

The repository's own .claude/CLAUDE.md is the live file for the running
session; every test here builds an isolated synthetic repo under tmp_path and
never touches real session state.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# scripts/ is a package; add skills/pact-memory so `scripts.*` imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "pact-memory"))

from scripts.working_memory import _resolve_display_claude_md_path, sync_to_claude_md
from scripts import working_memory as wm
from scripts.memory_api import PACTMemory


WORKING_MEMORY_SCAFFOLD = (
    "# {title}\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
    "Full history searchable via pact-memory skill. -->\n"
)
RETRIEVED_CONTEXT_SCAFFOLD = (
    "# {title}\n\n"
    "## Retrieved Context\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
)


def _git(*args, cwd):
    """Run a git command, raising on failure, with output suppressed."""
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_main_repo(root: Path, *, with_dot_claude=True, with_legacy=False):
    """Create a committed git repository at `root` with a CLAUDE.md.

    A worktree cannot be added until the main repo has at least one commit, so
    every fixture seeds an initial commit. The CLAUDE.md is intentionally NOT
    committed — it mirrors production, where CLAUDE.md is gitignored — so a
    README is committed instead to anchor the initial commit.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    if with_dot_claude:
        dot = root / ".claude"
        dot.mkdir(exist_ok=True)
        (dot / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Main"), encoding="utf-8"
        )
    if with_legacy:
        (root / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Main legacy"), encoding="utf-8"
        )
    (root / "README.md").write_text("anchor\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "initial", cwd=root)


def _add_worktree(main: Path, worktree: Path, *, branch="feature",
                  with_dot_claude=True, with_legacy=False):
    """Attach a git worktree to `main` and optionally seed its CLAUDE.md."""
    _git("worktree", "add", str(worktree), "-b", branch, cwd=main)
    if with_dot_claude:
        dot = worktree / ".claude"
        dot.mkdir(parents=True, exist_ok=True)
        (dot / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Worktree"), encoding="utf-8"
        )
    if with_legacy:
        (worktree / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Worktree legacy"), encoding="utf-8"
        )


@pytest.fixture
def worktree_repo(tmp_path):
    """A real main repo plus an attached worktree, each with .claude/CLAUDE.md.

    Yields (main_root, worktree_root). The current working directory is set to
    the worktree for the duration of the test and restored afterwards, matching
    a worktree-rooted session.
    """
    main = tmp_path / "mainproj"
    worktree = tmp_path / "wt-feature"
    _init_main_repo(main)
    _add_worktree(main, worktree)
    prev_cwd = Path.cwd()
    os.chdir(str(worktree))
    try:
        yield main, worktree
    finally:
        os.chdir(str(prev_cwd))


@pytest.fixture
def clean_env(monkeypatch):
    """Ensure CLAUDE_PROJECT_DIR is unset for env-unset scenarios."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Display-target resolver — _resolve_display_claude_md_path
# ---------------------------------------------------------------------------

class TestDisplayTargetResolver:
    """The resolver returns the CLAUDE.md the current session displays."""

    def test_worktree_session_resolves_worktree_file_when_env_unset(
        self, worktree_repo, clean_env
    ):
        """A worktree session with no env var resolves the worktree display file."""
        _main, worktree = worktree_repo
        resolved = _resolve_display_claude_md_path()
        assert resolved == worktree / ".claude" / "CLAUDE.md"

    def test_worktree_session_resolves_worktree_file_when_env_is_worktree(
        self, worktree_repo, monkeypatch
    ):
        """With CLAUDE_PROJECT_DIR set to the worktree, the worktree file wins."""
        _main, worktree = worktree_repo
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        resolved = _resolve_display_claude_md_path()
        assert resolved == worktree / ".claude" / "CLAUDE.md"

    def test_main_checkout_resolves_main_file(self, tmp_path, clean_env):
        """In a plain (non-worktree) checkout the display target is the main file.

        Here the display target and the database target coincide, so the
        resolver must return the same path a main-repo session would write.
        """
        main = tmp_path / "soloproj"
        _init_main_repo(main)
        prev = Path.cwd()
        os.chdir(str(main))
        try:
            resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved == main / ".claude" / "CLAUDE.md"

    def test_resolves_legacy_root_file_when_no_dot_claude(self, tmp_path, clean_env):
        """A worktree carrying only ./CLAUDE.md resolves that legacy location."""
        main = tmp_path / "mainproj"
        worktree = tmp_path / "wt-legacy"
        _init_main_repo(main)
        _add_worktree(main, worktree, with_dot_claude=False, with_legacy=True)
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved == worktree / "CLAUDE.md"

    def test_returns_none_when_no_display_file_exists(self, tmp_path, clean_env):
        """When neither display location exists, the resolver returns None so the
        caller skips the sync rather than creating a file."""
        main = tmp_path / "mainproj"
        worktree = tmp_path / "wt-bare"
        _init_main_repo(main)
        _add_worktree(main, worktree, with_dot_claude=False)
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved is None

    def test_non_git_directory_falls_through_to_cwd_without_crashing(
        self, tmp_path, clean_env
    ):
        """Outside any git repo the resolver probes cwd and never raises."""
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        prev = Path.cwd()
        os.chdir(str(plain))
        try:
            # No CLAUDE.md anywhere under cwd -> None, and crucially no exception.
            resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved is None

    def test_non_git_directory_resolves_cwd_file_when_present(
        self, tmp_path, clean_env
    ):
        """Outside git, a CLAUDE.md under cwd is still resolved via the cwd probe."""
        plain = tmp_path / "loose-dir"
        (plain / ".claude").mkdir(parents=True)
        (plain / ".claude" / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Loose"), encoding="utf-8"
        )
        prev = Path.cwd()
        os.chdir(str(plain))
        try:
            resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved == plain / ".claude" / "CLAUDE.md"

    def test_unavailable_git_binary_falls_through_to_cwd(self, tmp_path, clean_env):
        """If git cannot be invoked the resolver degrades to the cwd probe."""
        plain = tmp_path / "loose"
        (plain / ".claude").mkdir(parents=True)
        (plain / ".claude" / "CLAUDE.md").write_text(
            WORKING_MEMORY_SCAFFOLD.format(title="Loose"), encoding="utf-8"
        )
        prev = Path.cwd()
        os.chdir(str(plain))
        try:
            with patch("scripts.working_memory.subprocess.run",
                       side_effect=FileNotFoundError("git missing")):
                resolved = _resolve_display_claude_md_path()
        finally:
            os.chdir(str(prev))
        assert resolved == plain / ".claude" / "CLAUDE.md"

    def test_env_outside_worktree_does_not_force_worktree_write(
        self, worktree_repo, tmp_path, monkeypatch
    ):
        """When the env points at an unrelated directory that has no CLAUDE.md,
        the resolver does not invent a worktree write; it falls through to the
        git/cwd probe and resolves the worktree the session is actually in.

        This guards the cwd-divergence degradation path: a stale or mismatched
        CLAUDE_PROJECT_DIR must not silently redirect or suppress the sync.
        """
        _main, worktree = worktree_repo
        stray = tmp_path / "stray-no-claude"
        stray.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(stray))
        resolved = _resolve_display_claude_md_path()
        # Env dir has no CLAUDE.md -> probe falls through to the worktree (cwd).
        assert resolved == worktree / ".claude" / "CLAUDE.md"


# ---------------------------------------------------------------------------
# Database-key stability — _detect_project_id env branch
# ---------------------------------------------------------------------------

class TestProjectIdStability:
    """The database key stays the main-repo slug across worktree sessions."""

    def test_worktree_env_keys_to_main_repo_basename(self, worktree_repo, monkeypatch):
        """CLAUDE_PROJECT_DIR set to a worktree resolves to the MAIN basename.

        The worktree's own basename would fragment the project key across
        sessions; the main repo's basename keeps one shared key.
        """
        main, worktree = worktree_repo
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        assert PACTMemory._detect_project_id() == main.name

    def test_worktree_subdir_env_keys_to_main_repo_basename(
        self, worktree_repo, monkeypatch
    ):
        """CLAUDE_PROJECT_DIR at a subdirectory inside a worktree also keys to
        the main basename.

        A worktree subdirectory's git common-dir still resolves to the main
        repo's shared .git, so it aligns to the main basename like the worktree
        root and an in-repo subdirectory do. Exercised unmocked.
        """
        main, worktree = worktree_repo
        subdir = worktree / "src" / "module"
        subdir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(subdir))
        assert PACTMemory._detect_project_id() == main.name

    def test_worktree_session_env_unset_keys_to_main_repo_basename(
        self, worktree_repo, clean_env
    ):
        """A worktree session with no env var still keys to the main basename
        via the git-root (Strategy 2) detection path."""
        main, _worktree = worktree_repo
        assert PACTMemory._detect_project_id() == main.name

    def test_main_repo_env_keys_to_main_repo_basename(self, tmp_path, monkeypatch):
        """CLAUDE_PROJECT_DIR at a plain repo root returns that repo's basename
        unchanged — the worktree rewrite must not fire for the root itself."""
        main = tmp_path / "soloproj"
        _init_main_repo(main)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(main))
        assert PACTMemory._detect_project_id() == "soloproj"

    def test_non_git_env_keys_to_env_basename(self, tmp_path, monkeypatch):
        """A non-git CLAUDE_PROJECT_DIR cannot resolve a main repo, so the env
        basename is returned unchanged (graceful fall-through)."""
        plain = tmp_path / "plain-target"
        plain.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
        assert PACTMemory._detect_project_id() == "plain-target"

    def test_in_repo_subdir_env_keys_to_main_repo_basename(self, tmp_path, monkeypatch):
        """CLAUDE_PROJECT_DIR at a subdirectory inside a repo keys to the main
        basename, not the subdirectory's own.

        A subdirectory's basename is not the project name; returning it would
        fragment the project key. The env branch aligns with the git-root and
        cwd-marker branches, which already resolve any in-repo path to the repo
        root. Exercised against a real git repository, unmocked.
        """
        main = tmp_path / "mainproj"
        _init_main_repo(main)
        subdir = main / "src" / "module"
        subdir.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(subdir))
        assert PACTMemory._detect_project_id() == "mainproj"

    def test_in_repo_subdir_env_keys_to_main_repo_basename_mocked(self, monkeypatch):
        """Fast mocked mirror of the in-repo-subdir alignment case.

        A subdirectory's git common-dir resolves (absolutely) to the main repo's
        .git, whose parent differs from the subdirectory, so the main basename
        is returned. Kept alongside the unmocked pin as a quick regression check.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/mainproj/.git\n"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/home/user/mainproj/src/module")
        with patch("scripts.memory_api.subprocess.run", return_value=mock_result):
            assert PACTMemory._detect_project_id() == "mainproj"

    def test_git_timeout_in_env_branch_falls_back_to_env_basename(
        self, tmp_path, monkeypatch
    ):
        """A git timeout while probing a worktree env path degrades to the env
        basename rather than raising."""
        worktree_like = tmp_path / "wt-like"
        worktree_like.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree_like))
        with patch("scripts.memory_api.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 5)):
            assert PACTMemory._detect_project_id() == "wt-like"


# ---------------------------------------------------------------------------
# End-to-end parity — the acceptance criterion
# ---------------------------------------------------------------------------

def _working_memory_block(text: str) -> str:
    """Return the text of the ## Working Memory section (up to the next ##)."""
    match = re.search(r"## Working Memory\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    return match.group(1) if match else ""


def _retrieved_context_block(text: str) -> str:
    match = re.search(r"## Retrieved Context\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    return match.group(1) if match else ""


class TestEndToEndParity:
    """save() writes the worktree display file and keys the row to the main repo."""

    def test_worktree_save_populates_worktree_block_and_keys_to_main(
        self, worktree_repo, clean_env
    ):
        """A worktree-rooted save() writes the WORKTREE Working Memory block and
        stores the main-repo slug on the database row.

        This is the acceptance criterion: the display follows the worktree, the
        database key follows the main repo. Driven through the real save() path
        against a temporary database.
        """
        main, worktree = worktree_repo
        db_path = worktree.parent / "memory.db"
        mem = PACTMemory(db_path=db_path)

        memory_id = mem.save({"context": "Worktree session entry"})

        worktree_text = (worktree / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        main_text = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")

        # Display write landed in the worktree, not the main repo.
        assert "Worktree session entry" in _working_memory_block(worktree_text)
        assert "Worktree session entry" not in main_text

        # Database row keyed to the main-repo slug, not the worktree name.
        stored = mem.get(memory_id)
        assert stored is not None
        assert stored.project_id == main.name

    def test_main_session_save_populates_main_block(self, tmp_path, clean_env):
        """A plain main-repo save() writes the main Working Memory block and
        keys the row to the main slug."""
        main = tmp_path / "soloproj"
        _init_main_repo(main)
        prev = Path.cwd()
        os.chdir(str(main))
        try:
            db_path = tmp_path / "memory.db"
            mem = PACTMemory(db_path=db_path)
            memory_id = mem.save({"context": "Main session entry"})
        finally:
            os.chdir(str(prev))

        main_text = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Main session entry" in _working_memory_block(main_text)

        stored = mem.get(memory_id)
        assert stored is not None
        assert stored.project_id == "soloproj"

    def test_worktree_and_main_saves_render_identical_entry(self, tmp_path, clean_env):
        """Saving the same memory in a worktree session and a main session
        produces the same rendered entry in each session's display block.

        This is Option B's guarantee: a worktree session shows the same entries
        a main-repo session would.
        """
        # Main-repo session.
        main = tmp_path / "mainproj"
        _init_main_repo(main)
        worktree = tmp_path / "wt-feature"
        _add_worktree(main, worktree)

        memory = {"context": "Shared rendering check", "goal": "parity"}

        # Render in the worktree session.
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            with patch("scripts.working_memory._resolve_display_claude_md_path",
                       return_value=worktree / ".claude" / "CLAUDE.md"):
                sync_to_claude_md(dict(memory), memory_id="shared-id")
        finally:
            os.chdir(str(prev))

        # Render in the main session.
        with patch("scripts.working_memory._resolve_display_claude_md_path",
                   return_value=main / ".claude" / "CLAUDE.md"):
            sync_to_claude_md(dict(memory), memory_id="shared-id")

        worktree_entry = _working_memory_block(
            (worktree / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        )
        main_entry = _working_memory_block(
            (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        )
        assert worktree_entry == main_entry
        assert "Shared rendering check" in worktree_entry

    def test_retrieved_context_sync_targets_worktree_display_file(
        self, tmp_path, clean_env
    ):
        """The retrieved-context sibling syncs to the worktree display file too.

        sync_retrieved_to_claude_md shares the same display-target resolver, so a
        worktree session's Retrieved Context block lands in the worktree file.
        """
        from scripts.working_memory import sync_retrieved_to_claude_md

        main = tmp_path / "mainproj"
        worktree = tmp_path / "wt-feature"
        _init_main_repo(main)
        # Seed the worktree with a Retrieved Context scaffold instead of Working.
        _add_worktree(main, worktree, with_dot_claude=False)
        dot = worktree / ".claude"
        dot.mkdir(parents=True, exist_ok=True)
        (dot / "CLAUDE.md").write_text(
            RETRIEVED_CONTEXT_SCAFFOLD.format(title="Worktree"), encoding="utf-8"
        )

        retrieved = [{"context": "Recalled worktree note", "memory_id": "r1"}]
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            sync_retrieved_to_claude_md(retrieved, query="worktree note")
        finally:
            os.chdir(str(prev))

        worktree_text = (dot / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Recalled worktree note" in _retrieved_context_block(worktree_text)

    def test_save_succeeds_when_display_file_missing(self, tmp_path, clean_env):
        """A missing display file must never fail the save.

        The resolver returns None when no CLAUDE.md exists; save() still returns
        a memory_id and persists the database row.
        """
        main = tmp_path / "mainproj"
        worktree = tmp_path / "wt-no-display"
        _init_main_repo(main)
        _add_worktree(main, worktree, with_dot_claude=False)
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            db_path = tmp_path / "memory.db"
            mem = PACTMemory(db_path=db_path)
            memory_id = mem.save({"context": "No display file present"})
        finally:
            os.chdir(str(prev))

        assert memory_id is not None
        stored = mem.get(memory_id)
        assert stored is not None
        assert stored.context == "No display file present"

    def test_save_succeeds_when_sync_raises(self, worktree_repo, clean_env):
        """An exception inside the sync must be swallowed and never fail save()."""
        main, worktree = worktree_repo
        db_path = worktree.parent / "memory.db"
        mem = PACTMemory(db_path=db_path)
        with patch("scripts.memory_api.sync_to_claude_md",
                   side_effect=RuntimeError("disk on fire")):
            memory_id = mem.save({"context": "Sync blows up"})
        assert memory_id is not None
        stored = mem.get(memory_id)
        assert stored is not None
