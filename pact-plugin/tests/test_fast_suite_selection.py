"""Table-driven probes for the pure fast-suite selection core.

The selector is the piece that decides which tests a PR runs. Every row here
asserts a ``Selection`` FIELD — membership, an add-back reason, or the
SELECT_ALL reason — never a count: the corpus is checkout-dependent by
construction, and a pinned total goes red on every legitimate test addition.

Rows fall into three groups:

* pure ``select()`` rows, built from dataclasses so no filesystem or git is
  involved;
* ``load_manifest()`` rejection rows, each run against a rig that loads
  cleanly, so the bad input is the only difference (``test_valid_rig_loads``
  is the non-vacuity floor);
* argv rows, because the absolute-path contract is what stops pytest from
  reinterpreting an ignore relative to its working directory.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import ci_contract

SELECTOR = Path("scripts") / "fast_suite_selection.py"

PRODUCTION_ROOTS = ("hooks/", "scripts/", "skills/", "telegram/", "bin/")
SELECTION_MACHINERY = (
    "scripts/fast_suite_selection.py",
    "scripts/fast_suite_args.py",
    "scripts/run_fast_suite.py",
    "scripts/verify_fast_summary.py",
    "scripts/fast_suite_exclusions.json",
    ".github/workflows/fast-tests.yml",
    "constraints-ci.txt",
    "pyproject.toml",
)
# The exact ten discovery/isolation guards the manifest pins. The loader
# rejects any other set, so this constant is the contract, not a sample.
GUARD_FILES = (
    "tests/test_ci_collection_scope.py",
    "tests/test_import_hygiene.py",
    "tests/test_local_lint_parity.py",
    "tests/test_path_setup_pin.py",
    "tests/test_suite_home_isolation.py",
    "tests/test_conftest_config_root_isolation.py",
    "tests/test_audit_protocol.py",
    "tests/test_memory_store_isolation.py",
    "skills/pact-handoff-harvest/test_skill_loading_harvest.py",
    "skills/pact-agent-teams/test_skill_loading_agent_teams.py",
)

DISCOVERED = frozenset({
    "tests/test_slow.py",
    "tests/test_fast.py",
    "skills/pact-example/test_skill_loading.py",
})


@pytest.fixture(scope="module")
def core():
    root = ci_contract.require_root("the fast-suite selection core")
    path = root / SELECTOR
    assert path.is_file(), (
        f"source checkout detected at {root} but {path} is missing. The "
        f"selection core is specified at {SELECTOR.as_posix()}; its absence "
        f"is a FAILURE, not a skip."
    )
    spec = importlib.util.spec_from_file_location(
        "_pact_fast_suite_selection_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the module's dataclasses resolve string
    # annotations through sys.modules[cls.__module__], which is None for a
    # module loaded by path but never registered.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _manifest(core, exclusion_paths=("tests/test_slow.py",), seams=None):
    seams = seams or {}
    exclusions = tuple(
        core.Exclusion(
            path=path,
            displayed_seconds=10.0,
            tests=1,
            mechanism="synthetic row",
            production_seams=tuple(
                seams.get(path, ("hooks/slow_seam.py",))),
        )
        for path in exclusion_paths
    )
    return core.Manifest(
        path_base="plugin_root",
        production_roots=PRODUCTION_ROOTS,
        selection_machinery=SELECTION_MACHINERY,
        guard_files=GUARD_FILES,
        exclusions=exclusions,
    )


def _select(core, changed=(), status="ok", discovered=DISCOVERED, **kwargs):
    return core.select(discovered, _manifest(core, **kwargs), list(changed),
                       status)


def _assert_select_all(result, discovered=DISCOVERED):
    assert result.select_all_reason is not None
    assert result.included == discovered
    assert result.excluded == frozenset()
    assert result.added_back == ()


class TestDefaultInclusion:
    """New and unknown files are included; only manifest paths are removed."""

    def test_a_new_file_not_in_the_manifest_is_included_by_default(self, core):
        result = _select(core)
        assert "tests/test_fast.py" in result.included
        assert "tests/test_slow.py" not in result.included
        assert result.excluded == frozenset({"tests/test_slow.py"})
        assert result.select_all_reason is None

    def test_a_changed_path_outside_the_plugin_has_no_effect(self, core):
        result = _select(core, changed=["README.md"])
        assert result.select_all_reason is None
        assert "tests/test_fast.py" in result.included

    def test_a_changed_skill_test_is_not_unmapped_production(self, core):
        result = _select(
            core, changed=["pact-plugin/skills/pact-example/test_skill_loading.py"])
        assert result.select_all_reason is None
        assert "skills/pact-example/test_skill_loading.py" in result.included


class TestAddBacks:
    """A changed test or seam pulls its exclusion back into the run."""

    def test_changed_excluded_test_is_added_back(self, core):
        result = _select(core, changed=["pact-plugin/tests/test_slow.py"])
        assert result.select_all_reason is None
        assert "tests/test_slow.py" in result.included
        assert result.excluded == frozenset()
        assert len(result.added_back) == 1
        add_back = result.added_back[0]
        assert add_back.path == "tests/test_slow.py"
        assert add_back.reason == "changed_test"
        # matched_changed is REPO-relative (spec §3.1): exactly the path
        # changed_paths returned, prefix present.
        assert add_back.matched_changed == "pact-plugin/tests/test_slow.py"

    def test_changed_mapped_seam_adds_back_dependents(self, core):
        result = _select(
            core,
            changed=["pact-plugin/hooks/merge_guard_pre.py"],
            seams={"tests/test_slow.py": ("hooks/merge_guard_pre.py",)},
        )
        assert result.select_all_reason is None
        assert "tests/test_slow.py" in result.included
        assert len(result.added_back) == 1
        add_back = result.added_back[0]
        assert add_back.reason == "changed_seam"
        # matched_seam is PLUGIN-relative (the manifest seam); matched_changed
        # is REPO-relative (the path changed_paths returned).
        assert add_back.matched_seam == "hooks/merge_guard_pre.py"
        assert add_back.matched_changed == "pact-plugin/hooks/merge_guard_pre.py"


@pytest.mark.parametrize(
    "case_id,changed,status,kwargs,reason",
    [
        ("git-error", (), "git-error", {}, "changed-paths:git-error"),
        ("invalid-base", (), "invalid-base", {}, "changed-paths:invalid-base"),
        ("empty-diff", (), "empty-diff", {}, "changed-paths:empty-diff"),
        (
            "selection-machinery",
            ("scripts/run_fast_suite.py",),
            "ok",
            {},
            "selection-machinery",
        ),
        (
            "constraints-pin",
            ("constraints-ci.txt",),
            "ok",
            {},
            "selection-machinery",
        ),
        (
            "shared-conftest",
            ("pact-plugin/tests/conftest.py",),
            "ok",
            {},
            "shared-conftest",
        ),
        (
            "plugin-root-conftest",
            ("pact-plugin/conftest.py",),
            "ok",
            {},
            "shared-conftest",
        ),
        (
            "shared-fixture",
            ("pact-plugin/tests/fixtures/sample.py",),
            "ok",
            {},
            "shared-fixture",
        ),
        (
            "test-support-file",
            ("pact-plugin/tests/helpers.py",),
            "ok",
            {},
            "test-support-file",
        ),
        (
            "plugin-root-file",
            ("pact-plugin/setup.cfg",),
            "ok",
            {},
            "plugin-root-file",
        ),
        (
            "unmapped-production",
            ("pact-plugin/hooks/unmapped.py",),
            "ok",
            {},
            "unmapped-production:hooks/unmapped.py",
        ),
    ],
)
def test_select_all_fail_closed(core, case_id, changed, status, kwargs, reason):
    """Every fail-closed rule selects everything and says why."""
    result = _select(core, changed=changed, status=status, **kwargs)
    assert result.select_all_reason == reason, case_id
    _assert_select_all(result)


def test_mapped_and_unmapped_production_in_one_change_selects_all(core):
    """One unmapped production path is enough; mapped ones cannot rescue it."""
    result = _select(
        core,
        changed=[
            "pact-plugin/hooks/merge_guard_pre.py",
            "pact-plugin/hooks/unmapped.py",
        ],
        seams={"tests/test_slow.py": ("hooks/merge_guard_pre.py",)},
    )
    assert result.select_all_reason == "unmapped-production:hooks/unmapped.py"


# ---------------------------------------------------------------------------
# Manifest loading: a rejection rig that otherwise loads cleanly.
# ---------------------------------------------------------------------------

def _write(path, text="def test_ok():\n    assert True\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_rig(tmp_path):
    """A valid manifest rig: 24 exclusions, 10 guards, every path existing."""
    root = tmp_path / "rig"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
    plugin = root / "pact-plugin"
    for directory in ("hooks", "scripts", "skills", "telegram", "bin"):
        (plugin / directory).mkdir(parents=True, exist_ok=True)

    guards = list(GUARD_FILES)
    exclusions = [f"tests/test_slow_{index}.py" for index in range(24)]
    for relative in guards + exclusions:
        _write(plugin / relative)

    exclusion_entries = []
    for index, relative in enumerate(exclusions):
        seam = f"hooks/seam_{index}.py"
        _write(plugin / seam)
        exclusion_entries.append({
            "path": relative,
            "displayed_seconds": 10.0,
            "tests": 1,
            "mechanism": "synthetic slow file",
            "production_seams": [seam],
        })

    for relative in SELECTION_MACHINERY:
        _write(root / relative, "")
    document = {
        "schema_version": 1,
        "path_base": "plugin_root",
        "baseline": {
            "measured_at_commit": "0" * 40,
            "interpreter": "3.12.3",
            "pytest": "9.2.0",
            "platform": "linux",
            "collected_node_ids": 100,
            "excluded_node_ids": 24,
            "retained_node_ids": 76,
            "retained_files": 24,
            "excluded_files": 24,
            "displayed_seconds_total": 10.0,
            "wall_seconds_total": 11.0,
        },
        "production_roots": list(PRODUCTION_ROOTS),
        "selection_machinery": list(SELECTION_MACHINERY),
        "guard_files": guards,
        "exclusions": exclusion_entries,
    }
    return root, plugin, document


def _write_manifest(root, document):
    path = root / "scripts" / "fast_suite_exclusions.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def test_valid_rig_loads(core, tmp_path):
    """Non-vacuity floor: the rejection rows below start from a valid manifest."""
    root, _, document = _build_rig(tmp_path)
    manifest = core.load_manifest(_write_manifest(root, document))
    assert len(manifest.exclusions) == 24
    assert len(manifest.guard_files) == 10


def _mutate_exclusion(document, index, **changes):
    document["exclusions"][index].update(changes)


def _swap_a_guard_for_a_real_but_wrong_path(document, root):
    """Replace one real guard with a DIFFERENT existing test module.

    Distinct from the guard-in-exclusions row: that one moves a guard into the
    exclusions and the membership rule can see the overlap. This one keeps the
    shape legal — ten entries, every path exists, every path is a test module —
    and is caught ONLY by the loader's identity pin. A count-only loader
    accepts it, and the substituted list then lets a real guard be moved into
    exclusions without the membership rule firing.
    """
    _write(root / "pact-plugin" / "tests" / "test_unused_extra.py")
    document["guard_files"][0] = "tests/test_unused_extra.py"


@pytest.mark.parametrize(
    "case_id,mutate,token",
    [
        (
            "stale-path",
            lambda doc, root: (root / "pact-plugin" / doc["exclusions"][0]["path"]).unlink(),
            "tests/test_slow_0.py",
        ),
        (
            "glob",
            lambda doc, root: _mutate_exclusion(
                doc, 0, path="tests/test_slow_*.py"),
            "tests/test_slow_*.py",
        ),
        (
            "rootdir-relative",
            lambda doc, root: _mutate_exclusion(
                doc, 0, path="pact-plugin/tests/test_slow_0.py"),
            "pact-plugin/tests/test_slow_0.py",
        ),
        (
            "duplicate",
            lambda doc, root: _mutate_exclusion(
                doc, 1, path=doc["exclusions"][0]["path"]),
            "duplicate",
        ),
        (
            "guard-in-exclusions",
            lambda doc, root: _mutate_exclusion(
                doc, 0, path=doc["guard_files"][0]),
            "guard",
        ),
        (
            "guard-identity-swapped",
            _swap_a_guard_for_a_real_but_wrong_path,
            "guard set changed",
        ),
        (
            "unknown-key",
            lambda doc, root: doc.update({"surprise": 1}),
            "surprise",
        ),
        (
            "absolute-path",
            lambda doc, root: _mutate_exclusion(doc, 0, path="/etc/passwd"),
            "/etc/passwd",
        ),
        (
            "backslash",
            lambda doc, root: _mutate_exclusion(
                doc, 0, path="tests\\test_slow_0.py"),
            "backslash",
        ),
        (
            "not-python",
            lambda doc, root: _mutate_exclusion(doc, 0, path="tests/notes.txt"),
            "notes.txt",
        ),
        (
            "seam-is-directory",
            lambda doc, root: (
                (root / "pact-plugin" / "hooks" / "seam_dir").mkdir(parents=True),
                doc["exclusions"][0].update({"production_seams": ["hooks/seam_dir"]}),
            ),
            "hooks/seam_dir",
        ),
    ],
)
def test_manifest_rejections_name_the_bad_input(core, tmp_path, case_id, mutate, token):
    """Each rejection is the only difference from a rig that loads."""
    root, _, document = _build_rig(tmp_path)
    mutate(document, root)
    path = _write_manifest(root, document)
    with pytest.raises(core.ManifestError) as excinfo:
        core.load_manifest(path)
    rendered = "\n".join(excinfo.value.errors)
    assert token in rendered, (
        f"{case_id}: the error list does not name {token!r}. "
        f"Errors: {excinfo.value.errors}"
    )


# ---------------------------------------------------------------------------
# Path normalization and argv emission.
# ---------------------------------------------------------------------------

def test_repo_to_plugin_is_a_pure_table(core):
    assert core.repo_to_plugin("pact-plugin/tests/x.py") == "tests/x.py"
    assert core.repo_to_plugin("pact-plugin") == ""
    assert core.repo_to_plugin("README.md") is None
    assert core.repo_to_plugin("scripts/run_fast_suite.py") is None


def _selection(core, excluded, discover=None):
    discover = DISCOVERED if discover is None else discover
    included = frozenset(discover) - frozenset(excluded)
    return core.Selection(
        status="ok",
        select_all_reason=None,
        discovered=frozenset(discover),
        included=included,
        excluded=frozenset(excluded),
        added_back=(),
    )


def test_to_pytest_args_are_absolute_and_exist(core, tmp_path):
    root, plugin, _ = _build_rig(tmp_path)
    selection = _selection(core, {"tests/test_slow_0.py"})
    args = core.to_pytest_args(selection, plugin)
    expected = (plugin / "tests" / "test_slow_0.py").resolve()
    assert args == [f"--ignore={expected}"]
    assert Path(args[0].split("=", 1)[1]).is_absolute()


def test_to_pytest_args_empty_selection_is_a_bare_run(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    assert core.to_pytest_args(_selection(core, set()), plugin) == []


def test_to_pytest_args_refuses_a_missing_path(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    with pytest.raises(ValueError):
        core.to_pytest_args(_selection(core, {"tests/test_missing.py"}), plugin)


def test_to_pytest_args_refuses_an_escape(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    with pytest.raises(ValueError):
        core.to_pytest_args(_selection(core, {"../outside.py"}), plugin)


def test_build_argv_run_mode(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    selection = _selection(core, {"tests/test_slow_0.py"})
    ignore = core.to_pytest_args(selection, plugin)
    assert core.build_argv(selection, plugin, "run") == [
        sys.executable, "-m", "pytest", "-ra", *ignore]


def test_build_argv_collect_only_mode(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    selection = _selection(core, {"tests/test_slow_0.py"})
    ignore = core.to_pytest_args(selection, plugin)
    assert core.build_argv(selection, plugin, "collect-only") == [
        sys.executable, "-m", "pytest", "-ra", "--collect-only", "-q",
        "-p", "no:cacheprovider", *ignore]


def test_build_argv_appends_workers_after_the_ignore_args(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    selection = _selection(core, {"tests/test_slow_0.py"})
    ignore = core.to_pytest_args(selection, plugin)
    assert core.build_argv(selection, plugin, "run", workers=2) == [
        sys.executable, "-m", "pytest", "-ra", *ignore,
        "-n", "2", "--dist", "loadfile"]


def test_build_argv_rejects_workers_in_collect_only(core, tmp_path):
    _, plugin, _ = _build_rig(tmp_path)
    with pytest.raises(ValueError):
        core.build_argv(_selection(core, set()), plugin, "collect-only", workers=2)


def test_discover_excludes_a_nested_worktree_on_the_relative_path(core, tmp_path):
    root, _, _ = _build_rig(tmp_path)
    plugin = root / "pact-plugin"
    nested = plugin / ".worktrees" / "feat" / "pact-plugin" / "tests" / "test_nested.py"
    _write(nested)
    discovered = core.discover(root)
    assert "tests/test_slow_0.py" in discovered
    assert not any(".worktrees" in path for path in discovered)
    assert all(not Path(path).is_absolute() for path in discovered)