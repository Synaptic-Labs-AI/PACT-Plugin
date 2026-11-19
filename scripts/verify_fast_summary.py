"""Verify fast-suite artifacts and the distributed run record. Never runs pytest.

The selector and workflow base SHA are independent inputs. The serial canary
proves the exact node IDs that selection collected; the real run and its
schema-v3 in-band record must prove the same nodes reached terminal outcomes,
with every xdist worker reporting and all outcome/skip arithmetic intact.
Absent evidence is a failure, never a zero. Full-suite skip-allowlist findings
remain advisory because this lane deliberately runs a selected subset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fast_suite_selection import (  # noqa: E402  (path must precede import)
    ManifestError,
    build_argv,
    load_manifest,
    parse_pytest_summary,
    select_for_change,
)
from verify_full_run import (  # noqa: E402  (path must precede import)
    RunFailure,
    SKIP_ALLOWLIST,
    _load as _load_record,
    _require_int as _require_record_int,
    _require_keys as _require_record_keys,
    _require_run_identity,
    _require_skip_sites,
    load_skip_allowlist,
    skip_site_problems,
)

SCHEMA_VERSION = 2
RECORD_SCHEMA_VERSION = 3
_OUTCOME_KEYS = (
    "passed", "failed", "errors", "skipped", "deselected", "xfailed", "xpassed",
)
_SELECTION_KEYS = (
    "status", "select_all_reason", "discovered", "excluded", "added_back",
    "included", "included_files", "included_sha256",
)
_CANARY_KEYS = (
    "command", "ignore_paths", "rc", "summary_line", "expected_files",
    "collected_files", "collected_items", "collected_node_ids", "missing",
    "extra", "unresolvable_node_id_bases",
)
_RUN_KEYS = ("command", "rc", "summary_line", "collected", *_OUTCOME_KEYS)
_COMMON_KEYS = (
    "manifest", "base_sha", "head_sha", "run_id", "run_attempt", "selection",
    "mode",
)


class VerificationFailure(Exception):
    """One named failure condition. The message names the condition."""

    def __init__(self, condition: str, detail: str):
        self.condition = condition
        self.detail = detail
        super().__init__(f"[{condition}] {detail}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_artifact(path: Path, label: str) -> dict:
    if not path.is_file():
        raise VerificationFailure(
            f"ARTIFACT-ABSENT-{label.upper()}",
            f"{path} does not exist. A missing artifact is a failure, never "
            "'nothing to check'.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(
            f"ARTIFACT-UNPARSEABLE-{label.upper()}",
            f"{path} is not readable JSON ({exc})",
        ) from exc
    if not isinstance(data, dict):
        raise VerificationFailure(
            f"ARTIFACT-UNPARSEABLE-{label.upper()}",
            f"{path} does not carry a JSON object at top level",
        )
    return data


def _require_keys(artifact: dict, keys: tuple[str, ...], parent: str,
                  label: str) -> None:
    for key in keys:
        if key not in artifact:
            raise VerificationFailure(
                f"KEY-MISSING-{label.upper()}",
                f"{label}: required key {key!r} is missing from {parent}",
            )


def _require_schema_version(artifact: dict, label: str) -> None:
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise VerificationFailure(
            f"SCHEMA-VERSION-{label.upper()}",
            f"{label}: schema_version is {artifact.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}",
        )


def _require_summary_line(artifact: dict, label: str) -> dict:
    line = artifact.get("summary_line")
    parsed = parse_pytest_summary(line) if isinstance(line, str) else None
    if parsed is None:
        raise VerificationFailure(
            f"NO-SUMMARY-{label.upper()}",
            f"{label}: summary_line {line!r} is absent or unparseable; counts "
            "cannot distinguish that from a clean run.",
        )
    return parsed


def _positive_int(container: dict, key: str, condition: str, label: str) -> int:
    value = container.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VerificationFailure(
            condition, f"{label}.{key} is {value!r}; expected an integer > 0")
    return value


def _outcome_int(container: dict, key: str, label: str) -> int:
    value = container.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise VerificationFailure(
            f"BAD-FIELD-{label.upper()}-{key.upper()}",
            f"{label}.{key} is {value!r}; expected an integer >= 0")
    return value


def _record_outcome_int(record: dict, key: str) -> int:
    value = _require_record_int(record, key)
    if value < 0:
        raise VerificationFailure(
            f"BAD-FIELD-{key.upper()}",
            f"record.{key} is {value!r}; expected an integer >= 0")
    return value


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise VerificationFailure(
            f"BAD-FIELD-{label.upper().replace('.', '-')}",
            f"{label} is {value!r}; expected a JSON object")
    return value


def _set_difference(expected: list[str], actual: list[str],
                    condition: str, label: str) -> None:
    if (not isinstance(actual, list)
            or any(not isinstance(item, str) or not item for item in actual)):
        raise VerificationFailure(
            condition, f"{label} must be a list of non-empty file paths")
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing or extra:
        raise VerificationFailure(
            condition,
            f"{label}: file-set mismatch. missing={missing} extra={extra}",
        )


def _require_artifact_shape(canary: dict, summary: dict, record: dict) -> tuple[dict, dict, dict]:
    _require_schema_version(canary, "canary")
    _require_schema_version(summary, "run")
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise VerificationFailure(
            "SCHEMA-VERSION-RECORD",
            f"record schema_version is {record.get('schema_version')!r}; "
            f"expected {RECORD_SCHEMA_VERSION}")
    _require_keys(canary, (*_COMMON_KEYS, "canary"), "the canary artifact", "canary")
    _require_keys(summary, (*_COMMON_KEYS, "run"), "the run artifact", "run")
    _require_record_keys(record)
    for artifact, nested, label, expected_mode in (
            (canary, "canary", "canary", "collect-only"),
            (summary, "run", "run", "run")):
        if artifact["mode"] != expected_mode:
            raise VerificationFailure(
                "MODE-MISMATCH",
                f"{label} mode is {artifact['mode']!r}; expected {expected_mode!r}")
        _require_object(artifact["manifest"], f"{label}.manifest")
        _require_keys(artifact["manifest"], ("path", "sha256"),
                      f"{label}.manifest", label)
        _require_object(artifact["selection"], f"{label}.selection")
        _require_keys(artifact["selection"], _SELECTION_KEYS,
                      f"{label}.selection", label)
        _require_object(artifact[nested], f"{label}.{nested}")
        _require_keys(artifact[nested],
                      _CANARY_KEYS if label == "canary" else _RUN_KEYS,
                      f"{label}.{nested}", label)
    canary_summary = _require_summary_line(canary["canary"], "canary")
    run_summary = _require_summary_line(summary["run"], "run")
    record_summary = _require_summary_line(record, "record")
    return canary_summary, run_summary, record_summary


def _require_identity(canary: dict, summary: dict, record: dict) -> None:
    for key in ("base_sha", "head_sha"):
        if canary.get(key) != summary.get(key):
            raise VerificationFailure(
                "ARTIFACT-SHA-MISMATCH",
                f"{key}: canary records {canary.get(key)!r} but run records "
                f"{summary.get(key)!r}")
    for key, env_name in (("run_id", "GITHUB_RUN_ID"),
                          ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        values = (canary.get(key), summary.get(key), record.get(key))
        if any(value != values[0] for value in values[1:]):
            raise VerificationFailure(
                "RUN-IDENTITY", f"{key} differs across canary/run/record: {values!r}")
        value = values[0]
        if value is not None and (not isinstance(value, str) or not value):
            raise VerificationFailure(
                "RUN-IDENTITY", f"{key} must be a non-empty string or null, got {value!r}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            expected = os.environ.get(env_name)
            if not expected or value != expected:
                raise VerificationFailure(
                    "RUN-IDENTITY",
                    f"{key} is {value!r} but {env_name} is {expected!r}")
    _require_run_identity(record, "run record")


def _require_commands(canary: dict, summary: dict, recomputed, plugin_root: Path) -> None:
    canary_command = canary["canary"]["command"]
    run_command = summary["run"]["command"]
    canary_ignores = canary["canary"]["ignore_paths"]
    if (not isinstance(canary_command, list)
            or any(not isinstance(argument, str) for argument in canary_command)):
        raise VerificationFailure(
            "CANARY-COMMAND-MISMATCH", "canary command must be a list of strings")
    if (not isinstance(run_command, list)
            or any(not isinstance(argument, str) for argument in run_command)):
        raise VerificationFailure(
            "RUN-COMMAND-MISMATCH", "run command must be a list of strings")
    if (not isinstance(canary_ignores, list)
            or any(not isinstance(argument, str) for argument in canary_ignores)):
        raise VerificationFailure(
            "IGNORE-ARGS-MISMATCH", "canary ignore_paths must be a list of strings")
    run_ignores = [
        argument.split("=", 1)[1] for argument in run_command
        if argument.startswith("--ignore=")
    ]
    if sorted(canary_ignores or []) != sorted(run_ignores):
        raise VerificationFailure(
            "IGNORE-ARGS-MISMATCH",
            f"canary/run ignore paths differ: {canary_ignores!r} != {run_ignores!r}")
    expected_canary = build_argv(recomputed, plugin_root, "collect-only")
    expected_run = build_argv(recomputed, plugin_root, "run", workers=4)
    if canary_command != expected_canary:
        raise VerificationFailure(
            "CANARY-COMMAND-MISMATCH",
            f"canary command {canary_command!r} != independently constructed {expected_canary!r}")
    if run_command != expected_run:
        raise VerificationFailure(
            "RUN-COMMAND-MISMATCH",
            f"run command {run_command!r} != independently constructed {expected_run!r}")


def _require_process_and_workers(canary: dict, summary: dict, record: dict) -> None:
    if canary["canary"]["rc"] != 0:
        raise VerificationFailure("CANARY-RC", f"canary rc is {canary['canary']['rc']}")
    if summary["run"]["rc"] != 0:
        raise VerificationFailure("RUN-RC", f"run rc is {summary['run']['rc']}")
    if _require_record_int(record, "exitstatus") != 0:
        raise VerificationFailure("EXITSTATUS", f"record exitstatus is {record['exitstatus']}")
    deselected = _record_outcome_int(record, "deselected")
    for key in ("failed", "errors", "deselected"):
        if summary["run"].get(key) != 0:
            raise VerificationFailure(
                f"RUN-{key.upper()}", f"run.{key} is {summary['run'].get(key)!r}; expected 0")
        if _record_outcome_int(record, key) != 0:
            raise VerificationFailure(
                f"RECORD-{key.upper()}", f"record.{key} is {record[key]!r}; expected 0")
    before = _require_record_int(record, "items_before_modifyitems")
    after = _require_record_int(record, "items_after_modifyitems")
    collected = _require_record_int(record, "collected")
    if after != collected or after + deselected != before:
        raise VerificationFailure(
            "COLLECTION-HOOK-NARROWING",
            f"before={before}, after={after}, deselected={deselected}, collected={collected}")


def _require_node_identity(canary: dict, summary: dict, record: dict,
                           canary_summary: dict) -> None:
    canary_items = _positive_int(
        canary["canary"], "collected_items", "CANARY-COLLECTED", "canary")
    run_collected = _positive_int(summary["run"], "collected", "RUN-COLLECTED", "run")
    record_collected = _require_record_int(record, "collected")
    if record_collected <= 0:
        raise VerificationFailure("RECORD-COLLECTED", "record collected must be positive")
    if canary_summary["collected"] != canary_items:
        raise VerificationFailure(
            "CANARY-COLLECTED",
            f"canary summary collected {canary_summary['collected']} != {canary_items}")
    if not canary_items == run_collected == record_collected:
        raise VerificationFailure(
            "COLLECTED-COUNT-MISMATCH",
            f"canary/run/record collected counts are {canary_items}/"
            f"{run_collected}/{record_collected}")
    node_ids = canary["canary"]["collected_node_ids"]
    if (not isinstance(node_ids, list)
            or any(not isinstance(node_id, str) or not node_id for node_id in node_ids)
            or node_ids != sorted(node_ids)
            or len(set(node_ids)) != len(node_ids)
            or len(node_ids) != canary_items):
        raise VerificationFailure(
            "CANARY-NODE-IDS",
            "collected_node_ids must be sorted, unique, non-empty strings "
            "whose length equals collected_items")
    terminal = record["terminal_node_ids"]
    if (not isinstance(terminal, list)
            or any(not isinstance(node_id, str) or not node_id for node_id in terminal)
            or len(set(terminal)) != len(terminal)
            or len(terminal) != record_collected):
        raise VerificationFailure(
            "TERMINAL-SET",
            "terminal_node_ids must be unique, non-empty strings whose length "
            "equals record collected")
    missing = sorted(set(node_ids) - set(terminal))
    extra = sorted(set(terminal) - set(node_ids))
    if missing or extra:
        raise VerificationFailure(
            "NODE-ID-SET-MISMATCH",
            f"canary/run node-ID sets differ: missing={missing} extra={extra}")


def _require_outcomes(summary: dict, record: dict, run_summary: dict,
                      record_summary: dict) -> None:
    run_counts = {key: _outcome_int(summary["run"], key, "run") for key in _OUTCOME_KEYS}
    record_counts = {key: _record_outcome_int(record, key) for key in _OUTCOME_KEYS}
    for key in _OUTCOME_KEYS:
        if run_summary[key] != run_counts[key]:
            raise VerificationFailure(
                "RUN-SUMMARY-OUTCOME-MISMATCH",
                f"run summary {key}={run_summary[key]} != run field {run_counts[key]}")
        if record_summary[key] != record_counts[key]:
            raise VerificationFailure(
                "RECORD-SUMMARY-OUTCOME-MISMATCH",
                f"record summary {key}={record_summary[key]} != record field {record_counts[key]}")
        if run_counts[key] != record_counts[key]:
            raise VerificationFailure(
                "OUTCOME-MISMATCH",
                f"run/record {key} differ: {run_counts[key]} != {record_counts[key]}")
    collection_skips = _require_record_int(record, "skipped_at_collection")
    if not 0 <= collection_skips <= record_counts["skipped"]:
        raise VerificationFailure(
            "BAD-FIELD-SKIPPED_AT_COLLECTION",
            f"skipped_at_collection={collection_skips}, skipped={record_counts['skipped']}")
    total = sum(record_counts[key] for key in _OUTCOME_KEYS
                if key != "deselected") - collection_skips
    if total != record["collected"]:
        raise VerificationFailure(
            "ACCOUNTING",
            f"outcomes minus skipped_at_collection={total}, collected={record['collected']}")
    sites = _require_skip_sites(record)
    if sum(site["count"] for site in sites) != record_counts["skipped"]:
        raise VerificationFailure(
            "SKIP-SITES-INCOMPLETE",
            f"skip_sites total {sum(site['count'] for site in sites)} != "
            f"skipped {record_counts['skipped']}")


def _skip_advisory(record_path: str) -> None:
    """Compare structurally valid skip sites with the full allowlist; never fail."""
    try:
        record = json.loads(Path(record_path).read_text(encoding="utf-8"))
        problems = skip_site_problems(
            record["skip_sites"], load_skip_allowlist(SKIP_ALLOWLIST))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            RunFailure) as exc:
        print(f"SKIP-ADVISORY: skip sites not compared ({exc})", file=sys.stderr)
        return
    for condition, detail in problems:
        print(f"SKIP-ADVISORY {condition}: {detail}. The required full-suite "
              "gate will refuse this; add the allowlist entry in the commit "
              "that adds the skip.", file=sys.stderr)
    if not problems:
        print("SKIP-ADVISORY-OK every skip site is in the allowlist")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify fast-suite evidence.")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--canary", required=True)
    parser.add_argument("--run-record", required=True,
                        help="schema-v3 in-band record from this fast run")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--base-sha", default=None,
                        help="independent PR base SHA; falls back to BASE_SHA")
    args = parser.parse_args(argv)
    independent_base = (args.base_sha or os.environ.get("BASE_SHA") or "").strip()
    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parent.parent)
    plugin_root = repo_root / "pact-plugin"
    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / "scripts" / "fast_suite_exclusions.json")
    try:
        canary = _load_artifact(Path(args.canary), "canary")
        summary = _load_artifact(Path(args.summary), "run")
        record = _load_record(Path(args.run_record), "RECORD-ABSENT")
        canary_summary, run_summary, record_summary = _require_artifact_shape(
            canary, summary, record)
        if not independent_base:
            raise VerificationFailure(
                "BASE-SHA-MISSING", "no independent base SHA was supplied")
        for label, artifact in (("canary", canary), ("run", summary)):
            if artifact["base_sha"] != independent_base:
                raise VerificationFailure(
                    "BASE-SHA-MISMATCH",
                    f"{label} base_sha {artifact['base_sha']!r} != {independent_base!r}")
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as exc:
            raise VerificationFailure(
                "MANIFEST-INVALID", f"{manifest_path}: " + "; ".join(exc.errors)) from exc
        recomputed, _, _ = select_for_change(repo_root, manifest, independent_base)
        _require_commands(canary, summary, recomputed, plugin_root)
        _require_identity(canary, summary, record)
        expected = sorted(recomputed.included)
        if not expected:
            raise VerificationFailure("EMPTY-EXPECTED", "the recomputed expected set is empty")
        collected_files = canary["canary"]["collected_files"]
        if not collected_files:
            raise VerificationFailure("EMPTY-COLLECTED", "the canary collected no files")
        _set_difference(expected, canary["canary"]["expected_files"],
                        "EXPECTED-SET-MISMATCH", "canary.expected_files")
        _set_difference(expected, collected_files,
                        "COLLECTED-SET-MISMATCH", "canary.collected_files")
        manifest_sha = _sha256_file(manifest_path)
        for label, artifact in (("canary", canary), ("run", summary)):
            if artifact["manifest"].get("sha256") != manifest_sha:
                raise VerificationFailure(
                    "MANIFEST-DRIFT", f"{label} manifest hash differs from the current manifest")
        _require_process_and_workers(canary, summary, record)
        _require_node_identity(canary, summary, record, canary_summary)
        _require_outcomes(summary, record, run_summary, record_summary)
        identity = len(recomputed.discovered) - len(recomputed.excluded)
        if len(recomputed.included) != identity:
            raise VerificationFailure(
                "SELECTION-ARITHMETIC",
                f"included {len(recomputed.included)} != discovered "
                f"{len(recomputed.discovered)} - excluded {len(recomputed.excluded)}")
    except (VerificationFailure, RunFailure) as failure:
        print(f"VERIFY-FAILURE {failure.condition}: {failure.detail}", file=sys.stderr)
        return 1
    _skip_advisory(args.run_record)
    print(f"VERIFY-OK expected={len(expected)} collected={len(collected_files)} "
          f"status={recomputed.status} select_all={recomputed.select_all_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
