"""Pin the per-save project-scope disclosure — the primary mis-scope deliverable.

WHY DISCLOSURE RATHER THAN A WARNING. A warning must decide something is wrong,
and the resolver cannot: nothing in a save payload asserts what a record is
ABOUT, so "is this filed correctly" is not decidable here. Disclosure makes no
such judgement. It reports which project the record was filed under and which
of the five sources decided it, on every save, and therefore has no
false-positive rate BY CONSTRUCTION. The mis-scope that prompted this was
undetectable after the fact by any means; with this recorded it is one field
away.

TOTALITY IS THE LOAD-BEARING PROPERTY and the one most easily lost. The
disclosure belongs with ``last_sync_status`` (set on every branch) and NOT with
``last_embedding_status`` (set only when there is a problem). If it ever
becomes partial -- emitted only on divergence, say -- an absent value starts
meaning "fine", which is exactly the silent-success inference this removes.
``test_disclosure_is_total_not_partial`` fails on that change.

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


# --- totality ---------------------------------------------------------------

def test_disclosure_starts_absent_and_absence_means_no_save_ran(mem):
    """None is 'no save on this instance', never 'scope was fine'."""
    assert mem.last_project_scope is None


def test_disclosure_is_total_not_partial(mem):
    """The AGREEING case discloses too — absence must never imply 'fine'.

    Fails if the disclosure is narrowed to fire only on divergence, which would
    restore the silent-success inference the field exists to remove.
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
