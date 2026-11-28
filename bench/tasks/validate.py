#!/usr/bin/env python3
"""Validate Track B task fixtures, hidden tests, and gold patches.

A task is valid only when:
  - the untouched fixture's seed tests pass;
  - hidden tests are non-vacuous and fail on the untouched fixture;
  - the gold patch leaves hidden_tests/ alone, applies, changes the tree, makes
    hidden tests pass, and keeps seed tests green.

Every task is validated and reported; the exit code is 1 if any task failed.
Git runs hermetically (no global/system config, no hooks), so host settings
cannot change the result.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "schema",
    "id",
    "title",
    "class",
    "variety",
    "revision",
    "timeout_seconds",
    "max_turns",
    "prompt",
    "fixture",
    "hidden_tests",
    "gold_patch",
    "rubric",
)
HIDDEN_DIR = "hidden_tests"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "pact-bench",
    "GIT_AUTHOR_EMAIL": "pact-bench@example.invalid",
    "GIT_COMMITTER_NAME": "pact-bench",
    "GIT_COMMITTER_EMAIL": "pact-bench@example.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
}
GIT = ["git", "-c", "core.hooksPath=/dev/null"]


class TaskInvalid(SystemExit):
    """A task failed validation; main() reports it and moves to the next task."""


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(GIT_ENV)
    return env


def _run(cmd: list[str], cwd: Path, task_id: str, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), env=_child_env(), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise TaskInvalid(f"{task_id}: {' '.join(cmd)} timed out after {timeout}s") from exc


def _git(args: list[str], work: Path, task_id: str, timeout: int) -> subprocess.CompletedProcess[str]:
    proc = _run([*GIT, *args], work, task_id, timeout)
    if proc.returncode != 0:
        raise TaskInvalid(f"{task_id}: git {' '.join(args)} failed:\n{proc.stderr[-4000:]}")
    return proc


def _touches_hidden(numstat: str) -> bool:
    for line in numstat.splitlines():
        path = line.split("\t", 2)[-1]
        # Rename lines read "old => new" or "dir/{old => new}/file".
        for part in re.split(r" => |[{}]", path):
            if part == HIDDEN_DIR or part.startswith(HIDDEN_DIR + "/"):
                return True
    return False


def _load_meta(task_dir: Path) -> dict[str, Any]:
    meta_path = task_dir / "task.json"
    if not meta_path.is_file():
        raise TaskInvalid(f"{task_dir.name}: missing task.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskInvalid(f"{task_dir.name}: task.json is not valid JSON: {exc}") from exc
    missing = [field for field in REQUIRED_FIELDS if field not in meta]
    if missing:
        raise TaskInvalid(f"{task_dir.name}: missing task.json fields: {missing}")
    if meta.get("schema") != "pact-bench/task/1":
        raise TaskInvalid(f"{task_dir.name}: unsupported schema {meta.get('schema')!r}")
    return meta


def validate_task(task_dir: Path, python: str) -> dict[str, Any]:
    task_dir = task_dir.resolve()
    meta = _load_meta(task_dir)
    task_id = str(meta["id"])
    timeout = int(meta["timeout_seconds"])
    fixture = task_dir / meta["fixture"]
    hidden = task_dir / meta["hidden_tests"]
    gold = task_dir / meta["gold_patch"]
    prompt = task_dir / meta["prompt"]
    rubric = task_dir / meta["rubric"]
    for path, label in ((fixture, "fixture"), (hidden, "hidden_tests"), (gold, "gold_patch"), (prompt, "prompt"), (rubric, "rubric")):
        if not path.exists():
            raise TaskInvalid(f"{task_id}: missing {label}: {path}")
    pytest_cmd = [python, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider"]

    def pytest(*extra: str) -> subprocess.CompletedProcess[str]:
        return _run([*pytest_cmd, *extra], work, task_id, timeout)

    with tempfile.TemporaryDirectory(prefix=f"pact-task-{task_id}-") as tmp:
        work = Path(tmp) / "work"
        shutil.copytree(fixture, work)
        _git(["init", "-q", "-b", "main"], work, task_id, timeout)
        _git(["add", "-A"], work, task_id, timeout)
        _git(["commit", "-q", "--no-gpg-sign", "-m", "seed"], work, task_id, timeout)

        seed = pytest()
        if seed.returncode != 0:
            raise TaskInvalid(f"{task_id}: seed tests failed:\n{seed.stdout[-4000:]}\n{seed.stderr[-2000:]}")

        shutil.copytree(hidden, work / HIDDEN_DIR)
        before = pytest(HIDDEN_DIR)
        if before.returncode == 0:
            raise TaskInvalid(f"{task_id}: hidden tests passed on the untouched fixture (vacuous)")

        # An unparseable patch yields no numstat lines; the apply below rejects it.
        stat = _run([*GIT, "apply", "--numstat", str(gold.resolve())], work, task_id, timeout)
        if _touches_hidden(stat.stdout):
            raise TaskInvalid(f"{task_id}: gold patch modifies {HIDDEN_DIR}/")
        applied = _run([*GIT, "apply", "--whitespace=nowarn", str(gold.resolve())], work, task_id, timeout)
        if applied.returncode != 0:
            raise TaskInvalid(f"{task_id}: gold patch did not apply:\n{applied.stderr[-4000:]}")
        # status (unlike diff) also sees files the patch adds.
        changed = _git(
            ["status", "--porcelain", "--untracked-files=all", "--", ".", f":(exclude){HIDDEN_DIR}"],
            work, task_id, timeout,
        )
        if not changed.stdout.strip():
            raise TaskInvalid(f"{task_id}: gold patch applied but changed nothing")

        after = pytest(HIDDEN_DIR)
        if after.returncode != 0:
            raise TaskInvalid(f"{task_id}: hidden tests failed after gold patch:\n{after.stdout[-4000:]}\n{after.stderr[-2000:]}")

        seed_after = pytest(f"--ignore={HIDDEN_DIR}")
        if seed_after.returncode != 0:
            raise TaskInvalid(f"{task_id}: seed tests failed after gold patch:\n{seed_after.stdout[-4000:]}")

    return {
        "id": meta["id"],
        "path": str(task_dir),
        "class": meta["class"],
        "variety": meta["variety"],
        "status": "pass",
        "seed": "pass",
        "hidden_before": "fail",
        "hidden_after": "pass",
        "seed_after": "pass",
    }


def discover(root: Path) -> list[Path]:
    return sorted(
        path.parent
        for path in root.glob("*/task.json")
        if path.is_file() and path.parent.name not in {"tasks", "fixtures"}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Track B tasks (every task under --root by default).")
    parser.add_argument("--root", default=None, help="task suite root (default bench/tasks)")
    parser.add_argument("--task", action="append", default=None, help="specific task directory (repeatable)")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    targets = [Path(item).resolve() for item in args.task] if args.task else discover(root)
    if not targets:
        raise SystemExit("no tasks found")
    results: list[dict[str, Any]] = []
    for target in targets:
        try:
            results.append(validate_task(target, args.python))
        except TaskInvalid as exc:
            results.append({"path": str(target), "status": "fail", "error": str(exc)})
    print(json.dumps({"schema": "pact-bench/task-validation/1", "tasks": results}, indent=2))
    return 0 if all(item["status"] == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
