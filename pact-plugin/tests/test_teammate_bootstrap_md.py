"""Drift tests for pact-plugin/commands/teammate-bootstrap.md (#401 Commit #11).

The state-machine section in teammate-bootstrap.md references the 4 locked
teachback states by name. If TEACHBACK_STATES changes in the shared module
without updating this markdown file, teammates will see stale state names
in their bootstrap load — these drift tests catch that.

Also guards against banned F12 state names (teachback_awaiting_lead,
teachback_cleared, teachback_expired, teachback_bypassed) appearing in the
file — TERMINOLOGY-LOCK.md §Banned terms grep-zero requirement.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "commands"
_BOOTSTRAP_MD = _COMMANDS_DIR / "teammate-bootstrap.md"


class TestTeammateBootstrapStatesMatchConstant:
    """Every TEACHBACK_STATES value must appear at least once in the md."""

    def test_bootstrap_md_exists(self):
        assert _BOOTSTRAP_MD.exists(), (
            f"teammate-bootstrap.md missing at {_BOOTSTRAP_MD}"
        )

    def test_all_states_referenced(self):
        from shared import TEACHBACK_STATES

        content = _BOOTSTRAP_MD.read_text(encoding="utf-8")
        for state in TEACHBACK_STATES:
            assert state in content, (
                f"teammate-bootstrap.md missing state name {state!r}; "
                f"TEACHBACK_STATES change must be mirrored in the md"
            )

    def test_teachback_submit_field_name_present(self):
        """The load-bearing metadata field name must be in the example."""
        content = _BOOTSTRAP_MD.read_text(encoding="utf-8")
        assert "teachback_submit" in content
        assert "teachback_approved" in content
        assert "teachback_corrections" in content


class TestBannedStateNamesAbsent:
    """F12 banned names must not appear in teammate-bootstrap.md.

    TERMINOLOGY-LOCK.md §Banned terms locks a grep-zero contract. The
    state-machine section must use the 4 locked names verbatim and NEVER
    the F12 alternatives (teachback_awaiting_lead / teachback_cleared /
    teachback_expired / teachback_bypassed), nor the issue-body flat
    variety shape (variety_score / variety_dimensions).
    """

    _BANNED = (
        "teachback_awaiting_lead",
        "teachback_cleared",
        "teachback_expired",
        "teachback_bypassed",
        "metadata.variety_score",
        "metadata.variety_dimensions",
    )

    def test_no_banned_names(self):
        content = _BOOTSTRAP_MD.read_text(encoding="utf-8")
        hits = [term for term in self._BANNED if term in content]
        assert not hits, (
            f"teammate-bootstrap.md contains banned terms {hits}; "
            f"TERMINOLOGY-LOCK.md §Banned terms requires grep-zero"
        )


class TestThresholdReference:
    """The variety threshold 7 must match TEACHBACK_BLOCKING_THRESHOLD.

    If the threshold constant changes, the documentation explaining the
    gate trigger must move in lockstep — otherwise teammates read a
    stale number and mis-calibrate their teachback timing.
    """

    def test_threshold_literal_matches_constant(self):
        from shared import TEACHBACK_BLOCKING_THRESHOLD

        content = _BOOTSTRAP_MD.read_text(encoding="utf-8")
        expected = f"variety >= {TEACHBACK_BLOCKING_THRESHOLD}"
        assert expected in content, (
            f"teammate-bootstrap.md missing exact phrase "
            f"{expected!r}; if TEACHBACK_BLOCKING_THRESHOLD changes, "
            f"update the md in lockstep"
        )


# Command files that carry the variety-threshold literal `7` in prose or
# metadata examples. If TEACHBACK_BLOCKING_THRESHOLD moves (e.g. to 8),
# every entry here must update in lockstep. rePACT.md / peer-review.md /
# imPACT.md are deliberately excluded: they delegate variety scoring to
# orchestrate.md's Per-Agent Variety Scoring section and never write the
# threshold literal themselves.
_COMMAND_FILES_WITH_THRESHOLD_LITERAL = [
    "orchestrate.md",
    "comPACT.md",
    "plan-mode.md",
]


class TestCommandFileThresholdDrift:
    """Risk #8 drift guard (RISK-MAP.md): command .md files that reference
    the variety-threshold literal must co-mention `variety` and the
    TEACHBACK_BLOCKING_THRESHOLD value on the same line. A future move of
    the constant must force an editor to update these files; missing a
    drift hit per file indicates desync."""

    @pytest.mark.parametrize("filename", _COMMAND_FILES_WITH_THRESHOLD_LITERAL)
    def test_command_file_has_threshold_literal_on_variety_line(self, filename):
        from shared import TEACHBACK_BLOCKING_THRESHOLD

        path = _COMMANDS_DIR / filename
        assert path.exists(), f"{filename} missing at {path}"
        content = path.read_text(encoding="utf-8")

        threshold_pattern = re.compile(
            rf"(?i)(^|\W)variety\W.*\b{TEACHBACK_BLOCKING_THRESHOLD}\b"
            rf"|\b{TEACHBACK_BLOCKING_THRESHOLD}\b.*\Wvariety\W"
            rf"|'total':\s*{TEACHBACK_BLOCKING_THRESHOLD}\b"
        )
        hits = [
            line for line in content.splitlines()
            if threshold_pattern.search(line)
        ]
        assert hits, (
            f"{filename} has no line co-mentioning 'variety' and "
            f"{TEACHBACK_BLOCKING_THRESHOLD}; if TEACHBACK_BLOCKING_THRESHOLD "
            f"changed, update {filename} in lockstep (or remove from "
            f"_COMMAND_FILES_WITH_THRESHOLD_LITERAL if this file no longer "
            f"references the literal)."
        )
