# Runbook: `--fork-session` Context-Dir Fate Probe (case-1/2/3 discriminator)

> **DEV TOOL — `tests/runbooks/` only. This runbook is NEVER registered in
> `hooks.json`.** It is an operator-driven empirical probe, not a shipped
> hook (a `hooks.json` entry would run a subprocess in every consumer
> session). The operator runs the kill+relaunch out-of-process; the
> inspection here interprets the result and emits a self-verifying verdict.

## §0 — The question this probe settles (and why it gates a fix)

A durable reconciliation fix would key on a single "is this a restart?"
comparison of two raw values (a predicate the durable-fix branch proposes
under `hooks/shared/stale_session.py`; it is NOT in the shipped/HEAD source,
so the probe keys on the two raw values directly, never on the function):

```
S_live != S_persisted          (both present and valid)
  S_live      = input_data["session_id"]          (this running process)
  S_persisted = get_pact_context()["session_id"]  (the session_id FIELD read
                                                    from pact-session-context.json)
```

`S_persisted` is the `session_id` FIELD inside `pact-session-context.json`
(read at `pact_context.py` ~`:297`). The context-file PATH is keyed by the
**LIVE** session_id (`pact_context.py` `_build_session_path(slug, session_id)`
~`:235-239` -> `~/.claude/pact-sessions/{slug}/{session_id}/pact-session-context.json`).
So the comparison can only be TRUE if a **stale `session_id` FIELD** (== an
OLD id) sits inside the file at a **NEW-id-keyed PATH**, both present and
valid. There are exactly three ways the new-id path can look after a
`--fork-session` (which mints a new session_id):

| Case | What happened | `session_id` FIELD at new-id path | Predicate | Who repairs it |
|------|---------------|-----------------------------------|-----------|----------------|
| **1** | The forked session's `session_init` ran -> wrote a **fresh** context at the new-id path | == NEW (the live id) | **FALSE** | n/a — already correct; HEAD's per-prompt write-back already fixes team-name staleness |
| **2** | Cold start — `session_init` has not run yet -> **no file** | (absent) | **FALSE** | n/a — empty-SSOT fail-closed (#989/#992) |
| **3** | The platform **physically copied** the OLD session dir -> the new-id path WITH a stale `session_id` FIELD that **survives** to dispatch time | == OLD (stale) | **TRUE** | **the durable write-back live-keying fix (the gated commit)** |

**Case 3 is the sole scenario the durable fix repairs.** If case 3 never
occurs, the durable write-back is a no-op / mis-targeted and the originating
issue must be re-diagnosed. All circumstantial evidence points AWAY from
case 3 (source: `session_init` unconditionally rewrites the live id for a
lead frame; an on-disk corpus scan found 2242/2243 context files
self-consistent, the 1 mismatch a test fixture; the prior restart-probe
measured only that the id MINTS on fork, never the context-dir's fate). This
probe measures it directly, with a **leadness-robust** detector (see §3).

### Structural prediction (the probe VERIFIES this, never assumes it)

The platform owns `~/.claude/projects/{path-slug}/{session-id}.jsonl` (its
own conversation-transcript store). PACT owns
`~/.claude/pact-sessions/{basename-slug}/{session-id}/` (written ONLY by
`session_init`'s `persist_context`). These are two separate trees. The
structural prediction is therefore: `--fork-session` copies/forks the
platform's **own** transcript (`projects/{NEW}.jsonl` — expected and
**irrelevant**) but does NOT physically copy PACT's `pact-sessions/{OLD}/`
dir to `{NEW}/`; the forked `session_init` writes `pact-sessions/{NEW}/`
FRESH with `session_id == NEW` (**case 1**). The probe settles this by
checking for OLD-session fingerprints in the NEW PACT dir.

---

## §1 — Acceptance criterion (what a verdict means)

This is NOT a green-suite gate; it is a **single empirical verdict** that
drives a downstream PR decision. The probe is COMPLETE when the operator has
run the steps and the inspection prints exactly one of:

- **`VERDICT: CASE 1 (FRESH)`** — case 3 refuted. The durable write-back fix
  has no scenario to repair -> re-diagnose the originating issue / do NOT ship
  the durable commit as-is.
- **`VERDICT: CASE 2 (COLD-START)`** — no PACT context tree at inspection
  time; predicate FALSE; same downstream implication as case 1.
- **`VERDICT: CASE 3 (STALE COPY)`** — case 3 confirmed. The durable
  write-back fix is justified -> it unblocks. **Escalate to the §4 timing
  probe** to determine whether the stale field is durable or self-heals.
- **`VERDICT: INVALID`** — the built-in non-vacuity check failed (the detector
  could not see a simulated copy). Do NOT trust any verdict; fix the detector.

**Refuse a false PASS.** A wrong verdict mis-directs the entire PR decision.
The inspection is **parser-verifiable** (a single grep-able `VERDICT:` line +
per-dir OBSERVED counts) AND **self-checking** (a simulated copy must fire
CASE 3, or the run is INVALID). The team-lead RE-VERIFIES the evidence block
against the printed verdict before acting.

---

## §2 — SAFETY design (why this probe cannot corrupt the live session)

The probe forks a **disposable** session, never the live one. Hazards and
neutralizations:

### H1 — CLAUDE.md clobber (the dominant hazard)

A forked session's `session_init` rewrites the project CLAUDE.md
`## Current Session` block (`update_session_info`). If the fork ran in THIS
project dir, it would re-point the live session's gates at the fork's team.

**Neutralized structurally:** the disposable session is BORN in a
`mktemp -d` **temp project dir**, so its CLAUDE.md write lands on a throwaway
CLAUDE.md under the temp dir — never ours. Because it is born there (not
forked from ours), there is no "does `--fork-session` honor cwd" question: its
project resolves to the temp dir from birth, and the fork inherits it.

**Belt (team-lead-operated tripwire):** the team-lead snapshots the live
project CLAUDE.md `sha256` before relaying the operator steps and re-verifies
it is byte-unchanged after the run. A changed sha means isolation failed -> HALT.

### H2 — Faithfulness requires a LEAD frame (do NOT use plain `claude -p`)

The context-file write is **`is_lead`-gated**: `session_init` calls
`persist_context(...)` only `if frame_is_lead`, and `is_lead` is TRUE only for
`agent_type in {"PACT:pact-orchestrator", "pact-orchestrator"}`
(`LEAD_AGENT_TYPES`, `pact_context.py`). A PLAIN `claude -p` frame carries no
`agent_type` -> `is_lead` FALSE -> `persist_context` SKIPPED -> **no context
file at all**, and the probe would falsely read "no context".

**Therefore the SEED session MUST run as a PACT lead** (`--agent
pact-orchestrator`, the only mechanism that stamps a `LEAD_AGENT_TYPES`
`agent_type`). The full interactive TEAM construct is NOT needed.

### H3 — Fork-leadness and verdict trust (the load-bearing subtlety)

A real re-launched orchestrator IS a lead, so a `--fork-session` of it is a
lead frame whose `session_init` OVERWRITES the NEW-id context FIELD to NEW at
SessionStart. **A steady-state read of the context field therefore MASKS a
real copy** (it would see NEW and report a false CASE 1). §3 avoids this by
keying the verdict on the **OLD journal events** the fork's `session_init`
does NOT rewrite (see §3), and by forking both WITH and WITHOUT `--agent` so
the verdict is shown identical regardless of whether `--resume` inherits
`agent_type`.

### H4 — No live teammates: dispatch is PROMPT-gated, not `--agent`-gated

The stray-team concern conflates two separable things, and `--agent` causes
neither: (i) a stray empty **team DIR** — the platform births `session-<id8>/`
for ANY launched session regardless of `--agent`, so it is unavoidable either
way (and disposable-cleaned); (ii) live **dispatched teammates** — the real
risk, which is **prompt-gated**: no SessionStart hook calls `Agent()` (hooks
only WRITE context/markers and EMIT instructions); the orchestrator dispatches
ONLY when it ACTS on an orchestration-triggering prompt. **A trivial,
non-orchestrating prompt** (no `/PACT:`, no "orchestrate", no multi-step task
language) loads the persona for `is_lead` persistence WITHOUT reaching any
dispatch decision — verified empirically (the executed self-run spawned no
teammate and wrote no registry line). Should a future prompt ever trigger a
spawn, §5 still covers any `session-<id8>` team/tasks dir + registry line
(keyed by the disposable's own session_id, never the live id).

### Path lock — PRIMARY vs FALLBACK

- **PRIMARY** (use this): disposable `--agent pact-orchestrator -p` in a
  `mktemp -d` temp project dir + trivial prompt + full §5 cleanup. Faithful
  (lead frame persists the context) AND isolated.
- **FALLBACK** (only if `--agent` will not boot disposably — a
  headless-auth/config wall): fork OUR session with the team-lead's CLAUDE.md
  `sha256` snapshot -> run -> restore + verify as a hard belt.

---

## §3 — TIER 1: the leadness-ROBUST copy-detector (DO THIS FIRST)

**Why NOT a steady-state context-field read** — see §2/H3: a lead fork
overwrites the NEW-id field to NEW, masking a real copy.

**The robust copy signal: OLD-id traces in the NEW dir.** If `--fork-session`
physically copies `pact-sessions/{slug}/{OLD}/ -> {NEW}/`, the copied
`session-journal.jsonl` carries the OLD session's events, which embed
`session_id == OLD`. A fresh `session_init` only ever writes `session_id ==
NEW` and never appends OLD events. So **`grep` the NEW journal (and the NEW
context field) for the OLD id**: any OLD-id trace => COPIED (case 3 possible);
none => FRESH. This survives a lead fork's field-overwrite. The probe forks
both WITH and WITHOUT `--agent`, and runs a **non-vacuity check** (simulate a
copy; the detector MUST fire CASE 3) so a CASE-1 result cannot be a
silently-broken detector.

### OPERATOR STEPS (the team-lead relays this block VERBATIM to the user)

> Run in a normal shell on the machine running the live PACT session. Creates a
> throwaway session in a temp dir, forks it twice, inspects. Does NOT touch
> your live session. Prints a `VERDICT:` line.
>
> ```bash
> # ── Probe: does --fork-session COPY the PACT context dir? ─────────────────
> set -u
> OLD_ID="$(uuidgen | tr 'A-Z' 'a-z')"
> TMP_PROJ_RAW="$(mktemp -d -t pact-forkprobe-XXXXXX)"
> # PATH-DERIVATION CARE (verified by self-run): mirror two transforms or you
> # inspect the WRONG dir: (a) platform canonicalizes /var -> /private/var
> # (resolve via pwd -P); (b) PACT sanitizes the slug [^A-Za-z0-9_-] -> "_".
> TMP_PROJ="$(cd "$TMP_PROJ_RAW" && pwd -P)"
> RAW_SLUG="$(basename "$TMP_PROJ")"
> SLUG="$(printf '%s' "$RAW_SLUG" | sed 's/[^A-Za-z0-9_-]/_/g')"
> PSESS="$HOME/.claude/pact-sessions/$SLUG"
> echo "temp project : $TMP_PROJ"; echo "PACT slug : $SLUG"; echo "OLD session : $OLD_ID"
>
> # 1) Seed a disposable PACT LEAD at OLD_ID (writes pact-sessions/{slug}/{OLD}/).
> ( cd "$TMP_PROJ" && claude --agent pact-orchestrator --session-id "$OLD_ID" \
>     -p "Reply with the single word OK and take no other action." ) >/dev/null 2>&1
> OLD_CTX="$PSESS/$OLD_ID/pact-session-context.json"
> [ -f "$OLD_CTX" ] && echo "seed OK" || echo "NO SEED CONTEXT — see §6 FALLBACK"
>
> # 2) Fork TWICE: A) without --agent, B) with --agent. Both must agree.
> if [ -f "$OLD_CTX" ]; then
>   ( cd "$TMP_PROJ" && claude --resume "$OLD_ID" --fork-session \
>       -p "Reply with the single word OK and take no other action." ) >/dev/null 2>&1
>   ( cd "$TMP_PROJ" && claude --agent pact-orchestrator --resume "$OLD_ID" --fork-session \
>       -p "Reply with the single word OK and take no other action." ) >/dev/null 2>&1
> fi
>
> # 3) Robust copy-detector + non-vacuity check.
> python3 - "$PSESS" "$OLD_ID" <<'PY'
> import json, sys, pathlib, shutil, uuid
> psess = pathlib.Path(sys.argv[1]); old = sys.argv[2]
> def old_in_journal(d):
>     j = d / "session-journal.jsonl"
>     if not j.is_file(): return 0
>     return sum(1 for ln in j.read_text(encoding="utf-8", errors="replace").splitlines() if old in ln)
> def field_of(d):
>     c = d / "pact-session-context.json"
>     if not c.is_file(): return None
>     try: return str(json.loads(c.read_text()).get("session_id", ""))
>     except Exception: return None
> def classify(d):
>     oj = old_in_journal(d); f = field_of(d); oc = 1 if f == old else 0
>     print(f"  {d.name}/  ctxFIELD={f!r}  OLD-in-journal={oj}  OLD-in-ctx={oc}")
>     return "CASE 3 (STALE COPY)" if (oj > 0 or oc == 1) else "CASE 1 (FRESH)"
> if not psess.is_dir():
>     print("VERDICT: CASE 2 (COLD-START)  [no PACT context tree]"); sys.exit(0)
> new_dirs = [d for d in sorted(psess.iterdir()) if d.is_dir() and d.name != old]
> if not new_dirs:
>     print("VERDICT: CASE 1 (FRESH)  [fork created no PACT context dir]"); sys.exit(0)
> overall = "CASE 1 (FRESH)"
> for d in new_dirs:
>     v = classify(d); print(f"  VERDICT[{d.name}]: {v}")
>     if v.startswith("CASE 3"): overall = v
> # NON-VACUITY: simulate a physical copy; the detector MUST fire CASE 3.
> olddir = psess / old
> if olddir.is_dir():
>     sim = psess / str(uuid.uuid4()); shutil.copytree(olddir, sim)
>     sv = classify(sim); shutil.rmtree(sim, ignore_errors=True)
>     print(f"  NON-VACUITY[simulated-copy]: {sv}  (MUST be CASE 3)")
>     if not sv.startswith("CASE 3"):
>         print("VERDICT: INVALID — detector failed non-vacuity; do NOT trust."); sys.exit(0)
> print(f"VERDICT: {overall}")
> PY
> echo "── cleanup is the NEXT block (§5). Do NOT skip it. ──"
> ```

### Verdict map (what the operator/lead reads)

| Printed `VERDICT:` | Meaning | Downstream action |
|--------------------|---------|-------------------|
| `CASE 1 (FRESH)` | every NEW-id dir has NO OLD-id trace (journal + context). Fork wrote fresh; no copy. | **case 3 refuted.** Durable write-back has no scenario -> re-diagnose. "No NEW dir" is also CASE 1, not inconclusive. |
| `CASE 2 (COLD-START)` | No PACT context tree at the slug. Predicate FALSE (empty SSOT). | same as case 1 for the durable fix. |
| `CASE 3 (STALE COPY)` | some NEW-id dir carries an OLD-id trace (OLD id in NEW journal, or NEW field == OLD). | **case 3 confirmed.** Durable write-back justified. **Go to §4.** |
| `INVALID` | the non-vacuity simulated-copy did NOT fire CASE 3 -> detector broken. | do NOT trust; investigate the detector. |

> **Leadness-robustness + refuse-false-PASS, both baked in:** the detector keys
> on the un-overwritten OLD journal events (immune to a lead fork's field
> overwrite); it forks both with and without `--agent` (verdicts must agree);
> the non-vacuity check proves the detector can SEE a copy. Per-dir OBSERVED
> counts print BEFORE each verdict so the lead re-derives the label from raw
> evidence.

---

## §4 — TIER 1.5: the timing probe (ONLY if §3 printed `CASE 3`)

If §3 confirmed a copy, a stale field does not yet say whether it is
**durable** or a **cold-start window** `session_init` self-heals. Capture
three snapshots of the NEW-id context file's `session_id` field:

| Snapshot | When | Question it answers |
|----------|------|---------------------|
| **t0** | Immediately post-fork, BEFORE the forked `session_init` runs | Is a copied file already present with `session_id == OLD`? (the case-3 discriminator) |
| **t1** | After the forked `session_init`'s SessionStart write, BEFORE first gate `PreToolUse` | Did `session_init` OVERWRITE to NEW? `t1 == OLD` -> stale-forever; `t1 == NEW` -> self-heals in the cold-start window |
| **t2** | At the first gate `PreToolUse` | The operationally-relevant value the gate actually reads |

`t1` distinguishes "durable fix REQUIRED" (never self-heals) from "the
cheap-win dispatch-deny self-diagnosis already covers the transient window".

### Instrument: a SessionStart logging shim (DEV-only, reversible)

Reuse the atomic-tee idiom in the repo-root
`dev/install_session_start_logging_shim.sh` (`.tmp`-sibling + `os.rename`,
`try/except: pass`, `.preshim.bak` backup, single in-file marker). That script
lives in the PACT source repository and is deliberately NOT distributed in the
plugin package — if you are reading this from an installed plugin cache,
`dev/` is absent by design, not missing. Adapt it so
each SessionStart fire ALSO appends the firing PID, wall-clock, and — if the
NEW-id `pact-session-context.json` exists — its `session_id` field. That
yields `t0`; a post-prompt read yields `t1`/`t2`. The shim is a **conditional**
deliverable: do NOT install it unless §3 returns `CASE 3`.

---

## §5 — CLEANUP (mandatory — run regardless of verdict)

> **Cleanup is the highest-risk step.** A careless `rm` could wipe live state.
> This block runs in a **subshell with a hard format-guard**: it ABORTS (without
> touching the operator's shell) unless `$SLUG` matches `pact-forkprobe-*` and
> `$TMP_PROJ`/`$OLD_ID` are non-empty — so an empty/unset var (fresh-shell paste,
> failed `mktemp`) can NEVER widen an `rm` to `~/.claude/pact-sessions/`. Every
> target is an EXACT path (no bare globs) and a `guarded_rm` refuses any path
> with a protected live token. Set `PROTECT_TOKENS` to the live session id8 +
> project slug first. (`$ALL_IDS` = OLD + every minted NEW id, from §3.)

```bash
PROTECT_TOKENS=("<live-session-id8>" "<live-project-slug>")
ALL_IDS="${ALL_IDS:-$OLD_ID}"   # OLD + minted NEW ids (set during §3); fallback to OLD
(
  set -eu
  # HARD GUARD — empty/unset/wrong slug ABORTS before any rm (subshell-local).
  case "${SLUG:-}" in pact-forkprobe-*) : ;; *)
    echo "ABORT: SLUG is not a probe slug ('${SLUG:-<unset>}') — refusing all rm"; exit 2 ;; esac
  [ -n "${TMP_PROJ:-}" ] && [ -d "$TMP_PROJ" ] || { echo "ABORT: TMP_PROJ unset/missing"; exit 2; }
  [ -n "${OLD_ID:-}" ] || { echo "ABORT: OLD_ID unset"; exit 2; }

  guarded_rm() {  # absolute + no protected token; targets are already exact (no glob)
    local target="$1" tok
    case "$target" in /*) : ;; *) echo "GUARD: non-absolute '$target'"; return 0 ;; esac
    for tok in "${PROTECT_TOKENS[@]}"; do
      case "$target" in *"$tok"*) echo "GUARD: protected '$target'"; return 0 ;; esac
    done
    [ -e "$target" ] && { rm -rf "$target" && echo "removed: $target"; } || echo "absent: $target"
  }

  guarded_rm "$TMP_PROJ"
  guarded_rm "$HOME/.claude/pact-sessions/$SLUG"          # SLUG format-checked -> never bare
  # Platform transcript dir: EXACT encoded name (no glob); platform maps /._ -> -.
  PROJ_ENCODED="$(printf '%s' "$TMP_PROJ" | sed 's#[/._]#-#g')"
  guarded_rm "$HOME/.claude/projects/$PROJ_ENCODED"
  for ID in $ALL_IDS; do
    case "$ID" in *"${PROTECT_TOKENS[0]}"*) echo "GUARD: skipping protected id"; continue ;; esac
    ID8="$(printf '%s' "$ID" | cut -c1-8)"
    guarded_rm "$HOME/.claude/teams/session-$ID8"
    guarded_rm "$HOME/.claude/tasks/session-$ID8"
    guarded_rm "$HOME/.claude/teams/$ID"
    guarded_rm "$HOME/.claude/tasks/$ID"
  done

  # SHARED registry: surgical prune of disposable-keyed lines (NEVER blind rewrite).
  REG="$HOME/.claude/pact-sessions/.teammate-registry.jsonl"
  if [ -f "$REG" ]; then
    DISPOSABLE_IDS="$ALL_IDS" PT="${PROTECT_TOKENS[*]}" python3 - "$REG" <<'PY'
import json, os, sys, pathlib, tempfile
reg = pathlib.Path(sys.argv[1])
protect = set(os.environ.get("PT", "").split())
disposable = set(os.environ.get("DISPOSABLE_IDS", "").split())
kept, pruned = [], 0
for line in reg.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        sid = str(json.loads(line).get("session_id", ""))
    except Exception:
        kept.append(line); continue                 # unparseable -> keep
    if any(tok and tok in line for tok in protect):
        kept.append(line); continue                 # protected -> keep
    if sid in disposable:
        pruned += 1                                  # disposable -> drop
    else:
        kept.append(line)
if pruned:
    fd, tmp = tempfile.mkstemp(dir=str(reg.parent), prefix=".reg-prune-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + ("\n" if kept else ""))
    os.replace(tmp, str(reg))
print(f"registry: pruned {pruned} disposable line(s); kept {len(kept)}.")
PY
  fi
  echo "cleanup done (guarded)."
) || echo "cleanup subshell aborted (guard tripped) — inspect manually; nothing deleted."
```

**On the shared `.teammate-registry.jsonl`:** any disposable secretary's
`register()` line is keyed by the disposable's OWN `session_id` (never the live
id), so it is harmless and the prune removes it surgically (a JSON-aware line
filter, never a blind rewrite). If `register()` no-ops (in-process self-guard),
the prune removes nothing.

---

## §6 — Notes, contingencies, and the RUNBOOK_RUN_DATES row

> **EXECUTED RESULT — VERDICT: CASE 1 (FRESH), leadness-robust.** Run via the
> disposable self-run path on plugin v4.4.36 (Claude Code 2.1.185, macOS), with
> FULL raw-evidence capture. The disposable lead persisted a coherent OLD
> context; the fork was run BOTH without and with `--agent`.
>
> **`--resume` INHERITS `agent_type`** (empirically settled): the NO-`--agent`
> fork still wrote a fresh NEW-id context dir (`team_name: session-<NEW8>`), so
> its `session_init` treated it as a LEAD — i.e. a `--fork-session` of an
> `--agent`-created session is a lead frame. This is exactly the masking risk
> the steady-state field-read would hit; the journal-artifact detector below
> handles it.
>
> **Both real forks: CASE 1, journal-proven.** Each NEW-id dir's
> `session-journal.jsonl` contained ONLY its own `session_end`/`cleanup_summary`
> events (no `session_start` carrying the OLD id, OLD-in-journal=0). The
> SIMULATED `cp -R OLD/ -> SIMNEW/` (the non-vacuity control) by contrast showed
> the OLD `session_start` event copied in (OLD-in-journal=3, ctx FIELD == OLD) and
> correctly fired CASE 3. **A real platform copy would have left OLD events in
> the NEW journal exactly like the simulation; it did not.** So the forks created
> FRESH PACT dirs — the platform did NOT copy `pact-sessions/{OLD}/`.
>
> **CASE 3 is refuted; the durable write-back has no scenario to repair.** The
> verdict is robust to fork-leadness (the rejected v1 steady-state read keyed on
> the overwritable context field; this keys on the un-overwritten OLD journal
> events) and self-checked by the non-vacuity control. §4 timing shim not needed.

- **"No seed context file" at §3 step 1**: first confirm the PATH-DERIVATION
  (sanitized slug + canonical `/private/var`) — a path mismatch reads as a
  false "no context" (this exact bug was caught + fixed during the executed
  run). If the path is correct and the file is genuinely absent, `--agent
  pact-orchestrator` did not boot headless (auth/config wall) -> use the §2
  FALLBACK (fork OUR session + CLAUDE.md snapshot/restore).
- **`projects/{NEW}.jsonl` existing is NOT case 3.** The platform copying its
  OWN transcript is expected/irrelevant; the discriminator is OLD-id traces in
  the PACT `pact-sessions/` tree, which §3 keys on.
- **Record the run** in `RUNBOOK_RUN_DATES.md` under a
  `994-fork-session-context-fate.md` section: run date (UTC), operator, plugin
  version, the printed `VERDICT:` line, the per-dir counts, AND the non-vacuity
  result. Single-verdict probe (not a per-mode live-probe); tmux-vs-in-process
  does not apply (the context write is a lead-frame property, mode-independent).
- **Scope boundary:** this probe settles the context-dir-fate question (case
  1/2/3) ONLY. The end-to-end dispatch-DENIAL reproduction needs a real
  interactive Agent-Teams restart and is OUT OF SCOPE here.
```
