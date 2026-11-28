#!/usr/bin/env python3
"""Run PluginEval against a prepared PACT copy.

The tool is pinned by the caller via --plugin-eval-dir. This runner does not
clone it; bench/external/README.md records the pinned commit and setup. It runs
uv sync --frozen, then the whole-plugin quick score and the skill scores listed
in plugin-eval-skills.txt (or --skills-file). Every command is recorded in
plugin-eval/result.json, even when one times out or the run aborts.
Tokens are real; PluginEval's model labels and USD are Anthropic-oriented and
must not be reported as DeepSeek billing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from . import prepare


DEFAULT_SKILLS_FILE = prepare.REPO_ROOT / "bench" / "external" / "plugin-eval-skills.txt"
TIMEOUT_EXIT_CODE = 124


def _parse_json(text: str) -> dict[str, Any]:
    """Return the whole output, or the last line, that parses as a JSON object."""
    for candidate in (text, *reversed(text.splitlines())):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object in output")


def load_skills(skills_file: Path) -> list[str]:
    if not skills_file.is_file():
        raise SystemExit(f"skills file not found: {skills_file}")
    skills = [
        line.strip()
        for line in skills_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not skills:
        raise SystemExit(f"skills file lists no skills: {skills_file}")
    return skills


def build_commands(
    uv: str,
    plugin: Path,
    skills: list[str],
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    sync = [uv, "sync", "--frozen", "--extra", "llm"]

    def score(target: Path, depth: str) -> list[str]:
        return [uv, "run", "plugin-eval", "score", str(target), "--depth", depth, "--output", "json"]

    skill_cmds = [(skill, score(plugin / "skills" / skill, "standard")) for skill in skills]
    return sync, [(str(plugin), score(plugin, "quick")), *skill_cmds]


def _exec(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return prepare.run_process_group(
            cmd, timeout, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"cannot run {cmd[0]!r}: {exc}") from exc


def _score_one(
    label: str, cmd: list[str], cwd: Path, env: dict[str, str], timeout: int, out: Path
) -> dict[str, Any]:
    record: dict[str, Any] = {"label": label, "command": cmd}
    stem = label.replace("/", "_").replace(".", "_")
    try:
        proc = _exec(cmd, cwd, env, timeout)
    except subprocess.TimeoutExpired as exc:
        # output_complete is False when the bounded drain after the kill expired.
        record.update(exit_code=TIMEOUT_EXIT_CODE, timed_out=True, output_complete=getattr(exc, "output_complete", True))
        stdout, stderr = _text(exc.output), _text(exc.stderr)
        record.update(stderr_tail=stderr[-2000:], stdout_tail=stdout[-2000:])
        _save(out, stem, stdout, stderr)
        return record
    record["exit_code"] = proc.returncode
    record["stderr_tail"] = proc.stderr[-2000:]
    # Raw output is saved before parsing, so a later error cannot lose it.
    _save(out, stem, proc.stdout, proc.stderr)
    try:
        parsed = _parse_json(proc.stdout)
        record["result"] = parsed
        record["composite"] = parsed.get("composite")
        layers = parsed.get("layers")
        record["layers"] = [layer.get("layer") for layer in layers if isinstance(layer, dict)] if isinstance(layers, list) else []
        record["model_usage"] = parsed.get("model_usage")
    except ValueError as exc:
        record["parse_error"] = str(exc)
        record["stdout_tail"] = proc.stdout[-2000:]
    return record


def _text(data: str | bytes | None) -> str:
    return data.decode(errors="replace") if isinstance(data, bytes) else (data or "")


def _save(out: Path, stem: str, stdout: str, stderr: str) -> None:
    (out / f"{stem}.stdout.json").write_text(stdout, encoding="utf-8")
    (out / f"{stem}.stderr.txt").write_text(stderr, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    scratch = Path(manifest["paths"]["out"])
    plugin = Path(manifest["paths"]["plugin"])
    plugin_eval_dir = Path(args.plugin_eval_dir).expanduser().resolve()
    if not (plugin_eval_dir / "pyproject.toml").is_file():
        raise SystemExit(f"not a PluginEval checkout: {plugin_eval_dir}")
    skills = load_skills(Path(args.skills_file).expanduser() if args.skills_file else DEFAULT_SKILLS_FILE)
    settings = json.loads((scratch / "config" / "settings.json").read_text(encoding="utf-8"))
    env = prepare.build_child_env(
        dict(settings.get("env") or {}),
        scratch,
        Path(manifest["provider"]["token_file"]),
        str(manifest["provider"]["base_url"]),
    )
    # uv sync only installs packages; it never needs the provider token.
    sync_env = {key: value for key, value in env.items() if key != "ANTHROPIC_AUTH_TOKEN"}

    out = scratch / "plugin-eval"
    out.mkdir(parents=True, exist_ok=True)
    sync, commands = build_commands(args.uv, plugin, skills)
    results: list[dict[str, Any]] = []
    not_run = [label for label, _ in commands]
    # complete stays false, with error/aborted_command/not_run set, unless every command was attempted.
    summary: dict[str, Any] = {
        "schema": "pact-bench/plugin-eval/1",
        "plugin_eval_dir": str(plugin_eval_dir),
        "complete": False,
        "error": None,
        "aborted_command": None,
        "sync": None,
        "results": results,
        "not_run": not_run,
    }
    current: dict[str, Any] | None = None
    try:
        if not args.skip_sync:
            current = {"label": "sync", "command": sync}
            summary["sync"] = dict(current)
            try:
                proc = _exec(sync, plugin_eval_dir, sync_env, args.sync_timeout)
            except subprocess.TimeoutExpired as exc:
                summary["sync"].update(timed_out=True, stderr_tail=_text(exc.stderr)[-2000:])
                raise SystemExit(f"uv sync timed out after {args.sync_timeout}s") from exc
            summary["sync"].update(exit_code=proc.returncode, stderr_tail=proc.stderr[-2000:])
            (out / "sync.stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (out / "sync.stderr.txt").write_text(proc.stderr, encoding="utf-8")
            if proc.returncode != 0:
                raise SystemExit(f"uv sync failed: {proc.returncode}")
        for label, cmd in commands:
            current = {"label": label, "command": cmd}
            not_run.remove(label)
            # The per-command boundary: an unexpected error fails only this record. SystemExit
            # (no uv binary) still aborts, because every later command would fail the same way.
            try:
                results.append(_score_one(label, cmd, plugin_eval_dir, env, args.timeout, out))
            except Exception as exc:
                results.append({**current, "exit_code": None, "error": f"{type(exc).__name__}: {exc}"})
        current = None
        summary["complete"] = True
    except BaseException as exc:
        summary["error"] = str(exc) if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        summary["aborted_command"] = current
        raise
    finally:
        (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    ok = all(item.get("exit_code") == 0 and "parse_error" not in item for item in results)
    return 0 if ok else 1


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run PluginEval on a prepared PACT copy.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--plugin-eval-dir", required=True)
    parser.add_argument("--skills-file", default=None, help="default bench/external/plugin-eval-skills.txt")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--sync-timeout", type=int, default=600)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
