"""Concurrent-write integrity tests for the working_memory CLAUDE.md sync sites.

Location: pact-plugin/tests/test_working_memory_concurrency.py

Summary: Proves that the file_lock added to sync_to_claude_md /
sync_retrieved_to_claude_md actually serializes the full read-modify-write
window, so two concurrent writers cannot lose-update each other's entry in the
shared project CLAUDE.md. Verification-level for the CODE phase; comprehensive
edge/perf coverage is TEST-phase work.

Used by: pytest (the suite's working_memory concurrency gate).

WHY THIS FILE EXISTS (the gap it closes)
----------------------------------------
working_memory.py does a full-file read -> parse -> mutate -> overwrite of the
project CLAUDE.md at two sites. Pre-fix, both wrote with no lock, so under the
N:1 tmux teammateMode (N agent processes, one team) two writers could each read
the same base, each reconstruct a full new file, and the second overwrite would
silently drop the first writer's just-committed entry (the #877-class lost
update). The fix wraps the ENTIRE read->write window in a file_lock; the
load-bearing property is read-UNDER-lock (a write-only lock would let the 2nd
writer read stale content and re-introduce the clobber).

NON-VACUITY (the load-bearing methodology)
------------------------------------------
The lost-update test below is deterministic, not timing-hopeful: two REAL
processes rendezvous at a multiprocessing barrier so both enter their
read-modify-write window simultaneously, then each writes ONE distinct memory.
Empirically, against an unlocked (reverted) working_memory this loses one
writer's entry on every run; under the lock both entries always survive. The
barrier forces the exact interleave the lock exists to prevent, so a GREEN
result reflects real serialization rather than a window too small to race.

EXECUTION SHAPE / FLAKE AVOIDANCE
---------------------------------
Workers are module-level functions (picklable for the ``spawn`` start method,
which is macOS's default and the most portable). Each worker rebuilds its own
sys.path + CLAUDE_PROJECT_DIR (neither crosses a spawn boundary) and resolves
the same tmp .claude/CLAUDE.md, so all writers contend on one sidecar inode.
"""

import multiprocessing as mp
import os
import sys
from pathlib import Path

import pytest

# spawn is macOS's default and the most portable start method; pin it explicitly
# so the suite behaves identically wherever it runs (fork would inherit parent
# interpreter state and can be flaky under pytest).
_MP = mp.get_context("spawn")

# The scripts dir that holds working_memory.py — re-derived in each spawned
# child because sys.path does not cross the spawn boundary.
_SCRIPTS_DIR = str(
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
)


# ---------------------------------------------------------------------------
# Module-level worker (picklable for the spawn start method).
# ---------------------------------------------------------------------------

def _sync_worker(args):
    """Drive the REAL sync_to_claude_md once, under a barrier so all writers
    enter their read-modify-write window simultaneously and contend for the
    lock. Each writer commits one uniquely-marked memory."""
    home, writer_id, barrier = args
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    # _get_claude_md_path checks CLAUDE_PROJECT_DIR first, so all writers
    # resolve the same tmp .claude/CLAUDE.md (and thus the same sidecar).
    os.environ["CLAUDE_PROJECT_DIR"] = home
    import working_memory as wm

    # Rendezvous: both writers read the same base before either writes.
    barrier.wait()
    wm.sync_to_claude_md(
        {"context": f"CONCURRENT-WRITER-{writer_id}", "goal": f"goal-{writer_id}"},
        None,
        f"concurrency-id-{writer_id}",
    )


def _seed_claude_md(home: Path) -> Path:
    """Create a minimal project CLAUDE.md with an empty Working Memory section."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n",
        encoding="utf-8",
    )
    return claude_md


class TestConcurrentSyncLostUpdate:
    """The lock prevents the #877-class lost update across real processes."""

    def test_two_concurrent_writers_both_entries_survive(self, tmp_path):
        """Two REAL processes that enter their read-modify-write window
        simultaneously must BOTH have their memory in the final file.

        This is the regression the fix exists to prevent: pre-fix (unlocked),
        the barrier-forced interleave drops one writer's entry on every run;
        under the lock the writers serialize and both entries survive. RED on
        reverted code, GREEN with the lock.
        """
        claude_md = _seed_claude_md(tmp_path)
        barrier = _MP.Barrier(2)
        procs = [
            _MP.Process(target=_sync_worker, args=((str(tmp_path), wid, barrier),))
            for wid in (0, 1)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        for p in procs:
            assert p.exitcode == 0, f"worker exited with {p.exitcode}"

        final = claude_md.read_text(encoding="utf-8")
        assert "CONCURRENT-WRITER-0" in final and "CONCURRENT-WRITER-1" in final, (
            "A concurrent writer's entry was lost — the lock did not serialize "
            "the full read-modify-write window. Final Working Memory:\n"
            f"{final[final.find('## Working Memory'):]}"
        )


class TestCrossWriterSerialization:
    """The vendored twin contends on the SAME sidecar inode as the canonical
    lock — the load-bearing cross-process property (duplicated code, one lock)."""

    def test_twin_blocks_while_canonical_holds_same_target(self, tmp_path, monkeypatch):
        """Holding claude_md_manager.file_lock(target) must make
        working_memory.file_lock(target) time out on the SAME target, proving
        the two copies serialize on one sidecar (not two independent locks)."""
        from shared.claude_md_manager import file_lock as canonical
        import working_memory as wm

        target = tmp_path / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# x\n", encoding="utf-8")

        # Shrink the twin's timeout so the test is fast; the canonical holder
        # keeps the lock for the whole window.
        monkeypatch.setattr(wm, "_LOCK_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(wm, "_LOCK_POLL_INTERVAL", 0.05)

        with canonical(target):
            with pytest.raises(TimeoutError):
                with wm.file_lock(target):
                    pass  # pragma: no cover - acquisition must not succeed

    def test_twin_sidecar_path_matches_canonical(self, tmp_path):
        """Both copies derive the sidecar as .{name}.lock beside the target —
        the identity that makes the inode shared across processes."""
        from shared import claude_md_manager as cmm
        import working_memory as wm

        target = tmp_path / "CLAUDE.md"
        target.write_text("# x\n", encoding="utf-8")
        expected = tmp_path / ".CLAUDE.md.lock"

        with wm.file_lock(target):
            assert expected.exists(), "twin did not create the canonical sidecar"
        # Sanity: the canonical lock uses the identical sidecar path.
        with cmm.file_lock(target):
            assert expected.exists()


class TestFailOpenOnTimeout:
    """A lock TimeoutError must fail open (skip the sync, return False) — the
    existing try/except contract, not a hard crash."""

    def test_sync_to_claude_md_returns_false_on_timeout(self, tmp_path, monkeypatch):
        """If file_lock raises TimeoutError, sync_to_claude_md returns False and
        does not raise (next save retries)."""
        import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        from contextlib import contextmanager

        @contextmanager
        def timing_out_lock(target_file):
            raise TimeoutError("simulated contention")
            yield  # pragma: no cover - unreachable

        monkeypatch.setattr(wm, "file_lock", timing_out_lock)

        result = wm.sync_to_claude_md(
            {"context": "should-not-be-written", "goal": "g"}, None, "id"
        )
        assert result is False
        # The sync was skipped — the failing entry must NOT have been written.
        assert "should-not-be-written" not in claude_md.read_text(encoding="utf-8")

    def test_sync_retrieved_to_claude_md_returns_false_on_timeout(
        self, tmp_path, monkeypatch
    ):
        """Same fail-open contract for the retrieved-context sync site."""
        import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        from contextlib import contextmanager

        @contextmanager
        def timing_out_lock(target_file):
            raise TimeoutError("simulated contention")
            yield  # pragma: no cover - unreachable

        monkeypatch.setattr(wm, "file_lock", timing_out_lock)

        result = wm.sync_retrieved_to_claude_md(
            [{"context": "should-not-be-written"}], query="q", scores=[0.9],
            memory_ids=["id"],
        )
        assert result is False
        assert "should-not-be-written" not in claude_md.read_text(encoding="utf-8")
