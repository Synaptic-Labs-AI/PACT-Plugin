# PACT 4.8.38 Track A baseline

Run: 2026-11-25T2232Z
Plugin SHA: 1befafb314dd8124ad9225b87d0d5b064f0a6af6
Plugin tree: e668fbf505d74f4eb7b35a543cfa26e70e8b0f1b
Status: provisional on native behavior. P1 is green, the harness and smoke pass,
PluginEval completed, native eval is blocked by the host shell sandbox, and the
coverage map (1.0.1) has an independent review with 13/13 agreement on direction.
This report and summary.json are assembled by hand from the raw runs under
bench/.runs/ (gitignored); no command generates them.

## What ran

| Area | Result |
|---|---|
| P1 internal suite at evaluated SHA | green: full-suite and release checks succeeded on the GitHub commit checks |
| A1 isolated prepare + provider check | pass: plugin copy, scratch config, DeepSeek model resolved (deepseek-flash[1m]) |
| A2 native eval | blocked: eval Bash sandbox failed to initialize; probe produced no valid baseline case |
| A3 PluginEval | complete: 3 selected skills scored; whole-plugin result exploratory only |
| A4 eval-bench | deferred: 4 prompt/rubric entries committed, no judged run claimed |
| A5 evolve | skipped: conditional tool; no candidate change package above threshold |
| A6 token snapshot | 2 spawn samples: 52,112 and 86,606 first-request tokens; median 69,359 |
| A1 orchestration smoke | pass: exit 0, feature branch commit, 9 tasks, 3 valid HANDOFFs |
| P2 coverage map | version 1.0.1; independently reviewed, 13/13 agree, no flips (review note unpublished) |
| Track B | 6 tasks authored and validated out of tree; fixtures land in a follow-up PR; no outcome runs executed |

## Independent coverage review

The independent coverage reviewer agreed with all 13 keep/narrow/drop decisions and
recommended no flips. Required fixes were applied: native checks are marked
blocked/deferred, the bootstrap/impact-triage/memory-save graders were hardened,
the eval-bench review prompt now uses a diff with a seeded defect plus a
distractor, map metadata/citations were corrected, and the token measurement
sources were recorded. The review note is unpublished, so the agreement
cannot be verified from this tree. Because native eval is blocked, the review
does not certify a native score.

## Native eval probe: blocked, not passed

The native eval command itself exited 0, but the probe is not a baseline pass.
The eval Bash sandbox failed to initialize in the child sessions:

    Sandbox is required but failed to initialize:
    Failed to create bridge sockets after 5 attempts.

Root cause: the eval child TMPDIR made the sandbox bridge AF_UNIX socket path
exceed socat's 108-character limit. The prepared parent TMPDIR was 107 chars;
the child TMPDIR was 130 and the socket 164. The measured boundary is parent
TMPDIR <= 51 chars (50/51 pass, 52 fails). This is a host path-length bug, not
a PACT bug.

At measurement time the harness created a short TMPDIR/HOME outside /tmp
(default under HOME), asserted TMPDIR <= 51 chars and recorded temp_root; a
minimal sandboxed-Bash marker case then scored 1.0. The current harness accepts
any --temp-root short enough for the 51-character limit, /tmp included (see
bench/external/README.md). Native eval is still not valid PACT baseline
evidence: the eval sandbox denies reading/executing the plugin-under-test
scripts and denies writes to config/out, so bootstrap and memory-save remain
deferred. Attempt-based Bash graders can also pass on a failed sandbox call;
effect-based graders are required.

Cases that need shell steps could not run their required PACT commands. The
probe also exposed invalid case signals:

| Probe case | With | Without | Finding |
|---|---:|---:|---|
| bootstrap | 0.50 | 0.50 | TaskCreate 0x; the without arm matched the prompt text; Bash blocked |
| handoff-schema | 0.00 | 0.00 | max turns 8 in both arms |
| impact-edit | 1.00 | 1.00 | no plugin delta; the command is triage, not edit-impact |
| memory-save | 1.00 | 1.00 | saved regex passes without the plugin; Bash blocked the memory CLI |

A reframed no-shell impact-triage probe also measured zero delta (1.0 with and
1.0 without; the with arm hit max turns 20), so it is not valid baseline
evidence either. No native eval result is used as baseline evidence. The native
cases remain useful as a follow-up once the shell sandbox works or the
shell-dependent assertions move to the direct headless runner.

## PluginEval selected skills

| Skill | Composite | Badge | Main signal |
|---|---:|---|---|
| pact-agent-teams | 49.65 | none | triggering D+, orchestration F, output F |
| pact-handoff-harvest | 54.44 | none | triggering C-, orchestration F, output D- |
| pact-memory | 74.50 | silver | orchestration C, output D-; strongest selected skill |

The whole-plugin static score (51.17, no badge) is exploratory and was dropped
from Track A because internal structural oracles and CI lint are stricter.
PluginEval's model labels and USD are Anthropic-oriented; token counts are real,
USD is not DeepSeek billing. Judge scores are experimental.

## Orchestration smoke

The direct headless smoke passed:

- exit code 0, feature branch feature/hello-docstring, commit 4fcef53
- PREPARE skipped, ARCHITECT skipped, CODE started and completed, TEST skipped
- 9 tasks created; 3 HANDOFFs emitted; all 3 passed validate_handoff_schema
- no push, PR, or merge

Finding: both teammates reported that TaskCreate/TaskUpdate/TaskList/TaskGet
were absent after ToolSearch and relayed their handoffs through SendMessage; the
lead persisted task state. The relay path worked, but it adds overhead and a
durability dependency. This is an environmental/tool-surface issue, not a plugin
crash.

Env check: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 and
CLAUDE_CODE_ENABLE_TODO_TOOLS=1 are both present in the live settings and in the
scratch config used by the smoke, and both are in the prepare manifest env_keys.
Agent Teams was active (teammates spawned). ToolSearch for TaskCreate,
TaskUpdate, TaskList, and TaskGet returned "No matching deferred tools found",
and no TodoWrite/TodoRead calls were observed. TodoWrite/TodoRead do not exist
in Claude Code 2.1.332.

Hypothesis, not isolated by a probe: under claude -p, 2.1.332 does not
initialize the session team; named spawns fall back to ordinary background
subagents, and the four Task tools are reserved for true teammates. PACT's README already documents that a
named -p agent is an ordinary subagent, not team formation. A foreground spawn
with run_in_background:false does receive the four Task tools, and PACT
documents that only as a legacy non-TTY compatibility workaround. Interactive
PTY is the correct path for true Agent Teams.

## Token snapshot

Two subagent first-request samples: 52,112 and 86,606 tokens; median 69,359.
A trivial provider probe used 19,235 input and 60 output tokens. The sample is
small and reports tokens, not USD. The fixed teammate payload is a material
share of spawn cost and should be audited before adding more agents.

## Track B suite

Six tasks were authored and validated with bench/tasks/validate.py outside
this tree. Their fixtures land in a follow-up PR, so this validation is not
reproducible from this commit:

| Task | Class | Variety | Validation |
|---|---|---:|---|
| notes-api-crud | low | 5 | seed pass, hidden fail on seed, gold pass, seed after pass |
| invoice-rounding-bug | low | 5 | same |
| jwt-auth | medium | 9 | same |
| discount-strategy-refactor | medium | 10 | same |
| oauth-pkce-flow | high | 13 | same |
| job-queue-retries | high | 14 | same |

No Track B model runs have been executed. The next step is a pilot and then the
full PACT-vs-solo matrix with the scorecard; no coding-outcome claim is made.

## Recommendations

1. Keep the short-TMPDIR native-eval fix, but do not use shell-dependent
   bootstrap/memory-save cases as baseline evidence until the eval sandbox can
   read/execute the plugin scripts and effect-based graders show a positive
   delta. Do not report native eval scores.
2. Do not run Track B PACT-vs-solo in claude -p as if it were true PACT team
   mode. Use an interactive pseudo-TTY for the PACT arm, or explicitly label a
   foreground/relay compatibility mode and do not claim Agent Teams parity.
3. Prioritize prompting/orchestration quality for pact-agent-teams and
   pact-handoff-harvest; keep pact-memory's output-quality gap on the list.
4. Audit the fixed teammate spawn payload; 52K-87K first-request tokens per
   subagent is high for small tasks.
5. Run the Track B pilot and full matrix before making any outcome claim.

## Reproduce

These commands produce the native-eval and PluginEval raw runs under
bench/.runs/; summary.json and this report are then written by hand from them.
`--plugin-ref` pins the evaluated plugin to this baseline's SHA; without it,
prepare copies the current worktree.

    bench/bootstrap.sh
    bench/pact-bench prepare --out bench/.runs/external/probe --plugin-ref 1befafb314dd8124ad9225b87d0d5b064f0a6af6 --verify
    bench/pact-bench native-eval --manifest bench/.runs/external/probe/manifest.json --runs 3
    bench/pact-bench plugin-eval --manifest bench/.runs/external/probe/manifest.json --plugin-eval-dir <pinned-plugin-eval>

The other results came from manual steps, not commands. The token snapshot
follows the recipe in bench/external/README.md ("Token snapshot"). The
orchestration smoke has no committed recipe; what it checks is described in the
coverage map's orchestration-smoke entry in bench/external/README.md. The P1
status was read from the GitHub check runs named in summary.json.

## Caveats

- Native eval is blocked on the measuring host; no native score is claimed.
- Where the orchestration smoke and the token snapshot were started is not
  recorded. If they ran inside the repository, they loaded the project
  CLAUDE.md files (claude reads CLAUDE.md from the working directory and every
  parent), and the spawn-token numbers may include that text. The token-snapshot
  recipe now runs from a directory outside the repository and $HOME, and the
  harness's own child runs start from a clean temp directory.
- Eval-bench and evolve were deferred; no judged scores are claimed.
- Token sample n=2.
- The coverage map 1.0.1 is independently reviewed (13/13 agree, no flips), but
  native checks remain blocked/deferred and are not a native score.
- Track B was validated out of tree and is not executed; its fixtures are not
  in this commit.
