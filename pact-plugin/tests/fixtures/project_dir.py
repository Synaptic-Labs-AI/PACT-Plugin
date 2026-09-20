"""
Project-dir read-contract test helpers: umbrella tree factory, session-context
writer, discovery enabler, and subprocess env builder.

Consumed by: tests/test_project_dir_resolution.py (Phase B of
claude-project-dir-once — the session-record rung and the fail-closed
env/record write refusal).

Per the thin-conftest rule these are direct-import helpers, not pytest
fixtures; nothing here is injected.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from shared.pact_context import project_slug

from fixtures.hf_cache import hf_cache_env

# Sentinel for child_env's project_dir argument: leave the variable ABSENT in
# the child (distinct from setting it to "").
DELETE = object()


def make_umbrella(tmp_path: Path) -> SimpleNamespace:
    """Fabricate the umbrella workspace the read contract exists for.

    Layout under ``tmp_path``:
      - ``.claude/`` — the config root children resolve (CLAUDE_CONFIG_DIR).
      - ``umbrella/`` — the project workspace: NO ``.git`` of its own, a
        CLAUDE.md carrying the managed markers, and ``subrepo/`` with its own
        ``git init`` and NO CLAUDE.md.

    PRECONDITION GUARD (not optional): ``git -C <umbrella> rev-parse`` MUST
    FAIL at setup. A contributor whose TMPDIR sits under a repository would
    otherwise watch the marker walk / git rung resolve their TMPDIR's root,
    and every umbrella arm would measure that foreign repo while reading as
    the umbrella one.
    """
    config_root = tmp_path / ".claude"
    config_root.mkdir()
    umbrella = tmp_path / "umbrella"
    umbrella.mkdir()
    (umbrella / "CLAUDE.md").write_text(
        "# umbrella\n\n<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
        "<!-- PACT_MANAGED_END -->\n",
        encoding="utf-8",
    )
    subrepo = umbrella / "subrepo"
    subrepo.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(subrepo)],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )

    probe = subprocess.run(
        ["git", "-C", str(umbrella), "rev-parse"],
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    assert probe.returncode != 0, (
        f"git resolves a repository at/above {umbrella} — the umbrella is not "
        f"git-less (TMPDIR under a repo?), so every arm built on it would "
        f"measure the wrong scope"
    )
    return SimpleNamespace(config_root=config_root, project=umbrella, subrepo=subrepo)


def write_session_context(
    config_root: Path,
    session_id: str,
    project_dir: Path,
    *,
    slug: Optional[str] = None,
    body: Optional[str] = None,
) -> Path:
    """Write a pact-session-context.json where the discovery glob looks:
    ``<config_root>/pact-sessions/<slug>/<session_id>/pact-session-context.json``.

    The glob wildcard matches ANY slug directory, so the slug only needs to be
    present; it defaults to the writer's own derivation (project_slug of the
    recorded dir) so the fixture stays faithful to production. ``body``
    overrides the whole payload for the corrupt/shape arms.
    """
    slug = slug if slug is not None else project_slug(str(project_dir))
    target_dir = config_root / "pact-sessions" / slug / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "pact-session-context.json"
    if body is None:
        body = json.dumps({
            "team_name": "session-test",
            "session_id": session_id,
            "project_dir": str(project_dir),
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        })
    target.write_text(body, encoding="utf-8")
    return target


def enable_record_discovery(monkeypatch, pact_session_module) -> None:
    """Turn the shipped PYTEST_CURRENT_TEST refusal off for the test body.

    Same pattern as test_session_discovery_route.enable_discovery: the refusal
    reads os.environ, so deleting the variable supplies the REAL predicate a
    different input — no stub, no seam. MUST be called from the test body
    (pytest re-sets the variable between phases), and ONLY after the sandbox
    assertion: with the refusal off, the discovery glob must be rooted under
    the per-test tmp home, never the operator's real one.
    """
    real_home = Path(os.path.expanduser("~")).resolve()
    assert Path.home().resolve() != real_home, (
        "REFUSING to enable discovery: Path.home() is the real home, so the "
        "autouse redirect is not in effect and the glob would search the "
        "operator's live session directory"
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        pact_session_module, "_discovered_record", pact_session_module._DISCOVERY_UNSET
    )
    assert not os.environ.get("PYTEST_CURRENT_TEST"), (
        "the pytest marker is still set — the refusal will fire and every "
        "assertion below would pass without exercising the record route"
    )


def child_env(
    config_root: Path,
    *,
    home: Path,
    session_id: Optional[str] = None,
    project_dir=DELETE,
    memory_dir: Optional[Path] = None,
) -> dict:
    """A CONSTRUCTED env for a subprocess row — never an os.environ copy.

    An inherited env leaks the operator's session (CLAUDE_CODE_SESSION_ID),
    the suite's pytest marker (PYTEST_CURRENT_TEST, which would trip the
    record discovery's refusal in the child), and the real config root into
    the child, and the row would pass against live session state. Only what a
    row names crosses the boundary:

    - PATH (the child must find git/python)
    - HOME=home — Path.home monkeypatching does NOT cross the process
      boundary, so the child's home fallback must come from the real variable
    - CLAUDE_CONFIG_DIR=config_root (the fixture's tmp config root)
    - CLAUDE_CODE_SESSION_ID=session_id when given (the discovery glob key)
    - CLAUDE_PROJECT_DIR=project_dir when given; ABSENT when DELETE (the
      default), which is the point of most rows
    - PACT_TEST_MEMORY_DIR=memory_dir when given (the store redirect; the
      child must not open the operator's real memory store)
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config_root),
    }
    # HOME above relocates the HuggingFace cache with it, because the child
    # derives that cache root from HOME at import time. Without this a child
    # running the memory CLI sees an EMPTY cache and downloads the embedding
    # model — measured at ~81s for one test spawning three such children,
    # against its own 120s budget, which is the margin that finally lost.
    #
    # BOUND HERE RATHER THAN AT EACH SPAWN because this is the single
    # constructor for every child this fixture serves; a per-site spelling is
    # what let a sibling go without last time.
    env.update(hf_cache_env())
    if session_id is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session_id
    if project_dir is not DELETE:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    if memory_dir is not None:
        env["PACT_TEST_MEMORY_DIR"] = str(memory_dir)
    return env


def git_flake_shim(tmp_path: Path) -> Path:
    """A PATH shim dir whose `git` ALWAYS fails — the #1600 induced-flakiness
    experiment as a permanent fixture.

    Prepended to PATH it makes every git subprocess exit 1, so a resolution
    arm can prove its answer cannot have come from git. Pair every shimmed arm
    with a shim-LIVE control (an arm where git's death changes the answer) —
    without it, a shim that never took effect reads identically to the
    invariant it exists to prove.
    """
    shim_dir = tmp_path / "git-shim"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "git"
    shim.write_text("#!/bin/sh\necho 'git: disabled by test shim' >&2\nexit 1\n")
    shim.chmod(0o755)
    return shim_dir


def source_export_line(env_file: Path, name: str) -> Optional[str]:
    """Parse `export NAME=<shlex-quoted value>` back out of an env file.

    pytest standing in for the platform's source step: the platform reads
    CLAUDE_ENV_FILE and exports the lines into the next Bash tool env; the
    row asserts the value a spawned Bash WOULD see. shlex.split is the honest
    inverse of the producer's shlex.quote.
    """
    if not env_file.exists():
        return None
    prefix = f"export {name}="
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            parts = shlex.split(line[len(prefix):])
            return parts[0] if parts else ""
    return None
