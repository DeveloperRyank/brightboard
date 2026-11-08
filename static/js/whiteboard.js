// Animated whiteboard engine.
//
// Two kinds of visuals:
//   1. TEMPLATES — whole diagrams with FIXED, pre-verified geometry (apparatus,
//      molecules, energy profiles, graphs). The AI only picks a template and
//      fills label slots; it never invents coordinates, so diagrams can't
//      collide or drift off-canvas.
//   2. Freeform glyphs/primitives — still supported, but every placement is
//      validated (bounds + overlap) before it renders; invalid ones are skipped.
//
// Everything is drawn progressively, like a teacher's chalk, stroke by stroke.

const BOARD_W = 1000;
const BOARD_H = 620;
const HIGHLIGHT_COLOR = "#fbbf24";
const CHALK = "#eef2f7";
const DRAW_SPEED = 430; // board units of stroke length per second

function rad(deg) { return (deg * Math.PI) / 180; }

function arcPts(cx, cy, r, a0, a1, n = 28) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const a = a0 + ((a1 - a0) * i) / n;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

function ellipsePts(cx, cy, rx, ry, rot, n = 36) {
  const pts = [];
  const c = Math.cos(rot), s = Math.sin(rot);
  for (let i = 0; i <= n; i++) {
    const a = (2 * Math.PI * i) / n;
    const x = rx * Math.cos(a), y = ry * Math.sin(a);
    pts.push([cx + x * c - y * s, cy + x * s + y * c]);
  }
  return pts;
}

// --- glyph library: strokes in a 100x100 local box (y down) ---
const GLYPHS = {
  round_flask: [
    { pts: [[38, 6], [62, 6]] },
    { pts: [[42, 6], [42, 36]] },
    { pts: [[58, 6], [58, 36]] },
    { pts: arcPts(50, 64, 30, rad(-74), rad(254)) },
  ],
  beaker: [
    { pts: [[22, 14], [22, 86], [78, 86], [78, 14]] },
    { pts: [[22, 14], [14, 8]] },
    { pts: [[22, 42], [34, 42]] },
    { pts: [[22, 62], [34, 62]] },
  ],
  test_tube: [
    { pts: [[40, 8], [40, 72]] },
    { pts: arcPts(50, 72, 10, rad(180), rad(0)) },
    { pts: [[60, 72], [60, 8]] },
    { pts: [[36, 8], [64, 8]] },
  ],
  thermometer: [
    { pts: [[45, 8], [45, 78]] },
    { pts: [[55, 8], [55, 78]] },
    { pts: [[45, 8], [55, 8]] },
    { pts: arcPts(50, 84, 8, 0, rad(360)) },
    { pts: [[50, 80], [50, 38]], color: "#fca5a5", width: 4 },
    { pts: [[55, 24], [62, 24]] },
    { pts: [[55, 40], [62, 40]] },
    { pts: [[55, 56], [62, 56]] },
  ],
  condenser: [
    { pts: [[8, 46], [92, 46]] },
    { pts: [[8, 54], [92, 54]] },
    { pts: [[28, 32], [72, 32], [72, 68], [28, 68], [28, 32]] },
    { pts: [[34, 32], [34, 18]] },
    { pts: [[66, 68], [66, 82]] },
  ],
  burner: [
    { pts: [[30, 90], [70, 90], [64, 80], [36, 80], [30, 90]] },
    { pts: [[45, 80], [45, 42]] },
    { pts: [[55, 80], [55, 42]] },
    { pts: [[43, 62], [57, 62]] },
    { pts: [[50, 40], [43, 28], [50, 8], [57, 28], [50, 40]], color: "#fde68a" },
  ],
  funnel: [
    { pts: [[16, 10], [50, 52], [84, 10]] },
    { pts: [[50, 52], [50, 88]] },
  ],
  filter_paper: [
    { pts: [[26, 16], [50, 46], [74, 16]], width: 2 },
  ],
  evaporating_dish: [
    { pts: arcPts(50, 38, 36, rad(180), rad(0)) },
    { pts: [[14, 38], [86, 38]] },
  ],
  electrode: [
    { pts: [[50, 6], [50, 70]], width: 5 },
    { pts: [[50, 6], [68, 6]] },
  ],
  atom: [
    { pts: arcPts(50, 50, 7, 0, rad(360)), color: "#fde68a" },
    { pts: ellipsePts(50, 50, 40, 14, 0) },
    { pts: ellipsePts(50, 50, 40, 14, rad(60)) },
    { pts: ellipsePts(50, 50, 40, 14, rad(120)) },
  ],
  graph_axes: [
    { pts: [[18, 10], [18, 86]] },
    { pts: [[18, 86], [92, 86]] },
    { pts: [[18, 10], [12, 20]] },
    { pts: [[18, 10], [24, 20]] },
    { pts: [[92, 86], [82, 80]] },
    { pts: [[92, 86], [82, 92]] },
  ],
  bulb: [
    { pts: arcPts(50, 38, 20, 0, rad(360)) },
    { pts: [[42, 56], [42, 70], [58, 70], [58, 56]] },
    { pts: [[20, 20], [12, 12]], color: "#fde68a" },
    { pts: [[80, 20], [88, 12]], color: "#fde68a" },
    { pts: [[50, 12], [50, 2]], color: "#fde68a" },
  ],
  burette: [
    { pts: [[44, 6], [44, 78]] },
    { pts: [[56, 6], [56, 78]] },
    { pts: [[44, 6], [56, 6]] },
    { pts: [[36, 62], [64, 62]] },
    { pts: [[50, 62], [50, 70]] },
    { pts: [[44, 78], [50, 92], [56, 78]] },
    { pts: [[44, 22], [51, 22]] },
    { pts: [[44, 38], [51, 38]] },
    { pts: [[44, 54], [51, 54]] },
  ],
  conical_flask: [
    { pts: [[40, 8], [60, 8]] },
    { pts: [[44, 8], [44, 34]] },
    { pts: [[56, 8], [56, 34]] },
    { pts: [[44, 34], [20, 86], [80, 86], [56, 34]] },
    { pts: [[31, 66], [69, 66]], color: "#7dd3fc", width: 2 },
  ],
  speech_bubble: [
    { pts: [[20, 15], [80, 15], [85, 20], [85, 55], [80, 60], [45, 60], [28, 80], [32, 60], [20, 60], [15, 55], [15, 20], [20, 15]] },
  ],
  mic: [
    { pts: arcPts(50, 26, 12, 0, rad(360)) },
    { pts: [[50, 38], [50, 62]] },
    { pts: arcPts(50, 34, 26, rad(160), rad(20)) },
    { pts: [[50, 60], [50, 84]] },
    { pts: [[32, 86], [68, 86]] },
  ],
};

// --- template library: FIXED, pre-verified geometry in board coordinates ---
// Each op is one of:
//   { g:'glyphName', x, y, size, color? }        apparatus placed by box corner
//   { p:[[x,y],...], color?, width? }            a fixed polyline
//   { arc:{cx,cy,r,a0,a1,n?}, color?, width? }   arc, angles in degrees
//   { slot:'name', x, y, size?, color?, def? }   a label the AI may fill
//   { text:'str', x, y, size?, color? }          a fixed label
// templates may define variants: { name: [extraOps] } chosen by action.variant.
const TEMPLATES = {
  distillation: {
    ops: [
      { g: "burner", x: 150, y: 430, size: 140 },
      { g: "round_flask", x: 140, y: 250, size: 200 },
      { g: "thermometer", x: 300, y: 120, size: 180 },
      { g: "condenser", x: 360, y: 250, size: 320 },
      { g: "beaker", x: 740, y: 360, size: 150 },
      { slot: "mixture", x: 120, y: 235, size: 17, def: "Mixture" },
      { slot: "thermometer", x: 300, y: 100, size: 15, def: "Thermometer" },
      { slot: "condenser", x: 430, y: 232, size: 15, def: "Condenser" },
      { slot: "distillate", x: 725, y: 530, size: 15, def: "Distillate" },
    ],
  },
  titration: {
    ops: [
      { g: "burette", x: 460, y: 30, size: 300 },
      { g: "conical_flask", x: 470, y: 350, size: 190 },
      { slot: "titrant", x: 690, y: 120, size: 17, def: "Titrant (in burette)" },
      { slot: "analyte", x: 660, y: 430, size: 16, def: "Analyte + indicator" },
      { slot: "endpoint", x: 660, y: 470, size: 15, def: "End point: colour change" },
    ],
  },
  filtration: {
    ops: [
      { g: "funnel", x: 420, y: 110, size: 230 },
      { g: "filter_paper", x: 470, y: 150, size: 140 },
      { g: "conical_flask", x: 430, y: 310, size: 210 },
      { slot: "residue", x: 655, y: 170, size: 16, def: "Residue (on paper)" },
      { slot: "filtrate", x: 655, y: 420, size: 16, def: "Filtrate" },
    ],
  },
  methane: {
    ops: [
      { p: [[430, 300], [430, 205]], width: 3 },
      { p: [[430, 300], [338, 368]], width: 3 },
      { p: [[430, 300], [522, 368]], width: 3 },
      { p: [[430, 300], [410, 428]], width: 3 },
      { p: [[430, 300], [450, 428]], width: 3 },
      { p: [[410, 428], [450, 428]], width: 2 },
      { arc: { cx: 430, cy: 300, r: 20 }, color: "#fde68a", width: 3 },
      { arc: { cx: 430, cy: 190, r: 18 }, width: 3 },
      { arc: { cx: 330, cy: 375, r: 18 }, width: 3 },
      { arc: { cx: 530, cy: 375, r: 18 }, width: 3 },
      { arc: { cx: 430, cy: 445, r: 18 }, width: 3 },
      { text: "C", x: 423, y: 300, size: 20, color: "#fde68a" },
      { text: "H", x: 423, y: 190, size: 18 },
      { text: "H", x: 323, y: 375, size: 18 },
      { text: "H", x: 523, y: 375, size: 18 },
      { text: "H", x: 423, y: 445, size: 18 },
      { slot: "name", x: 660, y: 300, size: 18, def: "Methane, CH\u2084" },
      { slot: "geometry", x: 660, y: 335, size: 15, def: "Tetrahedral, 109.5\u00b0" },
    ],
  },
  water: {
    ops: [
      { p: [[450, 300], [368, 368]], width: 3 },
      { p: [[450, 300], [532, 368]], width: 3 },
      { p: [[436, 272], [428, 254]], width: 2, color: "#c4b5fd" },
      { p: [[464, 272], [472, 254]], width: 2, color: "#c4b5fd" },
      { arc: { cx: 450, cy: 300, r: 22 }, color: "#fca5a5", width: 3 },
      { arc: { cx: 360, cy: 378, r: 18 }, width: 3 },
      { arc: { cx: 540, cy: 378, r: 18 }, width: 3 },
      { text: "O", x: 442, y: 300, size: 20, color: "#fca5a5" },
      { text: "H", x: 352, y: 378, size: 18 },
      { text: "H", x: 532, y: 378, size: 18 },
      { slot: "angle", x: 420, y: 348, size: 15, color: "#c4b5fd", def: "104.5\u00b0" },
      { slot: "name", x: 660, y: 300, size: 18, def: "Water, H\u2082O" },
      { slot: "geometry", x: 660, y: 335, size: 15, def: "Bent / V-shaped" },
    ],
  },
  energy_profile: {
    ops: [
      { p: [[150, 520], [150, 80]], width: 3 },
      { p: [[150, 80], [143, 96]], width: 3 },
      { p: [[150, 80], [157, 96]], width: 3 },
      { p: [[150, 520], [880, 520]], width: 3 },
      { p: [[880, 520], [863, 513]], width: 3 },
      { p: [[880, 520], [863, 527]], width: 3 },
      { text: "Energy", x: 96, y: 88, size: 15 },
      { text: "Reaction coordinate", x: 620, y: 548, size: 15 },
      { slot: "ea", x: 508, y: 225, size: 16, color: "#fde68a", def: "E\u2090" },
      { slot: "ts", x: 420, y: 122, size: 15, def: "Transition state" },
    ],
    variants: {
      exo: [
        { p: [[200, 300], [320, 300], [380, 272], [440, 205], [500, 152], [560, 205], [640, 330], [720, 410], [820, 410]], color: "#7dd3fc", width: 4 },
        { p: [[500, 300], [500, 152]], color: "#fde68a", width: 2 },
        { p: [[760, 300], [760, 410]], color: "#86efac", width: 2 },
        { p: [[760, 410], [753, 396]], color: "#86efac", width: 2 },
        { p: [[760, 410], [767, 396]], color: "#86efac", width: 2 },
        { slot: "reactants", x: 175, y: 285, size: 15, def: "Reactants" },
        { slot: "products", x: 745, y: 432, size: 15, def: "Products" },
        { slot: "dh", x: 772, y: 350, size: 16, color: "#86efac", def: "\u0394H (exothermic)" },
      ],
      endo: [
        { p: [[200, 410], [320, 410], [380, 360], [440, 250], [500, 152], [560, 230], [640, 285], [720, 300], [820, 300]], color: "#7dd3fc", width: 4 },
        { p: [[500, 410], [500, 152]], color: "#fde68a", width: 2 },
        { p: [[760, 410], [760, 300]], color: "#86efac", width: 2 },
        { p: [[760, 300], [753, 314]], color: "#86efac", width: 2 },
        { p: [[760, 300], [767, 314]], color: "#86efac", width: 2 },
        { slot: "reactants", x: 175, y: 432, size: 15, def: "Reactants" },
        { slot: "products", x: 745, y: 285, size: 15, def: "Products" },
        { slot: "dh", x: 772, y: 350, size: 16, color: "#86efac", def: "\u0394H (endothermic)" },
      ],
    },
  },
  graph_axes: {
    ops: [
      { p: [[150, 520], [150, 90]], width: 3 },
      { p: [[150, 90], [143, 106]], width: 3 },
      { p: [[150, 90], [157, 106]], width: 3 },
      { p: [[150, 520], [870, 520]], width: 3 },
      { p: [[870, 520], [853, 513]], width: 3 },
      { p: [[870, 520], [853, 527]], width: 3 },
      { p: [[150, 440], [140, 440]], width: 2 },
      { p: [[150, 360], [140, 360]], width: 2 },
      { p: [[150, 280], [140, 280]], width: 2 },
      { p: [[150, 200], [140, 200]], width: 2 },
      { p: [[310, 520], [310, 530]], width: 2 },
      { p: [[470, 520], [470, 530]], width: 2 },
      { p: [[630, 520], [630, 530]], width: 2 },
      { p: [[790, 520], [790, 530]], width: 2 },
      { slot: "ylabel", x: 92, y: 96, size: 15, def: "y" },
      { slot: "xlabel", x: 800, y: 548, size: 15, def: "x" },
      { slot: "title", x: 400, y: 60, size: 17, def: "" },
    ],
    variants: {
      line: [{ p: [[180, 470], [820, 150]], color: "#7dd3fc", width: 4 }],
      curve: [{ p: [[180, 480], [340, 340], [500, 255], [680, 205], [840, 185]], color: "#7dd3fc", width: 4 }],
      empty: [],
    },
  },
};

// --- validation for dynamically generated coordinates ---

function boxOf(x, y, w, h) { return { x1: x, y1: y, x2: x + w, y2: y + h }; }

function withinBounds(b, pad = 0) {
  return b.x1 >= -pad && b.y1 >= -pad && b.x2 <= BOARD_W + pad && b.y2 <= BOARD_H + pad;
}

function overlapArea(a, b) {
  const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
  const h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
  return w > 0 && h > 0 ? w * h : 0;
}

function area(b) { return Math.max(1, (b.x2 - b.x1) * (b.y2 - b.y1)); }

// Bounding box for a freeform draw action (glyph or primitive). Returns null if
// the action carries no explicit geometry we can bound.
function actionBox(a) {
  if (a.type === "draw" && a.glyph) {
    const size = a.size || 160;
    return boxOf(a.x, a.y, size, size);
  }
  if (a.type === "draw" && a.shape === "rect") return boxOf(a.x, a.y, a.w, a.h);
  if (a.type === "draw" && a.shape === "circle") return boxOf(a.cx - a.r, a.cy - a.r, 2 * a.r, 2 * a.r);
  if (a.type === "draw" && (a.shape === "line" || a.shape === "arrow")) {
    return boxOf(Math.min(a.x1, a.x2), Math.min(a.y1, a.y2),
      Math.abs(a.x2 - a.x1), Math.abs(a.y2 - a.y1));
  }
  if (a.type === "highlight") {
    return a.shape === "circle"
      ? boxOf(a.cx - a.r, a.cy - a.r, 2 * a.r, 2 * a.r)
      : boxOf(a.x, a.y, a.w, a.h);
  }
  return null;
}

// Validate one step's freeform actions against canvas bounds and against each
// other. Template actions are pre-verified and skipped. Returns a list of
// human-readable problems (empty = valid).
function validateActions(actions, overlapTolerance = 0.35) {
  const problems = [];
  const placed = [];
  for (const a of actions || []) {
    if (!a || a.type === "template" || a.type === "label") continue;
    const b = actionBox(a);
    if (!b) continue;
    if (!withinBounds(b)) {
      problems.push(`shape out of bounds: ${JSON.stringify(a).slice(0, 120)}`);
      continue;
    }
    for (const prev of placed) {
      const inter = overlapArea(b, prev.box);
      const frac = inter / Math.min(area(b), area(prev.box));
      if (frac > overlapTolerance) {
        problems.push(`shape overlaps a previous shape (${Math.round(frac * 100)}%): ${JSON.stringify(a).slice(0, 120)}`);
        break;
      }
    }
    placed.push({ box: b });
  }
  return problems;
}

// Defensive client-side guard: is this freeform draw fully off-canvas?
function renderableBox(a) {
  const b = actionBox(a);
  if (!b) return true; // no explicit geometry (template/label) — always render
  // skip only if it lies entirely outside the board
  return !(b.x2 < 0 || b.y2 < 0 || b.x1 > BOARD_W || b.y1 > BOARD_H);
}

function setupBoard(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth - 20;
  const cssHeight = Math.round((cssWidth * BOARD_H) / BOARD_W);
  canvas.width = cssWidth * dpr;
  canvas.height = cssHeight * dpr;
  // The box is sized by CSS (aspect-ratio on canvas#board), never from here: writing
  // style.height after the first paint is what made everything below the board jump.
  const ctx = canvas.getContext("2d");
  ctx.setTransform((cssWidth / BOARD_W) * dpr, 0, 0, (cssHeight / BOARD_H) * dpr, 0, 0);
  return ctx;
}

function strokeLen(pts) {
  let len = 0;
  for (let i = 1; i < pts.length; i++) {
    len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return len;
}

function clampDur(len) { return Math.min(1.4, Math.max(0.18, len / DRAW_SPEED)); }

function primitiveStrokes(a) {
  switch (a.shape) {
    case "line": return [{ pts: [[a.x1, a.y1], [a.x2, a.y2]], color: a.color }];
    case "arrow": {
      const angle = Math.atan2(a.y2 - a.y1, a.x2 - a.x1);
      const h = 14;
      return [
        { pts: [[a.x1, a.y1], [a.x2, a.y2]], color: a.color },
        { pts: [[a.x2, a.y2], [a.x2 - h * Math.cos(angle - 0.45), a.y2 - h * Math.sin(angle - 0.45)]], color: a.color },
        { pts: [[a.x2, a.y2], [a.x2 - h * Math.cos(angle + 0.45), a.y2 - h * Math.sin(angle + 0.45)]], color: a.color },
      ];
    }
    case "circle": return [{ pts: arcPts(a.cx, a.cy, a.r, 0, rad(360)), color: a.color }];
    case "rect": return [{ pts: [[a.x, a.y], [a.x + a.w, a.y], [a.x + a.w, a.y + a.h], [a.x, a.y + a.h], [a.x, a.y]], color: a.color }];
    case "path": return [{ pts: a.points || [], color: a.color }];
    default: return [];
  }
}

function pushStroke(items, pts, color, width, tRef) {
  if (!pts || pts.length < 2) return tRef.t;
  const dur = clampDur(strokeLen(pts));
  items.push({ kind: "stroke", pts, color: color || CHALK, width: width || 3, start: tRef.t, dur });
  tRef.t += dur;
  return tRef.t;
}

function pushLabel(items, text, x, y, size, color, tRef) {
  items.push({ kind: "label", a: { text, x, y, size: size || 17, color: color || "#cbd5e1" }, start: tRef.t, dur: 0.28 });
  tRef.t += 0.24;
}

// Expand a template (base ops + chosen variant) into timeline items.
function templateItems(name, labels, variant, tRef, items) {
  const tpl = TEMPLATES[name];
  if (!tpl) return;
  const ops = tpl.ops.concat((variant && tpl.variants && tpl.variants[variant]) || []);
  const L = labels || {};
  for (const op of ops) {
    if (op.g && GLYPHS[op.g]) {
      const size = op.size || 160, k = size / 100;
      for (const s of GLYPHS[op.g]) {
        const pts = s.pts.map(([px, py]) => [op.x + px * k, op.y + py * k]);
        pushStroke(items, pts, op.color || s.color, s.width || 3, tRef);
      }
    } else if (op.p) {
      pushStroke(items, op.p, op.color, op.width, tRef);
    } else if (op.arc) {
      const a = op.arc;
      pushStroke(items, arcPts(a.cx, a.cy, a.r, rad(a.a0 == null ? 0 : a.a0), rad(a.a1 == null ? 360 : a.a1), a.n || 40), op.color, op.width, tRef);
    } else if (op.slot) {
      const text = (L[op.slot] != null && String(L[op.slot]).trim()) ? L[op.slot] : (op.def || "");
      if (text) pushLabel(items, text, op.x, op.y, op.size, op.color, tRef);
    } else if (op.text) {
      pushLabel(items, op.text, op.x, op.y, op.size, op.color, tRef);
    }
  }
}

// Expand a step's actions into a sequential draw timeline.
function buildTimeline(step) {
  const items = [];
  const tRef = { t: 0 };
  for (const a of (step && step.actions) || []) {
    try {
      if (a.type === "template" && TEMPLATES[a.template]) {
        templateItems(a.template, a.labels, a.variant, tRef, items);
      } else if (a.type === "draw" && a.glyph && GLYPHS[a.glyph]) {
        if (!renderableBox(a)) continue; // defensive: skip off-canvas placement
        const size = a.size || 160, k = size / 100;
        for (const s of GLYPHS[a.glyph]) {
          const pts = s.pts.map(([px, py]) => [a.x + px * k, a.y + py * k]);
          pushStroke(items, pts, a.color || s.color, s.width || 3, tRef);
        }
      } else if (a.type === "draw") {
        if (!renderableBox(a)) continue;
        for (const s of primitiveStrokes(a)) pushStroke(items, s.pts, s.color, 3, tRef);
      } else if (a.type === "highlight") {
        items.push({ kind: "hl", shape: a.shape, a, start: tRef.t, dur: 0.45 });
        tRef.t += 0.45;
      } else if (a.type === "label") {
        pushLabel(items, a.text, a.x, a.y, a.size, a.color, tRef);
      }
    } catch (e) {
      // malformed action — skip rather than break the board
    }
  }
  return { items, total: tRef.t };
}

// Point along a polyline at a given arc-length budget (for the chalk tip).
function pointAtBudget(pts, budget) {
  let used = 0;
  for (let i = 1; i < pts.length; i++) {
    const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (used + seg >= budget) {
      const f = seg === 0 ? 0 : (budget - used) / seg;
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f];
    }
    used += seg;
  }
  return pts[pts.length - 1];
}

function partialStroke(ctx, pts, budget) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  let used = 0;
  for (let i = 1; i < pts.length; i++) {
    const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (used + seg <= budget) {
      ctx.lineTo(pts[i][0], pts[i][1]);
      used += seg;
    } else {
      const f = seg === 0 ? 0 : (budget - used) / seg;
      ctx.lineTo(pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f);
      break;
    }
  }
  ctx.stroke();
}

function drawLabel(ctx, a, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  const size = Math.min(28, Math.max(12, a.size || 17));
  ctx.font = `600 ${size}px "Segoe UI", system-ui, sans-serif`;
  ctx.fillStyle = a.color || CHALK;
  ctx.textBaseline = "middle";
  const words = String(a.text || "").split(" ");
  const maxW = 320;
  let line = "";
  let y = a.y;
  for (const w of words) {
    const test = line ? line + " " + w : w;
    if (ctx.measureText(test).width > maxW && line) {
      ctx.fillText(line, a.x, y);
      line = w;
      y += size + 4;
    } else {
      line = test;
    }
  }
  if (line) ctx.fillText(line, a.x, y);
  ctx.restore();
}

function drawHighlight(ctx, item, p) {
  const a = item.a;
  ctx.save();
  ctx.globalAlpha = p;
  ctx.strokeStyle = HIGHLIGHT_COLOR;
  ctx.lineWidth = 4;
  ctx.setLineDash([10, 7]);
  if (a.shape === "circle") {
    ctx.beginPath(); ctx.arc(a.cx, a.cy, a.r, 0, Math.PI * 2); ctx.stroke();
  } else {
    ctx.strokeRect(a.x, a.y, a.w, a.h);
  }
  ctx.restore();
}

function drawGrid(ctx) {
  ctx.strokeStyle = "rgba(38, 52, 79, 0.28)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= BOARD_W; x += 50) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, BOARD_H); ctx.stroke();
  }
  for (let y = 0; y <= BOARD_H; y += 50) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(BOARD_W, y); ctx.stroke();
  }
}

function drawVignette(ctx) {
  const g = ctx.createRadialGradient(BOARD_W / 2, BOARD_H / 2, BOARD_H * 0.25, BOARD_W / 2, BOARD_H / 2, BOARD_W * 0.72);
  g.addColorStop(0, "rgba(0,0,0,0)");
  g.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, BOARD_W, BOARD_H);
}

// Render the timeline at time t (seconds): chalk strokes drawn pen-style up to t,
// with a soft glow and a bright tip on the stroke currently being drawn.
let _ctx = null;

function initBoard(canvas) {
  _ctx = setupBoard(canvas);
  return _ctx;
}

function drawFrame(timeline, t) {
  // The player starts its frame loop before a lesson exists, and a question interlude
  // can be dismissed mid-build; both hand in a null timeline.
  if (!timeline || !_ctx) return;
  const ctx = _ctx;
  if (!ctx) return;
  ctx.clearRect(0, 0, BOARD_W, BOARD_H);
  ctx.fillStyle = "#0a101c";
  ctx.fillRect(0, 0, BOARD_W, BOARD_H);
  drawGrid(ctx);
  drawVignette(ctx);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const item of timeline.items) {
    if (t < item.start) break;
    const p = Math.min(1, (t - item.start) / item.dur);
    if (item.kind === "stroke") {
      const budget = p * strokeLen(item.pts);
      ctx.save();
      ctx.shadowColor = item.color;
      ctx.shadowBlur = 6;
      ctx.strokeStyle = item.color;
      ctx.lineWidth = item.width;
      partialStroke(ctx, item.pts, budget);
      ctx.restore();
      if (p > 0 && p < 1) {
        const tip = pointAtBudget(item.pts, budget);
        ctx.save();
        ctx.shadowColor = "#ffffff";
        ctx.shadowBlur = 14;
        ctx.fillStyle = "#ffffff";
        ctx.beginPath(); ctx.arc(tip[0], tip[1], 3.2, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }
    } else if (item.kind === "label") {
      drawLabel(ctx, item.a, p);
    } else if (item.kind === "hl") {
      drawHighlight(ctx, item, p);
    }
  }
}
