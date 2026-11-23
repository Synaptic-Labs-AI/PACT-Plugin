"""No value the memory store work reads reaches a git command, at runtime.

Location: pact-plugin/tests/test_store_work_git_values_runtime.py.

The route census in test_remediation_skill_prose.py proves by reading code
that no store-work value reaches a git command's argv, cwd or environment.
It does not follow a value written into a container item, or the result of a
callable outside the project (`copy.copy`, `getattr`, `max`, ...). This test
backs those limits by running the memory CLI's store-work commands on values
marked with sentinel strings, through a `git` first on PATH that logs every
call, and failing when a sentinel reaches one.

`ROUTES` lists what runs: every command of the memory CLI's parser, by each
flag that changes what it reads (setup; save with `--claude-md-root`, with
`--no-sync` and with its default root; search plain, with `--current-file`
and with `--claude-md-root`; list; get; status; update and `update
--replace`; sync with `--claude-md-root` and with its default root;
project-file; `project-id show`; rekey with `--candidates`, with `--apply`,
with `--dry-run`, with an explicit `--to` and no mode flag, with `--from`
alone on another repository's canonical bucket, which refuses, and with
`--from` alone on the checkout's own legacy bucket, whose `--to` defaults;
delete). Positional JSON input and `--limit` are not run separately: they
change only how a value arrives, or how many rows come back.

Each sentinel also needs evidence that the run read it; otherwise a clean
log would mean nothing. Store rows are read back in command output, and the
session records and the prune ledger are read through files the process
opens, which an audit hook records. The repository's `description` is read
for its ctime only, so no string can mark it; the records that make the
check consult it are marked, and their reads are observed. The legacy
bucket's rows are read only once a session record vouches for the bucket
and no row's session is recorded at another directory, so the last two
routes give the live legacy session a start after the `description`, move
the bucket's other two rows to that session, and then list and rekey: the
list prints the legacy rows.

One flow is allowed: a write whose CLAUDE_PROJECT_DIR disagrees with the
session record asks git about the recorded project directory, to tell a
worktree session from a moved one. Each of the four write sites (save,
update, delete, sync) is run once with that disagreement, and the recorded
directory's sentinel may reach git only as the two questions that check
asks of that directory, once each.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures.identity_git import make_repo, start_after_description
from shared.pact_context import PRUNE_LEDGER_NAME, project_slug

PLUGIN = Path(__file__).resolve().parent.parent
CLI = PLUGIN / "skills" / "pact-memory" / "scripts" / "cli.py"
MARK = "q76s"

def _marked(field: str) -> str:
    return f"{MARK}-row-{field.replace('_', '-')}"


# A store row with a sentinel in every field a save accepts.
ROW = {
    "context": _marked("context"),
    "goal": _marked("goal"),
    "active_tasks": [{"task": _marked("active_tasks"), "status": "pending"}],
    "decisions": [{"decision": _marked("decisions"), "rationale": _marked("rationale")}],
    "entities": [{"name": _marked("entities"), "type": "module", "notes": _marked("notes")}],
    **{field: [_marked(field)] for field in (
        "lessons_learned", "reasoning_chains", "agreements_reached", "disagreements_resolved",
    )},
}
ROW_MARKS = [_marked(field) for field in (*ROW, "rationale", "notes")]
FILE_ROW = f"{MARK}-file-row/notes.txt"
OTHER_BUCKET = f"{MARK}-bucket"
CURRENT_SESSION = f"{MARK}-current-session"
LEGACY_LIVE = f"{MARK}-legacy-live"
LEGACY_LIVE_ELSEWHERE = f"{MARK}-legacy-live-elsewhere"
LEGACY_PRUNED = f"{MARK}-legacy-pruned"
DISAGREEING_SESSION = f"{MARK}-disagreeing-session"
RECORDED_DIR_NAME = f"{MARK}-recorded-dir"
ELSEWHERE = f"/{MARK}-recorded-elsewhere"
# A session start before any repository's description, so a record whose
# project_dir is the repository is read, its description consulted, and the
# record still not taken as evidence: every record is read, none stops the check.
LONG_AGO = "2000-01-01T00:00:00+00:00"
# Every route the rig runs, in order: each memory CLI command, by each flag
# that changes what it reads, then each write site with the disagreement.
ROUTES = (
    "setup", "save", "save --no-sync", "rekey --apply", "search", "search --current-file",
    "search --claude-md-root", "list", "get", "status", "update", "update --replace", "sync",
    "sync, default root", "save, default root", "project-file", "project-id show",
    "rekey --candidates", "rekey", "rekey --dry-run", "rekey --from (canonical, refused)", "delete",
    "save, disagreeing record", "update, disagreeing record", "delete, disagreeing record",
    "sync, disagreeing record", "list, legacy bucket vouched for",
    "rekey --from (legacy, --to defaulted)",
)
# The legacy bucket's rows, read only once a session record vouches for it.
LEGACY_ROWS = tuple(f"{MARK}-row-{row_id}" for row_id in ("l1", "l2", "l3"))
# The routes whose output carries store rows, so each must print a marked one.
ROUTES_THAT_PRINT_ROWS = ("search", "search --current-file", "search --claude-md-root", "list", "get")
# What the disagreement check asks git about the recorded directory, in order.
RECORDED_DIR_QUESTIONS = (("rev-parse", "--show-toplevel"), ("rev-parse", "--git-common-dir"))

_GIT_WRAPPER = """#!/bin/sh
{{ printf 'CALL\\t%s' "$(pwd -P)"; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'
  env | grep -E '^(GIT_[A-Za-z0-9_]*|PATH|HOME)=' | sed 's/^/ENV\\t/'; }} >> '{log}'
exec '{git}' "$@"
"""

# Records each file or folder the CLI process opens or lists under the
# config dir, so a test can show a record was read. A file object opened
# before the hook is installed does the writing, so the hook raises no
# event of its own.
_SITECUSTOMIZE = """import os, sys
_log = os.environ.get("Q76_READ_LOG")
_root = os.environ.get("Q76_READ_ROOT")
if _log and _root:
    _out = open(_log, "a", encoding="utf-8")
    def _seen(event, args):
        if event in ("open", "os.listdir", "os.scandir") and args and args[0] is not None:
            try:
                path = os.fsdecode(args[0])
            except TypeError:
                return
            if path.startswith(_root):
                _out.write(event + "\\t" + path + "\\n")
                _out.flush()
    sys.addaudithook(_seen)
"""


def _calls(log: Path) -> list:
    """Each logged git call as (cwd, argv, env lines)."""
    calls = []
    for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else ():
        if line.startswith("CALL\t"):
            cwd, *argv = line.split("\t")[1:]
            calls.append((cwd, tuple(argv), []))
        elif line.startswith("ENV\t") and calls:
            calls[-1][2].append(line[4:])
    return calls


def _question(cwd: str, argv: tuple) -> tuple:
    """(directory git ran in, the rest of argv), with each leading -C applied."""
    while len(argv) >= 2 and argv[0] == "-C":
        cwd, argv = os.path.join(cwd, argv[1]), argv[2:]
    return os.path.realpath(cwd), argv


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """Run every store-work command once on marked values; return what each
    run logged, printed and opened."""
    base = tmp_path_factory.mktemp("store-work-rig")
    repo = make_repo(base / "proj", remote="https://example.invalid/o/proj.git")
    recorded = make_repo(base / RECORDED_DIR_NAME)
    home = base / "home"
    config = home / ".claude"
    config.mkdir(parents=True)
    db = base / "store.db"
    bindir = base / "bin"
    bindir.mkdir()
    git_log = base / "git.log"
    (bindir / "git").write_text(
        _GIT_WRAPPER.format(log=git_log, git=shutil.which("git")), encoding="utf-8"
    )
    (bindir / "git").chmod(0o755)
    sitedir = base / "site"
    sitedir.mkdir()
    (sitedir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    read_log = base / "read.log"
    documents = {p: p.read_bytes() for p in base.rglob("CLAUDE.md")}

    sessions = config / "pact-sessions"
    slug_dir = sessions / project_slug(str(repo))

    def record(session_id, folder, **fields):
        (folder / session_id).mkdir(parents=True, exist_ok=True)
        path = folder / session_id / "pact-session-context.json"
        path.write_text(json.dumps({"session_id": session_id, **fields}), encoding="utf-8")
        return path

    record(CURRENT_SESSION, slug_dir, project_dir=str(repo), started_at=LONG_AGO)
    legacy_live = record(LEGACY_LIVE, slug_dir, project_dir=str(repo), started_at=LONG_AGO)
    legacy_elsewhere = record(
        LEGACY_LIVE_ELSEWHERE, slug_dir, project_dir=ELSEWHERE + "-live", started_at=LONG_AGO
    )
    ledger = slug_dir / PRUNE_LEDGER_NAME
    ledger.write_text(
        json.dumps({"session_id": LEGACY_PRUNED, "project_dir": str(repo), "started_at": LONG_AGO})
        + "\n"
        + json.dumps({"session_id": LEGACY_PRUNED, "project_dir": ELSEWHERE + "-ledger",
                      "started_at": LONG_AGO})
        + "\n",
        encoding="utf-8",
    )
    disagreeing = sessions / project_slug(str(recorded))
    disagreeing_record = record(
        DISAGREEING_SESSION, disagreeing, project_dir=str(recorded), started_at=LONG_AGO
    )
    (disagreeing / DISAGREEING_SESSION / "worktree-identity.json").write_text(
        json.dumps({"session_id": DISAGREEING_SESSION, "declared": str(recorded),
                    "worktree": str(recorded), "common_dir": str(recorded / ".git")}),
        encoding="utf-8",
    )

    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "PACT_", "GIT_", "PYTEST_")) and k != "PYTHONPATH"}
    env.update(
        PATH=f"{bindir}{os.pathsep}{env['PATH']}", HOME=str(home),
        CLAUDE_CONFIG_DIR=str(config), PACT_TEST_MEMORY_DIR=str(base / "memdir"),
        CLAUDE_PROJECT_DIR=str(repo), CLAUDE_CODE_SESSION_ID=CURRENT_SESSION,
        PYTHONPATH=str(sitedir), Q76_READ_LOG=str(read_log), Q76_READ_ROOT=str(config),
    )
    runs = []

    def run(label, *argv, stdin=None, session=CURRENT_SESSION):
        git_log.unlink(missing_ok=True)
        done = subprocess.run(
            [sys.executable, str(CLI), *map(str, argv),
             *(() if argv[0] == "project-file" else ("--db-path", str(db)))],
            cwd=str(repo), env={**env, "CLAUDE_CODE_SESSION_ID": session},
            input=stdin, capture_output=True, text=True, timeout=120, check=False,
        )
        runs.append((label, done, _calls(git_log)))
        return done

    made = run("setup", "setup")
    assert made.returncode == 0, made.stderr
    saved = run("save", "save", "--stdin", "--claude-md-root", repo, stdin=json.dumps(ROW))
    assert saved.returncode == 0, saved.stderr
    first = json.loads(saved.stdout)["result"]["memory_id"]
    saved = run("save --no-sync", "save", "--stdin", "--no-sync",
                stdin=json.dumps({"context": f"{MARK}-row-no-sync"}))
    assert saved.returncode == 0, saved.stderr
    second = json.loads(saved.stdout)["result"]["memory_id"]
    with sqlite3.connect(db) as conn:
        (canonical,) = conn.execute("SELECT project_id FROM memories WHERE id = ?", (first,)).fetchone()
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)", (FILE_ROW, canonical))
        conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES (?, 'f1')", (first,))
        stamp = "2026-01-01T00:00:00+00:00"
        for row_id, bucket, session_id in (
            ("l1", "proj", LEGACY_LIVE), ("l2", "proj", LEGACY_LIVE_ELSEWHERE),
            ("l3", "proj", LEGACY_PRUNED), ("o1", OTHER_BUCKET, CURRENT_SESSION),
        ):
            conn.execute(
                "INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row_id, f"{MARK}-row-{row_id}", bucket, session_id, stamp, stamp),
            )

    # The move runs first so the search below reads the moved row.
    run("rekey --apply", "project-id", "rekey", "--from", OTHER_BUCKET, "--to", canonical, "--apply")
    run("search", "search", MARK)
    run("search --current-file", "search", MARK, "--current-file", repo / FILE_ROW)
    run("search --claude-md-root", "search", MARK, "--claude-md-root", repo)
    run("list", "list")
    run("get", "get", first)
    run("status", "status")
    run("update", "update", first, "--stdin", stdin=json.dumps({"goal": f"{MARK}-row-updated"}))
    run("update --replace", "update", second, "--stdin", "--replace",
        stdin=json.dumps({"context": f"{MARK}-row-replaced"}))
    run("sync", "sync", "--claude-md-root", repo)
    run("sync, default root", "sync")
    run("save, default root", "save", "--stdin", stdin=json.dumps({"context": f"{MARK}-row-default-root"}))
    run("project-file", "project-file")
    run("project-id show", "project-id", "show")
    run("rekey --candidates", "project-id", "rekey", "--candidates")
    run("rekey", "project-id", "rekey", "--from", "proj", "--to", canonical)
    run("rekey --dry-run", "project-id", "rekey", "--from", "proj", "--to", canonical, "--dry-run")
    # Another repository's canonical bucket: a defaulted --to here is this
    # checkout's identity, so --from equal to it would be a no-op success.
    run("rekey --from (canonical, refused)", "project-id", "rekey", "--from", "example.invalid/o/other")
    run("delete", "delete", second)
    for label, argv, stdin in (
        ("save", ("save", "--stdin", "--claude-md-root", repo), json.dumps({"context": "x"})),
        ("update", ("update", first, "--stdin"), json.dumps({"goal": "x"})),
        ("delete", ("delete", first), None),
        # A declared --claude-md-root is a warrant sync honours over the
        # disagreement, so this run declares none.
        ("sync", ("sync",), None),
    ):
        run(f"{label}, disagreeing record", *argv, stdin=stdin, session=DISAGREEING_SESSION)
    # Last, so no route above reads the bucket as vouched for: the live
    # legacy session now starts after the repository's description, dated
    # from that file's ctime rather than this process's clock.
    record(LEGACY_LIVE, slug_dir, project_dir=str(repo),
           started_at=start_after_description(repo))
    # A row whose session is recorded at another directory keeps the whole
    # bucket out of the read scope, and the routes above have read both of
    # those records. The two rows now carry the live legacy session, so the
    # bucket is vouched for and the list prints all three.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE memories SET session_id = ? WHERE id IN ('l2', 'l3')", (LEGACY_LIVE,)
        )
    run("list, legacy bucket vouched for", "list", "--limit", "50")
    run("rekey --from (legacy, --to defaulted)", "project-id", "rekey", "--from", "proj")
    return {
        "runs": runs, "recorded": recorded, "read_log": read_log, "base": base,
        "documents": documents,
        "records": {"legacy live session record": legacy_live,
                    "legacy session record naming another directory": legacy_elsewhere,
                    "prune ledger": ledger, "disagreeing session record": disagreeing_record},
    }


def test_no_store_work_value_reaches_a_git_command(rig):
    """Every store-work command runs, every marked value is read, and no
    marked value reaches a git command except the one allowed question."""
    runs = rig["runs"]
    assert tuple(label for label, _done, _calls in runs) == ROUTES
    refusals = [label for label, _done, _calls in runs if label.endswith("disagreeing record")]
    assert len(refusals) == 4, refusals
    for label, done, _calls in runs:
        if label in refusals:
            assert done.returncode != 0 and RECORDED_DIR_NAME in done.stdout + done.stderr, (
                f"{label}: the write did not refuse on the disagreement: {done.stdout} {done.stderr}"
            )
        elif label.endswith("(canonical, refused)"):
            out = done.stdout + done.stderr
            assert "REKEY_REFUSED" in out and "is a canonical bucket" in out, (label, out)
        else:
            assert done.returncode == 0, (label, done.stdout, done.stderr)

    # Controls: each marked value was read in this run, and each route that
    # prints rows printed a marked one.
    silent = [label for label, done, _calls in runs
              if label in ROUTES_THAT_PRINT_ROWS and ROW["context"] not in done.stdout]
    assert silent == [], f"these routes printed no marked row: {silent}"
    printed = "".join(done.stdout for label, done, _calls in runs)
    # The values `update` writes are inputs to that write, not reads, so
    # they are checked for leaks but need no read evidence. The other
    # bucket's mark is its id, which the rekey commands read and print.
    unread = [value for value in (*ROW_MARKS, *LEGACY_ROWS, FILE_ROW, OTHER_BUCKET)
              if value not in printed]
    opened = rig["read_log"].read_text(encoding="utf-8") if rig["read_log"].exists() else ""
    unread += [name for name, path in rig["records"].items() if str(path) not in opened]
    assert unread == [], f"no evidence these marked values were read: {unread}"

    # The leak check. The one allowed flow is the disagreement check's
    # verification of the recorded directory (`record_resolution`): asked of
    # that directory, `--show-toplevel` and then `--git-common-dir`, once each.
    recorded = os.path.realpath(rig["recorded"])
    leaks, asked = [], {label: [] for label in refusals}
    for label, _done, calls in runs:
        for cwd, argv, environ in calls:
            if MARK not in "\t".join((cwd, *argv, *environ)):
                continue
            where, question = _question(cwd, argv)
            if (label in refusals and where == recorded and question in RECORDED_DIR_QUESTIONS
                    and not any(MARK in entry for entry in environ)):
                asked[label].append(question)
                continue
            leaks.append((label, cwd, argv, [entry for entry in environ if MARK in entry]))
    assert leaks == [], f"a marked store-work value reached a git command: {leaks}"
    assert asked == {label: list(RECORDED_DIR_QUESTIONS) for label in refusals}, (
        f"the disagreement check did not ask exactly its questions at each write site: {asked}"
    )
    assert {p: p.read_bytes() for p in rig["base"].rglob("CLAUDE.md")} == rig["documents"]
