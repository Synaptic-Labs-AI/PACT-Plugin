# Fast-suite duplicate consolidations — ablation proofs

Four duplicate test pairs were consolidated in this change. Identical bodies
do not prove the surviving copy protects the seam, so each deletion was
accompanied by an ablation in which the SURVIVOR must go red. This runbook
records those proofs so a reviewer can reproduce them from the repository
rather than from a session store that drains.

Run everything from the repository root's `pact-plugin/` directory with the
project interpreter. Each ablation: apply the edit, run the survivor, observe
the failure, then restore with `git checkout -- <path>` and confirm
`git diff --stat -- <path>` is empty.

## 1. `tests/test_embedding_catchup.py`

- Survivor: `TestMinCatchupRamMbConstant::test_embed_pending_memories_default_uses_constant`
- Deleted duplicate: `TestEmbedPendingMemories::test_default_min_ram_mb_uses_constant`
- Seam edit: in `skills/pact-memory/scripts/embedding_catchup.py`, change
  `min_ram_mb: float = MIN_CATCHUP_RAM_MB` to `min_ram_mb: float = 60.0`
  (the constant is 75.0).
- Command:
  `python -m pytest -q -p no:cacheprovider "tests/test_embedding_catchup.py::TestMinCatchupRamMbConstant::test_embed_pending_memories_default_uses_constant"`
- Expected survivor failure:
  `>       assert default == MIN_CATCHUP_RAM_MB`
  `E       assert 60.0 == 75.0`
  `tests/test_embedding_catchup.py:117: AssertionError` — `1 failed`
- Restore: `git checkout -- pact-plugin/skills/pact-memory/scripts/embedding_catchup.py`;
  `grep -c "min_ram_mb: float = MIN_CATCHUP_RAM_MB"` returns 1.

## 2. `tests/test_intentional_wait.py`

- Survivor: `TestKnownReasonsLiteralRegressionGuard::test_awaiting_lead_completion_is_present`
- Deleted duplicate: `TestKnownReasonsCompletionAddition::test_awaiting_lead_completion_in_known_reasons`
- Seam edit (COORDINATED, the decisive arm): remove
  `"awaiting_lead_completion",` from `KNOWN_REASONS` in
  `hooks/shared/intentional_wait.py` AND from `EXPECTED_REASONS` in the test
  file. A production-only removal would also redden the exact-set sibling;
  only the coordinated edit proves the literal pin is not subsumed.
- Command:
  `python -m pytest -q -p no:cacheprovider "tests/test_intentional_wait.py::TestKnownReasonsLiteralRegressionGuard::test_exact_set" "tests/test_intentional_wait.py::TestKnownReasonsLiteralRegressionGuard::test_awaiting_lead_completion_is_present"`
- Expected: the literal pin FAILS with
  `E       AssertionError: assert 'awaiting_lead_completion' in frozenset({...})`
  (`tests/test_intentional_wait.py:1054`), while `test_exact_set` PASSES —
  `1 failed, 1 passed`.
- Restore: `git checkout -- pact-plugin/hooks/shared/intentional_wait.py` and
  restore the removed line in the test file; both tests then pass.

## 3. `tests/test_merge_guard.py`

- Survivor: `TestFalsePositivePrevention::test_semicolon_after_echo_still_detected`
- Deleted duplicate: `TestAdversarialCommandDetection::test_command_after_semicolon`
- Seam edit: in `hooks/shared/merge_guard_common.py`, inside
  `is_dangerous_command()`, change `return _is_dangerous_at_depth(command, 0)`
  to `return _is_dangerous_at_depth(command.split(";", 1)[0], 0)` (the
  scanner stops at the first semicolon, modelling a lost chain leg).
- Command:
  `python -m pytest -q -p no:cacheprovider "tests/test_merge_guard.py::TestFalsePositivePrevention::test_semicolon_after_echo_still_detected"`
- Expected survivor failure:
  `>       assert is_dangerous_command("echo hello; gh pr merge 42")`
  `E       AssertionError: assert False`
  `E        +  where False = <function is_dangerous_command ...>('echo hello; gh pr merge 42')`
  `tests/test_merge_guard.py:1615: AssertionError` — `1 failed`
- Control: the ablated function still returns True for a bare
  `gh pr merge 42`, so the mutation removes the chain case only.
- Restore: `git checkout -- pact-plugin/hooks/shared/merge_guard_common.py`;
  the survivor passes again (`1 passed`).

## 4. `tests/test_peer_inject.py`

- Survivor: `TestPeerInject::test_alone_message_when_only_member`
- Deleted duplicate: `TestTeachbackReminder::test_reminder_appended_when_alone`
  (the deleted copy asserted no reminder text, so nothing is lost).
- Seam edit: in `hooks/shared/peer_context.py`, change
  `peer_context = "You are the only active teammate on this team."` to
  `peer_context = "You are the only teammate on this team."`.
- Command:
  `python -m pytest -q -p no:cacheprovider "tests/test_peer_inject.py::TestPeerInject::test_alone_message_when_only_member"`
- Expected survivor failure:
  `>       assert "only active teammate" in result.lower()`
  `E       AssertionError` (`tests/test_peer_inject.py:144`) — `1 failed`
- Restore: `git checkout -- pact-plugin/hooks/shared/peer_context.py`.

## Verdict

All four survivors went RED under their ablations; none stayed green. The
four production seams were restored and `git diff --stat` over them is
empty. The four affected files re-ran green afterwards.