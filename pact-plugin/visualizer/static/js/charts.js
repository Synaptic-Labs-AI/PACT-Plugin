/* PACT Mission Control — dependency-free canvas charts.
 * drawRadar() renders the variety radar and drawSparkline() renders variety
 * history. Both are device-pixel-ratio aware, read their colours from the CSS
 * tokens in base.css, and never load an external library or asset. */

import { cssToken } from "./dom.js";

const DIM_KEYS = ["novelty", "scope", "uncertainty", "risk"];
const DIM_LABELS = {
  novelty: "NOVELTY",
  scope: "SCOPE",
  uncertainty: "UNCERTAINTY",
  risk: "RISK",
};

const token = cssToken;

/* Canvas accepts rgba() for every token; tokens are hex, so convert. */
function withAlpha(color, alpha) {
  const value = String(color || "").trim();
  const match = value.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return value || `rgba(232, 238, 252, ${alpha})`;
  let hex = match[1];
  if (hex.length === 3) hex = hex.split("").map((ch) => ch + ch).join("");
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function setFont(ctx, size) {
  ctx.font = `${size}px "SF Mono", ui-monospace, Menlo, Consolas, monospace`;
}

/* Size the backing store from the CSS layout box and expose logical pixels. */
function surface(canvas, options) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  let rect = { width: 0, height: 0 };
  try {
    if (typeof canvas.getBoundingClientRect === "function") rect = canvas.getBoundingClientRect();
  } catch (_) {
    rect = { width: 0, height: 0 };
  }
  const fallbackWidth = Number(canvas.width) || 240;
  const fallbackHeight = Number(canvas.height) || 150;
  const width = Math.max(80, Math.round(Number(options.width) || rect.width || fallbackWidth));
  const height = Math.max(60, Math.round(Number(options.height) || rect.height || fallbackHeight));
  const dpr = Math.max(1, Math.min(3, (typeof window !== "undefined" && window.devicePixelRatio) || 1));
  const backingWidth = Math.round(width * dpr);
  const backingHeight = Math.round(height * dpr);
  if (canvas.width !== backingWidth) canvas.width = backingWidth;
  if (canvas.height !== backingHeight) canvas.height = backingHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function normalizeDims(dims) {
  if (Array.isArray(dims) && dims.length) {
    return dims.map((item, index) => {
      if (typeof item === "number") {
        const key = DIM_KEYS[index] || `dim${index}`;
        return { key, label: DIM_LABELS[key] || `D${index + 1}`, value: item };
      }
      if (!item || typeof item !== "object") {
        return { key: `dim${index}`, label: `D${index + 1}`, value: 0 };
      }
      const rawKey = String(item.key || item.dim || item.name || DIM_KEYS[index] || `dim${index}`);
      const label = String(item.label || item.name || DIM_LABELS[rawKey.toLowerCase()] || rawKey).toUpperCase();
      const source = item.value !== undefined ? item.value : item.total !== undefined ? item.total : 0;
      return { key: rawKey, label, value: Number(source) || 0 };
    });
  }
  if (dims && typeof dims === "object") {
    return DIM_KEYS.map((key) => ({ key, label: DIM_LABELS[key], value: Number(dims[key]) || 0 }));
  }
  return DIM_KEYS.map((key) => ({ key, label: DIM_LABELS[key], value: 0 }));
}

export function drawRadar(canvas, dims, options = {}) {
  const view = surface(canvas, options);
  if (!view) return;
  const entries = normalizeDims(dims);
  if (!entries.length) return;
  const { ctx, width, height } = view;
  const color = options.color || token("--accent");
  const gridColor = token("--line");
  const max = Math.max(1, Number(options.max) || 4);
  const n = entries.length;

  const cx = width / 2;
  const cy = height / 2 + (options.shiftDown ? 3 : 0);
  const radius = Math.max(18, Math.min(width, height) / 2 - Math.max(28, options.padding || 30));
  const angleFor = (index) => -Math.PI / 2 + (index / n) * Math.PI * 2;

  ctx.save();

  /* Concentric rings. */
  for (let ring = 1; ring <= max; ring += 1) {
    const ringRadius = (radius * ring) / max;
    ctx.beginPath();
    for (let i = 0; i < n; i += 1) {
      const angle = angleFor(i);
      const x = cx + Math.cos(angle) * ringRadius;
      const y = cy + Math.sin(angle) * ringRadius;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = gridColor;
    ctx.globalAlpha = ring === max ? 0.85 : 0.4;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /* Spokes. */
  ctx.globalAlpha = 0.35;
  for (let i = 0; i < n; i += 1) {
    const angle = angleFor(i);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
    ctx.strokeStyle = gridColor;
    ctx.stroke();
  }

  /* Value polygon. */
  const points = entries.map((entry, index) => {
    const angle = angleFor(index);
    const factor = Math.min(1, Math.max(0, (Number(entry.value) || 0) / max));
    return [cx + Math.cos(angle) * radius * factor, cy + Math.sin(angle) * radius * factor];
  });
  ctx.globalAlpha = 1;
  if (points.length >= 2) {
    ctx.beginPath();
    points.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = withAlpha(color, 0.16);
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  ctx.fillStyle = color;
  for (const [x, y] of points) {
    ctx.beginPath();
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
  }

  /* Axis labels: label above value, placed just outside each vertex. */
  setFont(ctx, 11);
  ctx.textBaseline = "middle";
  entries.forEach((entry, index) => {
    const angle = angleFor(index);
    const cos = Math.cos(angle);
    const x = cx + cos * (radius + 12);
    const y = cy + Math.sin(angle) * (radius + 12);
    if (cos > 0.25) ctx.textAlign = "left";
    else if (cos < -0.25) ctx.textAlign = "right";
    else ctx.textAlign = "center";
    ctx.fillStyle = token("--muted");
    ctx.fillText(entry.label, x, y - 6);
    ctx.fillStyle = token("--text");
    ctx.fillText(String(entry.value), x, y + 6);
  });

  if (options.centerText !== undefined && options.centerText !== null && options.centerText !== "") {
    setFont(ctx, 14);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = token("--text");
    ctx.fillText(String(options.centerText), cx, cy);
  }
  if (options.centerSub) {
    setFont(ctx, 11);
    ctx.textAlign = "center";
    ctx.fillStyle = token("--dim");
    ctx.fillText(String(options.centerSub), cx, cy + 13);
  }

  ctx.restore();
}

function normalizeValues(values) {
  const list = Array.isArray(values) ? values : [];
  const numbers = [];
  for (const item of list) {
    const raw = item && typeof item === "object"
      ? item.value !== undefined ? item.value : item.total
      : item;
    const number = Number(raw);
    if (Number.isFinite(number)) numbers.push(number);
  }
  return numbers;
}

export function drawSparkline(canvas, values, options = {}) {
  const view = surface(canvas, options);
  if (!view) return;
  const { ctx, width, height } = view;
  const numbers = normalizeValues(values);
  const lineColor = options.color || token("--accent");
  const padLeft = 10;
  const padRight = 10;
  const padTop = 12;
  const padBottom = 10;
  const innerWidth = Math.max(1, width - padLeft - padRight);
  const innerHeight = Math.max(1, height - padTop - padBottom);

  ctx.save();
  ctx.strokeStyle = token("--line");
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  for (let line = 0; line <= 2; line += 1) {
    const y = padTop + (innerHeight * line) / 2 + 0.5;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - padRight, y);
    ctx.stroke();
  }
  ctx.restore();

  if (!numbers.length) {
    setFont(ctx, 11);
    ctx.fillStyle = token("--dim");
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("no variety history yet", width / 2, height / 2);
    return;
  }

  let rawMin = numbers[0];
  let rawMax = numbers[0];
  for (const number of numbers) {
    if (number < rawMin) rawMin = number;
    if (number > rawMax) rawMax = number;
  }
  const min = Math.min(Number.isFinite(options.min) ? Number(options.min) : rawMin, rawMin);
  const max = Math.max(Number.isFinite(options.max) ? Number(options.max) : rawMax, rawMax);
  const span = max - min || 1;

  const pointFor = (value, index) => {
    const x = numbers.length === 1
      ? padLeft + innerWidth / 2
      : padLeft + (innerWidth * index) / (numbers.length - 1);
    const y = padTop + innerHeight - ((value - min) / span) * innerHeight;
    return [x, y];
  };
  const points = numbers.map(pointFor);

  ctx.beginPath();
  ctx.moveTo(points[0][0], padTop + innerHeight);
  for (const [x, y] of points) ctx.lineTo(x, y);
  ctx.lineTo(points[points.length - 1][0], padTop + innerHeight);
  ctx.closePath();
  ctx.fillStyle = withAlpha(lineColor, 0.14);
  ctx.fill();

  ctx.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();

  ctx.fillStyle = lineColor;
  for (const [x, y] of points) {
    ctx.beginPath();
    ctx.arc(x, y, 1.8, 0, Math.PI * 2);
    ctx.fill();
  }

  if (options.showLabels !== false) {
    setFont(ctx, 11);
    ctx.fillStyle = token("--dim");
    ctx.textBaseline = "bottom";
    ctx.textAlign = "left";
    ctx.fillText(String(numbers[0]), padLeft, padTop - 2);
    ctx.textAlign = "right";
    ctx.fillText(String(numbers[numbers.length - 1]), width - padRight, padTop - 2);
  }
}