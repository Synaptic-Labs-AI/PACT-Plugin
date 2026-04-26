# Hook Input Conventions

Input-validation conventions for plugin hooks consuming Claude Code stdin fields.

These conventions emerged from issue #551 (commit 8772beb) and the subsequent
peer-review cycles (commits bbff9f4, [cycle-2 commit]). They apply to every
plugin hook that reads stdin JSON from Claude Code's hook dispatcher.

## Routing on `hook_event_name`

The platform-supplied `hook_event_name` field is the strongest available signal
for "what event is the platform telling us this is." Hooks bound to multiple
events (or hooks that need to distinguish event-name from on-disk state) use it
to route logic. PR #563's `agent_handoff_emitter.py` is the canonical first
consumer.

Four rules apply when consuming `hook_event_name`:

1. **Compare with a string literal.** Use `if hook_event_name == "TaskCompleted":`,
   not `if hook_event_name.startswith("Task"):` or `if "Task" in hook_event_name:`.
   The platform contract is exact-match; substring comparisons widen the surface
   to spoofing or future event-name additions you didn't intend to handle.

2. **Never use as a path component.** Do not concatenate `hook_event_name` into
   filesystem paths, log file names, or shell arguments. The field is
   platform-controlled and may contain values your code didn't anticipate;
   every untrusted-input rule from the existing path-traversal sanitization
   conventions still applies.

3. **Fail closed on non-string values.** A non-string `hook_event_name` (None,
   int, bool, dict) compares unequal to any string literal naturally. Do NOT
   cast (`str(hook_event_name)`) or trim (`.strip()`) — those silently
   normalize hostile inputs into a passing form. The string-literal compare
   is the type-validation; preserve it.

4. **Never log without sanitization.** If a hook logs `hook_event_name` for
   diagnostic purposes (rare; usually only during `/tmp/<hook>_diagnostic.log`-
   style probes per #551 PREPARE), apply the same path-component sanitization
   used for `task_id` / `team_name` in `agent_handoff_emitter._sanitize_path_component`.
   Otherwise a log-injection attack via crafted `hook_event_name` becomes
   possible.

## Pinning the type-validation in tests

Tests that consume the production stdin shape pin two properties:

- **Verbatim shape**: a `PLATFORM_STDIN_SHAPE` constant captured from real
  platform fires, used as a fixture so future emitter changes are tested
  against what the platform actually delivers (not a synthetic shape the
  test author guessed). See `test_agent_handoff_emitter.py::TestStdinShapePin`.

- **No-leakage invariant**: the journal event payload (or whatever the hook
  produces) must NOT forward fields the contract didn't promise. Tests assert
  via `assert leaked_field not in event` for every stdin field the hook
  intentionally drops.

When adding a new hook that consumes stdin, follow the same pattern:

1. Capture a verbatim platform stdin sample during PREPARE-phase probing
   (instrument the installed cache copy of the hook, fire real platform
   events, revert before phase complete).
2. Pin the captured shape as a module-level fixture in the hook's test file.
3. Add tests that exercise both the production shape (with `hook_event_name`)
   and the fallback shape (without it, if the hook supports forward-compat).

## Cross-references

- `pact-plugin/hooks/agent_handoff_emitter.py` — canonical first consumer.
  Shows the routing pattern (`if hook_event != "TaskCompleted":`) with the
  fallback branch for forward-compat.
- `pact-plugin/tests/test_agent_handoff_emitter.py` — pins the conventions
  via `TestStdinShapePin` (verbatim shape + no-leakage), `TestStatusFallbackGate`
  (forward-compat fallback path), and `TestProductionShapeMetadataOnly`
  (production-shape behavior including the Option E handoff-presence gate).
- `docs/architecture/551-fix-shape-decision.md` (worktree-only, gitignored) —
  Option B + Option E rationale that motivated these conventions.
