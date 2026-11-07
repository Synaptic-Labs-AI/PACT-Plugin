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


def _state(port: int, *, pid: int, instance_id: str, key: str = "test-key") -> dict:
    return {
        "version": settings.VERSION,
        "plugin_root": str(settings.PLUGIN_ROOT),
        "pid": pid,
        "pid_start": None,
        "host": settings.LOOPBACK_HOST,
        "port": port,
        "url": f"http://{settings.LOOPBACK_HOST}:{port}/",
        "instance_id": instance_id,
        "key": key,
        "roots": [],
    }


def _fake_health(instance_id: str, pid: int):
    return lambda port, key, timeout=1.0: {"ok": True, "instance_id": instance_id, "pid": pid}


class TestStopPidCrossCheck:
    def test_stop_refuses_a_pid_health_does_not_report(
        self, live_server, tmp_path, monkeypatch, capsys
    ):
        """A tampered state file must not make --stop signal a bystander."""
        srv = live_server
        state_dir = tmp_path / "state"
        # State names the real instance, port and key but an unrelated pid.
        cli.write_state(
            state_dir,
            _state(srv.server_port, pid=999_999, instance_id=srv.instance_id, key=srv.key),
        )
        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1

        assert signalled == []
        assert cli.read_state(state_dir) is not None
        out = capsys.readouterr().out
        assert "refusing to signal" in out
        assert "does not match" in out
        assert str(settings.state_file(state_dir)) in out

    def test_refusal_quotes_the_delete_operand(
        self, live_server, tmp_path, monkeypatch, capsys
    ):
        """The delete hint must be one shell word for any state-dir path."""
        srv = live_server
        state_dir = tmp_path / "state dir;touch PWNED"
        cli.write_state(
            state_dir,
            _state(srv.server_port, pid=999_999, instance_id=srv.instance_id, key=srv.key),
        )
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1

        out = capsys.readouterr().out
        operand = out.split("delete ", 1)[1].split(" to clear", 1)[0]
        assert shlex.split(operand) == [str(settings.state_file(state_dir))]

    def test_stop_signals_when_the_pid_matches(self, tmp_path, monkeypatch, capsys):
        """Control arm: the guard must not block an honest stop."""
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, _state(12345, pid=4242, instance_id="instance-ok"))
        monkeypatch.setattr(cli, "health_check", _fake_health("instance-ok", 4242))
        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))
        monkeypatch.setattr(cli, "_wait_until_stopped", lambda *a, **k: True)

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 0

        assert [pid for pid, _sig in signalled] == [4242]
        assert cli.read_state(state_dir) is None

    def test_stop_refuses_a_reused_pid_by_start_time(self, tmp_path, monkeypatch, capsys):
        """Same instance token and pid, different start time: never signal."""
        state_dir = tmp_path / "state"
        state = _state(12345, pid=4242, instance_id="instance-ok")
        state["pid_start"] = "111"
        cli.write_state(state_dir, state)
        monkeypatch.setattr(cli, "health_check", _fake_health("instance-ok", 4242))
        monkeypatch.setattr(cli, "pid_start_time", lambda pid: "222")
        signalled: list[int] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append(pid))

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1
        assert signalled == []
        assert "Stop: refused" in capsys.readouterr().out


class TestStopUnconfirmed:
    def test_unconfirmed_stop_does_not_claim_success(self, tmp_path, monkeypatch, capsys):
        """A signalled-but-still-answering instance must not print success."""
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, _state(12345, pid=4242, instance_id="instance-ok"))
        monkeypatch.setattr(cli, "health_check", _fake_health("instance-ok", 4242))
        monkeypatch.setattr(cli, "_terminate", lambda pid: None)
        monkeypatch.setattr(cli, "_wait_until_stopped", lambda *a, **k: False)
        monkeypatch.setattr(cli, "_pid_gone", lambda pid: True)

        args = cli.build_parser().parse_args(["--stop", "--state-dir", str(state_dir)])
        assert cli.cmd_stop(args) == 1

        out = capsys.readouterr().out
        assert "Stop: unconfirmed pid 4242" in out
        assert "Stop: stopped pid" not in out


class TestStopLine:
    """The printed Stop: line is runnable as pasted, for any state-dir path."""

    def test_launcher_form_round_trips_as_tokens(self, tmp_path, monkeypatch):
        weird = tmp_path / "state dir;touch PWNED"
        launcher = str(tmp_path / "bin dir" / "pact-viz")
        monkeypatch.setenv(settings.LAUNCHER_ENV, launcher)
        assert shlex.split(cli._stop_command(weird)) == [
            launcher, "--stop", "--state-dir", str(weird),
        ]

    def test_interpreter_form_without_the_launcher(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.delenv(settings.LAUNCHER_ENV, raising=False)
        weird = tmp_path / "state dir"
        assert shlex.split(cli._stop_command(weird)) == [
            f"PYTHONPATH={settings.PLUGIN_ROOT}", sys.executable, "-P", "-m", "visualizer",
            "--stop", "--state-dir", str(weird),
        ]

    def test_relative_state_dir_is_printed_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(settings.LAUNCHER_ENV, "/x/pact-viz")
        assert shlex.split(cli._stop_command(cli.Path("rel")))[-1] == str(tmp_path / "rel")


def _one_root(tmp_path):
    return [settings.RootSpec(id="r", label="r", path=tmp_path, default=True)]


def _reuse_args(tmp_path, *extra):
    args = cli.build_parser().parse_args(
        ["--detach", "--state-dir", str(tmp_path / "state"), *extra]
    )
    assert cli._check_args(cli.build_parser(), args) == 0
    return args


class TestReuseRules:
    """One server per state dir: selection never refuses; build, roots and port do."""

    @staticmethod
    def _running(tmp_path, monkeypatch, **overrides):
        state_dir = tmp_path / "state"
        state = {**_state(5555, pid=4242, instance_id="i1", key="stored-key"), "roots": ["r"]}
        state.update(overrides)
        cli.write_state(state_dir, state)
        monkeypatch.setattr(cli, "_our_instance", lambda state, timeout=1.0: {"pid": 4242})
        return state_dir

    @pytest.mark.parametrize(
        "extra, query",
        [((), ""), (("--demo",), "?demo=1"), (("--project", "p.x"), "?project=p.x"),
         (("--session", "abcdef12"), "?session=abcdef12")],
    )
    def test_any_selection_reuses_with_the_stored_key(
        self, tmp_path, monkeypatch, capsys, extra, query
    ):
        state_dir = self._running(tmp_path, monkeypatch)
        assert cli._reuse_if_running(state_dir, _reuse_args(tmp_path, *extra), _one_root(tmp_path))
        out = capsys.readouterr().out
        assert f"URL: http://127.0.0.1:5555/{query}#k=stored-key" in out

    def test_another_build_is_replaced_after_a_verified_stop(self, tmp_path, monkeypatch, capsys):
        state_dir = self._running(tmp_path, monkeypatch, version="0.0.1")
        stopped: list[str] = []
        monkeypatch.setattr(cli, "_stop_verified", lambda state: stopped.append(state["pid"]) or True)
        assert cli._reuse_if_running(state_dir, _reuse_args(tmp_path), _one_root(tmp_path)) is False
        assert stopped == [4242]
        assert cli.read_state(state_dir) is None
        assert "replaced an instance from another plugin build" in capsys.readouterr().out

    def test_another_plugin_root_is_replaced(self, tmp_path, monkeypatch):
        state_dir = self._running(tmp_path, monkeypatch, plugin_root="/elsewhere")
        monkeypatch.setattr(cli, "_stop_verified", lambda state: True)
        assert cli._reuse_if_running(state_dir, _reuse_args(tmp_path), _one_root(tmp_path)) is False

    def test_another_build_that_will_not_stop_refuses(self, tmp_path, monkeypatch):
        state_dir = self._running(tmp_path, monkeypatch, version="0.0.1")
        monkeypatch.setattr(cli, "_stop_verified", lambda state: False)
        with pytest.raises(cli.CliError) as excinfo:
            cli._reuse_if_running(state_dir, _reuse_args(tmp_path), _one_root(tmp_path))
        assert "stop it first with" in str(excinfo.value)

    def test_different_roots_refuse(self, tmp_path, monkeypatch):
        state_dir = self._running(tmp_path, monkeypatch, roots=["other"])
        with pytest.raises(cli.CliError) as excinfo:
            cli._reuse_if_running(state_dir, _reuse_args(tmp_path), _one_root(tmp_path))
        assert str(excinfo.value).startswith("a running instance serves different roots")

    def test_a_different_explicit_port_refuses(self, tmp_path, monkeypatch):
        state_dir = self._running(tmp_path, monkeypatch)
        with pytest.raises(cli.CliError):
            cli._reuse_if_running(
                state_dir, _reuse_args(tmp_path, "--port", "6000"), _one_root(tmp_path)
            )

    def test_an_unconfirmed_instance_starts_fresh(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        cli.write_state(state_dir, {**_state(5555, pid=4242, instance_id="i1"), "roots": ["r"]})
        monkeypatch.setattr(cli, "health_check", lambda *a, **k: None)
        assert cli._reuse_if_running(state_dir, _reuse_args(tmp_path), _one_root(tmp_path)) is False


class TestWaitUntilStopped:
    def test_a_disappeared_pid_is_not_success(self, monkeypatch):
        """The instance still answering wins over the pid having vanished."""
        monkeypatch.setattr(cli, "_our_instance", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(cli, "_pid_gone", lambda pid: True)
        assert cli._wait_until_stopped(_state(1, pid=1, instance_id="t"), timeout=0.15) is False

    def test_a_silent_instance_is_success(self, monkeypatch):
        monkeypatch.setattr(cli, "_our_instance", lambda *a, **k: None)
        assert cli._wait_until_stopped(_state(1, pid=1, instance_id="t"), timeout=0.15) is True


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
            b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
            + f"{settings.KEY_HEADER}: {srv.key}\r\n\r\n".encode(),
        )
        _head, separator, body = raw.partition(b"\r\n\r\n")
        assert separator == b"\r\n\r\n"
        assert body.startswith(b'{"ok":true')


def _get(port: int, path: str, key: str | None) -> tuple[int, dict]:
    headers = {settings.KEY_HEADER: key} if key is not None else {}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _get_json(port: int, path: str, key: str) -> dict:
    return _get(port, path, key)[1]


class TestLaunchKeyRequired:
    """Every /api/* route answers 401 without the launch key; static routes do not."""

    @pytest.mark.parametrize(
        "path",
        ["/api/health", "/api/sessions", "/api/session?slug=a&session_id=b", "/api/demo",
         "/api/nope"],
    )
    @pytest.mark.parametrize("presented", [None, "", "wrong", "café"])
    def test_api_without_the_right_key_is_401(self, live_server, path, presented):
        if presented == "café":
            raw = _read_until_close(
                live_server.server_port,
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n".encode()
                + settings.KEY_HEADER.encode() + b": caf\xe9\xff\r\n\r\n",
            )
            assert raw.startswith(b"HTTP/1.1 401")
            return
        status, body = _get(live_server.server_port, path, presented)
        assert status == 401
        assert body == {"ok": False, "error": "key required"}

    def test_the_right_key_is_accepted(self, live_server):
        status, body = _get(live_server.server_port, "/api/health", live_server.key)
        assert status == 200 and body["ok"] is True

    def test_static_page_needs_no_key(self, live_server):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{live_server.server_port}/", timeout=5) as response:
            assert response.status == 200


class TestHealthIdentity:
    def test_keyed_health_names_the_build_and_no_launch_mode(self, live_server):
        health = _get_json(live_server.server_port, "/api/health", live_server.key)
        assert health["version"] == settings.VERSION
        assert health["plugin_root"] == str(settings.PLUGIN_ROOT)
        assert health["demo_available"] is True
        assert "demo" not in health


class TestSessionsSelection:
    def test_unfiltered_sessions_echo_empty_filters(self, live_server):
        payload = _get_json(live_server.server_port, "/api/sessions", live_server.key)
        assert payload["filters"] == {}

    def test_request_filters_are_applied_and_echoed(self, live_server):
        payload = _get_json(
            live_server.server_port, "/api/sessions?project=p.x&session=abcdef12",
            live_server.key,
        )
        assert payload["filters"] == {"project": "p.x", "session": "abcdef12"}
        assert payload["sessions"] == []

    @pytest.mark.parametrize(
        "query", ["project=my%20project", "project=%C3%A9", "session=short", "session=a%2Fb12345"]
    )
    def test_malformed_selection_is_400_not_500(self, live_server, query):
        status, body = _get(live_server.server_port, f"/api/sessions?{query}", live_server.key)
        assert status == 400
        assert body["ok"] is False


class TestProjectMatcher:
    """Exact on slug, label, raw basename in that order; case-folded only after all miss."""

    @staticmethod
    def _sessions():
        return [
            {"key": "k1", "slug": "My_App", "project": "My_App", "session_id": "s1"},
            {"key": "k2", "slug": "my_app", "project": "my_app", "session_id": "s2"},
            {"key": "k3", "slug": "PACT-prompt", "project": "PACT-prompt", "session_id": "s3"},
        ]

    def test_exact_slug_wins_over_case_folding(self):
        from visualizer import model

        hits = model.match_project("my_app", self._sessions(), {})
        assert [hit["key"] for hit in hits] == ["k2"]

    def test_raw_basename_tier(self):
        from visualizer import model

        hits = model.match_project("My App", self._sessions(), {"k1": "My App"})
        assert [hit["key"] for hit in hits] == ["k1"]

    def test_case_insensitive_only_after_every_exact_tier_misses(self):
        from visualizer import model

        hits = model.match_project("pact-PROMPT", self._sessions(), {})
        assert [hit["key"] for hit in hits] == ["k3"]

    def test_never_a_substring(self):
        from visualizer import model

        assert model.match_project("PACT", self._sessions(), {}) == []

    def test_session_exact_then_unique_prefix(self):
        from visualizer import model

        sessions = [{"session_id": "abcdef12-0001"}, {"session_id": "abcdef12-0002"}]
        assert len(model.match_session("abcdef12", sessions)) == 2
        assert model.match_session("abcdef12-0001", sessions) == [sessions[0]]
        assert model.match_session("abcdef1", sessions) == []


class TestSelectionFlags:
    @pytest.mark.parametrize(
        "argv, message",
        [(["--project", "a b"], "pact-viz: --project must match [A-Za-z0-9._-]"),
         (["--project", "x" * 129], "pact-viz: --project must match [A-Za-z0-9._-]"),
         (["--session", "short"], "pact-viz: --session must match [A-Za-z0-9-]{8,64}")],
    )
    def test_bad_selection_value_exits_2(self, tmp_path, capsys, argv, message):
        assert cli.main([*argv, "--state-dir", str(tmp_path / "s")]) == 2
        assert capsys.readouterr().err.strip() == message

    @pytest.mark.parametrize(
        "argv",
        [["--stop", "--demo"], ["--status", "--project", "x"], ["--stop", "--port", "5"],
         ["--status", "--open"], ["--list", "--demo"], ["--list", "--open"],
         ["--list", "--port", "5"], ["--list", "--detach"], ["--project", "a", "--project-dir", "b"],
         ["--demo", "--session", "abcdef12"]],
    )
    def test_meaningless_combinations_are_usage_errors(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2

    def test_project_dir_uses_the_hooks_slug_rule(self, tmp_path):
        target = tmp_path / "My  App.v2"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        assert cli.project_slug(str(link)) == "My_App_v2"


class TestLaunchPage:
    def test_launch_page_is_private_and_carries_the_escaped_url(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        url = 'http://127.0.0.1:5/?project=a"b#k=K<x>'
        page = cli._write_launch_page(state_dir, url)
        assert (page.stat().st_mode & 0o777) == 0o600
        text = page.read_text()
        assert "K&lt;x&gt;" in text and 'a&quot;b' in text
        assert "<x>" not in text

    def test_browser_is_opened_on_the_file_not_the_keyed_url(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        opened: list[str] = []
        monkeypatch.setattr(cli, "_headless", lambda: False)
        monkeypatch.setattr(cli.webbrowser, "open", lambda target: opened.append(target) or True)
        assert cli._open_keyed(state_dir, "http://127.0.0.1:5/#k=SECRETKEY")
        assert opened == [(state_dir / "launch.html").as_uri()]
        assert "SECRETKEY" not in opened[0]

    def test_a_planted_symlink_is_replaced_not_followed(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        victim = tmp_path / "victim.txt"
        victim.write_text("keep me")
        (state_dir / "launch.html").symlink_to(victim)
        cli._write_launch_page(state_dir, "http://127.0.0.1:5/#k=K")
        assert victim.read_text() == "keep me"
        assert not (state_dir / "launch.html").is_symlink()


class TestKeyStaysOutOfArgv:
    def test_child_argv_carries_no_key_and_no_selection(self, tmp_path):
        args = cli.build_parser().parse_args(
            ["--detach", "--demo", "--state-dir", str(tmp_path / "s")]
        )
        command = cli._child_command(args, [], tmp_path / "s")
        assert "--demo" not in command and "--project" not in command
        assert not any(part.startswith("--key") or part.startswith("#k=") for part in command)

    def test_main_pops_the_inherited_key_and_instance(self, tmp_path, monkeypatch):
        monkeypatch.setenv(settings.KEY_ENV, "inherited-key")
        monkeypatch.setenv("PACT_VIZ_INSTANCE_ID", "inherited-instance")
        seen: dict = {}

        def fake_serve(args, roots, state_dir, instance_id, key):
            seen.update(instance_id=instance_id, key=key,
                        env_key=os.environ.get(settings.KEY_ENV),
                        env_instance=os.environ.get("PACT_VIZ_INSTANCE_ID"))
            return 0

        monkeypatch.setattr(cli, "serve_foreground", fake_serve)
        assert cli.main(["--state-dir", str(tmp_path / "s")]) == 0
        assert seen == {"instance_id": "inherited-instance", "key": "inherited-key",
                        "env_key": None, "env_instance": None}


class TestPidStartTime:
    def test_own_start_time_is_read_on_linux(self):
        import sys

        if not sys.platform.startswith("linux"):
            pytest.skip("pid start time comes from /proc")
        first = cli.pid_start_time(os.getpid())
        assert first and first.isdigit()
        assert cli.pid_start_time(os.getpid()) == first

    def test_a_missing_pid_has_no_start_time(self):
        assert cli.pid_start_time(2**31 - 1) is None


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
        foreign = {"pid": 2, "port": 1, "url": "u", "instance_id": "B", "key": "kb"}
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
                                    "instance_id": "i\x9b1", "key": "k\x1b[2J",
                                    "roots": ["r\x1bx"]})
        monkeypatch.setattr(cli, "_our_instance", lambda *a, **k: {"pid": os.getpid()})
        rc = cli.main(["--status", "--state-dir", str(state_dir)])
        out = capsys.readouterr().out
        assert rc == 0
        assert not any(ch in out for ch in ("\x1b", "\x07", "\x9b"))


def _write_session(root_dir, events, *, slug="proj", session_id="s1", age_s=0.0):
    session = root_dir / "pact-sessions" / slug / session_id
    session.mkdir(parents=True, exist_ok=True)
    path = session / "session-journal.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    stamp = __import__("time").time() - age_s
    os.utime(path, (stamp, stamp))
    return path


def _both_views(root_dir, tmp_path, slug="proj", session_id="s1"):
    from visualizer import discovery, model

    roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
    ref = discovery.find_session(roots, slug, session_id)
    full = model.build_model(ref)["meta"]
    listed = model.build_sessions_list(roots, show_all=True)["sessions"][0]
    return (full["status"], full["ended_by"]), (listed["status"], listed["ended_by"])


_START = {"type": "session_start", "ts": "2026-11-07T15:00:00Z", "session_id": "s1",
          "project_dir": "/x/proj", "team": "team-l"}
_END = {"type": "session_end", "ts": "2026-11-07T15:10:00Z"}


class TestRunStatusOneRule:
    """Both views take status from the newest lifecycle event, then the clock."""

    def test_resumed_run_is_live_in_both_views(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START, _END, {**_START, "source": "resume"},
                                  {"type": "phase_transition", "phase": "CODE",
                                   "status": "started"}])
        assert _both_views(root_dir, tmp_path) == (("live", None), ("live", None))

    def test_work_after_wrap_up_record_is_not_ended(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START, {**_END, "source": "wrap-up", "pr_state": "OPEN"},
                                  {"type": "commit", "sha": "a", "message": "m",
                                   "phase": "CODE"}])
        assert _both_views(root_dir, tmp_path) == (("live", None), ("live", None))

    def test_end_followed_only_by_trailers_is_ended_by_session_end(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START, _END, {"type": "cleanup_summary"}])
        assert _both_views(root_dir, tmp_path) == (
            ("ended", "session_end"), ("ended", "session_end"),
        )

    def test_paused_before_exit_reads_paused(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START, {"type": "session_paused", "pr_number": 1}, _END])
        assert _both_views(root_dir, tmp_path) == (
            ("paused", "session_end"), ("paused", "session_end"),
        )

    def test_old_end_beyond_scan_budget_agrees(self, tmp_path):
        pad = [{"type": "lifecycle_decision", "verdict": "allow", "tool_name": "Bash",
                "pad": "x" * 900} for _ in range(700)]
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START, _END, *pad])
        assert _both_views(root_dir, tmp_path) == (("live", None), ("live", None))

    def test_quiet_journal_with_busy_task_store_stays_live(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START], age_s=3600)
        tasks = root_dir / "tasks" / "team-l"
        tasks.mkdir(parents=True)
        (tasks / "1.json").write_text(json.dumps({"id": "1", "status": "in_progress"}))
        assert _both_views(root_dir, tmp_path) == (("live", None), ("live", None))

    def test_quiet_run_ends_idle(self, tmp_path):
        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START], age_s=3600)
        assert _both_views(root_dir, tmp_path) == (("ended", "idle"), ("ended", "idle"))


class TestEtagCoversEveryInput:
    """A since-poll must see status crossing the live window and task-store writes."""

    key = ""

    def _get(self, port, query):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/session?{query}", headers={settings.KEY_HEADER: self.key}
        )
        with opener.open(request, timeout=5) as response:
            return json.loads(response.read()), response.headers.get("ETag")

    def test_since_poll_sees_status_change_and_task_write(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone

        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START], age_s=10)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        srv = server_module.create_server(roots)
        self.key = srv.key
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            first, header = self._get(srv.server_port, "slug=proj&session_id=s1")
            assert first["meta"]["status"] == "live"
            assert header == f'"{first["etag"]}"'
            same, _ = self._get(srv.server_port, f"slug=proj&session_id=s1&since={first['etag']}")
            assert same.get("unchanged") is True

            later = datetime.now(timezone.utc) + timedelta(seconds=settings.LIVE_WINDOW_S + 5)
            monkeypatch.setattr(server_module, "_now", lambda: later)
            moved, _ = self._get(srv.server_port, f"slug=proj&session_id=s1&since={first['etag']}")
            assert moved.get("unchanged") is not True
            assert moved["meta"]["status"] == "ended"
            assert moved["meta"]["ended_by"] == "idle"
            monkeypatch.undo()

            tasks = root_dir / "tasks" / "team-l"
            tasks.mkdir(parents=True)
            (tasks / "9.json").write_text(json.dumps({"id": "9", "subject": "new"}))
            fresh, _ = self._get(srv.server_port, f"slug=proj&session_id=s1&since={first['etag']}")
            assert fresh.get("unchanged") is not True
            assert any(task["id"] == "9" for task in fresh["tasks"])
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)


class TestSessionContextIsAnTokenInput:
    def test_a_context_file_write_changes_the_token(self, tmp_path):
        from visualizer import discovery, model

        root_dir = tmp_path / "root"
        journal_path = _write_session(root_dir, [_START], age_s=3600)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        ref = discovery.find_session(roots, "proj", "s1")
        before, _ = model.session_token(ref)
        (journal_path.parent / "pact-session-context.json").write_text('{"team_name": "t"}')
        after, _ = model.session_token(ref)
        assert after != before


class TestCloudFollowsTheRealWriters:
    """Cloud is lead_address == "main" or a roster file; the roster uses key k."""

    @staticmethod
    def _model(tmp_path, starts, *, team_files=None):
        from visualizer import discovery, model

        root_dir = tmp_path / "root"
        _write_session(root_dir, starts)
        team_dir = root_dir / "teams" / "team-c"
        team_dir.mkdir(parents=True, exist_ok=True)
        for name, text in (team_files or {}).items():
            (team_dir / name).write_text(text)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        return model.build_model(discovery.find_session(roots, "proj", "s1"))

    _LOCAL = {**_START, "team": "team-c", "lead_address": "team-lead"}

    def test_a_local_start_with_a_config_less_team_dir_is_not_cloud(self, tmp_path):
        payload = self._model(tmp_path, [self._LOCAL], team_files={"file-edits.json": "{}"})
        assert payload["meta"]["cloud"] is False
        assert all(agent["cloud"] is False for agent in payload["agents"])

    def test_lead_address_main_is_cloud_and_names_the_lead(self, tmp_path):
        payload = self._model(tmp_path, [{**self._LOCAL, "lead_address": "main"}])
        assert payload["meta"]["cloud"] is True
        leads = [agent for agent in payload["agents"] if agent["kind"] == "lead"]
        assert [lead["name"] for lead in leads] == ["main"]

    def test_the_latest_start_with_a_lead_address_decides(self, tmp_path):
        old = {key: value for key, value in self._LOCAL.items() if key != "lead_address"}
        payload = self._model(tmp_path, [old, {**old, "source": "resume", "lead_address": "main"}])
        assert payload["meta"]["cloud"] is True

    def test_roster_intents_under_key_k_form_the_crew(self, tmp_path):
        roster_lines = "\n".join(json.dumps(record) for record in [
            {"v": 1, "k": "intent", "tu": "toolu_1", "name": "coder-a", "type": "PACT:pact-backend-coder"},
            {"v": 1, "k": "intent", "tu": "toolu_1", "name": "dup", "type": "x"},
            {"v": 2, "k": "intent", "tu": "toolu_2", "name": "future-version", "type": "x"},
            {"v": 1, "kind": "intent", "tu": "toolu_3", "name": "legacy-kind", "type": "x"},
            {"v": 1, "k": "foreign", "kind": "intent", "tu": "toolu_4", "name": "not-intent"},
            {"v": 1, "k": "bind", "tu": "toolu_1", "aid": "agent-1", "type": "x"},
        ]) + "\n"
        payload = self._model(tmp_path, [{**self._LOCAL, "lead_address": "main"}],
                              team_files={"pact-roster.jsonl": roster_lines})
        names = {agent["name"] for agent in payload["agents"]}
        assert {"coder-a", "legacy-kind"} <= names
        assert not names & {"dup", "future-version", "not-intent"}
        from visualizer import roster

        intents = roster.load_team(tmp_path / "root", "team-c").intents
        assert [(seed.name, seed.agent_id) for seed in intents][:1] == [("coder-a", "agent-1")]

    def test_a_roster_beside_a_config_is_not_read(self, tmp_path):
        roster_lines = json.dumps({"v": 1, "k": "intent", "tu": "t", "name": "ghost", "type": "x"})
        payload = self._model(
            tmp_path, [self._LOCAL],
            team_files={"config.json": json.dumps({"members": []}),
                        "pact-roster.jsonl": roster_lines + "\n"},
        )
        assert "ghost" not in {agent["name"] for agent in payload["agents"]}
    def test_a_team_lead_member_and_a_main_lead_address_are_one_lead(self, tmp_path):
        config = json.dumps({"members": [
            {"name": "team-lead", "agentType": "team-lead"},
            {"name": "coder", "agentType": "PACT:pact-backend-coder"},
        ]})
        payload = self._model(
            tmp_path,
            [{**self._LOCAL, "lead_address": "main"},
             {"type": "phase_transition", "phase": "CODE", "status": "started",
              "agent": "team-lead"}],
            team_files={"config.json": config},
        )
        leads = [agent for agent in payload["agents"] if agent["kind"] == "lead"]
        assert [lead["name"] for lead in leads] == ["main"]
        assert "team-lead" not in {agent["name"] for agent in payload["agents"]}
        assert leads[0]["stats"]["events"] >= 1


def _build(tmp_path, events, *, tasks=None, team="team-t"):
    from visualizer import discovery, model

    root_dir = tmp_path / "root"
    _write_session(root_dir, [{**_START, "team": team}, *events])
    if tasks is not None:
        tasks_dir = root_dir / "tasks" / team
        tasks_dir.mkdir(parents=True, exist_ok=True)
        for record in tasks:
            (tasks_dir / f"{record['id']}.json").write_text(json.dumps(record))
    roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
    return model.build_model(discovery.find_session(roots, "proj", "s1"))


def _snapshot(task_id, occupant, **metadata):
    return {"type": "task_metadata_snapshot", "task_id": task_id, "occupant": occupant,
            "subject": metadata.pop("subject", f"subject {occupant}"),
            "metadata": metadata}


class TestTaskIdentityAndOrder:
    def test_a_reused_id_keeps_one_card_per_occupant(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("3", "occA", subject="first task"),
            {"type": "agent_handoff", "agent": "a", "task_id": "3", "task_subject": "first task",
             "handoff": {}},
            _snapshot("3", "occB", subject="second task"),
            {"type": "agent_dispatch", "agent": "b", "task_id": "3", "phase": "CODE"},
        ])
        cards = {task["key"]: task for task in payload["tasks"]}
        assert set(cards) == {"3:occA", "3:occB"}
        assert cards["3:occA"]["status"] == "handoff"
        assert cards["3:occB"]["status"] == "active"
        assert payload["handoffs"][0]["task_key"] == "3:occA"

    def test_cards_are_numeric_and_the_cap_keeps_the_newest(self, tmp_path):
        from visualizer import settings as viz_settings

        count = viz_settings.MAX_TASKS + 5
        payload = _build(tmp_path, [], tasks=[{"id": str(i), "subject": f"t{i}",
                                               "status": "pending"} for i in range(1, count + 1)])
        ids = [int(task["id"]) for task in payload["tasks"]]
        assert ids == sorted(ids)
        assert ids[-1] == count and ids[0] == 6
        assert "tasks" in " ".join(payload["warnings"])

    def test_the_activity_clock_watches_the_same_newest_task_files(self, tmp_path):
        from visualizer import roster

        root_dir = tmp_path / "root"
        tasks_dir = root_dir / "tasks" / "team-x"
        tasks_dir.mkdir(parents=True)
        for i in range(1, roster.MAX_STORED_TASKS + 3):
            path = tasks_dir / f"{i}.json"
            path.write_text("{}")
            os.utime(path, (1_000_000, 1_000_000))
        os.utime(tasks_dir, (1_000_000, 1_000_000))
        selected = roster.task_files(root_dir / "tasks", "team-x")
        assert [path.stem for path in selected][-1] == str(roster.MAX_STORED_TASKS + 2)
        assert selected[0].stem == "3"
        base = roster.activity_mtime_ns(root_dir, "team-x")
        os.utime(tasks_dir / "1.json", (2_000_000, 2_000_000))
        assert roster.activity_mtime_ns(root_dir, "team-x") == base
        newest = tasks_dir / f"{roster.MAX_STORED_TASKS + 2}.json"
        os.utime(newest, (3_000_000, 3_000_000))
        assert roster.activity_mtime_ns(root_dir, "team-x") == 3_000_000 * 10**9


class TestTaskStatusClosedSet:
    def test_status_vocabulary_and_precedence(self, tmp_path):
        payload = _build(
            tmp_path,
            [
                {"type": "agent_dispatch", "agent": "a", "task_id": "1", "phase": "CODE"},
                {"type": "remediation", "task_id": "2", "cycle": 1, "items": [], "fixer": "f"},
                {"type": "agent_handoff", "agent": "c", "task_id": "3", "task_subject": "s",
                 "handoff": {}},
                {"type": "checkpoint", "phase": "CODE",
                 "active_agents": [{"name": "d", "task_id": "4", "status": "observing a2ea5a0"}]},
            ],
            tasks=[{"id": "3", "subject": "s", "status": "in_progress"},
                   {"id": "5", "subject": "done", "status": "completed"},
                   {"id": "6", "subject": "gone", "status": "deleted"}],
        )
        status = {task["id"]: task["status"] for task in payload["tasks"]}
        assert status == {"1": "active", "2": "active", "3": "handoff", "4": "pending",
                          "5": "completed"}
        assert {task["status"] for task in payload["tasks"]} <= {
            "pending", "active", "handoff", "completed"}
        assert payload["checkpoints"][0]["active_agents"][0]["note"] == "observing a2ea5a0"
        assert "status" not in payload["checkpoints"][0]["active_agents"][0]


class TestAlertsFromSignalTasks:
    def test_blocker_and_algedonic_tasks_become_alerts(self, tmp_path):
        payload = _build(
            tmp_path,
            [_snapshot("7", "o7", subject="Security HALT", type="algedonic", level="HALT",
                       category="META-BLOCK")],
            tasks=[{"id": "8", "subject": "tests cannot run", "status": "completed",
                    "metadata": {"type": "blocker", "category": "test-environment",
                                 "resolution": {"by": "lead", "how": "fixed venv"}}},
                   {"id": "9", "subject": "ordinary", "status": "pending"}],
        )
        alerts = {alert["task_id"]: alert for alert in payload["alerts"]}
        assert set(alerts) == {"7", "8"}
        halt = alerts["7"]
        assert (halt["kind"], halt["level"], halt["category"], halt["category_key"]) == (
            "algedonic", "halt", "META-BLOCK", "meta-block")
        assert isinstance(halt["seq"], int) and halt["resolved"] is False
        blocker = alerts["8"]
        assert (blocker["kind"], blocker["level"], blocker["seq"]) == ("blocker", "alert", None)
        assert blocker["resolved"] is True and '"how": "fixed venv"' in blocker["resolution"]
        assert set(halt) == {"seq", "ts", "kind", "level", "category", "category_key",
                             "summary", "task_id", "task_key", "status", "resolved",
                             "resolution"}
        assert payload["metrics"]["alerts"] == 2
        kinds = {task["id"]: task["kind"] for task in payload["tasks"]}
        assert kinds["7"] == "algedonic" and kinds["8"] == "blocker"
        assert all("_signal" not in task for task in payload["tasks"])


class TestTeachbackAttachesToItsGate:
    def test_ack_marks_the_gate_and_blocks_carry_teachback_to_the_work_task(self, tmp_path):
        payload = _build(
            tmp_path,
            [{"type": "teachback_ack", "task_id": "10", "rationale_articulates_this_dispatch": "yes"}],
            tasks=[{"id": "10", "subject": "coder: TEACHBACK for x", "status": "completed",
                    "blocks": ["11"]},
                   {"id": "11", "subject": "coder: implement x", "status": "pending"}],
        )
        tasks = {task["id"]: task for task in payload["tasks"]}
        assert tasks["10"]["kind"] == "gate"
        assert tasks["10"]["teachback"] is True
        assert tasks["11"]["teachback"] is True
        assert tasks["11"]["status"] == "pending"


class TestTaskCardsAreSubjectSegments:
    """Owner changes never split a card; a new subject after a restart does."""

    def test_records_before_the_first_snapshot_join_it(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "task_created", "task_id": "5", "subject": "work"},
            {"type": "agent_dispatch", "agent": "coder", "task_id": "5", "phase": "CODE"},
            {"type": "checkpoint", "phase": "CODE",
             "active_agents": [{"name": "coder", "task_id": "5", "status": "working"}]},
            _snapshot("5", "occ5", subject="work"),
        ])
        cards = [task for task in payload["tasks"] if task["id"] == "5"]
        assert [card["key"] for card in cards] == ["5:occ5"]
        assert cards[0]["status"] == "active"

    def test_owner_assignment_on_the_same_subject_is_one_card(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("7", "occ-no-owner", subject="build x"),
            {"type": "agent_dispatch", "agent": "coder", "task_id": "7", "phase": "CODE"},
            _snapshot("7", "occ-with-owner", subject="build x"),
            {"type": "agent_handoff", "agent": "coder", "task_id": "7",
             "task_subject": "build x", "handoff": {}},
        ])
        cards = [task for task in payload["tasks"] if task["id"] == "7"]
        assert [card["key"] for card in cards] == ["7:occ-no-owner"]
        assert cards[0]["status"] == "handoff"
        assert payload["handoffs"][0]["task_key"] == "7:occ-no-owner"

    def test_a_new_subject_after_a_restart_is_a_second_card(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("8", "occ-old", subject="old task"),
            {"type": "agent_handoff", "agent": "a", "task_id": "8", "task_subject": "old task",
             "handoff": {}},
            {"type": "session_start", "session_id": "s1", "project_dir": "/x/proj",
             "source": "resume"},
            {"type": "agent_dispatch", "agent": "b", "task_id": "8", "phase": "CODE"},
            _snapshot("8", "occ-new", subject="new task"),
        ], tasks=[{"id": "8", "subject": "new task", "status": "in_progress"}])
        cards = {task["key"]: task for task in payload["tasks"] if task["id"] == "8"}
        assert set(cards) == {"8:occ-old", "8:occ-new"}
        assert cards["8:occ-old"]["status"] == "handoff"
        assert cards["8:occ-new"]["status"] == "active"
        assert cards["8:occ-new"]["owner"] == "b"

    def test_a_new_subject_without_a_restart_splits_at_its_snapshot(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("9", "occ-a", subject="first"),
            {"type": "agent_dispatch", "agent": "a", "task_id": "9", "phase": "CODE"},
            _snapshot("9", "occ-b", subject="second"),
            {"type": "agent_handoff", "agent": "b", "task_id": "9", "task_subject": "second",
             "handoff": {}},
        ])
        cards = {task["key"]: task["status"] for task in payload["tasks"] if task["id"] == "9"}
        assert cards == {"9:occ-a": "active", "9:occ-b": "handoff"}

    def test_an_id_with_no_snapshot_keeps_the_empty_occupant(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "agent_dispatch", "agent": "coder", "task_id": "6", "phase": "CODE"},
        ])
        assert [task["key"] for task in payload["tasks"]] == ["6:"]



class TestCapNeverDropsAnAlertCard:
    def test_an_old_signal_task_survives_the_cap(self, tmp_path):
        from visualizer import settings as viz_settings

        count = viz_settings.MAX_TASKS + 10
        records = [{"id": str(i), "subject": f"t{i}", "status": "pending"}
                   for i in range(2, count + 2)]
        records.append({"id": "1", "subject": "old blocker", "status": "pending",
                        "metadata": {"type": "blocker"}})
        payload = _build(tmp_path, [], tasks=records)
        keys = {task["key"] for task in payload["tasks"]}
        assert len(payload["tasks"]) == viz_settings.MAX_TASKS
        assert all(alert["task_key"] in keys for alert in payload["alerts"])
        assert payload["alerts"] and payload["alerts"][0]["task_id"] == "1"

class TestGateCardsAndKeyCollisions:
    def test_a_teachback_ack_completes_its_own_gate_card(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("20", "occ20", subject="plain subject without the word"),
            {"type": "teachback_ack", "task_id": "20", "rationale_articulates_this_dispatch": "yes"},
            {"type": "agent_dispatch", "agent": "coder", "task_id": "21", "phase": "CODE"},
        ])
        cards = {task["id"]: task for task in payload["tasks"]}
        assert (cards["20"]["status"], cards["20"]["kind"]) == ("completed", "gate")
        assert cards["21"]["status"] == "active"

    def test_subjects_a_b_a_under_one_owner_get_three_distinct_keys(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("12", "same", subject="A"),
            _snapshot("12", "other", subject="B"),
            _snapshot("12", "same", subject="A"),
        ])
        keys = [task["key"] for task in payload["tasks"] if task["id"] == "12"]
        assert keys == ["12:same", "12:other", "12:same:2"]

class TestBlankSubjectNeverSplits:
    @pytest.mark.parametrize("blank", ["", "(no subject)"])
    def test_a_blank_subject_snapshot_stays_on_its_card(self, tmp_path, blank):
        payload = _build(tmp_path, [
            _snapshot("2", "occ-real", subject="real subject"),
            {"type": "session_start", "session_id": "s1", "project_dir": "/x/proj",
             "source": "compact"},
            _snapshot("2", "occ-blank", subject=blank),
            _snapshot("2", "occ-real-2", subject="real subject"),
        ])
        cards = [task for task in payload["tasks"] if task["id"] == "2"]
        assert [card["key"] for card in cards] == ["2:occ-real"]
        assert cards[0]["subject"] == "real subject"

    def test_a_blank_first_subject_takes_the_later_real_one(self, tmp_path):
        payload = _build(tmp_path, [
            _snapshot("3", "occ-blank", subject=""),
            _snapshot("3", "occ-real", subject="named later"),
        ])
        cards = [task for task in payload["tasks"] if task["id"] == "3"]
        assert [card["key"] for card in cards] == ["3:occ-blank"]


def _events_of(payload, event_type):
    return [event for event in payload["events"] if event["type"] == event_type]


class TestEventsCarryStageInputs:
    @pytest.mark.parametrize("phase, family", [
        ("PREPARE", "PREPARE"), ("plan", "PREPARE"), ("INVESTIGATE", "PREPARE"),
        ("ARCHITECT", "ARCHITECT"), ("EXECUTE", "CODE"), ("OPERATOR_DEV", "CODE"),
        ("deploy", "CODE"), ("QA", "TEST"), ("VERIFY", "TEST"), ("REVIEW", "REVIEW"),
        ("REVIEW-REMEDIATION", "REVIEW"), ("REVIEW_REMEDIATION", "REVIEW"),
        ("peer-review", "REVIEW"), ("WRAP", "OTHER"), ("", "OTHER"),
    ])
    def test_phase_family_mapping(self, phase, family):
        from visualizer import model

        assert model.phase_family(phase) == family

    def test_events_without_a_phase_inherit_the_current_phase(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "phase_transition", "phase": "REVIEW", "status": "started"},
            {"type": "review_finding", "severity": "minor", "finding": "f", "reviewer": "rev-a"},
        ])
        finding = _events_of(payload, "review_finding")[0]
        assert finding["phase_family"] == "REVIEW"
        assert finding["agent"] == "rev-a"

    def test_review_dispatch_lists_its_reviewers_and_they_join_the_crew(self, tmp_path):
        payload = _build(tmp_path, [{"type": "review_dispatch", "pr_number": 5, "pr_url": "u",
                                     "reviewers": ["rev-a", "rev-b"]}])
        assert _events_of(payload, "review_dispatch")[0]["agents"] == ["rev-a", "rev-b"]
        assert {"rev-a", "rev-b"} <= {agent["name"] for agent in payload["agents"]}

    def test_dispatch_decision_agent_is_its_name_and_null_stays_null(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "dispatch_decision", "decision": "ALLOW", "name": "coder-a"},
            {"type": "dispatch_decision", "decision": "WARN", "name": None},
        ])
        named, unnamed = _events_of(payload, "dispatch_decision")
        assert named["agent"] == "coder-a" and named["severity"] == "success"
        assert unnamed["agent"] is None and unnamed["severity"] == "warning"

    def test_dispatch_site_and_teachback_ack_take_their_task_owner(self, tmp_path):
        payload = _build(
            tmp_path,
            [{"type": "dispatch_site", "task_id": "4", "variety": {"novelty": 2, "scope": 2,
                                                                  "uncertainty": 2, "risk": 2}},
             {"type": "teachback_ack", "task_id": "3", "rationale_articulates_this_dispatch": "yes"},
             {"type": "dispatch_site", "task_id": "99"}],
            tasks=[{"id": "3", "subject": "x: TEACHBACK", "owner": "coder-g", "status": "completed"},
                   {"id": "4", "subject": "x: work", "owner": "coder-w", "status": "in_progress"}],
        )
        site, unknown = _events_of(payload, "dispatch_site")
        assert (site["title"], site["detail"], site["agent"]) == (
            "Dispatch site · task #4", "variety 8", "coder-w")
        assert unknown["agent"] is None
        assert _events_of(payload, "teachback_ack")[0]["agent"] == "coder-g"


class TestClassifyFollowsTheWriters:
    def test_bare_and_wrap_up_session_ends(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "session_end"},
            {"type": "session_start", "session_id": "s1", "project_dir": "/x/proj",
             "source": "resume"},
            {"type": "session_end", "source": "wrap-up", "pr_state": "NONE"},
        ])
        bare, wrap = _events_of(payload, "session_end")
        assert (bare["category"], bare["icon"], bare["severity"], bare["title"]) == (
            "session", "session", "muted", "Session process ended")
        assert "wrapup" not in bare
        assert (wrap["category"], wrap["icon"], wrap["wrapup"], wrap["detail"]) == (
            "pr", "pr", True, "no PR")

    def test_writer_field_names(self, tmp_path):
        payload = _build(tmp_path, [
            {"type": "commit", "sha": "abc", "message": "m", "phase": "CODE", "note": "amended"},
            {"type": "session_paused", "pr_number": 1, "pr_url": "u", "branch": "b",
             "worktree_path": "/w/one", "consolidation_completed": True},
            {"type": "session_refreshed", "consolidation_completed": True, "halt_active": False,
             "worktrees": ["/w/a", "/w/b"]},
            {"type": "pin_marker_write", "route": "pin-memory", "command": "add",
             "outcome": "written"},
            {"type": "pin_prune_skipped", "outcome": "declined", "pin_count": 3,
             "reason": "user kept them"},
            {"type": "variety_assessed", "task_id": "1", "variety": {
                "novelty": {"score": 3, "rationale": "r"}, "scope": 2, "uncertainty": 2, "risk": 1}},
        ])
        assert _events_of(payload, "commit")[0]["detail"] == "amended"
        assert "/w/one" in _events_of(payload, "session_paused")[0]["detail"]
        assert "/w/a, /w/b" in _events_of(payload, "session_refreshed")[0]["detail"]
        pin = _events_of(payload, "pin_marker_write")[0]
        assert (pin["title"], pin["detail"]) == ("Pin marker · written", "pin-memory · add")
        assert _events_of(payload, "pin_prune_skipped")[0]["detail"] == "user kept them"
        variety = _events_of(payload, "variety_assessed")[0]
        assert variety["title"].startswith("Variety assessed")
        assert variety["detail"].startswith("novelty 3 ")


class TestCapsKeepTheNewestAndSayWhich:
    def test_events_cap_names_itself_and_keeps_the_newest(self, tmp_path, monkeypatch):
        from visualizer import model

        monkeypatch.setattr(model, "MAX_EVENTS", 5)
        payload = _build(tmp_path, [{"type": "commit", "sha": str(i), "message": "m",
                                     "phase": "CODE"} for i in range(9)])
        assert payload["truncated"] is True and "events" in payload["truncated_by"]
        assert payload["events"][-1]["title"].startswith("Commit 8")
        assert payload["events"][0]["seq"] > 0

    def test_over_cap_journal_parses_its_tail_with_true_seq(self, tmp_path, monkeypatch):
        from visualizer import discovery, journal, model

        monkeypatch.setattr(journal, "MAX_JOURNAL_BYTES", 400)
        root_dir = tmp_path / "root"
        events = [_START] + [{"type": "commit", "sha": f"{i:03d}", "message": "m" * 20,
                              "phase": "CODE"} for i in range(30)]
        path = _write_session(root_dir, events)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        payload = model.build_model(discovery.find_session(roots, "proj", "s1"))
        assert "journal_bytes" in payload["truncated_by"]
        lines = path.read_text().splitlines()
        for event in payload["events"]:
            assert json.loads(lines[event["seq"]])["type"] == event["type"]
        assert payload["events"][-1]["seq"] == len(lines) - 1
        assert any("last 400 bytes" in warning for warning in payload["warnings"])

    def test_trivial_headless_sessions_are_hidden_by_default(self, tmp_path):
        from visualizer import model

        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START], session_id="tiny", age_s=3600)
        _write_session(root_dir, [_START, {"type": "phase_transition", "phase": "CODE",
                                           "status": "started"}],
                       session_id="orch", age_s=3600)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        listing = model.build_sessions_list(roots)
        assert [entry["session_id"] for entry in listing["sessions"]] == ["orch"]
        assert (listing["total"], listing["hidden_trivial"], listing["limit"]) == (1, 1, 500)
        everything = model.build_sessions_list(roots, show_all=True)
        assert {entry["session_id"]: entry["trivial"] for entry in everything["sessions"]} == {
            "tiny": True, "orch": False}

    def test_project_display_and_collision(self, tmp_path):
        from visualizer import model

        root_dir = tmp_path / "root"
        _write_session(root_dir, [{**_START, "project_dir": "/x/My App"}], slug="a",
                       session_id="s1")
        _write_session(root_dir, [{**_START, "project_dir": "/x/My_App"}], slug="b",
                       session_id="s2")
        _write_session(root_dir, [{**_START, "project_dir": "/x/caf\u00e9\u0007"}], slug="c",
                       session_id="s3")
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        entries = {entry["session_id"]: entry for entry in
                   model.build_sessions_list(roots, show_all=True)["sessions"]}
        assert entries["s1"]["project"] == entries["s2"]["project"] == "My_App"
        assert entries["s1"]["project_collision"] and entries["s2"]["project_collision"]
        assert entries["s1"]["project_display"] == "My App"
        assert entries["s3"]["project_display"] == "caf\u00e9"
        assert entries["s3"]["project_collision"] is False

    def test_api_sessions_all_shows_trivial_and_echoes(self, live_server):
        payload = _get_json(live_server.server_port, "/api/sessions?all=1", live_server.key)
        assert payload["filters"] == {"all": "1"}
        assert payload["hidden_trivial"] == 0


class TestServerAnswersOnlyWellFormedRequests:
    def test_http_0_9_gets_no_body(self, live_server):
        raw = _read_until_close(live_server.server_port, b"GET /api/health\r\n")
        assert b'"ok"' not in raw and b"instance_id" not in raw

    @pytest.mark.parametrize("path", ["/api/health", "/", "/static/favicon.svg"])
    def test_every_response_is_no_store_and_same_origin_only(self, live_server, path):
        raw = _read_until_close(
            live_server.server_port,
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n".encode()
            + f"{settings.KEY_HEADER}: {live_server.key}\r\n\r\n".encode(),
        ).lower()
        for header in (b"cache-control: no-store", b"cross-origin-resource-policy: same-origin",
                       b"cross-origin-opener-policy: same-origin"):
            assert header in raw

    def test_server_header_has_no_trailing_space(self, live_server):
        raw = _read_until_close(
            live_server.server_port, b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        )
        line = next(l for l in raw.split(b"\r\n") if l.lower().startswith(b"server:"))
        assert line == f"Server: {settings.APP_NAME}/{settings.VERSION}".encode()

    @pytest.mark.parametrize("host", ["127.0.0.1:1 evil", "localhost:47811@evil", "127.0.0.1:abc",
                                      "evil.example", ""])
    def test_strict_host(self, live_server, host):
        raw = _read_until_close(
            live_server.server_port,
            f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode(),
        )
        assert raw.startswith(b"HTTP/1.1 403")

    def test_limit_takes_ascii_digits_only(self, live_server):
        status, _body = _get(live_server.server_port, "/api/sessions?limit=%EF%BC%91",
                             live_server.key)
        assert status == 400

    def test_head_is_200_for_static_and_405_for_api(self, live_server):
        page = _read_until_close(
            live_server.server_port, b"HEAD / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        )
        head, _sep, body = page.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200") and body == b""
        assert b"content-length:" in head.lower()
        api = _read_until_close(
            live_server.server_port,
            b"HEAD /api/sessions HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
        )
        assert api.startswith(b"HTTP/1.1 405")

    def test_no_stray_loop_variable_on_the_handler(self):
        assert not hasattr(server_module.MissionControlHandler, "_method")

    def test_non_finite_floats_encode_as_null(self):
        body, status = server_module._encode_json(
            {"a": float("nan"), "b": [float("inf")], "c": 1.5}, 200)
        assert (body, status) == (b'{"a":null,"b":[null],"c":1.5}', 200)


class TestServerBoundsItsWork:
    def test_trickling_connections_never_lock_out_the_key_holder(self, live_server, monkeypatch):
        import time

        monkeypatch.setattr(server_module, "MAX_CONNECTIONS", 3)
        tricklers = []
        for _ in range(3):
            sock = socket.create_connection(("127.0.0.1", live_server.server_port))
            sock.sendall(b"GET /api/hea")
            tricklers.append(sock)
        try:
            time.sleep(0.2)
            for _ in range(3):
                status, _ = _get(live_server.server_port, "/api/health", live_server.key)
                assert status == 200
            closed = 0
            for sock in tricklers:
                sock.settimeout(2)
                try:
                    closed += sock.recv(100) == b""
                except OSError:
                    closed += 1
            assert closed >= 1
        finally:
            for sock in tricklers:
                sock.close()

    def test_evicting_a_static_response_closes_it_cleanly(self, live_server, monkeypatch, tmp_path):
        """A victim mid-static-response gets a short body then EOF, never a full one."""
        import time

        static = tmp_path / "static"
        static.mkdir()
        size = 16 * 1024 * 1024  # far beyond loopback socket buffers
        (static / "big.txt").write_bytes(b"x" * size)
        monkeypatch.setattr(server_module, "_static_root", lambda: static)
        monkeypatch.setattr(server_module, "MAX_CONNECTIONS", 1)
        victim = socket.create_connection(("127.0.0.1", live_server.server_port))
        try:
            # Ask for the big file and do not read, so the server blocks
            # mid-body on an unkeyed (evictable) request.
            victim.sendall(b"GET /static/big.txt HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            time.sleep(0.3)
            status, _ = _get(live_server.server_port, "/api/health", live_server.key)
            assert status == 200
            victim.settimeout(5)
            received = 0
            try:
                while True:
                    chunk = victim.recv(1 << 20)
                    if not chunk:
                        break
                    received += len(chunk)
            except OSError:
                pass
            assert received < size
        finally:
            victim.close()
        deadline = time.time() + 5
        while live_server.connections and time.time() < deadline:
            time.sleep(0.05)
        assert live_server.connections == 0

    def test_slots_serving_keyed_requests_are_never_evicted(self, live_server, monkeypatch):
        monkeypatch.setattr(server_module, "MAX_CONNECTIONS", 2)
        keyed = [object(), object()]
        with live_server.connections_lock:
            for marker in keyed:
                live_server.tracked[marker] = [0.0, True]
        closed: list = []
        monkeypatch.setattr(live_server, "shutdown_request", lambda request: closed.append(request))
        newcomer = object()
        live_server.process_request(newcomer, ("127.0.0.1", 1))
        assert closed == [newcomer]
        with live_server.connections_lock:
            assert all(marker in live_server.tracked for marker in keyed)
            for marker in keyed:
                del live_server.tracked[marker]

    def test_an_unchanged_session_is_built_once(self, tmp_path, monkeypatch):
        from visualizer import model

        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START], age_s=3600)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        srv = server_module.create_server(roots)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        calls: list[int] = []
        real = model.build_model
        monkeypatch.setattr(model, "build_model", lambda *a, **k: calls.append(1) or real(*a, **k))
        try:
            first = _get(srv.server_port, "/api/session?slug=proj&session_id=s1", srv.key)
            second = _get(srv.server_port, "/api/session?slug=proj&session_id=s1", srv.key)
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
        assert first == second and first[0] == 200
        assert calls == [1]

    def test_the_session_list_walks_the_roots_once(self, tmp_path, monkeypatch):
        from visualizer import discovery, model

        root_dir = tmp_path / "root"
        _write_session(root_dir, [_START])

        def no_second_walk(root):
            raise AssertionError("root_payload walked the roots again")

        monkeypatch.setattr(discovery, "count_root_journals", no_second_walk)
        roots = settings.resolve_roots([root_dir], env={}, home=tmp_path)
        payload = model.build_sessions_list(roots, show_all=True)
        assert payload["roots"][0]["journals"] == 1


class TestIncrementalParse:
    def test_appends_parse_only_new_lines_with_true_seq(self, tmp_path):
        from visualizer import journal

        path = tmp_path / "session-journal.jsonl"
        path.write_text('{"type":"a"}\n{"type":"b"}\n{"type":"c"')
        first = journal.read_journal(path)
        # The unfinished last line is skipped for this read and not cached.
        assert [(event.seq, event.data["type"]) for event in first.events] == [
            (0, "a"), (1, "b")]
        assert first.skipped_lines == 1
        with path.open("a") as handle:
            handle.write('}\nnot json\n{"type":"d"}\n')
        second = journal.read_journal(path)
        assert [(event.seq, event.data["type"]) for event in second.events] == [
            (0, "a"), (1, "b"), (2, "c"), (4, "d")]
        assert second.skipped_lines == 1
        assert [event.seq for event in first.events] == [0, 1]

    def test_a_truncated_or_replaced_journal_is_parsed_in_full(self, tmp_path):
        from visualizer import journal

        path = tmp_path / "session-journal.jsonl"
        path.write_text('{"type":"a"}\n{"type":"b"}\n')
        journal.read_journal(path)
        path.write_text('{"type":"z"}\n')
        assert [event.data["type"] for event in journal.read_journal(path).events] == ["z"]
        replacement = tmp_path / "other.jsonl"
        replacement.write_text('{"type":"y"}\n{"type":"x"}\n{"type":"w"}\n')
        os.replace(replacement, path)
        assert [event.data["type"] for event in journal.read_journal(path).events] == [
            "y", "x", "w"]


class _DeadChild:
    """A Popen stand-in whose child has already exited."""

    def poll(self):
        return 1

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 1


class TestCliChangesOnlyWhatItOwns:
    def test_an_existing_state_dir_keeps_its_mode(self, tmp_path):
        existing = tmp_path / "existing"
        existing.mkdir(mode=0o755)
        os.chmod(existing, 0o755)
        cli.write_state(existing, _state(5, pid=1, instance_id="i"))
        assert (existing.stat().st_mode & 0o777) == 0o755
        assert (settings.state_file(existing).stat().st_mode & 0o777) == 0o600

    def test_a_created_state_dir_is_private(self, tmp_path):
        created = tmp_path / "new" / "pact-viz"
        cli.write_state(created, _state(5, pid=1, instance_id="i"))
        assert (created.stat().st_mode & 0o777) == 0o700

    def test_a_symlinked_state_dir_is_refused_and_its_target_untouched(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(cli.CliError):
            cli.write_state(link, _state(5, pid=1, instance_id="i"))
        assert (target.stat().st_mode & 0o777) == 0o755
        assert not (target / "server.json").exists()

    @pytest.mark.parametrize("mode, outcome", [
        (0o777, "refused"), (0o722, "refused"), (0o1777, "refused"),
        (0o770, "warned"), (0o775, "warned"),
        (0o700, "silent"), (0o755, "silent"),
    ])
    def test_state_dir_write_bits(self, tmp_path, capsys, mode, outcome):
        shared = tmp_path / "shared"
        shared.mkdir()
        os.chmod(shared, mode)
        if outcome == "refused":
            with pytest.raises(cli.CliError) as excinfo:
                cli.write_state(shared, _state(5, pid=1, instance_id="i"))
            message = str(excinfo.value)
            assert "world-writable" in message and "chmod go-w" in message
            assert not (shared / "server.json").exists()
        else:
            cli.write_state(shared, _state(5, pid=1, instance_id="i"))
            assert cli.read_state(shared) is not None
            err = capsys.readouterr().err
            if outcome == "warned":
                assert "group-writable" in err and "chmod go-w" in err
                assert len(err.strip().splitlines()) == 1
            else:
                assert err == ""
        assert (shared.stat().st_mode & 0o7777) == mode

    def test_launch_page_open_is_exclusive_after_the_unlink(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        real_unlink = cli.Path.unlink

        def unlink_then_race(self, missing_ok=False):
            real_unlink(self, missing_ok=missing_ok)
            self.write_text("planted by someone else")

        monkeypatch.setattr(cli.Path, "unlink", unlink_then_race)
        with pytest.raises(FileExistsError):
            cli._write_launch_page(state_dir, "http://127.0.0.1:5/#k=SECRET")
        assert "SECRET" not in (state_dir / "launch.html").read_text()

    def test_bool_pid_or_port_is_not_a_state_file(self, tmp_path):
        for broken in ({"pid": True}, {"port": True}):
            state_dir = tmp_path / f"s{len(broken)}{list(broken)[0]}"
            cli.write_state(state_dir, {**_state(5, pid=4, instance_id="i"), **broken})
            assert cli.read_state(state_dir) is None

    def test_a_non_http_listener_on_the_recorded_port_reads_as_not_ours(self, tmp_path, capsys):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = listener.getsockname()[1]

        def answer_garbage():
            for _ in range(4):
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                with conn:
                    conn.recv(4096)
                    conn.sendall(b"-ERR not http\r\n")

        threading.Thread(target=answer_garbage, daemon=True).start()
        try:
            assert cli.health_check(port, "k") is None
            state_dir = tmp_path / "state"
            cli.write_state(state_dir, _state(port, pid=2**22 + 1, instance_id="i"))
            assert cli.main(["--status", "--state-dir", str(state_dir)]) == 0
            assert "Traceback" not in capsys.readouterr().err
        finally:
            listener.close()

    def test_stop_keeps_the_record_of_a_silent_live_server(self, tmp_path, capsys, monkeypatch):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        try:
            state_dir = tmp_path / "state"
            cli.write_state(state_dir, _state(listener.getsockname()[1], pid=os.getpid(),
                                              instance_id="i"))
            monkeypatch.setattr(cli, "health_check", lambda *a, **k: None)
            assert cli.main(["--stop", "--state-dir", str(state_dir)]) == 1
            assert "Stop: unconfirmed pid" in capsys.readouterr().out
            assert cli.read_state(state_dir) is not None
        finally:
            listener.close()

    def test_a_dead_pid_with_no_listener_is_cleaned(self, tmp_path, capsys):
        state_dir = tmp_path / "state"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        cli.write_state(state_dir, _state(free_port, pid=2**22 + 1, instance_id="i"))
        assert cli.main(["--stop", "--state-dir", str(state_dir)]) == 0
        assert cli.read_state(state_dir) is None


class TestCliNeverHangs:
    def test_open_is_skipped_on_a_headless_session(self, monkeypatch):
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
        monkeypatch.setattr(cli.webbrowser, "open", lambda url: pytest.fail("opened"))
        assert cli.open_browser("file:///x") is False

    def test_a_slow_browser_does_not_hold_the_cli(self, monkeypatch):
        import time

        monkeypatch.setattr(cli, "_headless", lambda: False)
        monkeypatch.setattr(cli, "_BROWSER_WAIT_S", 0.2)
        monkeypatch.setattr(cli.webbrowser, "open", lambda url: time.sleep(5) or True)
        started = time.monotonic()
        cli.open_browser("file:///x")
        assert time.monotonic() - started < 2

    def test_a_closed_pipe_is_a_quiet_exit(self, tmp_path, monkeypatch, capsys):
        def broken(args):
            raise BrokenPipeError()

        monkeypatch.setattr(cli, "cmd_list", broken)
        assert cli.main(["--list", "--root", str(tmp_path)]) == 0
        assert "Traceback" not in capsys.readouterr().err

    def test_a_child_that_exits_says_so_and_leaves_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: _DeadChild())
        state_dir = tmp_path / "state"
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path), "--state-dir", str(state_dir)])
        with pytest.raises(cli.CliError) as excinfo:
            cli.detach(args, cli._resolve_roots(args), state_dir)
        assert "exited before it was ready" in str(excinfo.value)
        assert not (state_dir / "server.log").exists()

    def test_a_child_that_stepped_aside_is_a_reuse(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: _DeadChild())
        answers = iter([False, True])
        monkeypatch.setattr(cli, "_reuse_if_running", lambda *a, **k: next(answers))
        state_dir = tmp_path / "state"
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path), "--state-dir", str(state_dir)])
        assert cli.detach(args, cli._resolve_roots(args), state_dir) == 0

    def test_a_planted_log_symlink_is_never_followed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: _DeadChild())
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        os.chmod(victim, 0o644)
        (state_dir / "server.log").symlink_to(victim)
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path), "--state-dir", str(state_dir)])
        with pytest.raises(cli.CliError):
            cli.detach(args, cli._resolve_roots(args), state_dir)
        assert victim.read_text() == "keep" and (victim.stat().st_mode & 0o777) == 0o644


class TestCliOutputIsReadable:
    def test_list_indexes_roots_instead_of_clipping_them(self, tmp_path, capsys):
        root_dir = tmp_path / "a-very-long-root-directory-name-that-would-clip"
        _write_session(root_dir, [_START])
        assert cli.main(["--list", "--root", str(root_dir)]) == 0
        out = capsys.readouterr().out
        assert f"root #1: {root_dir}" in out
        assert any(line.startswith("#1 ") for line in out.splitlines())

    @pytest.mark.parametrize("suffix, message", [
        ("missing", "does not exist"), ("pact-sessions", "looks like a pact-sessions directory")])
    def test_odd_roots_are_named_on_stderr(self, tmp_path, capsys, suffix, message):
        target = tmp_path / suffix
        if suffix == "pact-sessions":
            target.mkdir()
        cli.main(["--list", "--root", str(target)])
        assert message in capsys.readouterr().err


class TestStateFilesMustBeOwned:
    """In a group-writable state dir, only this user's private files are trusted."""

    @staticmethod
    def _plant(state_dir, mode):
        state_dir.mkdir(mode=0o775, exist_ok=True)
        os.chmod(state_dir, 0o775)
        path = settings.state_file(state_dir)
        path.write_text(json.dumps(_state(5555, pid=4242, instance_id="forged")))
        os.chmod(path, mode)
        return path

    @pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
    def test_a_group_or_other_writable_record_is_ignored(self, tmp_path, capsys, mode):
        state_dir = tmp_path / f"state{mode:o}"
        self._plant(state_dir, mode)
        assert cli.read_state(state_dir) is None
        assert "writable by group or others" in capsys.readouterr().err

    def test_a_foreign_owned_record_is_ignored_and_never_reused(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / "state"
        self._plant(state_dir, 0o600)
        real_fstat = os.fstat

        def foreign_fstat(descriptor):
            info = real_fstat(descriptor)
            values = list(info)
            values[4] = info.st_uid + 1  # st_uid
            return os.stat_result(values)

        monkeypatch.setattr(cli.os, "fstat", foreign_fstat)
        probed: list = []
        monkeypatch.setattr(cli, "health_check", lambda *a, **k: probed.append(a) or None)
        args = cli.build_parser().parse_args(["--detach", "--state-dir", str(state_dir)])
        assert cli._reuse_if_running(state_dir, args, []) is False
        assert probed == []
        assert "not owned by this user" in capsys.readouterr().err

    def test_stop_never_signals_from_or_deletes_an_untrusted_record(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / "state"
        path = self._plant(state_dir, 0o666)
        signalled: list = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append(pid))
        assert cli.main(["--stop", "--state-dir", str(state_dir)]) == 1
        assert signalled == []
        assert path.exists()
        out = capsys.readouterr()
        assert "Stop: refused" in out.out and "ignoring" in out.err
        assert cli._clear_state_if_instance(state_dir, "anything") is False
        assert path.exists()

    def test_our_private_record_in_a_group_writable_dir_stays_trusted(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        self._plant(state_dir, 0o600)
        monkeypatch.setattr(cli, "health_check", lambda *a, **k: None)
        monkeypatch.setattr(cli, "_pid_gone", lambda pid: True)
        assert cli.main(["--stop", "--state-dir", str(state_dir)]) == 0
        assert not settings.state_file(state_dir).exists()

    def test_a_trusted_record_still_reads(self, tmp_path):
        state_dir = tmp_path / "state"
        self._plant(state_dir, 0o600)
        assert cli.read_state(state_dir)["instance_id"] == "forged"

    def test_a_held_launch_lock_fails_within_the_bound(self, tmp_path, monkeypatch):
        import fcntl
        import time

        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        monkeypatch.setattr(cli, "_LOCK_WAIT_S", 0.5)
        holder = os.open(state_dir / ".launch.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX)
            started = time.monotonic()
            with pytest.raises(cli.CliError) as excinfo:
                with cli._launch_lock(state_dir):
                    pass
            assert time.monotonic() - started < 3
            assert "another launch holds" in str(excinfo.value)
        finally:
            os.close(holder)

    def test_a_foreign_lock_file_is_refused(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        real_fstat = os.fstat

        def foreign_fstat(descriptor):
            values = list(real_fstat(descriptor))
            values[4] += 1
            return os.stat_result(values)

        monkeypatch.setattr(cli.os, "fstat", foreign_fstat)
        with pytest.raises(cli.CliError) as excinfo:
            with cli._launch_lock(state_dir):
                pass
        assert "not owned by this user" in str(excinfo.value)

    def test_an_untrusted_log_is_not_quoted(self, tmp_path):
        log = tmp_path / "server.log"
        log.write_text("planted text")
        os.chmod(log, 0o666)
        assert cli._log_tail(log) == ""
        os.chmod(log, 0o600)
        assert cli._log_tail(log) == "planted text"


class TestHandoffAndConfigRootMirrorTheHooks:
    """The viewer resolves handoff fields and the config root like the hooks."""

    _CASES = [
        {"decisions": "", "key_decisions": "alias"},
        {"decisions": [], "key_decisions": ["alias item"]},
        {"decisions": {}, "key_decisions": "alias"},
        {"decisions": None, "key_decisions": "alias"},
        {"decisions": 0, "key_decisions": "alias"},
        {"decisions": False, "key_decisions": "alias"},
        {"decisions": "canonical", "key_decisions": "alias"},
        {"uncertainty": [], "areas_of_uncertainty": []},
        {"integration": "", "integration_points": ""},
        {},
    ]

    @pytest.mark.parametrize("handoff", _CASES)
    def test_field_resolution_matches_the_ssot(self, handoff):
        from shared import handoff_schema

        from visualizer import classify

        for field in classify.HANDOFF_CANONICAL_FIELDS:
            assert classify._resolve_handoff_field(handoff, field) == (
                handoff_schema.resolve_handoff_field(handoff, field)
            ), field

    def test_structured_values_render_and_never_read_as_empty(self):
        from visualizer import classify

        fields = classify.extract_handoff_fields({
            "produced": {"files": ["a.py"], "api_token": "kept-out"},
            "decisions": [{"decision": "use x", "why": "y"}, "plain"],
            "uncertainty": 0,
            "integration": [],
            "integration_points": "alias content",
            "open_questions": [],
        })
        assert '"files": ["a.py"]' in fields["produced"]
        assert "kept-out" not in fields["produced"] and "[redacted]" in fields["produced"]
        assert fields["decisions"] == '{"decision": "use x", "why": "y"} · plain'
        assert fields["uncertainty"] == "0"
        assert fields["integration"] == "alias content"
        assert fields["open_questions"] == "(none)"
        assert fields["reasoning_chain"] is None

    @pytest.mark.parametrize("raw", ["~", "~/x", "~other/x", "/abs/root", "rel/root"])
    def test_config_root_matches_get_claude_config_dir(self, tmp_path, monkeypatch, raw):
        from shared.paths import get_claude_config_dir

        monkeypatch.chdir(tmp_path)
        environment = {"CLAUDE_CONFIG_DIR": raw}
        ours = settings.resolve_roots((), env=environment, home=tmp_path / "home")[0].path
        theirs = get_claude_config_dir(env=environment, home=tmp_path / "home")
        assert ours == cli.Path(os.path.abspath(theirs))


class TestPlantedFifosNeverHang:
    """A FIFO at a state-dir path is rejected promptly, never opened blocking."""

    @staticmethod
    def _run_bounded(function, *args):
        import time

        result: list = []
        worker = threading.Thread(target=lambda: result.append(function(*args)), daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(3)
        assert not worker.is_alive(), "the open blocked on a FIFO"
        assert time.monotonic() - started < 1.5
        return result[0]

    def test_a_fifo_at_server_json_is_ignored(self, tmp_path, capsys):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        os.mkfifo(settings.state_file(state_dir), 0o600)
        assert self._run_bounded(cli.read_state, state_dir) is None
        assert "not a regular file" in capsys.readouterr().err

    def test_a_fifo_at_the_log_is_not_quoted(self, tmp_path, capsys):
        log = tmp_path / "server.log"
        os.mkfifo(log, 0o600)
        assert self._run_bounded(cli._log_tail, log) == ""
        assert "not a regular file" in capsys.readouterr().err

    def test_a_fifo_at_the_lock_is_refused(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        os.mkfifo(state_dir / ".launch.lock", 0o600)

        def take_lock():
            try:
                with cli._launch_lock(state_dir):
                    return "locked"
            except cli.CliError as error:
                return str(error)

        assert "not a regular file" in self._run_bounded(take_lock)

    def test_a_fifo_at_the_log_path_fails_the_launch_promptly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: _DeadChild())
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        os.mkfifo(state_dir / "server.log", 0o600)
        args = cli.build_parser().parse_args(
            ["--detach", "--root", str(tmp_path), "--state-dir", str(state_dir)])

        def launch():
            try:
                cli.detach(args, cli._resolve_roots(args), state_dir)
            except cli.CliError as error:
                return str(error)
            return "launched"

        message = self._run_bounded(launch)
        assert "cannot start the server process" in message and "not a regular file" in message

    @pytest.mark.parametrize("command, code", [("--stop", 1), ("--status", 0)])
    def test_stop_and_status_leave_a_fifo_record_in_place(self, tmp_path, capsys, command, code):
        state_dir = tmp_path / "state"
        state_dir.mkdir(mode=0o700)
        record = settings.state_file(state_dir)
        os.mkfifo(record, 0o600)
        assert self._run_bounded(cli.main, [command, "--state-dir", str(state_dir)]) == code
        assert record.exists() and not record.is_file()
