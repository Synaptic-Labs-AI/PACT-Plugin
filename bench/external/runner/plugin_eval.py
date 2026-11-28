#!/usr/bin/env python3
"""Run PluginEval against a prepared PACT copy.

The tool is pinned by the caller via --plugin-eval-dir. This runner does not
clone it; bench/external/README.md records the pinned commit and setup. It runs
uv sync --frozen, then the whole-plugin quick score and the skill scores listed
in plugin-eval-skills.txt (or --skills-file). All uv commands of a run share one
fresh cwd under --temp-root (refused by prepare.check_clean_root if that root is
in the repo or below a Claude Code memory file), with the checkout passed as
--project; uv sync and uv run both use --frozen. The record names the git
checkout holding the PluginEval dir (plugin_eval_repo, its toplevel), that
checkout's HEAD commit, and whether files under the PluginEval dir are dirty; all
three are null if the dir is in no git checkout. The pin is enforced only with
--expect-commit, which refuses a different commit, a dirty PluginEval dir or a
non-git dir before any command runs. A score whose
JSON has no composite counts as a failed command.
Output files a previous run left in plugin-eval/ are removed at the start.
Score commands start with the provider token as ANTHROPIC_AUTH_TOKEN (with
ANTHROPIC_BASE_URL); PluginEval's judge is expected to pass that environment to
the claude CLI through claude-agent-sdk (not verified in this tree). uv sync
gets no token, and ANTHROPIC_API_KEY is never set.
Child output (in result.json's tails and parsed results, and the per-command
.stdout.json/.stderr.txt and sync.*.txt files) can contain the token; stdout
prints only the record's path.
Once the manifest is read, plugin-eval/result.json is written when the run ends,
fails a check, times out, aborts or gets SIGTERM/SIGHUP. An unreadable manifest
exits with a message and no record (the record lives under the manifest's out
directory); SIGKILL leaves no record.
Tokens are real; PluginEval's model labels and USD are Anthropic-oriented and
must not be reported as DeepSeek billing.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import prepare


DEFAULT_SKILLS_FILE = prepare.REPO_ROOT / "bench" / "external" / "plugin-eval-skills.txt"
TIMEOUT_EXIT_CODE = 124
# Files this runner writes under plugin-eval/ besides result.json (rewritten every run).
STALE_OUTPUT = re.compile(r"\d{2}-.+\.(stdout\.json|stderr\.txt)|sync\.(stdout|stderr)\.txt")


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


def _checkout_state(directory: Path) -> tuple[str | None, str | None, bool | None]:
    """(toplevel, HEAD commit, dirty) of the git checkout holding the PluginEval dir; the dir
    may sit below the toplevel (plugins/plugin-eval in the agents clone). dirty covers only
    files under the dir. (None, None, None) if the dir is in no git checkout."""
    try:
        top = prepare._git(directory, "rev-parse", "--show-toplevel").strip()
        commit = prepare._git(directory, "rev-parse", "HEAD").strip()
        # git -C <dir> status -- . scopes the check to the PluginEval dir; --untracked-files=all
        # overrides a checkout's own status.showUntrackedFiles=no.
        dirty = bool(prepare._git(directory, "status", "--porcelain", "--untracked-files=all", "--", ".").strip())
    except (SystemExit, OSError):
        return None, None, None
    return top, commit, dirty


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
    bad = [skill for skill in skills if "/" in skill or skill.startswith(".")]
    if bad:
        raise SystemExit(f"skills file entries must be plain skill directory names: {bad}")
    duplicates = sorted({skill for skill in skills if skills.count(skill) > 1})
    if duplicates:
        raise SystemExit(f"skills file lists a skill twice: {duplicates}")
    return skills


def build_commands(
    uv: str,
    project: Path,
    plugin: Path,
    skills: list[str],
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    sync = [uv, "sync", "--project", str(project), "--frozen", "--extra", "llm"]

    def score(target: Path, depth: str) -> list[str]:
        return [
            uv, "run", "--frozen", "--project", str(project), "plugin-eval", "score", str(target),
            "--depth", depth, "--output", "json",
        ]

    skill_cmds = [(skill, score(plugin / "skills" / skill, "standard")) for skill in skills]
    return sync, [(str(plugin), score(plugin, "quick")), *skill_cmds]


def _exec(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return prepare.run_process_group(
            cmd, timeout, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"cannot run {cmd[0]!r}: {exc}") from exc


def output_stem(index: int, label: str) -> str:
    """Collision-free, length-bounded file stem: the command's position plus a short slug."""
    return f"{index:02d}-{re.sub(r'[^A-Za-z0-9_-]+', '_', Path(label).name)[:60]}"


def _score_one(
    label: str, cmd: list[str], cwd: Path, env: dict[str, str], timeout: int, out: Path, stem: str
) -> dict[str, Any]:
    record: dict[str, Any] = {"label": label, "command": cmd, "output_stem": stem}
    try:
        proc = _exec(cmd, cwd, env, timeout)
    except subprocess.TimeoutExpired as exc:
        # output_complete is False when the bounded drain after the kill expired.
        # child_exit_code: the child's own code when it had exited and only a leftover held the pipe.
        record.update(
            exit_code=TIMEOUT_EXIT_CODE, timed_out=True, output_complete=getattr(exc, "output_complete", True),
            child_exit_code=getattr(exc, "child_exit_code", None),
        )
        stdout, stderr = _text(exc.output), _text(exc.stderr)
        record.update(stderr_tail=stderr[-2000:], stdout_tail=stdout[-2000:])
        _save(out, stem, stdout, stderr)
        return record
    record["exit_code"] = proc.returncode
    record["stderr_tail"] = proc.stderr[-2000:]
    _save(out, stem, proc.stdout, proc.stderr)
    try:
        parsed = _parse_json(proc.stdout)
        record["result"] = parsed
        record["composite"] = parsed.get("composite")
        layers = parsed.get("layers")
        record["layers"] = [layer.get("layer") for layer in layers if isinstance(layer, dict)] if isinstance(layers, list) else []
        record["model_usage"] = parsed.get("model_usage")
        if record["composite"] is None:
            record["parse_error"] = "JSON object has no composite score"
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
    manifest_path = Path(args.manifest).expanduser()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scratch = Path(manifest["paths"]["out"])
        plugin = Path(manifest["paths"]["plugin"])
        provider = manifest["provider"]
        out = scratch / "plugin-eval"
        out.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"cannot read prepare manifest {manifest_path}: {exc}") from exc

    results: list[dict[str, Any]] = []
    not_run: list[str] = []
    # complete stays false, with error/aborted_command set, unless every command was attempted.
    # not_run lists planned commands never started; it is empty if the run failed before planning.
    summary: dict[str, Any] = {
        "schema": "pact-bench/plugin-eval/1",
        "plugin_eval_dir": None,
        "plugin_eval_repo": None,
        "plugin_eval_commit": None,
        "plugin_eval_dirty": None,
        "temp_root": None,
        "complete": False,
        "error": None,
        "aborted_command": None,
        "sync": None,
        "results": results,
        "not_run": not_run,
    }
    current: dict[str, Any] | None = None
    cwd: Path | None = None
    try:
        with prepare._signals_kill_child():
            for stale in out.iterdir():
                if STALE_OUTPUT.fullmatch(stale.name) and (stale.is_file() or stale.is_symlink()):
                    stale.unlink()
            plugin_eval_dir = Path(args.plugin_eval_dir).expanduser().resolve()
            summary["plugin_eval_dir"] = str(plugin_eval_dir)
            if not (plugin_eval_dir / "pyproject.toml").is_file():
                raise SystemExit(f"not a PluginEval checkout: {plugin_eval_dir}")
            (
                summary["plugin_eval_repo"], summary["plugin_eval_commit"], summary["plugin_eval_dirty"]
            ) = _checkout_state(plugin_eval_dir)
            if args.expect_commit and (
                summary["plugin_eval_commit"] != args.expect_commit or summary["plugin_eval_dirty"] is not False
            ):
                raise SystemExit(
                    f"PluginEval checkout is at {summary['plugin_eval_commit']} "
                    f"(dirty={summary['plugin_eval_dirty']}), expected clean {args.expect_commit}"
                )
            skills = load_skills(Path(args.skills_file).expanduser() if args.skills_file else DEFAULT_SKILLS_FILE)
            settings = json.loads((scratch / "config" / "settings.json").read_text(encoding="utf-8"))
            env = prepare.build_child_env(
                dict(settings.get("env") or {}),
                scratch,
                Path(provider["token_file"]),
                str(provider["base_url"]),
            )
            # uv sync only installs packages; it never needs the provider token.
            sync_env = {key: value for key, value in env.items() if key != "ANTHROPIC_AUTH_TOKEN"}
            temp_root = prepare.check_clean_root(Path(args.temp_root))
            summary["temp_root"] = str(temp_root)
            temp_root.mkdir(parents=True, exist_ok=True)
            cwd = Path(tempfile.mkdtemp(prefix="pact-plugin-eval-", dir=str(temp_root)))
            sync, commands = build_commands(args.uv, plugin_eval_dir, plugin, skills)
            not_run.extend(label for label, _ in commands)

            if not args.skip_sync:
                current = {"label": "sync", "command": sync}
                summary["sync"] = dict(current)
                try:
                    proc = _exec(sync, cwd, sync_env, args.sync_timeout)
                except subprocess.TimeoutExpired as exc:
                    summary["sync"].update(timed_out=True, stderr_tail=_text(exc.stderr)[-2000:])
                    raise SystemExit(f"uv sync timed out after {args.sync_timeout}s") from exc
                summary["sync"].update(exit_code=proc.returncode, stderr_tail=proc.stderr[-2000:])
                (out / "sync.stdout.txt").write_text(proc.stdout, encoding="utf-8")
                (out / "sync.stderr.txt").write_text(proc.stderr, encoding="utf-8")
                if proc.returncode != 0:
                    raise SystemExit(f"uv sync failed: {proc.returncode}")
            for index, (label, cmd) in enumerate(commands):
                current = {"label": label, "command": cmd}
                not_run.remove(label)
                # The per-command boundary: an unexpected error fails only this record. SystemExit
                # (no uv binary, a signal) still aborts, because every later command would fail too.
                try:
                    results.append(_score_one(label, cmd, cwd, env, args.timeout, out, output_stem(index, label)))
                except Exception as exc:
                    results.append({**current, "exit_code": None, "error": f"{type(exc).__name__}: {exc}"})
            current = None
            summary["complete"] = True
    except BaseException as exc:
        summary["error"] = str(exc) if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        summary["aborted_command"] = current
        raise
    finally:
        if cwd is not None:
            try:
                shutil.rmtree(cwd)
            except OSError as exc:
                summary["cleanup_error"] = f"{cwd}: {exc}"
        (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    # Only the path: the record holds child output, which can contain the provider token.
    print(f"plugin-eval: complete={summary['complete']} record={out / 'result.json'}")
    ok = all(item.get("exit_code") == 0 and "parse_error" not in item for item in results)
    return 0 if ok and "cleanup_error" not in summary else 1


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(description="Run PluginEval on a prepared PACT copy.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--plugin-eval-dir", required=True)
    parser.add_argument("--skills-file", default=None, help="default bench/external/plugin-eval-skills.txt")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--timeout", type=_positive_int, default=600)
    parser.add_argument("--sync-timeout", type=_positive_int, default=600)
    parser.add_argument(
        "--expect-commit",
        default=None,
        help="refuse to run unless the git checkout holding the PluginEval dir is at this commit (full SHA) "
        "and the PluginEval dir is clean",
    )
    parser.add_argument(
        "--temp-root",
        default=str(prepare.DEFAULT_TEMP_ROOT),
        help="parent of the one fresh cwd the run's uv commands share (outside the repo, no memory file above it)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Outputs can hold child output, so they are created 0600/0700 (umask 077), restored after.
    with prepare.private_umask():
        return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
