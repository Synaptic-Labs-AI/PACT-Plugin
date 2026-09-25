"""
Tests for PACTMemory._detect_project_id() -- multi-strategy fallback detection.

Tests cover:
1. Strategy 1: CLAUDE_PROJECT_DIR env var
1.5. Strategy 1.5: session-record project_dir (between env and git). NOT
   exercised here: the record discovery refuses test processes, so the real
   leg is inert in this suite and the replica below stays equivalent without
   it. The leg's own coverage lives in test_project_dir_resolution.py.
2. Strategy 2: git rev-parse --git-common-dir (worktree-safe repo root)
3. Strategy 3: Current working directory basename
4. Fallback ordering when strategies fail
5. Explicit project_id in constructor overrides detection
6. Edge cases: subprocess timeout, git not found, OSError

Note: memory_api.py uses relative imports requiring package context.
We replicate the _detect_project_id logic here rather than fighting
Python's import system, then validate equivalence via a source-check test.
"""

import os
import re
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The real detector, for the arms that need a real filesystem: a symlink cannot
# be expressed through the replica-plus-mock style below. conftest.py puts
# skills/pact-memory on sys.path.
from scripts import memory_api
from scripts.memory_api import PACTMemory


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

    This helper patches the walk's ONE probe, `memory_api._stat_if_present`,
    to report "absent" for any path that is NOT inside `tmp_path`. Within
    tmp_path, the original behavior is preserved. Effect: walk-up across
    ancestors above tmp_path always sees "no markers" regardless of ambient
    state. The replica and the real `_find_project_root` both reach the probe
    through the module, so the patch covers both; a walk that stops calling it
    escapes the isolation.
    """
    tmp_resolved = tmp_path.resolve()
    original_probe = memory_api._stat_if_present

    def _is_inside(p):
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved == tmp_resolved or tmp_resolved in resolved.parents

    def _confined_probe(path):
        if _is_inside(Path(path)):
            return original_probe(path)
        return None

    monkeypatch.setattr(memory_api, "_stat_if_present", _confined_probe)


# Path to the actual source file for equivalence checking
_MEMORY_API_PATH = (
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts" / "memory_api.py"
)


def _find_project_root_under_test(start: Path) -> Path:
    """
    Replica of PACTMemory._find_project_root() for isolated testing.

    Walks UP from `start` looking for project markers; returns first match
    or `start` unchanged if none found. Probes through the real module's
    `_stat_if_present`, so `_isolate_walkup_to` reaches this walk too.
    """
    try:
        current = start.resolve()
    except (OSError, RuntimeError):
        return start
    for parent in [current] + list(current.parents):
        if memory_api._stat_if_present(parent / ".git") is not None:
            return parent
        dot_claude = memory_api._stat_if_present(parent / ".claude")
        if dot_claude is not None and stat.S_ISDIR(dot_claude.st_mode):
            return parent
        if memory_api._stat_if_present(parent / "CLAUDE.md") is not None:
            return parent
        if memory_api._stat_if_present(parent / ".claude" / "CLAUDE.md") is not None:
            return parent
    return start  # fallback: use original


def _detect_project_id_under_test():
    """
    Replica of PACTMemory._detect_project_id() for isolated testing.

    This function mirrors the implementation in memory_api.py. The
    test_source_equivalence test verifies that the source of the real
    method matches this replica, so any drift will be caught.

    NO RECORD LEG, DELIBERATELY. The real method's Strategy 1.5 consults the
    session record through pact_session, whose discovery refuses test
    processes (PYTEST_CURRENT_TEST), so under this suite the leg is inert and
    the replica is equivalent without it. Do not "repair" the replica by
    adding one — the leg's coverage lives in test_project_dir_resolution.py.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Strategy 1: Environment variable (original behavior)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        # When CLAUDE_PROJECT_DIR points below a repo's root (a worktree OR
        # an in-repo subdirectory), its basename is not the project name and
        # would fragment the project_id across sessions. Prefer the MAIN
        # repo's basename so every session of a project shares one key,
        # aligning this env branch (Strategy 1) with the git-root and
        # cwd-marker branches (Strategies 2/3), which already resolve to the
        # repo root. The rewrite fires when git resolves a main repo whose
        # root differs from the env path; only a repo-root env path or a
        # non-git path (where the main anchor equals, or cannot be resolved
        # from, the env path) keeps the env basename — RESOLVED, so a
        # symlinked project dir names its target. A path that will not
        # resolve keeps its unresolved basename.
        try:
            env_root = Path(project_dir).resolve()
        except (OSError, RuntimeError):
            env_root = None
        try:
            result = subprocess.run(
                ["git", "-C", project_dir, "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = Path(result.stdout.strip())
                if not common_dir.is_absolute():
                    common_dir = Path(project_dir) / common_dir
                main_repo_root = common_dir.resolve().parent
                # Compare via normcase so a case-insensitive filesystem does
                # not fire the rewrite for paths that differ only in case
                # (a no-op on case-sensitive systems, where normcase is
                # identity).
                if env_root is not None and os.path.normcase(
                    str(main_repo_root)
                ) != os.path.normcase(str(env_root)):
                    logger.debug(
                        "project_id detected from CLAUDE_PROJECT_DIR worktree main repo: %s",
                        main_repo_root.name,
                    )
                    return main_repo_root.name
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        project_name = (env_root or Path(project_dir)).name
        logger.debug("project_id detected from CLAUDE_PROJECT_DIR: %s", project_name)
        return project_name

    # Strategy 2: Git repository root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, fragmenting
    # project_id across sessions. --git-common-dir always points to the
    # shared .git directory; its parent is the main repo root.
    # git returns this path relative to the invoking directory when run at a
    # repo root (the bare ".git") and absolute elsewhere, so resolve a relative
    # result against the cwd before taking its parent.
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
            common_dir = Path(result.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = Path.cwd() / common_dir
            repo_root = common_dir.resolve().parent
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
        Strategy 1 (env var) before Strategy 1.5 (session record) before
        Strategy 2 (git) before Strategy 3 (cwd). This catches accidental
        strategy reordering that substring checks alone miss. The 1.5 position
        is load-bearing: BELOW env and ABOVE git, because in a multi-repo
        workspace the cwd's git root can be the wrong scope.
        """
        real_source = _extract_method_body(_MEMORY_API_PATH, "_detect_project_id")
        assert real_source is not None, "Could not find _detect_project_id in memory_api.py"

        # Check key implementation lines are present
        assert 'os.environ.get("CLAUDE_PROJECT_DIR")' in real_source
        # The env and record strategies name a project from a declared directory
        # through the shared _project_name_for_declared_dir helper; the git
        # derivation markers that used to sit inline moved with it and are
        # pinned on the helper by
        # test_declared_dir_helper_carries_the_main_repo_rewrite.
        assert "_project_name_for_declared_dir(project_dir," in real_source
        # Strategy 1.5: the session-record rung between env and git.
        assert "get_project_dir_from_session_record()" in real_source
        assert "_project_name_for_declared_dir(record_dir," in real_source
        # Strategy 2 reaches git through the module-level main_repo_root()
        # helper rather than spawning its own subprocess. The subprocess
        # markers this assertion used to carry moved with it and are pinned on
        # the helper by test_main_repo_root_carries_the_git_derivation.
        assert "repo_root = main_repo_root()" in real_source
        # Strategy 3 now walks up from cwd to find the nearest project marker.
        assert "_find_project_root(Path.cwd())" in real_source

        # Verify strategy ordering in the CODE (not docstring).
        # Use code-specific markers that won't appear in the docstring.
        pos_env = real_source.index('os.environ.get("CLAUDE_PROJECT_DIR")')
        pos_record = real_source.index("get_project_dir_from_session_record()")
        pos_git = real_source.index("repo_root = main_repo_root()")
        pos_cwd = real_source.index("_find_project_root(Path.cwd())")

        assert pos_env < pos_record, (
            f"Strategy ordering violation: env var (pos {pos_env}) should appear "
            f"before the session record (pos {pos_record})"
        )
        assert pos_record < pos_git, (
            f"Strategy ordering violation: session record (pos {pos_record}) "
            f"should appear before git (pos {pos_git})"
        )
        assert pos_git < pos_cwd, (
            f"Strategy ordering violation: git (pos {pos_git}) should appear "
            f"before cwd (pos {pos_cwd})"
        )

    def test_declared_dir_helper_carries_the_main_repo_rewrite(self):
        """The extracted helper holds the worktree/main-repo rewrite both
        declared-directory strategies (env, session record) share.

        These markers moved out of _detect_project_id when Strategy 1's inline
        derivation collapsed onto _project_name_for_declared_dir, so they are
        pinned here rather than dropped — the same migration pattern as
        test_main_repo_root_carries_the_git_derivation.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "_project_name_for_declared_dir")
        assert helper_body is not None, "_project_name_for_declared_dir must exist in memory_api.py"
        assert "main_repo_root(declared_dir)" in helper_body
        assert "os.path.normcase" in helper_body
        assert "declared_root or Path(declared_dir)" in helper_body

    def test_main_repo_root_carries_the_git_derivation(self):
        """The extracted helper holds what the two strategies used to duplicate.

        These four markers moved out of _detect_project_id when Strategies 1
        and 2 collapsed onto one derivation, so they are pinned here rather
        than dropped. The relative-result guard is the load-bearing one: git
        returns a bare ".git" at a repo root, and a caller joining a relative
        base against a path would get a cwd-relative result.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "main_repo_root")
        assert helper_body is not None, "main_repo_root must exist in memory_api.py"
        assert '"rev-parse", "--git-common-dir"' in helper_body
        assert "timeout=5" in helper_body
        assert "(subprocess.TimeoutExpired, FileNotFoundError, OSError)" in helper_body
        assert "if not common_dir.is_absolute():" in helper_body

    def test_main_repo_root_bases_a_relative_result_on_its_start_argument(self):
        """A relative git result resolves against `start`, not unconditionally
        against the cwd.

        The two callers pass different bases — Strategy 1 passes the env path
        and Strategy 2 passes nothing — and those bases differ precisely inside
        a worktree. A helper that always joined against the cwd would change
        Strategy 1 silently, in the one case the caller exists to handle.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "main_repo_root")
        assert helper_body is not None
        assert "Path(start) if start is not None else Path.cwd()" in helper_body

    def test_detect_project_id_does_not_rebind_the_helper_name(self):
        """No local named main_repo_root inside _detect_project_id.

        Rebinding the module-level helper's own name in the method would make
        it local for the whole method and raise UnboundLocalError on the call
        that produces the value.
        """
        real_source = _extract_method_body(_MEMORY_API_PATH, "_detect_project_id")
        assert real_source is not None
        assert "main_repo_root =" not in real_source

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

    def test_env_var_worktree_prefers_main_repo_basename(self):
        """When CLAUDE_PROJECT_DIR is a worktree, the MAIN repo basename wins.

        A worktree env path resolves via git to a main repo whose root differs
        from the env path; the project_id must be the main repo's basename so
        all sessions of the project share one key, rather than the worktree's
        own (fragmenting) basename.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/some/git/repo/.git\n"

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/env/var/project"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "repo"

    def test_env_var_in_repo_subdir_prefers_main_repo_basename(self):
        """An env path inside a repo (a subdirectory) keys to the main basename.

        The subdirectory's git common-dir resolves to the repo's shared .git,
        whose parent differs from the subdirectory, so the env branch returns the
        repo basename — aligning Strategy 1 with the repo-root semantics of
        Strategies 2 and 3 and preventing per-subdirectory key fragmentation.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/myrepo/.git\n"

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/myrepo/src/mod"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "myrepo"

    def test_env_var_worktree_subdir_prefers_main_repo_basename(self):
        """An env path inside a worktree (a subdirectory) keys to the main basename.

        A worktree subdirectory's common-dir still points at the main repo's
        shared .git, so it resolves to the main basename like the worktree root
        and an in-repo subdirectory do.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/myrepo/.git\n"

        with patch.dict(os.environ,
                        {"CLAUDE_PROJECT_DIR": "/home/user/wt-feature/src/mod"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "myrepo"

    def test_env_var_repo_root_keeps_env_basename(self):
        """At a repo ROOT, --git-common-dir is the relative '.git', which resolves
        back to the root itself, so the rewrite does not fire and the env
        basename is kept.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ".git\n"

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/myrepo"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "myrepo"

    def test_env_var_non_git_keeps_env_basename(self):
        """A non-git env path cannot resolve a main repo (git returns non-zero),
        so the original env basename is returned unchanged.
        """
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/tmp/loose-dir"}), \
             patch("subprocess.run", return_value=mock_result):
            result = _detect_project_id_under_test()
        assert result == "loose-dir"

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

    @pytest.mark.parametrize(
        "walk",
        [_find_project_root_under_test, PACTMemory._find_project_root],
        ids=["replica", "real"],
    )
    @pytest.mark.parametrize("isolated", [True, False], ids=["isolated", "open"])
    def test_the_isolation_reaches_the_walk(self, walk, isolated, tmp_path, monkeypatch):
        """_isolate_walkup_to must hide a marker ABOVE the confined tree from
        both walks. The open run is the matched control: the same marker is
        found, so a hidden marker in the isolated run is the patch working,
        not an empty tree."""
        (tmp_path / ".claude").mkdir()
        confined = tmp_path / "confined"
        nested = confined / "a"
        nested.mkdir(parents=True)
        if isolated:
            _isolate_walkup_to(monkeypatch, confined)
        expected = nested if isolated else tmp_path
        assert walk(nested).resolve() == expected.resolve()

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


class TestSymlinkedProjectDir:
    """Strategy 1 names the directory CLAUDE_PROJECT_DIR RESOLVES TO, not the
    link it was reached through.

    The backlog writer stores the resolved path, so a link basename here and a
    target path there split one project into two names. Real detector, real
    filesystem, real git for the repo arm: the resolve is the thing under test.
    """

    def test_symlink_to_git_less_dir_names_the_target(self, tmp_path, monkeypatch):
        """RED WHEN Strategy 1 returns the unresolved env basename on the
        git-less branch."""
        target = tmp_path / "plain-target"
        target.mkdir()
        link = tmp_path / "plain-link"
        link.symlink_to(target)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(link))
        assert PACTMemory._detect_project_id() == "plain-target"

    def test_symlink_to_repo_root_names_the_target(self, tmp_path, monkeypatch):
        """RED WHEN Strategy 1 returns the unresolved env basename on the
        branch where git's main root equals the env path."""
        target = tmp_path / "repo-target"
        target.mkdir()
        subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
        link = tmp_path / "repo-link"
        link.symlink_to(target)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(link))
        assert PACTMemory._detect_project_id() == "repo-target"

    def test_plain_dir_keeps_its_own_basename(self, tmp_path, monkeypatch):
        """Regression pin: resolving a path that is not a link changes nothing."""
        plain = tmp_path / "plain-dir"
        plain.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
        assert PACTMemory._detect_project_id() == "plain-dir"
