"""Static contract for the fast workflow and the full workflow's certification shape.

Two subjects, kept in one place because the guards are the same kind: text
that must be exactly what a reviewer approved. The fast-workflow subject
lives ONLY here; ``test_ci_collection_scope.py`` owns "what is the full
corpus" and must not grow a contradictory second subject.

The removability arm is the independence proof: a rig copy with every
fast-path file deleted must still run the full-collection guard and satisfy
the full-workflow contract. A guard that depends on the lane it guards is
not a guard.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import ci_contract

RUN_FORM = (
    'python ../scripts/run_fast_suite.py --base-sha '
    '"${{ github.event.pull_request.base.sha }}"'
)
COLLECT_FORM = (
    'python ../scripts/run_fast_suite.py --collect-only --base-sha '
    '"${{ github.event.pull_request.base.sha }}"'
)
FAST_JOB = "fast-suite"
FULL_JOB = "full-suite"
FULL_IF = (
    "github.event_name == 'merge_group' || github.event_name == 'push' || "
    "github.event_name == 'workflow_dispatch'"
)
ALLOWED_ACTIONS = (
    "actions/checkout@",
    "actions/setup-python@",
    "actions/cache/restore@",
    "actions/cache/save@",
)
FAST_PATH_FILES = (
    Path("scripts/fast_suite_selection.py"),
    Path("scripts/fast_suite_args.py"),
    Path("scripts/run_fast_suite.py"),
    Path("scripts/verify_fast_summary.py"),
    Path("scripts/fast_suite_exclusions.json"),
    Path(".github/workflows/fast-tests.yml"),
)


def _on_block(doc):
    """The trigger mapping. PyYAML 1.1 reads the bare key `on` as boolean True."""
    for key in ("on", True):
        if key in doc:
            return doc[key]
    raise AssertionError(
        "PARSE FAILURE: the workflow has no `on:` trigger block (PyYAML may "
        "have read it as the boolean key True; neither spelling was found)."
    )


def _fast_doc(root):
    return ci_contract.load_workflow(root, ci_contract.FAST_WORKFLOW)


def _full_doc(root):
    return ci_contract.load_workflow(root, ci_contract.FULL_WORKFLOW)


class TestFastWorkflowShape:
    """The advisory lane: one job, one runner path, two pinned forms."""

    def test_fast_workflow_triggers_on_pull_request_only(self):
        root = ci_contract.require_root("the fast workflow trigger")
        triggers = _on_block(_fast_doc(root))
        assert set(triggers or {}) == {"pull_request"}, (
            f"fast-tests.yml triggers {sorted(triggers or {})}; the advisory "
            f"lane is PR-only by design."
        )
        types = (triggers or {}).get("pull_request")
        if isinstance(types, dict):
            assert set(types) <= {"types"}, (
                f"fast-tests.yml carries a path/branch filter: {sorted(types)}. "
                f"A filter on the advisory lane is not part of the contract."
            )
            assert types.get("types") == ["opened", "synchronize", "reopened"]

    def test_fast_job_is_a_scalar_named_fast_suite(self):
        root = ci_contract.require_root("the fast job name")
        jobs = ci_contract.jobs_of(_fast_doc(root))
        assert FAST_JOB in jobs, f"fast-tests.yml has no {FAST_JOB!r} job: {sorted(jobs)}"
        job = jobs[FAST_JOB]
        assert ci_contract.effective_check_name(FAST_JOB, job) == "fast-suite"
        assert "strategy" not in job, (
            "a matrix renames the check cells; the fast job must stay scalar"
        )

    def test_runner_forms_appear_exactly_once_each(self):
        root = ci_contract.require_root("the pinned runner forms")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        runs = ci_contract.run_texts(steps)
        assert runs.count(RUN_FORM) == 1, (
            f"expected exactly one run form {RUN_FORM!r}; found {runs.count(RUN_FORM)}"
        )
        assert runs.count(COLLECT_FORM) == 1, (
            f"expected exactly one collect-only form {COLLECT_FORM!r}; "
            f"found {runs.count(COLLECT_FORM)}"
        )

    def test_no_run_line_names_pytest(self):
        root = ci_contract.require_root("the fast runner indirection")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        offenders = [run for run in ci_contract.run_texts(steps)
                     if re.search(r"\bpytest\b", run)]
        assert not offenders, (
            f"fast-tests.yml runs pytest directly in {offenders!r}. The "
            f"runner hides the invocation, and a second bare pytest step "
            f"would bypass the canary and the verify bracket."
        )

    def test_lint_is_the_local_gate_and_no_ruff_legs_are_restated(self):
        root = ci_contract.require_root("the fast lint invocation")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        runs = ci_contract.run_texts(steps)
        assert runs.count("scripts/verify-local-lint-gate.sh") == 1, (
            "fast-tests.yml must invoke the local lint gate exactly once"
        )
        offenders = [run for run in runs if "ruff" in run]
        assert not offenders, (
            f"fast-tests.yml restates ruff legs in {offenders!r}; the byte-"
            f"parity contract lives in scripts/verify-local-lint-gate.sh"
        )

    def test_both_runner_steps_are_immediately_preceded_by_the_env_verify(self):
        root = ci_contract.require_root("the fast verify bracket")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        runs = ci_contract.run_texts(steps)
        runner_positions = [index for index, run in enumerate(runs)
                            if run in (RUN_FORM, COLLECT_FORM)]
        assert len(runner_positions) == 2, (
            f"expected two runner steps; found {len(runner_positions)}"
        )
        for position in runner_positions:
            assert position > 0, "the first runner step cannot be the first step"
            assert runs[position - 1] == ci_contract.VERIFY_CMD, (
                f"the runner step at index {position} is preceded by "
                f"{runs[position - 1]!r}, not {ci_contract.VERIFY_CMD!r}"
            )
        assert runs[-1] == ci_contract.VERIFY_CMD, (
            f"the last step is {runs[-1]!r}, not the after-verify"
        )
        assert str(steps[-1].get("if", "")).strip() in ("always()", "${{ always() }}")


class TestFastWorkflowHygiene:
    """No env/shell escape, no narrowed dependency set, no unvetted action."""

    def test_governed_steps_carry_no_env_shell_or_continue_on_error(self):
        root = ci_contract.require_root("the fast governed steps")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        problems = []
        for index, step in enumerate(steps):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            governed = (run in (RUN_FORM, COLLECT_FORM)
                        or run == ci_contract.VERIFY_CMD)
            if not governed:
                continue
            for key in ("env", "shell"):
                if key in step:
                    problems.append(f"step {index} ({run!r}) carries {key}:")
            if step.get("continue-on-error", False) is not False:
                problems.append(
                    f"step {index} ({run!r}) carries continue-on-error")
        assert not problems, "\n".join(problems)

    def test_the_full_constraints_file_is_installed(self):
        root = ci_contract.require_root("the fast dependency install")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        runs = "\n".join(ci_contract.run_texts(steps))
        assert "-r constraints-ci.txt" in runs, (
            "fast-tests.yml does not install constraints-ci.txt in full; "
            "verify_ci_environment.py requires the pins' closure"
        )

    def test_checkout_uses_full_history(self):
        root = ci_contract.require_root("the fast checkout depth")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        checkouts = [step for step in steps
                     if str(step.get("uses", "")).startswith("actions/checkout@")]
        assert len(checkouts) == 1, "expected exactly one checkout step"
        with_block = checkouts[0].get("with") or {}
        assert with_block.get("fetch-depth") == 0, (
            "fetch-depth: 0 is required: git diff <base>...HEAD needs the "
            "merge base, and two history-reading modules skip silently in a "
            "shallow clone"
        )

    def test_actions_are_allowlisted_and_sha_pinned(self):
        root = ci_contract.require_root("the fast action allowlist")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        uses_values = [step["uses"] for step in steps
                       if isinstance(step.get("uses"), str)]
        assert uses_values, "no action steps read"
        problems = []
        for uses in uses_values:
            if not uses.startswith(ALLOWED_ACTIONS):
                problems.append(f"{uses!r} is not on the allowlist")
            match = re.search(r"@([0-9a-f]{40})(?:\s|$)", uses)
            if not match:
                problems.append(f"{uses!r} is not pinned to a full commit SHA")
        assert not problems, "\n".join(problems)


def _assert_full_workflow_contract(root):
    """The full workflow's certification shape. Reused by the removability rig."""
    doc = _full_doc(root)
    triggers = _on_block(doc)
    assert "pull_request" in triggers, (
        "tests.yml lost its pull_request trigger: the required check would "
        "never be reported on a PR and the merge queue would deadlock"
    )
    pull_request = triggers.get("pull_request")
    if isinstance(pull_request, dict):
        forbidden = {"paths", "paths-ignore", "branches", "branches-ignore"}
        assert not (set(pull_request) & forbidden), (
            f"tests.yml carries a workflow-level filter {sorted(set(pull_request) & forbidden)}; "
            f"a skipped workflow emits no check run and the required check stays Pending"
        )
    assert "workflow_dispatch" in triggers, (
        "the documented manual fallback needs workflow_dispatch"
    )
    assert "merge_group" in triggers, "tests.yml has no merge_group trigger"
    merge = triggers.get("merge_group") or {}
    assert merge.get("types") == ["checks_requested"]

    jobs = ci_contract.jobs_of(doc)
    assert FULL_JOB in jobs, f"tests.yml has no {FULL_JOB!r} job: {sorted(jobs)}"
    job = jobs[FULL_JOB]
    assert ci_contract.effective_check_name(FULL_JOB, job) == "full-suite"
    assert "strategy" not in job, "the full job must be scalar"
    first_key = next(iter(job), None)
    assert first_key == "if", (
        f"the job-level `if:` must be the first key under {FULL_JOB!r}; "
        f"found {first_key!r}"
    )
    assert job.get("if") == FULL_IF, (
        f"the job-level `if:` is {job.get('if')!r}, not the pinned positive "
        f"three-event list {FULL_IF!r}"
    )

    steps = ci_contract.steps_of(job)
    runs = ci_contract.run_texts(steps)
    pytest_runs = [run for run in runs if re.search(r"\bpytest\b", run)]
    assert len(pytest_runs) == 1, (
        f"tests.yml must contain exactly one pytest invocation; found "
        f"{len(pytest_runs)}: {pytest_runs!r}"
    )
    assert pytest_runs[0] == ci_contract.TEST_CMD, (
        f"the pytest step is {pytest_runs[0]!r}, not exactly "
        f"{ci_contract.TEST_CMD!r}"
    )
    words = pytest_runs[0].split()
    after_pytest = words[words.index("pytest") + 1:]
    for flag in ("-k", "-m", "--deselect", "--ignore", "--maxfail"):
        assert flag not in after_pytest, (
            f"the full invocation carries a selection flag {flag!r}: {words!r}"
        )
    assert "PYTEST_ADDOPTS" not in ci_contract.read_workflow(
        root, ci_contract.FULL_WORKFLOW), (
        "tests.yml names PYTEST_ADDOPTS; fast-only options must never reach "
        "the unrestricted run"
    )

    lint_lines = [
        line.strip() for run in runs for line in run.splitlines()
        if line.strip().startswith("ruff check")
    ]
    assert len(lint_lines) == 2, (
        f"expected two ruff legs in tests.yml; found {lint_lines!r}"
    )
    assert any("F401,F821" in line for line in lint_lines)
    assert any("--select F821" in line for line in lint_lines)

    collection_guard = [run for run in runs
                        if "verify_full_collection.py" in run]
    assert collection_guard == ["python scripts/verify_full_collection.py"], (
        f"expected one collection-guard step; found {collection_guard!r}"
    )
    assert not re.search(r"\bpytest\b", collection_guard[0]), (
        "the collection guard's run text names pytest; "
        "test_ci_collection_scope permits exactly one such line"
    )

    position = runs.index(ci_contract.TEST_CMD)
    assert runs[position - 1] == ci_contract.VERIFY_CMD, (
        "the full pytest step is not immediately preceded by the env verify"
    )
    assert runs[-1] == ci_contract.VERIFY_CMD, "the after-verify is not the last step"
    assert str(steps[-1].get("if", "")).strip() in ("always()", "${{ always() }}")

    merge_steps = [step for step in steps
                   if step.get("name") == "Assert the merge-group certification subject"]
    assert len(merge_steps) == 1, "the merge-group SHA assertion is missing"
    assert str(merge_steps[0].get("if", "")).strip() == (
        "github.event_name == 'merge_group'")
    assert "MERGE_GROUP_HEAD" in (merge_steps[0].get("env") or {})


class TestFullWorkflowContract:
    """The required lane's trigger, job name, exact command and guards."""

    def test_full_workflow_contract(self):
        root = ci_contract.require_root("the full workflow contract")
        _assert_full_workflow_contract(root)


class TestRemovability:
    """Deleting the fast lane must leave the full gate green."""

    def _rig(self, tmp_path):
        root = ci_contract.require_root("the removability rig")
        rig = tmp_path / "no-fast"
        (rig / ".claude-plugin").mkdir(parents=True)
        (rig / ".claude-plugin" / "marketplace.json").write_text(
            "{}", encoding="utf-8")
        for relative in (
            ci_contract.FULL_WORKFLOW,
            Path("scripts") / "verify_full_collection.py",
            Path("pact-plugin") / "tests" / "test_ci_collection_scope.py",
        ):
            target = rig / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, target)
        smoke = rig / "pact-plugin" / "tests" / "test_smoke.py"
        smoke.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
        for relative in FAST_PATH_FILES:
            assert not (rig / relative).exists(), (
                f"the rig inherited a fast-path file: {relative}"
            )
        return rig

    def test_full_contract_and_collection_guard_survive_removal(self, tmp_path):
        rig = self._rig(tmp_path)
        _assert_full_workflow_contract(rig)
        guard = rig / "scripts" / "verify_full_collection.py"
        text = guard.read_text(encoding="utf-8")
        for token in ("fast_suite", "run_fast_suite", "fast-tests",
                      "fast_suite_exclusions", "verify_fast_summary"):
            assert token not in text, (
                f"verify_full_collection.py names the fast-path token {token!r}; "
                f"the full guard must remain independent of the lane it guards"
            )
        proc = subprocess.run(
            [sys.executable, str(guard)], cwd=str(rig),
            capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, (
            f"the full-collection guard failed in a rig with no fast files "
            f"(rc={proc.returncode}).\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )