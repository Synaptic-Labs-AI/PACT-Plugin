"""A caller-supplied store path is OPENED, never brought into existence.

THE HALF THIS CLOSES. An earlier refusal covered a typo in the DIRECTORY. A
typo in the FILE NAME reached a directory that was present, built a store, and
reported an ordinary result. In the true spelling that throwaway store lands
INSIDE the live store directory, so the caller keeps working against a store
that holds nothing.

THE REFUSAL LIVES AT THE CLI BOUNDARY, IN `cli.main`, AND THESE ARMS DRIVE THE
CLI. A refusal inside `database.get_connection` was built first and rejected:
it reaches EACH caller of the connection factory, so it breaks the custom-store
contract that production and the test suite depend on. The boundary can read
the command name as a fact on `args`, so it separates `setup` from the commands
that open a store.

THE ASYMMETRY IS DELIBERATE. `setup` creates, because bringing a store into
existence at a named location is its operation. `save`, `get` and `list` open a
store that should be there, so an absent one is a typo.

THE CLASS OF EACH ARM, BECAUSE RED HAS SEVERAL CAUSES AND ONLY ONE OF THEM IS
THE MECHANISM.

  RED FOR THE MECHANISM on the unfixed code:
    test_an_absent_caller_file_is_refused. The store was built and the caller
    saw an ordinary result.

  OVER-BLOCK GUARDS. These cannot go red on the unfixed code, because it
  carries no refusal to over-block. They bound the refusal instead:
    test_a_store_that_is_present_still_opens
    test_a_path_holding_shell_or_uri_syntax_still_opens
    test_setup_may_bring_a_caller_path_into_existence

  GREEN BEFORE AND AFTER, AND IT OUTRANKS THE REFUSAL:
    test_the_derived_route_still_creates. An arm proving only the refusal would
    pass against a fix that broke each production caller.

  PINS AN ACCEPTED TRADE RATHER THAN A DESIRED OUTCOME:
    test_an_absent_path_is_reported_before_malformed_input. Read its docstring
    before you change the boundary check.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_PARENT = Path(__file__).parent.parent / "skills" / "pact-memory"

from scripts import cli  # noqa: E402
from scripts.database import get_connection  # noqa: E402

_CLI = SCRIPTS_PARENT / "scripts" / "cli.py"

# THE MINIMAL ARGUMENTS THAT REACH THE BOUNDARY, ONE ROW PER COMMAND.
#
# ⚠️ A COMMAND WITH A REQUIRED POSITIONAL EXITS AT ARGPARSE BEFORE `main`
# READS THE PATH. That exit also returns a non-zero status, so a row that
# omits the positional passes a status assertion, reaches nothing, and pins
# nothing. This table is what carries each row past argparse and into the
# refusal. A test below asserts that this table and the dispatch table hold
# the same command names, so a new command cannot slip through un-swept.
_MINIMAL_ARGS = {
    "save": ('{"context": "x"}',),
    "search": ("q",),
    "list": (),
    "get": ("a" * 32,),
    "status": (),
    "update": ("a" * 32, '{"context": "x"}'),
    "delete": ("a" * 32,),
    "sync": (),
    # `show` is the read-only verb; the absent-path guard sits before the
    # handler, so the subcommand never reaches detection.
    "project-id": ("show",),
}

# A command that opens no store takes no `--db-path`, so it has no caller path
# to refuse; it is swept by its own row below instead.
_NON_EXEMPT_COMMANDS = sorted(
    set(cli._COMMANDS)
    - set(cli._COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH)
    - set(cli._COMMANDS_WITHOUT_A_STORE)
)


def _run(*argv):
    """Run the memory CLI as a real subprocess, the way its own tests do."""
    return subprocess.run(
        [sys.executable, str(_CLI), *argv],
        capture_output=True, text=True, timeout=60,
    )


def _envelope(proc):
    """Return the parsed error envelope, failing with the streams on a mis-parse."""
    try:
        return json.loads(proc.stderr)
    except json.JSONDecodeError:
        pytest.fail(
            f"stderr did not parse as JSON, so the envelope contract is broken. "
            f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )


def _make_store(path):
    """Bring a store into existence at `path` by the one command that may."""
    proc = _run("setup", "--db-path", str(path))
    assert proc.returncode == 0, (
        f"setup could not create the store, so the arm below would measure the "
        f"setup failure rather than its own subject. "
        f"rc={proc.returncode} stderr={proc.stderr!r}"
    )
    assert path.exists(), "setup reported success and left no store behind"
    return path


class TestACallerPathIsOpenedNotCreated:
    def test_an_absent_caller_file_is_refused(self, tmp_path):
        """RED BEFORE THE FIX: the store was built and the caller saw a result.

        The parent directory is present here on purpose. That is what separates
        this arm from the directory half: the earlier guard has nothing to
        refuse, so only a guard on the FILE can fail this call.
        """
        present_dir = tmp_path / "present-dir"
        present_dir.mkdir()
        typo = present_dir / "memroy.db"

        proc = _run("get", "x" * 32, "--db-path", str(typo))

        assert proc.returncode != 0, "a mistyped file name reported success"
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND"
        assert not typo.exists(), (
            "a mistyped file name built its own store, so the typo succeeded "
            "against an empty database instead of failing"
        )

    def test_a_store_that_is_present_still_opens(self, tmp_path):
        """THE OVER-BLOCK GUARD. A refusal that also refuses correct paths is
        worse than the defect. This arm is why the refusal is bounded to
        ABSENT files rather than to caller paths as a class."""
        store = _make_store(tmp_path / "already-there.db")

        proc = _run("list", "--db-path", str(store))

        assert proc.returncode == 0, (
            f"a store that is present was refused. rc={proc.returncode} "
            f"stderr={proc.stderr!r}"
        )

    @pytest.mark.parametrize(
        "name", ["has#hash.db", "has?query.db", "has%pct.db", "has space.db"]
    )
    def test_a_path_holding_shell_or_uri_syntax_still_opens(self, tmp_path, name):
        """THE CARDINAL ARM, AND IT IS NOT OPTIONAL.

        Over-block is the cardinal failure here. A store path may hold `#`, `?`,
        `%` or a space. Each of those is syntax to a URI, and a space is syntax
        to a shell. The path must survive the argument vector AND the existence
        test, so a correct path holding one of them continues to open.
        """
        store = _make_store(tmp_path / name)

        proc = _run("list", "--db-path", str(store))

        assert proc.returncode == 0, (
            f"a correct path holding {name!r} was refused, which turns a guard "
            f"against typos into a guard against good-faith callers. "
            f"stderr={proc.stderr!r}"
        )

    def test_setup_may_bring_a_caller_path_into_existence(self, tmp_path):
        """THE EXEMPTION, AND IT IS THE OTHER HALF OF THE RULE.

        `setup` is the one command allowed to create at a path a caller typed.
        Without this arm the refusal would read as correct while leaving no way
        to make a store at a named location at all.
        """
        store = tmp_path / "brand-new.db"
        assert not store.exists()

        proc = _run("setup", "--db-path", str(store))

        assert proc.returncode == 0, f"setup was refused. stderr={proc.stderr!r}"
        assert store.exists(), "setup reported success and created no store"


class TestTheDirectoryHalfStillHoldsBelowTheBoundary:
    """THE DIRECTORY HALF IS PINNED HERE, AT THE LAYER THE CLI SEAM CANNOT SEE.

    WHY THIS ARM EXISTS, AND IT IS A COVERAGE REPAIR RATHER THAN A NEW RULE.
    `database.get_db_path` creates the parent directory for a DERIVED origin
    only, so a caller path with an absent parent gets no tree built for it. A
    CLI arm used to prove that. It cannot any longer: the boundary refusal now
    fires FIRST for an absent caller path, so the CLI arm passes without ever
    reaching the resolver, and the directory-half guard would ship unpinned.

    A LIBRARY CALLER IS THE ONE ROUTE THAT STILL REACHES IT. That is the same
    route the CLI seam names as its accepted residual, so this arm covers what
    the seam does not.
    """

    def test_a_caller_path_with_an_absent_parent_builds_no_tree(self, tmp_path):
        """MUTANT THAT KILLS THIS ARM: make the `mkdir` in `get_db_path`
        unconditional rather than gated on a derived origin. The directory then
        appears and this arm reddens."""
        absent_parent = tmp_path / "typo-dir"
        target = absent_parent / "x.db"
        assert not absent_parent.exists()

        with pytest.raises(Exception):
            get_connection(target)

        assert not absent_parent.exists(), (
            "a caller path with a mistyped directory had its tree built, so the "
            "typo would succeed against an empty store instead of failing"
        )
        assert not target.exists()


class TestTheCreatingRouteKeepsItsHardening:
    def test_the_derived_route_still_creates(self, tmp_path, monkeypatch):
        """THE RULE THAT OUTRANKS THE REFUSAL. A pathless production caller
        must reach AND create its store.

        This is the arm that fails if the refusal is placed where it cannot
        tell a caller path from a derived one.
        """
        derived_root = tmp_path / "derived" / "pact-memory"
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(derived_root))

        with get_connection() as conn:
            conn.execute("SELECT 1")

        assert (derived_root / "memory.db").exists()

    def test_a_new_derived_store_hardens_itself_and_its_sidecars(
        self, tmp_path, monkeypatch
    ):
        """`is_new` DRIVES THE CREATE AND THE SIDECAR PERMISSIONS.

        A change that suppressed the create would leave a genuinely new store
        with sidecars at the process umask, holding memory content. This arm
        measures the MODE rather than the presence.
        """
        derived_root = tmp_path / "derived" / "pact-memory"
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(derived_root))
        store = derived_root / "memory.db"

        with get_connection() as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")

            assert stat.S_IMODE(store.stat().st_mode) == 0o600

            hardened = 0
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(store) + suffix)
                if sidecar.exists():
                    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
                    hardened += 1
            # CONTROL: at least one sidecar was present, so the loop above was
            # not vacuous. WAL mode creates them on the first write.
            assert hardened > 0

    def test_a_new_store_made_through_the_schema_factory_hardens_file_and_sidecars(
        self, tmp_path, monkeypatch
    ):
        """THE SHAPE THE DEFERRED-WAL REGRESSION SLIPPED THROUGH.

        The arm above hand-rolls `get_connection` + `CREATE TABLE`, so it can
        only observe sidecars that some earlier step happened to create. This
        one goes through the SCHEMA FACTORY the production create path uses
        (`init_schema`), and names EACH artifact and its mode: the store file,
        `-wal` and `-shm` must all exist and be 0600. A deferred WAL switch
        leaves the sidecars absent, which the presence check catches before
        the mode check can be satisfied vacuously.
        """
        derived_root = tmp_path / "factory" / "pact-memory"
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(derived_root))
        store = derived_root / "memory.db"

        from scripts.database import init_schema

        with get_connection() as conn:
            init_schema(conn)

            assert stat.S_IMODE(store.stat().st_mode) == 0o600, (
                "a brand-new store created through the schema factory is not "
                "0600"
            )
            hardened = 0
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(store) + suffix)
                assert sidecar.exists(), (
                    f"the new store has no {suffix} sidecar, so the creating "
                    "path never switched to WAL and the hardening ran against "
                    "artifacts that did not exist"
                )
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600, (
                    f"the {suffix} sidecar is not 0600"
                )
                hardened += 1
            assert hardened == 2, "both WAL sidecars must be asserted"


class TestAPresentButUnreadableStoreIsAnError:
    """A store that EXISTS but cannot be read must not answer as a success.

    MEASURED at 3ac1d896: `chmod 000` on a valid store made `cli list` return
    `{"ok": true, "result": []}` with exit 0, and `status` a zeroed envelope
    with no degraded marker. Before the read-purity arc the same fault raised
    `SYSTEM_ERROR`. An empty success is the silent-loss shape this whole arc
    exists to remove: a permission fault is not "this project has no
    memories", and the operator is told nothing.
    """

    _VERBS = (("list",), ("status",), ("project-id", "show"))

    @pytest.mark.requires_non_root
    @pytest.mark.parametrize(
        "argv", _VERBS, ids=["list", "status", "project-id-show"]
    )
    def test_an_unreadable_store_reports_an_error_naming_the_fault(
        self, argv, tmp_path
    ):
        store = _make_store(tmp_path / "valid.db")
        os.chmod(store, 0)
        try:
            proc = _run(*argv, "--db-path", str(store))
        finally:
            # Restore before any assertion can abort, so tmp cleanup is clean.
            os.chmod(store, 0o600)

        assert proc.returncode != 0, (
            f"{' '.join(argv)} answered successfully on a store it could not "
            f"read: rc={proc.returncode} stdout={proc.stdout!r}"
        )
        payload = _envelope(proc)
        assert payload.get("ok") is False, payload
        # THE MESSAGE FIELD ONLY, AND TOKENS THE TMP PATH CANNOT SMUGGLE.
        # Searching the whole JSON was a false green: the tmp directory is
        # named after this test, and "unreadable" contains "readable", so the
        # token matched inside the echoed store path while the message said
        # nothing about the fault. These tokens do not occur in the path.
        message = str(payload.get("message", "")).lower()
        assert str(store) in payload.get("message", ""), (
            f"the error does not name the store: {payload!r}"
        )
        assert any(
            token in message
            for token in ("permission", "chmod", "denied", "traverse")
        ), (
            "the error names neither the fault nor a remedy the operator can "
            f"act on: {payload!r}"
        )


class TestTheOrderOfTheBoundaryCheck:
    def test_an_absent_path_is_reported_before_malformed_input(self, tmp_path):
        """PINS AN ACCEPTED TRADE. DO NOT READ THIS AS A DESIRED OUTCOME.

        The boundary check runs BEFORE the handler, so a caller who makes TWO
        mistakes at one time, an absent `--db-path` AND malformed JSON, hears
        about the PATH and not about the JSON.

        WHY THAT ORDER SHIPS. To report the JSON first, the check must move
        after input validation, which means placing it inside each handler that
        opens a store. That is the per-caller seam this design rejected, and it
        returns the refusal to a place that cannot tell a person from a library
        caller.

        WHY IT IS TOLERABLE. The message is TRUE rather than misleading. The
        save cannot land anywhere, whatever the JSON says.

        THIS ARM MAKES THE TRADE VISIBLE. Without it the order is untested
        rather than chosen, and a later reader cannot tell which it was. If you
        change the boundary check, change this arm deliberately.
        """
        absent = tmp_path / "not-there.db"

        proc = _run("save", "--db-path", str(absent), "{not valid json")

        assert proc.returncode != 0
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND", (
            "the boundary check no longer precedes handler input validation. "
            "That may be an improvement. Read this docstring and decide, rather "
            "than update the expected string to whatever the code now returns."
        )
        assert not absent.exists()

    def test_an_absent_path_precedes_malformed_input_to_update_too(self, tmp_path):
        """THE SECOND HANDLER OF THE PAIR, SO NEITHER ONE STANDS ALONE.

        `cmd_save` and `cmd_update` are the ONLY two handlers that report an
        error before they open a store. An arm on one of them leaves the other
        free to drift. This arm is the same claim on `cmd_update`.
        """
        absent = tmp_path / "not-there.db"

        proc = _run(
            "update", "--db-path", str(absent), "a" * 32, "{not valid json"
        )

        assert proc.returncode != 0
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND"
        assert not absent.exists()

    @pytest.mark.parametrize(
        "argv_tail",
        [
            ("save", "{not valid json"),
            ("update", "a" * 32, "{not valid json"),
        ],
        ids=["save", "update"],
    )
    def test_with_the_store_present_the_handler_reports_the_input(
        self, memory_store, argv_tail
    ):
        """THE POSITIVE CONTROL, AND THE TWO ARMS ABOVE ARE VACUOUS WITHOUT IT.

        The arms above asserts that the PATH is named first. Each of them passes
        against a build that DELETED the JSON check, because a deleted check also
        never reports `INVALID_JSON`. This arm removes that reading: with the
        store PRESENT, the boundary has nothing to refuse, so the handler check
        runs and names the input.

        READ THE THREE TOGETHER. They say the boundary check CHANGES THE ORDER of
        two live checks. They do not say it suppresses one.
        """
        command, *rest = argv_tail
        store = memory_store("present.db")

        proc = _run(command, "--db-path", str(store), *rest)

        assert proc.returncode != 0
        assert _envelope(proc)["error"] == "INVALID_JSON", (
            "with the store present the handler no longer reports the malformed "
            "input, so the ordering arms above prove nothing about ordering"
        )


class TestEachNonExemptCommandRefusesAnAbsentCallerPath:
    """DERIVE THE ALPHABET FROM THE GUARDED THING, NOT FROM A HAND LIST.

    The rule is a statement about `cli._COMMANDS` minus the exemption set, so
    the row set is computed from those two objects rather than typed out. A
    hand list covers the commands its author remembered. It reddens for none
    of the commands added afterwards, and it reddens for none of the commands
    the exemption set grows to hold, which is the drift that lets a guard
    ship narrower than the thing it guards.

    THE ROWS OVERLAP THE HAND-WRITTEN ARMS ABOVE ON PURPOSE. Those arms assert
    richer things about one command each. These assert one thing about each
    command.
    """

    def test_the_row_set_is_the_dispatch_table_minus_the_exemption(self):
        """THE NON-VACUITY AND DRIFT GUARD FOR THE SWEEP BELOW.

        A sweep whose own population is unfalsifiable is the condition each
        instance of this defect class shares, so the population is asserted
        here rather than assumed.
        """
        assert _NON_EXEMPT_COMMANDS, (
            "the row set is empty, so the sweep below runs no rows and passes"
        )
        assert set(_MINIMAL_ARGS) == set(_NON_EXEMPT_COMMANDS), (
            f"the argument table and the dispatch table disagree. A command "
            f"added to `cli._COMMANDS` needs a row in `_MINIMAL_ARGS`, or the "
            f"sweep covers the alphabet it was written against rather than "
            f"the one that ships. "
            f"dispatch-only={sorted(set(_NON_EXEMPT_COMMANDS) - set(_MINIMAL_ARGS))} "
            f"table-only={sorted(set(_MINIMAL_ARGS) - set(_NON_EXEMPT_COMMANDS))}"
        )
        assert cli._COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH == frozenset({"setup"}), (
            "the exemption set widened. Each command it holds stops being "
            "swept, so a widening must be a deliberate edit here as well as "
            "there."
        )
        assert cli._COMMANDS_WITHOUT_A_STORE == frozenset({"project-file"}), (
            "the storeless set widened. Each command it holds stops being "
            "swept, so a widening must be a deliberate edit here as well as "
            "there."
        )

    @pytest.mark.parametrize("command", sorted(cli._COMMANDS_WITHOUT_A_STORE))
    def test_a_storeless_command_accepts_no_caller_path(self, command, tmp_path):
        """A command that opens no store refuses `--db-path` at argparse, so a
        caller path can never reach it, and nothing is built there."""
        absent = tmp_path / f"{command}-not-there.db"

        proc = _run(command, "--db-path", str(absent))

        assert proc.returncode == 2, proc.stderr
        assert "unrecognized arguments: --db-path" in proc.stderr, proc.stderr
        assert not absent.exists()

    @pytest.mark.parametrize("command", _NON_EXEMPT_COMMANDS)
    def test_an_absent_caller_path_is_refused(self, command, tmp_path):
        """Each command other than `setup` refuses a caller path that is absent.

        ⚠️ THIS ARM READS THE ERROR NAME AND NOT THE STATUS, AND THAT IS THE
        WHOLE DESIGN OF IT. A row that exits at argparse also returns a
        non-zero status. A status assertion would pass for that row while the
        row never reached the boundary, which would rebuild inside this sweep
        the defect the sweep exists to close.
        """
        absent = tmp_path / f"{command}-not-there.db"

        proc = _run(command, *_MINIMAL_ARGS[command], "--db-path", str(absent))

        assert proc.returncode != 0, f"`{command}` reported success"
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND", (
            f"`{command}` did not reach the boundary refusal, so this row "
            f"pins nothing. stdout={proc.stdout[:200]!r}"
        )
        assert not absent.exists(), (
            f"`{command}` built a store at a path a caller typed"
        )


class TestSetupMayAlsoCreateTheParentDirectory:
    """PINS AN ACCEPTED TRADE. DO NOT READ THIS AS A DESIRED OUTCOME.

    `setup` is exempt from the boundary refusal, and it then reaches
    `setup_memory.ensure_directories`, which creates the parent with
    `parents=True`. So a typo in the DIRECTORY under `setup` builds a tree and
    a store at the mistyped location, which is the harm class this branch
    closed for each other command.

    WHY IT SHIPS THIS WAY. `setup` exists to bring a store into existence at a
    named location, so a refusal of an absent parent is a behaviour change to
    the one command whose purpose is creation. That deserves its own
    consideration rather than a tail-end edit.

    THE RESIDUAL, NAMED RATHER THAN IMPLIED: a DIRECTORY typo on `setup
    --db-path` is refused nowhere. The FILE half is refused for each other
    command at the boundary, and the DIRECTORY half is refused for a library
    caller by the class above. `setup` sits outside the two.

    THIS ARM MAKES THE TRADE VISIBLE. Without it the behaviour is untested
    rather than chosen, and a later reader cannot tell which it was. If you
    change it, change this arm deliberately.
    """

    def test_setup_builds_an_absent_parent_at_a_caller_path(self, tmp_path):
        target = tmp_path / "typo-dir" / "x.db"
        assert not target.parent.exists()

        proc = _run("setup", "--db-path", str(target))

        assert proc.returncode == 0, f"setup was refused. stderr={proc.stderr!r}"
        assert target.parent.is_dir(), (
            "setup no longer creates the parent of a caller path. That may be "
            "an improvement. Read this docstring and decide, rather than "
            "delete the assertion."
        )
        assert target.exists()


class TestTheEmptyStringIsNotACallerPath:
    """THE ONE EXCEPTION TO THE RULE, PINNED SO THE RULE STAYS HONEST.

    `--db-path ""` is a caller value that is not a store, and it is NOT
    refused. The falsy coercion in `cli.main` collapses it to None before the
    refusal reads it, so it takes the DERIVED route. That coercion is
    deliberate and pre-existing, and this arm does not ask for it to change.
    It pins the EXCEPTION, so a reader of the rule meets its boundary.

    WHAT ANSWERS INSTEAD, which is why this arm needs no store. Under pytest
    the unscoped-store guard refuses the derived route, so the call is refused
    for a different reason and reaches nothing.
    """

    def test_an_empty_db_path_is_not_refused_as_a_caller_path(self):
        proc = _run("list", "--db-path", "")

        envelope = _envelope(proc)
        assert envelope["error"] != "DB_PATH_NOT_FOUND", (
            "the empty string is now treated as a caller path. That may be an "
            "improvement, and it changes a declared behaviour: read the "
            "coercion comment in `cli.main` and decide deliberately."
        )
        assert envelope["error"] == "UNSCOPED_TEST_DB", (
            f"the empty string took neither the refusal nor the guarded "
            f"derived route, so this arm no longer shows where it lands: "
            f"{envelope}"
        )


class TestTheRefusalMessageStatesWhatItObserved:
    """THE MESSAGE MUST NOT ASSERT ABSENCE, BECAUSE THE TEST CANNOT SEE IT.

    `Path.exists()` answers False for a path that is ABSENT and for a path the
    process cannot STAT. A permission fault on a parent directory therefore
    reads the same as a typo, and a message that asserts the store is not
    there sends such a caller to `setup`, which does not repair one.

    THE PREMISE ABOVE IS MEASURED, NOT ASSUMED, AND THE MEASUREMENT IS NOT
    IN THIS FILE. A store that is present below a parent directory at mode
    0o000 makes `Path.exists()` answer False, and the CLI then reports
    DB_PATH_NOT_FOUND for a store that IS there. `Path.exists()` delegates to
    `os.path.exists`, which returns False for an OSError rather than raising
    it. That is why the message names the stat case, and it is what lets this
    arm stub the predicate rather than build the condition.

    THIS ARM REPRODUCES THE CONDITION AND NOT THE CAUSE. It makes the
    predicate answer False while the store IS present. That is the semantics
    that matter, and it is environment-independent. A mode change on a parent
    directory does not hold when the suite runs as root, and would give a
    silent pass there.
    """

    def test_the_message_does_not_claim_the_store_is_absent(
        self, tmp_path, monkeypatch, capfd
    ):
        store = _make_store(tmp_path / "present.db")
        real_exists = Path.exists

        def _blind_to_one_path(self):
            if self == store:
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", _blind_to_one_path)

        with pytest.raises(SystemExit):
            cli.main(["list", "--db-path", str(store)])

        envelope = json.loads(capfd.readouterr().err)
        assert envelope["error"] == "DB_PATH_NOT_FOUND"
        message = envelope["message"]
        assert "cannot stat" in message, (
            f"the message does not tell the caller that the test it failed "
            f"also fails for a path this process cannot stat, so a permission "
            f"fault reads as a typo: {message!r}"
        )
        assert "No store at" not in message, (
            f"the message asserts absence, which is an inference the "
            f"predicate did not establish: {message!r}"
        )


class TestTheRefusalScrubsTheHomeDirectory:
    """THE MESSAGE CARRIES A CALLER PATH, SO IT REPORTS THE SCRUBBED FORM.

    Six sibling error sites in `cli.py` scrub before they report, because a
    caller pipes this envelope into a log and an absolute path carries the
    account name. The refusal is the seventh site and follows the same idiom.

    THIS ARM EXISTS BECAUSE THE PROPERTY WAS CORRECT AND UNDEFENDED. Removal
    of the `_scrub` call left the suite green, which is the same shape as the
    defect this file closes: a property that holds today and that no arm
    watches tomorrow. A guard nobody watches is one edit from gone.

    MUTANT THAT KILLS THIS ARM: report `str(db_path)` in place of the scrubbed
    value at the refusal site in `cli.main`.
    """

    def test_the_refused_path_is_reported_below_a_tilde(self, tmp_path):
        home = tmp_path / "home"
        (home / "store-dir").mkdir(parents=True)
        absent = home / "store-dir" / "not-there.db"

        env = dict(os.environ)
        env["HOME"] = str(home)
        proc = subprocess.run(
            [sys.executable, str(_CLI), "list", "--db-path", str(absent)],
            capture_output=True, text=True, timeout=60, env=env,
        )

        message = _envelope(proc)["message"]
        assert str(home) not in message, (
            f"the refusal reported the home directory literally, so a caller "
            f"who pipes this envelope into a log leaks it: {message!r}"
        )
        # THE POSITIVE CONTROL. Absence of the literal is also satisfied by a
        # message that names no path at all, so the arm reads the scrubbed
        # form as well.
        assert "~/store-dir/not-there.db" in message, (
            f"the path is neither literal nor scrubbed to a tilde, so this "
            f"arm cannot say the scrub ran: {message!r}"
        )
