# Changelog

All notable changes to the PACT plugin, newest first. Each version's section
is that version's GitHub release: when a version bump reaches `main`,
`.github/workflows/release.yml` tags it and publishes the section.

Format: a section starts with `## v<version>: <short summary>`, where the
summary is printable ASCII, em dash or en dash only and the text after `## `
becomes the tag message, followed by `### Summary`, `### What landed` and
`### Why`. A section ends at the next `## vX.Y.Z: ` heading. To keep the
section split identical to what GitHub renders, the file uses a restricted
Markdown: LF line endings; no HTML comments or HTML blocks; no fenced code
blocks (no run of three backticks or three tildes anywhere; inline `code` is
fine); no collapsible details or summary HTML tags; no line starting with
`<` (write links as `[text](url)`); no lines made only of `=` or `-`; and
every heading that names a version is exactly `## vX.Y.Z: <short summary>` at
column 0. Inside a section use `###` or deeper; an `## [Unreleased]` heading
goes only above the newest version. Every PR checks the whole file with
`.github/scripts/check-release-notes.sh`.

## v4.8.17: a worktree lead writes project memory to the main checkout (issue #1653)

### Summary

A lead session started inside a linked git worktree now writes its project CLAUDE.md in the main checkout instead of planting one in the worktree. One rule decides where project memory lives: the writers, the memory sync and every advisory reader resolve the main checkout for a worktree session, and a plain session is byte-identical to before. When git cannot answer, no writer writes at all, and each reports the skip on its own channel.

### What landed

- One resolver: `project_memory_root` returns the main checkout root for a linked worktree and its input exactly as given otherwise. It verifies the candidate as a checkout root and requires the worktree to be listed, so a forged gitfile cannot redirect a write into another repository.
- Writers: `session_init` steps 3, 3b, 4 and 5b, the two bootstrap hooks, `realign_team`, `pin_marker_writer`, `archive_pin` and the memory sync writers all target the memory root, and each writer's targets and guards derive from that same root.
- `archive_pin` now refuses where it used to resolve: an unknown project memory root THAT EXISTS (git could not run, or a forged or unverifiable checkout), and a resolution that lands on a linked worktree copy, both raise its refusal type with a message. An archive that used to succeed against a document nothing writes now fails and says why.
- Readers: the display resolver and every advisory reader (pin-slot status, refusal reporting, stale-block directive, `track_files`, `check_pin_caps`, `claude_md_drift`, `match_project_claude_md`) resolve the same root, so no reader watches a file that nothing writes. On an unknown root the advisory readers stay silent rather than describe the worktree copy, and the pin-cap gate allows an edit with an advisory instead of passing it in silence.
- Drift cache: its format is version 2, and a version 1 entry is discarded and rebuilt on first read, so an upgrading session stops watching a stale worktree copy.
- Unknown roots: a git that cannot answer is distinguished from a git that answers not-a-worktree, and a declared directory that does not exist is could-not-run rather than a known root. Every writer refuses on the unknown case and reports it, rather than planting in a worktree during a git failure, and no session re-creates a declared directory to plant `.claude/CLAUDE.md` inside it.
- Removed declarations: the memory sync and `archive_pin` distinguish a declared directory that no longer exists from one git cannot identify. A missing declaration retargets through the working directory's checkout memory root, so a removed worktree still syncs AND archives the main checkout's document; `archive_pin` now writes where it previously refused. A resolution that still lands on a sibling worktree's own copy keeps the existing refusal, and an existing base git cannot identify keeps the unknown-root refusal.
- Removed worktrees: a session whose declared worktree was deleted also keeps its session record, and that record now resolves the main checkout for the memory sync, `archive_pin` and the pin-cap gate -- verified, once the worktree is gone, against the candidate main checkout rather than the worktree listing, because `git worktree prune` deletes both on the normal cleanup path. A record that names the declaration but does not verify is not followed, and a refusal on this shape says the declared directory no longer exists rather than describing a git failure.
- Skip messages: every channel that reports an unknown project-memory root -- the SessionStart skip, the bootstrap prompt gate and marker writer, `realign_team`, the memory-sync warning and `archive_pin`'s refusal -- names the cause that occurred, and a missing directory is named by its path. Three causes are distinguished: a directory that no longer exists, an existing directory git could not identify, and an existing directory whose git answer failed verification (a forged gitfile, or a separate-git-dir worktree with no identifiable main checkout). A bare repository's worktree stays a known root, so it prints no skip line and no unknown-root text.
- Worktree saves: an archive that refused for EVERY worktree session now succeeds. The memory save compared the main checkout a worktree session retargets to against the session record's worktree path textually and refused, on live worktrees as well as removed ones; a verified worktree record now proves the two are one project, while a symlinked spelling of the same directory still refuses. The refusal's remedy no longer tells a user to re-export a directory that has been deleted.
- Worktree records: a record is trusted only when the checkout it names CONTAINS the declared worktree, and the relationship is verified when the record is written -- while the declaration still exists and git can prove it, which is exactly what `git worktree prune` removes. One visible limit follows: a worktree created OUTSIDE the repository (`git worktree add ../feature`) and later pruned is no longer resolved from the record. After pruning it is indistinguishable from a record naming someone else's repository, so it falls through and is refused rather than redirecting a write into that repository. The pin cap cannot check the main checkout's document in that shape either: it allows the edit with an advisory naming the unknown root rather than passing it silently.
- Session state: a start whose session directory cannot be written now says so on the user-visible channel. The context file and the journal are reported separately, so a cold start that loses both names both; previously the only trace was hook stderr, which the platform does not show, while the project-document failure in the same run surfaced normally.
- Worktree listing: read NUL-separated so a path containing a newline survives, with an admin-gitdir fallback for a git without that option, and a raw toplevel read so paths ending in whitespace compare like with like.
- Path bytes: every git call that returns a path decodes with `surrogateescape`, so a project or worktree whose path is not valid UTF-8 classifies normally instead of failing session start. One such worktree no longer breaks the classification for every worktree of that repository.
- Staged names: a file whose name is not valid UTF-8 can no longer evade a suffix-based check. Two layers did it, and both are fixed. By default git C-quotes such a name, so the commit hook received `"x\377.env"` instead of the real bytes, it ends in a quote, and the `.env` test missed it; that mangled name was the defect under a default config. With `core.quotePath=false` the locale decode instead raised `UnicodeDecodeError` and skipped every scan. Git is now read NUL-separated and decoded with `surrogateescape`, so the real name reaches the checks either way; the same change keeps a staged binary file's content from aborting the scan.
- Commit scan: the secret scan no longer blocks its own maintenance. Its detection patterns and its test fixtures are written so the staged source contains no contiguous match, and the scan exempts those two files by EXACT repository-relative path -- a real secret in any other file under `tests/`, and any foreign file that merely carries one of those basenames, is still caught.
- Contract change: the pin-cap gate and the pin marker writer bind the managed document by IDENTITY -- the two canonical paths under the memory root -- not by containment, because PACT creates worktrees at `$REPO_ROOT/.worktrees/{branch}`, inside the root. For a linked worktree of a main checkout, in an untampered checkout, its own gitignored CLAUDE.md is therefore never the managed document, whether the worktree sits beside or inside the main checkout, with or without `CLAUDE_PROJECT_DIR` set. A worktree session writing main's file is still denied at the cap.
- Restored context names its source session neutrally and computes no liveness state, so a session killed before it recorded an end still restores.
- Journal tokens: the pin marker writer reports `noop_not_the_managed_document` for a target that is not the managed document, and `noop_memory_root_unknown` when the project memory root is unknown. The hook prints nothing; `pin_marker_write.outcome` is the only surface, and nothing in this plugin reads it.

### Why

The worktree copy was a shadow document: every resolver preferred it, memory syncs landed in a file deleted with the worktree, and the pin gates stopped recognising the real project file. An earlier change had stopped teammates doing this, and the lead case needed a decision about where that memory belongs; the answer is the main checkout. The obvious fix, skipping creation in a worktree, cannot work, because the session-resume writer creates the file on its own. Verification covers a census of the writers and readers, per-arm ablations that revert a production site, a 47-module symbol census run locally, and the full suite in CI on three interpreter versions.

## v4.8.16: correct SendMessage hook guidance and verify journal writes (issue #1684)

### Summary

The wake guidance now states that `SendMessage` fires `PreToolUse`, keeps `PostToolUse` explicitly unmeasured, and preserves the asynchronous-inbox prohibition on synchronous wake detection. Every command-template session journal write now has an executable physical-last-line read-back, backed by dynamic structural, mutation, and real-journal coverage.

### What landed

- Corrected the `SendMessage` hook claim in the completion-authority protocol and its byte-mirrored consolidated region, which now also says the `PreToolUse` hook runs before delivery and so cannot observe a wake. Seven hook comments that repeated the old claim are corrected too. Existing write/read behavior and event schemas are unchanged.
- Added `verify-last` read-backs to all 50 journal-write templates across ten command files. Each non-prune site runs `write && verify || { report; exit; }`, so a failed write or read-back stops the block loudly with or without `set -e`; prune-memory telemetry warns and continues on either failure. The `review_finding` read-back also binds `severity` and `finding`, and each command file says what to do when a read-back fails.
- `verify-last` requires a non-empty `exact.type`, rejects unknown spec keys, makes `absent` optional, reads only the journal's final line so one bad earlier byte cannot break later checks, and bounds comparison depth.
- Added per-site structural and behavioral coverage: every site must carry the guard, every site is run against a seeded stale event with a failing write, and the write census finds env-prefixed, `if`-wrapped and console-fenced writes. The retired-claim pin now scans every non-test file under `pact-plugin/` and catches paraphrases.
- Kept the cloud-startup test seam from reaching a checkout's real `CLAUDE.md`.

### Why

The old hook claim was false for `PreToolUse`. A journal write already exits 1 on bad JSON, an invalid schema or a write error, but a block that discarded that exit code could let an older event with the same keys pass the read-back. The guarded per-site read-backs and the stricter verifier make both contracts durable through the existing journal CLI, without changing event schemas or existing write/read behavior.

## v4.8.15: pact-memory search default 20, dash stdin sentinel, live dedup search (issue #1647)

### Summary

pact-memory `search` now returns 20 memories by default instead of 5, so recall-oriented consumers stop truncating at the old window. `save` and `update` accept `-` as the Unix stdin sentinel and report empty input as MISSING_INPUT naming the channel, instead of dying as INVALID_JSON. The handoff-harvest dedup search line now carries an invocation that actually runs, and tests pin the documented invocation shapes, the helper defaults, and the search window.

### What landed

- Default limit 5 to 20 everywhere the search surface exposes a default: `PACTMemory.search`, `graph_enhanced_search`, `find_similar_memories`, the CLI, and the pact-memory skill text (issue #1647).
- `-` as the stdin sentinel at both stdin sites (`save` and `update`, verbatim twins by house style): bare `-` reads stdin, and empty or whitespace-only input on either channel fails as MISSING_INPUT naming `--stdin` or `'-'`.
- Handoff-harvest dedup SOP fixed from `search --query "{topic}" --limit 5` (a flag argparse never accepted, so the step had never executed) to `search "{topic}"`, inheriting the new default 20; the store-access guard token moves to the live spelling so the guard net stays whole.
- Review remediations: docs and invocation parity pins, the dash sentinel documented in the skill, helper signature defaults pinned to the parser default, and the harvest span corrected.

### Why

A 5-result default truncated recall: agents searching for prior decisions saw only the top slice and re-derived work already remembered. Bare `save -` reported INVALID_JSON, misattributing an invocation error to the data. The dead dedup line meant duplicate memories were never actually checked before a save. The parity pins keep the documented invocations and the code from drifting apart again.

## v4.8.14: align Agent Team dispatch with the current schema (issue #1673)

### Summary

Agent Team dispatch now uses the current named, flagless Agent call shape at all 13 template sites. Recovery is evidence-ordered and role-aware instead of flag-first, while the measured #1658 workaround survives only in one guarded legacy note. The non-TTY runbook and structural pins now cover the same contract, including all review remediations for schema guidance, actor roles, malformed-dispatch diagnostics, and coverage controls.

### What landed

- Current dispatch: all 13 Agent Team call sites across seven templates omit the legacy `run_in_background` argument, with no Agent `background` substitute, new minimum release, or version-gated current path. Named teammates, `team_name`, and the deferred-tool `ToolSearch` preamble remain.
- Role-aware recovery: the A–H tree in the persona, both READMEs, agent-teams skill, and mirrored protocol text checks deferred tools, the lead session, the live Agent schema, teammate creation, and display mode before relay or a blocker report. The affected teammate self-helps in A and relays in G; the lead owns B–F and the final blocker report.
- Legacy and probe guidance: one plugin-README note gates the measured #1658 workaround on the older-runtime symptom, completed A–E diagnostics, live-schema support, post-spawn `TaskList`, and the full ceremony. The non-TTY runbook keeps current ARM 1 and ARM 2 flagless, adds the same diagnostics, and preserves dated 2.1.303 evidence outside current arms; historical references carry the approved scrub exemptions.
- Review remediations: the first-spawn summary now matches the full recovery tree, schema presence is expressibility rather than necessity, the malformed `Task(...)` versus `Agent(...)` example is restored, and actor ownership is explicit. Structural pins retain the dynamic 13-site census and add scoped current/legacy regions, mandatory-template and branch controls, and role-cardinality checks without truncating their controls.
- Preservation: Bash background-watch guidance, captured fixtures, `team_name`, and the protocol SSOT/extract mirror remain intact. No production hook, CI workflow, or runtime-permission surface changed.

### Why

The legacy Agent argument and flag-first recovery sent operators toward respawn loops instead of the actual session, schema, registration, display-mode, or messaging cause. The guarded note preserves the measured older-runtime workaround without presenting it as current dispatch guidance. Verification covers the static contract through focused and full suites, mutation and preservation controls, protocol-mirror checks, and review reruns; no new live-runtime, split-pane, or legacy-version certification is claimed.

## v4.8.13: wrap-up always follows a merge, and a merge-guard approval runs its command once (issue #1660)

### Summary

A merge is now always followed by `/PACT:wrap-up`, and the other weak hand-offs between PACT commands named in #1660 are closed. The merge guard's approvals were also tightened in both directions: fewer honest commands are refused, and one approval can no longer run its guarded command more than once.

### What landed

- Wrap-up owns everything after a merge. `/PACT:orchestrate` ends at peer review; `/PACT:peer-review` reads the PR state after a merge and, on `MERGED`, invokes wrap-up. Project post-merge steps (tags, releases) run alongside it, never instead of it.
- A PostToolUse reminder after the lead's successful `gh pr merge`, and a session-end warning when a session ends with a merged PR and no wrap-up. Wrap-up's `session_end` journal event carries `"source": "wrap-up"`.
- comPACT, imPACT and orchestrate return control to the workflow that invoked them; a remediation cycle that reopens a finding class counts as failed toward the imPACT escalation; teammate shutdown has one owner, and the workflow chain is stated once in `pact-workflows.md`.
- Merge guard, approvals: a token retires once GitHub reports the PR merged or closed, once the command's own success shows, or, when a later step fails, unless the output or a readable PR state shows the guarded command itself failed. After an approval the lead sees what it authorizes.
- Merge guard, one approval runs once: loops, `xargs`, `find -exec`, traps, recursive functions and other carriers that would run a guarded command several times are refused, while bounded single runs (`head -1 | xargs`, `find -maxdepth 0`, an exit-status `break`) still run on one click.
- Merge guard, fewer false refusals and fewer misses: interpreter code is refused only when it calls an exec primitive; more interpreter heads, runtimes, wrappers and substitution forms are read, and pushes whose remote, upstream or push routing changed before them bind their text.
- This repo's committed `.claude/settings.json` turns off commit and PR attribution for every session in it, cloud sessions included.

### Why

A merge with no wrap-up skipped the retrospective, harvest and propagation that only wrap-up runs, and a guard approval that could run its command twice turned one click into two destructive operations. Each guard change was checked by mutation and by an independent security re-verify against the real hooks.

## v4.8.12: backlog ref checks over REST when GraphQL is blocked, and releases cut by CI (issue #1667)

### Summary

In Claude Code cloud sessions the session proxy refuses GitHub GraphQL, so every ref in `/PACT:next` read `unverifiable` (#1667). `hooks/shared/backlog.py` keeps GraphQL as the fast path and adds REST, both as a fallback and for links to other repositories. This release also moves tagging and releasing into CI, because cloud sessions get HTTP 403 when they push a tag or create a release — which is why v4.8.9–v4.8.11 were merged without tags or releases.

### What landed

- Repo slug without GraphQL: `GH_REPO`, then the raw `origin` URL, then `gh repo view` run inside the project and used only when it answers github.com. Ambiguous remotes are refused and the remote URL is never echoed.
- REST fallback on a whole-request GraphQL error body: refs de-duplicated and checked over `gh api --hostname github.com`, up to 8 in parallel and at most 25, under the same 5 s deadline as GraphQL. Pending or capped refs read `unverifiable` with a reason; `GH_HOST` is removed from every child environment.
- REST bodies are converted to the GraphQL node shape, so `_ref_outcome` decides every bucket on both paths; one pure `_classify_ref` drives read, write and abandoned-work checks, with bounded issue numbers and linear-time link detection.
- Tests: tracker tests moved to `test_backlog_rest_fallback.py` behind a `_backlog_tracker_guard.py` that fails any real `gh` call; 155 → 214 tests, 100% line and branch coverage of the new tracker functions, 43 of 44 mutations caught.
- `.github/workflows/release.yml`: on every push to main it gives each `plugin.json` version bump since v4.8.8 an annotated tag and a release — tag message and notes from that version's section of `CHANGELOG.md`, only the highest version marked Latest. It is idempotent, stops on a tag pointing at the wrong commit or a missing or invalid `CHANGELOG.md` section, and authors as the owner of an optional `RELEASE_TOKEN`.

### Why

A tracker check that is always `unverifiable` in the cloud gives `/PACT:next` nothing to reconcile. The REST path was checked live in a cloud session: real refs, including other-repo and comment links, resolved in about 1 s with correct outcomes. Release automation closes the other cloud gap for good: the first run after this merge creates v4.8.9, v4.8.10 and v4.8.11, then v4.8.12 as Latest.

## v4.8.11: test suite passes in a root container

### Summary

On unmodified main the suite had 26 failures and 11 inline `geteuid` skips when run as root in a cloud container (uid 0, shallow clone, `PYTHONUNBUFFERED=1`), though the same tests pass in CI. Each is fixed by its cause; the change is almost entirely tests, plus one small production fix found in review.

### What landed

- A `requires_non_root` marker (`tests/fixtures/root_guard.py`, registered in `tests/conftest.py`) skips with a stated reason when euid is 0, with a child-pytest test faking uid 0 to prove the hook is wired — used only where a state can be created by file permissions alone.
- Where the production reader catches `OSError` or broader, tests now provoke the failure without chmod (a directory where a file is read, a file where a directory is created), reaching the same handler; the session-journal test gained one arm per error branch.
- `test_merge_guard_1178_cert.py`: the base-commit half moved into separate `@requires_history` tests that show as named skips in a shallow clone and run in CI.
- `test_memory_cli.py`: child processes drop `PYTHONUNBUFFERED`, restoring the buffered stdout they depend on.
- `hooks/shared/state_file.py`: a file descriptor leaked when `os.fdopen` failed after a successful `os.open`; it is now closed and the error re-raised unchanged, with a test.

### Why

A suite that only passes in CI cannot be the gate in a cloud session. Full suite as root: 22224 passed, 221 skipped, 0 failed; changed sites also pass as uid 65534; CI green on Python 3.9, 3.13 and 3.14. All 11 review findings (1 blocking) were fixed, each checked by a reviewer who did not write it.

## v4.8.10: recorded lead address and full cloud team name at session start (issue #1666)

### Summary

Two cloud-session papercuts, fixed at session start (#1666 items 1 and 2). In Claude Code cloud sessions the lead is reachable only as `main`, not `team-lead`, and every teammate used to find that out by failing a send first. And `session_init` recorded the team as `session-<id8>` until the next prompt's write-back silently corrected it to the full session UUID the platform uses.

### What landed

- `session_init` writes `lead_address` to `pact-session-context.json`: `main` when `CLAUDE_CODE_REMOTE` is `"true"`, `team-lead` otherwise. Allowlisted to `{team-lead, main}` at write and read, recomputed on every session start, carried forward by the other context writers, and logged on the `session_start` journal event.
- When `CLAUDE_CODE_REMOTE` is `"true"`, the startup team-name default is the full session id; `generate_team_name` is unchanged and the identity-match resolver stays authoritative.
- A `{lead_address}` placeholder is filled into all 13 spawn prompts and three Task B template lines; a new "Your Lead's Address" rule in `pact-agent-teams` defines what `team-lead` means as an address, so the existing literal sites stay byte-identical.
- `conftest` scrubs `CLAUDE_CODE_REMOTE`, so the suite behaves the same in a cloud container and locally.
- Runbook `non-tty-harness-probe.md` §2.10 adds the live cloud SessionStart check; a new §0 item says a probe launching a child `claude -p` must unset the session env vars.
- 274 edge-case tests across the env value matrix, all four session-start sources, carry-forward and the tamper allowlist.

### Why

Both defects cost every cloud session something before any work started. The fix re-opens the PR #1662 no-env-input ruling for `CLAUDE_CODE_REMOTE` only (user-approved): it is never a harness-stamp input, `CLAUDE_CODE_ENTRYPOINT` stays excluded, and an absent variable or any other value keeps today's behaviour — local sessions gain only the new address sentence and `"lead_address": "team-lead"`.

## v4.8.9: self-completion actor from harness data, lead-only completion rule and cloud roster (issue #1666)

### Summary

The self-completion advisory in `task_lifecycle_gate` never fired in any real mode: `trustworthy_actor_name` accepted only an `agent_id` of the form `name@team`, a shape no captured hook frame carries (in-process teammates send `a<name>-<16 hex>`, cloud subagents `a<16 hex>`, tmux teammates none). The actor is now resolved from harness-written data, and the gate enforces the actual policy — lead-only completion — instead of only catching self-completion (#1666 item 4).

### What landed

- `hooks/shared/actor_identity.py` (new): `resolve_actor()` takes the role from `agent_type` and the name from harness data only — team-config membership for in-process ids, `subagents/agent-<id>.meta.json` for cloud subagents. It never reaches the self-asserted session registry; `trustworthy_actor_name` is deleted and the new module joins the trust-partition guard.
- Rule B: a non-lead completing a non-exempt task with an owner gets `completion_disputed` plus a `self_completion` or `non_lead_completion` advisory, closing the peer-task and reassign-then-complete escapes.
- `is_self_complete_exempt`: a cloud actor-type surface keeps the secretary exempt where no team config exists; when the actor's name is known it must equal the owner, so `owner="secretary"` cannot be borrowed.
- Cloud roster: a lead-written append-only `teams/<team>/pact-roster.jsonl` (intent / bind / confirm per spawn) cross-checks names against `meta.json`; `dispatch_gate` records foreign spawns; local sessions read and write no roster.
- `lifecycle_decision` journal events carry the resolved actor (never `agent_id`); protocol text edited identically in `pact-completion-authority.md` and `pact-protocols.md`.
- 73 edge-case tests, three frames captured from a live cloud probe, and a live-replay regression test for the roster.

### Why

A gate that never fires is worse than none: it reads as coverage. Rule B shipped only after a live probe on real TaskUpdate frames confirmed that `agent_type` separates the lead from teammates — the lead's completion stayed silent, teammate and peer completions were flagged, and the secretary's own briefing was not. Known gaps are documented and pinned: tmux teammates are not covered, and the in-process id shape is still unmeasured.
