# Canary Verification Checklist

This checklist defines a 3-tier verification process for PRs that touch `pact-plugin/`. Each tier adds a layer of confidence that protocol changes have not introduced regressions.

**When to apply**: Any PR that modifies files under `pact-plugin/` (protocols, commands, agents, skills, hooks).

---

## Tier 1: Automated Verification

Run all verification scripts from the repository root. Every PR touching `pact-plugin/` must pass all four.

```bash
bash scripts/verify-protocol-extracts.sh
bash scripts/verify-scope-integrity.sh
bash scripts/verify-task-hierarchy.sh
bash scripts/verify-worktree-protocol.sh
```

**Expected output**: Each script prints individual check results (lines prefixed with `✓` or `✗`) followed by a summary block:

```
=== Summary ===
Passed: N
Failed: 0

VERIFICATION PASSED
```

Any script exiting with `VERIFICATION FAILED` (exit code 1) means the PR has broken a protocol invariant and must not be merged.

### Checklist

- [ ] `verify-protocol-extracts.sh` passes (16 checks) -- SSOT extracts match source line ranges
- [ ] `verify-scope-integrity.sh` passes (68 checks) -- cross-references, naming conventions, nesting limits, worktree integration, memory hooks, executor interface, agent persistent memory
- [ ] `verify-task-hierarchy.sh` passes (28 checks) -- task lifecycle patterns in all command files
- [ ] `verify-worktree-protocol.sh` passes (20 checks) -- worktree skill existence, command references, path propagation

### What failures mean

| Script | Common Failure Cause | Fix |
|--------|---------------------|-----|
| `verify-protocol-extracts.sh` | Protocol SSOT content shifted; line ranges in the script no longer match | Update the `sed` line ranges in the script to match the new SSOT layout |
| `verify-scope-integrity.sh` | A cross-reference was broken, a required pattern was removed, or a new agent file is missing expected content | Trace the `✗` output to the specific check and restore the expected pattern |
| `verify-task-hierarchy.sh` | A command file's Task Hierarchy section is missing a lifecycle keyword (`TaskCreate`, `in_progress`, `completed`) | Add the missing lifecycle pattern to the command's Task Hierarchy section |
| `verify-worktree-protocol.sh` | A command file lost its worktree skill reference or a skill file is missing its frontmatter | Restore the worktree reference or skill frontmatter |

---

## Tier 2: Structural Review

Human reviewers verify structural properties that automated scripts cannot fully cover. These checks apply to every PR touching `pact-plugin/`.

### Checklist

- [ ] **Protocol extract line ranges are still valid** -- If the PR modifies `pact-protocols.md`, confirm that `verify-protocol-extracts.sh` line ranges (in the script itself) have been updated to match
- [ ] **Cross-references are intact** -- Protocol files that reference other protocols (e.g., `pact-scope-contract.md` referencing `rePACT.md`) still point to correct targets
- [ ] **SSOT extracts match their sources** -- Extracted protocol files are verbatim copies of their SSOT sections in `pact-protocols.md` (the automated script verifies this, but reviewers should confirm the line ranges themselves are correct)
- [ ] **No orphaned references** -- Search for references to renamed or deleted files; confirm no dead links remain
- [ ] **Agent definition consistency** -- All agent `.md` files under `pact-plugin/agents/` have matching frontmatter fields (`memory: user`, nesting limit, HANDOFF format)
- [ ] **Command file structure preserved** -- Command files retain their expected section headings (Task Hierarchy, phase sections, etc.)
- [ ] **Version numbers updated if needed** -- If the PR represents a version bump, both `plugin.json` and `marketplace.json` reflect the same version

---

## Tier 3: Behavioral Validation

Run a live PACT orchestration cycle to verify that the framework functions correctly end-to-end. Apply this tier at major milestones (version bumps, multi-step implementation rounds, or any PR that changes orchestration flow).

### Approach

Use the PACT framework on itself (dogfooding): run `/PACT:orchestrate` or `/PACT:comPACT` on a real task and observe whether the full lifecycle works.

### Checklist

- [ ] **Agent spawning works** -- Specialist agents are invoked and receive their prompts (check that the orchestrator delegates rather than acting directly)
- [ ] **Handoff format is correct** -- Agent responses end with the 5-item HANDOFF structure (Produced, Key decisions, Areas of uncertainty, Integration points, Open questions)
- [ ] **Memory saves succeed** -- The `pact-memory` skill saves context without errors; saved memories are retrievable via search
- [ ] **Worktree lifecycle completes** -- `worktree-setup` creates a worktree, agents work within it, and `worktree-cleanup` removes it after PR
- [ ] **Verification scripts pass in the worktree** -- All Tier 1 scripts pass when run from the worktree working directory
- [ ] **Task tracking is consistent** -- Task statuses follow the `pending -> in_progress -> completed` lifecycle without orphaned or stuck tasks
- [ ] **Phase transitions are clean** -- Each PACT phase completes with a handoff before the next begins; no phase is skipped without documented rationale

### When to run

- Before merging a version bump PR
- After completing a multi-step implementation round (e.g., all D1-D6 steps)
- When changing orchestration logic in `orchestrate.md`, `comPACT.md`, or `rePACT.md`
- Quarterly as a general regression check
