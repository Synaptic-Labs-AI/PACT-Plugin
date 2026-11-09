"""Focused tests for scripts/compare_full_suite_results.py.

Synthetic run directories exercise fail-closed evidence handling and outcome
comparison. One child-pytest probe anchors the JUnit/XPASS representation.
"""

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compare_full_suite_results.py"
SPEC = importlib.util.spec_from_file_location("compare_full_suite_results", SCRIPT)
comparator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparator)
SHA = "a" * 40
ALPHA = "tests/test_example.py::test_alpha"
BETA = "tests/test_example.py::test_beta"
GAMMA = "tests/test_example.py::test_gamma"


def case(node_id, outcome="pass", reason=None, duration=0.01):
    classname, name = comparator._junit_key(node_id)
    return {"node_id": node_id, "classname": classname, "name": name,
            "outcome": outcome, "reason": reason, "duration": duration}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")


def update_json(path, **updates):
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(updates)
    write_json(path, value)


def summary_for(counts):
    labels = (("passed", "passed"), ("failed", "failed"),
              ("skipped", "skipped"), ("xfailed", "xfailed"),
              ("xpassed", "xpassed"), ("errors", "error"))
    parts = [f"{counts[key]} {label}" for key, label in labels if counts[key]]
    return ", ".join(parts) + " in 0.10s"


def actions_line(text):
    return f"full-suite\tUNKNOWN STEP\t2026-11-09T00:00:00.0000000Z {text}"


def make_run(root, name, run_id=100, nodes=None, cases=None,
             record_updates=None, collection_updates=None, metadata_updates=None):
    directory = root / name
    artifact = directory / "artifact"
    artifact.mkdir(parents=True)
    nodes = list(nodes or [ALPHA, BETA])
    cases = cases or [case(node_id) for node_id in nodes]
    counts = Counter(item["outcome"] for item in cases)
    totals = {"passed": counts["pass"], "failed": counts["failure"],
              "errors": counts["error"], "skipped": counts["skip"],
              "xfailed": counts["xfail"], "xpassed": counts["xpass"]}
    summary = summary_for(totals)
    red = totals["failed"] or totals["errors"]
    skip_counts = Counter((item["node_id"].split("::", 1)[0], item["reason"] or "")
                          for item in cases if item["outcome"] == "skip")
    record = {"schema_version": 3, "collected": len(nodes), **totals,
              "deselected": 0, "exitstatus": int(bool(red)), "summary_line": summary,
              "terminal_node_ids": nodes, "items_before_modifyitems": len(nodes),
              "items_after_modifyitems": len(nodes), "run_id": str(run_id),
              "run_attempt": "1", "owner_pid": 123, "skipped_at_collection": 0,
              "skip_sites": [{"file": file, "reason": reason, "count": count}
                             for (file, reason), count in sorted(skip_counts.items())]}
    record.update(record_updates or {})
    write_json(artifact / ".full-run-record.json", record)
    collection = {"schema_version": 1, "collected_items": len(nodes),
                  "collected_files": 1, "discovered_files": 1, "subject": SHA,
                  "run_id": str(run_id), "run_attempt": "1"}
    collection.update(collection_updates or {})
    write_json(artifact / ".full-collection-record.json", collection)
    metadata = {"attempt": 1, "databaseId": run_id,
                "workflowDatabaseId": comparator.WORKFLOW_DATABASE_ID,
                "displayTitle": name, "event": "workflow_dispatch", "headSha": SHA,
                "headBranch": "issue-1714", "status": "completed",
                "conclusion": "failure" if red else "success",
                "url": f"https://example.invalid/runs/{run_id}", "jobs": []}
    metadata.update(metadata_updates or {})
    write_json(directory / "metadata.json", metadata)

    suite = ET.Element("testsuite", name="pytest")
    for item in cases:
        element = ET.SubElement(suite, "testcase", classname=item["classname"],
                                name=item["name"], time=str(item["duration"]))
        outcome = item["outcome"]
        if outcome == "xpass":
            continue
        if outcome in ("failure", "error"):
            child = ET.SubElement(element, outcome)
            child.set("message", item["reason"] or outcome)
        elif outcome in ("skip", "xfail"):
            child = ET.SubElement(element, "skipped")
            child.set("type", "pytest.skip" if outcome == "skip" else "pytest.xfail")
            child.set("message", item["reason"] or "")
    root_element = ET.Element("testsuites", name="pytest tests")
    root_element.append(suite)
    ET.ElementTree(root_element).write(artifact / ".full-suite-results.xml",
                                       encoding="utf-8", xml_declaration=True)

    command = ("python -m pytest -ra -n 4 --dist loadfile "
               "--junitxml=$GITHUB_WORKSPACE/.full-suite-results.xml")
    lines = [
        actions_line(f"##[group]Run {command}"),
        actions_line(command),
        actions_line("platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0"),
        actions_line("CI environment matches constraints-ci.txt."),
        actions_line(f"FULL-COLLECTION-OK 1 test files and {len(nodes)} items match discovery; subject={SHA}"),
    ]
    lines.extend(actions_line(f"XPASS {item['node_id']}" +
                              (f" - {item['reason']}" if item["reason"] else ""))
                 for item in cases if item["outcome"] == "xpass")
    lines.append(actions_line(f"===== {summary} ====="))
    if red:
        lines.append(actions_line("RUN-VERIFY-FAILURE FAILED: synthetic test outcome"))
    else:
        lines.append(actions_line(
            f"FULL-RUN-OK collected={len(nodes)} passed={totals['passed']} "
            f"skipped={totals['skipped']} failed=0 errors=0 deselected=0 "
            f"collection_count={len(nodes)} subject={SHA}"))
    lines.append(actions_line("CI environment matches constraints-ci.txt."))
    (directory / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def invoke(tmp_path, first, second, output_name="out"):
    output = tmp_path / output_name
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--expected-sha", SHA,
         "--output-dir", str(output), f"--run=one={first}", f"--run=two={second}"],
        capture_output=True, text=True, check=False)
    comparison = None
    if (output / "comparison.json").is_file():
        comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    return completed, comparison, output


def test_forward_mapping_literal_control():
    assert comparator._junit_key(
        "tests/test_demo.py::TestThing::test_case[a-b]"
    ) == ("tests.test_demo.TestThing", "test_case[a-b]")


def test_real_actions_job_step_timestamp_envelope(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    command = ("python -m pytest -ra -n 4 --dist loadfile "
               "--junitxml=$GITHUB_WORKSPACE/.full-suite-results.xml")
    for directory in (first, second):
        log = directory / "run.log"
        log.write_text(log.read_text().replace(
            actions_line(command),
            actions_line(f"\x1b[36;1m{command}\x1b[0m")), encoding="utf-8")
        assert all(line.startswith("full-suite\tUNKNOWN STEP\t2026-11-09T")
                   for line in log.read_text().splitlines())
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_byte_different_semantically_identical_runs(tmp_path):
    first = make_run(tmp_path, "one", 100)
    second = make_run(tmp_path, "two", 101,
                      cases=[case(BETA, duration=0.9), case(ALPHA, duration=0.8)])
    log = second / "run.log"
    log.write_text(log.read_text().replace("00:00:00", "01:02:03"), encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, completed.stderr
    assert comparison["status"] == "identical"
    assert comparison["deltas"] == []


def test_distribution_and_duration_changes_are_observations(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA, duration=0.8), case(BETA, duration=0.9)])
    log = second / "run.log"
    log.write_text(log.read_text().replace("--dist loadfile", "--dist load"),
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert {item["type"] for item in comparison["observations"]} == {"argv", "duration"}


def test_repeated_run_identity_is_invalid(tmp_path):
    first = make_run(tmp_path, "one", 100)
    second = make_run(tmp_path, "two", 100)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "repeated" in comparison["evidence_errors"][0]["error"]


def test_pass_skip_swap_is_a_named_delta(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "skip", "later")])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1
    assert any(delta["type"] == "outcome" and delta["name"] == "test_beta"
               for delta in comparison["deltas"])


def test_skip_reason_change_is_a_named_delta(tmp_path):
    first = make_run(tmp_path, "one", cases=[case(ALPHA), case(BETA, "skip", "one")])
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "skip", "two")])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1
    assert [delta["type"] for delta in comparison["deltas"]] == ["skip_reason"]


def test_collection_skip_reason_change_is_a_skip_sites_delta(tmp_path):
    directories = []
    for name, run_id, reason in (("one", 100, "module one"), ("two", 101, "module two")):
        sites = [{"file": "tests/test_example.py", "reason": "test skip", "count": 1},
                 {"file": "tests/test_example.py", "reason": reason, "count": 1}]
        directory = make_run(
            tmp_path, name, run_id, cases=[case(ALPHA), case(BETA, "skip", "test skip")],
            record_updates={"skipped": 2, "skipped_at_collection": 1,
                            "skip_sites": sites, "summary_line": "1 passed, 2 skipped in 0.10s"})
        log = directory / "run.log"
        log.write_text(log.read_text().replace("1 passed, 1 skipped", "1 passed, 2 skipped")
                       .replace("skipped=1", "skipped=2"), encoding="utf-8")
        directories.append(directory)
    completed, comparison, _ = invoke(tmp_path, *directories)
    assert completed.returncode == 1
    assert [delta["type"] for delta in comparison["deltas"]] == ["skip_sites"]


def test_xfail_is_classified_and_compared(tmp_path):
    cases = [case(ALPHA), case(BETA, "xfail", "expected")]
    first = make_run(tmp_path, "one", cases=cases)
    second = make_run(tmp_path, "two", 101, cases=cases)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    outcomes = Counter(outcome["outcome"] for entry in run["cases"]
                       for outcome in entry["outcomes"])
    assert outcomes == {"pass": 1, "xfail": 1}


def test_failure_and_error_outcomes_are_different_not_invalid(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA, "failure", "assertion"),
                             case(BETA, "error", "teardown")])
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1
    assert comparison["status"] == "different"
    runs = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()]
    assert runs[1]["validity"] == "test_outcome"


@pytest.mark.parametrize("added", [True, False])
def test_collection_identity_changes(tmp_path, added):
    first_nodes = [ALPHA, BETA, GAMMA] if added else [ALPHA, BETA]
    second_nodes = [ALPHA, BETA] if added else [ALPHA, BETA, GAMMA]
    first = make_run(tmp_path, "one", nodes=first_nodes)
    second = make_run(tmp_path, "two", 101, nodes=second_nodes)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1
    expected = "collection_removed" if added else "collection_added"
    assert any(delta["type"] == expected and delta["node_ids"] == [GAMMA]
               for delta in comparison["deltas"])


def test_duplicate_call_failure_and_teardown_error_form_a_multiset(tmp_path):
    cases = [case(ALPHA, "failure", "call"), case(ALPHA, "error", "teardown")]
    first = make_run(tmp_path, "one", nodes=[ALPHA], cases=cases)
    second = make_run(tmp_path, "two", 101, nodes=[ALPHA], cases=cases)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    outcomes = Counter(outcome["outcome"] for entry in run["cases"]
                       for outcome in entry["outcomes"])
    assert outcomes == {"failure": 1, "error": 1}
    assert run["validity"] == "test_outcome"


def test_raw_xpass_overlay_is_compared(tmp_path):
    first = make_run(tmp_path, "one")
    xpass_cases = [case(ALPHA, "xpass", "unexpected"), case(BETA)]
    second = make_run(tmp_path, "two", 101, cases=xpass_cases)
    third = make_run(tmp_path, "three", 102, cases=xpass_cases)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1
    assert any(delta["type"] == "xpass" for delta in comparison["deltas"])
    completed, comparison, _ = invoke(tmp_path, second, third, "same-xpass")
    assert completed.returncode == 0, comparison


@pytest.mark.parametrize("mutation", ["missing", "unmapped", "count", "ambiguous"])
def test_bad_xpass_evidence_is_invalid(tmp_path, mutation):
    cases = [case(ALPHA, "xpass", "unexpected"), case(BETA)]
    if mutation == "ambiguous":
        cases = [case(ALPHA), case(ALPHA, "xpass", "unexpected")]
    first = make_run(tmp_path, "one", nodes=[ALPHA] if mutation == "ambiguous" else None,
                     cases=cases)
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    lines = log.read_text().splitlines()
    if mutation == "missing":
        lines = [line for line in lines if "XPASS " not in line]
    elif mutation == "unmapped":
        lines = [line.replace("XPASS " + ALPHA, "XPASS tests/test_example.py::test_missing")
                 for line in lines]
    elif mutation == "count":
        lines.append(actions_line("XPASS " + ALPHA + " - unexpected"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"


@pytest.mark.parametrize("mutation", [
    "summary", "xml", "json", "malformed_xml", "identity", "duplicate_terminal",
    "aggregate", "worker", "collection", "deselection", "full_run"])
def test_missing_or_inconsistent_evidence_is_invalid(tmp_path, mutation):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    if mutation == "summary":
        path = first / "run.log"
        path.write_text("\n".join(line for line in path.read_text().splitlines()
                                  if "2 passed in" not in line) + "\n", encoding="utf-8")
    elif mutation == "xml":
        (first / "artifact" / ".full-suite-results.xml").unlink()
    elif mutation == "json":
        (first / "artifact" / ".full-run-record.json").unlink()
    elif mutation == "malformed_xml":
        (first / "artifact" / ".full-suite-results.xml").write_text("<broken")
    elif mutation == "identity":
        update_json(first / "metadata.json", databaseId=999)
    elif mutation == "duplicate_terminal":
        update_json(first / "artifact" / ".full-run-record.json",
                    terminal_node_ids=[ALPHA, ALPHA])
    elif mutation == "aggregate":
        update_json(first / "artifact" / ".full-run-record.json", passed=3)
    elif mutation == "worker":
        update_json(first / "artifact" / ".full-run-record.json",
                    items_before_modifyitems=None)
    elif mutation == "collection":
        update_json(first / "artifact" / ".full-collection-record.json", collected_items=3)
    elif mutation == "deselection":
        update_json(first / "artifact" / ".full-run-record.json",
                    deselected=1, items_before_modifyitems=3)
    elif mutation == "full_run":
        path = first / "run.log"
        path.write_text("\n".join(line for line in path.read_text().splitlines()
                                  if "FULL-RUN-OK " not in line) + "\n",
                        encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, (mutation, comparison)
    assert comparison["status"] == "invalid"
    assert comparison["evidence_errors"]


def test_identical_valid_red_runs_are_not_certified_clean(tmp_path):
    cases = [case(ALPHA, "failure", "call"), case(BETA)]
    first = make_run(tmp_path, "one", cases=cases)
    second = make_run(tmp_path, "two", 101, cases=cases)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0
    assert comparison["status"] == "identical"
    runs = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()]
    assert [run["validity"] for run in runs] == ["test_outcome", "test_outcome"]


def test_red_run_without_verifier_disposition_is_invalid(tmp_path):
    first = make_run(tmp_path, "one", cases=[case(ALPHA, "failure"), case(BETA)])
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    log.write_text("\n".join(line for line in log.read_text().splitlines()
                             if "RUN-VERIFY-FAILURE " not in line) + "\n",
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"


def test_red_run_with_explicitly_skipped_verifier_is_valid(tmp_path):
    cases = [case(ALPHA, "failure", "call"), case(BETA)]
    jobs = [{"name": "full-suite", "steps": [
        {"name": "Verify the certifying run", "conclusion": "skipped"}]}]
    first = make_run(tmp_path, "one", cases=cases, metadata_updates={"jobs": jobs})
    second = make_run(tmp_path, "two", 101, cases=cases, metadata_updates={"jobs": jobs})
    for directory in (first, second):
        log = directory / "run.log"
        log.write_text("\n".join(line for line in log.read_text().splitlines()
                                 if "RUN-VERIFY-FAILURE " not in line) + "\n",
                       encoding="utf-8")
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["validity"] == "test_outcome"
    assert run["verifier_results"]["full_run"] == "skipped"


def test_real_pytest_junit_xpass_and_duplicate_representation(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_repr.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def broken_teardown():\n"
        "    yield\n"
        "    raise RuntimeError('teardown exploded')\n"
        "def test_call_and_teardown(broken_teardown):\n"
        "    raise AssertionError('call exploded')\n"
        "@pytest.mark.xfail(reason='unexpected success')\n"
        "def test_xpass():\n"
        "    pass\n", encoding="utf-8")
    xml_path = child / "results.xml"
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for key in ("GITHUB_ACTIONS", "GITHUB_WORKSPACE", "PACT_FULL_RUN_RECORD_OWNER",
                "PYTEST_ADDOPTS"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", f"--junitxml={xml_path}"],
        cwd=child, env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    raw_log = completed.stdout + completed.stderr
    root = ET.parse(xml_path).getroot()
    emitted = list(root.iter("testcase"))
    assert len(emitted) == 3
    assert [(element.get("classname"), element.get("name")) for element in emitted] == [
        ("test_repr", "test_call_and_teardown"),
        ("test_repr", "test_call_and_teardown"), ("test_repr", "test_xpass")]
    assert [child.tag for element in emitted for child in element] == ["failure", "error"]
    xpass_line = next(line for line in raw_log.splitlines() if line.startswith("XPASS "))
    assert xpass_line == ("XPASS test_repr.py::test_xpass - unexpected success")
    summary = next(line.strip().strip("=").strip() for line in raw_log.splitlines()
                   if "1 failed" in line and "1 xpassed" in line and "1 error" in line)

    nodes = ["test_repr.py::test_call_and_teardown", "test_repr.py::test_xpass"]
    cases = [case(nodes[0], "failure", "call"), case(nodes[0], "error", "teardown"),
             case(nodes[1], "xpass", "unexpected success")]
    totals = {"passed": 0, "failed": 1, "errors": 1, "skipped": 0,
              "xfailed": 0, "xpassed": 1}
    first = make_run(tmp_path, "one", nodes=nodes, cases=cases,
                     record_updates={"summary_line": summary})
    second = make_run(tmp_path, "two", 101, nodes=nodes, cases=cases,
                      record_updates={"summary_line": summary})
    for directory in (first, second):
        (directory / "artifact" / ".full-suite-results.xml").write_bytes(xml_path.read_bytes())
        log = directory / "run.log"
        lines = log.read_text().splitlines()
        lines = [line for line in lines if "0.10s" not in line and "XPASS " not in line]
        lines.insert(-1, actions_line(summary))
        lines.insert(-1, actions_line(xpass_line))
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert totals["failed"] == 1  # Keep the real red-run accounting explicit.
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["validity"] == "test_outcome"
    assert Counter(outcome["outcome"] for entry in run["cases"]
                   for outcome in entry["outcomes"]) == {
                       "failure": 1, "error": 1, "xpass": 1}
