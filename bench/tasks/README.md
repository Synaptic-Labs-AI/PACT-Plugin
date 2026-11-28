# Track B task suite

Each task is a small, deterministic Python 3.12 stdlib project. The design: the
agent under test receives fixture/ as its workspace and prompt.md as the
request, and is scored against hidden_tests/, which are kept out of its
workspace. The runner that does this is not in this PR; only the validator is.

No task directories are committed yet; they land in a follow-up PR. Until then
`bench/pact-bench tasks validate` exits with "no tasks found".

## Task directory layout

    <task-id>/
      task.json
      prompt.md
      fixture/            # seed repo, no .git, stdlib only
      hidden_tests/       # pytest tests, stored outside the agent workspace
      gold.patch          # git-format patch that applies to fixture/ and passes hidden tests
      rubric.md           # blind quality rubric (required; the validator checks it exists)

## task.json

{
  "schema": "pact-bench/task/1",
  "id": "kebab-case-id",
  "title": "...",
  "class": "low" | "medium" | "high",
  "variety": 5,
  "revision": 1,
  "timeout_seconds": 1500,
  "max_turns": 80,
  "prompt": "prompt.md",
  "fixture": "fixture",
  "hidden_tests": "hidden_tests",
  "gold_patch": "gold.patch",
  "rubric": "rubric.md"
}

## Fixture rules

- Python 3.12 stdlib only; no network, no third-party runtime deps.
- Seed tests live under fixture/tests/ and are runnable with:
    python -m pytest -q
- Seed tests must pass on the untouched fixture.
- The agent must be able to run them; pytest is available in the verifier env.
- Include a short fixture README describing how to run tests.

## Hidden tests

- pytest files under hidden_tests/.
- They must fail on the untouched fixture with pytest exit status 1 (tests ran
  and at least one failed). A collection error (2) or no tests (5) fails the
  task, so a hidden test that imports a module the gold patch creates must
  import it inside the test body.
- They must pass after gold.patch applies.
- They must not import or reference gold.patch, task.json, or the harness.
- They test user-visible requirements from prompt.md, not implementation details.

## Gold patch

- A git-format patch relative to fixture/.
- Apply with: git apply --directory=fixture gold.patch (or from a copy of fixture).
- It must not modify hidden_tests/ in any way (edit, delete, rename in or out).
- It must make hidden tests pass (at least one passed, no plain skips; xfails are
  allowed) and keep seed tests passing (no fewer passed, no more skipped).

## Prompt

- State the user-visible requirements and constraints.
- Include: work autonomously; do not ask questions; commit your work on a feature
  branch; do not push, open a PR, or merge.
- Do not reveal hidden tests or the gold patch.

## Author validation

Run from the task directory:

1. Copy fixture to a temp dir, git init, commit.
2. Run the seed tests; they pass.
3. Copy hidden_tests in, run pytest; at least one fails.
4. Apply gold.patch to the fixture copy, run hidden_tests; they pass.
5. Run seed tests again; they pass.

Record the test counts and any issues in the final report.

## Validator

    bench/pact-bench tasks validate [--root DIR] [--task DIR ...]

With no `--task` it validates every task under `--root` (default bench/tasks)
and reports each one: a passing task carries `"status": "pass"`, a failing one
`{"id", "path", "status": "fail", "error"}` (id from task.json, else the
directory name). Any error in one task (an unreadable or non-UTF-8 task.json, a
wrong field type, a copy error, anything unexpected) becomes that task's fail
row and the run continues; an unexpected error reads
`<id>: <ExceptionType>: <message>`. The run exits 1 if any task failed. `timeout_seconds` must be a positive
integer and bounds each command; a command that times out has its process
group killed (a grandchild that starts its own session is not killed). Git runs without the
global or system config, with hooks off and with no inherited `GIT_*` variables;
the child pytest gets no inherited `PYTEST_*` or `PYTHONPATH`. The hidden tests are snapshotted
before the gold patch is applied, and any change to them (an edit, a deletion,
a rename in or out, non-ASCII names included) rejects the task. A gold patch
that only adds files is accepted, and the seed tests after the patch run with
`--ignore=hidden_tests`. After the patch, the hidden tests must have at least one
test passed and no plain skips (xfails are counted separately and allowed), and
the seed tests no fewer passed and no more skipped than before, counted from
pytest's JUnit report; a pytest run that writes no JUnit report fails the task; a patch whose conftest
rewrites that report can still fool the check, which targets accidental skips.
Every path in task.json must be a string that resolves inside the task directory
(`../`, absolute paths and symlinks pointing out fail the task), and every
symlink inside fixture/ or hidden_tests/ must resolve inside the task directory.
