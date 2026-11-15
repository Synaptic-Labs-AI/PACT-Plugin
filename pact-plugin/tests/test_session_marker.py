"""
Location: pact-plugin/tests/test_session_marker.py
Summary: Pins that fixtures/session_marker.py names the same embedding marker
         path that memory_init composes, so the fixture's cleanup removes the
         file memory_init writes.
Used by: the suite.
"""
import uuid
from unittest.mock import patch

from fixtures.session_marker import embedding_marker


def test_the_fixture_names_the_marker_memory_init_writes():
    from scripts.memory_init import _get_embedding_attempted_path

    # Only [0-9a-z-], as the fixture's callers build, so the sanitiser keeps it.
    session_id = f"marker-pin-{uuid.uuid4().hex}"
    with patch("scripts.memory_init.get_session_id_from_context_file",
               return_value=session_id):
        assert _get_embedding_attempted_path() == embedding_marker(session_id)
