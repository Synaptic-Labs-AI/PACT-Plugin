# Add update and delete to the notes API

You are working on `notes-api`, a small in-memory notes service written for
Python 3.12 using only the standard library. The package `notes_api` currently
exposes:

- `create_note(title, body="")` -> note dict
- `get_note(note_id)` -> note dict
- `list_notes()` -> list of note dicts
- `reset_store()` (test helper)
- domain errors `NotesError`, `NoteNotFoundError`, `ValidationError`,
  `ConflictError`

A note is a dict with keys `id`, `title`, `body`, `created_at`,
`updated_at`. Titles are unique among notes: `create_note` strips surrounding
whitespace and raises `ConflictError` for a title that is already in use.
The seed pytest suite under `tests/` passes today.

Users can create, read, and list notes but cannot edit or remove them. Add the
two missing operations to the public API (importable as `notes_api.update_note`
and `notes_api.delete_note`) and keep all existing behavior unchanged.

## Required behavior

### `update_note(note_id, *, title=None, body=None) -> dict`

- Returns the updated note dict with the same five keys. `id` and
  `created_at` never change; `updated_at` is refreshed.
- Only the supplied fields change. `title=None` and `body=None` mean "leave
  this field unchanged".
- Calling it with neither a title nor a body (e.g. `update_note(note_id)`)
  must raise `ValidationError`.
- `title` follows the same rules as `create_note`: it must be a string,
  surrounding whitespace is stripped, and an empty or whitespace-only title
  raises `ValidationError`.
- `body` must be a string; an empty string is allowed and clears the body.
  Non-string bodies raise `ValidationError`.
- An unknown `note_id` raises `NoteNotFoundError`.
- Renaming a note to a title owned by another note raises `ConflictError`.
  Re-sending a note's own current title is not a conflict.
- When an update raises, the stored notes must be left exactly as they were.

### `delete_note(note_id) -> dict`

- Removes the note and returns a copy of the removed note.
- An unknown or already-deleted `note_id` raises `NoteNotFoundError`.
- After a delete, `get_note` and `update_note` raise `NoteNotFoundError` for
  that id, `list_notes` no longer contains it, and its title can be reused by
  a later `create_note`.

Error messages should be descriptive, but their wording is not part of the
contract. Use the existing error classes; do not add new exception types.

## Constraints

- Python 3.12 standard library only. No new dependencies.
- Do not change existing public functions, signatures, return shapes, or error
  classes. The seed tests must keep passing.
- Add pytest tests under `tests/` for the new behavior and run them with
  `python -m pytest -q`.

## Delivery

Work autonomously; do not ask questions. Commit your work on a feature branch;
do not push, open a PR, or merge.
