from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from external.runner import native_eval, prepare


def test_native_eval_injects_cases_and_records_command(tmp_path: Path) -> None:
    scratch = tmp_path / "run"
    plugin = scratch / "pact-plugin"
    plugin.mkdir(parents=True)
    config = scratch / "config"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "deepseek-flash[1m]"}}),
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

    fake = tmp_path / "claude"
    fake_env = tmp_path / "env.json"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(fake_env)!r}, 'w').write(json.dumps({{'TMPDIR': os.environ.get('TMPDIR'), 'HOME': os.environ.get('HOME')}}))\n"
        "args = sys.argv[1:]\n"
        "if '--json' in args:\n"
        "    path = args[args.index('--json') + 1]\n"
        "    open(path, 'w').write(json.dumps({'ok': True}))\n"
        "if '--report' in args:\n"
        "    path = args[args.index('--report') + 1]\n"
        "    open(path, 'w').write('<html></html>')\n"
        "print('fake native eval ok')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    short_root = Path(tempfile.mkdtemp(prefix="pnt-", dir="/tmp"))
    args = native_eval.build_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--cases-dir",
            str(cases),
            "--claude-bin",
            str(fake),
            "--runs",
            "1",
            "--temp-root",
            str(short_root),
        ]
    )
    assert native_eval.run(args) == 0
    short_env = json.loads(fake_env.read_text(encoding="utf-8"))
    assert short_env["TMPDIR"].startswith(str(short_root / "pact-native-"))
    assert short_env["HOME"].startswith(str(short_root / "pact-native-"))
    assert (plugin / "evals" / "probe" / "prompt.md").is_file()
    record = json.loads((scratch / "native-eval" / "result.json").read_text(encoding="utf-8"))
    assert record["exit_code"] == 0
    assert record["temp_root"].startswith(str(short_root / "pact-native-"))
    assert record["report_json"].endswith("report.json")
    shutil.rmtree(short_root, ignore_errors=True)
    assert any(str(plugin) in str(part) for part in record["command"])


def test_native_eval_rejects_empty_cases(tmp_path: Path) -> None:
    scratch = tmp_path / "run"
    (scratch / "pact-plugin").mkdir(parents=True)
    (scratch / "config").mkdir()
    (scratch / "config" / "settings.json").write_text(json.dumps({"env": {}}), encoding="utf-8")
    (tmp_path / "token").write_text("dummy", encoding="utf-8")
    manifest = scratch / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "paths": {"out": str(scratch), "plugin": str(scratch / "pact-plugin")},
                "provider": {
                    "token_file": str(tmp_path / "token"),
                    "base_url": "https://api.deepseek.com/anthropic",
                    "model": "deepseek-flash[1m]",
                },
            }
        ),
        encoding="utf-8",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    args = native_eval.build_parser().parse_args(
        ["--manifest", str(manifest), "--cases-dir", str(empty), "--claude-bin", str(tmp_path / "nope")]
    )
    try:
        native_eval.run(args)
    except SystemExit as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("expected empty cases to be rejected")
