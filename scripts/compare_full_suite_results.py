#!/usr/bin/env python3
"""Compare diagnostic full-suite outcomes without replacing CI certification.

Location: scripts/compare_full_suite_results.py
Used by: development evidence collection for the fixed-SHA full-suite matrix.
Reads downloaded metadata, raw logs, schema-v3 records, and JUnit artifacts;
writes runs.jsonl and comparison.json under --output-dir.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import xml.etree.ElementTree as ET

WORKFLOW_DATABASE_ID = 291463576
SUMMARY_OUTCOME = re.compile(
    r"\b(?:passed|failed|errors?|skipped|xfailed|xpassed|deselected|no tests ran)\b")
SUMMARY_TIME = re.compile(r"\bin \d+(?:\.\d+)?s\b")
TIMESTAMP_TEXT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z "
OPTIONAL_BOM = re.escape(chr(0xFEFF)) + "?"
TIMESTAMP = re.compile("^" + OPTIONAL_BOM + TIMESTAMP_TEXT)
ACTIONS_ENVELOPE = re.compile(
    r"^[^\t\r\n]*\t[^\t\r\n]*\t" + OPTIONAL_BOM + TIMESTAMP_TEXT)
SHA = re.compile(r"[0-9a-fA-F]{40}")
COUNT_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
RAW_PATHS = {"metadata": "metadata.json", "log": "run.log",
             "junit": "artifact/.full-suite-results.xml",
             "run_record": "artifact/.full-run-record.json",
             "collection_record": "artifact/.full-collection-record.json"}


class EvidenceError(ValueError):
    """The run cannot support an unambiguous comparison."""


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{path}: unreadable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path}: expected a JSON object")
    return value


def _integer(record: dict, key: str, *, minimum: int = 0) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise EvidenceError(f"{key} must be an integer >= {minimum}, got {value!r}")
    return value


def _log_lines(path: Path) -> list[str]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise EvidenceError(f"{path}: unreadable log: {exc}") from exc
    return [TIMESTAMP.sub("", ACTIONS_ENVELOPE.sub("", line, count=1), count=1)
            for line in raw_lines]


def _summary_text(line: str) -> str:
    return line.strip().strip("=").strip()


def _summary_identity(line: str) -> str:
    return re.sub(r"\s+in \d+(?:\.\d+)?s(?: \([^)]*\))?$", "", line)


def _junit_key(node_id: str) -> tuple[str, str]:
    try:
        from _pytest.junitxml import mangle_test_address
        parts = mangle_test_address(node_id)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise EvidenceError(f"cannot map terminal node ID {node_id!r}: {exc}") from exc
    if (not isinstance(parts, list) or not parts
            or any(not isinstance(part, str) or not part for part in parts)):
        raise EvidenceError(f"incompatible JUnit mapping for {node_id!r}: {parts!r}")
    return ".".join(parts[:-1]), parts[-1]


def _command_argv(lines: list[str]) -> list[str]:
    candidates = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##[group]Run "):
            stripped = stripped[len("##[group]Run "):]
        if stripped.startswith("python -m pytest "):
            try:
                candidates.append(shlex.split(stripped))
            except ValueError as exc:
                raise EvidenceError(f"unparseable pytest command: {exc}") from exc
    if (len(candidates) not in (1, 2)
            or len(candidates) == 2 and candidates[0] != candidates[1]):
        raise EvidenceError("expected one command, optionally repeated by the Actions group header")
    argv = candidates[0]
    if (len(argv) != 9 or argv[:7] != [
            "python", "-m", "pytest", "-ra", "-n", "4", "--dist"]
            or argv[7] not in ("loadfile", "load")
            or argv[8] != "--junitxml=$GITHUB_WORKSPACE/.full-suite-results.xml"):
        raise EvidenceError(f"unexpected full-suite pytest argv: {argv!r}")
    return argv


def _parse_junit(path: Path) -> list[dict]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise EvidenceError(f"{path}: unreadable JUnit XML: {exc}") from exc
    cases = []
    for element in root.iter("testcase"):
        classname, name = element.get("classname"), element.get("name")
        if not classname or not name:
            raise EvidenceError("every JUnit testcase needs classname and name")
        children = [child for child in element
                    if child.tag in ("failure", "error", "skipped")]
        if len(children) > 1:
            raise EvidenceError(f"{classname}.{name}: multiple outcome children")
        outcome, reason = "pass", None
        if children:
            child = children[0]
            if child.tag in ("failure", "error"):
                outcome = child.tag
            elif child.get("type") == "pytest.skip":
                outcome = "skip"
            elif child.get("type") == "pytest.xfail":
                outcome = "xfail"
            else:
                raise EvidenceError(f"{classname}.{name}: unknown skip type")
            if child.tag == "skipped":
                reason = child.get("message")
        try:
            duration = float(element.get("time", ""))
        except ValueError as exc:
            raise EvidenceError(f"{classname}.{name}: invalid duration") from exc
        if not math.isfinite(duration) or duration < 0:
            raise EvidenceError(f"{classname}.{name}: invalid duration")
        cases.append({"classname": classname, "name": name, "outcome": outcome,
                      "reason": reason, "duration": duration,
                      "file": element.get("file")})
    if not cases:
        raise EvidenceError("JUnit contains no testcase elements")
    return cases


def _xpass_lines(lines: list[str]) -> list[str]:
    return [line[len("XPASS "):] for line in lines if line.startswith("XPASS ")]


def _resolve_xpass(payload: str, terminal: list[str]) -> tuple[str, str | None]:
    if payload in terminal:
        return payload, None
    matches = [node_id for node_id in terminal
               if payload.startswith(node_id + " - ")]
    if not matches:
        raise EvidenceError(f"unmapped XPASS payload: {payload!r}")
    longest = max(len(node_id) for node_id in matches)
    matches = [node_id for node_id in matches if len(node_id) == longest]
    if len(matches) != 1:
        raise EvidenceError(f"ambiguous XPASS payload: {payload!r}")
    node_id = matches[0]
    return node_id, payload[len(node_id) + 3:]


def _apply_xpass(cases: list[dict], lines: list[str], terminal: list[str],
                 expected_count: int) -> None:
    payloads = _xpass_lines(lines)
    if len(payloads) != expected_count or len(set(payloads)) != len(payloads):
        raise EvidenceError(
            f"raw XPASS evidence count/uniqueness mismatch: {len(payloads)} lines, "
            f"record xpassed={expected_count}")
    by_key = {}
    for case in cases:
        by_key.setdefault((case["classname"], case["name"]), []).append(case)
    resolved = set()
    for payload in payloads:
        node_id, reason = _resolve_xpass(payload, terminal)
        if node_id in resolved:
            raise EvidenceError(f"duplicate XPASS node ID: {node_id}")
        resolved.add(node_id)
        key = _junit_key(node_id)
        candidates = [case for case in by_key.get(key, [])
                      if case["outcome"] == "pass"]
        if len(candidates) != 1:
            raise EvidenceError(
                f"XPASS {node_id!r} needs one unambiguous JUnit pass, "
                f"found {len(candidates)}")
        candidates[0]["outcome"] = "xpass"
        candidates[0]["reason"] = reason


def _validate_skip_sites(record: dict, cases: list[dict],
                         node_for_key: dict[tuple[str, str], str]) -> tuple[list[dict], list[dict]]:
    raw_sites = record.get("skip_sites")
    if not isinstance(raw_sites, list):
        raise EvidenceError("skip_sites must be a list")
    recorded = Counter()
    for site in raw_sites:
        if (not isinstance(site, dict) or not isinstance(site.get("file"), str)
                or not isinstance(site.get("reason"), str)):
            raise EvidenceError("invalid skip site")
        count = _integer(site, "count", minimum=1)
        recorded[(site["file"], site["reason"])] += count
    if sum(recorded.values()) != record["skipped"]:
        raise EvidenceError("skip_sites do not account for the record's skips")
    emitted = Counter()
    for case in cases:
        if case["outcome"] != "skip":
            continue
        key = (case["classname"], case["name"])
        path = node_for_key[key].split("::", 1)[0]
        emitted[(Path(path).as_posix(), case["reason"] or "")] += 1
    canonical_recorded = Counter()
    for (file, reason), count in recorded.items():
        candidates = [key for key in emitted
                      if key[1] == reason
                      and (key[0] == file or key[0].endswith("/" + file))]
        if len(candidates) > 1:
            raise EvidenceError(f"ambiguous skip-site path mapping for {file!r}")
        target = candidates[0] if candidates else (file, reason)
        canonical_recorded[target] += count
    recorded = canonical_recorded
    remaining = recorded - emitted
    if sum(remaining.values()) != record["skipped_at_collection"] or recorded != remaining + emitted:
        raise EvidenceError("JUnit skip files/reasons do not reconcile with skip_sites")
    remaining_sites = [{"file": file, "reason": reason, "count": count}
                       for (file, reason), count in sorted(remaining.items())]
    return raw_sites, remaining_sites


def _full_run_verifier(metadata: dict, lines: list[str], passed: bool) -> tuple[str, list[str]]:
    failures = [line for line in lines if line.startswith("RUN-VERIFY-FAILURE ")]
    if passed:
        return "passed", failures
    if failures:
        return "failed", failures
    for job in metadata.get("jobs", []):
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        for step in job["steps"]:
            if (isinstance(step, dict)
                    and step.get("name") == "Verify the certifying run"
                    and step.get("conclusion") == "skipped"):
                return "skipped", []
    return "missing", failures


def _normalize_run(logical: str, directory: Path, expected_sha: str) -> dict:
    normalized = {"logical_name": logical, "validity": "invalid",
                  "raw_paths": RAW_PATHS, "evidence_errors": []}
    metadata = _json_object(directory / "metadata.json")
    for key in ("attempt", "databaseId", "workflowDatabaseId"):
        _integer(metadata, key, minimum=1)
    if (metadata["attempt"] != 1
            or metadata["workflowDatabaseId"] != WORKFLOW_DATABASE_ID
            or metadata.get("event") != "workflow_dispatch"
            or metadata.get("headSha") != expected_sha
            or metadata.get("status") != "completed"):
        raise EvidenceError("metadata is not a completed attempt-1 dispatch on the expected SHA")
    if not isinstance(metadata.get("jobs"), list):
        raise EvidenceError("metadata jobs must be a list")
    artifact = directory / "artifact"
    record = _json_object(artifact / ".full-run-record.json")
    collection = _json_object(artifact / ".full-collection-record.json")
    if record.get("schema_version") != 3 or collection.get("schema_version") != 1:
        raise EvidenceError("unexpected run or collection record schema")
    if (record.get("run_id") != str(metadata["databaseId"])
            or collection.get("run_id") != record["run_id"]
            or record.get("run_attempt") != "1"
            or collection.get("run_attempt") != "1"):
        raise EvidenceError("API and in-band run identities disagree")
    if collection.get("subject") != expected_sha:
        raise EvidenceError("collection record is for a different SHA")
    for key in COUNT_KEYS + ("collected", "deselected", "exitstatus",
                             "items_before_modifyitems", "items_after_modifyitems",
                             "skipped_at_collection"):
        _integer(record, key)
    collected = _integer(record, "collected", minimum=1)
    _integer(collection, "collected_items", minimum=1)
    for key in ("collected_files", "discovered_files"):
        _integer(collection, key, minimum=1)
    terminal = record.get("terminal_node_ids")
    if (not isinstance(terminal, list)
            or any(not isinstance(node_id, str) or not node_id for node_id in terminal)
            or len(set(terminal)) != len(terminal)):
        raise EvidenceError("terminal_node_ids must be unique non-empty strings")
    if (collection["collected_items"] != collected
            or record["items_after_modifyitems"] != collected
            or record["items_before_modifyitems"]
            != collected + record["deselected"]
            or len(terminal) != collected):
        raise EvidenceError("collection, hook, and terminal evidence disagree")
    if record["deselected"]:
        raise EvidenceError("deselection makes the unrestricted evidence invalid")
    if not isinstance(record.get("summary_line"), str) or not record["summary_line"].strip():
        raise EvidenceError("run record has no summary line")
    if record["skipped_at_collection"] > record["skipped"]:
        raise EvidenceError("skipped_at_collection exceeds skipped")

    lines = _log_lines(directory / "run.log")
    argv = _command_argv(lines)
    summary = _summary_text(record["summary_line"])
    if not SUMMARY_OUTCOME.search(summary) or not SUMMARY_TIME.search(summary):
        raise EvidenceError("record summary is not a real pytest summary")
    summary_identity = _summary_identity(summary)
    summary_indexes = [index for index, line in enumerate(lines)
                       if SUMMARY_TIME.search(line)
                       and _summary_identity(_summary_text(line)) == summary_identity]
    if len(summary_indexes) != 1:
        raise EvidenceError("raw log does not contain exactly one matching summary")
    summary_index = summary_indexes[0]
    collection_marker = f"FULL-COLLECTION-OK "
    if not any(line.startswith(collection_marker)
               and line.endswith(f"subject={expected_sha}") for line in lines):
        raise EvidenceError("FULL-COLLECTION-OK on the expected SHA is absent")
    full_run_ok = [line for line in lines if line.startswith("FULL-RUN-OK ")]
    if any(not line.endswith(f"subject={expected_sha}") for line in full_run_ok):
        raise EvidenceError("FULL-RUN-OK names a different SHA")
    environment_indexes = [index for index, line in enumerate(lines)
                           if "CI environment matches constraints-ci.txt." in line]
    if (len(environment_indexes) < 2
            or min(environment_indexes) > summary_index
            or max(environment_indexes) < summary_index
            or any("CI ENVIRONMENT DRIFT" in line or "PYTEST_ADDOPTS is set" in line
                   for line in lines)):
        raise EvidenceError("before/after environment evidence is not clean")
    environment_passes = len(environment_indexes)

    cases = _parse_junit(artifact / ".full-suite-results.xml")
    node_for_key = {}
    for node_id in terminal:
        key = _junit_key(node_id)
        if key in node_for_key:
            raise EvidenceError(f"multiple terminal IDs map to JUnit key {key}")
        node_for_key[key] = node_id
    seen_keys = set()
    for case in cases:
        key = (case["classname"], case["name"])
        if key not in node_for_key:
            raise EvidenceError(f"unmapped JUnit case: {key}")
        seen_keys.add(key)
        case["node_id"] = node_for_key[key]
        emitted_file = case["file"]
        expected_file = Path(case["node_id"].split("::", 1)[0]).as_posix()
        if emitted_file is not None:
            emitted_path = Path(emitted_file).as_posix()
            if emitted_path != expected_file and not emitted_path.endswith("/" + expected_file):
                raise EvidenceError(f"JUnit file disagrees for {case['node_id']}")
    if seen_keys != set(node_for_key):
        raise EvidenceError("one or more terminal IDs have no JUnit case")
    _apply_xpass(cases, lines, terminal, record["xpassed"])
    outcome_counts = Counter(case["outcome"] for case in cases)
    expected_counts = {
        "pass": record["passed"], "failure": record["failed"],
        "error": record["errors"],
        "skip": record["skipped"] - record["skipped_at_collection"],
        "xfail": record["xfailed"], "xpass": record["xpassed"]}
    if any(outcome_counts[outcome] != count
           for outcome, count in expected_counts.items()):
        raise EvidenceError(
            f"JUnit aggregates {dict(outcome_counts)} != record {expected_counts}")
    skip_sites, collection_skip_sites = _validate_skip_sites(record, cases, node_for_key)

    verifier_state, verifier_failures = _full_run_verifier(
        metadata, lines, bool(full_run_ok))
    red = record["failed"] > 0 or record["errors"] > 0
    if red and (record["exitstatus"] == 0 or full_run_ok or verifier_state == "missing"):
        raise EvidenceError("red outcomes lack consistent exit and verifier evidence")
    if not red and record["exitstatus"] != 0:
        raise EvidenceError("nonzero exit without a test outcome is invalid infrastructure")
    clean = not red and record["exitstatus"] == 0
    if clean and (len(full_run_ok) != 1 or record["passed"] <= 0 or verifier_failures):
        raise EvidenceError("a clean run needs positive passes and one FULL-RUN-OK")
    normalized.update({
        "validity": "clean" if clean else "test_outcome",
        "identity": {"database_id": metadata["databaseId"],
                     "workflow_database_id": metadata["workflowDatabaseId"],
                     "attempt": 1, "head_sha": expected_sha,
                     "head_branch": metadata.get("headBranch"),
                     "url": metadata.get("url")},
        "argv": argv,
        "environment": {
            "passed_checks": environment_passes,
            "platform": [line for line in lines if line.startswith("platform ")],
            "measurements": [line for line in lines if "sys.executable=" in line],
            "runner": [{"name": job.get("name"), "runner_name": job.get("runnerName")}
                       for job in metadata.get("jobs", []) if isinstance(job, dict)],
            "pins": [line for line in lines if "constraints-ci.txt" in line],
            "cache": [line for line in lines if "cache" in line.lower()],
            "pytest_addopts_absent": True},
        "collection": {"collected": collected,
                       "collected_files": collection["collected_files"],
                       "discovered_files": collection["discovered_files"],
                       "deselected": record["deselected"],
                       "terminal_node_ids": terminal},
        "totals": {key: record[key] for key in COUNT_KEYS + ("exitstatus",)},
        "summary": summary, "skip_sites": skip_sites,
        "collection_skip_sites": collection_skip_sites,
        "cases": [{"classname": case["classname"], "name": case["name"],
                   "node_id": case["node_id"],
                   "outcomes": [{"outcome": case["outcome"], "reason": case["reason"],
                                 "duration": case["duration"]}]} for case in cases],
        "verifier_results": {"full_collection": True, "full_run": verifier_state,
                             "failures": verifier_failures},
    })
    return normalized


def _case_multiset(run: dict) -> dict[tuple[str, str], Counter]:
    result = {}
    for case in run["cases"]:
        key = (case["classname"], case["name"])
        outcomes = result.setdefault(key, Counter())
        for outcome in case["outcomes"]:
            outcomes[json.dumps({"outcome": outcome["outcome"],
                                 "reason": outcome["reason"]}, sort_keys=True)] += 1
    return result


def _outcome_list(counter: Counter) -> list[dict]:
    values = []
    for encoded, count in sorted(counter.items()):
        outcome = json.loads(encoded)
        outcome["count"] = count
        values.append(outcome)
    return values


def _outcome_totals(counter: Counter) -> Counter:
    totals = Counter()
    for encoded, count in counter.items():
        totals[json.loads(encoded)["outcome"]] += count
    return totals


def _duration_multiset(run: dict) -> Counter:
    return Counter(json.dumps({"classname": case["classname"], "name": case["name"],
                               "duration": outcome["duration"]}, sort_keys=True)
                   for case in run["cases"] for outcome in case["outcomes"])


def _compare(runs: list[dict], expected_sha: str, errors: list[dict]) -> dict:
    comparison = {"schema_version": 1, "expected_sha": expected_sha,
                  "input_runs": [{"logical_name": run["logical_name"],
                                  "identity": run.get("identity"),
                                  "validity": run["validity"]} for run in runs],
                  "status": "invalid" if errors else "identical",
                  "deltas": [], "observations": [], "evidence_errors": errors}
    if errors:
        return comparison
    reference, reference_cases = runs[0], _case_multiset(runs[0])
    reference_durations = _duration_multiset(runs[0])
    reference_nodes = set(reference["collection"]["terminal_node_ids"])
    for run in runs[1:]:
        label = run["logical_name"]
        nodes = set(run["collection"]["terminal_node_ids"])
        for kind, values in (("collection_added", nodes - reference_nodes),
                             ("collection_removed", reference_nodes - nodes)):
            if values:
                comparison["deltas"].append(
                    {"type": kind, "run": label, "node_ids": sorted(values)})
        cases = _case_multiset(run)
        for key in sorted(set(reference_cases) | set(cases)):
            before, after = reference_cases.get(key, Counter()), cases.get(key, Counter())
            if before == after:
                continue
            before_outcomes = _outcome_totals(before)
            after_outcomes = _outcome_totals(after)
            if any(json.loads(encoded)["outcome"] == "xpass"
                   for encoded in set(before) | set(after)
                   if before[encoded] != after[encoded]):
                kind = "xpass"
            elif before_outcomes == after_outcomes:
                kind = "skip_reason"
            else:
                kind = "outcome"
            comparison["deltas"].append({
                "type": kind, "run": label, "classname": key[0], "name": key[1],
                "before": _outcome_list(before), "after": _outcome_list(after)})
        if run["collection_skip_sites"] != reference["collection_skip_sites"]:
            comparison["deltas"].append({"type": "skip_sites", "run": label,
                                         "before": reference["collection_skip_sites"],
                                         "after": run["collection_skip_sites"]})
        if run["argv"] != reference["argv"]:
            comparison["observations"].append({"type": "argv", "run": label})
        if _duration_multiset(run) != reference_durations:
            comparison["observations"].append({"type": "duration", "run": label})
        if run["collection"]["terminal_node_ids"] != reference["collection"]["terminal_node_ids"]:
            comparison["observations"].append({"type": "ordering", "run": label})
        if run["environment"] != reference["environment"]:
            comparison["observations"].append({"type": "environment", "run": label})
    if comparison["deltas"]:
        comparison["status"] = "different"
    return comparison


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run", action="append", required=True,
                        metavar="LOGICAL=ABSOLUTE_DIRECTORY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not SHA.fullmatch(args.expected_sha):
        print("expected-sha must be 40 hexadecimal characters", file=sys.stderr)
        return 2
    if len(args.run) < 2:
        print("at least two --run arguments are required", file=sys.stderr)
        return 2
    specs, names = [], set()
    for value in args.run:
        logical, separator, raw_directory = value.partition("=")
        directory = Path(raw_directory)
        if not logical or not separator or not directory.is_absolute():
            print(f"invalid --run {value!r}", file=sys.stderr)
            return 2
        if logical in names:
            print(f"duplicate logical run name {logical!r}", file=sys.stderr)
            return 2
        names.add(logical)
        specs.append((logical, directory))
    runs, errors = [], []
    for logical, directory in specs:
        try:
            runs.append(_normalize_run(logical, directory, args.expected_sha.lower()))
        except EvidenceError as exc:
            error = {"run": logical, "error": str(exc)}
            errors.append(error)
            runs.append({"logical_name": logical, "validity": "invalid",
                         "raw_paths": RAW_PATHS, "evidence_errors": [str(exc)]})
    identities = {}
    for run in runs:
        database_id = run.get("identity", {}).get("database_id")
        if database_id is None:
            continue
        if database_id in identities:
            error = {"run": run["logical_name"],
                     "error": f"databaseId {database_id} is repeated across input runs"}
            errors.append(error)
            run["validity"] = "invalid"
            run["evidence_errors"] = [error["error"]]
            identities[database_id]["validity"] = "invalid"
            identities[database_id]["evidence_errors"] = [error["error"]]
        else:
            identities[database_id] = run
    comparison = _compare(runs, args.expected_sha.lower(), errors)
    try:
        _atomic_write(args.output_dir / "runs.jsonl", "".join(
            json.dumps(run, sort_keys=True) + "\n" for run in runs))
        _atomic_write(args.output_dir / "comparison.json",
                      json.dumps(comparison, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"cannot write comparison outputs: {exc}", file=sys.stderr)
        return 2
    print(f"comparison: {comparison['status']} ({len(comparison['deltas'])} deltas, "
          f"{len(errors)} evidence errors)")
    return {"identical": 0, "different": 1, "invalid": 2}[comparison["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
