"""Location: pact-plugin/tests/test_visualizer_js_pins.py
Summary: Regression pins for the front-end behaviours that had no coverage:
         run identity across journal revisions, journal-seq -> visible-index
         mapping, pending phases in the stage's phase state, mission time for
         an empty window, and the bundled dev-demo fixture matching
         demo.build_demo_model(). The JS pins run only when `node` is on PATH:
         they import the pure module functions, with no browser, no server and
         no build step. The fixture parity pin is pure Python and always runs.
Used by: pytest (front-end regression gate for pact-plugin/visualizer).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from visualizer import demo

PLUGIN_ROOT = Path(__file__).parent.parent
STATIC_JS_DIR = PLUGIN_ROOT / "visualizer" / "static" / "js"
DEV_DEMO = PLUGIN_ROOT / "visualizer" / "static" / "dev-demo.json"

_NODE = shutil.which("node")

# Runs inside static/js so the modules' relative imports resolve. Each check
# names the behaviour, not the file, so a failure says what broke.
PIN_SCRIPT = r"""
const failed = [];
const check = (name, ok) => { if (!ok) failed.push(name); };

(async () => {
  const { Stage } = await import("./stage.js");
  const {
    createSeqIndex,
    missionSecondsAt,
    rootChangeAction,
    modeBadge,
    feedKey,
    requestedFilterLabel,
  } = await import("./dom.js");

  /* A live poll is a new journal revision of the SAME run. Identity must not
   * include the etag, or the stage resets its roster and selection each tick. */
  const identity = Stage.prototype._identity;
  const base = {
    source: { kind: "real", root: "~/.claude", slug: "P", session_id: "abc" },
    meta: { team: "t" },
  };
  const first = identity.call({}, Object.assign({}, base, { etag: "1700000000-1000" }));
  const second = identity.call({}, Object.assign({}, base, { etag: "1700000010-2000" }));
  const otherRun = identity.call({}, Object.assign({}, base, {
    etag: "1700000000-1000",
    source: Object.assign({}, base.source, { session_id: "zzz" }),
  }));
  check("identity is stable across a journal revision", first === second);
  check("identity still separates different runs", first !== otherRun);

  /* Phase boundaries are journal seqs; the event array is the visible window. */
  const indexFor = createSeqIndex({ events: [{ seq: 100 }, { seq: 101 }, { seq: 103 }] });
  check("seq maps to its visible index", indexFor(103) === 2);
  check("a seq before the window maps to 0", indexFor(99) === 0);
  check("a seq past the window maps to its length", indexFor(104) === 3);
  check("an absent seq has no index", indexFor(null) === null);

  /* A filtered root with no sessions clears the open run: the rails, recorder
   * and caption must not disagree about what is on screen. */
  check("a session in the new root always opens", rootChangeAction({
    hasSession: true, requestedFilter: "P", hasRun: true,
  }) === "open");
  check("a filtered empty root clears the open run", rootChangeAction({
    hasSession: false, requestedFilter: "P", hasRun: true,
  }) === "empty");
  check("an unfiltered empty root keeps an open run", rootChangeAction({
    hasSession: false, requestedFilter: "", hasRun: true,
  }) === "keep");
  check("an unfiltered empty root with nothing open opens the demo", rootChangeAction({
    hasSession: false, requestedFilter: "", hasRun: false,
  }) === "demo");

  /* The feed cache must distinguish a SHIFTED visible window from a new run:
   * above MAX_EVENTS the length is constant while events[0].seq advances. */
  const feedBase = {
    source: { kind: "real", root: "r", slug: "s", session_id: "abc" },
    meta: { team: "t" },
  };
  const windowA = Object.assign({}, feedBase, { events: [{ seq: 6000 }, { seq: 6001 }] });
  const windowB = Object.assign({}, feedBase, { events: [{ seq: 6001 }, { seq: 6002 }] });
  const windowA2 = Object.assign({}, feedBase, { events: [{ seq: 6000 }, { seq: 6001 }] });
  check("the same run and window keep the feed key", feedKey(windowA) === feedKey(windowA2));
  check("a shifted window changes the feed key", feedKey(windowA) !== feedKey(windowB));
  check("a different run changes the feed key", feedKey(windowA) !== feedKey(Object.assign({}, windowB, {
    source: Object.assign({}, feedBase.source, { session_id: "zzz" }),
  })));

  /* Drive the real setModel cache decision with a fake Panels `this`: the
   * render surfaces are stubbed, but the sameRun/key logic is the shipped
   * code. A shifted window must rebuild even at constant event count. */
  const { Panels } = await import("./panels.js");
  let rebuilds = 0;
  const fakePanels = (cachedKey, shown) => ({
    model: shown,
    cursor: shown.events.length,
    filter: "all",
    modalTab: null,
    _nodes: { feed: {}, feedCount: null },
    _feed: {
      key: cachedKey,
      filter: "all",
      count: shown.events.length,
      matched: shown.events.length,
      rendered: shown.events.length,
      list: {},
      capNote: null,
    },
    _eventTotal: Panels.prototype._eventTotal,
    _updateFeed: Panels.prototype._updateFeed,
    _rebuildFeed: () => { rebuilds += 1; },
    _updateFeedCount: () => {},
    renderLeft: () => {},
    renderFeed: () => {},
  });
  rebuilds = 0;
  Panels.prototype.setModel.call(fakePanels(feedKey(windowA), windowB), windowB);
  check("setModel rebuilds a shifted window at constant length", rebuilds === 1);
  rebuilds = 0;
  Panels.prototype.setModel.call(fakePanels(feedKey(windowB), windowB), windowB);
  check("setModel leaves an unchanged window alone", rebuilds === 0);

  /* The launch filter label is built only from the server's echoed filters. */
  check("a project filter is named", requestedFilterLabel({ project: "My App" }) === "project My App");
  check("a session filter is named", requestedFilterLabel({ session: "abc" }) === "session abc");
  check("both filters are named", requestedFilterLabel({ project: "P", session: "s" }) === "project P and session s");
  check("no filters means no label", requestedFilterLabel({}) === "" && requestedFilterLabel(null) === "");

  /* The mode badge must agree with the screen. With no run open it is IDLE,
   * even if stale mode/status values still say live (the named-empty load). */
  const idle = modeBadge({ hasModel: false, mode: "live", followLive: true, status: "live" });
  check("no run means IDLE, not LIVE", idle.label === "● IDLE" && idle.variant === "");
  check("the demo badge is DEMO", modeBadge({
    hasModel: true, mode: "demo", followLive: false, status: "ended",
  }).label === "◆ DEMO");
  check("following a live run says LIVE", modeBadge({
    hasModel: true, mode: "live", followLive: true, status: "live",
  }).label === "● LIVE");
  check("replaying a live run says REPLAY", modeBadge({
    hasModel: true, mode: "replay", followLive: false, status: "live",
  }).label === "◼ REPLAY");
  check("an ended run says ENDED", modeBadge({
    hasModel: true, mode: "replay", followLive: false, status: "ended",
  }).label === "◼ ENDED");

  /* An empty window is zero mission time, never wall-clock elapsed time. */
  const meta = { started_at: "2026-01-01T00:00:00Z" };
  check("empty event window is 0s", missionSecondsAt({ meta, events: [] }, 0) === 0);
  check("cursor 0 is 0s", missionSecondsAt({
    meta,
    events: [{ seq: 0, ts: "2026-01-01T00:00:12Z" }],
  }, 0) === 0);

  /* A pending phase (null boundaries) must not become the current phase.
   * Number(null) is 0, which used to make every pending phase look started. */
  const staged = {
    model: {
      phases: {
        items: [
          { name: "PREPARE", family: "PREPARE", started_seq: 1, ended_seq: 5 },
          { name: "ARCHITECT", family: "ARCHITECT", started_seq: 6, ended_seq: null },
          { name: "CODE", family: "CODE", started_seq: null, ended_seq: null },
          { name: "TEST", family: "TEST", started_seq: null, ended_seq: null },
        ],
      },
    },
    events: Array.from({ length: 10 }, (_, seq) => ({ seq })),
    cursor: 3,
    phaseState: { current: "", completed: new Set() },
  };
  Stage.prototype._computePhaseState.call(staged);
  check("in PREPARE, current is PREPARE", staged.phaseState.current === "PREPARE");
  check("nothing is complete yet", staged.phaseState.completed.size === 0);

  staged.cursor = 7;
  Stage.prototype._computePhaseState.call(staged);
  check("in ARCHITECT, current is ARCHITECT", staged.phaseState.current === "ARCHITECT");
  check("PREPARE is complete past its end seq", staged.phaseState.completed.has("PREPARE"));

  if (failed.length) {
    console.error("FAILED PINS: " + failed.join("; "));
    process.exit(1);
  }
  console.log("PINS OK");
})().catch((err) => {
  console.error("PIN SCRIPT ERROR: " + (err && err.stack ? err.stack : err));
  process.exit(1);
});
"""


def test_dev_demo_fixture_matches_build_demo_model():
    """The mock fixture must be the Python demo, not an older hand copy."""
    fixture = json.loads(DEV_DEMO.read_text(encoding="utf-8"))
    expected = demo.build_demo_model()
    assert fixture == expected, (
        "visualizer/static/dev-demo.json has drifted from demo.build_demo_model(); "
        "regenerate it with json.dumps(demo.build_demo_model(), indent=1) + '\\n'"
    )


@pytest.mark.skipif(
    _NODE is None,
    reason="node executable not found on PATH; JS behaviour pins skipped "
    "(the Python dev-demo parity pin still runs)",
)
def test_js_behaviour_pins():
    """Import the pure stage/dom functions and assert the fixed behaviours."""
    assert _NODE is not None  # narrowing for type-checkers; skipif guarantees it
    proc = subprocess.run(
        [_NODE, "--input-type=module", "-e", PIN_SCRIPT],
        cwd=STATIC_JS_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"JS behaviour pins failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "PINS OK" in proc.stdout, (
        f"pin script produced no success marker:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_no_query_param_project_fallback():
    """The named-empty state comes only from the server's echoed filters.

    A `?project=` query param never narrowed the response, so trusting it
    could name a filter that was not applied — and the pick path would open
    an unrelated run instead of the empty state. The server echoes
    `filters` (server.py, since b7ecf6fb), which is the only source.
    """
    source = (STATIC_JS_DIR / "app.js").read_text(encoding="utf-8")
    assert 'params.get("project")' not in source, (
        "app.js reads a project query param; only the server's launch filters "
        "narrow the session list, and only data.filters may name them"
    )
