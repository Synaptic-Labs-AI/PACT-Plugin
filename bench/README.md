# PACT evaluation harness

Operational docs for the external PACT evaluation. This directory is dev-only:
it is outside pact-plugin/, is not shipped to plugin users, and does not change
plugin runtime behavior.

## Layout

- external/ - native claude plugin eval cases, PluginEval skill selection,
  eval-bench prompts, and the isolated prepare/run wrapper.
- tests/ - hermetic harness tests: external tools are replaced by fakes, so
  they make no model or provider calls.
- results/ - committed per-run summaries only, assembled by hand from the raw
  runs under .runs/; tests/test_results.py checks them against BENCHMARKS.md.
- .runs/ - gitignored scratch homes, configs, plugin copies, transcripts.
- tasks/ - Track B task spec and validator; the task fixtures land in a
  follow-up PR.

## Provider setup

The harness copies keys from the env block of ~/.claude-deepseek/settings.json
into a scratch config only when the name has an allowed prefix (ANTHROPIC_,
CLAUDE_CODE_, DISABLE_, ENABLE_, MAX_, MCP_) or is one of four named timeouts
and limits (API_TIMEOUT_MS and three BASH_* keys), is not credential-shaped
(TOKEN, KEY, SECRET, PASSWORD, HEADERS or CREDENTIAL, except the token-count
tuning keys matching ^(CLAUDE_CODE_)?MAX_[A-Z0-9_]*TOKENS$), is not a live
session id, and the value has no URL userinfo or credential-shaped URL query
parameter; manifest.json lists the dropped key names
in settings.dropped_env_keys. It points
CLAUDE_CONFIG_DIR at the scratch directory. It keeps the live teammateMode
(in-process), sets permissions.additionalDirectories to the scratch teams,
tasks and pact-sessions directories only, and does not copy live
enabledPlugins or the PACT orchestrator agent setting.

The child process gets PATH, LANG, TERM and LC_* from the live environment and
nothing else, then the scratch settings env, then:

- ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
- ANTHROPIC_AUTH_TOKEN from ~/.claude-keys/deepseek
- scratch HOME, TMPDIR and CLAUDE_CONFIG_DIR

prepare writes ANTHROPIC_BASE_URL (--base-url) and the model (--model) into the
scratch settings env, so the settings and the child agree; native-eval takes no
--model and uses the prepared model. The harness itself writes the token to no
file, and the manifest, prepare's stdout and its error messages carry no child output or
child-reported string, but files
that hold child output (provider-check.json, kept transcripts) can contain it: keep --out under bench/.runs/,
which is gitignored. prepare warns when --out is inside the repository but
outside bench/.runs/.

The harness starts each claude child (prepare --verify, native-eval) from a
fresh directory under --temp-root (default /tmp). Its real path must be outside
the repository with no CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md,
.claude/settings.json, .claude/settings.local.json or non-empty .claude/rules
directory in it or any parent. Sessions that claude plugin eval starts in its own scaffolds choose
their own directories.

## Inspect-only first step

    bench/bootstrap.sh
    bench/.venv/bin/python bench/pact-bench prepare --out bench/.runs/external/probe --verify

Run pact-bench with the bench venv: native-eval needs its PyYAML.

That checks its inputs before writing anything, copies the current pact-plugin/
(tracked and untracked-not-ignored files, without tests/ and caches at any
depth), creates the scratch config, and runs a trivial headless provider check. Inspect
bench/.runs/external/probe/manifest.json and provider-check.json.

## Native eval and external tools

See bench/external/README.md for the coverage map, case inventory, PluginEval
commands, eval-bench prompts, and the current tool caveats.

## Result schema (pact-bench/result/1)

A committed result is bench/results/<plugin version>/<run id>/summary.json with
a report.md beside it, both written by hand from the raw runs. The summary is a
JSON object with:

- schema: "pact-bench/result/1".
- run_id: the directory name; created_at: ISO-8601 UTC.
- assembly: how the file was produced (by hand, from which raw runs).
- plugin: name, version (the parent directory name), sha, tree_sha, source,
  dirty_paths.
- p1: the internal suite at that SHA: status and the check runs it came from.
- environment: model, judge model, provider URL, tool versions and pins.
- track_a: one object per external check (native_eval, plugin_eval,
  eval_bench, evolve, token_snapshot, orchestration_smoke); each has a status
  except token_snapshot, which carries its samples and median.
- track_b: status, and the tasks when there are any.
- limitations: what the run does not show.

Optional keys: coverage_map (the map version the run used), recommendations
and review.

bench/tests/test_results.py requires these keys and checks that the
BENCHMARKS.md row and report.md carry the summary's numbers: SHA, run id, date,
P1 status, the native-eval blocked/pass verdict, the PluginEval status and
composites, the spawn-token median and the smoke result; it checks the Track B column only when track_b.status is
"authored_out_of_tree". It also checks the probe case rows, Track B task rows,
the tree SHA, the token min/max, the whole-plugin composite and the smoke
counts in report.md, and scans BENCHMARKS.md and every file under bench/results
for token shapes (sk-, eyJ, gh*_, Bearer) and /home or /Users paths.

## Limits

- Native eval and PluginEval are external behavior/quality diagnostics; they do
  not measure coding outcomes. Track B is the outcome benchmark.
- PluginEval labels its models with Anthropic names and reports list-price USD
  over a DeepSeek connection; use token counts, not USD, and treat judge scores
  as experimental.
- The PACT internal suite remains the first line of defense. Track A cases are
  chosen only for external gaps recorded in the coverage map.
