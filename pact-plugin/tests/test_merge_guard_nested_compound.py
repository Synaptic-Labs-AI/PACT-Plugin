"""
Location: pact-plugin/tests/test_merge_guard_nested_compound.py
Summary: The compound count through nested shell strings: two guarded commands, one of
         them inside a -c or eval string (quoted either way, with escaped or live
         substitutions and heredocs), are two operations, which one click never
         runs; one guarded command reached through such a string is one.
Used by: pytest; test_merge_guard_refused_is_mintable.py reuses its nesting helpers.
         Text only: nothing here is executed. Guarded verbs are assembled at runtime
         so this file stays inert to the live guard.
"""

import itertools

from merge_guard_post import _mint_context_from_bundle
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import is_compound_destructive_command, is_dangerous_command

_BT = "`"
_GUARDED = [
    "git push " + "--force origin main",
    "gh pr " + "merge 5 --squash",
    "gh pr " + "close 5 --delete-branch",
    "git branch " + "-D feat",
]


def _sq(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _dq(s):
    return '"' + "".join("\\" + ch if ch in '\\$`"' else ch for ch in s) + '"'


def _dq_raw(s):
    return '"' + "".join("\\" + ch if ch in '\\"' else ch for ch in s) + '"'


_WRAPPERS = {
    "bash -c '…'": lambda s: "bash -c " + _sq(s),
    'sh -c "…" escaped': lambda s: "sh -c " + _dq(s),
    'bash -c "…" raw': lambda s: "bash -c " + _dq_raw(s),
    "eval '…'": lambda s: "eval " + _sq(s),
    'eval "…" escaped': lambda s: "eval " + _dq(s),
    'echo "$(…)"': lambda s: 'echo "$(' + s + ')"',
    "x=$(echo hi; …)": lambda s: "x=$(echo hi; " + s + ")",
    "heredoc span": lambda s: "cat <<EOF\n$(" + s + ")\nEOF",
    "x=`…`": lambda s: None if _BT in s else "x=" + _BT + s + _BT,
}

_NESTED = "bash -c 'eval \"echo \\$(" + _GUARDED[0] + ")\"'"


def _click(command):
    """The faithful click on an option that shows `command` in a fenced block."""
    description = f"Run this:\n```bash\n{command}\n```"
    questions = [{"question": "Run this?", "header": "Run", "multiSelect": False,
                  "options": [{"label": "Yes, run", "description": description},
                              {"label": "No", "description": "Hold."}]}]
    return description, _mint_context_from_bundle(questions, {"Run this?": "Yes, run"})


def _two_operations():
    """Two guarded commands joined inside one nesting, or one nested beside a plain one,
    for every wrapper at one and two levels."""
    first, second = _GUARDED[0], _GUARDED[2]
    rows = []
    heredoc = _WRAPPERS["heredoc span"]
    for outer, inner in itertools.product([lambda s: s, *_WRAPPERS.values()], _WRAPPERS.values()):
        if outer is heredoc and inner is heredoc:
            continue  # the same delimiter twice: bash and zsh end the heredocs differently
        nested = inner(first)
        for text in (inner(first + "; " + second), nested and nested + " && " + second,
                     nested and second + " && " + nested):
            text = text and outer(text)
            if text:
                rows.append(text)
    return rows


def test_two_guarded_operations_at_any_depth_stay_compound_and_never_mint():
    wrong = []
    for command in _two_operations():
        if not is_compound_destructive_command(command):
            wrong.append(("not compound", command))
            continue
        _description, result = _click(command)
        if result.context is not None and _token_matches_command({"context": result.context}, command):
            wrong.append(("runs on one click", command))
    assert not wrong, f"{len(wrong)} two-operation commands: {wrong[:12]}"


# One guarded command reached through a -c or eval string the strip blanks, where the
# substitution that runs it belongs to the calling shell or encloses the string.
_ONE_THROUGH_A_BLANKED_STRING = [
    'bash -c "echo \\"$(eval \'' + _GUARDED[0] + '\')\\""',
    'bash -c "echo \\"$(bash -c \'' + _GUARDED[3] + '\')\\""',
    'x=$(echo hi; eval "echo \\"\\$(' + _GUARDED[1] + ')\\"")',
    'x=$(echo hi; eval "echo \\"\\$(' + _GUARDED[0] + ')\\"")',
]

# Two guarded commands, one of them two levels down in a quoted -c or eval string.
_TWO_THROUGH_A_BLANKED_STRING = [
    _NESTED + " && " + _GUARDED[2],
    _GUARDED[2] + " && " + _NESTED,
    "eval 'eval \"echo \\$(" + _GUARDED[0] + ")\"' && " + _GUARDED[2],
    'sh -c "cat <<EOF\n\\$(' + _GUARDED[0] + ")\nEOF && " + _GUARDED[2] + '"',
]


def test_one_command_through_a_blanked_string_counts_once():
    for command in _ONE_THROUGH_A_BLANKED_STRING:
        assert is_dangerous_command(command), command
        assert not is_compound_destructive_command(command), command


def test_two_commands_through_a_blanked_string_stay_compound():
    for command in _TWO_THROUGH_A_BLANKED_STRING:
        assert is_compound_destructive_command(command), command
        _description, result = _click(command)
        assert result.context is None or not _token_matches_command({"context": result.context}, command)
