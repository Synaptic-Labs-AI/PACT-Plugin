"""Verify the fast-suite artifacts. Fails; never skips; never runs pytest.

WHY IT RECOMPUTES INSTEAD OF TRUSTING
  The two artifacts are the runner's CLAIM. If this verifier read the expected
  set out of the artifact it is checking, the comparison would be the claim
  against itself. So it recomputes the expected world from first principles --
  manifest, discovery, changed paths, select() -- and compares the artifacts
  to that.

WHAT IT REFUSES TO DO
  Read a count before proving a summary line exists. An absent summary and a
  zero count produce the same digits; only one of them means the suite ran.
  The presence check therefore runs first, and its failure is named
  NO-SUMMARY rather than folded into a count check.

NO CONSTANT TOTALS. The corpus is checkout-dependent (a clean tree and an
authoring tree differ by one parametrized node), so every comparison here is
a SET comparison. Counts appear only in arithmetic identities computed from
this run's own numbers.
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
    changed_paths,
    discover,
    load_manifest,
    parse_pytest_summary,
    select,
)

SCHEMA_VERSION = 1

_SELECTION_KEYS = (
    "status", "select_all_reason", "discovered", "excluded", "added_back",
    "included", "included_files", "included_sha256",
)
_CANARY_KEYS = (
    "command", "ignore_paths", "rc", "summary_line", "expected_files",
    "collected_files", "missing", "extra",
)
_RUN_KEYS = (
    "command", "rc", "summary_line", "collected", "passed", "failed",
    "errors", "skipped", "deselected",
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
            f"'nothing to check'.",
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


def _require_summary_line(artifact: dict, label: str) -> str:
    """Condition 6. Runs BEFORE any count is read."""
    line = artifact.get("summary_line")
    if not isinstance(line, str) or not line.strip():
        raise VerificationFailure(
            f"NO-SUMMARY-{label.upper()}",
            f"{label}: summary_line is absent, null or empty. This is the "
            f"'the suite never started' case; a zero count cannot "
            f"distinguish it from a clean run.",
        )
    parsed = parse_pytest_summary(line)
    if parsed is None:
        raise VerificationFailure(
            f"NO-SUMMARY-{label.upper()}",
            f"{label}: summary_line {line!r} does not parse under the "
            f"decoration this invocation produces (bare under -q, "
            f"'='-decorated without it).",
        )
    return line


def _set_difference(expected: list[str], actual: list[str],
                    condition: str, label: str) -> None:
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing or extra:
        raise VerificationFailure(
            condition,
            f"{label}: file-set mismatch. missing={missing} extra={extra}",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the fast-suite canary and run artifacts.")
    parser.add_argument("--summary", default=None,
                        help="run artifact path (default: "
                             "${GITHUB_WORKSPACE}/.fast-suite-summary.json)")
    parser.add_argument("--canary", default=None,
                        help="canary artifact path (default: sibling "
                             ".fast-suite-canary.json)")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args(argv)

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parent.parent)
    # Same default rule as the runner: ${GITHUB_WORKSPACE} when set, else the
    # repository root. The two must agree or the verifier reads a path the
    # runner never wrote and reports a missing artifact for the wrong reason.
    summary_path = Path(args.summary) if args.summary else (
        Path(os.environ.get("GITHUB_WORKSPACE")
             or Path(__file__).resolve().parent.parent)
        / ".fast-suite-summary.json")
    canary_path = (Path(args.canary) if args.canary
                   else summary_path.with_name(".fast-suite-canary.json"))
    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / "scripts" / "fast_suite_exclusions.json")

    try:
        # 1, 2, 3: presence and parseability.
        canary = _load_artifact(canary_path, "canary")
        summary = _load_artifact(summary_path, "run")

        # 4: schema version.
        _require_schema_version(canary, "canary")
        _require_schema_version(summary, "run")

        # 5: required keys.
        _require_keys(canary, ("manifest", "base_sha", "selection", "canary"),
                      "the canary artifact", "canary")
        _require_keys(summary, ("manifest", "base_sha", "selection", "run"),
                      "the run artifact", "run")
        _require_keys(canary["selection"], _SELECTION_KEYS,
                      "canary.selection", "canary")
        _require_keys(summary["selection"], _SELECTION_KEYS,
                      "run.selection", "run")
        _require_keys(canary["canary"], _CANARY_KEYS,
                      "canary.canary", "canary")
        _require_keys(summary["run"], _RUN_KEYS, "run.run", "run")

        # 6: summary presence and parseability, BEFORE any count.
        _require_summary_line(canary["canary"], "canary")
        _require_summary_line(summary["run"], "run")

        # 7: the canary process must have succeeded.
        if canary["canary"]["rc"] != 0:
            raise VerificationFailure(
                "CANARY-RC", f"canary rc is {canary['canary']['rc']}")

        # Independent recomputation. Never read from either artifact.
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as exc:
            raise VerificationFailure(
                "MANIFEST-INVALID",
                f"{manifest_path}: " + "; ".join(exc.errors),
            ) from exc
        base_sha = summary.get("base_sha")
        changed, status = changed_paths(base_sha, repo_root)
        recomputed = select(discover(repo_root), manifest, changed, status)
        expected = sorted(recomputed.included)

        # 10: both sets non-empty.
        if not expected:
            raise VerificationFailure(
                "EMPTY-EXPECTED", "the recomputed expected set is empty")
        collected_files = canary["canary"]["collected_files"]
        if not collected_files:
            raise VerificationFailure(
                "EMPTY-COLLECTED", "the canary collected no files")

        # 8, 9: file-set equality, both directions, named separately.
        _set_difference(expected, canary["canary"]["expected_files"],
                        "EXPECTED-SET-MISMATCH", "canary.expected_files")
        _set_difference(expected, collected_files,
                        "COLLECTED-SET-MISMATCH", "canary.collected_files")

        # 11: the manifest the run used is the manifest now.
        manifest_sha = _sha256_file(manifest_path)
        for label, artifact in (("canary", canary), ("run", summary)):
            recorded = artifact["manifest"].get("sha256")
            if recorded != manifest_sha:
                raise VerificationFailure(
                    "MANIFEST-DRIFT",
                    f"{label}: recorded manifest sha {recorded!r} != current "
                    f"{manifest_sha!r}",
                )

        # 12: the suite process must have succeeded.
        if summary["run"]["rc"] != 0:
            raise VerificationFailure(
                "RUN-RC", f"run rc is {summary['run']['rc']}")

        # 13-16: counts. Only reachable after the summary was proven present.
        if summary["run"]["collected"] is None or (
                int(summary["run"]["collected"]) <= 0):
            raise VerificationFailure(
                "RUN-COLLECTED",
                f"run collected {summary['run']['collected']!r}; expected > 0",
            )
        for key in ("failed", "errors", "deselected"):
            value = summary["run"].get(key)
            if value != 0:
                raise VerificationFailure(
                    f"RUN-{key.upper()}",
                    f"run.{key} is {value!r}; expected 0",
                )

        # 17: secondary arithmetic identity, from the recomputation.
        identity = (len(recomputed.discovered) - len(recomputed.excluded)
                    + len(recomputed.added_back))
        if len(recomputed.included) != identity:
            raise VerificationFailure(
                "SELECTION-ARITHMETIC",
                f"set equality is primary; this redundancy check failed: "
                f"included {len(recomputed.included)} != discovered "
                f"{len(recomputed.discovered)} - excluded "
                f"{len(recomputed.excluded)} + added_back "
                f"{len(recomputed.added_back)}",
            )
    except VerificationFailure as failure:
        print(f"VERIFY-FAILURE {failure.condition}: {failure.detail}",
              file=sys.stderr)
        return 1

    print(f"VERIFY-OK expected={len(expected)} "
          f"collected={len(collected_files)} "
          f"status={recomputed.status} "
          f"select_all={recomputed.select_all_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())