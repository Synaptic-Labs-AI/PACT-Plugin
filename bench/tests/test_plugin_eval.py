from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from external.runner import plugin_eval

FAKE_UV = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
with open({calls!r}, "a") as calls:
    calls.write(json.dumps({{"cwd": os.getcwd(), "project": args[args.index("--project") + 1]}}) + "\\n")
if args[0] == "sync":
    open({log!r}, "w").write(json.dumps({{"token": "ANTHROPIC_AUTH_TOKEN" in os.environ}}))
    if os.environ.get("FAKE_SYNC_SLEEP"):
        sys.stderr.write("sync-marker\\n")
        sys.stderr.flush()
        time.sleep(10)
    sys.exit(int(os.environ.get("FAKE_SYNC_RC", "0")))
name = os.path.basename(args[args.index("score") + 1])
if name == "nullayers":
    print(json.dumps({{"composite": 1, "layers": None}}))
elif name == "slowkid":
    import subprocess
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    open({kid_pid!r}, "w").write(str(kid.pid))
    time.sleep(30)
elif name == "hang":
    import subprocess
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    open({kid_pid!r}, "w").write(str(kid.pid))
    open({kid_pid!r} + ".started", "w").write("1")
    time.sleep(30)
elif name == "badbytes":
    sys.stdout.buffer.write(b"\\xff\\xfe not utf-8\\n")
    print(json.dumps({{"composite": 7}}))
elif name == "nonjson":
    print("score finished")
elif name == "scalar":
    print("42")
elif name == "bad":
    print(json.dumps({{"composite": 0}}))
    sys.exit(3)
elif name == "slow":
    print("partial-out", flush=True)
    sys.stderr.write("late-marker\\n")
    sys.stderr.flush()
    time.sleep(10)
else:
    print("log line")
    print(json.dumps({{"composite": {{"score": 1}}, "layers": [{{"layer": "static"}}]}}))
"""


def _setup(tmp_path: Path, skills: list[str] | None) -> tuple[Path, Path, Path, Path]:
    scratch = tmp_path / "run"
    plugin = scratch / "pact-plugin"
    plugin.mkdir(parents=True)
    (scratch / "config").mkdir()
    (scratch / "config" / "settings.json").write_text(json.dumps({"env": {}}), encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("secret-token", encoding="utf-8")
    manifest = scratch / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "paths": {"out": str(scratch), "plugin": str(plugin)},
                "provider": {"token_file": str(token), "base_url": "https://example.invalid"},
            }
        ),
        encoding="utf-8",
    )
    tool = tmp_path / "plugin-eval"
    tool.mkdir()
    (tool / "pyproject.toml").write_text("", encoding="utf-8")
    log = tmp_path / "sync.json"
    uv = tmp_path / "uv"
    uv.write_text(
        FAKE_UV.format(
            python=sys.executable, log=str(log), kid_pid=str(tmp_path / "kid.pid"), calls=str(tmp_path / "calls.jsonl")
        ), encoding="utf-8"
    )
    uv.chmod(0o755)
    skills_file = tmp_path / "skills.txt"
    if skills is not None:
        skills_file.write_text("\n".join(skills) + "\n", encoding="utf-8")
    return manifest, tool, uv, skills_file


def _args(manifest: Path, tool: Path, uv: Path, *extra: str):
    return plugin_eval.build_parser().parse_args(
        [
            "--manifest", str(manifest), "--plugin-eval-dir", str(tool), "--uv", str(uv), "--timeout", "1",
            "--temp-root", str(manifest.parent.parent / "temp-root"), *extra,
        ]
    )


def _result(manifest: Path) -> dict:
    return json.loads((manifest.parent / "plugin-eval" / "result.json").read_text(encoding="utf-8"))


def test_build_commands_includes_plugin_and_each_skill(tmp_path: Path) -> None:
    plugin = tmp_path / "pact-plugin"
    project = tmp_path / "plugin-eval"
    sync, commands = plugin_eval.build_commands("/opt/uv", project, plugin, ["alpha", "beta"])
    assert sync[:2] == ["/opt/uv", "sync"]
    assert "--frozen" in sync
    for cmd in (sync, *(cmd for _, cmd in commands)):
        assert cmd[cmd.index("--project") + 1] == str(project)
    labels = [label for label, _ in commands]
    assert labels == [str(plugin), "alpha", "beta"]
    assert commands[0][1][0] == "/opt/uv"
    assert "quick" in commands[0][1]
    for label, cmd in commands[1:]:
        assert cmd[0] == "/opt/uv"
        assert str(plugin / "skills" / label) in cmd
        assert "standard" in cmd


def test_parse_json_accepts_trailing_json_line() -> None:
    parsed = plugin_eval._parse_json('info\n{"composite": {"score": 1}}\n')
    assert parsed["composite"]["score"] == 1


def test_parse_json_skips_scalar_lines_for_an_object() -> None:
    assert plugin_eval._parse_json('{"a": 1}\n42\n') == {"a": 1}
    with pytest.raises(ValueError):
        plugin_eval._parse_json("42\n")


def test_run_all_good_returns_zero_and_hides_token_from_sync(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 0
    assert json.loads((tmp_path / "sync.json").read_text(encoding="utf-8")) == {"token": False}
    records = _result(manifest)["results"]
    assert [r["label"] for r in records] == [str(manifest.parent / "pact-plugin"), "good"]
    assert all(r["exit_code"] == 0 and "parse_error" not in r for r in records)
    assert records[1]["layers"] == ["static"]
    summary = _result(manifest)
    assert (summary["complete"], summary["error"], summary["aborted_command"], summary["not_run"]) == (True, None, None, [])
    assert summary["sync"]["exit_code"] == 0


def test_run_null_layers_does_not_lose_later_records(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["nullayers", "good"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 0
    records = {r["label"]: r for r in _result(manifest)["results"]}
    assert records["nullayers"]["layers"] == []
    assert records["good"]["layers"] == ["static"]


def _dead(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return True
        if state == "Z":
            return True
        time.sleep(0.1)
    return False


def test_timeout_kills_grandchildren(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["slowkid"])
    started = time.monotonic()
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 1
    # A surviving grandchild holds the output pipe open until its 30s sleep ends.
    assert time.monotonic() - started < 15
    assert _result(manifest)["results"][1]["timed_out"] is True
    pid = int((tmp_path / "kid.pid").read_text())
    try:
        assert _dead(pid)
    finally:
        if not _dead(pid):
            os.kill(pid, 9)


@pytest.mark.parametrize(
    ("skill", "check"),
    [
        ("nonjson", lambda r: r["exit_code"] == 0 and "parse_error" in r),
        ("scalar", lambda r: r["exit_code"] == 0 and "parse_error" in r),
        ("bad", lambda r: r["exit_code"] == 3),
        (
            "slow",
            lambda r: r["exit_code"] == 124 and r["timed_out"] is True
            and "late-marker" in r["stderr_tail"] and "partial-out" in r["stdout_tail"],
        ),
    ],
)
def test_run_failing_command_returns_one_and_keeps_every_record(tmp_path: Path, skill: str, check) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, [skill, "good"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 1
    records = {r["label"]: r for r in _result(manifest)["results"]}
    assert check(records[skill])
    assert records["good"]["exit_code"] == 0 and "parse_error" not in records["good"]


def test_run_missing_uv_exits_clearly_and_writes_result(tmp_path: Path) -> None:
    manifest, tool, _, skills = _setup(tmp_path, ["good"])
    args = _args(manifest, tool, tmp_path / "no-uv", "--skills-file", str(skills), "--skip-sync")
    with pytest.raises(SystemExit, match="cannot run"):
        plugin_eval.run(args)
    summary = _result(manifest)
    assert summary["results"] == []
    assert summary["complete"] is False
    assert "cannot run" in summary["error"]
    assert summary["aborted_command"]["label"] == str(manifest.parent / "pact-plugin")
    assert summary["not_run"] == ["good"]


def test_run_failed_sync_is_recorded(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    settings = manifest.parent / "config" / "settings.json"
    settings.write_text(json.dumps({"env": {"FAKE_SYNC_RC": "5"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="uv sync failed: 5"):
        plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills)))
    summary = _result(manifest)
    assert summary["complete"] is False
    assert summary["error"] == "uv sync failed: 5"
    assert summary["sync"]["exit_code"] == 5
    assert summary["aborted_command"]["label"] == "sync"
    assert summary["not_run"] == [str(manifest.parent / "pact-plugin"), "good"]


def test_run_sync_timeout_records_its_output(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    settings = manifest.parent / "config" / "settings.json"
    settings.write_text(json.dumps({"env": {"FAKE_SYNC_SLEEP": "1"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="uv sync timed out"):
        plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills), "--sync-timeout", "1"))
    sync = _result(manifest)["sync"]
    assert sync["timed_out"] is True
    assert "sync-marker" in sync["stderr_tail"]


def test_unexpected_error_fails_only_its_command(tmp_path: Path) -> None:
    long_name = "x" * 300  # its output file name exceeds NAME_MAX, so saving raises OSError
    manifest, tool, uv, skills = _setup(tmp_path, [long_name, "good"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 1
    summary = _result(manifest)
    records = {r["label"]: r for r in summary["results"]}
    assert records[long_name]["exit_code"] is None
    assert records[long_name]["error"].startswith("OSError")
    assert records["good"]["exit_code"] == 0
    assert summary["complete"] is True


HARNESS = """import sys
sys.path.insert(0, {bench!r})
from external.runner import plugin_eval
raise SystemExit(plugin_eval.main({argv!r}))
"""


def test_sigterm_during_a_command_kills_the_group_and_is_recorded(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["hang"])
    args = _args(manifest, tool, uv, "--skills-file", str(skills), "--skip-sync", "--timeout", "60")
    argv = [
        "--manifest", args.manifest, "--plugin-eval-dir", args.plugin_eval_dir, "--uv", args.uv,
        "--skills-file", args.skills_file, "--skip-sync", "--timeout", "60", "--temp-root", args.temp_root,
    ]
    bench = str(Path(__file__).resolve().parents[1])
    started = time.monotonic()
    harness = subprocess.Popen(
        [sys.executable, "-c", HARNESS.format(bench=bench, argv=argv)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    marker = tmp_path / "kid.pid.started"
    while not marker.exists():
        assert time.monotonic() - started < 15 and harness.poll() is None, harness.communicate()
        time.sleep(0.05)
    harness.send_signal(signal.SIGTERM)
    harness.communicate(timeout=15)
    assert harness.returncode == 128 + signal.SIGTERM
    assert time.monotonic() - started < 15
    summary = _result(manifest)
    assert summary["error"] == "terminated by SIGTERM"
    assert summary["complete"] is False
    assert summary["aborted_command"]["label"] == "hang"
    pid = int((tmp_path / "kid.pid").read_text())
    assert _dead(pid)


def test_sigterm_between_commands_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    real = plugin_eval._score_one

    def score_then_signal(*args: object, **kwargs: object) -> dict:
        record = real(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)  # arrives while no child runs
        return record

    monkeypatch.setattr(plugin_eval, "_score_one", score_then_signal)
    with pytest.raises(SystemExit) as exc:
        plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills), "--skip-sync"))
    assert exc.value.code == 128 + signal.SIGTERM
    summary = _result(manifest)
    assert summary["error"] == "terminated by SIGTERM"
    # The signal landed inside the first command's step, so that command is the aborted one.
    assert summary["aborted_command"]["label"] == str(manifest.parent / "pact-plugin")
    assert summary["not_run"] == ["good"]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_commands_run_from_a_clean_cwd_with_the_checkout_as_project(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 0
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 3  # sync, the whole plugin, one skill
    temp_root = tmp_path / "temp-root"
    for call in calls:
        assert Path(call["cwd"]).parent == temp_root
        assert Path(call["cwd"]).name.startswith("pact-plugin-eval-")
        assert call["project"] == str(tool)
    assert not Path(calls[0]["cwd"]).exists()


def test_temp_root_below_a_memory_file_is_refused_and_recorded(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    dirty = tmp_path / "dirty"
    (dirty / "root").mkdir(parents=True)
    (dirty / "CLAUDE.md").write_text("memory\n", encoding="utf-8")
    args = _args(manifest, tool, uv, "--skills-file", str(skills), "--temp-root", str(dirty / "root"))
    with pytest.raises(SystemExit, match="memory files"):
        plugin_eval.run(args)
    summary = _result(manifest)
    assert "memory files" in summary["error"]
    assert summary["results"] == [] and not (tmp_path / "calls.jsonl").exists()


def test_non_utf8_output_keeps_its_score(tmp_path: Path) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["badbytes"])
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills))) == 0
    record = {r["label"]: r for r in _result(manifest)["results"]}["badbytes"]
    assert record["composite"] == 7


def test_missing_checkout_is_recorded(tmp_path: Path) -> None:
    manifest, _, uv, skills = _setup(tmp_path, ["good"])
    with pytest.raises(SystemExit, match="not a PluginEval checkout"):
        plugin_eval.run(_args(manifest, tmp_path / "nowhere", uv, "--skills-file", str(skills)))
    summary = _result(manifest)
    assert summary["complete"] is False and "not a PluginEval checkout" in summary["error"]


@pytest.mark.parametrize("manifest_text", ["{", '{"paths": {}}', "[]"])
def test_unreadable_manifest_exits_with_a_message(tmp_path: Path, manifest_text: str) -> None:
    manifest, tool, uv, _ = _setup(tmp_path, ["good"])
    manifest.write_text(manifest_text, encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot read prepare manifest"):
        plugin_eval.run(_args(manifest, tool, uv))


def test_skills_file_expands_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, ["good"])
    monkeypatch.setenv("HOME", str(tmp_path))
    assert plugin_eval.run(_args(manifest, tool, uv, "--skills-file", "~/" + skills.name)) == 0


@pytest.mark.parametrize("content", [None, "", "../escape\n", "a\nb\na\n"])
def test_run_rejects_a_bad_skills_file_and_records_it(tmp_path: Path, content: str | None) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, None)
    if content is not None:
        skills.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match="skills file"):
        plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills)))
    summary = _result(manifest)
    assert summary["complete"] is False and "skills file" in summary["error"]
    assert not (tmp_path / "calls.jsonl").exists()


def test_default_selection_is_the_data_file(tmp_path: Path) -> None:
    assert not hasattr(plugin_eval, "DEFAULT_SKILLS")
    expected = [
        line.strip()
        for line in plugin_eval.DEFAULT_SKILLS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert expected
    manifest, tool, uv, _ = _setup(tmp_path, None)
    plugin_eval.run(_args(manifest, tool, uv))
    labels = [r["label"] for r in _result(manifest)["results"]]
    assert labels[1:] == expected
