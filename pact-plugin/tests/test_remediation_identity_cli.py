"""CLI contracts: what a refusal names, and what a printed remedy does.

Each class names the behaviour it pins and the defect it would catch. The
fixtures are REAL repositories built from a fresh `git init` with a fake
origin URL, and every store is a scratch file under `tmp_path`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    write_common_dir_override,
)
from helpers import create_test_schema

UNREPRESENTABLE = "https://host.example/own%ZZr/repo"


@pytest.fixture
def identity_env(monkeypatch, tmp_path):
    """A tmp root outside any repository, with no ambient declaration."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


@pytest.fixture
def store(tmp_path):
    """A scratch store with the schema present."""
    path = tmp_path / "store.db"
    conn = sqlite3.connect(str(path))
    create_test_schema(conn)
    conn.close()
    return path


def _cli(argv, capfd):
    """Run the CLI in process; return (exit code, parsed envelope)."""
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    stream = captured.out if exit_info.value.code == 0 else captured.err
    return exit_info.value.code, json.loads(stream)


class TestTheRefusalNamesTheDecision:
    """An unresolved-identity refusal reports what the resolver decided.

    Catches a refusal that re-derives its cause from the process location:
    it then names the cwd's override instead of the declared repository's,
    says "None could be verified" about a repository git answered for, and
    tells a caller standing inside the project to run from inside it.
    """

    def _declined_repo(self, root):
        repo = make_repo(root / "declined", remote=UNREPRESENTABLE)
        override = write_common_dir_override(repo, "bad\x01pin")
        return repo, override

    def test_a_declared_repository_names_its_cause_and_its_override(
        self, identity_env, store, monkeypatch, capfd
    ):
        repo, override = self._declined_repo(identity_env)
        outside = identity_env / "elsewhere"
        outside.mkdir()
        monkeypatch.chdir(outside)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        code, envelope = _cli(
            ["save", json.dumps({"context": "c"}), "--db-path", str(store)], capfd
        )
        assert code == 1
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED"
        message = envelope["message"]
        assert "cannot be represented" in message
        assert str(override) in message
        assert "None could be verified" not in message
        assert "U+003C" not in message

    def test_inside_the_repository_it_does_not_claim_nothing_was_declared(
        self, identity_env, store, monkeypatch, capfd
    ):
        repo, override = self._declined_repo(identity_env)
        monkeypatch.chdir(repo)
        code, envelope = _cli(
            ["save", json.dumps({"context": "c"}), "--db-path", str(store)], capfd
        )
        assert code == 1
        message = envelope["message"]
        assert "No project directory was declared" not in message
        assert "cannot be represented" in message
        assert str(override) in message
        assert "U+003C" not in message


class TestAnEmptyUrlDeclineNamesItsDetailOnce:
    """The empty-url decline's fixed detail prints once in the warning and
    once in the refusal.

    Catches the detail dropped on the way from the root identity to the
    operator (the cause then reads as a generic unrepresentable origin), and
    the detail appended twice by two layers.
    """

    def _repo(self, root):
        from fixtures.identity_git import run_git

        repo = make_repo(root / "proj", remote="git@a.example:o/a.git")
        run_git("config", "--add", "remote.origin.url", "", cwd=repo)
        return repo

    def test_the_warning_and_the_refusal_each_carry_it_once(
        self, identity_env, store, monkeypatch, capfd, caplog
    ):
        from scripts import memory_api
        from scripts.project_identity import EMPTY_URL_ENTRY_DETAIL

        memory_api._LEGACY_SCOPE_CACHE.clear()
        monkeypatch.chdir(self._repo(identity_env))
        caplog.clear()
        code, envelope = _cli(
            ["save", json.dumps({"context": "c"}), "--db-path", str(store)], capfd
        )
        assert code == 1, envelope
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED"
        assert envelope["message"].count(EMPTY_URL_ENTRY_DETAIL) == 1, envelope
        warnings = [r.getMessage() for r in caplog.records if "no project id was derived" in r.getMessage()]
        assert warnings, caplog.text
        assert all(w.count(EMPTY_URL_ENTRY_DETAIL) == 1 for w in warnings), warnings

    def test_another_unrepresentable_origin_carries_no_detail(
        self, identity_env, store, monkeypatch, capfd, caplog
    ):
        """Control: the detail belongs to the empty-url cause only."""
        from scripts import memory_api
        from scripts.project_identity import EMPTY_URL_ENTRY_DETAIL

        memory_api._LEGACY_SCOPE_CACHE.clear()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=UNREPRESENTABLE))
        code, envelope = _cli(
            ["save", json.dumps({"context": "c"}), "--db-path", str(store)], capfd
        )
        assert code == 1, envelope
        assert "cannot be represented" in envelope["message"]
        assert EMPTY_URL_ENTRY_DETAIL not in envelope["message"]
        assert EMPTY_URL_ENTRY_DETAIL not in caplog.text


class TestShowRunsNoWritePathInitialiser:
    """`project-id show` reports on a store; it never prepares one.

    Catches the legacy probe running the write-path initialiser behind a
    read: on a store with no vector table it tries the CREATE, logs that it
    could not, and loads the embedding service a report never needs.
    """

    def test_show_on_a_store_without_a_vector_table(
        self, identity_env, store, monkeypatch, capfd, caplog
    ):
        import logging

        from scripts import embeddings

        repo = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        conn = sqlite3.connect(str(store))
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES ('legacy0001', 'c', 'proj', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()
        calls = []
        real = embeddings.get_embedding_service
        monkeypatch.setattr(
            embeddings, "get_embedding_service",
            lambda: calls.append(1) or real(),
        )
        monkeypatch.chdir(repo)
        with caplog.at_level(logging.WARNING):
            code, envelope = _cli(["project-id", "show", "--db-path", str(store)], capfd)
        assert code == 0, envelope
        assert envelope["result"]["legacy_scope"]["candidate"] == "proj"
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "Could not create vector table" not in messages
        assert calls == []


# ---------------------------------------------------------------------------
# Printed remedies name their target; a defaulted target is refused where the
# gate cannot support it
# ---------------------------------------------------------------------------

CREATED_AT = "2026-01-01T00:00:00+00:00"


def _seed(store, bucket, *, memory_id, path, session_id="legacy-session"):
    conn = sqlite3.connect(str(store))
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, 'row', ?, ?, ?, ?)",
        (memory_id, bucket, session_id, CREATED_AT, CREATED_AT),
    )
    conn.execute(
        "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
        ("file-" + memory_id, path, bucket),
    )
    conn.commit()
    conn.close()


def _snapshot(store):
    conn = sqlite3.connect(str(store))
    try:
        return (
            sorted(conn.execute("SELECT id, project_id FROM memories").fetchall()),
            sorted(conn.execute("SELECT id, path, project_id FROM files").fetchall()),
        )
    finally:
        conn.close()


@pytest.fixture
def excluded_rig(identity_env, store, monkeypatch):
    """Two checkouts named `proj`: the bucket's absolute rows are in `two`.

    The cwd is `one/proj`, a different repository, so the gate EXCLUDES the
    `proj` bucket: its rows belong to `two/proj`.
    """
    from scripts import memory_api

    for memo in (memory_api._LEGACY_SCOPE_CACHE, memory_api._EXCLUSION_WARNED):
        memo.clear()
    one = make_repo(identity_env / "one" / "proj", remote="https://github.com/o/one.git")
    two = identity_env / "two" / "proj"
    two.mkdir(parents=True)
    _seed(store, "proj", memory_id="victim0001", path=str(two / "secret.py"))
    _seed(store, "github.com/o/one", memory_id="own00001", path=str(one / "a.py"))
    monkeypatch.chdir(one)
    return one


class TestTheRekeyRefusalContract:
    """A defaulted `--to` moves only this checkout's own, non-excluded bucket.

    Catches the printed remedy moving another checkout's live bucket into
    this repository's id: the gate excluded it, the command did not ask.
    """

    def test_an_excluded_bucket_with_a_defaulted_target_is_refused(
        self, excluded_rig, store, capfd
    ):
        before = _snapshot(store)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", "--apply", "--db-path", str(store)],
            capfd,
        )
        assert code == 1
        assert envelope["error"] == "REKEY_REFUSED"
        assert _snapshot(store) == before

    def test_the_same_move_with_an_explicit_target_applies(
        self, excluded_rig, store, capfd
    ):
        code, envelope = _cli(
            [
                "project-id", "rekey", "--from=proj", "--to=github.com/o/two",
                "--apply", "--db-path", str(store),
            ],
            capfd,
        )
        assert code == 0, envelope
        result = envelope["result"]
        assert result["memories"] == 1
        assert result["target_defaulted"] is False
        assert result["legacy_verdict"] == "excluded"

    def test_a_canonical_source_with_a_defaulted_target_is_refused(
        self, excluded_rig, store, capfd
    ):
        _seed(store, "github.com/o/other", memory_id="other0001", path="x.py")
        before = _snapshot(store)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=github.com/o/other", "--apply",
             "--db-path", str(store)],
            capfd,
        )
        assert code == 1 and envelope["error"] == "REKEY_REFUSED"
        assert _snapshot(store) == before

    def test_a_bucket_that_is_not_the_candidate_with_a_defaulted_target_is_refused(
        self, excluded_rig, store, capfd
    ):
        _seed(store, "foreign", memory_id="foreign01", path="y.py")
        before = _snapshot(store)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=foreign", "--apply", "--db-path", str(store)],
            capfd,
        )
        assert code == 1 and envelope["error"] == "REKEY_REFUSED"
        assert _snapshot(store) == before

    def test_no_surface_renders_a_command_for_an_excluded_bucket(
        self, excluded_rig, store, capfd, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            code, show = _cli(["project-id", "show", "--db-path", str(store)], capfd)
            assert code == 0
            code, status = _cli(["status", "--db-path", str(store)], capfd)
            assert code == 0
        assert show["result"]["legacy_scope"]["excluded"] is True
        surfaces = [json.dumps(show), json.dumps(status)] + [
            record.getMessage() for record in caplog.records
        ]
        offenders = [text for text in surfaces if "--from=proj" in text]
        assert offenders == []
        rows = {row["id"]: row for row in show["result"]["candidates"]}
        assert rows["proj"]["relation"] == "legacy_candidate"
        assert rows["proj"]["rekey"] is None
        assert rows["github.com/o/one"]["relation"] == "current"

    def test_every_rendered_command_names_its_target(self, identity_env, monkeypatch):
        from scripts import memory_api

        commands = [
            memory_api.render_rekey_command("a", "github.com/o/a"),
            memory_api.render_rekey_command("a", "github.com/o/a", db_path="/s.db"),
        ]
        for command in commands:
            assert "--to=github.com/o/a" in command and "--from=a" in command


class TestAPrintedCommandRuns:
    """A printed rekey command, pasted as printed, runs.

    Catches an id beginning with `-` reaching argparse as an option, so the
    remedy exits 2 'expected one argument' for a legal folder name.
    """

    @pytest.mark.parametrize("name", ["-weird", "--to"])
    def test_a_dash_led_bucket_id_round_trips(
        self, identity_env, store, monkeypatch, capfd, name
    ):
        import os
        import shlex
        import subprocess
        import sys

        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory_api._UNPROVEN_WARNED.clear()
        repo = make_repo(identity_env / name, remote="https://github.com/o/dash.git")
        _seed(store, name, memory_id="dash0001", path="rel.py")
        monkeypatch.chdir(repo)
        code, show = _cli(["project-id", "show", "--db-path", str(store)], capfd)
        assert code == 0
        command = show["result"]["legacy_scope"]["rekey"]
        assert command and "--to=" in command
        argv = shlex.split(command)
        argv[0] = sys.executable
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(repo), timeout=120,
            env=dict(os.environ),
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["result"]["dry_run"] is True


class TestNoRekeyRefusalRendersAnAngleBracket:
    """Every rekey refusal, and the show and status remedies, print as typed.

    Catches a placeholder written with angle brackets: the envelope escaper
    renders `<` as `<U+003C>`, so `--to=<canonical id>` reached the operator
    as `--to=<U+003C>canonical id>`. The inputs below carry no `<`, so any
    `<` or marker in the output came from the message text itself.
    """

    @staticmethod
    def _rendered(argv, capfd):
        from scripts.cli import main

        capfd.readouterr()
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        captured = capfd.readouterr()
        return exit_info.value.code, captured.out + captured.err

    def _assert_plain(self, rendered):
        """No marker anywhere; no `<` in any error message. Returns the
        number of error envelopes read, so a caller can require one."""
        assert "U+003C" not in rendered, rendered
        errors = 0
        for line in rendered.splitlines():
            if line.startswith('{"ok": false'):
                errors += 1
                assert "<" not in json.loads(line)["message"], line
        return errors

    @pytest.mark.parametrize("args, code, error", [
        (["--candidates", "--apply"], 2, "INVALID_INPUT"),
        (["--from=proj", "--dry-run", "--apply"], 2, "INVALID_INPUT"),
        ([], 2, "INVALID_INPUT"),
        (["--from=proj", "--to=proj"], 2, "INVALID_INPUT"),
        (["--from=proj"], 1, "REKEY_REFUSED"),
        (["--from=github.com/o/other"], 1, "REKEY_REFUSED"),
        (["--from=foreign"], 1, "REKEY_REFUSED"),
    ], ids=["candidates-with-option", "dry-run-and-apply", "no-from",
            "non-canonical-to", "excluded", "canonical-source", "not-candidate"])
    def test_a_refusal_in_a_repository(self, excluded_rig, store, capfd, args, code, error):
        _seed(store, "github.com/o/other", memory_id="other0001", path="x.py")
        _seed(store, "foreign", memory_id="foreign01", path="y.py")
        got, rendered = self._rendered(
            ["project-id", "rekey", *args, "--db-path", str(store)], capfd
        )
        assert got == code, rendered
        assert f'"error": "{error}"' in rendered, rendered
        assert self._assert_plain(rendered) == 1

    @pytest.mark.parametrize("remote", [None, UNREPRESENTABLE], ids=["no-repository", "declined"])
    def test_the_unresolved_target_refusal(self, identity_env, store, monkeypatch, capfd, remote):
        if remote is None:
            where = identity_env / "proj"
            where.mkdir()
        else:
            where = make_repo(identity_env / "proj", remote=remote)
        monkeypatch.chdir(where)
        got, rendered = self._rendered(
            ["project-id", "rekey", "--from=proj", "--db-path", str(store)], capfd
        )
        assert got == 1, rendered
        assert "PROJECT_IDENTITY_UNRESOLVED" in rendered
        assert "--to=host/owner/repo" in rendered, rendered
        assert self._assert_plain(rendered) == 1

    @pytest.mark.parametrize("command", [["project-id", "show"], ["status"]])
    def test_the_show_and_status_remedies_for_an_excluded_bucket(
        self, excluded_rig, store, capfd, command
    ):
        got, rendered = self._rendered([*command, "--db-path", str(store)], capfd)
        assert got == 0, rendered
        assert self._assert_plain(rendered) == 0

    @pytest.mark.parametrize("command", [["project-id", "show"], ["status"]])
    def test_the_show_and_status_remedies_for_a_declined_repository(
        self, identity_env, store, monkeypatch, capfd, caplog, command
    ):
        """The remedy is the decline warning; the envelope carries its cause."""
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=UNREPRESENTABLE))
        got, rendered = self._rendered([*command, "--db-path", str(store)], capfd)
        warned = "\n".join(caplog.messages)
        assert got == 0, rendered
        assert "origin_unrepresentable" in rendered, rendered
        assert "cannot be represented" in warned, warned
        assert self._assert_plain(rendered + warned) == 0


def _rendered_help() -> dict:
    """Every parser's `format_help()`, keyed by its command path, with the
    wrapping argparse adds collapsed to single spaces."""
    import argparse

    from scripts.cli import build_parser

    found = {}

    def walk(parser, path):
        found[path] = " ".join(parser.format_help().split())
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    walk(child, f"{path} {name}".strip())

    walk(build_parser(), "")
    return found


class TestTheHelpNeverSaysFollowTheCandidate:
    """The rendered help gives the skill's rekey rule, not "follow the candidate".

    Catches help text telling an operator to follow whatever bucket `show`
    lists, which can be an excluded bucket or another repository's id, and a
    defaulted `--to` described as always safe.
    """

    def test_no_help_says_follow_the_candidate(self):
        rendered = _rendered_help()
        assert {"sync", "project-id show", "project-id rekey"} <= set(rendered)
        for path, text in rendered.items():
            assert "follow the candidate" not in text.lower(), path

    @pytest.mark.parametrize("path", ["sync", "project-id rekey"])
    def test_the_rekey_help_requires_approval_and_names_the_limits(self, path):
        text = _rendered_help()[path]
        assert "--apply only with the user's explicit approval" in text, text
        assert "never for an excluded bucket or another repository's id" in text, text

    def test_the_target_defaults_only_for_this_checkouts_own_bucket(self):
        text = _rendered_help()["project-id rekey"]
        assert "Defaults to the detected canonical id" not in text, text
        assert "it defaults only for this checkout's own legacy bucket" in text, text


class TestTheSyncHelpMatchesWhatSyncReports:
    """Each outcome the `sync` help states is the status `sync` reports.

    Catches help that names the wrong status for a case: an operator reading
    `empty` as "no CLAUDE.md" (or `unresolved` as "no identity") looks for the
    wrong fault.
    """

    EMPTY_SENTENCE = (
        "When project identity is unresolved or the project has no memories, "
        "sync reports sync_status 'empty' and leaves the file untouched."
    )
    UNRESOLVED_SENTENCE = (
        "When the project has memories but no CLAUDE.md resolves, sync "
        "reports 'unresolved' and writes nothing."
    )

    def _sync(self, where, store, capfd, *, row_bucket=None, claude_md=True):
        from scripts import memory_api

        for memo in (memory_api._LEGACY_SCOPE_CACHE, memory_api._EXCLUSION_WARNED):
            memo.clear()
        if row_bucket is not None:
            _seed(store, row_bucket, memory_id="row000001", path="a.py")
        target = where / "CLAUDE.md"
        if claude_md:
            target.write_text("# project\n", encoding="utf-8")
        before = target.read_bytes() if claude_md else None
        code, envelope = _cli(
            ["sync", "--db-path", str(store), "--claude-md-root", str(where)], capfd
        )
        assert code == 0, envelope
        after = target.read_bytes() if target.exists() else None
        return envelope["result"], before == after

    @pytest.mark.parametrize("remote", [UNREPRESENTABLE, None], ids=["unresolved-identity", "no-memories"])
    def test_empty_is_an_unresolved_identity_or_no_memories(
        self, identity_env, store, monkeypatch, capfd, remote
    ):
        assert self.EMPTY_SENTENCE in _rendered_help()["sync"]
        where = make_repo(
            identity_env / "proj", remote=remote or "https://github.com/o/proj.git"
        )
        monkeypatch.chdir(where)
        # The unresolved identity holds a row too: `empty` is about the
        # identity there, not about the store.
        result, untouched = self._sync(
            where, store, capfd, row_bucket="proj" if remote else None
        )
        assert result["sync_status"] == "empty"
        assert (result["project_id"] is None) == (remote is not None)
        assert untouched

    def test_unresolved_is_memories_with_no_claude_md(
        self, identity_env, store, monkeypatch, capfd
    ):
        assert self.UNRESOLVED_SENTENCE in _rendered_help()["sync"]
        where = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.chdir(where)
        result, untouched = self._sync(
            where, store, capfd, row_bucket="github.com/o/proj", claude_md=False
        )
        assert result["sync_status"] == "unresolved"
        assert untouched and not (where / "CLAUDE.md").exists()

    def test_the_same_project_writes_with_a_claude_md(
        self, identity_env, store, monkeypatch, capfd
    ):
        """Control: the row and the identity above are writable."""
        where = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.chdir(where)
        result, untouched = self._sync(where, store, capfd, row_bucket="github.com/o/proj")
        assert result["sync_status"] == "wrote" and result["projected"] == 1
        assert not untouched


def _not_a_database(path: Path, kind: str) -> Path:
    """A present store file sqlite cannot read: random bytes, or a real
    store whose pages past the header were overwritten."""
    if kind == "garbage":
        path.write_bytes(b"not a sqlite store, " * 8)
        return path
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE memories (id TEXT)")
    conn.executemany(
        "INSERT INTO memories VALUES (?)", [(str(i) * 50,) for i in range(200)]
    )
    conn.commit()
    conn.close()
    data = bytearray(path.read_bytes())
    data[100:4096] = b"\xff" * (4096 - 100)
    path.write_bytes(bytes(data))
    return path


class TestAStoreThatIsNotADatabaseNamesItsRemedy:
    """Each read-only verb names the remedy for a store sqlite cannot read.

    Catches the bare `SYSTEM_ERROR: file is not a database` (or `database
    disk image is malformed`): the operator sees sqlite's words and no check
    to make, while an unopenable store already names one.
    """

    @pytest.mark.parametrize("kind", ["garbage", "corrupt"])
    @pytest.mark.parametrize("argv, code, error", [
        (["project-id", "show"], 2, "SYSTEM_ERROR"),
        (["status"], 2, "SYSTEM_ERROR"),
        (["project-id", "rekey", "--candidates"], 2, "SYSTEM_ERROR"),
        (["project-id", "rekey", "--from=proj", "--to=github.com/o/p"], 3, "REKEY_FAILED"),
    ], ids=["show", "status", "candidates", "rekey-dry-run"])
    @pytest.mark.parametrize("where", ["no-repository", "repository"])
    def test_the_refusal_names_the_check(
        self, identity_env, monkeypatch, capfd, argv, code, error, kind, where
    ):
        """In a repository the legacy-bucket probe reads the store first."""
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        store = _not_a_database(identity_env / "store.db", kind)
        before = store.read_bytes()
        monkeypatch.chdir(
            make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
            if where == "repository"
            else identity_env
        )
        got, envelope = _cli([*argv, "--db-path", str(store)], capfd)
        assert got == code, envelope
        assert envelope["error"] == error
        message = envelope["message"]
        assert "exists but cannot be opened" in message, message
        assert "names a PACT memory store" in message, message
        assert "no memory was written to it" in message, message
        assert store.read_bytes() == before


def _page_corrupt_store(path: Path, journal: str = "wal") -> Path:
    """A store with the real schema whose MIDDLE page is overwritten.

    The schema page is intact, so the store opens and a narrow query can
    still answer; only a read that reaches the overwritten page fails.
    `journal="delete"` leaves it in rollback-journal mode, as an older store
    may be.
    """
    conn = sqlite3.connect(str(path))
    create_test_schema(conn)
    conn.executemany(
        "INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, ?, 'github.com/o/proj', 's', ?, ?)",
        [(f"m{i:05d}", "x" * 300, CREATED_AT, CREATED_AT) for i in range(400)],
    )
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if journal == "delete":
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    data = bytearray(path.read_bytes())
    page = 4096
    middle = (len(data) // page // 2) * page
    data[middle:middle + page] = bytes((i * 37 + 11) % 256 for i in range(page))
    path.write_bytes(bytes(data))
    return path


class TestStatusNeverWritesIntoACorruptStore:
    """`status` on a page-corrupt store names the remedy and writes nothing.

    Catches the embedding catch-up inserting vectors into a store whose
    pending query still answers although its pages are corrupt, and the bare
    sqlite text in place of the store remedy.
    """

    @pytest.fixture
    def catchup(self, tmp_path, monkeypatch):
        """Let the catch-up run once in this test, with one pending memory,
        and record each embedding instead of loading a model. The recorder
        writes a row, as an embedding does."""
        from scripts import embedding_catchup, memory_init

        monkeypatch.setattr(
            memory_init, "_get_embedding_attempted_path",
            lambda: tmp_path / "catchup-attempted",
        )
        monkeypatch.setattr(embedding_catchup, "get_available_ram_mb", lambda: 1e9)
        monkeypatch.setattr(
            embedding_catchup, "get_unembedded_memories",
            lambda project_id=None, limit=20: ["m00001"],
        )
        embedded = []

        def record(memory_id):
            from scripts.config import resolve_db_path

            embedded.append(memory_id)
            conn = sqlite3.connect(str(resolve_db_path()))
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES ('f-x', 'x.py', 'p')"
            )
            conn.commit()
            conn.close()
            return True

        monkeypatch.setattr(embedding_catchup, "embed_single_memory", record)
        return embedded

    def _status(self, store, capfd):
        from scripts.cli import main

        capfd.readouterr()
        with pytest.raises(SystemExit) as exit_info:
            main(["status", "--db-path", str(store)])
        captured = capfd.readouterr()
        return exit_info.value.code, captured.out + captured.err

    def test_a_corrupt_store_is_left_byte_identical(
        self, identity_env, monkeypatch, capfd, catchup
    ):
        store = _page_corrupt_store(identity_env / "store.db")
        before = store.read_bytes()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote="https://github.com/o/proj.git"))
        code, rendered = self._status(store, capfd)
        assert catchup == [], "the catch-up embedded into a corrupt store"
        assert store.read_bytes() == before
        assert code == 2, rendered
        assert "exists but cannot be opened" in rendered, rendered
        assert "names a PACT memory store" in rendered, rendered

    # The header fields a read-write open may rewrite: the file-format
    # versions (WAL), the change counter and the version-valid-for number.
    HEADER_FIELDS_AN_OPEN_REWRITES = {18, 19, *range(24, 28), *range(92, 100)}

    def test_a_rollback_journal_store_keeps_every_page_and_says_so(
        self, identity_env, monkeypatch, capfd, catchup
    ):
        """Opening a rollback-journal store switches it to WAL in the header,
        so the remedy may only claim what holds: no memory was written."""
        store = _page_corrupt_store(identity_env / "store.db", journal="delete")
        before = store.read_bytes()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote="https://github.com/o/proj.git"))
        code, rendered = self._status(store, capfd)
        after = store.read_bytes()
        assert catchup == []
        assert code == 2, rendered
        assert "no memory was written to it" in rendered, rendered
        assert "not modified" not in rendered, rendered
        assert len(after) == len(before)
        assert after[100:] == before[100:], "a page past the header changed"
        changed = {i for i in range(100) if after[i] != before[i]}
        assert changed <= self.HEADER_FIELDS_AN_OPEN_REWRITES, sorted(changed)

    def test_a_check_that_reports_rows_instead_of_raising_stops_the_catch_up(
        self, identity_env, monkeypatch, capfd, catchup
    ):
        """`quick_check` RETURNS rows, rather than raising, for a store whose
        pages read but whose structure is wrong (here a freelist count), so
        the rows are the only signal and must stop the catch-up."""
        import struct

        store = identity_env / "store.db"
        conn = sqlite3.connect(str(store))
        create_test_schema(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
            "VALUES ('m00001', 'c', 'github.com/o/proj', 's', ?, ?)",
            (CREATED_AT, CREATED_AT),
        )
        conn.commit()
        conn.close()
        data = bytearray(store.read_bytes())
        data[36:40] = struct.pack(">I", 999)
        store.write_bytes(bytes(data))
        check = sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = check.execute("PRAGMA quick_check").fetchall()
        finally:
            check.close()
        assert rows != [("ok",)], "precondition: the check must report rows"
        monkeypatch.chdir(make_repo(identity_env / "proj", remote="https://github.com/o/proj.git"))
        self._status(store, capfd)
        assert catchup == [], "the catch-up embedded into a store that failed its check"

    @pytest.mark.parametrize("journal", ["wal", "delete"])
    def test_the_real_pending_query_writes_no_schema(
        self, identity_env, monkeypatch, capfd, journal
    ):
        """Unpatched: the real pending query and the real integrity check run.

        A store without a vector table is the shape where the pending query's
        connection used to CREATE vec_memories before the check.
        """
        pytest.importorskip("sqlite_vec")
        from scripts import database, embedding_catchup, memory_init

        assert database.SQLITE_EXTENSIONS_ENABLED, "no extension loading: nothing to prove"
        monkeypatch.setattr(
            memory_init, "_get_embedding_attempted_path",
            lambda: identity_env / "catchup-attempted",
        )
        monkeypatch.setattr(embedding_catchup, "get_available_ram_mb", lambda: 1e9)
        queried = []
        real_query = embedding_catchup.get_unembedded_memories

        def recording_query(*args, **kwargs):
            result = real_query(*args, **kwargs)
            queried.append(list(result))
            return result

        monkeypatch.setattr(embedding_catchup, "get_unembedded_memories", recording_query)
        store = _page_corrupt_store(identity_env / "store.db", journal=journal)
        before = store.read_bytes()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote="https://github.com/o/proj.git"))
        code, rendered = self._status(store, capfd)
        after = store.read_bytes()
        assert queried and queried[0], "the real pending query found no work"
        assert code == 2, rendered
        assert "no memory was written to it" in rendered, rendered
        if journal == "wal":
            assert after == before
        else:
            assert after[100:] == before[100:] and len(after) == len(before)

    def test_an_intact_store_still_gets_its_catch_up(
        self, identity_env, monkeypatch, capfd, catchup
    ):
        """Control: the integrity check passes and the pending memory embeds."""
        store = identity_env / "store.db"
        conn = sqlite3.connect(str(store))
        create_test_schema(conn)
        conn.close()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote="https://github.com/o/proj.git"))
        code, rendered = self._status(store, capfd)
        assert code == 0, rendered
        assert catchup == ["m00001"]


# Each read-only verb, with the exit code its store fault reports.
STORE_FAULT_COMMANDS = {
    "show": (["project-id", "show"], 2),
    "status": (["status"], 2),
    "candidates": (["project-id", "rekey", "--candidates"], 2),
    "rekey-dry-run": (["project-id", "rekey", "--from=proj", "--to=github.com/o/p"], 3),
    "list": (["list"], 2),
    "search": (["search", "anything"], 2),
}
# A page-corrupt store faults only where a read reaches the overwritten page;
# these are the verbs that do, from inside the repository.
CORRUPT_STORE_FAULTS = ("list", "search", "status")


class TestAStoreFaultNamesItsRemedyOnce:
    """Every store-fault message carries the remedy exactly once.

    Catches a wrapper that meets an error whose text already IS the remedy
    and builds it again, so the operator reads "exists but cannot be opened
    (... exists but cannot be opened (...) ...)".
    """

    @staticmethod
    def _store(path: Path, kind: str) -> Path:
        if kind == "garbage":
            return _not_a_database(path, "garbage")
        if kind == "corrupt":
            return _page_corrupt_store(path)
        conn = sqlite3.connect(str(path))
        create_test_schema(conn)
        conn.close()
        path.chmod(0)
        return path

    @pytest.mark.parametrize(
        "kind, command, where",
        [
            # The mode-000 store is unreadable only to a non-root process.
            pytest.param(
                kind, command, where,
                marks=[pytest.mark.requires_non_root] if kind == "mode000" else [],
            )
            for kind in ("garbage", "mode000")
            for command in STORE_FAULT_COMMANDS
            for where in ("repository", "outside")
        ]
        + [("corrupt", command, "repository") for command in CORRUPT_STORE_FAULTS],
    )
    def test_the_remedy_appears_once(
        self, identity_env, monkeypatch, capfd, kind, command, where
    ):
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        store = self._store(identity_env / "store.db", kind)
        monkeypatch.chdir(
            make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
            if where == "repository"
            else identity_env
        )
        argv, code = STORE_FAULT_COMMANDS[command]
        try:
            got, envelope = _cli([*argv, "--db-path", str(store)], capfd)
        finally:
            store.chmod(0o600)
        assert got == code, envelope
        assert envelope["message"].count("exists but cannot be opened") == 1, envelope


class TestRekeyInputAndStoreEdges:
    def test_candidates_takes_no_other_option(self, identity_env, store, capfd):
        code, envelope = _cli(
            ["project-id", "rekey", "--candidates", "--apply", "--db-path", str(store)],
            capfd,
        )
        assert code == 2 and envelope["error"] == "INVALID_INPUT"

    def test_an_absent_source_bucket_warns_and_succeeds(
        self, excluded_rig, store, capfd
    ):
        from scripts.cli import main

        with pytest.raises(SystemExit) as exit_info:
            main([
                "project-id", "rekey", "--from=nosuch", "--to=github.com/o/one",
                "--db-path", str(store),
            ])
        captured = capfd.readouterr()
        assert exit_info.value.code == 0
        assert json.loads(captured.out)["result"]["from_bucket_empty"] is True
        assert "no rows are filed under --from='nosuch'" in captured.err

    def test_a_dry_run_against_an_absent_store_creates_nothing(
        self, identity_env, monkeypatch, capfd
    ):
        memory_dir = identity_env / "user-memory"
        memory_dir.mkdir()
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        repo = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.chdir(repo)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", "--to=github.com/o/proj"], capfd
        )
        assert code == 0, envelope
        assert envelope["result"]["store_absent"] is True
        assert list(memory_dir.iterdir()) == []

    def test_an_applied_move_drops_the_cached_scope(
        self, identity_env, store, monkeypatch, capfd
    ):
        from scripts import memory_api
        from scripts.memory_api import PACTMemory
        from fixtures.identity_git import seed_session_evidence

        memory_api._LEGACY_SCOPE_CACHE.clear()
        repo = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        _seed(store, "proj", memory_id="legacy01", path="rel.py")
        seed_session_evidence(identity_env / "home" / ".claude", repo, "legacy-session")
        monkeypatch.chdir(repo)
        # A constant stamp: only the explicit clear can drop the cached scope.
        monkeypatch.setattr(memory_api, "_store_stat_key", lambda store: ("fixed",))
        assert PACTMemory(db_path=store)._read_project_scopes() == (
            "github.com/o/proj", "proj",
        )
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", "--apply", "--db-path", str(store)],
            capfd,
        )
        assert code == 0, envelope
        assert PACTMemory(db_path=store)._read_project_scopes() == ("github.com/o/proj",)


# ---------------------------------------------------------------------------
# sync reports its read scope, and the secretary counts across it
# ---------------------------------------------------------------------------

SECRETARY = Path(__file__).resolve().parents[1] / "agents" / "pact-secretary.md"

# The single-id form the secretary used before it read the scope: the inverse
# control, so the arm below proves the scope is what reaches both buckets.
SINGLE_ID_SQL = """
SELECT COUNT(*) FROM memories
WHERE project_id = :project_id
  AND EXISTS (
    SELECT 1 FROM json_each(COALESCE(memories.entities,'[]')) je
    WHERE (je.type='object' AND json_extract(je.value,'$.name') = :name)
       OR (je.type='text'   AND je.value = :name))
"""


def _secretary_calibration_sql() -> str:
    """The calibration count exactly as the secretary agent file states it."""
    text = SECRETARY.read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", text, re.S)
    matches = [block for block in blocks if "json_each(COALESCE" in block]
    assert len(matches) == 1, matches
    return matches[0]


class TestSyncReportsItsReadScope:
    """`sync` names every bucket this session reads, and the count uses it.

    Catches the secretary's calibration count reading only the canonical id
    while the session also reads an admitted legacy bucket, so records filed
    before the upgrade stop counting toward the gate.
    """

    def test_the_scope_and_the_secretary_count_cover_both_buckets(
        self, identity_env, store, monkeypatch, capfd
    ):
        from fixtures.identity_git import seed_session_evidence
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        repo = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        (repo / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        entity = json.dumps([{"name": "orchestration_calibration"}])
        conn = sqlite3.connect(str(store))
        for memory_id, project in (("canon001", "github.com/o/proj"), ("legacy01", "proj")):
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, entities, created_at, updated_at) "
                "VALUES (?, 'c', ?, 'legacy-session', ?, ?, ?)",
                (memory_id, project, entity, CREATED_AT, CREATED_AT),
            )
        conn.commit()
        conn.close()
        seed_session_evidence(identity_env / "home" / ".claude", repo, "legacy-session")
        monkeypatch.chdir(repo)
        code, envelope = _cli(
            ["sync", "--db-path", str(store), "--claude-md-root", str(repo)], capfd
        )
        assert code == 0, envelope
        read_scope = envelope["result"]["read_scope"]
        assert read_scope == ["github.com/o/proj", "proj"]

        conn = sqlite3.connect(str(store))
        try:
            counted = conn.execute(
                _secretary_calibration_sql(),
                {"name": "orchestration_calibration", "scope": json.dumps(read_scope)},
            ).fetchone()[0]
            single = conn.execute(
                SINGLE_ID_SQL,
                {"name": "orchestration_calibration", "project_id": "github.com/o/proj"},
            ).fetchone()[0]
        finally:
            conn.close()
        assert counted == 2
        assert single == 1

    def test_no_identity_is_an_empty_scope(self, identity_env, store, monkeypatch, capfd):
        from scripts import memory_api
        from scripts.memory_api import PACTMemory

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: memory_api._DetectedIdentity(
                    project_id=None, source="unresolved", legacy_basename=None,
                    override_ignored=None, identity_root=None,
                )
            ),
        )
        code, envelope = _cli(["sync", "--db-path", str(store)], capfd)
        assert code == 0, envelope
        assert envelope["result"]["read_scope"] == []


class TestAZeroByteStoreDegrades:
    """A store file with no schema is reported, never initialised or crashed.

    Catches a read primitive reached before the schema check: with the
    primitives free of any initialiser, a 0-byte store would raise "no such
    table" from `show` or `search` instead of degrading.
    """

    def _zero_byte(self, identity_env, monkeypatch):
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        repo = make_repo(identity_env / "proj", remote="https://github.com/o/proj.git")
        monkeypatch.chdir(repo)
        store = identity_env / "zero.db"
        store.write_bytes(b"")
        return store

    def test_show_on_a_zero_byte_store(self, identity_env, monkeypatch, capfd):
        store = self._zero_byte(identity_env, monkeypatch)
        code, envelope = _cli(["project-id", "show", "--db-path", str(store)], capfd)
        assert code == 0, envelope
        assert envelope["result"]["candidates"] == []
        assert store.read_bytes() == b""

    def test_search_on_a_zero_byte_store(self, identity_env, monkeypatch, capfd):
        store = self._zero_byte(identity_env, monkeypatch)
        from scripts.cli import main

        with pytest.raises(SystemExit) as exit_info:
            main(["search", "anything", "--db-path", str(store)])
        captured = capfd.readouterr()
        assert "no such table" not in captured.err + captured.out
        assert exit_info.value.code == 0, captured.err
        assert json.loads(captured.out)["result"] == []
        assert store.read_bytes() == b""


class TestARedirectsRemedyIsItsMarker:
    """A forged `.git` pointer's remedy names the marker, never a pin.

    Catches the refusal printing the TARGET repository's override path as
    the fix: following it leaves the forged checkout declined and re-pins
    the victim repository and every genuine checkout of it.
    """

    def test_following_the_printed_remedy_leaves_the_victim_unchanged(
        self, identity_env, store, monkeypatch, capfd
    ):
        from fixtures.identity_git import make_worktree, run_git
        from scripts.memory_api import PACTMemory

        source = make_repo(identity_env / "subsrc")
        superproject = make_repo(identity_env / "super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "vendor/lib", cwd=superproject,
        )
        lib = superproject / "vendor" / "lib"
        run_git("remote", "set-url", "origin", "https://github.com/victim/lib.git", cwd=lib)
        libwt = make_worktree(lib, identity_env / "libwt")
        admin_root = superproject / ".git" / "modules" / "vendor" / "lib"
        forged = identity_env / "forged"
        forged.mkdir()
        (forged / ".git").write_text(
            f"gitdir: {admin_root / 'worktrees' / 'libwt'}\n", encoding="utf-8"
        )

        def victim_ids():
            ids = []
            for checkout in (lib, libwt):
                monkeypatch.chdir(checkout)
                ids.append(PACTMemory._detect_project_identity_with_legacy().project_id)
            return ids

        before = victim_ids()
        assert before == ["github.com/victim/lib", "github.com/victim/lib"]

        monkeypatch.chdir(forged)
        code, envelope = _cli(
            ["save", json.dumps({"context": "c"}), "--db-path", str(store)], capfd
        )
        assert code == 1
        message = envelope["message"]
        assert str(admin_root) not in message
        assert "`.git` file" in message
        code, show = _cli(["project-id", "show", "--db-path", str(store)], capfd)
        assert show["result"]["decline_reason"]["reason"] == "gitfile_redirect"
        assert show["result"]["decline_reason"]["override_path"] is None

        # Follow the remedy the refusal printed: remove the forged marker.
        (forged / ".git").unlink()
        assert victim_ids() == before
        assert not (admin_root / "pact-project-id").exists()


class TestTheRekeyRefusalOffersOnlyAWorkablePin:
    """With no canonical identity, `rekey` names the remedy that works here.

    Catches the refusal telling an operator in a non-repository directory to
    write a `<git-common-dir>/pact-project-id` pin: there is no repository,
    so there is no such file to write, and the only remedy is `--to=`.
    """

    def test_a_non_repository_directory_is_told_to_pass_to(
        self, identity_env, store, monkeypatch, capfd
    ):
        plain = identity_env / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=plain", "--db-path", str(store)], capfd
        )
        assert code == 1
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED"
        message = envelope["message"]
        assert "--to=" in message
        assert "pact-project-id" not in message and "git-common-dir" not in message

    def test_a_declining_repository_is_offered_its_own_override_file(
        self, identity_env, store, monkeypatch, capfd
    ):
        repo = make_repo(identity_env / "declined", remote=UNREPRESENTABLE)
        monkeypatch.chdir(repo)
        code, envelope = _cli(
            ["project-id", "rekey", "--from=declined", "--db-path", str(store)], capfd
        )
        assert code == 1
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED"
        assert str(repo / ".git" / "pact-project-id") in envelope["message"]
