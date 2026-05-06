"""AST-based schema compliance tests for hook output.

Regression guard for #658: every `hookSpecificOutput` dict literal in any
`pact-plugin/hooks/*.py` file MUST contain the key `hookEventName`. The
Claude Code harness silently rejects hook JSON missing this field, causing
a fail-open condition for permission gates.

Strategy:
  - Use Python's `ast` module to walk every hook source file
  - Locate every dict literal that is the value of a `hookSpecificOutput` key
  - Assert each such inner dict contains a `hookEventName` constant key

Counter-test discipline:
  A second test synthesizes a known-bad source string with a missing
  `hookEventName`, runs the same detection logic, and asserts the violation
  is detected. This proves the AST-walk's negative case actually fires.

Coverage assertion:
  A sanity check counts the dicts found across live source and asserts
  the count is >= 11. This proves the walk scans real source rather than
  silently iterating an empty fixture set. As of #658 fix, the count is
  approximately 18 (counted via grep on live source minus docstring/comment
  references).
"""

from __future__ import annotations

import ast
import pathlib
import textwrap

HOOKS_DIR = pathlib.Path(__file__).parent.parent / "hooks"


def _find_hook_specific_output_dicts(tree: ast.AST):
    """Yield every inner dict literal that is the value of a `hookSpecificOutput` key."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "hookSpecificOutput"
                and isinstance(value, ast.Dict)
            ):
                yield value


def _inner_dict_violations(tree: ast.AST, filename: str):
    """Return list of violation strings for a parsed source tree."""
    violations = []
    for inner in _find_hook_specific_output_dicts(tree):
        inner_keys = [k.value for k in inner.keys if isinstance(k, ast.Constant)]
        if "hookEventName" not in inner_keys:
            violations.append(
                f"{filename} line {inner.lineno}: hookSpecificOutput missing hookEventName"
            )
    return violations


def test_every_hookSpecificOutput_dict_includes_hookEventName():
    """Every hookSpecificOutput literal in hooks/*.py must include hookEventName.

    Regression guard for #658: missing hookEventName causes the harness
    to silently reject the JSON and fail open on permission gates.
    """
    violations = []
    for hook_file in sorted(HOOKS_DIR.glob("*.py")):
        tree = ast.parse(hook_file.read_text())
        violations.extend(_inner_dict_violations(tree, hook_file.name))
    assert not violations, (
        "hookEventName missing at:\n" + "\n".join(violations)
    )


def test_ast_walk_detects_missing_hookEventName_in_synthesized_source():
    """Counter-test: prove the detection logic fires on known-bad input.

    Synthesize a hook-shaped source string with a hookSpecificOutput dict
    that omits hookEventName, parse it, and assert a violation is reported.
    Without this test, the positive test could silently pass against an
    empty fixture set or a broken AST walk.
    """
    bad_source = textwrap.dedent(
        '''
        import json
        def main():
            output = {
                "hookSpecificOutput": {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "missing hookEventName here",
                }
            }
            print(json.dumps(output))
        '''
    )
    tree = ast.parse(bad_source)
    violations = _inner_dict_violations(tree, "synthetic_bad.py")
    assert len(violations) == 1, (
        f"Expected exactly 1 violation in synthesized bad source, got {len(violations)}: {violations}"
    )
    assert "hookEventName" in violations[0]


def test_ast_walk_passes_on_synthesized_compliant_source():
    """Counter-test (positive): prove the detection logic does NOT false-positive.

    Synthesize a compliant hookSpecificOutput dict and assert no violations
    are reported. Pairs with the bad-source counter-test to fence both
    sides of the detection boundary.
    """
    good_source = textwrap.dedent(
        '''
        import json
        def main():
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "compliant",
                }
            }
            print(json.dumps(output))
        '''
    )
    tree = ast.parse(good_source)
    violations = _inner_dict_violations(tree, "synthetic_good.py")
    assert violations == [], (
        f"Expected no violations in compliant source, got: {violations}"
    )


def test_ast_walk_finds_minimum_expected_dict_count():
    """Sanity check: assert the AST walk actually scans live source.

    Counts every hookSpecificOutput dict literal across all hook files
    and asserts >= 11. This guards against a bug where the walk silently
    iterates an empty set (e.g., wrong HOOKS_DIR, glob pattern miss),
    which would make the positive compliance test vacuously pass.

    Live count as of PR #660 (#658 fix): ~18 dicts. Threshold of 11
    leaves headroom for hooks being removed without forcing test churn.
    """
    total = 0
    for hook_file in sorted(HOOKS_DIR.glob("*.py")):
        tree = ast.parse(hook_file.read_text())
        total += sum(1 for _ in _find_hook_specific_output_dicts(tree))
    assert total >= 11, (
        f"Expected >= 11 hookSpecificOutput dict literals across hooks/, found {total}. "
        "Either hooks were removed (lower the threshold) or the AST walk is misconfigured."
    )
