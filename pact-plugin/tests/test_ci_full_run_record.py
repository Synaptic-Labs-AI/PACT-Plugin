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

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import ci_contract
from ci_full_run_record import (
    OWNER_ENV,
    RECORD_NAME,
    FullRunRecord,
    aggregate_worker_collection_counts,
)

PLUGIN = Path(__file__).with_name("ci_full_run_record.py")
COLLECTION_RECORD_NAME = ".full-collection-record.json"

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


NESTING_SUITE = '''\
import os
import subprocess
import sys
from pathlib import Path

RECORD = Path(os.environ["GITHUB_WORKSPACE"]) / "{record_name}"


def test_a_nested_session_runs_while_the_outer_one_is_open():
    plugin_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    if os.environ.get("RIG_NESTED_UNCLAIMED") == "1":
        env.pop("{owner}", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(plugin_root / "inner_case.py")],
        cwd=str(plugin_root), capture_output=True, text=True, env=env,
        timeout=120)
    assert "2 passed" in proc.stdout, proc.stdout + proc.stderr
    expect_written = os.environ.get("RIG_NESTED_UNCLAIMED") == "1"
    assert RECORD.exists() is expect_written, (
        "nested session record state was not the expected one")
'''.replace("{owner}", OWNER_ENV).replace("{record_name}", RECORD_NAME)

INNER_CASE = '''\
def test_inner_one():
    assert True


def test_inner_two():
    assert True
'''

SILENT_REMOVAL_CONFTEST = '''

def pytest_collection_modifyitems(items):
    del items[-1]
'''


def _rig(tmp_path, suite_text, extra_conftest=""):
    root = tmp_path / "rig"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        "{}", encoding="utf-8")
    plugin_root = root / "pact-plugin"
    (plugin_root / "tests").mkdir(parents=True)
    shutil.copy2(PLUGIN, plugin_root / "tests" / PLUGIN.name)
    (plugin_root / "conftest.py").write_text(
        RIG_CONFTEST + extra_conftest, encoding="utf-8")
    (plugin_root / "tests" / "test_sample.py").write_text(
        suite_text, encoding="utf-8")
    workspace = root / "workspace"
    workspace.mkdir()
    return root, plugin_root, workspace


def _run(plugin_root, workspace=None, args=(), gated=True, extra_env=None):
    env = os.environ.copy()
    env.pop("GITHUB_ACTIONS", None)
    env.pop("GITHUB_WORKSPACE", None)
    # The certifying session that runs THIS module has claimed ownership;
    # the rig session is a fresh outermost session and must claim its own.
    env.pop(OWNER_ENV, None)
    env.pop("PYTEST_ADDOPTS", None)
    if gated:
        env["GITHUB_ACTIONS"] = "true"
    if workspace is not None:
        env["GITHUB_WORKSPACE"] = str(workspace)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *args],
        cwd=str(plugin_root), capture_output=True, text=True, env=env,
        timeout=180)
    return proc


def _record(workspace):
    return json.loads((workspace / RECORD_NAME).read_text(encoding="utf-8"))


def _allowlist_for(record):
    """An allowlist admitting exactly the record's own skip sites."""
    return {"schema_version": 1, "about": "rig allowlist", "entries": [
        {"file": site["file"], "reason": site["reason"], "match": "exact",
         "allowed": site["count"]} for site in record["skip_sites"]]
        or [{"file": "tests/none.py", "reason": "none", "match": "exact",
             "allowed": 1}]}


def _verify(root, record_path, collection_count=None, allowlist=None):
    """Run the real verifier against the record and a matching collection record.

    The collection record is written beside the run record with the run's
    own collected count (or `collection_count`), and the allowlist admits
    the record's own skips (or is `allowlist`), so each scenario reaches the
    condition it is about rather than a collection or skip mismatch.
    """
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    allowlist_path = Path(record_path).with_name("skip-allowlist.json")
    allowlist_path.write_text(json.dumps(
        allowlist if allowlist is not None else _allowlist_for(record)),
        encoding="utf-8")
    collection = Path(record_path).with_name(COLLECTION_RECORD_NAME)
    collection.write_text(json.dumps({
        "schema_version": 1,
        "collected_items": (record["collected"] if collection_count is None
                            else collection_count),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }), encoding="utf-8")
    verifier = ci_contract.require_root("the full-run verifier") / "scripts" / "verify_full_run.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), "--record", str(record_path),
         "--collection-record", str(collection),
         "--skip-allowlist", str(allowlist_path)],
        capture_output=True, text=True, timeout=120)
    return proc


def test_a_passing_session_records_skips_and_xfail_xpass(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert not (root / RECORD_NAME).exists(), (
        "the record must land under GITHUB_WORKSPACE, never the repo root")
    assert record["schema_version"] == 3
    # The skip is recorded as the -ra summary prints it: plugin-relative
    # path, verbatim reason, no "Skipped: " prefix. The xfail is not a skip.
    assert record["skip_sites"] == [{
        "file": "tests/test_sample.py", "reason": "legitimate skip",
        "count": 1}], record["skip_sites"]
    assert record["items_before_modifyitems"] == 4
    assert record["items_after_modifyitems"] == 4
    assert record["run_id"] == os.environ.get("GITHUB_RUN_ID")
    assert record["run_attempt"] == os.environ.get("GITHUB_RUN_ATTEMPT")
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

def test_a_collect_only_session_writes_nothing(tmp_path):
    """A collection is not a run, so it never leaves a run record behind."""
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace, ["--collect-only"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (workspace / RECORD_NAME).exists(), (
        "a collect-only session wrote a run record")


def test_a_nested_session_cannot_stand_in_for_the_outer_one(tmp_path):
    """Only the outermost gated session writes; a nested one is silent.

    The rig's single test spawns a nested gated session that inherits the
    whole environment and checks, while the outer session is still open,
    that the nested run left no record. The record that exists afterwards is
    the outer session's: one test, not the nested session's two.
    """
    root, plugin_root, workspace = _rig(tmp_path, NESTING_SUITE)
    (plugin_root / "inner_case.py").write_text(INNER_CASE, encoding="utf-8")
    proc = _run(plugin_root, workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert record["collected"] == 1 and record["passed"] == 1, record
    assert record["terminal_node_ids"] == [
        "tests/test_sample.py::"
        "test_a_nested_session_runs_while_the_outer_one_is_open"], record


def test_an_unclaimed_nested_session_would_write(tmp_path):
    """Counter-arm: without the ownership claim the nested session writes.

    This proves the nested session in the arm above is gated and reaches
    the writer, so its silence there is the ownership claim at work and not
    an inert rig.
    """
    root, plugin_root, workspace = _rig(tmp_path, NESTING_SUITE)
    (plugin_root / "inner_case.py").write_text(INNER_CASE, encoding="utf-8")
    proc = _run(plugin_root, workspace,
                extra_env={"RIG_NESTED_UNCLAIMED": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_unreported_item_removal_fails_the_verifier(tmp_path):
    """A hook that drops an item without deselecting it is caught."""
    root, plugin_root, workspace = _rig(
        tmp_path, PASSING_SUITE, extra_conftest=SILENT_REMOVAL_CONFTEST)
    proc = _run(plugin_root, workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert record["items_before_modifyitems"] == 4, record
    assert record["items_after_modifyitems"] == 3, record
    assert record["deselected"] == 0, record
    assert record["passed"] == 1, record
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 1, verdict.stdout + verdict.stderr
    assert "RUN-VERIFY-FAILURE COLLECTION-HOOK-NARROWING:" in verdict.stderr


# --- xdist aggregation: the policy runs in CI; the seam is probe-only ---


def test_the_aggregate_is_the_pair_every_worker_agrees_on():
    assert aggregate_worker_collection_counts(
        [(4, 4, 0), (4, 4, 0)], 4) == (4, 4, 0)


def test_the_aggregate_is_not_a_sum_of_workers():
    """Every worker collected the whole corpus; a sum would be 16."""
    assert aggregate_worker_collection_counts([(4, 4, 0)] * 4, 4) == (4, 4, 0)


def test_disagreeing_workers_have_no_faithful_aggregate():
    assert aggregate_worker_collection_counts(
        [(4, 4, 0), (4, 3, 0)], 4) is None


def test_an_aggregate_inconsistent_with_collected_is_refused():
    assert aggregate_worker_collection_counts([(4, 4, 0)], 3) is None


def test_the_aggregate_carries_a_narrowed_pair():
    """The identity reads AFTER, not BEFORE or DESELECTED.

    Every other fixture here has before == after and deselected == 0, so a
    check on the wrong element passes them all; this is the only case that
    can tell ``triples[0][1]`` from ``triples[0][0]`` or ``triples[0][2]``.
    The pair is a legitimate deselection (4 taken to 3, one reported), which
    must be RETURNED rather than refused so the verifier can see it.
    """
    assert aggregate_worker_collection_counts([(4, 3, 1)], 3) == (4, 3, 1)


def test_no_worker_reports_have_no_aggregate():
    assert aggregate_worker_collection_counts([], 4) is None
    assert aggregate_worker_collection_counts([None], 4) is None


def test_a_non_integer_pair_value_has_no_aggregate():
    """float and bool are the shapes the identity check cannot reject.

    ``4.0 == 4`` and ``True == 1``, so without the type guard each would be
    returned as a faithful triple. A None entry would be caught downstream by
    the collected check and so would not exercise this guard. The third
    element is asserted separately: a guard that checked only before/after
    would miss it.
    """
    assert aggregate_worker_collection_counts([(4.0, 4.0, 0)], 4) is None
    assert aggregate_worker_collection_counts([(True, True, 0)], 1) is None
    assert aggregate_worker_collection_counts([(4, 4, 0.0)], 4) is None


def test_a_non_integer_collected_has_no_aggregate():
    assert aggregate_worker_collection_counts([(4, 4, 0)], 4.0) is None
    assert aggregate_worker_collection_counts([(1, 1, 0)], True) is None


def test_a_malformed_pair_has_no_aggregate():
    """A triple of the wrong length is refused, not unpacked."""
    assert aggregate_worker_collection_counts([(4, 3)], 3) is None
    assert aggregate_worker_collection_counts([(4, 4, 0, 0)], 4) is None


def test_the_recorder_counts_deselected_items_from_the_hook(monkeypatch):
    """The published deselected count comes from pytest_deselected.

    Under xdist the controller never collects, so its own stats report 0 and
    a controller-sourced count would be a number nobody measured. This is
    the only CI-visible pin on the source: the hook fires in the session
    that deselected, and the triple carries what it counted.
    """
    monkeypatch.setattr("ci_full_run_record._gated_repo_root", lambda: None)
    record = FullRunRecord()
    record.pytest_deselected(["a", "b", "c"])
    record.pytest_deselected(["d"])
    assert record._deselected == 4


def test_xdist_workers_agree_and_the_record_verifies(tmp_path):
    """PROBE-ONLY COVERAGE: skips until pytest-xdist is in the CI pins.

    The agreement rule itself is covered in CI by the pure-function tests
    above. This test exercises the real workeroutput/testnodedown seam, and
    it deliberately skips rather than mocking that seam: a skip is not a
    pass, and the probe arms in the HANDOFF are the evidence until the pin
    lands.
    """
    pytest.importorskip("xdist")
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    proc = _run(plugin_root, workspace,
                args=("-n", "2", "--dist", "loadfile"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert record["items_before_modifyitems"] == 4, record
    assert record["items_after_modifyitems"] == 4, record
    assert record["collected"] == 4, record
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    assert "FULL-RUN-OK" in verdict.stdout, verdict.stdout


def test_xdist_narrowing_reaches_the_narrowing_check(tmp_path):
    """PROBE-ONLY COVERAGE (see the test above): the negative arm.

    An xdist run whose collection hook silently drops an item must fail on
    COLLECTION-HOOK-NARROWING, not on an earlier condition. The assertion on
    the reason token is the point: an arm that reds for the wrong reason
    proves nothing about this check.
    """
    pytest.importorskip("xdist")
    root, plugin_root, workspace = _rig(
        tmp_path, PASSING_SUITE, extra_conftest=SILENT_REMOVAL_CONFTEST)
    proc = _run(plugin_root, workspace,
                args=("-n", "2", "--dist", "loadfile"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert record["items_before_modifyitems"] == 4, record
    assert record["items_after_modifyitems"] == 3, record
    assert record["deselected"] == 0, record
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 1, verdict.stdout + verdict.stderr
    assert "RUN-VERIFY-FAILURE COLLECTION-HOOK-NARROWING:" in verdict.stderr, (
        verdict.stderr)


MODULE_SKIP_SUITE = '''\
import pytest

pytest.skip("module unavailable here", allow_module_level=True)


def test_never():
    assert True
'''


def _shifted(suite_text, lines):
    """The same suite with blank lines above it, so every skip moves."""
    return "\n" * lines + suite_text


def test_a_moved_skip_records_the_same_site_and_still_verifies(tmp_path):
    """The allowlist key carries no line number, so moving code is harmless."""
    root, plugin_root, workspace = _rig(tmp_path / "a", PASSING_SUITE)
    assert _run(plugin_root, workspace).returncode == 0
    before = _record(workspace)
    allowlist = _allowlist_for(before)
    root2, plugin_root2, workspace2 = _rig(
        tmp_path / "b", _shifted(PASSING_SUITE, 40))
    proc = _run(plugin_root2, workspace2)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = _record(workspace2)
    assert after["skip_sites"] == before["skip_sites"]
    verdict = _verify(root2, workspace2 / RECORD_NAME, allowlist=allowlist)
    assert verdict.returncode == 0, verdict.stderr


def test_an_unlisted_skip_fails_the_verifier_by_name(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    assert _run(plugin_root, workspace).returncode == 0
    allowlist = {"schema_version": 1, "entries": [{
        "file": "tests/test_sample.py", "reason": "some other reason",
        "match": "exact", "allowed": 1}]}
    verdict = _verify(root, workspace / RECORD_NAME, allowlist=allowlist)
    assert verdict.returncode == 1
    assert "RUN-VERIFY-FAILURE SKIP-UNLISTED:" in verdict.stderr
    assert "legitimate skip" in verdict.stderr


def test_a_module_level_skip_is_recorded_as_a_site(tmp_path):
    root, plugin_root, workspace = _rig(tmp_path, PASSING_SUITE)
    (plugin_root / "tests" / "test_module_skip.py").write_text(
        MODULE_SKIP_SUITE, encoding="utf-8")
    proc = _run(plugin_root, workspace, ["-ra"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = _record(workspace)
    assert {"file": "tests/test_module_skip.py",
            "reason": "module unavailable here",
            "count": 1} in record["skip_sites"], record["skip_sites"]
    assert sum(site["count"] for site in record["skip_sites"]) == (
        record["skipped"]), record
    # The recorded site is the one -ra prints.
    assert "tests/test_module_skip.py:3: module unavailable here" in (
        proc.stdout), proc.stdout
    assert record["skipped_at_collection"] == 1, record
    verdict = _verify(root, workspace / RECORD_NAME)
    assert verdict.returncode == 0, verdict.stderr


def test_the_verifier_and_the_recorder_name_the_same_record_file():
    """One file name, two homes: scripts/ cannot import from tests/.

    The recorder's constant is the source of truth; the verifier's copy is
    pinned to it here, so changing one without the other reddens.
    """
    verifier = ci_contract.require_root("the full-run verifier") / "scripts" / "verify_full_run.py"
    spec = importlib.util.spec_from_file_location("verify_full_run", verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RECORD_NAME == RECORD_NAME, (module.RECORD_NAME, RECORD_NAME)


def test_the_gitignore_lists_the_record_file():
    """A run record must never be committed, so .gitignore names it exactly."""
    root = ci_contract.require_root("the gitignore record entry")
    lines = [line.strip() for line in
             (root / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert RECORD_NAME in lines, (
        f".gitignore has no line {RECORD_NAME!r}; a renamed record would be "
        f"committable")
