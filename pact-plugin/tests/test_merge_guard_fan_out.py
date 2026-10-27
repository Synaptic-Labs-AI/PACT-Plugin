"""
Location: pact-plugin/tests/test_merge_guard_fan_out.py
Summary: One approval runs one operation, so a command whose guarded op the shell can
         run more than once counts as more than one op: find -exec / -execdir / -ok /
         -okdir, xargs, while / until loops, a for loop over a list that is not plain
         literal words or over several words with nothing ending it early, a guarded
         op as a plain while condition, and a shell
         function holding a guarded op called more than once or from such a loop. Such a
         command is refused and no click mints for it. A single run still mints and runs,
         and so does a retry: an op whose own success ends the loop (`op && break`,
         `if op; then break`, `until op`, `while ! op`) or that an unconditional break
         follows succeeds at most once. GNU parallel and watch repeat what they run, and
         an awk program runs a system() call once per input record unless the call sits
         only in a BEGIN or END block.
Used by: pytest. Text only, except the run-count oracle, which runs each row in bash in
         an empty temp dir with every guarded command swapped for an append to a log
         file. Guarded verbs are assembled at runtime so this file stays inert to the
         live guard.
"""

import os
import re
import subprocess

import pytest
from merge_guard_post import _mint_context_from_bundle
from merge_guard_pre import _token_matches_command
from shared.merge_guard_common import is_compound_destructive_command, is_dangerous_command

_FP = "git push " + "--force origin main"
_MG = "gh pr " + "merge"
_FP_TO = "git push " + "--force origin"

# (label, command, how many times bash runs the guarded op; None when it prompts)
FAN_OUT = [
    ("find -exec", "find . -name '*.txt' -exec " + _FP + " \\;", 2),
    ("find -execdir", "find . -name '*.txt' -execdir " + _FP + " \\;", 2),
    ("find -ok", "find . -name '*.txt' -ok " + _FP + " \\;", None),
    ("find -okdir", "find . -name '*.txt' -okdir " + _FP + " \\;", None),
    ("xargs -I", "printf '%s\\n' 5 6 | xargs -I{} " + _MG + " {} --squash", 2),
    ("xargs -n1", "printf '5 6' | xargs -n1 " + _MG + " --squash", 2),
    ("xargs -L1", "printf '5\\n6\\n' | xargs -L1 " + _MG + " --squash", 2),
    ("xargs -P", "printf '5\\n6\\n' | xargs -P 2 -I{} " + _MG + " {} --squash", 2),
    ("parallel over arguments", "parallel " + _MG + " {} --squash ::: 5 6", None),
    ("parallel from a pipe", "printf '5\\n6\\n' | parallel " + _MG + " {} --squash", None),
    ("watch", "watch -n 1 " + _FP, None),
    ("awk per line, merge", "printf '5\\n6\\n' | awk '{system(\"" + _MG + " \" $1 \" --squash\")}'", 2),
    ("awk per line, push", "printf 'a\\nb\\n' | awk '{system(\"" + _FP + "\")}'", 2),
    ("xargs from a file", "xargs -I{} " + _MG + " {} --squash < prs.txt", None),
    ("while read", "seq 2 | while read i; do " + _FP + "; done", 2),
    ("while read with an unrelated break", "seq 3 | while read i; do " + _FP + "; [ $i = 2 ] && break; done", 2),
    ("guarded op as a while condition", "while " + _FP + "; do sleep 1; done", None),
    ("guarded op in a bounded while condition", "i=0; while [ $i -lt 2 ] && " + _FP + "; do i=$((i+1)); done", 2),
    ("until", "i=0; until [ $i -ge 2 ]; do " + _FP + "; i=$((i+1)); done", 2),
    ("until with the op in the body", "until false; do " + _FP + "; done", None),
    ("for over $(...)", "for i in $(seq 2); do " + _FP + "; done", 2),
    ("for over $VAR", "L='a b'; for i in $L; do " + _FP + "; done", 2),
    ("for over a glob", "for f in *.txt; do " + _FP + "; done", 2),
    ("for over literal words", "for i in 1 2; do " + _FP + "; done", 2),
    ("for over literal words, read", "for p in 5 6; do " + _MG + " $p --squash; done", 2),
    ("C-style for", "for ((i = 0; i < 2; i++)); do " + _FP + "; done", 2),
    ("function called twice", "f() { " + _FP + "; }; f; f", 2),
    ("function with $1 called twice", "f() { " + _MG + " $1 --squash; }; f 5; f 6", 2),
    ("function called in a for loop", "f() { " + _FP + "; }; for i in 1 2; do f; done", 2),
    ("function called in a while loop", "f() { " + _FP + "; }; seq 2 | while read i; do f; done", 2),
    ("sh -c in a two-pass loop", "for i in 1 2; do sh -c '" + _MG + " 5 --squash'; done", 2),
    ("find -execdir +, once per directory", "find . -name '*.txt' -execdir sh -c '" + _FP + "' _ {} +", None),
    ("find on two start paths at depth 0", "find a.txt b.txt -maxdepth 0 -exec " + _FP + " \\;", 2),
    ("xargs from a two-word here-string", "xargs -n1 " + _MG + " --squash <<< '5 6'", 2),
    ("xargs from a here-string of a variable", "xargs -n1 " + _MG + " --squash <<< \"$PRS\"", None),
    ("head -2 into xargs", "printf '5\\n6\\n' | head -2 | xargs -I{} " + _MG + " {} --squash", 2),
    ("a loop in a substitution", "x=$(for i in 1 2; do " + _FP + "; done)", 2),
    ("time before a loop", "time for i in 1 2; do " + _FP + "; done", 2),
    ("time -p before a loop", "time -p for i in 1 2; do " + _FP + "; done", 2),
    ("! before a loop", "! for i in 1 2; do " + _FP + "; done", 2),
    ("a function that calls itself", "f() { " + _FP + "; [ -n \"$1\" ] && f; }; f go", 2),
    ("a trap on EXIT plus a call", "f() { " + _FP + "; }; trap f EXIT; f", 2),
    ("head -c 4 into xargs", "printf '5\\n6\\n' | head -c 4 | xargs -I{} " + _MG + " {} --squash", 2),
    ("tail -2 into xargs", "printf '5\\n6\\n' | tail -2 | xargs -I{} " + _MG + " {} --squash", 2),
    ("sed -n 1,2p into xargs", "printf '5\\n6\\n' | sed -n 1,2p | xargs -I{} " + _MG + " {} --squash", 2),
    ("grep -m2 into xargs", "printf '5\\n6\\n' | grep -m2 . | xargs -I{} " + _MG + " {} --squash", 2),
    ("grep -m1 over two files into xargs", "grep -m1 . a.txt b.txt | xargs -I{} " + _MG + " {} --squash", None),
    ("jq of every element into xargs", "printf '[5,6]' | jq -r '.[]' | xargs -I{} " + _MG + " {} --squash", None),
    ("xargs from a file", "xargs -I{} " + _MG + " {} --squash < one.txt", None),
    ("cat of a file into xargs", "cat one.txt | xargs -I{} " + _MG + " {} --squash", None),
    ("rev-parse of two revs into xargs", "git rev-parse --abbrev-ref HEAD main | xargs -I{} " + _FP_TO + " {}", None),
    ("gh pr list into xargs", "gh pr list --json number -q '.[].number' | xargs -I{} " + _MG + " {} --squash", None),
    # The text does not bound these to one run, though this oracle run is one:
    ("a status test after another command", "for i in 1 2; do " + _FP + "; echo hi; [ $? -eq 0 ] && break; done", 1),
    ("a trap on INT plus a call", "f() { " + _FP + "; }; trap f INT; f", 1),
    ("in a -c string", "bash -c 'seq 2 | while read i; do " + _FP + "; done'", 2),
    ("in a substitution", "x=$(seq 2 | xargs -I{} " + _MG + " {} --squash)", 2),
]

SINGLE_RUN = [
    ("plain", _FP, 1),
    ("function called once", "f() { " + _FP + "; }; f", 1),
    ("function keyword called once", "function f { " + _FP + "; }; f", 1),
    ("for over one literal word", "for p in 5; do " + _MG + " $p --squash; done", 1),
    ("for retry with break", "for i in 1 2 3; do " + _FP + " && break; done", 1),
    ("for retry with if", "for i in 1 2 3; do if " + _FP + "; then break; fi; done", 1),
    ("until retry", "until " + _FP + "; do sleep 1; done", 1),
    ("while-not retry", "while ! " + _FP + "; do sleep 1; done", 1),
    ("while true with break", "while true; do " + _FP + "; break; done", 1),
    ("while read with break after the op", "seq 3 | while read i; do " + _FP + " && break; done", 1),
    ("for over a list with a success exit", "for p in $(seq 3); do " + _MG + " 5 --squash && break; done", 1),
    ("status test then break", "for i in 1 2 3; do " + _FP + "; [ $? -eq 0 ] && break; done", 1),
    ("test $? then break", "for i in 1 2 3; do " + _FP + "; test $? = 0 && break; done", 1),
    ("(( $? )) then break", "for i in 1 2 3; do " + _FP + "; (( $? == 0 )) && break; done", 1),
    ("if on the status then break", "for i in 1 2 3; do " + _FP + "; if [ $? -eq 0 ]; then break; fi; done", 1),
    ("status test in a while", "while true; do " + _FP + "; [ $? -eq 0 ] && break; done", 1),
    ("find -exec +", "find . -name '*.txt' -exec sh -c '" + _FP + "' _ {} +", 1),
    ("find -exec + running the command itself", "find . -name '*.txt' -exec " + _FP + " {} +", 1),
    ("find . at depth 0", "find . -maxdepth 0 -exec " + _FP + " \\;", 1),
    ("xargs from a one-word here-string", "xargs " + _MG + " --squash <<< 5", 1),
    ("xargs --repo from a one-word here-string", "xargs " + _MG + " --squash --repo o/b <<< 5", 1),
    ("xargs -n1 from a quoted one-word here-string", "xargs -n1 " + _MG + " --squash <<< \"5\"", 1),
    ("xargs from a here-string in bash -c", "bash -c 'xargs " + _MG + " --squash <<< 5'", 1),
    ("head -1 into xargs", "printf '5\\n6\\n' | head -1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("head -n 1 into xargs", "printf '5\\n6\\n' | head -n 1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("head -n1 into xargs", "printf '5\\n6\\n' | head -n1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("head -c 1 into xargs", "printf '5\\n6\\n' | head -c 1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("tail -1 into xargs", "printf '5\\n6\\n' | tail -1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("tail -n 1 into xargs", "printf '5\\n6\\n' | tail -n 1 | xargs -I{} " + _MG + " {} --squash", 1),
    ("sed -n 1p into xargs", "printf '5\\n6\\n' | sed -n 1p | xargs -I{} " + _MG + " {} --squash", 1),
    ("sed 1q into xargs", "printf '5\\n6\\n' | sed 1q | xargs -I{} " + _MG + " {} --squash", 1),
    ("awk NR==1 into xargs", "printf '5\\n6\\n' | awk 'NR==1' | xargs -I{} " + _MG + " {} --squash", 1),
    ("grep -m1 into xargs", "printf '5\\n6\\n' | grep -m1 . | xargs -I{} " + _MG + " {} --squash", 1),
    ("grep -m 1 into xargs", "printf '5\\n6\\n' | grep -m 1 . | xargs -I{} " + _MG + " {} --squash", 1),
    ("jq of one index into xargs", "printf '[5,6]' | jq -r '.[0]' | xargs -I{} " + _MG + " {} --squash", None),
    ("the current branch into xargs", "git rev-parse --abbrev-ref HEAD | xargs -I{} " + _FP_TO + " {}", None),
    ("branch --show-current into xargs", "git branch --show-current | xargs -I{} " + _FP_TO + " {}", None),
    ("the current PR number into xargs", "gh pr view --json number -q .number | xargs -I{} " + _MG + " {} --squash", None),
    ("a PR's number into xargs", "gh pr view 5 --json number -q .number | xargs -I{} " + _MG + " {} --squash", None),
    ("a trap on EXIT alone", "f() { " + _FP + "; }; trap f EXIT", 1),
    ("xargs fed one word by printf", "printf '%s\\n' 5 | xargs -I{} " + _MG + " {} --squash", 1),
    ("awk BEGIN only", "awk 'BEGIN{system(\"" + _FP + "\")}'", 1),
    ("awk END only", "printf 'a\\nb\\n' | awk 'END{system(\"" + _FP + "\")}'", 1),
    ("xargs fed one word by echo", "echo 5 | xargs -I{} " + _MG + " {} --squash", 1),
]

STYLES = {
    "inline": lambda c: f"Run `{c}` now",
    "double-backtick": lambda c: f"Run `` {c} `` now",
    "fenced": lambda c: f"Run this:\n```bash\n{c}\n```",
}


def _runs_after_one_click(command, style):
    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": style(command)},
                      {"label": "No", "description": "Hold."}]}]
    ctx = _mint_context_from_bundle(q, {"Run this?": "Yes, run"}).context
    return ctx is not None and _token_matches_command({"context": ctx}, command)


@pytest.mark.parametrize("style", STYLES.values(), ids=STYLES.keys())
@pytest.mark.parametrize("label,command,runs", FAN_OUT, ids=[r[0] for r in FAN_OUT])
def test_a_fan_out_carrier_is_refused_and_no_click_runs_it(label, command, runs, style):
    assert is_dangerous_command(command) is True
    assert is_compound_destructive_command(command) is True
    assert not _runs_after_one_click(command, style)


@pytest.mark.parametrize("style", STYLES.values(), ids=STYLES.keys())
@pytest.mark.parametrize("label,command,runs", SINGLE_RUN, ids=[r[0] for r in SINGLE_RUN])
def test_a_single_run_is_refused_then_runs_on_one_click(label, command, runs, style):
    assert is_dangerous_command(command) is True
    assert is_compound_destructive_command(command) is False
    assert _runs_after_one_click(command, style)


@pytest.mark.parametrize("label,command,runs", [r for r in FAN_OUT + SINGLE_RUN if r[2] is not None],
                         ids=[r[0] for r in FAN_OUT + SINGLE_RUN if r[2] is not None])
def test_the_rows_match_how_often_bash_runs_the_op(label, command, runs, tmp_path):
    """Ground truth: bash runs each row in an empty dir holding two *.txt files, with
    every guarded command swapped for an append to a log. Nothing guarded runs."""
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("")
    log = tmp_path / "LOG"
    mark = "printf x >> " + str(log)
    harmless = command.replace('"' + _MG + ' " $1 " --squash"', '"' + mark + '"').replace(_FP, mark)
    harmless = re.sub(re.escape(_MG) + r"(\s+(\$\w+|\{\}|\d+))?\s+--squash", mark, harmless)
    harmless = harmless.replace(_MG + " --squash", mark)
    assert "git push" not in harmless and "gh pr" not in harmless
    subprocess.run(["bash", "-c", harmless], cwd=tmp_path, capture_output=True, timeout=20, input="",
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert (os.path.getsize(log) if log.exists() else 0) == runs


def test_watch_does_not_ride_a_token_for_the_command_it_repeats(tmp_path):
    from merge_guard_post import write_token
    from merge_guard_pre import check_merge_authorization

    q = [{"question": "Run this?", "header": "Run", "multiSelect": False,
          "options": [{"label": "Yes, run", "description": f"Run `{_FP}` now"},
                      {"label": "No", "description": "Hold."}]}]
    ctx = _mint_context_from_bundle(q, {"Run this?": "Yes, run"}).context
    assert ctx is not None and _token_matches_command({"context": ctx}, _FP)
    assert write_token(dict(ctx), token_dir=tmp_path)
    assert check_merge_authorization("watch -n 1 " + _FP, tmp_path) is not None
    assert check_merge_authorization(_FP, tmp_path) is None
