// ═══════════════════════ props: the film's recurring drawings ═══════════════════════
// Each prop is drawn around its own origin (usually its base or centre) at about real proportions,
// cached once, and placed by scenes with a position, a scale and a draw-on progress.
const PR = {};

// ── marks ───────────────────────────────────────────────────────────────────────────────────
PR.check = (g, p, x, y, s = 1, col = GREEN) => g.prop('check' + col, k => k.path([-40, 0, -10, 32, 50, -46], { w: 1.8, col }), p, x, y, s);
PR.cross = (g, p, x, y, s = 1, col = RED) => g.prop('cross' + col, k => k.line(-40, -40, 40, 40, { w: 1.8, col }).line(40, -40, -40, 40, { w: 1.8, col }), p, x, y, s);
PR.circleMark = (g, p, x, y, rx, ry, col = RED, seed = 1) => g.prop('ring' + rx + 'x' + ry + col + seed, k => k.stroke(arcPts(0, 0, rx, ry, -2.2, -2.2 + Math.PI * 2.15, 0.05, 0.04, seed), { w: 1.3, col }), p, x, y, 1);
PR.under = (g, p, x, y, w, col = INK) => g.prop('under' + w + col, k => k.path([-w / 2, 0, w / 2, 4], { w: 1.2, col }), p, x, y, 1);
// an arrow from (x1,y1) to (x2,y2), optionally bent by `bend`
PR.arrow = (g, p, x1, y1, x2, y2, o = {}) => {
  const bend = o.bend || 0, mx = (x1 + x2) / 2 - (y2 - y1) * bend, my = (y1 + y2) / 2 + (x2 - x1) * bend;
  const pts = bend ? spline([x1, y1, mx, my, x2, y2], false, 12) : [x1, y1, x2, y2];
  const n = pts.length, ax = pts[n - 2], ay = pts[n - 1], bx = pts[n - 4], by = pts[n - 3], a = Math.atan2(ay - by, ax - bx), hl = o.head ?? 30;
  const col = o.col || INK, wv = o.w ?? 1.1;
  g.wl(pts, (o.seed ?? 1) + x1 * 0.01, clamp(p / 0.82), { col, lw: wv, dash: o.dash });
  if (p > 0.82) { const q = clamp((p - 0.82) / 0.18); g.wl([ax + Math.cos(a + 2.6) * hl, ay + Math.sin(a + 2.6) * hl, ax, ay, ax + Math.cos(a - 2.6) * hl, ay + Math.sin(a - 2.6) * hl], (o.seed ?? 1) + 5, q, { col, lw: wv, amp: 0.6 }); }
};
// a speech bubble with its tail pointing at (tx, ty)
function bubbleOutline(w, h, dx, dy, jag) {
  // the bubble's outline as dense points (a rounded oval, or a jagged burst for a shout)
  let ring = [];
  if (jag) {
    const n = 18, c = [];
    for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2, r = i % 2 ? 0.78 : 1.05; c.push([Math.cos(a) * w / 2 * r, Math.sin(a) * h / 2 * r]); }
    for (let i = 0; i < n; i++) { const [x0, y0] = c[i], [x1, y1] = c[(i + 1) % n], m = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 8)); for (let j = 0; j < m; j++) ring.push([lerp(x0, x1, j / m), lerp(y0, y1, j / m)]); }
  } else {
    const sp = spline([-w / 2, 0, -w * 0.38, -h * 0.46, 0, -h / 2, w * 0.38, -h * 0.46, w / 2, 0, w * 0.38, h * 0.46, 0, h / 2, -w * 0.38, h * 0.46], true, 16);
    for (let i = 0; i < sp.length - 2; i += 2) ring.push([sp[i], sp[i + 1]]);
  }
  // the tail leaves the outline where it faces the speaker: cut a gap there and run the outline out to the tip and back
  const ang = Math.atan2(dy, dx), n = ring.length;
  let i0 = 0, bd = 1e9;
  ring.forEach(([px, py], i) => { const d = Math.abs(Math.atan2(Math.sin(Math.atan2(py, px) - ang), Math.cos(Math.atan2(py, px) - ang))); if (d < bd) { bd = d; i0 = i; } });
  const half = Math.min(36, Math.min(w, h) * 0.14), at = k => ring[((k % n) + n) % n], d0 = k => Math.hypot(at(k)[0] - ring[i0][0], at(k)[1] - ring[i0][1]);
  let a = 0, b = 0;
  while (a < n / 4 && d0(i0 - a) < half) a++;
  while (b < n / 4 && d0(i0 + b) < half) b++;
  const out = [];
  for (let k = i0 + b; k <= i0 - a + n; k++) { const [px, py] = at(k); out.push(px, py); }
  out.push(dx, dy);
  return out;
}
PR.bubble = (g, p, x, y, w, h, tx, ty, o = {}) => {
  const key = `bub2_${w}x${h}_${Math.round(tx - x)}_${Math.round(ty - y)}${o.jag ? 'j' : ''}`;
  g.prop(key, k => k.poly(bubbleOutline(w, h, tx - x, ty - y, o.jag), { fill: '#ffffff', dx: 0, dy: 0 }), p, x, y, 1);
};
// a thought cloud
PR.cloud = (g, p, x, y, w, h, tx, ty) => { g.prop(`cloud2_${w}x${h}_${Math.round(tx - x)}_${Math.round(ty - y)}`, k => {
  const n = 11, pts = [];
  for (let i = 0; i < n; i++) {
    const a0 = (i + 0.15) / n * Math.PI * 2, a1 = (i + 1.15) / n * Math.PI * 2;
    const x0 = Math.cos(a0) * w * 0.42, y0 = Math.sin(a0) * h * 0.4, x1 = Math.cos(a1) * w * 0.42, y1 = Math.sin(a1) * h * 0.4;
    const L = Math.hypot(x1 - x0, y1 - y0), nx = (y1 - y0) / L, ny = -(x1 - x0) / L, cx = (x0 + x1) / 2 + nx * L * 0.62, cy = (y0 + y1) / 2 + ny * L * 0.62;
    for (let j = 0; j < 8; j++) { const u = j / 8; pts.push((1 - u) * (1 - u) * x0 + 2 * (1 - u) * u * cx + u * u * x1, (1 - u) * (1 - u) * y0 + 2 * (1 - u) * u * cy + u * u * y1); }
  }
  k.loop(pts, { fill: '#ffffff', smooth: false, dx: 0, dy: 0 });
}, p, x, y, 1); g.prop(`cloud2:bubbles_${Math.round(tx - x)}_${Math.round(ty - y)}`, k => {
  // the bubbles in front of the cloud: a drawing of their own, so the cloud's outline stops at the first one
  const dx = tx - x, dy = ty - y; k.circle(dx * 0.6, dy * 0.6, 20, { fill: '#ffffff' }).circle(dx * 0.8, dy * 0.8, 12, { fill: '#ffffff' });
}, p, x, y, 1); };
// impact burst (CRASH): a jagged star
PR.burst = (g, p, x, y, r, col = AMBER, seed = 1) => g.prop('burst' + r + col + seed, k => {
  const pts = [], n = 14; for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2 + 0.2, rr = (i % 2 ? 0.48 : 1) * r * (0.85 + hash(i + seed) * 0.3); pts.push(Math.cos(a) * rr, Math.sin(a) * rr); }
  k.poly(pts, { fill: col, w: 1.1 });
}, p, x, y, 1);
// a little four-point sparkle (stars, shine)
PR.spark = (g, x, y, r, col = INK, a = 1) => { const c = g.ctx; c.save(); c.globalAlpha *= a; c.fillStyle = col; c.beginPath(); c.moveTo(x, y - r); c.quadraticCurveTo(x, y, x + r, y); c.quadraticCurveTo(x, y, x, y + r); c.quadraticCurveTo(x, y, x - r, y); c.quadraticCurveTo(x, y, x, y - r); c.fill(); c.restore(); };
// motion lines and puffs
PR.lines = (g, p, x, y, n, len, a, gap = 18, col = INK, seed = 1) => { for (let i = 0; i < n; i++) { const o = (i - (n - 1) / 2) * gap; g.wl([x - Math.sin(a) * o, y + Math.cos(a) * o, x - Math.sin(a) * o + Math.cos(a) * len, y + Math.cos(a) * o + Math.sin(a) * len], seed + i, p, { col, lw: 0.8 }); } };
PR.puff = (g, x, y, r, a = 1, col = PALE) => { const c = g.ctx; c.save(); c.globalAlpha *= a; c.fillStyle = col; c.strokeStyle = GREY; c.lineWidth = 3; for (const [dx, dy, k] of [[0, 0, 1], [r * 0.7, r * 0.2, 0.75], [-r * 0.65, r * 0.25, 0.7], [r * 0.15, -r * 0.5, 0.65]]) { c.beginPath(); c.arc(x + dx, y + dy, r * k, 0, Math.PI * 2); c.fill(); } c.restore(); };
// a label: hand-lettered words written on as they are said
PR.label = (g, p, str, x, y, o = {}) => g.text(str, x, y, { size: o.size || 72, font: o.font || 'k', col: o.col || INK, align: o.align, lh: o.lh, under: o.under, outline: o.outline, rot: o.rot }, p);

// ── the trade ──────────────────────────────────────────────────────────────────────────────
// stocking frame: a wooden hand machine; origin at the floor, centre. `t` drives the carriage.
PR.frame = (g, p, x, y, s = 1, o = {}) => {
  const wide = o.wide ? 1.9 : 1, Wd = 330 * wide;
  g.prop('frame' + wide, k => {
    const w = Wd, L = -w / 2, R = w / 2;
    k.rect(L, -300, w, 40, { fill: WOOD });                       // top beam
    k.line(L + 20, -260, L + 20, 0, { w: 1.2 }).line(R - 20, -260, R - 20, 0, { w: 1.2 });   // posts
    k.line(L - 10, 0, L + 60, 0).line(R - 60, 0, R + 10, 0);       // feet
    k.rect(L + 30, -170, w - 60, 26, { fill: WOOD });              // needle bed
    for (let i = 0; i < 18 * wide; i++) { const xx = L + 50 + i * (w - 100) / (18 * wide - 1); k.line(xx, -170, xx, -196, { w: 0.55 }); }
    k.line(L + 20, -80, R - 20, -80, { w: 0.9 });                  // cross brace
    k.path([L + 60, 0, L + 120, -40, R - 120, -40, R - 60, 0], { w: 0.8 });   // treadle
    k.rect(L - 6, -330, 34, 30, { fill: WOOD }).rect(R - 28, -330, 34, 30, { fill: WOOD });
  }, p, x, y, s, o);
  if (p >= 1 && !o.still) { // the sinker bar slides as the knitter works, and the fabric grows
    const t = g.t, k = Math.sin(t * (o.speed || 3.2)) * 0.5 + 0.5, c = g.ctx;
    c.save(); c.translate(x, y); c.scale(s, s);
    const bx = lerp(-Wd / 2 + 60, Wd / 2 - 160, k);
    c.lineWidth = LW / s; c.strokeStyle = INK; c.fillStyle = '#d9b77f';
    c.beginPath(); c.rect(bx, -232, 100, 22); c.fill(); c.stroke();
    if (!o.noCloth) { const L = Wd * 0.5 - 90, len = 46 + 10 * Math.sin(t * 0.7); c.fillStyle = o.clothCol || '#f4f2ee'; c.beginPath(); c.rect(-L / 1.5, -144, L * 1.33, len); c.fill(); c.lineWidth = LW * 0.55 / s; c.stroke(); for (let i = 1; i < 6; i++) { c.beginPath(); c.moveTo(-L / 1.5, -144 + i * len / 6); c.lineTo(L / 1.5 * 1.0, -144 + i * len / 6); c.globalAlpha = 0.35; c.stroke(); c.globalAlpha = 1; } }
    c.restore();
  }
};
// a broken frame: the same machine in pieces
PR.brokenFrame = (g, p, x, y, s = 1) => g.prop('brokenFrame', k => {
  k.poly([-165, -300, -20, -300, -40, -270, -165, -262], { fill: WOOD }).poly([10, -322, 160, -290, 150, -260, 0, -290], { fill: WOOD, r: 0.2 });
  k.line(-145, -250, -150, 0, { w: 1.2 }).line(150, -230, 110, 0, { w: 1.2 });
  k.poly([-130, -160, 10, -170, 0, -140, -128, -136], { fill: WOOD }).poly([40, -120, 140, -150, 145, -126, 46, -96], { fill: WOOD });
  for (let i = 0; i < 9; i++) { const xx = -120 + i * 22 + hash(i) * 8, yy = -40 + hash(i + 9) * 30; k.line(xx, yy, xx + 10 * (hash(i + 3) - 0.5) * 4, yy - 18, { w: 0.55 }); }
  k.line(-185, 0, 185, 0, { w: 0.8, col: GREY });
}, p, x, y, s);
PR.sledge = (g, p, x, y, s = 1, o = {}) => g.prop('sledge2', k => {   // a sledgehammer standing on its handle end; origin at the handle end
  k.line(0, -392, 0, 30, { w: 2.2 });
  k.rect(-100, -478, 200, 92, { fill: '#8b857c' });
  k.line(-100, -462, -100, -402, { w: 0.6 }).line(100, -462, 100, -402, { w: 0.6 });
}, p, x, y, s, o);
PR.sledgeDown = (g, p, x, y, s = 1, tilt = 0) => PR.sledge(g, p, x + Math.sin(tilt) * 478 * s, y - Math.cos(tilt) * 478 * s, s, { r: Math.PI + tilt });
PR.stocking = (g, p, x, y, s = 1, col = '#ffffff', o = {}) => g.prop('stocking' + col + (o.cut ? 'c' : ''), k => {
  const pts = [-30, -150, 30, -150, 30, -20, 70, 20, 70, 50, 10, 50, -30, 20];
  if (o.cut) k.poly(pts, { fill: col, dash: [16, 14] }); else k.loop(pts, { fill: col, smooth: false });
  k.line(-30, -128, 30, -128, { w: 0.6 });
}, p, x, y, s, o);
PR.coin = (g, p, x, y, s = 1, col = AMBER) => g.prop('coin' + col, k => { k.circle(0, 0, 40, { fill: col }); k.text('£', 0, 18, { size: 50, font: 'k' }); }, p, x, y, s);
PR.bread = (g, p, x, y, s = 1) => g.prop('bread', k => {
  k.loop([-120, 20, -110, -40, -40, -70, 40, -70, 110, -40, 120, 20], { fill: '#e2b373' });
  for (const xx of [-60, -10, 40]) k.line(xx, -55, xx + 26, -20, { w: 0.7 });
}, p, x, y, s);
PR.tag = (g, p, x, y, str, o = {}) => g.prop('tag' + str + (o.col || ''), k => {
  k.poly([-20, -40, 150, -40, 150, 40, -20, 40, -55, 0], { fill: o.fill || '#ffffff' }); k.circle(-28, 0, 7);
  k.text(str, 65, 20, { size: o.size || 56, font: 'k', col: o.col || INK });
}, p, x, y, o.s || 1, { r: o.r });
PR.cottage = (g, p, x, y, s = 1, o = {}) => {
  g.prop('cottage', k => {
    k.rect(-130, -170, 260, 170, { fill: '#f3f0ea' });
    k.poly([-160, -165, 0, -290, 160, -165], { fill: '#c9b48e' });
    k.rect(70, -280, 36, 70, { fill: '#c9b48e' });
    k.rect(-30, -95, 60, 95, {});
    k.rect(-105, -130, 54, 46, { fill: o.lit === false ? '#ffffff' : MOON });
    k.rect(51, -130, 54, 46, { fill: o.lit === false ? '#ffffff' : MOON });
  }, p, x, y, s, o);
  if (p >= 1 && o.smoke !== false) { const t = g.t; for (let i = 0; i < 3; i++) { const u = ((t * 0.35 + i / 3 + (o.seed || 0)) % 1); PR.puff(g, x + (88 + u * 40 + Math.sin(u * 6 + i) * 10) * s, y + (-300 - u * 140) * s, (14 + u * 22) * s, (1 - u) * 0.9); } }
};
PR.moon = (g, p, x, y, s = 1) => g.prop('moonFull', k => {   // a full moon with a few soft craters
  k.circle(0, 0, 64, { fill: MOON, w: 1.1 });
  for (const [cx, cy, r] of [[-22, -14, 13], [19, 17, 9], [24, -24, 6], [-6, 26, 5]]) k.circle(cx, cy, r, { fill: '#ecca57', col: '#cfae4c', w: 0.45 });
}, p, x, y, s);
PR.stars = (g, p, x, y, w, h, n = 12, seed = 1) => { for (let i = 0; i < n; i++) { const r = hash(i * 3 + seed), rx = hash(i * 7 + seed + 1), ry = hash(i * 11 + seed + 2); const q = clamp(p * n - i); if (q <= 0) continue; const tw = 0.75 + 0.25 * Math.sin(g.t * (2 + r * 2) + i); PR.spark(g, x + rx * w, y + ry * h, (8 + r * 9) * tw * pop(q), SKY, 0.9); } };
PR.snow = (g, x, y, w, h, n = 30, seed = 1, a = 1) => { const c = g.ctx; c.save(); c.globalAlpha *= a; c.fillStyle = '#9fb3c8'; for (let i = 0; i < n; i++) { const r = hash(i + seed), u = ((g.t * (0.06 + r * 0.05) + hash(i * 5 + seed)) % 1); c.beginPath(); c.arc(x + hash(i * 3 + seed) * w + Math.sin(g.t + i) * 12, y + u * h, 4 + r * 4, 0, Math.PI * 2); c.fill(); } c.restore(); };
PR.letter = (g, p, x, y, s = 1, sig = 'Ned Ludd', o = {}) => g.prop('letter' + sig, k => {
  k.poly([-150, -200, 150, -200, 150, 200, -150, 200], { fill: '#fffaf0' });
  for (let i = 0; i < 6; i++) k.path([-110, -150 + i * 40, 110 - (i === 5 ? 90 : hash(i) * 30), -150 + i * 40], { w: 0.5, col: GREY });
  k.text(sig, 0, 150, { size: 64, font: 'c', col: o.col || INK });
}, p, x, y, s, o);
PR.house2 = (g, p, x, y, s = 1, o = {}) => g.prop('house2' + (o.fill || ''), k => { // plain house outline (for "which houses")
  k.rect(-90, -130, 180, 130, { fill: o.fill || '#ffffff' }); k.poly([-110, -125, 0, -210, 110, -125], { fill: '#d8cfbf' }); k.rect(-22, -70, 44, 70); k.rect(-70, -100, 34, 30, { fill: MOON }); k.rect(36, -100, 34, 30, { fill: MOON });
}, p, x, y, s, o);

// ── buildings ──────────────────────────────────────────────────────────────────────────────
PR.mill = (g, p, x, y, s = 1, o = {}) => {
  g.prop('mill' + (o.lit ? 'Lit' : ''), k => {
    k.rect(-380, -420, 760, 420, { fill: '#efe9df' });
    k.poly([-400, -415, -330, -500, 330, -500, 400, -415], { fill: '#bdb3a3' });
    for (let r = 0; r < 3; r++) for (let c2 = 0; c2 < 7; c2++) k.rect(-330 + c2 * 98, -380 + r * 118, 52, 66, { fill: o.lit ? MOON : '#ffffff', w: 0.8 });
    k.rect(-40, -110, 80, 110, { fill: '#8f7a5d' });
    k.rect(420, -700, 70, 700, { fill: '#c9b39a' });
  }, p, x, y, s, o);
  if (p >= 1 && o.smoke !== false) for (let i = 0; i < 4; i++) { const u = ((g.t * 0.22 + i / 4) % 1); PR.puff(g, x + (455 + u * 90) * s, y + (-720 - u * 260) * s, (30 + u * 50) * s, (1 - u) * 0.85); }
};
PR.parliament = (g, p, x, y, s = 1, o = {}) => g.prop('parliament', k => {
  k.rect(-420, -260, 840, 260, { fill: '#e6edf8' });
  for (let i = 0; i < 9; i++) { const xx = -380 + i * 95; k.rect(xx, -200, 36, 120, { w: 0.7, fill: '#ffffff' }); k.poly([xx - 4, -200, xx + 18, -232, xx + 40, -200], { w: 0.7 }); }
  for (let i = 0; i < 12; i++) k.line(-420 + i * 76, -260, -420 + i * 76, -290, { w: 0.8 });
  k.rect(300, -620, 120, 360, { fill: '#e6edf8' });            // clock tower
  k.circle(360, -520, 40, { fill: '#ffffff' }); k.line(360, -520, 360, -548, { w: 0.7 }).line(360, -520, 382, -512, { w: 0.7 });
  k.poly([290, -620, 360, -720, 430, -620], { fill: BLUE });
  k.rect(-60, -100, 120, 100, { fill: '#c7d6ef' });
}, p, x, y, s, o);
PR.court = (g, p, x, y, s = 1) => g.prop('court', k => {
  k.poly([-380, -380, 0, -520, 380, -380], { fill: '#ece8e0' });
  k.rect(-380, -380, 760, 40, { fill: '#ece8e0' });
  for (let i = 0; i < 6; i++) { const xx = -320 + i * 128; k.rect(xx, -340, 46, 300, { fill: '#ffffff', w: 0.9 }); }
  k.rect(-400, -40, 800, 40, { fill: '#ece8e0' });
  k.text('YORK', 0, -420, { size: 64, font: 'k' });
}, p, x, y, s);
PR.ship = (g, p, x, y, s = 1, o = {}) => g.prop('ship', k => {
  k.poly([-160, -40, 160, -40, 120, 30, -120, 30], { fill: '#8f6a45' });
  k.line(-20, -40, -20, -300, { w: 1.1 }).line(70, -40, 70, -230, { w: 1 });
  k.poly([-20, -290, 50, -180, -20, -70], { fill: '#ffffff' }).poly([70, -220, 130, -140, 70, -60], { fill: '#ffffff' });
  k.path([-150, -230, -20, -300], { w: 0.6 });
}, p, x, y, s, o);
PR.waves = (g, p, x, y, w, seed = 1) => { const t = g.t, c = g.ctx; for (let j = 0; j < 2; j++) { const pts = []; for (let i = 0; i <= 24; i++) { const u = i / 24; pts.push(x - w / 2 + u * w, y + j * 26 + Math.sin(u * 14 + t * 2 + j) * 7); } g.wl(pts, seed + j, p, { col: BLUE, lw: 0.7 }); } };
PR.church = (g, p, x, y, s = 1) => g.prop('church', k => {
  k.rect(-120, -160, 240, 160, { fill: '#f3f0ea' }); k.poly([-140, -160, 0, -250, 140, -160], { fill: '#c9b48e' });
  k.rect(-50, -330, 100, 170, { fill: '#f3f0ea' }); k.poly([-60, -330, 0, -470, 60, -330], { fill: '#c9b48e' }); k.line(0, -470, 0, -520).line(-18, -500, 18, -500);
  k.rect(-22, -80, 44, 80);
}, p, x, y, s);
PR.forge = (g, p, x, y, s = 1) => {
  g.prop('forge', k => {
    k.poly([-140, 0, -120, -110, 120, -110, 140, 0], { fill: '#7a7068' });          // anvil
    k.poly([-200, -110, 180, -110, 120, -170, -150, -170], { fill: '#8b857c' });
    k.poly([-200, -150, -270, -128, -200, -110], { fill: '#8b857c' });
  }, p, x, y, s);
};
PR.fire = (g, x, y, s = 1, a = 1) => { const t = g.t, c = g.ctx; c.save(); c.globalAlpha *= a; c.translate(x, y); c.scale(s, s); for (const [dx, h, col] of [[-30, 120, RED], [10, 160, AMBER], [40, 110, RED], [0, 90, MOON]]) { const f = 1 + 0.18 * Math.sin(t * 11 + dx) + 0.08 * Math.sin(t * 23 + dx); c.beginPath(); c.moveTo(dx - 40, 0); c.quadraticCurveTo(dx - 36, -h * 0.6 * f, dx, -h * f); c.quadraticCurveTo(dx + 36, -h * 0.6 * f, dx + 40, 0); c.closePath(); c.fillStyle = col; c.fill(); } c.restore(); };
PR.scroll = (g, p, x, y, s = 1, lines = [], o = {}) => {
  const r = g.prop('scroll' + lines.join('|'), k => {
    k.poly([-230, -300, 230, -300, 230, 300, -230, 300], { fill: '#fbf5e6' });
    lines.forEach((l, i) => k.text(l, 0, -170 + i * 92, { size: o.size || 70, font: 'k', col: o.col || INK }));
    for (let i = 0; i < 3; i++) k.path([-160, 140 + i * 40, 160 - hash(i) * 60, 140 + i * 40], { w: 0.5, col: GREY });
  }, p, x, y, s, o);
  // the rods' ends in front of the sheet: a drawing of their own, so the sheet's outline stops at them
  g.prop('scroll:rods', k => {
    k.circle(-230, -300, 22, { fill: '#efe2c3' }).circle(230, -300, 22, { fill: '#efe2c3' }).circle(-230, 300, 22, { fill: '#efe2c3' }).circle(230, 300, 22, { fill: '#efe2c3' });
  }, p, x, y, s, o);
  return r;
};
PR.stamp = (g, p, x, y, str, col = RED, s = 1, r = -0.18) => {
  if (p <= 0) return; const q = pop(p), sc = s * lerp(1.8, 1, q);
  g.save(); g.alpha(clamp(p * 3)); g.prop('stamp' + str + col, k => { const w = str.length * 52 + 70; k.rect(-w / 2, -70, w, 140, { col, w: 1.6, over: 0 }); k.text(str, 0, 34, { size: 96, font: 'k', col }); }, 1, x, y, sc, { r }); g.restore();
};
// Gavel at its block (x, y): f=1 points the head right, with the grip on the left.
// holderX selects the holder's side automatically; f=-1 mirrors it. Positive ang raises the head.
// gavelPose supplies scene-space grip/head/contact points before drawing a figure with handTo.
PR.gavelPose = (x, y, s = 1, ang = 0, o = {}) => {
  const f = o.holderX === undefined ? (o.f === -1 ? -1 : 1) : (o.holderX > x ? -1 : 1);
  const grip = [x - 232 * s * f, y - 78 * s], rotation = -f * ang;
  const at = (u, v) => { const [dx, dy] = rot(u * s * f, v * s, rotation); return [grip[0] + dx, grip[1] + dy]; };
  return { grip, head: at(232, 0), contact: at(232, 44), block: [x, y - 34 * s], f, rotation };
};
PR.gavel = (g, p, x, y, s = 1, ang = 0, o = {}) => {
  const pose = PR.gavelPose(x, y, s, ang, o);
  fadeWhole(g, o.a ?? 1, () => {
    g.prop('block', k => k.rect(-100, -34, 200, 34, { fill: '#8a5a33' }), p, x, y, s);
    g.save();
    try {
      g.tr(pose.grip[0], pose.grip[1], 1, pose.rotation); g.ctx.scale(pose.f, 1);
      g.prop('gavelHeld', k => {
        k.line(0, 0, 196, 0, { w: 1.5 });
        k.rect(196, -44, 72, 88, { fill: '#8a5a33' });
        k.line(196, -26, 268, -26, { w: 0.5 }).line(196, 26, 268, 26, { w: 0.5 });
      }, p, 0, 0, s);
    } finally { g.restore(); }
  });
  return pose;
};
PR.calendar = (g, p, x, y, s, top, big, o = {}) => g.prop('cal' + top + big, k => {
  k.rect(-170, -200, 340, 400, { fill: '#ffffff' }); k.rect(-170, -200, 340, 100, { fill: o.col || RED });
  for (const xx of [-90, 90]) k.rect(xx - 8, -230, 16, 50, { fill: '#ffffff' });
  const bs = o.size || 190; k.text(top, 0, -128, { size: 64, font: 'k', col: '#ffffff' }); k.text(big, 0, 50 + bs * 0.32, { size: bs, font: 'k' });
}, p, x, y, s, o);

// ── charts ─────────────────────────────────────────────────────────────────────────────────
// a line drawn left to right in step with p; pts in chart units 0..1 (x) and 0..1 (y up)
PR.chartLine = (g, p, x, y, w, h, pts, col, o = {}) => {
  const P = []; for (let i = 0; i < pts.length; i += 2) P.push(x + pts[i] * w, y - pts[i + 1] * h);
  g.wl(o.smooth === false ? P : spline(P, false, 10), o.seed ?? 3, p, { col, lw: o.lw ?? 1.6, amp: 1 });
  if (p > 0.02 && o.dot !== false) { // the pen tip / end dot follows the line
    const sp = spline(P, false, 10), n = sp.length / 2; let L = 0; const cum = [0]; for (let i = 1; i < n; i++) { L += Math.hypot(sp[2 * i] - sp[2 * i - 2], sp[2 * i + 1] - sp[2 * i - 1]); cum.push(L); }
    const tgt = p * L; let j = 1; while (j < n - 1 && cum[j] < tgt) j++; const f = (tgt - cum[j - 1]) / ((cum[j] - cum[j - 1]) || 1);
    g.dot(lerp(sp[2 * j - 2], sp[2 * j], f), lerp(sp[2 * j - 1], sp[2 * j + 1], f), 11, col);
  }
};
PR.axes = (g, p, x, y, w, h, o = {}) => g.prop(`axes${w}x${h}`, k => { k.path([0, -h, 0, 0, w, 0], { w: 1 }); }, p, x, y, 1);

// ── maps ───────────────────────────────────────────────────────────────────────────────────
// M: {lon0, lat0, k: pixels per degree of latitude, cx, cy}
// longitudes are unwrapped along the ring, so a ring that crosses the antimeridian stays in one piece
function mapPts(ring, M) {
  const lons = [], cl = Math.cos(M.lat0 * D2R); let off = 0, prev = ring[0], sum = 0;
  for (let i = 0; i < ring.length; i += 2) {
    let lon = ring[i] + off; const d = lon - prev;
    if (d > 180) { off -= 360; lon -= 360; } else if (d < -180) { off += 360; lon += 360; }
    prev = lon; lons.push(lon); sum += lon;
  }
  const shift = 360 * Math.round((M.lon0 - sum / lons.length) / 360), out = [];   // put the ring on the side of the map we're looking at
  for (let i = 0; i < lons.length; i++) out.push(M.cx + (lons[i] + shift - M.lon0) * M.k * cl, M.cy - (ring[2 * i + 1] - M.lat0) * M.k);
  return out;
}
function mapXY(lon, lat, M) { return [M.cx + (lon - M.lon0) * M.k * Math.cos(M.lat0 * D2R), M.cy - (lat - M.lat0) * M.k]; }
PR.land = (g, p, key, rings, M, o = {}) => g.sk('land/' + key, k => {
  for (const r of rings) { const pts = mapPts(r, M); const bb = bbox(pts); if (o.box && (bb[2] < o.box[0] || bb[0] > o.box[2] || bb[3] < o.box[1] || bb[1] > o.box[3])) continue; k.poly(pts, { fill: o.fill || PALE, col: o.col || GREY, w: o.w ?? 0.55, amp: 0.7, dx: 0, dy: 0 }); }
}, p, 0, 0, 1, { a: o.a });
PR.region = (g, p, key, rings, M, col = AMBER, o = {}) => g.sk('region/' + key, k => { for (const r of rings) k.fill(mapPts(r, M), col, { dx: 0, dy: 0, fa: o.fa ?? 1 }); }, p, 0, 0, 1);
PR.pin = (g, p, x, y, col = RED, s = 1) => {
  if (p <= 0) return; const q = clamp(p * 1.4), dy = -(1 - eout(q)) * 90, c = g.ctx;
  c.save(); c.translate(x, y + dy * s); c.scale(s, s); c.globalAlpha *= clamp(p * 4);
  c.beginPath(); c.moveTo(0, 0); c.bezierCurveTo(-26, -34, -30, -60, 0, -72); c.bezierCurveTo(30, -60, 26, -34, 0, 0); c.fillStyle = col; c.fill(); c.lineWidth = LW * 0.8 / s; c.strokeStyle = INK; c.stroke();
  c.beginPath(); c.arc(0, -46, 10, 0, Math.PI * 2); c.fillStyle = '#ffffff'; c.fill(); c.restore();
  if (p > 0.7) { c.save(); c.globalAlpha *= 0.8 * clamp((p - 0.7) * 3); c.beginPath(); c.ellipse(x, y + 3, 18 * s, 5 * s, 0, 0, Math.PI * 2); c.fillStyle = GREY; c.fill(); c.restore(); }
};
PR.route = (g, p, pts, col = INK, seed = 1, o = {}) => g.wl(spline(pts, false, 10), seed, p, { col, lw: o.lw ?? 1, dash: o.dash || [18, 16] });

// ── objects ────────────────────────────────────────────────────────────────────────────────
PR.crown = (g, p, x, y, s = 1) => g.prop('crown', k => { k.poly([-120, 60, -140, -80, -70, -10, 0, -110, 70, -10, 140, -80, 120, 60], { fill: AMBER }); for (const xx of [-140, 0, 140]) k.circle(xx, xx ? -88 : -120, 14, { fill: RED }); }, p, x, y, s);
PR.bicorne = (g, p, x, y, s = 1) => g.prop('bicorne', k => { k.stroke(arcPts(0, 0, 170, 80, Math.PI, Math.PI * 2), {}); k.fill([...arcPts(0, 0, 170, 80, Math.PI, Math.PI * 2)], '#24211e'); k.line(-170, 0, 170, 0); k.circle(60, -40, 24, { fill: BLUE }); }, p, x, y, s);
PR.book = (g, p, x, y, s = 1) => g.prop('book', k => {
  k.poly([0, -10, -300, -40, -300, 200, 0, 230], { fill: '#fffaf0' }).poly([0, -10, 300, -40, 300, 200, 0, 230], { fill: '#fffaf0' });
  k.line(0, -10, 0, 230, { w: 0.8 }); k.path([-300, 200, -310, 215, 0, 246, 310, 215, 300, 200], { w: 0.8 });
}, p, x, y, s);
PR.wanted = (g, p, x, y, s = 1, name = 'NED LUDD') => g.prop('wanted' + name, k => {
  k.poly([-200, -270, 200, -270, 200, 270, -200, 270], { fill: '#f7ecd2' });
  k.text('WANTED', 0, -170, { size: 92, font: 'k' });
  k.stroke(arcPts(0, -30, 70, 70, 0, Math.PI * 2.05), { dash: [14, 12], col: GREY, w: 1 });
  k.path([-90, 120, -60, 50, 60, 50, 90, 120], { dash: [14, 12], col: GREY, w: 1 });
  k.text('?', 0, 0, { size: 110, font: 'k', col: GREY });
  k.text(name, 0, 210, { size: 66, font: 'k' });
}, p, x, y, s);
PR.handcuffs = (g, p, x, y, s = 1) => g.prop('cuffs', k => { k.circle(-50, 0, 36, { w: 1.4 }); k.circle(50, 0, 36, { w: 1.4 }); k.path([-14, -6, 0, -14, 14, -6], { w: 1 }); }, p, x, y, s);
PR.shears = (g, p, x, y, s = 1, open = 0) => { // a cropper's shears seen from the side; `open` swings the top blade
  g.save(); g.tr(x, y, s);
  g.prop('shearLow', k => { k.poly([-260, 6, 220, -10, 232, 6, -260, 24], { fill: '#c9ccd1' }); k.rect(-330, -4, 80, 34, { fill: '#8a5a33' }); }, p, 0, 0, 1);
  g.save(); g.tr(-250, 6, 1, -open * 0.22); g.prop('shearTop', k => { k.poly([0, -6, 470, -26, 482, -12, 0, 12], { fill: '#d7dade' }); k.rect(-80, -26, 80, 34, { fill: '#8a5a33' }); k.circle(0, 0, 10, { fill: INK }); }, p, 0, 0, 1); g.restore();
  g.restore();
};
PR.cloth = (g, p, x, y, w, h, col = '#ececf3', seed = 1) => g.prop(`cloth${w}x${h}${col}`, k => { k.loop([-w / 2, -h / 2, w / 2, -h / 2 + 6, w / 2 - 4, h / 2, -w / 2 + 6, h / 2 - 4], { fill: col, smooth: false }); for (let i = 1; i < 5; i++) k.path([-w / 2 + 20, -h / 2 + i * h / 5, w / 2 - 20, -h / 2 + i * h / 5 + 4], { w: 0.4, col: GREY }); }, p, x, y, 1);
PR.loom = (g, p, x, y, s = 1, o = {}) => { // a handloom: two posts, beams, warp threads, and a beater that swings
  g.prop('loom', k => {
    k.line(-200, -330, -200, 0, { w: 1.2 }).line(200, -330, 200, 0, { w: 1.2 }).rect(-230, -350, 460, 30, { fill: WOOD });
    k.rect(-180, -60, 360, 26, { fill: WOOD }); k.rect(-180, -300, 360, 20, { fill: WOOD });
    for (let i = 0; i < 16; i++) k.line(-160 + i * 21.3, -280, -160 + i * 21.3, -60, { w: 0.35, col: GREY });
    k.rect(-160, -150, 320, 90, { fill: '#e7e2f2', w: 0.6 });
  }, p, x, y, s);
  if (p >= 1) { const k = Math.sin(g.t * (o.speed || 2.2)) * 0.5 + 0.5; g.save(); g.tr(x, y + lerp(-210, -170, k) * s, s); g.prop('beater', kk => kk.rect(-190, -10, 380, 20, { fill: '#c9a46c' }), 1, 0, 0, 1); g.restore(); }
};
PR.powerloom = (g, p, x, y, s = 1, o = {}) => {
  g.prop('powerloom', k => {
    k.rect(-240, -240, 480, 240, { fill: '#cfd6dc' }); k.circle(-150, -120, 60, { fill: '#ffffff' }); k.circle(150, -120, 40, { fill: '#ffffff' });
    k.rect(-200, -320, 400, 80, { fill: '#b7c0c8' }); k.line(-210, -120, 110, -120, { w: 0.7 });
  }, p, x, y, s);
  if (p >= 1) { const a = g.t * (o.speed || 4); for (const [cx, r] of [[-150, 60], [150, 40]]) { g.save(); g.tr(x + cx * s, y - 120 * s, s, a * (cx < 0 ? 1 : -1.5)); g.wl([-r * 0.8, 0, r * 0.8, 0], 3, 1, { lw: 0.8 }); g.wl([0, -r * 0.8, 0, r * 0.8], 4, 1, { lw: 0.8 }); g.restore(); } }
};
PR.shearframe = (g, p, x, y, s = 1) => {
  g.prop('shearframe', k => {
    k.rect(-320, -200, 640, 200, { fill: '#cfd6dc' }); k.rect(-280, -260, 560, 60, { fill: '#ececf3' });
    k.line(-340, -320, 340, -320, { w: 1.2 }); k.line(-300, -320, -300, -200).line(300, -320, 300, -200);
    k.circle(-250, -100, 50, { fill: '#ffffff' }); k.circle(250, -100, 50, { fill: '#ffffff' });
  }, p, x, y, s);
  if (p >= 1) { const u = (Math.sin(g.t * 3) * 0.5 + 0.5); g.save(); g.tr(x + lerp(-200, 200, u) * s, y - 300 * s, s); g.prop('shearHead', k => { k.rect(-60, -10, 120, 70, { fill: '#9ea7b0' }); k.line(-40, 60, 40, 60, { w: 1.4 }); }, 1, 0, 0, 1); g.restore(); }
};
PR.scale = (g, p, x, y, s = 1, tilt = 0) => { // balance scale; tilt > 0 drops the right pan
  g.prop('scaleBase', k => { k.line(0, -40, 0, -420, { w: 1.4 }); k.poly([-120, 0, 120, 0, 60, -40, -60, -40], { fill: WOOD }); k.circle(0, -430, 16, { fill: AMBER }); }, p, x, y, s);
  if (p <= 0.4) return; const q = clamp((p - 0.4) / 0.6), a = tilt * 0.32;
  const L = 330, lx = -Math.cos(a) * L, ly = -Math.sin(a) * L, rx = Math.cos(a) * L, ry = Math.sin(a) * L;
  g.save(); g.tr(x, y - 430 * s, s);
  g.wl([lx, ly, rx, ry], 11, q, { lw: 1.3 });
  for (const [px, py, sd] of [[lx, ly, 1], [rx, ry, 2]]) { g.wl([px, py, px - 70, py + 170], 20 + sd, q, { lw: 0.6 }); g.wl([px, py, px + 70, py + 170], 30 + sd, q, { lw: 0.6 }); g.wl(arcPts(px, py + 170, 110, 30, 0, Math.PI), 40 + sd, q, { lw: 1 }); }
  g.restore();
  return { left: [x + lx * s, y + (-430 + ly + 170) * s], right: [x + rx * s, y + (-430 + ry + 170) * s] };
};
PR.table = (g, p, x, y, s = 1) => g.prop('table', k => { k.rect(-400, -190, 800, 34, { fill: WOOD }); k.line(-360, -156, -340, 0, { w: 1.2 }).line(360, -156, 340, 0, { w: 1.2 }); }, p, x, y, s);
PR.pie = (g, p, x, y, r, split = 0.5, cols = [GREEN, AMBER], a0 = -Math.PI / 2) => {
  if (p <= 0) return; const c = g.ctx, q = eout(clamp(p * 1.3));
  c.save(); c.translate(x, y);
  const ang = split * Math.PI * 2;
  c.beginPath(); c.moveTo(0, 0); c.arc(0, 0, r * q, a0, a0 + ang); c.closePath(); c.fillStyle = cols[0]; c.fill();
  c.beginPath(); c.moveTo(0, 0); c.arc(0, 0, r * q, a0 + ang, a0 + Math.PI * 2); c.closePath(); c.fillStyle = cols[1]; c.fill();
  c.restore();
  g.wl(arcPts(x, y, r * q, r * q, 0, Math.PI * 2.03), 7, clamp(p * 1.2), { lw: 1 });
};
PR.robot = (g, p, x, y, s = 1, o = {}) => {
  g.prop('robot', k => {
    k.rect(-70, -330, 140, 110, { fill: '#e3e7eb' }); k.line(0, -330, 0, -370).circle(0, -380, 12, { fill: AMBER });
    k.circle(-30, -280, 14, { fill: '#ffffff' }).circle(30, -280, 14, { fill: '#ffffff' }); k.path([-28, -245, 0, -238, 28, -245], { w: 0.8 });
    k.rect(-85, -210, 170, 150, { fill: '#e3e7eb' }); k.rect(-40, -180, 80, 50, { fill: '#ffffff', w: 0.7 });
    k.line(-85, -190, -130, -100, { w: 1.2 }).line(85, -190, 130, -100, { w: 1.2 }); k.line(-40, -60, -45, 0, { w: 1.2 }).line(40, -60, 45, 0, { w: 1.2 });
  }, p, x, y, s, o);
  if (p >= 1) { const bl = (g.t % 3.3) < 0.15; g.dot(x - 30 * s, y - 280 * s, (bl ? 2 : 6) * s); g.dot(x + 30 * s, y - 280 * s, (bl ? 2 : 6) * s); }
};
PR.laptop = (g, p, x, y, s = 1) => g.prop('laptop', k => { k.poly([-150, -200, 150, -200, 150, -20, -150, -20], { fill: '#dfe7f0' }); k.poly([-190, 0, 190, 0, 150, -20, -150, -20], { fill: '#c9d2dc' }); }, p, x, y, s);
PR.clock = (g, p, x, y, s = 1, a = 0) => { g.prop('clock', k => { k.circle(0, 0, 120, { fill: '#ffffff' }); for (let i = 0; i < 12; i++) { const an = i / 12 * Math.PI * 2; k.line(Math.cos(an) * 100, Math.sin(an) * 100, Math.cos(an) * 112, Math.sin(an) * 112, { w: 0.6 }); } }, p, x, y, s); if (p >= 1) { g.save(); g.tr(x, y, s); g.wl([0, 0, Math.sin(a) * 70, -Math.cos(a) * 70], 1, 1, { lw: 1.2 }); g.wl([0, 0, Math.sin(a * 12) * 95, -Math.cos(a * 12) * 95], 2, 1, { lw: 0.8 }); g.dot(0, 0, 9); g.restore(); } };
PR.candle = (g, p, x, y, s = 1, flame = 1) => {
  g.prop('candle', k => { k.rect(-30, -200, 60, 200, { fill: '#fbf6ea' }); k.line(0, -200, 0, -224, { w: 0.8 }); k.loop([-80, 0, 80, 0, 70, 16, -70, 16], { fill: '#c9b48e', smooth: false }); }, p, x, y, s);
  if (p >= 1 && flame > 0.01) { const t = g.t, f = flame * (1 + 0.12 * Math.sin(t * 13) + 0.06 * Math.sin(t * 31)), c = g.ctx; c.save(); c.translate(x, y - 224 * s); c.scale(s * Math.max(0.2, flame), s * f); c.beginPath(); c.moveTo(0, 4); c.quadraticCurveTo(-26, -30, 0, -90); c.quadraticCurveTo(26, -30, 0, 4); c.fillStyle = AMBER; c.fill(); c.lineWidth = LW / s; c.strokeStyle = INK; c.stroke(); c.beginPath(); c.moveTo(0, 0); c.quadraticCurveTo(-10, -20, 0, -46); c.quadraticCurveTo(10, -20, 0, 0); c.fillStyle = MOON; c.fill(); c.restore(); }
};
PR.hourglass = (g, p, x, y, s = 1, flip = 0) => { g.save(); g.tr(x, y, s, flip * Math.PI); g.prop('hourglass', k => { k.rect(-110, -200, 220, 24, { fill: WOOD }).rect(-110, 176, 220, 24, { fill: WOOD }); k.path([-80, -176, -80, -120, -10, 0, -80, 120, -80, 176]).path([80, -176, 80, -120, 10, 0, 80, 120, 80, 176]); }, p, 0, 0, 1); g.restore(); };
PR.notes = (g, p, x, y, s = 1, seed = 1) => { for (let i = 0; i < 4; i++) { const u = ((g.t * 0.4 + i / 4 + seed) % 1), q = clamp(p * 4 - i); if (q <= 0) continue; const xx = x + (i - 1.5) * 70 * s + Math.sin(u * 6 + i) * 20 * s, yy = y - u * 220 * s; g.save(); g.alpha((1 - u) * q); g.prop('note' + (i % 2), k => { k.circle(0, 0, 18, { ry: 13, rot: -0.4, fill: INK }); k.line(16, -6, 16, -90, { w: 0.9 }); if (i % 2) k.path([16, -90, 50, -70, 50, -40], { w: 0.9 }); }, 1, xx, yy, s); g.restore(); } };
PR.lever = (g, p, x, y, s = 1, u = 0) => { // a big switch on a box; u = 0 up, 1 pulled down
  g.prop('leverBox', k => k.rect(-90, -80, 180, 80, { fill: '#cfd6dc' }), p, x, y, s);
  if (p >= 1) { const a = lerp(-0.55, 0.55, u); g.save(); g.tr(x, y - 80 * s, s, a); g.wl([0, 0, 0, -170], 9, 1, { lw: 1.4 }); g.restore(); const [hx, hy] = rot(0, -170, a); g.dot(x + hx * s, y - 80 * s + hy * s, 22 * s, RED); }
};
// PR.bag(g, p, x, y, s, { sym }): a money bag; o.sym the currency sign on it (default '£'; '$' for a dollar bag).
PR.bag = (g, p, x, y, s = 1, o = {}) => { const sym = o.sym ?? '£'; return g.prop(sym === '£' ? 'bag' : 'bag:' + sym, k => { k.loop([-90, 0, -110, -110, -40, -170, 40, -170, 110, -110, 90, 0], { fill: AMBER }); k.path([-40, -170, -60, -200, 0, -186, 60, -200, 40, -170]); k.text(sym, 0, -50, { size: 90, font: 'k' }); }, p, x, y, s); };
PR.toolbox = (g, p, x, y, s = 1) => g.prop('toolbox', k => { k.rect(-120, -120, 240, 120, { fill: GREEN }); k.path([-60, -120, -60, -170, 60, -170, 60, -120]); k.line(-120, -80, 120, -80, { w: 0.7 }); }, p, x, y, s);
PR.car = (g, p, x, y, s = 1) => g.prop('car', k => { k.loop([-170, -30, -160, -90, -90, -100, -50, -160, 70, -160, 120, -100, 170, -90, 175, -30], { fill: '#dfe7f0', smooth: false }); k.circle(-100, -20, 34, { fill: '#ffffff' }).circle(100, -20, 34, { fill: '#ffffff' }); k.rect(-30, -148, 80, 46, { fill: '#ffffff', w: 0.6 }); }, p, x, y, s);
PR.brush = (g, p, x, y, s = 1) => g.prop('brush', k => { k.line(-120, 120, 40, -40, { w: 1.6 }); k.poly([40, -40, 70, -90, 110, -100, 90, -60], { fill: RED }); }, p, x, y, s);
PR.brain = (g, p, x, y, s = 1) => g.prop('brain', k => { k.loop([-110, 20, -120, -60, -60, -110, 20, -110, 100, -80, 120, 0, 60, 50, -50, 50], { fill: '#f5d3d8' }); k.curve([-60, -90, -40, -40, -70, 10]); k.curve([10, -100, 0, -40, 30, 20]); k.curve([70, -70, 60, -10, 90, 20]); }, p, x, y, s);
PR.pen = (g, p, x, y, s = 1) => g.prop('penIcon', k => { k.poly([-110, 110, -80, 40, 70, -110, 110, -70, -40, 80], { fill: AMBER }); k.line(-110, 110, -86, 86); }, p, x, y, s);
PR.target = (g, p, x, y, r, col = RED) => g.prop('target' + r + col, k => { k.circle(0, 0, r, { col, w: 1.1 }); k.circle(0, 0, r * 0.55, { col, w: 1 }); k.line(-r * 1.25, 0, -r * 0.3, 0, { col }).line(r * 0.3, 0, r * 1.25, 0, { col }).line(0, -r * 1.25, 0, -r * 0.3, { col }).line(0, r * 0.3, 0, r * 1.25, { col }); }, p, x, y, 1);
PR.hand = (g, p, x, y, s = 1, o = {}) => { // a big hand reaching down from above; origin at the fingertips
  // the cuff is its own drawing, drawn after the hand: in one cached drawing the hand's top line showed through it
  const r = g.prop('bighand', k => {
    k.loop([-60, -40, -60, -120, -40, -330, 60, -330, 70, -120, 80, -60, 40, 0, -10, 10], { fill: '#ffffff' });
    k.path([-30, -60, -32, -10]).path([0, -70, 2, -2]).path([28, -66, 30, -14]);
  }, p, x, y, s, o);
  g.prop('bighandcuff' + (o.cuff || ''), k => { k.rect(-56, -420, 128, 90, { fill: o.cuff || AMBER }); }, p, x, y, s, o);
  return r;
};
PR.chains = (g, p, x, y, n = 6, s = 1) => { for (let i = 0; i < n; i++) { const q = clamp(p * n - i); if (q <= 0) break; g.prop('link' + (i % 2), k => { k.circle(0, 0, 34, { ry: 20, w: 1.3 }); }, q, x + i * 52 * s, y, s, { r: i % 2 ? 0 : 0, sy: i % 2 ? 0.55 : 1 }); } };
PR.iceberg = (g, p, x, y, s = 1) => g.prop('iceberg', k => {
  k.poly([-160, 0, -90, -140, -20, -110, 40, -200, 130, -60, 170, 0], { fill: '#eef6fc' });
  k.poly([-160, 0, 170, 0, 300, 140, 240, 330, 20, 420, -230, 330, -300, 150], { fill: '#d7eaf7' });
}, p, x, y, s);
PR.water = (g, p, x, y, w) => { const pts = []; for (let i = 0; i <= 30; i++) { const u = i / 30; pts.push(x - w / 2 + u * w, y + Math.sin(u * 24 + g.t * 1.5) * 6); } g.wl(pts, 19, p, { col: BLUE, lw: 1 }); };
PR.globe = (g, p, x, y, r, a = 0) => {
  g.prop('globe' + r, k => { k.circle(0, 0, r, { fill: '#eaf3fb' }); k.stroke(arcPts(0, 0, r, r * 0.3, 0, Math.PI * 2), { w: 0.6, col: GREY }); k.stroke(arcPts(0, 0, r * 0.35, r, -Math.PI / 2, Math.PI * 1.5), { w: 0.6, col: GREY }); }, p, x, y, 1);
};
PR.vice = (g, p, x, y, s = 1, u = 0) => { // two jaws closing; u = 0 open, 1 shut on the middle
  const gap = lerp(330, 150, u);
  g.prop('jawL', k => { k.rect(-60, -220, 60, 300, { fill: '#9ea7b0' }); }, p, x - gap, y, s);
  g.prop('jawR', k => { k.rect(0, -220, 60, 300, { fill: '#9ea7b0' }); }, p, x + gap, y, s);
  if (p >= 1) { g.wl([x - gap - 60 * s, y + 20 * s, x - 520 * s, y + 20 * s], 3, 1, { lw: 1.4 }); g.wl([x + gap + 60 * s, y + 20 * s, x + 520 * s, y + 20 * s], 4, 1, { lw: 1.4 }); }
};
PR.horse = (g, p, x, y, s = 1, ph = 0) => { // a simple trotting horse; origin under the body
  const c = g.ctx; g.save(); g.tr(x, y, s);
  g.prop('horseBody', k => { k.loop([-150, -170, -60, -200, 80, -195, 140, -170, 130, -120, -140, -120], { fill: '#c9a27a' }); k.poly([92, -192, 138, -284, 160, -306, 176, -292, 242, -262, 238, -236, 182, -230, 146, -172], { fill: '#c9a27a' }); k.path([150, -300, 160, -326, 172, -298], { w: 0.8 }); k.path([132, -270, 110, -232, 96, -200], { w: 0.7 }); k.path([-150, -165, -190, -110, -175, -100], { w: 1 }); }, p, 0, 0, 1);
  if (p >= 0.6) { const q = clamp((p - 0.6) / 0.4); [[-110, 0], [-80, Math.PI], [90, Math.PI], [120, 0]].forEach(([lx, off], i) => { const a = Math.sin(ph + off) * 0.45; g.wl([lx, -125, lx + Math.sin(a) * 60, -125 + Math.cos(a) * 62, lx + Math.sin(a * 0.5) * 70 - 6, 0], 50 + i, q, { lw: 1.1 }); }); g.dot(196, -270, 5); }
  g.restore();
};
PR.musketSmoke = (g, x, y, t0, t) => { const d = t - t0; if (d < 0 || d > 2.5) return; if (d < 0.12) PR.burst(g, 1, x, y, 40, AMBER, 7); PR.puff(g, x + d * 40, y - d * 30, 20 + d * 30, clamp(1 - d / 2.5) * 0.9); };
PR.blossom = (g, p, x, y, s = 1) => { g.prop('tree', k => { k.path([0, 0, -6, -160, 10, -260], { w: 2 }); k.path([-2, -150, -80, -230]).path([6, -200, 90, -270]); }, p, x, y, s); if (p >= 1) { for (let i = 0; i < 16; i++) { const a = hash(i * 3) * Math.PI * 2, r = 60 + hash(i * 7) * 80; const q = clamp(g.on(0, 1) * 1); g.save(); g.alpha(0.95); g.dot(x + (Math.cos(a) * r + 5) * s, y + (-250 + Math.sin(a) * r * 0.7) * s, (12 + hash(i) * 8) * s, i % 3 ? '#f3b7c8' : '#ffffff'); g.restore(); } } };
PR.lightning = (g, p, x, y, s = 1, col = AMBER) => g.prop('bolt' + col, k => k.poly([20, -160, -50, 10, 0, 10, -30, 160, 70, -30, 15, -30, 50, -160], { fill: col }), p, x, y, s);
PR.eye = (g, p, x, y, s = 1) => g.prop('eye', k => { k.stroke([...arcPts(0, 40, 110, 80, -2.4, -0.74)]); k.stroke([...arcPts(0, -40, 110, 80, 0.74, 2.4)]); k.circle(0, 0, 32, { fill: INK }); }, p, x, y, s);

// ── more props (people, homes, money, offices, a pharmacy) ─────────────────────────────────────
PR.swords = (g, p, x, y, s = 1) => g.prop('swords', k => { for (const sgn of [1, -1]) { k.line(-150 * sgn, 150, 150 * sgn, -150, { w: 1.3 }); k.line(-120 * sgn - 30, 120 - 30 * sgn, -120 * sgn + 30, 120 + 30 * sgn, { w: 1.4 }); } }, p, x, y, s);
PR.workshop = (g, p, x, y, s = 1) => g.prop('workshop', k => { k.rect(-230, -380, 460, 380, { fill: '#f6f3ee' }); k.poly([-262, -376, 0, -500, 262, -376], { fill: '#c9b48e' }); k.rect(-175, -305, 350, 230, { fill: '#ffffff', w: 0.8 }); }, p, x, y, s);
PR.chair = (g, p, x, y, s = 1, o = {}) => g.prop('chair' + (o.dash ? 'd' : ''), k => { const d = o.dash ? { dash: [12, 10], col: '#a39e96' } : {}; k.path([-50, -260, -50, 0], d).path([-50, -120, 60, -120, 60, 0], d).line(-50, -125, 60, -125, d); }, p, x, y, s);
PR.key = (g, p, x, y, s = 1) => g.prop('key', k => { k.circle(-70, 0, 46, { fill: AMBER }); k.circle(-70, 0, 14, { fill: '#ffffff' }); k.path([-24, 0, 110, 0]).path([80, 0, 80, 30]).path([105, 0, 105, 36]); }, p, x, y, s);
PR.hearth = (g, p, x, y, s = 1) => { g.prop('hearth', k => { k.rect(-200, -160, 400, 160, { fill: '#c98a6b' }); for (let r = 0; r < 3; r++) k.line(-200, -40 - r * 40, 200, -40 - r * 40, { w: 0.5 }); for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) k.line(-200 + 100 * c + (r % 2) * 50 + 25, -r * 40, -200 + 100 * c + (r % 2) * 50 + 25, -40 - r * 40, { w: 0.45 }); }, p, x, y, s); if (p >= 1) PR.fire(g, x, y - 160 * s, s, 1); };
PR.dcoin = (g, p, x, y, s = 1, col = AMBER) => g.prop('dcoin' + col, k => { k.circle(0, 0, 40, { fill: col }); k.text('$', 0, 17, { size: 50, font: 'k', col: INK }); }, p, x, y, s);
PR.home = (g, p, x, y, s = 1, o = {}) => g.prop('home' + (o.dark ? 'D' : '') + (o.fill || ''), k => {   // a little house; origin at the base, centre
  k.rect(-150, -200, 300, 200, { fill: o.fill || '#f6f3ee' });
  k.poly([-182, -194, 0, -330, 182, -194], { fill: '#d9c7a7' });
  k.rect(-30, -104, 60, 104, { fill: '#c9a77c' });
  k.rect(-120, -164, 58, 50, { fill: o.dark ? '#d5d8dc' : MOON }); k.rect(62, -164, 58, 50, { fill: o.dark ? '#d5d8dc' : MOON });
}, p, x, y, s, o);
PR.office = (g, p, x, y, s = 1) => g.prop('office', k => {   // an employer: an office block
  k.rect(-110, -260, 220, 260, { fill: '#e7ecf1' });
  for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) k.rect(-82 + c * 60, -232 + r * 46, 40, 28, { fill: '#ffffff', w: 0.55 });
  k.rect(-26, -62, 52, 62, { fill: '#c6d0da' });
}, p, x, y, s);
PR.capitol = (g, p, x, y, s = 1) => g.prop('capitol', k => {   // government: a pediment on columns
  k.poly([-170, -232, 0, -320, 170, -232], { fill: '#dfe7f4' });
  k.rect(-160, -232, 320, 26, { fill: '#e9eef7' });
  for (let i = 0; i < 4; i++) k.rect(-128 + i * 80, -206, 36, 170, { fill: '#ffffff', w: 0.8 });
  k.rect(-176, -36, 352, 36, { fill: '#e9eef7' });
}, p, x, y, s);
PR.safe = (g, p, x, y, s = 1) => g.prop('safe', k => {   // owning something: a strongbox
  k.rect(-110, -200, 220, 200, { fill: '#d4d8dd' }); k.rect(-90, -180, 180, 160, { fill: '#e6e9ec', w: 0.8 });
  k.circle(-8, -100, 40, { fill: '#ffffff' }); for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; k.line(-8 + Math.cos(a) * 30, -100 + Math.sin(a) * 30, -8 + Math.cos(a) * 38, -100 + Math.sin(a) * 38, { w: 0.5 }); }
  k.line(-8, -100, -8, -128, { w: 0.8 }); k.line(62, -130, 62, -70, { w: 1.4 }); k.text('$', -8, -136, { size: 0.1, font: 'k' });
}, p, x, y, s);
PR.pencil = (g, x, y, a = -0.75, s = 1) => g.prop('pencil', k => {   // tip at the origin
  k.poly([0, 0, 34, -14, 34, 14], { fill: '#f3d7a6' }); k.poly([10, -4, 10, 4, 0, 0], { fill: INK });
  k.poly([34, -14, 300, -14, 300, 14, 34, 14], { fill: AMBER }); k.rect(300, -14, 36, 28, { fill: '#f0a3a3' });
}, 1, x, y, s, { r: a });
PR.lamp = (g, p, x, y, len, s = 1) => g.prop('lamp' + len, k => { k.line(0, 0, 0, len, { w: 0.8 }); k.poly([-70, len + 70, -40, len, 40, len, 70, len + 70], { fill: AMBER }); }, p, x, y, s);
PR.chair2 = (g, p, x, y, s = 1, flip = false) => g.prop('kchair2', k => { k.rect(-62, -300, 22, 190, { fill: WOOD }); k.line(-58, -300, -44, -300, { w: 0.8 }); k.rect(-62, -118, 136, 20, { fill: WOOD }); k.line(-54, -98, -60, 0, { w: 1.2 }).line(64, -98, 70, 0, { w: 1.2 }); }, p, x, y, s, { flip });
PR.car2 = (g, p, x, y, s = 1) => g.prop('car2', k => {   // the family car, a little older than they'd like
  k.loop([-190, -34, -182, -96, -110, -108, -64, -170, 76, -170, 128, -108, 186, -98, 196, -34], { fill: '#9fb7cf', smooth: false });
  k.rect(-50, -158, 54, 46, { fill: '#ffffff', w: 0.6 }); k.rect(16, -158, 56, 46, { fill: '#ffffff', w: 0.6 });
  k.circle(-110, -24, 36, { fill: '#ffffff' }).circle(116, -24, 36, { fill: '#ffffff' });
  k.circle(-110, -24, 12, { fill: INK }).circle(116, -24, 12, { fill: INK });
  k.path([140, -90, 156, -76, 150, -60], { w: 0.6 });                       // a dent
  k.circle(-150, -66, 9, { fill: '#c07a45', w: 0.4 }); k.circle(-136, -56, 5, { fill: '#c07a45', w: 0.3 });   // rust
}, p, x, y, s);
PR.apartments = (g, p, x, y, s = 1) => g.prop('apartments', k => {   // a mid-rise block; origin at the base, centre
  k.rect(-280, -500, 560, 500, { fill: '#efe6dc' }); k.rect(-300, -520, 600, 22, { fill: '#d9cbb9' });
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) { if (r === 3 && (c === 1 || c === 2)) continue; k.rect(-240 + c * 128, -470 + r * 116, 76, 70, { fill: '#ffffff', w: 0.6 }); }
  k.rect(-60, -110, 120, 110, { fill: '#c9b39a' });
}, p, x, y, s);
PR.skyline = (g, p, x, y, w) => g.prop('skyline' + w, k => {
  let xx = -w / 2, i = 0;
  while (xx < w / 2 - 60) { const bw = 90 + hash(i * 5) * 120, bh = 160 + hash(i * 7 + 1) * 300; k.rect(xx, -bh, bw, bh, { fill: '#eef1f4', col: '#a9b0b8', w: 0.55 }); for (let r = 0; r < Math.floor(bh / 60) - 1; r++) k.line(xx + 16, -bh + 40 + r * 60, xx + bw - 16, -bh + 40 + r * 60, { w: 0.35, col: '#c4c9cf' }); xx += bw + 14; i++; }
}, p, x, y, 1);
PR.box = (g, p, x, y, s = 1) => g.prop('cbox', k => { k.rect(-60, -100, 120, 100, { fill: '#d9b27c' }); k.line(-60, -70, 60, -70, { w: 0.5 }); k.rect(-12, -100, 24, 30, { fill: '#c49a62', w: 0.5 }); }, p, x, y, s);
PR.warehouse = (g, p, x, y, s = 1) => g.prop('warehouse', k => {   // origin at the base, centre
  k.rect(-470, -330, 940, 330, { fill: '#e3e8ee' }); k.rect(-490, -352, 980, 24, { fill: '#cdd5de' });
  k.rect(-410, -294, 820, 78, { fill: '#ffffff', w: 0.9 });
  for (let i = 0; i < 3; i++) { const x0 = -385 + i * 275; k.rect(x0, -190, 220, 190, { fill: '#d5dbe2' }); for (let j = 1; j < 6; j++) k.line(x0 + 8, -190 + j * 31, x0 + 212, -190 + j * 31, { w: 0.4, col: '#9aa3ad' }); }
}, p, x, y, s);
PR.shelves = (g, p, x, y, s = 1) => g.prop('shelves', k => {   // origin top-left
  k.rect(0, 0, 760, 460, { fill: '#f4f1ec' });
  for (let r = 1; r < 4; r++) k.line(0, r * 115, 760, r * 115, { w: 0.9 });
  for (let r = 0; r < 4; r++) for (let i = 0; i < 9; i++) { const bx = 28 + i * 81 + (hash(r * 9 + i) - 0.5) * 10, by = r * 115 + 110, h = 52 + hash(r * 13 + i) * 26; k.rect(bx, by - h, 40, h, { fill: [AMBER, '#ffffff', '#f6c56b', '#cfe3f3'][(r + i) % 4], w: 0.55 }); k.rect(bx - 2, by - h - 12, 44, 12, { fill: '#ffffff', w: 0.5 }); }
}, p, x, y, s);
PR.counter = (g, p, x0, y0, w, h) => g.prop(`counter${w}x${h}`, k => { k.rect(0, 0, w, h, { fill: '#ffffff' }); k.rect(-16, -20, w + 32, 24, { fill: '#dfe7ec' }); }, p, x0, y0, 1);
PR.plus = (g, p, x, y, s = 1, col = GREEN) => g.prop('plus' + col, k => { k.poly([-20, -60, 20, -60, 20, -20, 60, -20, 60, 20, 20, 20, 20, 60, -20, 60, -20, 20, -60, 20, -60, -20, -20, -20], { fill: col }); }, p, x, y, s);
PR.bottle = (g, p, x, y, s = 1) => g.prop('rxbottle', k => { k.rect(-34, -110, 68, 110, { fill: AMBER }); k.rect(-40, -140, 80, 30, { fill: '#ffffff' }); k.rect(-26, -80, 52, 46, { fill: '#ffffff', w: 0.5 }); }, p, x, y, s);
PR.gauge = (g, p, x, y, r, u, o = {}) => {   // a dial; u: 0 (left) .. 1 (right)
  g.prop('gauge' + r + (o.zones ? 'z' : ''), k => {
    k.fill([...arcPts(0, 0, r, r, Math.PI, Math.PI * 2), r, 0, -r, 0], '#ffffff', { dx: 0, dy: 0 });
    if (o.zones) [[GREEN, 0, 0.5], [AMBER, 0.5, 0.78], [RED, 0.78, 1]].forEach(([col, a, b]) => k.fill([...arcPts(0, 0, r * 0.94, r * 0.94, Math.PI * (1 + a), Math.PI * (1 + b)), ...arcPts(0, 0, r * 0.64, r * 0.64, Math.PI * (1 + b), Math.PI * (1 + a))], col, { dx: 0, dy: 0, fa: 0.8 }));
    k.stroke(arcPts(0, 0, r, r, Math.PI, Math.PI * 2)); k.line(-r - 10, 0, r + 10, 0);
    for (let i = 0; i <= 10; i++) { const a = Math.PI * (1 + i / 10); k.line(Math.cos(a) * r * 0.86, Math.sin(a) * r * 0.86, Math.cos(a) * r, Math.sin(a) * r, { w: 0.6 }); }
  }, p, x, y, 1);
  if (p >= 0.8) { const a = Math.PI * (1 + clamp(u)); g.wl([x, y, x + Math.cos(a) * r * 0.8, y + Math.sin(a) * r * 0.8], 3, 1, { lw: 1.5, col: o.needle || RED }); g.dot(x, y, 13); }
};
PR.form = (g, p, x, y, s = 1, title = '', o = {}) => g.prop('form' + title + (o.col || ''), k => {
  k.poly([-150, -200, 100, -200, 150, -150, 150, 200, -150, 200], { fill: '#ffffff' }); k.path([100, -200, 100, -150, 150, -150], { w: 0.6 });
  if (title) k.text(title, -14, -122, { size: o.size || 36, font: 'k', col: o.col || INK });
  for (let i = 0; i < 6; i++) k.line(-110, -50 + i * 44, 110 - hash(i + title.length) * 50, -50 + i * 44, { w: 0.45, col: GREY });
}, p, x, y, s);
PR.jar = (g, p, x, y, s = 1, level = 0, col = '#d7ebdd') => {   // a glass jar; level 0..1
  if (level > 0 && p > 0.5) { const c = g.ctx, h = 296 * clamp(level); c.save(); c.translate(x, y); c.scale(s, s); c.globalAlpha *= clamp((p - 0.5) * 2); c.beginPath(); c.moveTo(-110, -14); c.lineTo(-110, -14 - h); c.lineTo(110, -14 - h); c.lineTo(110, -14); c.quadraticCurveTo(110, 0, 92, 0); c.lineTo(-92, 0); c.quadraticCurveTo(-110, 0, -110, -14); c.fillStyle = col; c.fill(); c.restore(); }
  g.prop('jar', k => { k.path([-96, -338, -116, -310, -116, -20, -98, 0, 98, 0, 116, -20, 116, -310, 96, -338], { w: 1.1 }); k.rect(-104, -372, 208, 34, { fill: '#d9dde2' }); k.path([-82, -280, -82, -70], { w: 0.5, col: '#c3d3df' }); }, p, x, y, s);
};
PR.basket = (g, p, x, y, s = 1) => g.prop('basket', k => {   // an index fund: a basket holding many small stocks
  for (let i = 0; i < 16; i++) { const bx = -152 + i * 19.5, h = 56 + hash(i * 3 + 1) * 120; k.rect(bx, -150 - h, 13, h, { fill: [GREEN, BLUE, AMBER, '#8fc1e8', '#c7b3d6'][i % 5], w: 0.45 }); }
  k.poly([-205, -170, 205, -170, 165, 0, -165, 0], { fill: '#d9b27c' });
  for (let i = 1; i < 4; i++) k.line(-205 + i * 10, -170 + i * 42, 205 - i * 10, -170 + i * 42, { w: 0.5 });
  for (let i = -3; i <= 3; i++) k.line(i * 52, -170, i * 41, 0, { w: 0.5 });
}, p, x, y, s);
PR.receipt = (g, p, x0, y0, w, h) => g.prop(`receipt${w}x${h}`, k => { const pts = [0, 0, w, 0, w, h]; const n = Math.round(w / 40); for (let i = n; i >= 0; i--) pts.push(i * w / n, h + (i % 2 ? 0 : 20)); k.poly(pts, { fill: '#ffffff' }); }, p, x0, y0, 1);
PR.valve = (g, p, x, y, s = 1, a = 0) => { g.prop('valveStem', k => k.rect(-16, -64, 32, 64, { fill: '#9ea7b0' }), p, x, y, s); g.prop('valveWheel', k => { k.circle(0, 0, 70, { w: 1.5, fill: '#f6d0c8' }); k.circle(0, 0, 14, { fill: INK }); for (let i = 0; i < 3; i++) { const an = i / 3 * Math.PI; k.line(Math.cos(an) * 66, Math.sin(an) * 66, -Math.cos(an) * 66, -Math.sin(an) * 66, { w: 1 }); } }, p, x, y - 64 * s - 70 * s, s, { r: a }); };
PR.dispenser = (g, p, x, y, s = 1, o = {}) => {   // an automated dispensing system; origin at the base, centre
  g.prop('dispenser', k => {
    k.rect(-280, -700, 560, 700, { fill: '#e6eaef' });
    k.rect(-252, -672, 330, 420, { fill: '#f6f8fa', w: 0.8 });
    for (let r = 0; r < 6; r++) for (let c = 0; c < 5; c++) k.rect(-238 + c * 64, -658 + r * 68, 48, 52, { fill: ['#ffffff', '#fde9c2', '#d8e5f9', '#ffffff', '#d3eedd'][(r + c) % 5], w: 0.5 });
    k.rect(108, -672, 146, 112, { fill: '#2f3a48' });
    k.rect(108, -528, 146, 34, { fill: '#cfd6dc', w: 0.7 });
    k.line(118, -470, 244, -470, { w: 0.6 }).line(118, -440, 244, -440, { w: 0.6 });
    k.rect(-252, -226, 504, 150, { fill: '#cfd6dc' });
    k.rect(-210, -64, 420, 64, { fill: '#9ea7b0' });
  }, p, x, y, s);
  if (p < 1 || o.still) return;
  const t = g.t, c = g.ctx;
  c.save(); c.translate(x, y); c.scale(s, s);
  // the screen counts
  c.fillStyle = '#7ee0a6'; c.font = `700 46px ${FONT.k}, ${FALLBACK}`; c.textAlign = 'center'; c.fillText(String(Math.floor((t * 7) % 1000)).padStart(3, '0'), 181, -598);
  // pills falling down the chute, bottles sliding out of the bay with labels on
  for (let i = 0; i < 6; i++) { const u = ((t * 1.7 + i / 6) % 1); c.beginPath(); c.ellipse(-120 + (i % 3) * 40, -250 + u * 120, 9, 6, 0, 0, Math.PI * 2); c.fillStyle = [AMBER, '#ffffff', BLUE][i % 3]; c.fill(); c.lineWidth = 2; c.strokeStyle = INK; c.stroke(); }
  for (let i = 0; i < 3; i++) { const u = ((t * 0.5 + i / 3) % 1), bx = lerp(-200, 220, u); c.fillStyle = AMBER; c.fillRect(bx - 20, -150, 40, 62); c.fillStyle = '#ffffff'; c.fillRect(bx - 23, -166, 46, 16); if (u > 0.4) { c.fillRect(bx - 15, -136, 30, 26); } c.lineWidth = 2.4; c.strokeStyle = INK; c.strokeRect(bx - 20, -150, 40, 62); }
  c.restore();
};
PR.clipboard = (g, p, x, y, s = 1) => g.prop('clipboard', k => {
  k.rect(-170, -230, 340, 460, { fill: '#d9b27c' }); k.rect(-140, -190, 280, 390, { fill: '#ffffff', w: 0.8 }); k.rect(-60, -250, 120, 50, { fill: '#9ea7b0' });
  for (let i = 0; i < 5; i++) { k.rect(-110, -150 + i * 72, 34, 34, { w: 0.7 }); k.line(-56, -133 + i * 72, 110 - hash(i * 5) * 50, -133 + i * 72, { w: 0.5, col: GREY }); }
}, p, x, y, s);
PR.paycheck = (g, p, x, y, s = 1) => g.prop('paycheck', k => {
  k.rect(-200, -90, 400, 180, { fill: '#e9f5ec' }); k.text('PAY', -150, -36, { size: 34, font: 'k', col: GREEN, align: 'left' });
  k.line(-150, 6, 60, 6, { w: 0.5, col: GREY }); k.rect(80, -24, 96, 46, { fill: '#ffffff', w: 0.6 }); k.text('$', 128, 12, { size: 34, font: 'k' }); k.path([40, 54, 80, 40, 110, 58, 160, 44], { w: 0.6 });
}, p, x, y, s);
PR.bill = (g, p, x, y, s = 1, str = '$') => g.prop('bill' + str, k => { k.rect(-130, -60, 260, 120, { fill: '#cfe8d6' }); k.circle(0, 0, 40, { fill: '#e9f5ec', w: 0.6 }); k.text(str, 0, 22, { size: str.length > 2 ? 46 : 60, font: 'k', col: '#1f6b3c' }); }, p, x, y, s);
PR.grocery = (g, p, x, y, s = 1) => g.prop('grocery', k => {
  k.path([-40, -240, -20, -330, 0, -240], { w: 1.2 }); k.loop([-10, -230, 30, -330, 60, -320, 40, -230], { fill: '#e2b373', smooth: false });
  k.circle(60, -250, 34, { fill: GREEN }); k.circle(-70, -244, 26, { fill: RED });
  k.poly([-130, -240, 130, -240, 110, 0, -110, 0], { fill: '#d9b27c' }); k.line(-120, -200, 120, -200, { w: 0.5 });
}, p, x, y, s);
PR.padlock = (g, p, x, y, s = 1) => g.prop('padlock', k => { k.stroke(arcPts(0, -40, 34, 40, Math.PI, Math.PI * 2), { w: 1.4 }); k.line(-34, -40, -34, -20, { w: 1.4 }).line(34, -40, 34, -20, { w: 1.4 }); k.rect(-50, -24, 100, 80, { fill: AMBER }); k.circle(0, 10, 10, { fill: INK }); }, p, x, y, s);
PR.cushion = (g, p, x, y, s = 1) => g.prop('cushion', k => { k.loop([-260, -10, -230, -60, 0, -70, 230, -60, 260, -10, 230, 4, 0, 10, -230, 4], { fill: '#fde9c2' }); k.path([-170, -40, -150, -20]).path([150, -42, 170, -20]); }, p, x, y, s);
PR.card = (g, p, x, y, s = 1) => g.prop('ccard', k => { k.loop(rrPts(-150, -95, 300, 190, 20), { fill: '#8fb3df', smooth: false }); k.rect(-150, -60, 300, 36, { fill: '#2f3a48', w: 0.5 }); k.rect(-110, 10, 60, 42, { fill: AMBER, w: 0.6 }); }, p, x, y, s);
PR.plate = (g, p, x, y, s = 1) => g.prop('plate', k => { k.circle(0, 0, 110, { ry: 40, fill: '#ffffff' }); k.circle(0, 0, 70, { ry: 24, w: 0.6 }); k.line(-150, -10, -150, 50, { w: 1 }).line(150, -10, 150, 50, { w: 1 }); }, p, x, y, s);
PR.cliff = (g, p, x0, x1, top, bot, side) => g.prop(`cliff${x0}_${x1}`, k => { const e = side > 0 ? x1 : x0, pts = side > 0 ? [x0, top, e, top, e + 10, top + 60, e - 18, top + 130, e + 4, bot, x0, bot] : [x1, top, e, top, e - 10, top + 60, e + 18, top + 130, e - 4, bot, x1, bot]; k.poly(pts, { fill: '#ece4d6' }); }, p, 0, 0, 1);
PR.piggy = (g, p, x, y, s = 1) => g.prop('piggy', k => {
  k.circle(0, -140, 170, { ry: 120, fill: '#f6c9d4' }); k.circle(170, -150, 40, { ry: 46, fill: '#f6c9d4' }); k.circle(160, -158, 6, { fill: INK }).circle(182, -150, 6, { fill: INK });
  k.poly([60, -250, 90, -300, 110, -236], { fill: '#f6c9d4' }); k.dot(100, -190, 9);
  for (const xx of [-110, -40, 50, 110]) k.rect(xx - 18, -40, 36, 40, { fill: '#f6c9d4' });
  k.rect(-50, -268, 100, 12, { fill: INK }); k.path([-170, -150, -210, -170, -200, -130], { w: 0.8 });
}, p, x, y, s);
PR.vault = (g, p, x, y, s = 1) => g.prop('swf', k => {   // a national fund: a vault with a flag
  k.rect(-150, -220, 300, 220, { fill: '#e7ecf1' }); k.circle(0, -110, 70, { fill: '#ffffff' }); k.circle(0, -110, 18, { fill: AMBER }); for (let i = 0; i < 6; i++) { const a = i / 6 * Math.PI * 2; k.line(Math.cos(a) * 22, -110 + Math.sin(a) * 22, Math.cos(a) * 62, -110 + Math.sin(a) * 62, { w: 0.6 }); }
  k.line(120, -220, 120, -330, { w: 1 }); k.poly([120, -330, 200, -310, 120, -290], { fill: BLUE });
}, p, x, y, s);
PR.cert = (g, p, x, y, s = 1, str = 'SHARE') => g.prop('cert' + str, k => { k.rect(-130, -90, 260, 180, { fill: '#fff6dd' }); k.rect(-114, -74, 228, 148, { w: 0.5, col: AMBER }); k.text(str, 0, -18, { size: 40, font: 'k', col: '#8a5a12' }); k.circle(70, 40, 22, { fill: AMBER }); }, p, x, y, s);
