## Concurrent Audit Protocol

> **Cybernetic basis**: Ashby's Law of Requisite Variety applied to quality assurance —
> a pure post-hoc review (TEST phase) has lower variety than concurrent observation.
> Real-time observation during CODE phase catches architecture drift before it compounds.

The pact-auditor agent provides independent quality observation during the CODE phase, complementing (not replacing) the TEST phase and peer review.

### Dispatch Conditions

The auditor is dispatched alongside coders by default. To skip, the orchestrator outputs on its own line:

> **Auditor skipped**: [justification]

**Dispatch is mandatory when**:
- Variety score >= 7 (Medium or higher)
- 3+ coders running in parallel (coordination complexity warrants observation)
- Task touches security-sensitive code (auth, crypto, user input handling)
- Domain has prior history of architecture drift (from pact-memory calibration data)

**Valid skip reasons**: Single coder on familiar pattern, variety reassessed below 7, user requested skip.

### Hybrid Observation Model

The auditor operates primarily through file observation, not messaging. This minimizes disruption to coders while maintaining quality oversight.

| Method | When | Cost |
|--------|------|------|
| **File reading** (git diff, Read) | Primary — every observation cycle | Zero disruption |
| **TaskList monitoring** | Check coder progress, task status | Zero disruption |
| **SendMessage to coder** | Only when file observation raises a question code alone can't answer | Low disruption (one specific question per message) |
| **RED signal to orchestrator** | Clear architecture violation or requirement misunderstanding | Appropriate disruption |

**Rule of thumb**: 80%+ of observation should be silent file reading. If the auditor is messaging coders frequently, it's disrupting more than observing.

### Observation Phases

**Phase A: Warm-up** (while coders start):
1. Read all available references (architecture doc, plan, dispatch context)
2. Identify key interfaces, high-risk dimensions, cross-cutting requirements
3. Note coder assignments from TaskList
4. Wait for coders to produce initial output before observing

**Phase B: Observation cycles** (periodic):
1. Check modified files: `git diff`, read changed files
2. Compare against reference chain: architecture spec > approved plan > dispatch context
3. Assess concern level and respond:
   - No concern → silent, continue next cycle
   - Minor concern → log internally, observe next cycle (may self-resolve)
   - Significant but ambiguous → SendMessage to coder (one specific question)
   - Clear violation → RED signal to orchestrator immediately

**Phase C: Final observation** (triggered by orchestrator or all coders completing):
1. Sweep all modified files
2. Emit summary signal (GREEN/YELLOW/RED)
3. Store audit summary in task metadata

### Audit Criteria (Priority Order)

1. **Architecture drift** — Module boundaries, interfaces, data flow, dependencies matching the design
2. **Risk-proportional concerns** — High uncertainty areas from variety assessment get extra attention
3. **Cross-agent consistency** — When parallel coders: compatible interfaces? Consistent naming? No semantic overlap?
4. **Cross-cutting gaps** — Error handling patterns, security basics, performance red flags
5. **Requirement alignment** — Solving the right problem as specified?

**NOT audited**: Code style, test coverage (TEST phase), code cleanliness mid-work, micro-optimization.

### Structural Verification Discipline

Before emitting GREEN on any **structural acceptance criterion**, the auditor MUST verify the claim against `git diff` ground truth. Pattern-matching on HANDOFF prose, commit messages, or coder self-attestation alone is NOT sufficient evidence. Four internally-consistent layers of prose can all be wrong together; the diff is evidence.

**Rationale**: This instantiates the general rule **`file inspection beats HANDOFF inference`**, established during PR #371 calibration (memory `bcead760`, 2026-04-08) and re-materialized at the auditor layer in PR #501 (memory `bb101a99`, 2026-04-21): "Auditor GREEN signal, coder HANDOFF narrative, and commit message body can all pattern-match to self-attestation without any of them verifying against git diff." HANDOFF narrative is a retrieval aid, not ground truth. The specific failure mode this rule prevents is the PHANTOM-SYMMETRIC-CLAIM variant: in PR #501 commit `bef7f24` (corrected in `7ed354e`), four layers — the coder's HANDOFF prose, the commit message body, the coder's self-attestation messages, and the audit signal — all agreed on a fabricated structural claim (three mirror-added skips at a specific line range) while the actual diff contained one.

**Structural ACs** (diff-verifiable): countable or locatable artifacts — "all files in one commit", "N skips at lines Y–Z", "function `foo` untouched", "helper extracted into a new file", "added after the existing imports block". If the AC contains a count, a line range, a path, or a touched/untouched/added/removed verb, it is structural.

**Non-structural ACs** (judgment calls): "correct function separation", "clean naming", "appropriate error handling", "idiomatic for this codebase". Cannot be derived from diff alone; the auditor should inspect the relevant code and say so in the finding.

**Verification procedure**:

1. Run `git diff <base>..HEAD -- <path>` (or `git show <sha>`) against the path(s) the structural AC references.
2. Count or locate the claimed artifact in the actual diff output — not in the HANDOFF, commit message, or coder messages.
3. If count/location matches the claim → cite the exact diff range in the Evidence field (command + path + hunk header or line range). Specificity requirement: a verifier must be able to reproduce the read.
4. If count/location does NOT match → emit RED (clear violation) or YELLOW (ambiguous) with the discrepancy named. Do NOT emit GREEN. A count mismatch (HANDOFF says N, diff shows M, M ≠ N) is a clear violation — RED. Reserve YELLOW for cases where the count matches but the location or context is partially off.
5. If the AC is not structural → say so in the finding; do NOT manufacture diff citations.

**Failure modes to avoid**:

- **PHANTOM-SYMMETRIC-CLAIM**: HANDOFF prose, commit message, coder self-attestation, and audit signal all agree on a specific structural claim. Agreement across layers is cheap. If you cite "the coder's HANDOFF states…" as evidence for a structural AC, stop and read the diff.
- **VAGUE-DIFF-CITATION**: Evidence field contains "git diff excerpt" or "see diff" with no specific path, hunk, or line range. Not reproducible; indistinguishable from pattern-matching on prose.
- **STRUCTURAL-DRESSING-ON-JUDGMENT-CALL**: GREEN on a judgment-call AC with a fabricated-looking Evidence field. If the AC is a judgment call, name it as such.

### Signal Format

```
📋 AUDIT SIGNAL: [GREEN|YELLOW|RED]

Reference: [architecture doc / plan / dispatch context]
Scope: [which coder(s) / which files]
Finding: [One-line summary]
Evidence: [For structural ACs: `git diff <base>..HEAD -- <path>` plus the specific hunk header or line range that demonstrates the claim. For non-structural ACs: specific file:line of the code inspected. Vague citations like "see diff" are not acceptable — a verifier must be able to reproduce the read.]
Action: [None (GREEN) / Route to test (YELLOW) / Intervene (RED)]
```

### Signal Levels

| Signal | Meaning | When | Orchestrator Response |
|--------|---------|------|---------------------|
| **GREEN** | On track | Final summary; silence during cycles is implicit GREEN | None needed |
| **YELLOW** | Worth noting | Minor drift, convention inconsistency, potential edge case | Pass finding to test engineer as focus area |
| **RED** | Intervene now | Architecture violation, requirement misunderstanding, security concern | SendMessage to affected coder; may pause coder's work |

**Before emitting RED**: Verify via targeted question to the coder when practical. Skip verification for clear-cut violations (e.g., wrong module boundary, missing auth check on sensitive endpoint).

### Reference Fallback Chain

The auditor checks implementation against available references in priority order:

1. **Architecture doc** (`docs/architecture/`) — Most authoritative for design decisions
2. **Approved plan** (`docs/plans/`) — Authoritative for scope and approach
3. **Dispatch context** (task description/metadata) — Authoritative for specific agent instructions
4. **Established conventions** (existing codebase patterns) — When no explicit reference exists

If no reference exists for a concern, the auditor logs it as YELLOW (convention gap) rather than RED (violation).

### Completion Lifecycle

The auditor uses signal-based completion rather than standard HANDOFF:

1. Task is created with `metadata: {"completion_type": "signal"}`
2. Auditor stores final signal as `metadata.audit_summary` via `TaskUpdate`
3. Auditor marks task completed
4. Completion gate accepts `audit_summary` as the completion artifact (see teammate_completion_gate.py)

**audit_summary format**:
```json
{
  "signal": "GREEN",
  "findings": [
    {"level": "YELLOW", "scope": "backend-coder", "finding": "Error handling inconsistent in auth module"}
  ],
  "observation_cycles": 3,
  "files_reviewed": 12
}
```

### Algedonic Escalation

If the auditor discovers a viability threat (not just a quality issue), bypass the signal system and emit a full algedonic signal per [algedonic.md](algedonic.md). Examples:
- Hardcoded credentials discovered in coder's work → HALT SECURITY
- PII being logged → HALT DATA
- Fundamental misunderstanding of requirements → ALERT SCOPE

### Relationship to Other Quality Mechanisms

| Mechanism | Timing | Focus | Scope |
|-----------|--------|-------|-------|
| **Auditor** | During CODE | Architecture drift, requirement alignment | Concurrent observation |
| **TEST phase** | After CODE | Functional correctness, edge cases, coverage | Comprehensive testing |
| **Peer review** | After TEST | Cross-domain quality, code health | Multi-reviewer synthesis |
| **Security review** | During review | Adversarial security analysis | Security-focused |

The auditor is additive — it catches issues during CODE that would otherwise only surface in TEST or review, when the cost of correction is higher.

**Related protocol**: [S4 Checkpoints](pact-s4-checkpoints.md) — Auditor RED signals should prompt an S4 checkpoint to reassess plan viability.

---
