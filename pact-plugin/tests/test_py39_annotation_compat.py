"""
Location: pact-plugin/tests/test_py39_annotation_compat.py
Summary: Static AST guard keeping pact-plugin/hooks/ importable under
         Python 3.9 (GUI-launched macOS sessions run hooks on
         /usr/bin/python3 = 3.9.x). Three rules:
         R1 every scanned .py carries `from __future__ import annotations`;
         R2 no runtime-position type unions (the future import cannot
            defer those);
         R3 no typing.get_type_hints / typing.cast anywhere in scanned
            roots — their absence is what makes universal annotation
            stringification safe on 3.9.
         Pure static analysis: runs identically under any CI interpreter.
Used by: pact-plugin test suite (standing merge gate).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent

# Extension point: add adjacent bare-python3 surfaces here (one line each),
# e.g. PLUGIN_ROOT / "skills" / "pact-memory" / "scripts",
#      PLUGIN_ROOT / "scripts".
SCANNED_ROOTS: tuple[Path, ...] = (PLUGIN_ROOT / "hooks",)

# Integer-flag namespaces: `a.X | a.Y` rooted here is bitwise-OR on int
# constants, valid on every Python 3.x — never a type union.
FLAG_MODULES = frozenset({"os", "re", "fcntl", "stat", "mmap", "socket", "errno", "signal", "select"})

# Names that mark a BitOr operand as type-like in runtime position.
TYPE_NAMES = frozenset({
    "str", "int", "float", "bool", "bytes", "bytearray", "complex",
    "dict", "list", "tuple", "set", "frozenset", "type", "object",
    "Path", "Optional", "Union", "Any", "Callable", "Iterable",
    "Iterator", "Sequence", "Mapping", "MutableMapping",
})

_CLASS_LIKE = re.compile(r"^[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*$")  # CamelCase, NOT ALL_CAPS

ANNOTATION_EVAL_NAMES = frozenset({"cast", "get_type_hints"})

FUTURE_IMPORT_LINE = "from __future__ import annotations"


@dataclass(frozen=True)
class Violation:
    relpath: str
    lineno: int
    rule: str      # "missing_future_import" | "runtime_type_union" | "annotation_eval_api"
    detail: str


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def iter_python_files(roots: Iterable[Path] = SCANNED_ROOTS) -> Iterator[Path]:
    """Yield every .py file under the given roots, sorted for stable ids."""
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            yield path


def _relpath(path: Path) -> str:
    try:
        return path.relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------

def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _annotation_nodes(tree: ast.AST) -> Iterator[ast.expr]:
    """Yield every annotation expression in the module.

    Carriers: FunctionDef/AsyncFunctionDef argument annotations (positional-only,
    positional, keyword-only, *args, **kwargs), return annotations, and
    AnnAssign annotations. Lambdas cannot carry annotations.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            every_arg = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            every_arg.append(args.vararg)
            every_arg.append(args.kwarg)
            for arg in every_arg:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _annotation_subtree_ids(tree: ast.AST) -> set:
    """Object ids of every AST node living inside an annotation expression."""
    ids = set()
    for annotation in _annotation_nodes(tree):
        for sub in ast.walk(annotation):
            ids.add(id(sub))
    return ids


def _annotation_union_lines(tree: ast.AST) -> List[int]:
    """Line numbers of annotation-position `X | Y` unions (3.9 crash sites
    when the future import is absent)."""
    lines = []
    for annotation in _annotation_nodes(tree):
        for sub in ast.walk(annotation):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                lines.append(sub.lineno)
    return sorted(set(lines))


def _flatten_bitor(node: ast.BinOp) -> List[ast.expr]:
    """Flatten a (possibly nested) `a | b | c` chain into its operand list."""
    operands: List[ast.expr] = []
    stack = [node.left, node.right]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
            stack.append(current.left)
            stack.append(current.right)
        else:
            operands.append(current)
    return operands


def _root_and_terminal(node: ast.expr) -> Tuple[Optional[str], Optional[str]]:
    """For Name/Attribute operands, return (root identifier, terminal identifier).

    `os.O_CREAT` -> ("os", "O_CREAT"); `Path` -> ("Path", "Path");
    `pkg.mod.Cls` -> ("pkg", "Cls"). Anything else -> (None, None).
    """
    if isinstance(node, ast.Name):
        return node.id, node.id
    if isinstance(node, ast.Attribute):
        terminal = node.attr
        current: ast.expr = node.value
        while isinstance(current, ast.Attribute):
            current = current.value
        if isinstance(current, ast.Name):
            return current.id, terminal
        return None, terminal
    return None, None


def _operand_type_likeness(node: ast.expr) -> Optional[str]:
    """Return a human-readable reason if the operand marks a type union,
    else None. Whitelist-first: FLAG_MODULES-rooted operands never flag."""
    if isinstance(node, ast.Constant) and node.value is None:
        return "operand is the None constant"
    root, terminal = _root_and_terminal(node)
    if terminal is None:
        return None
    if root in FLAG_MODULES:
        return None
    if terminal in TYPE_NAMES:
        return "operand {!r} is in TYPE_NAMES".format(terminal)
    if _CLASS_LIKE.match(terminal):
        return "operand {!r} is class-like (CamelCase)".format(terminal)
    return None


# ---------------------------------------------------------------------------
# Detectors — pure (source -> list[Violation]) so the non-vacuity tests can
# drive them with synthetic strings.
# ---------------------------------------------------------------------------

def check_future_import(source: str, relpath: str) -> List[Violation]:
    """R1: the first non-docstring statement must be the annotations future
    import. Position is checked, not just presence — a future import buried
    below other imports is a 3.9 SyntaxError. On failure the detail cites
    every annotation-position union in the file (the lines that would crash
    Python 3.9)."""
    tree = ast.parse(source)
    body = tree.body

    if not body:
        return [Violation(
            relpath=relpath, lineno=1, rule="missing_future_import",
            detail="empty module: add the import as the sole line",
        )]

    first_index = 1 if _is_docstring_stmt(body[0]) else 0
    if first_index < len(body):
        first_stmt = body[first_index]
        if (
            isinstance(first_stmt, ast.ImportFrom)
            and first_stmt.module == "__future__"
            and any(alias.name == "annotations" for alias in first_stmt.names)
        ):
            return []

    union_lines = _annotation_union_lines(tree)
    enrichment = (
        "; annotation-position unions that would crash 3.9 at lines: {}".format(
            ", ".join(str(line) for line in union_lines)
        )
        if union_lines else ""
    )
    return [Violation(
        relpath=relpath, lineno=1, rule="missing_future_import",
        detail=(
            "`{}` must be the first statement after the module docstring{}"
            .format(FUTURE_IMPORT_LINE, enrichment)
        ),
    )]


def check_runtime_type_unions(source: str, relpath: str) -> List[Violation]:
    """R2: flag `X | Y` outside annotation positions when an operand is the
    None constant or a type-like name — the future import cannot defer those,
    so they crash Python 3.9 at runtime. Integer-flag ORs (os.O_CREAT |
    os.O_WRONLY, re.IGNORECASE | re.DOTALL, ...) pass via FLAG_MODULES and
    the ALL-CAPS exclusion in _CLASS_LIKE."""
    tree = ast.parse(source)
    annotation_ids = _annotation_subtree_ids(tree)
    runtime_bitors = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.BitOr)
        and id(node) not in annotation_ids
    ]

    # Report each chain once: skip BitOr nodes that are operands of another
    # runtime BitOr (the outermost node sees the flattened chain).
    inner_ids = set()
    for node in runtime_bitors:
        for side in (node.left, node.right):
            if isinstance(side, ast.BinOp) and isinstance(side.op, ast.BitOr):
                inner_ids.add(id(side))

    violations = []
    for node in runtime_bitors:
        if id(node) in inner_ids:
            continue
        for operand in _flatten_bitor(node):
            reason = _operand_type_likeness(operand)
            if reason is not None:
                violations.append(Violation(
                    relpath=relpath, lineno=node.lineno, rule="runtime_type_union",
                    detail=(
                        "runtime `|` looks like a type union ({}); use "
                        "typing.Union/typing.Optional or a tuple of types here, "
                        "or extend FLAG_MODULES if this is an integer-flag "
                        "namespace".format(reason)
                    ),
                ))
                break
    return violations


def check_annotation_eval_apis(source: str, relpath: str) -> List[Violation]:
    """R3: typing.cast / typing.get_type_hints re-evaluate stringified
    annotations at runtime and would silently re-introduce the 3.9 crash
    class. Their count in the scanned roots is zero and must stay zero.
    Detects both `from typing import cast` (any alias) and attribute access
    through `import typing` / `import typing as T` bindings."""
    tree = ast.parse(source)
    typing_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    typing_aliases.add(alias.asname or "typing")

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name in ANNOTATION_EVAL_NAMES:
                    violations.append(Violation(
                        relpath=relpath, lineno=node.lineno, rule="annotation_eval_api",
                        detail="`from typing import {}` re-evaluates stringified "
                               "annotations at runtime".format(alias.name),
                    ))
        elif isinstance(node, ast.Attribute) and node.attr in ANNOTATION_EVAL_NAMES:
            if isinstance(node.value, ast.Name) and node.value.id in typing_aliases:
                violations.append(Violation(
                    relpath=relpath, lineno=node.lineno, rule="annotation_eval_api",
                    detail="`{}.{}` re-evaluates stringified annotations at "
                           "runtime".format(node.value.id, node.attr),
                ))
    return violations


def _format_violations(violations: List[Violation]) -> str:
    return "\n".join(
        "{}:{} [{}] {}".format(v.relpath, v.lineno, v.rule, v.detail)
        for v in violations
    )


_SCANNED_FILES = list(iter_python_files())


# ---------------------------------------------------------------------------
# Live-tree rules
# ---------------------------------------------------------------------------

class TestFutureImportPresence:
    """R1 over every scanned file, one test per file."""

    @pytest.mark.parametrize(
        "path", _SCANNED_FILES, ids=[_relpath(p) for p in _SCANNED_FILES]
    )
    def test_future_import_is_first_statement(self, path):
        source = path.read_text(encoding="utf-8")
        violations = check_future_import(source, _relpath(path))
        assert not violations, _format_violations(violations)


class TestNoRuntimeTypeUnions:
    """R2 aggregate: zero runtime-position type unions across scanned roots."""

    def test_no_runtime_type_unions(self):
        violations = []
        for path in _SCANNED_FILES:
            source = path.read_text(encoding="utf-8")
            violations.extend(check_runtime_type_unions(source, _relpath(path)))
        assert not violations, _format_violations(violations)


class TestNoAnnotationEvalAPIs:
    """R3 aggregate: zero typing.cast / typing.get_type_hints uses."""

    def test_no_annotation_eval_apis(self):
        violations = []
        for path in _SCANNED_FILES:
            source = path.read_text(encoding="utf-8")
            violations.extend(check_annotation_eval_apis(source, _relpath(path)))
        assert not violations, _format_violations(violations)


# ---------------------------------------------------------------------------
# Detector non-vacuity — synthetic sources prove each rule actually fires
# (and stays silent where it must). A detector that flags everything or
# nothing fails the silent cases.
# ---------------------------------------------------------------------------

class TestDetectorNonVacuity:

    def test_union_annotation_without_future_import_fires(self):
        source = "def f(x: str | None): ...\n"
        violations = check_future_import(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "missing_future_import"
        # Enrichment cites the line of the annotation union.
        assert "lines: 1" in violations[0].detail

    def test_future_import_first_passes(self):
        source = (
            "from __future__ import annotations\n"
            "\n"
            "def f(x: str | None): ...\n"
        )
        assert check_future_import(source, "synthetic.py") == []

    def test_future_import_below_other_imports_fires(self):
        source = (
            "import json\n"
            "from __future__ import annotations\n"
        )
        violations = check_future_import(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "missing_future_import"

    def test_docstring_then_future_import_passes(self):
        source = (
            '"""Docstring."""\n'
            "\n"
            "from __future__ import annotations\n"
        )
        assert check_future_import(source, "synthetic.py") == []

    def test_runtime_union_with_none_operand_fires(self):
        source = (
            "from __future__ import annotations\n"
            "\n"
            "def g(x):\n"
            "    return isinstance(x, str | None)\n"
        )
        violations = check_runtime_type_unions(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "runtime_type_union"
        assert violations[0].lineno == 4

    def test_module_level_type_alias_union_fires(self):
        source = "Alias = str | int\n"
        violations = check_runtime_type_unions(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "runtime_type_union"

    def test_camelcase_exception_union_fires(self):
        source = (
            "def g(x):\n"
            "    return isinstance(x, MyError | ValueError)\n"
        )
        violations = check_runtime_type_unions(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "runtime_type_union"

    def test_integer_flag_ors_stay_silent(self):
        source = (
            "import os\n"
            "import re\n"
            "\n"
            "flags = os.O_CREAT | os.O_WRONLY\n"
            "pattern = re.compile('p', re.IGNORECASE | re.DOTALL)\n"
            "lock = fcntl.LOCK_EX | fcntl.LOCK_NB\n"
        )
        assert check_runtime_type_unions(source, "synthetic.py") == []

    def test_annotation_position_union_not_flagged_by_r2(self):
        source = (
            "from __future__ import annotations\n"
            "\n"
            "def f(x: str | None) -> int | None: ...\n"
            "value: dict | None = None\n"
        )
        assert check_runtime_type_unions(source, "synthetic.py") == []

    def test_typing_cast_import_fires(self):
        source = "from typing import cast\n"
        violations = check_annotation_eval_apis(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "annotation_eval_api"

    def test_typing_module_alias_attribute_fires(self):
        source = (
            "import typing as t\n"
            "\n"
            "def f(): ...\n"
            "hints = t.get_type_hints(f)\n"
        )
        violations = check_annotation_eval_apis(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "annotation_eval_api"
        assert violations[0].lineno == 4

    def test_unrelated_attribute_named_cast_stays_silent(self):
        source = (
            "import numpy\n"
            "\n"
            "value = numpy.cast('f8')\n"
        )
        assert check_annotation_eval_apis(source, "synthetic.py") == []

    def test_empty_module_fires_with_sole_line_remediation(self):
        violations = check_future_import("", "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "missing_future_import"
        assert "sole line" in violations[0].detail
