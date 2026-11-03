"""
Location: pact-plugin/tests/test_required_document_parameters.py
Summary: Pins that every project-memory helper takes the session's document
         (or the document's path) as a REQUIRED argument, so no helper can
         resolve a CLAUDE.md for itself from CLAUDE_PROJECT_DIR or a git
         fall-through when a caller passes nothing.
Used by: pytest. Inspects hooks/session_init.py, hooks/shared/session_resume.py,
         hooks/shared/claude_md_manager.py and hooks/staleness.py.

Restoring a default on any listed parameter, or a legacy base parameter
(`project_dir` / `memory_root`) on a helper that has none, turns its arm red.
"""
from __future__ import annotations

import inspect

import pytest

import session_init
import staleness
from shared import claude_md_manager, session_resume

REQUIRED = [
    (session_init.check_pinned_staleness, "doc"),
    (session_init.check_pin_slot_status, "doc"),
    (session_init.check_claude_md_refusals, "doc"),
    (session_init.check_pin_stale_block_directive, "doc"),
    (session_init._extract_session_started, "doc"),
    (session_resume.update_session_info, "doc"),
    (session_resume.read_session_started, "doc"),
    (session_resume._extract_prev_session_dir, "doc"),
    (session_resume.existing_document, "doc"),
    (claude_md_manager.ensure_project_memory_md, "doc"),
    (claude_md_manager.migrate_to_managed_structure, "doc"),
    (staleness.check_pinned_staleness, "claude_md_path"),
    (staleness.check_pinned_block_signal, "claude_md_path"),
]


@pytest.mark.parametrize(
    ("function", "name"), REQUIRED, ids=[f.__name__ for f, _ in REQUIRED]
)
def test_the_document_parameter_has_no_default(function, name):
    parameter = inspect.signature(function).parameters[name]
    assert parameter.default is inspect.Parameter.empty


NO_BASE = [function for function, _ in REQUIRED if function is not (
    session_resume._extract_prev_session_dir
)] + [staleness.resolve_session_claude_md_path]


@pytest.mark.parametrize("function", NO_BASE, ids=[f.__name__ for f in NO_BASE])
def test_no_helper_takes_a_base_to_resolve_from(function):
    """`_extract_prev_session_dir` keeps `project_dir` as its fallback SLUG
    source, which is not a resolver input, so it is the one exception."""
    parameters = inspect.signature(function).parameters
    assert not {"project_dir", "memory_root"} & set(parameters)


def test_the_session_helper_takes_no_arguments():
    assert list(inspect.signature(staleness.resolve_session_claude_md_path).parameters) == []
