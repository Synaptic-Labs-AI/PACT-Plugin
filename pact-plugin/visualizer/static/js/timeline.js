/* PACT Mission Control — flight recorder timeline.
 * Transport controls, phase bands, category-coloured event markers, replay
 * cursor and mission labels. The visual track is a decorative canvas; a real
 * range input underneath keeps the recorder keyboard accessible. */

import { el, clear, fmtClock, fmtTime, clamp } from "./dom.js";

const SPEEDS = [1, 2, 4, 8];

const LANES = [
  { key: "ops", cats: ["session", "phase", "checkpoint", "compact", "wake", "task", "teachback", "variety", "raw"] },
  { key: "dispatch", cats: ["dispatch"] },
  { key: "handoff", cats: ["handoff"] },
  { key: "commit", cats: ["commit", "pr"] },
  { key: "review", cats: ["review", "finding", "remediation"] },
  { key: "gate", cats: ["gate", "alert"] },
];

const LANE_BY_CATEGORY = new Map();
LANES.forEach((lane, index) => {
  for (const category of lane.cats) LANE_BY_CATEGORY.set(category, index);
});

const FALLBACK = {
  text: "#e8eefc",
  muted: "#8fa0c4",
  dim: "#5f7096",
  line: "#24324f",
  accent: "#a3e635",
  success: "#4ade80",
  warning: "#fbbf24",
  danger: "#fb7185",
  violet: "#a78bfa",
  prepare: "#2dd4bf",
  architect: "#60a5fa",
  code: "#fbbf24",
  test: "#f472b6",
};

let paletteCache = null;

function palette() {
  if (paletteCache) return paletteCache;
  const read = (name, fallback) => {
    try {
      if (typeof getComputedStyle !== "function") return fallback;
      const value = getComputedStyle(document.documentElement).getPropertyValue(name);
      return value && value.trim() ? value.trim() : fallback;
    } catch (_) {
      return fallback;
    }
  };
  paletteCache = {
    text: read("--text", FALLBACK.text),
    muted: read("--muted", FALLBACK.muted),
    dim: read("--dim", FALLBACK.dim),
    line: read("--line", FALLBACK.line),
    accent: read("--accent", FALLBACK.accent),
    success: read("--success", FALLBACK.success),
    warning: read("--warning", FALLBACK.warning),
    danger: read("--danger", FALLBACK.danger),
    violet: read("--violet", FALLBACK.violet),
    prepare: read("--prepare", FALLBACK.prepare),
    architect: read("--architect", FALLBACK.architect),
    code: read("--code", FALLBACK.code),
    test: read("--test", FALLBACK.test),
  };
  return paletteCache;
}

function colorFor(event, colors) {
  const category = String((event && event.category) || "other");
  const phase = String((event && event.phase) || "").toLowerCase();
  if (category === "phase" && colors[phase]) return colors[phase];
  switch (category) {
    case "dispatch":
      return colors.architect;
    case "handoff":
      return colors.prepare;
    case "commit":
      return colors.code;
    case "review":
      return colors.violet;
    case "finding":
    case "remediation":
      return colors.danger;
    case "gate":
      return colors.warning;
    case "alert":
      return colors.danger;
    case "pr":
      return colors.success;
    case "checkpoint":
      return colors.test;
    case "session":
      return colors.muted;
    default:
      return colors.dim;
  }
}

function colorKeyFor(event) {
  const category = String((event && event.category) || "other");
  if (category === "phase") return `phase:${String((event && event.phase) || "").toLowerCase()}`;
  return category;
}

function laneFor(event) {
  const category = String((event && event.category) || "");
  const lane = LANE_BY_CATEGORY.get(category);
  return lane === undefined ? 0 : lane;
}

function toggleClass(node, name, on) {
  if (!node) return;
  if (node.classList && typeof node.classList.toggle === "function") {
    node.classList.toggle(name, Boolean(on));
    return;
  }
  const set = new Set(String(node.className || "").split(/\s+/).filter(Boolean));
  if (on) set.add(name);
  else set.delete(name);
  node.className = [...set].join(" ");
}

function safeClass(value) {
  const cleaned = String(value || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  return cleaned || "run";
}

function truncate(value, max) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, Math.max(0, max - 1))}…` : text;
}

export function createTimeline(root, options = {}) {
  if (!root) {
    return {
      setModel() {},
      setCursor() {},
      setMode() {},
      destroy() {},
    };
  }
  const callbacks = {
    onSeek: typeof options.onSeek === "function" ? options.onSeek : () => {},
    onPlayPause: typeof options.onPlayPause === "function" ? options.onPlayPause : () => {},
    onSpeed: typeof options.onSpeed === "function" ? options.onSpeed : () => {},
    onToggleLive: typeof options.onToggleLive === "function" ? options.onToggleLive : () => {},
  };
  const state = {
    model: null,
    cursor: 0,
    live: false,
    playing: false,
    speed: 1,
    hover: null,
  };

  let canvas = null;
  let context = null;
  let drawWidth = 0;
  let drawHeight = 0;
  let drawQueued = false;
  let resizeObserver = null;
  let destroyed = false;

  /* Build the recorder shell once; setters update it in place. */
  const shell = el("div", { class: "rec-shell" });

  const playButton = el("button", {
    class: "rec-btn rec-play",
    text: "▶",
    attrs: { type: "button", title: "Play or pause replay (space)", "aria-label": "Play replay", "aria-pressed": "false" },
  });
  playButton.addEventListener("click", () => callbacks.onPlayPause());

  const speedButtons = new Map();
  const speedsWrap = el("div", { class: "rec-speeds", attrs: { role: "group", "aria-label": "Playback speed" } });
  for (const speed of SPEEDS) {
    const button = el("button", {
      class: "rec-btn rec-speed",
      text: `${speed}×`,
      attrs: { type: "button", title: `Playback speed ${speed}x`, "aria-pressed": "false" },
    });
    button.addEventListener("click", () => callbacks.onSpeed(speed));
    speedButtons.set(speed, button);
    speedsWrap.appendChild(button);
  }

  const liveButton = el("button", {
    class: "rec-btn rec-live",
    text: "LIVE",
    attrs: { type: "button", title: "Return to the live head (l)", "aria-pressed": "false" },
  });
  liveButton.addEventListener("click", () => callbacks.onToggleLive());

  const phaseLabel = el("span", { class: "rec-meta-phase", text: "STANDBY", attrs: { title: "Phase at the cursor" } });
  const clockLabel = el("span", { class: "rec-meta-clock", text: "00:00:00", attrs: { title: "Mission time at the cursor" } });
  const countLabel = el("span", { class: "rec-meta-count", text: "0 / 0", attrs: { title: "Events replayed" } });
  const meta = el("div", { class: "rec-meta" }, [phaseLabel, clockLabel, countLabel]);

  const top = el("div", { class: "rec-top" }, [
    playButton,
    speedsWrap,
    liveButton,
    el("div", { class: "rec-spacer" }),
    meta,
  ]);

  const bandLayer = el("div", { class: "rec-bands", attrs: { "aria-hidden": "true" } });
  canvas = el("canvas", { class: "rec-canvas", attrs: { "aria-hidden": "true" } });
  const cursorElement = el("div", { class: "rec-cursor", attrs: { "aria-hidden": "true" } });
  const track = el("div", { class: "rec-track", attrs: { role: "presentation" } }, [bandLayer, canvas, cursorElement]);

  const slider = el("input", {
    class: "rec-slider",
    attrs: { type: "range", min: "0", max: "0", value: "0", step: "1", "aria-label": "Flight recorder position" },
  });
  slider.addEventListener("input", () => {
    callbacks.onSeek(clamp(Number(slider.value) || 0, 0, eventTotal()));
  });

  shell.appendChild(top);
  shell.appendChild(track);
  shell.appendChild(slider);
  clear(root);
  root.appendChild(shell);

  /* --- data helpers ----------------------------------------------------- */

  function eventTotal() {
    return state.model && Array.isArray(state.model.events) ? state.model.events.length : 0;
  }

  function eventAtCursor() {
    const total = eventTotal();
    if (!total) return null;
    const index = clamp(state.cursor - 1, 0, total - 1);
    return state.model.events[index];
  }

  function phaseAtCursor() {
    const event = eventAtCursor();
    if (event && event.phase) return String(event.phase).toUpperCase();
    const phases = state.model && state.model.phases;
    return phases && phases.current ? String(phases.current).toUpperCase() : "STANDBY";
  }

  function missionSecondsAt(cursor) {
    const model = state.model;
    if (!model || !model.meta || !model.meta.started_at) return 0;
    const start = new Date(model.meta.started_at).getTime();
    if (Number.isNaN(start)) return 0;
    const total = eventTotal();
    if (!total || cursor <= 0) return 0;
    const event = model.events[clamp(cursor - 1, 0, total - 1)];
    const end = event && event.ts ? new Date(event.ts).getTime() : NaN;
    if (Number.isNaN(end)) return 0;
    return Math.max(0, Math.round((end - start) / 1000));
  }

  /* --- rendering -------------------------------------------------------- */

  function updatePhaseLabel() {
    if (state.hover !== null) return;
    phaseLabel.textContent = phaseAtCursor();
  }

  function updateCursorDom() {
    const total = eventTotal();
    const cursor = clamp(state.cursor, 0, total);
    cursorElement.style.left = `${total ? (cursor / total) * 100 : 0}%`;
    cursorElement.hidden = total === 0;
    slider.max = String(total);
    slider.value = String(cursor);
    slider.setAttribute("aria-valuetext", `${cursor} of ${total} events`);
    countLabel.textContent = `${cursor} / ${total}`;
    clockLabel.textContent = fmtClock(missionSecondsAt(cursor));
    updatePhaseLabel();
  }

  function updateModeDom() {
    toggleClass(shell, "is-live", state.live);
    toggleClass(shell, "is-playing", state.playing);
    playButton.textContent = state.playing ? "❚❚" : "▶";
    playButton.setAttribute("aria-label", state.playing ? "Pause replay" : "Play replay");
    playButton.setAttribute("aria-pressed", state.playing ? "true" : "false");
    toggleClass(playButton, "is-active", state.playing);
    for (const [speed, button] of speedButtons) {
      const active = state.speed === speed;
      toggleClass(button, "is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    toggleClass(liveButton, "is-active", state.live);
    liveButton.setAttribute("aria-pressed", state.live ? "true" : "false");
  }

  function renderBands() {
    clear(bandLayer);
    const model = state.model;
    const total = eventTotal();
    if (!total) {
      bandLayer.appendChild(el("div", { class: "rec-band rec-band-idle" }));
      return;
    }
    const items = model && model.phases && Array.isArray(model.phases.items) ? model.phases.items.slice() : [];
    const bands = [];
    for (const item of items) {
      const startSeq = Number(item.started_seq);
      const endSeq = Number(item.ended_seq);
      const start = Number.isFinite(startSeq) ? clamp(startSeq, 0, total - 1) : 0;
      const end = Number.isFinite(endSeq) ? clamp(endSeq + 1, start + 1, total) : total;
      bands.push({
        name: String(item.name || item.family || "PHASE"),
        family: String(item.family || item.name || "").toLowerCase(),
        start,
        end,
        run: Number(item.run) || 1,
      });
    }
    bands.sort((left, right) => left.start - right.start || left.end - right.end);
    if (!bands.length) {
      const current = model && model.phases && model.phases.current ? String(model.phases.current) : "RUN";
      bands.push({ name: current, family: current.toLowerCase(), start: 0, end: total, run: 1 });
    }
    const normalized = [];
    let position = 0;
    for (const band of bands) {
      if (band.start > position) {
        normalized.push({ name: "PRELUDE", family: "prelude", start: position, end: band.start, run: 1 });
      }
      const start = Math.max(position, band.start);
      const end = Math.max(start, Math.min(total, band.end));
      if (end > start) normalized.push({ name: band.name, family: band.family, start, end, run: band.run });
      position = Math.max(position, end);
    }
    if (position < total) {
      normalized.push({ name: "TAIL", family: "tail", start: position, end: total, run: 1 });
    }
    for (const band of normalized) {
      const node = el("div", {
        class: `rec-band band-${safeClass(band.family)}`,
        attrs: { title: `${band.name}${band.run > 1 ? ` · run ${band.run}` : ""}` },
      }, [el("span", { class: "rec-band-label", text: band.name })]);
      node.style.left = `${(band.start / total) * 100}%`;
      node.style.width = `${((band.end - band.start) / total) * 100}%`;
      bandLayer.appendChild(node);
    }
  }

  function getContext() {
    if (!context && canvas && typeof canvas.getContext === "function") {
      context = canvas.getContext("2d");
    }
    return context;
  }

  function resizeCanvas() {
    if (!canvas || !track) return;
    let rect = { width: 0, height: 0 };
    try {
      if (typeof track.getBoundingClientRect === "function") rect = track.getBoundingClientRect();
    } catch (_) {
      rect = { width: 0, height: 0 };
    }
    drawWidth = Math.max(40, Math.floor(rect.width || track.clientWidth || 0));
    drawHeight = Math.max(18, Math.floor(rect.height || track.clientHeight || 0));
    const dpr = Math.max(1, Math.min(3, (typeof window !== "undefined" && window.devicePixelRatio) || 1));
    const backingWidth = Math.round(drawWidth * dpr);
    const backingHeight = Math.round(drawHeight * dpr);
    if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
      canvas.width = backingWidth;
      canvas.height = backingHeight;
      context = null;
    }
    const ctx = getContext();
    if (ctx && typeof ctx.setTransform === "function") ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawMarkers() {
    const ctx = getContext();
    if (!ctx) return;
    const width = Math.max(1, drawWidth || 900);
    const height = Math.max(1, drawHeight || 30);
    ctx.clearRect(0, 0, width, height);
    const colors = palette();
    const events = state.model && Array.isArray(state.model.events) ? state.model.events : [];
    const total = events.length;
    const laneCount = LANES.length;
    const cellHeight = height / laneCount;

    ctx.save();
    ctx.strokeStyle = colors.line;
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 1;
    for (let lane = 1; lane < laneCount; lane += 1) {
      const y = Math.round(cellHeight * lane) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.restore();
    if (!total) return;

    const dimBuckets = new Uint8Array(width * laneCount);
    const brightBuckets = new Uint8Array(width * laneCount);
    const colorEntries = [];
    const colorLookup = new Map();
    const cursor = clamp(state.cursor, 0, total);

    for (let index = 0; index < total; index += 1) {
      const event = events[index];
      const lane = laneFor(event);
      const x = total === 1
        ? Math.floor((width - 1) / 2)
        : Math.min(width - 1, Math.floor(((index + 0.5) / total) * (width - 1)));
      const slot = lane * width + x;
      if (index < cursor) {
        const key = colorKeyFor(event);
        let colorIndex = colorLookup.get(key);
        if (colorIndex === undefined) {
          colorIndex = colorEntries.length;
          colorLookup.set(key, colorIndex);
          colorEntries.push(colorFor(event, colors));
        }
        brightBuckets[slot] = Math.min(255, colorIndex + 1);
      } else {
        dimBuckets[slot] = 1;
      }
    }

    ctx.save();
    ctx.globalAlpha = 0.3;
    ctx.fillStyle = colors.muted;
    for (let lane = 0; lane < laneCount; lane += 1) {
      let x = 0;
      while (x < width) {
        const slot = lane * width + x;
        if (!dimBuckets[slot] || brightBuckets[slot]) {
          x += 1;
          continue;
        }
        let end = x + 1;
        while (end < width && dimBuckets[lane * width + end] && !brightBuckets[lane * width + end]) end += 1;
        ctx.fillRect(x, lane * cellHeight + 1, end - x, Math.max(1, cellHeight - 2));
        x = end;
      }
    }
    ctx.restore();

    for (let lane = 0; lane < laneCount; lane += 1) {
      let x = 0;
      while (x < width) {
        const value = brightBuckets[lane * width + x];
        if (!value) {
          x += 1;
          continue;
        }
        let end = x + 1;
        while (end < width && brightBuckets[lane * width + end] === value) end += 1;
        ctx.fillStyle = colorEntries[value - 1] || colors.accent;
        ctx.fillRect(x, lane * cellHeight + 1, end - x, Math.max(1, cellHeight - 2));
        x = end;
      }
    }
  }

  function queueDraw() {
    if (destroyed || drawQueued) return;
    drawQueued = true;
    const run = () => {
      drawQueued = false;
      if (!destroyed) drawMarkers();
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else setTimeout(run, 16);
  }

  /* --- pointer interaction --------------------------------------------- */

  function fractionFromEvent(event) {
    let rect = { left: 0, width: 0 };
    try {
      rect = track.getBoundingClientRect();
    } catch (_) {
      rect = { left: 0, width: 0 };
    }
    if (!rect || !rect.width) return 0;
    return clamp((event.clientX - rect.left) / rect.width, 0, 1);
  }

  function setHovering(on) {
    toggleClass(track, "is-hovering", on);
  }

  track.addEventListener("pointermove", (event) => {
    const total = eventTotal();
    if (!total) return;
    const fraction = fractionFromEvent(event);
    const index = clamp(Math.floor(fraction * total), 0, total - 1);
    state.hover = index;
    const item = state.model.events[index];
    phaseLabel.textContent = `${fmtTime(item.ts) || "--:--"} · ${truncate(item.title || item.type || "event", 52)}`;
    setHovering(true);
  });

  track.addEventListener("pointerleave", () => {
    state.hover = null;
    setHovering(false);
    updatePhaseLabel();
  });

  track.addEventListener("click", (event) => {
    const total = eventTotal();
    if (!total) return;
    const fraction = fractionFromEvent(event);
    callbacks.onSeek(clamp(Math.floor(fraction * total) + 1, 0, total));
  });

  /* --- public interface -------------------------------------------------- */

  function setModel(model) {
    if (destroyed) return;
    state.model = model && typeof model === "object" ? model : null;
    state.hover = null;
    const total = eventTotal();
    state.cursor = clamp(state.cursor, 0, total);
    renderBands();
    resizeCanvas();
    updateCursorDom();
    updateModeDom();
    queueDraw();
  }

  function setCursor(cursor) {
    if (destroyed) return;
    const total = eventTotal();
    state.cursor = clamp(Math.round(Number(cursor) || 0), 0, total);
    updateCursorDom();
    queueDraw();
  }

  function setMode(mode = {}) {
    if (mode && mode.live !== undefined) state.live = Boolean(mode.live);
    if (mode && mode.playing !== undefined) state.playing = Boolean(mode.playing);
    if (mode && mode.speed !== undefined && SPEEDS.includes(Number(mode.speed))) {
      state.speed = Number(mode.speed);
    }
    updateModeDom();
  }

  function handleResize() {
    resizeCanvas();
    queueDraw();
  }

  renderBands();
  updateCursorDom();
  updateModeDom();
  resizeCanvas();
  queueDraw();

  let usedWindowResize = false;
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(track);
  } else if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    usedWindowResize = true;
    window.addEventListener("resize", handleResize);
  }

  /* Mirrors Stage.destroy: stop observation, drop the fallback listener,
   * drop the built shell, and make later setters no-ops. Element listeners
   * on the shell die with the removed nodes. */
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (resizeObserver) {
      try { resizeObserver.disconnect(); } catch (err) { /* ignore */ }
      resizeObserver = null;
    }
    if (usedWindowResize && typeof window !== "undefined" && typeof window.removeEventListener === "function") {
      window.removeEventListener("resize", handleResize);
    }
    clear(root);
    state.model = null;
    state.hover = null;
  }

  return { setModel, setCursor, setMode, destroy };
}