from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "bench-harness.yml"
RUN_HERMETIC = ROOT / "bench" / "run_hermetic.sh"
BOOTSTRAP = ROOT / "bench" / "bootstrap.sh"
BENCH_LOCK = ROOT / "bench" / "constraints.txt"
SCAFFOLD = ROOT / "bench" / "external" / "native-evals" / "evals" / "bootstrap" / "scaffold.sh"
CI_LOCK = ROOT / "constraints-ci.txt"

PATHS = [
    "bench/**",
    ".github/workflows/bench-harness.yml",
    "constraints-ci.txt",
    "scripts/verify_ci_environment.py",
    "BENCHMARKS.md",
    "pyproject.toml",
]
HASHED_INSTALL = "--only-binary=:all: --require-hashes -r"
VERIFY = "python scripts/verify_ci_environment.py"
STEPS = [
    ("uses", "actions/checkout"),
    ("uses", "actions/setup-python"),
    ("run", f"python -m pip install {HASHED_INSTALL} constraints-ci.txt"),
    ("run", "ruff check --select F401,F821 --target-version py312 bench/"),
    ("run", VERIFY),
    ("run", "bash bench/run_hermetic.sh"),
    ("run", VERIFY),
]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    jobs = _workflow()["jobs"]
    assert list(jobs) == ["hermetic"]
    return jobs["hermetic"]["steps"]


def _lock(path: Path) -> dict[str, str]:
    """Name -> version for every pin; asserts each pin carries a hash line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    pins: dict[str, str] = {}
    for n, line in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", line)
        if m:
            assert line.rstrip().endswith("\\"), f"{path.name}: {m.group(1)} has no hash"
            assert lines[n + 1].strip().startswith("--hash=sha256:"), m.group(1)
            pins[m.group(1).lower()] = m.group(2)
    return pins


def test_workflow_triggers_and_paths() -> None:
    doc = _workflow()
    on = doc.get("on", doc.get(True))  # PyYAML reads a bare `on` key as True
    assert on == {
        "pull_request": {"paths": PATHS},
        "push": {"branches": ["main"], "paths": PATHS},
    }
    assert doc["permissions"] == {"contents": "read"}
    assert "github.ref" in doc["concurrency"]["group"]


def _glob(pattern: str) -> re.Pattern[str]:
    """GitHub's paths-filter glob for the subset PATHS may use: `**` crosses `/`,
    `*` does not. Other glob syntax ([], !, +, ?) is refused, not guessed."""
    assert not set(pattern) & set("[]!+?"), f"unsupported glob syntax in {pattern!r}"
    parts = re.split(r"(\*\*|\*)", pattern)
    table = {"**": ".*", "*": "[^/]*"}
    return re.compile("".join(table.get(part, re.escape(part)) for part in parts))


def _covered(rel: str, patterns: list[str] = PATHS) -> bool:
    return any(_glob(pattern).fullmatch(rel) for pattern in patterns)


def test_glob_matches_github_semantics() -> None:
    assert _covered("bench/tests/x.py", ["bench/**"])
    assert not _covered("bench/tests/x.py", ["bench/*"])
    assert _covered("bench/x.py", ["bench/*"])
    assert not _covered("a/b.md", ["*.md"])
    assert not _covered("BENCHMARKSxmd", ["BENCHMARKS.md"])
    for pattern in ("[ab]", "a?b", "!x", "a+"):
        with pytest.raises(AssertionError, match="unsupported"):
            _covered("x", [pattern])


def test_path_filter_covers_every_file_the_bench_tests_read(pytestconfig: pytest.Config) -> None:
    # Every ROOT-relative path chain in a bench test module, plus pytest's own config file.
    read = set()
    for module in (ROOT / "bench" / "tests").glob("*.py"):
        for chain in re.findall(r'\bROOT((?:\s*/\s*"[^"]+")+)', module.read_text(encoding="utf-8")):
            read.add("/".join(re.findall(r'"([^"]+)"', chain)))
    assert {"BENCHMARKS.md", "constraints-ci.txt", "bench/results"} <= read  # control: the scan sees known reads
    inipath = pytestconfig.inipath
    if inipath is not None and inipath.is_relative_to(ROOT):
        read.add(inipath.relative_to(ROOT).as_posix())
    assert "pyproject.toml" in read  # control: the run's config file was seen
    # GitHub filters changed FILES, so a directory read is covered only if every file under it is.
    files = set()
    for rel in read:
        path = ROOT / rel
        files |= ({p.relative_to(ROOT).as_posix() for p in path.rglob("*") if p.is_file()}
                  if path.is_dir() else {rel})
    missing = sorted(rel for rel in files if not _covered(rel))
    assert not missing, f"read by bench tests but outside the workflow path filter: {missing}"


def test_workflow_steps_are_pinned_and_bracketed() -> None:
    steps = _steps()
    shape = [("uses", s["uses"].split("@")[0]) if "uses" in s else ("run", s["run"].strip())
             for s in steps]
    assert shape == STEPS
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
        else:
            assert not {"env", "shell", "continue-on-error", "working-directory"} & set(step), step
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[-1].get("if") == "always()"
    assert all("if" not in s for s in steps[:-1])
    assert _workflow()["jobs"]["hermetic"]["timeout-minutes"] == 10


def test_workflow_uses_no_secrets_or_model_calls() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"secrets\.|ANTHROPIC|OPENAI|claude\s+-p|claude plugin eval", text)


def test_run_hermetic_runs_only_bench_tests_from_repo_root() -> None:
    text = RUN_HERMETIC.read_text(encoding="utf-8")
    assert 'cd "$(dirname "$0")/.."' in text
    assert re.search(r'^exec "\$py" -m pytest bench/tests -q "\$@"$', text, re.M)
    assert re.search(r'^command -v "\$py" >/dev/null 2>&1 \|\| py=python3$', text, re.M)


def test_bootstrap_installs_the_hashed_bench_lock() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert f'-m pip install {HASHED_INSTALL} "$here/constraints.txt"' in text
    assert "sys.version_info < (3, 12)" in text
    assert 'python3 -m venv --clear "$venv"' in text
    assert "--upgrade" not in text
    bench = _lock(BENCH_LOCK)
    assert {"pip", "pytest", "pyyaml"} <= set(bench)
    ci = _lock(CI_LOCK)
    drift = {n: (v, ci[n]) for n, v in bench.items() if n in ci and ci[n] != v}
    assert not drift, f"bench/constraints.txt drifted from constraints-ci.txt: {drift}"


def test_scaffold_seed_ignores_host_git_config_and_hooks(tmp_path: Path) -> None:
    # One hostile input per isolation layer: a system and a global config that each
    # ignore README.md (defeated by GIT_CONFIG_NOSYSTEM and GIT_CONFIG_GLOBAL=/dev/null),
    # and a template dir whose pre-commit hook fails and leaves a marker (core.hooksPath).
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / "template" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ignore").write_text("README.md\n", encoding="utf-8")
    ignore_cfg = f"[core]\n\texcludesFile = {home / 'ignore'}\n"
    (home / ".gitconfig").write_text(ignore_cfg, encoding="utf-8")
    (tmp_path / "system.gitconfig").write_text(ignore_cfg, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_TEMPLATE_DIR": str(tmp_path / "template"),
           "GIT_CONFIG_SYSTEM": str(tmp_path / "system.gitconfig")}
    subprocess.run(["bash", str(SCAFFOLD)], cwd=work, env=env, check=True)
    assert not marker.exists()
    log = subprocess.run(["git", "log", "--format=%s"], cwd=work, env=env, capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "seed"
