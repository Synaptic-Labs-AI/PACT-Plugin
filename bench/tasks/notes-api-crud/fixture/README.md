# notes-api

A small in-memory notes service. Python 3.12, standard library only.

## Layout

- `notes_api/` - the service package (`store` is process-local memory).
- `tests/` - pytest suite covering the current public API.

## Run the tests

    python -m pytest -q

## Public API

`notes_api` exposes:

- `create_note(title, body="") -> dict`
- `get_note(note_id) -> dict`
- `list_notes() -> list[dict]`
- `reset_store() -> None` (test helper: forgets every note and resets id generation)
- errors: `NotesError`, `NoteNotFoundError`, `ValidationError`, `ConflictError`

A note is a plain dict with keys `id`, `title`, `body`, `created_at`,
`updated_at`. Notes are addressed by the `id` returned from `create_note`.
