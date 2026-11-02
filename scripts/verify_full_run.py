"""Verify the RUN that certifies a merge, not a separate collection of it.

WHAT THIS EXISTS TO STOP
  verify_full_collection.py proves the corpus COLLECTS completely, but it
  inspects a separate `--collect-only` run. Nothing used to read the summary
  of the certifying run itself, so a conftest that deselects at run time was
  invisible: measured, a session that reported `1 passed, 50 deselected` and
  exited 0 left the required check green with the corpus unexecuted.

HOW IT KNOWS
  An in-band pytest plugin writes a run record at pytest_sessionfinish, and
  verify_full_collection.py writes a collection record to the path the
  workflow passes it. This script reads BOTH and refuses to certify anything
  it cannot prove. A missing record is a FAILURE, never "nothing to check":
  the whole point is that the gate must not be green when the evidence is
  absent.

THE SKIP IDENTITY, AND WHY IT IS NOT `executed == collected`
  The suite carries legitimate skips. A skip produces no call-phase report,
  so a literal executed-equals-collected rule would demand skipped == 0 and
  redden on every real run. The non-vacuous identity is
  `passed + failed + errors + skipped + xfailed + xpassed == collected`,
  which also equals "every collected node ID reached a terminal outcome".
  Both are checked: the arithmetic, and the size of the terminal node-ID set.
  Skips are bounded separately by SKIP_CEILING, and a run that passed
  nothing fails whatever else it reports.

FAIL CLOSED, ALWAYS. Every condition below is a named failure with exit 1.
There is no skip path and no "warn" path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 2
RECORD_NAME = ".pytest-run-record.json"

# The most skips a certifying run may report. Set from the CI-observed count
# of the tree that introduced this gate (27 skipped). A commit that adds a
# legitimate skip raises this number in the SAME commit, stating the skip it
# admits; a run above the ceiling fails rather than certifying a suite that
# quietly stopped executing part of itself.
SKIP_CEILING = 27

_REQUIRED_KEYS = (
    "collected", "passed", "skipped", "failed", "errors", "deselected",
    "xfailed", "xpassed", "exitstatus", "summary_line", "terminal_node_ids",
    "items_before_modifyitems", "items_after_modifyitems", "run_id",
    "run_attempt",
)
_ACCOUNTING_KEYS = (
    "passed", "failed", "errors", "skipped", "xfailed", "xpassed",
)


class RunFailure(Exception):
    """One named failure condition. The message names the condition."""

    def __init__(self, condition: str, detail: str):
        self.condition = condition
        self.detail = detail
        super().__init__(f"[{condition}] {detail}")


def _default_record_path() -> Path:
    """The record lives in the CI workspace and NOWHERE else.

    There is deliberately no repository-root fallback: the plugin writes
    nothing outside CI, and a fallback would either read a stale artifact or
    make a local run look like it had evidence. To probe locally, produce the
    record in an explicit rig with GITHUB_ACTIONS=true and GITHUB_WORKSPACE
    set, then pass --record.
    """
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        raise RunFailure(
            "RECORD-PATH-UNAVAILABLE",
            "GITHUB_WORKSPACE is not set, so the record path is unknown. The "
            "plugin writes nothing outside CI; produce a record in an "
            "explicit rig (GITHUB_ACTIONS=true, GITHUB_WORKSPACE=<rig>) and "
            "pass --record.",
        )
    return Path(workspace) / RECORD_NAME


def _load(path: Path, condition_absent: str) -> dict:
    if not path.is_file():
        raise RunFailure(
            condition_absent,
            f"{path} does not exist. A missing record is a failure, never "
            f"'nothing to check': without it the gate cannot tell a complete "
            f"run from one that never started.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunFailure(
            "RECORD-UNPARSEABLE", f"{path} is not readable JSON ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise RunFailure(
            "RECORD-UNPARSEABLE", f"{path} is not a JSON object at top level")
    return data


def _require_keys(record: dict) -> None:
    for key in _REQUIRED_KEYS:
        if key not in record:
            raise RunFailure(
                "KEY-MISSING", f"the run record is missing required key {key!r}")


def _require_summary(record: dict) -> None:
    line = record.get("summary_line")
    if not isinstance(line, str) or not line.strip():
        raise RunFailure(
            "NO-SUMMARY",
            "the run record carries no summary line. This is the 'the suite "
            "never produced a summary' case; a zero count cannot distinguish "
            "it from a clean run.",
        )


def _require_int(record: dict, key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RunFailure(
            f"BAD-FIELD-{key.upper()}", f"{key} is {value!r}; expected an integer")
    return value


def _require_run_identity(record: dict, label: str) -> None:
    """Under Actions, the record must name THIS run and THIS attempt.

    A record committed to the tree, or left by another run, otherwise reads
    exactly like the real one.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for key, env_name in (("run_id", "GITHUB_RUN_ID"),
                          ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        expected = os.environ.get(env_name)
        if not expected or record.get(key) != expected:
            raise RunFailure(
                "RUN-IDENTITY",
                f"{label}: {key} is {record.get(key)!r} but {env_name} is "
                f"{expected!r}; the record does not belong to this run",
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the in-band record of the certifying full-suite run.")
    parser.add_argument("--record", default=None,
                        help=f"run record path (default: "
                             f"${{GITHUB_WORKSPACE}}/{RECORD_NAME}; there is "
                             f"no fallback when GITHUB_WORKSPACE is unset)")
    parser.add_argument("--collection-record", default=None,
                        help="collection record path, as written by "
                             "verify_full_collection.py --record. Required: "
                             "without it the run fails "
                             "COLLECTION-RECORD-ABSENT.")
    args = parser.parse_args(argv)

    try:
        record_path = (Path(args.record) if args.record
                       else _default_record_path())
        record = _load(record_path, "RECORD-ABSENT")

        if record.get("schema_version") != SCHEMA_VERSION:
            raise RunFailure(
                "SCHEMA-VERSION",
                f"schema_version is {record.get('schema_version')!r}, "
                f"expected {SCHEMA_VERSION}",
            )

        _require_keys(record)
        _require_run_identity(record, "run record")
        _require_summary(record)

        collected = _require_int(record, "collected")
        counts = {key: _require_int(record, key) for key in _ACCOUNTING_KEYS}
        deselected = _require_int(record, "deselected")

        if collected <= 0:
            raise RunFailure(
                "COLLECTED", f"collected is {collected}; expected > 0")
        if deselected != 0:
            raise RunFailure(
                "DESELECTED",
                f"deselected is {deselected}; the unrestricted gate "
                f"must not deselect anything",
            )
        if counts["failed"] != 0:
            raise RunFailure("FAILED", f"failed is {counts['failed']}")
        if counts["errors"] != 0:
            raise RunFailure("ERRORS", f"errors is {counts['errors']}")
        if counts["passed"] <= 0:
            raise RunFailure(
                "NOTHING-PASSED",
                f"passed is {counts['passed']}; a run that passed nothing "
                f"executed nothing worth certifying",
            )
        if counts["skipped"] > SKIP_CEILING:
            raise RunFailure(
                "SKIP-CEILING",
                f"skipped is {counts['skipped']}, above the committed ceiling "
                f"{SKIP_CEILING}. Raise SKIP_CEILING in the commit that adds "
                f"a legitimate skip; otherwise find what stopped executing.",
            )

        before = _require_int(record, "items_before_modifyitems")
        after = _require_int(record, "items_after_modifyitems")
        if after != collected or after + deselected != before:
            raise RunFailure(
                "COLLECTION-HOOK-NARROWING",
                f"the collection hooks took {before} items to {after} with "
                f"{deselected} reported deselected and {collected} collected. "
                f"Items removed without being reported as deselected are a "
                f"narrowing the count comparison cannot see.",
            )

        terminal = record.get("terminal_node_ids")
        if not isinstance(terminal, list) or any(
                not isinstance(item, str) for item in terminal):
            raise RunFailure(
                "TERMINAL-SET",
                "terminal_node_ids must be a list of strings",
            )

        total = sum(counts[key] for key in _ACCOUNTING_KEYS)
        problems: list[str] = []
        if total != collected:
            problems.append(
                "composition identity: passed+failed+errors+skipped+xfailed+"
                f"xpassed = {total}, collected = {collected} ("
                + ", ".join(f"{key}={counts[key]}" for key in _ACCOUNTING_KEYS)
                + ")")
        if len(terminal) != collected:
            problems.append(
                f"terminal_node_ids: {len(terminal)} entries, collected = "
                f"{collected}")
        if len(set(terminal)) != len(terminal):
            problems.append("terminal_node_ids contains duplicates")
        if problems:
            # BOTH identities are reported when both disagree. They fail for
            # different causes, and picking one hides the other.
            raise RunFailure(
                "ACCOUNTING" if total != collected else "TERMINAL-SET",
                "; ".join(problems),
            )

        if not args.collection_record:
            raise RunFailure(
                "COLLECTION-RECORD-ABSENT",
                "no --collection-record was given. The run is always compared "
                "with an independent collection; a missing comparison is a "
                "failure, never a skipped check.",
            )
        collection = _load(Path(args.collection_record),
                           "COLLECTION-RECORD-ABSENT")
        collection_count = collection.get("collected_items")
        if (not isinstance(collection_count, int)
                or isinstance(collection_count, bool)):
            raise RunFailure(
                "COLLECTION-RECORD-BAD",
                f"{args.collection_record} carries collected_items "
                f"{collection_count!r}; expected an integer",
            )
        _require_run_identity(collection, "collection record")
        if collection_count != collected:
            raise RunFailure(
                "COLLECTION-COUNT-MISMATCH",
                f"the certifying run collected {collected} items but the "
                f"independent collection saw {collection_count}. A "
                f"run-only narrowing is exactly what this comparison "
                f"exists to catch.",
            )
    except RunFailure as failure:
        print(f"RUN-VERIFY-FAILURE {failure.condition}: {failure.detail}",
              file=sys.stderr)
        return 1

    subject = os.environ.get("GITHUB_SHA") or "local"
    print(
        f"FULL-RUN-OK collected={collected} passed={counts['passed']} "
        f"skipped={counts['skipped']} failed={counts['failed']} "
        f"errors={counts['errors']} deselected={deselected} "
        f"collection_count={collection_count} subject={subject}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
