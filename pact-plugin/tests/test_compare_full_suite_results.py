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

SCRIPT = Path(os.environ.get(
    "PACT_COMPARE_FULL_SUITE_SCRIPT",
    str(Path(__file__).resolve().parents[2] / "scripts" / "compare_full_suite_results.py"),
))
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


def set_step_conclusion(directory, name, conclusion):
    path = directory / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    steps = [step for job in metadata["jobs"] for step in job["steps"]
             if step.get("name") == name]
    assert len(steps) == 1
    steps[0]["conclusion"] = conclusion
    write_json(path, metadata)


def summary_for(counts):
    labels = (("passed", "passed"), ("failed", "failed"),
              ("skipped", "skipped"), ("xfailed", "xfailed"),
              ("xpassed", "xpassed"), ("errors", "error"))
    parts = [f"{counts[key]} {label}" for key, label in labels if counts[key]]
    return ", ".join(parts) + " in 0.10s"


def actions_line(text, bom=False):
    prefix = chr(0xFEFF) if bom else ""
    return f"full-suite\tUNKNOWN STEP\t{prefix}2026-11-09T00:00:00.0000000Z {text}"


ENVIRONMENT_STEP_NAMES = (
    "Verify the CI environment (before tests)",
    "Verify the CI environment (after tests)",
)


def environment_steps():
    return [{"name": name, "conclusion": "success"}
            for name in ENVIRONMENT_STEP_NAMES]


def make_run(root, name, run_id=100, nodes=None, cases=None, dist="loadfile",
             record_updates=None, collection_updates=None, metadata_updates=None,
             teardown_after_pass=(), collection_skips=()):
    directory = root / name
    artifact = directory / "artifact"
    artifact.mkdir(parents=True)
    nodes = list(nodes or [ALPHA, BETA])
    cases = cases or [case(node_id) for node_id in nodes]
    teardown_after_pass = tuple(teardown_after_pass)
    collection_skips = tuple(collection_skips)
    counts = Counter(item["outcome"] for item in cases)
    # A teardown error after a passing call is counted as BOTH a pass and an
    # error by pytest, while its JUnit element carries only the error; the
    # case list already counts that node once as a pass.
    totals = {"passed": counts["pass"],
              "failed": counts["failure"],
              "errors": counts["error"] + len(teardown_after_pass),
              "skipped": counts["skip"] + len(collection_skips),
              "xfailed": counts["xfail"], "xpassed": counts["xpass"]}
    summary = summary_for(totals)
    red = totals["failed"] or totals["errors"]
    skip_counts = Counter((item["node_id"].split("::", 1)[0], item["reason"] or "")
                          for item in cases if item["outcome"] == "skip")
    for file, reason in collection_skips:
        skip_counts[(file, reason)] += 1
    record = {"schema_version": 3, "collected": len(nodes), **totals,
              "deselected": 0, "exitstatus": int(bool(red)), "summary_line": summary,
              "terminal_node_ids": nodes, "items_before_modifyitems": len(nodes),
              "items_after_modifyitems": len(nodes), "run_id": str(run_id),
              "run_attempt": "1", "owner_pid": 123,
              "skipped_at_collection": len(collection_skips),
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
                "url": f"https://example.invalid/runs/{run_id}",
                "jobs": [{"name": "full-suite",
                          "conclusion": "failure" if red else "success",
                          "steps": environment_steps()}]}
    metadata.update(metadata_updates or {})
    write_json(directory / "metadata.json", metadata)

    suite = ET.Element("testsuite", name="pytest")
    for item in cases:
        element = ET.SubElement(suite, "testcase", classname=item["classname"],
                                name=item["name"], time=str(item["duration"]))
        outcome = item["outcome"]
        if outcome == "xpass":
            continue
        if outcome == "pass" and item["node_id"] in teardown_after_pass:
            child = ET.SubElement(element, "error")
            child.set("message", 'failed on teardown with "RuntimeError: boom"')
            continue
        if outcome in ("failure", "error"):
            child = ET.SubElement(element, outcome)
            child.set("message", item["reason"] or outcome)
        elif outcome in ("skip", "xfail"):
            child = ET.SubElement(element, "skipped")
            child.set("type", "pytest.skip" if outcome == "skip" else "pytest.xfail")
            child.set("message", item["reason"] or "")
    for file, _reason in collection_skips:
        element = ET.SubElement(suite, "testcase", classname="",
                                name=Path(file).stem, time="0.000")
        child = ET.SubElement(element, "skipped")
        child.set("message", "collection skipped")
    root_element = ET.Element("testsuites", name="pytest tests")
    root_element.append(suite)
    ET.ElementTree(root_element).write(artifact / ".full-suite-results.xml",
                                       encoding="utf-8", xml_declaration=True)

    command = (f'python -m pytest -ra -n 4 --dist "{dist}" '
               '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
    lines = [
        actions_line(f"##[group]Run {command}", bom=True),
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
    command = ('python -m pytest -ra -n 4 --dist "loadfile" '
               '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
    for directory in (first, second):
        log = directory / "run.log"
        log.write_text(log.read_text().replace(
            actions_line(command),
            actions_line(f"\x1b[36;1m{command}\x1b[0m")), encoding="utf-8")
        assert all(line.startswith("full-suite\tUNKNOWN STEP\t")
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


def test_summary_duration_skew_is_not_an_outcome_delta(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    log.write_text(log.read_text().replace("2 passed in 0.10s", "2 passed in 0.12s"),
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_summary_outcome_mismatch_is_invalid(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    log.write_text(log.read_text().replace("2 passed in 0.10s", "1 passed in 0.10s"),
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"


def test_distribution_and_duration_changes_are_observations(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, dist="load",
                      cases=[case(ALPHA, duration=0.8), case(BETA, duration=0.9)])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert {item["type"] for item in comparison["observations"]} == {"argv", "duration"}


@pytest.mark.parametrize("dist", ["loadfile", "load"])
def test_quoted_distribution_and_bom_command_envelope(tmp_path, dist):
    first = make_run(tmp_path, "one", dist=dist)
    second = make_run(tmp_path, "two", 101, dist=dist)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert f'--dist "{dist}"' in (first / "run.log").read_text(encoding="utf-8")
    assert "\t" + chr(0xFEFF) + "2026-" in (first / "run.log").read_text(encoding="utf-8")
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["argv"][7] == dist


@pytest.mark.parametrize("mutation", ["arbitrary", "narrowing", "unsupported", "conflicting"])
def test_dynamic_command_mutations_remain_invalid(tmp_path, mutation):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    text = log.read_text(encoding="utf-8")
    if mutation == "arbitrary":
        text = text.replace("--junitxml=", "--verbose --junitxml=")
    elif mutation == "narrowing":
        text = text.replace("--junitxml=", "tests/test_example.py --junitxml=")
    elif mutation == "unsupported":
        text = text.replace('"loadfile"', '"loadgroup"')
    else:
        command = ('python -m pytest -ra -n 4 --dist "loadfile" '
                   '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
        conflicting = command.replace('"loadfile"', '"load"')
        text = text.replace(actions_line(command), actions_line(conflicting))
    log.write_text(text, encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, (mutation, comparison)
    assert comparison["status"] == "invalid"


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


def test_skip_sites_reconcile_unique_invocation_relative_paths(tmp_path):
    nodes = ["pact-plugin/tests/test_example.py::test_alpha",
             "pact-plugin/tests/test_example.py::test_beta"]
    cases = [case(nodes[0]), case(nodes[1], "skip", "later")]
    sites = [{"file": "tests/test_example.py", "reason": "later", "count": 1}]
    first = make_run(tmp_path, "one", nodes=nodes, cases=cases,
                     record_updates={"skip_sites": sites})
    second = make_run(tmp_path, "two", 101, nodes=nodes, cases=cases,
                      record_updates={"skip_sites": sites})
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison


def test_skip_site_suffix_mapping_must_be_unambiguous(tmp_path):
    nodes = ["pact-plugin/tests/test_example.py::test_alpha",
             "other/tests/test_example.py::test_beta"]
    cases = [case(node_id, "skip", "later") for node_id in nodes]
    sites = [{"file": "tests/test_example.py", "reason": "later", "count": 2}]
    first = make_run(tmp_path, "one", nodes=nodes, cases=cases,
                     record_updates={"skip_sites": sites})
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert "ambiguous skip-site path" in comparison["evidence_errors"][0]["error"]


def test_collection_skip_reason_change_is_a_skip_sites_delta(tmp_path):
    directories = []
    for name, run_id, reason in (("one", 100, "module one"), ("two", 101, "module two")):
        # Real pytest emits one collection-skip <testcase> per module-level
        # skip; modelling skipped_at_collection with no such case was the
        # unreachable shape.
        directory = make_run(
            tmp_path, name, run_id,
            cases=[case(ALPHA), case(BETA, "skip", "test skip")],
            collection_skips=[("tests/test_example.py", reason)])
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
    assert all(run["environment"]["overall_conclusion"] == "failure" for run in runs)


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
    jobs = [{"name": "full-suite", "conclusion": "failure", "steps": [
        *environment_steps(),
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


def test_failed_after_environment_step_is_invalid(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    set_step_conclusion(
        first, "Verify the CI environment (after tests)", "failure")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "did not conclude success" in comparison["evidence_errors"][0]["error"]


def test_nested_environment_success_text_cannot_spoof_failed_step(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    set_step_conclusion(
        first, "Verify the CI environment (after tests)", "failure")
    log = first / "run.log"
    log.write_text(
        log.read_text() + actions_line("CI environment matches constraints-ci.txt.") + "\n",
        encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "did not conclude success" in comparison["evidence_errors"][0]["error"]


def test_green_tests_with_overall_failure_are_invalid(tmp_path):
    first = make_run(tmp_path, "one", metadata_updates={"conclusion": "failure"})
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "green test outcomes have an inconsistent overall conclusion" in (
        comparison["evidence_errors"][0]["error"])


def test_red_tests_with_overall_success_are_invalid(tmp_path):
    cases = [case(ALPHA, "failure"), case(BETA)]
    first = make_run(tmp_path, "one", cases=cases,
                     metadata_updates={"conclusion": "success"})
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "red test outcomes have an inconsistent overall conclusion" in (
        comparison["evidence_errors"][0]["error"])


def test_missing_environment_step_is_invalid(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    path = first / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["jobs"][0]["steps"] = [
        step for step in metadata["jobs"][0]["steps"]
        if step["name"] != "Verify the CI environment (after tests)"]
    write_json(path, metadata)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "expected exactly one" in comparison["evidence_errors"][0]["error"]


def test_zero_job_infrastructure_failure_is_invalid(tmp_path):
    first = make_run(tmp_path, "one", metadata_updates={
        "jobs": [], "conclusion": "failure"})
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"
    assert "no workflow jobs" in comparison["evidence_errors"][0]["error"]


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


def test_environment_difference_beside_a_delta_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "skip", "later")])
    log = second / "run.log"
    log.write_text(log.read_text().replace(
        "platform linux -- Python 3.12.3", "platform darwin -- Python 3.12.3"),
        encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["status"] == "invalid"
    assert comparison["deltas"], "the delta is still reported as evidence"
    assert "platform" in comparison["evidence_errors"][0]["error"]
    assert "cannot be attributed" in comparison["evidence_errors"][0]["error"]


def test_environment_difference_without_a_delta_is_an_observation(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = second / "run.log"
    log.write_text(log.read_text().replace(
        "platform linux -- Python 3.12.3", "platform darwin -- Python 3.12.3"),
        encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert comparison["observations"] == [
        {"type": "environment", "run": "two", "fields": ["platform"]}]


def test_duplicate_same_outcome_junit_case_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    classname, name = comparator._junit_key(ALPHA)
    xml_path = second / "artifact" / ".full-suite-results.xml"
    tree = ET.parse(xml_path)
    ET.SubElement(tree.getroot().find("testsuite"), "testcase",
                  classname=classname, name=name, time="0.01")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    update_json(second / "artifact" / ".full-run-record.json", passed=3)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "duplicate JUnit outcome" in comparison["evidence_errors"][0]["error"]


def test_internal_error_exits_three_and_still_writes_evidence(tmp_path, monkeypatch):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    real = comparator._normalize_run

    def boom(logical, directory, expected_sha):
        if logical == "one":
            raise RuntimeError("synthetic fault")
        return real(logical, directory, expected_sha)

    monkeypatch.setattr(comparator, "_normalize_run", boom)
    output = tmp_path / "out"
    code = comparator.main(["--expected-sha", SHA, "--output-dir", str(output),
                            f"--run=one={first}", f"--run=two={second}"])
    assert code == 3
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["status"] == "invalid"
    assert comparison["internal_errors"] == [
        {"run": "one", "error": "RuntimeError: synthetic fault"}]
    assert (output / "runs.jsonl").is_file()


def test_vendored_mangling_matches_pytest_internal():
    mangle = pytest.importorskip("_pytest.junitxml").mangle_test_address
    for address in ("tests/test_demo.py",
                    "tests/test_demo.py::test_case",
                    "tests/test_demo.py::TestThing::test_case[a-b]",
                    "tests/test_demo.py::TestThing::test_case[a[b]]",
                    "test[1].py::test_case"):
        assert comparator._mangle_test_address(address) == mangle(address)


def test_metadata_identity_failure_names_the_mismatched_field(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    update_json(first / "metadata.json", event="push")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "metadata event" in comparison["evidence_errors"][0]["error"]
    assert "workflow_dispatch" in comparison["evidence_errors"][0]["error"]


def test_material_duration_slowdown_names_the_test_and_both_values(tmp_path):
    first = make_run(tmp_path, "one",
                     cases=[case(ALPHA, duration=0.1), case(BETA, duration=0.1)])
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA, duration=3.0), case(BETA, duration=0.2)])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["observations"] == [{
        "type": "duration", "run": "two", "count": 1,
        "tests": [{"test": "tests.test_example.test_alpha",
                   "before_seconds": 0.1, "after_seconds": 3.0}]}]


def test_subthreshold_duration_noise_is_not_an_observation(tmp_path):
    first = make_run(tmp_path, "one", cases=[case(ALPHA, duration=0.10), case(BETA)])
    second = make_run(tmp_path, "two", 101, cases=[case(ALPHA, duration=0.11), case(BETA)])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["observations"] == []


def test_teardown_error_after_a_pass_reconciles(tmp_path):
    """Measured shape: record passes+1 and errors+1, JUnit emits only the error."""
    cases = [case(ALPHA), case(BETA)]
    first = make_run(tmp_path, "one", cases=cases, teardown_after_pass=[BETA])
    second = make_run(tmp_path, "two", 101, cases=cases,
                      teardown_after_pass=[BETA])
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["validity"] == "test_outcome"


def test_collection_skip_cases_reconcile_with_the_record(tmp_path):
    skips = [("tests/test_modskip.py", "module skip")]
    first = make_run(tmp_path, "one", collection_skips=skips)
    second = make_run(tmp_path, "two", 101, collection_skips=skips)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_collection_skip_count_mismatch_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    xml_path = first / "artifact" / ".full-suite-results.xml"
    tree = ET.parse(xml_path)
    element = ET.SubElement(tree.getroot().find("testsuite"), "testcase",
                            classname="", name="test_modskip", time="0.000")
    ET.SubElement(element, "skipped", message="collection skipped")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "skipped_at_collection=0" in comparison["evidence_errors"][0]["error"]


def test_collection_error_case_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    xml_path = first / "artifact" / ".full-suite-results.xml"
    tree = ET.parse(xml_path)
    element = ET.SubElement(tree.getroot().find("testsuite"), "testcase",
                            classname="", name="test_broken", time="0.000")
    ET.SubElement(element, "error", message="collection failure")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "collection failure" in comparison["evidence_errors"][0]["error"]


def test_real_pytest_pins_teardown_and_collection_shapes(tmp_path):
    """Both shapes a synthetic fixture cannot model, pinned by real pytest.

    Measured: a pass whose teardown errors emits ONE <testcase> carrying only
    the error, and a module-level skip emits a <testcase> with an EMPTY
    classname. The record comes from the real in-band plugin, so no part of
    the shape model is reconstructed here.

    ``PACT_COMPARE_FULL_SUITE_SCRIPT`` points this arm at another copy of the
    comparator for mutation probing (e.g. the pre-fix script). Every failure
    message below carries the script actually loaded, so an ambient value
    cannot change what was tested without showing up in the failure text.
    """
    child = tmp_path / "child"
    child.mkdir()
    tests_dir = Path(__file__).resolve().parent
    (child / "conftest.py").write_text(
        "import sys\n\n"
        f"TESTS = {str(tests_dir)!r}\n\n\n"
        "def pytest_configure(config):\n"
        "    if TESTS not in sys.path:\n"
        "        sys.path.insert(0, TESTS)\n"
        "    from ci_full_run_record import FullRunRecord\n"
        '    config.pluginmanager.register(FullRunRecord(), "full-run-record")\n',
        encoding="utf-8")
    (child / "test_shapes.py").write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def boom():\n"
        "    yield\n"
        '    raise RuntimeError("teardown boom")\n\n\n'
        "def test_pass():\n    assert True\n\n\n"
        "def test_pass_then_teardown_error(boom):\n    assert True\n\n\n"
        "def test_fail_then_teardown_error(boom):\n    assert False\n\n\n"
        "@pytest.fixture\n"
        "def broken():\n"
        '    raise RuntimeError("setup boom")\n\n\n'
        "def test_setup_error(broken):\n    assert True\n\n\n"
        "@pytest.fixture\n"
        "def broken_after_boom(boom):\n"
        '    raise RuntimeError("setup boom")\n\n\n'
        "def test_setup_and_teardown_error(broken_after_boom):\n    assert True\n\n\n"
        "def test_call_skip_then_teardown_error(boom):\n"
        '    pytest.skip("call skip")\n\n\n'
        '@pytest.mark.xfail(reason="expected")\n'
        "def test_xfail_then_teardown_error(boom):\n    assert False\n\n\n"
        '@pytest.mark.xfail(reason="expected pass")\n'
        "def test_xpass_then_teardown_error(boom):\n    assert True\n",
        encoding="utf-8")
    (child / "test_modskip.py").write_text(
        'import pytest\n\npytest.skip("module skip", allow_module_level=True)\n\n\n'
        "def test_never():\n    assert True\n",
        encoding="utf-8")
    workspace = tmp_path / "rig"
    workspace.mkdir()
    xml = workspace / ".full-suite-results.xml"
    where = f"comparator script actually loaded: {SCRIPT}"
    env = {**os.environ, "GITHUB_ACTIONS": "true",
           "GITHUB_WORKSPACE": str(workspace), "GITHUB_RUN_ID": "200",
           "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": SHA}
    env.pop("PACT_FULL_RUN_RECORD_OWNER", None)
    env.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         f"--junitxml={xml}"],
        cwd=child, env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 1, (
        completed.stdout + completed.stderr + "\n" + where)
    record = json.loads((workspace / ".full-run-record.json").read_text(encoding="utf-8"))
    assert (record["collected"], record["passed"], record["failed"],
            record["errors"], record["skipped"], record["skipped_at_collection"],
            record["xfailed"], record["xpassed"]) == (8, 2, 1, 6, 2, 1, 3, 1), (
                record, where)

    shapes = {}
    for element in ET.parse(xml).getroot().iter("testcase"):
        shapes.setdefault((element.get("classname"), element.get("name")), []).append(
            [(child.tag, child.get("message") or "") for child in element])
    teardown_after_pass = shapes[("test_shapes", "test_pass_then_teardown_error")]
    assert len(teardown_after_pass) == 1, (shapes, where)
    assert [item[0][0] for item in teardown_after_pass] == ["error"], (
        teardown_after_pass, where)
    assert teardown_after_pass[0][0][1].startswith("failed on teardown with"), (
        teardown_after_pass, where)
    call_and_teardown = shapes[("test_shapes", "test_fail_then_teardown_error")]
    assert [item[0][0] for item in call_and_teardown] == ["failure", "error"], (
        call_and_teardown, where)
    assert call_and_teardown[1][0][1].startswith("failed on teardown with"), (
        call_and_teardown, where)
    setup_error = shapes[("test_shapes", "test_setup_error")]
    assert [item[0][0] for item in setup_error] == ["error"], (setup_error, where)
    assert setup_error[0][0][1].startswith("failed on setup with"), (
        setup_error, where)
    setup_and_teardown = shapes[("test_shapes", "test_setup_and_teardown_error")]
    assert [child[0] for child in setup_and_teardown[0]] == ["error", "error"], (
        setup_and_teardown, where)
    assert setup_and_teardown[0][0][1].startswith("failed on setup with"), (
        setup_and_teardown, where)
    assert setup_and_teardown[0][1][1].startswith("failed on teardown with"), (
        setup_and_teardown, where)
    skip_and_teardown = shapes[
        ("test_shapes", "test_call_skip_then_teardown_error")]
    assert [child[0] for child in skip_and_teardown[0]] == ["skipped", "error"], (
        skip_and_teardown, where)
    assert [item[0][0] for item in shapes[("", "test_modskip")]] == ["skipped"], (
        shapes, where)
    assert shapes[("", "test_modskip")][0][0][1] == "collection skipped", (
        shapes, where)
    typed = {(element.get("classname"), element.get("name")):
             [(child.tag, child.get("type")) for child in element]
             for element in ET.parse(xml).getroot().iter("testcase")}
    assert typed[("test_shapes", "test_xfail_then_teardown_error")] == [
        ("skipped", "pytest.xfail"), ("skipped", "pytest.xfail")], (typed, where)
    assert typed[("test_shapes", "test_xpass_then_teardown_error")] == [
        ("skipped", "pytest.xfail")], (typed, where)

    def build(logical, run_id):
        directory = tmp_path / logical
        (directory / "artifact").mkdir(parents=True)
        (directory / "artifact" / ".full-suite-results.xml").write_bytes(
            xml.read_bytes())
        run_record = json.loads(
            (workspace / ".full-run-record.json").read_text(encoding="utf-8"))
        run_record.update(run_id=str(run_id), run_attempt="1")
        write_json(directory / "artifact" / ".full-run-record.json", run_record)
        write_json(directory / "artifact" / ".full-collection-record.json", {
            "schema_version": 1, "collected_items": run_record["collected"],
            "collected_files": 2, "discovered_files": 2, "subject": SHA,
            "run_id": str(run_id), "run_attempt": "1"})
        write_json(directory / "metadata.json", {
            "attempt": 1, "databaseId": run_id,
            "workflowDatabaseId": comparator.WORKFLOW_DATABASE_ID,
            "displayTitle": logical, "event": "workflow_dispatch",
            "headSha": SHA, "headBranch": "issue-1714", "status": "completed",
            "conclusion": "failure", "url": f"https://example.invalid/{run_id}",
            "jobs": [{"name": "full-suite", "runnerName": "r1",
                      "conclusion": "failure", "steps": environment_steps()}]})
        (directory / "run.log").write_text("\n".join([
            actions_line('python -m pytest -ra -n 4 --dist "loadfile" '
                         '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"'),
            actions_line("platform linux -- Python 3.12.3"),
            actions_line("CI environment matches constraints-ci.txt."),
            actions_line(f"FULL-COLLECTION-OK 2 test files and "
                         f"{run_record['collected']} items match discovery; subject={SHA}"),
            *[actions_line(raw) for raw in completed.stdout.splitlines()
              if raw.startswith("XPASS ")],
            actions_line(f"===== {run_record['summary_line']} ====="),
            actions_line("RUN-VERIFY-FAILURE FAILED: real-shape rig is red"),
            actions_line("CI environment matches constraints-ci.txt."),
        ]) + "\n", encoding="utf-8")
        return directory

    first = build("one", 100)
    second = build("two", 101)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, (comparison, where)
    assert comparison["status"] == "identical", (comparison, where)
    assert comparison["evidence_errors"] == [], (comparison, where)
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["validity"] == "test_outcome", (run, where)


def test_junit_with_an_entity_declaration_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    (first / "artifact" / ".full-suite-results.xml").write_text(
        '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        '<testsuites><testsuite><testcase classname="tests.test_example" '
        'name="test_alpha" time="0.01">&lol2;</testcase></testsuite></testsuites>',
        encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "DOCTYPE or ENTITY" in comparison["evidence_errors"][0]["error"]
    assert "can expand entities" in comparison["evidence_errors"][0]["error"]


def test_ordinary_junit_without_a_doctype_still_parses(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    text = (first / "artifact" / ".full-suite-results.xml").read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "DOCTYPE" not in text
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def _set_runner_name(directory, value):
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata["jobs"][0]["runnerName"] = value
    write_json(directory / "metadata.json", metadata)


def _append_junit_child(directory, node_id, tag, message, child_type=None):
    classname, name = comparator._junit_key(node_id)
    xml_path = directory / "artifact" / ".full-suite-results.xml"
    tree = ET.parse(xml_path)
    for element in tree.getroot().iter("testcase"):
        if (element.get("classname"), element.get("name")) == (classname, name):
            child = ET.SubElement(element, tag)
            child.set("message", message)
            if child_type is not None:
                child.set("type", child_type)
            break
    else:
        raise AssertionError(f"{node_id} has no JUnit element")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def _rewrite_log_summary(directory, old, new):
    log_path = directory / "run.log"
    log_path.write_text(log_path.read_text(encoding="utf-8").replace(old, new),
                        encoding="utf-8")


def test_runner_instance_id_does_not_suppress_a_delta(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "skip", "later")])
    _set_runner_name(first, "GitHub Actions 1023381672")
    _set_runner_name(second, "GitHub Actions 1023380269")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["evidence_errors"] == []
    assert [(delta["type"], delta["name"]) for delta in comparison["deltas"]] == [
        ("outcome", "test_beta")]


def test_cache_line_difference_does_not_suppress_a_delta(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "skip", "later")])
    log_path = second / "run.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, actions_line("Cache restored from key: Linux-pip-abc123"))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["evidence_errors"] == []
    assert any(delta["name"] == "test_beta" for delta in comparison["deltas"])


def test_setup_and_teardown_error_in_one_element_reconciles(tmp_path):
    """Measured shape: one element, two <error> children, record errors=2."""
    def build(name, run_id):
        directory = make_run(
            tmp_path, name, run_id, nodes=[ALPHA],
            cases=[case(ALPHA, "error", 'failed on teardown with "boom"')],
            record_updates={"errors": 2, "summary_line": "2 errors in 0.10s"})
        _append_junit_child(directory, ALPHA, "error",
                            'failed on setup with "setup boom"')
        _rewrite_log_summary(directory, "1 error in 0.10s", "2 errors in 0.10s")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_skip_and_teardown_error_in_one_element_reconciles(tmp_path):
    """Measured shape: one element, <skipped> + <error>, record skip=1 error=1."""
    def build(name, run_id):
        # ALPHA keeps the run red so the metadata conclusion and verifier
        # disposition match the record after the error count is bumped.
        directory = make_run(
            tmp_path, name, run_id, nodes=[ALPHA, BETA],
            cases=[case(ALPHA, "failure", "assert False"),
                   case(BETA, "skip", "call skip")],
            record_updates={"errors": 1,
                            "summary_line": "1 failed, 1 skipped, 1 error in 0.10s"})
        _append_junit_child(directory, BETA, "error",
                            'failed on teardown with "boom"')
        _rewrite_log_summary(directory, "1 failed, 1 skipped in 0.10s",
                             "1 failed, 1 skipped, 1 error in 0.10s")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_pass_with_teardown_skip_is_refused_by_name(tmp_path):
    """A pass whose teardown skipped is not marked in JUnit; refuse by name."""
    def build(name, run_id):
        directory = make_run(
            tmp_path, name, run_id, nodes=[ALPHA],
            cases=[case(ALPHA, "skip", "teardown skip")],
            record_updates={"passed": 1,
                            "summary_line": "1 passed, 1 skipped in 0.10s"})
        _rewrite_log_summary(directory, "1 skipped in 0.10s",
                             "1 passed, 1 skipped in 0.10s")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 2, comparison
    assert "teardown skipped is not marked in JUnit" in (
        comparison["evidence_errors"][0]["error"])


def test_xpass_with_teardown_xfail_reconciles(tmp_path):
    """Measured shape: an xpass whose teardown errors emits only the xfail."""
    def build(name, run_id):
        old = "1 passed, 1 xfailed in 0.10s"
        new = "1 passed, 1 xfailed, 1 xpassed in 0.10s"
        directory = make_run(
            tmp_path, name, run_id, nodes=[ALPHA, BETA],
            cases=[case(ALPHA), case(BETA, "xfail", "expected")],
            record_updates={"xpassed": 1, "summary_line": new})
        log_path = directory / "run.log"
        lines = log_path.read_text(encoding="utf-8").replace(old, new).splitlines()
        index = next(i for i, line in enumerate(lines) if new in line)
        lines.insert(index, actions_line(f"XPASS {BETA} - expected"))
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
