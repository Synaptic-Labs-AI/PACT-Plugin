"""Domain errors raised by the notes service."""


class NotesError(Exception):
    """Base class for every notes-service error."""


class NoteNotFoundError(NotesError):
    """Raised when a note id does not exist."""

    def __init__(self, note_id: str) -> None:
        super().__init__(f"note not found: {note_id}")
        self.note_id = note_id


class ValidationError(NotesError, ValueError):
    """Raised when an argument does not satisfy the service contract."""


class ConflictError(NotesError):
    """Raised when a change conflicts with data that already exists."""
