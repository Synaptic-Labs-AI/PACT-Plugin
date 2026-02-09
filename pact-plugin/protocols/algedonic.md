# Algedonic Signals Protocol

> **Purpose**: Emergency bypass for viability-threatening conditions.
> Algedonic signals go directly to user (S5), bypassing normal orchestration triage.
>
> **VSM Context**: In Stafford Beer's Viable System Model, algedonic signals are "pain/pleasure" signals that bypass management hierarchy to reach policy level (S5) instantly. They exist because some conditions are too critical to wait for normal processing.

---

## Signal Categories

### HALT Signals (Stop Everything)

Immediate cessation of ALL work. User must acknowledge before ANY work resumes.

| Category | Triggers | Examples |
|----------|----------|----------|
| **SECURITY** | Credentials exposure, injection vulnerability, auth bypass | API key in committed code, SQL injection found, JWT validation missing, hardcoded passwords |
| **DATA** | PII exposure, data corruption risk, integrity violation | User emails in logs, DELETE without WHERE, foreign key violations, unencrypted sensitive data |
| **ETHICS** | Deceptive output, harmful content, policy violation | Misleading user-facing text, generating harmful instructions, violating stated project values |

### ALERT Signals (Immediate Attention)

Orchestrator pauses current work. User notified. User may choose to continue or halt.

| Category | Triggers | Examples |
|----------|----------|----------|
| **QUALITY** | Repeated build failures, severe coverage gaps | Build broken >2 attempts, coverage <50% on critical path, same test failing repeatedly |
| **SCOPE** | Fundamental requirement misunderstanding | Building wrong feature, architecture contradicts user intent, major assumption invalid |
| **META-BLOCK** | 3+ imPACT cycles without resolution | Same blocker recurring, systemic issue detected, unable to make progress |

---

## Signal Format

When emitting an algedonic signal, use this format:

```
⚠️ ALGEDONIC [HALT|ALERT]: {Category}

**Issue**: {One-line description of what was found}
**Evidence**: {Specific details—file, line, what you observed}
**Impact**: {Why this threatens viability—what could go wrong}
**Recommended Action**: {What you suggest doing about it}
```

### Example HALT Signal

```
⚠️ ALGEDONIC HALT: SECURITY

**Issue**: AWS credentials hardcoded in source file
**Evidence**: Found in `src/config/aws.ts:15` - `AWS_SECRET_ACCESS_KEY = "AKIA..."`
**Impact**: Credentials will be exposed if code is committed; potential AWS account compromise
**Recommended Action**: Remove credentials, use environment variables, rotate the exposed key
```

### Framing Guidelines

Apply the S5 Decision Framing Protocol (see [pact-s5-policy.md](pact-s5-policy.md)) when presenting algedonic signals:

1. **Be specific** in evidence—file paths and line numbers when applicable
2. **Quantify impact** when possible—"3 API endpoints affected" vs "some endpoints"
3. **Provide concrete options** in Recommended Action—not vague "investigate"
4. **Keep it brief**—user can ask for details; don't bury the signal in prose

### Example ALERT Signal

```
⚠️ ALGEDONIC ALERT: META-BLOCK

**Issue**: Third consecutive imPACT cycle without resolution
**Evidence**: Auth middleware failing on same error since first attempt; tried 3 different approaches
**Impact**: May indicate fundamental misunderstanding or missing requirement
**Recommended Action**: User review of requirements; possible need to restart from PREPARE phase
```

---

## Signal Delivery Mechanism

**Protocol-level, not architectural**: Agents emit algedonic signals through the same communication channel (to orchestrator), but the SIGNAL FORMAT itself demands immediate user escalation. The orchestrator is **REQUIRED** to surface algedonic signals to user immediately—it cannot triage, delay, or suppress them.

### Task System Integration

With PACT Task integration, algedonic signals create **signal Tasks** that persist and block affected work.

**Agent behavior on algedonic signal:**

Under Agent Teams, teammates have access to Task tools (TaskGet, TaskUpdate, TaskList) and messaging (SendMessage). When an agent detects a viability threat:
1. Stop work immediately
2. Send the signal to the lead via SendMessage (using the Signal Format above):
   ```
   SendMessage(type="message", recipient="lead",
     content="⚠️ ALGEDONIC [HALT|ALERT]: {Category}\n\nIssue: ...\nEvidence: ...\nImpact: ...\nRecommended Action: ...\n\nPartial HANDOFF:\n...",
     summary="ALGEDONIC [HALT|ALERT]: [category]")
   ```
3. Provide a partial handoff with whatever work was completed

**Exception**: `pact-memory-agent` does not use SendMessage (it is not a team member). It reports algedonic signals as structured text in its response output.

The lead receives the SendMessage signal and handles algedonic Task creation and scope amplification.

**Orchestrator creates and manages the algedonic Task:**
1. `TaskCreate(subject="[HALT|ALERT]: {category}", metadata={"type": "algedonic", "level": "...", "category": "..."})` — creates the signal Task
2. `TaskUpdate(agent_task_id, addBlockedBy=[algedonic_task_id])` — blocks the reporting agent's task

**Orchestrator amplifies scope:**
| Signal Level | Orchestrator Action |
|--------------|---------------------|
| **ALERT** | `TaskUpdate(current_phase_id, addBlockedBy=[algedonic_id])` — blocks phase |
| **HALT** | `TaskUpdate(feature_task_id, addBlockedBy=[algedonic_id])` — blocks entire workflow |

**Resolution:**
```
1. User acknowledges/resolves the issue
2. TaskUpdate(algedonic_task_id, status="completed")
3. Blocked Tasks become unblocked automatically
4. Work can resume
```

This makes algedonic signals **visible in TaskList** and ensures they properly block affected work until resolved.

### Flow

```
Agent detects trigger condition
    ↓
Agent stops work immediately
    ↓
Agent sends algedonic signal via SendMessage to lead + provides partial handoff
    ↓
Orchestrator creates algedonic Task + blocks agent's task (addBlockedBy)
    ↓
Orchestrator amplifies scope (blocks phase or feature Task)
    ↓
Orchestrator IMMEDIATELY presents to user (no other work continues)
    ↓
User responds
    ↓
Algedonic Task marked completed
    ↓
Work resumes (or stops) based on user decision
```

### Orchestrator Behavior

On receiving an algedonic signal:

1. **IMMEDIATELY** present signal to user (do not continue other work first)
2. For **HALT**: Stop ALL agents, await user acknowledgment
3. For **ALERT**: Pause current work, present options to user
4. **Log** the signal in session record

**Handling parallel agents on HALT**:

When multiple agents are running and HALT is triggered:
1. **Stop all agents immediately** — no agent continues work
2. **Preserve work-in-progress** — do NOT discard uncommitted changes
3. **Do NOT commit partial work** — leave changes staged/unstaged as-is
4. **Document agent states** — note which agents were interrupted and their progress

After HALT is resolved:
- Review interrupted agents' work before resuming
- Decide whether to continue from checkpoint or restart affected work
- The HALT fix may invalidate some parallel work (especially for SECURITY issues)

### User Response Options

**For HALT signals**:
- "Acknowledged, investigate" — Work stops, investigation begins
- "Override, continue anyway" — See override protocol below

**Override Protocol** (for HALT signals only):

User must provide **explicit justification** that:
1. Acknowledges the **specific risk** identified (not just "I understand")
2. Explains **why** proceeding is acceptable despite the risk
3. Accepts **responsibility** for consequences if the risk materializes

**Document the override**:
- Log in session notes: "HALT OVERRIDE: {category} - {user's justification}"
- If the project has a decision log, add: "⚠️ Overrode {category} HALT: {justification}"

**If overridden risk materializes later**:
- Emit a new HALT signal (the previous override doesn't carry forward)
- The new signal should reference the prior override: "Previously overridden {category} issue has materialized"
- User must acknowledge again—no automatic continuation

**For ALERT signals**:
- "Investigate" — Pause and dig deeper
- "Continue with caution" — Proceed but with awareness
- "Stop work" — Halt all activity

---

## Who Can Emit Algedonic Signals

**Any agent** can emit algedonic signals when they recognize trigger conditions. This is part of the Autonomy Charter (see [pact-s1-autonomy.md](pact-s1-autonomy.md)).

Agents do **NOT** need orchestrator permission to emit—the conditions themselves authorize the signal.

### Agent Responsibility

Each specialist should be aware of algedonic triggers relevant to their domain:

| Agent | Watch For |
|-------|-----------|
| **Backend Coder** | SECURITY (auth flaws, injection), DATA (query safety, PII handling) |
| **Frontend Coder** | SECURITY (XSS, CSRF), DATA (client-side storage of sensitive data) |
| **Database Engineer** | DATA (schema integrity, PII exposure, destructive operations) |
| **Test Engineer** | SECURITY (discovered vulnerabilities), QUALITY (coverage gaps, repeated failures) |
| **Architect** | SCOPE (design contradictions), ETHICS (architectural decisions with ethical implications) |
| **Preparer** | SCOPE (requirement misunderstanding discovered during research) |

---

## Relationship to Other Protocols

### imPACT vs Algedonic

| Aspect | imPACT | Algedonic |
|--------|--------|-----------|
| **Level** | S3 (operational) | S5 (policy/viability) |
| **Who decides** | Orchestrator triages | User decides |
| **Question** | "How do we proceed?" | "Should we proceed at all?" |
| **Examples** | Missing info, wrong approach, need help | Security breach, data risk, ethics issue |

### When imPACT Becomes Algedonic

- **3+ consecutive imPACT cycles without resolution** → Emit ALERT (META-BLOCK)
- **During imPACT, discover viability threat** → Emit appropriate HALT/ALERT instead

imPACT is for operational problem-solving. If you're questioning whether the work should continue at all, emit an algedonic signal instead.

### TEST Phase Signals vs Algedonic

| Signal | Source | Scope | Bypass |
|--------|--------|-------|--------|
| 🔴 RED (Test) | Test engineer during TEST phase | Test failures, code issues | No—routes back to coders |
| HALT/ALERT (Algedonic) | Any agent, any time | Viability threat | Yes—goes directly to user |

A 🔴 RED test signal indicates critical issues found during testing (test failures, security vulnerabilities). The orchestrator routes these back to coders for fixes. An algedonic signal indicates a viability threat that questions whether the work should continue at all.

---

## Best Practices

### When in Doubt

- If unsure whether something is an operational blocker or viability threat, **emit algedonic** (safer)
- False positives are acceptable; missed viability threats are not
- The user can always say "continue" if it's not as serious as you thought

### Signal Quality

- Be **specific** in evidence—vague signals are harder to act on
- Include **file paths and line numbers** when applicable
- Suggest **concrete actions** in recommendations
- Don't emit both imPACT blocker and algedonic for the same issue—choose one

### Avoiding False Positives

Before emitting HALT, verify:
- Is this a **current** threat or merely a **potential** threat?
- Could this be handled as a normal blocker (imPACT) without user escalation?
- Do you have **clear evidence**, not just suspicion?
- Is the severity **viability-threatening**, or just concerning?

If answers lean toward "potential/normal/suspicion/concerning," consider imPACT first. Reserve algedonic for clear, current, evidence-based viability threats.

### After Resolution

- Once a HALT is resolved, **verify** the fix before resuming:
  1. **Who verifies**: The agent who emitted the signal, or test engineer if security/data issue
  2. **What to verify**: The specific issue is fixed AND the fix doesn't introduce new issues
  3. **Scope**: Focused verification of the fix, not comprehensive testing (that comes in TEST phase)
  4. **Report**: Confirm to orchestrator: "HALT resolved: {one-line summary of fix}"
- Document what was found and how it was resolved
- Consider whether similar issues might exist elsewhere (orchestrator may request targeted audit)
