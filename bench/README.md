# PACT evaluation harness

Operational docs for the external PACT evaluation. This directory is dev-only:
it is outside pact-plugin/, is not shipped to plugin users, and does not change
plugin runtime behavior.

## Layout

- external/ - native claude plugin eval cases, PluginEval skill selection,
  eval-bench prompts, and the isolated prepare/run wrapper.
- tests/ - hermetic harness tests (no model calls, no network).
- results/ - committed per-run summaries only, assembled by hand from the raw
  runs under .runs/; tests/test_results.py checks them against BENCHMARKS.md.
- .runs/ - gitignored scratch homes, configs, plugin copies, transcripts.
- tasks/ - Track B task spec and validator; the task fixtures land in a
  follow-up PR.
- score/ - Track B scorecard (added later).

## Provider setup

The harness copies the env block from ~/.claude-deepseek/settings.json into a
scratch config, minus every key whose name matches TOKEN, KEY, SECRET,
PASSWORD, HEADERS or CREDENTIAL and the live session ids, and points
CLAUDE_CONFIG_DIR at the scratch directory. It keeps the live teammateMode
(in-process), sets permissions.additionalDirectories to the scratch teams,
tasks and pact-sessions directories only, and does not copy live
enabledPlugins or the PACT orchestrator agent setting.

The child process gets PATH, LANG, TERM and LC_* from the live environment and
nothing else, then the scratch settings env, then:

- ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
- ANTHROPIC_AUTH_TOKEN from ~/.claude-keys/deepseek
- scratch HOME, TMPDIR and CLAUDE_CONFIG_DIR

The token is never written to disk; the manifest records only the token file
path.

## Inspect-only first step

    bench/bootstrap.sh
    python3 bench/pact-bench prepare --out bench/.runs/external/probe --verify

That checks its inputs before writing anything, copies the current pact-plugin/
(tracked and untracked-not-ignored files, without tests/ and caches at any
depth), creates the scratch config, and runs a trivial headless provider check. Inspect
bench/.runs/external/probe/manifest.json and provider-check.json.

## Native eval and external tools

See bench/external/README.md for the coverage map, case inventory, PluginEval
commands, eval-bench prompts, and the current tool caveats.

## Limits

- Native eval and PluginEval are external behavior/quality diagnostics; they do
  not measure coding outcomes. Track B is the outcome benchmark.
- PluginEval labels its models with Anthropic names and reports list-price USD
  over a DeepSeek connection; use token counts, not USD, and treat judge scores
  as experimental.
- The PACT internal suite remains the first line of defense. Track A cases are
  chosen only for external gaps recorded in the coverage map.
