# Integration Test Scenario: Standard Orchestration Workflow

**Priority**: P0
**Type**: Integration (runbook)
**Estimated Time**: 30-45 minutes

## Scenario Description

Verify the full PACT orchestration lifecycle: PREPARE, ARCHITECT, CODE, TEST phases executing in sequence with proper task hierarchy, worktree isolation, agent delegation, and phase transitions.

## Prerequisites

1. PACT plugin installed at `~/.claude/plugins/cache/pact-marketplace/PACT/`
2. A git repository with `CLAUDE.md` containing the PACT orchestrator configuration
3. No active worktrees for the test feature branch (run `git worktree list` to verify)
4. All four verification scripts passing: `bash scripts/verify-scope-integrity.sh`, `bash scripts/verify-protocol-extracts.sh`, `bash scripts/verify-task-hierarchy.sh`, `bash scripts/verify-worktree-protocol.sh`
5. `pact-memory` database accessible at `~/.claude/pact-memory/memory.db`

## Steps

### Step 1: Invoke Orchestration

Run the following command in Claude Code:

```
/PACT:orchestrate Add a utility function to validate email addresses in the shared utils module
```

### Step 2: Observe Variety Assessment

**What happens**: The orchestrator assesses task variety using the dimensions in `pact-plugin/protocols/pact-variety.md` (Novelty, Scope, Uncertainty, Risk).

**Expected outcome**: A one-line variety summary appears, such as:
```
Variety: Low (5) -- proceeding with orchestrate
```

For low-variety tasks (score 4-6), the orchestrator should offer comPACT as an alternative via `AskUserQuestion`. Select "Full orchestrate" to continue the scenario.

### Step 3: Observe Task Hierarchy Creation

**What happens**: The orchestrator creates a feature task and four phase tasks (PREPARE, ARCHITECT, CODE, TEST) with blockedBy chains.

**Expected outcome**: Feature task created, phase tasks created with dependencies:
- ARCHITECT blockedBy PREPARE
- CODE blockedBy ARCHITECT
- TEST blockedBy CODE

**Verification**: The orchestrator should state the task hierarchy creation. No direct task tool output is visible to agents, but the orchestrator manages this internally.

### Step 4: Observe Worktree Setup

**What happens**: The orchestrator invokes `/PACT:worktree-setup` to create an isolated worktree.

**Expected outcome**:
```
Worktree ready at {REPO_ROOT}/.worktrees/{branch-name}
Branch: {branch-name}
```

**Verification**:
```bash
git worktree list
```
Should show the new worktree alongside the main working directory. The `.worktrees/` entry should exist in `.gitignore`.

### Step 5: Observe Phase Skip Decision Flow

**What happens**: The orchestrator evaluates the 3-layer phase-skip decision flow (variety hard gates → plan completeness → structured analysis) to determine which phases run. For each of PREPARE and ARCHITECT, all three layers must pass before the phase can be skipped.

**Expected outcome** (no plan exists for this task):
- Layer 1 (Variety Hard Gates): Checked first — if Novelty ≥ 3 or Uncertainty ≥ 3, PREPARE is locked to run; if Scope ≥ 3 or Risk ≥ 3, ARCHITECT is locked to run
- Layer 2 (Plan Completeness): No approved plan exists, so this layer cannot grant a skip
- Layer 3 (Structured Analysis): Orchestrator must answer concrete questions with specific, verifiable answers to earn a skip
- CODE: Always runs
- TEST: Runs unless all four skip conditions are met

### Step 6: Observe PREPARE Phase

**What happens**: The orchestrator invokes `pact-preparer` as a background agent.

**Expected outcome**:
- Agent dispatched with task description
- Agent produces output in `docs/preparation/` within the worktree
- Agent returns a structured HANDOFF with all 5 items (Produced, Key decisions, Areas of uncertainty, Integration points, Open questions)
- Orchestrator runs S4 Checkpoint before proceeding

**Verification**:
```bash
ls {worktree_path}/docs/preparation/
```
Should contain research output file(s).

### Step 7: Observe PREPARE→ARCHITECT Coupling

**What happens**: The orchestrator reviews PREPARE output before evaluating ARCHITECT's skip eligibility. Hard gates are checked first (Scope ≥ 3, Risk ≥ 3, or total ≥ 10), then PREPARE output is reviewed for new components, interface changes, or cross-module impact.

**Expected outcome**: If PREPARE reveals new components or interfaces, ARCHITECT runs. If hard gates don't fire and no new architectural concerns emerged, ARCHITECT proceeds to structured analysis gate or may be skipped.

### Step 8: Observe Scope Detection

**What happens**: After PREPARE, the orchestrator evaluates scope detection heuristics from `pact-plugin/protocols/pact-scope-detection.md`.

**Expected outcome** (single-domain utility task):
```
Scope detection: Single scope (score 1/3 threshold)
```
No decomposition proposed. Standard flow continues.

### Step 9: Observe ARCHITECT Phase (if not skipped)

**What happens**: The orchestrator invokes `pact-architect` with PREPARE outputs.

**Expected outcome**:
- Agent dispatched with task description and pointer to preparation docs
- Agent produces output in `docs/architecture/` within the worktree
- Structured HANDOFF returned

### Step 10: Observe CODE Phase

**What happens**: The orchestrator runs QDCL (Quick Dependency Checklist), selects coder(s), and dispatches them.

**Expected outcome**:
- S5 Policy Checkpoint passes (architecture aligns, delegation is happening)
- Coder selected (likely `pact-backend-coder` for a utility function)
- Coder dispatched with architecture context and smoke test instructions
- Coder returns HANDOFF with implementation details and any flagged decisions
- Orchestrator creates atomic commit(s) of CODE phase work

**Verification**:
```bash
git log --oneline -3  # in the worktree
```
Should show commit(s) from the CODE phase.

### Step 11: Observe TEST Phase

**What happens**: The orchestrator invokes `pact-test-engineer` with coder handoff summaries.

**Expected outcome**:
- Test engineer dispatched with task description and CODE handoff
- Test engineer creates comprehensive tests (unit, edge cases, integration as needed)
- Structured HANDOFF with test signal (GREEN/YELLOW/RED)
- Orchestrator creates atomic commit(s) of TEST phase work

**Verification**:
```bash
git log --oneline -5  # in the worktree
```
Should show both CODE and TEST phase commits.

### Step 12: Observe Completion

**What happens**: The orchestrator marks all tasks completed and offers to run `/PACT:peer-review`.

**Expected outcome**:
- Feature task marked completed
- Plan status updated to IMPLEMENTED (if plan existed)
- Orchestrator prompts: "Work committed. Create PR?"

## Verification Checks

| Check | How to Verify | Pass Criteria |
|-------|---------------|---------------|
| Task hierarchy | Orchestrator output during setup | Feature + 4 phase tasks created with blockedBy chain |
| Worktree created | `git worktree list` | New worktree appears at `.worktrees/{branch}` |
| Phase ordering | Observe orchestrator output | P then A then C then T (or skipped phases noted) |
| Agent delegation | Observe orchestrator output | Each phase dispatches the correct specialist type |
| HANDOFF format | Agent responses | All 5 items present in each agent HANDOFF |
| S4 Checkpoints | Orchestrator output between phases | Checkpoint runs (silently if all clear, visibly if issues) |
| Commits exist | `git log` in worktree | At least one CODE commit and one TEST commit |
| No orchestrator code edits | Observe tool usage | Orchestrator never uses Edit/Write on application code |

## Failure Modes

| Failure | Symptom | Diagnosis |
|---------|---------|-----------|
| Orchestrator writes code directly | Edit/Write tool used on `.py`/`.ts`/`.js` files | S5 delegation policy violated; orchestrator should delegate |
| Agent stalls | No response after extended time | Check `pact-plugin/protocols/pact-agent-stall.md` for stall indicators |
| Phase skipped incorrectly | PREPARE skipped but requirements unclear | Completeness check in `pact-plugin/protocols/pact-completeness.md` may have been bypassed |
| Worktree not created | Agent works in main repo directory | `/PACT:worktree-setup` was not invoked or failed silently |
| HANDOFF missing items | Agent response lacks structured handoff | `validate_handoff` hook should warn; check `pact-plugin/hooks/validate_handoff.py` |
| Scope detection false positive | Decomposition proposed for simple task | Counter-signals may not have been applied; verify scoring in output |
