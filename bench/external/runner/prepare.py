#!/usr/bin/env python3
"""Prepare an isolated PACT evaluation run.

WHAT THIS OWNS
  - Resolve the plugin under test from the current worktree, a git ref, or an
    explicit plugin directory.
  - Copy that plugin into a scratch directory, excluding tests/ and caches.
  - Build a scratch Claude Code settings.json from the live DeepSeek settings,
    overriding CLAUDE_CONFIG_DIR and additionalDirectories.
  - Verify the provider with a trivial headless claude -p call and record the
    resolved model.
  - Write a manifest with provenance. Never write the provider token to disk.

WHY
  The evaluation must not read or write the live ~/.claude-deepseek state, and
  every headless run must inherit the user's DeepSeek configuration without
  copying live PACT plugins or the PACT orchestrator agent into unrelated
  sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETTINGS = Path.home() / ".claude-deepseek" / "settings.json"
DEFAULT_TOKEN = Path.home() / ".claude-keys" / "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash[1m]"
MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
LIVE_ONLY_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_SYNC_SESSION_REFS",
)
EXCLUDED_NAMES = {"tests", "__pycache__", ".git", ".pytest_cache"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES}


def copy_plugin_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_ignore)


def plugin_version(repo: Path, sha: str | None, plugin_dir: Path | None) -> str:
    if plugin_dir is not None:
        manifest = plugin_dir / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "unknown"))
        return "unknown"
    if sha is None:
        return "unknown"
    try:
        raw = _git(repo, "show", f"{sha}:pact-plugin/.claude-plugin/plugin.json")
        return str(json.loads(raw).get("version", "unknown"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return "unknown"


def provenance(repo: Path, ref: str | None, plugin_dir: Path | None) -> dict[str, Any]:
    if plugin_dir is not None:
        return {
            "source": str(plugin_dir),
            "sha": None,
            "tree_sha": None,
            "version": plugin_version(repo, None, plugin_dir),
            "dirty_paths": [],
        }
    if ref is None:
        sha = _git(repo, "rev-parse", "HEAD")
        tree = _git(repo, "rev-parse", "HEAD:pact-plugin")
        dirty = [line for line in _git(repo, "status", "--porcelain", "--", "pact-plugin").splitlines() if line]
        return {
            "source": "current-worktree",
            "sha": sha,
            "tree_sha": tree,
            "version": plugin_version(repo, sha, None),
            "dirty_paths": dirty,
        }
    sha = _git(repo, "rev-parse", f"{ref}^{{commit}}")
    tree = _git(repo, "rev-parse", f"{sha}:pact-plugin")
    return {
        "source": ref,
        "sha": sha,
        "tree_sha": tree,
        "version": plugin_version(repo, sha, None),
        "dirty_paths": [],
    }


def materialize_plugin(
    repo: Path,
    out: Path,
    ref: str | None,
    plugin_dir: Path | None,
    allow_dirty: bool,
) -> tuple[Path, dict[str, Any]]:
    prov = provenance(repo, ref, plugin_dir)
    if prov["dirty_paths"] and not allow_dirty:
        raise SystemExit(
            "refusing to evaluate a dirty pact-plugin tree; commit or pass --allow-dirty; "
            f"dirty: {prov['dirty_paths'][:5]}"
        )
    dest = out / "pact-plugin"
    if plugin_dir is not None:
        copy_plugin_tree(plugin_dir, dest)
    elif ref is None:
        copy_plugin_tree(repo / "pact-plugin", dest)
    else:
        archive = out / "plugin.tar"
        with archive.open("wb") as fh:
            subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", ref, "pact-plugin"],
                check=True,
                stdout=fh,
            )
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter="data")
        archive.unlink()
        for extra in ("tests", "__pycache__", ".pytest_cache"):
            target = dest / extra
            if target.exists():
                shutil.rmtree(target)
    prov["path"] = str(dest)
    return dest, prov


def build_scratch_settings(
    source: Path,
    config_dir: Path,
    scratch: Path,
    model: str = DEFAULT_MODEL,
) -> tuple[dict[str, Any], dict[str, str]]:
    data = json.loads(source.read_text(encoding="utf-8"))
    env = dict(data.get("env") or {})
    for key in MODEL_ENV_KEYS:
        env.setdefault(key, model)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    settings = {
        "env": env,
        "teammateMode": data.get("teammateMode", "in-process"),
        "permissions": {
            "additionalDirectories": [
                str(scratch / "teams"),
                str(scratch / "tasks"),
                str(scratch / "pact-sessions"),
                "/tmp",
            ]
        },
    }
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings, env


def build_child_env(
    settings_env: dict[str, str],
    scratch: Path,
    token_file: Path,
    base_url: str,
) -> dict[str, str]:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"empty provider token file: {token_file}")
    env = {key: value for key, value in os.environ.items() if key not in LIVE_ONLY_KEYS}
    env.update(settings_env)
    env["CLAUDE_CONFIG_DIR"] = str(scratch / "config")
    env["HOME"] = str(scratch / "home")
    env["TMPDIR"] = str(scratch / "tmp")
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = token
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    for name in ("home", "tmp", "teams", "tasks", "pact-sessions"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    return env


def _parse_claude_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("empty claude output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON result in claude output")


def verify_provider(
    scratch: Path,
    out: Path,
    claude_bin: str,
    env: dict[str, str],
    timeout: int = 120,
) -> dict[str, Any]:
    proc = subprocess.run(
        [claude_bin, "-p", "Reply with exactly OK", "--output-format", "json", "--max-turns", "1"],
        cwd=str(out),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    record: dict[str, Any] = {
        "checked_at": utc_now(),
        "exit_code": proc.returncode,
        "stderr": proc.stderr[-2000:],
        "stdout_tail": proc.stdout[-2000:],
    }
    try:
        parsed = _parse_claude_json(proc.stdout)
    except ValueError as exc:
        record["error"] = str(exc)
        (out / "provider-check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"provider verification failed: {exc}") from exc
    usage = parsed.get("modelUsage") or {}
    record["result"] = parsed.get("result")
    record["model_usage"] = usage
    record["model_keys"] = sorted(str(key) for key in usage)
    record["is_error"] = parsed.get("is_error")
    record["num_turns"] = parsed.get("num_turns")
    (out / "provider-check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if proc.returncode != 0 or parsed.get("is_error"):
        raise SystemExit(f"provider verification exited {proc.returncode}")
    if not any("deepseek-flash" in key for key in usage):
        raise SystemExit(f"provider verification did not resolve a DeepSeek model: {sorted(usage)}")
    return record


def prepare(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        raise SystemExit(f"output directory is not empty: {out}; pass --force to replace")
    if out.exists() and args.force:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("config", "home", "tmp", "teams", "tasks", "pact-sessions"):
        (out / name).mkdir(parents=True, exist_ok=True)

    settings_source = Path(args.settings_source).expanduser()
    token_file = Path(args.token_file).expanduser()
    plugin_dir = Path(args.plugin_dir).expanduser().resolve() if args.plugin_dir else None
    plugin_path, prov = materialize_plugin(
        REPO_ROOT,
        out,
        args.plugin_ref,
        plugin_dir,
        args.allow_dirty,
    )
    settings, env = build_scratch_settings(settings_source, out / "config", out, args.model)
    child_env = build_child_env(env, out, token_file, args.base_url)
    manifest: dict[str, Any] = {
        "schema": "pact-bench/prepare/1",
        "created_at": utc_now(),
        "plugin": prov,
        "settings": {
            "source": str(settings_source),
            "sha256": sha256_file(settings_source),
            "path": str(out / "config" / "settings.json"),
            "teammate_mode": settings.get("teammateMode"),
            "env_keys": sorted(env),
            "model": env.get("ANTHROPIC_MODEL"),
        },
        "provider": {
            "base_url": args.base_url,
            "token_file": str(token_file),
            "model": env.get("ANTHROPIC_MODEL"),
        },
        "paths": {
            "out": str(out),
            "plugin": str(plugin_path),
            "config": str(out / "config"),
            "home": str(out / "home"),
            "tmp": str(out / "tmp"),
            "teams": str(out / "teams"),
            "tasks": str(out / "tasks"),
            "pact_sessions": str(out / "pact-sessions"),
        },
    }
    if args.verify:
        claude_bin = args.claude_bin or shutil.which("claude") or "claude"
        manifest["provider"]["check"] = verify_provider(out, out, claude_bin, child_env)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare an isolated PACT evaluation run.")
    parser.add_argument("--out", required=True, help="scratch output directory")
    parser.add_argument("--plugin-ref", default=None, help="git tag/SHA to evaluate; default current worktree")
    parser.add_argument("--plugin-dir", default=None, help="explicit plugin directory to copy")
    parser.add_argument("--settings-source", default=str(DEFAULT_SETTINGS), help="live DeepSeek settings.json")
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN), help="DeepSeek token file")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DeepSeek Anthropic-compatible endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="requested model alias")
    parser.add_argument("--allow-dirty", action="store_true", help="allow a dirty pact-plugin tree")
    parser.add_argument("--verify", action="store_true", help="run a trivial headless provider check")
    parser.add_argument("--claude-bin", default=None, help="claude binary (default: PATH)")
    parser.add_argument("--force", action="store_true", help="replace a non-empty output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    return prepare(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
