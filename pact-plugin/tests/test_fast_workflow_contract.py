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

import ast
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import ci_contract
import ci_full_run_record

# The run record's file name comes from the recorder that writes it, so a
# rename there that misses the workflow fails the step pins below.
_RUN_RECORD = ci_full_run_record.RECORD_NAME
_ARTIFACT_ARGS = (
    '--summary "$GITHUB_WORKSPACE/.fast-suite-summary.json" '
    '--canary "$GITHUB_WORKSPACE/.fast-suite-canary.json"'
)
RUN_FORM = (
    'python ../scripts/run_fast_suite.py --base-sha "$BASE_SHA" '
    + _ARTIFACT_ARGS
)
COLLECT_FORM = (
    'python ../scripts/run_fast_suite.py --collect-only --base-sha "$BASE_SHA" '
    + _ARTIFACT_ARGS
)
FAST_EVIDENCE_CMD = (
    'python scripts/verify_fast_summary.py --base-sha "$BASE_SHA" '
    + _ARTIFACT_ARGS
    + f' --run-record "$GITHUB_WORKSPACE/{_RUN_RECORD}"'
)
FULL_COLLECTION_CMD = (
    'python scripts/verify_full_collection.py '
    '--record "$GITHUB_WORKSPACE/.full-collection-record.json"'
)
FULL_EVIDENCE_CMD = (
    'python scripts/verify_full_run.py '
    f'--record "$GITHUB_WORKSPACE/{_RUN_RECORD}" '
    '--collection-record "$GITHUB_WORKSPACE/.full-collection-record.json"'
)
FAST_RUNNER_PREFIX = "python ../scripts/run_fast_suite.py"
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
    "actions/upload-artifact@",
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


def _assert_step_cannot_be_silenced(steps, run_text, where):
    """The ONE step running `run_text` carries neither `if:` nor
    `continue-on-error`.

    Indexed over `steps`, never over `run_texts(steps)`: that list drops
    every `uses:` step, so a position taken from it addresses a different
    step. Any `if:` key fails, `success()` included, and so does any
    `continue-on-error` value. A step whose text drifted matches nothing and
    fails here by name rather than passing because no step was checked.
    """
    matches = [step for step in steps
               if isinstance(step.get("run"), str)
               and step["run"].strip() == run_text]
    assert len(matches) == 1, (
        f"{where}: expected exactly one step running {run_text!r}; found "
        f"{len(matches)}. A changed run text must be re-pinned here, not "
        f"left unchecked.")
    for key in ("if", "continue-on-error"):
        assert key not in matches[0], (
            f"{where}: the step running {run_text!r} carries `{key}:` "
            f"({matches[0][key]!r}). An evidence step that can be skipped or "
            f"made non-fatal certifies nothing.")


def _required_flags(root, script):
    """Every option `scripts/<script>` declares with `required=True`.

    Read from the script's own argparse calls, so a flag the script gains
    is pinned here without editing this file.
    """
    source = (root / "scripts" / script).read_text(encoding="utf-8")
    flags = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        required = any(
            keyword.arg == "required"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords)
        if required:
            flags.update(
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.startswith("--"))
    return frozenset(flags)


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
        pulls = (triggers or {}).get("pull_request")
        assert isinstance(pulls, dict), (
            "fast-tests.yml declares a BARE `pull_request:`; the event-type "
            "list is part of the contract and must be explicit (a removed "
            "types: key must redden here)."
        )
        assert set(pulls) <= {"types"}, (
            f"fast-tests.yml carries a path/branch filter: {sorted(pulls)}. "
            f"A filter on the advisory lane is not part of the contract."
        )
        assert pulls.get("types") == ["opened", "synchronize", "reopened"], (
            f"fast-tests.yml pull_request types are {pulls.get('types')!r}, "
            f"not ['opened', 'synchronize', 'reopened']"
        )

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

    def test_every_artifact_step_passes_every_required_flag(self):
        """The two runner steps and the evidence step each pass every option
        their script declares required, so a dropped artifact path fails here
        by name rather than at run time on a pull request."""
        root = ci_contract.require_root("the artifact steps' required flags")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        runs = [run.strip() for run in ci_contract.run_texts(steps)]
        problems = []
        for script, prefix, expected_steps in (
                ("run_fast_suite.py", FAST_RUNNER_PREFIX, 2),
                ("verify_fast_summary.py",
                 "python scripts/verify_fast_summary.py", 1)):
            required = _required_flags(root, script)
            assert required, (
                f"PARSE FAILURE: no `required=True` option was found in "
                f"scripts/{script}, so this arm would check nothing.")
            matched = [run for run in runs if run.startswith(prefix)]
            assert len(matched) == expected_steps, (
                f"expected {expected_steps} step(s) running {prefix!r}; "
                f"found {matched!r}")
            for run in matched:
                passed = {token.split("=", 1)[0] for token in shlex.split(run)
                          if token.startswith("--")}
                missing = sorted(required - passed)
                if missing:
                    problems.append(f"{run!r} does not pass {missing}")
        assert not problems, (
            "a fast-workflow step omits a flag its script requires:\n  "
            + "\n  ".join(problems))

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

    def test_the_evidence_step_cannot_be_silenced(self):
        root = ci_contract.require_root("the fast evidence step")
        steps = ci_contract.steps_of(
            ci_contract.jobs_of(_fast_doc(root))[FAST_JOB])
        _assert_step_cannot_be_silenced(
            steps, FAST_EVIDENCE_CMD, "fast-tests.yml evidence step")

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
        """Both workflows, not only the fast one.

        This catches drift and honest mistakes. It is NOT an adversarial
        barrier: a PR can edit this pin and the workflow in the same commit,
        so review remains the real control.
        """
        root = ci_contract.require_root("the action allowlist")
        problems = []
        read_any = False
        for relative in (ci_contract.FAST_WORKFLOW, ci_contract.FULL_WORKFLOW):
            doc = ci_contract.load_workflow(root, relative)
            for job in ci_contract.jobs_of(doc).values():
                for step in ci_contract.steps_of(job):
                    uses = step.get("uses")
                    if not isinstance(uses, str):
                        continue
                    read_any = True
                    if not uses.startswith(ALLOWED_ACTIONS):
                        problems.append(f"{relative}: {uses!r} is not allowlisted")
                    if not re.search(r"@[0-9a-f]{40}(?:\s|$)", uses):
                        problems.append(
                            f"{relative}: {uses!r} is not pinned to a full "
                            f"commit SHA")
        assert read_any, "no action steps read from either workflow"
        assert not problems, "\n".join(problems)


class TestWorkflowHardening:
    """Minimal permissions and no pull_request_target.

    A drift guard, not an adversarial barrier: the reviewed PR can edit these
    pins alongside the workflows.
    """

    def test_workflow_permissions_are_minimal(self):
        root = ci_contract.require_root("the workflow permissions")
        for relative in (ci_contract.FAST_WORKFLOW, ci_contract.FULL_WORKFLOW):
            doc = ci_contract.load_workflow(root, relative)
            assert doc.get("permissions") == {"contents": "read"}, (
                f"{relative} declares permissions {doc.get('permissions')!r}; "
                f"both workflows must declare exactly {{'contents': 'read'}}"
            )

    def test_pull_request_target_appears_nowhere(self):
        root = ci_contract.require_root("the workflow event names")
        for relative in (ci_contract.FAST_WORKFLOW, ci_contract.FULL_WORKFLOW):
            text = ci_contract.read_workflow(root, relative)
            assert "pull_request_target" not in text, (
                f"{relative} names pull_request_target; that event runs with "
                f"write permissions for fork PRs and is not part of this design"
            )


def _assert_full_workflow_contract(root):
    """The full workflow's certification shape. Reused by the removability rig."""
    doc = _full_doc(root)
    triggers = _on_block(doc)
    assert "pull_request" in triggers, (
        "tests.yml lost its pull_request trigger: the required check would "
        "never be reported on a PR and the merge queue would deadlock"
    )
    pull_request = triggers.get("pull_request")
    assert isinstance(pull_request, dict), (
        "tests.yml declares a BARE `pull_request:`; the event-type list is "
        "part of the contract, and a removed types: key must redden here."
    )
    forbidden = {"paths", "paths-ignore", "branches", "branches-ignore"}
    assert not (set(pull_request) & forbidden), (
        f"tests.yml carries a workflow-level filter {sorted(set(pull_request) & forbidden)}; "
        f"a skipped workflow emits no check run and the required check stays Pending"
    )
    assert pull_request.get("types") == ["opened", "synchronize", "reopened"], (
        f"tests.yml pull_request types are {pull_request.get('types')!r}, "
        f"not ['opened', 'synchronize', 'reopened']"
    )
    dispatch = triggers.get("workflow_dispatch")
    assert isinstance(dispatch, dict), (
        "the documented manual fallback needs workflow_dispatch with its "
        "constrained dist input"
    )
    inputs = dispatch.get("inputs")
    assert isinstance(inputs, dict) and set(inputs) == {"dist"}, (
        f"workflow_dispatch inputs are {inputs!r}; only the constrained dist "
        "choice is admitted, never arbitrary pytest arguments"
    )
    dist = inputs["dist"]
    assert dist.get("required") is False
    assert dist.get("default") == "loadfile"
    assert dist.get("type") == "choice"
    assert dist.get("options") == ["loadfile", "load"], (
        f"dist options are {dist.get('options')!r}; loadfile stays the default "
        "and load is the only diagnostic alternative"
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
    assert pytest_runs[0] == ci_contract.WORKFLOW_TEST_CMD, (
        f"the pytest step is {pytest_runs[0]!r}, not exactly "
        f"{ci_contract.WORKFLOW_TEST_CMD!r}"
    )
    words = shlex.split(pytest_runs[0])
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
    assert collection_guard == [FULL_COLLECTION_CMD], (
        f"expected one collection-guard step; found {collection_guard!r}"
    )
    _assert_step_cannot_be_silenced(
        steps, FULL_COLLECTION_CMD, "tests.yml collection guard")
    assert not re.search(r"\bpytest\b", collection_guard[0]), (
        "the collection guard's run text names pytest; "
        "test_ci_collection_scope permits exactly one such line"
    )

    position = runs.index(ci_contract.WORKFLOW_TEST_CMD)
    assert runs[position - 1] == ci_contract.VERIFY_CMD, (
        "the full pytest step is not immediately preceded by the env verify"
    )
    assert runs[-1] == ci_contract.VERIFY_CMD, "the after-verify is not the last step"
    assert str(steps[-1].get("if", "")).strip() in ("always()", "${{ always() }}")

    # The certifying run's own evidence step. It must be the step
    # immediately after the test step, must not name pytest (the one-pytest
    # rule), and must not be silenceable.
    evidence_cmd = FULL_EVIDENCE_CMD
    assert (root / "scripts" / "verify_full_run.py").is_file(), (
        "scripts/verify_full_run.py is missing; the evidence step cannot run")
    assert position + 1 < len(runs), "no step follows the test step"
    assert runs[position + 1] == evidence_cmd, (
        f"the step after the test step is {runs[position + 1]!r}, not the "
        f"run-evidence verifier {evidence_cmd!r}")
    assert "pytest" not in runs[position + 1], (
        "the evidence step's run text names pytest; test_ci_collection_scope "
        "permits exactly one pytest invocation in this file")
    # `runs` DROPS every step without a `run:` key (all `uses:` steps), so a
    # position derived from `runs` does NOT address the same step in
    # `steps`. Reusing it would pin continue-on-error on an unrelated step
    # and let a silenced evidence step pass. Index `steps` independently,
    # then assert the two indices agree so the coupling is explicit.
    evidence_indices = [
        index for index, step in enumerate(steps)
        if step.get("run") == evidence_cmd
    ]
    assert len(evidence_indices) == 1, (
        f"expected exactly one step running {evidence_cmd!r}; found "
        f"{len(evidence_indices)}")
    evidence_index = evidence_indices[0]
    step_run_positions = [
        index for index, step in enumerate(steps)
        if isinstance(step.get("run"), str)
    ]
    assert step_run_positions[position + 1] == evidence_index, (
        "the runs list and the steps list disagree about which step follows "
        "the test step; the continue-on-error pin would check the wrong step")
    _assert_step_cannot_be_silenced(
        steps, evidence_cmd, "tests.yml run-evidence step")

    uploads = [
        (index, step) for index, step in enumerate(steps)
        if step.get("name") == "Upload full-suite diagnostic evidence"
    ]
    assert len(uploads) == 1, (
        f"expected exactly one diagnostic evidence upload; found {len(uploads)}"
    )
    upload_index, upload = uploads[0]
    assert upload.get("uses") == ci_contract.UPLOAD_ARTIFACT_ACTION, (
        f"diagnostic upload uses {upload.get('uses')!r}, not the reviewed "
        f"full-SHA action {ci_contract.UPLOAD_ARTIFACT_ACTION!r}"
    )
    assert str(upload.get("if", "")).strip() == (
        "always() && github.event_name == 'workflow_dispatch'"), (
        f"diagnostic upload condition is {upload.get('if')!r}; it must run "
        "after either test outcome but only for workflow_dispatch"
    )
    upload_with = upload.get("with") or {}
    assert upload_with.get("name") == (
        "full-suite-evidence-${{ github.run_id }}-${{ github.run_attempt }}")
    assert (upload_with.get("path") or "").splitlines() == [
        ".full-suite-results.xml",
        ".full-run-record.json",
        ".full-collection-record.json",
    ], f"diagnostic upload paths are {upload_with.get('path')!r}"
    assert upload_with.get("if-no-files-found") == "error"
    assert upload_with.get("include-hidden-files") is True, (
        "all three evidence files are dotfiles; omitting hidden files would "
        "silently upload no usable diagnostic evidence"
    )
    assert upload_index == evidence_index + 1 == len(steps) - 2, (
        f"diagnostic upload index {upload_index}, run evidence index "
        f"{evidence_index}, final step index {len(steps) - 1}: upload must sit "
        "immediately between run verification and the final environment check"
    )

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
    """Deleting the fast lane must leave the full gate green — EXECUTED.

    Copying a test is not running it. The two fast-dependent arms are
    executed in the rig and must SKIP loudly there; a silent skip, a pass, or
    a failure all redden this arm.
    """

    _FAST_DEPENDENT_NODE_IDS = (
        "tests/test_memory_init.py::TestDependencyDriftIsDetectable::"
        "test_the_fast_lane_is_present_or_wholly_absent",
        "tests/test_ci_collection_scope.py::"
        "TestTheFastManifestNamesRealCorpusFiles::"
        "test_manifest_paths_exist_and_appear_in_the_discovered_corpus",
    )

    def _rig(self, tmp_path):
        root = ci_contract.require_root("the removability rig")
        rig = tmp_path / "no-fast"
        (rig / ".claude-plugin").mkdir(parents=True)
        (rig / ".claude-plugin" / "marketplace.json").write_text(
            "{}", encoding="utf-8")
        for relative in (
            ci_contract.FULL_WORKFLOW,
            Path("scripts") / "verify_full_collection.py",
            Path("scripts") / "verify_full_run.py",
            Path("pact-plugin") / "tests" / "test_ci_collection_scope.py",
            Path("pact-plugin") / "tests" / "test_memory_init.py",
            Path("pact-plugin") / "tests" / "ci_contract.py",
        ):
            target = rig / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, target)
        smoke = rig / "pact-plugin" / "tests" / "test_smoke.py"
        smoke.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
        # test_memory_init.py carries an autouse fixture that imports
        # `scripts.memory_init` (which resolves through the skill scripts dir
        # in the real tree). The executed arm does not use it; this stub keeps
        # the harness importable without dragging the memory stack into the
        # rig.
        stub_pkg = rig / "pact-plugin" / "scripts"
        stub_pkg.mkdir(parents=True, exist_ok=True)
        (stub_pkg / "__init__.py").write_text("", encoding="utf-8")
        (stub_pkg / "memory_init.py").write_text(
            "def reset_initialization():\n"
            "    \"\"\"Rig stub: the removability arm does not exercise it.\"\"\"\n"
            "    return None\n", encoding="utf-8")
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
        # EXECUTE the fast-dependent arms; they must skip loudly.
        arms = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rA",
             "-p", "no:cacheprovider", *self._FAST_DEPENDENT_NODE_IDS],
            cwd=str(rig / "pact-plugin"), capture_output=True, text=True,
            timeout=300)
        assert arms.returncode == 0, (
            f"the fast-dependent arms did not skip cleanly in a rig with no "
            f"fast lane (rc={arms.returncode}).\nstdout:\n{arms.stdout}\n"
            f"stderr:\n{arms.stderr}")
        assert "2 skipped" in arms.stdout, (
            f"expected both fast-dependent arms to SKIP; output:\n{arms.stdout}")
        assert "WHOLE fast lane is absent" in arms.stdout, (
            f"the skip reason does not name the missing lane; output:\n"
            f"{arms.stdout}")
        assert "FAILED" not in arms.stdout and "ERROR" not in arms.stdout

    def test_a_partially_deleted_fast_lane_fails(self, tmp_path):
        """One side present without the other is a failure, not a skip."""
        rig = self._rig(tmp_path)
        partial = rig / ".github/workflows/fast-tests.yml"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(
            "name: fast-tests\non: [pull_request]\n", encoding="utf-8")
        arms = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rA",
             "-p", "no:cacheprovider", *self._FAST_DEPENDENT_NODE_IDS],
            cwd=str(rig / "pact-plugin"), capture_output=True, text=True,
            timeout=300)
        assert arms.returncode != 0, (
            f"a partially deleted fast lane did not fail; output:\n"
            f"{arms.stdout}")
        combined = arms.stdout + arms.stderr
        assert "FAST LANE PARTIALLY PRESENT" in combined, combined