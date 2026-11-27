#!/usr/bin/env python3
"""Track B PTY runner and scorecard.

Runs each task in a fresh workspace, under either the PACT plugin with the
interactive orchestrator agent or the no-plugin solo condition. Completion is
detected with an absolute sentinel file; hidden tests are run only after the
agent exits. All raw state stays under bench/.runs/track-b/.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from collections import Counter
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pexpect

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))
from external.runner import prepare

TASKS = BENCH / "tasks"
DEFAULT_PROBE = BENCH / ".runs" / "external" / "probe"
DEFAULT_OUT = BENCH / ".runs" / "track-b"
BENCH_PYTHON = BENCH / ".venv" / "bin" / "python"
CLAUDE = Path.home() / ".local" / "bin" / "claude"
MODEL = "deepseek-flash[1m]"


@dataclass(frozen=True)
class Task:
    id: str
    directory: Path
    fixture: Path
    hidden_tests: Path
    prompt: Path
    timeout_seconds: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_task(directory: Path) -> Task:
    meta = json.loads((directory / "task.json").read_text(encoding="utf-8"))
    return Task(
        id=str(meta["id"]),
        directory=directory,
        fixture=directory / str(meta["fixture"]),
        hidden_tests=directory / str(meta["hidden_tests"]),
        prompt=directory / str(meta["prompt"]),
        timeout_seconds=int(meta["timeout_seconds"]),
    )


def discover_tasks(root: Path = TASKS, only: list[str] | None = None) -> list[Task]:
    tasks = [load_task(p.parent) for p in sorted(root.glob("*/task.json"))]
    if only:
        wanted = set(only)
        tasks = [task for task in tasks if task.id in wanted]
    if not tasks:
        raise SystemExit("no tasks selected")
    return tasks


def _git(work: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "pact-bench",
            "GIT_AUTHOR_EMAIL": "pact-bench@example.invalid",
            "GIT_COMMITTER_NAME": "pact-bench",
            "GIT_COMMITTER_EMAIL": "pact-bench@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
    )
    return env


def seed_workspace(fixture: Path, workspace: Path) -> None:
    shutil.copytree(fixture, workspace)
    env = _git_env()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=workspace, env=env, check=True)


def build_prompt(task: Task, sentinel: Path) -> str:
    prompt = " ".join(task.prompt.read_text(encoding="utf-8").split())
    return (
        prompt
        + " When the entire task is complete, all tests have been handled, and all work is committed, "
        + f"write exactly DONE to the absolute path {sentinel} and then stop. "
        + "Do not put this file in the workspace."
    )


def build_config(probe: Path, run_dir: Path, workspace: Path) -> Path:
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads((probe / "config" / "settings.json").read_text(encoding="utf-8"))
    source.setdefault("env", {})["CLAUDE_CONFIG_DIR"] = str(config_dir)
    source.setdefault("permissions", {})["additionalDirectories"] = [
        str(run_dir / "home"),
        str(run_dir / "tmp"),
        str(config_dir / "teams"),
        str(config_dir / "tasks"),
        str(config_dir / "pact-sessions"),
        str(workspace),
    ]
    (config_dir / "settings.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    state_path = probe / "config" / ".claude.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state.update(
        {
            "hasCompletedOnboarding": True,
            "hasSeenTasksHint": True,
            "lastOnboardingVersion": "2.1.332",
            "theme": "dark",
            "numStartups": 2,
        }
    )
    state.setdefault("projects", {})[str(workspace)] = {
        "hasTrustDialogAccepted": True,
        "allowedTools": [],
        "mcpServers": {},
    }
    (config_dir / ".claude.json").write_text(json.dumps(state), encoding="utf-8")
    home = run_dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps(state), encoding="utf-8")
    return config_dir


def build_env(probe: Path, run_dir: Path) -> dict[str, str]:
    manifest = json.loads((probe / "manifest.json").read_text(encoding="utf-8"))
    settings = json.loads((probe / "config" / "settings.json").read_text(encoding="utf-8"))
    env = prepare.build_child_env(
        dict(settings.get("env") or {}),
        run_dir,
        Path(manifest["provider"]["token_file"]),
        str(manifest["provider"]["base_url"]),
    )
    env["CLAUDE_CONFIG_DIR"] = str(run_dir / "config")
    return env


def parse_journal(run_dir: Path) -> dict[str, Any]:
    files = sorted(
        run_dir.rglob("session-journal.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {"events": 0, "handoffs": 0, "phases": [], "commit": None, "path": None}
    rows: list[dict[str, Any]] = []
    for line in files[0].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    commits = [row for row in rows if row.get("type") == "commit"]
    return {
        "events": len(rows),
        "handoffs": sum(1 for row in rows if row.get("type") == "agent_handoff"),
        "phases": [(row.get("phase"), row.get("status")) for row in rows if row.get("type") == "phase_transition"],
        "commit": commits[-1].get("sha") if commits else None,
        "path": str(files[0]),
    }


def parse_subagents(run_dir: Path) -> dict[str, Any]:
    files = list(run_dir.rglob("subagents/*.jsonl"))
    tools: set[str] = set()
    task_tools: set[str] = set()
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "assistant":
                continue
            for block in (row.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    tools.add(name)
                    if name in {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}:
                        task_tools.add(name)
    return {"files": len(files), "tools": sorted(tools), "task_tools": sorted(task_tools)}


def parse_tokens(run_dir: Path) -> int:
    total = 0
    for path in run_dir.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = ((row.get("message") or {}).get("usage")) if row.get("type") == "assistant" else None
            if isinstance(usage, dict):
                total += sum(
                    int(usage.get(key) or 0)
                    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
                )
    return total


def run_hidden_tests(task: Task, workspace: Path, attempts: int = 2) -> dict[str, Any]:
    hidden = workspace / "hidden_tests"
    if hidden.exists():
        shutil.rmtree(hidden)
    shutil.copytree(task.hidden_tests, hidden)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    last_error = ""
    try:
        for attempt in range(1, attempts + 1):
            try:
                proc = subprocess.run(
                    [
                        str(BENCH_PYTHON),
                        "-m",
                        "pytest",
                        "-q",
                        "-p",
                        "no:randomly",
                        "hidden_tests",
                    ],
                    cwd=workspace,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                return {
                    "rc": proc.returncode,
                    "passed": proc.returncode == 0,
                    "attempts": attempt,
                    "output_tail": (proc.stdout + proc.stderr)[-2000:],
                }
            except subprocess.TimeoutExpired as exc:
                last_error = f"verifier timeout on attempt {attempt}: {exc}"
    finally:
        shutil.rmtree(hidden, ignore_errors=True)
    return {
        "rc": None,
        "passed": False,
        "attempts": attempts,
        "timeout": True,
        "output_tail": last_error[-2000:],
    }


def run_one(
    task: Task,
    condition: str,
    sample: int,
    run_id: str,
    probe: Path,
    out_root: Path,
    timeout: int,
    model: str = MODEL,
) -> dict[str, Any]:
    run_dir = (out_root / run_id / task.id / condition / f"sample-{sample}").resolve()
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    workspace = run_dir / "workspace"
    seed_workspace(task.fixture, workspace)
    build_config(probe, run_dir, workspace)
    sentinel = run_dir / "DONE"
    sentinel.unlink(missing_ok=True)
    prompt = build_prompt(task, sentinel)
    env = build_env(probe, run_dir)
    args = [
        str(CLAUDE),
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]
    if condition == "pact":
        args += ["--plugin-dir", str(probe / "pact-plugin"), "--agent", "PACT:pact-orchestrator"]
    log_path = run_dir / "pty.log"
    result: dict[str, Any] = {
        "schema": "pact-bench/track-b-run/1",
        "task": task.id,
        "condition": condition,
        "sample": sample,
        "run_id": run_id,
        "workspace": str(workspace),
        "log": str(log_path),
        "sentinel": False,
        "timed_out": False,
        "auto_approvals": 0,
        "error": None,
        "hidden": None,
        "journal": None,
        "subagents": None,
        "tokens": 0,
        "commit": None,
        "branch": None,
    }
    started = time.time()
    child = None
    log = log_path.open("w", encoding="utf-8")
    try:
        child = pexpect.spawn(
            args[0],
            args[1:],
            cwd=str(workspace),
            env=env,
            encoding="utf-8",
            codec_errors="replace",
            dimensions=(48, 140),
            timeout=60,
        )
        child.logfile = log
        screens = ["WARNING", "continue", "folder", "effort"]
        cleared: list[str] = []
        for _ in range(5):
            index = child.expect(screens + [pexpect.TIMEOUT], timeout=15)
            if index >= len(screens):
                break
            label = screens[index]
            cleared.append(label)
            if label == "effort":
                break
            time.sleep(2)
            if label == "WARNING":
                child.send("\x1b[B")
                time.sleep(0.3)
                child.send("\r")
                time.sleep(5)
            elif label == "folder":
                child.send("\t")
                time.sleep(0.3)
                child.send("\r")
                time.sleep(5)
            else:
                child.send("\r")
                time.sleep(4)
        result["screens_cleared"] = cleared
        child.send("\r")
        time.sleep(2)
        child.send("\x1b[200~" + prompt + "\x1b[201~")
        time.sleep(1)
        child.send("\r")
        deadline = time.time() + min(timeout, task.timeout_seconds)
        tail = ""
        last_approval = 0.0
        while time.time() < deadline:
            if sentinel.is_file():
                result["sentinel"] = True
                break
            if not child.isalive():
                break
            try:
                data = child.read_nonblocking(size=65536, timeout=0.5)
                if data:
                    tail = (tail + data)[-8000:]
                    if "Do you want to proceed?" in tail and time.time() - last_approval > 3:
                        child.send("1\r")
                        result["auto_approvals"] += 1
                        last_approval = time.time()
                        tail = ""
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break
            time.sleep(2)
        if not result["sentinel"]:
            result["timed_out"] = time.time() >= deadline
        time.sleep(3)
        try:
            child.sendcontrol("c")
            time.sleep(1)
            child.sendline("/exit")
            time.sleep(2)
        except Exception:
            pass
        result["exit_code"] = child.exitstatus
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass
        log.close()
        result["elapsed_seconds"] = round(time.time() - started, 1)
        try:
            result["branch"] = _git(workspace, "branch", "--show-current")
            result["commit"] = _git(workspace, "rev-parse", "HEAD")
        except Exception:
            pass
        try:
            result["hidden"] = run_hidden_tests(task, workspace)
        except Exception as exc:
            result["hidden"] = {"rc": None, "passed": False, "output_tail": f"{type(exc).__name__}: {exc}"}
        try:
            result["journal"] = parse_journal(run_dir)
            result["subagents"] = parse_subagents(run_dir)
            result["tokens"] = parse_tokens(run_dir)
        except Exception as exc:
            result["error"] = (result["error"] or "") + f"; evidence: {type(exc).__name__}: {exc}"
        result["outcome_pass"] = bool(result["hidden"] and result["hidden"].get("passed"))
        result["completed"] = bool(result["sentinel"])
        result["status"] = (
            "success"
            if result["outcome_pass"]
            else "hidden_fail"
            if result["sentinel"]
            else "timeout"
            if result["timed_out"]
            else "error"
        )
        (run_dir / "run.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _summary_row(task: str, condition: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    outcome_passes = sum(
        1 for item in items if item.get("outcome_pass", item.get("status") == "success")
    )
    completed = sum(
        1 for item in items if item.get("completed", item.get("status") == "success")
    )
    timeouts = sum(
        1 for item in items if item.get("timeout") or item.get("status") == "timeout"
    )
    elapsed = [item.get("elapsed_seconds") or 0 for item in items]
    tokens = [item.get("tokens") or 0 for item in items]
    return {
        "task": task,
        "condition": condition,
        "n": len(items),
        "outcome_passes": outcome_passes,
        "outcome_pass_rate": outcome_passes / len(items),
        "completed": completed,
        "completion_rate": completed / len(items),
        "timeouts": timeouts,
        "mean_elapsed_seconds": sum(elapsed) / len(elapsed),
        "mean_tokens": sum(tokens) / len(tokens),
        "handoffs_mean": sum((item.get("journal") or {}).get("handoffs") or 0 for item in items) / len(items),
        "task_tools": sorted(
            {tuple((item.get("subagents") or {}).get("task_tools") or []) for item in items}
        ),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault((result["task"], result["condition"]), []).append(result)
    rows = [
        _summary_row(task, condition, items)
        for (task, condition), items in sorted(groups.items())
    ]
    return {"rows": rows, "n_runs": len(results)}


def aggregate_overall(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(result["condition"], []).append(result)
    overall = []
    for condition, items in sorted(groups.items()):
        row = _summary_row("overall", condition, items)
        row.pop("task", None)
        overall.append(row)
    return overall


def run_matrix(args: argparse.Namespace) -> int:
    tasks = discover_tasks(TASKS, args.tasks.split(",") if args.tasks else None)
    conditions = [part.strip() for part in args.conditions.split(",") if part.strip()]
    for condition in conditions:
        if condition not in {"pact", "solo"}:
            raise SystemExit(f"unknown condition: {condition}")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = Path(args.out).expanduser() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        (task, condition, sample)
        for task in tasks
        for condition in conditions
        for sample in range(1, args.samples + 1)
    ]
    results_path = out_dir / "results.jsonl"
    results: list[dict[str, Any]] = []
    existing_keys: set[tuple[str, str, int]] = set()
    if results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(item)
            existing_keys.add((item["task"], item["condition"], int(item["sample"])))
    jobs = [job for job in jobs if (job[0].id, job[1], job[2]) not in existing_keys]
    print(json.dumps({"run_id": run_id, "new_jobs": len(jobs), "already_done": len(existing_keys), "tasks": [t.id for t in tasks]}, indent=2), flush=True)
    if args.dry_run:
        return 0
    with results_path.open("a", encoding="utf-8") as fh:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(run_one, task, condition, sample, run_id, Path(args.probe), Path(args.out), args.timeout, args.model): (task, condition, sample)
                for task, condition, sample in jobs
            }
            for future in concurrent.futures.as_completed(futures):
                task, condition, sample = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "task": task.id,
                        "condition": condition,
                        "sample": sample,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(result)
                fh.write(json.dumps(result) + "\n")
                fh.flush()
                print(
                    json.dumps(
                        {
                            "done": len(results),
                            "total": len(jobs),
                            "task": task.id,
                            "condition": condition,
                            "sample": sample,
                            "status": result.get("status"),
                            "elapsed": result.get("elapsed_seconds"),
                        }
                    ),
                    flush=True,
                )
    agg = aggregate(results)
    scorecard = {
        "schema": "pact-bench/track-b-scorecard/1",
        "run_id": run_id,
        "model": args.model,
        "samples": args.samples,
        "jobs": args.jobs,
        "tasks": [task.id for task in tasks],
        "conditions": conditions,
        "status_counts": dict(Counter(item.get("status") for item in results)),
        "overall": aggregate_overall(results),
        "rows": agg["rows"],
        "timeouts": [
            {
                "task": item.get("task"),
                "condition": item.get("condition"),
                "sample": item.get("sample"),
                "elapsed_seconds": item.get("elapsed_seconds"),
                "hidden_passed": bool((item.get("hidden") or {}).get("passed")),
            }
            for item in results
            if item.get("timeout") or item.get("status") == "timeout"
        ],
        "verifier_reruns": [
            {
                "task": item.get("task"),
                "condition": item.get("condition"),
                "sample": item.get("sample"),
                "attempts": (item.get("hidden") or {}).get("attempts"),
            }
            for item in results
            if int((item.get("hidden") or {}).get("attempts") or 1) > 1
        ],
        "caveats": [
            "outcome_pass uses hidden tests; completed uses sentinel/exit",
            "PACT token counts are summed assistant usage, not USD",
        ],
    }
    (out_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(scorecard["overall"], indent=2), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Track B PACT-vs-solo PTY matrix.")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=2, help="concurrent PTY sessions")
    parser.add_argument("--tasks", default=None, help="comma-separated task ids")
    parser.add_argument("--conditions", default="pact,solo")
    parser.add_argument("--timeout", type=int, default=2700)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--probe", default=str(DEFAULT_PROBE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return run_matrix(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
