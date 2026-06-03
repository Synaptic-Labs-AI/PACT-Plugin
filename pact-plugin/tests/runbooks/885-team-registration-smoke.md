# Team-Registration Register-Delivery Smoke (#885)

End-to-end operator runbook proving that a spawned teammate **actually records
its `name@team` to the registry as its first action** — the empirical gate the
LEG-4 NO-GO taught us to require. Static tests (presence/liveness/frontmatter)
prove the directive is *present and points at a live skill*; they cannot prove
it *fires*. Prior spikes proved firing with a `printf` proxy and proved the
registry module self-contains from `/tmp`, but the
agent-def/spawn-prompt → **real-register** end-to-end was never run. This
runbook is that run. **Do not declare the fix done on tests alone.**

The smoke validates, for BOTH a standard teammate AND the secretary:

- The spawned teammate's **first tool action** is
  `Invoke Skill("PACT:pact-team-registration")` (before `TaskList`).
- The loaded skill body runs the register `Bash` command (pre-teachback, via
  the skill's teachback-`Bash`-exempt note).
- The global registry file
  `~/.claude/pact-sessions/.teammate-registry.jsonl` gains a line
  `{"session_id": <teammate_sid>, "value": "<name>@<team>"}` for that
  teammate — i.e. the registry is **non-empty** afterward (the exact
  ground-truth check that caught the NO-GO: empty registry after 2 real
  teammates + 2 probes).
- **Secretary path is its own case**: the secretary has a custom briefing-first
  On-Start and is teachback-exempt; assert it self-supplies `secretary@<team>`
  and registers **before** its session briefing `SendMessage`.

After each execution, append a row to
[`RUNBOOK_RUN_DATES.md`](RUNBOOK_RUN_DATES.md) under a section header matching
this runbook's filename.

---

## Why an overlay is required

The new skill (`skills/pact-team-registration/`), the new leaf module
(`hooks/shared/session_registry.py`), the rewired spawn prompts
(commands + persona), and the 12 updated agent-def frontmatters are **not in
the installed plugin cache** until this PR is released. A fresh session's
symlink (`~/.claude/protocols/pact-plugin`, re-pointed to the LIVE version at
every SessionStart by `setup_plugin_symlinks/symlinks.py`) therefore resolves
to a cache that **lacks** these files — so the register command path
(`~/.claude/protocols/pact-plugin/../hooks/shared/session_registry.py`) would
404 and the agent-defs/commands would carry the old form. The overlay copies
the branch's `pact-plugin/` tree onto the live cache so the fresh smoke session
exercises the **real** helper end-to-end. This is a THROWAWAY overlay — the
revert (mandatory) restores the pristine cache.

> **Mode scope**: run this in **tmux teammate mode** — the mode the bug
> manifested in (a tmux teammate cannot compute its own `@team`;
> `get_team_name` returns its OWN session hash, so `@team` must be
> self-supplied from the spawn prompt). The registry keys on each teammate's
> OWN `session_id` (`$CLAUDE_CODE_SESSION_ID`), which under tmux is distinct per
> teammate process.

---

## Prerequisites

1. The `feat/self-registration-885` branch checked out in its worktree, with
   commits through the On-Start-removal landed (the register directive present
   in all 13 spawn literals; the new skill present; the 12 frontmatters
   updated).
2. `tmux` available; operator able to start a fresh PACT session.
3. Note the live cache dir and back it up BEFORE overlaying.

---

## Step 1 — Stage the overlay (operator)

```bash
# Resolve the LIVE plugin cache dir the fresh session will use.
LIVE="$(dirname "$(readlink ~/.claude/protocols/pact-plugin)")"   # → .../PACT/<version>
echo "LIVE cache = $LIVE"

# Back up the pristine cache (MANDATORY — the revert restores from this).
cp -a "$LIVE" "$LIVE.pristine-bak"

# Overlay the branch's plugin tree onto the live cache — EXCLUDING the
# version-tracking files. The cache version MUST keep matching the HMAC-signed
# bootstrap marker: the marker signs `plugin_version`, so a cache whose
# .claude-plugin/plugin.json version differs from the signed marker's makes
# `bootstrap_gate` fail-CLOSED and BLOCKS Agent dispatch. Overlay only the
# FUNCTIONAL files (skill, session_registry.py, agent-defs, commands); the
# cache keeps its pristine version. (Whole-tree rsync carried the branch's
# version bump into the older cache and blocked dispatch until plugin.json was
# restored — exclude the version files to avoid it.)
WT=~/Sites/collab/PACT-prompt/.worktrees/feat/self-registration-885/pact-plugin
rsync -a --exclude='/.claude-plugin/plugin.json' --exclude='/README.md' "$WT"/ "$LIVE"/

# Confirm the new files landed in the live cache.
ls "$LIVE/skills/pact-team-registration/SKILL.md" \
   "$LIVE/hooks/shared/session_registry.py"

# Snapshot the registry baseline (should be empty / pre-existing lines noted).
cp -a ~/.claude/pact-sessions/.teammate-registry.jsonl /tmp/registry.before 2>/dev/null \
  || echo "(no registry file yet — fresh)"
```

## Step 2 — Spawn a STANDARD teammate (operator)

1. Start a fresh PACT session/team (SessionStart re-points the symlink at the
   overlaid cache and loads the overlaid agent-defs/commands).
2. Dispatch one standard teammate via the normal flow (e.g. `/PACT:orchestrate`
   PREPARE, or `/PACT:comPACT` — any path that emits the canonical spawn
   prompt). A `preparer` or any coder is fine.
3. **Observe the teammate's first turn**: its first tool call is
   `Invoke Skill("PACT:pact-team-registration")`, then it runs the register
   `Bash` command, then it proceeds to `TaskList`.

## Step 3 — Spawn the SECRETARY (operator)

1. Trigger the bootstrap secretary spawn (`/PACT:bootstrap`, or it occurs at
   session start).
2. **Observe**: the secretary self-supplies `secretary@<team>` and runs the
   register command **before** emitting its session-briefing `SendMessage`
   (register-first, no ordering carve-out).

## Step 4 — Assert the registry (operator)

```bash
REG=~/.claude/pact-sessions/.teammate-registry.jsonl

# (a) Registry is NON-EMPTY (the NO-GO ground-truth check).
test -s "$REG" && echo "PASS: registry non-empty" || echo "FAIL: registry EMPTY (NO-GO)"

# (b) One line per spawned teammate, each a valid session_id -> name@team.
python3 - "$REG" <<'PY'
import json, sys
seen = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    rec = json.loads(line)
    sid, val = rec.get("session_id"), rec.get("value", "")
    ok = bool(sid) and "@" in val and val.split("@")[0] and val.split("@")[1]
    seen.append((sid, val, ok))
    print(f"{'OK' if ok else 'BAD'}  sid={sid!r}  value={val!r}")
assert seen, "FAIL: registry has no parseable lines"
assert all(ok for *_, ok in seen), "FAIL: a line is missing sid or a name@team half"
# Expect at least the standard teammate AND the secretary (secretary@<team>).
assert any(v.startswith("secretary@") for _, v, _ in seen), \
    "FAIL: secretary path did not register secretary@<team>"
print(f"PASS: {len(seen)} valid registration line(s), including the secretary")
PY
```

**Pass criteria**:

- [ ] Standard teammate's first action is
      `Invoke Skill("PACT:pact-team-registration")` (before `TaskList`).
- [ ] Secretary registers `secretary@<team>` **before** its briefing
      `SendMessage`.
- [ ] `~/.claude/pact-sessions/.teammate-registry.jsonl` is **non-empty** and
      contains a valid `{"session_id", "value":"<name>@<team>"}` line per
      spawned teammate (standard + secretary).

If any check is RED → apply a §8.6 contingency from the architecture doc
(inline the register command directly in the spawn prompt; or, if firing is
merely flaky, adopt the pre-specified agent-def `(b)` backstop). Do **not**
merge on a RED smoke.

## Step 5 — REVERT the overlay (MANDATORY — operator)

```bash
LIVE="$(dirname "$(readlink ~/.claude/protocols/pact-plugin)")"

# Restore the pristine cache.
rm -rf "$LIVE"
mv "$LIVE.pristine-bak" "$LIVE"

# Reset the throwaway registry entries written during the smoke.
: > ~/.claude/pact-sessions/.teammate-registry.jsonl

# Confirm the overlay is gone (the new files should be ABSENT again).
ls "$LIVE/skills/pact-team-registration/SKILL.md" 2>/dev/null \
  && echo "REVERT INCOMPLETE — new skill still present" \
  || echo "REVERT OK — pristine cache restored"
```

> The overlay touches the SHARED machine-wide plugin cache; every session that
> starts while the overlay is live reads the overlaid version. Keep the overlay
> window short and run the revert promptly. `backup ≠ revert` — the revert is
> only complete once the pristine cache is moved back AND the new files are
> confirmed absent.

---

## Implementation references

- New skill: [pact-plugin/skills/pact-team-registration/SKILL.md](../../skills/pact-team-registration/SKILL.md)
- Registry leaf module: `pact-plugin/hooks/shared/session_registry.py`
  (`register --name '<name>@<team>'`; `REGISTRY_PATH`; fail-safe no-op)
- Spawn-prompt directive (canonical source): [pact-plugin/agents/pact-orchestrator.md](../../agents/pact-orchestrator.md) §Agent Teams Dispatch
- Secretary spawn: [pact-plugin/commands/bootstrap.md](../../commands/bootstrap.md) §Step — Spawn `pact-secretary`
- Presence/liveness guards: `pact-plugin/tests/test_commands_structure.py`
  (`TestRegisterDirectivePresentInAllSpawnSurfaces`,
  `TestTeamRegistrationSkillLiveness`)
- Frontmatter invocability guard: `pact-plugin/tests/test_agents_structure.py`
  (`test_frontmatter_includes_team_registration`)
- Companion runbook: [691-bootstrap-secretary-dispatch.md](691-bootstrap-secretary-dispatch.md)
  (secretary dispatch shape; its spawn literal now carries this register directive)
