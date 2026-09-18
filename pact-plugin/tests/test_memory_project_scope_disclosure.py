"""Pin the per-save project-scope disclosure — the primary mis-scope deliverable.

WHY DISCLOSURE RATHER THAN A WARNING. A warning must decide something is wrong,
and the resolver cannot: nothing in a save payload asserts what a record is
ABOUT, so "is this filed correctly" is not decidable here. Disclosure makes no
such judgement. It reports which project the record was filed under and which
of the five sources decided it, on every save, and therefore has no
false-positive rate BY CONSTRUCTION.

IT IS NOT PERSISTED, AND THIS DOCSTRING USED TO IMPLY IT WAS. The old wording
read "the mis-scope that prompted this was undetectable after the fact by any
means; with this recorded it is one field away". The second half is false:
``project_scope`` is not a column and appears in neither ``database.py`` nor
``models.py``. It lives on the instance and in the save envelope and is gone
when the process exits. What it buys is real and smaller -- the resolution is
visible AT THE TIME OF THE SAVE, and afterwards only in whatever output the
caller kept. Do not plan an after-the-fact audit around this field.

WHAT IS LOAD-BEARING IS THAT AN ORDINARY, AGREEING SAVE STILL DISCLOSES. If the
field is ever narrowed to fire only on divergence, an absent value starts
meaning "fine", which is exactly the silent-success inference it exists to
remove. ``test_an_agreeing_save_discloses_too`` fails on that change.

THAT IS NOT THE SAME AS "SET ON EVERY BRANCH", AND THIS DOCSTRING USED TO SAY
IT WAS. It claimed the disclosure "belongs with ``last_sync_status`` (set on
every branch)". Both halves are wrong. The disclosure is assigned only after
the store write is read back and verified, so it is NOT set on every branch --
a save that raises earlier leaves it absent. And ``last_sync_status`` is not
set on every branch either: it is cleared at entry and ``_ensure_ready()`` runs
after that clear and can raise. Anchoring one field's contract to another
field's property is what let a false claim sit here unexamined; the property
above is stated directly instead, so it can be checked without a second field.

``location_divergence`` IS NOT A MISFILE FLAG. It compares the process's
working directory against the filed project. False means those two agree, never
that the record is correctly filed.

EVERY ASSERTION RUNS THE REAL ``save()`` over a throwaway database with
``sync_to_claude=False``, so no ambient store and no CLAUDE.md is reachable. An
earlier draft recomputed save()'s disclosure block inside the test helper and
asserted on THAT -- which would have passed with the production code deleted,
since it was testing the test's own arithmetic. ``main_repo_root`` is patched so
no git subprocess runs and results do not depend on the invocation directory.
"""
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import memory_api
from scripts.memory_api import PACTMemory


def _save(mem, repo_root, payload=None):
    """Run the REAL save() with the cwd repo pinned, and return the disclosure."""
    with patch.object(memory_api, "_ensure_ready", lambda: None), \
         patch.object(PACTMemory, "_store_embedding", return_value=None), \
         patch.object(memory_api, "main_repo_root", return_value=repo_root):
        mem._cwd_repo_root = False  # clear the per-instance cache
        mem.save(payload or {"context": "scope disclosure probe"},
                 sync_to_claude=False)
    return mem.last_project_scope


@pytest.fixture
def mem(tmp_path):
    """A real PACTMemory over a throwaway database, project supplied."""
    return PACTMemory(
        project_id="PACT-prompt", session_id="s", db_path=tmp_path / "scope.db"
    )


# --- the source is named ----------------------------------------------------

def test_supplied_project_id_is_named_as_its_own_source(mem):
    """`supplied` is the least-checked of the five sources, so it must be named.

    A payload project_id short-circuits detection AND is invisible to the
    env/record scope guard, which takes no arguments and never sees a payload.
    A reader who cannot tell a supplied project from a detected one cannot tell
    a deliberate cross-project filing from an inherited one.
    """
    scope = _save(mem, Path("/repos/PACT-prompt"))
    assert scope["source"] == "supplied", f"source was {scope['source']!r}"


def test_detected_project_names_the_winning_strategy():
    """A detected project reports WHICH strategy won, not merely that one did."""
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/some-project"}):
        project_id, source = PACTMemory._detect_project_id_with_source()
    assert source == "CLAUDE_PROJECT_DIR", f"source was {source!r}"
    assert project_id == "some-project"


def test_save_discloses_a_DETECTED_source_not_just_a_supplied_one(tmp_path):
    """The disclosure must carry the real source through save(), not a constant.

    Sibling of `test_supplied_project_id_is_named_as_its_own_source`, and the
    pair is the point. Every other save-path arm here builds its PACTMemory
    with `project_id=` supplied, so `source` is legitimately "supplied" in all
    of them -- and a mutant that HARDCODES `"source": "supplied"` survived the
    whole file until this arm existed. A field is only pinned by an input that
    makes the right and wrong implementations disagree, so this one resolves
    its project by detection instead.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/detected-project"}):
        mem = PACTMemory(session_id="s", db_path=tmp_path / "detected.db")
    assert mem._project_id == "detected-project", "fixture did not detect"

    scope = _save(mem, Path("/repos/detected-project"))
    assert scope["source"] == "CLAUDE_PROJECT_DIR", (
        f"save() disclosed source={scope['source']!r}; the detected strategy "
        "is not reaching the disclosure"
    )
    assert scope["source"] != "supplied"


def test_a_PAYLOAD_supplied_project_is_disclosed_as_supplied(tmp_path):
    """The source must describe THIS RECORD, not the instance's default.

    Found end-to-end, missed by every unit arm here. The CLI passes a payload
    project_id inside the memory dict rather than as a constructor argument, so
    an instance that detected its own default via CLAUDE_PROJECT_DIR was
    labelling a payload-supplied project `CLAUDE_PROJECT_DIR`. That is wrong on
    exactly the route with the least checking: a payload project_id
    short-circuits detection AND is invisible to the env/record scope guard.

    A reader who sees `CLAUDE_PROJECT_DIR` believes the environment chose this
    project and that the guard therefore vetted it. Neither is true here.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/detected-project"}):
        mem = PACTMemory(session_id="s", db_path=tmp_path / "payload.db")
    assert mem._project_id_source == "CLAUDE_PROJECT_DIR", "fixture did not detect"

    scope = _save(
        mem,
        Path("/repos/detected-project"),
        payload={"context": "x", "project_id": "explicitly-elsewhere"},
    )
    assert scope["project_id"] == "explicitly-elsewhere"
    assert scope["source"] == "supplied", (
        f"disclosed source={scope['source']!r} for a PAYLOAD-supplied project; "
        "that describes the instance's default, not this record"
    )


def test_the_string_only_detector_still_returns_a_bare_string():
    """The long-standing signature is preserved for its external callers.

    `_detect_project_id` is called by the precedence pins in
    test_project_dir_resolution.py and by test_backlog.py. Returning a tuple
    there would break them, which is why the richer variant was added beside it
    rather than replacing it.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/some-project"}):
        result = PACTMemory._detect_project_id()
    assert isinstance(result, str), f"expected a bare str, got {type(result)}"
    assert result == "some-project"


# --- what absence licenses, and what an agreeing save still discloses --------

def test_disclosure_starts_absent_on_a_fresh_instance(mem):
    """A fresh instance has confirmed no filing, so the field is None.

    THE NAME OF THIS TEST USED TO ASSERT SOMETHING FALSE. It read
    ``test_disclosure_starts_absent_and_absence_means_no_save_ran``, and
    absence does NOT mean "no save ran". The assignment sits below the store
    write and its read-back, so a save can exit AFTER ``create_memory``
    returned an id -- the row is then in the store with this field still None.
    Absence licenses "this process did not confirm a filing" and nothing
    stronger: not a filing, and not the absence of one.

    What this arm actually pins is the narrow true case it always tested -- a
    fresh instance, no save attempted. A name is not cosmetic in a test: it is
    what a reader greps for and what a failure message prints, so a false name
    ships the false contract even while the assertion below is correct.
    """
    assert mem.last_project_scope is None


def test_an_agreeing_save_discloses_too(mem):
    """The AGREEING case discloses too — absence must never imply 'fine'.

    Fails if the disclosure is narrowed to fire only on divergence, which would
    restore the silent-success inference the field exists to remove.

    RENAMED FROM ``test_disclosure_is_total_not_partial``, because the field is
    no longer total and the old name asserted that it was. "Total" was carrying
    two meanings: set on every BRANCH of save() (now false -- the assignment is
    below the verified write), and reported for every OUTCOME of a completed
    save including the agreeing one (still true, and the only thing this arm
    ever checked). The name now says which.
    """
    scope = _save(mem, Path("/repos/PACT-prompt"))
    assert scope is not None, "an ordinary correct save disclosed nothing"
    assert scope["location_divergence"] is False
    assert scope["project_id"] == "PACT-prompt"
    assert scope["cwd_repo"] == "PACT-prompt"
    assert scope["source"], "source is empty on an ordinary correct save"


# --- divergence, reported and not judged ------------------------------------

def test_divergence_is_reported_without_being_called_a_misfile(mem):
    """The measured incident's shape: saved from inside a different repo."""
    scope = _save(mem, Path("/Users/mj/Sites/scratchpad/viable"))
    assert scope["location_divergence"] is True
    assert scope["cwd_repo"] == "viable"
    assert scope["project_id"] == "PACT-prompt"


def test_no_repository_is_not_divergence(mem):
    """Outside a repo there is NO lower answer, which is not disagreement."""
    scope = _save(mem, None)
    assert scope["location_divergence"] is False
    assert scope["cwd_repo"] is None


def test_agreement_does_not_certify_the_subject(mem):
    """THE BLIND SPOT, asserted as a False rather than left to a comment.

    A record about another project written from the correct directory. Every
    strategy agrees, so `location_divergence` is False -- and that False is NOT
    a statement that the filing is right. Pinned so anyone reading the field as
    a misfile flag meets this test first.
    """
    scope = _save(
        mem,
        Path("/repos/PACT-prompt"),
        payload={"context": "a record entirely about the viable project"},
    )
    assert scope["location_divergence"] is False


@pytest.mark.parametrize(
    "key", ["project_id", "source", "cwd_repo", "location_divergence"]
)
def test_every_documented_key_is_present(mem, key):
    """The property's docstring names four keys; all four must be emitted.

    Guards the doc/behaviour pairing this whole arc has been about: a
    documented key the code does not emit is the same defect one level over.
    """
    assert key in _save(mem, Path("/repos/PACT-prompt"))


# --- staleness: the failure path must not inherit the previous save ---------

def test_a_refused_save_does_not_leave_the_previous_disclosure_readable(mem):
    """THE GAP THAT REACHED REVIEW. Every other arm here saves SUCCESSFULLY and
    then reads the field, so all of them pass whether or not save() clears it at
    entry -- the disclosure was pinned on the success path only.

    This one saves, then fails on the SAME LIVE INSTANCE, and asserts the
    disclosure does not still describe the first save. A stale value is worse
    than an absent one: absence licenses only "this process did not confirm a
    filing", which sends a reader to look, while a stale value names a real
    project, plausibly, and wrongly -- answering the reader instead, in the
    confident direction, with the previous save's fact.
    """
    first = _save(mem, Path("/repos/PACT-prompt"))
    assert first is not None and first["project_id"] == "PACT-prompt", (
        "setup failed: the first save disclosed nothing to go stale"
    )

    # Refuse the second save the way production does, at the env/record guard,
    # which fires BEFORE the project is resolved and before the disclosure.
    with patch.object(
        memory_api, "env_record_project_dir_disagreement",
        return_value=("/env/elsewhere", "/record/here"),
    ):
        with pytest.raises(memory_api.ProjectScopeDisagreementError):
            mem.save({"context": "refused"}, sync_to_claude=False)

    assert mem.last_project_scope is None, (
        "the refused save left the PREVIOUS save's disclosure readable: "
        f"{mem.last_project_scope!r}. A caller reads another record's filing "
        "as this attempt's."
    )


def test_a_save_raising_before_the_disclosure_does_not_leave_a_stale_value(mem):
    """Sibling of the arm above, for a NON-refusal early exit.

    The refusal is one named exit; `_ensure_ready()` and seven other callables
    between entry and the disclosure can propagate too, and the set is not
    reliably enumerable. This arm proves the clear covers an exit that is NOT
    the one the reviewer named, which is what makes it a root-cause fix rather
    than a patch on the reported path.
    """
    first = _save(mem, Path("/repos/PACT-prompt"))
    assert first is not None, "setup failed: nothing to go stale"

    boom = RuntimeError("initialization exploded")
    with patch.object(memory_api, "_ensure_ready", side_effect=boom):
        with pytest.raises(RuntimeError):
            mem.save({"context": "never lands"}, sync_to_claude=False)

    assert mem.last_project_scope is None, (
        "a save that raised in _ensure_ready left the previous disclosure "
        f"readable: {mem.last_project_scope!r}"
    )


def test_the_sync_status_sibling_still_reports_the_refusal(mem):
    """The asymmetry is deliberate, and this arm pins BOTH halves of it.

    Clearing the scope disclosure must not quietly change the sibling: on the
    same refusal, `last_sync_status` still reports REFUSED, because REFUSED is
    a member of ITS domain. If someone later "restores symmetry" by populating
    the scope on refusal, or by dropping the sync status's refusal value, one
    of these two assertions fails and the domain argument gets re-read.
    """
    with patch.object(
        memory_api, "env_record_project_dir_disagreement",
        return_value=("/env/elsewhere", "/record/here"),
    ):
        with pytest.raises(memory_api.ProjectScopeDisagreementError):
            mem.save({"context": "refused"}, sync_to_claude=False)

    assert mem.last_project_scope is None, "scope should be cleared, not populated"
    assert mem.last_sync_status == "refused", (
        f"the sibling stopped reporting the refusal: {mem.last_sync_status!r}"
    )


# --- the RELOCATION ITSELF: a save that REACHES the store and fails there ----

def test_a_save_that_fails_AT_THE_STORE_WRITE_discloses_nothing(mem, caplog):
    """THE FIX ITSELF, which nothing pinned until this arm existed.

    MEASURED, against the fix's own author's work rather than inferred: the
    mutant is that change reverted -- ``git show af1cbf72:...memory_api.py`` --
    and THAT MUTANT PASSES THE ENTIRE SUITE. Before the fix, 28 tests across
    the two project_scope files passed both WITH and WITHOUT the defect. That
    is exactly why it reached review invisible to everyone who ran the tests,
    and it is why this arm exists: not to add coverage, but because the suite
    was green straight through the defect.

    WHY EVERY OTHER STALENESS ARM HERE MISSES IT. The two above raise BEFORE
    the disclosure's position -- at the env/record guard and inside
    ``_ensure_ready`` -- so both pass whether the assignment sits above or
    below the store write. The relocation is observable ONLY on a save that
    gets PAST the old position and then fails, which is what this drives by
    making ``create_memory`` raise.

    WHAT ABSENCE LICENSES, and this assertion claims no more than that.
    ``None`` means THIS PROCESS DID NOT CONFIRM A FILING. It does NOT mean
    nothing was written: ``create_memory`` can return an id and the read-back
    verification still fail, leaving a row in the store with this field absent.
    So the arm asserts absence and says nothing whatever about the store.

    THE POSITIVE CONTROL IS LOAD-BEARING. Without a completed save first, a
    green here cannot distinguish "the field was correctly left absent" from
    "nothing in this arm ever populates the field", and the second is a dead
    arm that reports success.
    """
    # POSITIVE CONTROL: a completed save populates the field on this instance.
    ok = _save(mem, Path("/Users/mj/Sites/scratchpad/viable"))
    assert ok is not None and ok["location_divergence"] is True, (
        "the control save disclosed nothing, so a later absence would prove "
        f"only that this arm never populates the field: {ok!r}"
    )

    # Same instance, same divergent repo, failing AT the store write.
    with caplog.at_level(logging.WARNING):
        with patch.object(memory_api, "_ensure_ready", lambda: None), \
             patch.object(
                 memory_api, "main_repo_root",
                 return_value=Path("/Users/mj/Sites/scratchpad/viable")), \
             patch.object(
                 memory_api, "create_memory",
                 side_effect=RuntimeError("store write exploded")):
            mem._cwd_repo_root = False
            with pytest.raises(RuntimeError):
                mem.save({"context": "never lands"}, sync_to_claude=False)

    assert mem.last_project_scope is None, (
        "a save that died at the store write still discloses "
        f"{mem.last_project_scope!r}. The disclosure describes a COMPLETED, "
        "read-back filing; emitted here it asserts a filing this process never "
        "confirmed, in the confident direction, to a reader who has no "
        "exception and no return value to correct it."
    )

    # The warning describes an ATTEMPT and fires before the store write, so it
    # is REQUIRED to be present on exactly the path the disclosure is absent.
    # Asserting both here is what pins the asymmetry as behaviour rather than
    # leaving it as a comment somebody later "restores symmetry" over.
    assert "location divergence" in caplog.text, (
        "the divergence warning did not fire on the failure path. It describes "
        "an attempt, not a filing, so it must survive a save the disclosure "
        f"correctly abandons. Captured: {caplog.text!r}"
    )
