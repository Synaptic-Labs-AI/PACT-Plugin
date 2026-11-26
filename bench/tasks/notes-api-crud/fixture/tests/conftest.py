import pytest

from notes_api import reset_store


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()
