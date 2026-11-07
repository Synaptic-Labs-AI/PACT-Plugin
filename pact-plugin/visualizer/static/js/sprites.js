/* PACT Mission Control — programmatic pixel-art sprites.
 *
 * No images, no external assets, no smoothing: every crew member is drawn from
 * hard rectangles on a small integer grid. Role-specific silhouettes and
 * accessories (crown, hard hat, shield, test tube, wrench, ...) make each
 * specialist readable at a glance; team colour drives the palette.
 *
 * Public surface:
 *   SPRITE_W / SPRITE_H                 nominal grid in pixel units
 *   paletteFor(color)                   palette lookup with fallback
 *   normalizeRole(roleOrName)           tolerant role matcher
 *   drawAgent(ctx, role, palette, x, y, frame, scale, opts)
 *   drawMascot(ctx, x, y, frame, scale, opts)
 *   disableSmoothing(ctx)
 *
 * drawAgent anchors at the sprite's feet centre: (x, y) is the point between
 * the boots. `frame` is a monotonically increasing integer tick; it drives
 * walking legs, blinks and blinking gear. `opts`:
 *   walking  boolean  scissor the legs for a walk cycle
 *   bob      number   vertical offset in logical pixels (idle bob)
 *   alpha    number   0..1 fade
 *   flip     boolean  mirror the sprite around its centre
 * The return value is the drawn bounding box in logical pixels.
 */

export const SPRITE_W = 18;
export const SPRITE_H = 34;

const INK = "#0b1220";
const WHITE = "#e8eefc";
const SKIN = "#e8b892";
const SKIN_DARK = "#c18d66";
const GOLD = "#fbbf24";
const METAL = "#a9bbd9";
const METAL_DARK = "#65789c";
const GLASS = "#bfe9ff";
const HAIR = "#2b1d16";
const COAT = "#1a2338";

const TEAM_COLORS = Object.freeze([
  "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan",
]);

/* ---- colour helpers ---------------------------------------------------- */

function hexToRgb(hex) {
  const raw = String(hex || "").replace("#", "");
  if (raw.length === 3) {
    return [
      parseInt(raw[0] + raw[0], 16),
      parseInt(raw[1] + raw[1], 16),
      parseInt(raw[2] + raw[2], 16),
    ];
  }
  if (raw.length !== 6 || !/^[0-9a-f]{6}$/i.test(raw)) return [143, 160, 196];
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

function mix(a, b, t) {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  const k = Math.max(0, Math.min(1, t));
  return rgbToHex(
    ca[0] + (cb[0] - ca[0]) * k,
    ca[1] + (cb[1] - ca[1]) * k,
    ca[2] + (cb[2] - ca[2]) * k,
  );
}

export function rgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  const a = Math.max(0, Math.min(1, Number(alpha) || 0));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

function lighten(hex, t) { return mix(hex, "#ffffff", t); }
function darken(hex, t) { return mix(hex, "#000000", t); }

/* ---- team palettes ----------------------------------------------------- */

function makePalette(name, base) {
  return Object.freeze({
    name,
    base,
    light: lighten(base, 0.36),
    pale: lighten(base, 0.62),
    dark: darken(base, 0.34),
    deep: darken(base, 0.58),
    glow: lighten(base, 0.14),
    trim: WHITE,
    metal: METAL,
    metalDark: METAL_DARK,
  });
}

const PALETTES = Object.freeze({
  red: makePalette("red", "#fb7185"),
  blue: makePalette("blue", "#60a5fa"),
  green: makePalette("green", "#4ade80"),
  yellow: makePalette("yellow", "#fbbf24"),
  purple: makePalette("purple", "#a78bfa"),
  orange: makePalette("orange", "#fb923c"),
  pink: makePalette("pink", "#f472b6"),
  cyan: makePalette("cyan", "#22d3ee"),
  slate: makePalette("slate", "#8fa0c4"),
});

const COLOR_ALIASES = {
  gray: "slate",
  grey: "slate",
  silver: "slate",
  white: "slate",
  gold: "yellow",
  amber: "orange",
  teal: "cyan",
  aqua: "cyan",
  turquoise: "cyan",
  magenta: "pink",
  rose: "red",
  violet: "purple",
  indigo: "blue",
  navy: "blue",
  lime: "green",
  mint: "green",
  coral: "red",
  peach: "orange",
};

/** Resolve a team colour (or palette object) to a palette, never null. */
export function paletteFor(color) {
  if (color && typeof color === "object" && color.base && color.light) return color;
  const raw = String(color || "").trim().toLowerCase();
  const key = raw.replace(/[^a-z0-9]/g, "");
  if (PALETTES[key]) return PALETTES[key];
  const alias = COLOR_ALIASES[key];
  if (alias && PALETTES[alias]) return PALETTES[alias];
  return PALETTES.slate;
}

/** Deterministic fallback colour for agents that carry no team colour. */
export function colorForSeed(seed) {
  const text = String(seed || "agent");
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) % 997;
  }
  return TEAM_COLORS[hash % TEAM_COLORS.length];
}

/* ---- role normalisation ------------------------------------------------ */

const ROLE_PATTERNS = [
  ["lead", /team[-_ ]?lead|orchestrat|^lead$|\blead\b/],
  ["preparer", /prepar/],
  ["architect", /architect/],
  ["database-engineer", /database|db[-_ ]?(?:eng|coder|admin)|data[-_ ]?engineer/],
  ["backend-coder", /back[-_ ]?end|backend|server[-_ ]?coder/],
  ["frontend-coder", /front[-_ ]?end|frontend|ui[-_ ]?coder/],
  ["devops-engineer", /devops|sre\b|infra(?:structure)?|platform[-_ ]?engineer/],
  ["auditor", /audit/],
  ["qa-engineer", /\bqa\b|quality[-_ ]?(?:engineer|assurance)/],
  /* Word-anchored, so "latest-bot" is not a tester; pytest still is. */
  ["test-engineer", /\btest|pytest/],
  ["security-engineer", /security|secops|appsec/],
  ["secretary", /secretar|scribe|chro?nicler/],
  ["n8n", /n8n|automation|workflow[-_ ]?bot/],
];

/** Map a role, agent name, or agent_type onto a canonical role id. */
export function normalizeRole(roleOrName) {
  const text = String(roleOrName || "").trim().toLowerCase();
  if (!text) return "unknown";
  if (text === "n8n") return "n8n";
  for (const [role, pattern] of ROLE_PATTERNS) {
    if (pattern.test(text)) return role;
  }
  return "unknown";
}

/* ---- pixel pen --------------------------------------------------------- */

/**
 * Build a draw function on the sprite grid. Coordinates are snapped to whole
 * logical pixels so nested rectangles share edges: no seams, no soft corners.
 */
function makePen(ctx, left, top, u, flip) {
  return function pen(col, row, w, h, color) {
    if (!color || w <= 0 || h <= 0) return;
    const c = flip ? SPRITE_W - col - w : col;
    const x0 = left + Math.round(c * u);
    const y0 = top + Math.round(row * u);
    const x1 = left + Math.round((c + w) * u);
    const y1 = top + Math.round((row + h) * u);
    if (x1 <= x0 || y1 <= y0) return;
    ctx.fillStyle = color;
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
  };
}

/** Turn off interpolation for a pixel-art context (canvas 2d). */
export function disableSmoothing(ctx) {
  if (!ctx) return;
  ctx.imageSmoothingEnabled = false;
  if ("webkitImageSmoothingEnabled" in ctx) ctx.webkitImageSmoothingEnabled = false;
}

/* ---- role styling ------------------------------------------------------ */

const ROLE_STYLE = {
  lead: {
    headgear: "crown",
    held: "baton",
    cape: true,
    shoulderPads: true,
    belt: false,
  },
  preparer: { headgear: "cap", held: "book", satchel: true },
  architect: { headgear: "hardhat", held: "scroll", broad: true, chest: "blueprint" },
  "backend-coder": { headgear: "headset", held: "keyboard", chest: "code" },
  "frontend-coder": { headgear: "beret", held: "brush", chest: "browser" },
  "database-engineer": { headgear: "visor", backpack: "db", stocky: true },
  "devops-engineer": { headgear: "cap", held: "wrench", belt: true },
  auditor: { headgear: "hat", held: "clipboard", coat: true, monocle: true },
  "test-engineer": { headgear: "goggles", held: "tube", belt: true },
  "qa-engineer": { headgear: "headband", held: "net", chest: "check" },
  "security-engineer": {
    headgear: "helmet",
    held: "shield",
    broad: true,
    shoulderPads: true,
  },
  secretary: { headgear: "headset", held: "notepad", hairBun: true, skirt: true, tie: true },
  unknown: { headgear: "antenna", hood: true },
};

/* ---- shared humanoid body --------------------------------------------- */

function drawHumanoid(pen, pal, frame, opts, style) {
  const walking = Boolean(opts.walking);
  const step = walking ? (frame >> 2) & 3 : 0;
  const blink = frame % 97 < 3;
  const lOff = walking && (step === 1 || step === 2) ? 1 : 0;
  const rOff = walking && (step === 0 || step === 3) ? 1 : 0;
  const broad = Boolean(style.broad);
  const bodyL = broad ? 3 : 4;
  const bodyW = broad ? 12 : 10;
  const armL = broad ? 1 : 2;
  const armR = broad ? 16 : 15;

  /* shadow */
  pen(3, 32, 12, 2, "rgba(0, 0, 0, 0.32)");

  /* gear carried on the back, drawn behind the body */
  if (style.cape) {
    pen(3, 12, 2, 19, pal.deep);
    pen(13, 12, 2, 19, pal.deep);
    pen(3, 12, 12, 2, pal.dark);
  }
  if (style.backpack === "db") {
    pen(0, 13, 4, 4, pal.base);
    pen(0, 13, 4, 1, pal.light);
    pen(0, 18, 4, 4, pal.dark);
    pen(0, 18, 4, 1, pal.base);
    pen(0, 23, 4, 3, pal.deep);
    pen(4, 20, 1, 1, pal.light);
    pen(4, 25, 1, 1, pal.light);
  }

  /* legs */
  pen(5, 24, 3, 8 - lOff, pal.deep);
  pen(5, 32 - lOff, 3, 2, COAT);
  pen(10, 24, 3, 8 - rOff, pal.deep);
  pen(10, 32 - rOff, 3, 2, COAT);

  /* arms behind the torso */
  pen(armL, 14, 2, 8, pal.dark);
  pen(armL, 22, 2, 2, SKIN);
  pen(armR, 14, 2, 8, pal.dark);
  pen(armR, 22, 2, 2, SKIN);

  /* torso: team-coloured jacket or a long coat */
  if (style.coat) {
    pen(2, 13, 14, 16, pal.deep);
    pen(3, 13, 12, 2, pal.dark);
    pen(8, 15, 2, 14, pal.dark);
    pen(9, 18, 1, 1, GOLD);
    pen(9, 21, 1, 1, GOLD);
    pen(9, 24, 1, 1, GOLD);
    pen(2, 13, 1, 16, pal.deep);
    pen(15, 13, 1, 16, pal.deep);
    pen(4, 24, 3, 3, COAT);
    pen(11, 24, 3, 3, COAT);
    pen(5, 27, 3, 2, COAT);
    pen(10, 27, 3, 2, COAT);
  } else if (style.skirt) {
    pen(bodyL, 13, bodyW, 9, pal.base);
    pen(bodyL, 13, bodyW, 2, pal.light);
    pen(bodyL, 13, 1, 9, pal.dark);
    pen(bodyL + bodyW - 1, 13, 1, 9, pal.dark);
    pen(3, 22, 12, 2, pal.dark);
    pen(4, 24, 10, 4, pal.dark);
    pen(5, 28, 8, 1, pal.deep);
    pen(6, 29, 2, 4, SKIN_DARK);
    pen(6, 33, 2, 1, COAT);
    pen(10, 29, 2, 4, SKIN_DARK);
    pen(10, 33, 2, 1, COAT);
  } else if (style.stocky) {
    pen(bodyL, 14, bodyW, 10, pal.base);
    pen(bodyL, 14, bodyW, 2, pal.light);
    pen(bodyL, 22, bodyW, 2, pal.dark);
    pen(bodyL, 14, 1, 10, pal.dark);
    pen(bodyL + bodyW - 1, 14, 1, 10, pal.dark);
    pen(5, 26, 3, 5, pal.deep);
    pen(5, 31, 3, 2, COAT);
    pen(10, 26, 3, 5, pal.deep);
    pen(10, 31, 3, 2, COAT);
  } else {
    pen(bodyL, 13, bodyW, 11, pal.base);
    pen(bodyL, 13, bodyW, 2, pal.light);
    pen(bodyL, 13, 1, 11, pal.dark);
    pen(bodyL + bodyW - 1, 13, 1, 11, pal.dark);
    pen(bodyL, 22, bodyW, 2, pal.dark);
  }

  /* neck + head */
  pen(8, 12, 2, 2, SKIN_DARK);
  const headTop = style.stocky ? 6 : 5;
  pen(5, headTop, 8, 8, SKIN);
  pen(5, headTop, 8, 1, lighten(SKIN, 0.2));
  pen(5, headTop + 6, 8, 2, SKIN_DARK);
  pen(4, headTop + 3, 1, 3, SKIN);
  pen(13, headTop + 3, 1, 3, SKIN);
  if (style.hairBun) {
    pen(12, headTop - 1, 3, 3, HAIR);
    pen(4, headTop - 1, 10, 2, HAIR);
  }
  if (style.hood) {
    pen(4, headTop - 2, 10, 3, "#233254");
    pen(3, headTop, 1, 5, "#233254");
    pen(14, headTop, 1, 5, "#233254");
  }

  /* eyes */
  const eyeRow = headTop + 3;
  if (blink) {
    pen(6, eyeRow, 2, 1, INK);
    pen(10, eyeRow, 2, 1, INK);
  } else {
    pen(6, eyeRow, 2, 2, INK);
    pen(10, eyeRow, 2, 2, INK);
    pen(6, eyeRow, 1, 1, pal.base);
    pen(10, eyeRow, 1, 1, pal.base);
  }
  pen(8, headTop + 6, 2, 1, SKIN_DARK);

  /* chest badge / screen */
  if (style.chest === "code") {
    pen(8, 16, 5, 4, INK);
    pen(9, 17, 2, 1, pal.light);
    pen(9, 18, 3, 1, pal.base);
    pen(9, 19, 1, 1, METAL);
  } else if (style.chest === "browser") {
    pen(8, 16, 5, 4, INK);
    pen(8, 16, 5, 1, pal.light);
    pen(9, 18, 1, 1, pal.base);
    pen(11, 18, 1, 1, GOLD);
  } else if (style.chest === "blueprint") {
    pen(8, 16, 5, 4, "#16304f");
    pen(9, 17, 3, 1, GLASS);
    pen(9, 19, 2, 1, GLASS);
  } else if (style.chest === "check") {
    pen(8, 16, 4, 4, "#14311f");
    pen(9, 18, 1, 1, "#4ade80");
    pen(10, 17, 1, 1, "#4ade80");
    pen(11, 16, 1, 1, "#4ade80");
  }

  /* belt, tie, satchel */
  if (style.tie) {
    pen(9, 15, 1, 1, GOLD);
    pen(9, 16, 1, 5, pal.dark);
  }
  if (style.belt) {
    pen(4, 22, 10, 2, COAT);
    pen(9, 22, 2, 2, pal.light);
    pen(5, 22, 2, 2, pal.dark);
    pen(11, 22, 2, 2, pal.dark);
  }
  if (style.satchel) {
    pen(7, 15, 2, 1, pal.dark);
    pen(9, 17, 2, 1, pal.dark);
    pen(11, 19, 2, 1, pal.dark);
    pen(12, 21, 3, 4, pal.dark);
    pen(12, 21, 3, 1, pal.base);
    pen(13, 23, 1, 1, GOLD);
  }
  if (style.shoulderPads) {
    pen(2, 12, 3, 2, pal.light);
    pen(13, 12, 3, 2, pal.light);
  }

  drawHeadgear(pen, pal, frame, style);
  drawHeldItem(pen, pal, frame, style);
}

function drawHeadgear(pen, pal, frame, style) {
  switch (style.headgear) {
    case "crown":
      pen(5, 2, 2, 2, GOLD);
      pen(8, 1, 2, 3, GOLD);
      pen(11, 2, 2, 2, GOLD);
      pen(5, 4, 8, 2, GOLD);
      pen(5, 4, 8, 1, lighten(GOLD, 0.35));
      break;
    case "hardhat":
      pen(5, 3, 8, 3, GOLD);
      pen(5, 3, 8, 1, lighten(GOLD, 0.35));
      pen(4, 6, 10, 2, darken(GOLD, 0.3));
      pen(8, 4, 2, 1, WHITE);
      break;
    case "cap":
      pen(5, 3, 8, 3, pal.base);
      pen(5, 3, 8, 1, pal.light);
      pen(4, 6, 10, 1, pal.deep);
      pen(8, 2, 2, 1, pal.light);
      break;
    case "beret":
      pen(5, 2, 9, 3, pal.base);
      pen(5, 2, 9, 1, pal.light);
      pen(9, 5, 6, 1, pal.dark);
      pen(12, 1, 1, 1, pal.pale);
      break;
    case "visor":
      pen(4, 8, 10, 2, INK);
      pen(6, 8, 2, 1, GLASS);
      pen(10, 8, 2, 1, GLASS);
      pen(5, 6, 8, 1, pal.dark);
      break;
    case "hat":
      pen(4, 5, 10, 2, COAT);
      pen(6, 1, 6, 4, COAT);
      pen(6, 4, 6, 1, pal.base);
      break;
    case "goggles":
      pen(4, 6, 4, 4, METAL_DARK);
      pen(5, 7, 2, 2, GLASS);
      pen(10, 6, 4, 4, METAL_DARK);
      pen(11, 7, 2, 2, GLASS);
      pen(8, 7, 2, 1, METAL_DARK);
      pen(4, 5, 10, 1, pal.dark);
      break;
    case "headset":
      pen(5, 2, 8, 2, COAT);
      pen(3, 7, 2, 4, pal.dark);
      pen(13, 7, 2, 4, pal.dark);
      pen(13, 10, 2, 1, COAT);
      pen(14, 11, 1, 2, pal.light);
      break;
    case "headband":
      pen(4, 5, 10, 2, pal.base);
      pen(4, 5, 10, 1, pal.light);
      pen(8, 4, 2, 1, pal.pale);
      break;
    case "helmet":
      pen(4, 3, 10, 5, pal.dark);
      pen(4, 3, 10, 1, pal.light);
      pen(4, 8, 10, 3, pal.deep);
      pen(5, 9, 8, 1, pal.light);
      pen(3, 6, 1, 4, pal.dark);
      pen(14, 6, 1, 4, pal.dark);
      break;
    case "antenna": {
      const lit = frame % 41 < 20;
      pen(9, 1, 1, 3, METAL);
      pen(8, 0, 2, 1, lit ? pal.light : pal.dark);
      break;
    }
    default:
      break;
  }
  if (style.monocle) {
    pen(10, 7, 4, 1, GOLD);
    pen(10, 10, 4, 1, GOLD);
    pen(10, 8, 1, 2, GOLD);
    pen(13, 8, 1, 2, GOLD);
    pen(14, 11, 1, 2, METAL);
  }
}

function drawHeldItem(pen, pal, frame, style) {
  switch (style.held) {
    case "baton":
      pen(15, 14, 1, 9, pal.light);
      pen(14, 12, 2, 2, GOLD);
      break;
    case "book":
      pen(13, 19, 4, 6, WHITE);
      pen(13, 19, 4, 1, pal.dark);
      pen(14, 21, 2, 1, METAL);
      pen(14, 23, 2, 1, METAL);
      break;
    case "scroll":
      pen(13, 18, 4, 7, "#f4e6c8");
      pen(12, 17, 6, 1, GOLD);
      pen(12, 25, 6, 1, GOLD);
      pen(14, 20, 2, 1, "#b08968");
      pen(14, 22, 2, 1, "#b08968");
      break;
    case "keyboard":
      pen(13, 21, 5, 3, COAT);
      pen(14, 21, 1, 1, pal.light);
      pen(16, 22, 1, 1, pal.light);
      break;
    case "brush":
      pen(15, 18, 1, 7, pal.dark);
      pen(14, 25, 3, 2, pal.light);
      pen(14, 27, 3, 1, pal.base);
      break;
    case "wrench":
      pen(15, 18, 1, 8, METAL);
      pen(14, 16, 3, 2, METAL);
      pen(14, 20, 3, 1, METAL);
      break;
    case "clipboard":
      pen(13, 18, 4, 8, WHITE);
      pen(14, 17, 2, 1, METAL_DARK);
      pen(14, 20, 2, 1, METAL_DARK);
      pen(14, 22, 2, 1, METAL_DARK);
      pen(14, 24, 2, 1, METAL_DARK);
      break;
    case "tube":
      pen(15, 18, 2, 7, GLASS);
      pen(15, 22, 2, 3, pal.base);
      pen(14, 17, 3, 1, METAL_DARK);
      break;
    case "net":
      pen(15, 17, 1, 9, METAL);
      pen(13, 13, 4, 4, rgba(GLASS, 0.8));
      pen(14, 13, 1, 4, METAL_DARK);
      pen(13, 15, 4, 1, METAL_DARK);
      break;
    case "notepad":
      pen(13, 19, 4, 6, WHITE);
      pen(14, 21, 2, 1, METAL_DARK);
      pen(14, 23, 2, 1, METAL_DARK);
      pen(12, 16, 1, 6, pal.light);
      break;
    case "shield":
      pen(0, 14, 4, 10, pal.dark);
      pen(0, 14, 4, 1, pal.light);
      pen(1, 24, 2, 2, pal.dark);
      pen(1, 17, 2, 2, GOLD);
      pen(2, 20, 2, 1, pal.base);
      break;
    default:
      break;
  }
}

/* ---- the n8n automation robot ----------------------------------------- */

function drawN8n(pen, pal, frame, opts) {
  const walking = Boolean(opts.walking);
  const step = walking ? (frame >> 2) & 3 : 0;
  const blink = frame % 73 < 3;
  pen(3, 32, 12, 2, "rgba(0, 0, 0, 0.32)");
  /* legs */
  pen(6, 28, 2, 4, METAL_DARK);
  pen(5, 31, 3, 2, METAL);
  pen(10, 28, 2, 4, METAL_DARK);
  pen(10, 31, 3, 2, METAL);
  /* body */
  pen(3, 12, 12, 16, pal.dark);
  pen(3, 12, 12, 1, pal.light);
  pen(3, 12, 1, 16, pal.base);
  pen(14, 12, 1, 16, pal.base);
  pen(4, 13, 10, 2, pal.base);
  /* screen face */
  pen(5, 16, 8, 7, INK);
  if (blink) {
    pen(6, 19, 2, 1, pal.light);
    pen(10, 19, 2, 1, pal.light);
  } else {
    pen(6, 18, 2, 2, pal.light);
    pen(10, 18, 2, 2, pal.light);
    pen(6, 18, 1, 1, WHITE);
    pen(10, 18, 1, 1, WHITE);
  }
  pen(7, 22, 4, 1, pal.base);
  /* arms + claws */
  pen(1, 15, 2, 8, METAL_DARK);
  pen(15, 15, 2, 8, METAL_DARK);
  pen(1, 23, 3, 2, pal.base);
  pen(14, 23, 3, 2, pal.base);
  /* circuit inlays */
  pen(4, 25, 5, 1, pal.base);
  pen(10, 26, 4, 1, pal.light);
  pen(5, 24, 1, 1, pal.light);
  pen(13, 25, 1, 1, pal.light);
  /* antenna */
  pen(8, 4, 2, 3, METAL);
  pen(7, 1, 4, 3, pal.base);
  const spark = frame % 51 < 25;
  pen(8, 2, 2, 1, spark ? WHITE : pal.light);
  /* hover jets */
  if (walking) {
    const flick = step % 2 === 0;
    pen(6, 30, 2, 2, flick ? GOLD : pal.light);
    pen(10, 30, 2, 2, flick ? pal.light : GOLD);
  }
}

/* ---- public agent draw ------------------------------------------------- */

/**
 * Draw one crew member. See the module header for the parameter contract.
 * Returns the bounding box in logical pixels.
 */
export function drawAgent(ctx, role, palette, x, y, frame, scale, opts = {}) {
  const canonical = normalizeRole(role);
  const pal = palette && typeof palette === "object" && palette.base
    ? palette
    : paletteFor(palette);
  const u = Math.max(1, Number(scale) || 1);
  const f = Number.isFinite(frame) ? Math.floor(frame) : 0;
  const bob = Number(opts.bob) || 0;
  const alpha = opts.alpha === undefined ? 1 : Math.max(0, Math.min(1, Number(opts.alpha)));
  const width = SPRITE_W * u;
  const height = SPRITE_H * u;
  const left = Math.round(x - width / 2);
  const top = Math.round(y - height + bob);
  if (alpha <= 0.02) return { x: left, y: top, w: width, h: height };
  ctx.save();
  if (alpha < 1) ctx.globalAlpha = alpha;
  const pen = makePen(ctx, left, top, u, Boolean(opts.flip));
  if (canonical === "n8n") {
    drawN8n(pen, pal, f, opts);
  } else {
    const style = ROLE_STYLE[canonical] || ROLE_STYLE.unknown;
    drawHumanoid(pen, pal, f, opts, style);
  }
  ctx.restore();
  return { x: left, y: top, w: width, h: height };
}

/* ---- mascot ------------------------------------------------------------ */

/**
 * Draw the little floor mascot, a floating one-eyed drone. Feet anchor at
 * (x, y); grid is 16 x 18 pixel units. opts: {hover, alpha, mood}.
 */
export function drawMascot(ctx, x, y, frame, scale, opts = {}) {
  const u = Math.max(1, Number(scale) || 1);
  const f = Number.isFinite(frame) ? Math.floor(frame) : 0;
  const width = 16 * u;
  const height = 18 * u;
  const left = Math.round(x - width / 2);
  const top = Math.round(y - height);
  const alpha = opts.alpha === undefined ? 1 : Math.max(0, Math.min(1, Number(opts.alpha)));
  if (alpha <= 0.02) return;
  ctx.save();
  if (alpha < 1) ctx.globalAlpha = alpha;
  const pen = makePen(ctx, left, top, u, false);
  /* shadow */
  pen(3, 16, 10, 2, "rgba(0, 0, 0, 0.3)");
  /* fins */
  pen(2, 9, 2, 5, "#33456b");
  pen(12, 9, 2, 5, "#33456b");
  pen(2, 12, 2, 1, "#a3e635");
  pen(12, 12, 2, 1, "#a3e635");
  /* chassis */
  pen(4, 7, 8, 9, "#2a3b5c");
  pen(4, 7, 8, 1, "#3d5378");
  pen(4, 7, 1, 9, "#3d5378");
  pen(11, 7, 1, 9, "#1b2740");
  /* visor */
  pen(5, 9, 6, 5, "#0b1220");
  const blink = f % 83 < 4;
  if (blink) {
    pen(6, 12, 4, 1, "#a3e635");
  } else {
    const eyeX = opts.hover ? 1 : 0;
    pen(6 + eyeX, 11, 1, 3, "#a3e635");
    pen(9 + eyeX, 11, 1, 3, "#a3e635");
    pen(6 + eyeX, 11, 1, 1, "#d9f99d");
  }
  /* lights */
  pen(6, 8, 1, 1, "#f472b6");
  pen(9, 8, 1, 1, "#22d3ee");
  /* antenna */
  pen(7, 4, 2, 3, "#65789c");
  pen(6, 2, 4, 2, "#a3e635");
  pen(7, 1, 1, 1, f % 37 < 18 ? "#e8eefc" : "#a3e635");
  /* thruster flame */
  const flick = (f >> 2) % 3;
  pen(6, 16, 4, 1, "#fbbf24");
  pen(7, 17, 2, 1, flick === 0 ? "#fb7185" : "#f97316");
  ctx.restore();
}
