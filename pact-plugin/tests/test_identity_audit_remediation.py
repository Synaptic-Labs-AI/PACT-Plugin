"""
Audit remediation for the repository-identity change.

Four arms, one per defect the closing audit found in the shipped code:

- the rekey target must be CANONICAL, because moving rows into a basename
  bucket is the misfile the command exists to prevent;
- the save envelope's legacy-candidate disclosure must carry the candidate's
  memory AND file counts and the literal command, not just its id;
- a rendered command carries a STORE-INFLUENCED bucket id, so it must be
  shell-quoted;
- the override reader must not allocate an arbitrarily large file before
  checking its size.

The sync-help arm lives beside its existing pin in
`test_project_dir_resolution.py`, where the phrase it extends is asserted.
"""

import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import project_identity
from scripts.cli import _project_candidates, main
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory, _DetectedIdentity

_LEGACY = "Legacy-Bucket"


def _make_store(store: Path) -> None:
    with db_connection(store) as conn:
        ensure_initialized(conn)


def _seed(store: Path, project_id: str, memory_id: str, path: str = "") -> None:
    """Insert one memory (and optionally one file row) under `project_id`."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, "seed", project_id,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        if path:
            conn.execute(
                "INSERT INTO files (id, path, project_id, last_modified) "
                "VALUES (?, ?, ?, ?)",
                (memory_id[::-1], path, project_id, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()


class TestRekeyTargetMustBeCanonical:
    """A move target that names a folder rather than a repository is refused."""

    def test_a_supplied_non_canonical_target_is_bad_input(
        self, tmp_path, capfd
    ):
        store = tmp_path / "rekey.db"
        _make_store(store)
        with pytest.raises(SystemExit) as refused:
            main([
                "project-id", "rekey",
                "--from", "old-bucket",
                "--to", "not/../../a/canonical",  # contains .., so non-canonical
                "--apply",
                "--db-path", str(store),
            ])
        assert refused.value.code == 2, refused.value.code
        envelope = json.loads(capfd.readouterr().err)
        assert envelope["error"] == "INVALID_INPUT", envelope
        assert "canonical" in envelope["message"]

    def test_no_canonical_identity_refuses_with_exit_1(
        self, tmp_path, capfd, monkeypatch
    ):
        """A detected BASENAME is not a target; the command refuses."""
        store = tmp_path / "rekey-noid.db"
        _make_store(store)
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(_LEGACY, "basename", _LEGACY, None)
            ),
        )
        with pytest.raises(SystemExit) as refused:
            main([
                "project-id", "rekey",
                "--from", "old-bucket",
                "--apply",
                "--db-path", str(store),
            ])
        assert refused.value.code == 1, refused.value.code
        envelope = json.loads(capfd.readouterr().err)
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED", envelope


class TestRenderedRekeyCommandIsQuoted:
    """A bucket id is store-influenced, so it is never rendered raw."""

    def test_a_hostile_bucket_id_round_trips_through_the_rendered_command(
        self, tmp_path
    ):
        store = tmp_path / "candidates.db"
        hostile = "Legacy; rm -rf /tmp/x $(id)"
        _seed(store, hostile, "a" * 32)

        candidates = _project_candidates(store)
        assert len(candidates) == 1, candidates
        entry = candidates[0]
        assert entry["id"] == hostile
        # The rendered command must survive shlex parsing as ONE argument tail.
        argv = shlex.split(entry["rekey"])
        assert argv[:4] == ["pact-memory", "project-id", "rekey", "--from"]
        assert argv[4] == hostile, argv
        # And it must not contain the raw, unquoted id as a command fragment.
        assert f"--from {hostile}" not in entry["rekey"]


class TestSaveEnvelopeDisclosesTheCandidate:
    """Surface 4 carries the candidate's size and the literal command."""

    def test_the_legacy_scope_carries_counts_and_the_command(
        self, tmp_path, monkeypatch
    ):
        store = tmp_path / "surface4.db"
        _make_store(store)
        _seed(store, _LEGACY, "b" * 32, path="relative/linked.py")
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(
                    "host/owner/repo", "canonical", _LEGACY, None
                )
            ),
        )
        with patch("scripts.memory_api._ensure_ready"):
            memory = PACTMemory(session_id="s", db_path=store)
            memory.save({"context": "surface-4"}, sync_to_claude=False)

        scope = memory.last_project_scope
        assert scope is not None
        legacy = scope["legacy_scope"]
        assert legacy["candidate"] == _LEGACY
        assert legacy["memory_count"] == 1, legacy
        # DISTINCT PATHS, via `count_project_file_paths`, not file rows.
        assert legacy["file_paths"] == 1, legacy
        assert legacy["rekey"] == shlex.join(
            ["pact-memory", "project-id", "rekey", "--from", _LEGACY]
        )
        assert legacy["canonical_count"] == 0, (
            "the disclosure exists because the canonical bucket was empty"
        )


class TestOverrideReadIsBounded:
    """A huge override file is classified without being allocated whole."""

    def test_an_oversized_file_reads_as_too_long(self, tmp_path):
        common = tmp_path / "common"
        common.mkdir()
        (common / "pact-project-id").write_bytes(b"a" * 8192)
        read = project_identity.read_override(common)
        assert read.value is None
        assert read.reason == "too_long"

    def test_a_usable_file_still_reads(self, tmp_path):
        """Positive control: the bound must not break the ordinary file."""
        common = tmp_path / "common"
        common.mkdir()
        (common / "pact-project-id").write_text("host/Owner/Repo\n", encoding="utf-8")
        read = project_identity.read_override(common)
        assert read.value == "host/Owner/Repo"
        assert read.reason is None