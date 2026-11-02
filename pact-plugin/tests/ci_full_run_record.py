"""In-band record of the certifying full-suite run.

Location: pact-plugin/tests/ci_full_run_record.py

Summary: A pytest plugin object that writes the counts, summary line, terminal
node IDs, collection-hook item counts and run identity of THIS run to
``${GITHUB_WORKSPACE}/.pytest-run-record.json``, so
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

SCHEMA_VERSION = 2
RECORD_NAME = ".pytest-run-record.json"
OWNER_ENV = "PACT_FULL_RUN_RECORD_OWNER"


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

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        before = len(items)
        try:
            return (yield)
        finally:
            if self._items_before is None:
                self._items_before = before
                self._items_after = len(items)

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

    @staticmethod
    def _summary_line(terminalreporter):
        """The line pytest would print, from the terminal summary source."""
        parts, _ = terminalreporter.build_summary_stats_line()
        text = ", ".join(part for part, _ in parts)
        elapsed = terminalreporter._session_start.elapsed().seconds
        return f"{text} in {_format_duration(elapsed)}"

    def pytest_sessionfinish(self, session, exitstatus):
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
        record = {
            "schema_version": SCHEMA_VERSION,
            "collected": int(session.testscollected),
            "passed": len(stats.get("passed", [])),
            "skipped": len(stats.get("skipped", [])),
            "failed": len(stats.get("failed", [])),
            "errors": len(stats.get("error", [])),
            "deselected": len(stats.get("deselected", [])),
            "xfailed": len(stats.get("xfailed", [])),
            "xpassed": len(stats.get("xpassed", [])),
            "exitstatus": int(exitstatus),
            "summary_line": summary_line,
            "terminal_node_ids": list(self._terminal_node_ids),
            "items_before_modifyitems": self._items_before,
            "items_after_modifyitems": self._items_after,
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "owner_pid": os.getpid(),
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
