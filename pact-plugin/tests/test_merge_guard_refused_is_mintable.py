"""
Location: pact-plugin/tests/test_merge_guard_refused_is_mintable.py
Summary: The differential property between the pre-hook and the mint: every command
         the pre-hook refuses as ONE guarded operation must be one a single click
         mints a token for, and the pre-hook must then accept that token for that
         exact command. A refused command no click can mint blocks a faithful
         approval for good.
Used by: pytest. The rows are every git/gh string literal in the test suite, shell
         nestings of -c, eval, substitution and heredoc around a guarded command (all
         two-level nestings and a covering set of three-level ones, escaped and
         unescaped), and the review matrices of heredoc, substitution and nested
         eval shapes. The nesting helpers come from
         test_merge_guard_nested_compound.py. Text only: nothing here is executed.
         Guarded verbs are assembled at runtime so this file stays inert to the live
         guard.
"""

import ast
import itertools
from pathlib import Path

from merge_guard_post import _code_spans, _mint_context_from_bundle
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import is_compound_destructive_command, is_dangerous_command
from test_merge_guard_nested_compound import (
    _BT,
    _GUARDED,
    _NESTED,
    _ONE_THROUGH_A_BLANKED_STRING,
    _WRAPPERS,
    _click,
)

_TESTS = Path(__file__).resolve().parent

# Heredoc, substitution and nested-eval shapes from review, with {G} for the guarded
# command.
_REVIEW_TEMPLATES = [
    "python3 - <<EOF\nprint('note: run " + _BT + "echo <({G})" + _BT + " later')\nEOF",
    "python3 - <<EOF\nprint('$({G})')\nEOF",
    'python3 - <<EOF\nprint("$({G})")\nEOF',
    "python3 - <<'EOF'\nprint('$({G})')\nEOF",
    'cat <<E"OF"\n$({G})\nEOF',
    "cat <<E'O'F\n$({G})\nEOF",
    "cat <<EO\\F\n$({G})\nEOF",
    "cat <<-EOF\n\t$({G})\n\tEOF",
    "python3 - <<-EOF\n\tprint('x " + _BT + "{G}" + _BT + "')\n\tEOF",
    "cat << EOF\n$({G})\nEOF",
    "cat <<EOF >/dev/null\n$({G})\nEOF",
    "python3 - <<EOF 2>&1\nprint('$({G})')\nEOF",
    "cat <<EOF; echo done\n$({G})\nEOF",
    "cat <<EOF | wc -l\n$({G})\nEOF",
    "cat <<A <<'B'\n$({G})\nA\n$(echo safe)\nB",
    "cat <<'A' <<B\n$(echo safe)\nA\n$({G})\nB",
    'cat <<<"$({G})"',
    "cat <<< $({G})",
    "x=$(cat <<EOF\n$({G})\nEOF\n); echo \"$x\"",
    "cat <<EOF\n$(( $({G}) + 1 ))\nEOF",
    "cat <<EOF\n$( ({G}) )\nEOF",
    "cat <<EOF\n<({G})\nEOF",
    "bash -c 'cat <<EOF\n$({G})\nEOF'",
    "eval 'cat <<EOF\n$({G})\nEOF'",
    "cat <<EOF\n\"" + _BT + "{G}" + _BT + "\"\nEOF",
    "cat <<EOF\n'$({G})'\nEOF",
    'bash -c "cat <<EOF\n\\$({G})\nEOF"',
    "sh -c 'cat <<EOF\n$({G})\nEOF'",
    "zsh -c 'cat <<EOF\n$({G})\nEOF'",
    "bash <<EOF\n{G}\nEOF",
    "bash <<'EOF'\n{G}\nEOF",
    "cat <<'EOF' | bash\n{G}\nEOF",
    "cat <<EOF\n$(\n{G}\n)\nEOF",
    "cat <<EOF\n" + _BT + "echo $({G})" + _BT + "\nEOF",
    "cat <<EOF\n$(echo hi; {G})\nEOF",
    "cat <<EOF\n$({G} &)\nEOF",
    'cat <<EOF\n$(eval "{G}")\nEOF',
    "cat <<EOF\n$(bash -c '{G}')\nEOF",
    "cat <<EOF\n$(X=1 {G})\nEOF",
    "cat <<EOF\n$(cd /tmp && {G})\nEOF",
    "cat <<EOF\n$(echo $({G}))\nEOF",
    "tee notes.md <<EOF\n$({G})\nEOF",
    "f() { cat <<EOF\n$({G})\nEOF\n}; f",
    "x=$(echo hi; {G})",
    'echo "$(echo hi; {G})"',
    "x=$(cd /tmp && {G})",
    "x=$(\n{G}\n)",
    "cat <<EOF\n$({G}; echo hi)\nEOF",
    "cat <<EOF\n$(echo hi && {G})\nEOF",
    "bash -c 'eval \"echo \\$({G})\"'",
    "bash -c 'eval \"{G}\"'",
    "bash -c 'eval \"echo $({G})\"'",
    "bash -c \"eval '{G}'\"",
    'eval "eval \\"{G}\\""',
    "eval 'eval \"echo \\$({G})\"'",
    "sh -c 'eval \"echo \\$({G})\"'",
    "bash -c 'eval echo \\$\\({G}\\)'",
    "bash -c 'x=$(eval \"{G}\")'",
    # Refused although the shell runs nothing: arithmetic, an uncalled function, a
    # branch that never runs.
    "cat <<EOF\n$(({G}))\nEOF",
    "f() { cat <<EOF\nrun $({G})\nEOF\n}",
    "if false; then cat <<EOF\nrun $({G})\nEOF\nfi",
]


def _prose_nodes(tree):
    """The string nodes of `tree` that are prose, not commands: docstrings, assert
    messages and the messages given to pytest.fail / skip / xfail."""
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            prose.add(id(node.value))
        elif isinstance(node, ast.Assert) and node.msg is not None:
            prose.update(id(n) for n in ast.walk(node.msg))
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("fail", "skip", "xfail"):
            prose.update(id(n) for arg in node.args for n in ast.walk(arg))
    return prose


def _corpus():
    found = set()
    for path in sorted(_TESTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        prose = _prose_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose \
                    and len(node.value) < 2000 and ("git " in node.value or "gh " in node.value):
                found.add(node.value)
    return found


def _nested():
    found = set()
    names = list(_WRAPPERS)
    # Every one- and two-level nesting, and a covering set of three-level ones:
    # each wrapper appears at each level beside every other wrapper at least once.
    chains = [(a,) for a in names] + list(itertools.product(names, repeat=2))
    chains += [(a, b, names[(i + j) % len(names)])
               for i, a in enumerate(names) for j, b in enumerate(names)]
    for core in _GUARDED:
        for chain in chains:
            text = core
            for name in chain:
                text = _WRAPPERS[name](text) if text is not None else None
            if text is not None:
                found.add(text)
    return found


def _rows():
    rows = _corpus() | _nested()
    rows |= {t.replace("{G}", g) for t in _REVIEW_TEMPLATES for g in _GUARDED}
    return sorted(rows)


def test_every_command_refused_as_one_operation_mints_a_token_pre_accepts():
    checked, unmintable = 0, []
    for command in _rows():
        if not is_dangerous_command(command) or is_compound_destructive_command(command):
            continue
        description, result = _click(command)
        # The click must show this one command as its code block.
        if [content.strip() for _raw, content in _code_spans(description)] != [command.strip()]:
            continue
        checked += 1
        if result.context is None or not _token_matches_command({"context": result.context}, command):
            unmintable.append((result.refusal_reason, command))
    assert checked > 1500, checked
    assert not unmintable, f"{len(unmintable)} refused but unmintable: {unmintable[:20]}"


def test_the_exact_text_token_carries_the_operation_pre_reads():
    _description, result = _click(_NESTED)
    assert result.context == {"operation_type": "force-push", "command_text": _NESTED}


def test_a_multi_select_click_on_a_refused_command_mints_nothing():
    questions = [{"question": "Run these?", "header": "Run", "multiSelect": True,
                  "options": [{"label": "Yes, run", "description": f"Run `{_NESTED}` now"},
                              {"label": "No", "description": "Hold."}]}]
    assert _mint_context_from_bundle(questions, {"Run these?": "Yes, run"}).context is None


def test_a_click_showing_two_refused_commands_binds_neither_by_its_text():
    from merge_guard_post import _clicked_refused_command

    other = "bash -c 'eval \"echo \\$(" + _GUARDED[1] + ")\"'"
    questions = [{"question": "Run this?", "header": "Run", "multiSelect": False,
                  "options": [{"label": "Yes, run", "description": f"Run `{_NESTED}` then `{other}`"},
                              {"label": "No", "description": "Hold."}]}]
    assert _clicked_refused_command(questions, {"Run this?": "Yes, run"}) is None
    context = _mint_context_from_bundle(questions, {"Run this?": "Yes, run"}).context
    assert context is None or "command_text" not in context


def test_a_compound_command_is_no_single_refused_command():
    from merge_guard_post import _clicked_refused_command

    both = "bash -c 'eval \"echo \\$(" + _GUARDED[0] + "; " + _GUARDED[1] + ")\"'"
    questions = [{"question": "Run this?", "header": "Run", "multiSelect": False,
                  "options": [{"label": "Yes, run", "description": f"Run `{both}` now"}]}]
    assert is_dangerous_command(both) and is_compound_destructive_command(both)
    assert _clicked_refused_command(questions, {"Run this?": "Yes, run"}) is None
    assert _mint_context_from_bundle(questions, {"Run this?": "Yes, run"}).context is None


def test_a_reading_that_refuses_a_click_is_not_overridden_by_the_exact_text_token():
    # The option chains a second operation outside the code span; the reading refuses it.
    description = f"Run `{_NESTED}` && {_GUARDED[2]}"
    questions = [{"question": "Run this?", "header": "Run", "multiSelect": False,
                  "options": [{"label": "Yes, run", "description": description},
                              {"label": "No", "description": "Hold."}]}]
    result = _mint_context_from_bundle(questions, {"Run this?": "Yes, run"})
    assert result.context is None and result.refusal_reason not in (None, "no_command")


def test_one_command_through_a_blanked_string_runs_on_one_click():
    for command in _ONE_THROUGH_A_BLANKED_STRING:
        _description, result = _click(command)
        assert _token_matches_command({"context": result.context}, command), command
