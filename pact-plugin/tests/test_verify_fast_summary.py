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
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_fast_summary.py"

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
    if summary_line is None:
        summary_line = f"{len(expected_files)} passed in 0.01s"
    selection = {
        "status": selection_status,
        "select_all_reason": select_all_reason,
        "discovered": len(discovered),
        "excluded": excluded,
        "added_back": added_back,
        "included": len(expected_files),
        "included_files": expected_files,
        "included_sha256": "0" * 64,
    }
    canary = {
        "schema_version": 1,
        "mode": "collect-only",
        "manifest": {"path": "scripts/fast_suite_exclusions.json",
                     "sha256": manifest_sha},
        "base_sha": base_sha,
        "head_sha": base_sha,
        "selection": selection,
        "canary": {
            "command": ["python", "-m", "pytest", "--collect-only", "-q"],
            "ignore_paths": [],
            "rc": 0,
            "summary_line": f"{len(expected_files)} tests collected in 0.01s",
            "expected_files": expected_files,
            "collected_files": expected_files,
            "missing": [],
            "extra": [],
        },
    }
    summary = {
        "schema_version": 1,
        "mode": "run",
        "manifest": {"path": "scripts/fast_suite_exclusions.json",
                     "sha256": manifest_sha},
        "base_sha": base_sha,
        "head_sha": base_sha,
        "selection": selection,
        "run": {
            "command": ["python", "-m", "pytest", "-ra"],
            "rc": 0,
            "summary_line": summary_line,
            "collected": len(expected_files),
            "passed": len(expected_files),
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "deselected": 0,
        },
    }
    summary_path = root / ".fast-suite-summary.json"
    canary_path = root / ".fast-suite-canary.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    canary_path.write_text(json.dumps(canary, indent=1), encoding="utf-8")
    return summary_path, canary_path, base_sha


def _run(summary_path, canary_path, base_sha, root, manifest):
    command = [
        sys.executable, str(VERIFIER),
        "--summary", str(summary_path),
        "--canary", str(canary_path),
        "--repo-root", str(root),
        "--manifest", str(manifest),
    ]
    if base_sha is not None:
        command += ["--base-sha", base_sha]
    env = os.environ.copy()
    env.pop("BASE_SHA", None)
    return subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=120)


def test_the_valid_baseline_is_green(tmp_path, rig):
    """Non-vacuity floor: every bad-input row below starts from this pair."""
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(root, plugin, manifest)
    proc = _run(summary, canary, base_sha, root, manifest)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFY-OK" in proc.stdout


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
    """Third B2 arm: the everyday path — exclusions applied, no add-backs.

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


def test_a_decorated_summary_is_accepted(tmp_path, rig):
    """The CI run form (`-ra`, no `-q`) is `=`-decorated."""
    root, plugin, manifest = rig
    summary, canary, base_sha = _artifacts(
        root, plugin, manifest, summary_line="===== 7 passed in 0.01s =====")
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
    proc = subprocess.run(
        [sys.executable, str(runner), "--collect-only",
         "--base-sha", base_sha, "--repo-root", str(root),
         "--manifest", str(manifest), "--summary", str(summary)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
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