"""
Tests for the #1504 session-scoped compact-summary path.

The compact summary lives at ``{session_dir}/compact-summary.txt`` when the
frame is identifiable, and degrades LOSS-FREE to the root singleton when it is
not. ``resolve_compact_summary_path`` is TOTAL: one call, degradation inside,
never None — the writer has no fallback branch.

Arms here: the resolver's scoped leg, its two degradation legs, totality on
degenerate inputs, the two-session non-interference classes (one per race
arm), the legacy-drain two-pass demonstration, and the end-to-end degradation
pin. Identity-collapse suppression (a same-session_id teammate PostCompact
writes nothing) is covered by test_role_gate_flip_and_postcompact and
test_postcompact_archive, whose frames carry session_id since #1504.
"""
import io
import json
from unittest.mock import patch

import pytest


class TestResolveCompactSummaryPath:
    """The resolver contract: TOTAL, degradation inside, one filename spelling."""

    def test_scoped_frame_resolves_to_session_dir(self, tmp_path, monkeypatch):
        from shared.pact_context import resolve_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/some/where/my-project")
        path = resolve_compact_summary_path({"session_id": "abc-123"})
        assert path == (
            tmp_path / "pact-sessions" / "my-project" / "abc-123"
            / "compact-summary.txt"
        )

    def test_missing_session_id_degrades_to_root_singleton(self, tmp_path, monkeypatch):
        from shared.constants import get_compact_summary_path
        from shared.pact_context import resolve_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/some/where/my-project")
        assert resolve_compact_summary_path({}) == get_compact_summary_path()

    def test_missing_project_dir_degrades_to_root_singleton(self, tmp_path, monkeypatch):
        from shared.constants import get_compact_summary_path
        from shared.pact_context import resolve_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert resolve_compact_summary_path({"session_id": "abc-123"}) == (
            get_compact_summary_path()
        )

    def test_total_on_degenerate_inputs(self, tmp_path, monkeypatch):
        from shared.constants import get_compact_summary_path
        from shared.pact_context import resolve_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/some/where/my-project")
        expected_root = get_compact_summary_path()
        for degenerate in (None, "", {"session_id": ""}, {"session_id": None}):
            assert resolve_compact_summary_path(degenerate) == expected_root

    def test_session_leg_shares_the_root_filename_spelling(self, tmp_path, monkeypatch):
        from shared.constants import COMPACT_SUMMARY_NAME, get_compact_summary_path
        from shared.pact_context import resolve_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/some/where/my-project")
        scoped = resolve_compact_summary_path({"session_id": "abc-123"})
        assert scoped.name == COMPACT_SUMMARY_NAME == get_compact_summary_path().name


# ---------------------------------------------------------------------------
# Behavior-flip arms: the properties the architecture requires demonstrated
# ---------------------------------------------------------------------------

_SID_A = "aaaaaaaa-1111-0000-0000-000000000000"
_SID_B = "bbbbbbbb-2222-0000-0000-000000000000"
_PROJECT = "/some/where/my-project"


def _run_postcompact_main(frame, monkeypatch, tmp_path):
    """Drive postcompact_archive.main() end-to-end in-process under an
    isolated config root; returns the exit code (always 0 — fail-open)."""
    from postcompact_archive import main

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc:
            main()
    return exc.value.code


class TestTwoSessionWriterNonInterference:
    """Race arm 1: two same-root sessions both compact.

    Sequential interleaving, NO threads — both races are orderings of atomic
    syscalls, and the invariant is WHERE the bytes land, not concurrency.
    DISTINCT sentinel bytes: interference would show as a byte mismatch, not
    merely as existence.
    """

    def test_both_summaries_stage_byte_intact_in_own_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT)
        bodies = ((_SID_A, "SENTINEL-A summary bytes"), (_SID_B, "SENTINEL-B summary bytes"))
        for sid, body in bodies:
            frame = {"session_id": sid, "agent_type": "PACT:pact-orchestrator",
                     "hook_event_name": "PostCompact", "compact_summary": body}
            assert _run_postcompact_main(frame, monkeypatch, tmp_path) == 0

        base = tmp_path / "pact-sessions" / "my-project"
        for sid, body in bodies:
            [pending] = (base / sid).glob("compact-summary.pending-*.json")
            assert json.loads(pending.read_text(encoding="utf-8"))["summary"] == body
            assert not (base / sid / "compact-summary.txt").exists()


class TestOwnDirClearDoesNotCrossSessions:
    """Race arm 2: session A wrote; session B's session_init clears.

    The own-dir clear is keyed on the CLEARING session's identifiers, so A's
    bytes must survive B's clear, while B's own stale bytes move to a
    timestamped archive in B's own directory (slot clear, bytes kept).
    """

    def test_other_sessions_file_untouched_by_this_sessions_clear(
        self, tmp_path, monkeypatch
    ):
        from session_init import _archive_own_dir_stale_summary

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        base = tmp_path / "pact-sessions" / "my-project"
        a_dir = base / _SID_A
        b_dir = base / _SID_B
        a_dir.mkdir(parents=True)
        b_dir.mkdir(parents=True)
        (a_dir / "compact-summary.txt").write_text("SENTINEL-A", encoding="utf-8")
        (b_dir / "compact-summary.txt").write_text("STALE-B", encoding="utf-8")

        _archive_own_dir_stale_summary(_SID_B, _PROJECT)

        # B's slot cleared, B's bytes archived in B's own dir.
        assert not (b_dir / "compact-summary.txt").exists()
        archives = list(b_dir.glob("compact-summary-*.txt"))
        assert len(archives) == 1
        assert archives[0].read_text(encoding="utf-8") == "STALE-B"
        # A's bytes untouched.
        assert (a_dir / "compact-summary.txt").read_text(
            encoding="utf-8") == "SENTINEL-A"

    def test_unidentified_clear_is_a_clean_noop(self, tmp_path, monkeypatch):
        """F-TEST-2: the empty-identifier guard returns cleanly, touching
        nothing. WITHOUT the guard an unidentified clear does not raise — the
        empty path segment DROPS OUT of the composition, so the call resolves
        to a sibling location (empty sid -> ``my-project/compact-summary.txt``
        directly; empty project_dir -> ``{_SID_A}/compact-summary.txt`` under
        the sessions root) and MOVES whatever it finds there. Sentinels planted
        at exactly those two resolutions make the delete-the-guard mutation
        visibly red; the mutation was probed physically, not reasoned (the
        first reasoned version of this arm was VACUOUS — it planted nothing
        where the guardless call would look, and passed under the mutation)."""
        from session_init import _archive_own_dir_stale_summary

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        base = tmp_path / "pact-sessions"
        a_dir = base / "my-project" / _SID_A
        ragged_sid = base / "my-project"          # where empty-sid resolves
        ragged_proj = base / _SID_A               # where empty-project resolves
        for d in (a_dir, ragged_sid, ragged_proj):
            d.mkdir(parents=True, exist_ok=True)
        (a_dir / "compact-summary.txt").write_text("SENTINEL-A", encoding="utf-8")
        (ragged_sid / "compact-summary.txt").write_text("RAGGED-SID", encoding="utf-8")
        (ragged_proj / "compact-summary.txt").write_text("RAGGED-PROJ", encoding="utf-8")

        for missing in (("", _PROJECT), (_SID_A, "")):
            _archive_own_dir_stale_summary(*missing)

        # Nothing moved: the guard makes BOTH shapes a clean no-op.
        assert (a_dir / "compact-summary.txt").read_text(
            encoding="utf-8") == "SENTINEL-A"
        assert (ragged_sid / "compact-summary.txt").read_text(
            encoding="utf-8") == "RAGGED-SID"
        assert (ragged_proj / "compact-summary.txt").read_text(
            encoding="utf-8") == "RAGGED-PROJ"
        assert list(base.rglob("compact-summary-*.txt")) == []


class TestLegacyDrainTwoPass:
    """Demonstration: pre-upgrade root bytes drain losslessly, exactly once.

    Pass 1 moves them (never deletes) into the starting session's archive;
    pass 2 is a no-op — the MOVE emptied the state, and there is no once-flag.
    """

    def test_root_bytes_rehome_byte_equal_and_second_pass_is_noop(
        self, tmp_path, monkeypatch
    ):
        from session_init import _archive_stale_compact_summary
        from shared.constants import get_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        root = get_compact_summary_path()
        root.parent.mkdir(parents=True, exist_ok=True)
        root.write_text("LEGACY SENTINEL", encoding="utf-8")

        _archive_stale_compact_summary(_SID_A, _PROJECT)

        assert not root.exists()
        dest_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        archives = list(dest_dir.glob("compact-summary-*.txt"))
        assert len(archives) == 1
        assert archives[0].read_text(encoding="utf-8") == "LEGACY SENTINEL"

        # Pass 2: nothing new appears, pass-1 bytes unchanged.
        _archive_stale_compact_summary(_SID_A, _PROJECT)
        archives = list(dest_dir.glob("compact-summary-*.txt"))
        assert len(archives) == 1
        assert archives[0].read_text(encoding="utf-8") == "LEGACY SENTINEL"

    def test_symlinked_project_dir_drains_under_target_basename(
        self, tmp_path, monkeypatch
    ):
        """The drain lands where the session's own writers land: under the
        resolved project basename, never the symlink's name."""
        from session_init import _archive_stale_compact_summary
        from shared.constants import get_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
        real = tmp_path / "real-project"
        real.mkdir()
        link = tmp_path / "link-name"
        link.symlink_to(real, target_is_directory=True)
        root = get_compact_summary_path()
        root.parent.mkdir(parents=True, exist_ok=True)
        root.write_text("LEGACY SENTINEL", encoding="utf-8")

        _archive_stale_compact_summary(_SID_A, str(link))

        dest_dir = tmp_path / "cfg" / "pact-sessions" / "real-project" / _SID_A
        assert len(list(dest_dir.glob("compact-summary-*.txt"))) == 1
        assert not (tmp_path / "cfg" / "pact-sessions" / "link-name").exists()


class TestDegradationIsLossFree:
    """Degradation pin: an unidentified frame's bytes exist SOMEWHERE under
    the config root after the writer runs — at the root singleton. The rglob
    is positive on purpose: suppression and degradation both produce an empty
    session dir, so only a scan proves the bytes survived."""

    def _assert_bytes_under_root(self, tmp_path, body):
        found = [p for p in tmp_path.rglob("compact-summary.txt")
                 if p.read_text(encoding="utf-8") == body]
        assert found == [tmp_path / "pact-sessions" / "compact-summary.txt"], (
            f"degraded bytes must land at the root singleton, found {found}"
        )

    def test_missing_session_id_degrades_loss_free(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT)
        frame = {"agent_type": "PACT:pact-orchestrator",
                 "hook_event_name": "PostCompact",
                 "compact_summary": "DEGRADED-NO-SID"}
        assert _run_postcompact_main(frame, monkeypatch, tmp_path) == 0
        self._assert_bytes_under_root(tmp_path, "DEGRADED-NO-SID")

    def test_missing_project_dir_degrades_loss_free(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        frame = {"session_id": _SID_A, "agent_type": "PACT:pact-orchestrator",
                 "hook_event_name": "PostCompact",
                 "compact_summary": "DEGRADED-NO-PROJ"}
        assert _run_postcompact_main(frame, monkeypatch, tmp_path) == 0
        self._assert_bytes_under_root(tmp_path, "DEGRADED-NO-PROJ")


class TestResumeOwnSummaryClause:
    """Unit arms for ``session_init._resume_own_summary_clause`` — the
    read-side resume-limb pointer (re-scoped #1520). Canonical name first,
    NEWEST archive by mtime as fallback, read-only, OSError fail-open to "".

    The canonical branch is unreachable through a full main() run with
    source="resume" (the own-dir clear archives the live slot before the
    limbs render — the integration arms in test_session_init.py pin THAT
    world); it is live only when the clear failed or has not run, which is
    why these direct-call arms exist. Vacuity discipline: every absence
    arm's fixture is the positive arm's fixture minus exactly one element.
    """

    def _clause(self, session_dir):
        from session_init import _resume_own_summary_clause

        return _resume_own_summary_clause(str(session_dir))

    def test_canonical_present_named_by_absolute_path(self, tmp_path):
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        (session_dir / "compact-summary.txt").write_text(
            "CANONICAL-BYTES", encoding="utf-8"
        )

        clause = self._clause(session_dir)

        assert str(session_dir / "compact-summary.txt") in clause
        assert clause.startswith(
            " A compact summary from an earlier point of this session is "
            "available at"
        )
        assert clause.endswith(".")

    def test_newest_archive_wins_by_mtime(self, tmp_path):
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        old_archive = session_dir / "compact-summary-2026-01-01T00-00-00.txt"
        new_archive = session_dir / "compact-summary-2026-08-27T00-00-00.txt"
        old_archive.write_text("OLD", encoding="utf-8")
        new_archive.write_text("NEW", encoding="utf-8")
        # mtime, not the name, decides: force the lexically-older stamp to be
        # the mtime-newest file.
        import os

        os.utime(old_archive, (2_000_000_000, 2_000_000_000))
        os.utime(new_archive, (1_000_000_000, 1_000_000_000))

        clause = self._clause(session_dir)

        assert str(old_archive) in clause
        assert str(new_archive) not in clause

    def test_empty_dir_no_clause_carrier_control(self, tmp_path):
        """Absence arm + carrier-present control: the same fixture with a
        file planted yields a clause (control), without it yields "" — so the
        empty verdict is the probe's, not a broken fixture."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)

        assert self._clause(session_dir) == ""
        (session_dir / "compact-summary.txt").write_text(
            "CONTROL", encoding="utf-8"
        )
        assert self._clause(session_dir) != ""

    def test_missing_dir_no_clause(self, tmp_path):
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        assert self._clause(session_dir) == ""

    def test_empty_session_dir_string_no_clause(self):
        from session_init import _resume_own_summary_clause

        assert _resume_own_summary_clause("") == ""

    def test_named_path_never_contains_sid_free_root_fragment(self, tmp_path):
        """Degraded-pointer substring pin keeps discriminating: the clause's
        named path embeds slug + session id between 'pact-sessions/' and the
        filename, so the sid-free ROOT-singleton fragment never appears."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        (session_dir / "compact-summary.txt").write_text(
            "BYTES", encoding="utf-8"
        )

        clause = self._clause(session_dir)

        assert "pact-sessions/compact-summary.txt" not in clause
        assert f"pact-sessions/my-project/{_SID_A}/compact-summary.txt" in clause

    def test_canonical_wins_over_newer_mtime_archive(self, tmp_path):
        """F-TE-1: both-present PRIORITY. The canonical live slot outranks the
        archive fallback even when an archive is mtime-NEWER — priority is
        existence-shaped, not recency-shaped. Load-bearing in the
        clear-failure world (own-dir clear raised OSError, canonical
        survived) with prior archives present: inversion would send the lead
        to STALE archived bytes while the fresh canonical sits unnamed.
        Review-cycle-1 counter-test anchor — the priority-inversion mutation
        that shipped green (0/274) must fail THIS arm."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        canonical = session_dir / "compact-summary.txt"
        canonical.write_text("FRESH-CANONICAL", encoding="utf-8")
        archive = session_dir / "compact-summary-2026-01-01T00-00-00.txt"
        archive.write_text("STALE-ARCHIVE", encoding="utf-8")
        # Force the archive mtime-NEWER than the freshly written canonical:
        # priority must win on existence, not recency.
        import os

        os.utime(archive, (2_000_000_000, 2_000_000_000))

        clause = self._clause(session_dir)

        assert str(canonical) in clause
        assert str(archive) not in clause

    # ── Cycle-2 first-surface gate (architect-confirmed semantics) ─────────
    #
    # A candidate (canonical or archive) is named only if NO consuming
    # session_start (source in {resume, startup, clear}; absent/unknown
    # NON-consuming) has ts strictly after its mtime. Newest unsuppressed
    # archive wins. Missing/unreadable/empty journal fails OPEN to naming.
    #
    # Fixture clock: archives get fixed mtimes via os.utime; journal events
    # get explicit ts via make_event (setdefault-honored). 1e9 = 2001-09-09,
    # 2e9 = 2033-05-18, 1.5e9 = 2017-07-14T02:40:00Z sits strictly between.

    def _plant_journal(self, session_dir, events):
        """Write real-shaped session_start events to the session journal."""
        from shared.session_journal import make_event

        lines = [json.dumps(make_event("session_start", **e)) for e in events]
        (session_dir / "session-journal.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _archive(self, session_dir, name, mtime):
        """Plant one archive with a FORCED mtime (deterministic clock)."""
        p = session_dir / name
        p.write_text(f"BYTES-{name}", encoding="utf-8")
        import os

        os.utime(p, (mtime, mtime))
        return p

    def test_first_surface_names_when_consuming_start_predates(self, tmp_path):
        """Primary case (resume-after-compact world): the archive is named
        when every consuming session_start in the journal PREDATES its
        mtime — a startup long ago does not suppress a fresh archive."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        archive = self._archive(
            session_dir, "compact-summary-2026-08-27T00-00-00.txt", 1_000_000_000
        )
        self._plant_journal(
            session_dir, [{"ts": "2000-01-01T00:00:00Z", "source": "startup"}]
        )

        clause = self._clause(session_dir)

        assert str(archive) in clause

    def test_second_resume_consuming_start_suppresses_clause(self, tmp_path):
        """Second-resume suppression: a consuming session_start(resume)
        whose ts postdates the archive mtime means a later session already
        surfaced this summary — NO clause. Carrier-present control in the
        same fixture: a NON-consuming source (compact) postdating the same
        archive leaves the clause in place, proving the suppression is
        gated on the consuming source set, not on any session_start."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        archive = self._archive(
            session_dir, "compact-summary-2026-08-27T00-00-00.txt", 1_000_000_000
        )

        # Control FIRST: non-consuming postdating start → still names.
        self._plant_journal(
            session_dir, [{"ts": "2030-01-01T00:00:00Z", "source": "compact"}]
        )
        assert str(archive) in self._clause(session_dir)

        # Now the consuming shape: same ts, source=resume → suppressed.
        self._plant_journal(
            session_dir, [{"ts": "2030-01-01T00:00:00Z", "source": "resume"}]
        )
        assert self._clause(session_dir) == ""

    def test_missing_or_empty_journal_fails_open_to_naming(self, tmp_path):
        """Fail-open: no journal file at all, then an EMPTY journal file —
        both worlds cannot prove consumption, and the gate must NAME (the
        cycle-1 behavior) rather than suppress on absent evidence."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        archive = self._archive(
            session_dir, "compact-summary-2026-08-27T00-00-00.txt", 1_000_000_000
        )

        assert not (session_dir / "session-journal.jsonl").exists()
        assert str(archive) in self._clause(session_dir)

        (session_dir / "session-journal.jsonl").write_text("", encoding="utf-8")
        assert str(archive) in self._clause(session_dir)

    def test_multi_archive_consumed_old_suppressed_fresh_named(self, tmp_path):
        """Newest UNSUPPRESSED archive wins: a consuming start at 1.5e9
        postdates the old archive (1e9 — consumed, suppressed) but predates
        the fresh one (2e9 — never surfaced) → the fresh archive is named
        and the old one is not."""
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A
        session_dir.mkdir(parents=True)
        old = self._archive(
            session_dir, "compact-summary-2026-01-01T00-00-00.txt", 1_000_000_000
        )
        fresh = self._archive(
            session_dir, "compact-summary-2026-08-27T00-00-00.txt", 2_000_000_000
        )
        self._plant_journal(
            session_dir, [{"ts": "2017-07-14T02:40:00Z", "source": "resume"}]
        )

        clause = self._clause(session_dir)

        assert str(fresh) in clause
        assert str(old) not in clause

    def test_root_drain_artifact_distinct_prefix_never_named(
        self, tmp_path, monkeypatch
    ):
        """F-TE-2 INVERTED for cycle 2 (F-SEC-2): drained root bytes are
        UNATTRIBUTABLE to this session's compactions — they carry a DISTINCT
        prefix and are NEVER named by the clause. Cycle 1 pinned the drain
        artifact AS named; the architect ruling reversed that, so this arm
        now composes the REAL root drain with the REAL clause helper and
        asserts: the drain still RUNS (root singleton moved away, exactly
        one compact-summary* artifact lands in the session dir), but the
        landed artifact is not the canonical slot and the clause names
        NOTHING — under fail-open (no journal planted), so ONLY the
        prefix/shape separation can be what prevents the naming."""
        from session_init import (
            _archive_stale_compact_summary,
            _resume_own_summary_clause,
        )
        from shared.constants import COMPACT_SUMMARY_NAME, get_compact_summary_path

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT)
        root_singleton = get_compact_summary_path()
        root_singleton.parent.mkdir(parents=True, exist_ok=True)
        root_singleton.write_text("DEGRADED-ROOT-BYTES", encoding="utf-8")
        session_dir = tmp_path / "pact-sessions" / "my-project" / _SID_A

        # Control: no drain ran — bytes still at root, session dir absent,
        # and the clause names nothing.
        assert _resume_own_summary_clause(str(session_dir)) == ""

        _archive_stale_compact_summary(_SID_A, _PROJECT)

        landed = sorted(
            p.name for p in session_dir.glob("compact-summary*")
        )
        assert len(landed) == 1, (
            f"the root drain should land exactly one compact-summary* "
            f"artifact in the session dir, found {landed}"
        )
        assert landed[0] != COMPACT_SUMMARY_NAME
        # Moved-not-copied: the root singleton slot is empty afterwards.
        assert not root_singleton.exists()
        # F-SEC-2 core: no journal exists (fail-open would name ANY valid
        # archive), yet the drained artifact is not named — only its
        # distinct, non-archive prefix/shape can be the blocker.
        assert _resume_own_summary_clause(str(session_dir)) == ""
        assert str(root_singleton) not in _resume_own_summary_clause(
            str(session_dir)
        )
