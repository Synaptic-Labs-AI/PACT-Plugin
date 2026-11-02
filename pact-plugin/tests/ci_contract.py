"""Shared helpers for the CI-contract tests.

No business logic lives here. A checkout is identified by a POSITIVE marker,
never by "the workflow file is absent"; a workflow that fails to parse is a
``PARSE FAILURE`` rather than an empty step list; and the pytest summary
parser knows both decorations, because a summary regex anchored to one form
matches nothing in the other and reads exactly like a run that never started.

The runtime option probe exists so a test can demonstrate the difference
between a bare invocation and a filtered one without trusting prose: it runs
pytest under a probe conftest and returns the parsed ``config.args`` and the
selection options.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_MARKER = Path(".claude-plugin") / "marketplace.json"
FULL_WORKFLOW = Path(".github") / "workflows" / "tests.yml"
FAST_WORKFLOW = Path(".github") / "workflows" / "fast-tests.yml"

VERIFY_CMD = "python scripts/verify_ci_environment.py"
TEST_CMD = "python -m pytest -ra"

# The files that together make up the fast lane. Used to distinguish a
# WHOLLY deleted lane (a loud skip) from a half-deleted one (a failure).
FAST_LANE_FILES = (
    ".github/workflows/fast-tests.yml",
    "scripts/fast_suite_exclusions.json",
    "scripts/fast_suite_args.py",
    "scripts/fast_suite_selection.py",
    "scripts/run_fast_suite.py",
    "scripts/verify_fast_summary.py",
)


def fast_lane_state(root):
    """Return (state, present, missing) for the fast lane's sentinel files.

    state is "present" (all files), "absent" (none), or "mixed" (some).
    A mixed state is a broken checkout, never a skip.
    """
    root = Path(root)
    present = [rel for rel in FAST_LANE_FILES if (root / rel).is_file()]
    missing = [rel for rel in FAST_LANE_FILES if rel not in present]
    if not present:
        return "absent", present, missing
    if not missing:
        return "present", present, missing
    return "mixed", present, missing


def source_repo_root(start=None):
    """The checkout root by the positive marker, or None when there is none."""
    here = Path(start) if start is not None else Path(__file__).resolve()
    candidates = [here] if here.is_dir() else []
    candidates.extend(here.parents)
    for base in candidates:
        if (base / REPO_MARKER).exists():
            return base
    return None


def require_root(what, start=None):
    """The checkout root, or a LOUD skip when this is an installed cache."""
    root = source_repo_root(start)
    if root is None:
        pytest.skip(
            "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
            f"{Path(start) if start is not None else Path(__file__).resolve()}, "
            "so this is not a PACT source checkout. The expected case is an "
            "INSTALLED PLUGIN CACHE, which has no .github/ tree by design. "
            f"This skip is NO INFORMATION about {what} — never read it as a pass."
        )
    return root


def read_workflow(root, rel):
    """Workflow text. Inside a checkout a missing required workflow is a FAILURE."""
    path = Path(root) / rel
    assert path.is_file(), (
        f"source checkout detected at {root} but {path} is missing. Inside a "
        f"checkout a required workflow or script is a FAILURE, not a skip."
    )
    return path.read_text(encoding="utf-8")


def load_workflow(root, rel):
    """Parsed workflow mapping; unparseable YAML is a PARSE FAILURE."""
    path = Path(root) / rel
    text = read_workflow(root, rel)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AssertionError(
            f"PARSE FAILURE, not a clean read: {path} does not parse as YAML "
            f"({exc.__class__.__name__}), so its steps cannot be enumerated. "
            f"GitHub would refuse to run it; fix the syntax."
        ) from exc
    assert isinstance(doc, dict), (
        f"PARSE FAILURE, not a clean read: {path} parsed to "
        f"{type(doc).__name__}, not a mapping, so its jobs cannot be read."
    )
    return doc


def jobs_of(doc):
    """Every job mapping keyed by job id."""
    return {
        job_id: job
        for job_id, job in (doc.get("jobs") or {}).items()
        if isinstance(job, dict)
    }


def steps_of(job):
    """Every step mapping in one job."""
    return [step for step in job.get("steps") or [] if isinstance(step, dict)]


def run_texts(steps):
    """The `run:` strings of a step list, in order, ignoring steps without one."""
    return [step["run"] for step in steps if isinstance(step.get("run"), str)]


def effective_check_name(job_id, job):
    """The check context GitHub reports: `name:` when set, else the job id."""
    name = job.get("name")
    return name if isinstance(name, str) and name else job_id


_SUMMARY_EDGE = re.compile(r"^[=\s]+|[=\s]+$")
_SUMMARY_START = re.compile(
    r"^(?:no tests (?:collected|ran)|\d+ tests? collected|\d+ passed)")


def summary_line(text):
    """The pytest summary line, decoration stripped, or None when absent.

    Handles both decorations: `N tests collected in Xs` under `-q` and
    `===== N tests collected in Xs =====` without it. An absent summary is
    None — callers must treat that as "the run may never have started", never
    as a zero count.
    """
    for raw in reversed(text.splitlines()):
        line = _SUMMARY_EDGE.sub("", raw.strip())
        if _SUMMARY_START.match(line):
            return line
    return None


def summary_counts(line):
    """Counts parsed from a summary line; every count defaults to zero."""
    if line is None:
        raise ValueError("no summary line to parse")
    counts = {
        "collected": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "deselected": 0,
    }
    if line.startswith("no tests collected"):
        match = re.search(r"\((\d+) deselected\)", line)
        if match:
            counts["deselected"] = int(match.group(1))
        return counts
    if line.startswith("no tests ran"):
        return counts
    match = re.match(r"^(\d+) tests? collected", line)
    if match:
        counts["collected"] = int(match.group(1))
    for key, pattern in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("errors", r"(\d+) errors?"),
        ("skipped", r"(\d+) skipped"),
        ("deselected", r"(\d+) deselected"),
    ):
        match = re.search(pattern, line)
        if match:
            counts[key] = int(match.group(1))
    return counts


_PROBE_CONFTEST = '''\
import json


def pytest_configure(config):
    print("PACT-OPTION-PROBE:" + json.dumps({
        "args": [str(arg) for arg in config.args],
        "keyword": config.option.keyword,
        "markexpr": getattr(config.option, "markexpr", "") or "",
        "deselect": list(getattr(config.option, "deselect", None) or []),
        "ignore": list(getattr(config.option, "ignore", None) or []),
    }, sort_keys=True))
'''


def probe_options(probe_dir, args=(), timeout=120):
    """Run pytest under a probe conftest; return (options dict or None, proc).

    The probe is what lets a test assert "bare invocation" as a SHAPE
    (`config.args == [cwd]`) instead of a claim about what the flags ought to
    imply. A filtered invocation reports the filter here regardless of how the
    command was wrapped.
    """
    probe_dir = Path(probe_dir)
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "conftest.py").write_text(_PROBE_CONFTEST, encoding="utf-8")
    (probe_dir / "test_option_probe.py").write_text(
        "def test_option_probe():\n    assert True\n\n"
        "def test_second_probe():\n    assert True\n",
        encoding="utf-8")
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q",
               "-p", "no:cacheprovider", *args]
    proc = subprocess.run(
        command, cwd=str(probe_dir), capture_output=True, text=True,
        timeout=timeout)
    report = None
    for raw in proc.stdout.splitlines():
        if raw.startswith("PACT-OPTION-PROBE:"):
            report = json.loads(raw.split(":", 1)[1])
    return report, proc