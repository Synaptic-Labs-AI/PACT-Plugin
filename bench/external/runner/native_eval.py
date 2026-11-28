#!/usr/bin/env python3
"""Run native claude plugin evals against a prepared PACT copy.

Reads the prepare manifest, injects bench/external/native-evals/evals into the
scratch plugin copy, runs claude plugin eval headlessly with the scratch
DeepSeek environment, and records the command/result/exit code in
<run>/native-eval/result.json on every exit path. The model (CLI --model and
--judge-model) is the one fixed at prepare time, read from the manifest. A
timeout kills the eval's whole process group.

Cleanup contract: the short TMPDIR/HOME root under --temp-root and the
injected evals/ dir are removed on every exit path (an evals/results/ dir the
tool wrote is moved to <run>/native-eval/plugin-evals-results/<run id>/ first).
The scaffold dirs that --keep-temp leaves under TMPDIR are moved to
<run>/native-eval/kept-temp/<run id>/ first (the eval tool makes some of them read-only, so they are made writable
before the move). A cleanup failure is recorded and makes the run exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import prepare

# Unix-socket path budget for the sandbox bridge, measured on TMPDIR itself.
MAX_TMPDIR_CHARS = 51
TEMP_PREFIX = "pact-native-"
# tempfile.mkdtemp appends 8 random characters to the prefix.
TEMP_SUFFIX_CHARS = 8
TIMEOUT_EXIT_CODE = 124
REPORT_FILES = {"report_json": "report.json", "report_html": "report.html"}


def _default_cases() -> Path:
    return prepare.REPO_ROOT / "bench" / "external" / "native-evals" / "evals"


def predicted_tmpdir_length(temp_parent: Path) -> int:
    return len(str(temp_parent / (TEMP_PREFIX + "x" * TEMP_SUFFIX_CHARS) / "tmp"))


def make_writable(root: Path) -> None:
    """Give the owner rwx on every directory under root (top-down) so it can be moved or removed."""
    if root.is_symlink() or not root.is_dir():
        return
    os.chmod(root, stat.S_IRWXU)
    for dirpath, dirnames, _files in os.walk(root):
        for name in dirnames:
            child = Path(dirpath) / name
            if not child.is_symlink():
                os.chmod(child, stat.S_IRWXU)


def _check_cases(cases_dir: Path) -> None:
    if not cases_dir.is_dir():
        raise SystemExit(f"native eval cases directory does not exist: {cases_dir}")
    if not any(cases_dir.iterdir()):
        raise SystemExit(f"native eval cases directory is empty: {cases_dir}")


def _child_env(manifest: dict[str, Any], scratch: Path) -> dict[str, str]:
    settings = json.loads((scratch / "config" / "settings.json").read_text(encoding="utf-8"))
    return prepare.build_child_env(
        dict(settings.get("env") or {}),
        scratch,
        Path(manifest["provider"]["token_file"]),
        str(manifest["provider"]["base_url"]),
    )


def build_command(args: argparse.Namespace, claude_bin: str, plugin: Path, out: Path, model: str) -> list[str]:
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
        # Variadic (<tools...>): the list ends at the next option.
        "--allow-tools",
        "Bash",
        "Write",
        "Edit",
        "--model",
        model,
        "--judge-model",
        model,
    ]
    if args.max_cost_usd is not None:
        cmd.extend(["--max-cost-usd", str(args.max_cost_usd)])
    return cmd


def _launch(cmd: list[str], cwd: Path, env: dict[str, str], out: Path, timeout: int) -> tuple[int, bool]:
    with (out / "stdout.txt").open("w", encoding="utf-8") as stdout, (out / "stderr.txt").open(
        "w", encoding="utf-8"
    ) as stderr:
        try:
            proc = prepare.run_process_group(cmd, timeout, cwd=str(cwd), env=env, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired:
            return TIMEOUT_EXIT_CODE, True
        except OSError as exc:
            raise SystemExit(f"could not launch {cmd[0]}: {exc}") from exc
    return proc.returncode, False


def _cleanup(short_root: Path | None, injected: Path | None, out: Path, record: dict[str, Any]) -> None:
    """Keep the --keep-temp scaffolds and the evals results under this run's id
    (the short root's name), then remove the short root and the injected evals.
    injected is only ever set after short_root exists."""
    errors: list[str] = []
    run_id = short_root.name if short_root is not None else "unknown-run"
    if short_root is not None and short_root.exists():
        try:
            make_writable(short_root)
            kept = short_root / "tmp"
            if kept.is_dir() and any(kept.iterdir()):
                target = out / "kept-temp" / run_id
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(kept), str(target))
                record["kept_temp"] = str(target)
            shutil.rmtree(short_root)
        except OSError as exc:
            errors.append(f"temp root {short_root}: {exc}")
    if injected is not None and injected.exists():
        try:
            make_writable(injected)
            results = injected / "results"
            if results.is_dir():
                target = out / "plugin-evals-results" / run_id
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(results), str(target))
                record["plugin_evals_results"] = str(target)
            shutil.rmtree(injected)
        except OSError as exc:
            errors.append(f"injected evals {injected}: {exc}")
    if errors:
        record["cleanup_error"] = "; ".join(errors)
        print(f"native eval cleanup failed: {record['cleanup_error']}", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    scratch = Path(manifest["paths"]["out"])
    plugin = Path(manifest["paths"]["plugin"])
    cases_dir = Path(args.cases_dir).expanduser().resolve() if args.cases_dir else _default_cases()
    temp_parent = Path(args.temp_root).expanduser().absolute()
    out = scratch / "native-eval"
    out.mkdir(parents=True, exist_ok=True)
    # The reports are rewritten each run; a stale one must not be recorded as this run's.
    for name in REPORT_FILES.values():
        (out / name).unlink(missing_ok=True)
    model = manifest["provider"]["model"]
    record: dict[str, Any] = {
        "schema": "pact-bench/native-eval/1",
        "command": None,
        "cases_dir": str(cases_dir),
        "temp_root": None,
        "exit_code": None,
        "timed_out": False,
        "error": None,
        "kept_temp": None,
        "plugin_evals_results": None,
        "cleanup_error": None,
        "report_json": None,
        "report_html": None,
    }
    short_root: Path | None = None
    injected: Path | None = None
    try:
        _check_cases(cases_dir)
        claude_bin = prepare.resolve_claude_bin(args.claude_bin)
        if predicted_tmpdir_length(temp_parent) > MAX_TMPDIR_CHARS:
            raise SystemExit(
                "native eval TMPDIR is too long for the sandbox bridge: "
                f"{predicted_tmpdir_length(temp_parent)} chars (max {MAX_TMPDIR_CHARS}); "
                "choose a shorter --temp-root"
            )
        env = _child_env(manifest, scratch)
        temp_parent.mkdir(parents=True, exist_ok=True)
        short_root = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=str(temp_parent)))
        record["temp_root"] = str(short_root)
        for name in ("tmp", "home"):
            (short_root / name).mkdir()
        env["TMPDIR"] = str(short_root / "tmp")
        env["HOME"] = str(short_root / "home")

        injected = plugin / "evals"
        if injected.exists():
            shutil.rmtree(injected)
        shutil.copytree(cases_dir, injected)

        cmd = build_command(args, claude_bin, plugin, out, model)
        record["command"] = cmd
        record["exit_code"], record["timed_out"] = _launch(cmd, scratch, env, out, args.timeout)
    except SystemExit as exc:
        record["error"] = str(exc)
        raise
    finally:
        _cleanup(short_root, injected, out, record)
        for key, name in REPORT_FILES.items():
            record[key] = str(out / name) if (out / name).is_file() else None
        (out / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    ok = record["exit_code"] == 0 and not record["timed_out"] and not record["cleanup_error"]
    return 0 if ok else 1


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run native claude plugin evals.")
    parser.add_argument("--manifest", required=True, help="prepare manifest.json")
    parser.add_argument("--cases-dir", default=None, help="native eval cases directory")
    parser.add_argument(
        "--temp-root",
        default=str(Path.home()),
        help="short writable parent for TMPDIR/HOME; TMPDIR must stay within "
        f"{MAX_TMPDIR_CHARS} chars to keep sandbox bridge sockets under the Unix socket path limit",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--claude-bin", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
