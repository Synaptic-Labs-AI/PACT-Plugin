"""Thin CLI over the fast-suite selection core.

WHAT THIS IS FOR
  Local and test use: inspect what the fast lane would run, and validate the
  manifest, without starting pytest. The CI workflow does NOT invoke this --
  it runs `run_fast_suite.py`, which owns the artifacts.

EXIT CODES
  0  success
  2  manifest invalid (every violation printed, one per line)
  3  input or git error
  4  unexpected error

EMISSION CONTRACT
  `--emit ignore-args` prints ABSOLUTE `--ignore=` arguments, one per line.
  It never prints a plugin-relative path: measured, a rootdir-relative
  `--ignore` is a silent no-op, so a caller that pasted this output could run
  a file it believed it had excluded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fast_suite_selection import (  # noqa: E402  (path must precede import)
    ManifestError,
    changed_paths,
    discover,
    gather_facts,
    load_manifest,
    select,
    to_pytest_args,
)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit the fast PR suite's pytest selection.",
        epilog=(
            "examples:\n"
            '  python3 "scripts/fast_suite_args.py" --check\n'
            '  python3 "scripts/fast_suite_args.py" --base-sha <sha> '
            "--emit ignore-args\n"
            '  printf "<repo-relative-path>\\n" | python3 '
            '"scripts/fast_suite_args.py" --changed-file -\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest",
        default="scripts/fast_suite_exclusions.json",
        help="manifest path, repo-relative (default: %(default)s)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repository root (default: the parent of this script's directory)",
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        help="plugin root (default: <repo-root>/pact-plugin)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--base-sha", default=None,
                        help="base commit for the changed-path diff")
    source.add_argument(
        "--changed-file",
        default=None,
        help="'-' reads newline-separated repo-relative changed paths from stdin",
    )
    parser.add_argument(
        "--emit",
        choices=("ignore-args", "selected-files", "json"),
        default="ignore-args",
        help="output form (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the manifest and exit; no base SHA needed",
    )
    return parser


def _read_changed_file(source: str) -> tuple[list[str], str]:
    if source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    paths = [line.strip() for line in text.splitlines() if line.strip()]
    return (paths, "ok") if paths else ([], "empty-diff")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else _default_repo_root())
    plugin_root = (Path(args.plugin_root).resolve() if args.plugin_root
                   else repo_root / "pact-plugin")
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path

    try:
        manifest = load_manifest(manifest_path, plugin_root=plugin_root)
    except ManifestError as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        return 2

    if args.check:
        print(f"MANIFEST-OK {manifest_path}", file=sys.stderr)
        return 0

    if args.changed_file is not None:
        try:
            changed, status = _read_changed_file(args.changed_file)
        except OSError as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return 3
    elif args.base_sha is not None:
        changed, status = changed_paths(args.base_sha, repo_root)
        if status == "git-error":
            print(
                f"git error: could not diff against {args.base_sha!r} in "
                f"{repo_root}",
                file=sys.stderr,
            )
            return 3
        if status == "invalid-base":
            # A caller must be able to tell "your base was unusable" from
            # "no exclusions apply". Both used to exit 0 with SELECT_ALL.
            print(
                f"input error: base SHA {args.base_sha!r} is not a usable "
                f"commit id (expected 7-40 hex characters, not all zeros)",
                file=sys.stderr,
            )
            return 3
    else:
        print(
            "input error: one of --base-sha, --changed-file or --check is "
            "required",
            file=sys.stderr,
        )
        return 3

    try:
        facts = gather_facts(repo_root, manifest, changed,
                             args.base_sha if status == "ok" else None)
        selection = select(discover(repo_root), manifest, changed, status,
                           facts)
        ignore_args = to_pytest_args(selection, plugin_root)
    except ValueError as exc:
        print(f"selection error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - exit contract, not control flow
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 4

    if args.emit == "ignore-args":
        for argument in ignore_args:
            print(argument)
    elif args.emit == "selected-files":
        for path in sorted(selection.included):
            print(path)
    else:
        print(json.dumps({
            "status": selection.status,
            "select_all_reason": selection.select_all_reason,
            "discovered": len(selection.discovered),
            "excluded": len(selection.excluded),
            "added_back": [
                {
                    "path": entry.path,
                    "reason": entry.reason,
                    "matched_changed": entry.matched_changed,
                    "matched_seam": entry.matched_seam,
                    "evidence": entry.evidence,
                }
                for entry in selection.added_back
            ],
            "included_files": sorted(selection.included),
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())