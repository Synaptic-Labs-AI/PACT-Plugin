from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from external.runner import native_eval, prepare


@pytest.fixture
def short_root() -> Iterator[Path]:
    """A --temp-root short enough for the TMPDIR budget (pytest's tmp_path is too long)."""
    root = Path(tempfile.mkdtemp(prefix="pnt-", dir="/tmp"))
    try:
        yield root
    finally:
        native_eval.make_writable(root)
        shutil.rmtree(root, ignore_errors=True)


def _prepared(tmp_path: Path) -> tuple[Path, Path, Path]:
    scratch = tmp_path / "run"
    plugin = scratch / "pact-plugin"
    plugin.mkdir(parents=True)
    (scratch / "config").mkdir()
    (scratch / "config" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "deepseek-flash[1m]", "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash[1m]"}}),
        encoding="utf-8",
    )
    token = tmp_path / "token"
    token.write_text("dummy", encoding="utf-8")
    manifest = scratch / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "paths": {"out": str(scratch), "plugin": str(plugin)},
                "provider": {
                    "token_file": str(token),
                    "base_url": "https://api.deepseek.com/anthropic",
                    "model": "deepseek-flash[1m]",
                },
            }
        ),
        encoding="utf-8",
    )
    cases = tmp_path / "cases"
    (cases / "probe").mkdir(parents=True)
    (cases / "probe" / "prompt.md").write_text("Reply OK", encoding="utf-8")
    return manifest, cases, plugin


def _fake_claude(tmp_path: Path, body: str = "") -> Path:
    """Fake claude that records its env, writes the reports, then runs body."""
    fake = tmp_path / "claude"
    env_out = tmp_path / "env.json"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        f"keys = ('TMPDIR', 'HOME', 'ANTHROPIC_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL')\n"
        f"open({str(env_out)!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in keys}}))\n"
        "args = sys.argv[1:]\n"
        "open(args[args.index('--json') + 1], 'w').write(json.dumps({'ok': True}))\n"
        "open(args[args.index('--report') + 1], 'w').write('<html></html>')\n"
        + body,
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _args(manifest: Path, cases: Path, claude: Path, temp_root: Path, *extra: str):
    return native_eval.build_parser().parse_args(
        [
            "--manifest", str(manifest),
            "--cases-dir", str(cases),
            "--claude-bin", str(claude),
            "--runs", "1",
            "--temp-root", str(temp_root),
            *extra,
        ]
    )


def _result(manifest: Path) -> dict:
    return json.loads((manifest.parent / "native-eval" / "result.json").read_text(encoding="utf-8"))


def _leftovers(root: Path) -> list[str]:
    return [path.name for path in root.glob("pact-native-*")]


def test_native_eval_injects_cases_and_records_command(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, plugin = _prepared(tmp_path)
    seen = tmp_path / "seen"
    fake = _fake_claude(tmp_path, f"import shutil; shutil.copytree({str(plugin / 'evals')!r}, {str(seen)!r})\n")
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
    child = json.loads((tmp_path / "env.json").read_text(encoding="utf-8"))
    assert child["TMPDIR"].startswith(str(short_root / "pact-native-"))
    assert child["HOME"].startswith(str(short_root / "pact-native-"))
    assert (seen / "probe" / "prompt.md").is_file()
    record = _result(manifest)
    assert record["exit_code"] == 0
    assert record["error"] is None and record["cleanup_error"] is None
    assert record["report_json"].endswith("report.json")
    assert any(str(plugin) in str(part) for part in record["command"])
    # The probe case lists no allowed_tools, so nothing is granted.
    assert "--allow-tools" not in record["command"] and record["tool_grants"] == []
    assert not (plugin / "evals").exists()
    assert _leftovers(short_root) == []


def test_native_eval_uses_the_prepared_model(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path)
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
    command = _result(manifest)["command"]
    assert command[command.index("--model") + 1] == "deepseek-flash[1m]"
    assert command[command.index("--judge-model") + 1] == "deepseek-flash[1m]"
    child = json.loads((tmp_path / "env.json").read_text(encoding="utf-8"))
    assert child["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-flash[1m]"


@pytest.mark.parametrize("flag", ["--model", "--judge-model"])
def test_native_eval_has_no_model_overrides(flag: str, tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _args(manifest, cases, _fake_claude(tmp_path), short_root, flag, "other")
    assert exc.value.code == 2


def _dead(pid: int, wait: float = 5.0) -> bool:
    """True once pid is gone or a zombie (no longer running)."""
    if not Path("/proc/self/stat").exists():
        pytest.skip("the liveness check needs /proc; without it a surviving process would read as dead")
    deadline = time.monotonic() + wait
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return True
        if state == "Z":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _wait_for(path: Path, wait: float = 10.0) -> str:
    deadline = time.monotonic() + wait
    while not (path.is_file() and path.read_text(encoding="utf-8").strip()):
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)
    return path.read_text(encoding="utf-8").strip()


def test_native_eval_timeout_kills_grandchildren(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    pid_file = tmp_path / "grandchild.pid"
    body = (
        "import subprocess\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    fake = _fake_claude(tmp_path, body)
    started = time.monotonic()
    try:
        assert native_eval.run(_args(manifest, cases, fake, short_root, "--timeout", "2")) == 1
        assert time.monotonic() - started < 15
        assert _result(manifest)["timed_out"] is True
        assert _dead(int(_wait_for(pid_file)))
    finally:
        if pid_file.is_file():
            _kill(int(pid_file.read_text(encoding="utf-8")))


def test_native_eval_runs_from_a_clean_cwd_outside_the_repo(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    seen = tmp_path / "cwd.txt"
    fake = _fake_claude(tmp_path, f"open({str(seen)!r}, 'w').write(os.getcwd())\n")
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
    cwd = Path(seen.read_text(encoding="utf-8"))
    assert str(cwd) == _result(manifest)["cwd"]
    assert cwd.parent.parent == short_root and cwd.name == "work"
    assert not cwd.is_relative_to(prepare.REPO_ROOT)
    assert prepare.memory_files_above(cwd) == []


def test_native_eval_refuses_a_temp_root_below_memory_files(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    # CLAUDE.local.md stands in for every memory file name; the test never writes a CLAUDE.md.
    (short_root / "CLAUDE.local.md").write_text("memory", encoding="utf-8")
    nested = short_root / "r"
    with pytest.raises(SystemExit, match="memory files"):
        native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), nested))
    assert "memory files" in _result(manifest)["error"]
    assert not nested.exists()


def test_native_eval_records_a_sigterm_and_cleans_up(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, plugin = _prepared(tmp_path)
    pid_file = tmp_path / "claude.pid"
    fake = _fake_claude(tmp_path, f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    harness = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(native_eval.__file__).resolve().parents[2])!r})\n"
        "from external.runner import native_eval\n"
        f"sys.exit(native_eval.main({['--manifest', str(manifest), '--cases-dir', str(cases), '--claude-bin', str(fake), '--runs', '1', '--temp-root', str(short_root)]!r}))\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", harness], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    claude = int(_wait_for(pid_file))
    try:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 128 + signal.SIGTERM
        assert _dead(claude)
        record = _result(manifest)
        assert record["error"] == "terminated by SIGTERM"
        assert _leftovers(short_root) == []
        assert not (plugin / "evals").exists()
    finally:
        proc.kill()
        proc.wait()
        _kill(claude)


def test_native_eval_unreadable_manifest_is_clear(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot read prepare manifest"):
        native_eval.main(["--manifest", str(bad)])


def test_native_eval_records_a_non_exit_error(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    (manifest.parent / "config" / "settings.json").unlink()
    with pytest.raises(FileNotFoundError):
        native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), short_root))
    assert _result(manifest)["error"].startswith("FileNotFoundError")
    assert _leftovers(short_root) == []


def test_native_eval_does_not_record_a_stale_report(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    out = manifest.parent / "native-eval"
    out.mkdir()
    (out / "report.json").write_text("{}", encoding="utf-8")
    (out / "report.html").write_text("<html></html>", encoding="utf-8")
    silent = tmp_path / "silent-claude"
    silent.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    silent.chmod(0o755)
    assert native_eval.run(_args(manifest, cases, silent, short_root)) == 0
    record = _result(manifest)
    assert record["report_json"] is None and record["report_html"] is None


def test_native_eval_keeps_each_runs_eval_results_separately(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, plugin = _prepared(tmp_path)
    body = (
        f"results = os.path.join({str(plugin / 'evals')!r}, 'results')\n"
        "os.makedirs(results)\n"
        "open(os.path.join(results, 'aggregate.json'), 'w').write('{}')\n"
    )
    fake = _fake_claude(tmp_path, body)
    kept = []
    for _ in range(2):
        assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
        kept.append(Path(_result(manifest)["plugin_evals_results"]))
    assert kept[0] != kept[1]
    for path in kept:
        assert path.parent == manifest.parent / "native-eval" / "plugin-evals-results"
        assert sorted(p.name for p in path.iterdir()) == ["aggregate.json"]


def test_native_eval_nonzero_exit_fails_the_run(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path, "sys.exit(3)\n")
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 1
    assert _result(manifest)["exit_code"] == 3
    assert _leftovers(short_root) == []


def test_native_eval_timeout_is_recorded(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path, "time.sleep(10)\n")
    assert native_eval.run(_args(manifest, cases, fake, short_root, "--timeout", "1")) == 1
    record = _result(manifest)
    assert record["exit_code"] == 124
    assert record["timed_out"] is True
    assert _leftovers(short_root) == []


def test_native_eval_keeps_read_only_scaffolds_and_cleans_up(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    body = (
        "scaffold = os.path.join(os.environ['TMPDIR'], 'claude-eval-x')\n"
        "os.makedirs(os.path.join(scaffold, 'sealed', 'inner'))\n"
        "os.chmod(os.path.join(scaffold, 'sealed'), 0)\n"
        "os.chmod(scaffold, 0o500)\n"
    )
    fake = _fake_claude(tmp_path, body)
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
    record = _result(manifest)
    assert record["cleanup_error"] is None
    kept = Path(record["kept_temp"])
    assert kept.parent == manifest.parent / "native-eval" / "kept-temp"
    assert (kept / "claude-eval-x" / "sealed" / "inner").is_dir()
    assert _leftovers(short_root) == []


def test_native_eval_missing_claude_bin_is_clear(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, plugin = _prepared(tmp_path)
    with pytest.raises(SystemExit, match="claude binary not found"):
        native_eval.run(_args(manifest, cases, tmp_path / "nope", short_root))
    assert "claude binary not found" in _result(manifest)["error"]
    assert _leftovers(short_root) == []
    assert not (plugin / "evals").exists()


def test_native_eval_long_temp_root_fails_before_creating_dirs(tmp_path: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    long_root = tmp_path / ("x" * 60)
    with pytest.raises(SystemExit, match="choose a shorter --temp-root$"):
        native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), long_root))
    assert not long_root.exists()
    assert "too long" in _result(manifest)["error"]


def test_native_eval_rejects_empty_cases(tmp_path: Path) -> None:
    manifest, _cases, _plugin = _prepared(tmp_path)
    neutral = tmp_path / "cases-dir"
    neutral.mkdir()
    with pytest.raises(SystemExit, match="is empty"):
        native_eval.run(_args(manifest, neutral, tmp_path / "nope", tmp_path))
    assert _leftovers(tmp_path) == []


def test_native_eval_stores_the_real_temp_root(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    link = Path(tempfile.mkdtemp(prefix="pnl-", dir="/tmp")) / "l"
    try:
        link.symlink_to(short_root)
        assert native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), link)) == 0
        record = _result(manifest)
        assert record["temp_root"].startswith(str(short_root.resolve()) + "/")
        assert record["cwd"].startswith(str(short_root.resolve()) + "/")
    finally:
        shutil.rmtree(link.parent, ignore_errors=True)


def test_native_eval_removes_the_temp_root_when_keeping_fails(
    tmp_path: Path, short_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path, "os.makedirs(os.path.join(os.environ['TMPDIR'], 'claude-eval-x'))\n")

    def failing_move(*_args: object) -> None:
        raise OSError("cross-device copy failed")

    monkeypatch.setattr(native_eval.shutil, "move", failing_move)
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 1
    record = _result(manifest)
    assert "keeping temp root" in record["cleanup_error"]
    assert str(manifest.parent / "native-eval" / "kept-temp" / Path(record["temp_root"]).name) in record["cleanup_error"]
    assert record["kept_temp"] is None
    assert _leftovers(short_root) == []


def test_native_eval_refuses_a_manifest_whose_plugin_is_not_its_own_copy(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, plugin = _prepared(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    elsewhere = tmp_path / "real-plugin"
    (elsewhere / "evals").mkdir(parents=True)
    data["paths"]["plugin"] = str(elsewhere)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="not the run's own"):
        native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), short_root))
    assert (elsewhere / "evals").is_dir()



def test_native_eval_names_a_partial_keep(tmp_path: Path, short_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path, "os.makedirs(os.path.join(os.environ['TMPDIR'], 'claude-eval-x'))\n")

    def partial_move(_source: str, target: str) -> None:
        os.makedirs(target)  # the copy got this far, then failed
        raise OSError("cross-device copy failed")

    monkeypatch.setattr(native_eval.shutil, "move", partial_move)
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 1
    assert "partial copy left at" in _result(manifest)["cleanup_error"]



def test_native_eval_build_command_is_the_documented_command_line(tmp_path: Path) -> None:
    args = native_eval.build_parser().parse_args(["--manifest", "m.json"])
    plugin, out, runs = tmp_path / "plugin", tmp_path / "out", tmp_path / "out" / "runs" / "r1"
    assert native_eval.build_command(args, "/bin/claude", plugin, out, "model-x", runs, ["Bash", "Write", "Edit"]) == [
        "/bin/claude", "plugin", "eval", str(plugin),
        "--eval-dir", "evals",
        "--ablation", "with-without",
        "--runs", "3",
        "--concurrency", "1",
        "--trust-plugin",
        "--no-publish",
        "--threshold", "0",
        "--scaffold",
        "--json", str(out / "report.json"),
        "--report", str(out / "report.html"),
        "--output-dir", str(runs),
        "--keep-temp",
        "--allow-tools", "Bash", "Write", "Edit",
        "--model", "model-x",
        "--judge-model", "model-x",
    ]


def test_native_eval_uses_a_fresh_runs_dir_per_run(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    fake = _fake_claude(tmp_path)
    dirs = []
    for _ in range(2):
        assert native_eval.run(_args(manifest, cases, fake, short_root)) == 0
        record = _result(manifest)
        command = record["command"]
        assert command[command.index("--output-dir") + 1] == record["runs_dir"]
        dirs.append(Path(record["runs_dir"]))
    assert dirs[0] != dirs[1] and {d.parent for d in dirs} == {manifest.parent / "native-eval" / "runs"}


def test_native_eval_fails_when_a_memory_file_appears_during_the_run(tmp_path: Path, short_root: Path) -> None:
    manifest, cases, _plugin = _prepared(tmp_path)
    # CLAUDE.local.md stands in for every memory file name.
    fake = _fake_claude(tmp_path, f"open({str(short_root / 'CLAUDE.local.md')!r}, 'w').write('late')\n")
    assert native_eval.run(_args(manifest, cases, fake, short_root)) == 1
    record = _result(manifest)
    assert record["exit_code"] == 0
    assert record["clean_root_changed"] == [str(short_root.resolve() / "CLAUDE.local.md")]



def _case(root: Path, name: str, frontmatter: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "prompt.md").write_text(f"---\nname: {name}\n{frontmatter}---\nDo it.\n", encoding="utf-8")


def test_shipped_cases_grant_only_what_they_list() -> None:
    assert native_eval.case_tool_grants(native_eval._default_cases()) == ["Bash", "Write", "Edit"]


def test_a_case_set_listing_no_gated_tool_gets_no_grant(tmp_path: Path) -> None:
    _case(tmp_path, "triage", "allowed_tools: [Read, Glob, Grep, Skill]\n")
    assert native_eval.case_tool_grants(tmp_path) == []
    args = native_eval.build_parser().parse_args(["--manifest", "m.json"])
    command = native_eval.build_command(args, "claude", tmp_path, tmp_path, "m", tmp_path / "runs", [])
    assert "--allow-tools" not in command


def test_grants_keep_patterns_and_mcp_tools_and_skip_ungated(tmp_path: Path) -> None:
    _case(tmp_path, "a", "allowed_tools: [Read, 'Bash(npm test)', mcp__srv__tool]\n")
    _case(tmp_path, "b", 'allowed_tools: ["WebFetch", Read, Bash(npm test)]\n')
    assert native_eval.case_tool_grants(tmp_path) == ["Bash(npm test)", "mcp__srv__tool", "WebFetch"]


def test_a_block_list_allowed_tools_is_refused(tmp_path: Path, short_root: Path) -> None:
    manifest, _cases, _plugin = _prepared(tmp_path)
    cases = tmp_path / "block-cases"
    _case(cases, "c", "allowed_tools:\n  - Bash\n")
    with pytest.raises(SystemExit, match="inline"):
        native_eval.run(_args(manifest, cases, _fake_claude(tmp_path), short_root))
    assert "inline" in _result(manifest)["error"]
