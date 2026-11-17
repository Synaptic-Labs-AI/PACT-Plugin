#!/usr/bin/env python3
"""Compare diagnostic full-suite outcomes without replacing CI certification.

Location: scripts/compare_full_suite_results.py
Used by: development evidence collection for the fixed-SHA full-suite matrix.
Reads downloaded metadata, raw logs, schema-v3 records, and JUnit artifacts;
writes runs.jsonl and comparison.json under --output-dir.

A --run directory is one downloaded workflow run, assembled by hand:
  metadata.json   the ``/actions/runs/<id>`` object with a ``jobs`` array
                  merged in from the separate ``/actions/runs/<id>/jobs``
                  call (the run object does not carry its jobs), and the
                  identity keys attempt, databaseId, workflowDatabaseId and
                  headSha normalized onto the top level (the raw response
                  carries none of the four names)
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
# The shape of this tool's own outputs: comparison.json and every runs.jsonl
# record. 2 added shuffle_seed, census_failures, the census and census_tests
# delta types and the census_outcome validity.
OUTPUT_SCHEMA_VERSION = 2

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
# Actions writes every log line as a timestamp and one space before the text, an
# empty output line included, so an empty line strips to "" and ends the report
# header. Measured 2026-11-16 on runs 43861607028 (ordered) and 43861611679
# (seed 103): every empty line in both the `gh run view --log` form and the
# job-logs API form was the timestamp, "Z " and nothing after it, with no bare
# "Z" and no carriage return.
TIMESTAMP_TEXT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z "
OPTIONAL_BOM = re.escape(chr(0xFEFF)) + "?"
TIMESTAMP = re.compile("^" + OPTIONAL_BOM + TIMESTAMP_TEXT)
ACTIONS_ENVELOPE = re.compile(
    r"^[^\t\r\n]*\t[^\t\r\n]*\t" + OPTIONAL_BOM + TIMESTAMP_TEXT)
SHA = re.compile(r"[0-9a-fA-F]{40}")
# The run line's unexpanded
# `${PACT_SHUFFLE_SEED:+-p randomly --randomly-seed="$PACT_SHUFFLE_SEED" --randomly-dont-reset-seed}`,
# as shlex splits it, and pytest-randomly's own report-header line. SEED_LINE is
# the only accepted spelling: 1 to 10 ASCII digits holds every 32-bit seed, and
# every seed the seed guard in .github/workflows/tests.yml admits (at most 9).
# SEED_LIKE_LINE is the same line with any run of Unicode digits; a header line
# that matches it but not SEED_LINE is refused rather than read.
_SEED_ARGV = ["${PACT_SHUFFLE_SEED:+-p", "randomly", "--randomly-seed=$PACT_SHUFFLE_SEED",
              "--randomly-dont-reset-seed}"]
SEED_LINE = re.compile(r"Using --randomly-seed=([0-9]{1,10})")
SEED_LIKE_LINE = re.compile(r"Using --randomly-seed=\d+")
# The test step's environment as the Actions runner echoes it, between the
# step's run command and its ##[endgroup] line, before the step's process
# starts: an ordered run shows the variable empty, a seeded run its seed.
STEP_ENV_SEED = re.compile(r"  PACT_SHUFFLE_SEED:(?: (.*))?")
# A census problem line names the test it came from: PYTEST_CURRENT_TEST, or
# '<outside a test>', after an xdist worker's '[gwN] ' prefix.
CENSUS_LINE_TEST = re.compile(
    r"(?:\[gw[0-9]+\] )?(.*? \((?:setup|call|teardown)\)|<outside a test>): ")
SESSION_BANNER = re.compile(r"=+ test session starts =+")
# A top-level section line pytest itself writes (write_sep("=", title)): the
# report sections and the final summary line. Only these end the report
# header and the session-finish span. "_" and "-" separators appear only
# inside those sections, and a stray line of dashes on stderr, such as the
# logging module's "--- Logging error ---", must not end either part.
PYTEST_SECTION = re.compile(r"=+ \S(?:.*\S)? =+")
SHORT_SUMMARY_HEADER = re.compile(r"=+ short test summary info =+")
# The header line each census's finish() writes when it fails the session.
# Each finish() (child_pytest_census, clock_shift_census, home_census) starts
# the header on a new line when the terminal reporter's writer says its line is
# open, as xdist's last progress line ("....[100%]") leaves it. The optional
# progress prefix stays so a header that still follows a progress marker on one
# line is read: the new-line check reads pytest's private _tw.width_of_current_line
# and quietly skips the break if a release renames it, and logs recorded before
# the censuses started the new line carry the joined form. A finish() with no
# terminal reporter prints to stderr instead; with no reporter nothing writes
# progress, so that header starts its own line (measured on pytest 9.2 for the
# HOME census with -p no:terminal, serial and -n 2; the other censuses share
# the same fallback line).
_PROGRESS_PREFIX = r"(?:[^\[]*\[ *[0-9]{1,3}%\])?"
CENSUS_HEADERS = {
    "child_pytest": re.compile(
        _PROGRESS_PREFIX + r"CHILD PYTEST CENSUS: ([0-9]{1,9}) distinct pytest child "
        r"launch\(es\) refused or not inspected"),
    "clock_shift": re.compile(
        _PROGRESS_PREFIX + r"clock-shift census: ([0-9]{1,9}) problem\(s\); "
        r"this run did not shift every clock"),
    "home": re.compile(
        _PROGRESS_PREFIX + r"HOME CENSUS: ([0-9]{1,9}) distinct child launch\(es\) without "
        r"HOME refused"),
    # The HOME census writes launches it could not inspect in their own block,
    # after its refusal block when both occur; a fault alone fails the session.
    "home_uninspected": re.compile(
        _PROGRESS_PREFIX + r"HOME CENSUS: ([0-9]{1,9}) launch\(es\) the census could not inspect"),
}
COUNT_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
# A wide-encoding document hides an ASCII token behind interleaved NUL bytes, so
# the DOCTYPE/ENTITY byte scan cannot see it; the UTF-16 LE mark also opens
# UTF-32 LE.
_WIDE_ENCODING_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")
# A downloaded artifact is read whole; the bound exists only so a pathological
# upload cannot exhaust memory. Measured: the 14 real full-suite artifacts at
# the review head are ~5.2 MiB each, so this leaves about 50% headroom.
MAX_JUNIT_BYTES = 8 * 1024 * 1024
# The structured evidence files (metadata, both records) are kilobytes in
# every real run; one bound covers all three. The merged job log carries
# per-test -ra detail and is larger, so it gets its own bound. Same shape,
# same reason: a pathological upload cannot exhaust memory.
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 32 * 1024 * 1024


def _read_bounded(path: Path, limit: int) -> bytes:
    """Read at most ``limit``+1 bytes, so a larger file is refused by name
    instead of being read whole. OSError propagates: each caller wraps it
    with its own evidence wording."""
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise EvidenceError(
            f"{path}: evidence exceeds the {limit}-byte bound; "
            "refusing to read further")
    return raw
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
        raw = _read_bounded(path, MAX_JSON_BYTES)
    except OSError as exc:
        raise EvidenceError(f"{path}: unreadable JSON: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
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
        raw = _read_bounded(path, MAX_LOG_BYTES)
    except OSError as exc:
        raise EvidenceError(f"{path}: unreadable log: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceError(f"{path}: unreadable log: {exc}") from exc
    return [TIMESTAMP.sub("", ACTIONS_ENVELOPE.sub("", line, count=1), count=1)
            for line in text.splitlines()]


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


def _command_text(line: str) -> str | None:
    """The echoed pytest command on this line, or None when it carries none."""
    stripped = line.strip()
    if stripped.startswith("##[group]Run "):
        stripped = stripped[len("##[group]Run "):]
    return stripped if stripped.startswith("python -m pytest ") else None


def _step_bounds(lines: list[str]) -> tuple[list[int], int | None, int | None, int | None]:
    """Where the full-suite step's parts sit in the log.

    Returns the echoed run command's line indexes, the session-start banner's
    index, and the end indexes of the report header and the session-finish
    span. The command lines run from the first one up to the first banner
    after it, so a command a test prints in its output cannot count, and a
    banner from an earlier step cannot anchor the header. Without a banner the
    last three are None.
    """
    first = next((index for index, line in enumerate(lines)
                  if _command_text(line) is not None), None)
    if first is None:
        return [], None, None, None
    banner = next((index for index in range(first + 1, len(lines))
                   if SESSION_BANNER.fullmatch(lines[index])), None)
    stop = len(lines) if banner is None else banner
    commands = [index for index in range(first, stop)
                if _command_text(lines[index]) is not None]
    if banner is None:
        return commands, None, None, None
    end = banner + 1
    while (end < len(lines) and lines[end].strip()
           and not PYTEST_SECTION.fullmatch(lines[end])):
        end += 1
    finish = end
    while finish < len(lines) and not PYTEST_SECTION.fullmatch(lines[finish]):
        finish += 1
    return commands, banner, end, finish


def _test_text_range(lines: list[str], summary_index: int) -> range:
    """pytest's report sections: from the first pytest section line after the
    session-finish span through the final summary line.

    Every text a test produces (captured output, assert messages, tracebacks,
    warning text, skip and failure reasons) is printed here and nowhere else in
    the log, so no evidence marker is read from this range. Empty when the
    session banner cannot be located; that run is refused when its header is
    read. A log whose session-finish span ends after the final summary line
    has no report sections to locate, and is refused here rather than read
    whole.
    """
    _commands, banner, _end, finish = _step_bounds(lines)
    if banner is None:
        return range(0)
    if finish > summary_index:
        raise EvidenceError(
            "the final summary line comes before the end of the session-finish "
            "span, so pytest's report sections cannot be located")
    return range(finish, summary_index + 1)


def _short_summary_ranges(lines: list[str], report: range) -> list[range]:
    """Where the 'short test summary info' section may start, latest first.

    It is the last section before the final summary line, and -ra prints its
    XPASS lines there. On CI pytest prints each failure's whole message in it,
    untrimmed and over several lines, and a message can hold a child pytest's
    whole report, that report's own header line included, as can a failing
    test's captured output in an earlier section. So every line that is exactly
    a short-summary header is returned, and _apply_xpass refuses a log with more
    than one. Separator-shaped lines that are not the header are passed over.
    """
    if not report:
        return []
    summary_index = report[-1]
    return [range(index + 1, summary_index)
            for index in range(summary_index - 1, report.start - 1, -1)
            if SHORT_SUMMARY_HEADER.fullmatch(lines[index])]


def _short_summary_range(lines: list[str], report: range) -> range:
    """The latest candidate short-summary section, or an empty range."""
    ranges = _short_summary_ranges(lines, report)
    return ranges[0] if ranges else range(0)


def _command_argv(lines: list[str]) -> list[str]:
    candidates = []
    for index in _step_bounds(lines)[0]:
        try:
            candidates.append(shlex.split(_command_text(lines[index])))
        except ValueError as exc:
            raise EvidenceError(f"unparseable pytest command: {exc}") from exc
    if (len(candidates) not in (1, 2)
            or len(candidates) == 2 and candidates[0] != candidates[1]):
        raise EvidenceError("expected one command, optionally repeated by the Actions group header")
    argv = candidates[0]
    # `$DIST` and `${PACT_SHUFFLE_SEED:+…}` are the shipped workflow's own text:
    # Actions echoes the `run:` body unexpanded, so a real log carries the
    # variables, not the resolved values. The argv text is therefore the same
    # for every seed and cannot tell a shuffled run from an ordered one; the
    # seed comes from pytest-randomly's header line instead. Exactly two shapes
    # are accepted: the 9-token ordered line, and the same with the four seed
    # tokens before the literal JUnit argument. This widens the accepted VALUE
    # and adds one reviewed shape, never an open-ended command.
    seeded_length = 9 + len(_SEED_ARGV)
    if (len(argv) not in (9, seeded_length) or argv[:7] != [
            "python", "-m", "pytest", "-ra", "-n", "4", "--dist"]
            or argv[7] not in ("loadfile", "load", "$DIST")
            or len(argv) == seeded_length and argv[8:-1] != _SEED_ARGV
            or argv[-1] != "--junitxml=$GITHUB_WORKSPACE/.full-suite-results.xml"):
        raise EvidenceError(f"unexpected full-suite pytest argv: {argv!r}")
    return argv


def _session_spans(lines: list[str]) -> tuple[list[str], list[str]]:
    """The full-suite step's pytest report header and its session-finish span.

    The header starts after the first session-start banner that follows the
    step's echoed run command, so a banner from an earlier step cannot anchor
    it, and ends at the first blank line or pytest section line. The
    session-finish span runs from there to the next pytest section line: it holds
    the progress lines and what pytest_sessionfinish writes, and no test's
    captured output, which pytest prints only inside a later section. A missing
    banner is refused: reading no seed would look like a valid ordered run.
    """
    _commands, start, end, finish = _step_bounds(lines)
    if start is None:
        raise EvidenceError(
            "no pytest session-start banner follows the full-suite run command; "
            "the run's seed header cannot be located")
    return lines[start + 1:end], lines[end:finish]


def _census_failures(finish_span: list[str]) -> list[dict]:
    """Every census that failed the session, with its problem lines.

    Read from the session-finish span only, so a census-shaped line in a test's
    captured output is never counted. A census writes one indented line per
    problem straight after its header, so a block with fewer or more lines than
    its header counts (a log cut short, or another line written in between) is
    refused. Each census's finish() prints its header once per session, from the
    controller only, so a header that appears twice is refused too.
    """
    failures = []
    for index, line in enumerate(finish_span):
        for name, pattern in CENSUS_HEADERS.items():
            match = pattern.fullmatch(line)
            if match is None:
                continue
            if any(failure["census"] == name for failure in failures):
                raise EvidenceError(
                    f"the {name} census header appears more than once in the "
                    f"session-finish span")
            problems = []
            for detail in finish_span[index + 1:]:
                if not detail.startswith("  "):
                    break
                problems.append(detail.strip())
            if len(problems) != int(match.group(1)):
                raise EvidenceError(
                    f"the {name} census header counts {int(match.group(1))} "
                    f"problem(s) but {len(problems)} problem line(s) follow it")
            failures.append({"census": name, "problems": int(match.group(1)),
                             "lines": problems})
    return sorted(failures, key=lambda failure: failure["census"])


def _shuffle_seed(header: list[str]) -> int | None:
    """The pytest-randomly seed from the report header, or None for an ordered run.

    Only header lines count, so a seed line a test prints in its captured output
    is ignored. Each line is matched as-is. A seed-like line that is not 1 to 10
    ASCII digits, or a second seed line, is refused.
    """
    seeds = []
    for line in header:
        match = SEED_LINE.fullmatch(line)
        if match is None and SEED_LIKE_LINE.fullmatch(line):
            raise EvidenceError(
                "pytest-randomly seed line is not 1 to 10 ASCII digits")
        if match is not None:
            seeds.append(match.group(1))
    if len(seeds) > 1:
        raise EvidenceError("more than one pytest-randomly seed line")
    return int(seeds[0]) if seeds else None


def _step_env_seed(lines: list[str]) -> str | None:
    """The seed the test step's environment echo names: '' for an ordered run,
    its text for a seeded one, or None when the log carries no echo for it."""
    commands, banner, _end, _finish = _step_bounds(lines)
    if not commands or banner is None:
        return None
    for index in range(commands[0], banner):
        if lines[index] == "##[endgroup]":
            break
        match = STEP_ENV_SEED.fullmatch(lines[index])
        if match is not None:
            return match.group(1) or ""
    return None


def _run_seed(lines: list[str], report: range) -> int | None:
    """The run's pytest-randomly seed, or None for an ordered run.

    The seed is read from the report header. A pytest-randomly seed line
    anywhere else in the test step's log, outside pytest's report sections, is
    refused (earlier steps are not read, as for the header itself): a stray line
    (worker output merged in) that ends the header before the seed line would
    otherwise make a seeded run read as ordered. Where the log carries the
    test step's environment echo, which the runner writes before pytest
    starts, the header must agree with it.
    """
    commands, banner, end, _finish = _step_bounds(lines)
    header, _finish_span = _session_spans(lines)
    seed = _shuffle_seed(header)
    displaced = [index for index in range(commands[0], len(lines))
                 if "Using --randomly-seed=" in lines[index] and index not in report
                 and not banner < index < end]
    if displaced:
        raise EvidenceError(
            f"a pytest-randomly seed line sits outside the report header (log line "
            f"{displaced[0] + 1}), so the run's order cannot be read from the header")
    named = _step_env_seed(lines)
    if named is None:
        return seed
    if named == "" and seed is not None:
        raise EvidenceError(
            f"the test step's environment names no seed, but the report header "
            f"reads seed {seed}")
    if named != "" and (not named.isascii() or not named.isdigit()
                        or seed != int(named)):
        raise EvidenceError(
            f"the test step's environment names seed {named!r}, but the report "
            f"header reads {'no seed' if seed is None else f'seed {seed}'}")
    return seed


_LAUNCH_TEXT = re.compile(r"\[[^\]]*\]")
_CWD_TEXT = re.compile(r"\(cwd [^)]*\)")


def _census_tests(failure: dict) -> list[str]:
    """Each test a census's problem lines name, with its problem, without the
    worker prefix.

    A line's launch text and working directory hold temporary paths that
    differ from run to run. So in the problem the first bracketed span, the
    launch, becomes '[...]' and each '(cwd ...)' loses its path; a later
    bracket, such as a child-pytest family label, is kept. A clock-shift line
    keeps only the problem after its last ' -- ', since the argv before it is
    launch text too. The same test failing for a different problem is then a
    different key. A launch whose shown text holds ']' keeps the rest of it in
    the key, and so does any other run-varying text a line carries outside
    those spans, such as a wrapper program named by a temporary path; such a
    line can report a difference between runs that is only that text. A line
    that names no test is keyed by its problem, the text before its first
    ': ', because the rest (counts, ledger paths, exception text) also differs
    by run.
    """
    keys = set()
    for line in failure["lines"]:
        line = re.sub(r"^\[gw[0-9]+\] ", "", line)
        match = CENSUS_LINE_TEST.match(line)
        if match is None:
            keys.add(line.partition(": ")[0])
            continue
        problem = line[match.end():]
        if failure["census"] == "clock_shift":
            problem = problem.rpartition(" -- ")[2]
        problem = _CWD_TEXT.sub("(cwd ...)", _LAUNCH_TEXT.sub("[...]", problem, count=1))
        keys.add(f"{match.group(1)}: {problem}")
    return sorted(keys)


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
    guarantee is total over every document this tool accepts. The one family
    that still passes this byte scan — an EBCDIC document, whose '<!DOCTYPE'
    bytes are not ASCII — is refused only because pyexpat cannot parse it;
    swapping the parser reopens that gap, so the scan's totality is bounded
    by the parser's encoding set.
    """
    try:
        raw = _read_bounded(path, MAX_JUNIT_BYTES)
    except OSError as exc:
        raise EvidenceError(f"{path}: unreadable JUnit XML: {exc}") from exc
    except EvidenceError as exc:
        raise EvidenceError(
            f"{path}: JUnit XML exceeds the {MAX_JUNIT_BYTES}-byte bound; "
            "refusing to read further") from exc
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
    except (RecursionError, ET.ParseError) as exc:
        # RecursionError is malformed evidence, aligned with _json_object.
        raise EvidenceError(f"{path}: unreadable JUnit XML: {exc}") from exc
    cases = []
    for index, element in enumerate(root.iter("testcase")):
        cases.extend(_junit_case(element, index))
    if not cases:
        raise EvidenceError("JUnit contains no testcase elements")
    return cases


# The log is read with str.splitlines, which breaks a line at each of these.
_LINE_BREAK = re.compile("\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
# pytest's junitxml.bin_xml_escape (9.2), which rewrites each character XML
# cannot hold as the literal text '#xNN'; a test compares this copy with it.
_JUNIT_ILLEGAL = re.compile(
    "[^\u0009\u000a\u000d\u0020-\u007e\u0080-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
# A JUnit xfail message split where the log reader splits: separators XML
# keeps stay as they are, the others are written as their '#xNN' escape.
_JUNIT_LINE_BREAK = re.compile("\r\n|[\n\r\x85\u2028\u2029]|#x0B|#x0C|#x1C|#x1D|#x1E")


def _junit_escape(text: str) -> str:
    return _JUNIT_ILLEGAL.sub(
        lambda match: (f"#x{ord(match.group()):02X}" if ord(match.group()) <= 0xFF
                       else f"#x{ord(match.group()):04X}"), text)


def _one_extra_count(counts: set[int], entry: str, kind: str) -> int:
    if not counts:
        raise EvidenceError(f"a {kind} summary entry has no reason in the run's evidence: "
                            f"{entry!r}")
    if len(counts) > 1:
        raise EvidenceError(f"a {kind} summary entry matches reasons of different "
                            f"lengths: {entry!r}")
    return counts.pop()


def _skip_extra_lines(entry: str, skip_reasons: list[str]) -> int:
    """How many log lines after a SKIPPED entry its reason continues over.

    The run record holds each skip reason as pytest prints it, and pytest
    prints a reason whole and unindented, so it continues over one log line
    per line break the log reader splits at (measured on 9.2 under CI=true,
    serial and xdist). A continuation line can therefore start like an entry.
    """
    return _one_extra_count(
        {len(_LINE_BREAK.findall(reason)) for reason in skip_reasons
         if entry.endswith(": " + _LINE_BREAK.split(reason, maxsplit=1)[0])
         or not reason and entry.endswith(":")}, entry, "SKIPPED")


def _xfail_extra_lines(entry: str, following: list[str],
                       xfail_reasons: dict[str, list[str]]) -> int:
    """How many log lines after an XFAIL entry its reason continues over.

    The terminal prints the xfail reason raw; junitxml writes it with a
    leading 'reason: ' removed and every character XML cannot hold escaped
    (measured on 9.2). So the terminal lines are put in junitxml's form and
    compared with the JUnit message, split where the log reader splits.
    """
    rest = re.sub(r"^at (?:setup|call|teardown) of ", "", entry[len("XFAIL "):])
    counts = set()
    for node_id, reasons in xfail_reasons.items():
        for reason in reasons:
            if rest == node_id:
                if not reason:
                    counts.add(0)
                continue
            if not rest.startswith(node_id + " - "):
                continue
            segments = _JUNIT_LINE_BREAK.split(reason)
            lines = [rest[len(node_id) + 3:], *following[:len(segments) - 1]]
            if len(lines) != len(segments):
                continue
            lines[0] = lines[0].removeprefix("reason: ")
            if [_junit_escape(line) for line in lines] == segments:
                counts.add(len(segments) - 1)
    return _one_extra_count(counts, entry, "XFAIL")


def _xpass_lines(summary: list[str], skip_reasons: list[str],
                 xfail_reasons: dict[str, list[str]]) -> list[str]:
    """The XPASS entries of the short test summary.

    -ra prints its entries in a fixed order: skips, xfails, xpasses, errors,
    failures (measured on 9.2, serial and xdist; test_real_ra_entry_order
    re-measures it). Every error and failure
    message therefore follows the last XPASS entry, so lines are read only up
    to the first ERROR or FAILED entry. Each SKIPPED and XFAIL entry passes
    over the lines its reason continues on, read from the run record's skip
    sites and the JUnit xfail reasons, so a reason line that starts with
    'XPASS ', 'ERROR ' or 'FAILED ' is never read as an entry. An XPASS
    entry's own reason is not in that evidence: a continuation line of it that
    starts with 'XPASS ' is counted, and the count check then refuses the run.
    """
    payloads = []
    index = 0
    while index < len(summary):
        line = summary[index]
        index += 1
        if line.startswith(("ERROR ", "FAILED ")):
            break
        if line.startswith("XPASS "):
            payloads.append(line[len("XPASS "):])
        elif line.startswith("SKIPPED "):
            index += _skip_extra_lines(line, skip_reasons)
        elif line.startswith("XFAIL "):
            index += _xfail_extra_lines(line, summary[index:], xfail_reasons)
    return payloads


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


def _apply_xpass(cases: list[dict], summaries: list[list[str]], terminal: list[str],
                 record: dict) -> None:
    """Read the XPASS entries from the short test summary and mark them.

    A log with more than one 'short test summary info' header is refused: a
    child pytest's report printed in a test's output, whether in its captured
    output or inside a failure message in the summary itself, carries the same
    header, and the log alone cannot tell it from pytest's own. That refuses a
    valid red run whose failure message holds a child pytest's report. With one
    header, its summary is read, and a summary that does not read cleanly
    against the run's evidence refuses the run.
    """
    expected_count = record["xpassed"]
    sites = record.get("skip_sites")
    skip_reasons = [site["reason"] for site in sites if isinstance(site, dict)
                    and isinstance(site.get("reason"), str)] if isinstance(sites, list) else []
    xfail_reasons = {}
    for case in cases:
        if case["outcome"] == "xfail":
            xfail_reasons.setdefault(case["node_id"], []).append(case["reason"] or "")
    if len(summaries) > 1:
        raise EvidenceError(
            f"the log holds {len(summaries)} short test summary headers, so a child "
            f"pytest's report printed in a test's output cannot be told from "
            f"pytest's own summary")
    payloads = _xpass_lines(summaries[0] if summaries else [], skip_reasons, xfail_reasons)
    if len(payloads) != expected_count or len(set(payloads)) != len(payloads):
        raise EvidenceError(
            f"raw XPASS evidence count/uniqueness mismatch: {len(payloads)} "
            f"lines, record xpassed={expected_count}")
    entries = [_resolve_xpass(payload, terminal) for payload in payloads]
    by_key = {}
    for case in cases:
        by_key.setdefault((case["classname"], case["name"]), []).append(case)
    resolved = set()
    for node_id, reason in entries:
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
        # The record holds the reason as pytest printed it; JUnit holds it as
        # junitxml escaped it, so the record side is escaped to compare.
        recorded[(site["file"], _junit_escape(site["reason"]))] += count
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
                  expected_sha: str) -> tuple[list[str], list[str], str, int, range]:
    """Validate the raw log against the record; return lines, argv, summary, checks
    and the range of pytest's report sections, which no evidence read uses."""
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
    report = _test_text_range(lines, summary_index)
    evidence = [line for index, line in enumerate(lines) if index not in report]
    collection_marker = "FULL-COLLECTION-OK "
    if not any(line.startswith(collection_marker)
               and line.endswith(f"subject={expected_sha}") for line in evidence):
        raise EvidenceError("FULL-COLLECTION-OK on the expected SHA is absent")
    full_run_ok = [line for line in evidence if line.startswith("FULL-RUN-OK ")]
    if any(not line.endswith(f"subject={expected_sha}") for line in full_run_ok):
        raise EvidenceError("FULL-RUN-OK names a different SHA")
    # run.log is the job log download. A step's stderr reaches it: measured on
    # run 42830046567, whose job log carries the RUN-VERIFY-FAILURE line that
    # verify_full_run.py prints to stderr. No real drift run has been seen, so
    # that the binding's stderr heading is in a real drift log is inferred from
    # that measurement, not observed. The binding's session-end check runs after
    # the censuses and sets 3 even over a run a census raised to 1. Drift is
    # named before the match lines are counted: when the installed set changed
    # during the run, the after-tests verify step reports the drift too and
    # prints no match line, so a count-first check would hide the cause.
    if any("CI ENVIRONMENT DRIFT" in line for line in evidence):
        message = ("the CI environment binding reported environment drift (a CI "
                   "ENVIRONMENT DRIFT heading is in the log), so the run is invalid "
                   "infrastructure")
        names = _census_names(lines)
        if names and record["exitstatus"] == 3:
            message += f", and it set exit status 3 over the {names} census failure"
        raise EvidenceError(message)
    environment_indexes = [index for index, line in enumerate(lines)
                           if index not in report
                           and "CI environment matches constraints-ci.txt." in line]
    if (len(environment_indexes) < 2
            or min(environment_indexes) > summary_index
            or max(environment_indexes) < summary_index):
        raise EvidenceError("before/after environment evidence is not clean")
    if any("PYTEST_ADDOPTS is set" in line for line in evidence):
        raise EvidenceError(
            "a check in the log reported PYTEST_ADDOPTS is set, so the run's "
            "options are not the reviewed command alone")
    return lines, argv, summary, len(environment_indexes), report


def _census_names(lines: list[str]) -> str:
    """The censuses that failed the session, joined for a message; empty when the
    session-finish span holds none or cannot be read, so a malformed census block
    never replaces the drift refusal."""
    try:
        failures = _census_failures(_session_spans(lines)[1])
    except EvidenceError:
        return ""
    return " and ".join(failure["census"] for failure in failures)


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


def _validate_junit(record: dict, summaries: list[list[str]], terminal: list[str],
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
    _apply_xpass(cases, summaries, terminal, record)
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
                           overall_conclusion: str,
                           census_failures: list[dict]) -> tuple[str, list[str], str]:
    """Reconcile exit status, verifier disposition, and the workflow conclusion.

    A green run that a census failed is a census outcome, checked like a red
    run: the censuses raise only a clean status, so its exit status must be 1.
    """
    full_run_ok = [line for line in lines if line.startswith("FULL-RUN-OK ")]
    verifier_state, verifier_failures = _full_run_verifier(
        metadata, lines, bool(full_run_ok))
    red = record["failed"] > 0 or record["errors"] > 0
    if census_failures and not red and record["exitstatus"] != 1:
        names = " and ".join(failure["census"] for failure in census_failures)
        if record["exitstatus"] == 0:
            raise EvidenceError(
                f"the {names} census reported a failure beside exit status 0; a "
                "census raises a clean exit status to 1, so the run record and "
                "the log disagree")
        raise EvidenceError(
            f"the {names} census reported a failure beside exit status "
            f"{record['exitstatus']}, which a census does not produce, so another "
            "cause set the exit status")
    if red and record["exitstatus"] not in (1, 2):
        # pytest ends a session with failures or errors at 1, or at 2 when it
        # was interrupted (collection errors). 3 is pytest's internal error or
        # the CI binding's drift status, and 4 and 5 carry no test outcome.
        raise EvidenceError(
            f"red test outcomes beside exit status {record['exitstatus']}, which "
            "a test failure does not produce, so another cause set the exit status")
    census = not red and bool(census_failures)
    failing = red or census
    if failing and overall_conclusion != "failure":
        raise EvidenceError(
            "red test outcomes have an inconsistent overall conclusion" if red
            else "a census failure has an inconsistent overall conclusion")
    if not failing and overall_conclusion != "success":
        raise EvidenceError("green test outcomes have an inconsistent overall conclusion")
    if failing and (record["exitstatus"] == 0 or full_run_ok or verifier_state == "missing"):
        raise EvidenceError(
            "red outcomes lack consistent exit and verifier evidence" if red
            else "a census failure lacks consistent exit and verifier evidence")
    if not failing and record["exitstatus"] != 0:
        raise EvidenceError("nonzero exit without a test outcome is invalid infrastructure")
    clean = not failing and record["exitstatus"] == 0
    if clean and (len(full_run_ok) != 1 or record["passed"] <= 0 or verifier_failures):
        raise EvidenceError("a clean run needs positive passes and one FULL-RUN-OK")
    validity = "clean" if clean else "census_outcome" if census else "test_outcome"
    return verifier_state, verifier_failures, validity


def _normalize_run(logical: str, directory: Path, expected_sha: str) -> dict:
    metadata = _json_object(directory / "metadata.json")
    _validate_metadata(metadata, expected_sha)
    artifact = directory / "artifact"
    record = _json_object(artifact / ".full-run-record.json")
    collection = _json_object(artifact / ".full-collection-record.json")
    collected, terminal = _validate_records(metadata, record, collection, expected_sha)
    lines, argv, summary, environment_passes, report = _validate_log(
        directory, record, expected_sha)
    # Evidence is read outside pytest's report sections, where only a test's
    # own text can be; XPASS lines only from the short summary, the one place
    # -ra prints them.
    evidence = [line for index, line in enumerate(lines) if index not in report]
    summaries = [[lines[index] for index in summary]
                 for summary in _short_summary_ranges(lines, report)]
    _header, finish_span = _session_spans(lines)
    shuffle_seed = _run_seed(lines, report)
    census_failures = _census_failures(finish_span)
    if shuffle_seed is not None and len(argv) == 9:
        raise EvidenceError(
            "a pytest-randomly seed line sits beside the 9-token run line, "
            "which has no seed option and cannot load pytest-randomly")
    overall_conclusion = _require_environment_step_success(metadata)
    cases, skip_sites, collection_skip_sites = _validate_junit(
        record, summaries, terminal, artifact)
    verifier_state, verifier_failures, validity = _validate_run_outcomes(
        record, metadata, evidence, overall_conclusion, census_failures)
    return {
        "logical_name": logical, "raw_paths": RAW_PATHS, "evidence_errors": [],
        "validity": validity,
        "identity": {"database_id": metadata["databaseId"],
                     "workflow_database_id": metadata["workflowDatabaseId"],
                     "attempt": 1, "head_sha": expected_sha,
                     "head_branch": metadata.get("headBranch"),
                     "url": metadata.get("url")},
        # Beside identity, never in environment: a seed is the variable under
        # test, so it must not make a delta unattributable.
        "shuffle_seed": shuffle_seed,
        # Outcome evidence, not environment: a census failure is a suite failure.
        "census_failures": census_failures,
        "argv": argv,
        "environment": {
            "overall_conclusion": overall_conclusion,
            "passed_checks": environment_passes,
            "platform": [line for line in evidence if line.startswith("platform ")],
            "measurements": [line for line in evidence if "sys.executable=" in line],
            # Job NAMES identify the workflow shape; the runner instance id is
            # a fresh value on every GitHub-hosted run and attributes nothing.
            "job_names": [job.get("name") for job in metadata.get("jobs", [])
                          if isinstance(job, dict)],
            "pins": [line for line in evidence if "constraints-ci.txt" in line],
            "cache": [line for line in evidence if line.startswith(CACHE_PREFIXES)],
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


def _input_runs(runs: list[dict]) -> list[dict]:
    """One summary per input run; a run refused during normalization has no
    identity or seed, so both read None."""
    return [{"logical_name": run["logical_name"], "identity": run.get("identity"),
             "shuffle_seed": run.get("shuffle_seed"), "validity": run["validity"]}
            for run in runs]


def _compare(runs: list[dict], expected_sha: str, errors: list[dict]) -> dict:
    comparison = {"schema_version": OUTPUT_SCHEMA_VERSION, "expected_sha": expected_sha,
                  "input_runs": _input_runs(runs),
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
        census_before = sorted({failure["census"] for failure in reference["census_failures"]})
        census_after = sorted({failure["census"] for failure in run["census_failures"]})
        if census_after != census_before:
            comparison["deltas"].append({"type": "census", "run": label,
                                         "before": census_before, "after": census_after})
        # A census that failed both runs is compared by the tests it refused
        # in, so the same census failing on different launches is a delta too.
        tests_before = {failure["census"]: _census_tests(failure)
                        for failure in reference["census_failures"]}
        for failure in run["census_failures"]:
            name = failure["census"]
            if name in tests_before and _census_tests(failure) != tests_before[name]:
                comparison["deltas"].append(
                    {"type": "census_tests", "run": label, "census": name,
                     "before": tests_before[name], "after": _census_tests(failure)})
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
    return {"schema_version": OUTPUT_SCHEMA_VERSION, "expected_sha": expected_sha,
            "input_runs": _input_runs(runs),
            "status": "invalid", "deltas": [], "observations": [],
            "evidence_errors": errors}


_RUN_LAYOUT_EPILOG = """\
A --run directory is one downloaded workflow run:
  metadata.json                          the /actions/runs/<id> object with a
                                         "jobs" array merged in from the
                                         separate /actions/runs/<id>/jobs call,
                                         and the identity keys normalized onto
                                         the top level: attempt, databaseId,
                                         workflowDatabaseId and headSha, added
                                         beside the response's own run_attempt /
                                         id / workflow_id / head_sha spellings.
                                         An un-normalized object is refused
                                         (the raw response carries none of the
                                         four names)
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
            json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, **run}, sort_keys=True)
            + "\n" for run in runs))
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
