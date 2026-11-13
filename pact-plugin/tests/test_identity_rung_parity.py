"""Every identity rung gives a hostile `.git` target the same answer.

Location: pact-plugin/tests/test_identity_rung_parity.py

The resolver reaches a checkout through several rungs: the git root of the
process cwd, a declared directory (`CLAUDE_PROJECT_DIR`, or the session
record), the worktree identity record of a gone declaration, and the
cwd-marker walk. Each rung runs the repository guards before it derives an
id. A test per rung proves each guard that EXISTS is tested; it cannot show a
guard one rung LACKS while its siblings have it. This module compares the
rungs side by side instead: every hostile target shape is run through every
route, and every route that reaches the shape must give it the same answer
(the decline reason, or the bound id for a benign control).

What is derived from the code, not typed here:
- the rungs: every call site of `_root_guard` and `_identity_at_root` in the
  resolver must be executed by some cell of the matrix;
- the guard arms: every decline reason `_root_guard` can return must be
  produced by some cell;
- the identity layer's `.git`-marker predicates: every predicate
  `detect_id_for_root` applies to the asked root must answer True for some
  cell;
- the reasons: every reason measured must be one the code registers;
- the identity callers: every call of detect_id_for_root, _identity_at_root,
  main_repo_root or main_repo_common_dir in the memory scripts and the
  backlog writer must execute while the routes run, or be listed in
  NO_IDENTITY_BOUND with the reason it binds no store key.

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
    make_repo,
    qualified_name,
    run_git,
)

from scripts import memory_api
from scripts import project_identity as pi
from scripts.memory_api import PACTMemory

VICTIM = "https://github.com/victim/private.git"
VICTIM_ID = "github.com/victim/private"
OWN = "https://github.com/own/project.git"


# --- target shapes ---------------------------------------------------------
# Each builder returns the checkout root a route is pointed at. Hostile
# shapes redirect the checkout's `.git` at a git directory that is not its
# own; the controls are genuine checkouts every rung must bind alike.


def _victim(base: Path) -> Path:
    return make_repo(base / "victim", remote=VICTIM)


def _forged_worktree_admin(base: Path) -> Path:
    victim = _victim(base)
    run_git("worktree", "add", "-q", str(base / "victim-wt"), "-b", "wt", cwd=victim)
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(
        f"gitdir: {victim / '.git' / 'worktrees' / 'victim-wt'}\n", encoding="utf-8"
    )
    return target


def _foreign_gitdir(base: Path) -> Path:
    victim = _victim(base)
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
    return target


def _symlinked_git(base: Path) -> Path:
    victim = _victim(base)
    target = base / "target"
    target.mkdir()
    (target / ".git").symlink_to(victim / ".git")
    return target


def _dangling_gitfile(base: Path) -> Path:
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(f"gitdir: {base / 'missing'}\n", encoding="utf-8")
    return target


def _bare_target(base: Path) -> Path:
    run_git("init", "-q", "--bare", str(base / "victim.git"), cwd=base)
    run_git("remote", "add", "origin", VICTIM, cwd=base / "victim.git")
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(f"gitdir: {base / 'victim.git'}\n", encoding="utf-8")
    return target


def _foreign_external_store(base: Path) -> Path:
    run_git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "victim"), cwd=base)
    run_git("remote", "add", "origin", VICTIM, cwd=base / "victim")
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(f"gitdir: {base / 'store.git'}\n", encoding="utf-8")
    return target


def _own_checkout(base: Path) -> Path:
    return make_repo(base / "target", remote=OWN)


def _own_linked_worktree(base: Path) -> Path:
    main = make_repo(base / "main", remote=OWN)
    run_git("worktree", "add", "-q", str(base / "target"), "-b", "wt", cwd=main)
    return base / "target"


SHAPES = {
    "forged_worktree_admin": _forged_worktree_admin,
    "foreign_gitdir": _foreign_gitdir,
    "symlinked_git": _symlinked_git,
    "dangling_gitfile": _dangling_gitfile,
    "bare_target": _bare_target,
    "foreign_external_store": _foreign_external_store,
    "control_own_checkout": _own_checkout,
    "control_own_linked_worktree": _own_linked_worktree,
}


# --- routes ----------------------------------------------------------------
# Each route points one rung at the target and returns the detected identity.
# `elsewhere` is a plain folder outside any repository.


def _clear(monkeypatch):
    for name in ("CLAUDE_PROJECT_DIR", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    memory_api._LEGACY_SCOPE_CACHE.clear()


def _detect():
    return PACTMemory._detect_project_identity_with_legacy()


def _git_root(monkeypatch, target, elsewhere):
    monkeypatch.chdir(target)
    return _detect()


def _git_root_subdirectory(monkeypatch, target, elsewhere):
    monkeypatch.chdir(target / "sub")
    return _detect()


def _declared(monkeypatch, target, elsewhere):
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(target))
    return _detect()


def _declared_subdirectory(monkeypatch, target, elsewhere):
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(target / "sub"))
    return _detect()


def _session_record(monkeypatch, target, elsewhere):
    """FORCED: the session record is inert in a test process."""
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(memory_api, "get_project_dir_from_session_record", lambda: str(target))
    return _detect()


def _worktree_identity_record(monkeypatch, target, elsewhere):
    """FORCED: a record the record check accepts names a verified main
    checkout, so the record check is made to return the target."""
    from shared import project_scope

    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere / "gone-worktree"))
    monkeypatch.setattr(project_scope, "recorded_main_root", lambda declared, identity: target)
    return _detect()


def _cwd_marker(monkeypatch, target, elsewhere):
    """A `GIT_CEILING_DIRECTORIES` entry at the checkout root hides it from
    git and from the marker walk, so resolution reaches the cwd-marker rung."""
    monkeypatch.chdir(target / "sub")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(target))
    return _detect()


# route -> (function, the decline/identity sources that show this rung answered)
ROUTES = {
    "git root": (_git_root, {"git root", "origin remote"}),
    "git root (subdirectory)": (_git_root_subdirectory, {"git root", "origin remote"}),
    "CLAUDE_PROJECT_DIR": (_declared, {"CLAUDE_PROJECT_DIR"}),
    "CLAUDE_PROJECT_DIR (subdirectory)": (_declared_subdirectory, {"CLAUDE_PROJECT_DIR"}),
    "session record (forced)": (_session_record, {"session record"}),
    # The guard's decline names the record's rung; an identity-layer decline
    # names the declaration that asked, as on the declared rung.
    "worktree identity record (forced)": (
        _worktree_identity_record, {"worktree identity record", "CLAUDE_PROJECT_DIR"},
    ),
    "cwd marker": (_cwd_marker, {"cwd"}),
}


def _answer(detected) -> tuple:
    """(answer, answering rung): the decline reason, or the bound id."""
    record = detected.decline_reason or {}
    if record:
        return record.get("reason"), record.get("source")
    return f"bound:{detected.project_id}", detected.source


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


def _marker_predicates() -> set:
    """The `.git`-marker predicates the identity layer applies to the asked
    root: every function called on `asked` in an `if` test of
    `detect_id_for_root`."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    detect = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "detect_id_for_root"
    )
    names = set()
    for node in ast.walk(detect):
        if not isinstance(node, ast.If):
            continue
        for call in ast.walk(node.test):
            if (
                isinstance(call, ast.Call)
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "asked"
                and isinstance(call.func, ast.Name)
            ):
                names.add(call.func.id)
    return names


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    """{(shape, route): (answer, answering rung)}, the call sites hit, and the
    marker predicates that answered True."""
    sites = _resolver_call_sites()
    path = memory_api.__file__
    predicates = _marker_predicates()
    fired = set()

    def tracer(frame, event, arg):
        if event == "return" and frame.f_code.co_name in predicates and arg is True:
            fired.add(frame.f_code.co_name)
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
            (target / "sub").mkdir(exist_ok=True)
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
            cells[(shape, route)] = _answer(detected)
    return cells, sites, fired


# Routes that do not reach a shape, by measurement, with the rung that
# answered instead. A route listed here for a shape is excluded from that
# shape's comparison, and the test checks the exclusion still holds.
NOT_REACHED: dict = {}

# Hostile shapes every rung binds alike, by design. Binding a `.git` that
# names an external git directory is a user-ruled behaviour: such a file is
# the same shape as the user's own `--separate-git-dir` checkout (git records
# no worktree there), and forging one needs the victim's absolute path. The
# id is derived and only the override path is withheld; the skill makes
# pinning that git directory the user's decision.
ACCEPTED_BINDS = {"foreign_external_store"}


def _reached(cells) -> dict:
    """{shape: {route: answer}} for the cells whose own rung answered."""
    by_shape = collections.defaultdict(dict)
    for (shape, route), (answer, rung) in cells.items():
        if rung in ROUTES[route][1]:
            by_shape[shape][route] = answer
    return by_shape


class TestEveryRungGivesAShapeTheSameAnswer:
    def test_every_route_that_reaches_a_shape_agrees(self, matrix):
        cells, _sites, _fired = matrix
        disagreements = {
            shape: answers
            for shape, answers in _reached(cells).items()
            if len(set(answers.values())) > 1
        }
        assert disagreements == {}, disagreements

    def test_every_unreached_cell_is_a_recorded_exception(self, matrix):
        cells, _sites, _fired = matrix
        unreached = {
            (shape, route): rung
            for (shape, route), (_answer_, rung) in cells.items()
            if rung not in ROUTES[route][1]
        }
        assert unreached == NOT_REACHED

    def test_no_hostile_shape_binds_the_victim(self, matrix):
        cells, _sites, _fired = matrix
        bound = {
            cell: answer for cell, (answer, _rung) in cells.items()
            if answer == f"bound:{VICTIM_ID}" and cell[0] not in ACCEPTED_BINDS
        }
        assert bound == {}

    def test_every_accepted_bind_still_binds(self, matrix):
        """An accepted shape that starts declining is no longer an exception."""
        cells, _sites, _fired = matrix
        for shape in ACCEPTED_BINDS:
            answers = {a for (s, _route), (a, _r) in cells.items() if s == shape}
            assert answers == {f"bound:{VICTIM_ID}"}, (shape, answers)


class TestTheMatrixCoversTheCode:
    def test_every_rung_call_site_is_executed(self, matrix):
        _cells, sites, _fired = matrix
        unexecuted = [key for key, hits in sites.items() if hits == 0]
        assert unexecuted == [], f"a rung no route reaches: {unexecuted}"

    def test_every_guard_arm_is_produced(self, matrix):
        cells, _sites, _fired = matrix
        produced = {answer for answer, _rung in cells.values()}
        assert _guard_reasons() - produced == set()

    def test_every_marker_predicate_fires(self, matrix):
        """Each `.git`-marker predicate the identity layer applies answers
        True for some shape, so a new predicate needs a shape here."""
        _cells, _sites, fired = matrix
        assert _marker_predicates() - fired == set()

    def test_every_measured_reason_is_registered(self, matrix):
        cells, _sites, _fired = matrix
        reasons = {a for a, _r in cells.values() if not a.startswith("bound:")}
        assert reasons <= set(pi.DECLINE_REASONS)


# --- every identity caller in the memory scripts ---------------------------
# The rung list above comes from memory_api's resolver. A new caller of the
# identity functions anywhere else (another memory script, or the backlog
# writer) would derive an identity no route here exercises. So every call
# site of these functions in those files must either execute while the
# routes run, or be listed with the reason it binds no store key.

IDENTITY_FUNCTIONS = frozenset(
    {"detect_id_for_root", "_identity_at_root", "main_repo_root", "main_repo_common_dir"}
)

PLUGIN_ROOT = Path(memory_api.__file__).resolve().parents[3]


def _identity_caller_files() -> list:
    scripts = sorted((PLUGIN_ROOT / "skills" / "pact-memory" / "scripts").glob("*.py"))
    return scripts + [PLUGIN_ROOT / "hooks" / "shared" / "backlog.py"]


# (file relative to the plugin root, enclosing function, callee) -> why the
# site binds no store key.
NO_IDENTITY_BOUND = {
    ("skills/pact-memory/scripts/cli.py", "cmd_project_id", "main_repo_root"): (
        "display only: the show envelope's process_repo_root"
    ),
    ("hooks/shared/backlog.py", "project_root", "main_repo_root"): (
        "the backlog file's directory; the file is keyed by the pre-change "
        "folder name by design"
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_location_divergence_warning", "main_repo_root"): (
        "compares the process's repository with the id already bound; binds nothing"
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_read_project_scopes", "main_repo_common_dir"): (
        "the common dir of the root the resolver already chose, for the legacy gate"
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_legacy_suppression", "main_repo_common_dir"): (
        "discloses a suppressed legacy name for the root the resolver already chose"
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
    """{(file, enclosing function, callee)} for every call of an identity
    function in the memory scripts and the backlog writer, module-level code
    included (`<module>`)."""
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
                        sites.add((rel, function, callee))
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
def identity_callers(tmp_path_factory):
    """(call sites executed while every route runs, {shape: cwd_repo id})."""
    files = {str(path): path.relative_to(PLUGIN_ROOT).as_posix() for path in _identity_caller_files()}
    executed = set()

    def tracer(frame, event, arg):
        if event == "call" and frame.f_code.co_name in IDENTITY_FUNCTIONS:
            caller = frame.f_back
            if caller is not None and caller.f_code.co_filename in files:
                executed.add((files[caller.f_code.co_filename], caller.f_code.co_name, frame.f_code.co_name))
        return None

    cwd_ids = {}
    for shape, build in SHAPES.items():
        runs = [(route, run) for route, (run, _sources) in ROUTES.items()] + [("cwd_repo", _cwd_repo)]
        for route, run in runs:
            base = tmp_path_factory.mktemp("callers")
            assert_outside_repository(base)
            target = build(base)
            (target / "sub").mkdir(exist_ok=True)
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


class TestEveryIdentityCallerIsCovered:
    def test_every_call_site_runs_or_binds_nothing(self, identity_callers):
        executed, _cwd_ids = identity_callers
        uncovered = _identity_call_sites() - executed - set(NO_IDENTITY_BOUND)
        assert uncovered == set(), (
            "an identity call site no route executes and no reason excuses: "
            f"{sorted(uncovered)}"
        )

    def test_every_excused_site_still_exists(self):
        stale = set(NO_IDENTITY_BOUND) - _identity_call_sites()
        assert stale == set(), f"an excused call site is gone: {sorted(stale)}"

    def test_the_cwd_repository_never_names_the_victim(self, identity_callers):
        _executed, cwd_ids = identity_callers
        named = {
            shape: found for shape, found in cwd_ids.items()
            if found == VICTIM_ID and shape not in ACCEPTED_BINDS
        }
        assert named == {}
