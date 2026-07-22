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
import sys
from pathlib import Path
from typing import Optional

import pytest

# conftest adds `hooks/` and `skills/pact-memory/` to sys.path. We also need
# `skills/pact-memory/scripts/` on sys.path so `working_memory` imports as a
# bare module (mirrors test_staleness.py line 31). memory_api.py uses
# `from .database import ...` so it MUST be loaded via the `scripts.` package
# path -- loading it standalone with importlib breaks its relative imports.
_SCRIPTS_DIR = Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


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
    from working_memory import _get_claude_md_path

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
        from working_memory import (
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
        from working_memory import (
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
        from working_memory import (
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
