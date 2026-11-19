"""Real fast-runner/xdist/record/verifier seams, with matched valid controls.

Every rig is a fresh local git checkout with no remote. The runner and verifier
are the production scripts; the recorder is copied unchanged. xdist is a hard
test dependency here: an environment without it must fail, never skip the seam.
"""

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import xdist  # noqa: F401  # required: missing xdist must fail these real seams

from ci_full_run_record import OWNER_ENV, RECORD_NAME
from helpers import env_without_pytest_vars
from test_ci_full_run_record import (
    LATE_WORKER_INTERNAL_ERROR_CONFTEST,
    PASSING_SUITE,
    RIG_CONFTEST,
)
from test_verify_fast_summary import BASELINE, GUARD_FILES, SELECTION_MACHINERY

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_fast_suite.py"
VERIFIER = ROOT / "scripts" / "verify_fast_summary.py"
RECORDER = Path(__file__).with_name("ci_full_run_record.py")

PARAM_SUITE = '''\
import pytest


@pytest.mark.parametrize("value", [0, 1])
def test_param(value):
    assert value in (0, 1)
'''

MODULE_SKIP_SUITE = '''\
import pytest

pytest.skip("module unavailable in this rig", allow_module_level=True)


def test_never_collected():
    assert True
'''


def _write(path, text="def test_ok():\n    assert True\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def _rig(tmp_path, suites=None, extra_conftest=""):
    root = tmp_path / "rig"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        "{}", encoding="utf-8")
    plugin = root / "pact-plugin"
    exclusions = [f"tests/test_slow_{index}.py" for index in range(24)]
    for relative in list(GUARD_FILES) + exclusions:
        _write(plugin / relative)

    entries = []
    for index, relative in enumerate(exclusions):
        seam = f"hooks/seam_{index}.py"
        _write(plugin / seam)
        entries.append({
            "path": relative,
            "displayed_seconds": 1.0,
            "tests": 1,
            "mechanism": "real xdist rig exclusion",
            "production_seams": [seam],
        })
    for relative in SELECTION_MACHINERY:
        _write(root / relative, "")
    manifest = root / "scripts" / "fast_suite_exclusions.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "path_base": "plugin_root",
        "baseline": BASELINE,
        "selection_machinery": list(SELECTION_MACHINERY),
        "value_inert_keys": {},
        "reach_reviews": {},
        "guard_files": list(GUARD_FILES),
        "exclusions": entries,
    }, indent=2), encoding="utf-8")

    selected_suites = {
        "tests/test_alpha.py": PASSING_SUITE,
        "tests/test_beta.py": PARAM_SUITE,
        **(suites or {}),
    }
    for relative, source in selected_suites.items():
        _write(plugin / relative, source)
    shutil.copy2(RECORDER, plugin / "tests" / RECORDER.name)
    (plugin / "conftest.py").write_text(
        RIG_CONFTEST + extra_conftest, encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "rig")
    workspace = root / "workspace"
    workspace.mkdir()

    env = env_without_pytest_vars()
    env.pop(OWNER_ENV, None)
    env.update({
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_RUN_ID": uuid.uuid4().hex,
        "GITHUB_RUN_ATTEMPT": "1",
    })
    return SimpleNamespace(
        root=root,
        plugin=plugin,
        manifest=manifest,
        workspace=workspace,
        summary=workspace / ".fast-suite-summary.json",
        canary=workspace / ".fast-suite-canary.json",
        record=workspace / RECORD_NAME,
        base_sha=_git(root, "rev-parse", "HEAD"),
        env=env,
    )


def _runner(rig, collect_only=False):
    command = [
        sys.executable, str(RUNNER),
        "--base-sha", rig.base_sha,
        "--repo-root", str(rig.root),
        "--manifest", str(rig.manifest),
        "--summary", str(rig.summary),
        "--canary", str(rig.canary),
    ]
    if collect_only:
        command.insert(2, "--collect-only")
    return subprocess.run(
        command, capture_output=True, text=True, env=rig.env, timeout=180)


def _verify(rig):
    return subprocess.run([
        sys.executable, str(VERIFIER),
        "--base-sha", rig.base_sha,
        "--repo-root", str(rig.root),
        "--manifest", str(rig.manifest),
        "--summary", str(rig.summary),
        "--canary", str(rig.canary),
        "--run-record", str(rig.record),
    ], capture_output=True, text=True, env=rig.env, timeout=120)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_valid(rig):
    canary_proc = _runner(rig, collect_only=True)
    assert canary_proc.returncode == 0, canary_proc.stdout + canary_proc.stderr
    run_proc = _runner(rig)
    assert run_proc.returncode == 0, run_proc.stdout + run_proc.stderr
    verdict = _verify(rig)
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    assert "VERIFY-OK" in verdict.stdout
    summary = _read(rig.summary)
    canary = _read(rig.canary)
    record = _read(rig.record)
    assert summary["run"]["command"][-4:] == ["-n", "4", "--dist", "loadfile"]
    assert "-n" not in canary["canary"]["command"]
    assert len(record["terminal_node_ids"]) == record["collected"]
    assert set(record["terminal_node_ids"]) == set(
        canary["canary"]["collected_node_ids"])
    return summary, canary, record, verdict


def test_real_distributed_skip_and_xfail_evidence_verifies(tmp_path):
    rig = _rig(tmp_path)
    summary, canary, record, verdict = _assert_valid(rig)
    assert (summary["run"]["skipped"], summary["run"]["xfailed"],
            summary["run"]["xpassed"]) == (1, 1, 1)
    assert (record["skipped"], record["xfailed"], record["xpassed"]) == (1, 1, 1)
    assert record["items_before_modifyitems"] == record["collected"]
    assert record["items_after_modifyitems"] == record["collected"]
    assert record["deselected"] == 0
    assert summary["run_id"] == canary["run_id"] == record["run_id"]
    assert summary["run_attempt"] == canary["run_attempt"] == record["run_attempt"]
    assert "SKIP-ADVISORY" in verdict.stderr


def test_real_distributed_module_skip_reconciles_without_a_node_id(tmp_path):
    rig = _rig(tmp_path, suites={"tests/test_alpha.py": MODULE_SKIP_SUITE})
    _, canary, record, _ = _assert_valid(rig)
    assert record["skipped"] == 1
    assert record["skipped_at_collection"] == 1
    assert not any(node_id.startswith("tests/test_alpha.py")
                   for node_id in canary["canary"]["collected_node_ids"])
    assert not any(node_id.startswith("tests/test_alpha.py")
                   for node_id in record["terminal_node_ids"])


def test_real_run_only_same_count_node_mutation_is_refused(tmp_path):
    rig = _rig(tmp_path)
    _assert_valid(rig)  # matched unmodified positive control
    with (rig.plugin / "conftest.py").open("a", encoding="utf-8") as conftest:
        # Mutate only the controller's deserialized terminal report. Collection
        # and xdist's worker-side node-ID assertion stay valid, while every
        # terminal report carries the same changed ID to the recorder.
        conftest.write('''\n\nimport pytest


@pytest.hookimpl(wrapper=True)
def pytest_report_from_serializable(config, data):
    report = yield
    if report.nodeid.endswith("test_beta.py::test_param[0]"):
        report.nodeid = report.nodeid.replace(
            "test_param[0]", "test_run_only[0]")
    return report
''')
    run_proc = _runner(rig)
    assert run_proc.returncode == 0, run_proc.stdout + run_proc.stderr
    record = _read(rig.record)
    assert any(node_id.endswith("test_beta.py::test_run_only[0]")
               for node_id in record["terminal_node_ids"])
    verdict = _verify(rig)
    assert verdict.returncode == 1, verdict.stdout + verdict.stderr
    assert "NODE-ID-SET-MISMATCH" in verdict.stderr


def test_real_late_worker_loss_with_exit_zero_is_refused(tmp_path):
    control = _rig(tmp_path / "control")
    _, _, control_record, _ = _assert_valid(control)

    rig = _rig(
        tmp_path / "late",
        extra_conftest=LATE_WORKER_INTERNAL_ERROR_CONFTEST)
    canary_proc = _runner(rig, collect_only=True)
    assert canary_proc.returncode == 0, canary_proc.stdout + canary_proc.stderr
    run_proc = _runner(rig)
    output = run_proc.stdout + run_proc.stderr
    assert run_proc.returncode == 0, output
    assert "internal error on gw1 after its tests" in output
    assert "LATE-WORKER-SYNCHRONIZATION-FAILURE" not in output
    record = _read(rig.record)
    assert record["exitstatus"] == 0
    assert record["passed"] == control_record["passed"]
    assert record["items_before_modifyitems"] is None
    assert record["items_after_modifyitems"] is None
    assert record["deselected"] is None
    verdict = _verify(rig)
    assert verdict.returncode == 1, verdict.stdout + verdict.stderr
    assert "BAD-FIELD-DESELECTED" in verdict.stderr
