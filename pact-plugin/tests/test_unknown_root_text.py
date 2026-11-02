"""
Location: pact-plugin/tests/test_unknown_root_text.py
Summary: Pins the text helpers for an unknown project-memory root:
         `unknown_root_reason` names each of the three causes and raises on
         any other value, and `unknown_root_status` is the one skip-line
         template built from it.
Used by: pytest. Exercises hooks/shared/project_scope.py.

Reverting the explicit UNANSWERABLE branch to a default return turns the
raise arms red ("ok" and an unknown value would render "git could not
answer"). Changing the template text turns the status arm red.
"""
from __future__ import annotations

import pytest

from shared.project_scope import (
    ROOT_CAUSE_MISSING,
    ROOT_CAUSE_OK,
    ROOT_CAUSE_UNANSWERABLE,
    ROOT_CAUSE_UNVERIFIED,
    unknown_root_reason,
    unknown_root_status,
)

ANCHOR = "/work/wt"


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        (ROOT_CAUSE_MISSING, "the project directory /work/wt no longer exists"),
        (
            ROOT_CAUSE_UNVERIFIED,
            "the project directory /work/wt exists but could not be verified "
            "as a checkout of a repository",
        ),
        (ROOT_CAUSE_UNANSWERABLE, "git could not answer"),
    ],
)
def test_each_unknown_cause_has_its_own_clause(cause, expected):
    assert unknown_root_reason(cause, ANCHOR) == expected


@pytest.mark.parametrize("cause", [ROOT_CAUSE_OK, None, "bogus", ""])
def test_a_value_that_is_not_an_unknown_cause_raises(cause):
    with pytest.raises(ValueError):
        unknown_root_reason(cause, ANCHOR)


def test_the_status_template_wraps_the_reason():
    assert unknown_root_status(
        "Project CLAUDE.md",
        ROOT_CAUSE_MISSING,
        ANCHOR,
        "this start wrote nothing to either checkout.",
    ) == (
        "Project CLAUDE.md skipped: the project directory /work/wt no longer "
        "exists, so the project memory target is unknown; this start wrote "
        "nothing to either checkout."
    )


def test_the_status_template_refuses_a_known_cause():
    with pytest.raises(ValueError):
        unknown_root_status("Project CLAUDE.md", ROOT_CAUSE_OK, ANCHOR, "x.")
