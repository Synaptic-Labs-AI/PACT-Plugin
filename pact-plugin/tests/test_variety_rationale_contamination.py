"""
Location: pact-plugin/tests/test_variety_rationale_contamination.py

Variety rationales are read by a teammate BEFORE it starts work, so a rationale
carrying the dispatch's hypothesis hands a seat the thing it may exist not to
know. These arms pin the constraint at both places it has to hold: the
placeholder a lead fills in, and the schema block that defines it.

POPULATION, not a fixed list: the placeholder arm walks every `commands/*.md`
and both protocol files, so a template added later is covered on the day it
arrives rather than when someone remembers this file.
"""

import re
import subprocess
from pathlib import Path

from fixtures.tracked_files import tracked_glob as _tracked_glob

PLUGIN = Path(__file__).resolve().parent.parent
VARIETY = PLUGIN / "protocols" / "pact-variety.md"
PROTOCOLS = PLUGIN / "protocols" / "pact-protocols.md"

# A placeholder a lead fills in. The suffix is the constraint; a bare one is the defect.
_PLACEHOLDER = re.compile(r"why this score for THIS dispatch's (?:novelty|scope|uncertainty|risk)")

_RULE = "Do not write the dispatch's hypothesis, its expected answer, or the reasoning that produced it."


def _command_files(root=PLUGIN):
    """Tracked command templates; a working-tree walk reads scratch files."""
    return sorted(_tracked_glob(root, "commands/*.md", allow_empty=True))


def _surfaces():
    return _command_files() + [VARIETY, PROTOCOLS]


def test_no_bare_rationale_placeholder_survives():
    """Every writer-side placeholder carries the constraint inline.

    "why this score" solicits causal reasoning, and the honest causal answer is
    often the belief itself — so the prompt must not ask it at all.
    """
    bare = {
        p.relative_to(PLUGIN).as_posix(): len(_PLACEHOLDER.findall(p.read_text(encoding="utf-8")))
        for p in _surfaces()
    }
    offenders = {k: v for k, v in bare.items() if v}
    assert not offenders, f"causal-prompt rationale placeholders: {offenders}"


def test_constraint_reaches_the_writer():
    """Non-vacuity: the population is non-empty and does carry placeholders.

    Without this, the arm above passes on a glob that matches nothing.

    THE FLOOR IS ARBITRARY HEADROOM, NOT A DERIVED COUNT. Its only job is to
    prove the glob is live. It is deliberately far below the real population
    so that legitimately removing a template or consolidating a dispatch block
    does not redden it — this file pins that no placeholder solicits causal
    reasoning, not how many placeholders exist. Do not tighten it to track the
    census: a count that carries no meaning teaches the next reader that it
    does.
    """
    suffixed = sum(
        t.count("never what you expect to find")
        for t in (p.read_text(encoding="utf-8") for p in _surfaces())
    )
    assert suffixed >= 20, f"expected the placeholder population, found {suffixed}"


def test_schema_block_states_the_rule_in_both_mirror_halves():
    """The clause a placeholder suffix cannot carry, in the SSOT and its mirror."""
    for path in (VARIETY, PROTOCOLS):
        text = path.read_text(encoding="utf-8")
        assert _RULE in text, f"{path.name}: rule absent"
        assert "must not disclose it" in text, f"{path.name}: not-knowing clause absent"

TEACHBACK_SKILL = PLUGIN / "skills" / "pact-teachback" / "SKILL.md"

# The three surfaces that state when a teammate may read the rationales.
_ORDERING_SITES = (VARIETY, PROTOCOLS, TEACHBACK_SKILL)

# The lead-side re-stamp decision point. THREE surfaces, TWO edit-units: the
# persona carries its own wording, VARIETY and PROTOCOLS are the byte-mirrored
# pair. A clause landing on the persona alone leaves the protocol pair still
# instructing the re-stamp, which is how the seat-side fix was reached with the
# lead side left open.
ORCHESTRATOR = PLUGIN / "agents" / "pact-orchestrator.md"
WRAP_UP = PLUGIN / "commands" / "wrap-up.md"
_RESTAMP_SITES = (ORCHESTRATOR, VARIETY, PROTOCOLS)


def test_all_three_surfaces_state_the_same_read_ordering():
    """The split read is stated at three sites; two of three is worse than none.

    WHAT THIS CANNOT DO, and it is the point of the arm rather than a caveat:
    it cannot check that a teammate OBEYS the ordering. That happens at runtime
    in another agent. A phrase pin passes on a violated ordering. What it does
    check is the failure this change could actually introduce — one surface
    updated and another left stating the old rule.
    """
    for path in _ORDERING_SITES:
        text = path.read_text(encoding="utf-8")
        assert "ONLY AFTER" in text, f"{path.name}: read-ordering clause absent"
        assert "cannot be un-read" in text, f"{path.name}: required-not-preferred force absent"

def test_ignorance_dependent_dispatches_stamp_the_withheld_sentinel():
    """The writer-side fix: the protocol pair, and the COMMAND writer templates.

    SCOPE, stated because the assertion is narrower than "every writer
    surface" reads: the derived set globs `commands/*.md` only. Two further
    stamping sites live outside it — protocols/pact-variety.md and
    protocols/pact-protocols.md both carry `"novelty_rationale":` — and this
    arm checks PRESENCE there, never count parity. Measured stamps=1/shows=2
    on each (a rule mention plus the exception clause), so count-equality is
    genuinely inapplicable to them rather than merely omitted.

    RETARGETED, not extended: the previous form pinned "do not ask that seat
    for variety_acknowledgment", which the hook denies — that field is in
    TEACHBACK_REQUIRED_FIELDS and its absence raises. The sentinel satisfies
    the hook (four keys, four non-empty strings, content unconstrained) and
    discloses nothing, because nothing was written.
    """
    sentinel = "WITHHELD: ignorance-dependent dispatch"
    for path in (VARIETY, PROTOCOLS):
        text = path.read_text(encoding="utf-8")
        assert sentinel in text, f"{path.name}: sentinel rule absent"
        assert "do not ask that seat" not in text, (
            f"{path.name}: superseded carve-out still present — it and the "
            f"sentinel rule contradict each other"
        )
    # DERIVED population, not a count. A file stamps variety iff it carries the
    # JSON key form; prose QUOTING a rationale key (wrap-up's retrospective
    # sample output) is not a stamp and must not join the set. Comparing
    # name->count dicts checks two properties at once: that the sentinel reaches
    # exactly the stamping files, and that a file stamping N times carries it N
    # times — presence alone passes a file that stamps 3x and shows it once.
    stamp_key = '"novelty_rationale":'
    stamps: dict[str, int] = {}
    shows: dict[str, int] = {}
    for writer in _command_files():
        body = writer.read_text(encoding="utf-8")
        if stamp_key in body:
            stamps[writer.name] = body.count(stamp_key)
        if sentinel in body:
            shows[writer.name] = body.count(sentinel)
    # DECLARED EXCEPTION, one file: wrap-up.md carries the literal as a MATCH
    # TARGET (question 6 excludes withholding acks by testing for it), not as a
    # stamp. Named rather than filtered by shape, so any OTHER non-stamping file
    # carrying the sentinel still reddens. Its presence is pinned by
    # test_withholding_acknowledgments_are_excluded_from_the_cargo_cult_rate.
    shows.pop("wrap-up.md", None)
    assert shows == stamps, (
        f"sentinel sites {shows} do not match stamping sites {stamps} — "
        f"missing: {set(stamps) - set(shows)}, "
        f"outside the stamping set: {set(shows) - set(stamps)}"
    )

    # The seat must be able to tell DELIBERATE from BROKEN. Without this the
    # honest `concern` is "these are empty, please re-stamp" — and that
    # round-trip ends in real prose, reopening the leak the sentinel closes.
    seat = TEACHBACK_SKILL.read_text(encoding="utf-8")
    assert sentinel in seat, "teachback skill: seat cannot recognise the sentinel"
    assert "do NOT ask for a re-stamp" in seat, "teachback skill: re-stamp trap open"


def test_withheld_stamp_exempts_the_lead_from_re_stamping():
    """The lead side of the loop the seat-side fix left open.

    On `"no"`/`"concern"` every one of these surfaces tells the lead to prefer
    ORCHESTRATOR-SIDE CORRECTION — re-stamp with refined per-dimension
    rationales. On a dispatch stamped WITHHELD that instruction writes the
    hypothesis the withholding exists to keep out, and the seat files `concern`
    on such a dispatch BY CONSTRUCTION, so the input is not hypothetical.

    WHAT THIS CHECKS, stated narrowly because an earlier form of this arm was
    GREEN OVER THIS DEFECT LIVE IN THE TREE: four literals present at each of
    the three surfaces, plus an ORDERING fact — where a surface states the
    preference and the exception in one prose line, the exception must come
    LAST. Presence alone could not see the defect, because the defect ADDED an
    unqualified preference sentence after the clause rather than removing
    anything, and presence sees absence, not contradiction. And on the two
    NESTED surfaces, that the exception is the FIRST corrective-options bullet,
    so an option is never offered before it is forbidden — reordering the list
    to present the options first reddens. Only the bullet COUNT was dropped;
    the ORDER is still pinned.

    WHAT IT STILL CANNOT DO. It cannot check that a reader OBEYS. Beyond that,
    THREE KNOWN shapes pass — known, not all; the class-level paragraph
    below is what classifies a fourth nobody has built yet:
    1. THE SYNONYM. A re-instruction that says re-stamp WITHOUT spending the
       word — "refresh the per-dimension rationales", say.
    2. THE REGION EXIT. A TOP-LEVEL bullet placed past the blockquote is
       outside the censused range and passes carrying the word. Measured with a
       positional control: identical text at the same line, indented to stay
       inside the region, reddens.
    3. THE COMPENSATING PAIR, below.
    CARRIERS INSIDE THE REGION ARE COVERED — appended prose, the next line, a
    deeper bullet, an indented blockquote (this file's own house style, four
    lines below in the protocols) — each reddening WITH the word and passing
    WITHOUT it. That is what the census bought, and it is narrower than "every
    positional variant", which is what this docstring said until it was
    measured. WHICH FAILURE EACH LEG OWNS, because the census is NOT strictly better than
    what it replaced and treating it that way is how the next hole gets made.
    An ordering pin is blind to a SUBSTITUTION and catches a MOVE; a census is
    the reverse. CONSTRUCTED, not observed in the wild: the COMPENSATING PAIR —
    add a re-instruction that spends the word, delete one legitimate mention in
    the same region — keeps the count at 3 and passes GREEN. No widening of the
    pattern can reach it, because nothing was evaded; the arithmetic was paid.
    Its plausibility is raised, not established, by the close-bullet case: ONE
    innocent edit perturbs this count by a mechanism its author did not
    consider, so the count is coupled to layout in ways an ordinary editor
    cannot see. The redesign traded one
    blind spot for another, deliberately, because a move is cheap to make by
    accident and a compensating pair is not.

    TWO STRUCTURAL DECLARATIONS, neither chased:
    - THE REGION IS DERIVED FROM THE CONTENT IT POLICES. `end` is the next line
      starting "- " or "#", so a top-level bullet placed far enough after the
      exception is outside the region BY CONSTRUCTION and evades. Placed close,
      it instead truncates the range and lowers the count — which is why the
      failure message prints the line range and says a LOW count usually means
      the region shrank rather than a mention being deleted.
    - THE 3 IS THREE UNRELATED SENTENCES, one of them the `> ⚠️` re-stamp
      mechanics warning that has nothing to do with the exception. So an
      ordinary edit to that warning reddens this arm. The message says to repair
      the text and NOT to bump the number, because re-pinning the count to
      whatever the tree now says is exactly how a live re-instruction is adopted.
      Tighten-back trigger, the same contract MAX_SKILL_CHARS carries in
      test_agents_structure.py: if a re-instruction is REMOVED from this region,
      LOWER this number. Do NOT raise it. A constant that only ever goes up
      stops being a ceiling, which is the argument this branch already made
      once tonight about a character budget and then paid for by trimming.

    NOTHING GUARDS THE REGION'S EXTENT. A top-level bullet contracted the range
    from [217,228) to [217,224) — seven lines of the corrective-options block
    fell outside the censused text and the arm stayed GREEN, because the
    truncation dropped no counted occurrence. Coverage shrank and no number the
    assertion reads moved.

    Also uncaught, deliberately: a fourth bullet that adds no re-stamp
    wording — its COUNT only, since the exception's position in that list
    is still pinned above.
    That is the price of dropping a bullet-count leg which was brittle to a
    legitimate fourth bullet AND porous to an indented one — paying for
    brittleness without buying coverage. The ordering leg keys on two literal
    preference markers; an ordinary rewording of either makes the loop find nothing, which is why the
    vacuity guard below fails LOUD instead of passing on an empty check. The
    nested-list surfaces get their subordination from layout, so the ordering
    leg is expected to bind on the flat prose surface only.
    """
    for path in _RESTAMP_SITES:
        text = path.read_text(encoding="utf-8")
        assert "EXCEPTION — a withheld stamp" in text, (
            f"{path.name}: lead-side re-stamp exception absent"
        )
        assert "take NEITHER option" in text, (
            f"{path.name}: exception does not close BOTH corrective options"
        )
        assert "writes the hypothesis the withholding exists to keep out" in text, (
            f"{path.name}: deviation reason absent — a bare exception gets "
            f"reasoned back to re-stamping"
        )
        assert "overrides the preference for orchestrator-side" in text, (
            f"{path.name}: exception does not name what it overrides, so the "
            f"preference sentence still reads as governing"
        )

    # ORDER, not merely presence. All three phrases can be present while a LATER
    # unqualified preference sentence re-instructs the re-stamp — on a withheld
    # stamp the teammate's flag is correct by construction, so "prefer
    # orchestrator-side when the flag is correct" routes straight back to it.
    # Where a surface states preference and exception in the SAME prose line,
    # nothing subordinates one to the other except their order; the nested-list
    # surfaces get that subordination from layout instead.
    preference_markers = (
        "Prefer orchestrator-side",
        "preferred when teammate's flag is correct",
    )
    ordered_sites = 0
    for path in _RESTAMP_SITES:
        for line in path.read_text(encoding="utf-8").split("\n"):
            if "EXCEPTION — a withheld stamp" not in line:
                continue
            for marker in preference_markers:
                if marker in line:
                    ordered_sites += 1
                    assert line.index("EXCEPTION — a withheld stamp") > line.index(marker), (
                        f"{path.name}: the preference sentence reads AFTER the "
                        f"exception, so the last instruction on the line is the "
                        f"re-stamp the exception forbids"
                    )
                    # And nothing may follow it. Ordering alone is not enough:
                    # the defect this arm exists for was ADDITIVE — every pinned
                    # literal intact, a re-instruction appended beside them — so
                    # a later author restoring the forbidden meaning in words
                    # that collide with no pinned literal would still pass an
                    # index compare. Requiring the clause to END the line means
                    # any appended instruction collides with THIS assertion
                    # whatever words it uses.
                    assert line.rstrip().endswith(
                        "writes the hypothesis the withholding exists to keep "
                        "out of the task."
                    ), (
                        f"{path.name}: text follows the exception on this line, "
                        f"so something is instructed after the clause that is "
                        f"supposed to be the lead's last word"
                    )
    # The nested surfaces take their subordination from LAYOUT, so the flat-prose
    # rule above does not bind there and an additive re-instruction arrives as a
    # FOURTH sibling bullet instead of appended prose. Pin the one layout fact
    # the census below CANNOT see: the exception comes BEFORE the options.
    # Moving text changes no token count, so a demotion is invisible to a census
    # and visible only here. `subs` lists TWO-SPACE sibling bullets only —
    # deeper bullets and blockquotes inside the range are not in it, and are
    # covered by the census rather than by this leg.
    nested_sites = 0
    for path in _RESTAMP_SITES:
        lines = path.read_text(encoding="utf-8").split("\n")
        marked = [
            n for n, line in enumerate(lines)
            if line.startswith("  - ") and "EXCEPTION — a withheld stamp" in line
        ]
        if not marked:
            continue
        nested_sites += 1
        i = marked[0]
        start = next(n for n in range(i, 0, -1) if lines[n].startswith("- "))
        end = next(
            (n for n in range(i + 1, len(lines))
             if lines[n].startswith("- ") or lines[n].startswith("#")),
            len(lines),
        )
        subs = [n for n in range(start, end) if lines[n].startswith("  - ")]
        assert subs[0] == i, (
            f"{path.name}: the exception is not the FIRST corrective-options "
            f"bullet, so an option is offered before it is forbidden"
        )
    # POSITION-INDEPENDENT BACKSTOP, and the reason the legs above are not
    # enough on their own. Each of them pins the POSITION of the attack that
    # produced it — appended after the clause, added as a sibling bullet — so a
    # later re-instruction needs no new WORDS, only a new POSITION: the next
    # line, a deeper indent. Measured: both evaded every positional leg. Census
    # the region instead. Wherever a re-instruction sits and however it is
    # indented, instructing a re-stamp spends the word, and the count moves.
    # Three per surface today: the option's own text, the exception's reason,
    # and the re-stamp mechanics warning.
    for path in _RESTAMP_SITES:
        lines = path.read_text(encoding="utf-8").split("\n")
        i = next(n for n, l in enumerate(lines) if "EXCEPTION — a withheld stamp" in l)
        start = next(n for n in range(i, 0, -1) if lines[n].startswith("- "))
        end = next(
            (n for n in range(i + 1, len(lines))
             if lines[n].startswith("- ") or lines[n].startswith("#")),
            len(lines),
        )
        # The separator class is NOT decoration: a U+2011 non-breaking hyphen
        # renders identically to ASCII "-" and arrives from ordinary Unicode
        # punctuation in an edit, not from an attacker. Space form too.
        found = re.findall(
            r"re[-\u2010-\u2015\u2212\s]?stamp\w*",
            "\n".join(lines[start:end]),
            re.IGNORECASE,
        )
        assert len(found) == 3, (
            f"{path.name}: {len(found)} re-stamp mentions in the "
            f"corrective-options region (lines {start + 1}-{end}), expected 3. "
            f"HIGHER means a re-instruction was ADDED — that is what this "
            f"census exists to catch. LOWER usually means the REGION SHRANK "
            f"rather than a mention being removed: the range ends at the next "
            f"top-level bullet, so one inserted after the exception truncates "
            f"it and drops a legitimate mention. Check the line range above "
            f"against the section, and repair the TEXT. Do NOT bump this "
            f"number — the 3 is measured, and re-pinning it to whatever the "
            f"tree now says is how a live re-instruction gets adopted: {found}"
        )

    assert nested_sites == 2, (
        f"layout check bound on {nested_sites} nested surfaces, expected 2 — "
        f"the list structure changed, so re-derive this check rather than "
        f"letting it pass by finding nothing"
    )

    assert ordered_sites, (
        "ordering check went vacuous — no surface now states the preference and "
        "the exception in one line. Re-derive it against the current layout "
        "rather than letting it pass by finding nothing to check."
    )


def test_withholding_acknowledgments_are_excluded_from_the_cargo_cult_rate():
    """The counting site, which no clause aimed at a READER would reach.

    Question 6 computes (count "no" + count "concern") / total_teachbacks and
    surfaces on rate >= 0.20 OR any single "no". A withheld dispatch makes the
    seat file `concern` BY CONSTRUCTION, so ONE such dispatch in an arc of four
    trips a threshold that means "one in five teammates flagged your scoring",
    and the retrospective then writes a cargo_cult_signal entity into memory.

    BOTH TERMS: excluding from the numerator alone drags the rate toward "no
    concern", the direction that hides the signal the question exists to
    surface. The two halves of the join are pinned together on purpose — the
    aggregator matches a LITERAL, so a seat instructed only to "name" the
    withholding leaves the aggregator matching prose.
    """
    sentinel = "WITHHELD: ignorance-dependent dispatch"
    text = WRAP_UP.read_text(encoding="utf-8")
    assert sentinel in text, "wrap-up: aggregator has no literal to match on"
    # BOTH TERMS, pinned STRUCTURALLY rather than as a phrase. An earlier form
    # of this assertion pinned the words "excluded from BOTH terms" and went RED
    # on a legitimate rewording of that sentence — the exact fragility that makes
    # prose a bad pin. The property is real while the denominator derives from
    # the same list the rate counts, so filtering that list covers both at once.
    assert "total_teachbacks = len(flags)" in text, (
        "wrap-up: the denominator no longer derives from `flags`, so filtering "
        "the comprehension stops covering both terms — both-terms is structural "
        "ONLY while the rate and the denominator come off the same list"
    )
    assert "counts as a REAL SIGNAL" in text, (
        "wrap-up: failure direction unstated — an unmatched withholding ack must "
        "over-count (a false alarm someone investigates) rather than vanish"
    )
    assert "rather than recounting them here" in text, (
        "wrap-up: the payload row may recount, which restores the contaminated "
        "numbers after question 6 corrected them"
    )
    seat = TEACHBACK_SKILL.read_text(encoding="utf-8")
    assert "QUOTING that literal verbatim" in seat, (
        "teachback skill: seat is not required to emit the literal the "
        "aggregator matches on — the join is broken at the writing end"
    )


def test_withholding_exclusion_is_executable_where_it_is_stated():
    """The exclusion must sit where the field it keys on still exists.

    THE DEFECT THIS PINS WAS SHIPPED: the rate is computed from `flags`, a list
    comprehension that keeps the flag VALUE and discards `concern`; the
    exclusion keyed on `concern` was stated AFTER it. An executor following the
    instruction in order had already thrown the field away. Same class as the
    split-read instruction whose only command printed what it told you not to
    read — an instruction its own named instrument cannot perform.

    PINS EXPRESSIONS, NOT PROSE. The three assertions name a comprehension
    condition, a dotted metadata path, and a literal — identifiers a rewording
    does not touch. An arm checking the ORDER of two SENTENCES would go green
    the first time someone legitimately rephrased one, which is why the check is
    "the filter is inside the expression" rather than "clause A precedes B".
    """
    wrap_up = WRAP_UP.read_text(encoding="utf-8")
    sentinel = "WITHHELD: ignorance-dependent dispatch"

    # The comprehension that builds `flags` is the LAST point holding `concern`.
    start = wrap_up.find("flags = [")
    assert start != -1, (
        "wrap-up: the `flags = [` comprehension is gone — this arm's subject no "
        "longer exists, so re-derive it rather than letting it pass on a miss"
    )
    end = wrap_up.find("]`", start)
    assert end != -1, "wrap-up: `flags` comprehension is unterminated"
    comprehension = wrap_up[start:end]
    assert sentinel in comprehension, (
        "wrap-up: the withholding exclusion is NOT inside the `flags` "
        "comprehension. Wherever it is stated instead, `concern` has already "
        "been discarded by this expression and the exclusion cannot be performed"
    )

    # The fallback reads a different source and has no earlier step to move to,
    # so the READ itself must carry the sibling field.
    assert "variety_acknowledgment.concern" in wrap_up, (
        "wrap-up: the fallback path reads the flag without its `concern` "
        "sibling, so nothing on that path can be excluded on"
    )

    # The full spec a reader is SENT to must carry the rule, in both mirrors.
    # SECTION-SCOPED, not file-scoped: the sentinel ALREADY appears elsewhere in
    # both files (the lead-side re-stamp exception), so a file-level `in` check
    # is satisfied by that other occurrence and cannot see this section lose it.
    # Measured — a file-level form of this assertion passed while the section
    # had been stripped.
    heading = "#### Variety Acknowledgment Signal"
    for spec in (VARIETY, PROTOCOLS):
        body = spec.read_text(encoding="utf-8")
        start = body.find(heading)
        assert start != -1, (
            f"{spec.name}: the aggregation spec section is gone — re-derive this "
            f"check rather than letting it pass on a missing heading"
        )
        end = body.find("\n---", start)
        section = body[start:end if end != -1 else len(body)]
        assert sentinel in section, (
            f"{spec.name}: the aggregation spec section omits the exclusion, so "
            f"a reader sent here for the rule meets no exclusion"
        )


def test_an_untracked_command_needle_cannot_change_the_stamp_sets(tmp_path):
    """An untracked scratch command must not join the stamping population.

    Positive control: the tracked command IS in the population and is counted
    on both sides, so an empty population cannot pass this arm.
    """
    root = tmp_path / "plugin"
    (root / "commands").mkdir(parents=True)
    tracked = root / "commands" / "tracked.md"
    body = '"novelty_rationale":\nWITHHELD: ignorance-dependent dispatch\n'
    tracked.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)
    needle = root / "commands" / "zzprobe.md"
    needle.write_text("WITHHELD: ignorance-dependent dispatch\n", encoding="utf-8")

    population = _command_files(root)
    sentinel = "WITHHELD: ignorance-dependent dispatch"
    stamp_key = '"novelty_rationale":'
    stamps = {path.name: path.read_text(encoding="utf-8").count(stamp_key)
              for path in population
              if stamp_key in path.read_text(encoding="utf-8")}
    shows = {path.name: path.read_text(encoding="utf-8").count(sentinel)
             for path in population
             if sentinel in path.read_text(encoding="utf-8")}

    assert tracked in population, "the tracked control is missing from the population"
    assert needle not in population, (
        f"untracked needle entered the stamping population: {needle}")
    assert shows == stamps == {"tracked.md": 1}, (shows, stamps)
