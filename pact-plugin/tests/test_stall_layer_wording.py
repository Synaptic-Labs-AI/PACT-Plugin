"""Pins for the stall layers' wording about whether a job is still running.

Location: pact-plugin/tests/test_stall_layer_wording.py
Summary: the launch advisory says nothing wakes a teammate only as an in-process
         teammate, and the Layer 2 advisory and Layer 3 surface no longer call a
         launched job "outstanding", since it may already have finished. Every
         pinned sentence is true whether or not the separate-process split is in.
Used by: the suite. Path setup is conftest-owned.

Both arms are REVERT PROOFS: each fails against the texts as they were before.
"""

from __future__ import annotations


def test_the_launch_advisory_scopes_nothing_wakes_you_to_in_process_mode():
    import wait_filler_gate

    text = wait_filler_gate._BACKGROUND_ADVISORY
    assert "As an in-process teammate, NOTHING WILL WAKE YOU when it finishes" in text
    assert "default" not in text.lower(), (
        "in-process is not the default teammate mode everywhere; scope by role"
    )


def test_the_unflagged_texts_do_not_call_a_finished_job_outstanding():
    import missed_wake_scan as mw
    import teammate_idle

    advisory = teammate_idle.UNFLAGGED_ADVISORY
    assert "You launched background work and have no flagged wait; it may already have finished." in advisory
    assert "outstanding" not in advisory

    surface = mw.build_unflagged_surface([{"agent_name": "inproc-coder", "task_ids": ["2"]}])
    assert "these teammates launched background work and have no flagged wait:" in surface
    assert "The job may already have finished." in surface
    assert "outstanding" not in surface
