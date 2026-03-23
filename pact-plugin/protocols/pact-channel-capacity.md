## Channel Capacity Management

> **Cybernetic basis**: Shannon's Channel Capacity Theorem — every communication channel has a
> finite throughput. In PACT, the "channel" is the context window; exceeding capacity degrades
> signal quality. Distinct from source coding (handoff compression), which is addressed by the
> Transduction Protocol.

Channel capacity defines how much information can cross a VSM boundary per interaction without degradation, and what to do when capacity is approached.

### Context Window as Channel

| Property | Shannon Channel | PACT Context Window |
|----------|----------------|---------------------|
| **Capacity** | Bits per second | Tokens per interaction |
| **Noise** | Physical interference | Irrelevant context, stale state, compaction artifacts |
| **Throughput** | Data rate | Useful information processed per phase |
| **Error** | Bit errors | Misunderstood requirements, dropped context, hallucinated state |

### Capacity Indicators

The orchestrator monitors these signals to assess current channel load:

| Indicator | Healthy | Degraded | Critical |
|-----------|---------|----------|----------|
| **Compaction frequency** | 0-1 per phase | 2-3 per phase | 4+ per phase |
| **State reconstruction** | Not needed | Occasional TaskGet for recovery | Frequent state loss requiring full reconstruction |
| **Agent dispatch clarity** | Agents start work without clarification | Occasional teachback corrections | Agents frequently misunderstand assignments |
| **Handoff fidelity** | Lossless fields intact | Some fields missing, recoverable | Critical fields lost, requires re-work |

### Batch Protocol

When capacity indicators show degradation, batch information to reduce boundary crossings:

**Batching strategies**:
1. **Combine handoffs**: If multiple agents complete near-simultaneously, process handoffs in one batch rather than interleaving with other work
2. **Defer non-critical updates**: CLAUDE.md updates, memory processing, and status reporting can be deferred to natural pauses
3. **Compress dispatch context**: For subsequent agents, reference upstream task IDs for `TaskGet` retrieval rather than inlining full context
4. **Prioritize lossless fields**: When summarizing, preserve lossless fields (produced, integration_points, open_questions) and compress lossy fields (reasoning_chain, detailed rationale)

### Capacity Signals

```
📊 CAPACITY SIGNAL: [NOMINAL|ELEVATED|CRITICAL]

Current load: [compaction count / dispatch clarity / handoff fidelity]
Trend: [stable / increasing / decreasing]
Recommended action: [continue | batch | compact | pause-and-recover]
```

| Signal | Meaning | Action |
|--------|---------|--------|
| **NOMINAL** | Capacity healthy | Continue normal operations |
| **ELEVATED** | Approaching limits | Batch handoffs; compress dispatch context; defer non-critical work |
| **CRITICAL** | Capacity exceeded | Pause dispatching; recover state via TaskGet; consider session checkpoint |

### Active Back-Pressure

When capacity signals indicate ELEVATED or CRITICAL, the orchestrator applies back-pressure to reduce throughput demands:

**ELEVATED back-pressure**:
- Sequence remaining agent dispatches instead of parallel (reduce concurrent load)
- Compress dispatch prompts to essential context + TaskGet references
- Defer memory processing and CLAUDE.md updates to next natural pause
- Request shorter progress signals from agents ("summary only, skip reasoning")

**CRITICAL back-pressure**:
- Pause all new agent dispatches
- Trigger session checkpoint via `/PACT:pause` (persists state to paused-state.json)
- Invoke self-repair Pattern 1 (organizational state snapshot) before proceeding
- If resuming: use TaskGet + organizational snapshot for state reconstruction instead of re-reading files

**Self-regulation**: Back-pressure is the orchestrator's primary response to its own capacity limits. It bridges the gap between observing capacity degradation (monitoring) and acting on it (adaptation). The orchestrator should apply back-pressure before capacity signals reach CRITICAL — early intervention at ELEVATED prevents cascading degradation.

### Relationship to Other Protocols

- **Transduction** ([pact-transduction.md](pact-transduction.md)): Transduction addresses *translation quality* (does meaning survive?). Channel capacity addresses *throughput limits* (can we process this volume?). They are complementary — high-fidelity transduction is meaningless if the channel is overloaded.
- **S4 Checkpoints** ([pact-s4-checkpoints.md](pact-s4-checkpoints.md)): Capacity degradation should trigger an S4 checkpoint — "Is our approach still viable given capacity constraints?"
- **Variety Management** ([pact-variety.md](pact-variety.md)): High-variety tasks consume more channel capacity. Variety scoring should inform capacity planning.

---
