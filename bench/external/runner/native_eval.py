#!/usr/bin/env python3
"""Run native claude plugin evals against a prepared PACT copy.

Reads the prepare manifest, injects bench/external/native-evals/evals into the
scratch plugin copy, runs claude plugin eval headlessly with the scratch
DeepSeek environment, and records the command/result/exit code in
<run>/native-eval/result.json on every exit path once the manifest is read and
its run directory is writable. A
SIGTERM/SIGHUP is recorded only while the eval child runs; at any other moment
it gets the default action. The eval runs from <temp-root>/pact-native-XXXXXXXX/work
(the temp root's real path), outside the repo and with no Claude Code memory
file above it, checked before the temp root is created and again after the eval
(a file that appeared meanwhile is listed in clean_root_changed and fails the
run). The model (CLI --model and --judge-model) is the one fixed at prepare
time, read from the manifest. The eval's whole process group is killed when it
ends, times out or is signalled. Its --output-dir is <run>/native-eval/runs/<run id>/.
--threshold 0 is always passed, so the eval's pass/fail and exit code are not
the measurement; the with/without delta in report.json is. native-eval exits 0
only if the eval exited 0, wrote report.json, and cleanup and the clean-root
re-check passed. --allow-tools grants
only the gated tools some case's own allowed_tools lists (recorded as
tool_grants). Per the claude 2.1.332 CLI's own messages, a gated tool also has
to be in the case's allowed_tools, so the grant does not widen a case (not
verified with a live run).

Cleanup contract: the short TMPDIR/HOME root under --temp-root and the
injected evals/ dir are removed on every exit path, even when keeping their
contents fails (an evals/results/ dir the
tool wrote is moved to <run>/native-eval/plugin-evals-results/<run id>/ first).
The scaffold dirs that --keep-temp leaves under TMPDIR are moved to
<run>/native-eval/kept-temp/<run id>/ first (the eval tool makes some of them read-only, so they are made writable
before the move). A cleanup failure is recorded and makes the run exit 1; a
failed keep names its target and says when a partial copy was left there.
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
# Tools claude plugin eval treats as gated (its --help); mcp__* tools are gated too.
GATED_TOOLS = ("Bash", "Write", "Edit", "WebFetch")
CASE_FILES = ("prompt.md", "case.yaml")


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


def _case_dirs(cases_dir: Path) -> list[Path]:
    """Every case dir claude plugin eval would discover: any dir under cases_dir,
    at any depth, holding a case.yaml or a prompt.md."""
    found = {path.parent for name in CASE_FILES for path in cases_dir.rglob(name) if path.is_file()}
    return sorted(found)


def _front_matter(text: str) -> str | None:
    """The YAML between a leading '---' line and the next '---' line, else None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    raise SystemExit("front matter opened with '---' is never closed")


def _split_scalar(value: str) -> list[str]:
    """A comma-separated scalar, split at commas outside Tool(...) patterns."""
    entries, depth, current = [], 0, ""
    for char in value:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            entries.append(current)
            current = ""
        else:
            current += char
    return [entry.strip() for entry in [*entries, current] if entry.strip()]


def _allowed_tools(source: Path) -> list[str]:
    """allowed_tools from a case.yaml or a prompt.md's front matter: a list of
    strings or a single (comma-separated) string; absent means none."""
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("native-eval needs PyYAML to read case allowed_tools (it is in bench/constraints.txt)") from exc
    text = source.read_text(encoding="utf-8")
    try:
        body = text if source.name == "case.yaml" else _front_matter(text)
        data = yaml.safe_load(body) if body else None
    except (SystemExit, yaml.YAMLError) as exc:
        raise SystemExit(f"{source}: cannot read its YAML: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, dict):
        raise SystemExit(f"{source}: expected a YAML mapping, got {type(data).__name__}")
    value = data.get("allowed_tools")
    if value is None:
        return []
    if isinstance(value, str):
        return _split_scalar(value)
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return [entry.strip() for entry in value if entry.strip()]
    raise SystemExit(f"{source}: allowed_tools must be a list of names or a single string, got {value!r}")


def case_tool_grants(cases_dir: Path) -> list[str]:
    """The gated tools (GATED_TOOLS, mcp__*, and their Tool(pattern) forms) that some
    case's own allowed_tools lists, in first-seen order over the discovered cases.

    In claude plugin eval a gated tool is usable only when it is in the case's
    allowed_tools AND granted with --allow-tools, so granting this union adds no
    tool to a case that does not list it, and a run whose cases list none grants nothing.
    """
    grants: list[str] = []
    for case in _case_dirs(cases_dir):
        for name in CASE_FILES:
            if not (case / name).is_file():
                continue
            for entry in _allowed_tools(case / name):
                base = entry.split("(", 1)[0]
                if (base in GATED_TOOLS or base.startswith("mcp__")) and entry not in grants:
                    grants.append(entry)
    return grants


def build_command(
    args: argparse.Namespace,
    claude_bin: str,
    plugin: Path,
    out: Path,
    model: str,
    runs_dir: Path,
    grants: list[str],
) -> list[str]:
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
        # threshold 0: every completed case passes, so pass/fail and the exit code are
        # not the signal; read the with/without delta in report.json.
        "--threshold",
        "0",
        "--scaffold",
        "--json",
        str(out / "report.json"),
        "--report",
        str(out / "report.html"),
        "--output-dir",
        str(runs_dir),
        "--keep-temp",
        # Variadic (<tools...>): the list ends at the next option. Only the gated tools
        # the cases themselves list (case_tool_grants); none when they list none.
        *(["--allow-tools", *grants] if grants else []),
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
    # Keeping and removing are separate steps, so a failed keep (e.g. a cross-filesystem
    # move that cannot copy a socket) still removes the directory.
    for root, keep, field, label in (
        (short_root, "tmp", "kept_temp", "temp root"),
        (injected, "results", "plugin_evals_results", "injected evals"),
    ):
        if root is None or not root.exists():
            continue
        source = root / keep
        target = out / ("kept-temp" if field == "kept_temp" else "plugin-evals-results") / run_id
        try:
            make_writable(root)
            if source.is_dir() and any(source.iterdir()):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                record[field] = str(target)
        except OSError as exc:
            # A failed cross-filesystem move can leave a partial copy at target; name it.
            partial = f" (partial copy left at {target})" if target.exists() else ""
            errors.append(f"keeping {label} {source} -> {target}{partial}: {exc}")
        try:
            make_writable(root)
            shutil.rmtree(root)
        except OSError as exc:
            errors.append(f"removing {label} {root}: {exc}")
    if errors:
        record["cleanup_error"] = "; ".join(errors)
        print(f"native eval cleanup failed: {record['cleanup_error']}", file=sys.stderr)


def _load_manifest(path: Path) -> tuple[dict[str, Any], Path, Path, str]:
    """Read the prepare manifest; SystemExit naming what is wrong, before anything is written.

    The plugin path must be the run's own <out>/pact-plugin copy, because the run
    injects and later deletes evals/ inside it.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        scratch, plugin = Path(manifest["paths"]["out"]), Path(manifest["paths"]["plugin"])
        model = str(manifest["provider"]["model"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"cannot read prepare manifest {path}: {type(exc).__name__}: {exc}") from exc
    if plugin.resolve() != (scratch / "pact-plugin").resolve():
        raise SystemExit(f"prepare manifest {path}: plugin {plugin} is not the run's own {scratch / 'pact-plugin'}")
    return manifest, scratch, plugin, model


def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest, scratch, plugin, model = _load_manifest(manifest_path)
    cases_dir = Path(args.cases_dir).expanduser().resolve() if args.cases_dir else _default_cases()
    temp_parent = Path(args.temp_root).expanduser().resolve()
    out = scratch / "native-eval"
    out.mkdir(parents=True, exist_ok=True)
    # The reports are rewritten each run; a stale one must not be recorded as this run's.
    for name in REPORT_FILES.values():
        (out / name).unlink(missing_ok=True)
    record: dict[str, Any] = {
        "schema": "pact-bench/native-eval/1",
        "command": None,
        "cases_dir": str(cases_dir),
        "temp_root": None,
        "cwd": None,
        "exit_code": None,
        "timed_out": False,
        "error": None,
        "kept_temp": None,
        "plugin_evals_results": None,
        "cleanup_error": None,
        "runs_dir": None,
        "tool_grants": None,
        "clean_root_changed": None,
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
        temp_parent = prepare.check_clean_root(temp_parent)
        env = _child_env(manifest, scratch)
        temp_parent.mkdir(parents=True, exist_ok=True)
        short_root = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=str(temp_parent)))
        record["temp_root"] = str(short_root)
        for name in ("tmp", "home", "work"):
            (short_root / name).mkdir()
        # The eval runs from a clean cwd under the temp root, so no repo CLAUDE.md loads.
        cwd = short_root / "work"
        record["cwd"] = str(cwd)
        env["TMPDIR"] = str(short_root / "tmp")
        env["HOME"] = str(short_root / "home")

        injected = plugin / "evals"
        if injected.exists():
            shutil.rmtree(injected)
        shutil.copytree(cases_dir, injected)

        runs_dir = out / "runs" / short_root.name  # per run, so earlier runs never mix in
        record["runs_dir"] = str(runs_dir)
        record["tool_grants"] = case_tool_grants(cases_dir)
        cmd = build_command(args, claude_bin, plugin, out, model, runs_dir, record["tool_grants"])
        record["command"] = cmd
        record["exit_code"], record["timed_out"] = _launch(cmd, cwd, env, out, args.timeout)
        # The clean-root check ran at start; a memory or settings file that appeared
        # during the run (e.g. another user writing to a shared /tmp) is recorded.
        changed = prepare.memory_files_above(temp_parent)
        record["clean_root_changed"] = [str(path) for path in changed] or None
    except BaseException as exc:
        record["error"] = str(exc) if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _cleanup(short_root, injected, out, record)
        for key, name in REPORT_FILES.items():
            record[key] = str(out / name) if (out / name).is_file() else None
        (out / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    ok = (
        record["exit_code"] == 0
        and not record["timed_out"]
        and not record["cleanup_error"]
        and not record["clean_root_changed"]
        and record["report_json"]  # a run that wrote no report measured nothing
    )
    return 0 if ok else 1


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run native claude plugin evals.")
    parser.add_argument("--manifest", required=True, help="prepare manifest.json")
    parser.add_argument("--cases-dir", default=None, help="native eval cases directory")
    parser.add_argument(
        "--temp-root",
        default=str(prepare.DEFAULT_TEMP_ROOT),
        help="short writable parent for the eval's cwd, TMPDIR and HOME: outside the repo, with no "
        "Claude Code memory or project-settings file (see prepare.check_clean_root) in it or its "
        "parents, and TMPDIR within "
        f"{MAX_TMPDIR_CHARS} chars (sandbox bridge "
        "sockets must stay under the Unix socket path limit)",
    )
    parser.add_argument("--runs", type=prepare.positive_int, default=3)
    parser.add_argument("--concurrency", type=prepare.positive_int, default=1)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--timeout", type=prepare.positive_int, default=3600)
    parser.add_argument("--claude-bin", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
