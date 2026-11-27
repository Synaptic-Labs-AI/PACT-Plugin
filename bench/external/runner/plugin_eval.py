#!/usr/bin/env python3
"""Run PluginEval against a prepared PACT copy.

The tool is pinned by the caller via --plugin-eval-dir. This runner does not
clone it; bench/external/README.md records the pinned commit and setup. It runs
uv sync --frozen, then the whole-plugin quick score and selected skill scores.
Tokens are real; PluginEval's model labels and USD are Anthropic-oriented and
must not be reported as DeepSeek billing.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import prepare


DEFAULT_SKILLS = (
    "pact-agent-teams",
    "pact-handoff-harvest",
    "pact-memory",
)
DEFAULT_SKILLS_FILE = prepare.REPO_ROOT / "bench" / "external" / "plugin-eval-skills.txt"


def _parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object in output")


def build_commands(
    plugin_eval_dir: Path,
    plugin: Path,
    skills: list[str],
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    sync = ["uv", "sync", "--frozen", "--extra", "llm"]
    plugin_cmd = [
        "uv",
        "run",
        "plugin-eval",
        "score",
        str(plugin),
        "--depth",
        "quick",
        "--output",
        "json",
    ]
    skill_cmds = [
        (
            skill,
            [
                "uv",
                "run",
                "plugin-eval",
                "score",
                str(plugin / "skills" / skill),
                "--depth",
                "standard",
                "--output",
                "json",
            ],
        )
        for skill in skills
    ]
    return sync, [(str(plugin), plugin_cmd), *skill_cmds]


def run(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    scratch = Path(manifest["paths"]["out"])
    plugin = Path(manifest["paths"]["plugin"])
    plugin_eval_dir = Path(args.plugin_eval_dir).expanduser().resolve()
    if not (plugin_eval_dir / "pyproject.toml").is_file():
        raise SystemExit(f"not a PluginEval checkout: {plugin_eval_dir}")
    settings = json.loads((scratch / "config" / "settings.json").read_text(encoding="utf-8"))
    env = prepare.build_child_env(
        dict(settings.get("env") or {}),
        scratch,
        Path(manifest["provider"]["token_file"]),
        str(manifest["provider"]["base_url"]),
    )
    skills_file = Path(args.skills_file) if args.skills_file else DEFAULT_SKILLS_FILE
    if skills_file.is_file():
        skills = [
            line.strip()
            for line in skills_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        skills = list(DEFAULT_SKILLS)

    out = scratch / "plugin-eval"
    out.mkdir(parents=True, exist_ok=True)
    sync, commands = build_commands(plugin_eval_dir, plugin, skills)
    results: list[dict[str, Any]] = []
    if not args.skip_sync:
        proc = subprocess.run(
            [args.uv, *sync[1:]],
            cwd=str(plugin_eval_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=args.sync_timeout,
        )
        (out / "sync.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (out / "sync.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise SystemExit(f"uv sync failed: {proc.returncode}")
    for label, cmd in commands:
        full = [args.uv, *cmd[1:]]
        proc = subprocess.run(
            full,
            cwd=str(plugin_eval_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        record: dict[str, Any] = {
            "label": label,
            "command": full,
            "exit_code": proc.returncode,
            "stderr_tail": proc.stderr[-2000:],
        }
        try:
            parsed = _parse_json(proc.stdout)
            record["result"] = parsed
            record["composite"] = parsed.get("composite")
            record["layers"] = [layer.get("layer") for layer in parsed.get("layers", [])]
            record["model_usage"] = parsed.get("model_usage")
        except ValueError as exc:
            record["parse_error"] = str(exc)
            record["stdout_tail"] = proc.stdout[-2000:]
        results.append(record)
        (out / (label.replace("/", "_").replace(".", "_") + ".stdout.json")).write_text(
            proc.stdout, encoding="utf-8"
        )
        (out / (label.replace("/", "_").replace(".", "_") + ".stderr.txt")).write_text(
            proc.stderr, encoding="utf-8"
        )
    summary = {"schema": "pact-bench/plugin-eval/1", "plugin_eval_dir": str(plugin_eval_dir), "results": results}
    (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all(item.get("exit_code") == 0 for item in results) else 1


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run PluginEval on a prepared PACT copy.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--plugin-eval-dir", required=True)
    parser.add_argument("--skills-file", default=None)
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--sync-timeout", type=int, default=600)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
