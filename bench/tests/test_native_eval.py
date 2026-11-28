from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from external.runner import native_eval


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
    tools = record["command"][record["command"].index("--allow-tools") + 1:][:4]
    assert tools == ["Bash", "Write", "Edit", "--model"]
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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


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
    assert native_eval.run(_args(manifest, cases, fake, short_root, "--timeout", "2")) == 1
    assert _result(manifest)["timed_out"] is True
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


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
