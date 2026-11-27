"""Black-box checks for the delete_note contract described in prompt.md."""

import pytest

import notes_api
from notes_api import (
    NoteNotFoundError,
    create_note,
    get_note,
    list_notes,
)


def test_delete_returns_the_removed_note():
    created = create_note("Delete me", "body")

    deleted = notes_api.delete_note(created["id"])

    assert deleted == created


def test_delete_removes_the_note_from_get_and_list():
    created = create_note("Delete me")
    notes_api.delete_note(created["id"])

    with pytest.raises(NoteNotFoundError):
        get_note(created["id"])
    assert list_notes() == []


def test_delete_unknown_id_raises_not_found():
    with pytest.raises(NoteNotFoundError):
        notes_api.delete_note("note-missing")


def test_delete_twice_raises_not_found():
    created = create_note("Once only")

    notes_api.delete_note(created["id"])

    with pytest.raises(NoteNotFoundError):
        notes_api.delete_note(created["id"])


def test_delete_leaves_other_notes_untouched():
    first = create_note("First")
    second = create_note("Second")

    notes_api.delete_note(first["id"])

    assert get_note(second["id"]) == second
    assert [note["id"] for note in list_notes()] == [second["id"]]


def test_delete_frees_the_title_for_reuse():
    created = create_note("Reusable title")

    notes_api.delete_note(created["id"])

    replacement = create_note("Reusable title")
    assert replacement["id"] != created["id"]
    assert get_note(replacement["id"]) == replacement


def test_update_after_delete_raises_not_found():
    created = create_note("Gone soon")

    notes_api.delete_note(created["id"])

    with pytest.raises(NoteNotFoundError):
        notes_api.update_note(created["id"], body="too late")
