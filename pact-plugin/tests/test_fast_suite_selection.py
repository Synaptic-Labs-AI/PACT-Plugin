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
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ci_contract

SELECTOR = Path("scripts") / "fast_suite_selection.py"

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


def _manifest(core, exclusion_paths=("tests/test_slow.py",), seams=None,
              machinery=SELECTION_MACHINERY):
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
        selection_machinery=tuple(machinery),
        guard_files=GUARD_FILES,
        exclusions=exclusions,
    )


def _view(core, closure=(), targets=(), literals=(), names=(),
          path_literals=(), scan_roots=(), repo_reach=()):
    return core.ExclusionView(
        closure=frozenset(closure), import_targets=frozenset(targets),
        literals=frozenset(literals), test_names=frozenset(names),
        path_literals=frozenset(path_literals),
        scan_roots=frozenset(scan_roots), repo_reach=tuple(repo_reach))


def _facts(core, manifest, views=None, value_inert=(), verdicts=None,
           conftest_imported=()):
    views = views or {}
    return core.ChangeFacts(
        views={exclusion.path: views.get(
            exclusion.path, _view(core, closure=(exclusion.path,)))
            for exclusion in manifest.exclusions},
        value_inert=frozenset(value_inert),
        conftest_verdicts=dict(verdicts or {}),
        conftest_imported=frozenset(conftest_imported),
    )


def _select(core, changed=(), status="ok", discovered=DISCOVERED,
            facts=None, **kwargs):
    """`facts` is a dict of _facts keyword arguments."""
    manifest = _manifest(core, **kwargs)
    return core.select(discovered, manifest, list(changed), status,
                       _facts(core, manifest, **(facts or {})))


def _assert_select_all(result, discovered=DISCOVERED):
    assert result.select_all_reason is not None
    assert result.included == discovered
    assert result.excluded == frozenset()
    assert result.added_back == ()


def _reasons(result):
    return [(entry.path, entry.reason) for entry in result.added_back]


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

    def test_a_changed_skill_test_adds_nothing_back(self, core):
        result = _select(
            core, changed=["pact-plugin/skills/pact-example/test_skill_loading.py"])
        assert result.select_all_reason is None
        assert "skills/pact-example/test_skill_loading.py" in result.included
        assert result.added_back == ()


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
        # matched_changed is REPO-relative: exactly the path changed_paths
        # returned, prefix present.
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
        assert add_back.matched_seam == "hooks/merge_guard_pre.py"
        assert add_back.matched_changed == "pact-plugin/hooks/merge_guard_pre.py"


@pytest.mark.parametrize(
    "case_id,changed,status,reason",
    [
        ("git-error", (), "git-error", "changed-paths:git-error"),
        ("invalid-base", (), "invalid-base", "changed-paths:invalid-base"),
        ("empty-diff", (), "empty-diff", "changed-paths:empty-diff"),
        ("selection-machinery", ("scripts/run_fast_suite.py",), "ok",
         "selection-machinery"),
        ("constraints-pin", ("constraints-ci.txt",), "ok",
         "selection-machinery"),
        ("pyproject-pin", ("pyproject.toml",), "ok", "selection-machinery"),
        # A changed conftest with no classification cannot be cleared.
        ("conftest-without-verdict", ("pact-plugin/tests/conftest.py",), "ok",
         "conftest:tests/conftest.py:no-verdict"),
    ],
)
def test_select_all_fail_closed(core, case_id, changed, status, reason):
    """Every fail-closed rule selects everything and says why."""
    result = _select(core, changed=changed, status=status)
    assert result.select_all_reason == reason, case_id
    _assert_select_all(result)


def test_a_plugin_change_without_facts_selects_all(core):
    """A caller that forgets the facts must not narrow the run."""
    result = core.select(DISCOVERED, _manifest(core),
                         ["pact-plugin/hooks/anything.py"], "ok", None)
    assert result.select_all_reason == "change-facts-unavailable"
    _assert_select_all(result)


def test_changed_test_wins_over_changed_seam(core):
    """One add-back per entry; changed_test is the first reason."""
    result = _select(
        core,
        changed=[
            "pact-plugin/tests/test_slow.py",
            "pact-plugin/hooks/merge_guard_pre.py",
        ],
        seams={"tests/test_slow.py": ("hooks/merge_guard_pre.py",)},
    )
    assert result.select_all_reason is None
    assert len(result.added_back) == 1, result.added_back
    assert result.added_back[0].reason == "changed_test"


@pytest.mark.parametrize("pin", ["constraints-ci.txt", "pyproject.toml"])
def test_the_dependency_pins_select_all_without_a_machinery_entry(core, pin):
    """The hard-coded pin rule fires on its own, not via the manifest list."""
    machinery = tuple(path for path in SELECTION_MACHINERY
                      if path not in ("constraints-ci.txt", "pyproject.toml"))
    result = _select(core, changed=[pin], machinery=machinery)
    assert result.select_all_reason == "selection-machinery"
    _assert_select_all(result)


class TestUnmappedPython:
    """An unmapped .py (production or test support) adds back by import
    closure, never ALL."""

    @pytest.mark.parametrize("changed", [
        "pact-plugin/hooks/unmapped.py",          # production
        "pact-plugin/tests/support_helper.py",    # test support
        "pact-plugin/tests/fixtures/sample.py",   # fixture, not conftest-imported
    ])
    def test_a_file_in_the_closure_adds_its_exclusion_back(self, core, changed):
        rel = changed.removeprefix("pact-plugin/")
        result = _select(core, changed=[changed], facts={"views": {
            "tests/test_slow.py": _view(core, closure=(
                "tests/test_slow.py", rel))}})
        assert result.select_all_reason is None
        assert _reasons(result) == [("tests/test_slow.py", "closure")]

    def test_a_file_in_no_closure_adds_nothing(self, core):
        result = _select(core, changed=["pact-plugin/hooks/unmapped.py"])
        assert result.select_all_reason is None
        assert result.added_back == ()
        assert result.excluded == frozenset({"tests/test_slow.py"})

    def test_a_deleted_module_named_by_an_import_adds_back(self, core):
        """A deletion is no longer in any closure; its import target is."""
        result = _select(core, changed=["pact-plugin/hooks/gone.py"], facts={
            "views": {"tests/test_slow.py": _view(
                core, closure=("tests/test_slow.py",),
                targets=("hooks/gone.py",))}})
        assert _reasons(result) == [("tests/test_slow.py", "closure")]


class TestDataFiles:
    """A non-.py file adds back only exclusions that can read it."""

    def test_a_literal_naming_the_path_adds_back(self, core):
        result = _select(core, changed=["pact-plugin/commands/x.md"], facts={
            "views": {"tests/test_slow.py": _view(
                core, closure=("tests/test_slow.py",),
                literals=("commands/x.md",))}})
        assert _reasons(result) == [("tests/test_slow.py", "data_reference")]

    def test_a_literal_naming_the_basename_adds_back(self, core):
        result = _select(core, changed=["pact-plugin/hooks/hooks.json"], facts={
            "views": {"tests/test_slow.py": _view(
                core, closure=("tests/test_slow.py",),
                literals=("hooks.json",))}})
        assert _reasons(result) == [("tests/test_slow.py", "data_reference")]

    def test_a_constructed_ancestor_directory_adds_back(self, core):
        result = _select(core, changed=["pact-plugin/commands/x.md"], facts={
            "views": {"tests/test_slow.py": _view(
                core, closure=("tests/test_slow.py",),
                path_literals=("commands",))}})
        assert _reasons(result) == [("tests/test_slow.py", "constructed_path")]

    def test_a_file_nothing_reads_adds_nothing(self, core):
        """The README case: no literal, no ancestor, no listing."""
        result = _select(core, changed=["pact-plugin/README.md"], facts={
            "views": {"tests/test_slow.py": _view(
                core, closure=("tests/test_slow.py",),
                literals=("commands/x.md",), path_literals=("hooks",))}})
        assert result.select_all_reason is None
        assert result.added_back == ()


class TestValueInertKeys:
    """A version-only plugin.json change neither widens nor adds back."""

    PATH = "pact-plugin/.claude-plugin/plugin.json"

    def _views(self, core):
        return {"tests/test_slow.py": _view(
            core, closure=("tests/test_slow.py",), literals=("plugin.json",))}

    def test_a_value_inert_change_is_dropped(self, core):
        result = _select(core, changed=[self.PATH], facts={
            "views": self._views(core), "value_inert": (self.PATH,)})
        assert result.select_all_reason is None
        assert result.added_back == ()

    def test_any_other_change_is_an_ordinary_data_change(self, core):
        result = _select(core, changed=[self.PATH],
                         facts={"views": self._views(core)})
        assert _reasons(result) == [("tests/test_slow.py", "data_reference")]


class TestScannedTrees:
    """A scanner exclusion sees every file under its roots, any type."""

    def _views(self, core, *roots):
        return {"tests/test_slow.py": _view(
            core, closure=("tests/test_slow.py",), scan_roots=roots)}

    @pytest.mark.parametrize("changed", [
        "pact-plugin/hooks/any_hook.py",       # an unmapped .py
        "pact-plugin/hooks/slow_seam.py",      # a mapped seam: seam rule first
        "pact-plugin/hooks/hooks.json",        # data
    ])
    def test_any_file_under_a_root_adds_back(self, core, changed):
        result = _select(core, changed=[changed],
                         facts={"views": self._views(core, "hooks")})
        assert [entry.path for entry in result.added_back] == [
            "tests/test_slow.py"]
        assert result.added_back[0].reason in ("scanned_tree", "changed_seam")

    def test_an_unmapped_python_file_names_the_scanner_rule(self, core):
        result = _select(core, changed=["pact-plugin/hooks/any_hook.py"],
                         facts={"views": self._views(core, "hooks")})
        assert _reasons(result) == [("tests/test_slow.py", "scanned_tree")]

    def test_a_file_outside_every_root_adds_nothing(self, core):
        result = _select(core, changed=["pact-plugin/skills/x/y.py"],
                         facts={"views": self._views(core, "hooks")})
        assert result.added_back == ()

    def test_the_plugin_root_sees_everything(self, core):
        result = _select(core, changed=["pact-plugin/tests/test_fast.py"],
                         facts={"views": self._views(core, ".")})
        assert _reasons(result) == [("tests/test_slow.py", "scanned_tree")]

    def test_a_built_directory_does_not_cover_python_files(self, core):
        """A built directory adds back on data files only; a Python file
        reaches a directory reader only through the scanner rule."""
        result = _select(core, changed=["pact-plugin/hooks/any_hook.py"],
                         facts={"views": {"tests/test_slow.py": _view(
                             core, closure=("tests/test_slow.py",),
                             path_literals=("hooks",))}})
        assert result.added_back == ()


class TestRepoReaders:
    """An exclusion whose closure reads repository files outside the plugin
    comes back on any change outside the plugin; others ignore those."""

    def test_an_outside_change_adds_back_a_repo_reader(self, core):
        result = _select(core, changed=[".github/workflows/tests.yml"],
                         facts={"views": {"tests/test_slow.py": _view(
                             core, closure=("tests/test_slow.py",),
                             repo_reach=("tests/test_slow.py:3:names x",))}})
        assert _reasons(result) == [("tests/test_slow.py", "repo_reader")]
        assert result.added_back[0].matched_changed == (
            ".github/workflows/tests.yml")

    def test_an_outside_change_leaves_a_non_reader_excluded(self, core):
        result = _select(core, changed=[".github/workflows/tests.yml"])
        assert result.select_all_reason is None
        assert result.excluded == frozenset({"tests/test_slow.py"})

    def test_a_plugin_change_does_not_wake_a_repo_reader(self, core):
        result = _select(core, changed=["pact-plugin/hooks/unmapped.py"],
                         facts={"views": {"tests/test_slow.py": _view(
                             core, closure=("tests/test_slow.py",),
                             repo_reach=("tests/test_slow.py:3:names x",))}})
        assert result.added_back == ()

    def test_an_outside_change_without_facts_selects_all(self, core):
        result = core.select(DISCOVERED, _manifest(core), ["CHANGELOG.md"],
                             "ok", None)
        assert result.select_all_reason == "change-facts-unavailable"


class TestConftestVerdicts:
    """select() applies the conftest classification gather_facts recorded."""

    CONFTEST = "pact-plugin/tests/conftest.py"

    def test_a_global_shape_selects_all_and_names_it(self, core):
        result = _select(core, changed=[self.CONFTEST], facts={"verdicts": {
            "tests/conftest.py": core.ConftestVerdict("hook:pytest_configure")}})
        assert result.select_all_reason == (
            "conftest:tests/conftest.py:hook:pytest_configure")
        _assert_select_all(result)

    def test_a_changed_fixture_adds_back_only_its_users(self, core):
        manifest_paths = ("tests/test_slow.py", "tests/test_other.py")
        result = _select(
            core, changed=[self.CONFTEST], exclusion_paths=manifest_paths,
            discovered=DISCOVERED | {"tests/test_other.py"},
            facts={"verdicts": {"tests/conftest.py": core.ConftestVerdict(
                None, frozenset({"seeded_home"}))},
                "views": {"tests/test_slow.py": _view(
                    core, closure=("tests/test_slow.py",),
                    names=("seeded_home",))}})
        assert _reasons(result) == [("tests/test_slow.py", "fixture_name")]

    def test_a_scoped_autouse_adds_back_closures_that_load_its_module(self, core):
        result = _select(core, changed=[self.CONFTEST], facts={
            "verdicts": {"tests/conftest.py": core.ConftestVerdict(
                None, frozenset(), frozenset({"hooks/shared/merge_guard_common.py"}))},
            "views": {"tests/test_slow.py": _view(core, closure=(
                "tests/test_slow.py", "hooks/shared/merge_guard_common.py"))}})
        assert _reasons(result) == [("tests/test_slow.py", "autouse_module")]

    def test_a_conftest_imported_module_is_classified_too(self, core):
        result = _select(
            core, changed=["pact-plugin/tests/fixtures/seed.py"], facts={
                "conftest_imported": ("tests/fixtures/seed.py",),
                "verdicts": {"tests/fixtures/seed.py": core.ConftestVerdict(
                    "module-level-effect")}})
        assert result.select_all_reason == (
            "conftest:tests/fixtures/seed.py:module-level-effect")


class TestEmptySeams:
    """An exclusion whose closure reaches no production module."""

    def test_it_is_added_back_when_its_own_module_changes(self, core):
        result = _select(core, changed=["pact-plugin/tests/test_slow.py"],
                         seams={"tests/test_slow.py": ()})
        assert result.select_all_reason is None
        assert [(entry.path, entry.reason) for entry in result.added_back] == [
            ("tests/test_slow.py", "changed_test")]

    def test_it_stays_excluded_on_a_test_only_change(self, core):
        result = _select(core, changed=["pact-plugin/tests/test_fast.py"],
                         seams={"tests/test_slow.py": ()})
        assert result.excluded == frozenset({"tests/test_slow.py"})


class TestClassifyConftestChange:
    """The pure conftest classifier. Unknown statement kinds select
    everything."""

    BASE = (
        '"""doc."""\n'
        "import pytest\n"
        "import shared.merge_guard_common as mgc\n"
        "SEED = 1\n"
        "@pytest.fixture\n"
        "def seeded_home(tmp_path):\n    return tmp_path\n"
        "@pytest.fixture(autouse=True)\n"
        "def _scrub(monkeypatch):\n    monkeypatch.delenv('X', raising=False)\n"
        "@pytest.fixture(autouse=True)\n"
        "def _guard(monkeypatch):\n"
        "    monkeypatch.setattr(mgc, 'TOKEN_TTL', 1)\n"
        "def pytest_configure(config):\n    pass\n"
    )

    @staticmethod
    def _resolve(dotted):
        return frozenset({"hooks/shared/merge_guard_common.py"}) if (
            dotted == "shared.merge_guard_common") else frozenset()

    def _classify(self, core, new, old=None, is_conftest=True):
        return core.classify_conftest_change(
            self.BASE if old is None else old, new, is_conftest,
            resolve=self._resolve)

    def test_an_unchanged_file_changes_nothing(self, core):
        verdict = self._classify(core, self.BASE)
        assert verdict == core.ConftestVerdict(None)

    def test_a_docstring_only_change_changes_nothing(self, core):
        verdict = self._classify(core, self.BASE.replace("doc.", "docs."))
        assert verdict == core.ConftestVerdict(None)

    @pytest.mark.parametrize("edit,reason", [
        (lambda s: s.replace("    pass\n", "    config.x = 1\n"),
         "hook:pytest_configure"),
        (lambda s: s + "import os\nos.environ['HOME'] = '/x'\n",
         "module-level-effect"),
        (lambda s: s + "if True:\n    SEED = 2\n", "module-level-effect"),
        (lambda s: s.replace("'X'", "'Y'"), "autouse-unscoped:_scrub"),
        (lambda s: s + "pytest_plugins = ['x']\n", "hook:pytest_plugins"),
    ])
    def test_a_global_shape_selects_all(self, core, edit, reason):
        assert self._classify(core, edit(self.BASE)).select_all == reason

    @pytest.mark.parametrize("literal", [
        ".claude", "~/.claude/teams", "a-b.json", "merge-authorized-*.consumed",
        "..", "x..y",
    ])
    def test_a_non_module_string_does_not_scope_an_autouse_fixture(
            self, core, literal):
        """A string that is not an identifier-dotted name names no module,
        even when the resolver would answer for the empty name."""
        source = (
            "import pytest\n"
            "@pytest.fixture(autouse=True)\n"
            "def _isolate(monkeypatch, tmp_path):\n"
            f"    monkeypatch.setenv('HOME', str(tmp_path / {literal!r}))\n")
        verdict = core.classify_conftest_change(
            "import pytest\n", source, True,
            resolve=lambda dotted: frozenset({"hooks/__init__.py"})
            if not dotted or not all(
                part.isidentifier() for part in dotted.split("."))
            else frozenset())
        assert verdict.select_all == "autouse-unscoped:_isolate"

    @pytest.mark.parametrize("filename", [
        "config.json", "working_memory.md", "memory.db", "search.py",
        "constants.txt", "config",
    ])
    def test_a_file_name_whose_stem_is_a_module_does_not_scope(
            self, core, filename):
        """str(tmp_path / "config.json") names a file, not the config module."""
        source = (
            "import pytest\n"
            "@pytest.fixture(autouse=True)\n"
            "def _home(monkeypatch, tmp_path):\n"
            f"    monkeypatch.setenv('HOME', str(tmp_path / {filename!r}))\n")
        stem = filename.split(".")[0]
        verdict = core.classify_conftest_change(
            "import pytest\n", source, True,
            resolve=lambda dotted: frozenset({f"skills/x/scripts/{stem}.py"})
            if dotted == stem else frozenset())
        assert verdict.select_all == "autouse-unscoped:_home"

    @pytest.mark.parametrize("call", [
        "monkeypatch.setattr('hooks.shared.pact_context.X', 1)",
        "monkeypatch.delattr('hooks.shared.pact_context.X')",
        "mock.patch('hooks.shared.pact_context.X')",
    ])
    def test_a_patch_target_names_its_module(self, core, call):
        source = (
            "import pytest\nfrom unittest import mock\n"
            "@pytest.fixture(autouse=True)\n"
            f"def _patched(monkeypatch):\n    {call}\n")
        verdict = core.classify_conftest_change(
            "import pytest\nfrom unittest import mock\n", source, True,
            resolve=lambda dotted: frozenset({"hooks/shared/pact_context.py"})
            if dotted == "hooks.shared.pact_context" else frozenset())
        assert verdict.select_all is None
        assert verdict.modules == frozenset({"hooks/shared/pact_context.py"})

    def test_a_dotted_monkeypatch_target_still_scopes(self, core):
        verdict = self._classify(core, self.BASE.replace(
            "monkeypatch.setattr(mgc, 'TOKEN_TTL', 1)",
            "monkeypatch.setattr('shared.merge_guard_common.TOKEN_TTL', 2)"))
        assert verdict.modules == frozenset(
            {"hooks/shared/merge_guard_common.py"})

    def test_production_files_refuses_an_invalid_name(self, core, tmp_path):
        plugin = tmp_path / "pact-plugin"
        _write(plugin / "hooks" / "__init__.py", "")
        _write(plugin / "hooks" / "shared" / "__init__.py", "")
        index = core.ScanIndex(plugin)
        for name in ("", ".", "a..b", "hooks-x", " shared"):
            assert core._production_files(name, index) == frozenset(), name
        assert core._production_files("shared", index) == frozenset(
            {"hooks/shared/__init__.py"})

    def test_a_conftest_added_or_removed_selects_all(self, core):
        assert core.classify_conftest_change(
            None, self.BASE, True).select_all == "conftest-added-or-removed"
        assert core.classify_conftest_change(
            self.BASE, None, True).select_all == "conftest-added-or-removed"

    def test_a_changed_fixture_reports_its_name(self, core):
        verdict = self._classify(
            core, self.BASE.replace("return tmp_path", "return tmp_path / 'h'"))
        assert verdict.select_all is None
        assert verdict.names == frozenset({"seeded_home"})

    def test_a_scoped_autouse_reports_its_module(self, core):
        verdict = self._classify(core, self.BASE.replace("'TOKEN_TTL', 1",
                                                         "'TOKEN_TTL', 2"))
        assert verdict.select_all is None
        assert verdict.modules == frozenset(
            {"hooks/shared/merge_guard_common.py"})

    def test_a_removed_fixture_module_reports_every_name(self, core):
        """A conftest-imported module being deleted is not a conftest
        removal: its names still identify the exclusions that used them."""
        verdict = core.classify_conftest_change(
            "def helper():\n    pass\n", None, False)
        assert verdict.select_all is None
        assert verdict.names == frozenset({"helper"})


class TestJsonValueInert:
    """The version-bump comparison: parsed JSON, the ignored keys removed."""

    @pytest.mark.parametrize("old,new,expected", [
        ('{"name": "p", "version": "1.0.0"}', '{"name": "p", "version": "1.0.1"}', True),
        ('{"name": "p", "version": "1.0.0"}', '{\n  "version": "1.0.1",\n  "name": "p"\n}', True),
        ('{"name": "p", "version": "1.0.0"}', '{"name": "q", "version": "1.0.1"}', False),
        ('{"name": "p"}', '{"name": "p", "extra": 1}', False),
        ('{"name": "p"}', "not json", False),
        (None, '{"name": "p"}', False),
        ('[1]', '[1]', False),
    ])
    def test_the_comparison(self, core, old, new, expected):
        assert core.json_change_is_value_inert(old, new, ("version",)) is expected


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
        "selection_machinery": list(SELECTION_MACHINERY),
        "value_inert_keys": {},
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


def test_value_inert_keys_and_empty_seams_load(core, tmp_path):
    root, _, document = _build_rig(tmp_path)
    document["value_inert_keys"] = {
        ".claude-plugin/marketplace.json": ["version"]}
    document["exclusions"][0]["production_seams"] = []
    manifest = core.load_manifest(_write_manifest(root, document))
    assert manifest.value_inert_keys == (
        (".claude-plugin/marketplace.json", ("version",)),)
    assert manifest.exclusions[0].production_seams == ()


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
            # Rule-unique fragment: the nonexistence error also names this
            # path, so the path alone would not prove the glob rule fired.
            "contains a glob character",
        ),
        (
            "rootdir-relative",
            lambda doc, root: _mutate_exclusion(
                doc, 0, path="pact-plugin/tests/test_slow_0.py"),
            # The wrong-base rejection is the existence message's diagnostic;
            # assert the part that names the repo-relative-spelling cause.
            "repo-relative spellings",
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
            # Rule-unique fragment: without this branch the `.py` rule would
            # also name `/etc/passwd`.
            "must be a relative path with no '..' segment and no backslash",
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
            # Rule-unique fragment: the nonexistence error also names this
            # path, so the path alone would not prove the .py rule fired.
            "must end in .py",
        ),
        (
            "seam-under-tests",
            lambda doc, root: (
                _write(root / "pact-plugin" / "tests" / "support" / "census.py"),
                doc["exclusions"][0].update(
                    {"production_seams": ["tests/support/census.py"]}),
            ),
            "is under tests/",
        ),
        (
            "value-inert-key-missing",
            lambda doc, root: doc.pop("value_inert_keys"),
            "missing required key 'value_inert_keys'",
        ),
        (
            "value-inert-not-an-object",
            lambda doc, root: doc.update({"value_inert_keys": ["x.json"]}),
            "value_inert_keys: expected an object",
        ),
        (
            "value-inert-not-json-file",
            lambda doc, root: doc.update(
                {"value_inert_keys": {"pact-plugin/hooks/seam_0.py": ["v"]}}),
            "must be a .json file",
        ),
        (
            "value-inert-missing-on-disk",
            lambda doc, root: doc.update(
                {"value_inert_keys": {"NOT-THERE.json": ["version"]}}),
            "is not a readable JSON file",
        ),
        (
            "value-inert-is-machinery",
            lambda doc, root: doc.update({"value_inert_keys": {
                "scripts/fast_suite_exclusions.json": ["version"]}}),
            "is selection machinery",
        ),
        (
            "value-inert-empty-keys",
            lambda doc, root: doc.update({"value_inert_keys": {
                ".claude-plugin/marketplace.json": []}}),
            "expected a non-empty list of key names",
        ),
        (
            "seam-is-directory",
            lambda doc, root: (
                # End in .py so the `.py` rule does NOT fire first: the
                # directory check must be the rule that rejects it.
                (root / "pact-plugin" / "hooks" / "seam_dir.py").mkdir(
                    parents=True),
                doc["exclusions"][0].update(
                    {"production_seams": ["hooks/seam_dir.py"]}),
            ),
            "hooks/seam_dir.py",
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
    root, plugin, _ = _build_rig(tmp_path)
    # The outside file EXISTS, so the existence check cannot be what raises;
    # the escape check must be. Assert its message, not merely ValueError.
    outside = root / "outside.py"
    outside.write_text(
        "def test_outside():\n    assert True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the pytest working directory"):
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


def _git(repo, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _git_repo(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "one")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "two")
    return repo


class TestChangedPathsStatuses:
    """The four statuses, including the all-zeros regression.

    The fail-closed chain depends on these labels: a git failure mislabelled
    `ok` would fire no add-backs and narrow the fast lane silently.
    """

    def test_all_zero_base_is_invalid_base(self, core, tmp_path):
        repo = _git_repo(tmp_path)
        paths, status = core.changed_paths("0" * 40, repo)
        assert status == "invalid-base"
        assert paths == []

    def test_missing_and_empty_base_are_invalid_base(self, core, tmp_path):
        repo = _git_repo(tmp_path)
        for base in (None, ""):
            paths, status = core.changed_paths(base, repo)
            assert status == "invalid-base", (base, status)
            assert paths == []

    def test_unknown_sha_is_git_error(self, core, tmp_path):
        repo = _git_repo(tmp_path)
        paths, status = core.changed_paths("f" * 40, repo)
        assert status == "git-error"
        assert paths == []

    def test_head_against_itself_is_empty_diff(self, core, tmp_path):
        repo = _git_repo(tmp_path)
        # A full 40-hex SHA: "HEAD" is not a valid base and maps to
        # invalid-base, which the row above covers.
        head = _git(repo, "rev-parse", "HEAD")
        paths, status = core.changed_paths(head, repo)
        assert status == "empty-diff"
        assert paths == []

    def test_parent_against_head_is_ok_with_paths(self, core, tmp_path):
        repo = _git_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")
        paths, status = core.changed_paths(parent, repo)
        assert status == "ok"
        assert paths == ["b.txt"]

    def test_a_rename_reports_both_paths(self, core, tmp_path):
        """--no-renames: the deleted old path must reach the selector too."""
        repo = _git_repo(tmp_path)
        base = _git(repo, "rev-parse", "HEAD")
        _git(repo, "mv", "a.txt", "renamed.txt")
        _git(repo, "commit", "-q", "-m", "rename")
        paths, status = core.changed_paths(base, repo)
        assert status == "ok"
        assert sorted(paths) == ["a.txt", "renamed.txt"]

    def test_a_non_ascii_path_comes_back_verbatim(self, core, tmp_path):
        """Without -z and quotePath=false git C-quotes it and it is dropped."""
        repo = _git_repo(tmp_path)
        base = _git(repo, "rev-parse", "HEAD")
        target = repo / "pact-plugin" / "hooks" / "caf\u00e9.py"
        target.parent.mkdir(parents=True)
        target.write_text("", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "non-ascii")
        paths, status = core.changed_paths(base, repo)
        assert status == "ok"
        assert paths == ["pact-plugin/hooks/caf\u00e9.py"]

    def test_the_diff_is_against_the_merge_base(self, core, tmp_path):
        """Three-dot: a commit on the base branch after the fork is not ours."""
        repo = _git_repo(tmp_path)
        fork = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "feature.txt").write_text("f", encoding="utf-8")
        _git(repo, "add", "feature.txt")
        _git(repo, "commit", "-q", "-m", "feature")
        _git(repo, "checkout", "-q", "--detach", fork)
        (repo / "base_only.txt").write_text("m", encoding="utf-8")
        _git(repo, "add", "base_only.txt")
        _git(repo, "commit", "-q", "-m", "base moved")
        base_tip = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "feature")
        paths, status = core.changed_paths(base_tip, repo)
        assert status == "ok"
        # A two-dot diff would also report base_only.txt (as a deletion).
        assert paths == ["feature.txt"]


def test_discover_excludes_a_nested_worktree_on_the_relative_path(core, tmp_path):
    """The rig itself lives under a `.worktrees` directory, as CI never does
    but every local worktree checkout does: a check on the ABSOLUTE path
    would discover nothing here."""
    root, _, _ = _build_rig(tmp_path / ".worktrees" / "feat")
    assert ".worktrees" in root.parts
    plugin = root / "pact-plugin"
    nested = plugin / ".worktrees" / "feat" / "pact-plugin" / "tests" / "test_nested.py"
    _write(nested)
    discovered = core.discover(root)
    assert discovered, "discovery found nothing under a .worktrees checkout"
    assert "tests/test_slow_0.py" in discovered
    assert not any(".worktrees" in path for path in discovered)
    assert all(not Path(path).is_absolute() for path in discovered)


def test_a_no_tests_ran_summary_parses_as_zero_collected(core):
    parsed = core.parse_pytest_summary("no tests ran in 0.01s")
    assert parsed is not None
    assert parsed["collected"] == 0
    assert parsed["summary_line"] == "no tests ran in 0.01s"


class TestImportClosure:
    """The static closure the seam-completeness contract is built on."""

    def _rig(self, tmp_path):
        plugin = tmp_path / "pact-plugin"
        _write(plugin / "hooks" / "shared" / "__init__.py",
               "from .eager import thing\n")
        _write(plugin / "hooks" / "shared" / "eager.py", "thing = 1\n")
        _write(plugin / "hooks" / "shared" / "direct.py", "value = 1\n")
        _write(plugin / "hooks" / "shared" / "unrelated.py", "")
        _write(plugin / "hooks" / "runme.py", "import shared.direct\n")
        _write(plugin / "tests" / "helper.py", "")
        _write(plugin / "conftest.py", "import shared.unrelated\n")
        return plugin

    def test_a_package_import_follows_its_eager_init(self, core, tmp_path):
        plugin = self._rig(tmp_path)
        _write(plugin / "tests" / "test_a.py",
               "from shared.direct import value\n")
        closure = core.import_closure(plugin, ["tests/test_a.py"])
        assert {"hooks/shared/__init__.py", "hooks/shared/eager.py",
                "hooks/shared/direct.py"} <= closure
        assert "hooks/shared/unrelated.py" not in closure

    def test_a_hook_named_as_a_string_is_followed(self, core, tmp_path):
        plugin = self._rig(tmp_path)
        _write(plugin / "tests" / "test_b.py",
               'HOOK = "runme.py"\nOTHER = "conftest.py"\n'
               'FIXTURE = "tests/helper.py"\n')
        closure = core.import_closure(plugin, ["tests/test_b.py"])
        assert {"hooks/runme.py", "hooks/shared/direct.py",
                "hooks/shared/eager.py"} <= closure
        # A fixture file name is not a dependency edge.
        assert "conftest.py" not in closure
        assert "hooks/shared/unrelated.py" not in closure
        assert "tests/helper.py" not in closure


class TestScanIndex:
    """What one module contributes to its exclusions' views."""

    def _scan(self, core, tmp_path, rel, source):
        plugin = tmp_path / "pact-plugin"
        _write(plugin / rel, source)
        (plugin / "hooks").mkdir(parents=True, exist_ok=True)
        return core.ScanIndex(plugin).scan(rel)

    def test_docstrings_and_prose_are_not_literals(self, core, tmp_path):
        scan = self._scan(core, tmp_path, "tests/test_a.py",
                          '"""agents/only_in_a_docstring.md"""\n'
                          'MESSAGE = "see commands/x.md for details"\n'
                          'TARGET = "commands/x.md"\n')
        assert scan.literals == frozenset({"commands/x.md"})

    def test_only_path_positions_are_path_literals(self, core, tmp_path):
        scan = self._scan(core, tmp_path, "hooks/m.py",
                          'from pathlib import Path\n'
                          'KINDS = {"commands": 1}\n'
                          'def f(root):\n'
                          '    return (Path(root) / "agents").glob("*.md")\n')
        assert "agents" in scan.path_literals
        assert "commands" not in scan.path_literals

    def test_a_file_anchored_scan_is_a_scanner_of_its_directory(
            self, core, tmp_path):
        scan = self._scan(core, tmp_path, "tests/test_b.py",
                          'from pathlib import Path\n'
                          'HERE = Path(__file__).resolve().parent\n'
                          'def test_x():\n'
                          '    return sorted(HERE.rglob("*.md"))\n')
        assert scan.scanner_roots == frozenset({"tests"})

    def test_a_parents_anchor_scans_from_that_depth(self, core, tmp_path):
        scan = self._scan(core, tmp_path, "tests/test_c.py",
                          'from pathlib import Path\n'
                          'ROOT = Path(__file__).resolve().parents[1]\n'
                          'def test_x():\n'
                          '    return sorted(ROOT.rglob("*.py"))\n')
        assert scan.scanner_roots == frozenset({"."})

    @pytest.mark.parametrize("anchor,expected", [
        # One resolver, every spelling. From tests/, one level up is the
        # module's directory and two levels up is the plugin root.
        ("Path(__file__).parent", "tests"),
        ("Path(__file__).resolve().parents[0]", "tests"),
        ("os.path.dirname(__file__)", "tests"),
        ("Path(__file__).parent.parent", "."),
        ("Path(__file__).resolve().parents[1]", "."),
        ("os.path.dirname(os.path.dirname(__file__))", "."),
        # Depths that cannot be resolved, or that climb above the plugin,
        # are the plugin root, never the module's own directory.
        ("Path(__file__).parents[DEPTH]", "."),
        ("Path(__file__).parents[4]", "."),
        ("locate(__file__)", "."),
        ("Path(other_module.__file__).parent", "."),
        # A literal joined after the anchor narrows it.
        ("Path(__file__).parent / 'fixtures'", "tests/fixtures"),
        ("Path(__file__).parent.joinpath('fixtures', 'corpus')",
         "tests/fixtures/corpus"),
    ])
    def test_every_anchor_spelling_resolves_the_same_way(
            self, core, tmp_path, anchor, expected):
        scan = self._scan(core, tmp_path, "tests/test_depth.py",
                          'import os\nfrom pathlib import Path\n'
                          'import other_module\nDEPTH = 1\n'
                          'def locate(path):\n    return Path(path)\n'
                          f'ROOT = {anchor}\n'
                          'def test_x():\n'
                          '    return sorted(Path(ROOT).rglob("*"))\n')
        assert scan.scanner_roots == frozenset({expected})

    @pytest.mark.parametrize("anchor", [
        "Path(__file__).parents[DEPTH]",        # a variable depth
        "Path(__file__).parents[6]",            # above the plugin
        "locate(__file__)",                     # a helper call
    ])
    def test_an_unresolved_anchor_is_not_narrowed_by_literals(
            self, core, tmp_path, anchor):
        """With no resolvable start, a 'hooks' literal in the scope cannot be
        placed under it, so the root is the whole plugin."""
        (tmp_path / "pact-plugin" / "hooks").mkdir(parents=True)
        scan = self._scan(core, tmp_path, "tests/test_unresolved.py",
                          'from pathlib import Path\nDEPTH = 1\n'
                          'def locate(path):\n    return Path(path)\n'
                          f'BASE = {anchor}\n'
                          'def test_x():\n'
                          '    for top in ("hooks",):\n'
                          '        list((BASE / top).rglob("*.py"))\n')
        assert scan.scanner_roots == frozenset({"."})

    def test_a_resolved_anchor_is_still_narrowed_by_literals(
            self, core, tmp_path):
        """Control: from a resolved plugin-root anchor, 'hooks' narrows."""
        (tmp_path / "pact-plugin" / "hooks").mkdir(parents=True)
        scan = self._scan(core, tmp_path, "tests/test_resolved.py",
                          'from pathlib import Path\n'
                          'BASE = Path(__file__).parents[1]\n'
                          'def test_x():\n'
                          '    for top in ("hooks",):\n'
                          '        list((BASE / top).rglob("*.py"))\n')
        assert scan.scanner_roots == frozenset({"hooks"})

    def test_an_anchored_scope_that_scans_no_checkout_path_is_no_scanner(
            self, core, tmp_path):
        """ast.walk and a tmp_path listing next to a __file__ read."""
        scan = self._scan(core, tmp_path, "tests/test_reads_itself.py",
                          'import ast\nfrom pathlib import Path\n'
                          'def test_x(tmp_path):\n'
                          '    tree = ast.parse(Path(__file__).read_text())\n'
                          '    list(ast.walk(tree))\n'
                          '    list(tmp_path.iterdir())\n')
        assert scan.scanner_roots is None

    def test_scope_literals_naming_directories_are_the_roots(
            self, core, tmp_path):
        """Only the scanning function's literals and the constants it uses;
        a directory word elsewhere in the module is not a root."""
        (tmp_path / "pact-plugin" / "skills").mkdir(parents=True)
        (tmp_path / "pact-plugin" / "agents").mkdir(parents=True)
        (tmp_path / "pact-plugin" / "__pycache__").mkdir(parents=True)
        scan = self._scan(core, tmp_path, "tests/test_d.py",
                          'from pathlib import Path\n'
                          'PLUGIN = Path(__file__).parents[1]\n'
                          'TOPS = ("hooks", "skills", "__pycache__")\n'
                          'OTHER = "agents"\n'
                          'def test_x():\n'
                          '    for top in TOPS:\n'
                          '        list((PLUGIN / top).rglob("*.py"))\n')
        assert scan.scanner_roots == frozenset({"hooks", "skills"})

    def test_a_tmp_path_scan_is_not_a_scanner(self, core, tmp_path):
        scan = self._scan(core, tmp_path, "tests/test_e.py",
                          'def test_x(tmp_path):\n'
                          '    list((tmp_path / "hooks").rglob("*"))\n')
        assert scan.scanner_roots is None

    def test_a_seeded_scanner_exclusion_is_added_back(self, core, tmp_path):
        """End to end: detection, view and select() on a rig."""
        plugin = tmp_path / "pact-plugin"
        _write(plugin / "tests" / "docs" / "test_reads_docs.py",
               'from pathlib import Path\n'
               'def test_x():\n'
               '    assert list(Path(__file__).parent.glob("*.md")) is not None\n')
        _write(plugin / "tests" / "docs" / "x.md", "notes\n")
        manifest = _manifest(core, exclusion_paths=(
            "tests/docs/test_reads_docs.py",), seams={
            "tests/docs/test_reads_docs.py": ()})
        index = core.ScanIndex(plugin)
        facts = core.ChangeFacts(
            views={"tests/docs/test_reads_docs.py": index.view(
                "tests/docs/test_reads_docs.py")},
            value_inert=frozenset(), conftest_verdicts={},
            conftest_imported=frozenset())
        discovered = frozenset({"tests/docs/test_reads_docs.py"})
        hit = core.select(discovered, manifest,
                          ["pact-plugin/tests/docs/x.md"], "ok", facts)
        miss = core.select(discovered, manifest,
                           ["pact-plugin/hooks/x.md"], "ok", facts)
        assert _reasons(hit) == [
            ("tests/docs/test_reads_docs.py", "scanned_tree")]
        assert miss.added_back == ()

    @pytest.mark.parametrize("source,why", [
        ('from pathlib import Path\n'
         'def test_x():\n'
         '    (Path(__file__).parents[2] / ".github" / "x.yml").read_text()\n',
         "climbs above the plugin"),
        ('from pathlib import Path\n'
         'def test_x():\n'
         '    (Path(__file__).parent / ".." / ".." / ".github").exists()\n',
         "climbs above the plugin"),
        ('from pathlib import Path\nDEPTH = 2\n'
         'def test_x():\n'
         '    Path(__file__).parents[DEPTH].exists()\n',
         "unresolved climb"),
        ('def test_x():\n    assert "../.github/x.yml"\n', "names ../.github/x.yml"),
        ('def test_x():\n    assert "CHANGELOG.md"\n', "names CHANGELOG.md"),
    ])
    def test_a_read_outside_the_plugin_is_detected(
            self, core, tmp_path, source, why):
        repo = tmp_path / "repo"
        _write(repo / ".github" / "x.yml", "on: push\n")
        _write(repo / "CHANGELOG.md", "# changes\n")
        plugin = repo / "pact-plugin"
        _write(plugin / "tests" / "test_reader.py", source)
        scan = core.ScanIndex(plugin).scan("tests/test_reader.py")
        assert any(why in entry for entry in scan.repo_reach), scan.repo_reach

    @pytest.mark.parametrize("walk", [
        # The repository's own root-finder shape (test_local_lint_parity).
        "    for base in Path(__file__).resolve().parents:\n"
        "        if (base / '.claude-plugin' / 'marketplace.json').exists():\n"
        "            return base / '.github' / 'workflows' / 'tests.yml'\n",
        "    return [p for p in list(Path(__file__).parents)]\n",
        "    return Path('/x') in Path(__file__).resolve().parents\n",
        "    return sorted(Path(__file__).parents)\n",
        "    return next(iter(HERE.parents))\n",
    ])
    def test_a_walk_over_every_ancestor_is_a_repo_read(
            self, core, tmp_path, walk):
        plugin = tmp_path / "repo" / "pact-plugin"
        _write(plugin / "tests" / "test_walker.py",
               "from pathlib import Path\nHERE = Path(__file__)\n"
               "def find():\n" + walk)
        scan = core.ScanIndex(plugin).scan("tests/test_walker.py")
        assert any("walks every ancestor" in entry
                   for entry in scan.repo_reach), scan.repo_reach

    @pytest.mark.parametrize("call", [
        "Path.cwd() / '.github' / 'workflows'",
        "os.path.join(os.getcwd(), 'CHANGELOG.md')",
        "pathlib.Path.cwd().parent",
        "getcwd()",
    ])
    def test_a_cwd_relative_read_is_a_repo_read(self, core, tmp_path, call):
        plugin = tmp_path / "repo" / "pact-plugin"
        _write(plugin / "tests" / "test_cwd.py",
               "import os\nimport pathlib\nfrom os import getcwd\n"
               "from pathlib import Path\n"
               f"def test_x():\n    return {call}\n")
        scan = core.ScanIndex(plugin).scan("tests/test_cwd.py")
        assert any("relative to the cwd" in entry
                   for entry in scan.repo_reach), scan.repo_reach

    @pytest.mark.parametrize("source,rel", [
        # A string naming the call (fixture text for a matcher) is not one.
        ('CMD = "cd $(pwd) && python -c \'import os; os.getcwd()\'"\n'
         'PROBE = "Path.cwd()"\n', "tests/test_cwd.py"),
        # Shipped code resolves the consumer's cwd, not this repository.
        ("import os\ndef f():\n    return os.getcwd()\n", "hooks/cwd_user.py"),
    ])
    def test_a_cwd_mention_or_shipped_cwd_is_not_a_repo_read(
            self, core, tmp_path, source, rel):
        plugin = tmp_path / "repo" / "pact-plugin"
        _write(plugin / rel, source)
        assert core.ScanIndex(plugin).scan(rel).repo_reach == ()

    def test_an_indexed_parents_is_not_a_walk(self, core, tmp_path):
        plugin = tmp_path / "repo" / "pact-plugin"
        _write(plugin / "tests" / "test_walker.py",
               "from pathlib import Path\n"
               "def find():\n    return Path(__file__).parents[1]\n")
        assert core.ScanIndex(plugin).scan(
            "tests/test_walker.py").repo_reach == ()

    @pytest.mark.parametrize("source", [
        # Inside the plugin: the module's directory and the plugin root.
        'from pathlib import Path\n'
        'def test_x():\n    Path(__file__).parents[1].exists()\n',
        # A home-relative name, not a repository path.
        'def test_x():\n    assert "~/.claude/teams"\n',
        # A plugin path that also exists at the repository root.
        'def test_x():\n    assert "hooks/x.py"\n',
    ])
    def test_a_read_inside_the_plugin_is_not_a_repo_read(
            self, core, tmp_path, source):
        repo = tmp_path / "repo"
        _write(repo / "hooks" / "x.py", "")
        plugin = repo / "pact-plugin"
        _write(plugin / "hooks" / "x.py", "")
        _write(plugin / "tests" / "test_reader.py", source)
        assert core.ScanIndex(plugin).scan(
            "tests/test_reader.py").repo_reach == ()

    def test_shipped_code_literals_are_not_repo_reads(self, core, tmp_path):
        """'.github/x.yml' in a hook names the consumer's project."""
        repo = tmp_path / "repo"
        _write(repo / ".github" / "x.yml", "on: push\n")
        plugin = repo / "pact-plugin"
        _write(plugin / "hooks" / "reader.py", 'PATH = ".github/x.yml"\n')
        assert core.ScanIndex(plugin).scan("hooks/reader.py").repo_reach == ()

    def test_another_modules_file_is_placed_through_its_import(
            self, core, tmp_path):
        """Path(mod.__file__).parent.parent is the plugin root, not unknown."""
        plugin = tmp_path / "repo" / "pact-plugin"
        _write(plugin / "hooks" / "mod.py", "")
        _write(plugin / "tests" / "test_reader.py",
               'import mod\nfrom pathlib import Path\n'
               'def test_x():\n'
               '    Path(mod.__file__).resolve().parent.parent.exists()\n')
        scan = core.ScanIndex(plugin).scan("tests/test_reader.py")
        assert scan.repo_reach == ()

    def test_a_seeded_repo_reader_is_added_back_by_a_github_change(
            self, core, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".github" / "x.yml", "on: push\n")
        plugin = repo / "pact-plugin"
        _write(plugin / "tests" / "test_reader.py",
               'from pathlib import Path\n'
               'def test_x():\n'
               '    assert (Path(__file__).parents[2] / ".github" / "x.yml"'
               ').is_file()\n')
        _write(plugin / "tests" / "test_other.py", "def test_ok():\n    pass\n")
        paths = ("tests/test_reader.py", "tests/test_other.py")
        manifest = _manifest(core, exclusion_paths=paths,
                             seams={path: () for path in paths})
        index = core.ScanIndex(plugin)
        facts = core.ChangeFacts(
            views={path: index.view(path) for path in paths},
            value_inert=frozenset(), conftest_verdicts={},
            conftest_imported=frozenset())
        result = core.select(frozenset(paths), manifest,
                             [".github/x.yml"], "ok", facts)
        assert _reasons(result) == [("tests/test_reader.py", "repo_reader")]
        assert result.excluded == frozenset({"tests/test_other.py"})

    def test_computed_imports_and_fixtures_are_recorded(self, core, tmp_path):
        scan = self._scan(core, tmp_path, "tests/test_g.py",
                          'import importlib\n'
                          'NAMES = (("a", "b"),)\n'
                          'def test_x(request, name):\n'
                          '    for left, right in NAMES:\n'
                          '        __import__(right)\n'
                          '    importlib.import_module(name)\n'
                          '    request.getfixturevalue(name)\n'
                          '    request.getfixturevalue("tmp_path")\n')
        assert scan.dynamic_imports == ("test_g.py:6:import_module",)
        assert scan.dynamic_fixtures == ("test_g.py:7",)


def _git_rig(tmp_path):
    repo = tmp_path / "factsrig"
    plugin = repo / "pact-plugin"
    _write(plugin / ".claude-plugin" / "plugin.json",
           '{"name": "pact", "version": "1.0.0"}\n')
    _write(plugin / "tests" / "conftest.py",
           "import pytest\n\n@pytest.fixture\ndef seeded(tmp_path):\n"
           "    return tmp_path\n")
    _write(plugin / "tests" / "test_slow.py",
           "def test_ok(seeded):\n    assert seeded\n")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, plugin, _git(repo, "rev-parse", "HEAD")


def _facts_manifest(core):
    return core.Manifest(
        path_base="plugin_root", selection_machinery=SELECTION_MACHINERY,
        guard_files=GUARD_FILES,
        exclusions=(core.Exclusion("tests/test_slow.py", 1.0, 1, "rig", ()),),
        value_inert_keys=(("pact-plugin/.claude-plugin/plugin.json",
                           ("version",)),))


class TestGatherFacts:
    """The content-dependent inputs come from the merge base and HEAD."""

    PLUGIN_JSON = "pact-plugin/.claude-plugin/plugin.json"

    def _commit(self, repo, rel, text):
        (repo / rel).write_text(text, encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "change")

    def test_a_version_bump_is_value_inert(self, core, tmp_path):
        repo, _, base = _git_rig(tmp_path)
        self._commit(repo, self.PLUGIN_JSON,
                     '{\n  "name": "pact",\n  "version": "1.0.1"\n}\n')
        facts = core.gather_facts(repo, _facts_manifest(core),
                                  [self.PLUGIN_JSON], base)
        assert facts.value_inert == frozenset({self.PLUGIN_JSON})

    def test_a_bump_with_another_key_is_not(self, core, tmp_path):
        repo, _, base = _git_rig(tmp_path)
        self._commit(repo, self.PLUGIN_JSON,
                     '{"name": "other", "version": "1.0.1"}\n')
        facts = core.gather_facts(repo, _facts_manifest(core),
                                  [self.PLUGIN_JSON], base)
        assert facts.value_inert == frozenset()

    def test_without_a_base_nothing_is_value_inert(self, core, tmp_path):
        repo, _, _ = _git_rig(tmp_path)
        self._commit(repo, self.PLUGIN_JSON,
                     '{"name": "pact", "version": "1.0.1"}\n')
        facts = core.gather_facts(repo, _facts_manifest(core),
                                  [self.PLUGIN_JSON], None)
        assert facts.value_inert == frozenset()

    def test_a_changed_fixture_adds_back_its_user(self, core, tmp_path):
        repo, _, base = _git_rig(tmp_path)
        self._commit(repo, "pact-plugin/tests/conftest.py",
                     "import pytest\n\n@pytest.fixture\ndef seeded(tmp_path):\n"
                     "    return tmp_path / 'x'\n")
        changed = ["pact-plugin/tests/conftest.py"]
        manifest = _facts_manifest(core)
        facts = core.gather_facts(repo, manifest, changed, base)
        assert facts.conftest_verdicts["tests/conftest.py"].names == frozenset(
            {"seeded"})
        discovered = core.discover(repo)
        result = core.select(discovered, manifest, changed, "ok", facts)
        assert _reasons(result) == [("tests/test_slow.py", "fixture_name")]

    def test_a_changed_hook_selects_all(self, core, tmp_path):
        repo, _, base = _git_rig(tmp_path)
        path = repo / "pact-plugin" / "tests" / "conftest.py"
        path.write_text(path.read_text(encoding="utf-8")
                        + "\ndef pytest_configure(config):\n    pass\n",
                        encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "hook")
        changed = ["pact-plugin/tests/conftest.py"]
        manifest = _facts_manifest(core)
        facts = core.gather_facts(repo, manifest, changed, base)
        result = core.select(core.discover(repo), manifest, changed, "ok",
                             facts)
        assert result.select_all_reason == (
            "conftest:tests/conftest.py:hook:pytest_configure")


class TestRunnerArtifactDestinations:
    """The runner writes only where its flags say, never to an ambient
    location such as $GITHUB_WORKSPACE."""

    def _runner(self):
        root = ci_contract.require_root("the fast-suite runner")
        return root / "scripts" / "run_fast_suite.py"

    def _env(self, workspace):
        env = {key: value for key, value in os.environ.items()
               if key != "PYTEST_ADDOPTS"}
        env["GITHUB_WORKSPACE"] = str(workspace)
        return env

    @pytest.mark.parametrize("mode", [[], ["--collect-only"]])
    def test_a_missing_destination_flag_fails_and_writes_nothing(
            self, tmp_path, mode):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        proc = subprocess.run(
            [sys.executable, str(self._runner()), *mode,
             "--base-sha", "0" * 40],
            capture_output=True, text=True, timeout=120,
            env=self._env(workspace), cwd=str(tmp_path))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        required = [line for line in proc.stderr.splitlines()
                    if "the following arguments are required" in line]
        assert required, proc.stderr
        assert "--summary" in required[0] and "--canary" in required[0]
        assert list(workspace.iterdir()) == []

    def test_the_canary_goes_to_its_flag_not_the_workspace(
            self, core, tmp_path):
        root, plugin, document = _build_rig(tmp_path)
        _write_manifest(root, document)
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "test@example.com")
        _git(root, "config", "user.name", "Test")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "rig")
        base = _git(root, "rev-parse", "HEAD")
        discovered = sorted(core.discover(root))
        # A stand-in pytest in the runner's working directory.
        (plugin / "pytest.py").write_text(
            "\n".join(f'print("{rel}::test_ok")' for rel in discovered)
            + f'\nprint("{len(discovered)} tests collected in 0.01s")\n',
            encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        proc = subprocess.run(
            [sys.executable, str(self._runner()), "--collect-only",
             "--base-sha", base, "--repo-root", str(root),
             "--manifest", str(root / "scripts" / "fast_suite_exclusions.json"),
             "--summary", str(out / "summary.json"),
             "--canary", str(out / "canary.json")],
            capture_output=True, text=True, timeout=120,
            env=self._env(workspace), cwd=str(plugin))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "canary.json").is_file()
        assert not (out / "summary.json").exists()
        assert list(workspace.iterdir()) == []
