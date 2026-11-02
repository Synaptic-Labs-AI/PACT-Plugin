"""Every named failure in scripts/verify_full_run.py is reachable.

The verifier is the required gate's last word on the certifying run, so a
condition no test can reach is a condition whose deletion nothing notices.
Each arm below starts from a crafted record pair that verifies green, breaks
exactly one thing, runs the real script, and asserts the condition NAME it
reports. The green baseline runs first, so a broken rig cannot pass every
arm by failing for an unrelated reason. Every arm keeps the collection count
equal to the run's, so no arm depends on the order of the checks.

The verifier runs with a controlled environment: the GITHUB_* identity of
the job running THIS suite is removed, and the identity arms set their own.
Each arm passes its own skip allowlist, so the committed allowlist can
change without moving these arms; the committed file is checked separately.
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
COMMITTED_ALLOWLIST = ROOT / "scripts" / "full_suite_skip_allowlist.json"

_IDENTITY_ENV = ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                 "GITHUB_WORKSPACE")
_ACTIONS_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "4242",
                "GITHUB_RUN_ATTEMPT": "1"}
_SITE = {"file": "tests/test_x.py", "reason": "needs a live tmux", "count": 1}


def _verifier_module():
    spec = importlib.util.spec_from_file_location("verify_full_run", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _good_record():
    node_ids = [f"tests/test_x.py::test_{index}" for index in range(5)]
    return {
        "schema_version": 3,
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
        "skip_sites": [dict(_SITE)],
        "skipped_at_collection": 0,
    }


def _good_collection():
    return {"schema_version": 1, "collected_items": 5, "run_id": None,
            "run_attempt": None}


def _entry(file=_SITE["file"], reason=_SITE["reason"], match="exact",
           allowed=1):
    return {"file": file, "reason": reason, "match": match,
            "allowed": allowed}


def _allowlist(*entries):
    return {"schema_version": 1, "about": "test allowlist",
            "entries": list(entries) or [_entry()]}


def _with_identity(document):
    document = copy.deepcopy(document)
    document["run_id"] = _ACTIONS_ENV["GITHUB_RUN_ID"]
    document["run_attempt"] = _ACTIONS_ENV["GITHUB_RUN_ATTEMPT"]
    return document


def _write(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(record_path=None, collection_path=None, env_overrides=None,
         allowlist_path=None):
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
    if allowlist_path is not None:
        argv += ["--skip-allowlist", str(allowlist_path)]
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=60)


def _verify(tmp_path, record, collection=None, allowlist=None,
            env_overrides=None):
    return _run(_write(tmp_path / "run.json", record),
                _write(tmp_path / "collection.json",
                       collection or _good_collection()),
                env_overrides,
                _write(tmp_path / "allowlist.json",
                       allowlist or _allowlist()))


def _failure(proc, expected):
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert f"RUN-VERIFY-FAILURE {expected}:" in proc.stderr, proc.stderr


def test_the_crafted_pair_is_green(tmp_path):
    proc = _verify(tmp_path, _good_record())
    assert proc.returncode == 0, proc.stderr
    assert "FULL-RUN-OK" in proc.stdout


def test_the_crafted_pair_is_green_under_actions_with_this_runs_identity(
        tmp_path):
    proc = _verify(tmp_path, _with_identity(_good_record()),
                   _with_identity(_good_collection()),
                   env_overrides=_ACTIONS_ENV)
    assert proc.returncode == 0, proc.stderr


def _set(**fields):
    def mutate(document):
        document.update(fields)
    return mutate


def _drop(key):
    def mutate(document):
        del document[key]
    return mutate


_CASES = [
    # (case id, record mutation, collection mutation, expected condition)
    ("schema", _set(schema_version=2), None, "SCHEMA-VERSION"),
    ("key-missing", _drop("items_before_modifyitems"), None, "KEY-MISSING"),
    ("skip-sites-missing", _drop("skip_sites"), None, "KEY-MISSING"),
    ("no-summary", _set(summary_line="  "), None, "NO-SUMMARY"),
    ("bad-collected", _set(collected="5"), None, "BAD-FIELD-COLLECTED"),
    ("collected-zero", _set(collected=0), None, "COLLECTED"),
    ("deselected", _set(deselected=1), None, "DESELECTED"),
    ("failed", _set(failed=1, passed=3), None, "FAILED"),
    ("errors", _set(errors=1, passed=3), None, "ERRORS"),
    ("nothing-passed", _set(passed=0, skipped=5), None, "NOTHING-PASSED"),
    ("skip-sites-bad", _set(skip_sites=[{"file": "f", "reason": "r",
                                         "count": 0}]), None,
     "SKIP-SITES-BAD"),
    ("skip-sites-incomplete", _set(skip_sites=[]), None,
     "SKIP-SITES-INCOMPLETE"),
    ("hook-narrowing", _set(items_before_modifyitems=6), None,
     "COLLECTION-HOOK-NARROWING"),
    ("after-differs-from-collected",
     _set(items_before_modifyitems=6, items_after_modifyitems=6), None,
     "COLLECTION-HOOK-NARROWING"),
    ("accounting", _set(passed=3), None, "ACCOUNTING"),
    ("collection-skips-out-of-range", _set(skipped_at_collection=2), None,
     "BAD-FIELD-SKIPPED_AT_COLLECTION"),
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
    _failure(_verify(tmp_path, record, collection), expected)


def test_a_non_string_terminal_id_is_a_type_failure_not_a_length_one(
        tmp_path):
    """Five entries, so the length identity holds and only the type check fires."""
    record = _good_record()
    record["terminal_node_ids"] = ["t::1", "t::2", 3, "t::4", "t::5"]
    proc = _verify(tmp_path, record)
    _failure(proc, "TERMINAL-SET")
    assert "must be a list of strings" in proc.stderr, proc.stderr


def test_a_non_object_record_is_unparseable(tmp_path):
    record = tmp_path / "run.json"
    record.write_text("[1, 2, 3]", encoding="utf-8")
    proc = _run(record, _write(tmp_path / "c.json", _good_collection()),
                allowlist_path=_write(tmp_path / "a.json", _allowlist()))
    _failure(proc, "RECORD-UNPARSEABLE")
    assert "not a JSON object" in proc.stderr, proc.stderr


# ---- the skip allowlist ---------------------------------------------------


def test_a_listed_skip_site_certifies(tmp_path):
    proc = _verify(tmp_path, _good_record(), allowlist=_allowlist(_entry()))
    assert proc.returncode == 0, proc.stderr


def test_an_unlisted_skip_site_fails_and_is_named(tmp_path):
    allowlist = _allowlist(_entry(reason="a different reason"))
    proc = _verify(tmp_path, _good_record(), allowlist=allowlist)
    _failure(proc, "SKIP-UNLISTED")
    assert _SITE["file"] in proc.stderr and _SITE["reason"] in proc.stderr


def test_more_skips_than_allowed_fails_and_is_named(tmp_path):
    record = _good_record()
    record.update(passed=3, skipped=2,
                  skip_sites=[dict(_SITE, count=2)])
    proc = _verify(tmp_path, record, allowlist=_allowlist(_entry(allowed=1)))
    _failure(proc, "SKIP-OVER-ALLOWANCE")
    assert _SITE["file"] in proc.stderr, proc.stderr


def test_skips_up_to_the_allowance_certify(tmp_path):
    record = _good_record()
    record.update(passed=3, skipped=2, skip_sites=[dict(_SITE, count=2)])
    proc = _verify(tmp_path, record, allowlist=_allowlist(_entry(allowed=2)))
    assert proc.returncode == 0, proc.stderr


def test_a_prefix_entry_admits_a_variable_suffix(tmp_path):
    record = _good_record()
    record["skip_sites"] = [dict(_SITE, reason="needs a live tmux (pid 81)")]
    allowlist = _allowlist(_entry(reason="needs a live tmux", match="prefix"))
    proc = _verify(tmp_path, record, allowlist=allowlist)
    assert proc.returncode == 0, proc.stderr


def test_an_exact_entry_does_not_admit_a_longer_reason(tmp_path):
    record = _good_record()
    record["skip_sites"] = [dict(_SITE, reason="needs a live tmux (pid 81)")]
    proc = _verify(tmp_path, record, allowlist=_allowlist(_entry()))
    _failure(proc, "SKIP-UNLISTED")


@pytest.mark.parametrize("case_id,allowlist", [
    ("null-allowed", {"schema_version": 1, "entries": [
        _entry(allowed=None)]}),
    ("zero-allowed", {"schema_version": 1, "entries": [_entry(allowed=0)]}),
    ("bad-match", {"schema_version": 1, "entries": [_entry(match="regex")]}),
    ("duplicate", {"schema_version": 1, "entries": [_entry(), _entry()]}),
    ("empty", {"schema_version": 1, "entries": []}),
    ("schema", {"schema_version": 2, "entries": [_entry()]}),
])
def test_an_invalid_allowlist_fails(tmp_path, case_id, allowlist):
    _failure(_verify(tmp_path, _good_record(), allowlist=allowlist),
             "ALLOWLIST-INVALID")


def test_the_committed_allowlist_is_valid_and_every_entry_is_counted():
    module = _verifier_module()
    entries = module.load_skip_allowlist(COMMITTED_ALLOWLIST)
    assert all(isinstance(entry["allowed"], int) and entry["allowed"] >= 1
               for entry in entries)
    assert module.SKIP_ALLOWLIST == COMMITTED_ALLOWLIST


# ---- presence and identity -------------------------------------------------


def test_an_absent_run_record_is_a_failure(tmp_path):
    collection = _write(tmp_path / "collection.json", _good_collection())
    _failure(_run(tmp_path / "missing.json", collection), "RECORD-ABSENT")


def test_an_unparseable_run_record_is_a_failure(tmp_path):
    record = tmp_path / "run.json"
    record.write_text("{not json", encoding="utf-8")
    collection = _write(tmp_path / "collection.json", _good_collection())
    _failure(_run(record, collection), "RECORD-UNPARSEABLE")


def test_no_record_argument_is_a_failure_even_with_a_workspace_record(
        tmp_path):
    """The record path is never derived from GITHUB_WORKSPACE.

    A valid record sits where an ambient default would look; without
    --record the verifier must refuse rather than read it.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write(workspace / _verifier_module().RECORD_NAME, _good_record())
    collection = _write(tmp_path / "collection.json", _good_collection())
    allowlist = _write(tmp_path / "allowlist.json", _allowlist())
    proc = _run(None, collection, {"GITHUB_WORKSPACE": str(workspace)},
                allowlist)
    _failure(proc, "RECORD-ABSENT")
    assert "no --record was given" in proc.stderr, proc.stderr


def test_an_absent_collection_record_file_is_a_failure(tmp_path):
    record = _write(tmp_path / "run.json", _good_record())
    _failure(_run(record, tmp_path / "missing-collection.json",
                  allowlist_path=_write(tmp_path / "a.json", _allowlist())),
             "COLLECTION-RECORD-ABSENT")


def test_no_collection_record_argument_is_a_failure(tmp_path):
    record = _write(tmp_path / "run.json", _good_record())
    _failure(_run(record, None,
                  allowlist_path=_write(tmp_path / "a.json", _allowlist())),
             "COLLECTION-RECORD-ABSENT")


@pytest.mark.parametrize("which", ["run", "collection"])
def test_a_record_from_another_run_is_refused_under_actions(tmp_path, which):
    record = _with_identity(_good_record())
    collection = _with_identity(_good_collection())
    target = record if which == "run" else collection
    target["run_id"] = "9999"
    proc = _verify(tmp_path, record, collection, env_overrides=_ACTIONS_ENV)
    _failure(proc, "RUN-IDENTITY")
    assert ("run record" if which == "run" else "collection record") in (
        proc.stderr)


def test_a_record_with_no_identity_is_refused_under_actions(tmp_path):
    _failure(_verify(tmp_path, _good_record(), env_overrides=_ACTIONS_ENV),
             "RUN-IDENTITY")


def test_a_module_level_skip_is_outside_the_per_item_identities(tmp_path):
    """A collection-level skip adds to skipped without adding an item."""
    record = _good_record()
    record.update(skipped=2, skipped_at_collection=1,
                  skip_sites=[dict(_SITE), dict(_SITE, reason="module")])
    allowlist = _allowlist(_entry(), _entry(reason="module"))
    proc = _verify(tmp_path, record, allowlist=allowlist)
    assert proc.returncode == 0, proc.stderr
