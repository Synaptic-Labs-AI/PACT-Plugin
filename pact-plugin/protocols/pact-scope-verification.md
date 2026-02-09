## Scope Verification Protocol

> **Purpose**: Cross-scope contract compatibility and fulfillment verification.
> Executed by the lead at the end of the outer CODE phase during scoped orchestration,
> after all sub-scopes complete their inner P→A→C→T cycles via rePACT.
>
> This protocol replaces the retired `pact-scope-phases.md` (ATOMIZE/CONSOLIDATE).
> Scoped orchestration now uses the same P→A→C→T sequence at every level —
> no special phase names.

### When This Runs

After all sub-scopes have completed (still within the outer CODE phase):

```
Outer PREPARE → Outer ARCHITECT (includes decomposition) → Outer CODE:
  ├── rePACT sub-scope 1: inner P→A→C→T (sequential)
  ├── rePACT sub-scope 2: inner P→A→C→T (sequential)
  ├── ...
  └── Scope Verification Protocol (this document)  ← you are here
→ Outer TEST
```

### Step 1: Contract Compatibility Verification

**Who**: Lead spawns `pact-architect` as teammate.

**Task**: Verify cross-scope interface alignment:
- Compare contract fulfillment sections from all sub-scope handoffs
- Check that exports from each scope match imports expected by siblings
- Flag interface mismatches, type conflicts, or undelivered contract items
- Assess whether any contract deviations are architecturally acceptable

**Invoke with**:
- Feature description and all scope contracts (from scope task metadata)
- All sub-scope handoffs (contract fulfillment sections from task metadata)
- "This is cross-scope integration verification. Focus on compatibility between scopes, not internal scope correctness."

### Step 2: Contract Fulfillment Verification

**Who**: Lead (orchestrator-level metadata comparison).

**Task**: Compare each scope's `contract_fulfillment` metadata against original contracts:
- For each scope: are all deliverables marked ✅?
- Are any ❌ items blocking? (deferred items may be acceptable)
- Do interface exports/imports match across scopes?
- Are deviations documented with rationale?

This is metadata comparison, not code review — the lead can do this directly.

### Step 3: Integration Testing (Optional)

**Who**: Lead spawns `pact-test-engineer` as teammate (in parallel with Step 1).

**Task**: Run cross-scope integration tests:
- Verify cross-scope interfaces work together (API calls, shared types, data flow)
- Test integration points identified in scope contracts
- Confirm no shared file constraint violations occurred

**Invoke with**:
- Feature description and scope contract summaries
- All sub-scope handoffs (integration points)
- "This is cross-scope integration testing. Focus on interactions between scopes."

### On Verification Failure

Route through `/PACT:imPACT` for triage. Possible outcomes:
- Interface mismatch → re-invoke affected scope's coder to fix
- Contract deviation → architect reviews whether deviation is acceptable
- Test failure → test engineer provides details, coder fixes

If verification failure is severe (multiple scopes incompatible):
- Quarantine affected scope(s) — `TaskCreate("QUARANTINE: {reason}")` → block downstream
- Escalate to user (Failure Mode #7 per design doc)

### Completion

After verification passes:
- Lead commits any verification-phase work (integration test files, etc.)
- S4 Checkpoint: Scopes compatible? Integration clean? Plan viable?
- Outer CODE phase is now complete → proceed to outer TEST phase

---

### Related Protocols

- [pact-scope-detection.md](pact-scope-detection.md) — Heuristics for detecting multi-scope tasks
- [pact-scope-contract.md](pact-scope-contract.md) — Contract format and lifecycle
- [rePACT.md](../commands/rePACT.md) — Sequential sub-scope execution command
