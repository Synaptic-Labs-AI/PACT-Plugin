"""Non-vacuity probes for the independent full-collection guard.

Every parser and comparator in ``scripts/verify_full_collection.py`` is shown
to reject a known-bad input in a rig copy. A guard whose failure branches are
never exercised is counted as coverage without being coverage, so each probe
below feeds one bad shape through the real entry point and asserts the
category the guard reports:

* a skill-adjacent test file is discovered and its absence from collection is
  reported (the founding collection-scope defect);
* a narrowed collection (``--ignore``) is reported as a set difference;
* a ``testpaths`` config entry is rejected;
* an empty corpus is a refusal, not a vacuous pass;
* an absent summary is a PARSE FAILURE, reported separately from a mismatch;
* a node ID that resolves under neither base is refused.

The runtime option probe at the bottom proves the bare-vs-filtered shape the
whole design leans on is observable, not assumed.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import ci_contract

GUARD = Path("scripts") / "verify_full_collection.py"
ANCHOR = Path("pact-plugin") / "tests" / "test_ci_collection_scope.py"
SKILL_TEST = Path("pact-plugin") / "skills" / "pact-example" / "test_skill_loading.py"

FAKE_OMIT_SKILL = '''\
print("tests/test_alpha.py::test_alpha")
print("tests/test_ci_collection_scope.py::test_anchor")
print("2 tests collected in 0.01s")
'''

FAKE_NO_SUMMARY = '''\
print("tests/test_alpha.py::test_alpha")
'''

FAKE_UNRESOLVABLE = '''\
print("nowhere/test_unknown.py::test_unknown")
print("1 test collected in 0.01s")
'''

FAKE_DESELECTED = '''\
print("tests/test_alpha.py::test_alpha")
print("tests/test_ci_collection_scope.py::test_anchor")
print("2 tests collected, 1 deselected in 0.01s")
'''

FAKE_ERRORS = '''\
print("tests/test_alpha.py::test_alpha")
print("tests/test_ci_collection_scope.py::test_anchor")
print("2 tests collected, 1 error in 0.01s")
'''


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _base_rig(tmp_path, name="rig", corpus=True):
    """A minimal checkout carrying the real guard and the real anchor module."""
    root = ci_contract.require_root("the full-collection guard rig")
    guard = root / GUARD
    assert guard.is_file(), (
        f"source checkout detected at {root} but {guard} is missing. The "
        f"independent full-collection guard is specified; its absence is a "
        f"FAILURE, not a skip."
    )
    rig = tmp_path / name
    (rig / ".claude-plugin").mkdir(parents=True)
    (rig / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
    target = rig / GUARD
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(guard, target)
    if corpus:
        anchor = rig / ANCHOR
        anchor.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / ANCHOR, anchor)
        # The anchor module imports the shared test helper; a rig without it
        # produces a COLLECTION ERROR that masks the behaviour under test.
        shutil.copy2(root / "pact-plugin" / "tests" / "ci_contract.py",
                     anchor.parent / "ci_contract.py")
        _write(rig / "pact-plugin" / "tests" / "test_alpha.py",
               "def test_alpha():\n    assert True\n")
        _write(rig / SKILL_TEST, "def test_skill():\n    assert True\n")
    return rig


def _install_fake_pytest(rig, body):
    """Shadow `python -m pytest` for the guard's child process.

    cwd is the plugin root, so `-m` resolves this module before site-packages.
    It lets a probe control collection output without editing the guard.
    """
    _write(rig / "pact-plugin" / "pytest.py", body)


def _run_guard(rig):
    proc = subprocess.run(
        [sys.executable, str(rig / GUARD)], cwd=str(rig),
        capture_output=True, text=True, timeout=300)
    return proc, proc.stdout + proc.stderr


def test_clean_rig_passes(tmp_path):
    """Non-vacuity floor: the bad-input probes start from a green rig."""
    proc, output = _run_guard(_base_rig(tmp_path))
    assert proc.returncode == 0, (
        f"the guard reddened a clean rig (rc={proc.returncode}):\n{output}")


def test_skill_adjacent_file_is_discovered_and_compared(tmp_path):
    """A module outside tests/ exists on disk but not in collection."""
    rig = _base_rig(tmp_path)
    _install_fake_pytest(rig, FAKE_OMIT_SKILL)
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "SCOPE MISMATCH" in output, output
    assert "test_skill_loading.py" in output, (
        f"the guard did not name the skill-adjacent module:\n{output}")


def test_a_narrowed_collection_is_reported_as_a_set_difference(tmp_path):
    """`--ignore` added to the guard's own collection command is caught."""
    rig = _base_rig(tmp_path)
    guard = rig / GUARD
    text = guard.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(["\']-p["\']\s*,\s*["\']no:cacheprovider["\'])')
    ignored = (rig / SKILL_TEST).resolve()
    mutated, count = pattern.subn(
        lambda match: f'{match.group(1)}, "--ignore={ignored}"', text, count=1)
    assert count == 1, (
        "the guard's collection command no longer contains the pinned "
        "`-p no:cacheprovider` anchor; re-point this mutation at the new "
        f"spelling instead of relaxing it.\n---\n{text}"
    )
    guard.write_text(mutated, encoding="utf-8")
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "SCOPE MISMATCH" in output, output
    assert "test_skill_loading.py" in output, output


def test_testpaths_is_rejected(tmp_path):
    """A config-level narrowing is rejected, not silently obeyed."""
    rig = _base_rig(tmp_path)
    _write(rig / "pact-plugin" / "pytest.ini",
           "[pytest]\ntestpaths = tests\n")
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "testpaths" in output, (
        f"the guard did not report the testpaths entry:\n{output}")


def test_an_empty_corpus_is_a_refusal(tmp_path):
    """An empty discovery is refused, never compared as two empty sets."""
    rig = _base_rig(tmp_path, name="empty", corpus=False)
    proc, output = _run_guard(rig)
    assert proc.returncode == 3, output
    assert "REFUSAL" in output, output


def test_an_absent_summary_is_a_parse_failure_not_a_scope_mismatch(tmp_path):
    """The never-started case is reported separately from a scope difference."""
    rig = _base_rig(tmp_path)
    _install_fake_pytest(rig, FAKE_NO_SUMMARY)
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "PARSE FAILURE" in output, output
    assert "SCOPE MISMATCH" not in output, (
        f"a missing summary was reported as a scope mismatch:\n{output}")


@pytest.mark.parametrize(
    "fake,case_id",
    [(FAKE_DESELECTED, "deselected"), (FAKE_ERRORS, "errors")],
)
def test_a_deselected_or_error_summary_is_rejected(tmp_path, fake, case_id):
    """F3: the token scan is the only catch for a partial deselection.

    A deselection leaves the file collected, so the file-set comparison
    passes; only this scan sees it. Deleting the scan must redden here.
    """
    rig = _base_rig(tmp_path, name=f"scan-{case_id}")
    _install_fake_pytest(rig, fake)
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "COLLECTION FAILURE" in output, output
    assert "reports deselection or errors" in output, output


def test_an_unresolvable_node_id_base_is_refused(tmp_path):
    """A node-ID file part under neither repo root nor plugin root fails."""
    rig = _base_rig(tmp_path)
    _install_fake_pytest(rig, FAKE_UNRESOLVABLE)
    proc, output = _run_guard(rig)
    assert proc.returncode == 2, output
    assert "unresolvable node-ID base" in output, output


class TestRuntimeOptionProbe:
    """The bare-invocation shape the design depends on is observable."""

    def test_a_bare_invocation_reports_the_cwd_and_no_filters(self, tmp_path):
        probe = tmp_path / "bare"
        report, proc = ci_contract.probe_options(probe)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert report is not None
        assert len(report["args"]) == 1
        assert Path(report["args"][0]).resolve() == probe.resolve()
        assert not report["keyword"]
        assert not report["markexpr"]
        assert report["deselect"] == []
        assert report["ignore"] == []

    @pytest.mark.parametrize(
        "args,field",
        [
            (["-k", "option_probe"], "keyword"),
            (["-m", "not slow"], "markexpr"),
            (["--deselect", "test_option_probe.py::test_option_probe"], "deselect"),
            (["--ignore=/definitely/missing"], "ignore"),
        ],
    )
    def test_a_filtered_invocation_is_visible(self, tmp_path, args, field):
        probe = tmp_path / "filtered"
        report, proc = ci_contract.probe_options(probe, args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert report is not None
        assert report[field], (
            f"{field} was not reported for args {args!r}: {report!r}"
        )