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
    worktree_guard.py:139-144. This wrapper mirrors that exact expression.
    Because the inline probe is a bool, drift here is especially easy to
    miss without this parity test.
    """
    # Mirror worktree_guard.py lines 139-144 exactly
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
