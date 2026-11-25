#!/usr/bin/env python3
"""Run native claude plugin evals against a prepared PACT copy.

Reads the prepare manifest, injects bench/external/native-evals/evals into the
scratch plugin copy, runs claude plugin eval headlessly with the scratch
DeepSeek environment, and records the command/result/exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import prepare


def _default_cases() -> Path:
    return prepare.REPO_ROOT / "bench" / "external" / "native-evals" / "evals"


def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    scratch = Path(manifest["paths"]["out"])
    plugin = Path(manifest["paths"]["plugin"])
    settings = json.loads((scratch / "config" / "settings.json").read_text(encoding="utf-8"))
    settings_env = dict(settings.get("env") or {})
    token_file = Path(manifest["provider"]["token_file"])
    base_url = str(manifest["provider"]["base_url"])
    env = prepare.build_child_env(settings_env, scratch, token_file, base_url)
    temp_parent = Path(args.temp_root).expanduser()
    temp_parent.mkdir(parents=True, exist_ok=True)
    short_root = Path(tempfile.mkdtemp(prefix="pact-native-", dir=str(temp_parent)))
    for name in ("tmp", "home"):
        (short_root / name).mkdir(parents=True, exist_ok=True)
    short_tmp = short_root / "tmp"
    if len(str(short_tmp)) > 51:
        raise SystemExit(
            "native eval TMPDIR is too long for the sandbox bridge: "
            f"{len(str(short_tmp))} chars (max 51); choose a shorter --temp-root outside /tmp"
        )
    env["TMPDIR"] = str(short_tmp)
    env["HOME"] = str(short_root / "home")
    env.pop("TMP", None)
    env.pop("TEMP", None)

    cases_dir = Path(args.cases_dir).expanduser().resolve() if args.cases_dir else _default_cases()
    if not cases_dir.is_dir():
        raise SystemExit(f"native eval cases directory does not exist: {cases_dir}")
    if not any(cases_dir.iterdir()):
        raise SystemExit(f"native eval cases directory is empty: {cases_dir}")

    injected = plugin / "evals"
    if injected.exists():
        shutil.rmtree(injected)
    shutil.copytree(cases_dir, injected)

    out = scratch / "native-eval"
    out.mkdir(parents=True, exist_ok=True)
    claude_bin = args.claude_bin or shutil.which("claude") or "claude"
    model = args.model or manifest["provider"]["model"]
    cmd = [
        claude_bin,
        "plugin",
        "eval",
        str(plugin),
        "--eval-dir",
        "evals",
        "--ablation",
        "with-without",
        "--runs",
        str(args.runs),
        "--concurrency",
        str(args.concurrency),
        "--trust-plugin",
        "--no-publish",
        "--threshold",
        "0",
        "--scaffold",
        "--json",
        str(out / "report.json"),
        "--report",
        str(out / "report.html"),
        "--output-dir",
        str(out / "runs"),
        "--keep-temp",
        "--allow-tools",
        "Bash",
        "Write",
        "Edit",
        "--model",
        model,
        "--judge-model",
        args.judge_model or model,
    ]
    if args.max_cost_usd is not None:
        cmd.extend(["--max-cost-usd", str(args.max_cost_usd)])

    stdout = (out / "stdout.txt").open("w", encoding="utf-8")
    stderr = (out / "stderr.txt").open("w", encoding="utf-8")
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(scratch),
            env=env,
            stdout=stdout,
            stderr=stderr,
            timeout=args.timeout,
        )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        code = 124
    finally:
        stdout.close()
        stderr.close()

    record = {
        "schema": "pact-bench/native-eval/1",
        "command": cmd,
        "cases_dir": str(cases_dir),
        "temp_root": str(short_root),
        "exit_code": code,
        "timed_out": timed_out,
        "report_json": str(out / "report.json") if (out / "report.json").is_file() else None,
        "report_html": str(out / "report.html") if (out / "report.html").is_file() else None,
    }
    (out / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0 if code == 0 and not timed_out else 1


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run native claude plugin evals.")
    parser.add_argument("--manifest", required=True, help="prepare manifest.json")
    parser.add_argument("--cases-dir", default=None, help="native eval cases directory")
    parser.add_argument(
        "--temp-root",
        default=str(Path.home()),
        help="short writable parent for TMPDIR/HOME, outside /tmp; keeps sandbox bridge sockets under the Unix socket path limit",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--model", default=None)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--claude-bin", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
