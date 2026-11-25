#!/usr/bin/env python3
"""Validate Track B task fixtures, hidden tests, and gold patches.

A task is valid only when:
  - the untouched fixture's seed tests pass;
  - hidden tests are non-vacuous and fail on the untouched fixture;
  - the gold patch applies, makes hidden tests pass, and keeps seed tests green.
"""
from __future__ import annotations

import argparse
import json
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


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    import os

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)


def _prepare_git(work: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "pact-bench",
        "GIT_AUTHOR_EMAIL": "pact-bench@example.invalid",
        "GIT_COMMITTER_NAME": "pact-bench",
        "GIT_COMMITTER_EMAIL": "pact-bench@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    import os
    child = os.environ.copy()
    child.update(env)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(work), env=child, check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(work), env=child, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=str(work), env=child, check=True)


def validate_task(task_dir: Path, python: str) -> dict[str, Any]:
    task_dir = task_dir.resolve()
    meta_path = task_dir / "task.json"
    if not meta_path.is_file():
        raise SystemExit(f"missing task.json: {task_dir}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED_FIELDS if field not in meta]
    if missing:
        raise SystemExit(f"{task_dir.name}: missing task.json fields: {missing}")
    if meta.get("schema") != "pact-bench/task/1":
        raise SystemExit(f"{task_dir.name}: unsupported schema {meta.get('schema')!r}")
    fixture = task_dir / meta["fixture"]
    hidden = task_dir / meta["hidden_tests"]
    gold = task_dir / meta["gold_patch"]
    prompt = task_dir / meta["prompt"]
    rubric = task_dir / meta["rubric"]
    for path, label in ((fixture, "fixture"), (hidden, "hidden_tests"), (gold, "gold_patch"), (prompt, "prompt"), (rubric, "rubric")):
        if not path.exists():
            raise SystemExit(f"{task_dir.name}: missing {label}: {path}")

    with tempfile.TemporaryDirectory(prefix=f"pact-task-{meta['id']}-") as tmp:
        work = Path(tmp) / "work"
        shutil.copytree(fixture, work)
        _prepare_git(work)

        seed = _run([python, "-m", "pytest", "-q"], work)
        if seed.returncode != 0:
            raise SystemExit(f"{meta['id']}: seed tests failed:\n{seed.stdout[-4000:]}\n{seed.stderr[-2000:]}")

        hidden_copy = work / "hidden_tests"
        shutil.copytree(hidden, hidden_copy)
        before = _run([python, "-m", "pytest", "-q", "hidden_tests"], work)
        if before.returncode == 0:
            raise SystemExit(f"{meta['id']}: hidden tests passed on the untouched fixture (vacuous)")

        applied = _run(["git", "apply", "--whitespace=nowarn", str(gold.resolve())], work)
        if applied.returncode != 0:
            raise SystemExit(f"{meta['id']}: gold patch did not apply:\n{applied.stderr[-4000:]}")
        changed = _run(["git", "diff", "--quiet"], work)
        if changed.returncode == 0:
            raise SystemExit(f"{meta['id']}: gold patch applied but changed nothing")

        after = _run([python, "-m", "pytest", "-q", "hidden_tests"], work)
        if after.returncode != 0:
            raise SystemExit(f"{meta['id']}: hidden tests failed after gold patch:\n{after.stdout[-4000:]}\n{after.stderr[-2000:]}")

        seed_after = _run([python, "-m", "pytest", "-q"], work)
        if seed_after.returncode != 0:
            raise SystemExit(f"{meta['id']}: seed tests failed after gold patch:\n{seed_after.stdout[-4000:]}")

    return {
        "id": meta["id"],
        "path": str(task_dir),
        "class": meta["class"],
        "variety": meta["variety"],
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
    parser = argparse.ArgumentParser(description="Validate Track B tasks.")
    parser.add_argument("--root", default=None, help="task suite root (default bench/tasks)")
    parser.add_argument("--task", action="append", default=None, help="specific task directory")
    parser.add_argument("--all", action="store_true", help="validate every task")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    targets = [Path(item).resolve() for item in args.task] if args.task else discover(root)
    if not targets:
        raise SystemExit("no tasks found")
    results = [validate_task(target, args.python) for target in targets]
    print(json.dumps({"schema": "pact-bench/task-validation/1", "tasks": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
