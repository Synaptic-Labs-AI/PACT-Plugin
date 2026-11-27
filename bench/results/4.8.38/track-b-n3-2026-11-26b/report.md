# PACT Track B — n=3 interactive PTY matrix

Run: track-b-n3-2026-11-26b
Plugin: PACT 4.8.38 at 1befafb3
Model: deepseek-flash[1m]
Samples: 3 per task and condition (36 sessions)
Mode: real interactive Claude Code in a PTY; PACT arm used the PACT plugin and
PACT:pact-orchestrator; solo arm used no plugin. Completion was detected with an
absolute sentinel; hidden tests were run only after the agent exited.

## Headline

Both arms passed every hidden-test outcome: PACT 18/18, solo 18/18. There is no
outcome separation at this sample size.

The cost and completion picture is different:

| Metric | PACT | Solo |
|---|---:|---:|
| Hidden-test outcomes | 18/18 | 18/18 |
| Sentinel/exit completion | 15/18 | 18/18 |
| Mean wall time | 1,103 s | 233 s |
| Mean tokens (main + subagents) | 74.1M | 4.2M |
| Timeouts | 3 | 0 |

PACT was about 4.7x slower and 17.6x more token-heavy. Three PACT runs hit the
1,800 s harness cap but still passed the hidden tests. The task configs allow
2,700 s for high-variety tasks, so completion rate is a lower bound, not a
quality failure.

## Per-task results

| Task | Class | PACT outcome | Solo outcome | PACT mean time | Solo mean time | PACT mean tokens | Solo mean tokens | PACT HANDOFFs |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| discount-strategy-refactor | medium | 3/3 | 3/3 | 1,200 s | 198 s | 93.8M | 3.9M | 4.67 |
| invoice-rounding-bug | low | 3/3 | 3/3 | 657 s | 170 s | 43.7M | 2.6M | 2.67 |
| job-queue-retries | high | 3/3 | 3/3 | 1,748 s | 334 s | 116.4M | 5.3M | 5.33 |
| jwt-auth | medium | 3/3 | 3/3 | 1,544 s | 348 s | 87.1M | 6.7M | 3.33 |
| notes-api-crud | low | 3/3 | 3/3 | 450 s | 148 s | 22.6M | 2.6M | 0.67 |
| oauth-pkce-flow | high | 3/3 | 3/3 | 1,021 s | 203 s | 81.1M | 4.0M | 3.67 |

## True-team evidence

All PACT task groups show teammate task tools in subagent transcripts:
TaskGet, TaskList, and TaskUpdate. PACT averaged 2.7-5.3 HANDOFFs per task
depending on task. The pilot and matrix teammate transcripts also show
teachback and task assignment; this is not the relay-only headless path.

## Verifier rerun

One solo job-queue-retries run initially failed with a 120 s hidden-test
timeout under concurrent load. Re-running the same hidden suite passed 12/12 in
7.7 s. It is recorded as an infrastructure rerun, not a model failure. The
runner verifier timeout is now 300 s.

## Interpretation

- These six tasks are within solo capability; PACT's process did not improve
  hidden-test outcomes at n=3.
- PACT's coordination overhead is substantial in wall time and tokens. The
  large token figure includes the main session and all teammate transcripts.
- The PACT arm is not merely a wrapper: true teammates used task tools and
  emitted HANDOFFs. The cost is the price of that process.
- Three PACT runs hit the 1,800 s cap. This is a completion/harness finding,
  not evidence that the code was wrong.

## Recommendations

1. Do not claim a coding-outcome advantage from this run. Use n>=5 and report
   confidence intervals before making a quality claim.
2. Reduce PACT's spawn and coordination token overhead before scaling; 74M mean
   tokens per task is the dominant cost.
3. Re-run high-variety tasks with the full task timeout (2,700 s) and distinguish
   completion from hidden-test outcome in the scorecard.
4. Keep the hidden-test outcome as the primary metric; use completion time and
   tokens as secondary costs, not quality proxies.
5. Investigate the PACT in-process wait/timeout behavior seen on jwt-auth and
   job-queue-retries; all three timeout runs had hidden-passing code.

## Reproduction

    bench/.venv/bin/python bench/runner/track_b.py \
      --samples 3 --jobs 6 --timeout 2700 \
      --run-id <run-id>

Raw transcripts and workspaces remain under bench/.runs/track-b/ and are
gitignored. Only aggregate results belong in bench/results/.
