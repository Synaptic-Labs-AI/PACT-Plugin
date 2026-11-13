"""Real-git fixtures for the repository-identity read contract.

Consumed by the test_project_identity_* modules. These are direct-import
helpers, not pytest fixtures (the thin-conftest rule).

Why real repositories: the failure modes under test are integration-shaped
(what `git config` answers, what `rev-parse --git-common-dir` reports for a
linked worktree or a --separate-git-dir checkout). A mocked git would pass
while the seam it replaces is the one that is broken.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Optional


def run_git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and return stripped stdout; fail loudly on error."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed in {cwd}: {result.stderr!r}"
    )
    return result.stdout.strip()


def assert_outside_repository(path: Path) -> None:
    """Guard: the fixture root must not sit inside a repository.

    Mirrors the existing layout_root guard. Without it `detect_id_for_root`
    can answer from the suite's own checkout and an arm measures the harness.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0, (
        f"{path} sits inside a repository: {result.stdout!r}"
    )


def make_repo(
    path: Path,
    *,
    remote: str | None = None,
    upstream: str | None = None,
    commit: bool = True,
) -> Path:
    """A real repository, optionally carrying origin/upstream remotes.

    `remote` is written to `.git/config` by `git remote add`; no network is
    contacted (the identity code only reads config).
    """
    path.mkdir(parents=True, exist_ok=True)
    run_git("init", "-q", ".", cwd=path)
    run_git("config", "user.email", "identity-test@example.invalid", cwd=path)
    run_git("config", "user.name", "Identity Test", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    run_git("add", "README", cwd=path)
    if commit:
        run_git("commit", "-qm", "seed", cwd=path)
    if remote is not None:
        run_git("remote", "add", "origin", remote, cwd=path)
    if upstream is not None:
        run_git("remote", "add", "upstream", upstream, cwd=path)
    return path


def add_remote(repo: Path, url: str, name: str = "origin") -> None:
    run_git("remote", "add", name, url, cwd=repo)


def make_worktree(main: Path, path: Path, branch: str | None = None) -> Path:
    """A real linked worktree of `main`; its `.git` is a file, not a directory."""
    branch = branch or path.name
    run_git("worktree", "add", "-q", str(path), "-b", branch, cwd=main)
    return path


def common_dir(repo: Path) -> Path:
    """The common dir git reports for `repo`, resolved against `repo`."""
    raw = run_git("rev-parse", "--git-common-dir", cwd=repo)
    path = Path(raw)
    return path if path.is_absolute() else (repo / path).resolve()


def write_common_dir_override(repo: Path, value: str) -> Path:
    """Write `<git-common-dir>/pact-project-id` — the ONLY override location."""
    target = common_dir(repo) / "pact-project-id"
    target.write_text(value + "\n", encoding="utf-8")
    return target


def make_separate_git_dir_repo(path: Path, external_git_dir: Path) -> Path:
    """A real repo whose common dir lives outside the work tree."""
    path.mkdir(parents=True, exist_ok=True)
    external_git_dir.mkdir(parents=True, exist_ok=True)
    run_git("init", "-q", f"--separate-git-dir={external_git_dir}", ".", cwd=path)
    run_git("config", "user.email", "identity-test@example.invalid", cwd=path)
    run_git("config", "user.name", "Identity Test", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    run_git("add", "README", cwd=path)
    run_git("commit", "-qm", "seed", cwd=path)
    return path

def seed_session_evidence(
    config_dir, project_dir: Path, session_id: str, started_at: Optional[str] = None
) -> Path:
    """Record a session for `project_dir`, where the legacy-admission check looks.

    Writes `pact-session-context.json` into the session directory
    `reconstruct_session_dir(project_dir, session_id)` names, with the
    `project_dir` it records and a zone-aware `started_at` (now, unless
    given). A legacy bucket whose rows carry `session_id` is then read by a
    session whose identity root is `project_dir`, provided the repository's
    `description` was created before that start.
    `config_dir` is the isolated config root the file must land under (the
    suite sandboxes it at `tmp_path / "home" / ".claude"`); the helper
    refuses to write anywhere else, so a test can never seed a real session.
    """
    import json
    from datetime import datetime, timezone

    from shared.pact_context import reconstruct_session_dir

    session_dir = Path(reconstruct_session_dir(str(project_dir), session_id))
    assert Path(config_dir).resolve() in session_dir.resolve().parents, (
        f"{session_dir} is not under the isolated config root {config_dir}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    context = session_dir / "pact-session-context.json"
    context.write_text(
        json.dumps({
            "project_dir": str(project_dir),
            "session_id": session_id,
            "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        }),
        encoding="utf-8",
    )
    return context


def _is_text(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def reason_dict_sites(tree: ast.AST, exempt: frozenset = frozenset()) -> list:
    """Every place the code puts a `reason` key into a dict, as
    `(lineno, has_path)`, in each statically visible form.

    The forms: a dict display (`{"reason": ...}`, beside `**` splices too); a
    `dict(...)` call with a `reason` keyword or a `("reason", ...)` pair; a
    subscript store (`d["reason"] = ...`); and `.update(reason=...)` or
    `.setdefault("reason", ...)`. `has_path` is True when the same construct
    also sets `path`, so a caller can exempt the `{path, reason}` pair. Nodes
    whose `id()` is in `exempt` (a builder's own body) are skipped.
    """
    sites = []
    for node in ast.walk(tree):
        if id(node) in exempt:
            continue
        if isinstance(node, ast.Dict):
            keys = node.keys
            if any(_is_text(k, "reason") for k in keys):
                sites.append((node.lineno, any(_is_text(k, "path") for k in keys)))
        elif isinstance(node, ast.Call):
            func = node.func
            names = {kw.arg for kw in node.keywords}
            pairs = [
                element.elts[0]
                for argument in node.args
                if isinstance(argument, (ast.List, ast.Tuple))
                for element in argument.elts
                if isinstance(element, ast.Tuple) and element.elts
            ]
            if isinstance(func, ast.Name) and func.id == "dict":
                if "reason" in names or any(_is_text(k, "reason") for k in pairs):
                    has_path = "path" in names or any(_is_text(k, "path") for k in pairs)
                    sites.append((node.lineno, has_path))
            elif isinstance(func, ast.Attribute) and func.attr in ("update", "setdefault"):
                first = node.args[0] if node.args else None
                if "reason" in names or (func.attr == "setdefault" and _is_text(first, "reason")):
                    sites.append((node.lineno, "path" in names))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_text(target.slice, "reason"):
                    sites.append((node.lineno, False))
    return sites


def positional_constructions(tree: ast.AST, class_name: str) -> list:
    """Line numbers where `class_name` is built with a positional field.

    The class is recognised by its name, as an attribute (`module.Name`), or
    through any name bound to it (aliases followed to a fixed point). A
    construction is positional when it passes a positional argument (a
    starred one included), when it goes through `_make` (always
    positional), or when `functools.partial` binds positional arguments to
    it.
    """
    aliases = {class_name}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_class(node.value, aliases, class_name):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in aliases:
                        aliases.add(target.id)
                        changed = True
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if _is_class(func, aliases, class_name) and node.args:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "_make" and _is_class(
            func.value, aliases, class_name
        ):
            lines.append(node.lineno)
        elif (
            (isinstance(func, ast.Name) and func.id == "partial")
            or (isinstance(func, ast.Attribute) and func.attr == "partial")
        ) and node.args and _is_class(node.args[0], aliases, class_name) and len(node.args) > 1:
            lines.append(node.lineno)
    return lines


def _is_class(node, aliases: set, class_name: str) -> bool:
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == class_name
    )
