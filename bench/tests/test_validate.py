from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tasks import validate

SEED_TEST = "from calc import value\n\ndef test_seed():\n    assert isinstance(value(), int)\n"
HIDDEN_TEST = "from calc import value\n\ndef test_fix():\n    assert value() == 2\n"


def _patch(path: str, old: str, new: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@\n def value():\n-    return {old}\n+    return {new}\n"


def _add_patch(path: str, body: str) -> str:
    lines = body.splitlines()
    added = "".join(f"+{line}\n" for line in lines)
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"


GOLD = _patch("calc.py", "1", "2")
SKIP_ALL = (
    "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
    "    for item in items:\n        item.add_marker(pytest.mark.skip(reason='skip'))\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_task(
    root: Path,
    name: str = "calc",
    *,
    files: dict[str, str] | None = None,
    hidden: dict[str, str] | None = None,
    gold: str = GOLD,
    meta: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    task = root / name
    fixture = {"calc.py": "def value():\n    return 1\n", "tests/test_calc.py": SEED_TEST}
    fixture.update(files or {})
    for rel, text in fixture.items():
        _write(task / "fixture" / rel, text)
    for rel, text in (hidden or {"test_hidden.py": HIDDEN_TEST}).items():
        _write(task / "hidden_tests" / rel, text)
    _write(task / "gold.patch", gold)
    _write(task / "prompt.md", "Make value return 2.\n")
    _write(task / "rubric.md", "Score 0-5.\n")
    data: dict[str, Any] = {
        "schema": "pact-bench/task/1",
        "id": name,
        "title": "Calc",
        "class": "low",
        "variety": 5,
        "revision": 1,
        "timeout_seconds": 60,
        "max_turns": 5,
        "prompt": "prompt.md",
        "fixture": "fixture",
        "hidden_tests": "hidden_tests",
        "gold_patch": "gold.patch",
        "rubric": "rubric.md",
    }
    data.update(meta or {})
    for key in drop:
        data.pop(key)
    _write(task / "task.json", json.dumps(data))
    return task


def test_validator_accepts_non_vacuous_task(tmp_path: Path) -> None:
    result = validate.validate_task(make_task(tmp_path), sys.executable)
    assert result["status"] == "pass"
    assert result["hidden_before"] == "fail"
    assert result["hidden_after"] == "pass"
    assert result["seed_after"] == "pass"


def test_validator_accepts_add_only_patch(tmp_path: Path) -> None:
    task = make_task(
        tmp_path,
        # Imported inside the test, so before the patch the test fails (exit 1) rather than erroring at collection.
        hidden={"test_hidden.py": "def test_fix():\n    from extra import X\n\n    assert X == 2\n"},
        gold=_add_patch("extra.py", "X = 2\n"),
    )
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_seed_after_ignores_hidden_tests(tmp_path: Path) -> None:
    # Same basename in tests/ and hidden_tests/ cannot be collected in one session.
    task = make_task(tmp_path, hidden={"test_calc.py": HIDDEN_TEST})
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_pytest_runs_with_plugin_isolation_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    real = validate._run

    def record(cmd: list[str], *args: Any) -> Any:
        calls.append(cmd)
        return real(cmd, *args)

    monkeypatch.setattr(validate, "_run", record)
    validate.validate_task(make_task(tmp_path), sys.executable)
    pytest_calls = [cmd for cmd in calls if cmd[1:3] == ["-m", "pytest"]]
    assert len(pytest_calls) == 4  # seed, hidden before, hidden after, seed after
    for cmd in pytest_calls:
        assert cmd[3:8] == ["-q", "-p", "no:randomly", "-p", "no:cacheprovider"]


def test_hidden_xfail_is_not_a_skip(tmp_path: Path) -> None:
    hidden = HIDDEN_TEST + "\n\nimport pytest\n\n\n@pytest.mark.xfail(strict=True)\ndef test_known_bug():\n    assert False\n"
    task = make_task(tmp_path, hidden={"test_hidden.py": hidden})
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


@pytest.mark.parametrize("where", ["fixture", "hidden_tests"])
def test_symlink_inside_a_copied_dir_pointing_out_is_rejected(tmp_path: Path, where: str) -> None:
    task = make_task(tmp_path / "t")
    _write(tmp_path / "outside.py", "X = 1\n")
    (task / where / "linked.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(validate.TaskInvalid, match=f"{where} has symlinks resolving outside"):
        validate.validate_task(task, sys.executable)


def test_outside_link_behind_an_inside_directory_link_is_rejected(tmp_path: Path) -> None:
    # fixture/shared -> <task>/shared (inside), and <task>/shared/leak.txt -> a host file (outside).
    task = make_task(tmp_path / "t")
    _write(tmp_path / "secret.txt", "HOST-SECRET\n")
    (task / "shared").mkdir()
    (task / "shared" / "leak.txt").symlink_to(tmp_path / "secret.txt")
    (task / "fixture" / "shared").symlink_to(task / "shared", target_is_directory=True)
    with pytest.raises(validate.TaskInvalid, match="fixture has symlinks resolving outside"):
        validate.validate_task(task, sys.executable)


def test_outside_directory_link_is_not_walked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "host"
    _write(host / "deep" / "deeper" / "file.txt", "x\n")
    task = make_task(tmp_path / "t")
    (task / "fixture" / "host").symlink_to(host, target_is_directory=True)
    walked: list[str] = []
    real_walk = validate.os.walk

    def counting_walk(top, **kwargs):
        for entry in real_walk(top, **kwargs):
            walked.append(os.path.realpath(entry[0]))
            yield entry

    monkeypatch.setattr(validate.os, "walk", counting_walk)
    with pytest.raises(validate.TaskInvalid, match=r"fixture has symlinks resolving outside .*fixture/host"):
        validate.validate_task(task, sys.executable)
    assert not any(Path(root).is_relative_to(host) for root in walked)


def test_directory_link_cycle_does_not_hang(tmp_path: Path) -> None:
    task = make_task(tmp_path / "t")
    (task / "fixture" / "loop").symlink_to(task / "fixture", target_is_directory=True)
    with pytest.raises(Exception):  # copytree cannot copy a cycle; the scan itself must return
        validate.validate_task(task, sys.executable)


def test_symlink_inside_a_copied_dir_pointing_inside_is_kept(tmp_path: Path) -> None:
    task = make_task(tmp_path / "t")
    (task / "fixture" / "calc_link.py").symlink_to(task / "fixture" / "calc.py")
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_symlink_out_of_the_task_is_rejected(tmp_path: Path) -> None:
    task = make_task(tmp_path / "t", meta={"rubric": "rubric-link.md"})
    _write(tmp_path / "outside.md", "x\n")
    (task / "rubric-link.md").symlink_to(tmp_path / "outside.md")
    with pytest.raises(validate.TaskInvalid, match="rubric 'rubric-link.md' resolves outside"):
        validate.validate_task(task, sys.executable)


def test_global_git_config_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A global excludesFile of "*" would make the seed commit empty and fail.
    excludes = tmp_path / "excludes"
    _write(excludes, "*\n")
    config = tmp_path / "gitconfig"
    _write(config, f"[core]\n\texcludesFile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"


def _failing_hooks(directory: Path) -> Path:
    _write(directory / "pre-commit", "#!/bin/sh\nexit 1\n")
    (directory / "pre-commit").chmod(0o755)
    return directory


def test_inherited_git_config_parameters_do_not_reach_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # GIT_CONFIG_PARAMETERS is how "git -c" reaches child gits; the env allowlist drops it.
    hooks = _failing_hooks(tmp_path / "hooks")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hookspath'='{hooks}'")
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"


def test_a_fixtures_own_git_hooks_do_not_run(tmp_path: Path) -> None:
    task = make_task(tmp_path / "t")
    env = {**os.environ, **validate.GIT_ENV}
    subprocess.run(["git", "init", "-q"], cwd=task / "fixture", env=env, check=True)
    _failing_hooks(task / "fixture" / ".git" / "hooks")
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_host_per_user_git_ignore_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The host's ~/.config/git/ignore hiding the file the patch adds would read as "changed nothing".
    home = tmp_path / "home"
    _write(home / ".config" / "git" / "ignore", "extra.py\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    task = make_task(
        tmp_path / "t",
        hidden={"test_hidden.py": "def test_fix():\n    from extra import X\n\n    assert X == 2\n"},
        gold=_add_patch("extra.py", "X = 2\n"),
    )
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_host_secrets_do_not_reach_task_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "host-secret")
    # HOME and the temp dir must point into the task's own temp dir (<tmp>/work's parent).
    seed = (
        "import os, tempfile\nfrom pathlib import Path\n\ndef test_seed():\n"
        "    assert 'GH_TOKEN' not in os.environ\n"
        "    task_tmp = Path.cwd().parent.resolve()\n"
        "    assert Path(os.environ['HOME']).resolve() == task_tmp\n"
        "    assert Path(tempfile.gettempdir()).resolve() == task_tmp\n"
    )
    task = make_task(tmp_path, files={"tests/test_calc.py": seed})
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


@pytest.mark.parametrize(
    ("name", "value"),
    [("PYTEST_ADDOPTS", "--no-such-pytest-flag"), ("PYTEST_PLUGINS", "no_such_plugin_module"), ("PYTHONPATH", "shadow")],
)
def test_live_python_env_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    if name == "PYTHONPATH":
        # A pytest module earlier on PYTHONPATH would replace the real one.
        _write(tmp_path / "shadow" / "pytest.py", "raise SystemExit(3)\n")
        value = str(tmp_path / "shadow")
    monkeypatch.setenv(name, value)
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"


BROKEN = {
    "hidden_passes_before": (
        {"hidden": {"test_hidden.py": "from calc import value\n\ndef test_fix():\n    assert value() == 1\n"}},
        "vacuous",
    ),
    "empty_patch": ({"gold": ""}, "did not apply"),
    "patch_does_not_apply": ({"gold": _patch("calc.py", "7", "2")}, "did not apply"),
    "patch_changes_only_ignored_files": (
        {"files": {".gitignore": "*.log\n"}, "gold": _add_patch("out.log", "x\n")},
        "changed nothing",
    ),
    "hidden_fails_after": ({"gold": _patch("calc.py", "1", "3")}, "hidden tests failed after"),
    "seed_fails": ({"files": {"tests/test_calc.py": "def test_seed():\n    assert False\n"}}, "seed tests failed:"),
    "seed_fails_after": (
        {"files": {"tests/test_calc.py": "from calc import value\n\ndef test_seed():\n    assert value() != 2\n"}},
        "seed tests failed after",
    ),
    "missing_field": ({"drop": ("rubric",)}, "missing task.json fields"),
    "wrong_schema": ({"meta": {"schema": "pact-bench/task/0"}}, "unsupported schema"),
    "patch_touches_hidden_tests": (
        {"gold": "diff --git a/hidden_tests/test_hidden.py b/hidden_tests/test_hidden.py\n"
         "--- a/hidden_tests/test_hidden.py\n+++ b/hidden_tests/test_hidden.py\n"
         "@@ -3,2 +3,2 @@\n def test_fix():\n-    assert value() == 2\n+    assert value() == 1\n"},
        "modifies hidden_tests",
    ),
    "patch_touches_non_ascii_hidden_path": (
        {
            "hidden": {"test_\u00e9.py": HIDDEN_TEST},
            "gold": "diff --git a/hidden_tests/test_\u00e9.py b/hidden_tests/test_\u00e9.py\n"
            "--- a/hidden_tests/test_\u00e9.py\n+++ b/hidden_tests/test_\u00e9.py\n"
            "@@ -3,2 +3,2 @@\n def test_fix():\n-    assert value() == 2\n+    assert value() == 1\n",
        },
        "modifies hidden_tests",
    ),
    "patch_renames_into_hidden_tests": (
        {
            "gold": "diff --git a/calc.py b/hidden_tests/calc.py\nsimilarity index 100%\n"
            "rename from calc.py\nrename to hidden_tests/calc.py\n",
        },
        "modifies hidden_tests",
    ),
    "patch_renames_out_of_hidden_tests": (
        {
            "gold": "diff --git a/hidden_tests/test_hidden.py b/test_moved.py\nsimilarity index 100%\n"
            "rename from hidden_tests/test_hidden.py\nrename to test_moved.py\n",
        },
        "modifies hidden_tests",
    ),
    "git_fails": ({"files": {".git": "gitdir: /nonexistent\n"}}, "git init -q -b main failed"),
    "timeout": (
        {
            "files": {"tests/test_calc.py": "import time\n\ndef test_seed():\n    time.sleep(10)\n"},
            "meta": {"timeout_seconds": 1},
        },
        "timed out after 1s",
    ),
    "patch_skips_every_test": (
        {"gold": GOLD + _add_patch("conftest.py", SKIP_ALL)},
        "must run and pass, none skipped: 0 passed",
    ),
    "patch_skips_one_hidden_test": (
        {
            "hidden": {"test_hidden.py": HIDDEN_TEST + "\n\ndef test_extra():\n    assert value() == 2\n"},
            "gold": GOLD + _add_patch(
                "conftest.py",
                "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n        if item.name == 'test_extra':\n"
                "            item.add_marker(pytest.mark.skip(reason='skip'))\n",
            ),
        },
        "1 passed, 1 skipped",
    ),
    "patch_skips_seed_tests": (
        {"gold": GOLD + _add_patch("tests/conftest.py", SKIP_ALL)},
        "the gold patch removed or skipped seed tests",
    ),
    "patch_adds_one_seed_test_and_skips_another": (
        {
            "files": {"tests/test_other.py": "def test_other():\n    assert True\n"},
            "gold": GOLD + _add_patch("tests/test_new.py", "def test_new():\n    assert True\n") + _add_patch(
                "tests/conftest.py",
                "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n        if item.name == 'test_other':\n"
                "            item.add_marker(pytest.mark.skip(reason='skip'))\n",
            ),
        },
        "the gold patch removed or skipped seed tests",
    ),
    "patch_deletes_a_seed_test": (
        {
            "files": {"tests/test_extra.py": "def test_extra():\n    assert True\n"},
            "gold": GOLD + "diff --git a/tests/test_extra.py b/tests/test_extra.py\ndeleted file mode 100644\n"
            "--- a/tests/test_extra.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def test_extra():\n-    assert True\n",
        },
        "the gold patch removed or skipped seed tests",
    ),
    "patch_xfails_every_hidden_test": (
        {
            "gold": _patch("calc.py", "1", "3") + _add_patch(
                "conftest.py",
                "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n        item.add_marker(pytest.mark.xfail(reason='x'))\n",
            ),
        },
        "must run and pass, none skipped: 0 passed, 0 skipped",
    ),
    "fixture_path_escapes": ({"meta": {"fixture": "../elsewhere"}}, "resolves outside the task directory"),
    "absolute_gold_path": ({"meta": {"gold_patch": "/etc/hostname"}}, "resolves outside the task directory"),
    "junit_report_disabled": (
        {"files": {"pytest.ini": "[pytest]\naddopts = -p no:junitxml\n"}},
        "pytest wrote no JUnit report for the seed run",
    ),
    "hidden_collection_error": (
        {"hidden": {"test_hidden.py": "def test_fix(:\n"}},
        "pytest exit 2",
    ),
    "hidden_has_no_tests": ({"hidden": {"test_hidden.py": "VALUE = 1\n"}}, "pytest exit 5"),
    "timeout_not_a_number": ({"meta": {"timeout_seconds": "60"}}, "timeout_seconds must be a positive integer"),
    "timeout_zero": ({"meta": {"timeout_seconds": 0}}, "timeout_seconds must be a positive integer"),
    "timeout_bool": ({"meta": {"timeout_seconds": True}}, "timeout_seconds must be a positive integer"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_validator_rejects_broken_task(tmp_path: Path, case: str) -> None:
    kwargs, message = BROKEN[case]
    with pytest.raises(validate.TaskInvalid, match=message):
        validate.validate_task(make_task(tmp_path, **kwargs), sys.executable)


def test_main_reports_every_task_and_fails_if_any_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_task(tmp_path, "a_bad", gold="", meta={"id": "a-bad-id"})
    (make_task(tmp_path, "b_copy_error") / "fixture" / "dangling").symlink_to(tmp_path / "missing")
    make_task(tmp_path, "c_good")
    assert validate.main(["--root", str(tmp_path)]) == 1
    tasks = json.loads(capsys.readouterr().out)["tasks"]
    # The fail row carries task.json's id even when it differs from the directory name.
    assert [(t["id"], t["status"]) for t in tasks] == [("a-bad-id", "fail"), ("b_copy_error", "fail"), ("c_good", "pass")]
    assert "a-bad-id" in tasks[0]["error"]
    assert "b_copy_error" in tasks[1]["error"]


def _raw(text: bytes):
    def write(task: Path) -> None:
        (task / "task.json").write_bytes(text)

    return write


def _raise_in_check(monkeypatch: pytest.MonkeyPatch):
    real = validate._check_in_temp

    def check(task_id: str, *args: Any) -> None:
        if task_id == "bad-id":
            raise LookupError("unexpected")
        real(task_id, *args)

    monkeypatch.setattr(validate, "_check_in_temp", check)


# (make_task overrides, post-write hook, expected fail-row id, expected error fragment)
BAD_INPUTS = {
    "non_utf8_task_json": ({}, _raw(b'{"id": "\xff"}'), "a_bad", "UnicodeDecodeError"),
    "unparseable_task_json": ({}, _raw(b"{"), "a_bad", "JSONDecodeError"),
    "task_json_not_an_object": ({}, _raw(b"7"), "a_bad", "TypeError"),
    "non_string_path_field": ({"meta": {"id": "bad-id", "fixture": 5}}, None, "bad-id", "must be a path string"),
    "deeply_nested_task_json": ({}, _raw(b"[" * 200000 + b"]" * 200000), "a_bad", "RecursionError"),
    "id_with_a_slash": ({"meta": {"id": "bad/id"}, "gold": ""}, None, "bad/id", "did not apply"),
    "unexpected_exception": ({"meta": {"id": "bad-id"}}, "check", "bad-id", "LookupError: unexpected"),
}


@pytest.mark.parametrize("case", sorted(BAD_INPUTS))
def test_one_bad_task_fails_only_itself(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    kwargs, hook, expected_id, fragment = BAD_INPUTS[case]
    bad = make_task(tmp_path, "a_bad", **kwargs)
    if hook == "check":
        _raise_in_check(monkeypatch)
    elif hook:
        hook(bad)
    make_task(tmp_path, "b_good")
    assert validate.main(["--root", str(tmp_path)]) == 1
    tasks = json.loads(capsys.readouterr().out)["tasks"]
    assert [(t["id"], t["status"]) for t in tasks] == [(expected_id, "fail"), ("b_good", "pass")]
    assert fragment in tasks[0]["error"]
    assert tasks[0]["error"].startswith(expected_id + ":")


def _git_count(repo: Path) -> int:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"], env=env, capture_output=True, text=True, check=True
    )
    return int(out.stdout)


def test_inherited_git_env_does_not_redirect_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **validate.GIT_ENV}
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "outer"]):
        subprocess.run(["git", *args], cwd=outer, env=env, check=True)
    assert _git_count(outer) == 1
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer / ".git" / "index"))
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"
    assert _git_count(outer) == 1


def test_timeout_kills_grandchildren(tmp_path: Path) -> None:
    pid_file = tmp_path / "kid.pid"
    seed = (
        "import subprocess, sys, time\n\ndef test_seed():\n"
        "    kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"    open({str(pid_file)!r}, 'w').write(str(kid.pid))\n"
        "    time.sleep(30)\n"
    )
    task = make_task(tmp_path, files={"tests/test_calc.py": seed}, meta={"timeout_seconds": 2})
    started = time.monotonic()
    with pytest.raises(validate.TaskInvalid, match="timed out"):
        validate.validate_task(task, sys.executable)
    # A surviving grandchild holds the output pipe open until its 30s sleep ends.
    assert time.monotonic() - started < 15
    pid = int(pid_file.read_text())
    alive = not _dead(pid)
    if alive:
        os.kill(pid, 9)
    assert not alive


def _dead(pid: int) -> bool:
    """True once pid is gone (or a zombie, where /proc shows it); works without /proc too."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return True
        except OSError:
            pass  # no /proc (or the pid just vanished): os.kill decides on the next pass
        time.sleep(0.1)
    return False


def test_dead_sees_a_live_process() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not _dead(proc.pid)
    finally:
        proc.kill()
        proc.wait()
    assert _dead(proc.pid)


def test_main_single_task_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    task = make_task(tmp_path)
    assert validate.main(["--task", str(task)]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"][0]["id"] == "calc"


def test_main_empty_root_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no tasks found"):
        validate.main(["--root", str(tmp_path)])


def test_main_has_no_all_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        validate.main(["--all", "--root", str(tmp_path)])
    assert exc.value.code == 2
