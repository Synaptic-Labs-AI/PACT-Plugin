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
import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# A truth test of NotImplemented warns now and raises from Python 3.14, so a
# profile function that reads a return value it has not checked fails here.
pytestmark = pytest.mark.filterwarnings(
    "error:NotImplemented should not be used in a boolean context:DeprecationWarning"
)

from fixtures.code_readers import (
    _CalleeReader,
    _DOTTED_FORMS,
    _IDENTIFIER_FORMS,
    _SCOPES,
    _SCOPE_WRAPPERS,
    _TYPE_PARAM_OWNERS,
    _TYPE_PARAM_SPELLINGS,
    _binding_probes,
    _callee_readers,
    _code_objects,
    _implicit_bindings,
    _resolver_walk,
    _stored_names,
    call_result_callees,
    chooser_elements,
    dynamic_access_forms,
    dynamic_reaches,
    module_string_constants,
    name_values,
    named_anchor_strings,
    unread_bindings,
)
from fixtures.identity_git import (
    assert_outside_repository,
    bindings,
    import_aliases,
    make_repo,
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
    home_for,
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

    A reason read from the arm chooser's result (`_root_guard_arm`) is each
    reason its returns name (`chooser_elements`). FAIL CLOSED: a `_decline`
    call whose reason is neither a string literal (positional or `reason=`)
    nor such an element, a `_decline` used other than as a called name, and
    a `return` that is neither None nor such a call are listed as
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
            continue
        found, more = chooser_elements(tree, reason) if reason is not None else (set(), ["no reason"])
        reasons |= found
        unresolved += [f"{call.lineno}: {ast.unparse(call)} ({form})" for form in more]
        if not found and not more:
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


# {home shape: (whether the cwd marker walk from the target, with every `.git`
# git looks past left out, lands on HOME,
# the answer every route gives when the session's checkout is HOME, or None
# when it is not)}, measured with the filesystem and git by the matrix fixture.
HOME_FACTS: dict = {}

# [(home shape, `.git` directory the walk tested, whether `_home_facts`
# judged git looked past it, whether memory_api's `_git_skips_marker` does
# on git's answers there)], filled with HOME_FACTS, so the two rules cannot
# drift apart without a test noticing.
HOME_SKIPS: list = []


def _git_looked_past(walk: Path, top: str, common: str) -> bool:
    """The home walk's own judgement, from git's answers asked at `walk`,
    that git looked past the `.git` directory there: git names another top
    level and a common dir other than that `.git`. A `.git` git uses as a
    linked worktree's git dir has a common dir elsewhere but names `walk`
    as its top level, so it is not looked past."""
    return bool(
        top and common and Path(top).resolve() != walk
        and Path(common).resolve() != (walk / ".git").resolve()
    )


def _home_facts(shape: str, target: Path) -> None:
    """Record, once per home shape, where the cwd marker walk lands and
    whether git's toplevel for the target, or the target itself, is HOME.
    The walk leaves out a `.git` directory when git, asked there, names
    another top level and a common dir other than that `.git`: git looked
    past it, so it is no marker."""
    import subprocess

    def ask(where, *flags):
        return subprocess.run(
            ["git", "-C", str(where), "rev-parse", *flags],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.strip()

    home = home_for(shape, target)
    if home is None or shape in HOME_FACTS:
        return
    home = home.resolve()
    walk = PACTMemory._find_project_root(target / "sub").resolve()
    while walk != home and (walk / ".git").is_dir():
        top = ask(walk, "--show-toplevel")
        common = ask(walk, "--path-format=absolute", "--git-common-dir")
        other_marker = (walk / ".claude").is_dir() or (walk / "CLAUDE.md").exists()
        skipped = _git_looked_past(walk, top, common)
        if not other_marker:
            HOME_SKIPS.append((shape, walk, skipped, memory_api._git_skips_marker(
                walk, Path(common) if common else None, ask(walk, "--absolute-git-dir") or None,
            )))
        if other_marker or not skipped:
            break
        walk = PACTMemory._find_project_root(walk.parent).resolve()
    toplevel = ask(target, "--show-toplevel")
    at_home = target.resolve() == home or (toplevel and Path(toplevel).resolve() == home)
    HOME_FACTS[shape] = (walk == home, f"bound:{home.name}" if at_home else None)


def test_a_git_dir_whose_common_dir_is_elsewhere_is_not_looked_past(tmp_path):
    """A `.git` directory that is a linked worktree's git dir (its
    `commondir` names another repository's `.git`) is one git uses: asked
    there, git names the folder as its top level, that `.git` as its git
    dir, and the other repository's `.git` as its common dir. The home
    walk's judgement and memory_api's `_git_skips_marker` must both keep it
    as a marker. The home shapes do not build this layout, so it is the one
    place the walk's top-level comparison decides.

    RED WHEN the walk's judgement drops the top-level comparison, or either
    rule stops reading the git dir git names."""
    import os
    import shutil

    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(where, *args):
        return subprocess.run(
            ["git", "-C", str(where), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            capture_output=True, text=True, env=env, timeout=30, check=True,
        ).stdout.strip()

    base = tmp_path.resolve()
    assert_outside_repository(base)
    main = base / "main"
    git(base, "init", "-q", str(main))
    git(main, "commit", "-q", "--allow-empty", "-m", "seed")
    git(main, "worktree", "add", "-q", str(base / "wt"), "-b", "wt")
    walk = base / "folder"
    walk.mkdir()
    shutil.move(str(main / ".git" / "worktrees" / "wt"), str(walk / ".git"))
    shutil.rmtree(base / "wt")
    (walk / ".git" / "commondir").write_text(f"{main / '.git'}\n", encoding="utf-8")

    top = git(walk, "rev-parse", "--show-toplevel")
    common = git(walk, "rev-parse", "--path-format=absolute", "--git-common-dir")
    git_dir = git(walk, "rev-parse", "--absolute-git-dir")
    # control: the layout is the one where only the top-level comparison decides
    assert (Path(top), Path(git_dir), Path(common)) == (walk, walk / ".git", main / ".git")
    assert (
        _git_looked_past(walk, top, common),
        memory_api._git_skips_marker(walk, Path(common), git_dir),
    ) == (False, False)


def _set_home(monkeypatch, shape: str, target: Path) -> None:
    """Point HOME at the directory a `home_` shape names."""
    home = home_for(shape, target)
    if home is not None:
        monkeypatch.setenv("HOME", str(home))


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
    defs = {
        node.name: node for node in ast.parse(Path(pi.__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    refusals = {name for name in predicates if _return_kind(defs[name], defs)[0] == "refusal"}
    fired = set()
    files = {str(file): file.relative_to(PLUGIN_ROOT).as_posix() for file in _identity_caller_files()}
    executed = set()

    def tracer(frame, event, arg):
        if event == "return" and frame.f_code.co_name in predicates and (
            arg is True or (frame.f_code.co_name in refusals and isinstance(arg, str))
        ):
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
            _home_facts(shape, target)
            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                _set_home(cell_patch, shape, target)
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


def _setting_at_home(shape: str, route: str) -> bool:
    """Whether the cell is the user-setting route at a home shape whose cwd
    marker walk lands on HOME. That walk's HOME landing never asks git, so
    the user setting binds there by design while every other route lands on
    the folder name; measured, not typed: the route is the one whose sources
    include "user setting", the shape one whose walk HOME_FACTS records as
    landing on HOME."""
    return "user setting" in ROUTES[route][1] and HOME_FACTS.get(shape, (False, None))[0]


# Shapes whose routes see different repositories by design: a folder inside a
# bare repository, where git asked from the folder finds the bare repository
# (no working tree, so those routes decline) while the cwd-marker routes,
# whose ceiling hides it, find no repository and bind the folder. They are
# left out of the agreement arm; each must still disagree there, and each
# stray `.git` shape must answer on every route exactly as its no-`.git`
# twin (`test_a_stray_git_answers_as_the_same_folder_with_none`).
SPLIT_BY_VISIBILITY = {
    "stray_dotgit_under_bare_repository": "no_dotgit_under_bare_repository",
    "no_dotgit_under_bare_repository": None,
}


def _git_derived_decline(target: Path):
    """The decline git's own answers about `target` predict, independently
    of the resolver, or None when they predict none: no git dir answer ->
    `broken_git_marker`; a bare repository -> `no_working_tree`; a
    `remote.origin.url` none of whose values comes from the common dir's
    own config file -> `origin_unrepresentable`."""
    import os
    import subprocess

    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, env=env, timeout=10)

    if git("-C", str(target), "rev-parse", "--absolute-git-dir").returncode != 0:
        return "broken_git_marker"
    if git("-C", str(target), "rev-parse", "--is-bare-repository").stdout.strip() == b"true":
        return "no_working_tree"
    common = git("-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0:
        return None
    common_dir = os.fsdecode(common.stdout[:-1])
    urls = git("--git-dir", common_dir, "config", "--show-origin", "-z", "--get-all", "remote.origin.url")
    origins = urls.stdout.split(b"\0")[:-1][0::2] if urls.returncode == 0 else []
    own = os.path.realpath(os.path.join(common_dir, "config"))
    if origins and not any(
        origin.startswith(b"file:") and os.path.realpath(os.fsdecode(origin[len(b"file:"):])) == own
        for origin in origins
    ):
        return "origin_unrepresentable"
    return None

def _reached(cells) -> dict:
    """{shape: {route: (answer, override report)}} for the cells whose own
    rung answered, except the user setting at a home target (see
    `_setting_at_home`, whose cells are checked on their own). The override
    report is (reason, path withheld) or None, so a route that starts or
    stops reading the file, or starts printing its path, disagrees."""
    by_shape = collections.defaultdict(dict)
    for (shape, route), (answer, rung, override) in cells.items():
        if (
            rung in ROUTES[route][1] and not _setting_at_home(shape, route)
            and shape not in SPLIT_BY_VISIBILITY
        ):
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

    def test_a_checkout_at_home_lands_on_the_home_folder(self, matrix):
        """Every home shape whose checkout git or the target names as HOME
        lands on the home folder, with no override read, on every route that
        reaches it (the user-setting cells at a walk that lands on HOME are
        checked below). A stray `.git` git skips must not move the landing
        off a home repository."""
        cells, _sites, _fired, _executed = matrix
        landing = {shape: answer for shape, (_walk, answer) in HOME_FACTS.items() if answer}
        assert landing, "no home shape has its checkout at HOME"
        wrong = {
            (shape, route): (answer, override)
            for (shape, route), (answer, rung, override) in cells.items()
            if shape in landing and rung in ROUTES[route][1] and not _setting_at_home(shape, route)
            and (answer, override) != (landing[shape], None)
        }
        assert wrong == {}, f"a checkout at HOME did not land on the home folder: {wrong}"

    def test_the_user_setting_binds_only_at_a_home_target(self, matrix):
        """The cells `_reached` leaves out must answer exactly the user
        setting, with no override read; and there must be some."""
        from fixtures.identity_routes import USER_SETTING_ID

        cells, _sites, _fired, _executed = matrix
        excluded = {cell: cells[cell] for cell in cells if _setting_at_home(*cell)}
        assert excluded, "no home-target cell was measured"
        wrong = {
            cell: answer for cell, answer in excluded.items()
            if answer != (f"bound:{USER_SETTING_ID}", "user setting", None)
        }
        assert wrong == {}, f"a home-target cell left out of the agreement arm answered: {wrong}"

    def test_a_stray_git_answers_as_the_same_folder_with_none(self, matrix):
        """A `.git` git looks past for a bare repository above answers on
        every route exactly as the same folder with no `.git`, and the pair
        splits by route as SPLIT_BY_VISIBILITY states: a route that asks git
        (one whose sources hold no "cwd") declines no_working_tree; a
        cwd-marker route binds a folder name; and the user setting over the
        cwd marker binds the setting."""
        from fixtures.identity_routes import USER_SETTING_ID

        cells, _sites, _fired, _executed = matrix
        wrong = {}
        for shape in SPLIT_BY_VISIBILITY:
            for route, (_run, sources) in ROUTES.items():
                answer, rung, override = cells[(shape, route)]
                if "user setting" in sources:
                    right = (answer, rung) == (f"bound:{USER_SETTING_ID}", "user setting")
                elif "cwd" in sources:
                    right = rung == "cwd" and answer.startswith("bound:") and "/" not in answer
                else:
                    right = answer == "no_working_tree" and rung in sources
                if not right or override is not None:
                    wrong[(shape, route)] = (answer, rung, override)
        assert wrong == {}, f"a route of the bare-repository pair answered outside its class: {wrong}"
        for stray, twin in SPLIT_BY_VISIBILITY.items():
            if twin is None:
                continue
            differ = {
                route: (cells[(stray, route)][0], cells[(twin, route)][0])
                for route in ROUTES
                if cells[(stray, route)][0::2] != cells[(twin, route)][0::2]
            }
            assert differ == {}, f"{stray} differs from {twin}: {differ}"
        for shape in SPLIT_BY_VISIBILITY:
            answers = {cells[(shape, route)][0] for route in ROUTES}
            assert len(answers) > 1, (shape, answers)

    def test_every_declining_shape_answers_its_one_derived_reason(self, matrix, tmp_path_factory):
        """Every shape whose routes all decline, and every shape for which a
        reason is derived, answers that reason on every route. The reason
        has exactly one source: git's own answers about the built target
        (`_git_derived_decline`), or the builder's own declaration of what
        it built (`declines`, for a forged pointer git's answers cannot
        tell from an honest one). A declining shape with no source, a shape
        with both, and a shape that binds where a reason is derived all
        fail. The bare-repository pair splits by design and is held by
        test_a_stray_git_answers_as_the_same_folder_with_none."""
        cells, _sites, _fired, _executed = matrix
        derived, wrong = {}, {}
        for shape, build in SHAPES.items():
            if shape in SPLIT_BY_VISIBILITY:
                continue
            base = tmp_path_factory.mktemp("declines")
            assert_outside_repository(base)
            oracle = _git_derived_decline(build(base))
            declared = getattr(build, "declines", None)
            if oracle is not None and declared is not None:
                wrong[shape] = ("two sources", oracle, declared)
            derived[shape] = oracle or declared
        declining = {
            shape for shape in derived
            if all(not cells[(shape, route)][0].startswith("bound:") for route in ROUTES)
        }
        for shape in sorted(declining | {shape for shape, reason in derived.items() if reason}):
            if derived[shape] is None:
                wrong[shape] = ("no derived reason", sorted({cells[(shape, route)][0] for route in ROUTES}))
                continue
            for route in ROUTES:
                if cells[(shape, route)][0] != derived[shape]:
                    wrong[(shape, route)] = (cells[(shape, route)][0], derived[shape])
        assert wrong == {}, f"a declining shape answered other than its derived reason: {wrong}"
        assert {"no_working_tree", "broken_git_marker"} <= set(derived.values()), derived  # control

    def test_the_home_walk_skips_a_git_dir_where_the_resolver_does(self, matrix):
        """`_home_facts` judges by its own comparison whether git looked past
        a `.git` directory, and that judgement decides which cells the
        agreement tests read. It must agree with memory_api's own rule,
        `_git_skips_marker`, on git's answers at every `.git` the walk
        tested, so neither can change alone.

        RED WHEN either rule changes for a home shape's `.git` and the other
        does not."""
        assert any(skipped for _, _, skipped, _ in HOME_SKIPS), HOME_SKIPS  # control
        assert any(not skipped for _, _, skipped, _ in HOME_SKIPS), HOME_SKIPS  # control
        assert [row for row in HOME_SKIPS if row[2] != row[3]] == []

    def test_the_derived_origin_rule_is_read_origins_rule(self, tmp_path_factory):
        """`_git_derived_decline` derives `origin_unrepresentable` with its
        own reading of `remote.origin.url`; read_origin's own-config-file
        rule (`not_own_file`) is the one the resolver declines on. On every
        shape that has a common dir the two must agree, so neither can
        change alone.

        RED WHEN either rule changes for a shape and the other does not."""
        import subprocess

        verdicts = {}
        for shape, build in SHAPES.items():
            base = tmp_path_factory.mktemp("origin-rule")
            assert_outside_repository(base)
            target = build(base)
            derived = _git_derived_decline(target)
            if derived in ("broken_git_marker", "no_working_tree"):
                continue
            common = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if common.returncode != 0:
                continue
            with pytest.MonkeyPatch.context() as cell_patch:
                _clear(cell_patch)
                status = pi.read_origin(Path(common.stdout.rstrip("\n"))).state
            verdicts[shape] = (derived == "origin_unrepresentable", status == pi.ORIGIN_NOT_OWN_FILE)
        assert any(derived for derived, _ in verdicts.values()), verdicts  # control
        assert any(not derived for derived, _ in verdicts.values()), verdicts  # control
        assert {shape: v for shape, v in verdicts.items() if v[0] != v[1]} == {}

    def test_a_stray_git_with_no_repository_above_declines_on_every_route(self, matrix):
        """A `.git` directory with no repository above gets no answer from
        git, the same None git's errors give, so it stays a marker git
        cannot answer for: every route declines it as a broken marker, the
        user-setting route included, and no override is read."""
        cells, _sites, _fired, _executed = matrix
        answers = {
            route: (answer, override)
            for (shape, route), (answer, _rung, override) in cells.items()
            if shape == "stray_dotgit_without_repository"
        }
        assert set(answers) == set(ROUTES), sorted(answers)
        assert set(answers.values()) == {("broken_git_marker", None)}, answers

    def test_a_line_separator_in_a_path_binds_its_own_origin(self, matrix):
        """A repository whose folder, or whose parent folder, is named with
        every character `str.splitlines()` splits at binds its own origin on
        every route, the cwd-marker routes included: git's answers about it
        hold those characters, and none of them may cut an answer short."""
        cells, _sites, _fired, _executed = matrix
        own = f"bound:{pi.normalize_remote_url(OWN)}"
        shapes = [shape for shape in SHAPES if shape.startswith("control_line_separator_")]
        assert len(shapes) == 2, shapes
        answers = {(shape, route): cells[(shape, route)][0] for shape in shapes for route in ROUTES}
        wrong = {cell: answer for cell, answer in answers.items() if answer != own}
        assert wrong == {}, f"a line separator in a path changed the answer: {wrong}"

    def test_a_path_ending_in_a_space_or_holding_a_newline_binds_its_own_origin(self, matrix):
        """A `--separate-git-dir` checkout whose git dir ends in a space, and a
        linked worktree whose folder ends in a space or holds a newline, bind
        their own origin on every route: no answer about them is stripped or
        split at a line separator."""
        cells, _sites, _fired, _executed = matrix
        own = f"bound:{pi.normalize_remote_url(OWN)}"
        shapes = [shape for shape in SHAPES if shape.startswith("control_path_")]
        assert len(shapes) == 3, shapes
        wrong = {
            (shape, route): cells[(shape, route)][0]
            for shape in shapes for route in ROUTES if cells[(shape, route)][0] != own
        }
        assert wrong == {}, f"a path ending in a space or holding a newline changed the answer: {wrong}"

    def test_an_origin_from_another_file_declines_on_every_route(self, matrix):
        """A linked worktree whose origin comes from a file the repository's
        own config includes (an `includeIf onbranch:`, an `includeIf
        gitdir:`, a plain `include.path`) declines as unrepresentable on
        every route; with the origin set in the repository's own config
        before such an include, every route binds it."""
        cells, _sites, _fired, _executed = matrix
        own = f"bound:{pi.normalize_remote_url(OWN)}"
        included = [shape for shape in SHAPES if shape.startswith("origin_from_")]
        assert len(included) == 3, included
        wrong = {
            (shape, route): cells[(shape, route)][0]
            for shape in included for route in ROUTES
            if cells[(shape, route)][0] != "origin_unrepresentable"
        }
        wrong.update({
            ("control_origin_before_a_gitdir_include", route): answer
            for route in ROUTES
            for answer in [cells[("control_origin_before_a_gitdir_include", route)][0]]
            if answer != own
        })
        assert wrong == {}, f"an included origin answered other than its rule: {wrong}"

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
        True for some shape, and each refusal (a check `_return_kind` reads
        as answering None or a detail) answers a detail for some shape, so a
        new check needs a shape here."""
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
    ("hooks/shared/backlog.py", "_root_and_common_dir", "main_repo_root"): (
        1, "the backlog file's directory; the file is keyed by the pre-change "
        "folder name by design",
    ),
    ("hooks/shared/backlog.py", "_branch_and_worktree_names", "main_repo_common_dir"): (
        1, "the common dir that decides whether the listed worktree folders are "
        "this project's names; binds nothing",
    ),
    ("hooks/shared/backlog.py", "_project_anchor", "main_repo_common_dir"): (
        1, "the declared project directory's common dir, whose worktree listing "
        "gives the backlog's roots; binds nothing",
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_location_divergence_warning", "main_repo_root"): (
        1, "compares the process's repository with the id already bound; binds nothing",
    ),
    ("skills/pact-memory/scripts/memory_api.py", "_legacy_gate_roots", "main_repo_common_dir"): (
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
    resolves by its attribute name. Every other way to reach one (a name in a
    string handed to `getattr`, `eval` or an import, or a namespace such as
    `globals()`) is banned by `_identity_reaches_unread`.
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
    included (`<module>`). FAIL CLOSED: an identity function read other than
    as a call target or the value of a plain assignment (which `_callee`
    resolves through the scope's bindings), for example passed to `partial`
    or stored in a container, is listed in `unresolved`. Only reads count:
    an assignment's target is a name being bound, not a reference."""
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
                    and isinstance(child.ctx, ast.Load)
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


def test_the_callee_reader_refuses_two_files_of_one_stem(tmp_path):
    """Row for `_callee_readers`: two files named `m.py` in different
    directories would share one entry of the imported-names map, so an
    import of `m` could not say which it means. The reader refuses the
    population instead of reading one file's names as the other's."""
    import pytest

    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "m.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="share a module stem"):
        _callee_readers(sorted(tmp_path.rglob("m.py")))


def _identity_modules() -> set:
    """The names an import of a module defining an identity function can
    carry: each such module's name and its package's."""
    names = set()
    for path in _identity_caller_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in IDENTITY_FUNCTIONS
            for node in ast.walk(tree)
        ):
            names |= {path.stem, path.parent.name}
    return names


def _identity_reached_by(kind, value) -> bool:
    """Whether a name, line of code or module name handed to a dynamic
    access form can name an identity function."""
    if kind == "attribute":
        return value in IDENTITY_FUNCTIONS
    if kind == "code":
        return any(name in value for name in IDENTITY_FUNCTIONS)
    return bool(set(value.split(".")) & _identity_modules())


@pytest.fixture(scope="module")
def identity_dynamic_forms():
    return dynamic_access_forms(_identity_caller_files())


def _identity_reaches_unread(forms) -> list:
    """Each place the memory scripts and the backlog writer can reach an
    identity function through a form the call-site census does not read."""
    return dynamic_reaches(_identity_caller_files(), forms, _identity_reached_by)


def _module_defs(path) -> set:
    """The names of the module-level functions defined in the file at `path`."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _reached_by_name(anchors, module_names):
    """`reaches_by_name` for a census whose anchor is the names `anchors`,
    defined in the modules `module_names`."""
    def reached(kind, value):
        if kind == "attribute":
            return value in anchors
        if kind == "code":
            return any(name in value for name in anchors)
        return bool(set(value.split(".")) & module_names)
    return reached


# The other censuses that read their anchor by name in one module: the rung
# call sites and the guard's decline reasons in memory_api, and the marker
# and layout predicates among project_identity's functions.
_NAMED_ANCHOR_CENSUSES = {
    "rungs and decline reasons": (
        lambda: Path(memory_api.__file__), lambda: {*RUNGS, "_decline"}, {"memory_api", "scripts"},
    ),
    "marker and layout predicates": (
        lambda: Path(pi.__file__), lambda: _module_defs(pi.__file__), {"project_identity", "scripts"},
    ),
}


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
                _set_home(cell_patch, shape, target)
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

    def test_no_identity_function_is_reached_through_an_unread_form(self, identity_dynamic_forms):
        """FAIL CLOSED: a string name, a line of code, a module name or a
        namespace that can reach an identity function is a call the census
        cannot see."""
        assert _identity_reaches_unread(identity_dynamic_forms) == []

    def test_the_unread_form_ban_sees_the_scope_and_spares_honest_names(self, identity_dynamic_forms):
        """Control: the forms come from the probe (the attribute, namespace,
        code and import builtins among them), every use of one in the scope
        is found, and the uses with a known name that is no identity
        function (`getattr(args, ...)`, `getattr(record, ...)`) are spared."""
        assert {
            "builtins.getattr", "builtins.setattr", "builtins.delattr", "builtins.hasattr",
            "builtins.vars", "builtins.globals", "builtins.locals", "builtins.exec",
            "builtins.eval", "builtins.compile", "builtins.__import__",
            "importlib.import_module", ".__dict__", ".__getattribute__",
        } <= set(identity_dynamic_forms)
        every_use = dynamic_reaches(
            _identity_caller_files(), identity_dynamic_forms, lambda kind, value: True,
        )
        def getattr_of(use):
            """The object `getattr` reads in the use's code, or None."""
            try:
                call = ast.parse(use[2], mode="eval").body
            except SyntaxError:
                return None
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "getattr"
                    and call.args and isinstance(call.args[0], ast.Name)):
                return call.args[0].id
            return None

        honest = [use for use in every_use if use[0] == "backlog.py" and getattr_of(use) in ("args", "record")]
        assert {getattr_of(use) for use in honest} == {"args", "record"}
        assert not set(honest) & set(_identity_reaches_unread(identity_dynamic_forms))

    @pytest.mark.parametrize("census", sorted(_NAMED_ANCHOR_CENSUSES))
    def test_no_named_anchor_is_reached_through_an_unread_form(self, census):
        """FAIL CLOSED, for each census that reads its anchor by name in one
        module: a string name, a line of code, a module name or a namespace
        that can reach an anchor is a reference the census cannot see.
        Control: the anchor set is not empty."""
        path, anchors, module_names = _NAMED_ANCHOR_CENSUSES[census]
        names = anchors()
        assert names, census
        forms = dynamic_access_forms([path()])
        assert dynamic_reaches([path()], forms, _reached_by_name(names, module_names)) == []

    def test_no_string_names_an_identity_function(self):
        """FAIL CLOSED: a string naming an identity function can reach it
        through a callable the probe does not model
        (`operator.methodcaller("__getattribute__", name)`,
        `pkgutil.resolve_name("module:name")`)."""
        assert named_anchor_strings(_identity_caller_files(), IDENTITY_FUNCTIONS.__contains__) == []

    @pytest.mark.parametrize("census", sorted(_NAMED_ANCHOR_CENSUSES))
    def test_no_string_names_a_named_anchor(self, census):
        """FAIL CLOSED, for each census that reads its anchor by name in one
        module: a string naming an anchor. Control: the anchor set is not
        empty."""
        path, anchors, _module_names = _NAMED_ANCHOR_CENSUSES[census]
        names = anchors()
        assert names, census
        assert named_anchor_strings([path()], names.__contains__) == []

    def test_no_identity_scope_call_has_a_call_result_as_its_callee(self):
        """FAIL CLOSED: a callee another call returned
        (`operator.methodcaller("__getattribute__", name)(module)`) can
        reach an identity function by a name built at run time
        (`"MAIN_REPO_ROOT".lower()`, a join, an f-string), which no census
        reads. The scope holds both named-anchor modules."""
        assert call_result_callees(_identity_caller_files()) == []

    def test_the_callee_reader_reads_every_binding_in_the_identity_scope(self):
        """The ban above is only as wide as its reader: every name the
        compiled identity-scope modules bind is read by some binding form."""
        assert unread_bindings(_identity_caller_files()) == []

    @pytest.mark.parametrize("source, held", [
        ("operator.methodcaller('a', n)(x)\n", True),
        ("get = operator.methodcaller('a', n)\nget(x)\n", True),
        ("def f():\n    get = operator.methodcaller('a', n)\n    return get(x)\n", True),
        ("table = {'k': operator.methodcaller('a', n)}\ntable['k'](x)\n", True),
        ("table = {'k': operator.methodcaller('a', n)}\nhandler = table.get('k')\nhandler(x)\n", True),
        ("class Holder:\n    get = operator.methodcaller('a', n)\nHolder.get(x)\n", True),
        ("holder.get = operator.methodcaller('a', n)\nholder.get(x)\n", True),
        ("def use(fn):\n    return fn(x)\nuse(operator.methodcaller('a', n))\n", True),
        ("def use(fn):\n    return fn(x)\n", True),
        ("rows[0](x)\n", True),
        ("(lambda fn: fn(x))(g)\n", True),
        ("get = cached or operator.methodcaller('a', n)\nget(x)\n", True),
        ("def a():\n    pass\ntable = {'a': a}\nhandler = table.get(k)\nhandler(x)\n", False),
        ("def a():\n    pass\ntable = {'a': a}\ntable['a'](x)\n", False),
        ("def a():\n    pass\na()\n", False),
        ("import os\nos.getcwd()\n", False),
        ("obj.method(x)\n", False),
        ("class K:\n    def m(self):\n        return self(1)\n", False),
        ("def use(fn):\n    return fn(x)\ndef a():\n    pass\nuse(a)\n", False),
        ("def wrap(method):\n    def inner(*a):\n        return method(*a)\n    return inner\n@wrap\ndef a():\n    pass\n",
         False),
        ("def wrap(method):\n    def inner(*a):\n        return method(*a)\n    return inner\nwrap(operator.methodcaller('a', n))\n",
         True),
        ("for get in [operator.methodcaller('a', n)]:\n    get(x)\n", True),
        ("async def f(rows):\n    async for get in rows:\n        get(x)\nf([operator.methodcaller('a', n)])\n", True),
        ("with operator.methodcaller('a', n) as get:\n    get(x)\n", True),
        ("[get(x) for get in [operator.methodcaller('a', n)]]\n", True),
        ("list(get(x) for get in [operator.methodcaller('a', n)])\n", True),
        ("{k: get(x) for k, get in [(1, operator.methodcaller('a', n))]}\n", True),
        ("{get(x) for get in (operator.methodcaller('a', n),)}\n", True),
        ("get, other = operator.methodcaller('a', n), None\nget(x)\n", True),
        ('get, other = make()\nget(x)\n', True),
        ("def make():\n    return operator.methodcaller('a', n), None\nget, other = make()\nget(x)\n", True),
        ("def make():\n    yield operator.methodcaller('a', n)\nfor get in make():\n    get(x)\n", True),
        ("*rest, get = [None, operator.methodcaller('a', n)]\nget(x)\n", True),
        ("first, *more = [None, operator.methodcaller('a', n)]\nmore[0](x)\n", True),
        ("a, (get, b) = 1, (operator.methodcaller('a', n), 2)\nget(x)\n", True),
        ("get: object = operator.methodcaller('a', n)\nget(x)\n", True),
        ("(get := operator.methodcaller('a', n))(x)\n", True),
        ("if (get := operator.methodcaller('a', n)):\n    get(x)\n", True),
        ("[get := operator.methodcaller('a', n) for _ in (1,)]\nget(x)\n", True),
        ("rows = []\nrows += [operator.methodcaller('a', n)]\nrows[0](x)\n", True),
        ("def deco(f):\n    return operator.methodcaller('a', n)\n@deco\ndef get():\n    pass\nget(x)\n", True),
        ("def deco(c):\n    return operator.methodcaller('a', n)\n@deco\nclass Get:\n    pass\nGet(x)\n", True),
        ("@lambda f: operator.methodcaller('a', n)\ndef get():\n    pass\nget(x)\n", True),
        ("def make():\n    def deco(f):\n        return operator.methodcaller('a', n)\n    return deco\n@make()\ndef get():\n    pass\nget(x)\n", True),
        ("match operator.methodcaller('a', n):\n    case get:\n        get(x)\n", True),
        ("match [operator.methodcaller('a', n)]:\n    case [get]:\n        get(x)\n", True),
        ("match {'k': operator.methodcaller('a', n)}:\n    case {'k': get}:\n        get(x)\n", True),
        ("match {'k': operator.methodcaller('a', n)}:\n    case {**rest}:\n        rest['k'](x)\n", True),
        ("match [operator.methodcaller('a', n)]:\n    case [*rest]:\n        rest[0](x)\n", True),
        ("match operator.methodcaller('a', n):\n    case object() as get:\n        get(x)\n", True),
        ("def use(fn=operator.methodcaller('a', n)):\n    return fn(x)\nuse()\n", True),
        ("def a():\n    pass\ndef use(fn=operator.methodcaller('a', n)):\n    return fn(x)\nuse(a)\n", True),
        ("def use(fn):\n    return fn(x)\nuse(*[operator.methodcaller('a', n)])\n", True),
        ("def use(fn):\n    return fn(x)\nuse(**{'fn': operator.methodcaller('a', n)})\n", True),
        ("def a():\n    pass\ndef use(fn):\n    return fn(x)\nuse(a)\nlist(map(use, [operator.methodcaller('a', n)]))\n", True),
        ("def f():\n    global get\n    get = operator.methodcaller('a', n)\nf()\nget(x)\n", True),
        ("def f():\n    get = None\n    def g():\n        nonlocal get\n        get = operator.methodcaller('a', n)\n    g()\n    return get(x)\n", True),
        ("class Holder:\n    get = operator.methodcaller('a', n)\nHolder().get(x)\n", True),
        ("class Base:\n    get = operator.methodcaller('a', n)\nclass Child(Base):\n    pass\nChild.get(x)\n", True),
        ("class Base:\n    def run(self):\n        return self.get(x)\nclass Child(Base):\n    get = operator.methodcaller('a', n)\n", True),
        ("class K:\n    get = staticmethod(dict.get)\nd = {}\nd.get('k')\nK.get(d, 'k')\n", False),
        ('class K:\n    make = classmethod(lambda cls: cls)\nK.make()\n', False),
        ('def a():\n    pass\nfor fn in [a]:\n    fn(x)\n', False),
        ('def a():\n    pass\nfn, other = a, None\nfn(x)\n', False),
        ("def a():\n    pass\nfn, other = a, operator.methodcaller('a', n)\nfn(x)\n", False),
        ('def a():\n    pass\ndef make():\n    return a, None\nfn, _ = make()\nfn(x)\n', False),
        ('with open(p) as handle:\n    handle.read()\n', False),
        ('import functools\n@functools.lru_cache\ndef a():\n    pass\na()\n', False),
        ('def a():\n    pass\nmatch a:\n    case fn:\n        fn(x)\n', False),
        ('try:\n    pass\nexcept Exception as err:\n    err.with_traceback(None)\n', False),
        ('def a():\n    pass\ndef use(fn=a):\n    return fn(x)\nuse()\n', False),
        ('def a():\n    pass\n[fn(x) for fn in (a,)]\n', False),
        ('rows = []\nrows += [len]\nrows[0](x)\n', False),
        ("import functools\nfunctools.partial(int, base=2)('10')\n", False),
        ("from functools import partial\npartial(int, base=2)('10')\n", False),
        ("import functools\nfunctools.partial(operator.methodcaller('a', n))(x)\n", True),
        ("from functools import partial\nget = partial(operator.methodcaller('a', n))\nget(x)\n", True),
        ("def partial(f):\n    return operator.methodcaller('a', n)\npartial(int)(x)\n", True),
        ("import functools\nfunctools.partial()(x)\n", True),
        # a container filled after its literal holds what the filling put in
        ("rows = []\nrows.append(operator.methodcaller('a', n))\nrows[0](x)\n", True),
        ("rows = []\nrows.append(operator.methodcaller('a', n))\nfor get in rows:\n    get(x)\n", True),
        ("rows = []\nrows.extend([operator.methodcaller('a', n)])\nrows[0](x)\n", True),
        ("rows = []\nrows.insert(0, operator.methodcaller('a', n))\nrows[0](x)\n", True),
        ("table = {}\ntable['k'] = operator.methodcaller('a', n)\ntable['k'](x)\n", True),
        ("table = {}\ntable['k'] = operator.methodcaller('a', n)\nhandler = table.get('k')\nhandler(x)\n", True),
        ("table = {}\ntable.update(k=operator.methodcaller('a', n))\ntable['k'](x)\n", True),
        ("table = {}\ntable.setdefault('k', operator.methodcaller('a', n))\ntable['k'](x)\n", True),
        ("table = {}\ntable |= {'k': operator.methodcaller('a', n)}\ntable['k'](x)\n", True),
        ("table = {}\ndef register():\n    table['k'] = operator.methodcaller('a', n)\ntable['k'](x)\n", True),
        ("def fill(t):\n    t['k'] = operator.methodcaller('a', n)\ntable = {}\nfill(table)\ntable['k'](x)\n", True),
        ("class H:\n    rows = []\nH.rows.append(operator.methodcaller('a', n))\nH.rows[0](x)\n", True),
        # read in place only: the literal is what it holds
        ("def a():\n    pass\nrows = [a]\nif rows and len(rows) > 0:\n    rows[0](x)\n", False),
        ("def a():\n    pass\nrows = [a]\nfor fn in rows:\n    fn(x)\n", False),
        ("def a():\n    pass\ntable = {'k': a}\nif 'k' in table:\n    table.get('k')(x)\n", False),
        ("def a():\n    pass\nrows = []\nrows.append(a)\nrows[0](x)\n", True),
    ])
    def test_a_callee_is_followed_to_where_it_comes_from(self, tmp_path, source, held):
        path = tmp_path / "scope.py"
        path.write_text(source, encoding="utf-8")
        assert bool(call_result_callees([path])) is held, call_result_callees([path])

    @pytest.mark.parametrize("use, held", [
        ("from scope import get\nget(x)\n", True),
        ("from scope import get as fetch\nfetch(x)\n", True),
        ("import scope\nscope.get(x)\n", True),
        ("import scope as other\nother.get(x)\n", True),
        ("from scope import plain\nplain(x)\n", False),
    ])
    def test_a_callee_imported_from_another_file_in_scope_is_followed(self, tmp_path, use, held):
        (tmp_path / "scope.py").write_text(
            "get = operator.methodcaller('a', n)\ndef plain():\n    pass\n", encoding="utf-8"
        )
        (tmp_path / "use.py").write_text(use, encoding="utf-8")
        found = call_result_callees([tmp_path / "scope.py", tmp_path / "use.py"])
        assert any(name == "use.py" for name, _line, _source in found) is held, found

    @pytest.mark.parametrize("source, held", [
        ("def f():\n    n = 'O_A'\n    return getattr(os, n)\n", ['O_A']),
        ("def f():\n    for n in ('O_A', 'O_B'):\n        getattr(os, n)\n", ['O_A', 'O_B']),
        ("N = 'O_A'\ndef f():\n    return getattr(os, N)\n", ['O_A']),
        ("def f(n):\n    return getattr(os, n)\nf('O_A')\n", ['O_A']),
        ("def f(n='O_A'):\n    return getattr(os, n)\nf()\n", ['O_A']),
        ("def f():\n    n, m = 'O_A', 0\n    return getattr(os, n)\n", ['O_A']),
        ("def f():\n    n = 'O_A'\n    n += 'X'\n    return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    with c as n:\n        return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    if (n := c()):\n        return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    n: str = c()\n    return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    n, m = c()\n    return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    for (n, m), k in c():\n        return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    try:\n        pass\n    except Exception as n:\n        return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    import os as n\n    return getattr(os, n)\n", None),
        ("def f(c):\n    n = 'O_A'\n    match c:\n        case [n]:\n            return getattr(os, n)\n", None),
        ("def f(c):\n    global n\n    n = 'O_A'\n    return getattr(os, n)\ndef g(c):\n    global n\n    n = c()\n", None),
        ("def f(c):\n    n = 'O_A'\n    return [getattr(os, n) for n in c()]\n", None),
        ("def f(c):\n    n = 'O_A'\n    def n():\n        pass\n    return getattr(os, n)\n", None),
        ('def f(n):\n    return getattr(os, n)\n', None),
        ("def f(n='O_A'):\n    return getattr(os, n)\nf(g())\n", None),
        ("NAMES = ['O_A']\ndef f():\n    for n in NAMES:\n        getattr(os, n)\n", ['O_A']),
        ("def f():\n    n: str\n    n = 'O_A'\n    return getattr(os, n)\n", ['O_A']),
        ("def f(c):\n    n: str\n    n = c()\n    return getattr(os, n)\n", None),
        ("NAMES = ['O_A']\nNAMES.append(g())\ndef f():\n    for n in NAMES:\n        getattr(os, n)\n", None),
        ("NAMES = ['O_A']\ndef add():\n    NAMES[0] = g()\ndef f():\n    for n in NAMES:\n        getattr(os, n)\n", None),
    ])
    def test_a_name_handed_to_a_lookup_is_read_from_every_binding(self, source, held):
        """A name is read as the strings it holds only when every binding of
        it, by any binding form, holds a known string; any other binding
        makes it unreadable, which the dynamic-access ban reds."""
        tree = ast.parse(source)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "getattr")
        found = name_values(call.args[1], None, tree)
        assert (sorted(found) if found is not None else None) == held

    @pytest.mark.parametrize("source, table", [
        ("A = 'a'\n", {"A": "a"}),
        ("A: Final = 'a'\n", {"A": "a"}),
        ("A = 'a'\nA = 'a'\n", {"A": "a"}),
        ("A = B = 'a'\n", {"A": "a", "B": "a"}),
        ("A = 'a'\nA = 'b'\n", {}),
        ("A = 'a'\nA += 'b'\n", {}),
        ("A = 'a'\ndef f():\n    global A\n    A = g()\n", {}),
        ("A = 'a'\nfor A in ('a',):\n    pass\n", {}),
        ("A = 'a'\nfrom m import A\n", {}),
        ("A = 'a'\nA, B = g()\n", {}),
        ("A = 'a'\ndef f():\n    A = g()\n", {"A": "a"}),
        ("A: str\nA = 'a'\n", {"A": "a"}),
        ("A: str\n", {}),
        ("A: str\nA = g()\n", {}),
    ])
    def test_a_module_string_constant_is_read_from_every_binding(self, source, table):
        """A module name is a string constant only when every module-level
        binding of it, by any binding form, assigns that one string; a
        function's own local of the same name is not one of them."""
        assert module_string_constants(ast.parse(source)) == table

    @pytest.mark.parametrize("arm, texts, resolved", [
        ("def arm():\n    return 'a', None\n", {"a"}, True),
        ("A = 'a'\ndef arm():\n    return A, None\n", {"a"}, True),
        ("def arm():\n    r = 'a'\n    return r, None\n", {"a"}, True),
        ("A = 'a'\nB = 'b'\ndef arm(c):\n    if c:\n        return A, None\n    return B, None\n", {"a", "b"}, True),
        ("A = 'a'\nA = g()\ndef arm():\n    return A, None\n", set(), False),
        ("def arm():\n    return UNBOUND, None\n", set(), False),
        ("A = None\ndef arm():\n    return A, None\n", set(), False),
        ("def arm():\n    return str('a'), None\n", set(), False),
    ])
    def test_a_chosen_reason_is_read_from_every_binding(self, arm, texts, resolved):
        """`chooser_elements` reads a reason the chooser returns as a string
        or as a name each of whose bindings holds one; any other form is
        unresolved."""
        tree = ast.parse(arm + "def g2():\n    x = arm(1)\n    use(x[0])\n")
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "use").args[0]
        found, unresolved = chooser_elements(tree, node)
        assert found == texts and (not unresolved) is resolved, (found, unresolved)

    def test_a_rebinding_through_the_module_object_is_listed_not_read(self):
        """The constant tables' one adversarial-only form: the docstring names
        it, and the table does not read it."""
        listed = module_string_constants.__doc__.split("ADVERSARIAL-ONLY")
        assert len(listed) == 2 and "sys.modules[__name__]" in listed[1], module_string_constants.__doc__
        source = "import sys\nA = 'a'\nsys.modules[__name__].A = 'b'\n"
        assert module_string_constants(ast.parse(source)) == {"A": "a"}

    def test_the_reader_reads_every_binding_the_compiler_makes(self):
        """The population is the compiler's: every name a code object of each
        binding probe (`_binding_probes`: every binding form of the grammar,
        PEP 695 type parameters included, in every scope) stores is read by
        some form. Control: the forms of each table are compiled."""
        probes = _binding_probes()
        assert len(probes) > 100, len(probes)
        sources = {ast.unparse(tree) for tree, _code in probes}
        assert {"def f[X]():\n    pass", "type A[*X] = int", "def w():\n    import X"} <= sources, sources
        unread = [
            (ast.unparse(tree), sorted(_CalleeReader(tree, code).unread)) for tree, code in probes
            if _CalleeReader(tree, code).unread
        ]
        assert unread == [], unread

    def test_the_binding_probes_cover_the_grammar(self):
        """The probe tables are the grammar's: each type parameter kind the
        compiler defines, each node class that owns type parameters, and each
        statement that opens a scope of its own (`_SCOPES`)."""
        classes = _compiler_classes()
        assert set(_TYPE_PARAM_SPELLINGS) == {name for name, c in classes.items() if issubclass(c, ast.type_param)
                                              and c is not ast.type_param}
        assert set(_TYPE_PARAM_OWNERS) == {name for name, c in classes.items() if "type_params" in c._fields}
        assert set(_SCOPE_WRAPPERS) - {"Module"} == {
            name for name, c in classes.items() if issubclass(c, ast.stmt) and c in _SCOPES
        }
        refused = []
        _binding_probes(refused)
        # the one form the compiler refuses: a `nonlocal` with no binding to name
        assert len(refused) == len(_SCOPE_WRAPPERS) and all("nonlocal X" in s for s in refused), refused

    @pytest.mark.parametrize("source", [
        "def first[T](items: list[T]) -> T:\n    return items[0]\nfirst([1])\n",
        "def f[**P](g):\n    return g\nf(len)\n",
        "def f[*Ts](g):\n    return g\nf(len)\n",
        "def f[T: (int, str)](x: T) -> T:\n    return x\nf(1)\n",
        "class Box[T]:\n    def __init__(self, v: T):\n        self.v = v\nBox(1)\n",
        "class K:\n    def m[T](self, v: T) -> T:\n        return v\nK().m(1)\n",
        "type Pair[T] = tuple[T, T]\n",
        "import functools\n@functools.cache\ndef f[T](x: T) -> T:\n    return x\nf(1)\n",
        "def outer():\n    def inner[T](x: T) -> T:\n        return x\n    return inner(1)\nouter()\n",
    ])
    def test_a_generic_definition_is_read(self, tmp_path, source):
        """An honest generic def, class, method or type alias (PEP 695, on the
        3.12 floor) binds nothing the reader leaves unread, and no call in it
        is a hit."""
        path = tmp_path / "scope.py"
        path.write_text(source, encoding="utf-8")
        assert unread_bindings([path]) == [] and call_result_callees([path]) == []

    def test_a_binding_no_form_reads_is_unread_and_fails_closed(self):
        """Each name a binding probe binds, with what the reader read for it
        taken away, is reported unread, and a call through it is a hit."""
        checked = 0
        for tree, code in _binding_probes():
            names = {
                name for compiled in _code_objects(code) for name in _stored_names(compiled)
            } - _implicit_bindings()
            for name in sorted(names):

                class Unread(_CalleeReader):
                    def _collect(self):
                        super()._collect()
                        for key in [key for key in self.sources if key[1] == name]:
                            del self.sources[key]

                assert name in {n for _scope, n in Unread(tree, code).unread}, (name, ast.unparse(tree))
                checked += 1
        assert checked > 100, checked
        reader = _CalleeReader(ast.parse("get = len\nget(x)\n"), compile("get = len\nget(x)\n", "<t>", "exec"))
        assert not reader.may_be(ast.Name("get"), None)
        reader.unread.add((None, "get"))
        assert reader.may_be(ast.Name("get"), None)

    @pytest.mark.parametrize("source, named", [
        ("VALUE = 'detect_id_for_root'\n", True),
        ("VALUE = 'scripts.project_identity.detect_id_for_root'\n", True),
        ("VALUE = 'project_identity:detect_id_for_root'\n", True),
        ("VALUE = '{0.detect_id_for_root!r}'\n", False),
        ("VALUE = b'detect_id_for_root'\n", False),
        ("VALUE = ' detect_id_for_root '\n", True),
        ("def f():\n    'detect_id_for_root'\n", True),
        ("VALUE = f'{x}.detect_id_for_root'\n", True),
        ("VALUE = 'calls detect_id_for_root on the asked root'\n", False),
        ("VALUE = 'detect_id_for_roots'\n", False),
        ("VALUE = '_detect_id_for_root'\n", False),
        ("VALUE = '.detect_id_for_root'\n", True),
        ("VALUE = 'detect_id_for_root:'\n", True),
        ("VALUE = 'detect_id_for_root: the asked root'\n", False),
        ("def f():\n    '''Args:\n        detect_id_for_root: the asked root.\n    '''\n", False),
        ("VALUE = 'scripts.project_identity.detect_id_for_root is read here'\n", False),
        ("VALUE = 'Wraps project_identity.detect_id_for_root'\n", False),
        ("VALUE = 'see project_identity:detect_id_for_root'\n", False),
        ("VALUE = 'project_identity.detect_id_for_root'\n", True),
        ("VALUE = '..scripts.detect_id_for_root'\n", True),
    ])
    def test_a_string_names_an_anchor_only_as_a_whole_part(self, tmp_path, source, named):
        """A string a resolver takes as a name walking to the anchor names
        it; text no resolver takes as a name (prose around it, a longer name,
        a format field, bytes) does not."""
        path = tmp_path / "scope.py"
        path.write_text(source, encoding="utf-8")
        found = named_anchor_strings([path], IDENTITY_FUNCTIONS.__contains__)
        assert bool(found) is named, found

    def test_the_resolvers_are_run_and_import_nothing(self):
        """The accepted names are the resolvers' own: each part of a name
        `pkgutil.resolve_name` or `importlib.import_module` takes is walked,
        and the stand-in importer imports nothing and is put back."""
        import importlib

        real = importlib._bootstrap._find_and_load
        assert _resolver_walk("no_such_module_here.inner:attr.leaf") >= {
            "no_such_module_here", "inner", "attr", "leaf",
        }
        assert "no_such_module_here" not in sys.modules
        assert importlib._bootstrap._find_and_load is real
        dotted = [name for name in list(sys.modules) if all(part.isidentifier() for part in name.split("."))]
        assert len(dotted) > 50
        for name in dotted:
            assert set(name.split(".")) <= _resolver_walk(name), name
        assert _resolver_walk("two words") == set()
        assert _resolver_walk("calls os.O_NOFOLLOW") == set()
        assert _resolver_walk(b"bytes.name") == set()

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


# The `.git`-layout predicates: every `-> bool` function and every refusal
# (`_is_refusal`: answers None or a detail string) of project_identity that
# detect_id_for_root reaches from the asked root (the marker predicates and
# every project_identity function they call, transitively). Each truthy
# `return` is one layout outcome the identity code distinguishes, and each
# operand of a returned `or` is one outcome of its own.
#
# NOT A FLOOR LAYOUT: the outcomes of these predicates, which no own layout in
# the unpinned table produces, or which describe no layout at all. Each reason
# says how a layout reaching them ends: a decline, or, for the user's own
# dot-git store, a bind through a pin, counted in the pinned table.
NOT_A_FLOOR_LAYOUT = {
    # Every detail the three refusals below return reaches one place:
    # detect_id_for_root's `_gitfile_redirect_detail(asked)` check, which
    # returns a `gitfile_redirect` decline carrying that detail. A usable pin
    # is read before that check, so a pinned checkout binds its pin.
    "_gitfile_redirect_detail": (
        "its one detail-giving return hands the `.git` marker's target to "
        "`_redirect_target_refusal`, so it refuses for exactly the cases "
        "the next reason lists, and each declines `gitfile_redirect` at "
        "detect_id_for_root's redirect check. The one own layout it reaches "
        "is the user's `--separate-git-dir=<dir>/.git` store: unpinned it "
        "declines there too, and pinned it binds through the pin read first "
        "(TestTheGitCallFloorWithAPin)"
    ),
    "_redirect_target_refusal": (
        "refuses when the target is named `.git` (the foreign_gitdir, "
        "relative_foreign_gitdir and symlinked_git shapes decline "
        "`gitfile_redirect`; the user's own dot-git store is the pinned "
        "exception above), and whenever `_admin_dir_refusal` refuses for a "
        "submodule admin dir with no `commondir` file"
    ),
    "_admin_dir_refusal": (
        "refuses for a submodule admin dir whose recorded checkout cannot be "
        "proven to be the asked one. Reached by a shape: `os.path.samefile` "
        "says the recorded checkout is another one, a forgery (the "
        "forged_submodule_admin and symlinked_submodule_admin shapes decline "
        "`gitfile_redirect`). Reached by no fixture shape, and declining at "
        "the same redirect check when reached: `git config core.worktree` "
        "cannot run, or exits non-zero with output (a git failure); "
        "`core.worktree` is unset and git, asked at the checkout, does not "
        "name it as its top level (`_git_names_toplevel`); or the recorded or "
        "the asked path cannot be resolved. When `samefile` raises because "
        "the recorded checkout does not exist, the return is reached only by "
        "a direct call or a mocked `samefile`: with the checkout really "
        "deleted, the git-root, CLAUDE_PROJECT_DIR and cwd-marker routes "
        "decline `broken_git_marker` in their root guard before this "
        "refusal is called"
    ),
    "_git_env_bool": "reads a git environment flag, not the `.git` layout",
    "_git_names_toplevel": (
        "answers only for a submodule admin dir whose `core.worktree` is "
        "unset (`git config --unset core.worktree`), which no own layout "
        "builds; there a True lets the checkout bind the submodule's own "
        "origin (test_project_identity_name_provenance's unset-core.worktree "
        "arms), and a False declines `gitfile_redirect`"
    ),
}


def _bool_shaped(value) -> bool:
    """Whether a returned expression is a bool by its own form: a True/False
    constant, a comparison, a `not`, or an `and`/`or` of such (an `or` of
    other values returns one of them, not a bool)."""
    if isinstance(value, ast.BoolOp):
        return all(_bool_shaped(operand) for operand in value.values)
    return (
        (isinstance(value, ast.Constant) and isinstance(value.value, bool))
        or isinstance(value, ast.Compare)
        or (isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not))
    )


def _fault_dot_git_check_raises(monkeypatch, target):
    """The root's `.git` cannot be examined: its `is_file` raises."""
    real = Path.is_file

    def is_file(self):
        if self == target / ".git":
            raise OSError("probe")
        return real(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    return None


def _fault_no_git_dir(monkeypatch, target):
    """Git gives no git dir for the asked directory."""
    monkeypatch.setattr(pi, "git_dir_answer", lambda start: None)
    return None


def _fault_common_dir_loops(monkeypatch, target):
    """The common dir handed to the check is a submodule admin path that is
    a symlink loop, so it will not resolve. No route hands the check such a
    common dir (the resolver declines `origin_unreadable` first), so the
    check is called with it directly."""
    modules = target.parent / "loop" / ".git" / "modules" / "vendor"
    modules.mkdir(parents=True)
    (modules / "lib").symlink_to(modules / "lib2")
    (modules / "lib2").symlink_to(modules / "lib")
    return modules / "lib"


# NOT A FLOOR OUTCOME: single truthy returns of a layout predicate that no own
# layout reaches, keyed (predicate, the guard the return sits under, its
# ordinal among the predicate's returns under the same guard), as
# `_outcome_keys` computes them. Each names the fault that reaches it. The
# fault returns None when it acts through the resolver, or the common dir to
# hand the predicate directly when no route can carry it.
# test_each_excluded_outcome_is_reached_by_its_fault applies each fault and
# requires that this outcome, and no other, returns True, and that the
# resolver then declines `submodule_checkout` where a route carries the fault.
NOT_A_FLOOR_OUTCOME = {
    ("_submodule_checkout", "except (OSError, RuntimeError, ValueError)", 0): _fault_dot_git_check_raises,
    ("_submodule_checkout", "if git_dir is None", 0): _fault_no_git_dir,
    ("_submodule_checkout", "except (OSError, RuntimeError, ValueError)", 1): _fault_common_dir_loops,
}


def _outcome_keys(predicates: set) -> dict:
    """{(predicate, return line, operand index): (predicate, guard, ordinal)}
    for every outcome `_layout_outcomes` lists. The guard is the nearest
    enclosing `if` test or `except` clause, as source (`body` when there is
    none), and the ordinal counts the predicate's earlier returns under the
    same guard text."""
    tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    outcomes = _layout_outcomes(predicates)
    keys = {}
    for function in tree.body:
        if not (isinstance(function, ast.FunctionDef) and function.name in predicates):
            continue
        parents = {id(child): node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
        seen = collections.Counter()
        returns = sorted((n for n in ast.walk(function) if isinstance(n, ast.Return)), key=lambda n: n.lineno)
        for node in returns:
            up, guard = node, "body"
            while id(up) in parents:
                up = parents[id(up)]
                if isinstance(up, ast.If):
                    guard = f"if {ast.unparse(up.test)}"
                    break
                if isinstance(up, ast.ExceptHandler):
                    guard = f"except {ast.unparse(up.type)}" if up.type is not None else "except"
                    break
            ordinal = seen[guard]
            seen[guard] += 1
            for outcome in outcomes:
                if outcome[0] == function.name and outcome[1] == node.lineno:
                    keys[outcome] = (function.name, guard, ordinal)
    return keys

# The typing generics an annotation may be built from and still be read as a
# non-bool type, checked by identity against `typing`, with every argument
# checked in turn.
NON_BOOL_GENERICS = (
    "Optional", "Tuple", "List", "Dict", "Set", "FrozenSet",
    "Iterable", "Iterator", "Sequence", "Mapping",
)
_UNRESOLVED = object()


def _resolved(name: str):
    """What `name` means in project_identity: its module global, else the
    builtin, else _UNRESOLVED."""
    import builtins

    namespace = vars(pi)
    if name in namespace:
        return namespace[name]
    return getattr(builtins, name, _UNRESOLVED)


def _not_a_bool_type(node) -> "str | None":
    """None when the annotation `node` is positively a type no bool belongs
    to, else why it is not known to be one. Admitted, each by a check on the
    value it names:
    - the constant None (inside a union or a generic);
    - a name that resolves in project_identity to a class that `bool` is not
      a subclass of (so `int`, `object`, `typing.Any` and a bool alias are
      refused);
    - a NON_BOOL_GENERICS generic, resolved by identity, whose every
      argument is admitted;
    - `X | Y` with both sides admitted.
    Every other form (TypeGuard, TypeIs, Annotated, Literal, a string, an
    attribute, an unresolved name) is refused."""
    import inspect
    import typing

    if isinstance(node, ast.Constant) and node.value is None:
        return None
    if isinstance(node, ast.Name):
        value = _resolved(node.id)
        if value is typing.Any or not inspect.isclass(value):
            return f"{node.id!r} does not resolve to a class"
        if issubclass(bool, value):
            return f"{node.id!r} resolves to {value.__name__}, which a bool is an instance of"
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        generic = _resolved(node.value.id)
        if not any(generic is getattr(typing, known) for known in NON_BOOL_GENERICS):
            return f"{ast.unparse(node.value)!r} is not a generic the walk reads"
        arguments = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for argument in arguments:
            why = _not_a_bool_type(argument)
            if why:
                return why
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _not_a_bool_type(node.left) or _not_a_bool_type(node.right)
    return f"{ast.unparse(node)!r} is not a form the walk reads"


def _predicate_results(function, defined: dict, seen: frozenset) -> dict:
    """{id(returned node): predicate name} for each value `function` returns
    that is a direct call to a project_identity function not proven to be a
    non-bool. A result reaching a return any other way (a local name, an
    `or`, a ternary) is not read here; `_layout_predicate_forms` reports it."""
    def predicate_call(node):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in defined
        ):
            name = node.func.id
            if name in seen or _return_kind(defined[name], defined, seen)[0] != "not a predicate":
                return name
        return None

    found = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and node.value is not None:
            name = predicate_call(node.value)
            if name:
                found[id(node.value)] = name
    return found


def _bindings(tree, name: str) -> int:
    """How many places in `tree` may bind `name`: every `Name` that is not
    read (an assignment, unpacking, `for`, `with`, walrus, comprehension or
    `del` target), and every identifier any other node carries that is
    `name` or starts `name.` (an import, `global`, `nonlocal`, a def, a
    class, an `except ... as`, a match capture, a parameter). An identifier
    that binds nothing (an attribute or keyword named the same) is counted
    too, so the count can only be too high, never too low."""
    return _binding_counts(tree)[name]


# The last tree counted and its counts. Callers ask about one tree for many
# names in a row, so one walk answers all of them; one slot keeps no tree
# alive past the next.
_LAST_BINDING_COUNTS: list = [None, None]


def _binding_counts(tree) -> "collections.Counter":
    """{name: the count `_bindings` gives} for every name in `tree`, from
    one walk."""
    if _LAST_BINDING_COUNTS[0] is not tree:
        counts = collections.Counter()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                continue
            if isinstance(node, ast.Name):
                if not isinstance(node.ctx, ast.Load):
                    counts[node.id] += 1
                continue
            for _, value in ast.iter_fields(node):
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str):
                        counts[item.split(".")[0]] += 1
        _LAST_BINDING_COUNTS[:] = [tree, counts]
    return _LAST_BINDING_COUNTS[1]


def _is_refusal(function, defined: dict, seen: frozenset, tree=None) -> bool:
    """Whether `function` is annotated exactly `Optional[str]` and every
    value it returns is None, a module-level name of project_identity whose
    only binding anywhere in the module is one plain assignment of a string
    constant (`_bindings`), a ternary of two such values, or a direct
    call to a project_identity function that is itself a refusal: a check
    whose answer is None (it does not refuse) or the detail it refuses
    with. `tree` is the module read, project_identity's when not given."""
    if not (function.returns is not None and ast.unparse(function.returns) == "Optional[str]"):
        return False
    if tree is None:
        tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
    details = {
        node.targets[0].id for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        and _bindings(tree, node.targets[0].id) == 1
    }
    seen = seen | {function.name}

    def admitted(value) -> bool:
        if isinstance(value, ast.Constant) and value.value is None:
            return True
        if isinstance(value, ast.Name) and value.id in details:
            return True
        if isinstance(value, ast.IfExp):
            return admitted(value.body) and admitted(value.orelse)
        if (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in defined and value.func.id not in seen
        ):
            return _is_refusal(defined[value.func.id], defined, seen, tree)
        return False

    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    return bool(returns) and all(node.value is not None and admitted(node.value) for node in returns)


def _return_kind(function, defined: "dict | None" = None, seen: frozenset = frozenset()) -> tuple:
    """("predicate", "refusal" or "not a predicate", None), or (None, why
    the walk cannot classify `function`). The walk reads these forms and no
    others, each admitted by a check on what it names:
    - PREDICATE: the annotation is the bare name `bool`, and `bool` is not
      rebound in project_identity;
    - REFUSAL: `_is_refusal` admits it, so it answers None or a detail
      string, and its detail is its layout outcome;
    - NOT A PREDICATE, `-> None`: every return in the body is bare or
      returns None;
    - NOT A PREDICATE, any other annotation: `_not_a_bool_type` admits it,
      no return in the body is bool-shaped, and none returns the result of
      a project_identity function that is not itself not a predicate.
    Anything else, a missing annotation included, is unreadable. LIMIT: a
    function annotated with a non-bool class that returns a bool from a
    call outside project_identity is read as its annotation says."""
    import builtins

    if defined is None:
        tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
        defined = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    seen = seen | {function.name}
    returns = function.returns
    returned = [
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    if isinstance(returns, ast.Name) and returns.id == "bool":
        if _resolved("bool") is not builtins.bool:
            return None, "is annotated `bool`, but `bool` is rebound in project_identity"
        return "predicate", None
    if _is_refusal(function, defined, seen):
        return "refusal", None
    if returns is None:
        return None, "has no return annotation"
    if isinstance(returns, ast.Constant) and returns.value is None:
        for value in returned:
            if not (isinstance(value, ast.Constant) and value.value is None):
                return None, f"is annotated None but returns {ast.unparse(value)!r}"
        return "not a predicate", None
    why = _not_a_bool_type(returns)
    if why:
        return None, f"has the return annotation {ast.unparse(returns)!r}: {why}"
    for value in returned:
        if _bool_shaped(value):
            return None, (
                f"is annotated {ast.unparse(returns)!r} but returns "
                f"{ast.unparse(value)!r} at line {value.lineno}"
            )
    for value in returned:
        callee = _predicate_results(function, defined, seen).get(id(value))
        if callee:
            return None, (
                f"is annotated {ast.unparse(returns)!r} but returns the result "
                f"of {callee}, which is not known to be a non-bool, at line {value.lineno}"
            )
    return "not a predicate", None


# The exact-text pins this module keeps, each with why a behaviour check
# cannot stand in for it. A function here that reads Python source or a
# docstring and looks a literal up in it (`source_text_pins`) must be listed,
# and a listed function must still do so: a pin that is not here is a change
# detector to replace with a check of the behaviour it states.
_KEPT_TEXT_PINS = {
    "NAMED_LAYOUT_FORMS": (
        "each row is a judgement that the git-call floor still covers a form the layout walk cannot "
        "read; a new form must fail until someone makes it. Rows are keyed by the function a form "
        "calls (`_layout_form_key`), not by its text, and each reason is derived from the live site "
        "by what the walk reads, not by how an annotation is spelled"
    ),
    "TestEveryIdentityCallerIsCovered.test_a_rebinding_through_the_module_object_is_listed_not_read": (
        "`module_string_constants`' docstring names its one adversarial-only form; the anchor "
        "`sys.modules[__name__]` ties that listing to the row in the same test that shows the table "
        "does not read the form"
    ),
    "_documented_floor": (
        "PACTMemory.__init__'s git-call table is the subject: its rows are parsed here and compared with "
        "the measured counts, and its sentence 'No call is repeated with the same argument.' is the claim "
        "`test_no_route_repeats_a_git_call` measures"
    ),
    "_documented_pinned_floor": (
        "the pinned git-call table is parsed and compared with the counts measured with a pin; the "
        "sentence 'These counts assume no override pin.' is what the main table's measurement, made "
        "with no pin, holds the docstring to"
    ),
    "_CONVERSION_MISSING": (
        "Python 3.12's ast records no field types, so the grammar's identifier fields are read from the "
        "compiler's own conversion errors; changed wording empties the result, which fails "
        "`test_every_identifier_field_of_the_grammar_has_a_form` (its `Name.id` control) instead of passing"
    ),
    "_CONVERSION_SORT": "the same compiler wording, read for the class a field expects",
    "_CONVERSION_IDENTIFIER": "the same compiler wording, read for a field that holds an identifier",
}


def test_each_kept_text_pin_names_a_pin_here():
    """Every function here that pins Python source or docstring text is in
    `_KEPT_TEXT_PINS` with a reason, and every function listed there still
    pins it, so the table and the module cannot drift apart."""
    from fixtures.code_readers import kept_pin_mismatch

    assert kept_pin_mismatch(sys.modules[__name__], _KEPT_TEXT_PINS) == {"unlisted": [], "stale": []}


def _layout_form_key(function: str, kind: str, form: str) -> tuple:
    """The NAMED_LAYOUT_FORMS key of a form the layout walk does not read:
    (function, kind, what the form calls). A bind is keyed by the
    project_identity function its value calls, a `return not` by the
    function it negates, a call in a place by that place and the function;
    a local's name, the arguments and the spelling of the statement are
    not part of the key. Any other form keeps its text."""
    try:
        if kind == "binds":
            value = getattr(ast.parse(form).body[0], "value", None)
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                return (function, kind, f"a call to {value.func.id}")
        if kind == "returns":
            value = ast.parse(form, mode="eval").body
            if (isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
                    and isinstance(value.operand, ast.Call) and isinstance(value.operand.func, ast.Name)):
                return (function, kind, f"not a call to {value.operand.func.id}")
        if kind == "uses":
            place, _, call = form.partition(": ")
            value = ast.parse(call, mode="eval").body
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                return (function, kind, f"{place}: a call to {value.func.id}")
    except SyntaxError:
        pass
    return (function, kind, form)


def _named_layout_forms() -> collections.Counter:
    """The forms the layout walk does not read (`_layout_predicate_forms`),
    counted by their NAMED_LAYOUT_FORMS key (`_layout_form_key`)."""
    counted = collections.Counter()
    for (function, kind, form), count in _layout_predicate_forms()[1].items():
        counted[_layout_form_key(function, kind, form)] += count
    return counted


# Every form the layout walk does not read, on the tree as it is, keyed by
# what it calls (`_layout_form_key`), with how many times it occurs and what
# the code at the site is: the project_identity function it calls (or the
# module-level name it reaches) and the function the site is in, each with
# what `_return_kind` reads it as (a predicate, a refusal or not a
# predicate), so respelling an annotation the walk reads the same way
# changes no row.
# `test_each_named_layout_reason_states_the_code_at_its_site` derives each
# reason's text from the code at each site and requires the reason to equal
# it; a new form, or a form that calls another function, fails the census arm.
NAMED_LAYOUT_FORMS = {
    ('_git_names_toplevel', 'binds', 'a call to one_git_answer'): (1, 'assigns a call to `one_git_answer` (not a predicate) to a local in `_git_names_toplevel` (predicate)'),
    ('_admin_dir_refusal', 'uses', 'an `if` or `elif` test: a call to _git_names_toplevel'): (1, 'uses a call to `_git_names_toplevel` (predicate) in an `if` or `elif` test in `_admin_dir_refusal` (refusal)'),
    ('_admin_dir_refusal', 'binds', 'a call to one_git_answer'): (1, 'assigns a call to `one_git_answer` (not a predicate) to a local in `_admin_dir_refusal` (refusal)'),
    ('_marker_target', 'binds', 'a call to gitfile_target'): (1, 'assigns a call to `gitfile_target` (not a predicate) to a local in `_marker_target` (not a predicate)'),
    ('git_dir_answer', 'binds', 'a call to one_git_answer'): (1, 'assigns a call to `one_git_answer` (not a predicate) to a local in `git_dir_answer` (not a predicate)'),
    ('_gitfile_redirect_detail', 'binds', 'a call to _marker_target'): (1, 'assigns a call to `_marker_target` (not a predicate) to a local in `_gitfile_redirect_detail` (refusal)'),
    ('_marker_names_external_git_dir', 'binds', 'a call to _marker_target'): (1, 'assigns a call to `_marker_target` (not a predicate) to a local in `_marker_names_external_git_dir` (predicate)'),
    ('_marker_names_external_git_dir', 'returns', 'not a call to _submodule_admin_dir'): (1, 'returns `not` applied to a call to `_submodule_admin_dir` (predicate) in `_marker_names_external_git_dir` (predicate)'),
    ('_marker_target', 'binds', 'a call to read_git_file'): (1, 'assigns a call to `read_git_file` (not a predicate) to a local in `_marker_target` (not a predicate)'),
    ('_marker_target', 'binds', 'a call to find_git_marker'): (1, 'assigns a call to `find_git_marker` (not a predicate) to a local in `_marker_target` (not a predicate)'),
    ('_resolved_git_dir', 'binds', 'a call to git_dir_answer'): (1, 'assigns a call to `git_dir_answer` (not a predicate) to a local in `_resolved_git_dir` (not a predicate)'),
    ('_submodule_checkout', 'binds', 'a call to _resolved_git_dir'): (1, 'assigns a call to `_resolved_git_dir` (not a predicate) to a local in `_submodule_checkout` (predicate)'),
    ('find_git_marker', 'binds', 'a call to _git_ceiling_directories'): (1, 'assigns a call to `_git_ceiling_directories` (not a predicate) to a local in `find_git_marker` (not a predicate)'),
    ('find_git_marker', 'binds', 'a call to _git_env_bool'): (1, 'assigns a call to `_git_env_bool` (predicate) to a local in `find_git_marker` (not a predicate)'),
    ('git_dir_answer', 'calls', 'DETECTION_GIT_DIRS.get'): (1, 'calls a method on `DETECTION_GIT_DIRS`, a module-level assignment of a `contextvars.ContextVar(...)` call, in `git_dir_answer` (not a predicate)'),
    ('_redirect_target_refusal', 'uses', 'an `if` or `elif` test: a call to _submodule_admin_dir'): (1, 'uses a call to `_submodule_admin_dir` (predicate) in an `if` or `elif` test in `_redirect_target_refusal` (refusal)'),
    ('_submodule_checkout', 'uses', 'an `if` or `elif` test: a call to _submodule_admin_dir'): (1, 'uses a call to `_submodule_admin_dir` (predicate) in an `if` or `elif` test in `_submodule_checkout` (predicate)'),
}


# The words a layout-walk report gives for the place a project_identity call
# stands when no read form covers it, keyed by (parent node type, field).
# A place not listed here is reported by its node type and field.
LAYOUT_CALL_PLACES = {
    ("If", "test"): "an `if` or `elif` test",
    ("While", "test"): "a `while` test",
    ("Assert", "test"): "an `assert` test",
    ("Assert", "msg"): "an `assert` message",
    ("IfExp", "test"): "a ternary test",
    ("IfExp", "body"): "a ternary branch",
    ("IfExp", "orelse"): "a ternary branch",
    ("comprehension", "ifs"): "a comprehension `if`",
    ("match_case", "guard"): "a match guard",
    ("Match", "subject"): "a match subject with no capture",
    ("Raise", "exc"): "a `raise`",
    ("Raise", "cause"): "a raise cause",
    ("Expr", "value"): "an expression statement, its value discarded",
}
# The words for a call that only a read form would cover, standing in the
# body of a def or lambda nested in the reached function: that body runs
# whenever its caller runs it, not as the reached function's own code.
NESTED_BODY_PLACE = "the body of a nested def or lambda"
# The words for a call that stands inside an argument of a project_identity
# call without being that argument itself (an operand, a ternary branch, a
# display item, a starred expression): the walk follows the outer call, but
# does not read what it is handed there.
INSIDE_AN_ARGUMENT_PLACE = "inside an argument of a project_identity call"


def _in_nested_body(node, parents) -> bool:
    """Whether `node` stands in the body of a def, async def or lambda that
    is itself inside the function `parents` was built from."""
    child = node
    while id(child) in parents:
        parent, field = parents[id(child)]
        if field == "body" and (
            isinstance(parent, ast.Lambda)
            or (isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(parent) in parents)
        ):
            return True
        child = parent
    return False


def _layout_call_place(node, parents, defined) -> str | None:
    """Where the project_identity call `node` stands, or None when a form the
    walk reads or already reports covers it.

    Climbing from the call toward its statement: the whole value of a
    `return` is read (classified by `_return_kind`), and any other value of
    a `return` is reported as `returns`; an assignment, annotated or
    augmented assignment or walrus value, a `with` item and a `for` or
    comprehension iterable are reported as `binds` or `iterates`, a match
    subject as `binds` when a case captures; an argument of a callee that is
    not a module-level def of project_identity is reported as `passes`. A
    direct argument (positional, keyword or starred) of a module-level def of
    project_identity is read, since that def is followed. Those two reads
    hold only for the reached function's own code: in the body of a nested
    def or lambda the call returns NESTED_BODY_PLACE instead, since the walk
    does not follow when that body runs. A call inside an argument of a
    project_identity def without being that argument is never covered by a
    read on the way up: where a `return` or a direct argument would read it,
    it returns the first `LAYOUT_CALL_PLACES` slot passed, or else
    INSIDE_AN_ARGUMENT_PLACE. Every other place returns its words: the first
    `LAYOUT_CALL_PLACES` slot on the way up, or else the statement's node
    type and field."""
    nested = _in_nested_body(node, parents)
    child, place, inside = node, None, False
    while id(child) in parents:
        parent, field = parents[id(child)]
        kind = type(parent).__name__
        if place is None and (kind, field) in LAYOUT_CALL_PLACES:
            place = LAYOUT_CALL_PLACES[(kind, field)]
        if isinstance(parent, ast.Return):
            value = parent.value
            read = isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in defined
            if read and nested:
                return NESTED_BODY_PLACE
            if read and inside:
                return place if place is not None else INSIDE_AN_ARGUMENT_PLACE
            return None
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and field == "value":
            return None
        if isinstance(parent, ast.withitem) and field == "context_expr":
            return None
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and field == "iter":
            return None
        if isinstance(parent, ast.Match) and field == "subject" and any(
            (isinstance(part, (ast.MatchAs, ast.MatchStar)) and part.name)
            or (isinstance(part, ast.MatchMapping) and part.rest)
            for case in parent.cases for part in ast.walk(case.pattern)
        ):
            return None
        call, direct = parent, child is node
        if isinstance(parent, (ast.keyword, ast.Starred)) and id(parent) in parents:
            call = parents[id(parent)][0]
        if isinstance(call, ast.Call) and call is not child and (
            child in call.args or parent in call.args or parent in call.keywords
        ):
            if not (isinstance(call.func, ast.Name) and call.func.id in defined):
                return None
            if direct:
                return NESTED_BODY_PLACE if nested else None
            inside = True
        if isinstance(parent, ast.stmt):
            return place if place is not None else f"`{kind}.{field}`"
        child = parent
    return place


def _layout_predicate_forms() -> tuple:
    """(predicates, unresolved): the predicates among the functions reached
    from the marker predicates through called names, and a Counter of every
    form the walk does not read, keyed (function, kind, form).

    ANCHOR: every reached function: its return annotation, every call in its
    body and every value its calls produce. The walk reads outright a direct
    call to a module-level def of project_identity (followed), a direct
    `return` of one (classified by `_return_kind`), and a callee that is an
    import or a builtin, or a method on one of them or on a local or
    parameter. FAIL CLOSED, with no exception, it reports: a callee bound in
    project_identity other than as a def (an alias, a lambda, a module-level
    assignment), or a method on one; a called local or parameter, and a
    called value that is not a name or a method (a subscript, a call's
    result), which the walk cannot resolve; every local binding of a
    project_identity call's result in any form (assignment, annotated or
    augmented assignment, walrus, a `for` or `with` target, a `match`
    capture), which also
    covers the methods later called on it; a project_identity call passed
    as an argument, positional, keyword or starred, to any callee other than
    a module-level def of project_identity (a container method such as
    `append`, `setdefault` or `extend` included); a return that carries such
    a result inside another expression (`or`, `not`, a ternary); a
    project_identity function used as a value; an annotation
    `_return_kind` cannot classify; and a project_identity call in any place
    none of those covers (`_layout_call_place`): a test of an `if`, `elif`,
    `while` or `assert`, a ternary, a comprehension `if` or a match guard, a
    match subject with no capture, a `raise` or its cause, a discarded
    expression statement, and any other place, at any depth inside it (a
    display, a subscript, an f-string, an operator); a call inside an
    argument of a project_identity call without being that argument, where
    the `return` or direct argument above it would otherwise read it; and a
    call that only a read form would cover, in the body of a nested def or
    lambda. The census arm
    compares the Counter with NAMED_LAYOUT_FORMS exactly, so a new form fails
    it.

    The walk reads project_identity's text and, through `_resolved`, the
    values its module globals hold, so it runs once per process for each
    text and each set of those values (`_layout_predicate_forms_of`), and
    each caller gets its own copy of the result."""
    predicates, unresolved = _layout_predicate_forms_of(
        Path(pi.__file__).read_text(encoding="utf-8"),
        tuple(sorted((name, id(value)) for name, value in vars(pi).items())),
    )
    return set(predicates), collections.Counter(unresolved)


@functools.lru_cache(maxsize=None)
def _layout_predicate_forms_of(text: str, _globals: tuple) -> tuple:
    """`_layout_predicate_forms` for one text of project_identity and one set
    of its module globals (`_globals`, by name and object, is the cache key)."""
    import builtins

    tree = ast.parse(text)
    defined = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    module_imports = {
        (alias.asname or alias.name).split(".")[0]
        for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    reached, frontier = set(_marker_predicates()), list(_marker_predicates())
    unresolved, predicates = collections.Counter(), set()

    def callee_root(func):
        """The Name a callee hangs from, "" for a method on a literal, or
        None for another form."""
        while isinstance(func, (ast.Attribute, ast.Call, ast.Subscript, ast.BinOp)):
            func = func.left if isinstance(func, ast.BinOp) else (
                func.func if isinstance(func, ast.Call) else func.value
            )
        if isinstance(func, ast.Constant) and isinstance(func.value, (str, bytes)):
            return ""
        return func.id if isinstance(func, ast.Name) else None

    def project_call(value) -> bool:
        return any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in defined
            for node in ast.walk(value)
        )

    while frontier:
        function = defined[frontier.pop()]
        name = function.name
        kind, why = _return_kind(function, defined)
        if kind in ("predicate", "refusal"):
            predicates.add(name)
        elif kind is None:
            unresolved[(name, "annotation", why)] += 1
        called = {id(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}
        local_imports = {
            (alias.asname or alias.name).split(".")[0]
            for node in ast.walk(function) if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        local_names = {
            node.id for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        } | {argument.arg for argument in ast.walk(function) if isinstance(argument, ast.arg)} | {
            node.name for node in ast.walk(function)
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name
        } | {node.rest for node in ast.walk(function) if isinstance(node, ast.MatchMapping) and node.rest}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id in defined and id(node) not in called:
                unresolved[(name, "uses as a value", node.id)] += 1
            if isinstance(node, ast.Call):
                if not (isinstance(node.func, ast.Name) and node.func.id in defined) and any(
                    project_call(argument)
                    for argument in [*node.args, *(keyword.value for keyword in node.keywords)]
                ):
                    unresolved[(name, "passes", ast.unparse(node))] += 1
                if isinstance(node.func, ast.Name) and node.func.id in defined:
                    if node.func.id not in reached:
                        reached.add(node.func.id)
                        frontier.append(node.func.id)
                    continue
                root = callee_root(node.func) if isinstance(node.func, (ast.Name, ast.Attribute)) else None
                if root is None:
                    unresolved[(name, "calls", ast.unparse(node.func))] += 1
                    continue
                if root == "":
                    continue
                if root in local_imports or (root not in local_names and root in module_imports):
                    continue
                if root not in local_names and hasattr(builtins, root) and root not in vars(pi):
                    continue
                if isinstance(node.func, ast.Attribute) and root in local_names:
                    continue
                unresolved[(name, "calls", ast.unparse(node.func))] += 1
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                if node.value is not None and project_call(node.value):
                    unresolved[(name, "binds", ast.unparse(node))] += 1
            if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and project_call(node.iter):
                unresolved[(name, "iterates", ast.unparse(node.iter))] += 1
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if project_call(item.context_expr):
                        unresolved[(name, "binds", ast.unparse(item))] += 1
            if isinstance(node, ast.Match) and project_call(node.subject):
                for case in node.cases:
                    if any(
                        (isinstance(part, (ast.MatchAs, ast.MatchStar)) and part.name)
                        or (isinstance(part, ast.MatchMapping) and part.rest)
                        for part in ast.walk(case.pattern)
                    ):
                        form = f"match {ast.unparse(node.subject)}: case {ast.unparse(case.pattern)}"
                        unresolved[(name, "binds", form)] += 1
            if (
                isinstance(node, ast.Return) and node.value is not None
                and project_call(node.value)
                and not (
                    isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                    and node.value.func.id in defined
                )
            ):
                unresolved[(name, "returns", ast.unparse(node.value))] += 1
        parents = {
            id(child): (parent, field)
            for parent in ast.walk(function)
            for field, value in ast.iter_fields(parent)
            for child in (value if isinstance(value, list) else [value])
            if isinstance(child, ast.AST)
        }
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in defined:
                place = _layout_call_place(node, parents, defined)
                if place is not None:
                    unresolved[(name, "uses", f"{place}: {ast.unparse(node)}")] += 1
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


def _schema_inputs() -> tuple:
    """Everything the store `ensure_initialized` creates depends on: the
    schema module's text, the read-only scope, whether the vector extension
    can load (the module's extension flag, its sqlite3 module, the
    `sqlite_vec` import as `sys.modules` holds it and that module's `load`),
    the embedding dimension the vector table takes, and the module's schema
    functions and its extension loader (`load_sqlite_vec`), which a test may
    patch. The objects themselves are the key, held by the cache, so a
    patched one is a new key and no freed object's id can be reused."""
    from scripts import database, embeddings

    return (
        Path(database.__file__).read_text(encoding="utf-8"),
        database.read_only_scope_active(),
        database.SQLITE_EXTENSIONS_ENABLED,
        database.sqlite3,
        sys.modules.get("sqlite_vec", _schema_inputs),
        getattr(sys.modules.get("sqlite_vec"), "load", _schema_inputs),
        embeddings.get_embedding_service().embedding_dimension,
        *(getattr(database, name) for name in (
            "db_connection", "get_connection", "ensure_initialized", "init_schema",
            "_init_vector_table", "_check_and_migrate_vector_table", "_migrate_ct_fields",
            "load_sqlite_vec",
        )),
    )


def _seed_bucket(store: Path, bucket: str) -> None:
    """One memory and one file row in `bucket`, in a new store at `store`.
    The empty initialized store is built once per process for each set of
    schema inputs (`_schema_inputs`, `_initialized_store`) and copied; its
    schema, rows and pragmas equal a store `ensure_initialized` creates in
    place with the same inputs."""
    from scripts.database import db_connection

    assert not store.exists(), store
    store.write_bytes(_initialized_store(_schema_inputs()))
    with db_connection(store) as conn:
        conn.execute(
            "INSERT INTO memories (id, context, project_id, session_id, created_at, "
            "updated_at) VALUES ('m1', 'seeded', ?, 's', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
            (bucket,),
        )
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f', 'src/a.py', ?)", (bucket,))
        conn.commit()


@functools.lru_cache(maxsize=None)
def _initialized_store(inputs: tuple) -> bytes:
    """The bytes of an empty store `ensure_initialized` created, for one set
    of `_schema_inputs`; `inputs` is the cache key, read by no line here."""
    import tempfile

    from scripts.database import db_connection, ensure_initialized

    with tempfile.TemporaryDirectory() as folder:
        store = Path(folder) / "m.db"
        with db_connection(store) as conn:
            ensure_initialized(conn)
            conn.commit()
        return store.read_bytes()


@pytest.mark.parametrize("change", [
    "none", "vector extension off", "embedding dimension 384", "sqlite-vec load fails", "extension loader fails",
])
def test_a_seeded_store_has_the_schema_an_in_place_store_has(tmp_path, monkeypatch, change):
    """`_seed_bucket` copies a cached empty store, so the copy must have the
    schema `ensure_initialized` gives in place under the same inputs. The
    cache is filled first with nothing changed, so a key that misses an
    input serves that first store after the change.

    RED WHEN the cache key leaves out the vector extension's state, the
    extension loader (`load_sqlite_vec`, `sqlite_vec.load`) or the embedding
    dimension."""
    from scripts import database, embeddings
    from scripts.database import db_connection, ensure_initialized

    def schema(store: Path) -> list:
        with db_connection(store) as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )]

    _seed_bucket(tmp_path / "warm.db", "b")
    if change == "vector extension off":
        monkeypatch.setattr(database, "SQLITE_EXTENSIONS_ENABLED", False)
    elif change == "embedding dimension 384":
        monkeypatch.setattr(embeddings, "EMBEDDING_DIM", 384)
    elif change == "sqlite-vec load fails":
        import sqlite_vec

        def cannot_load(conn):
            raise RuntimeError("no extension")

        monkeypatch.setattr(sqlite_vec, "load", cannot_load)
    elif change == "extension loader fails":
        def cannot_load(conn):
            raise RuntimeError("no extension")

        monkeypatch.setattr(database, "load_sqlite_vec", cannot_load)
    _seed_bucket(tmp_path / "seeded.db", "b")
    with db_connection(tmp_path / "in-place.db") as conn:
        ensure_initialized(conn)
        conn.commit()

    in_place = schema(tmp_path / "in-place.db")
    vector = [sql for _, name, sql in in_place if name == "vec_memories"]
    expected = {"none": "float[256]", "embedding dimension 384": "float[384]"}
    # control: the change reached the schema an in-place store gets
    assert [expected[change] in sql for sql in vector] == (
        [True] if change in expected else []
    ), in_place
    assert schema(tmp_path / "seeded.db") == in_place


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
        # A profile function sees every return, so the return value is read
        # only after the frame is known to be one of project_identity's
        # predicates: any other function may return an object whose truth
        # is not a bool (NotImplemented from an `__eq__`).
        if event != "return" or code.co_name not in predicates:
            return None
        if code.co_filename != pi.__file__ or not arg:
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
                _set_home(cell_patch, shape, target)
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
        whose outcomes no own layout can reach, and each must still be one;
        NOT_A_FLOOR_OUTCOME names single outcomes, each of which must still
        exist."""
        _measured, reached = git_calls
        predicates, _unresolved = _layout_predicate_forms()
        unresolved = _named_layout_forms()
        named = {key: count for key, (count, _reason) in NAMED_LAYOUT_FORMS.items()}
        assert all(reason for _count, reason in NAMED_LAYOUT_FORMS.values())
        new = {key: count for key, count in unresolved.items() if named.get(key) != count}
        gone = {key: count for key, count in named.items() if unresolved.get(key) != count}
        assert new == {} and gone == {}, (
            f"layout-walk forms not named, or named and gone: new={new} gone={gone}"
        )
        assert {"_marker_names_external_git_dir", "_submodule_checkout"} <= predicates  # control
        assert set(NOT_A_FLOOR_LAYOUT) <= predicates, set(NOT_A_FLOOR_LAYOUT) - predicates
        outcomes = _layout_outcomes(predicates)
        keys = _outcome_keys(predicates)
        assert set(NOT_A_FLOOR_OUTCOME) <= set(keys.values()), set(NOT_A_FLOOR_OUTCOME) - set(keys.values())
        unreached = sorted(
            key for key in outcomes
            if key[0] not in NOT_A_FLOOR_LAYOUT and keys.get(key) not in NOT_A_FLOOR_OUTCOME
            and not reached.get(key)
        )
        assert unreached == [], f"a layout outcome no own layout reaches: {unreached}"

    def test_each_excluded_outcome_is_reached_by_its_fault(self, tmp_path):
        """Each NOT_A_FLOOR_OUTCOME entry is computed, not stated: its fault,
        applied to a submodule checkout with no origin, makes the predicate return True from
        that outcome and from no other, and where the fault acts through the
        resolver, detect_id_for_root declines `submodule_checkout`."""
        from fixtures.identity_routes import SHAPES

        predicates, _unresolved = _layout_predicate_forms()
        lines = {key: outcome[1] for outcome, key in _outcome_keys(predicates).items()}
        wrong = {}
        for index, (key, fault) in enumerate(sorted(NOT_A_FLOOR_OUTCOME.items(), key=str)):
            base = tmp_path / f"fault{index}"
            base.mkdir()
            assert_outside_repository(base)
            target = SHAPES["control_own_submodule"](base)
            run_git("remote", "remove", "origin", cwd=target)  # a basename is all it has
            common = memory_api.main_repo_common_dir(str(target))
            assert common is not None, target  # control: the checkout is a submodule
            predicate = getattr(pi, key[0])
            returned = []

            def tracer(frame, event, value, code=predicate.__code__):
                if frame.f_code is code and event == "return" and value is True:
                    returned.append(frame.f_lineno)
                return tracer

            with pytest.MonkeyPatch.context() as patch:
                given = fault(patch, target)
                previous = sys.gettrace()
                sys.settrace(tracer)
                try:
                    result = predicate(target, common if given is None else given, target)
                finally:
                    sys.settrace(previous)
                declined = (
                    (pi.detect_id_for_root(target, common).decline_reason or {}).get("reason")
                    if given is None else "submodule_checkout"
                )
            if not (result is True and returned == [lines.get(key)] and declined == "submodule_checkout"):
                wrong[key] = (result, returned, lines.get(key), declined)
        assert wrong == {}, f"(result, true returns, expected line, decline) per excluded outcome: {wrong}"

    @pytest.mark.parametrize(
        ("statement", "place"),
        [
            ("if (_p(x),)[0]:\n        pass", "an `if` or `elif` test"),
            ("if False:\n        pass\n    elif [_p(x)][0]:\n        pass", "an `if` or `elif` test"),
            ("while {'k': _p(x)}['k']:\n        break", "a `while` test"),
            ("assert (_p(x),)[0] or True", "an `assert` test"),
            ("f'{_p(x)}'", "an expression statement, its value discarded"),
            ("(_p(x),)[0]", "an expression statement, its value discarded"),
            ("_p(x) and None", "an expression statement, its value discarded"),
            ("None if (_p(x),)[0] else None", "a ternary test"),
            ("match 0:\n        case 0 if (_p(x),)[0]:\n            pass", "a match guard"),
            ("if [1 for _ in (0,) if (_p(x),)[0]]:\n        pass", "a comprehension `if`"),
            ("raise ValueError() from (_p(x),)[0]", "a raise cause"),
            ("match _p(x):\n        case True:\n            pass", "a match subject with no capture"),
            ("raise (_p(x),)[0]", "a `raise`"),
            ("_q(not _p(x))", "an expression statement, its value discarded"),
            ("del [_p(x)][0]", "`Delete.targets`"),
            ("def g(p):\n        return _p(p)\n    return any(map(g, [x]))", NESTED_BODY_PLACE),
            ("def g(p):\n        return _p(p)\n    sorted([x], key=g)", NESTED_BODY_PLACE),
            ("async def g(p):\n        return _p(p)\n    max([x], key=g)", NESTED_BODY_PLACE),
            ("def g(p):\n        return _q(_p(p))\n    list(map(g, [x]))", NESTED_BODY_PLACE),
            ("return _q(lambda p: _p(p))", NESTED_BODY_PLACE),
            ("return _q(*(_p(x) or x))", INSIDE_AN_ARGUMENT_PLACE),
            ("return _q(_p(x) or x)", INSIDE_AN_ARGUMENT_PLACE),
            ("return _q(_p(x) if x else x)", "a ternary branch"),
            ("return _q([_p(x)][0])", INSIDE_AN_ARGUMENT_PLACE),
            ("return _q(value=not _p(x))", INSIDE_AN_ARGUMENT_PLACE),
            ("return _q(f'{_p(x)}')", INSIDE_AN_ARGUMENT_PLACE),
            ("return _q(_q(_p(x) or x))", INSIDE_AN_ARGUMENT_PLACE),
        ],
        ids=[
            "tuple in if", "list in elif", "dict in while", "tuple in assert", "f-string statement",
            "subscripted tuple statement", "boolean operand statement", "ternary test", "match guard",
            "comprehension if", "raise cause", "match subject", "raise argument",
            "nested in a project argument", "del target",
            "nested def returned through map", "nested def as a sorted key", "async nested def as a max key",
            "nested def passing it to a project def", "lambda passed to a project def",
            "starred operand returned", "operand returned", "ternary branch returned",
            "subscripted display returned", "negated keyword returned", "f-string returned",
            "operand two calls deep",
        ],
    )
    def test_a_project_call_in_an_unread_place_is_reported(self, statement, place):
        """`_layout_call_place` gives the words for every place a
        project_identity call stands that no read or reported form covers,
        however deep the call sits inside it, the body of a nested def or
        lambda included. Controls, each None: the call bound by an
        assignment, returned whole or inside another expression, iterated,
        used as a `with` item or a captured match subject, passed as a direct
        argument to a project def or to any other callee, in a lambda handed
        to another callee, and bound inside a nested def."""
        defined = {"_p", "_q"}

        def places(body):
            function = ast.parse(f"def f(x):\n    {body}\n").body[0]
            parents = {
                id(child): (parent, field)
                for parent in ast.walk(function)
                for field, value in ast.iter_fields(parent)
                for child in (value if isinstance(value, list) else [value])
                if isinstance(child, ast.AST)
            }
            return [
                _layout_call_place(node, parents, defined) for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_p"
            ]

        assert places(statement) == [place]
        for read in (
            "y = (_p(x),)[0]", "return _p(x)", "return not _p(x)", "for y in [_p(x)]:\n        pass",
            "with _p(x) as y:\n        pass", "match _p(x):\n        case y:\n            pass",
            "return _q(_p(x))", "return _q(value=_p(x))", "return _q(*_p(x))", "print(_p(x))",
            "return any(map(lambda p: _p(p), [x]))", "def g(p):\n        y = _p(p)\n        return y",
        ):
            assert places(read) == [None], read

    def test_each_named_layout_reason_states_the_code_at_its_site(self):
        """Each NAMED_LAYOUT_FORMS reason IS the text derived from the code at
        each site the layout walk does not read (its row found by
        `_layout_form_key`), compared by equality, so a reason that adds a claim (a flow
        fact, a purpose) fails as surely as one that drops a fragment. A
        bind names the project_identity function it calls and the function
        it is in, each with what `_return_kind` reads it as (not its
        annotation's spelling); a return of `not` applied
        to a direct call names the same two; a method called on a
        module-level name names that name and the call its assignment makes.
        A call in a place no read form covers names that function, the
        place in the walk's own words and the function it is in. A form of
        another shape has no derived text and fails."""
        tree = ast.parse(Path(pi.__file__).read_text(encoding="utf-8"))
        defs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        assigned = {}
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(getattr(node, "value", None), ast.Call):
                    assigned[target.id] = ast.unparse(node.value.func)

        def annotated(name):
            kind, why = _return_kind(defs[name], defs)
            return f"`{name}` ({kind or 'unclassified: ' + why})"

        def callee(node):
            """The def a direct call names, or None."""
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in defs:
                return node.func.id
            return None

        def derived(function, kind, form):
            site = annotated(function)
            if kind == "binds":
                statement = ast.parse(form).body[0]
                value = getattr(statement, "value", None)
                if callee(value):
                    return f"assigns a call to {annotated(callee(value))} to a local in {site}"
            if kind == "returns":
                value = ast.parse(form, mode="eval").body
                if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not) and callee(value.operand):
                    return f"returns `not` applied to a call to {annotated(callee(value.operand))} in {site}"
            if kind == "uses":
                place, _, call = form.partition(": ")
                value = ast.parse(call, mode="eval").body
                if callee(value) and place in set(LAYOUT_CALL_PLACES.values()):
                    return f"uses a call to {annotated(callee(value))} in {place} in {site}"
            if kind == "calls":
                value = ast.parse(form, mode="eval").body
                if (
                    isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id in assigned
                ):
                    root = value.value.id
                    return (
                        f"calls a method on `{root}`, a module-level assignment of a "
                        f"`{assigned[root]}(...)` call, in {site}"
                    )
            return None

        sites = _layout_predicate_forms()[1]
        wrong = {
            (function, kind, form): (reason, derived(function, kind, form))
            for function, kind, form in sites
            for _count, reason in [NAMED_LAYOUT_FORMS.get(_layout_form_key(function, kind, form), (0, None))]
            if reason != derived(function, kind, form)
        }
        assert len(sites) >= len(NAMED_LAYOUT_FORMS), sites  # control: every named key has a site
        assert wrong == {}, f"(stated, derived) for a named reason that is not its site's code: {wrong}"

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
    """A canonical pin binds on every route in every own layout, so no rung
    (the user setting over a cwd marker included) can answer before the
    repository the route finds. No own layout is a home directory, where no
    rung reads a pin. The unusable-pin report on the same route is the
    parity matrix's override column."""

    def test_every_route_binds_the_pin(self, pinned_answers):
        wrong = {
            cell: answer for cell, answer in pinned_answers.items()
            if answer != (f"bound:{PIN_ID}", "override file")
        }
        assert wrong == {}, f"a route did not bind the repository pin: {wrong}"

    def test_the_user_setting_route_is_measured(self, pinned_answers):
        """Control: the route that sets a user setting is one of the cells."""
        assert any("user setting" in route for _layout, route in pinned_answers)


# --- the home check when the marker's directory will not resolve ------------


class TestAnUnresolvableMarkerIsNotTheIdentityRoot:
    """When the `.git` marker's directory cannot be resolved, the home check
    tests that directory as spelled, never the identity root, so a marker in
    the home directory still lands home."""

    def test_the_spelled_marker_directory_is_tested(self, tmp_path, monkeypatch):
        import types

        home = tmp_path / "home"
        home.mkdir()

        class Unresolvable(type(Path())):
            def resolve(self, strict=False):
                raise OSError("will not resolve")

        marker = types.SimpleNamespace(parent=Unresolvable(home))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(memory_api, "find_git_marker", lambda start: marker)
        elsewhere = tmp_path / "elsewhere"
        assert memory_api._home_checkout(elsewhere, home / "sub", None) == home


# --- every git question about the identity asks its own repository ----------


class TestTheIdentityIsAskedOfItsOwnRepository:
    """A repository whose `core.worktree` names a directory inside another
    repository: git's top level is that directory, so a git question asked
    THERE answers for the other repository. Every question about the
    identity is asked of the common dir it was read from."""

    def _layout(self, base: Path):
        from fixtures.identity_routes import VICTIM

        victim = make_repo(base / "victim", remote=VICTIM)
        run_git("worktree", "add", "-q", str(base / "victim-linked"), cwd=victim)
        project = make_repo(base / "project", remote=OWN)
        run_git("worktree", "add", "-q", str(base / "project-linked"), cwd=project)
        (victim / "wt").mkdir()
        run_git("config", "core.worktree", str(victim / "wt"), cwd=project)
        return victim, project

    def test_the_read_gate_lists_the_identitys_own_worktrees(self, tmp_path, monkeypatch):
        """The read gate's worktree listing and submodule exemption use the
        common dir the identity came from: this repository's own linked
        worktree is in its gate, and the other repository's never is."""
        victim, project = self._layout(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        store = elsewhere / "m.db"
        _seed_bucket(store, project.name)
        from scripts import database

        listed, anchors = [], []
        real_roots = memory_api.project_identity.verified_worktree_roots
        real_plausibility = database.legacy_candidate_plausibility

        def roots(repo_root, common_dir, listing=None):
            found = real_roots(repo_root, common_dir, listing)
            listed.append((common_dir, found))
            return found

        def plausibility(*args, **kwargs):
            anchors.append(kwargs.get("root_common"))
            return real_plausibility(*args, **kwargs)

        _clear(monkeypatch)
        _fresh_scope_state()
        monkeypatch.chdir(project)
        monkeypatch.setattr(memory_api.project_identity, "verified_worktree_roots", roots)
        monkeypatch.setattr(database, "legacy_candidate_plausibility", plausibility)
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._project_id == "github.com/own/project", memory._project_id
        memory._read_project_scopes()
        own = (project / ".git").resolve()
        assert listed and anchors, "the read gate was not reached"
        assert [Path(common).resolve() for common, _found in listed] == [own]
        assert [Path(anchor).resolve() for anchor in anchors] == [own]
        foreign = (tmp_path / "victim-linked").resolve()
        assert all(foreign not in found for _common, found in listed), listed
        assert all((tmp_path / "project-linked").resolve() in found for _common, found in listed), listed

    def test_the_suppression_disclosure_reads_the_given_common_dir(self, tmp_path):
        """`_legacy_suppression` tests the common dir the identity came from;
        git is asked at the root only when no common dir was recorded."""
        victim, project = self._layout(tmp_path)
        assert memory_api._legacy_suppression(victim / "wt", project / ".git") == (None, None)
        asked = []
        real = memory_api.main_repo_common_dir

        def spy(start=None):
            asked.append(start)
            return real(start)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(memory_api, "main_repo_common_dir", spy)
            memory_api._legacy_suppression(victim / "wt", project / ".git")
            assert asked == []
            memory_api._legacy_suppression(victim / "wt")
            assert asked == [str(victim / "wt")]


class TestAGitTheBareRepositoryAboveLooksPast:
    @pytest.mark.requires_non_root
    def test_an_unreadable_git_answers_as_the_same_folder_with_none(self, tmp_path_factory):
        """A `.git` directory git cannot read, in a folder inside a bare
        repository: git answers the bare repository's git dir for it, so it
        is looked past, and every route answers as for the same folder with
        no `.git`."""
        import os

        from fixtures.identity_routes import (
            _no_dotgit_under_bare_repository,
            _stray_dotgit_under_bare_repository,
        )

        answers = {}
        for name, build in (
            ("unreadable", _stray_dotgit_under_bare_repository),
            ("none", _no_dotgit_under_bare_repository),
        ):
            base = tmp_path_factory.mktemp(f"bare_{name}")
            assert_outside_repository(base)
            target = build(base)
            (target / "sub").mkdir()
            if name == "unreadable":
                os.chmod(target / ".git", 0)
            try:
                for index, (route, (run, _sources)) in enumerate(ROUTES.items()):
                    elsewhere = base / f"elsewhere-{index}"
                    elsewhere.mkdir()
                    with pytest.MonkeyPatch.context() as cell_patch:
                        _clear(cell_patch)
                        answers[(name, route)] = _answer(run(cell_patch, target, elsewhere))[0]
            finally:
                if name == "unreadable":
                    os.chmod(target / ".git", 0o755)
        differ = {
            route: (answers[("unreadable", route)], answers[("none", route)])
            for route in ROUTES
            if answers[("unreadable", route)] != answers[("none", route)]
        }
        assert differ == {}, f"an unreadable .git under a bare repository answered differently: {differ}"


def _worktree_named_elsewhere(main: Path, worktree: Path, elsewhere: Path) -> None:
    """A linked worktree of `main` whose `core.worktree` names `elsewhere`."""
    run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    run_git("config", "extensions.worktreeConfig", "true", cwd=main)
    elsewhere.mkdir(parents=True)
    run_git("config", "--worktree", "core.worktree", str(elsewhere), cwd=worktree)


class TestTheGitDirAnswerIsNeverSplitAtALineSeparator:
    """`_ask_git_dir_and_toplevel` reads git's two answers without splitting
    at a line separator, one arm per branch its docstring lists, each asking
    real git. LINE_SEPARATOR_NAME holds every character `str.splitlines()`
    splits at."""

    def test_the_own_git_dir_is_read_by_its_known_start(self, tmp_path):
        from fixtures.identity_routes import LINE_SEPARATOR_NAME

        assert_outside_repository(tmp_path)
        root = make_repo(tmp_path / LINE_SEPARATOR_NAME / "target")
        elsewhere = tmp_path / "free" / LINE_SEPARATOR_NAME
        elsewhere.mkdir(parents=True)
        run_git("config", "core.worktree", str(elsewhere), cwd=root)
        real = os.path.realpath(root)
        assert memory_api._ask_git_dir_and_toplevel(str(root)) == (
            os.path.join(real, ".git"), os.path.realpath(elsewhere),
        )

    def test_a_bare_repository_answers_its_whole_git_dir(self, tmp_path):
        from fixtures.identity_routes import LINE_SEPARATOR_NAME

        assert_outside_repository(tmp_path)
        bare = tmp_path / LINE_SEPARATOR_NAME / "store.git"
        bare.mkdir(parents=True)
        run_git("init", "-q", "--bare", str(bare), cwd=tmp_path)
        assert memory_api._ask_git_dir_and_toplevel(str(bare)) == (os.path.realpath(bare), None)

    def test_another_git_dir_with_plain_paths_is_split_at_its_one_newline(self, tmp_path):
        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree, elsewhere = tmp_path / "wt", tmp_path / "free" / "wt"
        _worktree_named_elsewhere(main, worktree, elsewhere)
        assert memory_api._ask_git_dir_and_toplevel(str(worktree)) == (
            os.path.join(os.path.realpath(main), ".git", "worktrees", "wt"),
            os.path.realpath(elsewhere),
        )

    def test_another_git_dir_ends_at_the_start_it_was_asked_from(self, tmp_path):
        from fixtures.identity_routes import LINE_SEPARATOR_NAME

        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree = tmp_path / LINE_SEPARATOR_NAME
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        git_dir, toplevel = memory_api._ask_git_dir_and_toplevel(str(worktree))
        assert toplevel == os.path.realpath(worktree)
        assert os.path.dirname(git_dir) == os.path.join(os.path.realpath(main), ".git", "worktrees")

    def test_an_answer_ending_at_no_ancestor_keeps_its_git_dir(self, tmp_path):
        """A linked worktree whose `core.worktree` names a folder outside
        its ancestors, with a newline in that folder's name: the git dir is
        the whole answer and the top level is unknown."""
        from fixtures.identity_routes import LINE_SEPARATOR_NAME

        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree, elsewhere = tmp_path / "wt", tmp_path / "free" / LINE_SEPARATOR_NAME
        _worktree_named_elsewhere(main, worktree, elsewhere)
        git_dir = os.path.join(os.path.realpath(main), ".git", "worktrees", "wt")
        assert memory_api._ask_git_dir_and_toplevel(str(worktree)) == (
            f"{git_dir}\n{os.path.realpath(elsewhere)}", None,
        )


class TestAGitPathAnswerIsNeverStripped:
    """Each reader of one path from git, or from a `.git` file, keeps a path
    that ends in a space or holds a newline whole. One arm per reader, each
    against real git."""

    def test_the_common_dir_keeps_a_trailing_space(self, tmp_path):
        assert_outside_repository(tmp_path)
        store = tmp_path / "store "
        run_git("init", "-q", f"--separate-git-dir={store}", str(tmp_path / "target"), cwd=tmp_path)
        assert memory_api.main_repo_common_dir(str(tmp_path / "target")) == store.resolve()

    def test_the_git_dir_answer_keeps_a_trailing_space(self, tmp_path):
        assert_outside_repository(tmp_path)
        store = tmp_path / "store "
        run_git("init", "-q", f"--separate-git-dir={store}", str(tmp_path / "target"), cwd=tmp_path)
        answer = pi.git_dir_answer(str(tmp_path / "target"))
        assert answer is not None and os.path.realpath(answer) == os.path.realpath(store)

    def test_the_marker_target_keeps_a_trailing_space(self, tmp_path):
        assert_outside_repository(tmp_path)
        store = tmp_path / "store "
        run_git("init", "-q", f"--separate-git-dir={store}", str(tmp_path / "target"), cwd=tmp_path)
        assert pi._marker_target(tmp_path / "target") == (store.resolve(), tmp_path / "target")

    def test_the_top_level_keeps_a_trailing_space(self, tmp_path):
        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree = tmp_path / "wt "
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        assert memory_api._ask_rev_parse(str(worktree), ("--show-toplevel",)) == os.path.realpath(worktree)

    def test_the_worktree_listing_keeps_a_newline(self, tmp_path):
        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "main")
        worktree = tmp_path / "w\nt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        listed = memory_api.project_identity.worktree_toplevels(memory_api.project_identity._ask_worktree_records(None, main / ".git"))
        assert listed == {main.resolve(), worktree.resolve()}

    def test_the_verified_worktrees_keep_a_newline_in_the_admin_path(self, tmp_path):
        """The worktree's `.git` file names an admin dir under a main
        checkout whose folder holds a newline."""
        assert_outside_repository(tmp_path)
        main = make_repo(tmp_path / "ma\nin")
        worktree = tmp_path / "wt"
        run_git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        roots = memory_api.project_identity.verified_worktree_roots(main, (main / ".git").resolve())
        assert roots == [main.resolve(), worktree.resolve()]

    def test_a_recorded_worktree_keeps_a_trailing_space(self, tmp_path):
        """A submodule admin dir whose `core.worktree` names the checkout
        asked about, a folder ending in a space, records that checkout."""
        assert_outside_repository(tmp_path)
        admin = tmp_path / "admin"
        run_git("init", "-q", "--bare", str(admin), cwd=tmp_path)
        checkout = tmp_path / "checkout "
        checkout.mkdir()
        run_git("--git-dir", str(admin), "config", "core.worktree", str(checkout), cwd=tmp_path)
        assert pi._admin_dir_names_another_checkout(admin, checkout) is False


_CONVERSION_MISSING = re.compile(r'required field "(\w+)" missing from (\w+)')
_CONVERSION_SORT = re.compile(r"expected some sort of (\w+)")
_CONVERSION_IDENTIFIER = "AST identifier must be of type str"


def _compiler_classes() -> dict:
    """Every node class the compiler's own module (`_ast`) defines."""
    import _ast

    return {
        name: node for name, node in vars(_ast).items()
        if isinstance(node, type) and issubclass(node, ast.AST) and node is not ast.AST
    }


def _grammar_type(node_class) -> str:
    """The grammar type a node of `node_class` fills: its base class, or
    the class itself when it is a product type."""
    base = node_class.__bases__[0]
    return base.__name__ if base is not ast.AST else node_class.__name__


def _probe_module(*statements):
    return ast.Module(body=list(statements), type_ignores=[])


def _probe_name():
    return ast.Name(id="x", ctx=ast.Load())


def _probe_arguments(**lists):
    fields = {field: [] for field in ("posonlyargs", "args", "kwonlyargs", "kw_defaults", "defaults")}
    fields.update(lists)
    return ast.arguments(**fields)


# Where a node of each grammar type sits so that compile() converts it. A
# grammar type with no entry here leaves its fields unread, which fails the
# arm below.
_PROBE_PLACES = {
    "mod": lambda n: (n, {"Module": "exec", "Expression": "eval", "Interactive": "single",
                          "FunctionType": "func_type"}[type(n).__name__]),
    "expr": lambda n: (ast.Expression(body=n), "eval"),
    "stmt": lambda n: (_probe_module(n), "exec"),
    "expr_context": lambda n: (ast.Expression(body=ast.Name(id="x", ctx=n)), "eval"),
    "boolop": lambda n: (ast.Expression(body=ast.BoolOp(op=n, values=[_probe_name(), _probe_name()])), "eval"),
    "operator": lambda n: (ast.Expression(body=ast.BinOp(left=_probe_name(), op=n, right=_probe_name())), "eval"),
    "unaryop": lambda n: (ast.Expression(body=ast.UnaryOp(op=n, operand=_probe_name())), "eval"),
    "cmpop": lambda n: (
        ast.Expression(body=ast.Compare(left=_probe_name(), ops=[n], comparators=[_probe_name()])), "eval"
    ),
    "comprehension": lambda n: (ast.Expression(body=ast.ListComp(elt=_probe_name(), generators=[n])), "eval"),
    "excepthandler": lambda n: (
        _probe_module(ast.Try(body=[ast.Pass()], handlers=[n], orelse=[], finalbody=[])), "exec"
    ),
    "arguments": lambda n: (ast.Expression(body=ast.Lambda(args=n, body=_probe_name())), "eval"),
    "arg": lambda n: (ast.Expression(body=ast.Lambda(args=_probe_arguments(args=[n]), body=_probe_name())), "eval"),
    "keyword": lambda n: (ast.Expression(body=ast.Call(func=_probe_name(), args=[], keywords=[n])), "eval"),
    "alias": lambda n: (_probe_module(ast.Import(names=[n])), "exec"),
    "withitem": lambda n: (_probe_module(ast.With(items=[n], body=[ast.Pass()])), "exec"),
    "match_case": lambda n: (_probe_module(ast.Match(subject=_probe_name(), cases=[n])), "exec"),
    "pattern": lambda n: (
        _probe_module(ast.Match(subject=_probe_name(), cases=[ast.match_case(pattern=n, body=[ast.Pass()])])),
        "exec",
    ),
    "type_ignore": lambda n: (ast.Module(body=[], type_ignores=[n]), "exec"),
    "type_param": lambda n: (
        _probe_module(ast.TypeAlias(name=ast.Name(id="x", ctx=ast.Store()), type_params=[n], value=_probe_name())),
        "exec",
    ),
}


def _conversion_error(node_class, node):
    """The compiler's TypeError text converting `node` in its place, or None
    once it converts (a later check refusing the tree counts as converted).
    A grammar type with no place never converts."""
    place = _PROBE_PLACES.get(_grammar_type(node_class))
    if place is None:
        return f"no place for {_grammar_type(node_class)}"
    tree, mode = place(node)
    try:
        compile(ast.fix_missing_locations(tree), "<probe>", mode)
    except TypeError as error:
        return str(error)
    except (ValueError, SyntaxError):
        return None
    return None


def _probe_build(node_class, found, reached, depth=0):
    """A `node_class` node whose every field converts, built field by field
    in the order the compiler converts them. Handing each field an int first
    makes the compiler name the field's type; each field it names as an
    identifier goes in `found`, and each field it reached in `reached`."""
    if depth > 5:
        return None
    # Every field is passed (a call missing a required field is deprecated
    # from Python 3.13), then removed: the probe needs each field unset until
    # it hands that field a value.
    fields = list(node_class._fields)
    node = node_class(**dict.fromkeys(fields))
    for field in fields:
        delattr(node, field)
    classes = _compiler_classes()
    for index, field in enumerate(fields):
        later = set(fields[index + 1:])

        def converts_past(error):
            missing = _CONVERSION_MISSING.search(error or "")
            return error is None or bool(
                missing and missing.group(2) == node_class.__name__ and missing.group(1) in later
            )

        def trial(value):
            setattr(node, field, value)
            return _conversion_error(node_class, node)

        error = trial(1)
        missing = _CONVERSION_MISSING.search(error or "")
        if not (missing and missing.group(1) in fields[:index]):
            reached.add((node_class.__name__, field))
        if error and _CONVERSION_IDENTIFIER in error:
            found.add((node_class.__name__, field))
        if converts_past(error):
            continue
        as_list = bool(error and "must be a list" in error)
        if as_list:
            error = trial([1])
            if error and _CONVERSION_IDENTIFIER in error:
                found.add((node_class.__name__, field))
            if converts_past(error):
                continue
        value = None
        if error and "must be of type str" in error:
            value = "x"
        else:
            sort = _CONVERSION_SORT.search(error or "")
            missing = _CONVERSION_MISSING.search(error or "")
            kind = sort.group(1) if sort else missing.group(2) if missing else None
            if kind in classes and kind != node_class.__name__:
                base = classes[kind]
                choices = [c for c in classes.values() if c is not base and issubclass(c, base)] or [base]
                for choice in sorted(choices, key=lambda c: (len(c._fields), c.__name__)):
                    value = _probe_build(choice, found, set(), depth + 1)
                    if value is not None:
                        break
        if value is None or not converts_past(trial([value] if as_list else value)):
            return None
    return node


def _grammar_identifier_fields() -> tuple:
    """(identifier fields, fields the compiler was never made to convert):
    every (node class, field) of Python's grammar that holds an identifier,
    as the compiler itself answers when handed an int in that field. A form
    the grammar gains is a form the arms below must cover; a field the probe
    cannot reach is listed, never assumed not to be an identifier."""
    found, reached, every = set(), set(), set()
    for name, node_class in _compiler_classes().items():
        if node_class.__subclasses__():
            continue
        every |= {(name, field) for field in node_class._fields}
        if node_class._fields and _grammar_type(node_class) in _PROBE_PLACES:
            _probe_build(node_class, found, reached)
    return found, every - reached


# A Name in each context the grammar names (`expr_context`): stored and
# deleted names bind, a loaded one does not.
_NAME_CONTEXT_FORMS = {"Store": ("X = 1", 1), "Del": ("del X", 1), "Load": ("print(X)", 0)}


class TestEveryBindingFormOfADetailIsCounted:
    """`_bindings` counts every place the grammar lets a name be bound, and
    a detail constant bound anywhere a second time is not admitted as a
    refusal's detail. The forms are the grammar's own identifier fields."""

    def test_every_identifier_field_of_the_grammar_has_a_form(self):
        fields, unread = _grammar_identifier_fields()
        assert unread == set(), sorted(unread)  # every field of the grammar was converted
        assert ("Name", "id") in fields and ("alias", "asname") in fields  # control
        assert fields == set(_IDENTIFIER_FORMS) | {("alias", "asname")}, (
            fields ^ (set(_IDENTIFIER_FORMS) | {("alias", "asname")})
        )

    def test_every_expression_context_has_a_form(self):
        """The contexts are the ones the compiler accepts: each subclass of
        `ast.expr_context` the compiler's own module (`_ast`) defines, which
        `compile` takes on a Name in a load, store or delete position. The
        others (`ast` keeps deprecated ones in Python) it refuses in every
        position, so a context the compiler gains or drops changes this
        set."""
        import _ast

        def positions(context):
            name = ast.Name(id="x", ctx=context())
            trees = {
                "load": (ast.Expression(body=name), "eval"),
                "store": (ast.Module(body=[ast.Assign(targets=[name], value=ast.Constant(1))], type_ignores=[]), "exec"),
                "del": (ast.Module(body=[ast.Delete(targets=[name])], type_ignores=[]), "exec"),
            }
            accepted = set()
            for position, (tree, mode) in trees.items():
                try:
                    compile(ast.fix_missing_locations(tree), "<context>", mode)
                except (TypeError, ValueError, SyntaxError):
                    continue
                accepted.add(position)
            return accepted

        subclasses = ast.expr_context.__subclasses__()
        compiled = {c.__name__ for c in subclasses if vars(_ast).get(c.__name__) is c}
        assert "Store" in compiled, compiled  # control
        refused = {c.__name__: positions(c) for c in subclasses if c.__name__ not in compiled}
        assert all(not accepted for accepted in refused.values()), refused
        unplaced = {c.__name__ for c in subclasses if c.__name__ in compiled and not positions(c)}
        assert unplaced == set(), unplaced
        assert compiled == set(_NAME_CONTEXT_FORMS), compiled

    @pytest.mark.parametrize(
        "source",
        [*_IDENTIFIER_FORMS.values(), "import y as X", *_DOTTED_FORMS],
    )
    def test_each_form_counts_once(self, source):
        assert _bindings(ast.parse(source), "X") == 1, source

    @pytest.mark.parametrize("context", sorted(_NAME_CONTEXT_FORMS))
    def test_a_name_counts_in_a_binding_context_only(self, context):
        source, count = _NAME_CONTEXT_FORMS[context]
        assert _bindings(ast.parse(source), "X") == count, source

    @pytest.mark.parametrize(
        "source",
        [*_IDENTIFIER_FORMS.values(), "import y as X", *_DOTTED_FORMS, "del X"],
    )
    def test_a_detail_bound_again_is_not_a_refusal_detail(self, source):
        """A module whose refusal returns `X`, bound once as a string and
        again by `source`, has no refusal; bound once only, it has one."""
        once = 'X = "detail"\n\ndef refusal() -> Optional[str]:\n    return X\n'
        for module, expected in ((once, True), (once + "\n" + source + "\n", False)):
            tree = ast.parse(module)
            defined = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
            assert _is_refusal(defined["refusal"], defined, frozenset(), tree) is expected, module
