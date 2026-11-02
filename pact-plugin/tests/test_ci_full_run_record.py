"""The in-band record must be the certifying run's true account.

Each scenario runs a REAL pytest session in a rig checkout that carries the
plugin module and a marker, then reads the record the plugin wrote. The
record is checked field by field against the run's known outcome, and the
real ``scripts/verify_full_run.py`` is run against it so the record's schema
is tested where it is consumed, not only where it is produced.

The deletion proof for the record is the same as for the rest of the lane:
a record that exists only on success cannot evidence a failure, so the
failing-session scenario asserts the file still exists.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import ci_contract

PLUGIN = Path(__file__).with_name("ci_full_run_record.py")
RECORD_NAME = ".pytest-run-record.json"

PASSING_SUITE = '''\
import pytest


def test_pass():
    assert True


def test_skip():
    pytest.skip("legitimate skip")


@pytest.mark.xfail
def test_xfail():
    assert False


@pytest.mark.xfail
def test_xpass():
    assert True
'''

FAILING_SUITE = '''\
def test_fail():
    assert False
'''

RIG_CONFTEST = '''\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests"))
from ci_full_run_record import FullRunRecord


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("full-run-record"):
        config.pluginmanager.register(FullRunRecord(), "full-run-record")
'''


def _rig(tmp_path, suite_text):
    root = tmp_path / "rig"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        "{}", encoding="utf-8")
    plugin_root = root / "pact-plugin"
    (plugin_root / "tests").mkdir(parents=True)
    shutil.copy2(PLUGIN, plugin_root / "tests" / PLUGIN.name)
    (plugin_root / "conftest.py").write_text(RIG_CONFTEST, encoding="utf-8")
    (plugin_root / "tests" / "test_sample.py").write_text(
        suite_text, encoding="utf-8")
    workspace = root / "workspace"
    workspace.mkdir()
    return root, plugin_root, workspace


def _run(plugin_root, workspace=None, args=(), gated=True):
    env = os.environ.copy()
    env.pop("GITHUB_ACTIONS", None)
    env.pop("GITHUB_WORKSPACE", None)
    if gated:
        env["GITHUB_ACTIONS"] = "true"
    if workspace is not None:
        env["GITHUB_WORKSPACE"] = str(workspace)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *args],
        cwd=str(plugin_root), capture_output=True, text=True, env=env,
        timeout=180)
    return proc


def _record(workspace):
    return json.loads((workspace / RECORD_NAME).read_text(encoding="utf-8"))


def _verify(root, record_path):
    verifier = ci_contract.require_root("the full-run verifier") / "scripts" / "verify_full_run.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), "--record", str(record_path)],
        capture_output=True, text=True, timeout=120)
    return proc


def test_a_passing_session_records_skips_and_xfail_xpass(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert not (root / RECORD_NAME).exists(), (
        "the record must land under GITHUB_WORKSPACE, never the repo root")
    assert record["schema_version"] == 1
    assert record["collected"] == 4
    assert record["passed"] == 1
    assert record["skipped"] == 1
    assert record["xfailed"] == 1
    assert record["xpassed"] == 1
    assert record["failed"] == 0
    assert record["errors"] == 0
    assert record["deselected"] == 0
    assert record["exitstatus"] == 0
    assert len(record["terminal_node_ids"]) == record["collected"]
    assert len(set(record["terminal_node_ids"])) == record["collected"]
    # The summary line is the terminal source, not a fabricated string: the
    # counts part appears in the run's own output.
    assert record["summary_line"]
    counts_part = record["summary_line"].split(" in ")[0]
    assert counts_part in proc.stdout
    # ci_contract's parser is used here so it is exercised by a real summary.
    assert ci_contract.summary_line(record["summary_line"]) == record["summary_line"]
    parsed = ci_contract.summary_counts(record["summary_line"])
    assert parsed["passed"] == 1 and parsed["skipped"] == 1
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 0, verdict.stderr
    assert "FULL-RUN-OK" in verdict.stdout


def test_a_failing_session_still_writes_the_record(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, FAILING_SUITE)
    proc = _run(plugin_root, workspace)
    assert proc.returncode != 0
    record = _record(workspace)
    assert record["failed"] == 1
    assert record["exitstatus"] != 0
    assert len(record["terminal_node_ids"]) == record["collected"] == 1
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 1
    assert "RUN-VERIFY-FAILURE FAILED:" in verdict.stderr


def test_deselection_is_read_from_terminal_stats_not_the_item_list(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace, ["-k", "test_pass or test_skip"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert record["deselected"] == 2, record
    assert record["collected"] == 2
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 1
    assert "RUN-VERIFY-FAILURE DESELECTED:" in verdict.stderr


def test_an_ungated_local_run_writes_nothing(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace, gated=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (workspace / RECORD_NAME).exists(), (
        "a run with GITHUB_ACTIONS unset wrote a record; ordinary local runs "
        "must write nothing")
    assert not (root / RECORD_NAME).exists()


def test_a_gated_run_without_a_workspace_writes_nothing_and_says_so(tmp_path):
    root, plugin_root, _ = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace=None, gated=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (root / RECORD_NAME).exists(), (
        "a gated run with no GITHUB_WORKSPACE must not fall back to the repo "
        "root")
    assert "GITHUB_WORKSPACE is empty" in proc.stderr