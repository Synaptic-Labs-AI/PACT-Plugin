# Runbook: task_claim_gate.py Live-Probe Acceptance Gate (#961)

**Purpose:** the post-merge runtime-confirmation gate for the teammate-side
`task_claim_gate.py` PreToolUse hook shipped in PR #967 / v4.4.24 (merge
`54df9f4e`). The comprehensive L1/L2 unit + integration suite
(`tests/test_task_claim_gate.py`, 54 tests) ships in the PR and runs in CI. L3 —
that a REAL tmux teammate in a REAL Claude Code session, performing a REAL
Edit/Write/Bash tool-use, actually gets its unique owned-unblocked-pending task
**auto-flipped** by the live gate — CANNOT be driven by pytest (it needs real
platform PreToolUse delivery + real registry resolution + a real distinct-session
frame). This runbook is that gate. It mirrors the #923/#926 live-probe pattern: a
fix is "merged-but-not-runtime-confirmed" until a live session demonstrates the
behavior end-to-end. Issue #961 closure is gated on this logged PASS.

**Run date:** 2026-06-15 · **Probe agent:** test-engineer@pact-860c2595 (tmux) ·
**Verdict:** ✅ **PASS** (all 6 contract columns).

---

## §0 — Precondition confirmation

| Precondition | Status | Evidence |
|---|---|---|
| Installed plugin == v4.4.24 | ✅ | `.claude-plugin/plugin.json` → `"version": "4.4.24"` |
| Hook wired as PreToolUse `Edit\|Write\|Bash` | ✅ | `hooks/hooks.json:137` → `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/task_claim_gate.py"`; module docstring declares `matcher="Edit\|Write\|Bash"` (lead pre-verified the matcher) |
| Live hook == installed cache copy | ✅ | authoritative path read: `/Users/mj/.claude/plugins/cache/pact-plugin/PACT/4.4.24/hooks/task_claim_gate.py` (NOT a worktree/source copy) |
| teammateMode == tmux (distinct session) | ✅ | my `session_id` `fef3f483-6b54-4ec1-b81e-aa3c0da69ffd` (registry line) ≠ `leadSessionId` `860c2595-02b8-484a-b6ca-c638fff6bcfa` (team config) → genuine distinct-session teammate (the M2-relevant frame) |

**Mode:** tmux (N processes, N:1 session:team). The auto-flip (M2) path exists
ONLY under tmux — in-process collapses to one shared `session_id` so per-teammate
attribution is impossible. Column 1 is therefore live-exercisable here; column 5
(in-process) is the captured-frame/fixture cross-check.

---

## §1 — Firing mechanic (settled from source BEFORE triggering)

Read from the live cache copy (`task_claim_gate.py`):

- **Idempotent-via-state-change; NO once-per-session marker.** After a flip the
  task is `in_progress` (not `pending`), so the `mine` filter
  (`owner==confident_name AND status=="pending" AND _is_unblocked AND
  not-teachback AND not-self-exempt`, L489–497) goes empty → NO-OP on every
  subsequent fire (F1 idempotency comment, L499–502). `_atomic_claim` re-validates
  `status=="pending"` under read-back as the no-clobber guard (L364).
- **Consequence for clean capture:** the FIRST gate-firing Bash after the teachback
  Task A (#7) completes (which unblocks Task B #8) auto-flips #8 cleanly. My
  spawn-time `pact-team-registration` Bash + later read-planning Bash calls all
  fired the gate too, but while #8 was still `blockedBy=[7]` (unblocked predicate
  False) → `mine` empty → silent NO-OP, leaving #8 pending for a clean capture.
- **Discrimination (3 tiers, structural signal, never a mode flag):**
  Step 0 `pact_context.is_lead(stdin)` → `agent_type ∈ {"pact-orchestrator",
  "PACT:pact-orchestrator"}` → NO-OP (lead). Step 3 topology:
  `in_process = (session_id == leadSessionId from team config)`; in-process →
  generic advisory only, tmux → M1/M2 enforce.
- **Identity:** `session_registry.resolve(session_id)` self-lookup against the
  global `~/.claude/pact-sessions/.teammate-registry.jsonl`, members[]-validated;
  `None` name → identity UNCONFIDENT → never a typed flip.

---

## §2 — Contract verdict (6 columns)

| # | Contract | Method | Verdict | Evidence |
|---|---|---|---|---|
| **1** | tmux teammate, exactly ONE unblocked+pending owned task → registry-resolved **AUTO-FLIP** `pending→in_progress` (+ advisory) | **LIVE** (canonical #961) | ✅ **PASS** | See §3 |
| **2** | >1 candidate → advisory **LIST**, never a guessed flip | isolated harness (real binary, synth stdin) | ✅ PASS | exact `_claim_nudge_multi(["201","202"])` emitted; BOTH tasks stayed `pending` (no flip) |
| **3** | registry miss → **generic** advisory, never typed flip | isolated harness + incidental live | ✅ PASS | exact `_GENERIC_CLAIM_NUDGE`; no flip. Incidental LIVE: registration Bash hit registry-miss-with-no-candidate → silent NO-OP (F3 relevance-guard) |
| **4** | lead frame → **NO-OP** (no advisory, no flip) | isolated harness | ✅ PASS | `{"suppressOutput":true}`, no flip EVEN with 2 candidates present. **Nuance:** keyed on `agent_type ∈ LEAD_AGENT_TYPES` (Step 0), NOT on `session_id==leadSessionId` — *more* robust than the dispatch shorthand (lead NO-OPs regardless of session_id) |
| **5** | in-process teammate → generic attribution-free advisory, **NEVER auto-flip** | isolated harness (in-process frame) + un-skipped platform-fidelity test | ✅ PASS | exact `_GENERIC_CLAIM_NUDGE`, no flip EVEN with a resolvable identity registered; `test_T1_in_process_leg_generic_advisory_never_flips`, `test_T12_3_real_pretooluse_frames_platform_fidelity` (real captured frames, `tests/fixtures/role_frames.py`) PASS un-skipped |
| **6** | every error path → **exit 0** (fail-open, never deny) | isolated harness | ✅ PASS | empty / garbage / `[]` / `123` / `null` / `"hi"` stdin → `{"suppressOutput":true}` exit 0; unsafe team_name (`../etc`) → NO-OP exit 0; list-typed `agent_type` → exit 0, no traceback; output NEVER contains `deny` / `permissionDecision` |

**F1 idempotency (incidental LIVE):** the isolated-harness driver run was itself a
real Bash tool-use while #8 was already `in_progress` — NO advisory surfaced and
#8 was not re-flipped/altered (`status==in_progress`, `gate_writeback==true`
unchanged). Confirms re-fire → NO-OP once claimed.

---

## §3 — Column 1 LIVE evidence (PRIMARY — the canonical #961 scenario)

Per the dispatch **inversion**, Task B (#8) was deliberately NOT self-claimed; the
gate auto-flipped it. Verified by disk re-read (the gate is PreToolUse, not
SendMessage — no wake to observe).

- **BEFORE** (non-gate `Read` of `~/.claude/tasks/pact-860c2595/8.json`):
  `status == "pending"`; `metadata == {variety, agent_id}` — NO `gate_writeback` key.
  (#7 confirmed `completed` → #8 is the UNIQUE owned-unblocked-pending candidate.)
- **TRIGGER** (first deliberate gate-firing tool-use): `git status` (real tmux Bash).
- **GATE OUTPUT** (PreToolUse `additionalContext`, surfaced verbatim):
  > `PACT task_claim_gate: Auto-claimed your pre-assigned Task #8 (`pending → in_progress`) to preserve the lead's work-started signal. No action needed.`
  This is exactly `_auto_claimed_note("8")` (source L169–176) → the
  `len(mine)==1 → _atomic_claim → success` path.
- **AFTER** (non-gate `Read` + `TaskGet`):
  `status == "in_progress"`; `metadata.gate_writeback == true`.
  - `gate_writeback:true` is the forensic smoking gun — ONLY `_atomic_claim` sets
    it (source L380); it cannot come from a manual TaskUpdate.
  - Independent tell: the file was rewritten as **compact single-line JSON**
    (matches `json.dumps(task)` at L404, no `indent`), whereas the
    platform-written original was pretty-printed — corroborates the *gate* (not the
    platform) wrote the file.

Note the advisory channel surfaced here (the "ADVISORY-CHANNEL CAVEAT" in the hook
docstring is an open platform question); but the load-bearing evidence is the
direct FS flip + `gate_writeback`, which lands regardless of the advisory channel.

---

## §4 — Isolated cross-check harness (columns 2,3,4,5,6 + col-1 corroboration)

Method: the REAL installed hook binary run as a subprocess with
`CLAUDE_CONFIG_DIR` pointed at a throwaway fixture dir (isolated registry + team
config + tasks) — zero real `~/.claude` state touched — with synthesized
PreToolUse stdin. Expected advisory strings were **imported from the hook module
itself** (`_GENERIC_CLAIM_NUDGE`, `_claim_nudge_multi`, `_auto_claimed_note`) so
they cannot drift from source. Driver: `/tmp/tcg_probe_driver.py` (throwaway).

Result (all PASS):
```
PASS [col4-lead-noop] rc=0 out={"suppressOutput": true} noflip=True
PASS [col5-inprocess-generic-never-flip] rc=0 generic=True noflip=True
PASS [col3-registrymiss-generic] rc=0 generic=True noflip=True
PASS [col2-multi-list-never-flip] rc=0 match=True noflip=True
PASS [col1-iso-autoflip] rc=0 note=True before=pending after=in_progress gate_writeback=True
PASS [col6-failopen-exit0] empty/garbage/list/int/null/string→suppress exit0; unsafe-team→NO-OP; weird-agenttype→exit0 no-raise
OVERALL: ALL PASS
```

Unit-suite corroboration (`rtk proxy python -m pytest tests/test_task_claim_gate.py
-rA -q`): **54 passed, 0 failed, 0 errors, 0 skipped** (1 benign warning).
`test_T12_3_real_pretooluse_frames_platform_fidelity` PASSED **un-skipped**.

---

## §5 — Notes / discrepancies (no defects found)

This is a verification probe; no gate defect was found, so nothing was fixed.
Minor, non-blocking observations for the lead:

1. **Spec-vs-source nuance (column 4):** the dispatch describes the lead NO-OP as
   `session_id == leadSessionId`, but source keys it on `agent_type ∈
   LEAD_AGENT_TYPES` (Step 0 early-exit). This is *stronger* (a lead NO-OPs
   regardless of session_id). Behavior is correct; only the shorthand differs.
2. **Stale convention reference (doc):** the dispatch said to follow
   `tests/runbooks/960-backstop-seal.md`, which does not exist in this tree. This
   runbook follows the actual live-probe convention
   (`live-probe-template.md`, `923/924/926-*.md`). Also the dispatch's target path
   `.worktrees/.../tests/runbooks/` is missing the `pact-plugin/` segment; the
   real runbooks dir is `pact-plugin/tests/runbooks/` (where this file lives).
3. **TOCTOU (out of scope):** the lock-free read→`os.replace` window in
   `_atomic_claim` is a KNOWN/ACCEPTED limitation tracked by #968 — explicitly
   NOT part of this gate's fire/flip/advise contract and NOT a FAIL here.

---

## §6 — Overall verdict

✅ **PASS — all 6 contract columns verified** (column 1 LIVE; columns 2–6 via
isolated real-binary cross-check + un-skipped platform-fidelity test; F1
idempotency confirmed live). Recommendation: #961 may close on this logged
evidence.
