"""Every `.resolve()` in project_scope.py is caught for a symlink loop.

Location: pact-plugin/tests/test_project_scope_resolve_census.py

`Path.resolve()` raises RuntimeError on a symlink loop before Python 3.13
(OSError from 3.13). project_scope classifies directories for writers, so a
raise there would crash a hook instead of answering "unknown". This census
reads the module's syntax tree: each `.resolve()` call must sit in the body
of a `try` that has a handler naming RuntimeError. A new call outside one
turns this red; the loop itself is exercised on 3.13 by
tests/test_scope_symlink_loop.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parent.parent / "hooks" / "shared" / "project_scope.py"
)


def _names(node) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        return {name for elt in node.elts for name in _names(elt)}
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def _guarded_lines(tree) -> set[int]:
    """Lines inside a try body whose handlers name RuntimeError."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
            "RuntimeError" in _names(handler.type) for handler in node.handlers
        ):
            for stmt in node.body:
                lines.update(range(stmt.lineno, stmt.end_lineno + 1))
    return lines


def _resolve_calls(tree) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resolve"
        and not node.args
    ]


def test_every_resolve_call_is_caught_for_a_symlink_loop():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = _resolve_calls(tree)
    # Control: the census sees the calls it is about (nine at this writing,
    # after the containment guard's home check was removed).
    assert len(calls) >= 9, calls

    unguarded = sorted(set(calls) - _guarded_lines(tree))

    assert not unguarded, f"project_scope.py lines with an uncaught .resolve(): {unguarded}"


def test_the_census_flags_a_resolve_outside_a_runtime_error_handler():
    """The detector against known shapes: an OSError-only handler and no
    handler are flagged; a tuple naming RuntimeError is not."""
    tree = ast.parse(
        "try:\n    a.resolve()\nexcept (OSError, RuntimeError):\n    pass\n"
        "try:\n    b.resolve()\nexcept OSError:\n    pass\n"
        "c.resolve()\n"
    )

    assert sorted(set(_resolve_calls(tree)) - _guarded_lines(tree)) == [6, 9]
