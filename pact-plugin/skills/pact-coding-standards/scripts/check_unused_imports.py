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
      suppression work for parenthesized multi-line imports). Codes are
      matched as exact word-bounded tokens: a noqa listing only other codes
      does not suppress, `F401x`/`F4011` are different codes, and a code
      list with no recognizable code suppresses nothing — only a truly bare
      `# noqa` blanket-suppresses.
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

# The noqa marker itself; a following colon opens a code list. Codes are
# read from the list region only (up to any second `#`) as exact
# word-bounded letter+digit tokens, so trailing prose (`noqa: F401 some
# reason` — the example omits its hash so linters never parse it as a
# real directive) cannot bleed into the code list, and a letter-suffixed
# non-code (`F401x`) or a code mentioned in a second `#` reason comment
# cannot suppress.
_NOQA_RE = re.compile(r"#\s*noqa(?P<sep>:)?", re.IGNORECASE)
_NOQA_CODE_TOKEN_RE = re.compile(r"\b[A-Za-z]+[0-9]+\b")

_VALID_STRICTNESS = ("strict", "advisory")


class Finding(NamedTuple):
    """One unused-import finding: the statement's first line and the name
    as written in the source (`x`, `a.b.c`, or `x as y`)."""

    lineno: int
    name: str


def _line_has_noqa_f401(line: str) -> bool:
    """True when the physical line carries a noqa that suppresses F401:
    either a bare `# noqa` or a code list containing the exact token F401."""
    m = _NOQA_RE.search(line)
    if not m:
        return False
    if m.group("sep") is None:
        return True  # bare noqa suppresses everything on the line
    # A colon opens a code list: suppress only on an exact word-bounded
    # F401 token inside the list region (before any second `#`, so a
    # reason comment mentioning F401 does not count). A list with no
    # recognizable code token (e.g. `noqa: banana` — hash omitted so
    # linters don't parse this example) suppresses nothing.
    codes_region = line[m.end():].split("#", 1)[0]
    return any(
        token.upper() == "F401"
        for token in _NOQA_CODE_TOKEN_RE.findall(codes_region)
    )


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` tests."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _collect_all_names(tree: ast.Module) -> set[str]:
    """Names declared in `__all__`: Assign or AugAssign of string constants,
    plus `__all__.extend([...])` calls (the other common declaration idiom)."""
    exported: set[str] = set()
    for node in ast.walk(tree):
        values: list[ast.expr] = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                values = [node.value]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "extend"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "__all__"
        ):
            values = list(node.args)
        for value in values:
            for elt in ast.walk(value):
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

    # `try/except*` (ast.TryStar, PEP 654) is try-scoped exactly like plain
    # `try` — the alias is inert on interpreters whose ast lacks TryStar.
    visit_TryStar = visit_Try

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
