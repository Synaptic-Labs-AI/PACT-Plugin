"""Focused tests for scripts/compare_full_suite_results.py.

Synthetic run directories exercise fail-closed evidence handling and outcome
comparison. One child-pytest probe anchors the JUnit/XPASS representation.
"""

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

import pytest

# One unconditional subject: an env override here would let an ambient value
# redirect every arm to a different script with no writer and no record.
SCRIPT = (Path(__file__).resolve().parents[2]
          / "scripts" / "compare_full_suite_results.py")
if not SCRIPT.is_file():
    pytest.skip(
        f"repo-level comparator not present in this layout: {SCRIPT}; this "
        "module exercises a repo script and is not part of the plugin payload",
        allow_module_level=True)

import ci_contract  # noqa: E402  (repo-local helper; after the layout guard)

SPEC = importlib.util.spec_from_file_location("compare_full_suite_results", SCRIPT)
comparator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparator)
SHA = "a" * 40
ALPHA = "tests/test_example.py::test_alpha"
BETA = "tests/test_example.py::test_beta"
GAMMA = "tests/test_example.py::test_gamma"


SESSION_BANNER_TEXT = "=" * 29 + " test session starts " + "=" * 30
SHORT_SUMMARY_TEXT = "=" * 27 + " short test summary info " + "=" * 28


def case(node_id, outcome="pass", reason=None, duration=0.01):
    classname, name = comparator._junit_key(node_id)
    return {"node_id": node_id, "classname": classname, "name": name,
            "outcome": outcome, "reason": reason, "duration": duration}


def child_env():
    """This process's environment without any PYTEST_* variable, so a developer
    shell that exports one cannot change what a child pytest loads or runs."""
    return {key: value for key, value in os.environ.items()
            if not key.startswith("PYTEST_")}


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


def make_run(root, name, run_id=100, nodes=None, cases=None, dist=None,
             record_updates=None, collection_updates=None, metadata_updates=None,
             teardown_after_pass=(), collection_skips=(), command=None, seeds=(),
             header_lines=(), captured=(), banner=True, finish_lines=(),
             census_failed=False):
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
    # A census fails a green session with exit 1: the job and verifier read red.
    failing = red or census_failed
    skip_counts = Counter((item["node_id"].split("::", 1)[0], item["reason"] or "")
                          for item in cases if item["outcome"] == "skip")
    for file, reason in collection_skips:
        skip_counts[(file, reason)] += 1
    record = {"schema_version": 3, "collected": len(nodes), **totals,
              "deselected": 0, "exitstatus": int(bool(failing)), "summary_line": summary,
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
                "conclusion": "failure" if failing else "success",
                "url": f"https://example.invalid/runs/{run_id}",
                "jobs": [{"name": "full-suite",
                          "conclusion": "failure" if failing else "success",
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

    # The replica is the workflow's own run text: Actions echoes it unexpanded,
    # so shlex sees $DIST. `dist=` builds the legacy literal form (older logs
    # and the observation arm); no current workflow emits it.
    # `seeds=` writes one pytest-randomly header line per value.
    if command is None:
        command = (ci_contract.WORKFLOW_TEST_CMD if dist is None
                   else f'python -m pytest -ra -n 4 --dist "{dist}" '
                        '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
    # The report header mirrors a real full-suite step: one banner after the
    # echoed command, the header block, then a blank line before the results.
    # `seeds=` and `header_lines=` add lines inside that block; `captured=`
    # adds lines to a failing test's captured stdout, outside it. `finish_lines=`
    # adds lines to the session-finish span after the header, where a census
    # writes.
    header = [SESSION_BANNER_TEXT] if banner else []
    header += ["platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0",
               *(f"Using --randomly-seed={seed}" for seed in seeds), *header_lines,
               "rootdir: /home/runner/work/PACT-Plugin/PACT-Plugin",
               "configfile: pyproject.toml", "plugins: xdist-3.8.0",
               "created: 4/4 workers", f"4 workers [{len(nodes)} items]", ""]
    # A real log carries the before-tests environment check and the collection
    # check in their own earlier steps, before the test step's run command.
    lines = [
        actions_line("CI environment matches constraints-ci.txt."),
        actions_line(f"FULL-COLLECTION-OK 1 test files and {len(nodes)} items match discovery; subject={SHA}"),
        actions_line(f"##[group]Run {command}", bom=True),
        actions_line(command),
        *map(actions_line, header),
        *map(actions_line, finish_lines),
    ]
    if captured:
        lines.extend(map(actions_line, [
            "=" * 35 + " FAILURES " + "=" * 35, "_" * 30 + " test_beta " + "_" * 30,
            "-" * 29 + " Captured stdout call " + "-" * 29, *captured]))
    # -ra prints XPASS lines in the short test summary, the last report section.
    xpass_lines = [actions_line(f"XPASS {item['node_id']}" +
                                (f" - {item['reason']}" if item["reason"] else ""))
                   for item in cases if item["outcome"] == "xpass"]
    if xpass_lines:
        lines.append(actions_line(SHORT_SUMMARY_TEXT))
    lines.extend(xpass_lines)
    lines.append(actions_line(f"===== {summary} ====="))
    if failing:
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


# Byte-exact empty lines from a real full-suite log (run 43861611679), the line
# after the report header's "4 workers [N items]": first the `gh run view --log`
# form, then the job-logs API form.
REAL_EMPTY_LINES = (
    b"full-suite\tRun test suite\t2026-11-16T11:54:13.8907517Z ",
    b"2026-11-16T11:54:13.8907525Z ",
)


@pytest.mark.parametrize("empty_line", REAL_EMPTY_LINES, ids=["run-view-log", "job-logs-api"])
def test_a_real_empty_log_line_ends_the_report_header(tmp_path, empty_line):
    log = tmp_path / "run.log"
    log.write_bytes(b"\n".join([
        actions_line(ci_contract.WORKFLOW_TEST_CMD).encode("utf-8"),
        actions_line(SESSION_BANNER_TEXT).encode("utf-8"),
        actions_line("Using --randomly-seed=103").encode("utf-8"),
        empty_line,
        actions_line("." * 72 + " [  0%]").encode("utf-8"),
    ]) + b"\n")
    lines = comparator._log_lines(log)
    assert lines[3] == ""
    header, finish_span = comparator._session_spans(lines)
    assert header == ["Using --randomly-seed=103"]
    assert finish_span[1:] == ["." * 72 + " [  0%]"]


def test_real_actions_job_step_timestamp_envelope(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    command = ci_contract.WORKFLOW_TEST_CMD
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
    # Legacy literal form: the current workflow logs one `$DIST` argv for both
    # distributions, so this observation is reachable only from older logs.
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, dist="load",
                      cases=[case(ALPHA, duration=0.8), case(BETA, duration=0.9)])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert {item["type"] for item in comparison["observations"]} == {"argv", "duration"}


@pytest.mark.parametrize("dist, expected", [
    (None, "$DIST"),          # the shipped workflow's unexpanded text
    ("loadfile", "loadfile"),  # legacy literal forms, still accepted
    ("load", "load"),
])
def test_quoted_distribution_and_bom_command_envelope(tmp_path, dist, expected):
    first = make_run(tmp_path, "one", dist=dist)
    second = make_run(tmp_path, "two", 101, dist=dist)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert f'--dist "{expected}"' in (first / "run.log").read_text(encoding="utf-8")
    assert "\t" + chr(0xFEFF) + "2026-" in (first / "run.log").read_text(encoding="utf-8")
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["argv"][7] == expected


def test_unexpanded_dist_variable_is_accepted_on_an_identical_pair(tmp_path):
    """Actions echoes the `run:` body unexpanded, so a real log carries $DIST.

    The value is an argv observation, not a gate: the fixed tokens around it
    still pin the command shape.
    """
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    for directory in (first, second):
        assert '--dist "$DIST"' in (directory / "run.log").read_text(encoding="utf-8")
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert comparison["observations"] == []
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["argv"][7] == "$DIST"


@pytest.mark.parametrize("mutation", [
    "unknown_dist", "path_operand", "selection_flag", "wrong_junit"])
def test_argv_relaxation_is_not_a_blanket_accept(tmp_path, mutation):
    """Accepting `$DIST` must not accept a different shape or value."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    command = ci_contract.WORKFLOW_TEST_CMD
    if mutation == "unknown_dist":
        changed = command.replace('"$DIST"', '"loadgroup"')
    elif mutation == "path_operand":
        changed = command.replace("--junitxml=", "tests/test_example.py --junitxml=")
    elif mutation == "selection_flag":
        changed = command.replace("--junitxml=", "-k smoke --junitxml=")
    else:
        changed = command.replace(".full-suite-results.xml", ".other.xml", 1)
    # Replace every occurrence so both candidate lines carry the mutation and
    # the fixed-token check, not the duplicate-command check, is what fires.
    log.write_text(log.read_text(encoding="utf-8").replace(command, changed),
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, (mutation, comparison)
    assert comparison["status"] == "invalid"
    assert "unexpected full-suite pytest argv" in (
        comparison["evidence_errors"][0]["error"]), (mutation, comparison)


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
        text = text.replace('"$DIST"', '"loadgroup"')
    else:
        command = ci_contract.WORKFLOW_TEST_CMD
        conflicting = command.replace('"$DIST"', '"load"')
        text = text.replace(actions_line(command), actions_line(conflicting))
    log.write_text(text, encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, (mutation, comparison)
    assert comparison["status"] == "invalid"


# The two accepted run-line shapes, written out here so the shape check is
# pinned independently of ci_contract; the shipped line must be the seeded one.
ORDERED_COMMAND = ('python -m pytest -ra -n 4 --dist "$DIST" '
                   '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
SEEDED_COMMAND = ('python -m pytest -ra -n 4 --dist "$DIST" '
                  '${PACT_SHUFFLE_SEED:+-p randomly --randomly-seed="$PACT_SHUFFLE_SEED" --randomly-dont-reset-seed} '
                  '--junitxml="$GITHUB_WORKSPACE/.full-suite-results.xml"')
SEED_TOKENS = ["${PACT_SHUFFLE_SEED:+-p", "randomly", "--randomly-seed=$PACT_SHUFFLE_SEED",
               "--randomly-dont-reset-seed}"]


def read_runs(output):
    return [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()]


def test_the_shipped_run_line_is_the_seeded_shape():
    assert shlex.split(ci_contract.WORKFLOW_TEST_CMD) == shlex.split(SEEDED_COMMAND)
    assert comparator._command_argv([ci_contract.WORKFLOW_TEST_CMD])[8:12] == SEED_TOKENS


def test_seeded_run_line_is_accepted_and_its_seed_is_recorded(tmp_path):
    first = make_run(tmp_path, "one", command=SEEDED_COMMAND, seeds=(7,))
    second = make_run(tmp_path, "two", 101, command=SEEDED_COMMAND)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [7, None]
    runs = read_runs(output)
    assert [run["shuffle_seed"] for run in runs] == [7, None]
    assert runs[0]["argv"][8:12] == SEED_TOKENS
    assert "shuffle_seed" not in runs[0]["environment"]
    assert runs[0]["environment"] == runs[1]["environment"]


def test_ordered_run_line_has_no_seed(tmp_path):
    first = make_run(tmp_path, "one", command=ORDERED_COMMAND)
    second = make_run(tmp_path, "two", 101, command=ORDERED_COMMAND)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [None, None]
    assert len(read_runs(output)[0]["argv"]) == 9


# The test step's seed echo exactly as two real full-suite job logs carry it
# after the step-name columns (runs 43997065717, ordered, and 43997069304,
# seed 159): the runner writes "name: value", so an empty value keeps its
# trailing space.
MEASURED_ORDERED_ECHO = "2026-11-17T01:39:12.7703570Z   PACT_SHUFFLE_SEED: "
MEASURED_SEEDED_ECHO = "2026-11-17T01:39:20.1671713Z   PACT_SHUFFLE_SEED: 159"


def _with_step_env(directory, seed, echo=None):
    """Add the test step's environment echo, as the Actions runner writes it
    between the step's run command and its ##[endgroup]. `echo` replaces the
    seed line with a raw measured line, timestamp included."""
    log = directory / "run.log"
    lines = log.read_text(encoding="utf-8").split("\n")
    command = next(index for index, line in enumerate(lines) if "##[group]Run " in line) + 2
    block = [actions_line(text) for text in (
        "shell: /usr/bin/bash -e {0}", "env:", "  DIST: loadfile",
        f"  PACT_SHUFFLE_SEED: {seed}", "##[endgroup]")]
    if echo is not None:
        block[3] = f"full-suite\tRun test suite\t{echo}"
    lines[command:command] = block
    log.write_text("\n".join(lines), encoding="utf-8")
    return directory


@pytest.mark.parametrize("echo, seeds, expected", [
    (MEASURED_ORDERED_ECHO, (), None),
    (MEASURED_SEEDED_ECHO, (159,), 159),
    (MEASURED_ORDERED_ECHO.rstrip(" "), (), None),
], ids=["ordered", "seeded", "ordered-without-trailing-space"])
def test_the_measured_step_environment_echo_is_read(tmp_path, echo, seeds, expected):
    first = _with_step_env(make_run(tmp_path, "one", seeds=seeds), "", echo=echo)
    second = _with_step_env(make_run(tmp_path, "two", 101, seeds=seeds), "", echo=echo)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [expected, expected]


def test_an_empty_echo_without_its_trailing_space_still_disagrees_with_a_header_seed(
        tmp_path):
    first = make_run(tmp_path, "one")
    second = _with_step_env(make_run(tmp_path, "two", 101, seeds=(159,)), "",
                            echo=MEASURED_ORDERED_ECHO.rstrip(" "))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": (
        "the test step's environment names no seed, but the report header reads "
        "seed 159")}], comparison


@pytest.mark.parametrize("named, seeds, expected", [
    ("7", (7,), 7), ("", (), None),
], ids=["seeded", "ordered"])
def test_a_step_environment_that_agrees_with_the_header_is_read(
        tmp_path, named, seeds, expected):
    first = _with_step_env(make_run(tmp_path, "one", seeds=seeds), named)
    second = _with_step_env(make_run(tmp_path, "two", 101, seeds=seeds), named)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [expected, expected]


@pytest.mark.parametrize("named, seeds, error", [
    ("7", (8,), "the test step's environment names seed '7', but the report header "
                "reads seed 8"),
    ("7", (), "the test step's environment names seed '7', but the report header "
              "reads no seed"),
    ("", (7,), "the test step's environment names no seed, but the report header "
               "reads seed 7"),
], ids=["different-seed", "seed-missing-from-header", "seed-in-an-ordered-run"])
def test_a_step_environment_that_disagrees_with_the_header_is_refused(
        tmp_path, named, seeds, error):
    first = make_run(tmp_path, "one")
    second = _with_step_env(make_run(tmp_path, "two", 101, seeds=seeds), named)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": error}], comparison


@pytest.mark.parametrize("named", ["7", None], ids=["with-step-env", "without-step-env"])
def test_a_seed_line_a_blank_line_pushed_out_of_the_header_is_refused(tmp_path, named):
    """A blank line between the session banner and the seed line ends the
    header before the seed. The run is refused, never read as ordered: the
    displaced seed line is refused on its own, so the refusal holds whether or
    not the log carries the test step's environment echo."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, header_lines=("", "Using --randomly-seed=7"))
    if named is not None:
        _with_step_env(second, named)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    [refusal] = comparison["evidence_errors"]
    assert refusal["run"] == "two"
    assert refusal["error"].startswith(
        "a pytest-randomly seed line sits outside the report header"), refusal


@pytest.mark.parametrize("command, seeds, expected", [
    (ORDERED_COMMAND, (), None),
    (ORDERED_COMMAND, (4,), "refused"),
    (SEEDED_COMMAND, (), None),     # every ordered post-change run logs this
    (SEEDED_COMMAND, (4,), 4),
], ids=["9-unseeded", "9-seeded", "13-unseeded", "13-seeded"])
def test_run_line_shape_and_seed_line_agree(tmp_path, command, seeds, expected):
    first = make_run(tmp_path, "one", command=command, seeds=seeds)
    second = make_run(tmp_path, "two", 101, command=command)
    completed, comparison, _ = invoke(tmp_path, first, second)
    if expected == "refused":
        assert completed.returncode == 2, comparison
        assert comparison["evidence_errors"] == [
            {"run": "one", "error": "a pytest-randomly seed line sits beside the "
             "9-token run line, which has no seed option and cannot load "
             "pytest-randomly"}]
        return
    assert completed.returncode == 0, comparison
    assert comparison["input_runs"][0]["shuffle_seed"] == expected


def test_shuffled_run_with_a_real_delta_is_different_not_invalid(tmp_path):
    """A seed must never make a shuffled-versus-ordered delta unattributable."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, seeds=(3,),
                      cases=[case(ALPHA), case(BETA, "failure", "order")])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["status"] == "different"
    assert comparison["evidence_errors"] == []
    assert [(delta["type"], delta["name"]) for delta in comparison["deltas"]] == [
        ("outcome", "test_beta")]
    assert not any(item["type"] == "environment" for item in comparison["observations"])
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [None, 3]


@pytest.mark.parametrize("decoy", [
    "    Using --randomly-seed=9",       # indented, as inside a traceback
    "E   Using --randomly-seed=9",       # an assertion-detail line
    "Using --randomly-seed=9 (echo)",    # trailing text
    "Using --randomly-seed=nine",
])
def test_only_a_whole_seed_header_line_is_a_seed(tmp_path, decoy):
    first = make_run(tmp_path, "one", header_lines=(decoy,))
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, (decoy, comparison)
    assert comparison["input_runs"][0]["shuffle_seed"] is None


@pytest.mark.parametrize("seed_text, expected", [
    ("4294967295", 4294967295),          # the largest 32-bit seed, 10 digits
    ("12345678901", "refused"),          # 11 digits
    ("1" * 5000, "refused"),             # past int()'s default digit limit
    ("١٢٣", "refused"),   # Arabic-Indic digits
    ("７", "refused"),               # a fullwidth digit
], ids=["10-digits", "11-digits", "5000-digits", "arabic-indic", "fullwidth"])
def test_seed_line_must_be_one_to_ten_ascii_digits(tmp_path, seed_text, expected):
    first = make_run(tmp_path, "one",
                     header_lines=(f"Using --randomly-seed={seed_text}",))
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    if expected == "refused":
        assert completed.returncode == 2, comparison
        assert comparison["evidence_errors"] == [
            {"run": "one",
             "error": "pytest-randomly seed line is not 1 to 10 ASCII digits"}]
        return
    assert completed.returncode == 0, comparison
    assert comparison["input_runs"][0]["shuffle_seed"] == expected


@pytest.mark.parametrize("seeds, captured, expected", [
    ((), ("Using --randomly-seed=99",), None),
    ((3,), ("Using --randomly-seed=99",), 3),
    ((), ("Using --randomly-seed=" + "1" * 5000,), None),
], ids=["captured-only", "header-and-captured", "captured-hostile"])
def test_a_seed_in_captured_output_is_not_the_runs_seed(tmp_path, seeds, captured,
                                                        expected):
    first = make_run(tmp_path, "one", seeds=seeds, captured=captured)
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["input_runs"][0]["shuffle_seed"] == expected


def test_a_banner_from_an_earlier_step_does_not_anchor_the_header(tmp_path):
    first = make_run(tmp_path, "one", seeds=(6,))
    second = make_run(tmp_path, "two", 101)
    log = first / "run.log"
    earlier = [actions_line(text) for text in (
        SESSION_BANNER_TEXT, "Using --randomly-seed=1", "")]
    log.write_text("\n".join(earlier) + "\n" + log.read_text(encoding="utf-8"),
                   encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["input_runs"][0]["shuffle_seed"] == 6


def test_a_summary_line_before_the_session_finish_span_is_refused(tmp_path):
    """The run's only summary line sits before the test step's run command, so
    the report sections cannot be located. The run is refused, never read with
    the whole log as evidence."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = second / "run.log"
    lines = log.read_text(encoding="utf-8").split("\n")
    [summary] = [index for index, line in enumerate(lines) if line.endswith(" in 0.10s =====")]
    lines.insert(0, lines.pop(summary))
    log.write_text("\n".join(lines), encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "the final summary line comes before the end of the "
         "session-finish span, so pytest's report sections cannot be located"}]


def test_a_missing_session_banner_is_refused_not_read_as_ordered(tmp_path):
    first = make_run(tmp_path, "one", banner=False)
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "one", "error": "no pytest session-start banner follows the "
         "full-suite run command; the run's seed header cannot be located"}]


CHILD_CENSUS_HEADER = ("CHILD PYTEST CENSUS: 1 distinct pytest child launch(es) refused "
                       "or not inspected")
CLOCK_CENSUS_HEADER = "clock-shift census: 1 problem(s); this run did not shift every clock"
HOME_CENSUS_HEADER = "HOME CENSUS: 1 distinct child launch(es) without HOME refused"
HOME_FAULT_HEADER = "HOME CENSUS: 1 launch(es) the census could not inspect"
HOME_FAULT_LINE = ("  [gw2] t::f (call): HOME census could not inspect the environment of "
                   "[true]: LookupError()")
CENSUS_PROBLEM = "  [gw0] test_x (call): child"


@pytest.mark.parametrize("finish, name", [
    ((CHILD_CENSUS_HEADER, "  [gw0] test_x (call): pytest child [python -m pytest]"),
     "child_pytest"),
    ((CLOCK_CENSUS_HEADER, "  the shim did not load in children"), "clock_shift"),
    (("." * 40 + " [100%]" + CHILD_CENSUS_HEADER, "  [gw1] test_y (call): child"),
     "child_pytest"),
    ((HOME_CENSUS_HEADER, "  [gw0] test_z (call): child [git status] is given an "
      "environment without HOME"), "home"),
    (("." * 40 + " [100%]" + HOME_CENSUS_HEADER, "  [gw2] test_w (call): child"),
     "home"),
    ((HOME_FAULT_HEADER, HOME_FAULT_LINE), "home_uninspected"),
], ids=["child", "clock", "child-after-xdist-progress", "home", "home-after-xdist-progress",
        "home-faults-only"])
def test_a_census_failed_green_run_names_the_census(tmp_path, finish, name):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=finish)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["status"] == "different"
    assert comparison["deltas"] == [
        {"type": "census", "run": "two", "before": [], "after": [name]}]
    assert [run["validity"] for run in comparison["input_runs"]] == [
        "clean", "census_outcome"]
    assert read_runs(output)[1]["census_failures"] == [
        {"census": name, "problems": 1, "lines": [finish[1].strip()]}]


# What the logging module writes to stderr when a handler cannot format a
# record (measured from a real pytest 9.2 session whose pytest_sessionfinish
# logs a bad record, serial and -n 2). A second such block, or one after any
# newline-terminated stderr line, starts at the beginning of a log line inside
# the session-finish span, before the census header.
LOGGING_ERROR_BLOCK = (
    "--- Logging error ---",
    "Traceback (most recent call last):",
    '  File "/usr/lib/python3.12/logging/__init__.py", line 1160, in emit',
    "    msg = self.format(record)",
    "TypeError: not enough arguments for format string",
    "Call stack:",
    '  File "/home/runner/work/PACT-Plugin/conftest.py", line 21, in pytest_sessionfinish',
    "Message: 'bad %s %s'",
    "Arguments: (2,)",
)


def test_a_logging_error_before_a_census_header_keeps_the_census_outcome(tmp_path):
    """Only a section line pytest itself writes ends the session-finish span, so a
    stderr line of dashes before the census header cannot hide it."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(*LOGGING_ERROR_BLOCK, CLOCK_CENSUS_HEADER,
                                    CENSUS_PROBLEM))
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["evidence_errors"] == [], comparison
    assert comparison["deltas"] == [
        {"type": "census", "run": "two", "before": [], "after": ["clock_shift"]}]
    assert [run["validity"] for run in comparison["input_runs"]] == [
        "clean", "census_outcome"]
    assert read_runs(output)[1]["census_failures"] == [
        {"census": "clock_shift", "problems": 1, "lines": [CENSUS_PROBLEM.strip()]}]


def test_a_logging_error_in_the_report_header_keeps_the_seed(tmp_path):
    """A stderr line of dashes inside the report header does not end it, so the
    pytest-randomly seed line after it is still read."""
    first = make_run(tmp_path, "one", command=SEEDED_COMMAND,
                     header_lines=(*LOGGING_ERROR_BLOCK, "Using --randomly-seed=7"))
    second = make_run(tmp_path, "two", 101, command=SEEDED_COMMAND)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [7, None]


@pytest.mark.parametrize("line", [
    "=" * 31 + " warnings summary " + "=" * 31,
    "=" * 27 + " short test summary info " + "=" * 28,
    "=" * 35 + " FAILURES " + "=" * 35,
    "=" * 30 + " 1 failed, 2 passed in 0.10s " + "=" * 30,
], ids=["warnings-summary", "short-summary", "failures", "final-summary"])
def test_pytest_section_lines_end_the_session_finish_span(line):
    assert comparator.PYTEST_SECTION.fullmatch(line)


@pytest.mark.parametrize("line", [
    "--- Logging error ---", "-" * 29 + " Captured stdout call " + "-" * 29,
    "_" * 30 + " test_beta " + "_" * 30, "=" * 40, "===  ===", "-----",
], ids=["logging-error", "captured-output", "test-header", "bare-equals", "empty-title",
        "dashes"])
def test_other_separator_shaped_lines_do_not(line):
    assert not comparator.PYTEST_SECTION.fullmatch(line)


def test_a_run_with_both_home_census_blocks_counts_both(tmp_path):
    """The HOME census writes its refusal block and then its fault block; each
    header counts only its own lines, and both are named."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(HOME_CENSUS_HEADER, CENSUS_PROBLEM,
                                    HOME_FAULT_HEADER, HOME_FAULT_LINE))
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["deltas"] == [{"type": "census", "run": "two", "before": [],
                                     "after": ["home", "home_uninspected"]}]
    assert [run["validity"] for run in comparison["input_runs"]] == [
        "clean", "census_outcome"]
    assert read_runs(output)[1]["census_failures"] == [
        {"census": "home", "problems": 1, "lines": [CENSUS_PROBLEM.strip()]},
        {"census": "home_uninspected", "problems": 1, "lines": [HOME_FAULT_LINE.strip()]}]


def test_a_home_fault_block_shorter_than_its_header_is_refused(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(HOME_FAULT_HEADER.replace(": 1 ", ": 2 "), HOME_FAULT_LINE))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "the home_uninspected census header counts 2 "
                                "problem(s) but 1 problem line(s) follow it"}]


# The child-pytest census's refusal text after the launch, as finish() prints
# it, and the same text as its census_tests key reads it (cwd path dropped).
CHILD_REFUSAL = (' (cwd /rig) does not block pytest-randomly (family [program]); add "-p", '
                 '"no:randomly" to the child argv')
CHILD_REFUSAL_KEY = CHILD_REFUSAL.replace("(cwd /rig)", "(cwd ...)")


def test_a_census_failing_in_different_tests_is_a_delta(tmp_path):
    """The same census failing both runs, but in different tests, is reported:
    the name-level census delta is empty, so a census_tests delta names them."""
    first = make_run(tmp_path, "one", census_failed=True, finish_lines=(
        CHILD_CENSUS_HEADER,
        "  [gw0] tests/test_a.py::test_x (call): pytest child [pytest]" + CHILD_REFUSAL))
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=(
        CHILD_CENSUS_HEADER,
        "  [gw2] tests/test_b.py::test_y (call): pytest child [pytest]" + CHILD_REFUSAL))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["deltas"] == [
        {"type": "census_tests", "run": "two", "census": "child_pytest",
         "before": ["tests/test_a.py::test_x (call): pytest child [...]"
                    + CHILD_REFUSAL_KEY],
         "after": ["tests/test_b.py::test_y (call): pytest child [...]"
                   + CHILD_REFUSAL_KEY]}], comparison


@pytest.mark.parametrize("census, header, before, after", [
    ("child_pytest", CHILD_CENSUS_HEADER,
     "  [gw0] tests/test_a.py::test_x (call): pytest child [pytest /tmp/a] (cwd /tmp/a) "
     "does not block pytest-randomly (family [program]); add \"-p\", \"no:randomly\" to "
     "the child argv",
     "  [gw1] tests/test_a.py::test_x (call): pytest child [bash -c pytest /tmp/b] "
     "(cwd /tmp/b) does not block pytest-randomly (family [shell], then [program]); add "
     "\"-p\", \"no:randomly\" to the child argv"),
    ("home", HOME_CENSUS_HEADER,
     "  [gw0] tests/test_a.py::test_x (call): child [git status /tmp/a] is given an "
     "environment without HOME",
     "  [gw0] tests/test_a.py::test_x (call): child [git status /tmp/b] inherits an "
     "environment without HOME"),
    ("clock_shift", CLOCK_CENSUS_HEADER,
     "  tests/test_a.py::test_x (call): python /tmp/a/run.py -- the shim did not load",
     "  tests/test_a.py::test_x (call): python /tmp/b/run.py -- the clock was not shifted"),
], ids=["child", "home", "clock"])
def test_the_same_test_failing_a_census_for_a_different_problem_is_a_delta(
        tmp_path, census, header, before, after):
    """A census failing both runs in the same test, but for a different problem,
    is a census_tests delta; the launch text and paths in each line are not."""
    first = make_run(tmp_path, "one", census_failed=True, finish_lines=(header, before))
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=(header, after))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    [delta] = comparison["deltas"]
    assert delta["type"] == "census_tests" and delta["census"] == census, delta
    assert delta["before"] != delta["after"], delta
    assert all("/tmp/" not in key for key in delta["before"] + delta["after"]), delta


def test_a_census_header_printed_twice_is_refused(tmp_path):
    """Each census prints its header once per session, so a second block for
    the same census is refused rather than read over the first."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=(
        CHILD_CENSUS_HEADER, "  [gw0] tests/test_a.py::test_x (call): pytest child [pytest]",
        CHILD_CENSUS_HEADER, "  [gw1] tests/test_b.py::test_y (call): pytest child [pytest]"))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "the child_pytest census header appears more than once "
                                "in the session-finish span"}], comparison


def test_a_census_failing_in_the_same_tests_on_other_launch_text_is_no_delta(tmp_path):
    """Launch text carries temporary paths, and xdist puts a test on any worker;
    neither is a difference between runs, so only the tests a census names count."""
    first = make_run(tmp_path, "one", census_failed=True, finish_lines=(
        CHILD_CENSUS_HEADER.replace(": 1 ", ": 2 "),
        "  [gw0] tests/test_a.py::test_x (call): pytest child [pytest /tmp/pytest-of-u/pytest-1]"
        + CHILD_REFUSAL.replace("/rig", "/tmp/pytest-of-u/pytest-1"),
        "  <outside a test>: pytest child [pytest /tmp/a]" + CHILD_REFUSAL))
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=(
        CHILD_CENSUS_HEADER.replace(": 1 ", ": 2 "),
        "  [gw3] tests/test_a.py::test_x (call): pytest child [pytest /tmp/pytest-of-u/pytest-9]"
        + CHILD_REFUSAL.replace("/rig", "/tmp/pytest-of-u/pytest-9"),
        "  <outside a test>: pytest child [pytest /tmp/b]" + CHILD_REFUSAL))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["deltas"] == [], comparison


def _shim_line(launches, ledger, worker=""):
    return (f"  {worker}the shim did not load in children: {launches} Python launch(es) "
            f"writing this run's ledger seen, and no child process wrote to "
            f"PACT_TEST_CLOCK_SHIFT_LEDGER ({ledger})")


@pytest.mark.parametrize("second_line, deltas", [
    (_shim_line(5, "/tmp/pytest-of-u/b/ledger"), []),
    (_shim_line(5, "/tmp/pytest-of-u/b/ledger", worker="[gw1] "), []),
    ("  the ledger could not be read: PermissionError(13)",
     [{"type": "census_tests", "run": "two", "census": "clock_shift",
       "before": ["the shim did not load in children"],
       "after": ["the ledger could not be read"]}]),
], ids=["count-and-ledger-differ", "forwarded-from-a-worker", "a-different-problem"])
def test_a_census_line_naming_no_test_is_compared_by_its_problem(
        tmp_path, second_line, deltas):
    """A problem line naming no test carries counts and paths that differ by
    run; only the problem it names is compared."""
    first = make_run(tmp_path, "one", census_failed=True, finish_lines=(
        CLOCK_CENSUS_HEADER, _shim_line(2, "/tmp/pytest-of-u/a/ledger")))
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=(
        CLOCK_CENSUS_HEADER, second_line))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == (1 if deltas else 0), comparison
    assert comparison["evidence_errors"] == [], comparison
    assert comparison["deltas"] == deltas, comparison


@pytest.mark.parametrize("finish, found", [
    (("HOME CENSUS: 2 distinct child launch(es) without HOME refused", "  [gw0] test_z (call): child"),
     1),
    ((HOME_CENSUS_HEADER, "  [gw0] test_z (call): child", "  [gw1] test_y (call): child"),
     2),
    (("HOME CENSUS: 2 distinct child launch(es) without HOME refused", "  [gw0] test_z (call): child",
      "[gw3] PASSED tests/test_other.py::test_ok", "  [gw1] test_y (call): child"), 1),
], ids=["truncated", "extra-line", "interleaved"])
def test_a_census_block_whose_lines_disagree_with_its_header_is_refused(
        tmp_path, finish, found):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True, finish_lines=finish)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    counted = int(finish[0].split(": ")[1].split(" ")[0])
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": f"the home census header counts {counted} "
         f"problem(s) but {found} problem line(s) follow it"}]


def test_a_census_line_only_in_captured_output_does_not_explain_the_exit(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      captured=(CHILD_CENSUS_HEADER,))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "green test outcomes have an inconsistent overall conclusion"}]


HOME_CENSUS_INFO = "HOME CENSUS: installed, 812 launches checked, 0 refused"
HOME_CENSUS_INFO_ONE = "HOME CENSUS: installed, 1 launch checked, 0 refused"


@pytest.mark.parametrize("finish", [
    (HOME_CENSUS_INFO,),
    ("." * 40 + " [100%]" + HOME_CENSUS_INFO,),
    (HOME_CENSUS_INFO_ONE,),
    ("." * 40 + " [100%]" + HOME_CENSUS_INFO_ONE,),
], ids=["own-line", "after-xdist-progress", "singular-own-line", "singular-after-progress"])
def test_a_green_run_carrying_the_home_census_information_line_is_clean(tmp_path, finish):
    first = make_run(tmp_path, "one", finish_lines=finish)
    second = make_run(tmp_path, "two", 101, finish_lines=finish)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["deltas"] == [] and comparison["evidence_errors"] == []
    assert [run["validity"] for run in comparison["input_runs"]] == ["clean", "clean"]
    assert [run["census_failures"] for run in read_runs(output)] == [[], []]


def test_the_home_census_failure_header_still_fails_beside_a_run_with_the_information_line(
        tmp_path):
    first = make_run(tmp_path, "one", finish_lines=(HOME_CENSUS_INFO,))
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(HOME_CENSUS_HEADER, "  [gw0] test_z (call): child [x]"))
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["deltas"] == [
        {"type": "census", "run": "two", "before": [], "after": ["home"]}]
    assert read_runs(output)[1]["census_failures"] == [
        {"census": "home", "problems": 1, "lines": ["[gw0] test_z (call): child [x]"]}]


def test_a_census_line_beside_a_clean_exit_is_refused(tmp_path):
    """A census that fails a session raises its status to 1, so exit 0 contradicts it."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      finish_lines=(CHILD_CENSUS_HEADER, CENSUS_PROBLEM))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["deltas"] == []
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "the child_pytest census reported a failure beside exit status 0; "
                  "a census raises a clean exit status to 1, so the run record and "
                  "the log disagree"}]


# What the in-process CI binding writes at session end when it finds drift.
BINDING_DRIFT_LINES = (
    "CI ENVIRONMENT DRIFT (pytest process, at session end)",
    "UNEXPECTED: some-package",
    "The session had ALREADY ended with exit status 1 before this check; its test "
    "results are reported above and are not hidden by this exit status.",
)
ENVIRONMENT_MATCH = "CI environment matches constraints-ci.txt."


def append_log_lines(directory, texts):
    log = directory / "run.log"
    log.write_text(log.read_text(encoding="utf-8")
                   + "".join(actions_line(text) + "\n" for text in texts),
                   encoding="utf-8")


def add_drift(directory, shape="installed-set"):
    """Write a real drift run's log shape.

    installed-set: the installed packages changed during the run, so the
    after-tests verify step reports the drift too and prints no match line,
    leaving only the before-tests one. process-only: only the pytest process's
    own path changed, so the after-tests step still matches.
    """
    if shape == "installed-set":
        log = directory / "run.log"
        lines = log.read_text(encoding="utf-8").splitlines()
        last = max(index for index, line in enumerate(lines) if ENVIRONMENT_MATCH in line)
        lines[last:last + 1] = [actions_line("UNEXPECTED: some-package")]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_log_lines(directory, BINDING_DRIFT_LINES)


@pytest.mark.parametrize("shape", ["installed-set", "process-only"])
def test_a_drift_log_names_the_ci_environment_binding(tmp_path, shape):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    add_drift(second, shape)
    if shape == "installed-set":
        log = (second / "run.log").read_text(encoding="utf-8")
        assert log.count(ENVIRONMENT_MATCH) == 1
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "the CI environment binding reported environment drift (a CI "
                  "ENVIRONMENT DRIFT heading is in the log), so the run is invalid "
                  "infrastructure"}]


def test_drift_over_a_census_failure_names_the_binding_and_the_census(tmp_path):
    """The binding's session-end check sets 3 over a run a census raised to 1."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(CHILD_CENSUS_HEADER, "  [gw0] test_x (call): child"),
                      record_updates={"exitstatus": 3})
    add_drift(second)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "the CI environment binding reported environment drift (a CI "
                  "ENVIRONMENT DRIFT heading is in the log), so the run is invalid "
                  "infrastructure, and it set exit status 3 over the child_pytest "
                  "census failure"}]


def test_drift_beside_a_census_at_exit_1_does_not_claim_the_binding_set_3(tmp_path):
    """The suffix says the binding set 3; a record still at 1 does not show that."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(CHILD_CENSUS_HEADER, "  [gw0] test_x (call): child"))
    add_drift(second)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "the CI environment binding reported environment drift (a CI "
                  "ENVIRONMENT DRIFT heading is in the log), so the run is invalid "
                  "infrastructure"}]


def test_drift_stays_the_reason_beside_a_malformed_census_block(tmp_path):
    """A census header with no problem line cannot replace the drift refusal."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(CHILD_CENSUS_HEADER,),
                      record_updates={"exitstatus": 3})
    add_drift(second)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "the CI environment binding reported environment drift (a CI "
                  "ENVIRONMENT DRIFT heading is in the log), so the run is invalid "
                  "infrastructure"}]


def test_an_inherited_pytest_addopts_is_named(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    append_log_lines(second, (
        "PYTEST_ADDOPTS is set in this process; inherited options can narrow it",))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": "a check in the log reported PYTEST_ADDOPTS is set, so the run's "
                  "options are not the reviewed command alone"}]


def test_missing_environment_evidence_keeps_its_own_refusal(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    log = second / "run.log"
    kept = [line for line in log.read_text(encoding="utf-8").splitlines()
            if "CI environment matches constraints-ci.txt." not in line]
    log.write_text("\n".join(kept) + "\n", encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "before/after environment evidence is not clean"}]


def _drop_first_line_with(text):
    def drop(directory):
        log = directory / "run.log"
        lines = log.read_text(encoding="utf-8").splitlines()
        index = next(i for i, line in enumerate(lines) if text in line)
        del lines[index]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return drop


# Each evidence marker, as a failing test's own output can print it. Some rows
# also drop the real marker, so the printed one is the only copy left.
TEST_PRINTED_MARKERS = {
    "drift": ("CI ENVIRONMENT DRIFT (pytest process, at session end)", None),
    "addopts": ("PYTEST_ADDOPTS is set in this process; inherited options can narrow it",
                None),
    "command": ("python -m pytest -q tests/test_example.py", None),
    "xpass": (f"XPASS {ALPHA}", None),
    "run-verify-failure": ("RUN-VERIFY-FAILURE FAILED: a line a test printed", None),
    "full-collection-ok": (
        f"FULL-COLLECTION-OK 1 test files and 2 items match discovery; subject={SHA}",
        _drop_first_line_with("FULL-COLLECTION-OK ")),
    "full-run-ok": (
        "FULL-RUN-OK collected=2 passed=2 skipped=0 failed=0 errors=0 deselected=0 "
        f"collection_count=2 subject={'b' * 40}", None),
    "environment-match": (ENVIRONMENT_MATCH, _drop_first_line_with(ENVIRONMENT_MATCH)),
    "platform": ("platform linux -- Python 3.99.0, pytest-9.2.0, pluggy-1.7.0", None),
    "measurement": ("sys.executable=/elsewhere/python which python3=/elsewhere", None),
    "pins": ("the test expected constraints-ci.txt to pin something else", None),
    "cache": ("Cache restored from key: forged-by-a-test", None),
}


@pytest.mark.parametrize("marker", list(TEST_PRINTED_MARKERS))
def test_a_marker_a_failing_test_prints_changes_nothing(tmp_path, marker):
    """Evidence is read only where its real writer prints it, never from the
    report sections where a test's own text appears. The same red run is
    compared with and without the marker in its captured output, and every
    result must be the same."""
    text, mutate = TEST_PRINTED_MARKERS[marker]
    first = make_run(tmp_path, "one")
    results = []
    for variant, captured in (("plain", ()), ("marked", (text,))):
        second = make_run(tmp_path / variant, "two", 101,
                          cases=[case(ALPHA), case(BETA, "failure", "boom")],
                          captured=("the test's own output", *captured))
        if mutate is not None:
            mutate(second)
        completed, comparison, output = invoke(tmp_path, first, second, f"out-{variant}")
        run = read_runs(output)[1]
        results.append((completed.returncode, comparison["status"], comparison["deltas"],
                        comparison["evidence_errors"], run["validity"],
                        run.get("verifier_results"), run.get("environment")))
    assert results[0] == results[1], (marker, results)
    if mutate is None:
        assert results[0][:2] == (1, "different"), results[0]
    else:
        assert results[0][:2] == (2, "invalid"), results[0]


@pytest.mark.parametrize("exitstatus", [2, 3, 4])
def test_a_census_line_beside_an_exit_status_a_census_cannot_set_is_refused(
        tmp_path, exitstatus):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(CLOCK_CENSUS_HEADER, CENSUS_PROBLEM),
                      record_updates={"exitstatus": exitstatus})
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": f"the clock_shift census reported a failure beside exit status "
                  f"{exitstatus}, which a census does not produce, so another cause "
                  f"set the exit status"}]


@pytest.mark.parametrize("exitstatus", [1, 2])
def test_a_red_run_with_a_census_line_keeps_its_test_outcome(tmp_path, exitstatus):
    """The census-status refusal is for green runs: a red run's exit status has a
    test failure behind it, so either status a failure sets (1, or 2 for an
    interrupted session) is consistent beside a census line."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "failure", "boom")],
                      finish_lines=(CHILD_CENSUS_HEADER, CENSUS_PROBLEM),
                      record_updates={"exitstatus": exitstatus})
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["evidence_errors"] == []
    run = read_runs(output)[1]
    assert run["validity"] == "test_outcome"
    assert [failure["census"] for failure in run["census_failures"]] == ["child_pytest"]


@pytest.mark.parametrize("census", [False, True], ids=["plain", "census"])
@pytest.mark.parametrize("exitstatus", [3, 4, 5])
def test_a_red_run_beside_an_exit_status_a_failure_cannot_set_is_refused(
        tmp_path, exitstatus, census):
    """3 is pytest's internal error (and the CI binding's drift status), 4 a usage
    error, 5 no tests collected: none is a test outcome, red or not."""
    first = make_run(tmp_path, "one")
    finish = (CHILD_CENSUS_HEADER, CENSUS_PROBLEM) if census else ()
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "failure", "boom")],
                      finish_lines=finish, record_updates={"exitstatus": exitstatus})
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two",
         "error": f"red test outcomes beside exit status {exitstatus}, which a test "
                  "failure does not produce, so another cause set the exit status"}]


def test_every_output_carries_schema_version_2(tmp_path):
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["schema_version"] == 2
    assert [run["schema_version"] for run in read_runs(output)] == [2, 2]
    refused = make_run(tmp_path, "three", 102, seeds=(7, 8))
    completed, comparison, output = invoke(tmp_path, refused, second, "out2")
    assert completed.returncode == 2, comparison
    assert comparison["schema_version"] == 2
    assert [run["schema_version"] for run in read_runs(output)] == [2, 2]


def test_a_red_run_records_its_census_and_ignores_a_captured_one(tmp_path):
    first = make_run(tmp_path, "one")
    captured_shape = CHILD_CENSUS_HEADER.replace(": 1 ", ": 9 ")
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA), case(BETA, "failure", "boom")],
                      finish_lines=(CLOCK_CENSUS_HEADER, "  the shim did not load"),
                      captured=(captured_shape,))
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert sorted(delta["type"] for delta in comparison["deltas"]) == ["census", "outcome"]
    assert read_runs(output)[1]["validity"] == "test_outcome"
    assert read_runs(output)[1]["census_failures"] == [
        {"census": "clock_shift", "problems": 1, "lines": ["the shim did not load"]}]


def test_the_census_comparison_is_by_name_not_by_count(tmp_path):
    first = make_run(tmp_path, "one", census_failed=True,
                     finish_lines=(CHILD_CENSUS_HEADER.replace(": 1 ", ": 2 "),
                                   *(CENSUS_PROBLEM,) * 2))
    second = make_run(tmp_path, "two", 101, census_failed=True,
                      finish_lines=(CHILD_CENSUS_HEADER.replace(": 1 ", ": 3 "),
                                    *(CENSUS_PROBLEM,) * 3))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["deltas"] == []


@pytest.mark.parametrize("module_name", ["child_pytest_census", "clock_shift_census",
                                         "home_census"])
def test_each_census_header_written_by_finish_is_recognised(monkeypatch, module_name):
    """The comparator's census patterns match the line each real finish() writes."""
    if module_name == "child_pytest_census":
        import child_pytest_census as census
        name = "child_pytest"
        monkeypatch.setitem(census._state, "misses", ["a miss"])
        monkeypatch.setitem(census._state, "forwarded", [])
    elif module_name == "home_census":
        import home_census as census
        name = "home"
        monkeypatch.setitem(census._state, "misses", ["a miss"])
        monkeypatch.setitem(census._state, "forwarded", [])
    else:
        from clock_shift import clock_shift_census as census
        name = "clock_shift"
        monkeypatch.setitem(census._state, "installed", True)
        monkeypatch.setitem(census._state, "misses", ["a miss"])
        monkeypatch.setitem(census._state, "workers", [])
        monkeypatch.setitem(census._state, "python_children", 0)
    written = []
    reporter = types.SimpleNamespace(write_line=written.append)
    session = types.SimpleNamespace(
        exitstatus=0, config=types.SimpleNamespace(
            pluginmanager=types.SimpleNamespace(get_plugin=lambda _name: reporter)))
    census.finish(session)
    assert session.exitstatus == 1
    assert comparator._census_failures(written) == [
        {"census": name, "problems": 1, "lines": ["a miss"]}]


def test_the_information_line_the_real_home_census_writes_is_no_census_failure(monkeypatch):
    import home_census as census
    monkeypatch.setitem(census._state, "misses", [])
    monkeypatch.setitem(census._state, "forwarded", [])
    monkeypatch.setitem(census._state, "installed", True)
    for checked, line in ((812, HOME_CENSUS_INFO), (1, HOME_CENSUS_INFO_ONE)):
        monkeypatch.setitem(census._state, "checked", checked)
        monkeypatch.setitem(census._state, "forwarded_checked", 0)
        written = []
        reporter = types.SimpleNamespace(write_line=written.append)
        session = types.SimpleNamespace(
            exitstatus=0, config=types.SimpleNamespace(
                pluginmanager=types.SimpleNamespace(get_plugin=lambda _name: reporter)))
        census.finish(session)
        assert session.exitstatus == 0
        assert written == [line]
        assert comparator._census_failures(written) == []


class _UnreadableEnvironment:
    """An environment the HOME census cannot read, so its audit hook records a
    fault rather than a refusal."""

    def __iter__(self):
        raise LookupError()


@pytest.mark.parametrize("with_refusal", [False, True], ids=["faults-only", "both-blocks"])
def test_the_home_census_fault_block_finish_writes_is_recognised(monkeypatch, with_refusal):
    """The real home_census renders its fault lines (its audit hook, locally and
    as an xdist worker's forwarded line) and its fault header (finish()), and the
    comparator reads them as the home_uninspected census, beside the refusal
    block when there is one."""
    import home_census as census
    for key, value in (("misses", ["a miss"] if with_refusal else []), ("forwarded", []),
                       ("faults", []), ("forwarded_faults", []), ("exempt", None),
                       ("shared", 0), ("checked", 0), ("forwarded_checked", 0),
                       ("collected", set())):
        monkeypatch.setitem(census._state, key, value)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::f (call)")
    census._audit("os.posix_spawn", ("/bin/true", ["true"], _UnreadableEnvironment()))
    assert len(census._state["faults"]) == 1, census._state
    census.collect(types.SimpleNamespace(
        gateway=types.SimpleNamespace(id="gw2"),
        workeroutput={census.WORKEROUTPUT_FAULTS_KEY: list(census._state["faults"])}))
    written = []
    reporter = types.SimpleNamespace(write_line=written.append)
    session = types.SimpleNamespace(
        exitstatus=0, config=types.SimpleNamespace(
            pluginmanager=types.SimpleNamespace(get_plugin=lambda _name: reporter)))
    census.finish(session)
    assert session.exitstatus == 1
    failures = comparator._census_failures(written)
    expected = [("home_uninspected", 2)]
    if with_refusal:
        expected.insert(0, ("home", 1))
    assert [(failure["census"], failure["problems"]) for failure in failures] == expected, written
    fault_lines = failures[-1]["lines"]
    assert fault_lines[1].startswith("[gw2] "), fault_lines
    assert all("HOME census could not inspect the environment of [true]: LookupError()"
               in line for line in fault_lines), fault_lines


RIG_CONFTEST = """\
import child_pytest_census


def pytest_configure(config):
    child_pytest_census.install()


def pytest_testnodedown(node, error):
    child_pytest_census.collect(node)


def pytest_sessionfinish(session):
    child_pytest_census.finish(session)
"""
RIG_TESTS = """\
import subprocess
import sys


def test_starts_an_unblocked_child():
    try:
        subprocess.run([sys.executable, "-m", "pytest", "--version"], check=False)
    except Exception:
        pass


def test_prints_a_census_shaped_line():
    print({shape!r})
    assert False
"""


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_census_output_lands_in_the_session_finish_span(tmp_path, parallel):
    """Real pytest output: the census header is read where pytest writes it, and a
    census-shaped line a failing test prints is not."""
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "conftest.py").write_text(RIG_CONFTEST, encoding="utf-8")
    shape = CHILD_CENSUS_HEADER.replace(": 1 ", ": 9 ")
    (rig / "test_rig.py").write_text(RIG_TESTS.format(shape=shape), encoding="utf-8")
    env = {**child_env(), "PYTHONPATH": str(Path(__file__).parent)}
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", *parallel],
        cwd=rig, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert shape in completed.stdout  # the decoy reached the captured output
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    _header, finish_span = comparator._session_spans(lines)
    failures = comparator._census_failures(finish_span)
    assert [(failure["census"], failure["problems"]) for failure in failures] == [
        ("child_pytest", 1)], completed.stdout


LOGGING_ERROR_RIG_CONFTEST = RIG_CONFTEST.replace("""\
def pytest_sessionfinish(session):
""", """\
import logging
import sys

_probe = logging.getLogger("probe")
_probe.propagate = False
_probe.addHandler(logging.StreamHandler(sys.stderr))


def pytest_sessionfinish(session):
    _probe.warning("bad %s %s", 1)
    _probe.warning("bad %s %s", 2)
""")


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_logging_error_before_the_census_keeps_it_in_the_span(tmp_path, parallel):
    """Real pytest output with stderr merged into stdout, as the Actions log
    has it: a logging error at session finish puts a '--- Logging error ---'
    line at the start of a log line before the census header, and the census
    is still read from the session-finish span."""
    assert "_probe.warning" in LOGGING_ERROR_RIG_CONFTEST
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "conftest.py").write_text(LOGGING_ERROR_RIG_CONFTEST, encoding="utf-8")
    shape = CHILD_CENSUS_HEADER.replace(": 1 ", ": 9 ")
    (rig / "test_rig.py").write_text(RIG_TESTS.format(shape=shape), encoding="utf-8")
    env = {**child_env(), "PYTHONPATH": str(Path(__file__).parent)}
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", *parallel],
        cwd=rig, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=300, check=False)
    assert completed.returncode == 1, completed.stdout
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    _commands, _banner, end, finish = comparator._step_bounds(lines)
    census = next(index for index, line in enumerate(lines)
                  if line.startswith("CHILD PYTEST CENSUS: 1 "))
    assert any(lines[index] == "--- Logging error ---"
               for index in range(end, census)), completed.stdout
    assert end < census < finish, completed.stdout
    _header, finish_span = comparator._session_spans(lines)
    failures = comparator._census_failures(finish_span)
    assert [(failure["census"], failure["problems"]) for failure in failures] == [
        ("child_pytest", 1)], completed.stdout


SEEDED_RIG_CONFTEST = """\
import os

import randomly_seed_gate


def pytest_configure(config):
    with open(os.environ["SEEDED_RIG_LOADED"], "w") as handle:
        handle.write(str(randomly_seed_gate.randomly_loaded(config.pluginmanager)))
"""


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_a_real_seeded_session_is_read_by_the_comparator_and_the_seed_gate(tmp_path, parallel):
    """A real pytest-randomly session started with a seed, serial and under
    xdist: the comparator's own header parsers must read that seed from its
    real terminal output, and the seed gate must find the plugin loaded. The
    child starts as `python -c "...pytest.main(...)"`, which the child-pytest
    census leaves unjudged (its blind spot 3), rooted outside the repository, so
    neither the census nor the seed gate loads in it. CI installs the lock, so
    a CI run without pytest-randomly fails here instead of skipping."""
    if importlib.util.find_spec("pytest_randomly") is None:
        reason = "pytest-randomly is not importable, so no real seeded session can run"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"{reason}; CI installs the lock, so this arm must run there")
        pytest.skip(f"{reason}; CI installs the lock and runs this arm")
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (rig / "conftest.py").write_text(SEEDED_RIG_CONFTEST, encoding="utf-8")
    (rig / "test_two.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8")
    loaded = tmp_path / "loaded.txt"
    env = child_env()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(Path(__file__).parent), env.get("PYTHONPATH")) if part)
    env["SEEDED_RIG_LOADED"] = str(loaded)
    completed = subprocess.run(
        [sys.executable, "-c", "import sys, pytest; sys.exit(pytest.main(sys.argv[1:]))",
         "-ra", "-p", "no:cacheprovider", "-p", "randomly", "--randomly-seed=4242",
         "--randomly-dont-reset-seed", *parallel],
        cwd=rig, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    header, _finish = comparator._session_spans(lines)
    assert comparator._shuffle_seed(header) == 4242, completed.stdout
    assert loaded.read_text(encoding="utf-8") == "True", completed.stdout


CI_SHAPE_RIG_CONFTEST = """\
import os

import pytest

import randomly_seed_gate


def pytest_configure(config):
    randomly_seed_gate.refuse_unseeded(config)
    with open(os.environ["SEEDED_RIG_LOADED"], "w") as handle:
        handle.write(str(randomly_seed_gate.randomly_loaded(config.pluginmanager)))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with open(os.path.join(os.environ["SEEDED_RIG_ORDER"], worker), "w") as handle:
        handle.write(" ".join(item.name for item in items))
"""
CI_SHAPE_TESTS = [f"test_{index:02d}" for index in range(10)]


def _ci_seed_flags(seed):
    """The words tests.yml's run line adds for a seed: its
    `${PACT_SHUFFLE_SEED:+...}` expansion, read from ci_contract.WORKFLOW_TEST_CMD
    (which test_memory_init pins to the workflow), with the seed substituted."""
    words = shlex.split(ci_contract.WORKFLOW_TEST_CMD)
    opener = "${PACT_SHUFFLE_SEED:+"
    start = next(index for index, word in enumerate(words) if word.startswith(opener))
    end = next(index for index in range(start, len(words)) if words[index].endswith("}"))
    found = [words[start][len(opener):], *words[start + 1:end], words[end][:-1]]
    return [word.replace("$PACT_SHUFFLE_SEED", str(seed)) for word in found]


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_the_ci_shuffle_shape_runs_shuffled_with_its_seed(tmp_path, parallel):
    """CI's shuffled run as a real session: the rig's config blocks pytest-randomly
    as the root pyproject.toml does (`addopts = -p no:randomly`), and the command
    line adds exactly the words tests.yml adds for a seed. The run must load the
    plugin, pass the real seed gate, print the seed where the comparator reads
    it, and collect the tests in a shuffled order every process agrees on. The
    same rig without those words is the control: it must keep the plugin blocked
    and the file order, so the config's block is shown to be in force. The child
    starts as `python -c "...pytest.main(...)"` outside the repository, as the
    arm above does. CI installs the lock, so a CI run without pytest-randomly
    fails here instead of skipping."""
    if importlib.util.find_spec("pytest_randomly") is None:
        reason = "pytest-randomly is not importable, so no real seeded session can run"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"{reason}; CI installs the lock, so this arm must run there")
        pytest.skip(f"{reason}; CI installs the lock and runs this arm")
    seed = 4242
    flags = _ci_seed_flags(seed)
    assert flags[:2] == ["-p", "randomly"] and f"--randomly-seed={seed}" in flags, flags
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "pytest.ini").write_text("[pytest]\naddopts = -p no:randomly\n", encoding="utf-8")
    (rig / "conftest.py").write_text(CI_SHAPE_RIG_CONFTEST, encoding="utf-8")
    (rig / "test_order.py").write_text(
        "".join(f"def {name}():\n    pass\n\n\n" for name in CI_SHAPE_TESTS), encoding="utf-8")
    env = child_env()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(Path(__file__).parent), env.get("PYTHONPATH")) if part)

    def run(name, extra):
        loaded, order = tmp_path / f"{name}-loaded.txt", tmp_path / f"{name}-order"
        order.mkdir()
        completed = subprocess.run(
            [sys.executable, "-c", "import sys, pytest; sys.exit(pytest.main(sys.argv[1:]))",
             "-ra", "-p", "no:cacheprovider", *extra, *parallel],
            cwd=rig, env={**env, "SEEDED_RIG_LOADED": str(loaded), "SEEDED_RIG_ORDER": str(order)},
            capture_output=True, text=True, timeout=300, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        orders = {tuple(path.read_text(encoding="utf-8").split()) for path in order.iterdir()}
        assert len(orders) == 1, (orders, completed.stdout)
        return completed.stdout, loaded.read_text(encoding="utf-8"), list(orders.pop())

    stdout, loaded, order = run("seeded", flags)
    header, _finish = comparator._session_spans([ci_contract.WORKFLOW_TEST_CMD, *stdout.splitlines()])
    assert comparator._shuffle_seed(header) == seed, stdout
    assert loaded == "True", stdout
    assert sorted(order) == CI_SHAPE_TESTS and order != CI_SHAPE_TESTS, (order, stdout)
    _stdout, control_loaded, control_order = run("control", [])
    assert control_loaded == "False" and control_order == CI_SHAPE_TESTS, (
        control_loaded, control_order)


def test_two_seed_lines_are_refused(tmp_path):
    first = make_run(tmp_path, "one", seeds=(7, 8))
    second = make_run(tmp_path, "two", 101, seeds=(7,))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["status"] == "invalid"
    assert comparison["evidence_errors"] == [
        {"run": "one", "error": "more than one pytest-randomly seed line"}]
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [None, 7]


@pytest.mark.parametrize("mutation", [
    "literal_seed", "no_wrapper", "extra_token", "seed_after_junit", "wrong_junit",
    "previous_seeded_line", "dont_reset_outside_expansion", "other_variable",
    "names_mixed", "earlier_name"])
def test_seeded_shape_is_exact(tmp_path, mutation):
    """The second shape admits exactly four tokens in exactly one place."""
    seed_text = '${PACT_SHUFFLE_SEED:+-p randomly --randomly-seed="$PACT_SHUFFLE_SEED" --randomly-dont-reset-seed}'
    if mutation == "previous_seeded_line":
        changed = SEEDED_COMMAND.replace(" --randomly-dont-reset-seed}", "}")
    elif mutation == "dont_reset_outside_expansion":
        changed = SEEDED_COMMAND.replace(" --randomly-dont-reset-seed}",
                                         "} --randomly-dont-reset-seed")
    elif mutation == "literal_seed":
        changed = SEEDED_COMMAND.replace('--randomly-seed="$PACT_SHUFFLE_SEED"', "--randomly-seed=7")
    elif mutation == "no_wrapper":
        changed = SEEDED_COMMAND.replace(
            seed_text, '-p randomly --randomly-seed="$PACT_SHUFFLE_SEED" --randomly-dont-reset-seed')
    elif mutation == "other_variable":
        changed = SEEDED_COMMAND.replace("PACT_SHUFFLE_SEED", "OTHER_SEED")
    elif mutation == "earlier_name":
        changed = SEEDED_COMMAND.replace("PACT_SHUFFLE_SEED", "SEED")
    elif mutation == "names_mixed":
        changed = SEEDED_COMMAND.replace('--randomly-seed="$PACT_SHUFFLE_SEED"',
                                         '--randomly-seed="$SEED"')
    elif mutation == "extra_token":
        changed = SEEDED_COMMAND.replace("--junitxml=", "-x --junitxml=")
    elif mutation == "seed_after_junit":
        changed = ORDERED_COMMAND + " " + seed_text
    else:
        changed = SEEDED_COMMAND.replace(".full-suite-results.xml", ".other.xml")
    assert changed != SEEDED_COMMAND
    first = make_run(tmp_path, "one", command=changed)
    second = make_run(tmp_path, "two", 101, command=SEEDED_COMMAND)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, (mutation, comparison)
    assert "unexpected full-suite pytest argv" in (
        comparison["evidence_errors"][0]["error"]), (mutation, comparison)


def test_internal_error_comparison_still_records_each_seed(tmp_path, monkeypatch):
    """The invalid-comparison builder reports the same input_runs fields."""
    first = make_run(tmp_path, "one", seeds=(5,))
    second = make_run(tmp_path, "two", 101)
    normalize = comparator._normalize_run

    def crash_on_two(logical, directory, expected_sha):
        if logical == "two":
            raise RuntimeError("synthetic crash")
        return normalize(logical, directory, expected_sha)

    monkeypatch.setattr(comparator, "_normalize_run", crash_on_two)
    output = tmp_path / "out"
    code = comparator.main(["--expected-sha", SHA, "--output-dir", str(output),
                            f"--run=one={first}", f"--run=two={second}"])
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert code == 3
    assert comparison["status"] == "invalid"
    assert comparison["schema_version"] == 2
    assert [run["shuffle_seed"] for run in comparison["input_runs"]] == [5, None]


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
        index = next(index for index, line in enumerate(lines) if "XPASS " in line)
        lines.insert(index, actions_line("XPASS " + ALPHA + " - unexpected"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2
    assert comparison["status"] == "invalid"


# On CI pytest 9.2 prints each failure's whole message in the short test
# summary, untrimmed, one message line per log line after its FAILED entry
# (measured with CI=true, -ra, serial and -n 2; test_real_ci_short_summary_shape
# below re-measures it). These are message lines such a failure carried.
CI_FAILURE_MESSAGES = {
    "xpass-line": (f"XPASS {ALPHA}",),
    "separator-line": ("-----",),
    "both": ("-----", f"XPASS {ALPHA}", f"XPASS {BETA}"),
}


def _add_ci_failure_message(directory, message, entry=f"FAILED {BETA}"):
    """Add BETA's FAILED entry (or `entry`) and its message lines after the
    XPASS entries, where -ra prints errors and failures."""
    log = directory / "run.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    index = max(index for index, line in enumerate(lines) if "XPASS " in line) + 1
    lines[index:index] = [actions_line(text) for text in
                          (f"{entry} - RuntimeError: boom", *message)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("message", list(CI_FAILURE_MESSAGES))
def test_a_ci_failure_message_in_the_short_summary_keeps_the_red_run(tmp_path, message):
    """A failure message that holds an XPASS line or a separator-shaped line is
    still compared as a red run, with the real XPASS entry counted."""
    xpass = case(ALPHA, "xpass", "unexpected")
    first = make_run(tmp_path, "one", cases=[xpass, case(BETA)])
    second = make_run(tmp_path, "two", 101, cases=[xpass, case(BETA, "failure", "boom")])
    _add_ci_failure_message(second, CI_FAILURE_MESSAGES[message])
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 1, comparison
    assert comparison["status"] == "different", comparison
    assert comparison["evidence_errors"] == [], comparison
    run = read_runs(output)[1]
    assert run["validity"] == "test_outcome", run
    assert not any(delta["type"] == "xpass" for delta in comparison["deltas"]), comparison


# A child pytest's -ra report as a failing test's CI message carries it,
# its own short-summary header included (each row a different entry mix).
CHILD_REPORTS = {
    "failed-only": ("FAILED test_probe.py::test_child - x",),
    "skipped": ("SKIPPED [1] test_probe.py:3: a reason only the child knows",
                "FAILED test_probe.py::test_child - x"),
    "xfail-and-xpass": ("XFAIL test_probe.py::test_xfail - expected",
                        "XPASS test_probe.py::test_child",
                        "FAILED test_probe.py::test_other - x"),
}


TWO_HEADERS = ("the log holds 2 short test summary headers, so a child pytest's report "
               "printed in a test's output cannot be told from pytest's own summary")
CHILD_HEADER = "=" * 3 + " short test summary info " + "=" * 3


@pytest.mark.parametrize("child", list(CHILD_REPORTS))
@pytest.mark.parametrize("word, outcome", [("FAILED", "failure"), ("ERROR", "error")])
def test_a_child_report_in_a_failure_message_refuses_the_run(tmp_path, child, word, outcome):
    """A failure or error message holding a child pytest's whole -ra report
    carries a second short-summary header, after pytest's own. The log cannot
    tell which is pytest's, so even this valid red run is refused, for that
    cause."""
    xpass = case(ALPHA, "xpass", "unexpected")
    first = make_run(tmp_path, "one", cases=[xpass, case(BETA)])
    second = make_run(tmp_path, "two", 101, cases=[xpass, case(BETA, outcome, "boom")])
    _add_ci_failure_message(second, (CHILD_HEADER, *CHILD_REPORTS[child]),
                            entry=f"{word} {BETA}")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": TWO_HEADERS}], comparison


def _add_own_summary(directory, entries):
    """Put pytest's own short summary, holding `entries`, before the final
    summary line of a run that printed none."""
    log = directory / "run.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    index = next(index for index, line in enumerate(lines) if line.endswith(" in 0.10s ====="))
    lines[index:index] = [actions_line(text) for text in (SHORT_SUMMARY_TEXT, *entries)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_child_report_in_captured_output_refuses_the_run(tmp_path):
    """A child report a failing test printed sits in the FAILURES section,
    before pytest's own summary, and its header makes two: the run is refused
    even though pytest's own summary reads cleanly."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, cases=[case(ALPHA), case(BETA, "failure", "boom")],
                      captured=(CHILD_HEADER, "FAILED test_child.py::test_t - x"))
    _add_own_summary(second, (f"FAILED {BETA} - RuntimeError: boom",))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": TWO_HEADERS}], comparison


def test_an_unreadable_summary_with_a_child_report_in_a_failure_message_is_refused(tmp_path):
    """pytest's own summary names a skip reason the evidence lacks, and a
    failure message after it carries a child report with a clean FAILED line.
    The child's header is never read in its place: the run is refused for the
    two headers."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, cases=[case(ALPHA), case(BETA, "failure", "boom")])
    _add_own_summary(second, (
        "SKIPPED [1] tests/test_example.py:7: a reason the record does not hold",
        f"FAILED {BETA} - RuntimeError: child failed:", CHILD_HEADER,
        "FAILED test_child.py::test_t - x"))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": TWO_HEADERS}], comparison


@pytest.mark.parametrize("own_entries, error", [
    ((f"FAILED {BETA} - RuntimeError: boom",), None),
    (("SKIPPED [1] tests/test_example.py:7: a reason the record does not hold",
      f"FAILED {BETA} - RuntimeError: boom"),
     "a SKIPPED summary entry has no reason in the run's evidence: "
     "'SKIPPED [1] tests/test_example.py:7: a reason the record does not hold'"),
], ids=["reads", "unreadable"])
def test_a_single_short_summary_is_read_or_refused_for_its_own_reason(
        tmp_path, own_entries, error):
    """With one short-summary header, its summary is read: a clean one gives a
    compared red run, and one that does not read refuses the run for that."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101, cases=[case(ALPHA), case(BETA, "failure", "boom")])
    _add_own_summary(second, own_entries)
    completed, comparison, output = invoke(tmp_path, first, second)
    if error is None:
        assert completed.returncode == 1, comparison
        assert comparison["evidence_errors"] == [], comparison
        assert read_runs(output)[1]["validity"] == "test_outcome"
    else:
        assert completed.returncode == 2, comparison
        assert comparison["evidence_errors"] == [{"run": "two", "error": error}], comparison


def _add_summary_entries(directory, entries):
    """Put SKIPPED and XFAIL entries, each followed by the rest of its reason,
    at the top of the short test summary, where -ra prints them."""
    log = directory / "run.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    index = next(index for index, line in enumerate(lines)
                 if line.endswith(SHORT_SUMMARY_TEXT)) + 1
    lines[index:index] = [actions_line(text) for text in entries]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Each reason continues on lines that read like summary entries, as pytest
# prints them under CI=true (test_real_ci_short_summary_shape measures it).
MULTILINE_REASONS = {
    "skip-xpass": ("skip", f"first line\nXPASS {ALPHA}"),
    "skip-failed": ("skip", f"first line\nFAILED {ALPHA} - x"),
    "xfail-xpass": ("xfail", f"expected\nXPASS {ALPHA}"),
    "xfail-error": ("xfail", f"expected\nERROR {ALPHA} - y\nXPASS {ALPHA}"),
}


def _multiline_reason_run(root, name, run_id, kind, reason):
    nodes = [ALPHA, BETA, GAMMA]
    directory = make_run(root, name, run_id, nodes=nodes,
                         cases=[case(ALPHA, "xpass", "unexpected"), case(BETA, kind, reason),
                                case(GAMMA)])
    first, *rest = reason.split("\n")
    entry = (f"SKIPPED [1] tests/test_example.py:7: {first}" if kind == "skip"
             else f"XFAIL {BETA} - {first}")
    _add_summary_entries(directory, (entry, *rest))
    return directory


@pytest.mark.parametrize("reason", list(MULTILINE_REASONS))
def test_a_multiline_skip_or_xfail_reason_adds_no_xpass_entry(tmp_path, reason):
    """A skip or xfail reason line that starts with 'XPASS ', 'ERROR ' or
    'FAILED ' is passed over by the reason's length in the run's evidence, so a
    good run with one real xpass is compared as good."""
    kind, text = MULTILINE_REASONS[reason]
    first = _multiline_reason_run(tmp_path, "one", 100, kind, text)
    second = _multiline_reason_run(tmp_path, "two", 101, kind, text)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical", comparison
    assert [run["validity"] for run in comparison["input_runs"]] == ["clean", "clean"]
    assert [run["totals"]["xpassed"] for run in read_runs(output)] == [1, 1]


@pytest.mark.parametrize("entry, error", [
    ("SKIPPED [1] tests/test_example.py:7: a reason the record does not hold",
     "a SKIPPED summary entry has no reason in the run's evidence: "
     "'SKIPPED [1] tests/test_example.py:7: a reason the record does not hold'"),
    (f"XFAIL {GAMMA} - expected",
     f"a XFAIL summary entry has no reason in the run's evidence: "
     f"'XFAIL {GAMMA} - expected'"),
], ids=["skip", "xfail"])
def test_a_summary_entry_whose_reason_is_unknown_is_refused(tmp_path, entry, error):
    """An entry whose reason the run's own evidence does not hold cannot be
    measured, so the run is refused rather than read."""
    first = make_run(tmp_path, "one", cases=[case(ALPHA, "xpass", "unexpected"), case(BETA)])
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA, "xpass", "unexpected"), case(BETA)])
    _add_summary_entries(second, (entry,))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [{"run": "two", "error": error}], comparison


def test_an_xpass_reason_continuing_with_an_xpass_line_is_refused(tmp_path):
    """An XPASS entry's own reason is not in the run's evidence, so a reason line
    that starts 'XPASS <node>' is counted and the count check refuses the run;
    it is never compared as a different outcome."""
    first = make_run(tmp_path, "one", cases=[case(ALPHA, "xpass", "unexpected"), case(BETA)])
    second = make_run(tmp_path, "two", 101,
                      cases=[case(ALPHA, "xpass", f"unexpected\nXPASS {BETA}"), case(BETA)])
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "raw XPASS evidence count/uniqueness mismatch: 2 lines, "
                                "record xpassed=1"}], comparison


def test_the_copied_junit_escape_matches_pytests_own():
    """The comparator rewrites a terminal reason the way junitxml rewrites it
    before matching; every code point must come out as pytest writes it."""
    from _pytest.junitxml import bin_xml_escape
    text = "".join(map(chr, range(0x110000)))
    assert comparator._junit_escape(text) == bin_xml_escape(text)


def test_the_log_line_breaks_are_the_ones_splitlines_uses():
    """A reason continues over one log line per break the log reader splits at."""
    text = "".join(map(chr, range(0x110000))) + "\r\nend"
    assert comparator._LINE_BREAK.split(text) == text.splitlines()


# Each reason as the terminal prints it (raw), as junitxml writes it (the
# leading 'reason: ' removed, characters XML cannot hold escaped), and the
# log text the entry occupies (measured on pytest 9.2, CI=true, serial and
# -n 2). The log reader splits that text where str.splitlines does.
JUNIT_REWRITTEN_REASONS = {
    "reason-prefix": ("xfail", "legacy wording", "reason: legacy wording"),
    "control-character": ("xfail", f"bell#x07here\nXPASS {ALPHA}",
                          f"bell\x07here\nXPASS {ALPHA}"),
    "vertical-tab": ("xfail", f"vt#x0BXPASS {ALPHA}", f"vt\x0bXPASS {ALPHA}"),
    "line-separator": ("xfail", f"ls\u2028XPASS {ALPHA}", f"ls\u2028XPASS {ALPHA}"),
    "skip-form-feed": ("skip", f"ff#x0CXPASS {ALPHA}", f"ff\x0cXPASS {ALPHA}"),
}


def _junit_rewritten_run(root, name, run_id, kind, junit_text, terminal_text):
    """A good run with one real xpass whose skip or xfail reason junitxml
    rewrote: the record and the log carry the reason raw, JUnit its form."""
    directory = make_run(root, name, run_id, nodes=[ALPHA, BETA, GAMMA],
                         cases=[case(ALPHA, "xpass", "unexpected"),
                                case(BETA, kind, "JUNIT-PLACEHOLDER"), case(GAMMA)])
    junit = directory / "artifact" / ".full-suite-results.xml"
    junit.write_text(junit.read_text(encoding="utf-8").replace(
        "JUNIT-PLACEHOLDER", junit_text.replace("\n", "&#10;")), encoding="utf-8")
    record_path = directory / "artifact" / ".full-run-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for site in record["skip_sites"]:
        if site["reason"] == "JUNIT-PLACEHOLDER":
            site["reason"] = terminal_text
    write_json(record_path, record)
    entry = (f"SKIPPED [1] tests/test_example.py:7: {terminal_text}" if kind == "skip"
             else f"XFAIL {BETA} - {terminal_text}")
    log = directory / "run.log"
    lines = log.read_text(encoding="utf-8").split("\n")
    index = next(index for index, line in enumerate(lines)
                 if line.endswith(SHORT_SUMMARY_TEXT)) + 1
    # One physical log line per '\n' the terminal wrote; any other break stays
    # inside the line, where the log reader splits it.
    lines[index:index] = [actions_line(part) for part in entry.split("\n")]
    log.write_text("\n".join(lines), encoding="utf-8")
    return directory


@pytest.mark.parametrize("shape", list(JUNIT_REWRITTEN_REASONS))
def test_a_reason_junitxml_rewrote_is_still_matched(tmp_path, shape):
    """A reason junitxml stripped or escaped is put in junitxml's form on the
    terminal side, so the run is compared as good with its one real xpass."""
    kind, junit_text, terminal_text = JUNIT_REWRITTEN_REASONS[shape]
    first = _junit_rewritten_run(tmp_path, "one", 100, kind, junit_text, terminal_text)
    second = _junit_rewritten_run(tmp_path, "two", 101, kind, junit_text, terminal_text)
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical", comparison
    assert [run["totals"]["xpassed"] for run in read_runs(output)] == [1, 1]


def test_an_xfail_reason_its_junit_form_does_not_hold_is_refused(tmp_path):
    first = _junit_rewritten_run(tmp_path, "one", 100, "xfail", "legacy wording",
                                 "reason: legacy wording")
    second = _junit_rewritten_run(tmp_path, "two", 101, "xfail", "legacy wording",
                                  "other wording")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert comparison["evidence_errors"] == [
        {"run": "two", "error": "a XFAIL summary entry has no reason in the run's "
                                f"evidence: 'XFAIL {BETA} - other wording'"}], comparison


JUNIT_REWRITE_RIG = """\
import pytest


@pytest.mark.xfail(reason="reason: legacy wording")
def test_prefixed():
    assert False


@pytest.mark.xfail(reason="bell\\x07here\\nXPASS test_rig.py::test_xpass")
def test_control():
    assert False


@pytest.mark.xfail(reason="vt\\x0bXPASS test_rig.py::test_xpass")
def test_vertical_tab():
    assert False


@pytest.mark.xfail(reason="ls\\u2028XPASS test_rig.py::test_xpass")
def test_line_separator():
    assert False


@pytest.mark.skip(reason=SKIP_REASON)
def test_skip():
    pass


@pytest.mark.xfail(reason="unexpected")
def test_xpass():
    pass
"""
JUNIT_REWRITE_SKIP_REASON = "ff\x0cXPASS test_rig.py::test_xpass"


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_reasons_junitxml_rewrites_are_matched(tmp_path, parallel):
    """Real pytest under CI=true: xfail reasons with a 'reason: ' prefix, a
    control character and line breaks splitlines honours, and a skip reason
    with a form feed. The comparator reads the JUnit xfail reasons, the skip
    reason as the run record holds it, and only the one real XPASS entry."""
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "test_rig.py").write_text(
        f"SKIP_REASON = {JUNIT_REWRITE_SKIP_REASON!r}\n\n" + JUNIT_REWRITE_RIG,
        encoding="utf-8")
    junit = tmp_path / "junit.xml"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", f"--junitxml={junit}", *parallel],
        cwd=rig, env={**child_env(), "CI": "true"}, capture_output=True, text=True,
        timeout=300, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    report = comparator._test_text_range(lines, len(lines) - 1)
    summary = [lines[index] for index in comparator._short_summary_range(lines, report)]
    cases = comparator._parse_junit(junit)
    xfail_reasons = {f"test_rig.py::{case['name']}": [case["reason"]]
                     for case in cases if case["outcome"] == "xfail"}
    assert xfail_reasons["test_rig.py::test_prefixed"] == ["legacy wording"], xfail_reasons
    assert "#x07" in xfail_reasons["test_rig.py::test_control"][0], xfail_reasons
    assert sum(line.startswith("XPASS ") for line in summary) == 5, summary
    assert comparator._xpass_lines(summary, [JUNIT_REWRITE_SKIP_REASON], xfail_reasons) == [
        "test_rig.py::test_xpass - unexpected"], summary


CI_SHAPE_RIG = """\
import pytest


@pytest.mark.skip(reason="a plain skip")
def test_skipped():
    pass


@pytest.mark.skip(reason="first line\\nXPASS test_rig.py::test_xpass\\nFAILED test_rig.py::test_xpass - x")
def test_skipped_over_lines():
    pass


def test_skipped_at_call():
    pytest.skip("called\\nXPASS test_rig.py::test_xpass")


@pytest.mark.xfail(reason="expected")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="expected\\nXPASS test_rig.py::test_xpass\\nERROR test_rig.py::test_xpass - y")
def test_xfail_over_lines():
    assert False


@pytest.mark.xfail(reason="unexpected")
def test_xpass():
    pass


def test_fails():
    raise RuntimeError("boom\\nXPASS test_rig.py::test_xpass\\n-----")


@pytest.fixture
def broken():
    raise RuntimeError("setup broke\\nXPASS test_rig.py::test_xpass")


def test_errors(broken):
    pass
"""


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_ci_short_summary_shape(tmp_path, parallel):
    """Real pytest under CI=true: -ra prints skips, xfails, xpasses, errors and
    failures in that order; skip and xfail reasons, and error and failure
    messages, run on untrimmed and unindented after their entry, a reason line
    looking like an entry; and the comparator reads only the one real XPASS
    entry, passing over each reason by its length in JUnit."""
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "test_rig.py").write_text(CI_SHAPE_RIG, encoding="utf-8")
    env = {**child_env(), "CI": "true"}
    junit = tmp_path / "junit.xml"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", f"--junitxml={junit}", *parallel],
        cwd=rig, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    report = comparator._test_text_range(lines, len(lines) - 1)
    summary = [lines[index] for index in comparator._short_summary_range(lines, report)]
    words = [line.split(" ", 1)[0] for line in summary]
    first_xpass = summary.index("XPASS test_rig.py::test_xpass - unexpected")
    assert set(words[:first_xpass]) == {"SKIPPED", "XFAIL", "XPASS", "FAILED", "ERROR"}, summary
    assert words[first_xpass + 1:] == ["ERROR", "XPASS", "FAILED", "XPASS", "-----"], summary
    cases = comparator._parse_junit(junit)
    skip_reasons = [case["reason"] for case in cases if case["outcome"] == "skip"]
    xfail_reasons = {f"test_rig.py::{case['name']}": [case["reason"]]
                     for case in cases if case["outcome"] == "xfail"}
    assert sum(reason.count("\n") for reason in skip_reasons) == 3, skip_reasons
    assert comparator._xpass_lines(summary, skip_reasons, xfail_reasons) == [
        "test_rig.py::test_xpass - unexpected"], summary


RA_ORDER_RIG = """\
import pytest


def test_a_fails():
    raise RuntimeError("boom")


@pytest.fixture
def broken():
    raise RuntimeError("setup broke")


def test_b_errors(broken):
    pass


@pytest.mark.xfail(reason="unexpected")
def test_c_xpass():
    pass


@pytest.mark.xfail(reason="expected")
def test_d_xfail():
    assert False


def test_e_skip():
    pytest.skip("skipped here")
"""


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_ra_entry_order(tmp_path, parallel):
    """Real pytest under CI=true prints -ra entries as skips, xfails, xpasses,
    errors, failures, whatever order the tests ran in. _xpass_lines reads only
    up to the first ERROR or FAILED entry, which rests on errors and failures
    coming last; a pytest that changes the order fails here."""
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "test_order.py").write_text(RA_ORDER_RIG, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", *parallel],
        cwd=rig, env={**child_env(), "CI": "true"}, capture_output=True, text=True,
        timeout=300, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    lines = completed.stdout.splitlines()
    header = lines.index(next(line for line in lines if SHORT_SUMMARY_TEXT in line))
    assert [line.split(" ", 1)[0] for line in lines[header + 1:-1]] == [
        "SKIPPED", "XFAIL", "XPASS", "ERROR", "FAILED"], completed.stdout


CHILD_REPORT_RIG = """\
CHILD = ("=" * 3 + " short test summary info " + "=" * 3 + "\\n"
         "SKIPPED [1] child.py:3: a reason only the child knows\\n"
         "FAILED child.py::test_t - x")


def test_carries():
    raise RuntimeError("child failed:\\n" + CHILD)


def test_prints():
    print(CHILD)
    raise RuntimeError("printed")
"""


@pytest.mark.parametrize("parallel", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_real_child_reports_refuse_the_run(tmp_path, parallel):
    """Real pytest under CI=true: a child report a failing test printed lands
    in the FAILURES section, before pytest's own summary, and one a failure
    message carries lands inside it, after a FAILED entry for that test. Each
    adds a short-summary header the log cannot tell from pytest's own, so the
    run is refused for its three headers."""
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "test_rig.py").write_text(CHILD_REPORT_RIG, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", *parallel],
        cwd=rig, env={**child_env(), "CI": "true"}, capture_output=True, text=True,
        timeout=300, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    report = comparator._test_text_range(lines, len(lines) - 1)
    summaries = [[lines[index] for index in summary]
                 for summary in comparator._short_summary_ranges(lines, report)]
    assert len(summaries) == 3, completed.stdout
    assert sorted(line.split(" - ", 1)[0] for line in summaries[1]
                  if line.startswith("FAILED test_rig.py::")) == [
        "FAILED test_rig.py::test_carries", "FAILED test_rig.py::test_prints"], summaries[1]
    with pytest.raises(comparator.EvidenceError, match="the log holds 3 short test summary"):
        comparator._apply_xpass([], summaries, [], {"xpassed": 0, "skip_sites": []})


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
    env = {**child_env(), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for key in ("GITHUB_ACTIONS", "GITHUB_WORKSPACE", "PACT_FULL_RUN_RECORD_OWNER"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", f"--junitxml={xml_path}"],
        cwd=child, env=env, capture_output=True, text=True, timeout=300, check=False)
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
    # In real pytest output the XPASS line sits in the short test summary, the
    # one part of the report sections the comparator reads XPASS lines from.
    real_lines = [ci_contract.WORKFLOW_TEST_CMD, *completed.stdout.splitlines()]
    summary_index = next(index for index, line in enumerate(real_lines)
                         if "1 xpassed" in line and "1 failed" in line)
    report = comparator._test_text_range(real_lines, summary_index)
    short_summary = comparator._short_summary_range(real_lines, report)
    assert [real_lines[index] for index in short_summary
            if real_lines[index].startswith("XPASS ")] == [xpass_line]

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
        # The real XPASS and summary lines replace the synthetic ones in place:
        # XPASS in the short test summary, the summary line after it, and the
        # certifying step's verdict after that, as a real log orders them.
        lines = [actions_line(xpass_line) if "XPASS " in line
                 else actions_line(summary) if "0.10s" in line
                 else line for line in lines]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    assert totals["failed"] == 1  # Keep the real red-run accounting explicit.
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert run["validity"] == "test_outcome"
    assert Counter(outcome["outcome"] for entry in run["cases"]
                   for outcome in entry["outcomes"]) == {
                       "failure": 1, "error": 1, "xpass": 1}


def test_real_pytest_xfail_teardown_shape_needs_no_pass_accounting(tmp_path):
    """Real pytest 9.2, xfail item with a broken fixture teardown: the
    teardown failure never reaches the XML as an <error>. The expected
    failure emits TWO pytest.xfail children on one element (call + teardown
    reports), the unexpected pass emits one plus the raw XPASS line, and the
    run counts them xfailed — so the aggregates reconcile with NO missing-
    pass accounting and no teardown <error> exists to account."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_xfail_teardown.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def broken_teardown():\n"
        "    yield\n"
        "    raise RuntimeError('teardown exploded')\n"
        "@pytest.mark.xfail(reason='expected to fail')\n"
        "def test_xfail_unexpected_pass(broken_teardown):\n"
        "    pass\n"
        "@pytest.mark.xfail(reason='expected to fail')\n"
        "def test_xfail_expected_fail(broken_teardown):\n"
        "    raise AssertionError('xfail body')\n"
        "def test_plain_pass():\n"
        "    assert True\n", encoding="utf-8")
    xml_path = child / "results.xml"
    env = {**child_env(), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for key in ("GITHUB_ACTIONS", "GITHUB_WORKSPACE", "PACT_FULL_RUN_RECORD_OWNER"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", f"--junitxml={xml_path}"],
        cwd=child, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    root = ET.parse(xml_path).getroot()
    emitted = list(root.iter("testcase"))
    assert [element.get("name") for element in emitted] == [
        "test_xfail_unexpected_pass", "test_xfail_expected_fail",
        "test_plain_pass"]
    children = [child for element in emitted for child in element]
    assert [child.tag for child in children] == ["skipped"] * 3
    assert all(child.get("type") == "pytest.xfail" for child in children)
    xpass_line = next(line for line in (completed.stdout + completed.stderr)
                      .splitlines() if line.startswith("XPASS "))
    assert xpass_line == ("XPASS test_xfail_teardown.py::"
                          "test_xfail_unexpected_pass - expected to fail")
    nodes = ["test_xfail_teardown.py::test_xfail_unexpected_pass",
             "test_xfail_teardown.py::test_xfail_expected_fail",
             "test_xfail_teardown.py::test_plain_pass"]
    cases = [case(nodes[0], "xfail", "expected to fail"),
             case(nodes[1], "xfail", "expected to fail"),
             case(nodes[1], "xfail", "expected to fail"),
             case(nodes[0], "xpass", "expected to fail"),
             case(nodes[2])]
    first = make_run(tmp_path, "one", nodes=nodes, cases=cases)
    second = make_run(tmp_path, "two", 101, nodes=nodes, cases=cases)
    for directory in (first, second):
        (directory / "artifact" / ".full-suite-results.xml").write_bytes(
            xml_path.read_bytes())
    completed, comparison, output = invoke(tmp_path, first, second)
    assert completed.returncode == 0, comparison
    run = json.loads((output / "runs.jsonl").read_text().splitlines()[0])
    assert Counter(outcome["outcome"] for entry in run["cases"]
                   for outcome in entry["outcomes"]) == {
                       "xfail": 3, "xpass": 1, "pass": 1}


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

    ``test_imperative_xfail_then_teardown_error`` is the shape that reaches
    ``_teardown_after_pass_count``'s xfail guard: an imperative ``pytest.xfail()``
    whose teardown errors emits ``[skipped/pytest.xfail, error]`` on one
    element, while a plain ``@pytest.mark.xfail`` mark emits two skips and no
    error, so the guard stays untaken. Every failure message below carries the
    script actually loaded.
    """
    child = tmp_path / "child"
    child.mkdir()
    tests_dir = Path(__file__).resolve().parent
    (child / "conftest.py").write_text(
        "def pytest_configure(config):\n"
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
        "def test_xpass_then_teardown_error(boom):\n    assert True\n\n\n"
        "def test_imperative_xfail_then_teardown_error(boom):\n"
        '    pytest.xfail("imperative")\n',
        encoding="utf-8")
    (child / "test_modskip.py").write_text(
        'import pytest\n\npytest.skip("module skip", allow_module_level=True)\n\n\n'
        "def test_never():\n    assert True\n",
        encoding="utf-8")
    workspace = tmp_path / "rig"
    workspace.mkdir()
    xml = workspace / ".full-suite-results.xml"
    where = f"comparator script actually loaded: {SCRIPT}"
    env = {**child_env(), "GITHUB_ACTIONS": "true",
           "GITHUB_WORKSPACE": str(workspace), "GITHUB_RUN_ID": "200",
           "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": SHA,
           "PYTHONPATH": str(tests_dir)}
    env.pop("PACT_FULL_RUN_RECORD_OWNER", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-ra", "-p", "no:cacheprovider",
         "-p", "no:randomly", f"--junitxml={xml}"],
        cwd=child, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 1, (
        completed.stdout + completed.stderr + "\n" + where)
    stdout_lines = completed.stdout.splitlines()
    banner_index = next(index for index, line in enumerate(stdout_lines)
                        if comparator.SESSION_BANNER.fullmatch(line))
    header_end = stdout_lines.index("", banner_index)
    real_header = stdout_lines[banner_index:header_end + 1]
    record = json.loads((workspace / ".full-run-record.json").read_text(encoding="utf-8"))
    assert (record["collected"], record["passed"], record["failed"],
            record["errors"], record["skipped"], record["skipped_at_collection"],
            record["xfailed"], record["xpassed"]) == (9, 2, 1, 7, 2, 1, 4, 1), (
                record, where)
    # The triple the teardown-after-pass reconciliation keys on: the imperative
    # xfail contributes an xfail and an error, never a pass.
    assert (record["passed"], record["errors"], record["xfailed"]) == (2, 7, 4), (
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
    assert typed[("test_shapes", "test_imperative_xfail_then_teardown_error")] == [
        ("skipped", "pytest.xfail"), ("error", None)], (typed, where)

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
            actions_line(ci_contract.WORKFLOW_TEST_CMD),
            # The real child's own banner and report header, so the header
            # anchor is exercised against pytest's actual output.
            *map(actions_line, real_header),
            actions_line("CI environment matches constraints-ci.txt."),
            actions_line(f"FULL-COLLECTION-OK 2 test files and "
                         f"{run_record['collected']} items match discovery; subject={SHA}"),
            # The real XPASS lines, under the short test summary header -ra
            # prints them beneath.
            actions_line(SHORT_SUMMARY_TEXT),
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


def test_junit_with_a_wide_encoding_is_refused(tmp_path):
    """A wide encoding hides an ASCII DOCTYPE behind interleaved NUL bytes.

    The entity rewrites a name the mapping otherwise validates, so an unguarded
    parser expands it and reconciles (exit 0) while the guard refuses: the arm
    reddens on the unfixed code rather than merely failing to parse.
    """
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    (first / "artifact" / ".full-suite-results.xml").write_bytes(
        ('<?xml version="1.0" encoding="UTF-16"?>\n'
         '<!DOCTYPE testsuites [<!ENTITY n "test_alpha">]>\n'
         '<testsuites><testsuite name="pytest">'
         '<testcase classname="tests.test_example" name="&n;" time="0.01"/>'
         '<testcase classname="tests.test_example" name="test_beta" time="0.01"/>'
         '</testsuite></testsuites>').encode("utf-16"))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "not UTF-8" in comparison["evidence_errors"][0]["error"]


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_wide_encoding_with_an_entity_is_refused(tmp_path, encoding):
    """A BOM-less wide document hides the DOCTYPE from the byte-order-mark
    tuple AND from the ASCII byte scan, yet expat still auto-detects it. The
    NUL-byte refusal must fire; the arm above encodes WITH a BOM, so it cannot
    kill this gap."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    (first / "artifact" / ".full-suite-results.xml").write_bytes(
        ('<?xml version="1.0" encoding="UTF-16"?>\n'
         '<!DOCTYPE testsuites [<!ENTITY n "test_alpha">]>\n'
         '<testsuites><testsuite name="pytest">'
         '<testcase classname="tests.test_example" name="&n;" time="0.01"/>'
         '<testcase classname="tests.test_example" name="test_beta" time="0.01"/>'
         '</testsuite></testsuites>').encode(encoding))
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "NUL byte" in comparison["evidence_errors"][0]["error"]


def test_junit_artifact_size_bound_is_exact(tmp_path):
    """The bound is a named constant: exactly at it is accepted, one byte over
    is refused with exit 2. The accepted arm reads directly; the refused arm
    goes through the CLI so the exit code is part of the contract."""
    first = make_run(tmp_path, "one")
    artifact = first / "artifact" / ".full-suite-results.xml"
    prologue = b'<?xml version="1.0" encoding="utf-8"?><testsuites/>'
    at_limit = prologue + b" " * (comparator.MAX_JUNIT_BYTES - len(prologue))
    artifact.write_bytes(at_limit)
    assert comparator._junit_bytes(artifact) == at_limit
    artifact.write_bytes(at_limit + b" ")
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert str(comparator.MAX_JUNIT_BYTES) in (
        comparison["evidence_errors"][0]["error"])


@pytest.mark.parametrize("relative,limit", [
    ("metadata.json", "MAX_JSON_BYTES"),
    ("artifact/.full-run-record.json", "MAX_JSON_BYTES"),
    ("artifact/.full-collection-record.json", "MAX_JSON_BYTES"),
    ("run.log", "MAX_LOG_BYTES"),
])
def test_text_evidence_size_bounds_are_exact(tmp_path, relative, limit):
    """Every evidence read is bounded like the JUnit artifact, not just the
    XML: the three JSON files and the merged log. Exactly at the bound is
    accepted (trailing whitespace is inert in both formats); one byte over is
    refused with exit 2, and the refusal names the file and the bound."""
    bound = getattr(comparator, limit)
    reader = (comparator._json_object if limit == "MAX_JSON_BYTES"
              else comparator._log_lines)
    first = make_run(tmp_path, "one")
    path = first / relative
    base = path.read_bytes()
    at_limit = base + b" " * (bound - len(base))
    assert len(at_limit) == bound
    path.write_bytes(at_limit)
    reader(path)  # accepted at the bound
    path.write_bytes(at_limit + b" ")
    second = make_run(tmp_path, "two", 101)
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    error = comparison["evidence_errors"][0]["error"]
    assert str(path) in error, error
    assert str(bound) in error, error


def test_deeply_nested_json_evidence_is_refused(tmp_path):
    """Pathological JSON nesting is malformed evidence (2), never a fault (3)."""
    first = make_run(tmp_path, "one")
    second = make_run(tmp_path, "two", 101)
    (first / "metadata.json").write_text("[" * 200000 + "]" * 200000,
                                         encoding="utf-8")
    completed, comparison, _ = invoke(tmp_path, first, second)
    assert completed.returncode == 2, comparison
    assert "unreadable JSON" in comparison["evidence_errors"][0]["error"]
    assert "internal_errors" not in comparison


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
        # -ra prints the XPASS line in the short test summary, under its header.
        lines[index:index] = [actions_line(SHORT_SUMMARY_TEXT),
                              actions_line(f"XPASS {BETA} - expected")]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def _rewrite_failure_child(directory, message, *, keep_type=False):
    artifact = directory / "artifact" / ".full-suite-results.xml"
    tree = ET.parse(artifact)
    for child in list(tree.find(".//testcase")):
        child.tag = "skipped"
        child.set("message", message)
        if not keep_type:
            child.attrib.pop("type", None)
    tree.write(artifact, encoding="utf-8", xml_declaration=True)


def test_no_type_xfail_skip_message_is_a_failure_not_a_refusal(tmp_path):
    """A foreign or older pytest may emit a no-type <skipped> with this exact
    message for a FAILED report carrying wasxfail; the pinned 9.2 emits a
    <failure message="[XPASS(strict)]"> instead. Such a report is counted as
    failed, never xpassed, so it reconciles as a failure."""
    def build(name, run_id):
        directory = make_run(tmp_path, name, run_id, nodes=[ALPHA],
                             cases=[case(ALPHA, "failure")])
        _rewrite_failure_child(directory, "xfail-marked test passes unexpectedly")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 0, comparison
    assert comparison["status"] == "identical"


def test_unknown_no_type_skip_is_still_refused(tmp_path):
    """The message-specific mapping must not swallow every no-type skip."""
    def build(name, run_id):
        directory = make_run(tmp_path, name, run_id, nodes=[ALPHA],
                             cases=[case(ALPHA, "failure")])
        _rewrite_failure_child(directory, "some future skip form")
        return directory

    completed, comparison, _ = invoke(tmp_path, build("one", 100), build("two", 101))
    assert completed.returncode == 2, comparison
    assert "unknown skip type" in comparison["evidence_errors"][0]["error"]


def test_ambient_script_override_cannot_redirect_the_subject(tmp_path):
    """The module's subject is fixed; an ambient env value must not change it."""
    decoy = tmp_path / "decoy.py"
    decoy.write_text("raise SystemExit('decoy loaded')\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-c",
         "import test_compare_full_suite_results as module; print(module.SCRIPT)"],
        cwd=Path(__file__).parent,
        env={**os.environ, "PACT_COMPARE_FULL_SUITE_SCRIPT": str(decoy)},
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(SCRIPT)


def test_module_skips_loudly_outside_the_repo_layout(tmp_path):
    """Where the repo-level script does not ship, skip with a named reason."""
    layout = tmp_path / "pact-plugin" / "tests"
    layout.mkdir(parents=True)
    copied = layout / Path(__file__).name
    shutil.copy(Path(__file__), copied)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(copied), "-q", "-rA",
         "--noconftest", "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=layout, env=child_env(), capture_output=True, text=True, timeout=300, check=False)
    output = completed.stdout + completed.stderr
    # 5 is pytest's "no tests collected": the module skipped instead of failing
    # to import. A missing script used to abort collection with exit 2.
    assert completed.returncode == 5, output
    assert "repo-level comparator not present" in output
