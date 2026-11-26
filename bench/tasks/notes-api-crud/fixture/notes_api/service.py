"""In-memory notes service.

Notes live in a process-local dict for the lifetime of the interpreter.  Every
public function returns copies so callers cannot mutate stored state by
accident.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any

from .errors import ConflictError, NoteNotFoundError, ValidationError

Note = dict[str, Any]

_notes: dict[str, Note] = {}
_id_counter = itertools.count(1)


def reset_store() -> None:
    """Remove every stored note and start id generation over. Tests use this."""
    global _id_counter
    _notes.clear()
    _id_counter = itertools.count(1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_title(title: Any) -> str:
    if not isinstance(title, str):
        raise ValidationError("title must be a string")
    title = title.strip()
    if not title:
        raise ValidationError("title must not be blank")
    return title


def _validate_body(body: Any) -> str:
    if not isinstance(body, str):
        raise ValidationError("body must be a string")
    return body


def _title_taken(title: str) -> bool:
    return any(note["title"] == title for note in _notes.values())


def create_note(title: str, body: str = "") -> Note:
    """Create and store a note, returning a copy of the stored note."""
    title = _validate_title(title)
    body = _validate_body(body)
    if _title_taken(title):
        raise ConflictError(f"a note titled {title!r} already exists")
    stamp = _now()
    note: Note = {
        "id": f"note-{next(_id_counter)}",
        "title": title,
        "body": body,
        "created_at": stamp,
        "updated_at": stamp,
    }
    _notes[note["id"]] = note
    return dict(note)


def get_note(note_id: str) -> Note:
    """Return a copy of the note with *note_id*."""
    try:
        note = _notes[note_id]
    except KeyError:
        raise NoteNotFoundError(note_id) from None
    return dict(note)


def list_notes() -> list[Note]:
    """Return copies of every note in creation order."""
    return [dict(note) for note in _notes.values()]
