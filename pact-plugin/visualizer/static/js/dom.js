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

export function shortSha(sha) {
  if (!sha) return "";
  return String(sha).slice(0, 7);
}

export function debounce(fn, ms = 120) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
