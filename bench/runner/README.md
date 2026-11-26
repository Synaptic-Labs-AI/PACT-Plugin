# Track B PTY runner

Runs the six validated Track B tasks under PACT and solo in real interactive
Claude Code sessions. Interactive PTY is required for true Agent Teams: under
claude -p, PACT teammates are ordinary background subagents and do not receive
TaskCreate/TaskGet/TaskList/TaskUpdate.

## Prerequisites

    bench/bootstrap.sh
    bench/pact-bench prepare --out bench/.runs/external/probe --verify

The runner uses pexpect from bench/.venv and the prepared scratch plugin/config.

## Usage

    bench/.venv/bin/python bench/runner/track_b.py \
      --samples 3 --jobs 6 --timeout 2700

It discovers bench/tasks/*/task.json, copies each fixture into a fresh
workspace, starts an interactive Claude Code session, sends the task prompt plus
an absolute DONE sentinel path, and runs hidden tests only after the session
exits. PACT uses --plugin-dir and --agent PACT:pact-orchestrator; solo uses no
plugin.

Raw workspaces, transcripts, and per-run JSON stay under
bench/.runs/track-b/<run-id>/ and are gitignored. Committed results live under
bench/results/<version>/.

## Startup and safety

- The runner handles the first-run bypass warning, security notice, and folder
  trust modal. The workspace path is absolute and written into the copied
  .claude.json so trust is recognized.
- The runner auto-approves the dangerous-command confirmation in the disposable
  benchmark workspace. Only run trusted fixtures.
- Completion is the sentinel. Hidden-test outcome is separate from completion:
  a run can time out after producing hidden-passing code.
- The hidden-test verifier timeout is 300 s; infrastructure timeouts are rerun
  and recorded, not treated as model failures.

## Current baseline

bench/results/4.8.38/track-b-n3-2026-11-26b/ records n=3:
PACT 18/18 hidden outcomes, solo 18/18; PACT completion 15/18 (three 1,800 s
caps), mean 1,103 s/74.1M tokens vs solo 233 s/4.2M. See that report for the
per-task table and caveats.
