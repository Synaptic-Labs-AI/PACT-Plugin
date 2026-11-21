#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pact_wait.py
Summary: Structural wait mechanism — a stateless deliverable watcher the
         team-lead launches as a harness-tracked background Bash task
         (`run_in_background: true`) BEFORE ending its turn on a teammate
         deliverable. The watcher polls the task file for the named
         deliverable predicate; its process completion is the wake that
         re-invokes the lead. Deliberately NOT registered in hooks.json:
         it is a plain stdlib CLI, launched solely by the lead's own Bash
         frame (a hook-spawned process is not harness-tracked and wakes
         nothing).
Used by: the lead's arm command (supplied ready-to-run by
         hooks/task_lifecycle_gate.py PostToolUse branch and the
         hooks/wait_filler_gate.py tier-3 outlet); hooks/shared/wait_target.py
         (predicate registry + one-pass evaluation); tests/test_pact_wait.py.

Exit contract (loud by design; a running watch NEVER exits 1 or 2):
    0  FIRED — the deliverable is present (checked before the first sleep,
       so a duplicate watcher armed after delivery fast-exits here)
    3  EXPIRED — the watch RAN to completion and the deadline passed with
       the deliverable absent
    4  TERMINAL — the watch cannot do its job at all (unknown predicate,
       unresolvable team/task target, missing task file, unreadable task
       file, persistently unparseable task file)
    2  usage error — missing/invalid CLI arguments (argparse; caller bug,
       surfaces before any watching starts)
    1  unexpected runtime error — an uncaught exception escapes with its
       traceback (a crash must never masquerade as EXPIRED)

Stdout on FIRED and stderr on EXPIRED/TERMINAL carry the predicate, task id
and team name so the background-completion notification answers "what woke
me" without re-deriving it. Stateless: no state file, no reaper, no cleanup
owed — the process lifecycle IS the state.
"""

from __future__ import annotations

import argparse
import sys
import time

from shared import wait_target

# Poll cadence. The bounded internal poll is the ONE permitted sleep loop in
# the wait discipline: it runs inside this subprocess, invisible to the
# harness and to the lead's own flow.
POLL_INTERVAL_SECONDS = 5.0

# A task file that fails to parse on CONSECUTIVE polls past this count is
# corrupt, not mid-rewrite: the platform rewrites task files in milliseconds,
# so two full poll intervals of parse failure is a broken precondition and
# exits TERMINAL instead of burning the deadline toward a misleading EXPIRED.
MAX_CONSECUTIVE_CORRUPT_READS = 2

EXIT_FIRED = 0
EXIT_EXPIRED = 3
EXIT_TERMINAL = 4


def _positive_float(text: str) -> float:
    """argparse type for REQUIRED-nonzero numeric arguments. Rejects
    non-numbers and any value <= 0 as usage errors (exit 2)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {text!r}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    """The arm-command argument surface: --team/--task/--predicate/--deadline,
    all required. --tasks-base-dir is a testing seam mirroring the
    tasks_base_dir override idiom of the shared readers."""
    script = str(__file__)
    examples = (
        "Examples:\n"
        f'  python3 "{script}" --team my-team --task 12 --predicate handoff --deadline 900\n'
        f'  python3 "{script}" --team my-team --task 12 --predicate teachback_submit --deadline 600'
    )
    parser = argparse.ArgumentParser(
        prog="pact_wait.py",
        description="Watch a task file for a deliverable; exit 0 when it "
        "arrives, 3 on deadline expiry, 4 when the watch cannot run.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--team", required=True, help="team name (tasks/{team}/ store)")
    parser.add_argument("--task", required=True, help="task id to watch")
    parser.add_argument(
        "--predicate",
        required=True,
        help=f"deliverable predicate; one of: {', '.join(sorted(wait_target.PREDICATES))}",
    )
    parser.add_argument(
        "--deadline",
        required=True,
        type=_positive_float,
        help="maximum seconds to wait (REQUIRED, nonzero)",
    )
    parser.add_argument(
        "--tasks-base-dir",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def wait_for_deliverable(
    team_name: str,
    task_id: str,
    predicate_name: str,
    deadline: float,
    tasks_base_dir: str | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> int:
    """Poll the deliverable until present or deadline; return the exit code.

    The first evaluation happens BEFORE any sleep (the fast-exit: an already
    delivered deliverable fires immediately, which is also how a duplicate
    watcher re-arm resolves). One pass per poll_interval, bounded by the
    deadline — total watch time never exceeds the deadline by more than the
    final truncated sleep.

    Terminal outcomes (exit 4) surface immediately, except a parse failure,
    which is tolerated as a possible mid-rewrite until it persists across
    MAX_CONSECUTIVE_CORRUPT_READS consecutive polls.
    """
    deadline_at = time.monotonic() + deadline
    corrupt_streak = 0
    while True:
        outcome = wait_target.evaluate_target(
            team_name, task_id, predicate_name, tasks_base_dir=tasks_base_dir
        )
        if outcome == wait_target.EVAL_PRESENT:
            print(
                f"pact-wait FIRED: predicate '{predicate_name}' satisfied "
                f"for task {task_id} (team {team_name})"
            )
            return EXIT_FIRED
        if outcome in (
            wait_target.EVAL_TARGET_MISSING,
            wait_target.EVAL_TARGET_UNREADABLE,
            wait_target.EVAL_UNKNOWN_PREDICATE,
        ):
            print(
                f"pact-wait FAILED: cannot watch task {task_id} "
                f"(team {team_name}, predicate '{predicate_name}'): {outcome}",
                file=sys.stderr,
            )
            return EXIT_TERMINAL
        if outcome == wait_target.EVAL_TARGET_CORRUPT:
            corrupt_streak += 1
            if corrupt_streak >= MAX_CONSECUTIVE_CORRUPT_READS:
                print(
                    f"pact-wait FAILED: task file for task {task_id} "
                    f"(team {team_name}) unparseable on "
                    f"{corrupt_streak} consecutive reads — corrupt, "
                    "not mid-rewrite",
                    file=sys.stderr,
                )
                return EXIT_TERMINAL
        else:
            corrupt_streak = 0
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            print(
                f"pact-wait EXPIRED: deadline {deadline}s reached with the "
                f"deliverable absent (predicate '{predicate_name}', "
                f"task {task_id}, team {team_name}) — check the task file "
                "manually",
                file=sys.stderr,
            )
            return EXIT_EXPIRED
        time.sleep(min(poll_interval, remaining))


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the exit code per the module exit contract;
    unexpected exceptions propagate (traceback + exit 1, deliberately
    uncaught)."""
    args = _build_parser().parse_args(argv)
    return wait_for_deliverable(
        args.team,
        args.task,
        args.predicate,
        args.deadline,
        tasks_base_dir=args.tasks_base_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
