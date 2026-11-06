"""In-band record of the certifying full-suite run.

Location: pact-plugin/tests/ci_full_run_record.py

Summary: A pytest plugin object that writes the counts, summary line, terminal
node IDs, collection-hook item counts and run identity of THIS run to
``${GITHUB_WORKSPACE}/.full-run-record.json``, so
``scripts/verify_full_run.py`` can check the run that certifies a merge rather
than a separate collection of it. There is no other location: with
GITHUB_WORKSPACE unset nothing is written.

Used by: pact-plugin/conftest.py, registered in ``pytest_configure``.

GATED ON GITHUB_ACTIONS, and written ONLY under GITHUB_WORKSPACE. A stray
artifact in the working tree is worse than a probe that sets two environment
variables. To probe a local run:
``GITHUB_ACTIONS=true GITHUB_WORKSPACE=/tmp/<rig> python -m pytest ...``.
A run with GITHUB_ACTIONS unset writes nothing; a gated run with no
workspace prints a loud diagnostic and writes nothing (the verifier then
fails on the absent record, which is the fail-closed direction).

ONLY THE OUTERMOST SESSION WRITES. The certifying suite spawns nested pytest
sessions, and every one of them inherits GITHUB_ACTIONS and GITHUB_WORKSPACE,
so the record path alone cannot tell the certifying session from a child. The
first instance in a process tree claims ``OWNER_ENV`` in the environment;
every session that finds it already claimed is nested and writes nothing. A
test that builds a child environment for its own record (a rig) must remove
``OWNER_ENV`` from that environment, or its child is treated as nested.
A collect-only session is not a run and writes nothing either.

WHY THE TERMINAL SUMMARY IS THE SOURCE. ``deselected`` comes from the
terminal reporter's stats, never ``len(session.items)``: deselection shrinks
the item list before tests run, so the item list cannot see it. The summary
line is built from the same reporter's ``build_summary_stats_line`` so the
record carries the line pytest would have printed.

WHY THE COLLECTION HOOK IS WRAPPED. A ``pytest_collection_modifyitems``
implementation can drop items without reporting them as deselected; that
shrinks the collect-only count and the run count equally, so comparing the
two cannot see it. The outermost wrapper records the item count before and
after every other implementation, and the verifier requires
``after + deselected == before``.

UNDER XDIST THE CONTROLLER NEVER RUNS THAT HOOK. Execution is distributed;
collection is repeated in every worker, so the controller's wrapper never
fires and both counts would be None. Each worker therefore PUBLISHES its own
``(before, after, deselected)`` triple in ``config.workeroutput``, and the
controller reads the triples in ``pytest_testnodedown``. The deselected count
travels with the pair because the controller never collects: it never sees a
worker's deselection, so its own stats report 0 and the record would claim a
deselection nobody measured. It is taken from the workers, never derived
from ``before - after``, which would satisfy the verifier's identity
arithmetically and leave the narrowing detector unable to tell a reported
deselection from an item removed without one. THE TRIPLES ARE NOT ADDITIVE:
every worker collected the whole corpus, so summing N workers yields N x the
corpus while the verifier requires ``after == collected``. The only faithful
aggregate is the triple every worker AGREES on; disagreement, or an
``after`` that does not equal the certifying session's own
``testscollected``, leaves the collection fields null and prints why. A
wrong integer here would turn a fail-closed gate into a fail-open one, so
None is the correct answer when no aggregate is provable.

WHY EACH SKIP IS RECORDED BY SITE. The verifier admits only skips listed in
a committed allowlist keyed on (file, reason), so the record carries every
skip as the file and verbatim reason the `-ra` summary prints for it: the
path relative to the invocation directory and the reason without pytest's
``Skipped: `` prefix. An xfail is not a skip and is not recorded here. A
module-level skip is counted in ``skipped`` but collects no item, so it is
also counted in ``skipped_at_collection`` for the verifier's accounting.

WRITTEN EVEN WHEN THE SESSION FAILED. A record that exists only on success
cannot evidence a failure. The write is atomic (temp + ``os.replace``) so the
verifier never reads a partial JSON document.
"""

import datetime
import json
import os
import sys
from pathlib import Path

import pytest

SCHEMA_VERSION = 3
RECORD_NAME = ".full-run-record.json"
OWNER_ENV = "PACT_FULL_RUN_RECORD_OWNER"
WORKER_OUTPUT_KEY = "pact_collection_item_counts"


def _gated_repo_root():
    """The checkout root, or None when this is not a gated CI run.

    The marker is the repository itself, matching the other in-band plugins:
    a nested pytest run in a temp rig has no marker above it and writes
    nothing.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _claim_ownership():
    """True for the first instance in a process tree, False for any nested one."""
    if os.environ.get(OWNER_ENV):
        return False
    os.environ[OWNER_ENV] = str(os.getpid())
    return True


def record_path():
    """The verifier's only path: under GITHUB_WORKSPACE, or None."""
    workspace = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if not workspace:
        return None
    return Path(workspace) / RECORD_NAME


def aggregate_worker_collection_counts(worker_pairs, collected):
    """The (before, after, deselected) triple every worker agreed on, or None.

    Every worker collects the WHOLE corpus, so these triples are not
    additive: summing N workers yields N x the corpus while the verifier
    requires ``after == collected``. The only faithful cross-worker value is
    a triple every worker reports identically. Disagreement, a non-integer
    entry, or an ``after`` inconsistent with the certifying session's own
    collected count means no aggregate is provable; None is returned so the
    verifier's BAD-FIELD failure stands rather than an invented integer
    turning a fail-closed gate into a fail-open one.

    The ``deselected`` entry is what the worker's own ``pytest_deselected``
    counted. It is taken from the workers rather than derived from
    ``before - after``: a derived count would satisfy the second identity
    arithmetically and leave the verifier's narrowing detector unable to
    distinguish a reported deselection from an item removed without one.

    The reporting population is complete: xdist dispatches
    ``pytest_testnodedown`` on both of its paths (``workerfinished`` and
    ``errordown``), so every node that ever connected is represented here, and
    one that published nothing arrives as None and is rejected.

    This guarantees only ``after == collected``; the second verifier identity,
    ``after + deselected == before``, is enforced in
    ``scripts/verify_full_run.py``, where a non-zero ``deselected`` is a hard
    failure before it is reached.
    """
    if not worker_pairs:
        return None
    triples = []
    for triple in worker_pairs:
        if not isinstance(triple, (list, tuple)) or len(triple) != 3:
            return None
        before, after, deselected = triple
        for value in (before, after, deselected):
            if not isinstance(value, int) or isinstance(value, bool):
                return None
        triples.append((before, after, deselected))
    if any(triple != triples[0] for triple in triples[1:]):
        return None
    if not isinstance(collected, int) or isinstance(collected, bool):
        return None
    if triples[0][1] != collected:
        return None
    return triples[0]


def _format_duration(seconds):
    """Match pytest's own summary duration rendering."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    delta = datetime.timedelta(seconds=int(seconds))
    return f"{seconds:.2f}s ({delta})"


class FullRunRecord:
    """Session-start to session-end recorder of the certifying run."""

    def __init__(self):
        root = _gated_repo_root()
        self._root = root if root is not None and _claim_ownership() else None
        self._terminal_node_ids = []
        self._seen_node_ids = set()
        self._items_before = None
        self._items_after = None
        self._deselected = 0
        self._worker_pairs = []
        self._skips = []
        self._collection_skips = 0

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        before = len(items)
        try:
            return (yield)
        finally:
            if self._items_before is None:
                self._items_before = before
                self._items_after = len(items)

    def pytest_deselected(self, items):
        """Count the items reported as deselected in this session.

        Under xdist the controller never collects, so it never sees a worker's
        deselection and its own stats report 0. The worker sees every event,
        which is why this count travels with the worker's (before, after)
        pair rather than being left to the controller.
        """
        self._deselected += len(items)

    def pytest_runtest_logreport(self, report):
        """Record every node ID that reached ANY terminal outcome.

        A skip reports at setup and an error at setup or call, so recording
        the first report per node ID includes skips without counting a
        passing test's setup/call/teardown three times.
        """
        if self._root is None:
            return
        node_id = report.nodeid
        if node_id not in self._seen_node_ids:
            self._seen_node_ids.add(node_id)
            self._terminal_node_ids.append(node_id)
        self._note_skip(report)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        """Collect each xdist worker's published pair (controller only).

        OPTIONAL HOOK: this spec comes from xdist, and the suite spawns
        child sessions with ``-p no:xdist``. Pluggy rejects a plugin that
        defines an unknown hook, so without ``optionalhook`` every such
        child session dies with PluginValidationError before running.
        """
        if self._root is None:
            return
        self._worker_pairs.append(
            getattr(node, "workeroutput", {}).get(WORKER_OUTPUT_KEY))

    def pytest_collectreport(self, report):
        """A module-level skip is a collection report, not a test report."""
        if self._root is not None and self._note_skip(report):
            self._collection_skips += 1

    def _note_skip(self, report):
        if not report.skipped or hasattr(report, "wasxfail"):
            return False
        longrepr = report.longrepr
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            path, _, reason = longrepr
        else:
            path, reason = report.nodeid.split("::", 1)[0], str(longrepr)
        self._skips.append((str(path), str(reason).removeprefix("Skipped: ")))
        return True

    @staticmethod
    def _skip_sites(skips, start):
        """(file, reason) -> count, with the path as the -ra summary shows it."""
        counts = {}
        for path, reason in skips:
            candidate = Path(path)
            if candidate.is_absolute():
                path = os.path.relpath(candidate, start)
            key = (Path(path).as_posix(), reason)
            counts[key] = counts.get(key, 0) + 1
        return [{"file": file, "reason": reason, "count": count}
                for (file, reason), count in sorted(counts.items())]

    @staticmethod
    def _summary_line(terminalreporter):
        """The line pytest would print, from the terminal summary source."""
        parts, _ = terminalreporter.build_summary_stats_line()
        text = ", ".join(part for part, _ in parts)
        elapsed = terminalreporter._session_start.elapsed().seconds
        return f"{text} in {_format_duration(elapsed)}"

    def pytest_sessionfinish(self, session, exitstatus):
        # A worker's _root is None (the controller claimed ownership), so this
        # branch must precede the guard below or no worker ever publishes.
        if getattr(session.config, "workerinput", None) is not None:
            # An xdist worker of a distributed run: publish this worker's own
            # counts for the controller. The controller aggregates by
            # agreement; see aggregate_worker_collection_counts.
            session.config.workeroutput[WORKER_OUTPUT_KEY] = (
                self._items_before, self._items_after, self._deselected)
            return
        if self._root is None or session.config.option.collectonly:
            return
        path = record_path()
        if path is None:
            # Gated but no workspace: fail loudly rather than write into the
            # working tree. The verifier treats the absent record as a
            # failure, which is the fail-closed direction.
            print(
                "CI-FULL-RUN-RECORD: GITHUB_ACTIONS is set but "
                "GITHUB_WORKSPACE is empty; no run record was written. "
                "Probe with GITHUB_WORKSPACE=/tmp/<rig>.",
                file=sys.stderr,
            )
            return
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats if reporter is not None else {}
        summary_line = None
        if reporter is not None:
            try:
                summary_line = self._summary_line(reporter)
            except Exception as exc:
                # A missing summary must surface as NO-SUMMARY in the
                # verifier, not as a crashed session. Print the reason so the
                # CI log names it.
                print(f"CI-FULL-RUN-RECORD: summary unavailable: {exc!r}",
                      file=sys.stderr)
                summary_line = None
        items_before = self._items_before
        items_after = self._items_after
        deselected = len(stats.get("deselected", []))
        # xdist's -n is absent without xdist, None for a serial run, and 0
        # for -n 0, which does not distribute; only a positive count is
        # distributed.
        numprocesses = getattr(session.config.option, "numprocesses", None)
        distributed = isinstance(numprocesses, int) and numprocesses > 0
        if items_before is None and distributed:
            collected = int(session.testscollected)
            aggregate = aggregate_worker_collection_counts(
                self._worker_pairs, collected)
            if aggregate is None:
                print(
                    "CI-FULL-RUN-RECORD: no faithful cross-worker collection "
                    f"aggregate (workers reported {self._worker_pairs!r}, "
                    f"collected={collected}); items_before/after_modifyitems "
                    "stay null so the verifier fails closed.",
                    file=sys.stderr,
                )
            else:
                items_before, items_after, deselected = aggregate
        record = {
            "schema_version": SCHEMA_VERSION,
            "collected": int(session.testscollected),
            "passed": len(stats.get("passed", [])),
            "skipped": len(stats.get("skipped", [])),
            "failed": len(stats.get("failed", [])),
            "errors": len(stats.get("error", [])),
            "deselected": deselected,
            "xfailed": len(stats.get("xfailed", [])),
            "xpassed": len(stats.get("xpassed", [])),
            "exitstatus": int(exitstatus),
            "summary_line": summary_line,
            "terminal_node_ids": list(self._terminal_node_ids),
            "items_before_modifyitems": items_before,
            "items_after_modifyitems": items_after,
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "owner_pid": os.getpid(),
            "skipped_at_collection": self._collection_skips,
            "skip_sites": self._skip_sites(
                self._skips, session.config.invocation_params.dir),
        }
        temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            # Never crash the certifying run. The verifier fails loudly on a
            # missing record; the log carries the reason.
            print(f"CI-FULL-RUN-RECORD: cannot write {path}: {exc}",
                  file=sys.stderr)
            try:
                temp.unlink()
            except OSError:
                pass
