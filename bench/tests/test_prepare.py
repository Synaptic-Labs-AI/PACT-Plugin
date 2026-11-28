from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from external.runner import prepare


def _fake_claude(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\nimport json, sys, time\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _json_claude(path: Path, payload: dict, exit_code: int = 0) -> Path:
    return _fake_claude(path, f"print(json.dumps({payload!r}))\nsys.exit({exit_code})\n")


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "run" / "out"
    out.mkdir(parents=True)
    return out


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
    dirs = settings["permissions"]["additionalDirectories"]
    assert dirs == [str(scratch / "teams"), str(scratch / "tasks"), str(scratch / "pact-sessions")]
    assert "/tmp" not in dirs
    written = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    assert written["env"]["CLAUDE_CONFIG_DIR"] == str(config)


def test_scratch_settings_strip_credentials_and_override_models(tmp_path: Path) -> None:
    secrets = {
        "ANTHROPIC_AUTH_TOKEN": "s1",
        "ANTHROPIC_API_KEY": "s2",
        "CLAUDE_CODE_OAUTH_TOKEN": "s3",
        "ANTHROPIC_CUSTOM_HEADERS": "s4",
        "MY_SERVICE_SECRET": "s5",
        "db_password": "s6",
        "AWS_CREDENTIALS_FILE": "s7",
        "CLAUDE_CODE_SESSION_ID": "s8",
    }
    source = tmp_path / "settings.json"
    source.write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "live", "ANTHROPIC_DEFAULT_SONNET_MODEL": "live", **secrets}}),
        encoding="utf-8",
    )
    settings, env = prepare.build_scratch_settings(source, tmp_path / "config", tmp_path, "deepseek-flash[1m]")
    serialized = (tmp_path / "config" / "settings.json").read_text(encoding="utf-8")
    for key, value in secrets.items():
        assert key not in env
        assert value not in serialized
    assert all(env[key] == "deepseek-flash[1m]" for key in prepare.MODEL_ENV_KEYS)


def test_child_env_is_an_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = {
        "CLAUDE_CODE_MESSAGING_TOKEN": "x",
        "CLAUDE_PROJECT_DIR": "/live",
        "CLAUDECODE": "1",
        "CLAUDE_CONFIG_DIR": "/live/config",
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "x",
        "SSH_AUTH_SOCK": "/s",
        "XDG_RUNTIME_DIR": "/run",
        "DBUS_SESSION_BUS_ADDRESS": "x",
        "AWS_PROFILE": "x",
        "ANTHROPIC_API_KEY": "x",
    }
    allowed = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb"}
    monkeypatch.setattr(prepare.os, "environ", {**live, **allowed})
    token = tmp_path / "token"
    token.write_text("provider-token\n", encoding="utf-8")
    settings_env = {"CLAUDE_CODE_EFFORT_LEVEL": "max", "ANTHROPIC_MODEL": "m", "LEAKED_TOKEN": "x"}
    env = prepare.build_child_env(settings_env, tmp_path, token, "https://example.invalid")
    expected = {
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "ANTHROPIC_MODEL": "m",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "ANTHROPIC_BASE_URL": "https://example.invalid",
        "ANTHROPIC_AUTH_TOKEN": "provider-token",
    }
    assert env == expected


def test_copy_plugin_tree_excludes_tests_and_caches(tmp_path: Path) -> None:
    src = tmp_path / "plugin"
    (src / "tests").mkdir(parents=True)
    (src / "tests" / "test_thing.py").write_text("x", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    (src / "skills" / "s" / "tests").mkdir(parents=True)
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    dest = tmp_path / "copy"
    prepare.copy_plugin_tree(src, dest)
    assert (dest / "keep.txt").is_file()
    assert not (dest / "tests").exists()
    assert not (dest / "__pycache__").exists()
    assert not (dest / "skills" / "s" / "tests").exists()


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        env=env,
    )


def _plugin_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    for rel in ("keep.txt", "hooks/a.py", "tests/test_a.py", "skills/s/tests/test_b.py", "skills/s/SKILL.md"):
        (plugin / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugin / rel).write_text(rel, encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "seed")
    (plugin / "debug.log").write_text("ignored", encoding="utf-8")
    return repo


def _tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_every_plugin_source_prunes_the_same_way(tmp_path: Path) -> None:
    repo = _plugin_repo(tmp_path)
    shapes = {}
    for label, ref in (("worktree", None), ("ref", "HEAD")):
        out = tmp_path / label
        out.mkdir()
        prov = prepare.check_source(repo, ref, None, allow_dirty=False)
        dest, _ = prepare.materialize_plugin(repo, out, ref, None, prov)
        shapes[label] = _tree(dest)
        assert not (out / ".plugin-src").exists()
    assert shapes["worktree"] == shapes["ref"] == ["hooks/a.py", "keep.txt", "skills/s/SKILL.md"]


def test_dirty_tree_is_refused_before_any_write(tmp_path: Path) -> None:
    repo = _plugin_repo(tmp_path)
    (repo / "pact-plugin" / "keep.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(SystemExit, match="dirty pact-plugin tree"):
        prepare.check_source(repo, None, None, allow_dirty=False)
    assert prepare.check_source(repo, None, None, allow_dirty=True)["dirty_paths"]


def _prepare_args(tmp_path: Path, out: Path, *extra: str) -> list[str]:
    settings = tmp_path / "live-settings.json"
    settings.write_text(json.dumps({"env": {}}), encoding="utf-8")
    token = tmp_path / "live-token"
    if not token.exists():
        token.write_text("t", encoding="utf-8")
    plugin = tmp_path / "plugin-src"
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    return [
        "--out", str(out),
        "--plugin-dir", str(plugin),
        "--settings-source", str(settings),
        "--token-file", str(token),
        *extra,
    ]


def test_prepare_refuses_non_empty_out_without_force(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "user-file").write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit, match="not empty"):
        prepare.main(_prepare_args(tmp_path, out))
    assert (out / "user-file").is_file()


def test_force_refuses_dirs_without_prior_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "user-file").write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit, match="no previous pact-bench"):
        prepare.main(_prepare_args(tmp_path, out, "--force"))
    assert (out / "user-file").is_file()


@pytest.mark.parametrize("target", ["root", "home", "repo", "repo_parent"])
def test_force_refuses_dangerous_targets(target: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(prepare.Path, "home", classmethod(lambda cls: tmp_path))
    out = {
        "root": Path("/"),
        "home": tmp_path,
        "repo": prepare.REPO_ROOT,
        "repo_parent": prepare.REPO_ROOT.parent,
    }[target]
    with pytest.raises(SystemExit, match="refusing --force"):
        prepare.check_force_target(out)


def test_force_replaces_previous_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert prepare.main(_prepare_args(tmp_path, out)) == 0
    (out / "stale").write_text("x", encoding="utf-8")
    assert prepare.main(_prepare_args(tmp_path, out, "--force")) == 0
    assert not (out / "stale").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == prepare.MANIFEST_SCHEMA


@pytest.mark.parametrize("missing", ["--settings-source", "--token-file", "--plugin-dir", "--claude-bin"])
def test_prepare_checks_inputs_before_writing(missing: str, tmp_path: Path) -> None:
    out = tmp_path / "out"
    args = _prepare_args(tmp_path, out, "--verify", "--claude-bin", str(tmp_path / "no-claude"))
    if missing != "--claude-bin":
        args[args.index(missing) + 1] = str(tmp_path / "does-not-exist")
    with pytest.raises(SystemExit):
        prepare.main(args)
    assert not out.exists()


def test_prepare_rejects_empty_token_before_writing(tmp_path: Path) -> None:
    (tmp_path / "live-token").write_text("  \n", encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="empty provider token"):
        prepare.main(_prepare_args(tmp_path, out))
    assert not out.exists()


def test_verify_provider_accepts_deepseek_json(tmp_path: Path) -> None:
    fake = _json_claude(tmp_path / "claude", {"result": "OK", "is_error": False, "num_turns": 1,
                                              "modelUsage": {"deepseek-flash[1m]": {}}})
    out = _out(tmp_path)
    record = prepare.verify_provider(out, out, str(fake), dict(os.environ))
    assert record["model_keys"] == ["deepseek-flash[1m]"]
    assert (out / "provider-check.json").is_file()


def test_verify_provider_checks_the_requested_model(tmp_path: Path) -> None:
    fake = _json_claude(tmp_path / "claude", {"is_error": False, "modelUsage": {"deepseek-pro": {}}})
    out = _out(tmp_path)
    record = prepare.verify_provider(out, out, str(fake), dict(os.environ), model="deepseek-pro[1m]")
    assert record["model_keys"] == ["deepseek-pro"]
    with pytest.raises(SystemExit, match="did not resolve deepseek-flash"):
        prepare.verify_provider(out, out, str(fake), dict(os.environ))


def test_verify_provider_rejects_non_deepseek_model(tmp_path: Path) -> None:
    fake = _json_claude(tmp_path / "claude", {"is_error": False, "modelUsage": {"claude-sonnet-5": {}}})
    out = _out(tmp_path)
    with pytest.raises(SystemExit, match="did not resolve"):
        prepare.verify_provider(out, out, str(fake), dict(os.environ))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("print(json.dumps({'is_error': True, 'modelUsage': {'deepseek-flash': {}}}))\n", "is_error=True"),
        ("print(json.dumps({'is_error': False, 'modelUsage': {'deepseek-flash': {}}}))\nsys.exit(3)\n", "exited 3"),
        ("pass\n", "empty claude output"),
        ("print('not json')\nprint(42)\n", "no JSON object"),
        ("time.sleep(5)\n", "did not answer"),
    ],
    ids=["is_error", "non_zero", "empty", "non_json", "timeout"],
)
def test_verify_provider_failures_write_the_check(body: str, message: str, tmp_path: Path) -> None:
    fake = _fake_claude(tmp_path / "claude", body)
    out = _out(tmp_path)
    with pytest.raises(SystemExit, match=message):
        prepare.verify_provider(out, out, str(fake), dict(os.environ), timeout=1)
    assert json.loads((out / "provider-check.json").read_text(encoding="utf-8"))["checked_at"]


def test_verify_provider_missing_binary_is_a_clear_failure(tmp_path: Path) -> None:
    out = _out(tmp_path)
    with pytest.raises(SystemExit, match="claude binary not found"):
        prepare.verify_provider(out, out, str(tmp_path / "nope"), dict(os.environ))
    assert "error" in json.loads((out / "provider-check.json").read_text(encoding="utf-8"))
