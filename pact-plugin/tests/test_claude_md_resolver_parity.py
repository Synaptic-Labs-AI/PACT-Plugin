"""
Parity lint for the 5 CLAUDE.md resolvers.

Five independent resolvers probe for project-level CLAUDE.md at both
`.claude/CLAUDE.md` (new default, priority) and `./CLAUDE.md` (legacy).
This test drives all five against the same tmp project for each scenario
and asserts they all agree on the classification (not_found / dot_claude /
legacy). The test is about CONSISTENCY, not correctness -- if one resolver
is updated, its siblings must stay in sync.

Resolvers under test:
1. shared.claude_md_manager.resolve_project_claude_md_path  -- canonical
2. staleness.get_project_claude_md_path                      -- hooks/
3. working_memory._get_claude_md_path                        -- skills/
4. memory_api.PACTMemory._find_project_root                  -- skills/ (walks UP)
5. worktree_guard inline probe                                -- hooks/ (inline)
"""

import os
from pathlib import Path
from typing import Optional

import pytest


# Classification vocabulary -- shared across all resolvers
NOT_FOUND = "not_found"
DOT_CLAUDE = "dot_claude"
LEGACY = "legacy"


def _classify_path(path: Optional[Path], tmp: Path) -> str:
    """
    Map an Optional[Path] to the shared vocabulary.

    .resolve() both sides to handle macOS /private/var vs /var symlink quirks
    that break direct tmp_path equality checks.
    """
    if path is None:
        return NOT_FOUND
    resolved = path.resolve()
    if resolved == (tmp / ".claude" / "CLAUDE.md").resolve():
        return DOT_CLAUDE
    if resolved == (tmp / "CLAUDE.md").resolve():
        return LEGACY
    return NOT_FOUND


# --- Per-resolver wrappers ----------------------------------------------------
# Each takes (tmp, monkeypatch) and returns a classification string.


def resolver_claude_md_manager(tmp: Path, monkeypatch) -> str:
    """Canonical: returns (Path, source). `new_default` means neither exists."""
    from shared.claude_md_manager import resolve_project_claude_md_path

    path, source = resolve_project_claude_md_path(tmp)
    if source == "new_default":
        return NOT_FOUND
    return _classify_path(path, tmp)


def resolver_staleness(tmp: Path, monkeypatch) -> str:
    """Public entry exercises env-var short-circuit + git fallback chain."""
    from staleness import get_project_claude_md_path

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp))
    return _classify_path(get_project_claude_md_path(), tmp)


def resolver_working_memory(tmp: Path, monkeypatch) -> str:
    """Mirror of staleness; same env-var-driven resolution strategy."""
    from scripts.working_memory import _get_claude_md_path

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp))
    return _classify_path(_get_claude_md_path(), tmp)


def resolver_memory_api(tmp: Path, monkeypatch) -> str:
    """
    _find_project_root walks UP looking for .git / .claude/ / CLAUDE.md markers
    and returns a *directory*, not a file. We probe the returned dir for
    CLAUDE.md in canonical priority order to map it to the shared vocabulary.

    Must be loaded via `scripts.` package path -- memory_api uses relative
    imports (`from .database import ...`) that break under standalone load.

    ⚠ Walk-up environment dependency: `_find_project_root` walks upward from
    `tmp` searching parent directories for `.git`, `.claude/`, or `CLAUDE.md`.
    This test relies on pytest's `tmp_path` resolving OUTSIDE the real project
    tree -- on macOS that's typically `/var/folders/...` and on Linux usually
    `/tmp/...`, neither of which has any of those markers, so the walk falls
    through harmlessly and `root` ends up at the filesystem root (or `tmp`
    itself depending on the helper's exact return shape). If pytest is ever
    reconfigured with `--basetemp` pointing INSIDE the project root, the walk
    will hit the real `.git` and silently start exercising a different code
    path -- the parity assertion may still pass for the wrong reason. Keep
    pytest's basetemp outside the project tree.
    """
    from scripts.memory_api import PACTMemory

    root = PACTMemory._find_project_root(tmp)
    if (root / ".claude" / "CLAUDE.md").exists():
        return _classify_path(root / ".claude" / "CLAUDE.md", tmp)
    if (root / "CLAUDE.md").exists():
        return _classify_path(root / "CLAUDE.md", tmp)
    return NOT_FOUND


def resolver_worktree_guard(tmp: Path, monkeypatch) -> str:
    """
    worktree_guard has no importable helper -- its probe is inline at
    worktree_guard.py:179-184. This wrapper mirrors that exact expression.
    Because the inline probe is a bool, drift here is especially easy to
    miss without this parity test.
    """
    # Mirror worktree_guard.py lines 179-184 exactly
    is_project_dir = (
        (tmp / "CLAUDE.md").exists()
        or (tmp / ".claude" / "CLAUDE.md").exists()
    )
    if not is_project_dir:
        return NOT_FOUND
    # Map to vocabulary with canonical priority (dot_claude > legacy)
    if (tmp / ".claude" / "CLAUDE.md").exists():
        return DOT_CLAUDE
    return LEGACY


ALL_RESOLVERS = [
    ("claude_md_manager", resolver_claude_md_manager),
    ("staleness", resolver_staleness),
    ("working_memory", resolver_working_memory),
    ("memory_api", resolver_memory_api),
    ("worktree_guard", resolver_worktree_guard),
]


# --- Scenario builders --------------------------------------------------------


def _scenario_empty(tmp: Path) -> None:
    """No CLAUDE.md anywhere."""


def _scenario_legacy_only(tmp: Path) -> None:
    (tmp / "CLAUDE.md").write_text("# legacy")


def _scenario_dot_claude_only(tmp: Path) -> None:
    (tmp / ".claude").mkdir()
    (tmp / ".claude" / "CLAUDE.md").write_text("# dot-claude")


def _scenario_both(tmp: Path) -> None:
    """Priority check: .claude/CLAUDE.md must win."""
    (tmp / ".claude").mkdir()
    (tmp / ".claude" / "CLAUDE.md").write_text("# preferred")
    (tmp / "CLAUDE.md").write_text("# legacy")


def _scenario_bare_dot_claude(tmp: Path) -> None:
    """Bare .claude/ directory, no CLAUDE.md inside."""
    (tmp / ".claude").mkdir()


def _scenario_symlink(tmp: Path) -> None:
    """.claude/CLAUDE.md as a symlink to a real file elsewhere."""
    external = tmp / "external"
    external.mkdir()
    real_file = external / "real_claude.md"
    real_file.write_text("# symlinked content")
    (tmp / ".claude").mkdir()
    (tmp / ".claude" / "CLAUDE.md").symlink_to(real_file)


SCENARIOS = [
    ("empty", _scenario_empty, NOT_FOUND),
    ("legacy_only", _scenario_legacy_only, LEGACY),
    ("dot_claude_only", _scenario_dot_claude_only, DOT_CLAUDE),
    ("both_files", _scenario_both, DOT_CLAUDE),
    ("bare_dot_claude_dir", _scenario_bare_dot_claude, NOT_FOUND),
    ("symlink_dot_claude", _scenario_symlink, DOT_CLAUDE),
]


# --- Parity tests -------------------------------------------------------------


class TestClaudeMdResolverParity:
    """All 5 resolvers must agree on each scenario."""

    @pytest.mark.parametrize(
        "scenario_name,build_scenario,expected",
        SCENARIOS,
        ids=[s[0] for s in SCENARIOS],
    )
    def test_all_resolvers_agree(
        self, scenario_name, build_scenario, expected, tmp_path, monkeypatch
    ):
        # Isolate from any ambient CLAUDE_PROJECT_DIR. Per-resolver wrappers
        # re-set it as needed; without this, staleness and working_memory's
        # git fallback would escape tmp_path and find the real project.
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        build_scenario(tmp_path)

        classifications = {
            name: wrapper(tmp_path, monkeypatch) for name, wrapper in ALL_RESOLVERS
        }
        mismatches = {
            name: result
            for name, result in classifications.items()
            if result != expected
        }
        assert not mismatches, (
            f"Scenario {scenario_name!r}: expected {expected!r}, "
            f"got divergent results:\n"
            f"  All:        {classifications}\n"
            f"  Mismatches: {mismatches}"
        )


# --- Display-resolver parity invariant ---------------------------------------
#
# The lint above drives every resolver through the CLAUDE_PROJECT_DIR branch,
# so it never exercises the git-topology branches -- and it covers
# _get_claude_md_path, which has NO production callers, while omitting
# _resolve_display_claude_md_path, which determines every real sync write
# target. This class pins the specific invariant _resolve_display_claude_md_path's
# docstring asserts: it and _get_claude_md_path differ ONLY in the
# worktree-root branch, so in a non-worktree checkout they resolve identically.
#
# It must run with CLAUDE_PROJECT_DIR UNSET -- the env branch short-circuits
# before the git branches and would make the equivalence hold trivially,
# testing nothing. Re-pointing the 5-way lint at the live resolver and deleting
# the dead sibling is a separate follow-up, not this pin.


def _pgit(cwd: Path, *args: str) -> None:
    """Run git in `cwd` with a hermetic config (no user/global interference)."""
    import subprocess

    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd),
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
        check=False,
    )


def _init_repo_with_claude_md(root: Path) -> Path:
    """A committed git repo whose CLAUDE.md is gitignored + untracked (as in prod)."""
    root.mkdir(parents=True, exist_ok=True)
    _pgit(root, "init")
    _pgit(root, "config", "user.email", "t@e")
    _pgit(root, "config", "user.name", "T")
    (root / ".gitignore").write_text("CLAUDE.md\n.claude/\n", encoding="utf-8")
    (root / "README.md").write_text("seed", encoding="utf-8")
    _pgit(root, "add", ".gitignore", "README.md")
    _pgit(root, "commit", "-m", "seed")
    dot = root / ".claude"
    dot.mkdir()
    claude_md = dot / "CLAUDE.md"
    claude_md.write_text("# main\n", encoding="utf-8")
    return claude_md


class TestDisplayResolverParityInvariant:
    """Pin the docstring claim Option C revised (working_memory:_resolve_display_...).

    _resolve_display_claude_md_path anchors branch 2 on the WORKTREE root
    (--show-toplevel) and falls back on the MAIN repo root (--git-common-dir)
    in branch 3; _get_claude_md_path uses only the main-repo anchor. The claim:
    they differ ONLY in that worktree-root branch.

    Three cases together demonstrate the "only". Two of them predate Option C
    and pin the CONTEXT that gives "only" its meaning; one is the actual
    Option-C regression guard:

      - coincide_in_non_worktree  -- CONTEXT (branch-3-independent): both
        resolvers already coincided outside a worktree before Option C.
      - diverge_when_worktree_owns_a_file -- CONTEXT (branch-3-independent):
        the single legitimate divergence, in the worktree-root branch.
      - coincide_in_pact_worktree_without_own_file -- LOAD-BEARING: this case
        DIVERGED before Option C (display -> None, main -> the main file) and
        COINCIDES after, purely because branch 3 was added. Deleting branch 3
        flips only this case red; the other two stay green. It is the sole
        member of this class that guards the Option-C change.

    A sibling BEHAVIOURAL guard for the same case lives in
    test_working_memory_worktree_sync.py: that one asserts the concrete main
    file comes out AND that branch 3 fired. This class asserts the two
    resolvers AGREE -- the resolver-relationship axis this parity file owns.
    Different axis, different file, both fail on a branch-3 regression:
    defence in depth, not duplication.
    """

    def test_non_worktree_checkout_resolvers_coincide(self, tmp_path, monkeypatch):
        """In a plain (non-worktree) checkout the two resolvers return the SAME
        existing path -- the equivalence the docstring promises."""
        from scripts.working_memory import (
            _get_claude_md_path,
            _resolve_display_claude_md_path,
        )

        repo = tmp_path / "plainrepo"
        expected = _init_repo_with_claude_md(repo)

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(repo)

        display = _resolve_display_claude_md_path()
        main = _get_claude_md_path()

        # Both must resolve to the real file (a shared None would be vacuously
        # "equal" while proving nothing), and to the SAME file.
        assert display is not None and main is not None
        assert os.path.realpath(display) == os.path.realpath(expected)
        assert os.path.realpath(display) == os.path.realpath(main)

    def test_worktree_divergence_is_confined_to_the_worktree_root_branch(
        self, tmp_path, monkeypatch
    ):
        """Non-vacuity guard for the test above: the two resolvers CAN diverge,
        and do so only in the worktree-root branch. A worktree that owns a
        CLAUDE.md resolves the display path to its OWN file (branch 2) while the
        main-repo resolver still points at the main file -- so the coincidence
        above is a real property of the non-worktree case, not a constant."""
        from scripts.working_memory import (
            _get_claude_md_path,
            _resolve_display_claude_md_path,
        )

        repo = tmp_path / "mainrepo"
        main_file = _init_repo_with_claude_md(repo)
        worktree = tmp_path / "wt"
        _pgit(repo, "worktree", "add", str(worktree), "-b", "feature")
        wt_dot = worktree / ".claude"
        wt_dot.mkdir(parents=True)
        wt_file = wt_dot / "CLAUDE.md"
        wt_file.write_text("# worktree-own\n", encoding="utf-8")

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(worktree)

        display = _resolve_display_claude_md_path()
        main = _get_claude_md_path()

        assert display is not None and main is not None
        # Display follows the worktree's OWN file (branch 2, --show-toplevel);
        # the main-repo resolver stays on the main file (--git-common-dir).
        assert os.path.realpath(display) == os.path.realpath(wt_file)
        assert os.path.realpath(main) == os.path.realpath(main_file)
        assert os.path.realpath(display) != os.path.realpath(main)

    def test_coincide_in_pact_worktree_without_own_file(self, tmp_path, monkeypatch):
        """LOAD-BEARING: the one case Option C changed, and the only test in this
        class that a branch-3 regression turns red.

        A PACT-convention worktree has no CLAUDE.md of its own, so branch 2
        (--show-toplevel) finds nothing. BEFORE Option C the display resolver
        then fell through to cwd and returned None, while _get_claude_md_path
        returned the main-repo file -- they DIVERGED. Option C's branch 3
        (--git-common-dir) now sends the display resolver to that same main file,
        so the two AGREE. Delete branch 3 and this assertion fails (display -> None
        != main); the other two cases in this class stay green. That is the
        proof this pin actually guards the change, not merely the docstring's
        wording.

        The sibling BEHAVIOURAL guard (concrete file + branch-3-fired) lives in
        test_working_memory_worktree_sync.py; this asserts only that the two
        resolvers converge, which is the invariant this parity file exists for.
        """
        from scripts.working_memory import (
            _get_claude_md_path,
            _resolve_display_claude_md_path,
        )

        repo = tmp_path / "mainrepo"
        main_file = _init_repo_with_claude_md(repo)
        worktree = tmp_path / "wt-no-own"
        _pgit(repo, "worktree", "add", str(worktree), "-b", "feature")
        # Deliberately NO .claude/CLAUDE.md in the worktree -- the PACT convention.

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(worktree)

        display = _resolve_display_claude_md_path()
        main = _get_claude_md_path()

        assert display is not None and main is not None
        assert os.path.realpath(display) == os.path.realpath(main)
        # ...and specifically at the MAIN repo file, so a future change that made
        # both resolvers agree on the WRONG file would still be caught.
        assert os.path.realpath(display) == os.path.realpath(main_file)


# --- A location that cannot be examined --------------------------------------
#
# `Path.exists()` re-raised a PermissionError on 3.9 and 3.13 and returned
# False on 3.14, so the same unsearchable directory aborted these resolvers on
# two CI interpreters and fell through on the third. The rule every arm below
# holds, on every interpreter: an ABSENT location (ENOENT, ENOTDIR, EBADF,
# ELOOP, an unencodable path) is skipped silently; a location that cannot be
# EXAMINED ends resolution, at whatever rung it is met, with no fallback to a
# legacy file or a later rung, and is recorded; a git call that fails before
# anything is examined moves on to the next rung.

import shutil  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from tests.test_unreadable_location_carriers import (  # noqa: E402
    _NEEDS_NON_ROOT,
    _ancestor_unsearchable,
    _LEGACY_TEXT,
    _preferred_unsearchable,
    _symlink_loop,
    lock,  # noqa: F401 -- a fixture, requested by name below
)

_NEEDS_GIT = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="these arms build a real git repository and need git",
)


def _all_three(errors_display, errors_staleness):
    """Run the display resolver, the staleness twin and _get_claude_md_path
    in the current environment. Returns the three paths (None for none)."""
    import staleness
    from scripts.working_memory import (
        _get_claude_md_path,
        _resolve_display_claude_md_with_base,
    )

    display, _ = _resolve_display_claude_md_with_base(errors=errors_display)
    stale, _ = staleness._resolve_project_claude_md_with_base(errors=errors_staleness)
    return display, stale, _get_claude_md_path()


def _cwd_hit(tmp_path, monkeypatch):
    """A readable non-repository cwd holding .claude/CLAUDE.md; chdir into it."""
    cwdhit = tmp_path / "cwdhit"
    (cwdhit / ".claude").mkdir(parents=True)
    (cwdhit / ".claude" / "CLAUDE.md").write_text("cwd\n")
    monkeypatch.chdir(cwdhit)
    return cwdhit / ".claude" / "CLAUDE.md"


def _same(path, expected):
    return path is not None and os.path.realpath(path) == os.path.realpath(expected)


class TestALocationThatCannotBeExaminedResolvesAlikeOnEveryInterpreter:
    @_NEEDS_NON_ROOT
    def test_an_unsearchable_declared_base_ends_resolution(
        self, tmp_path, lock, monkeypatch
    ):
        """The declared project sits under a directory the process cannot
        search. Every resolver stops there instead of carrying on to the cwd's
        file, and says why.

        RED BEFORE THE FIX: 3.9 and 3.13 aborted (display swallowed to None,
        staleness raised); 3.14 returned the cwd's file. Continuing past the
        declared base, instead of stopping, returns the cwd's file everywhere.
        """
        proj, _ = _ancestor_unsearchable(tmp_path, lock)
        _cwd_hit(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        errs, errs2 = [], []

        assert _all_three(errs, errs2) == (None, None, None)
        for recorded in (errs, errs2):
            assert len(recorded) == 1, recorded
            assert "PermissionError" in recorded[0] and str(proj) in recorded[0]

    def test_the_cwd_file_is_reachable_without_the_declaration(
        self, tmp_path, monkeypatch
    ):
        """The control for the arm above: with no declaration each resolver
        finds the cwd's file, so the None above is the stop and not a miss."""
        hit = _cwd_hit(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        errs, errs2 = [], []

        assert all(_same(p, hit) for p in _all_three(errs, errs2))
        assert errs == errs2 == []

    @_NEEDS_NON_ROOT
    def test_an_unexaminable_preferred_file_never_hands_over_to_legacy(
        self, tmp_path, lock, monkeypatch
    ):
        """`.claude/` cannot be searched, the preferred file is behind it, and
        a readable legacy ./CLAUDE.md sits beside it. No resolver returns the
        legacy file, the sync writes nothing and reports RESOLVE_ERROR, and
        the legacy bytes are untouched.

        RED BEFORE THE FIX ON 3.14, which returned the legacy file; and against
        a helper that skips an unexaminable location and tries the next shape,
        on every interpreter.
        """
        from scripts.working_memory import SyncResult, sync_to_claude_md

        proj, legacy = _preferred_unsearchable(tmp_path, lock)
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        errs, errs2 = [], []

        assert _all_three(errs, errs2) == (None, None, None)
        preferred = str(proj / ".claude" / "CLAUDE.md")
        for recorded in (errs, errs2):
            assert any(
                preferred in e and "PermissionError" in e for e in recorded
            ), recorded

        result = sync_to_claude_md({"context": "c"}, None, "id", claude_md_root=tmp_path)

        assert result.reason == SyncResult.RESOLVE_ERROR, result
        assert legacy.read_text() == _LEGACY_TEXT, "the legacy file was written"

    def test_the_preferred_file_readable_is_returned(self, tmp_path, monkeypatch):
        """The matched control: the same layout with `.claude/` readable."""
        proj = tmp_path / "proj"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "CLAUDE.md").write_text("preferred\n")
        (proj / "CLAUDE.md").write_text(_LEGACY_TEXT)
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        errs, errs2 = [], []

        paths = _all_three(errs, errs2)

        assert all(_same(p, proj / ".claude" / "CLAUDE.md") for p in paths), paths
        assert errs == errs2 == []

    def test_a_looped_git_common_dir_falls_through_to_the_cwd(
        self, tmp_path, monkeypatch
    ):
        """git names a common dir that is a symlink loop. Every resolver falls
        through to the cwd's file and records nothing, on every interpreter.

        RED BEFORE THE FIX ON 3.9, where `Path.resolve()` raised RuntimeError
        past the git rung's handler: display aborted to None and staleness
        raised.
        """
        hit = _cwd_hit(tmp_path, monkeypatch)
        loop = tmp_path / "loop"
        loop.mkdir()
        looped = _symlink_loop(loop / "a")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        git = bindir / "git"
        git.write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            f'  *--git-common-dir*) echo "{looped / ".git"}" ;;\n'
            "  *) exit 128 ;;\n"
            "esac\n"
        )
        git.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        errs, errs2 = [], []

        assert all(_same(p, hit) for p in _all_three(errs, errs2))
        assert errs == errs2 == []

    def test_a_git_call_that_fails_moves_on_to_the_cwd(self, tmp_path, monkeypatch):
        """git cannot be run at all. Nothing was examined at the git rungs, so
        each failure is recorded and resolution moves on to the cwd's file:
        the other half of the rule, a failed RUNG continues while a failed
        PROBE stops."""
        hit = _cwd_hit(tmp_path, monkeypatch)
        no_git = tmp_path / "no-git"
        no_git.mkdir()
        monkeypatch.setenv("PATH", str(no_git))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        errs, errs2 = [], []

        assert all(_same(p, hit) for p in _all_three(errs, errs2))
        assert errs and all(e.startswith("git rung: FileNotFoundError") for e in errs), errs
        assert errs2 and all(e.startswith("git rung: FileNotFoundError") for e in errs2), errs2

    @_NEEDS_GIT
    @_NEEDS_NON_ROOT
    def test_the_staleness_writer_never_marks_another_projects_file(
        self, tmp_path, lock, monkeypatch
    ):
        """The declared project's `.claude/` cannot be searched and the cwd is
        a DIFFERENT repository whose CLAUDE.md carries a stale pin. Nothing
        downstream of the staleness resolver refuses a write there, so the
        declared-base stop is the only thing between the two projects.

        RED BEFORE THE FIX ON 3.14, which resolved the other repository and
        marked its pin; and against a resolver that continues past the
        declared base, on every interpreter.
        """
        import staleness
        from staleness import PINNED_STALENESS_DAYS

        proj, _legacy = _preferred_unsearchable(tmp_path, lock)
        other = tmp_path / "other"
        other.mkdir()
        _pgit(other, "init")
        old = (datetime.now() - timedelta(days=PINNED_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")
        other_md = other / "CLAUDE.md"
        other_md.write_text(
            "# Project Memory\n\n## Pinned Context\n\n"
            f"### Old Feature (PR #50, merged {old})\n- details\n\n"
        )
        before = (other_md.read_bytes(), other_md.stat().st_ino)
        monkeypatch.chdir(other)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        assert staleness._resolve_project_claude_md_with_base() == (None, None)
        staleness.check_pinned_staleness()

        assert (other_md.read_bytes(), other_md.stat().st_ino) == before

    @_NEEDS_GIT
    def test_the_other_projects_file_is_reachable_without_the_declaration(
        self, tmp_path, monkeypatch
    ):
        """The control for the arm above: undeclared, the staleness resolver
        reaches the other repository's file, so the arm above is the stop."""
        import staleness

        other = tmp_path / "other"
        other.mkdir()
        _pgit(other, "init")
        other_md = other / "CLAUDE.md"
        other_md.write_text("# Project Memory\n")
        monkeypatch.chdir(other)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        found, _ = staleness._resolve_project_claude_md_with_base()

        assert _same(found, other_md)

    @staticmethod
    def _worktree_with_its_own_claude_md(tmp_path):
        """main/ is a repository with its CLAUDE.md; main/.worktrees/wt is a
        linked worktree whose own .claude/CLAUDE.md exists."""
        main = tmp_path / "main"
        main_md = _init_repo_with_claude_md(main)
        wt = main / ".worktrees" / "wt"
        _pgit(main, "worktree", "add", str(wt), "-b", "feature")
        (wt / ".claude").mkdir()
        (wt / ".claude" / "CLAUDE.md").write_text("worktree\n")
        return main_md, wt

    @_NEEDS_GIT
    @_NEEDS_NON_ROOT
    def test_an_unexaminable_worktree_file_ends_resolution_undeclared(
        self, tmp_path, lock, monkeypatch
    ):
        """No declaration; the cwd is a worktree whose `.claude/` cannot be
        searched, and the main checkout has a readable CLAUDE.md. The display
        resolver, which probes the worktree root, stops there instead of
        writing the main checkout's file past the worktree's own, and records
        the worktree's file.

        The staleness resolver never probes the worktree root -- its rungs are
        the declaration, the git common-dir parent, then the cwd -- so it
        reaches the main checkout's file before it could examine the worktree,
        in this layout and in the readable one alike.

        Against a display resolver that carries on past a location it could
        not examine, it returns the main checkout's file and this goes red.
        """
        import staleness
        from scripts.working_memory import _resolve_display_claude_md_with_base

        main_md, wt = self._worktree_with_its_own_claude_md(tmp_path)
        lock(wt / ".claude")
        monkeypatch.chdir(wt)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        errs = []

        assert _resolve_display_claude_md_with_base(errors=errs) == (None, None)
        # git names the worktree by its real path, so compare real paths.
        wt_real = os.path.realpath(wt)
        wt_md = os.path.join(wt_real, ".claude", "CLAUDE.md")
        assert any(
            "PermissionError" in e and wt_md in e.replace(str(wt), wt_real) for e in errs
        ), errs
        # Staleness is unchanged by the lock: it never examines wt/.claude.
        stale, _ = staleness._resolve_project_claude_md_with_base()
        assert _same(stale, main_md), stale

    @_NEEDS_GIT
    def test_a_readable_worktree_file_is_the_display_answer(self, tmp_path, monkeypatch):
        """The matched control: `.claude/` readable, the display resolver
        returns the worktree's own file, so the stop above is the lock."""
        import staleness
        from scripts.working_memory import _resolve_display_claude_md_with_base

        main_md, wt = self._worktree_with_its_own_claude_md(tmp_path)
        monkeypatch.chdir(wt)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        display, _ = _resolve_display_claude_md_with_base()

        assert _same(display, wt / ".claude" / "CLAUDE.md"), display
        # Staleness never examines wt/.claude, so it answers main here too.
        stale, _ = staleness._resolve_project_claude_md_with_base()
        assert _same(stale, main_md), stale

    @_NEEDS_NON_ROOT
    def test_the_scope_escape_refusal_holds_for_an_unsearchable_declaration(
        self, tmp_path, lock, monkeypatch
    ):
        """The display writer's own guard against landing in another project,
        driven directly with a declaration that cannot be examined. It must
        refuse on every interpreter, whichever way each one reads the
        unsearchable directory."""
        from scripts.working_memory import (
            AmbientSyncRefused,
            _refuse_ambient_sync_on_declared_scope_escape,
        )

        proj, _ = _ancestor_unsearchable(tmp_path, lock)
        hit = _cwd_hit(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        with pytest.raises(AmbientSyncRefused):
            _refuse_ambient_sync_on_declared_scope_escape(
                None, None, resolved_root=hit.parent.parent, claude_md_path=hit
            )

    def test_the_scope_escape_refusal_admits_the_declaration_itself(
        self, tmp_path, monkeypatch
    ):
        """The control: the same call, with the declaration naming the
        directory resolution landed in, admits. So the refusal above is a
        verdict and not a guard that refuses everything."""
        from scripts.working_memory import _refuse_ambient_sync_on_declared_scope_escape

        hit = _cwd_hit(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(hit.parent.parent))

        _refuse_ambient_sync_on_declared_scope_escape(
            None, None, resolved_root=hit.parent.parent, claude_md_path=hit
        )


class TestASymlinkLoopGetsOneVerdictOnEveryInterpreter:
    """The project-scope checks the display writer runs after resolution.
    Each compares resolved paths, and on 3.9 `Path.resolve()` raised
    RuntimeError on a symlink loop where 3.13 and 3.14 return the path with the
    looping component unresolved. `os.path.realpath` does the latter on every
    interpreter, so a loop gets one verdict everywhere.

    RED BEFORE THE FIX ON 3.9 ONLY, where each of these crashed.
    """

    @staticmethod
    def _repo_with_a_looped_dir(tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _pgit(repo, "init")
        (repo / "CLAUDE.md").write_text("x\n")
        return repo, _symlink_loop(repo / "loopdir")

    @_NEEDS_GIT
    def test_a_looped_declaration_inside_the_repository_is_admitted(self, tmp_path):
        """The declaration is a looped directory inside the repository that
        resolution landed in, so it is the same project: ADMIT."""
        from shared.project_scope import stays_in_declared_project

        repo, looped = self._repo_with_a_looped_dir(tmp_path)
        assert stays_in_declared_project(looped, repo, repo / "CLAUDE.md") is True

    @_NEEDS_GIT
    def test_the_repository_itself_is_admitted(self, tmp_path):
        """The control: declared == resolved, ADMIT on every interpreter."""
        from shared.project_scope import stays_in_declared_project

        repo, _looped = self._repo_with_a_looped_dir(tmp_path)
        assert stays_in_declared_project(repo, repo, repo / "CLAUDE.md") is True

    @_NEEDS_GIT
    def test_a_looped_base_is_not_the_main_repository(self, tmp_path):
        from shared.project_scope import same_repository

        repo, looped = self._repo_with_a_looped_dir(tmp_path)
        assert same_repository(repo, looped) is False

    def test_a_looped_git_answer_is_the_unresolved_path(self, tmp_path, monkeypatch):
        """git names a symlink loop; the answer is that path with the loop
        left unresolved, on every interpreter."""
        from shared.project_scope import _rev_parse_path

        _symlink_loop(tmp_path / "loop")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        git = bindir / "git"
        git.write_text(f'#!/bin/sh\necho "{tmp_path / "loop"}"\n')
        git.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")

        answer = _rev_parse_path(tmp_path, "--git-common-dir")

        assert answer == Path(os.path.realpath(tmp_path)) / "loop"

    def test_a_target_under_a_looped_project_dir_is_inside_it(
        self, tmp_path, monkeypatch
    ):
        from scripts.working_memory import _target_is_inside_the_declared_project_dir

        looped = _symlink_loop(tmp_path / "declared")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(looped))
        assert _target_is_inside_the_declared_project_dir(looped / "CLAUDE.md") is True

    def test_a_target_elsewhere_is_not_inside_a_looped_project_dir(
        self, tmp_path, monkeypatch
    ):
        """The control: a target outside the looped declaration is outside."""
        from scripts.working_memory import _target_is_inside_the_declared_project_dir

        looped = _symlink_loop(tmp_path / "declared")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(looped))
        target = tmp_path / "other" / "CLAUDE.md"
        assert _target_is_inside_the_declared_project_dir(target) is False
