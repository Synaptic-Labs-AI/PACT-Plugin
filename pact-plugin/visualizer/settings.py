"""Location: pact-plugin/visualizer/settings.py
Summary: Immutable configuration for PACT Mission Control: product identity,
         response caps, the CSP string, static-asset allowlists, and the
         resolution of config roots / state directories from flags, the
         environment and the home directory.
Used by: visualizer.discovery (root specs), visualizer.server (caps + CSP),
         visualizer.cli (state dir, python floor), visualizer.demo (etag).

This module performs no I/O beyond reading the environment mapping handed to
it; every resolver is pure so tests can pass an explicit environment and home
directory instead of touching the operator's real ~/.claude.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# --- Product identity -------------------------------------------------------

APP_NAME = "pact-mission-control"
APP_LABEL = "PACT Mission Control"
DEMO_ETAG = "demo-v1"
# The scripted demo is a fixed script, not a build: it reports this constant
# instead of the plugin version, so a version bump does not change it.
DEMO_VERSION = "demo"

# The plugin directory this package runs from (``<plugin>/visualizer/..``).
PLUGIN_ROOT: Path = Path(__file__).resolve().parents[1]


def _plugin_version(plugin_root: Path) -> str:
    """The ``version`` from ``<plugin_root>/.claude-plugin/plugin.json``.

    Falls back to ``0+unknown`` when the manifest is missing or unreadable,
    so the CLI still runs from a bare checkout.
    """
    try:
        manifest = json.loads(
            (plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return "0+unknown"
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) and version.strip() else "0+unknown"


# Build identity: a running server from another plugin build is replaced,
# because it serves a different viewer than the one asked for.
VERSION = _plugin_version(PLUGIN_ROOT)

# --- Launch key and URL selection -------------------------------------------

# The per-launch key travels to the detached child in this environment
# variable (never argv) and to the server in this request header.
KEY_ENV = "PACT_VIZ_KEY"
KEY_HEADER = "X-PACT-Key"
# bin/pact-viz exports its resolved path here so the Stop: line is runnable.
LAUNCHER_ENV = "PACT_VIZ_LAUNCHER"

PROJECT_LABEL_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
SESSION_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
# The hooks' on-disk slug rule (hooks/shared/pact_context._UNSAFE_SLUG_CHARS_RE).
SLUG_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
# A --session prefix must be at least this long to select a session.
SESSION_PREFIX_MIN = 8

# The server is loopback-only by construction. There is no --host flag and no
# --allow-remote escape hatch; this is the single bind address the server
# accepts and every refusal message names it.
LOOPBACK_HOST = "127.0.0.1"

MIN_PYTHON: tuple[int, int] = (3, 12)

# --- Response caps ----------------------------------------------------------

MAX_EVENTS = 6000
MAX_HANDOFFS = 400
MAX_TASKS = 400
MAX_FIELD_CHARS = 4000
MAX_TITLE_CHARS = 240
MAX_DETAIL_CHARS = 500
MAX_WARNINGS = 20
MAX_FINDINGS = 500
MAX_ALERTS = 200
MAX_REVIEWS = 300
MAX_COMMITS = 2000
MAX_CHECKPOINTS = 500
MAX_HEALTH_FAILURES = 200
MAX_GATE_RULES = 12
MAX_GATE_TOOLS = 40
MAX_AGENTS = 200
MAX_VARIETY_HISTORY = 500
MAX_REVIEWERS = 40
MAX_SESSIONS_LIMIT = 2000
DEFAULT_SESSIONS_LIMIT = 500

# --- Journal layout ---------------------------------------------------------

PACT_SESSIONS_DIRNAME = "pact-sessions"
JOURNAL_FILENAME = "session-journal.jsonl"
TEAMS_DIRNAME = "teams"
TASKS_DIRNAME = "tasks"
TEAM_CONFIG_FILENAME = "config.json"
ROSTER_FILENAME = "pact-roster.jsonl"

# Event types that make a run an orchestration rather than a bare session.
# THE single marker set: the full model's run_kind derivation and the cheap
# session summary's tail scan both read it, so the two can never disagree
# about what counts as orchestration (session_end is deliberately absent —
# every run ends, so it marks nothing).
ORCHESTRATION_MARKER_TYPES: frozenset[str] = frozenset(
    {
        "phase_transition",
        "agent_dispatch",
        "checkpoint",
        "agent_handoff",
        "dispatch_decision",
        "commit",
    }
)

# --- Time windows -----------------------------------------------------------

# A journal whose mtime is this fresh and which has no session_end is
# considered live. 300s keeps a short pause from flipping a run to "ended"
# while still clearing after a real stop.
LIVE_WINDOW_S = 300.0

# Head/tail windows for the cheap /api/sessions summary: bounded reads only,
# never a full journal parse.
SUMMARY_HEAD_BYTES = 64 * 1024
SUMMARY_TAIL_BYTES = 128 * 1024

# --- HTTP / static ----------------------------------------------------------

CSP = (
    "default-src 'self'; img-src 'self' data:; "
    "style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-cache"),
)

STATIC_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

JSON_CONTENT_TYPE = "application/json; charset=utf-8"

# A journal larger than this is refused a full parse (read_journal answers an
# empty read with a warning) so a runaway multi-GB journal cannot balloon the
# server's RSS; summaries keep their own smaller head/tail windows below.
MAX_JOURNAL_BYTES = 64 * 1024 * 1024


def _variety_int(value: object) -> int | None:
    """Best-effort int for a variety dimension (rejects bools and text).

    An older ``{"score": n, "rationale": ...}`` dimension reads its score.
    """
    if isinstance(value, Mapping):
        value = value.get("score")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def normalize_variety(value: object) -> dict | None:
    """Project a variety payload onto the four dimensions plus total.

    Location: settings.normalize_variety.
    Summary: Accepts {"novelty","scope","uncertainty","risk","total"}; missing
             dimensions stay 0, a missing total is the dimension sum. Returns
             None unless the payload carries at least one dimension or total,
             so a UI can distinguish "no assessment" from "zero".
    Used by: classify (variety renderers), model (tasks, checkpoints, variety
             block), roster (task-store records).
    """
    if not isinstance(value, Mapping):
        return None
    dims = ("novelty", "scope", "uncertainty", "risk")
    projected: dict[str, int] = {}
    found = False
    for name in dims:
        number = _variety_int(value.get(name))
        if number is None:
            projected[name] = 0
        else:
            projected[name] = max(0, min(99, number))
            found = True
    total = _variety_int(value.get("total"))
    if total is None:
        total = sum(projected[name] for name in dims)
    projected["total"] = max(0, min(999, total))
    if not found and not projected["total"]:
        return None
    return {name: projected[name] for name in (*dims, "total")}


@dataclass(frozen=True)
class RootSpec:
    """One config root the server may read journals from.

    Location: settings.RootSpec.
    Summary: ``id`` is the stable token the API uses in query parameters
             (``~/.claude`` when the root lives under the home directory,
             an absolute path otherwise); ``label`` is the display name;
             ``path`` is the absolute directory; ``default`` marks the root
             used when a request omits ``root``.
    Used by: discovery, model, server, cli.
    """

    id: str
    label: str
    path: Path
    default: bool = False

    def as_dict(self) -> dict:
        """The roots entry shape used by /api/health and /api/sessions."""
        return {
            "id": self.id,
            "label": self.label,
            "path": str(self.path),
            "journals": 0,
            "default": self.default,
        }


def python_version_ok(version_info: object = sys.version_info) -> bool:
    """True when the interpreter is Python >= MIN_PYTHON.

    Location: settings.python_version_ok.
    Summary: One pure predicate shared by the CLI entry point so the floor is
             testable without re-execing an interpreter.
    Used by: cli.
    """
    try:
        major = int(version_info[0])  # type: ignore[index]
        minor = int(version_info[1])  # type: ignore[index]
    except (TypeError, IndexError, ValueError):
        return False
    return (major, minor) >= MIN_PYTHON


def _env_config_dir(environment: Mapping[str, str]) -> str:
    """$CLAUDE_CONFIG_DIR, stripped: a set-but-blank value is UNSET.

    Location: settings._env_config_dir.
    Summary: Mirrors hooks/shared/paths.get_claude_config_dir, which strips
             before its own truthiness test. Without the strip a
             whitespace-only value passes the truthiness test and resolves to
             the process working directory, so the dashboard would read a
             different root than the hooks that write the journals.
    Used by: resolve_roots, default_state_dir.
    """
    return (environment.get("CLAUDE_CONFIG_DIR") or "").strip()


def _expanded(path: str | os.PathLike[str], home: Path) -> Path:
    """Expand a user-supplied path and make it absolute (never resolved).

    ``~`` and ``~/x`` expand against the INJECTED ``home``, exactly as
    hooks/shared/paths.get_claude_config_dir does. Using os.path.expanduser
    for those two forms reads the process HOME instead, so a test that passes
    ``home=tmp_path`` would silently touch the operator's real home, and the
    two resolvers would disagree whenever the injected home is not the real
    one. Bare ``~user`` forms still go through expanduser: they need the
    passwd database, and the hooks do not support them either.
    """
    text = os.fspath(path)
    if text == "~":
        text = os.fspath(home)
    elif text.startswith("~/"):
        text = os.fspath(home / text[2:])
    elif text.startswith("~"):
        text = os.path.expanduser(text)
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return _normalise(candidate)


def _normalise(path: Path) -> Path:
    """Absolute path with '.'/'..' collapsed but symlinks left alone."""
    return Path(os.path.abspath(os.fspath(path)))


def compact_path(path: Path, home: Path) -> str:
    """Render ``path`` with a leading ``~`` when it lives under ``home``.

    Location: settings.compact_path.
    Summary: Produces the stable root id used by the API; paths outside the
             home directory stay absolute. Purely lexical: no symlink
             resolution, so ids do not change when a checkout moves.
    Used by: resolve_roots, cli (state messages).
    """
    candidate = _normalise(path)
    try:
        relative = candidate.relative_to(_normalise(home))
    except ValueError:
        return str(candidate)
    if str(relative) in ("", "."):
        return "~"
    return "~/" + relative.as_posix()


def _root_spec(path: Path, home: Path, default: bool) -> RootSpec:
    """Build a RootSpec from an absolute config-root path."""
    label = path.name or str(path)
    return RootSpec(
        id=compact_path(path, home),
        label=label,
        path=_normalise(path),
        default=default,
    )


def resolve_roots(
    root_args: Sequence[str | os.PathLike[str]] = (),
    *,
    all_roots: bool = False,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[RootSpec]:
    """Resolve the ordered list of config roots the server will read.

    Location: settings.resolve_roots.
    Summary: ``--root`` (repeatable) wins; otherwise $CLAUDE_CONFIG_DIR;
             otherwise ~/.claude. ``--all-roots`` appends every sibling
             ``~/.claude*`` directory under the home directory that contains a
             ``pact-sessions/`` child. The first root is the default. Paths are
             de-duplicated by lexical absolute path while preserving order.
    Used by: discovery, cli.
    """
    environment = os.environ if env is None else env
    home_dir = Path.home() if home is None else home
    if root_args:
        paths = [_expanded(item, home_dir) for item in root_args]
    else:
        env_root = _env_config_dir(environment)
        paths = [_expanded(env_root, home_dir) if env_root else _normalise(home_dir / ".claude")]
    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    paths = deduped
    roots = [_root_spec(paths[0], home_dir, True)]
    for path in paths[1:]:
        roots.append(_root_spec(path, home_dir, False))

    if all_roots:
        seen = {root.path for root in roots}
        try:
            siblings = sorted(home_dir.glob(".claude*"))
        except OSError:
            siblings = []
        for sibling in siblings:
            try:
                if not sibling.is_dir():
                    continue
                candidate = _normalise(sibling)
                if candidate in seen:
                    continue
                if not (candidate / PACT_SESSIONS_DIRNAME).is_dir():
                    continue
            except OSError:
                continue
            roots.append(_root_spec(candidate, home_dir, False))
            seen.add(candidate)
    return roots


def default_state_dir(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """``<config>/pact-viz``: where the detached-server state file lives.

    Location: settings.default_state_dir.
    Summary: The config root is $CLAUDE_CONFIG_DIR or ~/.claude (never a
             --root the user passed for reading, so two servers reading
             different roots still agree on where "the server" lives).
    Used by: cli.
    """
    environment = os.environ if env is None else env
    home_dir = Path.home() if home is None else home
    env_root = _env_config_dir(environment)
    base = _expanded(env_root, home_dir) if env_root else _normalise(home_dir / ".claude")
    return base / "pact-viz"


def state_file(state_dir: Path) -> Path:
    """The single state file inside a state directory.

    Location: settings.state_file.
    Summary: ``server.json``; the directory itself is created 0700 and the file
             0600 by the CLI. One instance per config root by design.
    Used by: cli, server tests.
    """
    return Path(state_dir) / "server.json"
