"""The session-evidence check's slug-folder listing is a speedup, not the answer.

Location: pact-plugin/tests/test_remediation_session_evidence_listing.py.
`memory_api._legacy_session_evidence` lists each gate root's session folder
once and treats a listed entry as a live session folder. Only an ABSENT
entry lets the prune ledger stand in for a session. These arms pin that a
folder the listing cannot read changes nothing: the check then falls back to
the per-session existence test, exactly as before the listing existed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fixtures.identity_git import make_repo, seed_session_evidence
from scripts.database import db_connection, ensure_initialized
from shared.pact_context import PRUNE_LEDGER_NAME, reconstruct_session_dir

from scripts import memory_api

REMOTE = "https://github.com/o/proj.git"
SESSION = "live-session"


def _iso(offset_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _rig(tmp_path: Path):
    """A repository, a store whose legacy bucket `proj` was written by
    SESSION, and that session's slug folder (not created yet)."""
    memory_api._PRUNED_SESSION_INDEX.clear()
    repo = make_repo(tmp_path / "proj", remote=REMOTE)
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES (?, 'x', 'proj', ?, ?, ?)",
            ("a" * 32, SESSION, _iso(0), _iso(0)),
        )
        conn.commit()
    slug_dir = Path(reconstruct_session_dir(str(repo), SESSION)).parent
    return repo, store, slug_dir


def _ledger_line(slug_dir: Path, repo: Path) -> None:
    """A ledger line for SESSION that WOULD count as evidence."""
    slug_dir.mkdir(parents=True, exist_ok=True)
    line = {"session_id": SESSION, "project_dir": str(repo), "started_at": _iso(0)}
    (slug_dir / PRUNE_LEDGER_NAME).write_text(json.dumps(line) + "\n", encoding="utf-8")


def _evidence(store: Path, repo: Path) -> bool:
    with db_connection(store) as conn:
        return memory_api._legacy_session_evidence(
            conn, "proj", [str(repo)], repo / ".git"
        )


class TestAnUnlistableSlugFolderIsNotAnEmptyOne:
    """A slug folder that is searchable but not readable keeps its live
    session folders: the ledger does not stand in for them.

    Catches the one-listing presence test reading an unlistable folder as
    empty, so every writing session was treated as reaped and the ledger
    stood in while the live folders existed.
    """

    @staticmethod
    def _unlistable(slug_dir: Path) -> None:
        os.chmod(slug_dir, 0o300)
        with pytest.raises(PermissionError):
            os.listdir(slug_dir)

    @pytest.mark.requires_non_root
    def test_a_live_folder_whose_context_does_not_count_is_not_replaced_by_the_ledger(
        self, tmp_path
    ):
        """The live context started BEFORE the repository existed, so it is
        no evidence; the ledger line for the same session would be. With the
        live folder present, the answer is the live context's: False."""
        repo, store, slug_dir = _rig(tmp_path)
        seed_session_evidence(
            tmp_path / "home" / ".claude", repo, SESSION, started_at=_iso(-3600)
        )
        _ledger_line(slug_dir, repo)
        self._unlistable(slug_dir)
        try:
            assert _evidence(store, repo) is False, (
                "the ledger stood in for a session folder that exists"
            )
        finally:
            os.chmod(slug_dir, 0o700)

    @pytest.mark.requires_non_root
    def test_a_live_folder_whose_context_counts_is_read(self, tmp_path):
        """The live context counts and no ledger line exists: the folder is
        found by the per-session test, so the answer is True."""
        repo, store, slug_dir = _rig(tmp_path)
        seed_session_evidence(tmp_path / "home" / ".claude", repo, SESSION)
        self._unlistable(slug_dir)
        try:
            assert _evidence(store, repo) is True, (
                "a live session folder in an unlistable slug folder was not read"
            )
        finally:
            os.chmod(slug_dir, 0o700)


class TestAnAbsentFolderStillLetsTheLedgerStandIn:
    """Controls for the fast path and the missing cases."""

    def test_a_reaped_session_in_a_listable_folder_uses_its_ledger_line(self, tmp_path):
        repo, store, slug_dir = _rig(tmp_path)
        _ledger_line(slug_dir, repo)
        assert _evidence(store, repo) is True

    def test_a_missing_slug_folder_is_no_evidence_and_no_error(self, tmp_path):
        repo, store, slug_dir = _rig(tmp_path)
        assert not slug_dir.exists()
        assert _evidence(store, repo) is False

    def test_a_slug_path_that_is_a_file_is_no_evidence_and_no_error(self, tmp_path):
        repo, store, slug_dir = _rig(tmp_path)
        slug_dir.parent.mkdir(parents=True, exist_ok=True)
        slug_dir.write_text("not a folder", encoding="utf-8")
        assert _evidence(store, repo) is False
