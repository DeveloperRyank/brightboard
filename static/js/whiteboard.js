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
const BOARD_BG = "#0A1730";
const HIGHLIGHT_COLOR = "#FF6B57";
const CHALK = "#F4EBDD";
const CHALK_DIM = "#BFB09C";    // secondary ink on the board, the cream muted down
const DRAW_SPEED = 430; // board units of stroke length per second
// The board is drawn in 1000x620 units and scaled to fit, and CSS never lets it
// narrower than 700px (it scrolls sideways instead), so the scale stays above 0.7.
// Chalk thinner than this would fade to nothing at that scale.
const MIN_CHALK_CSS_PX = 1.15;

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

// --- geometry builders for the physics templates ---
// The physics diagrams are made of things that repeat — coils, waves, ruler ticks,
// hatchings, arrows. Each builder below computes that geometry once, in board units,
// so the templates hold no hand-typed coordinate runs. Physics is the one subject
// where a wrong diagram is worse than no diagram, so this is verified by rendering.

// A helical spring hanging vertically: n full turns between (x,y0) and (x,y1).
// Stretching it means the same turns over a longer span, which is what a student
// has to SEE in a Hooke's law step.
function springPts(x, y0, y1, halfWidth, turns) {
  const pts = [[x, y0]];
  const n = turns * 4;
  for (let i = 1; i < n; i++) {
    const f = i / n;
    const side = Math.sin(2 * Math.PI * turns * f);
    pts.push([x + halfWidth * side, y0 + (y1 - y0) * f]);
  }
  pts.push([x, y1]);
  return pts;
}

// A transverse wave along y = yMid between x0 and x1.
function wavePts(x0, x1, yMid, amp, cycles, n = 140) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n;
    pts.push([x0 + (x1 - x0) * f, yMid - amp * Math.sin(2 * Math.PI * cycles * f)]);
  }
  return pts;
}

// Dots along a horizontal line, bunched at compressions and spread at rarefactions:
// a longitudinal wave (sound) as the booklet draws it. `amp` is the displacement in
// board units; the bunching only reads if amp is a good fraction of the dot spacing,
// and dots cross over once amp exceeds (x1-x0)/(2*pi*cycles).
function rowDots(x0, x1, yMid, cycles, amp, color) {
  const ops = [], n = Math.max(12, 12 * cycles);
  for (let i = 0; i <= n; i++) {
    const f = i / n;
    const x = x0 + (x1 - x0) * f + amp * Math.sin(2 * Math.PI * cycles * f);
    ops.push({ arc: { cx: x, cy: yMid, r: 4.5 }, color: color || CHALK, width: 2 });
  }
  return ops;
}

// The hatching that means "this surface is fixed", along a line from x0 to x1.
function hatch(x0, x1, y, gap, len, up) {
  const ops = [];
  for (let x = x0; x <= x1; x += gap) {
    ops.push({ p: [[x, y], [x - len, up ? y - len : y + len]], color: CHALK_DIM, width: 2 });
  }
  return ops;
}

// Hatching down a slope — a plane makes an angle with the horizontal. The strokes
// run into the body of the slope, never up into the space the block sits in.
function hatchAlong(x0, y0, dx, dy, gap, len) {
  const ops = [], L = Math.hypot(dx, dy), n = Math.floor(L / gap);
  const ux = dx / L, uy = dy / L;
  const nx = -uy, ny = ux;
  for (let i = 1; i <= n; i++) {
    const px = x0 + ux * gap * i, py = y0 + uy * gap * i;
    ops.push({ p: [[px, py], [px + nx * len, py + ny * len]], color: CHALK_DIM, width: 2 });
  }
  return ops;
}

// A straight line with an arrowhead at its end — force, ray, field direction.
function arrowOps(x1, y1, x2, y2, opts) {
  const o = opts || {};
  const color = o.color || CHALK, width = o.width || 3, head = o.head || 15;
  const a = Math.atan2(y2 - y1, x2 - x1);
  return [
    { p: [[x1, y1], [x2, y2]], color, width },
    { p: [[x2, y2], [x2 - head * Math.cos(a - 0.42), y2 - head * Math.sin(a - 0.42)]], color, width },
    { p: [[x2, y2], [x2 - head * Math.cos(a + 0.42), y2 - head * Math.sin(a + 0.42)]], color, width },
  ];
}

// A bracket with ticked ends, for measuring an extension or a distance between two
// levels. Runs vertically at x between y0 and y1.
function bracketOps(x, y0, y1, opts) {
  const o = opts || {}, color = o.color || CHALK_DIM, width = o.width || 2, tick = o.tick || 14;
  const t = tick / 2;
  return [
    { p: [[x, y0], [x, y1]], color, width },
    { p: [[x - t, y0], [x + t, y0]], color, width },
    { p: [[x - t, y1], [x + t, y1]], color, width },
  ];
}

// A vertical measuring scale with ticks every `step` units.
function rulerOps(x, y0, y1, step, len) {
  const ops = [{ p: [[x, y0], [x, y1]], color: CHALK_DIM, width: 2 }];
  for (let y = y0; y <= y1; y += step) {
    ops.push({ p: [[x, y], [x + len, y]], color: CHALK_DIM, width: 2 });
  }
  return ops;
}

// Two facing plates of a capacitor, centred on x and spanning y .. y+h with a gap
// between them. Wires meet the plates on the centre line, so h/2 straddles that line.
function capPlateOps(x, y, h, gap, opts) {
  const o = opts || {}, color = o.color || CHALK, width = o.width || 6;
  return [
    { p: [[x - gap / 2, y], [x - gap / 2, y + h]], color, width },
    { p: [[x + gap / 2, y], [x + gap / 2, y + h]], color, width },
  ];
}

// The plates of a capacitor wired to a cell: the long thin line of the cell is its +
// terminal, so the plate joined to it is the one that ends up positive.
function capacitorFrameOps() {
  return [
    ...capPlateOps(480, 200, 150, 160, { width: 7 }),
    { p: [[400, 350], [400, 470]], width: 3 },
    { p: [[560, 350], [560, 470]], width: 3 },
    { p: [[400, 470], [470, 470]], width: 3 },
    { p: [[470, 447], [470, 493]], width: 4 },
    { p: [[500, 457], [500, 483]], width: 7 },
    { p: [[500, 470], [560, 470]], width: 3 },
    { text: "+", x: 455, y: 432, size: 17, color: "#D9A648" },
    { text: "−", x: 506, y: 432, size: 17, color: "#74A98D" },
  ];
}

// A rectangle whose base lies along a slope through (x, y), the base rising at `deg`
// (canvas degrees, so a plane going up to the right is negative). The block has to sit
// OUTSIDE the wedge, so the normal points away from the body of the slope: for a plane
// rising to the right that is up and to the left.
function slopeRect(x, y, w, h, deg) {
  const t = rad(deg), ux = Math.cos(t), uy = Math.sin(t);
  const nx = uy, ny = -ux, hw = w / 2;
  const ax = x - ux * hw, ay = y - uy * hw, bx = x + ux * hw, by = y + uy * hw;
  return [[ax, ay], [bx, by], [bx + nx * h, by + ny * h], [ax + nx * h, ay + ny * h], [ax, ay]];
}

// A filled-out label: text plus the leader line back to the thing it names.
// The text sits at (tx,ty); the leader starts 14 units to its left and runs to (px,py).
// If the label would be wider than the space to its right, it is moved left instead —
// board text is left-anchored, so a long label starting near the right edge runs off.
function callout(text, tx, ty, px, py, opts) {
  const o = opts || {}, size = o.size || 15;
  const color = o.color || CHALK_DIM, maxRight = o.maxRight == null ? BOARD_W - 30 : o.maxRight;
  const w = text.length * size * 0.52;
  let x = tx;
  if (tx > maxRight) x = Math.max(160, maxRight - w);
  else if (x + w > maxRight) x = maxRight - w;
  const ops = [{ text, x, y: ty, size, color }];
  if (px != null) {
    const sx = x < px ? x + w + 6 : x - 6;
    ops.push({ p: [[sx, ty], [px, py]], color, width: 1.5 });
  }
  return ops;
}

// Move a whole pre-built block of ops, so a template can be composed at convenient
// coordinates and then positioned on the board without re-deriving every number.
function shiftOps(ops, dx, dy) {
  return ops.map((op) => {
    const o = Object.assign({}, op);
    if (o.p) o.p = o.p.map(([x, y]) => [x + dx, y + dy]);
    if (o.arc) o.arc = { cx: o.arc.cx + dx, cy: o.arc.cy + dy, r: o.arc.r, a0: o.arc.a0, a1: o.arc.a1, n: o.arc.n };
    if (o.g) { o.x += dx; o.y += dy; }
    if (o.x != null && !o.g && !o.arc) { o.x += dx; o.y += dy; }
    return o;
  });
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
    { pts: [[50, 80], [50, 38]], color: "#FF6B57", width: 4 },
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
    { pts: [[50, 40], [43, 28], [50, 8], [57, 28], [50, 40]], color: "#D9A648" },
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
    { pts: arcPts(50, 50, 7, 0, rad(360)), color: "#D9A648" },
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
    { pts: [[20, 20], [12, 12]], color: "#D9A648" },
    { pts: [[80, 20], [88, 12]], color: "#D9A648" },
    { pts: [[50, 12], [50, 2]], color: "#D9A648" },
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
    { pts: [[31, 66], [69, 66]], color: "#F4EBDD", width: 2 },
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
  // Physics symbols, same 100x100 box. A circuit drawn from these is the examiner's
  // symbol, not an illustration: the plates, the box and the cross are the convention.
  battery_cell: [
    { pts: [[20, 50], [42, 50]] },
    { pts: [[42, 12], [42, 88]], width: 3 },
    { pts: [[58, 32], [58, 68]], width: 6 },
    { pts: [[58, 50], [80, 50]] },
  ],
  resistor: [
    { pts: [[10, 50], [24, 50]] },
    { pts: [[24, 34], [76, 34], [76, 66], [24, 66], [24, 34]] },
    { pts: [[76, 50], [90, 50]] },
  ],
  lamp: [
    { pts: arcPts(50, 50, 26, 0, rad(360)) },
    { pts: [[32, 32], [68, 68]] },
    { pts: [[68, 32], [32, 68]] },
    { pts: [[10, 50], [24, 50]] },
    { pts: [[76, 50], [90, 50]] },
  ],
  magnet_bar: [
    { pts: [[10, 32], [90, 32], [90, 68], [10, 68], [10, 32]] },
    { pts: [[50, 32], [50, 68]], width: 2 },
  ],
  pivot: [
    { pts: [[50, 22], [18, 84], [82, 84], [50, 22]] },
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
      { arc: { cx: 430, cy: 300, r: 20 }, color: "#D9A648", width: 3 },
      { arc: { cx: 430, cy: 190, r: 18 }, width: 3 },
      { arc: { cx: 330, cy: 375, r: 18 }, width: 3 },
      { arc: { cx: 530, cy: 375, r: 18 }, width: 3 },
      { arc: { cx: 430, cy: 445, r: 18 }, width: 3 },
      { text: "C", x: 423, y: 300, size: 20, color: "#D9A648" },
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
      { p: [[436, 272], [428, 254]], width: 2, color: "#D9A648" },
      { p: [[464, 272], [472, 254]], width: 2, color: "#D9A648" },
      { arc: { cx: 450, cy: 300, r: 22 }, color: "#FF6B57", width: 3 },
      { arc: { cx: 360, cy: 378, r: 18 }, width: 3 },
      { arc: { cx: 540, cy: 378, r: 18 }, width: 3 },
      { text: "O", x: 442, y: 300, size: 20, color: "#FF6B57" },
      { text: "H", x: 352, y: 378, size: 18 },
      { text: "H", x: 532, y: 378, size: 18 },
      { slot: "angle", x: 420, y: 348, size: 15, color: "#D9A648", def: "104.5\u00b0" },
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
      { slot: "ea", x: 508, y: 225, size: 16, color: "#D9A648", def: "E\u2090" },
      { slot: "ts", x: 420, y: 122, size: 15, def: "Transition state" },
    ],
    variants: {
      exo: [
        { p: [[200, 300], [320, 300], [380, 272], [440, 205], [500, 152], [560, 205], [640, 330], [720, 410], [820, 410]], color: "#F4EBDD", width: 4 },
        { p: [[500, 300], [500, 152]], color: "#D9A648", width: 2 },
        { p: [[760, 300], [760, 410]], color: "#74A98D", width: 2 },
        { p: [[760, 410], [753, 396]], color: "#74A98D", width: 2 },
        { p: [[760, 410], [767, 396]], color: "#74A98D", width: 2 },
        { slot: "reactants", x: 175, y: 285, size: 15, def: "Reactants" },
        { slot: "products", x: 745, y: 432, size: 15, def: "Products" },
        { slot: "dh", x: 772, y: 350, size: 16, color: "#74A98D", def: "\u0394H (exothermic)" },
      ],
      endo: [
        { p: [[200, 410], [320, 410], [380, 360], [440, 250], [500, 152], [560, 230], [640, 285], [720, 300], [820, 300]], color: "#F4EBDD", width: 4 },
        { p: [[500, 410], [500, 152]], color: "#D9A648", width: 2 },
        { p: [[760, 410], [760, 300]], color: "#74A98D", width: 2 },
        { p: [[760, 300], [753, 314]], color: "#74A98D", width: 2 },
        { p: [[760, 300], [767, 314]], color: "#74A98D", width: 2 },
        { slot: "reactants", x: 175, y: 432, size: 15, def: "Reactants" },
        { slot: "products", x: 745, y: 285, size: 15, def: "Products" },
        { slot: "dh", x: 772, y: 350, size: 16, color: "#74A98D", def: "\u0394H (endothermic)" },
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
      line: [{ p: [[180, 470], [820, 150]], color: "#F4EBDD", width: 4 }],
      curve: [{ p: [[180, 480], [340, 340], [500, 255], [680, 205], [840, 185]], color: "#F4EBDD", width: 4 }],
      empty: [],
    },
  },

  // --- PHYSICS ---
  // Chemistry's templates are lab apparatus a student can only look at. These are the
  // diagrams a Physics booklet draws on the page: a spring under load, forces on a
  // body, a circuit, a ray hitting a mirror. Notes-driven lessons have to draw them,
  // so the geometry is fixed here and verified by rendering, not invented by a model.
  spring: {
    ops: [
      { p: [[280, 130], [660, 130]], width: 4 },
      ...hatch(300, 660, 130, 45, 18, true),
      ...rulerOps(566, 150, 490, 40, 16),
      ...callout("Fixed support", 330, 88, 380, 126, { maxRight: 640 }),
      ...callout("Millimetre scale", 640, 508, 584, 486, { maxRight: 900 }),
    ],
    variants: {
      unstretched: [
        { p: springPts(400, 130, 250, 50, 6), width: 3 },
        { p: [[400, 250], [400, 272]], width: 2 },
        { p: [[400, 272], [558, 272]], width: 2, dash: [9, 7] },
        ...callout("Pointer marks the original length", 250, 330, 340, 285, { maxRight: 620 }),
      ],
      loaded: [
        { p: springPts(400, 130, 320, 50, 6), width: 3 },
        { p: [[400, 320], [400, 344]], width: 2 },
        { p: [[345, 344], [455, 344], [455, 414], [345, 414], [345, 344]], width: 3 },
        { p: [[455, 379], [558, 379]], width: 2, dash: [9, 7] },
        { p: [[400, 272], [706, 272]], width: 2, dash: [4, 6], color: "#BFB09C" },
        ...callout("Original length", 620, 252, null, null, { maxRight: 900 }),
        ...bracketOps(720, 272, 379, { color: "#D9A648", tick: 20 }),
        { slot: "extension", x: 740, y: 318, size: 16, color: "#D9A648", def: "Extension e" },
        { slot: "load", x: 255, y: 385, size: 16, def: "Load mg" },
        ...callout("Spring stretches", 180, 200, 355, 225, { maxRight: 380 }),
      ],
      spring_balance: [
        { p: [[418, 88], [462, 88], [462, 112], [418, 112]], width: 2 },
        { p: [[380, 112], [500, 112], [500, 430], [380, 430], [380, 112]], width: 3 },
        { p: springPts(450, 150, 330, 30, 7), width: 2 },
        { p: [[450, 330], [450, 372]], width: 2 },
        ...rulerOps(480, 150, 410, 34, -14),
        { p: [[450, 372], [486, 372]], width: 2 },
        { p: [[440, 430], [464, 430], [464, 452], [440, 452]], width: 2 },
        { p: [[440, 452], [440, 492]], width: 2 },
        ...arrowOps(440, 492, 440, 552, { color: "#D9A648" }),
        { slot: "reading", x: 520, y: 372, size: 16, color: "#D9A648", def: "Scale reads the force in newtons" },
        ...callout("Spring inside the tube", 620, 250, 484, 250, { maxRight: 900 }),
      ],
    },
  },

  free_body: {
    ops: [],
    variants: {
      on_surface: [
        { p: [[180, 420], [840, 420]], width: 3 },
        ...hatch(200, 840, 420, 45, 16, false),
        { p: [[420, 300], [580, 300], [580, 420], [420, 420], [420, 300]], width: 3 },
        ...arrowOps(500, 360, 500, 545, { color: "#FF6B57", width: 4 }),
        ...arrowOps(500, 360, 500, 185),
        ...arrowOps(500, 360, 800, 360),
        ...arrowOps(500, 360, 205, 360, { color: "#74A98D" }),
        { slot: "weight", x: 516, y: 530, size: 16, color: "#FF6B57", def: "Weight W = mg" },
        { slot: "normal", x: 516, y: 172, size: 16, def: "Normal reaction N" },
        { slot: "applied", x: 640, y: 336, size: 16, def: "Applied force F" },
        { slot: "friction", x: 215, y: 336, size: 16, color: "#74A98D", def: "Friction f" },
      ],
      inclined_plane: [
        { p: [[220, 505], [785, 505]], width: 3 },
        { p: [[785, 505], [785, 200]], width: 2 },
        { p: [[220, 505], [785, 200]], width: 4 },
        ...hatchAlong(220, 505, 565, -305, 55, 16),
        { arc: { cx: 220, cy: 505, r: 115, a0: 0, a1: -28.4 }, color: "#D9A648", width: 2 },
        { slot: "angle", x: 350, y: 470, size: 16, color: "#D9A648", def: "θ" },
        { p: slopeRect(520, 340, 150, 95, -28.4), width: 3 },
        ...arrowOps(500, 292, 500, 500, { color: "#FF6B57", width: 4 }),
        ...arrowOps(500, 292, 430, 165),
        ...arrowOps(500, 292, 655, 208, { color: "#74A98D" }),
        { slot: "weight", x: 515, y: 490, size: 16, color: "#FF6B57", def: "Weight mg (vertical)" },
        { slot: "normal", x: 265, y: 150, size: 16, def: "Normal reaction N" },
        { slot: "friction", x: 600, y: 185, size: 16, color: "#74A98D", def: "Friction up the plane" },
      ],
      suspended: [
        { p: [[300, 130], [700, 130]], width: 4 },
        ...hatch(320, 700, 130, 45, 18, true),
        { p: [[500, 130], [500, 330]], width: 2 },
        { p: [[435, 330], [565, 330], [565, 425], [435, 425], [435, 330]], width: 3 },
        ...arrowOps(500, 377, 500, 555, { color: "#FF6B57", width: 4 }),
        ...arrowOps(500, 377, 500, 195),
        { slot: "tension", x: 516, y: 218, size: 16, def: "Tension T" },
        { slot: "weight", x: 516, y: 540, size: 16, color: "#FF6B57", def: "Weight W = mg" },
        ...callout("Mass m", 612, 380, null, null, { maxRight: 900 }),
        ...callout("String", 612, 250, 512, 250, { maxRight: 900 }),
      ],
    },
  },

  lever: {
    ops: shiftOps([{ p: [[180, 300], [820, 300]], width: 5 }], 0, -55),
    variants: {
      first_class: shiftOps([
        { g: "pivot", x: 430, y: 269, size: 140 },
        { p: [[415, 387], [585, 387]], width: 3 },
        ...hatch(430, 585, 387, 40, 15, false),
        ...arrowOps(250, 300, 250, 468, { color: "#FF6B57", width: 4 }),
        ...arrowOps(760, 300, 760, 468),
        { slot: "load", x: 180, y: 488, size: 16, color: "#FF6B57", def: "Load L" },
        { slot: "effort", x: 700, y: 488, size: 16, def: "Effort E" },
        ...callout("Fulcrum / pivot", 300, 420, 468, 400, { maxRight: 430 }),
        { p: [[250, 320], [250, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        { p: [[500, 400], [500, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        { p: [[760, 480], [760, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        ...arrowOps(490, 525, 262, 525, { color: "#BFB09C", width: 2 }),
        ...arrowOps(510, 525, 748, 525, { color: "#BFB09C", width: 2 }),
        { slot: "load_arm", x: 285, y: 560, size: 15, color: "#BFB09C", def: "Load arm" },
        { slot: "effort_arm", x: 585, y: 560, size: 15, color: "#BFB09C", def: "Effort arm" },
      ], 0, -55),
      second_class: shiftOps([
        { g: "pivot", x: 190, y: 269, size: 140 },
        { p: [[165, 387], [345, 387]], width: 3 },
        ...hatch(180, 345, 387, 40, 15, false),
        ...arrowOps(480, 300, 480, 462, { color: "#FF6B57", width: 4 }),
        ...arrowOps(790, 300, 790, 148),
        { slot: "load", x: 415, y: 482, size: 16, color: "#FF6B57", def: "Load L" },
        { slot: "effort", x: 700, y: 140, size: 16, def: "Effort E (upwards)" },
        ...callout("Fulcrum", 95, 425, 258, 400, { maxRight: 250 }),
        { p: [[260, 320], [260, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        { p: [[480, 470], [480, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        ...arrowOps(270, 525, 470, 525, { color: "#BFB09C", width: 2 }),
        ...arrowOps(270, 585, 780, 585, { color: "#BFB09C", width: 2 }),
        { slot: "load_arm", x: 300, y: 505, size: 15, color: "#BFB09C", def: "Load arm (short)" },
        { slot: "effort_arm", x: 520, y: 565, size: 15, color: "#BFB09C", def: "Effort arm (long)" },
      ], 0, -55),
      third_class: shiftOps([
        { g: "pivot", x: 190, y: 269, size: 140 },
        { p: [[165, 387], [345, 387]], width: 3 },
        ...hatch(180, 345, 387, 40, 15, false),
        ...arrowOps(470, 300, 470, 148),
        ...arrowOps(790, 300, 790, 462, { color: "#FF6B57", width: 4 }),
        { slot: "effort", x: 390, y: 140, size: 16, def: "Effort E (upwards)" },
        { slot: "load", x: 720, y: 482, size: 16, color: "#FF6B57", def: "Load L" },
        ...callout("Fulcrum", 95, 425, 258, 400, { maxRight: 250 }),
        { p: [[260, 320], [260, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        { p: [[470, 320], [470, 545]], width: 1.5, dash: [8, 6], color: "#BFB09C" },
        ...arrowOps(270, 525, 460, 525, { color: "#BFB09C", width: 2 }),
        ...arrowOps(270, 585, 780, 585, { color: "#BFB09C", width: 2 }),
        { slot: "effort_arm", x: 285, y: 505, size: 15, color: "#BFB09C", def: "Effort arm (short)" },
        { slot: "load_arm", x: 540, y: 565, size: 15, color: "#BFB09C", def: "Load arm (long)" },
      ], 0, -55),
    },
  },

  circuit: {
    ops: [
      { p: [[220, 170], [440, 170]], width: 3 },
      { p: [[440, 150], [560, 150], [560, 190], [440, 190], [440, 150]], width: 3 },
      { p: [[560, 170], [780, 170]], width: 3 },
      { p: [[780, 170], [780, 470]], width: 3 },
      { p: [[780, 470], [530, 470]], width: 3 },
      { p: [[530, 432], [530, 508]], width: 4 },
      { p: [[510, 450], [510, 490]], width: 6 },
      { p: [[490, 432], [490, 508]], width: 4 },
      { p: [[470, 450], [470, 490]], width: 6 },
      { p: [[470, 470], [326, 470]], width: 3 },
      { arc: { cx: 300, cy: 470, r: 26 }, width: 3 },
      { text: "A", x: 294, y: 471, size: 19 },
      { p: [[274, 470], [220, 470]], width: 3 },
      { p: [[220, 470], [220, 440]], width: 3 },
      { p: [[220, 380], [220, 170]], width: 3 },
      { arc: { cx: 220, cy: 440, r: 5 }, width: 2 },
      { arc: { cx: 220, cy: 380, r: 5 }, width: 2 },
      // A circuit with no switch variant drawn open has to look complete, so the base
      // closes it; switch_open hides this lever and puts the hinged bar up instead.
      { p: [[220, 440], [220, 380]], width: 4, except: ["switch_open"] },
      { slot: "component", x: 488, y: 171, size: 17, def: "R" },
      ...callout("Cell: long plate is positive", 575, 552, 534, 512, { maxRight: 880 }),
      ...callout("Ammeter in series", 150, 545, 268, 496, { maxRight: 430 }),
    ],
    variants: {
      switch_closed: [
        ...callout("Switch closed: current flows", 300, 415, 292, 415, { maxRight: 700 }),
      ],
      switch_open: [
        { p: [[220, 440], [266, 392]], color: "#FF6B57", width: 3 },
        ...callout("Switch open: no current", 300, 428, 292, 428, { color: "#FF6B57", maxRight: 700 }),
      ],
      voltmeter_across: [
        { p: [[440, 150], [440, 102]], width: 2 },
        { p: [[440, 102], [474, 102]], width: 2 },
        { arc: { cx: 500, cy: 102, r: 26 }, width: 3 },
        { text: "V", x: 494, y: 103, size: 19 },
        { p: [[526, 102], [560, 102]], width: 2 },
        { p: [[560, 102], [560, 150]], width: 2 },
        ...callout("Voltmeter in parallel", 620, 78, 566, 100, { maxRight: 900 }),
      ],
      second_lamp: [
        { p: [[220, 320], [440, 320]], width: 3 },
        { arc: { cx: 500, cy: 320, r: 26 }, width: 3 },
        { p: [[482, 302], [518, 338]], width: 2 },
        { p: [[518, 302], [482, 338]], width: 2 },
        { p: [[560, 320], [780, 320]], width: 3 },
        ...callout("Second lamp across the cell", 620, 296, 574, 316, { maxRight: 880 }),
      ],
      parallel_lamps: [
        { p: [[220, 250], [444, 250]], width: 3 },
        { arc: { cx: 470, cy: 250, r: 26 }, width: 3 },
        { p: [[452, 232], [488, 268]], width: 2 },
        { p: [[488, 232], [452, 268]], width: 2 },
        { p: [[496, 250], [780, 250]], width: 3 },
        { p: [[220, 340], [444, 340]], width: 3 },
        { arc: { cx: 470, cy: 340, r: 26 }, width: 3 },
        { p: [[452, 322], [488, 358]], width: 2 },
        { p: [[488, 322], [452, 358]], width: 2 },
        { p: [[496, 340], [780, 340]], width: 3 },
        { p: [[590, 190], [590, 400]], width: 2, dash: [8, 6], color: "#BFB09C" },
        ...callout("Full voltage across each branch", 240, 205, 590, 215, { maxRight: 490 }),
      ],
    },
  },

  wave: {
    ops: [
      { p: [[150, 320], [880, 320]], width: 2, color: "#BFB09C", dash: [10, 8] },
    ],
    variants: {
      transverse: [
        { p: wavePts(160, 870, 320, 120, 3), width: 3 },
        { p: [[219, 170], [456, 170]], color: "#D9A648", width: 2 },
        { p: [[219, 158], [219, 182]], color: "#D9A648", width: 2 },
        { p: [[456, 158], [456, 182]], color: "#D9A648", width: 2 },
        ...bracketOps(640, 320, 440, { color: "#74A98D" }),
        { slot: "wavelength", x: 250, y: 140, size: 16, color: "#D9A648", def: "One wavelength λ" },
        { slot: "amplitude", x: 655, y: 400, size: 16, color: "#74A98D", def: "Amplitude A" },
        { slot: "trough_note", x: 175, y: 475, size: 15, color: "#BFB09C", def: "Trough" },
        { slot: "crest_note", x: 700, y: 185, size: 15, color: "#BFB09C", def: "Crest" },
        { slot: "vibration", x: 175, y: 555, size: 16, def: "Vibration is at 90° to the direction of travel" },
      ],
      longitudinal: [
        ...rowDots(160, 870, 300, 3, 24),
        { p: [[278, 215], [278, 385]], width: 1.5, dash: [7, 6], color: "#FF6B57" },
        { p: [[515, 215], [515, 385]], width: 1.5, dash: [7, 6], color: "#FF6B57" },
        { p: [[752, 215], [752, 385]], width: 1.5, dash: [7, 6], color: "#FF6B57" },
        { p: [[397, 215], [397, 385]], width: 1.5, dash: [7, 6], color: "#BFB09C" },
        { p: [[633, 215], [633, 385]], width: 1.5, dash: [7, 6], color: "#BFB09C" },
        { slot: "compression", x: 200, y: 175, size: 16, color: "#FF6B57", def: "Compression: particles crowded" },
        { slot: "rarefaction", x: 430, y: 430, size: 16, color: "#BFB09C", def: "Rarefaction: particles spread" },
        { slot: "vibration", x: 200, y: 500, size: 16, def: "Vibration is parallel to the direction of travel" },
      ],
    },
  },

  curved_mirror: {
    ops: [
      { p: [[210, 330], [860, 330]], width: 2, color: "#BFB09C" },
      { slot: "title", x: 170, y: 120, size: 17, def: "Ray diagram: spherical mirror" },
    ],
    variants: {
      concave: [
        { arc: { cx: 500, cy: 330, r: 200, a0: -32, a1: 32 }, width: 4 },
        { p: [[700, 330], [718, 330]], color: "#BFB09C", width: 2 },
        { p: [[696, 372], [714, 372]], color: "#BFB09C", width: 2 },
        { p: [[696, 288], [714, 288]], color: "#BFB09C", width: 2 },
        { p: [[681, 415], [699, 415]], color: "#BFB09C", width: 2 },
        { p: [[681, 245], [699, 245]], color: "#BFB09C", width: 2 },
        ...arrowOps(300, 330, 300, 230, { width: 3 }),
        { p: [[300, 230], [673, 230]], width: 2 },
        { p: [[300, 230], [679, 419]], width: 2 },
        ...arrowOps(673, 230, 551, 383, { color: "#74A98D", width: 2 }),
        ...arrowOps(679, 419, 549, 354, { color: "#74A98D", width: 2 }),
        ...arrowOps(567, 330, 567, 363, { color: "#FF6B57", width: 3 }),
        { p: [[500, 322], [500, 338]], width: 2 },
        { p: [[600, 322], [600, 338]], width: 2 },
        { p: [[700, 322], [700, 338]], width: 2 },
        ...callout("C", 494, 358, null, null, { size: 17, color: "#BFB09C" }),
        ...callout("F", 594, 358, null, null, { size: 17, color: "#BFB09C" }),
        { slot: "object_note", x: 215, y: 210, size: 15, def: "Object beyond C" },
        { slot: "image_note", x: 560, y: 458, size: 15, color: "#FF6B57", def: "Image: real, inverted, diminished" },
        { slot: "use", x: 200, y: 500, size: 16, def: "Concave: converging mirror" },
      ],
      convex: [
        { arc: { cx: 860, cy: 330, r: 200, a0: 148, a1: 212 }, width: 4 },
        { p: [[660, 330], [678, 330]], color: "#BFB09C", width: 2 },
        { p: [[664, 372], [682, 372]], color: "#BFB09C", width: 2 },
        { p: [[664, 288], [682, 288]], color: "#BFB09C", width: 2 },
        { p: [[679, 415], [697, 415]], color: "#BFB09C", width: 2 },
        { p: [[679, 245], [697, 245]], color: "#BFB09C", width: 2 },
        ...arrowOps(300, 330, 300, 235, { width: 3 }),
        { p: [[300, 235], [684, 235]], width: 2 },
        ...arrowOps(684, 235, 566, 83, { color: "#74A98D", width: 2 }),
        { p: [[684, 235], [760, 330]], width: 2, dash: [6, 6], color: "#BFB09C" },
        { p: [[760, 322], [760, 338]], width: 2 },
        { p: [[860, 322], [860, 338]], width: 2 },
        ...callout("F", 754, 358, null, null, { size: 17, color: "#BFB09C" }),
        ...callout("C", 854, 358, null, null, { size: 17, color: "#BFB09C" }),
        { slot: "virtual_note", x: 700, y: 400, size: 15, color: "#BFB09C", def: "Extensions meet behind the mirror: virtual image" },
        { slot: "use", x: 200, y: 480, size: 16, def: "Convex: always upright and diminished — driving mirror" },
      ],
    },
  },

  thin_lens: {
    ops: [
      { p: [[150, 320], [880, 320]], width: 2, color: "#BFB09C" },
      { p: [[500, 175], [500, 465]], width: 3 },
      { slot: "title", x: 170, y: 120, size: 17, def: "Ray diagram: thin lens" },
    ],
    variants: {
      converging: [
        { p: [[487, 193], [500, 175], [513, 193]], width: 3 },
        { p: [[487, 447], [500, 465], [513, 447]], width: 3 },
        { p: [[250, 320], [250, 215]], width: 3 },
        { p: [[235, 232], [250, 215], [265, 232]], width: 3 },
        { p: [[250, 215], [500, 215]], width: 2 },
        ...arrowOps(500, 215, 790, 453, { color: "#74A98D", width: 2 }),
        ...arrowOps(250, 215, 790, 453, { color: "#74A98D", width: 2 }),
        { p: [[790, 320], [790, 440]], width: 3, color: "#FF6B57" },
        { p: [[775, 423], [790, 440], [805, 423]], width: 3, color: "#FF6B57" },
        { p: [[650, 312], [650, 328]], width: 2 },
        { p: [[800, 312], [800, 328]], width: 2 },
        { p: [[350, 312], [350, 328]], width: 2 },
        { p: [[200, 312], [200, 328]], width: 2 },
        ...callout("F", 644, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("2F", 794, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("F", 344, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("2F", 194, 348, null, null, { size: 16, color: "#BFB09C" }),
        { slot: "object_note", x: 185, y: 190, size: 15, def: "Object" },
        { slot: "image_note", x: 810, y: 470, size: 15, color: "#FF6B57", def: "Real, inverted, magnified" },
        { slot: "power", x: 620, y: 545, size: 16, def: "Converging lens: f positive, power P = 1/f in dioptres" },
      ],
      diverging: [
        { p: [[487, 157], [500, 175], [513, 157]], width: 3 },
        { p: [[487, 483], [500, 465], [513, 483]], width: 3 },
        { p: [[250, 320], [250, 215]], width: 3 },
        { p: [[235, 232], [250, 215], [265, 232]], width: 3 },
        { p: [[250, 215], [500, 215]], width: 2 },
        ...arrowOps(500, 215, 730, 85, { color: "#74A98D", width: 2 }),
        ...arrowOps(500, 320, 730, 225, { color: "#74A98D", width: 2 }),
        { p: [[500, 215], [350, 320]], width: 2, dash: [7, 6], color: "#BFB09C" },
        { p: [[500, 320], [350, 320]], width: 2, dash: [7, 6], color: "#BFB09C" },
        { p: [[380, 320], [380, 259]], width: 2, dash: [5, 5], color: "#FF6B57" },
        { p: [[650, 312], [650, 328]], width: 2 },
        { p: [[800, 312], [800, 328]], width: 2 },
        { p: [[350, 312], [350, 328]], width: 2 },
        { p: [[200, 312], [200, 328]], width: 2 },
        ...callout("F", 644, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("2F", 794, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("F", 344, 348, null, null, { size: 16, color: "#BFB09C" }),
        ...callout("2F", 194, 348, null, null, { size: 16, color: "#BFB09C" }),
        { slot: "image_note", x: 300, y: 240, size: 15, color: "#FF6B57", def: "Virtual, upright, diminished" },
        { slot: "power", x: 620, y: 545, size: 16, def: "Diverging lens: f and power are negative" },
      ],
    },
  },

  refraction: {
    ops: [],
    variants: {
      // The normal is perpendicular to the FACE, so on the vertical sides of a block
      // it is horizontal — the angles i, r and e are all measured from that horizontal.
      // n = 1.5, i = 30° gives r = 19.5° inside and an emergent ray back at 30°.
      glass_block: [
        { p: [[290, 190], [710, 190], [710, 450], [290, 450], [290, 190]], width: 3 },
        ...arrowOps(117, 170, 290, 270, { width: 3 }),
        ...arrowOps(290, 270, 710, 418, { width: 2.5 }),
        ...arrowOps(710, 418, 814, 478, { width: 2.5 }),
        { p: [[220, 270], [360, 270]], width: 1.5, dash: [6, 5], color: "#BFB09C" },
        { p: [[640, 418], [780, 418]], width: 1.5, dash: [6, 5], color: "#BFB09C" },
        { arc: { cx: 290, cy: 270, r: 55, a0: 180, a1: 210 }, color: "#D9A648", width: 2 },
        { arc: { cx: 290, cy: 270, r: 60, a0: 0, a1: 19.5 }, color: "#74A98D", width: 2 },
        { arc: { cx: 710, cy: 418, r: 45, a0: 180, a1: 199.5 }, color: "#74A98D", width: 2 },
        { arc: { cx: 710, cy: 418, r: 55, a0: 0, a1: 30 }, color: "#D9A648", width: 2 },
        { slot: "i", x: 212, y: 256, size: 16, color: "#D9A648", def: "i" },
        { slot: "r", x: 368, y: 288, size: 16, color: "#74A98D", def: "r" },
        { slot: "r2", x: 626, y: 406, size: 16, color: "#74A98D", def: "r" },
        { slot: "e", x: 756, y: 440, size: 16, color: "#D9A648", def: "e" },
        ...callout("Glass block", 560, 165, 520, 190, { maxRight: 780 }),
        { slot: "law", x: 170, y: 520, size: 16, def: "sin i / sin r = n, constant for the pair of media" },
        { slot: "emerge", x: 560, y: 560, size: 16, def: "Emerges parallel to the incident ray, shifted sideways" },
      ],
      // Apex angle 60°, n = 1.5, minimum deviation: i = e = 48.6°, r1 = r2 = 30°,
      // so the ray inside runs parallel to the base and D = i + e - A = 37.2°.
      prism: [
        { p: [[500, 140], [350, 400], [650, 400], [500, 140]], width: 3 },
        ...arrowOps(173, 376, 410, 296, { width: 3 }),
        ...arrowOps(410, 296, 590, 296, { width: 2.5 }),
        ...arrowOps(590, 296, 827, 376, { width: 2.5 }),
        { p: [[332, 251], [458, 324]], width: 1.5, dash: [6, 5], color: "#BFB09C" },
        { p: [[542, 324], [668, 251]], width: 1.5, dash: [6, 5], color: "#BFB09C" },
        { arc: { cx: 410, cy: 296, r: 52, a0: 161, a1: 210 }, color: "#D9A648", width: 2 },
        { arc: { cx: 410, cy: 296, r: 58, a0: 0, a1: 30 }, color: "#74A98D", width: 2 },
        { arc: { cx: 590, cy: 296, r: 58, a0: 150, a1: 180 }, color: "#74A98D", width: 2 },
        { arc: { cx: 590, cy: 296, r: 52, a0: -30, a1: 19 }, color: "#D9A648", width: 2 },
        { arc: { cx: 500, cy: 140, r: 45, a0: 60, a1: 120 }, color: "#FF6B57", width: 2 },
        { slot: "i", x: 330, y: 288, size: 15, color: "#D9A648", def: "i" },
        { slot: "r1", x: 472, y: 330, size: 15, color: "#74A98D", def: "r₁" },
        { slot: "r2", x: 524, y: 330, size: 15, color: "#74A98D", def: "r₂" },
        { slot: "e", x: 650, y: 288, size: 15, color: "#D9A648", def: "e" },
        { slot: "a", x: 494, y: 200, size: 15, color: "#FF6B57", def: "A" },
        { slot: "deviation", x: 620, y: 155, size: 16, color: "#D9A648", def: "D = i + e - A" },
        { slot: "note", x: 170, y: 480, size: 16, def: "A = r₁ + r₂; white light splits into its colours: dispersion" },
      ],
    },
  },

  magnetism: {
    ops: [],
    variants: {
      bar_magnet: [
        { p: [[400, 275], [600, 275], [600, 345], [400, 345], [400, 275]], width: 3 },
        { p: [[500, 275], [500, 345]], width: 2 },
        { text: "N", x: 442, y: 316, size: 22 },
        { text: "S", x: 546, y: 316, size: 22 },
        { p: ellipsePts(500, 310, 300, 110, 0), width: 2, color: "#BFB09C" },
        ...arrowOps(500, 200, 560, 214, { color: "#BFB09C", width: 2, head: 12 }),
        ...callout("Field lines leave N and enter S", 170, 155, null, null, { maxRight: 620 }),
        ...callout("North-seeking pole", 180, 405, 400, 342, { maxRight: 380 }),
        { slot: "field", x: 640, y: 430, size: 16, def: "Direction of the magnetic field" },
      ],
      solenoid: [
        { p: [[400, 300], [600, 300]], width: 2 },
        { p: [[400, 340], [600, 340]], width: 2 },
        { p: ellipsePts(300, 320, 26, 58, 0), width: 3 },
        { p: ellipsePts(350, 320, 26, 58, 0), width: 3 },
        { p: ellipsePts(400, 320, 26, 58, 0), width: 3 },
        { p: ellipsePts(450, 320, 26, 58, 0), width: 3 },
        { p: ellipsePts(500, 320, 26, 58, 0), width: 3 },
        { p: ellipsePts(550, 320, 26, 58, 0), width: 3 },
        ...arrowOps(648, 320, 730, 320, { width: 3 }),
        ...arrowOps(352, 320, 270, 320, { width: 3 }),
        { text: "N", x: 596, y: 248, size: 20, color: "#FF6B57" },
        { text: "S", x: 296, y: 248, size: 20, color: "#FF6B57" },
        ...callout("Right-hand grip rule: fingers follow the current, the thumb points to N", 170, 150, null, null, { maxRight: 700 }),
        { slot: "core", x: 700, y: 250, size: 16, def: "Soft-iron core" },
      ],
      two_wires: [
        { arc: { cx: 400, cy: 310, r: 15 }, width: 2 },
        { p: [[400, 298], [400, 288]], width: 2 },
        { p: [[400, 322], [400, 332]], width: 2 },
        { arc: { cx: 600, cy: 310, r: 15 }, width: 2 },
        { p: [[600, 295], [600, 285]], width: 2 },
        { p: [[600, 325], [600, 335]], width: 2 },
        ...arrowOps(400, 285, 378, 300, { width: 2, head: 11 }),
        ...arrowOps(378, 300, 400, 308, { width: 2, head: 11 }),
        ...arrowOps(400, 308, 422, 300, { width: 2, head: 11 }),
        ...arrowOps(422, 300, 400, 285, { width: 2, head: 11 }),
        ...arrowOps(600, 285, 622, 300, { width: 2, head: 11 }),
        ...arrowOps(622, 300, 600, 308, { width: 2, head: 11 }),
        ...arrowOps(600, 308, 578, 300, { width: 2, head: 11 }),
        ...arrowOps(578, 300, 600, 285, { width: 2, head: 11 }),
        ...arrowOps(432, 150, 568, 150, { color: "#74A98D" }),
        ...arrowOps(568, 470, 432, 470, { color: "#FF6B57" }),
        ...callout("Currents in the same direction attract", 190, 112, null, null, { color: "#74A98D", maxRight: 700 }),
        ...callout("Currents in opposite directions repel", 190, 520, null, null, { color: "#FF6B57", maxRight: 700 }),
        ...callout("Cross-section: current out of the page (left wire)", 165, 400, 390, 340, { maxRight: 520 }),
        ...callout("Into the page (right wire)", 700, 390, 612, 330, { maxRight: 900 }),
      ],
    },
  },

  vector_triangle: {
    ops: [],
    variants: {
      parallelogram: [
        { p: [[300, 420], [700, 420]], width: 3 },
        { p: [[300, 420], [460, 240]], width: 3 },
        { p: [[460, 240], [700, 240]], width: 2, dash: [8, 6], color: "#BFB09C" },
        { p: [[700, 420], [700, 240]], width: 2, dash: [8, 6], color: "#BFB09C" },
        ...arrowOps(300, 420, 700, 420, { color: "#D9A648", width: 4 }),
        ...arrowOps(300, 420, 460, 240, { color: "#74A98D", width: 4 }),
        ...arrowOps(300, 420, 700, 160, { color: "#FF6B57", width: 4 }),
        { arc: { cx: 360, cy: 420, r: 60, a0: 0, a1: -45 }, color: "#BFB09C", width: 2 },
        ...callout("First force", 380, 455, null, null, { color: "#D9A648", maxRight: 620 }),
        ...callout("Second force", 200, 300, null, null, { color: "#74A98D", maxRight: 300 }),
        { slot: "resultant", x: 560, y: 130, size: 16, color: "#FF6B57", def: "Resultant (diagonal of the parallelogram)" },
        { slot: "angle", x: 385, y: 385, size: 15, color: "#BFB09C", def: "θ" },
      ],
      polygon: [
        { p: [[300, 430], [620, 430]], width: 3, color: "#D9A648" },
        { p: [[620, 430], [760, 290]], width: 3, color: "#74A98D" },
        { p: [[760, 290], [560, 170]], width: 3, color: "#BFB09C" },
        { p: [[560, 170], [300, 430]], width: 4, color: "#FF6B57" },
        ...arrowOps(590, 400, 620, 430, { color: "#D9A648", width: 3, head: 12 }),
        ...arrowOps(730, 320, 760, 290, { color: "#74A98D", width: 3, head: 12 }),
        ...arrowOps(660, 230, 560, 170, { color: "#BFB09C", width: 3, head: 12 }),
        ...arrowOps(430, 300, 300, 430, { color: "#FF6B57", width: 3, head: 12 }),
        { slot: "rule", x: 600, y: 128, size: 16, def: "Forces in equilibrium close the polygon" },
        { slot: "equilibrant", x: 180, y: 300, size: 15, color: "#FF6B57", def: "Closing side = equilibrant" },
      ],
    },
  },

  electroscope: {
    ops: [
      { p: [[400, 205], [400, 490], [600, 490], [600, 205]], width: 3 },
      { p: [[365, 490], [635, 490]], width: 4 },
      { p: [[440, 190], [560, 190]], width: 5 },
      { p: [[500, 190], [500, 320]], width: 3 },
      ...callout("Brass disc", 650, 190, 572, 192, { maxRight: 900 }),
      ...callout("Metal rod", 650, 250, 512, 250, { maxRight: 900 }),
      ...callout("Glass jar", 650, 460, 606, 460, { maxRight: 900 }),
    ],
    variants: {
      uncharged: [
        { p: [[495, 320], [495, 432]], width: 2 },
        { p: [[505, 320], [505, 432]], width: 2 },
        { slot: "observation", x: 650, y: 380, size: 16, def: "Leaves hang together: uncharged" },
      ],
      charged: [
        { p: [[500, 320], [455, 428]], color: "#FF6B57", width: 3 },
        { p: [[500, 320], [545, 428]], color: "#FF6B57", width: 3 },
        { slot: "observation", x: 650, y: 380, size: 16, color: "#FF6B57", def: "Like charges repel: leaves diverge" },
        ...callout("Gold leaves", 180, 420, 450, 400, { maxRight: 380 }),
      ],
      positive: [
        { p: [[500, 320], [462, 420]], width: 3 },
        { p: [[500, 320], [538, 420]], width: 3 },
        { text: "+", x: 494, y: 255, size: 18, color: "#D9A648" },
        { text: "+", x: 448, y: 385, size: 16, color: "#D9A648" },
        { text: "+", x: 540, y: 385, size: 16, color: "#D9A648" },
        { slot: "observation", x: 650, y: 380, size: 16, def: "Charged positively: excess + on rod and leaves" },
      ],
      negative: [
        { p: [[500, 320], [462, 420]], width: 3 },
        { p: [[500, 320], [538, 420]], width: 3 },
        { text: "−", x: 494, y: 255, size: 18, color: "#74A98D" },
        { text: "−", x: 448, y: 385, size: 16, color: "#74A98D" },
        { text: "−", x: 540, y: 385, size: 16, color: "#74A98D" },
        { slot: "observation", x: 650, y: 380, size: 16, def: "Charged negatively: excess electrons" },
      ],
      discharge: [
        { p: [[500, 320], [470, 425]], width: 3 },
        { p: [[500, 320], [530, 425]], width: 3 },
        ...arrowOps(300, 210, 432, 192, { color: "#D9A648" }),
        { slot: "observation", x: 650, y: 380, size: 16, def: "A charged body near the disc: leaves spread further" },
      ],
    },
  },

  // Two facing plates and the cell that charges them is what a capacitor IS, so the frame
  // is drawn once and each variant only adds what it teaches: the field across the gap,
  // the dielectric in it, or the way several are wired together.
  capacitor: {
    ops: [],
    variants: {
      air_gap: [
        ...capacitorFrameOps(),
        ...arrowOps(414, 238, 546, 238, { color: "#D9A648", width: 2, head: 11 }),
        ...arrowOps(414, 278, 546, 278, { color: "#D9A648", width: 2, head: 11 }),
        ...arrowOps(414, 318, 546, 318, { color: "#D9A648", width: 2, head: 11 }),
        { slot: "title", x: 120, y: 132, size: 18, def: "Parallel-plate capacitor" },
        ...callout("Plate on the + terminal holds +Q", 620, 210, 566, 232, { maxRight: 930 }),
        ...callout("Facing plate holds the same charge, minus", 620, 348, 566, 330, { maxRight: 930 }),
        { slot: "gap", x: 640, y: 420, size: 15, color: "#BFB09C", def: "Field runs across the air gap d" },
        { slot: "note", x: 150, y: 545, size: 16, def: "C = Q / V, in farads" },
      ],
      dielectric: [
        ...capacitorFrameOps(),
        { p: [[412, 206], [548, 206], [548, 344], [412, 344], [412, 206]], width: 2, color: "#BFB09C" },
        ...arrowOps(414, 278, 546, 278, { color: "#74A98D", width: 2, head: 11 }),
        { slot: "title", x: 120, y: 132, size: 18, def: "Capacitor with a dielectric" },
        ...callout("Mica, paper or oil between the plates", 150, 178, 420, 212, { maxRight: 620 }),
        { slot: "effect", x: 640, y: 300, size: 16, def: "The same charge, a lower p.d: C rises" },
        { slot: "note", x: 150, y: 545, size: 16, def: "C = k A / d for a parallel-plate capacitor" },
      ],
      series: [
        { p: [[140, 300], [330, 300]], width: 3 },
        ...capPlateOps(360, 255, 90, 60, { width: 5 }),
        { p: [[390, 300], [470, 300]], width: 3 },
        ...capPlateOps(500, 255, 90, 60, { width: 5 }),
        { p: [[530, 300], [610, 300]], width: 3 },
        ...capPlateOps(640, 255, 90, 60, { width: 5 }),
        { p: [[670, 300], [860, 300]], width: 3 },
        { text: "C1", x: 340, y: 372, size: 16, color: "#BFB09C" },
        { text: "C2", x: 480, y: 372, size: 16, color: "#BFB09C" },
        { text: "C3", x: 620, y: 372, size: 16, color: "#BFB09C" },
        { slot: "title", x: 150, y: 140, size: 18, def: "Capacitors in series" },
        ...callout("One charge, shared by all three", 150, 200, 350, 250, { maxRight: 620 }),
        { slot: "rule", x: 150, y: 470, size: 16, def: "1/C = 1/C1 + 1/C2 + 1/C3" },
        { slot: "pds", x: 150, y: 512, size: 15, color: "#BFB09C", def: "V = V1 + V2 + V3" },
      ],
      parallel: [
        { p: [[220, 170], [220, 450]], width: 3 },
        { p: [[760, 170], [760, 450]], width: 3 },
        { p: [[220, 170], [150, 170]], width: 3 },
        { p: [[760, 170], [830, 170]], width: 3 },
        { p: [[220, 200], [460, 200]], width: 3 },
        ...capPlateOps(480, 170, 60, 40, { width: 5 }),
        { p: [[500, 200], [760, 200]], width: 3 },
        { p: [[220, 310], [460, 310]], width: 3 },
        ...capPlateOps(480, 280, 60, 40, { width: 5 }),
        { p: [[500, 310], [760, 310]], width: 3 },
        { p: [[220, 420], [460, 420]], width: 3 },
        ...capPlateOps(480, 390, 60, 40, { width: 5 }),
        { p: [[500, 420], [760, 420]], width: 3 },
        { text: "C1", x: 520, y: 178, size: 15, color: "#BFB09C" },
        { text: "C2", x: 520, y: 288, size: 15, color: "#BFB09C" },
        { text: "C3", x: 520, y: 398, size: 15, color: "#BFB09C" },
        { slot: "title", x: 120, y: 120, size: 18, def: "Capacitors in parallel" },
        { slot: "rule", x: 120, y: 510, size: 16, def: "C = C1 + C2 + C3" },
        { slot: "pds", x: 560, y: 510, size: 15, color: "#BFB09C", def: "Each branch sees the same V" },
      ],
      variable: [
        { p: [[410, 205], [550, 205]], width: 5 },
        { p: [[420, 205], [420, 315]], width: 4 },
        { p: [[480, 205], [480, 315]], width: 4 },
        { p: [[540, 205], [540, 315]], width: 4 },
        { p: [[550, 205], [680, 205]], width: 3 },
        { p: [[440, 345], [580, 345]], width: 5 },
        { p: [[450, 235], [450, 345]], width: 4, color: "#74A98D" },
        { p: [[510, 235], [510, 345]], width: 4, color: "#74A98D" },
        { p: [[570, 235], [570, 345]], width: 4, color: "#74A98D" },
        { p: [[440, 345], [330, 345]], width: 3 },
        ...arrowOps(700, 360, 700, 240, { color: "#FF6B57", width: 2, head: 11 }),
        { arc: { cx: 660, cy: 300, r: 44, a0: -60, a1: 60 }, color: "#FF6B57", width: 2 },
        { slot: "title", x: 120, y: 132, size: 18, def: "Variable capacitor" },
        ...callout("Fixed plates", 700, 168, 552, 206, { maxRight: 930 }),
        ...callout("Plates that turn", 700, 380, 586, 348, { maxRight: 930 }),
        { slot: "note", x: 150, y: 470, size: 16, def: "Turning meshes the plates: area and C change" },
      ],
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

let _scale = 1; // CSS pixels per board unit, set by setupBoard

function setupBoard(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth - 20;
  const cssHeight = Math.round((cssWidth * BOARD_H) / BOARD_W);
  canvas.width = cssWidth * dpr;
  canvas.height = cssHeight * dpr;
  // The box is sized by CSS (aspect-ratio on canvas#board), never from here: writing
  // style.height after the first paint is what made everything below the board jump.
  const ctx = canvas.getContext("2d");
  _scale = cssWidth / BOARD_W;
  ctx.setTransform(_scale * dpr, 0, 0, (cssHeight / BOARD_H) * dpr, 0, 0);
  return ctx;
}

// A stroke width in board units, floored so it is never less than MIN_CHALK_CSS_PX
// of real screen. Units, not pixels — the ctx transform does the scaling.
function chalkWidth(w) {
  return Math.max(w || 2, MIN_CHALK_CSS_PX / (_scale || 1));
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

function pushStroke(items, pts, color, width, tRef, dash) {
  if (!pts || pts.length < 2) return tRef.t;
  const dur = clampDur(strokeLen(pts));
  items.push({ kind: "stroke", pts, color: color || CHALK, width: width || 3, start: tRef.t, dur, dash });
  tRef.t += dur;
  return tRef.t;
}

function pushLabel(items, text, x, y, size, color, tRef) {
  items.push({ kind: "label", a: { text, x, y, size: size || 17, color: color || CHALK_DIM }, start: tRef.t, dur: 0.28 });
  tRef.t += 0.24;
}

// Expand a template (base ops + chosen variant) into timeline items.
function templateItems(name, labels, variant, tRef, items) {
  const tpl = TEMPLATES[name];
  if (!tpl) return;
  const ops = tpl.ops.concat((variant && tpl.variants && tpl.variants[variant]) || []);
  const L = labels || {};
  for (const op of ops) {
    // An op may name variants it must not appear in — the base circuit draws a closed
    // switch so it never reads as a broken loop, and switch_open replaces that bar.
    if (op.except && variant && op.except.indexOf(variant) >= 0) continue;
    if (op.g && GLYPHS[op.g]) {
      const size = op.size || 160, k = size / 100;
      for (const s of GLYPHS[op.g]) {
        const pts = s.pts.map(([px, py]) => [op.x + px * k, op.y + py * k]);
        pushStroke(items, pts, op.color || s.color, s.width || 3, tRef, op.dash);
      }
    } else if (op.p) {
      pushStroke(items, op.p, op.color, op.width, tRef, op.dash);
    } else if (op.arc) {
      const a = op.arc;
      pushStroke(items, arcPts(a.cx, a.cy, a.r, rad(a.a0 == null ? 0 : a.a0), rad(a.a1 == null ? 360 : a.a1), a.n || 40), op.color, op.width, tRef, op.dash);
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
  ctx.lineWidth = chalkWidth(4);
  ctx.setLineDash([10, 7]);
  if (a.shape === "circle") {
    ctx.beginPath(); ctx.arc(a.cx, a.cy, a.r, 0, Math.PI * 2); ctx.stroke();
  } else {
    ctx.strokeRect(a.x, a.y, a.w, a.h);
  }
  ctx.restore();
}

function drawGrid(ctx) {
  ctx.strokeStyle = "rgba(244, 235, 221, 0.05)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= BOARD_W; x += 50) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, BOARD_H); ctx.stroke();
  }
  for (let y = 0; y <= BOARD_H; y += 50) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(BOARD_W, y); ctx.stroke();
  }
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
  ctx.fillStyle = BOARD_BG;
  ctx.fillRect(0, 0, BOARD_W, BOARD_H);
  drawGrid(ctx);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const item of timeline.items) {
    if (t < item.start) break;
    const p = Math.min(1, (t - item.start) / item.dur);
    if (item.kind === "stroke") {
      const budget = p * strokeLen(item.pts);
      ctx.strokeStyle = item.color;
      ctx.lineWidth = chalkWidth(item.width);
      ctx.setLineDash(item.dash || []);
      partialStroke(ctx, item.pts, budget);
      if (p > 0 && p < 1) {
        const tip = pointAtBudget(item.pts, budget);
        ctx.save();
        ctx.fillStyle = CHALK;
        ctx.beginPath(); ctx.arc(tip[0], tip[1], Math.max(3.2, 1.5 / _scale), 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }
    } else if (item.kind === "label") {
      drawLabel(ctx, item.a, p);
    } else if (item.kind === "hl") {
      drawHighlight(ctx, item, p);
    }
  }
}
