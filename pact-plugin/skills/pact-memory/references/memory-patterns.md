# PACT Memory Usage Patterns

This document provides patterns and examples for effective use of the PACT Memory
skill across different scenarios.

## Pattern 1: Phase Completion Memory

Save context after completing each PACT phase to preserve learnings.

### After Prepare Phase

```python
memory.save({
    "context": "Completed research phase for the API gateway authentication system on the feature/api-auth branch. The project requires securing 15+ microservices that communicate both internally and with external third-party clients. Current architecture has no centralized auth - each service handles its own validation, leading to inconsistent security policies and duplicated code. Stakeholders require SSO capability for enterprise customers and API key support for developer integrations. Evaluated three main approaches: OAuth2 with dedicated auth server, JWT tokens with shared secret, and a hybrid approach. Reviewed documentation for Auth0, Keycloak, and custom implementations.",
    "goal": "Determine the optimal authentication strategy for the API gateway that balances security requirements, implementation complexity, and support for both internal services and external third-party integrations.",
    "lessons_learned": [
        "OAuth2 adds significant complexity (auth server, token endpoints, refresh flows) but provides robust third-party integration with standard scopes and consent flows",
        "JWT works well for internal service-to-service auth because services can validate tokens independently without network calls, reducing latency",
        "Token revocation is the critical differentiator - JWTs are stateless so revocation requires either short expiry times or a distributed blacklist",
        "Auth0 pricing becomes prohibitive at our scale (50k+ MAU), making self-hosted Keycloak or custom solution more viable",
        "Existing services already use a shared Redis cluster, which could serve as a token blacklist store"
    ],
    "decisions": [
        {
            "decision": "Use JWT for internal services, OAuth2 for external third-party clients",
            "rationale": "Hybrid approach provides the best of both worlds. Internal services get low-latency validation with JWTs (signed with RS256 for security). External clients get standard OAuth2 flows they expect, with proper scope management. Both can share the same identity database.",
            "alternatives": ["OAuth2 everywhere - rejected due to latency impact on internal service mesh", "Custom token system - rejected due to maintenance burden and security risk of non-standard approach"]
        }
    ],
    "entities": [
        {"name": "APIGateway", "type": "component", "notes": "Kong-based gateway, will handle OAuth2 token exchange"},
        {"name": "AuthService", "type": "service", "notes": "New service to be created for identity management"},
        {"name": "Redis Cluster", "type": "infrastructure", "notes": "Existing cluster to be used for token blacklist"}
    ]
})
```

### After Architect Phase

```python
memory.save({
    "context": "Designing authentication microservice architecture",
    "goal": "Create scalable auth service with token management",
    "active_tasks": [
        {"task": "Define API contracts", "status": "completed"},
        {"task": "Design database schema", "status": "completed"},
        {"task": "Plan caching strategy", "status": "in_progress"}
    ],
    "decisions": [
        {
            "decision": "Use Redis for token blacklist",
            "rationale": "Fast TTL, distributed, already in stack",
            "alternatives": ["PostgreSQL with TTL", "In-memory cache"]
        },
        {
            "decision": "Separate refresh token table",
            "rationale": "Different lifecycle, rotation tracking",
            "alternatives": ["Single token table with type field"]
        }
    ],
    "entities": [
        {"name": "TokenBlacklist", "type": "component", "notes": "Redis-backed"},
        {"name": "RefreshTokenStore", "type": "table"}
    ]
})
```

### After Code Phase

```python
memory.save({
    "context": "Implemented JWT authentication with refresh tokens",
    "goal": "Complete auth service implementation",
    "lessons_learned": [
        "PyJWT requires explicit algorithm specification",
        "Redis SCAN is safer than KEYS for production",
        "Refresh token rotation prevents replay attacks"
    ],
    "decisions": [
        {
            "decision": "Use sliding window for rate limiting",
            "rationale": "Smoother experience than fixed window",
            "alternatives": ["Fixed window", "Token bucket"]
        }
    ],
    "entities": [
        {"name": "JWTHandler", "type": "class"},
        {"name": "RateLimiter", "type": "middleware"}
    ]
})
```

### After Test Phase

```python
memory.save({
    "context": "Completed authentication service testing",
    "goal": "Ensure auth service reliability and security",
    "lessons_learned": [
        "Mock Redis for unit tests, real Redis for integration",
        "Time-based tests need freezegun or similar",
        "Security tests should cover token tampering scenarios"
    ],
    "decisions": [
        {
            "decision": "Add chaos testing for Redis failures",
            "rationale": "Auth must gracefully degrade",
            "alternatives": ["Skip chaos testing for MVP"]
        }
    ]
})
```

## Pattern 2: Blocker Documentation

When hitting a blocker, save context for future reference.

```python
memory.save({
    "context": "Blocked on sqlite-lembed installation on M1 Mac",
    "goal": "Enable local embeddings for memory skill",
    "lessons_learned": [
        "sqlite-lembed requires Rosetta 2 on M1",
        "Alternative: use sentence-transformers as fallback",
        "Binary distribution needs architecture-specific builds"
    ],
    "decisions": [
        {
            "decision": "Implement fallback chain for embeddings",
            "rationale": "Graceful degradation over hard failure"
        }
    ],
    "active_tasks": [
        {"task": "Add sentence-transformers fallback", "status": "pending", "priority": "high"},
        {"task": "Test cross-platform compatibility", "status": "pending"}
    ]
})
```

## Pattern 3: Search Before Starting

Query for relevant context before beginning work.

```python
# Starting work on authentication
results = memory.search("authentication security tokens")

for mem in results:
    print(f"\n=== Past Context ===")
    print(f"Context: {mem.context}")
    print(f"Goal: {mem.goal}")

    if mem.lessons_learned:
        print(f"\nLessons:")
        for lesson in mem.lessons_learned:
            print(f"  - {lesson}")

    if mem.decisions:
        print(f"\nDecisions:")
        for dec in mem.decisions:
            print(f"  - {dec.decision}")
            if dec.rationale:
                print(f"    Rationale: {dec.rationale}")
```

## Pattern 4: File-Based Context

Search for memories related to files you're working on.

```python
# Get context for the file you're editing
current_file = "src/auth/token_manager.py"
related = memory.search_by_file(current_file)

for mem in related:
    print(f"Previous work on related files:")
    print(f"  Context: {mem.context}")
    print(f"  Files: {', '.join(mem.files)}")
```

## Pattern 5: Decision Tracking

Use memories as a decision log across the project.

```python
# Search for past decisions on a topic
decisions = memory.search("caching strategy decisions")

# Compile decision history
for mem in decisions:
    if mem.decisions:
        print(f"\n{mem.created_at}: {mem.context}")
        for dec in mem.decisions:
            print(f"  Decision: {dec.decision}")
            print(f"  Rationale: {dec.rationale}")
            if dec.alternatives:
                print(f"  Alternatives: {', '.join(dec.alternatives)}")
```

## Pattern 6: Entity Reference

Build up knowledge about system components.

```python
# Search for memories mentioning a component
auth_memories = memory.search("AuthService")

# Compile entity knowledge
entity_notes = {}
for mem in auth_memories:
    for entity in mem.entities:
        if entity.name not in entity_notes:
            entity_notes[entity.name] = {
                "type": entity.type,
                "notes": []
            }
        if entity.notes:
            entity_notes[entity.name]["notes"].append(entity.notes)

# Display accumulated knowledge
for name, info in entity_notes.items():
    print(f"{name} ({info['type']})")
    for note in info["notes"]:
        print(f"  - {note}")
```

## Pattern 7: Session Wrap-Up

Save comprehensive session summary before ending.

```python
# Get files modified in this session
tracked_files = memory.get_tracked_files()

memory.save({
    "context": "Wrapping up a 4-hour session on the feature/jwt-auth branch implementing the JWT authentication system with refresh token support. Started the session by reviewing the architecture docs from the previous phase, then implemented the core TokenManager class and RateLimiter middleware. Hit a blocker mid-session when Redis connection pooling caused memory leaks under load testing - resolved by switching from redis-py's default connection handling to explicit pool management with max_connections=50. The implementation now passes all unit tests (47 tests) and integration tests (12 tests). Deferred chaos testing based on discussion with tech lead who wants to review the core implementation first. PR #234 is ready for review with all CI checks passing.",
    "goal": "Complete the JWT authentication implementation with refresh token rotation, including rate limiting middleware, ready for code review and stakeholder demo scheduled for tomorrow.",
    "active_tasks": [
        {"task": "Add unit tests for TokenManager", "status": "completed"},
        {"task": "Implement rate limiting middleware", "status": "completed"},
        {"task": "Fix Redis connection pooling memory leak", "status": "completed"},
        {"task": "Add chaos tests for Redis failures", "status": "pending", "priority": "medium"}
    ],
    "lessons_learned": [
        "Token rotation requires careful state management - we track the previous token hash to allow a 30-second grace period for in-flight requests using the old token",
        "Redis connection pooling is essential for performance, but redis-py's default lazy connection creation causes memory issues under burst load. Explicit pool with max_connections and socket_timeout prevents resource exhaustion",
        "Always log auth failures with correlation IDs - debugging token issues in production is nearly impossible without request tracing. Added X-Correlation-ID header propagation through the middleware chain",
        "Rate limiting at the gateway level catches most abuse, but service-level limits are still needed for internal service-to-service calls that bypass the gateway",
        "PyJWT's decode() method silently accepts expired tokens unless you explicitly pass options={'verify_exp': True} - this default is dangerous and should be overridden"
    ],
    "decisions": [
        {
            "decision": "Defer chaos testing to next sprint",
            "rationale": "Core functionality is complete and tested. Tech lead wants to review the implementation before we invest in chaos testing. This also gives the team time to set up the chaos engineering infrastructure (Chaos Monkey integration). Stakeholder demo is tomorrow and chaos tests aren't required for that milestone.",
            "alternatives": ["Complete chaos tests now - rejected due to time constraints and missing infrastructure", "Skip chaos tests entirely - rejected as auth service is critical path"]
        }
    ],
    "entities": [
        {"name": "TokenManager", "type": "class", "notes": "Core JWT handling with RS256 signing, refresh rotation, and 30-second grace period"},
        {"name": "RateLimiter", "type": "middleware", "notes": "Sliding window algorithm, 100 req/min default, configurable per-route"},
        {"name": "AuthService", "type": "service", "notes": "Main entry point, exposes /login, /logout, /refresh, /validate endpoints"},
        {"name": "src/auth/token_manager.py", "type": "file", "notes": "Primary implementation file, 340 lines"},
        {"name": "src/middleware/rate_limit.py", "type": "file", "notes": "Rate limiting middleware, 180 lines"}
    ]
},
files=tracked_files,
include_tracked=False  # We're explicitly providing files
)
```

## Pattern 8: Incremental Learning

Update memories as understanding evolves. `memory.update()` merges list-valued
fields additively with content-hash deduplication, so you can pass just the new
items — no read-merge-write-back required.

```python
# Append new lessons and a new entity to memory abc123.
# The existing lessons_learned and entities are preserved; duplicates (by
# content hash) are silently deduplicated, so this call is idempotent.
memory.update("abc123", {
    "lessons_learned": [
        "Redis cluster mode requires different connection handling",
        "Sentinel provides better HA than standalone Redis",
    ],
    "entities": [
        {"name": "RedisSentinel", "type": "component", "notes": "HA setup"},
    ],
})
```

**Content-hash dedup makes incremental learning cheap.** Calling `update()`
twice with the same lesson stores it once. You don't need to check "did I
already save this?" — just call `update` and let the merge handle it.

**When you genuinely want wholesale replacement** (e.g. removing a stale item
from a list), pass `replace=True`:

```python
# Replace the entire lessons_learned column with exactly this one item.
# Any existing lessons are discarded.
memory.update("abc123", {"lessons_learned": ["Only lesson that matters"]}, replace=True)
```

> ⚠️ Before this behavior existed (pre-#374), partial list updates silently
> clobbered the entire column. If you see code that reads → merges in Python →
> writes back, it's obsolete — delete the read/merge scaffolding and pass just
> the new items directly to `update()`.

## Anti-Patterns to Avoid

### Too Vague

```python
# BAD - no actionable information, future you learns nothing
memory.save({
    "context": "Working on auth",
    "lessons_learned": ["Things were hard"]
})

# GOOD - comprehensive and actionable
memory.save({
    "context": "Debugging JWT token validation failures on the feature/auth-fixes branch. Users reported 401 errors after ~15 minutes of activity. Investigation revealed the issue was in the token refresh logic where concurrent requests could trigger multiple refresh attempts, causing token rotation conflicts. The auth system uses access tokens (15min TTL) with refresh tokens (7 day TTL, single-use with rotation).",
    "goal": "Identify and fix the root cause of intermittent 401 errors occurring after extended user sessions.",
    "lessons_learned": [
        "Concurrent API requests detecting expired tokens simultaneously each triggered their own refresh, causing the server to invalidate the 'old' refresh token before all requests could use it",
        "Added a mutex pattern around token refresh - first request to detect expiry acquires lock and refreshes, others wait for the new token",
        "The bug was hard to reproduce locally because it requires high latency (>500ms) to create the race window"
    ]
})
```

### Too Granular

```python
# BAD - noise in the memory system, not worth persisting
memory.save({
    "context": "Fixed typo in variable name",
    "lessons_learned": ["Check spelling"]
})

# Note: Small fixes don't warrant memories. Save memories for:
# - Phase completions
# - Significant decisions with rationale
# - Non-obvious lessons that would help future work
# - Blockers and their resolutions
```

### Missing Rationale

```python
# BAD - decision without context is useless for future reference
memory.save({
    "decisions": [
        {"decision": "Use Redis"}  # Why? What alternatives? When does this apply?
    ]
})

# GOOD - decision with full context
memory.save({
    "decisions": [
        {
            "decision": "Use Redis for token blacklist instead of PostgreSQL",
            "rationale": "Token blacklist requires fast writes (every logout/refresh) and automatic TTL-based cleanup. Redis provides O(1) writes, native TTL expiry, and we already have a cluster deployed. PostgreSQL would require manual cleanup jobs and adds latency.",
            "alternatives": [
                "PostgreSQL with TTL column - rejected due to need for cleanup cron job and slower writes",
                "In-memory cache per service - rejected because tokens would remain valid on other instances after logout"
            ]
        }
    ]
})
```

### No Entity Links

```python
# BAD - hard to connect to related work, won't surface in graph search
memory.save({
    "context": "Refactored the authentication service",
    # Missing: which components? what files?
})

# GOOD - entities enable graph-based retrieval
memory.save({
    "context": "Refactored the authentication service to extract token management into a dedicated TokenManager class. This improves testability and separates concerns between identity validation and token lifecycle management.",
    "entities": [
        {"name": "AuthService", "type": "service", "notes": "Now delegates token ops to TokenManager"},
        {"name": "TokenManager", "type": "class", "notes": "New class extracted from AuthService"},
        {"name": "src/auth/auth_service.py", "type": "file", "notes": "Reduced from 450 to 280 lines"},
        {"name": "src/auth/token_manager.py", "type": "file", "notes": "New file, 200 lines"}
    ]
})
```

## Best Practices Summary

1. **Be Specific**: Include concrete details that will be useful later
2. **Capture Rationale**: Document why, not just what
3. **Link Entities**: Reference components for graph connectivity
4. **Include Alternatives**: Record options that were considered
5. **Save at Transitions**: Phase completions, blockers, decisions
6. **Search First**: Check for relevant context before starting
7. **Update Incrementally**: Add to existing memories as you learn
