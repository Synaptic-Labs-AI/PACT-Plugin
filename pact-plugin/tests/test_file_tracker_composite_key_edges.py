"""file_tracker composite-key — adversarial / backward-compat edges (#878 NEW-1).

The coder's test_file_tracker.py::TestFileTrackerCompositeKey covers the plan's
P1 matrix (same-type distinct-session detection, label disambiguation,
same-instance no-false-positive, in-process shared-session detection, session_id
persistence, single-editor-no-disambiguation). This file is the adversarial
supplement: the edges that matrix does not exercise —

  * backward-compat: a LEGACY entry written before the composite key existed
    (no ``session_id`` field) behaves as session_id="" and does not corrupt
    detection,
  * the empty-``agent_name`` early-return (a frame whose resolve_agent_name
    yielded "" must short-circuit to no-conflict),
  * a mixed legacy/new shape where the same name appears once WITH and once
    WITHOUT a session_id (two distinct composite keys → a real conflict).
"""

import json



class TestCompositeKeyBackwardCompat:
    """Entries predating the session_id field default to session_id="" and
    must not break detection."""

    def test_legacy_entry_no_session_field_treated_as_empty_session(self, tmp_path):
        """A tracking entry written WITHOUT a session_id key (legacy shape) is
        read as session_id="" — a new instance of the SAME name with a real
        session is a DIFFERENT composite → conflict detected."""
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        abs_path = str(tmp_path / "src" / "auth.ts")
        # Hand-write a legacy entry: no "session_id" key at all.
        legacy = [{"file": __import__("os").path.realpath(abs_path),
                   "agent": "backend-coder", "tool": "Edit", "ts": 1}]
        tracking_file.write_text(json.dumps(legacy))

        conflict = check_conflict(
            abs_path, "backend-coder", str(tracking_file), session_id="sess-new"
        )
        assert conflict is not None, (
            "a legacy (session-less) entry must still count as a distinct "
            "editor instance vs a new session — conflict detected"
        )
        assert "backend-coder" in conflict

    def test_same_name_legacy_and_self_session_less_is_self(self, tmp_path):
        """A legacy entry (session-less) and a current frame that ALSO has no
        session_id share the SAME composite ('backend-coder', '') → it is the
        SAME instance → NO false-positive conflict."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")
        # track_edit with the default empty session_id (legacy-equivalent).
        track_edit(abs_path, "backend-coder", "Edit", tracking_file)
        conflict = check_conflict(abs_path, "backend-coder", tracking_file)
        assert conflict is None, (
            "same name + same (empty) session = same instance → no conflict"
        )

    def test_same_name_one_with_one_without_session_conflicts(self, tmp_path):
        """The same agent_name appearing once WITH a session and once WITHOUT
        are two distinct composite keys → a real cross-instance conflict."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")
        # Instance A: legacy (no session).
        track_edit(abs_path, "backend-coder", "Edit", tracking_file)
        # Instance B: same name, real session → distinct composite → checks.
        conflict = check_conflict(
            abs_path, "backend-coder", tracking_file, session_id="sess-bbbb"
        )
        assert conflict is not None
        assert "backend-coder" in conflict


class TestEmptyAgentNameShortCircuit:
    """An empty agent_name (resolve_agent_name yielded "") short-circuits
    check_conflict to None — no attribution is possible without a name."""

    def test_empty_agent_name_returns_none(self, tmp_path):
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")
        # Another instance has edited the file.
        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="s1")
        # The checking frame has no resolvable name.
        conflict = check_conflict(abs_path, "", tracking_file, session_id="s2")
        assert conflict is None, (
            "empty agent_name must short-circuit to None (cannot attribute)"
        )
