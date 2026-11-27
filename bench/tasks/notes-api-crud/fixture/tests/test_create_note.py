import pytest

from notes_api import (
    ConflictError,
    NoteNotFoundError,
    ValidationError,
    create_note,
    get_note,
    list_notes,
    reset_store,
)


def test_create_note_returns_note_with_expected_fields():
    note = create_note("First note", "hello")

    assert set(note) == {"id", "title", "body", "created_at", "updated_at"}
    assert note["title"] == "First note"
    assert note["body"] == "hello"
    assert isinstance(note["id"], str) and note["id"]
    assert note["created_at"] == note["updated_at"]


def test_create_note_defaults_body_to_empty_string_and_strips_title():
    note = create_note("  Padded title  ")

    assert note["title"] == "Padded title"
    assert note["body"] == ""


def test_get_note_returns_created_note_as_a_copy():
    created = create_note("Alpha", "body")

    fetched = get_note(created["id"])

    assert fetched == created
    assert fetched is not created


def test_get_note_for_unknown_id_raises_not_found():
    with pytest.raises(NoteNotFoundError):
        get_note("note-does-not-exist")


def test_list_notes_returns_notes_in_creation_order():
    first = create_note("Alpha")
    second = create_note("Beta", "two")

    notes = list_notes()

    assert [note["id"] for note in notes] == [first["id"], second["id"]]
    assert notes == [first, second]


def test_create_note_rejects_blank_or_non_string_title():
    for bad_title in ("", "   ", None, 42):
        with pytest.raises(ValidationError):
            create_note(bad_title)  # type: ignore[arg-type]


def test_create_note_rejects_non_string_body():
    with pytest.raises(ValidationError):
        create_note("Title", 42)  # type: ignore[arg-type]


def test_create_note_rejects_duplicate_title():
    create_note("Unique")

    with pytest.raises(ConflictError):
        create_note("  Unique  ")

    assert len(list_notes()) == 1


def test_reset_store_clears_notes():
    create_note("Temporary")

    reset_store()

    assert list_notes() == []
