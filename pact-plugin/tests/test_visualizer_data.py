"""Location: pact-plugin/tests/test_visualizer_data.py
Summary: Data-layer tests for PACT Mission Control: root discovery, tolerant
         JSONL parsing, event classification, roster/team/task joining, model
         derivation (phases, agents, tasks, handoffs, gates, variety, wrapup,
         metrics), response caps, the cheap session list, health, and the
         deterministic demo. Every root used here is a temporary directory;
         no test reads the operator's real ~/.claude content.
Used by: pytest (data half of the visualizer backend acceptance suite).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visualizer import classify, demo, discovery, journal, model, roster, settings

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_root(tmp_path: Path, name: str = ".claude") -> Path:
    """Create a temporary config root directory."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_journal(
    root: Path,
    slug: str,
    session_id: str,
    events: list[dict] | bytes,
    *,
    suffix_bytes: bytes = b"",
) -> Path:
    """Write a session journal at the canonical depth and return its path."""
    session_dir = root / "pact-sessions" / slug / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session-journal.jsonl"
    if isinstance(events, bytes):
        data = events + suffix_bytes
    else:
        lines = [json.dumps(event, separators=(",", ":")) for event in events]
        data = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        data += suffix_bytes
    path.write_bytes(data)
    return path


def roots_for(root: Path, tmp_path: Path):
    """RootSpec list for one explicit root (never the real home config)."""
    return settings.resolve_roots([root], env={"CLAUDE_CONFIG_DIR": str(root)}, home=tmp_path)


def first_ref(root: Path, tmp_path: Path) -> discovery.SessionRef:
    """The single discovered session under a root."""
    refs = discovery.discover_sessions(roots_for(root, tmp_path))
    assert len(refs) == 1, refs
    return refs[0]


def stamp(minute: int = 0, second: int = 0) -> str:
    """A fixed UTC timestamp for deterministic fixtures."""
    base = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
    return (base + timedelta(minutes=minute, seconds=second)).strftime("%Y-%m-%dT%H:%M:%SZ")


def basic_events() -> list[dict]:
    """A small complete run: two phases, one agent, one handoff, wrap-up."""
    return [
        {
            "v": 1,
            "type": "session_start",
            "ts": stamp(0),
            "session_id": "sess-1",
            "project_dir": "/Users/dev/PACT-prompt",
            "team": "session-abc",
        },
        {
            "v": 1,
            "type": "phase_transition",
            "ts": stamp(1),
            "phase": "PREPARE",
            "status": "started",
        },
        {
            "v": 1,
            "type": "agent_dispatch",
            "ts": stamp(2),
            "agent": "preparer",
            "task_id": "2",
            "phase": "PREPARE",
        },
        {
            "v": 1,
            "type": "teachback_ack",
            "ts": stamp(3),
            "agent": "preparer",
            "task_id": "2",
        },
        {
            "v": 1,
            "type": "agent_handoff",
            "ts": stamp(4),
            "agent": "preparer",
            "task_id": "2",
            "task_subject": "preparer: research the spec",
            "handoff": {
                "produced": "docs/preparation/spec.md",
                "decisions": "narrow scope",
                "reasoning_chain": "because",
                "uncertainty": "[LOW] none",
                "integration": "architect consumes the doc",
                "open_questions": "None",
            },
        },
        {
            "v": 1,
            "type": "commit",
            "ts": stamp(5),
            "sha": "94c2da8",
            "message": "Add PREPARE research",
            "phase": "PREPARE",
        },
        {
            "v": 1,
            "type": "checkpoint",
            "ts": stamp(6),
            "phase": "PREPARE",
            "completed_phases": ["PREPARE"],
            "active_agents": [{"name": "preparer", "task_id": "2", "status": "completed"}],
            "variety": {"novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8},
        },
        {
            "v": 1,
            "type": "phase_transition",
            "ts": stamp(7),
            "phase": "PREPARE",
            "status": "completed",
        },
        {
            "v": 1,
            "type": "lifecycle_decision",
            "ts": stamp(8),
            "tool_name": "TaskUpdate",
            "advisories_count": 1,
            "rules": ["reasoning_reconstruction_band_unresolvable"],
            "advisories": ["band unresolved"],
            "verdict": "advisory",
        },
        {
            "v": 1,
            "type": "lifecycle_decision",
            "ts": stamp(8),
            "tool_name": "TaskUpdate",
            "advisories_count": 0,
            "rules": [],
            "advisories": [],
            "verdict": "allow",
        },
        {"v": 1, "type": "gate_health", "ts": stamp(9), "hook": "task_lifecycle_gate",
         "status": "failed", "stage": "runtime", "error": "AttributeError: boom"},
        {"v": 1, "type": "journal_emit_skipped", "ts": stamp(9), "skipped_type": "dispatch_site",
         "cause": "schema"},
        {"v": 1, "type": "handoff_refusal_degraded", "ts": stamp(9), "agent_type": "pact-body",
         "detail": "would refuse", "classes": ["missing_handoff"]},
        {"v": 1, "type": "review_finding", "ts": stamp(10), "severity": "blocking",
         "finding": "missing auth check", "reviewer": "security-engineer", "task_id": "30"},
        {"v": 1, "type": "remediation", "ts": stamp(11), "cycle": 1,
         "items": ["missing auth check"], "fixer": "backend-coder", "task_id": "31"},
        {"v": 1, "type": "session_end", "ts": stamp(12), "source": "wrap-up",
         "pr_state": "MERGED", "pr_number": 1151},
    ]


# ---------------------------------------------------------------------------
# Root resolution and discovery
# ---------------------------------------------------------------------------


class TestRoots:
    def test_root_flag_beats_env(self, tmp_path):
        explicit = make_root(tmp_path, "explicit")
        env_root = tmp_path / "from-env"
        roots = settings.resolve_roots([explicit], env={"CLAUDE_CONFIG_DIR": str(env_root)}, home=tmp_path)
        assert len(roots) == 1
        assert roots[0].path == explicit
        assert roots[0].default is True
        assert roots[0].label == "explicit"

    def test_env_root_used_without_flag(self, tmp_path):
        env_root = make_root(tmp_path, "env-root")
        roots = settings.resolve_roots((), env={"CLAUDE_CONFIG_DIR": str(env_root)}, home=tmp_path)
        assert [root.path for root in roots] == [env_root]
        assert roots[0].default is True

    def test_home_fallback_is_dot_claude(self, tmp_path):
        roots = settings.resolve_roots((), env={}, home=tmp_path)
        assert len(roots) == 1
        assert roots[0].path == tmp_path / ".claude"
        assert roots[0].id == "~/.claude"
        assert roots[0].label == ".claude"

    def test_tilde_roots_expand_against_the_injected_home(self, tmp_path):
        """`~` and `~/x` must use the injected home, never the process HOME.

        os.path.expanduser reads the real HOME, so a test passing
        home=tmp_path would silently touch the operator's home and the
        resolver would disagree with the hooks whenever the two homes differ.
        """
        for raw in ("~", "~/nested/root"):
            resolved = settings.resolve_roots([raw], env={}, home=tmp_path)[0].path
            assert resolved.is_relative_to(tmp_path), raw
        assert settings.resolve_roots(["~"], env={}, home=tmp_path)[0].path == tmp_path
        assert (
            settings.resolve_roots(["~/nested/root"], env={}, home=tmp_path)[0].path
            == tmp_path / "nested" / "root"
        )

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n ", " \t \n"])
    def test_blank_env_root_is_unset(self, tmp_path, blank):
        """$CLAUDE_CONFIG_DIR that is set but blank means UNSET, not cwd.

        hooks/shared/paths.get_claude_config_dir strips before its truthiness
        test; a whitespace-only value that slipped through here would resolve
        to the process working directory and the dashboard would read a
        different root than the hooks that write the journals.
        """
        roots = settings.resolve_roots((), env={"CLAUDE_CONFIG_DIR": blank}, home=tmp_path)
        assert [root.path for root in roots] == [tmp_path / ".claude"]

    def test_env_root_precedence_matches_the_hooks_resolver(self, tmp_path):
        """Parity with the hooks' config-root resolver, not just with literals.

        The visualizer duplicates this resolver rather than importing hooks,
        so the two are only kept honest by a test that loads both.
        """
        from shared.paths import get_claude_config_dir

        for value in (
            "",
            "   ",
            "\t\n",
            str(tmp_path / "abs"),
            "relative-dir",
            "~",
            "~/sub",
        ):
            environment = {"CLAUDE_CONFIG_DIR": value}
            ours = settings.resolve_roots((), env=environment, home=tmp_path)[0].path
            theirs = get_claude_config_dir(env=environment, home=tmp_path)
            assert Path(ours).resolve() == Path(theirs).resolve(), value

    def test_repeatable_roots_keep_order_and_first_default(self, tmp_path):
        one = make_root(tmp_path, "one")
        two = make_root(tmp_path, "two")
        roots = settings.resolve_roots([one, two], env={}, home=tmp_path)
        assert [root.path for root in roots] == [one, two]
        assert [root.default for root in roots] == [True, False]

    def test_all_roots_adds_siblings_with_pact_sessions(self, tmp_path):
        primary = tmp_path / ".claude"
        (primary / "pact-sessions").mkdir(parents=True)
        sibling = tmp_path / ".claude-work"
        (sibling / "pact-sessions").mkdir(parents=True)
        no_sessions = tmp_path / ".claude-empty"
        no_sessions.mkdir()
        unrelated = tmp_path / "not-claude"
        (unrelated / "pact-sessions").mkdir(parents=True)
        roots = settings.resolve_roots((), all_roots=True, env={}, home=tmp_path)
        ids = [root.id for root in roots]
        assert ids[0] == "~/.claude"
        assert "~/.claude-work" in ids
        assert "~/.claude-empty" not in ids
        assert all(not root.id.startswith(str(tmp_path)) for root in roots)
        assert len({root.path for root in roots}) == len(roots)

    def test_duplicate_roots_are_deduplicated(self, tmp_path):
        one = make_root(tmp_path, "dup")
        roots = settings.resolve_roots([one, one], env={}, home=tmp_path)
        assert len(roots) == 1


class TestDefaultStateDir:
    """The bare `pact-viz --stop` path resolves its state dir through
    default_state_dir (no --state-dir flag), so detach and stop must agree
    on `<config>/pact-viz`. Injected env/home only; never the real config."""

    def test_env_config_dir_defines_the_state_root(self, tmp_path):
        env_root = tmp_path / "zed"
        state = settings.default_state_dir(
            env={"CLAUDE_CONFIG_DIR": str(env_root)}, home=tmp_path
        )
        assert state == env_root / "pact-viz"

    def test_home_fallback_without_env(self, tmp_path):
        state = settings.default_state_dir(env={}, home=tmp_path)
        assert state == tmp_path / ".claude" / "pact-viz"

    def test_empty_env_var_falls_back_to_home(self, tmp_path):
        state = settings.default_state_dir(
            env={"CLAUDE_CONFIG_DIR": ""}, home=tmp_path
        )
        assert state == tmp_path / ".claude" / "pact-viz"

    @pytest.mark.parametrize("blank", ["   ", "\t", "\n "])
    def test_blank_env_var_falls_back_to_home(self, tmp_path, blank):
        """The state dir uses the same resolver as the roots: blank is UNSET."""
        state = settings.default_state_dir(
            env={"CLAUDE_CONFIG_DIR": blank}, home=tmp_path
        )
        assert state == tmp_path / ".claude" / "pact-viz"


class TestDiscovery:
    def test_exact_depth_only(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "PACT-prompt", "sess-1", basic_events())
        # A nested scratch journal must never be discovered.
        scratch = root / "pact-sessions" / "PACT-prompt" / "sess-1" / "scratch"
        scratch.mkdir(parents=True)
        (scratch / "session-journal.jsonl").write_text("{}\n", encoding="utf-8")
        # A journal one level too shallow must never be discovered.
        (root / "pact-sessions" / "PACT-prompt" / "session-journal.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        # A journal outside pact-sessions must never be discovered.
        other = root / "elsewhere" / "session-journal.jsonl"
        other.parent.mkdir(parents=True)
        other.write_text("{}\n", encoding="utf-8")

        refs = discovery.discover_sessions(roots_for(root, tmp_path))
        assert [(ref.slug, ref.session_id) for ref in refs] == [("PACT-prompt", "sess-1")]
        assert refs[0].relative_journal == "pact-sessions/PACT-prompt/sess-1/session-journal.jsonl"
        assert isinstance(refs[0].root_id, str) and refs[0].root_id.endswith(".claude")
        assert refs[0].key == f"{refs[0].root_id}::PACT-prompt::sess-1"

    def test_missing_root_and_unreadable_dirs_are_empty(self, tmp_path):
        root = make_root(tmp_path)
        assert discovery.discover_sessions(roots_for(root, tmp_path)) == []
        (root / "pact-sessions" / "project" / "not-a-session").mkdir(parents=True)
        assert discovery.discover_sessions(roots_for(root, tmp_path)) == []

    def test_hidden_and_malformed_components_are_skipped(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "PACT-prompt", "..", basic_events())
        write_journal(root, ".hidden", "sess", basic_events())
        write_journal(root, "PACT-prompt", "sess-1", basic_events())
        refs = discovery.discover_sessions(roots_for(root, tmp_path))
        assert [(ref.slug, ref.session_id) for ref in refs] == [("PACT-prompt", "sess-1")]

    def test_find_session_is_traversal_guarded(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "PACT-prompt", "sess-1", basic_events())
        roots = roots_for(root, tmp_path)
        assert discovery.find_session(roots, "PACT-prompt", "sess-1") is not None
        for slug, session_id in (
            ("..", "sess-1"),
            ("PACT-prompt", ".."),
            ("PACT-prompt/../..", "sess-1"),
            ("PACT-prompt", "sess-1/../../etc"),
            ("", "sess-1"),
        ):
            assert discovery.find_session(roots, slug, session_id) is None
        assert discovery.find_session(roots, "PACT-prompt", "missing") is None

    def test_find_root_accepts_id_and_path(self, tmp_path):
        root = make_root(tmp_path)
        roots = roots_for(root, tmp_path)
        assert discovery.find_root(roots, roots[0].id) is not None
        assert discovery.find_root(roots, str(root)) is not None
        assert discovery.find_root(roots, "nope") is None
        assert discovery.find_root(roots, None) is roots[0]


# ---------------------------------------------------------------------------
# Tolerant journal reading
# ---------------------------------------------------------------------------


class TestJournalRead:
    def test_malformed_partial_non_utf8_lines_are_skipped_with_warnings(self, tmp_path):
        root = make_root(tmp_path)
        good = [
            {"v": 1, "type": "session_start", "ts": stamp(0), "session_id": "s", "project_dir": "/x"},
            {"v": 1, "type": "phase_transition", "ts": stamp(1), "phase": "PREPARE", "status": "started"},
        ]
        payload = b""
        payload += json.dumps(good[0], separators=(",", ":")).encode() + b"\n"
        payload += b"\n"  # blank line, skipped silently
        payload += b'{"v": 1, "type": \n'  # malformed JSON
        payload += b"[1, 2, 3]\n"  # valid JSON, not an object
        payload += b'{"v": 1, "ts": "x"}\n'  # object without type
        payload += b"\xff\xfe\x00garbage\n"  # invalid UTF-8
        payload += json.dumps(good[1], separators=(",", ":")).encode() + b"\n"
        payload += b'{"v": 1, "type": "session_end"'  # partial final line
        path = write_journal(root, "proj", "sess", payload)

        read = journal.read_journal(path)
        assert [event.seq for event in read.events] == [0, 6]
        assert [event.data["type"] for event in read.events] == ["session_start", "phase_transition"]
        assert read.lines_total == 8
        assert read.valid_events == 2
        assert read.skipped_lines == 5
        assert read.warnings
        assert len(read.warnings) <= settings.MAX_WARNINGS
        assert read.etag == journal.make_etag(read.mtime, read.size)

    def test_final_line_without_newline_still_parses(self, tmp_path):
        root = make_root(tmp_path)
        path = write_journal(
            root,
            "proj",
            "sess",
            [{"v": 1, "type": "session_start", "ts": stamp(0)}],
        )
        raw = path.read_bytes().rstrip(b"\n")
        path.write_bytes(raw)
        read = journal.read_journal(path)
        assert len(read.events) == 1
        assert read.skipped_lines == 0

    def test_empty_file_reads_as_no_events_without_warnings(self, tmp_path):
        """A zero-byte journal is a run with no events, not a malformed file."""
        path = tmp_path / "session-journal.jsonl"
        path.write_bytes(b"")
        read = journal.read_journal(path)
        assert read.events == []
        assert read.warnings == []

    def test_boundary_truncated_final_line_is_skipped_with_a_warning(self, tmp_path):
        """A reader that catches a writer mid-append must not fail on the
        partial last line, and must say so rather than dropping it silently."""
        path = tmp_path / "session-journal.jsonl"
        path.write_bytes(b'{"v": 1, "type": "session_start", "ts": "2026-11')
        read = journal.read_journal(path)
        assert read.events == []
        assert read.skipped_lines == 1
        assert read.warnings

    def test_missing_file_reads_empty(self, tmp_path):
        read = journal.read_journal(tmp_path / "missing.jsonl")
        assert read.events == []
        assert read.mtime == 0.0
        assert read.size == 0

    def test_etag_changes_after_append(self, tmp_path):
        root = make_root(tmp_path)
        path = write_journal(root, "proj", "sess", [{"v": 1, "type": "session_start", "ts": stamp(0)}])
        first = journal.read_journal(path)
        with path.open("ab") as handle:
            handle.write(b'{"v":1,"type":"session_end","ts":"2026-11-01T10:01:00Z"}\n')
        second = journal.read_journal(path)
        assert first.etag != second.etag
        assert second.etag == journal.make_etag(second.mtime, second.size)

    def test_summarize_reads_ended_session(self, tmp_path):
        root = make_root(tmp_path)
        path = write_journal(root, "proj", "sess", basic_events()[:4] + [
            {"v": 1, "type": "session_end", "ts": stamp(20)},
        ])
        summary = journal.summarize_journal(path)
        assert summary.started_at == stamp(0)
        assert summary.updated_at == stamp(20)
        assert summary.last_event_type == "session_end"
        assert summary.last_phase == "PREPARE"
        assert summary.team == "session-abc"
        assert summary.project_dir == "/Users/dev/PACT-prompt"
        assert summary.run_kind == "orchestrate"
        assert summary.status == "ended"
        assert summary.is_live is False

    def test_summarize_fresh_unended_is_live(self, tmp_path):
        root = make_root(tmp_path)
        path = write_journal(root, "proj", "sess", basic_events()[:4])
        summary = journal.summarize_journal(path)
        assert summary.status == "live"
        assert summary.is_live is True

    def test_summarize_old_unended_is_ended(self, tmp_path):
        root = make_root(tmp_path)
        path = write_journal(root, "proj", "sess", basic_events()[:4])
        future = datetime(2027, 1, 1, tzinfo=UTC)
        summary = journal.summarize_journal(path, now=future)
        assert summary.status == "ended"

    def test_summarize_finds_last_phase_beyond_the_tail_window(self, tmp_path):
        """A phase_transition earlier than the tail window still reports.

        The summary's last_phase used to read only the final tail window, so
        a long tail of non-phase events erased the phase. The bounded
        backward scan must recover it without parsing the whole file.
        """
        root = make_root(tmp_path)
        events = [
            {"v": 1, "type": "phase_transition", "ts": "2026-01-01T00:00:00Z",
             "phase": "ARCHITECT", "status": "started"},
        ]
        filler = [
            {"v": 1, "type": "commit", "ts": "2026-01-01T00:30:00Z",
             "sha": f"{index:08x}", "message": "m" * 400, "phase": "CODE"}
            for index in range(400)
        ]
        path = write_journal(root, "proj", "sess", events + filler)
        assert path.stat().st_size > settings.SUMMARY_TAIL_BYTES
        summary = journal.summarize_journal(path)
        assert summary.last_phase == "ARCHITECT"

    def test_summarize_phase_scan_boundary_is_four_windows(self, tmp_path):
        """The backward scan's bound, at its edge: a phase_transition just
        inside 4 x SUMMARY_TAIL_BYTES (512KB) is recovered; one just beyond
        it is out of budget and leaves last_phase empty.

        The budget is pinned ABSOLUTELY (CONTRACT.md documents 512KB), not
        derived from the constant under test — a fixture derived from
        journal._PHASE_SCAN_MAX_WINDOWS adapts to a mutation of that constant
        and stays green through a silent contract change.
        """
        assert (
            journal._PHASE_SCAN_MAX_WINDOWS * settings.SUMMARY_TAIL_BYTES
            == 4 * settings.SUMMARY_TAIL_BYTES
        ), "the documented 512KB scan budget changed; update CONTRACT.md with it"
        scan_budget = 4 * settings.SUMMARY_TAIL_BYTES
        phase = (
            json.dumps(
                {"v": 1, "type": "phase_transition", "ts": "2026-01-01T00:00:00Z",
                 "phase": "ARCHITECT", "status": "started"},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        shell_prefix = '{"v":1,"type":"note","pad":"'
        shell_suffix = '"}\n'
        line_size = 128

        def filler_line(total_bytes: int) -> bytes:
            """One valid note line of exactly total_bytes, newline included."""
            pad_chars = total_bytes - len(shell_prefix) - len(shell_suffix)
            assert pad_chars >= 0
            line = (shell_prefix + "x" * pad_chars + shell_suffix).encode("utf-8")
            assert len(line) == total_bytes
            return line

        def filler_bytes(total_bytes: int) -> bytes:
            """Exactly total_bytes of parseable note lines.

            Many normal-sized lines, not one giant line: the tail reader's
            window must contain whole parseable lines or summarize_journal
            skips the last_phase scan entirely (empty tail branch).
            """
            remainder = total_bytes % line_size
            first_len = remainder if remainder >= 64 else remainder + line_size
            first = filler_line(first_len)
            count = (total_bytes - len(first)) // line_size
            data = first + filler_line(line_size) * count
            assert len(data) == total_bytes
            return data

        front = filler_line(64)
        for distance, expected in (
            (scan_budget - 2048, "ARCHITECT"),  # ~2KB inside the last window
            (scan_budget + 2048, ""),            # ~2KB beyond the scan budget
        ):
            session_dir = tmp_path / f".claude-{distance}"
            path = session_dir / "session-journal.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = front + phase + filler_bytes(distance)
            path.write_bytes(data)
            assert path.stat().st_size == len(front) + len(phase) + distance
            summary = journal.summarize_journal(path)
            assert summary.last_phase == expected, distance

    def test_read_journal_refuses_oversize_file(self, tmp_path, monkeypatch):
        """A journal over the read cap yields an empty read with a warning."""
        root = make_root(tmp_path)
        path = write_journal(root, "proj", "sess", basic_events()[:4])
        monkeypatch.setattr(journal, "MAX_JOURNAL_BYTES", 10)
        read = journal.read_journal(path)
        assert read.events == []
        assert read.warnings and "byte cap" in read.warnings[0]

    def test_summarize_run_kind_uses_the_shared_marker_set(self, tmp_path):
        """session_end must not mark orchestration in the cheap summary.

        A bare ended run (session_start + session_end) is a session, matching
        the full model's marker set; a commit journal IS an orchestration by
        the same set — the summary and the model must never disagree.
        """
        root = make_root(tmp_path)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        bare = [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T00:02:00Z"},
        ]
        summary = journal.summarize_journal(
            write_journal(root, "proj", "bare", bare), now=now
        )
        assert summary.run_kind == "session"
        assert summary.status == "ended"
        with_commit = bare[:1] + [
            {"v": 1, "type": "commit", "ts": "2026-01-01T00:01:00Z",
             "sha": "aaa", "message": "m", "phase": "CODE"},
        ]
        summary = journal.summarize_journal(
            write_journal(root, "proj", "commit", with_commit), now=now
        )
        assert summary.run_kind == "orchestrate"

    def test_summarize_finds_early_markers_despite_markerless_wrapup_tail(
        self, tmp_path
    ):
        """An ended orchestration with a long markerless tail lists orchestrate.

        The run's only markers sit near the journal's start; a wrap-up tail of
        non-marker events pushes them out of the tail window, so the head
        window (already loaded) must carry the verdict.
        """
        root = make_root(tmp_path)
        now = datetime(2026, 1, 2, tzinfo=UTC)
        events = [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "started", "ts": "2026-01-01T00:01:00Z"},
        ]
        events += [
            {"v": 1, "type": "lifecycle_decision",
             "ts": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z",
             "tool_name": "X", "verdict": "allow"}
            for index in range(120)
        ]
        events.append({"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:00Z"})
        summary = journal.summarize_journal(
            write_journal(root, "proj", "wrapup", events), now=now
        )
        assert summary.run_kind == "orchestrate"
        assert summary.status == "ended"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassify:
    def test_known_event_classified_with_bounded_strings(self):
        raw = journal.RawEvent(
            seq=7,
            data={
                "v": 1,
                "type": "agent_handoff",
                "ts": stamp(1),
                "agent": "preparer",
                "task_id": 8,
                "handoff": {"produced": "x" * 8000},
            },
        )
        event = classify.classify_event(
            raw, {"project": "P", "team": "t", "cloud": False}
        )
        assert event is not None
        assert event["seq"] == 7
        assert event["category"] == "handoff"
        assert event["icon"] == "handoff"
        assert event["severity"] == "success"
        assert event["agent"] == "preparer"
        assert event["task_id"] == "8"
        assert len(event["title"]) <= settings.MAX_TITLE_CHARS
        assert len(event["detail"]) <= settings.MAX_DETAIL_CHARS
        # raw payload is trimmed to the 4000-char field cap
        assert len(event["raw"]["handoff"]["produced"]) == settings.MAX_FIELD_CHARS

    def test_unknown_type_is_omitted(self):
        raw = journal.RawEvent(seq=0, data={"v": 1, "type": "totally_new_event"})
        assert classify.classify_event(raw) is None
        assert classify.is_visual_type("totally_new_event") is False
        assert classify.is_visual_type("commit") is True

    def test_handoff_canonical_fields_and_legacy_aliases(self):
        fields = classify.extract_handoff_fields(
            {
                "produced": "p",
                "key_decisions": "legacy decisions",
                "areas_of_uncertainty": "legacy uncertainty",
                "integration_points": "legacy integration",
                "open_questions": "",
            }
        )
        assert fields["produced"] == "p"
        assert fields["decisions"] == "legacy decisions"
        assert fields["uncertainty"] == "legacy uncertainty"
        assert fields["integration"] == "legacy integration"
        assert fields["reasoning_chain"] is None
        assert fields["open_questions"] is None
        assert classify.handoff_schema_complete(fields) is False

    def test_handoff_schema_complete_for_full_payload(self):
        fields = classify.extract_handoff_fields(
            {name: name.upper() for name in classify.HANDOFF_CANONICAL_FIELDS}
        )
        assert classify.handoff_schema_complete(fields) is True

    def test_handoff_list_valued_fields_are_joined_not_dropped(self):
        """The handoff schema is presence-only, so a field may carry a list.

        open_questions is commonly list-shaped in real handoffs; coercing it
        to None would badge a schema-valid handoff incomplete (the same
        failure class the reasoning_chain carve-out exists to prevent).
        """
        payload = {name: name.upper() for name in classify.HANDOFF_REQUIRED_FIELDS}
        payload["open_questions"] = ["one question", "another question"]
        fields = classify.extract_handoff_fields(payload)
        assert fields["open_questions"] == "one question · another question"
        assert classify.handoff_schema_complete(fields) is True

    def test_handoff_empty_list_field_counts_as_present(self):
        """An explicit empty list is PRESENT under the presence-only schema.

        The real-world shape (measured by qa): all five required keys carry
        values and open_questions is an empty list. Dropping it to None would
        badge a valid handoff incomplete; it renders "(none)" instead.
        """
        payload = {name: name.upper() for name in classify.HANDOFF_REQUIRED_FIELDS}
        payload["open_questions"] = []
        fields = classify.extract_handoff_fields(payload)
        assert fields["open_questions"] == "(none)"
        assert classify.handoff_schema_complete(fields) is True

    def test_handoff_schema_complete_allows_missing_recommended_reasoning_chain(self):
        """The SSOT marks reasoning_chain recommended, not required.

        A five-key handoff is well-formed and must not be labelled incomplete
        in the debrief just because the optional field is absent.
        """
        payload = {
            name: name.upper() for name in classify.HANDOFF_REQUIRED_FIELDS
        }
        fields = classify.extract_handoff_fields(payload)
        assert fields["reasoning_chain"] is None
        assert classify.handoff_schema_complete(fields) is True

    def test_handoff_schema_required_set_excludes_the_recommended_field(self):
        assert "reasoning_chain" not in classify.HANDOFF_REQUIRED_FIELDS
        assert classify.HANDOFF_REQUIRED_FIELDS == tuple(
            field
            for field in classify.HANDOFF_CANONICAL_FIELDS
            if field not in classify.HANDOFF_RECOMMENDED_FIELDS
        )

    def test_handoff_schema_mirrors_the_hooks_ssot(self):
        """The mirror is hand-copied, so this test is all that keeps it in sync.

        visualizer/ must not import hooks at runtime (it is launched with only
        the plugin root on sys.path), so the three tuples are duplicated by
        hand. Without this test a field moving between required and recommended
        in hooks/shared/handoff_schema leaves the debrief badge reporting the
        old partition while every visualizer test stays green.
        """
        from shared import handoff_schema as ssot

        assert classify.HANDOFF_CANONICAL_FIELDS == ssot.HANDOFF_CANONICAL_FIELDS
        assert classify.HANDOFF_RECOMMENDED_FIELDS == ssot.HANDOFF_RECOMMENDED_FIELDS
        assert classify.HANDOFF_LEGACY_ALIASES == ssot.HANDOFF_LEGACY_ALIASES

    def test_normalize_variety(self):
        assert classify.normalize_variety({"novelty": 2, "scope": 3}) == {
            "novelty": 2,
            "scope": 3,
            "uncertainty": 0,
            "risk": 0,
            "total": 5,
        }
        assert classify.normalize_variety({"total": 9}) == {
            "novelty": 0,
            "scope": 0,
            "uncertainty": 0,
            "risk": 0,
            "total": 9,
        }
        assert classify.normalize_variety(None) is None
        assert classify.normalize_variety("nope") is None

    def test_severity_and_icon_tables_are_consistent(self):
        for event_type, (category, icon, severity) in classify.EVENT_TYPE_INFO.items():
            assert isinstance(category, str) and category
            assert isinstance(icon, str) and icon
            assert severity in classify.SEVERITIES

    def test_every_emitted_icon_token_is_handled_by_the_stage(self):
        """The server's icon vocabulary and the canvas's must agree.

        The table check above is self-consistency only: a typo'd token (say
        "hadnoff") is still a non-empty string, and the feed row still renders,
        so nothing reddens while the stage silently animates nothing.
        """
        stage_js = (
            Path(__file__).resolve().parent.parent
            / "visualizer" / "static" / "js" / "stage.js"
        ).read_text(encoding="utf-8")
        handled = set(re.findall(r'icon === "([a-z_]+)"', stage_js))
        emitted = {icon for _category, icon, _severity in classify.EVENT_TYPE_INFO.values()}
        # Feed-row only: panels.js renders these by category, so they carry no
        # stage effect by design.
        inert = {"raw", "variety", "wake"}
        unhandled = emitted - handled - inert
        assert not unhandled, (
            f"event icons with no stage.js branch: {sorted(unhandled)}. "
            f"Either branch on the token in stage.js or add it to `inert` here."
        )


# ---------------------------------------------------------------------------
# Team config, cloud roster and task store
# ---------------------------------------------------------------------------


class TestRoster:
    def test_team_config_members(self, tmp_path):
        root = make_root(tmp_path)
        team_dir = root / "teams" / "session-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps(
                {
                    "leadSessionId": "abc",
                    "members": [
                        {"name": "preparer", "agentId": "preparer@session-abc",
                         "agentType": "PACT:pact-preparer", "backendType": "in-process"},
                        {"name": "backend-coder", "agentType": "pact-backend-coder"},
                        {"not-a-member": True},
                    ],
                }
            ),
            encoding="utf-8",
        )
        info = roster.load_team(root, "session-abc")
        assert info.name == "session-abc"
        assert info.config_present is True
        assert info.cloud is False
        assert [member.name for member in info.members] == ["preparer", "backend-coder"]
        assert info.members[0].role == "preparer"
        assert info.members[1].role == "backend-coder"
        assert info.members[0].cloud is False
        assert roster.color_for_role("preparer") == "green"

    def test_cloud_roster_intents_used_when_no_config(self, tmp_path):
        root = make_root(tmp_path)
        team_dir = root / "teams" / "session-cloud"
        team_dir.mkdir(parents=True)
        lines = [
            {"v": 1, "kind": "intent", "tu": "toolu_1", "name": "preparer",
             "type": "PACT:pact-preparer"},
            {"v": 1, "kind": "intent", "tu": "toolu_1", "name": "ignored-duplicate",
             "type": "PACT:pact-preparer"},
            {"v": 1, "kind": "intent", "tu": "toolu_2", "name": "architect",
             "type": "pact-architect"},
            {"v": 1, "kind": "bind", "tu": "toolu_1", "aid": "a123"},
            "not json at all",
            {"v": 1, "kind": "intent", "tu": "toolu_3", "name": "", "type": "x"},
        ]
        (team_dir / "pact-roster.jsonl").write_text(
            "\n".join(item if isinstance(item, str) else json.dumps(item) for item in lines) + "\n",
            encoding="utf-8",
        )
        info = roster.load_team(root, "session-cloud")
        assert info.cloud is True
        assert info.config_present is False
        assert [intent.name for intent in info.intents] == ["preparer", "architect"]
        assert all(intent.cloud for intent in info.intents)
        assert info.intents[0].role == "preparer"

    def test_task_store_loaded_and_normalized(self, tmp_path):
        root = make_root(tmp_path)
        tasks_dir = root / "tasks" / "session-abc"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "1.json").write_text(
            json.dumps(
                {
                    "id": "1",
                    "subject": "feature: build auth",
                    "owner": "team-lead",
                    "status": "in_progress",
                    "metadata": {
                        "type": "feature",
                        "variety": {"novelty": 3, "scope": 2, "uncertainty": 2,
                                    "risk": 1, "total": 8},
                    },
                }
            ),
            encoding="utf-8",
        )
        (tasks_dir / "2.json").write_text(
            json.dumps(
                {
                    "id": "2",
                    "subject": "preparer: research",
                    "owner": "preparer",
                    "status": "completed",
                    "metadata": {
                        "variety": {"novelty": 1, "scope": 1, "uncertainty": 1,
                                    "risk": 1, "total": 4},
                        "teachback_submit": {"understanding": "x"},
                        "handoff": {"produced": "y"},
                    },
                }
            ),
            encoding="utf-8",
        )
        (tasks_dir / "junk.json").write_text("not json", encoding="utf-8")
        info = roster.load_team(root, "session-abc")
        assert [task.id for task in info.tasks] == ["1", "2"]
        assert info.tasks[1].status == "completed"
        assert info.tasks[1].teachback is True
        assert info.tasks[1].handoff is True
        assert info.tasks[1].variety == {
            "novelty": 1, "scope": 1, "uncertainty": 1, "risk": 1, "total": 4,
        }
        assert info.tasks[0].status == "active"

    def test_invalid_team_name_is_empty(self, tmp_path):
        root = make_root(tmp_path)
        assert roster.load_team(root, "..").name == ""
        assert roster.load_team(root, "").known is False
        assert roster.load_team(root, "missing").known is False

    def test_session_context_file_read(self, tmp_path):
        session_dir = tmp_path / "sessdir"
        session_dir.mkdir()
        (session_dir / "pact-session-context.json").write_text(
            json.dumps({"session_id": "s", "project_dir": "/x/P", "team_name": "session-abc"}),
            encoding="utf-8",
        )
        context = roster.read_session_context(session_dir)
        assert context["team_name"] == "session-abc"
        assert roster.read_session_context(tmp_path / "nope") == {}


# ---------------------------------------------------------------------------
# Model derivation
# ---------------------------------------------------------------------------


def build_basic_model(root: Path, tmp_path: Path) -> dict:
    ref = first_ref(root, tmp_path)
    return model.build_model(ref, now=datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC))


class TestModelBasics:
    def test_agent_commit_stats_attributed_to_latest_same_phase_dispatch(
        self, tmp_path
    ):
        """Crew commit counts must be non-zero and correctly attributed.

        Commit events carry no agent; each attributes to the most recent
        dispatch in the same phase. Before this pass every crew member's
        stats.commits was permanently 0.
        """
        root = make_root(tmp_path)
        events = [
            {"v": 1, "type": "session_start", "ts": stamp(0),
             "project_dir": "/x/proj", "team": "t"},
            {"v": 1, "type": "agent_dispatch", "ts": stamp(1),
             "agent": "backend-coder", "task_id": "1", "phase": "CODE",
             "scope": ["implement"]},
            {"v": 1, "type": "commit", "ts": stamp(2),
             "sha": "aaa11111", "message": "one", "phase": "CODE"},
            {"v": 1, "type": "agent_dispatch", "ts": stamp(3),
             "agent": "preparer", "task_id": "2", "phase": "PREPARE",
             "scope": ["research"]},
            {"v": 1, "type": "commit", "ts": stamp(4),
             "sha": "bbb22222", "message": "two", "phase": "PREPARE"},
            {"v": 1, "type": "commit", "ts": stamp(5),
             "sha": "ccc33333", "message": "three", "phase": "CODE"},
        ]
        write_journal(root, "PACT-prompt", "sess-1", events)
        payload = build_basic_model(root, tmp_path)
        stats = {agent["name"]: agent["stats"]["commits"] for agent in payload["agents"]}
        assert stats["backend-coder"] == 2  # both CODE commits
        assert stats["preparer"] == 1  # the PREPARE commit
        assert sum(stats.values()) == 3

    def test_full_model_shape(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "PACT-prompt", "sess-1", basic_events())
        payload = build_basic_model(root, tmp_path)

        assert payload["ok"] is True
        assert payload["etag"] == journal.read_journal(
            root / "pact-sessions" / "PACT-prompt" / "sess-1" / "session-journal.jsonl"
        ).etag
        assert payload["source"]["kind"] == "real"
        assert isinstance(payload["source"]["root"], str) and payload["source"]["root"]
        assert payload["source"]["journal"] == (
            "pact-sessions/PACT-prompt/sess-1/session-journal.jsonl"
        )
        meta = payload["meta"]
        assert meta["project"] == "PACT-prompt"
        assert meta["project_dir"] == "/Users/dev/PACT-prompt"
        assert meta["team"] == "session-abc"
        assert meta["status"] == "ended"
        assert meta["event_count"] == len(basic_events())
        assert meta["run_kind"] == "orchestrate"
        assert meta["cloud"] is False
        assert isinstance(payload["warnings"], list)
        assert payload["truncated"] is False

    def test_phases(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        phases = build_basic_model(root, tmp_path)["phases"]
        assert phases["order"] == ["PREPARE", "ARCHITECT", "CODE", "TEST"]
        assert phases["current"] == "PREPARE"
        assert phases["completed"] == ["PREPARE"]
        assert phases["pending"] == ["ARCHITECT", "CODE", "TEST"]
        prepare = phases["items"][0]
        assert prepare["status"] == "completed"
        assert prepare["run"] == 1
        assert prepare["started_seq"] == 1
        assert prepare["ended_seq"] == 7

    def test_agents_from_team_config_and_events(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        team_dir = root / "teams" / "session-abc"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"members": [
                {"name": "preparer", "agentType": "pact-preparer"},
                {"name": "architect", "agentType": "pact-architect"},
            ]}),
            encoding="utf-8",
        )
        agents = build_basic_model(root, tmp_path)["agents"]
        by_name = {agent["name"]: agent for agent in agents}
        assert agents[0]["kind"] == "lead"
        assert "preparer" in by_name and "architect" in by_name
        preparer = by_name["preparer"]
        assert preparer["role"] == "preparer"
        assert preparer["color"] == "green"
        assert preparer["stats"]["dispatches"] == 1
        assert preparer["stats"]["handoffs"] == 1
        assert preparer["task_id"] == "2"
        assert preparer["state"] == "done"
        assert preparer["latest"]["type"] == "agent_handoff"
        assert by_name["architect"]["state"] == "standby"
        assert by_name["architect"]["latest"] is None

    def test_tasks_derived_from_events_and_store(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        tasks_dir = root / "tasks" / "session-abc"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "30.json").write_text(
            json.dumps({"id": "30", "subject": "security: check auth",
                        "owner": "security-engineer", "status": "in_progress",
                        "metadata": {"type": "signal"}}),
            encoding="utf-8",
        )
        tasks = build_basic_model(root, tmp_path)["tasks"]
        by_id = {task["id"]: task for task in tasks}
        assert by_id["2"]["owner"] == "preparer"
        assert by_id["2"]["handoff"] is True
        assert by_id["2"]["teachback"] is True
        assert by_id["2"]["subject"] == "preparer: research the spec"
        assert by_id["30"]["source"] == "store"
        assert by_id["30"]["status"] == "active"
        assert by_id["31"]["owner"] == "backend-coder"

    def test_handoffs_and_commits(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        payload = build_basic_model(root, tmp_path)
        assert len(payload["handoffs"]) == 1
        handoff = payload["handoffs"][0]
        assert handoff["agent"] == "preparer"
        assert handoff["task_id"] == "2"
        assert handoff["task_subject"] == "preparer: research the spec"
        assert set(handoff["fields"]) == set(classify.HANDOFF_CANONICAL_FIELDS)
        assert handoff["schema_complete"] is True
        assert [commit["sha"] for commit in payload["commits"]] == ["94c2da8"]
        assert payload["commits"][0]["phase"] == "PREPARE"

    def test_findings_alerts_checkpoints_wrapup(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        payload = build_basic_model(root, tmp_path)
        assert payload["findings"] == [
            {
                "seq": 13,
                "ts": stamp(10),
                "severity": "blocking",
                "finding": "missing auth check",
                "reviewer": "security-engineer",
                "task_id": "30",
            }
        ]
        assert payload["alerts"] == []
        assert len(payload["checkpoints"]) == 1
        assert payload["checkpoints"][0]["completed_phases"] == ["PREPARE"]
        assert payload["wrapup"] == {
            "source": "wrap-up",
            "pr_state": "MERGED",
            "pr_number": 1151,
            "ts": stamp(12),
        }
        events = {event["type"] for event in payload["events"]}
        assert "lifecycle_decision" in events
        assert "journal_emit_skipped" in events
        assert "handoff_refusal_degraded" in events
        assert "remediation" in events

    def test_gates_and_telemetry(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        gates = build_basic_model(root, tmp_path)["gates"]
        assert gates["allowed"] == 1
        assert gates["advisory"] == 1
        assert gates["by_tool"]["TaskUpdate"] == {"allow": 1, "advisory": 1}
        assert gates["top_rules"] == [
            {"rule": "reasoning_reconstruction_band_unresolvable", "count": 1}
        ]
        assert gates["health_failures"][0]["hook"] == "task_lifecycle_gate"
        assert gates["health_failures"][0]["error"] == "AttributeError: boom"
        assert gates["telemetry"] == {
            "journal_emit_skipped": 1,
            "handoff_refusal_degraded": 1,
        }

    def test_variety_and_metrics(self, tmp_path):
        root = make_root(tmp_path)
        write_journal(root, "P", "s", basic_events())
        payload = build_basic_model(root, tmp_path)
        variety = payload["variety"]
        assert variety["feature"] is None
        assert variety["latest"] == {
            "novelty": 2, "scope": 2, "uncertainty": 3, "risk": 1, "total": 8,
        }
        assert variety["history"] == [
            {"seq": 6, "ts": stamp(6), "total": 8, "source": "checkpoint"}
        ]
        metrics = payload["metrics"]
        assert metrics["events_total"] == len(basic_events())
        assert metrics["events_visible"] == len(payload["events"])
        assert metrics["agents"] == len(payload["agents"])
        assert metrics["handoffs"] == 1
        assert metrics["commits"] == 1
        assert metrics["findings"] == {"total": 1, "blocking": 1, "suggestion": 0}
        assert metrics["alerts"] == 0
        assert metrics["teachbacks"] == 1
        assert metrics["checkpoints"] == 1
        assert metrics["compactions"] == 0
        assert metrics["phases_completed"] == 1
        assert metrics["phases_skipped"] == 0
        assert metrics["prs_ready"] == 0
        assert metrics["variety_total"] == 8
        assert metrics["gates_allowed"] == 1
        assert metrics["gates_advisory"] == 1
        assert metrics["gate_health_failures"] == 1

    def test_wrapup_null_without_wrapup_session_end(self, tmp_path):
        root = make_root(tmp_path)
        events = [event for event in basic_events() if event["type"] != "session_end"]
        events.append({"v": 1, "type": "session_end", "ts": stamp(30)})
        write_journal(root, "P", "s", events)
        assert build_basic_model(root, tmp_path)["wrapup"] is None

    def test_unknown_event_types_counted_and_omitted(self, tmp_path):
        root = make_root(tmp_path)
        events = basic_events() + [
            {"v": 1, "type": "brand_new_thing", "ts": stamp(40)},
            {"v": 1, "type": "another_new_thing", "ts": stamp(41)},
        ]
        write_journal(root, "P", "s", events)
        payload = build_basic_model(root, tmp_path)
        assert payload["meta"]["event_count"] == len(events)
        assert all(event["type"] not in {"brand_new_thing", "another_new_thing"}
                   for event in payload["events"])
        assert any("unrecognized" in warning for warning in payload["warnings"])


class TestPhasesRerunSkip:
    def test_rerun_increments_run_and_skip_reason_recorded(self, tmp_path):
        root = make_root(tmp_path)
        events = [
            {"v": 1, "type": "phase_transition", "ts": stamp(0), "phase": "PREPARE", "status": "started"},
            {"v": 1, "type": "phase_transition", "ts": stamp(1), "phase": "PREPARE", "status": "completed"},
            {"v": 1, "type": "phase_transition", "ts": stamp(2), "phase": "PREPARE", "status": "started"},
            {"v": 1, "type": "phase_transition", "ts": stamp(3), "phase": "PREPARE", "status": "completed"},
            {"v": 1, "type": "phase_transition", "ts": stamp(4), "phase": "ARCHITECT",
             "status": "skipped", "skip_reason": "design already approved"},
            {"v": 1, "type": "phase_transition", "ts": stamp(5), "phase": "CODE", "status": "started"},
        ]
        write_journal(root, "P", "s", events)
        payload = build_basic_model(root, tmp_path)
        phases = payload["phases"]
        assert phases["current"] == "CODE"
        prepare = phases["items"][0]
        assert prepare["run"] == 2
        assert prepare["status"] == "completed"
        architect = phases["items"][1]
        assert architect["status"] == "skipped"
        assert architect["skip_reason"] == "design already approved"
        assert phases["completed"] == ["PREPARE"]
        assert phases["pending"] == ["TEST"]
        assert payload["metrics"]["phases_skipped"] == 1
        assert payload["metrics"]["phases_completed"] == 1


class TestCaps:
    def test_gates_by_tool_is_capped_with_an_honest_total(self):
        """by_tool must not grow without bound with distinct tool names."""
        events = [
            {
                "type": "lifecycle_decision",
                "seq": index,
                "raw": {"tool_name": f"tool{index}", "verdict": "allow"},
            }
            for index in range(settings.MAX_GATE_TOOLS + 20)
        ]
        gates = model.derive_gates(events)
        assert len(gates["by_tool"]) == settings.MAX_GATE_TOOLS
        assert gates["by_tool_total"] == settings.MAX_GATE_TOOLS + 20
        # the kept tools are the busiest, deterministically ordered
        kept = list(gates["by_tool"])
        assert kept == sorted(
            kept, key=lambda name: (-sum(gates["by_tool"][name].values()), name)
        )

    def test_event_cap_keeps_most_recent_and_marks_truncated(self, tmp_path):
        root = make_root(tmp_path)
        total = settings.MAX_EVENTS + 250
        events = [
            {"v": 1, "type": "phase_transition", "ts": stamp(i // 60, i % 60),
             "phase": "PREPARE", "status": "started"}
            for i in range(total)
        ]
        write_journal(root, "P", "s", events)
        payload = build_basic_model(root, tmp_path)
        assert len(payload["events"]) == settings.MAX_EVENTS
        assert payload["metrics"]["events_total"] == total
        assert payload["metrics"]["events_visible"] == settings.MAX_EVENTS
        assert payload["truncated"] is True
        assert any("truncated" in warning for warning in payload["warnings"])
        # seq remains the physical file index and the kept window is the tail
        assert payload["events"][0]["seq"] == total - settings.MAX_EVENTS
        assert payload["events"][-1]["seq"] == total - 1

    def test_handoff_and_task_caps(self, tmp_path):
        root = make_root(tmp_path)
        events = []
        for index in range(settings.MAX_HANDOFFS + 25):
            events.append(
                {
                    "v": 1,
                    "type": "agent_handoff",
                    "ts": stamp(index // 60, index % 60),
                    "agent": "preparer",
                    "task_id": f"h{index}",
                    "task_subject": f"task {index}",
                    "handoff": {"produced": f"artifact-{index}"},
                }
            )
        events.extend(
            {
                "v": 1,
                "type": "task_metadata_snapshot",
                "ts": stamp(1, index % 60),
                "task_id": f"t{index}",
                "subject": f"task {index}",
            }
            for index in range(settings.MAX_TASKS + 20)
        )
        write_journal(root, "P", "s", events)
        payload = build_basic_model(root, tmp_path)
        assert len(payload["handoffs"]) == settings.MAX_HANDOFFS
        assert payload["metrics"]["handoffs"] == settings.MAX_HANDOFFS + 25
        assert len(payload["tasks"]) == settings.MAX_TASKS
        # Union of the handoff task ids (h0..) and snapshot task ids (t0..):
        # both streams create tasks, so the full count is their sum.
        assert payload["metrics"]["tasks"] == (
            settings.MAX_HANDOFFS + 25 + settings.MAX_TASKS + 20
        )
        assert payload["truncated"] is True

    def test_handoff_field_cap(self, tmp_path):
        root = make_root(tmp_path)
        huge = "x" * (settings.MAX_FIELD_CHARS * 3)
        events = [
            {
                "v": 1,
                "type": "agent_handoff",
                "ts": stamp(0),
                "agent": "preparer",
                "task_id": "2",
                "handoff": {
                    "produced": huge,
                    "decisions": "d",
                    "reasoning_chain": "r",
                    "uncertainty": "u",
                    "integration": "i",
                    "open_questions": "o",
                },
            }
        ]
        write_journal(root, "P", "s", events)
        handoff = build_basic_model(root, tmp_path)["handoffs"][0]
        assert len(handoff["fields"]["produced"]) == settings.MAX_FIELD_CHARS
        assert len(handoff["fields"]["decisions"]) <= settings.MAX_FIELD_CHARS


class TestReviewsPayload:
    """derive_reviews feeds payload["reviews"] and metrics reviews_total;
    until these tests nothing pinned its field mapping or its cap."""

    def test_field_mapping_and_only_review_dispatch_is_collected(self):
        events = [
            {"type": "phase_transition", "seq": 0, "raw": {"phase": "TEST"}},
            {
                "type": "review_dispatch",
                "seq": 1,
                "ts": "2026-11-01T10:00:00Z",
                "raw": {
                    "pr_number": 1697,
                    "pr_url": "https://example.invalid/pull/1697",
                    "reviewers": ["security-engineer", "test-engineer"],
                },
            },
            {
                "type": "review_dispatch",
                "seq": 2,
                "ts": "2026-11-01T11:30:00Z",
                "raw": {},
            },
        ]
        reviews, total = model.derive_reviews(events)
        assert total == 2
        assert reviews == [
            {
                "seq": 1,
                "ts": "2026-11-01T10:00:00Z",
                "pr_number": 1697,
                "pr_url": "https://example.invalid/pull/1697",
                "reviewers": ["security-engineer", "test-engineer"],
            },
            {"seq": 2, "ts": "2026-11-01T11:30:00Z",
             "pr_number": None, "pr_url": "", "reviewers": []},
        ]

    def test_cap_keeps_the_tail_with_an_honest_total(self):
        count = settings.MAX_REVIEWS + 20
        events = [
            {
                "type": "review_dispatch",
                "seq": index,
                "ts": stamp(index // 60, index % 60),
                "raw": {
                    "pr_number": 1700 + index,
                    "pr_url": f"https://example.invalid/pull/{1700 + index}",
                    "reviewers": [f"reviewer-{index}"],
                },
            }
            for index in range(count)
        ]
        reviews, total = model.derive_reviews(events)
        assert total == count
        assert len(reviews) == settings.MAX_REVIEWS
        assert reviews[0]["seq"] == 20
        assert reviews[-1]["seq"] == count - 1
        kept = reviews[-1]
        assert kept["pr_number"] == 1700 + count - 1
        assert kept["pr_url"] == f"https://example.invalid/pull/{1700 + count - 1}"
        assert kept["reviewers"] == [f"reviewer-{count - 1}"]


class TestSessionsListAndHealth:
    def _two_sessions(self, tmp_path: Path) -> tuple[Path, list[dict]]:
        root = make_root(tmp_path)
        write_journal(
            root, "Older", "sess-old", [
                {"v": 1, "type": "session_start", "ts": "2026-10-01T10:00:00Z",
                 "project_dir": "/Users/dev/Older"},
                {"v": 1, "type": "phase_transition", "ts": "2026-10-01T10:01:00Z",
                 "phase": "PREPARE", "status": "started"},
                {"v": 1, "type": "session_end", "ts": "2026-10-01T10:02:00Z"},
            ],
        )
        write_journal(
            root, "Newer", "sess-new", [
                {"v": 1, "type": "session_start", "ts": "2026-10-02T10:00:00Z",
                 "project_dir": "/Users/dev/Newer"},
                {"v": 1, "type": "phase_transition", "ts": "2026-10-02T10:01:00Z",
                 "phase": "CODE", "status": "started"},
                {"v": 1, "type": "session_end", "ts": "2026-10-02T10:02:00Z"},
            ],
        )
        roots = roots_for(root, tmp_path)
        return root, roots

    def test_sessions_sorted_newest_first_with_cheap_fields(self, tmp_path):
        _root, roots = self._two_sessions(tmp_path)
        payload = model.build_sessions_list(roots, now=datetime(2026, 11, 1, tzinfo=UTC))
        assert payload["ok"] is True
        assert payload["default_root"] == roots[0].id
        assert payload["generated_at"].endswith("Z")
        assert payload["roots"][0]["journals"] == 2
        assert payload["roots"][0]["default"] is True
        sessions = payload["sessions"]
        assert [session["session_id"] for session in sessions] == ["sess-new", "sess-old"]
        newest = sessions[0]
        assert newest["project"] == "Newer"
        assert newest["project_dir"] == "/Users/dev/Newer"
        assert newest["updated_at"] == "2026-10-02T10:02:00Z"
        assert newest["started_at"] == "2026-10-02T10:00:00Z"
        assert newest["last_event_type"] == "session_end"
        assert newest["last_phase"] == "CODE"
        assert newest["status"] == "ended"
        assert newest["is_live"] is False
        assert newest["run_kind"] == "orchestrate"
        assert newest["size_bytes"] > 0
        assert newest["key"].endswith("::Newer::sess-new")

    def test_limit_and_filters(self, tmp_path):
        _root, roots = self._two_sessions(tmp_path)
        limited = model.build_sessions_list(roots, limit=1)
        assert len(limited["sessions"]) == 1
        filtered = model.build_sessions_list(roots, project_filter="older")
        assert [session["session_id"] for session in filtered["sessions"]] == ["sess-old"]
        by_session = model.build_sessions_list(roots, session_filter="new")
        assert [session["session_id"] for session in by_session["sessions"]] == ["sess-new"]
        none = model.build_sessions_list(roots, project_filter="does-not-exist")
        assert none["sessions"] == []

    def test_multiple_roots_are_listed_with_first_as_default(self, tmp_path):
        first = make_root(tmp_path, ".claude")
        second = make_root(tmp_path, ".claude-work")
        write_journal(first, "Alpha", "sess-a", [
            {"v": 1, "type": "session_start", "ts": "2026-10-01T10:00:00Z",
             "project_dir": "/Users/dev/Alpha"},
            {"v": 1, "type": "session_end", "ts": "2026-10-01T10:01:00Z"},
        ])
        write_journal(second, "Beta", "sess-b", [
            {"v": 1, "type": "session_start", "ts": "2026-10-02T10:00:00Z",
             "project_dir": "/Users/dev/Beta"},
            {"v": 1, "type": "session_end", "ts": "2026-10-02T10:01:00Z"},
        ])
        roots = settings.resolve_roots([first, second], env={}, home=tmp_path)
        payload = model.build_sessions_list(roots)
        assert payload["default_root"] == roots[0].id
        assert [root["journals"] for root in payload["roots"]] == [1, 1]
        assert [session["session_id"] for session in payload["sessions"]] == [
            "sess-b", "sess-a",
        ]
        assert {session["root"] for session in payload["sessions"]} == {
            roots[0].id, roots[1].id,
        }

    def test_health_payload(self, tmp_path):
        _root, roots = self._two_sessions(tmp_path)
        payload = model.build_health(
            roots, pid=1234, instance_id="deadbeef", uptime_s=12.34,
            now=datetime(2026, 11, 1, tzinfo=UTC),
        )
        assert payload["ok"] is True
        assert payload["name"] == "pact-mission-control"
        # CONTRACT.md is the source of truth for the payload shape, so pin its
        # literal: `== settings.VERSION` would pass on any value at all.
        assert payload["version"] == "0.1.0"
        assert payload["version"] == settings.VERSION
        assert payload["pid"] == 1234
        assert payload["instance_id"] == "deadbeef"
        assert payload["uptime_s"] == 12.3
        assert payload["demo"] is True
        assert payload["roots"][0]["journals"] == 2


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def model_payload() -> dict:
    """One deterministic demo build shared by the demo tests."""
    return demo.build_demo_model()


class TestDemo:
    def test_identity(self, model_payload):
        assert model_payload["ok"] is True
        assert model_payload["etag"] == settings.DEMO_ETAG == "demo-v1"
        source = model_payload["source"]
        assert source["kind"] == "demo"
        assert source["root"] == "demo"
        assert source["slug"] == "training-simulation"
        assert source["journal"] == ""

    def test_four_phases(self, model_payload):
        phases = model_payload["phases"]
        assert phases["order"] == ["PREPARE", "ARCHITECT", "CODE", "TEST"]
        assert phases["completed"] == ["PREPARE", "ARCHITECT", "CODE", "TEST"]
        assert phases["current"] == "TEST"
        assert phases["pending"] == []
        for item in phases["items"]:
            assert item["status"] == "completed"
            assert item["run"] == 1

    def test_nine_agents_and_lead(self, model_payload):
        agents = model_payload["agents"]
        assert len(agents) >= 8
        assert agents[0]["name"] == "team-lead"
        assert agents[0]["kind"] == "lead"
        assert agents[0]["state"] == "done"
        names = {agent["name"] for agent in agents}
        assert {
            "preparer", "secretary", "architect", "auditor", "backend-coder",
            "frontend-coder", "test-engineer", "security-engineer",
        } <= names
        for agent in agents:
            assert set(agent["stats"]) == {
                "events", "dispatches", "handoffs", "commits", "findings",
            }

    def test_handoffs_are_six_field(self, model_payload):
        handoffs = model_payload["handoffs"]
        assert len(handoffs) == 4
        for handoff in handoffs:
            assert list(handoff["fields"]) == list(classify.HANDOFF_CANONICAL_FIELDS)
            assert all(handoff["fields"][name] for name in classify.HANDOFF_CANONICAL_FIELDS)
            assert handoff["schema_complete"] is True
            assert handoff["task_subject"]

    def test_wrapup_merged(self, model_payload):
        assert model_payload["wrapup"] == {
            "source": "wrap-up",
            "pr_state": "MERGED",
            "pr_number": 42,
            "ts": model_payload["events"][-1]["ts"],
        }
        assert model_payload["events"][-1]["type"] == "session_end"
        assert model_payload["metrics"]["prs_ready"] == 1

    def test_gates_health_and_telemetry(self, model_payload):
        gates = model_payload["gates"]
        assert gates["advisory"] >= 1
        assert gates["allowed"] >= 1
        assert gates["health_failures"]
        assert gates["health_failures"][0]["hook"] == "task_lifecycle_gate"
        assert gates["top_rules"]
        assert gates["telemetry"]["journal_emit_skipped"] >= 1
        assert model_payload["metrics"]["gate_health_failures"] == 1

    def test_required_event_variety(self, model_payload):
        types = {event["type"] for event in model_payload["events"]}
        required = {
            "session_start",
            "phase_transition",
            "agent_dispatch",
            "dispatch_decision",
            "agent_handoff",
            "teachback_ack",
            "task_metadata_snapshot",
            "checkpoint",
            "commit",
            "review_finding",
            "remediation",
            "review_dispatch",
            "pr_ready",
            "algedonic_signal",
            "blocker_raised",
            "compaction_attributed",
            "team_realigned",
            "lifecycle_decision",
            "gate_health",
            "journal_emit_skipped",
            "variety_assessed",
            "session_end",
        }
        assert required <= types
        metrics = model_payload["metrics"]
        assert metrics["compactions"] == 1
        assert metrics["checkpoints"] == 4
        assert metrics["alerts"] == 2
        assert metrics["teachbacks"] == 5
        assert metrics["findings"]["blocking"] == 1
        assert metrics["findings"]["suggestion"] == 2
        assert metrics["phases_completed"] == 4
        assert metrics["phases_skipped"] == 0

    def test_variety_block_present(self, model_payload):
        variety = model_payload["variety"]
        assert variety["feature"] is not None
        assert variety["feature"]["total"] == 10
        assert variety["latest"] is not None
        assert len(variety["history"]) >= 3
        sources = {entry["source"] for entry in variety["history"]}
        assert {"feature", "dispatch", "checkpoint"} <= sources
        assert model_payload["metrics"]["variety_total"] == 10

    def test_deterministic_and_base_time_injectable(self):
        first = demo.build_demo_model()
        second = demo.build_demo_model()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        shifted_base = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
        shifted = demo.build_demo_model(base_time=shifted_base)
        assert shifted["meta"]["started_at"] == "2027-01-01T00:00:00Z"
        assert shifted["metrics"]["events_total"] == first["metrics"]["events_total"]
        assert len(shifted["events"]) == len(first["events"])
        assert shifted["events"][-1]["type"] == "session_end"

    def test_demo_does_not_expose_real_paths(self, model_payload):
        blob = json.dumps(model_payload)
        assert "/Users/demo/Sites/pact-demo" in blob  # obviously synthetic
        home = str(Path.home())
        assert home not in blob
