# Post-merge activation: actor-discriminator-capture-gate fixture campaign

This README documents the operational flow for activating the 5
currently-skipped TaskCompleted-fixture test stubs in
`pact-plugin/tests/test_actor_discriminator_capture_gate.py`
(class `TestPostMergeFollowUpSpecs`). Read this first if you are
the implementer picking up the captured-fixture campaign post-merge.

## Context

The 5 stubs encode SPEC SHAPES for assertions that activate only
when paired captured-from-production TaskCompleted stdin payloads
exist on disk under this directory. Each stub `pytest.skip()`s
on a fixture-presence guard until activation; the docstrings ARE
the spec the implementation must satisfy. The stubs cover:

- `test_captured_lead_context_fixture_shape_pin_spec` — pin the
  9-key lead-frame shape against the captured fixture.
- `test_captured_teammate_context_fixture_shape_pin_spec` — pin
  the 10-key teammate-frame shape (with `agent_id`) against the
  captured fixture.
- `test_lead_and_teammate_fixtures_paired_via_meta_provenance_spec`
  — enforce pair-revert-in-single-session discipline (the lead and
  teammate fixtures MUST share `_meta.capture_session_id` so a
  negative test cannot pass by absence).
- `test_786_post_tool_use_teammate_agent_id_presence_falsifier_spec`
  — the #786 falsifier: does the teammate PostToolUse payload carry
  `agent_id` as documented? Outcome A confirms cell-1/cell-3
  backstop; Outcome B falsifies and triggers a corridor follow-up.
- `test_806_subagent_stop_discriminator_audit_spec` — opportunistic
  rider for #806 if the campaign captures a SubagentStop fire.

## Activation gate sequence

The activation is strictly serial:

1. **Install the logging-shim** per the preparer's deliverable #2
   spec (`/private/tmp/install_logging_shim.sh`). The shim is
   version-agnostic (resolves the live plugin root dynamically);
   if it hardcodes an outdated `PACT/X.Y.Z` path, re-point it
   FIRST per preparer's deliverable #1 — silent zero-capture
   failure otherwise.

2. **Run a fresh post-merge PACT session** (NEW, not `/resume`,
   per the pinned "hooks-cannot-be-smoke-tested-against-the-
   running-plugin" constraint). Spawn at least one teammate task
   so the orchestrator naturally exercises both lead-context AND
   teammate-context TaskCompleted fires AND a teammate PostToolUse
   fire (the #786 falsifier).

3. **Capture both context arms.** The logging-shim writes captured
   stdin payloads to `/tmp/pact-hook-stdin-captures/`. Confirm
   BOTH a lead-context AND a teammate-context TaskCompleted fire
   were captured before promotion — single-arm captures cannot
   support the pair-revert-in-single-session discipline.

4. **Independent SEC-AC-2 classification.** For each captured
   payload, classify the actor via the composite signal
   (disk-record `owner` field + runtime env evidence per the
   preparer's deliverable #3), NOT by the predicate under test.
   If either leg fails to classify (e.g., disk says teammate but
   env evidence is missing), the capture is REJECTED — do NOT
   promote to a repo fixture; re-run the campaign.

5. **Promote paired fixtures with provenance metadata.** Land
   captured payloads at:
   - `pact-plugin/tests/fixtures/wake_lifecycle/taskcompleted_lead_context_shape.json`
   - `pact-plugin/tests/fixtures/wake_lifecycle/taskcompleted_teammate_context_shape.json`

   Each fixture MUST carry an `_meta` block:

   ```json
   {
     "_meta": {
       "capture_method": "logging-shim",
       "capture_session_id": "<pact-session-uuid>",
       "captured_at": "<ISO-8601 timestamp>",
       "actor_classification_signal": "disk-owner + runtime-env"
     },
     "session_id": "...",
     "task_id": "...",
     ...
   }
   ```

   The paired fixtures MUST share `capture_session_id` (pair-
   revert discipline).

6. **Lift the 5 skip markers and verify.** Remove the
   `pytest.skip(reason="POST-MERGE GATE: ...")` calls from the 5
   stubs and replace with the assertions described in each
   docstring. Run:

   ```sh
   cd pact-plugin
   python -m pytest tests/test_actor_discriminator_capture_gate.py::TestPostMergeFollowUpSpecs -v
   ```

   All 5 should pass on the captured fixtures.

## Forward-coupling: TS-2 PATH B contingency

If the campaign falsifies the documented schema — specifically,
if the teammate-context TaskCompleted payload does NOT carry
`agent_id` (the upstream-docs-claimed conditional-presence is
contradicted at runtime) — then:

- The cell-1/cell-3 backstop is wrong; Cell 2 (belt-and-
  suspenders `agent_id is None AND teammate_name is None`)
  becomes the load-bearing predicate.
- `TestSiblingDiscriminatorParity` (TS-2) parametric expected
  values MUST update WITH the predicate swap — the 8-payload
  body-identity proof becomes false under Cell 2 (PostToolUse
  stays single-field while TaskCompleted goes compound; parity
  breaks loudly on the documented divergence).
- `TestCounterTestMechanismDocumentation` (TS-15) has a guard
  that flags compound-predicate swap; update it per the same
  PATH B follow-up.

The 3 updates (predicate body + TS-2 expected values + TS-15
contingency guard) MUST land in one atomic follow-up PR. Per the
documented PATH B roadmap, this is its own bounded change; do not
bundle with unrelated post-merge polish.

## What this PR does NOT include

- The shim implementation itself (preparer deliverable #2; lives
  at `/private/tmp/`, not committed).
- The capture campaign session itself (post-merge operational
  activity, not a code artifact).
- The captured fixtures (this README's existence is the spec; the
  fixtures themselves are the post-merge follow-up's deliverable).
- The #786 disposition commit (Outcome A docstring-only OR
  Outcome B follow-up issue — both decisions land separately).
- The #806 SubagentStop fix (if any) — opportunistic capture only;
  #806's own session promotes any SubagentStop captures.
