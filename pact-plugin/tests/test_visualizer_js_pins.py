"""Location: pact-plugin/tests/test_visualizer_js_pins.py
Summary: Regression pins for the front-end behaviours that had no coverage:
         run identity across journal revisions, journal-seq -> visible-index
         mapping, pending phases in the stage's phase state, mission time for
         an empty window, and the bundled dev-demo fixture matching
         demo.build_demo_model(). The JS pins run only when `node` is on PATH:
         they import the pure module functions, with no browser, no server and
         no build step. They skip when node is absent locally and fail under
         CI. The fixture parity pin is pure Python and always runs.
Used by: pytest (front-end regression gate for pact-plugin/visualizer).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from visualizer import demo
from visualizer_node import ci_is_declared, require_node

PLUGIN_ROOT = Path(__file__).parent.parent
STATIC_JS_DIR = PLUGIN_ROOT / "visualizer" / "static" / "js"
DEV_DEMO = PLUGIN_ROOT / "visualizer" / "static" / "dev-demo.json"

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
    pollClearsError,
    pollMayShowError,
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

  /* A successful poll clears only a banner the poll itself raised: a failed
   * navigation or refresh must survive it. */
  check("a poll clears its own banner", pollClearsError("poll") === true);
  check("a poll does not clear a navigation banner", pollClearsError("navigation") === false);
  check("a poll does not clear a refresh banner", pollClearsError("refresh") === false);
  check("a poll does not clear a load banner", pollClearsError("load") === false);
  check("a poll does not clear an untagged banner", pollClearsError("") === false);

  /* Precedence: a failing poll may take the banner only when it is empty or
   * poll-authored; navigation/refresh/load banners keep their retry. */
  check("a poll may raise a banner on a clean view", pollMayShowError("") === true);
  check("a poll may replace its own banner", pollMayShowError("poll") === true);
  check("a poll does not displace a navigation banner", pollMayShowError("navigation") === false);
  check("a poll does not displace a refresh banner", pollMayShowError("refresh") === false);
  check("a poll does not displace a load banner", pollMayShowError("load") === false);

  /* A multi-event batch must stay newest-first. Drive the real _updateFeed
   * insert path over a fake list: events 110..112 arrive over a feed whose
   * top is 109. */
  const batchModel = Object.assign({}, feedBase, {
    events: Array.from({ length: 13 }, (_, i) => ({ seq: 100 + i })),
  });
  const list = {
    nodes: [],
    get firstChild() { return this.nodes[0] || null; },
    get lastChild() { return this.nodes[this.nodes.length - 1] || null; },
    insertBefore(node, ref) {
      // The real DOM throws when the reference is not a child; the fake must
      // too, or a bad call could pass here and fail in the browser.
      if (!ref) {
        this.nodes.push(node);
        return node;
      }
      const at = this.nodes.indexOf(ref);
      if (at < 0) throw new Error("insertBefore: reference is not a child");
      this.nodes.splice(at, 0, node);
      return node;
    },
    removeChild(node) {
      const at = this.nodes.indexOf(node);
      if (at >= 0) this.nodes.splice(at, 1);
      return node;
    },
  };
  list.nodes.push({ seq: 109 });
  const batchThis = {
    model: batchModel,
    cursor: 13,
    filter: "all",
    _nodes: { feed: list, feedCount: null },
    _feed: {
      key: feedKey(batchModel),
      filter: "all",
      count: 10,
      matched: 10,
      rendered: 10,
      list,
      capNote: null,
    },
    _feedRow: (event) => ({ seq: event.seq }),
    _ensureCapNote: () => {},
    _updateFeedCount: () => {},
  };
  Panels.prototype._updateFeed.call(batchThis);
  check("a batch insert keeps the feed newest-first",
    JSON.stringify(list.nodes.map((node) => node.seq)) === JSON.stringify([112, 111, 110, 109]));

  /* Drive the REAL _rebuildFeed against a fake feed node. This pins both the
   * cache key assignment (a regression to runKey would rebuild every poll)
   * and the newest-first row order of a full rebuild. */
  const makeFeedNode = () => {
    const nodes = [];
    return {
      get firstChild() { return nodes[0] || null; },
      get lastChild() { return nodes[nodes.length - 1] || null; },
      appendChild(node) { nodes.push(node); return node; },
      insertBefore(node, ref) {
        // Same contract as the real DOM: a non-child reference throws.
        if (!ref) {
          nodes.push(node);
          return node;
        }
        const at = nodes.indexOf(ref);
        if (at < 0) throw new Error("insertBefore: reference is not a child");
        nodes.splice(at, 0, node);
        return node;
      },
      removeChild(node) {
        const at = nodes.indexOf(node);
        if (at >= 0) nodes.splice(at, 1);
        return node;
      },
      nodes,
    };
  };
  const rebuiltFeed = makeFeedNode();
  const rebuildThis = {
    model: batchModel,
    cursor: 13,
    filter: "all",
    _nodes: { feed: rebuiltFeed, feedCount: null },
    _feed: { key: null, filter: "all", count: 0, matched: 0, rendered: 0, list: null, capNote: null },
    _feedRow: (event) => ({ seq: event.seq }),
    _ensureCapNote: () => {},
    _updateFeedCount: () => {},
  };
  Panels.prototype._rebuildFeed.call(rebuildThis, rebuiltFeed);
  check("a rebuild renders the feed newest-first",
    JSON.stringify(rebuiltFeed.nodes.map((node) => node.seq)) === JSON.stringify(
      [112, 111, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
    ));
  check("a rebuild keys the cache by the visible window",
    rebuildThis._feed.key === feedKey(rebuildThis.model));

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


# app.js wires the DOM at import time, so its orchestration is driven through a
# harness: the real app.js and dom.js are copied next to stub view modules and
# a stub api.js whose calls return promises the scenario resolves by hand. The
# fake DOM implements only what app.js touches.
APP_STUBS = {
    "stubs.js": r"""
export const log = { stage: [], urls: [], toasts: [], notices: [], modes: [] };
export const pending = { session: [], demo: [], sessions: [] };
function deferred(kind, args) {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  pending[kind].push({ args, resolve, reject });
  return promise;
}
export const api = {
  sessionsData: null,
  sessionsDeferred: false,
  sessions: (args) => (api.sessionsDeferred ? deferred("sessions", args) : Promise.resolve(api.sessionsData)),
  session: (args) => deferred("session", args),
  demo: (args) => deferred("demo", args || {}),
  health: async () => ({ demo_available: true }),
};
""",
    "api.js": r"""
import { api } from "./stubs.js";
export const USE_MOCK = false;
export const sessions = (...a) => api.sessions(...a);
export const session = (...a) => api.session(...a);
export const demo = (...a) => api.demo(...a);
export const health = (...a) => api.health(...a);
""",
    "stage.js": r"""
import { log } from "./stubs.js";
export class Stage {
  setModel(m) { log.stage.push(m ? `${m.source.kind}:${m.source.session_id}:${m.etag}` : "null"); }
  setCursor() {}
  setSelectedAgent() {}
  resize() {}
}
""",
    "panels.js": r"""
import { log } from "./stubs.js";
export class Panels {
  setModel() {} setCursor() {} setSelectedAgent() {} setLive() {}
  toast(t) { log.toasts.push(t.title); }
  setNotice(n) { log.notices.push(n); }
  openDebrief() {} closeModal() {}
}
""",
    "timeline.js": r"""
import { log } from "./stubs.js";
export function createTimeline() {
  return { setModel() {}, setCursor() {}, setMode(m) { log.modes.push(Object.assign({}, m)); } };
}
""",
    "audio.js": "export function createAudio() { return { setEnabled() {}, play() {} }; }\n",
    "achievements.js": "export function createAchievements() { return { evaluate() {} }; }\n",
}

APP_PRELUDE = r"""
import { log, pending, api } from "./stubs.js";
const failed = [];
const check = (name, ok) => { if (!ok) failed.push(name); };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function fakeEl(id, tag = "DIV") {
  const listeners = {};
  const classes = new Set();
  const node = {
    id, tagName: tag, hidden: false, textContent: "", className: "", value: "",
    options: [], children: [], style: {}, dataset: {}, attrs: {},
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, on) => { const v = on === undefined ? !classes.has(c) : Boolean(on); if (v) classes.add(c); else classes.delete(c); return v; },
    },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, extra = {}) {
      const event = Object.assign({ target: node, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, extra);
      for (const fn of listeners[type] || []) fn(event);
      return event;
    },
    appendChild(child) { this.children.push(child); if (child.tagName === "OPTION") this.options.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); this.options = this.options.filter((o) => o !== child); return child; },
    get firstChild() { return this.children[0] || null; },
    getBoundingClientRect() { return { height: 64, width: 100 }; },
    closest() { return null; },
    focus() { globalThis.document.activeElement = node; },
  };
  return node;
}
const byId = new Map();
const windowListeners = {};
globalThis.document = {
  title: "",
  activeElement: null,
  getElementById(id) { if (!byId.has(id)) byId.set(id, fakeEl(id)); return byId.get(id); },
  createElement(tag) { return fakeEl("", tag.toUpperCase()); },
  createTextNode(text) { return { nodeType: 3, textContent: text }; },
  body: fakeEl("body", "BODY"),
  documentElement: fakeEl("html", "HTML"),
  addEventListener() {},
};
globalThis.document.documentElement.style.setProperty = () => {};
globalThis.Node = Object;
globalThis.window = {
  location: { search: globalThis.__SEARCH__ || "", hash: globalThis.__HASH__ || "", pathname: "/" },
  addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
};
globalThis.fireWindow = (type, extra) => {
  const event = Object.assign({ defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, extra);
  for (const fn of windowListeners[type] || []) fn(event);
  return event;
};
globalThis.history = { replaceState(_s, _t, url) { log.urls.push(url); } };
const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
globalThis.requestAnimationFrame = () => 0;
const intervals = [];
globalThis.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };
globalThis.clearInterval = (id) => { if (intervals[id - 1]) intervals[id - 1].cleared = true; };
const activeInterval = () => [...intervals].reverse().find((entry) => !entry.cleared) || null;

const model = (kind, sid, etag, status = "ended") => ({
  source: { kind, session_id: sid },
  meta: { status, started_at: "2026-11-01T00:00:00Z" },
  events: [{ seq: 1, ts: "2026-11-01T00:00:01Z", category: "session" }],
  etag,
});
const ended = { root: "r", slug: "p", session_id: "S1", is_live: false, status: "ended", updated_at: "2026-11-01" };
const live = { root: "r", slug: "p", session_id: "L1", is_live: true, status: "live", updated_at: "2026-11-02" };
"""

APP_EPILOGUE = r"""
if (failed.length) {
  console.error("FAILED PINS: " + failed.join("; "));
  process.exit(1);
}
console.log("PINS OK");
"""


def _run_app_scenario(scenario: str, tmp_path: Path) -> None:
    """Run one app.js scenario under the harness and assert it passed."""
    node = _require_node()
    for name in ("app.js", "dom.js"):
        shutil.copy(STATIC_JS_DIR / name, tmp_path / name)
    for name, body in APP_STUBS.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    (tmp_path / "harness.js").write_text(APP_PRELUDE + scenario + APP_EPILOGUE, encoding="utf-8")
    proc = subprocess.run([node, "harness.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "PINS OK" in proc.stdout, (
        f"app.js scenario failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_a_slow_demo_never_overwrites_a_later_session(tmp_path):
    """openDemo checks its own request token after its await."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();
const loading = document.getElementById("loading");
const select = document.getElementById("session-select");

/* DEMO, then a session; the session answers first, the demo last. */
document.getElementById("btn-demo").fire("click");
await tick();
select.value = "r::p::S1";
select.fire("change");
await tick();
pending.session.shift().resolve(model("real", "S1", "e1"));
await tick();
pending.demo.shift().resolve(model("demo", "demo", "demo-v1"));
await tick();
check("the later session stays on screen", log.stage.at(-1) === "real:S1:e1");
check("the URL names the session, not the demo", !String(log.urls.at(-1)).includes("demo=1"));

/* DEMO, then a session; the demo answers first: it is dropped and the
 * overlay stays up for the pending session. */
document.getElementById("btn-demo").fire("click");
await tick();
select.value = "r::p::S1";
select.fire("change");
await tick();
const before = log.stage.length;
pending.demo.shift().resolve(model("demo", "demo", "demo-v1"));
await tick();
check("a superseded demo does not render", log.stage.length === before);
check("a superseded demo leaves the overlay to the pending load", loading.hidden === false);
pending.session.shift().resolve(model("real", "S1", "e2"));
await tick();
check("the pending session renders", log.stage.at(-1) === "real:S1:e2");
check("the session load clears the overlay", loading.hidden === true);
""", tmp_path)


def test_a_refresh_does_not_reopen_a_run_after_a_newer_selection(tmp_path):
    """refreshAll drops the intent it captured once a view switch happened."""
    _run_app_scenario(r"""
const other = { root: "r", slug: "p", session_id: "S2", is_live: false, status: "ended", updated_at: "2026-10-01" };
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended, other], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();

/* Refresh starts; the list answer is held while the user picks S2. */
api.sessionsDeferred = true;
document.getElementById("btn-refresh").fire("click");
await tick();
const select = document.getElementById("session-select");
select.value = "r::p::S2";
select.fire("change");
await tick();
pending.sessions.shift().resolve(api.sessionsData);
await tick();
check("the refresh opens nothing after the newer selection", pending.session.length === 1);
pending.session.shift().resolve(model("real", "S2", "s2"));
await tick();
check("the newer selection is shown", log.stage.at(-1) === "real:S2:s2");
""", tmp_path)


def test_a_failed_refresh_does_not_banner_over_a_newer_selection(tmp_path):
    """The refresh and root-change catch arms are gated like their success arms."""
    _run_app_scenario(r"""
const other = { root: "r", slug: "p", session_id: "S2", is_live: false, status: "ended", updated_at: "2026-10-01" };
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended, other], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();
const banner = document.getElementById("error-banner");
const select = document.getElementById("session-select");
banner.hidden = true;
api.sessionsDeferred = true;

/* Refresh, then a newer selection, then the refresh's list call fails. */
document.getElementById("btn-refresh").fire("click");
await tick();
select.value = "r::p::S2";
select.fire("change");
await tick();
pending.sessions.shift().reject(new Error("boom"));
await tick();
check("a superseded refresh failure shows no banner", banner.hidden === true);
pending.session.shift().resolve(model("real", "S2", "s2"));
await tick();

/* The same for a root change. */
const root = document.getElementById("root-select");
root.value = "r";
root.fire("change");
await tick();
select.value = "r::p::S1";
select.fire("change");
await tick();
pending.sessions.shift().reject(new Error("boom"));
await tick();
check("a superseded root-change failure shows no banner", banner.hidden === true);

/* Control: with no newer selection, a failed refresh still banners. */
pending.session.shift().resolve(model("real", "S1", "e1"));
await tick();
document.getElementById("btn-refresh").fire("click");
await tick();
pending.sessions.shift().reject(new Error("boom"));
await tick();
check("an unsuperseded refresh failure still banners", banner.hidden === false);
""", tmp_path)


def test_a_stale_poll_never_overwrites_a_reopened_run(tmp_path):
    """A poll in flight when the same run is reopened is dropped."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [live], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "L1", "e1", "live"));
await tick();
const poll = activeInterval();
check("an open live run polls", Boolean(poll));
poll.fn();
await tick();
check("the poll is in flight", pending.session.length === 1);
const stalePoll = pending.session.shift();

/* Refresh reopens the same run while the older poll is still out. */
document.getElementById("btn-refresh").fire("click");
await tick();
pending.session.shift().resolve(model("real", "L1", "e3", "live"));
await tick();
stalePoll.resolve(model("real", "L1", "e2", "live"));
await tick();
check("the reopened run keeps its newer journal", log.stage.at(-1) === "real:L1:e3");
""", tmp_path)


def test_shortcuts_leave_handled_keys_chords_and_the_dialog_alone(tmp_path):
    """The global keydown acts only on keys nothing else handled."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();
const playing = () => Boolean(log.modes.length && log.modes.at(-1).playing);
const row = { tagName: "DIV" };
fireWindow("keydown", { key: " ", code: "Space", target: row, defaultPrevented: true });
check("Space already handled by a row does not toggle playback", !playing());
fireWindow("keydown", { key: "d", target: row, ctrlKey: true });
fireWindow("keydown", { key: "d", target: row, metaKey: true });
check("a Ctrl/Cmd chord does not open the demo", pending.demo.length === 0);
document.getElementById("modal-root").hidden = false;
fireWindow("keydown", { key: " ", code: "Space", target: row });
fireWindow("keydown", { key: "d", target: row });
check("an open dialog blocks the page shortcuts", !playing() && pending.demo.length === 0);
document.getElementById("modal-root").hidden = true;
fireWindow("keydown", { key: " ", code: "Space", target: row });
check("an unhandled Space still toggles playback", playing());
""", tmp_path)


TOAST_SCRIPT = r"""
const failed = [];
const check = (name, ok) => { if (!ok) failed.push(name); };
const timers = new Map();
let nextTimer = 1;
globalThis.setTimeout = (fn) => { const id = nextTimer++; timers.set(id, fn); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); };
const flush = () => { const due = [...timers.values()]; timers.clear(); for (const fn of due) fn(); };
function node(tag) {
  const listeners = {};
  return {
    tagName: tag.toUpperCase(), children: [], attrs: {}, parentNode: null, style: {}, dataset: {},
    className: "", textContent: "",
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type) { for (const fn of listeners[type] || []) fn({ preventDefault() {}, key: "" }); },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; },
    contains(other) { return other === this || this.children.some((c) => c.contains && c.contains(other)); },
    focus() { globalThis.document.activeElement = this; },
  };
}
const layout = node("main");
globalThis.document = {
  activeElement: null,
  createElement: node,
  createTextNode: (text) => ({ textContent: text }),
  getElementById: (id) => (id === "layout" ? layout : null),
};
globalThis.Node = Object;
(async () => {
  const { Panels } = await import("./panels.js");
  const root = node("div");
  const host = { toastRoot: root, toasts: [], _toastTimers: new Map(), _dismissToast: Panels.prototype._dismissToast };
  const card = Panels.prototype.toast.call(host, { title: "Hi", body: "there", kind: "info" });
  check("a toast card carries no nested live role", card.attrs.role === undefined);
  card.fire("mouseenter");
  flush();
  check("a hovered toast is not dismissed by its timer", card.parentNode === root);
  card.fire("mouseleave");
  card.focus();
  card.fire("focusin");
  flush();
  check("a focused toast is not dismissed by its timer", card.parentNode === root);
  card.fire("focusout");
  card.focus();
  flush();
  check("a released toast is dismissed by its timer", card.parentNode === null);
  check("dismissing the focused toast moves focus to the layout", document.activeElement === layout);
  if (failed.length) { console.error("FAILED PINS: " + failed.join("; ")); process.exit(1); }
  console.log("PINS OK");
})().catch((err) => { console.error("PIN SCRIPT ERROR: " + (err && err.stack ? err.stack : err)); process.exit(1); });
"""


def test_toasts_hold_for_the_reader_and_keep_focus():
    """Hover or focus pauses a toast; dismissing a focused toast keeps focus."""
    node = _require_node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", TOAST_SCRIPT],
        cwd=STATIC_JS_DIR, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and "PINS OK" in proc.stdout, (
        f"toast pins failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_dev_demo_fixture_matches_build_demo_model():
    """The mock fixture must be the Python demo, not an older hand copy."""
    fixture = json.loads(DEV_DEMO.read_text(encoding="utf-8"))
    expected = demo.build_demo_model()
    assert fixture == expected, (
        "visualizer/static/dev-demo.json has drifted from demo.build_demo_model(); "
        "regenerate it with json.dumps(demo.build_demo_model(), indent=1) + '\\n'"
    )


def _require_node() -> str:
    return require_node("the JS behaviour pins (the Python dev-demo parity pin still runs)")


def test_js_behaviour_pins():
    """Import the pure stage/dom functions and assert the fixed behaviours."""
    node = _require_node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", PIN_SCRIPT],
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


def test_poll_path_uses_the_banner_rules():
    """The pure banner rules only matter if the poll path calls them.

    app.js is not importable without a DOM, so the behaviour pins never reach
    this wiring; a future edit could drop either call and leave the rules
    green while the poll steals or erases another banner.
    """
    source = (STATIC_JS_DIR / "app.js").read_text(encoding="utf-8")
    # Narrow to the pollLive body and drop comments, so a call moved elsewhere
    # or commented out cannot satisfy the pin. The patterns stay
    # whitespace-tolerant: reindentation or a wrapped argument must still pass.
    start = source.index("function pollLive(")
    body = source[start:source.index("\n}\n", start)]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    # Three assertions, each with a mutation that must redden it:
    # (a) the clear guard's polarity and action, (b) the show guard's
    # polarity, (c) the source literal on the poll error banner.
    assert re.search(
        r"if\s*\(\s*state\.lastError\s*&&\s*pollClearsError\s*\("
        r"\s*state\.errorSource\s*\)\s*\)\s*hideError\s*\(\s*\)",
        body,
    ), (
        "pollLive no longer clears with the positive "
        "'state.lastError && pollClearsError(state.errorSource)' guard; a "
        "negated or weakened guard changes when a banner is erased"
    )
    assert re.search(
        r"if\s*\(\s*pollMayShowError\s*\(\s*state\.errorSource\s*\)\s*\)",
        body,
    ), (
        "pollLive no longer shows with the positive "
        "'pollMayShowError(state.errorSource)' guard; a negated guard changes "
        "banner precedence"
    )
    assert re.search(
        r'showError\s*\(\s*`Live poll failed[^`]*`\s*,\s*pollLive\s*,\s*"poll"\s*\)',
        body,
    ), (
        "the poll error banner is no longer tagged with source \"poll\"; "
        "another source changes which banner a poll may clear or displace"
    )


# The node gate itself. A truthiness check on the environment reads CI=false
# as CI, so the falsy spellings are listed as inputs: only they separate a
# correct predicate from a bare `os.environ.get`.
_NOT_CI = ["", "0", "false", "no", "False", "NO", "  false  "]
_CI = ["true", "1", "yes", "TRUE", "on"]


def _clear_ci(monkeypatch):
    for name in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
@pytest.mark.parametrize("value", _NOT_CI)
def test_falsy_ci_spellings_are_not_ci(monkeypatch, variable, value):
    _clear_ci(monkeypatch)
    monkeypatch.setenv(variable, value)
    assert ci_is_declared() is False, f"{variable}={value!r} means NOT CI"


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
@pytest.mark.parametrize("value", _CI)
def test_other_ci_values_are_ci(monkeypatch, variable, value):
    _clear_ci(monkeypatch)
    monkeypatch.setenv(variable, value)
    assert ci_is_declared() is True, f"{variable}={value!r} means CI"


def test_missing_node_skips_when_ci_is_false(monkeypatch):
    import visualizer_node

    _clear_ci(monkeypatch)
    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(visualizer_node.shutil, "which", lambda name: None)
    with pytest.raises(pytest.skip.Exception):
        require_node("the probe")


def test_missing_node_fails_under_ci(monkeypatch):
    import visualizer_node

    _clear_ci(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(visualizer_node.shutil, "which", lambda name: None)
    with pytest.raises(pytest.fail.Exception):
        require_node("the probe")
