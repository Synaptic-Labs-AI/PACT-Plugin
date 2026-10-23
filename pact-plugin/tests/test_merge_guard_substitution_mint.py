"""
Location: pact-plugin/tests/test_merge_guard_substitution_mint.py
Summary: A faithful approval of a guarded command whose target is a command
         substitution (`git push --force origin $(git branch --show-current)`)
         mints and runs. The region reading cannot resolve such a target, so the
         mint reads the command's one guarded op and binds the token to the
         command's normalized text; the pre-hook then runs exactly that text.
         Also pins the ruled case of a guarded command held in a variable and
         passed to a script.
Used by: pytest. The real mint function and the real pre-hook matcher run in
         process; a fenced block carries an approval whose command holds backticks.
"""

from __future__ import annotations

import pytest
from merge_guard_post import _exact_text_reading, _mint_context_from_bundle
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import (
    is_compound_destructive_command,
    is_dangerous_command,
    normalize_command_text,
)

FORCE = "git push --" + "force origin "
MERGE = "gh pr " + "merge "
CURRENT = "$(git branch --show-current)"
ROWS = {
    "force-dollar-paren": FORCE + CURRENT,
    "merge-dollar-paren": MERGE + "$(gh pr view --json number -q .number)",
    "force-backtick": FORCE + "`git branch --show-current`",
    "force-dollar-paren-in-bash-c-dq": 'bash -c "' + FORCE + CURRENT + '"',
    "force-dollar-paren-in-bash-c-sq": "bash -c '" + FORCE + CURRENT + "'",
    "merge-held-in-a-variable-for-a-script": "CMD='" + MERGE + "5'; ./retry.sh \"$CMD\"",
}


def _mint(command: str):
    desc = f"Run this:\n```bash\n{command}\n```" if "`" in command else f"Run `{command}` now"
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": desc},
                      {"label": "No", "description": "Hold."}]}]
    return _mint_context_from_bundle(q, {"Run this?": "Yes, run"})


def _runs(token_ctx, command: str) -> bool:
    if not is_dangerous_command(command):
        return True
    if is_compound_destructive_command(command):
        return False
    return token_ctx is not None and _token_matches_command({"context": token_ctx}, command)


class TestAFaithfulClickRuns:
    @pytest.mark.parametrize("command", ROWS.values(), ids=ROWS.keys())
    def test_the_approved_command_is_refused_then_runs(self, command):
        assert not _runs(None, command)
        ctx, _ = _mint(command)
        assert _runs(ctx, command)

    @pytest.mark.parametrize("key", ["force-dollar-paren", "merge-dollar-paren", "force-backtick"])
    def test_a_substituted_target_is_bound_by_its_text(self, key):
        ctx, _ = _mint(ROWS[key])
        assert ctx is not None
        assert ctx["command_text"] == normalize_command_text(ROWS[key])


class TestASubstitutionThatRunsCommandsTypesNothing:
    """The op must stand outside the substitution; a substitution that itself runs
    guarded commands would otherwise type one op for a text that runs several."""

    @pytest.mark.parametrize("command", [
        pytest.param('eval "$(cat <<EOF\n' + MERGE + "5\n" + FORCE + 'main\nEOF\n)"', id="evald-heredoc"),
        pytest.param("bash -c 'eval \"$(printf %s \\\"" + MERGE + "5; " + FORCE + "main\\\")\"'",
                     id="evald-in-bash-c"),
    ])
    def test_two_commands_in_a_substitution_never_type_an_op(self, command):
        assert _exact_text_reading(command, f"`{command}`") is None
        assert _mint(command)[0] is None

    def test_a_substituted_target_still_mints_and_runs(self):
        ctx, _ = _mint(ROWS["force-dollar-paren"])
        assert _runs(ctx, ROWS["force-dollar-paren"])


class TestNothingElseRuns:
    @pytest.mark.parametrize("other", [
        pytest.param(FORCE + "$(git rev-parse --abbrev-ref HEAD)", id="other-substitution"),
        pytest.param(FORCE + "main", id="literal-target"),
        pytest.param(FORCE + CURRENT + " --no-verify", id="extra-flag"),
    ])
    def test_a_token_runs_only_its_own_substitution_text(self, other):
        ctx, _ = _mint(ROWS["force-dollar-paren"])
        assert not _runs(ctx, other)

    def test_a_compound_with_a_substitution_mints_nothing(self):
        command = f"{FORCE}{CURRENT} && {MERGE}5"
        ctx, reason = _mint(command)
        assert (ctx, reason) == (None, "compound_command")
