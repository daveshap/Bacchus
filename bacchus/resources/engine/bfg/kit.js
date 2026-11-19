// ═══════════════════════ kit: helpers every film's scenes use ═══════════════════════
// Loaded after props.js and before a film's scenes.js. Labels, fades, poses, coin stacks, bars, chips,
// camera keyframes, pictogram people, pipes (with flowing coins), coin towers and week grids.
const GROUND = 900;
// labels are written on as they're said; with window.__AUDIT set, each one is recorded for tools/audit.mjs
const LABREG = new Map();
const lab = (g, str, x, y, t0, o = {}) => {
  const dur = o.dur ?? clamp(str.length * 0.055, 0.35, 1.3);
  if (typeof window !== 'undefined' && window.__AUDIT) { const key = g.id + '|' + str + '|' + t0.toFixed(3); const e = LABREG.get(key) || { id: g.id, str, t0, dur }; Object.assign(e, { key, x, y, size: o.size || 72, font: o.font || 'k', align: o.align, lh: o.lh, rot: o.rot, outline: o.outline, seen: g.t }); LABREG.set(key, e); if (window.__DRAWN) window.__DRAWN.add(key); }
  return PR.label(g, g.on(t0, dur), str, x, y, o);
};
const big = (g, str, x, y, t0, o = {}) => lab(g, str, x, y, t0, { size: 120, ...o });
const quote = (g, str, x, y, t0, o = {}) => lab(g, str, x, y, t0, { font: 'c', size: 78, ...o });
const ground = (g, x0, x1, y, p, col = GREY) => g.wl([x0, y, x1, y + 3], 7 + x0 * 0.01, p, { col, lw: 0.7 });
// Group opacity: paint overlapping parts once at full opacity, then composite their layer.
// The destination keeps its clip and blend mode. Drawing state is copied into each pooled layer;
// callbacks obtain their drawing context from g.ctx after entering the group.
const FADE_LY = []; let FADE_DEPTH = 0;
const FADE_STYLE = ['fillStyle', 'strokeStyle', 'lineWidth', 'lineCap', 'lineJoin', 'miterLimit',
  'lineDashOffset', 'font', 'textAlign', 'textBaseline', 'direction', 'imageSmoothingEnabled',
  'imageSmoothingQuality', 'fontKerning', 'fontStretch', 'fontVariantCaps', 'letterSpacing',
  'wordSpacing', 'textRendering', 'shadowBlur', 'shadowColor', 'shadowOffsetX', 'shadowOffsetY', 'filter'];
function fadeLayer(c) {
  const cv = c.canvas;
  let L = FADE_LY[FADE_DEPTH];
  if (!L || L.canvas.width !== cv.width || L.canvas.height !== cv.height) {
    const n = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(cv.width, cv.height) : document.createElement('canvas');
    n.width = cv.width; n.height = cv.height; L = FADE_LY[FADE_DEPTH] = n.getContext('2d');
  }
  // Reset clips and drawing state too; an earlier callback must not contaminate the next group.
  if (L.reset) L.reset(); else L.canvas.width = cv.width;
  for (const key of FADE_STYLE) if (key in c && key in L) L[key] = c[key];
  L.setLineDash(c.getLineDash()); L.setTransform(c.getTransform());
  return L;
}
function fadeWhole(g, a, fn) {
  if (a <= 0) return;
  a = Math.min(1, a);
  const c = g.ctx;
  if (a >= 1 && (!c || c.globalAlpha === undefined || c.globalAlpha >= 1)) return fn();
  if (!c || !c.canvas || !c.getTransform || (typeof document === 'undefined' && typeof OffscreenCanvas === 'undefined')) {
    // Non-painting analysis tools can still obtain geometry and callback results.
    g.save(); g.alpha(a); try { return fn(); } finally { g.restore(); }
  }
  const L = fadeLayer(c); let r;
  g.ctx = L; FADE_DEPTH++;
  try { r = fn(); } finally { FADE_DEPTH--; g.ctx = c; }
  c.save();
  try {
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha *= a;
    // The layer already contains these effects; applying them again would double them.
    c.shadowBlur = 0; c.shadowColor = 'rgba(0,0,0,0)'; c.shadowOffsetX = 0; c.shadowOffsetY = 0;
    if ('filter' in c) c.filter = 'none';
    c.drawImage(L.canvas, 0, 0);
  } finally { c.restore(); }
  return r;
}
// fade something out from t1 over d seconds, or in from t0
const fadeOut = (g, t1, d, fn) => fadeWhole(g, 1 - clamp((g.t - t1) / d), fn);
const fadeIn = (g, t0, d, fn) => fadeWhole(g, clamp((g.t - t0) / d), fn);
// a figure that fades to grey (u: 0 → 1)
const greyFig = (g, o, u) => { if (u <= 0) return figure(g, o); if (u >= 1) return figure(g, { ...o, grey: 1 }); figure(g, { ...o, a: (o.a ?? 1) * (1 - u) }); return figure(g, { ...o, grey: 1, a: (o.a ?? 1) * u, noShadow: true }); };
function mixPose(a, b, u) {
  const o = {};
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    const x = a[k], y = b[k];
    if (Array.isArray(x) || Array.isArray(y)) { const xa = x || [0, 0], ya = y || [0, 0]; o[k] = [lerp(xa[0], ya[0], u), lerp(xa[1], ya[1], u)]; }
    else if (typeof x === 'number' || typeof y === 'number') o[k] = lerp(x || 0, y || 0, u);
    else o[k] = u < 0.5 ? x : y;
  }
  return o;
}
// coins seen edge-on, stacked; they drop in one by one
const coinStack = (g, x, y, n, p, col = GREEN, s = 1) => {
  for (let i = 0; i < n; i++) {
    const q = clamp(p * n - i); if (q <= 0) break;
    g.save(); g.alpha(clamp(q * 3));
    g.prop('coinSide' + col, k => k.circle(0, 0, 60, { ry: 17, fill: col, w: 0.9 }), 1, x, y - i * 24 * s - (1 - eout(q)) * 70 * s, s);
    g.restore();
  }
};
// a bar that grows from its base (immediate mode, so it can animate)
const bar = (g, x, y, w, h, u, col, o = {}) => {
  if (u <= 0) return; const hh = h * (o.lin ? clamp(u) : eio(u));
  if (col) g.fillPoly([x - w / 2 + 3, y - hh + 3, x + w / 2 + 3, y - hh + 3, x + w / 2 + 3, y, x - w / 2 + 3, y], col, o.a ?? 1);
  g.wl([x - w / 2, y, x - w / 2, y - hh, x + w / 2, y - hh, x + w / 2, y], o.seed ?? 3, 1, { lw: o.lw ?? 1, dash: o.dash, col: o.ink });
};
// a horizontal bar that grows to the right
const hbar = (g, x, y, w, h, u, col, o = {}) => {
  if (u <= 0) return; const ww = w * (o.lin ? clamp(u) : eio(u));
  if (col) g.fillPoly([x + 3, y - h / 2 + 3, x + ww + 3, y - h / 2 + 3, x + ww + 3, y + h / 2 + 3, x + 3, y + h / 2 + 3], col, o.a ?? 1);
  g.wl([x, y - h / 2, x + ww, y - h / 2, x + ww, y + h / 2, x, y + h / 2, x, y - h / 2], o.seed ?? 5, 1, { lw: o.lw ?? 1, dash: o.dash, col: o.ink });
};
const sitOn = (g, o, y) => { const sc = o.s ?? 1; g.prop('stool2', k => { k.rect(-50, -14, 100, 14, { fill: WOOD }); k.line(-38, 0, -46, 52).line(38, 0, 46, 52); }, o.p ?? 1, o.x - 8 * sc * (o.f ?? 1), y - 48 * sc + 6 * sc, sc); return figure(g, { ...o, y }); };
const heartPts = (cx, cy, k) => { const out = []; for (let i = 0; i <= 80; i++) { const a = -Math.PI + i / 80 * Math.PI * 2; out.push(cx + 16 * Math.pow(Math.sin(a), 3) * k, cy - (13 * Math.cos(a) - 5 * Math.cos(2 * a) - 2 * Math.cos(3 * a) - Math.cos(4 * a)) * k); } return out; };
const rrPts = (x, y, w, h, r) => { const out = []; const c = [[x + w - r, y + r, -Math.PI / 2], [x + w - r, y + h - r, 0], [x + r, y + h - r, Math.PI / 2], [x + r, y + r, Math.PI]]; for (const [cx, cy, a0] of c) for (let k = 0; k <= 6; k++) { const a = a0 + k / 6 * Math.PI / 2; out.push(cx + Math.cos(a) * r, cy + Math.sin(a) * r); } return out; };
// camera keyframes inside a scene: [time, dx, dy, zoom(, seconds)]; each move starts at its time
function camKeys(t, keys, d = 0.9) {
  let x = keys[0][1], y = keys[0][2], z = keys[0][3];
  for (let i = 1; i < keys.length; i++) { const [tk, kx, ky, kz, kd] = keys[i]; const u = eio((t - tk) / (kd ?? d)); if (u <= 0) break; x = lerp(x, kx, u); y = lerp(y, ky, u); z = Math.exp(lerp(Math.log(z), Math.log(kz), u)); }
  return { x, y, z };
}
// a rounded tag with a word in it
const chip = (g, p, str, x, y, col, o = {}) => {
  const sz = o.size || 38, key = 'chip' + str + col + sz + (o.fill || '');
  g.prop(key, k => { const w = textWidth(g.ctx, str, { font: 'k', size: sz }) + sz * 1.3, h = sz * 1.45; k.loop(rrPts(-w / 2, -h / 2, w, h, h * 0.42), { fill: o.fill || '#ffffff', col, smooth: false, w: 0.85, dx: 0, dy: 0 }); k.text(str, 0, sz * 0.36, { size: sz, font: 'k', col: o.tcol || INK }); }, p, x, y, 1);
};

// glasses on a figure (side view, facing f)
const glasses = (g, f, s, dir = 1) => { if (!f) return; const [hx, hy] = f.head, c = g.ctx; c.save(); c.strokeStyle = INK; c.lineWidth = LW * 0.55; for (const dx of [6, 21]) { c.beginPath(); c.arc(hx + dx * s * dir, hy - 5 * s, 7.5 * s, 0, Math.PI * 2); c.stroke(); } c.beginPath(); c.moveTo(hx + 13.5 * s * dir, hy - 6 * s); c.lineTo(hx + 13.5 * s * dir, hy - 6 * s); c.stroke(); c.beginPath(); c.moveTo(hx - 1.5 * s * dir, hy - 6 * s); c.lineTo(hx - 14 * s * dir, hy - 9 * s); c.stroke(); c.restore(); };
// a small pictogram person (for crowds of hundreds)
const person = (g, x, y, s, col = GREY, a = 1) => { const c = g.ctx; c.save(); c.globalAlpha *= a; c.fillStyle = col; c.beginPath(); c.arc(x, y - 46 * s, 11 * s, 0, Math.PI * 2); c.fill(); c.beginPath(); c.moveTo(x - 14 * s, y); c.lineTo(x - 15 * s, y - 22 * s); c.quadraticCurveTo(x - 15 * s, y - 32 * s, x - 6 * s, y - 32 * s); c.lineTo(x + 6 * s, y - 32 * s); c.quadraticCurveTo(x + 15 * s, y - 32 * s, x + 15 * s, y - 22 * s); c.lineTo(x + 14 * s, y); c.closePath(); c.fill(); c.restore(); };

// ── pipes ──────────────────────────────────────────────────────────────────────────────────
const TINT = { [GREEN]: '#d3eedd', [BLUE]: '#d8e5f9', [AMBER]: '#fde9c2', [GREY]: '#f1efeb', [RED]: '#f9d5ce' };
const PIPEG = new Map();
function pipeGeom(pts, r) {
  const key = pts.map(v => Math.round(v)).join(',') + '|' + r;
  let G = PIPEG.get(key); if (G) return G;
  const c = [pts[0], pts[1]];
  for (let i = 2; i + 3 < pts.length; i += 2) {   // round every elbow
    const ax = pts[i - 2], ay = pts[i - 1], bx = pts[i], by = pts[i + 1], cx = pts[i + 2], cy = pts[i + 3];
    const l1 = Math.hypot(bx - ax, by - ay) || 1, l2 = Math.hypot(cx - bx, cy - by) || 1, rr = Math.min(r, l1 / 2, l2 / 2);
    const p0x = bx + (ax - bx) / l1 * rr, p0y = by + (ay - by) / l1 * rr, p1x = bx + (cx - bx) / l2 * rr, p1y = by + (cy - by) / l2 * rr;
    for (let k = 0; k <= 10; k++) { const u = k / 10; c.push((1 - u) * (1 - u) * p0x + 2 * (1 - u) * u * bx + u * u * p1x, (1 - u) * (1 - u) * p0y + 2 * (1 - u) * u * by + u * u * p1y); }
  }
  c.push(pts[pts.length - 2], pts[pts.length - 1]);
  const R = resample(c, 4), n = R.length / 2, cum = new Float32Array(n), nx = new Float32Array(n), ny = new Float32Array(n);
  for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + Math.hypot(R[2 * i] - R[2 * i - 2], R[2 * i + 1] - R[2 * i - 1]);
  for (let i = 0; i < n; i++) { const a = Math.max(0, i - 2), b = Math.min(n - 1, i + 2); const tx = R[2 * b] - R[2 * a], ty = R[2 * b + 1] - R[2 * a + 1], l = Math.hypot(tx, ty) || 1; nx[i] = -ty / l; ny[i] = tx / l; }
  G = { R, n, cum, nx, ny, L: cum[n - 1] };
  PIPEG.set(key, G); return G;
}
function pipeAt(G, s) { s = clamp(s, 0, G.L); let lo = 0, hi = G.n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (G.cum[m] < s) lo = m; else hi = m; } const f = clamp((s - G.cum[lo]) / ((G.cum[hi] - G.cum[lo]) || 1)); return [lerp(G.R[2 * lo], G.R[2 * hi], f), lerp(G.R[2 * lo + 1], G.R[2 * hi + 1], f), G.nx[lo], G.ny[lo]]; }
// a pipe: two walls with collars, the bore tinted with what flows in it, and the flow moving along it
// pts: corner points; w: bore; col: GREEN / BLUE / AMBER (GREY when nothing is in it yet); p: how much is laid
// o.flow px/s (0 = still) · o.dry (empty, dashed walls) · o.coins (coins in the flow; default for wide pipes)
// o.pour {len, every, drip} (coins or drops fall from the open end) · o.glow 0..1 · o.r elbow radius
function pipe(g, pts, w, col, p, o = {}) {
  if (p <= 0 || w <= 0) return null;
  const G = pipeGeom(pts, o.r ?? 56), c = g.ctx, t = g.t, hw = w / 2, seed = o.seed ?? (pts[0] * 0.013 + pts[1] * 0.007);
  const Lp = G.L * clamp(p); let m = G.n - 1; if (p < 1) { m = 1; while (m < G.n - 1 && G.cum[m] < Lp) m++; }
  const L = [], Rr = [];
  for (let i = 0; i <= m; i++) { const x = G.R[2 * i], y = G.R[2 * i + 1]; L.push(x + G.nx[i] * hw, y + G.ny[i] * hw); Rr.push(x - G.nx[i] * hw, y - G.ny[i] * hw); }
  c.save();
  if (o.glow > 0) { c.beginPath(); for (let i = 0; i <= m; i++) { const x = G.R[2 * i], y = G.R[2 * i + 1]; i ? c.lineTo(x, y) : c.moveTo(x, y); } c.lineWidth = w + 46; c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = col; c.globalAlpha *= 0.22 * o.glow; c.stroke(); c.globalAlpha /= 0.22 * o.glow; }
  c.beginPath(); c.moveTo(L[0], L[1]); for (let i = 2; i < L.length; i += 2) c.lineTo(L[i], L[i + 1]); for (let i = Rr.length - 2; i >= 0; i -= 2) c.lineTo(Rr[i], Rr[i + 1]); c.closePath();
  c.fillStyle = o.dry ? '#ffffff' : (o.tint || TINT[col] || col); c.fill();
  const v = o.flow ?? 150;
  if (!o.dry && v && col !== GREY) {
    c.clip();
    if (o.coins ?? w >= 34) {
      const gap = o.gap ?? Math.max(w * 1.6, 70), r = Math.min(hw * 0.62, 22);
      c.font = `700 ${Math.round(r * 1.35)}px ${FONT.k}, ${FALLBACK}`; c.textAlign = 'center';
      for (let s = ((t * v) % gap + gap) % gap; s < Lp; s += gap) { const [x, y] = pipeAt(G, s); c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fillStyle = col; c.fill(); c.lineWidth = 2.4; c.strokeStyle = INK; c.stroke(); c.fillStyle = '#ffffff'; c.fillText('$', x, y + r * 0.46); }
    } else {
      c.setLineDash([Math.max(10, w * 0.9), Math.max(16, w * 1.3)]); c.lineDashOffset = -t * v; c.lineCap = 'round';
      c.beginPath(); for (let i = 0; i <= m; i++) { const x = G.R[2 * i], y = G.R[2 * i + 1]; i ? c.lineTo(x, y) : c.moveTo(x, y); }
      c.lineWidth = Math.max(3, w * 0.42); c.strokeStyle = col; c.stroke(); c.setLineDash([]);
    }
  }
  c.restore();
  const wo = { lw: o.lw ?? (w >= 30 ? 0.9 : 0.7), dash: o.dry ? [14, 12] : undefined, col: o.ink || (o.dry ? '#9a958e' : INK), amp: 1 };
  g.wl(L, seed, 1, wo); g.wl(Rr, seed + 3.3, 1, wo);
  if (w >= 16 && !o.noCollars) for (let s = o.collar0 ?? 120; s < Lp - 40; s += o.collarGap ?? 280) { const [x, y, nx, ny] = pipeAt(G, s), e = hw + 6; g.wl([x + nx * e, y + ny * e, x - nx * e, y - ny * e], seed + s * 0.01, 1, { lw: 0.75, col: wo.col }); }
  const [ex, ey, enx, eny] = pipeAt(G, Lp);
  if (p >= 1 && !o.noLip) { const e = hw + 8; g.wl([ex + enx * e, ey + eny * e, ex - enx * e, ey - eny * e], seed + 9, 1, { lw: 1.1, col: wo.col }); }
  if (p >= 1 && o.pour && !o.dry && col !== GREY) pour(g, ex, ey, w, col, o.pour);
  return { end: [ex, ey], G };
};
// what falls out of an open pipe end: spinning coins, or slow drops for a trickle
function pour(g, x, y, w, col, o) {
  const t = g.t, len = o.len ?? 140, every = o.every ?? 0.3, T = o.fall ?? Math.sqrt(len / 900) * 1.4, c = g.ctx;
  const j1 = Math.floor(t / every), j0 = Math.ceil((t - T) / every);
  for (let j = j0; j <= j1; j++) {
    const tau = t - j * every; if (tau < 0 || tau > T) continue;
    const u = tau / T, yy = y + len * u * u, xx = x + (hash(j * 3 + 1) - 0.5) * w * 0.35;
    if (o.drip) { c.save(); c.fillStyle = col; c.beginPath(); c.moveTo(xx, yy - 16); c.quadraticCurveTo(xx + 9, yy, xx, yy + 8); c.quadraticCurveTo(xx - 9, yy, xx, yy - 16); c.fill(); c.restore(); continue; }
    const r = o.r ?? Math.min(w * 0.36, 18), sx = Math.abs(Math.cos(tau * 9 + j));
    c.save(); c.translate(xx, yy); c.scale(Math.max(0.15, sx), 1); c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fillStyle = col; c.fill(); c.lineWidth = 2.4 / Math.max(0.15, sx); c.strokeStyle = INK; c.stroke(); c.restore();
  }
}

// a week grid (13 x 4) that fills in
const weekGrid = (g, x0, y0, n, u, col, o = {}) => { const pc = o.pitch || 46, sz = o.size || 40; for (let i = 0; i < n; i++) { const r = Math.floor(i / (o.cols || 13)), c = i % (o.cols || 13), x = x0 + c * pc, y = y0 + r * pc, q = clamp(u * n - i); g.ctx.save(); g.ctx.lineWidth = 2.4; g.ctx.strokeStyle = '#a39e96'; g.ctx.strokeRect(x, y, sz, sz); if (q > 0) { g.ctx.globalAlpha *= q; g.ctx.fillStyle = col; g.ctx.fillRect(x + 4, y + 4, sz - 8, sz - 8); } g.ctx.restore(); } };
// a tower of coins seen edge-on, rising from its base
const coinTower = (g, x, base, h, u, col = AMBER, w = 170) => {
  if (u <= 0) return; const hh = h * eio(u), c = g.ctx;
  c.save(); c.fillStyle = col; c.fillRect(x - w / 2 + 3, base - hh + 3, w, hh);
  c.strokeStyle = 'rgba(30,28,26,0.35)'; c.lineWidth = 2; for (let y = base - 20; y > base - hh + 6; y -= 20) { c.beginPath(); c.moveTo(x - w / 2 + 6, y); c.lineTo(x + w / 2, y); c.stroke(); }
  c.beginPath(); c.ellipse(x + 3, base - hh + 3, w / 2, 14, 0, 0, Math.PI * 2); c.fillStyle = '#f9c96a'; c.fill(); c.restore();
  g.wl([x - w / 2, base, x - w / 2, base - hh], 3 + x * 0.01, 1, { lw: 1 }); g.wl([x + w / 2, base, x + w / 2, base - hh], 4 + x * 0.01, 1, { lw: 1 });
  g.wl(arcPts(x, base - hh, w / 2, 14, 0, Math.PI * 2.02), 5 + x * 0.01, 1, { lw: 0.9 });
};

// the dashed outline of somebody who isn't there
const ghost = (g, p, x, y, s = 1) => g.prop('ghost', k => {
  const d = { dash: [13, 11], col: '#8d8880' };
  k.circle(0, -214, 36, d); k.path([-22, -168, -26, -90, 26, -90, 22, -168, -22, -168], d);
  k.path([-10, -90, -14, 0], d).path([10, -90, 16, 0], d).path([-22, -160, -40, -84], d).path([22, -160, 40, -84], d);
  k.text('?', 0, -188, { size: 64, font: 'k', col: '#8d8880' });
}, p, x, y, s);
// a signpost with arms: [text, side (1 right, -1 left), y]
// The post is a drawing of its own, drawn first, so the boards nailed across it hide it (when one drawing held both, the
// post's line showed through the boards).
const signpost = (g, p, x, y, arms, key, o = {}) => {
  g.prop('signPost/' + (o.h || 420), k => k.line(0, 0, 0, -(o.h || 420), { w: 1.4 }), p, x, y, 1);
  g.prop('sign/' + key, k => {
    arms.forEach(([str, d, yy], i) => { const wd = o.w || 300, x0 = d > 0 ? -16 : 16, x1 = x0 + d * wd; k.poly([x0, yy - 34, x1, yy - 34, x1 + d * 34, yy, x1, yy + 34, x0, yy + 34], { fill: '#ffffff' }); k.text(str, (x0 + x1) / 2, yy + 15, { size: o.size || 40, font: 'k' }); });
  }, p, x, y, 1);
};
// the night sky over a scene
const nightSky = (g, p, mx, my) => { PR.moon(g, p, mx, my, 0.9); PR.stars(g, p, 80, 60, 1760, 260, 12, 5); };

