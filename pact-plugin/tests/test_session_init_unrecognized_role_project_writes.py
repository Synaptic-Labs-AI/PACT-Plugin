"""A frame with an unrecognized agent_type does not create or migrate the project CLAUDE.md.

A present agent_type that is neither a lead spelling nor a registered PACT
specialist (a typo'd ``--agent pact-orchestrater``, or a user's own non-PACT
``--agent my-reviewer``) classifies "teammate" and passes
``_should_warn_unknown_role``. session_init skips steps 3/3b for it, so that
session writes no PACT structure into the project. A registered PACT specialist
(``pact-architect``, qualified or not) still creates and migrates the file:
that arm pins today's behaviour, so a later widening of the gate is deliberate.

The specialist arm is also the control for the unrecognized arm: the same
layout, differing only in agent_type, reaches both writers.
"""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init  # noqa: E402
from session_init import _UNKNOWN_ROLE_NOTICE, _should_warn_unknown_role  # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SESSION_ID = "cccccccc-dddd-eeee-ffff-000000000000"
USER_MD = "# My project\n\nBuild with make.\n"
CREATED = "Created project CLAUDE.md"
MIGRATED = "Migrated project CLAUDE.md"

UNRECOGNIZED = ["pact-orchestrater", "my-reviewer"]
SPECIALIST = ["pact-architect", "PACT:pact-architect"]


@pytest.fixture(autouse=True)
def _live_registry(monkeypatch):
    """Point the specialist registry at this plugin's agents/ directory.

    conftest scrubs CLAUDE_PLUGIN_ROOT, which empties the registry and would
    make every specialist read as unrecognized.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(_PLUGIN_ROOT))
    assert _should_warn_unknown_role({"agent_type": "pact-architect"}) is False, (
        "the registry is not live, so a specialist reads as unrecognized"
    )


def _run(monkeypatch, project_dir, agent_type):
    """Drive ``session_init.main()`` on ``startup`` with nothing stubbed."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": _SESSION_ID, "source": "startup", "agent_type": agent_type}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    output = json.loads(mock_stdout.getvalue())
    return (
        output.get("hookSpecificOutput", {}).get("additionalContext", ""),
        output.get("systemMessage", ""),
    )


def _project(tmp_path, name, user_md=False):
    project = tmp_path / name
    project.mkdir()
    if user_md:
        (project / "CLAUDE.md").write_text(USER_MD)
    return project


class TestUnrecognizedRoleWritesNothing:

    @pytest.mark.parametrize("agent_type", UNRECOGNIZED)
    def test_no_project_claude_md_is_created(self, agent_type, monkeypatch, tmp_path):
        project = _project(tmp_path, "plain")
        context, system_message = _run(monkeypatch, project, agent_type)
        assert sorted(p.name for p in project.rglob("*")) == [], (
            f"agent_type={agent_type!r} wrote into the project"
        )
        assert CREATED not in context and CREATED not in system_message
        assert _UNKNOWN_ROLE_NOTICE in system_message, (
            "the startup notice must still reach an unrecognized agent_type"
        )

    @pytest.mark.parametrize("agent_type", UNRECOGNIZED)
    def test_a_user_claude_md_is_left_byte_identical(
        self, agent_type, monkeypatch, tmp_path
    ):
        project = _project(tmp_path, "plain", user_md=True)
        context, system_message = _run(monkeypatch, project, agent_type)
        assert (project / "CLAUDE.md").read_text() == USER_MD, (
            f"agent_type={agent_type!r} rewrote the user's own CLAUDE.md"
        )
        assert MIGRATED not in context and MIGRATED not in system_message
        assert _UNKNOWN_ROLE_NOTICE in system_message


class TestRegisteredSpecialistStillWrites:
    """Today's behaviour for a PACT specialist, pinned so a widening is deliberate."""

    @pytest.mark.parametrize("agent_type", SPECIALIST)
    def test_the_project_claude_md_is_created(self, agent_type, monkeypatch, tmp_path):
        project = _project(tmp_path, "specialist")
        context, _ = _run(monkeypatch, project, agent_type)
        assert (project / ".claude" / "CLAUDE.md").exists()
        assert CREATED in context

    @pytest.mark.parametrize("agent_type", SPECIALIST)
    def test_a_user_claude_md_is_migrated(self, agent_type, monkeypatch, tmp_path):
        project = _project(tmp_path, "specialist", user_md=True)
        context, _ = _run(monkeypatch, project, agent_type)
        assert (project / "CLAUDE.md").read_text() != USER_MD
        assert MIGRATED in context
