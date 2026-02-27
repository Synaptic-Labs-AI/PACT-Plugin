## Incompleteness Signals

> **Purpose**: Define the signals that indicate a plan section is NOT complete.
> Used by `plan-mode` (producer) to populate the Phase Requirements table,
> and by `orchestrate` (consumer) to verify phase-skip decisions.

A plan section may exist without being complete. Before skipping a phase, the orchestrator checks the corresponding plan section for these 7 incompleteness signals. **Any signal present means the phase should run.**

> **Layer 2**: This protocol serves as Layer 2 of the phase-skip protection system. See orchestrate.md "Context Assessment: Phase Skip Decision Flow" for the full 3-layer gate model.

---

### Signal Definitions

| # | Signal | What to Look For | Example |
|---|--------|-------------------|---------|
| 1 | **Unchecked research items** | `- [ ]` checkboxes in "Research Needed" sections | `- [ ] Investigate OAuth2 library options` |
| 2 | **TBD values in decision tables** | Cells containing "TBD" in "Key Decisions" or similar tables | `| Auth strategy | TBD | TBD | Needs research |` |
| 3 | **Forward references** | Deferred work markers using the format `⚠️ Handled during {PHASE_NAME}` | `⚠️ Handled during PREPARE` |
| 4 | **Unchecked questions** | `- [ ]` checkboxes in "Questions to Resolve" sections | `- [ ] Which caching layer to use?` |
| 5 | **Empty or placeholder sections** | Template text still present, or sections with no substantive content | `{Description of architectural approach}` |
| 6 | **Unresolved open questions** | `- [ ]` checkboxes in "Open Questions > Require Further Research" | `- [ ] Performance impact of encryption at rest` |
| 7 | **Research/investigation tasks in implementation plan** | Go/no-go items, feasibility studies, audit tasks, or items explicitly requiring PREPARE-phase runtime execution | `- Investigate whether Redis Streams can replace Kafka for our throughput needs` |

### Detection Guidance

- **Signals 1, 4, 6**: Search for `- [ ]` within the relevant section. Checked items (`- [x]`) are resolved and do not count.
- **Signal 2**: Scan table cells for the literal string "TBD" (case-insensitive).
- **Signal 3**: Search for the exact prefix `⚠️ Handled during`. Informal variants ("deferred to", "will be addressed in") are non-standard but should also raise suspicion.
- **Signal 5**: Look for curly-brace placeholders (`{...}`) or sections containing only headings with no content beneath them.
- **Signal 7**: Scan the implementation plan (e.g., "Implementation Sequence", "Code Phase") for tasks that involve research, investigation, feasibility assessment, or go/no-go decisions. These require PREPARE-phase runtime execution even if the plan's Preparation section appears complete. Common indicators: "investigate", "research", "evaluate", "assess feasibility", "determine whether", "audit", "spike".

### Usage

**In `plan-mode` (Phase 2 synthesis)**: Check each phase's plan section for these signals to populate the Phase Requirements table.

**In `orchestrate` (Context Assessment: Phase Skip Decision Flow)**: The completeness check is Layer 2 of the 3-layer skip protection. Before skipping a phase via an approved plan, verify its plan section passes — all 7 signals absent. Use skip reason `"plan_section_complete"`. (Phases can also be skipped via Layer 3 structured analysis with reason `"structured_gate_passed"` — see orchestrate.md for the full decision flow.)

---
