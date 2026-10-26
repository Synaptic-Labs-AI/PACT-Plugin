# Changelog

All notable changes to the PACT plugin, newest first. Each version's section
is that version's GitHub release: when a version bump reaches `main`,
`.github/workflows/release.yml` tags it and publishes the section.

Format: a section starts with `## v<version>: <short summary>`, where the
summary is printable ASCII, em dash or en dash only and the text after `## `
becomes the tag message, followed by `### Summary`, `### What landed` and
`### Why`. A section ends at the next `## v<digit>` heading. The file uses LF
line endings, has no HTML comments, closes every code fence, and puts no
`## v<digit>` line inside one. Every PR checks the whole file with
`.github/scripts/check-release-notes.sh`.

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
