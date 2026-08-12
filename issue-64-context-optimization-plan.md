# Context Architecture Optimization Plan

**Issue**: #64 - Context Architecture Review: Optimize PACT for Context Engineering Best Practices
**Status**: Draft - Awaiting Approval
**Author**: PACT Orchestrator
**Date**: 2026-01-14

---

## Executive Summary

Optimize PACT framework's context architecture by implementing a three-tier loading system that aligns with Anthropic's guidance on efficient context use. The goal is to reduce always-loaded context from ~291 lines (CLAUDE.md) plus indirect loading of protocols/commands to a lean ~100-150 line core, with remaining content loaded just-in-time when invoked.

---

## Current State Analysis

### Token Budget Assessment

| Component | Lines | Est. Tokens | Loading |
|-----------|-------|-------------|---------|
| CLAUDE.md | 291 | ~3,500 | Always |
| pact-protocols.md | 681 | ~8,000 | Referenced |
| orchestrate.md | 449 | ~5,000 | On command |
| plan-mode.md | 441 | ~5,000 | On command |
| rePACT.md | 249 | ~3,000 | On command |
| comPACT.md | 189 | ~2,300 | On command |
| algedonic.md | 225 | ~2,700 | On reference |
| **Total potential load** | ~2,500 | ~30,000+ | Variable |

### CLAUDE.md Content Breakdown

Current structure (291 lines):

| Section | Lines | Purpose | Tier Candidate |
|---------|-------|---------|----------------|
| MISSION/MOTTO | 1-8 | Identity | Tier 1 (Keep) |
| S5 POLICY | 11-58 | Governance | Tier 1 (Compress) |
| INSTRUCTIONS | 60-66 | Core directives | Tier 1 (Keep) |
| Context Management | 69-75 | Session protocol | Tier 1 (Keep) |
| Git Workflow | 77-78 | Basic rule | Tier 1 (Keep) |
| S3/S4 Modes | 80-105 | Operational guidance | Tier 2 (Extract) |
| PACT Phase Principles | 107-144 | Detailed principles | Tier 2 (Extract) |
| Dev Best Practices | 146-158 | Coding guidelines | Tier 2 (Extract) |
| Quality Assurance | 154-158 | QA rules | Tier 2 (Extract) |
| Communication | 160-169 | Interaction style | Tier 1 (Compress) |
| Always Be Delegating | 171-229 | Detailed enforcement | Tier 2 (Extract) |
| Agent Roster | 231-240 | Specialist list | Tier 1 (Keep) |
| How to Delegate | 242-258 | Command references | Tier 1 (Keep) |
| Agent Workflow | 260-274 | Detailed workflow | Tier 2 (Extract) |
| PR Review Workflow | 276-290 | Review process | Tier 2 (Extract) |

### Key Problem: Context Rot Risk

The framework documents Ashby's Law (variety management) but violates it at the meta-level:
- Dense procedural instructions loaded before task context
- Detailed protocols that should be reference material occupy prime context real estate
- As documentation grows, risk of degraded accuracy increases

---

## Proposed Three-Tier Architecture

### Tier 1: Core Identity (~100-150 lines) - Always Loaded

**Purpose**: Establish identity, non-negotiables, and navigation. Minimal footprint, maximum essential guidance.

**Content**:
1. **MISSION** (3 lines) - Identity and purpose
2. **MOTTO** (2 lines) - Core principle reminder
3. **S5 POLICY Essentials** (~30 lines)
   - Non-negotiables table (compressed)
   - Key checkpoints (one-liners)
   - Algedonic signal quick reference
4. **CORE INSTRUCTIONS** (~10 lines)
   - High-level directives
   - "Read CLAUDE.md at session start"
   - "Delegate application code"
5. **PROTOCOL INDEX** (~15 lines)
   - List of available protocols with one-line descriptions
   - File paths for just-in-time loading
6. **AGENT ROSTER** (~15 lines)
   - Specialist names and brief domain descriptions
7. **COMMAND QUICK REFERENCE** (~20 lines)
   - Command names with one-line purposes
   - "See {file} for full protocol"
8. **SESSION STATE** (~10 lines)
   - Current phase/task (if mid-session)
   - Active blockers
   - Project-specific notes

**Estimated**: 100-120 lines

### Tier 2: Command/Workflow Protocols - Loaded on Invocation

**Purpose**: Detailed procedural guidance loaded when specific commands are invoked.

**Files** (already exist in `.claude/commands/PACT/`):
- `orchestrate.md` - Full orchestration workflow
- `plan-mode.md` - Planning consultation protocol
- `comPACT.md` - Light ceremony delegation
- `rePACT.md` - Recursive PACT cycles
- `imPACT.md` - Blocker triage protocol
- `peer-review.md` - PR review workflow
- `log-changes.md` - CLAUDE.md update protocol
- `wrap-up.md` - Session cleanup

**New extractions needed**:
- `.claude/protocols/delegation-enforcement.md` - Detailed delegation rules
- `.claude/protocols/operational-modes.md` - S3/S4 mode guidance
- `.claude/protocols/phase-principles.md` - PACT phase principles
- `.claude/protocols/dev-best-practices.md` - Development guidelines

### Tier 3: Reference Materials - On-Demand Retrieval

**Purpose**: Deep reference content accessed when specific topics arise.

**Files** (already exist):
- `.claude/protocols/pact-protocols.md` - Comprehensive protocol reference
- `.claude/protocols/algedonic.md` - Emergency bypass details
- `.claude/reference/vsm-glossary.md` - VSM terminology
- `.claude/skills/*/SKILL.md` - Domain-specific skills

**Loading triggers**:
- Agent hits blocker → load `pact-protocols.md#imPACT`
- Security concern → load `pact-security-patterns` skill
- Testing guidance → load `pact-testing-strategies` skill

---

## Implementation Plan

### Phase 1: Extract Tier 2 Protocols

**Task**: Move detailed procedural content from CLAUDE.md to new protocol files.

**New files to create**:

#### 1.1 `.claude/protocols/delegation-enforcement.md`
Extract from CLAUDE.md lines 171-229:
- "Always Be Delegating" detailed rules
- "What Is Application Code?" table
- Tool Checkpoint Protocol
- Recovery Protocol

#### 1.2 `.claude/protocols/operational-modes.md`
Extract from CLAUDE.md lines 80-105:
- S3/S4 Mode definitions
- Mode transition triggers
- Mode naming guidance

#### 1.3 `.claude/protocols/phase-principles.md`
Extract from CLAUDE.md lines 107-144:
- PREPARE phase principles
- ARCHITECT phase principles
- CODE phase principles
- TEST phase principles

#### 1.4 `.claude/protocols/dev-best-practices.md`
Extract from CLAUDE.md lines 146-169:
- Development best practices
- Quality assurance rules
- Communication guidelines

### Phase 2: Compress CLAUDE.md to Tier 1

**Task**: Rewrite CLAUDE.md as lean core identity document.

**Structure**:
```markdown
# MISSION
[Keep as-is]

## MOTTO
[Keep as-is]

---

## S5 POLICY (Governance)

### Non-Negotiables (SACROSANCT)
[Keep table, compress explanatory text]

### Core Checkpoints
| When | Action |
|------|--------|
| Before CODE | Verify architecture alignment |
| Before Edit/Write | Delegate if application code |
| Before PR | Verify tests pass |

### Algedonic Signals
HALT (SECURITY/DATA/ETHICS) → Stop, escalate to user
ALERT (QUALITY/SCOPE/META-BLOCK) → Pause, user decides
See `protocols/algedonic.md` for details.

---

## INSTRUCTIONS
1. Read CLAUDE.md at session start
2. Delegate ALL application code to specialists
3. Update CLAUDE.md via /PACT:log-changes
4. Follow PACT phases: Prepare → Architect → Code → Test

---

## PROTOCOL INDEX

| Protocol | File | When |
|----------|------|------|
| Full orchestration | `.claude/commands/PACT/orchestrate.md` | Multi-agent tasks |
| Planning mode | `.claude/commands/PACT/plan-mode.md` | Pre-implementation |
| Light delegation | `.claude/commands/PACT/comPACT.md` | Single-domain tasks |
| Recursive PACT | `.claude/commands/PACT/rePACT.md` | Complex sub-tasks |
| Blocker triage | `.claude/commands/PACT/imPACT.md` | When blocked |
| Delegation rules | `.claude/protocols/delegation-enforcement.md` | Before editing |
| Operational modes | `.claude/protocols/operational-modes.md` | Decision-making |
| Phase principles | `.claude/protocols/phase-principles.md` | During phases |

---

## SPECIALIST AGENTS

| Agent | Domain | Phase |
|-------|--------|-------|
| pact-preparer | Research, docs, requirements | PREPARE |
| pact-architect | System design, interfaces | ARCHITECT |
| pact-backend-coder | Server-side, APIs | CODE |
| pact-frontend-coder | Client-side, UI | CODE |
| pact-database-engineer | Schema, queries | CODE |
| pact-n8n | Workflow automation | CODE |
| pact-test-engineer | Testing, QA | TEST |

---

## COMMANDS

| Command | Purpose |
|---------|---------|
| /PACT:orchestrate | Multi-agent full ceremony |
| /PACT:plan-mode | Planning consultation |
| /PACT:comPACT | Single-domain delegation |
| /PACT:rePACT | Recursive nested PACT |
| /PACT:imPACT | Blocker triage |
| /PACT:peer-review | PR creation with review |
| /PACT:log-changes | Update CLAUDE.md |
| /PACT:wrap-up | Session cleanup |

---

## SESSION STATE

[Project-specific state maintained here]
```

### Phase 3: Update Cross-References

**Task**: Ensure all files reference the new protocol locations.

**Files to update**:
- Agent definitions in `.claude/agents/` - add protocol references
- Command files in `.claude/commands/PACT/` - update any CLAUDE.md references
- `pact-protocols.md` - align with new structure

### Phase 4: Validate Loading Behavior

**Task**: Test that Claude Code correctly loads Tier 2 protocols when commands are invoked.

**Validation criteria**:
- Invoking `/PACT:orchestrate` loads full orchestration protocol
- Invoking `/PACT:comPACT` loads light delegation protocol
- Delegation enforcement accessible via explicit reference
- Agent definitions load correctly when agents are spawned

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Lost context during transition | Medium | High | Maintain pact-protocols.md as comprehensive backup |
| Agents miss critical guidance | Medium | Medium | Include protocol references in agent prompts |
| User confusion from changed structure | Low | Low | Update README with new architecture |
| Commands fail to load protocols | Low | High | Test each command path before merging |

---

## Success Metrics

1. **CLAUDE.md line count**: 291 → 100-150 lines (~50% reduction)
2. **Always-loaded tokens**: ~3,500 → ~1,500 (~60% reduction)
3. **Protocol accessibility**: All detailed guidance still reachable via explicit references
4. **Functionality preserved**: All commands and workflows operate correctly

---

## Recommended Workflow

1. **Prepare Phase**: Research Claude Code's context loading behavior (this plan)
2. **Architect Phase**: Design file structure and cross-references
3. **Code Phase**: Extract protocols, rewrite CLAUDE.md, update references
4. **Test Phase**: Validate each command path and agent invocation

**Estimated specialist involvement**:
- pact-preparer: Context loading research (if needed)
- pact-architect: Final structure review
- pact-backend-coder: File creation and editing (since these are .md files in .claude/)
- pact-test-engineer: Validation testing

---

## Open Questions for User

1. **Session State section**: Should it be a separate file (`SESSION.md`) or remain in CLAUDE.md?
2. **Protocol loading**: Are there specific Claude Code mechanisms for lazy-loading protocols?
3. **Priority**: Should we maintain backward compatibility with existing CLAUDE.md structure during transition?

---

## Approval

- [ ] User approves overall approach
- [ ] User confirms priority level (Medium)
- [ ] User answers open questions

**Next step**: Upon approval, run `/PACT:orchestrate` to implement this plan.
