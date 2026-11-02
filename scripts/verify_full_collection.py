"""Prove the required full-suite check still collects the WHOLE corpus.

WHAT THIS EXISTS TO STOP
  A merge gate that collects less than the corpus and reports a five-digit
  total that reads as complete. Nothing in a pytest log distinguishes a full
  run from one that silently dropped a directory; the only tell is the
  number, and no reader has a reference value for it. This guard compares the
  collected FILE SET against an independent filesystem discovery, so a
  narrowing has to remove a file from disk before it can pass.

INDEPENDENCE (load-bearing)
  This file must not import, read, or name any fast-lane file. If the fast
  workflow, the manifest and the selector were deleted, this guard and the
  full-workflow contract must stay green. A guard that shares machinery with
  the thing it certifies inherits its bugs.

INVOCATION
  python scripts/verify_full_collection.py      (from the repository root)

  The workflow invokes it exactly that way. Its `run:` text must never
  contain the token `pytest`: test_ci_collection_scope.py permits exactly one
  pytest invocation in tests.yml and parses that token.

EXIT CODES
  0 green; 2 scope mismatch or parse failure; 3 refusal (no checkout, empty
  corpus, unreadable tree).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIRNAME = "pact-plugin"
CHECKOUT_MARKER = Path(".claude-plugin") / "marketplace.json"
NON_VACUITY_FILE = "tests/test_ci_collection_scope.py"
_CONFIG_NAMES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini",
                 "setup.cfg")
_NARROWING_TOKENS = ("--ignore", "--deselect", "-k", "-m")


def _is_narrowing(token: str) -> bool:
    """Exact-token match, never a substring.

    A substring test reads the `-m` inside `--strict-markers` (or any future
    `--m...` flag) as a marker expression and reports an innocent config as
    narrowing. The flag must be the whole token, or the whole token before an
    equals sign.
    """
    return any(token == flag or token.startswith(flag + "=")
               for flag in _NARROWING_TOKENS)
_SUMMARY_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")


def _refuse(message: str) -> int:
    print(f"REFUSAL: {message}", file=sys.stderr)
    return 3


def _failure(category: str, message: str) -> int:
    print(f"{category}: {message}", file=sys.stderr)
    return 2


def _repo_root() -> Path | None:
    """Walk up for a POSITIVE marker; absence is distinct from a wrong file."""
    for base in Path(__file__).resolve().parents:
        if (base / CHECKOUT_MARKER).is_file():
            return base
    return None


def _discover(plugin_root: Path, repo_root: Path) -> frozenset[str]:
    """Every plugin-relative test_*.py, excluding .worktrees RELATIVELY."""
    found: set[str] = set()
    for path in plugin_root.rglob("test_*.py"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root)
        if ".worktrees" in relative.parts:
            continue
        found.add(path.relative_to(plugin_root).as_posix())
    return frozenset(found)


def _summary_line(lines: list[str]) -> str | None:
    for raw in reversed(lines):
        line = raw.strip().strip("=").strip()
        if _SUMMARY_RE.search(line) and "collected" in line:
            return line
    return None


def _node_id_file(line: str) -> str | None:
    candidate = line.strip()
    if "::" not in candidate or not candidate.split("::", 1)[0].endswith(".py"):
        return None
    return candidate.split("::", 1)[0]


def _config_problems(repo_root: Path, plugin_root: Path) -> list[str]:
    problems: list[str] = []
    for directory in (repo_root, plugin_root):
        for name in _CONFIG_NAMES:
            path = directory / name
            if not path.is_file():
                continue
            for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace")
                    .splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if re.match(r"testpaths\s*[=:]", stripped):
                    problems.append(
                        f"{path}:{number}: a testpaths entry re-narrows "
                        f"collection; the full gate must stay unrestricted")
                if "addopts" in stripped and any(
                        _is_narrowing(token)
                        for token in stripped.partition("=")[2].split()):
                    problems.append(
                        f"{path}:{number}: addopts carries a narrowing flag "
                        f"({stripped!r}); the full gate must stay unrestricted")
    return problems


def _write_record(path: Path, payload: dict) -> None:
    """Atomic write: a truncated record must never look like a real one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove the full suite still collects the whole corpus.")
    parser.add_argument(
        "--record", default=None,
        help="write a collection record here (default: "
             "${GITHUB_WORKSPACE}/.full-collection-record.json when "
             "GITHUB_WORKSPACE is set; no record is written otherwise, so a "
             "local run leaves the tree clean)",
    )
    args = parser.parse_args(argv)
    repo_root = _repo_root()
    if repo_root is None:
        return _refuse(
            f"no {CHECKOUT_MARKER} above {Path(__file__).resolve()}, so this "
            f"is not a source checkout. The full-collection guard cannot run.")

    plugin_root = repo_root / PLUGIN_DIRNAME
    if not plugin_root.is_dir():
        return _refuse(f"{plugin_root} is not a directory")

    if os.environ.get("PYTEST_ADDOPTS"):
        return _failure(
            "COLLECTION FAILURE",
            "PYTEST_ADDOPTS is set in this process; inherited options can "
            "narrow or reshape the collection and are not allowed here",
        )

    discovered = _discover(plugin_root, repo_root)
    if not discovered:
        return _refuse(
            f"discovered no test_*.py under {plugin_root}; an empty corpus "
            f"must never be a vacuous pass")

    config_problems = _config_problems(repo_root, plugin_root)
    if config_problems:
        return _failure("PARSE FAILURE", "; ".join(config_problems))

    argv = [sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider"]
    try:
        completed = subprocess.run(
            argv, cwd=str(plugin_root), capture_output=True, text=True,
            timeout=1800, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _refuse(f"the collection command could not run ({exc})")

    lines = completed.stdout.splitlines()
    summary = _summary_line(lines)
    if summary is None:
        return _failure(
            "PARSE FAILURE",
            "no collection summary line was found, so the guard cannot tell "
            "a complete run from a suite that never started",
        )
    if "deselected" in summary or re.search(r"\berrors?\b", summary):
        return _failure(
            "COLLECTION FAILURE",
            f"the collection summary reports deselection or errors: "
            f"{summary!r}",
        )
    if completed.returncode != 0:
        return _failure(
            "COLLECTION FAILURE",
            f"the collection command exited {completed.returncode}",
        )

    collected: set[str] = set()
    unresolvable: list[str] = []
    plugin_resolved = plugin_root.resolve()
    for line in lines:
        file_part = _node_id_file(line)
        if file_part is None:
            continue
        repo_candidate = (repo_root / file_part).resolve()
        plugin_candidate = (plugin_root / file_part).resolve()
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
    if unresolvable:
        return _failure(
            "PARSE FAILURE",
            f"unresolvable node-ID base for {sorted(set(unresolvable))[:5]}",
        )

    missing = sorted(discovered - collected)
    extra = sorted(collected - discovered)
    if missing or extra:
        return _failure(
            "SCOPE MISMATCH",
            f"collected set differs from the filesystem. "
            f"missing={missing} extra={extra}",
        )

    if NON_VACUITY_FILE not in discovered or NON_VACUITY_FILE not in collected:
        return _failure(
            "COLLECTION FAILURE",
            f"non-vacuity anchor {NON_VACUITY_FILE!r} is absent from "
            f"{'discovery' if NON_VACUITY_FILE not in discovered else 'collection'}",
        )

    count_match = _COLLECTED_RE.search(summary)
    if count_match is None:
        return _failure(
            "PARSE FAILURE",
            f"the collection summary carries no item count: {summary!r}. The "
            f"run-record comparison needs it to catch a run-only narrowing.",
        )
    collected_items = int(count_match.group(1))

    # A record so the certifying RUN can be compared against this independent
    # collection. Only written inside a CI workspace (or when asked): a local
    # run must not leave an artifact in the tree.
    if args.record:
        record_path: Path | None = Path(args.record)
    elif os.environ.get("GITHUB_WORKSPACE"):
        record_path = (Path(os.environ["GITHUB_WORKSPACE"])
                       / ".full-collection-record.json")
    else:
        record_path = None
    if record_path is not None:
        _write_record(record_path, {
            "schema_version": 1,
            "collected_items": collected_items,
            "collected_files": len(collected),
            "discovered_files": len(discovered),
            "subject": os.environ.get("GITHUB_SHA") or "local",
        })

    subject = os.environ.get("GITHUB_SHA") or "local"
    print(f"FULL-COLLECTION-OK {len(collected)} test files and "
          f"{collected_items} items match discovery; subject={subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())