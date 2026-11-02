"""Location: pact-plugin/visualizer/server.py
Summary: The loopback-only HTTP server: a ThreadingHTTPServer bound hard to
         127.0.0.1, exposing the four JSON API routes plus the static page and
         an allowlisted /static/ tree, with the contract's security headers on
         every response.
Used by: visualizer.cli (foreground/detached serving), tests (create_server
         seam), visualizer.__main__ indirectly.

The server is READ-ONLY and local-only: no request handler writes to a PACT
store, makes an outbound request, or reaches a non-loopback address. The
``create_server`` factory is the test seam: it binds, sets every attribute the
handlers use, and returns the server without serving, so tests can run an
ephemeral port and shut it down without leaving a listener behind.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from . import demo, discovery, journal, model, settings
from .settings import (
    APP_NAME,
    DEFAULT_SESSIONS_LIMIT,
    JSON_CONTENT_TYPE,
    LOOPBACK_HOST,
    MAX_SESSIONS_LIMIT,
    SECURITY_HEADERS,
    STATIC_MIME_TYPES,
    VERSION,
    RootSpec,
)

# Only GET is part of the contract. Everything else (including HEAD, which
# BaseHTTPRequestHandler would otherwise answer like GET) is a 405 so the
# access surface is exactly the four API routes plus the two static routes.
_ALLOWED_METHODS = "GET"

# Root-relative asset aliases (CONTRACT static routes): the page served at "/"
# references its assets page-relative, so these EXACT prefixes/filenames are
# aliased into static/ with the same containment and MIME rules as /static/.
# This is an explicit allowlist, not a general root static mount: any other
# root path stays a 404.
_ROOT_ASSET_FILES: frozenset[str] = frozenset({"/favicon.svg", "/dev-demo.json"})
_ROOT_ASSET_PREFIXES: tuple[str, ...] = ("/css/", "/js/")


def _static_root() -> Path:
    """The visualizer package's static directory (resolved lazily for tests)."""
    return Path(__file__).resolve().parent / "static"


def _now() -> datetime:
    """UTC wall clock for generated_at/uptime."""
    return datetime.now(timezone.utc)


class MissionControlServer(ThreadingHTTPServer):
    """The bound server plus the small amount of state handlers need.

    Location: server.MissionControlServer.
    Summary: Carries the resolved roots, the random instance token, the
             server's start time and the CLI's list filters. Threading
             (daemon threads) keeps a slow static read from blocking the API
             and lets shutdown return promptly.
    Used by: create_server, cli, tests.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        roots: Sequence[RootSpec],
        instance_id: str,
        verbose: bool = False,
        filters: Mapping[str, str] | None = None,
    ) -> None:
        self.roots: list[RootSpec] = list(roots)
        self.instance_id = instance_id
        self.verbose = bool(verbose)
        self.filters = dict(filters or {})
        self.started_at = _now()
        self.started_monotonic = time.monotonic()
        super().__init__(address, handler_class)

    @property
    def uptime_s(self) -> float:
        """Seconds served since create_server returned."""
        return max(0.0, time.monotonic() - self.started_monotonic)

    @property
    def url(self) -> str:
        """The canonical loopback URL this server answers on."""
        return f"http://{LOOPBACK_HOST}:{self.server_port}/"


class MissionControlHandler(BaseHTTPRequestHandler):
    """Per-request router for the Mission Control API + static page.

    Location: server.MissionControlHandler.
    Summary: Every response (JSON, static, error) gets the security headers,
             the correct Content-Type and Content-Length, and a JSON
             ``{"ok": false, "error": ...}`` body on 400/404/405. Query
             parameters are validated before any file or journal is touched.
    Used by: MissionControlServer.
    """

    server_version = f"{APP_NAME}/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- lifecycle -------------------------------------------------------

    @property
    def mission_server(self) -> MissionControlServer:
        """Typed access to the owning server (BaseHTTPRequestHandler's is generic)."""
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence per-request logging unless the CLI was run with --verbose."""
        if getattr(self.mission_server, "verbose", False):
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        """Route one GET request."""
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 — a handler crash must still answer JSON
            if getattr(self.mission_server, "verbose", False):
                self.log_error("unhandled request error: %s", self.path)
            self._send_json(500, {"ok": False, "error": "internal error"})

    def _method_not_allowed(self) -> None:
        """Answer any non-GET method with the contract's 405 JSON body."""
        self._send_json(
            405,
            {"ok": False, "error": "method not allowed"},
            extra_headers=(("Allow", _ALLOWED_METHODS),),
        )

    for _method in ("do_HEAD", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_OPTIONS"):
        locals()[_method] = _method_not_allowed

    # -- routing ---------------------------------------------------------

    def _route_get(self) -> None:
        """Split the URL and dispatch to one route (percent-decoded once)."""
        split = urlsplit(self.path)
        # Decode EXACTLY ONCE here so every route (static, aliases, traversal
        # checks) sees the same effective path; nothing decodes again later,
        # which keeps %2e%2e from being re-expanded after a check.
        path = unquote(split.path)
        if path == "/":
            self._serve_index()
        elif path == "/api/health":
            self._serve_health()
        elif path == "/api/sessions":
            self._serve_sessions(split.query)
        elif path == "/api/session":
            self._serve_session(split.query)
        elif path == "/api/demo":
            self._serve_demo(split.query)
        elif path.startswith("/static/"):
            self._serve_static_relative(path[len("/static/"):])
        else:
            alias = _alias_relative_path(path)
            if alias is not None:
                self._serve_static_relative(alias)
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

    # -- response helpers -------------------------------------------------

    def _send_headers(
        self,
        status: int,
        content_type: str | None,
        length: int | None,
        extra_headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Write the status line and every header, security headers first."""
        self.send_response(status)
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()

    def _send_json(
        self,
        status: int,
        payload: Mapping[str, Any],
        extra_headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Send one JSON response (UTF-8, no-store, security headers)."""
        try:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            body = b'{"ok":false,"error":"serialization error"}'
            status = 500
        self._send_headers(status, JSON_CONTENT_TYPE, len(body), extra_headers)
        self.wfile.write(body)

    def _send_error(self, status: int, reason: str) -> None:
        """Send the contract's short error body."""
        self._send_json(status, {"ok": False, "error": reason})

    def _send_file(
        self,
        path: Path,
        content_type: str,
        extra_headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Read and send one static file, or a 404 when it is unreadable."""
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error(404, "not found")
            return
        self._send_headers(200, content_type, len(body), extra_headers)
        self.wfile.write(body)

    # -- API routes -------------------------------------------------------

    def _serve_health(self) -> None:
        """GET /api/health -> identity, pid, instance token, uptime, roots."""
        server = self.mission_server
        payload = model.build_health(
            server.roots,
            pid=os.getpid(),
            instance_id=server.instance_id,
            uptime_s=server.uptime_s,
            now=_now(),
        )
        self._send_json(200, payload)

    def _serve_sessions(self, query: str) -> None:
        """GET /api/sessions?limit=N -> the cheap session list."""
        params = parse_qs(query, keep_blank_values=True)
        limit = DEFAULT_SESSIONS_LIMIT
        raw_limit = _first_param(params, "limit")
        if raw_limit is not None:
            parsed = _parse_limit(raw_limit)
            if parsed is None:
                self._send_error(400, "limit must be an integer between 1 and "
                                      f"{MAX_SESSIONS_LIMIT}")
                return
            limit = parsed
        server = self.mission_server
        filters = server.filters
        payload = model.build_sessions_list(
            server.roots,
            limit=limit,
            project_filter=filters.get("project", ""),
            session_filter=filters.get("session", ""),
            now=_now(),
        )
        self._send_json(200, payload)

    def _serve_session(self, query: str) -> None:
        """GET /api/session?root=&slug=&session_id=&since= -> full model or unchanged."""
        params = parse_qs(query, keep_blank_values=True)
        slug = _first_param(params, "slug")
        session_id = _first_param(params, "session_id")
        if not slug or not session_id:
            self._send_error(400, "slug and session_id are required")
            return
        if not discovery.valid_component(slug) or not discovery.valid_component(session_id):
            self._send_error(400, "invalid slug or session_id")
            return
        root_param = _first_param(params, "root")
        server = self.mission_server
        if root_param is not None and discovery.find_root(server.roots, root_param) is None:
            self._send_error(400, "unknown root")
            return
        ref = discovery.find_session(server.roots, slug, session_id, root_param)
        if ref is None:
            self._send_error(404, "session not found")
            return
        stat = journal.journal_stat(ref.journal)
        if stat is not None:
            current_etag = journal.make_etag(stat[0], stat[1])
            since = _first_param(params, "since")
            if since is not None and since == current_etag:
                self._send_json(
                    200,
                    {"ok": True, "unchanged": True, "etag": current_etag},
                    extra_headers=(("ETag", current_etag),),
                )
                return
        payload = model.build_model(ref, now=_now())
        self._send_json(200, payload, extra_headers=(("ETag", payload.get("etag", "")),))

    def _serve_demo(self, query: str) -> None:
        """GET /api/demo?since=demo-v1 -> the deterministic scripted model."""
        params = parse_qs(query, keep_blank_values=True)
        since = _first_param(params, "since")
        if since is not None and since == settings.DEMO_ETAG:
            self._send_json(
                200,
                {"ok": True, "unchanged": True, "etag": settings.DEMO_ETAG},
                extra_headers=(("ETag", settings.DEMO_ETAG),),
            )
            return
        payload = demo.build_demo_model()
        self._send_json(200, payload, extra_headers=(("ETag", settings.DEMO_ETAG),))

    # -- static routes ----------------------------------------------------

    def _serve_index(self) -> None:
        """GET / -> static/index.html."""
        index = _static_root() / "index.html"
        if not index.is_file():
            self._send_error(404, "not found")
            return
        self._send_file(index, STATIC_MIME_TYPES[".html"])

    def _serve_static_relative(self, relative: str) -> None:
        """Serve one already-decoded static-relative path with containment guards.

        Location: MissionControlHandler._serve_static_relative.
        Summary: The single implementation behind both ``/static/<path>`` and
                 the root asset aliases: rejects NUL bytes, absolute paths,
                 empty/"."/".." segments (so no root read or escape), resolves
                 the candidate and requires it to stay under static/, then
                 serves it only when its extension is allowlisted. Anything
                 else is a 404 with the contract's error body.
        Used by: _route_get (static + aliases).
        """
        if not relative or "\x00" in relative:
            self._send_error(404, "not found")
            return
        normalized = relative.replace("\\", "/")
        if normalized.startswith("/"):
            self._send_error(404, "not found")
            return
        segments = normalized.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            self._send_error(404, "not found")
            return
        root = _static_root()
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root.resolve())
        except (OSError, ValueError):
            self._send_error(404, "not found")
            return
        content_type = STATIC_MIME_TYPES.get(candidate.suffix.lower())
        if content_type is None:
            self._send_error(404, "not found")
            return
        if not candidate.is_file():
            self._send_error(404, "not found")
            return
        self._send_file(candidate, content_type)


def _alias_relative_path(path: str) -> str | None:
    """Map an allowlisted root asset alias to its static-relative file.

    Location: server._alias_relative_path.
    Summary: Only ``/css/<filename>``, ``/js/<filename>`` (a single filename,
             never a nested path), ``/favicon.svg`` and ``/dev-demo.json`` are
             aliased; anything else returns None and stays a 404. The caller has
             already percent-decoded the path exactly once.
    Used by: MissionControlHandler._route_get.
    """
    if path in _ROOT_ASSET_FILES:
        return path.lstrip("/")
    for prefix in _ROOT_ASSET_PREFIXES:
        if not path.startswith(prefix):
            continue
        filename = path[len(prefix):]
        if not filename or "/" in filename or "\\" in filename:
            return None
        return f"{prefix[1:-1]}/{filename}"
    return None


def _first_param(params: Mapping[str, list[str]], key: str) -> str | None:
    """First non-empty value for a query parameter, or None."""
    values = params.get(key)
    if not values:
        return None
    for value in values:
        if value != "":
            return value
    return values[0]


def _parse_limit(value: str) -> int | None:
    """Parse the /api/sessions limit parameter (1..MAX_SESSIONS_LIMIT)."""
    text = value.strip()
    if not text.lstrip("-").isdigit():
        return None
    try:
        limit = int(text)
    except ValueError:
        return None
    if limit < 1 or limit > MAX_SESSIONS_LIMIT:
        return None
    return limit


def create_server(
    roots: Sequence[RootSpec],
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
    instance_id: str | None = None,
    verbose: bool = False,
    filters: Mapping[str, str] | None = None,
) -> MissionControlServer:
    """Bind and return a Mission Control server without serving it.

    Location: server.create_server.
    Summary: The test seam. Refuses any host other than the hard-coded
             127.0.0.1 loopback literal, binds, generates a random instance
             token when one is not supplied (the CLI supplies one so it can
             health-check its own detached child), and verifies the bound
             socket address really is loopback.
    Used by: cli (foreground/detached), tests.
    """
    if host != LOOPBACK_HOST:
        raise ValueError(
            f"PACT Mission Control is loopback-only; refusing to bind {host!r} "
            f"(the only allowed address is {LOOPBACK_HOST})"
        )
    try:
        port_number = int(port)
    except (TypeError, ValueError) as error:
        raise ValueError("port must be an integer") from error
    if port_number < 0 or port_number > 65535:
        raise ValueError("port must be between 0 and 65535")
    token = instance_id if instance_id else secrets.token_hex(16)
    server = MissionControlServer(
        (LOOPBACK_HOST, port_number),
        MissionControlHandler,
        roots=roots,
        instance_id=token,
        verbose=verbose,
        filters=filters,
    )
    bound_host = str(server.server_address[0])
    if bound_host != LOOPBACK_HOST:
        server.server_close()
        raise ValueError(
            f"refusing to serve on non-loopback address {bound_host!r}"
        )
    return server


def server_url(server: MissionControlServer, *, demo_mode: bool = False) -> str:
    """The user-facing URL for a bound server, with ?demo=1 when requested."""
    base = server.url
    return f"{base}?demo=1" if demo_mode else base
