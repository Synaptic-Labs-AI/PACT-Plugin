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


class TestChildIgnoresCallerCwd:
    """The detached child must import the plugin's visualizer, never a cwd copy."""

    def test_child_argv_carries_dash_p_before_dash_m(self, tmp_path):
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path / "root"), "--state-dir", str(tmp_path / "s")]
        )
        command = cli._child_command(args, cli._resolve_roots(args), tmp_path / "s")
        assert command[1:4] == ["-P", "-m", "visualizer"]

    def test_child_command_from_decoy_cwd_runs_plugin_code(self, tmp_path):
        """Run the child's own interpreter prefix from a cwd holding a decoy package."""
        import subprocess
        from pathlib import Path

        decoy = tmp_path / "decoy"
        (decoy / "visualizer").mkdir(parents=True)
        (decoy / "visualizer" / "__init__.py").write_text("raise SystemExit('DECOY')\n")
        (decoy / "visualizer" / "__main__.py").write_text("raise SystemExit('DECOY')\n")
        (decoy / "json.py").write_text("raise SystemExit('DECOY')\n")
        args = cli.build_parser().parse_args(["--detach", "--state-dir", str(tmp_path / "s")])
        command = cli._child_command(args, [], tmp_path / "s")
        prefix = command[: command.index("visualizer") + 1]
        plugin_root = str(Path(cli.__file__).resolve().parent.parent)
        env = {**os.environ, "PYTHONPATH": plugin_root}
        result = subprocess.run(
            [*prefix, "--version"], cwd=decoy, env=env, capture_output=True, text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("pact-viz ")
        assert "DECOY" not in result.stdout + result.stderr


class TestOneContainmentRule:
    """Listing, fetch and the roster loaders accept and refuse the same paths."""

    @staticmethod
    def _journal(directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "session-journal.jsonl"
        path.write_text('{"type":"session_start","ts":"2026-11-07T15:00:00Z"}\n')
        return path

    @staticmethod
    def _views(root_dir, tmp_path, slug, session_id):
        from visualizer import discovery

        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        listed = [(ref.slug, ref.session_id) for ref in discovery.discover_root(roots[0])]
        fetched = discovery.find_session(roots, slug, session_id)
        return listed, fetched

    def test_symlinked_pact_sessions_dir_is_listed_and_fetchable(self, tmp_path):
        real = tmp_path / "elsewhere"
        self._journal(real / "proj" / "s1")
        root_dir = tmp_path / "root"
        root_dir.mkdir()
        (root_dir / "pact-sessions").symlink_to(real, target_is_directory=True)
        listed, fetched = self._views(root_dir, tmp_path, "proj", "s1")
        assert listed == [("proj", "s1")]
        assert fetched is not None

    @pytest.mark.parametrize("level", ["slug", "session", "journal"])
    def test_symlink_below_pact_sessions_is_refused_by_both(self, tmp_path, level):
        outside = tmp_path / "outside"
        target_journal = self._journal(outside / "proj" / "s1")
        root_dir = tmp_path / "root"
        sessions = root_dir / "pact-sessions"
        sessions.mkdir(parents=True)
        if level == "slug":
            (sessions / "proj").symlink_to(outside / "proj", target_is_directory=True)
        elif level == "session":
            (sessions / "proj").mkdir()
            (sessions / "proj" / "s1").symlink_to(outside / "proj" / "s1", target_is_directory=True)
        else:
            (sessions / "proj" / "s1").mkdir(parents=True)
            (sessions / "proj" / "s1" / "session-journal.jsonl").symlink_to(target_journal)
        listed, fetched = self._views(root_dir, tmp_path, "proj", "s1")
        assert listed == []
        assert fetched is None

    def test_roster_skips_symlinked_team_and_task_paths(self, tmp_path):
        from visualizer import roster

        root_dir = tmp_path / "root"
        outside = tmp_path / "outside"
        (outside / "team").mkdir(parents=True)
        (outside / "team" / "config.json").write_text(
            json.dumps({"members": [{"name": "planted", "agentType": "x"}]})
        )
        (outside / "7.json").write_text(json.dumps({"id": "7", "subject": "planted"}))
        (root_dir / "teams").mkdir(parents=True)
        (root_dir / "teams" / "team-a").symlink_to(outside / "team", target_is_directory=True)
        tasks = root_dir / "tasks" / "team-a"
        tasks.mkdir(parents=True)
        (tasks / "7.json").symlink_to(outside / "7.json")
        (tasks / "8.json").write_text(json.dumps({"id": "8", "subject": "real"}))
        info = roster.load_team(root_dir, "team-a")
        assert info.members == []
        assert not info.team_dir_present
        assert [task.id for task in info.tasks] == ["8"]


# Built from fragments at runtime so the source never holds a contiguous
# credential or key header; the values under test are the full shapes.
_PEM_KIND = "RSA " + "PRIVATE " + "KEY"
_PLANTED_SECRETS = (
    "gh" + "p_" + "A" * 30,
    "github" + "_pat_" + "B" * 30,
    "sk" + "-ant-" + "C" * 30,
    "sk" + "-" + "D" * 30,
    "AK" + "IA" + "E" * 16,
    "xo" + "xb-" + "1234567890abc",
    "Bear" + "er " + "F" * 20,
    "-----" + "BEGIN " + _PEM_KIND + "-----\nMIIBOGUS\n-----" + "END " + _PEM_KIND + "-----",
)
_PLANTED_ASSIGNMENT = ("pass" + "word") + ": " + "hunter" + "2"


class TestServerSideRedaction:
    """No planted credential reaches a model payload, whatever field carries it."""

    def test_model_payload_carries_no_planted_secret(self, tmp_path):
        from visualizer import discovery, model

        root_dir = tmp_path / "root"
        session = root_dir / "pact-sessions" / "proj" / "s1"
        session.mkdir(parents=True)
        events = [
            {"type": "session_start", "ts": "2026-11-07T15:00:00Z", "session_id": "s1",
             "project_dir": "/x/proj", "team": "team-r"},
        ]
        for index, secret in enumerate(_PLANTED_SECRETS):
            events.append({"type": "review_finding", "ts": "2026-11-07T15:01:00Z",
                           "severity": "minor", "reviewer": "r", "finding": f"saw {secret}"})
            events.append({"type": "agent_handoff", "ts": "2026-11-07T15:02:00Z",
                           "agent": "a", "task_id": str(index), "task_subject": secret,
                           "handoff": {"produced": secret, "decisions": [secret],
                                       "uncertainty": "", "integration": "",
                                       "open_questions": []}})
            events.append({"type": "commit", "ts": "2026-11-07T15:03:00Z", "sha": "abc",
                           "message": "m", "phase": "CODE",
                           "nested": {"deeper": {"value": secret}}})
        events.append({"type": "commit", "ts": "2026-11-07T15:04:00Z", "sha": "abc",
                       "message": _PLANTED_ASSIGNMENT, "phase": "CODE",
                       "api_key": {"anything": "plain"}})
        deep = {"type": "commit", "ts": "2026-11-07T15:05:00Z", "sha": "abc",
                "message": "m", "phase": "CODE"}
        node = deep
        for level in range(7):
            node["d"] = {"leaf": f"{_PLANTED_SECRETS[0]} at {level}", "list": [_PLANTED_SECRETS[4]]}
            node = node["d"]
        events.append(deep)
        (session / "session-journal.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events)
        )
        tasks = root_dir / "tasks" / "team-r"
        tasks.mkdir(parents=True)
        (tasks / "1.json").write_text(json.dumps({"id": "1", "subject": _PLANTED_SECRETS[0]}))
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        ref = discovery.find_session(roots, "proj", "s1")
        text = json.dumps(model.build_model(ref))
        text += json.dumps(model.build_sessions_list(roots))
        for secret in _PLANTED_SECRETS:
            assert secret not in text
            assert secret.splitlines()[0] not in text
        assert "hunter" + "2" not in text
        assert '"plain"' not in text
        assert "[redacted]" in text


class TestDepthCappedLeavesAreRedacted:
    """A string leaf at or past the depth cap is redacted and bounded too."""

    @pytest.mark.parametrize("depth", [4, 5, 6])
    def test_string_leaf_at_depth(self, depth):
        from visualizer import classify

        leaf = _PLANTED_SECRETS[2] + " " + "x" * (settings.MAX_FIELD_CHARS + 50)
        data: dict = {"type": "commit"}
        node = data
        for _ in range(depth):
            node["k"] = {}
            node = node["k"]
        node["leaf"] = leaf
        trimmed = classify.trim_raw(data)
        text = json.dumps(trimmed)
        assert _PLANTED_SECRETS[2] not in text
        value = trimmed
        while isinstance(value, dict) and "k" in value:
            value = value["k"]
        if isinstance(value, dict):
            assert value["leaf"].startswith("[redacted]")
            assert len(value["leaf"]) <= settings.MAX_FIELD_CHARS
        else:
            assert value == "[truncated]"


class TestTerminalOutputStripsControls:
    def test_list_and_status_strip_control_characters(self, tmp_path, capsys, monkeypatch):
        root_dir = tmp_path / "root"
        session = root_dir / "pact-sessions" / "proj" / "s1"
        session.mkdir(parents=True)
        (session / "session-journal.jsonl").write_text(
            json.dumps({"type": "session_start", "ts": "2026-11-07T15:00:00\x1b[2J",
                        "session_id": "s1"}) + "\n"
        )
        rc = cli.main(["--list", "--root", str(root_dir)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "\x1b" not in out
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, {"pid": os.getpid(), "port": 1, "url": "http://x/\x1b]0;t\x07",
                                    "instance_id": "i\x9b1", "roots": ["r\x1bx"]})
        monkeypatch.setattr(cli, "_health_for_instance", lambda *a, **k: {"pid": os.getpid()})
        rc = cli.main(["--status", "--state-dir", str(state_dir)])
        out = capsys.readouterr().out
        assert rc == 0
        assert not any(ch in out for ch in ("\x1b", "\x07", "\x9b"))
