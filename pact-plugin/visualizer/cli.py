"""Location: pact-plugin/visualizer/cli.py
Summary: The ``pact-viz`` command line: argument parsing, root resolution,
         foreground and detached serving, the on-disk server state file
         (atomic, 0600 in a 0700 directory), the per-launch key and the URL
         that carries it, key-authenticated --stop/--status and reuse, the
         plain-text --list, and the Python >= 3.12 floor.
Used by: visualizer.__main__ (python -m visualizer), pact-plugin/bin/pact-viz
         (exec'd wrapper), tests.

One server runs per state directory and serves every session of its roots;
which run a page opens is chosen by the URL (?demo=1, ?project=, ?session=),
so every launch can reuse a running server. Only two blocking operations live
here: starting/stopping the detached child and the wait-for-ready health poll.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import http.client
import json
import os
import re
import secrets
import shlex
import signal
import socket
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import model, settings
from . import server as server_module
from .settings import (
    APP_LABEL,
    KEY_ENV,
    KEY_HEADER,
    LAUNCHER_ENV,
    LOOPBACK_HOST,
    MIN_PYTHON,
    PLUGIN_ROOT,
    VERSION,
    RootSpec,
)

# Internal env var carrying the parent-generated instance token to the detached
# child, so the parent can health-check THE instance it just spawned and never
# mistake a stale state file or a foreign server for its own. Not a user flag.
_INSTANCE_ENV = "PACT_VIZ_INSTANCE_ID"

_LOG_FILENAME = "server.log"
_LAUNCH_FILENAME = "launch.html"
_LOCK_FILENAME = ".launch.lock"
_BROWSER_WAIT_S = 3.0
_READY_TIMEOUT_S = 10.0
_STOP_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05


class CliError(Exception):
    """A user-facing CLI failure (bad state directory, unkillable pid, ...)."""


def _python_version_text() -> str:
    """The running interpreter version as 'major.minor.patch'."""
    return "{}.{}.{}".format(*sys.version_info[:3])


def build_parser() -> argparse.ArgumentParser:
    """The pact-viz argument parser.

    Location: cli.build_parser.
    Summary: Defines every flag in the contract. There is deliberately no
             --host/--allow-remote: the bind address is the hard loopback
             literal in settings/server. --demo, --project, --project-dir and
             --session select what the printed URL opens; they never change
             what the server serves.
    Used by: main, tests.
    """
    parser = argparse.ArgumentParser(
        prog="pact-viz",
        description=(
            "PACT Mission Control: a local, read-only visualizer for PACT "
            "session journals. Binds 127.0.0.1 only."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pact-viz {VERSION}",
        help="print the version and exit",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        metavar="PORT",
        help="loopback port (0 = pick an ephemeral port; default: 0)",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        metavar="PATH",
        help="config root to read (repeatable; default: $CLAUDE_CONFIG_DIR or ~/.claude)",
    )
    parser.add_argument(
        "--all-roots",
        action="store_true",
        help="also read sibling ~/.claude* config roots that contain pact-sessions/",
    )
    parser.add_argument(
        "--project",
        metavar="LABEL",
        help="open this project's newest run ([A-Za-z0-9._-]; with --list: filter)",
    )
    parser.add_argument(
        "--project-dir",
        metavar="PATH",
        help="open the newest run of the project at PATH (its on-disk slug)",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="open this session (id or a prefix of 8+ characters; with --list: filter)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="open the training simulation (?demo=1)",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="open the default browser at the printed URL (never suppresses URL:)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="start the server in the background and exit once it is healthy",
    )
    parser.add_argument("--stop", action="store_true", help="stop the running detached server")
    parser.add_argument(
        "--status", action="store_true", help="report whether a detached server is running"
    )
    parser.add_argument(
        "--list", action="store_true", help="print the discovered sessions and exit"
    )
    parser.add_argument(
        "--state-dir",
        metavar="DIR",
        default=None,
        help="override the state directory (default: <config>/pact-viz)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log HTTP requests to stderr (detached: <state-dir>/server.log)",
    )
    return parser


def project_slug(project_dir: str) -> str:
    """The hooks' on-disk slug for a project directory ("" when none).

    Location: cli.project_slug.
    Summary: The resolved directory's basename with every run of characters
             outside ``[A-Za-z0-9_-]`` collapsed to ``_`` — the rule the hooks
             use when they name ``pact-sessions/<slug>/``.
    Used by: main (--project-dir).
    """
    try:
        name = Path(project_dir).resolve().name
    except (OSError, RuntimeError):
        name = Path(project_dir).name
    return settings.SLUG_UNSAFE_RE.sub("_", name) if name else ""


def _state_dir(args: argparse.Namespace) -> Path:
    """Resolve the state directory: --state-dir or <config>/pact-viz."""
    override = getattr(args, "state_dir", None)
    if override:
        return Path(os.path.abspath(os.path.expanduser(str(override))))
    return settings.default_state_dir()


def prepare_state_dir(state_dir: Path) -> None:
    """Create the state directory 0700, or accept an existing one as it is.

    Location: cli.prepare_state_dir.
    Summary: A directory this call creates is made 0700. An existing
             directory is never chmod-ed — ``--state-dir ~`` must not change
             the home directory — but it must be a real directory (not a
             symlink) owned by this user and not world-writable; a
             group-writable one gets a one-line warning. The state file itself
             is always 0600.
    Used by: write_state, detach.
    """
    try:
        state_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            state_dir.mkdir(mode=0o700)
            os.chmod(state_dir, 0o700)
            return
        except FileExistsError:
            pass
        info = os.lstat(state_dir)
    except OSError as error:
        raise CliError(f"cannot create state directory {state_dir}: {error}") from error
    if stat_module.S_ISLNK(info.st_mode) or not stat_module.S_ISDIR(info.st_mode):
        raise CliError(f"state directory {state_dir} is not a real directory")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise CliError(f"state directory {state_dir} is not owned by this user")
    mode = oct(stat_module.S_IMODE(info.st_mode))
    advice = f"run chmod go-w {shlex.quote(str(state_dir))}"
    if info.st_mode & 0o002:
        # Any local user could plant launch.html, server.json or the lock.
        raise CliError(f"state directory {state_dir} is world-writable (mode {mode}); {advice}")
    if info.st_mode & 0o020:
        # Group write is the umask-002 default for a user-private group, so it
        # is not refused; the exclusive opens already close the keyed-URL race.
        print(
            f"pact-viz: warning: state directory {state_dir} is group-writable "
            f"(mode {mode}); {advice}",
            file=sys.stderr,
        )


def read_state(state_dir: Path) -> dict | None:
    """Read and validate the server state file, or None.

    Location: cli.read_state.
    Summary: Returns the parsed object only when it has the required types
             (pid/port ints, url/instance_id/key strings); a truncated, hostile
             or older-format file reads as "no state", never as a partial
             running server.
    Used by: cmd_status, cmd_stop, detach, serve_foreground, tests.
    """
    path = settings.state_file(state_dir)
    try:
        if not path.is_file() or path.is_symlink():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    port = payload.get("port")
    pid = payload.get("pid")
    if isinstance(port, bool) or isinstance(pid, bool):
        return None
    if not isinstance(port, int) or not isinstance(pid, int):
        return None
    if not (0 < port <= 65535) or pid <= 0:
        return None
    for name in ("instance_id", "url", "key"):
        if not isinstance(payload.get(name), str) or not payload.get(name):
            return None
    return payload


def write_state(state_dir: Path, payload: dict) -> Path:
    """Atomically write the server state file (dir 0700, file 0600).

    Location: cli.write_state.
    Summary: The directory is prepared by prepare_state_dir, the JSON is
             written to a temp file in it with mode 0600, then ``os.replace``
             makes it visible atomically. Partial files are removed on
             failure.
    Used by: serve_foreground, tests.
    """
    prepare_state_dir(state_dir)
    target = settings.state_file(state_dir)
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".server-", suffix=".json.tmp", dir=str(state_dir)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        temporary = ""
        return target
    except OSError as error:
        raise CliError(f"cannot write state file {target}: {error}") from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def clear_state(state_dir: Path) -> None:
    """Remove the state file (and any stale temp files); never raises."""
    for pattern in ("server.json", ".server-*.json.tmp"):
        try:
            for path in state_dir.glob(pattern):
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _clear_state_if_instance(state_dir: Path, instance_id: str) -> bool:
    """Clear the state file only when it still names ``instance_id``.

    Location: cli._clear_state_if_instance.
    Summary: A concurrent ``--detach`` that overwrote the state file between
             the read and the clear must not lose ITS state file, or its
             server runs on with no record to stop it. Returns True when the
             state dir was cleared (ours or already empty) — the caller then
             also removes the detached server's log and launch residue.
    Used by: cmd_stop, detach, serve_foreground.
    """
    current = read_state(state_dir)
    if current is None or current.get("instance_id") == instance_id:
        clear_state(state_dir)
        return True
    return False


def _remove_residue(state_dir: Path) -> None:
    """Remove the detached server's log and the launch page; never raises.

    After a stop both are residue: the writer has exited, and even in the
    unconfirmed-exit case an unlink only orphans the child's open fd. A later
    launch recreates them.
    """
    for name in (_LOG_FILENAME, _LAUNCH_FILENAME):
        try:
            (state_dir / name).unlink(missing_ok=True)
        except OSError:
            pass


def pid_start_time(pid: int) -> str | None:
    """The kernel start time of ``pid`` (Linux /proc stat field 22), or None.

    Location: cli.pid_start_time.
    Summary: Together with the pid it names one process for its whole life,
             so a pid the OS reused after the server exited never matches.
             None when /proc is unavailable (not Linux) or the pid is gone.
    Used by: serve_foreground (recorded), _our_instance (compared).
    """
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    # The command name (field 2) may contain spaces and parentheses; every
    # field after it follows the LAST ')'. Field 22 is index 19 from there.
    tail = text.rsplit(")", 1)[-1].split()
    return tail[19] if len(tail) > 19 else None


def health_check(port: int, key: str, timeout: float = 1.0) -> dict | None:
    """GET /api/health on loopback with the launch key; the payload or None.

    Location: cli.health_check.
    Summary: Uses an opener with proxies disabled so a machine-wide HTTP proxy
             can never route a loopback check off-box. Short timeout; any
             failure (including the 401 an unkeyed or wrong-key request gets)
             reads as "not healthy".
    Used by: _our_instance, tests.
    """
    if not isinstance(port, int) or not (0 < port <= 65535):
        return None
    url = f"http://{LOOPBACK_HOST}:{port}/api/health"
    request = urllib.request.Request(url, headers={KEY_HEADER: str(key)})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError, urllib.error.URLError, http.client.HTTPException,
        json.JSONDecodeError, ValueError,
    ):
        # A non-HTTP service now holding the recorded port (BadStatusLine)
        # or a truncated answer (IncompleteRead) is "not our server".
        return None
    if isinstance(payload, dict) and payload.get("ok"):
        return payload
    return None


def _our_instance(state: dict, timeout: float = 1.0) -> dict | None:
    """The health payload when the state file's server answers as itself.

    Location: cli._our_instance.
    Summary: Key-authenticated health must report the recorded instance_id
             AND pid, and, when a start time was recorded, the live process
             must still have that start time. Anything else — a 401, another
             instance, a reused pid — reads as "not our instance".
    Used by: cmd_status, cmd_stop, _reuse_if_running, detach.
    """
    health = health_check(int(state["port"]), str(state["key"]), timeout=timeout)
    if health is None:
        return None
    if health.get("instance_id") != state.get("instance_id"):
        return None
    if not isinstance(health.get("pid"), int) or health.get("pid") != state.get("pid"):
        return None
    recorded_start = state.get("pid_start")
    if isinstance(recorded_start, str) and recorded_start:
        if pid_start_time(int(state["pid"])) != recorded_start:
            return None
    # With no recorded start time (not Linux) the instance_id + pid match
    # above is the whole identity check.
    return health


def _instance_gone(state: dict) -> bool:
    """True once the recorded instance no longer answers as itself."""
    return _our_instance(state, timeout=0.25) is None


def _wait_until_stopped(state: dict, timeout: float) -> bool:
    """Poll until the instance stops answering; True when it did.

    A disappeared pid is not enough: the recorded pid can name an unrelated
    process, so success means THIS instance's health no longer answers.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _instance_gone(state):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def _pid_gone(pid: int) -> bool:
    """True when a pid no longer exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return True
    return False


def _port_accepts(port: int) -> bool:
    """True when something accepts a TCP connection on the loopback port."""
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    """SIGTERM one process, ignoring an already-gone pid."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise CliError(f"cannot signal pid {pid}: {error}") from error


def _stop_verified(state: dict) -> bool:
    """Signal a CONFIRMED instance and wait; True once it stopped answering.

    The caller has already confirmed identity with _our_instance. SIGTERM is
    followed by a bounded wait and a SIGKILL only if the process is still
    alive.
    """
    pid = int(state["pid"])
    _terminate(pid)
    stopped = _wait_until_stopped(state, _STOP_TIMEOUT_S)
    if not stopped and not _pid_gone(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        stopped = _wait_until_stopped(state, 2.0)
    return stopped


def _headless() -> bool:
    """True over SSH, or on Linux with no display server to open a window on."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return True
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def open_browser(url: str) -> bool:
    """Best-effort browser open (only ever called with --open); never raises.

    Skipped on a headless session, and never waits more than _BROWSER_WAIT_S:
    a console browser can run for as long as the user keeps it open, which
    must not hold the CLI (or the session that ran it).
    """
    if _headless():
        return False
    result: list[bool] = []

    def launch() -> None:
        try:
            result.append(bool(webbrowser.open(url)))
        except Exception:  # noqa: BLE001 — a broken browser launch is not an error
            result.append(False)

    worker = threading.Thread(target=launch, daemon=True)
    worker.start()
    worker.join(_BROWSER_WAIT_S)
    return result[0] if result else True


def _write_launch_page(state_dir: Path, url: str) -> Path:
    """Write ``<state-dir>/launch.html``, a redirect to the keyed URL (0600).

    Location: cli._write_launch_page.
    Summary: The browser is opened on this file instead of the keyed URL, so
             the key never appears in a process argument list, which other
             local users can read. Opened O_NOFOLLOW so a planted symlink is
             refused rather than followed; rewritten on every launch.
    Used by: _open_keyed.
    """
    path = state_dir / _LAUNCH_FILENAME
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    body = (
        "<!doctype html>\n<meta charset=\"utf-8\">\n"
        f"<meta http-equiv=\"refresh\" content=\"0;url={html.escape(url, quote=True)}\">\n"
        "<title>PACT Mission Control</title>\n"
    ).encode("utf-8")
    # O_EXCL after the unlink: if anything reappeared at the path in between,
    # the open fails instead of writing the keyed URL into it.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, body)
    finally:
        os.close(descriptor)
    return path


def _open_keyed(state_dir: Path, url: str) -> bool:
    """Open the keyed URL through the launch page; never raises."""
    try:
        page = _write_launch_page(state_dir, url)
    except OSError:
        return False
    return open_browser(page.as_uri())


def _print_url(url: str) -> None:
    """Print the contract's single URL line (stdout, flushed)."""
    print(f"URL: {url}", flush=True)


def _stop_command(state_dir: Path) -> str:
    """The runnable Stop: command for this state directory.

    Location: cli._stop_command.
    Summary: Uses the launcher path bin/pact-viz exported, else the direct
             interpreter form, and always the absolute state directory, so
             the line works pasted into any shell. Every part is
             shell-quoted.
    Used by: _print_stop_line, refusal messages.
    """
    directory = shlex.quote(str(Path(os.path.abspath(str(state_dir)))))
    launcher = os.environ.get(LAUNCHER_ENV, "").strip()
    if launcher:
        return f"{shlex.quote(launcher)} --stop --state-dir {directory}"
    return (
        f"PYTHONPATH={shlex.quote(str(PLUGIN_ROOT))} {shlex.quote(sys.executable)} "
        f"-P -m visualizer --stop --state-dir {directory}"
    )


def _print_stop_line(state_dir: Path) -> None:
    """Print the Stop: line documenting how to end the server."""
    print(f"Stop: {_stop_command(state_dir)}", flush=True)


def _selection_query(args: argparse.Namespace) -> str:
    """The URL query this invocation selects ("" for the default view)."""
    if getattr(args, "demo", False):
        return "?demo=1"
    params: list[tuple[str, str]] = []
    session = getattr(args, "session", None)
    if session:
        params.append(("session", str(session)))
    project = getattr(args, "selected_project", None)
    if project:
        params.append(("project", str(project)))
    return "?" + urllib.parse.urlencode(params) if params else ""


def keyed_url(base_url: str, query: str, key: str) -> str:
    """``<base>/<query>#k=<key>``: the key rides only in the fragment.

    A fragment is never sent to the server, so the key stays out of request
    lines, logs and Referer headers.
    """
    return base_url.rstrip("/") + "/" + query + "#k=" + key


def _state_roots_label(roots: Sequence[RootSpec]) -> str:
    """Comma-joined root ids for state/diagnostic output."""
    return ", ".join(root.id for root in roots)


def _resolve_roots(args: argparse.Namespace) -> list[RootSpec]:
    """Resolve CLI roots through the shared settings resolver."""
    return settings.resolve_roots(
        root_args=args.root or (),
        all_roots=bool(getattr(args, "all_roots", False)),
    )


_TERMINAL_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _terminal(value: object) -> str:
    """``value`` as text with every C0/C1 control character removed.

    Location: cli._terminal.
    Summary: Journal- and state-file-derived text reaches the operator's
             terminal through --list and --status; an embedded escape
             sequence must not be able to rewrite or hide what is printed.
    Used by: cmd_list, cmd_status.
    """
    return _TERMINAL_CONTROL.sub("", str(value))


def _utc_now_iso() -> str:
    """Current UTC time in the journal's timestamp shape."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_list(args: argparse.Namespace) -> int:
    """--list: print the discovered sessions and exit without a server.

    Location: cli.cmd_list.
    Summary: A compact plain-text table: root, project, session id, updated,
             status and size. --project/--project-dir/--session filter with
             the same matcher /api/sessions uses. Reads only the bounded
             head/tail summaries and never starts a listener.
    Used by: main, tests.
    """
    roots = _resolve_roots(args)
    payload = model.build_sessions_list(
        roots,
        limit=settings.DEFAULT_SESSIONS_LIMIT,
        project_filter=str(getattr(args, "selected_project", None) or ""),
        session_filter=str(getattr(args, "session", None) or ""),
        show_all=True,
    )
    print(f"{APP_LABEL} · sessions")
    if not roots:
        print("(no roots configured)")
    index_by_root = {root.id: f"#{number}" for number, root in enumerate(roots, 1)}
    for root in roots:
        print(f"root {index_by_root[root.id]}: {_terminal(root.id)} ({_terminal(root.path)})")
    sessions = payload.get("sessions", [])
    if not sessions:
        print("No PACT session journals found.")
        return 0
    header = f"{'ROOT':<5} {'PROJECT':<22} {'SESSION':<38} {'UPDATED':<21} {'STATUS':<9} {'SIZE':>9}"
    print(header)
    for session in sessions:
        root_label = index_by_root.get(str(session.get("root", "")), "?")
        print(
            f"{root_label:<5} "
            f"{_terminal(session.get('project', '')):<22.22} "
            f"{_terminal(session.get('session_id', '')):<38.38} "
            f"{_terminal(session.get('updated_at') or '-'):<21.21} "
            f"{_terminal(session.get('status', '')):<9.9} "
            f"{int(session.get('size_bytes') or 0):>9}"
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """--status: report whether the detached server is running.

    Location: cli.cmd_status.
    Summary: Reads the state file, then confirms the instance with
             key-authenticated health. A stale or foreign server reads as
             "not running"; the command is informational and exits 0 in both
             cases. The URL line carries the launch key; PID comes from
             health.
    Used by: main, tests.
    """
    state_dir = _state_dir(args)
    state = read_state(state_dir)
    if state is None:
        print("PACT Mission Control: not running")
        return 0
    health = _our_instance(state)
    if health is None:
        print("PACT Mission Control: not running (stale state file)")
        return 0
    print("PACT Mission Control: running")
    print(f"URL: {_terminal(keyed_url(str(state['url']), '', str(state['key'])))}")
    print(f"PID: {health['pid']}")
    print(f"Instance: {_terminal(state['instance_id'])}")
    if state.get("roots"):
        print("Roots: " + ", ".join(_terminal(root) for root in state["roots"]))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """--stop: confirm and stop the detached server; idempotent.

    Location: cli.cmd_stop.
    Summary: The process is only signalled when key-authenticated health
             reports the recorded instance_id and pid (and the recorded start
             time, where one exists) — a stale or tampered pid the OS reused,
             or one naming a bystander, is never signalled. Stop success means
             the instance stopped answering. ``Stop: stopped pid N`` (exit 0),
             ``Stop: not running`` (exit 0), ``Stop: refused`` (exit 1) and
             ``Stop: unconfirmed pid N`` (exit 1).
    Used by: main, tests.
    """
    state_dir = _state_dir(args)
    state = read_state(state_dir)
    if state is None:
        print("PACT Mission Control: not running")
        print("Stop: not running")
        return 0
    instance_id = str(state["instance_id"])
    pid = int(state["pid"])
    health = health_check(int(state["port"]), str(state["key"]))
    if health is None and not _pid_gone(pid) and _port_accepts(int(state["port"])):
        # The pid lives and the port still accepts connections, yet health
        # did not answer: a stopped or overloaded server. Its record is the
        # only way to stop it later, so keep it.
        print(f"Stop: unconfirmed pid {pid}")
        print(
            "PACT Mission Control: the server did not answer its health check; "
            "the record is kept — run the Stop line again once it responds"
        )
        return 1
    if health is None or health.get("instance_id") != instance_id:
        if _clear_state_if_instance(state_dir, instance_id):
            _remove_residue(state_dir)
        print("Stop: not running")
        print("PACT Mission Control: not running (stale state file cleaned)")
        return 0
    if _our_instance(state) is None:
        # The instance answers with our token but under another pid or start
        # time; signalling the recorded pid would hit an unrelated process.
        print("Stop: refused")
        print(
            f"PACT Mission Control: state file pid {pid} does not match the "
            f"server's pid {health.get('pid')!r}; refusing to signal — delete "
            f"{shlex.quote(str(settings.state_file(state_dir)))} to clear "
            "the record"
        )
        return 1
    stopped = _stop_verified(state)
    if _clear_state_if_instance(state_dir, instance_id):
        _remove_residue(state_dir)
    if not stopped:
        print(f"Stop: unconfirmed pid {pid}")
        print(
            "PACT Mission Control: stop was signalled but the process did "
            "not confirm exit"
        )
        return 1
    print(f"Stop: stopped pid {pid}")
    print("PACT Mission Control: stopped")
    return 0


def _refuse(difference: str, state_dir: Path) -> None:
    """Raise the contract's reuse refusal for one difference."""
    raise CliError(
        f"a running instance {difference}; stop it first with {_stop_command(state_dir)}"
    )


def _reuse_if_running(
    state_dir: Path, args: argparse.Namespace, roots: Sequence[RootSpec]
) -> bool:
    """Reuse a healthy instance of this build; True when it was reused.

    Location: cli._reuse_if_running.
    Summary: Only a confirmed instance (_our_instance) counts; anything else
             starts fresh. In order: (1) another plugin build (plugin_root or
             version differs) is stopped with a verified stop and replaced —
             its viewer is not the one asked for; (2) different roots refuse;
             (3) an explicit --port that differs refuses. A reused instance
             prints its URL with the stored key and THIS invocation's
             selection query; reuse never mints a key.
    Used by: serve_foreground, detach, tests.
    """
    state = read_state(state_dir)
    if state is None or _our_instance(state) is None:
        return False
    if state.get("plugin_root") != str(PLUGIN_ROOT) or state.get("version") != VERSION:
        if not _stop_verified(state):
            _refuse("from another plugin build did not stop", state_dir)
        if _clear_state_if_instance(state_dir, str(state["instance_id"])):
            _remove_residue(state_dir)
        print(f"{APP_LABEL}: replaced an instance from another plugin build")
        return False
    wanted_roots = [root.id for root in roots]
    running_roots = [str(item) for item in state.get("roots", [])]
    if wanted_roots != running_roots:
        _refuse(
            f"serves different roots ({', '.join(running_roots) or 'unknown'}) than "
            f"requested ({', '.join(wanted_roots) or 'unknown'})",
            state_dir,
        )
    wanted_port = int(getattr(args, "port", 0) or 0)
    if wanted_port and wanted_port != int(state["port"]):
        _refuse(f"is on port {state['port']} but port {wanted_port} was requested", state_dir)
    url = keyed_url(str(state["url"]), _selection_query(args), str(state["key"]))
    print(f"{APP_LABEL}: reusing the running instance")
    _print_url(url)
    _print_stop_line(state_dir)
    if getattr(args, "open_browser", False):
        _open_keyed(state_dir, url)
    return True


def serve_foreground(
    args: argparse.Namespace,
    roots: Sequence[RootSpec],
    state_dir: Path,
    instance_id: str,
    key: str,
) -> int:
    """Run the server in the foreground until interrupted.

    Location: cli.serve_foreground.
    Summary: Reuses a healthy running instance first; otherwise binds, writes
             the state file (with the key, plugin root and own start time),
             prints the URL/Stop lines, optionally opens the browser, and
             serves until SIGINT/SIGTERM (both trigger a clean shutdown that
             removes only this instance's state file).
    Used by: main, tests.
    """
    if _reuse_if_running(state_dir, args, roots):
        return 0
    port = int(getattr(args, "port", 0) or 0)
    try:
        server = server_module.create_server(
            roots,
            port=port,
            instance_id=instance_id,
            key=key,
            verbose=bool(getattr(args, "verbose", False)),
        )
    except OSError as error:
        # A port already in use is a user-facing condition, not a traceback.
        raise CliError(f"cannot bind {LOOPBACK_HOST}:{port}: {error}") from error
    base_url = server_module.server_url(server)
    url = keyed_url(base_url, _selection_query(args), key)
    state_payload = {
        "version": VERSION,
        "plugin_root": str(PLUGIN_ROOT),
        "pid": os.getpid(),
        "pid_start": pid_start_time(os.getpid()),
        "host": LOOPBACK_HOST,
        "port": int(server.server_port),
        "url": base_url,
        "instance_id": instance_id,
        "key": key,
        "started_at": _utc_now_iso(),
        "roots": [root.id for root in roots],
    }
    try:
        write_state(state_dir, state_payload)
    except CliError:
        server.server_close()
        raise
    print(f"{APP_LABEL} {VERSION} · {_state_roots_label(roots)}")
    _print_url(url)
    _print_stop_line(state_dir)
    if getattr(args, "open_browser", False):
        if not _open_keyed(state_dir, url):
            print("pact-viz: could not open a browser; use the URL above", file=sys.stderr)

    def _request_shutdown(_signum: int, _frame: Any) -> None:
        # shutdown() blocks until serve_forever returns; a signal handler runs
        # on the serving thread, so it must not call it directly.
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signum] = signal.signal(signum, _request_shutdown)
        except (ValueError, OSError):
            continue
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        finally:
            for signum, handler in previous_handlers.items():
                try:
                    signal.signal(signum, handler)
                except (ValueError, OSError):
                    continue
            current = read_state(state_dir)
            if current is not None and current.get("instance_id") == instance_id:
                clear_state(state_dir)
    print("PACT Mission Control: stopped")
    return 0


def _child_command(
    args: argparse.Namespace,
    roots: Sequence[RootSpec],
    state_dir: Path,
) -> list[str]:
    """The detached child's argv: serving flags only, never selection or key.

    Location: cli._child_command.
    Summary: Root paths and the state directory are passed ABSOLUTE because
             the child runs with its working directory set to the plugin root;
             forwarding a relative --root/--state-dir verbatim would make the
             child resolve them against a different directory than the parent.
             ``-P`` keeps the working directory off sys.path, so the child
             imports the visualizer only through PYTHONPATH. The key travels
             in the environment, not here, because argv is readable by other
             local users.
    Used by: detach.
    """
    command = [
        sys.executable,
        "-P",
        "-m",
        "visualizer",
        "--port",
        str(int(getattr(args, "port", 0) or 0)),
    ]
    for root in roots:
        command.extend(["--root", str(root.path)])
    if getattr(args, "all_roots", False):
        command.append("--all-roots")
    command.extend(["--state-dir", str(state_dir)])
    if getattr(args, "verbose", False):
        command.append("--verbose")
    return command


def detach(
    args: argparse.Namespace,
    roots: Sequence[RootSpec],
    state_dir: Path,
) -> int:
    """--detach: spawn the server in the background and wait until healthy.

    Location: cli.detach.
    Summary: Reuses a healthy instance first. Otherwise mints a random
             instance token and launch key, passes both to the child through
             internal env vars (the child pops them), spawns ``python -P -m
             visualizer`` in its own session with stderr appended to
             ``<state-dir>/server.log``, and polls the state file + keyed
             health until the child answers as that instance (or the timeout
             expires, in which case the child is killed and a nonzero exit is
             returned). The parent's stdout carries exactly the banner, URL
             and Stop lines.
    Used by: main, tests.
    """
    prepare_state_dir(state_dir)
    # One launcher at a time per state dir: a second concurrent --detach
    # waits here and then reuses the instance the first one started.
    with _launch_lock(state_dir):
        return _detach_locked(args, roots, state_dir)


@contextlib.contextmanager
def _launch_lock(state_dir: Path):
    """An exclusive advisory lock on ``<state-dir>/.launch.lock`` (POSIX)."""
    try:
        import fcntl
    except ImportError:  # not POSIX: no lock, the reuse retry still applies
        yield
        return
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(state_dir / _LOCK_FILENAME, flags, 0o600)
    except OSError as error:
        raise CliError(f"cannot lock state directory {state_dir}: {error}") from error
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _open_log(path: Path):
    """Open the child's log for append, 0600, refusing a symlink."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "ab")


def _detach_locked(
    args: argparse.Namespace,
    roots: Sequence[RootSpec],
    state_dir: Path,
) -> int:
    """detach() under the launch lock."""
    if _reuse_if_running(state_dir, args, roots):
        return 0
    instance_id = secrets.token_hex(16)
    key = secrets.token_urlsafe(32)
    environment = dict(os.environ)
    environment[_INSTANCE_ENV] = instance_id
    environment[KEY_ENV] = key
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{PLUGIN_ROOT}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(PLUGIN_ROOT)
    )
    log_path = state_dir / _LOG_FILENAME
    command = _child_command(args, roots, state_dir)
    try:
        with _open_log(log_path) as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(PLUGIN_ROOT),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as error:
        raise CliError(f"cannot start the server process: {error}") from error

    deadline = time.monotonic() + _READY_TIMEOUT_S
    ready: dict | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        state = read_state(state_dir)
        if state is not None and state.get("instance_id") == instance_id:
            if _our_instance(state) is not None:
                ready = state
                break
        time.sleep(_POLL_INTERVAL_S)
    if ready is None:
        exited = process.poll() is not None
        if not exited:
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        # Same instance guard as every other clear site: a concurrent detach
        # that already published its state must not lose its record here.
        _clear_state_if_instance(state_dir, instance_id)
        if exited and _reuse_if_running(state_dir, args, roots):
            # The child found a healthy instance and stepped aside for it.
            return 0
        tail = _log_tail(log_path)
        try:
            log_path.unlink(missing_ok=True)
        except OSError:
            pass
        message = (
            "the server exited before it was ready" if exited
            else "server did not become healthy in time"
        )
        if tail:
            message += f"; its log said:\n{tail}"
        raise CliError(message)
    url = keyed_url(str(ready["url"]), _selection_query(args), key)
    # Reap the child without blocking: this process returns immediately, but a
    # long-lived caller (a test harness, an embedding script) would otherwise
    # carry a zombie when the detached server eventually exits.
    threading.Thread(target=process.wait, daemon=True).start()
    print(f"{APP_LABEL} {VERSION} · detached")
    _print_url(url)
    _print_stop_line(state_dir)
    if getattr(args, "open_browser", False):
        if not _open_keyed(state_dir, url):
            print("pact-viz: could not open a browser; use the URL above", file=sys.stderr)
    return 0


def _log_tail(path: Path, max_chars: int = 1200) -> str:
    """The tail of the detached child's log (for the failure message)."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").strip()
    return text[-max_chars:]


def _check_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Validate selection values and flag combinations; nonzero exit on error.

    Sets ``args.selected_project`` (the --project label or the --project-dir
    slug). Meaningless combinations are argparse usage errors (exit 2).
    """
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    modes = [
        name
        for name, enabled in (("--stop", args.stop), ("--status", args.status), ("--list", args.list))
        if enabled
    ]
    if len(modes) > 1:
        parser.error("choose at most one of --stop, --status, --list")
    if args.detach and modes:
        parser.error("--detach cannot be combined with --stop, --status or --list")
    selection = [
        flag
        for flag, value in (
            ("--demo", args.demo),
            ("--project", args.project),
            ("--project-dir", args.project_dir),
            ("--session", args.session),
        )
        if value
    ]
    if args.stop or args.status:
        extra = selection + [
            flag
            for flag, value in (("--port", args.port), ("--open", args.open_browser))
            if value
        ]
        if extra:
            parser.error(f"{modes[0]} cannot be combined with {', '.join(extra)}")
    if args.list:
        extra = [
            flag
            for flag, value in (
                ("--demo", args.demo),
                ("--open", args.open_browser),
                ("--port", args.port),
            )
            if value
        ]
        if extra:
            parser.error(f"--list cannot be combined with {', '.join(extra)}")
    if args.project and args.project_dir:
        parser.error("choose one of --project and --project-dir")
    if args.demo and len(selection) > 1:
        parser.error("--demo cannot be combined with --project, --project-dir or --session")
    if args.project is not None and not settings.PROJECT_LABEL_RE.fullmatch(args.project):
        print("pact-viz: --project must match [A-Za-z0-9._-]", file=sys.stderr)
        return 2
    if args.session is not None and not settings.SESSION_ID_RE.fullmatch(args.session):
        print("pact-viz: --session must match [A-Za-z0-9-]{8,64}", file=sys.stderr)
        return 2
    args.selected_project = args.project or (
        project_slug(args.project_dir) if args.project_dir else ""
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """The pact-viz entry point.

    Location: cli.main.
    Summary: Takes the internal instance token and launch key out of the
             environment first (so nothing this process spawns inherits
             them), enforces the Python floor (so an old interpreter gets a
             clear error before argparse), validates flags, and dispatches to
             --stop/--status/--list, detached or foreground serving. CLI
             failures print one ``pact-viz:`` line to stderr and return 1;
             usage errors exit 2.
    Used by: visualizer.__main__, tests.
    """
    inherited_instance = os.environ.pop(_INSTANCE_ENV, None)
    inherited_key = os.environ.pop(KEY_ENV, None)
    if not settings.python_version_ok():
        required = ".".join(str(part) for part in MIN_PYTHON)
        print(
            f"pact-viz: Python {required}+ is required (found {_python_version_text()})",
            file=sys.stderr,
        )
        return 1
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    status = _check_args(parser, args)
    if status:
        return status
    try:
        if args.stop:
            return cmd_stop(args)
        if args.status:
            return cmd_status(args)
        if args.list:
            _warn_odd_roots(_resolve_roots(args))
            return cmd_list(args)
        roots = _resolve_roots(args)
        _warn_odd_roots(roots)
        state_dir = _state_dir(args)
        if args.detach:
            return detach(args, roots, state_dir)
        instance_id = inherited_instance or secrets.token_hex(16)
        key = inherited_key or secrets.token_urlsafe(32)
        return serve_foreground(args, roots, state_dir, instance_id, key)
    except CliError as error:
        print(f"pact-viz: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # `pact-viz --list | head`: the reader left; that is not an error.
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


def _warn_odd_roots(roots: Sequence[RootSpec]) -> None:
    """One stderr line for a root that cannot hold sessions as given."""
    for root in roots:
        if root.path.name == settings.PACT_SESSIONS_DIRNAME:
            print(
                f"pact-viz: root {root.path} looks like a pact-sessions directory; "
                "pass its parent",
                file=sys.stderr,
            )
        elif not root.path.is_dir():
            print(f"pact-viz: root {root.path} does not exist", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - module entry point parity
    raise SystemExit(main())
