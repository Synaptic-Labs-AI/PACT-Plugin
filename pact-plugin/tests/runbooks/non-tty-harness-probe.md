# Runbook: Non-TTY Harness Support Live-Probe Acceptance Gate

**Purpose:** the pre-merge runtime-confirmation gate for the non-TTY harness
support change (#1658 root fix + #1309 dual-key resolution / stamp; #1019
verify-and-close), maintained under the current Agent Team contract (#1673).
The CI suite proves the resolver keying matrix, the stamp truth table, the
wiring through `session_init.main()`, and the prose pins — none of it can
prove the four platform-side facts this change's value rests on: (1) that
named, flagless Agent Team dispatch keeps the Task tools for a teammate's life
on the installed plugin build under test, (2) that the harness stamp actually
lands `non_tty` in a real cloud session and says WHICH input fired, (3) that
the full ceremony (teachback → work → HANDOFF → completion) runs with zero
path misses under the UUID keying with the plugin's own hooks firing, and (4)
whether hook subprocesses receive launcher-set env vars at all. A green suite
is NECESSARY but NOT SUFFICIENT. The dated #1658 measurements that motivated
the original dispatch workaround are historical evidence (§4), not current
dispatch guidance.

**Closure criterion (two-harness convergent verdict, pact-memory `b4fc2626`):**
the change is runtime-confirmed only when ARM 1 (local interactive TTY) AND
ARM 2 (cloud) both pass. One green arm certifies nothing about the other
launcher class — headless and interactive sessions have DIFFERENT team-store
shapes, so a headless null cannot certify an interactive behavior and vice
versa. When a refusal/no-op is proven by absence, run the matched positive
control beside it before concluding anything.

## §0 Preconditions and build

1. **Ask the user first.** Patching/switching the installed plugin is global —
   it changes every session on the machine, not just the probe session.
2. **Build via the platform's marketplace installer ONLY** (a hand-copied
   cache fails cache-miss). Switch builds via uninstall → install; a same-name
   same-version install no-ops. Verify the installed `gitCommitSha` equals the
   worktree HEAD under test. Restore afterwards = uninstall + `marketplace
   remove` + re-add the canonical marketplace + install the released version
   (the re-add is REQUIRED).
3. **Verify the installed files are byte-identical to the git worktree BEFORE
   backing up** — that yields two independent restore sources rather than one
   snapshot you have to trust.
4. **Compute the true IMPORT CLOSURE** of the touched hooks before backing up
   (a missing dependency can fail silently): `session_init`,
   `bootstrap_marker_writer`, `dispatch_gate`, `agent_handoff_emitter`,
   `handoff_ordering_gate`, `task_lifecycle_gate`, `task_claim_gate`, and the
   shared modules `task_utils`, `pact_context`, `session_journal` (schema
   registration), `paths`, `agent_handoff_marker`.
5. **Backup with checksums plus an explicit delete-list for files the probe
   ADDS.** Restoring the patched set does not remove an added file. Reuse the
   probe kit built and dry-run for PR #1661 (pact-memory `a7c6161b`) for the
   backup/restore mechanics rather than re-deriving them.
6. **Teammate is the SUBJECT, lead is the OBSERVER.** Forbid the subject from
   inspecting the instrumentation; a subject that goes looking contaminates
   the one clean run you get.
7. **Isolate any child `claude -p` a probe launches.** Run it with
   `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_REMOTE_SESSION_ID` and
   `CLAUDE_CODE_SYNC_SESSION_REFS` unset and `CLAUDE_CONFIG_DIR` pointing at a
   scratch directory. Otherwise the child inherits the parent session's
   identity and writes into the parent's task list (observed in #1666 Track
   A's P2 probe).
8. **Record the run identity before each arm.** Capture the launcher and
   display mode, `claude --version`, the installed build's `gitCommitSha`, and
   the live Agent schema. Keep the outputs with the run evidence. The live
   schema shows what a call can express; it does not by itself prove teammate
   lifecycle support.

## §1 ARM 1 — local interactive TTY, flagless named teammates

Verify that current named Agent Team dispatch does not regress local
interactive team formation on the installed build. Attach the §0 item 8 run
record to this arm.

**Procedure.** Local interactive terminal session (`claude --agent
PACT:pact-orchestrator`) on the probe build. `/PACT:bootstrap`, then spawn
two `general-purpose` teammates (`t1`, `t2`) in ONE message. Use named Agent
Team calls with `name`, `team_name`, `subagent_type`, `description`, and
`prompt`; do not add an execution-mode argument. Each teammate: one
`ToolSearch "select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage"`
load, `TaskList`, a ~20s wait (`python3 -c 'import time; time.sleep(20)'` —
the filler hook refuses bare `sleep`), then `SendMessage(to="team-lead")`
with its findings.

**PASS criteria (all must hold):**

| # | Check | Pass |
|---|-------|------|
| 1 | Registration unchanged | `teams/<team>/config.json` `members[]` contains `team-lead`, `secretary`, `t1`, `t2` |
| 2 | Concurrency unchanged | both teammates join/start within ~5s of each other |
| 3 | Task tools load | one `ToolSearch` select loads all four; `TaskList` works after |
| 4 | Sends delivered | both `SendMessage(to="team-lead")` reach the lead |
| 5 | Zero NEW hook advisories | compare hook stderr/warnings against a pre-change control session |
| 6 | Misstamp guard LIVE | `pact-session-context.json` `harness` is `""` (or transcript-mapped CLI) — NOT `non_tty` |
| 7 | No name split | the SessionStart prose team name == context `team_name` (a realigned short name in TTY) |
| 8 | Run identity recorded | §0 item 8 names the launcher/display mode, `claude --version` output, build SHA, and live Agent schema |

If any check fails, follow §3 in order. For a failure inferred from missing
output, run a matched positive control beside it on the same launcher and
build: the lead's `ToolSearch` + `TaskList` checks session tool availability,
and a `SendMessage` exchange with an already-registered teammate checks the
messaging substrate. A null without a successful matched control is
NOT-EXERCISED, not a platform finding. Do not conclude from a lone null or
respawn before diagnosis.

## §2 ARM 2 — one cloud run, flagless full ceremony

**Procedure.** The user's Claude Code on the web session (`CLAUDE_CODE_ENTRYPOINT=remote`
posture) with the probe build installed for that account. Attach the §0 item 8
run record. `/PACT:bootstrap`, then the lead dispatches a REAL `pact-preparer`
on a trivial Task A (teachback) + Task B (micro-consultation) pair. Use a named
Agent Team call with `name`, `team_name`, `subagent_type`, `description`, and
`prompt`; include
`ToolSearch "select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage"`
in the prompt and no execution-mode argument. The teammate loads the tools, confirms `TaskList`, then runs the
full teachback → wait (end turn) → work → `metadata.handoff` → notify cycle;
the lead accepts and completes.

**PASS criteria (all must hold):**

| # | Check | Pass |
|---|-------|------|
| 1 | Zero failed sends | teammate `SendMessage` delivered; `team-lead` unreachable → `main` fallback exercised AND the lead receives it (sanctioned construction + NOT-EXERCISED recording: §2.9) |
| 2 | Zero path misses | `session-journal.jsonl` records the `agent_handoff` event for Task B under the UUID-keyed store; the lead's raw `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/tasks/{team_name}/{taskId}.json \| jq .metadata.handoff` resolves, `{team_name}` read from `pact-session-context.json` |
| 3 | Zero false advisories | no teachback-skipped / completed-without-handoff advisories from `task_lifecycle_gate` / `handoff_ordering_gate` during the cycle |
| 4 | Stamp correct | `pact-session-context.json` `harness == "non_tty"` |
| 5 | Marker written | bootstrap-complete marker present |
| 6 | **MUST-RECORD: which stamp input fired** | see below |
| 7 | Env-forwarding diagnostic | one-shot capture executed and read (see below) |
| 8 | CLAUDE.md write-back skip-conditions | see below |
| 9 | Run identity recorded | §0 item 8 names the cloud launcher/display mode, `claude --version` output, build SHA, and live Agent schema |

Follow §3 for dispatch or messaging failures. As in ARM 1, prove any
absence-based result with a successful matched positive control on the same
cloud launcher and build; otherwise record NOT-EXERCISED rather than a
platform finding.

**§2.6 Stamp attribution (MUST-RECORD: which WRITER fired).** After the run,
read the session's `session_start` journal event and the on-disk context, and
record exactly one attribution — the WRITER that landed the stamp (and, for
Writer 2, the leg), not just the input source:
  - `writer1-transcript` — the event carries an `entrypoint` value in
    `session_init._NON_TTY_ENTRYPOINTS` (the code's mapping constant — read
    the symbol, never restate its values here, or the runbook drifts when
    the constant changes) and the
    stamp is set (session_init's SessionStart read fired first; any later
    Writer-2 write was refused by the monotonic guard — a same-value no-op).
  - `writer2-leg1-uuid-name` — entrypoint empty/absent, stamp set, and the
    aligned team name equals the full session UUID.
  - `writer2-leg2-config-less` — entrypoint empty/absent, stamp set, and the
    live team dir holds no config.json after a witnessed secretary join.
  - `none` — stamp missing: a RED finding, not a pass.
Both legs' evidence is on the substrate (context `team_name` vs session id;
`teams/<team>/config.json` presence), so the row names the writer without
code instrumentation. This closes the writer-attribution question in one
direction permanently; record it in the run row.

**§2.7 One-shot env-forwarding diagnostic.** On the PATCHED build only, add a
temporary line to `session_init` that writes the sorted `os.environ` key set
to `/tmp/<probe-prefix>-envkeys.txt` at SessionStart. Read it ONCE, then
DELETE the line and the file (one-shot diagnostic exemption per
`HOOK_INPUT_CONVENTIONS.md`; the log is read once by a trusted human and
removed). The question answered: do `CLAUDE_CODE_ENTRYPOINT` /
`CLAUDE_CODE_REMOTE` appear in a hook subprocess's env on this CLI version?
pact-memory `87176f36` (2026-04-02) predicts NO; this is the measurement on a
current build. Either answer is recorded; NEVER promote the env var to a
stamp input on the strength of a single observation without the architect and
lead re-opening the design.

*Amendment (#1666).* The architect and lead re-opened this ruling for
`CLAUDE_CODE_REMOTE` ONLY (`docs/architecture/session-start-lead-address.md`
§4, and the PR that shipped it). Scope: it is read solely through
`pact_context.is_cloud_session()`, for the cloud team-name resolver *default*
and the recorded `lead_address`. It is still NOT a harness-stamp input, and
`CLAUDE_CODE_ENTRYPOINT` stays excluded. Absent, or any value other than
`true`, gives the pre-#1666 behaviour. §2.10 is its measurement.

**§2.8 CLAUDE.md write-back skip-conditions (architect risk (i)).** Check
whether the project CLAUDE.md `Current Session` block was written during the
cloud session. If `CLAUDE_PROJECT_DIR` is unset in the cloud hook env, or no
`session_start` journal event fired, the `- Team:` line never converges.
REPORT the observed shape as a finding for the owning harness change; do NOT
pre-implement a fix during the probe.

**§2.9 Main-fallback check (check 1) — sanctioned construction.** The
`team-lead`-unreachable leg of check 1 is exercised by the cloud harness's
NATURAL unaddressability only: a teammate spawned before the platform's team
registry names the lead — e.g. the Task A dispatch landing before
`/PACT:bootstrap`'s team formation completes. If the run's timing never
produces it, record `main-fallback: NOT-EXERCISED` with the timing observed
in the run row; do not manufacture unreachability by other means (renaming,
deregistering, or editing team state to force the fallback).

**§2.10 CLAUDE_CODE_REMOTE at cloud SessionStart (#1666 items 1 and 2).** The
user starts a FRESH cloud session (`source=startup`) with the plugin loaded
from the branch under test. The session dir is
`{config_dir}/pact-sessions/<project-slug>/<session-id>/` (the `- Session dir:`
line of the project CLAUDE.md names it; `{config_dir}` is
`$CLAUDE_CONFIG_DIR`, else `~/.claude`). Read three things:
  1. `<session dir>/session-journal.jsonl` — the FIRST `session_start` event,
     the one with `"source": "startup"`: its `lead_address` and `team`
     fields. Only that event counts. Later `resume` events carry the full
     session id as `team` even on 4.8.7, because the resolver realigns
     after startup, so they prove nothing about this change.
  2. `<session dir>/pact-session-context.json` — its `team_name` and
     `lead_address`. A later prompt can rewrite `team_name`, so read it
     before the first prompt if you can, and treat the journal as primary.
  3. The first-turn SessionStart directive, the
     ``Use `<value>` wherever {lead_address} appears`` sentence. A cloud
     session shows no hook output, so either ask the lead, before it
     bootstraps, to quote that sentence from its SessionStart context
     verbatim, or read the SessionStart `additionalContext` in the session
     transcript.
On a startup fire nothing is carried, so the journal field is a direct reading
of `is_cloud_session()` inside the SessionStart hook process. Record one of:
  - `remote-visible` — `lead_address: "main"` and `team` equal to the full
    session id on the startup event (items 1 and 2 are live in the cloud);
  - `remote-absent` — `lead_address: "team-lead"` and `team: session-<id8>`.
    The code is still correct (fail-safe to today's behaviour), but items 1
    and 2 do nothing in the cloud: record it as a #1666 finding, not a revert
    trigger;
  - `mixed` — any other combination (for example `main` with
    `session-<id8>`, or `team-lead` with the full session id). On a fresh
    startup both fields derive from the one `is_cloud_session()` reading, so
    a mixed result is a BUG: record the event, the context file and the
    directive sentence verbatim and report it on #1666.
If the probe launches a child `claude -p`, isolate it first (§0 item 7).

## §3 Current Agent Team dispatch diagnostics (A–H)

Use this ordered tree for a dispatch or messaging failure in either current
arm. Record the branch, actor, and evidence in the run row. Roles are
explicit: the subject teammate self-helps in A and relays in G; the lead
observer owns B–F and the final blocker report. Do not start with a respawn,
and do not treat a harness label as proof of the cause.

A. **Load deferred tools.** The actor missing Task tools runs
   `ToolSearch "select:TaskCreate,TaskUpdate,TaskList,TaskGet,SendMessage"`
   when it has not already run. If a teammate's tools load, it continues
   without a respawn. If they remain absent, the teammate reports the exact
   missing tools and load result to its lead when messaging permits, then
   stops implementation work and idles while the lead continues below. The
   lead's own successful ToolSearch proves only the lead's tool surface,
   never the teammate's.
B. **Check the lead session.** The lead checks its own four Task tools. If
   they are missing, the lead fixes the session opt-in/settings and must
   start the required new session. Never respawn a teammate for this cause;
   the teammate does not restart the session.
C. **Compare the call with the live Agent schema.** The lead compares its
   dispatch with the live schema. Current teammate creation requires an
   interactive session, agent teams enabled, and a named, non-fork,
   non-isolation Agent call. The lead corrects `Task(...)`, a missing `name`,
   fork/isolation, or non-interactive `-p` usage. Never add a parameter absent
   from the live schema or substitute subagent-definition frontmatter for an
   Agent argument; the teammate does not rewrite the lead's call.
D. **Confirm named-teammate creation and addressability.** The lead uses
   platform spawn, roster, and messaging evidence, including the teammate's
   exact report. A missing `config.json` alone is not failure in a config-less
   cloud session. A named `-p` agent is an ordinary subagent, not evidence of
   team formation; the lead corrects team enablement/registration instead.
E. **Apply display-mode inheritance.** The lead checks the actual teammate
   display mode. In-process teammates follow the lead session. For split-pane,
   the lead checks `--tools`/deny rules and environment forwarding through
   `--settings`, then corrects launch configuration before replacement; the
   teammate does not change it. This is the documented diagnostic path, not
   split-pane runtime certification.
F. **Use the legacy-only branch only after A–E.** For an older non-TTY runtime
   exhibiting the #1658 symptom, only the lead follows the
   single operational legacy note in `pact-plugin/README.md`; the teammate never
   applies legacy dispatch. Do not copy its dispatch template into current guidance or treat
   schema presence alone as lifecycle evidence.
G. **Relay residual structured content.** If Task tools remain absent but
   `SendMessage` reaches `team-lead` or `main`, the teammate relays the
   teachback/HANDOFF content and the lead writes metadata. Acceptance/rejection
   ordering is unchanged; do not enter a respawn loop.
H. **Stop on messaging failure.** If `SendMessage` is also unavailable or
   unreachable, the teammate cannot relay and stops/idles. The lead reports
   the team-formation/messaging blocker with the evidence gathered above.

## §4 Historical evidence — #1658 on Claude Code 2.1.303 (2026-10-19)

**This dated region records measurements, not current dispatch guidance.**
The probes used PACT 4.8.3 and Claude Code 2.1.303:

- In cloud sessions, default asynchronous spawns lacked Task tools and could
  not address `team-lead`. The measured `run_in_background: false` workaround
  retained deferred Task tools after one `ToolSearch` load and through later
  resumes, including when the harness later made a spawn asynchronous.
- A real `pact-preparer` completed the teachback → work → HANDOFF → completion
  ceremony with the `main` fallback and zero hook warnings. Multiple such
  spawns ran concurrently, and a long-running probe retained Task tools.
- A separate local interactive TTY probe found the old argument was a no-op
  there on that build: registration, concurrency, `ToolSearch` + `TaskList`,
  and delivery to `team-lead` all passed.

These results are specific to the measured runtime. They do not certify
unattended 2.1.303 operation, split-pane behavior, or the unknown
2.1.304–2.1.309 schema boundary, and they set no minimum supported release.
For an older environment exhibiting the same symptom after diagnostics A–E,
the only operational compatibility path is the ordered legacy note in
`pact-plugin/README.md`, including its live-schema, post-spawn `TaskList`, and
full-ceremony guards. Schema presence is expressibility, not necessity; do
not duplicate the legacy dispatch template here.

## §5 Desktop arm / #1019 close-out

Default disposition (presented to the user at plan presentation): close #1019
on ARM 2's evidence plus #1309's posture-equivalence argument — both launchers
share the config-less UUID substrate and the TTY-gated team formation, and
#1019's deadlock mechanism is exactly what the resolver + stamp address. A
literal Desktop arm remains the alternative; if the user chooses it, run ARM
2's procedure and pass table unchanged on a Desktop session (the transcript
read must yield a value in `session_init._NON_TTY_ENTRYPOINTS`).

## §6 Revert and restore

Restore the checksummed backup; delete every file on the probe's delete-list;
clear `__pycache__` under the plugin hooks tree (a stale `.pyc` survives a
file restore); verify the restored tree against BOTH sources (backup
checksums AND `git main`); smoke-test the hooks (a SessionStart fires with no
stderr); uninstall the marketplace build, re-add the canonical marketplace,
reinstall the released version, and verify `gitCommitSha` matches the release.

## §7 Evidence recording

Add a `RUNBOOK_RUN_DATES.md` row only for an actual authorized probe; a static
documentation edit or test run is not a probe. Record each run per its row
schema. Capture for each arm: launcher and display mode, CLI version, build
SHA, live Agent schema, diagnostic branch/outcome (§3), journal path,
`harness` value, `entrypoint` raw value, stamp-input attribution (§2.6),
env-key presence booleans (§2.7), `config.json` absence/presence, marker path,
the startup `session_start` event's `lead_address` and `team` with the §2.10
outcome (`remote-visible`, `remote-absent` or `mixed`), and any advisory text
observed. These rows are the evidence #1309 / #1019 close on.
