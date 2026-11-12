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
