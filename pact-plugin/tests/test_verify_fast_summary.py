"""The fast-lane verifier's named fail conditions, exercised for real.

The verifier is the advisory lane's evidence gate. Each row below starts from
a VALID artifact pair (asserted green first) and changes exactly one thing;
the named condition the verifier prints is the assertion. A condition that
is never fed a bad input is counted as coverage without being coverage.

The rig is a minimal checkout with a valid manifest and a git HEAD; the base
SHA is HEAD, so the recomputed selection is SELECT_ALL (empty diff) and the
expected file set is every discovered module.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace as _replace_selection
from pathlib import Path

import pytest

from ci_full_run_record import OWNER_ENV
from helpers import env_without_pytest_vars

ROOT = Path(__file__).resolve().parents[2]
_SELECTOR_SPEC = importlib.util.spec_from_file_location(
    "_pact_fast_verifier_selection", ROOT / "scripts" / "fast_suite_selection.py")
selector = importlib.util.module_from_spec(_SELECTOR_SPEC)
# Dataclass annotation resolution requires registration before execution.
sys.modules[_SELECTOR_SPEC.name] = selector
try:
    _SELECTOR_SPEC.loader.exec_module(selector)
except BaseException:
    sys.modules.pop(_SELECTOR_SPEC.name, None)
    raise
build_argv = selector.build_argv
load_manifest = selector.load_manifest
select_for_change = selector.select_for_change

# The verifier module itself, for the two guards no honest manifest can
# reach: EMPTY-EXPECTED and SELECTION-ARITHMETIC fire on a degenerate
# RECOMPUTED selection, and a correct selector never produces one (guard
# files are always included, and included == discovered - excluded by
# construction). Their tests patch the selection seam in-process instead of
# forging an input the selector cannot return.
_VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "_pact_fast_verifier_summary", ROOT / "scripts" / "verify_fast_summary.py")
verifier = importlib.util.module_from_spec(_VERIFIER_SPEC)
sys.modules[_VERIFIER_SPEC.name] = verifier
try:
    _VERIFIER_SPEC.loader.exec_module(verifier)
except BaseException:
    sys.modules.pop(_VERIFIER_SPEC.name, None)
    raise

VERIFIER = ROOT / "scripts" / "verify_fast_summary.py"

GUARD_FILES = (
    "tests/test_ci_collection_scope.py",
    "tests/test_import_hygiene.py",
    "tests/test_local_lint_parity.py",
    "tests/test_path_setup_pin.py",
    "tests/test_session_home_sandbox.py",
    "tests/test_conftest_config_root_isolation.py",
    "tests/test_audit_protocol.py",
    "tests/test_memory_store_isolation.py",
    "skills/pact-handoff-harvest/test_skill_loading_harvest.py",
    "skills/pact-agent-teams/test_skill_loading_agent_teams.py",
)
SELECTION_MACHINERY = (
    "scripts/fast_suite_selection.py",
    "scripts/fast_suite_args.py",
    "scripts/run_fast_suite.py",
    "scripts/verify_fast_summary.py",
    "scripts/fast_suite_exclusions.json",
    ".github/workflows/fast-tests.yml",
    "constraints-ci.txt",
    "constraints-ci.in",
    "pyproject.toml",
)
BASELINE = {
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
}


def _write(path, text="def test_ok():\n    assert True\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "rig"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        "{}", encoding="utf-8")
    plugin = root / "pact-plugin"
    guards = list(GUARD_FILES)
    exclusions = [f"tests/test_slow_{index}.py" for index in range(24)]
    for relative in guards + exclusions:
        _write(plugin / relative)
    entries = []
    for index, relative in enumerate(exclusions):
        seam = f"hooks/seam_{index}.py"
        _write(plugin / seam)
        entries.append({
            "path": relative,
            "displayed_seconds": 1.0,
            "tests": 1,
            "mechanism": "rig slow file",
            "production_seams": [seam],
        })
    for relative in SELECTION_MACHINERY:
        _write(root / relative, "")
    document = {
        "schema_version": 1,
        "path_base": "plugin_root",
        "baseline": BASELINE,
        "selection_machinery": list(SELECTION_MACHINERY),
        "value_inert_keys": {},
        "reach_reviews": {},
        "guard_files": guards,
        "exclusions": entries,
    }
    manifest = root / "scripts" / "fast_suite_exclusions.json"
    manifest.write_text(json.dumps(document, indent=2), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "rig")
    return root, plugin, manifest


def _discovered(plugin):
    return sorted(
        path.relative_to(plugin).as_posix()
        for path in plugin.rglob("test_*.py")
        if ".worktrees" not in path.relative_to(plugin).parts
    )


def _artifacts(root, plugin, manifest, *, summary_line=None,
               base_sha=None, head_sha=None, expected_files=None,
               selection_status="empty-diff",
               select_all_reason="changed-paths:empty-diff",
               excluded=0, added_back=0):
    if base_sha is None:
        base_sha = _git(root, "rev-parse", "HEAD")
    if head_sha is None:
        head_sha = base_sha
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    discovered = _discovered(plugin)
    if expected_files is None:
        expected_files = discovered
    node_ids = sorted(f"{path}::test_ok" for path in expected_files)
    count = len(expected_files)
    if summary_line is None:
        summary_line = f"{count} passed in 0.01s"
    selection = {
        "status": selection_status,
        "select_all_reason": select_all_reason,
        "discovered": len(discovered),
        "excluded": excluded,
        "added_back": added_back,
        "included": count,
        "included_files": expected_files,
        "included_sha256": hashlib.sha256(
            "".join(f"{path}\n" for path in sorted(expected_files)).encode("utf-8")
        ).hexdigest(),
    }
    recomputed, _, _ = select_for_change(
        root, load_manifest(manifest), base_sha)
    canary_command = build_argv(recomputed, plugin, "collect-only")
    run_command = build_argv(recomputed, plugin, "run", workers=4)
    common = {
        "schema_version": 2,
        "manifest": {"path": "scripts/fast_suite_exclusions.json",
                     "sha256": manifest_sha},
        "base_sha": base_sha,
        "head_sha": head_sha,
        "run_id": "test-run",
        "run_attempt": "1",
        "selection": selection,
    }
    canary = dict(common, mode="collect-only", canary={
        "command": canary_command,
        "ignore_paths": [argument.split("=", 1)[1]
                         for argument in canary_command
                         if argument.startswith("--ignore=")],
        "rc": 0,
        "summary_line": f"{count} tests collected in 0.01s",
        "expected_files": expected_files,
        "collected_files": expected_files,
        "collected_items": count,
        "collected_node_ids": node_ids,
        "missing": [],
        "extra": [],
        "unresolvable_node_id_bases": [],
    })
    summary = dict(common, mode="run", run={
        "command": run_command,
        "rc": 0,
        "summary_line": summary_line,
        "collected": count,
        "collected_header": f"4 workers [{count} items]",
        "passed": count,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
    })
    record = {
        "schema_version": 3,
        "collected": count,
        "passed": count,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
        "exitstatus": 0,
        "summary_line": summary_line,
        "terminal_node_ids": node_ids,
        "items_before_modifyitems": count,
        "items_after_modifyitems": count,
        "run_id": "test-run",
        "run_attempt": "1",
        "skip_sites": [],
        "skipped_at_collection": 0,
    }
    summary_path = root / ".fast-suite-summary.json"
    canary_path = root / ".fast-suite-canary.json"
    record_path = root / ".full-run-record.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    canary_path.write_text(json.dumps(canary, indent=1), encoding="utf-8")
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return summary_path, canary_path, base_sha


def _run(summary_path, canary_path, base_sha, root, manifest,
         run_record=None, env_updates=None):
    command = [
        sys.executable, str(VERIFIER),
        "--summary", str(summary_path),
        "--canary", str(canary_path),
        "--repo-root", str(root),
        "--manifest", str(manifest),
    ]
    if base_sha is not None:
        command += ["--base-sha", base_sha]
    if run_record is None:
        run_record = root / ".full-run-record.json"
    command += ["--run-record", str(run_record)]
    env = os.environ.copy()
    env.pop("BASE_SHA", None)
    # Synthetic-artifact tests must deterministically exercise the LOCAL
    # verifier path even under Actions, where GITHUB_ACTIONS=true and
    # GITHUB_SHA name the REAL checkout: these artifacts carry fabricated
    # head_sha values (rig heads built mid-test, not the PR head), so the
    # Actions-gated bindings would rightly refuse them. Scrub unless a test
    # explicitly supplies its own Actions identity (the three head_sha arms
    # and the null-identity test do). Measured cost of NOT scrubbing: the
    # four selection tests died under Actions on HEAD-SHA-MISMATCH — a
    # GITHUB_SHA pin to the test's base cannot survive the tests' own
    # post-pin commits.
    for name in ("GITHUB_ACTIONS", "GITHUB_SHA"):
        env.pop(name, None)
    env["GITHUB_RUN_ID"] = "test-run"
    env["GITHUB_RUN_ATTEMPT"] = "1"
    if env_updates:
        env.update(env_updates)
    return subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=120)


def test_the_valid_baseline_is_green(tmp_path, rig):
    """Non-vacuity floor: every bad-input row below starts from this pair.

    This is an Actions-mode control, so it names the checkout its synthetic
    artifacts carry (the post-F2 contract: the Actions path always has a
    GITHUB_SHA to bind against).
    """
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest, env_updates={
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": base_sha})
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout


@pytest.mark.parametrize("artifact_label", ["run", "canary"])
@pytest.mark.parametrize("field,forged_value", [
    ("status", "forged"),
    ("select_all_reason", "forged-reason"),
    ("discovered", -1),
    ("excluded", -1),
    ("added_back", -1),
    ("included", -1),
    ("included_files", ["tests/not_run.py"]),
    ("included_sha256", "f" * 64),
])
def test_each_selection_metadata_forgery_is_refused(
        tmp_path, rig, artifact_label, field, forged_value):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    valid = _run(summary, canary, base_sha, root, manifest)
    assert valid.returncode == 0, valid.stderr

    target = summary if artifact_label == "run" else canary
    _mutate(target, _set(("selection", field), forged_value))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, (artifact_label, field, proc.stdout, proc.stderr)
    assert "SELECTION-METADATA-MISMATCH" in proc.stderr
    assert f"{artifact_label}.selection" in proc.stderr
    assert field in proc.stderr


def test_the_cold_probe_run_selection_forgery_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    valid = _run(summary, canary, base_sha, root, manifest)
    assert valid.returncode == 0, valid.stderr

    def forge(data):
        data["selection"].update(
            status="forged", included=1,
            included_files=["tests/not_run.py"], included_sha256="f" * 64)

    _mutate(summary, forge)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "SELECTION-METADATA-MISMATCH" in proc.stderr
    assert "run.selection" in proc.stderr
    for field in ("status", "included", "included_files", "included_sha256"):
        assert field in proc.stderr


def test_an_add_back_selection_is_accepted(tmp_path, rig):
    """Positive control: the verifier accepts a non-SELECT_ALL add-back.

    An excluded test file is changed, so the selector adds it back; the
    expected set is every discovered file except the 23 still excluded. This
    is the path the earlier arithmetic bug reddened on every such PR.
    """
    root, plugin, manifest = rig
    base = _git(root, "rev-parse", "HEAD")
    slow = plugin / "tests" / "test_slow_0.py"
    slow.write_text("def test_ok():\n    assert True  # changed\n",
                    encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change the excluded test")
    head = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    expected = [
        path for path in discovered
        if path == "tests/test_slow_0.py"
        or not path.startswith("tests/test_slow_")
    ]
    summary, canary, _ = _artifacts(
        root, plugin, manifest, base_sha=base, head_sha=head,
        expected_files=expected, selection_status="ok",
        select_all_reason=None, excluded=len(discovered) - len(expected),
        added_back=1)
    proc = _run(summary, canary, base, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout
    assert "select_all=None" in proc.stdout


def test_the_ordinary_selection_is_accepted(tmp_path, rig):
    """The everyday path — exclusions applied, no add-backs.

    The other controls are SELECT_ALL (no exclusions applied) and an
    add-back (>0). This pins the shape between them: a changed path with no
    selection effect, the 24 exclusions applied, added_back == 0.
    """
    root, plugin, manifest = rig
    base = _git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change a non-plugin file")
    head = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    expected = [
        path for path in discovered
        if not path.startswith("tests/test_slow_")
    ]
    summary, canary, _ = _artifacts(
        root, plugin, manifest, base_sha=base, head_sha=head,
        expected_files=expected, selection_status="ok",
        select_all_reason=None, excluded=24, added_back=0)
    proc = _run(summary, canary, base, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout
    assert "select_all=None" in proc.stdout


def _commit_closure_change(root, plugin):
    """Make test_slow_0 import a support module, then change that module.

    Returns (base, head, expected) for the closure add-back: test_slow_0 comes
    back by closure, the other 23 exclusions stay applied.
    """
    _write(plugin / "tests" / "support_mod.py", "VALUE = 1\n")
    _write(plugin / "tests" / "test_slow_0.py",
           "import support_mod\n\ndef test_ok():\n"
           "    assert support_mod.VALUE\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "import a support module")
    base = _git(root, "rev-parse", "HEAD")
    _write(plugin / "tests" / "support_mod.py", "VALUE = 2\n")
    _git(root, "commit", "-q", "-am", "change the support module")
    head = _git(root, "rev-parse", "HEAD")
    expected = [path for path in _discovered(plugin)
                if path == "tests/test_slow_0.py"
                or not path.startswith("tests/test_slow_")]
    return base, head, expected


def test_a_closure_add_back_is_accepted(tmp_path, rig):
    """The verifier recomputes with the same rules: a support-module change
    adds back only the exclusion that imports it."""
    root, plugin, manifest = rig
    base, head, expected = _commit_closure_change(root, plugin)
    summary, canary, _ = _artifacts(
        root, plugin, manifest, base_sha=base, head_sha=head,
        expected_files=expected, selection_status="ok",
        select_all_reason=None, excluded=23, added_back=1)
    proc = _run(summary, canary, base, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "select_all=None" in proc.stdout


def test_a_selection_that_misses_a_closure_add_back_is_refused(tmp_path, rig):
    """Artifacts that ran the old narrow answer (all 24 applied) fail."""
    root, plugin, manifest = rig
    base, head, _ = _commit_closure_change(root, plugin)
    narrow = [path for path in _discovered(plugin)
              if not path.startswith("tests/test_slow_")]
    summary, canary, _ = _artifacts(
        root, plugin, manifest, base_sha=base, head_sha=head,
        expected_files=narrow, selection_status="ok",
        select_all_reason=None, excluded=24, added_back=0)
    proc = _run(summary, canary, base, root, manifest)
    assert proc.returncode == 1
    assert "EXPECTED-SET-MISMATCH" in proc.stderr


def test_a_decorated_summary_is_accepted(tmp_path, rig):
    """The CI run form (`-ra`, no `-q`) is `=`-decorated."""
    root, plugin, manifest = rig
    count = len(_discovered(plugin))
    summary, canary, base_sha = _artifacts(
        root, plugin, manifest,
        summary_line=f"===== {count} passed in 0.01s =====")
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout


def _mutate(artifact, mutation):
    data = json.loads(artifact.read_text(encoding="utf-8"))
    mutation(data)
    artifact.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _set(path, value):
    def apply(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return apply


@pytest.mark.parametrize(
    "case_id,mutate_summary,mutate_canary,expected",
    [
        ("canary-absent", None, "ABSENT", "ARTIFACT-ABSENT-CANARY"),
        ("run-absent", "ABSENT", None, "ARTIFACT-ABSENT-RUN"),
        ("schema-version", None, _set(("schema_version",), 99),
         "SCHEMA-VERSION-CANARY"),
        ("mode-missing", None, lambda d: d.pop("mode"), "KEY-MISSING-CANARY"),
        ("mode-mismatch", None, _set(("mode",), "run"), "MODE-MISMATCH"),
        ("manifest-key-missing", None,
         lambda d: d["manifest"].pop("sha256"), "KEY-MISSING-CANARY"),
        ("selection-not-object", None, _set(("selection",), None),
         "BAD-FIELD-CANARY-SELECTION"),
        ("key-missing", None,
         lambda d: d["canary"].pop("summary_line"), "KEY-MISSING-CANARY"),
        ("no-summary", None, _set(("canary", "summary_line"), ""),
         "NO-SUMMARY-CANARY"),
        ("canary-rc", None, _set(("canary", "rc"), 1), "CANARY-RC"),
        ("expected-mismatch", None,
         _set(("canary", "expected_files"), ["tests/test_missing.py"]),
         "EXPECTED-SET-MISMATCH"),
        ("collected-mismatch", None,
         _set(("canary", "collected_files"), ["tests/test_missing.py"]),
         "COLLECTED-SET-MISMATCH"),
        ("run-rc", _set(("run", "rc"), 1), None, "RUN-RC"),
        ("run-collected", _set(("run", "collected"), 0), None,
         "RUN-COLLECTED"),
        ("run-failed", _set(("run", "failed"), 1), None, "RUN-FAILED"),
        ("run-errors", _set(("run", "errors"), 1), None, "RUN-ERRORS"),
        ("run-deselected", _set(("run", "deselected"), 1), None,
         "RUN-DESELECTED"),
        ("canary-collected", None, _set(("canary", "collected_items"), 0),
         "CANARY-COLLECTED"),
        # A narrowing only the run sees: the canary collected one more item
        # than the run, with no deselection reported anywhere.
        ("collected-count-mismatch", None,
         lambda d: d["canary"].update(
             collected_items=d["canary"]["collected_items"] + 1,
             summary_line=f"{d['canary']['collected_items'] + 1} tests collected in 0.01s"),
         "COLLECTED-COUNT-MISMATCH"),
        ("ignore-mismatch", None,
         _set(("canary", "ignore_paths"), ["/tmp/x.py"]),
         "IGNORE-ARGS-MISMATCH"),
        ("sha-mismatch", None, _set(("head_sha",), "f" * 40),
         "ARTIFACT-SHA-MISMATCH"),
        ("manifest-drift", _set(("manifest", "sha256"), "0" * 64), None,
         "MANIFEST-DRIFT"),
    ],
)
def test_each_named_failure_is_reachable(
        tmp_path, rig, case_id, mutate_summary, mutate_canary, expected):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    if mutate_summary == "ABSENT":
        summary.unlink()
    elif callable(mutate_summary):
        _mutate(summary, mutate_summary)
    if mutate_canary == "ABSENT":
        canary.unlink()
    elif callable(mutate_canary):
        _mutate(canary, mutate_canary)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, (case_id, proc.stdout, proc.stderr)
    assert expected in proc.stderr, (case_id, proc.stderr)


@pytest.mark.parametrize("target_name,label", [("summary", "run"), ("canary", "canary")])
def test_manifest_path_and_hash_are_bound_with_valid_controls(
        tmp_path, rig, target_name, label):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stderr

    target = {"summary": summary, "canary": canary}[target_name]
    _mutate(target, _set(("manifest", "path"), "scripts/other-manifest.json"))
    wrong_path = _run(summary, canary, base_sha, root, manifest)
    assert wrong_path.returncode == 1
    assert f"MANIFEST-PATH-MISMATCH: {label}" in wrong_path.stderr

    _mutate(target, _set(
        ("manifest", "path"), "scripts/fast_suite_exclusions.json"))
    _mutate(target, _set(("manifest", "sha256"), "0" * 64))
    wrong_hash = _run(summary, canary, base_sha, root, manifest)
    assert wrong_hash.returncode == 1
    assert f"MANIFEST-DRIFT: {label}" in wrong_hash.stderr


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_an_outside_repository_manifest_invocation_remains_valid(
        tmp_path, rig, relative):
    root, plugin, manifest = rig
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    invoked = Path(os.path.relpath(outside, Path.cwd())) if relative else outside
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    for artifact in (summary, canary):
        _mutate(artifact, _set(("manifest", "path"), str(invoked)))
    proc = _run(summary, canary, base_sha, root, invoked)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_a_genuinely_invalid_outside_repository_manifest_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    outside = tmp_path / "invalid-outside-manifest.json"
    outside.write_text("{}", encoding="utf-8")
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, outside)
    assert proc.returncode == 1
    assert "MANIFEST-INVALID" in proc.stderr
    assert str(outside) in proc.stderr


def test_a_missing_independent_base_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, _ = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, None, root, manifest)
    assert proc.returncode == 1
    assert "BASE-SHA-MISSING" in proc.stderr


def test_an_artifact_base_that_differs_from_the_independent_source_is_refused(
        tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, "f" * 40, root, manifest)
    assert proc.returncode == 1
    assert "BASE-SHA-MISMATCH" in proc.stderr


def test_an_empty_collected_set_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    _mutate(canary, _set(("canary", "collected_files"), []))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "EMPTY-COLLECTED" in proc.stderr


def test_the_runner_canary_rejects_a_deselected_summary(tmp_path, rig):
    """The fast canary's own token scan, not only the full guard's.

    The file sets match (a deselection leaves the file collected), so only
    the token scan can fail the canary. Deleting it must redden here.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    fake = "\n".join(f'print("{rel}::test_ok")' for rel in discovered)
    fake += (f'\nprint("{len(discovered)} tests collected, 1 deselected '
             f'in 0.01s")\n')
    (plugin / "pytest.py").write_text(fake, encoding="utf-8")
    summary = tmp_path / "summary.json"
    runner = ROOT / "scripts" / "run_fast_suite.py"
    env = env_without_pytest_vars()
    env.pop(OWNER_ENV, None)
    proc = subprocess.run(
        [sys.executable, str(runner), "--collect-only",
         "--base-sha", base_sha, "--repo-root", str(root),
         "--manifest", str(manifest), "--summary", str(summary),
         "--canary", str(summary.with_name(".fast-suite-canary.json"))],
        capture_output=True, text=True, timeout=120,
        env=env)
    assert proc.returncode == 1
    assert "CANARY FAILURE" in proc.stderr
    assert "reports deselection or errors" in proc.stderr


def test_no_summary_line_uses_the_no_summary_condition(tmp_path, rig):
    """An absent summary must not be read as a zero count."""
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", "summary_line"), ""))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "NO-SUMMARY-RUN" in proc.stderr
    assert "RUN-COLLECTED" not in proc.stderr


def _mutate_record(root, mutation):
    path = root / ".full-run-record.json"
    _mutate(path, mutation)
    return path


@pytest.mark.parametrize("case_id,mutation,expected", [
    ("schema", _set(("schema_version",), 2), "SCHEMA-VERSION-RECORD"),
    ("missing-key", lambda d: d.pop("terminal_node_ids"), "KEY-MISSING"),
    ("no-summary", _set(("summary_line",), ""), "NO-SUMMARY-RECORD"),
    ("zero-collected", lambda d: d.update(
        collected=0, items_before_modifyitems=0, items_after_modifyitems=0),
     "RECORD-COLLECTED"),
    ("summary-outcome-mismatch", lambda d: d.update(
        summary_line=f"{d['passed'] + 1} passed in 0.01s"),
     "RECORD-SUMMARY-OUTCOME-MISMATCH"),
    ("null-deselected", _set(("deselected",), None), "BAD-FIELD-DESELECTED"),
    ("null-before", _set(("items_before_modifyitems",), None),
     "BAD-FIELD-ITEMS_BEFORE_MODIFYITEMS"),
    ("null-after", _set(("items_after_modifyitems",), None),
     "BAD-FIELD-ITEMS_AFTER_MODIFYITEMS"),
    ("collection-hook", lambda d: d.update(
        items_after_modifyitems=d["collected"] - 1),
     "COLLECTION-HOOK-NARROWING"),
    ("exitstatus", _set(("exitstatus",), 1), "EXITSTATUS"),
    ("record-failed", _set(("failed",), 1), "RECORD-FAILED"),
    ("record-errors", _set(("errors",), 1), "RECORD-ERRORS"),
    ("record-deselected", _set(("deselected",), 1), "RECORD-DESELECTED"),
    ("negative-passed", _set(("passed",), -1), "BAD-FIELD-PASSED"),
    ("terminal-duplicate", lambda d: d["terminal_node_ids"].__setitem__(
        1, d["terminal_node_ids"][0]), "TERMINAL-SET"),
    ("terminal-missing", lambda d: d["terminal_node_ids"].pop(), "TERMINAL-SET"),
    ("same-count-different-node", lambda d: d["terminal_node_ids"].__setitem__(
        0, "tests/test_other.py::test_ok"), "NODE-ID-SET-MISMATCH"),
    ("bad-skip-sites", _set(("skip_sites",), [{"file": "x"}]), "SKIP-SITES-BAD"),
    ("skip-total", _set(("skip_sites",), [
        {"file": "tests/test_x.py", "reason": "x", "count": 1}]),
     "SKIP-SITES-INCOMPLETE"),
    ("collection-skips", _set(("skipped_at_collection",), 1),
     "BAD-FIELD-SKIPPED_AT_COLLECTION"),
])
def test_each_record_failure_is_reachable(tmp_path, rig, case_id, mutation, expected):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    record = _mutate_record(root, mutation)
    proc = _run(summary, canary, base_sha, root, manifest, run_record=record)
    assert proc.returncode == 1, (case_id, proc.stdout, proc.stderr)
    assert expected in proc.stderr, (case_id, proc.stderr)


@pytest.mark.parametrize("path,value,expected", [
    (("run", "command", -2), "2", "RUN-COMMAND-MISMATCH"),
    (("run", "xfailed"), 1, "RUN-SUMMARY-OUTCOME-MISMATCH"),
])
def test_run_command_and_outcome_mutations_are_refused(
        tmp_path, rig, path, value, expected):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(path, value))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert expected in proc.stderr


def test_a_canary_command_mutation_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    _mutate(canary, lambda d: d["canary"]["command"].append("-q"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "CANARY-COMMAND-MISMATCH" in proc.stderr


@pytest.mark.parametrize("mutation", [
    lambda d: d["canary"].update(
        collected_node_ids=list(reversed(d["canary"]["collected_node_ids"]))),
    lambda d: d["canary"]["collected_node_ids"].__setitem__(
        1, d["canary"]["collected_node_ids"][0]),
])
def test_invalid_canary_node_ids_are_refused(tmp_path, rig, mutation):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    _mutate(canary, mutation)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "CANARY-NODE-IDS" in proc.stderr


@pytest.mark.parametrize("artifact_name", ["summary", "canary", "record"])
def test_cross_artifact_run_identity_is_required(
        tmp_path, rig, artifact_name):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    artifact = {"summary": summary, "canary": canary,
                "record": root / ".full-run-record.json"}[artifact_name]
    _mutate(artifact, _set(("run_attempt",), "2"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "RUN-IDENTITY" in proc.stderr


def test_actions_rejects_null_identity_even_when_all_artifacts_agree(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    for artifact in (summary, canary, root / ".full-run-record.json"):
        _mutate(artifact, _set(("run_id",), None))
    # Actions mode names the checkout too (post-F2 contract), so the null
    # run_id — not a head mismatch — is what this refusal isolates.
    proc = _run(summary, canary, base_sha, root, manifest, env_updates={
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": base_sha})
    assert proc.returncode == 1
    assert "RUN-IDENTITY" in proc.stderr


def test_summary_record_outcome_disagreement_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)

    def make_xfail(record):
        record["passed"] -= 1
        record["xfailed"] = 1
        record["summary_line"] = (
            f"{record['passed']} passed, 1 xfailed in 0.01s")

    _mutate_record(root, make_xfail)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "OUTCOME-MISMATCH" in proc.stderr


def test_consistent_but_incomplete_outcome_accounting_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)

    def lose_one(artifact):
        target = artifact["run"] if "run" in artifact else artifact
        target["passed"] -= 1
        target["summary_line"] = f"{target['passed']} passed in 0.01s"

    _mutate(summary, lose_one)
    _mutate_record(root, lose_one)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "ACCOUNTING" in proc.stderr


def test_consistent_but_extra_non_skip_outcome_accounting_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)

    def add_one(artifact):
        target = artifact["run"] if "run" in artifact else artifact
        target["passed"] += 1
        target["summary_line"] = f"{target['passed']} passed in 0.01s"

    _mutate(summary, add_one)
    _mutate_record(root, add_one)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1
    assert "ACCOUNTING" in proc.stderr


def test_a_consistent_pass_plus_teardown_skip_is_accepted(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)

    def add_teardown_skip(artifact):
        target = artifact["run"] if "run" in artifact else artifact
        target["skipped"] = 1
        target["summary_line"] = f"{target['passed']} passed, 1 skipped in 0.01s"

    _mutate(summary, add_teardown_skip)
    _mutate_record(root, add_teardown_skip)
    _mutate_record(root, _set(("skip_sites",), [
        {"file": "tests/test_x.py", "reason": "teardown skip", "count": 1}]))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_the_run_record_option_is_required(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--summary", str(summary),
         "--canary", str(canary), "--repo-root", str(root),
         "--manifest", str(manifest), "--base-sha", base_sha],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "--run-record" in proc.stderr


def _fake_collect(plugin, discovered, summary):
    """A stand-in `pytest` module in the runner's working directory."""
    fake = "\n".join(f'print("{rel}::test_ok")' for rel in discovered)
    fake += f'\nprint("{summary}")\n'
    (plugin / "pytest.py").write_text(fake, encoding="utf-8")


def _run_canary(root, manifest, summary, base_sha, env=None):
    runner = ROOT / "scripts" / "run_fast_suite.py"
    if env is None:
        env = env_without_pytest_vars()
        env.pop("PACT_FULL_RUN_RECORD_OWNER", None)
    return subprocess.run(
        [sys.executable, str(runner), "--collect-only",
         "--base-sha", base_sha, "--repo-root", str(root),
         "--manifest", str(manifest), "--summary", str(summary),
         "--canary", str(summary.with_name(".fast-suite-canary.json"))],
        capture_output=True, text=True, timeout=120, env=env)


def test_the_canary_records_its_collected_item_count(tmp_path, rig):
    """The verifier compares the run against this number, so it must exist."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    _fake_collect(plugin, discovered,
                  f"{len(discovered)} tests collected in 0.01s")
    summary = tmp_path / "summary.json"
    proc = _run_canary(root, manifest, summary, base_sha)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    canary = json.loads(
        (tmp_path / ".fast-suite-canary.json").read_text(encoding="utf-8"))
    assert canary["canary"]["collected_items"] == len(discovered)
    assert canary["canary"]["collected_node_ids"] == sorted(
        f"{path}::test_ok" for path in discovered)


def test_the_canary_refuses_a_count_node_id_mismatch(tmp_path, rig):
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    _fake_collect(plugin, discovered,
                  f"{len(discovered) + 5} tests collected in 0.01s")
    summary = tmp_path / "summary.json"
    proc = _run_canary(root, manifest, summary, base_sha)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unique collected node IDs" in proc.stderr


def test_the_canary_refuses_duplicate_node_ids(tmp_path, rig):
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    fake = "\n".join(f'print("{relative}::test_ok")'
                     for relative in discovered)
    fake += f'\nprint("{discovered[0]}::test_ok")\n'
    fake += f'print("{len(discovered)} tests collected in 0.01s")\n'
    (plugin / "pytest.py").write_text(fake, encoding="utf-8")
    proc = _run_canary(root, manifest, tmp_path / "summary.json", base_sha)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "duplicate collected node IDs" in proc.stderr


@pytest.mark.parametrize("name", [
    "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_XDIST_WORKER",
    "PACT_FULL_RUN_RECORD_OWNER",
])
def test_the_runner_refuses_inherited_pytest_or_recorder_environment(
        tmp_path, rig, name):
    """Inherited options can narrow the run or suppress its in-band record."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    _fake_collect(plugin, discovered,
                  f"{len(discovered)} tests collected in 0.01s")
    summary = tmp_path / "summary.json"
    env = env_without_pytest_vars()
    env.pop("PACT_FULL_RUN_RECORD_OWNER", None)
    env[name] = "contaminated"
    proc = _run_canary(root, manifest, summary, base_sha, env=env)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert name in proc.stderr
    assert not (tmp_path / ".fast-suite-canary.json").exists()


# ---- head_sha checkout binding, header capture, degenerate selections ----

def test_actions_head_sha_must_equal_the_checked_out_head(tmp_path, rig):
    """Under Actions the artifacts name THIS checkout, not just each other.

    Both artifacts are moved to the same wrong head — a stale pair agreeing
    with each other, which the canary-to-run equality cannot see.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(canary, _set(("head_sha",), "f" * 40))
    _mutate(summary, _set(("head_sha",), "f" * 40))
    proc = _run(summary, canary, base_sha, root, manifest, env_updates={
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": base})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "HEAD-SHA-MISMATCH" in proc.stderr
    assert "ffffffff" in proc.stderr


def test_actions_head_sha_binding_refuses_a_missing_github_sha(tmp_path, rig):
    """No checkout anchor under Actions is a refusal, never a skip."""
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest, env_updates={
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": ""})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "HEAD-SHA-MISMATCH" in proc.stderr


def test_actions_head_sha_binding_accepts_the_real_checkout(tmp_path, rig):
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest, env_updates={
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": base})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_the_runner_captures_the_first_collected_header_only(tmp_path, rig):
    """Two disagreeing header lines: only the top-of-log one is this run's.

    The first line uses xdist's workers form, so the parse of that form is
    pinned too. A last-match capture records 999 here; first-match records
    the real header count.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    count = len(discovered)
    fake = (
        "import sys\n"
        f"print('4 workers [{count} items]')\n"
        "print('noise line one')\n"
        "print('collected 999 items')\n"
        f"print('{count} passed in 0.01s')\n"
    )
    (plugin / "pytest.py").write_text(fake, encoding="utf-8")
    summary = tmp_path / "summary.json"
    runner = ROOT / "scripts" / "run_fast_suite.py"
    env = env_without_pytest_vars()
    env.pop(OWNER_ENV, None)
    proc = subprocess.run(
        [sys.executable, str(runner), "--base-sha", base_sha,
         "--repo-root", str(root), "--manifest", str(manifest),
         "--summary", str(summary),
         "--canary", str(summary.with_name(".fast-suite-canary.json"))],
        capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(summary.read_text(encoding="utf-8"))
    assert artifact["run"]["collected"] == count, artifact["run"]
    assert artifact["run"]["collected"] != 999
    # The captured line itself is persisted, not just the count parsed from
    # it: the verifier re-derives the count from this string, so the
    # first-match rule is load-bearing evidence, not an implementation detail.
    assert artifact["run"]["collected_header"] == f"4 workers [{count} items]"


def test_the_runner_refuses_to_write_without_a_collected_header(tmp_path, rig):
    """No captured header, no artifact: the count would lack printed provenance.

    A run whose output never contains a collected-items header line (here a
    fake pytest that prints only node IDs and a summary) cannot produce an
    artifact the verifier can re-derive a count from, so the runner refuses
    at write time instead of writing an artifact whose count is only
    inferred.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    discovered = _discovered(plugin)
    fake = "\n".join(f'print("{rel}::test_ok")' for rel in discovered)
    fake += f'\nprint("{len(discovered)} passed in 0.01s")\n'
    (plugin / "pytest.py").write_text(fake, encoding="utf-8")
    summary = tmp_path / "summary.json"
    runner = ROOT / "scripts" / "run_fast_suite.py"
    env = env_without_pytest_vars()
    env.pop(OWNER_ENV, None)
    proc = subprocess.run(
        [sys.executable, str(runner), "--base-sha", base_sha,
         "--repo-root", str(root), "--manifest", str(manifest),
         "--summary", str(summary),
         "--canary", str(summary.with_name(".fast-suite-canary.json"))],
        capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "NO-COLLECTED-HEADER" in proc.stderr
    assert not summary.exists()


def _demote_collected_everywhere(root):
    """Demote the collected count by one in EVERY cross-checked field.

    run.collected, canary.collected_items and its node list and its own
    summary parity, record.collected and the terminal list and the
    before/after identity and the outcome sum, and both outcome summary
    lines all move together -- every pre-existing cross-check stays
    satisfied. Only the persisted header still says the original count.
    """
    summary_path = root / ".fast-suite-summary.json"
    canary_path = root / ".fast-suite-canary.json"
    record_path = root / ".full-run-record.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    canary = json.loads(canary_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    count = summary["run"]["collected"]
    demoted = count - 1
    node_ids = canary["canary"]["collected_node_ids"][:-1]
    demoted_line = f"{demoted} passed in 0.01s"
    summary["run"].update(
        collected=demoted, passed=demoted, summary_line=demoted_line,
        collected_header=f"4 workers [{count} items]")
    canary["canary"].update(
        collected_items=demoted,
        collected_node_ids=node_ids,
        summary_line=f"{demoted} tests collected in 0.01s")
    record.update(
        collected=demoted, passed=demoted, summary_line=demoted_line,
        terminal_node_ids=node_ids,
        items_before_modifyitems=demoted,
        items_after_modifyitems=demoted)
    for path, data in ((summary_path, summary), (canary_path, canary),
                       (record_path, record)):
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return demoted


def test_a_count_demoted_everywhere_but_the_header_is_refused(tmp_path, rig):
    """The gap the persisted header closes: the cross-checks move together.

    A count tampered in all cross-checked fields (run, canary, record --
    including node lists and arithmetic) satisfies every pre-existing check;
    before the header was persisted, nothing anchored the number to what the
    run actually printed. The stored header is that anchor. Control arm
    first: the unmutated artifacts verify green.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    demoted = _demote_collected_everywhere(root)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RUN-COLLECTED-HEADER" in proc.stderr
    assert "disagrees" in proc.stderr
    assert str(demoted) in proc.stderr
    assert str(demoted + 1) in proc.stderr


def test_a_missing_collected_header_is_refused_as_key_missing(tmp_path, rig):
    """The key is required, not optional: an artifact written before the key
    existed, or with it stripped, refuses at the shape gate, fail-closed."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    _mutate(summary, lambda d: d["run"].pop("collected_header"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "KEY-MISSING-RUN" in proc.stderr
    assert "'collected_header'" in proc.stderr


@pytest.mark.parametrize("value", [None, ""])
def test_a_non_string_collected_header_is_refused_as_bad_field(
        tmp_path, rig, value):
    """A present-but-unusable header is a bad field, not a count disagreement."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", "collected_header"), value))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BAD-FIELD-RUN-COLLECTED-HEADER" in proc.stderr


def test_an_unparseable_collected_header_is_refused(tmp_path, rig):
    """A header line no known shape matches cannot witness any count."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", "collected_header"), "beginning of session"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RUN-COLLECTED-HEADER" in proc.stderr
    assert "matches no known header shape" in proc.stderr


def test_a_workers_form_header_re_derives_to_the_same_count(tmp_path, rig):
    """Positive control for the xdist shape: `4 workers [N items]` re-derives
    N through group(2) and verifies green."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    count = len(_discovered(plugin))
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", "collected_header"),
                          f"4 workers [{count} items]"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_a_collected_form_header_beside_a_parallel_command_is_refused(
        tmp_path, rig):
    """The persisted header's FORM is bound to the run command's shape.

    A parallel command (`-n 4 --dist loadfile`) is distributed by xdist and
    prints the workers form; a serial header beside it would evidence a
    different run than the command recorded. Measured cold-7 hole: this
    verified green before the binding. Control arm first: the workers-form
    header beside the same command is green (the builders' default).
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    count = len(_discovered(plugin))
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    _mutate(summary, _set(("run", "collected_header"),
                          f"collected {count} items"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "RUN-COLLECTED-HEADER-FORM" in proc.stderr
    assert "parallel" in proc.stderr


def _strip_workers_from_run_command(root):
    summary_path = root / ".fast-suite-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["run"]["command"] = [
        argument for argument in summary["run"]["command"]
        if argument not in ("-n", "--dist", "loadfile", "4")]
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")


def _serial_form_verification(root, manifest, monkeypatch, header):
    """Verify with the recomputed EXPECTED run command forced serial.

    The patched build_argv strips the workers flags from run mode only, so
    the rebuilt expected command is serial and the artifact's (stripped)
    command matches it; _verify_in_process patches the selection seam as in
    the degenerate-selection tests. Returns the verifier's rc.
    """
    real = selector.build_argv

    def serial_argv(selection, cwd, mode, workers=None):
        argv = real(selection, cwd, mode, workers=workers)
        if mode == "run":
            argv = [argument for argument in argv
                    if argument not in ("-n", "--dist", "loadfile", "4")]
        return argv

    root_path, plugin, manifest_path = root, root / "pact-plugin", manifest
    base_sha = _git(root_path, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root_path, plugin, manifest_path)
    _strip_workers_from_run_command(root_path)
    _mutate(summary, _set(("run", "collected_header"), header))
    monkeypatch.setattr(verifier, "build_argv", serial_argv)
    selection, _, _ = select_for_change(
        root_path, load_manifest(manifest_path), base_sha)
    return _verify_in_process(root_path, manifest_path, summary, canary,
                              base_sha, selection, monkeypatch)


def test_a_serial_run_command_takes_the_collected_form(tmp_path, rig,
                                                       monkeypatch, capsys):
    """The other binding cell: a serial command prints pytest's collected
    form, so a collected-form header beside a serial command is green."""
    root, plugin, manifest = rig
    count = len(_discovered(plugin))
    rc = _serial_form_verification(root, manifest, monkeypatch,
                                   f"collected {count} items")
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    assert "VERIFY-OK" in captured.out


def test_a_serial_run_command_refuses_the_workers_form(tmp_path, rig,
                                                       monkeypatch, capsys):
    """Mirror refusal: a workers-form header beside a serial command
    evidences a distributed run that the recorded command did not run."""
    root, plugin, manifest = rig
    count = len(_discovered(plugin))
    rc = _serial_form_verification(root, manifest, monkeypatch,
                                   f"4 workers [{count} items]")
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "RUN-COLLECTED-HEADER-FORM" in captured.err
    assert "serial" in captured.err


def test_an_unparseable_canary_artifact_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    canary.write_text("{not json", encoding="utf-8")
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ARTIFACT-UNPARSEABLE-CANARY" in proc.stderr
    assert "not readable JSON" in proc.stderr


def test_an_unparseable_run_artifact_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    summary.write_text("{not json either", encoding="utf-8")
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ARTIFACT-UNPARSEABLE-RUN" in proc.stderr
    assert "not readable JSON" in proc.stderr


def test_an_invalid_manifest_is_refused(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    bad = root / "scripts" / "rejected_manifest.json"
    bad.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    proc = _run(summary, canary, base_sha, root, bad)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "MANIFEST-INVALID" in proc.stderr
    assert "rejected_manifest.json" in proc.stderr


def test_canary_internal_parity_summary_vs_collected_items(tmp_path, rig):
    """The canary's parsed summary count must equal its own collected_items.

    This is the PARITY arm of CANARY-COLLECTED: both numbers come from the
    canary artifact itself, so a disagreement is the artifact contradicting
    its own evidence. Distinct from the _positive_int arm the existing
    matrix drives (collected_items <= 0) — that one needs a bad number,
    this one needs two DIFFERENT positive counts. Control arm first: the
    unmutated artifacts verify green.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    assert "VERIFY-OK" in control.stdout
    count = len(_discovered(plugin))
    _mutate(canary, _set(("canary", "summary_line"),
                         f"{count - 1} tests collected in 0.01s"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CANARY-COLLECTED" in proc.stderr
    assert "canary summary collected" in proc.stderr


def test_a_missing_run_artifact_key_is_refused_as_key_missing_run(tmp_path, rig):
    """A required key popped from the run artifact refuses as KEY-MISSING-RUN.

    The record's missing-key case is censused by the shared matrix; this is
    the run-artifact sibling through the same _require_keys branch (the
    canary sibling shares the branch and differs only in label). Control
    arm first: the unmutated artifacts verify green.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    assert "VERIFY-OK" in control.stdout
    _mutate(summary, lambda d: d["run"].pop("collected"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "KEY-MISSING-RUN" in proc.stderr
    assert "'collected'" in proc.stderr


def test_canary_unresolvable_node_id_bases_are_refused(tmp_path, rig):
    """A canary that cannot account for a collected file cannot verify.

    The runner refuses non-empty unresolvable_node_id_bases at collection
    time; this is the verifier-side twin, so a tampered or stale canary
    artifact cannot carry unaccounted bases past the gate. Control arm
    first: the unmutated artifacts verify green.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    assert "VERIFY-OK" in control.stdout
    _mutate(canary, _set(("canary", "unresolvable_node_id_bases"),
                         ["tests/test_unaccounted.py::test_ok"]))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CANARY-UNRESOLVED-BASES" in proc.stderr
    assert "test_unaccounted" in proc.stderr


@pytest.mark.parametrize("value", ["", 0, False])
def test_a_falsy_unresolvable_field_is_refused_as_bad_field(
        tmp_path, rig, value):
    """Measured cold-7 hole: the guard was truthiness-only, so a falsy
    non-list slipped through green against the module's rule that absent or
    malformed evidence fails. Type first, then emptiness -- every sibling
    guard type-checks its input.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(canary, _set(("canary", "unresolvable_node_id_bases"), value))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BAD-FIELD-CANARY-UNRESOLVED-BASES" in proc.stderr


def test_an_empty_unresolvable_list_still_verifies_green(tmp_path, rig):
    """Control arm for the new type check: a proper empty list (the honest
    all-accounted-for case) must stay green, not be over-blocked."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_a_non_string_unresolvable_base_is_refused_as_bad_field(
        tmp_path, rig):
    """A list with a non-string element is malformed evidence, not a
    accounted-for canary."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(canary, _set(("canary", "unresolvable_node_id_bases"),
                         ["tests/test_ok.py", 7]))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BAD-FIELD-CANARY-UNRESOLVED-BASES" in proc.stderr


@pytest.mark.parametrize("artifact_name, nested_key, condition", [
    ("canary", "canary", "BAD-FIELD-CANARY-RC"),
    ("summary", "run", "BAD-FIELD-RUN-RC"),
])
@pytest.mark.parametrize("value", [False, True])
def test_a_boolean_rc_is_refused_as_a_bad_field(tmp_path, rig, artifact_name,
                                                nested_key, condition, value):
    """JSON false compares == 0 in Python, so rc truthiness cannot gate.

    An untyped rc check passes rc=false as a clean exit; rc=true only
    failed by the accident that True != 0. Both booleans are type-refused
    under BAD-FIELD names; integer rc values keep the CANARY-RC / RUN-RC
    conditions the existing matrix pins.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    artifact = {"canary": canary, "summary": summary}[artifact_name]
    _mutate(artifact, _set((nested_key, "rc"), value))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert condition in proc.stderr


def test_an_integer_zero_rc_passes_the_typed_check(tmp_path, rig):
    """The type refusal must not over-block a real clean exit (control)."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY-OK" in proc.stdout


def test_a_missing_canary_rc_takes_the_missing_key_path(tmp_path, rig):
    """rc absent is a shape failure (KEY-MISSING-CANARY), not a bad value."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(canary, lambda d: d["canary"].pop("rc"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "KEY-MISSING-CANARY" in proc.stderr
    assert "BAD-FIELD" not in proc.stderr


@pytest.mark.parametrize("key", ["missing", "extra"])
def test_a_nonempty_canary_delta_field_is_refused(tmp_path, rig, key):
    """The canary's missing/extra fields are write-time outputs of the same
    comparison the verifier re-derives from the file sets, so a clean canary
    always carries both empty; a tampered canary claiming a delta the
    recomputation does not corroborate refuses. Control arm first.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    _mutate(canary, _set(("canary", key), ["tests/test_not_collected.py"]))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"CANARY-{key.upper()}" in proc.stderr


def test_a_non_string_canary_delta_element_is_refused(tmp_path, rig):
    """A delta field with a non-string element is malformed evidence under
    the typed-reads convention, before any emptiness question."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(canary, _set(("canary", "missing"), ["tests/test_ok.py", 7]))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BAD-FIELD-CANARY-MISSING" in proc.stderr


def test_a_deselection_claiming_canary_summary_is_refused(tmp_path, rig):
    """Verifier-side mirror of the runner's write-time token scan: a canary
    summary reporting deselection or errors narrows the selection, and only
    this re-check sees it (a deselection leaves the file collected, so the
    file-set comparisons stay equal). Control arm first: the same parseable
    summary without the token is green.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    count = len(_discovered(plugin))
    summary, canary, base = _artifacts(root, plugin, manifest)
    control = _run(summary, canary, base_sha, root, manifest)
    assert control.returncode == 0, control.stdout + control.stderr
    _mutate(canary, _set(("canary", "summary_line"),
                         f"{count} tests collected, 2 deselected in 0.01s"))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CANARY-DESELECTED" in proc.stderr
    assert "2 deselected" in proc.stderr


@pytest.mark.parametrize("key", ["failed", "errors", "deselected"])
def test_a_false_run_outcome_refuses_at_the_local_zero_check(
        tmp_path, rig, key):
    """Site proof, not just name proof: JSON false equals 0 in Python, so the
    untyped zero-check passed it locally and a distant site (the outcome
    summary check) refused under the same condition name. run.collected=0 is
    co-mutated because node identity would refuse it first: whichever check
    names its condition is the one that saw the false. Post-fix the local
    zero-check is type-first and fires first; pre-fix RUN-COLLECTED fired.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", key), False))
    _mutate(summary, _set(("run", "collected"), 0))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"BAD-FIELD-RUN-{key.upper()}" in proc.stderr
    assert "RUN-COLLECTED" not in proc.stderr


@pytest.mark.parametrize("key", ["failed", "errors", "deselected"])
def test_a_false_run_outcome_refuses_with_the_local_detail(
        tmp_path, rig, key):
    """The local refusal names the type expectation; the distant site's
    message ('expected an integer >= 0') must not leak into the output."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate(summary, _set(("run", key), False))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"BAD-FIELD-RUN-{key.upper()}" in proc.stderr
    assert "expected an integer >= 0" not in proc.stderr


@pytest.mark.parametrize("key", ["failed", "errors", "deselected"])
def test_a_false_record_outcome_keeps_its_typed_local_refusal(
        tmp_path, rig, key):
    """The record side was already type-first (_require_record_int); these
    pins lock the typed local refusal so the run-side fix cannot drift the
    two sides apart."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    _mutate_record(root, _set((key,), False))
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"BAD-FIELD-{key.upper()}" in proc.stderr


def test_integer_zero_outcomes_pass_the_typed_checks(tmp_path, rig):
    """Control arm for the typed zero-checks: real integer zeros (the honest
    clean-run case) must stay green, not be over-blocked."""
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY-OK" in proc.stdout


def _selection_block_of(selection):
    """The selection block the verifier's _require_selection_identity expects."""
    included = sorted(selection.included)
    digest = hashlib.sha256()
    for path in included:
        digest.update(path.encode("utf-8"))
        digest.update(b"\n")
    return {
        "status": selection.status,
        "select_all_reason": selection.select_all_reason,
        "discovered": len(selection.discovered),
        "excluded": len(selection.excluded),
        "added_back": len(selection.added_back),
        "included": len(included),
        "included_files": included,
        "included_sha256": digest.hexdigest(),
    }


def _artifacts_from_selection(root, plugin, manifest, selection, base_sha):
    """Valid artifacts for an arbitrary selection, doctored or not."""
    expected_files = sorted(selection.included)
    node_ids = sorted(f"{path}::test_ok" for path in expected_files)
    count = len(expected_files)
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    common = {
        "schema_version": 2,
        "manifest": {"path": "scripts/fast_suite_exclusions.json",
                     "sha256": manifest_sha},
        "base_sha": base_sha,
        "head_sha": base_sha,
        "run_id": "test-run",
        "run_attempt": "1",
        "selection": _selection_block_of(selection),
    }
    canary_command = build_argv(selection, plugin, "collect-only")
    run_command = build_argv(selection, plugin, "run", workers=4)
    canary = dict(common, mode="collect-only", canary={
        "command": canary_command,
        "ignore_paths": [argument.split("=", 1)[1]
                         for argument in canary_command
                         if argument.startswith("--ignore=")],
        "rc": 0,
        "summary_line": f"{count} tests collected in 0.01s",
        "expected_files": expected_files,
        "collected_files": expected_files,
        "collected_items": count,
        "collected_node_ids": node_ids,
        "missing": [],
        "extra": [],
        "unresolvable_node_id_bases": [],
    })
    summary = dict(common, mode="run", run={
        "command": run_command,
        "rc": 0,
        "summary_line": f"{count} passed in 0.01s",
        "collected": count,
        "collected_header": f"4 workers [{count} items]",
        "passed": count,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
    })
    record = {
        "schema_version": 3,
        "collected": count,
        "passed": count,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
        "exitstatus": 0,
        "summary_line": f"{count} passed in 0.01s",
        "terminal_node_ids": node_ids,
        "items_before_modifyitems": count,
        "items_after_modifyitems": count,
        "run_id": "test-run",
        "run_attempt": "1",
        "skip_sites": [],
        "skipped_at_collection": 0,
    }
    summary_path = root / ".fast-suite-summary.json"
    canary_path = root / ".fast-suite-canary.json"
    record_path = root / ".full-run-record.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    canary_path.write_text(json.dumps(canary, indent=1), encoding="utf-8")
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return summary_path, canary_path


def _doctored_selection(root, manifest, base_sha, **changes):
    real, _, _ = select_for_change(root, load_manifest(manifest), base_sha)
    return _replace_selection(real, **changes)


def _verify_in_process(root, manifest, summary, canary, base_sha,
                       selection, monkeypatch):
    # Actions identity must not leak a real run's env into synthetic
    # artifacts: locally it is absent, in CI it is present and true.
    for name in ("GITHUB_ACTIONS", "BASE_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        verifier, "select_for_change",
        lambda repo_root, manifest_path, base: (selection, None, None))
    return verifier.main([
        "--summary", str(summary), "--canary", str(canary),
        "--repo-root", str(root), "--manifest", str(manifest),
        "--base-sha", base_sha,
        "--run-record", str(root / ".full-run-record.json")])


def test_an_empty_recomputed_selection_is_refused(tmp_path, rig, monkeypatch,
                                                  capsys):
    """EMPTY-EXPECTED: the recomputed expected set itself may not be empty.

    Unreachable through any manifest a correct selector accepts, so the
    selection seam is patched to exactly the degenerate output the guard
    exists for; the surrounding artifacts are built honest to that output.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    empty = _doctored_selection(root, manifest, base_sha,
                                included=frozenset())
    summary, canary = _artifacts_from_selection(root, plugin, manifest,
                                                empty, base_sha)
    rc = _verify_in_process(root, manifest, summary, canary, base_sha,
                            empty, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "EMPTY-EXPECTED" in captured.err
    assert "VERIFY-OK" not in captured.out


def test_selection_arithmetic_drift_is_refused(tmp_path, rig, monkeypatch,
                                               capsys):
    """SELECTION-ARITHMETIC: included must equal discovered minus excluded.

    One phantom file is added to `included` alone, so the artifacts stay
    self-consistent and only the arithmetic identity is violated.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    drifted = _doctored_selection(
        root, manifest, base_sha,
        included=frozenset({"tests/test_phantom_added.py"}),
    )
    summary, canary = _artifacts_from_selection(root, plugin, manifest,
                                                drifted, base_sha)
    rc = _verify_in_process(root, manifest, summary, canary, base_sha,
                            drifted, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "SELECTION-ARITHMETIC" in captured.err
    assert "VERIFY-OK" not in captured.out


def test_an_unbuildable_selection_is_refused_as_selection_args_invalid(
        tmp_path, rig, monkeypatch, capsys):
    """SELECTION-ARGS-INVALID: a recomputed selection whose excluded set
    names a nonexistent path cannot become pytest arguments (to_pytest_args
    refuses a missing or cwd-escaping exclusion), and the verifier names the
    condition instead of leaking an uncaught ValueError traceback from
    build_argv. In CI the tree cannot change between the run and this
    verify, so the seam guards corrupted recomputation, not a live hazard.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    real, _, _ = select_for_change(root, load_manifest(manifest), base_sha)
    ghost = _replace_selection(
        real, excluded=frozenset({*real.excluded, "tests/test_ghost.py"}))
    # Artifacts are built from the REAL selection: building them from the
    # ghost would raise inside the fixture (to_pytest_args refuses the
    # ghost's missing exclusion before the verifier is ever invoked). The
    # patched recomputation is what _require_commands tries to build from.
    summary, canary = _artifacts_from_selection(
        root, plugin, manifest, real, base_sha)
    rc = _verify_in_process(root, manifest, summary, canary, base_sha,
                            ghost, monkeypatch)
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "SELECTION-ARGS-INVALID" in captured.err
    assert "VERIFY-FAILURE" in captured.err
    assert "test_ghost" in captured.err
    assert "VERIFY-OK" not in captured.out


# ---- the skip advisory ---------------------------------------------------

_LISTED_SITE = {"file": "tests/test_plugin_root_allowlist.py",
                "reason": "the environment cannot hold a NUL", "count": 1}


def _record_with_sites(summary_path, sites):
    """Give both fast summary and record the same structurally valid skips."""
    root = summary_path.parent
    record_path = root / ".full-run-record.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    skipped = sum(site["count"] for site in sites)
    passed = record["collected"] - skipped
    summary_line = f"{passed} passed, {skipped} skipped in 0.01s"
    for artifact in (summary["run"], record):
        artifact["passed"] = passed
        artifact["skipped"] = skipped
        artifact["summary_line"] = summary_line
    record["skip_sites"] = sites
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return record_path


def test_a_listed_skip_site_is_reported_as_admitted(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest,
                run_record=_record_with_sites(summary, [_LISTED_SITE]))
    assert proc.returncode == 0, proc.stderr
    assert "SKIP-ADVISORY-OK" in proc.stdout, proc.stdout


def test_an_unlisted_skip_site_warns_without_failing(tmp_path, rig):
    """The fast lane surfaces what the required gate will refuse, as advice."""
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    unlisted = {"file": "tests/test_new.py", "reason": "brand new skip",
                "count": 1}
    proc = _run(summary, canary, base_sha, root, manifest,
                run_record=_record_with_sites(summary, [unlisted]))
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout
    assert "SKIP-ADVISORY SKIP-UNLISTED" in proc.stderr, proc.stderr
    assert "tests/test_new.py" in proc.stderr


def test_an_over_allowance_skip_site_warns_without_failing(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(
        summary, canary, base_sha, root, manifest,
        run_record=_record_with_sites(summary, [dict(_LISTED_SITE, count=9)]))
    assert proc.returncode == 0, proc.stderr
    assert "SKIP-ADVISORY SKIP-OVER-ALLOWANCE" in proc.stderr, proc.stderr


def test_an_absent_run_record_is_a_hard_failure(tmp_path, rig):
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest,
                run_record=tmp_path / "missing.json")
    assert proc.returncode == 1, proc.stderr
    assert "RECORD-ABSENT" in proc.stderr


def test_the_advisory_follows_the_failure_line_and_never_masks_it(
        tmp_path, rig):
    """On a refused verification the advisory still prints — strictly after.

    The failure line leads and the advisory follows on the same stream, so a
    red-lane author keeps the "this skip will be refused by the full gate"
    hint while masking stays impossible: the advisory can inform but never
    turn a failure green. Control arm first: the same unlisted site with no
    failure is green with the advisory warning present.
    """
    root, plugin, manifest = rig
    base_sha = _git(root, "rev-parse", "HEAD")
    summary, canary, base = _artifacts(root, plugin, manifest)
    unlisted = {"file": "tests/test_new.py", "reason": "brand new skip",
                "count": 1}
    record = _record_with_sites(summary, [unlisted])
    control = _run(summary, canary, base, root, manifest, run_record=record)
    assert control.returncode == 0, control.stderr
    assert "VERIFY-OK" in control.stdout
    assert "SKIP-ADVISORY SKIP-UNLISTED" in control.stderr
    # Failure arm: the same record, canary artifact removed. Still red with
    # the advisory printed, and on the shared stream the failure line comes
    # first — presence after the verdict, never instead of it.
    canary.unlink()
    proc = _run(summary, canary, base, root, manifest, run_record=record)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "VERIFY-FAILURE" in proc.stderr
    assert "SKIP-ADVISORY SKIP-UNLISTED" in proc.stderr
    assert (proc.stderr.index("VERIFY-FAILURE")
            < proc.stderr.index("SKIP-ADVISORY SKIP-UNLISTED"))


def test_the_artifact_paths_have_no_environment_default(tmp_path, rig):
    """Without --summary and --canary the verifier refuses to guess a path."""
    root, plugin, manifest = rig
    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--repo-root", str(root),
         "--manifest", str(manifest), "--base-sha", "0" * 40],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--summary" in proc.stderr and "--canary" in proc.stderr
