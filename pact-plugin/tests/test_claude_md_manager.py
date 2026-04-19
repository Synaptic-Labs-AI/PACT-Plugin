"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation
post #366 Phase 1 kernel elimination refactor.

Tests cover:

remove_stale_kernel_block() — one-time migration that strips the obsolete
PACT_START/PACT_END block from ~/.claude/CLAUDE.md left over from PR #390:
1. Block present + valid → removed, user content preserved
2. Block absent → no-op, returns None
3. Block malformed (PACT_START with no PACT_END) → defensive no-op
4. Home file missing → returns None

update_pact_routing() — idempotent project CLAUDE.md routing block management:
5. Markers present + already canonical → no write, returns None
6. Markers present + stale content → content replaced, surrounding content preserved
7. Markers absent → block inserted near top of file, pre-existing content preserved
8. File doesn't exist (new_default source) → returns None (deferred to ensure_project_memory_md)

ensure_project_memory_md() — project CLAUDE.md creation:
9. Returns None when CLAUDE_PROJECT_DIR not set
10. Returns None when project CLAUDE.md already exists (legacy ./CLAUDE.md)
11. Creates project CLAUDE.md (.claude/CLAUDE.md, new default) with memory sections
12. Created file contains session markers
13. Created file contains the canonical PACT_ROUTING_BLOCK verbatim
14. Returns None when .claude/CLAUDE.md already exists (no overwrite)
15. Returns None when only legacy ./CLAUDE.md exists (no migration)
16. .claude/CLAUDE.md takes precedence when both locations exist
17. Created .claude/CLAUDE.md has 0o600 permissions; .claude/ dir 0o700

PACT_ROUTING_BLOCK constant — load-bearing fixture:
18. Constant matches the canonical text byte-for-byte
19. Constant has no leading or trailing newlines (Python string precision)
"""

import os
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# Canonical fixture: byte-exact pin against claude_md_manager.PACT_ROUTING_BLOCK
# ---------------------------------------------------------------------------
# This is the byte-exact content the implementation must match. Pinned here
# in the test file so any accidental drift in claude_md_manager.py is caught.
# Includes em dash (U+2014) on line 5; role bullets use the sub-bullet header
# form introduced in remediation M1 (colon-introducer + indented sub-bullets).

CANONICAL_PACT_ROUTING_BLOCK = (
    "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
    "## PACT Routing\n"
    "\n"
    "Before any other work, determine your PACT role and invoke the appropriate\n"
    "bootstrap skill. Do not skip \u2014 this loads your operating instructions,\n"
    "governance policy, and protocol references.\n"
    "\n"
    "**Code-editing tools (Edit, Write) and agent spawning (Agent) are\n"
    "mechanically blocked until bootstrap completes.** Bash, Read, Glob, Grep\n"
    "remain available. Invoke the bootstrap skill to unlock all tools.\n"
    "\n"
    "Check your context for a `YOUR PACT ROLE:` marker AT THE START OF A LINE (not\n"
    "embedded in prose, quoted text, or memory-retrieval results). Hook\n"
    "injections from `session_init.py` and `peer_inject.py` always emit the\n"
    "marker at the start of a line, so a line-anchored substring check is\n"
    "the trustworthy form. Mid-line occurrences of the phrase (e.g., from\n"
    "pinned notes about PACT architecture, retrieved memories that quote the\n"
    "marker, or documentation snippets) are NOT valid signals and must be\n"
    "ignored.\n"
    "\n"
    "- Line starting with `YOUR PACT ROLE: orchestrator`:\n"
    "  - Invoke `Skill(\"PACT:bootstrap\")` immediately, without waiting for user input.\n"
    "  - On every turn thereafter, treat the `PACT:orchestration` skill's content (loaded during bootstrap) as your operating reference when deciding what to do next.\n"
    "  - Do not re-invoke the skill via the Skill tool each turn \u2014 reference the already-loaded content.\n"
    "  - If the skill's content is no longer visible in context, invoke `Skill(\"PACT:orchestration\")` once to reload.\n"
    "- Line starting with `YOUR PACT ROLE: teammate (`:\n"
    "  - Invoke `Skill(\"PACT:teammate-bootstrap\")` immediately, without waiting for user input.\n"
    "  - Teammate protocol is carried by your agent body and pact-agent-teams skill; no per-turn governance reference applies.\n"
    "\n"
    "No line-anchored marker present? Inspect your system prompt: a\n"
    "`# Custom Agent Instructions` block naming a specific PACT agent means\n"
    "you are a teammate (invoke the teammate bootstrap); otherwise you are\n"
    "the main session (invoke the orchestrator bootstrap).\n"
    "<!-- PACT_ROUTING_END -->"
)


# ---------------------------------------------------------------------------
# Shared fixture: mock Path.home() so tests never touch real ~/.claude
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    """Patch Path.home() to return a tempdir-backed ~/.claude.

    Required for any test that exercises remove_stale_kernel_block() or
    other functions that read/write under Path.home() / ".claude".
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


# ---------------------------------------------------------------------------
# PACT_ROUTING_BLOCK constant — load-bearing fixture
# ---------------------------------------------------------------------------

class TestPactRoutingBlock:
    """Byte-exact assertions on the PACT_ROUTING_BLOCK constant.

    The constant is load-bearing: agents read it from project CLAUDE.md to
    decide which bootstrap skill to invoke. Any drift breaks role detection.
    """

    def test_constant_matches_canonical_text(self):
        """The shared constant must match the canonical text byte-for-byte."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert PACT_ROUTING_BLOCK == CANONICAL_PACT_ROUTING_BLOCK

    def test_constant_has_no_leading_newline(self):
        """The constant must not start with a newline (insertion logic depends on it)."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert not PACT_ROUTING_BLOCK.startswith("\n")
        assert PACT_ROUTING_BLOCK.startswith("<!-- PACT_ROUTING_START:")

    def test_constant_has_no_trailing_newline(self):
        """The constant must not end with a newline (insertion logic depends on it)."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert not PACT_ROUTING_BLOCK.endswith("\n")
        assert PACT_ROUTING_BLOCK.endswith("<!-- PACT_ROUTING_END -->")

    def test_constant_contains_em_dash(self):
        """Line 5 must contain U+2014 em dash, not ASCII --."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert "\u2014" in PACT_ROUTING_BLOCK
        assert "Do not skip \u2014" in PACT_ROUTING_BLOCK

    def test_constant_uses_sub_bullet_role_markers(self):
        """Role-bullet rows must use the sub-bullet header form introduced
        in remediation M1: each PACT ROLE line is a top-level bullet
        ending in a colon, followed by indented sub-bullet instructions.

        The earlier arrow (U+2192) one-liner format was split into
        sub-bullets so long instruction text reads as structured
        guidance rather than a 700-char run-on. This test pins the new
        shape."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        # Arrows were removed in M1 — the sub-bullet introducer replaces
        # them. Any reintroduction would be a revert of M1's readability fix.
        assert PACT_ROUTING_BLOCK.count("\u2192") == 0, (
            "PACT_ROUTING_BLOCK contains U+2192 (rightwards arrow). "
            "Remediation M1 replaced the arrow one-liner format with "
            "sub-bullets; re-introducing arrows would revert the "
            "readability fix."
        )
        assert "- Line starting with `YOUR PACT ROLE: orchestrator`:" in PACT_ROUTING_BLOCK
        assert "- Line starting with `YOUR PACT ROLE: teammate (`:" in PACT_ROUTING_BLOCK

    def test_routing_block_does_not_contain_conditional_phrase(self):
        """T4 (negative-assertion, counter-test-by-revert):
        PACT_ROUTING_BLOCK must NOT contain the phrase 'unless already
        loaded'. That phrase is the conditional-evaluation pattern #452
        replaces with unconditional per-turn referral. If someone reverts
        either bullet to the pre-#452 conditional form, this test fails.

        Re-introducing 'unless already loaded' must cause this test to
        fail — that is the structural guarantee."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert "unless already loaded" not in PACT_ROUTING_BLOCK, (
            "PACT_ROUTING_BLOCK contains the banned conditional phrase "
            "'unless already loaded'. Per #452, both orchestrator and "
            "teammate routing lines must be unconditional — the LLM "
            "self-diagnosis required by 'unless already loaded' was "
            "empirically observed to silently fail (session e63c184b, "
            "2026-04-17). Use the unconditional FIRST-ACTION wording instead."
        )

    def test_routing_block_contains_per_turn_reminder(self):
        """T5 (positive-assertion, drift-shape pin): PACT_ROUTING_BLOCK
        must contain the per-turn orchestration-reference reminder phrase
        from the architect's proposed text. If someone removes the
        per-turn reminder (e.g., reverting to a bare 'invoke bootstrap'
        line without the 'treat ... skill's content ... as your operating
        reference' clause), this test fails.

        The substring pinned here is a stable phrase-shape — specific
        enough to catch semantic drift, tolerant of minor whitespace."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        required_substring = (
            "treat the `PACT:orchestration` skill's content"
        )
        assert required_substring in PACT_ROUTING_BLOCK, (
            f"PACT_ROUTING_BLOCK is missing the per-turn reminder "
            f"substring {required_substring!r}. Per #452, the orchestrator "
            f"line must instruct the lead to treat the orchestration "
            f"skill's content as its per-turn operating reference. "
            f"Without this, the Tier-0 per-turn-discipline layer of the "
            f"governance-delivery architecture is silently absent."
        )
        # Anti-pattern foreclosure — verify the 'do not re-invoke each
        # turn' clause is present to prevent the worst-case
        # +5500 tokens/turn misinterpretation.
        assert "Do not re-invoke the skill via the Skill tool each turn" in PACT_ROUTING_BLOCK, (
            "PACT_ROUTING_BLOCK is missing the anti-pattern foreclosure "
            "clause 'Do not re-invoke the skill via the Skill tool each "
            "turn'. Without it, a literal reading of the per-turn "
            "reminder could cause +5500 tokens/turn from redundant "
            "tool-call skill reloads."
        )

    def test_line_anchor_heuristic_rejects_mid_line_pact_role(self):
        """The routing block instructs agents to check for 'YOUR PACT ROLE:'
        AT THE START OF A LINE. A mid-line occurrence (e.g., inside a
        Working Memory section quoting the marker) must NOT be treated
        as a valid role signal.

        This test simulates the consumer-side heuristic described in the
        routing block text: split context into lines, check each line
        with startswith('YOUR PACT ROLE:'). A CLAUDE.md with the marker
        embedded mid-line in Working Memory should produce zero matches.
        """
        claude_md_content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "- 2026-04-12: The session_init hook injects YOUR PACT ROLE: orchestrator "
            "into additionalContext for the lead session.\n"
            "- Architecture note: YOUR PACT ROLE: teammate markers are injected by "
            "peer_inject.py.\n"
            "\n"
            "## Retrieved Context\n"
            "- Memory 0a52fd73: session_init emits `YOUR PACT ROLE: orchestrator` at "
            "byte 0 of additionalContext\n"
        )

        # Simulate the consumer-side line-anchored check
        line_anchored_matches = [
            line for line in claude_md_content.splitlines()
            if line.startswith("YOUR PACT ROLE:")
        ]

        assert line_anchored_matches == [], (
            f"Line-anchored check found false-positive PACT ROLE markers in "
            f"Working Memory / Retrieved Context sections: {line_anchored_matches}. "
            f"The routing block instructs agents to use a line-anchored check — "
            f"mid-line occurrences must not match."
        )


# ---------------------------------------------------------------------------
# remove_stale_kernel_block() — one-time migration
# ---------------------------------------------------------------------------

class TestRemoveStaleKernelBlockPresent:
    """The legacy PACT_START/PACT_END block exists and must be removed."""

    def test_strips_block_and_preserves_user_content(self, mock_home):
        """Block is removed; user content before/after survives verbatim."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User preamble line\n"
            "More user content\n"
            "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->\n"
            "# PACT Orchestrator\n"
            "Old kernel body that must be removed\n"
            "<!-- PACT_END -->\n"
            "User trailing content\n"
            "Even more user content\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result == "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        new_content = target.read_text(encoding="utf-8")
        # Markers and body are gone
        assert "PACT_START" not in new_content
        assert "PACT_END" not in new_content
        assert "Old kernel body that must be removed" not in new_content
        assert "# PACT Orchestrator" not in new_content
        # User content survives verbatim
        assert "User preamble line" in new_content
        assert "More user content" in new_content
        assert "User trailing content" in new_content
        assert "Even more user content" in new_content

    def test_secure_permissions_after_write(self, mock_home):
        """Rewritten file must end up at 0o600."""
        import stat
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: pact -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_kernel_strip_does_not_collide_with_managed_markers(self, mock_home):
        """
        Runtime integration guard: kernel marker strip must NOT touch
        PACT_MANAGED_*/PACT_MEMORY_* markers.

        The kernel block uses `<!-- PACT_START:` and `<!-- PACT_END -->`,
        which are distinct literals from `<!-- PACT_MANAGED_START:`,
        `<!-- PACT_MANAGED_END -->`, `<!-- PACT_MEMORY_START -->`, and
        `<!-- PACT_MEMORY_END -->`. A substring-based split on
        `<!-- PACT_START:` will NOT match the managed-start literal
        because the colon after `PACT_START` is absent from
        `PACT_MANAGED_START:` at that character position. This test
        enforces that invariant at runtime so any future refactor
        weakening the marker literal fails loudly here.
        """
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User preamble\n"
            "\n"
            "<!-- PACT_START: legacy kernel -->\n"
            "# PACT Orchestrator (legacy)\n"
            "Old kernel body\n"
            "<!-- PACT_END -->\n"
            "\n"
            "<!-- PACT_MANAGED_START: Managed by pact-plugin -->\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Retrieved Context\n"
            "- memory item\n"
            "<!-- PACT_MEMORY_END -->\n"
            "<!-- PACT_MANAGED_END -->\n"
            "User trailing content\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result == "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        new_content = target.read_text(encoding="utf-8")

        # Legacy kernel markers and body are stripped
        assert "<!-- PACT_START:" not in new_content
        assert "<!-- PACT_END -->" not in new_content
        assert "# PACT Orchestrator (legacy)" not in new_content
        assert "Old kernel body" not in new_content

        # All four managed/memory markers survive intact
        assert "<!-- PACT_MANAGED_START: Managed by pact-plugin -->" in new_content
        assert "<!-- PACT_MANAGED_END -->" in new_content
        assert "<!-- PACT_MEMORY_START -->" in new_content
        assert "<!-- PACT_MEMORY_END -->" in new_content

        # Managed block body and user content survive
        assert "# PACT Framework and Managed Project Memory" in new_content
        assert "## Retrieved Context" in new_content
        assert "- memory item" in new_content
        assert "User preamble" in new_content
        assert "User trailing content" in new_content

        # Marker ordering is preserved: MANAGED_START < MEMORY_START
        # < MEMORY_END < MANAGED_END (sibling invariant survives the strip).
        mgr_start = new_content.index("<!-- PACT_MANAGED_START:")
        mem_start = new_content.index("<!-- PACT_MEMORY_START -->")
        mem_end = new_content.index("<!-- PACT_MEMORY_END -->")
        mgr_end = new_content.index("<!-- PACT_MANAGED_END -->")
        assert mgr_start < mem_start < mem_end < mgr_end


class TestRemoveStaleKernelBlockAbsent:
    """No legacy block present — function must be a clean no-op."""

    def test_returns_none_when_home_file_missing(self, mock_home):
        """No CLAUDE.md at ~/.claude/CLAUDE.md → None, no side effects."""
        from shared.claude_md_manager import remove_stale_kernel_block

        # mock_home creates ~/.claude but not CLAUDE.md
        target = mock_home / ".claude" / "CLAUDE.md"
        assert not target.exists()

        result = remove_stale_kernel_block()

        assert result is None
        assert not target.exists()

    def test_returns_none_when_no_markers(self, mock_home):
        """File exists but contains no PACT_START → None, content unchanged."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = "User-managed CLAUDE.md\nNo PACT markers present\n"
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is None
        assert target.read_text(encoding="utf-8") == original

    def test_returns_skip_status_when_only_end_marker(self, mock_home):
        """PACT_END alone (no START) → returns a 'Migration skipped' status
        string so session_init.py surfaces the warning to the user via
        context_parts. Content is unchanged (defensive no-op)."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = "user content\n<!-- PACT_END -->\nmore content\n"
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_END" in result
        assert "PACT_START" in result
        # File is unchanged (defensive no-op)
        assert target.read_text(encoding="utf-8") == original


class TestRemoveStaleKernelBlockMalformed:
    """Malformed marker states — defensive no-op PLUS a status string
    returned so session_init.py surfaces the warning via context_parts.
    Previously the defensive paths emitted stderr warnings (never shown
    to the user) and returned None; now they return the warning string
    so it actually reaches the user's orchestrator context."""

    def test_returns_skip_status_when_start_without_end(self, mock_home):
        """Unterminated PACT block → defensive no-op, returns 'Migration skipped'."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "before\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body that never closes\n"
            "more content\n"
        )
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_START" in result
        assert "PACT_END" in result
        # File is unchanged (defensive no-op)
        assert target.read_text(encoding="utf-8") == original

    def test_returns_skip_status_when_end_appears_before_start(self, mock_home):
        """END marker appears textually before START → defensive no-op +
        'Migration skipped' string."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "before\n"
            "<!-- PACT_END -->\n"
            "stray END marker out of order\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body that never closes after this START\n"
        )
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        # The function splits on START first then checks for END in the
        # remainder. Here END is before START so the remainder has no END.
        # Content remains untouched.
        assert target.read_text(encoding="utf-8") == original


class TestRemoveStaleKernelBlockOSError:
    """OSError on read or write paths → graceful failure, status string returned.

    Cycle 2 minor item 5/6: exercises the try/except OSError fallback
    branches in remove_stale_kernel_block. Previously these branches were
    unexercised, so a bug in the error handling (wrong format string,
    wrong truncation length, accidentally raising instead of returning)
    would not be caught by CI.
    """

    def test_returns_none_when_read_fails(self, mock_home):
        """OSError on read_text → returns None (file appears unreadable)."""
        from unittest.mock import patch
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text("placeholder content", encoding="utf-8")

        # Patch Path.read_text to raise OSError when called on the home file
        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(target):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = remove_stale_kernel_block()

        assert result is None

    def test_returns_failure_status_when_write_fails(self, mock_home):
        """OSError on write_text → returns 'Failed to remove stale kernel block: ...'.

        The block is well-formed (so the function reaches the write path),
        but the write itself fails. The function must return a status
        string indicating the failure mode rather than raising.
        """
        from unittest.mock import patch
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "user content before\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "kernel body to be stripped\n"
            "<!-- PACT_END -->\n"
            "user content after\n"
        )
        target.write_text(original, encoding="utf-8")

        # Patch Path.write_text to raise OSError when called on the home file
        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(target):
                raise OSError("simulated write failure")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = remove_stale_kernel_block()

        assert result is not None
        assert "Failed to remove stale kernel block" in result
        # Original file unchanged because write was blocked
        assert target.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# update_pact_routing() — idempotent project CLAUDE.md routing block management
# ---------------------------------------------------------------------------

class TestUpdatePactRoutingIdempotent:
    """File already has the canonical block — no rewrite, returns None."""

    def test_no_write_when_block_canonical(self, tmp_path, monkeypatch):
        """Canonical content between markers → return None, no write."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            f"{CANONICAL_PACT_ROUTING_BLOCK}\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        # File untouched
        assert legacy.read_text(encoding="utf-8") == original

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Empty CLAUDE_PROJECT_DIR → None."""
        from shared.claude_md_manager import update_pact_routing

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        assert update_pact_routing() is None

    def test_returns_none_when_file_does_not_exist(self, tmp_path, monkeypatch):
        """Project dir exists but no CLAUDE.md → defer to ensure_project_memory_md."""
        from shared.claude_md_manager import update_pact_routing

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        # update_pact_routing must not create the file
        assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()


class TestUpdatePactRoutingUpdate:
    """Markers present, but content between them is stale → replace it."""

    def test_replaces_stale_content_between_markers(self, tmp_path, monkeypatch):
        """Stale routing block content gets replaced with canonical version."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## OLD ROUTING CONTENT\n"
            "Outdated instructions here\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
            "user notes\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result == "PACT routing block updated in project CLAUDE.md"
        new_content = legacy.read_text(encoding="utf-8")
        # Canonical block is now present
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Stale content gone
        assert "OLD ROUTING CONTENT" not in new_content
        assert "Outdated instructions here" not in new_content
        # Surrounding content preserved
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes" in new_content

    def test_secure_permissions_after_update(self, tmp_path, monkeypatch):
        """Updated file must end up at 0o600."""
        import stat
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "stale\n"
            "<!-- PACT_ROUTING_END -->\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        update_pact_routing()

        mode = stat.S_IMODE(legacy.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


class TestUpdatePactRoutingInsert:
    """Markers absent — insert the block near the top of the file."""

    def test_inserts_block_after_title(self, tmp_path, monkeypatch):
        """Routing block is inserted after the # title line."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "user notes that must survive\n"
        )
        legacy.write_text(original, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result == "PACT routing block inserted into project CLAUDE.md"
        new_content = legacy.read_text(encoding="utf-8")
        # Canonical block now present
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Original content preserved
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes that must survive" in new_content
        # Block sits between the title and the next section
        title_idx = new_content.index("# Project Memory")
        block_idx = new_content.index("<!-- PACT_ROUTING_START")
        wm_idx = new_content.index("## Working Memory")
        assert title_idx < block_idx < wm_idx

    def test_idempotent_after_insert(self, tmp_path, monkeypatch):
        """A second invocation after insert must be a no-op."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n\n## Working Memory\nuser notes\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        first = update_pact_routing()
        assert first == "PACT routing block inserted into project CLAUDE.md"

        # Second call must not write again
        second = update_pact_routing()
        assert second is None


class TestUpdatePactRoutingOrphanMarkers:
    """Cycle 2 minor item 13: orphan marker handling.

    If exactly one of PACT_ROUTING_START or PACT_ROUTING_END is present
    (e.g., user manually deleted the closing marker, or a prior write
    crashed mid-file), the function strips the orphan marker before
    falling through to the insert path. Without this fix, the file
    would accumulate a new routing block on every session because the
    update guard requires BOTH markers.
    """

    def test_orphan_start_marker_stripped_before_insert(
        self, tmp_path, monkeypatch
    ):
        """Orphan PACT_ROUTING_START with no matching END → orphan stripped,
        fresh canonical block inserted, no accumulation on subsequent runs."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # PACT_ROUTING_START present alone (orphan), no END marker
        original = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n\nstale orphan content with no closing marker\n"
            "\n"
            "## Working Memory\n"
            "user notes\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        # First call: insert path (after orphan strip) → file gets canonical block.
        # The return string now includes the orphan-stripped notice.
        assert result is not None
        assert "PACT routing block inserted into project CLAUDE.md" in result
        assert "orphan" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")

        # The orphan START marker line is gone (stripped before insertion)
        # and the canonical routing block is now present (with both markers)
        assert new_content.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1, (
            "Should have exactly 1 PACT_ROUTING_START marker after fix "
            "(the new canonical one). Orphan was not stripped."
        )
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # User content preserved
        assert "## Working Memory" in new_content
        assert "user notes" in new_content
        # Orphan content body was inside the orphan marker block — it remains
        # because orphan stripping only removes the marker line itself, not
        # the surrounding text. This is intentional — preserves user data.

        # Second call: idempotent no-op (markers now well-formed)
        second = update_pact_routing()
        assert second is None, (
            "Second call should be a no-op. If this fails, the orphan-strip "
            "+ insert path is not converging on canonical state."
        )

    def test_orphan_end_marker_stripped_before_insert(
        self, tmp_path, monkeypatch
    ):
        """Orphan PACT_ROUTING_END with no matching START → same handling."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Only END marker present, no START
        original = (
            "# Project Memory\n"
            "\n"
            "stale content with stray closing marker\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        # Insert path fires after orphan strip — return string includes
        # the orphan-stripped notice.
        assert result is not None
        assert "PACT routing block inserted into project CLAUDE.md" in result
        assert "orphan" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # Subsequent call is a no-op
        assert update_pact_routing() is None

    def test_no_accumulation_on_repeated_calls_with_orphan(
        self, tmp_path, monkeypatch
    ):
        """The fix's purpose: subsequent sessions with the orphan-stripped
        file must NOT accumulate additional routing blocks."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n\n<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\norphan body\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # First call: orphan strip + insert
        update_pact_routing()
        # Second call: idempotent no-op
        update_pact_routing()
        # Third call: idempotent no-op
        update_pact_routing()

        final = legacy.read_text(encoding="utf-8")
        # Exactly one of each marker — no accumulation
        assert final.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1
        assert final.count("<!-- PACT_ROUTING_END -->") == 1


class TestUpdatePactRoutingSessionStartIsolation:
    """SESSION_START preservation tripwire (#366 item 5, architect S3 finding).

    update_pact_routing and update_session_info both mutate the project
    CLAUDE.md but use disjoint markers (PACT_ROUTING_START/END vs
    SESSION_START/END). The current code relies on marker disjointness
    to avoid clobbering the session block, but there is no tripwire test
    asserting that:

    1. The insert path (markers absent) leaves the SESSION_START block
       byte-identical.
    2. The update path (markers canonicalized) leaves the SESSION_START
       block byte-identical.
    3. The orphan-strip path does NOT reach into a SESSION_START block
       even if the session body happens to contain a line that matches
       the routing marker substring.

    Test (3) is the worst-case scenario architect flagged. Without the
    SESSION_START isolation in the orphan-strip loop, a file whose
    SESSION_START body accidentally contained a line matching the
    PACT_ROUTING_START marker would be silently corrupted: the orphan
    strip would drop the line from inside the session body, and the
    subsequent insert path would add a fresh routing block at the top,
    leaving SESSION_START missing a line. The fix in claude_md_manager
    tracks inside_session_block and preserves those lines verbatim."""

    SESSION_BLOCK_BODY = (
        "<!-- SESSION_START -->\n"
        "## Current Session\n"
        "- Resume: `claude --resume deadbeef-dead-beef-dead-beefdeadbeef`\n"
        "- Team: `pact-deadbeef`\n"
        "- Session dir: `/Users/test/.claude/pact-sessions/proj/deadbeef`\n"
        "- Started: 2026-04-11 00:00:00 UTC\n"
        "<!-- SESSION_END -->\n"
    )

    def test_insert_path_preserves_session_start_block_verbatim(
        self, tmp_path, monkeypatch
    ):
        """Insert path: file has SESSION_START but NO PACT_ROUTING markers.
        After update_pact_routing, a PACT_ROUTING block is inserted AND the
        SESSION_START block is byte-identical to what was there before."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            "Some preamble that is not session metadata.\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        new_content = legacy.read_text(encoding="utf-8")

        # 1. PACT_ROUTING block was inserted
        assert result is not None
        assert "inserted" in result.lower()
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # 2. SESSION_START block body is byte-identical
        assert self.SESSION_BLOCK_BODY in new_content, (
            "SESSION_START block body must be byte-identical after "
            "update_pact_routing inserts a routing block."
        )

        # 3. Sensible position — SESSION_START block is not fragmented
        start_idx = new_content.index("<!-- SESSION_START -->")
        end_idx = new_content.index("<!-- SESSION_END -->")
        assert start_idx < end_idx
        # The routing block should be placed before SESSION_START (near top)
        routing_idx = new_content.index(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        )
        assert routing_idx < start_idx, (
            "PACT_ROUTING block should be inserted before the "
            "SESSION_START block, not inside or after it."
        )

    def test_update_path_preserves_session_start_block_verbatim(
        self, tmp_path, monkeypatch
    ):
        """Update path: file has BOTH a non-canonical PACT_ROUTING block
        AND a SESSION_START block. After update_pact_routing, the routing
        block is canonicalized AND SESSION_START is byte-identical."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n"
            "\n"
            "STALE non-canonical content that should be rewritten.\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        new_content = legacy.read_text(encoding="utf-8")

        # 1. Routing block canonicalized
        assert result is not None
        assert "updated" in result.lower()
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        assert "STALE non-canonical content" not in new_content

        # 2. SESSION_START block body byte-identical
        assert self.SESSION_BLOCK_BODY in new_content, (
            "SESSION_START block body must be byte-identical after "
            "update_pact_routing canonicalizes the routing block."
        )

    def test_orphan_strip_does_not_corrupt_session_start(
        self, tmp_path, monkeypatch
    ):
        """Worst-case tripwire: SESSION_START body contains a line matching
        the PACT_ROUTING_START marker, and there is NO matching END marker
        elsewhere in the file. This triggers the orphan-strip branch.

        Without the SESSION_START isolation fix in the orphan-strip loop,
        the loop would silently drop the matching line from inside the
        session body, then the insert path would prepend a new routing
        block at the top — leaving SESSION_START visibly corrupted.

        With the fix, lines inside SESSION_START/SESSION_END are preserved
        verbatim, and the insert path still adds a routing block at the
        top. Both blocks coexist cleanly."""
        from shared.claude_md_manager import update_pact_routing

        # SESSION_START body contains a line that is literally the routing
        # start marker — e.g., user pasted routing-block docs into the
        # session metadata. No PACT_ROUTING_END is present anywhere.
        session_block_with_marker = (
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "- Resume: `claude --resume deadbeef`\n"
            "- Team: `pact-deadbeef`\n"
            "- Note: docs pasted below\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "- Started: 2026-04-11 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n"
        )
        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            + session_block_with_marker
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()
        assert result is not None

        new_content = legacy.read_text(encoding="utf-8")

        # 1. SESSION_START block body byte-identical — the orphan strip
        # MUST NOT have dropped the line matching the routing marker.
        assert session_block_with_marker in new_content, (
            "SESSION_START block body was corrupted — the orphan strip "
            "reached into the session block and dropped a line. "
            "Expected byte-identical preservation of SESSION_START body."
        )

        # 2. A canonical routing block was added at the top (insert path)
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # 3. All the session content is still readable and intact
        assert "- Resume: `claude --resume deadbeef`" in new_content
        assert "- Team: `pact-deadbeef`" in new_content
        assert "- Started: 2026-04-11 00:00:00 UTC" in new_content
        assert "- Note: docs pasted below" in new_content

    def test_orphan_strip_outside_session_block_still_works(
        self, tmp_path, monkeypatch
    ):
        """Regression guard for the SESSION_START isolation fix:
        orphan markers OUTSIDE a SESSION_START block must still be
        stripped. This ensures the fix did not over-scope and break the
        main orphan-strip behavior for content outside session metadata."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Orphan START marker outside any SESSION_START block
        original_content = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()
        assert result is not None

        new_content = legacy.read_text(encoding="utf-8")

        # Exactly one of each marker — the orphan outside SESSION_START
        # was stripped, and a fresh canonical block was inserted at top.
        assert new_content.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1

        # SESSION_START body still byte-identical
        assert self.SESSION_BLOCK_BODY in new_content


class TestUpdatePactRoutingStaleOrchestratorLine:
    """F1: strip the v3.16.2-era 'The global PACT Orchestrator is loaded
    from ~/.claude/CLAUDE.md' line from upgraded project CLAUDE.mds.

    After the #366 Phase 1 migration, the routing block supersedes the
    stale line. Leaving it in place creates a factual contradiction for
    users who upgrade. update_pact_routing must strip the line even when
    the routing block is already canonical (the original short-circuit
    return would otherwise leave the stale line in place forever)."""

    STALE_LINE = "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."

    def test_strips_stale_line_when_block_already_canonical(
        self, tmp_path, monkeypatch
    ):
        """Stale line + canonical routing block → file is rewritten to
        drop the stale line; the canonical routing block is untouched."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            f"{self.STALE_LINE}\n"
            "\n"
            f"{CANONICAL_PACT_ROUTING_BLOCK}\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is not None
        assert "stale" in result.lower() or "orchestrator-loader" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert self.STALE_LINE not in new_content
        # Canonical routing block survives intact
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content

    def test_strips_stale_line_when_inserting_routing_block(
        self, tmp_path, monkeypatch
    ):
        """Stale line + no routing block → stale line is removed AND the
        canonical routing block is inserted in the same pass. The return
        string mentions the stale-line strip as a suffix."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            "This file contains project-specific memory managed by the PACT framework.\n"
            f"{self.STALE_LINE}\n"
            "\n"
            "## Working Memory\n"
            "user notes\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is not None
        assert "inserted" in result
        assert "stale" in result.lower() or "orchestrator-loader" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert self.STALE_LINE not in new_content
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Unrelated user content preserved
        assert "This file contains project-specific memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes" in new_content

    def test_no_write_when_no_stale_line_and_block_canonical(
        self, tmp_path, monkeypatch
    ):
        """Fresh project CLAUDE.md (no stale line, canonical block) →
        idempotent no-op: returns None, file is byte-identical after."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            f"{CANONICAL_PACT_ROUTING_BLOCK}\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        mtime_before = legacy.stat().st_mtime_ns
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        assert legacy.read_text(encoding="utf-8") == original
        assert legacy.stat().st_mtime_ns == mtime_before


class TestRemoveStaleKernelBlockBlankLinePreservation:
    """F3: preserve one blank line at the removal boundary when the
    obsolete kernel block is stripped. The pre-fix implementation
    collapsed intentional blank lines around the removed block, trampling
    user spacing."""

    def test_preserves_single_blank_line_between_pre_and_post(self, mock_home):
        """"Line1\\n\\n<block>\\n\\nLine2\\n" → "Line1\\n\\nLine2\\n" —
        one blank line survives the strip."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "Line1\n"
            "\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "\n"
            "Line2\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "Line1\n\nLine2\n"

    def test_block_at_top_of_file_leaves_clean_post_content(self, mock_home):
        """PACT block at the top of the file → post content starts fresh
        with no leading blank lines."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "\n"
            "User content starts here\n"
            "more content\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "User content starts here\nmore content\n"

    def test_block_at_end_of_file_leaves_trailing_newline_on_pre(self, mock_home):
        """PACT block at the end of the file → file ends with pre_clean + '\\n',
        no leftover whitespace or markers."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User content line 1\n"
            "User content line 2\n"
            "\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "User content line 1\nUser content line 2\n"


class TestSymlinkRefusal:
    """SECURITY hardening — refuse to operate on symlinks. Both
    remove_stale_kernel_block and update_pact_routing return a status
    string ('Migration skipped: ...' or 'Routing skipped: ...') if their
    target is a symlink, rather than following the link and writing to
    its target. session_init.py routes these to context_parts so the
    user sees the warning via orchestrator context (hook stderr is NOT
    shown to users).

    The status strings are deliberately opaque: they name WHAT was skipped
    and use a generic "path precondition not met" phrase that does not
    reveal the internal guard (symlink check) to a local attacker reading
    the output. Tests assert on the opaque phrasing, not on the word
    "symlink" or "refusing".

    Tests use os.symlink to create real symlinks pointing at unrelated
    files in tmp_path. We verify (1) the function returns the opaque
    skip status string, (2) the symlink target file is byte-identical
    (untouched), and (3) the symlink itself still exists."""

    def test_remove_stale_kernel_block_refuses_symlink(
        self, mock_home, tmp_path
    ):
        """If ~/.claude/CLAUDE.md is a symlink, remove_stale_kernel_block
        returns an opaque 'Migration skipped: ... path precondition not met'
        string and does not touch the symlink target."""
        from shared.claude_md_manager import remove_stale_kernel_block

        # Create a regular file as the symlink target
        symlink_target = tmp_path / "external_target.md"
        symlink_target_content = (
            "# External target\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "fake kernel content that should NOT be touched\n"
            "<!-- PACT_END -->\n"
            "more external content\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        # Replace ~/.claude/CLAUDE.md with a symlink to the external target
        managed_path = mock_home / ".claude" / "CLAUDE.md"
        if managed_path.exists() or managed_path.is_symlink():
            managed_path.unlink()
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        result = remove_stale_kernel_block()

        # Returns an opaque status string ("path precondition not met")
        # rather than one that discloses the symlink check to attackers.
        assert result is not None
        assert "Migration skipped" in result
        assert "path precondition not met" in result
        # Deliberately NOT revealing: the word "symlink" or "refusing"
        # should not appear in the status string.
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink target file is byte-identical (untouched)
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        # Symlink itself is still a symlink
        assert managed_path.is_symlink()

    def test_update_pact_routing_refuses_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the project CLAUDE.md is a symlink, update_pact_routing returns
        an opaque 'Routing skipped: ... path precondition not met' string
        and does not touch the symlink target."""
        from shared.claude_md_manager import update_pact_routing

        # Create a regular file as the symlink target
        symlink_target = tmp_path / "external_claude.md"
        symlink_target_content = (
            "# External target\n"
            "user content that should NOT be touched\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        # Project CLAUDE.md is a symlink to the external target
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        managed_path = project_dir / "CLAUDE.md"
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = update_pact_routing()

        # Returns an opaque status string ("path precondition not met")
        # rather than one that discloses the symlink check to attackers.
        assert result is not None
        assert "Routing skipped" in result
        assert "path precondition not met" in result
        # Deliberately NOT revealing: the word "symlink" or "refusing"
        # should not appear in the status string.
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink target file is byte-identical (untouched)
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        # Symlink itself is still a symlink
        assert managed_path.is_symlink()

    def test_ensure_project_memory_md_refuses_dangling_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the preferred .claude/CLAUDE.md path is a dangling symlink,
        ensure_project_memory_md returns an opaque skip status and does not
        follow the link.

        This covers the edge case where neither CLAUDE.md location exists
        (resolve returns "new_default") but the preferred path is a dangling
        symlink — e.g., a local attacker pre-planted a symlink before the
        first session. is_symlink uses lstat and returns True even for
        dangling links."""
        from shared.claude_md_manager import ensure_project_memory_md

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()

        # Create a dangling symlink at the preferred CLAUDE.md path
        managed_path = dot_claude / "CLAUDE.md"
        os.symlink("/nonexistent/target", str(managed_path))
        assert managed_path.is_symlink()
        assert not managed_path.exists()  # dangling

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = ensure_project_memory_md()

        assert result is not None
        assert "Project CLAUDE.md skipped" in result
        assert "path precondition not met" in result
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink is still a dangling symlink (not replaced with a file)
        assert managed_path.is_symlink()
        assert not managed_path.exists()

    def test_update_session_info_refuses_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the project CLAUDE.md is a symlink, update_session_info returns
        an opaque skip status and does not touch the symlink target.

        Placed in TestSymlinkRefusal alongside the other two guards for
        discoverability, with a parallel test in test_session_resume.py
        for the session_resume test suite."""
        from shared.session_resume import update_session_info

        symlink_target = tmp_path / "external_target.md"
        symlink_target_content = (
            "# External\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "<!-- SESSION_END -->\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        managed_path = project_dir / "CLAUDE.md"
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = update_session_info("sess-new", "pact-new")

        assert result is not None
        assert "Session info skipped" in result
        assert "path precondition not met" in result
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        assert managed_path.is_symlink()


class TestRemoveStaleKernelBlockMalformedFeedback:
    """Malformed-marker user-visible feedback.

    When ~/.claude/CLAUDE.md contains an orphan marker (one of
    PACT_START/PACT_END but not the other, or both with END before START),
    remove_stale_kernel_block returns a 'Migration skipped: ...' status
    string explaining what was wrong and what the user should do. Hook
    stderr is NOT shown to users by Claude Code, so a returned string is
    the only way to deliver the warning. session_init.py routes these
    status strings to context_parts for user visibility via the
    orchestrator's context.

    Normal (well-formed) case returns the success message with no noise."""

    def test_orphan_start_marker_returns_skip_status(
        self, mock_home
    ):
        """Only PACT_START present → returns 'Migration skipped: ...' string
        mentioning PACT_START and PACT_END for user diagnosis."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n<!-- PACT_START: Managed by pact-plugin -->\nbody\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_START" in result
        assert "PACT_END" in result
        assert "orphan" in result.lower() or "matching" in result.lower()

    def test_orphan_end_marker_returns_skip_status(
        self, mock_home
    ):
        """Only PACT_END present → returns 'Migration skipped: ...' string."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n<!-- PACT_END -->\nstray\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_END" in result
        assert "PACT_START" in result

    def test_well_formed_block_does_not_return_skip_status(
        self, mock_home
    ):
        """Normal case (well-formed block) → clean success message, no
        'Migration skipped' noise."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result == "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        # Normal case: no 'Migration skipped' in the success message
        assert "Migration skipped" not in result
        assert "Refusing" not in result


class TestUpdatePactRoutingOSError:
    """OSError on read or write paths → graceful failure, status string returned.

    Cycle 2 minor item 5/6: exercises the try/except OSError fallback
    branches in update_pact_routing. Previously these branches were
    unexercised, so a bug in the error handling (wrong format string,
    wrong truncation length, accidentally raising instead of returning)
    would not be caught by CI.
    """

    def test_returns_none_when_read_fails(self, tmp_path, monkeypatch):
        """OSError on read_text → returns None (file appears unreadable)."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("placeholder content", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = update_pact_routing()

        assert result is None

    def test_returns_failure_status_when_write_fails_during_update(
        self, tmp_path, monkeypatch
    ):
        """OSError on write_text during the update path → returns failure status."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Stale content between markers — triggers the update path
        original = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n\nstale content that should be replaced\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated write failure during update")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = update_pact_routing()

        assert result is not None
        assert "Failed to update PACT routing" in result
        # Original file unchanged because write was blocked
        assert legacy.read_text(encoding="utf-8") == original

    def test_returns_failure_status_when_write_fails_during_insert(
        self, tmp_path, monkeypatch
    ):
        """OSError on write_text during the insert path → returns failure status."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # No markers — triggers the insert path
        original = "# Project Memory\n\n## Working Memory\nuser notes\n"
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated write failure during insert")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = update_pact_routing()

        assert result is not None
        assert "Failed to insert PACT routing" in result
        # Original file unchanged because write was blocked
        assert legacy.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# ensure_project_memory_md() — preserved tests + canonical-block check
# ---------------------------------------------------------------------------

class TestEnsureProjectMemoryMd:
    """Tests for ensure_project_memory_md() -- project CLAUDE.md creation."""

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Should return None when CLAUDE_PROJECT_DIR not set."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = ensure_project_memory_md()

        assert result is None

    def test_returns_none_when_file_exists(self, tmp_path, monkeypatch):
        """Should return None when project CLAUDE.md already exists."""
        from shared.claude_md_manager import ensure_project_memory_md

        (tmp_path / "CLAUDE.md").write_text("existing content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        # Content should be unchanged
        assert (tmp_path / "CLAUDE.md").read_text() == "existing content"

    def test_creates_project_claude_md(self, tmp_path, monkeypatch):
        """Should create .claude/CLAUDE.md (new default) with memory sections."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result == "Created project CLAUDE.md with memory sections"
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        assert new_default.exists()
        assert not legacy.exists()
        content = new_default.read_text()
        assert "# PACT Framework and Managed Project Memory" in content
        assert "## Retrieved Context" in content
        assert "## Working Memory" in content

    def test_created_file_contains_session_markers(self, tmp_path, monkeypatch):
        """Should include session markers in the created file."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "<!-- SESSION_START -->" in content
        assert "<!-- SESSION_END -->" in content

    def test_created_file_contains_canonical_routing_block(self, tmp_path, monkeypatch):
        """The created file must embed the canonical PACT_ROUTING_BLOCK verbatim."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert CANONICAL_PACT_ROUTING_BLOCK in content

    def test_returns_none_when_dot_claude_exists(self, tmp_path, monkeypatch):
        """Should return None and not overwrite when .claude/CLAUDE.md already exists."""
        from shared.claude_md_manager import ensure_project_memory_md

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("existing dot-claude content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        assert dot_claude_file.read_text() == "existing dot-claude content"
        # Legacy was not created as a side effect
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_returns_none_when_legacy_exists(self, tmp_path, monkeypatch):
        """Should return None when only the legacy ./CLAUDE.md exists (no migration)."""
        from shared.claude_md_manager import ensure_project_memory_md

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("existing legacy content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        # Legacy file is preserved as-is; no migration to .claude/
        assert legacy.read_text() == "existing legacy content"
        assert not (tmp_path / ".claude").exists()

    def test_dot_claude_takes_precedence_over_legacy(self, tmp_path, monkeypatch):
        """When both exist, .claude/CLAUDE.md is preferred (return None, no edit)."""
        from shared.claude_md_manager import ensure_project_memory_md

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("preferred")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("legacy")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        assert dot_claude_file.read_text() == "preferred"
        assert legacy.read_text() == "legacy"

    def test_created_file_has_secure_permissions(self, tmp_path, monkeypatch):
        """Newly created .claude/CLAUDE.md should be 0o600; .claude/ dir should be 0o700."""
        import stat
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        ensure_project_memory_md()

        new_default = tmp_path / ".claude" / "CLAUDE.md"
        assert new_default.exists()
        file_mode = stat.S_IMODE(new_default.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"
        dir_mode = stat.S_IMODE(new_default.parent.stat().st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"


class TestEnsureProjectMemoryMdErrorPaths:
    """Tests for ensure_project_memory_md() exception handling."""

    def test_returns_error_message_on_write_failure(self, tmp_path, monkeypatch):
        """Should return truncated error message when write fails."""
        from shared.claude_md_manager import ensure_project_memory_md

        # Point to a directory where we can't write
        read_only = tmp_path / "readonly"
        read_only.mkdir()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(read_only))

        from unittest.mock import patch
        with patch.object(Path, "write_text", side_effect=OSError("No space left")):
            result = ensure_project_memory_md()

        assert result is not None
        assert "Project CLAUDE.md failed:" in result

    def test_lock_timeout_returns_skip_message(self, tmp_path, monkeypatch):
        """C: when file_lock raises TimeoutError, ensure_project_memory_md
        must return the human-readable skip message and NOT create the file.

        Coverage gap closed: the existing TestUpdatePactRoutingLockContention
        suite exercises the analogous TimeoutError fail-open in
        update_pact_routing, but no test exercises the equivalent path inside
        ensure_project_memory_md. A regression here would mean a stuck lock
        (concurrent session_init hooks) crashes session start instead of
        skipping the project CLAUDE.md creation gracefully.

        We monkeypatch the file_lock symbol on the claude_md_manager module
        directly to raise TimeoutError on entry — simpler than spinning up a
        threaded lock holder and isolates this test from the lock
        infrastructure's own contention semantics.
        """
        from contextlib import contextmanager
        from shared import claude_md_manager as cmm

        # Fresh empty project dir so the resolver returns "new_default" and
        # ensure_project_memory_md proceeds to the file_lock branch.
        project_dir = tmp_path / "fresh_project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Stub file_lock to raise TimeoutError on entry, mirroring how the
        # real implementation behaves when _LOCK_TIMEOUT_SECONDS elapses
        # without acquiring the sidecar lock.
        @contextmanager
        def timing_out_lock(_path):
            raise TimeoutError(
                "Failed to acquire lock on .CLAUDE.md.lock within 5s"
            )
            yield  # pragma: no cover  -- unreachable, contextmanager requires it

        monkeypatch.setattr(cmm, "file_lock", timing_out_lock)

        result = cmm.ensure_project_memory_md()

        # Result must be the human-readable skip message routed to systemMessage.
        assert result is not None
        assert "Failed to acquire lock" in result
        assert "5s" in result
        assert "skipped" in result
        assert "next session start" in result

        # The CLAUDE.md file must NOT have been created — the timeout aborts
        # the write before any filesystem mutation.
        assert not (project_dir / ".claude" / "CLAUDE.md").exists()
        assert not (project_dir / "CLAUDE.md").exists()


class TestResolveProjectClaudeMdPath:
    """Direct tests for resolve_project_claude_md_path() helper.

    The resolver returns (path, source) where source is one of:
      - "dot_claude": existing .claude/CLAUDE.md
      - "legacy": existing ./CLAUDE.md
      - "new_default": neither exists; path points to .claude/CLAUDE.md
    """

    def test_returns_dot_claude_when_only_dot_claude_exists(self, tmp_path):
        """Returns .claude/CLAUDE.md path with 'dot_claude' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# dot-claude")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == dot_claude
        assert source == "dot_claude"

    def test_returns_legacy_when_only_legacy_exists(self, tmp_path):
        """Returns ./CLAUDE.md path with 'legacy' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == legacy
        assert source == "legacy"

    def test_prefers_dot_claude_when_both_exist(self, tmp_path):
        """When both files exist, .claude/CLAUDE.md wins."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# preferred")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == dot_claude
        assert source == "dot_claude"
        assert path != legacy

    def test_returns_new_default_when_neither_exists(self, tmp_path):
        """When neither file exists, points to .claude/CLAUDE.md as the new default."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == tmp_path / ".claude" / "CLAUDE.md"
        assert source == "new_default"
        # No filesystem side effects -- resolver only inspects, never creates
        assert not path.exists()
        assert not (tmp_path / ".claude").exists()

    def test_accepts_string_project_dir(self, tmp_path):
        """Accepts string paths in addition to Path objects."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        path, source = resolve_project_claude_md_path(str(tmp_path))

        assert source == "new_default"
        assert path == tmp_path / ".claude" / "CLAUDE.md"


class TestEnsureDotClaudeParent:
    """Tests for ensure_dot_claude_parent() helper."""

    def test_creates_dot_claude_dir_with_secure_mode(self, tmp_path):
        """Creates the parent directory with mode 0o700."""
        import stat
        from shared.claude_md_manager import ensure_dot_claude_parent

        target = tmp_path / ".claude" / "CLAUDE.md"
        assert not target.parent.exists()

        ensure_dot_claude_parent(target)

        assert target.parent.exists()
        mode = stat.S_IMODE(target.parent.stat().st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    def test_no_op_when_parent_exists(self, tmp_path):
        """Does not raise when the parent directory already exists."""
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Pre-create the parent (legacy path -- no .claude/ subdir)
        target = tmp_path / "CLAUDE.md"
        # tmp_path always exists; nothing to create
        ensure_dot_claude_parent(target)  # Should not raise

        assert tmp_path.exists()

    def test_creates_nested_parents(self, tmp_path):
        """Creates intermediate directories if needed (parents=True)."""
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Simulate a deeper-than-expected layout (defensive)
        target = tmp_path / "outer" / ".claude" / "CLAUDE.md"

        ensure_dot_claude_parent(target)

        assert target.parent.exists()

    def test_raises_when_parent_is_regular_file(self, tmp_path):
        """Raises a clear OSError when `path.parent` exists but is a
        regular file instead of a directory.

        Without the is_dir guard, the failure would surface as a
        confusing late-stage OSError from `write_text` in
        ensure_project_memory_md. With the guard, callers catch the
        early OSError and return a clear failure status string.

        This is a pathological case (e.g., a local attacker blocks
        mkdir by planting a file at the `.claude/` path), but the
        early guard makes the failure mode readable.
        """
        import pytest
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Create a regular file where `.claude/` would go
        blocker = tmp_path / ".claude"
        blocker.write_text("I am a file, not a directory", encoding="utf-8")
        assert blocker.exists()
        assert blocker.is_file()
        assert not blocker.is_dir()

        target = blocker / "CLAUDE.md"  # ensure_dot_claude_parent inspects target.parent

        with pytest.raises(OSError, match="exists but is not a directory"):
            ensure_dot_claude_parent(target)

        # The blocker file is untouched — guard is read-only
        assert blocker.read_text(encoding="utf-8") == "I am a file, not a directory"


class TestMarkerConsistency:
    """Spec Section 8: cross-file fixture sanity check.

    The PACT_ROUTING_BLOCK constant in claude_md_manager.py pattern-matches
    against two role-marker substrings to route agents to the correct
    bootstrap skill:

      - `YOUR PACT ROLE: orchestrator` → PACT:bootstrap
      - `YOUR PACT ROLE: teammate (`   → PACT:teammate-bootstrap

    Meanwhile, three production sites emit these markers:

      - session_init.py `_team_create` / `_team_reuse` emit the
        orchestrator marker to fresh and resumed lead sessions.
      - peer_inject.py `_BOOTSTRAP_PRELUDE_TEMPLATE` emits the teammate
        marker to every newly spawned teammate via SubagentStart hook.

    The marker literals on both sides are plain strings in three
    different Python files. Nothing except tests prevents someone from
    editing the routing block's search patterns without also editing
    the hook emissions (or vice versa). A single-character drift silently
    breaks routing — the unit tests still pass because each side is
    internally consistent, but `PACT_ROUTING_BLOCK`'s guidance would
    point at a substring the hooks never actually emit.

    This test asserts the emitted strings contain the exact substrings
    the routing block searches for. Catches drift between the two files.
    """

    HOOKS_DIR = Path(__file__).parent.parent / "hooks"
    SESSION_INIT_PATH = HOOKS_DIR / "session_init.py"
    CORE_PATH = (
        Path(__file__).parent.parent / "skills" / "orchestration" / "SKILL.md"
    )

    ORCHESTRATOR_MARKER = "YOUR PACT ROLE: orchestrator"
    TEAMMATE_MARKER_PREFIX = "YOUR PACT ROLE: teammate ("

    @staticmethod
    def _core_dispatch_region(text: str) -> str:
        """Slice the Agent Teams Dispatch callout region out of
        skills/orchestration/SKILL.md.

        Mirrors TestDispatchTemplatePrelude._dispatch_region in
        test_agents_structure.py — same `MANDATORY` anchor, same ~80-line
        window. Duplicated locally so this test file has no cross-file
        import dependency on the sibling test module.
        """
        marker = "MANDATORY"
        idx = text.find(marker)
        if idx == -1:
            return ""
        tail = text[idx:]
        lines = tail.splitlines()[:80]
        return "\n".join(lines)

    def test_routing_block_contains_orchestrator_marker(self):
        """PACT_ROUTING_BLOCK must reference `YOUR PACT ROLE: orchestrator`."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert self.ORCHESTRATOR_MARKER in PACT_ROUTING_BLOCK, (
            f"PACT_ROUTING_BLOCK is missing the `{self.ORCHESTRATOR_MARKER}` "
            f"substring — routing logic in spawned leads cannot match "
            f"what the hooks emit."
        )

    def test_routing_block_contains_teammate_marker_prefix(self):
        """PACT_ROUTING_BLOCK must reference `YOUR PACT ROLE: teammate (`."""
        from shared.claude_md_manager import PACT_ROUTING_BLOCK

        assert self.TEAMMATE_MARKER_PREFIX in PACT_ROUTING_BLOCK, (
            f"PACT_ROUTING_BLOCK is missing the "
            f"`{self.TEAMMATE_MARKER_PREFIX}` substring — routing logic "
            f"in spawned teammates cannot match what peer_inject emits."
        )

    def test_session_init_team_create_emits_orchestrator_marker(self):
        """session_init.py's `_team_create` string literal must contain
        the exact orchestrator marker that the routing block searches for.

        `_team_create` is a local variable inside a function so we can't
        import it — assert via source-text read instead.
        """
        source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        # Locate the _team_create assignment and verify the orchestrator
        # marker appears within the literal that follows.
        assert "_team_create = (" in source, (
            "session_init.py is missing the `_team_create` assignment — "
            "schema drift since this test was written. Update the test "
            "anchor."
        )
        create_idx = source.find("_team_create = (")
        # Take a 2000-char window starting at the assignment to cover
        # the full string literal (which is ~600 chars).
        create_region = source[create_idx : create_idx + 2000]
        assert self.ORCHESTRATOR_MARKER in create_region, (
            f"session_init.py `_team_create` string literal must contain "
            f"`{self.ORCHESTRATOR_MARKER}` so fresh lead sessions are "
            f"routed to the orchestrator bootstrap. Routing-block search "
            f"pattern drift."
        )

    def test_session_init_team_reuse_emits_orchestrator_marker(self):
        """session_init.py's `_team_reuse` string literal must contain
        the exact orchestrator marker that the routing block searches for.
        """
        source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        assert "_team_reuse = (" in source, (
            "session_init.py is missing the `_team_reuse` assignment — "
            "schema drift since this test was written. Update the test "
            "anchor."
        )
        reuse_idx = source.find("_team_reuse = (")
        reuse_region = source[reuse_idx : reuse_idx + 2000]
        assert self.ORCHESTRATOR_MARKER in reuse_region, (
            f"session_init.py `_team_reuse` string literal must contain "
            f"`{self.ORCHESTRATOR_MARKER}` so resumed lead sessions are "
            f"routed to the orchestrator bootstrap. Routing-block search "
            f"pattern drift."
        )

    def test_peer_inject_prelude_template_emits_teammate_marker(self):
        """peer_inject.py's `_BOOTSTRAP_PRELUDE_TEMPLATE` must, after
        format() substitution, contain the exact teammate marker prefix
        the routing block searches for.
        """
        from peer_inject import _BOOTSTRAP_PRELUDE_TEMPLATE

        rendered = _BOOTSTRAP_PRELUDE_TEMPLATE.format(agent_name="sample-agent")
        assert self.TEAMMATE_MARKER_PREFIX in rendered, (
            f"peer_inject.py `_BOOTSTRAP_PRELUDE_TEMPLATE` (after format) "
            f"must contain `{self.TEAMMATE_MARKER_PREFIX}` so spawned "
            f"teammates are routed to the teammate bootstrap. Routing-"
            f"block search pattern drift."
        )

    def test_core_dispatch_template_emits_teammate_marker(self):
        """The Agent Teams Dispatch template in skills/orchestration/SKILL.md
        is the FOURTH production emission site for the teammate marker
        (alongside session_init.py _team_create/_team_reuse and
        peer_inject.py _BOOTSTRAP_PRELUDE_TEMPLATE — though session_init
        emits the orchestrator marker, not the teammate marker).

        The dispatch template is how the lead spawns specialists as
        teammates: the `prompt=` parameter of the `Task(...)` call embeds
        `YOUR PACT ROLE: teammate ({name})` so the spawned teammate's context
        carries the marker the routing block searches for. If the
        template drifts and drops the marker, spawned teammates will not
        self-bootstrap via the routing block and will lack team-protocol
        context — silent breakage.

        A sibling test in test_agents_structure.py::TestDispatchTemplatePrelude
        asserts the exact placeholder form `YOUR PACT ROLE: teammate ({name})`.
        This test adds a coarser cross-file-invariant check inside the
        TestMarkerConsistency class so the fourth emission site is
        visible in the same place as the other three.

        Note: the dispatch template moved from bootstrap.md to the
        orchestrator core file in #414 R3 (bootstrap restructure),
        then to skills/orchestration/SKILL.md in #452.
        """
        text = self.CORE_PATH.read_text(encoding="utf-8")
        region = self._core_dispatch_region(text)
        assert region, (
            "skills/orchestration/SKILL.md missing the Agent Teams Dispatch "
            "`MANDATORY` callout anchor — cannot locate dispatch template "
            "region."
        )
        assert self.TEAMMATE_MARKER_PREFIX in region, (
            f"skills/orchestration/SKILL.md Agent Teams Dispatch template "
            f"must contain `{self.TEAMMATE_MARKER_PREFIX}` inside the "
            f"dispatch region so teammates spawned via the dispatch "
            f"pattern receive the marker the routing block searches for. "
            f"Routing-block search pattern drift — spawned teammates "
            f"will not self-bootstrap."
        )

    def test_marker_consistency_end_to_end(self):
        """End-to-end tripwire: every marker substring the routing block
        searches for must be emitted by EVERY production site registered
        below. Acts as a tripwire if someone:
          (a) adds a new marker pattern to the routing block without
              wiring up a corresponding emitter, OR
          (b) drops the marker from ANY single emitter while the other
              emitters still hold the line.

        The (b) case matters because the PACT routing architecture is
        multi-layer: the lead session path (session_init), the spawned
        teammate path via hook injection (peer_inject), and the spawned
        teammate path via dispatch template (skills/orchestration/SKILL.md)
        are all independently load-bearing. A silent drop in any one of
        them breaks a specific code path without the unit tests on the
        other paths noticing — which is exactly the kind of drift this
        tripwire exists to catch.

        Note that session_init emits the ORCHESTRATOR marker (to lead
        sessions), while peer_inject and skills/orchestration/SKILL.md emit
        the TEAMMATE marker prefix (to spawned teammates). That split is
        intentional — the routing block uses each marker to dispatch to
        a different bootstrap skill.
        """
        from shared.claude_md_manager import PACT_ROUTING_BLOCK
        from peer_inject import _BOOTSTRAP_PRELUDE_TEMPLATE

        session_init_source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        rendered_prelude = _BOOTSTRAP_PRELUDE_TEMPLATE.format(
            agent_name="sample-agent"
        )
        core_text = self.CORE_PATH.read_text(encoding="utf-8")
        core_dispatch_region = self._core_dispatch_region(core_text)
        assert core_dispatch_region, (
            "skills/orchestration/SKILL.md missing the Agent Teams Dispatch "
            "`MANDATORY` callout anchor — cannot locate dispatch template "
            "region."
        )

        # For each marker the routing block searches for, verify EVERY
        # registered production emission site contains it. Missing from
        # even one emitter fires the tripwire.
        marker_to_emitters = {
            self.ORCHESTRATOR_MARKER: [
                ("session_init.py (_team_create/_team_reuse)", session_init_source),
            ],
            self.TEAMMATE_MARKER_PREFIX: [
                ("peer_inject.py (_BOOTSTRAP_PRELUDE_TEMPLATE)", rendered_prelude),
                ("skills/orchestration/SKILL.md (Agent Teams Dispatch template)", core_dispatch_region),
            ],
        }

        for marker, emitters in marker_to_emitters.items():
            assert marker in PACT_ROUTING_BLOCK, (
                f"Routing block does not search for `{marker}` — test "
                f"fixture is stale. Update the test or the routing block."
            )
            missing = [
                name for name, source in emitters if marker not in source
            ]
            assert not missing, (
                f"Routing block searches for `{marker}` but the following "
                f"production emission site(s) do not contain it: "
                f"{missing}. Registered emitters: "
                f"{[name for name, _ in emitters]}. Routing is broken "
                f"for this code path — a teammate or lead reaching the "
                f"broken emitter will not self-bootstrap."
            )


# ---------------------------------------------------------------------------
# #366 F1: File locking retrofit for concurrent SessionStart safety
# ---------------------------------------------------------------------------
# These tests cover the file_lock context manager and the concurrent-write
# behavior of remove_stale_kernel_block() and update_pact_routing().
#
# Why this matters: both functions perform read-mutate-write on managed
# CLAUDE.md files. Without a lock, two concurrent session_init hooks
# (e.g., user resumes session A in one window + starts session B in another
# on the same project) can interleave, and the last writer wins. Before
# this retrofit, update_session_info's SESSION_START block could be clobbered
# by a concurrent update_pact_routing write. Sidecar fcntl lock serializes
# the critical section.


class TestFileLockContextManager:
    """file_lock(target_file) acquires/releases an fcntl sidecar lock."""

    def test_sequential_acquisitions_work(self, tmp_path):
        """Acquire, release, re-acquire must succeed without blocking."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("content", encoding="utf-8")

        with file_lock(target):
            pass  # Exited cleanly; lock released

        with file_lock(target):
            pass  # Second acquisition must not block or raise

        # Sidecar exists and is not cleaned up (by design)
        sidecar = tmp_path / ".CLAUDE.md.lock"
        assert sidecar.exists()

    def test_sidecar_path_shape(self, tmp_path):
        """Sidecar lock must be `{parent}/.{name}.lock` adjacent to target."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        with file_lock(target):
            sidecar = tmp_path / ".CLAUDE.md.lock"
            assert sidecar.exists()
            # Sidecar is NOT the target itself
            assert sidecar != target

    def test_sidecar_has_secure_permissions(self, tmp_path):
        """Sidecar lock file should be 0o600 to match CLAUDE.md permissions."""
        import stat
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        sidecar = tmp_path / ".CLAUDE.md.lock"
        if sidecar.exists():
            sidecar.unlink()

        with file_lock(target):
            pass

        mode = stat.S_IMODE(sidecar.stat().st_mode)
        # Allow umask to tighten but not widen the permissions
        assert mode & 0o077 == 0, (
            f"Sidecar lock leaks permissions to group/other: {oct(mode)}"
        )

    def test_concurrent_acquisition_blocks_then_succeeds(self, tmp_path):
        """Thread A holds the lock; Thread B's acquire blocks until A releases."""
        import threading
        import time as _time
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        holder_has_lock = threading.Event()
        holder_release = threading.Event()
        b_acquired_at: list[float] = []
        holder_released_at: list[float] = []

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                # Hold the lock until main signals release
                holder_release.wait(timeout=5)
                holder_released_at.append(_time.monotonic())

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2), "holder thread never acquired"

        # Kick off the waiter in a second thread so we can release the
        # holder while the waiter is blocked in acquire.
        def waiter():
            with file_lock(target):
                b_acquired_at.append(_time.monotonic())

        w = threading.Thread(target=waiter)
        w.start()

        # Give the waiter a brief moment to enter the acquire loop, then
        # release the holder.
        _time.sleep(0.3)
        holder_release.set()

        t.join(timeout=5)
        w.join(timeout=5)
        assert not t.is_alive()
        assert not w.is_alive()
        assert len(b_acquired_at) == 1, "waiter did not acquire after release"
        assert len(holder_released_at) == 1
        # Waiter's acquire must happen AFTER holder's release (ordering proof)
        assert b_acquired_at[0] >= holder_released_at[0] - 0.05, (
            "waiter acquired before holder released — lock ordering broken"
        )

    def test_timeout_raises_timeouterror(self, tmp_path, monkeypatch):
        """If the lock cannot be acquired within the timeout, TimeoutError."""
        import threading
        from shared.claude_md_manager import file_lock
        from shared import claude_md_manager as cmm

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        # Shrink the timeout so the test completes quickly
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3)

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        # Second acquire must time out
        with pytest.raises(TimeoutError) as exc_info:
            with file_lock(target):
                pass

        assert "Failed to acquire lock" in str(exc_info.value)
        assert ".CLAUDE.md.lock" in str(exc_info.value)

        # Clean up the holder
        holder_release.set()
        t.join(timeout=5)

    def test_exception_in_body_releases_lock(self, tmp_path):
        """A raise inside `with file_lock(...)` must still release the lock."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        class _MarkerError(Exception):
            pass

        with pytest.raises(_MarkerError):
            with file_lock(target):
                raise _MarkerError("boom")

        # Re-acquire must succeed — if the finally clause didn't release,
        # this would deadlock until the default 5s timeout.
        with file_lock(target):
            pass


class TestRemoveStaleKernelBlockLocking:
    """Concurrent remove_stale_kernel_block calls do not corrupt the file."""

    def test_concurrent_writes_preserve_managed_block(self, mock_home):
        """Two concurrent threads running remove_stale_kernel_block on the
        same home CLAUDE.md must converge to a clean, valid final state.

        Both threads start with the same input (markers present). With the
        sidecar lock, the read-mutate-write is serialized: the second
        thread sees the already-migrated content and is an idempotent
        no-op. Final state must contain user content verbatim, with no
        markers remaining.
        """
        import threading
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User preamble\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "stale kernel body\n"
            "<!-- PACT_END -->\n"
            "User trailing\n",
            encoding="utf-8",
        )

        results: list[str | None] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(remove_stale_kernel_block())
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2

        # Final file must be well-formed: user content preserved, no markers
        final = target.read_text(encoding="utf-8")
        assert "User preamble" in final
        assert "User trailing" in final
        assert "PACT_START" not in final
        assert "PACT_END" not in final
        assert "stale kernel body" not in final

        # Exactly one of the two workers did the removal work; the other
        # saw the already-migrated content and returned None (idempotent).
        # Order is non-deterministic, so just assert the multiset.
        assert results.count(
            "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        ) == 1
        assert results.count(None) == 1


class TestUpdatePactRoutingLocking:
    """Concurrent update_pact_routing calls do not corrupt the project file."""

    def test_concurrent_writes_preserve_managed_block(
        self, tmp_path, monkeypatch
    ):
        """Two concurrent threads running update_pact_routing on the same
        project CLAUDE.md must converge to a clean, valid final state.

        Both threads start with a file containing the stale orchestrator
        line and NO routing block. With the sidecar lock, the read-mutate-
        write is serialized: exactly one thread does the insert, the
        other sees the canonical state and is a no-op (modulo idempotent
        re-write). Final state must contain the canonical routing block
        exactly once and preserve all user content.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "User-owned content line 1\n"
            "User-owned content line 2\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        results: list[str | None] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(update_pact_routing())
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2

        # Final file must contain exactly ONE canonical routing block
        final = claude_md.read_text(encoding="utf-8")
        assert final.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1, (
            "Concurrent writes accumulated multiple routing blocks — lock "
            "failed to serialize"
        )
        assert final.count("<!-- PACT_ROUTING_END -->") == 1
        assert "User-owned content line 1" in final
        assert "User-owned content line 2" in final
        assert "# Project Memory" in final

    def test_concurrent_writes_preserve_session_start_block(
        self, tmp_path, monkeypatch
    ):
        """Regression: pre-fix failure mode — update_pact_routing racing
        with a SESSION_START block write could clobber the session info.

        We simulate the real session_init sequence: one thread writes a
        SESSION_START block, another thread runs update_pact_routing.
        With the lock in place, update_pact_routing's read sees the
        SESSION_START block and its write preserves it. Without the
        lock, the routing thread's stale read would drop the block
        on write.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "User content\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Kick off update_pact_routing concurrently with another thread
        # that repeatedly writes a SESSION_START block. The lock must
        # serialize them so the final file contains both blocks.
        stop = threading.Event()
        errors: list[BaseException] = []

        def session_writer():
            """Simulates update_session_info by overwriting the file with
            a SESSION_START block plus whatever we read. This is the real
            failure mode: it must also go through the lock, but the pre-
            fix code did not. Here we exercise the lock on the routing
            side — the session writer is best-effort.
            """
            try:
                from shared.claude_md_manager import file_lock
                while not stop.is_set():
                    with file_lock(claude_md):
                        content = claude_md.read_text(encoding="utf-8")
                        if "<!-- SESSION_START -->" not in content:
                            new = content.replace(
                                "# Project Memory\n",
                                "# Project Memory\n\n<!-- SESSION_START -->\n"
                                "## Current Session\n"
                                "- Team: pact-abc123\n"
                                "<!-- SESSION_END -->\n",
                            )
                            claude_md.write_text(new, encoding="utf-8")
                    # Tiny yield so the routing thread gets a chance
                    import time as _time
                    _time.sleep(0.01)
            except BaseException as e:
                errors.append(e)

        def routing_worker():
            try:
                update_pact_routing()
            except BaseException as e:
                errors.append(e)

        sw = threading.Thread(target=session_writer)
        sw.start()
        # Give session_writer a moment to establish the SESSION_START block
        import time as _time
        _time.sleep(0.05)

        rw = threading.Thread(target=routing_worker)
        rw.start()
        rw.join(timeout=10)

        stop.set()
        sw.join(timeout=5)
        assert not errors, f"Thread errors: {errors}"

        final = claude_md.read_text(encoding="utf-8")
        # Both managed blocks must be present: routing preserved the
        # session block through its read-mutate-write cycle.
        assert "<!-- SESSION_START -->" in final, (
            "SESSION_START block was clobbered by concurrent "
            "update_pact_routing — lock did not serialize the critical "
            "section properly"
        )
        assert "<!-- SESSION_END -->" in final
        assert (
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
            in final
        )
        assert "<!-- PACT_ROUTING_END -->" in final
        assert "# Project Memory" in final
        assert "User content" in final

    def test_timeout_returns_fail_open_status(
        self, tmp_path, monkeypatch
    ):
        """When the lock cannot be acquired, update_pact_routing returns a
        'Failed to acquire lock ... Routing update skipped ...' status string.

        The 'failed' substring is load-bearing: session_init.py's routing
        check (`'failed' in msg.lower()`) uses it to send the message to
        system_messages (user-visible error surface) rather than silently
        into context_parts. A 5s lock acquisition failure is a genuine
        concurrency problem the user should see, not a silent fallback.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing, file_lock
        from shared import claude_md_manager as cmm

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\nUser content\n", encoding="utf-8"
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3)

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(claude_md):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        result = update_pact_routing()

        assert result is not None
        # MUST contain "failed" — session_init routes on
        # `'failed' in msg.lower()` to system_messages for user visibility.
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        assert "routing update skipped" in result.lower()

        holder_release.set()
        t.join(timeout=5)

    def test_remove_stale_kernel_block_timeout_returns_fail_open_status(
        self, mock_home
    ):
        """Companion test for remove_stale_kernel_block's timeout path.

        Same routing rationale as `test_timeout_returns_fail_open_status`:
        the 'failed' substring routes the message to system_messages for
        user visibility.
        """
        import threading
        from shared.claude_md_manager import remove_stale_kernel_block, file_lock
        from shared import claude_md_manager as cmm
        from unittest.mock import patch

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: pact -->\n"
            "kernel\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        with patch.object(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3):
            result = remove_stale_kernel_block()

        assert result is not None
        # MUST contain "failed" — session_init routes on
        # `'failed' in msg.lower()` to system_messages for user visibility.
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        assert "kernel-block migration skipped" in result.lower()
        # File was NOT mutated (timeout is fail-open, write never happened)
        assert "PACT_START" in target.read_text(encoding="utf-8")

        holder_release.set()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# #404: CLAUDE.md restructuring — PACT_MANAGED and PACT_MEMORY boundaries
# ---------------------------------------------------------------------------
# These tests cover the three new pieces introduced by issue #404:
#
#   _build_migrated_content(content)  — pure function that transforms old-format
#       CLAUDE.md content into the new managed structure with PACT_MANAGED_START/
#       END and PACT_MEMORY_START/END boundaries.
#
#   migrate_to_managed_structure()  — integration wrapper that does file I/O,
#       file_lock, symlink guard, and idempotent check around _build_migrated_content.
#
#   ensure_project_memory_md() template update  — the template for new files now
#       includes the PACT_MANAGED and PACT_MEMORY markers.
#
# Risk tier: HIGH — migration runs on every existing project CLAUDE.md.

# Marker constants pinned locally so drift in the implementation is caught.
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"
_ROUTING_START = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END = "<!-- PACT_ROUTING_END -->"
_SESSION_START = "<!-- SESSION_START -->"
_SESSION_END = "<!-- SESSION_END -->"


class TestBuildMigratedContentCurrentFormat:
    """_build_migrated_content() with the standard pre-#404 CLAUDE.md layout.

    This is the most common input shape: has the # Project Memory heading,
    routing block, session block, and all three memory sections with real
    content under them.
    """

    CURRENT_FORMAT = (
        "# Project Memory\n"
        "\n"
        "This file contains project-specific memory managed by the PACT framework.\n"
        "\n"
        f"{_ROUTING_START}\n"
        "## PACT Routing\n"
        "\n"
        "Some routing instructions here.\n"
        f"{_ROUTING_END}\n"
        "\n"
        f"{_SESSION_START}\n"
        "## Current Session\n"
        "- Team: pact-abc123\n"
        f"{_SESSION_END}\n"
        "\n"
        "## Retrieved Context\n"
        "Some retrieved context.\n"
        "\n"
        "## Pinned Context\n"
        "\n"
        "### Important pin\n"
        "Pin content here.\n"
        "\n"
        "## Working Memory\n"
        "### 2026-04-12 21:00\n"
        "Some working memory entry.\n"
    )

    def test_output_has_managed_boundary(self):
        """Migrated output must start with PACT_MANAGED_START and contain PACT_MANAGED_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert result.startswith(_MANAGED_START)
        assert _MANAGED_END in result

    def test_output_has_memory_boundary(self):
        """Migrated output must contain PACT_MEMORY_START and PACT_MEMORY_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _MEMORY_START in result
        assert _MEMORY_END in result

    def test_new_heading_replaces_old(self):
        """Legacy '# Project Memory' is replaced by the single canonical H1
        '# PACT Framework and Managed Project Memory'."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "# PACT Framework and Managed Project Memory" in result
        # Old heading text should not survive as a top-level heading
        lines = result.splitlines()
        top_level_headings = [l for l in lines if l.startswith("# ") and not l.startswith("## ")]
        assert "# Project Memory" not in top_level_headings
        # Single-H1 invariant: only one top-level heading (no interior H1
        # inside PACT_MEMORY).
        assert len(top_level_headings) == 1

    def test_memory_sections_inside_memory_boundary(self):
        """Retrieved Context, Pinned Context, and Working Memory must appear
        between PACT_MEMORY_START and PACT_MEMORY_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]

        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_memory_content_preserved(self):
        """Content under memory sections must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "Some retrieved context." in result
        assert "Pin content here." in result
        assert "Some working memory entry." in result

    def test_routing_block_preserved(self):
        """The routing block (between its markers) must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _ROUTING_START in result
        assert _ROUTING_END in result
        assert "Some routing instructions here." in result

    def test_session_block_preserved(self):
        """The session block (between its markers) must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _SESSION_START in result
        assert _SESSION_END in result
        assert "pact-abc123" in result

    def test_stale_orchestrator_line_stripped(self):
        """The 'loaded from ~/.claude/CLAUDE.md' line must be removed."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "project-specific memory managed by the PACT framework" not in result

    def test_marker_ordering(self):
        """Markers must appear in the correct order: MANAGED_START -> ROUTING ->
        SESSION -> MEMORY_START -> memory sections -> MEMORY_END -> MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        positions = {
            "managed_start": result.index(_MANAGED_START),
            "routing_start": result.index(_ROUTING_START),
            "routing_end": result.index(_ROUTING_END),
            "session_start": result.index(_SESSION_START),
            "session_end": result.index(_SESSION_END),
            "memory_start": result.index(_MEMORY_START),
            "memory_end": result.index(_MEMORY_END),
            "managed_end": result.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["routing_start"]
        assert positions["routing_end"] < positions["session_start"]
        assert positions["session_end"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_no_interior_h1_inside_memory_boundary(self):
        """With the single-H1 restructure (#404), PACT_MEMORY must not contain
        any top-level heading — memory sections begin directly with their H2
        headings. Prior to the restructure, an interior H1 ('# Project Memory
        (PACT-Managed)') lived inside the memory boundary; that has been
        dropped in favor of a single outer H1.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]

        memory_lines = memory_region.splitlines()
        h1_headings = [l for l in memory_lines if l.startswith("# ") and not l.startswith("## ")]
        assert h1_headings == [], (
            f"PACT_MEMORY region should have no H1 headings, found: {h1_headings}"
        )

    def test_pinned_context_sub_heading_preserved(self):
        """Sub-headings (### level) under memory sections must be preserved."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "### Important pin" in result
        assert "### 2026-04-12 21:00" in result


class TestBuildMigratedContentMissingSections:
    """_build_migrated_content() with various sections absent."""

    def test_no_routing_block(self):
        """File with no routing block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # No routing markers should appear
        assert _ROUTING_START not in result
        assert "Some context." in result

    def test_no_session_block(self):
        """File with no session block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "## PACT Routing\n"
            "Routing content.\n"
            f"{_ROUTING_END}\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _ROUTING_START in result
        assert _SESSION_START not in result

    def test_no_memory_sections(self):
        """File with no memory headings should get default memory sections."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "## PACT Routing\n"
            "Routing content.\n"
            f"{_ROUTING_END}\n"
        )

        result = _build_migrated_content(content)

        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # Default sections should be created
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_empty_content(self):
        """Empty string input should produce a minimal valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content("")

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        assert "# PACT Framework and Managed Project Memory" in result

    def test_only_heading_no_sections(self):
        """Just the heading line, nothing else."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content("# Project Memory\n")

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert "## Retrieved Context" in result  # default sections


class TestBuildMigratedContentUserContent:
    """_build_migrated_content() must preserve user content outside PACT sections."""

    def test_user_content_after_memory_sections(self):
        """User-owned sections after the last memory section must appear
        after PACT_MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "## My Custom Section\n"
            "User's custom notes here.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## My Custom Section" in after_managed
        assert "User's custom notes here." in after_managed

    def test_user_content_between_memory_sections(self):
        """A non-memory heading between memory sections splits into user content."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## User Notes\n"
            "Private user notes.\n"
            "\n"
            "## Working Memory\n"
            "Working memory data.\n"
        )

        result = _build_migrated_content(content)

        # User notes should be outside managed block
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## User Notes" in after_managed
        assert "Private user notes." in after_managed

        # Memory sections should be inside memory boundary
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Working Memory" in memory_region
        assert "Working memory data." in memory_region

    def test_user_content_before_memory_sections(self):
        """Content before any memory heading (after routing/session extraction)
        is classified as user content.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## My Early Section\n"
            "Early user content.\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## My Early Section" in after_managed
        assert "Early user content." in after_managed

    def test_user_h1_heading_survives_migration(self):
        """A user-owned H1 heading (e.g., '# My Project Notes') must be
        preserved outside the PACT_MANAGED block after migration.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "# My Project Notes\n"
            "Important notes the user added.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "# My Project Notes" in after_managed
        assert "Important notes the user added." in after_managed

    def test_trailing_user_content_still_below_pact_managed(self):
        """Round 6 item 4: user content APPENDED after memory sections
        must still land BELOW ``PACT_MANAGED_END`` (the existing behavior).
        The preamble fix only moves PRE-memory user content; it must not
        regress POST-memory user content placement.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "## Trailing Notes\n"
            "Stuff the user appended later.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Trailing Notes" in after_managed
        assert "Stuff the user appended later." in after_managed

        # And it must NOT have migrated into the preamble region
        managed_start_idx = result.index(_MANAGED_START)
        before_managed = result[:managed_start_idx]
        assert "## Trailing Notes" not in before_managed
        assert "Stuff the user appended later." not in before_managed

class TestBuildMigratedContentAdversarial:
    """Adversarial and edge-case inputs for _build_migrated_content().

    Tests the MEDIUM uncertainty flagged by the coder: user headings that
    match memory section names could be mis-classified.
    """

    def test_user_heading_matching_memory_name_exact(self):
        """A user heading that exactly matches a memory section name
        (e.g., '## Retrieved Context') is classified as a memory section.

        This is the expected behavior — the classifier uses heading text
        to identify memory sections. Users should not have headings with
        these exact names outside the memory area.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "PACT-managed retrieval data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "PACT-managed retrieval data." in memory_region

    def test_similar_but_different_heading_not_captured(self):
        """Headings that are similar but not exact matches should NOT be
        classified as memory sections (e.g., '## Retrieved Context (old)').
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context (old)\n"
            "User's old retrieval notes.\n"
            "\n"
            "## Retrieved Context\n"
            "Actual PACT context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # The exact-match section should be in memory
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Actual PACT context." in memory_region

        # The near-match should be user content
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Retrieved Context (old)" in after_managed
        assert "User's old retrieval notes." in after_managed

    def test_duplicate_memory_headings(self):
        """If '## Working Memory' appears twice, both instances and their
        content should end up in the memory region.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "First working memory block.\n"
            "\n"
            "## Working Memory\n"
            "Second working memory block.\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "First working memory block." in memory_region
        assert "Second working memory block." in memory_region

    def test_content_with_no_headings_at_all(self):
        """Flat text with no headings and no PACT-managed triggers is
        treated as user content and lands BELOW ``PACT_MANAGED_END``.

        Round 10 design decision: all user content migrates below the
        managed block. The prior (round 6) preamble mechanism placed such
        content above MANAGED_START, but that required fence-awareness in
        every downstream parser. Removing preamble handling eliminates
        the fence-awareness bug class at the cost of this one-time
        content relocation.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = "Just some random text in a CLAUDE.md file.\nAnother line.\n"

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        # User content lands BELOW MANAGED_END (round 10 contract)
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "Just some random text" in after_managed
        assert "Another line." in after_managed

        # And it must NOT appear ABOVE MANAGED_START
        managed_start_idx = result.index(_MANAGED_START)
        before_managed = result[:managed_start_idx]
        assert "Just some random text" not in before_managed

    def test_partial_routing_markers_no_end(self):
        """If only PACT_ROUTING_START is present with no END, the routing
        block regex won't match, so the marker text remains as-is in the
        remaining content (treated as user text).
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "Orphaned routing content.\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # With no matching END marker, routing extraction fails — the
        # start marker and its content flow into user_parts
        assert _MANAGED_START in result
        assert _MANAGED_END in result

    def test_partial_session_markers_start_only(self):
        """If only SESSION_START is present with no SESSION_END, the session
        block regex won't match, so the marker text remains in the remaining
        content. Must not crash or corrupt the output.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_SESSION_START}\n"
            "## Current Session\n"
            "- Team: pact-orphaned\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # Output must still have valid structure
        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # The orphaned SESSION_START text should survive somewhere
        assert "pact-orphaned" in result

    def test_memory_heading_with_trailing_whitespace(self):
        """'## Retrieved Context   ' (trailing spaces) must still match
        as a memory heading since the code uses line.rstrip().
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context   \n"
            "Context data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Context data." in memory_region

    def test_large_content_under_pinned_context(self):
        """Pinned Context with multiple sub-sections and substantial content
        must all be preserved inside the memory boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        pinned_content = "\n".join(
            [f"### Pin {i}\nContent for pin {i}.\n" for i in range(10)]
        )
        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            f"{pinned_content}\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        for i in range(10):
            assert f"Content for pin {i}." in memory_region

    def test_code_fenced_memory_heading_preserved_as_user_content(self):
        """A memory heading like `## Pinned Context` inside a fenced code
        block (```...```) must NOT be extracted as a real memory section.
        It is example/documentation text and belongs with surrounding user
        content.

        Regression guard for round-4 Item 3: the classifier previously did
        not track code fence state, so fenced `## Pinned Context` inside a
        user docs block was mis-classified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Notes on how memory works\n"
            "Here's an example of what a pinned context block looks like:\n"
            "\n"
            "```markdown\n"
            "## Pinned Context\n"
            "This is example documentation, not real memory data.\n"
            "```\n"
            "\n"
            "End of notes.\n"
        )

        result = _build_migrated_content(content)

        # The fenced `## Pinned Context` text must survive outside the
        # PACT_MANAGED region as user content.
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Notes on how memory works" in after_managed
        assert "```markdown" in after_managed
        assert "## Pinned Context" in after_managed
        assert "example documentation, not real memory data" in after_managed
        assert "End of notes." in after_managed

        # The memory region must be empty (no real memory headings existed).
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "example documentation" not in memory_region
        assert "End of notes." not in memory_region

    def test_code_fence_does_not_mask_real_memory_sections_elsewhere(self):
        """A fenced example of a memory heading in docs PLUS a real memory
        section elsewhere must still classify each correctly: the fenced one
        stays with user content, the real one is extracted into memory.

        Regression guard for round-4 Item 3: the fence toggle state must be
        per-fence, not latched — after a fence closes, subsequent real memory
        headings must still be detected.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Documentation\n"
            "Example of a memory heading:\n"
            "\n"
            "```\n"
            "## Working Memory\n"
            "(this is just an example)\n"
            "```\n"
            "\n"
            "## Retrieved Context\n"
            "Real retrieved context data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "- Real entry 1\n"
        )

        result = _build_migrated_content(content)

        # Fenced example must stay outside managed boundary as user content
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Documentation" in after_managed
        assert "(this is just an example)" in after_managed

        # Real memory sections after the fence must be extracted into memory
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Real retrieved context data." in memory_region
        assert "Real entry 1" in memory_region

        # The fenced example text must NOT bleed into the memory region
        assert "(this is just an example)" not in memory_region

    def test_fenced_stale_orchestrator_line_preserved(self):
        """Round 7 item 2 / round 8 item 4: `_strip_legacy_lines` must be
        fence-aware, and the regression test must exercise the POST-CUTOFF
        stripper path (not only the preamble-only path).

        Adversarial scenario: a user's CLAUDE.md has a fenced code block
        inside the managed region (e.g., inside `## Working Memory`) that
        quotes the legacy v3.16.2 orchestrator-loader line verbatim as
        migration documentation. The line is NOT part of the live PACT
        config — it's an example inside a fenced code block.

        Pre-round-7 behavior (verify-backend-coder-7 counter-test):
          `_STALE_ORCHESTRATOR_LINE_RE` was compiled with `re.MULTILINE`
          and applied via `_STALE_ORCHESTRATOR_LINE_RE.sub("", content)`
          against the full content. `^...$\\n?` matched every occurrence
          at any line boundary, INCLUDING lines inside user-authored
          fenced code blocks.

        Post-round-7: `_strip_legacy_lines` walks lines, tracks in-fence
        state via `^\\s*```` (round 7), and round 8 item 1 adds tilde
        fence state independently. Lines inside EITHER fence type are
        preserved byte-for-byte.

        Round 8 item 4 fixture fix: the pre-round-8 fixture had NO
        non-fenced PACT trigger — the fenced `# Project Memory` heading
        was the ONLY trigger in the file, and it was inside a fence, so
        `_find_preamble_cutoff` (also fence-aware) returned
        `len(content)`. That meant `remaining` was empty and the
        post-cutoff `_strip_legacy_lines` call NEVER saw the fenced
        content — only the preamble_text call did. Since the fenced line
        was in the preamble, the preamble-only stripping was enough.
        Round-8 audit caught that the counter-test-by-revert claim in
        the original docstring only exercised ONE of the two
        `_strip_legacy_lines` call sites.

        The new fixture places the fenced stale line INSIDE `## Working
        Memory`, with a non-fenced `# Project Memory` heading in
        position 0. `_find_preamble_cutoff` returns 0 → preamble is
        empty → full file is `remaining` → the post-cutoff
        `_strip_legacy_lines` call at line ~1330 is exercised by the
        fenced content. Reverting `_strip_legacy_lines` to the
        fence-unaware form now fails this test via the post-cutoff
        path, not the preamble path.

        Counter-test-by-revert validated for this regression: temporarily
        reverting `_strip_legacy_lines` to the fence-unaware
        `_STALE_ORCHESTRATOR_LINE_RE.sub` form makes this test fail
        (the fenced line disappears from the `## Working Memory`
        section's body inside the PACT_MANAGED region).
        """
        from shared.claude_md_manager import _build_migrated_content

        stale_line = (
            "The global PACT Orchestrator is loaded from "
            "`~/.claude/CLAUDE.md`."
        )

        # Non-fenced `# Project Memory` at position 0 → preamble cutoff is 0
        # → post-cutoff `remaining` is the full file body → fenced stale
        # line is scrubbed by the POST-cutoff `_strip_legacy_lines` call,
        # not the preamble-only call. This is the critical difference from
        # the pre-round-8 fixture.
        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "Migration notes for future reference:\n"
            "\n"
            "```markdown\n"
            "This is what the old v3.16.2 template looked like:\n"
            f"{stale_line}\n"
            "```\n"
            "\n"
            "The line inside the fence is an EXAMPLE, not live config.\n"
        )

        result = _build_migrated_content(content)

        # The PACT_MANAGED block must exist (the migration still runs).
        assert _MANAGED_START in result
        assert _MANAGED_END in result

        managed_start_idx = result.index(_MANAGED_START)
        managed_end_idx = result.index(_MANAGED_END)
        before_managed = result[:managed_start_idx]
        managed_region = result[managed_start_idx:managed_end_idx]
        after_managed = result[managed_end_idx:]

        # (a) PRIMARY ASSERTION — the fenced stale line MUST survive
        # verbatim INSIDE the managed region's `## Working Memory`
        # section. Pre-round-7 it was destroyed by the post-cutoff
        # `_STALE_ORCHESTRATOR_LINE_RE.sub`. Pre-round-8-fixture the
        # test couldn't detect this specific failure because the fenced
        # content was in the preamble path, not the remaining path.
        assert stale_line in managed_region, (
            "Fenced stale-orchestrator line must survive byte-for-byte "
            "inside the managed region. If this fails, the post-cutoff "
            "`_strip_legacy_lines` call at line ~1330 is still "
            "fence-unaware."
        )

        # (b) The fence boundaries survive intact in the managed region.
        fence_open_idx = managed_region.find("```markdown")
        assert fence_open_idx != -1, (
            "Opening ```markdown fence must survive in managed region"
        )
        fence_close_idx = managed_region.find("```\n", fence_open_idx + 1)
        assert fence_close_idx != -1, (
            "Closing ``` fence must survive in managed region"
        )
        assert fence_open_idx < fence_close_idx, (
            "Closing fence must appear after opening fence"
        )

        # (c) The stale line must appear BETWEEN the opening and closing
        # fences inside the managed region — not orphaned elsewhere, and
        # not stripped from its position inside the fence.
        stale_idx = managed_region.find(stale_line)
        assert fence_open_idx < stale_idx < fence_close_idx, (
            "Stale line must remain INSIDE the fence region inside the "
            "managed block, not be relocated or stripped"
        )

        # (d) The non-fenced narrative prose around the fence survives
        # too (inside the managed region's Working Memory section).
        assert "Migration notes for future reference:" in managed_region
        assert (
            "The line inside the fence is an EXAMPLE, not live config."
            in managed_region
        )

        # (e) The file had NO preamble (first line was `# Project
        # Memory`), so there should be no user content above
        # PACT_MANAGED_START.
        assert stale_line not in before_managed
        assert "```markdown" not in before_managed

        # (f) The trailing user region (below PACT_MANAGED_END) is also
        # empty of the fenced content.
        assert stale_line not in after_managed
        assert "```markdown" not in after_managed


    def test_tilde_fenced_memory_heading_classified_as_user_content(self):
        """PR #404 round 12 item 1: tilde-fenced ``## Working Memory``
        inside user content must NOT be extracted as a real memory section.

        The prior body classifier used a backtick-only boolean toggle
        (``in_code_fence``). Tilde fences (``~~~~``) are a valid CommonMark
        §4.5 alternative that the classifier must recognize.

        Counter-test-by-revert validated: reverting to the boolean toggle
        causes this test to fail because the tilde fence is invisible to
        ``startswith("```")``, so the ``## Working Memory`` heading inside
        the tilde fence is misclassified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## User Notes\n"
            "Example of a memory section in tilde fence:\n"
            "\n"
            "~~~~markdown\n"
            "## Working Memory\n"
            "This is example text inside a tilde fence.\n"
            "~~~~\n"
            "\n"
            "End of user notes.\n"
        )

        result = _build_migrated_content(content)

        # Fenced content must land as user content below MANAGED_END
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## User Notes" in after_managed
        assert "~~~~markdown" in after_managed
        assert "## Working Memory" in after_managed
        assert "example text inside a tilde fence" in after_managed
        assert "End of user notes." in after_managed

        # Memory region must not contain the fenced heading
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "example text inside a tilde fence" not in memory_region

    def test_nested_4backtick_fence_does_not_toggle_on_inner_3backtick(self):
        """PR #404 round 12 item 1: a ```````` outer fence containing an
        inner ````` must not toggle the fence state.

        CommonMark §4.5: closing fence must have run length >= opening.
        A 4-backtick outer fence containing a 3-backtick inner example
        line must stay open through the inner line. A boolean toggle
        would falsely close on the inner ````` and expose the remainder
        of the outer fence body to heading classification.

        Counter-test-by-revert validated: reverting to the boolean toggle
        causes the inner ````` to close the fence, so ``## Pinned Context``
        on the next line is misclassified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Migration Guide\n"
            "Example with nested fences:\n"
            "\n"
            "````markdown\n"
            "Here is a code block:\n"
            "```\n"
            "## Pinned Context\n"
            "Some inner content\n"
            "```\n"
            "End of inner example.\n"
            "````\n"
            "\n"
            "Post-fence user notes.\n"
        )

        result = _build_migrated_content(content)

        # All fenced content must land as user content below MANAGED_END
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Migration Guide" in after_managed
        assert "````markdown" in after_managed
        assert "## Pinned Context" in after_managed
        assert "Some inner content" in after_managed
        assert "Post-fence user notes." in after_managed

        # Memory region must not contain the fenced heading
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Some inner content" not in memory_region


class TestBuildMigratedContentOrphanRoutingMarker:
    """Round 5 item 5: `_build_migrated_content` must strip orphan
    PACT_ROUTING markers (exactly one of START/END present) to avoid
    leaving the downstream `update_pact_routing` with a half-block it
    cannot repair.

    Scenarios:
      - User manually deletes half of the routing block during editing
      - Partial-write corruption truncates the file mid-routing-block
      - A hand-rolled CLAUDE.md with a leftover marker from an older
        plugin version

    Recovery: drop the orphan marker and any adjacent `## PACT Routing`
    H2 block. A fresh routing block will be installed by the next
    `update_pact_routing` call, so no real content is lost (the routing
    block is plugin-authored template content rebuilt from
    PACT_ROUTING_BLOCK).
    """

    _ROUTING_START = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
    _ROUTING_END = "<!-- PACT_ROUTING_END -->"

    def test_orphan_routing_start_is_stripped(self):
        """A PACT_ROUTING_START marker with no matching END must be removed
        along with its adjacent `## PACT Routing` H2 block.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{self._ROUTING_START}\n"
            "## PACT Routing\n"
            "Orphaned routing prose that should be removed.\n"
            "\n"
            "## Working Memory\n"
            "- Real memory entry\n"
        )

        result = _build_migrated_content(content)

        # Orphan marker is gone from the final output
        assert self._ROUTING_START not in result
        # The adjacent `## PACT Routing` heading is gone too
        assert "## PACT Routing" not in result
        # The orphaned routing prose is gone
        assert "Orphaned routing prose" not in result

        # Real memory content survives
        assert "Real memory entry" in result

    def test_orphan_routing_end_is_stripped(self):
        """A PACT_ROUTING_END marker with no matching START must also be
        removed — the symmetric case.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## PACT Routing\n"
            "Orphaned routing prose.\n"
            "\n"
            f"{self._ROUTING_END}\n"
            "\n"
            "## Working Memory\n"
            "- Real memory entry\n"
        )

        result = _build_migrated_content(content)

        assert self._ROUTING_END not in result
        assert "## PACT Routing" not in result
        assert "Orphaned routing prose" not in result
        assert "Real memory entry" in result

    def test_paired_routing_markers_preserved_as_routing_block(self):
        """Regression guard: when BOTH markers are present, the orphan-strip
        branch must not fire — the routing block should be extracted and
        re-emitted normally.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{self._ROUTING_START}\n"
            "## PACT Routing\n"
            "Canonical routing prose.\n"
            f"{self._ROUTING_END}\n"
            "\n"
            "## Working Memory\n"
            "- Real memory entry\n"
        )

        result = _build_migrated_content(content)

        # Both markers present in result — routing block preserved
        assert self._ROUTING_START in result
        assert self._ROUTING_END in result
        assert "Canonical routing prose." in result
        assert "Real memory entry" in result

    def test_orphan_start_no_adjacent_h2(self):
        """A PACT_ROUTING_START marker with no adjacent `## PACT Routing`
        heading — just the marker in isolation — must still be stripped.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "Some user paragraph before the orphan.\n"
            "\n"
            f"{self._ROUTING_START}\n"
            "\n"
            "## Working Memory\n"
            "- Real memory entry\n"
        )

        result = _build_migrated_content(content)

        assert self._ROUTING_START not in result
        assert "Real memory entry" in result
        # The paragraph before the orphan is user content and must survive
        assert "Some user paragraph before the orphan." in result


class TestBuildMigratedContentIdempotent:
    """_build_migrated_content() has its own idempotency guard (round 5 item 2).

    The guard checks for MANAGED_START_MARKER at the top of the function and
    returns the content unchanged if already migrated. The integration wrapper
    migrate_to_managed_structure also has this guard, so the two layers provide
    belt-and-suspenders protection. Duplicating it at the pure-function layer
    means any caller (including tests and future consumers) gets the safety
    for free and double-passes can never double-wrap.
    """

    def test_double_pass_is_idempotent(self):
        """Calling _build_migrated_content twice returns the same content.

        Round 5, item 2: _build_migrated_content now guards on
        MANAGED_START_MARKER presence and returns unchanged content on the
        second call. The prior behavior was to double-wrap; that contract
        was intentional documentation, not a design goal.
        """
        from shared.claude_md_manager import _build_migrated_content

        original = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )
        first_pass = _build_migrated_content(original)

        # Second pass: the guard returns first_pass unchanged
        second_pass = _build_migrated_content(first_pass)

        assert second_pass.count(_MANAGED_START) == 1
        assert second_pass == first_pass

    def test_already_migrated_input_returns_unchanged(self):
        """Passing already-wrapped content returns byte-identical output."""
        from shared.claude_md_manager import _build_migrated_content

        already_managed = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "## Pinned Context\n"
            "## Working Memory\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
        )

        result = _build_migrated_content(already_managed)

        assert result == already_managed

    def test_migrate_to_managed_structure_guards_double_call(self, tmp_path, monkeypatch):
        """The integration wrapper migrate_to_managed_structure() prevents
        double-migration via its idempotency check.
        """
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\n## Retrieved Context\n\n## Working Memory\n"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        first = migrate_to_managed_structure()
        assert first is not None

        content_after = claude_md.read_text()
        assert content_after.count(_MANAGED_START) == 1

        second = migrate_to_managed_structure()
        assert second is None  # no-op

        assert claude_md.read_text() == content_after  # unchanged


class TestMigrateToManagedStructure:
    """Integration tests for migrate_to_managed_structure() — the wrapper
    that does file I/O around _build_migrated_content().
    """

    def test_migrates_existing_file(self, tmp_path, monkeypatch):
        """migrate_to_managed_structure() rewrites an old-format file."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "Migrated" in result
        content = claude_md.read_text(encoding="utf-8")
        assert _MANAGED_START in content
        assert _MANAGED_END in content
        assert _MEMORY_START in content
        assert _MEMORY_END in content

    def test_idempotent_noop_when_already_migrated(self, tmp_path, monkeypatch):
        """Second call returns None (no-op) when PACT_MANAGED_START is present."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # First call migrates
        first = migrate_to_managed_structure()
        assert first is not None

        content_after_first = claude_md.read_text(encoding="utf-8")

        # Second call is no-op
        second = migrate_to_managed_structure()
        assert second is None

        # File unchanged
        assert claude_md.read_text(encoding="utf-8") == content_after_first

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Returns None when CLAUDE_PROJECT_DIR not set."""
        from shared.claude_md_manager import migrate_to_managed_structure

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = migrate_to_managed_structure()

        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        """Returns None when file doesn't exist (new_default source)."""
        from shared.claude_md_manager import migrate_to_managed_structure

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = migrate_to_managed_structure()

        assert result is None

    def test_returns_none_when_read_fails(self, tmp_path, monkeypatch):
        """OSError on read_text -> returns None (file appears unreadable).

        Exercises the `except OSError: return None` branch at the read_text
        call inside migrate_to_managed_structure(). Uses identity-scoped
        patching so file_lock internals are not affected.
        """
        from unittest.mock import patch as mock_patch
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(claude_md):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with mock_patch.object(Path, "read_text", selective_read_text):
            result = migrate_to_managed_structure()

        assert result is None

    def test_symlink_guard(self, tmp_path, monkeypatch):
        """Returns 'skipped' message when target is a symlink."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        real_file = tmp_path / "real_claude.md"
        real_file.write_text("# Project Memory\n")
        claude_md = project_dir / "CLAUDE.md"
        claude_md.symlink_to(real_file)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "skipped" in result.lower()

    def test_migrated_file_has_secure_permissions(self, tmp_path, monkeypatch):
        """Migrated file should have 0o600 permissions."""
        import stat
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Retrieved Context\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        migrate_to_managed_structure()

        file_mode = stat.S_IMODE(claude_md.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    def test_timeout_returns_fail_open_status(self, tmp_path, monkeypatch):
        """Lock timeout returns a 'failed' message for session_init routing."""
        import threading
        from shared.claude_md_manager import migrate_to_managed_structure, file_lock
        from shared import claude_md_manager as cmm

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3)

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(claude_md):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        result = migrate_to_managed_structure()

        assert result is not None
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        # File was NOT mutated (fail-open)
        assert _MANAGED_START not in claude_md.read_text(encoding="utf-8")

        holder_release.set()
        t.join(timeout=5)

    def test_oserror_on_write_returns_failure(self, tmp_path, monkeypatch):
        """OSError during write_text returns a 'failed' message."""
        from unittest.mock import patch as mock_patch
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        with mock_patch.object(
            type(claude_md), "write_text", side_effect=OSError("disk full")
        ):
            result = migrate_to_managed_structure()

        assert result is not None
        assert "failed" in result.lower()

    def test_works_with_dot_claude_location(self, tmp_path, monkeypatch):
        """Migration should work for files in the .claude/ subdirectory."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()
        claude_md = dot_claude / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "Migrated" in result
        content = claude_md.read_text(encoding="utf-8")
        assert _MANAGED_START in content


class TestEnsureProjectMemoryMdNewMarkers:
    """Verify that ensure_project_memory_md() template includes #404 markers."""

    def test_created_file_has_managed_boundary(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md must have PACT_MANAGED markers."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert _MANAGED_START in content
        assert _MANAGED_END in content

    def test_created_file_has_memory_boundary(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md must have PACT_MEMORY markers."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert _MEMORY_START in content
        assert _MEMORY_END in content

    def test_created_file_has_new_top_heading(self, tmp_path, monkeypatch):
        """Template heading is the single canonical H1
        '# PACT Framework and Managed Project Memory'."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "# PACT Framework and Managed Project Memory" in content
        # Single-H1 invariant: only one top-level heading in the template.
        lines = content.splitlines()
        top_level_headings = [l for l in lines if l.startswith("# ") and not l.startswith("## ")]
        assert len(top_level_headings) == 1
        assert top_level_headings[0] == "# PACT Framework and Managed Project Memory"

    def test_created_file_memory_sections_inside_boundary(self, tmp_path, monkeypatch):
        """Memory sections in new file must be inside PACT_MEMORY boundary."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        mem_start_idx = content.index(_MEMORY_START)
        mem_end_idx = content.index(_MEMORY_END)
        memory_region = content[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_created_file_marker_ordering(self, tmp_path, monkeypatch):
        """Markers in newly created file must follow the canonical order."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()

        positions = {
            "managed_start": content.index(_MANAGED_START),
            "routing_start": content.index(_ROUTING_START),
            "session_start": content.index(_SESSION_START),
            "memory_start": content.index(_MEMORY_START),
            "memory_end": content.index(_MEMORY_END),
            "managed_end": content.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["routing_start"]
        assert positions["routing_start"] < positions["session_start"]
        assert positions["session_start"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_created_file_no_interior_h1_inside_memory_boundary(
        self, tmp_path, monkeypatch
    ):
        """With the single-H1 restructure (#404), the template must not put any
        top-level heading inside PACT_MEMORY — memory sections begin directly
        with their H2 headings.
        """
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        mem_start_idx = content.index(_MEMORY_START)
        mem_end_idx = content.index(_MEMORY_END)
        memory_region = content[mem_start_idx:mem_end_idx]
        memory_lines = memory_region.splitlines()
        h1_headings = [l for l in memory_lines if l.startswith("# ") and not l.startswith("## ")]
        assert h1_headings == [], (
            f"PACT_MEMORY region should have no H1 headings, found: {h1_headings}"
        )


class TestManagedMarkerConstants:
    """Verify the new marker constants match expected values.

    Same pattern as TestPactRoutingBlock — pin the exact values here
    so accidental drift in the implementation is caught.
    """

    def test_managed_start_marker_value(self):
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert MANAGED_START_MARKER == _MANAGED_START

    def test_managed_end_marker_value(self):
        from shared.claude_md_manager import MANAGED_END_MARKER
        assert MANAGED_END_MARKER == _MANAGED_END

    def test_memory_start_marker_value(self):
        from shared.claude_md_manager import MEMORY_START_MARKER
        assert MEMORY_START_MARKER == _MEMORY_START

    def test_memory_end_marker_value(self):
        from shared.claude_md_manager import MEMORY_END_MARKER
        assert MEMORY_END_MARKER == _MEMORY_END

    def test_managed_marker_names_avoid_pact_start_collision(self):
        """Marker names use PACT_MANAGED, NOT PACT_START, to avoid collision
        with old kernel block markers that remove_stale_kernel_block() searches for.
        """
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert "PACT_MANAGED_START" in MANAGED_START_MARKER
        assert "<!-- PACT_START" not in MANAGED_START_MARKER

    def test_markers_mutually_distinct(self):
        """No marker string may be a prefix of — or a substring of — any other.

        A prefix collision would break substring search via ``str.startswith``.
        A substring collision would break ``in``-operator lookup at any
        position. For example, if ``<!-- PACT_MANAGED_START`` were embedded
        inside ``<!-- PACT_META_MANAGED_START_V2``, an ``in``-operator lookup
        for the shorter marker would spuriously match the longer one even
        though ``startswith`` would not. This is the semantic invariant —
        an explicit cardinality check (len == 4) tested the counting mistake,
        not the substring-safety contract.

        Both relations are checked (``startswith`` AND ``in``) so future
        marker additions with embedded patterns are caught.
        """
        from shared import claude_md_manager as cmm
        markers = [
            cmm.MANAGED_START_MARKER,
            cmm.MANAGED_END_MARKER,
            cmm.MEMORY_START_MARKER,
            cmm.MEMORY_END_MARKER,
        ]
        for i, a in enumerate(markers):
            for j, b in enumerate(markers):
                if i == j:
                    continue
                assert not a.startswith(b), (
                    f"Marker prefix collision: {a!r} starts with {b!r}"
                )
                assert b not in a, (
                    f"Marker substring collision: {b!r} is contained in {a!r}"
                )

    def test_managed_title_symbol_matches_literal(self):
        """Round 6 item 5: symbol-level drift guard for ``MANAGED_TITLE``.

        The round 5 refactor extracted ``MANAGED_TITLE`` as a module
        constant so the three template sites (``ensure_project_memory_md``,
        ``_build_migrated_content``, and ``session_resume.update_session_info``
        Case 0) could not drift apart. This test pins the literal value so
        an accidental rename (e.g., "# PACT Framework" typo'd to
        "# PACT Framework" with an extra space) is caught by a targeted
        assertion rather than via indirect template-shape test failures.
        """
        from shared.claude_md_manager import MANAGED_TITLE
        assert MANAGED_TITLE == "# PACT Framework and Managed Project Memory"

    def test_managed_title_no_literal_copies_in_claude_md_manager(self):
        """PR #404: ensure the ``MANAGED_TITLE`` literal appears in
        ``claude_md_manager.py`` exactly 2 times (1 constant definition +
        1 docstring mention) — not hand-copied into a template string.

        This is a source-scan drift guard. The literal is allowed inside
        docstring examples and code comments (because those are
        documentation, not code that would drift), so the check counts
        assignment-style RHS occurrences and tolerates docstring mentions.
        The simple shape: the literal must appear no more than ``N+1``
        times in the source file where ``N`` is the allowed docstring /
        comment mentions (currently 1 — the migration strategy docstring).

        If a future refactor intentionally inlines the literal in a new
        comment/docstring, bump the allowed count here rather than
        allowing silent drift at a code site.

        Scope (round 7 item 4): this guard catches **copy-paste drift**
        of the literal title string across source files. It does NOT
        catch string fragmentation — e.g., a developer writing
        ``"# PACT " + "Framework and Managed Project Memory"`` or an
        f-string like ``f"# PACT Framework and {suffix}"``. That class
        of evasion is **out of scope** because the guard targets
        accidental drift (the common failure mode), not adversarial
        evasion. Strengthening to catch fragmentation would require AST
        parsing, which is disproportionate — a developer who
        deliberately hard-codes the title via concatenation is already
        breaking the single-source-of-truth pattern regardless of how
        they spell it, and the resulting bug surfaces through downstream
        tests that depend on ``MANAGED_TITLE`` consistency.
        """
        source_path = (
            Path(__file__).parent.parent
            / "hooks"
            / "shared"
            / "claude_md_manager.py"
        )
        source = source_path.read_text(encoding="utf-8")
        literal = "# PACT Framework and Managed Project Memory"
        # Allowed occurrences:
        #   - 1x MANAGED_TITLE constant definition (code)
        #   - 1x migration-strategy docstring mention in
        #     ``migrate_to_managed_structure``
        # Any additional copy means someone has inlined the literal at a
        # code site instead of referencing ``MANAGED_TITLE``, which is the
        # drift pattern item 5 guards against.
        count = source.count(literal)
        assert count == 2, (
            f"Expected exactly 2 occurrences of MANAGED_TITLE literal in "
            f"claude_md_manager.py (1 constant def + 1 docstring mention), "
            f"found {count}. A hand-copied literal indicates drift — use "
            f"the MANAGED_TITLE symbol at code sites instead."
        )

    def test_managed_title_no_literal_copies_in_session_resume(self):
        """PR #404: drift guard for ``session_resume.py``.

        ``update_session_info`` Case 0 (the fresh-file creation path)
        builds a PACT_MANAGED block and must use the imported
        ``MANAGED_TITLE`` symbol, not a hand-copied literal. One comment
        mention is tolerated; any additional occurrence indicates code-site
        drift.

        Scope (round 7 item 4): same bounds as the sibling guard on
        ``claude_md_manager.py`` — catches **copy-paste drift** of the
        literal title string, does NOT catch string fragmentation
        (concatenation, f-string interpolation). That is out of scope
        because the guard targets accidental drift, not adversarial
        evasion; catching fragmentation would require AST parsing and
        is disproportionate to the threat model.
        """
        source_path = (
            Path(__file__).parent.parent
            / "hooks"
            / "shared"
            / "session_resume.py"
        )
        source = source_path.read_text(encoding="utf-8")
        literal = "# PACT Framework and Managed Project Memory"
        # Allowed occurrences:
        #   - 1x docstring/comment mention documenting the Case 0 template
        # No code-site copy is permitted; ``MANAGED_TITLE`` is imported at
        # the top of the file and used as a symbol at the single site.
        count = source.count(literal)
        assert count == 1, (
            f"Expected exactly 1 occurrence of MANAGED_TITLE literal in "
            f"session_resume.py (comment mention only), found {count}. "
            f"Use the imported MANAGED_TITLE symbol at code sites."
        )


class TestSessionInitMigrationIntegration:
    """Verify that session_init.py calls migrate_to_managed_structure()
    and routes its return value correctly.
    """

    def test_session_init_calls_migration(self):
        """session_init.py must contain a call to migrate_to_managed_structure."""
        session_init_path = (
            Path(__file__).parent.parent / "hooks" / "session_init.py"
        )
        source = session_init_path.read_text(encoding="utf-8")
        assert "migrate_to_managed_structure()" in source

    def test_migration_result_routing_failed(self):
        """session_init routes 'failed'/'skipped' migration messages to
        system_messages, not context_parts.
        """
        session_init_path = (
            Path(__file__).parent.parent / "hooks" / "session_init.py"
        )
        source = session_init_path.read_text(encoding="utf-8")
        # The routing logic checks for "failed" or "skipped" in the message
        assert '"failed"' in source or "'failed'" in source
        assert '"skipped"' in source or "'skipped'" in source


class TestStripLegacyLines:
    """Direct unit tests for `_strip_legacy_lines`.

    Round-4 Item 7: the existing coverage is through two indirect paths
    (`update_pact_routing` and `_build_migrated_content`), both of which
    mask signal if `_strip_legacy_lines` itself regresses — downstream
    assertions are dominated by the routing-block text. These tests
    exercise the helper directly so a regression in the legacy-stripping
    logic produces a targeted failure.
    """

    # The exact stale line the v3.16.2 template carried. Pinned here as a
    # fixture constant so drift in _STALE_ORCHESTRATOR_LINE_RE is caught.
    STALE_LINE = (
        "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."
    )

    def test_strips_exact_stale_line_with_trailing_period(self):
        """Canonical form: stale line with trailing period and newline."""
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            f"{self.STALE_LINE}\n"
            "\n"
            "## Retrieved Context\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE not in result
        # Surrounding content survives
        assert "# Project Memory" in result
        assert "## Retrieved Context" in result

    def test_strips_stale_line_without_trailing_period(self):
        """Regex uses `\\.?` to match the line with OR without a trailing
        period — the v3.16.2 template has the period; hand-edited copies
        may lack it.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        # No trailing period after `CLAUDE.md`
        no_period = "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`"
        content = (
            "# Project Memory\n"
            f"{no_period}\n"
            "## Retrieved Context\n"
        )

        result = _strip_legacy_lines(content)

        assert no_period not in result

    def test_absent_stale_line_returns_content_unchanged(self):
        """No-op case: when the stale line is absent, the helper returns
        content identical to the input. Idempotency guarantee.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some retrieved data.\n"
            "\n"
            "## Working Memory\n"
        )

        result = _strip_legacy_lines(content)

        assert result == content, (
            "_strip_legacy_lines must be a no-op when no stale line is present"
        )

    def test_idempotent_across_two_invocations(self):
        """Applying `_strip_legacy_lines` twice is the same as applying it
        once — the function is pure and deterministic. This matches the
        expectation of shared helper usage (both `update_pact_routing` and
        `_build_migrated_content` call it; running both consecutively must
        not corrupt content).
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            f"{self.STALE_LINE}\n"
            "## Pinned Context\n"
            "### Pin\n"
            "Body.\n"
        )

        once = _strip_legacy_lines(content)
        twice = _strip_legacy_lines(once)

        assert once == twice
        assert self.STALE_LINE not in once

    def test_preserves_other_content_mentioning_orchestrator(self):
        """A line that mentions the word "orchestrator" but is not the
        exact stale template line must NOT be stripped. The regex is
        anchored to the full stale-line text.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "The orchestrator loads from somewhere else entirely.\n"
            "See also: orchestrator governance.\n"
            "\n"
            "## Pinned Context\n"
        )

        result = _strip_legacy_lines(content)

        # Both lines mention "orchestrator" but don't match the stale pattern
        assert "The orchestrator loads from somewhere else entirely." in result
        assert "See also: orchestrator governance." in result

    # Round 8 item 5: direct fence unit tests for `_strip_legacy_lines`.
    # Prior coverage exercised the walker's fence branches through
    # `_build_migrated_content` and `test_fenced_stale_orchestrator_line_preserved`
    # (an end-to-end driver). Direct unit tests produce targeted failures when
    # the walker's fence-state tracking regresses, without the downstream
    # migration-pipeline assertions masking the signal.

    def test_strip_legacy_lines_backtick_fenced_stale_line_preserved(self):
        """A stale line INSIDE a backtick fence must be preserved byte-for-byte.

        Round 7 item 2 added fence-awareness via `in_code_fence`; this unit
        test pins that behavior so a regression in the backtick-fence branch
        (distinct from the tilde branch below) fails here instead of only
        failing via the end-to-end driver.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside backtick fence must be preserved verbatim"
        )
        # The fence boundaries also survive intact.
        assert result.count("```") == 2

    def test_strip_legacy_lines_tilde_fenced_stale_line_preserved(self):
        """Round 8 item 1: tilde fences (CommonMark §4.5) must be recognized.

        Pre-round-8 `_strip_legacy_lines` only recognized backtick (```)
        fences. A user-authored `~~~` fence containing the stale line was
        treated as non-fenced content and silently destroyed. Round 8 adds
        an independent `in_tilde_fence` state so the stripper skips
        tilde-fenced content the same way it skips backtick-fenced content.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "~~~\n"
            f"{self.STALE_LINE}\n"
            "~~~\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside tilde fence must be preserved verbatim "
            "(round 8 item 1)"
        )
        assert result.count("~~~") == 2

    def test_strip_legacy_lines_unclosed_fence_at_eof_preserves_content(self):
        """An unclosed fence at EOF must still protect the content below.

        When a user's content has an opening fence with no matching close
        (file ends before the fence is closed), the walker should remain
        in-fence through to the end of the content. Any stale-line match
        inside the unclosed fence must be preserved.

        This is CommonMark-compatible: §4.5 explicitly allows unclosed
        fenced code blocks to extend to the end of the document.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "more content that never gets un-fenced\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside unclosed fence must be preserved — the "
            "walker's in-fence state must not reset at EOF"
        )
        assert "more content that never gets un-fenced" in result

    def test_strip_legacy_lines_indented_fence_preserves_content(self):
        """A fence with leading whitespace (`    \\`\\`\\``) is still detected.

        The walker uses `stripped = line.lstrip()` before checking for
        ```/~~~ prefixes, so an indented fence opener is recognized. The
        pattern matches Markdown conventions where a fence inside a list
        item or blockquote may be indented.

        Note: CommonMark §4.5 technically requires closing fences to have
        the same or less indentation than the opener, and treats leading
        whitespace >3 spaces as indicating an indented code block instead
        of a fenced block. Our walker uses a simpler "any leading
        whitespace" convention for symmetry with the other walker sites
        (_find_preamble_cutoff, staleness._find_terminator_offset,
        working_memory._find_terminator_offset) — this is documented
        divergence from strict CommonMark, sufficient for CLAUDE.md use.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "- List item with fenced example:\n"
            "  ```\n"
            f"  {self.STALE_LINE}\n"
            "  ```\n"
        )

        result = _strip_legacy_lines(content)

        # The stale line must survive — it's inside an indented fence body.
        assert self.STALE_LINE in result, (
            "Stale line inside indented fence must be preserved — the "
            "walker's `stripped = line.lstrip()` normalization must "
            "recognize leading-whitespace fence openers"
        )

    def test_strip_legacy_lines_consecutive_fences_state_resets(self):
        """Two consecutive fences: content inside BOTH must survive.

        The walker toggles fence state on each fence line, so after a
        fence closes, the next fence opener should correctly re-enter
        the in-fence state. This test pins the toggle behavior — a
        regression that fails to reset state (e.g., a sticky in-fence
        flag) would strip the stale line in the second fence.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
            "\n"
            "Some non-fenced narrative.\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
        )

        result = _strip_legacy_lines(content)

        # Both occurrences of the stale line (inside each fence) must
        # survive. The non-fenced narrative line in between is not a
        # stale-line match, so it also survives.
        assert result.count(self.STALE_LINE) == 2, (
            "Both fenced stale lines must survive — state must reset "
            "correctly between consecutive fences"
        )
        assert "Some non-fenced narrative." in result

    def test_strip_legacy_lines_backtick_inside_tilde_fence_is_inert(self):
        """Independent-state invariant: a ``` line INSIDE a ~~~ fence
        must NOT toggle backtick state.

        This test pins the CommonMark §4.5 guarantee that fence
        delimiters of different characters are independent. Without the
        independent-state tracking, a user's tilde-fenced example that
        shows a backtick-fence snippet inside it would see the backtick
        "line" treated as a fence boundary, flip the backtick state, and
        fool the walker into exiting in-fence state early. The stale
        line that follows would then be stripped.

        Pairs with `test_strip_legacy_lines_tilde_fenced_stale_line_preserved`
        which exercises the simple tilde-only case; this test exercises
        the nested / interaction case.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "~~~\n"
            "Example: how to open a code fence in Markdown:\n"
            "```\n"  # This ``` line is INSIDE a ~~~ fence, must be inert
            f"{self.STALE_LINE}\n"
            "```\n"  # Still inside ~~~, still inert
            "More content inside the tilde fence.\n"
            "~~~\n"
        )

        result = _strip_legacy_lines(content)

        # The stale line must survive — it's inside a ~~~ fence, and
        # the nested ``` lines do not toggle backtick state.
        assert self.STALE_LINE in result, (
            "Stale line inside backtick-inside-tilde nested fence must "
            "be preserved — backtick and tilde fence states must be "
            "independent (CommonMark §4.5)"
        )
        # All content lines also survive
        assert "Example: how to open a code fence in Markdown:" in result
        assert "More content inside the tilde fence." in result

    def test_strip_legacy_lines_length_tracked_fence_state(self):
        """Round 10 item 9: CommonMark §4.5 variable-length fence support.

        A 4-backtick outer fence (````) containing a 3-backtick inner
        example (```) must NOT close the outer fence on the inner line.
        Pre-round-10 behavior used boolean toggles which would falsely
        close the fence on the 3-backtick line, exposing the remainder
        of the outer fence body to legacy-line stripping.

        Counter-test-by-revert: revert `_strip_legacy_lines` to boolean
        toggles -> this test MUST fail; restore length-tracked state ->
        this test MUST pass.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "````markdown\n"
            "Here is an inner example:\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
            "Still inside the 4-backtick outer fence.\n"
            "````\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside 4-backtick outer fence (with 3-backtick "
            "inner example) must be preserved — length-tracked fence "
            "state required (round 10 item 9)"
        )
        assert "Still inside the 4-backtick outer fence." in result
        assert "Here is an inner example:" in result

    def test_strip_legacy_lines_length_tracked_tilde_fence(self):
        """Same as above but with tilde fences: 4-tilde outer, 3-tilde inner."""
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "~~~~\n"
            "Inner tilde example:\n"
            "~~~\n"
            f"{self.STALE_LINE}\n"
            "~~~\n"
            "Still inside the 4-tilde outer fence.\n"
            "~~~~\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside 4-tilde outer fence must be preserved"
        )
        assert "Still inside the 4-tilde outer fence." in result

    def test_strip_legacy_lines_closing_fence_needs_no_info_string(self):
        """CommonMark §4.5: closing fence cannot have an info string.
        A line with ``` followed by non-whitespace is NOT a closing fence.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "```\n"
            "```python\n"  # NOT a closing fence (has info string)
            f"{self.STALE_LINE}\n"
            "```\n"  # This IS the real close
        )

        result = _strip_legacy_lines(content)

        # The stale line is inside the fence (```python is not a close)
        assert self.STALE_LINE in result

    def test_strip_legacy_lines_no_trailing_newline(self):
        """PR #404 round 12 item 6: content with no trailing newline
        must still strip the stale line on the final line.

        Covers lines 217-219: the ``nl == -1`` branch where the last
        line in content has no ``\\n`` terminator.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        # Stale line is the last line, no trailing newline
        content = f"# Notes\n{self.STALE_LINE}"

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE not in result
        assert "# Notes" in result


class TestEnsureProjectMemoryMdOSErrorOnMkdir:
    """PR #404 round 12 item 6: OSError during .claude/ directory creation
    in ``ensure_project_memory_md``.

    Covers lines 823-824: the ``except OSError`` branch where
    ``target_dir.mkdir`` fails (e.g., read-only filesystem).
    """

    def test_oserror_during_mkdir_returns_failure_message(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Make the .claude directory creation fail
        dot_claude = tmp_path / ".claude"
        # Create a file at .claude to block mkdir
        dot_claude.write_text("blocker")

        result = ensure_project_memory_md()

        assert result is not None
        assert "failed" in result.lower() or "skipped" in result.lower()


class TestBuildMigratedContentOrphanMarkerAtEof:
    """PR #404 round 12 item 6: orphan routing marker at EOF with no
    trailing newline and no following section terminator.

    Covers lines 974 and 993: the ``scan_from = len(content)`` and
    ``strip_end = len(content)`` branches in the orphan-marker handler
    inside ``_build_migrated_content``.
    """

    def test_orphan_start_marker_at_eof_no_trailing_newline(self):
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "data here\n"
            # Orphan start marker at EOF, no newline, no end marker
            f"{_ROUTING_START}"
        )

        result = _build_migrated_content(content)

        # Migration should still succeed
        assert _MANAGED_START in result
        assert _MANAGED_END in result
        # The orphan marker should be stripped, not carried through
        assert _ROUTING_START not in result


class TestExtractManagedRegion:
    """Round 10: tests for extract_managed_region helper."""

    def test_returns_region_and_offset(self):
        """When both markers are present, returns (region_text, offset)."""
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        content = (
            "user preamble\n"
            f"{MANAGED_START_MARKER}\n"
            "managed content here\n"
            f"{MANAGED_END_MARKER}\n"
            "user epilogue\n"
        )

        result = extract_managed_region(content)
        assert result is not None
        region_text, offset = result
        assert "managed content here" in region_text
        assert "user preamble" not in region_text
        assert "user epilogue" not in region_text
        # offset should point to just after MANAGED_START_MARKER
        assert content[offset:].startswith("\nmanaged")

    def test_returns_none_when_start_missing(self):
        """When MANAGED_START_MARKER is absent, returns None."""
        from shared.claude_md_manager import (
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        content = f"some content\n{MANAGED_END_MARKER}\n"
        assert extract_managed_region(content) is None

    def test_returns_none_when_end_missing(self):
        """When MANAGED_END_MARKER is absent, returns None."""
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            extract_managed_region,
        )

        content = f"{MANAGED_START_MARKER}\nsome content\n"
        assert extract_managed_region(content) is None

    def test_returns_none_for_empty_string(self):
        """Empty string has no markers."""
        from shared.claude_md_manager import extract_managed_region

        assert extract_managed_region("") is None

    def test_offset_enables_correct_writeback(self):
        """The offset should allow callers to map managed-region positions
        back to full-content positions for write-back operations.
        """
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        preamble = "user notes above\n\n"
        managed_body = "## Pinned Context\npin content\n"
        epilogue = "\nuser notes below\n"

        content = (
            preamble
            + MANAGED_START_MARKER + "\n"
            + managed_body
            + MANAGED_END_MARKER + "\n"
            + epilogue
        )

        result = extract_managed_region(content)
        assert result is not None
        region_text, offset = result

        # Find "pin content" in the region
        local_idx = region_text.find("pin content")
        assert local_idx >= 0

        # Map back to full content
        full_idx = local_idx + offset
        assert content[full_idx:full_idx + len("pin content")] == "pin content"

