"""Hoist parity: `_is_teachback_subject` was moved from task_lifecycle_gate.py
to shared/task_utils.py (as the public `is_teachback_subject`) so a second hook
surface — the handoff_ordering_gate PreToolUse WARN — can reuse the predicate
without importing the sibling PostToolUse gate module.

These tests pin three properties the hoist must preserve:

  1. SINGLE DEFINITION — the gate re-imports the shared function (its
     `_is_teachback_subject` IS `task_utils.is_teachback_subject`), and the
     canonical regex `_TEACHBACK_SUBJECT_PATTERN` is NOT duplicated back into
     the gate source. Duplication would reopen the drift class the structural
     match was introduced to close.
  2. BEHAVIORAL PARITY — the shared function returns the same verdict the gate's
     predicate always returned across canonical / non-canonical / non-string
     inputs (the move is behavior-preserving, not a re-spec).
  3. PURITY — never raises on hostile input (mirrors the pre-hoist contract).
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402
from shared.task_utils import is_teachback_subject  # noqa: E402


# Canonical Teachback subjects (must return True) and near-misses (False).
# The near-misses pin the structural-match rationale: a bare substring check
# would false-fire on planning/discussion subjects that merely contain the word.
_TRUE_CASES = [
    "devops: TEACHBACK for journal-durability",
    "backend-coder-2: TEACHBACK for #880 CODE",
    "architect-1: TEACHBACK for the redesign",
    "secretary: TEACHBACK for consolidation",
]
_FALSE_CASES = [
    "Plan: wake-lifecycle teachback re-arm fix",   # substring, not anchored shape
    "devops: implement Fix A",                      # work task, no TEACHBACK
    "TEACHBACK for x",                              # missing the `<name>:` prefix
    "Devops: TEACHBACK for x",                      # uppercase name (pattern is [a-z0-9-])
    "devops:TEACHBACK for x",                       # missing the space after colon
    "",                                             # empty
]


class TestSingleDefinition:
    def test_gate_alias_is_the_shared_function(self):
        """The gate's `_is_teachback_subject` must BE the hoisted shared
        function — same object, proving the re-import (not a re-definition)."""
        assert tlg._is_teachback_subject is is_teachback_subject

    def test_regex_not_duplicated_in_gate_source(self):
        """The canonical pattern lives ONLY in task_utils — the gate source
        must NOT re-introduce `_TEACHBACK_SUBJECT_PATTERN` (single-definition
        pin against the drift class)."""
        gate_src = Path(tlg.__file__).read_text(encoding="utf-8")
        # Allow the explanatory hoist comment to NAME the symbol, but there must
        # be NO assignment (`_TEACHBACK_SUBJECT_PATTERN = re.compile(...)`).
        assert not re.search(r"^_TEACHBACK_SUBJECT_PATTERN\s*=", gate_src, re.MULTILINE), (
            "the teachback regex must not be duplicated back into the gate; "
            "single definition lives in shared/task_utils.py"
        )


class TestBehavioralParity:
    @pytest.mark.parametrize("subject", _TRUE_CASES)
    def test_canonical_subjects_match(self, subject):
        assert is_teachback_subject(subject) is True
        assert tlg._is_teachback_subject(subject) is True

    @pytest.mark.parametrize("subject", _FALSE_CASES)
    def test_non_canonical_subjects_do_not_match(self, subject):
        assert is_teachback_subject(subject) is False
        assert tlg._is_teachback_subject(subject) is False


class TestPurity:
    @pytest.mark.parametrize("bad", [None, 123, [], {}, object()])
    def test_non_string_input_returns_false_never_raises(self, bad):
        assert is_teachback_subject(bad) is False
