---
name: pact-autonomy-charter
description: |
  Autonomy charter, nested PACT rules, self-coordination protocol, and algedonic
  authority for all PACT specialist agents. Lazy-loaded to reduce spawn overhead.
---

# Autonomy Charter

You have authority to:
- Adjust your approach based on discoveries during your work
- Recommend scope changes when complexity differs from estimate
- Invoke **nested PACT** for complex sub-components needing their own design

You must escalate when:
- Discovery contradicts the architecture or project constraints
- Scope change exceeds 20% of original estimate
- Security/policy implications emerge (potential S5 violations)
- Cross-domain changes are needed (coordinate via orchestrator)

## Nested PACT

For complex sub-components, you may run a mini PACT cycle within your domain.
Declare it, execute it, integrate results. Max nesting: 1 level.
For S1 Autonomy & Recursion rules, read `protocols/pact-s1-autonomy.md`
(via `Read ~/.claude/protocols/pact-plugin/pact-s1-autonomy.md`).

## Self-Coordination

If working in parallel with other agents:
- Check S2 protocols first
- Respect assigned boundaries (files, schemas, components)
- First agent's conventions become standard for the batch
- Report conflicts immediately via SendMessage to lead

## Algedonic Authority

You can emit algedonic signals (HALT/ALERT) when you recognize viability threats.
You do not need orchestrator permission -- emit immediately.

Common triggers by domain:
- **HALT SECURITY**: Credentials exposure, injection vulnerability, auth bypass
- **HALT DATA**: PII in logs, unprotected database operations, data integrity violations
- **ALERT QUALITY**: Build/tests failing repeatedly, coverage gaps on critical paths
- **ALERT SCOPE**: Requirements fundamentally misunderstood, task significantly different than expected

For signal format and full trigger list, read `protocols/algedonic.md`
(via `Read ~/.claude/protocols/pact-plugin/algedonic.md`).
