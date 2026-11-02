"""The agent_handoff dedup key must DISCRIMINATE on handoff content.

WHAT THIS FILE COVERS THAT THE REST OF THE FAMILY DOES NOT, AND IT IS MEASURED
RATHER THAN FEARED. Two mutation passes forced the content term of the marker
key to a CONSTANT and left the whole handoff-family suite GREEN. The cause is
one cause: every other expectation in the family derives the key it predicts by
CALLING handoff_content_key, the function the mutant changes, so the expectation
moves with the mutant and the two agree. Those assertions guard the SHAPE of the
key. This file guards that the key DISCRIMINATES.

NOT ONE ASSERTION BELOW CALLS handoff_content_key OR occupant_hash. Every
expectation is derived a second way: from the RELATION between two marker
filenames written by two fires, and from the character class and width of each
segment. A mutant cannot drag those along with it.

THE TWO SURVIVING MUTANTS MOVE THE KEY IN OPPOSITE DIRECTIONS, AND ONE ARM
CANNOT CATCH BOTH.
  - A CONSTANT content term makes FEWER keys distinct. It dies to the REVISION
    arm, which asserts that two DIFFERENT handoffs give TWO events.
  - A KEY-ORDER-SENSITIVE serialization makes MORE keys distinct. It SURVIVES
    the revision arm and dies only to the SUPPRESSION arm, which asserts that
    two insertion orders of ONE handoff give ONE event.
  - A term that VARIES FOR EACH CALL without tracking content also makes more
    keys distinct, and also dies to the suppression arm. Proof that a key is
    not constant is not proof that the key tracks content.
  - A term keyed on the LENGTH of the canonical bytes is GREEN on the revision
    arm and GREEN on the suppression arm. It varies, it does not track
    content, and it collides for two DIFFERENT handoffs of EQUAL canonical
    length. Only an arm that holds the length CONSTANT and changes the content
    can see it, which is why TestEqualLengthContentStillDiscriminates exists.

WHY A SUPPRESSION ARM NEEDS ITS OWN CONTROL. An assertion that a count equals 1
passes for free if the second fire never reaches the marker layer at all, so a
broken fixture and a working dedup produce the same bytes. Each suppression arm
below therefore asserts the SET OF MARKER FILENAMES ON DISK, and then drives a
THIRD fire with genuinely different content that MUST emit. A dead fixture
cannot pass that third leg.

WHY THE FIRST-SET ARM SHIPS BESIDE THE REVISION ARM. A zero from a suppressed
emit and a zero from a broken fixture are also the same bytes. The first-set arm
pins that ONE post-completion write gives exactly one event, so the TWO in the
revision arm is caused by the second write and not by a fixture that doubles.

THE BOUND ON WHAT THIS FILE ESTABLISHES, AND IT IS A LIMIT ON THE CLAIM RATHER
THAN A NOTE BESIDE IT. NO FINITE ARM SET ESTABLISHES THAT THE CONTENT TERM IS
KEYED ON CONTENT. A term that hashes a PROJECTION of the content passes every
arm whose pair differs in that projection, and the projections do not run out.
These arms kill EIGHT NAMED PROJECTIONS: constant, canonical length, key set,
key count, byte prefix, byte suffix, values-only, and byte sum. A projection
outside that list is not covered by this file. See
TestContentProjectionsThatCollide for the rule that builds an arm for a
projection nobody has named.
"""

from pathlib import Path

import pytest

from fixtures.emitter import _run_main

_HEX = set("0123456789abcdef")

# Widths the marker key composes, read from the module constants of
# agent_handoff_marker as VALUES and re-stated here as literals on purpose: a
# test that imports the constant agrees with a change to the constant.
_OCCUPANT_WIDTH = 16
_CONTENT_WIDTH = 8

_TEAM = "pact-test"


def _marker_dir(tmp_path):
    """The marker directory the emit path writes into.

    The autouse config-root fixture redirects Path.home() to a per-test home
    below tmp_path, and get_claude_config_dir() resolves under that home.
    """
    return (
        Path.home() / ".claude" / "teams" / _TEAM / ".agent_handoff_emitted"
    )


def _marker_names(tmp_path):
    """The set of marker filenames on disk, or an empty set."""
    directory = _marker_dir(tmp_path)
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.is_file()}


def _split_key(name, task_id):
    """Split one marker filename into its occupant and content segments.

    Independently derived: the caller passes a task_id with no dash in it, so
    the filename has exactly three dash-joined segments and this function does
    not consult any production derivation to find the boundaries.
    """
    parts = name.split("-")
    assert len(parts) == 3, (
        f"marker filename {name!r} must be {{task_id}}-{{occupant}}-{{content}}"
    )
    assert parts[0] == task_id
    return parts[1], parts[2]


def _assert_segment_shape(occupant, content):
    """Both segments must be lowercase hex at their composed widths."""
    assert len(occupant) == _OCCUPANT_WIDTH, (
        f"occupant segment {occupant!r} must be {_OCCUPANT_WIDTH} hex chars"
    )
    assert set(occupant) <= _HEX, f"occupant segment {occupant!r} must be hex"
    assert len(content) == _CONTENT_WIDTH, (
        f"content segment {content!r} must be {_CONTENT_WIDTH} hex chars"
    )
    assert set(content) <= _HEX, f"content segment {content!r} must be hex"


def _completed_task(handoff):
    return {
        "status": "completed",
        "owner": "probe-agent",
        "metadata": {"handoff": handoff},
    }


def _payload(task_id):
    return {
        "task_id": task_id,
        "task_subject": "a standing task that spans sessions",
        "teammate_name": "probe-agent",
        "team_name": _TEAM,
    }


class TestPostCompletionHandoffRevision:
    """A HANDOFF written after the task completed, and then REVISED.

    This is the loss the content term repairs. With an occupant-only key, the
    first write claimed the marker and every later revision was suppressed for
    the lifespan of the team. A suppressed revision reached no carrier and left
    no record that it had been suppressed, so a reader cannot separate a
    suppressed revision from a revision nobody wrote.
    """

    def test_a_post_completion_first_set_emits_one_event(
        self, tmp_path, monkeypatch
    ):
        """FIXTURE CONTROL for the revision arm below, and an obligation of its
        own. One post-completion write of a handoff gives exactly one event and
        exactly one marker. Without this arm, a fixture that doubled every fire
        would make the revision arm pass while measuring nothing.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "firstset"
        handoff = {"produced": ["src/a.py"], "decisions": ["chose A"]}

        _run_main(_payload(task_id), _completed_task(handoff), calls)

        assert len(calls) == 1, (
            "one post-completion write must emit one agent_handoff event"
        )
        assert calls[0]["handoff"] == handoff
        names = _marker_names(tmp_path)
        assert len(names) == 1, f"expected one marker, found {sorted(names)}"
        occupant, content = _split_key(next(iter(names)), task_id)
        _assert_segment_shape(occupant, content)

    def test_a_revised_handoff_emits_a_second_event(
        self, tmp_path, monkeypatch
    ):
        """THE REVISION ARM. A DIFFERENT handoff on the SECOND write to a
        COMPLETED task must emit a SECOND event.

        This arm kills a content term forced to a CONSTANT: with a constant
        term the two writes derive one key, the second is suppressed, and the
        count is 1 rather than 2.

        It does NOT kill a key-order-sensitive serialization, which makes more
        keys distinct rather than fewer. The suppression arm below is what
        covers that direction.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "revision"
        first = {"produced": ["src/a.py"], "decisions": ["chose A"]}
        revised = {"produced": ["src/a.py"], "decisions": ["chose B instead"]}

        _run_main(_payload(task_id), _completed_task(first), calls)
        _run_main(_payload(task_id), _completed_task(revised), calls)

        assert len(calls) == 2, (
            "a REVISED handoff must emit a second agent_handoff event. A count "
            "of 1 means the content term stopped discriminating, which is the "
            "suppression this key exists to repair."
        )

        # READER SIDE: the revision must be CARRIED, not merely counted. A
        # second event holding the first handoff would be a duplicate rather
        # than a revision, and the count alone cannot separate the two.
        assert calls[0]["handoff"] == first
        assert calls[1]["handoff"] == revised

        names = _marker_names(tmp_path)
        assert len(names) == 2, f"expected two markers, found {sorted(names)}"

        occupants = set()
        contents = set()
        for name in names:
            occupant, content = _split_key(name, task_id)
            _assert_segment_shape(occupant, content)
            occupants.add(occupant)
            contents.add(content)

        # COMPOSED, NOT TRADED. The occupant term is the guard against a
        # task_id reused by a different occupant. A repair that passed the
        # content key through the occupant slot would drop that guard, and the
        # count of 2 above would not notice.
        assert len(occupants) == 1, (
            "the occupant segment must be IDENTICAL across a revision. Two "
            "occupant values means the content key was substituted into the "
            "occupant slot rather than composed with it."
        )
        assert len(contents) == 2, (
            "the content segment must DIFFER between two different handoffs"
        )


class TestEqualLengthContentStillDiscriminates:
    """Two DIFFERENT handoffs of EQUAL canonical length must give TWO events.

    WHY THIS ARM IS NOT COVERED BY THE TWO ARMS ABOVE, AND IT WAS MEASURED.
    A content term keyed on the LENGTH of the canonical bytes varies, and it
    does not track content. It passes the revision arm, because the two
    handoffs there are 49 and 57 canonical bytes and a length key separates
    them. It passes the suppression arm, because two insertion orders of one
    mapping have equal length and therefore agree, which is the correct
    verdict reached for an incorrect cause.

    So the length-keyed term suppresses a REVISION THAT HOLDS ITS LENGTH, and
    that is the loss the whole repair exists to remove. This arm holds the
    length CONSTANT and changes the content, which is the one shape that
    separates a length key from a content key.

    FOUR ROUTES BY WHICH THIS ARM CAN GO GREEN FOR A CAUSE THAT IS NOT CONTENT
    DISCRIMINATION. Each has a guard in the body below, and the guards are
    numbered to this list.
      1. THE PAIR DRIFTS TO DIFFERENT LENGTHS. Then the arm passes because the
         lengths differ, which reproduces the revision arm and adds nothing.
      2. THE OCCUPANT TERM DIFFERS BETWEEN THE TWO WRITES. Then the key differs
         on the occupant term and two events appear no matter what the content
         term does.
      3. THE TWO WRITES DO NOT SHARE A MARKER DIRECTORY. Then dedup is
         impossible, two events appear against EVERY mutant, and the arm is
         green by construction. THIS IS THE ROUTE THAT HIDES BEST, because the
         arm looks correct and reddens for nothing.
      4. THE COUNT IS READ FROM THE WRONG POPULATION. A neighbouring fixture
         can supply the second event.
    """

    def test_two_handoffs_of_equal_length_emit_two_events(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "eqlength"
        first = {"produced": "aaaa"}
        second = {"produced": "bbbb"}

        # GUARD 1. The equal length is the whole point of the pair, so it is
        # asserted here rather than recorded in a comment. An edit to one of
        # the two mappings that changed its length reddens this line instead
        # of degrading the arm in silence.
        from shared.canonical_json import canonical_bytes

        assert first != second
        assert len(canonical_bytes(first)) == len(canonical_bytes(second)), (
            "the two handoffs must have EQUAL canonical length, or this arm "
            "cannot separate a length-keyed term from a content-keyed one"
        )

        # GUARD 2, first half. ONE payload object drives both writes, so the
        # owner and the subject cannot drift apart between them. The second
        # half is the occupant-segment assertion below.
        payload = _payload(task_id)
        _run_main(payload, _completed_task(first), calls)
        _run_main(payload, _completed_task(second), calls)

        # GUARD 4. Count the events OF THIS TASK, not every event in the list.
        mine = [c for c in calls if c.get("task_id") == task_id]
        assert len(mine) == len(calls), (
            "a foreign event reached this arm's list, so the count below "
            "would read from the wrong population"
        )
        assert len(mine) == 2, (
            "two DIFFERENT handoffs of EQUAL canonical length must emit TWO "
            "events. A count of 1 means the content term is keyed on the SIZE "
            "of the handoff rather than on the handoff, so a revision that "
            "holds its length is suppressed."
        )
        assert mine[0]["handoff"] == first
        assert mine[1]["handoff"] == second

        names = _marker_names(tmp_path)
        assert len(names) == 2, f"expected two markers, found {sorted(names)}"
        occupants = set()
        contents = set()
        for name in names:
            occupant, content = _split_key(name, task_id)
            _assert_segment_shape(occupant, content)
            occupants.add(occupant)
            contents.add(content)
        # GUARD 2, second half. ONE occupant across the two writes. If this
        # were 2, the two events came from an occupant split and this arm
        # would say nothing about the content term.
        assert len(occupants) == 1, (
            "the occupant segment must be IDENTICAL across the two writes, or "
            "the second event came from an occupant split rather than from "
            "content discrimination"
        )
        # GUARD 3, first half. `_marker_names` reads ONE directory, so the
        # count of 2 above is itself the proof that the two writes shared a
        # marker directory. A per-write root would give one marker in each of
        # two directories, and that read would be 1.
        #
        # GUARD 3, second half, and it is the decisive one: it CANNOT live in
        # this test. A green arm cannot prove that dedup was possible. The
        # proof is that the CONSTANT-TERM mutant reddens THIS arm, which is a
        # positive control built from a mutant the rig already carries. If it
        # does not redden, this arm measures nothing.
        assert len(contents) == 2, (
            "the content segment must DIFFER for two handoffs of equal length"
        )


class TestContentProjectionsThatCollide:
    """Two more arms, each killing one PROJECTION of the handoff content.

    THE RULE THAT GENERATES THESE ARMS, AND IT IS WORTH MORE THAN THE LIST
    BELOW:

        AN ARM WITH A PAIR (p, q) KILLS A PROJECTION P WHEN
        `P(p) == P(q)` AND `p != q`.

    A term that hashes a PROJECTION of the content rather than the content
    passes every arm whose pair happens to differ in that projection. So to
    kill a named projection, build a pair that AGREES ON THAT PROJECTION and
    DIFFERS IN CONTENT. That construction works for a projection nobody has
    named yet, which is why the rule is recorded here and not only the
    projections it produced.

    NO FINITE ARM SET ESTABLISHES THAT THE TERM IS KEYED ON CONTENT, because
    the projections do not run out. Each arm in this file kills one NAMED
    projection and says nothing about a projection outside that list.

    ONE PAIR DOES NOT KILL THE TWO PROJECTIONS BELOW, which is why this is two
    arms. MEASURED: the rename pair agrees on values and DIFFERS in byte sum,
    and the permute pair agrees on byte sum and DIFFERS in values.

    HOW TO MIS-APPLY THE RULE ABOVE, AND IT IS THE WAY A CAREFUL READER WILL.
    THE RULE IS ABOUT A PAIR, AND AN ARM IS NOT ALWAYS ONE PAIR. An arm that
    carries a THIRD write, such as a positive-control leg, kills projections
    that its NOMINAL pair does not predict. So map the rule onto the WRITES in
    an arm, and not onto the arm.

    MEASURED INSTANCE, from the work that produced this file. By hand across
    the pairs here, a key-set-keyed term was predicted to die to 2 arms. The
    mutant measured 4 red. The difference is the suppression arm in this same
    file, of which the third write shares a key set with its first two and
    differs in content.

    🔴 THE DIRECTION IS WHAT MAKES THIS A WARNING RATHER THAN A NOTE. A
    hand-application of the rule predicts FEWER kills than occur, so the error
    runs in the SAFE direction. Nobody investigates a projection that turned
    out MORE dead than predicted, so a mis-application survives indefinitely
    and fires on the day it matters.
    """

    def test_a_field_rename_at_one_length_emits_two_events(
        self, tmp_path, monkeypatch
    ):
        """Kills a VALUES-ONLY projection: a term that hashes the values and
        drops the key names.

        THE DEFECT IT MODELS: a handoff revised by a FIELD RENAME, with the
        values unchanged, is suppressed and reaches no carrier.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "renamearm"
        first = {"aaa": "x"}
        second = {"bbb": "x"}

        from shared.canonical_json import canonical_bytes

        # The pair must AGREE on the projection under test and DIFFER in
        # content, or the arm cannot kill that projection. Asserted rather
        # than recorded in a comment, so a fixture edit reddens here.
        assert first != second
        assert len(canonical_bytes(first)) == len(canonical_bytes(second))
        assert sorted(first.values()) == sorted(second.values()), (
            "the pair must AGREE on values, or it cannot kill a values-only "
            "projection"
        )

        payload = _payload(task_id)
        _run_main(payload, _completed_task(first), calls)
        _run_main(payload, _completed_task(second), calls)

        mine = [c for c in calls if c.get("task_id") == task_id]
        assert len(mine) == len(calls)
        assert len(mine) == 2, (
            "a FIELD RENAME must emit a second event. A count of 1 means the "
            "content term hashes the VALUES and drops the key names, so a "
            "rename-only revision is suppressed."
        )
        names = _marker_names(tmp_path)
        assert len(names) == 2
        occupants = {_split_key(n, task_id)[0] for n in names}
        contents = {_split_key(n, task_id)[1] for n in names}
        assert len(occupants) == 1
        assert len(contents) == 2

    def test_a_byte_permutation_at_one_length_emits_two_events(
        self, tmp_path, monkeypatch
    ):
        """Kills a BYTE-SUM projection: a term that hashes the sum of the
        canonical bytes, which collides on any permutation of them.

        THE DEFECT IT MODELS: a content change that holds the byte multiset
        is suppressed and reaches no carrier.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "permutearm"
        first = {"k": "ab"}
        second = {"k": "ba"}

        from shared.canonical_json import canonical_bytes

        assert first != second
        assert len(canonical_bytes(first)) == len(canonical_bytes(second))
        assert sum(canonical_bytes(first)) == sum(canonical_bytes(second)), (
            "the pair must AGREE on byte sum, or it cannot kill a byte-sum "
            "projection"
        )

        payload = _payload(task_id)
        _run_main(payload, _completed_task(first), calls)
        _run_main(payload, _completed_task(second), calls)

        mine = [c for c in calls if c.get("task_id") == task_id]
        assert len(mine) == len(calls)
        assert len(mine) == 2, (
            "a content change that holds the byte multiset must emit a second "
            "event. A count of 1 means the content term hashes the SUM of the "
            "canonical bytes rather than their order."
        )
        names = _marker_names(tmp_path)
        assert len(names) == 2
        occupants = {_split_key(n, task_id)[0] for n in names}
        contents = {_split_key(n, task_id)[1] for n in names}
        assert len(occupants) == 1
        assert len(contents) == 2


class TestInsertionOrderDoesNotSplitTheKey:
    """One handoff, two insertion orders, one event.

    THE SUPPRESSION DIRECTION. The two emit paths observe the same handoff
    through different routes, so a content term that moved with insertion order
    would split them on content they agree about, and each split costs a
    duplicate event. The canonical serializer sorts keys, which is what makes
    the term insertion-order independent.

    This arm is what kills a key-order-sensitive serialization, and it also
    kills a term that varies without tracking content. The revision arm above
    stays GREEN against either of those, because each makes MORE keys distinct
    rather than fewer.
    """

    def test_two_insertion_orders_of_one_handoff_emit_one_event(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "reorder"
        # Equal content. Different insertion order. Python preserves insertion
        # order in a dict literal, so these two objects are NOT byte-identical
        # to a serializer that does not sort.
        forward = {
            "produced": ["src/a.py"],
            "decisions": ["chose A"],
            "uncertainty": ["none"],
        }
        reversed_order = {
            "uncertainty": ["none"],
            "decisions": ["chose A"],
            "produced": ["src/a.py"],
        }
        assert forward == reversed_order
        assert list(forward) != list(reversed_order), (
            "the two mappings must differ in insertion order, or this arm "
            "measures nothing"
        )

        _run_main(_payload(task_id), _completed_task(forward), calls)
        _run_main(_payload(task_id), _completed_task(reversed_order), calls)

        assert len(calls) == 1, (
            "two insertion orders of ONE handoff must dedup to ONE event. A "
            "count of 2 means the content term moved with insertion order, "
            "which splits the two emit paths on content they agree about."
        )
        # THE VACUITY GUARD. A count of 1 also results from a second fire that
        # never reached the marker layer. The marker SET is a state a dead
        # fixture cannot produce.
        names_after_two = _marker_names(tmp_path)
        assert len(names_after_two) == 1, (
            f"expected ONE marker after two reordered writes, found "
            f"{sorted(names_after_two)}"
        )

        # THE POSITIVE CONTROL, in the same test. A third fire carrying
        # genuinely DIFFERENT content MUST emit. Without this leg, a fixture
        # that had stopped reaching the emit path would pass every assertion
        # above.
        changed = {
            "produced": ["src/a.py"],
            "decisions": ["chose B instead"],
            "uncertainty": ["none"],
        }
        _run_main(_payload(task_id), _completed_task(changed), calls)
        assert len(calls) == 2, (
            "the emit path must still be live: a third fire with DIFFERENT "
            "content must emit. If this fails, the count of 1 above was a dead "
            "fixture rather than a working dedup."
        )
        names_after_three = _marker_names(tmp_path)
        assert len(names_after_three) == 2, (
            f"expected TWO markers after the changed write, found "
            f"{sorted(names_after_three)}"
        )
        assert names_after_two < names_after_three, (
            "the reordered pair's marker must survive the third write"
        )

    def test_an_unchanged_rewrite_emits_no_second_event(
        self, tmp_path, monkeypatch
    ):
        """The identity case, kept beside the reordering case on purpose.

        A content-keyed marker must leave the unchanged-rewrite behaviour
        intact: rewriting the SAME handoff emits once, not twice. This is the
        property a content term is most likely to break by accident.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "unchanged"
        handoff = {"produced": ["src/a.py"], "decisions": ["chose A"]}

        _run_main(_payload(task_id), _completed_task(handoff), calls)
        _run_main(_payload(task_id), _completed_task(dict(handoff)), calls)

        assert len(calls) == 1, (
            "an unchanged rewrite must NOT emit a second event"
        )
        assert len(_marker_names(tmp_path)) == 1


class TestContentTermIsATotalFunction:
    """A handoff that cannot serialize must not make the event vanish.

    Both emit paths wrap their whole body in a bare except, so a raise from the
    content-key derivation would drop the event silently. The derivation falls
    back to a constant term instead, which reverts that one task to the
    occupant-only dedup and still emits the first fire.

    THREE ARMS, THREE INPUT CLASSES, AND NO ONE OF THEM COVERS ANOTHER. DO
    NOT REMOVE ANY OF THEM AS REDUNDANT.
      - The SET-VALUED arm drives the genuine route with nothing patched,
        and it shows NO CRASH rather than a preserved event. See the
        correction below, which bounds what it establishes.
      - The MIXED-TYPE-KEY arm is the only one that shows the fallback
        PRESERVING an event, because it is the only input class where the
        two serializers disagree.
      - The PATCHED-RAISE arm drives a raise from OUTSIDE (TypeError,
        ValueError), which no unpatched input reaches, because the narrow
        handler absorbed TypeError. It is the only arm that separates the
        two handler widths. It patches the serializer, so it cannot exercise
        a genuine serialization route at all.

    🔴 A CORRECTION TO WHAT THE SET-VALUED ARM ESTABLISHES, AND THE
    OVERSTATEMENT WAS WRITTEN HERE BEFORE. MEASURED: for a set value,
    canonical_bytes RAISES TypeError and json.dumps, the serializer inside
    append_event, ALSO raises TypeError. So in production that event is lost
    by the second route after the fallback saved it from the first, and the
    fallback BUYS NOTHING for that input class. This arm reads as a
    preservation only because the harness SPIES append_event, and a spy
    accepts what production rejects. The arm is still worth keeping: it is
    the only unpatched arm, and it shows the derivation does not crash. It
    is NOT evidence that a handoff survives.

    WHERE THE FALLBACK GENUINELY PRESERVES, WHICH IS WHY THE SECOND ARM
    EXISTS. MEASURED, one input class: a mapping with MIXED-TYPE KEYS.
    canonical_bytes raises TypeError through sort_keys=True, which cannot
    order an int against a str, while append_event calls json.dumps WITHOUT
    sort_keys, which coerces the int key to a string and SUCCEEDS. So
    {"a": 1, 2: "b"} serializes to {"a": 1, "2": "b"} on the journal path
    after the content key falls back. THAT is totality changing the outcome.
    BOUND: both classes are unreachable from JSON-parsed metadata, which the
    module docstring states correctly, so this branch is defense in depth.
    """

    def test_a_non_serializable_handoff_still_emits(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "unserial"
        handoff = {"produced": {"a", "b"}}

        _run_main(_payload(task_id), _completed_task(handoff), calls)

        assert len(calls) == 1, (
            "a handoff that cannot serialize must still emit its first fire. A "
            "count of 0 means the raise escaped into the bare except and the "
            "event vanished from the journal with no record."
        )
        names = _marker_names(tmp_path)
        assert len(names) == 1
        occupant, content = _split_key(next(iter(names)), task_id)
        assert len(occupant) == _OCCUPANT_WIDTH
        assert set(occupant) <= _HEX
        # The fallback term must NOT be hex, or it could collide with the
        # digest of some other input.
        assert not set(content) <= _HEX, (
            f"the fallback content term {content!r} must not be hex, or it can "
            f"collide with a digest"
        )

    def test_a_mixed_key_handoff_is_preserved_by_the_fallback(self, tmp_path):
        """THE ONLY ARM THAT SHOWS THE FALLBACK PRESERVING AN EVENT.

        The set-valued arm above cannot show it: json.dumps rejects a set
        too, so production loses that event at append_event whatever the
        content key does. A MIXED-TYPE-KEY mapping is the one class where
        the two serializers disagree. canonical_bytes raises TypeError
        through sort_keys=True, which cannot order 2 against "produced",
        and append_event's json.dumps carries no sort_keys, so it coerces
        the int key and writes the event.

        SO THIS ARM MEASURES THE PROPERTY THE FALLBACK EXISTS FOR: the
        event reaches the journal WITH ITS CONTENT, and the marker key
        carries the non-hex fallback term rather than a digest.
        """
        monkeypatch_free_home = Path.home() / ".claude"
        assert not monkeypatch_free_home.exists()
        calls = []
        task_id = "mixedkey"
        # An int key beside a str key. json.loads cannot build this, which
        # is why the class is defense in depth, and it is REACHABLE from a
        # caller that hands b2 a mapping it built in process.
        handoff = {"produced": ["src/auth.ts"], 2: "an int key"}

        _run_main(_payload(task_id), _completed_task(handoff), calls)

        assert len(calls) == 1, (
            "a mixed-type-key handoff must still emit. This is the one "
            "input class where the content-key fallback preserves an event "
            "that would otherwise vanish, so a count of 0 means the "
            "fallback bought nothing where it was supposed to buy the most."
        )
        assert calls[0]["handoff"] == handoff, (
            "the event must carry the handoff CONTENT, not a placeholder. "
            "A preserved event with lost content is the loss this module "
            "is biased against, one field along."
        )
        names = _marker_names(tmp_path)
        assert len(names) == 1
        occupant, content = _split_key(next(iter(names)), task_id)
        assert len(occupant) == _OCCUPANT_WIDTH
        assert set(occupant) <= _HEX
        assert not set(content) <= _HEX, (
            f"the fallback content term {content!r} must not be hex, or it "
            f"can collide with a digest"
        )

    def test_a_raise_outside_typeerror_and_valueerror_still_emits(
        self, tmp_path, monkeypatch
    ):
        """THE ARM ABOVE CANNOT SEE THE DEFECT THIS ONE PINS.

        A set-valued handoff raises TypeError, and a handler named
        `(TypeError, ValueError)` absorbs it, so that arm passes under EITHER
        handler width and reports no difference between them. This arm drives
        a raise from OUTSIDE that pair, which the named handler lets escape
        into the emit path's bare except, where the event vanishes with no
        record. It therefore separates the two handlers: wide gives 1 event,
        named gives 0.

        WHY THE RAISE IS PATCHED AND NOT BUILT FROM A DEEP OBJECT. The
        genuine producer is RecursionError from a deeply nested mapping, and
        json.loads parses such a mapping without error, so the value does
        arrive through the JSON-parsed metadata the emit paths receive.
        THE DEPTH THAT REACHES THE RAISE CARRIES THREE PARAMETERS, AND ALL
        THREE ARE NAMED HERE ON PURPOSE. A parameter left implicit reads as
        a detail, and a reader who meets it as a detail does not check it.

            INTERPRETER   NOT the axis. STRUCK. An earlier record here named
                          the interpreter version, and a bisection falsified
                          it. Do not restore that wording.
            THREAD STACK  the axis, MEASURED BY BISECTION, moving the
                          threshold by more than 50x:
                            main thread, default   depth 64901 to 66460
                            thread stack 512 KiB   depth 1000 to 2558
                            thread stack 32 MiB    depth 130362 to 131921
                          `sys.getrecursionlimit()` read 1000 in all three
                          and predicted NONE of them, so do not size a
                          fixture against the recursion limit either.
            CONTAINER     MEASURED: a 100000-deep nested MAPPING raises, and
                          a 100000-deep nested LIST does NOT raise on the
                          same interpreter. The word "mapping" above is
                          load-bearing and is not decoration. A reader who
                          probes with a list fails to reproduce this record
                          and can discard it as stale, which is worse than
                          an absent record: the next reader re-derives it
                          and trusts the re-derivation less.

        A fixture pinned to a depth stops reaching the raise on a TOPOLOGY
        change and not merely on a version change, and it stops in the
        SILENT direction: the arm keeps passing while it drives an ordinary
        serialization. The 512 KiB row is what makes that sharp. At that
        stack the threshold falls to a few thousand, which is about 6 KiB of
        JSON, an ORDINARY payload. Carry no size without its parameters: the
        main-thread row is about 380 KiB of JSON at (main thread, default
        stack, mapping).

        The patch targets the name bound in agent_handoff_marker, because
        that module imports canonical_bytes by value and a patch of the
        defining module would not be the object it calls.
        """
        monkeypatch.setenv("HOME", str(Path.home()))
        calls = []
        task_id = "deepnest"

        def _raise_recursion(_value):
            raise RecursionError("maximum recursion depth exceeded")

        monkeypatch.setattr(
            "shared.agent_handoff_marker.canonical_bytes", _raise_recursion
        )
        _run_main(_payload(task_id), _completed_task({"produced": ["x"]}), calls)

        assert len(calls) == 1, (
            "a serialization raise outside (TypeError, ValueError) must still "
            "emit its first fire. A count of 0 means the raise escaped the "
            "content-key handler into the emit path's bare except and the "
            "event vanished from the journal with no record."
        )
        names = _marker_names(tmp_path)
        assert len(names) == 1
        occupant, content = _split_key(next(iter(names)), task_id)
        assert len(occupant) == _OCCUPANT_WIDTH
        assert set(occupant) <= _HEX
        assert not set(content) <= _HEX, (
            f"the fallback content term {content!r} must not be hex, or it can "
            f"collide with a digest"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
