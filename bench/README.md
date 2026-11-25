# PACT evaluation harness

Operational docs for the external PACT evaluation. This directory is dev-only:
it is outside pact-plugin/, is not shipped to plugin users, and does not change
plugin runtime behavior.

## Layout

- external/ - native claude plugin eval cases, PluginEval skill selection,
  eval-bench prompts, and the isolated prepare/run wrapper.
- plans/ - suite definitions.
- tests/ - hermetic harness tests (no model calls, no network).
- results/ - committed per-run summaries only; raw runs live under .runs/.
- .runs/ - gitignored scratch homes, configs, plugin copies, transcripts.
- score/, tasks/, runner/ - Track B outcome benchmark (added later).

## Provider setup

The harness copies the env block from ~/.claude-deepseek/settings.json into a
scratch config, overriding CLAUDE_CONFIG_DIR to the scratch directory. It keeps
the live teammateMode (in-process), replaces permissions.additionalDirectories
with scratch directories, and does not copy live enabledPlugins or the PACT
orchestrator agent setting. Provider env is set in the child process:

- ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
- ANTHROPIC_AUTH_TOKEN from ~/.claude-keys/deepseek
- ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN unset
- scratch HOME and CLAUDE_CONFIG_DIR

The token is never written to the manifest or committed.

## Inspect-only first step

    bench/bootstrap.sh
    python3 bench/pact-bench prepare --out bench/.runs/external/probe --verify

That copies the current pact-plugin/ (excluding tests/ and caches), creates the
scratch config, and runs a trivial headless provider check. Inspect
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
