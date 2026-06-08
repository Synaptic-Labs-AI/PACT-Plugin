"""
Standing AST guard against hardcoded-state-path drift (#926).

#926 migrated ~40 hardcoded `Path.home() / ".claude" / <state-subdir>` sites to
the central `shared.paths.get_claude_config_dir()` resolver. The behavioral
suites (test_paths.py L1, test_config_dir_dispatch_integration.py +
test_config_dir_comprehensive.py L2) prove the MIGRATED sites resolve correctly.
This file guards the COMPLETENESS claim itself: that no `Path.home()/".claude"`
state-path construction reappears (a future re-add or a regressed migration would
silently re-introduce the exact fail-silent/drift class #926 exists to kill).

The guarantee otherwise rests only on AttributeError-fail-loud (for the removed
constants) + a green suite + the manual sweep — none of which catches a NEW
hardcoded site. This standing guard does.

NON-VACUITY (mirrors the test_session_registry_trust_partition AST-guard
discipline): the detector is exercised against synthetic source that DOES and
does NOT contain the construction, so the negative leg cannot be a vacuous
always-pass. A guard that can't fail is the inert class we are fighting.

Allowlist — the ONE legitimate residual `Path.home() / ".claude"` LITERAL
(verified by hand + by the detector run at authoring time):
  - shared/symlinks.py   — the C6 dual-location protocols-symlink HOME-side root
                           (the link is created in BOTH ~/.claude and $CONFIG when
                           they differ; the home-side is intentional + answer-immune).
(shared/session_registry.py's inline `_config_root()` env-UNSET fallback and
shared/paths.py's fallback are BOTH param-style `home / ".claude"` — NOT the
literal `Path.home() / ".claude"` — so they do NOT match the detector and need
no allowlist entry. session_registry was a literal until the home-once refactor
aligned it with paths.py's canonical structure; the allowlist-rot test below
prunes-by-failing if a residual ever vanishes, which is what dropped it here.)
"""
import ast
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"

# (basename, exact unparsed construction) pairs that are legitimate residuals.
ALLOWLIST = {
    ("symlinks.py", "Path.home() / '.claude'"),
}


def _hardcoded_home_claude(tree: ast.AST, basename: str) -> list[tuple[str, int, str]]:
    """Return (basename, lineno, unparsed) for every `Path.home() / ".claude" ...`
    division construction in `tree`.

    Walks Div BinOps and matches on the normalized unparse prefix
    `Path.home() / '.claude'`, so it catches BOTH the bare 2-segment root AND any
    3+-segment state path (`.../"tasks"`, etc.), and is multi-line-robust (the AST
    normalizes a line-wrapped construction into one expression — a same-line grep
    would miss the `session_state.py:511-517`-style multi-line form). Nested
    BinOps at one site are deduped to the outermost (longest) per (file, lineno).
    """
    found: dict[tuple[str, int], str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            unparsed = ast.unparse(node).replace('"', "'")
            if unparsed.startswith("Path.home() / '.claude'"):
                key = (basename, node.lineno)
                if key not in found or len(unparsed) > len(found[key]):
                    found[key] = unparsed
    return [(b, ln, u) for (b, ln), u in found.items()]


def _scan_hooks() -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for path in sorted(HOOKS_DIR.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(HOOKS_DIR))
        violations += [(rel, ln, u) for (_, ln, u) in _hardcoded_home_claude(tree, path.name)]
    return violations


def _unallowlisted(violations: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    out = []
    for rel, ln, unparsed in violations:
        basename = Path(rel).name
        if (basename, unparsed) not in ALLOWLIST:
            out.append((rel, ln, unparsed))
    return out


class TestNoHardcodedStatePathDrift:
    def test_no_unallowlisted_home_claude_sites(self):
        """Every `Path.home()/".claude"` construction in hooks/**/*.py must be one
        of the two known-legitimate residuals. A future re-added/regressed
        hardcoded state path is NOT in the allowlist -> this FAILS loudly."""
        offenders = _unallowlisted(_scan_hooks())
        assert offenders == [], (
            "Hardcoded Path.home()/'.claude' state-path site(s) outside the "
            "allowlist — route through shared.paths.get_claude_config_dir() "
            f"instead:\n" + "\n".join(f"  {f}:{ln}: {u}" for f, ln, u in offenders)
        )

    def test_both_allowlisted_residuals_still_present(self):
        """Guard the allowlist against rot: if a residual is itself migrated away,
        prune the allowlist (so it cannot mask a future real offender at that
        basename). Keeps the allowlist honest."""
        scanned = {(Path(f).name, u) for f, _, u in _scan_hooks()}
        missing = ALLOWLIST - scanned
        assert missing == set(), (
            f"Allowlisted residual(s) no longer present (prune the allowlist): {missing}"
        )


class TestDetectorNonVacuity:
    """The detector + allowlist gate must be able to FAIL. Mirror the
    trust_partition synthetic-source non-vacuity discipline."""

    @pytest.mark.parametrize("src", [
        'x = Path.home() / ".claude" / "tasks" / team\n',
        'p = Path.home() / ".claude" / "pact-sessions"\n',
        # multi-line form a same-line grep would miss:
        'q = (\n    Path.home()\n    / ".claude"\n    / "teams"\n    / name\n)\n',
    ])
    def test_detector_fires_on_synthetic_hardcoded_state_path(self, src):
        hits = _hardcoded_home_claude(ast.parse(src), "synthetic_offender.py")
        assert hits, f"detector FAILED to flag a hardcoded state path: {src!r}"
        # And the full gate would reject it (not in allowlist):
        assert _unallowlisted([(h[0], h[1], h[2]) for h in
                               [("synthetic_offender.py", ln, u) for _, ln, u in hits]]), \
            "gate FAILED to reject an unallowlisted synthetic offender"

    @pytest.mark.parametrize("src", [
        'x = get_claude_config_dir() / "tasks" / team\n',   # the CORRECT post-#926 idiom
        'y = Path.home() / ".ssh" / "config"\n',            # unrelated home path
        'z = base / ".claude" / "tasks"\n',                 # not rooted at Path.home()
    ])
    def test_detector_ignores_non_offending_constructions(self, src):
        assert _hardcoded_home_claude(ast.parse(src), "ok.py") == [], (
            f"detector wrongly flagged a non-offending construction: {src!r}"
        )
