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
  check("an unfiltered empty root clears the open run", rootChangeAction({
    hasSession: false, requestedFilter: "", hasRun: true,
  }) === "empty");
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

  /* A reader off the live head stays on the same event across a poll, by
   * seq, even when the capped window slides. */
  const { cursorAfterRefresh } = await import("./dom.js");
  const win = (from, n) => Array.from({ length: n }, (_, i) => ({ seq: from + i }));
  check("a slid window keeps the reader on the same event",
    cursorAfterRefresh(win(100, 10), 4, win(103, 10)) === 1);
  check("an unslid window keeps the same index", cursorAfterRefresh(win(100, 10), 4, win(100, 12)) === 4);
  check("an event that slid out lands at the window start", cursorAfterRefresh(win(100, 10), 2, win(105, 10)) === 0);

  /* Lifecycle at the cursor: a session_start after a session_end reopens the
   * run, and only a wrap-up session_end at or before the cursor is the wrap-up. */
  const life = (evs, cursor) => Stage.prototype._lifecycleAtCursor.call({ events: evs, cursor });
  const bare = { type: "session_end", raw: {} };
  const wrap = { type: "session_end", wrapup: true, raw: { source: "wrap-up", pr_state: "merged", pr_number: 9 } };
  const start = { type: "session_start", raw: {} };
  check("a bare session_end ends the run at the cursor", life([start, bare], 2).endIdx === 1);
  check("a later session_start reopens the run", life([start, bare, start], 3).endIdx === -1);
  check("any event other than an end trailer reopens the run",
    life([start, bare, { type: "session_consolidated" }], 3).endIdx === -1);
  check("end trailers keep the run ended",
    life([start, bare, { type: "cleanup_summary" }, bare], 4).endIdx === 3);
  check("a bare session_end is not a wrap-up", life([start, bare], 2).wrapup === null);
  check("a wrap-up after the cursor is not shown yet", life([start, bare, wrap], 2).wrapup === null);
  const shown = life([start, wrap], 2).wrapup;
  check("a wrap-up at the cursor carries its own PR", shown && shown.pr_state === "MERGED" && shown.pr_number === 9);

  /* Only a wrap-up session_end fires the SHIPPED stamp. */
  const spawned = [];
  const spawnThis = {
    model: { meta: {} }, phaseState: { current: "" }, agents: new Map(), reducedMotion: false,
    _phaseRoomForEvent: Stage.prototype._phaseRoomForEvent,
    _agentPoint: () => null, _roomPoint: () => ({ x: 0, y: 0 }),
    _pushEffect: (effect) => spawned.push(effect.kind),
    _makeConfetti: () => ({ kind: "confetti" }), _addShake: () => {},
  };
  Stage.prototype._spawnForEvent.call(spawnThis, bare);
  check("a bare session_end fires no stamp", !spawned.includes("stamp"));
  Stage.prototype._spawnForEvent.call(spawnThis, wrap);
  check("a wrap-up session_end fires the stamp and, when merged, confetti",
    spawned.includes("stamp") && spawned.includes("confetti"));

  /* The launch URL's selection. */
  const { launchSelection } = await import("./dom.js");
  check("?demo=1 is the demo", launchSelection("?demo=1").kind === "demo");
  check("root, slug and session_id are the deep link",
    JSON.stringify(launchSelection("?root=r&slug=s&session_id=x")) === JSON.stringify({ kind: "deep", root: "r", slug: "s", session_id: "x" }));
  check("?session= selects by id and keeps the project",
    JSON.stringify(launchSelection("?session=abcd1234&project=p").query) === JSON.stringify({ project: "p", session: "abcd1234" })
    && launchSelection("?session=abcd1234").kind === "session");
  check("?project= selects the project", launchSelection("?project=p").kind === "project");
  check("no query is the default", launchSelection("").kind === "default");

  /* Past the event cap the window slides at constant length; the stage must
   * still animate what is new, found by seq. */
  const animated = [];
  const slid = Object.assign(Object.create(Stage.prototype), {
    model: {}, reducedMotion: false, effects: [],
    events: Array.from({ length: 6000 }, (_, i) => ({ seq: i })),
    cursor: 6000, cursorSeq: 5999,
    _computePhaseState() {}, _syncAgents() {}, _updateChrome() {},
    _spawnForEvent: (event) => animated.push(event.seq),
  });
  slid.events = Array.from({ length: 6000 }, (_, i) => ({ seq: i + 2 }));
  slid.setCursor(6000, { animate: true, live: true });
  check("a slid window animates its new events by seq",
    JSON.stringify(animated) === JSON.stringify([6000, 6001]));
  animated.length = 0;
  slid.setCursor(6000, { animate: true, live: true });
  check("an unchanged window animates nothing", animated.length === 0);

  /* Review deck: a reviewer named by a review_dispatch stands on the deck
   * until it acts; a review-family event keeps it there; a room event moves
   * it to that room; an OTHER-family event keeps the last room. */
  const zones = (events, cursor) => {
    const host = Object.assign(Object.create(Stage.prototype), {
      roster: [{ name: "reviewer-1", role: "security-engineer" }],
      events, cursor, phaseState: { current: "", completed: new Set() },
    });
    return host._computeViews()[0];
  };
  const dispatch = { type: "review_dispatch", category: "review", agents: ["reviewer-1"], phase_family: "REVIEW" };
  check("a dispatched reviewer stands on the deck before it acts", zones([dispatch], 1).zone === "review");
  check("a finding keeps it on the deck even when it inherits TEST",
    zones([dispatch, { type: "review_finding", agent: "reviewer-1", category: "finding", phase_family: "TEST" }], 2).zone === "review");
  check("a dispatch that inherits TEST still seats its reviewers on the deck",
    zones([Object.assign({}, dispatch, { phase_family: "TEST" })], 1).zone === "review");
  check("a room event moves it to that room",
    (() => { const v = zones([dispatch, { type: "commit", agent: "reviewer-1", phase_family: "CODE" }], 2); return v.zone === "room" && v.family === "CODE"; })());
  check("an OTHER-family event keeps the last room",
    zones([dispatch, { type: "commit", agent: "reviewer-1", phase_family: "CODE" }, { type: "checkpoint", agent: "reviewer-1", phase_family: "OTHER" }], 3).family === "CODE");

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
export const log = { stage: [], urls: [], toasts: [], notices: [], modes: [], selected: [], stageOpts: null };
export const pending = { session: [], demo: [], sessions: [] };
function deferred(kind, args) {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  pending[kind].push({ args, resolve, reject });
  return promise;
}
export const api = {
  key: "k-test",
  keyRejected: null,
  sessionsData: null,
  sessionsDeferred: false,
  sessionsCalls: [],
  healthCalls: 0,
  sessions: (args) => {
    api.sessionsCalls.push(Object.assign({}, args));
    return api.sessionsDeferred ? deferred("sessions", args) : Promise.resolve(api.sessionsData);
  },
  session: (args) => deferred("session", args),
  demo: (args) => deferred("demo", args || {}),
  health: async () => { api.healthCalls += 1; return { demo_available: true }; },
};
""",
    "api.js": r"""
import { api } from "./stubs.js";
export const USE_MOCK = false;
export const KEY_REQUIRED_MESSAGE = "This page needs its launch link. Run pact-viz --status to print it.";
export const takeLaunchKey = () => api.key;
export const setKeyRejectedHandler = (fn) => { api.keyRejected = fn; };
export const sessions = (...a) => api.sessions(...a);
export const session = (...a) => api.session(...a);
export const demo = (...a) => api.demo(...a);
export const health = (...a) => api.health(...a);
""",
    "stage.js": r"""
import { log } from "./stubs.js";
export class Stage {
  constructor(_canvas, opts) { log.stageOpts = opts; }
  setModel(m) { log.stage.push(m ? `${m.source.kind}:${m.source.session_id}:${m.etag}` : "null"); }
  setCursor() {}
  setSelectedAgent() {}
  resize() {}
}
""",
    "panels.js": r"""
import { log } from "./stubs.js";
export class Panels {
  setModel() {} setCursor() {} setLive() {}
  setSelectedAgent(name) { log.selected.push(name); }
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
const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
/* A scenario may set globalThis.blockStorage before importing app.js to make
 * every storage access throw, as blocked site data does. */
const storageGetter = {
  configurable: true,
  get() {
    if (globalThis.blockStorage) throw new Error("SecurityError: storage is blocked");
    return storage;
  },
};
Object.defineProperty(globalThis, "localStorage", storageGetter);
Object.defineProperty(globalThis.window, "localStorage", storageGetter);
/* The shell's overlays start hidden, as their markup declares. */
for (const id of ["modal-root", "loading", "error-banner"]) document.getElementById(id).hidden = true;
globalThis.requestAnimationFrame = () => 0;
/* Poll timers (a second or longer) are captured for the scenario to fire by
 * hand; short timers, which tick() uses, run normally. */
const realSetTimeout = setTimeout;
const realClearTimeout = clearTimeout;
const timers = [];
globalThis.setTimeout = (fn, ms) => {
  if (Number(ms) < 1000) return realSetTimeout(fn, ms);
  timers.push({ fn, ms });
  return `poll-${timers.length}`;
};
globalThis.clearTimeout = (id) => {
  if (typeof id === "string" && id.startsWith("poll-")) timers[Number(id.slice(5)) - 1].cleared = true;
  else realClearTimeout(id);
};
const pendingTimers = () => timers.filter((entry) => !entry.cleared && !entry.fired);
const firePoll = () => {
  const entry = pendingTimers().at(-1);
  if (!entry) return false;
  entry.fired = true;
  entry.fn();
  return true;
};

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


def _run_app_scenario(scenario: str, tmp_path: Path, search: str = "") -> None:
    """Run one app.js scenario under the harness and assert it passed.

    `search` is the page's location.search (the launch URL's query).
    """
    node = _require_node()
    for name in ("app.js", "dom.js"):
        shutil.copy(STATIC_JS_DIR / name, tmp_path / name)
    for name, body in APP_STUBS.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    prelude = f"globalThis.__SEARCH__ = {json.dumps(search)};\n" + APP_PRELUDE
    (tmp_path / "harness.js").write_text(prelude + scenario + APP_EPILOGUE, encoding="utf-8")
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


def test_the_live_flag_follows_only_the_live_head(tmp_path):
    """Scrubbing off the live head clears LIVE everywhere, and it stays clear."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [live], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(Object.assign(model("real", "L1", "e1", "live"), {
  events: [{ seq: 1, ts: "2026-11-01T00:00:01Z" }, { seq: 2, ts: "2026-11-01T00:00:02Z" }],
}));
await tick();
check("an open live run starts on the live head", log.modes.at(-1).live === true);
fireWindow("keydown", { key: "ArrowLeft", target: { tagName: "DIV" } });
check("after a scrub every recorder update says not live",
  log.modes.length > 0 && log.modes.at(-1).live === false);
""", tmp_path)


def test_blocked_storage_does_not_stop_the_page(tmp_path):
    """A storage access that throws leaves the sound preference at its default."""
    _run_app_scenario(r"""
globalThis.blockStorage = true;
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended], default_root: "r" };
await import("./app.js");
await tick();
check("the page still asks for the run", pending.session.length === 1);
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();
check("the run renders", log.stage.at(-1) === "real:S1:e0");
document.getElementById("btn-sound").fire("click");
check("toggling sound with blocked storage still works",
  document.getElementById("btn-sound").attrs["aria-pressed"] === "true");
""", tmp_path)


def test_a_new_run_starts_with_no_selected_agent(tmp_path):
    """A selection made in one run does not carry into the next."""
    _run_app_scenario(r"""
const other = { root: "r", slug: "p", session_id: "S2", is_live: false, status: "ended", updated_at: "2026-10-01" };
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended, other], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "S1", "e0"));
await tick();
log.stageOpts.onAgentClick("coder");
check("a click selects the agent", log.selected.at(-1) === "coder");
const select = document.getElementById("session-select");
select.value = "r::p::S2";
select.fire("change");
await tick();
pending.session.shift().resolve(model("real", "S2", "s2"));
await tick();
check("the next run starts with no selection", log.selected.at(-1) === null);
""", tmp_path)


def test_an_open_run_is_always_polled_at_its_status_cadence(tmp_path):
    """Live runs poll about every 1.2 s, others every 10 s, and a scrub keeps polling."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [live], default_root: "r" };
await import("./app.js");
await tick();
const events = (n) => Array.from({ length: n }, (_, i) => ({ seq: i, ts: "2026-11-01T00:00:01Z" }));
pending.session.shift().resolve(Object.assign(model("real", "L1", "e1", "live"), { events: events(3) }));
await tick();
check("a live run is polled about every 1.2 s", pendingTimers().at(-1).ms === 1200);
fireWindow("keydown", { key: "ArrowLeft", target: { tagName: "DIV" } });
check("scrubbing off the live head keeps polling", pendingTimers().length === 1);

/* The run ends: the page drops to replay and polls every 10 s. */
firePoll();
await tick();
pending.session.shift().resolve(Object.assign(model("real", "L1", "e2", "ended"), { events: events(4) }));
await tick();
check("an ended run says so", log.toasts.includes("Run ended"));
check("an ended run is still polled, every 10 s", pendingTimers().at(-1).ms === 10000);

/* The run resumes: the page says so and polls fast again. */
firePoll();
await tick();
pending.session.shift().resolve(Object.assign(model("real", "L1", "e3", "live"), { events: events(5) }));
await tick();
check("a resumed run says so", log.toasts.includes("Run resumed"));
check("a resumed run is polled fast again", pendingTimers().at(-1).ms === 1200);
check("only one poll is ever scheduled", pendingTimers().length === 1);
""", tmp_path)


def test_without_its_launch_key_the_page_calls_no_api(tmp_path):
    """No key: the launch-link banner, no retry, and not a single /api call."""
    _run_app_scenario(r"""
api.key = "";
api.sessionsData = { roots: [], sessions: [] };
await import("./app.js");
await tick();
const banner = document.getElementById("error-banner");
check("the launch-link banner shows", banner.hidden === false
  && document.getElementById("error-text").textContent.includes("needs its launch link"));
check("the banner offers no retry", document.getElementById("error-retry").hidden === true);
check("no /api call is made", api.sessionsCalls.length === 0 && api.healthCalls === 0
  && pending.session.length === 0 && pending.demo.length === 0);
""", tmp_path)


def test_a_401_shows_the_launch_link_banner_and_it_stays(tmp_path):
    """A rejected key replaces any banner, and later errors do not displace it."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [ended], default_root: "r" };
await import("./app.js");
await tick();
const rejected = new Error("This page needs its launch link. Run pact-viz --status to print it.");
rejected.status = 401;
api.keyRejected();
pending.session.shift().reject(rejected);
await tick();
const text = document.getElementById("error-text").textContent;
check("the 401 banner names the launch link", text.includes("needs its launch link"));
document.getElementById("btn-refresh").fire("click");
await tick();
check("a later navigation does not displace it",
  document.getElementById("error-text").textContent.includes("needs its launch link")
  && document.getElementById("error-banner").hidden === false);
""", tmp_path)


def test_the_launch_url_selects_the_run(tmp_path):
    """?project= narrows the list on the server and opens its newest live run."""
    _run_app_scenario(r"""
const older = { root: "r", slug: "p", session_id: "P1", is_live: false, status: "ended", updated_at: "2026-11-03" };
const liveRun = { root: "r", slug: "p", session_id: "P2", is_live: true, status: "live", updated_at: "2026-11-01" };
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [older, liveRun], default_root: "r", filters: { project: "p" } };
await import("./app.js");
await tick();
check("the project goes to the server", api.sessionsCalls[0] && api.sessionsCalls[0].project === "p");
check("the newest live run of the project opens", pending.session[0] && pending.session[0].args.session_id === "P2");
""", tmp_path, search="?project=p")


def test_an_ambiguous_or_unknown_session_names_the_id(tmp_path):
    """?session= opens only a single match; otherwise the error names the id."""
    _run_app_scenario(r"""
const a = { root: "r", slug: "p", session_id: "abcd1234-1", is_live: false, updated_at: "2026-11-02" };
const b = { root: "r", slug: "p", session_id: "abcd1234-2", is_live: false, updated_at: "2026-11-01" };
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [a, b], default_root: "r", filters: { session: "abcd1234" } };
await import("./app.js");
await tick();
check("the session goes to the server", api.sessionsCalls[0] && api.sessionsCalls[0].session === "abcd1234");
check("two matches open nothing", pending.session.length === 0);
check("the error names the id", document.getElementById("error-text").textContent.includes("abcd1234"));
""", tmp_path, search="?session=abcd1234")


def test_a_stale_poll_never_overwrites_a_reopened_run(tmp_path):
    """A poll in flight when the same run is reopened is dropped."""
    _run_app_scenario(r"""
api.sessionsData = { roots: [{ id: "r", label: "r" }], sessions: [live], default_root: "r" };
await import("./app.js");
await tick();
pending.session.shift().resolve(model("real", "L1", "e1", "live"));
await tick();
check("an open live run polls", firePoll());
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
document.body.classList.add("show-crew");
fireWindow("keydown", { key: "Escape", target: row, defaultPrevented: true });
check("Escape already handled by a toast leaves the drawers open", document.body.classList.contains("show-crew"));
fireWindow("keydown", { key: "Escape", target: row });
check("an unhandled Escape closes the drawers", !document.body.classList.contains("show-crew"));
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


KEY_SCRIPT = r"""
const failed = [];
const check = (name, ok) => { if (!ok) failed.push(name); };
const stored = new Map();
const replaced = [];
globalThis.history = { replaceState: (_s, _t, url) => replaced.push(url) };
globalThis.window = {
  location: { search: "?project=p", hash: "#k=secret-key-1", pathname: "/" },
  sessionStorage: { getItem: (k) => stored.get(k) || null, setItem: (k, v) => stored.set(k, v) },
};
const requests = [];
let status = 200;
globalThis.fetch = async (url, init) => {
  requests.push({ url, headers: init.headers });
  return { ok: status === 200, status, json: async () => ({ ok: status === 200, sessions: [] }) };
};
const api = await import("./api.js");
check("the fragment key is taken", api.takeLaunchKey() === "secret-key-1");
check("the key moves to this tab's storage", stored.get("pact-viz-key") === "secret-key-1");
check("the fragment is stripped and the query kept", replaced.at(-1) === "/?project=p");
await api.sessions({ project: "p", session: "abcd1234", all: true, limit: 50 });
check("every /api call sends the key", requests.at(-1).headers["X-PACT-Key"] === "secret-key-1");
check("list filters go to the server",
  requests.at(-1).url === "/api/sessions?project=p&session=abcd1234&all=1&limit=50");
let rejected = 0;
api.setKeyRejectedHandler(() => { rejected += 1; });
status = 401;
let error = null;
try { await api.session({ root: "r", slug: "s", session_id: "x" }); } catch (err) { error = err; }
check("a 401 reaches the page's handler", rejected === 1);
check("a 401 throws with its status", error && error.status === 401);
/* Without a fragment, the key comes back from this tab's storage. */
globalThis.window.location.hash = "";
check("a reload without the fragment reuses the stored key", api.takeLaunchKey() === "secret-key-1");
if (failed.length) { console.error("FAILED PINS: " + failed.join("; ")); process.exit(1); }
console.log("PINS OK");
"""


def test_api_carries_the_launch_key():
    """The fragment key is stored, stripped, and sent on every /api call."""
    node = _require_node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", KEY_SCRIPT],
        check=False, cwd=STATIC_JS_DIR, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and "PINS OK" in proc.stdout, (
        f"key pins failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


TASKS_SCRIPT = r"""
const failed = [];
const check = (name, ok) => { if (!ok) failed.push(name); };
function node(tag) {
  return {
    tagName: tag.toUpperCase(), children: [], attrs: {}, style: {}, dataset: {}, className: "", textContent: "",
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener() {},
    appendChild(child) { this.children.push(child); return child; },
  };
}
globalThis.document = { createElement: node, createTextNode: (t) => ({ textContent: t, children: [] }) };
globalThis.Node = Object;
const walk = (n, out = []) => { out.push(n); for (const c of n.children || []) walk(c, out); return out; };
const texts = (n) => walk(n).map((x) => x.textContent).filter(Boolean);
(async () => {
  const { Panels } = await import("./panels.js");
  const host = Object.create(Panels.prototype);
  const card = (task) => host._taskCard(task);
  const gate = card({ id: "7", key: "7:architect", kind: "gate", status: "completed", subject: "TEACHBACK" });
  check("a gate card is tagged GATE", texts(gate).includes("GATE"));
  check("the card identity is the task key", gate.dataset.taskKey === "7:architect");
  check("a blocker card is marked", texts(card({ id: "9", key: "9:", kind: "blocker", status: "pending" })).includes("BLOCKER"));
  check("an algedonic card is marked", texts(card({ id: "9", key: "9:", kind: "algedonic", status: "pending" })).includes("ALGEDONIC"));
  host.model = { tasks: ["pending", "active", "handoff", "completed"].map((status, i) => ({ id: String(i), key: `${i}:`, status })), metrics: {} };
  const columns = walk(host._tasksTab()).filter((n) => n.className === "task-col-label").map((n) => n.textContent);
  check("the board has one column per closed status",
    JSON.stringify(columns) === JSON.stringify(["ACTIVE", "HANDOFF", "PENDING", "COMPLETED"]));
  const halt = host._alertItem({ kind: "algedonic", level: "halt", category: "Security", category_key: "security", summary: "s", resolved: false });
  const level = walk(halt).find((n) => String(n.className).startsWith("alert-level"));
  check("a halt is styled as danger", level.className.includes("is-alert") && level.textContent === "HALT");
  check("the category is shown as written", texts(halt).includes("Security"));
  check("an unresolved alert says so", texts(halt).includes("Unresolved"));
  const done = host._alertItem({ kind: "blocker", level: "alert", category: "blocker", summary: "s", resolved: true, resolution: "fixed in 3" });
  const doneLevel = walk(done).find((n) => String(n.className).startsWith("alert-level"));
  check("an alert-level alert is styled as a warning", doneLevel.className.includes("is-warn"));
  check("a resolved alert shows its resolution", texts(done).includes("Resolved: fixed in 3"));
  host.model = { tasks: [{ id: "7", key: "7:architect", status: "handoff" }], metrics: {} };
  const link = host._taskCardLink("7:architect", "TASK #7", "handoff-task");
  check("a reference to a card on the board is a button", link.tagName === "BUTTON");
  check("a reference to a card not on the board is plain text",
    host._taskCardLink("9:coder", "TASK #9", "handoff-task").tagName === "SPAN"
    && host._taskCardLink("", "unattached", "handoff-task").tagName === "SPAN");
  let rendered = 0;
  host._renderModal = () => { rendered += 1; };
  host._showTaskCard("7:architect");
  check("following the link opens the board on that card",
    host.modalTab === "tasks" && host._pendingTaskKey === "7:architect" && rendered === 1);
  const handoff = host._handoffCard({ agent: "architect", task_id: "7", task_key: "7:architect", fields: {} });
  const kept = host._alertItem({ kind: "blocker", level: "alert", category: "blocker", summary: "capped out", task_id: "12", task_key: "12:coder", status: "pending", resolved: false });
  check("an alert whose card is not on the board stays, with its text and no link",
    texts(kept).includes("capped out") && texts(kept).includes("task #12") && texts(kept).includes("pending")
    && !walk(kept).some((n) => n.tagName === "BUTTON"));
  const linked = host._alertItem({ kind: "blocker", level: "alert", category: "blocker", summary: "s", task_id: "7", task_key: "7:architect", resolved: false });
  check("an alert whose card is on the board links to it", walk(linked).some((n) => n.tagName === "BUTTON"));
  check("a handoff links to its card", walk(handoff).some((n) => n.tagName === "BUTTON" && n.textContent.startsWith("TASK #7")));
  if (failed.length) { console.error("FAILED PINS: " + failed.join("; ")); process.exit(1); }
  console.log("PINS OK");
})().catch((err) => { console.error("PIN SCRIPT ERROR: " + (err && err.stack ? err.stack : err)); process.exit(1); });
"""


def test_task_board_and_alerts_follow_the_payload_shape():
    """Cards carry their key and kind tag; columns are the closed status set;
    alerts style by level and show category and resolution as written."""
    node = _require_node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", TASKS_SCRIPT],
        check=False, cwd=STATIC_JS_DIR, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and "PINS OK" in proc.stdout, (
        f"task and alert pins failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
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


def test_the_named_empty_label_comes_only_from_the_server_echo():
    """The URL's project or session goes to the server, which echoes the
    filters it applied; only that echo may name an empty selection, so the
    page never names a filter the response was not narrowed by.
    """
    source = (STATIC_JS_DIR / "app.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    assignments = re.findall(r"state\.requestedFilter\s*=(?!=)\s*([^;]+);", code)
    assert assignments == ["requestedFilterLabel(data && data.filters)"], (
        f"state.requestedFilter is set from something other than the server's echo: {assignments}"
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
