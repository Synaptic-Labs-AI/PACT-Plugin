/* PACT Mission Control — the floor.
 *
 * A 960 x 440 logical pixel-art control room rendered on one canvas:
 *
 *   sky / control tower (lead)
 *   four rooms: PREPARE library, ARCHITECT studio, CODE workshop (conveyor),
 *               TEST range
 *   plaza: standby lounge, review deck, gate turnstiles, wrap-up board
 *
 * `setModel` + `setCursor` are the only state inputs. `cursor` is the count
 * of events visible so far (0..events.length), matching app.js, panels.js and
 * timeline.js; positions, room assignments and effects are derived from
 * `model.events.slice(0, cursor)`, so replaying the recorder walks the same
 * crew through the same rooms.
 *
 * Public surface (see CONTRACT.md):
 *   new Stage(canvas, {onAgentClick, onRoomClick, onMascotClick})
 *   setModel(model) / setCursor(index, {animate, live})
 *   setSelectedAgent(name) / setReducedMotion(bool) / resize() / destroy()
 *
 * Rules: hard rectangles, no images, no external requests, no markup
 * injection. The caption element and the canvas aria-label are the only DOM
 * this module touches.
 */

import { runKey, phasePasses, isWrapupEvent as isWrapup, wrapupFromEvent as wrapupOf } from "./dom.js";
import {
  SPRITE_W,
  SPRITE_H,
  drawAgent,
  drawMascot,
  disableSmoothing,
  paletteFor,
  colorForSeed,
  normalizeRole,
  rgba,
} from "./sprites.js";

/* ---- layout constants -------------------------------------------------- */

const LOGICAL_W = 960;
const LOGICAL_H = 440;

const ROOM_TOP = 190;
const ROOM_FLOOR = 300;
const CORRIDOR_Y = 324;
const PLAZA_TOP = 340;
const GROUND_FLOOR = 432;
const TOWER_X = 480;
const TOWER_DECK_Y = 176;

const PHASE_ORDER = ["PREPARE", "ARCHITECT", "CODE", "TEST"];

const PHASE_COLORS = {
  PREPARE: "#2dd4bf",
  ARCHITECT: "#60a5fa",
  CODE: "#fbbf24",
  TEST: "#f472b6",
};

const ROOM_DEFS = PHASE_ORDER.map((family, index) => ({
  family,
  index,
  x: 28 + index * 228,
  w: 220,
  top: ROOM_TOP,
  floor: ROOM_FLOOR,
  color: PHASE_COLORS[family],
}));

const ROOM_BY_FAMILY = {};
for (const room of ROOM_DEFS) ROOM_BY_FAMILY[room.family] = room;

const REVIEW_DECK = { x: 336, w: 284, top: 344, floor: 432 };
const GATES = { x: 648, w: 152, top: 344, floor: 432 };
const PR_BOARD = { x: 812, w: 124, top: 336, h: 56 };
const LOUNGE = { x: 24, w: 276, top: 344, floor: 432 };

const ROOM_AGENT_Y = ROOM_FLOOR - 1;
const PLAZA_AGENT_Y = GROUND_FLOOR - 1;

const ROOM_SLOT_X = [58, 104, 150, 196];
const LOUNGE_SLOTS = [
  { x: 58, y: PLAZA_AGENT_Y }, { x: 108, y: PLAZA_AGENT_Y },
  { x: 158, y: PLAZA_AGENT_Y }, { x: 208, y: PLAZA_AGENT_Y },
  { x: 258, y: PLAZA_AGENT_Y },
  { x: 83, y: PLAZA_AGENT_Y - 19 }, { x: 133, y: PLAZA_AGENT_Y - 19 },
  { x: 183, y: PLAZA_AGENT_Y - 19 }, { x: 233, y: PLAZA_AGENT_Y - 19 },
];
const REVIEW_SLOT_X = [374, 424, 474, 524];
const TOWER_SLOTS = [
  { x: TOWER_X - 20, y: TOWER_DECK_Y },
  { x: TOWER_X + 20, y: TOWER_DECK_Y },
  { x: TOWER_X, y: TOWER_DECK_Y - 4 },
];


const FONT_STACK = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';
const ACCENT = "#a3e635";

const C = {
  ink: "#0b1220",
  bg: "#070b16",
  bg2: "#0a1020",
  panel: "#0e1628",
  panel2: "#131f36",
  line: "#24324f",
  lineBright: "#38507a",
  text: "#e8eefc",
  muted: "#8fa0c4",
  dim: "#5f7096",
  success: "#4ade80",
  warning: "#fbbf24",
  danger: "#fb7185",
  info: "#7dd3fc",
  metal: "#9fb2d4",
  metalDark: "#5f7096",
};

const MASCOT_LINES = [
  "BEEP.",
  "FLOOR LIGHTS OK",
  "PIP PIP",
  "ALL QUIET",
  "SIP COFFEE",
  "WAITING…",
];

/* ---- small helpers ----------------------------------------------------- */



function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function lerp(a, b, t) { return a + (b - a) * t; }

function easeInOut(t) {
  const p = clamp(t, 0, 1);
  return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
}

function easeOut(t) { return 1 - Math.pow(1 - clamp(t, 0, 1), 3); }

function easeOutBack(t) {
  const p = clamp(t, 0, 1);
  const c = 1.70158;
  return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2);
}

function hexToRgb(hex) {
  const raw = String(hex || "").replace("#", "");
  if (raw.length !== 6 || !/^[0-9a-f]{6}$/i.test(raw)) return [11, 18, 32];
  return [
    parseInt(raw.slice(0, 2), 16),
    parseInt(raw.slice(2, 4), 16),
    parseInt(raw.slice(4, 6), 16),
  ];
}

function rgbToHex(r, g, b) {
  const to = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

function mixColor(a, b, t) {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  const k = clamp(t, 0, 1);
  return rgbToHex(
    ca[0] + (cb[0] - ca[0]) * k,
    ca[1] + (cb[1] - ca[1]) * k,
    ca[2] + (cb[2] - ca[2]) * k,
  );
}

/* Event categories that keep a reviewer on the review deck. */
const REVIEW_CATEGORIES = new Set(["review", "finding"]);

/* The room an event places its agent in: its phase_family when that is one
 * of the four rooms, else its own phase. REVIEW and OTHER name no room. */
function roomFamilyOf(event) {
  if (!event) return "";
  const family = String(event.phase_family || "").toUpperCase();
  if (PHASE_ORDER.indexOf(family) >= 0) return family;
  return familyOf(event.phase);
}

function familyOf(phase) {
  const name = String(phase || "").toUpperCase();
  return PHASE_ORDER.indexOf(name) >= 0 ? name : "";
}

function quadPoint(x0, y0, cx, cy, x1, y1, t) {
  const u = 1 - t;
  return {
    x: u * u * x0 + 2 * u * t * cx + t * t * x1,
    y: u * u * y0 + 2 * u * t * cy + t * t * y1,
  };
}

function hashString(text) {
  let hash = 0;
  const value = String(text || "");
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function severityColor(severity, fallback) {
  switch (severity) {
    case "danger": return C.danger;
    case "warning": return C.warning;
    case "success": return C.success;
    case "muted": return C.dim;
    default: return fallback || C.muted;
  }
}

/* ---- Stage ------------------------------------------------------------- */

export class Stage {
  constructor(canvas, options = {}) {
    this.canvas = canvas || null;
    this.opts = options || {};
    this.ctx = this.canvas && this.canvas.getContext ? this.canvas.getContext("2d") : null;

    this.model = null;
    this.events = [];
    this.roster = [];
    this.views = [];
    this.agents = new Map();
    this.effects = [];
    this.cursor = 0;
    this.cursorSeq = -1;
    this.live = false;
    this.selectedAgent = "";
    this.hover = null;
    this.time = 0;
    this.frame = 0;
    this.destroyed = false;
    this.raf = 0;
    this.lastNow = 0;
    this.startedAt = 0;

    this.phaseState = { current: "", completed: new Set() };
    this.endEventIdx = -1;
    this.cursorWrapup = null;
    this.lastBug = null;
    this.shake = { t: 0, dur: 0, mag: 0 };

    this.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.fit = 1;
    this.viewW = LOGICAL_W;
    this.viewH = LOGICAL_H;
    this.offsetX = 0;
    this.offsetY = 0;
    this.cssW = 0;
    this.cssH = 0;
    this.bgCanvas = null;
    this.bgDirty = true;
    this.twinkles = [];
    this.mastLights = [];
    this.consoleLeds = [];
    this.consoleGlows = [];
    this.consoleRadars = [];
    this.consoleTally = null;
    this.runwayLights = [];

    this.reducedMotion = false;
    this.reducedExplicit = false;
    this.motionQuery = null;
    this.onMotionChange = null;

    this.mascot = {
      x: 878,
      y: PLAZA_AGENT_Y,
      from: 878,
      to: 878,
      t: 1,
      dur: 1,
      nextWander: 7,
      nextSay: 11,
      sayText: "",
      sayUntil: 0,
      seed: 3.7,
      hover: false,
    };

    this.captionEl = null;
    this.aria = "";
    this.resizeObserver = null;
    this.resizeQueued = false;
    this.onVisibility = null;
    this.onClick = null;
    this.onPointerMove = null;
    this.onPointerLeave = null;
    this.onWinResize = null;

    if (!this.canvas || !this.ctx) return;

    this._resolveCaption();
    this._initMotionPreference();
    if (this.captionEl && !this.captionEl.textContent) {
      this.captionEl.textContent = "WAITING FOR RUN DATA · PICK A RUN";
    }
    this._bind();
    this.resize();
    this._start();
  }

  /* ---- public API ------------------------------------------------------ */

  setModel(model) {
    const next = model && typeof model === "object" ? model : null;
    const previous = this.model;
    const identityBefore = this._identity(previous);
    const identityAfter = this._identity(next);
    const changedRun = Boolean(previous) && identityBefore !== identityAfter;

    this.model = next;
    this.events = next && Array.isArray(next.events) ? next.events : [];
    this.roster = this._buildRoster();

    if (changedRun) {
      this.agents.clear();
      this.views = [];
      this.effects.length = 0;
      this.cursor = 0;
      this.cursorSeq = -1;
      this.lastBug = null;
      this.shake = { t: 0, dur: 0, mag: 0 };
      this.selectedAgent = "";
    }
    this.cursor = clamp(this.cursor, 0, this.events.length);

    this._computePhaseState();
    this._syncAgents(false);
    this._updateChrome();
  }

  setCursor(index, options = {}) {
    if (!this.model) return;
    const { animate = false, live = false } = options || {};
    const total = this.events.length;
    const raw = Number.isFinite(Number(index)) ? Math.floor(Number(index)) : 0;
    const next = clamp(raw, 0, total);
    this.cursor = next;
    this.live = Boolean(live);

    /* New and rewound events are told apart by journal seq, not by array
     * index: above MAX_EVENTS the window keeps its length while its contents
     * slide, so an index range would read "nothing new" forever. */
    const previousSeq = this.cursorSeq;
    const nextSeq = next > 0 ? Number(this.events[next - 1].seq) : -1;
    this.cursorSeq = nextSeq;
    const forward = nextSeq > previousSeq;
    const canAnimate = Boolean(animate) && !this.reducedMotion;

    if (nextSeq < previousSeq) {
      /* a seek backwards rewinds the floor: drop in-flight effects */
      this.effects.length = 0;
    }

    this._computePhaseState();
    this._syncAgents(canAnimate);

    if (canAnimate && forward) {
      const fresh = [];
      for (let i = next - 1; i >= 0 && Number(this.events[i].seq) > previousSeq; i -= 1) {
        fresh.push(this.events[i]);
      }
      /* fresh is newest-first; a large jump animates only its last three */
      const shown = fresh.length > 6 ? fresh.slice(0, 3) : fresh;
      for (let i = shown.length - 1; i >= 0; i -= 1) this._spawnForEvent(shown[i]);
    }

    this._updateChrome();
  }

  setSelectedAgent(name) {
    const next = String(name || "");
    if (next === this.selectedAgent) return;
    this.selectedAgent = next;
  }

  setReducedMotion(enabled) {
    this.reducedExplicit = true;
    const next = Boolean(enabled);
    if (next === this.reducedMotion) return;
    this.reducedMotion = next;
    if (next) {
      this.effects.length = 0;
      this.shake = { t: 0, dur: 0, mag: 0 };
    }
    this._updateChrome();
  }

  resize() {
    if (!this.canvas || !this.ctx || this.destroyed) return;
    this.resizeQueued = false;
    const rect = this.canvas.getBoundingClientRect();
    const cssW = Math.max(1, Math.round(rect.width || this.canvas.clientWidth || 0));
    const cssH = Math.max(1, Math.round(rect.height || this.canvas.clientHeight || 0));
    if (cssW <= 1 || cssH <= 1) {
      this._queueResize();
      return;
    }
    this.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.cssW = cssW;
    this.cssH = cssH;
    this.fit = Math.min(cssW / LOGICAL_W, cssH / LOGICAL_H);
    if (!Number.isFinite(this.fit) || this.fit <= 0) this.fit = 1;

    const deviceW = Math.max(320, Math.round(cssW * this.dpr));
    const deviceH = Math.max(160, Math.round(cssH * this.dpr));
    if (this.canvas.width !== deviceW) this.canvas.width = deviceW;
    if (this.canvas.height !== deviceH) this.canvas.height = deviceH;

    const scale = this.fit * this.dpr;
    this.viewW = deviceW / scale;
    this.viewH = deviceH / scale;
    this.offsetX = Math.max(0, (this.viewW - LOGICAL_W) / 2);
    this.offsetY = Math.max(0, (this.viewH - LOGICAL_H) * 0.55);

    this._buildTwinkles();
    this._buildBackground();
  }

  destroy() {
    this.destroyed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.resizeObserver) {
      try { this.resizeObserver.disconnect(); } catch (err) { /* ignore */ }
      this.resizeObserver = null;
    }
    if (this.canvas && this.onClick) this.canvas.removeEventListener("click", this.onClick);
    if (this.canvas && this.onPointerMove) this.canvas.removeEventListener("pointermove", this.onPointerMove);
    if (this.canvas && this.onPointerLeave) this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    if (this.onWinResize) window.removeEventListener("resize", this.onWinResize);
    if (this.onVisibility) document.removeEventListener("visibilitychange", this.onVisibility);
    if (this.motionQuery && this.onMotionChange) {
      try { this.motionQuery.removeEventListener("change", this.onMotionChange); } catch (err) { /* ignore */ }
    }
    this.effects.length = 0;
    this.agents.clear();
    this.views = [];
    this.bgCanvas = null;
    this.model = null;
    this.canvas = null;
    this.ctx = null;
  }

  /* ---- setup ----------------------------------------------------------- */

  _identity(model) {
    /* Coordinates only. The etag used to be part of this key, which made
     * every live poll look like a different run. */
    return runKey(model);
  }

  _resolveCaption() {
    try {
      this.captionEl = document.getElementById("stage-caption");
    } catch (err) {
      this.captionEl = null;
    }
  }

  _initMotionPreference() {
    try {
      this.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
      this.reducedMotion = Boolean(this.motionQuery.matches);
      this.onMotionChange = (event) => {
        if (this.reducedExplicit) return;
        this.reducedMotion = Boolean(event.matches);
      };
      if (this.motionQuery.addEventListener) {
        this.motionQuery.addEventListener("change", this.onMotionChange);
      }
    } catch (err) {
      this.motionQuery = null;
    }
  }

  _bind() {
    this.onClick = (event) => this._handleClick(event);
    this.onPointerMove = (event) => this._handlePointerMove(event);
    this.onPointerLeave = () => {
      this.hover = null;
      this.mascot.hover = false;
      if (this.canvas) this.canvas.classList.remove("is-clickable");
    };
    this.onWinResize = () => this._queueResize();
    this.onVisibility = () => {
      if (document.hidden) {
        if (this.raf) cancelAnimationFrame(this.raf);
        this.raf = 0;
      } else if (!this.raf && !this.destroyed) {
        this._start();
      }
    };

    this.canvas.addEventListener("click", this.onClick);
    this.canvas.addEventListener("pointermove", this.onPointerMove);
    this.canvas.addEventListener("pointerleave", this.onPointerLeave);
    window.addEventListener("resize", this.onWinResize);
    document.addEventListener("visibilitychange", this.onVisibility);

    if (typeof ResizeObserver === "function") {
      this.resizeObserver = new ResizeObserver(() => this._queueResize());
      this.resizeObserver.observe(this.canvas);
    }
  }

  _queueResize() {
    if (this.resizeQueued || this.destroyed) return;
    this.resizeQueued = true;
    const run = () => {
      this.resizeQueued = false;
      this.resize();
    };
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(run);
    } else {
      setTimeout(run, 30);
    }
  }

  _start() {
    if (this.raf || this.destroyed) return;
    if (!this.startedAt) this.startedAt = performance.now();
    this.lastNow = performance.now();
    const tick = (now) => {
      if (this.destroyed) return;
      this.raf = requestAnimationFrame(tick);
      const dt = Math.min(0.06, Math.max(0, (now - this.lastNow) / 1000));
      this.lastNow = now;
      this.time = (now - this.startedAt) / 1000;
      this.frame += 1;
      this._tick(dt);
      this._render();
    };
    this.raf = requestAnimationFrame(tick);
  }

  /* ---- model derivation ------------------------------------------------ */

  _buildRoster() {
    const source = this.model && Array.isArray(this.model.agents) ? this.model.agents : [];
    const seen = new Set();
    const roster = [];
    for (const raw of source) {
      if (!raw || typeof raw !== "object") continue;
      const name = String(raw.name || raw.role || raw.agent_type || "agent");
      if (seen.has(name)) continue;
      seen.add(name);
      roster.push(raw);
    }
    for (const event of this.events) {
      /* Reviewers a review_dispatch names belong on the floor too, even
       * before they write an event of their own. */
      const names = [event && typeof event.agent === "string" ? event.agent : ""];
      if (event && event.type === "review_dispatch" && Array.isArray(event.agents)) names.push(...event.agents);
      for (const raw of names) {
        const name = String(raw || "");
        if (!name || seen.has(name)) continue;
        seen.add(name);
        roster.push({ name, role: name, color: "" });
      }
    }
    return roster;
  }

  _computePhaseState() {
    const completed = new Set();
    let current = "";
    const items = this.model && this.model.phases && Array.isArray(this.model.phases.items)
      ? this.model.phases.items
      : [];
    const cursorSeq = this.cursor > 0 && this.cursor <= this.events.length
      ? Number(this.events[this.cursor - 1].seq)
      : -1;

    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const family = familyOf(item.family || item.name);
      if (!family) continue;
      /* The pass containing the cursor decides: inside a pass (from its
       * start to its end, or open) the phase is current; after a closed pass
       * and before the next one starts it is complete. A pass with no start
       * reads as complete from its end. A pending phase has no pass. */
      let reading = "";
      for (const pass of phasePasses(item)) {
        if (pass.start !== null && cursorSeq >= pass.start) {
          reading = pass.end === null || cursorSeq < pass.end ? "current" : "complete";
        } else if (pass.start === null && pass.end !== null && cursorSeq >= pass.end) {
          reading = "complete";
        }
      }
      if (reading === "current") current = family;
      else if (reading === "complete") completed.add(family);
    }

    this.phaseState = { current, completed };
  }

  /* Per agent, up to the cursor: its latest event and that event's index,
   * the last of the four rooms its events placed it in, and the index of the
   * latest review_dispatch that named it as a reviewer. */
  _lastEventsByAgent() {
    const lastByName = new Map();
    const lastIdxByName = new Map();
    const lastRoomByName = new Map();
    const reviewIdxByName = new Map();
    for (let i = 0; i < this.cursor && i < this.events.length; i += 1) {
      const event = this.events[i];
      if (!event) continue;
      if (event.type === "review_dispatch" && Array.isArray(event.agents)) {
        for (const reviewer of event.agents) if (reviewer) reviewIdxByName.set(String(reviewer), i);
      }
      if (typeof event.agent !== "string" || !event.agent) continue;
      lastByName.set(event.agent, event);
      lastIdxByName.set(event.agent, i);
      const room = roomFamilyOf(event);
      if (room) lastRoomByName.set(event.agent, room);
    }
    return { lastByName, lastIdxByName, lastRoomByName, reviewIdxByName };
  }

  /* The run's lifecycle as of the cursor, walked over the events shown so
   * far, with the server's rule: the run reads ended only while its latest
   * session_end is followed by nothing but end trailers (session_end,
   * cleanup_summary). Any other event after it, a session_start included,
   * reopens the run. The wrap-up is the latest wrap-up session_end at or
   * before the cursor, never the final one from the whole journal. */
  _lifecycleAtCursor() {
    let endIdx = -1;
    let wrapup = null;
    for (let i = 0; i < this.cursor && i < this.events.length; i += 1) {
      const event = this.events[i];
      if (!event) continue;
      if (event.type === "session_end") {
        endIdx = i;
        if (isWrapup(event)) wrapup = wrapupOf(event);
      } else if (event.type !== "cleanup_summary") {
        endIdx = -1;
      }
    }
    return { endIdx, wrapup };
  }

  _computeViews() {
    const { lastByName, lastIdxByName, lastRoomByName, reviewIdxByName } = this._lastEventsByAgent();
    const lifecycle = this._lifecycleAtCursor();
    this.endEventIdx = lifecycle.endIdx;
    this.cursorWrapup = lifecycle.wrapup;

    const ended = this.endEventIdx >= 0;
    const completed = this.phaseState.completed;
    const current = this.phaseState.current;
    const views = [];

    for (let index = 0; index < this.roster.length; index += 1) {
      const raw = this.roster[index];
      const name = String(raw.name || raw.role || `agent-${index}`);
      const role = normalizeRole(raw.role || raw.agent_type || name);
      const palette = paletteFor(raw.color || colorForSeed(name));
      const latest = lastByName.get(name) || null;
      const latestIdx = lastIdxByName.has(name) ? lastIdxByName.get(name) : -1;

      let zone = "hidden";
      let family = "";
      let active = false;

      if (role === "lead") {
        zone = "tower";
        active = true;
        family = familyOf(latest && latest.phase) || current;
      } else if (latest || reviewIdxByName.has(name)) {
        /* The room is the latest event's phase family; an OTHER-family event
         * keeps the agent in the last room it had. A reviewer stands on the
         * deck while its latest event is a review or a finding, or while a
         * review_dispatch names it and it has done nothing since. Real
         * reviews rarely open a REVIEW phase, so their events inherit TEST:
         * the category, not the phase family, keeps a reviewer on the deck. */
        family = roomFamilyOf(latest) || lastRoomByName.get(name) || "";
        const phaseDone = Boolean(family && completed.has(family));
        const reviewIdx = reviewIdxByName.has(name) ? reviewIdxByName.get(name) : -1;
        const isReview = Boolean(latest && (REVIEW_CATEGORIES.has(latest.category) || latest.phase_family === "REVIEW"))
          || reviewIdx > latestIdx;
        if (ended) {
          zone = "lounge";
        } else if (isReview) {
          zone = "review";
          active = true;
        } else if (phaseDone) {
          zone = "lounge";
        } else if (family) {
          zone = "room";
          active = true;
        } else {
          zone = "lounge";
        }
      }

      const scale = role === "lead" ? 1.46 : role === "n8n" ? 1.34 : 1.34;
      views.push({
        name,
        role,
        palette,
        scale,
        zone,
        family,
        latest,
        latestIdx,
        active,
        present: zone !== "hidden",
        order: index,
        seed: (hashString(name) % 997) / 97,
        agent: null,
        x: 0,
        y: 0,
      });
    }
    return views;
  }

  _syncAgents(animate) {
    if (!this.model) {
      this.views = [];
      return;
    }
    const views = this._computeViews();
    const buckets = { tower: [], review: [], lounge: [] };
    for (const family of PHASE_ORDER) buckets[`room:${family}`] = [];

    for (const view of views) {
      if (!view.present) continue;
      if (view.zone === "room") {
        const key = `room:${view.family}`;
        if (buckets[key] && buckets[key].length < ROOM_SLOT_X.length) {
          buckets[key].push(view);
        } else {
          buckets.lounge.push(view);
        }
      } else if (view.zone === "tower") {
        buckets.tower.push(view);
      } else if (view.zone === "review") {
        buckets.review.push(view);
      } else {
        buckets.lounge.push(view);
      }
    }

    const targets = new Map();
    const assign = (view, x, y) => targets.set(view.name, { x, y });

    for (const family of PHASE_ORDER) {
      const room = ROOM_BY_FAMILY[family];
      const list = buckets[`room:${family}`];
      list.forEach((view, slotIndex) => {
        assign(view, room.x + ROOM_SLOT_X[slotIndex], ROOM_AGENT_Y);
      });
    }
    buckets.tower.forEach((view, index) => {
      if (buckets.tower.length === 1) {
        assign(view, TOWER_X, TOWER_DECK_Y);
        return;
      }
      const slot = TOWER_SLOTS[index % TOWER_SLOTS.length];
      assign(view, slot.x, slot.y);
    });
    buckets.review.forEach((view, index) => {
      assign(view, REVIEW_SLOT_X[index % REVIEW_SLOT_X.length], PLAZA_AGENT_Y);
    });
    buckets.lounge.forEach((view, index) => {
      if (index < LOUNGE_SLOTS.length) {
        const slot = LOUNGE_SLOTS[index];
        assign(view, slot.x, slot.y);
      } else {
        assign(view, 60 + ((index - LOUNGE_SLOTS.length) % 4) * 54, PLAZA_AGENT_Y - 36);
      }
    });

    for (const view of views) {
      const target = targets.get(view.name);
      view.present = Boolean(target);
      if (!view.present) {
        view.agent = this.agents.get(view.name) || null;
        continue;
      }
      let agent = this.agents.get(view.name);
      if (!agent) {
        agent = {
          x: target.x,
          y: target.y,
          alpha: animate ? 0 : 1,
          fade: animate ? 0.32 : 1,
          walking: false,
          path: null,
          pathIdx: 0,
          segT: 0,
          segDur: 0,
          segFromX: target.x,
          segFromY: target.y,
          flip: false,
          frameSeed: hashString(view.name) % 61,
          seed: view.seed,
          zone: view.zone,
        };
        this.agents.set(view.name, agent);
      }
      const zoneKey = view.zone === "room" ? `room:${view.family}` : view.zone;
      const movedZone = agent.zone !== zoneKey;
      const dx = target.x - agent.x;
      const dy = target.y - agent.y;
      const moved = Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5;

      if (!moved) {
        agent.walking = false;
        agent.path = null;
      } else if (!animate) {
        agent.x = target.x;
        agent.y = target.y;
        agent.walking = false;
        agent.path = null;
        agent.flip = dx < -0.5 ? true : dx > 0.5 ? false : agent.flip;
      } else if (movedZone || agent.walking) {
        this._assignWalk(agent, target.x, target.y, view.zone !== "lounge" && view.zone !== "review");
      } else {
        this._assignWalk(agent, target.x, target.y, false);
      }
      agent.zone = zoneKey;
      agent.flip = dx < -0.5 ? true : dx > 0.5 ? false : agent.flip;
      view.agent = agent;
    }

    for (const name of Array.from(this.agents.keys())) {
      if (!views.some((view) => view.name === name)) this.agents.delete(name);
    }
    this.views = views;
  }

  _assignWalk(agent, targetX, targetY, useCorridor) {
    const startX = agent.x;
    const startY = agent.y;
    const points = [];
    if (useCorridor && Math.abs(startY - targetY) > 2) {
      points.push({ x: startX, y: CORRIDOR_Y });
      points.push({ x: targetX, y: CORRIDOR_Y });
    }
    points.push({ x: targetX, y: targetY });
    const path = [];
    let fromX = startX;
    let fromY = startY;
    for (const point of points) {
      const distance = Math.hypot(point.x - fromX, point.y - fromY);
      const duration = clamp(distance / 190, 0.18, 1.1);
      path.push({ x: point.x, y: point.y, dur: duration, fromX, fromY });
      fromX = point.x;
      fromY = point.y;
    }
    agent.path = path;
    agent.pathIdx = 0;
    agent.segT = 0;
    agent.walking = true;
    if (path.length) {
      agent.segFromX = path[0].fromX;
      agent.segFromY = path[0].fromY;
      agent.segDur = path[0].dur;
    }
  }

  /* ---- animation loop -------------------------------------------------- */

  _tick(dt) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    if (Math.abs(dpr - this.dpr) > 0.01) {
      this.dpr = dpr;
      this._queueResize();
    }

    for (const agent of this.agents.values()) {
      if (agent.alpha < 1) agent.alpha = Math.min(1, agent.alpha + dt / Math.max(0.12, agent.fade));
      if (!agent.walking || !agent.path) continue;
      const segment = agent.path[agent.pathIdx];
      if (!segment) {
        agent.walking = false;
        agent.path = null;
        continue;
      }
      agent.segT += dt;
      const progress = segment.dur <= 0 ? 1 : clamp(agent.segT / segment.dur, 0, 1);
      const eased = easeInOut(progress);
      agent.x = lerp(segment.fromX, segment.x, eased);
      agent.y = lerp(segment.fromY, segment.y, eased);
      if (progress >= 1) {
        agent.segFromX = segment.x;
        agent.segFromY = segment.y;
        agent.pathIdx += 1;
        agent.segT = 0;
        const nextSegment = agent.path[agent.pathIdx];
        if (nextSegment) {
          nextSegment.fromX = agent.x;
          nextSegment.fromY = agent.y;
          agent.segDur = nextSegment.dur;
        } else {
          agent.walking = false;
          agent.path = null;
        }
      }
    }

    for (let i = this.effects.length - 1; i >= 0; i -= 1) {
      const effect = this.effects[i];
      effect.t += dt;
      if (effect.particles) {
        for (const particle of effect.particles) {
          particle.vy += 220 * dt;
          particle.x += particle.vx * dt;
          particle.y += particle.vy * dt;
        }
      }
      if (effect.t >= effect.dur) this.effects.splice(i, 1);
    }

    if (this.shake.t > 0) {
      this.shake.t = Math.max(0, this.shake.t - dt);
    }

    const mascot = this.mascot;
    if (!this.reducedMotion) {
      if (mascot.t < mascot.dur) {
        mascot.t = Math.min(mascot.dur, mascot.t + dt);
        const p = easeInOut(mascot.t / mascot.dur);
        mascot.x = lerp(mascot.from, mascot.to, p);
      } else if (this.time >= mascot.nextWander) {
        mascot.from = mascot.x;
        mascot.to = 842 + Math.random() * 66;
        mascot.t = 0;
        mascot.dur = 1.6 + Math.random() * 1.4;
        mascot.nextWander = this.time + 6 + Math.random() * 7;
      }
    }
    if (this.time >= mascot.nextSay && this.views.length) {
      mascot.sayText = MASCOT_LINES[this.frame % MASCOT_LINES.length];
      mascot.sayUntil = this.time + 2.8;
      mascot.nextSay = this.time + 9 + Math.random() * 6;
    }
  }

  /* ---- background ------------------------------------------------------ */

  _buildTwinkles() {
    const rand = seededRandom(0x5eed1);
    const twinkles = [];
    const left = -this.offsetX + 8;
    const right = -this.offsetX + this.viewW - 8;
    const top = -this.offsetY + 8;
    const bottom = ROOM_TOP - 26;
    for (let i = 0; i < 70; i += 1) {
      twinkles.push({
        x: Math.round(left + rand() * Math.max(1, right - left)),
        y: Math.round(top + rand() * Math.max(1, bottom - top)),
        size: rand() > 0.86 ? 2 : 1,
        speed: 0.6 + rand() * 1.8,
        phase: rand() * Math.PI * 2,
        color: rand() > 0.8 ? "#a3e635" : rand() > 0.5 ? "#bfe9ff" : "#e8eefc",
      });
    }
    this.twinkles = twinkles;
  }

  _buildBackground() {
    if (!this.canvas) return;
    const deviceW = this.canvas.width;
    const deviceH = this.canvas.height;
    if (!deviceW || !deviceH || this.fit <= 0) return;
    let cache = null;
    try {
      cache = document.createElement("canvas");
    } catch (err) {
      cache = null;
    }
    if (!cache) return;
    cache.width = deviceW;
    cache.height = deviceH;
    const g = cache.getContext("2d");
    if (!g) return;
    disableSmoothing(g);
    this.mastLights = [];
    this.consoleLeds = [];
    this.consoleGlows = [];
    this.consoleRadars = [];
    this.consoleTally = null;
    this.runwayLights = [];
    const scale = this.fit * this.dpr;
    g.setTransform(scale, 0, 0, scale, 0, 0);
    g.translate(this.offsetX, this.offsetY);
    this._paintBackdrop(g);
    this._paintTower(g);
    this._paintTowerDetails(g);
    this._paintRooms(g);
    this._paintPlaza(g);
    this._paintGroundExtension(g);
    this._paintForeground(g);
    this.bgCanvas = cache;
    this.bgDirty = false;
  }

  _paintBackdrop(g) {
    const left = -this.offsetX;
    const top = -this.offsetY;
    const width = this.viewW;
    const height = this.viewH;

    const sky = g.createLinearGradient(0, top, 0, ROOM_TOP + 20);
    sky.addColorStop(0, "#04070f");
    sky.addColorStop(0.5, "#0a1224");
    sky.addColorStop(0.85, "#131f3a");
    sky.addColorStop(1, "#1a2748");
    g.fillStyle = sky;
    g.fillRect(left, top, width, height);

    const ground = g.createLinearGradient(0, ROOM_TOP - 10, 0, ROOM_TOP + 140);
    ground.addColorStop(0, "#0a1020");
    ground.addColorStop(1, "#060a12");
    g.fillStyle = ground;
    g.fillRect(left, ROOM_TOP - 10, width, height);

    /* layered nebula clouds (hard rectangles, no blur) */
    const clouds = [
      { x: left, y: top + 46, w: width * 0.55, h: 30, a: 0.035, c: "#60a5fa" },
      { x: left + width * 0.28, y: top + 22, w: width * 0.4, h: 18, a: 0.03, c: "#a78bfa" },
      { x: left + width * 0.62, y: top + 84, w: width * 0.42, h: 26, a: 0.028, c: "#2dd4bf" },
      { x: left + width * 0.05, y: top + 120, w: width * 0.34, h: 18, a: 0.022, c: "#f472b6" },
    ];
    for (const cloud of clouds) {
      for (let band = 0; band < 3; band += 1) {
        g.fillStyle = rgba(cloud.c, cloud.a * (1 - band * 0.28));
        g.fillRect(
          Math.round(cloud.x - band * 8),
          Math.round(cloud.y + band * 6),
          Math.round(cloud.w + band * 16),
          Math.max(2, Math.round(cloud.h - band * 6)),
        );
      }
    }

    /* stars */
    const rand = seededRandom(0x51a7);
    for (let i = 0; i < 210; i += 1) {
      const x = Math.round(left + rand() * width);
      const y = Math.round(top + rand() * Math.max(8, ROOM_TOP - top - 30));
      const bright = rand();
      const size = bright > 0.94 ? 2 : 1;
      const alpha = 0.2 + bright * 0.7;
      g.fillStyle = rgba(bright > 0.85 ? "#bfe9ff" : "#dfe8fb", alpha);
      g.fillRect(x, y, size, size);
      if (size === 2) {
        g.fillStyle = rgba("#e8eefc", alpha * 0.45);
        g.fillRect(x - 1, y + 1, 1, 1);
        g.fillRect(x + 2, y + 1, 1, 1);
      }
    }

    /* deep-sky HUD: barely-there camera markings on the upper plate */
    this._paintSkyHud(g);

    /* planet with a stepped ring */
    this._paintPlanet(g, 846, 62, 30);

    /* a couple of distant satellites */
    this._paintSatellite(g, 132, 74, false);
    this._paintSatellite(g, 386, 44, true);
    this._paintSatellite(g, 690, 118, true);

    /* constellations, antenna farm and the tower radar field */
    this._paintConstellation(g, [
      [left + width * 0.08, top + 52],
      [left + width * 0.13, top + 34],
      [left + width * 0.18, top + 50],
      [left + width * 0.24, top + 30],
    ], "rgba(191, 233, 255, 0.35)");
    this._paintConstellation(g, [
      [left + width * 0.72, top + 92],
      [left + width * 0.78, top + 74],
      [left + width * 0.84, top + 96],
      [left + width * 0.9, top + 78],
    ], "rgba(167, 139, 250, 0.32)");
    this._paintConstellation(g, [
      [left + width * 0.42, top + 28],
      [left + width * 0.47, top + 16],
      [left + width * 0.52, top + 30],
    ], "rgba(45, 212, 191, 0.3)");

    this._paintRadarField(g, TOWER_X, 150);
    this._paintMast(g, left + width * 0.13, ROOM_TOP - 8, 84);
    this._paintMast(g, left + width * 0.25, ROOM_TOP - 8, 58);
    this._paintMast(g, left + width * 0.87, ROOM_TOP - 8, 108);
    this._paintMast(g, left + width * 0.94, ROOM_TOP - 8, 64);
    this._paintPixelMoon(g, left + width * 0.09, top + 96, 12);

    /* skyline silhouettes on the horizon */
    this._paintSkyline(g);
    /* low haze between the skyline and the floor */
    for (let band = 0; band < 4; band += 1) {
      g.fillStyle = `rgba(56, 80, 122, ${0.05 - band * 0.01})`;
      g.fillRect(left, ROOM_TOP - 46 + band * 9, width, 7);
    }

    /* horizon glow */
    g.fillStyle = "rgba(56, 80, 122, 0.55)";
    g.fillRect(left, ROOM_TOP - 8, width, 1);
    g.fillStyle = "rgba(45, 212, 191, 0.12)";
    g.fillRect(left, ROOM_TOP - 7, width, 6);
  }

  _paintSkyHud(g) {
    const left = -this.offsetX;
    const top = -this.offsetY;
    const pad = 20;
    const width = this.viewW;

    g.fillStyle = "rgba(56, 80, 122, 0.42)";
    g.fillRect(left + pad, top + pad, 30, 1);
    g.fillRect(left + pad, top + pad, 1, 10);
    g.fillRect(left + width - pad - 30, top + pad, 30, 1);
    g.fillRect(left + width - pad - 1, top + pad, 1, 10);
    g.fillRect(left + pad, top + pad + 40, 14, 1);
    g.fillRect(left + pad, top + pad + 36, 1, 5);

    g.font = `bold 11px ${FONT_STACK}`;
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(143, 160, 196, 0.3)";
    g.fillText("PACT // MISSION CONTROL", left + pad + 8, top + pad + 14);
    g.font = `8px ${FONT_STACK}`;
    g.fillStyle = "rgba(143, 160, 196, 0.22)";
    g.fillText("FLOOR 07 · ORBITAL WATCH", left + pad + 8, top + pad + 26);

    g.textAlign = "right";
    g.fillStyle = "rgba(143, 160, 196, 0.24)";
    g.fillText("SECTOR 7G · LIVE FEED", left + width - pad - 8, top + pad + 14);
    g.fillStyle = "rgba(251, 113, 133, 0.4)";
    g.fillRect(left + width - pad - 14, top + pad + 24, 3, 3);
    g.fillStyle = "rgba(143, 160, 196, 0.22)";
    g.fillText("REC", left + width - pad - 18, top + pad + 26);
    g.textAlign = "left";

    /* drifting scan ticks on the left edge */
    for (let i = 0; i < 4; i += 1) {
      g.fillStyle = `rgba(56, 80, 122, ${0.22 - i * 0.04})`;
      g.fillRect(left + pad, top + 62 + i * 10, 6 + i * 2, 1);
    }
  }

  _paintPlanet(g, cx, cy, radius) {
    const bands = ["#2b3f66", "#3b557f", "#486390", "#3b557f", "#2b3f66"];
    for (let dy = -radius; dy <= radius; dy += 2) {
      const half = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      const band = bands[(Math.floor((dy + radius) / 8)) % bands.length];
      g.fillStyle = band;
      g.fillRect(cx - half, cy + dy, half * 2, 2);
    }
    /* shadow crescent */
    g.fillStyle = "rgba(4, 7, 15, 0.42)";
    for (let dy = -radius; dy <= radius; dy += 2) {
      const half = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      g.fillRect(cx + Math.round(half * 0.2), cy + dy, half, 2);
    }
    /* ring */
    g.fillStyle = "rgba(191, 233, 255, 0.28)";
    g.fillRect(cx - radius - 12, cy - 4, radius * 2 + 24, 2);
    g.fillStyle = "rgba(96, 165, 250, 0.22)";
    g.fillRect(cx - radius - 6, cy + 4, radius * 2 + 12, 2);
  }

  _paintPixelMoon(g, cx, cy, radius) {
    for (let dy = -radius; dy <= radius; dy += 2) {
      const half = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      g.fillStyle = "#39476b";
      g.fillRect(cx - half, cy + dy, half * 2, 2);
      g.fillStyle = "rgba(8, 13, 26, 0.5)";
      g.fillRect(cx - half, cy + dy + 1, half * 2, 1);
    }
    g.fillStyle = "rgba(8, 13, 26, 0.55)";
    for (let dy = -radius; dy <= radius; dy += 2) {
      const half = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      g.fillRect(cx + Math.round(half * 0.35), cy + dy, half, 2);
    }
  }

  _paintRadarField(g, cx, cy) {
    for (const radius of [56, 104, 160]) {
      this._paintPixelRing(g, cx, cy, radius, "rgba(56, 80, 122, 0.22)", 1);
    }
    g.fillStyle = "rgba(56, 80, 122, 0.16)";
    for (let step = -3; step <= 3; step += 1) {
      g.fillRect(-this.offsetX, Math.round(cy + step * 18), this.viewW, 1);
    }
  }

  _paintPixelRing(g, cx, cy, radius, color, thickness) {
    const thick = Math.max(1, Math.round(thickness || 1));
    for (let dy = -radius; dy <= radius; dy += 2) {
      const half = Math.round(Math.sqrt(Math.max(0, radius * radius - dy * dy)));
      g.fillStyle = color;
      g.fillRect(Math.round(cx - half), Math.round(cy + dy), thick, 2);
      g.fillRect(Math.round(cx + half - thick), Math.round(cy + dy), thick, 2);
    }
  }

  _paintConstellation(g, points, color) {
    for (let i = 1; i < points.length; i += 1) {
      this._paintDottedLine(g, points[i - 1][0], points[i - 1][1], points[i][0], points[i][1], color, 5);
    }
    for (const point of points) {
      g.fillStyle = color;
      g.fillRect(Math.round(point[0]) - 1, Math.round(point[1]) - 1, 3, 3);
      g.fillStyle = "rgba(232, 238, 252, 0.55)";
      g.fillRect(Math.round(point[0]), Math.round(point[1]), 1, 1);
    }
  }

  _paintDottedLine(g, x1, y1, x2, y2, color, step) {
    const distance = Math.hypot(x2 - x1, y2 - y1);
    const count = Math.max(1, Math.floor(distance / (step || 5)));
    g.fillStyle = color;
    for (let i = 0; i <= count; i += 1) {
      const p = i / count;
      g.fillRect(Math.round(x1 + (x2 - x1) * p), Math.round(y1 + (y2 - y1) * p), 1, 1);
    }
  }

  _paintMast(g, x, baseY, height) {
    const top = baseY - height;
    const xx = Math.round(x);
    g.fillStyle = "#0d1728";
    g.fillRect(xx, top, 2, height);
    g.fillRect(xx + 10, top, 2, height);
    g.fillStyle = "#16233c";
    for (let y = top + 6; y < baseY; y += 9) {
      g.fillRect(xx, Math.round(y), 12, 1);
    }
    g.fillStyle = "#16233c";
    g.fillRect(xx + 4, top - 5, 4, 5);
    g.fillStyle = "#1b2a48";
    g.fillRect(xx + 2, top - 2, 8, 2);
    this.mastLights.push({ x: xx + 6, y: top - 6 });
  }

  _paintSatellite(g, x, y, flip) {
    g.fillStyle = C.metalDark;
    g.fillRect(x, y, 8, 4);
    g.fillStyle = "#2b3f66";
    g.fillRect(x - 7, y + 1, 5, 2);
    g.fillRect(x + 10, y + 1, 5, 2);
    g.fillStyle = "rgba(96, 165, 250, 0.5)";
    g.fillRect(x + 2, y - 2, 2, 2);
    if (flip) {
      g.fillStyle = "rgba(232, 238, 252, 0.6)";
      g.fillRect(x + 5, y - 1, 1, 1);
    }
  }

  _paintSkyline(g) {
    const rand = seededRandom(0x9e3f);
    let x = -this.offsetX - 20;
    const right = -this.offsetX + this.viewW;
    while (x < right) {
      const width = 14 + Math.round(rand() * 34);
      const height = 10 + Math.round(rand() * 40);
      const top = ROOM_TOP - 8 - height;
      g.fillStyle = "#0b1322";
      g.fillRect(x, top, width, height + 8);
      g.fillStyle = "#101b2f";
      g.fillRect(x + 2, top + 2, width - 4, 2);
      /* lit windows */
      for (let wy = top + 6; wy < ROOM_TOP - 14; wy += 6) {
        for (let wx = x + 3; wx < x + width - 3; wx += 6) {
          if (rand() > 0.72) {
            g.fillStyle = rand() > 0.6 ? "rgba(251, 191, 36, 0.65)" : "rgba(96, 165, 250, 0.55)";
            g.fillRect(wx, wy, 2, 2);
          }
        }
      }
      x += width + 4 + Math.round(rand() * 8);
    }
  }

  _paintTower(g) {
    const cx = TOWER_X;
    const deckY = TOWER_DECK_Y;

    /* pylons down into the plaza (mostly hidden by the rooms) */
    g.fillStyle = "#131f36";
    g.fillRect(cx - 30, deckY + 8, 8, ROOM_FLOOR - deckY + 120);
    g.fillRect(cx + 22, deckY + 8, 8, ROOM_FLOOR - deckY + 120);
    g.fillStyle = "rgba(96, 165, 250, 0.18)";
    for (let y = deckY + 20; y < PLAZA_TOP; y += 26) {
      g.fillRect(cx - 30, y, 60, 2);
    }

    /* tower body */
    g.fillStyle = "#101a2e";
    g.fillRect(cx - 62, 96, 124, deckY - 96);
    g.fillStyle = "#16233c";
    g.fillRect(cx - 56, 102, 112, deckY - 108);
    /* window band */
    g.fillStyle = "#0b1424";
    g.fillRect(cx - 48, 112, 96, 30);
    for (let i = 0; i < 5; i += 1) {
      g.fillStyle = i % 2 === 0 ? "rgba(45, 212, 191, 0.5)" : "rgba(96, 165, 250, 0.4)";
      g.fillRect(cx - 44 + i * 19, 116, 15, 22);
    }
    /* console glow */
    g.fillStyle = "rgba(163, 230, 53, 0.16)";
    g.fillRect(cx - 44, 146, 88, 4);

    /* deck platform */
    g.fillStyle = "#1b2a48";
    g.fillRect(cx - 88, deckY - 8, 176, 8);
    g.fillStyle = "#24324f";
    g.fillRect(cx - 88, deckY - 8, 176, 2);
    g.fillStyle = "#0b1220";
    g.fillRect(cx - 80, deckY, 160, 5);
    g.fillStyle = C.metalDark;
    g.fillRect(cx - 84, deckY - 2, 4, 8);
    g.fillRect(cx + 80, deckY - 2, 4, 8);

    /* railings */
    g.fillStyle = "#2a3b5c";
    for (let i = -3; i <= 3; i += 1) {
      if (Math.abs(i) < 2) continue;
      g.fillRect(cx + i * 26 - 1, deckY - 22, 2, 16);
    }
    g.fillRect(cx - 80, deckY - 22, 48, 2);
    g.fillRect(cx + 32, deckY - 22, 48, 2);

    /* mast + radar dish */
    g.fillStyle = "#24324f";
    g.fillRect(cx - 3, 50, 6, 46);
    g.fillStyle = "#38507a";
    g.fillRect(cx - 18, 60, 36, 4);
    g.fillStyle = "#1b2a48";
    g.fillRect(cx - 34, 64, 68, 3);
    g.fillStyle = "rgba(45, 212, 191, 0.35)";
    g.fillRect(cx - 30, 56, 10, 2);
    g.fillRect(cx + 20, 56, 10, 2);
    /* beacon housing */
    g.fillStyle = "#131f36";
    g.fillRect(cx - 6, 42, 12, 9);
    /* base sign */
    g.fillStyle = "#0b1220";
    g.fillRect(cx - 30, 84, 60, 9);
    g.fillStyle = "rgba(143, 160, 196, 0.5)";
    g.fillRect(cx - 28, 85, 56, 1);
  }

  _paintTowerDetails(g) {
    const cx = TOWER_X;
    const deckY = TOWER_DECK_Y;

    /* support column visible in the seam between the middle rooms */
    g.fillStyle = "#16233c";
    g.fillRect(cx - 5, deckY, 10, ROOM_FLOOR - deckY + 26);
    g.fillStyle = "#24324f";
    g.fillRect(cx - 5, deckY, 2, ROOM_FLOOR - deckY + 26);
    for (let y = deckY + 12; y < ROOM_FLOOR; y += 18) {
      g.fillStyle = "rgba(96, 165, 250, 0.16)";
      g.fillRect(cx - 5, y, 10, 2);
    }

    /* deck consoles: the lead's control post */
    for (const side of [-1, 1]) {
      const px = cx + side * 56 - 7;
      g.fillStyle = "#0b1220";
      g.fillRect(px, deckY - 18, 14, 10);
      g.fillStyle = "#1b2a48";
      g.fillRect(px, deckY - 18, 14, 1);
      g.fillStyle = "rgba(45, 212, 191, 0.65)";
      g.fillRect(px + 2, deckY - 16, 10, 1);
      g.fillStyle = "rgba(251, 191, 36, 0.5)";
      g.fillRect(px + 2, deckY - 14, 5, 1);
      g.fillStyle = "#65789c";
      g.fillRect(px + 6, deckY - 8, 2, 8);
    }

    /* deck sign, hanging just under the platform lip */
    g.fillStyle = "#060a12";
    g.fillRect(cx - 34, deckY + 2, 68, 9);
    g.fillStyle = "rgba(143, 160, 196, 0.45)";
    g.fillRect(cx - 34, deckY + 2, 68, 1);
    g.font = `bold 8px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = rgba("#e8eefc", 0.75);
    g.fillText("CONTROL", cx, deckY + 6);
    g.textAlign = "left";

    /* data links from the tower down to each room */
    const linkColor = "rgba(96, 165, 250, 0.28)";
    for (const room of ROOM_DEFS) {
      const tx = room.x + room.w / 2;
      const startX = cx + (tx < cx ? -52 : 52);
      const steps = 10;
      for (let i = 1; i < steps; i += 1) {
        const p = i / steps;
        const x = Math.round(lerp(startX, tx, p));
        const y = Math.round(lerp(deckY + 4, room.top - 3, p) + Math.sin(p * Math.PI) * -8);
        g.fillStyle = linkColor;
        g.fillRect(x - 1, y, 2, 1);
      }
    }

    /* under-deck truss */
    g.fillStyle = "#101a2e";
    for (let i = -2; i <= 2; i += 1) {
      if (i === 0) continue;
      g.fillRect(cx + i * 26 - 1, deckY + 2, 2, 12);
    }
    g.fillStyle = "#1b2a48";
    g.fillRect(cx - 58, deckY + 12, 116, 2);
  }

  _paintRooms(g) {
    for (const room of ROOM_DEFS) {
      this._paintRoom(g, room);
    }
  }

  _paintRoom(g, room) {
    const { x, w, top, floor, color, family } = room;
    const wall = mixColor(color, "#0b1220", 0.7);
    const trim = mixColor(color, "#000000", 0.1);

    /* top beam and side pillars */
    g.fillStyle = trim;
    g.fillRect(x, top - 5, w, 5);
    g.fillStyle = "rgba(7, 11, 22, 0.9)";
    g.fillRect(x, top - 2, w, 2);
    g.fillStyle = mixColor(color, "#000000", 0.55);
    g.fillRect(x, top, 3, floor - top + 12);
    g.fillRect(x + w - 3, top, 3, floor - top + 12);

    /* back wall */
    g.fillStyle = wall;
    g.fillRect(x + 3, top, w - 6, floor - top);
    g.fillStyle = rgba(color, 0.08);
    for (let sx = x + 16; sx < x + w - 10; sx += 34) {
      g.fillRect(sx, top + 5, 1, floor - top - 11);
    }
    g.fillStyle = rgba(color, 0.12);
    g.fillRect(x + 3, top + 54, w - 6, 1);
    /* ceiling pipe run, lamps and hanging cables */
    g.fillStyle = "#16233c";
    g.fillRect(x + 6, top + 9, w - 12, 2);
    g.fillStyle = "rgba(251, 191, 36, 0.22)";
    g.fillRect(x + 22, top + 9, 10, 2);
    g.fillRect(x + w - 50, top + 9, 14, 2);
    for (let lamp = 0; lamp < 3; lamp += 1) {
      const lx = x + 32 + lamp * 68;
      g.fillStyle = "#101a2e";
      g.fillRect(lx, top + 2, 2, 8);
      g.fillStyle = rgba(color, 0.55);
      g.fillRect(lx - 5, top + 10, 12, 2);
      g.fillStyle = rgba(color, 0.06);
      g.fillRect(lx - 8, top + 12, 18, 8);
    }

    /* floor slab */
    g.fillStyle = mixColor(color, "#0b1220", 0.8);
    g.fillRect(x + 3, floor - 10, w - 6, 10);
    g.fillStyle = rgba(color, 0.7);
    g.fillRect(x, floor - 2, w, 2);
    g.fillStyle = "rgba(7, 11, 22, 0.6)";
    for (let tx = x + 3; tx < x + w - 3; tx += 22) {
      g.fillRect(tx, floor - 10, 1, 9);
    }

    /* themed decor */
    if (family === "PREPARE") this._paintLibrary(g, x, w, top, floor);
    else if (family === "ARCHITECT") this._paintStudio(g, x, w, top, floor);
    else if (family === "CODE") this._paintWorkshop(g, x, w, top, floor);
    else this._paintRange(g, x, w, top, floor);

    /* room emblem on the back wall */
    this._paintRoomEmblem(g, family, x + w / 2, top + 28, color);

    /* name plaque under the floor */
    const plaqueW = 102;
    const plaqueX = x + Math.round((w - plaqueW) / 2);
    g.fillStyle = "#060a12";
    g.fillRect(plaqueX, floor + 3, plaqueW, 12);
    g.fillStyle = rgba(color, 0.75);
    g.fillRect(plaqueX, floor + 3, plaqueW, 1);
    g.fillRect(plaqueX, floor + 14, plaqueW, 1);
    g.fillRect(plaqueX, floor + 3, 1, 12);
    g.fillRect(plaqueX + plaqueW - 1, floor + 3, 1, 12);
    g.font = `bold 10px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = rgba(color, 0.95);
    g.fillText(family, x + w / 2, floor + 9);
    g.textAlign = "left";
  }

  _paintRoomEmblem(g, family, cx, cy, color) {
    const frame = "rgba(7, 11, 22, 0.85)";
    g.fillStyle = frame;
    g.fillRect(cx - 13, cy - 13, 26, 26);
    g.fillStyle = rgba(color, 0.5);
    g.fillRect(cx - 13, cy - 13, 26, 1);
    g.fillRect(cx - 13, cy + 12, 26, 1);
    g.fillRect(cx - 13, cy - 13, 1, 26);
    g.fillRect(cx + 12, cy - 13, 1, 26);
    if (family === "PREPARE") {
      g.fillStyle = "#e8eefc";
      g.fillRect(cx - 7, cy - 5, 6, 10);
      g.fillRect(cx + 1, cy - 5, 6, 10);
      g.fillStyle = color;
      g.fillRect(cx - 1, cy - 5, 2, 10);
      g.fillStyle = "rgba(7, 11, 22, 0.5)";
      g.fillRect(cx - 6, cy - 3, 4, 1);
      g.fillRect(cx + 2, cy - 3, 4, 1);
    } else if (family === "ARCHITECT") {
      g.fillStyle = color;
      g.fillRect(cx - 6, cy + 5, 13, 2);
      g.fillRect(cx - 5, cy + 2, 2, 3);
      g.fillRect(cx + 3, cy + 2, 2, 3);
      g.fillRect(cx - 1, cy - 6, 2, 8);
      g.fillStyle = "#e8eefc";
      g.fillRect(cx - 2, cy - 7, 4, 2);
    } else if (family === "CODE") {
      g.fillStyle = color;
      g.fillRect(cx - 6, cy - 2, 12, 4);
      g.fillRect(cx - 2, cy - 6, 4, 12);
      g.fillRect(cx - 6, cy - 6, 3, 3);
      g.fillRect(cx + 3, cy - 6, 3, 3);
      g.fillRect(cx - 6, cy + 3, 3, 3);
      g.fillRect(cx + 3, cy + 3, 3, 3);
      g.fillStyle = "#0b1220";
      g.fillRect(cx - 1, cy - 1, 2, 2);
    } else {
      for (let ring = 0; ring < 3; ring += 1) {
        const r = 9 - ring * 3;
        g.fillStyle = ring % 2 === 0 ? color : "#0b1220";
        g.fillRect(cx - r, cy - 1, r * 2, 2);
        g.fillRect(cx - 1, cy - r, 2, r * 2);
      }
      g.fillStyle = color;
      g.fillRect(cx - 1, cy - 1, 2, 2);
    }
  }

  _paintLibrary(g, x, w, top, floor) {
    const shelfX = x + 12;
    const shelfTop = top + 14;
    const shelfW = 58;
    const shelfH = floor - shelfTop - 16;
    g.fillStyle = "#16233c";
    g.fillRect(shelfX, shelfTop, shelfW, shelfH);
    g.fillStyle = "#24324f";
    g.fillRect(shelfX, shelfTop, shelfW, 2);
    g.fillRect(shelfX, shelfTop, 2, shelfH);
    g.fillRect(shelfX + shelfW - 2, shelfTop, 2, shelfH);
    const bookColors = ["#2dd4bf", "#60a5fa", "#fbbf24", "#fb7185", "#a78bfa", "#4ade80"];
    const rand = seededRandom(0xb00c);
    for (let shelf = 0; shelf < 3; shelf += 1) {
      const sy = shelfTop + 8 + shelf * 26;
      g.fillStyle = "#1b2a48";
      g.fillRect(shelfX + 2, sy + 20, shelfW - 4, 2);
      let bx = shelfX + 5;
      while (bx < shelfX + shelfW - 7) {
        const bw = 3 + Math.round(rand() * 3);
        const bh = 10 + Math.round(rand() * 8);
        g.fillStyle = bookColors[Math.floor(rand() * bookColors.length)];
        g.fillRect(bx, sy + 20 - bh, bw, bh);
        g.fillStyle = "rgba(7, 11, 22, 0.45)";
        g.fillRect(bx, sy + 20 - bh, bw, 1);
        bx += bw + 1;
      }
    }
    /* reading desk */
    g.fillStyle = "#1c2b46";
    g.fillRect(x + 118, floor - 34, 88, 5);
    g.fillStyle = "#24324f";
    g.fillRect(x + 118, floor - 34, 88, 1);
    g.fillRect(x + 122, floor - 29, 4, 19);
    g.fillRect(x + 198, floor - 29, 4, 19);
    g.fillStyle = "#e8eefc";
    g.fillRect(x + 138, floor - 40, 20, 7);
    g.fillStyle = "#8fa0c4";
    g.fillRect(x + 138, floor - 38, 20, 1);
    g.fillStyle = "#0b1220";
    g.fillRect(x + 139, floor - 37, 1, 4);
    /* lamp */
    g.fillStyle = "#2a3b5c";
    g.fillRect(x + 178, floor - 46, 2, 12);
    g.fillStyle = "rgba(45, 212, 191, 0.8)";
    g.fillRect(x + 174, floor - 50, 10, 4);
    g.fillStyle = "rgba(45, 212, 191, 0.12)";
    g.fillRect(x + 164, floor - 46, 30, 12);
    /* pinned notes */
    const noteColors = ["#fbbf24", "#2dd4bf", "#fb7185"];
    for (let i = 0; i < 3; i += 1) {
      g.fillStyle = noteColors[i];
      g.fillRect(x + 150 + i * 16, top + 18 + (i % 2) * 6, 9, 11);
      g.fillStyle = "rgba(7, 11, 22, 0.5)";
      g.fillRect(x + 150 + i * 16, top + 18 + (i % 2) * 6, 9, 1);
    }
  }

  _paintStudio(g, x, w, top, floor) {
    /* wall blueprints */
    const sheets = [
      { sx: x + 14, sy: top + 14, sw: 46, sh: 42 },
      { sx: x + 66, sy: top + 20, sw: 38, sh: 32 },
    ];
    for (const sheet of sheets) {
      g.fillStyle = "#12253f";
      g.fillRect(sheet.sx, sheet.sy, sheet.sw, sheet.sh);
      g.fillStyle = "rgba(96, 165, 250, 0.65)";
      g.fillRect(sheet.sx, sheet.sy, sheet.sw, 1);
      g.fillRect(sheet.sx, sheet.sy + sheet.sh - 1, sheet.sw, 1);
      g.fillRect(sheet.sx, sheet.sy, 1, sheet.sh);
      g.fillRect(sheet.sx + sheet.sw - 1, sheet.sy, 1, sheet.sh);
      for (let gy = sheet.sy + 6; gy < sheet.sy + sheet.sh - 3; gy += 7) {
        g.fillStyle = "rgba(96, 165, 250, 0.22)";
        g.fillRect(sheet.sx + 3, gy, sheet.sw - 6, 1);
      }
      g.fillStyle = "rgba(191, 233, 255, 0.85)";
      g.fillRect(sheet.sx + 6, sheet.sy + 9, 18, 1);
      g.fillRect(sheet.sx + 6, sheet.sy + 13, 26, 1);
      g.fillRect(sheet.sx + 10, sheet.sy + 22, 22, 8);
    }
    /* drafting table */
    g.fillStyle = "#1c2b46";
    g.fillRect(x + 112, floor - 30, 96, 4);
    g.fillStyle = "#2a3b5c";
    g.fillRect(x + 108, floor - 26, 104, 3);
    g.fillRect(x + 116, floor - 23, 4, 14);
    g.fillRect(x + 200, floor - 23, 4, 14);
    g.fillStyle = "#12253f";
    g.fillRect(x + 126, floor - 40, 58, 10);
    g.fillStyle = "rgba(96, 165, 250, 0.5)";
    for (let gx = x + 130; gx < x + 180; gx += 8) g.fillRect(gx, floor - 38, 1, 6);
    /* stool */
    g.fillStyle = "#24324f";
    g.fillRect(x + 188, floor - 20, 14, 3);
    g.fillRect(x + 194, floor - 17, 2, 8);
    /* compass + tools */
    g.fillStyle = "#a9bbd9";
    g.fillRect(x + 166, top + 66, 14, 2);
    g.fillRect(x + 176, top + 60, 2, 10);
    g.fillStyle = "rgba(251, 191, 36, 0.8)";
    g.fillRect(x + 160, top + 72, 6, 3);
  }

  _paintWorkshop(g, x, w, top, floor) {
    /* pegboard + tools */
    g.fillStyle = "#152238";
    g.fillRect(x + 10, top + 12, 58, 40);
    g.fillStyle = "rgba(96, 165, 250, 0.18)";
    for (let gy = top + 18; gy < top + 48; gy += 8) {
      for (let gx = x + 16; gx < x + 62; gx += 9) g.fillRect(gx, gy, 1, 1);
    }
    g.fillStyle = "#a9bbd9";
    g.fillRect(x + 20, top + 20, 2, 14);
    g.fillRect(x + 30, top + 18, 8, 2);
    g.fillRect(x + 44, top + 22, 2, 12);
    g.fillStyle = "#fb7185";
    g.fillRect(x + 52, top + 20, 5, 4);
    /* three monitors on the bench */
    const screens = [x + 76, x + 116, x + 156];
    for (const sx of screens) {
      g.fillStyle = "#101a2e";
      g.fillRect(sx, top + 34, 34, 24);
      g.fillStyle = "#24324f";
      g.fillRect(sx, top + 34, 34, 2);
      g.fillRect(sx + 15, top + 58, 4, 6);
      g.fillStyle = "#050914";
      g.fillRect(sx + 3, top + 37, 28, 17);
      g.fillStyle = "rgba(251, 191, 36, 0.55)";
      g.fillRect(sx + 6, top + 40, 8, 1);
      g.fillStyle = "rgba(96, 165, 250, 0.55)";
      g.fillRect(sx + 6, top + 43, 14, 1);
      g.fillStyle = "rgba(74, 222, 128, 0.55)";
      g.fillRect(sx + 6, top + 46, 6, 1);
    }
    /* workbench */
    g.fillStyle = "#1c2b46";
    g.fillRect(x + 66, floor - 36, 144, 6);
    g.fillStyle = "#2a3b5c";
    g.fillRect(x + 66, floor - 36, 144, 2);
    g.fillRect(x + 72, floor - 30, 4, 20);
    g.fillRect(x + 200, floor - 30, 4, 20);
    g.fillStyle = "#a9bbd9";
    g.fillRect(x + 78, floor - 40, 10, 4);
    g.fillStyle = "#fb7185";
    g.fillRect(x + 92, floor - 42, 6, 6);
    /* conveyor belt */
    g.fillStyle = "#1b2438";
    g.fillRect(x + 8, floor - 14, w - 16, 12);
    g.fillStyle = "#0f1728";
    g.fillRect(x + 10, floor - 12, w - 20, 7);
    g.fillStyle = "#33456b";
    for (let rx = x + 14; rx < x + w - 12; rx += 12) {
      g.fillRect(rx, floor - 11, 2, 5);
    }
    g.fillStyle = "rgba(251, 191, 36, 0.35)";
    g.fillRect(x + 8, floor - 14, w - 16, 1);
    /* robot arm */
    g.fillStyle = "#24324f";
    g.fillRect(x + w - 30, floor - 52, 4, 22);
    g.fillRect(x + w - 42, floor - 54, 14, 4);
    g.fillStyle = "#fbbf24";
    g.fillRect(x + w - 44, floor - 58, 6, 6);
    /* gears */
    g.fillStyle = "rgba(251, 191, 36, 0.55)";
    g.fillRect(x + 10, top + 62, 10, 10);
    g.fillStyle = "#0b1220";
    g.fillRect(x + 13, top + 65, 4, 4);
  }

  _paintRange(g, x, w, top, floor) {
    /* faux corridor wall panels */
    g.fillStyle = "rgba(244, 114, 182, 0.08)";
    g.fillRect(x + 6, top + 10, w - 12, 3);
    /* target boards */
    const drawTarget = (cx, cy, radius, colorA, colorB) => {
      for (let r = radius; r > 2; r -= 5) {
        g.fillStyle = Math.floor(r / 5) % 2 === 0 ? colorB : colorA;
        g.fillRect(cx - r, cy - r, r * 2, 2);
        g.fillRect(cx - r, cy + r - 2, r * 2, 2);
        g.fillRect(cx - r, cy - r, 2, r * 2);
        g.fillRect(cx + r - 2, cy - r, 2, r * 2);
      }
      g.fillStyle = colorB;
      g.fillRect(cx - 2, cy - 2, 4, 4);
    };
    drawTarget(x + 44, top + 44, 24, "rgba(26, 12, 28, 0.9)", "rgba(244, 114, 182, 0.7)");
    drawTarget(x + 100, top + 38, 13, "rgba(26, 12, 28, 0.9)", "rgba(244, 114, 182, 0.6)");
    /* range lane stripes */
    g.fillStyle = "rgba(244, 114, 182, 0.22)";
    for (let ly = top + 70; ly < floor - 16; ly += 12) {
      g.fillRect(x + 8, ly, w - 16, 1);
    }
    /* test-tube rack */
    g.fillStyle = "#16233c";
    g.fillRect(x + 148, floor - 38, 58, 24);
    g.fillStyle = "#24324f";
    g.fillRect(x + 148, floor - 38, 58, 2);
    g.fillRect(x + 148, floor - 20, 58, 2);
    const liquids = ["#f472b6", "#22d3ee", "#4ade80", "#fbbf24"];
    for (let i = 0; i < 4; i += 1) {
      const tx = x + 154 + i * 13;
      g.fillStyle = "rgba(191, 233, 255, 0.8)";
      g.fillRect(tx, floor - 48, 4, 12);
      g.fillStyle = liquids[i];
      g.fillRect(tx, floor - 42, 4, 6);
      g.fillStyle = "#65789c";
      g.fillRect(tx - 1, floor - 49, 6, 2);
    }
    /* bug net leaning on the wall */
    g.fillStyle = "#a9bbd9";
    g.fillRect(x + 122, top + 72, 2, 40);
    g.fillStyle = "rgba(191, 233, 255, 0.5)";
    g.fillRect(x + 118, top + 64, 12, 10);
    g.fillStyle = "#65789c";
    g.fillRect(x + 118, top + 68, 12, 1);
    /* range markers on the floor */
    g.fillStyle = "rgba(244, 114, 182, 0.4)";
    for (let mx = x + 16; mx < x + w - 16; mx += 16) {
      g.fillRect(mx, floor - 5, 8, 1);
    }
  }

  _paintPlaza(g) {
    const left = -this.offsetX;
    const width = this.viewW;

    /* corridor between the rooms and the plaza */
    g.fillStyle = "#080d18";
    g.fillRect(left, CORRIDOR_Y - 8, width, CORRIDOR_Y - ROOM_FLOOR + 8);
    g.fillStyle = "#0d1526";
    g.fillRect(left, CORRIDOR_Y - 2, width, 8);
    g.fillStyle = "rgba(56, 80, 122, 0.55)";
    for (let dx = left + 6; dx < left + width; dx += 26) {
      g.fillRect(dx, CORRIDOR_Y + 2, 12, 1);
    }
    g.fillStyle = "rgba(56, 80, 122, 0.9)";
    g.fillRect(left, CORRIDOR_Y - 2, width, 1);

    /* plaza floor */
    const plaza = g.createLinearGradient(0, PLAZA_TOP, 0, GROUND_FLOOR + 20);
    plaza.addColorStop(0, "#0b1220");
    plaza.addColorStop(1, "#070c16");
    g.fillStyle = plaza;
    g.fillRect(left, PLAZA_TOP, width, GROUND_FLOOR + 24 - PLAZA_TOP);
    g.fillStyle = "rgba(56, 80, 122, 0.28)";
    for (let gy = PLAZA_TOP + 22; gy < GROUND_FLOOR + 20; gy += 22) {
      g.fillRect(left, gy, width, 1);
    }
    g.fillStyle = "rgba(36, 50, 79, 0.55)";
    for (let gx = left + ((left % 48) + 48) % 48; gx < left + width; gx += 48) {
      g.fillRect(gx, PLAZA_TOP, 1, GROUND_FLOOR + 24 - PLAZA_TOP);
    }
    g.fillStyle = "rgba(143, 160, 196, 0.18)";
    g.fillRect(left, GROUND_FLOOR + 2, width, 1);

    this._paintLounge(g);
    this._paintReviewDeck(g);
    this._paintGates(g);
    this._paintWrapBoard(g);

    /* mascot charging pad */
    g.fillStyle = "#0d1526";
    g.fillRect(842, GROUND_FLOOR - 10, 72, 8);
    g.fillStyle = "rgba(34, 211, 238, 0.35)";
    g.fillRect(848, GROUND_FLOOR - 8, 60, 1);
    g.fillRect(848, GROUND_FLOOR - 5, 60, 1);
    g.fillStyle = "rgba(163, 230, 53, 0.2)";
    g.fillRect(842, GROUND_FLOOR - 10, 2, 8);
    g.fillRect(912, GROUND_FLOOR - 10, 2, 8);
  }

  _paintLounge(g) {
    const x = LOUNGE.x;
    const top = LOUNGE.top;
    const floor = LOUNGE.floor;

    /* area rug */
    g.fillStyle = "rgba(56, 80, 122, 0.22)";
    g.fillRect(x + 12, floor - 26, LOUNGE.w - 24, 22);
    g.fillStyle = "rgba(96, 165, 250, 0.18)";
    g.fillRect(x + 12, floor - 26, LOUNGE.w - 24, 1);
    g.fillRect(x + 12, floor - 5, LOUNGE.w - 24, 1);

    /* couches */
    const couch = (cx) => {
      g.fillStyle = "#1b2a48";
      g.fillRect(cx, top + 12, 84, 26);
      g.fillStyle = "#24324f";
      g.fillRect(cx, top + 12, 84, 4);
      g.fillRect(cx, top + 12, 4, 26);
      g.fillRect(cx + 80, top + 12, 4, 26);
      g.fillStyle = "#16233c";
      g.fillRect(cx + 6, top + 34, 72, 8);
      g.fillStyle = "#2a3b5c";
      g.fillRect(cx + 2, top + 40, 8, 4);
      g.fillRect(cx + 74, top + 40, 8, 4);
      g.fillStyle = "rgba(251, 191, 36, 0.3)";
      g.fillRect(cx + 34, top + 16, 16, 10);
    };
    couch(x + 16);
    couch(x + 112);
    /* coffee table */
    g.fillStyle = "#1c2b46";
    g.fillRect(x + 76, top + 54, 68, 5);
    g.fillRect(x + 82, top + 59, 3, 10);
    g.fillRect(x + 136, top + 59, 3, 10);
    g.fillStyle = "#e8eefc";
    g.fillRect(x + 96, top + 50, 12, 3);
    g.fillStyle = "#fb7185";
    g.fillRect(x + 116, top + 50, 6, 3);
    /* plant */
    g.fillStyle = "#2a1d16";
    g.fillRect(x + 240, top + 60, 12, 12);
    g.fillStyle = "#4ade80";
    g.fillRect(x + 238, top + 50, 4, 10);
    g.fillRect(x + 244, top + 46, 4, 14);
    g.fillRect(x + 250, top + 52, 4, 9);
    g.fillStyle = "rgba(74, 222, 128, 0.35)";
    g.fillRect(x + 236, top + 48, 20, 2);
    /* vending machine */
    g.fillStyle = "#101a2e";
    g.fillRect(x + 220, top + 14, 34, 56);
    g.fillStyle = "#16233c";
    g.fillRect(x + 223, top + 17, 28, 34);
    g.fillStyle = "rgba(251, 191, 36, 0.5)";
    for (let vy = top + 20; vy < top + 48; vy += 8) g.fillRect(x + 226, vy, 22, 2);
    g.fillStyle = "rgba(34, 211, 238, 0.65)";
    g.fillRect(x + 226, top + 54, 22, 6);
    /* standby sign */
    g.fillStyle = "#060a12";
    g.fillRect(x + 92, top + 4, 90, 13);
    g.fillStyle = "rgba(143, 160, 196, 0.65)";
    g.fillRect(x + 92, top + 4, 90, 1);
    g.fillRect(x + 92, top + 16, 90, 1);
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(143, 160, 196, 0.95)";
    g.fillText("STANDBY LOUNGE", x + 137, top + 11);
    g.textAlign = "left";
  }

  _paintReviewDeck(g) {
    const x = REVIEW_DECK.x;
    const top = REVIEW_DECK.top;
    const floor = REVIEW_DECK.floor;
    /* platform */
    g.fillStyle = "#0d1526";
    g.fillRect(x, floor - 24, REVIEW_DECK.w, 24);
    g.fillStyle = "#16233c";
    g.fillRect(x + 6, floor - 20, REVIEW_DECK.w - 12, 6);
    g.fillStyle = "#1b2a48";
    g.fillRect(x + 16, floor - 14, REVIEW_DECK.w - 32, 5);
    g.fillStyle = "rgba(96, 165, 250, 0.35)";
    g.fillRect(x + 16, floor - 14, REVIEW_DECK.w - 32, 1);
    /* screen */
    g.fillStyle = "#101a2e";
    g.fillRect(x + 92, top, 100, 44);
    g.fillStyle = "#24324f";
    g.fillRect(x + 92, top, 100, 2);
    g.fillStyle = "#050914";
    g.fillRect(x + 97, top + 5, 90, 32);
    g.fillStyle = "#16233c";
    g.fillRect(x + 132, top + 44, 20, 10);
    g.fillRect(x + 122, top + 54, 40, 3);
    /* table with review papers */
    g.fillStyle = "#1c2b46";
    g.fillRect(x + 58, top + 66, 168, 5);
    g.fillRect(x + 64, top + 71, 4, 14);
    g.fillRect(x + 216, top + 71, 4, 14);
    g.fillStyle = "#e8eefc";
    g.fillRect(x + 92, top + 62, 14, 4);
    g.fillRect(x + 136, top + 62, 20, 4);
    g.fillRect(x + 186, top + 62, 10, 4);
    /* stools */
    g.fillStyle = "#24324f";
    g.fillRect(x + 24, top + 76, 14, 3);
    g.fillRect(x + 30, top + 79, 2, 8);
    g.fillRect(x + 246, top + 76, 14, 3);
    g.fillRect(x + 252, top + 79, 2, 8);
    /* deck label */
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(96, 165, 250, 0.9)";
    g.fillText("REVIEW DECK", x + REVIEW_DECK.w / 2, floor - 17);
    g.textAlign = "left";
  }

  _paintGates(g) {
    const x = GATES.x;
    const top = GATES.top;
    const floor = GATES.floor;
    /* display frame */
    g.fillStyle = "#060a12";
    g.fillRect(x + 8, top, GATES.w - 16, 26);
    g.fillStyle = "rgba(251, 191, 36, 0.6)";
    g.fillRect(x + 8, top, GATES.w - 16, 1);
    g.fillRect(x + 8, top + 25, GATES.w - 16, 1);
    g.fillRect(x + 8, top, 1, 26);
    g.fillRect(x + GATES.w - 9, top, 1, 26);
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(251, 191, 36, 0.9)";
    g.fillText("GATES", x + 14, top + 9);
    /* turnstile booths */
    const booths = [x + 20, x + 84];
    for (const bx of booths) {
      g.fillStyle = "#16233c";
      g.fillRect(bx, floor - 30, 46, 30);
      g.fillStyle = "#1b2a48";
      g.fillRect(bx, floor - 30, 46, 2);
      g.fillStyle = "#0b1220";
      g.fillRect(bx + 4, floor - 26, 38, 20);
      g.fillStyle = "#24324f";
      g.fillRect(bx + 4, floor - 26, 38, 1);
      g.fillStyle = "#65789c";
      g.fillRect(bx + 22, floor - 26, 2, 20);
      g.fillStyle = "rgba(74, 222, 128, 0.55)";
      g.fillRect(bx + 8, floor - 32, 30, 2);
    }
    /* label */
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(143, 160, 196, 0.8)";
    g.fillText("GATE CONTROL", x + GATES.w / 2, floor + 9);
    g.textAlign = "left";
  }

  _paintWrapBoard(g) {
    const x = PR_BOARD.x;
    const top = PR_BOARD.top;
    g.fillStyle = "#060a12";
    g.fillRect(x, top, PR_BOARD.w, PR_BOARD.h);
    g.fillStyle = "rgba(163, 230, 53, 0.5)";
    g.fillRect(x, top, PR_BOARD.w, 1);
    g.fillRect(x, top + PR_BOARD.h - 1, PR_BOARD.w, 1);
    g.fillRect(x, top, 1, PR_BOARD.h);
    g.fillRect(x + PR_BOARD.w - 1, top, 1, PR_BOARD.h);
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(163, 230, 53, 0.85)";
    g.fillText("WRAP-UP", x + 6, top + 9);
    g.fillStyle = "rgba(143, 160, 196, 0.22)";
    g.fillRect(x + 6, top + 14, PR_BOARD.w - 12, 1);
  }

  _paintGroundExtension(g) {
    const top = GROUND_FLOOR + 8;
    const bottom = -this.offsetY + this.viewH;
    if (bottom <= top) return;
    g.fillStyle = "#050912";
    g.fillRect(-this.offsetX, top, this.viewW, bottom - top);
    /* perspective runway lines under the floor */
    const center = LOGICAL_W / 2;
    for (let i = 1; i < 7; i += 1) {
      const y = top + i * 9;
      g.fillStyle = `rgba(36, 50, 79, ${0.5 - i * 0.05})`;
      g.fillRect(-this.offsetX, Math.round(y), this.viewW, 1);
    }
    for (let i = -6; i <= 6; i += 1) {
      g.fillStyle = "rgba(36, 50, 79, 0.35)";
      for (let step = 0; step < 5; step += 1) {
        const y = top + 4 + step * 12;
        const spread = (i * (12 + step * 10));
        g.fillRect(Math.round(center + spread), Math.round(y), 2, 2);
      }
    }
  }

  _paintForeground(g) {
    const left = -this.offsetX;
    const width = this.viewW;
    const bottom = -this.offsetY + this.viewH;
    const floorTop = GROUND_FLOOR + 4;
    const bandHeight = bottom - floorTop;
    if (bandHeight < 48) return;

    const deskHeight = Math.min(88, Math.max(44, Math.round(bandHeight * 0.52)));
    const deskTop = bottom - deskHeight;

    this._paintServiceLevel(g, left, width, floorTop, deskTop);
    this._paintObservationDeck(g, left, width, deskTop, bottom);
  }

  _paintServiceLevel(g, left, width, top, bottom) {
    const height = bottom - top;
    if (height <= 2) return;

    /* grated lower-level floor with walkway panels */
    g.fillStyle = "#05090f";
    g.fillRect(left, top, width, height);
    for (let y = top + 4; y < bottom - 1; y += 5) {
      g.fillStyle = y % 10 < 5 ? "rgba(56, 80, 122, 0.2)" : "rgba(36, 50, 79, 0.16)";
      g.fillRect(left, y, width, 1);
    }
    for (let x = left + 16; x < left + width; x += 46) {
      g.fillStyle = "rgba(36, 50, 79, 0.26)";
      g.fillRect(x, top + 2, 1, height - 4);
    }

    /* hazard lip at the plaza edge */
    for (let hx = left; hx < left + width; hx += 20) {
      g.fillStyle = "rgba(251, 191, 36, 0.16)";
      g.fillRect(Math.round(hx), top + 1, 10, 2);
    }

    /* conduit runs feeding the camera deck */
    const pipeY = bottom - 14;
    g.fillStyle = "#101a2e";
    g.fillRect(left, pipeY, width, 7);
    g.fillStyle = "#1b2a48";
    g.fillRect(left, pipeY, width, 1);
    g.fillStyle = "rgba(34, 211, 238, 0.2)";
    g.fillRect(left, pipeY + 5, width, 1);
    for (let x = left + 26; x < left + width; x += 88) {
      g.fillStyle = "#24324f";
      g.fillRect(x, pipeY - 2, 6, 11);
      g.fillStyle = "rgba(34, 211, 238, 0.3)";
      g.fillRect(x + 2, pipeY - 1, 2, 1);
    }
    g.fillStyle = "#0d1526";
    g.fillRect(left, pipeY - 7, width, 2);

    /* maintenance crates and a cable reel on the lower level */
    const props = [
      { f: 0.15, w: 12, h: 9, c: "#33456b" },
      { f: 0.3, w: 9, h: 7, c: "#3d5378" },
      { f: 0.66, w: 14, h: 8, c: "#33456b" },
    ];
    for (const prop of props) {
      const px = Math.round(left + width * prop.f);
      const py = pipeY - 10;
      g.fillStyle = prop.c;
      g.fillRect(px, py, prop.w, prop.h);
      g.fillStyle = "rgba(251, 191, 36, 0.35)";
      g.fillRect(px, py, prop.w, 1);
      g.fillStyle = "rgba(7, 11, 22, 0.6)";
      g.fillRect(px, py + 4, prop.w, 1);
    }
    const reelX = Math.round(left + width * 0.86);
    g.fillStyle = "#16233c";
    g.fillRect(reelX, pipeY - 16, 14, 14);
    g.fillStyle = "#24324f";
    g.fillRect(reelX + 3, pipeY - 13, 8, 8);
    g.fillStyle = "rgba(251, 191, 36, 0.45)";
    g.fillRect(reelX + 6, pipeY - 10, 2, 2);

    /* stencilled lower-level label */
    g.font = `bold 9px ${FONT_STACK}`;
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(143, 160, 196, 0.34)";
    g.fillText("SERVICE LEVEL 07 · CONDUIT BAY", left + 16, top + 12);
    g.fillStyle = "rgba(143, 160, 196, 0.2)";
    g.fillRect(left + 16, top + 17, 132, 1);

    /* floor lights along the walkway (animated in the ambient pass) */
    this.runwayLights = [];
    for (let i = 0; i < 11; i += 1) {
      this.runwayLights.push({
        x: Math.round(left + 30 + (i * (width - 60)) / 10),
        y: top + 6,
      });
    }
  }

  _paintObservationDeck(g, left, width, top, bottom) {
    const height = bottom - top;
    if (height < 26) return;

    /* console bank rising behind the deck lip */
    const consoles = [
      { f: 0.1, w: 158, h: 48, label: "COMMS", color: C.info },
      { f: 0.31, w: 116, h: 36, label: "SENSOR", color: C.success },
      { f: 0.51, w: 178, h: 56, label: "RADAR", color: C.warning },
      { f: 0.75, w: 132, h: 42, label: "CAM 04", color: "#f472b6" },
      { f: 0.91, w: 90, h: 34, label: "POWER", color: "#22d3ee" },
    ];
    const baseY = top + 4;
    g.fillStyle = "#0d1526";
    g.fillRect(left, baseY - 6, width, 6);
    for (let i = 0; i < consoles.length; i += 1) {
      const consoleDef = consoles[i];
      const cx = left + width * consoleDef.f;
      this._paintConsoleBack(g, cx, baseY, consoleDef, i);
    }

    /* desk lip and fascia panel */
    g.fillStyle = "#1b2a48";
    g.fillRect(left, top, width, 3);
    g.fillStyle = "rgba(163, 230, 53, 0.2)";
    g.fillRect(left, top, width, 1);
    g.fillStyle = "#060a12";
    g.fillRect(left, top + 3, width, height - 3);
    for (let x = left + 24; x < left + width; x += 78) {
      g.fillStyle = "rgba(36, 50, 79, 0.45)";
      g.fillRect(x, top + 5, 1, height - 10);
    }
    /* fascia vents */
    for (let x = left + 42; x < left + width - 30; x += 78) {
      for (let row = 0; row < 3; row += 1) {
        g.fillStyle = "rgba(36, 50, 79, 0.5)";
        g.fillRect(x, bottom - 24 + row * 4, 30, 1);
      }
    }
    /* angled support brackets under the deck lip */
    for (let x = left + 40; x < left + width - 40; x += 150) {
      g.fillStyle = "rgba(27, 42, 72, 0.9)";
      g.fillRect(x, top + 4, 16, 4);
      g.fillRect(x + 3, top + 8, 10, 4);
      g.fillStyle = "rgba(56, 80, 122, 0.35)";
      g.fillRect(x, top + 4, 16, 1);
    }
    /* fascia status strip */
    for (let x = left + 58; x < left + width - 40; x += 96) {
      g.fillStyle = "rgba(36, 50, 79, 0.55)";
      g.fillRect(x, top + 16, 62, 3);
      g.fillStyle = "rgba(74, 222, 128, 0.25)";
      g.fillRect(x + 2, top + 17, 3, 1);
      g.fillStyle = "rgba(251, 191, 36, 0.2)";
      g.fillRect(x + 8, top + 17, 2, 1);
    }
    /* wide stencil wordmark on the fascia */
    g.font = `bold 26px ${FONT_STACK}`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = "rgba(56, 80, 122, 0.14)";
    g.fillText("MISSION CONTROL", LOGICAL_W / 2, top + (height - 3) / 2 - 2);
    /* stencilled deck label */
    g.font = `bold 10px ${FONT_STACK}`;
    g.fillStyle = "rgba(143, 160, 196, 0.42)";
    g.fillText("OBSERVATION DECK · CAMERA BANK 01-04", LOGICAL_W / 2, bottom - 11);
    g.textAlign = "left";
    /* corner hazard marks */
    for (let i = 0; i < 5; i += 1) {
      g.fillStyle = "rgba(251, 191, 36, 0.16)";
      g.fillRect(left + 10 + i * 12, bottom - 5, 7, 2);
      g.fillRect(left + width - 70 + i * 12, bottom - 5, 7, 2);
    }

    /* the camera crew silhouetted against their consoles */
    this._paintOperator(g, left + width * 0.2, top, bottom, 1.2);
    this._paintOperator(g, left + width * 0.66, top, bottom, 1.02);

    /* cables dropping from the consoles into the deck */
    for (let i = 0; i < consoles.length; i += 1) {
      const cx = left + width * consoles[i].f;
      g.fillStyle = "rgba(36, 50, 79, 0.7)";
      const drop = 6 + (i % 3) * 3;
      g.fillRect(Math.round(cx - consoles[i].w / 2) + 8, top - drop, 2, drop);
      g.fillRect(Math.round(cx + consoles[i].w / 2) - 10, top - drop - 2, 1, drop + 2);
    }
  }

  _paintConsoleBack(g, cx, baseY, consoleDef, index) {
    const w = consoleDef.w;
    const h = consoleDef.h;
    const x = Math.round(cx - w / 2);
    const top = baseY - h;
    const color = consoleDef.color;

    /* stepped monitor-back silhouette */
    g.fillStyle = "#0b1220";
    g.fillRect(x, top, w, h);
    g.fillStyle = "#101a2e";
    g.fillRect(x + 10, top - 6, w - 20, 6);
    g.fillStyle = "#131f36";
    g.fillRect(x, top, w, 2);
    g.fillRect(x, top, 2, h);
    g.fillRect(x + w - 2, top, 2, h);
    /* ventilation slots */
    const ventTop = top + 12;
    const ventRows = Math.max(1, Math.floor((h - 16) / 6));
    for (let row = 0; row < ventRows; row += 1) {
      g.fillStyle = "rgba(36, 50, 79, 0.55)";
      g.fillRect(x + 10, ventTop + row * 6, w - 20, 2);
      g.fillStyle = "rgba(7, 11, 22, 0.65)";
      g.fillRect(x + 12, ventTop + row * 6 + 1, 3, 1);
    }
    /* label plate */
    g.fillStyle = "#060a12";
    g.fillRect(x + 8, top + 2, Math.max(34, consoleDef.label.length * 6 + 10), 10);
    g.font = `bold 8px ${FONT_STACK}`;
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillStyle = rgba(color, 0.9);
    g.fillText(consoleDef.label, x + 12, top + 7);

    /* glow spill over the top edge + status LEDs (animated) */
    this.consoleGlows.push({
      x: x + 8,
      y: top - 2,
      w: w - 16,
      color,
      phase: index * 1.3,
    });
    for (let i = 0; i < 4; i += 1) {
      const lx = Math.round(x + w - 18 - i * 8);
      const ly = top + 6;
      g.fillStyle = rgba(color, 0.2);
      g.fillRect(lx, ly, 3, 2);
      this.consoleLeds.push({ x: lx, y: ly, color, phase: i * 0.7 + index });
    }
    if (consoleDef.label === "RADAR") {
      this.consoleRadars.push({ cx: x + w / 2, cy: top + 22, r: 12, phase: index });
    }
    if (index === 0) {
      this.consoleTally = { x: x + w - 8, y: top + 2 };
    }
  }

  _paintOperator(g, cx, deskTop, bottom, scale) {
    const x = Math.round(cx);
    const headSize = Math.round(13 * scale);
    const headTop = deskTop - Math.round(15 * scale);
    const shoulderTop = deskTop + Math.round(4 * scale);
    const shoulderW = Math.round(52 * scale);
    const dark = "#03060b";
    /* shoulders */
    g.fillStyle = dark;
    g.fillRect(x - shoulderW / 2, shoulderTop, shoulderW, bottom - shoulderTop);
    g.fillRect(x - shoulderW / 2 - 4, shoulderTop + 8, 8, bottom - shoulderTop - 8);
    g.fillRect(x + shoulderW / 2 - 4, shoulderTop + 8, 8, bottom - shoulderTop - 8);
    /* head */
    g.fillRect(x - headSize / 2, headTop, headSize, headSize);
    g.fillRect(x - headSize / 2 + 3, headTop - 2, headSize - 6, 2);
    /* rim light from the screens above */
    g.fillStyle = "rgba(96, 165, 250, 0.28)";
    g.fillRect(x - headSize / 2, headTop, headSize, 1);
    g.fillStyle = "rgba(96, 165, 250, 0.16)";
    g.fillRect(x - shoulderW / 2 + 3, shoulderTop, shoulderW - 6, 1);
    /* headset band */
    g.fillStyle = "#0b1220";
    g.fillRect(x - headSize / 2, headTop + 2, 2, 5);
    g.fillRect(x + headSize / 2 - 2, headTop + 2, 2, 5);
  }

  /* ---- event effects --------------------------------------------------- */

  _wrapup() {
    return this.cursorWrapup || null;
  }

  _wrapupVisible() {
    return Boolean(this._wrapup()) && this.endEventIdx >= 0;
  }

  _roomPoint(family) {
    const room = ROOM_BY_FAMILY[family];
    if (!room) return { x: LOGICAL_W / 2, y: ROOM_FLOOR - 20 };
    return { x: room.x + room.w / 2, y: room.floor - 24 };
  }

  _agentPoint(name) {
    if (!name) return null;
    const agent = this.agents.get(name);
    if (!agent) return null;
    return { x: agent.x, y: agent.y - 20 };
  }

  _phaseRoomForEvent(event) {
    return familyOf(event && event.phase) || this.phaseState.current || "CODE";
  }

  _spawnForEvent(event) {
    if (!event || typeof event !== "object") return;
    const type = String(event.type || "");
    const icon = String(event.icon || "");
    const family = this._phaseRoomForEvent(event);
    const room = ROOM_BY_FAMILY[family];
    const roomColor = room ? room.color : C.muted;
    const target = this._agentPoint(event.agent) || this._roomPoint(family);
    const severity = String(event.severity || "info");
    const color = severityColor(severity, roomColor);
    const tower = { x: TOWER_X, y: TOWER_DECK_Y - 26 };
    const review = { x: REVIEW_DECK.x + REVIEW_DECK.w / 2, y: REVIEW_DECK.top + 20 };

    const stamp = () => {
      const wrapup = wrapupOf(event);
      const state = String(wrapup.pr_state || "").toUpperCase() || "SHIPPED";
      const number = wrapup.pr_number !== undefined && wrapup.pr_number !== null ? ` #${wrapup.pr_number}` : "";
      const wrapColor = state === "MERGED" ? C.success : state === "OPEN" ? C.warning : C.muted;
      this._pushEffect({
        kind: "stamp",
        text: "SHIPPED",
        sub: `PR${number} ${state}`,
        color: wrapColor,
        layer: 1,
        dur: 3.4,
        x: LOGICAL_W / 2,
        y: 214,
      });
      if (state === "MERGED") this._pushEffect(this._makeConfetti(70));
      this._addShake(2.6, 0.5);
    };

    switch (type) {
      case "session_end":
        /* Only a wrap-up ships; a bare session_end is a process exit. */
        if (isWrapup(event)) stamp();
        return;
      case "session_start":
        this._pushEffect({
          kind: "banner",
          text: String(event.title || "RUN START").toUpperCase(),
          sub: (this.model && this.model.meta && this.model.meta.project) || "",
          color: ACCENT,
          dur: 2.0,
          y: 120,
        });
        return;
      case "team_realigned":
        this._pushEffect({ kind: "warp", dur: 1.15, color: "#a78bfa", label: "TEAM REALIGNED" });
        return;
      case "compaction_attributed":
        this._pushEffect({ kind: "wipe", dur: 0.9, color: "#22d3ee", label: "CONTEXT COMPACTED" });
        return;
      case "algedonic_signal":
      case "blocker_raised":
        this._pushEffect({
          kind: "siren",
          dur: 1.8,
          color: C.danger,
          label: String(event.title || "ALGEDONIC ALERT").toUpperCase(),
        });
        this._addShake(3.4, 0.9);
        return;
      case "pr_ready": {
        this._pushEffect(this._makeConfetti(56));
        this._pushEffect({
          kind: "banner",
          text: "PR READY",
          sub: String(event.title || "").replace(/^PR ready\s*[·-]?\s*/i, "") || "review complete",
          color: C.success,
          dur: 2.0,
          y: 120,
        });
        return;
      }
      case "review_dispatch":
        this._pushEffect({
          kind: "envelope",
          x1: tower.x,
          y1: tower.y,
          x2: review.x,
          y2: review.y,
          color: C.info,
          dur: 1.25,
          label: "REVIEW",
        });
        this._pushEffect({ kind: "ring", x: review.x, y: review.y, color: C.info, dur: 0.8 });
        return;
      case "agent_dispatch":
      case "dispatch_decision":
        this._pushEffect({
          kind: "beam",
          x1: tower.x,
          y1: tower.y,
          x2: target.x,
          y2: target.y,
          color: roomColor,
          dur: 0.9,
          label: type === "dispatch_decision" ? "GATE OK" : "",
        });
        this._pushEffect({ kind: "ring", x: target.x, y: target.y, color: roomColor, dur: 0.7 });
        if (type === "dispatch_decision") {
          this._pushEffect({ kind: "gateFlash", color: C.success, label: "ALLOW", dur: 1.0 });
        }
        return;
      case "teachback_ack":
        this._pushEffect({ kind: "check", x: target.x, y: target.y, color: C.success, dur: 1.0 });
        return;
      case "agent_handoff": {
        const nextIndex = PHASE_ORDER.indexOf(family) + 1;
        const nextRoom = PHASE_ORDER[nextIndex] ? ROOM_BY_FAMILY[PHASE_ORDER[nextIndex]] : null;
        const to = nextRoom
          ? { x: nextRoom.x + nextRoom.w / 2, y: nextRoom.floor - 26 }
          : { x: review.x, y: review.y };
        this._pushEffect({
          kind: "envelope",
          x1: target.x,
          y1: target.y,
          x2: to.x,
          y2: to.y,
          color: roomColor,
          dur: 1.25,
          label: "HANDOFF",
        });
        if (nextRoom) {
          this._pushEffect({ kind: "ring", x: to.x, y: to.y, color: nextRoom.color, dur: 0.8 });
        }
        return;
      }
      case "commit": {
        const sha = this._shaForEvent(event);
        this._pushEffect({
          kind: "rocket",
          x: (room ? room.x + room.w / 2 : target.x) + 12,
          y: ROOM_FLOOR - 22,
          sha,
          color: roomColor,
          dur: 1.7,
        });
        return;
      }
      case "review_finding": {
        const point = target;
        this.lastBug = { x: point.x, y: point.y };
        this._pushEffect({
          kind: "bug",
          x: point.x,
          y: point.y - 26,
          color: severity === "danger" ? C.danger : C.warning,
          dur: 1.8,
        });
        if (severity === "danger") this._addShake(2.2, 0.5);
        return;
      }
      case "remediation": {
        const from = this._agentPoint(event.agent) || this._roomPoint(family);
        const to = this.lastBug || this._roomPoint(family);
        this._pushEffect({
          kind: "zap",
          x1: from.x,
          y1: from.y,
          x2: to.x,
          y2: to.y,
          color: C.info,
          dur: 0.8,
        });
        this._pushEffect({ kind: "ring", x: to.x, y: to.y, color: C.success, dur: 0.7 });
        return;
      }
      case "checkpoint":
        this._pushEffect({
          kind: "flag",
          x: (room ? room.x + room.w / 2 : target.x) - 8,
          y: ROOM_FLOOR - 8,
          label: "SAVE",
          color: C.success,
          dur: 1.4,
        });
        return;
      case "phase_transition": {
        const phaseRoom = ROOM_BY_FAMILY[family];
        this._pushEffect({
          kind: "banner",
          text: String(event.title || `${family} TRANSITION`).toUpperCase(),
          sub: event.detail ? String(event.detail) : "",
          color: roomColor,
          dur: 1.8,
          y: 120,
        });
        if (phaseRoom) {
          this._pushEffect({
            kind: "ring",
            x: phaseRoom.x + phaseRoom.w / 2,
            y: phaseRoom.floor - 30,
            color: roomColor,
            dur: 0.9,
          });
        }
        return;
      }
      case "lifecycle_decision":
        this._pushEffect({ kind: "gateFlash", color: C.warning, label: "ADVISORY", dur: 1.2 });
        return;
      case "gate_health":
        this._pushEffect({ kind: "gateFlash", color: C.danger, label: "GATE FAULT", dur: 1.3 });
        this._addShake(2.4, 0.6);
        return;
      case "journal_emit_skipped":
        this._pushEffect({ kind: "glitch", dur: 0.7 });
        return;
      default:
        break;
    }

    if (icon === "dispatch") {
      this._pushEffect({
        kind: "beam",
        x1: tower.x, y1: tower.y, x2: target.x, y2: target.y,
        color, dur: 0.85,
      });
    } else if (icon === "handoff") {
      this._pushEffect({
        kind: "envelope",
        x1: target.x, y1: target.y, x2: review.x, y2: review.y,
        color, dur: 1.2, label: "HANDOFF",
      });
    } else if (icon === "commit") {
      this._pushEffect({
        kind: "rocket",
        x: target.x + 10,
        y: ROOM_FLOOR - 22,
        sha: this._shaForEvent(event),
        color: roomColor,
        dur: 1.6,
      });
    } else if (icon === "bug") {
      this.lastBug = { x: target.x, y: target.y };
      this._pushEffect({ kind: "bug", x: target.x, y: target.y - 24, color, dur: 1.7 });
    } else if (icon === "alert") {
      this._pushEffect({ kind: "siren", dur: 1.6, color: C.danger, label: String(event.title || "ALERT").toUpperCase() });
    } else if (icon === "save") {
      this._pushEffect({ kind: "flag", x: target.x, y: ROOM_FLOOR - 8, label: "SAVE", color: C.success, dur: 1.2 });
    } else if (icon === "compact") {
      this._pushEffect({ kind: "wipe", dur: 0.9, color: "#22d3ee", label: "COMPACT" });
    } else if (icon === "pr") {
      this._pushEffect(this._makeConfetti(40));
    } else if (icon === "gate") {
      this._pushEffect({ kind: "gateFlash", color, label: "GATE", dur: 1.0 });
    } else if (icon === "review") {
      this._pushEffect({
        kind: "envelope",
        x1: target.x, y1: target.y, x2: review.x, y2: review.y,
        color, dur: 1.2, label: "REVIEW",
      });
    } else if (icon === "phase") {
      this._pushEffect({
        kind: "banner",
        text: String(event.title || "PHASE").toUpperCase(),
        sub: "",
        color,
        dur: 1.7,
        y: 120,
      });
    } else if (icon === "teachback") {
      this._pushEffect({ kind: "check", x: target.x, y: target.y, color: C.success, dur: 0.9 });
    } else if (icon === "session") {
      this._pushEffect({ kind: "ring", x: target.x, y: target.y, color, dur: 0.7 });
    } else if (icon === "task") {
      this._pushEffect({ kind: "ring", x: target.x, y: target.y, color, dur: 0.55 });
    }
  }

  _shaForEvent(event) {
    const commits = this.model && Array.isArray(this.model.commits) ? this.model.commits : [];
    for (const commit of commits) {
      if (commit && Number(commit.seq) === Number(event.seq) && commit.sha) {
        return String(commit.sha).slice(0, 7);
      }
    }
    const match = String(event.title || "").match(/commit\s+([0-9a-f]{5,40})/i);
    return match ? match[1].slice(0, 7) : "";
  }

  _pushEffect(effect) {
    if (!effect || this.destroyed) return;
    if (this.reducedMotion && effect.kind !== "stamp" && effect.kind !== "banner"
      && effect.kind !== "gateFlash" && effect.kind !== "flag" && effect.kind !== "check"
      && effect.kind !== "ring" && effect.kind !== "siren") {
      return;
    }
    effect.t = 0;
    if (!Number.isFinite(effect.dur)) effect.dur = 1;
    if (effect.kind === "banner") {
      this.effects = this.effects.filter((item) => item.kind !== "banner");
    } else if (effect.kind === "siren") {
      this.effects = this.effects.filter((item) => item.kind !== "siren");
    }
    this.effects.push(effect);
    if (this.effects.length > 26) this.effects.splice(0, this.effects.length - 26);
  }

  _addShake(magnitude, duration) {
    if (this.reducedMotion) return;
    this.shake = { t: duration, dur: duration, mag: magnitude };
  }

  _makeConfetti(count) {
    const particles = [];
    const palette = ["#4ade80", "#fbbf24", "#60a5fa", "#fb7185", "#a78bfa", "#22d3ee", "#a3e635"];
    for (let i = 0; i < count; i += 1) {
      particles.push({
        x: 120 + Math.random() * 720,
        y: -10 - Math.random() * 80,
        vx: (Math.random() - 0.5) * 90,
        vy: 30 + Math.random() * 70,
        w: 2 + Math.round(Math.random() * 3),
        h: 2 + Math.round(Math.random() * 3),
        color: palette[Math.floor(Math.random() * palette.length)],
      });
    }
    return { kind: "confetti", particles, layer: 1, dur: 2.8 };
  }

  /* ---- rendering -------------------------------------------------------- */

  _render() {
    if (!this.ctx || !this.canvas || this.destroyed) return;
    if (!this.canvas.width || !this.canvas.height) return;
    const ctx = this.ctx;
    const scale = this.fit * this.dpr;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    if (this.bgCanvas) {
      ctx.drawImage(this.bgCanvas, 0, 0);
    } else {
      ctx.fillStyle = C.bg;
      ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    }

    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    disableSmoothing(ctx);

    let shakeX = 0;
    let shakeY = 0;
    if (this.shake.t > 0 && !this.reducedMotion) {
      const p = this.shake.t / Math.max(0.001, this.shake.dur);
      const amp = this.shake.mag * p;
      shakeX = Math.sin(this.time * 57) * amp;
      shakeY = Math.cos(this.time * 49) * amp * 0.7;
    }
    ctx.translate(this.offsetX + shakeX, this.offsetY + shakeY);

    this._drawAmbient(ctx);
    this._drawRoomStatus(ctx);
    this._drawPlazaStatus(ctx);
    this._drawMascot(ctx);
    this._drawAgents(ctx);
    this._drawEffects(ctx, 0);
    this._drawBubbles(ctx);
    this._drawEffects(ctx, 1);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  _text(ctx, value, x, y, size, color, align, weight) {
    ctx.font = `${weight || "normal"} ${size || 9}px ${FONT_STACK}`;
    ctx.fillStyle = color || C.muted;
    ctx.textAlign = align || "left";
    ctx.textBaseline = "middle";
    ctx.fillText(String(value), x, y);
  }

  _panel(ctx, x, y, w, h, fill, border, borderColor) {
    ctx.fillStyle = fill || "rgba(6, 10, 20, 0.9)";
    ctx.fillRect(Math.round(x), Math.round(y), Math.max(1, Math.round(w)), Math.max(1, Math.round(h)));
    if (border) {
      ctx.fillStyle = borderColor || C.lineBright;
      const xx = Math.round(x);
      const yy = Math.round(y);
      const ww = Math.max(1, Math.round(w));
      const hh = Math.max(1, Math.round(h));
      ctx.fillRect(xx, yy, ww, Math.max(1, border));
      ctx.fillRect(xx, yy + hh - Math.max(1, border), ww, Math.max(1, border));
      ctx.fillRect(xx, yy, Math.max(1, border), hh);
      ctx.fillRect(xx + ww - Math.max(1, border), yy, Math.max(1, border), hh);
    }
  }

  _drawAmbient(ctx) {
    const t = this.time;
    /* twinkling stars */
    for (const star of this.twinkles) {
      let alpha = 0.7;
      if (!this.reducedMotion) {
        alpha = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(t * star.speed + star.phase));
      }
      ctx.fillStyle = rgba(star.color, alpha);
      ctx.fillRect(star.x, star.y, star.size, star.size);
    }

    /* radar sweep on the tower dish */
    const sweep = t * 0.9;
    const blipX = Math.round(TOWER_X + Math.cos(sweep) * 16);
    const blipY = Math.round(100 + Math.sin(sweep) * 9);
    ctx.fillStyle = rgba("#22d3ee", 0.8);
    ctx.fillRect(blipX, blipY, 2, 2);

    /* radar field sweep behind the tower */
    const fieldSweep = t * 0.55;
    for (let i = 1; i <= 11; i += 1) {
      const radius = i * 15;
      const angle = fieldSweep;
      ctx.fillStyle = rgba("#22d3ee", Math.max(0.02, 0.2 - i * 0.015));
      ctx.fillRect(
        Math.round(TOWER_X + Math.cos(angle) * radius) - 1,
        Math.round(150 + Math.sin(angle) * radius * 0.62) - 1,
        3, 3,
      );
    }

    /* antenna farm beacons */
    for (const light of this.mastLights) {
      const on = this.reducedMotion ? true : Math.sin(t * 2.2 + light.x * 0.3) > 0;
      ctx.fillStyle = on ? rgba(C.danger, 0.9) : rgba(C.danger, 0.18);
      ctx.fillRect(light.x - 1, light.y, 2, 2);
      if (on) {
        ctx.fillStyle = rgba(C.danger, 0.12);
        ctx.fillRect(light.x - 4, light.y - 2, 8, 6);
      }
    }

    /* console + runway lights of the camera deck */
    for (const led of this.consoleLeds) {
      const on = this.reducedMotion ? true : Math.sin(t * 2.6 + led.phase) > -0.2;
      ctx.fillStyle = rgba(led.color, on ? 0.95 : 0.18);
      ctx.fillRect(led.x, led.y, 3, 2);
    }
    for (const glow of this.consoleGlows) {
      const pulse = this.reducedMotion ? 0.5 : 0.5 + 0.5 * Math.sin(t * 1.6 + glow.phase);
      ctx.fillStyle = rgba(glow.color, 0.1 + pulse * 0.18);
      ctx.fillRect(glow.x, glow.y - 1, glow.w, 2);
      ctx.fillStyle = rgba(glow.color, 0.05 + pulse * 0.08);
      ctx.fillRect(glow.x + 4, glow.y - 4, glow.w - 8, 3);
    }
    for (const radar of this.consoleRadars) {
      const angle = (this.reducedMotion ? 0.6 : t * 1.4) + radar.phase;
      for (let i = 1; i <= 4; i += 1) {
        ctx.fillStyle = rgba(C.warning, 0.5 - i * 0.08);
        ctx.fillRect(
          Math.round(radar.cx + Math.cos(angle) * (radar.r * i) / 4),
          Math.round(radar.cy + Math.sin(angle) * (radar.r * i) / 4),
          2, 2,
        );
      }
      ctx.fillStyle = "rgba(251, 191, 36, 0.25)";
      ctx.fillRect(radar.cx - radar.r, radar.cy, radar.r * 2, 1);
      ctx.fillRect(radar.cx, radar.cy - radar.r, 1, radar.r * 2);
    }
    if (this.consoleTally) {
      const blink = this.reducedMotion ? true : Math.floor(t * 2) % 2 === 0;
      ctx.fillStyle = blink ? C.danger : "rgba(251, 113, 133, 0.2)";
      ctx.fillRect(this.consoleTally.x, this.consoleTally.y, 3, 2);
      if (blink) {
        ctx.fillStyle = "rgba(251, 113, 133, 0.16)";
        ctx.fillRect(this.consoleTally.x - 3, this.consoleTally.y - 2, 9, 6);
      }
    }
    for (let i = 0; i < this.runwayLights.length; i += 1) {
      const light = this.runwayLights[i];
      const on = this.reducedMotion ? true : Math.sin(t * 3 + i * 1.4) > 0.4;
      ctx.fillStyle = rgba("#fbbf24", on ? 0.8 : 0.12);
      ctx.fillRect(light.x, light.y, 2, 1);
    }

    /* an occasional shooting star in the deep sky */
    if (!this.reducedMotion) {
      const cycle = t % 14;
      if (cycle < 0.9) {
        const p = cycle / 0.9;
        const sx = -this.offsetX + this.viewW * (0.72 - p * 0.18);
        const sy = -this.offsetY + 70 + p * 60;
        for (let i = 0; i < 6; i += 1) {
          ctx.fillStyle = rgba("#e8eefc", 0.6 * (1 - p) * (1 - i / 7));
          ctx.fillRect(Math.round(sx - i * 7), Math.round(sy - i * 4), 3, 2);
        }
      }
    }

    /* beacon blinking */
    const beaconOn = Math.floor(t * 1.6) % 2 === 0;
    const alert = this.effects.some((effect) => effect.kind === "siren");
    ctx.fillStyle = alert ? C.danger : beaconOn ? "#fb7185" : "#4a2231";
    ctx.fillRect(TOWER_X - 4, 43, 8, 5);
    ctx.fillStyle = alert ? rgba(C.danger, 0.28) : rgba("#fb7185", 0.12);
    ctx.fillRect(TOWER_X - 18, 40, 36, 11);

    /* conveyor crates */
    const codeRoom = ROOM_BY_FAMILY.CODE;
    if (codeRoom) {
      const beltX = codeRoom.x + 12;
      const beltW = codeRoom.w - 24;
      for (let i = 0; i < 3; i += 1) {
        const travel = (t * 22 + i * 66) % Math.max(1, beltW);
        const cx = beltX + travel;
        this._drawCrate(ctx, cx, ROOM_FLOOR - 12, i);
      }
    }

    /* monitor flicker in the workshop */
    const monitorColors = ["#fbbf24", "#60a5fa", "#4ade80"];
    for (let i = 0; i < 3; i += 1) {
      const flicker = Math.sin(t * (7 + i * 2) + i * 2.1) > 0.35;
      if (!flicker) continue;
      const mx = codeRoom ? codeRoom.x + 79 + i * 40 : 0;
      ctx.fillStyle = rgba(monitorColors[i], 0.8);
      ctx.fillRect(mx + ((this.frame + i) % 14), 234 + (i % 2) * 2, 2, 1);
    }

    /* gate lamps + rotating bars */
    this._drawGates(ctx, t);
  }

  _drawCrate(ctx, x, y, index) {
    const xx = Math.round(x);
    const yy = Math.round(y);
    ctx.fillStyle = index % 2 === 0 ? "#33456b" : "#3d5378";
    ctx.fillRect(xx, yy, 11, 9);
    ctx.fillStyle = "#fbbf24";
    ctx.fillRect(xx, yy, 11, 2);
    ctx.fillStyle = "rgba(7, 11, 22, 0.85)";
    ctx.fillRect(xx, yy + 4, 11, 1);
    ctx.fillStyle = "rgba(163, 230, 53, 0.8)";
    ctx.fillRect(xx + 2, yy + 6, 3, 2);
    ctx.fillStyle = "rgba(251, 191, 36, 0.35)";
    ctx.fillRect(xx + 1, yy + 9, 9, 1);
  }

  _drawGates(ctx, t) {
    const x = GATES.x;
    const floor = GATES.floor;
    const gateCount = 2;
    for (let i = 0; i < gateCount; i += 1) {
      const bx = x + 20 + i * 64;
      const step = Math.floor(t * 3 + i) % 4;
      ctx.fillStyle = "rgba(143, 160, 196, 0.75)";
      const barW = [16, 8, 2, 8][step];
      ctx.fillRect(bx + 22 - Math.round(barW / 2), floor - 22, barW, 2);
      ctx.fillStyle = "rgba(143, 160, 196, 0.35)";
      ctx.fillRect(bx + 22 - Math.round(barW / 2), floor - 19, Math.max(2, barW - 4), 1);
    }
  }

  _drawRoomStatus(ctx) {
    const completed = this.phaseState.completed;
    const current = this.phaseState.current;
    const t = this.time;
    for (const room of ROOM_DEFS) {
      const isCurrent = current === room.family;
      const isDone = completed.has(room.family);
      const crew = this.views.filter((view) => view.present && view.zone === "room" && view.family === room.family);
      if (isCurrent) {
        const pulse = this.reducedMotion ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.6);
        ctx.fillStyle = rgba(room.color, 0.05 + pulse * 0.04);
        ctx.fillRect(room.x + 3, room.top + 2, room.w - 6, room.floor - room.top - 2);
        ctx.fillStyle = rgba(room.color, 0.35 + pulse * 0.2);
        ctx.fillRect(room.x, room.top - 5, room.w, 2);
        for (let i = 0; i < 3; i += 1) {
          const on = Math.floor(t * 3 + i) % 3 === 0;
          ctx.fillStyle = on ? room.color : rgba(room.color, 0.25);
          ctx.fillRect(room.x + room.w - 16 - i * 5, room.top + 6, 3, 3);
        }
      } else if (isDone) {
        ctx.fillStyle = "rgba(74, 222, 128, 0.75)";
        ctx.fillRect(room.x + room.w - 16, room.top + 6, 3, 2);
        ctx.fillRect(room.x + room.w - 14, room.top + 8, 2, 2);
        ctx.fillRect(room.x + room.w - 17, room.top + 9, 2, 2);
      }
      /* crew pips on the plaque */
      const pips = Math.min(4, crew.length);
      for (let i = 0; i < pips; i += 1) {
        ctx.fillStyle = room.color;
        ctx.fillRect(room.x + room.w / 2 + 36 + i * 4, room.floor + 9, 2, 2);
      }
    }

    /* phase banner in the sky when nothing else is showing */
    if (!current && !completed.size && this.views.length) {
      this._text(ctx, "AWAITING PHASE", LOGICAL_W / 2, 160, 11, rgba(C.muted, 0.8), "center");
    }
  }

  _drawPlazaStatus(ctx) {
    const t = this.time;
    const wrapup = this._wrapup();
    const wrapVisible = this._wrapupVisible();
    const gates = (this.model && this.model.gates) || {};
    const allowed = Number(gates.allowed) || 0;
    const advisory = Number(gates.advisory) || 0;
    const failures = Array.isArray(gates.health_failures) ? gates.health_failures.length : 0;
    const reviews = this.model && Array.isArray(this.model.reviews) ? this.model.reviews.length : 0;
    const nextPhase = PHASE_ORDER.find((family) => !this.phaseState.completed.has(family)) || "WRAP-UP";

    /* review deck screen */
    const review = REVIEW_DECK;
    const reviewCenter = review.x + review.w / 2;
    const wrapState = wrapup ? String(wrapup.pr_state || "").toUpperCase() : "";
    const stateColor = wrapup
      ? wrapState === "MERGED" ? C.success : wrapState === "OPEN" ? C.warning : C.muted
      : C.info;
    this._text(ctx, wrapVisible ? "SHIP SCREEN" : "REVIEW", reviewCenter, review.top + 12, 9, rgba(C.muted, 0.9), "center", "bold");
    if (wrapVisible && wrapup) {
      this._text(
        ctx,
        wrapup.pr_number !== undefined && wrapup.pr_number !== null ? `PR #${wrapup.pr_number}` : "PR",
        reviewCenter,
        review.top + 24,
        9,
        C.muted,
        "center",
      );
      this._text(ctx, wrapState || "SHIPPED", reviewCenter, review.top + 34, 11, stateColor, "center", "bold");
    } else {
      this._text(ctx, `${reviews} PR`, reviewCenter, review.top + 24, 10, stateColor, "center");
      this._text(ctx, `${this.views.filter((view) => view.present && view.zone === "review").length} REVIEWERS`, reviewCenter, review.top + 34, 8, C.dim, "center");
    }
    if (!this.reducedMotion) {
      const scan = Math.floor((t * 30) % 30);
      ctx.fillStyle = "rgba(96, 165, 250, 0.14)";
      ctx.fillRect(review.x + 97, review.top + 5 + scan, 90, 1);
    }

    /* gate display counters */
    const gateX = GATES.x + GATES.w / 2;
    this._text(ctx, `ALLOW ${allowed}`, GATES.x + 14, GATES.top + 15, 9, C.success, "left");
    this._text(ctx, `ADV ${advisory}`, GATES.x + 14, GATES.top + 22, 9, advisory > 0 ? C.warning : C.dim, "left");
    const lampColor = failures > 0 ? C.danger : advisory > 0 ? C.warning : C.success;
    const lampOn = this.reducedMotion ? true : Math.floor(t * 2) % 2 === 0;
    ctx.fillStyle = lampOn ? lampColor : rgba(lampColor, 0.3);
    ctx.fillRect(gateX + 34, GATES.top + 6, 4, 4);
    if (failures > 0) {
      this._text(ctx, `${failures} FAULT`, GATES.x + 128, GATES.top + 22, 9, C.danger, "right");
    }

    /* wrap-up board */
    const board = PR_BOARD;
    let boardValue = "OPEN RUN";
    let boardColor = C.dim;
    if (wrapup) {
      const state = String(wrapup.pr_state || "PR").toUpperCase();
      boardValue = state;
      boardColor = state === "MERGED" ? C.success : state === "OPEN" ? C.warning : C.muted;
    } else if (nextPhase === "WRAP-UP") {
      boardValue = "WRAP-UP";
      boardColor = C.info;
    }
    this._text(ctx, wrapup && wrapup.pr_number ? `PR #${wrapup.pr_number}` : "PR —", board.x + board.w / 2, board.top + 26, 9, C.muted, "center");
    this._text(ctx, boardValue, board.x + board.w / 2, board.top + 40, 13, boardColor, "center", "bold");

    /* persistent shipped stamp once the run is over */
    if (wrapVisible && wrapup) {
      const state = String(wrapup.pr_state || "").toUpperCase() || "SHIPPED";
      const color = state === "MERGED" ? C.success : state === "OPEN" ? C.warning : C.muted;
      const stampW = 178;
      const stampX = LOGICAL_W - stampW - 10;
      this._panel(ctx, stampX, 8, stampW, 38, "rgba(6, 10, 20, 0.88)", 2, color);
      this._text(ctx, "SHIPPED", stampX + 10, 19, 15, color, "left", "bold");
      this._text(
        ctx,
        wrapup.pr_number !== undefined && wrapup.pr_number !== null ? `PR #${wrapup.pr_number} ${state}` : state,
        stampX + 10,
        34,
        10,
        C.muted,
        "left",
      );
      /* tiny pixel rocket in the stamp */
      ctx.fillStyle = color;
      ctx.fillRect(stampX + stampW - 30, 15, 3, 10);
      ctx.fillRect(stampX + stampW - 33, 25, 3, 4);
      ctx.fillRect(stampX + stampW - 27, 25, 3, 4);
      this._outline(ctx, stampX + stampW - 35, 24, 14, 12, 1, rgba(color, 0.5));
    }

    /* lounge label accent when the crew is resting */
    const resting = this.views.filter((view) => view.present && view.zone === "lounge").length;
    if (resting > 0) {
      this._text(ctx, `STANDBY · ${resting}`, LOUNGE.x + 8, GROUND_FLOOR + 8, 9, rgba(C.dim, 0.9), "left");
    }
  }

  _drawMascot(ctx) {
    const mascot = this.mascot;
    const bob = this.reducedMotion ? 0 : Math.sin(this.time * 2.2 + mascot.seed) * 1.6;
    const scale = 1.32;
    const drawY = mascot.y + bob;
    mascot.hover = this.hover && this.hover.kind === "mascot";
    drawMascot(ctx, mascot.x, drawY, Math.floor(this.time * 8), scale, {
      hover: mascot.hover,
      alpha: 1,
    });
    mascot.bob = bob;
    mascot.drawX = mascot.x;
    mascot.drawY = drawY;
    if (this.time < mascot.sayUntil && mascot.sayText) {
      this._bubble(ctx, mascot.sayText, mascot.x, drawY - 18 * scale - 2, C.info, 1);
    }
  }

  _drawAgents(ctx) {
    const visible = this.views.filter((view) => view.present && view.agent);
    visible.sort((a, b) => a.agent.y - b.agent.y || a.agent.x - b.agent.x);
    const t = this.time;
    for (const view of visible) {
      const agent = view.agent;
      const walking = Boolean(agent.walking) && !this.reducedMotion;
      const bob = this.reducedMotion || agent.walking ? 0 : Math.sin(t * 2.4 + agent.seed) * 1.2;
      const frame = Math.floor(t * 10) + agent.frameSeed;
      const box = drawAgent(
        ctx,
        view.role,
        view.palette,
        agent.x,
        agent.y,
        frame,
        view.scale,
        { walking, bob, alpha: agent.alpha, flip: agent.flip },
      );
      view.box = box;
      view.drawX = agent.x;
      view.drawY = agent.y;
      if (this.hover && this.hover.kind === "agent" && this.hover.key === view.name) {
        ctx.fillStyle = "rgba(232, 238, 252, 0.55)";
        this._outline(ctx, box.x - 2, box.y - 2, box.w + 4, box.h + 4, 1);
      }
      if (this.selectedAgent && this.selectedAgent === view.name) {
        this._drawSelection(ctx, box, view.name);
      }
    }
  }

  _drawSelection(ctx, box, name) {
    const pulse = this.reducedMotion ? 0 : Math.sin(this.time * 4) * 2;
    const pad = 3 + (this.reducedMotion ? 0 : pulse);
    const x = Math.round(box.x - pad);
    const y = Math.round(box.y - pad);
    const w = Math.round(box.w + pad * 2);
    const h = Math.round(box.h + pad * 2);
    this._outline(ctx, x, y, w, h, 2, ACCENT);
    /* corner accents */
    ctx.fillStyle = ACCENT;
    ctx.fillRect(x - 2, y - 2, 6, 2);
    ctx.fillRect(x + w - 4, y - 2, 6, 2);
    ctx.fillRect(x - 2, y + h, 6, 2);
    ctx.fillRect(x + w - 4, y + h, 6, 2);
    const label = name.toUpperCase().slice(0, 14);
    const labelW = Math.max(34, label.length * 6 + 8);
    this._panel(ctx, box.x + box.w / 2 - labelW / 2, box.y + box.h + 4, labelW, 12, "rgba(6, 10, 20, 0.92)", 1, ACCENT);
    this._text(ctx, label, box.x + box.w / 2, box.y + box.h + 10, 9, ACCENT, "center", "bold");
  }

  _bubble(ctx, value, x, y, color, maxLines) {
    const text = String(value || "").trim().replace(/\s+/g, " ");
    if (!text) return;
    const chars = 20;
    const lines = [];
    let rest = text;
    while (rest.length && lines.length < (maxLines || 2)) {
      if (rest.length <= chars) {
        lines.push(rest);
        rest = "";
      } else {
        let cut = rest.lastIndexOf(" ", chars);
        if (cut < 8) cut = chars;
        lines.push(rest.slice(0, cut));
        rest = rest.slice(cut).trim();
      }
    }
    if (rest) lines[lines.length - 1] = `${lines[lines.length - 1].slice(0, chars - 1)}…`;
    const width = Math.max(...lines.map((line) => line.length)) * 5.6 + 10;
    const height = lines.length * 11 + 5;
    let bx = clamp(x - width / 2, -this.offsetX + 4, -this.offsetX + this.viewW - width - 4);
    let by = y - height;
    if (by < -this.offsetY + 4) by = y + 8;
    this._panel(ctx, bx, by, width, height, "rgba(6, 10, 20, 0.92)", 1, color);
    /* tail */
    ctx.fillStyle = "rgba(6, 10, 20, 0.92)";
    ctx.fillRect(Math.round(x) - 2, Math.round(by + height), 4, 4);
    ctx.fillStyle = color;
    ctx.fillRect(Math.round(x) - 2, Math.round(by + height), 4, 1);
    for (let i = 0; i < lines.length; i += 1) {
      this._text(ctx, lines[i], bx + 5, by + 7 + i * 11, 9, C.text, "left");
    }
  }

  _drawBubbles(ctx) {
    const cursor = this.cursor;
    const candidates = this.views.filter((view) => {
      if (!view.present || !view.latest || view.latestIdx < 0) return false;
      if (view.role === "lead" && view.zone !== "tower") return false;
      if (view.latestIdx >= cursor) return false;
      return cursor - view.latestIdx <= 7;
    });
    candidates.sort((a, b) => b.latestIdx - a.latestIdx);
    const zoneOf = (view) => `${view.zone}:${view.family}`;
    const shown = [];
    for (const view of candidates) {
      if (!view.agent || !view.latest) continue;
      if (shown.length >= 2) break;
      const first = shown[0];
      if (first) {
        /* second bubble only when it will not collide with the first */
        if (zoneOf(view) === zoneOf(first.view)) continue;
        if (Math.abs(view.agent.x - first.view.agent.x) < 150) continue;
      }
      const lift = shown.length === 1 ? 6 : 0;
      shown.push({ view, lift });
    }
    for (const entry of shown) {
      const view = entry.view;
      const agent = view.agent;
      const y = agent.y - view.scale * SPRITE_H - 6 - entry.lift;
      const color = severityColor(view.latest.severity, view.zone === "room" && view.family ? ROOM_BY_FAMILY[view.family].color : C.muted);
      this._bubble(ctx, view.latest.title || view.latest.type || "", agent.x, y, color, 2);
    }
  }

  _outline(ctx, x, y, w, h, thickness, color) {
    const t = Math.max(1, Math.round(thickness || 1));
    const xx = Math.round(x);
    const yy = Math.round(y);
    const ww = Math.max(1, Math.round(w));
    const hh = Math.max(1, Math.round(h));
    ctx.fillStyle = color || C.metal;
    ctx.fillRect(xx, yy, ww, t);
    ctx.fillRect(xx, yy + hh - t, ww, t);
    ctx.fillRect(xx, yy, t, hh);
    ctx.fillRect(xx + ww - t, yy, t, hh);
  }

  _pixelLine(ctx, x1, y1, x2, y2, color, size, spacing) {
    const distance = Math.hypot(x2 - x1, y2 - y1);
    const step = spacing || 6;
    const count = Math.max(1, Math.floor(distance / step));
    const s = size || 3;
    ctx.fillStyle = color;
    for (let i = 0; i <= count; i += 1) {
      const p = i / count;
      const x = Math.round(x1 + (x2 - x1) * p);
      const y = Math.round(y1 + (y2 - y1) * p);
      ctx.fillRect(x - Math.floor(s / 2), y - Math.floor(s / 2), s, s);
    }
  }

  _drawEffects(ctx, layer) {
    for (const effect of this.effects) {
      if ((effect.layer || 0) !== layer) continue;
      const p = clamp(effect.t / Math.max(0.001, effect.dur), 0, 1);
      switch (effect.kind) {
        case "beam": this._drawBeam(ctx, effect, p); break;
        case "envelope": this._drawEnvelope(ctx, effect, p); break;
        case "rocket": this._drawRocket(ctx, effect, p); break;
        case "bug": this._drawBug(ctx, effect, p); break;
        case "zap": this._drawZap(ctx, effect, p); break;
        case "flag": this._drawFlag(ctx, effect, p); break;
        case "wipe": this._drawWipe(ctx, effect, p); break;
        case "confetti": this._drawConfetti(ctx, effect, p); break;
        case "stamp": this._drawStamp(ctx, effect, p); break;
        case "siren": this._drawSiren(ctx, effect, p); break;
        case "warp": this._drawWarp(ctx, effect, p); break;
        case "banner": this._drawBanner(ctx, effect, p); break;
        case "gateFlash": this._drawGateFlash(ctx, effect, p); break;
        case "ring": this._drawRing(ctx, effect, p); break;
        case "check": this._drawCheck(ctx, effect, p); break;
        case "glitch": this._drawGlitch(ctx, effect, p); break;
        default: break;
      }
    }
  }

  _drawBeam(ctx, effect, p) {
    const eased = easeOut(p);
    const headX = lerp(effect.x1, effect.x2, eased);
    const headY = lerp(effect.y1, effect.y2, eased);
    const color = effect.color || C.info;
    this._pixelLine(ctx, effect.x1, effect.y1, headX, headY, rgba(color, 0.85 * (1 - p * 0.4)), 3, 5);
    /* origin flash */
    const originSize = 4 + p * 14;
    this._outline(ctx, effect.x1 - originSize / 2, effect.y1 - originSize / 2, originSize, originSize, 1, rgba(color, 1 - p));
    /* incoming impact ring */
    if (eased > 0.88) {
      const ringSize = 6 + (p - 0.88) * 120;
      this._outline(ctx, effect.x2 - ringSize / 2, effect.y2 - ringSize / 2, ringSize, ringSize, 2, rgba(color, 1 - (p - 0.88) / 0.12));
      ctx.fillStyle = color;
      ctx.fillRect(Math.round(effect.x2) - 2, Math.round(effect.y2) - 2, 4, 4);
    }
    if (effect.label && p > 0.4) {
      this._text(ctx, effect.label, effect.x2 + 10, effect.y2 - 14, 9, rgba(color, 1 - p * 0.5), "left", "bold");
    }
  }

  _drawEnvelope(ctx, effect, p) {
    const x1 = effect.x1;
    const y1 = effect.y1;
    const x2 = effect.x2;
    const y2 = effect.y2;
    const cx = (x1 + x2) / 2;
    const cy = Math.min(y1, y2) - 46;
    const point = quadPoint(x1, y1, cx, cy, x2, y2, easeInOut(p));
    const color = effect.color || C.info;
    /* trail */
    for (let i = 1; i <= 4; i += 1) {
      const trail = quadPoint(x1, y1, cx, cy, x2, y2, clamp(easeInOut(p) - i * 0.035, 0, 1));
      ctx.fillStyle = rgba(color, 0.35 - i * 0.06);
      ctx.fillRect(Math.round(trail.x) - 2, Math.round(trail.y) - 1, 4, 2);
    }
    const ex = Math.round(point.x);
    const ey = Math.round(point.y);
    this._panel(ctx, ex - 6, ey - 4, 12, 8, "#e8eefc", 1, color);
    ctx.fillStyle = color;
    ctx.fillRect(ex - 6, ey - 4, 12, 1);
    ctx.fillRect(ex - 5, ey - 2, 1, 1);
    ctx.fillRect(ex + 4, ey - 2, 1, 1);
    ctx.fillRect(ex - 1, ey + 1, 2, 1);
    if (p > 0.9) {
      const burst = (p - 0.9) * 60;
      this._outline(ctx, x2 - burst, y2 - burst, burst * 2, burst * 2, 2, rgba(color, 1 - (p - 0.9) * 10));
    }
    if (effect.label && p < 0.75) {
      this._text(ctx, effect.label, ex + 9, ey - 8, 9, rgba(color, 0.95), "left", "bold");
    }
  }

  _drawRocket(ctx, effect, p) {
    const color = effect.color || C.warning;
    const travel = easeOut(p) * 138;
    const x = Math.round(effect.x + Math.sin(p * 9) * 3);
    const y = Math.round(effect.y - travel);
    /* flame trail */
    for (let i = 0; i < 6; i += 1) {
      const flicker = (Math.floor(this.time * 24) + i) % 3;
      ctx.fillStyle = i % 2 === 0 ? rgba(C.warning, 0.8 - i * 0.1) : rgba(C.danger, 0.65 - i * 0.08);
      ctx.fillRect(x - 1 + (flicker - 1), y + 16 + i * 7, 4 - Math.floor(i / 3), 6);
    }
    /* rocket body */
    ctx.fillStyle = "#e8eefc";
    ctx.fillRect(x - 3, y + 4, 7, 12);
    ctx.fillStyle = color;
    ctx.fillRect(x - 3, y + 8, 7, 3);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(x - 1, y + 6, 3, 3);
    ctx.fillStyle = C.metal;
    ctx.fillRect(x - 4, y + 12, 2, 5);
    ctx.fillRect(x + 3, y + 12, 2, 5);
    ctx.fillStyle = C.danger;
    ctx.fillRect(x - 2, y + 1, 5, 3);
    if (effect.sha) {
      const label = effect.sha;
      const w = label.length * 6 + 8;
      this._panel(ctx, x + 9, y + 4, w, 12, "rgba(6, 10, 20, 0.9)", 1, color);
      this._text(ctx, label, x + 13, y + 10, 9, color, "left", "bold");
    }
    if (p > 0.84) {
      const burst = (p - 0.84) * 80;
      const cx = effect.x;
      const cy = effect.y - 140;
      for (let i = 0; i < 8; i += 1) {
        const angle = (i / 8) * Math.PI * 2;
        ctx.fillStyle = rgba(color, 1 - (p - 0.84) / 0.16);
        ctx.fillRect(
          Math.round(cx + Math.cos(angle) * burst) - 1,
          Math.round(cy + Math.sin(angle) * burst) - 1,
          3, 3,
        );
      }
    }
  }

  _drawBug(ctx, effect, p) {
    const color = effect.color || C.danger;
    const x = effect.x + Math.sin(p * 18) * 18;
    const y = effect.y + Math.sin(p * 9) * 7 - 8;
    const bob = Math.round(Math.sin(this.time * 16) * 1);
    ctx.fillStyle = color;
    ctx.fillRect(Math.round(x) - 4, Math.round(y) + bob, 8, 5);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(Math.round(x) - 2, Math.round(y) + 1 + bob, 1, 1);
    ctx.fillRect(Math.round(x) + 2, Math.round(y) + 1 + bob, 1, 1);
    /* legs + antennae */
    ctx.fillStyle = color;
    for (let i = 0; i < 3; i += 1) {
      const wiggle = Math.sin(this.time * 20 + i) > 0 ? 1 : -1;
      ctx.fillRect(Math.round(x) - 5, Math.round(y) + 1 + i * 2 + bob, 2, 1);
      ctx.fillRect(Math.round(x) + 4, Math.round(y) + 1 + i * 2 + bob, 2 + wiggle, 1);
    }
    ctx.fillRect(Math.round(x) - 1, Math.round(y) - 2 + bob, 1, 2);
    ctx.fillRect(Math.round(x) + 1, Math.round(y) - 2 + bob, 1, 2);
    /* warning blink */
    if (Math.floor(this.time * 6) % 2 === 0) {
      this._panel(ctx, Math.round(x) - 2, Math.round(y) - 15, 6, 9, "rgba(6, 10, 20, 0.9)", 1, color);
      this._text(ctx, "!", Math.round(x) + 1, Math.round(y) - 10, 9, color, "center", "bold");
    }
    if (p > 0.72) {
      const ring = (p - 0.72) * 100;
      this._outline(ctx, x - ring, y - ring + 2, ring * 2, ring * 2, 1, rgba(color, 1 - (p - 0.72) / 0.28));
    }
  }

  _drawZap(ctx, effect, p) {
    const color = effect.color || C.info;
    const segments = 7;
    let prevX = effect.x1;
    let prevY = effect.y1;
    for (let i = 1; i <= segments; i += 1) {
      const t = i / segments;
      const nx = lerp(effect.x1, effect.x2, t) + (i === segments ? 0 : Math.sin(i * 7 + p * 22) * (1 - p) * 14);
      const ny = lerp(effect.y1, effect.y2, t) + (i === segments ? 0 : Math.cos(i * 5 + p * 17) * (1 - p) * 10);
      this._pixelLine(ctx, prevX, prevY, nx, ny, i % 2 === 0 ? "#e8eefc" : color, 3, 4);
      prevX = nx;
      prevY = ny;
    }
    if (p < 0.7) {
      ctx.fillStyle = rgba(color, 0.14 * (1 - p));
      ctx.fillRect(-this.offsetX, -this.offsetY, this.viewW, this.viewH);
    }
    if (p > 0.7) {
      const ring = (p - 0.7) * 110;
      this._outline(ctx, effect.x2 - ring, effect.y2 - ring, ring * 2, ring * 2, 2, rgba(C.success, 1 - (p - 0.7) / 0.3));
    }
  }

  _drawFlag(ctx, effect, p) {
    const color = effect.color || C.success;
    const pop = easeOutBack(clamp(p * 1.8, 0, 1));
    const height = Math.round(42 * pop);
    const x = Math.round(effect.x);
    const baseY = Math.round(effect.y);
    ctx.fillStyle = C.metal;
    ctx.fillRect(x, baseY - height, 2, height);
    ctx.fillStyle = color;
    ctx.fillRect(x + 2, baseY - height, 16, 9);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(x + 2, baseY - height + 4, 16, 1);
    if (Math.floor(this.time * 8) % 2 === 0) {
      ctx.fillStyle = rgba("#ffffff", 0.2);
      ctx.fillRect(x + 2, baseY - height, 16, 1);
    }
    this._text(ctx, effect.label || "SAVE", x + 24, baseY - height + 5, 9, color, "left", "bold");
    this._outline(ctx, x - 2, baseY - 2, 6, 3, 1, color);
  }

  _drawWipe(ctx, effect, p) {
    const color = effect.color || "#22d3ee";
    const top = -this.offsetY;
    const height = this.viewH;
    const bandY = top + p * height;
    const bandHeight = 44;
    ctx.fillStyle = rgba(color, 0.16 * (1 - p * 0.4));
    ctx.fillRect(-this.offsetX, bandY, this.viewW, bandHeight);
    ctx.fillStyle = rgba(color, 0.75);
    ctx.fillRect(-this.offsetX, Math.round(bandY), this.viewW, 2);
    for (let i = 1; i < 5; i += 1) {
      ctx.fillStyle = rgba(color, 0.22);
      ctx.fillRect(-this.offsetX, Math.round(bandY + i * 8), this.viewW, 1);
    }
    if (effect.label) {
      this._text(ctx, effect.label, LOGICAL_W / 2, bandY + 20, 12, C.text, "center", "bold");
    }
  }

  _drawConfetti(ctx, effect, p) {
    const fade = p > 0.8 ? 1 - (p - 0.8) / 0.2 : 1;
    for (const particle of effect.particles || []) {
      ctx.fillStyle = rgba(particle.color, fade);
      ctx.fillRect(
        Math.round(particle.x),
        Math.round(particle.y),
        particle.w,
        particle.h,
      );
    }
  }

  _drawStamp(ctx, effect, p) {
    const pop = easeOutBack(clamp(p * 3, 0, 1));
    const alpha = p > 0.85 ? 1 - (p - 0.85) / 0.15 : 1;
    const width = 280 * pop;
    const height = 84 * pop;
    if (width < 4 || height < 4) return;
    const x = Math.round(effect.x - width / 2);
    const y = Math.round(effect.y - height / 2 + (this.reducedMotion ? 0 : Math.sin(this.time * 14) * 0.6));
    ctx.globalAlpha = Math.max(0, alpha);
    this._panel(ctx, x + 4, y + 4, width, height, "rgba(3, 6, 12, 0.7)", 0, null);
    this._panel(ctx, x, y, width, height, "rgba(5, 9, 20, 0.84)", 3, effect.color || C.success);
    this._outline(ctx, x + 6, y + 6, width - 12, height - 12, 1, rgba(effect.color || C.success, 0.6));
    this._text(ctx, effect.text || "SHIPPED", effect.x, y + height * 0.38, Math.max(8, Math.round(28 * pop)), effect.color || C.success, "center", "bold");
    this._text(ctx, effect.sub || "", effect.x, y + height * 0.72, Math.max(7, Math.round(12 * pop)), C.text, "center");
    ctx.globalAlpha = 1;
  }

  _drawSiren(ctx, effect, p) {
    const color = effect.color || C.danger;
    const intensity = this.reducedMotion ? 0.72 : 0.5 + 0.5 * Math.sin(this.time * 18);
    if (!this.reducedMotion) {
      ctx.fillStyle = rgba(color, 0.05 + intensity * 0.09);
      ctx.fillRect(-this.offsetX, -this.offsetY, this.viewW, this.viewH);
    }
    this._outline(ctx, Math.round(-this.offsetX + 2), Math.round(-this.offsetY + 2), Math.round(this.viewW - 4), Math.round(this.viewH - 4), 2, rgba(color, 0.35 + intensity * 0.4));
    const label = effect.label || "ALGEDONIC ALERT";
    const width = Math.min(360, label.length * 7 + 24);
    const x = LOGICAL_W / 2 - width / 2;
    const y = 96;
    this._panel(ctx, x, y, width, 22, "rgba(40, 12, 22, 0.92)", 2, color);
    this._text(ctx, label, LOGICAL_W / 2, y + 11, 11, C.text, "center", "bold");
    if (!this.reducedMotion) {
      this._text(ctx, "!! ALERT", LOGICAL_W / 2, y + 32, 9, rgba(color, 0.9), "center");
    }
    ctx.fillStyle = rgba(color, 0.5 + intensity * 0.5);
    ctx.fillRect(TOWER_X - 6, 42, 12, 7);
  }

  _drawWarp(ctx, effect, p) {
    const color = effect.color || "#a78bfa";
    ctx.fillStyle = rgba(color, 0.09 * (1 - p));
    ctx.fillRect(-this.offsetX, -this.offsetY, this.viewW, this.viewH);
    const strips = 14;
    for (let i = 0; i < strips; i += 1) {
      const y = -this.offsetY + (i / strips) * this.viewH;
      const shift = Math.sin(p * 6 + i * 0.7) * (1 - p) * 26;
      ctx.fillStyle = rgba(color, 0.18 * (1 - p));
      ctx.fillRect(-this.offsetX + shift, y, this.viewW, 2);
    }
    const sweepX = -this.offsetX + easeOut(p) * this.viewW;
    ctx.fillStyle = rgba("#e8eefc", 0.28 * (1 - p));
    ctx.fillRect(sweepX, -this.offsetY, 10, this.viewH);
    if (effect.label) {
      this._text(ctx, effect.label, LOGICAL_W / 2, 120, 12, color, "center", "bold");
    }
  }

  _drawBanner(ctx, effect, p) {
    const fadeIn = clamp(p / 0.15, 0, 1);
    const fadeOut = p > 0.75 ? 1 - (p - 0.75) / 0.25 : 1;
    const alpha = fadeIn * fadeOut;
    if (alpha <= 0.01) return;
    const text = String(effect.text || "").slice(0, 40);
    const sub = String(effect.sub || "").slice(0, 52);
    const width = Math.max(200, Math.min(440, text.length * 9 + 40));
    const height = sub ? 38 : 28;
    const x = LOGICAL_W / 2 - width / 2;
    const y = (effect.y || 160) - height / 2;
    ctx.globalAlpha = alpha;
    this._panel(ctx, x, y, width, height, "rgba(6, 10, 20, 0.9)", 2, effect.color || ACCENT);
    this._text(ctx, text, LOGICAL_W / 2, y + (sub ? 13 : height / 2), 12, effect.color || C.text, "center", "bold");
    if (sub) this._text(ctx, sub, LOGICAL_W / 2, y + 27, 9, C.muted, "center");
    ctx.globalAlpha = 1;
  }

  _drawGateFlash(ctx, effect, p) {
    const color = effect.color || C.warning;
    const alpha = 0.6 + 0.4 * Math.sin(this.time * 12);
    const x = GATES.x + 8;
    const y = GATES.top;
    this._outline(ctx, x, y, GATES.w - 16, 26, 2, rgba(color, alpha));
    ctx.fillStyle = rgba(color, 0.18 * alpha);
    ctx.fillRect(x, y, GATES.w - 16, 26);
    const lampOn = Math.floor(this.time * 8) % 2 === 0;
    if (lampOn) {
      ctx.fillStyle = color;
      ctx.fillRect(GATES.x + GATES.w / 2 - 2, y + 4, 4, 4);
    }
    if (effect.label) {
      this._text(ctx, effect.label, GATES.x + GATES.w / 2, GATES.top + 36, 9, color, "center", "bold");
    }
  }

  _drawRing(ctx, effect, p) {
    const color = effect.color || C.info;
    const size = 8 + easeOut(p) * 34;
    this._outline(ctx, effect.x - size / 2, effect.y - size / 2, size, size, 2, rgba(color, 1 - p));
  }

  _drawCheck(ctx, effect, p) {
    const color = effect.color || C.success;
    const pop = easeOutBack(clamp(p * 3, 0, 1));
    const x = effect.x;
    const y = effect.y - 24 - pop * 6;
    ctx.fillStyle = color;
    ctx.fillRect(Math.round(x) - 8, Math.round(y), 4, 4);
    ctx.fillRect(Math.round(x) - 4, Math.round(y) + 4, 4, 4);
    ctx.fillRect(Math.round(x), Math.round(y) - 4, 4, 4);
    ctx.fillRect(Math.round(x) - 4, Math.round(y) + 4, 4, 4);
    this._outline(ctx, Math.round(x) - 16, Math.round(y) - 12, 32, 28, 1, rgba(color, 1 - p * 0.7));
  }

  _drawGlitch(ctx, effect, p) {
    const intensity = 1 - p;
    const seed = Math.floor(this.time * 24);
    const rand = seededRandom(seed * 9973 + 17);
    for (let i = 0; i < 6; i += 1) {
      const y = -this.offsetY + rand() * this.viewH;
      const h = 1 + Math.floor(rand() * 6);
      const shift = (rand() - 0.5) * 40 * intensity;
      ctx.fillStyle = `rgba(34, 211, 238, ${0.12 + rand() * 0.16 * intensity})`;
      ctx.fillRect(-this.offsetX + shift, y, this.viewW, h);
    }
    ctx.fillStyle = rgba(C.danger, 0.06 * intensity);
    ctx.fillRect(-this.offsetX, -this.offsetY, this.viewW, this.viewH);
  }

  /* ---- input ------------------------------------------------------------ */

  _toWorld(event) {
    if (!this.canvas || !this.fit) return { x: -1, y: -1 };
    const rect = this.canvas.getBoundingClientRect();
    const cssX = event.clientX - rect.left;
    const cssY = event.clientY - rect.top;
    return {
      x: cssX / this.fit - this.offsetX,
      y: cssY / this.fit - this.offsetY,
    };
  }

  _hitTest(point) {
    if (!point) return null;
    const { x, y } = point;
    const visible = this.views.filter((view) => view.present && view.agent).reverse();
    for (const view of visible) {
      const agent = view.agent;
      const half = (SPRITE_W * view.scale) / 2 + 3;
      const height = SPRITE_H * view.scale;
      if (x >= agent.x - half && x <= agent.x + half && y >= agent.y - height && y <= agent.y + 4) {
        return { kind: "agent", key: view.name, family: view.family };
      }
    }
    const mascot = this.mascot;
    if (Math.abs(x - mascot.x) <= 12 && Math.abs(y - mascot.drawY) <= 14) {
      return { kind: "mascot", key: "mascot" };
    }
    for (const room of ROOM_DEFS) {
      if (x >= room.x && x <= room.x + room.w && y >= room.top - 6 && y <= room.floor + 16) {
        return { kind: "room", key: room.family, family: room.family };
      }
    }
    for (const zone of [LOUNGE, REVIEW_DECK, GATES]) {
      if (x >= zone.x && x <= zone.x + zone.w && y >= zone.top && y <= zone.floor + 6) {
        return { kind: "room", key: "PLAZA", family: "" };
      }
    }
    return null;
  }

  _handleClick(event) {
    const hit = this._hitTest(this._toWorld(event));
    if (!hit) return;
    const options = this.opts || {};
    if (hit.kind === "agent" && typeof options.onAgentClick === "function") {
      options.onAgentClick(hit.key);
    } else if (hit.kind === "mascot" && typeof options.onMascotClick === "function") {
      options.onMascotClick();
    } else if (hit.kind === "room" && typeof options.onRoomClick === "function") {
      options.onRoomClick(hit.family || hit.key);
    }
    if (hit.kind === "mascot") {
      this.mascot.sayText = "HALT! READ-ONLY.";
      this.mascot.sayUntil = this.time + 2.4;
    }
  }

  _handlePointerMove(event) {
    const hit = this._hitTest(this._toWorld(event));
    this.hover = hit;
    const interactive = Boolean(hit);
    if (this.canvas) this.canvas.classList.toggle("is-clickable", interactive);
    this.mascot.hover = Boolean(hit && hit.kind === "mascot");
  }

  /* ---- chrome (caption + aria-label) ------------------------------------ */

  _updateChrome() {
    const model = this.model;
    const meta = (model && model.meta) || {};
    const wrapup = this._wrapup();
    const wrapVisible = this._wrapupVisible();
    const total = this.events.length;
    const cursor = this.cursor;
    const crew = this.views.filter((view) => view.present).length;
    const onDuty = this.views.filter((view) => view.present && view.active).length;
    const phase = this.phaseState.current || (wrapVisible ? "WRAP-UP" : "");
    const project = String(meta.project || (model && model.source && model.source.slug) || "run");

    if (this.captionEl && !model) {
      /* No run is open: say so instead of captioning an empty floor. */
      this.captionEl.textContent = "NO RUN OPEN · PICK A RUN OR THE TRAINING SIMULATION";
    } else if (this.captionEl) {
      const parts = [project.toUpperCase()];
      if (phase) parts.push(`PHASE ${phase}`);
      parts.push(`${crew} CREW`);
      if (total > 0) parts.push(`EVENT ${clamp(cursor, 0, total)}/${total}`);
      if (wrapVisible && wrapup) {
        const state = String(wrapup.pr_state || "").toUpperCase();
        parts.push(wrapup.pr_number !== undefined && wrapup.pr_number !== null
          ? `PR #${wrapup.pr_number}${state ? ` ${state}` : ""}`
          : "SHIPPED");
      }
      if (onDuty > 0 && !wrapVisible) parts.push(`${onDuty} ACTIVE`);
      if (this.live) parts.push("LIVE");
      this.captionEl.textContent = parts.join(" · ");
    }

    const ariaParts = [];
    ariaParts.push(`PACT Mission Control floor for ${project}.`);
    ariaParts.push(phase ? `Phase ${phase}.` : "No phase active.");
    ariaParts.push(`${crew} crew present, ${onDuty} active.`);
    /* Where the crew stands is otherwise drawn only on the canvas. */
    const onDeck = this.views.filter((view) => view.present && view.zone === "review").length;
    const resting = this.views.filter((view) => view.present && view.zone === "lounge").length;
    ariaParts.push(`${onDeck} on the review deck, ${resting} in the standby lounge.`);
    ariaParts.push(total > 0 ? `Showing ${clamp(cursor, 0, total)} of ${total} events.` : "No events yet.");
    if (wrapVisible && wrapup) {
      const state = String(wrapup.pr_state || "").toUpperCase();
      ariaParts.push(wrapup.pr_number !== undefined && wrapup.pr_number !== null
        ? `Shipped pull request ${wrapup.pr_number}${state ? ` ${state}` : ""}.`
        : "Run shipped.");
    }
    ariaParts.push("Select a crew member in the crew roster to highlight them here.");
    const aria = ariaParts.join(" ");
    if (this.canvas && aria !== this.aria) {
      this.aria = aria;
      this.canvas.setAttribute("aria-label", aria);
    }
  }
}

export default Stage;
