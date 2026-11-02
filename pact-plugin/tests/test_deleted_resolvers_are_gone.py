"""
Location: pact-plugin/tests/test_deleted_resolvers_are_gone.py
Summary: Pins that the fail-open root helpers and the raw git/cwd
         fall-through resolver stay deleted, so no caller can take the old
         route back to a worktree's own CLAUDE.md.
Used by: pytest. Inspects hooks/shared/project_scope.py,
         hooks/shared/claude_md_manager.py, hooks/staleness.py and
         hooks/session_init.py.

Every name below is read as an attribute of its module; restoring any one of
them (or re-exporting it) turns its arm red. The kept primitives are the
positive control: an arm that cannot see a name that exists is not looking.
"""
from __future__ import annotations

import pytest

import session_init
import staleness
from shared import claude_md_manager, project_scope

DELETED = [
    (project_scope, "project_memory_root"),
    (project_scope, "project_memory_root_or_none"),
    (project_scope, "project_memory_root_and_cause"),
    (project_scope, "_project_memory_root"),
    (project_scope, "unknown_root_cause"),
    (project_scope, "anchor_directory_is_missing"),
    (project_scope, "cwd_checkout_memory_root"),
    (project_scope, "record_is_for_declaration"),
    (claude_md_manager, "session_memory_root"),
    (staleness, "_resolve_project_claude_md_with_base"),
    (staleness, "get_project_claude_md_path"),
    (staleness, "_get_project_claude_md_path"),
    (staleness, "resolve_project_claude_md_path"),
    (session_init, "_get_project_claude_md_path"),
]

KEPT = [
    (project_scope, "memory_root_of"),
    (project_scope, "session_memory_root"),
    (claude_md_manager, "resolve_managed_document"),
    (claude_md_manager, "resolve_project_claude_md_path"),
    (staleness, "_find_existing_claude_md"),
    (staleness, "resolve_session_claude_md_path"),
]


@pytest.mark.parametrize(
    ("module", "name"), DELETED,
    ids=[f"{m.__name__}.{n}" for m, n in DELETED],
)
def test_the_deleted_name_is_not_importable(module, name):
    assert not hasattr(module, name)


@pytest.mark.parametrize(
    ("module", "name"), KEPT,
    ids=[f"{m.__name__}.{n}" for m, n in KEPT],
)
def test_the_kept_name_is_still_there(module, name):
    assert hasattr(module, name)
