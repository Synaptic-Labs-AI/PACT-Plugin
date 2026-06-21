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


# ===========================================================================
# Remediation (PR #1003, commit E 98bf4f4e) — F2 output-side COMPLETENESS.
#
# The single output-side arm was replaced by TWO arms + a shared path-qualified
# shell token ``_PROCSUB_SHELL = (?:[^\s)/]*/)*(?:bash|sh|zsh)(?![\w/])``:
#   * Arm A — redirect TARGET; operator set adds the csh ``>&`` (excl. fd-dup
#     ``>&N``) and the clobber ``>|`` (closes the test-engineer `>&`/`>|` finding);
#   * Arm B — procsub as a command ARGUMENT (``| tee >(bash)`` / general fanout),
#     keyed on a preceding NON-redirect token so ``2> >(bash)`` is still excluded.
#   * path prefix ``(?:[^\s)/]*/)*`` accepts ``>(/bin/bash)`` / ``>(./sh)``;
#   * trailing ``(?![\w/])`` anchors the shell name as a whole PATH-LEAF token:
#     KEEPS metachar-separated real vectors ``>(bash;ls)`` / ``>(bash&&x)`` /
#     ``>(bash|cat)`` (bash still executes — empirically confirmed), while
#     DROPPING suffix-of-name ``>(basht)`` / ``>(mysh)`` and ``>(bash/foo)``
#     (bash a directory). `(?![\w/])` was chosen over `\b` (over-blocks bash/foo)
#     and over `(?=[\s)])` (UNDER-blocks the metachar vectors — fix-introduced
#     INV-D2 holes); design §12.1.
#
# Counter-test-by-revert (non-vacuity, ephemeral — see remediation HANDOFF):
#   * positives — removal-revert the relevant arm/piece (e.g. drop the `>&`/`>|`
#     operators, or the path prefix, or Arm B) → the corresponding positive RED;
#   * exclusions — DISTINCT broaden-mutation (a removal-revert can't falsify an
#     exclusion): widen `(?![\w/])`->`\b` re-introduces the `>(bash/foo)`/suffix
#     over-blocks → those negatives flip RED.
# ===========================================================================

# Guard-unit positives (payload irrelevant — the guard keys on redirect/arg syntax).
_REMEDIATION_POSITIVES_GUARD = [
    'echo "x" >& >(bash)',        # csh stdout+stderr synonym of `&>` (Arm A)
    'echo "x" >| >(bash)',        # clobber-override redirect (Arm A)
    'echo "x" > >(/bin/bash)',    # absolute path-qualified target
    'echo "x" > >(./sh)',         # relative path-qualified target
    'echo "x" > >(/usr/bin/zsh)', # abs-path zsh
    'echo "x" | tee >(bash)',     # tee-fanout — procsub as command arg (Arm B)
    'echo "x" | tee -a >(bash)',  # tee -a fanout (Arm B)
    'echo "x" > >(bash;ls)',      # metachar `;` — bash executes before the sep
    'echo "x" > >(bash&&y)',      # metachar `&&`
    'echo "x" > >(bash|cat)',     # metachar `|`
]

# Guard-unit NEW exclusions (over-block bound — must stay False).
_REMEDIATION_NEGATIVES_GUARD = [
    'echo "x" > >(/usr/bin/tee)',  # path-qualified NON-shell target
    'echo "x" > >(teehee)',        # shell name is not a whole leaf (prefix-of-name)
    'echo "x" > >(basht)',         # `bash` + trailing word char (suffix-of-name)
    'echo "x" > >(mysh)',          # `sh` as a name suffix
    'echo "x" > >(bash/foo)',      # `bash` is a DIRECTORY, `foo` the executable
]

# Functional positives — DANGEROUS payload so the strip-skip is OBSERVABLE
# through is_dangerous_command (anti-phantom-green).
_REMEDIATION_FUNCTIONAL_POSITIVES = [
    'echo "gh pr merge 42" >& >(bash)',
    'echo "gh pr merge 42" >| >(bash)',
    'echo "gh pr merge 42" > >(/bin/bash)',
    'echo "gh pr merge 42" > >(./sh)',
    'echo "gh pr merge 42" | tee >(bash)',
    'echo "gh pr merge 42" | tee -a >(bash)',
    'echo "gh pr merge 42" > >(bash;ls)',
    'echo "gh pr merge 42" > >(bash&&y)',
    'echo "gh pr merge 42" > >(bash|cat)',
    "echo 'git branch -D real' > >(/bin/bash)",  # branch-delete payload, path-qualified
]

# Functional NEW exclusions — dangerous payload routed where it never reaches a
# shell (non-shell target / non-leaf shell name), so it stays stripped/benign.
_REMEDIATION_FUNCTIONAL_NEGATIVES = [
    'echo "gh pr merge 42" > >(/usr/bin/tee)',
    'echo "gh pr merge 42" > >(teehee)',
    'echo "gh pr merge 42" > >(basht)',
    'echo "gh pr merge 42" > >(mysh)',
    'echo "gh pr merge 42" > >(bash/foo)',
]


@pytest.mark.parametrize("command", _REMEDIATION_POSITIVES_GUARD)
def test_remediation_guard_detects_new_output_side_forms(command):
    """`>&`/`>|`/path-qualified/tee-fanout/metachar-separated procsub-to-shell
    forms are now detected by the two-arm guard."""
    assert _has_process_substitution_to_shell(command) is True


@pytest.mark.parametrize("command", _REMEDIATION_NEGATIVES_GUARD)
def test_remediation_guard_excludes_nonleaf_and_nonshell(command):
    """Path-qualified non-shell, prefix/suffix-of-name, and path-dir targets are
    NOT matched (the `(?![\\w/])` path-leaf over-block bound)."""
    assert _has_process_substitution_to_shell(command) is False


@pytest.mark.parametrize("command", _REMEDIATION_FUNCTIONAL_POSITIVES)
def test_remediation_new_forms_are_dangerous(command):
    """A dangerous literal echo'd into a newly-covered output-side shell
    process-sub is now caught (previously under-blocked)."""
    assert is_dangerous_command(command) is True


@pytest.mark.parametrize("command", _REMEDIATION_FUNCTIONAL_NEGATIVES)
def test_remediation_new_exclusions_stay_stripped(command):
    """A dangerous literal routed to a non-shell or non-leaf-shell target never
    reaches a shell, so the carrier strips it and it stays non-dangerous."""
    assert is_dangerous_command(command) is False


def test_carrier7_gh_create_output_side_now_over_blocks():
    """DOCUMENTING test (review f-1): with the output-side guard widened, the
    carrier-7 gh-create form ``gh issue create --title "<danger>" > >(bash)`` now
    OVER-blocks — the procsub makes the guard skip the strip, so the title literal
    is scanned and matched. This is INV-D2-ACCEPTABLE (over-block, never an
    under-block) and monotonic-safe. In reality the danger does NOT reach bash
    (gh's stdout is the issue URL, not the title), so this is a benign-but-
    suspicious form being conservatively flagged. Pinned so the over-block is
    intentional and visible, not a future surprise."""
    cmd = 'gh issue create --title "gh pr merge 42" > >(bash)'
    assert is_dangerous_command(cmd) is True


# ===========================================================================
# Carrier-gating completeness (PR #1003, commit 5623c938) — security #41
# MEDIUM-1. F2's output-side detection must be wired into EVERY stdout-producing
# content-stripping carrier, not just echo (#3) and gh-creation (#7). Carriers
# #1 (heredoc), #5 (commit-msg), #6 (here-string) are now gated on
# `not piped_to_shell and not process_sub_to_shell` (the two flags are hoisted
# once to the top of _strip_non_executable_content and compose with each
# carrier's existing input-side/cmd-subst guards). A dangerous literal carried by
# #1/#5/#6 and routed to a shell via OUTPUT-side procsub (`> >(bash)`) OR a pipe
# (`| bash`) is now PRESERVED (strip skipped) → detected, where it was previously
# stripped → under-blocked. Empirically confirmed the routing is real: a benign
# `cat <<< "echo PWN" > >(bash)` and `cat <<EOF ... echo PWN ... EOF > >(bash)`
# both EXECUTE in live bash (the carrier's stdout reaches the shell). Carriers #2
# (comments — bash no-op) and #4 (var-assign — emits no stdout) are deliberately
# NOT gated and stay benign.
#
# The gate keys on BOTH flags, so each carrier is covered with a procsub form AND
# a pipe form. Counter-test-by-revert (per-carrier non-vacuity — see HANDOFF):
# ungating ONE carrier (its `if not piped... and not procsub:` → `if True:`,
# always-strip = the pre-fix bug) flips ONLY that carrier's positives RED, proving
# each carrier's gate is INDEPENDENTLY load-bearing (not "some shared gate catches
# it").
# ===========================================================================

# Heredoc witnesses are multi-line (marker / body / closing) with the dangerous
# literal in the BODY and the shell routing on the `cat` line.
_HEREDOC_PROCSUB = 'cat <<EOF > >(bash)\n; gh pr merge 42\nEOF'
_HEREDOC_PIPE = 'cat <<EOF | bash\n; gh pr merge 42\nEOF'
_HEREDOC_BARE = 'cat <<EOF\n; gh pr merge 42\nEOF'

# Positives — dangerous payload routed to a shell; the gate preserves it for the
# scan. Leading `;` chains the op after whatever prefix the carrier prepends to
# stdout (e.g. git's commit-summary line). DANGEROUS payload = anti-phantom-green.
_CARRIER_GATING_POSITIVES = [
    'git commit -m "; gh pr merge 42" > >(bash)',   # #5 commit-msg, output-side procsub
    'git commit -m "; gh pr merge 42" | bash',      # #5 commit-msg, pipe-to-shell
    'cat <<< "; gh pr merge 42" > >(bash)',         # #6 here-string, procsub
    'cat <<< "; gh pr merge 42" | bash',            # #6 here-string, pipe
    _HEREDOC_PROCSUB,                                # #1 heredoc, procsub
    _HEREDOC_PIPE,                                   # #1 heredoc, pipe
]

# Negatives — no-regression (bare carriers still stripped), over-block control
# (benign payload → no spurious block even with procsub), and the two UNGATED
# carriers (#2 comment, #4 var-assign) staying benign.
_CARRIER_GATING_NEGATIVES = [
    'git commit -m "; gh pr merge 42"',          # #5 bare — stripped as before (no regression)
    'cat <<< "; gh pr merge 42"',                # #6 bare — stripped
    _HEREDOC_BARE,                               # #1 bare — stripped
    'git commit -m "safe message" > >(bash)',    # over-block control — benign payload, no block
    'X="; gh pr merge 42" > >(bash)',            # #4 var-assign UNGATED — emits no stdout → benign
    'echo "hi" > >(bash)  # ; gh pr merge 42',   # #2 comment UNGATED — bash no-op → benign
]


@pytest.mark.parametrize("command", _CARRIER_GATING_POSITIVES)
def test_gated_carrier_routed_to_shell_is_dangerous(command):
    """A dangerous literal carried by heredoc/commit-msg/here-string and routed to
    a shell (output-side procsub OR pipe) is now PRESERVED by the carrier gate and
    detected (previously stripped → under-blocked; security #41 MEDIUM-1)."""
    assert is_dangerous_command(command) is True


@pytest.mark.parametrize("command", _CARRIER_GATING_NEGATIVES)
def test_gated_carrier_no_regression_and_ungated_stay_benign(command):
    """No-regression: the same carriers WITHOUT shell-routing still strip → benign.
    Over-block control: a benign commit message with procsub is not spuriously
    flagged. Ungated carriers #2 (comment) and #4 (var-assign) stay benign because
    a comment is a bash no-op and a var-assign emits no stdout."""
    assert is_dangerous_command(command) is False


# ===========================================================================
# MINOR form-pins (PR #1003, review m-1 + m-2) — append-fd + tab output-side
# forms. Each was EMPIRICALLY classified in real bash (does the payload reach a
# shell = a genuine vector?) BEFORE pinning, so a non-vector is never asserted as
# a false positive:
#
#   GENUINE VECTORS (bash routes the payload to the shell — `echo PWN` executes):
#     `1>> >(bash)`, `&>> >(bash)` (append-fd, space), `>\t>(bash)` (tab between
#     `>` and `>(`). Pinned below with a dangerous payload.
#
#   NON-VECTORS — bash SYNTAX ERRORS (NOT pinned): the NO-SPACE forms
#     `>>(bash)`, `1>>(bash)`, `&>>(bash)` raise `bash: syntax error near
#     unexpected token '('` (rc=2) — the `>>`/`1>>`/`&>>` append operator requires
#     a filename, and `(bash)` (a subshell) is not a valid redirect target, so the
#     command never runs. The merge-guard regex over-detects them (is_dangerous
#     True, because `>>?\s*>\(` matches `>>(` as `>`+`>(`), which is INV-D2-
#     ACCEPTABLE (a conservative over-block of a non-executable form) but NOT a
#     required behavior — so they are deliberately NOT pinned as vectors.
#
# Non-vacuity (counter-test-by-revert, ephemeral — see HANDOFF):
#   * tab form — CLEAN isolated counter-test: tightening Arm A's first `\s*`
#     (operator->`>(`) to a literal space ` *` flips ONLY the tab positive RED
#     (proves the `\s*` tab-handling is load-bearing).
#   * append forms (`1>> >(bash)`/`&>> >(bash)`) — OVER-DETERMINED: they are caught
#     by the GENERIC embedded `> >(bash)` shape (the bare `(?<![0-9])>>?` op + the
#     procsub), NOT uniquely by the `1>>?`/`&>>?` operators — EMPIRICALLY, dropping
#     `1>>?`/`&>>?` flips NOTHING. So there is no per-operator counter-test for
#     them; their non-vacuity is the shared whole-Arm-A removal (which flips every
#     output-side redirect positive, incl. the basic `> >(bash)`).
# ===========================================================================

# Genuine-vector output-side form variants — dangerous payload (anti-phantom-green).
_MINOR_FORM_POSITIVES = [
    'echo "; gh pr merge 42" 1>> >(bash)',   # m-1 append fd-1 (space)
    'echo "; gh pr merge 42" &>> >(bash)',   # m-1 append stdout+stderr (space)
    'echo "; gh pr merge 42" >\t>(bash)',    # m-2 tab whitespace between > and >(
]


@pytest.mark.parametrize("command", _MINOR_FORM_POSITIVES)
def test_append_fd_and_tab_output_side_forms_are_dangerous(command):
    """Append-fd (`1>>`/`&>>`) and tab-whitespace output-side procsub-to-shell
    forms are genuine vectors (empirically: the payload reaches bash and executes)
    and are detected. The no-space `>>(bash)`/`1>>(bash)`/`&>>(bash)` forms are
    bash SYNTAX ERRORS (non-vectors) and are deliberately NOT pinned here."""
    assert is_dangerous_command(command) is True
