from __future__ import annotations

import json
import sys
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
        hidden={"test_hidden.py": "from extra import X\n\ndef test_fix():\n    assert X == 2\n"},
        gold=_add_patch("extra.py", "X = 2\n"),
    )
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def test_seed_after_ignores_hidden_tests(tmp_path: Path) -> None:
    # Same basename in tests/ and hidden_tests/ cannot be collected in one session.
    task = make_task(tmp_path, hidden={"test_calc.py": HIDDEN_TEST})
    assert validate.validate_task(task, sys.executable)["status"] == "pass"


def _hook_config(tmp_path: Path) -> Path:
    hooks = tmp_path / "hooks"
    _write(hooks / "pre-commit", "#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    config = tmp_path / "gitconfig"
    _write(config, f"[core]\n\thooksPath = {hooks}\n")
    return config


def test_global_git_config_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A global excludesFile of "*" would make the seed commit empty and fail.
    excludes = tmp_path / "excludes"
    _write(excludes, "*\n")
    config = tmp_path / "gitconfig"
    _write(config, f"[core]\n\texcludesFile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"


def test_hooks_path_is_disabled_against_inherited_git_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # GIT_CONFIG_PARAMETERS is how "git -c" reaches child gits; only -c core.hooksPath overrides it.
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hookspath'='{tmp_path / 'hooks'}'")
    _hook_config(tmp_path)
    assert validate.validate_task(make_task(tmp_path / "t"), sys.executable)["status"] == "pass"


def test_pytest_addopts_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--no-such-pytest-flag")
    assert validate.validate_task(make_task(tmp_path), sys.executable)["status"] == "pass"


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
    "timeout": (
        {
            "files": {"tests/test_calc.py": "import time\n\ndef test_seed():\n    time.sleep(10)\n"},
            "meta": {"timeout_seconds": 1},
        },
        "timed out after 1s",
    ),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_validator_rejects_broken_task(tmp_path: Path, case: str) -> None:
    kwargs, message = BROKEN[case]
    with pytest.raises(validate.TaskInvalid, match=message):
        validate.validate_task(make_task(tmp_path, **kwargs), sys.executable)


def test_main_reports_every_task_and_fails_if_any_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_task(tmp_path, "a_bad", gold="")
    make_task(tmp_path, "b_good")
    assert validate.main(["--root", str(tmp_path)]) == 1
    tasks = json.loads(capsys.readouterr().out)["tasks"]
    assert [t["status"] for t in tasks] == ["fail", "pass"]
    assert "a_bad" in tasks[0]["error"]


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
