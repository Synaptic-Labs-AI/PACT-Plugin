"""
Tests for PACTMemory._detect_project_id() -- multi-strategy fallback detection.

Tests cover:
1. Strategy 1: CLAUDE_PROJECT_DIR env var
1.5. Strategy 1.5: session-record project_dir (between env and git). NOT
   exercised here: the record discovery refuses test processes, so the real
   leg is inert in this suite. Its own coverage lives in
   test_project_dir_resolution.py.
2. Strategy 2: git rev-parse --git-common-dir (worktree-safe repo root)
3. Strategy 3: Current working directory basename
4. Fallback ordering when strategies fail
5. Explicit project_id in constructor overrides detection
6. Edge cases: subprocess timeout, git not found, OSError

These arms call the REAL PACTMemory._detect_project_id() through the
`scripts.` package route (conftest puts skills/pact-memory on sys.path).
There is no replica. A hand-maintained copy is what let the detector's new
decline semantics go untested here while four arms kept asserting the old
basename behaviour against paths that no longer exist: the replica said
"my-project" for a directory that was never on disk, and the production
method returned None.
"""

import ast
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The real detector, for the arms that need a real filesystem: a symlink cannot
# be expressed through the replica-plus-mock style below. conftest.py puts
# skills/pact-memory on sys.path.
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


def _rev_parse_mock(*, common, toplevel=None, git_dir=None, default_rc=1):
    """A `subprocess.run` side_effect answering each `rev-parse` subcommand.

    `main_repo_root` issues `--show-toplevel` and `--git-dir` IN ADDITION to
    `--git-common-dir`, so a blanket `return_value` mock would feed the
    common-dir answer to all three and describe a repository that cannot
    exist. Any other git call (`config --get`) answers the default: exit 1,
    which is "no key set" — a repository with no origin, NOT an unreadable
    one. A higher default would model a broken repository and legitimately
    DECLINE.
    """

    answers = {
        "--git-common-dir": common,
        "--show-toplevel": toplevel,
        "--git-dir": git_dir,
    }

    def _run(command, **_kwargs):
        result = MagicMock()
        for option, stdout in answers.items():
            if option in command and stdout is not None:
                result.returncode = 0
                result.stdout = stdout
                return result
        result.returncode = default_rc
        result.stdout = ""
        return result

    return _run


def _isolate_walkup_to(monkeypatch, tmp_path):
    """Confine the walk-up search to the test's tmp_path subtree.

    `PACTMemory._find_project_root` walks Path.parents up to "/" looking
    for project markers. On macOS, pytest's tmp_path lives under
    /private/var/folders/.../T/, and unrelated processes can leak markers
    (e.g., a stray `.claude/` dir) into that shared parent. The walk-up
    correctly returns the leaked marker, but the test loses isolation
    against ambient state.

    This helper monkeypatches Path.exists and Path.is_dir to return False
    for any path that is NOT inside `tmp_path`. Within tmp_path, the
    original behavior is preserved. Effect: walk-up across ancestors above
    tmp_path always sees "no markers" regardless of ambient state.

    Caveat: patches `Path.exists` and `Path.is_dir` at the CLASS level via
    monkeypatch — affects ALL Path operations in the test's scope, not just
    `PACTMemory._find_project_root` walks. Bounded by monkeypatch teardown.
    Future test authors extending tests that rely on Path methods for
    non-walk purposes should be aware.
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


def _walk_qualified_functions(tree):
    """Yield (node, qualified_name_parts) for every function in `tree`.

    Qualified names are built by descending through ClassDef nodes, so
    "PACTMemory._detect_project_id" is addressable and two classes that share
    a bare method name stay distinguishable. Functions nested inside functions
    are not descended into: no call site targets one, and walking them would
    make an inner name collide with its sibling.
    """
    stack = [(tree, [])]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child, prefix + [child.name]
            elif isinstance(child, ast.ClassDef):
                stack.append((child, prefix + [child.name]))


def _extract_method_body(source_path, method_name):
    """Extract one function body from a source file, by qualified name.

    REPLACES A REGEX-AND-INDENT WALK THAT MEASURED THE WRONG SPAN. That walk
    sliced from `match.start()`, which lands on the `def` keyword and skips
    the class indentation, so `base_indent` computed 0 for every method and the
    "body" ran to end-of-file — 1,238 of memory_api.py's 1,716 lines for
    `_detect_project_id`. The assertions below therefore matched code anywhere
    in that tail, including other methods, and passed by accident.

    An AST gives the exact span. `FunctionDef.lineno` is the `def` line, so
    decorators are excluded; `end_lineno` is the body's last line, so a
    function that legitimately runs to end-of-file is returned IN FULL rather
    than truncated — the failure mode a narrower fix would introduce.

    Args:
        source_path: File to read.
        method_name: A bare name ("_detect_project_id") or a dotted qualified
            path ("PACTMemory._detect_project_id"). A bare name that matches
            more than one definition raises rather than guessing: several
            methods in this tree share a bare name across classes, and a
            silent first-match would be the same measures-the-wrong-thing
            defect one level up.

    Returns:
        The source segment, stripped, or None when nothing matches.
    """
    content = source_path.read_text(encoding="utf-8")
    wanted = method_name.split(".")
    matches = [
        node
        for node, qualified in _walk_qualified_functions(ast.parse(content))
        if qualified[-len(wanted):] == wanted
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise AssertionError(
            f"{method_name!r} is ambiguous in {source_path.name}: "
            f"{len(matches)} definitions; pass a dotted qualified name"
        )
    segment = ast.get_source_segment(content, matches[0])
    return segment.strip() if segment is not None else None


class TestMethodBodyExtractor:
    """The instrument's own controls.

    The pins in TestSourceEquivalence only mean something if the extractor
    returns the named method's body and nothing else. Its predecessor did not:
    it sliced from match.start(), so base_indent was 0 and the body ran to
    end-of-file. These arms fail if that class of defect returns, and X2's
    negative half fails if a future narrowing fix truncates a body that
    legitimately ends at EOF.
    """

    def test_x1_the_real_extraction_is_narrow(self):
        """A real method's body contains no sibling def and no file tail."""
        body = _extract_method_body(_MEMORY_API_PATH, "_detect_project_id")
        assert body is not None
        assert "def _detect_project_id_with_source" not in body, (
            "the extractor returned a sibling method — the body is not narrowed"
        )
        assert "# Module-level singleton" not in body, (
            "the extractor ran to the module tail"
        )
        assert len(body.splitlines()) < 20, (
            f"extracted {len(body.splitlines())} lines for a one-line "
            "delegation; the span is not the method"
        )

    def test_x1_qualified_name_addresses_a_class_method(self):
        """A dotted qualified name reaches a method; a bare one still works."""
        dotted = _extract_method_body(
            _MEMORY_API_PATH, "PACTMemory._detect_project_identity_with_legacy"
        )
        bare = _extract_method_body(
            _MEMORY_API_PATH, "_detect_project_identity_with_legacy"
        )
        assert dotted is not None and bare is not None
        assert dotted == bare
        assert 'os.environ.get("CLAUDE_PROJECT_DIR")' in dotted

    def test_x1_an_ambiguous_bare_name_raises_rather_than_guessing(self, tmp_path):
        """Two classes sharing a method name must not silently first-match."""
        module = tmp_path / "ambiguous.py"
        module.write_text(
            "class A:\n"
            "    def run(self):\n"
            "        return 'a'\n"
            "\n"
            "class B:\n"
            "    def run(self):\n"
            "        return 'b'\n",
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="ambiguous"):
            _extract_method_body(module, "run")

        # The qualified form resolves the ambiguity rather than raising.
        assert "return 'b'" in _extract_method_body(module, "B.run")

    def test_x2_a_two_method_fixture_returns_the_first_body_only(self, tmp_path):
        """POSITIVE: the extractor stops at the end of the first method."""
        module = tmp_path / "two.py"
        module.write_text(
            "def first():\n"
            "    return 1\n"
            "\n"
            "def second():\n"
            "    return 2\n",
            encoding="utf-8",
        )
        body = _extract_method_body(module, "first")
        assert body is not None
        assert "return 1" in body
        assert "def second" not in body
        assert "return 2" not in body

    def test_x2_a_body_ending_at_eof_is_returned_in_full(self, tmp_path):
        """NEGATIVE: over-narrowing must not truncate a legitimate EOF body.

        Without this arm a repair could stop at any dedent and still pass the
        positive half, trading one blind spot for another.
        """
        module = tmp_path / "eof.py"
        module.write_text(
            "def only(x):\n"
            "    y = x + 1\n"
            "    return y\n",
            encoding="utf-8",
        )
        body = _extract_method_body(module, "only")
        assert body is not None
        assert body.splitlines()[-1].strip() == "return y", (
            f"the final statement was truncated: {body!r}"
        )
        assert "y = x + 1" in body


class TestSourceEquivalence:
    """Pins on the REAL implementation's source (there is no replica)."""

    def test_source_equivalence(self):
        """Pin the real detector's strategy markers and ordering directly.

        Reads _detect_project_identity_with_legacy, NOT
        _detect_project_id_with_source: the latter is now a one-line
        delegation, so the strategy markers and their order live in the
        former. The delegation itself is pinned separately by
        test_detect_project_id_still_delegates_with_its_signature.

        Verifies key implementation markers are present AND ordered correctly:
        the repository override before Strategy 1 (env var) before Strategy 1.5
        (session record) before Strategy 2 (git) before Strategy 3 (cwd). This
        catches accidental strategy reordering that substring checks alone
        miss. The 1.5 position is load-bearing: BELOW env and ABOVE git,
        because in a multi-repo workspace the cwd's git root can be the wrong
        scope.
        """
        real_source = _extract_method_body(
            _MEMORY_API_PATH, "_detect_project_identity_with_legacy"
        )
        assert real_source is not None, (
            "Could not find _detect_project_identity_with_legacy in "
            "memory_api.py"
        )

        # Check key implementation lines are present
        assert 'os.environ.get("CLAUDE_PROJECT_DIR")' in real_source
        # The env and record strategies name a project from a declared directory
        # through the shared _declared_dir_identity helper; the git derivation
        # markers that used to sit inline moved with it and are pinned on the
        # helper by test_declared_dir_helper_carries_the_main_repo_rewrite.
        assert "_declared_dir_identity(" in real_source
        assert "project_dir, \"CLAUDE_PROJECT_DIR\"" in real_source
        # Strategy 1.5: the session-record rung between env and git.
        assert "get_project_dir_from_session_record()" in real_source
        assert 'record_dir, "session record"' in real_source
        # Strategy 0 is the repository override, read from the git common dir.
        assert "read_override(cwd_common_dir)" in real_source
        # Strategy 2 reaches git through the module-level common-dir helper
        # rather than spawning its own subprocess. The subprocess markers this
        # assertion used to carry moved with it and are pinned on the helper by
        # test_main_repo_root_carries_the_git_derivation.
        assert "repo_root = main_repo_root(common=cwd_common_dir)" in real_source
        # Strategy 3 now walks up from cwd to find the nearest project marker.
        assert "_find_project_root(Path.cwd())" in real_source

        # Verify strategy ordering in the CODE (not docstring).
        # Use code-specific markers that won't appear in the docstring.
        pos_override = real_source.index("read_override(cwd_common_dir)")
        pos_env = real_source.index('os.environ.get("CLAUDE_PROJECT_DIR")')
        pos_record = real_source.index("get_project_dir_from_session_record()")
        pos_git = real_source.index("repo_root = main_repo_root(common=cwd_common_dir)")
        pos_cwd = real_source.index("_find_project_root(Path.cwd())")

        assert pos_override < pos_env, (
            f"Strategy ordering violation: repository override (pos "
            f"{pos_override}) should appear before env var (pos {pos_env})"
        )

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
        derivation collapsed onto the declared-dir helper, so they are pinned
        here rather than dropped — the same migration pattern as
        test_main_repo_root_carries_the_git_derivation.

        The helper is `_declared_dir_identity`; `_project_name_for_declared_dir`
        remains as the two-element façade the outside callers use.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "_declared_dir_identity")
        assert helper_body is not None, "_declared_dir_identity must exist in memory_api.py"
        assert "main_repo_common_dir(declared_dir)" in helper_body
        assert "os.path.normcase" in helper_body
        assert "declared_main_root or declared_root or Path(declared_dir)" in helper_body
        # The dead-path consult: when the declared path is gone, the helper
        # asks the worktree identity record (recorded_main_root) before
        # declining — presence pin only; the behavior is proven in
        # test_removed_worktree_record.py.
        assert "recorded_main_root(" in helper_body

    def test_main_repo_root_carries_the_git_derivation(self):
        """The extracted helper holds what the two strategies used to duplicate.

        These four markers moved out of _detect_project_id when Strategies 1
        and 2 collapsed onto one derivation, so they are pinned here rather
        than dropped. The relative-result guard is the load-bearing one: git
        returns a bare ".git" at a repo root, and a caller joining a relative
        base against a path would get a cwd-relative result.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "main_repo_common_dir")
        assert helper_body is not None, "main_repo_common_dir must exist in memory_api.py"
        assert '"rev-parse", "--git-common-dir"' in helper_body
        assert "timeout=5" in helper_body
        # ValueError joined the tuple with the #1653 path-byte fix: an
        # undecodable git answer is "could not answer", not a crash.
        assert (
            "(subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError)"
            in helper_body
        )
        assert "if not common.is_absolute():" in helper_body
        # The root is the common dir's parent, in the delegating wrapper.
        root_body = _extract_method_body(_MEMORY_API_PATH, "main_repo_root")
        assert root_body is not None, "main_repo_root must survive as a delegate"
        assert "main_repo_common_dir(start)" in root_body
        assert "common.parent" in root_body

    def test_main_repo_root_bases_a_relative_result_on_its_start_argument(self):
        """A relative git result resolves against `start`, not unconditionally
        against the cwd.

        The two callers pass different bases — Strategy 1 passes the env path
        and Strategy 2 passes nothing — and those bases differ precisely inside
        a worktree. A helper that always joined against the cwd would change
        Strategy 1 silently, in the one case the caller exists to handle.
        """
        helper_body = _extract_method_body(_MEMORY_API_PATH, "main_repo_common_dir")
        assert helper_body is not None
        assert "Path(start) if start is not None else Path.cwd()" in helper_body

    def test_detect_project_id_does_not_rebind_the_helper_name(self):
        """No local named main_repo_root inside the strategy body.

        Rebinding the module-level helper's own name in the method would make
        it local for the whole method and raise UnboundLocalError on the call
        that produces the value. The strategy body moved to
        _detect_project_id_with_source when the tuple-returning detector was
        split out, so the check follows it there.
        """
        real_source = _extract_method_body(
            _MEMORY_API_PATH, "_detect_project_identity_with_legacy"
        )
        assert real_source is not None
        assert "main_repo_root =" not in real_source
        assert "main_repo_common_dir =" not in real_source

    def test_detect_project_id_still_delegates_with_its_signature(self):
        """The bare-string detector must survive as a delegation.

        _detect_project_id is a public-shaped seam: outside callers and the
        precedence pins use it, and they expect a bare string. Re-pointing the
        strategy pins at _detect_project_id_with_source would otherwise leave
        this method with NO assertion at all — coverage lost at exactly the
        moment it stopped containing anything to cover.
        """
        real_source = _extract_method_body(_MEMORY_API_PATH, "_detect_project_id")
        assert real_source is not None, "Could not find _detect_project_id"
        assert "_detect_project_id_with_source()[0]" in real_source, (
            "the bare-string detector no longer delegates to the "
            "tuple-returning detector"
        )
        # The strategy logic must NOT have been duplicated back into it.
        assert 'os.environ.get("CLAUDE_PROJECT_DIR")' not in real_source

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

    def test_uses_env_var_when_set(self, tmp_path):
        """Should return basename of CLAUDE_PROJECT_DIR when env var is set.

        The directory must EXIST: a declared path that is gone now DECLINES,
        which is the production behaviour the old replica did not model.
        """
        project = tmp_path / "my-project"
        project.mkdir()
        no_repo = MagicMock()
        no_repo.returncode = 128
        no_repo.stdout = ""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(project)}), \
             patch("subprocess.run", return_value=no_repo):
            result = PACTMemory._detect_project_id()
        assert result == "my-project"

    def test_env_var_returns_basename_not_full_path(self, tmp_path):
        """Should return only the directory basename, not the full path."""
        project = tmp_path / "cool-repo"
        project.mkdir()
        no_repo = MagicMock()
        no_repo.returncode = 128
        no_repo.stdout = ""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(project)}), \
             patch("subprocess.run", return_value=no_repo):
            result = PACTMemory._detect_project_id()
        assert result == "cool-repo"

    def test_env_var_worktree_prefers_main_repo_basename(self):
        """When CLAUDE_PROJECT_DIR is a worktree, the MAIN repo basename wins.

        A worktree env path resolves via git to a main repo whose root differs
        from the env path; the project_id must be the main repo's basename so
        all sessions of the project share one key, rather than the worktree's
        own (fragmenting) basename.
        """
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/env/var/project"}), \
             patch("subprocess.run", side_effect=_rev_parse_mock(
                 common="/some/git/repo/.git\n",
                 toplevel="/some/git/repo\n",
                 git_dir="/some/git/repo/.git\n",
             )):
            result = PACTMemory._detect_project_id()
        assert result == "repo"

    def test_env_var_in_repo_subdir_prefers_main_repo_basename(self):
        """An env path inside a repo (a subdirectory) keys to the main basename.

        The subdirectory's git common-dir resolves to the repo's shared .git,
        whose parent differs from the subdirectory, so the env branch returns the
        repo basename — aligning Strategy 1 with the repo-root semantics of
        Strategies 2 and 3 and preventing per-subdirectory key fragmentation.
        """
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/myrepo/src/mod"}), \
             patch("subprocess.run", side_effect=_rev_parse_mock(
                 common="/home/user/myrepo/.git\n",
                 toplevel="/home/user/myrepo\n",
                 git_dir="/home/user/myrepo/.git\n",
             )):
            result = PACTMemory._detect_project_id()
        assert result == "myrepo"

    def test_env_var_worktree_subdir_prefers_main_repo_basename(self):
        """An env path inside a worktree (a subdirectory) keys to the main basename.

        A worktree subdirectory's common-dir still points at the main repo's
        shared .git, so it resolves to the main basename like the worktree root
        and an in-repo subdirectory do.
        """
        with patch.dict(os.environ,
                        {"CLAUDE_PROJECT_DIR": "/home/user/wt-feature/src/mod"}), \
             patch("subprocess.run", side_effect=_rev_parse_mock(
                 common="/home/user/myrepo/.git\n",
                 toplevel="/home/user/myrepo\n",
                 git_dir="/home/user/myrepo/.git\n",
             )):
            result = PACTMemory._detect_project_id()
        assert result == "myrepo"

    def test_env_var_repo_root_keeps_env_basename(self, tmp_path):
        """At a repo ROOT, --git-common-dir is the relative '.git', which resolves
        back to the root itself, so the rewrite does not fire and the env
        basename is kept.
        """
        project = tmp_path / "myrepo"
        project.mkdir()
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(project)}), \
             patch("subprocess.run", side_effect=_rev_parse_mock(
                 common=".git\n",
                 toplevel=str(project) + "\n",
                 git_dir=".git\n",
             )):
            result = PACTMemory._detect_project_id()
        assert result == "myrepo"

    def test_env_var_non_git_keeps_env_basename(self, tmp_path):
        """A non-git env path cannot resolve a main repo (git returns non-zero),
        so the original env basename is returned unchanged.
        """
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        loose = tmp_path / "loose-dir"
        loose.mkdir()
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(loose)}), \
             patch("subprocess.run", return_value=mock_result):
            result = PACTMemory._detect_project_id()
        assert result == "loose-dir"

    # --- Strategy 2: Git repo root ---

    def test_uses_git_when_env_var_not_set(self, clean_env_no_claude_project_dir):
        """Should fall back to git rev-parse when no env var.

        --git-common-dir returns the .git directory path; the code resolves
        its parent to get the repo root, then takes .name for project_id.
        """
        with patch("subprocess.run", side_effect=_rev_parse_mock(
            common="/users/dev/awesome-repo/.git\n",
            toplevel="/users/dev/awesome-repo\n",
            git_dir="/users/dev/awesome-repo/.git\n",
        )):
            result = PACTMemory._detect_project_id()
        assert result == "awesome-repo"

    def test_git_strips_whitespace_from_output(self, clean_env_no_claude_project_dir):
        """Should strip trailing whitespace/newline from git output."""
        with patch("subprocess.run", side_effect=_rev_parse_mock(
            common="  /path/to/repo/.git  \n",
            toplevel="  /path/to/repo  \n",
            git_dir="  /path/to/repo/.git  \n",
        )):
            result = PACTMemory._detect_project_id()
        assert result == "repo"

    def test_git_nonzero_returncode_falls_through(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git returns non-zero (not a repo)."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.cwd", return_value=Path("/fallback/cwd-dir")):
            result = PACTMemory._detect_project_id()
        assert result == "cwd-dir"

    def test_git_empty_stdout_falls_through(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git returns empty output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.cwd", return_value=Path("/fallback/from-cwd")):
            result = PACTMemory._detect_project_id()
        assert result == "from-cwd"

    # --- Strategy 2 failure modes ---

    def test_git_timeout_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git subprocess times out."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)), \
             patch("pathlib.Path.cwd", return_value=Path("/timeout/fallback")):
            result = PACTMemory._detect_project_id()
        assert result == "fallback"

    def test_git_not_found_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd when git binary is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")), \
             patch("pathlib.Path.cwd", return_value=Path("/no-git/project")):
            result = PACTMemory._detect_project_id()
        assert result == "project"

    def test_git_os_error_falls_back_to_cwd(self, clean_env_no_claude_project_dir):
        """Should fall back to cwd on generic OSError from subprocess."""
        with patch("subprocess.run", side_effect=OSError("permission denied")), \
             patch("pathlib.Path.cwd", return_value=Path("/oserror/fallback-proj")):
            result = PACTMemory._detect_project_id()
        assert result == "fallback-proj"

    # --- Strategy 3: CWD ---

    def test_cwd_used_as_final_fallback(self, clean_env_no_claude_project_dir):
        """Should use cwd basename when both env var and git are unavailable."""
        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", return_value=Path("/home/user/my-cwd-project")):
            result = PACTMemory._detect_project_id()
        assert result == "my-cwd-project"

    def test_cwd_oserror_returns_none(self, clean_env_no_claude_project_dir):
        """Should return None when even cwd() raises OSError."""
        with patch("subprocess.run", side_effect=FileNotFoundError()), \
             patch("pathlib.Path.cwd", side_effect=OSError("cwd deleted")):
            result = PACTMemory._detect_project_id()
        assert result is None

    # --- Subprocess call parameters ---

    def test_git_called_with_correct_args_and_timeout(self, clean_env_no_claude_project_dir):
        """Should call git with capture_output, text=True, timeout=5."""
        with patch("subprocess.run", side_effect=_rev_parse_mock(
            common="/some/repo/.git\n",
            toplevel="/some/repo\n",
            git_dir="/some/repo/.git\n",
        )) as mock_run:
            PACTMemory._detect_project_id()

        # FOUR calls since the identity change: `--git-common-dir`,
        # `--show-toplevel` and `--git-dir` describe the repository shape,
        # then `config --get` reads the origin. `common.parent` alone cannot
        # name the identity root for a submodule or a
        # `--separate-git-dir` repository.
        assert mock_run.call_count == 4
        expected = (
            ["git", "rev-parse", "--git-common-dir"],
            ["git", "rev-parse", "--show-toplevel"],
            ["git", "rev-parse", "--git-dir"],
        )
        for index, command in enumerate(expected):
            args, kwargs = mock_run.call_args_list[index]
            assert args[0] == command
            assert kwargs["capture_output"] is True
            # BYTES, decoded by os.fsdecode in the production helper: a path
            # can contain bytes invalid in the locale, and locale decoding
            # would raise.
            assert kwargs["text"] is False
            assert kwargs["timeout"] == 5
            assert "env" in kwargs
            assert "CLAUDE_PROJECT_DIR" not in kwargs["env"]

        origin_args, origin_kwargs = mock_run.call_args_list[3]
        assert origin_args[0] == [
            "git", "-C", "/some/repo", "config", "--get", "remote.origin.url"
        ]
        assert origin_kwargs["text"] is False
        assert origin_kwargs["timeout"] == 5


class TestFindProjectRoot:
    """Tests for PACTMemory._find_project_root() walk-up helper."""

    @pytest.mark.claude_md_ambient  # §11.3.1 (a): marker-absence detection — the fixture's CLAUDE.md IS a project marker
    def test_returns_start_when_no_markers(self, tmp_path, monkeypatch):
        """No markers anywhere on the path → returns start unchanged."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        # tmp_path is a clean isolated directory — no .git, .claude, CLAUDE.md.
        # Confine the walk-up to tmp_path so ambient markers above (e.g., a
        # stray .claude/ leaked into macOS /var/folders/.../T/ by an unrelated
        # process) cannot win the walk-up.
        _isolate_walkup_to(monkeypatch, tmp_path)
        result = PACTMemory._find_project_root(nested)
        # Walk-up found nothing → falls back to start
        assert result == nested

    def test_finds_git_ancestor(self, tmp_path):
        """Walk-up finds a .git marker on an ancestor."""
        project = tmp_path / "my-project"
        nested = project / "src" / "lib"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()

        result = PACTMemory._find_project_root(nested)
        assert result == project.resolve()
        assert result.name == "my-project"

    def test_finds_dot_claude_ancestor(self, tmp_path):
        """Walk-up finds a .claude/ dir on an ancestor."""
        project = tmp_path / "cool-repo"
        nested = project / "docs"
        nested.mkdir(parents=True)
        (project / ".claude").mkdir()

        result = PACTMemory._find_project_root(nested)
        assert result == project.resolve()
        assert result.name == "cool-repo"

    def test_finds_legacy_claude_md_ancestor(self, tmp_path):
        """Walk-up finds ./CLAUDE.md on an ancestor (legacy location)."""
        project = tmp_path / "legacy-proj"
        nested = project / "scripts"
        nested.mkdir(parents=True)
        (project / "CLAUDE.md").write_text("# project memory\n")

        result = PACTMemory._find_project_root(nested)
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

        result = PACTMemory._find_project_root(nested)
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

        result = PACTMemory._find_project_root(nested)
        # Should return the nearest (inner), not the farthest (outer)
        assert result == inner.resolve()
        assert result.name == "inner-project"

    def test_start_itself_has_marker(self, tmp_path):
        """When the start directory itself has a marker, return start."""
        project = tmp_path / "self-marked"
        project.mkdir()
        (project / ".git").mkdir()

        result = PACTMemory._find_project_root(project)
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
            result = PACTMemory._detect_project_id()

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
            result = PACTMemory._detect_project_id()

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
            result = PACTMemory._detect_project_id()

        assert result == "root-project"

    @pytest.mark.claude_md_ambient  # §11.3.1 (a): marker-absence detection — the fixture's CLAUDE.md IS a project marker
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
            result = PACTMemory._detect_project_id()

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
            result = PACTMemory._detect_project_id()

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
