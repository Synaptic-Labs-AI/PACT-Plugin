"""Location: pact-plugin/tests/test_visualizer_server.py
Summary: HTTP + CLI acceptance tests for PACT Mission Control: every API and
         static route over a real ephemeral loopback listener, security
         headers, 400/404/405 handling, path-traversal refusal, loopback-only
         binding, since/ETag semantics, and the direct CLI (--list/--status/
         --stop/--detach/--version + the bin/pact-viz wrapper), including the
         detached lifecycle and state-file permissions. Every root is a
         temporary directory; no test reads the operator's real ~/.claude.
Used by: pytest (server half of the visualizer backend acceptance suite).
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from visualizer import cli, demo, journal, model, server as server_module, settings

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
BIN_WRAPPER = PLUGIN_ROOT / "bin" / "pact-viz"
STATIC_DIR = PLUGIN_ROOT / "visualizer" / "static"
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def stamp(minute: int = 0) -> str:
    base = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
    return (base.replace(minute=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_journal(root: Path, slug: str, session_id: str, events: list[dict]) -> Path:
    session_dir = root / "pact-sessions" / slug / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session-journal.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )
    return path


def sample_events() -> list[dict]:
    return [
        {
            "v": 1,
            "type": "session_start",
            "ts": "2026-11-01T10:00:00Z",
            "session_id": "sess-1",
            "project_dir": "/Users/dev/PACT-prompt",
            "team": "session-abc",
        },
        {
            "v": 1,
            "type": "phase_transition",
            "ts": "2026-11-01T10:01:00Z",
            "phase": "PREPARE",
            "status": "started",
        },
        {
            "v": 1,
            "type": "agent_dispatch",
            "ts": "2026-11-01T10:02:00Z",
            "agent": "preparer",
            "task_id": "2",
            "phase": "PREPARE",
        },
        {
            "v": 1,
            "type": "agent_handoff",
            "ts": "2026-11-01T10:03:00Z",
            "agent": "preparer",
            "task_id": "2",
            "task_subject": "preparer: research",
            "handoff": {
                "produced": "docs/spec.md",
                "decisions": "d",
                "reasoning_chain": "r",
                "uncertainty": "u",
                "integration": "i",
                "open_questions": "o",
            },
        },
        {
            "v": 1,
            "type": "commit",
            "ts": "2026-11-01T10:04:00Z",
            "sha": "abc1234",
            "message": "Add PREPARE research",
            "phase": "PREPARE",
        },
        {
            "v": 1,
            "type": "session_end",
            "ts": "2026-11-01T10:05:00Z",
            "source": "wrap-up",
            "pr_state": "MERGED",
            "pr_number": 7,
        },
    ]


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    root.mkdir(parents=True, exist_ok=True)
    write_journal(root, "PACT-prompt", "sess-1", sample_events())
    return root


def roots_for(root: Path, tmp_path: Path):
    return settings.resolve_roots(
        [root], env={"CLAUDE_CONFIG_DIR": str(root)}, home=tmp_path
    )


class HttpResponse:
    """A tiny structured response from the raw http.client request."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


# Launch key of each test server, by port, so every /api request carries the
# key the way the page does. A test passes key=None (no header) or a wrong
# key to exercise the refusal.
_SERVER_KEYS: dict[int, str] = {}
_KEY_OF_SERVER = object()


def new_server(roots, **kwargs):
    """create_server, with the server's key recorded for http_request."""
    srv = server_module.create_server(roots, **kwargs)
    _SERVER_KEYS[srv.server_port] = srv.key
    return srv


def http_request(
    port: int,
    path: str,
    method: str = "GET",
    timeout: float = 5.0,
    key: object = _KEY_OF_SERVER,
) -> HttpResponse:
    """One raw HTTP request (no redirect/proxy/normalization layer)."""
    if key is _KEY_OF_SERVER:
        key = _SERVER_KEYS.get(port)
    headers = {"X-PACT-Key": key} if isinstance(key, str) else {}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return HttpResponse(response.status, headers, body)
    finally:
        connection.close()


@pytest.fixture
def live_server(tmp_path):
    """A real server on an ephemeral loopback port, torn down with the test."""
    root = make_root(tmp_path)
    roots = roots_for(root, tmp_path)
    srv = new_server(roots)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, thread
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Health + security headers
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_payload(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/api/health")
        assert response.status == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["name"] == "pact-mission-control"
        assert payload["version"] == settings.VERSION
        assert isinstance(payload["pid"], int)
        assert len(payload["instance_id"]) == 32
        int(payload["instance_id"], 16)  # hex
        assert payload["uptime_s"] >= 0
        assert "demo" not in payload
        assert payload["plugin_root"] == str(PLUGIN_ROOT)
        assert payload["demo_available"] is True
        assert payload["roots"][0]["default"] is True
        assert payload["roots"][0]["journals"] == 1
        assert payload["roots"][0]["path"].endswith(".claude")
        assert payload["generated_at"].endswith("Z")

    def test_security_headers_on_json(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/api/health")
        assert response.headers["content-type"] == "application/json; charset=utf-8"
        assert response.headers["content-security-policy"] == settings.CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"

    def test_security_headers_on_errors(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/api/does-not-exist")
        assert response.status == 404
        assert response.headers["content-security-policy"] == settings.CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.json() == {"ok": False, "error": "not found"}


class TestLaunchKey:
    """Every /api route needs the per-launch key; static routes do not."""

    _API_PATHS = (
        "/api/health",
        "/api/sessions",
        "/api/session?slug=PACT-prompt&session_id=sess-1",
        "/api/session?slug=PACT-prompt&session_id=missing",
        "/api/demo",
        "/api/no-such-route",
    )

    @pytest.mark.parametrize("path", _API_PATHS)
    def test_an_unkeyed_api_request_is_401_before_any_route_work(self, live_server, path):
        srv, _thread = live_server
        response = http_request(srv.server_port, path, key=None)
        assert response.status == 401, path
        assert response.json() == {"ok": False, "error": "key required"}
        assert response.headers["content-security-policy"] == settings.CSP

    @pytest.mark.parametrize(
        "wrong", ["", "x", "PREFIX", "SUFFIX", "\u00e9t\u00e9"], ids=["empty", "short", "prefix", "suffix", "latin1"]
    )
    def test_a_wrong_key_is_401_never_500(self, live_server, wrong):
        srv, _thread = live_server
        candidate = {"PREFIX": srv.key[:-1], "SUFFIX": srv.key + "x"}.get(wrong, wrong)
        response = http_request(srv.server_port, "/api/health", key=candidate)
        assert response.status == 401
        assert response.json() == {"ok": False, "error": "key required"}

    def test_the_right_key_is_accepted_on_every_route(self, live_server):
        srv, _thread = live_server
        for path in ("/api/health", "/api/sessions", "/api/demo",
                     "/api/session?slug=PACT-prompt&session_id=sess-1"):
            assert http_request(srv.server_port, path).status == 200, path

    @pytest.mark.parametrize("path", ["/", "/favicon.svg", "/static/favicon.svg", "/js/api.js"])
    def test_static_routes_need_no_key(self, live_server, path):
        srv, _thread = live_server
        assert http_request(srv.server_port, path, key=None).status == 200, path

    def test_each_server_mints_its_own_key(self, tmp_path):
        roots = roots_for(make_root(tmp_path), tmp_path)
        first = server_module.create_server(roots)
        second = server_module.create_server(roots)
        try:
            assert first.key and second.key and first.key != second.key
            assert len(first.key) >= 32
        finally:
            first.server_close()
            second.server_close()

    def test_a_supplied_key_is_the_one_enforced(self, tmp_path):
        roots = roots_for(make_root(tmp_path), tmp_path)
        srv = new_server(roots, key="k" * 43)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            assert srv.key == "k" * 43
            assert http_request(srv.server_port, "/api/health").status == 200
            assert http_request(srv.server_port, "/api/health", key="j" * 43).status == 401
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# /api/sessions
# ---------------------------------------------------------------------------


class TestSessionsRoute:
    def test_sessions_payload(self, live_server, tmp_path):
        srv, _thread = live_server
        write_journal(
            srv.roots[0].path,
            "Second",
            "sess-2",
            [
                {"v": 1, "type": "session_start", "ts": "2026-11-02T10:00:00Z",
                 "project_dir": "/Users/dev/Second"},
                {"v": 1, "type": "session_end", "ts": "2026-11-02T10:01:00Z"},
            ],
        )
        response = http_request(srv.server_port, "/api/sessions?all=1")
        assert response.status == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["default_root"] == srv.roots[0].id
        assert [session["session_id"] for session in payload["sessions"]] == [
            "sess-2",
            "sess-1",
        ]
        newest = payload["sessions"][0]
        assert newest["project"] == "Second"
        assert newest["status"] == "ended"
        assert newest["is_live"] is False
        assert newest["last_event_type"] == "session_end"
        assert isinstance(newest["size_bytes"], int)

    def test_trivial_runs_are_hidden_unless_all_is_asked_for(self, live_server):
        srv, _thread = live_server
        path = write_journal(
            srv.roots[0].path, "Tiny", "sess-tiny",
            [{"v": 1, "type": "session_start", "ts": "2026-11-02T10:00:00Z",
              "session_id": "sess-tiny", "project_dir": "/Users/dev/Tiny"},
             {"v": 1, "type": "session_end", "ts": "2026-11-02T10:01:00Z"}],
        )
        os.utime(path, (1_700_000_000, 1_700_000_000))
        default = http_request(srv.server_port, "/api/sessions").json()
        assert "sess-tiny" not in [row["session_id"] for row in default["sessions"]]
        assert default["hidden_trivial"] >= 1
        assert "all" not in default["filters"]
        everything = http_request(srv.server_port, "/api/sessions?all=1").json()
        assert "sess-tiny" in [row["session_id"] for row in everything["sessions"]]
        assert everything["filters"]["all"] == "1"
        assert everything["total"] >= len(everything["sessions"])

    def test_limit_parameter(self, live_server):
        srv, _thread = live_server
        assert len(http_request(srv.server_port, "/api/sessions?limit=1").json()["sessions"]) == 1
        for bad in ("limit=abc", "limit=0", "limit=-3", "limit=999999"):
            response = http_request(srv.server_port, f"/api/sessions?{bad}")
            assert response.status == 400, bad
            assert response.json()["ok"] is False


# ---------------------------------------------------------------------------
# /api/session
# ---------------------------------------------------------------------------


class TestSessionRoute:
    def _path(self, **overrides) -> str:
        params = {"slug": "PACT-prompt", "session_id": "sess-1"}
        params.update(overrides)
        from urllib.parse import urlencode

        return "/api/session?" + urlencode(params)

    def test_full_model(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, self._path())
        assert response.status == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["source"]["kind"] == "real"
        assert payload["source"]["slug"] == "PACT-prompt"
        assert payload["source"]["journal"] == (
            "pact-sessions/PACT-prompt/sess-1/session-journal.jsonl"
        )
        assert payload["meta"]["project"] == "PACT-prompt"
        assert payload["phases"]["order"] == ["PREPARE", "ARCHITECT", "CODE", "TEST"]
        assert payload["phases"]["current"] == "PREPARE"
        assert payload["events"]
        assert payload["handoffs"][0]["schema_complete"] is True
        assert payload["wrapup"]["pr_state"] == "MERGED"
        assert response.headers["etag"] == f'"{payload["etag"]}"'
        assert payload["metrics"]["handoffs"] == 1

    def test_since_returns_unchanged(self, live_server):
        srv, _thread = live_server
        first = http_request(srv.server_port, self._path())
        etag = first.json()["etag"]
        unchanged = http_request(srv.server_port, self._path(since=etag))
        assert unchanged.status == 200
        assert unchanged.json() == {"ok": True, "unchanged": True, "etag": etag}
        assert unchanged.headers["etag"] == f'"{etag}"'
        stale = http_request(srv.server_port, self._path(since="1-1"))
        assert stale.status == 200
        assert stale.json()["ok"] is True
        assert stale.json()["etag"] == etag
        assert stale.json()["events"]

    def test_missing_and_invalid_parameters(self, live_server):
        srv, _thread = live_server
        for path in (
            "/api/session",
            "/api/session?slug=PACT-prompt",
            "/api/session?session_id=sess-1",
            "/api/session?slug=..&session_id=sess-1",
            "/api/session?slug=PACT-prompt&session_id=..",
            "/api/session?slug=PACT-prompt&session_id=sess-1&root=nonexistent-root",
        ):
            response = http_request(srv.server_port, path)
            assert response.status == 400, path
            assert response.json()["ok"] is False

    def test_unknown_session_is_404(self, live_server):
        srv, _thread = live_server
        response = http_request(
            srv.server_port, "/api/session?slug=PACT-prompt&session_id=missing"
        )
        assert response.status == 404
        assert response.json() == {"ok": False, "error": "session not found"}

    def test_root_parameter_selects_root(self, live_server):
        srv, _thread = live_server
        from urllib.parse import urlencode

        path = "/api/session?" + urlencode(
            {"root": srv.roots[0].id, "slug": "PACT-prompt", "session_id": "sess-1"}
        )
        response = http_request(srv.server_port, path)
        assert response.status == 200
        assert response.json()["meta"]["project"] == "PACT-prompt"

    def test_concurrent_requests_are_all_served_consistently(self, live_server):
        """The server is a ThreadingHTTPServer; nothing else proves that under
        simultaneous polls every request is answered from the same snapshot."""
        srv, _thread = live_server
        path = "/api/session?slug=PACT-prompt&session_id=sess-1"
        results: list[tuple[int, bytes]] = []
        lock = threading.Lock()

        def fetch() -> None:
            response = http_request(srv.server_port, path)
            with lock:
                results.append((response.status, response.body))

        workers = [threading.Thread(target=fetch) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        assert not any(worker.is_alive() for worker in workers)
        assert len(results) == 8
        assert {status for status, _body in results} == {200}
        payloads = [json.loads(body) for _status, body in results]
        assert all(payload["ok"] is True for payload in payloads)
        assert len({tuple(event["seq"] for event in payload["events"]) for payload in payloads}) == 1


class TestEtagCoversEveryInput:
    """`since` answers unchanged only while nothing the model reads changed."""

    _PATH = "/api/session?slug=PACT-prompt&session_id=sess-1"

    def _etag(self, srv) -> str:
        return http_request(srv.server_port, self._PATH).json()["etag"]

    def _since(self, srv, etag: str) -> dict:
        return http_request(srv.server_port, f"{self._PATH}&since={etag}").json()

    def test_a_task_file_change_alone_invalidates_since(self, live_server):
        srv, _thread = live_server
        root = srv.roots[0].path
        tasks = root / "tasks" / "session-abc"
        tasks.mkdir(parents=True)
        etag = self._etag(srv)
        assert self._since(srv, etag)["unchanged"] is True
        (tasks / "5.json").write_text(
            json.dumps({"id": "5", "subject": "architect: design", "status": "pending",
                        "description": "", "blocks": [], "blockedBy": []}),
            encoding="utf-8",
        )
        stamp_ns = time.time_ns() + 5_000_000_000
        os.utime(tasks / "5.json", ns=(stamp_ns, stamp_ns))
        fresh = self._since(srv, etag)
        assert fresh.get("unchanged") is not True
        assert any(task["id"] == "5" for task in fresh["tasks"])

    def test_the_clock_alone_can_end_a_quiet_run(self, live_server, monkeypatch):
        """A run with no session_end turns idle as time passes, and a poll
        holding the old token must see the change."""
        srv, _thread = live_server
        root = srv.roots[0].path
        path = write_journal(
            root,
            "PACT-prompt",
            "sess-q",
            [{"v": 1, "type": "session_start", "ts": "2026-11-01T10:00:00Z",
              "session_id": "sess-q", "project_dir": "/Users/dev/PACT-prompt",
              "team": "session-q", "worktree": "", "source": "startup",
              "lead_address": "team-lead"}],
        )
        written = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
        os.utime(path, (written.timestamp(), written.timestamp()))
        quiet = "/api/session?slug=PACT-prompt&session_id=sess-q"
        monkeypatch.setattr(server_module, "_now", lambda: written.replace(minute=1))
        live = http_request(srv.server_port, quiet).json()
        assert live["meta"]["status"] == "live"
        monkeypatch.setattr(server_module, "_now", lambda: written.replace(hour=12))
        later = http_request(srv.server_port, f"{quiet}&since={live['etag']}").json()
        assert later.get("unchanged") is not True
        assert later["meta"]["status"] == "ended"
        assert later["meta"]["ended_by"] == "idle"


class TestSymlinkedSessionDirs:
    """A symlinked slug or session directory is not a session.

    Pinned on BOTH routes: a run must never be fetchable by /api/session
    while invisible in /api/sessions, or listed there and then refused.
    """

    def test_symlinked_slug_is_invisible_on_both_routes(self, live_server, tmp_path):
        srv, _thread = live_server
        outside = tmp_path / "outside"
        write_journal(outside, "ghost", "sess-g", sample_events())
        link = srv.roots[0].path / "pact-sessions" / "ghost"
        link.symlink_to(outside / "pact-sessions" / "ghost", target_is_directory=True)

        listed = http_request(srv.server_port, "/api/sessions").json()["sessions"]
        assert [(s["slug"], s["session_id"]) for s in listed] == [("PACT-prompt", "sess-1")]
        fetched = http_request(srv.server_port, "/api/session?slug=ghost&session_id=sess-g")
        assert fetched.status == 404

    def test_symlinked_session_dir_is_invisible_on_both_routes(self, live_server, tmp_path):
        srv, _thread = live_server
        outside = tmp_path / "outside"
        write_journal(outside, "real", "sess-h", sample_events())
        slug_dir = srv.roots[0].path / "pact-sessions" / "real"
        slug_dir.mkdir(parents=True, exist_ok=True)
        (slug_dir / "sess-h").symlink_to(
            outside / "pact-sessions" / "real" / "sess-h", target_is_directory=True
        )

        listed = http_request(srv.server_port, "/api/sessions").json()["sessions"]
        assert [(s["slug"], s["session_id"]) for s in listed] == [("PACT-prompt", "sess-1")]
        fetched = http_request(srv.server_port, "/api/session?slug=real&session_id=sess-h")
        assert fetched.status == 404


# ---------------------------------------------------------------------------
# /api/demo
# ---------------------------------------------------------------------------


class TestDemoRoute:
    def test_demo_model(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/api/demo")
        assert response.status == 200
        payload = response.json()
        assert payload["etag"] == "demo-v1"
        assert payload["source"]["kind"] == "demo"
        assert payload["source"]["root"] == "demo"
        assert payload["phases"]["completed"] == ["PREPARE", "ARCHITECT", "CODE", "TEST"]
        assert len(payload["agents"]) >= 8
        assert payload["wrapup"]["pr_state"] == "MERGED"
        assert response.headers["etag"] == '"demo-v1"'

    def test_demo_since_unchanged(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/api/demo?since=demo-v1")
        assert response.status == 200
        assert response.json() == {"ok": True, "unchanged": True, "etag": "demo-v1"}
        full = http_request(srv.server_port, "/api/demo?since=other")
        assert full.status == 200
        assert full.json()["source"]["kind"] == "demo"


class TestDemoWithNoSessions:
    def test_demo_available_when_no_real_sessions_exist(self, tmp_path):
        empty = tmp_path / ".claude"
        empty.mkdir()
        roots = roots_for(empty, tmp_path)
        srv = new_server(roots)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            assert http_request(srv.server_port, "/api/sessions").json()["sessions"] == []
            demo_response = http_request(srv.server_port, "/api/demo")
            assert demo_response.status == 200
            assert demo_response.json()["source"]["kind"] == "demo"
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Static routes, traversal and methods
# ---------------------------------------------------------------------------


class TestStaticRoutes:
    def test_index_served(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/")
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["content-security-policy"] == settings.CSP
        assert response.body.startswith(b"<!DOCTYPE html>")
        assert b"<title>PACT // Mission Control</title>" in response.body

    def test_static_files_mime(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/static/favicon.svg")
        assert response.status == 200
        assert response.headers["content-type"] == "image/svg+xml"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_static_json_and_js_allowlisted(self, live_server):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/static/dev-demo.json")
        assert response.status == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_root_asset_aliases(self, live_server):
        """Page-relative refs at `/` are aliased into static/ (CONTRACT carve-out)."""
        srv, _thread = live_server
        aliases = [
            ("/favicon.svg", "favicon.svg", "image/svg+xml"),
            ("/dev-demo.json", "dev-demo.json", "application/json"),
            ("/css/base.css", "css/base.css", "text/css"),
            ("/js/api.js", "js/api.js", "text/javascript"),
        ]
        for url, relative, expected_type in aliases:
            if not (STATIC_DIR / relative).is_file():
                continue
            response = http_request(srv.server_port, url)
            assert response.status == 200, url
            assert response.headers["content-type"].startswith(expected_type), url
            assert response.headers["content-security-policy"] == settings.CSP
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.body == (STATIC_DIR / relative).read_bytes(), url

    def test_traversal_and_unknown_static_paths_are_404(self, live_server):
        srv, _thread = live_server
        for path in (
            "/static/../CONTRACT.md",
            "/static/%2e%2e/CONTRACT.md",
            "/static/..%2fCONTRACT.md",
            "/static/sub/../../CONTRACT.md",
            "/static/%00evil.js",
            "/static/nope.css",
            "/static/",
            "/api/unknown",
            "/index.html",
            "/assets/hero.png",
            "/js/",
            "/css/",
            "/js/../CONTRACT.md",
            "/js/%2e%2e/CONTRACT.md",
            "/css/..%2fCONTRACT.md",
            "/js/sub/api.js",
            "/favicon.svg.bak",
            "/dev-demo.json/",
        ):
            response = http_request(srv.server_port, path)
            assert response.status == 404, path
            assert response.json()["ok"] is False

    def test_non_get_methods_are_405(self, live_server):
        srv, _thread = live_server
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            response = http_request(srv.server_port, "/api/health", method=method)
            assert response.status == 405, method
            assert response.headers["allow"] == "GET"
            if method != "HEAD":
                assert response.json()["ok"] is False
            assert response.headers["content-security-policy"] == settings.CSP
        alias_405 = http_request(srv.server_port, "/js/api.js", method="POST")
        assert alias_405.status == 405


# ---------------------------------------------------------------------------
# Loopback binding and teardown
# ---------------------------------------------------------------------------


class TestLoopbackOnly:
    def test_create_server_refuses_non_loopback_hosts(self, tmp_path):
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        for host in ("0.0.0.0", "localhost", "::1", "192.168.1.10", "example.invalid"):
            with pytest.raises(ValueError):
                server_module.create_server(roots, host=host)

    def test_cli_has_no_host_or_allow_remote_flag(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["--host", "0.0.0.0"])
        assert excinfo.value.code == 2
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["--allow-remote"])
        assert excinfo.value.code == 2
        args = parser.parse_args([])
        assert not hasattr(args, "host")
        assert not hasattr(args, "allow_remote")

    def test_bound_address_is_loopback(self, live_server):
        srv, _thread = live_server
        assert srv.server_address[0] == "127.0.0.1"
        assert srv.server_port > 0
        assert srv.url == f"http://127.0.0.1:{srv.server_port}/"

    def test_close_leaves_no_listening_socket(self, tmp_path):
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        srv = new_server(roots)
        port = srv.server_port
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        assert http_request(port, "/api/health").status == 200
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()


# ---------------------------------------------------------------------------
# Direct CLI
# ---------------------------------------------------------------------------


class TestDirectCli:
    def test_version_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert "pact-viz" in capsys.readouterr().out

    def test_list_prints_sessions_without_a_server(self, tmp_path, capsys):
        root = make_root(tmp_path)
        code = cli.main(["--list", "--root", str(root)])
        captured = capsys.readouterr()
        assert code == 0
        assert "sess-1" in captured.out
        assert "PACT-prompt" in captured.out
        assert "URL:" not in captured.out

    def test_list_empty_root(self, tmp_path, capsys):
        empty = tmp_path / ".claude"
        empty.mkdir()
        code = cli.main(["--list", "--root", str(empty)])
        captured = capsys.readouterr()
        assert code == 0
        assert "No PACT session journals found." in captured.out

    def test_status_and_stop_when_not_running(self, tmp_path, capsys):
        state_dir = tmp_path / "state"
        assert cli.main(["--status", "--state-dir", str(state_dir)]) == 0
        assert "not running" in capsys.readouterr().out
        assert cli.main(["--stop", "--state-dir", str(state_dir)]) == 0
        out = capsys.readouterr().out
        assert "Stop: not running" in out

    def test_invalid_port_exits_2(self):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--port", "70000", "--list"])
        assert excinfo.value.code == 2
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--port", "-1", "--list"])
        assert excinfo.value.code == 2

    def test_conflicting_modes_exit_2(self):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--list", "--stop"])
        assert excinfo.value.code == 2

    def test_python_floor_failure_prints_and_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.settings, "python_version_ok", lambda *a, **k: False)
        code = cli.main([])
        captured = capsys.readouterr()
        assert code == 1
        assert "Python 3.12+" in captured.err

    def test_health_check_refuses_non_loopback_port_args(self):
        assert cli.health_check(0, "k") is None
        assert cli.health_check(70000, "k") is None
        assert cli.health_check("nope", "k") is None  # type: ignore[arg-type]

    def test_state_file_round_trip_and_permissions(self, tmp_path):
        state_dir = tmp_path / "state"
        payload = {
            "pid": 4242,
            "port": 51234,
            "url": "http://127.0.0.1:51234/",
            "instance_id": "a" * 32,
            "key": "k" * 43,
        }
        cli.write_state(state_dir, payload)
        state_path = settings.state_file(state_dir)
        assert state_path.is_file()
        assert oct(state_path.stat().st_mode & 0o777) == oct(0o600)
        assert oct(state_dir.stat().st_mode & 0o777) == oct(0o700)
        assert cli.read_state(state_dir) == payload
        # A record without its launch key is not a server the CLI can reach.
        cli.write_state(state_dir, {key: value for key, value in payload.items() if key != "key"})
        assert cli.read_state(state_dir) is None
        # A malformed state file reads as no state.
        state_path.write_text("{not json", encoding="utf-8")
        assert cli.read_state(state_dir) is None
        cli.clear_state(state_dir)
        assert not state_path.exists()


def _wrapper_code_lines() -> list[str]:
    """bin/pact-viz with comment lines and blank lines removed."""
    return [
        line
        for line in BIN_WRAPPER.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TestBinWrapper:
    def test_wrapper_is_executable_bash_checking_python_floor(self):
        assert BIN_WRAPPER.is_file()
        assert os.access(BIN_WRAPPER, os.X_OK)
        text = BIN_WRAPPER.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash")
        code = "\n".join(_wrapper_code_lines())
        assert "(3, 12)" in code
        assert "PYTHONPATH" in code

    def test_every_exec_line_isolates_the_import_path(self):
        """Each exec of the package passes -P, so the caller's cwd is never
        put first on sys.path. Read from code lines only: a comment naming the
        command must not satisfy this."""
        exec_lines = [
            line for line in _wrapper_code_lines()
            if re.search(r"\bexec\b", line) and "visualizer" in line
        ]
        assert exec_lines, "bin/pact-viz has no exec line that runs the package"
        for line in exec_lines:
            assert re.search(r"\s-P\s+-m\s+visualizer\b", line), line

    def test_wrapper_ignores_a_shadowing_package_in_the_cwd(self, tmp_path):
        """A project directory that holds its own `visualizer` package or a
        top-level `json.py` must not replace the plugin's code: the launcher
        is run from the user's project directory."""
        decoy = tmp_path / "project"
        (decoy / "visualizer").mkdir(parents=True)
        (decoy / "visualizer" / "__init__.py").write_text("", encoding="utf-8")
        (decoy / "visualizer" / "__main__.py").write_text(
            "print('SHADOWED-BY-CWD')\nraise SystemExit(7)\n", encoding="utf-8"
        )
        (decoy / "json.py").write_text(
            "raise SystemExit('SHADOWED-STDLIB-JSON')\n", encoding="utf-8"
        )
        result = subprocess.run(
            [str(BIN_WRAPPER), "--version"],
            cwd=str(decoy),
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        assert "SHADOWED" not in combined, combined
        assert result.returncode == 0, combined
        assert "pact-viz" in result.stdout

    @pytest.mark.parametrize("caller_path", [":", "/x:", ":/x", "/x::/y"])
    def test_an_empty_pythonpath_entry_does_not_bring_the_cwd_back(
        self, tmp_path, caller_path
    ):
        """An empty PYTHONPATH entry means the current directory, ahead of the
        stdlib, and -P does not remove it; the launcher must drop it."""
        decoy = tmp_path / "project"
        decoy.mkdir()
        (decoy / "json.py").write_text(
            "raise SystemExit('SHADOWED-STDLIB-JSON')\n", encoding="utf-8"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = caller_path
        result = subprocess.run(
            [str(BIN_WRAPPER), "--version"],
            cwd=str(decoy),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        assert "SHADOWED" not in combined, combined
        assert result.returncode == 0, combined
        assert "pact-viz" in result.stdout

    def test_wrapper_version_runs(self):
        result = subprocess.run(
            [str(BIN_WRAPPER), "--version"],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "pact-viz" in result.stdout

    def test_wrapper_list_runs_against_temp_root(self, tmp_path):
        root = make_root(tmp_path)
        result = subprocess.run(
            [str(BIN_WRAPPER), "--list", "--root", str(root)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "sess-1" in result.stdout


# ---------------------------------------------------------------------------
# Detached lifecycle (start -> status -> reuse -> stop), state file, no leaks
# ---------------------------------------------------------------------------


def _cli_env(tmp_path: Path) -> dict[str, str]:
    """Environment for detached CLI children (isolated home/config)."""
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path / "home")
    environment["CLAUDE_CONFIG_DIR"] = str(tmp_path / ".claude")
    environment.pop("PACT_VIZ_INSTANCE_ID", None)
    environment.pop("PACT_VIZ_STATE", None)
    return environment


def _run_cli(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    environment = _cli_env(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "visualizer", *args],
        cwd=str(PLUGIN_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _https_ok(port: int, key: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health", headers={"X-PACT-Key": key}
    )
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.05)
    return False


def _parse_url(stdout: str) -> str:
    match = re.search(r"^URL: (http://127\.0\.0\.1:\d+/)", stdout, re.MULTILINE)
    assert match, f"no URL: line in output:\n{stdout}"
    return match.group(1)


def _parse_key(stdout: str) -> str:
    """The launch key, which the URL line carries only in its fragment."""
    match = re.search(r"^URL: http://127\.0\.0\.1:\d+/[^#\s]*#k=(\S+)$", stdout, re.MULTILINE)
    assert match, f"no keyed URL: line in output:\n{stdout}"
    return match.group(1)


def _stop_tokens(stdout: str) -> list[str]:
    match = re.search(r"^Stop: (.+)$", stdout, re.MULTILINE)
    assert match, f"no Stop: line in output:\n{stdout}"
    return shlex.split(match.group(1))


class TestDetachedLifecycle:
    def test_detach_status_reuse_stop(self, tmp_path):
        root = make_root(tmp_path)
        state_dir = tmp_path / "state"
        (tmp_path / "home").mkdir()
        args = [
            "--detach",
            "--root",
            str(root),
            "--state-dir",
            str(state_dir),
            "--port",
            "0",
        ]
        try:
            started = _run_cli(args, tmp_path)
            assert started.returncode == 0, started.stderr
            assert started.stdout.count("URL: ") == 1
            assert _stop_tokens(started.stdout)[-3:] == [
                "--stop", "--state-dir", str(state_dir.resolve())
            ]
            url = _parse_url(started.stdout)
            key = _parse_key(started.stdout)
            port = int(url.split(":")[-1].rstrip("/"))
            assert _https_ok(port, key)

            state = cli.read_state(state_dir)
            assert state is not None
            assert state["key"] == key
            assert state["port"] == port
            assert state["pid"] > 0
            assert len(state["instance_id"]) == 32
            assert state["instance_id"] != "" and state["url"] == url
            state_path = settings.state_file(state_dir)
            assert oct(state_path.stat().st_mode & 0o777) == oct(0o600)
            assert oct(state_dir.stat().st_mode & 0o777) == oct(0o700)

            status = _run_cli(["--status", "--state-dir", str(state_dir)], tmp_path)
            assert status.returncode == 0
            assert "PACT Mission Control: running" in status.stdout
            assert _parse_key(status.stdout) == key
            assert f"PID: {state['pid']}" in status.stdout

            # A second --detach reuses the healthy instance (same URL) and
            # does not spawn another listener.
            again = _run_cli(args, tmp_path)
            assert again.returncode == 0, again.stderr
            assert _parse_url(again.stdout) == url
            # Reuse prints the running instance's key; it never mints one.
            assert _parse_key(again.stdout) == key
            assert "reusing" in again.stdout
            assert cli.read_state(state_dir)["pid"] == state["pid"]

            stopped = _run_cli(["--stop", "--state-dir", str(state_dir)], tmp_path)
            assert stopped.returncode == 0, stopped.stderr
            assert f"Stop: stopped pid {state['pid']}" in stopped.stdout
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.25).close()
                except OSError:
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only on a real leak
                pytest.fail(f"listener on {port} survived --stop")
            assert not settings.state_file(state_dir).exists()
            # The detached run leaves no log residue either.
            assert not (state_dir / "server.log").exists()
            # The stopped child may need a moment to fully exit after its
            # listener closes (shutdown thread + interpreter teardown), so
            # poll for the pid to disappear rather than asserting instantly.
            gone_deadline = time.monotonic() + 5
            while time.monotonic() < gone_deadline:
                try:
                    os.kill(state["pid"], 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only on a real process leak
                pytest.fail(f"dashboard process {state['pid']} survived --stop")
        finally:
            if settings.state_file(state_dir).exists():
                _run_cli(["--stop", "--state-dir", str(state_dir)], tmp_path)

    def test_detach_demo_prints_demo_url(self, tmp_path):
        root = make_root(tmp_path)
        state_dir = tmp_path / "state-demo"
        (tmp_path / "home").mkdir(exist_ok=True)
        args = [
            "--detach",
            "--demo",
            "--root",
            str(root),
            "--state-dir",
            str(state_dir),
            "--port",
            "0",
        ]
        try:
            started = _run_cli(args, tmp_path)
            assert started.returncode == 0, started.stderr
            match = re.search(
                r"^URL: (http://127\.0\.0\.1:\d+/\?demo=1)#k=\S+$", started.stdout, re.MULTILINE
            )
            assert match, f"demo URL not printed with ?demo=1:\n{started.stdout}"
            base = match.group(1).split("?")[0]
            port = int(base.split(":")[-1].rstrip("/"))
            key = _parse_key(started.stdout)
            assert _https_ok(port, key)
            page = http_request(port, "/?demo=1", key=None)
            assert page.status == 200
            demo_payload = http_request(port, "/api/demo", key=key).json()
            assert demo_payload["source"]["kind"] == "demo"
        finally:
            stopped = _run_cli(["--stop", "--state-dir", str(state_dir)], tmp_path)
            assert stopped.returncode == 0, stopped.stderr

    @pytest.mark.parametrize("mismatch", ["instance_id", "key"])
    def test_stop_never_kills_a_foreign_instance(self, tmp_path, mismatch):
        """A state file naming a live server it cannot authenticate as its own
        (another instance token, or another launch key) is stale."""
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        srv = new_server(roots)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        state_dir = tmp_path / "state"
        try:
            cli.write_state(
                state_dir,
                {
                    "pid": 999999,
                    "port": srv.server_port,
                    "url": srv.url,
                    "instance_id": "b" * 32 if mismatch == "instance_id" else srv.instance_id,
                    "key": "z" * 43 if mismatch == "key" else srv.key,
                },
            )
            code = cli.main(["--stop", "--state-dir", str(state_dir)])
            assert code == 0
            # The live server is still healthy: the mismatched token protected it.
            assert http_request(srv.server_port, "/api/health").status == 200
            assert not settings.state_file(state_dir).exists()
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)


class TestBrowserOpen:
    def _run_foreground(
        self,
        tmp_path: Path,
        *,
        open_browser: bool,
        monkeypatch,
    ) -> tuple[int, list[str]]:
        """Run serve_foreground against a real server, then shut it down."""
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        state_dir = tmp_path / "state"
        opened: list[str] = []

        real_create = server_module.create_server
        servers: list[server_module.MissionControlServer] = []

        def fake_create(*args, **kwargs):
            srv = real_create(*args, **kwargs)
            servers.append(srv)
            return srv

        monkeypatch.setattr(cli.server_module, "create_server", fake_create)
        monkeypatch.setattr(cli, "open_browser", lambda url: opened.append(url) or True)
        argv = ["--port", "0"]
        if open_browser:
            argv.append("--open")
        args = cli.build_parser().parse_args(argv)
        result: dict[str, int] = {}

        def run() -> None:
            result["code"] = cli.serve_foreground(args, roots, state_dir, "a" * 32, "k" * 43)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not state_dir.exists():
            time.sleep(0.02)
        assert state_dir.exists(), "foreground server never wrote its state file"
        assert _https_ok(servers[0].server_port, "k" * 43)
        servers[0].shutdown()
        worker.join(timeout=10)
        assert not worker.is_alive()
        return result["code"], opened

    def test_open_is_off_by_default(self, tmp_path, monkeypatch, capsys):
        code, opened = self._run_foreground(tmp_path, open_browser=False, monkeypatch=monkeypatch)
        out = capsys.readouterr().out
        assert code == 0
        assert opened == []
        assert "URL: " in out

    def test_open_does_not_suppress_url_line(self, tmp_path, monkeypatch, capsys):
        code, opened = self._run_foreground(tmp_path, open_browser=True, monkeypatch=monkeypatch)
        out = capsys.readouterr().out
        assert code == 0
        assert len(opened) == 1
        assert "URL: " in out


class TestDemoModelDirect:
    """The demo model is importable and deterministic without the server."""

    def test_build_demo_model_matches_contract_identity(self):
        payload = demo.build_demo_model()
        assert payload["etag"] == settings.DEMO_ETAG
        assert payload["source"]["kind"] == "demo"
        assert payload["metrics"]["events_total"] == len(payload["events"])
        assert payload["meta"]["event_count"] == len(payload["events"])
        assert journal.make_etag(payload["source"]["mtime"], payload["source"]["size"]) == "0-0"
        assert model.PHASE_ORDER == ("PREPARE", "ARCHITECT", "CODE", "TEST")


def _host_request(port: int, path: str, host: str, timeout: float = 5.0) -> HttpResponse:
    """One request carrying an explicit Host header (rebinding probe shape)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.putrequest("GET", path, skip_host=True)
        connection.putheader("Host", host)
        if port in _SERVER_KEYS:
            connection.putheader("X-PACT-Key", _SERVER_KEYS[port])
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return HttpResponse(response.status, headers, body)
    finally:
        connection.close()


class TestLateCrashWindow:
    """A handler crash AFTER the status line is on the wire must end the
    connection, never attempt a second response (stream corruption)."""

    def test_crash_between_headers_and_body_closes_the_connection(
        self, tmp_path, monkeypatch
    ):
        """Fault injection for the headers-sent guard: the route sends a 200
        status line announcing Content-Length 1000, then dies before any body.

        The client must see a truncated response (IncompleteRead), and the
        handler must make EXACTLY ONE send attempt — a second _send_json call
        here would write a fresh status line into a stream that already has
        one, corrupting the HTTP framing.
        """
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        send_calls: list[int] = []
        real_send_json = server_module.MissionControlHandler._send_json
        real_send_headers = server_module.MissionControlHandler._send_headers

        def send_headers_then_die(self, status, content_type, length, extra_headers=()):
            real_send_headers(self, 200, "application/json", 1000, ())
            raise RuntimeError("mid-body crash (injected)")

        def counting_send_json(self, status, payload, extra_headers=()):
            send_calls.append(status)
            if payload.get("ok") is True and status == 200:
                # The route's own send: fault between headers and body.
                send_headers_then_die(self, status, None, None)
            return real_send_json(self, status, payload, extra_headers)

        monkeypatch.setattr(
            server_module.MissionControlHandler, "_send_json", counting_send_json
        )
        srv = new_server(roots)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            try:
                connection.request("GET", "/api/health", headers={"X-PACT-Key": srv.key})
                response = connection.getresponse()
                assert response.status == 200
                with pytest.raises(http.client.IncompleteRead):
                    response.read()
            finally:
                connection.close()
            assert send_calls == [200], (
                f"expected exactly one send attempt, saw {send_calls}; a second "
                "status line after headers are on the wire corrupts the stream"
            )
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)


class TestHostHeaderAndErrorContract:
    """A Host other than the loopback names is refused before any route runs."""

    def test_foreign_host_is_403_with_security_headers(self, live_server):
        srv, _root = live_server
        response = _host_request(srv.server_port, "/api/health", "attacker.example")
        assert response.status == 403
        assert response.json() == {"ok": False, "error": "forbidden host"}
        assert "content-security-policy" in response.headers

    def test_missing_host_is_refused(self, live_server):
        srv, _root = live_server
        response = _host_request(srv.server_port, "/api/health", "")
        assert response.status == 403

    def test_loopback_and_localhost_hosts_pass(self, live_server):
        srv, _root = live_server
        for host in (f"127.0.0.1:{srv.server_port}", "localhost", "LOCALHOST:80"):
            response = _host_request(srv.server_port, "/api/health", host)
            assert response.status == 200, host
            assert response.json()["ok"] is True

    def test_malformed_request_line_gets_json_and_security_headers(self):
        """The stdlib send_error path must honor the every-response contract.

        BaseHTTPRequestHandler answers an unparseable request line with a
        plain HTML body and no security headers; the server routes that path
        through its JSON error shape instead.
        """
        srv = new_server([])
        try:
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            with socket.create_connection(("127.0.0.1", srv.server_port), timeout=5) as sock:
                sock.sendall(b"GARBAGE\r\n\r\n")
                chunks = []
                try:
                    while True:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except socket.timeout:
                    pass
            raw = b"".join(chunks)
            assert b"HTTP/1.1 400" in raw
            assert b"content-security-policy" in raw.lower()
            assert b'"ok":false' in raw
        finally:
            srv.shutdown()
            srv.server_close()

    def test_allow_reuse_address_is_posix_only(self):
        assert server_module.MissionControlServer.allow_reuse_address == (
            sys.platform != "win32"
        )


class TestReuseAndStopGuards:
    """cmd_stop must not clear a replacement instance's state, and reuse
    must refuse when the running instance serves a different request."""

    def test_clear_state_if_instance_leaves_a_replacement_alone(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        cli.write_state(state_dir, {"pid": 1, "port": 1, "url": "u", "instance_id": "A", "key": "k"})
        cli.write_state(state_dir, {"pid": 2, "port": 2, "url": "u", "instance_id": "B", "key": "k"})
        # The stale-A clear must not delete instance B's state file, and must
        # report that it declined so the caller keeps B's log too.
        assert cli._clear_state_if_instance(state_dir, "A") is False
        assert cli.read_state(state_dir)["instance_id"] == "B"
        # Clearing for B itself (file gone → None → clear) still works.
        assert cli._clear_state_if_instance(state_dir, "B") is True
        assert cli.read_state(state_dir) is None

    def test_stop_removes_the_log_and_launch_page_residue(self, tmp_path):
        """--stop leaves no server.log or launch page behind when the state is ours."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        log = state_dir / "server.log"
        log.write_text("")
        page = state_dir / "launch.html"
        page.write_text("<meta>")
        cli.write_state(state_dir, {"pid": 1, "port": 1, "url": "u", "instance_id": "A", "key": "k"})
        assert cli._clear_state_if_instance(state_dir, "A") is True
        cli._remove_residue(state_dir)
        assert not log.exists()
        assert not page.exists()

    def test_foreign_state_keeps_the_log(self, tmp_path):
        """When the guard declines (foreign state), the log must survive."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        log = state_dir / "server.log"
        log.write_text("still running\n")
        cli.write_state(state_dir, {"pid": 1, "port": 1, "url": "u", "instance_id": "B", "key": "k"})
        assert cli._clear_state_if_instance(state_dir, "A") is False
        assert log.exists()

    def test_detach_refuses_reuse_for_different_roots(self, tmp_path):
        root_a = make_root(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        state_dir = tmp_path / "state"
        args_a = ["--detach", "--root", str(root_a), "--state-dir", str(state_dir), "--port", "0"]
        try:
            started = _run_cli(args_a, tmp_path)
            assert started.returncode == 0, started.stderr
            url = _parse_url(started.stdout)
            port = int(url.split(":")[-1].rstrip("/"))
            assert _https_ok(port, _parse_key(started.stdout))

            # Same request reuses (control arm).
            again = _run_cli(args_a, tmp_path)
            assert again.returncode == 0, again.stderr
            assert "reusing" in again.stdout

            # A different root must be refused with the stop hint, not
            # silently answered with the old instance's URL.
            mismatch = _run_cli(
                ["--detach", "--root", str(other), "--state-dir", str(state_dir)],
                tmp_path,
            )
            assert mismatch.returncode == 1
            assert "different roots" in mismatch.stderr
            assert "--stop" in mismatch.stderr
        finally:
            if settings.state_file(state_dir).exists():
                _run_cli(["--stop", "--state-dir", str(state_dir)], tmp_path)


# ---------------------------------------------------------------------------
# Static containment, request parsing and failure status
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_static(tmp_path, monkeypatch):
    """A throwaway static root the server serves instead of the shipped one,
    with an outside file of an allowed type next to it."""
    static = tmp_path / "static"
    (static / "css").mkdir(parents=True)
    (static / "css" / "base.css").write_text("body{}", encoding="utf-8")
    (static / "notes.md").write_text("not an asset", encoding="utf-8")
    (static / "server.py").write_text("SECRET = 1", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.json").write_text('{"leak": true}', encoding="utf-8")
    (static / "leak.json").symlink_to(outside / "leak.json")
    (static / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(server_module, "_static_root", lambda: static)
    return static


class TestStaticContainment:
    def test_allowlisted_asset_is_served(self, live_server, fake_static):
        srv, _thread = live_server
        response = http_request(srv.server_port, "/static/css/base.css")
        assert response.status == 200
        assert response.body == b"body{}"

    @pytest.mark.parametrize("path", ["/static/notes.md", "/static/server.py"])
    def test_a_file_type_outside_the_allowlist_is_404(self, live_server, fake_static, path):
        srv, _thread = live_server
        response = http_request(srv.server_port, path)
        assert response.status == 404, path
        assert response.json() == {"ok": False, "error": "not found"}

    @pytest.mark.parametrize("path", ["/static/leak.json", "/static/linked/leak.json"])
    def test_a_symlink_out_of_the_static_root_is_404(self, live_server, fake_static, path):
        srv, _thread = live_server
        response = http_request(srv.server_port, path)
        assert response.status == 404, path
        assert b"leak" not in response.body

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../assets/hero.png",
            "/static/%2e%2e/assets/hero.png",
            "/static/..%2fassets/hero.png",
            "/css/..%2f..%2fassets/hero.png",
        ],
    )
    def test_an_escape_to_an_allowed_file_type_is_404(self, live_server, path):
        """The target exists and its type is allowlisted, so only the
        traversal guards stand between the request and the file."""
        assert (PLUGIN_ROOT / "visualizer" / "assets" / "hero.png").is_file()
        srv, _thread = live_server
        response = http_request(srv.server_port, path)
        assert response.status == 404, path
        assert not response.body.startswith(b"\x89PNG"), path

    def test_a_dot_dot_segment_is_refused_even_inside_the_root(self, live_server):
        """`js/../favicon.svg` resolves inside static/, so containment alone
        would serve it; the segment rule refuses any `..`."""
        srv, _thread = live_server
        assert http_request(srv.server_port, "/static/favicon.svg").status == 200
        response = http_request(srv.server_port, "/static/js/../favicon.svg")
        assert response.status == 404


class TestQueryParsing:
    def test_a_blank_repeat_does_not_hide_the_real_value(self, live_server):
        srv, _thread = live_server
        response = http_request(
            srv.server_port,
            "/api/session?slug=&slug=PACT-prompt&session_id=&session_id=sess-1",
        )
        assert response.status == 200
        assert response.json()["source"]["session_id"] == "sess-1"

    def test_the_limit_ceiling_is_inclusive(self, live_server):
        srv, _thread = live_server
        ceiling = settings.MAX_SESSIONS_LIMIT
        assert http_request(srv.server_port, f"/api/sessions?limit={ceiling}").status == 200
        assert http_request(srv.server_port, f"/api/sessions?limit={ceiling + 1}").status == 400

    @pytest.mark.parametrize("component", ["a%01b", "a%5Cb", "a%0Ab"])
    def test_an_unsafe_component_is_400(self, live_server, component):
        srv, _thread = live_server
        response = http_request(
            srv.server_port, f"/api/session?slug={component}&session_id=sess-1"
        )
        assert response.status == 400
        assert response.json()["ok"] is False


class TestFailureStatus:
    def test_a_handler_crash_before_headers_is_a_500_with_the_contract(
        self, live_server, monkeypatch
    ):
        srv, _thread = live_server

        def boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(server_module.model, "build_health", boom)
        response = http_request(srv.server_port, "/api/health")
        assert response.status == 500
        assert response.json() == {"ok": False, "error": "internal error"}
        assert response.headers["content-security-policy"] == settings.CSP

    @pytest.mark.parametrize("port", [-1, 65536, 70000])
    def test_create_server_refuses_an_out_of_range_port(self, tmp_path, port):
        roots = roots_for(make_root(tmp_path), tmp_path)
        with pytest.raises(ValueError, match="between 0 and 65535"):
            server_module.create_server(roots, port=port)


class TestThreadedServing:
    def test_a_stalled_connection_does_not_block_other_requests(self, live_server):
        """A client that opens a connection and never finishes its request
        must not hold the listener: each connection gets its own thread."""
        srv, _thread = live_server
        with socket.create_connection(("127.0.0.1", srv.server_port), timeout=5) as stalled:
            stalled.sendall(b"GET /favicon.svg HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            response = http_request(srv.server_port, "/favicon.svg", timeout=3)
        assert response.status == 200


# ---------------------------------------------------------------------------
# CLI state record, keyed health and reuse
# ---------------------------------------------------------------------------


def _live_state(srv) -> dict:
    """A state record that names a live in-process test server as ours."""
    return {
        "version": settings.VERSION,
        "plugin_root": str(settings.PLUGIN_ROOT),
        "pid": os.getpid(),
        "pid_start": cli.pid_start_time(os.getpid()),
        "host": "127.0.0.1",
        "port": srv.server_port,
        "url": srv.url,
        "instance_id": srv.instance_id,
        "key": srv.key,
        "roots": [root.id for root in srv.roots],
    }


class TestStateRecord:
    def _write_raw(self, state_dir: Path, payload: dict, mode: int = 0o600) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = settings.state_file(state_dir)
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(path, mode)

    def _valid(self) -> dict:
        return {"pid": 4242, "port": 51234, "url": "http://127.0.0.1:51234/",
                "instance_id": "a" * 32, "key": "k" * 43}

    @pytest.mark.parametrize(
        "change",
        [{"port": 0}, {"port": 70000}, {"pid": 0}, {"pid": "4242"}, {"key": ""},
         {"instance_id": ""}, {"url": 5}],
        ids=["port0", "port-high", "pid0", "pid-text", "key-empty", "id-empty", "url-type"],
    )
    def test_an_invalid_record_reads_as_no_server(self, tmp_path, change):
        state_dir = tmp_path / "state"
        self._write_raw(state_dir, {**self._valid(), **change})
        assert cli.read_state(state_dir) is None

    def test_a_valid_record_reads_back(self, tmp_path):
        state_dir = tmp_path / "state"
        self._write_raw(state_dir, self._valid())
        assert cli.read_state(state_dir) == self._valid()

    @pytest.mark.parametrize("mode", [0o620, 0o602, 0o666], ids=oct)
    def test_a_record_others_can_write_is_ignored_with_a_warning(self, tmp_path, capsys, mode):
        state_dir = tmp_path / "state"
        self._write_raw(state_dir, self._valid(), mode=mode)
        assert cli.read_state(state_dir) is None
        assert "pact-viz: warning: ignoring" in capsys.readouterr().err

    def test_a_symlinked_record_is_refused(self, tmp_path):
        real = tmp_path / "elsewhere.json"
        real.write_text(json.dumps(self._valid()), encoding="utf-8")
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        settings.state_file(state_dir).symlink_to(real)
        assert cli.read_state(state_dir) is None


class TestKeyedHealthCheck:
    def test_health_check_needs_the_key(self, live_server):
        srv, _thread = live_server
        assert cli.health_check(srv.server_port, srv.key)["instance_id"] == srv.instance_id
        assert cli.health_check(srv.server_port, "z" * 43) is None

    def test_health_check_never_goes_through_a_proxy(self, live_server, monkeypatch):
        """A machine-wide proxy must not carry the loopback check off-box:
        with a dead proxy configured, the check still reaches the server."""
        srv, _thread = live_server
        for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        payload = cli.health_check(srv.server_port, srv.key)
        assert payload is not None and payload["instance_id"] == srv.instance_id


class TestReuse:
    def test_reuse_prints_the_stored_key_and_this_selection(self, live_server, tmp_path, capsys):
        srv, _thread = live_server
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, _live_state(srv))
        args = cli.build_parser().parse_args(["--detach", "--demo", "--state-dir", str(state_dir)])
        assert cli._reuse_if_running(state_dir, args, srv.roots) is True
        out = capsys.readouterr().out
        assert "reusing" in out
        assert re.search(
            rf"^URL: {re.escape(srv.url.rstrip('/'))}/\?demo=1#k={re.escape(srv.key)}$",
            out, re.MULTILINE,
        ), out

    def test_reuse_refuses_an_explicit_other_port(self, live_server, tmp_path):
        srv, _thread = live_server
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, _live_state(srv))
        other = srv.server_port + 1 if srv.server_port < 65535 else srv.server_port - 1
        args = cli.build_parser().parse_args(
            ["--detach", "--port", str(other), "--state-dir", str(state_dir)]
        )
        with pytest.raises(cli.CliError, match=f"is on port {srv.server_port}"):
            cli._reuse_if_running(state_dir, args, srv.roots)
        same = cli.build_parser().parse_args(
            ["--detach", "--port", str(srv.server_port), "--state-dir", str(state_dir)]
        )
        assert cli._reuse_if_running(state_dir, same, srv.roots) is True

    def test_an_unauthenticated_instance_is_not_reused(self, live_server, tmp_path):
        srv, _thread = live_server
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, {**_live_state(srv), "key": "z" * 43})
        args = cli.build_parser().parse_args(["--detach", "--state-dir", str(state_dir)])
        assert cli._reuse_if_running(state_dir, args, srv.roots) is False


# ---------------------------------------------------------------------------
# Bounded work and well-formed requests only
# ---------------------------------------------------------------------------


def _raw_exchange(port: int, payload: bytes, timeout: float = 3.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(payload)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
    return b"".join(chunks)


_EVERY_RESPONSE_HEADERS = {
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
    "x-content-type-options": "nosniff",
}


class TestResponseContract:
    @pytest.mark.parametrize(
        ("path", "keyed", "status"),
        [("/api/health", True, 200), ("/api/health", False, 401), ("/", False, 200),
         ("/static/favicon.svg", False, 200), ("/api/nope", True, 404),
         ("/static/missing.css", False, 404)],
    )
    def test_every_response_carries_the_header_set(self, live_server, path, keyed, status):
        srv, _thread = live_server
        response = http_request(srv.server_port, path, key=srv.key if keyed else None)
        assert response.status == status
        for name, value in _EVERY_RESPONSE_HEADERS.items():
            assert response.headers[name] == value, (path, name)
        assert response.headers["content-security-policy"] == settings.CSP
        assert response.headers["server"] == f"{settings.APP_NAME}/{settings.VERSION}"

    def test_an_http_0_9_request_gets_no_answer(self, live_server):
        srv, _thread = live_server
        assert _raw_exchange(srv.server_port, b"GET /\r\n", timeout=1.0) == b""

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1:", "127.0.0.1:123456", "127.0.0.1:8a", "localhost.:80",
         "127.0.0.1.evil", "evil.127.0.0.1", "[::1]:80", "127.0.0.1:80:80"],
    )
    def test_a_host_that_is_not_exactly_loopback_is_403(self, live_server, host):
        srv, _thread = live_server
        assert _host_request(srv.server_port, "/api/health", host).status == 403, host

    @pytest.mark.parametrize("host", ["127.0.0.1", "LOCALHOST", "localhost:65535"])
    def test_exact_loopback_hosts_pass(self, live_server, host):
        srv, _thread = live_server
        assert _host_request(srv.server_port, "/api/health", host).status == 200, host

    @pytest.mark.parametrize("value", ["%D9%A3", "%EF%BC%95", "%2B5", "5.0", "0x5"])
    def test_a_limit_that_is_not_ascii_digits_is_400(self, live_server, value):
        srv, _thread = live_server
        assert http_request(srv.server_port, f"/api/sessions?limit={value}").status == 400

    def test_head_answers_static_with_headers_only_and_refuses_the_api(self, live_server):
        srv, _thread = live_server
        raw = _raw_exchange(
            srv.server_port, b"HEAD / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        )
        head, _, body = raw.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200")
        assert b"content-length:" in head.lower()
        assert body == b""
        assert http_request(srv.server_port, "/api/health", method="HEAD").status == 405

    def test_a_non_finite_number_in_a_journal_reaches_the_page_as_null(self, live_server):
        srv, _thread = live_server
        path = srv.roots[0].path / "pact-sessions" / "PACT-prompt" / "sess-1" / "session-journal.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"v":1,"type":"commit","ts":"2026-11-01T10:06:00Z","sha":"abc1234",'
                         '"message":"m","phase":"CODE","weird":NaN,"big":Infinity}\n')
        response = http_request(srv.server_port, "/api/session?slug=PACT-prompt&session_id=sess-1")
        assert response.status == 200

        def refuse(name: str) -> None:
            raise AssertionError(f"non-JSON constant {name} in the response")

        payload = json.loads(response.body, parse_constant=refuse)
        raw = [event["raw"] for event in payload["events"] if event["raw"].get("weird", 0) is None]
        assert raw and raw[0]["big"] is None


class TestServerBounds:
    def test_idle_connections_at_the_cap_never_lock_out_the_key_holder(
        self, live_server, monkeypatch
    ):
        """Sockets that hold every slot with an unfinished request must not
        stop the page: a keyed API request is still answered."""
        srv, _thread = live_server
        monkeypatch.setattr(server_module, "MAX_CONNECTIONS", 3)
        held = [socket.create_connection(("127.0.0.1", srv.server_port), timeout=5)
                for _ in range(3)]
        try:
            for sock in held:
                sock.sendall(b"GET /api/hea")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and srv.connections < 3:
                time.sleep(0.02)
            assert srv.connections == 3
            response = http_request(srv.server_port, "/api/health")
            assert response.status == 200
            assert response.json()["instance_id"] == srv.instance_id
        finally:
            for sock in held:
                sock.close()

    def test_an_unchanged_session_is_built_once(self, live_server, monkeypatch):
        srv, _thread = live_server
        calls: list[int] = []
        real = server_module.model.build_model

        def counted(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(server_module.model, "build_model", counted)
        path = "/api/session?slug=PACT-prompt&session_id=sess-1"
        first = http_request(srv.server_port, path)
        second = http_request(srv.server_port, path)
        assert first.body == second.body
        assert len(calls) == 1
        journal_path = (srv.roots[0].path / "pact-sessions" / "PACT-prompt" / "sess-1"
                        / "session-journal.jsonl")
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write('{"v":1,"type":"phase_transition","ts":"2026-11-01T10:07:00Z",'
                         '"phase":"CODE","status":"started"}\n')
        third = http_request(srv.server_port, path)
        assert len(calls) == 2
        assert third.json()["etag"] != first.json()["etag"]

    def test_the_body_cache_is_bounded(self, live_server):
        srv, _thread = live_server
        root = srv.roots[0].path
        for index in range(settings.BODY_CACHE_ENTRIES + 3):
            write_journal(root, "Many", f"sess-many-{index}", sample_events())
            http_request(srv.server_port, f"/api/session?slug=Many&session_id=sess-many-{index}")
        assert len(srv.body_cache) == settings.BODY_CACHE_ENTRIES
