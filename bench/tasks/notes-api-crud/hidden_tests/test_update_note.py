"""Black-box checks for the update_note contract described in prompt.md."""

import pytest

import notes_api
from notes_api import (
    ConflictError,
    NoteNotFoundError,
    ValidationError,
    create_note,
    get_note,
    list_notes,
)


def test_update_title_returns_the_updated_note():
    created = create_note("Original title", "original body")

    updated = notes_api.update_note(created["id"], title="New title")

    assert set(updated) == {"id", "title", "body", "created_at", "updated_at"}
    assert updated["id"] == created["id"]
    assert updated["title"] == "New title"
    assert updated["body"] == "original body"
    assert get_note(created["id"]) == updated


def test_update_body_only_keeps_title():
    created = create_note("Keep me", "old body")

    updated = notes_api.update_note(created["id"], body="new body")

    assert updated["title"] == "Keep me"
    assert updated["body"] == "new body"


def test_update_both_title_and_body():
    created = create_note("Old", "old body")

    updated = notes_api.update_note(created["id"], title="New", body="new body")

    assert updated["title"] == "New"
    assert updated["body"] == "new body"


def test_update_does_not_change_note_count_or_identity():
    first = create_note("First")
    second = create_note("Second")

    notes_api.update_note(second["id"], body="changed")

    notes = list_notes()
    assert [note["id"] for note in notes] == [first["id"], second["id"]]
    assert notes[1]["body"] == "changed"


def test_update_strips_surrounding_whitespace_from_title():
    created = create_note("Spaced")

    updated = notes_api.update_note(created["id"], title="  Trimmed  ")

    assert updated["title"] == "Trimmed"
    assert get_note(created["id"])["title"] == "Trimmed"


def test_update_keeps_created_at_and_id():
    created = create_note("Stable")

    updated = notes_api.update_note(created["id"], title="Stable renamed")

    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]


def test_update_unknown_id_raises_not_found():
    with pytest.raises(NoteNotFoundError):
        notes_api.update_note("note-missing", title="Nope")


def test_update_unknown_id_with_body_raises_not_found():
    with pytest.raises(NoteNotFoundError):
        notes_api.update_note("note-missing", body="Nope")


def test_update_without_any_field_raises_validation_error():
    created = create_note("Unchanged")

    with pytest.raises(ValidationError):
        notes_api.update_note(created["id"])

    assert get_note(created["id"]) == created


@pytest.mark.parametrize("bad_title", ["", "   ", 42, ["list"]])
def test_update_rejects_invalid_title(bad_title):
    created = create_note("Valid title")

    with pytest.raises(ValidationError):
        notes_api.update_note(created["id"], title=bad_title)

    assert get_note(created["id"]) == created


@pytest.mark.parametrize("bad_body", [42, b"bytes", ["list"]])
def test_update_rejects_non_string_body(bad_body):
    created = create_note("Valid title", "valid body")

    with pytest.raises(ValidationError):
        notes_api.update_note(created["id"], body=bad_body)

    assert get_note(created["id"]) == created


def test_update_can_clear_body_with_empty_string():
    created = create_note("Has body", "text")

    updated = notes_api.update_note(created["id"], body="")

    assert updated["body"] == ""
    assert get_note(created["id"])["body"] == ""


def test_update_rejects_title_already_used_by_another_note():
    first = create_note("First title")
    second = create_note("Second title", "second body")

    with pytest.raises(ConflictError):
        notes_api.update_note(second["id"], title="First title")

    assert get_note(first["id"]) == first
    assert get_note(second["id"]) == second


def test_update_may_keep_the_notes_own_title():
    created = create_note("Same title", "body")

    updated = notes_api.update_note(created["id"], title="Same title")

    assert updated["title"] == "Same title"
    assert updated["body"] == "body"
