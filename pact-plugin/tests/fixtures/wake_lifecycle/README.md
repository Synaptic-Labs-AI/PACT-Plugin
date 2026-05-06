# wake_lifecycle hook stdin fixtures

Captured `PostToolUse` stdin payloads for the
`pact-plugin/hooks/wake_lifecycle_emitter.py` hook. These fixtures fossilize
the **production** shape of `tool_response` for `TaskCreate` and `TaskUpdate`
so tests cannot silently drift away from what the platform actually delivers.

Background: between PR #603 (regression introduction) and #620 (fix), every
test in `test_inbox_wake_lifecycle_emitter.py` used a hand-constructed flat
`tool_response: {"id": "..."}` payload. Production `TaskCreate` `tool_response`
is **nested** (`tool_response.task.id`) per #612's logging-shim capture from
session `pact-56ce3a2a` on 2026-05-02. The hook silently returned `None` on
every TaskCreate while tests stayed green. This directory exists to make that
class of failure structurally impossible going forward.

## Capture-provenance convention (MANDATORY)

Every fixture in this directory MUST be a JSON object with a sibling
top-level `_meta` key documenting where the payload came from:

```json
{
  "_meta": {
    "capture_session_id": "pact-56ce3a2a",
    "capture_date": "2026-05-02",
    "capture_method": "logging-shim",
    "issue_ref": "#612"
  },
  "tool_name": "TaskCreate",
  "tool_input": { "...": "..." },
  "tool_response": { "task": { "id": "...", "...": "..." } }
}
```

`_meta` is a sibling top-level key. It is NOT nested inside `tool_input` or
`tool_response`. Tests read it for diagnostic context and ignore it when
piping the payload through the hook (the hook itself ignores unknown
top-level keys).

### `_meta` fields

| Field                 | Required | Purpose                                                                    |
| --------------------- | -------- | -------------------------------------------------------------------------- |
| `capture_session_id`  | Yes      | PACT session ID where the payload was captured (e.g., `pact-56ce3a2a`).    |
| `capture_date`        | Yes      | ISO-8601 date of capture (e.g., `2026-05-02`).                             |
| `capture_method`      | Yes      | How it was captured: `logging-shim`, `manual-stdin-redirect`, `synthesized`, or `legacy`. |
| `issue_ref`           | Yes      | Issue or PR that justifies preserving this fixture (e.g., `#612`).         |
| `notes`               | No       | Free-form notes (e.g., "preserved as regression backstop").                |

### `capture_method` values

- `logging-shim` — payload was captured by an in-hook stdin logger writing
  the raw stdin bytes to a side-channel file. Highest fidelity; preferred for
  any new fixture covering platform-shape behavior.
- `manual-stdin-redirect` — payload was captured by tee-ing the hook's
  stdin into a file during a real PACT session. Equivalent fidelity to
  logging-shim; noted separately for traceability.
- `synthesized` — payload was hand-reconstructed (typically derived from
  an existing logging-shim shape with id/subject/owner re-parameterized
  for a new scenario). May be lossy relative to a live production payload;
  use ONLY when the scenario being modeled cannot be observed in
  production (e.g., a race window that requires controlled timing). Notes
  field MUST disclose the source fixture the shape was derived from.
- `legacy` — payload predates the convention and was hand-constructed.
  Permitted ONLY for backward-compat regression backstops (i.e., tests that
  intentionally assert behavior on the broken pre-fix shape). Never use
  `legacy` for new shape-resilience fixtures.

## Future hooks

This convention applies to **all future hook-stdin fixtures**, not just
wake_lifecycle. When adding fixtures for another hook (e.g., the
`peer_inject` SubagentStart payload referenced by the audit-test addendum
on PR B / #628), create a sibling subdirectory with its own README and
mirror this convention. The provenance-capture discipline IS the structural
defense against the failure class that #620 surfaced.
