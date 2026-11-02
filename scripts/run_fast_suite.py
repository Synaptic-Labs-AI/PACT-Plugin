"""The pinned fast-suite runner: the only test-run entry point in the fast lane.

TWO MODES
  --collect-only   the canary. Runs the SAME selection collection-only and
                   compares pytest's collected FILE SET to the pure
                   selector's expected set. It proves the --ignore arguments
                   actually excluded the excluded files; a matrix probe of
                   the selector would prove the TEST, not the lane.
  (default)        the real run. Streams output, parses the summary, writes
                   the summary artifact, and exits with pytest's code.

WHY THIS EXISTS AT ALL
  A wrapper invocation contains `python` but not the token `pytest`, so a
  text-only CI guard that greps for `pytest` would pass with no environment
  verify anywhere near the runner. The pinning lives on this file's PATH:
  test_memory_init.py classifies this invocation as a test step, so the
  verify-bracketing rules bind to it.

ARTIFACTS
  <summary path>          .fast-suite-summary.json   (run mode)
  <summary path> sibling  .fast-suite-canary.json   (collect-only mode)
  The default summary path is ${GITHUB_WORKSPACE}/.fast-suite-summary.json
  when GITHUB_WORKSPACE is set, else <repo-root>/.fast-suite-summary.json.
  Deliberately NOT an `env:` key on the workflow step: the governed-step
  contract forbids `env:` on a test step, and GITHUB_WORKSPACE is inherited.

INVARIANTS
  No PYTEST_ADDOPTS, no env overrides, no shell: a direct subprocess.run.
  Artifacts are written atomically and are written even when pytest fails --
  a missing artifact must mean "never ran", never "failed".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fast_suite_selection import (  # noqa: E402  (path must precede import)
    ManifestError,
    build_argv,
    load_manifest,
    parse_pytest_summary,
    select_for_change,
)

SCHEMA_VERSION = 1
_TAIL_LINES = 400
_ITEMS_LINE_RE = re.compile(r"collected \d+ items?\b")


def _node_id_file(line: str) -> str | None:
    """The file part of a pytest node ID line, or None for any other line."""
    candidate = line.strip()
    if "::" not in candidate or not candidate.split("::", 1)[0].endswith(".py"):
        return None
    return candidate.split("::", 1)[0]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_paths(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _git_head(repo_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    head = completed.stdout.strip()
    return head or None


def _default_summary_path(repo_root: Path) -> Path:
    workspace = os.environ.get("GITHUB_WORKSPACE")
    base = Path(workspace) if workspace else repo_root
    return base / ".fast-suite-summary.json"


def _canary_path(summary_path: Path) -> Path:
    return summary_path.with_name(".fast-suite-canary.json")


def _write_json(path: Path, payload: dict) -> None:
    """Atomic write: a truncated artifact must never look like a real one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _stream(argv: list[str], cwd: Path, on_line=None) -> tuple[int, list[str]]:
    """Run argv, echoing every line, and return (rc, tail lines).

    `on_line` sees EVERY line. The tail is bounded, so anything that must be
    complete -- the collected file set -- has to be classified here rather
    than recovered from the tail afterwards. A tail-only parse silently
    compared the last few hundred node IDs against the whole expected set and
    reported the rest as missing.
    """
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    process = subprocess.Popen(
        argv, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            stripped = line.rstrip("\n")
            tail.append(stripped)
            if on_line is not None:
                on_line(stripped)
    finally:
        process.stdout.close()
    return process.wait(), list(tail)


def _selection_block(selection, manifest_path: Path) -> dict:
    included = sorted(selection.included)
    return {
        "status": selection.status,
        "select_all_reason": selection.select_all_reason,
        "discovered": len(selection.discovered),
        "excluded": len(selection.excluded),
        "added_back": len(selection.added_back),
        "included": len(included),
        "included_files": included,
        "included_sha256": _sha256_paths(included),
    }


def _base_payload(selection, manifest_path: Path, base_sha: str | None,
                  repo_root: Path, mode: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "manifest": {
            "path": str(manifest_path.relative_to(repo_root))
            if manifest_path.is_relative_to(repo_root) else str(manifest_path),
            "sha256": _sha256_file(manifest_path),
        },
        "base_sha": base_sha,
        "head_sha": _git_head(repo_root),
        "selection": _selection_block(selection, manifest_path),
    }


def _collect_only(args, selection, manifest_path: Path, plugin_root: Path,
                  repo_root: Path) -> int:
    argv = build_argv(selection, plugin_root, "collect-only")
    ignore_paths = [argument.split("=", 1)[1] for argument in argv
                    if argument.startswith("--ignore=")]
    missing_ignores = [path for path in ignore_paths
                       if not Path(path).is_file()]
    if missing_ignores:
        print("CANARY FAILURE: --ignore path missing on disk: "
              + ", ".join(missing_ignores), file=sys.stderr)
        return 1

    node_files: list[str] = []

    def note_node_id(line: str) -> None:
        file_part = _node_id_file(line)
        if file_part is not None:
            node_files.append(file_part)

    rc, tail = _stream(argv, plugin_root, note_node_id)
    parsed = parse_pytest_summary("\n".join(tail))

    collected: set[str] = set()
    unresolvable: list[str] = []
    for file_part in node_files:
        repo_candidate = (repo_root / file_part).resolve()
        plugin_candidate = (plugin_root / file_part).resolve()
        plugin_resolved = plugin_root.resolve()
        if repo_candidate.is_file() and repo_candidate.is_relative_to(
                plugin_resolved):
            collected.add(repo_candidate.relative_to(plugin_resolved)
                          .as_posix())
        elif plugin_candidate.is_file() and plugin_candidate.is_relative_to(
                plugin_resolved):
            collected.add(plugin_candidate.relative_to(plugin_resolved)
                          .as_posix())
        else:
            unresolvable.append(file_part)

    expected = set(selection.included)
    missing = sorted(expected - collected)
    extra = sorted(collected - expected)

    payload = _base_payload(selection, manifest_path, args.base_sha,
                            repo_root, "collect-only")
    payload["canary"] = {
        "command": argv,
        "ignore_paths": ignore_paths,
        "rc": rc,
        "summary_line": parsed["summary_line"] if parsed else None,
        # The collected ITEM count. The run must collect exactly this many,
        # which catches a run-only narrowing that is not a deselection.
        "collected_items": int(parsed["collected"]) if parsed else None,
        "expected_files": sorted(expected),
        "collected_files": sorted(collected),
        "missing": missing,
        "extra": extra,
        "unresolvable_node_id_bases": sorted(set(unresolvable)),
    }
    _write_json(_canary_path(Path(args.summary)), payload)

    failures: list[str] = []
    if rc != 0:
        failures.append(f"rc {rc} != 0")
    if parsed is None:
        failures.append("no parseable summary line (the suite may not have started)")
    # DESELECTION AND COLLECTION ERRORS, scanned separately from the file
    # sets. A PARTIAL deselection -- an inherited PYTEST_ADDOPTS=-k, say --
    # leaves the file collected, so the file-set comparison passes and only
    # this token scan sees it. Mirrors verify_full_collection.py.
    summary_line = parsed["summary_line"] if parsed else ""
    if ("deselected" in summary_line
            or re.search(r"\berrors?\b", summary_line)):
        failures.append(
            f"the collection summary reports deselection or errors: "
            f"{summary_line!r}")
    if not expected:
        failures.append("expected set is empty")
    if not collected:
        failures.append("collected set is empty")
    if missing:
        failures.append(f"missing files ({len(missing)}): {missing}")
    if extra:
        failures.append(f"extra files ({len(extra)}): {extra}")
    if unresolvable:
        failures.append(
            f"unresolvable node-ID bases: {sorted(set(unresolvable))}")
    if failures:
        print("CANARY FAILURE: " + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"CANARY-OK expected={len(expected)} collected={len(collected)}")
    return 0


def _run(args, selection, manifest_path: Path, plugin_root: Path,
         repo_root: Path) -> int:
    argv = build_argv(selection, plugin_root, "run")
    # The `collected N items` header sits at the TOP of a run log of tens of
    # thousands of lines, so a tail-only parse never sees it and the collected
    # count silently falls back to the outcome sum. Capture it as it streams.
    collected_header: list[str] = []

    def note_collected(line: str) -> None:
        if _ITEMS_LINE_RE.search(line):
            collected_header[:] = [line.strip()]

    rc, tail = _stream(argv, plugin_root, note_collected)
    parsed = parse_pytest_summary(
        "\n".join([*collected_header, *tail]))

    payload = _base_payload(selection, manifest_path, args.base_sha,
                            repo_root, "run")
    payload["run"] = {
        "command": argv,
        "rc": rc,
        "summary_line": parsed["summary_line"] if parsed else None,
        "collected": int(parsed["collected"]) if parsed else None,
        "passed": int(parsed["passed"]) if parsed else None,
        "failed": int(parsed["failed"]) if parsed else None,
        "errors": int(parsed["errors"]) if parsed else None,
        "skipped": int(parsed["skipped"]) if parsed else None,
        "deselected": int(parsed["deselected"]) if parsed else None,
    }
    _write_json(Path(args.summary), payload)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fast PR suite through the pinned selection.")
    parser.add_argument("--base-sha", required=True,
                        help="PR base commit SHA")
    parser.add_argument("--collect-only", action="store_true",
                        help="canary mode: collect and compare file sets")
    parser.add_argument("--repo-root", default=None,
                        help="repository root (default: parent of scripts/)")
    parser.add_argument("--manifest", default=None,
                        help="manifest path (default: <repo-root>/scripts/"
                             "fast_suite_exclusions.json)")
    parser.add_argument("--summary", default=None,
                        help="summary artifact path (default: "
                             "${GITHUB_WORKSPACE}/.fast-suite-summary.json)")
    args = parser.parse_args(argv)

    # Inherited pytest options can change what runs without appearing in the
    # command, and the canary's collection checks cannot see a run-only
    # option. Refuse rather than run under an unknown configuration.
    if os.environ.get("PYTEST_ADDOPTS"):
        print(
            "FAST-SUITE REFUSAL: PYTEST_ADDOPTS is set in this process "
            f"({os.environ['PYTEST_ADDOPTS']!r}). Inherited options can "
            "narrow or reshape the run without appearing in the recorded "
            "command, so the runner refuses to start.",
            file=sys.stderr,
        )
        return 3

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parent.parent)
    plugin_root = repo_root / "pact-plugin"
    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / "scripts" / "fast_suite_exclusions.json")
    args.summary = str(Path(args.summary) if args.summary
                       else _default_summary_path(repo_root))

    try:
        manifest = load_manifest(manifest_path, plugin_root=plugin_root)
    except ManifestError as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        return 2

    selection, _, _ = select_for_change(repo_root, manifest, args.base_sha)
    seconds = {entry.path: entry.displayed_seconds
               for entry in manifest.exclusions}
    saved = sum(seconds.get(path, 0.0) for path in selection.excluded)
    print(f"FAST-SUITE-SELECTION status={selection.status} "
          f"select_all={selection.select_all_reason} "
          f"included={len(selection.included)} "
          f"excluded={len(selection.excluded)} "
          f"added_back={len(selection.added_back)} "
          f"estimated_saved_seconds={saved:.0f}", file=sys.stderr)
    for entry in selection.added_back:
        print(f"  ADDED-BACK {entry.path} reason={entry.reason} "
              f"changed={entry.matched_changed} "
              f"evidence={entry.evidence or entry.matched_seam}",
              file=sys.stderr)

    if args.collect_only:
        return _collect_only(args, selection, manifest_path, plugin_root,
                             repo_root)
    return _run(args, selection, manifest_path, plugin_root, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())