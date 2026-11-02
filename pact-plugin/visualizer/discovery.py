"""Location: pact-plugin/visualizer/discovery.py
Summary: Read-only discovery of PACT session journals. Owns the exact-depth
         layout rule (<root>/pact-sessions/<slug>/<session_id>/
         session-journal.jsonl), the root listing helpers the API exposes, and
         the traversal-guarded lookup behind /api/session.
Used by: visualizer.model (sessions + model), visualizer.server (routes),
         visualizer.cli (--list), tests.

Discovery NEVER recurses: a journal at any other depth (a nested scratch
journal, a backup directory) is not a session. Unknown files are ignored, not
errors, so a half-created session directory does not take the server down.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .settings import (
    JOURNAL_FILENAME,
    PACT_SESSIONS_DIRNAME,
    RootSpec,
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_COMPONENT = 255


@dataclass(frozen=True)
class SessionRef:
    """One discovered session journal.

    Location: discovery.SessionRef.
    Summary: Identifies a journal by root + slug + session_id and carries the
             absolute paths needed to read it. ``relative_journal`` is the
             root-relative path echoed in the model's source block.
    Used by: model, server, cli, tests.
    """

    root: RootSpec
    slug: str
    session_id: str
    journal: Path

    @property
    def root_id(self) -> str:
        """The root id used in query parameters and the sessions payload."""
        return self.root.id

    @property
    def session_dir(self) -> Path:
        """The directory holding the journal and its sibling session files."""
        return self.journal.parent

    @property
    def relative_journal(self) -> str:
        """``pact-sessions/<slug>/<session_id>/session-journal.jsonl``."""
        return (
            f"{PACT_SESSIONS_DIRNAME}/{self.slug}/{self.session_id}/{JOURNAL_FILENAME}"
        )

    @property
    def key(self) -> str:
        """Stable per-root session key (root id :: slug :: session id)."""
        return f"{self.root_id}::{self.slug}::{self.session_id}"


def valid_component(value: object) -> bool:
    """True when a slug/session id is a safe single path component.

    Location: discovery.valid_component.
    Summary: Rejects empty values, path separators, NUL/control characters,
             exact "." / ".." and leading dots. Deliberately permissive about
             the rest: real slugs are project-directory basenames and real
             session ids are harness ids, and the only thing that matters here
             is that the value cannot escape its parent directory.
    Used by: discover_root, find_session, roster.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_COMPONENT:
        return False
    if value in {".", ".."} or value.startswith("."):
        return False
    if "/" in value or "\\" in value:
        return False
    return not _CONTROL_RE.search(value)


def _safe_dirs(parent: Path) -> list[Path]:
    """Sorted non-hidden, non-symlink directories under ``parent``."""
    try:
        entries = sorted(parent.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    out: list[Path] = []
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            continue
        if not valid_component(entry.name):
            continue
        out.append(entry)
    return out


def discover_root(root: RootSpec) -> list[SessionRef]:
    """Discover the sessions under one config root.

    Location: discovery.discover_root.
    Summary: Enumerates ``<root>/pact-sessions/<slug>/<session_id>/
             session-journal.jsonl`` with exactly two levels of directory
             nesting. Nested or differently named journals are ignored. A
             missing root or pact-sessions directory reads as no sessions.
    Used by: discover_sessions, model, server, cli, tests.
    """
    sessions: list[SessionRef] = []
    sessions_root = root.path / PACT_SESSIONS_DIRNAME
    for slug_dir in _safe_dirs(sessions_root):
        slug = slug_dir.name
        for session_dir in _safe_dirs(slug_dir):
            journal = session_dir / JOURNAL_FILENAME
            try:
                if not journal.is_file():
                    continue
            except OSError:
                continue
            sessions.append(
                SessionRef(
                    root=root,
                    slug=slug,
                    session_id=session_dir.name,
                    journal=journal,
                )
            )
    return sessions


def discover_sessions(roots: Sequence[RootSpec]) -> list[SessionRef]:
    """Discover sessions across every configured root, in root order.

    Location: discovery.discover_sessions.
    Summary: Concatenates discover_root for each root; duplicate roots are the
             caller's problem (resolve_roots already de-duplicates).
    Used by: model, server, cli.
    """
    sessions: list[SessionRef] = []
    for root in roots:
        sessions.extend(discover_root(root))
    return sessions


def count_root_journals(root: RootSpec) -> int:
    """Count journals under one root for the health/sessions roots entries."""
    return len(discover_root(root))


def root_payload(
    roots: Sequence[RootSpec], *, with_counts: bool = True
) -> list[dict]:
    """The ``roots`` array used by /api/health and /api/sessions.

    Location: discovery.root_payload.
    Summary: One dict per root with id/label/path/journals/default. Counts are
             computed on demand so a stale cache can never hide a new journal.
    Used by: model.build_health, model.build_sessions_list, server.
    """
    payload: list[dict] = []
    for root in roots:
        entry = root.as_dict()
        if with_counts:
            entry["journals"] = count_root_journals(root)
        payload.append(entry)
    return payload


def find_root(roots: Sequence[RootSpec], root_id: str | None) -> RootSpec | None:
    """Resolve a requested root id (or None for the default root).

    Location: discovery.find_root.
    Summary: Matches on the compact id first, then on the lexical absolute
             path, so a caller can pass either the id returned by the API or
             an explicit path. Unknown ids return None (the handler answers
             400).
    Used by: find_session, server.
    """
    if not roots:
        return None
    if root_id is None or root_id == "":
        for root in roots:
            if root.default:
                return root
        return roots[0]
    for root in roots:
        if root.id == root_id:
            return root
    wanted = os.path.abspath(os.path.expanduser(root_id))
    for root in roots:
        if os.path.abspath(str(root.path)) == wanted:
            return root
    return None


def find_session(
    roots: Sequence[RootSpec],
    slug: object,
    session_id: object,
    root_id: str | None = None,
) -> SessionRef | None:
    """Traversal-guarded lookup of one session journal.

    Location: discovery.find_session.
    Summary: Validates both path components as single safe components, resolves
             the requested root (default root when omitted), and checks that
             the resulting journal exists. Returns None for any invalid or
             missing session; the HTTP layer turns that into 400 (bad params)
             or 404 (not found).
    Used by: server (/api/session), model, tests.
    """
    if not valid_component(slug) or not valid_component(session_id):
        return None
    root = find_root(roots, root_id)
    if root is None:
        return None
    expected = (
        root.path
        / PACT_SESSIONS_DIRNAME
        / str(slug)
        / str(session_id)
        / JOURNAL_FILENAME
    )
    try:
        resolved_root = root.path.resolve()
        resolved = expected.resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    try:
        if not resolved.is_file():
            return None
    except OSError:
        return None
    return SessionRef(
        root=root,
        slug=str(slug),
        session_id=str(session_id),
        journal=resolved,
    )


def iter_unique_roots(roots: Iterable[RootSpec]) -> list[RootSpec]:
    """De-duplicate roots by lexical path while preserving order."""
    seen: set[Path] = set()
    out: list[RootSpec] = []
    for root in roots:
        if root.path in seen:
            continue
        seen.add(root.path)
        out.append(root)
    return out
