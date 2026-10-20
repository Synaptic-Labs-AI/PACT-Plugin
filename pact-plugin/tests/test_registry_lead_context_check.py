"""
session_registry._lead_context_exists_for never raises.

The check decides whether register() skips a row for a session id that a lead
context file already claims. On any error it answers False, so register()
writes as it did before the check existed. A home directory that cannot be
resolved (Path.home() raising RuntimeError) is one such error.
"""

from pathlib import Path

from shared import session_registry

SID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"


def test_an_unresolvable_home_answers_false(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", no_home)
    assert session_registry._lead_context_exists_for(SID) is False


def test_a_lead_context_under_the_sid_answers_true(tmp_path, monkeypatch):
    """Control: with a resolvable home and a context file, the check holds."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    context = tmp_path / ".claude" / "pact-sessions" / "proj" / SID
    context.mkdir(parents=True)
    (context / "pact-session-context.json").write_text("{}", encoding="utf-8")
    assert session_registry._lead_context_exists_for(SID) is True
