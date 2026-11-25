# Track B task suite

Each task is a small, deterministic Python 3.12 stdlib project. The agent under
test receives fixture/ as its workspace, prompt.md as the request, and is scored
against hidden_tests/ that are never present in its workspace.

## Task directory layout

    <task-id>/
      task.json
      prompt.md
      fixture/            # seed repo, no .git, stdlib only
      hidden_tests/       # pytest tests, stored outside the agent workspace
      gold.patch          # git-format patch that applies to fixture/ and passes hidden tests
      rubric.md           # optional blind quality rubric

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
- They must fail on the untouched fixture (at least one failure, non-vacuous).
- They must pass after gold.patch applies.
- They must not import or reference gold.patch, task.json, or the harness.
- They test user-visible requirements from prompt.md, not implementation details.

## Gold patch

- A git-format patch relative to fixture/.
- Apply with: git apply --directory=fixture gold.patch (or from a copy of fixture).
- It must not modify hidden_tests/.
- It must make hidden tests pass and keep seed tests passing.

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
