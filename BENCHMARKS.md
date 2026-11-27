# PACT benchmarks

Append-only tracking for external PACT evaluations. Each row points at a
committed summary under bench/results/<version>/<run-id>/. These are
model-specific diagnostics, not certification or merge gates.

| Version | Plugin SHA | Date | P1 internal suite | Track A native eval | PluginEval selected | Orchestration smoke | Spawn tokens | Track B |
|---|---|---|---|---|---|---|---|---|
| 4.8.38 | 1befafb3 | 2026-11-25 | green | blocked: sandbox confinement; bridge path fixed | pact-agent-teams 49.65; pact-handoff-harvest 54.44; pact-memory 74.50 | pass: exit 0, 3/3 HANDOFFs valid | median 69,359 (n=2) | n=3 PTY: outcomes 18/18 vs 18/18; PACT 1,103s/74.1M tokens vs solo 233s/4.2M; PACT completion 15/18 (3 cap timeouts) |

## Reading the row

- P1 is the internal suite at the evaluated SHA, from CI evidence.
- The eval Bash bridge path-length bug is fixed at the harness level, but
  native eval remains blocked for shell-dependent PACT cases by sandbox
  confinement. No native score is claimed.
- PluginEval scores are experimental and Anthropic-labeled. Token counts are
  real; USD is not DeepSeek billing.
- Track B is the outcome benchmark. This row's n=3 PTY matrix has both arms
  at 18/18 hidden-test outcomes; PACT is ~4.7x slower and ~17.6x more
  token-heavy. Three PACT runs hit the 1,800s cap but hidden-passed. No
  statistical superiority claim is made.
- Spawn tokens are first-request subagent usage, n=2; not a statistical result.

## Run

See bench/README.md and bench/external/README.md. Raw runs stay under
bench/.runs/ and are gitignored.
