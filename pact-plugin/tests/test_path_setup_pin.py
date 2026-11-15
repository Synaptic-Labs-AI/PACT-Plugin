"""Structural pin: path setup is centralized — no per-file path inserts return.

The suite's path setup now lives in the two conftests (pact-plugin/conftest.py
and tests/conftest.py). This pin keeps it that way:

1. MODULE-LEVEL arm — no module-level path mutation outside MODULE_ALLOWLIST
   (the files whose inserts are load-bearing at import time: spawn-parent
   setup and the baseline loader).
2. ANY-FORM arm — no path mutation of ANY kind (module-level, function-level,
   or embedded in a codegen string) outside KEEP_SET. Spawn workers must
   rebuild sys.path in the child (it does not cross a spawn boundary) and
   subprocess harnesses generate their own path lines; both stay legitimate,
   but their file set is FIXED here so the set cannot grow silently.
3. COLLISION arm — no two sanctioned path roots expose the same module stem.
   Zero collisions today; a future skills/<new>/scripts/config.py shadowing
   pact-memory's config.py is the silent wrong-module failure this arm
   exists to catch.

Matched mutation forms: sys.path.insert/append/extend calls, slice-assignment
(sys.path[0:0] = [...]), and augmented assignment (+=) on sys.path — all but
insert were added after review findings that they dodged every arm while
being plausible honest-mistake forms, not adversarial constructions. `sys`
means any name the file binds to the sys module (`import sys as X`), and
`sys.path` also any name bound by `from sys import path [as P]` or assigned
sys.path (`entries = sys.path`), in any scope of the file.
A codegen string (a string constant or an f-string) that parses as Python,
as written or with its `%` and `{}` placeholders read as a name, after
dedent, is read as code for every one of these forms; its names are
collected together with the file's and its other strings', so an alias bound
in one string and used in another counts. Every string is also read as
tokens (`_mutates_path`: the call and augmented-assignment forms on the
literal names), which is all a string that does not parse is read for.
ACCEPTED UNDER-BLOCK (adversarial-only, documented boundary): wholesale
rebinding (`sys.path = [...]`), `getattr(sys.path, ...)` with a verb string or
`getattr(sys, "path")`, a dunder call (`sys.path.__iadd__(...)`), a name a
placeholder supplies, and an alias or slice-assign inside a codegen string
that does not parse — this pin is an honest-mistake guard, not an
adversary-proof one. OUT OF PARTITION (different failure mode): removal
mutations (remove/pop/clear/del) sabotage conftest setup rather than
re-introduce per-file path setup; zero occurrences at introduction, and the
failure would be loud (imports break), unlike a silent added root.

The matcher legs are pinned against silent rot by a committed negative
fixture (tests/fixtures/pin_negative_path_mutations.py) carrying one
violation per leg, plus test_negative_fixture_flags_every_matcher_leg
asserting the matcher flags each fixture line exactly. The fixture is
exempt from arms 1/2 via _NEGATIVE_FIXTURE — a single exact path, pinned
by the self-test so the exemption cannot widen silently.

Population: tests/**/*.py plus skills-adjacent test files
(skills/*/test_*.py). conftest.py files are the mechanism and are exempt.

Keep-set provenance (enumerated from the tree, not inherited from plan
text): module-level carriers, function-level (spawn-worker) carriers, and
codegen-string carriers. Files that imported the top-level scripts retain no
inserts at all once scripts/ is conftest-owned, so they do not appear here.
"""

import ast
import re
import subprocess
from pathlib import Path

from fixtures.tracked_files import tracked_files as _shared_tracked_files
from fixtures.tracked_files import tracked_glob as _shared_tracked_glob

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# What may sit between two tokens of a call form in a codegen string:
# whitespace, a line break, a backslash continuation, or a `#` comment, any
# number of them.
_BETWEEN = r"(?:\s|\\\n|#[^\n]*(?:\n|$))*"
# A path mutation in a codegen string, read as name and operator tokens:
# `sys . path . insert|append|extend (` or `sys . path +=`, with `_BETWEEN`
# between any two of them. Built in pieces so THIS file's own source never
# holds a needle — the any-form arm scans string constants and must not flag
# the pin itself. A codegen string that parses is also read as code
# (`_codegen_trees`).
_PATH_MUTATION = re.compile(
    r"\bsys" + _BETWEEN + r"\." + _BETWEEN + r"path\b" + _BETWEEN
    + r"(?:\." + _BETWEEN + r"(?:insert|append|extend)\b" + _BETWEEN + r"\(|\+=)"
)
_STR_VERBS = (".insert", ".append", ".extend", "+=")


def _mutates_path(text):
    """Whether `text` holds a path mutation, read as a stream of name and
    operator tokens (`_PATH_MUTATION`): whitespace, line breaks, backslash
    continuations and `#` comments between tokens are skipped. It reads any
    text, prose included, and never fails. It does not honour string-literal
    boundaries, so a call quoted inside a codegen string's own inner quotes
    is flagged too."""
    return _PATH_MUTATION.search(text) is not None

_MODULE_ALLOWLIST = {
    "tests/merge_guard_baseline_loader.py",
    "tests/test_session_journal.py",
    "tests/test_task_claim_gate.py",
    "tests/test_task_utils.py",
    "tests/test_working_memory_concurrency.py",
    # A FRESH-SUBPROCESS WORKER: it runs WITHOUT pytest's conftest (the arm
    # spawns it once per verb, so `memory_init._initialized` starts clean), so
    # it must locate the plugin root and put `scripts.*` on the path itself at
    # module level. This entry reaches `_KEEP_SET` through the union below.
    "tests/fixtures/zero_create_worker.py",
}

_KEEP_SET = _MODULE_ALLOWLIST | {
    # function-level (spawn workers re-derive path in the child process)
    "tests/test_session_registry_concurrency.py",
    "tests/test_working_memory_concurrency_comprehensive.py",
    # codegen strings (subprocess harnesses embed path lines in child code)
    "tests/test_agent_handoff_marker.py",
    # rig conftest embeds a sys.path line so the copied plugin imports
    "tests/test_ci_full_run_record.py",
    "tests/test_containment_certification.py",
    "tests/test_embedding_status_contract.py",
    "tests/test_memory_cli.py",
    "tests/test_memory_init.py",
    "tests/test_memory_reachability.py",
    "tests/test_memory_store_isolation.py",
    "tests/test_merge_guard.py",
    "tests/test_pact_session_config_dir_parity.py",
    # its driver string sets the child process's sys.path[0] the way
    # running the hook script does
    "tests/test_pin_caps_gate_paths.py",
    "tests/test_pin_growth.py",
    "tests/test_pin_growth_populations.py",
    "tests/test_pin_marker_writer_adversarial.py",
    "tests/test_pinned_section_parser_migration.py",
    "tests/test_project_dir_resolution.py",
    # embeds a sys.path line in a child-script codegen fixture
    "tests/test_project_identity_union.py",
    "tests/test_sync_result_contract.py",
    # classifier input data: path-mutation sources it parses, never runs
    "tests/test_fast_suite_selection.py",
}

# Keep-set files whose mutation tokens are DATA only: they may carry the
# tokens in strings, but no real mutation node at any level.
_DATA_ONLY = {"tests/test_fast_suite_selection.py"}

# Sanctioned path roots, mirroring the two conftests: tests/conftest.py owns
# tests/, hooks/, skills/pact-memory/, skills/pact-memory/scripts/,
# skills/pact-coding-standards/scripts/, scripts/; the root conftest owns
# skills/*/scripts (glob, future skills included) and the plugin root.
# tests/fixtures/ is reachable via an allowlisted per-file insert.
def _sanctioned_roots():
    roots = [
        PLUGIN_ROOT / "tests",
        PLUGIN_ROOT / "tests" / "fixtures",
        PLUGIN_ROOT / "hooks",
        PLUGIN_ROOT / "skills" / "pact-memory",
        PLUGIN_ROOT / "scripts",
        PLUGIN_ROOT,  # telegram package family
    ]
    # Derived from the index: an untracked skills/<x>/scripts directory must
    # not become a sanctioned root.
    roots.extend(sorted({
        path.parent for path in _shared_tracked_glob(
            PLUGIN_ROOT.parent, f"{PLUGIN_ROOT.name}/skills/*/scripts/*.py",
            f"{PLUGIN_ROOT.name}/skills/pact-memory/scripts/memory_init.py")
    }))
    # resolve() so equivalent spellings dedupe
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve())
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r.resolve())
    return out


def _population():
    """Test sources from the git index, one glob per population.

    An untracked scratch test file must not be judged by the path-setup pins;
    the seeded known members keep an empty or misnamed root loud.
    """
    name = PLUGIN_ROOT.name
    files = _shared_tracked_glob(
        PLUGIN_ROOT.parent, f"{name}/tests/**/*.py",
        f"{name}/tests/{Path(__file__).name}")
    files += _shared_tracked_glob(
        PLUGIN_ROOT.parent, f"{name}/skills/*/test_*.py",
        f"{name}/skills/pact-handoff-harvest/test_skill_loading_harvest.py")
    return [f for f in files if f.name != "conftest.py"]


def _tracked_python_in(root):
    """Tracked top-level .py files in one sanctioned root."""
    relative = root.relative_to(PLUGIN_ROOT).as_posix()
    prefix = PLUGIN_ROOT.name if relative == "." else f"{PLUGIN_ROOT.name}/{relative}"
    return [
        path for path in _shared_tracked_files(
            PLUGIN_ROOT.parent, prefix, allow_empty=True)
        if path.suffix == ".py" and path.parent == root
    ]


# (names bound to the sys module, names bound to sys.path)
_PLAIN_SYS = (frozenset({"sys"}), frozenset())


def _sys_names(tree):
    """Every name a file binds to the sys module (`import sys`, `import sys
    as X`) or to sys.path (`from sys import path [as P]`), in any scope.
    Collected file-wide: an alias in one function also counts in another,
    which can only over-match."""
    modules, paths = {"sys"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names
                           if a.name == "sys")
        elif isinstance(node, ast.ImportFrom) and node.module == "sys" and (
                not node.level):
            paths.update(a.asname or a.name for a in node.names
                         if a.name == "path")
    # A name assigned sys.path (`entries = sys.path`) holds the same list,
    # so a mutation through it is a path mutation; followed to a fixed point.
    changed = True
    while changed:
        changed = False
        names = (frozenset(modules), frozenset(paths))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                    and _is_sys_path(node.value, names):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in paths:
                        paths.add(target.id)
                        changed = True
    return frozenset(modules), frozenset(paths)


def _is_sys_path(value, names=_PLAIN_SYS):
    modules, paths = names
    if isinstance(value, ast.Name):
        return value.id in paths
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "path"
        and isinstance(value.value, ast.Name)
        and value.value.id in modules
    )


_MUTATION_VERBS = frozenset({"insert", "append", "extend"})


def _is_mutation_call(node, names=_PLAIN_SYS):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATION_VERBS
        and _is_sys_path(node.func.value, names)
    )


def _is_slice_assign(node, names=_PLAIN_SYS):
    """An Assign whose target subscripts sys.path (sys.path[0:0] = [...]).
    Insert-equivalent precedence, but not a Call, so the call predicate
    structurally cannot see it. Any subscript target matches (index
    replacement included) — all of them rewrite the search path."""
    return isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Subscript) and _is_sys_path(t.value, names)
        for t in node.targets
    )


def _is_aug_assign(node, names=_PLAIN_SYS):
    """An AugAssign whose target is the sys.path attribute (the += form).
    Extend-equivalent, and neither a Call nor an Assign, so both other
    predicates structurally miss it."""
    return isinstance(node, ast.AugAssign) and _is_sys_path(node.target, names)


def _is_path_mutation(node, names=_PLAIN_SYS):
    return (_is_mutation_call(node, names) or _is_slice_assign(node, names)
            or _is_aug_assign(node, names))


def _module_level_mutations(path):
    """Line numbers of path mutations in MODULE scope only (defs/classes
    excluded — a spawn worker's in-function rebuild is not module setup)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = _sys_names(tree)
    lines = []

    def walk(node, in_def):
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                walk(child, True)
                continue
            if _is_path_mutation(child, names) and not in_def:
                lines.append(child.lineno)
            walk(child, in_def)

    walk(tree, False)
    return lines


# A %-style or str.format placeholder in a codegen template (`%r`, `%(x)s`,
# `{}`, `{0!r}`), which stops the template parsing as Python.
_PLACEHOLDER = re.compile(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[rsadifxXeEgGc]|\{[^{}]*\}")


def _codegen_trees(tree):
    """Each string in `tree` that parses as Python, parsed: every string
    constant, and every f-string with each placeholder read as a name, taken
    as written and again with its `%` and `{}` placeholders read as a name,
    each after `textwrap.dedent`. A parsed string's own strings are read the
    same way. A string that parses in neither form is left to
    `_mutates_path`."""
    import textwrap
    import warnings

    trees = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value if isinstance(part, ast.Constant) else "_" for part in node.values
            )
        else:
            continue
        for candidate in {text, _PLACEHOLDER.sub("_", text)}:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    parsed = ast.parse(textwrap.dedent(candidate))
            except (SyntaxError, ValueError):
                continue
            trees.append(parsed)
            trees.extend(_codegen_trees(parsed))
    return trees


def _any_form_present(path):
    """True if the file contains a path mutation at any level OR embeds one
    in a string (codegen). A string that parses as Python
    (`_codegen_trees`) is read with the real-code matcher, and every string
    is also read as tokens (`_mutates_path`). The names bound to sys and
    sys.path are collected over the file and all its parsed strings
    together, so an alias bound in one string and used in another counts.
    Comments are AST-invisible and never match; a docstring is a string
    constant like any other, so a form in one is a form."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    trees = [tree, *_codegen_trees(tree)]
    names = _sys_names(ast.Module(body=[s for t in trees for s in t.body], type_ignores=[]))
    for node in (node for t in trees for node in ast.walk(t)):
        if _is_path_mutation(node, names):
            return True
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _mutates_path(node.value)
        ):
            return True
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                c.value
                for c in node.values
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            )
            if _mutates_path(text) or ("sys.path" in text and any(v in text for v in _STR_VERBS)):
                return True
    return False


def _rel(path):
    return str(path.resolve().relative_to(PLUGIN_ROOT))


# The committed negative fixture is DELIBERATE violation evidence (pinned by
# test_negative_fixture_flags_every_matcher_leg), so the population arms skip
# exactly this one path. The self-test asserts this constant's value — an
# exemption widening requires editing this line and failing the self-test.
_NEGATIVE_FIXTURE = "tests/fixtures/pin_negative_path_mutations.py"


def test_no_module_level_insert_outside_allowlist():
    violations = []
    for f in _population():
        rel = _rel(f)
        if rel in _MODULE_ALLOWLIST or rel == _NEGATIVE_FIXTURE:
            continue
        for ln in _module_level_mutations(f):
            violations.append(f"{rel}:{ln}")
    assert not violations, (
        "module-level path mutation outside the allowlist (path setup is "
        "conftest-owned now): " + ", ".join(violations)
    )


def test_no_insert_of_any_form_outside_keep_set():
    violations = []
    for f in _population():
        rel = _rel(f)
        if rel in _KEEP_SET or rel == _NEGATIVE_FIXTURE:
            continue
        if _any_form_present(f):
            violations.append(rel)
    assert not violations, (
        "path mutation (real or codegen) outside the keep-set — spawn-worker "
        "and subprocess files are a FIXED set; adding one means updating "
        "this pin deliberately: " + ", ".join(violations)
    )


def test_data_only_carriers_mutate_nothing():
    """A data-only keep-set entry exempts strings, not code: a real insert,
    append, extend, slice-assign or += on sys.path, at any level, still
    fails here."""
    assert _DATA_ONLY <= _KEEP_SET
    real = []
    for rel in sorted(_DATA_ONLY):
        tree = ast.parse((PLUGIN_ROOT / rel).read_text(encoding="utf-8"))
        names = _sys_names(tree)
        real += [f"{rel}:{node.lineno}" for node in ast.walk(tree)
                 if _is_path_mutation(node, names)]
    assert not real, "real path mutation in a data-only carrier: " + ", ".join(real)


def test_the_data_only_check_sees_a_function_level_insert():
    """Pins the arm above: its matcher flags a real function-level insert
    the module-level arm cannot see."""
    # Concatenated, like _PATH_MUTATION, so this file carries no needle itself.
    tree = ast.parse("import sys\ndef f():\n    sys.path" + ".insert(0, 'x')\n")
    assert [node.lineno for node in ast.walk(tree)
            if _is_path_mutation(node)] == [3]


def test_a_name_holding_sys_path_is_followed(tmp_path):
    """Control for the alias leg of `_sys_names`: a mutation through a name
    assigned sys.path (directly or through another such name) is a path
    mutation; a mutation of a copy of the list is not."""
    module = tmp_path / "aliased.py"
    name = "sys" + ".path"  # in pieces, so this file holds no form itself
    module.write_text(
        "import sys\n"
        f"entries = {name}\n"
        "entries.insert(0, 'x')\n"
        "again = entries\n"
        "again += ['y']\n"
        f"snapshot = list({name})\n"
        "snapshot.insert(0, 'x')\n",
        encoding="utf-8",
    )
    assert _module_level_mutations(module) == [3, 5]


def test_a_docstring_naming_a_mutation_is_a_form(tmp_path):
    """A docstring is scanned like any other string: one that quotes a path
    mutation is a form, as are a string assigned for a child process to run
    and a real mutation; a docstring that describes one in words is not."""
    needle = "sys.path" + ".insert(0, 'x')"
    documented = tmp_path / "documented.py"
    documented.write_text(
        f'def helper():\n    """Do not {needle}."""\n    return None\n',
        encoding="utf-8",
    )
    worded = tmp_path / "worded.py"
    worded.write_text(
        'def helper():\n    """Never insert into sys.path here."""\n    return None\n',
        encoding="utf-8",
    )
    codegen = tmp_path / "codegen.py"
    codegen.write_text(f'CHILD = "import sys; {needle}"\n', encoding="utf-8")
    real = tmp_path / "real.py"
    real.write_text(f"import sys\n{needle}\n", encoding="utf-8")
    assert _any_form_present(documented)
    assert not _any_form_present(worded)
    assert _any_form_present(codegen)
    assert _any_form_present(real)


def test_a_codegen_call_form_is_read_as_tokens(tmp_path):
    """A codegen string's call form is read as name and operator tokens:
    a space, a line break, a backslash continuation or a comment between
    them does not hide it, while prose that names `sys.path` without a call
    is not a form. The reader takes text Python's tokenizer rejects (an
    apostrophe, an unbalanced bracket, a dedent, an unterminated quote) and
    returns an answer for each."""
    name = "sys" + ".path"
    forms = [
        f"import sys; {name}.insert (0, 'x')",
        f"import sys; {name}.insert\n    (0, 'x')",
        f"import sys; {name} .insert(0, 'x')",
        f"import sys; {name}.insert  # first\n(0, 'x')",
        f"import sys; {name}.append \\\n('x')",
        f"import sys; {name}   +=   ['x']",
    ]
    for text in forms:
        assert _mutates_path(text), text
        carrier = tmp_path / "carrier.py"
        carrier.write_text(f"CHILD = {text!r}\n", encoding="utf-8")
        assert _any_form_present(carrier), text
    for text in [f"Never insert into {name} here.", f"the {name} entries", f"{name}[0]"]:
        assert not _mutates_path(text), text
    rejected = ["don't touch it", "f(", "    a\n  b", '"unterminated', f"don't {name}.insert (0, 'x')"]
    assert [_mutates_path(text) for text in rejected] == [False, False, False, False, True]


def test_a_codegen_string_that_parses_is_read_as_code(tmp_path):
    """A codegen string that parses as Python, as written or with its `%`
    and `{}` placeholders read as a name, is read with the real-code matcher:
    an alias of sys or of sys.path, a name assigned sys.path, an alias bound
    in one string and used in another, an indented block and a subscript
    assignment are all forms. A string that only reads sys.path, or mutates
    a copy, is not."""
    sys_ = "sys"  # the module name in pieces, so this file holds no form itself

    def carrier(source):
        path = tmp_path / "carrier.py"
        path.write_text(source, encoding="utf-8")
        return _any_form_present(path)

    forms = [
        f"import {sys_} as s; s.path.insert(0, %r)",
        f"from {sys_} import path; path.insert(0, %r)",
        f"from {sys_} import path as p; p.append(%r)",
        f"import {sys_}; entries = {sys_}.path; entries.insert(0, %r)",
        f"import {sys_} as s\ns.path.extend([{{root!r}}])",
        f"import {sys_}\n{sys_}.path[0:0] = [%r]",
    ]
    for text in forms:
        assert carrier(f"CHILD = {text!r}\n"), text
    indented = f"    import {sys_} as s\n    s.path.insert(0, root)\n"
    assert carrier(f"CHILD = {indented!r}\n")
    assert carrier(f"def child(root):\n    return f'import {sys_} as s; s.path.insert(0, {{root!r}})'\n")
    assert carrier(f"LINES = [{f'import {sys_} as s'!r}, 's.path.insert(0, %r)']\n")
    nested = f"RUN = {f'import {sys_} as s; s.path.insert(0, %r)'!r}"
    assert carrier(f"CHILD = {nested!r}\n")
    for text in [
        f"import {sys_} as s; print(s.path)",
        f"import {sys_}; entries = list({sys_}.path); entries.insert(0, %r)",
        f"import {sys_} as s; s.argv.append(%r)",
    ]:
        assert not carrier(f"CHILD = {text!r}\n"), text


def test_negative_fixture_flags_every_matcher_leg():
    """Pins the pin: the matcher must flag each committed violation line.

    The fixture carries one module-level mutation per leg — insert, append,
    extend (call leg), slice-assign, augmented assign — plus one through an
    alias of sys and one through `from sys import path`. Exact line numbers
    are the assertion: editing the fixture without updating this test fails
    here, so neither silent matcher rot nor silent fixture drift is
    possible. Also pins the exemption to exactly the fixture path."""
    fixture = PLUGIN_ROOT / _NEGATIVE_FIXTURE
    assert fixture.is_file(), f"negative fixture missing: {_NEGATIVE_FIXTURE}"
    assert _module_level_mutations(fixture) == [18, 19, 20, 21, 22, 28, 29]
    assert _any_form_present(fixture)
    assert _NEGATIVE_FIXTURE not in _MODULE_ALLOWLIST
    assert _NEGATIVE_FIXTURE not in _KEEP_SET


def test_untracked_test_file_cannot_join_the_path_setup_population(
        tmp_path, monkeypatch):
    """The population read is the index, so an untracked test file is invisible.

    Reverting ``_population`` to the two working-tree globs keeps the shipped
    tree green; this arm drives the module-level-insert pin and reddens naming
    the untracked file. The seeded tracked test is the positive control.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    tests_dir = plugin / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / Path(__file__).name).write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    skills = plugin / "skills" / "pact-handoff-harvest"
    skills.mkdir(parents=True)
    (skills / "test_skill_loading_harvest.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)

    assert {p.name for p in _population()} == {
        Path(__file__).name, "test_skill_loading_harvest.py"}, (
        f"positive control: the seeded sources are missing: "
        f"{[p.name for p in _population()]}")
    # Split so this file's own source holds no contiguous needle: the
    # any-form arm scans string constants and must not see its own fixture.
    (tests_dir / "zzprobe.py").write_text(
        "import sys\nsys.path." + "insert(0, 'x')\n", encoding="utf-8")

    test_no_module_level_insert_outside_allowlist()


def test_untracked_skill_scripts_dir_cannot_join_the_sanctioned_roots(
        tmp_path, monkeypatch):
    """A new sanctioned root comes from the index, not from the working tree.

    Reverting ``_sanctioned_roots`` to ``glob("skills/*/scripts")`` keeps the
    shipped tree green; this arm plants an untracked root whose module stem
    collides with a tracked one and reddens on the collision. The tracked
    scripts package is the positive control.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    tests_dir = plugin / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_audit_protocol.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    scripts = plugin / "skills" / "pact-memory" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "memory_init.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)

    assert scripts.resolve() in _sanctioned_roots(), (
        f"positive control: the tracked scripts root is missing: "
        f"{[str(r) for r in _sanctioned_roots()]}")
    probe_dir = plugin / "skills" / "zzprobe" / "scripts"
    probe_dir.mkdir(parents=True)
    (probe_dir / "test_audit_protocol.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")

    test_no_basename_collision_across_sanctioned_roots()


def test_no_basename_collision_across_sanctioned_roots():
    """No two sanctioned roots may expose the same module stem. A collision
    makes resolution order decide which module an import gets — the silent
    wrong-module failure the identity map can only see after the fact.
    Scope: .py stems (dunder-init excluded). Directory-name duality (the
    plugin-root scripts/ dir vs the pact-memory scripts package) is a
    designed state and out of scope for this arm."""
    stems = {}  # stem -> root that first exposed it
    collisions = []
    for root in _sanctioned_roots():
        for py in _tracked_python_in(root):
            if py.name in ("__init__.py", "conftest.py"):
                continue
            stem = py.stem
            if stem in stems and stems[stem] != root:
                collisions.append(
                    f"{stem}.py exposed by both {stems[stem]} and {root}"
                )
            else:
                stems.setdefault(stem, root)
    assert not collisions, "module-stem collision(s): " + "; ".join(collisions)
