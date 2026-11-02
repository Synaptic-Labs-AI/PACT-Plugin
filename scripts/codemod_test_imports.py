#!/usr/bin/env python3
"""Classify sys.path manipulation across the plugin's test suite (dry-run only).

Location: scripts/codemod_test_imports.py (repo root; maintainer tooling, not
shipped plugin payload).

Summary: AST-based classifier for the test-import restructure. Walks every
test file (tests/test_*.py plus skills-adjacent skills/*/test_*.py) and emits,
per file, a machine-readable record of every sys.path mutation call
(insert/append/remove), direct sys.path assignment, codegen string-literal
occurrences, bare scripts-module imports, multiprocessing usage, and
membership in (or reference by) the source-reading parity/twin-drift test
family. The report drives the deletion-batch stages: a call is a deletion
candidate only when its resolved target dir is centrally covered by the two
conftest files (tests/conftest.py + pact-plugin/conftest.py).

Used by: repo maintainers during the restructure arc. Dry-run only — this
script never edits a file. Run from the repo root:

    python3 scripts/codemod_test_imports.py [--report PATH]

AST-based on purpose: text/regex matching would rewrite the codegen test
files that carry 'sys.path.insert' inside string literals.
"""

import argparse
import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "pact-plugin"
TEST_GLOBS = ("tests/test_*.py", "skills/*/test_*.py")

# Roots centrally provided for tests/ files by tests/conftest.py after the
# centralization commit, plus the plugin-root conftest's skills/*/scripts glob
# and its explicit plugin-root insert (added by the precursor commit — the
# telegram test family's `from telegram.X import ...` resolves through it).
CENTRAL_ROOTS_TESTS = (
    "tests",  # tests dir itself
    "hooks",
    "skills/pact-memory",
    "skills/pact-memory/scripts",
    "skills/pact-coding-standards/scripts",
    "scripts",  # plugin-level scripts/
    "plugin root",  # pact-plugin/ itself, via the root conftest
)

BARE_SCRIPT_MODULES = ("working_memory", "memory_init", "embeddings")

# Filename family whose tests read/compare sibling source; edits to files they
# reference are assertions-affecting, not mechanical. Membership requires BOTH
# the name pattern AND an observed source-reading call — fixture readers are
# not family members.
SOURCE_READER_NAME = re.compile(r"(parity|twin|structure|mirror)")


def _iter_test_files():
    files = []
    for pattern in TEST_GLOBS:
        files.extend(PLUGIN.glob(pattern))
    return sorted(files)


class _PathExprEvaluator:
    """Resolve the tiny Path/os.path expression subset the suite actually uses.

    Handles: __file__, Name lookups against tracked module-level assignments,
    Path(...), str(...), os.path.join(...), os.path.dirname(...), the `/`
    join operator, .resolve(), .parent, .parents[i]. Anything else resolves to
    None (manual-tail classification).
    """

    def __init__(self, file_path, assignments):
        self.file_path = file_path
        self.assignments = assignments

    def eval(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return str(self.file_path)
            if node.id in self.assignments:
                return self.eval(self.assignments[node.id])
            return None
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = self.eval(node.left), self.eval(node.right)
            if left is None or right is None:
                return None
            return str(Path(left) / right)
        if isinstance(node, ast.Attribute):
            return self._eval_attr(node)
        if isinstance(node, ast.Subscript):
            # <path expr>.parents[i]
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
            ):
                base = self.eval(node.value.value)
                if base is None:
                    return None
                parents = Path(base).parents
                idx = node.slice.value
                return str(parents[idx]) if idx < len(parents) else None
            return None
        return None

    def _eval_call(self, node):
        name = self._call_name(node)
        if name in ("Path", "str"):
            return self.eval(node.args[0]) if node.args else None
        if name == "os.path.join":
            parts = [self.eval(a) for a in node.args]
            if any(p is None for p in parts):
                return None
            return str(Path(parts[0]).joinpath(*parts[1:]))
        if name == "os.path.dirname":
            base = self.eval(node.args[0]) if node.args else None
            return str(Path(base).parent) if base else None
        # Method-call form: <path expr>.resolve()
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve"
            and not node.args
        ):
            base = self.eval(node.func.value)
            return str(Path(base).resolve()) if base else None
        return None

    def _eval_attr(self, node):
        if node.attr == "parent":
            base = self.eval(node.value)
            return str(Path(base).parent) if base else None
        if node.attr == "resolve":
            base = self.eval(node.value)
            return str(Path(base).resolve()) if base else None
        # Path(__file__).parents[1] arrives as Subscript(value=Attribute(parents))
        return None

    @staticmethod
    def _call_name(node):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            parts = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            return ".".join(reversed(parts))
        return None


def _classify_target(resolved, arg_text):
    """Map a resolved path (or raw arg text) to its suite root name."""
    text = resolved or arg_text or ""
    text_norm = text.replace("\\", "/")
    plugin = str(PLUGIN).replace("\\", "/")
    if "pact-memory/scripts" in text_norm:
        return "skills/pact-memory/scripts"
    if "pact-coding-standards/scripts" in text_norm:
        return "skills/pact-coding-standards/scripts"
    if "pact-memory" in text_norm:
        return "skills/pact-memory"
    if re.search(r"/hooks/shared$", text_norm):
        return "hooks/shared"
    if re.search(r"/hooks$", text_norm) or text_norm == "hooks" or '"hooks"' in text_norm or "_HOOKS" in arg_text or "HOOKS" in arg_text:
        return "hooks"
    if re.search(r"/tests$", text_norm):
        return "tests"
    if text_norm.rstrip("/") == plugin:
        return "plugin root"
    if re.search(r"/scripts$", text_norm) and "skills" not in text_norm:
        return "scripts"
    if "/scripts" in text_norm and "skills" in text_norm:
        return "skills/*/scripts (other)"
    if "fixtures" in text_norm:
        return "tests/fixtures"
    if resolved is None:
        return "unresolved"
    return "other"


def _is_syspath_call(node):
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if (
        isinstance(f, ast.Attribute)
        and f.attr in ("insert", "append", "remove")
        and isinstance(f.value, ast.Attribute)
        and f.value.attr == "path"
        and isinstance(f.value.value, ast.Name)
        and f.value.value.id == "sys"
    ):
        return f.attr
    return None


def _is_syspath_assign(node):
    """Match sys.path = ... / sys.path[i] = ... assignment targets."""
    targets = node.targets if isinstance(node, ast.Assign) else []
    for t in targets:
        cand = t.value if isinstance(t, ast.Subscript) else t
        if (
            isinstance(cand, ast.Attribute)
            and cand.attr == "path"
            and isinstance(cand.value, ast.Name)
            and cand.value.id == "sys"
        ):
            return True
    return False


class _FileVisitor(ast.NodeVisitor):
    def __init__(self, source, file_path):
        self.source = source
        self.evaluator = None  # set after assignment pre-pass
        self.file_path = file_path
        self.calls = []
        self.assigns = 0
        self.imports = []  # (top_name, scope)
        self.uses_multiprocessing = False
        self.reads_source = False
        self.scope_depth = 0
        self.guard_stack = []

    # -- scope tracking -----------------------------------------------------
    def visit_FunctionDef(self, node):
        self.scope_depth += 1
        self.generic_visit(node)
        self.scope_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        seg = ast.get_source_segment(self.source, node.test) or ""
        self.guard_stack.append("sys.path" in seg)
        self.generic_visit(node)
        self.guard_stack.pop()

    # -- imports -------------------------------------------------------------
    def visit_Import(self, node):
        for a in node.names:
            top = a.name.split(".")[0]
            if top == "multiprocessing":
                self.uses_multiprocessing = True
            self.imports.append((top, "function" if self.scope_depth else "module"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            top = node.module.split(".")[0]
            if top == "multiprocessing":
                self.uses_multiprocessing = True
            self.imports.append((top, "function" if self.scope_depth else "module"))
        self.generic_visit(node)

    # -- sys.path mutations ----------------------------------------------------
    def visit_Assign(self, node):
        if _is_syspath_assign(node):
            self.assigns += 1
        self.generic_visit(node)

    def visit_Call(self, node):
        kind = _is_syspath_call(node)
        if kind:
            path_arg = (
                node.args[1]
                if kind == "insert" and len(node.args) > 1
                else (node.args[0] if node.args else None)
            )
            arg_text = (
                " ".join((ast.get_source_segment(self.source, path_arg) or "").split())
                if path_arg is not None
                else ""
            )
            resolved = (
                self.evaluator.eval(path_arg)
                if self.evaluator is not None and path_arg is not None
                else None
            )
            target = _classify_target(resolved, arg_text)
            self.calls.append(
                {
                    "line": node.lineno,
                    "kind": kind,
                    "scope": "function" if self.scope_depth else "module",
                    "guarded": any(self.guard_stack),
                    "arg": arg_text,
                    "resolved": resolved,
                    "target": target,
                    "variable_form": bool(
                        path_arg is not None
                        and not isinstance(path_arg, (ast.Call, ast.BinOp))
                    ),
                    "centrally_covered": target in CENTRAL_ROOTS_TESTS,
                }
            )
        # source-reading detection: read_text()/getsource/open() on anything
        name = _FileVisitor._call_name(node)
        if name and any(k in name for k in ("read_text", "getsource", "open")):
            self.reads_source = True
        self.generic_visit(node)

    @staticmethod
    def _call_name(node):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            parts = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            return ".".join(reversed(parts))
        return ""


def _collect_assignments(tree):
    """Pre-pass: module-level NAME = <expr> assignments for var resolution."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, node.value)
    return out


def _codegen_occurrences(tree, source):
    """Count string constants that themselves contain a sys.path mutation."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "sys.path.insert" in node.value or "sys.path.append" in node.value:
                count += 1
    return count


def _referenced_test_names(tree):
    """String literals naming test files or plugin .py paths (source readers)."""
    refs = set()
    pat = re.compile(r"(test_[\w]+\.py|[\w]+\.py)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in pat.finditer(node.value):
                refs.add(m.group(1))
    return sorted(refs)


def classify_file(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    visitor = _FileVisitor(source, path)
    visitor.evaluator = _PathExprEvaluator(path, _collect_assignments(tree))
    visitor.visit(tree)

    rel = path.relative_to(PLUGIN)
    bare_imports = sorted(
        {name for name, _scope in visitor.imports if name in BARE_SCRIPT_MODULES}
    )
    family = bool(SOURCE_READER_NAME.search(path.name)) and visitor.reads_source
    refs = (
        [r for r in _referenced_test_names(tree) if r != path.name]
        if family
        else []
    )
    return {
        "rel": str(rel),
        "calls": visitor.calls,
        "syspath_assignments": visitor.assigns,
        "codegen_string_occurrences": _codegen_occurrences(tree, source),
        "bare_scripts_imports": bare_imports,
        "uses_multiprocessing": visitor.uses_multiprocessing,
        "source_reader": family,
        "references": refs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report",
        default=str(REPO_ROOT / "scripts" / "codemod_test_imports_report.json"),
        help="Where to write the JSON classification report.",
    )
    args = parser.parse_args()

    files = _iter_test_files()
    records = {}
    summary = {
        "test_files": len(files),
        "files_with_calls": 0,
        "total_calls": 0,
        "module_scope": 0,
        "function_scope": 0,
        "guarded": 0,
        "unguarded": 0,
        "targets": {},
        "centrally_covered_calls": 0,
        "codegen_only_files": [],
        "bare_scripts_import_files": [],
        "multiprocessing_files": [],
        "source_reader_files": [],
        "referenced_test_files": [],
        "syspath_assignment_files": [],
    }
    referenced = set()
    for path in files:
        rec = classify_file(path)
        rel = rec.pop("rel")
        records[rel] = rec
        if rec["calls"]:
            summary["files_with_calls"] += 1
        for c in rec["calls"]:
            summary["total_calls"] += 1
            summary["module_scope" if c["scope"] == "module" else "function_scope"] += 1
            summary["guarded" if c["guarded"] else "unguarded"] += 1
            summary["targets"][c["target"]] = summary["targets"].get(c["target"], 0) + 1
            if c["centrally_covered"]:
                summary["centrally_covered_calls"] += 1
        if rec["codegen_string_occurrences"] and not rec["calls"]:
            summary["codegen_only_files"].append(rel)
        if rec["bare_scripts_imports"]:
            summary["bare_scripts_import_files"].append(rel)
        if rec["uses_multiprocessing"]:
            summary["multiprocessing_files"].append(rel)
        if rec["source_reader"]:
            summary["source_reader_files"].append(rel)
        if rec["syspath_assignments"]:
            summary["syspath_assignment_files"].append(rel)
        referenced.update(rec["references"])

    # Restrict to test files that exist in the suite (source readers also name
    # hooks/scripts modules, which are not deletion-batch members anyway).
    suite_names = {p.name for p in files}
    summary["referenced_test_files"] = sorted(referenced & suite_names)

    report = {"summary": summary, "files": records}
    report_path = Path(args.report)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"classified {summary['test_files']} files, "
        f"{summary['files_with_calls']} with sys.path calls, "
        f"{summary['total_calls']} calls; report -> {report_path}"
    )


if __name__ == "__main__":
    main()
