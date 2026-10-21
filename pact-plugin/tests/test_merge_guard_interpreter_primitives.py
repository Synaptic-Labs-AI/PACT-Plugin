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


# Other runtimes that run code given on their command line, each with its own exec route.
# `deno eval` is deno's one-liner subcommand: the token `eval` there is not a primitive.
RUNTIME_HEADS = [
    ("pypy3", "pypy3 -c", 'import os; os.system("{d}")', 'print("{d}")'),
    ("pypy", "pypy -c", 'import subprocess; subprocess.run("{d}", shell=True)', 'print("{d}")'),
    ("bun spawn", "bun -e", 'Bun.spawn(["sh", "-c", "{d}"])', 'console.log("{d}")'),
    ("bun $", "bun -e", "await Bun.$`{d}`", 'console.log("{d}")'),
    ("deno run", "deno eval", 'Deno.run({{cmd: ["sh", "-c", "{d}"]}})', 'console.log("{d}")'),
    ("deno Command", "deno eval", 'new Deno.Command("sh", {{args: ["-c", "{d}"]}}).outputSync()',
     'console.log("{d}")'),
    ("php-cgi", "php-cgi -r", 'echo system("{d}");', 'echo "{d}";'),
    ("lua execute", "lua -e", 'os.execute("{d}")', 'print("{d}")'),
    ("lua popen", "lua5.4 -e", 'io.popen("{d}")', 'print("{d}")'),
    ("luajit", "luajit -e", 'os.execute("{d}")', 'print("{d}")'),
    ("Rscript", "Rscript -e", 'system("{d}")', 'print("{d}")'),
    ("R system2", "R -e", 'system2("sh", c("-c", "{d}"))', 'print("{d}")'),
    ("R pipe", "Rscript -e", 'readLines(pipe("{d}"))', 'cat("{d}")'),
    ("osascript", "osascript -e", 'do shell script "{d}"', 'display dialog "{d}"'),
]


@pytest.mark.parametrize("label,head,executing,inert", RUNTIME_HEADS, ids=[r[0] for r in RUNTIME_HEADS])
def test_runtime_heads(label, head, executing, inert):
    assert is_dangerous_command(_sq(head, executing.format(d=D))) is True
    assert is_dangerous_command(_sq(head, inert.format(d=D))) is False


# Code given as a -c/-e argument follows the same direct-call rule as code fed on stdin:
# a primitive call whose leading literal arguments spell a guarded command is refused,
# including the list and separate-argument forms the text scan cannot read as one line.
DIRECT_CALL_ARG_FORMS = [
    ("py -c run list", "python3 -c \"import subprocess; subprocess.run(['gh', 'pr', 'merge', '5'])\"",
     "python3 -c \"print(['gh', 'pr', 'merge', '5'])\""),
    ("py -c run list sq", "python3 -c 'import subprocess; subprocess.run([\"gh\",\"pr\",\"merge\",\"5\"])'",
     "python3 -c 'print([\"gh\",\"pr\",\"merge\",\"5\"])'"),
    ("py -c Popen push list",
     "python3 -c \"import subprocess as s; s.Popen(['git', 'push', '--force', 'origin', 'main'])\"",
     "python3 -c \"print(['git', 'push', '--force', 'origin', 'main'])\""),
    ("py -c run list escaped dq",
     'python3 -c "import subprocess; subprocess.run([\\"gh\\", \\"pr\\", \\"merge\\", \\"5\\"])"',
     'python3 -c "print([\\"gh\\", \\"pr\\", \\"merge\\", \\"5\\"])"'),
    ("node -e spawnSync args", "node -e \"require('child_process').spawnSync('gh', ['pr', 'merge', '5'])\"",
     "node -e \"console.log('gh', ['pr', 'merge', '5'])\""),
    ("ruby -e system args", "ruby -e \"system('gh', 'pr', 'merge', '5')\"",
     "ruby -e \"p ['gh', 'pr', 'merge', '5']\""),
]


@pytest.mark.parametrize("label,executing,inert", DIRECT_CALL_ARG_FORMS,
                         ids=[r[0] for r in DIRECT_CALL_ARG_FORMS])
def test_code_argument_direct_call_forms(label, executing, inert):
    assert is_dangerous_command(executing) is True
    assert is_dangerous_command(inert) is False


def test_direct_call_rule_reads_a_later_argument_as_data():
    """The direct-call rule refuses only the command a primitive runs: a probe given
    guarded text as an argument runs `python3 probe.py`. (The whole -c leg is still
    refused by the older primitive-plus-guarded-text rule, a known over-block whose
    route is a script file.)"""
    from shared.merge_guard_common import _code_runs_guarded_directly

    code = "import subprocess; subprocess.run(['python3', 'probe.py', '" + D + "'])"
    assert _code_runs_guarded_directly("python3", code) is False
    direct = "import subprocess; subprocess.run(['gh', 'pr', 'merge', '5'])"
    assert _code_runs_guarded_directly("python3", direct) is True


# argv passed inside an options object: Deno.Command(cmd, {args: [...]}), Deno.run({cmd: [...]}).
# Only the object's own top-level `args`/`cmd` list counts; a brace inside a string does not
# end the object, and a list nested deeper is data.
OBJECT_ARG_FORMS = [
    ("deno eval Command args",
     "deno eval 'new Deno.Command(\"gh\", {args: [\"pr\", \"merge\", \"5\"]}).outputSync()'",
     "deno eval 'console.log(\"gh\", {args: [\"pr\", \"merge\", \"5\"]})'"),
    ("deno run - heredoc Command args",
     "deno run - <<'EOF'\nnew Deno.Command('gh', {args: ['pr', 'merge', '5']}).outputSync()\nEOF",
     "deno run - <<'EOF'\nconsole.log('gh', {args: ['pr', 'merge', '5']})\nEOF"),
    ("deno eval run cmd",
     "deno eval 'Deno.run({cmd: [\"gh\", \"pr\", \"merge\", \"5\"]})'",
     "deno eval 'console.log({cmd: [\"gh\", \"pr\", \"merge\", \"5\"]})'"),
    ("deno Command args after a brace in a string",
     "deno eval 'new Deno.Command(\"git\", {cwd: \"}\", args: [\"push\", \"--force\", \"origin\", \"main\"]}).outputSync()'",
     "deno eval 'console.log(\"git\", {cwd: \"}\", args: [\"push\", \"--force\", \"origin\", \"main\"]})'"),
]


@pytest.mark.parametrize("label,executing,inert", OBJECT_ARG_FORMS, ids=[r[0] for r in OBJECT_ARG_FORMS])
def test_object_argv_forms(label, executing, inert):
    assert is_dangerous_command(executing) is True
    assert is_dangerous_command(inert) is False


def test_object_argv_nested_deeper_is_data():
    cmd = "deno eval 'new Deno.Command(\"echo\", {env: {args: [\"gh\", \"pr\", \"merge\", \"5\"]}}).outputSync()'"
    assert is_dangerous_command(cmd) is False


# Primitives are matched as calls and statements outside string literals: an aliased
# import is still the primitive, a paren-less perl open or a ruby send(:system) is still a
# route, while python's open() is file I/O and awk's `||` is a logical or.
CALL_SHAPED_ROUTES = [
    ("py from-import run", "python3 -c 'from subprocess import run; run([\"gh\", \"pr\", \"merge\", \"5\"])'"),
    ("py import as", "python3 -c 'import subprocess as sp; sp.run([\"gh\", \"pr\", \"merge\", \"5\"])'"),
    ("py from os import system", f"python3 -c 'from os import system; system(\"{D}\")'"),
    ("py from-import alias", "python3 -c 'from subprocess import check_call as cc; cc([\"gh\", \"pr\", \"merge\", \"5\"])'"),
    ("pl paren-less open pipe", f"perl -e 'open F, \"| {D}\"; close F'"),
    ("rb send system", f"ruby -e 'Kernel.send(:system, \"{D}\")'"),
    ("rb public_send system", f"ruby -e 'Kernel.public_send(:system, \"{D}\")'"),
    ("pl -ne combined flag", f"perl -ne 'system(\"{D}\")' notes.txt"),
    ("py value flag before -c", f"python3 -W ignore -c 'import os; os.system(\"{D}\")'"),
]
CALL_SHAPED_DATA = [
    ("py open is file io", f"python3 -c 'print(open(\"session.log\").read().count(\"{D}\"))'"),
    ("awk logical or", f"awk '/x/ || /{D}/ {{print}}' session.log"),
    ("py word holding system", f"python3 -c 'print(\"filesystem check before {D}\")'"),
    ("rb send of another method", f"ruby -e 'puts [1].send(:first), \"{D}\"'"),
    ("py call name inside a string", f"python3 -c 'print(\"then os.system({D}) by hand\")'"),
    ("pl -ne word holding system", f"perl -ne 'print \"filesystem: $_\" if /{D}/' session.log"),
]


@pytest.mark.parametrize("label,cmd", CALL_SHAPED_ROUTES, ids=[r[0] for r in CALL_SHAPED_ROUTES])
def test_call_shaped_route_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", CALL_SHAPED_DATA, ids=[r[0] for r in CALL_SHAPED_DATA])
def test_primitive_like_text_that_runs_nothing_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False


def test_aliased_import_on_its_own_line_in_fed_code_is_refused():
    cmd = "python3 - <<'EOF'\nimport subprocess as sp\nsp.run([\"gh\", \"pr\", \"merge\", \"5\"])\nEOF"
    assert is_dangerous_command(cmd) is True


def test_parenthesized_multi_line_from_import_is_resolved():
    cmd = ("python3 - <<'EOF'\nfrom subprocess import (\n    run,\n    Popen as P,\n)\n"
           "P([\"gh\", \"pr\", \"merge\", \"5\"])\nEOF")
    assert is_dangerous_command(cmd) is True


def test_fork_of_a_module_passes_its_argv_as_data():
    """child_process.fork runs a JS module with the given argv; it starts no shell, so a
    guarded command in that argv is data to the module (the ratified script-runs-its-own-
    argv residual), not a command the guard can see run."""
    cmd = _sq("node -e", f'require("child_process").fork("x.js", ["{D}"])')
    assert is_dangerous_command(cmd) is False
