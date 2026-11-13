"""Every identity rung gives a hostile `.git` target the same answer.

Location: pact-plugin/tests/test_identity_rung_parity.py

The resolver reaches a checkout through several rungs: the git root of the
process cwd, a declared directory (`CLAUDE_PROJECT_DIR`, or the session
record), the worktree identity record of a gone declaration, and the
cwd-marker walk. Each rung runs the repository guards before it derives an
id. A test per rung proves each guard that EXISTS is tested; it cannot show a
guard one rung LACKS while its siblings have it. This module compares the
rungs side by side instead: every hostile target shape is run through every
route, and every route that reaches the shape must give it the same answer:
the decline reason (or the bound id for a benign control), and whether an
unusable override file planted in the common dir git reports for the target
is reported, with its reason and whether its path is withheld. The shapes
and routes live in tests/fixtures/identity_routes.py, shared with the
skill's override-read matrix.

What is derived from the code, not typed here:
- the rungs: every call site of `_root_guard` and `_identity_at_root` in the
  resolver must be executed by some cell of the matrix;
- the guard arms: every decline reason `_root_guard` can return must be
  produced by some cell;
- the identity layer's `.git`-marker predicates: every function
  `detect_id_for_root` calls with the asked root must answer True for some
  cell;
- the reasons: every reason measured must be one the code registers;
- the identity callers: every call expression of detect_id_for_root,
  _identity_at_root, main_repo_root or main_repo_common_dir in the memory
  scripts and the backlog writer must execute while the routes run, or be
  one of the sites NO_IDENTITY_BOUND counts with the reason it binds no store
  key.

A route reaches a shape when its own rung answers (the decline's `source`,
or the bound identity's). A route that does not is listed in NOT_REACHED, and
a hostile shape every rung binds by design is listed in ACCEPTED_BINDS; both
lists are checked against the measurement, so a stale entry fails.

A route marked FORCED stubs one input no real environment supplies under
pytest (the session record is inert in test processes; a worktree identity
record that the record check accepts always names a verified main checkout),
so the guard behind it can still be compared.
"""

from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    bindings,
    import_aliases,
    qualified_name,
)
from fixtures.identity_routes import (
    NOT_REACHED,
    ROUTES,
    SHAPES,
    VICTIM_ID,
    _answer,
    _clear,
    _override,
    plant_unusable_pin,
)

from scripts import memory_api
from scripts import project_identity as pi
from scripts.memory_api import PACTMemory

# --- call-site coverage ----------------------------------------------------


def _resolver_call_sites() -> dict:
    """{(callee, first line, last line)} for every rung call site in memory_api."""
    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    sites = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in ("_root_guard", "_identity_at_root"):
            sites[(name, node.lineno, node.end_lineno)] = 0
    return sites


def _guard_reasons() -> set:
    """Every decline reason `_root_guard` can return, read from its body."""
    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    guard = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_root_guard"
    )
    reasons = set()
    for node in ast.walk(guard):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_decline"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            reasons.add(node.args[1].value)
    return reasons


ASKED_NAMES = frozenset({"asked", "asked_root"})


def _marker_predicates() -> set:
    """The `.git`-marker predicates the identity layer applies to the asked
    root: every function `detect_id_for_root` calls with `asked` or
    `asked_root` as an argument, positional or keyword, wherever the call
    sits (an `if` test or an assignment such as the submodule check), that
    `project_identity` itself defines (a constructor such as `Path` is not a
    predicate)."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    detect = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "detect_id_for_root"
    )
    names = set()
    for call in ast.walk(detect):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        arguments = list(call.args) + [keyword.value for keyword in call.keywords]
        if call.func.id in defined and any(
            isinstance(a, ast.Name) and a.id in ASKED_NAMES for a in arguments
        ):
            names.add(call.func.id)
    return names


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    """{(shape, route): (answer, answering rung, override report)}, the call
    sites hit, and the marker predicates that answered True. Every cell
    plants an unusable override file in the common dir git reports for the
    target, so whether a route reads it is part of the answer. The same pass
    records every identity-function call (file, caller line, callee) for the
    caller census below."""
    sites = _resolver_call_sites()
    path = memory_api.__file__
    predicates = _marker_predicates()
    fired = set()
    files = {str(file): file.relative_to(PLUGIN_ROOT).as_posix() for file in _identity_caller_files()}
    executed = set()

    def tracer(frame, event, arg):
        if event == "return" and frame.f_code.co_name in predicates and arg is True:
            fired.add(frame.f_code.co_name)
        if event == "call" and frame.f_code.co_name in IDENTITY_FUNCTIONS:
            caller = frame.f_back
            if caller is not None and caller.f_code.co_filename in files:
                executed.add((files[caller.f_code.co_filename], caller.f_lineno, frame.f_code.co_name))
        if event == "call" and frame.f_code.co_name in ("_root_guard", "_identity_at_root"):
            caller = frame.f_back
            if caller is not None and caller.f_code.co_filename == path:
                for key in sites:
                    if key[0] == frame.f_code.co_name and key[1] <= caller.f_lineno <= key[2]:
                        sites[key] += 1
        return None

    cells = {}
    for shape, build in SHAPES.items():
        for route, (run, _sources) in ROUTES.items():
            base = tmp_path_factory.mktemp("parity")
            assert_outside_repository(base)
            target = build(base)
            plant_unusable_pin(target)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                previous = sys.getprofile()
                sys.setprofile(tracer)
                try:
                    detected = run(cell_patch, target, elsewhere)
                finally:
                    sys.setprofile(previous)
            cells[(shape, route)] = (*_answer(detected), _override(detected))
    return cells, sites, fired, executed


# Hostile shapes every rung binds alike, by design. Binding a `.git` that
# names an external git directory is a user-ruled behaviour: such a file is
# the same shape as the user's own `--separate-git-dir` checkout (git records
# no worktree there), and forging one needs the victim's absolute path. The
# id is derived and only the override path is withheld; the skill makes
# pinning that git directory the user's decision.
ACCEPTED_BINDS = {"foreign_external_store"}


def _reached(cells) -> dict:
    """{shape: {route: (answer, override report)}} for the cells whose own
    rung answered. The override report is (reason, path withheld) or None, so
    a route that starts or stops reading the file, or starts printing its
    path, disagrees."""
    by_shape = collections.defaultdict(dict)
    for (shape, route), (answer, rung, override) in cells.items():
        if rung in ROUTES[route][1]:
            by_shape[shape][route] = (answer, override)
    return by_shape


class TestEveryRungGivesAShapeTheSameAnswer:
    def test_every_route_that_reaches_a_shape_agrees(self, matrix):
        cells, _sites, _fired, _executed = matrix
        disagreements = {
            shape: answers
            for shape, answers in _reached(cells).items()
            if len(set(answers.values())) > 1
        }
        assert disagreements == {}, disagreements

    def test_every_unreached_cell_is_a_recorded_exception(self, matrix):
        cells, _sites, _fired, _executed = matrix
        unreached = {
            (shape, route): rung
            for (shape, route), (_answer_, rung, _override_) in cells.items()
            if rung not in ROUTES[route][1]
        }
        assert unreached == NOT_REACHED

    def test_no_hostile_shape_binds_the_victim(self, matrix):
        cells, _sites, _fired, _executed = matrix
        bound = {
            cell: answer for cell, (answer, _rung, _override_) in cells.items()
            if answer == f"bound:{VICTIM_ID}" and cell[0] not in ACCEPTED_BINDS
        }
        assert bound == {}

    def test_every_accepted_bind_still_binds(self, matrix):
        """An accepted shape that starts declining is no longer an exception."""
        cells, _sites, _fired, _executed = matrix
        for shape in ACCEPTED_BINDS:
            answers = {a for (s, _route), (a, _r, _o) in cells.items() if s == shape}
            assert answers == {f"bound:{VICTIM_ID}"}, (shape, answers)


class TestTheMatrixCoversTheCode:
    def test_every_rung_call_site_is_executed(self, matrix):
        _cells, sites, _fired, _executed = matrix
        unexecuted = [key for key, hits in sites.items() if hits == 0]
        assert unexecuted == [], f"a rung no route reaches: {unexecuted}"

    def test_every_guard_arm_is_produced(self, matrix):
        cells, _sites, _fired, _executed = matrix
        produced = {answer for answer, _rung, _override_ in cells.values()}
        assert _guard_reasons() - produced == set()

    def test_every_marker_predicate_fires(self, matrix):
        """Each `.git`-marker predicate the identity layer applies answers
        True for some shape, so a new predicate needs a shape here."""
        _cells, _sites, fired, _executed = matrix
        assert _marker_predicates() - fired == set()

    def test_every_measured_reason_is_registered(self, matrix):
        cells, _sites, _fired, _executed = matrix
        reasons = {a for a, _r, _o in cells.values() if not a.startswith("bound:")}
        assert reasons <= set(pi.DECLINE_REASONS)


# --- every identity caller in the memory scripts ---------------------------
# The rung list above comes from memory_api's resolver. A new caller of the
# identity functions anywhere else (another memory script, or the backlog
# writer) would derive an identity no route here exercises. So every call
# site of these functions in those files, one site per call expression, must
# either execute while the routes run, or be one of the excused sites listed
# with the reason it binds no store key.

IDENTITY_FUNCTIONS = frozenset(
    {"detect_id_for_root", "_identity_at_root", "main_repo_root", "main_repo_common_dir"}
)

PLUGIN_ROOT = Path(memory_api.__file__).resolve().parents[3]


def _identity_caller_files() -> list:
    scripts = sorted((PLUGIN_ROOT / "skills" / "pact-memory" / "scripts").glob("*.py"))
    return scripts + [PLUGIN_ROOT / "hooks" / "shared" / "backlog.py"]


# (file relative to the plugin root, enclosing function, callee) -> (how many
# call sites of that callee the function holds, why they bind no store key).
# The count makes a second, unexcused call in an excused function fail.
NO_IDENTITY_BOUND = {
    ("skills/pact-memory/scripts/cli.py", "cmd_project_id", "main_repo_root"): (
        1, "display only: the show envelope's process_repo_root",
    ),
    ("hooks/shared/backlog.py", "project_root", "main_repo_root"): (
        1, "the backlog file's directory; the file is keyed by the pre-change "
        "folder name by design",
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_location_divergence_warning", "main_repo_root"): (
        1, "compares the process's repository with the id already bound; binds nothing",
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_read_project_scopes", "main_repo_common_dir"): (
        1, "the common dir of the root the resolver already chose, for the legacy gate",
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_legacy_suppression", "main_repo_common_dir"): (
        1, "discloses a suppressed legacy name for the root the resolver already chose",
    ),
}


def _callee(func, aliases: dict, scope_bindings: dict):
    """The identity function a call's target names, or None.

    Resolved through the shared scanner resolution: an import, an import-as,
    a from-import and an attribute chain (`pi.detect_id_for_root`,
    `from scripts.project_identity import detect_id_for_root as d`), and a
    plain name assigned from one of those in the same scope (`r =
    main_repo_root`). An attribute on a call result (`_memory_api().x`)
    resolves by its attribute name. NOT SEEN: `getattr` with a string, `exec`
    and `eval`, which no memory script uses to reach these functions.
    """
    targets = [func]
    if isinstance(func, ast.Name) and func.id not in aliases:
        targets += [
            value for value in scope_bindings.get(func.id, ())
            if isinstance(value, (ast.Name, ast.Attribute))
        ]
    for target in targets:
        name = qualified_name(target, aliases)
        if name is None and isinstance(target, ast.Attribute):
            name = target.attr
        leaf = name.rsplit(".", 1)[-1] if name else None
        if leaf in IDENTITY_FUNCTIONS:
            return leaf
    return None


def _identity_call_sites() -> set:
    """{(file, enclosing function, callee, first line, last line)}: one site
    per call expression of an identity function in the memory scripts and the
    backlog writer, module-level code included (`<module>`)."""
    sites = set()
    for path in _identity_caller_files():
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = import_aliases(tree)
        module_bindings = bindings(tree, top_level_only=True)

        def visit(node, function, scope_bindings):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scoped = dict(module_bindings)
                    scoped.update(bindings(child))
                    visit(child, child.name, scoped)
                    continue
                if isinstance(child, ast.Call):
                    callee = _callee(child.func, aliases, scope_bindings)
                    if callee is not None:
                        sites.add((rel, function, callee, child.lineno, child.end_lineno))
                visit(child, function, scope_bindings)

        visit(tree, "<module>", module_bindings)
    return sites


def _cwd_repo(monkeypatch, target, elsewhere):
    """The process cwd's own repository identity (status `cwd_repo`, the
    divergence check), from inside the target checkout. The session is
    declared elsewhere, so the cwd's identity is derived on its own rather
    than reused from the resolver."""
    monkeypatch.chdir(target)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere))
    memory = PACTMemory(session_id="s", db_path=elsewhere / "m.db")
    return memory._current_repo_identity()


@pytest.fixture(scope="module")
def identity_callers(matrix, tmp_path_factory):
    """(calls traced while every route runs and the cwd_repo route runs, as
    (file, caller line, callee), {shape: cwd_repo id})."""
    files = {str(path): path.relative_to(PLUGIN_ROOT).as_posix() for path in _identity_caller_files()}
    executed = set(matrix[3])

    def tracer(frame, event, arg):
        if event == "call" and frame.f_code.co_name in IDENTITY_FUNCTIONS:
            caller = frame.f_back
            if caller is not None and caller.f_code.co_filename in files:
                executed.add((files[caller.f_code.co_filename], caller.f_lineno, frame.f_code.co_name))
        return None

    cwd_ids = {}
    for shape, build in SHAPES.items():
        for route, run in [("cwd_repo", _cwd_repo)]:
            base = tmp_path_factory.mktemp("callers")
            assert_outside_repository(base)
            target = build(base)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                previous = sys.getprofile()
                sys.setprofile(tracer)
                try:
                    result = run(cell_patch, target, elsewhere)
                finally:
                    sys.setprofile(previous)
            if route == "cwd_repo":
                cwd_ids[shape] = result
    return executed, cwd_ids


def _unexecuted_sites(executed) -> list:
    """The call sites no traced call landed in."""
    return [
        site for site in _identity_call_sites()
        if not any(
            file == site[0] and callee == site[2] and site[3] <= line <= site[4]
            for file, line, callee in executed
        )
    ]


def _excused_counts() -> collections.Counter:
    """{(file, function, callee): call sites it holds}, for the excused keys."""
    return collections.Counter(
        site[:3] for site in _identity_call_sites() if site[:3] in NO_IDENTITY_BOUND
    )


class TestEveryIdentityCallerIsCovered:
    def test_every_call_site_runs_or_binds_nothing(self, identity_callers):
        executed, _cwd_ids = identity_callers
        uncovered = sorted(
            site for site in _unexecuted_sites(executed) if site[:3] not in NO_IDENTITY_BOUND
        )
        assert uncovered == [], (
            "an identity call site no route executes and no reason excuses: "
            f"{uncovered}"
        )

    def test_every_excused_function_holds_exactly_its_excused_sites(self):
        """An excused function with a second identity call, or an excused
        call that is gone, no longer matches its listed count."""
        counts = _excused_counts()
        mismatched = {
            key: (counts.get(key, 0), listed)
            for key, (listed, _reason) in NO_IDENTITY_BOUND.items()
            if counts.get(key, 0) != listed
        }
        assert mismatched == {}, f"(found, listed) call sites: {mismatched}"

    def test_the_cwd_repository_never_names_the_victim(self, identity_callers):
        _executed, cwd_ids = identity_callers
        named = {
            shape: found for shape, found in cwd_ids.items()
            if found == VICTIM_ID and shape not in ACCEPTED_BINDS
        }
        assert named == {}

