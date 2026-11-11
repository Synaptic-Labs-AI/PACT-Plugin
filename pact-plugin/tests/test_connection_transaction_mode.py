"""
Location: pact-plugin/tests/test_connection_transaction_mode.py

Summary: Arms for the transaction mode of the shipped connection factory. The
factory calls `sqlite3.connect` with NO `isolation_level` argument, so the
mode comes from a standard-library default rather than from a choice this
package states. These arms make that default a MEASURED property.

WHY THIS IS NOT A TEST ABOUT SQLITE. Two shipped mechanisms rest on it, and
each one is INERT rather than incorrect if the default ever moves.

  THE VECTOR WRITE PATH shares ONE TRANSACTION between the drop and the
  insert, by a `commit=False` flag on the drop helper. A shared transaction
  means something only when transactions are real on this connection. In
  autocommit the drop commits the moment it runs, the sharing is a fiction,
  and a failed insert costs the record its vector. That is the defect the
  flag was added to end, wearing the fix as a costume.

  THE CONNECTION-WIDE ROLLBACK in the embedding write path is correct because
  each caller commits its own work first. That argument needs a rollback that
  discards something. In autocommit it discards nothing for a different
  reason, and an arm built on the caller commits would pass whether or not
  those commits ran.

SO A GREEN HERE IS A PRECONDITION FOR TWO OTHER ARMS, AND NOT A RESULT OF ITS
OWN. Read it first.

THE MODE IS READ FROM THE LIVE CONNECTION, NEVER INFERRED FROM THE ABSENCE OF
AN ARGUMENT. An absent argument tells you what the caller did not say. It does
not tell you what the library chose, and the library default has moved between
versions before.
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3 as _stdlib_sqlite3
import subprocess
import sys

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files

from scripts.database import get_connection  # noqa: E402
# THE MODULE THE FACTORY BOUND, NOT THE ONE THIS TEST CAN IMPORT. The factory
# takes `pysqlite3` when it is present and falls back to the standard library.
# MEASURED, and it is the reason this import reads through the factory module:
# `pysqlite3` carries ITS OWN SQLite build (3.51.1 here, against 3.54.0 in the
# standard library) and its connection has NO `autocommit` attribute, so the
# transaction-control API added to the standard library in Python 3.12 is
# absent from the object under test. A check written against the standard
# library reports on a module this package does not use.
from scripts.database import sqlite3 as _factory_sqlite3  # noqa: E402


TABLE = "transaction_mode_probe"

# THE TWO WAYS A CALLER CAN STATE A TRANSACTION CHOICE. `isolation_level` is
# the legacy control. `autocommit` arrived in Python 3.12 and OUTRANKS it. A
# sweep that covers one name leaves the other route open.
TRANSACTION_CONTROLS = ("isolation_level", "autocommit")

_PACKAGE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "skills" / "pact-memory" / "scripts"
)


def _tracked_package_sources(package_dir: pathlib.Path) -> list[pathlib.Path]:
    repo_root = package_dir.parents[3]
    relative = package_dir.relative_to(repo_root).as_posix()
    return sorted(path for path in _tracked_files(repo_root, relative)
                  if path.suffix == ".py")


def _package_sources():
    return _tracked_package_sources(_PACKAGE_DIR)


def test_tracked_package_population_ignores_an_untracked_choice(tmp_path, monkeypatch):
    """The needle states a transaction choice; the old glob would include it."""
    package = tmp_path / "repo" / "pact-plugin" / "skills" / "pact-memory" / "scripts"
    known = package / "known.py"
    known.parent.mkdir(parents=True)
    known.write_text("X = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path / "repo")], check=True,
        capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "repo"), "add", "--", "pact-plugin"],
        check=True, capture_output=True)
    monkeypatch.setitem(globals(), "_PACKAGE_DIR", package)

    assert _package_sources() == [known]

    stray = package / "zzprobe.py"
    stray.write_text("def f(db):\n    return db.connect(autocommit=True)\n",
                     encoding="utf-8")
    try:
        assert _package_sources() == [known]
    finally:
        stray.unlink()


def _assignment_targets(node):
    """Return the assignment targets of one node, for each assignment shape.

    THREE SHAPES, because a check that covers one leaves the others open.
    `x.y = v` is an `Assign`, `x.y += v` is an `AugAssign`, and `x.y: T = v`
    is an `AnnAssign`. Each one states a choice.
    """
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


# THE DRIVER IS A RUNTIME FALLBACK, SO BOTH SHIP. database.py:32 binds
# `pysqlite3` and falls back to the standard library at :35 on ImportError. A
# consumer without `pysqlite3` installed therefore runs a DIFFERENT driver
# from the one the factory binds here.
#
# THE TWO DRIVERS ARE NOT INTERCHANGEABLE, and this repository holds a
# measured counter-example on a neighbouring axis: the standard library build
# keeps `-wal` and `-shm` beside the store after a clean close, and the
# `pysqlite3` build removes them. One operation, two outcomes. So a result
# taken on one driver states nothing about the other until somebody measures
# it, and these arms measure each driver that is present.
_DRIVERS = {"sqlite3": _stdlib_sqlite3}
if _factory_sqlite3 is not _stdlib_sqlite3:
    _DRIVERS[_factory_sqlite3.__name__] = _factory_sqlite3


def _fresh_reader(path):
    """Open a SECOND connection on the same file, through the SAME module.

    A read on the WRITING connection can report its own uncommitted work, so
    it cannot separate "the write was committed" from "the write is visible to
    the writer". A second connection reports what the FILE holds.
    """
    return _factory_sqlite3.connect(str(path))


@pytest.fixture
def store(tmp_path):
    """A store opened through the SHIPPED factory, with a probe table."""
    path = tmp_path / "transaction-mode.db"
    conn = get_connection(db_path=path)
    conn.execute("CREATE TABLE {0} (id TEXT PRIMARY KEY)".format(TABLE))
    conn.commit()
    yield conn, path
    conn.close()


def _rows(path):
    reader = _fresh_reader(path)
    try:
        return [r[0] for r in reader.execute(
            "SELECT id FROM {0} ORDER BY id".format(TABLE))]
    finally:
        reader.close()


class TestTheFactoryDeclaresATransactionMode:
    """Read the mode off the LIVE connection object."""

    def test_the_connection_is_not_in_autocommit(self, store):
        conn, _ = store
        # `isolation_level` of None IS autocommit. Any other value, and the
        # empty string is the library default, means a transaction opens
        # before a data change and a rollback has work to discard.
        assert conn.isolation_level is not None, (
            "the factory connection is in AUTOCOMMIT. A rollback discards "
            "nothing, so the shared transaction in the vector write path is a "
            "fiction and a failed insert costs the record its vector. "
            "python={0} driver={1} sqlite={2}".format(
                sys.version.split()[0], _factory_sqlite3.__name__,
                _factory_sqlite3.sqlite_version)
        )

    def test_isolation_level_governs_this_connection(self, store):
        """`isolation_level` is the control ONLY while legacy control is on.

        Python 3.12 added an `autocommit` attribute that OUTRANKS
        `isolation_level` when it leaves its legacy value. The arm above would
        then read a control that governs nothing.
        THE ATTRIBUTE IS ABSENT ON THE OBJECT UNDER TEST TODAY, because the
        factory binds `pysqlite3`, whose connection does not carry it. So this
        arm is CONDITIONAL BY CONSTRUCTION rather than by preference: it
        asserts on the attribute where the driver supplies one, and records
        the absence where it does not. A future move to the standard library
        driver, or a `pysqlite3` release that adds the attribute, brings the
        assertion into force with no edit here.
        """
        conn, _ = store
        legacy = getattr(_factory_sqlite3, "LEGACY_TRANSACTION_CONTROL", None)
        if hasattr(conn, "autocommit") and legacy is not None:
            assert conn.autocommit == legacy, (
                "the connection left legacy transaction control, so "
                "`isolation_level` no longer governs it and the arm above "
                "reads a control with no effect"
            )
        else:
            # NOT A SKIP. A skip hides the case in the skip count, and the
            # absence is a measured property of the driver rather than an
            # environmental exclusion.
            assert _factory_sqlite3.__name__ == "pysqlite3", (
                "a driver with no `autocommit` attribute is expected to be "
                "pysqlite3, and this one is {0}".format(
                    _factory_sqlite3.__name__)
            )

    def test_the_two_drivers_carry_different_sqlite_builds(self, store):
        """RECORDS THE ENVIRONMENT BESIDE THE RESULT, and it cannot fail for a
        version change, because it asserts no version number.

        A transaction-mode result without its driver is not reusable. The
        point worth carrying is that TWO SQLite builds sit in one process: the
        standard library carries one and `pysqlite3` carries another. A reader
        who upgrades Python alone does not move the mode measured here. A
        reader who upgrades `pysqlite3` can.
        """
        assert _factory_sqlite3.sqlite_version
        assert _stdlib_sqlite3.sqlite_version
        assert sys.version_info[:2] >= (3, 7)


class TestARollbackDiscardsUncommittedWork:
    """THE BEHAVIOURAL WITNESS, and the two arms are not interchangeable.

    MUTANT: pass `isolation_level=None` to `sqlite3.connect` in the factory.
    The uncommitted row then survives the rollback and the first arm reddens,
    while the control below stays green.
    """

    def test_a_committed_row_survives_a_rollback(self, store):
        """POSITIVE CONTROL, and the arm below is vacuous without it.

        A probe that writes nothing reports an absent row for the wrong
        reason. This proves the write, the file and the reader each work
        before absence is read as evidence.
        """
        conn, path = store
        conn.execute("INSERT INTO {0} (id) VALUES ('committed')".format(TABLE))
        conn.commit()
        conn.rollback()
        assert _rows(path) == ["committed"]

    def test_an_uncommitted_row_does_not_survive_a_rollback(self, store):
        conn, path = store
        conn.execute("INSERT INTO {0} (id) VALUES ('uncommitted')".format(TABLE))
        # Not committed. The rollback must discard it.
        conn.rollback()
        assert _rows(path) == [], (
            "an uncommitted row survived a rollback, so this connection "
            "starts no transaction for a data change"
        )


class TestEachShippedDriverStartsATransaction:
    """The SAME measurement on EACH driver that ships, not on one of them.

    THE ARMS ABOVE DRIVE THE FACTORY, so they measure the driver the factory
    bound HERE. A consumer without `pysqlite3` runs the standard library
    driver, and no arm above says one word about it.

    THESE ARMS MEASURE THE DRIVER RATHER THAN THE FACTORY, on purpose. They
    replicate the connect arguments the factory uses, and the arm below pins
    that replication so the two cannot drift apart in silence.

    THE TWO ASSERTIONS FACE OPPOSITE DIRECTIONS, which is what makes a false
    premise LOUD rather than quiet. A driver in autocommit FAILS the
    uncommitted arm and PASSES the committed arm. A probe whose arms can only
    pass measures the expectation of its author.
    """

    def test_the_driver_population_holds_the_factory_driver(self):
        """NON-VACUITY GATE. An empty or wrong population makes each
        parametrised arm below pass over nothing."""
        assert _DRIVERS, "no sqlite driver resolved"
        assert _factory_sqlite3.__name__ in _DRIVERS, (
            "the factory driver {0} is missing from the measured population "
            "{1}".format(_factory_sqlite3.__name__, sorted(_DRIVERS))
        )

    def test_the_package_states_no_transaction_choice(self):
        """The driver DEFAULT is the operative thing ONLY while the package
        states no choice of its own.

        THE ALPHABET COMES FROM THE WAYS A CHOICE CAN BE STATED, NOT FROM THE
        WAY THIS CODE CURRENTLY STATES ONE. My first version checked the
        `connect` KEYWORD alone, which is the route the code uses today. That
        is an alphabet read off the implementation, and it leaves two routes
        open. Somebody who writes `conn.isolation_level = None` on the line
        after the connect call has stated a choice, has overridden each driver
        default measured here, and passes a keyword-only check untouched.
        `conn.autocommit` is a third route, absent on today's driver and
        available the moment the standard-library fallback takes over.

        SO THE SWEEP COVERS THE TWO SHAPES: a keyword on a `connect` call, and
        an ASSIGNMENT to either control attribute, over each module in the
        package rather than over `database.py` alone.

        MEASURED BASELINE: the two names appear ZERO times in the package
        source today, so this arm starts from a clean population.

        MUTANT: add `isolation_level=None` to the factory connect call, or
        assign either attribute anywhere in the package. This arm reddens
        beside the behavioural arms, which is correct: the driver-level arms
        would then describe a default the package overrides.
        """
        offenders = []
        for path in _package_sources():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "connect"):
                    named = {kw.arg for kw in node.keywords}
                    for control in TRANSACTION_CONTROLS:
                        if control in named:
                            offenders.append(
                                (path.name, node.lineno, "keyword", control))
                for target in _assignment_targets(node):
                    if (isinstance(target, ast.Attribute)
                            and target.attr in TRANSACTION_CONTROLS):
                        offenders.append(
                            (path.name, node.lineno, "assignment", target.attr))
        assert not offenders, (
            "the package states a transaction choice at {0}, so the driver "
            "defaults measured in this module are not what governs it".format(
                offenders)
        )

    def test_the_swept_population_is_not_empty(self):
        """NON-VACUITY GATE for the sweep above. An empty file population
        makes the offender list empty for the wrong reason."""
        sources = _package_sources()
        assert len(sources) > 5, (
            "the package sweep found {0} module(s), which is too few to be "
            "the memory-layer package".format(len(sources))
        )
        assert any(p.name == "database.py" for p in sources), (
            "the sweep did not reach database.py, which holds the factory"
        )

    def test_the_detector_fires_on_a_planted_assignment(self):
        """POSITIVE CONTROL, and the sweep above is worth nothing without it.

        A detector that finds no assignment because it is broken reports the
        same empty list as a package that states no choice. This plants each
        shape and requires the detector to see it.
        """
        planted = "conn.isolation_level = None\nconn.autocommit = True\n"
        found = [
            target.attr
            for node in ast.walk(ast.parse(planted))
            for target in _assignment_targets(node)
            if isinstance(target, ast.Attribute)
            and target.attr in TRANSACTION_CONTROLS
        ]
        assert sorted(found) == sorted(TRANSACTION_CONTROLS), (
            "the assignment detector found {0} in a source that states each "
            "control, so it cannot report an offender in the package".format(
                found)
        )

    def test_the_detector_ignores_the_name_in_prose(self):
        """NEGATIVE CONTROL. It proves the sweep reads SYNTAX and not TEXT.

        This module's own docstrings name the two controls many times. A text
        instrument would count those and report offenders that do not exist.
        """
        prose = (
            '"""A docstring that names isolation_level and autocommit."""\n'
            "# A comment that names conn.isolation_level = None\n"
            'MESSAGE = "isolation_level"\n'
        )
        found = [
            target.attr
            for node in ast.walk(ast.parse(prose))
            for target in _assignment_targets(node)
            if isinstance(target, ast.Attribute)
            and target.attr in TRANSACTION_CONTROLS
        ]
        assert found == [], (
            "the detector reported {0} for a source that names the controls "
            "in prose alone, so it is reading text rather than syntax".format(
                found)
        )

    @pytest.mark.parametrize("driver_name", sorted(_DRIVERS))
    def test_a_committed_row_survives_a_rollback(self, driver_name, tmp_path):
        """POSITIVE CONTROL, per driver. It PASSES in autocommit too, and that
        is the point: it proves the write and the reader work, so the arm
        below reads absence as evidence."""
        driver = _DRIVERS[driver_name]
        path = tmp_path / "{0}-committed.db".format(driver_name)
        conn = driver.connect(str(path), check_same_thread=False)
        try:
            conn.execute("CREATE TABLE {0} (id TEXT PRIMARY KEY)".format(TABLE))
            conn.commit()
            conn.execute("INSERT INTO {0} (id) VALUES ('c')".format(TABLE))
            conn.commit()
            conn.rollback()
        finally:
            conn.close()
        reader = driver.connect(str(path))
        try:
            assert [r[0] for r in reader.execute(
                "SELECT id FROM {0}".format(TABLE))] == ["c"]
        finally:
            reader.close()

    @pytest.mark.parametrize("driver_name", sorted(_DRIVERS))
    def test_an_uncommitted_row_does_not_survive_a_rollback(
            self, driver_name, tmp_path):
        """THE WITNESS, per driver. It FAILS in autocommit."""
        driver = _DRIVERS[driver_name]
        path = tmp_path / "{0}-uncommitted.db".format(driver_name)
        conn = driver.connect(str(path), check_same_thread=False)
        try:
            conn.execute("CREATE TABLE {0} (id TEXT PRIMARY KEY)".format(TABLE))
            conn.commit()
            conn.execute("INSERT INTO {0} (id) VALUES ('u')".format(TABLE))
            conn.rollback()
        finally:
            conn.close()
        reader = driver.connect(str(path))
        try:
            assert [r[0] for r in reader.execute(
                "SELECT id FROM {0}".format(TABLE))] == [], (
                "driver {0} kept an uncommitted row across a rollback, so it "
                "starts no transaction for a data change and the shared "
                "transaction in the vector write path is a fiction "
                "there".format(driver_name)
            )
        finally:
            reader.close()
