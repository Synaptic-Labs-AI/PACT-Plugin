#!/usr/bin/env python3
"""Prepare an isolated PACT evaluation run.

WHAT THIS OWNS
  - Resolve the plugin under test from the current worktree, a git ref, or an
    explicit plugin directory, and copy it into a scratch directory through
    one pruning path (tests/, .git and caches dropped at every depth; the
    worktree copy holds only tracked and untracked-not-ignored files).
  - Build a scratch Claude Code settings.json from the live DeepSeek settings,
    dropping credential-shaped and live-session keys and overriding
    CLAUDE_CONFIG_DIR and additionalDirectories.
  - Build the child environment from an allowlist (see build_child_env).
  - Verify the provider with a trivial headless claude -p call and record the
    resolved model. The child's output tails go only to provider-check.json.
  - Write a manifest with provenance. The harness never writes the provider
    token itself, and manifest.json, stdout and its error messages carry no child
    output or child-reported string (the manifest's provider check is exit_code,
    is_error as a bool, resolved_model and cwd).
    provider-check.json and the run transcripts kept under --out do carry child
    output, which can contain the token, so keep --out under bench/.runs
    (gitignored) when it is inside the repo (a warning says so).

WHY
  The evaluation must not read or write the live ~/.claude-deepseek state, and
  every headless run must inherit the user's DeepSeek configuration without
  copying live PACT plugins, credentials or the PACT orchestrator agent into
  unrelated sessions.

USED BY
  bench/pact-bench (prepare), native_eval.py and plugin_eval.py (build_child_env,
  MODEL_ENV_KEYS, REPO_ROOT).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETTINGS = Path.home() / ".claude-deepseek" / "settings.json"
DEFAULT_TOKEN = Path.home() / ".claude-keys" / "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash[1m]"
MANIFEST_SCHEMA = "pact-bench/prepare/1"
OUT_MARKER = ".pact-bench-out"
# The env keys that the claude 2.1.332 binary's strings name as model ids; all
# are pinned to the prepared model so none of those roles keeps a live value.
MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL",
    "CLAUDE_CODE_AUTO_MODE_MODEL",
    "CLAUDE_CODE_BG_CLASSIFIER_MODEL",
)
# Live-session handles that are not credential-shaped but must never reach a child.
LIVE_ONLY_KEYS = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_SYNC_SESSION_REFS",
)
CREDENTIAL_KEY = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|HEADERS|CREDENTIAL", re.IGNORECASE)
# Token-count tuning keys (e.g. CLAUDE_CODE_MAX_OUTPUT_TOKENS, MAX_THINKING_TOKENS)
# match CREDENTIAL_KEY by name but hold numbers, so they are kept.
NON_SECRET_KEY = re.compile(r"^(CLAUDE_CODE_)?MAX_[A-Z0-9_]*TOKENS$")
# Live settings env names copied at all (an allowlist); anything else is dropped and recorded.
LIVE_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "DISABLE_", "ENABLE_", "MAX_", "MCP_")
# Exact names, not a BASH_ prefix: BASH_ENV and BASH_FUNC_* would make every child bash
# source a live file.
LIVE_ENV_NAMES = ("API_TIMEOUT_MS", "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "BASH_MAX_OUTPUT_LENGTH")
# A credential-shaped URL query parameter (?api_key=, &token=, &sig=, ...).
# Matched as a whole word inside the parameter name (api_key, X-Amz-Signature), so
# ordinary names like design= or monkey= are not caught.
URL_QUERY_CREDENTIAL = re.compile(
    r"[?&](?:[^=&#\s]*[_.-])?(?:api_?key|key|token|secret|sig|signature|password|auth)(?:[_.-][^=&#\s]*)?=",
    re.IGNORECASE,
)
# Names that point a child at live host state (a shell wrapper, plugin or settings
# location, proxy, endpoint, directory or certificate); dropped even with an allowed prefix.
HOST_STATE_NAME = re.compile(r"SHELL|PLUGIN|SETTINGS|PROXY|COMMAND|SCRIPT|CERT|CREDS|BASE_URL|DIRECTOR|_DIRS?$|_PATH$|TMPDIR")
URL_USERINFO = re.compile(r"://[^/\s@]+@")
# The only live-environment keys a child inherits; everything else comes from
# the scratch settings env or is set explicitly below.
INHERITED_ENV_KEYS = ("PATH", "LANG", "TERM")
INHERITED_ENV_PREFIXES = ("LC_",)
EXCLUDED_NAMES = {"tests", "__pycache__", ".git", ".pytest_cache"}
SCRATCH_DIRS = ("config", "home", "tmp", "teams", "tasks", "pact-sessions")
RUNS_DIR = REPO_ROOT / "bench" / ".runs"
# Child claude processes run under this root; it must hold no memory file in it or its parents.
DEFAULT_TEMP_ROOT = Path("/tmp")
MEMORY_FILE_NAMES = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
MEMORY_RULES_DIR = ".claude/rules"
# Project settings Claude Code may read from a cwd or its parents (hooks, permissions).
PROJECT_SETTINGS_NAMES = (".claude/settings.json", ".claude/settings.local.json")
FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
KILL_DRAIN_SECONDS = 10
# provider-check.json fields copied into manifest.json: ours or derived, never a child string
# (is_error is normalised to a bool when copied).
CHECK_SUMMARY_KEYS = ("exit_code", "is_error", "resolved_model", "cwd")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# core.excludesFile=/dev/null also turns off git's default per-user ignore file
# ($XDG_CONFIG_HOME/git/ignore or ~/.config/git/ignore), which config isolation leaves on.
GIT = ["git", "-c", f"core.excludesFile={os.devnull}"]


def _git_env() -> dict[str, str]:
    """The host environment minus inherited GIT_* (so GIT_DIR and friends cannot point
    git at another repo), plus GIT_CONFIG_GLOBAL=/dev/null and GIT_CONFIG_NOSYSTEM=1
    as defence in depth. The host settings known to change what git reports here are
    neutralised explicitly instead: core.excludesFile by GIT's -c, and
    status.showUntrackedFiles by status --untracked-files=all."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            [*GIT, "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}: {exc.stderr.strip()}") from exc
    return proc.stdout


class SignalExit(SystemExit):
    """SIGTERM/SIGHUP received while a child ran; exit status 128+signum."""

    def __init__(self, signum: int) -> None:
        super().__init__(128 + signum)
        self.signum = signum

    def __str__(self) -> str:
        return f"terminated by {signal.Signals(self.signum).name}"


def _raise_signal_exit(signum: int, _frame: Any) -> None:
    raise SignalExit(signum)


@contextmanager
def _signals_kill_child() -> Iterator[None]:
    """Route SIGTERM/SIGHUP into an exception for the child's lifetime, so the
    group-kill path runs; the previous handlers are restored afterwards."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {signum: signal.signal(signum, _raise_signal_exit) for signum in FORWARDED_SIGNALS}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def _as_text(data: Any, text: bool) -> Any:
    return data.decode(errors="replace") if text and isinstance(data, bytes) else data


def _drain_after_kill(proc: subprocess.Popen, text: bool) -> tuple[Any, Any, bool]:
    """Collect the killed child's remaining output, waiting at most KILL_DRAIN_SECONDS.

    A process that left the group can hold the pipes open; then the pipes are
    closed, the output is partial and complete is False.
    """
    try:
        stdout, stderr = proc.communicate(timeout=KILL_DRAIN_SECONDS)
        return stdout, stderr, True
    except subprocess.TimeoutExpired as exc:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.wait()
        print(
            f"warning: {proc.args[0]} was killed but its output pipes stayed open for "
            f"{KILL_DRAIN_SECONDS}s (a process outside its group holds them); output is incomplete",
            file=sys.stderr,
        )
        return _as_text(exc.stdout, text), _as_text(exc.stderr, text), False


def run_process_group(
    cmd: list[str], timeout: int, **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """subprocess.run, but the child leads its own session and its whole process
    group is killed when the call ends: after a normal exit (any leftover in the
    group), on a timeout, on a SIGTERM/SIGHUP to this process, or on a
    KeyboardInterrupt. A grandchild that starts its own session escapes the kill.

    On timeout it raises subprocess.TimeoutExpired carrying all the output
    drained after the kill (stdout/stderr), output_complete (False when the
    bounded drain expired) and child_exit_code (the child's own exit code if it
    had already exited, e.g. while a leftover process held its pipe; else None).
    A signal raises SignalExit after the kill.
    """
    text = bool(kwargs.get("text") or kwargs.get("universal_newlines"))
    if text:
        # Undecodable bytes in a child's output must not turn its result into an error.
        kwargs.setdefault("errors", "replace")
    with _signals_kill_child():
        # shortcut: a signal landing inside Popen itself, before proc exists,
        # skips the group kill; blocking signals around Popen would leak the mask into the child.
        proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException as exc:
            child_exit_code = proc.poll()  # set when only a leftover holds the pipe
            _kill_group(proc.pid)
            drained_out, drained_err, complete = _drain_after_kill(proc, text)
            if isinstance(exc, subprocess.TimeoutExpired):
                exc.stdout, exc.stderr = drained_out, drained_err
                exc.output_complete = complete
                exc.child_exit_code = child_exit_code
            raise
        _kill_group(proc.pid)  # leftovers in the group must not outlive a normal exit
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _holds_files(directory: Path) -> bool:
    """True when directory holds any file at any depth; an unreadable directory or a
    symlinked subdirectory (not followed) counts as holding files."""
    def fail(exc: OSError) -> None:
        raise exc

    try:
        # os.walk, not Path.rglob: rglob silently skips a directory it cannot read.
        for root, dirs, files in os.walk(directory, onerror=fail):
            # A symlinked subdirectory is not walked, so it counts as holding files.
            if files or any(os.path.islink(os.path.join(root, name)) for name in dirs):
                return True
        return False
    except OSError:
        return True


def memory_files_above(path: Path) -> list[Path]:
    """The MEMORY_FILE_NAMES and PROJECT_SETTINGS_NAMES files, and any MEMORY_RULES_DIR
    holding files, in path or any of its ancestors."""
    found: list[Path] = []
    for directory in (path, *path.parents):
        found.extend(
            directory / name for name in (*MEMORY_FILE_NAMES, *PROJECT_SETTINGS_NAMES) if (directory / name).is_file()
        )
        rules = directory / MEMORY_RULES_DIR
        if rules.is_dir() and _holds_files(rules):
            found.append(rules)
    return found


def check_clean_root(root: Path) -> Path:
    """Return root's real path (symlinks resolved, as the child will see its cwd),
    refusing one inside the repo, one that exists but is not a directory, or one
    below a memory file, project settings file or non-empty .claude/rules directory
    (memory_files_above)."""
    root = root.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise SystemExit(f"--temp-root {root} is not a directory")
    if root.is_relative_to(REPO_ROOT):
        raise SystemExit(f"--temp-root {root} is inside the repository; choose one outside it, e.g. /tmp")
    found = memory_files_above(root)
    if found:
        raise SystemExit(
            f"--temp-root {root} has Claude Code memory files or project settings in it or its parents, which every "
            f"child session would load: {[str(path) for path in found]}; choose a --temp-root without them"
        )
    return root


def keeps_live_env(key: str, value: Any) -> bool:
    """True when a settings env entry may reach scratch settings.json or a child: its
    name is in LIVE_ENV_NAMES or has a LIVE_ENV_PREFIXES prefix, is not
    credential-shaped (is_scratch_env_key) or host-state-shaped (HOST_STATE_NAME),
    and its value is not a host path (starting with / or ~) and carries no URL
    userinfo (user:pass@) and no credential-shaped URL query parameter."""
    text = str(value)
    return (
        (key in LIVE_ENV_NAMES or key.startswith(LIVE_ENV_PREFIXES))
        and is_scratch_env_key(key)
        and not HOST_STATE_NAME.search(key)
        and not text.startswith(("/", "~"))
        and not URL_USERINFO.search(text)
        and not URL_QUERY_CREDENTIAL.search(text)
    )


def is_scratch_env_key(key: str) -> bool:
    """True when a settings env key may be copied into scratch settings."""
    if key in LIVE_ONLY_KEYS:
        return False
    return bool(NON_SECRET_KEY.match(key)) or not CREDENTIAL_KEY.search(key)


def _allowed_paths(files: list[str]) -> set[str]:
    """Every listed file plus each of its parent directories, as relative posix paths."""
    allowed: set[str] = set()
    for name in files:
        path = Path(name)
        allowed.add(path.as_posix())
        allowed.update(parent.as_posix() for parent in path.parents if parent != Path("."))
    return allowed


def copy_plugin_tree(src: Path, dest: Path, only: set[str] | None = None) -> None:
    """Copy src to dest, dropping EXCLUDED_NAMES at every depth. Symlinks are
    copied as links; one that resolves outside src is refused.

    When only is given, it lists the relative paths (files and their parent
    dirs) allowed into the copy; anything else is skipped.
    """

    def ignore(directory: str, names: list[str]) -> set[str]:
        rel = Path(directory).relative_to(src)
        return {
            name
            for name in names
            if name in EXCLUDED_NAMES
            or (only is not None and (rel / name).as_posix() not in only)
        }

    _refuse_outside_links(src, ignore)
    if dest.exists():
        shutil.rmtree(dest)
    # Links are kept as links. Only relative in-tree links get here, which is also what the
    # git-archive copy's tarfile 'data' filter allows, so both copy paths agree.
    shutil.copytree(src, dest, ignore=ignore, symlinks=True)


def _refuse_outside_links(src: Path, ignore: Any) -> None:
    """SystemExit if any symlink that will be copied is absolute (in the copy it would
    point back at the source) or resolves outside src."""
    real = src.resolve()
    for directory, dirs, files in os.walk(src):
        skipped = ignore(directory, dirs + files)
        dirs[:] = [name for name in dirs if name not in skipped]
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if name in skipped or not path.is_symlink():
                continue
            target = os.readlink(path)
            if os.path.isabs(target):
                raise SystemExit(f"link {path} is absolute ({target}); use a relative link")
            if not path.resolve().is_relative_to(real):
                raise SystemExit(f"link {path} points outside {src}: {target}")


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
    except (SystemExit, json.JSONDecodeError):
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
        sha = _git(repo, "rev-parse", "HEAD").strip()
        tree = _git(repo, "rev-parse", "HEAD:pact-plugin").strip()
        status = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", "pact-plugin")
        return {
            "source": "current-worktree",
            "sha": sha,
            "tree_sha": tree,
            "version": plugin_version(repo, sha, None),
            "dirty_paths": [line for line in status.splitlines() if line],
        }
    sha = _git(repo, "rev-parse", f"{ref}^{{commit}}").strip()
    tree = _git(repo, "rev-parse", f"{sha}:pact-plugin").strip()
    return {
        "source": ref,
        "sha": sha,
        "tree_sha": tree,
        "version": plugin_version(repo, sha, None),
        "dirty_paths": [],
    }


def check_source(
    repo: Path,
    ref: str | None,
    plugin_dir: Path | None,
    allow_dirty: bool,
) -> dict[str, Any]:
    """Resolve provenance and refuse a dirty tree, before anything is written."""
    if plugin_dir is not None and not plugin_dir.is_dir():
        raise SystemExit(f"--plugin-dir is not a directory: {plugin_dir}")
    prov = provenance(repo, ref, plugin_dir)
    if prov["dirty_paths"] and not allow_dirty:
        raise SystemExit(
            "refusing to evaluate a dirty pact-plugin tree; commit or pass --allow-dirty; "
            f"dirty: {prov['dirty_paths'][:5]}"
        )
    return prov


def materialize_plugin(
    repo: Path,
    out: Path,
    ref: str | None,
    plugin_dir: Path | None,
    prov: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Copy the plugin under test to out/pact-plugin through copy_plugin_tree."""
    dest = out / "pact-plugin"
    if plugin_dir is not None:
        copy_plugin_tree(plugin_dir, dest)
    elif ref is None:
        listed = _git(
            repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "pact-plugin"
        )
        files = [Path(name).relative_to("pact-plugin").as_posix() for name in listed.split("\0") if name]
        copy_plugin_tree(repo / "pact-plugin", dest, only=_allowed_paths(files))
    else:
        staging = out / ".plugin-src"
        staging.mkdir()
        try:
            archive = staging / "plugin.tar"
            with archive.open("wb") as fh:
                proc = subprocess.run(
                    [*GIT, "-C", str(repo), "archive", "--format=tar", prov["sha"], "pact-plugin"],
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=_git_env(),
                )
            if proc.returncode != 0:
                raise SystemExit(f"git archive {prov['sha']} pact-plugin failed in {repo}: {proc.stderr.strip()}")
            try:
                with tarfile.open(archive) as tf:
                    tf.extractall(staging, filter="data")
            except tarfile.TarError as exc:
                raise SystemExit(f"plugin source at {prov['sha']} cannot be extracted safely: {exc}") from exc
            copy_plugin_tree(staging / "pact-plugin", dest)
        finally:
            shutil.rmtree(staging)
    prov["path"] = str(dest)
    return dest, prov


def build_scratch_settings(
    source: Path,
    config_dir: Path,
    scratch: Path,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Write scratch settings.json. The model keys and ANTHROPIC_BASE_URL are
    written into its env too, so they agree with the child env (settings env
    can take precedence over the process env inside claude).
    """
    data = load_settings(source)
    env = {
        key: value
        for key, value in (data.get("env") or {}).items()
        if keeps_live_env(key, value)
    }
    for key in MODEL_ENV_KEYS:
        env[key] = model
    env["ANTHROPIC_BASE_URL"] = base_url
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    settings = {
        "env": env,
        "teammateMode": data.get("teammateMode", "in-process"),
        "permissions": {
            "additionalDirectories": [
                str(scratch / "teams"),
                str(scratch / "tasks"),
                str(scratch / "pact-sessions"),
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
    """Child environment: an allowlist of live keys, the scratch settings env (filtered
    again with keeps_live_env, since the child can rewrite its own settings.json),
    scratch HOME/TMPDIR/CLAUDE_CONFIG_DIR and the provider token. Nothing else
    from the live environment (CLAUDE*, GH_TOKEN, SSH_*, XDG_*, AWS_*, ...) passes.
    The isolation is environment-only: a child's file tools can still open
    absolute host paths.
    """
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"empty provider token file: {token_file}")
    env = {
        key: value
        for key, value in os.environ.items()
        if key in INHERITED_ENV_KEYS or key.startswith(INHERITED_ENV_PREFIXES)
    }
    env.update({key: env_text(value) for key, value in settings_env.items() if keeps_live_env(key, value)})
    env["CLAUDE_CONFIG_DIR"] = str(scratch / "config")
    env["HOME"] = str(scratch / "home")
    env["TMPDIR"] = str(scratch / "tmp")
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = token
    for name in ("home", "tmp", "teams", "tasks", "pact-sessions"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    return env


def _parse_claude_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("empty claude output")
    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object in claude output")


def model_base(model: str) -> str:
    """The model alias without a trailing [..] context suffix."""
    return re.sub(r"\[[^\]]*\]$", "", model)


def _write_check(out: Path, record: dict[str, Any]) -> None:
    (out / "provider-check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def verify_provider(
    scratch: Path,
    out: Path,
    claude_bin: str,
    env: dict[str, str],
    timeout: int = 120,
    model: str = DEFAULT_MODEL,
    temp_root: Path = DEFAULT_TEMP_ROOT,
) -> dict[str, Any]:
    """Run a trivial headless call from a fresh cwd under temp_root (refused by
    check_clean_root unless clean); write provider-check.json on every outcome."""
    record: dict[str, Any] = {"checked_at": utc_now(), "requested_model": model, "cwd": None}
    try:
        temp_root = check_clean_root(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)
        cwd = Path(tempfile.mkdtemp(prefix="pact-verify-", dir=str(temp_root)))
        record["cwd"] = str(cwd)
        try:
            proc = run_process_group(
                [claude_bin, "-p", "Reply with exactly OK", "--output-format", "json", "--max-turns", "1"],
                timeout,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
    except FileNotFoundError as exc:
        missing = exc.filename if exc.filename is not None else claude_bin
        record["error"] = (
            f"claude binary not found: {claude_bin}" if str(missing) == str(claude_bin) else f"file not found: {missing}"
        )
        _write_check(out, record)
        raise SystemExit(f"provider verification failed: {record['error']}") from exc
    except subprocess.TimeoutExpired as exc:
        record["error"] = f"claude did not answer within {timeout}s"
        record.update(stdout_tail=(exc.stdout or "")[-2000:], stderr=(exc.stderr or "")[-2000:])
        record["output_complete"] = getattr(exc, "output_complete", True)
        # Set when claude had already exited and only a leftover process held its pipe.
        record["child_exit_code"] = getattr(exc, "child_exit_code", None)
        _write_check(out, record)
        raise SystemExit(f"provider verification failed: {record['error']}") from exc
    except BaseException as exc:
        record["error"] = str(exc) if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        _write_check(out, record)
        raise
    record.update(
        exit_code=proc.returncode,
        stderr=proc.stderr[-2000:],
        stdout_tail=proc.stdout[-2000:],
    )
    try:
        parsed = _parse_claude_json(proc.stdout)
    except ValueError as exc:
        record["error"] = str(exc)
        _write_check(out, record)
        raise SystemExit(f"provider verification failed: {exc}") from exc
    usage = parsed.get("modelUsage") or {}
    record["result"] = parsed.get("result")
    record["model_usage"] = usage
    record["model_keys"] = sorted(str(key) for key in usage)
    record["is_error"] = parsed.get("is_error")
    record["num_turns"] = parsed.get("num_turns")
    _write_check(out, record)
    # Messages hold only our own text: what the child reported stays in provider-check.json.
    if proc.returncode != 0 or parsed.get("is_error"):
        raise SystemExit(
            f"provider verification exited {proc.returncode} (is_error={bool(parsed.get('is_error'))}); "
            f"see {out / 'provider-check.json'}"
        )
    wanted = model_base(model)
    # The base names must match exactly: deepseek-flash-pro is not deepseek-flash.
    record["resolved_model"] = any(model_base(str(key)) == wanted for key in usage)
    _write_check(out, record)
    if not record["resolved_model"]:
        raise SystemExit(f"provider verification did not resolve {wanted}; see {out / 'provider-check.json'}")
    return record


def check_force_target(out: Path) -> None:
    """Refuse to delete anything that is not a previous pact-bench output dir."""
    home = Path.home().resolve()
    if out == Path(out.anchor) or out == home or REPO_ROOT.is_relative_to(out):
        raise SystemExit(f"refusing --force on {out}: it is /, $HOME, or the repo root or one of its ancestors")
    if (out / OUT_MARKER).is_file():
        return
    manifest = out / "manifest.json"
    try:
        schema = json.loads(manifest.read_text(encoding="utf-8")).get("schema")
    except (OSError, ValueError, AttributeError):
        schema = None
    if schema != MANIFEST_SCHEMA:
        raise SystemExit(f"refusing --force on {out}: it holds no previous pact-bench prepare output")


def resolve_claude_bin(claude_bin: str | None) -> str:
    resolved = shutil.which(claude_bin or "claude")
    if resolved is None:
        raise SystemExit(f"claude binary not found: {claude_bin or 'claude (PATH)'}")
    return resolved


def load_settings(source: Path) -> dict[str, Any]:
    """Parse the live settings; SystemExit unless it is a JSON object whose env is an object."""
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"--settings-source is not readable JSON: {source}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("env", {}), (dict, type(None))):
        raise SystemExit(f"--settings-source must be a JSON object with an object env: {source}")
    bad = sorted(
        key for key, value in (data.get("env") or {}).items() if not isinstance(value, (str, int, float, bool))
    )
    if bad:
        raise SystemExit(f"--settings-source env values must be strings, numbers or booleans: {bad}")
    return data


def env_text(value: Any) -> str:
    """An env value as the text a child process gets (numbers and booleans as JSON)."""
    return value if isinstance(value, str) else json.dumps(value)


def out_location_warning(out: Path) -> str | None:
    """A warning when out is inside the repo but outside bench/.runs, where kept
    transcripts (which can hold the provider token) could be committed."""
    if out.is_relative_to(REPO_ROOT) and not out.is_relative_to(RUNS_DIR):
        return (
            f"warning: --out {out} is inside the repository but outside {RUNS_DIR}; "
            "run transcripts kept there can hold the provider token and are not gitignored"
        )
    return None


def check_inputs(settings_source: Path, token_file: Path) -> None:
    if not settings_source.is_file():
        raise SystemExit(f"--settings-source is not a file: {settings_source}")
    load_settings(settings_source)
    if not token_file.is_file():
        raise SystemExit(f"--token-file is not a file: {token_file}")
    if not token_file.read_text(encoding="utf-8").strip():
        raise SystemExit(f"empty provider token file: {token_file}")


def prepare(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve()
    warning = out_location_warning(out)
    if warning:
        print(warning, file=sys.stderr)
    if out.exists() and not out.is_dir():
        raise SystemExit(f"--out {out} exists and is not a directory")
    replace = out.exists() and any(out.iterdir())
    if replace and not args.force:
        raise SystemExit(f"output directory is not empty: {out}; pass --force to replace")
    if replace:
        check_force_target(out)
    settings_source = Path(args.settings_source).expanduser()
    token_file = Path(args.token_file).expanduser()
    plugin_dir = Path(args.plugin_dir).expanduser().resolve() if args.plugin_dir else None
    check_inputs(settings_source, token_file)
    prov = check_source(REPO_ROOT, args.plugin_ref, plugin_dir, args.allow_dirty)
    claude_bin = resolve_claude_bin(args.claude_bin) if args.verify else None
    temp_root = Path(args.temp_root)
    if claude_bin is not None:
        temp_root = check_clean_root(temp_root)

    if replace:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / OUT_MARKER).write_text(MANIFEST_SCHEMA + "\n", encoding="utf-8")
    for name in SCRATCH_DIRS:
        (out / name).mkdir(parents=True, exist_ok=True)

    plugin_path, prov = materialize_plugin(REPO_ROOT, out, args.plugin_ref, plugin_dir, prov)
    settings, env = build_scratch_settings(settings_source, out / "config", out, args.model, args.base_url)
    child_env = build_child_env(env, out, token_file, args.base_url)
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "created_at": utc_now(),
        "plugin": prov,
        "settings": {
            "source": str(settings_source),
            "sha256": sha256_file(settings_source),
            "path": str(out / "config" / "settings.json"),
            "teammate_mode": settings.get("teammateMode"),
            "env_keys": sorted(env),
            "dropped_env_keys": sorted(
                key
                for key, value in (load_settings(settings_source).get("env") or {}).items()
                if not keeps_live_env(key, value)
            ),
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
            **{name.replace("-", "_"): str(out / name) for name in SCRATCH_DIRS},
        },
    }
    if claude_bin is not None:
        check = verify_provider(out, out, claude_bin, child_env, model=args.model, temp_root=temp_root)
        # Summary fields only: the child's output tails stay in provider-check.json.
        manifest["provider"]["check"] = {
            "path": str(out / "provider-check.json"),
            **{key: check.get(key) for key in CHECK_SUMMARY_KEYS},
        }
        manifest["provider"]["check"]["is_error"] = bool(check.get("is_error"))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


def positive_int(text: str) -> int:
    """argparse type: an integer >= 1."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare an isolated PACT evaluation run.")
    parser.add_argument(
        "--out",
        required=True,
        help="scratch output directory; inside the repo, keep it under bench/.runs (gitignored)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--plugin-ref", default=None, help="git tag/SHA to evaluate; default current worktree")
    source.add_argument("--plugin-dir", default=None, help="explicit plugin directory to copy")
    parser.add_argument("--settings-source", default=str(DEFAULT_SETTINGS), help="live DeepSeek settings.json")
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN), help="DeepSeek token file")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DeepSeek Anthropic-compatible endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="requested model alias")
    parser.add_argument("--allow-dirty", action="store_true", help="allow a dirty pact-plugin tree")
    parser.add_argument("--verify", action="store_true", help="run a trivial headless provider check")
    parser.add_argument("--claude-bin", default=None, help="claude binary (default: PATH)")
    parser.add_argument(
        "--temp-root",
        default=str(DEFAULT_TEMP_ROOT),
        help="parent of the --verify call's cwd; must be outside the repo with no Claude Code memory "
        "or project-settings file (CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md, .claude/rules/*, "
        ".claude/settings*.json) in it or its parents",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace a non-empty output directory that holds a previous pact-bench prepare output",
    )
    return parser


@contextmanager
def private_umask() -> Iterator[None]:
    """Create files 0600 and dirs 0700 for the duration (outputs can hold the token),
    then restore the caller's umask."""
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def main(argv: list[str] | None = None) -> int:
    with private_umask():
        return prepare(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
