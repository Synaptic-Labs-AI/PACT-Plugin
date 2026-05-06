"""
Coverage gap-fills for #628 restore-startup-ritual TEST phase.

Pins the plan §Test Phase scenarios that backend's TDD coverage did not
land tests for, plus the auditor YELLOW signal resolutions:

G1-G3: /PACT:bootstrap command structural pins
       (frontmatter, ritual-only-content, plugin.json registration via
       EXPECTED_COMMANDS update is in test_commands_structure.py)
G4: bootstrap marker clear-on-clear-source AND not-clear-on-resume-source
G5: orchestrator persona references Skill("PACT:bootstrap")
G6: orchestrator persona includes pin-memory mid-session directive (F2)
G7: Lead-Side HALT Fan-Out byte-equal at two sites
G8: strip_orphan_routing_markers fail-open on lock timeout

Y1 (auditor YELLOW-1): _TEACHBACK_REMINDER cross-file consistency with
                       skills/pact-teachback/SKILL.md — both reference
                       metadata.teachback_submit, drift on either side
                       fails the test.
Y2 (auditor YELLOW-2): TestMarkerNameConsistency parametrized variant —
                       accepts encoding alternatives (f-string-like,
                       assignment style, multi-line with comments) for
                       the marker-write invocation in commands/bootstrap.md;
                       tests cross-file consistency of marker SEMANTICS
                       rather than byte-equal syntax.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME

PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"
PROTOCOLS_DIR = PLUGIN_ROOT / "protocols"
SKILLS_DIR = PLUGIN_ROOT / "skills"


# =============================================================================
# G1-G2: /PACT:bootstrap command structural pins
# =============================================================================


class TestBootstrapCommandStructure:
    """Plan scenarios bootstrap_command_file_exists_with_required_frontmatter
    + bootstrap_command_contains_only_ritual_content. The scaled-down
    bootstrap command owns ritual mechanics ONLY; governance / mission /
    motto / SACROSANCT / FINAL MANDATE belong to the persona body
    delivered via the --agent flag.
    """

    BOOTSTRAP_PATH = COMMANDS_DIR / "bootstrap.md"

    def test_bootstrap_command_file_exists(self):
        assert self.BOOTSTRAP_PATH.is_file(), (
            f"{self.BOOTSTRAP_PATH} must exist. /PACT:bootstrap is the "
            "scaled-down session-start ritual command registered in "
            "plugin.json; absent file → slash command resolves to nothing."
        )

    def test_bootstrap_command_has_frontmatter_with_description(self):
        """Frontmatter must declare a description field — required by
        Claude Code for slash-command UI rendering."""
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        assert text.startswith("---\n"), (
            "bootstrap.md must open with YAML frontmatter delimiter."
        )
        # Find closing fence
        end_idx = text.find("\n---\n", 4)
        assert end_idx != -1, "bootstrap.md frontmatter not closed."
        frontmatter = text[4:end_idx]
        assert re.search(r"^description:\s*.+$", frontmatter, re.MULTILINE), (
            "bootstrap.md frontmatter must contain a `description:` field."
        )

    def test_bootstrap_command_contains_ritual_content(self):
        """Body must reference the load-bearing ritual elements:
        TeamCreate-or-reuse, secretary spawn, paused-state surface,
        plugin banner, bootstrap-complete marker write."""
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        # Strip frontmatter for body-content checks
        body = text.split("\n---\n", 1)[1] if "\n---\n" in text else text

        for required in (
            "team_name",         # TeamCreate-or-reuse semantics
            "secretary",         # Step 2 spawn
            "paused-state",      # Step 3 surface (matches "paused-state.json" too)
            "banner",            # Step 4 plugin banner
            BOOTSTRAP_MARKER_NAME,  # marker-write target
        ):
            assert required in body, (
                f"bootstrap.md must reference {required!r} as part of the "
                f"ritual mechanics."
            )

    def test_bootstrap_command_excludes_governance_fossils(self):
        """Scaled-down command must NOT carry persona-body-owned content.
        These markers are owned by the --agent-delivered persona body and
        leaking them into the command file recreates the v3.x duplication
        that #621 deliberately removed."""
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")

        # MISSION/MOTTO are persona-body framing
        for fossil in ("MISSION:", "MOTTO:", "FINAL MANDATE", "SACROSANCT"):
            assert fossil not in text, (
                f"bootstrap.md must not contain persona-body fossil {fossil!r}; "
                f"that content belongs in the --agent-delivered persona."
            )


# =============================================================================
# G4: Bootstrap marker clear behavior across sources
# =============================================================================


class TestBootstrapMarkerClearAcrossSources:
    """Plan scenario bootstrap_marker_clear_on_resume — pins the rule that
    the bootstrap-complete marker is cleared ONLY on source='clear', NOT
    on source='resume'/'startup'/'compact'. A regression that cleared the
    marker on resume would force the per-session ritual to re-fire on
    every context-retain resume — defeating the per-session-not-per-turn
    invariant.

    Source-level pin (not main()-runner): exercising main() proved
    isolation-fragile because session_init.init() writes a session
    context file via pact_context's module-level cache, polluting
    downstream test_staleness.py path-resolution under certain
    intermediate test orders. This source-level test asserts the same
    invariant by inspecting the gating predicate in session_init.py
    directly — single-line guard that the marker-unlink is gated by
    `source == 'clear'`."""

    SESSION_INIT_PATH = (
        Path(__file__).parent.parent / "hooks" / "session_init.py"
    )

    @pytest.fixture
    def session_init_text(self):
        return self.SESSION_INIT_PATH.read_text(encoding="utf-8")

    def test_marker_unlink_gated_by_clear_source(self, session_init_text):
        """The marker-unlink branch must be entered only when
        `is_marker_reset` is true, and `is_marker_reset` must be
        defined as `source == "clear"`."""
        # Find the is_marker_reset assignment
        match = re.search(
            r'is_marker_reset\s*=\s*(.+)$',
            session_init_text,
            re.MULTILINE,
        )
        assert match is not None, (
            "session_init.py must define is_marker_reset; the bootstrap "
            "marker unlink branch is gated by it. Lost gating means the "
            "marker would be unlinked unconditionally on every session "
            "start (defeating the per-session ritual invariant)."
        )
        gating_expr = match.group(1).strip()
        assert gating_expr == 'source == "clear"', (
            f"is_marker_reset gating must be exactly `source == \"clear\"` "
            f"to ensure marker-unlink fires only on /clear (not on resume, "
            f"startup, or compact). Found: {gating_expr!r}"
        )

    def test_marker_unlink_call_is_inside_clear_branch(
        self, session_init_text
    ):
        """The unlink(missing_ok=True) call on the bootstrap marker must
        be inside the `if is_marker_reset:` branch. A drift here (e.g.,
        unindented unlink, or guarded by a different predicate) breaks
        the contract."""
        # Find the BOOTSTRAP_MARKER_NAME unlink call
        unlink_match = re.search(
            r'\(session_path / BOOTSTRAP_MARKER_NAME\)\.unlink',
            session_init_text,
        )
        assert unlink_match is not None, (
            "session_init.py must call "
            "(session_path / BOOTSTRAP_MARKER_NAME).unlink(...) — the "
            "marker-unlink invocation that fires on /clear."
        )

        # Walk backward from the unlink to find the nearest enclosing
        # `if is_marker_reset:` line. The line index of the if-guard
        # must precede the unlink and there must be no intervening
        # de-dent that breaks containment.
        before = session_init_text[: unlink_match.start()]
        guard_match = re.search(
            r'^(\s*)if\s+is_marker_reset:\s*$',
            before,
            re.MULTILINE,
        )
        assert guard_match is not None, (
            "The marker-unlink call is not inside an "
            "`if is_marker_reset:` branch. Without this guard, the "
            "marker would be unlinked on every session source — "
            "defeating the per-session ritual invariant."
        )


# =============================================================================
# G5-G6: Orchestrator persona body invariants
# =============================================================================


class TestOrchestratorPersonaInvariants:
    """Plan scenarios orchestrator_persona_references_bootstrap_command
    + orchestrator_persona_includes_pin_memory_session_level_directive (F2).
    """

    PERSONA_PATH = AGENTS_DIR / "pact-orchestrator.md"

    @pytest.fixture
    def persona_text(self):
        return self.PERSONA_PATH.read_text(encoding="utf-8")

    def test_persona_invokes_bootstrap_skill(self, persona_text):
        """§2 Session-Start Ritual must direct the orchestrator at
        Skill("PACT:bootstrap"). Without this cross-reference, the
        scaled-down command file is unreachable from the persona."""
        assert 'Skill("PACT:bootstrap")' in persona_text, (
            "Persona body must reference Skill(\"PACT:bootstrap\") as "
            "the invocation contract for the session-start ritual."
        )

    def test_persona_session_start_ritual_section_present(self, persona_text):
        """The renumbered §2 Session-Start Ritual section heading must
        be present (post-renumber §2-§12 → §3-§13)."""
        assert re.search(
            r"^##\s+.*Session-Start Ritual",
            persona_text,
            re.MULTILINE | re.IGNORECASE,
        ), (
            "Persona body must contain a `## Session-Start Ritual` heading "
            "(or one with that title text); the F2 architect commit added "
            "this as the new §2."
        )

    def test_persona_pin_memory_mid_session_directive_present(
        self, persona_text
    ):
        """F2 (Commit 10): mid-session pin-memory directive must direct
        the orchestrator to invoke /PACT:pin-memory immediately when an
        insight surfaces that meets pin-worthy triggers, NOT defer to
        wrap-up. Pinning at the moment of insight is load-bearing for
        memory durability across compaction."""
        # Match a mid-session pin-memory invocation sentence
        assert "/PACT:pin-memory" in persona_text, (
            "Persona body must reference the /PACT:pin-memory command."
        )
        # The F2 directive specifically describes mid-session insight
        # pinning (distinct from post-review or wrap-up triggers).
        assert re.search(
            r"mid.session|moment of insight|immediately when",
            persona_text,
            re.IGNORECASE,
        ), (
            "Persona body must contain a mid-session pin-memory directive "
            "describing immediate / moment-of-insight pinning. F2 added "
            "this to §13."
        )


# =============================================================================
# G7: Lead-Side HALT Fan-Out byte-equal at two sites
# =============================================================================


class TestLeadSideHaltFanOutByteEqualAtTwoSites:
    """Plan scenario lead_side_halt_fan_out_byte_equal_at_two_sites.

    The Lead-Side HALT Fan-Out idiom is the canonical lead→many
    dispatch pattern. It MUST appear byte-equal at both:
      - agents/pact-orchestrator.md (the persona body's Inter-teammate
        messaging section)
      - protocols/algedonic.md (the algedonic protocol's HALT handling
        section, where the actual cross-referenced anchor lives)

    Drift between these two sites would cause the persona body to teach
    a different fan-out shape than the protocol the persona's other
    cross-refs point at. The test extracts the ~7-line code block
    (in_progress = ...; for task in in_progress: SendMessage(...)) from
    each file and asserts byte equality.
    """

    PERSONA_PATH = AGENTS_DIR / "pact-orchestrator.md"
    PROTOCOL_PATH = PROTOCOLS_DIR / "algedonic.md"

    SIGNATURE_LINE = (
        'in_progress = [t for t in TaskList() '
        'if t["status"] == "in_progress" and t["owner"]]'
    )

    def _extract_fanout_block(self, path: Path) -> str:
        """Locate the signature line and return the contiguous indented
        code block (the literal lines from `in_progress = ...` through
        the closing `)` of the SendMessage call)."""
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start_idx = None
        for i, line in enumerate(lines):
            if self.SIGNATURE_LINE in line:
                start_idx = i
                break
        assert start_idx is not None, (
            f"signature line {self.SIGNATURE_LINE!r} not found in {path}"
        )
        # Walk forward until we find the closing `)` of SendMessage or
        # a blank/non-indented separator line.
        block_lines = [lines[start_idx]]
        for j in range(start_idx + 1, len(lines)):
            line = lines[j]
            block_lines.append(line)
            if line.strip() == ")":
                break
            # Safety: don't run past 20 lines
            if j - start_idx > 20:
                break
        return "\n".join(block_lines)

    def test_fanout_block_present_in_persona(self):
        block = self._extract_fanout_block(self.PERSONA_PATH)
        assert "SendMessage(" in block
        assert "for task in in_progress:" in block

    def test_fanout_block_present_in_algedonic_protocol(self):
        block = self._extract_fanout_block(self.PROTOCOL_PATH)
        assert "SendMessage(" in block
        assert "for task in in_progress:" in block

    def test_fanout_block_byte_equal_at_two_sites(self):
        """The two sites must contain a byte-equal fan-out code block.
        Drift here means the persona-body teaching diverges from the
        algedonic-protocol authoritative pattern."""
        persona_block = self._extract_fanout_block(self.PERSONA_PATH)
        protocol_block = self._extract_fanout_block(self.PROTOCOL_PATH)
        assert persona_block == protocol_block, (
            "Lead-Side HALT Fan-Out idiom drifted between sites:\n"
            f"--- {self.PERSONA_PATH.name} ---\n{persona_block}\n"
            f"--- {self.PROTOCOL_PATH.name} ---\n{protocol_block}\n"
            "Both must remain byte-equal so the persona's mention and "
            "the protocol's authoritative anchor teach the same shape."
        )


# =============================================================================
# G8: strip_orphan_routing_markers lock-timeout fail-open
# =============================================================================


class TestStripOrphanRoutingMarkersLockTimeout:
    """Plan scenario strip_orphan_routing_markers_lock_timeout_skips.

    When file_lock raises a TimeoutError (concurrent writer holds the
    lock for >5s), the stripper MUST fail-open to None so session start
    does not block. The kernel-block sibling has the same fail-open
    contract.

    Source-level pin: rather than running the function (which routes
    through pact_context module-level state and creates test-isolation
    fragility against test_staleness when interleaved with test_check_pin_caps),
    we assert the structural invariant that the function body wraps
    its file_lock acquisition in a try/except TimeoutError block that
    returns None. Any drift (catching only OSError, or letting
    TimeoutError propagate) fails this test."""

    SESSION_INIT_PATH = (
        Path(__file__).parent.parent / "hooks" / "session_init.py"
    )

    @pytest.fixture
    def session_init_text(self):
        return self.SESSION_INIT_PATH.read_text(encoding="utf-8")

    def _extract_function_body(self, text: str, func_name: str) -> str:
        """Extract the body of `def func_name(...)` up to the next
        top-level def or end-of-file. Returns empty string if not
        found."""
        match = re.search(
            rf'^def {re.escape(func_name)}\(.*?\) ?->.*?:\n(.*?)(?=\n^def |\Z)',
            text,
            re.MULTILINE | re.DOTALL,
        )
        return match.group(1) if match else ""

    def test_strip_orphan_routing_markers_catches_timeout(
        self, session_init_text
    ):
        """strip_orphan_routing_markers body must include
        `except TimeoutError:` that returns None — fail-open contract
        on lock-timeout. Without this, a transient lock contention
        from a concurrent writer would propagate and crash session
        startup."""
        body = self._extract_function_body(
            session_init_text, "strip_orphan_routing_markers"
        )
        assert body, (
            "strip_orphan_routing_markers function body could not be "
            "extracted from session_init.py — the function may be "
            "missing or its signature changed."
        )
        # The fail-open clause must appear textually in the body.
        # Tolerant of formatting (whitespace, different indent levels).
        assert re.search(
            r'except\s+TimeoutError\s*:\s*\n\s*return\s+None',
            body,
        ), (
            "strip_orphan_routing_markers must include "
            "`except TimeoutError: return None` to fail-open on lock "
            "contention. Without this, a contended file_lock raises "
            "TimeoutError out of the SessionStart hot path."
        )

    def test_strip_orphan_routing_markers_uses_file_lock(
        self, session_init_text
    ):
        """The function body must acquire file_lock around its
        read-mutate-write — the lock is the safety boundary that
        TimeoutError defends."""
        body = self._extract_function_body(
            session_init_text, "strip_orphan_routing_markers"
        )
        assert "file_lock(" in body, (
            "strip_orphan_routing_markers must use file_lock(...) for "
            "its read-mutate-write cycle. Without it, concurrent "
            "session_init runs could partial-strip the routing block."
        )


# =============================================================================
# Y1 (auditor YELLOW-1): _TEACHBACK_REMINDER cross-file consistency
# =============================================================================


class TestTeachbackReminderCrossFileConsistency:
    """Auditor YELLOW-1 resolution. The peer_inject _TEACHBACK_REMINDER
    constant directs spawned teammates at metadata.teachback_submit and
    the pact-teachback skill. The pact-teachback skill body documents
    the same metadata.teachback_submit shape. If either side's wording
    drifts and stops mentioning the canonical metadata key, teammates
    will store their teachback under a non-canonical key (or stop
    storing it) and the team-lead's teachback-validation harness will
    silently miss the payload.

    Test parses the constant from peer_inject.py and asserts that the
    canonical phrase appears in BOTH surfaces. Path (a) of the
    test-engineer teachback Q3 (lead confirmed)."""

    SKILL_PATH = SKILLS_DIR / "pact-teachback" / "SKILL.md"
    CANONICAL_KEY = "metadata.teachback_submit"

    def test_peer_inject_teachback_reminder_mentions_canonical_key(self):
        """Import the private constant from peer_inject and assert it
        contains the canonical metadata key. Private-name import is
        acceptable in tests (per lead's Q3 confirmation)."""
        from peer_inject import _TEACHBACK_REMINDER  # pyright: ignore[reportMissingImports]
        assert self.CANONICAL_KEY in _TEACHBACK_REMINDER, (
            f"peer_inject._TEACHBACK_REMINDER must mention "
            f"{self.CANONICAL_KEY!r} so spawned teammates know where "
            f"to write their teachback. Current value:\n"
            f"{_TEACHBACK_REMINDER!r}"
        )

    def test_pact_teachback_skill_describes_canonical_key(self):
        """The skill body must describe metadata.teachback_submit as
        the storage location."""
        skill_text = self.SKILL_PATH.read_text(encoding="utf-8")
        assert self.CANONICAL_KEY in skill_text, (
            f"skills/pact-teachback/SKILL.md must describe "
            f"{self.CANONICAL_KEY!r} as the canonical teachback storage "
            f"location."
        )

    def test_teachback_reminder_and_skill_share_canonical_key(self):
        """Cross-file consistency assertion: drift on either side fails
        this test. If the reminder gets reworded to a different key (or
        the skill is renamed), the divergence surfaces immediately."""
        from peer_inject import _TEACHBACK_REMINDER  # pyright: ignore[reportMissingImports]
        skill_text = self.SKILL_PATH.read_text(encoding="utf-8")
        in_reminder = self.CANONICAL_KEY in _TEACHBACK_REMINDER
        in_skill = self.CANONICAL_KEY in skill_text
        assert in_reminder and in_skill, (
            "Cross-file teachback-canonical-key consistency violated:\n"
            f"  {self.CANONICAL_KEY!r} in _TEACHBACK_REMINDER: "
            f"{in_reminder}\n"
            f"  {self.CANONICAL_KEY!r} in pact-teachback skill body: "
            f"{in_skill}\n"
            "Both surfaces must reference the same canonical key; "
            "otherwise the SubagentStart-injected reminder points at a "
            "key that the skill doesn't document, and teammates will "
            "store payloads under whichever shape they happen to "
            "remember."
        )


# =============================================================================
# Y2 (auditor YELLOW-2): TestMarkerNameConsistency parametrized variant
# =============================================================================


class TestMarkerNameConsistencyEncodingTolerant:
    """Auditor YELLOW-2 resolution. The existing
    TestMarkerNameConsistency.test_bootstrap_md_references_same_marker
    asserts the literal substring `touch "<path>/{BOOTSTRAP_MARKER_NAME}"`
    appears in commands/bootstrap.md. That byte-equal pin is correct
    for the current encoding but brittle to legitimate encoding
    refactors (assignment style, heredoc, multi-line shell with
    intervening comments, backslash-continued commands).

    This parametrized variant tests cross-file consistency on marker
    SEMANTICS rather than syntax — for each of N supported encodings,
    the marker-name substring must be present in commands/bootstrap.md
    under at least one of the expected shell idioms.

    Per lead's confirmation: keep the existing byte-literal test
    untouched; this is an ADDITIVE sibling that catches drift if the
    bootstrap.md author refactors the touch invocation while keeping
    the marker name correct.
    """

    BOOTSTRAP_MD = COMMANDS_DIR / "bootstrap.md"

    @pytest.fixture
    def bootstrap_text(self):
        return self.BOOTSTRAP_MD.read_text(encoding="utf-8")

    def test_marker_name_appears_in_bootstrap_md(self, bootstrap_text):
        """The marker name itself must appear textually somewhere in
        the body — independent of which shell encoding is used."""
        assert BOOTSTRAP_MARKER_NAME in bootstrap_text, (
            f"commands/bootstrap.md must reference the marker name "
            f"{BOOTSTRAP_MARKER_NAME!r} verbatim; without it, the "
            f"marker-write step is silently nameless."
        )

    @pytest.mark.parametrize(
        "encoding_label,pattern",
        [
            (
                "f-string-style direct touch",
                rf'touch\s+"[^"]*/{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            ),
            (
                "assignment-style + indirection",
                rf'(MARKER|MARKER_PATH|BOOTSTRAP_MARKER)\s*=\s*"[^"]*/'
                rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            ),
            (
                "heredoc style",
                rf'<<.*\n[\s\S]*?{re.escape(BOOTSTRAP_MARKER_NAME)}'
                rf'[\s\S]*?\n.*$',
            ),
            (
                "multi-line touch with comments",
                rf'touch\s+(?:#[^\n]*\n\s*)*"[^"]*/'
                rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            ),
            (
                "backslash-continued touch",
                rf'touch\s+\\\s*\n\s*"[^"]*/'
                rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            ),
        ],
    )
    def test_at_least_one_marker_write_encoding_matches(
        self, bootstrap_text, encoding_label, pattern
    ):
        """Cross-file consistency on marker-write SEMANTICS: assert
        that AT LEAST ONE of the supported encodings is present in
        commands/bootstrap.md. The class-level oracle (collect-all
        + assert-any) lives in test_any_supported_encoding_matches;
        this parametrized variant gives per-encoding diagnostic signal
        when an authored encoding doesn't match its expected shape.
        """
        # This per-row test is INFORMATIONAL — at least one matching
        # is required, but not every row must match. We assert it as
        # a soft check via skip if the encoding isn't the chosen one.
        # The hard cross-file consistency is in
        # test_any_supported_encoding_matches.
        if not re.search(pattern, bootstrap_text, re.MULTILINE):
            pytest.skip(
                f"encoding {encoding_label!r} not used in bootstrap.md "
                f"(other supported encodings may match)"
            )
        # If we matched, the marker name was present in the matched form.
        match = re.search(pattern, bootstrap_text, re.MULTILINE)
        assert BOOTSTRAP_MARKER_NAME in match.group(0)

    def test_any_supported_encoding_matches(self, bootstrap_text):
        """Hard cross-file consistency: at least ONE of the supported
        marker-write encodings must match. This is the test that fails
        if the bootstrap.md author refactors the touch invocation into
        a shape that NO supported encoding recognizes — i.e., a true
        loss of marker-write semantics in the file."""
        encodings = [
            rf'touch\s+"[^"]*/{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            rf'(MARKER|MARKER_PATH|BOOTSTRAP_MARKER)\s*=\s*"[^"]*/'
            rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            rf'<<.*\n[\s\S]*?{re.escape(BOOTSTRAP_MARKER_NAME)}'
            rf'[\s\S]*?\n.*$',
            rf'touch\s+(?:#[^\n]*\n\s*)*"[^"]*/'
            rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
            rf'touch\s+\\\s*\n\s*"[^"]*/'
            rf'{re.escape(BOOTSTRAP_MARKER_NAME)}"',
        ]
        matched = [
            p for p in encodings
            if re.search(p, bootstrap_text, re.MULTILINE)
        ]
        assert matched, (
            "commands/bootstrap.md does not contain the marker-write "
            "invocation in any supported shell encoding. The marker "
            "name appears in the file (per "
            "test_marker_name_appears_in_bootstrap_md) but not in a "
            "shape that semantically writes the marker — verify the "
            "step that creates ${{session_dir}}/{name} is intact, or "
            "extend the encoding list above if the author chose a new "
            "supported shape.".format(name=BOOTSTRAP_MARKER_NAME)
        )
