"""Verify the RUN that certifies a merge, not a separate collection of it.

WHAT THIS EXISTS TO STOP
  verify_full_collection.py proves the corpus COLLECTS completely, but it
  inspects a separate `--collect-only` run. Nothing used to read the summary
  of the certifying run itself, so a conftest that deselects at run time was
  invisible: measured, a session that reported `1 passed, 50 deselected` and
  exited 0 left the required check green with the corpus unexecuted.

HOW IT KNOWS
  An in-band pytest plugin writes a run record at pytest_sessionfinish. This
  script reads that record and refuses to certify anything it cannot prove.
  A missing record is a FAILURE, never "nothing to check": the whole point is
  that the gate must not be green when the evidence is absent.

THE SKIP IDENTITY, AND WHY IT IS NOT `executed == collected`
  The suite carries legitimate skips. A skip produces no call-phase report,
  so a literal executed-equals-collected rule would demand skipped == 0 and
  redden on every real run. The non-vacuous identity is
  `passed + failed + errors + skipped + xfailed + xpassed == collected`,
  which also equals "every collected node ID reached a terminal outcome".
  Both are checked: the arithmetic, and the size of the terminal node-ID set.

FAIL CLOSED, ALWAYS. Every condition below is a named failure with exit 1.
There is no skip path and no "warn" path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 1
RECORD_NAME = ".pytest-run-record.json"
COLLECTION_RECORD_NAME = ".full-collection-record.json"

_REQUIRED_KEYS = (
    "collected", "passed", "skipped", "failed", "errors", "deselected",
    "xfailed", "xpassed", "exitstatus", "summary_line", "terminal_node_ids",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the in-band record of the certifying full-suite run.")
    parser.add_argument("--record", default=None,
                        help=f"run record path (default: "
                             f"${{GITHUB_WORKSPACE}}/{RECORD_NAME}, else the "
                             f"repository root)")
    parser.add_argument("--collection-record", default=None,
                        help=f"optional collection record path (default: "
                             f"${{GITHUB_WORKSPACE}}/{COLLECTION_RECORD_NAME} "
                             f"when it exists)")
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parent.parent)
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

        collection_counts = None
        collection_path = (Path(args.collection_record)
                           if args.collection_record
                           else record_path.with_name(COLLECTION_RECORD_NAME))
        if collection_path.is_file():
            collection = _load(collection_path, "COLLECTION-RECORD-ABSENT")
            collection_counts = collection.get("collected_items")
            if not isinstance(collection_counts, int):
                raise RunFailure(
                    "COLLECTION-RECORD-BAD",
                    f"{collection_path} carries collected_items "
                    f"{collection_counts!r}; expected an integer",
                )
            if collection_counts != collected:
                raise RunFailure(
                    "COLLECTION-COUNT-MISMATCH",
                    f"the certifying run collected {collected} items but the "
                    f"independent collection saw {collection_counts}. A "
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
        f"collection_count={collection_counts} subject={subject}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())