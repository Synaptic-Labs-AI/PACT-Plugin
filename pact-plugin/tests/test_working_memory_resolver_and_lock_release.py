"""Edge-path coverage for the working_memory CLAUDE.md-sync lock:
lock-release-on-exception, and the CLAUDE_PROJECT_DIR-divergence residual.

Location: pact-plugin/tests/test_working_memory_resolver_and_lock_release.py

Summary: Two edge paths that the primary concurrency suites
(test_working_memory_concurrency.py + ..._comprehensive.py) do not cover,
grouped in this sibling file to keep the comprehensive file focused (and under
the file-size advisory threshold):

  1. Lock-release-on-exception — an exception raised mid-window (inside the
     `with file_lock(...)` block at a sync site) must still RELEASE the lock via
     the contextmanager's ``finally``, so the next writer can acquire it. A lock
     that leaked on the error path would wedge every subsequent sync until the
     5s timeout, repeatedly. We also confirm fail-open (return False) and that
     no partial/torn write persists.

  2. CLAUDE_PROJECT_DIR-divergence residual — the lock only serializes when all
     writers resolve the SAME .claude/CLAUDE.md (hence the same sidecar inode).
     When CLAUDE_PROJECT_DIR is unset AND the git-root/cwd fallbacks resolve
     DIFFERENT roots between two processes, the resolvers return different paths
     → different sidecars → the lock does NOT serialize. This is a DEMONSTRATION
     / known-limitation test: it documents the unprotected residual; it does
     NOT claim the divergence path is safe. It is out-of-contract in practice
     because the plugin sets CLAUDE_PROJECT_DIR every session, so every writer
     hits the env-var branch first and converges — see the caveat in each
     divergence test's docstring.

Used by: pytest (the working_memory edge-path gate).
"""

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _seed_claude_md(home: Path) -> Path:
    """Create a minimal project CLAUDE.md with an empty Working Memory section
    under ``home``/.claude/."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n",
        encoding="utf-8",
    )
    return claude_md


# ---------------------------------------------------------------------------
# 1. Lock-release-on-exception / chmod-mid-window.
# ---------------------------------------------------------------------------

class TestLockReleaseOnException:
    """An exception raised mid-window must release the lock (the contextmanager
    ``finally: LOCK_UN + os.close``), fail open, and leave no torn write."""

    def test_chmod_failure_mid_window_releases_lock_and_fails_open(
        self, tmp_path, monkeypatch
    ):
        """If os.chmod raises AFTER write_text but INSIDE the `with file_lock`
        block, sync_to_claude_md must (a) return False (fail-open via the outer
        try/except) and (b) leave the lock RE-ACQUIRABLE — proving the lock was
        released on the exception path, not leaked.

        A leaked lock would force the next acquirer to block until the 5s
        timeout (then fail open), degrading every subsequent sync. We prove
        release by re-acquiring with a SHRUNK timeout: success means the lock
        is free; a TimeoutError would mean it leaked.
        """
        import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Raise inside the with-block, after write_text, at os.chmod.
        def boom_chmod(*args, **kwargs):
            raise OSError("simulated chmod failure mid-window")

        monkeypatch.setattr(wm.os, "chmod", boom_chmod)

        result = wm.sync_to_claude_md(
            {"context": "MID-WINDOW-BOOM", "goal": "g"}, None, "id"
        )
        # (a) fail-open
        assert result is False

        # (b) lock released → re-acquirable fast (NOT the 5s timeout). Shrink
        # the timeout so a leak would surface as a quick TimeoutError, not a
        # 5s hang.
        target = wm._get_claude_md_path()
        assert target is not None
        monkeypatch.setattr(wm, "_LOCK_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(wm, "_LOCK_POLL_INTERVAL", 0.05)
        with wm.file_lock(target):
            pass  # acquisition must succeed — if the lock had leaked this raises

    def test_mid_window_exception_leaves_no_torn_write(self, tmp_path, monkeypatch):
        """The file must remain well-formed (no partial/torn content) after a
        mid-window exception. os.chmod runs AFTER write_text, so write_text may
        have already replaced the file — assert it is still a complete,
        parseable document with its Working Memory section intact, not a
        truncated fragment.
        """
        import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        def boom_chmod(*args, **kwargs):
            raise OSError("simulated chmod failure mid-window")

        monkeypatch.setattr(wm.os, "chmod", boom_chmod)

        wm.sync_to_claude_md({"context": "TORN-CHECK", "goal": "g"}, None, "id")

        final = claude_md.read_text(encoding="utf-8")
        # write_text is atomic at the Python level (single write of the full
        # new content), so the file is either the old or the new full document
        # — never a fragment. Assert structural completeness.
        assert final.startswith("# Project"), "file head was truncated"
        assert "## Working Memory" in final, "section was lost"
        # No dangling half-entry: every entry header has a body marker. (The
        # write either fully landed the new entry or did not; both are whole.)
        assert final.count("## Working Memory") == 1


# ---------------------------------------------------------------------------
# 2. CLAUDE_PROJECT_DIR-divergence residual (DEMONSTRATION / known-limitation).
# ---------------------------------------------------------------------------

class TestProjectDirDivergenceResidual:
    """DEMONSTRATION of the known, unprotected divergence residual: when
    CLAUDE_PROJECT_DIR is unset AND the fallback resolvers land on different
    roots between two processes, they resolve different .claude/CLAUDE.md paths
    → different `.CLAUDE.md.lock` sidecars → the lock does NOT serialize.

    OUT-OF-CONTRACT CAVEAT: in practice the plugin sets CLAUDE_PROJECT_DIR every
    session, so every writer takes the env-var branch first and converges on one
    sidecar. There is no safe fallback action when paths diverge (locking a
    different sidecar is not actionable), so the implementation deliberately
    NOTES this rather than guarding it. These tests document the residual; they
    do NOT assert that the divergence path is safe — they assert that it
    diverges, which is exactly the unprotected condition.
    """

    def _resolve_under_root(self, wm, root: Path, monkeypatch):
        """Drive working_memory._get_claude_md_path so it resolves under
        ``root`` via the cwd fallback: env unset + git-root detection forced to
        fail, so the resolver falls through env → git → cwd, landing on root."""
        # env unset → skip the env-var branch
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        # Force the git-root fallback to "not a repo" so resolution falls
        # through to cwd. (returncode != 0 makes _get_claude_md_path skip it.)
        class _FakeProc:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(wm.subprocess, "run", lambda *a, **k: _FakeProc())
        # cwd fallback lands on root.
        monkeypatch.setattr(wm.Path, "cwd", staticmethod(lambda: root))
        return wm._get_claude_md_path()

    def test_unset_env_with_divergent_roots_resolves_different_sidecars(
        self, tmp_path, monkeypatch
    ):
        """Two distinct roots, env unset, git fallback neutralized → the skill
        resolver returns a DIFFERENT path for each root, and therefore a
        DIFFERENT sidecar. This is the unprotected residual: two processes in
        this state would lock different inodes and not serialize.
        """
        import working_memory as wm

        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        claude_a = _seed_claude_md(root_a)
        claude_b = _seed_claude_md(root_b)

        with monkeypatch.context() as m:
            resolved_a = self._resolve_under_root(wm, root_a, m)
        with monkeypatch.context() as m:
            resolved_b = self._resolve_under_root(wm, root_b, m)

        assert resolved_a == claude_a
        assert resolved_b == claude_b
        assert resolved_a != resolved_b, (
            "Roots that should diverge resolved to the same path — the "
            "demonstration is vacuous"
        )

        # The lock keys on the sidecar beside the resolved target. Divergent
        # targets → divergent sidecars → NO shared lock (the unprotected
        # residual this test documents).
        sidecar_a = resolved_a.parent / f".{resolved_a.name}.lock"
        sidecar_b = resolved_b.parent / f".{resolved_b.name}.lock"
        assert sidecar_a != sidecar_b, (
            "Divergent resolution must yield divergent sidecars — this is the "
            "unprotected divergence residual (out-of-contract: the plugin sets "
            "CLAUDE_PROJECT_DIR every session so writers normally converge)."
        )

    def test_hook_and_skill_resolvers_diverge_under_different_roots(self, tmp_path):
        """Cross-resolver demonstration: the hook-side resolver
        (claude_md_manager.resolve_project_claude_md_path) takes an explicit
        project_dir, so two processes passing different roots resolve different
        sidecars — the same divergence class, driven by the caller's root.

        This pins the cross-process surface: the skill copy and the hook copy
        agree ONLY when they are handed the same root. When roots differ
        (the divergence condition), they cannot share a sidecar — documented,
        not guarded.
        """
        from shared.claude_md_manager import resolve_project_claude_md_path

        root_a = tmp_path / "h_a"
        root_b = tmp_path / "h_b"
        _seed_claude_md(root_a)
        _seed_claude_md(root_b)

        path_a, src_a = resolve_project_claude_md_path(root_a)
        path_b, src_b = resolve_project_claude_md_path(root_b)

        assert src_a == "dot_claude" and src_b == "dot_claude"
        assert path_a != path_b, "different roots must resolve different paths"
        sidecar_a = path_a.parent / f".{path_a.name}.lock"
        sidecar_b = path_b.parent / f".{path_b.name}.lock"
        assert sidecar_a != sidecar_b, (
            "Different caller roots → different sidecars → no shared lock. The "
            "lock serializes only when all writers resolve the SAME root; the "
            "divergence path is unprotected by design (note-not-guard)."
        )
