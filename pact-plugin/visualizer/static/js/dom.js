/* Tiny DOM helpers. No innerHTML anywhere: all data becomes text nodes. */

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null) continue;
    if (key === "class" || key === "className") {
      node.className = String(value);
    } else if (key === "text") {
      node.textContent = String(value);
    } else if (key === "style" && typeof value === "object") {
      Object.assign(node.style, value);
    } else if (key === "dataset" && typeof value === "object") {
      for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = String(dv);
    } else if (key === "on" && typeof value === "object") {
      for (const [type, fn] of Object.entries(value)) {
        if (typeof fn === "function") node.addEventListener(type, fn);
      }
    } else if (key === "attrs" && typeof value === "object") {
      for (const [ak, av] of Object.entries(value)) {
        if (av === false || av === undefined || av === null) continue;
        // Event-handler and URL attributes are the only attribute names that
        // can execute; every real call site uses a literal name, so reject
        // these rather than let a future computed key become a sink.
        if (/^(on|href$|src$|srcdoc$|xlink:href$)/i.test(ak)) {
          throw new Error(`dom.el: refusing attribute '${ak}'`);
        }
        node.setAttribute(ak, av === true ? "" : String(av));
      }
    } else if (key === "value") {
      node.value = value;
    } else {
      // Unknown props fail loudly: a generic node[key] = value fallback
      // would let a markup property slip past review instead of throwing.
      throw new Error(`dom.el: unsupported prop '${key}'; use class, text, style, dataset, on, attrs, or value`);
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  if (children === undefined || children === null) return;
  if (Array.isArray(children)) {
    for (const child of children) append(node, child);
  } else if (children instanceof Node) {
    node.appendChild(children);
  } else {
    node.appendChild(document.createTextNode(String(children)));
  }
}

export function clear(node) {
  if (!node) return node;
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function text(node, value) {
  if (!node) return node;
  node.textContent = value === undefined || value === null ? "" : String(value);
  return node;
}

export function toggleClass(node, name, on) {
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

export function on(node, type, fn, opts) {
  if (!node || typeof fn !== "function") return () => {};
  node.addEventListener(type, fn, opts);
  return () => node.removeEventListener(type, fn, opts);
}

export function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(n) || 0));
}

export function fmtClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

export function fmtDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* Mission time at a cursor that indexes the VISIBLE event window. Cursor 0
 * is the journal start, which is 0s by definition; an empty window must not
 * fall back to wall-clock time (that reads as a plausible wrong duration). */
export function missionSecondsAt(model, cursor) {
  if (!model || !model.meta || !model.meta.started_at) return 0;
  const start = new Date(model.meta.started_at).getTime();
  if (Number.isNaN(start)) return 0;
  const events = Array.isArray(model.events) ? model.events : [];
  if (!events.length || cursor <= 0) return 0;
  const event = events[clamp(cursor - 1, 0, events.length - 1)];
  const end = event && event.ts ? new Date(event.ts).getTime() : NaN;
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function shortSha(sha) {
  if (!sha) return "";
  return String(sha).slice(0, 7);
}

/* A run's identity is its coordinates, never its journal revision: a live
 * poll must not read as a different run. Shared by the stage and panels. */
export function runKey(model) {
  if (!model) return "";
  const source = model.source || {};
  const meta = model.meta || {};
  return [
    source.kind || "",
    source.root || "",
    source.slug || "",
    source.session_id || "",
    meta.team || "",
  ].join("|");
}

/* Journal `seq` values are physical line numbers, not positions in the
 * visible event array (the server keeps only the newest MAX_EVENTS). Build the
 * mapping once per model: firstIndexAtOrAfter(seq) returns the first visible
 * index whose seq is >= the target, clamped to events.length, or null when
 * `seq` is absent (a pending phase). Ascending seq is a contract guarantee. */
export function createSeqIndex(model) {
  const events = model && Array.isArray(model.events) ? model.events : [];
  const seqs = new Array(events.length);
  for (let index = 0; index < events.length; index += 1) {
    seqs[index] = Number(events[index] && events[index].seq);
  }
  return function firstIndexAtOrAfter(seq) {
    if (seq === undefined || seq === null) return null;
    const target = Number(seq);
    if (!Number.isFinite(target)) return null;
    let lo = 0;
    let hi = seqs.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (Number.isFinite(seqs[mid]) && seqs[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
}

/* Canvas modules read design tokens off the document root. One fallback map
 * keeps timeline.js and charts.js painting the same colours in tests,
 * headless runs, and before the stylesheet has applied. */
const CSS_FALLBACK = {
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

export function cssToken(name) {
  const key = String(name || "").replace(/^--/, "");
  const fallback = CSS_FALLBACK[key] || CSS_FALLBACK.text;
  try {
    if (typeof getComputedStyle !== "function") return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return value && value.trim() ? value.trim() : fallback;
  } catch (_) {
    return fallback;
  }
}

export function debounce(fn, ms = 120) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
