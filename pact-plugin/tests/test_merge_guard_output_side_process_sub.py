"""Output-side process-substitution-to-shell detection (#1002 / F2).

What F2 fixes
-------------
``_has_process_substitution_to_shell`` (``merge_guard_pre.py``) originally only
matched the *input-side* form ``bash <(...)`` — a shell consuming a process
substitution as its input script. It missed the **output-side** form
``cmd > >(bash)``, where a stdout-routing redirect feeds ``cmd``'s stdout into a
shell. For the echo/printf strip carrier (carrier 3, the only true stdout
vector — PREPARE §3.1), ``echo "gh pr merge 42" > >(bash)`` feeds the dangerous
literal to bash, yet the original guard returned ``False`` so the carrier
stripped the echo argument and ``is_dangerous_command`` read it as safe => a real
**under-block**.

F2 adds the output-side arm
``(?:&>>?|1>>?|(?<![0-9])>>?)\\s*>\\(\\s*(?:bash|sh|zsh)\\b``:

* operators are restricted to **stdout-routing** redirects — ``>``, ``>>``,
  ``1>``, ``1>>``, ``&>``, ``&>>``. The ``(?<![0-9])`` on the bare-``>`` arm
  excludes a digit-prefixed fd (so ``2>``/``3>`` stderr routing is NOT matched;
  ``1>`` is still caught by its own explicit arm);
* the target is restricted to the **shell interpreter** set ``bash|sh|zsh`` —
  the same set as the input-side guard — so non-shell consumers
  (``> >(tee ...)``, ``> >(cat ...)``, ``> >(grep ...)``) are NOT matched.

INV-D2 monotonicity
-------------------
The guard is consumed only as a strip-SKIP condition: ``True`` => skip the strip
=> preserve content => MORE detection. Widening it can only *add* detection, so
it cannot introduce a false-negative. The only real risk is over-block (matching
a non-shell consumer or stderr routing), which the negatives below bound.

Counter-test-by-revert (non-vacuity)
------------------------------------
* **Under-block guarantee (positives)** — removal-revert: delete the output-side
  arm (restore input-side-only). Every functional positive carries a *dangerous*
  payload (``gh pr merge 42`` / ``git branch -D x``) so the strip-skip vs
  strip-run difference changes the OBSERVABLE: ``is_dangerous_command`` flips
  True->False (under-block returns) => RED. The guard-unit positives flip
  True->False directly.
* **Over-block-bound guarantees (exclusions)** — a removal-revert leaves an
  exclusion still-``False`` (no flip = vacuous), so each exclusion is proven by
  the DISTINCT *broadening* mutation that would defeat it:
  - drop ``(?<![0-9])`` => ``2>``/``3>`` over-match => the stderr negatives flip
    (guard False->True; functional not-dangerous->dangerous) => RED;
  - widen the target ``(?:bash|sh|zsh)`` => ``\\S+`` => ``tee``/``cat``/``grep``
    over-match => the non-shell-target negatives flip => RED.
  The functional exclusion negatives also carry dangerous payloads so the
  broaden mutation exposes them through ``is_dangerous_command``.

The input-side preservation contract (``bash <(echo 'gh pr merge 42')`` dangerous;
``cat/grep <(...)`` not; the pinned ``> >(tee push.log)`` not-compound case) is
pinned by the existing suite in ``test_merge_guard.py`` (§7.1); this module adds
the new output-side coverage and re-pins the input-side parity that F2 must not
disturb.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from merge_guard_pre import (  # noqa: E402
    _has_process_substitution_to_shell,
    is_dangerous_command,
)


# ---------------------------------------------------------------------------
# Guard-unit tests — directly on _has_process_substitution_to_shell (cheap,
# precise; payload is irrelevant to the guard, which keys on redirect syntax).
# ---------------------------------------------------------------------------

# Output-side positives the widened guard MUST now detect (design §4.5).
_OUTPUT_SIDE_POSITIVES = [
    'echo "x" > >(bash)',          # canonical stdout -> shell
    'echo "x" > >(sh)',            # sh target
    'echo "x" > >(zsh)',           # zsh target
    'echo "x" 1> >(bash)',         # explicit fd-1
    'echo "x" &> >(bash)',         # stdout+stderr
    'echo "x" >> >(bash)',         # append-style
    'echo "x" >  >(bash)',         # extra whitespace between > and >(
    'echo "x" > >( bash )',        # whitespace inside the substitution
]

# Exclusions the guard MUST NOT match. Each is defeated only by a *broadening*
# mutation (named), never by removing the output-side arm — hence the
# counter-test uses the distinct broaden per exclusion, not a removal-revert.
_GUARD_NEGATIVES = [
    'echo "x" 2> >(bash)',                      # stderr fd-2 (drop (?<![0-9]) to defeat)
    'echo "x" 3> >(bash)',                      # fd-3 (drop (?<![0-9]) to defeat)
    "git push origin main > >(tee push.log)",   # pinned not-compound; tee not a shell
    'echo "x" > >(cat)',                        # non-shell target (widen target to defeat)
    'echo "x" > >(grep foo)',                   # non-shell target
    'echo "x" > file.log',                      # plain file redirect, no process-sub
]

# Input-side behavior F2 must leave UNCHANGED (the original guard).
_INPUT_SIDE_POSITIVES = ["bash <(echo x)", "sh <(echo x)", "zsh <(echo x)"]
_INPUT_SIDE_NEGATIVES = ["cat <(echo x)", "grep <(echo x)", "echo x", 'echo "just text"']


@pytest.mark.parametrize("command", _OUTPUT_SIDE_POSITIVES)
def test_guard_detects_output_side_to_shell(command):
    """Output-side stdout-routing redirect into a shell interpreter is detected."""
    assert _has_process_substitution_to_shell(command) is True


@pytest.mark.parametrize("command", _GUARD_NEGATIVES)
def test_guard_excludes_stderr_and_non_shell_targets(command):
    """stderr routing (2>/3>) and non-shell consumers (tee/cat/grep) and plain
    file redirects are NOT matched (over-block bound)."""
    assert _has_process_substitution_to_shell(command) is False


@pytest.mark.parametrize("command", _INPUT_SIDE_POSITIVES)
def test_guard_input_side_unchanged_positive(command):
    """Input-side `bash/sh/zsh <(...)` still detected (F2 is additive)."""
    assert _has_process_substitution_to_shell(command) is True


@pytest.mark.parametrize("command", _INPUT_SIDE_NEGATIVES)
def test_guard_input_side_unchanged_negative(command):
    """Non-shell input-side consumers and plain echo still not matched."""
    assert _has_process_substitution_to_shell(command) is False


# ---------------------------------------------------------------------------
# Functional tests — through is_dangerous_command. Positives and exclusion
# negatives carry a DANGEROUS payload so the strip-skip vs strip-run difference
# is OBSERVABLE (non-vacuous under the counter-test mutations).
# ---------------------------------------------------------------------------

# (command, dangerous-payload) — now-detected output-side under-blocks.
_FUNCTIONAL_POSITIVES = [
    'echo "gh pr merge 42" > >(bash)',
    'echo "gh pr merge 42" > >(sh)',
    'echo "gh pr merge 42" > >(zsh)',
    'echo "gh pr merge 42" 1> >(bash)',
    'echo "gh pr merge 42" &> >(bash)',
    'echo "gh pr merge 42" >> >(bash)',
    'echo "gh pr merge 42" >  >(bash)',
    'echo "gh pr merge 42" > >( bash )',
    "echo 'gh pr merge 42' > >(bash)",          # single-quoted carrier
    'printf "gh pr merge 42" > >(bash)',         # printf carrier
    'echo "git branch -D x" > >(bash)',          # branch-delete payload
]

# Exclusion negatives — dangerous payload routed where it never reaches a shell,
# so it stays stripped and non-dangerous. The broaden mutation named in the
# module docstring makes each flip to dangerous => RED (non-vacuity).
_FUNCTIONAL_NEGATIVES = [
    'echo "gh pr merge 42" 2> >(bash)',          # stderr — not an echo-stdout vector (OQ1)
    'echo "gh pr merge 42" 3> >(bash)',          # fd-3
    'echo "git branch -D x" > >(tee out.log)',   # tee not a shell
    'echo "gh pr merge 42" > >(cat)',            # cat not a shell
    'echo "gh pr merge 42" > >(grep foo)',       # grep not a shell
    'echo "gh pr merge 42" > push.log',          # plain file redirect
]


@pytest.mark.parametrize("command", _FUNCTIONAL_POSITIVES)
def test_output_side_to_shell_is_dangerous(command):
    """A dangerous literal echo/printf'd into an output-side shell process-sub is
    now caught (previously stripped => under-blocked)."""
    assert is_dangerous_command(command) is True


@pytest.mark.parametrize("command", _FUNCTIONAL_NEGATIVES)
def test_output_side_non_shell_and_stderr_stay_stripped(command):
    """A dangerous literal routed to a non-shell consumer or to stderr never
    reaches a shell, so the carrier strips it and it stays non-dangerous."""
    assert is_dangerous_command(command) is False


def test_input_side_functional_parity_unchanged():
    """The input-side carrier-3 preservation contract is undisturbed by F2:
    `bash <(echo '<danger>')` is dangerous; `cat <(...)` is not."""
    assert is_dangerous_command('bash <(echo "gh pr merge 42")') is True
    assert is_dangerous_command('cat <(echo "gh pr merge 42")') is False
