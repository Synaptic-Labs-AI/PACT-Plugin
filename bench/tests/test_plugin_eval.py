from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from external.runner import plugin_eval

FAKE_UV = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
if args[0] == "sync":
    open({log!r}, "w").write(json.dumps({{"token": "ANTHROPIC_AUTH_TOKEN" in os.environ}}))
    sys.exit(0)
name = os.path.basename(args[3])
if name == "nonjson":
    print("score finished")
elif name == "scalar":
    print("42")
elif name == "bad":
    print(json.dumps({{"composite": 0}}))
    sys.exit(3)
elif name == "slow":
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
    uv.write_text(FAKE_UV.format(python=sys.executable, log=str(log)), encoding="utf-8")
    uv.chmod(0o755)
    skills_file = tmp_path / "skills.txt"
    if skills is not None:
        skills_file.write_text("\n".join(skills) + "\n", encoding="utf-8")
    return manifest, tool, uv, skills_file


def _args(manifest: Path, tool: Path, uv: Path, *extra: str):
    return plugin_eval.build_parser().parse_args(
        ["--manifest", str(manifest), "--plugin-eval-dir", str(tool), "--uv", str(uv), "--timeout", "1", *extra]
    )


def _result(manifest: Path) -> dict:
    return json.loads((manifest.parent / "plugin-eval" / "result.json").read_text(encoding="utf-8"))


def test_build_commands_includes_plugin_and_each_skill(tmp_path: Path) -> None:
    plugin = tmp_path / "pact-plugin"
    sync, commands = plugin_eval.build_commands("/opt/uv", plugin, ["alpha", "beta"])
    assert sync[:2] == ["/opt/uv", "sync"]
    assert "--frozen" in sync
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


@pytest.mark.parametrize(
    ("skill", "check"),
    [
        ("nonjson", lambda r: r["exit_code"] == 0 and "parse_error" in r),
        ("scalar", lambda r: r["exit_code"] == 0 and "parse_error" in r),
        ("bad", lambda r: r["exit_code"] == 3),
        ("slow", lambda r: r["exit_code"] == 124 and r["timed_out"] is True),
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
    assert _result(manifest)["results"] == []


@pytest.mark.parametrize("content", [None, ""])
def test_run_rejects_missing_or_empty_skills_file(tmp_path: Path, content: str | None) -> None:
    manifest, tool, uv, skills = _setup(tmp_path, None)
    if content is not None:
        skills.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match="skills file"):
        plugin_eval.run(_args(manifest, tool, uv, "--skills-file", str(skills)))


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
