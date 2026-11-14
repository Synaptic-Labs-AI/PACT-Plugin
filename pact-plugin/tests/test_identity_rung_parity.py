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
  key;
- the git-call floor: the per-layout, per-rung counts PACTMemory.__init__
  documents, with no override pin and with one, are measured on every real
  route.

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
    run_git,
)
from fixtures.identity_routes import (
    FORCED_ROUTES,
    NOT_REACHED,
    OWN,
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


RUNGS = ("_root_guard", "_identity_at_root")


def _unresolved_rung_references() -> list:
    """Every reference to a rung in memory_api other than as a called name or
    attribute. FAIL CLOSED: a rung bound to a variable, passed as a value or
    wrapped (`partial`) is a call site `_resolver_call_sites` cannot list."""
    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return [
        f"{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if (
            (isinstance(node, ast.Name) and node.id in RUNGS)
            or (isinstance(node, ast.Attribute) and node.attr in RUNGS)
        )
        and id(node) not in called
    ]


def _guard_reason_forms() -> tuple:
    """(reasons, unresolved): every decline reason `_root_guard` can return,
    read from its body, and every form that census cannot read.

    FAIL CLOSED: a `_decline` call whose reason is not a string literal
    (positional or `reason=`), a `_decline` used other than as a called
    name, and a `return` that is neither None nor such a call are listed as
    unresolved, so a guard arm the census cannot see fails the arm instead
    of passing it."""
    tree = ast.parse(Path(memory_api.__file__).read_text(encoding="utf-8"))
    guard = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_root_guard"
    )
    calls = [
        node for node in ast.walk(guard)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_decline"
    ]
    called = {id(call.func) for call in calls}
    returned = {id(call) for call in calls}
    reasons, unresolved = set(), []
    for call in calls:
        reason = call.args[1] if len(call.args) >= 2 else next(
            (keyword.value for keyword in call.keywords if keyword.arg == "reason"), None,
        )
        if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
            reasons.add(reason.value)
        else:
            unresolved.append(f"{call.lineno}: {ast.unparse(call)}")
    for node in ast.walk(guard):
        if isinstance(node, ast.Name) and node.id == "_decline" and id(node) not in called:
            unresolved.append(f"{node.lineno}: _decline used as a value")
        if isinstance(node, ast.Return) and node.value is not None:
            value = node.value
            if (isinstance(value, ast.Constant) and value.value is None) or id(value) in returned:
                continue
            unresolved.append(f"{node.lineno}: return {ast.unparse(value)}")
    return reasons, unresolved


ASKED_NAMES = frozenset({"asked", "asked_root"})


# Calls the identity layer makes with the asked root that return no layout
# outcome, with the reason.
NOT_A_PREDICATE = {"Path": "builds the asked path; it answers no layout question"}


def _marker_predicate_forms() -> tuple:
    """(predicates, unresolved, excluded): the `.git`-marker predicates the
    identity layer applies to the asked root, every use of the asked root
    that census cannot read, and the NOT_A_PREDICATE calls it met.

    A predicate is a function `project_identity` itself defines that
    `detect_id_for_root` calls by name with `asked` or `asked_root` as a
    direct argument, positional or keyword, wherever the call sits (an `if`
    test or an assignment such as the submodule check). FAIL CLOSED: every
    other read of the asked root, except a test against None and a
    NOT_A_PREDICATE call, is unresolved: a call through a variable, an
    attribute or a call result, an alias, or the root passed inside an
    expression could hide a predicate the census would never count."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    detect = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "detect_id_for_root"
    )
    parents = {child: node for node in ast.walk(detect) for child in ast.iter_child_nodes(node)}
    names, unresolved, excluded = set(), [], set()
    for node in ast.walk(detect):
        if not (isinstance(node, ast.Name) and node.id in ASKED_NAMES and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents[node]
        if isinstance(parent, ast.keyword):
            parent = parents[parent]
        if isinstance(parent, ast.Compare) and all(
            isinstance(c, ast.Constant) and c.value is None for c in parent.comparators
        ):
            continue
        if isinstance(parent, ast.Call) and node is not parent.func and isinstance(parent.func, ast.Name):
            if parent.func.id in defined:
                names.add(parent.func.id)
                continue
            if parent.func.id in NOT_A_PREDICATE:
                excluded.add(parent.func.id)
                continue
        unresolved.append(f"{node.lineno}: {ast.unparse(parent)}")
    return names, unresolved, excluded


def _marker_predicates() -> set:
    """The predicate names `_marker_predicate_forms` reads."""
    return _marker_predicate_forms()[0]


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
        disagreements = {}
        for shape, answers in _reached(cells).items():
            if len(set(answers.values())) > 1:
                # Grouped by answer, so the route that differs is named.
                grouped = collections.defaultdict(list)
                for route, answer in answers.items():
                    grouped[answer].append(route)
                disagreements[shape] = {answer: sorted(routes) for answer, routes in grouped.items()}
        assert disagreements == {}, f"routes that answer a shape differently: {disagreements}"

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
        unresolved = _unresolved_rung_references()
        assert unresolved == [], f"a rung reference the call-site census cannot read: {unresolved}"
        unexecuted = [key for key, hits in sites.items() if hits == 0]
        assert unexecuted == [], f"a rung no route reaches: {unexecuted}"

    def test_every_guard_arm_is_produced(self, matrix):
        cells, _sites, _fired, _executed = matrix
        reasons, unresolved = _guard_reason_forms()
        assert unresolved == [], f"a guard decline the census cannot read: {unresolved}"
        produced = {answer for answer, _rung, _override_ in cells.values()}
        assert reasons - produced == set()

    def test_every_marker_predicate_fires(self, matrix):
        """Each `.git`-marker predicate the identity layer applies answers
        True for some shape, so a new predicate needs a shape here."""
        _cells, _sites, fired, _executed = matrix
        predicates, unresolved, excluded = _marker_predicate_forms()
        assert unresolved == [], f"a use of the asked root the census cannot read: {unresolved}"
        assert excluded == set(NOT_A_PREDICATE), f"a stale NOT_A_PREDICATE entry: {set(NOT_A_PREDICATE) - excluded}"
        assert predicates - fired == set()

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


def _identity_call_forms() -> tuple:
    """(sites, unresolved). `sites` is {(file, enclosing function, callee,
    first line, last line)}: one site per call expression of an identity
    function in the memory scripts and the backlog writer, module-level code
    included (`<module>`). FAIL CLOSED: an identity function referenced other
    than as a call target or the value of a plain assignment (which `_callee`
    resolves through the scope's bindings), for example passed to `partial`
    or stored in a container, is listed in `unresolved`."""
    sites, unresolved = set(), []
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
                elif (
                    isinstance(child, (ast.Name, ast.Attribute))
                    and not (isinstance(node, ast.Call) and child is node.func)
                    and not (isinstance(node, ast.Assign) and child is node.value)
                    and _callee(child, aliases, scope_bindings) is not None
                ):
                    unresolved.append(f"{rel}:{child.lineno}: {ast.unparse(node)}")
                visit(child, function, scope_bindings)

        visit(tree, "<module>", module_bindings)
    return sites, unresolved


def _identity_call_sites() -> set:
    """The call sites `_identity_call_forms` reads."""
    return _identity_call_forms()[0]


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
        unresolved = _identity_call_forms()[1]
        assert unresolved == [], f"an identity reference the call-site census cannot read: {unresolved}"
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


# --- the documented git-call floor ----------------------------------------
# PACTMemory.__init__'s docstring states, per layout and rung, how many git
# subprocesses detection runs, how many the first legacy-scope read adds, and
# that no call repeats. Every number is measured here on every real route;
# the forced routes are left out because their stubs replace git answers.

# Every own layout the shared fixture builds, by its row name in the table:
# each `control_own_` shape, read from SHAPES, so a new own layout joins here.
FLOOR_LAYOUTS = {
    build.layout: name for name, build in SHAPES.items() if name.startswith("control_own_")
}


# The `.git`-layout predicates: every `-> bool` function of project_identity
# that detect_id_for_root reaches from the asked root (the marker predicates
# and every project_identity function they call, transitively). Each truthy
# `return` is one layout outcome the identity code distinguishes, and each
# operand of a returned `or` is one outcome of its own.
#
# NOT A FLOOR LAYOUT: the outcomes of these predicates, which only a hostile
# `.git` produces, or which describe no layout at all. A layout that reaches
# them declines, or never binds an id, so it has no documented count.
NOT_A_FLOOR_LAYOUT = {
    "_gitfile_points_at_foreign_gitdir": (
        "true for a `.git` that points at a directory named `.git`: another "
        "repository's git dir, which declines, or the user's own "
        "`--separate-git-dir=<dir>/.git` store, which declines without a pin "
        "and is measured pinned (TestTheGitCallFloorWithAPin), because a pin "
        "answers before this predicate runs"
    ),
    "_redirect_target_is_foreign": (
        "the redirect test behind the predicate above, true for the same "
        "targets: a foreign git dir, or the user's own dot-git store"
    ),
    "_admin_dir_names_another_checkout": (
        "true for a submodule admin dir whose recorded core.worktree names "
        "another checkout (a forgery) or is unset (the user's own submodule "
        "after `git submodule deinit`, which is unprovable); both decline"
    ),
    "_git_env_bool": "reads a git environment flag, not the `.git` layout",
}


def _layout_predicate_forms() -> tuple:
    """(predicates, unresolved): the `-> bool` functions reached from the
    marker predicates through called names, and every form that walk cannot
    read. FAIL CLOSED: a reached function with no return annotation (it could
    be a predicate the `-> bool` filter drops) and a `project_identity`
    function referenced other than as a called name (a call through a
    variable or a wrapper the walk cannot follow) are unresolved."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    defined = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    reached, frontier = set(_marker_predicates()), list(_marker_predicates())
    unresolved = []
    while frontier:
        function = defined[frontier.pop()]
        if function.returns is None:
            unresolved.append(f"{function.lineno}: {function.name} has no return annotation")
        called = {id(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id in defined and id(node) not in called:
                unresolved.append(f"{node.lineno}: {function.name} uses {node.id} as a value")
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in defined and node.func.id not in reached
            ):
                reached.add(node.func.id)
                frontier.append(node.func.id)
    predicates = {
        name for name in reached
        if isinstance(defined[name].returns, ast.Name) and defined[name].returns.id == "bool"
    }
    return predicates, unresolved


def _layout_predicates() -> set:
    """The predicate names `_layout_predicate_forms` reads."""
    return _layout_predicate_forms()[0]


def _layout_outcomes(predicates: set) -> dict:
    """{(predicate, return line, operand index or None): operand node} for
    every truthy `return` of each predicate."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    outcomes = {}
    for function in tree.body:
        if not (isinstance(function, ast.FunctionDef) and function.name in predicates):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            if isinstance(value, ast.Constant) and not value.value:
                continue
            if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                for index, operand in enumerate(value.values):
                    outcomes[(function.name, node.lineno, index)] = operand
            else:
                outcomes[(function.name, node.lineno, None)] = value
    return outcomes


def _rung_family(route: str) -> str:
    """The rung a route name belongs to: the name before its qualifier."""
    return route.split(" (", 1)[0]


def _documented_floor() -> tuple:
    """({layout: {rung: (fewest, most)}}, first-read maximum) from the
    docstring, where a rung's count is `N` or `N-M`."""
    import re

    doc = " ".join(PACTMemory.__init__.__doc__.split())
    table = {}
    for layout in FLOOR_LAYOUTS:
        line = re.search(
            rf"(?<=[;:] ){re.escape(layout)}: ((?:[A-Za-z_ ]+ \d+(?:-\d+)?(?:, )?)+)[;.]", doc
        )
        assert line, f"the docstring states no git-call counts for {layout!r}"
        table[layout] = {
            rung.strip(): (int(low), int(high or low))
            for rung, low, high in re.findall(
                r"([A-Za-z_ ]+?) (\d+)(?:-(\d+))?(?:, |$)", line.group(1)
            )
        }
    rows = re.search(r"routes \(each takes about 1 ms\): (.+?)\.(?: |$)", doc)
    assert rows, "the docstring has no git-call table"
    stated = {row.split(": ", 1)[0] for row in rows.group(1).split("; ")}
    assert stated == set(FLOOR_LAYOUTS), (
        f"table rows {sorted(stated)} are not the own layouts {sorted(FLOOR_LAYOUTS)}"
    )
    read = re.search(r"first legacy-scope read adds up to (\d+)", doc)
    assert read, "the docstring states no first-read count"
    assert "No call is repeated with the same argument." in doc
    return table, int(read.group(1))


def _seed_bucket(store: Path, bucket: str) -> None:
    from scripts.database import db_connection, ensure_initialized

    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, session_id, created_at, "
            "updated_at) VALUES ('m1', 'seeded', ?, 's', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
            (bucket,),
        )
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f', 'src/a.py', ?)", (bucket,))
        conn.commit()


def _fresh_scope_state() -> None:
    for name in (
        "_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_CASE_VARIANT_WARNED",
        "_UNPROVEN_WARNED", "_SUBMODULE_SUPPRESSED",
    ):
        memo = getattr(memory_api, name, None)
        if memo is not None:
            memo.clear()


@pytest.fixture(scope="module")
def git_calls(tmp_path_factory):
    """{(layout, route): (detection calls, first-read calls, repeated calls)}
    over EVERY shape (an own layout keyed by its table row, any other shape by
    its name), and {layout outcome: the own layouts that reached it} over the
    same runs."""
    import subprocess

    real = subprocess.run
    measured = {}
    predicates = _layout_predicates()
    outcomes = _layout_outcomes(predicates)
    operands = collections.defaultdict(dict)
    for (name, line, index), node in outcomes.items():
        if index is not None:
            operands[(name, line)][index] = compile(ast.Expression(node), "<operand>", "eval")
    reached = collections.defaultdict(set)
    current = []

    def tracer(frame, event, arg):
        code = frame.f_code
        if event != "return" or not arg or code.co_name not in predicates:
            return None
        if code.co_filename != pi.__file__:
            return None
        key = (code.co_name, frame.f_lineno)
        if key in operands:
            # The operands are compiled from project_identity's own source and
            # re-evaluated on the returning frame's values, to tell which side
            # of a returned `or` made it true; no outside text is evaluated.
            for index, operand in operands[key].items():
                if eval(operand, frame.f_globals, dict(frame.f_locals)):
                    if current[0] is not None:
                        reached[(*key, index)].add(current[0])
        elif (*key, None) in outcomes:
            if current[0] is not None:
                reached[(*key, None)].add(current[0])
        return None

    own = {shape: layout for layout, shape in FLOOR_LAYOUTS.items()}
    for shape in SHAPES:
        # EVERY SHAPE IS RUN, so a declining shape is held to no repeats too;
        # only the own layouts are counted against the table.
        layout = own.get(shape, shape)
        current[:] = [layout if shape in own else None]
        for route, (run, _sources) in ROUTES.items():
            if route in FORCED_ROUTES:
                continue
            base = tmp_path_factory.mktemp("floor")
            assert_outside_repository(base)
            target = SHAPES[shape](base)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            store = elsewhere / "m.db"
            _seed_bucket(store, target.name)
            calls = []

            def counting(argv, *args, **kwargs):
                if argv and Path(str(argv[0])).name == "git":
                    calls.append((tuple(map(str, argv)), str(kwargs.get("cwd") or Path.cwd())))
                return real(argv, *args, **kwargs)

            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                run(cell_patch, target, elsewhere)  # puts the process on this route
                _fresh_scope_state()
                cell_patch.setattr(subprocess, "run", counting)
                previous = sys.getprofile()
                sys.setprofile(tracer)
                try:
                    memory = PACTMemory(session_id="s", db_path=store)
                finally:
                    sys.setprofile(previous)
                detection = len(calls)
                memory._read_project_scopes()
            repeated = sorted(c for c, n in collections.Counter(calls).items() if n > 1)
            measured[(layout, route)] = (detection, len(calls) - detection, repeated)
    return measured, dict(reached)


class TestTheDocumentedGitCallFloor:
    """The git-call counts PACTMemory.__init__ documents, measured on every
    own layout the shared fixture builds (FLOOR_LAYOUTS, read from SHAPES)
    and every real route, with no override pin (TestTheGitCallFloorWithAPin
    measures the pinned table). The forced routes are left out because their stubs
    replace git answers. Every other shape (a hostile pointer that declines,
    or the accepted external-store bind) has no documented count but is held
    to no repeated call on every route too. The layout set is complete when
    every truthy outcome of the `.git`-layout predicates is reached by some own layout,
    except the outcomes NOT_A_FLOOR_LAYOUT names with its reason."""

    def test_every_rung_runs_the_documented_detection_counts(self, git_calls):
        """Each layout and rung's documented (fewest, most) is exactly the
        measured range over that rung's real routes."""
        measured_calls, _reached = git_calls
        table, _read = _documented_floor()
        measured = collections.defaultdict(list)
        for (layout, route), counts in measured_calls.items():
            if layout in FLOOR_LAYOUTS:
                measured[(layout, _rung_family(route))].append(counts[0])
        wrong = {
            key: ((min(values), max(values)), table[key[0]].get(key[1]))
            for key, values in measured.items()
            if (min(values), max(values)) != table[key[0]].get(key[1])
        }
        assert wrong == {}, f"(measured, documented) detection calls: {wrong}"

    def test_the_first_read_adds_at_most_the_documented_count(self, git_calls):
        measured_calls, _reached = git_calls
        _table, read = _documented_floor()
        assert max(
            counts[1] for (layout, _route), counts in measured_calls.items()
            if layout in FLOOR_LAYOUTS
        ) == read

    def test_no_route_repeats_a_git_call(self, git_calls):
        """No git call is repeated with the same argument on any cell: EVERY
        SHAPES entry (own layouts, declining hostile shapes and accepted binds)
        on every real route, detection and first read together. The count
        table covers the own layouts only; this arm covers every shape."""
        measured_calls, _reached = git_calls
        repeated = {cell: counts[2] for cell, counts in measured_calls.items() if counts[2]}
        assert repeated == {}, f"repeated git calls on (shape or layout, route): {sorted(repeated)}"

    def test_every_documented_rung_has_a_real_route(self):
        table, _read = _documented_floor()
        families = {_rung_family(route) for route in ROUTES if route not in FORCED_ROUTES}
        for layout, rungs in table.items():
            assert set(rungs) == families, (layout, set(rungs), families)

    def test_the_own_layouts_reach_every_layout_outcome(self, git_calls):
        """Every truthy outcome of every `.git`-layout predicate is reached by
        some own layout, so a layout the identity code distinguishes cannot
        be missing from the floor. NOT_A_FLOOR_LAYOUT names the predicates
        whose outcomes no own layout can reach, and each must still be one."""
        _measured, reached = git_calls
        predicates, unresolved = _layout_predicate_forms()
        assert unresolved == [], f"a layout-predicate form the walk cannot read: {unresolved}"
        assert {"_marker_names_external_git_dir", "_submodule_checkout"} <= predicates  # control
        assert set(NOT_A_FLOOR_LAYOUT) <= predicates, set(NOT_A_FLOOR_LAYOUT) - predicates
        outcomes = _layout_outcomes(predicates)
        unreached = sorted(
            key for key in outcomes
            if key[0] not in NOT_A_FLOOR_LAYOUT and not reached.get(key)
        )
        assert unreached == [], f"a layout outcome no own layout reaches: {unreached}"


# --- the git-call floor with an override pin --------------------------------

FLOOR_PIN = "github.com/pinned/floor"
# The pinned table's row name for the own layout that binds only through a
# pin: a checkout made with `git init --separate-git-dir=<dir>/.git`.
DOT_GIT_STORE = "dot-git store checkout"


def _own_dot_git_store(base: Path) -> Path:
    """The user's own checkout whose separate git dir is named `.git`: its
    `.git` file names a directory called `.git`, the redirect shape, so the
    origin is not read and only a pin binds it."""
    store = base / "store" / ".git"
    store.parent.mkdir()
    run_git("init", "-q", f"--separate-git-dir={store}", str(base / "target"), cwd=base)
    run_git("remote", "add", "origin", OWN, cwd=base / "target")
    run_git(
        "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
        "--allow-empty", "-m", "i", cwd=base / "target",
    )
    (base / "target" / "sub").mkdir()
    return base / "target"


def _detection_calls(tmp_path_factory, build, pin: bool) -> dict:
    """{route: (git calls in one construction, bound id)} for one layout on
    every real route, measured as `git_calls` measures detection."""
    import subprocess

    real = subprocess.run
    measured = {}
    for route, (run, _sources) in ROUTES.items():
        if route in FORCED_ROUTES:
            continue
        base = tmp_path_factory.mktemp("pinned_floor")
        assert_outside_repository(base)
        target = build(base)
        if pin:
            assert plant_unusable_pin(target, FLOOR_PIN + "\n") is not None, route
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        store = elsewhere / "m.db"
        _seed_bucket(store, target.name)
        calls = []

        def counting(argv, *args, **kwargs):
            if argv and Path(str(argv[0])).name == "git":
                calls.append(argv)
            return real(argv, *args, **kwargs)

        with pytest.MonkeyPatch.context() as cell_patch:
            _clear(cell_patch)
            run(cell_patch, target, elsewhere)  # puts the process on this route
            _fresh_scope_state()
            cell_patch.setattr(subprocess, "run", counting)
            memory = PACTMemory(session_id="s", db_path=store)
        measured[route] = (len(calls), memory.project_id)
    return measured


@pytest.fixture(scope="module")
def pinned_git_calls(tmp_path_factory):
    """({(layout, route): (detection calls, bound id)} with a canonical pin
    planted, over every own layout and the dot-git store checkout, and
    {route: (calls, bound id)} for that checkout unpinned)."""
    builds = {layout: SHAPES[shape] for layout, shape in FLOOR_LAYOUTS.items()}
    builds[DOT_GIT_STORE] = _own_dot_git_store
    pinned = {
        (layout, route): counts
        for layout, build in builds.items()
        for route, counts in _detection_calls(tmp_path_factory, build, True).items()
    }
    return pinned, _detection_calls(tmp_path_factory, _own_dot_git_store, False)


def _documented_pinned_floor() -> dict:
    """{layout: {rung: (fewest, most)}} from the docstring's pinned table,
    after checking it states that the main table assumes no pin."""
    import re

    doc = " ".join(PACTMemory.__init__.__doc__.split())
    assert "These counts assume no override pin." in doc
    rows = re.search(
        r"With a canonical override pin, which answers before the origin is "
        r"read, the counts are: (.+?)\.(?: |$)", doc,
    )
    assert rows, "the docstring has no pinned git-call table"
    table = {}
    for row in rows.group(1).split("; "):
        layout, counts = row.split(": ", 1)
        table[layout] = {
            rung.strip(): (int(low), int(high or low))
            for rung, low, high in re.findall(r"([A-Za-z_ ]+?) (\d+)(?:-(\d+))?(?:, |$)", counts)
        }
    return table


class TestTheGitCallFloorWithAPin:
    """The main table assumes no override pin. A canonical pin answers before
    the origin is read, so the docstring states a second table, measured here
    on every own layout and real route, plus the dot-git store checkout,
    which declines without a pin and binds only through one."""

    def test_every_pinned_layout_runs_the_documented_counts(self, pinned_git_calls):
        pinned, _unpinned = pinned_git_calls
        ranges = collections.defaultdict(list)
        for (layout, route), (calls, _answer) in pinned.items():
            ranges[(layout, _rung_family(route))].append(calls)
        measured = collections.defaultdict(dict)
        for (layout, rung), values in ranges.items():
            measured[layout][rung] = (min(values), max(values))
        assert dict(measured) == _documented_pinned_floor()

    def test_every_pinned_layout_binds_the_pin(self, pinned_git_calls):
        pinned, _unpinned = pinned_git_calls
        wrong = {cell: answer for cell, (_calls, answer) in pinned.items() if answer != FLOOR_PIN}
        assert wrong == {}, f"a pinned layout did not bind its pin: {wrong}"

    def test_the_dot_git_store_declines_without_a_pin(self, pinned_git_calls):
        _pinned, unpinned = pinned_git_calls
        assert {answer for _calls, answer in unpinned.values()} == {None}, unpinned


# --- a repository pin outranks the user setting ----------------------------

PIN_ID = "github.com/pinned/project"


@pytest.fixture(scope="module")
def pinned_answers(tmp_path_factory):
    """{(own layout, route): answer} with a CANONICAL pin planted in the
    layout's common dir, on every route, the user-setting route included."""
    answers = {}
    for layout, shape in FLOOR_LAYOUTS.items():
        for route, (run, _sources) in ROUTES.items():
            base = tmp_path_factory.mktemp("pinned")
            assert_outside_repository(base)
            target = SHAPES[shape](base)
            assert plant_unusable_pin(target, PIN_ID + "\n") is not None, (layout, route)
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                answers[(layout, route)] = _answer(run(cell_patch, target, elsewhere))
    return answers


class TestARepositoryPinOutranksTheUserSetting:
    """A canonical pin binds on every route, so no rung (the user setting
    over a cwd marker included) can answer before the repository the route
    finds. The unusable-pin report on the same route is the parity matrix's
    override column."""

    def test_every_route_binds_the_pin(self, pinned_answers):
        wrong = {
            cell: answer for cell, answer in pinned_answers.items()
            if answer != (f"bound:{PIN_ID}", "override file")
        }
        assert wrong == {}, f"a route did not bind the repository pin: {wrong}"

    def test_the_user_setting_route_is_measured(self, pinned_answers):
        """Control: the route that sets a user setting is one of the cells."""
        assert any("user setting" in route for _layout, route in pinned_answers)
