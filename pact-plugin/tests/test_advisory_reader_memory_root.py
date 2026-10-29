"""Advisory readers follow the project-memory root (#1653, task #54).

Every writer and the display resolver target the MAIN checkout for a linked
worktree. A reader that resolved the worktree's own CLAUDE.md would describe a
document nothing writes any more. Each arm below gives the worktree a DIFFERENT
document from MAIN's, so the destination is discriminating, and asserts the
reader's output reflects MAIN.

Retargeted here: session_init's pin-slot status, refusal report and stale-block
directive; track_files' marker decision; check_pin_caps' listing; archive_pin's
resolution (a WRITER -- it archives a pin through the memory CLI); the drift
watcher's base; and the pin gate's match/target selectors.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
)


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _pin(date: str, heading: str) -> str:
    return f"<!-- pinned: {date} -->\n### {heading}\nbody text\n\n"


def _stale_pin(date: str, heading: str) -> str:
    """A pin the stale block counts: `is_stale` reads the STALE marker in the
    body, not the pin date."""
    return (
        f"<!-- pinned: {date} -->\n### {heading}\n"
        f"body <!-- STALE: Last relevant {date} -->\n\n"
    )


def _doc(*pins: str, duplicate_managed: bool = False) -> str:
    pins = "".join(pins)
    text = (
        f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n"
        f"{MEMORY_START_MARKER}\n"
        "## Retrieved Context\n\n"
        f"## Pinned Context\n\n{pins}"
        "## Working Memory\n"
        f"{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n"
    )
    if duplicate_managed:
        text += f"{MANAGED_START_MARKER}\n{MANAGED_END_MARKER}\n"
    return text


def _write_doc(directory: Path, text: str) -> Path:
    path = directory / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A real repo + linked worktree, env on the worktree, cwd in it."""
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    (main / "README").write_text("seed\n")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "adv", cwd=main)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    return main, worktree


def test_slot_status_counts_main_pins(world):
    from session_init import check_pin_slot_status
    from staleness import project_memory_root

    main, worktree = world
    _write_doc(main, _doc(
        _pin("2026-10-01", "Pin A"), _pin("2026-10-02", "Pin B")
    ))
    _write_doc(worktree, _doc())  # worktree: ZERO pins

    out = check_pin_slot_status(str(worktree), project_memory_root(worktree))

    assert out is not None
    assert "2/12" in out, f"slot status did not count MAIN's pins: {out!r}"


def test_refusal_report_names_main(world):
    from session_init import check_claude_md_refusals
    from staleness import project_memory_root

    main, worktree = world
    main_md = _write_doc(
        main, _doc(_pin("2026-10-01", "Pin A"), duplicate_managed=True)
    )
    _write_doc(worktree, _doc())  # worktree: clean

    out = check_claude_md_refusals(str(worktree), project_memory_root(worktree))

    assert out is not None
    assert str(main_md) in out, (
        f"the refusal report named another file: {out!r}"
    )


def test_the_helper_honours_a_base_passed_alone(world):
    """`resolve_session_claude_md_path(project_dir)` must read that base, not
    the environment. `world` points the environment at the worktree, so a
    helper that ignored its argument would answer with the worktree's file."""
    from staleness import resolve_session_claude_md_path

    main, worktree = world
    main_md = _write_doc(main, _doc())
    _write_doc(worktree, _doc(_pin("2026-10-01", "Other")))

    assert resolve_session_claude_md_path(str(main)).resolve() == main_md.resolve()


def test_stale_block_directive_reads_main(world):
    from session_init import check_pin_stale_block_directive
    from staleness import project_memory_root

    main, worktree = world
    _write_doc(main, _doc(
        _stale_pin("2020-01-05", "Old A"), _stale_pin("2020-02-05", "Old B")
    ))
    _write_doc(worktree, _doc())  # worktree: no stale pins

    out = check_pin_stale_block_directive(
        str(worktree), project_memory_root(worktree)
    )

    assert out is not None, (
        "the directive did not fire from MAIN's stale pins"
    )


def test_track_files_marker_decision_reads_main(world, monkeypatch):
    import shared.pact_context as pact_context
    import track_files
    from shared.constants import PIN_STALENESS_MARKER_NAME

    main, worktree = world
    _write_doc(main, _doc())  # MAIN: clean, no stale pins
    _write_doc(worktree, _doc(
        _stale_pin("2020-01-05", "Old A"), _stale_pin("2020-02-05", "Old B")
    ))

    session_dir = worktree / "session-dir"
    session_dir.mkdir()
    marker = session_dir / PIN_STALENESS_MARKER_NAME
    marker.write_text("pending", encoding="utf-8")
    monkeypatch.setattr(pact_context, "get_session_dir", lambda: str(session_dir))

    track_files.clear_pin_staleness_marker_if_resolved(
        "Bash", {"command": "python3 archive_pin.py 0"}
    )

    assert not marker.exists(), (
        "the marker was kept from the worktree's stale pins although MAIN is clean"
    )


def test_check_pin_caps_lists_main_pins(world):
    import check_pin_caps

    main, worktree = world
    _write_doc(main, _doc(
        _pin("2026-10-01", "Pin A"), _pin("2026-10-02", "Pin B")
    ))
    _write_doc(worktree, _doc())  # worktree: ZERO pins

    pins, reason = check_pin_caps._resolve_pins()

    assert reason is None, reason
    assert len(pins) == 2, f"the listing did not come from MAIN: {pins!r}"


def test_archive_pin_resolves_main(world):
    import archive_pin

    main, worktree = world
    main_md = _write_doc(main, _doc(_pin("2026-10-01", "Pin A")))
    _write_doc(worktree, _doc(_pin("2026-10-01", "Other")))

    path, _base = archive_pin.resolve_claude_md()

    assert Path(path).resolve() == main_md.resolve(), (
        f"archive_pin resolved {path}, not MAIN's file"
    )


def test_drift_watches_main(world):
    import shared.claude_md_drift as drift
    from shared.paths import get_claude_config_dir

    main, worktree = world
    main_md = _write_doc(main, _doc())
    _write_doc(worktree, _doc(_pin("2026-10-01", "Other")))
    session_dir = get_claude_config_dir() / "pact-sessions" / "proj" / "sd"
    session_dir.mkdir(parents=True)

    path, base = drift._resolve(session_dir)

    assert path is not None and base is not None
    assert Path(path).resolve() == main_md.resolve()
    assert Path(base).resolve() == main.resolve()


def test_the_pin_gate_matches_main(world):
    from shared.claude_md_manager import gate_target, match_project_claude_md

    main, worktree = world
    main_md = _write_doc(main, _doc())
    _write_doc(worktree, _doc())

    assert match_project_claude_md(str(main_md)) is not None, (
        "the pin gate did not recognise MAIN's file"
    )
    target = gate_target(str(main_md))
    assert target is not None and target.target.resolve() == main_md.resolve()
    assert match_project_claude_md(str(worktree / ".claude" / "CLAUDE.md")) is None
