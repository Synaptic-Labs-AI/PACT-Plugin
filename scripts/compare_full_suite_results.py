#!/usr/bin/env python3
"""Compare diagnostic full-suite outcomes without replacing CI certification.

Location: scripts/compare_full_suite_results.py
Used by: development evidence collection for the fixed-SHA full-suite matrix.
Reads downloaded metadata, raw logs, schema-v3 records, and JUnit artifacts;
writes runs.jsonl and comparison.json under --output-dir.

A --run directory is one downloaded workflow run, assembled by hand:
  metadata.json   the ``/actions/runs/<id>`` object with a ``jobs`` array
                  merged in from the separate ``/actions/runs/<id>/jobs``
                  call (the run object does not carry its jobs)
  run.log         the merged job log, stderr included: verify_full_run.py
                  writes RUN-VERIFY-FAILURE to stderr
  artifact/.full-suite-results.xml, artifact/.full-run-record.json,
  artifact/.full-collection-record.json
Both runs must be attempt 1 of a workflow_dispatch on --expected-sha.

Exit codes: 0 identical, 1 deltas found, 2 refused (the evidence cannot be
compared), 3 internal error. A refusal names the reason; a crash never reads
as a delta.
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

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
# The run record's schema has one owner; a third copy here could drift from
# the certifier. scripts/verify_full_collection.py writes the collection
# record's version inline and exports no constant, so it is named here.
from verify_full_run import SCHEMA_VERSION as RUN_RECORD_SCHEMA_VERSION  # noqa: E402

COLLECTION_RECORD_SCHEMA_VERSION = 1

# The `tests.yml` workflow's immutable database id, read with
# `gh api repos/{owner}/{repo}/actions/workflows/tests.yml --jq .id`. There is
# no automated oracle for it: the id exists only behind an authenticated API
# call and this comparator runs offline, so a test deriving the expected value
# from this line would prove nothing. Re-derive it if the workflow is ever
# recreated.
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
# A wide-encoding document hides an ASCII token behind interleaved NUL bytes, so
# the DOCTYPE/ENTITY byte scan cannot see it; the UTF-16 LE mark also opens
# UTF-32 LE.
_WIDE_ENCODING_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")
# A downloaded artifact is read whole; the bound exists only so a pathological
# upload cannot exhaust memory. Measured: the 14 real full-suite artifacts at
# the review head are ~5.2 MiB each, so this leaves about three times the
# shipped shape.
MAX_JUNIT_BYTES = 16 * 1024 * 1024
RAW_PATHS = {"metadata": "metadata.json", "log": "run.log",
             "junit": "artifact/.full-suite-results.xml",
             "run_record": "artifact/.full-run-record.json",
             "collection_record": "artifact/.full-collection-record.json"}
# Actions cache messages carry the restored key; matching the prefix keeps a
# test that merely mentions "cache" out of the environment comparison.
CACHE_PREFIXES = ("Cache restored from key:", "Cache not found for input keys:",
                  "Cache saved with key:")
# Durations differ on every run, so exact inequality carries no signal. Both
# the factor and the floor must be exceeded before a slowdown is evidence.
DURATION_FACTOR = 2.0
DURATION_FLOOR_SECONDS = 0.5


class EvidenceError(ValueError):
    """The run cannot support an unambiguous comparison."""


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        # RecursionError is malformed evidence (pathological nesting), not an
        # internal fault, so it must refuse rather than exit 3.
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


def _mangle_test_address(address: str) -> list[str]:
    """Vendor of _pytest.junitxml.mangle_test_address (pytest 9.2).

    pytest delegates the parse to _pytest.nodeid.NodeId.parse: the params
    bracket is peeled off the tail FIRST and kept as one opaque string,
    because the '-' separators inside it cannot be recovered reliably.
    """
    path, separator, tail = address.partition("::")
    if not separator:
        names, params = [path], None
    else:
        name_part, bracket, param_part = tail.partition("[")
        names = [path, *name_part.split("::")]
        params = param_part.removesuffix("]") if bracket else None
    names[0] = names[0].replace("/", ".")  # _pytest.nodes.SEP is "/"
    names[0] = re.sub(r"\.py$", "", names[0])
    if params is not None:
        names[-1] += f"[{params}]"
    return names


def _junit_key(node_id: str) -> tuple[str, str]:
    parts = _mangle_test_address(node_id) if isinstance(node_id, str) and node_id else []
    if not parts or any(not isinstance(part, str) or not part for part in parts):
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
    # `$DIST` is the shipped workflow's own text: Actions echoes the `run:` body
    # unexpanded, so a real log carries the variable, not the resolved value.
    # The fixed tokens and the literal JUnit argument still pin the command
    # shape, so this widens the accepted VALUE, never the command.
    if (len(argv) != 9 or argv[:7] != [
            "python", "-m", "pytest", "-ra", "-n", "4", "--dist"]
            or argv[7] not in ("loadfile", "load", "$DIST")
            or argv[8] != "--junitxml=$GITHUB_WORKSPACE/.full-suite-results.xml"):
        raise EvidenceError(f"unexpected full-suite pytest argv: {argv!r}")
    return argv


def _junit_bytes(path: Path) -> bytes:
    """Read the JUnit bytes, refusing a document that declares entities.

    The file crossed a trust boundary (downloaded CI artifact). A DTD
    declaration is never emitted by pytest and is the only way to define
    entities, so refuse it BEFORE ElementTree can expand anything.

    Scope: XML 1.0 forbids U+0000 outright, so no well-formed document can
    contain a NUL byte, and every UTF-16/32 encoding of ASCII markup carries
    NUL bytes. A NUL byte is therefore refused before parsing, which closes the
    wide encodings a byte-order mark alone cannot see (a BOM-less UTF-16
    document is still auto-detected by expat). pytest emits UTF-8, and the
    guarantee is total over every document this tool accepts.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_JUNIT_BYTES + 1)
    except OSError as exc:
        raise EvidenceError(f"{path}: unreadable JUnit XML: {exc}") from exc
    if len(raw) > MAX_JUNIT_BYTES:
        raise EvidenceError(
            f"{path}: JUnit XML exceeds the {MAX_JUNIT_BYTES}-byte bound; "
            "refusing to read further")
    if raw.startswith(_WIDE_ENCODING_BOMS):
        raise EvidenceError(
            f"{path}: JUnit XML is not UTF-8 (UTF-16/32 byte-order mark); "
            "pytest emits UTF-8 only, and the entity scan cannot read a wide "
            "encoding")
    if b"\x00" in raw:
        raise EvidenceError(
            f"{path}: JUnit XML contains a NUL byte; XML 1.0 forbids U+0000, "
            "and every UTF-16/32 encoding of ASCII markup carries NUL bytes, "
            "so a wide-encoded document cannot be pytest's UTF-8 output")
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", raw, re.IGNORECASE):
        raise EvidenceError(
            f"{path}: JUnit XML declares a DOCTYPE or ENTITY declaration; "
            "refusing to parse a document that can expand entities")
    return raw


def _junit_case(element, index: int) -> list[dict]:
    """One <testcase> element as one case per outcome child.

    A single element can carry several children: pytest writes a setup error
    plus a teardown error into one element, and a call skip plus a teardown
    error into another (measured on 9.2.0). The record counts each child as a
    separate outcome, so each becomes a case. The element's duration is
    assigned to the first case only, keeping per-node duration sums equal.
    """
    classname, name = element.get("classname"), element.get("name")
    if classname is None or not name:
        raise EvidenceError("every JUnit testcase needs classname and name")
    children = [child for child in element
                if child.tag in ("failure", "error", "skipped")]
    try:
        duration = float(element.get("time", ""))
    except ValueError as exc:
        raise EvidenceError(f"{classname or name}: invalid duration") from exc
    if not math.isfinite(duration) or duration < 0:
        raise EvidenceError(f"{classname or name}: invalid duration")
    emitted = element.get("file")
    if not children:
        return [{"classname": classname, "name": name, "outcome": "pass",
                 "reason": None, "message": None, "duration": duration,
                 "file": emitted, "element": index}]
    cases = []
    for position, child in enumerate(children):
        outcome, reason = _junit_outcome(classname, name, child)
        cases.append({"classname": classname, "name": name, "outcome": outcome,
                      "reason": reason, "message": child.get("message"),
                      "duration": duration if position == 0 else 0.0,
                      "file": emitted, "element": index})
    return cases


def _junit_outcome(classname: str, name: str, child) -> tuple[str, str | None]:
    """(outcome, reason) for one outcome child, refusing unknown tags."""
    if not classname:
        # Collection-phase case: pytest names the MODULE in `name` and
        # leaves classname empty (measured on 9.2.0, serial and xdist).
        if child.tag == "skipped":
            return "collection_skip", None
        if child.tag == "error":
            return "collection_error", None
        raise EvidenceError(f"{name}: unknown collection-phase outcome {child.tag!r}")
    if child.tag in ("failure", "error"):
        return child.tag, None
    if child.get("type") == "pytest.skip":
        return "skip", child.get("message")
    if child.get("type") == "pytest.xfail":
        return "xfail", child.get("message")
    # A foreign or older pytest may emit a no-type <skipped> with this exact
    # message for a FAILED report that still carries wasxfail (junitxml's
    # append_failure branch). The pinned 9.2 does not set wasxfail on a failed
    # report; a strict xpass emits a <failure message="[XPASS(strict)]">
    # instead. This is defensive handling, not coverage of a shape our
    # toolchain produces. Such a report is counted as failed, never xpassed,
    # so it reconciles as a failure; the exact message keeps every other
    # no-type skipped form a refusal.
    if (child.get("type") is None
            and child.get("message") == "xfail-marked test passes unexpectedly"):
        return "failure", None
    raise EvidenceError(f"{classname}.{name}: unknown skip type")


def _parse_junit(path: Path) -> list[dict]:
    try:
        root = ET.fromstring(_junit_bytes(path))
    except ET.ParseError as exc:
        raise EvidenceError(f"{path}: unreadable JUnit XML: {exc}") from exc
    cases = []
    for index, element in enumerate(root.iter("testcase")):
        cases.extend(_junit_case(element, index))
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
        if len(candidates) > 1:
            raise EvidenceError(
                f"XPASS {node_id!r} needs one unambiguous JUnit pass, "
                f"found {len(candidates)}")
        if candidates:
            candidates[0]["outcome"] = "xpass"
            candidates[0]["reason"] = reason
            continue
        # Measured: an xpass whose teardown errors emits ONE element carrying
        # only the teardown xfail, so the call pass has no element of its own.
        # The raw XPASS line is the evidence for that pass; synthesize it.
        fabricated = {"classname": key[0], "name": key[1], "outcome": "xpass",
                      "reason": reason, "message": None, "duration": 0.0,
                      "file": None, "node_id": node_id,
                      "element": max((case["element"] for case in cases),
                                     default=-1) + 1}
        cases.append(fabricated)
        by_key.setdefault(key, []).append(fabricated)


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


_ENVIRONMENT_STEP_NAMES = (
    "Verify the CI environment (before tests)",
    "Verify the CI environment (after tests)",
)


def _require_environment_step_success(metadata: dict) -> str:
    """Require actor evidence for both environment checks and overall coherence."""
    jobs = metadata.get("jobs")
    if not jobs:
        raise EvidenceError("no workflow jobs carry environment-step evidence")
    matched = {name: [] for name in _ENVIRONMENT_STEP_NAMES}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            raise EvidenceError("workflow job steps are missing or malformed")
        for step in job["steps"]:
            name = step.get("name") if isinstance(step, dict) else None
            if isinstance(name, str) and name in matched:
                matched[name].append(step)
    for name, steps in matched.items():
        if len(steps) != 1:
            raise EvidenceError(f"expected exactly one {name!r} step, found {len(steps)}")
        if steps[0].get("conclusion") != "success":
            raise EvidenceError(
                f"{name!r} did not conclude success: {steps[0].get('conclusion')!r}")
    conclusion = metadata.get("conclusion")
    if conclusion not in ("success", "failure"):
        raise EvidenceError(f"unexpected overall workflow conclusion: {conclusion!r}")
    return conclusion


def _validate_metadata(metadata: dict, expected_sha: str) -> None:
    """Reject metadata that is not a completed attempt-1 dispatch on the SHA."""
    for key in ("attempt", "databaseId", "workflowDatabaseId"):
        _integer(metadata, key, minimum=1)
    for key, expected in (("attempt", 1),
                          ("workflowDatabaseId", WORKFLOW_DATABASE_ID),
                          ("event", "workflow_dispatch"),
                          ("headSha", expected_sha),
                          ("status", "completed")):
        if metadata.get(key) != expected:
            raise EvidenceError(
                f"metadata {key} is {metadata.get(key)!r}, expected {expected!r}")
    if not isinstance(metadata.get("jobs"), list):
        raise EvidenceError("metadata jobs must be a list")


def _validate_records(metadata: dict, record: dict, collection: dict,
                      expected_sha: str) -> tuple[int, list[str]]:
    """Cross-check the two in-band records against the API metadata."""
    if record.get("schema_version") != RUN_RECORD_SCHEMA_VERSION:
        raise EvidenceError(
            f"run record schema_version is {record.get('schema_version')!r}, "
            f"expected {RUN_RECORD_SCHEMA_VERSION}")
    if collection.get("schema_version") != COLLECTION_RECORD_SCHEMA_VERSION:
        raise EvidenceError(
            f"collection record schema_version is "
            f"{collection.get('schema_version')!r}, "
            f"expected {COLLECTION_RECORD_SCHEMA_VERSION}")
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
    return collected, terminal


def _validate_log(directory: Path, record: dict,
                  expected_sha: str) -> tuple[list[str], list[str], str, int]:
    """Validate the raw log against the record; return lines, argv, summary, checks."""
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
    collection_marker = "FULL-COLLECTION-OK "
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
    return lines, argv, summary, len(environment_indexes)


def _require_one_outcome_kind_per_node(cases: list[dict]) -> None:
    """Refuse the same outcome kind on two <testcase> ELEMENTS for one node.

    One element may carry several children: pytest writes a setup error plus a
    teardown error into one element, and a skip plus a teardown error into
    another (measured on 9.2.0). Those are separate phase outcomes, not a
    duplicate. Two ELEMENTS for one node repeating a kind is never produced by
    pytest and is refused; the legitimate two-element pair is a call failure
    plus a teardown error, whose kinds differ.
    """
    by_node = {}
    for case in cases:
        by_node.setdefault(case["node_id"], []).append(
            (case["element"], case["outcome"]))
    for node_id, entries in by_node.items():
        elements = {index for index, _ in entries}
        kinds = [kind for _, kind in entries]
        if len(elements) > 1 and len(kinds) != len(set(kinds)):
            raise EvidenceError(
                f"{node_id}: duplicate JUnit outcome across testcase elements")


def _teardown_after_pass_count(cases: list[dict]) -> int:
    """Items whose call passed and whose teardown errored.

    pytest writes ONE error-only case for that shape and does not write the
    pass, so the record counts one more pass than the JUnit does. A node that
    has a failure, a skip, or a setup error did not pass, so its teardown
    error attributes no missing pass (a setup error plus a teardown error, or
    a call skip plus a teardown error, both arrive on one node).
    """
    did_not_pass = set()
    for case in cases:
        if case["outcome"] in ("failure", "skip", "xfail", "xpass"):
            did_not_pass.add(case["node_id"])
        elif (case["outcome"] == "error"
              and (case["message"] or "").startswith("failed on setup")):
            did_not_pass.add(case["node_id"])
    return sum(1 for case in cases
               if case["outcome"] == "error"
               and (case["message"] or "").startswith("failed on teardown")
               and case["node_id"] not in did_not_pass)


def _terminal_cases(cases: list[dict], record: dict) -> list[dict]:
    """Refuse collection failures, reconcile collection skips, drop both.

    A collection-phase <testcase> has an empty classname and the module in
    `name`; it is not one of the run's terminal outcomes, so it must leave
    the population before the node mapping and the aggregate reconciliation.
    """
    collection_errors = [case for case in cases
                         if case["outcome"] == "collection_error"]
    if collection_errors:
        raise EvidenceError(
            f"JUnit reports {len(collection_errors)} collection failure(s) "
            f"({', '.join(sorted(case['name'] for case in collection_errors))}); "
            "a run that cannot collect its corpus cannot support an "
            "unrestricted comparison")
    collection_skips = [case for case in cases
                        if case["outcome"] == "collection_skip"]
    if len(collection_skips) != record["skipped_at_collection"]:
        raise EvidenceError(
            f"JUnit reports {len(collection_skips)} collection skip(s), record "
            f"skipped_at_collection={record['skipped_at_collection']}")
    return [case for case in cases
            if case["outcome"] not in ("collection_skip", "collection_error")]


def _validate_junit(record: dict, lines: list[str], terminal: list[str],
                    artifact: Path) -> tuple[list[dict], list, list]:
    """Map every JUnit case to a terminal node and reconcile the aggregates."""
    cases = _terminal_cases(
        _parse_junit(artifact / ".full-suite-results.xml"), record)
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
    _require_one_outcome_kind_per_node(cases)
    outcome_counts = Counter(case["outcome"] for case in cases)
    teardown_after_pass = _teardown_after_pass_count(cases)
    expected_counts = {
        "pass": record["passed"] - teardown_after_pass,
        "failure": record["failed"], "error": record["errors"],
        "skip": record["skipped"] - record["skipped_at_collection"],
        "xfail": record["xfailed"], "xpass": record["xpassed"]}
    if expected_counts["pass"] < 0 or any(
            outcome_counts[outcome] != count
            for outcome, count in expected_counts.items()):
        shortfall = expected_counts["pass"] - outcome_counts["pass"]
        detail = (f"; {shortfall} pass(es) cannot be attributed: a pass whose "
                  "teardown skipped is not marked in JUnit" if shortfall > 0 else "")
        raise EvidenceError(
            f"JUnit aggregates {dict(outcome_counts)} != record {expected_counts} "
            f"(teardown-after-pass items: {teardown_after_pass}{detail})")
    skip_sites, collection_skip_sites = _validate_skip_sites(record, cases, node_for_key)
    return cases, skip_sites, collection_skip_sites


def _validate_run_outcomes(record: dict, metadata: dict, lines: list[str],
                           overall_conclusion: str) -> tuple[str, list[str], bool]:
    """Reconcile exit status, verifier disposition, and the workflow conclusion."""
    full_run_ok = [line for line in lines if line.startswith("FULL-RUN-OK ")]
    verifier_state, verifier_failures = _full_run_verifier(
        metadata, lines, bool(full_run_ok))
    red = record["failed"] > 0 or record["errors"] > 0
    if red and overall_conclusion != "failure":
        raise EvidenceError("red test outcomes have an inconsistent overall conclusion")
    if not red and overall_conclusion != "success":
        raise EvidenceError("green test outcomes have an inconsistent overall conclusion")
    if red and (record["exitstatus"] == 0 or full_run_ok or verifier_state == "missing"):
        raise EvidenceError("red outcomes lack consistent exit and verifier evidence")
    if not red and record["exitstatus"] != 0:
        raise EvidenceError("nonzero exit without a test outcome is invalid infrastructure")
    clean = not red and record["exitstatus"] == 0
    if clean and (len(full_run_ok) != 1 or record["passed"] <= 0 or verifier_failures):
        raise EvidenceError("a clean run needs positive passes and one FULL-RUN-OK")
    return verifier_state, verifier_failures, clean


def _normalize_run(logical: str, directory: Path, expected_sha: str) -> dict:
    metadata = _json_object(directory / "metadata.json")
    _validate_metadata(metadata, expected_sha)
    artifact = directory / "artifact"
    record = _json_object(artifact / ".full-run-record.json")
    collection = _json_object(artifact / ".full-collection-record.json")
    collected, terminal = _validate_records(metadata, record, collection, expected_sha)
    lines, argv, summary, environment_passes = _validate_log(
        directory, record, expected_sha)
    overall_conclusion = _require_environment_step_success(metadata)
    cases, skip_sites, collection_skip_sites = _validate_junit(
        record, lines, terminal, artifact)
    verifier_state, verifier_failures, clean = _validate_run_outcomes(
        record, metadata, lines, overall_conclusion)
    return {
        "logical_name": logical, "raw_paths": RAW_PATHS, "evidence_errors": [],
        "validity": "clean" if clean else "test_outcome",
        "identity": {"database_id": metadata["databaseId"],
                     "workflow_database_id": metadata["workflowDatabaseId"],
                     "attempt": 1, "head_sha": expected_sha,
                     "head_branch": metadata.get("headBranch"),
                     "url": metadata.get("url")},
        "argv": argv,
        "environment": {
            "overall_conclusion": overall_conclusion,
            "passed_checks": environment_passes,
            "platform": [line for line in lines if line.startswith("platform ")],
            "measurements": [line for line in lines if "sys.executable=" in line],
            # Job NAMES identify the workflow shape; the runner instance id is
            # a fresh value on every GitHub-hosted run and attributes nothing.
            "job_names": [job.get("name") for job in metadata.get("jobs", [])
                          if isinstance(job, dict)],
            "pins": [line for line in lines if "constraints-ci.txt" in line],
            "cache": [line for line in lines if line.startswith(CACHE_PREFIXES)],
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
    }


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


def _durations_by_case(run: dict) -> dict[tuple[str, str], list[float]]:
    grouped = {}
    for case in run["cases"]:
        grouped.setdefault((case["classname"], case["name"]), []).extend(
            outcome["duration"] for outcome in case["outcomes"])
    return {key: sorted(values) for key, values in grouped.items()}


def _material_duration_changes(reference: dict, run: dict) -> list[dict]:
    """Per-test duration changes large enough to be evidence, with both values."""
    current = _durations_by_case(run)
    changes = []
    for key in set(reference) & set(current):
        before, after = sum(reference[key]), sum(current[key])
        slower, faster = max(before, after), min(before, after)
        if slower >= DURATION_FLOOR_SECONDS and slower >= faster * DURATION_FACTOR:
            changes.append({"test": f"{key[0]}.{key[1]}",
                            "before_seconds": round(before, 3),
                            "after_seconds": round(after, 3)})
    return sorted(changes, key=lambda change: change["test"])


# Not attribution axes. overall_conclusion and passed_checks are derived from
# the outcome; pytest_addopts_absent is a constant; cache lines are incidental
# log text (cold vs warm is not a reason a test outcome changed).
_NON_ATTRIBUTION_ENVIRONMENT_KEYS = (
    "overall_conclusion", "passed_checks", "pytest_addopts_absent", "cache")


def _environment_differences(reference: dict, run: dict) -> list[str]:
    """Material environment axes that differ between two runs.

    Only platform, measurements, job names and pins can attribute an outcome
    change. Ephemeral per-run values (runner instance ids) are not recorded as
    an axis at all.
    """
    return sorted(key for key in set(reference) | set(run)
                  if key not in _NON_ATTRIBUTION_ENVIRONMENT_KEYS
                  and reference.get(key) != run.get(key))


def _case_deltas(reference_cases: dict, cases: dict, label: str) -> list[dict]:
    """Per-test differences between the reference and one run."""
    deltas = []
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
            kind = "skip_reason" if set(before_outcomes) == {"skip"} else "reason"
        else:
            kind = "outcome"
        deltas.append({
            "type": kind, "run": label, "classname": key[0], "name": key[1],
            "before": _outcome_list(before), "after": _outcome_list(after)})
    return deltas


def _classify_environment(comparison: dict, reference: dict,
                          differences: list) -> None:
    """Observe a lone environment difference; refuse one beside a delta.

    A delta next to a changed environment cannot be attributed to run
    dependence, so the comparison is refused rather than reported as a finding.
    """
    if not differences:
        return
    if not comparison["deltas"]:
        for label, fields in differences:
            comparison["observations"].append(
                {"type": "environment", "run": label, "fields": fields})
        return
    for label, fields in differences:
        comparison["evidence_errors"].append({
            "run": label,
            "error": f"environment differs from {reference['logical_name']!r} in "
                     f"{', '.join(fields)} while {len(comparison['deltas'])} "
                     "outcome delta(s) exist; the delta cannot be attributed"})
    comparison["status"] = "invalid"


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
    reference_durations = _durations_by_case(runs[0])
    reference_nodes = set(reference["collection"]["terminal_node_ids"])
    environment_differences = []
    for run in runs[1:]:
        label = run["logical_name"]
        nodes = set(run["collection"]["terminal_node_ids"])
        for kind, values in (("collection_added", nodes - reference_nodes),
                             ("collection_removed", reference_nodes - nodes)):
            if values:
                comparison["deltas"].append(
                    {"type": kind, "run": label, "node_ids": sorted(values)})
        comparison["deltas"].extend(
            _case_deltas(reference_cases, _case_multiset(run), label))
        if run["collection_skip_sites"] != reference["collection_skip_sites"]:
            comparison["deltas"].append({"type": "skip_sites", "run": label,
                                         "before": reference["collection_skip_sites"],
                                         "after": run["collection_skip_sites"]})
        if run["argv"] != reference["argv"]:
            comparison["observations"].append({"type": "argv", "run": label})
        duration_changes = _material_duration_changes(reference_durations, run)
        if duration_changes:
            comparison["observations"].append(
                {"type": "duration", "run": label, "tests": duration_changes[:10],
                 "count": len(duration_changes)})
        if run["collection"]["terminal_node_ids"] != reference["collection"]["terminal_node_ids"]:
            comparison["observations"].append({"type": "ordering", "run": label})
        differing = _environment_differences(reference["environment"], run["environment"])
        if differing:
            environment_differences.append((label, differing))
    _classify_environment(comparison, reference, environment_differences)
    if comparison["deltas"] and comparison["status"] == "identical":
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


def _run_specs(values: list[str]) -> tuple[list[tuple[str, Path]] | None, str | None]:
    """Parse LOGICAL=ABSOLUTE_DIRECTORY arguments or name the first problem."""
    specs, names = [], set()
    for value in values:
        logical, separator, raw_directory = value.partition("=")
        directory = Path(raw_directory)
        if not logical or not separator or not directory.is_absolute():
            return None, f"invalid --run {value!r}"
        if logical in names:
            return None, f"duplicate logical run name {logical!r}"
        names.add(logical)
        specs.append((logical, directory))
    return specs, None


def _load_runs(specs: list[tuple[str, Path]],
               expected_sha: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Normalize every run; a failure becomes evidence or an internal error."""
    runs, errors, internal_errors = [], [], []
    for logical, directory in specs:
        try:
            runs.append(_normalize_run(logical, directory, expected_sha))
        except EvidenceError as exc:
            error = {"run": logical, "error": str(exc)}
            errors.append(error)
            runs.append({"logical_name": logical, "validity": "invalid",
                         "raw_paths": RAW_PATHS, "evidence_errors": [str(exc)]})
        except Exception as exc:  # a crash must never read as a delta (exit 1)
            message = f"{type(exc).__name__}: {exc}"
            internal_errors.append({"run": logical, "error": message})
            print(f"internal error for run {logical!r}: {message}", file=sys.stderr)
            runs.append({"logical_name": logical, "validity": "invalid",
                         "raw_paths": RAW_PATHS, "evidence_errors": []})
    return runs, errors, internal_errors


def _reject_repeated_identities(runs: list[dict], errors: list[dict]) -> None:
    """Refuse two input runs that report the same workflow run id."""
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


def _invalid_comparison(expected_sha: str, runs: list[dict],
                        errors: list[dict]) -> dict:
    return {"schema_version": 1, "expected_sha": expected_sha,
            "input_runs": [{"logical_name": run["logical_name"],
                            "identity": run.get("identity"),
                            "validity": run["validity"]} for run in runs],
            "status": "invalid", "deltas": [], "observations": [],
            "evidence_errors": errors}


_RUN_LAYOUT_EPILOG = """\
A --run directory is one downloaded workflow run:
  metadata.json                          the /actions/runs/<id> object with a
                                         "jobs" array merged in from the
                                         separate /actions/runs/<id>/jobs call
  run.log                                the merged job log, stderr included
                                         (verify_full_run.py writes
                                         RUN-VERIFY-FAILURE there)
  artifact/.full-suite-results.xml       pytest JUnit output
  artifact/.full-run-record.json         schema-v3 in-band record
  artifact/.full-collection-record.json  collection verifier record
Both runs must be attempt 1 of a workflow_dispatch on --expected-sha.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=_RUN_LAYOUT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
    specs, problem = _run_specs(args.run)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    runs, errors, internal_errors = _load_runs(specs, args.expected_sha.lower())
    _reject_repeated_identities(runs, errors)
    comparison = None
    if not internal_errors:
        try:
            comparison = _compare(runs, args.expected_sha.lower(), errors)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            internal_errors.append({"run": None, "error": message})
            print(f"internal error during comparison: {message}", file=sys.stderr)
    if comparison is None:
        comparison = _invalid_comparison(args.expected_sha.lower(), runs, errors)
    if internal_errors:
        comparison["internal_errors"] = internal_errors
        comparison["status"] = "invalid"
    try:
        _atomic_write(args.output_dir / "runs.jsonl", "".join(
            json.dumps(run, sort_keys=True) + "\n" for run in runs))
        _atomic_write(args.output_dir / "comparison.json",
                      json.dumps(comparison, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"cannot write comparison outputs: {exc}", file=sys.stderr)
        return 3
    for error in comparison["evidence_errors"]:
        print(f"evidence error ({error['run']}): {error['error']}", file=sys.stderr)
    print(f"comparison: {comparison['status']} ({len(comparison['deltas'])} deltas, "
          f"{len(comparison['evidence_errors'])} evidence errors, "
          f"{len(comparison.get('internal_errors', []))} internal errors)")
    if internal_errors:
        return 3
    return {"identical": 0, "different": 1, "invalid": 2}[comparison["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
