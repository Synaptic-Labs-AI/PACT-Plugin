"""
Shared test fixtures and infrastructure.

This conftest.py is intentionally thin. It owns:
- sys.path setup for tests/, hooks/, skills/pact-memory/
- the genuinely cross-cutting ``pact_context`` fixture
- re-exports of pytest-fixture-injected symbols from tests/fixtures/

Concern-specific helpers live in tests/fixtures/<concern>.py and are
imported directly by the test files that need them (direct-import
symbols) or re-exported here (pytest-fixture-injected symbols).
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add tests directory to path for helpers + fixtures module imports
sys.path.insert(0, str(Path(__file__).parent))

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Add pact-memory scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

# Re-exports for pytest-fixture-injection.
# Pytest discovers fixtures by name in conftest.py; these re-exports
# make subdir-defined fixtures injectable in any test_*.py file.
from fixtures.refresh_system import (  # noqa: E402, F401
    tmp_transcript,
    sample_checkpoint,
    peer_review_mid_workflow_transcript,
    orchestrate_code_phase_transcript,
    no_workflow_transcript,
    terminated_workflow_transcript,
)


@pytest.fixture
def pact_context(tmp_path, monkeypatch):
    """
    Factory fixture to create a mock PACT session context file for testing.

    Creates a temporary context file and patches _context_path
    so hooks read from it instead of the real session-scoped location.

    Usage:
        def test_something(pact_context):
            pact_context(team_name="test-team", session_id="test-session")
            # Now get_team_name() returns "test-team", etc.

    Returns:
        Function that writes a context file and returns its path
    """
    import shared.pact_context as ctx_module

    context_file = tmp_path / "pact-session-context.json"

    def _write(
        team_name="test-team",
        session_id="test-session",
        project_dir="/test/project",
        plugin_root="",
        started_at="2026-01-01T00:00:00Z",
    ):
        context_file.write_text(json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": plugin_root,
            "started_at": started_at,
        }), encoding="utf-8")
        # Patch the resolved context path to point to our test file
        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        # Clear the module-level cache so fresh reads happen
        monkeypatch.setattr(ctx_module, "_cache", None)
        return context_file

    # Always reset module state at fixture setup (even if _write isn't called,
    # ensures no cross-test cache or path leakage)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_context_path", None)

    return _write
