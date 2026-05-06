# Dispatch-Gate Runbook (#662)

Manual fresh-session counter-test methodology for the dispatch-protocol
hardening landed in #662 (Commits 1-4). Hooks loaded in the session that
authors them do not fire — these checks MUST run in a fresh session
post-merge of the #662 PR.

The runbook validates four enforcement surfaces:

- **F22 hook-registration fidelity** — `matcher='Agent'` actually fires
  on `Agent()` invocations.
- **F18 Bash-marker-bypass** — `bootstrap_gate.is_marker_set` rejects an
  empty/forged `bootstrap-complete` file.
- **F7 advisory injection** — does PreToolUse `additionalContext` reach
  the dispatching agent's next turn? (Empirical; informs `PACT_DISPATCH_F7_MODE`
  flip from `warn` → `deny`.)
- **F25 sabotaged-import** — runtime gate-logic exception fail-closes
  with `hookEventName="PreToolUse"`.

After each execution, append a row to
[`RUNBOOK_RUN_DATES.md`](RUNBOOK_RUN_DATES.md) (`## 662-dispatch-gate.md`
section) with the date, operator, plugin version, sections passed,
F7-mode setting in effect, and any per-section observations.

Implementation references:

- `pact-plugin/hooks/dispatch_gate.py` — PreToolUse `matcher='Agent'`
- `pact-plugin/hooks/task_lifecycle_gate.py` — PostToolUse `matcher='TaskCreate|TaskUpdate'`
- `pact-plugin/hooks/bootstrap_gate.py::is_marker_set` — F24 SHA256 marker verifier
- `pact-plugin/hooks/hooks.json` — hook registration matchers
- `pact-plugin/hooks/shared/dispatch_helpers.py` — F4/F6 helpers + `F24_MARKER_VERSION`
- Architect: `docs/architecture/662-dispatch-protocol.md` §7(a) (F7 mode rationale), §10 (F24 verifier)

---

## Prerequisites

1. #662 PR is squash-merged to `main` and the new plugin version (≥ 4.2.0)
   is installed at `~/.claude/plugins/cache/pact-plugin/PACT/<version>/`.
2. Start a **fresh** session in a project that has the plugin installed.
   Do not reuse the session that authored the merge — its hook
   registrations are stale.
3. Confirm hooks are loaded:
   ```
   python -c "import json; d=json.load(open('$HOME/.claude/plugins/cache/pact-plugin/PACT/$(ls ~/.claude/plugins/cache/pact-plugin/PACT/ | tail -1)/pact-plugin/hooks/hooks.json')); \
     print([m['matcher'] for m in d['hooks']['PreToolUse']])"
   ```
   Expected: list contains both `"Agent"` and `"TaskCreate|TaskUpdate"`
   (the latter under PostToolUse, but the parse confirms structural
   integrity).
4. Confirm session journal is writable:
   ```
   ls -la ~/.claude/pact-sessions/<project>/<session-id>/session-journal.jsonl
   ```

---

## Section 1 — F22 hook-registration fidelity (`matcher='Agent'`)

**Goal**: confirm `dispatch_gate.py` actually fires when the harness
processes an `Agent()` tool call.

**Steps**:

1. In a fresh session with `/PACT:bootstrap` complete (so a team is
   resident), provoke a F1-trip dispatch — `Agent(subagent_type="pact-backend-coder")`
   with no `name=` and no `team_name=`.
2. Expect: tool call denied with `permissionDecisionReason` mentioning
   `"PACT dispatch_gate F1"` and the cheatsheet hint
   `Agent(subagent_type='pact-*', name='<role>', team_name='<session-team>', ...)`.
3. Inspect the session journal:
   ```
   tail -20 ~/.claude/pact-sessions/<project>/<sid>/session-journal.jsonl \
     | python -c 'import sys,json; \
       [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin if "dispatch_decision" in l]'
   ```
4. Expect: at least one event with `type="dispatch_decision"`,
   `decision="DENY"`, `f_row="F1"`.

**Pass criteria**:

- [ ] Spawn rejected with F1 message in `permissionDecisionReason`.
- [ ] Journal records `dispatch_decision` event matching the deny.
- [ ] `hookSpecificOutput.hookEventName == "PreToolUse"` in the deny
      payload (visible in transcript JSONL if available).

**Failure signals**: spawn proceeds (gate did not fire), no journal
event written, or `hookEventName` missing from deny payload.

### 1.1 Counter-test — mutate matcher to `'WrongName'`

Confirms the matcher value is load-bearing (not fired-on-everything by
some other registration).

1. In `~/.claude/plugins/cache/pact-plugin/PACT/<version>/pact-plugin/hooks/hooks.json`,
   edit the PreToolUse `matcher: "Agent"` block to `"WrongName"`.
2. Start ANOTHER fresh session (cache lives across sessions; the edit
   takes effect at the next process start).
3. Repeat the F1-trip dispatch from §1.
4. Expect: spawn proceeds (or fails for unrelated reasons — the gate
   does not fire because the matcher does not bind).
5. Inspect journal — no `dispatch_decision` entry for the failed dispatch.

**Pass criteria**: gate does NOT fire under the mutated matcher.

**Revert procedure**: restore `matcher: "Agent"` in `hooks.json` BEFORE
proceeding to Section 2 (otherwise §2-§4 produce no signal). Verify with
`grep -c '"matcher": "Agent"' .../hooks/hooks.json` ≥ 3.

---

## Section 2 — F18 Bash-marker-bypass (bootstrap_gate F24)

**Goal**: confirm an empty / forged `bootstrap-complete` marker is
rejected by `is_marker_set`'s F24 SHA256 sentinel check.

**Steps**:

1. In a fresh session, BEFORE invoking `/PACT:bootstrap`, inspect the
   session-dir path that the bootstrap_gate would consult:
   ```
   python -c "import shared.pact_context as p; p.init({}); print(p.get_session_dir())"
   ```
   (Run from `pact-plugin/hooks/` with `PYTHONPATH` set; or just note
   the path emitted in the bootstrap_gate's deny message.)
2. Forge an empty marker via Bash (the F18 attack surface):
   ```
   touch ~/.claude/pact-sessions/<project>/<sid>/bootstrap-complete
   ```
3. Run any command that would route through bootstrap_gate (the gate
   fires on `_BLOCKED_TOOLS`; an `Agent()` call is sufficient).
4. Expect: gate STILL denies because the marker file content is empty —
   F24 verifier (`is_marker_set`) requires a JSON body with
   `v == F24_MARKER_VERSION`, valid SHA256 sentinel bound to
   `(session_id, plugin_root, version)`.

**Pass criteria**:

- [ ] Forged empty marker is rejected.
- [ ] Deny message references bootstrap not being complete (or marker
      verification failure).
- [ ] Marker remains on disk; session is not silently elevated.

**Failure signals**: forged marker is accepted (regression — F24 broken
or `is_marker_set` reduced to file-presence check); session proceeds as
if bootstrap completed.

**Revert procedure**:
```
rm ~/.claude/pact-sessions/<project>/<sid>/bootstrap-complete
```
Then run `/PACT:bootstrap` to install a properly-stamped marker for the
remainder of the runbook.

### 2.1 Variant — malformed JSON

Repeat with the marker file containing the literal string `not-json` (or
a JSON object with `v=999`). Expect rejection — F24 enforces both schema
shape and `v == F24_MARKER_VERSION`.

---

## Section 3 — F7 advisory injection (empirical)

**Goal**: observe whether PreToolUse `additionalContext` actually
reaches the dispatcher's next turn. This is the calibration input for
flipping `PACT_DISPATCH_F7_MODE` from `warn` (default) to `deny`.

**Steps**:

1. With `PACT_DISPATCH_F7_MODE` unset (default `warn`), provoke an F7
   trip — dispatch a properly-named teammate but with a long inline
   prompt (≥ 800 chars) AND no `TaskList` / `task list` /
   `tasks assigned` / `check your tasks` phrase in the prompt.
2. Expect: tool call SUCCEEDS (F7 returns `WARN`, not `DENY`). The gate
   emits `additionalContext` per the harness contract.
3. Observe the dispatcher's next turn — does the dispatcher quote /
   reference / acknowledge the F7 advisory text?
4. Inspect the journal for the `dispatch_decision` event with
   `decision="WARN"`, `f_row="F7"`.

**Pass criteria** (advisory works):

- [ ] Spawn succeeds.
- [ ] Journal records `WARN` with `f_row="F7"`.
- [ ] Dispatcher's next turn references the advisory text (verbatim or
      paraphrased) — proves `additionalContext` was injected.

**Failure signals** (advisory silently dropped):

- Spawn succeeds, journal records the WARN, but dispatcher proceeds as
  if no advisory landed. This is the calibration trigger to flip
  `export PACT_DISPATCH_F7_MODE=deny` in the operator's shell rc.

### 3.1 Variant — `PACT_DISPATCH_F7_MODE=shadow`

```
export PACT_DISPATCH_F7_MODE=shadow
```

Restart session. Repeat the F7-trip dispatch.

**Pass criteria**:

- [ ] Spawn succeeds with no `additionalContext`.
- [ ] Journal still records `dispatch_decision` with `f_row="F7"`,
      decision `"ALLOW"` (the shadow path returns ALLOW so caller
      treats it as a normal allow; the journal entry is the calibration
      data).

### 3.2 Variant — `PACT_DISPATCH_F7_MODE=deny`

```
export PACT_DISPATCH_F7_MODE=deny
```

Restart session. Repeat the F7-trip dispatch.

**Pass criteria**:

- [ ] Spawn DENIED.
- [ ] `permissionDecisionReason` references F7 message.
- [ ] Journal records `decision="DENY"`, `f_row="F7"`.

**Revert**: `unset PACT_DISPATCH_F7_MODE` for subsequent sections.

---

## Section 4 — F25 sabotaged-import counter-test

**Goal**: confirm runtime gate-logic exception fail-closes with the
correct `hookEventName`.

**Steps**:

1. Run the existing CI test outside-of-session as the structural
   counter-test:
   ```
   cd pact-plugin && python -m pytest tests/test_dispatch_gate_smoke.py::test_f21_fail_closed_module_load -v
   ```
2. Expect: test passes. The fixture sabotages
   `shared/dispatch_helpers.py` via `shutil.copytree` + overwrite under
   `PYTHONSAFEPATH=1`, asserts exit code 2 + `hookEventName="PreToolUse"`
   + `permissionDecision="deny"`.
3. For an in-session smoke (optional, advisory only): a sabotaged
   import in a fresh session would require pre-corrupting the installed
   plugin cache, which contaminates the entire install — do NOT attempt
   this against the user's live cache. The CI test is the canonical
   signal; this runbook section exists to document the surface.

**Pass criteria**:

- [ ] CI test passes.
- [ ] No new sabotage attempts performed against the live install.

**Failure signals**: CI test failure indicates F21/F25 regression — file
a follow-up issue and block the runbook run.

---

## Section 5 — Acceptance summary

A successful run hits Sections 1, 1.1, 2, 2.1, 3 (mode-default), 3.1,
3.2, 4 — eight discrete checks. Append the result row to
`RUNBOOK_RUN_DATES.md` per the section header below.

If F7 §3 fails (advisory silently dropped), the mitigation is a config
change, not a code regression: set `PACT_DISPATCH_F7_MODE=deny` in the
project / user shell environment until the platform behavior changes.
File a tracking issue against the platform repo (not the plugin).

If §1, §1.1, §2, §2.1, or §4 fails: regression. Revert the offending
commit and file a P1 issue. The dispatch-gate is part of the SACROSANCT
governance surface; do NOT ship a release with these checks failing.

---

## Section 6 — Revert procedure (rollback the entire #662 surface)

If post-merge dogfooding surfaces a regression that cannot be
remediated in-place:

1. Tag the affected version on GitHub as `yanked` in release notes.
2. Bump plugin to a `4.2.1` patch that reverts:
   - `pact-plugin/hooks/dispatch_gate.py` (delete file)
   - `pact-plugin/hooks/task_lifecycle_gate.py` (delete file)
   - `pact-plugin/hooks/shared/dispatch_helpers.py` (delete file)
   - `pact-plugin/hooks/hooks.json` (revert PreToolUse `matcher='Agent'`
     hooks list to `[team_guard.py]` only; revert PostToolUse
     `matcher='TaskCreate|TaskUpdate'` to `[wake_lifecycle_emitter.py]`
     only)
   - `pact-plugin/hooks/bootstrap_gate.py` (revert F24 + F25 hardening
     from Commit 1)
3. Re-run this runbook against the patched version to confirm the
   surface is back to pre-#662 behavior. The expected result is that
   §1 / §2 / §4 all FAIL (the gates are gone) — this is the signal that
   the rollback is complete.
4. Document the regression in a follow-up issue with the journal
   evidence captured during the failing run.

The 4-file version dance applies to the rollback patch as well
(per pinned memory: every plugin-version bump gets a tag + GitHub
release).
