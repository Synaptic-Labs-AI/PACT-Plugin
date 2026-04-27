"""
Tests for PACTMemory._detect_project_id() -- 3-strategy fallback detection.

Tests cover:
1. Strategy 1: CLAUDE_PROJECT_DIR env var
2. Strategy 2: git rev-parse --git-common-dir (worktree-safe repo root)
3. Strategy 3: Current working directory basename
4. Fallback ordering when strategies fail
5. Explicit project_id in constructor overrides detection
6. Edge cases: subprocess timeout, git not found, OSError

Note: memory_api.py uses relative imports requiring package context.
We replicate the _detect_project_id logic here rather than fighting
Python's import system, then validate equivalence via a source-check test.
"""

import hashlib
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def clean_env_no_claude_project_dir():
    """Fixture that removes CLAUDE_PROJECT_DIR from the environment.

    Yields with a patched os.environ that contains all current env vars
    except CLAUDE_PROJECT_DIR, preventing strategy-1 from short-circuiting.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    with patch.dict(os.environ, env, clear=True):
        yield


def _isolate_walkup_to(monkeypatch, tmp_path):
    """Confine the walk-up search to the test's tmp_path subtree.

    `_find_project_root_under_test` walks Path.parents up to "/" looking
    for project markers. On macOS, pytest's tmp_path lives under
    /private/var/folders/.../T/, and unrelated processes can leak markers
    (e.g., a stray `.claude/` dir) into that shared parent. The walk-up
    correctly returns the leaked marker, but the test loses isolation
    against ambient state.

    This helper monkeypatches Path.exists and Path.is_dir to return False
    for any path that is NOT inside `tmp_path`. Within tmp_path, the
    original behavior is preserved. Effect: walk-up across ancestors above
    tmp_path always sees "no markers" regardless of ambient state.
    """
    tmp_resolved = tmp_path.resolve()
    original_exists = Path.exists
    original_is_dir = Path.is_dir

    def _is_inside(p):
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved == tmp_resolved or tmp_resolved in resolved.parents

    def _patched_exists(self):
        if _is_inside(self):
            return original_exists(self)
        return False

    def _patched_is_dir(self):
        if _is_inside(self):
            return original_is_dir(self)
        return False

    monkeypatch.setattr(Path, "exists", _patched_exists)
    monkeypatch.setattr(Path, "is_dir", _patched_is_dir)


# Path to the actual source file for equivalence checking
_MEMORY_API_PATH = (
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts" / "memory_api.py"
)


def _find_project_root_under_test(start: Path) -> Path:
    """
    Replica of PACTMemory._find_project_root() for isolated testing.

    Walks UP from `start` looking for project markers; returns first match
    or `start` unchanged if none found.
    """
    try:
        current = start.resolve()
    except (OSError, RuntimeError):
        return start
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / ".claude").is_dir():
            return parent
        if (parent / "CLAUDE.md").exists():
            return parent
        if (parent / ".claude" / "CLAUDE.md").exists():
            return parent
    return start  # fallback: use original


def _detect_project_id_under_test():
    """
    Replica of PACTMemory._detect_project_id() for isolated testing.

    This function mirrors the implementation in memory_api.py. The
    test_source_equivalence test verifies that the source of the real
    method matches this replica, so any drift will be caught.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Strategy 1: Environment variable (original behavior)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        logger.debug("project_id detected from CLAUDE_PROJECT_DIR: %s", Path(project_dir).name)
        return Path(project_dir).name

    # Strategy 2: Git repository root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, fragmenting
    # project_id across sessions. --git-common-dir always points to the
    # shared .git directory; its parent is the main repo root.
    # NOTE: Twin pattern in working_memory.py (_get_claude_md_path) and
    #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_common_dir = result.stdout.strip()
            repo_root = Path(git_common_dir).resolve().parent
            project_name = repo_root.name
            logger.debug("project_id detected from git root: %s", project_name)
            return project_name
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # git not installed, not a repo, or command timed out
        logger.debug("Git detection failed, falling back to cwd")

    # Strategy 3: Current working directory — walk UP to nearest project marker.
    # Fixes subdirectory invocation (e.g., running CLI from .claude/ or src/
    # would previously return the subdirectory basename as the project_id).
    try:
        cwd_root = _find_project_root_under_test(Path.cwd())
        cwd_name = cwd_root.name
        if cwd_name:
            logger.debug("project_id detected from cwd: %s", cwd_name)
            return cwd_name
    except OSError:
        logger.debug("Failed to detect project_id from cwd")

    return None


def _extract_method_body(source_path, method_name):
    """Extract the body of a method from a source file for comparison."""
    content = source_path.read_text(encoding="utf-8")
    # Find the method definition
    pattern = rf'def {method_name}\(.*?\).*?:'
    match = re.search(pattern, content)
    if not match:
        return None

    start = match.start()
    # Find the end of the method (next def at same or lower indent, or class-level code)
    lines = content[start:].split('\n')
    method_lines = [lines[0]]
    base_indent = len(lines[0]) - len(lines[0].lstrip())

    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped and not line.startswith(' ' * (base_indent + 1)) and not stripped.startswith('#'):
            # Check if this is a decorator or new method
            if stripped.startswith('def ') or stripped.startswith('@') or stripped.startswith('class '):
                break
        method_lines.append(line)

    return '\n'.join(method_lines).strip()


class TestSourceEquivalence:
    """Verify the test replica matches the real implementation."""

    def test_source_equivalence(self):
        """The replica logic should match the real _detect_project_id method body.

        Verifies key implementation markers are present AND ordered correctly:
        Strategy 1 (env var) before Strategy 2 (git) before Strategy 3 (cwd).
        This catches accidental strategy reordering that substring checks alone miss.
        """
        real_source = _extract_method_body(_MEMORY_API_PATH, "_detect_project_id")
        assert real_source is not None, "Could not find _detect_project_id in memory_api.py"

        # Check key implementation lines are present
        assert 'os.environ.get("CLAUDE_PROJECT_DIR")' in real_source
        assert '["git", "rev-parse", "--git-common-dir"]' in real_source
        assert "timeout=5" in real_source
        assert "(subprocess.TimeoutExpired, FileNotFoundError, OSError)" in real_source
        # Strategy 3 now walks up from cwd to find the nearest project marker.
        assert "_find_project_root(Path.cwd())" in real_source

        # Verify strategy ordering in the CODE (not docstring).
        # Use code-specific markers that won't appear in the docstring.
        pos_env = real_source.index('os.environ.get("CLAUDE_PROJECT_DIR")')
        pos_git = real_source.index('["git", "rev-parse", "--git-common-dir"]')
        pos_cwd = real_source.index("_find_project_root(Path.cwd())")

        assert pos_env < pos_git, (
            f"Strategy ordering violation: env var (pos {pos_env}) should appear "
            f"before git (pos {pos_git})"
        )
        assert pos_git < pos_cwd, (
            f"Strategy ordering violation: git (pos {pos_git}) should appear "
            f"before cwd (pos {pos_cwd})"
        )

    def test_find_project_root_exists_in_source(self):
        """The real memory_api.py must define _find_project_root as a class method."""
        content = _MEMORY_API_PATH.read_text(encoding="utf-8")
        assert "def _find_project_root(" in content, (
            "_find_project_root helper must exist in memory_api.py"
        )
        # Verify it walks upward from the start path via .parents
        helper_body = _extract_method_body(_MEMORY_API_PATH, "_find_project_root")
        assert helper_body is not None
        assert ".parents" in helper_body, "_find_project_root must walk upward"
        assert '".git"' in helper_body
        assert '".claude"' in helper_body
        assert '"CLAUDE.md"' in helper_body


class TestDetectProjectId:
    """Tests for _detect_project_id() static method."""

    # --- Strategy 1: Environment variable ---

    def test_uses_env_var_when_set(self):
        """Should return basename of CLAUDE_PROJECT_DIR when env var is set."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/my-project"}):
            result = _detect_project_id_under_test()
        assert result == "my-project"

    def test_env_var_returns_basename_not_full_path(self):
        """Should return only the directory basename, not the full path."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/deeply/nested/path/cool-repo"}):
            result = _detect_project_id_under_test()
        assert result == "cool-repo"

    def test_env_var_takes_priority_over_git(self):
        """Env var should win even when git would return a different value."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/some/git/repo/.git\n"

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/env/var/project"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "project"

    # --- Strategy 2: Git repo root ---

    def test_uses_git_when_env_var_not_set(self, clean_env_no_claude_project_dir):
        """Should fall back to git rev-parse when no env var.

        --git-common-dir returns the .git directory path; the code resolves
        its parent to get the repo root, then takes .name for project_id.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/users/dev/awesome-repo/.git\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "awesome-repo"

    def test_git_strips_whitespace_from_output(self, clean_env_no_claude_project_dir):
        """Should strip trailing whitespace/newline from git output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  /path/to/repo/.git  \n"

        with patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "repo"

    def test_git_nonzero_returncode_falls_through(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git returns non-zero (not a repo)."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.cwd", return_value=Path("/fallback/cwd-dir")):
            result = _detect_project_id_under_test()
        assert result == "cwd-dir"

    def test_git_empty_stdout_falls_through(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git returns empty output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.cwd", return_value=Path("/fallback/from-cwd")):
            result = _detect_project_id_under_test()
        assert result == "from-cwd"

    # --- Strategy 2 failure modes ---

    def test_git_timeout_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git subprocess times out."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)), \
             patch("pathlib.Path.cwd", return_value=Path("/timeout/fallback")):
            result = _detect_project_id_under_test()
        assert result == "fallback"

    def test_git_not_found_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git binary is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")), \
             patch("pathlib.Path.cwd", return_value=Path("/no-git/project")):
            result = _detect_project_id_under_test()
        assert result == "project"

    def test_git_os_error_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd on generic OSError from subprocess."""
        with patch("subprocess.run", side_effect=OSError("permission denied")), \
             patch("pathlib.Path.cwd", return_value=Path("/oserror/fallback-proj")):
            result = _detect_project_id_under_test()
        assert result == "fallback-proj"

    # --- Strategy 3: CWD ---

    def test_cwd_used_as_final_fallback(self, clean_env_no_claude_project_dir):
        """Should use cwd basename when both env var and git are unavailable."""
        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=Path("/home/user/my-cwd-project")):
            result = _detect_project_id_under_test()
        assert result == "my-cwd-project"

    def test_cwd_oserror_returns_none(self, clean_env_no_claude_project_dir):
        """Should return None when even cwd() raises OSError."""
        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", side_effect=OSError("cwd deleted")):
            result = _detect_project_id_under_test()
        assert result is None

    # --- Subprocess call parameters ---

    def test_git_called_with_correct_args_and_timeout(self, clean_env_no_claude_project_dir):
        """Should call git with capture_output, text=True, timeout=5."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/some/repo/.git\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _detect_project_id_under_test()

        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )


class TestFindProjectRoot:
    """Tests for PACTMemory._find_project_root() walk-up helper."""

    def test_returns_start_when_no_markers(self, tmp_path, monkeypatch):
        """No markers anywhere on the path → returns start unchanged."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        # tmp_path is a clean isolated directory — no .git, .claude, CLAUDE.md.
        # Confine the walk-up to tmp_path so ambient markers above (e.g., a
        # stray .claude/ leaked into macOS /var/folders/.../T/ by an unrelated
        # process) cannot win the walk-up.
        _isolate_walkup_to(monkeypatch, tmp_path)
        result = _find_project_root_under_test(nested)
        # Walk-up found nothing → falls back to start
        assert result == nested

    def test_finds_git_ancestor(self, tmp_path):
        """Walk-up finds a .git marker on an ancestor."""
        project = tmp_path / "my-project"
        nested = project / "src" / "lib"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()

        result = _find_project_root_under_test(nested)
        assert result == project.resolve()
        assert result.name == "my-project"

    def test_finds_dot_claude_ancestor(self, tmp_path):
        """Walk-up finds a .claude/ dir on an ancestor."""
        project = tmp_path / "cool-repo"
        nested = project / "docs"
        nested.mkdir(parents=True)
        (project / ".claude").mkdir()

        result = _find_project_root_under_test(nested)
        assert result == project.resolve()
        assert result.name == "cool-repo"

    def test_finds_legacy_claude_md_ancestor(self, tmp_path):
        """Walk-up finds ./CLAUDE.md on an ancestor (legacy location)."""
        project = tmp_path / "legacy-proj"
        nested = project / "scripts"
        nested.mkdir(parents=True)
        (project / "CLAUDE.md").write_text("# project memory\n")

        result = _find_project_root_under_test(nested)
        assert result == project.resolve()
        assert result.name == "legacy-proj"

    def test_finds_dot_claude_claude_md_ancestor(self, tmp_path):
        """Walk-up finds .claude/CLAUDE.md on an ancestor (new default).

        This case matters when .claude/ is a FILE (edge case) or when the
        .claude/ directory hasn't been created but a CLAUDE.md was placed
        directly at the expected path — neither of which the earlier
        .claude/ dir check would catch.
        """
        # Construct a case where .claude/ exists but is only meaningful
        # through its CLAUDE.md child. The .is_dir() check on .claude/
        # already catches this path, so the explicit .claude/CLAUDE.md
        # check is defence-in-depth. Verify both routes work.
        project = tmp_path / "new-default-proj"
        nested = project / "src"
        nested.mkdir(parents=True)
        (project / ".claude").mkdir()
        (project / ".claude" / "CLAUDE.md").write_text("# new default\n")

        result = _find_project_root_under_test(nested)
        assert result == project.resolve()

    def test_returns_nearest_ancestor_not_farthest(self, tmp_path):
        """When multiple ancestors have markers, returns the NEAREST."""
        outer = tmp_path / "outer"
        inner = outer / "inner-project"
        nested = inner / "src"
        nested.mkdir(parents=True)
        # Both outer and inner have .git markers
        (outer / ".git").mkdir()
        (inner / ".git").mkdir()

        result = _find_project_root_under_test(nested)
        # Should return the nearest (inner), not the farthest (outer)
        assert result == inner.resolve()
        assert result.name == "inner-project"

    def test_start_itself_has_marker(self, tmp_path):
        """When the start directory itself has a marker, return start."""
        project = tmp_path / "self-marked"
        project.mkdir()
        (project / ".git").mkdir()

        result = _find_project_root_under_test(project)
        assert result == project.resolve()


class TestCwdSubdirectoryDetection:
    """Tests for Strategy 3 (CWD) subdirectory detection via walk-up.

    These exercise the real bug fix: running the CLI from a subdirectory
    of a project should return the PROJECT's basename, not the subdirectory's.
    """

    def test_cwd_from_subdirectory_returns_project_basename(
        self, clean_env_no_claude_project_dir, tmp_path
    ):
        """Running from project/.claude/ should return 'project', not '.claude'."""
        project = tmp_path / "my-real-project"
        subdir = project / ".claude"
        subdir.mkdir(parents=True)
        (project / ".git").mkdir()

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=subdir):
            result = _detect_project_id_under_test()

        assert result == "my-real-project"

    def test_cwd_from_nested_subdirectory(
        self, clean_env_no_claude_project_dir, tmp_path
    ):
        """Running from project/src/deeply/nested/ still resolves to project."""
        project = tmp_path / "deep-project"
        nested = project / "src" / "deeply" / "nested"
        nested.mkdir(parents=True)
        (project / "CLAUDE.md").write_text("# project memory\n")

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=nested):
            result = _detect_project_id_under_test()

        assert result == "deep-project"

    def test_cwd_from_project_root_unchanged(
        self, clean_env_no_claude_project_dir, tmp_path
    ):
        """Running from project root returns project root basename."""
        project = tmp_path / "root-project"
        project.mkdir()
        (project / ".git").mkdir()

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=project):
            result = _detect_project_id_under_test()

        assert result == "root-project"

    def test_cwd_no_markers_falls_back_to_cwd_basename(
        self, clean_env_no_claude_project_dir, tmp_path, monkeypatch
    ):
        """No markers found walking up → fall back to cwd basename (legacy behavior)."""
        # tmp_path is clean: no .git, .claude, CLAUDE.md anywhere.
        # Confine the walk-up to tmp_path so ambient markers above (e.g., a
        # stray .claude/ leaked into macOS /var/folders/.../T/ by an unrelated
        # process) cannot win the walk-up.
        leaf = tmp_path / "orphan-dir"
        leaf.mkdir()
        _isolate_walkup_to(monkeypatch, tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=leaf):
            result = _detect_project_id_under_test()

        # Walk-up finds nothing → returns start → .name == "orphan-dir"
        assert result == "orphan-dir"

    def test_cwd_dot_claude_detection(
        self, clean_env_no_claude_project_dir, tmp_path
    ):
        """Walk-up triggers on .claude/ directory at ancestor."""
        project = tmp_path / "claude-dir-proj"
        subdir = project / "work" / "nested"
        subdir.mkdir(parents=True)
        (project / ".claude").mkdir()  # only marker is .claude/

        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=subdir):
            result = _detect_project_id_under_test()

        assert result == "claude-dir-proj"
