"""
Location: pact-plugin/tests/test_user_global_backstop.py
Summary: Pins the user-global backstop behind the project-memory root checks.
         `resolve_managed_document` raises UserGlobalDocumentRefused rather
         than return a user-global CLAUDE.md, `session_document` reads that
         refusal as an unknown root with cause user_global, the drift probe
         never watches a user-global file named by an older cache, and the
         kernel strip, which edits the config root's CLAUDE.md on purpose, is
         untouched by the backstop.
Used by: pytest. Exercises hooks/shared/claude_md_manager.py
         (`resolve_managed_document`, `session_document`,
         `strip_orphan_kernel_block`) and hooks/shared/claude_md_drift.py
         (`_resolve`).

Every home is tmp_path/"home" (the suite's Path.home()); the config root is
set per test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import claude_md_drift, project_scope
from shared.claude_md_manager import (
    UserGlobalDocumentRefused,
    resolve_managed_document,
    session_document,
    strip_orphan_kernel_block,
)
from shared.project_scope import ROOT_CAUSE_USER_GLOBAL, SessionRoot

KERNEL = "<!-- PACT_START: v3 -->\nold kernel\n<!-- PACT_END -->\n"


@pytest.fixture
def home():
    home = Path.home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _config(monkeypatch, config: Path) -> Path:
    config.mkdir(parents=True, exist_ok=True)
    (config / "CLAUDE.md").write_text("# user-global\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.mark.parametrize("config_at_home", [True, False], ids=["default", "elsewhere"])
def test_the_resolver_refuses_home(home, tmp_path, monkeypatch, config_at_home):
    """$HOME's create target is ~/.claude/CLAUDE.md, the global file of the
    active profile or of the default one."""
    _config(monkeypatch, home / ".claude" if config_at_home else tmp_path / "cfg")

    with pytest.raises(UserGlobalDocumentRefused):
        resolve_managed_document(home)


def test_the_resolver_refuses_the_config_dir(home, tmp_path, monkeypatch):
    config = _config(monkeypatch, tmp_path / "cfg")

    with pytest.raises(UserGlobalDocumentRefused):
        resolve_managed_document(config)


def test_the_resolver_still_returns_a_project_document(home, tmp_path, monkeypatch):
    """Control: a project directory beside the global files resolves."""
    _config(monkeypatch, tmp_path / "cfg")
    project = tmp_path / "project"
    project.mkdir()

    assert resolve_managed_document(project).path == project / ".claude" / "CLAUDE.md"


def test_a_session_whose_root_slipped_past_the_checks_reads_as_user_global(
    home, tmp_path, monkeypatch
):
    """A route that hands `session_document` a user-global root gets the
    unknown-root answer every reader already renders, not an exception. The
    catch is there and nowhere else: the resolver called directly on the same
    root still raises."""
    _config(monkeypatch, tmp_path / "cfg")
    slipped = SessionRoot(home, True, home, None)
    monkeypatch.setattr(project_scope, "session_memory_root", lambda: slipped)

    session, document = session_document()

    assert document is None
    assert (session.anchor, session.root, session.cause) == (
        home, None, ROOT_CAUSE_USER_GLOBAL
    )
    with pytest.raises(UserGlobalDocumentRefused):
        resolve_managed_document(home)


def test_a_drift_cache_naming_home_watches_nothing(home, tmp_path, monkeypatch):
    """A v3 cache written before the user-global rule names $HOME; the
    re-probe must not pick up ~/.claude/CLAUDE.md."""
    _config(monkeypatch, home / ".claude")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / claude_md_drift._BASE_CACHE).write_text(
        json.dumps({"version": claude_md_drift._BASE_CACHE_VERSION,
                    "base": str(home), "enclosing": None}),
        encoding="utf-8",
    )
    assert (home / ".claude" / "CLAUDE.md").exists()

    assert claude_md_drift._resolve(session_dir) == (None, None)


def test_a_drift_cache_naming_a_project_still_watches_it(tmp_path, monkeypatch):
    """Control: the same cache naming a project re-probes its document."""
    _config(monkeypatch, tmp_path / "cfg")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    document = project / ".claude" / "CLAUDE.md"
    document.write_text("# p\n", encoding="utf-8")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / claude_md_drift._BASE_CACHE).write_text(
        json.dumps({"version": claude_md_drift._BASE_CACHE_VERSION,
                    "base": str(project), "enclosing": None}),
        encoding="utf-8",
    )

    assert claude_md_drift._resolve(session_dir) == (document, project)


def test_the_kernel_strip_still_edits_the_config_root_document(home, monkeypatch):
    """The backstop sits at the resolver, not at the writer: the one writer
    that edits the global file on purpose still does."""
    config = _config(monkeypatch, home / ".claude")
    (config / "CLAUDE.md").write_text("# Mine\n\n" + KERNEL + "\nafter\n", encoding="utf-8")

    status = strip_orphan_kernel_block()

    assert status is not None and "Removed obsolete PACT kernel block" in status
    assert "PACT_START" not in (config / "CLAUDE.md").read_text(encoding="utf-8")
