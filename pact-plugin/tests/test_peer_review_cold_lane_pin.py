"""Structural pins for the cold reviewer lane in commands/peer-review.md.

The cold lane is a prose-only feature: an anonymous `general-purpose`
subagent dispatched with an information diet instead of a narrative. Its
independence lives in WHAT the dispatch text says, so the pins are TOKEN
pins inside the lane's own section — never file-wide, never full sentences.
A token living elsewhere in the file cannot satisfy a section-scoped arm,
and a rewording that keeps the load-bearing tokens survives.

COVERED, and this list is the scope:
- the lane section's anchor and the seven-clause dispatch contract tokens:
  AVOID's named paths and stores, CHANNELS' barred tools, the ARTIFACT
  PR-surface bar, the QUESTION's operative phrase, OPERATIONAL FACTS'
  tests/lint/path-convention content, and REPORT's null-legibility tokens;
- the lane's wiring exceptions: the skip sentence (no Task A + Task B, no
  variety stamp, no per-reviewer journal block), the null rule
  (review-did-not-happen, not clean), and the report-landing line
  (review_dispatch array + review_finding with reviewer cold-reviewer);
- the spawn shape: one inline Agent(subagent_type="general-purpose", ...)
  reference carrying NO name=, NO team_name=, NO teammate role prelude;
- the copy-paste guard: none of the warm dispatch machinery's tokens occur
  in the cold section;
- the always-lane bullet, the example-tree [Subagent] label, the
  division-of-labour sentence, and the clause-satisfaction paragraph
  appended after REVIEWER-MEMORY-CHANNEL;
- cross-surface coherence: peer-review.md carries the subagent cold
  dispatch IFF the orchestrator persona names the exception, at BOTH
  persona sites (the Agent-Teams checkpoint and the dispatch-mandatory
  carve-out).

ENUMERATION NOTE for the copy-paste guard: the plan anticipated pinning
the warm template's handed-path tokens (plan path, contract path). At HEAD
the warm template hands NO path literals — plan and contract paths are
runtime-filled into Task B descriptions, and no placeholder for either
exists in the file. The enumerated set below is therefore the warm
machinery's actual spawn tokens (the TGD block and the canonical spawn
literal), read off peer-review.md as written. The cold section's own
legitimate occurrences of the avoid-list paths sit inside the AVOID
clause, which is why those paths are pinned PRESENT, not absent.

WHAT THIS DOES NOT CATCH: obedience. Whether a dispatched cold reviewer
actually refrains from reading the avoid-list paths, actually reports the
required shape, or actually arrives without contaminated context is
runtime behavior no fixture observes — the first live peer-review dispatch
under this prose is the probe, per the repo dogfood rule. The ceiling on
these pins is prose reworded into uselessness while every token survives.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"
PERSONA = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"

SECTION_HEADING = "### Cold Reviewer Lane"
# The persona names the exception at exactly two sites — the Agent-Teams
# checkpoint and the dispatch-mandatory carve-out — whose lead-ins were
# reworded apart, so the pin keys on the phrase BOTH sites share. The
# count arm below holds the pair to two.
PERSONA_EXCEPTION = "the peer-review cold reviewer"
# The NEVER sentence the carve-out relaxes, located verbatim in the persona.
PERSONA_NEVER_ANCHOR = "for specialist agents"


def _peer_review_text() -> str:
    return PEER_REVIEW.read_text(encoding="utf-8")


def _cold_section() -> str:
    """The Cold Reviewer Lane section alone: heading to the next rule.

    Section-scoped so a token in the warm dispatch machinery or any other
    part of the file cannot satisfy an arm below.
    """
    text = _peer_review_text()
    start = text.find(SECTION_HEADING)
    assert start != -1, f"peer-review.md lost the {SECTION_HEADING!r} section"
    end = text.find("\n---", start)
    assert end != -1, "the cold lane section no longer ends at a rule"
    section = text[start:end]
    assert len(section) > 400, (
        "the cold lane section is too short to carry the seven-clause "
        "contract — an edit truncated it"
    )
    return section


def _spawn_reference() -> str:
    """The single inline Agent(...) call reference inside the cold section.

    Exactly one is required: the spawn shape is the lane's defining
    constraint, so a second Agent( form in the section is drift even if the
    first is correct.
    """
    refs = re.findall(r"Agent\(([^)]*)\)", _cold_section())
    assert len(refs) == 1, (
        f"expected exactly 1 inline Agent(...) reference in the cold lane "
        f"section, found {len(refs)}: {refs!r}"
    )
    return refs[0]


def _bullet(bullet_label: str, next_label: str) -> str:
    """One contract bullet of the cold section, label to next label."""
    section = _cold_section()
    start = section.find(bullet_label)
    assert start != -1, f"the cold lane section lost its {bullet_label!r} bullet"
    end = section.find(next_label, start)
    assert end != -1, f"the {bullet_label!r} bullet no longer precedes {next_label!r}"
    return section[start:end]


def _standard_combination_block() -> str:
    """The always-included reviewer list, before the conditional list."""
    text = _peer_review_text()
    start = text.find("Standard reviewer combination (always included):")
    assert start != -1, "peer-review.md lost the standard reviewer combination list"
    end = text.find("Conditional reviewers", start)
    assert end != -1, "the standard list no longer precedes the conditional list"
    return text[start:end]


def _clause_satisfaction_paragraph() -> str:
    """The cold-lane paragraph appended after REVIEWER-MEMORY-CHANNEL."""
    text = _peer_review_text()
    start = text.find("The cold reviewer lane satisfies")
    assert start != -1, (
        "the REVIEWER-MEMORY-CHANNEL clause lost its cold-lane "
        "satisfaction paragraph"
    )
    end = text.find("\n\n", start)
    return text[start:end if end != -1 else None]


def _strip_ticks(s: str) -> str:
    """Drop backticks so a vocabulary pin matches ticked and unticked forms."""
    return s.replace("`", "")


class TestSectionAnchor:
    def test_section_heading_present(self):
        assert SECTION_HEADING in _peer_review_text(), (
            "peer-review.md lost the Cold Reviewer Lane section heading"
        )


class TestAvoidList:
    """The information diet, named by PATH (and store), not by concept.

    Per the issue: 'do not read the author's plan' is easy to rationalise
    past when the path is handed over, so the paths themselves must be in
    the dispatch contract.
    """

    @pytest.mark.parametrize("token, lost", [
        ("docs/plans/", "the plan store"),
        ("docs/architecture/", "the architecture contract store"),
        ("docs/review/", "the review-doc store"),
        ("CLAUDE.md", "the managed-block file — the whole file is off-list"),
        ("team task directory", ("the task store, where dispatches and "
         "HANDOFFs carry the narrative")),
        ("handoff files", "upstream narrative in file form"),
        ("pact-memory", "the shared per-project memory store"),
    ])
    def test_avoid_token_present(self, token, lost):
        assert token in _cold_section(), (
            f"the cold lane's AVOID contract no longer names {lost} "
            f"({token!r}) by its literal path/store"
        )


class TestDisregardClause:
    """The unconditional disregard clause must name BOTH arrival surfaces.

    Naming only CLAUDE.md leaves the harness-placed auto-memory index as an
    open channel; naming only the index leaves the managed block. The
    clause is the belt-and-braces half of the diet: the avoid-list governs
    what the reviewer reads, the disregard clause governs what arrives
    anyway.
    """

    def test_both_surfaces_named_in_the_disregard_clause(self):
        clause = _bullet("**DISREGARD**", "**OPERATIONAL FACTS**")
        assert "CLAUDE.md" in clause, (
            "the DISREGARD clause no longer names the CLAUDE.md sections"
        )
        assert "auto-memory index" in clause, (
            "the DISREGARD clause no longer names the auto-memory index — "
            "the second arrival surface, without which naming only "
            "CLAUDE.md reads as complete"
        )

    def test_disregard_is_operative_not_informational(self):
        clause = _bullet("**DISREGARD**", "**OPERATIONAL FACTS**")
        assert "inadmissible" in clause, (
            "the DISREGARD clause lost its operative verb — context may "
            "arrive, but 'do not rely on it, cite it, or let it choose' "
            "must stay an instruction, not a disclosure"
        )


class TestReportContract:
    """The REPORT shape that makes a null result legible."""

    @pytest.mark.parametrize("token, lost", [
        ("coverage boundary", ("the required boundary — a partial review "
         "must be visible as partial")),
        ("nothing-found", ("the per-severity explicit no-findings lines — "
         "absent findings and an absent review look identical without them")),
        ("block a merge", ("the global merge judgement, required even when "
         "nothing would block")),
        ("derived from the diff alone", ("the report's opening claim must "
         "come from the diff, never the PR description")),
        ("replica", ("the trial's lesson — tests exercising a replica of "
         "production logic are exactly where warm lanes agree with the "
         "story")),
    ])
    def test_report_token_present(self, token, lost):
        assert token in _cold_section(), (
            f"the cold lane's REPORT contract lost {lost} ({token!r})"
        )

    @pytest.mark.parametrize("token", ["blocking", "suggestion", "nitpick"])
    def test_severity_enum_token_present(self, token):
        assert token in _strip_ticks(_cold_section()), (
            f"the cold lane's severity vocabulary lost {token!r} — the "
            f"review_finding journal event consumes this enum"
        )


class TestArtifactClause:
    """The PR surface is barred: a subagent with no team and no tasks can
    still reach the author's account of the work through `gh pr view` and
    `gh api` — the last open narrative channel — so the bar must be in the
    contract, not left to the reviewer's discretion."""

    @pytest.mark.parametrize("token, lost", [
        ("git diff", "the instruction to run the diff itself"),
        ("gh pr view", "the bar on reading the PR body"),
        ("gh api", "the bar on reading the PR comments"),
    ])
    def test_artifact_token_present(self, token, lost):
        clause = _bullet("**ARTIFACT**", "**AVOID by path**")
        assert token in clause, (
            f"the cold lane's ARTIFACT contract lost {lost} ({token!r})"
        )


class TestChannelsClause:
    """The channel prohibitions. The subagent shape closes the team inbox
    by construction, but task tools and messaging remain loadable for a
    plain subagent — the belt that bars them is load-bearing precisely
    because the construction does not reach them."""

    @pytest.mark.parametrize("token, lost", [
        ("SendMessage", ("the bar on messaging reviewers, secretary, or "
         "lead about the work")),
        ("TaskList", ("the bar on loading task tools — the task store "
         "carries the narrative")),
        ("TaskGet", "the bar on reading task descriptions — ditto"),
    ])
    def test_channels_token_present(self, token, lost):
        clause = _bullet("**CHANNELS**", "**DISREGARD**")
        assert token in clause, (
            f"the cold lane's CHANNELS contract lost {lost} ({token!r})"
        )


class TestQuestionClause:
    """The operative question, verbatim. 'Does this diff implement the
    plan' is the warm lanes' question; the cold lane's value depends on
    asking the other one."""

    def test_question_phrase_present(self):
        clause = _bullet("**QUESTION**", "**REPORT**")
        assert '"what is wrong with this diff"' in clause, (
            "the cold lane's QUESTION no longer asks 'what is wrong with "
            "this diff' — a drift toward the plan-conformance question "
            "would erase the frame break the lane exists to buy"
        )


class TestOperationalFactsClause:
    """The trial's own failure mode: a cold reviewer that cannot run the
    project's tests or lint because the access instructions lived in the
    documents it was told to avoid. Independence is about the narrative,
    not about being under-equipped."""

    @pytest.mark.parametrize("token, lost", [
        ("tests", "how to run the project's tests"),
        ("lint", "the lint gate command"),
        ("docs/", "the path-convention facts (untracked docs/ is normal)"),
    ])
    def test_operational_facts_token_present(self, token, lost):
        clause = _bullet("**OPERATIONAL FACTS**", "**QUESTION**")
        assert token in clause, (
            f"the cold lane's OPERATIONAL FACTS contract lost {lost} "
            f"({token!r}) — an under-equipped lane produces "
            f"static-tracing evidence only"
        )


class TestWiringExceptions:
    """The lane's structural exceptions: what the cold dispatch SKIPS, how
    a null report is read, and where its findings land. The skip sentence
    is the guard against the surrounding warm-dispatch procedure absorbing
    the cold lane back into the Task A + Task B pattern."""

    def test_skip_sentence_present(self):
        section = _cold_section()
        assert "Task A + Task B" in section and "skips" in section, (
            "the cold lane's skip sentence is gone — without it the "
            "surrounding per-reviewer dispatch procedure invites wiring "
            "the cold lane into the Task A + Task B pattern, the "
            "conversion the section exists to prevent"
        )

    def test_null_rule_present(self):
        section = _cold_section()
        assert "review-did-not-happen" in section, (
            "the null rule is gone: without 'review-did-not-happen, not "
            "clean', a silent or empty cold report reads as a clean "
            "review at synthesis"
        )
        assert "valid only with" in section, (
            "the null rule lost its operative claim — a no-findings "
            "report is VALID ONLY WITH the nothing-found lines, the "
            "merge-block sentence, and the coverage boundary; without "
            "'valid only with' the receipt list reads as advisory"
        )

    def test_report_landing_present(self):
        section = _cold_section()
        for token in ("review_dispatch", "review_finding", "cold-reviewer"):
            assert token in section, (
                f"the report-landing line lost {token!r} — a subagent's "
                f"report lives only in the lead's conversation; without "
                f"the journal landing it vanishes at compaction"
            )


class TestDivisionOfLabour:
    """The deliberate split: warm lanes own project-policy conformance,
    the cold lane judges the code against itself. It works only if stated,
    so the statement is pinned."""

    @pytest.mark.parametrize("token, lost", [
        ("policy files", "the warm lanes' ownership of project policy"),
        ("against itself", ("the cold lane's frame — the code's own "
         "claims, not the project's rules")),
    ])
    def test_division_token_present(self, token, lost):
        assert token in _cold_section(), (
            f"the division-of-labour sentence lost {lost} ({token!r})"
        )


class TestSpawnShape:
    """JOINT-HIGHEST: the spawn reference carries the anonymous-subagent
    shape. A cold dispatch written by copying the warm template inherits
    name=/team_name=/the role prelude — a teammate spawn that joins the
    team, loads its stores, and defeats the lane by construction while
    every census stays green (the no-prelude shape keeps it outside the
    spawn-literal census by design)."""

    def test_subagent_type_is_general_purpose(self):
        ref = _spawn_reference()
        assert 'subagent_type="general-purpose"' in ref, (
            f"the cold spawn reference is not a general-purpose subagent: "
            f"{ref!r}"
        )

    def test_spawn_carries_description_and_prompt(self):
        ref = _spawn_reference()
        assert "description=" in ref and "prompt=" in ref, (
            f"the cold spawn reference lost its description/prompt params: "
            f"{ref!r}"
        )

    def test_no_name_param(self):
        assert "name=" not in _spawn_reference(), (
            "the cold spawn reference carries name= — an anonymous "
            "subagent has none; naming it makes it addressable as a teammate"
        )

    def test_no_team_name_param(self):
        assert "team_name" not in _spawn_reference(), (
            "the cold spawn reference carries team_name= — that single "
            "param is what joins an agent to the team's task store and "
            "inbox"
        )

    def test_no_role_prelude(self):
        assert "YOUR PACT ROLE" not in _spawn_reference(), (
            "the cold spawn reference carries the teammate role prelude — "
            "the marker that routes a spawn into the teammate protocol"
        )


class TestCopyPasteGuard:
    """None of the warm dispatch machinery's tokens occur in the cold
    section. The warm tokens are enumerated from peer-review.md at HEAD —
    see the module docstring's ENUMERATION NOTE."""

    @pytest.mark.parametrize("token, origin", [
        ("pact-{reviewer-type}", "the warm subagent_type placeholder"),
        ("{reviewer-name}", "the warm name/prompt placeholder"),
        ('team_name="{team_name}"', "the warm Agent call's team binding"),
        ("YOUR PACT ROLE: teammate", "the warm spawn literal's role prelude"),
        ("PACT:pact-team-registration", ("the register first-action the "
         "warm literal mandates")),
        ("TaskCreate", ("the TGD block's task machinery — the cold lane "
         "skips the Task A + Task B pair by design")),
        ("--type variety_assessed", ("the per-reviewer journal write the "
         "cold lane skips")),
        ("{lead_address}", "the warm prompt's lead-routing line"),
    ])
    def test_warm_token_absent_from_cold_section(self, token, origin):
        assert token not in _cold_section(), (
            f"the cold lane section contains {token!r} ({origin}) — the "
            f"copy-paste signature of a warm dispatch, which hands the "
            f"lane exactly the narrative it exists to avoid"
        )


class TestLanePlacement:
    """The lane's three satellite surfaces: always-bullet, tree label, and
    the clause-satisfaction paragraph."""

    def test_cold_reviewer_in_always_included_list(self):
        block = _standard_combination_block()
        assert "Cold reviewer" in block, (
            "the cold reviewer is not in the always-included reviewer "
            "combination — the design decision is ALWAYS, not conditional"
        )

    def test_example_tree_labels_it_subagent(self):
        assert '[Subagent] "cold-reviewer' in _peer_review_text(), (
            "the example tree no longer labels the cold reviewer "
            "[Subagent] — the label is the one-character-class signal "
            "that this lane is not a teammate like the [Agent] rows"
        )

    @pytest.mark.parametrize("token, lost", [
        ("general-purpose", "the type carrying no PACT agent-memory store"),
        ("by construction", ("the claim that the clause is satisfied "
         "structurally, not by discipline")),
        ("no PACT agent-memory store", ("the mechanism — without it the "
         "satisfaction claim is an assertion, not a reason")),
    ])
    def test_clause_satisfaction_paragraph(self, token, lost):
        para = _clause_satisfaction_paragraph()
        assert token in para, (
            f"the clause-satisfaction paragraph lost {lost} ({token!r})"
        )


class TestCrossSurfaceCoherence:
    """peer-review.md carries the subagent cold dispatch IFF the persona's
    dispatch-mandatory section names the exception. The persona's blanket
    sentences (specialists are MANDATORILY teammates; NEVER a plain
    subagent spawn) contradict the cold lane unless the carve-out sits
    beside them — and nothing else reddens on the contradiction, because
    no existing test pins those persona sentences."""

    def test_cold_dispatch_iff_persona_names_the_exception(self):
        has_cold = SECTION_HEADING in _peer_review_text()
        persona = PERSONA.read_text(encoding="utf-8")
        persona_names = PERSONA_EXCEPTION in persona
        assert has_cold == persona_names, (
            "peer-review.md and the orchestrator persona disagree on the "
            "cold lane: one carries it and the other does not name the "
            "exception. A lead following the persona's blanket "
            "specialists-are-teammates rule would refuse or 'correct' the "
            "cold dispatch."
        )

    def test_carve_out_sits_beside_the_never_sentence(self):
        persona = PERSONA.read_text(encoding="utf-8")
        never = persona.find(PERSONA_NEVER_ANCHOR)
        assert never != -1, (
            "the persona lost its NEVER-plain-subagent sentence for "
            "specialist agents"
        )
        carve = persona.find(PERSONA_EXCEPTION, never)
        assert carve != -1 and carve - never < 400, (
            "the cold-reviewer exception no longer sits beside the NEVER "
            "sentence it relaxes — a carve-out elsewhere in the persona "
            "leaves the blanket rule reading as unqualified at the point "
            "a lead reads it"
        )

    def test_exception_named_at_both_persona_sites(self):
        """TWO sites, not one: the dispatch-mandatory carve-out (read when
        dispatching) and the Agent-Teams checkpoint (read when a lead
        catches an Agent call without team_name — exactly the cold spawn
        shape). Either one alone leaves the other surface reading as an
        unqualified rule, and a lead obeying it 'corrects' the cold spawn
        back into a teammate."""
        persona = PERSONA.read_text(encoding="utf-8")
        count = persona.count(PERSONA_EXCEPTION)
        assert count >= 2, (
            f"the persona names {PERSONA_EXCEPTION!r} at {count} site(s); "
            f"expected both the checkpoint and the dispatch-mandatory "
            f"carve-out. A site lost its exception and now reads as an "
            f"unqualified rule at the point a lead checks it."
        )

    def test_persona_reviewer_list_names_the_lane(self):
        persona = PERSONA.read_text(encoding="utf-8")
        assert "Cold reviewer" in persona, (
            "the persona's reviewer list no longer names the cold "
            "reviewer lane — the dispatch enumeration and the command "
            "must agree on the lane's existence"
        )
