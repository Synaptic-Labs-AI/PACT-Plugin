from __future__ import annotations

import json
import os
from pathlib import Path

from external.runner import prepare


def test_scratch_settings_preserve_env_and_override_config(tmp_path: Path) -> None:
    source = tmp_path / "settings.json"
    source.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_MODEL": "deepseek-flash[1m]",
                    "CLAUDE_CONFIG_DIR": "/live/config",
                    "CLAUDE_CODE_EFFORT_LEVEL": "max",
                },
                "teammateMode": "in-process",
                "enabledPlugins": {"PACT@pact-plugin": True},
                "agent": "PACT:pact-orchestrator",
            }
        ),
        encoding="utf-8",
    )
    scratch = tmp_path / "run"
    config = scratch / "config"
    settings, env = prepare.build_scratch_settings(source, config, scratch)
    assert env["CLAUDE_CONFIG_DIR"] == str(config)
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    assert settings["teammateMode"] == "in-process"
    assert "enabledPlugins" not in settings
    assert "agent" not in settings
    assert str(scratch / "teams") in settings["permissions"]["additionalDirectories"]
    written = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    assert written["env"]["CLAUDE_CONFIG_DIR"] == str(config)


def test_copy_plugin_tree_excludes_tests_and_caches(tmp_path: Path) -> None:
    src = tmp_path / "plugin"
    (src / "tests").mkdir(parents=True)
    (src / "tests" / "test_thing.py").write_text("x", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    dest = tmp_path / "copy"
    prepare.copy_plugin_tree(src, dest)
    assert (dest / "keep.txt").is_file()
    assert not (dest / "tests").exists()
    assert not (dest / "__pycache__").exists()


def test_verify_provider_accepts_deepseek_json(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'result':'OK','is_error':False,'num_turns':1,"
        "'modelUsage':{'deepseek-flash[1m]':{}}}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    scratch = tmp_path / "run"
    out = scratch / "out"
    out.mkdir(parents=True)
    record = prepare.verify_provider(scratch, out, str(fake), dict(os.environ))
    assert record["model_keys"] == ["deepseek-flash[1m]"]
    assert (out / "provider-check.json").is_file()


def test_verify_provider_rejects_non_deepseek_model(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'result':'OK','is_error':False,'num_turns':1,"
        "'modelUsage':{'claude-sonnet-5':{}}}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    scratch = tmp_path / "run"
    out = scratch / "out"
    out.mkdir(parents=True)
    try:
        prepare.verify_provider(scratch, out, str(fake), dict(os.environ))
    except SystemExit as exc:
        assert "DeepSeek model" in str(exc)
    else:
        raise AssertionError("expected provider verification to fail")
