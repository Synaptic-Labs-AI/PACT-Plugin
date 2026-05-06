"""
Three-surface architectural split enforcement (Arch-F4).

Pins the documentation discipline that the three surfaces of the
session-start ritual stay disjoint:

1. **Directive surface** (`pact-plugin/agents/pact-orchestrator.md` §2):
   describes WHAT the ritual achieves and WHEN to invoke it. Owns
   imperative governance (YOUR FIRST ACTION, when-to-re-invoke).
   FORBIDDEN: shell mechanics (mkdir, touch, literal session-dir paths,
   shell command examples). Persona body must NOT duplicate the
   command file's mechanics.

2. **Mechanics surface** (`pact-plugin/commands/bootstrap.md`):
   per-step procedural instructions for the orchestrator to execute.
   Owns shell commands, marker-write semantics, placeholder substitution.
   FORBIDDEN: governance directives ("S5 POLICY", "Algedonic",
   "Completion Authority", "MISSION", "MOTTO", "SACROSANCT",
   "FINAL MANDATE") and persona-body-style mandatory imperatives
   (`**You MUST`) outside `## Step N` sections. Mandatory voice INSIDE
   Step sections is legitimate (e.g., "Substitute `<path>` with...").

3. **Enforcement surface** (`pact-plugin/hooks/bootstrap_gate.py`):
   PreToolUse hook blocking Edit/Write/Agent/NotebookEdit until the
   bootstrap-complete marker exists. NOT TESTED HERE — the gate's
   behavior is covered by `test_bootstrap_gate.py` (the test in this
   module is for the directive↔mechanics coupling only).

Design rationale (per architect-review of PR #641, finding #4):
the mechanics-vs-enforcement coupling is structurally enforced via
the load-bearing `bootstrap-complete` literal (`shared.BOOTSTRAP_MARKER_NAME`
referenced by `bootstrap_gate.py`, `bootstrap_prompt_gate.py`, and
`commands/bootstrap.md`). The directive-vs-mechanics coupling is
documentation-discipline only; this test makes it structural.

Apply MEMORY pattern `convention_must_be_enforced_not_just_documented.md`:
README-only conventions decay under contributor pressure; structural
tests catch drift at the moment of introduction.
"""

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PERSONA_PATH = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
BOOTSTRAP_MD_PATH = PLUGIN_ROOT / "commands" / "bootstrap.md"


def _extract_persona_section_2(text: str) -> str:
    """Return the body of `## 2. Session-Start Ritual` from the persona,
    bounded by the next `---` separator OR the next top-level `## ` heading.

    Raises AssertionError if the section heading is not found — pins the
    invariant that persona §2 exists.
    """
    # Match the §2 heading line and capture forward.
    section_match = re.search(
        r"^## 2\.\s+Session-Start Ritual\s*$",
        text,
        re.MULTILINE,
    )
    assert section_match is not None, (
        "Persona body must contain a `## 2. Session-Start Ritual` heading. "
        "If the renumber drifted (e.g., to ## 3.), update this regex AND "
        "audit downstream cross-references."
    )
    start = section_match.end()
    # End of section: next `---` separator OR next `## ` heading.
    rest = text[start:]
    end_sep = rest.find("\n---\n")
    next_h2_match = re.search(r"\n## [^\n]", rest)
    candidates = [c for c in (end_sep, next_h2_match.start() if next_h2_match else -1) if c >= 0]
    end = min(candidates) if candidates else len(rest)
    return rest[:end]


# =============================================================================
# Directive surface: persona §2 contains NO mechanics prose
# =============================================================================


class TestPersonaSection2ContainsNoMechanics:
    """Pins the directive↔mechanics asymmetry: persona §2 describes
    WHAT/WHEN; commands/bootstrap.md owns HOW. Mechanics in persona §2
    creates a drift surface where future contributors duplicate
    command-body text and the two sides desynchronize silently.
    """

    @pytest.fixture
    def section_2_body(self):
        return _extract_persona_section_2(
            PERSONA_PATH.read_text(encoding="utf-8")
        )

    @pytest.mark.parametrize(
        "label,pattern",
        [
            (
                "mkdir command",
                r"\bmkdir\b",
            ),
            (
                "touch command",
                r"\btouch\b",
            ),
            (
                "literal session-dir path with bootstrap-complete",
                r"/bootstrap-complete\b",
            ),
            (
                "fenced shell code block",
                r"```(?:sh|bash|shell|zsh)?\s*\n",
            ),
        ],
    )
    def test_persona_section_2_excludes_mechanics_pattern(
        self, section_2_body, label, pattern
    ):
        """Persona §2 must not contain shell mechanics. Each pattern
        names a load-bearing class of mechanics-leakage that, if it
        appeared, would create a duplicate-mechanics drift surface.

        Mechanics belong in `commands/bootstrap.md` Step-N sections; the
        persona §2 Read(file_path="../commands/bootstrap.md") cross-ref
        is the proper indirection.
        """
        match = re.search(pattern, section_2_body)
        assert match is None, (
            f"Persona §2 contains mechanics-leakage ({label}): "
            f"matched {match.group(0)!r} at offset {match.start()}. "
            f"Mechanics belong in commands/bootstrap.md, not the persona "
            f"body — see the three-surface split discipline at the top "
            f"of test_three_surface_split_enforcement.py."
        )


# =============================================================================
# Mechanics surface: bootstrap.md contains NO governance directives
# =============================================================================


class TestBootstrapCommandExcludesGovernanceDirectives:
    """Pins the mechanics↔governance asymmetry: commands/bootstrap.md
    owns per-step procedural mechanics; governance lives in the
    --agent-delivered persona body. Governance leakage into bootstrap.md
    creates the same v3.x duplication that PR #621 deliberately removed.

    Coverage extends `test_bootstrap_command_excludes_governance_fossils`
    (in test_628_coverage.py) with architect-review's recommended
    additions: `S5 POLICY`, `Algedonic`, `Completion Authority` (literal
    governance-section names) AND a pattern guard against
    `**You MUST ...**` mandatory imperatives outside Step-N sections.
    """

    GOVERNANCE_KEYWORDS = (
        "MISSION:",
        "MOTTO:",
        "FINAL MANDATE",
        "SACROSANCT",
        "S5 POLICY",
        "Algedonic",
        "Completion Authority",
    )

    @pytest.fixture
    def bootstrap_text(self):
        return BOOTSTRAP_MD_PATH.read_text(encoding="utf-8")

    @pytest.mark.parametrize("keyword", GOVERNANCE_KEYWORDS)
    def test_bootstrap_md_excludes_governance_keyword(
        self, bootstrap_text, keyword
    ):
        """Each governance keyword names a persona-body-owned section.
        Its presence in bootstrap.md indicates governance leaking into
        the mechanics surface.

        Case-insensitive substring check: a future contributor lowercasing
        a section heading (e.g., `## sacrosanct non-negotiables`) or
        mixing case (`Mission:`) would bypass a case-sensitive guard.
        Lowercase BOTH sides before comparing so all case variants are
        rejected.
        """
        bootstrap_lower = bootstrap_text.lower()
        keyword_lower = keyword.lower()
        assert keyword_lower not in bootstrap_lower, (
            f"commands/bootstrap.md contains governance keyword "
            f"{keyword!r} (case-insensitive); that content belongs in "
            f"the --agent-delivered persona body, not the per-session "
            f"ritual command. Re-locate to pact-orchestrator.md or remove."
        )

    def test_bootstrap_md_no_mandatory_imperative_outside_step_sections(
        self, bootstrap_text
    ):
        """The `**You MUST ...**` pattern (any case variant of MUST/must/Must)
        is the signature of persona-body imperative directive prose. It is
        legitimate INSIDE `## Step N — ...` sections (mechanics naturally
        use mandatory voice, e.g., "**Substitute `<path>` with ...**") but
        FORBIDDEN outside Step-N sections, where its presence indicates
        governance-style imperative leakage.

        Walks the document section-by-section. Each section starts at a
        `## ` heading and ends at the next `## ` heading or EOF. A section
        is a "Step section" iff its heading matches `## Step N — ...`.
        Non-Step sections are scanned for `**You (MUST|must|Must)` and any
        match fails the test. The triple-form variant catches all three
        common-case forms (all-caps mandate, lowercase imperative, leading-
        cap stylized) without false-firing on substring matches like
        `Mustard` (`\\b` boundary).
        """
        # Split into sections by `## ` heading lines
        section_starts = [
            m.start() for m in re.finditer(r"^## ", bootstrap_text, re.MULTILINE)
        ]
        # Append EOF as terminal boundary
        section_starts.append(len(bootstrap_text))

        offenders = []
        for i in range(len(section_starts) - 1):
            section = bootstrap_text[section_starts[i]:section_starts[i + 1]]
            heading_line_end = section.find("\n")
            heading = section[:heading_line_end] if heading_line_end >= 0 else section
            # Step sections are mechanics-allowed
            if re.match(r"## Step \d+\b", heading):
                continue
            # Non-Step section: check for **You (MUST|must|Must) pattern
            for match in re.finditer(
                r"\*\*[Yy]ou\s+(?:MUST|must|Must)\b",
                section,
            ):
                offenders.append(
                    (heading.strip(), match.group(0), match.start())
                )

        assert not offenders, (
            f"commands/bootstrap.md contains persona-body-style mandatory "
            f"imperative(s) `**You MUST` outside `## Step N` sections — "
            f"governance imperative leaking into mechanics surface. "
            f"Offenders:\n"
            + "\n".join(
                f"  - section {h!r}: {p!r} at offset {o}"
                for h, p, o in offenders
            )
            + "\n\n"
            "Mandatory voice belongs INSIDE Step-N sections (where it "
            "applies to mechanics like substitution rules) OR in the "
            "persona body's directive surface — not in bootstrap.md "
            "headers, intros, or non-Step sections."
        )
