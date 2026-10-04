"""
Integration tests for pin caps — cross-module boundary verification.

Covers:
- staleness.check_pinned_block_signal: end-to-end CLAUDE.md → CapViolation
- session_init.check_pin_slot_status: Tier-0 additionalContext line
- session_init.check_pin_stale_block_directive: marker lifecycle + directive
- pin-memory.md prose contract: two-step AskUserQuestion grammar
- Property-test: parse_pins stale detection agrees with
  detect_stale_entries walker on shared fixtures
- Boundary-agreement: live CLAUDE.md:68 override line round-trips

Risk tier: CRITICAL.
"""

import re
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


def _build_pinned_claude_md(n_pins=0, pin_body_chars=100, stale_indices=()):
    """Thin wrapper around helpers.py factories preserving legacy signature."""
    entries = [
        make_pin_entry(
            title=f"Pin {i}",
            body_chars=pin_body_chars,
            stale_date="2026-01-01" if i in stale_indices else None,
        )
        for i in range(n_pins)
    ]
    return make_claude_md_with_pins(entries)


class TestCheckPinnedBlockSignal_EndToEnd:
    """staleness.check_pinned_block_signal on real CLAUDE.md content."""

    def test_no_stale_pins_returns_none(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_build_pinned_claude_md(3), encoding="utf-8")
        assert check_pinned_block_signal(claude_md) is None

    def test_one_stale_pin_returns_none_below_threshold(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0}), encoding="utf-8"
        )
        assert check_pinned_block_signal(claude_md) is None

    def test_two_stale_pins_returns_violation(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        result = check_pinned_block_signal(claude_md)
        assert result is not None
        assert result.kind == "stale"

    def test_three_stale_pins_returns_violation(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1, 2}),
            encoding="utf-8",
        )
        result = check_pinned_block_signal(claude_md)
        assert result is not None

    def test_missing_claude_md_fails_open(self, tmp_path):
        from staleness import check_pinned_block_signal
        nonexistent = tmp_path / "does-not-exist.md"
        # read_text raises OSError → fail-open (None)
        assert check_pinned_block_signal(nonexistent) is None

    def test_no_pinned_section_fails_open(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Working Memory\n\nNo pins.\n", encoding="utf-8"
        )
        assert check_pinned_block_signal(claude_md) is None

    def test_parse_exception_fails_open(self, tmp_path, monkeypatch):
        """parse_pins raising does NOT propagate — block signal returns None."""
        from staleness import check_pinned_block_signal
        import staleness as staleness_mod
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2, stale_indices={0, 1}), encoding="utf-8"
        )

        def _boom(_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(staleness_mod, "parse_pins", _boom)
        assert check_pinned_block_signal(claude_md) is None


class TestCheckPinSlotStatus_SessionInit:
    """session_init.check_pin_slot_status emits Tier-0 additionalContext line."""

    def test_returns_status_string_with_pins(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, pin_body_chars=200), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        result = check_pin_slot_status()
        assert result is not None
        assert "3/12" in result

    def test_returns_zero_status_when_no_pinned_section(
        self, tmp_path, monkeypatch
    ):
        """Missing pinned section → surface 0-used so orchestrator sees headroom."""
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Working Memory\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        result = check_pin_slot_status()
        assert result == "Pin slots: 0/12 used"

    def test_returns_none_when_no_claude_md(self, monkeypatch):
        from session_init import check_pin_slot_status
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: None
        )
        assert check_pin_slot_status() is None

    def test_returns_none_on_read_error(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )

        def _raise(*a, **k):
            raise IOError("simulated")

        monkeypatch.setattr(Path, "read_text", _raise)
        assert check_pin_slot_status() is None

    def test_idempotent_on_repeated_invocation(self, tmp_path, monkeypatch):
        """P0: SessionStart fires repeatedly; output MUST NOT drift."""
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(5, pin_body_chars=300), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        results = [check_pin_slot_status() for _ in range(3)]
        assert results[0] == results[1] == results[2]

    def test_returns_none_on_parse_exception(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        import session_init as si
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )

        def _boom(_doc, _located):
            raise RuntimeError("nope")

        monkeypatch.setattr(si, "section_pins", _boom)
        assert check_pin_slot_status() is None


class TestCheckPinStaleBlockDirective_MarkerLifecycle:
    """session_init.check_pin_stale_block_directive — marker arm/clear cycle.

    The directive returns a hard-rule MUST string on positive detection
    AND writes a session-scoped marker so pin_staleness_gate (PreToolUse)
    can block later Edit/Write. When detection goes negative, the marker
    MUST be cleared so the gate does not persist stale arming.
    """

    def test_positive_detection_emits_directive_and_arms_marker(
        self, tmp_path, monkeypatch, pact_context
    ):
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        result = check_pin_stale_block_directive()
        assert result is not None
        assert "MUST" in result
        # NAME THE COMMAND THAT ARCHIVES. This arm read `/PACT:pin-memory`,
        # and the one source of that substring was an incorrect clause in the
        # detail, so the arm held the defect in place. The directive must name
        # the command that can clear a stale pin.
        assert "/PACT:prune-memory" in result
        assert (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_negative_detection_clears_marker(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Resolved state MUST unwind the marker so the gate disarms."""
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices=()), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # Pre-seed marker — simulating a prior armed state.
        (session_dir / PIN_STALENESS_MARKER_NAME).touch()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        result = check_pin_stale_block_directive()
        assert result is None
        assert not (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_positive_detection_idempotent_marker_arming(
        self, tmp_path, monkeypatch, pact_context
    ):
        """P0: double-invocation does not error; marker stays single file."""
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        r1 = check_pin_stale_block_directive()
        r2 = check_pin_stale_block_directive()
        assert r1 == r2
        assert (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_no_session_dir_still_returns_directive_on_detection(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Marker management is best-effort; directive must still fire."""
        from session_init import check_pin_stale_block_directive
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: None)

        result = check_pin_stale_block_directive()
        assert result is not None
        assert "MUST" in result


class TestPinMemoryCommand_Grammar:
    """pin-memory.md contract assertions (cycle-8 demoted scope).

    Post-cycle-8, pin-memory.md is a thin pin-add guide. Cap enforcement
    lives in hooks/pin_caps_gate.py; interactive eviction lives in
    /PACT:prune-memory. The bulky two-step AskUserQuestion eviction flow
    and the shell-scaffolding heredoc+nonce surface are GONE. Tests here
    cover the residual informational surface: caps mentioned, refusal
    flow enumerated, hook-as-enforcer called out, cross-reference to
    prune-memory present.

    DELETED in cycle-8 commit 6 alongside the pin-memory.md rewrite:
    - test_documents_check_cli_invocation (CLI no longer invoked)
    - test_documents_step_a_three_options / test_documents_step_b_pagination_cap
      (eviction moved to /PACT:prune-memory)
    - test_documents_size_refusal_three_options (size refusal is now a
      hook deny-reason + plain-text remediation, not an AskUserQuestion)
    - test_heredoc_opener_is_always_quoted_in_code_fences (no heredoc)
    - test_heredoc_nonce_uses_python3_secrets_not_openssl (no nonce)
    """

    @pytest.fixture(scope="class")
    def pin_memory_content(self):
        path = (
            Path(__file__).parent.parent / "commands" / "pin-memory.md"
        )
        return path.read_text(encoding="utf-8")

    def test_documents_caps(self, pin_memory_content):
        """Cap numbers remain informational for curator awareness."""
        assert "12 pins maximum" in pin_memory_content
        assert "1500 characters" in pin_memory_content
        # Cycle-8: the hook is the authoritative enforcer. Command text
        # MUST direct curators away from manual bypass attempts.
        assert "MUST NOT bypass" in pin_memory_content

    def test_documents_hook_as_enforcer(self, pin_memory_content):
        """pin-memory.md must name the hook so curators know where denies
        come from (they appear as PreToolUse permissionDecision: deny,
        not as a CLI exit code)."""
        assert "pin_caps_gate" in pin_memory_content

    def test_documents_refusal_flow(self, pin_memory_content):
        """Hook deny-reasons are enumerated so curators see the exact
        actionable next step without leaving the command text."""
        assert "Pin count cap reached" in pin_memory_content
        assert "Ask the team-lead to free a pin slot" in pin_memory_content
        assert "New pin body is" in pin_memory_content
        assert "is its own pin" in pin_memory_content
        assert "Override rationale malformed" in pin_memory_content

    def test_documents_rationale_120_char_limit(self, pin_memory_content):
        """The 120-char rationale cap remains informational so curators
        self-limit before the hook denies."""
        assert "120 chars" in pin_memory_content

    def test_documents_override_grammar_example(self, pin_memory_content):
        """Exact override comment form (live CLAUDE.md:68) is preserved
        as a verbatim example. parse_pins round-trips this exact line;
        drift would break the live CLAUDE.md round-trip (see
        TestLiveClaudeMdOverrideLine_RoundTrip below)."""
        assert (
            "pin-size-override: verbatim dispatch form is load-bearing "
            "for LLM readers"
        ) in pin_memory_content

    def test_references_prune_memory_for_eviction(self, pin_memory_content):
        """Cap-count refusal must direct the curator to /PACT:prune-memory
        for interactive eviction — the command that owns that flow
        post-demotion."""
        assert "/PACT:prune-memory" in pin_memory_content

    def test_no_heredoc_scaffolding(self, pin_memory_content):
        """Regression guard: the shell-scaffolding heredoc+nonce surface
        MUST NOT reappear in pin-memory.md. Cycle-8 eliminated it by
        construction — any re-introduction (perhaps by a well-meaning
        future commit adding a "before add" validation step) would
        reopen the shell-injection surface cycle-7 hardened against.
        """
        # No heredoc markers.
        assert "<<'" not in pin_memory_content
        assert '<<"' not in pin_memory_content
        # No bash fences at all (pin-memory.md became a plain-text guide).
        assert "```bash" not in pin_memory_content
        # No CLI invocation of check_pin_caps — that's now hook-only.
        # (The CLI still exists as /PACT:prune-memory's backing, but
        # pin-memory.md does not invoke it.)
        assert "check_pin_caps.py" not in pin_memory_content
        # No retired flags.
        for flag in [
            "--new-body",
            "--body-from-stdin",
            "--has-override",
            "--override-rationale",
        ]:
            assert flag not in pin_memory_content, (
                f"Retired cycle-7 flag {flag} reappeared in pin-memory.md; "
                "cap enforcement is now hook-authoritative and the CLI is "
                "advisory-only. Remove the reference."
            )


class TestPruneMemoryCommand_Grammar:
    """/PACT:prune-memory contract assertions (cycle-8 commit 7).

    The prune flow that pin-memory.md previously embedded now lives in
    a dedicated command. prune-memory.md describes a 5-step interactive
    flow:
        1. Invoke check_pin_caps.py --status to get evictable_pins
        2. AskUserQuestion (paginated) on the list
        3. Archive the selected pin and verify it arrived; refuse the
           eviction on anything short of a verified archive
        4. Edit CLAUDE.md to remove the selected block
        5. Report (+ journal event)

    Tests here pin the informational contract (CLI invocation shape,
    pagination structure, cross-references) so structural refactors
    are caught.
    """

    @pytest.fixture(scope="class")
    def prune_memory_content(self):
        path = (
            Path(__file__).parent.parent / "commands" / "prune-memory.md"
        )
        return path.read_text(encoding="utf-8")

    def test_documents_status_cli_invocation(self, prune_memory_content):
        """prune-memory.md drives the interactive flow from the advisory
        CLI's --status output. The command must name the CLI + flag so
        readers know the JSON source."""
        assert "check_pin_caps.py" in prune_memory_content
        assert "--status" in prune_memory_content

    def test_documents_askuserquestion_flow(self, prune_memory_content):
        """The core UX is paginated AskUserQuestion over evictable_pins."""
        assert "AskUserQuestion" in prune_memory_content
        assert "evictable_pins" in prune_memory_content

    def test_documents_pagination_three_plus_one(self, prune_memory_content):
        """Pagination is 3 pins + 1 navigation slot per page (the platform
        caps AskUserQuestion at 4 options per call)."""
        assert "3 candidate pins" in prune_memory_content or "3 pins" in prune_memory_content
        assert "Show more" in prune_memory_content

    def test_documents_cancel_path(self, prune_memory_content):
        """A Cancel option is always presented so the curator can back
        out without modifying CLAUDE.md."""
        assert "Cancel" in prune_memory_content
        assert "unchanged" in prune_memory_content

    def test_documents_why_the_hook_allows_a_prune(self, prune_memory_content):
        """The hook ALLOWS the prune edit because the change adds no pin.
        This must be called out so curators understand why the same hook
        that denies adds allows evicts."""
        assert "adds no pin" in prune_memory_content
        assert "pin_caps_gate" in prune_memory_content

    def test_documents_stale_preference(self, prune_memory_content):
        """Stale pins are surfaced first in the pagination order — they
        are the safest to evict. This is an explicit UX rule."""
        assert "STALE" in prune_memory_content
        assert (
            "Prefer" in prune_memory_content
            or "stale first" in prune_memory_content.lower()
            or "stale pins first" in prune_memory_content.lower()
        )

    def test_references_pin_memory(self, prune_memory_content):
        """Cross-reference to /PACT:pin-memory so the two-command
        workflow is discoverable from either direction."""
        assert "/PACT:pin-memory" in prune_memory_content

    # Bash disables parameter expansion when ANY part of the heredoc
    # delimiter word is quoted OR escaped -- single quotes, double quotes
    # and a backslash are equivalent for this purpose.
    #
    # BOTH `<<`-guards are load-bearing and each was found by a different
    # reviewer failing the other's case. `<<<` is a HERESTRING, a distinct
    # operator, and it must not be reported as an unquoted heredoc:
    #   (?<!<) stops the match starting on the SECOND `<` of `<<<`
    #   (?!<)  stops it starting on the FIRST
    # Drop either and `cat <<<$VAR` is flagged. Drop `\\` from the
    # non-expanding set and `<<\EOF` -- which bash does NOT expand -- is
    # flagged. Three independently-authored predicates each carried
    # exactly one of these defects; this is the intersection.
    #
    # KNOWN RESIDUAL, deliberately not fixed: arithmetic left-shift inside
    # `$(( ))` is flagged as an expanding heredoc -- `x=$((1<<3))` and
    # `x=$((a<<b))` both match. It is an OVER-BLOCK, so it surfaces as a red
    # test a human investigates rather than as a hole something slips
    # through, and it is currently unreachable: this file contains no
    # arithmetic. TWO FIXES WERE TRIED AND REJECTED, recorded so neither is
    # re-attempted:
    #   (?<![<0-9])  fixes `1<<3` but NOT `a<<b` -- half the case for real
    #                added complexity.
    #   excluding a preceding identifier character fixes both and BREAKS a
    #                valid heredoc: `cat<<EOF` with no space is legal bash
    #                (measured), so a letter may legitimately precede `<<`.
    # A documented over-block on a construct that cannot currently occur is
    # a better resting place than a more complex pattern with its own
    # untested edges. This guard has already been wrong three times from
    # exactly that instinct.
    _DELIM_RE = re.compile(r"(?<!<)<<-?[ \t]*(?!<)(.)")
    _NON_EXPANDING_LEAD = ("'", '"', "\\")

    @staticmethod
    def _expanding(text):
        """Every heredoc operator in `text` whose delimiter is UNQUOTED."""
        cls = TestPruneMemoryCommand_Grammar
        return [
            m.group(0) for m in cls._DELIM_RE.finditer(text)
            if m.group(1) not in cls._NON_EXPANDING_LEAD
        ]

    # (form, expands?) -- `expands` measured against real bash, not reasoned.
    _HEREDOC_FORMS = [
        ("<<EOF", True), ("<<'EOF'", False), ('<<"EOF"', False),
        ("<<-EOF", True), ("<<-'EOF'", False), ('<<-"EOF"', False),
        ("<< EOF", True), ("<< 'EOF'", False), ('<< "EOF"', False),
        ("<<   EOF", True), ("<<-  'EOF'", False), ("<<-\t'EOF'", False),
        ("<<\\EOF", False), ("<<$VAR", True),
        # Backslash-escaped delimiters, added because they LOOK unquoted at a
        # glance and are the forms most likely to be got wrong by a future
        # edit. All three measured non-expanding.
        ("<< \\EOF", False), ("<<-  \\EOF", False), ("<<-\\EOF", False),
    ]

    def test_expanding_heredoc_predicate_matches_bash(self):
        """The PREDICATE, tested as a pure function against a fixed table.

        Split deliberately from the file scan below, because one regex was
        being asked two different questions -- "is this form expanding?"
        (a pure function) and "is the shipped file clean?" (a document
        scan). Conflating them is why three successive revisions of this
        guard each traded one error direction for another: it shipped with
        holes (`<<-EOF`, `<< EOF`, `<<   EOF` all expand and were
        permitted), was then corrected into an OVER-BLOCK that banned
        `<<"EOF"` under a message calling it expanding when bash does not
        expand it, and a scoped fix after that could pass while scanning
        nothing.

        `expands` in the table is MEASURED against real bash, not derived
        from the regex -- deriving it would make this test agree with the
        implementation by construction and assert nothing.
        """
        # A REQUIRED-SET over EVERY form, not a count and not a subset.
        # Measured against five mutations; only this shape gets all five
        # right (count: 2 wrong, 7-form subset: 1, count+subset: 1):
        #   delete a listed row      -> caught (a count catches this too)
        #   SWAP one form for another-> caught; A COUNT PASSES IT at 14
        #                               while a form silently loses coverage
        #   add a legitimate 15th    -> ALLOWED; a count wrongly fails it
        #   empty table              -> caught
        # A count conflates deletion with addition; a subset cannot see the
        # deletion of a row outside it. Enumerating every form makes the
        # table's contents the contract and leaves it free to grow.
        #
        # This literal is deliberately NOT derived from _HEREDOC_FORMS.
        # `{f for f, _ in TABLE} - {f for f, _ in TABLE}` is empty by
        # construction -- a self-referential guard that can never fail.
        required = frozenset({
            "<<EOF", "<<-EOF", "<< EOF", "<<   EOF", "<<$VAR",      # expanding
            "<<'EOF'", '<<"EOF"', "<<-'EOF'", '<<-"EOF"',           # quoted
            "<< 'EOF'", '<< "EOF"', "<<-  'EOF'", "<<-\t'EOF'",     # quoted + space/tab
            "<<\\EOF", "<< \\EOF", "<<-  \\EOF", "<<-\\EOF",         # escaped
        })
        missing = required - {form for form, _ in self._HEREDOC_FORMS}
        assert not missing, (
            f"heredoc form table lost coverage of: {sorted(missing)}. Four "
            "of the five expanding forms listed were real holes in a "
            "shipped revision of this guard; `<<EOF` is the control the "
            "original regex caught correctly. The quoted and escaped forms "
            "pin cases a revision wrongly banned. Adding forms is free; "
            "removing one means re-deriving why it stopped mattering."
        )
        for form, expands in self._HEREDOC_FORMS:
            flagged = bool(self._expanding(f"cat {form}\npayload\nEOF\n"))
            assert flagged == expands, (
                f"{form!r}: bash expands={expands} but the guard "
                f"{'flags' if flagged else 'permits'} it — "
                + ("a HOLE (expanding form permitted)" if expands
                   else "an OVER-BLOCK (safe quoted form banned)")
            )

    def test_shipped_file_has_no_expanding_heredoc(self, prune_memory_content):
        """The SCAN. prune-memory.md's own fenced shell must be clean.

        Reads the WHOLE document. There is deliberately no fence
        extraction, and removing that component is the point rather than a
        simplification: every prior defect in this guard lived in the part
        trying to be precise about WHERE to look. It was a total `<<` ban,
        then a holed regex, then a ```bash-scoped scan that passed while
        scanning nothing when the fences were renamed. A component that
        has been wrong three times and can fail SILENTLY is worth deleting,
        not narrowing a fourth time.

        ACCEPTED WART, so nobody 'fixes' it later: documenting the banned
        form unquoted in this file's prose WILL fail this test. That is an
        over-block -- a red test resolved in minutes by quoting the example
        -- and it is the deliberate price of removing a failure mode that
        reported success forever. Do not reintroduce fence-scoping to
        soften it.
        """
        assert "<<'JSON'" in prune_memory_content, (
            "the legitimate quoted heredoc is missing from prune-memory.md — "
            "this guard's subject is gone, so a clean result below would mean "
            "nothing. POSITIVE CONTROL: it proves the scan reached the real "
            "surface, which merely asserting the file is non-empty would not."
        )
        bad = self._expanding(prune_memory_content)
        assert bad == [], (
            f"unquoted (expanding) heredoc delimiter(s) in prune-memory.md: "
            f"{bad}. An unquoted delimiter re-enables shell expansion inside "
            "the payload — quote it as <<'DELIM'."
        )


class TestParsePinsVsDetectStaleEntries_Agreement:
    """Property-test: parse_pins.is_stale agrees with detect_stale_entries
    on shared fixtures.

    Mechanical rule: detect_stale_entries uses a regex walker to find pins
    containing date-matched staleness; parse_pins uses STALE marker
    presence. For pins ALREADY carrying `<!-- STALE: Last relevant ... -->`
    markers, both parsers must agree the entry is stale — otherwise the
    twin-parsing architecture diverges (audit sub-YELLOW).
    """

    @pytest.mark.parametrize("n_pins,stale_indices", [
        (0, set()),
        (1, set()),
        (1, {0}),
        (3, set()),
        (3, {0}),
        (3, {0, 1}),
        (3, {0, 1, 2}),
        (5, {2}),
    ])
    def test_stale_marker_detection_agrees(self, n_pins, stale_indices):
        from pin_caps import parse_pins
        from helpers import make_pin_entry, make_pinned_section
        # Build content with explicit STALE markers — detect_stale_entries
        # skips already-marked entries, so our axis of comparison is
        # "pin_caps.is_stale == True iff STALE marker present".
        entries = [
            make_pin_entry(
                title=f"Pin {i}",
                body_chars=4,
                stale_date="2026-01-01" if i in stale_indices else None,
            )
            for i in range(n_pins)
        ]
        content = make_pinned_section(entries) if entries else ""
        pins = parse_pins(content)
        actual_stale = {i for i, p in enumerate(pins) if p.is_stale}
        assert actual_stale == stale_indices, (
            f"parse_pins stale set {actual_stale} disagrees with "
            f"expected {stale_indices}"
        )
        # Cross-parser agreement: detect_stale_entries skips entries already
        # carrying a STALE marker, so on marked fixtures it MUST return the
        # empty list — both parsers agree "marked entries are finalized
        # stale, no further flagging needed."
        from staleness import detect_stale_entries
        assert len(detect_stale_entries(content)) == 0, (
            "detect_stale_entries flagged marker-carrying entries; it "
            "should skip them to avoid double-marking"
        )


class TestLiveClaudeMdOverrideLine_RoundTrip:
    """The override line on live CLAUDE.md:68 must round-trip through
    parse_pins unchanged. Regression guard against regex drift."""

    LIVE_LINE = (
        "<!-- pinned: 2026-04-11, pin-size-override: "
        "verbatim dispatch form is load-bearing for LLM readers -->"
    )
    LIVE_RATIONALE = "verbatim dispatch form is load-bearing for LLM readers"

    def test_round_trip_preserves_rationale(self):
        from pin_caps import parse_pins
        content = f"{self.LIVE_LINE}\n### Canonical Task Form\nbody\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].override_rationale == self.LIVE_RATIONALE
        assert pins[0].date_comment == self.LIVE_LINE

    def test_round_trip_inside_multi_pin_context(self):
        from pin_caps import parse_pins
        content = (
            "<!-- pinned: 2026-04-01 -->\n"
            "### Other Pin\n"
            "body a\n\n"
            f"{self.LIVE_LINE}\n"
            "### Canonical Task Form\n"
            "body b\n"
        )
        pins = parse_pins(content)
        assert len(pins) == 2
        assert pins[0].override_rationale is None
        assert pins[1].override_rationale == self.LIVE_RATIONALE
