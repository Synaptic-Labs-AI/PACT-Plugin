#!/usr/bin/env python3
"""
Location: pact-plugin/skills/pact-coding-standards/scripts/check_unused_imports.py
Summary: Stdlib-only AST predicate that reports unused imports in Python files.
Used by: lint-check.sh --files mode (advisory tier of the import-hygiene ladder)
         and the dev-repo test suite's import-hygiene gate (strict tier). Both
         call sites must declare try-scope strictness explicitly — see below.

Checks the F401 class only (imported name never used in the module). It does
NOT attempt undefined-name (F821) analysis — that requires real scope analysis
and is served by the ruff/pyflakes rungs of the ladder when available.

Try-scope strictness (REQUIRED-EXPLICIT, no default anywhere in the chain):
    strict   — imports inside try/except blocks are checked like any other.
    advisory — imports inside try/except blocks are skipped. Try-scoped
               imports are commonly optional-dependency probes whose "unused"
               appearance is intentional; the friction-sensitive consumer
               tier opts out of flagging them.
The strictness parameter has NO default: the pure predicate raises TypeError
when it is omitted and ValueError on an unknown value, and the CLI flag is
argparse-required. This is deliberate — the fail-safe direction genuinely
differs per call site, and a default would let one caller silently inherit
the other's choice. Do not add a default or a convenience wrapper that
supplies one.

Suppression and carve-outs:
    * `# noqa` / `# noqa: F401` on the import statement's FIRST physical
      line suppresses findings for that whole statement (this is what makes
      suppression work for parenthesized multi-line imports). A noqa listing
      only other codes does not suppress.
    * `from __future__ import ...` is never flagged.
    * Imports inside an `if TYPE_CHECKING:` block are never flagged (they
      serve string annotations the AST usage walk cannot see). The carve-out
      spans the whole `if` statement including any `else:` arm, so a runtime
      import there is exempted too — a known trade-off (the pattern is
      vanishingly rare and the behavior is pinned by a suite test).
    * Names listed in `__all__` count as used (re-export convention).
    * Star imports are ignored entirely.
    * `import a.b.c` binds the root name `a`; usage of `a` marks it used.

Usage:
    python3 check_unused_imports.py --try-scope {strict,advisory} FILE [FILE ...]

Output: one finding per line on stdout, `path:line: unused import X`.
Files are read via `tokenize.open`, which honors PEP 263 coding cookies and
BOMs — legal non-UTF8 source is decoded per its declared encoding and checked
like any other file. A file that cannot be parsed produces `path:line: syntax
error (unable to check imports): <msg>` on stdout, and a file that cannot be
read or decoded produces `path:0: unable to read file (<msg>)` — a check
failure is loud, never a skip, and never aborts the rest of the batch.
Exit codes: 0 = no findings, 1 = findings (or unreadable/unparsable input).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tokenize
from typing import NamedTuple

# Codes are matched as letter+digit tokens (e.g. F401) separated by commas
# and/or whitespace, so trailing prose after the last code — `# noqa: F401
# some reason` without a comma or second `#` — cannot bleed into the code
# list and silently defeat the suppression.
_NOQA_RE = re.compile(
    r"#\s*noqa(?::\s*(?P<codes>[A-Za-z]+[0-9]+(?:[,\s]+[A-Za-z]+[0-9]+)*))?",
    re.IGNORECASE,
)

_VALID_STRICTNESS = ("strict", "advisory")


class Finding(NamedTuple):
    """One unused-import finding: the statement's first line and the name
    as written in the source (`x`, `a.b.c`, or `x as y`)."""

    lineno: int
    name: str


def _line_has_noqa_f401(line: str) -> bool:
    """True when the physical line carries a noqa that suppresses F401:
    either a bare `# noqa` or a code list containing F401."""
    m = _NOQA_RE.search(line)
    if not m:
        return False
    codes = m.group("codes")
    if codes is None:
        # Bare `# noqa` — or a code list that doesn't parse as code tokens
        # (e.g. `# noqa: banana`) — suppresses everything on the line,
        # matching flake8's blanket-noqa semantics.
        return True
    return "F401" in {c.upper() for c in re.split(r"[,\s]+", codes) if c}


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` tests."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _collect_all_names(tree: ast.Module) -> set[str]:
    """Names declared in `__all__` (Assign or AugAssign of string constants)."""
    exported: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        for elt in ast.walk(node.value):
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                exported.add(elt.value)
    return exported


class _ScopedImportCollector(ast.NodeVisitor):
    """Collects import statements, tagging each with whether it sits inside a
    try/except block or an `if TYPE_CHECKING:` block."""

    def __init__(self) -> None:
        # (node, in_try, in_type_checking)
        self.imports: list[tuple[ast.stmt, bool, bool]] = []
        self._try_depth = 0
        self._type_checking_depth = 0

    def visit_Try(self, node: ast.Try) -> None:
        self._try_depth += 1
        self.generic_visit(node)
        self._try_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            self._type_checking_depth += 1
            self.generic_visit(node)
            self._type_checking_depth -= 1
        else:
            self.generic_visit(node)

    def _record(self, node: ast.stmt) -> None:
        self.imports.append(
            (node, self._try_depth > 0, self._type_checking_depth > 0)
        )

    def visit_Import(self, node: ast.Import) -> None:
        self._record(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._record(node)


def find_unused_imports(source: str, *, try_scope: str) -> list[Finding]:
    """Pure predicate: return unused-import findings for one module's source.

    `try_scope` is REQUIRED-EXPLICIT ("strict" | "advisory") — see the module
    docstring. Raises ValueError on an unknown value and propagates
    SyntaxError from unparsable source (the CLI layer turns that into a loud
    stdout finding; callers embedding this function decide for themselves).
    """
    if try_scope not in _VALID_STRICTNESS:
        raise ValueError(
            f"try_scope must be one of {_VALID_STRICTNESS!r}, got {try_scope!r}"
        )

    tree = ast.parse(source)
    lines = source.splitlines()

    collector = _ScopedImportCollector()
    collector.visit(tree)

    # Bound name -> (first line of the statement, display name as written).
    # Later bindings of the same name overwrite earlier ones, matching runtime.
    imported: dict[str, tuple[int, str]] = {}
    for node, in_try, in_type_checking in collector.imports:
        if in_type_checking:
            continue  # carve-out: serves string annotations
        if in_try and try_scope == "advisory":
            continue  # carve-out: optional-dependency probes, consumer tier
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue  # carve-out: compiler directive, never "used"
        if _line_has_noqa_f401(lines[node.lineno - 1]):
            continue  # suppression: first physical line of the statement
        for alias in node.names:
            if alias.name == "*":
                continue  # carve-out: star imports are not tracked
            if isinstance(node, ast.Import):
                bound = (alias.asname or alias.name).split(".")[0]
            else:
                bound = alias.asname or alias.name
            display = (
                f"{alias.name} as {alias.asname}" if alias.asname else alias.name
            )
            imported[bound] = (node.lineno, display)

    used: set[str] = set()

    class _UsageVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            used.add(node.id)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            root: ast.expr = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
            self.generic_visit(node)

    _UsageVisitor().visit(tree)
    used |= _collect_all_names(tree)

    findings = [
        Finding(lineno, display)
        for bound, (lineno, display) in imported.items()
        if bound not in used
    ]
    return sorted(findings)


def check_paths(paths: list[str], *, try_scope: str) -> list[str]:
    """Run the predicate over files; return formatted finding lines.

    Unreadable, undecodable, or unparsable files produce a loud finding-format
    line rather than being skipped (a file the checker cannot verify is a
    failure, not a pass), and never abort the rest of the batch. Non-`.py`
    paths are refused the same way — the contract is Python-file paths only.
    """
    out: list[str] = []
    for path in paths:
        if not path.endswith(".py"):
            out.append(f"{path}:0: not a .py file (unable to check imports)")
            continue
        try:
            # tokenize.open honors PEP 263 coding cookies and BOMs, so legal
            # non-UTF8 source is read per its declared encoding.
            with tokenize.open(path) as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError, SyntaxError, LookupError) as exc:
            # OSError: unreadable path. UnicodeDecodeError: bytes that do not
            # decode under the detected encoding. SyntaxError: a bad/unknown
            # coding cookie (raised by the encoding detection itself).
            # LookupError: a cookie naming a NON-TEXT codec (e.g. base64) —
            # detection resolves it but the text-mode open refuses it. All
            # mean "cannot verify this file" — loud line, batch continues.
            out.append(f"{path}:0: unable to read file ({exc})")
            continue
        try:
            findings = find_unused_imports(source, try_scope=try_scope)
        except SyntaxError as exc:
            out.append(
                f"{path}:{exc.lineno or 0}: syntax error "
                f"(unable to check imports): {exc.msg}"
            )
            continue
        out.extend(
            f"{path}:{lineno}: unused import {name}" for lineno, name in findings
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report unused imports (F401 class) in Python files."
    )
    # required=True (not a default) is the CLI face of the no-default design.
    parser.add_argument(
        "--try-scope",
        choices=_VALID_STRICTNESS,
        required=True,
        dest="try_scope",
        help="strict: check try/except-scoped imports too; "
        "advisory: skip them (optional-dependency probes)",
    )
    parser.add_argument("paths", nargs="+", metavar="FILE", help=".py files to check")
    args = parser.parse_args(argv)

    lines = check_paths(args.paths, try_scope=args.try_scope)
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
