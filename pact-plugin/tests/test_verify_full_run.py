"""Every named failure in scripts/verify_full_run.py is reachable.

The verifier is the required gate's last word on the certifying run, so a
condition no test can reach is a condition whose deletion nothing notices.
Each arm below starts from a crafted record pair that verifies green, breaks
exactly one thing, runs the real script, and asserts the condition NAME it
reports. The green baseline runs first, so a broken rig cannot pass every
arm by failing for an unrelated reason.

The verifier runs with a controlled environment: the GITHUB_* identity of
the job running THIS suite is removed, and the identity arms set their own.
"""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_full_run.py"

_IDENTITY_ENV = ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                 "GITHUB_WORKSPACE")
_ACTIONS_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "4242",
                "GITHUB_RUN_ATTEMPT": "1"}


def _skip_ceiling():
    spec = importlib.util.spec_from_file_location("verify_full_run", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SKIP_CEILING


def _good_record():
    node_ids = [f"tests/test_x.py::test_{index}" for index in range(5)]
    return {
        "schema_version": 2,
        "collected": 5,
        "passed": 4,
        "skipped": 1,
        "failed": 0,
        "errors": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
        "exitstatus": 0,
        "summary_line": "4 passed, 1 skipped in 0.10s",
        "terminal_node_ids": node_ids,
        "items_before_modifyitems": 5,
        "items_after_modifyitems": 5,
        "run_id": None,
        "run_attempt": None,
        "owner_pid": 1,
    }


def _good_collection():
    return {"schema_version": 1, "collected_items": 5, "run_id": None,
            "run_attempt": None}


def _with_identity(document):
    document = copy.deepcopy(document)
    document["run_id"] = _ACTIONS_ENV["GITHUB_RUN_ID"]
    document["run_attempt"] = _ACTIONS_ENV["GITHUB_RUN_ATTEMPT"]
    return document


def _write(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(record_path=None, collection_path=None, env_overrides=None):
    assert VERIFIER.is_file(), f"{VERIFIER} is missing"
    env = os.environ.copy()
    for name in _IDENTITY_ENV:
        env.pop(name, None)
    env.update(env_overrides or {})
    argv = [sys.executable, str(VERIFIER)]
    if record_path is not None:
        argv += ["--record", str(record_path)]
    if collection_path is not None:
        argv += ["--collection-record", str(collection_path)]
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=60)


def _pair(tmp_path, record, collection):
    return (_write(tmp_path / "run.json", record),
            _write(tmp_path / "collection.json", collection))


def test_the_crafted_pair_is_green(tmp_path):
    proc = _run(*_pair(tmp_path, _good_record(), _good_collection()))
    assert proc.returncode == 0, proc.stderr
    assert "FULL-RUN-OK" in proc.stdout


def test_the_crafted_pair_is_green_under_actions_with_this_runs_identity(
        tmp_path):
    proc = _run(*_pair(tmp_path, _with_identity(_good_record()),
                       _with_identity(_good_collection())),
                env_overrides=_ACTIONS_ENV)
    assert proc.returncode == 0, proc.stderr


def test_skips_at_the_ceiling_still_certify(tmp_path):
    ceiling = _skip_ceiling()
    record = _good_record()
    record.update(collected=ceiling + 1, passed=1, skipped=ceiling,
                  items_before_modifyitems=ceiling + 1,
                  items_after_modifyitems=ceiling + 1,
                  terminal_node_ids=[f"t::{index}" for index in range(ceiling + 1)])
    collection = _good_collection()
    collection["collected_items"] = ceiling + 1
    proc = _run(*_pair(tmp_path, record, collection))
    assert proc.returncode == 0, proc.stderr


def _set(**fields):
    def mutate(document):
        document.update(fields)
    return mutate


def _drop(key):
    def mutate(document):
        del document[key]
    return mutate


def _over_ceiling(document):
    ceiling = _skip_ceiling()
    total = ceiling + 2
    document.update(collected=total, passed=1, skipped=ceiling + 1,
                    items_before_modifyitems=total,
                    items_after_modifyitems=total,
                    terminal_node_ids=[f"t::{index}" for index in range(total)])


_CASES = [
    # (case id, record mutation, collection mutation, expected condition)
    ("schema", _set(schema_version=1), None, "SCHEMA-VERSION"),
    ("key-missing", _drop("items_before_modifyitems"), None, "KEY-MISSING"),
    ("no-summary", _set(summary_line="  "), None, "NO-SUMMARY"),
    ("bad-collected", _set(collected="5"), None, "BAD-FIELD-COLLECTED"),
    ("collected-zero", _set(collected=0), None, "COLLECTED"),
    ("deselected", _set(deselected=1), None, "DESELECTED"),
    ("failed", _set(failed=1, passed=3), None, "FAILED"),
    ("errors", _set(errors=1, passed=3), None, "ERRORS"),
    ("nothing-passed", _set(passed=0, skipped=5), None, "NOTHING-PASSED"),
    ("skip-ceiling", _over_ceiling, None, "SKIP-CEILING"),
    ("hook-narrowing", _set(items_before_modifyitems=6), None,
     "COLLECTION-HOOK-NARROWING"),
    ("terminal-type", _set(terminal_node_ids="t::1"), None, "TERMINAL-SET"),
    ("accounting", _set(passed=3), None, "ACCOUNTING"),
    ("terminal-length", _set(terminal_node_ids=["t::1", "t::2"]), None,
     "TERMINAL-SET"),
    ("terminal-duplicates",
     _set(terminal_node_ids=["t::1", "t::1", "t::2", "t::3", "t::4"]), None,
     "TERMINAL-SET"),
    ("collection-bad", None, _set(collected_items="5"),
     "COLLECTION-RECORD-BAD"),
    ("collection-mismatch", None, _set(collected_items=6),
     "COLLECTION-COUNT-MISMATCH"),
]


@pytest.mark.parametrize(
    "case_id,mutate_record,mutate_collection,expected",
    _CASES, ids=[case[0] for case in _CASES])
def test_each_named_failure_is_reachable(
        tmp_path, case_id, mutate_record, mutate_collection, expected):
    record, collection = _good_record(), _good_collection()
    if mutate_record is not None:
        mutate_record(record)
    if mutate_collection is not None:
        mutate_collection(collection)
    proc = _run(*_pair(tmp_path, record, collection))
    assert proc.returncode == 1, (case_id, proc.stdout, proc.stderr)
    assert f"RUN-VERIFY-FAILURE {expected}:" in proc.stderr, (
        case_id, proc.stderr)


def test_an_absent_run_record_is_a_failure(tmp_path):
    collection = _write(tmp_path / "collection.json", _good_collection())
    proc = _run(tmp_path / "missing.json", collection)
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE RECORD-ABSENT:" in proc.stderr


def test_an_unparseable_run_record_is_a_failure(tmp_path):
    record = tmp_path / "run.json"
    record.write_text("{not json", encoding="utf-8")
    collection = _write(tmp_path / "collection.json", _good_collection())
    proc = _run(record, collection)
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE RECORD-UNPARSEABLE:" in proc.stderr


def test_no_record_path_and_no_workspace_is_a_failure(tmp_path):
    collection = _write(tmp_path / "collection.json", _good_collection())
    proc = _run(None, collection)
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE RECORD-PATH-UNAVAILABLE:" in proc.stderr


def test_an_absent_collection_record_file_is_a_failure(tmp_path):
    record = _write(tmp_path / "run.json", _good_record())
    proc = _run(record, tmp_path / "missing-collection.json")
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE COLLECTION-RECORD-ABSENT:" in proc.stderr


def test_no_collection_record_argument_is_a_failure(tmp_path):
    record = _write(tmp_path / "run.json", _good_record())
    proc = _run(record, None)
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE COLLECTION-RECORD-ABSENT:" in proc.stderr


@pytest.mark.parametrize("which", ["run", "collection"])
def test_a_record_from_another_run_is_refused_under_actions(tmp_path, which):
    record = _with_identity(_good_record())
    collection = _with_identity(_good_collection())
    target = record if which == "run" else collection
    target["run_id"] = "9999"
    proc = _run(*_pair(tmp_path, record, collection),
                env_overrides=_ACTIONS_ENV)
    assert proc.returncode == 1, proc.stderr
    assert "RUN-VERIFY-FAILURE RUN-IDENTITY:" in proc.stderr
    assert ("run record" if which == "run" else "collection record") in (
        proc.stderr)


def test_a_record_with_no_identity_is_refused_under_actions(tmp_path):
    proc = _run(*_pair(tmp_path, _good_record(), _good_collection()),
                env_overrides=_ACTIONS_ENV)
    assert proc.returncode == 1
    assert "RUN-VERIFY-FAILURE RUN-IDENTITY:" in proc.stderr
