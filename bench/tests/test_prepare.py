from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
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
    assert env["ANTHROPIC_BASE_URL"] == prepare.DEFAULT_BASE_URL
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


def test_scratch_settings_carry_the_chosen_base_url(tmp_path: Path) -> None:
    source = tmp_path / "settings.json"
    source.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://live.invalid"}}), encoding="utf-8")
    settings, env = prepare.build_scratch_settings(
        source, tmp_path / "config", tmp_path, "m", "https://chosen.invalid"
    )
    written = json.loads((tmp_path / "config" / "settings.json").read_text(encoding="utf-8"))
    assert written["env"]["ANTHROPIC_BASE_URL"] == "https://chosen.invalid"
    token = tmp_path / "token"
    token.write_text("t", encoding="utf-8")
    child = prepare.build_child_env(env, tmp_path, token, "https://chosen.invalid")
    assert child["ANTHROPIC_BASE_URL"] == written["env"]["ANTHROPIC_BASE_URL"]


@pytest.mark.parametrize("content", ["", "  \n"])
def test_child_env_rejects_an_empty_token(content: str, tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match="empty provider token"):
        prepare.build_child_env({}, tmp_path, token, "https://example.invalid")


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


def test_failed_git_archive_is_a_clear_failure(tmp_path: Path) -> None:
    repo = _plugin_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(SystemExit, match="git archive"):
        prepare.materialize_plugin(repo, out, "HEAD", None, {"sha": "0" * 40})
    assert not (out / ".plugin-src").exists()


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
    # Make the later "previous pact-bench output" check pass for this target, so only the
    # /, $HOME and repo-ancestor guard can refuse it.
    marker = (prepare.REPO_ROOT / "bench" / "README.md").relative_to(out) if target != "home" else Path("m")
    if target == "home":
        (tmp_path / "m").write_text("x", encoding="utf-8")
    monkeypatch.setattr(prepare, "OUT_MARKER", str(marker))
    with pytest.raises(SystemExit, match=r"it is /, \$HOME, or the repo root"):
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


@pytest.mark.parametrize("content", ["{not json", "[]", '{"env": []}', '{"env": "x"}'])
def test_prepare_rejects_bad_settings_before_writing(content: str, tmp_path: Path) -> None:
    out = tmp_path / "out"
    args = _prepare_args(tmp_path, out)
    (tmp_path / "live-settings.json").write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit, match="--settings-source"):
        prepare.main(args)
    assert not out.exists()


def test_plugin_dir_and_plugin_ref_are_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        prepare.main(_prepare_args(tmp_path, tmp_path / "out", "--plugin-ref", "HEAD"))
    assert exc.value.code == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("location", "warns"),
    [("repo_root_child", True), ("bench_runs", False), ("outside_repo", False)],
)
def test_out_location_warning(location: str, warns: bool, tmp_path: Path) -> None:
    out = {
        "repo_root_child": prepare.REPO_ROOT / "eval-out",
        "bench_runs": prepare.RUNS_DIR / "eval-out",
        "outside_repo": tmp_path / "eval-out",
    }[location]
    warning = prepare.out_location_warning(out)
    assert (warning is not None) == warns
    if warns:
        assert "bench/.runs" in warning and "token" in warning


def test_prepare_prints_the_out_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(prepare, "out_location_warning", lambda out: "warning: test-marker")
    assert prepare.main(_prepare_args(tmp_path, tmp_path / "out")) == 0
    assert "warning: test-marker" in capsys.readouterr().err


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



def _dead(pid: int, wait: float = 5.0) -> bool:
    """True once pid is gone or a zombie (reaped by nobody yet but no longer running)."""
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


def test_token_count_tuning_keys_are_kept_and_dropped_keys_are_recorded(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "live-token").write_text("provider-token-7f3c9a\n", encoding="utf-8")
    args = _prepare_args(tmp_path, out)
    live_env = {
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000",
        "MAX_THINKING_TOKENS": "32000",
        "MAX_MCP_OUTPUT_TOKENS": "25000",
        "ANTHROPIC_AUTH_TOKEN": "secret",
        "MY_API_KEY": "secret",
    }
    (tmp_path / "live-settings.json").write_text(json.dumps({"env": live_env}), encoding="utf-8")
    assert prepare.main(args) == 0
    settings_env = json.loads((out / "config" / "settings.json").read_text(encoding="utf-8"))["env"]
    for key in ("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "MAX_THINKING_TOKENS", "MAX_MCP_OUTPUT_TOKENS"):
        assert settings_env[key] == live_env[key]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["settings"]["dropped_env_keys"] == ["ANTHROPIC_AUTH_TOKEN", "MY_API_KEY"]
    # The provider token (from --token-file) is never written anywhere under --out.
    token = (tmp_path / "live-token").read_bytes().strip()
    assert all(token not in path.read_bytes() for path in out.rglob("*") if path.is_file())
    assert "secret" not in (out / "manifest.json").read_text(encoding="utf-8")


def test_every_model_key_is_pinned_including_small_fast(tmp_path: Path) -> None:
    assert "ANTHROPIC_SMALL_FAST_MODEL" in prepare.MODEL_ENV_KEYS
    source = tmp_path / "settings.json"
    source.write_text(json.dumps({"env": {key: "live-model" for key in prepare.MODEL_ENV_KEYS}}), encoding="utf-8")
    _settings, env = prepare.build_scratch_settings(source, tmp_path / "config", tmp_path, "deepseek-pro")
    assert {key: env[key] for key in prepare.MODEL_ENV_KEYS} == dict.fromkeys(prepare.MODEL_ENV_KEYS, "deepseek-pro")


# Literal names, not prepare.MEMORY_FILE_NAMES: dropping a name from the constant must fail a case.
@pytest.mark.parametrize("name", ["CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"])
def test_clean_root_refuses_each_memory_file_in_parents(tmp_path: Path, name: str) -> None:
    # Written only under pytest's tmp_path (neither a project nor a home dir); removed with it.
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("memory", encoding="utf-8")
    root = tmp_path / "a" / "b"
    with pytest.raises(SystemExit, match="memory files"):
        prepare.check_clean_root(root)
    assert prepare.memory_files_above(root) == [tmp_path / name]


def test_clean_root_refuses_a_rules_dir_holding_files(tmp_path: Path) -> None:
    rules = tmp_path / ".claude" / "rules"
    (rules / "nested").mkdir(parents=True)
    root = tmp_path / "a"
    assert prepare.check_clean_root(root) == root.resolve()  # an empty rules dir loads nothing
    (rules / "nested" / "r.md").write_text("rule", encoding="utf-8")
    with pytest.raises(SystemExit, match="memory files"):
        prepare.check_clean_root(root)
    assert prepare.memory_files_above(root) == [rules]


def test_clean_root_counts_an_unreadable_rules_dir_as_memory(tmp_path: Path) -> None:
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    rules.chmod(0)
    try:
        if os.access(rules, os.R_OK):
            pytest.skip("running as a user who can read a mode-000 dir")
        with pytest.raises(SystemExit, match="memory files"):
            prepare.check_clean_root(tmp_path / "a")
    finally:
        rules.chmod(0o700)


def test_clean_root_refuses_the_repo() -> None:
    with pytest.raises(SystemExit, match="inside the repository"):
        prepare.check_clean_root(prepare.RUNS_DIR / "x")


def test_verify_runs_from_a_clean_cwd_outside_the_repo(tmp_path: Path) -> None:
    seen = tmp_path / "cwd.txt"
    fake = _fake_claude(
        tmp_path / "claude",
        f"import os\nopen({str(seen)!r}, 'w').write(os.getcwd())\n"
        "print(json.dumps({'is_error': False, 'modelUsage': {'deepseek-flash': {}}}))\n",
    )
    out = _out(tmp_path)
    record = prepare.verify_provider(out, out, str(fake), dict(os.environ))
    cwd = Path(seen.read_text(encoding="utf-8"))
    assert str(cwd) == record["cwd"]
    assert not cwd.is_relative_to(prepare.REPO_ROOT)
    assert prepare.memory_files_above(cwd) == []
    assert not cwd.exists()
    assert json.loads((out / "provider-check.json").read_text(encoding="utf-8"))["cwd"] == str(cwd)


def test_verify_refuses_a_dirty_temp_root_before_writing(tmp_path: Path) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "CLAUDE.local.md").write_text("memory", encoding="utf-8")
    out = tmp_path / "out"
    args = _prepare_args(tmp_path, out, "--verify", "--claude-bin", sys.executable, "--temp-root", str(dirty))
    with pytest.raises(SystemExit, match="memory files"):
        prepare.main(args)
    assert not out.exists()


def test_timeout_carries_the_drained_output(tmp_path: Path) -> None:
    child = [sys.executable, "-c", "import sys, time; print('before'); sys.stdout.flush(); time.sleep(30)"]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        prepare.run_process_group(child, 1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert time.monotonic() - started < 15
    assert exc.value.stdout == "before\n"
    assert exc.value.output_complete is True


def test_post_kill_drain_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prepare, "KILL_DRAIN_SECONDS", 1)
    pid_file = tmp_path / "escaped.pid"
    script = (
        "import os, subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
        f"open({str(pid_file)!r}, 'w').write(str(kid.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired) as exc:
            prepare.run_process_group([sys.executable, "-c", script], 1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert time.monotonic() - started < 15
        assert exc.value.output_complete is False
    finally:
        _kill(int(_wait_for(pid_file)))


@pytest.mark.parametrize(
    ("signum", "expected_rc"),
    [(signal.SIGTERM, 128 + signal.SIGTERM), (signal.SIGHUP, 128 + signal.SIGHUP), (signal.SIGINT, -signal.SIGINT)],
    ids=["SIGTERM", "SIGHUP", "SIGINT"],
)
def test_a_signal_to_the_harness_kills_the_child_group(tmp_path: Path, signum: int, expected_rc: int) -> None:
    pid_file = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(kid.pid))\n"
        "time.sleep(60)\n"
    )
    harness = (
        "import subprocess, sys\n"
        f"sys.path.insert(0, {str(Path(prepare.__file__).resolve().parents[2])!r})\n"
        "from external.runner import prepare\n"
        f"prepare.run_process_group([sys.executable, '-c', {child!r}], 60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", harness], stderr=subprocess.PIPE, text=True)
    grandchild = int(_wait_for(pid_file))
    try:
        started = time.monotonic()
        proc.send_signal(signum)
        assert proc.wait(timeout=15) == expected_rc
        assert time.monotonic() - started < 15
        assert _dead(grandchild)
    finally:
        proc.kill()
        proc.wait()
        _kill(grandchild)


def test_signal_handlers_are_restored() -> None:
    # Sentinel handlers, so a baseline left behind by an earlier test cannot make this pass.
    def sentinel(_signum: int, _frame: object) -> None:
        pass

    original = {signum: signal.signal(signum, sentinel) for signum in prepare.FORWARDED_SIGNALS}
    try:
        prepare.run_process_group([sys.executable, "-c", "pass"], 10)
        assert all(signal.getsignal(signum) is sentinel for signum in prepare.FORWARDED_SIGNALS)
    finally:
        for signum, handler in original.items():
            signal.signal(signum, handler)


def test_verify_provider_itself_refuses_a_dirty_temp_root(tmp_path: Path) -> None:
    dirty = tmp_path / "dirty"
    (dirty / "r").mkdir(parents=True)
    (dirty / "CLAUDE.local.md").write_text("memory", encoding="utf-8")
    ran = tmp_path / "ran"
    fake = _fake_claude(tmp_path / "claude", f"open({str(ran)!r}, 'w').write('x')\n")
    out = _out(tmp_path)
    with pytest.raises(SystemExit, match="memory files"):
        prepare.verify_provider(out, out, str(fake), dict(os.environ), temp_root=dirty / "r")
    assert not ran.exists()
    assert "memory files" in json.loads((out / "provider-check.json").read_text(encoding="utf-8"))["error"]


def test_clean_root_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    (real / "CLAUDE.local.md").write_text("memory", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real / "sub")
    with pytest.raises(SystemExit, match="memory files"):
        prepare.check_clean_root(link)
    clean = tmp_path / "clean"
    clean.mkdir()
    (tmp_path / "clean-link").symlink_to(clean)
    assert prepare.check_clean_root(tmp_path / "clean-link") == clean.resolve()


def test_clean_root_refuses_a_symlink_into_the_repo(tmp_path: Path) -> None:
    link = tmp_path / "into-repo"
    link.symlink_to(prepare.REPO_ROOT / "bench")
    with pytest.raises(SystemExit, match="inside the repository"):
        prepare.check_clean_root(link)


def test_clean_root_refuses_a_file(tmp_path: Path) -> None:
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="not a directory"):
        prepare.check_clean_root(afile)


def test_text_mode_output_with_undecodable_bytes_is_kept() -> None:
    child = [sys.executable, "-c", "import os; os.write(1, b'\\xff\\xfe ok\\n')"]
    proc = prepare.run_process_group(child, 10, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.returncode == 0
    assert proc.stdout == "�� ok\n"


def test_provider_output_stays_out_of_the_manifest_and_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    token = "provider-token-9b1e4d"
    (tmp_path / "live-token").write_text(token + "\n", encoding="utf-8")
    fake = _fake_claude(
        tmp_path / "claude",
        "import os\n"
        "sys.stderr.write('auth debug: ' + os.environ['ANTHROPIC_AUTH_TOKEN'] + '\\n')\n"
        "print(json.dumps({'result': os.environ['ANTHROPIC_AUTH_TOKEN'], 'is_error': False,\n"
        "                  'modelUsage': {'deepseek-flash': {}}}))\n",
    )
    assert prepare.main(_prepare_args(tmp_path, out, "--verify", "--claude-bin", str(fake))) == 0
    captured = capsys.readouterr()
    manifest_text = (out / "manifest.json").read_text(encoding="utf-8")
    assert token not in manifest_text
    assert token not in captured.out and token not in captured.err
    check = json.loads(manifest_text)["provider"]["check"]
    assert set(check) == {"path", *prepare.CHECK_SUMMARY_KEYS}
    assert check["resolved_model"] is True and check["exit_code"] == 0 and check["is_error"] is False
    # The full record, with the child's output tails, is only in provider-check.json.
    assert token in Path(check["path"]).read_text(encoding="utf-8")


def test_out_that_is_a_file_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.write_text("not a dir", encoding="utf-8")
    with pytest.raises(SystemExit, match="exists and is not a directory"):
        prepare.main(_prepare_args(tmp_path, out))
    assert out.read_text(encoding="utf-8") == "not a dir"



def test_verify_failure_messages_hold_no_child_strings(tmp_path: Path) -> None:
    out = _out(tmp_path)
    leaky = _json_claude(tmp_path / "claude", {"is_error": False, "modelUsage": {"LEAK-usage-key": {}}})
    with pytest.raises(SystemExit) as exc:
        prepare.verify_provider(out, out, str(leaky), dict(os.environ))
    assert "did not resolve deepseek-flash" in str(exc.value) and "LEAK" not in str(exc.value)
    erroring = _json_claude(tmp_path / "claude2", {"is_error": "LEAK-error-text", "modelUsage": {}})
    with pytest.raises(SystemExit) as exc:
        prepare.verify_provider(out, out, str(erroring), dict(os.environ))
    assert "is_error=True" in str(exc.value) and "LEAK" not in str(exc.value)


def _dirty_probe_repo(tmp_path: Path) -> Path:
    repo = _plugin_repo(tmp_path)
    (repo / "pact-plugin" / "extra.py").write_text("x", encoding="utf-8")
    return repo


def test_untracked_file_is_dirty_whatever_the_repo_config(tmp_path: Path) -> None:
    repo = _dirty_probe_repo(tmp_path)
    _git(repo, "config", "status.showUntrackedFiles", "no")
    with pytest.raises(SystemExit, match="dirty pact-plugin tree"):
        prepare.check_source(repo, None, None, allow_dirty=False)


def test_git_ignores_inherited_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _plugin_repo(tmp_path / "a")
    other = _plugin_repo(tmp_path / "b")
    # Give each repo a commit the other lacks (the two seed commits can be identical objects).
    (repo / "pact-plugin" / "keep.txt").write_text("a-version", encoding="utf-8")
    _git(repo, "commit", "-qam", "a", "--no-gpg-sign")
    (other / "pact-plugin" / "keep.txt").write_text("changed", encoding="utf-8")
    _git(other, "commit", "-qam", "other", "--no-gpg-sign")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    sha = prepare.check_source(repo, None, None, allow_dirty=False)["sha"]
    out = tmp_path / "out"
    out.mkdir()
    prov = prepare.check_source(repo, "HEAD", None, allow_dirty=False)
    dest, _ = prepare.materialize_plugin(repo, out, "HEAD", None, prov)  # git archive path
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "a-version"
    monkeypatch.delenv("GIT_DIR")
    expected = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert sha == expected.stdout.strip()


def test_clean_root_refuses_a_symlinked_rules_subdir(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "r.md").write_text("rule", encoding="utf-8")
    rules = tmp_path / "base" / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "linked").symlink_to(elsewhere)
    with pytest.raises(SystemExit, match="memory files"):
        prepare.check_clean_root(tmp_path / "base" / "a")


# Literal names, not prepare.PROJECT_SETTINGS_NAMES, so dropping one fails a case.
@pytest.mark.parametrize("name", [".claude/settings.json", ".claude/settings.local.json"])
def test_clean_root_refuses_project_settings_in_parents(tmp_path: Path, name: str) -> None:
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="memory files or project settings"):
        prepare.check_clean_root(tmp_path / "a")



def test_leftovers_in_the_group_die_after_a_normal_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "leftover.pid"
    script = (
        "import subprocess, sys\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(kid.pid))\n"
    )
    pid = None
    try:
        proc = prepare.run_process_group([sys.executable, "-c", script], 30, stdout=subprocess.PIPE, text=True)
        assert proc.returncode == 0
        pid = int(_wait_for(pid_file))
        assert _dead(pid)
    finally:
        if pid is not None:
            _kill(pid)


def test_a_held_pipe_reports_the_childs_own_exit_code(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        prepare.run_process_group(["sh", "-c", "sleep 30 & echo hi; exit 0"], 2, stdout=subprocess.PIPE, text=True)
    assert time.monotonic() - started < 15
    assert exc.value.child_exit_code == 0
    assert exc.value.stdout == "hi\n"


def test_verify_timeout_records_the_child_exit_code(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nsleep 30 &\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    out = _out(tmp_path)
    with pytest.raises(SystemExit, match="did not answer"):
        prepare.verify_provider(out, out, str(fake), dict(os.environ), timeout=2)
    assert json.loads((out / "provider-check.json").read_text(encoding="utf-8"))["child_exit_code"] == 0


@pytest.mark.parametrize("where", ["xdg_default", "home_default", "global_config"])
def test_host_ignore_files_do_not_hide_untracked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    repo = _plugin_repo(tmp_path)
    (repo / "pact-plugin" / "secret.local.py").write_text("x", encoding="utf-8")
    home = tmp_path / "home"
    ignore = home / ".config" / "git" / "ignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("*.local.py\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    if where == "xdg_default":
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    else:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    if where == "global_config":
        ignore.unlink()
        (home / "ignore-all").write_text("*\n", encoding="utf-8")
        (home / ".gitconfig").write_text(f"[core]\n\texcludesFile = {home / 'ignore-all'}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="dirty pact-plugin tree"):
        prepare.check_source(repo, None, None, allow_dirty=False)
    out = tmp_path / "out"
    out.mkdir()
    prov = prepare.check_source(repo, None, None, allow_dirty=True)
    dest, _ = prepare.materialize_plugin(repo, out, None, None, prov)
    assert (dest / "secret.local.py").is_file()


def test_plugin_dir_link_outside_the_source_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "plugin"
    (src / "hooks").mkdir(parents=True)
    (src / "inside").symlink_to(src / "hooks")
    secret = tmp_path / "secret"
    secret.write_text("key", encoding="utf-8")
    (src / "leak").symlink_to(secret)
    with pytest.raises(SystemExit, match="points outside"):
        prepare.copy_plugin_tree(src, tmp_path / "copy")
    (src / "leak").unlink()
    prepare.copy_plugin_tree(src, tmp_path / "copy")
    assert (tmp_path / "copy" / "inside").is_symlink()


def test_links_copy_the_same_way_from_worktree_and_ref(tmp_path: Path) -> None:
    repo = _plugin_repo(tmp_path)
    (repo / "pact-plugin" / "alias").symlink_to("hooks")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alias", "--no-gpg-sign")
    shapes = {}
    for label, ref in (("worktree", None), ("ref", "HEAD")):
        out = tmp_path / label
        out.mkdir()
        prov = prepare.check_source(repo, ref, None, allow_dirty=False)
        dest, _ = prepare.materialize_plugin(repo, out, ref, None, prov)
        assert (dest / "alias").is_symlink() and os.readlink(dest / "alias") == "hooks"
        shapes[label] = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*"))
    assert shapes["worktree"] == shapes["ref"]


@pytest.mark.parametrize("ref", [None, "HEAD"], ids=["worktree", "ref"])
def test_a_committed_link_out_of_the_plugin_is_refused(tmp_path: Path, ref: str | None) -> None:
    repo = _plugin_repo(tmp_path)
    (repo / "pact-plugin" / "leak").symlink_to("/etc/hostname")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "leak", "--no-gpg-sign")
    out = tmp_path / "out"
    out.mkdir()
    prov = prepare.check_source(repo, ref, None, allow_dirty=False)
    with pytest.raises(SystemExit, match="points outside|cannot be extracted safely"):
        prepare.materialize_plugin(repo, out, ref, None, prov)
    assert not (out / ".plugin-src").exists()


def test_copy_drops_a_git_dir(tmp_path: Path) -> None:
    src = tmp_path / "plugin"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config").write_text("x", encoding="utf-8")
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    prepare.copy_plugin_tree(src, tmp_path / "copy")
    assert not (tmp_path / "copy" / ".git").exists() and (tmp_path / "copy" / "keep.txt").is_file()


def test_live_env_outside_the_allowlist_or_with_url_credentials_is_dropped(tmp_path: Path) -> None:
    out = tmp_path / "out"
    live = {
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "DISABLE_AUTO_COMPACT": "1",
        "HTTPS_PROXY": "http://user:PROXYPASS@proxy:8080",
        "DEEPSEEK_AUTH": "SECRET-AUTH",
        "ANTHROPIC_CUSTOM_URL": "https://u:URLPASS@example.invalid",
    }
    args = _prepare_args(tmp_path, out)
    (tmp_path / "live-settings.json").write_text(json.dumps({"env": live}), encoding="utf-8")
    assert prepare.main(args) == 0
    env = json.loads((out / "config" / "settings.json").read_text(encoding="utf-8"))["env"]
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max" and env["DISABLE_AUTO_COMPACT"] == "1"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["settings"]["dropped_env_keys"] == ["ANTHROPIC_CUSTOM_URL", "DEEPSEEK_AUTH", "HTTPS_PROXY"]
    blob = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    for value in (b"PROXYPASS", b"SECRET-AUTH", b"URLPASS"):
        assert value not in blob



@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("https://user:pass@proxy:8080", False),  # an 's' in the userinfo
        ("https://bob:hunter2@proxy", False),
        ("http://token@host/path", False),
        ("https://api.example.invalid/anthropic", True),
        ("mailto user@example.invalid", True),  # no scheme separator before the @
        ("deepseek-flash[1m]", True),
    ],
)
def test_url_userinfo_values_are_not_kept(value: str, kept: bool) -> None:
    assert prepare.keeps_live_env("ANTHROPIC_CUSTOM_URL", value) is kept
