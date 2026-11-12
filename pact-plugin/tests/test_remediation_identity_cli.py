"""CLI contracts: what a refusal names, and what a printed remedy does.

Each class names the behaviour it pins and the defect it would catch. The
fixtures are REAL repositories built from a fresh `git init` with a fake
origin URL, and every store is a scratch file under `tmp_path`.
"""

from __future__ import annotations

import json
import sqlite3

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
