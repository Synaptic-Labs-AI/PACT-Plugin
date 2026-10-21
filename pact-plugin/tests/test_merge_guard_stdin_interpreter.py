"""Interpreter code fed on stdin: refused when it runs a guarded command directly.

An interpreter given no code argument reads its program from stdin: a heredoc
(`python3 - <<'EOF'`), a here-string (`python3 <<< '…'`), or a pipe into it
(`echo '…' | python3`, `cat <<'EOF' | python3`). That program is refused when an
exec-primitive call's FIRST argument is a literal the danger scan reads as a guarded
command: a string, or a list of strings joined as argv. A Tcl `exec` line and a
backtick that runs a shell (ruby, perl, php, and `$`-tagged JS templates) count the
same way. Anything else is data: guarded text in a variable, in a later argument, in
a test-case list, or in a string the code only prints. A command passed through a
variable needs dataflow to follow, and is a named residual.
"""


import pytest
import shared.merge_guard_common as mgc
from shared.merge_guard_common import is_dangerous_command

D = "gh pr " + "merge 5"  # assembled so this file carries no guarded literal
PY_RUN = 'import os; os.system("{d}")'

FEEDS = [
    # (label, executing, inert)
    ("pipe echo|python3", f"echo '{PY_RUN.format(d=D)}' | python3", f"echo 'print(\"{D}\")' | python3"),
    ("pipe printf|perl", f"printf '%s' 'system(\"{D}\")' | perl", f"printf '%s' 'print \"{D}\"' | perl"),
    ("herestring python3", f"python3 <<< '{PY_RUN.format(d=D)}'", f"python3 <<< 'print(\"{D}\")'"),
    ("heredoc python3 -", f"python3 - <<'EOF'\nimport subprocess\nsubprocess.run('{D}', shell=True)\nEOF",
     f"python3 - <<'EOF'\nprint('{D}')\nEOF"),
    ("heredoc python3", f"python3 <<'EOF'\n{PY_RUN.format(d=D)}\nEOF", f"python3 <<'EOF'\nprint(\"{D}\")\nEOF"),
    ("heredoc list form", "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['gh', 'pr', 'merge', '5'])\nEOF",
     "python3 - <<'EOF'\nprint(['gh', 'pr', 'merge', '5'])\nEOF"),
    ("cat heredoc|python3", f"cat <<'EOF' | python3\n{PY_RUN.format(d=D)}\nEOF", f"cat <<'EOF' | python3\nprint(\"{D}\")\nEOF"),
    ("heredoc ruby backtick", f"ruby <<'EOF'\nputs `{D}`\nEOF", f"ruby <<'EOF'\nputs \"{D}\"\nEOF"),
    ("heredoc node", f"node <<'EOF'\nrequire('child_process').execSync('{D}')\nEOF",
     f"node <<'EOF'\nconsole.log(`{D}`)\nEOF"),
    ("tclsh pipe", f"echo 'exec sh -c \"{D}\"' | tclsh", f"echo 'puts \"{D}\"' | tclsh"),
    ("tclsh herestring", f"tclsh <<< 'exec sh -c \"{D}\"'", f"tclsh <<< 'puts \"{D}\"'"),
    ("wish heredoc", f"wish <<'EOF'\nexec sh -c \"{D}\"\nEOF", f"wish <<'EOF'\nputs \"{D}\"\nEOF"),
]

# Guarded text that the fed program holds only as data must stay allowed.
DATA_ROWS = [
    ("git commit -F - heredoc", f"git commit -F - <<'EOF'\nfix: stop refusing {D} text\n\nThe guard no longer blocks '{D}'.\nEOF"),
    ("jq reads json", f"echo '{{\"cmd\": \"{D}\"}}' | jq -r .cmd"),
    ("cat heredoc|python3 print", f"cat <<'EOF' | python3\nprint('{D}')\nEOF"),
    ("rig: guarded text in a case list", (f"python3 - <<'EOF'\nimport subprocess\ncases = ['{D}']\n"
                                          "subprocess.run(['python3', 'probe.py'], input='\\n'.join(cases), text=True)\nEOF")),
    ("rig: guarded text in a later argument", ("python3 - <<'EOF'\nimport json, subprocess\n"
                                               f"subprocess.run(['python3', '-c', GATE], input=json.dumps(['{D}']))\nEOF")),
    ("rig: call name inside a string", f"python3 - <<'EOF'\nprint('os.system(\"{D}\")')\nEOF"),
    ("rig: shell eval text in a string", f"python3 - <<'EOF'\nrows = ['eval \"{D}\"']\nprint(rows)\nEOF"),
    ("node template literal", f"node <<'EOF'\nconst s = `{D}`; console.log(s)\nEOF"),
    ("script argument reads a file", f"echo '{PY_RUN.format(d=D)}' | python3 consume.py"),
    ("-c code reads stdin as data", f"echo '{PY_RUN.format(d=D)}' | python3 -c 'import sys; print(sys.stdin.read())'"),
]


@pytest.mark.parametrize("label,executing,inert", FEEDS, ids=[r[0] for r in FEEDS])
def test_fed_code_running_a_guarded_command_is_refused(label, executing, inert):
    assert is_dangerous_command(executing) is True, f"{label}: fed code runs a guarded command"
    assert is_dangerous_command(inert) is False, f"{label}: fed code only prints it"


@pytest.mark.parametrize("label,cmd", DATA_ROWS, ids=[r[0] for r in DATA_ROWS])
def test_guarded_text_as_data_stays_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False, f"{label}: data was refused"


def test_variable_indirection_is_the_named_residual():
    """A command reached through a variable needs dataflow; it stays allowed."""
    cmd = f"python3 - <<'EOF'\nimport subprocess\nc = '{D}'\nsubprocess.run(c, shell=True)\nEOF"
    assert is_dangerous_command(cmd) is False


def test_every_head_pattern_uses_the_single_head_source():
    """The stdin feed pattern, the head finder and _INTERPRETER_HEADS are all built from
    _INTERPRETER_HEAD_ALTERNATION, so they cannot drift apart."""
    alternation = mgc._INTERPRETER_HEAD_ALTERNATION
    for pattern in (mgc._STDIN_INTERPRETER, mgc._INTERPRETER_WORD_RE.pattern, mgc._INTERPRETER_HEADS.pattern):
        assert alternation in pattern
