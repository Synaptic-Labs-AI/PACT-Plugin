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
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import ci_contract
import ci_full_run_record
from helpers import env_without_pytest_vars


def _child_environ():
    """This process's environment without any PYTEST_* name, so the pytest a
    rig starts (directly, or through the full-collection guard) takes no
    PYTEST_ADDOPTS, PYTEST_PLUGINS or PYTEST_DISABLE_PLUGIN_AUTOLOAD from the
    shell that runs the suite."""
    return env_without_pytest_vars()


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
# PER WORKFLOW. The full suite may upload diagnostic evidence; the fast lane
# may not, and the split is what makes an upload step added there redden
# instead of riding the full-suite entry.
_SHARED_ACTIONS = (
    "actions/checkout@",
    "actions/setup-python@",
    "actions/cache/restore@",
    "actions/cache/save@",
)
ALLOWED_ACTIONS_BY_WORKFLOW = {
    ci_contract.FAST_WORKFLOW: _SHARED_ACTIONS,
    ci_contract.FULL_WORKFLOW: _SHARED_ACTIONS + ("actions/upload-artifact@",),
}


def _action_allowlist_problems(doc, relative, problems: list[str]) -> bool:
    """Append this workflow's action problems; True when it had any step to read."""
    allowed = ALLOWED_ACTIONS_BY_WORKFLOW[relative]
    read_any = False
    for job in ci_contract.jobs_of(doc).values():
        for step in ci_contract.steps_of(job):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            read_any = True
            if not uses.startswith(allowed):
                problems.append(f"{relative}: {uses!r} is not allowlisted")
            if not re.search(r"@[0-9a-f]{40}(?:\s|$)", uses):
                problems.append(
                    f"{relative}: {uses!r} is not pinned to a full commit SHA")
    return read_any
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


def _render_lint_gate_remedy(root, scratch, venv, target="gone", readlink=True):
    """The stderr the lint gate prints when ruff is missing, run from a scratch
    root that holds a copy of the script and the lock and whose .venv is absent
    ("absent"), a venv directory ("existing") or a link whose text is target,
    as given, to a path that does not exist ("dangling"). PATH holds only
    dirname and, unless readlink is false, readlink, so no ruff resolves."""
    scratch.mkdir()
    (scratch / "scripts").mkdir()
    (scratch / "pact-plugin").mkdir()
    shutil.copy(root / "scripts" / "verify-local-lint-gate.sh", scratch / "scripts")
    shutil.copy(root / "constraints-ci.txt", scratch)
    if venv == "existing":
        (scratch / ".venv" / "bin").mkdir(parents=True)
    elif venv == "dangling":
        (scratch / ".venv").symlink_to(target)
        assert not (scratch / ".venv").exists(), target
    bin_dir = scratch / "bin"
    bin_dir.mkdir()
    for tool in ("dirname", "readlink") if readlink else ("dirname",):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    proc = subprocess.run(
        [shutil.which("bash"), str(scratch / "scripts" / "verify-local-lint-gate.sh")],
        env={"PATH": str(bin_dir), "HOME": str(scratch)},
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    return proc.stderr


def _lint_gate_remedy_lines(root, scratch, venv="absent"):
    """The lines the lint gate prints when ruff is missing, stripped, for one
    .venv state (_render_lint_gate_remedy)."""
    return [line.strip() for line in _render_lint_gate_remedy(root, scratch, venv).splitlines()]


def _remedy_block(printed):
    """The remedy's command lines as printed: the lines indented as commands
    before the CI legs."""
    lines = printed.splitlines()
    end = next(i for i, line in enumerate(lines) if line.startswith("  To lint without this gate"))
    return [line for line in lines[:end] if line.startswith("    ")]


def _remedy_steps(block):
    """The steps of the remedy's one command, its continuation lines joined and
    split at each &&."""
    return " ".join(line.strip().removesuffix("\\").strip() for line in block).split(" && ")


def _remedy_floor_minor(check):
    """The minor version of the floor the remedy's printed check enforces."""
    found = re.findall(r"sys\.version_info >= \(3, (\d+)\)", check)
    assert len(found) == 1, check
    return int(found[0])


def _install_floor_minor(root):
    text = (root / ".cursor" / "install.sh").read_text(encoding="utf-8")
    found = re.findall(r"(?m)^PYTHON_FLOOR_MINOR=(\d+)$", text)
    assert len(found) == 1, found
    return int(found[0])


def _declared_floor_minor(root):
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    found = re.fullmatch(r">=3\.(\d+)", declared["project"]["requires-python"])
    assert found, declared["project"]["requires-python"]
    return int(found.group(1))


# A number standing alone in prose (`40`, `1.2.3`), not one inside a word
# (`py312`, `F401`, `python3`).
_STANDALONE_NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)*(?![\w.-])")


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
            read_any = read_any or _action_allowlist_problems(
                doc, relative, problems)
        assert read_any, "no action steps read from either workflow"
        assert not problems, "\n".join(problems)

    def test_the_fast_lane_may_not_upload_artifacts(self):
        """The per-workflow split: an upload step added to the fast lane must
        redden. It is admitted only in the full-suite workflow."""
        root = ci_contract.require_root("the fast-lane action allowlist")
        doc = ci_contract.load_workflow(root, ci_contract.FAST_WORKFLOW)
        job = next(iter(ci_contract.jobs_of(doc).values()))
        job.setdefault("steps", []).append(
            {"uses": ci_contract.UPLOAD_ARTIFACT_ACTION})
        problems: list[str] = []
        assert _action_allowlist_problems(
            doc, ci_contract.FAST_WORKFLOW, problems)
        assert any("upload-artifact" in problem for problem in problems), (
            f"an upload step in the fast lane did not redden: {problems}")


# The exact install line at each site. CI refuses a source build; the local
# install accepts one, because pysqlite3 has no aarch64 Linux or x86_64 macOS
# wheel, and pip then installs that build's own dependencies without hashes.
_CI_LOCK_INSTALL = (
    "python -m pip install --only-binary=:all: --require-hashes -r constraints-ci.txt")
_LOCAL_LOCK_INSTALL = "python -m pip install --require-hashes -r constraints-ci.txt"


class TestHashCheckedInstall:
    """Every install of the CI set is the one hash-checked lock install for its site."""

    @staticmethod
    def _install_lines(text):
        return [line.strip() for line in text.splitlines()
                if "pip install" in line and not line.strip().startswith(("#", "printf"))]

    @pytest.mark.parametrize("source, expected", [
        ("tests.yml", _CI_LOCK_INSTALL),
        ("fast-tests.yml", _CI_LOCK_INSTALL),
        (".cursor/install.sh", _LOCAL_LOCK_INSTALL),
    ])
    def test_the_only_install_is_the_hash_checked_lock(self, source, expected):
        root = ci_contract.require_root("the hash-checked install")
        if source == ".cursor/install.sh":
            text = (root / source).read_text(encoding="utf-8")
        else:
            doc = ci_contract.load_workflow(root, f".github/workflows/{source}")
            text = "\n".join(
                run for job in ci_contract.jobs_of(doc).values()
                for run in ci_contract.run_texts(ci_contract.steps_of(job)))
        assert self._install_lines(text) == [expected], (
            f"{source} installs {self._install_lines(text)!r}; this site installs "
            f"the CI set only by `{expected}`, which moves pip to its pinned "
            "version too. A `-c constraints-ci.txt` install fails under hashes, "
            "dropping --require-hashes lets a lock without hashes install "
            "unchecked, and in CI dropping --only-binary=:all: lets a source "
            "build install its build dependencies unchecked.")

    # Ruff's one-entry lock goes to a temporary file, never into .venv: a
    # worktree's .venv is a link to the main checkout's shared venv.
    _REMEDY_LOCK = '"$lock"'
    _REMEDY_INSTALL = (".venv/bin/python -m pip install --only-binary=:all: --require-hashes "
                       '--no-deps -r "$lock"')

    @pytest.mark.parametrize("venv", ["absent", "existing"])
    def test_the_lint_gate_remedy_installs_ruff_alone(self, tmp_path, venv):
        """The remedy runs on a developer's machine and the gate needs ruff only,
        so it refuses an interpreter below the floor, copies ruff's own entry out
        of the lock into a temporary file, installs that entry from a wheel with
        its hashes and nothing else, removes the file, and activates the venv.

        With no .venv it creates one with python3. With a .venv it never runs
        `python3 -m venv`, which would re-initialise the venv with whatever
        python3 is first on PATH, and it checks the floor with the venv's own
        interpreter. The full environment, which compiles a
        package on some platforms, stays .cursor/install.sh's. The steps are one
        command, each continuation line starting with &&, so a failed step stops
        the rest when the block is pasted."""
        root = ci_contract.require_root("the lint gate remedy")
        rendered = _render_lint_gate_remedy(root, tmp_path / "root", venv)
        block = _remedy_block(rendered)
        printed = [line.strip() for line in rendered.splitlines()]
        assert all(line.endswith(" \\") for line in block[:-1]), block
        assert not block[-1].endswith("\\"), block
        assert all(line.strip().startswith("&& ") for line in block[1:]), block
        steps = _remedy_steps(block)
        commands = [line.strip() for line in block]
        cursor = [line.strip() for line in
                  (root / ".cursor" / "install.sh").read_text(encoding="utf-8").splitlines()]
        check = steps[0]
        if venv == "absent":
            create, temp, extraction, *rest = steps[1:]
            assert check.startswith("python3 -c "), check
            assert create == "python3 -m venv .venv", steps
            assert create in cursor, "the remedy's venv line is not .cursor/install.sh's"
            assert "repository venv (.venv), install ruff alone" in " ".join(printed)
        else:
            temp, extraction, *rest = steps[1:]
            assert check.startswith(".venv/bin/python -c "), check
            assert not any("-m venv" in line for line in printed), printed
            assert "venv (.venv) exists" in " ".join(printed), printed
        assert temp == 'lock="$(mktemp)"', steps
        extract, _, target = extraction.partition(" > ")
        assert [target, *rest] == [
            self._REMEDY_LOCK, self._REMEDY_INSTALL, 'rm "$lock"', ". .venv/bin/activate"], steps
        assert not any(".venv/ruff-lock" in step for step in steps), steps
        assert extract.startswith("sed "), extract
        assert steps[-1] in cursor, "the remedy's activate line is not .cursor/install.sh's"
        # The floor check is a third copy of the floor; it must equal the other two.
        assert _remedy_floor_minor(check) == _install_floor_minor(root) \
            == _declared_floor_minor(root), check
        # Python drops an assert under -O or PYTHONOPTIMIZE, so the check exits instead.
        assert "assert" not in check and "sys.exit(" in check, check
        prose = " ".join(line for line in printed
                         if line not in commands and not line.startswith("ruff "))
        for phrase in ("repository venv (.venv)", "ruff alone",
                       "from a wheel only", ".cursor/install.sh", "for a POSIX shell",
                       "On Windows", ".venv\\Scripts\\python"):
            assert phrase in prose, phrase
        # A version or count here would go stale on a lock refresh; the printed
        # commands carry none either, since the version comes from the lock.
        assert not _STANDALONE_NUMBER.search(prose), prose
        script = (root / "scripts" / "verify-local-lint-gate.sh").read_text(encoding="utf-8")
        assert "-c constraints-ci.txt" not in script

    @pytest.mark.parametrize("target, readlink", [
        ("gone", True), ("gone%s\\n", True), ("gone", False),
    ], ids=["plain", "percent-and-backslash", "no-readlink"])
    def test_the_lint_gate_remedy_names_a_broken_venv_link(self, tmp_path, target, readlink):
        """A .venv link to nothing still counts as a venv, so nothing is created
        over it, and no command could install into it, so the remedy prints none.
        It names the link's target as a printf argument, so a % or backslash in
        the path prints as itself, says so when the target cannot be read, and
        gives the two ways out: restore that venv, or remove the link and run the
        gate again for the commands that create one."""
        root = ci_contract.require_root("the lint gate remedy")
        scratch = tmp_path / "root"
        printed = [line.strip() for line in _render_lint_gate_remedy(
            root, scratch, "dangling", target, readlink).splitlines()]
        text = " ".join(printed)
        assert "venv (.venv) is a link to a directory that does not exist" in text, printed
        assert "Restore the venv it points at" in text, printed
        named = (f".venv -> {target}" if readlink
                 else ".venv -> (the link target could not be read)")
        assert named in printed, printed
        assert "rm .venv" in printed, printed
        assert "venv (.venv) exists" not in text, printed
        assert not [line for line in printed
                    if line.startswith(("python3 ", ".venv/", ". ", "sed "))
                    or "pip install" in line or "-m venv" in line], printed

    def test_the_remedy_extracts_exactly_the_locks_ruff_entry(self, tmp_path):
        """The printed extraction, run as printed against the real lock, yields
        ruff's entry with every hash the lock lists for it and no other
        requirement, so the remedy installs CI's ruff and cannot drift from it."""
        root = ci_contract.require_root("the lint gate remedy")
        steps = _remedy_steps(_remedy_block(_render_lint_gate_remedy(root, tmp_path / "root",
                                                                     "absent")))
        extract = next(step for step in steps if step.startswith("sed ")).partition(" > ")[0]
        proc = subprocess.run(shlex.split(extract), cwd=root, capture_output=True,
                              text=True, timeout=30)
        assert proc.returncode == 0, proc
        lines = (root / "constraints-ci.txt").read_text(encoding="utf-8").splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith("ruff=="))
        last = next(i for i in range(first, len(lines)) if not lines[i].endswith("\\"))
        assert proc.stdout.splitlines() == lines[first:last + 1], proc.stdout
        requirements = [line for line in proc.stdout.splitlines() if not line.startswith(" ")]
        assert len(requirements) == 1 and requirements[0].startswith("ruff=="), requirements
        assert proc.stdout.count("--hash=sha256:") == last - first, proc.stdout

    @pytest.mark.parametrize("prose, has_number", [
        ("every locked package, 40 of them", True),
        ("all 40 locked packages", True),
        ("every locked package (40)", True),
        ("pinned at 1.2.3 in the lock", True),
        ("40", True),
        ("ruff check --select F401,F821 --target-version py312", False),
        ("python3 -m venv .venv", False),
        ("pact-plugin/ scripts/ constraints-ci.txt", False),
    ])
    def test_the_remedy_number_check_finds_a_standalone_number(self, prose, has_number):
        assert bool(_STANDALONE_NUMBER.search(prose)) is has_number, prose

    @pytest.mark.parametrize("venv", ["absent", "existing"])
    @pytest.mark.parametrize("below_floor", [False, True], ids=["at-floor", "below-floor"])
    def test_the_remedy_floor_check_refuses_under_pythonoptimize(self, tmp_path, below_floor,
                                                                   venv):
        """The printed floor check, run as printed under PYTHONOPTIMIZE=1, passes
        this interpreter and refuses one below the floor, naming its version. A
        floor above every real interpreter stands in for a below-floor python3."""
        root = ci_contract.require_root("the lint gate remedy")
        check = _remedy_steps(_remedy_block(_render_lint_gate_remedy(root, tmp_path / "root",
                                                                     venv)))[0]
        assert check.startswith(("python3 -c ", ".venv/bin/python -c ")), check
        code = shlex.split(check)[2]
        if below_floor:
            code = re.sub(r"\(3, \d+\)", "(99, 0)", code)
            assert "(99, 0)" in code, code
        proc = subprocess.run([sys.executable, "-c", code],
                              env=dict(os.environ, PYTHONOPTIMIZE="1"),
                              capture_output=True, text=True, timeout=30)
        if below_floor:
            assert proc.returncode == 1 and proc.stderr.strip() == sys.version, proc
        else:
            assert proc.returncode == 0 and proc.stderr == "", proc

    # An interpreter for the pasted-remedy rig: it logs each call, hands a -c
    # program to the real interpreter, makes a venv holding a copy of itself
    # and an activate that logs, and records a pip run, with the requirements
    # file it was given, without installing.
    _REMEDY_STUB = '''\
import os, shutil, sys
args = sys.argv[1:]
with open(os.environ["REMEDY_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\\n")
if args[:1] == ["-c"]:
    os.execv(os.environ["REMEDY_REAL_PYTHON"], [os.environ["REMEDY_REAL_PYTHON"], *args])
if args[:2] == ["-m", "venv"]:
    os.makedirs(os.path.join(args[2], "bin"))
    shutil.copy(__file__, os.path.join(args[2], "bin", "python"))
    with open(os.path.join(args[2], "bin", "activate"), "w", encoding="utf-8") as act:
        act.write('echo activate >> "$REMEDY_LOG"\\n')
    sys.exit(0)
if args[:2] == ["-m", "pip"]:
    shutil.copy(args[args.index("-r") + 1], os.environ["REMEDY_PIP_READ"])
    sys.exit(0)
sys.exit(2)
'''

    def _remedy_stub(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{self._REMEDY_STUB}", encoding="utf-8")
        path.chmod(0o755)

    @pytest.mark.parametrize("venv", ["absent", "existing"])
    @pytest.mark.parametrize("below_floor", [False, True], ids=["at-floor", "below-floor"])
    def test_the_pasted_remedy_installs_only_past_the_floor(self, tmp_path, venv, below_floor):
        """The printed remedy, pasted as one block into bash, runs its real floor
        check through a real interpreter. Below the floor (the check's floor
        raised past every interpreter) nothing after it runs: no venv is made,
        nothing is written and pip is never started. At the floor every step
        runs: pip reads ruff's own hashed lock entry from a temporary file, the
        file is removed, and nothing is left in .venv, which in a worktree is
        the main checkout's shared venv."""
        root = ci_contract.require_root("the lint gate remedy")
        block = "\n".join(_remedy_block(_render_lint_gate_remedy(root, tmp_path / "render", venv)))
        if below_floor:
            block = re.sub(r"\(3, \d+\)", "(99, 0)", block)
            assert "(99, 0)" in block, block
        rig = tmp_path / "rig"
        rig.mkdir()
        shutil.copy(root / "constraints-ci.txt", rig)
        self._remedy_stub(tmp_path / "bin" / "python3")
        if venv == "existing":
            self._remedy_stub(rig / ".venv" / "bin" / "python")
            (rig / ".venv" / "bin" / "activate").write_text(
                'echo activate >> "$REMEDY_LOG"\n', encoding="utf-8")
        temp_dir = tmp_path / "tmp"
        temp_dir.mkdir()
        log = tmp_path / "calls.log"
        log.touch()
        pip_read = tmp_path / "pip-read.txt"
        proc = subprocess.run(
            [shutil.which("bash"), "-c", block], cwd=rig, capture_output=True, text=True,
            env={"PATH": f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin",
                 "HOME": str(tmp_path), "TMPDIR": str(temp_dir), "REMEDY_LOG": str(log),
                 "REMEDY_REAL_PYTHON": sys.executable, "REMEDY_PIP_READ": str(pip_read)},
            timeout=60)
        calls = log.read_text(encoding="utf-8").splitlines()
        pip_calls = [call for call in calls if call.startswith("-m pip ")]
        left = sorted(path.relative_to(rig).as_posix() for path in rig.rglob("*")
                      if not path.is_dir())
        venv_files = [".venv/bin/activate", ".venv/bin/python"]
        assert list(temp_dir.iterdir()) == [], list(temp_dir.iterdir())
        if below_floor:
            assert pip_calls == [], calls
            assert len(calls) == 1 and calls[0].startswith("-c "), calls
            assert proc.returncode == 1 and proc.stderr.strip() == sys.version, proc
            assert left == sorted(["constraints-ci.txt",
                                   *(venv_files if venv == "existing" else [])]), left
            assert not pip_read.exists()
        else:
            assert proc.returncode == 0 and proc.stderr == "", proc
            # Nothing the remedy wrote is left in .venv or the root.
            assert left == sorted(["constraints-ci.txt", *venv_files]), left
            # The install as printed, less the interpreter that runs it, with
            # the temporary file in place of "$lock".
            install = self._REMEDY_INSTALL.split(" ", 1)[1].removesuffix(' "$lock"')
            assert len(pip_calls) == 1, calls
            given, _, read_from = pip_calls[0].rpartition(" ")
            assert given == install and Path(read_from).parent == temp_dir, pip_calls
            lines = (root / "constraints-ci.txt").read_text(encoding="utf-8").splitlines()
            first = next(i for i, line in enumerate(lines) if line.startswith("ruff=="))
            last = next(i for i in range(first, len(lines)) if not lines[i].endswith("\\"))
            assert pip_read.read_text(encoding="utf-8").splitlines() == lines[first:last + 1]
            assert "--hash=sha256:" in pip_read.read_text(encoding="utf-8")
            assert calls[-1] == "activate", calls

    def test_the_lint_gate_refuses_without_ruff_and_prints_its_remedy(self, tmp_path):
        """With no ruff on PATH the real gate, run in the real root, exits 2 and
        prints the ruff-missing remedy for that root's .venv state, line for line
        as a copy of the script prints it in a scratch root in the same state."""
        root = ci_contract.require_root("the lint gate remedy")
        script = root / "scripts" / "verify-local-lint-gate.sh"
        bin_dir = tmp_path / "bin"  # dirname and readlink only, so no ruff resolves
        bin_dir.mkdir()
        for tool in ("dirname", "readlink"):
            (bin_dir / tool).symlink_to(shutil.which(tool))
        proc = subprocess.run(
            [shutil.which("bash"), str(script)],
            env={"PATH": str(bin_dir), "HOME": str(tmp_path / "home")},
            capture_output=True, text=True, timeout=30)
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        venv = root / ".venv"
        if venv.is_symlink() and not venv.exists():
            # The copy's link must name the same target, so it prints the same line.
            expected = _render_lint_gate_remedy(root, tmp_path / "copy", "dangling",
                                                os.readlink(venv))
        else:
            state = "existing" if venv.exists() else "absent"
            expected = _render_lint_gate_remedy(root, tmp_path / "copy", state)
        assert proc.stderr == expected, proc.stderr
        assert proc.stderr.startswith("GATE REFUSED: ruff is not available"), proc.stderr

    def test_the_environment_check_remedy_is_one_install_per_host(self):
        """The verifier's MISSING remedy has exactly two spellings: the CI
        install and the local one."""
        root = ci_contract.require_root("the environment check remedy")
        text = (root / "scripts" / "verify_ci_environment.py").read_text(encoding="utf-8")
        printed = re.findall(r"install the pinned set: (python[^\"]*)\"", text)
        assert printed == [_CI_LOCK_INSTALL, _LOCAL_LOCK_INSTALL], printed

    @pytest.mark.parametrize("github_actions, expected, other", [
        ("true", _CI_LOCK_INSTALL, _LOCAL_LOCK_INSTALL),
        (None, _LOCAL_LOCK_INSTALL, _CI_LOCK_INSTALL),
        ("false", _LOCAL_LOCK_INSTALL, _CI_LOCK_INSTALL),
    ])
    def test_the_environment_check_remedy_names_the_install_for_its_host(
            self, monkeypatch, github_actions, expected, other):
        """In a GitHub Actions run the remedy is the CI install, which refuses
        a source build; anywhere else it is the local install, with the
        source-build risk stated beside it."""
        import importlib.util

        root = ci_contract.require_root("the environment check remedy")
        spec = importlib.util.spec_from_file_location(
            "_remedy_verify_ci_environment",
            root / "scripts" / "verify_ci_environment.py")
        verify = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = verify
        try:
            spec.loader.exec_module(verify)
            if github_actions is None:
                monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
            else:
                monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
            rendered = verify.Verdict(
                wrong_version={}, missing=("alpha",), unexpected=(),
                shadowed={}, interpreter_mismatch=None).render()
        finally:
            sys.modules.pop(spec.name, None)
        remedy = [line for line in rendered.splitlines()
                  if "install the pinned set:" in line]
        assert len(remedy) == 1, rendered
        assert f"install the pinned set: {expected}" in remedy[0], rendered
        assert f"install the pinned set: {other}" not in remedy[0], rendered
        assert ("builds it from source" in remedy[0]) is (
            expected == _LOCAL_LOCK_INSTALL), rendered

    def test_the_tripwire_names_the_ci_install(self):
        """test_memory_init's tripwire tells a workflow author which install to
        use, so its canonical line is the CI one."""
        root = ci_contract.require_root("the tripwire's canonical install")
        tree = ast.parse((root / "pact-plugin" / "tests" / "test_memory_init.py")
                         .read_text(encoding="utf-8"))
        values = [ast.literal_eval(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and getattr(node.targets[0], "id", "") == "_CANONICAL_INSTALL_LINE"]
        assert values == [_CI_LOCK_INSTALL], values

    def test_the_lock_input_documents_the_ci_install(self):
        root = ci_contract.require_root("the lock input header")
        header = " ".join(
            line.lstrip("#").strip() for line in
            (root / "constraints-ci.in").read_text(encoding="utf-8").splitlines()
            if line.startswith("#"))
        assert f"`{_CI_LOCK_INSTALL}`" in header, (
            "constraints-ci.in no longer documents CI's install line verbatim")


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
    assert isinstance(inputs, dict) and set(inputs) == {"dist", "shuffle_seed"}, (
        f"workflow_dispatch inputs are {inputs!r}; only the constrained dist "
        "choice and the guarded shuffle seed are admitted, never arbitrary "
        "pytest arguments"
    )
    dist = inputs["dist"]
    assert dist.get("required") is False
    assert dist.get("default") == "loadfile"
    assert dist.get("type") == "choice"
    assert dist.get("options") == ["loadfile", "load"], (
        f"dist options are {dist.get('options')!r}; loadfile stays the default "
        "and load is the only diagnostic alternative"
    )
    # The seed is free text, so it is only ever admitted through the job-level
    # PACT_SHUFFLE_SEED key and the seed guard below; empty, the default, is file order.
    seed = inputs["shuffle_seed"]
    assert seed.get("type") == "string", seed
    assert seed.get("required") is False, seed
    assert seed.get("default") == "", seed
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
    # The dispatch value reaches pytest only through this key, so the key IS
    # the contract: a literal here flips the merge gate's xdist scheduling
    # while every command-text pin stays green.
    assert (job.get("env") or {}).get("DIST") == "${{ inputs.dist || 'loadfile' }}", (
        f"the job-level DIST env is {(job.get('env') or {}).get('DIST')!r}; it "
        "must evaluate from inputs.dist and default to loadfile outside a dispatch"
    )
    # Empty outside a dispatch, so the merge gate's run line expands the seed
    # to no words and the root config keeps pytest-randomly blocked.
    assert "SEED" not in (job.get("env") or {}), "the seed's earlier, generic name is back"
    assert (job.get("env") or {}).get("PACT_SHUFFLE_SEED") == "${{ inputs.shuffle_seed || '' }}", (
        f"the job-level PACT_SHUFFLE_SEED env is {(job.get('env') or {}).get('PACT_SHUFFLE_SEED')!r}; it "
        "must evaluate from inputs.shuffle_seed and be empty outside a dispatch"
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

    # The dispatch value is one argv element, so it is case-guarded before
    # anything expensive runs. A guard that can be skipped or silenced
    # certifies nothing, and one with its own env/shell view would check a
    # different DIST than the test step sees.
    guard_runs = [run for run in runs if "DIST GUARD" in run]
    assert len(guard_runs) == 1, (
        f"expected exactly one DIST guard run step; found {guard_runs!r}")
    guard = next(step for step in steps
                 if isinstance(step.get("run"), str)
                 and "DIST GUARD" in step["run"])
    for key in ("if", "continue-on-error", "env", "shell"):
        assert key not in guard, (
            f"the DIST guard carries `{key}:` ({guard[key]!r}); it must run "
            "unconditionally in the default shell with the job-level DIST"
        )
    assert "loadfile|load)" in guard_runs[0], guard_runs[0]
    assert "$DIST" in guard_runs[0], guard_runs[0]
    assert not re.search(r"\bpytest\b", guard_runs[0]), (
        "the DIST guard's run text names pytest; test_ci_collection_scope "
        "permits exactly one pytest invocation in this file"
    )
    guard_position = runs.index(guard_runs[0])
    verify_positions = [
        index for index, run in enumerate(runs)
        if run == ci_contract.VERIFY_CMD
    ]
    assert verify_positions and guard_position < verify_positions[0], (
        "the DIST guard must run before the before-tests environment check, "
        "so an out-of-enum value refuses before setup work"
    )

    # The seed is word-split into the run line, so its guard is the DIST
    # guard's twin: exactly one step, unconditional, in the job's own env and
    # default shell, and it publishes the only seed text the artifact name
    # uses (`steps.order.outputs.order`).
    seed_guards = [step for step in steps
                   if isinstance(step.get("run"), str)
                   and "SEED GUARD" in step["run"]]
    assert len(seed_guards) == 1, (
        f"expected exactly one SEED guard step; found {seed_guards!r}")
    seed_guard = seed_guards[0]
    assert seed_guard.get("id") == "order", (
        f"the SEED guard's id is {seed_guard.get('id')!r}; the artifact name "
        "reads steps.order.outputs.order")
    for key in ("if", "continue-on-error", "env", "shell"):
        assert key not in seed_guard, (
            f"the SEED guard carries `{key}:` ({seed_guard[key]!r}); it must "
            "run unconditionally in the default shell with the job-level PACT_SHUFFLE_SEED"
        )
    seed_run = seed_guard["run"]
    assert "$PACT_SHUFFLE_SEED" in seed_run, seed_run
    assert "^(0|[123456789][0123456789]{0,8})$" in seed_run, seed_run
    assert ">> \"$GITHUB_OUTPUT\"" in seed_run, seed_run
    assert not re.search(r"\bpytest\b", seed_run), (
        "the SEED guard's run text names pytest; test_ci_collection_scope "
        "permits exactly one pytest invocation in this file"
    )
    seed_position = runs.index(seed_run)
    warm_positions = [index for index, run in enumerate(runs)
                      if "StaticModel.from_pretrained" in run]
    assert warm_positions and guard_position < seed_position < warm_positions[0], (
        "the SEED guard must run after the DIST guard and before the "
        "model warm, so an invalid seed refuses before setup work"
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
    assert runs[-1] == ci_contract.VERIFY_CMD, (
        "the after-verify is not the last `run:` step")
    after_verify_indices = [
        index for index, step in enumerate(steps)
        if step.get("run") == ci_contract.VERIFY_CMD
    ]
    assert len(after_verify_indices) == 2, (
        f"expected the before/after environment checks; found "
        f"{len(after_verify_indices)}")
    assert str(steps[after_verify_indices[-1]].get("if", "")).strip() in (
        "always()", "${{ always() }}"), "the after-tests environment check is not `if: always()`"

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
        "always() && (failure() || cancelled() || "
        "github.event_name == 'workflow_dispatch')"), (
        f"diagnostic upload condition is {upload.get('if')!r}; it must upload "
        "after a red or cancelled step on every event and after either outcome "
        "on a dispatch"
    )
    upload_with = upload.get("with") or {}
    assert upload_with.get("name") == ci_contract.UPLOAD_ARTIFACT_NAME
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
    assert upload_index == len(steps) - 1, (
        f"diagnostic upload index {upload_index}, final step index "
        f"{len(steps) - 1}: the upload must be the LAST step, so a red "
        "after-tests environment check still uploads the run's evidence"
    )
    assert steps[upload_index - 1].get("run") == ci_contract.VERIFY_CMD, (
        f"the step before the upload is "
        f"{steps[upload_index - 1].get('name')!r}, not the after-tests "
        "environment check"
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


def _seed_guard_run(root):
    """The seed guard step's `run:` body, as tests.yml ships it."""
    job = ci_contract.jobs_of(_full_doc(root))["full-suite"]
    guards = [text for text in ci_contract.run_texts(ci_contract.steps_of(job))
              if "SEED GUARD" in text]
    assert len(guards) == 1, f"expected exactly one SEED guard step, found {len(guards)}"
    return guards[0]


# Which locale legs the seed-guard rows run under, decided by PLATFORM, never
# by tool availability: on Linux (glibc, where CI runs) the range-widening leg
# is generated, and a missing localedef or en_US source fails it in CI
# (GITHUB_ACTIONS=true) and skips it elsewhere; on other platforms glibc's
# locales do not exist, so only the ASCII leg applies.
_GLIBC_LOCALES = sys.platform.startswith("linux")
_LOCALE_LEGS = ("ascii", "range-widening") if _GLIBC_LOCALES else ("ascii",)


def _range_widening_locale(tmp_path_factory, localedef):
    """An en_US.UTF-8 locale built into a tmp LOCPATH by `localedef`. In CI
    (GITHUB_ACTIONS=true) a missing localedef or a failed build fails, because
    the runner ships glibc's en_US source and the seed guard's digit classes must
    be proven there. Elsewhere either skips, since a host without the `locales`
    package cannot build it; the static no-range arm still runs."""
    def unavailable(reason):
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"{reason}, so the seed guard's digit classes go unproven")
        pytest.skip(f"{reason}; outside CI the static no-range arm still runs")

    if not localedef:
        unavailable("localedef is not on PATH, so the range-widening locale cannot be built")
    locpath = tmp_path_factory.mktemp("locpath")
    home = tmp_path_factory.mktemp("localedef-home")
    proc = subprocess.run(
        [localedef, "-i", "en_US", "-f", "UTF-8", str(locpath / "en_US.UTF-8")],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True, text=True, timeout=120)
    if not (locpath / "en_US.UTF-8" / "LC_COLLATE").is_file():
        unavailable(f"localedef did not build en_US.UTF-8 (rc {proc.returncode}): "
                    f"{proc.stderr.strip()}")
    return {"LC_ALL": "en_US.UTF-8", "LOCPATH": str(locpath)}


@pytest.fixture(scope="module")
def range_widening_locale(tmp_path_factory):
    """An en_US.UTF-8 locale built into a tmp LOCPATH, independent of which
    locales the runner generated. Under it, bash's `[0-9]` matches non-ASCII
    digits such as U+0663; under C.UTF-8 it does not. Building it needs only
    localedef and glibc's en_US source, which the `locales` package ships."""
    return _range_widening_locale(tmp_path_factory, shutil.which("localedef"))


class TestRangeWideningLocaleUnavailable:
    """Without localedef or glibc's en_US source the locale fails in CI and
    skips elsewhere."""

    @staticmethod
    def _failing_localedef(tmp_path):
        script = tmp_path / "localedef"
        script.write_text("#!/bin/sh\necho 'cannot open locale definition file' >&2\nexit 4\n",
                          encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    def _outcome(self, tmp_path, tmp_path_factory, missing):
        """The exception the helper ends with. A skip or a fail escapes
        pytest.raises for the other kind, so catch both and compare."""
        localedef = None if missing == "binary" else self._failing_localedef(tmp_path)
        try:
            _range_widening_locale(tmp_path_factory, localedef)
        except (pytest.fail.Exception, pytest.skip.Exception) as exc:
            return type(exc)
        return None

    @pytest.mark.parametrize("missing", ["binary", "source"])
    def test_it_fails_in_ci(self, monkeypatch, tmp_path, tmp_path_factory, missing):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert self._outcome(tmp_path, tmp_path_factory, missing) is pytest.fail.Exception

    @pytest.mark.parametrize("missing", ["binary", "source"])
    def test_it_skips_outside_ci(self, monkeypatch, tmp_path, tmp_path_factory, missing):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        assert self._outcome(tmp_path, tmp_path_factory, missing) is pytest.skip.Exception


class TestSeedGuardBehaviour:
    """The seed guard's verdicts, EXECUTED under `bash -e` as the runner does,
    in an ASCII locale and in one where bracket ranges match non-ASCII digits."""

    @staticmethod
    def _run_guard(tmp_path, seed, locale):
        root = ci_contract.require_root("the seed guard's behaviour")
        bash = shutil.which("bash")
        assert bash, "bash is not on PATH; the seed guard is bash"
        script = tmp_path / "guard.sh"
        script.write_text(_seed_guard_run(root), encoding="utf-8")
        output = tmp_path / "github_output"
        output.write_text("", encoding="utf-8")
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        proc = subprocess.run(
            [bash, "-e", str(script)],
            env={"PATH": "/usr/bin:/bin", "HOME": str(home),
                 "LC_ALL": locale["LC_ALL"], "LOCPATH": locale["LOCPATH"],
                 "PACT_SHUFFLE_SEED": seed, "GITHUB_OUTPUT": str(output)},
            capture_output=True, text=True, timeout=30)
        return proc, output.read_text(encoding="utf-8")

    @pytest.fixture(params=_LOCALE_LEGS)
    def locale(self, request, tmp_path_factory):
        """C.UTF-8 is built into glibc, so an empty LOCPATH leaves it intact."""
        if request.param == "ascii":
            return {"LC_ALL": "C.UTF-8", "LOCPATH": str(tmp_path_factory.mktemp("no-locales"))}
        return request.getfixturevalue("range_widening_locale")

    def test_the_locale_legs_match_the_platform(self, tmp_path, request):
        """CONTROL. On Linux the non-ASCII digit rows run under a built
        en_US.UTF-8 and can only catch a `[0-9]` guard if `[0-9]` matches
        U+0663 there, so prove it does. A missing localedef or en_US source
        fails here and in every widening row in CI, and skips them elsewhere.
        Off Linux glibc's locales do not exist, the widening leg is not
        generated, and the static no-range arm is the only check of the digit
        classes."""
        if not sys.platform.startswith("linux"):
            assert _LOCALE_LEGS == ("ascii",), _LOCALE_LEGS
            return
        assert _LOCALE_LEGS == ("ascii", "range-widening"), _LOCALE_LEGS
        range_widening_locale = request.getfixturevalue("range_widening_locale")
        home = tmp_path / "home"
        home.mkdir()
        probe = subprocess.run(
            [shutil.which("bash"), "-c", '[[ "\u0663" =~ ^[0-9]$ ]]'],
            env={"PATH": "/usr/bin:/bin", "HOME": str(home),
                 "LC_ALL": range_widening_locale["LC_ALL"],
                 "LOCPATH": range_widening_locale["LOCPATH"]},
            capture_output=True, text=True, timeout=30)
        assert probe.returncode == 0, (
            "under the built en_US.UTF-8, bash's [0-9] no longer matches U+0663, "
            f"so the non-ASCII digit rows cannot tell a range from the spelled-out "
            f"class: {probe.stderr}")

    @pytest.mark.parametrize("seed, order", [
        ("", "ordered"),
        ("0", "seed-0"),
        ("7", "seed-7"),
        ("123456789", "seed-123456789"),
    ])
    def test_an_accepted_seed_writes_one_order_line(self, tmp_path, locale, seed, order):
        proc, written = self._run_guard(tmp_path, seed, locale)
        assert proc.returncode == 0, proc.stderr
        assert written == f"order={order}\n"

    @pytest.mark.parametrize("seed", [
        "007", "1234567890", "1 2", "$(id)", "-1", "abc", "5\n", "5\nx=y",
        "\u0663", "1\u0663", "\u0969", "\uff13",
    ])
    def test_a_refused_seed_exits_before_writing(self, tmp_path, locale, seed):
        proc, written = self._run_guard(tmp_path, seed, locale)
        assert proc.returncode == 1, (proc.returncode, proc.stderr)
        assert "SEED GUARD:" in proc.stderr
        assert seed not in proc.stdout + proc.stderr, "the guard echoed the value"
        assert written == ""

    def test_the_guard_regex_spells_out_its_classes(self):
        """A bracket range such as `[0-9]` is collation-dependent. The
        range-widening rows catch it under the built locale; this pins it
        without running anything."""
        root = ci_contract.require_root("the seed guard's digit classes")
        patterns = re.findall(r"=~\s+(\S+)\s+\]\]", _seed_guard_run(root))
        assert len(patterns) == 1, patterns
        classes = re.findall(r"\[([^\]]*)\]", patterns[0])
        assert classes, patterns[0]
        ranged = [c for c in classes if re.search(r".-.", c)]
        assert not ranged, f"the seed guard's regex uses bracket ranges: {ranged}"


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
            # The converted modules import these at module scope, so a rig that
            # collects them without one fails collection instead of exercising
            # the arm.
            Path("pact-plugin") / "tests" / "fixtures" / "tracked_files.py",
            Path("pact-plugin") / "tests" / "helpers.py",
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
            capture_output=True, text=True, timeout=300, env=_child_environ())
        assert proc.returncode == 0, (
            f"the full-collection guard failed in a rig with no fast files "
            f"(rc={proc.returncode}).\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        # EXECUTE the fast-dependent arms; they must skip loudly.
        arms = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rA",
             "-p", "no:cacheprovider", "-p", "no:randomly",
             *self._FAST_DEPENDENT_NODE_IDS],
            cwd=str(rig / "pact-plugin"), capture_output=True, text=True,
            timeout=300, env=_child_environ())
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
             "-p", "no:cacheprovider", "-p", "no:randomly",
             *self._FAST_DEPENDENT_NODE_IDS],
            cwd=str(rig / "pact-plugin"), capture_output=True, text=True,
            timeout=300, env=_child_environ())
        assert arms.returncode != 0, (
            f"a partially deleted fast lane did not fail; output:\n"
            f"{arms.stdout}")
        combined = arms.stdout + arms.stderr
        assert "FAST LANE PARTIALLY PRESENT" in combined, combined