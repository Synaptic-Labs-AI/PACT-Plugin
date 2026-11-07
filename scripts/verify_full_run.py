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
  Skips are admitted site by site from a committed allowlist
  (scripts/full_suite_skip_allowlist.json), and an entry that admits no
  skip of the certifying run is stale and fails it. A run that passed
  nothing, or that exited non-zero, fails whatever else it reports.

FAIL CLOSED, ALWAYS. Every condition below is a named failure with exit 1.
There is no skip path and no "warn" path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 3
RECORD_NAME = ".full-run-record.json"

# Every skip the certifying run reports must match an entry in this file,
# keyed on (file, verbatim reason) and never on a line number, and no site
# may skip more often than its entry allows. A commit that adds a legitimate
# skip adds its entry in the SAME commit; the file's "about" field says how.
SKIP_ALLOWLIST = Path(__file__).resolve().with_name(
    "full_suite_skip_allowlist.json")

_REQUIRED_KEYS = (
    "collected", "passed", "skipped", "failed", "errors", "deselected",
    "xfailed", "xpassed", "exitstatus", "summary_line", "terminal_node_ids",
    "items_before_modifyitems", "items_after_modifyitems", "run_id",
    "run_attempt", "skip_sites", "skipped_at_collection",
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


def load_skip_allowlist(path: Path) -> list[dict]:
    """The validated allowlist entries. Raises RunFailure(ALLOWLIST-INVALID)."""
    def invalid(detail: str) -> RunFailure:
        return RunFailure("ALLOWLIST-INVALID", f"{path}: {detail}")

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise invalid(f"not readable JSON ({exc})") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise invalid("expected a JSON object with schema_version 1")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise invalid("entries must be a list")
    seen = set()
    for index, entry in enumerate(entries):
        where = f"entries[{index}]"
        if not isinstance(entry, dict):
            raise invalid(f"{where} is not an object")
        file, reason = entry.get("file"), entry.get("reason")
        if not (isinstance(file, str) and file
                and isinstance(reason, str) and reason):
            raise invalid(f"{where} needs a non-empty file and reason")
        if entry.get("match") not in ("exact", "prefix"):
            raise invalid(f"{where} match must be 'exact' or 'prefix'")
        allowed = entry.get("allowed")
        if (not isinstance(allowed, int) or isinstance(allowed, bool)
                or allowed < 1):
            raise invalid(f"{where} allowed must be an integer >= 1")
        if (file, reason) in seen:
            raise invalid(f"{where} duplicates ({file!r}, {reason!r})")
        seen.add((file, reason))
    return entries


def _entry_for(site: dict, entries: list[dict]) -> dict | None:
    for entry in entries:
        if entry["file"] != site["file"]:
            continue
        if entry["match"] == "exact" and entry["reason"] == site["reason"]:
            return entry
        if (entry["match"] == "prefix"
                and site["reason"].startswith(entry["reason"])):
            return entry
    return None


def skip_site_problems(sites: list[dict], entries: list[dict]
                       ) -> list[tuple[str, str]]:
    """(condition, detail) for each recorded skip site the allowlist refuses.

    Counts are summed per ENTRY, so a prefix entry bounds every reason it
    admits together.
    """
    problems: list[tuple[str, str]] = []
    per_entry: dict[int, int] = {}
    for site in sites:
        entry = _entry_for(site, entries)
        if entry is None:
            problems.append((
                "SKIP-UNLISTED",
                f"{site['file']}: {site['reason']!r} skipped {site['count']} "
                f"time(s) and is not in the skip allowlist"))
            continue
        per_entry[id(entry)] = per_entry.get(id(entry), 0) + site["count"]
    for entry in entries:
        allowed = entry["allowed"]
        count = per_entry.get(id(entry), 0)
        if count > allowed:
            problems.append((
                "SKIP-OVER-ALLOWANCE",
                f"{entry['file']}: {entry['reason']!r} skipped {count} "
                f"time(s); the allowlist admits {allowed}"))
    return problems


def unused_allowlist_entries(sites: list[dict], entries: list[dict]
                             ) -> list[dict]:
    """The entries that admit none of the recorded skip sites.

    The certifying run is the only environment this verifier judges, so an
    entry no skip uses there admits nothing and is stale. Only the full-run
    check calls this: the fast lane runs a subset, where unused entries are
    expected.
    """
    used = {id(entry) for site in sites
            if (entry := _entry_for(site, entries)) is not None}
    return [entry for entry in entries if id(entry) not in used]


def _require_skip_sites(record: dict) -> list[dict]:
    sites = record.get("skip_sites")
    if not isinstance(sites, list) or any(
            not isinstance(site, dict)
            or not isinstance(site.get("file"), str)
            or not isinstance(site.get("reason"), str)
            or not isinstance(site.get("count"), int)
            or isinstance(site.get("count"), bool)
            or site["count"] < 1
            for site in sites):
        raise RunFailure(
            "SKIP-SITES-BAD",
            "skip_sites must be a list of {file, reason, count >= 1}")
    return sites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the in-band record of the certifying full-suite run.")
    parser.add_argument("--record", default=None,
                        help=f"run record path ({RECORD_NAME} as the in-band "
                             f"plugin wrote it). Required: the path is never "
                             f"derived from the environment, and without it "
                             f"the run fails RECORD-ABSENT.")
    parser.add_argument("--collection-record", default=None,
                        help="collection record path, as written by "
                             "verify_full_collection.py --record. Required: "
                             "without it the run fails "
                             "COLLECTION-RECORD-ABSENT.")
    parser.add_argument("--skip-allowlist", default=str(SKIP_ALLOWLIST),
                        help="skip allowlist path (default: the committed "
                             "file beside this script)")
    args = parser.parse_args(argv)

    try:
        if not args.record:
            raise RunFailure(
                "RECORD-ABSENT",
                "no --record was given. The run record's path is always "
                "explicit; reading one from an ambient location could read a "
                "record a rig wrote.")
        record_path = Path(args.record)
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
        exitstatus = _require_int(record, "exitstatus")
        if exitstatus != 0:
            raise RunFailure(
                "EXITSTATUS",
                f"exitstatus is {exitstatus}; the certifying run must exit 0. "
                f"A session that exited non-zero failed, whatever its counts "
                f"say.",
            )
        sites = _require_skip_sites(record)
        if sum(site["count"] for site in sites) != counts["skipped"]:
            raise RunFailure(
                "SKIP-SITES-INCOMPLETE",
                f"skip_sites account for "
                f"{sum(site['count'] for site in sites)} skips but the run "
                f"reports {counts['skipped']}")
        entries = load_skip_allowlist(Path(args.skip_allowlist))
        problems = skip_site_problems(sites, entries)
        if problems:
            condition = problems[0][0]
            raise RunFailure(
                condition, "; ".join(detail for _, detail in problems))
        unused = unused_allowlist_entries(sites, entries)
        if unused:
            raise RunFailure(
                "SKIP-ALLOWLIST-UNUSED",
                "; ".join(f"{entry['file']}: {entry['reason']!r} admits no "
                          f"skip this run reported" for entry in unused)
                + ". Remove each entry in the commit that stops its skip.")

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

        # A module-level skip counts as skipped but collects no item, so it
        # is outside the per-item identities.
        collection_skips = _require_int(record, "skipped_at_collection")
        if not 0 <= collection_skips <= counts["skipped"]:
            raise RunFailure(
                "BAD-FIELD-SKIPPED_AT_COLLECTION",
                f"skipped_at_collection is {collection_skips}; expected "
                f"0..{counts['skipped']}")
        total = sum(counts[key] for key in _ACCOUNTING_KEYS) - collection_skips
        problems: list[str] = []
        if total != collected:
            problems.append(
                "composition identity: passed+failed+errors+skipped+xfailed+"
                f"xpassed-skipped_at_collection = {total}, collected = "
                f"{collected} ("
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
