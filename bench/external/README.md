# PACT external evaluation — Track A operations and internal-coverage map

Operational guide for the Track A external checks: how to set them up, how to run
them, and why each one is (or is not) worth running given the PACT internal test
suite. Track A exists to cover **external behavior, external quality, version A/B,
and runtime-token** questions that the internal pytest suite does not prove. It is
not a second copy of that suite.

## Map metadata

| Field | Value |
|---|---|
| Map version | `1.0.2` (1.0.2 renamed `evalbench/review` to `evalbench/review-findings`, matching its prompt id; no decision changed. The 4.8.38 baseline was produced under 1.0.1.) |
| Decision hash | `sha256:644c44d2413e405da20d9083251a3de23c3ff5c08e424eefb7156caecdb44afb` — sha256 of the sorted, newline-joined rows `check id|decision|status` (no trailing newline), exactly as listed in the summary table |
| Generated | 2026-11-25 |
| Base commit | `1befafb314dd8124ad9225b87d0d5b064f0a6af6` (worktree HEAD at map time) |
| Plugin under test | `pact-plugin` v4.8.38 |
| Internal-suite fingerprint | `sha256:6f94098412764f598e6c312bf4e069c32564d00e54be32529e621cc8bed2d46c` — sha256 over `find pact-plugin/tests -type f | sort | xargs sha256sum` (775 files, 620 Python test files) |
| External tool pins | `claude` 2.1.332; PluginEval (`wshobson/agents`, `plugins/plugin-eval`) at `c96b5f6552dba32e4492307493ee613581430715` |
| Decision counts | **keep 6**, **narrow 4**, **drop 3** (13 checks mapped; 2 of 5 PluginEval skills and 3 whole checks dropped) |
| Reviewer status | reviewed 2026-11-25 by an independent coverage reviewer: 13/13 agree on direction, no flips; fixes required by the review were applied. The review note is unpublished, so the agreement cannot be verified from this tree. Native checks remain blocked/deferred. |

Re-baseline the map (bump the version and regenerate the fingerprint and decision
hash) whenever the plugin tree under evaluation changes, that is the
`pact-plugin/` or `pact-plugin/tests/` contents of the ref being evaluated. A
committed baseline records its own plugin SHA, so later commits on the branch
that carries the harness do not stale it. A stale map silently certifies the
wrong tree.

## Where Track A sits

- **Internal suite first.** `pact-plugin/tests/` is the first line of defense and
  already covers unit, integration, schema, static-byte, protocol-extract,
  scenario-pinned, and hook-entrypoint behavior. A Track A check that re-asserts
  any of that is out of scope, by construction (see
  [Dropped checks](#dropped-checks)).
- **Track A keeps only four gap classes:** (1) black-box behavior of a real
  headless session, (2) external quality that a lint/unit test cannot judge,
  (3) version A/B comparisons, (4) runtime-token evidence. It is not an outcome
  benchmark; coding outcomes belong to Track B.
- **No live model runs in CI.** Every proposed check here is validated against
  the internal suite *before* it is allowed to call a model, and the internal
  suite keeps running in CI unchanged.

## Layout

```
bench/
  bootstrap.sh                  # build bench/.venv (Python >= 3.12; hashed lock: pytest 9.2.0, pyyaml 6.0.3)
  pact-bench                    # CLI: prepare | native-eval | plugin-eval | tasks validate
  external/
    README.md                   # this file
    native-evals/evals/         # native claude plugin eval cases (prompt/graders or case.yaml)
    eval-bench/prompts.yaml     # eval-bench prompt + rubric inventory
    plugin-eval-skills.txt      # skill selection passed to plugin-eval
    runner/                     # prepare.py, native_eval.py, plugin_eval.py
  tests/                        # hermetic harness tests: no model calls, no network
  .runs/                        # gitignored scratch homes, plugin copies, transcripts
  results/                      # committed per-run summaries only
```

## Setup

Prerequisites: Python >= 3.12, `uv`, `git`, `claude` CLI (>= 2.1.332 for
`claude plugin eval`), and a local PluginEval checkout for the skill-judge runs.

```bash
# 1. Hermetic harness environment (verifier + hermetic tests only).
./bench/bootstrap.sh

# 2. PluginEval (external tool; not vendored here, not cloned by the runner).
git clone https://github.com/wshobson/agents.git <agents-checkout>   # if absent
cd <agents-checkout>/plugins/plugin-eval
git checkout c96b5f6552dba32e4492307493ee613581430715
uv sync --frozen --extra llm

# 3. Prepare an isolated run: copies current pact-plugin/ (without tests/ and
#    caches at any depth, and without git-ignored files) into scratch, builds a scratch Claude settings.json from the live
#    DeepSeek settings, runs a provider smoke check, writes manifest.json.
./bench/pact-bench prepare --out bench/.runs/external/probe --verify

# 4. Inspect before running anything that costs tokens.
jq '{plugin, provider, settings: {model: .settings.model}}' \
  bench/.runs/external/probe/manifest.json
jq '{exit_code, model_keys, is_error, result}' \
  bench/.runs/external/probe/provider-check.json

# 5. Harness self-tests (hermetic; run after every harness edit).
./bench/.venv/bin/python -m pytest -q bench/tests
```

The prepared copy is what external checks evaluate. Nothing under
`pact-plugin/` is written: native eval cases are injected into the **scratch
copy** (`<out>/pact-plugin/evals`) for the run and removed after it, and
native-eval results land under `<out>/native-eval/`. All three plugin sources
(the worktree, `--plugin-ref`, `--plugin-dir`) drop `tests/`, `__pycache__`,
`.git` and `.pytest_cache` at every depth.

`prepare` checks its inputs (a settings file holding a JSON object with an
object `env`, a non-empty token file, `--plugin-dir`, a clean source unless
`--allow-dirty`, and the `claude` binary with `--verify`) before it creates
`<out>`; `--plugin-ref` and `--plugin-dir` cannot be combined. `--force` replaces only an `<out>`
that holds a previous pact-bench output, and never `/`, `$HOME`, or the repo
root or one of its ancestors.

**Keep `--out` under `bench/.runs/`.** The provider token is in every child
process's environment, so any file that captures child output or session state
can hold it if the model or a tool prints the environment. Under `<out>` that
is `native-eval/kept-temp/`, `native-eval/runs/`, `report.json`/`report.html`,
`native-eval/stdout.txt`/`stderr.txt`, `plugin-eval/*.stdout.json`/`*.stderr.txt`,
`provider-check.json` and `config/` (session transcripts); `manifest.json` and
`settings.json` never contain it. Only `bench/.runs/` is gitignored. `prepare` warns on stderr when `--out` is
inside the repository but outside `bench/.runs/`; it does not refuse.

## Provider configuration (DeepSeek)

`prepare.py` copies the `env` block from `~/.claude-deepseek/settings.json`
into a scratch config, minus every key whose name matches
`TOKEN|KEY|SECRET|PASSWORD|HEADERS|CREDENTIAL` (case-insensitive) and the live
session ids, and overrides the session plumbing; the live config is never read
during a run.

- **Endpoint:** `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, or
  `--base-url`. `prepare` writes it into the scratch settings env (replacing any
  live value), so the settings env and the child env agree.
- **Credential:** `ANTHROPIC_AUTH_TOKEN` read from `~/.claude-keys/deepseek`
  (path overridable with `--token-file`); never written to disk, and the
  manifest records only the file path.
- **Child environment (allowlist):** from the live environment the child gets
  only `PATH`, `LANG`, `TERM` and `LC_*`; then the scratch settings env,
  filtered by the same credential rule; then `CLAUDE_CONFIG_DIR`, `HOME`,
  `TMPDIR`, `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`. No other live
  variable reaches it (no `CLAUDE*`, `GH_TOKEN`, `SSH_*`, `AWS_*` or
  `ANTHROPIC_API_KEY`). `plugin-eval` uses the same child environment.
- **Model:** `deepseek-flash[1m]` via `ANTHROPIC_MODEL` and the
  `ANTHROPIC_DEFAULT_*/CLAUDE_CODE_SUBAGENT_MODEL` aliases, all mapped to the
  same DeepSeek alias. The model is fixed at prepare time: `prepare --model`
  sets all five keys, and `native-eval` takes no `--model` or `--judge-model`;
  it passes the manifest's model as both. `prepare --verify` checks the requested model
  (any `[..]` suffix stripped) against the reported usage keys.
- **Scratch settings and env override:** `CLAUDE_CONFIG_DIR`, `HOME`, `TMPDIR`
  and the team/task/session dirs point inside the scratch `<out>/`, and
  `permissions.additionalDirectories` is exactly `<out>/teams`, `<out>/tasks`
  and `<out>/pact-sessions`. Live `enabledPlugins` and the PACT orchestrator
  agent setting are **not** copied; `teammateMode` is preserved from the live
  settings (currently `in-process`).
- **Do not "fix" the model warning:** the provider check prints
  `[claude-code:unrecognized_model]` on stderr yet exits 0 and reports a
  `deepseek-flash[1m]` usage key. Treat an exit code + `model_keys` mismatch as
  the failure, not that line. `<out>/provider-check.json` is written on every
  outcome, including a missing binary and a timeout.
- **Costs:** `total_cost_usd`/`costUSD` are Anthropic list-price estimates over
  a DeepSeek connection (`costBasis: unknown`). Report **tokens**, never USD.
- **Unattended caveat:** in-process teammate mode can stall when the lead waits
  for a teammate wake with nobody watching; use `--teammate-mode tmux` for long
  unattended runs (see `pact-plugin/reference/unattended-runs.md`).

## How to run the harness

### Native claude plugin evals (Track A black-box cases)

```bash
./bench/pact-bench native-eval \
  --manifest bench/.runs/external/probe/manifest.json \
  --cases-dir bench/external/native-evals/evals \
  --runs 3 --concurrency 1
```

The runner copies `bench/external/native-evals/evals/` into the scratch plugin as
`evals/` and runs `claude plugin eval <scratch plugin> --eval-dir evals
--ablation with-without --trust-plugin --no-publish --scaffold --runs 3
--json report.json --report report.html --output-dir runs --model <manifest
model> --judge-model <manifest model>`.
Case layout is the CLI's native format: `<eval dir>/**/case.yaml`, or
`prompt.md` + `graders/*.md`. Results: `<out>/native-eval/result.json` (written
on every exit once the manifest loads; a timeout records exit code 124),
`report.json`, `report.html`, and `runs/`. The child's `HOME` and `TMPDIR` are
`<temp-root>/pact-native-XXXXXXXX/{home,tmp}` (default `--temp-root` is
`$HOME`); `TMPDIR` must be at most 51 characters, checked before anything is
created. `--keep-temp` scaffolds are moved to
`<out>/native-eval/kept-temp/<run id>/`, any `evals/results/` the tool wrote to
`<out>/native-eval/plugin-evals-results/<run id>/` (the run id is the
`pact-native-XXXXXXXX` name), and the rest of the temp root is deleted. A
previous run's `report.json` and `report.html` are deleted at the start of each
run, so `result.json` names only reports this run wrote. A timeout kills the
eval's whole process group.

The cases directory must be non-empty; the runner refuses an empty suite. The
authored cases and the inventory they must meet are in
[Native eval cases](#native-eval-cases).

### PluginEval (whole-plugin lint + per-skill judge)

```bash
./bench/pact-bench plugin-eval \
  --manifest bench/.runs/external/probe/manifest.json \
  --plugin-eval-dir <agents-checkout>/plugins/plugin-eval \
  --skills-file bench/external/plugin-eval-skills.txt
```

Runs `uv sync --frozen --extra llm` (without the provider token), then
`plugin-eval score <scratch plugin> --depth quick` (whole plugin; static layer
only) and `plugin-eval score <scratch plugin>/skills/<skill> --depth standard`
for each skill in the skills file (static + experimental LLM judge). The skills
come only from `--skills-file` or, by default,
`bench/external/plugin-eval-skills.txt` (`~` is expanded); a missing or empty
file is an error.
Results: `<out>/plugin-eval/result.json`, always written. A command that times
out is recorded with exit code 124 and `timed_out: true` (its whole process
group is killed) and the run continues; the runner exits 1 if any command
failed, timed out or printed no JSON object. `complete` is true only when every
command was attempted. On an abort (missing `uv`, or a `uv sync` failure or
timeout) `error` and `aborted_command` are set and `not_run` lists the commands
never started; `sync` records the sync step (null with `--skip-sync`). PluginEval's judge layers are experimental and
not human-validated; model labels are Anthropic-oriented. Use the judge score as
a diagnostic, not a gate.

### eval-bench (prompt + rubric quality probes)

`bench/external/eval-bench/prompts.yaml` holds four prompt/rubric pairs
(`orchestration-plan`, `variety-assessment`, `handoff-contract`,
`review-findings`). There is no automated eval-bench runner yet; until one lands, run
each prompt headlessly in a prepared scratch environment, once with the PACT
plugin present and once without (the with/without baseline arm), for 3 runs each,
and grade blind to condition:

1. Prepare one scratch run per arm (`prepare` with no `--plugin-ref`, which
   copies the current worktree, vs. a no-plugin
   scratch config that does not register PACT).
2. For each prompt: `prompt.md` becomes the `claude -p --output-format json`
   input; working directory is a scratch workspace copy; capture the full JSON.
3. Grade each transcript against the rubric **without** the plugin name or arm
   label visible. Keep raw JSONL/transcripts under `bench/.runs/` and only
   summaries under `bench/results/`.
4. Report per-prompt rubric means, judge model, run count, and arm delta.

The rubric may test only behavior the internal suite cannot: choices and
reasoning quality, not field names or CLI output shapes.

### Token snapshot (runtime tokens)

```bash
./bench/pact-bench prepare --out bench/.runs/external/token-snapshot --force
# For each of 3 runs: run the standard smoke prompt headlessly and keep the JSON.
claude -p --output-format json --model deepseek-flash[1m] "<smoke prompt>" \
  > bench/.runs/external/token-snapshot/run-N.json
jq '{usage, modelUsage}' bench/.runs/external/token-snapshot/run-N.json
```

Use the scratch env produced by `prepare` (same `HOME`/`CLAUDE_CONFIG_DIR`/
provider env as the native-eval child) so the runs are comparable. Record
input, cache-read, cache-creation, output, and thinking tokens per run; report
the median and spread over 3 runs. If `agentburn`/`pai` are supplied, point
them at the transcripts under the scratch config's `projects/` directory and
record the tool version/commit in the result; **neither tool is installed on
this host** (verified absent from `PATH` and common checkouts), so the
`claude -p` usage fields are the fallback. This is distinct from the internal
spawn-byte pin (`test_spawn_overhead_benchmark.py`), which measures static body
bytes, not runtime tokens.

## Coverage map

### Summary

| Check | Closest internal evidence kind | Decision | Status | Reviewer |
|---|---|---|---|---|
| native/bootstrap | mocked, manual | **keep** | blocked: eval sandbox | agree |
| native/impact-triage | static, manual | **narrow** | blocked: no delta | agree |
| native/memory-save | unit/integration | **keep** | blocked: eval sandbox | agree |
| native/handoff-schema | integration/hook-entrypoint, static | **drop** | n/a | agree |
| plugin-eval/whole-plugin-static | static, CI | **drop** | n/a | agree |
| plugin-eval/skills-standard | static | **narrow** | ready (experimental) | agree |
| evalbench/orchestration-plan | mocked, manual | **keep** | ready | agree |
| evalbench/variety-assessment | mocked | **keep** | ready | agree |
| evalbench/handoff-contract | mocked, static | **narrow** | ready | agree |
| evalbench/memory-use | mocked | **drop** | n/a | agree |
| evalbench/review-findings | static | **keep** | ready | agree |
| token-snapshot | static | **narrow** | ready | agree |
| orchestration-smoke | manual | **keep** | ready | agree |

Evidence kinds are those of the *closest existing internal evidence*. Legend:
`mocked` = fixture-supplied payloads; `unit/integration` = real subprocess/DB
without model calls; `static` = text/structure pins; `manual` = runbook or
scenario not run in CI. "agree" means the independent reviewer agreed with the
decision direction; native checks remain blocked until they produce a positive
with/without delta under a working eval sandbox.

### Native eval cases

Current authored cases: `bootstrap`, `impact-triage` (output-only triage),
and `memory-save` (save then retrieve in one session).

#### native/bootstrap — keep

- **User-visible behavior:** a fresh PACT session runs `/PACT:bootstrap`: it
  identifies the session team, spawns the secretary once via single-task
  dispatch, surfaces banner/backlog flags and any paused/refreshed-work prompt,
  and the `bootstrap-complete` marker then unblocks Write/Edit/Agent dispatch.
- **Closest internal evidence:** `pact-plugin/tests/test_bootstrap_gate.py`,
  `test_bootstrap_marker_writer.py`, `test_bootstrap_prompt_gate.py`,
  `test_bootstrap_prompt_gate_forked_lead.py`,
  `test_bootstrap_prompt_gate_unrecorded_lead.py`,
  `test_harness_aware_bootstrap.py`, `test_team_split_bootstrap_marker.py`;
  live procedure in `pact-plugin/tests/runbooks/691-bootstrap-secretary-dispatch.md`;
  command structure pinned by `test_commands_structure.py`.
- **Evidence kind:** mocked (hook unit/integration with fake team config and
  session dirs) + manual (live runbook, run by hand, not CI).
- **External gap:** genuine. The internal arms mock the Agent/Task/team-config
  surfaces; no automated check proves that one real headless session completes
  the ritual and unblocks editing under the DeepSeek provider.
- **Decision:** keep — 1 case, assertions on marker existence, exactly one
  secretary spawn, banner line, and a Write attempt succeeding only after
  marker. No re-assertion of hook unit behavior.
- **Rationale:** highest-value black-box path; cheap; the marker is the
  load-bearing gate and its live acquisition is not proven internally.
- **Reviewer decision:** agree (see Reviewer status).

#### native/impact-triage — narrow (reframe)

- **User-visible behavior (proposed):** `/PACT:imPACT` on a small function edit.
- **Correction:** `imPACT` is **S3 blocker triage**, not impact analysis of an
  edit: it reads the blocker task, triages (redo/augment/rePACT/terminate/not
  blocked), writes the backlog item `blocked` then `active`, delegates a redo
  through the teachback-gated dispatch, and returns to the caller workflow;
  `impact_cycle_count >= 3` raises a META-BLOCK. A "small function edit" is not
  what the command does.
- **Closest internal evidence:** prose pins only —
  `pact-plugin/tests/test_commands_structure.py`,
  `test_dual_channel_acceptance_pinned.py`,
  `test_post_merge_chain_pins.py`, `test_backlog.py` (backlog `set`
  contract); dispatch protocol covered by `test_dispatch_gate*.py`; no test
  exercises triage decisions.
- **Evidence kind:** static (prose structure pins) + manual runbook
  `runbooks/662-dispatch-gate.md` (fresh-session counter-test methodology).
- **External gap:** genuine for triage behavior; the proposed "impact analysis
  of an edit" framing tests nothing PACT claims.
- **Decision:** narrow — reframe as `native/impact-triage`: seed a failing test
  so a dispatched specialist reports a blocker; assert the orchestrator creates
  and completes a blocker task, writes backlog `blocked` then `active`, and
  resumes the blocked phase (or delegates a redo). Drop the function-impact
  framing. 1 case.
- **Rationale:** keeps the only black-box path through a real blocker; drops a
  behavior the plugin does not have.
- **Reviewer decision:** agree (see Reviewer status).

#### native/memory-save — keep (narrowed to save + retrieve)

- **User-visible behavior:** an agent saves a project convention to PACT memory
  through the skill/CLI, gets a success confirmation with an id, and a later
  session retrieves it by search/query.
- **Closest internal evidence:** `test_memory_cli.py`,
  `test_memory_skill_save_fields_match_schema.py` (docs↔CLI allowlist parity),
  `test_memory_database.py`, `test_memory_models.py`,
  `test_setup_memory.py`, `test_memory_store_scope.py`,
  `test_embeddings_stale/unavailable/status-*.py`,
  `test_harvest_skill_text_guards.py`. All integration tests drive the CLI/DB
  directly; none makes a model choose the skill and fields.
- **Evidence kind:** unit/integration (real CLI subprocess and real SQLite; no model calls).
- **External gap:** genuine for skill-use and field selection; persistence
  mechanics are fully proven internally.
- **Decision:** keep — 1 case: save a stated convention, then a second headless
  session searches and returns the stored value. Assert a real saved record
  (SQLite/project memory), retrieval content, and no exit-2 rejection. Do not
  assert schema shapes beyond what `validate` already tests.
- **Rationale:** the documented-vs-allowed field divergence caught by the
  internal pin is exactly the class that a live skill invocation can re-open.
- **Reviewer decision:** agree (see Reviewer status).

#### native/handoff-schema — drop

- **User-visible behavior:** a specialist's final HANDOFF carries the canonical
  fields; the `SubagentStop` hook validates it and refuses malformed handoffs;
  the orchestrator harvests it.
- **Closest internal evidence:** `test_handoff_schema.py` (6 canonical / 5
  required fields, legacy aliases, validator silent arms),
  `test_validate_handoff.py` (hook stdin→exit-code/refusal shape,
  last_assistant_message preference, lossless fields),
  `test_validate_handoff_integration.py`,
  `test_validate_handoff_turn_end.py`, `test_handoff_census.py`,
  `test_handoff_content_discrimination.py`, `test_handoff_merge*.py`.
- **Evidence kind:** integration/hook-entrypoint (real stdin→exit path) + static.
- **External gap:** none for schema. The validator contract is pinned in both
  directions and exercised through its real stdin/exit-code path.
- **Decision:** drop as a standalone native case. The only genuine gap is
  whether a *real* specialist emits a passable handoff at phase end; that is
  asserted inside `orchestration-smoke` (validate_handoff exit 0 + harvest),
  so a separate case would duplicate both the internal schema tests and the
  smoke.
- **Rationale:** schema/validator evidence is already exhaustive; do not re-buy
  it with model tokens.
- **Reviewer decision:** agree (see Reviewer status).

### PluginEval

#### plugin-eval/whole-plugin-static — drop

- **User-visible behavior:** maintainer-facing lint of plugin structure
  (frontmatter, headings, references, portability, trigger phrases, bloat).
- **Closest internal evidence:** `test_skills_structure.py`,
  `test_commands_structure.py`, `test_agents_structure.py`,
  `test_plugin_manifest.py`, `test_plugin_manifest_parity.py`,
  `test_cross_references.py`, `test_lazy_load_cross_references.py`, plus
  `scripts/verify-*.sh` (protocol extracts, gate tree, scope integrity, task
  hierarchy, worktree protocol) and the CI lint gate
  (`scripts/verify-local-lint-gate.sh`).
- **Evidence kind:** static + CI.
- **External gap:** no user-visible behavior gap. Internal structure and CI
  checks cover PACT's known shape; generic trigger/bloat lint is not duplicated
  internally, but static-only scores are maintainer-facing and out of Track A
  scope. A one-off quick run exists (`~/pact-eval-quick.json`: static 0.682,
  no badge, 4×`MISSING_TRIGGER`, 1×`BLOATED_SKILL`) but it is a snapshot, not
  a repeatable behavior check.
- **Decision:** drop as a Track A gap check. The runner may still record the
  whole-plugin quick score as a provenance-tagged baseline in release A/B
  (cheap, no judge calls), but it must not be reported as external evidence of
  behavior.
- **Rationale:** static-byte/structure territory the internal suite already
  owns; keeping it would violate the no-duplication rule.
- **Reviewer decision:** agree (see Reviewer status).

#### plugin-eval/skills-standard — narrow (3 of 5 skills)

- **User-visible behavior:** skill description/trigger accuracy and content
  usability, judged by the PluginEval standard layer; surfaced as per-skill
  scores/anti-pattern flags.
- **Closest internal evidence:** skill structure/frontmatter
  (`test_skills_structure.py`), content pins per skill:
  `test_harvest_skill_text_guards.py` + `test_harvest_skill_routes.py`
  (handoff-harvest), `test_memory_skill_save_fields_match_schema.py` +
  memory tests (pact-memory), `test_team_registration_skill.py` + team pins
  (pact-agent-teams), `test_teachback_schema.py` +
  `test_teachback_reasoning_reconstruction.py` (pact-teachback),
  `test_test_run_scope_pins.py` + `test_check_unused_imports.py`
  (pact-coding-standards scripts).
- **Evidence kind:** static (text pins), no judged-content evidence.
- **External gap:** partial. A standard-depth judge measures skill usability
  and trigger clarity that no static pin can, but the judge layer is
  experimental and unvalidated against human labels.
- **Decision:** narrow to 3 skills — `pact-agent-teams`,
  `pact-handoff-harvest`, `pact-memory` — and label results experimental.
  Drop `pact-teachback` (the real gap is whether the gate forces
  understanding, which the smoke asserts, not how the prose reads) and
  `pact-coding-standards` (documentation/scripts, better covered by Track B
  coding outcomes; judge adds little).
- **Rationale:** keep the judge only where it prices a real external quality
  question cheaply; avoid paying 4 judge calls per skill for static-adjacent
  signal.
- **Reviewer decision:** agree (see Reviewer status).

### eval-bench prompts

#### evalbench/orchestration-plan — keep

- **User-visible behavior:** given a feature request, the orchestrator plans
  phases, tasks, blockedBy edges, specialist roles, and handoff/verification
  expectations.
- **Closest internal evidence:** `test_dispatch_gate*.py` (5 files) enforce
  the task-wiring protocol; `test_handoff_ordering_gate.py`,
  `test_task_*_gate*.py`; manual walkthroughs in
  `testing/scenario-standard-orchestration.md` and
  `testing/scenario-scoped-orchestration.md` (30-45 min runbooks, not CI);
  `runbooks/662-dispatch-gate.md`.
- **Evidence kind:** mocked (hook/gate unit + integration) + manual (scenario
  runbook).
- **External gap:** genuine. Internal tests prove the wiring is enforced when
  used; they do not prove a model given a natural request chooses phases,
  specialists, and blockedBy edges correctly.
- **Decision:** keep `orchestration-plan` as written (rubric: hierarchy,
  specialist use, handoff/verification), one prompt, blind-judged arm vs.
  no-plugin baseline.
- **Rationale:** this is the core orchestration-choice question and is exactly
  what a prompt-level probe measures well.
- **Reviewer decision:** agree (see Reviewer status).

#### evalbench/variety-assessment — keep (narrowed)

- **User-visible behavior:** the orchestrator scores novelty/scope/uncertainty/
  risk with task-specific reasons, computes the total, and routes to the
  correct workflow tier.
- **Closest internal evidence:** `test_variety_scorer.py` (deterministic score,
  boundary, routing), `test_variety_scorer_fuzz.py`,
  `test_per_dispatch_variety.py` (4-rationale stamp enforced),
  `test_variety_divergence.py`,
  `test_variety_rationale_contamination.py` (rationale must not recycle
  definitions), `test_dispatch_variety_deny_honor_probe.py`.
- **Evidence kind:** mocked/unit (deterministic scorer + gate enforcement).
- **External gap:** partial. Arithmetic and routing are fully proven; what is
  not is whether a model produces sensible, task-specific scores and reasons
  for a real task.
- **Decision:** keep `variety-assessment` but narrow the rubric: do not
  re-test arithmetic (internal scorer owns it); require four task-specific
  rationales, an internally consistent total, and a tier that follows the
  rubric. One prompt.
- **Rationale:** the contamination/parroting failure mode is model behavior and
  cheaply observable in a prompt probe.
- **Reviewer decision:** agree (see Reviewer status).

#### evalbench/handoff-contract — narrow

- **User-visible behavior (proposed):** show a valid HANDOFF for a backend task,
  canonical fields, concrete summary, verification evidence, risks.
- **Closest internal evidence:** `test_handoff_schema.py`,
  `test_validate_handoff.py`, `test_handoff_census.py`,
  `test_handoff_content_discrimination.py`, protocol pins in
  `test_post_merge_chain_pins.py`.
- **Evidence kind:** mocked + static.
- **External gap:** only the *content quality* half (concrete change summary,
  actual verification evidence, honest residual risk) is uncovered; field
  presence/schema is exhaustively covered.
- **Decision:** narrow the rubric to concrete verification evidence + residual
  risk/quality; drop canonical-field presence scoring (schema is internal
  evidence; `orchestration-smoke` asserts a real handoff passes the hook).
- **Rationale:** avoids paying a model to restate a schema the suite already
  enforces in both directions.
- **Reviewer decision:** agree (see Reviewer status).

#### evalbench/memory-use — drop

- **User-visible behavior:** save a convention to PACT memory and explain
  retrieval.
- **Closest internal evidence:** same memory evidence as
  `native/memory-save`, plus `test_memory_project_scope_*.py`,
  `test_project_memory_line_instructions.py`, `test_memory_cli.py`.
- **Evidence kind:** mocked.
- **External gap:** none beyond `native/memory-save`, which is the stronger
  black-box version (it actually persists across sessions rather than asking
  the model to describe memory).
- **Decision:** drop; remove the prompt from `prompts.yaml` or keep it only as
  a free local sanity prompt, not Track A evidence.
- **Rationale:** duplicate of a kept native case; two probes of the same gap
  add cost without evidence.
- **Reviewer decision:** agree (see Reviewer status).

#### evalbench/review-findings — keep (prompt added)

- **User-visible behavior:** `/PACT:peer-review` produces correct, independent
  review findings on a diff (including a seeded defect), with verification
  evidence rather than opinions.
- **Closest internal evidence:** all review evidence is static prose pins:
  `test_peer_review_cold_lane_pin.py`,
  `test_peer_review_freshness_pin.py`,
  `test_peer_review_greedy_pin.py`,
  `test_peer_review_independence_pin.py`,
  `test_peer_review_prose_pin.py`,
  `test_peer_review_verify_only_report_pin.py`; the command spec is in
  `commands/peer-review.md`. No test exercises review findings.
- **Evidence kind:** static.
- **External gap:** genuine and large: review correctness is a model-behavior
  question the suite never answers.
- **Decision:** keep — add one prompt to `prompts.yaml`: seed one real defect
  plus one distractor in a small diff; rubric scores defect detection,
  distractor non-flagging, and concrete evidence. Blind-judged vs. no-plugin
  arm.
- **Rationale:** highest-value external quality gap in the proposed list; the
  internal suite protects process (independence/freshness) but not output.
- **Reviewer decision:** agree (see Reviewer status).

### token-snapshot — narrow

- **User-visible behavior:** runtime token cost of real PACT sessions
  (per-run input, cache-read/creation, output, thinking), which is what a user
  actually pays.
- **Closest internal evidence:**
  `pact-plugin/tests/test_spawn_overhead_benchmark.py` (static byte ceiling for
  teammate spawn bodies, with orchestrator-persona leakage guard),
  `test_token_budget.py`, `test_token_budget_constants_coupling.py` (budget
  constants), `test_idle_preamble.py` (static preamble shape).
- **Evidence kind:** static.
- **External gap:** genuine: no internal check measures runtime tokens; the
  spawn-byte pin is a proxy only.
- **Decision:** narrow — 3 runs of the smoke prompt in the prepared DeepSeek
  environment, report median/spread of token fields from `claude -p
  --output-format json` (or `agentburn`/`pai` if supplied; neither tool is
  installed on this host). Tokens only; no USD; one configuration (current
  release) per run, with A/B reserved for `release.json`.
- **Rationale:** cheap, real, and completely unproven internally; the main risk
  is tool availability, hence the fallback and the "narrow" label.
- **Reviewer decision:** agree (see Reviewer status).

### orchestration-smoke — keep (minimal)

- **User-visible behavior:** `/PACT:orchestrate` on a tiny feature runs
  bootstrap→phase dispatch→specialist work→HANDOFF→completion with task
  hierarchy, journal events, and worktree isolation intact.
- **Closest internal evidence:** manual scenario docs
  `testing/scenario-standard-orchestration.md` (30-45 min) and
  `testing/scenario-scoped-orchestration.md`; live runbooks
  `runbooks/885-team-registration-smoke.md`, `runbooks/662-dispatch-gate.md`,
  `runbooks/691-bootstrap-secretary-dispatch.md`; automated hooks/gates only:
  `test_dispatch_gate*.py`, `test_task_*gate*.py`, `test_session_init*.py`,
  `test_team_registration_skill.py`, `test_handoff_ordering_gate.py`.
- **Evidence kind:** manual (scenario/runbook procedures; no CI execution).
- **External gap:** genuine. The only end-to-end orchestration evidence is
  human-run runbooks; nothing in CI runs a live PACT workflow.
- **Decision:** keep one minimal smoke: a two-file feature, `/PACT:orchestrate`,
  assertions on observable substrate only — bootstrap marker present, journal
  has phase `agent_dispatch`/`variety_assessed` events, feature→phase→agent
  task hierarchy with blockedBy edges, at least one specialist task completed,
  the specialist HANDOFF passes `validate_handoff` (exit 0) and is harvested,
  and a feature-branch commit exists. No model-output quality grading; cap
  runtime.
- **Rationale:** proves the integration that internal mocks and manual runbooks
  cannot certify; folded-in handoff assertion removes the need for the dropped
  standalone handoff case.
- **Reviewer decision:** agree (see Reviewer status).

## Dropped checks

Every dropped case/sub-case, with the reason. Nothing here should reappear in a
Track A run without a new gap analysis.

| Dropped item | Reason |
|---|---|
| `native/handoff-schema` (standalone case) | Canonical fields, required split, aliases, validator entrypoint, refusal shape, lossless fields all pinned in `test_handoff_schema.py` and `test_validate_handoff*.py`. Real-emission gap folded into `orchestration-smoke`. |
| `plugin-eval/whole-plugin-static` as a gap check | Internal structural/static oracles + CI lint are stricter; whole-plugin score is static-only and not user-visible behavior. May appear only as a provenance-decorated A/B baseline. |
| `evalbench/memory-use` | Duplicate of `native/memory-save`; the native case is the stronger cross-session black-box version. |
| PluginEval skill `pact-teachback` | Gate behavior (does teachback force understanding?) is asserted in the smoke; prose-quality judge adds cost without external evidence. |
| PluginEval skill `pact-coding-standards` | Documentation/scripts skill; coding quality is a Track B outcome question, not a skill-prose judge question. |
| Canonical-field presence assertions in `evalbench/handoff-contract` | Schema is already exhaustively pinned; retain only content-quality scoring. |
| "Impact analysis of a small function edit" framing in `native/impact-triage` | `imPACT` is S3 blocker triage, not edit-impact analysis; the framing tests behavior PACT does not claim. Reframed to `native/impact-triage`; output-only and blocked pending a working sandbox. |
| PluginEval USD/cost figures | `costUSD` is Anthropic list-price metadata over a DeepSeek connection (`costBasis: unknown`); token counts are the only reportable quantity. |
| Unit/schema/static-byte/protocol-extract checks generally | Covered by the internal suite and CI; excluded by the Track A gap rules. |

## What the internal suite already covers

Measured at the map baseline (`pact-plugin/tests`, 775 files, 620 Python test
files, ~456k lines, ~15.9k `def test_` functions collecting to roughly 28k
tests with parametrization):

- **Unit and integration behavior** for every hook (bootstrap gate/marker,
  dispatch gate, task claim/lifecycle gates, handoff ordering/validation,
  pin caps/staleness, worktree guard, merge guard, session init/end,
  teammate idle, missed-wake scan, structural wait, wait filler), including
  real stdin→exit-code entrypoints and subprocess seams. Many are organized as
  "pins" that fail on a load-bearing clause deletion or on a protocol drift.
- **Schema/contract tests:** canonical JSON, handoff schema, task metadata
  snapshot, marker schema, settings schema, teachback schema, memory models,
  journal frame gates, error envelopes.
- **Static-byte gates:** spawn-overhead body bytes
  (`test_spawn_overhead_benchmark.py`), token-budget constants, pin size/caps,
  import hygiene, byte-level identity pins, protocol extract verification
  (`scripts/verify-protocol-extracts.sh`), cross-reference and lazy-load
  parity.
- **Structural oracles:** skills/commands/agents frontmatter and content,
  manifest parity, pinned prose clauses, word/count guards.
- **Integration across seams** using real temp homes/dirs, subprocess hook
  entrypoints, sqlite stores, and in-process team/task fixtures — with model
  calls mocked or absent.
- **CI:** `.github/workflows/tests.yml` runs the unrestricted pytest suite as
  the required merge gate; `.github/workflows/fast-tests.yml` runs a whole-file
  fast subset on PRs; the local lint gate mirrors CI's import-hygiene legs
  (`scripts/verify-local-lint-gate.sh`). `testing/canary-checklist.md` is the
  3-tier PR verification contract, and its Tier 1 includes the four
  `scripts/verify-*.sh` checks plus the pytest suite.
- **Manual/live evidence (not CI):** four integration scenario runbooks in
  `testing/scenario-*.md`, 20 live-probe runbooks in
  `pact-plugin/tests/runbooks/` (bootstrap/secretary dispatch, dispatch gate,
  team registration, config dir/env precedence, missed wake, structural wait,
  non-TTY harness, refresh checkpoint, task-claim gate, v4.0.0 launch), with
  run dates tracked in `runbooks/RUNBOOK_RUN_DATES.md`. These are precisely the
  gaps Track A's black-box checks must cover; they are evidence that the gap is
  real, not evidence that it is closed.

## Limitations and caveats

- **Map scope:** Track A only. Track B (outcome benchmark in `bench/tasks/`)
  answers coding-outcome questions and is not mapped here.
- **External tools are external:** PluginEval (judge/Monte Carlo experimental,
  not human-validated; model labels and USD Anthropic-oriented) and `claude
  plugin eval` (bundled suite passing is not a security vetting; runs as the
  invoking user) are diagnostic, not certification.
- **No live runs in CI:** the internal suite remains the merge gate. Track A
  results are per-run diagnostics committed as summaries under
  `bench/results/`; raw runs are gitignored.
- **Small-n and variance:** 3 runs per case is a smoke-level sample, not a
  statistical result. Report spread, not just the mean; never compare two
  releases on one run.
- **Provider caveats:** DeepSeek alias resolution is verified by `prepare
  --verify` (exit code + `model_keys`), but the CLI may warn
  `[claude-code:unrecognized_model]`; the in-process teammate mode can stall
  unattended runs (use tmux mode for long runs); costs are estimates only.
- **Case inventory (authored):** `bench/external/native-evals/evals/` now has
  `bootstrap`, `impact-triage`, and `memory-save`; the intended inventory above
  is the contract those cases must meet. The authored impact-triage case is
  output-only triage, so blocker-task/backlog writes remain a gap. Re-run the
  harness self-tests and `prepare --verify` when cases change.
- **Native eval probe (2026-11-25):** the eval Bash sandbox failed to initialize
  (`Sandbox is required but failed to initialize: Failed to create bridge
  sockets`). Cases that need shell steps (`bootstrap`, `memory-save`) are
  deferred from the baseline. The old four-case probe measured zero with/without
  delta on every case: bootstrap 0.5/0.5, impact-edit 1.0/1.0, memory-save
  1.0/1.0, handoff-schema 0/0 (max turns). The reframed impact-triage probe also
  measured 0 delta (with arm hit max turns 20). No native case is valid baseline
  evidence; native eval is reported as blocked, not passed.
- **MCP mocks:** `claude plugin eval --mocks record` does not start the plugin's
  `pact-telegram` MCP server when it has no mock (`has no mock and is NOT
  started`). No native case may depend on its tools.
- **Reviewer status:** reviewed 2026-11-25 by an independent coverage
  reviewer: 13/13 agree on direction, no flips. Required fixes were applied. The
  review note is unpublished, so the agreement cannot be verified from this
  tree. Native checks remain blocked/deferred, so do not report a native eval
  baseline.

## Maintenance

Regenerate the fingerprint and decision hash when the branch under test changes,
when a case is added/removed, or when a check's decision changes. Keep the
decision hash computed over the same normalized rows; if a decision changes,
bump the map version and update this file in the same commit. Never let a Track A
result be reported under a map version older than the tree that produced it.
