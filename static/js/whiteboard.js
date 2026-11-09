// The board's hand.
//
// A lesson script (the schema tutor.py writes) is a flat list of ops: say this, write
// this, ring that, tick the answer. planScript() measures the real glyphs of every write
// and turns the script into SCENES — one sheet of board each — with an absolute time on
// the lesson clock for every character and every stroke. The spoken line owns those
// times: a sheet's ink is stretched or squeezed so it lands while its sentence is being
// said, which is what makes the two read as one teacher rather than a slideshow with a
// voiceover laid on top.
//
// render(t) is the only thing that draws, and it is pure: the same t always gives the
// same board. Pause, scrub, replay, interrupt and resume are therefore all the same call
// with a different number, and no animation can be left running behind one.

const WB_NS = "http://www.w3.org/2000/svg";
const WB_ENGINE = 2;

const WB_LAYOUT = {
  left: 60,
  right: 940,
  rowTop: 112,
  rowGap: 110,
  rows: 5,
  size: 54,
  noteSize: 34,
  sheetGap: 0.6,          // seconds of quiet reading time between sheets
};

// Per-character cadence in seconds, uneven on purpose: a hand is not a printer.
const WB_CADENCE = {
  char: [0.058, 0.112],
  space: [0.046, 0.12],
  punct: 0.11,            // , ; . ! each cost a small pause
  label: [0.014, 0.028],  // diagram labels: still written, just quickly
};

const WB_INK = "var(--wb-ink)";
const WB_ACCENT = "var(--wb-accent)";
const WB_HAND = "'Caveat','Segoe Print','Bradley Hand',cursive";
const WB_PUNCT = ",;:.!?";
const WB_CORAL = "#FF6B57";

function wbRnd(a, b) { return a + Math.random() * (b - a); }
function wbEase(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
function wbClamp(v, a, b) { return v < a ? a : v > b ? b : v; }
// The board has two colours, so a diagram's four-chalk palette collapses: coral is the
// one thing to look at, everything else is the working.
function wbRole(color) { return color === WB_CORAL || color === "accent" ? "accent" : "ink"; }
function wbPaint(role) { return role === "accent" ? WB_ACCENT : WB_INK; }
function wbLen(pts) {
  let l = 0;
  for (let i = 1; i < pts.length; i++) {
    l += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return l;
}

class Whiteboard {
  constructor(svg, opts = {}) {
    this.o = Object.assign({}, WB_LAYOUT, opts);
    this.svg = svg;
    this.plan = null;
    this.sceneAt = -1;
    this.nodes = [];
    this.items = [];
    this.reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    svg.setAttribute("viewBox", `0 0 ${BOARD_W} ${BOARD_H}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.innerHTML = `
      <defs>
        <filter id="wb-rough" x="-4%" y="-4%" width="108%" height="108%">
          <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="4" result="n"/>
          <feDisplacementMap in="SourceGraphic" in2="n" scale="2.4"/>
        </filter>
      </defs>
      <g class="wb-layer" filter="url(#wb-rough)"></g>
      <g class="wb-eraser" opacity="0" aria-hidden="true">
        <rect x="-20" y="-34" width="${BOARD_W + 40}" height="34" rx="8"
              style="fill:var(--wb-ink)" fill-opacity="0.12"/>
      </g>
      <g class="wb-pen" opacity="0" aria-hidden="true">
        <circle r="11" fill="none" style="stroke:var(--wb-accent)" stroke-opacity="0.3" stroke-width="2"/>
        <circle r="5.5" style="fill:var(--wb-accent)"/>
      </g>
      <g class="wb-measure" opacity="0" aria-hidden="true">
        <text x="0" y="0" style="font-family:${WB_HAND};font-weight:600"></text>
      </g>`;
    this.layer = svg.querySelector(".wb-layer");
    this.pen = svg.querySelector(".wb-pen");
    this.eraser = svg.querySelector(".wb-eraser");
    this.meas = svg.querySelector(".wb-measure text");
  }

  // Glyph measurement is only meaningful once the hand itself has loaded, or every mark
  // lands against the fallback font's widths.
  async ready() {
    if (!document.fonts) return;
    try {
      await Promise.all([document.fonts.load("600 54px Caveat"),
                         document.fonts.load("600 34px Caveat")]);
      await document.fonts.ready;
    } catch (e) { /* measure against whatever font did resolve */ }
  }

  // ---------- planning ----------

  planScript(script) {
    const scenes = [];
    let scene = this._scene("");
    let beat = this._beat(scene);
    for (const op of ((script && script.steps) || [])) {
      if (op.op === "caption") {
        if (this._has(scene)) { scenes.push(scene); scene = this._scene(String(op.text || "")); beat = this._beat(scene); }
        else scene.caption = String(op.text || "");
        continue;
      }
      if (op.op === "say") {
        if (beat.text && beat.items.length) beat = this._beat(scene);
        else if (beat.text) { scene.beats.pop(); beat = this._beat(scene); }
        beat.text = String(op.text || "").trim();
        beat.dur = wbClamp(Number(op.dur) || this._estimate(beat.text), 0.8, 45);
        beat.speech = beat.dur;          // how long the line takes to say, before reading room
        continue;
      }
      if (op.op === "pause") { beat.pause = (beat.pause || 0) + (Number(op.ms) || 400) / 1000; continue; }
      if (op.op === "write" && scene.nextRow >= this.o.rows) {
        scenes.push(scene); scene = this._scene(""); beat = this._beat(scene);
      }
      const built = this._build(op, scene);
      if (built) for (const it of built) { it.beat = beat; beat.items.push(it); }
    }
    if (this._has(scene)) scenes.push(scene);
    this.plan = this._timed(scenes.filter((s) => this._has(s)));
    this.sceneAt = -1;
    return this.plan;
  }

  _scene(caption) {
    return { caption: String(caption || "").trim(), items: [], placed: [], beats: [], nextRow: 0 };
  }

  /** A sheet with nothing on it is not a sheet: it would erase the board for no reason. */
  _has(scene) {
    for (const b of scene.beats) if (b.items.length) return true;
    return false;
  }

  _beat(scene) {
    const b = { scene, text: "", dur: 0, items: [], pause: 0, t0: 0 };
    scene.beats.push(b);
    return b;
  }

  _estimate(text) { return wbClamp(String(text).length / 14.5, 1.2, 40); }

  /** Fold every beat's spoken line and ink onto one absolute clock. */
  _timed(scenes) {
    const beats = [];
    let t = 0;
    for (const scene of scenes) {
      scene.t0 = t;
      for (const beat of scene.beats) {
        const natural = beat.items.reduce((s, it) => s + it.dur, 0) + (beat.pause || 0);
        // Ink slower than the sentence is stretched to fill it; ink quicker than the
        // sentence gets the room, so the pen never races the voice.
        const span = Math.max(natural, beat.dur + (beat.text ? 0.3 : 0));
        const stretch = natural > 0 ? span / natural : 1;
        let at = 0;
        for (const it of beat.items) {
          const own = it.dur;
          it.t0 = at * stretch;
          it.dur = own * stretch;
          if (it.chars) for (const c of it.chars) c.t *= stretch;
          at += own;
        }
        beat.t0 = t;
        beat.dur = span;
        beats.push(beat);
        t += span;
      }
      scene.t1 = t;
      scene.items = [];
      for (const b of scene.beats) for (const it of b.items) { it.t0 += b.t0; scene.items.push(it); }
      t += this.o.sheetGap;
    }
    const cues = [];
    for (const s of scenes) {
      if (s.caption) cues.push({ at: s.t0, end: s.t1, text: s.caption, kind: "caption" });
      for (const b of s.beats) if (b.text) cues.push({ at: b.t0, end: b.t0 + b.dur, text: b.text, kind: "say" });
    }
    cues.sort((a, b) => a.at - b.at);
    return { scenes, beats, cues, total: Math.max(0.2, t - this.o.sheetGap) };
  }

  // ---------- geometry per op ----------

  _build(op, scene) {
    switch (op.op) {
      case "write": return this._write(op, scene);
      case "ring": return this._ring(op, scene);
      case "underline": return this._underline(op, scene);
      case "arrow": return this._arrow(op, scene);
      case "tick": return this._tick(op, scene);
      case "figure": return this._figure(op, scene);
      case "actions": return wbLegacyItems(this, op.actions, scene);
      default: return null;
    }
  }

  /** Rows are counted, not measured: the engine owns the sheet, the script owns the order. */
  _rowY(scene, op) {
    let row = op.row == null ? scene.nextRow : wbClamp(Math.round(Number(op.row)), 0, this.o.rows - 1);
    row = wbClamp(row, 0, this.o.rows - 1);
    scene.nextRow = Math.max(scene.nextRow, row + 1);
    return this.o.rowTop + row * this.o.rowGap + (Number(op.dy) || 0);
  }

  _write(op, scene) {
    const text = String(op.text || "");
    if (!text.trim()) return null;
    const size = Number(op.size) || this.o.size;
    const x = op.x == null ? this.o.left : Number(op.x);
    const maxW = Math.max(140, (op.w ? Number(op.x) + Number(op.w) : this.o.right) - x);
    const y = this._rowY(scene, op);
    const color = wbRole(op.color);
    const rot = Number(op.rot) || 0;
    const lines = this._measure(text, size, maxW);
    const items = lines.map((line, i) =>
      this._textItem(line, x, y + i * size * 1.16, size, rot, color, op.id));
    // Index every line under its id so a later mark can find its words wherever they
    // ended up after wrapping.
    if (op.id) for (const it of items) scene.placed.push(it);
    return items;
  }

  /** Split a write into sheet-width lines. */
  _measure(text, size, maxWidth) {
    const node = this.meas;
    node.setAttribute("font-size", size);
    node.textContent = text;
    let n = 0;
    try { n = node.getNumberOfChars(); } catch (e) { n = 0; }
    if (!n) { node.textContent = ""; return []; }
    const ext = [];
    for (let i = 0; i < n; i++) {
      const e = node.getExtentOfChar(i);
      ext.push({ x: e.x, w: e.width });
    }
    const ranges = [];
    let start = 0, lastSpace = -1;
    for (let i = 0; i < n; i++) {
      if (text[i] === " ") lastSpace = i;
      if (ext[i].x + ext[i].w - ext[start].x > maxWidth && i > start) {
        const cut = lastSpace > start ? lastSpace : i;
        ranges.push([start, cut]);
        start = text[cut] === " " ? cut + 1 : cut;
        lastSpace = -1;
      }
    }
    ranges.push([start, n]);
    return ranges.map(([a, b]) => {
      const slice = text.slice(a, b);
      node.textContent = slice;
      let m = 0;
      try { m = node.getNumberOfChars(); } catch (e) { m = 0; }
      const chars = [];
      let x0 = 0, width = 0;
      for (let i = 0; i < m; i++) {
        const e = node.getExtentOfChar(i);
        if (i === 0) x0 = e.x;
        chars.push({ x0: e.x - x0, x1: e.x + e.width - x0 });
        width = Math.max(width, e.x + e.width - x0);
      }
      node.textContent = "";
      return { text: slice, chars, width };
    }).filter((l) => l.text.length);
  }

  _textItem(line, x, y, size, rot, color, id) {
    const scale = this.reduced ? 0.004 : 1;
    const quick = size < this.o.noteSize * 0.95;
    let t = 0;
    const chars = line.chars.map((c, i) => {
      const ch = line.text[i] || "";
      const band = quick ? WB_CADENCE.label : WB_CADENCE.char;
      let ms = ch === " " ? wbRnd(WB_CADENCE.space[0], WB_CADENCE.space[1]) : wbRnd(band[0], band[1]);
      if (WB_PUNCT.includes(ch)) ms += WB_CADENCE.punct;
      t += ms * scale;
      return { x0: x + c.x0, x1: x + c.x1, ch, t };
    });
    return { kind: "text", id, x, y, size, rot, color, text: line.text, chars,
             dur: Math.max(0.08, t * scale), width: line.width, span: {}, shown: -1 };
  }

  /** Where a phrase actually landed: measured off the glyphs, never guessed. */
  _span(item, find) {
    const text = item.text || "";
    if (!find) {
      const a = item.chars[0], b = item.chars[item.chars.length - 1];
      return a && b ? { x0: a.x0, x1: b.x1, cx: (a.x0 + b.x1) / 2, y: item.y, size: item.size } : null;
    }
    const i = text.indexOf(find);
    if (i < 0 || !item.chars.length) return null;
    const a = item.chars[Math.max(0, i)];
    const b = item.chars[Math.min(item.chars.length - 1, i + find.length - 1)];
    return { x0: a.x0, x1: b.x1, cx: (a.x0 + b.x1) / 2, y: item.y, size: item.size };
  }

  /** A mark may target a wrapped write: use the line that actually holds the phrase. */
  _target(scene, id, find) {
    const lines = scene.placed.filter((it) => it.kind === "text" && it.id === id);
    if (!lines.length) return null;
    if (!find) return lines[0];
    for (const it of lines) if (it.text.indexOf(find) >= 0) return it;
    return null;
  }

  _ring(op, scene) {
    const it = this._target(scene, op.target, op.find);
    const s = it && this._span(it, String(op.find == null ? "" : op.find));
    if (!s) return null;
    const pts = this._ringPts(s.cx, s.y - s.size * 0.24,
      Math.max(22, (s.x1 - s.x0) / 2 + 18), Math.max(24, s.size * 0.62));
    return [this._strokeItem(pts, wbRole(op.color), 4, 0.7, true)];
  }

  _underline(op, scene) {
    const it = this._target(scene, op.target, op.find);
    const s = it && this._span(it, String(op.find == null ? "" : op.find));
    if (!s) return null;
    const y = s.y + 16;
    return [this._strokeItem(this._linePts(s.x0 - 5, y, s.x1 + 5, y + wbRnd(1, 4)),
                            wbRole(op.color), 4.5, 0.52, true)];
  }

  _tick(op, scene) {
    const it = this._target(scene, op.target, null);
    if (!it) return null;
    const last = it.chars[it.chars.length - 1];
    const lines = scene.placed.filter((x) => x.kind === "text" && x.id === op.target);
    const end = lines.length ? lines[lines.length - 1].chars.slice(-1)[0] : last;
    const x = end.x1, y = (lines.length ? lines[lines.length - 1].y : it.y) - 12;
    return [this._strokeItem([[x + 20, y], [x + 31, y + 17], [x + 39, y + 6],
                              [x + 64, y - 31], [x + 70, y - 38]], "accent", 5, 0.42, true)];
  }

  _arrow(op, scene) {
    const to = this._target(scene, op.target, op.find);
    const s = to && this._span(to, String(op.find == null ? "" : op.find));
    if (!s) return null;
    const from = op.from ? this._target(scene, op.from, null) : null;
    const fx = from ? Math.max(this.o.left, from.x - 16) : s.cx - 30;
    const fy = from ? from.y - from.size * 0.55 : s.y - 96;
    return [this._strokeItem(this._arrowPts(fx, fy, s.cx + 2, s.y + 26), "accent", 3.5, 0.55, true)];
  }

  // ---------- stroke shapes ----------

  /** Never a perfect ellipse: it overshoots, wobbles and starts at a random angle. */
  _ringPts(cx, cy, rx, ry) {
    const P = [], ph = wbRnd(0, 6), a0 = wbRnd(-2.6, -1.8);
    for (let i = 0; i <= 80; i++) {
      const t = i / 80, a = a0 + t * Math.PI * 2 * 1.12;
      const k = 1 + 0.04 * Math.sin(3 * a + ph) + t * 0.07;
      P.push([cx + rx * k * Math.cos(a), cy + ry * k * Math.sin(a)]);
    }
    return P;
  }

  _linePts(x1, y1, x2, y2, n = 26, j = 1.6) {
    const P = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      P.push([x1 + (x2 - x1) * t + wbRnd(-j, j),
              y1 + (y2 - y1) * t + Math.sin(t * 3.1) * 2.2 + wbRnd(-j, j)]);
    }
    return P;
  }

  _arrowPts(x1, y1, x2, y2) {
    const P = [], n = 30, bx = (x1 + x2) / 2 - 26, by = (y1 + y2) / 2;
    for (let i = 0; i <= n; i++) {
      const t = i / n, u = 1 - t;
      P.push([u * u * x1 + 2 * u * t * bx + t * t * x2, u * u * y1 + 2 * u * t * by + t * t * y2]);
    }
    const a = Math.atan2(y2 - by, x2 - bx), h = 16, w = 0.5;
    return P.concat([[x2 - h * Math.cos(a - w), y2 - h * Math.sin(a - w)], [x2, y2],
                     [x2 - h * Math.cos(a + w), y2 - h * Math.sin(a + w)]]);
  }

  _strokeItem(pts, color, width, dur, mark) {
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const len = cum[cum.length - 1] || 1;
    return { kind: "stroke", pts, cum, len, color: wbRole(color), width: width || 3, mark: !!mark,
             dur: this.reduced ? 0.02 : Math.max(0.1, dur || 0.5) };
  }

  // ---------- diagrams (their geometry lives in diagrams.js) ----------

  _figure(op, scene) {
    const geo = op.name ? wbTemplateGeometry(op.name, op.labels, op.variant)
                       : wbGlyphGeometry(op.glyph, op.size);
    if (!geo) return null;
    const x0 = op.x == null ? this.o.left : Number(op.x);
    // A script that names a row gets the sheet's row; a translated board that already
    // knows its own coordinates keeps them, or the apparatus would be pushed down.
    const fromRow = !(op.row == null && op.y != null);
    const y0 = fromRow ? this._rowY(scene, op) : Number(op.y);
    // A drawing fills the sheet from its row to the bench, so nothing can be written under
    // it: the next line of working has to start a fresh sheet.
    if (fromRow) scene.nextRow = this.o.rows;
    const boxW = (op.w ? Number(op.w) : this.o.right - x0);
    const boxH = (op.h ? Number(op.h) : BOARD_H - y0 - 24);
    const bb = geo.box;
    // A diagram is never allowed to hang off the bottom of the sheet: whatever the script
    // asked for, the box shrinks to the room that is actually left.
    const room = Math.max(60, BOARD_H - y0 - 20);
    const h = Math.min(boxH, room);
    // A drawing that only fills a tenth of the space it was given is a dot, not a
    // diagram, so a small one is scaled up to fill the box. Stroke weight grows far more
    // slowly than the shape: chalk does not get thicker because you drew a bigger circle.
    const k = Math.min(boxW / Math.max(1, bb.w), h / Math.max(1, bb.h), 2.2);
    const ink = Math.min(k, 1.45);
    const ox = x0 + (boxW - bb.w * k) / 2 - bb.x0 * k;
    const oy = y0 + (h - bb.h * k) / 2 - bb.y0 * k;
    const at = ([px, py]) => [ox + px * k, oy + py * k];
    const items = geo.strokes.map((s) => {
      const pts = s.pts.map(at);
      return this._strokeItem(pts, s.color, Math.max(2, s.width * ink),
                              wbClamp(wbLen(pts) / DRAW_SPEED, 0.12, 1.3));
    });
    for (const L of geo.labels) {
      const [lx, ly] = at([L.x, L.y]);
      const size = wbClamp(L.size * k * 1.55, 22, 44);
      for (const line of this._measure(L.text, size, Math.max(120, this.o.right - lx))) {
        const it = this._textItem(line, lx, ly, size, 0, L.color, null);
        it.fig = true;      // a diagram's own names: they crowd, they do not collide
        items.push(it);
      }
    }
    return items;
  }

  // ---------- rendering ----------

  render(t) {
    if (!this.plan || !this.plan.scenes.length) return;
    const time = wbClamp(t, 0, this.plan.total);
    let idx = 0;
    for (let i = 0; i < this.plan.scenes.length; i++) if (time >= this.plan.scenes[i].t0 - 1e-6) idx = i;
    const scene = this.plan.scenes[idx];
    if (idx !== this.sceneAt) this._mount(scene, idx > this.sceneAt && this.sceneAt >= 0);
    let pen = null;
    // Items carry an absolute time, so a sheet is painted from the lesson clock and not
    // from a per-sheet stop watch that would drift whenever a sheet was re-mounted.
    for (let i = 0; i < scene.items.length; i++) {
      const it = scene.items[i];
      const at = time - it.t0;
      const p = it.kind === "text" ? this._paintText(i, it, at) : this._paintStroke(i, it, at);
      if (p) pen = p;
    }
    this._pen(pen);
  }

  _mount(scene, advance) {
    this.sceneAt = this.plan.scenes.indexOf(scene);
    this.layer.textContent = "";
    this.nodes = [];
    this.items = scene.items;
    scene.items.forEach((it, i) => {
      this.nodes[i] = it.kind === "text" ? this._textNode(it) : this._strokeNode(it);
    });
    if (advance && !this.reduced) this._sweep();
  }

  _sweep() {
    // One wipe at a time: scrubbing across several sheets would otherwise leave a
    // superseded loop still writing the eraser's transform behind the new one.
    if (this._wipe) cancelAnimationFrame(this._wipe);
    const g = this.eraser;
    g.setAttribute("opacity", "1");
    const t0 = performance.now();
    const step = (now) => {
      if (g !== this.eraser || this._wipe !== id) return;
      const k = Math.min(1, (now - t0) / 360);
      g.setAttribute("transform", `translate(0 ${(k * (BOARD_H + 34)).toFixed(1)})`);
      if (k < 1) this._wipe = requestAnimationFrame(step);
      else { g.setAttribute("opacity", "0"); this._wipe = 0; }
    };
    const id = requestAnimationFrame(step);
    this._wipe = id;
  }

  _textNode(it) {
    const g = this._el("g", it.rot ? { transform: `rotate(${it.rot} ${it.x} ${it.y})` } : null, this.layer);
    const text = this._el("text", { x: it.x, y: it.y }, g);
    text.style.fontFamily = WB_HAND;
    text.style.fontWeight = "600";
    text.style.fill = wbPaint(it.color);
    const spans = it.chars.map((c) => {
      const s = this._el("tspan", {}, text);
      s.textContent = c.ch === " " ? " " : c.ch;
      s.style.visibility = "hidden";
      return s;
    });
    return { g, spans, shown: -1 };
  }

  _paintText(i, it, at) {
    const node = this.nodes[i];
    if (!node || !it.chars.length) return null;
    let shown = -1;
    if (at >= it.dur) shown = it.chars.length - 1;
    else if (at > 0) {
      for (let c = 0; c < it.chars.length; c++) {
        if (it.chars[c].t > at) break;
        shown = c;
      }
    }
    if (shown !== node.shown) {
      for (let c = node.shown + 1; c <= shown; c++) node.spans[c].style.visibility = "visible";
      for (let c = Math.max(shown + 1, 0); c < node.spans.length; c++) node.spans[c].style.visibility = "hidden";
      node.shown = shown;
    }
    if (shown < 0) return null;
    if (at >= it.dur) return null;          // the line is finished: the pen moves on
    const c = it.chars[shown];
    return { x: c.x1, y: it.y - it.size * 0.26 + Math.sin(at * 9 + i) * 3 };
  }

  _strokeNode(it) {
    const d = "M" + it.pts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("L");
    const path = this._el("path", {
      d, fill: "none", "stroke-width": it.width, "stroke-linecap": "round",
      "stroke-linejoin": "round", "stroke-dasharray": it.len.toFixed(1),
      "stroke-dashoffset": it.len.toFixed(1),
    }, this.layer);
    path.style.stroke = wbPaint(it.color);
    return { path, open: null };
  }

  _paintStroke(i, it, at) {
    const node = this.nodes[i];
    if (!node) return null;
    const k = at <= 0 ? 0 : at >= it.dur ? 1 : wbEase(at / it.dur);
    node.path.setAttribute("stroke-dashoffset", (it.len * (1 - k)).toFixed(2));
    if (k <= 0 || k >= 1) return null;
    return this._pointAt(it, it.len * k);
  }

  _pointAt(it, dist) {
    const cum = it.cum;
    let lo = 0, hi = cum.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= dist) lo = mid; else hi = mid;
    }
    const seg = Math.max(1e-6, cum[hi] - cum[lo]);
    const f = wbClamp((dist - cum[lo]) / seg, 0, 1);
    const a = it.pts[lo], b = it.pts[hi];
    return { x: a[0] + (b[0] - a[0]) * f, y: a[1] + (b[1] - a[1]) * f };
  }

  _pen(p) {
    if (!p) { this.pen.setAttribute("opacity", "0"); return; }
    this.pen.setAttribute("opacity", "1");
    this.pen.setAttribute("transform", `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
  }

  _el(tag, attrs, parent) {
    const e = document.createElementNS(WB_NS, tag);
    if (attrs) for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    (parent || this.layer).appendChild(e);
    return e;
  }

  /** The line being spoken at this instant, for the caption strip. */
  cue(t, kind) {
    if (!this.plan) return null;
    let cue = null;
    for (const c of this.plan.cues) {
      if (kind && c.kind !== kind) continue;
      if (c.at <= t + 1e-6) cue = c;
    }
    return cue;
  }

  /** Where every spoken line starts and how long it should hold the board. */
  schedule() {
    if (!this.plan) return [];
    const out = [];
    for (const s of this.plan.scenes) {
      for (const b of s.beats) {
        if (b.text) out.push({ at: b.t0, dur: b.dur, speech: b.speech || b.dur,
                               end: b.t0 + b.dur, text: b.text });
      }
    }
    return out;
  }
}

// ---------- diagram geometry ----------

/** A template's fixed geometry, flattened into strokes and labels in board units. */
function wbTemplateGeometry(name, labels, variant) {
  const tpl = typeof TEMPLATES !== "undefined" && TEMPLATES[name];
  if (!tpl) return null;
  const cases = tpl.variants || {};
  // A script that names no case still gets a whole drawing rather than an empty frame:
  // every template says which of its cases to show by default.
  const v = cases[variant] ? variant : (cases[tpl.default_variant] ? tpl.default_variant
                                       : Object.keys(cases)[0]);
  const ops = (tpl.ops || []).concat(v ? cases[v] : []);
  const L = labels || {};
  const strokes = [], text = [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const grow = (px, py) => {
    x0 = Math.min(x0, px); y0 = Math.min(y0, py);
    x1 = Math.max(x1, px); y1 = Math.max(y1, py);
  };
  const stroke = (pts, color, width) => {
    if (!pts || pts.length < 2) return;
    pts.forEach(grow);
    strokes.push({ pts, color: wbRole(color), width: width || 3 });
  };
  for (const op of ops) {
    if (op.except && variant && op.except.indexOf(variant) >= 0) continue;
    if (op.g && typeof GLYPHS !== "undefined" && GLYPHS[op.g]) {
      const size = op.size || 160, k = size / 100;
      for (const s of GLYPHS[op.g]) {
        stroke(s.pts.map(([px, py]) => [op.x + px * k, op.y + py * k]), op.color || s.color, s.width || 3);
      }
    } else if (op.p) {
      stroke(op.p, op.color, op.width);
    } else if (op.arc) {
      const a = op.arc;
      stroke(arcPts(a.cx, a.cy, a.r, rad(a.a0 == null ? 0 : a.a0), rad(a.a1 == null ? 360 : a.a1), a.n || 40),
             op.color, op.width);
    } else if (op.slot || op.text) {
      const t = String((op.slot && L[op.slot] != null && String(L[op.slot]).trim())
        ? L[op.slot] : (op.text || op.def || "")).trim();
      if (!t) continue;
      const size = op.size || 18;
      grow(op.x - 8, op.y - size * 0.9);
      grow(op.x + t.length * size * 0.58 + 8, op.y + size * 0.4);
      text.push({ text: t, x: op.x, y: op.y, size, color: wbRole(op.color) });
    }
  }
  if (!strokes.length && !text.length) return null;
  if (!isFinite(x0)) { x0 = 0; y0 = 0; x1 = BOARD_W; y1 = BOARD_H; }
  return { strokes, labels: text, box: { x0: x0 - 12, y0: y0 - 12, x1: x1 + 12, y1: y1 + 12,
                                         w: x1 - x0 + 24, h: y1 - y0 + 24 } };
}

function wbGlyphGeometry(glyph, size) {
  const g = typeof GLYPHS !== "undefined" ? GLYPHS[glyph] : null;
  if (!g) return null;
  const strokes = g.map((s) => ({ pts: s.pts, color: wbRole(s.color), width: s.width || 3 }));
  const box = { x0: 0, y0: 0, x1: 100, y1: 100, w: 100, h: 100 };
  return { strokes, labels: [], box };
}

// ---------- legacy vocabulary: lessons saved before the script schema ----------
//
// Older boards (and the guided paper walkthrough) speak {type:"template"|"draw"|
// "label"|"highlight"} actions in absolute board coordinates. Rather than keep a second
// renderer alive, that vocabulary is translated into this engine's items, so one hand
// writes everywhere and one clock plays both schemas.

function wbGlyphItems(wb, op) {
  const geo = wbGlyphGeometry(op.glyph, op.size);
  if (!geo) return [];
  const k = (op.size || 160) / 100;
  return geo.strokes.map((s) => {
    const pts = s.pts.map(([px, py]) => [op.x + px * k, op.y + py * k]);
    return wb._strokeItem(pts, op.color || s.color, (s.width || 3),
                          wbClamp(wbLen(pts) / DRAW_SPEED, 0.14, 1.3));
  });
}

/** One legacy step's actions, measured into the scene the engine is building. */
function wbLegacyItems(wb, actions, scene) {
  const items = [];
  for (const a of actions || []) {
    if (a.type === "template") {
      const built = wb._figure({ name: a.template, labels: a.labels, variant: a.variant,
                                 x: 20, y: 24, w: BOARD_W - 40, h: BOARD_H - 44 }, scene);
      if (built) items.push(...built);
    } else if (a.type === "draw" && a.glyph) {
      items.push(...wbGlyphItems(wb, a));
    } else if (a.type === "draw" && a.shape === "circle") {
      items.push(wb._strokeItem(arcPts(a.cx, a.cy, a.r, 0, rad(360)), a.color, a.width || 3, 0.6));
    } else if (a.type === "draw" && a.shape === "rect") {
      items.push(wb._strokeItem([[a.x, a.y], [a.x + a.w, a.y], [a.x + a.w, a.y + a.h],
                                 [a.x, a.y + a.h], [a.x, a.y]], a.color, a.width || 3, 0.7));
    } else if (a.type === "draw" && (a.shape === "path" || a.shape === "line" || a.shape === "arrow")) {
      let pts = a.shape === "path" ? (a.points || []) : [[a.x1, a.y1], [a.x2, a.y2]];
      if (a.shape === "arrow") {
        const ang = Math.atan2(a.y2 - a.y1, a.x2 - a.x1), h = 14;
        pts = [pts[0], [a.x2, a.y2], [a.x2 - h * Math.cos(ang - 0.45), a.y2 - h * Math.sin(ang - 0.45)],
               [a.x2, a.y2], [a.x2 - h * Math.cos(ang + 0.45), a.y2 - h * Math.sin(ang + 0.45)]];
      }
      items.push(wb._strokeItem(pts, a.color, a.width || 3, wbClamp(wbLen(pts) / DRAW_SPEED, 0.14, 1.3)));
    } else if (a.type === "highlight") {
      items.push(wb._strokeItem(wb._ringPts(a.cx, a.cy, a.r || 40, (a.r || 40) * 0.8), "accent", 4, 0.7));
    } else if (a.type === "label") {
      const built = wb._write({ text: String(a.text || ""), x: a.x, row: 0,
                                dy: (a.y || WB_LAYOUT.rowTop) - WB_LAYOUT.rowTop,
                                size: (a.size || 17) * 1.5, color: a.color }, scene);
      if (built) items.push(...built);
    }
  }
  return items;
}

/** A legacy step becomes a one-sheet plan whose clock is the step's own drawing time. */
function wbLegacyPlan(wb, step) {
  const scene = { caption: "", items: [], beats: [], nextRow: 0, t0: 0 };
  const beat = { scene, text: "", dur: 0, items: [], pause: 0, t0: 0 };
  const items = wbLegacyItems(wb, step && step.actions, scene);
  items.forEach((it) => beat.items.push(it));
  scene.items = items;
  scene.beats = [beat];
  let t = 0;
  for (const it of items) { it.t0 = t; t += it.dur; }
  beat.dur = t;
  scene.t1 = t;
  return { scenes: [scene], beats: [beat], cues: [], total: Math.max(0.2, t) };
}

/**
 * Any stored lesson, in either schema, as a script the engine can plan.
 *
 * A script is {steps:[{op:…}]}. A legacy lesson is {steps:[{narration, actions, concept}]}:
 * its spoken line becomes a say, its board actions ride along untranslated, and each step
 * gets its own sheet of board — which is what a student already expects from it.
 */
function wbScriptFrom(lesson) {
  const steps = (lesson && lesson.steps) || [];
  if (steps.length && steps[0] && steps[0].op) return lesson;
  const ops = [];
  for (const s of steps) {
    const caption = (typeof CONCEPT_NAMES !== "undefined" && CONCEPT_NAMES[s.concept]) || "";
    ops.push({ op: "caption", text: caption });
    if (s.narration) ops.push({ op: "say", text: String(s.narration) });
    if (s.actions && s.actions.length) ops.push({ op: "actions", actions: s.actions });
  }
  return { title: (lesson && lesson.title) || "Lesson", steps: ops };
}

// ---------- the interface lesson.js and walkthrough.js drive ----------

let _board = null;

function initBoard(el) {
  // The board scales itself, so a resize must not rebuild it: a fresh engine would come
  // back with no plan and the player would be staring at an empty sheet.
  if (_board && _board.svg === el) return _board;
  _board = new Whiteboard(el);
  return _board;
}

function board() { return _board; }

function buildTimeline(step) {
  if (!_board) return { scenes: [], beats: [], cues: [], total: 0 };
  _board.plan = wbLegacyPlan(_board, step);
  _board.sceneAt = -1;
  return _board.plan;
}

function drawFrame(_plan, t) {
  if (_board) _board.render(t);
}
