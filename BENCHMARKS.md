# PACT benchmarks

Append-only tracking for external PACT evaluations. Each row points at a
committed summary under bench/results/<version>/<run-id>/. These are
model-specific diagnostics, not certification or merge gates.

| Version | Plugin SHA | Date | P1 internal suite | Track A native eval | PluginEval selected | Orchestration smoke | Spawn tokens | Track B |
|---|---|---|---|---|---|---|---|---|
| 4.8.38 | 1befafb3 | 2026-11-25 | green | blocked: eval Bash sandbox; no valid case | pact-agent-teams 49.65; pact-handoff-harvest 54.44; pact-memory 74.50 | pass: exit 0, 3/3 HANDOFFs valid | median 69,359 (n=2) | 6 tasks validated; not executed |

## Reading the row

- P1 is the internal suite at the evaluated SHA, from CI evidence.
- Native eval is blocked on this host because the eval Bash sandbox fails to
  initialize. No native score is claimed.
- PluginEval scores are experimental and Anthropic-labeled. Token counts are
  real; USD is not DeepSeek billing.
- Track B is the outcome benchmark. The six tasks are validated, but no
  PACT-vs-solo runs have been executed, so this row makes no coding-outcome
  claim.
- Spawn tokens are first-request subagent usage, n=2; not a statistical result.

## Run

See bench/README.md and bench/external/README.md. Raw runs stay under
bench/.runs/ and are gitignored.
