"""Location: pact-plugin/visualizer/cli.py
Summary: The ``pact-viz`` command line: argument parsing, root resolution,
         foreground and detached serving, the on-disk server state file
         (atomic, 0600 in a 0700 directory), health-checked --stop/--status,
         the plain-text --list, and the Python >= 3.12 floor.
Used by: visualizer.__main__ (python -m visualizer), pact-plugin/bin/pact-viz
         (exec'd wrapper), tests.

Only two blocking operations live here: starting/stopping the detached child
and the wait-for-ready health poll. Everything else is a pure function of the
state file and the environment, so the direct CLI is testable without a real
server.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import model, settings
from . import server as server_module
from .settings import (
    APP_LABEL,
    LOOPBACK_HOST,
    MIN_PYTHON,
    VERSION,
    RootSpec,
)

# Internal env var carrying the parent-generated instance token to the detached
# child, so the parent can health-check THE instance it just spawned and never
# mistake a stale state file or a foreign server for its own. Not a user flag.
_INSTANCE_ENV = "PACT_VIZ_INSTANCE_ID"

_LOG_FILENAME = "server.log"
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
             literal in settings/server.
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
    parser.add_argument("--project", metavar="SLUG", help="only list sessions in this project")
    parser.add_argument(
        "--session", metavar="ID", help="only list sessions whose id contains this text"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="open the training simulation (?demo=1) instead of a real session",
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


def _state_dir(args: argparse.Namespace) -> Path:
    """Resolve the state directory: --state-dir or <config>/pact-viz."""
    override = getattr(args, "state_dir", None)
    if override:
        return Path(os.path.abspath(os.path.expanduser(str(override))))
    return settings.default_state_dir()


def read_state(state_dir: Path) -> dict | None:
    """Read and validate the server state file, or None.

    Location: cli.read_state.
    Summary: Returns the parsed object only when it has the required types
             (pid/port ints, url/instance_id strings); a truncated or hostile
             file reads as "no state", never as a partial running server.
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
    if not isinstance(port, int) or not isinstance(pid, int):
        return None
    if not (0 < port <= 65535) or pid <= 0:
        return None
    if not isinstance(payload.get("instance_id"), str):
        return None
    if not isinstance(payload.get("url"), str):
        return None
    return payload


def write_state(state_dir: Path, payload: dict) -> Path:
    """Atomically write the server state file (dir 0700, file 0600).

    Location: cli.write_state.
    Summary: The directory is created/forced to 0700, the JSON is written to a
             temp file in the same directory with mode 0600, then
             ``os.replace`` makes it visible atomically. Partial files are
             removed on failure.
    Used by: serve_foreground, tests.
    """
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
    except OSError as error:
        raise CliError(f"cannot create state directory {state_dir}: {error}") from error
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
    Summary: The guard serve_foreground's shutdown path has always had: a
             concurrent ``--detach`` that overwrote the state file between the
             read and the clear must not lose ITS state file, or its server
             runs on with no record to stop it. Returns True when the state
             dir was cleared (ours or already empty) — the caller then also
             removes the detached server's log residue.
    Used by: cmd_stop (both clear sites).
    """
    current = read_state(state_dir)
    if current is None or current.get("instance_id") == instance_id:
        clear_state(state_dir)
        return True
    return False


def _remove_server_log(state_dir: Path) -> None:
    """Remove the detached server's log; never raises.

    Location: cli._remove_server_log.
    Summary: detach creates ``server.log`` for its child's stderr; after a
             stop the file is residue (usually 0 bytes). Unlinking after the
             bounded stop wait is safe: the writer has exited, and even in the
             unconfirmed-exit case an unlink only orphans the child's open fd
             rather than corrupting anything. A later detach recreates it.
    Used by: cmd_stop (after a guarded clear).
    """
    try:
        (state_dir / _LOG_FILENAME).unlink(missing_ok=True)
    except OSError:
        pass


def health_check(port: int, timeout: float = 1.0) -> dict | None:
    """GET /api/health on loopback; the parsed payload or None.

    Location: cli.health_check.
    Summary: Uses an opener with proxies disabled so a machine-wide HTTP proxy
             can never route a loopback check off-box. Short timeout; any
             failure reads as "not healthy".
    Used by: cmd_status, cmd_stop, detach, serve_foreground, tests.
    """
    if not isinstance(port, int) or not (0 < port <= 65535):
        return None
    url = f"http://{LOOPBACK_HOST}:{port}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("ok"):
        return payload
    return None


def _health_for_instance(port: int, instance_id: str, timeout: float = 1.0) -> dict | None:
    """Health-check and require the expected random instance token."""
    payload = health_check(port, timeout=timeout)
    if payload is None:
        return None
    return payload if payload.get("instance_id") == instance_id else None


def open_browser(url: str) -> bool:
    """Best-effort browser open (only ever called with --open); never raises."""
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 — a broken browser launch is not an error
        return False


def _print_url(url: str) -> None:
    """Print the contract's single URL line (stdout, flushed)."""
    print(f"URL: {url}", flush=True)


def _stop_hint(args: argparse.Namespace | None = None) -> str:
    """The Stop: command matching how this server was launched.

    Location: cli._stop_hint.
    Summary: The state directory is shell-quoted because this line is meant to
             be pasted into a shell; an unquoted path with a metacharacter
             would execute it.
    Used by: _print_stop_line, _refuse_on_reuse_mismatch.
    """
    hint = "pact-viz --stop"
    override = getattr(args, "state_dir", None) if args is not None else None
    if override:
        hint += f" --state-dir {shlex.quote(str(override))}"
    return hint


def _print_stop_line(args: argparse.Namespace | None = None) -> None:
    """Print the Stop: line documenting how to end the server."""
    print(f"Stop: {_stop_hint(args)}", flush=True)


def _demo_url(base_url: str) -> str:
    """The same URL with the client-side demo flag, exactly once."""
    if "?demo=1" in base_url:
        return base_url
    return base_url.rstrip("/") + "/?demo=1"


def _state_roots_label(roots: Sequence[RootSpec]) -> str:
    """Comma-joined root ids for state/diagnostic output."""
    return ", ".join(root.id for root in roots)


def _resolve_roots(args: argparse.Namespace) -> list[RootSpec]:
    """Resolve CLI roots through the shared settings resolver."""
    return settings.resolve_roots(
        root_args=args.root or (),
        all_roots=bool(getattr(args, "all_roots", False)),
    )


def _filters(args: argparse.Namespace) -> dict[str, str]:
    """The /api/sessions filters from --project/--session."""
    filters: dict[str, str] = {}
    project = getattr(args, "project", None)
    session = getattr(args, "session", None)
    if project:
        filters["project"] = str(project)
    if session:
        filters["session"] = str(session)
    return filters


def _utc_now_iso() -> str:
    """Current UTC time in the journal's timestamp shape."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_list(args: argparse.Namespace) -> int:
    """--list: print the discovered sessions and exit without a server.

    Location: cli.cmd_list.
    Summary: A compact plain-text table: root, project, session id, updated,
             status and size. Reads only the bounded head/tail summaries, so
             it is fast on large journals and never starts a listener.
    Used by: main, tests.
    """
    roots = _resolve_roots(args)
    filters = _filters(args)
    payload = model.build_sessions_list(
        roots,
        limit=settings.DEFAULT_SESSIONS_LIMIT,
        project_filter=filters.get("project", ""),
        session_filter=filters.get("session", ""),
    )
    print(f"{APP_LABEL} · sessions")
    if not roots:
        print("(no roots configured)")
    else:
        print("roots: " + ", ".join(f"{root.id} ({root.path})" for root in roots))
    sessions = payload.get("sessions", [])
    if not sessions:
        print("No PACT session journals found.")
        return 0
    header = f"{'ROOT':<18} {'PROJECT':<22} {'SESSION':<26} {'UPDATED':<21} {'STATUS':<7} {'SIZE':>9}"
    print(header)
    for session in sessions:
        print(
            f"{session.get('root', ''):<18.18} "
            f"{session.get('project', ''):<22.22} "
            f"{session.get('session_id', ''):<26.26} "
            f"{(session.get('updated_at') or '-'):<21.21} "
            f"{session.get('status', ''):<7.7} "
            f"{int(session.get('size_bytes') or 0):>9}"
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """--status: report whether the detached server is running.

    Location: cli.cmd_status.
    Summary: Reads the state file, then health-checks the recorded port and
             requires the recorded instance token. A stale or foreign server
             reads as "not running"; the command is informational and exits 0
             in both cases.
    Used by: main, tests.
    """
    state_dir = _state_dir(args)
    state = read_state(state_dir)
    if state is None:
        print("PACT Mission Control: not running")
        return 0
    if _health_for_instance(int(state["port"]), str(state["instance_id"])) is None:
        print("PACT Mission Control: not running (stale state file)")
        return 0
    status_url = str(state["url"])
    if state.get("demo"):
        status_url = _demo_url(status_url)
    print("PACT Mission Control: running")
    print(f"URL: {status_url}")
    print(f"PID: {state['pid']}")
    print(f"Instance: {state['instance_id']}")
    if state.get("roots"):
        print("Roots: " + ", ".join(str(root) for root in state["roots"]))
    return 0


def _terminate(pid: int) -> None:
    """SIGTERM one process, ignoring an already-gone pid."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise CliError(f"cannot signal pid {pid}: {error}") from error


def _wait_until_stopped(port: int, instance_id: str, timeout: float) -> bool:
    """Poll until the instance stops answering.

    Location: cli._wait_until_stopped.
    Summary: Success means THIS instance's /api/health no longer answers. A
             disappeared pid is not enough: the recorded pid can name an
             unrelated process, and treating its exit as success reported a
             stop while the real server kept serving.
    Used by: cmd_stop.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_for_instance(port, instance_id, timeout=0.25) is None:
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


def cmd_stop(args: argparse.Namespace) -> int:
    """--stop: health-check and stop the detached server; idempotent.

    Location: cli.cmd_stop.
    Summary: The process is only signalled when /api/health answers with the
             SAME random instance token recorded in the state file AND reports
             the same pid — a stale or tampered pid that the OS reused, or that
             names a bystander, is never killed. Stop success means the
             instance stopped answering, not merely that a pid disappeared.
             SIGTERM is followed by a bounded wait and a SIGKILL only if the
             process is still alive; the state file and the detached server's
             log are removed either way, unless a concurrent detach already
             replaced the state with a different instance's. ``Stop: stopped
             pid N`` prints only once health confirms the instance is gone
             (exit 0); ``Stop: not running`` also exits 0; a delivered signal
             whose exit is unconfirmed prints ``Stop: unconfirmed pid N`` and
             exits 1.
    Used by: main, tests.
    """
    state_dir = _state_dir(args)
    state = read_state(state_dir)
    if state is None:
        print("PACT Mission Control: not running")
        print("Stop: not running")
        return 0
    port = int(state["port"])
    instance_id = str(state["instance_id"])
    pid = int(state["pid"])
    health = _health_for_instance(port, instance_id)
    if health is None:
        if _clear_state_if_instance(state_dir, instance_id):
            _remove_server_log(state_dir)
        print("Stop: not running")
        print("PACT Mission Control: not running (stale state file cleaned)")
        return 0
    reported_pid = health.get("pid")
    if not isinstance(reported_pid, int) or reported_pid != pid:
        # The state file names a different process than the one answering with
        # this instance token; signalling it would kill an unrelated process.
        print("Stop: refused")
        print(
            f"PACT Mission Control: state file pid {pid} does not match the "
            f"server's pid {reported_pid!r}; refusing to signal — delete "
            f"{shlex.quote(str(settings.state_file(state_dir)))} to clear "
            "the record"
        )
        return 1
    _terminate(pid)
    stopped = _wait_until_stopped(port, instance_id, _STOP_TIMEOUT_S)
    if not stopped and not _pid_gone(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        stopped = _wait_until_stopped(port, instance_id, 2.0)
    if _clear_state_if_instance(state_dir, instance_id):
        _remove_server_log(state_dir)
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


def _reuse_if_running(
    state_dir: Path, args: argparse.Namespace, roots: Sequence[RootSpec]
) -> bool:
    """Print the existing healthy instance's URL and reuse it; True on reuse.

    Location: cli._reuse_if_running.
    Summary: Reuses only when the running instance serves EXACTLY what this
             invocation asked for — the same build version, the same root ids
             in the same order, a compatible port (an explicit --port must
             match; 0 accepts any), the same launch mode (--demo), and the
             same --project/--session filters. A mismatched request
             refuses with a CliError pointing at --stop, because silently
             printing the old instance's URL would show the user the wrong
             data. The state file records the filters so the comparison is
             honest across restarts.
    Used by: serve_foreground, detach, tests.
    """
    state = read_state(state_dir)
    if state is None:
        return False
    if _health_for_instance(int(state["port"]), str(state["instance_id"])) is None:
        return False
    _refuse_on_reuse_mismatch(state, args, roots)
    url = str(state["url"])
    if getattr(args, "demo", False):
        url = _demo_url(url)
    print(f"{APP_LABEL}: reusing the running instance")
    _print_url(url)
    _print_stop_line(args)
    if getattr(args, "open_browser", False):
        open_browser(url)
    return True


def _refuse_on_reuse_mismatch(
    state: dict, args: argparse.Namespace, roots: Sequence[RootSpec]
) -> None:
    """Raise CliError when the running instance does not serve this request."""
    running_version = state.get("version")
    if not isinstance(running_version, str) or running_version != VERSION:
        # Every writer of this file has recorded its version, so a missing or
        # non-text value is a hand-edited/corrupt record, not an old build.
        shown = (
            running_version
            if isinstance(running_version, str) and running_version
            else "(unknown)"
        )
        raise CliError(
            f"a running instance is version {shown} but this is {VERSION}; "
            f"stop it first with {_stop_hint(args)}"
        )
    wanted_roots = [root.id for root in roots]
    running_roots = [str(item) for item in state.get("roots", [])]
    if wanted_roots != running_roots:
        raise CliError(
            "a running instance serves different roots "
            f"({', '.join(running_roots) or 'unknown'}) than requested "
            f"({', '.join(wanted_roots) or 'unknown'}); stop it first with "
            f"{_stop_hint(args)}"
        )
    wanted_port = int(getattr(args, "port", 0) or 0)
    if wanted_port and wanted_port != int(state["port"]):
        raise CliError(
            f"a running instance is on port {state['port']} but port "
            f"{wanted_port} was requested; stop it first with {_stop_hint(args)}"
        )
    running_demo = bool(state.get("demo", False))
    wanted_demo = bool(getattr(args, "demo", False))
    if wanted_demo != running_demo:
        raise CliError(
            "a running instance was started "
            f"{'with --demo' if running_demo else 'without --demo'}; "
            f"stop it first with {_stop_hint(args)}"
        )
    running_filters = state.get("filters") or {}
    for key in ("project", "session"):
        wanted = str(getattr(args, key, None) or "")
        running = str(running_filters.get(key, "") or "")
        if wanted != running:
            raise CliError(
                f"a running instance was started with --{key} "
                f"{running or '(none)'} but --{key} {wanted or '(none)'} was "
                f"requested; stop it first with {_stop_hint(args)}"
            )


def serve_foreground(
    args: argparse.Namespace,
    roots: Sequence[RootSpec],
    state_dir: Path,
    instance_id: str,
) -> int:
    """Run the server in the foreground until interrupted.

    Location: cli.serve_foreground.
    Summary: Reuses a healthy running instance first; otherwise binds, writes
             the state file, prints the URL/Stop lines, optionally opens the
             browser, and serves until SIGINT/SIGTERM (both trigger a clean
             shutdown that removes only this instance's state file).
    Used by: main, tests.
    """
    if _reuse_if_running(state_dir, args, roots):
        return 0
    port = int(getattr(args, "port", 0) or 0)
    filters = _filters(args)
    demo_mode = bool(getattr(args, "demo", False))
    try:
        server = server_module.create_server(
            roots,
            port=port,
            instance_id=instance_id,
            verbose=bool(getattr(args, "verbose", False)),
            filters=filters,
            demo_mode=demo_mode,
        )
    except OSError as error:
        # A port already in use is a user-facing condition, not a traceback.
        raise CliError(f"cannot bind {LOOPBACK_HOST}:{port}: {error}") from error
    actual_port = int(server.server_port)
    # The state file always stores the canonical BASE url; ?demo=1 is a
    # client-side display mode, so appending it at print/open time (and
    # exactly once) keeps --status/--stop/reuse from doubling the query.
    base_url = server_module.server_url(server)
    url = _demo_url(base_url) if demo_mode else base_url
    state_payload = {
        "version": VERSION,
        "pid": os.getpid(),
        "host": LOOPBACK_HOST,
        "port": actual_port,
        "url": base_url,
        "instance_id": instance_id,
        "started_at": _utc_now_iso(),
        "demo": demo_mode,
        "roots": [root.id for root in roots],
        "filters": dict(filters),
    }
    try:
        write_state(state_dir, state_payload)
    except CliError:
        server.server_close()
        raise
    print(f"{APP_LABEL} {VERSION} · {_state_roots_label(roots)}")
    _print_url(url)
    _print_stop_line(args)
    if getattr(args, "open_browser", False):
        if not open_browser(url):
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
    """The detached child's argv: the same flags minus --detach/--open.

    Location: cli._child_command.
    Summary: Root paths and the state directory are passed ABSOLUTE because
             the child runs with its working directory set to the plugin root;
             forwarding a relative --root/--state-dir verbatim would make the
             child resolve them against a different directory than the parent.
    Used by: detach.
    """
    command = [
        sys.executable,
        "-m",
        "visualizer",
        "--port",
        str(int(getattr(args, "port", 0) or 0)),
    ]
    for root in roots:
        command.extend(["--root", str(root.path)])
    if getattr(args, "all_roots", False):
        command.append("--all-roots")
    if getattr(args, "project", None):
        command.extend(["--project", str(args.project)])
    if getattr(args, "session", None):
        command.extend(["--session", str(args.session)])
    if getattr(args, "demo", False):
        command.append("--demo")
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
    Summary: Reuses a healthy instance first. Otherwise generates a random
             instance token, passes it to the child through an internal env
             var, spawns ``python -m visualizer`` in its own session with
             stderr appended to ``<state-dir>/server.log``, and polls the
             state file + /api/health until the child answers with that exact
             token (or the timeout expires, in which case the child is killed
             and a nonzero exit is returned). The child's stdout/stderr are not
             inherited, so the parent's own stdout carries exactly the URL and
             Stop lines.
    Used by: main, tests.
    """
    if _reuse_if_running(state_dir, args, roots):
        return 0
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
    except OSError as error:
        raise CliError(f"cannot create state directory {state_dir}: {error}") from error
    instance_id = secrets.token_hex(16)
    package_root = Path(__file__).resolve().parent.parent
    environment = dict(os.environ)
    environment[_INSTANCE_ENV] = instance_id
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(package_root)
    )
    log_path = state_dir / _LOG_FILENAME
    command = _child_command(args, roots, state_dir)
    try:
        with log_path.open("ab") as log_handle:
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
            process = subprocess.Popen(
                command,
                cwd=str(package_root),
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
    reused_url = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        state = read_state(state_dir)
        if state is not None and state.get("instance_id") == instance_id:
            if _health_for_instance(int(state["port"]), instance_id) is not None:
                reused_url = str(state["url"])
                break
        time.sleep(_POLL_INTERVAL_S)
    if not reused_url:
        if process.poll() is None:
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
        tail = _log_tail(log_path)
        message = "server did not become healthy in time"
        if tail:
            message += f"; last log lines from {log_path}:\n{tail}"
        raise CliError(message)
    url = _demo_url(reused_url) if bool(getattr(args, "demo", False)) else reused_url
    # Reap the child without blocking: this process returns immediately, but a
    # long-lived caller (a test harness, an embedding script) would otherwise
    # carry a zombie when the detached server eventually exits. A blocking
    # wait() here would hang the CLI until the server stops.
    threading.Thread(target=process.wait, daemon=True).start()
    print(f"{APP_LABEL} {VERSION} · detached")
    _print_url(url)
    _print_stop_line(args)
    if getattr(args, "open_browser", False):
        if not open_browser(url):
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


def main(argv: Sequence[str] | None = None) -> int:
    """The pact-viz entry point.

    Location: cli.main.
    Summary: Enforces the Python floor first (so an old interpreter gets a
             clear error before argparse), parses flags, rejects conflicting
             modes, and dispatches to --stop/--status/--list, detached or
             foreground serving. CLI failures print one ``pact-viz:`` line to
             stderr and return 1; usage errors exit 2 through argparse.
    Used by: visualizer.__main__, tests.
    """
    if not settings.python_version_ok():
        required = ".".join(str(part) for part in MIN_PYTHON)
        print(
            f"pact-viz: Python {required}+ is required (found {_python_version_text()})",
            file=sys.stderr,
        )
        return 1
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    modes = [
        name
        for name, enabled in (
            ("--stop", args.stop),
            ("--status", args.status),
            ("--list", args.list),
        )
        if enabled
    ]
    if len(modes) > 1:
        parser.error("choose at most one of --stop, --status, --list")
    if args.detach and modes:
        parser.error("--detach cannot be combined with --stop, --status or --list")
    try:
        if args.stop:
            return cmd_stop(args)
        if args.status:
            return cmd_status(args)
        if args.list:
            return cmd_list(args)
        roots = _resolve_roots(args)
        state_dir = _state_dir(args)
        if args.detach:
            return detach(args, roots, state_dir)
        instance_id = os.environ.get(_INSTANCE_ENV) or secrets.token_hex(16)
        return serve_foreground(args, roots, state_dir, instance_id)
    except CliError as error:
        print(f"pact-viz: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry point parity
    raise SystemExit(main())
