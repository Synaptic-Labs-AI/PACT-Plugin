from __future__ import annotations

from pathlib import Path

from external.runner import plugin_eval


def test_build_commands_includes_plugin_and_each_skill(tmp_path: Path) -> None:
    plugin = tmp_path / "pact-plugin"
    plugin.mkdir()
    sync, commands = plugin_eval.build_commands(tmp_path, plugin, ["alpha", "beta"])
    assert sync[:2] == ["uv", "sync"]
    assert "--frozen" in sync
    labels = [label for label, _ in commands]
    assert labels[0] == str(plugin)
    assert labels[1:] == ["alpha", "beta"]
    for label, cmd in commands[1:]:
        assert label in " ".join(cmd)
        assert "--depth" in cmd
        assert "standard" in cmd


def test_parse_json_accepts_trailing_json_line() -> None:
    parsed = plugin_eval._parse_json('info\n{"composite": {"score": 1}}\n')
    assert parsed["composite"]["score"] == 1
