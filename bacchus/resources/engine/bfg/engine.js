// ═══════════════════════ engine: hand-drawn ink on one white page ═══════════════════════
// Every frame is a pure function of the time t (seconds). Nothing here reads a clock.
'use strict';
const W = 1920, H = 1080;
const INK = '#1e1c1a', GREY = '#bcb8b1', PALE = '#ecebe7', PAPER = '#ffffff';
const GREEN = '#2e9b57', AMBER = '#f2a31b', BLUE = '#2f6fd6', RED = '#e0452f';
const WOOD = '#ead1a4', SOOT = '#3b3936', MOON = '#f7dc78', SKY = '#24345c', SAND = '#f3e4c4';
const LW = 5.4;                      // the one pen, in scene units (1080p)
const FONT = { k: 'Kalam', p: 'PatrickHand', c: 'CaveatSig' };
const FALLBACK = '"Comic Sans MS", "Segoe Print", cursive';

// ── math ────────────────────────────────────────────────────────────────────────────────
const clamp = (x, a = 0, b = 1) => x < a ? a : x > b ? b : x;
const lerp = (a, b, u) => a + (b - a) * u;
const ss = u => (u = clamp(u), u * u * (3 - 2 * u));
const eio = u => (u = clamp(u), u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
const eout = u => (u = clamp(u), 1 - Math.pow(1 - u, 3));
const ein = u => (u = clamp(u), u * u * u);
const pop = u => { u = clamp(u); const c = 1.9; return 1 + (c + 1) * Math.pow(u - 1, 3) + c * Math.pow(u - 1, 2); };
const D2R = Math.PI / 180;
function hash(n) { n = Math.imul((n | 0) ^ 0x9e3779b9, 0x85ebca6b); n ^= n >>> 13; n = Math.imul(n, 0xc2b2ae35); n ^= n >>> 16; return (n >>> 0) / 4294967296; }
function hstr(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return (h >>> 0) % 1000003; }
function noise(x) { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return lerp(hash(i) * 2 - 1, hash(i + 1) * 2 - 1, u); }
function rng(seed) { let a = (Math.floor(seed * 2654435761) >>> 0) || 1; return () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const rot = (x, y, a) => [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];

// ── geometry: flat arrays [x0,y0,x1,y1,...] ─────────────────────────────────────────────
function resample(a, step) {
  const out = [a[0], a[1]]; let carry = 0;
  for (let i = 2; i < a.length; i += 2) {
    const x0 = a[i - 2], y0 = a[i - 1], x1 = a[i], y1 = a[i + 1], d = Math.hypot(x1 - x0, y1 - y0);
    if (d === 0) continue;
    let s = step - carry;
    while (s <= d) { out.push(x0 + (x1 - x0) * s / d, y0 + (y1 - y0) * s / d); s += step; }
    carry = d - (s - step);
  }
  const n = a.length;
  if (Math.hypot(out[out.length - 2] - a[n - 2], out[out.length - 1] - a[n - 1]) > 0.5) out.push(a[n - 2], a[n - 1]);
  return out;
}
// a little hand tremor along the path: an offset along the normal from smooth noise of the arc length
function wob(a, seed, amp = 1.6, wl = 120, step = 6) {
  const r = resample(a, step), n = r.length / 2, out = new Float32Array(r.length);
  let s = 0;
  for (let i = 0; i < n; i++) {
    const x = r[2 * i], y = r[2 * i + 1];
    if (i > 0) s += Math.hypot(x - r[2 * i - 2], y - r[2 * i - 1]);
    const j0 = Math.max(0, i - 1), j1 = Math.min(n - 1, i + 1);
    let tx = r[2 * j1] - r[2 * j0], ty = r[2 * j1 + 1] - r[2 * j0 + 1]; const tl = Math.hypot(tx, ty) || 1; tx /= tl; ty /= tl;
    const o = amp * (0.72 * noise(s / wl + seed * 7.31) + 0.28 * noise(s / (wl * 0.31) + seed * 3.17));
    out[2 * i] = x - ty * o; out[2 * i + 1] = y + tx * o;
  }
  return out;
}
function spline(p, closed = false, seg = 10) { // Catmull-Rom through points
  const n = p.length / 2, out = [];
  const P = i => closed ? [p[2 * ((i + n) % n)], p[2 * ((i + n) % n) + 1]] : [p[2 * clamp(i, 0, n - 1)], p[2 * clamp(i, 0, n - 1) + 1]];
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i++) {
    const [x0, y0] = P(i - 1), [x1, y1] = P(i), [x2, y2] = P(i + 1), [x3, y3] = P(i + 2);
    for (let k = 0; k < seg; k++) {
      const t = k / seg, t2 = t * t, t3 = t2 * t;
      out.push(0.5 * (2 * x1 + (-x0 + x2) * t + (2 * x0 - 5 * x1 + 4 * x2 - x3) * t2 + (-x0 + 3 * x1 - 3 * x2 + x3) * t3),
        0.5 * (2 * y1 + (-y0 + y2) * t + (2 * y0 - 5 * y1 + 4 * y2 - y3) * t2 + (-y0 + 3 * y1 - 3 * y2 + y3) * t3));
    }
  }
  const [xe, ye] = closed ? P(0) : P(n - 1); out.push(xe, ye);
  return out;
}
function arcPts(cx, cy, rx, ry, a0, a1, rotA = 0, wobR = 0, seed = 0) {
  const n = Math.max(12, Math.ceil(Math.abs(a1 - a0) * Math.max(rx, ry) / 7)), out = [];
  for (let i = 0; i <= n; i++) {
    const a = lerp(a0, a1, i / n), k = 1 + wobR * noise(a * 1.3 + seed * 5.1);
    let x = Math.cos(a) * rx * k, y = Math.sin(a) * ry * k;
    if (rotA) [x, y] = rot(x, y, rotA);
    out.push(cx + x, cy + y);
  }
  return out;
}

// ── items ────────────────────────────────────────────────────────────────────────────────
function mkStroke(pts, o = {}) {
  const n = pts.length / 2, cum = new Float32Array(n); let L = 0;
  for (let i = 1; i < n; i++) { L += Math.hypot(pts[2 * i] - pts[2 * i - 2], pts[2 * i + 1] - pts[2 * i - 1]); cum[i] = L; }
  return { k: 's', pts, cum, L, w: o.w ?? 1, col: o.col ?? INK, a: o.a ?? 1, dash: o.dash || null };
}
function tracePart(ctx, st, p) {
  const pts = st.pts, cum = st.cum, n = cum.length, target = p >= 1 ? 1e12 : p * st.L;
  ctx.moveTo(pts[0], pts[1]);
  for (let i = 1; i < n; i++) {
    if (cum[i] <= target) ctx.lineTo(pts[2 * i], pts[2 * i + 1]);
    else { const f = (target - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1); ctx.lineTo(lerp(pts[2 * i - 2], pts[2 * i], f), lerp(pts[2 * i - 1], pts[2 * i + 1], f)); break; }
  }
}
function drawStroke(ctx, st, p, lw, col) {
  if (p <= 0 || st.L <= 0) return;
  ctx.beginPath(); tracePart(ctx, st, p);
  ctx.lineWidth = lw * st.w; ctx.strokeStyle = col || st.col;
  if (st.dash) ctx.setLineDash(st.dash.map(d => d * lw / LW));
  const a = ctx.globalAlpha; if (st.a !== 1) ctx.globalAlpha = a * st.a;
  ctx.stroke(); ctx.globalAlpha = a;
  if (st.dash) ctx.setLineDash([]);
}
function bbox(pts) { let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; for (let i = 0; i < pts.length; i += 2) { x0 = Math.min(x0, pts[i]); x1 = Math.max(x1, pts[i]); y0 = Math.min(y0, pts[i + 1]); y1 = Math.max(y1, pts[i + 1]); } return [x0, y0, x1, y1]; }
function mkFill(pts, col, o = {}) { return { k: 'f', pts, col, a: o.a ?? 1, bb: bbox(pts), dx: o.dx ?? 3, dy: o.dy ?? 3, dir: o.dir || 'x', rings: o.rings || null }; }
function drawFill(ctx, f, p, col) {
  if (p <= 0) return;
  ctx.save();
  if (p < 1) {
    const [x0, y0, x1, y1] = f.bb, pad = 12; ctx.beginPath();
    if (f.dir === 'y') { const h = (y1 - y0 + 2 * pad) * p; ctx.rect(x0 - pad, y1 + pad - h, x1 - x0 + 2 * pad, h); }
    else ctx.rect(x0 - pad, y0 - pad, (x1 - x0 + 2 * pad) * p, y1 - y0 + 2 * pad);
    ctx.clip();
  }
  ctx.beginPath();
  for (const r of (f.rings || [f.pts])) { ctx.moveTo(r[0] + f.dx, r[1] + f.dy); for (let i = 2; i < r.length; i += 2) ctx.lineTo(r[i] + f.dx, r[i + 1] + f.dy); ctx.closePath(); }
  ctx.globalAlpha *= f.a; ctx.fillStyle = col || f.col; ctx.fill('evenodd');
  ctx.restore();
}
const TW = new Map();
function fontStr(o) { const f = o.font || 'k'; return `${f === 'k' || f === 'c' ? 700 : 400} ${o.size || 80}px ${FONT[f] || FONT.k}, ${FALLBACK}`; }
function textWidth(ctx, str, o) { const key = fontStr(o) + '|' + str; let w = TW.get(key); if (w === undefined) { ctx.save(); ctx.font = fontStr(o); w = ctx.measureText(str).width; ctx.restore(); TW.set(key, w); } return w; }
function drawText(ctx, it, p, col) {
  if (p <= 0) return;
  const lines = it.str.split('\n'), size = it.size || 80, lh = (it.lh || 1.12) * size;
  ctx.save(); ctx.font = fontStr(it); ctx.fillStyle = col || it.col || INK; ctx.textBaseline = 'alphabetic';
  if (it.a !== undefined && it.a !== 1) ctx.globalAlpha *= it.a;
  const widths = lines.map(l => textWidth(ctx, l, it)), total = widths.reduce((s, w) => s + w, 0) || 1;
  let acc = 0;
  lines.forEach((ln, i) => {
    const w = widths[i], y = it.y + i * lh, x = it.align === 'left' ? it.x : it.align === 'right' ? it.x - w : it.x - w / 2;
    const q = p >= 1 ? 1 : clamp((p * total - acc) / (w || 1)); acc += w;
    if (q <= 0) return;
    if (it.rot) { ctx.save(); ctx.translate(it.x, it.y); ctx.rotate(it.rot); ctx.translate(-it.x, -it.y); }
    if (q < 1) { ctx.save(); ctx.beginPath(); ctx.rect(x - 20, y - size * 1.2, w * q + 20, size * 1.7); ctx.clip(); }
    if (it.outline) { ctx.lineWidth = it.outline; ctx.strokeStyle = PAPER; ctx.lineJoin = 'round'; ctx.strokeText(ln, x, y); }
    ctx.fillText(ln, x, y);
    if (it.under) { const yy = y + size * 0.18; ctx.lineWidth = Math.max(3, size * 0.06); ctx.strokeStyle = it.under; ctx.beginPath(); ctx.moveTo(x, yy); ctx.lineTo(x + w * q, yy + 2); ctx.stroke(); }
    if (q < 1) ctx.restore();
    if (it.rot) ctx.restore();
  });
  ctx.restore();
}

// ── Sketch: a drawing made of strokes, fills and lettering, drawn on in order ───────────────
class Sketch {
  constructor(seed) { this.seed = seed; this.n = 0; this.seq = []; this.fills = []; this.Wt = 0; }
  _s() { return this.seed * 0.0137 + (this.n++) * 1.618; }
  _add(it, wt) { it.wt = wt; this.seq.push(it); return this; }
  stroke(pts, o = {}) { const st = mkStroke(o.raw ? Float32Array.from(pts) : wob(pts, this._s(), o.amp ?? 1.6, o.wl ?? 120), o); return this._add(st, st.L * (o.slow ?? 1)); }
  line(x1, y1, x2, y2, o = {}) { return this.stroke([x1, y1, x2, y2], o); }
  path(pts, o = {}) { return this.stroke(o.smooth ? spline(pts, false, o.seg || 8) : pts, o); }
  curve(pts, o = {}) { return this.stroke(spline(pts, false, o.seg || 10), o); }
  loop(pts, o = {}) { const p = o.smooth === false ? [...pts, pts[0], pts[1]] : spline(pts, true, o.seg || 10); if (o.fill) this.fill(p, o.fill, o); return this.stroke(p, o); }
  circle(cx, cy, r, o = {}) {
    const a0 = (hash(this.n * 31 + this.seed) * 2 - 1) * 1.2, over = o.over ?? 0.06;
    const pts = arcPts(cx, cy, r, o.ry ?? r, a0, a0 + Math.PI * 2 * (1 + over), o.rot || 0, o.wr ?? 0.025, this.seed + this.n);
    if (o.fill) this.fill(arcPts(cx, cy, r, o.ry ?? r, 0, Math.PI * 2, o.rot || 0), o.fill, o);
    return this.stroke(pts, { ...o, amp: o.amp ?? 0.6 });
  }
  arc(cx, cy, r, a0, a1, o = {}) { return this.stroke(arcPts(cx, cy, r, o.ry ?? r, a0, a1, o.rot || 0), o); }
  rect(x, y, w, h, o = {}) {
    if (o.fill) this.fill([x, y, x + w, y, x + w, y + h, x, y + h], o.fill, o);
    const e = o.over ?? 4; // a hand overshoots the corners a little
    if (o.one) return this.stroke([x, y, x + w, y, x + w, y + h, x, y + h, x, y - e * 0.5], o);
    return this.line(x - e, y, x + w + e, y, o).line(x + w, y - e, x + w, y + h + e, o).line(x + w + e, y + h, x - e, y + h, o).line(x, y + h + e, x, y - e, o);
  }
  poly(pts, o = {}) { if (o.fill) this.fill(pts, o.fill, o); return this.stroke([...pts, pts[0], pts[1]], o); }
  fill(pts, col, o = {}) { const f = mkFill(pts, col, { a: o.fa ?? 1, dx: o.dx ?? 3, dy: o.dy ?? 3, dir: o.dir, rings: o.rings }); this.fills.push(f); f.at = this.seq.length; return this; }
  text(str, x, y, o = {}) { const it = { k: 't', str, x, y, ...o }; return this._add(it, (o.size || 80) * 0.5 * Math.max(...str.split('\n').map(s => s.length)) * (o.slow ?? 0.6)); }
  dot(x, y, r = 5, o = {}) { const it = { k: 'd', x, y, r, col: o.col || INK }; return this._add(it, 8); }
  finish() { this.Wt = this.seq.reduce((s, it) => s + it.wt, 0) || 1; return this; }
  // p: 0..1 draw-on progress. o.grey: 0..1 fades the drawing to grey. o.lw: pen multiplier (1/scale keeps the pen even).
  draw(ctx, p, o = {}) {
    if (p <= 0) return;
    const g = o.grey || 0;
    if (g > 0 && g < 1) { this._draw(ctx, p, o, 1 - g, false); this._draw(ctx, p, o, g, true); }
    else this._draw(ctx, p, o, 1, g >= 1);
  }
  _draw(ctx, p, o, alpha, grey) {
    const lw = LW * (o.lw ?? 1), hasF = this.fills.length > 0;
    const ps = hasF ? clamp(p / 0.8) : p, pf = clamp((p - 0.55) / 0.45);
    ctx.save(); ctx.globalAlpha *= alpha * (o.a ?? 1); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const nf = this.fills.length;
    this.fills.forEach((f, i) => { const q = pf >= 1 ? 1 : clamp(pf * nf - i); if (q > 0) drawFill(ctx, f, q, grey ? (f.col === PAPER ? PAPER : PALE) : o.fillCol); });
    let acc = 0; const Wt = this.Wt;
    for (const it of this.seq) {
      const a0 = acc / Wt, a1 = (acc + it.wt) / Wt; acc += it.wt;
      const q = ps >= 1 ? 1 : clamp((ps - a0) / ((a1 - a0) || 1e-6));
      if (q <= 0) break;
      const col = grey ? GREY : o.col;
      if (it.k === 's') drawStroke(ctx, it, q, lw, col);
      else if (it.k === 't') drawText(ctx, it, q, col);
      else if (it.k === 'd') { ctx.beginPath(); ctx.arc(it.x, it.y, it.r * Math.min(1, q * 2), 0, Math.PI * 2); ctx.fillStyle = col || it.col; ctx.fill(); }
    }
    ctx.restore();
  }
}
const SK = new Map();
function sketch(key, build) { let s = SK.get(key); if (!s) { s = new Sketch(hstr(key)); build(s); s.finish(); SK.set(key, s); } return s; }

// ── the pen handed to every scene ───────────────────────────────────────────────────────────
class Pen {
  constructor(ctx, t, id) { this.ctx = ctx; this.t = t; this.id = id; }
  on(t0, dur = 0.8) { return clamp((this.t - t0) / dur); }         // linear draw-on progress
  ease(t0, dur = 0.8, f = eio) { return f((this.t - t0) / dur); }
  // draw a cached sketch with a transform. key is local to the scene.
  sk(key, build, p, x = 0, y = 0, s = 1, o = {}) {
    if (p <= 0) return;
    const sk = sketch(this.id + '/' + key, build), c = this.ctx;
    c.save(); c.translate(x, y); if (o.r) c.rotate(o.r); if (s !== 1 || o.sx) c.scale(s * (o.sx || 1), s);
    sk.draw(c, p, { ...o, lw: (o.lw ?? 1) / s });
    c.restore();
  }
  // shared drawings (props) cached once for the whole film
  prop(name, build, p, x, y, s = 1, o = {}) {
    if (p <= 0) return;
    const sk = sketch('prop/' + name, build), c = this.ctx;
    c.save(); c.translate(x, y); if (o.r) c.rotate(o.r); c.scale(s * (o.flip ? -1 : 1) * (o.sx || 1), s * (o.sy || 1));
    sk.draw(c, p, { ...o, lw: (o.lw ?? 1) / s });
    c.restore();
  }
  text(str, x, y, o = {}, p = 1) { drawText(this.ctx, { str, x, y, ...o }, p, o.col); }
  // immediate-mode wobbly line (for things that move)
  wl(pts, seed, p = 1, o = {}) {
    if (p <= 0) return;
    const st = mkStroke(wob(o.smooth ? spline(pts, false, 8) : pts, seed, o.amp ?? 1.5, o.wl ?? 120), o);
    const c = this.ctx; c.save(); c.lineCap = 'round'; c.lineJoin = 'round'; drawStroke(c, st, p, LW * (o.lw ?? 1), o.col); c.restore();
  }
  dot(x, y, r, col = INK) { const c = this.ctx; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fillStyle = col; c.fill(); }
  fillPoly(pts, col, a = 1) { const c = this.ctx; c.save(); c.globalAlpha *= a; c.beginPath(); c.moveTo(pts[0], pts[1]); for (let i = 2; i < pts.length; i += 2) c.lineTo(pts[i], pts[i + 1]); c.closePath(); c.fillStyle = col; c.fill(); c.restore(); }
  shadow(x, y, rx, a = 1) { const c = this.ctx; c.save(); c.globalAlpha *= 0.9 * a; c.beginPath(); c.ellipse(x, y, rx, rx * 0.16, 0, 0, Math.PI * 2); c.fillStyle = PALE; c.fill(); c.restore(); }
  save() { this.ctx.save(); } restore() { this.ctx.restore(); }
  tr(x, y, s = 1, r = 0) { const c = this.ctx; c.translate(x, y); if (r) c.rotate(r); if (s !== 1) c.scale(s, s); }
  alpha(a) { this.ctx.globalAlpha *= a; }
}

// ── timing: cue words from the narration ───────────────────────────────────────────────────
const LINES = TIMING.lines.map(([t0, t1, text, toks], i) => ({ i, t0, t1, text, toks }));
// time a word starts: w(lineNumber (1-based, as in the script), 'token', occurrence)
function w(ln, tok, n = 0) {
  const L = LINES[ln - 1]; if (!L) throw new Error('no line ' + ln);
  if (tok === undefined) return L.t0;
  let k = -1;
  for (const [tk, a] of L.toks) if (tk === tok && ++k === n) return a;
  throw new Error(`line ${ln}: no '${tok}' #${n} in "${L.text}"`);
}
function we(ln, tok, n = 0) { const L = LINES[ln - 1]; if (tok === undefined) return L.t1; let k = -1; for (const [tk, , b] of L.toks) if (tk === tok && ++k === n) return b; throw new Error(`line ${ln}: no '${tok}'`); }
const l0 = ln => LINES[ln - 1].t0, l1 = ln => LINES[ln - 1].t1;
const FILM_END = TIMING.dur + (typeof FILM_META !== 'undefined' ? FILM_META.tail_s ?? (FILM_META.intake_schema ? 0 : 4.6) : 4.6);
const DUR = typeof FILM_META !== 'undefined' && FILM_META.intake_schema ? FILM_END : Math.ceil(FILM_END * 10) / 10;

// ── scenes and the camera ──────────────────────────────────────────────────────────────────
const SCENES = [];
function scene(def) { SCENES.push(def); return def; }
const GLIDE = 0.75, LEAD = 0.05;    // the camera leaves LEAD s before the next scene starts and takes GLIDE s
function layoutScenes() {
  const COLS = 10, DX = 2300, DY = 1480;
  let auto = 0;
  SCENES.forEach((s, i) => {
    s.start = typeof s.at === 'number' ? s.at : s.at.length > 1 ? w(s.at[0], s.at[1], s.at[2] || 0) - (s.pre ?? 0.25) : l0(s.at[0]) - (s.pre ?? 0.25);
    if (i === 0) s.start = 0;
    s.z = s.z || 1; s.k = s.k || 1;   // k: the scene's own scale on the page (nested detail scenes are small)
    if (!s.pos) {
      const r = Math.floor(auto / COLS), c = auto % COLS, cc = r % 2 ? COLS - 1 - c : c;
      s.pos = [cc * DX + (s.nudge ? s.nudge[0] : 0), r * DY + (s.nudge ? s.nudge[1] : 0)]; auto++;
    }
    if (typeof s.pos === 'function') s.pos = s.pos(SCENES);
  });
  SCENES.forEach((s, i) => { s.end = i + 1 < SCENES.length ? SCENES[i + 1].start : DUR; });
  for (let i = 0; i < SCENES.length; i++) if (SCENES[i].end - SCENES[i].start < 0.6) console.warn('short scene', SCENES[i].id, SCENES[i].end - SCENES[i].start);
}
// where the camera looks during scene s at time t: the scene centre, a slow push-in, plus any scripted move
function sceneCam(s, t) {
  const u = clamp((t - s.start) / Math.max(0.5, s.end - s.start));
  let x = s.pos[0] + 960 * s.k, y = s.pos[1] + 540 * s.k, z = s.z / s.k * (1 + (s.push ?? 0.04) * ss(u));
  if (s.cam) { const m = s.cam(t, u); if (m) { x += (m.x || 0) * s.k; y += (m.y || 0) * s.k; z *= m.z || 1; } }
  return { x, y, z };
}
function camAt(t) {
  let i = 0; while (i + 1 < SCENES.length && SCENES[i + 1].start - LEAD <= t) i++;
  const s = SCENES[i], nx = SCENES[i + 1];
  if (i > 0) {
    const a = s.start - LEAD, G = s.glide ?? GLIDE;   // a scene can ask for a longer glide (deep zooms)
    if (t < a + G && !s.cut) {
      const p = SCENES[i - 1], A = sceneCam(p, a), B = sceneCam(s, a + G), u = eio((t - a) / G);
      const lz0 = Math.log(A.z), lz1 = Math.log(B.z), z = Math.exp(lerp(lz0, lz1, u));
      let f = u; if (Math.abs(lz1 - lz0) > 0.05) f = (1 / A.z - 1 / z) / (1 / A.z - 1 / B.z);   // zooms keep their target steady
      return { x: lerp(A.x, B.x, f), y: lerp(A.y, B.y, f), z, i };
    }
  }
  return { ...sceneCam(s, t), i };
}

// ── shake (for impacts) ────────────────────────────────────────────────────────────────────
const SHAKES = [];   // [time, strength]
function shakeAt(t) { let x = 0, y = 0; for (const [t0, k] of SHAKES) { const d = t - t0; if (d > 0 && d < 0.45) { const e = k * (1 - d / 0.45); x += e * noise(d * 40 + t0); y += e * noise(d * 40 + t0 + 9); } } return [x, y]; }

// ── source banners ───────────────────────────────────────────────────────────────────────────
// David, 29 Oct 2026: "for the big things, we should pop up a small text scroll at the top or bottom to cite critical
// sources when I'm making the big claims ... we can just do like 5 seconds of a small text banner". A small paper tag
// fixed to the screen (above the scenes, untouched by the camera), sliding in at t0 and out after dur seconds.
// At a film's top level, as SHAKES are: sourceBanner(t0, 'Bloomberg, "...", 26 Oct 2026', { dur: 5, at: 'top' |
// 'bottom', align: 'left' | 'center' | 'right' }). Top left by default: YouTube's captions sit at the bottom.
const BANNERS = [];
function sourceBanner(t0, text, o = {}) { BANNERS.push({ t0, text, dur: o.dur ?? 5, at: o.at || 'top', align: o.align || 'left' }); }
function drawBanners(ctx, t, S) {
  for (const b of BANNERS) {
    const u = t - b.t0; if (u < 0 || u > b.dur) continue;
    const io = Math.min(1, u / 0.45, (b.dur - u) / 0.45), e = io * io * (3 - 2 * io);
    ctx.setTransform(S, 0, 0, S, 0, 0); ctx.save(); ctx.globalAlpha = e;
    const tag = { font: 'k', size: 21 }, body = { font: 'p', size: 31 };
    let bw = textWidth(ctx, b.text, body);
    if (bw > 1500) { body.size = Math.max(22, Math.floor(31 * 1500 / bw)); bw = textWidth(ctx, b.text, body); }
    const tw = textWidth(ctx, 'SOURCE', tag), pad = 18, w = pad + tw + 14 + bw + pad, h = 50;
    const x = b.align === 'right' ? W - 60 - w : b.align === 'center' ? (W - w) / 2 : 60;
    const y = (b.at === 'bottom' ? H - 58 - h : 50) + (1 - e) * (b.at === 'bottom' ? 26 : -26);
    const pts = [], r = 10, seed = b.t0 * 7.3;                     // a rounded tag with the pen's slight tremor
    for (let i = 0; i < 4; i++) {
      const [cx, cy] = [[x + w - r, y + r], [x + w - r, y + h - r], [x + r, y + h - r], [x + r, y + r]][i];
      for (let k = 0; k <= 6; k++) { const a = (i - 1) * Math.PI / 2 + k / 6 * Math.PI / 2; pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); }
    }
    ctx.beginPath(); pts.forEach(([px, py], i) => { const j = 0.6 * noise(seed + i * 0.7); i ? ctx.lineTo(px + j, py - j) : ctx.moveTo(px + j, py - j); }); ctx.closePath();
    ctx.shadowColor = 'rgba(0,0,0,0.16)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 3; ctx.fillStyle = PAPER; ctx.fill();
    ctx.shadowColor = 'transparent'; ctx.lineWidth = 1.8; ctx.strokeStyle = INK; ctx.stroke();
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    ctx.font = fontStr(tag); ctx.fillStyle = '#7d7870'; ctx.fillText('SOURCE', x + pad, y + h / 2 + 1);
    ctx.font = fontStr(body); ctx.fillStyle = INK; ctx.fillText(b.text, x + pad + tw + 14, y + h / 2 + 2);
    ctx.restore();
  }
}

// ── render one frame ─────────────────────────────────────────────────────────────────────────
// A film's look can change the paper and draw a frame fixed to the screen over the scenes (course.js does both).
const PAGE = { paper: PAPER, over: null };   // over(ctx, t, S)
let LAYOUT_DONE = false;
function renderFrame(ctx, t, S = 1) {
  if (!LAYOUT_DONE) { layoutScenes(); LAYOUT_DONE = true; }
  ctx.setTransform(S, 0, 0, S, 0, 0); ctx.globalAlpha = 1;
  ctx.fillStyle = PAGE.paper; ctx.fillRect(0, 0, W, H);
  const cam = camAt(t);
  const [sx, sy] = shakeAt(t);
  const vw = W / cam.z, vh = H / cam.z, vx0 = cam.x - vw / 2, vy0 = cam.y - vh / 2;
  for (let i = 0; i < SCENES.length; i++) {
    const s = SCENES[i];
    if (t < s.start - LEAD - 0.1) break;
    // s.bleed: a scene that draws past its page's edge (a road run on past the frame) is drawn until that much past it
    // has left the view too, so the strip never vanishes in one frame (default 0)
    const x0 = s.pos[0], y0 = s.pos[1], x1 = x0 + W * s.k, y1 = y0 + H * s.k, bl = (s.bleed || 0) * s.k;
    if (x1 + bl < vx0 || x0 - bl > vx0 + vw || y1 + bl < vy0 || y0 - bl > vy0 + vh) continue;
    const tt = Math.min(t, s.end + (s.hold ?? 3));
    ctx.setTransform(S * cam.z, 0, 0, S * cam.z, S * (W / 2 - cam.x * cam.z + sx), S * (H / 2 - cam.y * cam.z + sy));
    ctx.translate(x0, y0); if (s.k !== 1) ctx.scale(s.k, s.k);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    try { s.draw(new Pen(ctx, tt, s.id), tt, s); } catch (e) { if (!s._err) { s._err = 1; console.error('scene ' + s.id, e); } }
  }
  if (BANNERS.length) drawBanners(ctx, t, S);
  if (PAGE.over) { ctx.setTransform(S, 0, 0, S, 0, 0); ctx.globalAlpha = 1; PAGE.over(ctx, t, S); }
}
