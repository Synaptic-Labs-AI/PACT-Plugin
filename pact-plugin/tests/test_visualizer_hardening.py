"""Location: pact-plugin/tests/test_visualizer_hardening.py
Summary: Regression pins for the Mission Control review-hardening fixes:
         --stop refuses a pid /api/health does not report, stop success means
         the instance went silent, HEAD carries no body, half-open sockets get
         a timeout, a port collision is a one-line CLI error, and a failed
         detach cannot delete another instance's state file.
Used by: pytest (hardening regressions for the visualizer server and CLI).
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import threading
import urllib.error
import urllib.request

import pytest

from visualizer import cli, settings
from visualizer import server as server_module


@pytest.fixture
def live_server(tmp_path):
    """A real server on an ephemeral loopback port, torn down with the test."""
    root = tmp_path / "root"
    (root / "pact-sessions").mkdir(parents=True)
    roots = settings.resolve_roots([root], env={}, home=tmp_path)
    srv = server_module.create_server(roots)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


def _read_until_close(port: int, raw_request: bytes, read_timeout: float = 0.5) -> bytes:
    """Send one raw request and return everything the server writes back."""
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(raw_request)
        sock.settimeout(read_timeout)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
    return b"".join(chunks)


def _state(port: int, *, pid: int, instance_id: str) -> dict:
    return {
        "version": settings.VERSION,
        "pid": pid,
        "host": settings.LOOPBACK_HOST,
        "port": port,
        "url": f"http://{settings.LOOPBACK_HOST}:{port}/",
        "instance_id": instance_id,
        "roots": [],
    }


class TestStopPidCrossCheck:
    def test_stop_refuses_a_pid_health_does_not_report(
        self, live_server, tmp_path, monkeypatch, capsys
    ):
        """A tampered state file must not make --stop signal a bystander."""
        srv = live_server
        state_dir = tmp_path / "state"
        # State names the real instance and port but an unrelated pid.
        cli.write_state(
            state_dir,
            _state(srv.server_port, pid=999_999, instance_id=srv.instance_id),
        )
        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1

        assert signalled == []
        # The live instance's state survives: it still names the server to stop.
        assert cli.read_state(state_dir) is not None
        out = capsys.readouterr().out
        assert "refusing to signal" in out
        assert "does not match" in out
        # The message names the escape hatch for a stuck user.
        assert str(settings.state_file(state_dir)) in out

    def test_refusal_quotes_the_delete_operand(
        self, live_server, tmp_path, monkeypatch, capsys
    ):
        """The delete hint must be one shell word for any state-dir path."""
        srv = live_server
        state_dir = tmp_path / "state dir;touch PWNED"
        cli.write_state(
            state_dir,
            _state(srv.server_port, pid=999_999, instance_id=srv.instance_id),
        )
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)

        args = cli.build_parser().parse_args(
            ["--stop", "--state-dir", str(state_dir)]
        )
        assert cli.cmd_stop(args) == 1

        out = capsys.readouterr().out
        operand = out.split("delete ", 1)[1].split(" to clear", 1)[0]
        assert shlex.split(operand) == [str(settings.state_file(state_dir))]

    def test_stop_signals_when_the_pid_matches(self, tmp_path, monkeypatch, capsys):
        """Control arm: the guard must not block an honest stop."""
        state_dir = tmp_path / "state"
        cli.write_state(
            state_dir, _state(12345, pid=4242, instance_id="instance-ok")
        )
        monkeypatch.setattr(
            cli,
            "_health_for_instance",
            lambda port, instance_id, timeout=1.0: {"ok": True, "pid": 4242},
        )
        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))
        monkeypatch.setattr(cli, "_wait_until_stopped", lambda *a, **k: True)

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 0

        assert [pid for pid, _sig in signalled] == [4242]
        assert cli.read_state(state_dir) is None


class TestStopUnconfirmed:
    def test_unconfirmed_stop_does_not_claim_success(
        self, tmp_path, monkeypatch, capsys
    ):
        """A signalled-but-still-answering instance must not print success."""
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, _state(12345, pid=4242, instance_id="instance-ok"))
        monkeypatch.setattr(
            cli,
            "_health_for_instance",
            lambda port, instance_id, timeout=1.0: {"ok": True, "pid": 4242},
        )
        monkeypatch.setattr(cli, "_terminate", lambda pid: None)
        monkeypatch.setattr(cli, "_wait_until_stopped", lambda *a, **k: False)
        # Signal escalation is skipped when the pid is already gone.
        monkeypatch.setattr(cli, "_pid_gone", lambda pid: True)

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1

        out = capsys.readouterr().out
        assert "Stop: unconfirmed pid 4242" in out
        assert "Stop: stopped pid" not in out


class TestStopHintQuoting:
    def test_state_dir_metacharacters_round_trip_as_one_token(self, tmp_path):
        """The printed Stop: line must be paste-safe for any state-dir path."""
        weird = tmp_path / "state dir;touch PWNED"
        args = cli.build_parser().parse_args(
            ["--stop", "--state-dir", str(weird)]
        )
        hint = cli._stop_hint(args)

        assert shlex.split(hint) == [
            "pact-viz",
            "--stop",
            "--state-dir",
            str(weird),
        ]


def _one_root(tmp_path):
    return [settings.RootSpec(id="r", label="r", path=tmp_path, default=True)]


def _reuse_args(tmp_path, *extra):
    return cli.build_parser().parse_args(
        ["--detach", "--state-dir", str(tmp_path / "state"), *extra]
    )


class TestReuseDemoMode:
    def test_reuse_refuses_across_demo_modes(self, tmp_path):
        state = {
            "version": settings.VERSION,
            "roots": ["r"],
            "port": 5,
            "filters": {},
            "demo": False,
        }
        with pytest.raises(cli.CliError) as excinfo:
            cli._refuse_on_reuse_mismatch(
                state, _reuse_args(tmp_path, "--demo"), _one_root(tmp_path)
            )
        # Branch-specific: the wording must say which mode the server runs.
        assert "started without --demo" in str(excinfo.value)

    def test_reuse_refuses_demo_request_against_a_real_instance(self, tmp_path):
        state = {
            "version": settings.VERSION,
            "roots": ["r"],
            "port": 5,
            "filters": {},
            "demo": True,
        }
        with pytest.raises(cli.CliError) as excinfo:
            cli._refuse_on_reuse_mismatch(
                state, _reuse_args(tmp_path), _one_root(tmp_path)
            )
        assert "started with --demo" in str(excinfo.value)

    def test_reuse_allows_the_same_demo_mode(self, tmp_path):
        state = {
            "version": settings.VERSION,
            "roots": ["r"],
            "port": 5,
            "filters": {},
            "demo": True,
        }
        cli._refuse_on_reuse_mismatch(
            state, _reuse_args(tmp_path, "--demo"), _one_root(tmp_path)
        )


class TestReuseVersion:
    def test_reuse_refuses_a_different_version(self, tmp_path):
        state = {
            "version": "0.0.1",
            "roots": ["r"],
            "port": 5,
            "filters": {},
            "demo": False,
        }
        with pytest.raises(cli.CliError) as excinfo:
            cli._refuse_on_reuse_mismatch(
                state, _reuse_args(tmp_path), _one_root(tmp_path)
            )
        message = str(excinfo.value)
        assert "version 0.0.1" in message
        assert f"this is {settings.VERSION}" in message

    def test_reuse_refuses_an_unknown_version(self, tmp_path):
        state = {"roots": ["r"], "port": 5, "filters": {}, "demo": False}
        with pytest.raises(cli.CliError) as excinfo:
            cli._refuse_on_reuse_mismatch(
                state, _reuse_args(tmp_path), _one_root(tmp_path)
            )
        assert "version (unknown)" in str(excinfo.value)

    def test_reuse_allows_the_same_version(self, tmp_path):
        state = {
            "version": settings.VERSION,
            "roots": ["r"],
            "port": 5,
            "filters": {},
            "demo": False,
        }
        cli._refuse_on_reuse_mismatch(
            state, _reuse_args(tmp_path), _one_root(tmp_path)
        )


class TestWaitUntilStopped:
    def test_a_disappeared_pid_is_not_success(self, monkeypatch):
        """The instance still answering wins over the pid having vanished."""
        monkeypatch.setattr(
            cli, "_health_for_instance", lambda *a, **k: {"ok": True}
        )
        monkeypatch.setattr(cli, "_pid_gone", lambda pid: True)
        assert cli._wait_until_stopped(1, "token", timeout=0.15) is False

    def test_a_silent_instance_is_success(self, monkeypatch):
        monkeypatch.setattr(cli, "_health_for_instance", lambda *a, **k: None)
        assert cli._wait_until_stopped(1, "token", timeout=0.15) is True


class TestHeadResponse:
    def test_head_has_no_body(self, live_server):
        srv = live_server
        raw = _read_until_close(
            srv.server_port,
            b"HEAD /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
        )
        head, separator, body = raw.partition(b"\r\n\r\n")
        assert b"405" in head
        assert b"content-security-policy" in head.lower()
        assert separator == b"\r\n\r\n"
        assert body == b""

    def test_get_still_carries_the_json_body(self, live_server):
        """Control arm: the HEAD fix must not silence GET responses."""
        srv = live_server
        raw = _read_until_close(
            srv.server_port,
            b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
        )
        _head, separator, body = raw.partition(b"\r\n\r\n")
        assert separator == b"\r\n\r\n"
        assert body.startswith(b'{"ok":true')


def _get_json(port: int, path: str) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _health(port: int) -> dict:
    return _get_json(port, "/api/health")


class TestHealthLaunchMode:
    def test_health_reports_the_actual_launch_mode(self, live_server):
        """/api/health reports the launch mode, not a hard-coded demo flag."""
        real = _health(live_server.server_port)
        assert real["demo"] is False
        assert real["demo_available"] is True

        demo_srv = server_module.create_server(live_server.roots, demo_mode=True)
        thread = threading.Thread(target=demo_srv.serve_forever, daemon=True)
        thread.start()
        try:
            demo = _health(demo_srv.server_port)
        finally:
            demo_srv.shutdown()
            demo_srv.server_close()
            thread.join(timeout=5)

        assert demo["demo"] is True
        assert demo["demo_available"] is True


class TestSessionsFilterEcho:
    def test_unfiltered_sessions_echo_empty_filters(self, live_server):
        payload = _get_json(live_server.server_port, "/api/sessions")
        assert payload["filters"] == {}

    def test_launch_project_filter_is_echoed(self, live_server):
        """A filtered launch's /api/sessions names the filter that emptied it."""
        srv = server_module.create_server(
            live_server.roots, filters={"project": "my project"}
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            payload = _get_json(srv.server_port, "/api/sessions")
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

        assert payload["filters"]["project"] == "my project"
        assert payload["sessions"] == []

    def test_invalid_byte_filter_is_escaped_not_a_500(self, live_server):
        """A lone surrogate must escape as JSON, not kill the whole response."""
        bad = "evil-\udcff-proj"  # what an undecodable argv byte becomes
        srv = server_module.create_server(
            live_server.roots, filters={"project": bad}
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/api/sessions"
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

        assert status == 200
        assert b"\\udcff" in raw
        assert json.loads(raw.decode("utf-8"))["filters"]["project"] == bad


class TestHalfOpenTimeout:
    def test_handler_declares_a_connection_timeout(self):
        """A half-open socket must not pin a handler thread forever."""
        assert server_module.MissionControlHandler.timeout == 30


class TestPortInUse:
    def test_port_collision_is_a_single_cli_error(self, tmp_path, capsys):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            busy_port = busy.getsockname()[1]

            rc = cli.main(
                [
                    "--port",
                    str(busy_port),
                    "--root",
                    str(tmp_path / "root"),
                    "--state-dir",
                    str(tmp_path / "state"),
                ]
            )

        err = capsys.readouterr().err
        assert rc == 1
        assert "pact-viz: cannot bind" in err
        assert "Traceback" not in err


class TestDetachFailureGuard:
    def test_failed_detach_keeps_another_instances_state(self, tmp_path, monkeypatch):
        """A concurrent detach's published state must survive our failure path."""
        state_dir = tmp_path / "state"
        foreign = {"pid": 2, "port": 1, "url": "u", "instance_id": "B"}
        cli.write_state(state_dir, foreign)

        class DeadProcess:
            def poll(self):
                return 1

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 1

        monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: DeadProcess())
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path / "root"), "--state-dir", str(state_dir)]
        )

        with pytest.raises(cli.CliError):
            cli.detach(args, cli._resolve_roots(args), state_dir)

        assert cli.read_state(state_dir) == foreign
