// ═══════════════════════ figures: round heads, dot eyes, flat clothes ═══════════════════════
// Local units at scale 1: feet at y = 0, head centre at y = -214. Angles in degrees: 0 hangs straight
// down, positive swings forward (toward the way the figure faces). Knees bend backward with negative values.
const FIG = { hipY: -92, shY: -170, headY: -214, headR: 34, thigh: 46, shin: 46, upper: 46, fore: 44 };
const POSES = {
  stand: () => ({ la: [-8, 6], ra: [10, 8], ll: [4, -2], rl: [-5, -2] }),
  front: () => ({ la: [-14, 4], ra: [14, -4], ll: [7, 0], rl: [-7, 0] }),
  walk: (ph, k = 1) => ({ la: [-22 * k * Math.sin(ph), 18], ra: [22 * k * Math.sin(ph), 18], ll: [26 * k * Math.sin(ph), -26 * k * Math.max(0, Math.sin(ph + 1.6))],
    rl: [-26 * k * Math.sin(ph), -26 * k * Math.max(0, Math.sin(ph - 1.6))], bob: -3 * Math.abs(Math.cos(ph)) }),
  sneak: ph => ({ la: [30, 70], ra: [40, 70], ll: [22 * Math.sin(ph), -30 * Math.max(0, Math.sin(ph + 1.6)) - 10], rl: [-22 * Math.sin(ph), -30 * Math.max(0, Math.sin(ph - 1.6)) - 10], lean: 14, bob: -2 * Math.abs(Math.cos(ph)), hip: 8 }),
  march: ph => ({ la: [-26 * Math.sin(ph), 10], ra: [28, 112], ll: [20 * Math.sin(ph), -14 * Math.max(0, Math.sin(ph + 1.6))], rl: [-20 * Math.sin(ph), -14 * Math.max(0, Math.sin(ph - 1.6))], bob: -2.5 * Math.abs(Math.cos(ph)) }),
  run: ph => ({ la: [-45 * Math.sin(ph), 80], ra: [45 * Math.sin(ph), 80], ll: [40 * Math.sin(ph), -55 * Math.max(0, Math.sin(ph + 1.6))], rl: [-40 * Math.sin(ph), -55 * Math.max(0, Math.sin(ph - 1.6))], lean: 12, bob: -5 * Math.abs(Math.cos(ph)) }),
  point: (a = 92) => ({ la: [-8, 6], ra: [a, 0], ll: [4, -2], rl: [-5, -2] }),
  cheer: (k = 0) => ({ la: [-148 - 8 * Math.sin(k), -16], ra: [148 + 8 * Math.sin(k), 16], ll: [-8, 0], rl: [8, 0], front: 1 }),
  shrug: () => ({ la: [-55, 85], ra: [55, -85], ll: [6, 0], rl: [-6, 0], front: 1, head: 6 }),
  think: () => ({ la: [20, 70], ra: [28, 140], ll: [4, -2], rl: [-5, -2], head: -6 }),
  hold: (a = 55, b = 35) => ({ la: [a - 6, b], ra: [a, b], ll: [4, -2], rl: [-5, -2] }),
  carry: () => ({ la: [20, 95], ra: [26, 95], ll: [4, -2], rl: [-5, -2] }),
  sad: () => ({ la: [-4, 2], ra: [4, 2], ll: [3, 0], rl: [-3, 0], lean: 9, head: 16, hip: 3 }),
  sit: (k = 0) => ({ la: [55 + 6 * Math.sin(k), 40], ra: [62 - 6 * Math.sin(k), 38], ll: [86, -86], rl: [80, -84], hip: 44 }),
  sitIdle: () => ({ la: [20, 30], ra: [26, 34], ll: [86, -86], rl: [80, -84], hip: 44 }),
  // a hammer held in both hands: u = 0 raised behind the head, 1 struck down in front
  swing: u => { const a = lerp(212, 62, u), b = lerp(-30, 6, u); return { la: [a - 4, b], ra: [a, b], ll: [16, -6], rl: [-14, -4], lean: lerp(-10, 14, u), behind: u < 0.45 }; },
  shoulder: () => ({ la: [-6, 8], ra: [30, 125], ll: [4, -2], rl: [-5, -2] }),   // a tool resting on the shoulder
  scared: () => ({ la: [60, 120], ra: [70, 120], ll: [10, -4], rl: [-10, -4], lean: -10, head: -8 }),
  angry: (k = 0) => ({ la: [70 + 10 * Math.sin(k * 3), 100], ra: [80 - 10 * Math.sin(k * 3), 100], ll: [10, -2], rl: [-10, -2], lean: 6 }),
  speak: (k = 0) => ({ la: [-6, 8], ra: [48 + 14 * Math.sin(k * 1.7), 50 + 10 * Math.sin(k * 2.3)], ll: [4, -2], rl: [-5, -2], head: -3 }),
  orate: (k = 0) => ({ la: [-6, 8], ra: [118 + 10 * Math.sin(k * 1.3), 10], ll: [6, -2], rl: [-8, -2], head: -8, lean: -3 }),
  lie: () => ({ la: [-20, 10], ra: [30, 20], ll: [6, 0], rl: [-6, 0] }),
  wave: (k = 0) => ({ la: [-6, 8], ra: [150, 20 + 25 * Math.sin(k * 5)], ll: [4, -2], rl: [-5, -2] }),
  reach: () => ({ la: [80, 0], ra: [88, 0], ll: [14, -4], rl: [-10, -4], lean: 12 }),
  thumbs: () => ({ la: [-8, 6], ra: [40, 110], ll: [4, -2], rl: [-5, -2], thumb: 1 }),
};
const SHIRT = { worker: GREEN, owner: AMBER, soldier: RED, state: BLUE, plain: '#ffffff', grey: '#d6d3cd', dark: '#45413c', judge: '#2b2926', modern: '#8fc1e8' };

function limb(x, y, a1, l1, a2, l2) {
  const r1 = a1 * D2R, r2 = (a1 + a2) * D2R;
  const ex = x + Math.sin(r1) * l1, ey = y + Math.cos(r1) * l1, hx = ex + Math.sin(r2) * l2, hy = ey + Math.cos(r2) * l2;
  return [x, y, ex, ey, hx, hy];
}
// Legs never cross in a stance (David, 25 Oct 2026: "almost everyone still has their legs oddly crossed"). The standing
// poses aim each leg a few degrees past the other (stand, front, W_FRONT's [7, 0] / [-7, 0]), so the feet crossed. When a
// pose without a gait (gaits carry bob) would put the left foot at or past the right one, both legs turn out until the
// feet stand apart: 30 units facing front, 12 in profile. In profile a wide staggered stance (feet far apart, legs
// crossing up by the hip, like a stride) is left as it is; so are sitting and kneeling (a thigh past 40 degrees).
function legsApart(P, fr) {
  if (P.bob !== undefined || P.cross || Math.abs(P.ll[0]) > 40 || Math.abs(P.rl[0]) > 40) return P;
  const hx = fr ? 10 : 6, gap = fr ? 30 : 12, len = FIG.thigh + FIG.shin;
  const foot = (x, A) => x + Math.sin(A[0] * D2R) * FIG.thigh + Math.sin((A[0] + A[1]) * D2R) * FIG.shin;
  const d = foot(-hx, P.ll) - foot(hx, P.rl);
  const c = (d + gap) * (fr ? 1 : 1 - ss((d - 18) / 18));
  if (c <= 0) return P;
  const da = Math.asin(clamp(c / 2 / len, 0, 1)) / D2R;
  return { ...P, ll: [P.ll[0] - da, P.ll[1]], rl: [P.rl[0] + da, P.rl[1]] };
}

// o: x, y, s (scale), f (1 faces right, -1 left), pose (object), p (draw-on), t, seed, shirt, hat, hair, mouth,
//    brows, soot, hold {k, hand}, grey, a, front, rotate (radians, about the feet), dress, robe
// The kinds holdProp() and hat() draw. Anything else used to fall through and draw nothing; it now fails by name
// (hold: 'phone' as a bare string was once silently ignored; the API is hold: { k: 'phone' }).
const HOLD_KINDS = ['hammer', 'smallhammer', 'musket', 'torch', 'lantern', 'quill', 'gavel', 'letter', 'coin', 'phone', 'sign', 'magnifier', 'pen', 'shears'];
const HAT_KINDS = ['cap', 'tophat', 'shako', 'crown', 'bicorne', 'wig', 'bonnet', 'hood'];
function figure(g, o) {
  if (o.hold && !(typeof o.hold === 'object' && HOLD_KINDS.includes(o.hold.k)))
    throw new Error(`figure(): unsupported hold ${JSON.stringify(o.hold)}; use { k: '${HOLD_KINDS.join("' | '")}' }`);
  if (o.hat && !HAT_KINDS.includes(o.hat)) throw new Error(`figure(): unsupported hat ${JSON.stringify(o.hat)}; use '${HAT_KINDS.join("' | '")}'`);
  const a = clamp(o.a ?? 1), c = g.ctx;
  const draw = () => figureDraw(g, { ...o, a: 1 });
  // Invisible figures still supply attachment points to their callers.
  if (a <= 0 || c.globalAlpha <= 0) {
    c.save(); c.globalAlpha = 0;
    try { return draw(); } finally { c.restore(); }
  }
  return fadeWhole(g, a, draw);
}
function figureDraw(g, o) {
  const c = g.ctx, t = g.t, s = o.s ?? 1, f = o.f ?? 1, seed = o.seed ?? 1, P = o.pose || POSES.stand();
  const p = o.p ?? 1; if (p <= 0) return null;
  const grey = o.grey || 0;
  c.save();
  c.translate(o.x, o.y); if (o.rotate) c.rotate(o.rotate); c.scale(s * f, s);
  if (o.a !== undefined) c.globalAlpha *= o.a;
  const lw = LW / s * (o.lw ?? 1);
  const breathe = (o.still ? 0 : 1.6 * Math.sin(t * 2.1 + seed * 1.7));
  const hipY = FIG.hipY + (P.hip || 0) + (P.bob || 0), lean = (P.lean || 0) * D2R;
  const shX = Math.sin(lean) * (hipY - FIG.shY) * -1, shY = hipY - (hipY - FIG.shY) * Math.cos(lean) + breathe * 0.4;
  const headTilt = ((P.head || 0) + (o.head || 0)) * D2R;
  const hx = shX + Math.sin(lean + headTilt * 0.5) * 44 + (P.front || o.front ? 0 : 3), hy = shY - Math.cos(lean) * 44 + breathe * 0.2;
  const ink = grey ? GREY : INK;
  // parts in drawing order, each with its share of the draw-on
  const parts = [];
  const PL = legsApart(P, P.front || o.front);
  const legL = limb(-((P.front || o.front) ? 10 : 6), hipY, PL.ll[0], FIG.thigh, PL.ll[1], FIG.shin), legR = limb((P.front || o.front) ? 10 : 6, hipY, PL.rl[0], FIG.thigh, PL.rl[1], FIG.shin);
  const fr = P.front || o.front, sw = fr ? 19 : 4, lgw = fr ? 10 : 6;
  const armL = limb(shX - sw, shY + 6, P.la[0], FIG.upper, P.la[1], FIG.fore), armR = limb(shX + sw, shY + 6, P.ra[0], FIG.upper, P.ra[1], FIG.fore);
  const W2 = { head: 3, torso: 2, leg: 1, arm: 1, face: 1, acc: 1.2 };
  const order = ['head', 'torso', 'legL', 'legR', 'armL', 'armR', 'face', 'acc'];
  const wts = { head: W2.head, torso: W2.torso, legL: 1, legR: 1, armL: 1, armR: 1, face: W2.face, acc: W2.acc };
  const tot = order.reduce((a, k) => a + wts[k], 0); let acc = 0; const q = {};
  for (const k of order) { const a0 = acc / tot, a1 = (acc + wts[k]) / tot; acc += wts[k]; q[k] = p >= 1 ? 1 : clamp((p - a0) / (a1 - a0)); }
  const S = (pts, sd, qq, ww = 1, col = ink) => { if (qq <= 0) return; const st = mkStroke(wob(pts, seed * 13.1 + sd, 1.2, 90, 5)); c.lineCap = 'round'; c.lineJoin = 'round'; drawStroke(c, st, qq, lw * ww, col); };
  const foot = (L, sd, qq) => { if (qq < 1) return; const fx = L[4], fy = L[5]; S([fx - 3, fy, fx + 13, fy + 1], sd + 40, 1, 1); };
  // shadow on the ground
  if (!o.noShadow && !o.rotate && p > 0.2) { c.save(); c.globalAlpha *= 0.85 * clamp((p - 0.2) * 3); c.beginPath(); c.ellipse(4, 2, 46, 8, 0, 0, Math.PI * 2); c.fillStyle = PALE; c.fill(); c.restore(); }
  // far leg and arm behind the body
  S(legL, 1, q.legL); foot(legL, 1, q.legL);
  S(armL, 2, q.armL);
  // torso: a flat garment, or a single line
  const shirt = grey ? SHIRT.grey : (o.shirt === undefined ? '#ffffff' : (SHIRT[o.shirt] || o.shirt));
  if (q.torso > 0) {
    c.save(); c.translate(shX * 0.5, 0);
    const top = shY - 6, bot = hipY + 4, tw = o.thin ? 15 : 21, bw = o.thin ? 17 : 24;
    const dress = o.dress ? 44 : 0, robe = o.robe ? -bot - 6 : 0;
    const body = spline([-tw, top + 8, 0, top - 2, tw, top + 8, bw + dress * 0.5, bot + dress + robe, 0, bot + dress + robe + 3, -bw - dress * 0.5, bot + dress + robe], true, 6);
    if (q.torso >= 0.5) { c.save(); c.globalAlpha *= clamp((q.torso - 0.5) * 2); c.beginPath(); c.moveTo(body[0] + 2.5, body[1] + 2.5); for (let i = 2; i < body.length; i += 2) c.lineTo(body[i] + 2.5, body[i + 1] + 2.5); c.closePath(); c.fillStyle = shirt; c.fill(); c.restore(); }
    S(Array.from(body), 3, clamp(q.torso * 1.6));
    if (o.belt) S([-bw + 2, bot - 22, bw - 2, bot - 22], 30, clamp(q.torso * 2 - 1), 0.8);
    if (o.apron && q.torso >= 1) { c.save(); c.globalAlpha *= 0.95; c.fillStyle = o.apron; c.fillRect(-bw + 6, top + 26, (bw - 6) * 2, bot - top - 12 + dress); c.restore(); }
    if (o.crossbelt && q.torso >= 1) { S([-tw + 2, top + 10, bw - 4, bot - 6], 31, 1, 0.75, '#ffffff'); S([tw - 2, top + 10, -bw + 4, bot - 6], 32, 1, 0.75, '#ffffff'); }
    c.restore();
  }
  // near leg
  S(legR, 4, q.legR); foot(legR, 4, q.legR);
  if (P.behind) S(armR, 6, q.armR);
  // head
  if (q.head > 0) {
    const r = FIG.headR, hfill = o.soot ? SOOT : '#ffffff';
    if (q.head >= 0.6) { c.beginPath(); c.arc(hx + 2, hy + 2, r, 0, Math.PI * 2); c.fillStyle = grey ? '#ffffff' : hfill; c.fill(); }
    const a0 = -1.1 + hash(seed) * 0.8;
    S(arcPts(hx, hy, r, r, a0, a0 + Math.PI * 2.08, 0, 0.03, seed), 5, q.head);
    if (o.hair && q.head >= 1) hair(c, hx, hy, r, o.hair, lw, seed, grey, t);
  }
  // face
  if (q.face > 0 && !o.back) face(c, hx, hy, o, P, t, lw, seed, grey, q.face);
  // the near arm in front, and whatever it holds
  if (!P.behind) S(armR, 6, q.armR);
  const hand = (L, k) => { if (q[k] >= 1) { c.beginPath(); c.arc(L[4], L[5], 6.2, 0, Math.PI * 2); c.fillStyle = ink; c.fill(); } };
  hand(armL, 'armL'); hand(armR, 'armR');
  if (q.acc > 0) {
    if (o.hat) hat(c, hx, hy, o.hat, lw, seed, grey, q.acc, t);
    if (o.hold) holdProp(c, o.hold, o.hold.hand === 'l' ? armL : armR, lw, seed, grey, q.acc, t, armL, armR);
    if (o.scarf && !grey) { c.save(); c.globalAlpha *= q.acc; c.fillStyle = o.scarf === true ? GREEN : o.scarf; c.beginPath(); c.moveTo(shX - 14, shY - 2); c.lineTo(shX + 16, shY - 2); c.lineTo(shX + 4, shY + 22); c.closePath(); c.fill(); c.restore(); }
  }
  c.restore();
  // hands in scene coordinates, for anything that follows a hand
  const toScene = (x, y) => { let X = x * s * f, Y = y * s; if (o.rotate) [X, Y] = rot(X, Y, o.rotate); return [o.x + X, o.y + Y]; };
  return { hand: toScene(armR[4], armR[5]), handL: toScene(armL[4], armL[5]), head: toScene(hx, hy), headR: FIG.headR * s };
}

function face(c, hx, hy, o, P, t, lw, seed, grey, q) {
  const ink = grey ? GREY : INK, eyeCol = o.soot ? '#ffffff' : ink, front = o.front || P.front;
  const blinkPer = 3.7 + hash(seed) * 2.2, ph = ((t + hash(seed + 3) * 9) % blinkPer);
  const blink = !o.still && ph < 0.13;
  const ex = front ? [-11, 11] : [6, 21], ey = -5 + (o.lookUp ? -5 : 0) + (o.lookDown ? 4 : 0);
  c.save(); c.globalAlpha *= q;
  for (const dx of ex) {
    if (blink) { c.beginPath(); c.moveTo(hx + dx - 5, hy + ey); c.lineTo(hx + dx + 5, hy + ey); c.strokeStyle = eyeCol; c.lineWidth = lw * 0.6; c.stroke(); }
    else { c.beginPath(); c.arc(hx + dx, hy + ey, o.eyes === 'big' ? 6.5 : 4.8, 0, Math.PI * 2); c.fillStyle = eyeCol; c.fill(); }
  }
  const m = o.mouth || 'smile', mx = front ? 0 : 13, my = 13;
  c.strokeStyle = eyeCol; c.lineWidth = lw * 0.65; c.lineCap = 'round'; c.beginPath();
  if (m === 'smile') c.arc(hx + mx, hy + my - 7, 9, 0.35, Math.PI - 0.35);
  else if (m === 'frown') c.arc(hx + mx, hy + my + 8, 8, Math.PI + 0.45, -0.45);
  else if (m === 'flat') { c.moveTo(hx + mx - 7, hy + my); c.lineTo(hx + mx + 7, hy + my); }
  else if (m === 'o') { c.arc(hx + mx, hy + my, 5.5, 0, Math.PI * 2); }
  else if (m === 'grin') { c.arc(hx + mx, hy + my - 4, 10, 0.15, Math.PI - 0.15); c.closePath(); c.fillStyle = eyeCol; c.fill(); }
  else if (m === 'talk') { const k = 2 + 4 * Math.abs(Math.sin(t * 9 + seed)); c.ellipse(hx + mx, hy + my, 6, k, 0, 0, Math.PI * 2); }
  if (m !== 'grin') c.stroke();
  if (o.brows === 'angry') { c.beginPath(); for (const dx of ex) { c.moveTo(hx + dx - 7, hy + ey - 12 + (dx > ex[0] ? 0 : 0)); c.lineTo(hx + dx + 5, hy + ey - 7 - (dx === ex[0] ? -3 : 3)); } c.lineWidth = lw * 0.6; c.stroke(); }
  if (o.brows === 'worried') { c.beginPath(); for (const dx of ex) { c.moveTo(hx + dx - 6, hy + ey - 9 - (dx === ex[0] ? 3 : 0)); c.lineTo(hx + dx + 6, hy + ey - 9 - (dx === ex[0] ? 0 : 3)); } c.lineWidth = lw * 0.55; c.stroke(); }
  if (o.sweat) { const k = (t * 0.9 + hash(seed)) % 1; c.beginPath(); c.fillStyle = '#8fc6ee'; c.ellipse(hx - 30, hy - 10 + k * 26, 4, 6, 0, 0, Math.PI * 2); c.globalAlpha *= 1 - k; c.fill(); }
  c.restore();
}

function hair(c, hx, hy, r, kind, lw, seed, grey, t) {
  const col = grey ? GREY : (typeof kind === 'object' ? kind.col : '#3a2f27'), k = typeof kind === 'object' ? kind.k : kind;
  c.save(); c.fillStyle = col; c.strokeStyle = grey ? GREY : INK; c.lineWidth = lw * 0.7;
  if (k === 'curls') { // Byron: dark curls on top and at the back
    for (let i = 0; i < 7; i++) { const a = Math.PI * (1.05 + i * 0.13), x = hx + Math.cos(a) * r * 0.92, y = hy + Math.sin(a) * r * 0.92 - 4; c.beginPath(); c.arc(x, y, 11 + hash(seed + i) * 3, 0, Math.PI * 2); c.fill(); }
    c.beginPath(); c.arc(hx - r * 0.75, hy + 4, 12, 0, Math.PI * 2); c.fill();
  } else if (k === 'long') {
    c.beginPath(); c.moveTo(hx - r - 4, hy + 30); c.quadraticCurveTo(hx - r - 8, hy - r - 8, hx + 4, hy - r - 6); c.quadraticCurveTo(hx + r * 0.6, hy - r, hx + r * 0.2, hy - r * 0.4); c.lineTo(hx - r * 0.5, hy - 2); c.lineTo(hx - r * 0.6, hy + 34); c.closePath(); c.fill();
  } else if (k === 'bun') {
    c.beginPath(); c.arc(hx - r * 0.2, hy - r * 0.55, r * 0.75, Math.PI, 0); c.fill(); c.beginPath(); c.arc(hx - r * 0.95, hy - r * 0.55, 13, 0, Math.PI * 2); c.fill();
  } else if (k === 'grey') {
    c.fillStyle = grey ? GREY : '#9a958e'; c.beginPath(); c.arc(hx - 4, hy - r * 0.45, r * 0.85, Math.PI * 1.05, Math.PI * 1.95); c.fill();
  } else { // short
    c.beginPath(); c.arc(hx - 3, hy - r * 0.42, r * 0.92, Math.PI * 1.02, Math.PI * 1.98); c.lineTo(hx + r * 0.5, hy - r * 0.55); c.closePath(); c.fill();
  }
  c.restore();
}

function hat(c, hx, hy, kind, lw, seed, grey, q, t) {
  const ink = grey ? GREY : INK, r = FIG.headR;
  c.save(); c.globalAlpha *= q; c.lineWidth = lw * 0.85; c.strokeStyle = ink; c.lineJoin = 'round';
  const fillPath = (pts, col) => { c.beginPath(); c.moveTo(pts[0], pts[1]); for (let i = 2; i < pts.length; i += 2) c.lineTo(pts[i], pts[i + 1]); c.closePath(); c.fillStyle = grey ? PALE : col; c.fill(); c.stroke(); };
  if (kind === 'cap') { // flat cap: a soft crown over the top of the head and a short brim in front
    fillPath(Array.from(spline([hx - r - 3, hy - 8, hx - r * 0.55, hy - r - 2, hx + r * 0.25, hy - r - 9, hx + r * 0.95, hy - r + 3, hx + r + 22, hy - r + 13, hx + r * 0.45, hy - r + 12, hx - r * 0.2, hy - r + 13], true, 6)), '#7d756b');
  } else if (kind === 'tophat') {
    fillPath([hx - 26, hy - r + 8, hx - 22, hy - r - 66, hx + 22, hy - r - 66, hx + 26, hy - r + 8], '#24211e');
    fillPath([hx - 42, hy - r + 10, hx + 42, hy - r + 10, hx + 40, hy - r + 2, hx - 40, hy - r + 2], '#24211e');
    if (!grey) { c.fillStyle = AMBER; c.fillRect(hx - 23, hy - r - 6, 46, 9); }
  } else if (kind === 'shako') {
    fillPath([hx - 24, hy - r + 8, hx - 20, hy - r - 52, hx + 20, hy - r - 52, hx + 24, hy - r + 8], '#24211e');
    fillPath([hx - 6, hy - r + 8, hx + 40, hy - r + 12, hx + 34, hy - r + 4, hx - 6, hy - r + 2], '#24211e');
    c.beginPath(); c.ellipse(hx, hy - r - 60, 7, 13, 0, 0, Math.PI * 2); c.fillStyle = grey ? PALE : '#ffffff'; c.fill(); c.stroke();
  } else if (kind === 'crown') {
    fillPath([hx - 30, hy - r + 4, hx - 34, hy - r - 38, hx - 16, hy - r - 18, hx, hy - r - 44, hx + 16, hy - r - 18, hx + 34, hy - r - 38, hx + 30, hy - r + 4], AMBER);
  } else if (kind === 'bicorne') {
    c.beginPath(); c.ellipse(hx, hy - r + 2, 62, 30, 0, Math.PI, 0); c.closePath(); c.fillStyle = grey ? PALE : '#24211e'; c.fill(); c.stroke();
    if (!grey) { c.beginPath(); c.arc(hx + 18, hy - r - 12, 8, 0, Math.PI * 2); c.fillStyle = BLUE; c.fill(); }
  } else if (kind === 'wig') {
    c.fillStyle = grey ? PALE : '#f4f1ea';
    for (let i = 0; i < 11; i++) { const a = Math.PI * (0.62 + i * 0.16); for (const rr of [1.06, 1.32]) { const x = hx + Math.cos(a) * r * rr, y = hy + Math.sin(a) * r * rr * 0.95 + (rr > 1.2 ? 6 : 0); if (rr > 1.2 && (a < Math.PI * 0.8 || a > Math.PI * 2.2)) continue; c.beginPath(); c.arc(x, y, 12, 0, Math.PI * 2); c.fill(); c.stroke(); } }
  } else if (kind === 'bonnet') {
    c.beginPath(); c.arc(hx - 6, hy - 6, r + 12, Math.PI * 0.6, Math.PI * 1.85); c.fillStyle = grey ? PALE : '#f1e6d2'; c.fill(); c.stroke();
  } else if (kind === 'hood') {
    c.beginPath(); c.arc(hx - 4, hy - 4, r + 10, Math.PI * 0.55, Math.PI * 1.9); c.fillStyle = grey ? PALE : '#5c564e'; c.fill(); c.stroke();
  }
  c.restore();
}

// things held in a hand, drawn along the forearm
function holdProp(c, h, L, lw, seed, grey, q, t, armL, armR) {
  const ink = grey ? GREY : INK, [ex, ey, hx, hy] = [L[2], L[3], L[4], L[5]];
  const a = Math.atan2(hy - ey, hx - ex) + (h.ang || 0);
  c.save(); c.globalAlpha *= q; c.translate(hx, hy); c.rotate(a); c.lineWidth = lw * 0.85; c.strokeStyle = ink; c.lineCap = 'round'; c.lineJoin = 'round';
  const k = h.k;
  if (k === 'hammer') { // sledgehammer: the handle runs back through the hands, the head sits at the far end
    c.beginPath(); c.moveTo(-30, 0); c.lineTo(118, 0); c.lineWidth = lw * 1.25; c.stroke(); c.lineWidth = lw * 0.85;
    c.beginPath(); c.rect(108, -34, 34, 68); c.fillStyle = grey ? PALE : '#8b857c'; c.fill(); c.stroke();
  } else if (k === 'smallhammer') {
    c.beginPath(); c.moveTo(-8, 0); c.lineTo(62, 0); c.stroke(); c.beginPath(); c.rect(56, -20, 18, 40); c.fillStyle = grey ? PALE : '#8b857c'; c.fill(); c.stroke();
  } else if (k === 'musket') {
    c.rotate(-Math.PI / 2 + (h.tilt || 0)); c.beginPath(); c.moveTo(-60, 0); c.lineTo(150, 0); c.lineWidth = lw * 1.05; c.stroke();
    c.beginPath(); c.moveTo(-60, 0); c.lineTo(-92, 10); c.lineTo(-88, -6); c.closePath(); c.fillStyle = grey ? PALE : '#8a5a33'; c.fill(); c.stroke();
    c.beginPath(); c.moveTo(150, 0); c.lineTo(186, 0); c.lineWidth = lw * 0.5; c.stroke();
  } else if (k === 'torch') {
    c.beginPath(); c.moveTo(-10, 0); c.lineTo(70, 0); c.stroke();
    const fl = 1 + 0.2 * Math.sin(t * 13 + seed) + 0.1 * Math.sin(t * 29); c.save(); c.translate(78, 0); c.rotate(-a - Math.PI / 2 + Math.PI / 2);
    c.beginPath(); c.moveTo(0, 8); c.quadraticCurveTo(-18, -14 * fl, 0, -40 * fl); c.quadraticCurveTo(18, -14 * fl, 0, 8); c.fillStyle = grey ? PALE : AMBER; c.fill(); c.stroke(); c.restore();
  } else if (k === 'lantern') {
    c.rotate(-a); c.beginPath(); c.moveTo(0, 0); c.lineTo(0, 16); c.stroke(); c.beginPath(); c.rect(-15, 16, 30, 38); c.fillStyle = grey ? PALE : MOON; c.fill(); c.stroke();
  } else if (k === 'quill') {
    c.beginPath(); c.moveTo(-6, 0); c.quadraticCurveTo(30, -26, 66, -40); c.quadraticCurveTo(40, -10, -6, 0); c.fillStyle = '#ffffff'; c.fill(); c.stroke();
  } else if (k === 'gavel') {
    c.beginPath(); c.moveTo(-6, 0); c.lineTo(58, 0); c.stroke(); c.beginPath(); c.rect(52, -18, 26, 36); c.fillStyle = grey ? PALE : '#8a5a33'; c.fill(); c.stroke();
  } else if (k === 'letter') {
    c.rotate(-a); c.beginPath(); c.rect(-10, -36, 54, 40); c.fillStyle = '#fffaf0'; c.fill(); c.stroke();
  } else if (k === 'coin') {
    c.rotate(-a); c.beginPath(); c.arc(10, 0, 15, 0, Math.PI * 2); c.fillStyle = grey ? PALE : AMBER; c.fill(); c.stroke();
  } else if (k === 'phone') {
    c.rotate(-a); c.beginPath(); c.rect(-8, -34, 26, 44); c.fillStyle = '#ffffff'; c.fill(); c.stroke();
  } else if (k === 'sign') { // a placard on a stick
    c.rotate(-a); c.beginPath(); c.moveTo(0, 30); c.lineTo(0, -150); c.stroke();
    c.beginPath(); c.rect(-90, -260, 180, 120); c.fillStyle = '#ffffff'; c.fill(); c.stroke();
    if (h.text) { c.font = `700 ${h.size || 88}px ${FONT.k}, ${FALLBACK}`; c.textAlign = 'center'; c.fillStyle = h.col || INK; c.fillText(h.text, 0, -170); }
  } else if (k === 'magnifier') {
    c.beginPath(); c.moveTo(-6, 0); c.lineTo(40, 0); c.lineWidth = lw * 1.1; c.stroke(); c.beginPath(); c.arc(66, 0, 26, 0, Math.PI * 2); c.fillStyle = 'rgba(200,225,245,0.5)'; c.fill(); c.lineWidth = lw * 0.85; c.stroke();
  } else if (k === 'pen') {
    c.beginPath(); c.moveTo(-6, 0); c.lineTo(46, 0); c.lineWidth = lw * 1.1; c.stroke();
  } else if (k === 'shears') { // a cropper's enormous hand shears
    c.rotate(0.15); c.beginPath(); c.moveTo(-40, -8); c.lineTo(190, -14); c.lineTo(196, -2); c.lineTo(-40, 4); c.closePath(); c.fillStyle = grey ? PALE : '#c9ccd1'; c.fill(); c.stroke();
    c.beginPath(); c.moveTo(-40, 8); c.lineTo(186, 18); c.lineTo(190, 6); c.lineTo(-40, -2); c.closePath(); c.fill(); c.stroke();
    c.beginPath(); c.arc(-56, 0, 18, 0, Math.PI * 2); c.stroke();
  }
  c.restore();
}

// a crowd helper: n figures in a row with small variations
function crowd(g, n, x0, x1, y, o = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const u = n === 1 ? 0.5 : i / (n - 1), r = hash(i * 7 + (o.seed || 0));
    const x = lerp(x0, x1, u) + (r - 0.5) * (o.jit ?? 20), yy = y + (o.rows ? (i % o.rows) * (o.rowGap || 30) : 0);
    const oo = typeof o.each === 'function' ? o.each(i, u, r) : {};
    out.push(figure(g, { x, y: yy, s: o.s ?? 0.6, f: o.f ?? 1, seed: (o.seed || 0) + i * 3.3, shirt: o.shirt, hat: o.hat, pose: o.pose ? o.pose(i, r) : POSES.stand(), p: o.p ?? 1, grey: o.grey, mouth: o.mouth, hold: o.hold, ...oo }));
  }
  return out;
}
