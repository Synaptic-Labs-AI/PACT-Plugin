"""
Tests for the pin-comment DOMINANCE invariant in hooks/pin_caps.py.

Location: pact-plugin/tests/test_pin_comment_dominance.py

Summary: pins the safety property that ties pin-comment attribution to the
size charge, both read through the functions that ship. Attribution
(`pin_caps._date_comment_row`, with the row patterns `_DATE_COMMENT_ROW` and
`_OVERRIDE_COMMENT_ROW`) decides which row is a pin's comment. The charge
(`pin_caps._charged_rows`) strikes pin comments from each body row. A pin's
comment row sits at the end of the previous pin's body, so if the strike is
NARROWER than attribution, the comment that annotates pin N is charged to pin
N-1. The neighbour then takes a size deny that the curator did not cause.

DOMINANCE, stated over a single row `L`:

    attribution reads L as the comment of the pin below it
    =>  the charge keeps nothing of L

The strip may remove MORE than attribution accepts. It must never remove
less, because less is a charge against the neighbour.

Used with: hooks/pin_caps.py (attribution, the charge, and the four
fragments their patterns are built from) and hooks/pin_caps_gate.py (one
lead-frame integration witness). Sibling coverage of the same module lives
in test_pin_caps.py; this file owns the cross-oracle property only.

Test organization uses scope-suffix class naming per the convention in
test_pin_caps.py — duplicate test class basenames across files silently drop
the losing file's tests.
"""

import itertools

import pytest

from helpers import make_claude_md_with_pins, point_resolver_at  # noqa: E402

# ---------------------------------------------------------------------------
# The corpus grammar.
#
# THESE AXES ARE LITERAL ON PURPOSE. Do NOT rebuild them from the fragments in
# pin_caps.py, and do not import `_COMMENT_CHAR` or its siblings here.
#
# A corpus derived from the module under test asserts that module against
# itself. It goes red on the old code and green on the new code, so it LOOKS
# certified, and it protects nothing: a later editor who moves a fragment
# moves the corpus with it, and the property stays green while the behaviour
# breaks. Independence from the implementation is the whole value of this
# file, because a shared-source rule is structural and this property is not.
#
# The axes are curator-shaped, not exhaustive. Each one carries at least one
# member that the old body class refused: a `>` in prose, a `>=` comparison, a
# quoted blockquote marker, an arrow, and a date field that closes the comment
# early.
# ---------------------------------------------------------------------------

CORPUS_OPENERS = [
    "<!-- pinned: ",
    "<!--pinned:",
    "<!--  PINNED:  ",
    "<!-- Pinned: ",
    # The opener's separators are `\s*`, which accepts 28 characters, 18 of
    # them INLINE rather than line separators. All 18 are attributed by the
    # shipped pattern, and the four openers above express exactly ONE of them
    # — the plain space. A corpus built only from shapes a curator TYPES
    # cannot see a change to what the pattern TOLERATES, so narrowing `\s*`
    # to ` *` used to leave every number here identical.
    #
    # TAB is ordinary. NBSP is what a paste from a rendered document or a word
    # processor inserts, and it is INVISIBLE IN EVERY EDITOR — which is why it
    # is written here as an escape and MUST STAY one. A literal would be a
    # character nobody can see in the fixture that exists to make it visible.
    "<!--\tpinned:\t",
    "<!--\u00a0pinned:\u00a0",
]

CORPUS_DATES = [
    "2026-01-01",
    "a > b",
    "a -> b",
    "x-->y",
    "2026-01-01 ",
    "",
]

CORPUS_CLAUSES = [
    "",
    ", pin-size-override: plain",
    ", pin-size-override: a -> b",
    ", pin-size-override: use >= not >",
    ', pin-size-override: quote "> foo"',
    ", pin-size-override: keep <!-- pinned: form",
    ", pin-size-override: dash-heavy -- text",
    ", reconfirmed: 2026-03-01 because A -> B",
    ", reconfirmed: 2026-03-01 because n > 2",
]

CORPUS_CLOSERS = [
    " -->",
    "-->",
    "  -->",
]

# Non-vacuity floor on the ATTRIBUTED population.
#
# Dominance is an IMPLICATION, so every line that attribution REFUSES
# satisfies it for free. A corpus that drifts toward refused shapes therefore
# reaches zero violations for the wrong reason, and a passing test cannot tell
# the difference. This floor makes the antecedent load-bearing.
#
# Measured attributed count with the shipped patterns: 809 of 972 generated
# lines. The floor sits at 750, so the measured headroom is 59 lines. A change
# that narrows attribution past that trips this floor instead of quietly
# emptying the property.
#
# THE FLOOR MOVED WITH THE CORPUS, ON PURPOSE. Adding the TAB and NBSP openers
# took the corpus from 648 lines to 972 and the attributed count from 539 to
# 809. Leaving the floor at its old 500 would have widened the headroom from
# 39 to 309 — the guard would still have passed, and it would have stopped
# detecting anything short of a two-thirds collapse. A floor is only as good
# as its distance from the measurement, so widening the corpus without moving
# the floor SILENTLY WEAKENS it.
#
# The floor is itself a predicate, so it is proved by mutation in
# TestPinCommentDominance_FloorGuard rather than asserted.
MIN_ATTRIBUTED = 750


def build_corpus(openers, dates, clauses, closers):
    """Return every `opener + date + clause + closer` combination."""
    return [
        opener + date + clause + closer
        for opener, date, clause, closer in itertools.product(
            openers, dates, clauses, closers
        )
    ]


def attributed(line):
    """Whether shipped attribution reads `line` as the comment of the pin
    headed on the row below it."""
    from pin_caps import _date_comment_row
    from shared.claude_md_markers import parse

    return _date_comment_row(parse(f"{line}\n### P\n"), 0, 1) is not None


def charged(line):
    """What the shipped size charge keeps of `line` as a row of a pin body."""
    from pin_caps import _charged_rows
    from shared.claude_md_markers import parse

    return _charged_rows(parse(f"{line}\n"), 0, 0)[0]


def measure_corpus(corpus):
    """Return (attributed, violations) for a list of candidate lines.

    A line is ATTRIBUTED when attribution reads it as a pin comment. An
    attributed line is a VIOLATION when the charge keeps a residue of it.
    """
    count = 0
    violations = 0
    for line in corpus:
        if attributed(line):
            count += 1
            if charged(line) != "":
                violations += 1
    return count, violations


def two_pin_section(body_a, comment_b, title_a="PinA", title_b="PinB"):
    """Build a two-pin Pinned Context body.

    The comment of pin B sits inside the forward slice of pin A, which is the
    overlap the strip exists to compensate for. So `parse_pins(...)[0]
    .body_chars` equals `len(body_a)` exactly when the strip removes the
    comment of pin B in full.
    """
    entry_a = f"<!-- pinned: 2026-01-01 -->\n### {title_a}\n{body_a}"
    entry_b = f"{comment_b}\n### {title_b}\nshort"
    return entry_a + "\n\n" + entry_b + "\n"


class TestPinCommentDominance_Property:
    """The generative invariant, over a corpus declared in this file."""

    def test_dominance_holds_over_the_generated_corpus(self):
        corpus = build_corpus(
            CORPUS_OPENERS, CORPUS_DATES, CORPUS_CLAUSES, CORPUS_CLOSERS
        )
        offenders = [(line, charged(line)) for line in corpus
                     if attributed(line) and charged(line) != ""]

        assert offenders == [], (
            f"{len(offenders)} line(s) are attributed as a pin comment but are "
            f"not stripped in full. Each residue is charged to the PRECEDING "
            f"pin. First offender: {offenders[0] if offenders else None}"
        )

    def test_attributed_population_is_non_vacuous(self):
        """The implication is worthless if almost nothing satisfies it."""
        corpus = build_corpus(
            CORPUS_OPENERS, CORPUS_DATES, CORPUS_CLAUSES, CORPUS_CLOSERS
        )
        attributed, _ = measure_corpus(corpus)
        assert attributed >= MIN_ATTRIBUTED, (
            f"only {attributed} of {len(corpus)} corpus lines are attributed "
            f"as pin comments (floor {MIN_ATTRIBUTED}). Dominance is an "
            f"implication, so a shrunken antecedent makes it pass vacuously."
        )

    def test_realistic_curator_lines_are_attributed_and_stripped(self):
        """Named shapes, so the floor cannot be met by uninteresting lines."""
        lines = [
            "<!-- pinned: 2026-01-01 -->",
            "<!-- pinned: 2026-01-01, pin-size-override: verbatim form -->",
            "<!-- pinned: 2026-01-01, pin-size-override: a -> b -->",
            "<!-- pinned: 2026-01-01, pin-size-override: use >= not > -->",
            "<!-- pinned: 2026-01-01, pin-size-override: keep <!-- pinned: form -->",
            "<!-- pinned: 2026-05-26, reconfirmed: 2026-07-25 because n > 2 -->",
            "<!-- PINNED: 2026-01-01 -->",
        ]
        for line in lines:
            assert attributed(line), f"not attributed as a pin comment: {line!r}"
            assert charged(line) == "", (
                f"attributed but not stripped in full: {line!r}"
            )


class TestPinCommentDominance_FloorGuard:
    """Prove the non-vacuity floor by mutation. A floor that cannot fail is
    not a floor — it is a second assertion that passes for every
    implementation and reads as rigour while it supplies none."""

    # Every date field closes the comment early, so the shipped patterns
    # refuse the whole corpus at attribution.
    DRIFTED_DATES = ["x-->y", "2026-01-01-->", "a-->b"]

    def test_drifted_corpus_satisfies_dominance_vacuously(self):
        """The property alone cannot detect the drift. This is the hazard."""
        drifted = build_corpus(
            CORPUS_OPENERS, self.DRIFTED_DATES, CORPUS_CLAUSES, CORPUS_CLOSERS
        )
        _, violations = measure_corpus(drifted)
        assert violations == 0, (
            "expected the drifted corpus to satisfy dominance vacuously; if it "
            "does not, this guard no longer demonstrates the hazard it exists for"
        )

    def test_floor_trips_on_the_drifted_corpus(self):
        """The floor DOES detect the drift. This is the mutation proof."""
        drifted = build_corpus(
            CORPUS_OPENERS, self.DRIFTED_DATES, CORPUS_CLAUSES, CORPUS_CLOSERS
        )
        attributed, _ = measure_corpus(drifted)
        assert attributed < MIN_ATTRIBUTED, (
            f"the drifted corpus reported {attributed} attributed lines, which "
            f"meets the floor of {MIN_ATTRIBUTED}. The floor therefore does not "
            f"discriminate a healthy corpus from a vacuous one."
        )

    def test_healthy_corpus_clears_the_floor(self):
        """Both directions, so the floor is not simply always false."""
        healthy = build_corpus(
            CORPUS_OPENERS, CORPUS_DATES, CORPUS_CLAUSES, CORPUS_CLOSERS
        )
        attributed, _ = measure_corpus(healthy)
        assert attributed >= MIN_ATTRIBUTED


class TestPinCommentCharge_Metamorphic:
    """The defect stated as a property rather than as one example."""

    def test_neighbour_charge_is_invariant_under_equal_length_rationales(self):
        from fixtures.pin_helpers import parse_pins

        body_a = "x" * 40
        arrow = "<!-- pinned: 2026-02-02, pin-size-override: keep a -> b now -->"
        plain = "<!-- pinned: 2026-02-02, pin-size-override: keep a to b now -->"
        assert len(arrow) == len(plain), "fixture error: comments differ in length"

        charge_arrow = parse_pins(two_pin_section(body_a, arrow))[0].body_chars
        charge_plain = parse_pins(two_pin_section(body_a, plain))[0].body_chars

        assert charge_arrow == charge_plain, (
            f"pin A is charged {charge_arrow} chars with an arrow in pin B's "
            f"rationale and {charge_plain} without it, though the two comments "
            f"are the same length. The rationale TEXT of one pin must not move "
            f"the size of its neighbour."
        )
        assert charge_arrow == len(body_a)


class TestPinCommentTrigger_AnyGreaterThan:
    """The trigger is any `>` character, not the arrow.

    A repair that special-cased the arrow would pass an arrow-only test and
    still leave every other `>` shape broken.
    """

    @pytest.mark.parametrize("rationale", [
        "a > b",
        "use >= not >",
        "> quoted blockquote",
        "C++ -> Rust",
        "a -> b",
        "count > 2 and count < 9",
    ])
    def test_neighbour_is_not_charged_for_the_comment(self, rationale):
        from fixtures.pin_helpers import parse_pins

        body_a = "x" * 40
        comment_b = f"<!-- pinned: 2026-02-02, pin-size-override: {rationale} -->"
        pins = parse_pins(two_pin_section(body_a, comment_b))
        assert pins[0].body_chars == len(body_a), (
            f"pin A charged {pins[0].body_chars} chars, expected {len(body_a)}. "
            f"The comment of pin B leaked into pin A's slice for rationale "
            f"{rationale!r}."
        )


class TestPinCommentReconfirmed_Repair:
    """A `reconfirmed:` clause holding `>` fails BOTH oracles on the old code.

    The pin loses its date, so its age degrades to unknown in silence, AND the
    neighbour is still charged. Both halves are asserted, because both fail.
    """

    @pytest.mark.parametrize("clause", [
        "reconfirmed: 2026-03-01 because A -> B",
        "reconfirmed: 2026-03-01 because n > 2",
    ])
    def test_date_survives_and_neighbour_is_not_charged(self, clause):
        from fixtures.pin_helpers import parse_pins

        body_a = "x" * 40
        comment_b = f"<!-- pinned: 2026-01-01, {clause} -->"
        pins = parse_pins(two_pin_section(body_a, comment_b))

        assert pins[1].date_comment == comment_b, (
            "pin B lost its date comment, so its age degrades to unknown"
        )
        assert pins[0].body_chars == len(body_a), (
            f"pin A charged {pins[0].body_chars} chars, expected {len(body_a)}"
        )

    def test_reconfirmed_date_is_recoverable_for_age(self):
        """The whole point of the clause is that the age reads from it."""
        import re

        from fixtures.pin_helpers import parse_pins

        body_a = "x" * 40
        comment_b = (
            "<!-- pinned: 2026-01-01, reconfirmed: 2026-03-01 because n > 2 -->"
        )
        pins = parse_pins(two_pin_section(body_a, comment_b))
        found = re.search(
            r"reconfirmed:\s*(\d{4}-\d{2}-\d{2})", pins[1].date_comment or ""
        )
        assert found is not None and found.group(1) == "2026-03-01"


class TestPinCommentDateField_BypassClosure:
    """A date field that closes the comment early must not grant an override.

    In the rendered document the comment ENDS at the first `-->`. Everything
    after it is ordinary visible prose. An override granted on the strength of
    that prose is an unlimited-size grant bought with text that is not inside
    a comment at all.
    """

    EARLY_CLOSING = "<!-- pinned: 2026-01-01--> tail, pin-size-override: r -->"

    def test_oversized_body_with_early_closing_date_field_denies(self):
        from pin_caps import PIN_SIZE_CAP, evaluate_full_state
        from fixtures.pin_helpers import parse_pins

        body = "y" * (PIN_SIZE_CAP + 100)
        section = f"{self.EARLY_CLOSING}\n### Oversized\n{body}\n"
        pins = parse_pins(section)
        violation = evaluate_full_state(pins)

        assert violation is not None, (
            "the size cap was bypassed: an override was granted from text that "
            "sits outside the rendered comment"
        )
        assert violation.kind == "size"

    def test_early_closing_date_field_grants_no_rationale(self):
        from fixtures.pin_helpers import parse_pins

        section = f"{self.EARLY_CLOSING}\n### Oversized\nshort\n"
        pins = parse_pins(section)
        assert pins[0].override_rationale is None

    def test_faithful_override_still_grants(self):
        """The closure must not refuse an honest curator line."""
        from pin_caps import PIN_SIZE_CAP, evaluate_full_state
        from fixtures.pin_helpers import parse_pins

        body = "y" * (PIN_SIZE_CAP + 100)
        comment = (
            "<!-- pinned: 2026-01-01, pin-size-override: verbatim dispatch form -->"
        )
        section = f"{comment}\n### Oversized\n{body}\n"
        pins = parse_pins(section)

        assert pins[0].override_rationale == "verbatim dispatch form"
        assert evaluate_full_state(pins) is None


class TestPinCommentStrip_NoCrossBoundary:
    """The strip must not run from one comment into the next.

    A run of the body class cannot contain `-->`, so the first terminator at
    or after the body start ends the match, and `re.sub` resumes after it.
    """

    @pytest.mark.parametrize("body,expected", [
        (
            "KEEP1 <!-- pinned: 2026-01-01 --> KEEP2 "
            "<!-- pinned: 2026-02-02 --> KEEP3",
            "KEEP1  KEEP2  KEEP3",
        ),
        (
            "KEEP1 <!-- pinned: 2026-01-01, pin-size-override: a -> b --> KEEP2 "
            "<!-- pinned: 2026-02-02 --> KEEP3",
            "KEEP1  KEEP2  KEEP3",
        ),
        (
            "KEEP1 <!-- just a note --> KEEP3",
            "KEEP1 <!-- just a note --> KEEP3",
        ),
        (
            "KEEP1 <!-- pinned: 2026-01-01 KEEP2 --> KEEP3",
            "KEEP1  KEEP3",
        ),
        (
            "KEEP1 <!-- pinned: 2026-01-01 a > b KEEP2 --> KEEP3",
            "KEEP1  KEEP3",
        ),
        (
            "KEEP1 <!-- pinned: 2026-01-01 KEEP2 KEEP3",
            "KEEP1 <!-- pinned: 2026-01-01 KEEP2 KEEP3",
        ),
    ])
    def test_strip_residue(self, body, expected):
        from pin_caps import _DATE_COMMENT_RE
        assert _DATE_COMMENT_RE.sub("", body) == expected

    def test_only_a_pin_comment_is_ever_stripped(self):
        from pin_caps import _DATE_COMMENT_RE
        body = "<!-- STALE: Last relevant 2026-01-01 --> <!-- other -->"
        assert _DATE_COMMENT_RE.sub("", body) == body


class TestPinCommentRows_WholeRowReaders:
    """Attribution and the rationale read a pin comment's whole row.

    Attribution matches the row patterns from the row's start
    (`Document.find_lines`) to its end (`\\s*\\Z`), so text before or
    after a comment on its row refuses it. The rationale is the text after
    the field name that follows the comment's first comma, and the date field
    refuses a comma, so a date field that spells the field name is never read
    as the rationale.
    """

    @staticmethod
    def _rationale(line):
        from pin_caps import override_rationale_text
        from shared.claude_md_markers import parse

        return override_rationale_text(parse(f"{line}\n"), 0)

    @pytest.mark.parametrize("row", [
        "lead <!-- pinned: 2026-01-01, pin-size-override: r -->",
        "<!-- pinned: 2026-01-01, pin-size-override: r --> trail",
        "lead <!-- pinned: 2026-01-01 -->",
        "<!-- pinned: 2026-01-01 --> trail",
    ])
    def test_text_before_or_after_the_comment_refuses_the_row(self, row):
        assert not attributed(row), f"attributed a row holding more than a comment: {row!r}"
        assert self._rationale(row) is None

    def test_the_whole_row_probe_is_not_vacuous(self):
        """CONTROL. The same comments alone on their rows are attributed.

        Without this, the refusals above also hold for attribution that
        accepts nothing at all.
        """
        assert attributed("<!-- pinned: 2026-01-01, pin-size-override: r -->")
        assert attributed("<!-- pinned: 2026-01-01 -->")
        assert self._rationale("<!-- pinned: 2026-01-01, pin-size-override: r -->") == "r"

    def test_the_rationale_is_the_override_field(self):
        line = "<!-- pinned: 2026-01-01, pin-size-override: keep a -> b -->"
        assert self._rationale(line) == "keep a -> b"

    def test_a_date_field_spelling_the_field_name_is_not_the_rationale(self):
        line = "<!-- pinned: 2026-01-01 pin-size-override: date, pin-size-override: real -->"
        assert self._rationale(line) == "real"

    def test_the_comment_patterns_stay_case_insensitive(self):
        import re

        from pin_caps import _DATE_COMMENT_RE, _DATE_COMMENT_ROW, _OVERRIDE_COMMENT_ROW
        for pattern in (_DATE_COMMENT_RE, _DATE_COMMENT_ROW, _OVERRIDE_COMMENT_ROW):
            assert pattern.flags & re.IGNORECASE, pattern.pattern


@pytest.fixture
def dominance_gate_env(tmp_path, monkeypatch, pact_context):
    """Point the gate at a temporary CLAUDE.md and return its path."""
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-dominance",
        project_dir=str(tmp_path),
    )
    point_resolver_at(monkeypatch, tmp_path)
    return claude_md


class TestPinCommentGate_LeadFrameWitness:
    """One integration witness, in a LEAD frame.

    A gate assertion in a NON-lead frame would be a predicate that cannot
    fail: the role check precedes the path match, so no decision payload is
    formed and an ALLOW assertion passes for every implementation, broken ones
    included. The pure-function tests above carry the certification; this test
    only witnesses that the repair reaches the real gate.
    """

    def test_over_block_on_the_neighbour_is_gone(self, dominance_gate_env):
        from pin_caps_gate import _check_tool_allowed

        claude_md = dominance_gate_env
        clean = make_claude_md_with_pins([
            "<!-- pinned: 2026-01-01 -->\n### Baseline\nshort"
        ])
        claude_md.write_text(clean, encoding="utf-8")

        # Pin A sits just under the cap on its own. Pin B's comment carries a
        # `>`, so on the old code it was charged to pin A and pushed it over.
        body_a = "x" * 1480
        entries = [
            f"<!-- pinned: 2026-01-01 -->\n### PinA\n{body_a}",
            "<!-- pinned: 2026-02-02, pin-size-override: keep a -> b -->\n"
            "### PinB\nshort",
        ]
        result = _check_tool_allowed({
            "tool_name": "Write",
            "agent_type": "pact-orchestrator",
            "tool_input": {
                "file_path": str(claude_md),
                "content": make_claude_md_with_pins(entries),
            },
        })
        assert result is None, (
            f"the gate denied a faithful curator edit: {result}. Pin A is "
            f"{len(body_a)} chars on its own; the deny can only come from the "
            f"comment of pin B being charged to it."
        )
