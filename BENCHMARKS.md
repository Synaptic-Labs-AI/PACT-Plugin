# PACT benchmarks

Append-only tracking for external PACT evaluations. Each row points at a
committed summary under bench/results/<version>/<run-id>/. Summaries are
assembled by hand from the raw runs; bench/tests/test_results.py checks each
row against its summary. These are model-specific diagnostics, not
certification or merge gates.

| Version | Plugin SHA | Date | P1 internal suite | Track A native eval | PluginEval selected | Orchestration smoke | Spawn tokens | Track B |
|---|---|---|---|---|---|---|---|---|
| 4.8.38 | 1befafb3 | 2026-11-25 | green | blocked: sandbox confinement; bridge path fixed | pact-agent-teams 49.65; pact-handoff-harvest 54.44; pact-memory 74.50 | pass: exit 0, 3/3 HANDOFFs valid | median 69,359 (n=2) | 6 tasks validated out of tree; fixtures in a follow-up PR; not executed |

## Reading the row

- P1 is the internal suite at the evaluated SHA, from CI evidence.
- The eval Bash bridge path-length bug is fixed at the harness level, but
  native eval remains blocked for shell-dependent PACT cases by sandbox
  confinement. No native score is claimed.
- PluginEval scores are experimental and Anthropic-labeled. Token counts are
  real; USD is not DeepSeek billing.
- Track B is the outcome benchmark. The six tasks were validated out of tree;
  their fixtures land in a follow-up PR, so the validation is not reproducible
  from this commit. No PACT-vs-solo runs have been executed, so this row makes
  no coding-outcome claim.
- Spawn tokens are first-request subagent usage, n=2; not a statistical result.

## Run

See bench/README.md and bench/external/README.md. Raw runs stay under
bench/.runs/ and are gitignored.
