# hook_event_name routing convention

Input-validation conventions for plugin hooks consuming Claude Code stdin
fields.

## Routing on `hook_event_name`

The platform-supplied `hook_event_name` field is the strongest available
signal for "what event is the platform telling us this is." Hooks bound
to multiple events (or hooks that need to distinguish event-name from
on-disk state) use it to route logic. `agent_handoff_emitter.py` is the
canonical first consumer.

Four rules apply when consuming `hook_event_name`:

1. **Compare with a string literal.** Use
   `if hook_event_name == "TaskCompleted":`, not
   `if hook_event_name.startswith("Task"):` or
   `if "Task" in hook_event_name:`. The platform contract is exact-match;
   substring comparisons widen the surface to spoofing or future
   event-name additions you didn't intend to handle.

2. **Never use as a path component.** Do not concatenate
   `hook_event_name` into filesystem paths, log file names, or shell
   arguments. The field is platform-controlled and may carry values your
   code did not anticipate; every untrusted-input rule from the existing
   path-traversal sanitization conventions still applies.

3. **Fail closed on non-string values.** A non-string `hook_event_name`
   (None, int, bool, dict) compares unequal to any string literal
   naturally. Do NOT cast (`str(hook_event_name)`) or trim (`.strip()`) —
   those silently normalize hostile inputs into a passing form. The
   string-literal compare is the type-validation; preserve it.

4. **Do not log `hook_event_name` outside one-shot diagnostic probes.**
   Logging a platform-controlled string into a shared log surface invites
   log-injection attacks (terminal escape sequences, embedded newlines
   spoofing other log lines, ANSI CSI cursor-control). The defense is to
   not log the raw value at all in production code paths. One-shot
   diagnostic probes (e.g., a temporary `/tmp/<hook>_diagnostic.log`
   capture during PREPARE-phase debugging) are exempt only when the log
   is read once by a trusted human and then deleted.

## Pinning the type-validation in tests

Tests that consume the production stdin shape pin two properties:

- **Verbatim shape**: a `PLATFORM_STDIN_SHAPE` constant captured from
  real platform fires, used as a fixture so future hook changes are
  tested against what the platform actually delivers (not a synthetic
  shape the test author guessed). See
  `test_emitter_real_disk.py::TestStdinShapePin`.

- **No-leakage invariant**: the journal event payload (or whatever the
  hook produces) must NOT forward fields the contract didn't promise.
  Tests assert via `assert leaked_field not in event` for every stdin
  field the hook intentionally drops.

When adding a new hook that consumes stdin, follow the same pattern:

1. Capture a verbatim platform stdin sample by instrumenting the
   installed cache copy of the hook, firing real platform events, and
   reverting the instrumentation before the change ships.
2. Pin the captured shape as a module-level fixture in the hook's test
   file.
3. Add tests that exercise both the production shape (with
   `hook_event_name`) and the fallback shape (without it, if the hook
   supports forward-compat).

## Cross-references

- `pact-plugin/hooks/agent_handoff_emitter.py` — canonical first
  consumer. Shows the routing pattern
  (`if hook_event != "TaskCompleted":`) with the fallback branch for
  forward-compat.
- `pact-plugin/tests/test_emitter_real_disk.py::TestStdinShapePin` —
  pins the verbatim shape + no-leakage invariant.
- `pact-plugin/tests/test_emitter_happy_and_gates.py::TestStatusFallbackGate` —
  pins the forward-compat fallback path (stdin lacks `hook_event_name`).
- `pact-plugin/tests/test_emitter_happy_and_gates.py::TestProductionShapeMetadataOnly` —
  pins production-shape behavior including the handoff-presence gate.
