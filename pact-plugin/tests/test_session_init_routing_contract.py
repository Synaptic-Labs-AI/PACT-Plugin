"""The routing word is a machine contract between each status status source and
the channel selector in ``session_init.main()``.

THE CONTRACT. ``main()`` selects an output channel by a literal substring of a
human-readable status string:

    if "failed" in <msg>.lower() or "skipped" in <msg>.lower():
        system_messages.append(<msg>)      # the user-visible error surface
    else:
        context_parts.append(<msg>)        # ordinary context

So the word ``failed`` (and at four of the five sites the word ``skipped``) is
the ROUTING KEY, and that key is a word inside prose that a person reads. A
status source that rewords its failure return keeps the signal for a person and
DOWNGRADES THE ROUTING WITH NO SIGNAL. Nothing goes red, because no test in this
repository asserted which channel a status string reaches.

THE FAMILY IS SEVEN SITES IN ``hooks/session_init.py``. Six are covered here:

    line 991   setup_plugin_symlinks()         shared/symlinks.py
    line 1005  ensure_project_memory_md()      shared/claude_md_manager.py
    line 1018  migrate_to_managed_structure()  shared/claude_md_manager.py
    line 1032  strip_orphan_kernel_block()     shared/claude_md_manager.py
    line 1060  check_pinned_staleness()        staleness.py
    line 1601  check_resumption_context()      shared/session_resume.py

The seventh site consumes ``update_session_info()`` from
``shared/session_resume.py`` and is covered by the arms in
``tests/test_session_resume.py``.

SITE 1601 ROUTES ON A DIFFERENT LITERAL, ``"**Blockers:"``, so the word-based
sweep that found the other six could not see it. THE FAMILY IS NOT "the
failed-or-skipped predicate". It is ROUTING KEYED ON A SUBSTRING OF PROSE, and
the word is one spelling of that idea.

HOW THE SEVEN WERE MEASURED, so a later reader can judge the number rather
than inherit it. A word search finds the spelling it is given, and cannot find
a spelling nobody thought of. So the count comes from an enumeration that reads
the OPERATOR: parse the module with ``ast``, take each ``Compare`` that uses
``In`` with a string constant on the left, and read the result. Across the whole
``hooks/`` tree that returns these seven routing sites and two non-members,
which are a ``name@team`` parse and a code-fence detector. NO EIGHTH.
The rule does not cover a key held in a named constant, a key built at run
time, or a consumer outside ``hooks/``.

THREE PROPERTIES OF THE FAMILY, MEASURED RATHER THAN ASSUMED.

1. NONE OF THE FIRST FIVE SITES CARRIES A ROLE GATE OR A SOURCE GATE. Each sits
   at ``def main()`` then ``try:`` then ``if <msg>:``. The only condition
   before the routing predicate is that the status source returned a truthy string, so
   the live population of these five is each SessionStart frame. The seventh
   site is different, and its narrower population is stated in its own suite.
2. THE ORDINARY-CONTEXT BRANCH IS NOT ALWAYS UNGATED. Site 991 sends the
   no-change message to context only when the frame is not a context reset, and
   site 1060 sends its advisory to context only for a non-teammate frame. The
   control tests below thus choose their frame on purpose. See
   ``_route`` and the comment on each control.
3. SITE 1601 NEEDS TWO GATE CONDITIONS TOGETHER, unlike the other six: ``if
   tasks:`` and then ``if resumption_msg:``. So its fixture supplies a task
   list, and its class says which task shape reaches which branch.

WHY THE TESTS DO NOT ASSERT THE MESSAGE TEXT. An arm that pins the words of a
status message reddens each time a contributor improves that message, which is
an over-block. The next contributor an over-block obstructs removes the arm
rather than repairs it, and the protection is then gone. So each test CALLS THE
STATUS SOURCE, GETS WHATEVER STRING IT RETURNS, and asserts only which of the two
channels that string reaches. A reword that keeps the routing word changes the
captured string and changes no assertion. A reword that loses the routing word
moves the string to the other channel and reddens the test.

THE ONE CHANGE THESE ARMS OBSTRUCT is a decision to route a status source failure
into ordinary context. That is a design decision and not an accident, and this
docstring is the statement of what the arm protects, so a contributor who meets
it can repair it on purpose.

========================= ANTI-MOCK INVARIANT ==============================
DO NOT PATCH THE STATUS SOURCE THAT THE TEST MEASURES. ``_route`` takes the name of the
one collaborator to leave live and patches the others. A test that patched its
own status source exercises the consumer branch alone: it cannot fire on a
reword of the status source, which is the only mutation these arms exist to catch.
The failure and the ordinary states are built from real filesystem conditions
(a read-only directory, an orphan marker, a stale pin), not from an injected
exception, so each arm below is a PRODUCT arm and not a harness arm.
============================================================================

======================== NON-VACUITY (source mutation) =====================
Each test names its own mutant in its docstring. The shape is the same for the
five: reword the failure or skip return of the status source so the routing word is
lost while the message stays informative, then run this file.
EXPECTED: the routing test for that status source fails, and the ordinary-context
test for the same status source stays green.
THE PAIRED CONTROL is the second half and it is not optional: change the same
message in a form that KEEPS the routing word and the routing test MUST stay
green, which is what proves these arms are not an over-block.
============================================================================
"""

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


_SESSION_ID = "ccdd3344-0000-0000-0000-000000000000"

# The five status sources this file covers, as they are bound in the session_init
# module namespace. ``_route`` patches each name in this set, but not the one
# left live, so one live status source runs for each measurement.
_STATUS_SOURCES = (
    "setup_plugin_symlinks",
    "ensure_project_memory_md",
    "migrate_to_managed_structure",
    "strip_orphan_kernel_block",
    "check_pinned_staleness",
)

# A stale pin, so check_pinned_staleness has something to report and reaches
# its write path. The date is from 2020, so it is stale for each plausible
# staleness window.
_STALE_PIN_DOC = (
    "# PACT Framework and Managed Project Memory\n"
    "\n"
    "## Pinned Context\n"
    "\n"
    "### An outdated pin 2020-01-05\n"
    "\n"
    "body text\n"
    "\n"
    "## Working Memory\n"
)


def _route(tmp_home, project_dir, live, *, agent_type="PACT:pact-orchestrator",
           env=None, tasks=None):
    """Drive the live ``session_init.main()`` and return the two channels.

    ``live`` names the ONE status source left unpatched. ``env`` carries any extra
    environment the live status source reads, so that the status source sees the SAME
    state inside ``main()`` that it saw when the test captured its message.
    ``tasks`` is what ``get_task_list`` returns. IT IS THE INPUT TO
    ``check_resumption_context``, NOT THAT STATUS SOURCE. The status source
    stays unpatched and builds its own message from these tasks, so a reword of
    that message is visible to the arm.
    ``agent_type`` defaults to a lead spelling: every status source routed here
    runs on the lead path, and a frame with no agent_type returns before most
    of them.
    Returns the pair ``(system_message, additional_context)``, with an absent
    channel reported as the empty string, because ``main()`` omits a channel it
    has nothing for.

    THE FAILURE DIRECTION OF THIS HELPER IS TO RED, WHICH IS WHY ONE
    HELPER BEHIND ALL 14 TESTS IN THIS FILE IS ACCEPTABLE HERE. If it returns two empty
    strings, each routing assertion below fails, because a captured message is
    not empty. If it interchanges the two channels, the routing test and the
    ordinary-context test for the same status source disagree, and the two fail
    together. If it patched the measured status source by mistake, the
    independently captured message and the routed string diverge, and the
    routing assertion fails. It cannot fail to green.

    session_init is imported inside the function, matching the sibling suites:
    a collection-time import of that module pulls staleness, pin_caps and
    claude_md_manager into every test in this file.
    """
    import session_init

    payload = {"source": "startup", "session_id": _SESSION_ID}
    if agent_type is not None:
        payload["agent_type"] = agent_type

    suppressed = [
        patch(f"session_init.{name}", return_value=None)
        for name in _STATUS_SOURCES
        if name != live
    ]
    # Collaborators that are not part of this contract. They are suppressed so
    # the measurement does not depend on task state, journal writes or disk
    # persistence. None of them contributes a status string to either channel.
    suppressed += [
        patch("session_init.get_task_list", return_value=tasks),
        patch("session_init.restore_last_session", return_value=None),
        patch("session_init.update_session_info", return_value=None),
        patch("session_init.check_resume_state", return_value=None),
        patch("session_init.append_event"),
        patch("session_init.persist_context", return_value=None),
        patch("session_init.build_context_cache",
              return_value=(Path("/tmp/ctx.json"), {})),
    ]

    with patch.object(Path, "home", staticmethod(lambda: tmp_home)), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        for p in suppressed:
            p.start()
        try:
            overrides = {"CLAUDE_PROJECT_DIR": str(project_dir)}
            overrides.update(env or {})
            with patch.dict(os.environ, overrides, clear=False):
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
                with pytest.raises(SystemExit) as exc:
                    session_init.main()
        finally:
            for p in reversed(suppressed):
                p.stop()

    assert exc.value.code == 0
    emitted = json.loads(out.getvalue())
    hook_out = emitted.get("hookSpecificOutput") or {}
    return emitted.get("systemMessage", ""), hook_out.get("additionalContext", "")


def _assert_routes_to_system_messages(message, system_message, context):
    """The shared oracle for a status source failure or skip return.

    It asserts the message is non-empty first. An empty string is a substring
    of every string, so the two membership tests below would hold for a cause
    that has nothing to do with routing.
    """
    assert message, (
        "the fixture did not drive the status source to a failure or skip return, "
        "so this measurement has no subject"
    )
    assert message in system_message, (
        "a status source failure or skip status must reach systemMessage. It did "
        "not, which means the routing word is absent from the message that "
        f"the status source returned: {message!r}"
    )
    assert message not in context, (
        "a status source failure or skip status must NOT also reach ordinary "
        "context"
    )


def _assert_routes_to_context(message, system_message, context):
    """The shared oracle for an ordinary status source return.

    This is the discrimination control. Without it the routing oracle above
    cannot tell correct routing apart from a channel that receives each string.
    """
    assert message, (
        "the fixture did not drive the status source to an ordinary return, so "
        "this measurement has no subject"
    )
    assert message in context, (
        "an ordinary status source status must reach additionalContext"
    )
    assert message not in system_message, (
        "an ordinary status source status must NOT reach systemMessage. It did, "
        "so the routing word is present in a message that reports no "
        f"failure: {message!r}"
    )


@pytest.fixture
def home(tmp_path):
    """A redirected home with the config directory in place."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


@pytest.fixture
def project(tmp_path):
    """An empty project directory, writable."""
    p = tmp_path / "project"
    p.mkdir()
    return p


class TestSymlinkStatusRouting:
    """Site 991, status source ``setup_plugin_symlinks``.

    THIS SITE IS NOT LIKE THE OTHER FOUR. It reads ``"failed"`` alone and has
    no ``"skipped"`` arm, so a skip-worded status from this status source routes to
    ordinary context today. The arm below pins the word the site reads.

    MUTANT: in ``hooks/shared/symlinks.py``, change the agents-loop status
    ``f"{agents_failed} agents failed"`` to ``f"{agents_failed} agents could
    not be linked"``. EXPECTED: the routing test fails and the linked test
    stays green.
    CONTROL MUTANT: change the same status to ``f"{agents_failed} agents
    failed to link"``. EXPECTED: both tests stay green, because the routing
    word survives.
    """

    @staticmethod
    def _plugin_root(base, agents_mode):
        plugin_root = base / "plugin"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "agents" / "pact-example.md").write_text("body")
        return plugin_root

    def test_failure_status_reaches_system_messages(self, tmp_path, home, project):
        plugin_root = self._plugin_root(tmp_path, None)
        agents_dst = home / ".claude" / "agents"
        agents_dst.mkdir()
        os.chmod(agents_dst, 0o500)  # the link write fails, the loop counts it
        try:
            with patch.dict(os.environ,
                            {"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
                            clear=False), \
                 patch.object(Path, "home", staticmethod(lambda: home)):
                from shared.symlinks import setup_plugin_symlinks
                message = setup_plugin_symlinks()

            system_message, context = _route(
                home, project, live="setup_plugin_symlinks",
                env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)})
        finally:
            os.chmod(agents_dst, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(
        self, tmp_path, home, project
    ):
        """The control. The frame is a launch source and not a context reset,
        because the no-change status of this status source reaches context only for
        a non-reset frame. A newly linked status takes the third branch, which
        carries no gate, and the launch source keeps the control correct if the
        fixture ever moves to the no-change status.
        """
        plugin_root = self._plugin_root(tmp_path, None)
        with patch.dict(os.environ,
                        {"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
                        clear=False), \
             patch.object(Path, "home", staticmethod(lambda: home)):
            from shared.symlinks import setup_plugin_symlinks
            message = setup_plugin_symlinks()

        # The status source is idempotent: the second call inside main() finds the
        # links in place. Make the destination again so main() performs the same
        # first-time link and returns the same status.
        for stale in (home / ".claude" / "agents").glob("pact-*.md"):
            stale.unlink()

        system_message, context = _route(
            home, project, live="setup_plugin_symlinks",
            env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)})

        _assert_routes_to_context(message, system_message, context)


class TestProjectMemoryStatusRouting:
    """Site 1005, status source ``ensure_project_memory_md``.

    THIS SITE AND SITE 1018 CANNOT SHARE ONE FIXTURE. This status source
    returns None unless the project CLAUDE.md is ABSENT, and the migration
    status source returns None when it is absent. The two states are not compatible,
    so each site builds its own project directory.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the outer handler
    status ``f"Project CLAUDE.md failed: {str(e)[:50]}"`` to
    ``f"Project CLAUDE.md not written: {str(e)[:50]}"``. EXPECTED: the routing
    test fails and the created test stays green.
    CONTROL MUTANT: change it to ``f"Project CLAUDE.md write failed: ..."``.
    EXPECTED: the two tests stay green.
    """

    def test_failure_status_reaches_system_messages(self, home, project):
        # MEASURED: the resolver returns `<project>/.claude/CLAUDE.md` here, so
        # the step that fails in a sealed project is `ensure_dot_claude_parent`
        # creating `.claude`, NOT lock acquisition. Its OSError reaches the
        # OUTER handler either way, which is what this arm measures.
        os.chmod(project, 0o500)
        try:
            from shared.claude_md_manager import ensure_project_memory_md
            with patch.dict(os.environ,
                            {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
                message = ensure_project_memory_md()

            system_message, context = _route(
                home, project, live="ensure_project_memory_md")
        finally:
            os.chmod(project, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(self, home, project):
        """The control. This site sends its ordinary status through a plain
        else branch, so no frame condition applies.
        """
        from shared.claude_md_manager import ensure_project_memory_md
        with patch.dict(os.environ,
                        {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
            message = ensure_project_memory_md()

        # The status source is a one-time creation. Remove what the capture wrote so
        # main() performs the same creation and returns the same status.
        for made in project.rglob("CLAUDE.md"):
            made.unlink()

        system_message, context = _route(
            home, project, live="ensure_project_memory_md")

        _assert_routes_to_context(message, system_message, context)


class TestMigrationStatusRouting:
    """Site 1018, status source ``migrate_to_managed_structure``.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, find the lock-failure
    status of this status source, the one that ends with the words ``migration
    skipped``. Change that ending to ``migration did not run`` so the routing
    word is lost. EXPECTED: the routing test fails and the migrated test stays
    green.
    CONTROL MUTANT: reword the first half and keep the final clause. EXPECTED:
    both tests stay green.
    """

    UNMIGRATED = (
        "# Project Memory\n"
        "\n"
        "## Retrieved Context\n"
        "\n"
        "## Working Memory\n"
    )

    def test_failure_status_reaches_system_messages(self, home, project):
        (project / "CLAUDE.md").write_text(self.UNMIGRATED)
        os.chmod(project, 0o500)  # the lock sidecar cannot be written
        try:
            from shared.claude_md_manager import migrate_to_managed_structure
            with patch.dict(os.environ,
                            {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
                message = migrate_to_managed_structure()

            system_message, context = _route(
                home, project, live="migrate_to_managed_structure")
        finally:
            os.chmod(project, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(self, home, project):
        """The control. This site sends its ordinary status through a plain
        else branch, so no frame condition applies.
        """
        (project / "CLAUDE.md").write_text(self.UNMIGRATED)
        from shared.claude_md_manager import migrate_to_managed_structure
        with patch.dict(os.environ,
                        {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
            message = migrate_to_managed_structure()

        # The migration is idempotent. Restore the unmigrated document so
        # main() performs the same migration and returns the same status.
        (project / "CLAUDE.md").write_text(self.UNMIGRATED)

        system_message, context = _route(
            home, project, live="migrate_to_managed_structure")

        _assert_routes_to_context(message, system_message, context)


class TestKernelStripStatusRouting:
    """Site 1032, status source ``strip_orphan_kernel_block``.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the orphan-marker
    status prefix ``f"Migration skipped: {target_file} contains "`` to
    ``f"Migration not performed: {target_file} contains "``. EXPECTED: the
    routing test fails and the removed test stays green.
    CONTROL MUTANT: change the same status to ``"Migration skipped for
    safety: ..."``. EXPECTED: the two tests stay green.
    """

    ORPHAN = "# Home\n\n<!-- PACT_START:v3 -->\nkernel body\n"
    MARKER_PAIR = (
        "# Home\n"
        "\n"
        "<!-- PACT_START:v3 -->\n"
        "kernel body\n"
        "<!-- PACT_END -->\n"
        "\n"
        "user content\n"
    )

    def test_skip_status_reaches_system_messages(self, home, project):
        (home / ".claude" / "CLAUDE.md").write_text(self.ORPHAN)
        from shared.claude_md_manager import strip_orphan_kernel_block
        with patch.object(Path, "home", staticmethod(lambda: home)):
            message = strip_orphan_kernel_block()

        system_message, context = _route(
            home, project, live="strip_orphan_kernel_block")

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(self, home, project):
        """The control. This site sends its ordinary status through a plain
        else branch, so no frame condition applies.
        """
        target = home / ".claude" / "CLAUDE.md"
        target.write_text(self.MARKER_PAIR)
        from shared.claude_md_manager import strip_orphan_kernel_block
        with patch.object(Path, "home", staticmethod(lambda: home)):
            message = strip_orphan_kernel_block()

        # The strip is a one-time removal. Restore the block so main() performs
        # the same removal and returns the same status.
        target.write_text(self.MARKER_PAIR)

        system_message, context = _route(
            home, project, live="strip_orphan_kernel_block")

        _assert_routes_to_context(message, system_message, context)


class TestPinnedStalenessStatusRouting:
    """Site 1060, status source ``check_pinned_staleness``.

    MUTANT: in ``hooks/staleness.py``, change the write-failure status
    ``f"Failed to update pinned staleness: {str(e)[:50]}"`` to
    ``f"Could not update pinned staleness: {str(e)[:50]}"``. EXPECTED: the
    routing test fails and the detected test stays green.
    CONTROL MUTANT: change it to ``f"Pinned staleness update failed: ..."``.
    EXPECTED: the two tests stay green.
    """

    def test_failure_status_reaches_system_messages(self, home, project):
        (project / "CLAUDE.md").write_text(_STALE_PIN_DOC)
        os.chmod(project, 0o500)  # the staleness write path cannot lock
        try:
            from staleness import check_pinned_staleness
            message = check_pinned_staleness(
                claude_md_path=project / "CLAUDE.md")

            system_message, context = _route(
                home, project, live="check_pinned_staleness")
        finally:
            os.chmod(project, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(self, home, project):
        """The control. THE FRAME MATTERS AT THIS SITE. The ordinary advisory
        reaches context only for a non-teammate frame, so this test uses a
        frame with no teammate agent type. A teammate frame sends the
        advisory to no channel at all, and the control then passes for a cause
        that has nothing to do with routing.
        """
        target = project / "CLAUDE.md"
        target.write_text(_STALE_PIN_DOC)
        from staleness import check_pinned_staleness
        message = check_pinned_staleness(claude_md_path=target)

        # The pass writes a stale marker into the document. Restore the
        # original so main() performs the same pass and returns the same
        # status.
        target.write_text(_STALE_PIN_DOC)

        system_message, context = _route(
            home, project, live="check_pinned_staleness")

        _assert_routes_to_context(message, system_message, context)


# ===========================================================================
# THE SEVENTH SITE, AND THE TWO SECOND OCCURRENCES.
#
# The five classes above pin ONE exit of each status source. Three exits stay
# open, and this block closes them.
#
# SITE 1601 IS A DIFFERENT LITERAL, NOT A DIFFERENT WORD. It routes on
# ``"**Blockers:"``, so a sweep for ``failed`` and ``skipped`` cannot see it.
# An enumeration that reads the OPERATOR rather than the word does see it, and
# that enumeration returns these seven and no eighth.
#
# THE OTHER TWO ARE SECOND EXITS AT SITES THIS FILE BEFORE THIS COVERS. Each of
# those two status sources emits its status string from TWO handlers, and the
# fixtures above reach one of the two. An arm on a second exit has one hazard
# of its own: the fixture can silently drive the FIRST exit again and the arm
# passes for that. So each of the two carries a SEPARATION assertion, which
# proves the new fixture reaches a different exit from the one above it.
# ===========================================================================


class TestResumptionBlockerStatusRouting:
    """Site 1601, status source ``check_resumption_context``.

    THE GATE CHAIN NEEDS TWO CONDITIONS, unlike the five sites above: ``main()``
    reaches this site only ``if tasks:`` and then ``if resumption_msg:``. So the
    fixture supplies a task list AND that list must make the status source
    report something.

    ``get_task_list`` supplies the INPUT and is patched. ``check_resumption_context``
    is the status source and is NOT patched, so a reword of its message is
    visible to this arm.

    MUTANT: in ``hooks/shared/session_resume.py``, change
    ``f"**Blockers: {len(blocker_tasks)}**"`` to ``f"Blocked: {len(blocker_tasks)}"``.
    EXPECTED: the routing test fails and the ordinary test stays green.
    CONTROL MUTANT: change it to ``f"**Blockers: {len(blocker_tasks)} open**"``.
    EXPECTED: the two tests stay green, because the routing literal survives.
    """

    BLOCKER_TASKS = [{
        "status": "in_progress",
        "subject": "a task that blocks",
        "metadata": {"type": "blocker"},
    }]
    ORDINARY_TASKS = [{
        "status": "in_progress",
        "subject": "ordinary feature work",
        "metadata": {},
    }]

    def test_blocker_status_reaches_system_messages(self, home, project):
        from shared.session_resume import check_resumption_context
        message = check_resumption_context(self.BLOCKER_TASKS)

        system_message, context = _route(
            home, project, live="check_resumption_context",
            tasks=self.BLOCKER_TASKS)

        _assert_routes_to_system_messages(message, system_message, context)

    def test_ordinary_status_reaches_additional_context(self, home, project):
        """The control. This site sends its ordinary status through a plain
        else branch, so no frame condition applies. The task carries no blocker
        metadata, so the status source reports a feature line and no token.
        """
        from shared.session_resume import check_resumption_context
        message = check_resumption_context(self.ORDINARY_TASKS)

        system_message, context = _route(
            home, project, live="check_resumption_context",
            tasks=self.ORDINARY_TASKS)

        _assert_routes_to_context(message, system_message, context)


class TestProjectMemoryInnerHandlerRouting:
    """Site 1005, the SECOND exit of ``ensure_project_memory_md``.

    That status source emits its ``failed`` status from TWO handlers. The outer
    one catches whatever fails before or during lock acquisition, and
    ``TestProjectMemoryStatusRouting`` above drives it with a sealed project
    directory. The INNER one reports a write failure after the lock is held,
    and this class drives it.

    HOW THE FIXTURE SEPARATES THE TWO. The lock sidecar sits in the target's own
    directory. To open an EXISTING file needs write permission on the FILE, not
    on the directory. So a sidecar made BEFORE the directory is sealed lets the
    lock succeed, and the write into the sealed directory then fails. That is a
    real filesystem condition, so this arm measures the shipped code.

    THE TWO EXITS EMIT AN IDENTICAL STRING, AND THAT IS MEASURED, NOT ASSUMED.
    Each one returns ``f"Project CLAUDE.md failed: {failure_cause(e)}"``, and
    the cause token is a function of the exception alone. A sealed directory
    gives ``PermissionError (EACCES)`` at the two exits, so NO ASSERTION ON THE
    MESSAGE CAN SEPARATE THEM. The separation proof is the mutant below, not a
    text comparison: an INNER-ONLY reword reddens this arm and leaves the outer
    arm green, which no fixture driving the outer exit can produce.

    THE CONSEQUENCE FOR COVERAGE, STATED SO IT IS NOT MIS-READ. Because the
    routing predicate reads the string alone, and the two exits build one string
    from one function, the ROUTING behaviour of the two is identical by
    construction. What this arm adds is protection against a LATER edit that
    changes one handler and not the other.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the INNER handler
    status ``f"Project CLAUDE.md failed: {failure_cause(e)}"`` to
    ``f"Project CLAUDE.md not written: {failure_cause(e)}"``. Change the INNER
    one only. EXPECTED: this routing test fails and
    ``TestProjectMemoryStatusRouting`` stays green.
    """

    def test_inner_write_failure_reaches_system_messages(self, home, project):
        # THE TARGET IS `<project>/.claude/CLAUDE.md`, NOT `<project>/CLAUDE.md`.
        # That is measured, and it decides the whole fixture. Seal the `.claude`
        # directory and NOT the project, for three separate reasons:
        #   1. `ensure_dot_claude_parent` runs BEFORE the lock. A sealed project
        #      makes IT fail, and that lands in the OUTER handler.
        #   2. The lock sidecar sits with the target, so it goes in `.claude`.
        #   3. To open an EXISTING file needs write on the FILE, not on its
        #      directory, so a sidecar made first lets the lock succeed.
        # The write into the sealed `.claude` then fails, which is the inner
        # handler. The mutant in this docstring is what proves the reach.
        dot_claude = project / ".claude"
        dot_claude.mkdir()
        (dot_claude / ".CLAUDE.md.lock").touch(mode=0o600)
        os.chmod(dot_claude, 0o500)
        try:
            from shared.claude_md_manager import ensure_project_memory_md
            with patch.dict(os.environ,
                            {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
                message = ensure_project_memory_md()

            system_message, context = _route(
                home, project, live="ensure_project_memory_md")
        finally:
            os.chmod(dot_claude, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)


class TestKernelStripEndBeforeStartRouting:
    """Site 1032, the SECOND exit of ``strip_orphan_kernel_block``.

    That status source emits its ``skipped`` status from TWO branches. The first
    reports one marker with no partner, and ``TestKernelStripStatusRouting``
    above drives it with an orphan START. The second reports a PACT_END that
    sits before its PACT_START, and this class drives it.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the
    END-before-START status so the word ``skipped`` is lost, for example to
    ``"Migration not performed: ~/.claude/CLAUDE.md contains "``. Change that
    branch only, so the orphan-marker arm above stays green. EXPECTED: this
    routing test fails.
    """

    END_BEFORE_START = (
        "# Home\n"
        "\n"
        "<!-- PACT_END -->\n"
        "stray tail\n"
        "<!-- PACT_START:v3 -->\n"
        "kernel body\n"
    )
    ORPHAN_START = "# Home\n\n<!-- PACT_START:v3 -->\nkernel body\n"

    def test_end_before_start_reaches_system_messages(self, home, project):
        from shared.claude_md_manager import strip_orphan_kernel_block
        target = home / ".claude" / "CLAUDE.md"

        target.write_text(self.ORPHAN_START)
        with patch.object(Path, "home", staticmethod(lambda: home)):
            orphan_message = strip_orphan_kernel_block()

        target.write_text(self.END_BEFORE_START)
        with patch.object(Path, "home", staticmethod(lambda: home)):
            message = strip_orphan_kernel_block()

        system_message, context = _route(
            home, project, live="strip_orphan_kernel_block")

        _assert_routes_to_system_messages(message, system_message, context)
        assert message != orphan_message, (
            "the fixture reached the SAME branch as the orphan-marker arm, so "
            "this arm measures nothing the arm above it does not measure "
            "before this. The two statuses must name different marker states."
        )
