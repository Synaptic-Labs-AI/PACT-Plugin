from notes_api import ConflictError, NoteNotFoundError, NotesError, ValidationError


def test_domain_errors_share_a_base_class():
    assert issubclass(NoteNotFoundError, NotesError)
    assert issubclass(ValidationError, NotesError)
    assert issubclass(ConflictError, NotesError)


def test_validation_error_is_also_a_value_error():
    assert issubclass(ValidationError, ValueError)


def test_not_found_error_exposes_the_missing_id():
    error = NoteNotFoundError("note-7")

    assert error.note_id == "note-7"
    assert "note-7" in str(error)
