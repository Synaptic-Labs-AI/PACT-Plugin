"""Interpreter legs: inert without an exec primitive, gated with one.

An interpreter (python, ruby, perl, node, php, awk) runs its quoted text as program
code, and that code reaches a shell only through an exec primitive of its language.
The merge guard therefore strips an interpreter leg's quoted values unless the leg
names a primitive. The primitive list is a substring denylist, so its coverage is
the security claim: every standard, non-obfuscated route from each interpreter to a
process must be refused. Each row below is one such route carrying a guarded
command; each must stay dangerous. The inert rows show the same interpreters with
the guarded text as plain data, which must be allowed.

A primitive name assembled at runtime (string arithmetic inside a reflective call)
is not matched. That needs deliberate obfuscation and is an accepted residual.
"""

import pytest
from shared.merge_guard_common import is_dangerous_command

D = "gh pr " + "merge 5"  # assembled so this file carries no guarded literal


def _sq(head: str, code: str) -> str:
    assert "'" not in code
    return f"{head} '{code}'"


EXECUTING_ROUTES = [
    # python
    ("py os.system", _sq("python3 -c", f'import os; os.system("{D}")')),
    ("py os.popen", _sq("python3 -c", f'import os; os.popen("{D}").read()')),
    ("py os.execvp", _sq("python3 -c", f'import os; os.execvp("sh", ["sh", "-c", "{D}"])')),
    ("py os.execl", _sq("python3 -c", f'import os; os.execl("/bin/sh", "sh", "-c", "{D}")')),
    ("py os.spawnlp", _sq("python3 -c", f'import os; os.spawnlp(0, "sh", "sh", "-c", "{D}")')),
    ("py os.posix_spawn", _sq("python3 -c", f'import os; os.posix_spawn("/bin/sh", ["sh", "-c", "{D}"], {{}})')),
    ("py os.posix_spawnp", _sq("python3 -c", f'import os; os.posix_spawnp("sh", ["sh", "-c", "{D}"], {{}})')),
    ("py subprocess.run", _sq("python3 -c", f'import subprocess; subprocess.run("{D}", shell=True)')),
    ("py subprocess.Popen", _sq("python3 -c", f'import subprocess as s; s.Popen(["sh", "-c", "{D}"])')),
    ("py from subprocess", _sq("python3 -c", f'from subprocess import check_output as c; c("{D}", shell=True)')),
    ("py asyncio shell", _sq("python3 -c", f'import asyncio; asyncio.run(asyncio.create_subprocess_shell("{D}"))')),
    ("py pty.spawn", _sq("python3 -c", f'import pty; pty.spawn(["sh", "-c", "{D}"])')),
    ("py commands", _sq("python -c", f'import commands; commands.getoutput("{D}")')),
    ("py fork+exec", _sq("python3 -c", f'import os\nif os.fork() == 0: os.execv("/bin/sh", ["sh", "-c", "{D}"])')),
    ("py __import__", _sq("python3 -c", f'__import__("os").system("{D}")')),
    ("py import_module", _sq("python3 -c", f'import importlib as i; i.import_module("os").system("{D}")')),
    # ruby
    ("rb system", _sq("ruby -e", f'system("{D}")')),
    ("rb exec", _sq("ruby -e", f'exec("{D}")')),
    ("rb spawn", _sq("ruby -e", f'Process.wait(spawn("{D}"))')),
    ("rb backticks", _sq("ruby -e", f"puts `{D}`")),
    ("rb %x", _sq("ruby -e", f"puts %x({D})")),
    ("rb IO.popen", _sq("ruby -e", f'IO.popen("{D}").read')),
    ("rb Open3", _sq("ruby -e", f'require "open3"; Open3.capture2("{D}")')),
    ("rb Kernel#`", _sq("ruby -e", f'Kernel.send(:`, "{D}")')),
    ("rb PTY.spawn", _sq("ruby -e", f'require "pty"; PTY.spawn("{D}")')),
    ("rb open pipe", _sq("ruby -e", f'open("|{D}").read')),
    # perl
    ("pl system", _sq("perl -e", f'system("{D}")')),
    ("pl exec", _sq("perl -e", f'exec("{D}")')),
    ("pl qx", _sq("perl -e", f"print qx({D})")),
    ("pl backticks", _sq("perl -e", f"print `{D}`")),
    ("pl readpipe", _sq("perl -e", f'print readpipe("{D}")')),
    ("pl open -|", _sq("perl -e", f'open(my $f, "-|", "{D}"); print <$f>')),
    ("pl open |-", _sq("perl -e", f'open(my $f, "|-", "{D}"); close $f')),
    ("pl open leading pipe", _sq("perl -e", f'open(F, "| {D}"); close F')),
    ("pl open trailing pipe", _sq("perl -e", f'open(F, "{D} |"); print <F>')),
    ("pl IPC::Open3", _sq("perl -e", f'use IPC::Open3; open3(my $i, my $o, undef, "{D}")')),
    ("pl IPC::Run", _sq("perl -e", f'use IPC::Run qw(run); run ["sh", "-c", "{D}"]')),
    # node
    ("js exec", _sq("node -e", f'require("child_process").exec("{D}")')),
    ("js execSync", _sq("node -e", f'require("child_process").execSync("{D}")')),
    ("js spawn", _sq("node -e", f'require("child_process").spawn("sh", ["-c", "{D}"])')),
    ("js execFile", _sq("nodejs -e", f'require("child_process").execFile("sh", ["-c", "{D}"])')),
    ("js fork", _sq("node -e", f'require("child_process").fork("x.js", ["{D}"])')),
    ("js node: prefix", _sq("node -e", f'require("node:child_process").execSync("{D}")')),
    ("js process.binding", _sq("node -e", f'process.binding("spawn_sync").spawn({{file: "sh", args: ["sh", "-c", "{D}"]}})')),
    # php
    ("php system", _sq("php -r", f'system("{D}");')),
    ("php exec", _sq("php -r", f'exec("{D}");')),
    ("php shell_exec", _sq("php -r", f'echo shell_exec("{D}");')),
    ("php passthru", _sq("php -r", f'passthru("{D}");')),
    ("php popen", _sq("php -r", f'pclose(popen("{D}", "r"));')),
    ("php proc_open", _sq("php -r", f'proc_open("{D}", [], $p);')),
    ("php pcntl_exec", _sq("php -r", f'pcntl_exec("/bin/sh", ["-c", "{D}"]);')),
    ("php backticks", _sq("php -r", f"echo `{D}`;")),
    # php's `echo` statement inside the code is not the shell builtin: the echo/printf
    # carrier must leave its argument for the interpreter clause to judge.
    ("php echo system", _sq("php -r", f'echo system("{D}");')),
    ("php echo shell_exec wrapped", _sq("timeout 5 php -r", f'echo shell_exec("{D}");')),
    # The same php leg behind a prefix the leg slicer must see through: an fd redirect
    # (sliced on the shared view, not at its `&`) and a coarse wrapper (fails safe).
    ("php echo behind 2>&1", _sq("2>&1 php -r", f'echo shell_exec("{D}");')),
    ("php echo behind flock", _sq("flock f php -r", f'echo system("{D}");')),
    ("php echo behind env", _sq("env X=1 php -r", f'echo system("{D}");')),
    ("php echo behind nohup", _sq("nohup php -r", f'echo system("{D}");')),
    ("ruby behind env", _sq("env X=1 ruby -e", f'system("{D}")')),
    ("perl behind nohup", _sq("nohup perl -e", f'system("{D}")')),
    # awk
    ("awk system", _sq("awk", f'BEGIN {{ system("{D}") }}')),
    ("awk print pipe", _sq("awk", f'BEGIN {{ print "x" | "{D}" }}')),
    ("awk getline pipe", _sq("awk", f'BEGIN {{ "{D}" | getline out }}')),
    ("gawk coprocess", _sq("gawk", f'BEGIN {{ print "x" |& "{D}" }}')),
]

INERT_ROWS = [
    ("py print", _sq("python3 -c", f'print("{D}")')),
    ("py scan", _sq("python3 -c", f'import re, sys; print(re.findall("{D}", sys.stdin.read()))')),
    ("rb puts", _sq("ruby -e", f'puts "{D}"')),
    ("pl print", _sq("perl -e", f'print "{D}\\n"')),
    ("js log", _sq("node -e", f'console.log("{D}")')),
    ("php echo", _sq("php -r", f'echo "{D}";')),
    ("shell echo in bash -c", _sq("bash -c", f'echo "{D}"')),
    # Only php has an `echo` statement. In other interpreter code an `echo ... | bash`
    # string is data (a rig writing a test command), even beside a primitive.
    ("py rig with echo data", "python3 -c \"import json, subprocess; "
                              "json.dump({'c': \\\"echo '" + D + "' | bash\\\"}, open('c.json', 'w'))\""),
    ("awk pattern", f"awk '/{D}/ {{print}}' notes.txt"),
]


@pytest.mark.parametrize("label,cmd", EXECUTING_ROUTES, ids=[r[0] for r in EXECUTING_ROUTES])
def test_every_standard_spawn_route_stays_refused(label, cmd):
    assert is_dangerous_command(cmd) is True, f"{label}: an interpreter route to a shell was freed: {cmd!r}"


@pytest.mark.parametrize("label,cmd", INERT_ROWS, ids=[r[0] for r in INERT_ROWS])
def test_interpreter_text_as_data_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False, f"{label}: inert interpreter code still refused: {cmd!r}"


# A versioned or suffixed binary is the same interpreter: with a primitive its code
# can run the guarded command; without one the text is data.
VERSIONED_HEADS = [
    ("php8.2 -r", 'system("{d}");', 'echo "{d}";'),
    ("php82 -r", 'echo system("{d}");', 'echo "{d}";'),
    ("ruby3.2 -e", 'system("{d}")', 'puts "{d}"'),
    ("perl5.36 -e", 'system("{d}")', 'print "{d}"'),
    ("node18 -e", 'require("child_process").execSync("{d}")', 'console.log("{d}")'),
    ("nodejs20 -e", 'require("child_process").execSync("{d}")', 'console.log("{d}")'),
    ("python3.11 -c", 'import os; os.system("{d}")', 'print("{d}")'),
    ("python3.12 -c", 'import subprocess; subprocess.run("{d}", shell=True)', 'print("{d}")'),
    ("mawk", 'BEGIN {{ system("{d}") }}', '/{d}/ {{ print }}'),
    ("nawk", 'BEGIN {{ system("{d}") }}', '/{d}/ {{ print }}'),
]


@pytest.mark.parametrize("head,executing,inert", VERSIONED_HEADS, ids=[r[0] for r in VERSIONED_HEADS])
def test_versioned_interpreter_heads(head, executing, inert):
    assert is_dangerous_command(_sq(head, executing.format(d=D))) is True
    assert is_dangerous_command(_sq(head, inert.format(d=D))) is False
