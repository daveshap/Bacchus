// Bacchus Studio · HTML5 Canvas 2D Engine (BFG / Warm-Editorial architecture)
// Pure deterministic frame renderer: renderFrame(ctx, t, S) in 1920x1080 logical coordinates.
// Exposes the shared window.__film contract for multi-worker headless Chrome rendering,
// automated layout/occlusion/cue/jump QC, contact sheets, and interactive 60fps preview.
(function () {
  "use strict";

  const W = 1920;
  const H = 1080;
  const SPEC = window.__BACCHUS_FILM__;
  if (!SPEC || !SPEC.scenes) {
    window.__film_error = "Missing __BACCHUS_FILM__ specification.";
    return;
  }

  // ── Math & Easing ──────────────────────────────────────────────────────────────────────────
  const {clamp, lerp, ss, eio, eout, ein, hash, hstr, noise} = BFG_STUDIO;
  const back = u => {
    u = clamp(u);
    const c = 1.55;
    return 1 + (c + 1) * Math.pow(u - 1, 3) + c * Math.pow(u - 1, 2);
  };

  // ── Palette & Theme ────────────────────────────────────────────────────────────────────────
  const PRESET = SPEC.preset || "warm-editorial";
  const STYLE_ID = String(SPEC.styleId || PRESET).toLowerCase();
  const IS_DARK = PRESET === "night-signal";
  const IS_HAND = STYLE_ID.includes("hand-drawn") || STYLE_ID.includes("explainer");

  function parseHex(hex) {
    const clean = String(hex || "#000000").replace("#", "");
    const n = parseInt(clean.length === 3
      ? clean.split("").map(c => c + c).join("")
      : clean, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function toHex(r, g, b) {
    const c = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return "#" + c(r) + c(g) + c(b);
  }
  function mixColor(a, b, u) {
    const [r1, g1, b1] = parseHex(a);
    const [r2, g2, b2] = parseHex(b);
    return toHex(lerp(r1, r2, u), lerp(g1, g2, u), lerp(b1, b2, u));
  }
  function hexAlpha(hex, a) {
    const [r, g, b] = parseHex(hex);
    return `rgba(${r},${g},${b},${clamp(a)})`;
  }
  function luminance(hex) {
    const [r, g, b] = parseHex(hex);
    return 0.299 * r + 0.587 * g + 0.114 * b;
  }

  const RAW_PAL = SPEC.palette || {};
  const PAL = {
    paper: RAW_PAL.paper || (IS_DARK ? "#141d2c" : PRESET === "studio-ink" ? "#f8f7f3" : "#f3e8d8"),
    ink: RAW_PAL.ink || (IS_DARK ? "#f1f6ff" : PRESET === "studio-ink" ? "#202120" : "#26372d"),
    muted: RAW_PAL.muted || (IS_DARK ? "#b1c2d9" : PRESET === "studio-ink" ? "#494b48" : "#505c50"),
    accent: RAW_PAL.accent || (IS_DARK ? "#6ce9c1" : PRESET === "studio-ink" ? "#634f89" : "#b86b49"),
    rule: RAW_PAL.rule || (IS_DARK ? "#405371" : PRESET === "studio-ink" ? "#b9b7b3" : "#d8c6ac"),
    matte: RAW_PAL.matte || null,
  };
  if (!PAL.matte) PAL.matte = mixColor(PAL.paper, PAL.ink, IS_DARK ? 0.12 : 0.06);
  PAL.card = IS_DARK ? mixColor(PAL.paper, "#ffffff", 0.06) : mixColor(PAL.paper, "#ffffff", 0.42);
  PAL.cardAlt = mixColor(PAL.paper, PAL.accent, IS_DARK ? 0.16 : 0.11);
  PAL.accentPale = mixColor(PAL.paper, PAL.accent, IS_DARK ? 0.24 : 0.18);
  PAL.good = IS_DARK ? "#5dd39e" : "#2e7d52";
  PAL.bad = IS_DARK ? "#f27878" : "#b9463c";
  PAL.blue = IS_DARK ? "#64a6df" : "#53748a";
  PAL.mustard = IS_DARK ? "#e6b85c" : "#c68e38";
  PAL.sage = IS_DARK ? "#88b38c" : "#6c8462";
  PAL.shadow = IS_DARK ? "rgba(0,0,0,0.34)" : "rgba(65,45,25,0.12)";
  PAL.paperIsDark = luminance(PAL.paper) < 128;

  const FONT_DISPLAY = IS_HAND
    ? '"Kalam", "Montserrat", "Segoe UI", sans-serif'
    : PRESET === "night-signal"
      ? '"Montserrat", "Segoe UI", Arial, sans-serif'
      : '"Montserrat", Georgia, "Segoe UI", sans-serif';
  const FONT_BODY = IS_HAND
    ? '"PatrickHand", "Montserrat", "Segoe UI", sans-serif'
    : '"Montserrat", "Segoe UI", Arial, sans-serif';
  const FONT_KICKER = '"Bebas Neue", "Montserrat", "Segoe UI", sans-serif';
  const FONT_QUOTE = '"Caveat", "Kalam", Georgia, serif';

  function fontStr(size, weight = 600, family = FONT_BODY, italic = false) {
    return `${italic ? "italic " : ""}${weight} ${Math.round(size * 10) / 10}px ${family}`;
  }

  // ── Timing & Cue Lookups (BFG-compatible) ──────────────────────────────────────────────────
  const TIMING = SPEC.timing || { dur: 1, lines: [], words: [] };
  const DUR = Number(TIMING.dur) || 1;
  const LINES = (TIMING.lines || []).map((ln, idx) => ({
    id: ln.id ?? (idx + 1),
    t0: Number(ln.start) || 0,
    t1: Number(ln.end ?? ln.start) || 0,
    text: String(ln.text || ""),
    firstWord: ln.firstWord || 0,
    lastWord: ln.lastWord || 0,
    toks: ln.toks || [],
  }));
  const WORDS = (TIMING.words || []).map((wd, idx) => ({
    id: wd.id ?? (idx + 1),
    text: String(wd.text || ""),
    start: Number(wd.start) || 0,
    end: Number(wd.end ?? wd.start) || 0,
  }));

  const {l0, l1, w, we} = BFG_STUDIO;
  function wordTime(wordId) {
    const word = WORDS[wordId - 1];
    if (!word) throw new Error("Unknown narration word " + wordId);
    return word.start;
  }
  function talking(t) {
    for (let i = 0; i < LINES.length; i++) {
      if (t >= LINES[i].t0 && t <= LINES[i].t1) return true;
    }
    return false;
  }

  // ── Audit Registry (exact BFG / Warm measured-box model) ───────────────────────────────────
  const AUDIT = { on: false, S: 1, inTransition: false, items: [] };
  window.__SUPPRESS_TEXT = false;

  function regBox(c, sceneId, kind, x0, y0, x1, y1, str = "") {
    if (!AUDIT.on) return;
    if (!(x1 > x0 && y1 > y0)) return;
    const m = c.getTransform();
    const S = AUDIT.S || 1;
    const pts = [
      [x0, y0], [x1, y0], [x0, y1], [x1, y1],
    ].map(([x, y]) => [(m.a * x + m.c * y + m.e) / S, (m.b * x + m.d * y + m.f) / S]);
    const xs = pts.map(p => p[0]);
    const ys = pts.map(p => p[1]);
    AUDIT.items.push({
      kind,
      id: sceneId,
      str: String(str || ""),
      a: Math.round(c.globalAlpha * 1000) / 1000,
      inTransition: Boolean(AUDIT.inTransition),
      box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)].map(v => Math.round(v)),
    });
  }

  // ── Procedural Backdrops ───────────────────────────────────────────────────────────────────
  let PAPER_CANVAS = null;
  function makeBackdrop() {
    const cv = document.createElement("canvas");
    cv.width = W;
    cv.height = H;
    const c = cv.getContext("2d");
    c.fillStyle = PAL.paper;
    c.fillRect(0, 0, W, H);

    if (PRESET === "night-signal" || PAL.paperIsDark) {
      // Crisp operational drafting dot-grid over dark slate (highly PNG-compressible)
      c.fillStyle = hexAlpha(PAL.rule, 0.24);
      for (let y = 72; y < H - 48; y += 54) {
        for (let x = 72; x < W - 48; x += 54) {
          c.fillRect(x, y, 2, 2);
        }
      }
      c.strokeStyle = hexAlpha(PAL.rule, 0.35);
      c.lineWidth = 1.5;
      c.strokeRect(32, 32, W - 64, H - 64);
    } else if (PRESET === "studio-ink") {
      // Crisp archival paper with subtle museum-mat inner hairline
      c.strokeStyle = hexAlpha(PAL.rule, 0.42);
      c.lineWidth = 1.5;
      c.strokeRect(36, 36, W - 72, H - 72);
    } else {
      // Warm editorial paper: clean flat cream stock + delicate organic paper fibers (BFG PNG-fast)
      let seed = 12345;
      const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
      c.save();
      c.globalAlpha = 0.055;
      c.strokeStyle = PAL.rule;
      c.lineWidth = 1.4;
      for (let i = 0; i < 160; i++) {
        const x = 40 + rand() * (W - 80);
        const y = 40 + rand() * (H - 80);
        const len = 10 + rand() * 20;
        const ang = rand() * Math.PI;
        c.beginPath();
        c.moveTo(x, y);
        c.quadraticCurveTo(
          x + Math.cos(ang) * len * 0.5 + 3,
          y + Math.sin(ang) * len * 0.5,
          x + Math.cos(ang) * len,
          y + Math.sin(ang) * len,
        );
        c.stroke();
      }
      c.restore();
      c.strokeStyle = hexAlpha(PAL.rule, 0.32);
      c.lineWidth = 1.5;
      c.strokeRect(34, 34, W - 68, H - 68);
    }
    // Keep top-left corner pixel exact palette paper for crisp edge sampling
    c.fillStyle = PAL.paper;
    c.fillRect(0, 0, 2, 2);
    return cv;
  }
  function drawBackdrop(c) {
    if (!PAPER_CANVAS) PAPER_CANVAS = makeBackdrop();
    c.drawImage(PAPER_CANVAS, 0, 0);
  }

  // ── Canvas Drawing Primitives (BFG + Warm-Editorial) ───────────────────────────────────────
  function rr(c, x, y, w, h, r = 16) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
  function inkPath(c, fill, lw = 2.6, stroke = PAL.ink) {
    if (fill) { c.fillStyle = fill; c.fill(); }
    if (lw > 0) {
      c.lineWidth = lw;
      c.strokeStyle = stroke;
      c.lineJoin = "round";
      c.lineCap = "round";
      c.stroke();
    }
  }

  function wrapLines(c, str, maxW) {
    const out = [];
    for (const para of String(str || "").split("\n")) {
      if (!maxW || maxW <= 0) { out.push(para); continue; }
      let cur = "";
      for (const word of para.split(/\s+/).filter(Boolean)) {
        const cand = cur ? cur + " " + word : word;
        if (cur && c.measureText(cand).width > maxW) {
          out.push(cur);
          cur = word;
        } else if (!cur && c.measureText(word).width > maxW) {
          let piece = "";
          for (const ch of word) {
            if (piece && c.measureText(piece + ch).width > maxW) {
              out.push(piece);
              piece = ch;
            } else {
              piece += ch;
            }
          }
          cur = piece;
        } else {
          cur = cand;
        }
      }
      if (cur || para === "") out.push(cur);
    }
    return out;
  }

  function txt(g, str, x, y, o = {}) {
    const text = String(str ?? "").trim();
    if (!text) return { w: 0, h: 0, lines: 0, size: o.size || 36 };
    const c = g.c;
    let size = o.size || 42;
    const minSize = o.minSize || 16;
    const weight = o.weight ?? 600;
    const family = o.family || FONT_BODY;
    const italic = Boolean(o.italic);
    const lhRatio = o.lh || 1.24;
    let lh, lines, widths;
    c.save();
    for (let k = 0; k < 48; k++) {
      lh = lhRatio * size;
      c.font = fontStr(size, weight, family, italic);
      lines = wrapLines(c, text, o.maxW);
      widths = lines.map(ln => c.measureText(ln).width);
      const wide = o.maxW && Math.max(0, ...widths) > o.maxW + 1;
      const tall = o.maxH && lines.length * lh > o.maxH + 1;
      if (!(wide || tall) || size * 0.94 < minSize) break;
      size *= 0.94;
    }
    c.fillStyle = o.col || PAL.ink;
    c.textBaseline = o.base || "top";
    c.textAlign = "left";
    const wmax = Math.max(0, ...widths);
    const totalH = lines.length * lh;
    const p = clamp(o.p ?? 1);
    const allWords = lines.reduce((sum, ln) => sum + (ln ? ln.split(" ").length : 0), 0);
    let wordIndex = 0;
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    const startY = o.vcenter ? y - totalH / 2 : y;
    lines.forEach((ln, i) => {
      const lw = widths[i];
      const yy = startY + i * lh;
      let xx = o.align === "center" ? x - lw / 2 : o.align === "right" ? x - lw : x;
      if (ln.trim()) {
        const m = c.measureText(ln);
        const topOffset = c.textBaseline === "middle" ? m.actualBoundingBoxAscent : 0;
        const botOffset = c.textBaseline === "middle" ? m.actualBoundingBoxDescent : (m.actualBoundingBoxDescent || size * 0.85);
        bx0 = Math.min(bx0, xx);
        bx1 = Math.max(bx1, xx + lw);
        by0 = Math.min(by0, yy - topOffset);
        by1 = Math.max(by1, yy + Math.max(botOffset, size * 0.82));
      }
      if (!window.__SUPPRESS_TEXT && p > 0) {
        if (p >= 0.999 || !o.wordReveal) {
          c.fillText(ln, xx, yy);
          wordIndex += ln ? ln.split(" ").length : 0;
        } else {
          const span = Math.max(1, allWords);
          for (const word of ln.split(" ")) {
            const q = clamp((p * (span + 0.65) - wordIndex) / 0.85);
            wordIndex++;
            if (q > 0) {
              c.save();
              c.globalAlpha *= eout(q);
              c.fillText(word, xx, yy + (1 - eout(q)) * size * 0.22);
              c.restore();
            }
            xx += c.measureText(word + " ").width;
          }
        }
      }
    });
    if (bx1 > bx0 && p > 0.05 && !o.noAudit) {
      regBox(c, g.s?.id || "scene", "text", bx0, by0, bx1, by1, text);
    }
    if (o.under && p > 0.05) {
      const uy = startY + totalH + 6;
      const ux0 = o.align === "center" ? x - wmax / 2 : o.align === "right" ? x - wmax : x;
      c.strokeStyle = o.under;
      c.lineWidth = Math.max(3.5, size * 0.08);
      c.lineCap = "round";
      c.beginPath();
      c.moveTo(ux0, uy);
      c.lineTo(ux0 + wmax * clamp(o.underP ?? p), uy);
      c.stroke();
    }
    c.restore();
    return { w: wmax, h: totalH, lines: lines.length, size };
  }

  function card(g, x, y, w, h, o = {}) {
    const c = g.c;
    const r = o.r ?? 20;
    c.save();
    if (o.shadow !== false) {
      c.save();
      rr(c, x + 5, y + 8, w, h, r);
      c.fillStyle = PAL.shadow;
      c.fill();
      c.restore();
    }
    rr(c, x, y, w, h, r);
    inkPath(c, o.fill || PAL.card, o.lw ?? 2.5, o.stroke || PAL.rule);
    if (!o.noAudit) regBox(c, g.s?.id || "scene", "card", x, y, x + w, y + h, o.label || "");
    if (o.band) {
      c.save();
      rr(c, x, y, w, h, r);
      c.clip();
      c.fillStyle = o.bandCol || PAL.accent;
      c.fillRect(x, y, w, o.band);
      c.restore();
      rr(c, x, y, w, h, r);
      inkPath(c, null, o.lw ?? 2.5, o.stroke || PAL.rule);
    }
    c.restore();
  }

  function pill(g, str, x, y, o = {}) {
    const c = g.c;
    let size = o.size || 28;
    const padX = o.padX ?? size * 0.72;
    const h = o.h ?? size * 1.65;
    c.save();
    c.font = fontStr(size, o.weight ?? 700, o.family || FONT_BODY);
    let tw = c.measureText(str).width;
    if (o.maxW && tw + padX * 2 > o.maxW) {
      size *= Math.max(0.5, (o.maxW - padX * 2) / tw);
      c.font = fontStr(size, o.weight ?? 700, o.family || FONT_BODY);
      tw = c.measureText(str).width;
    }
    const w = tw + padX * 2;
    const x0 = o.align === "left" ? x : o.align === "right" ? x - w : x - w / 2;
    rr(c, x0, y - h / 2, w, h, h / 2);
    inkPath(c, o.fill || PAL.accentPale, o.lw ?? 0, o.stroke || PAL.accent);
    if (!window.__SUPPRESS_TEXT) {
      c.fillStyle = o.col || PAL.ink;
      c.textBaseline = "middle";
      c.textAlign = "left";
      c.fillText(str, x0 + padX, y + 1);
    }
    if (!o.noAudit) regBox(c, g.s?.id || "scene", "pill", x0, y - h / 2, x0 + w, y + h / 2, str);
    c.restore();
    return w;
  }

  function polyPart(c, pts, p) {
    let total = 0;
    const segs = [];
    for (let i = 2; i < pts.length; i += 2) {
      const d = Math.hypot(pts[i] - pts[i - 2], pts[i + 1] - pts[i - 1]);
      segs.push(d);
      total += d;
    }
    let left = total * clamp(p);
    c.moveTo(pts[0], pts[1]);
    let ex = pts[0], ey = pts[1], ang = 0;
    for (let i = 2, j = 0; i < pts.length; i += 2, j++) {
      const d = segs[j];
      ang = Math.atan2(pts[i + 1] - pts[i - 1], pts[i] - pts[i - 2]);
      if (left >= d) {
        c.lineTo(pts[i], pts[i + 1]);
        ex = pts[i]; ey = pts[i + 1];
        left -= d;
      } else {
        const f = d ? left / d : 0;
        ex = pts[i - 2] + (pts[i] - pts[i - 2]) * f;
        ey = pts[i - 1] + (pts[i + 1] - pts[i - 1]) * f;
        c.lineTo(ex, ey);
        break;
      }
    }
    return { x: ex, y: ey, a: ang };
  }

  function arrow(g, pts, o = {}) {
    const p = clamp(o.p ?? 1);
    if (p <= 0 || pts.length < 4) return;
    const c = g.c;
    const col = o.col || PAL.accent;
    const lw = o.lw ?? 4.5;
    c.save();
    c.strokeStyle = col;
    c.lineWidth = lw;
    c.lineCap = "round";
    c.lineJoin = "round";
    if (o.dash) c.setLineDash(o.dash);
    c.beginPath();
    const end = polyPart(c, pts, p);
    c.stroke();
    c.setLineDash([]);
    if (o.head !== false && p > 0.15) {
      const hs = o.hs ?? lw * 3.2;
      c.translate(end.x, end.y);
      c.rotate(end.a);
      c.beginPath();
      c.moveTo(-hs, -hs * 0.58);
      c.lineTo(1, 0);
      c.lineTo(-hs, hs * 0.58);
      c.closePath();
      c.fillStyle = col;
      c.fill();
    }
    c.restore();
  }

  function icon(g, name, x, y, size = 34, col = PAL.ink) {
    const c = g.c;
    c.save();
    c.translate(x, y);
    c.lineCap = "round";
    c.lineJoin = "round";
    if (name === "check" || name === "cross") {
      BFG_STUDIO.PR[name](g, 1, 0, 0, size / 100, col);
    } else if (name === "star") {
      c.beginPath();
      for (let i = 0; i < 10; i++) {
        const a = -Math.PI / 2 + i * Math.PI / 5;
        const r = i % 2 ? size * 0.2 : size * 0.44;
        c[i ? "lineTo" : "moveTo"](Math.cos(a) * r, Math.sin(a) * r);
      }
      c.closePath();
      inkPath(c, col, 0);
    } else if (name === "dot") {
      c.beginPath();
      c.arc(0, 0, size * 0.26, 0, Math.PI * 2);
      inkPath(c, col, 0);
    }
    c.restore();
  }

  function numRow(g, badgeText, str, x, y, o = {}) {
    const c = g.c;
    const size = o.size || 32;
    const h = o.h || size * 1.75;
    const w = o.w || 700;
    const fill = o.fill || PAL.accent;
    const on = o.on !== false;
    c.save();
    if (o.bg) {
      rr(c, x, y - h / 2, w, h, 14);
      inkPath(c, o.bg, 1.8, PAL.rule);
    }
    const bx = x + (o.bg ? 12 : 0);
    const bsz = h * 0.72;
    rr(c, bx, y - bsz / 2, bsz, bsz, 11);
    inkPath(c, on ? fill : PAL.matte, 0);
    if (o.icon) {
      icon(g, o.icon, bx + bsz / 2, y, bsz * 0.62, on ? "#ffffff" : PAL.ink);
    } else if (!window.__SUPPRESS_TEXT) {
      c.font = fontStr(size * 0.82, 700, FONT_BODY);
      c.fillStyle = on ? "#ffffff" : PAL.ink;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(String(badgeText), bx + bsz / 2, y + 1);
    }
    txt(g, str, bx + bsz + 20, y, {
      size, weight: o.weight ?? 600, col: o.col || PAL.ink,
      maxW: Math.max(80, w - bsz - 46), maxH: h * 0.88, vcenter: true, base: "top",
    });
    c.restore();
  }

  function camKeys(t, keys, d = 0.85) {
    let x = keys[0][1], y = keys[0][2], z = keys[0][3];
    for (let i = 1; i < keys.length; i++) {
      const [tk, kx, ky, kz, kd] = keys[i];
      const u = eio((t - tk) / (kd ?? d));
      if (u <= 0) break;
      x = lerp(x, kx, u);
      y = lerp(y, ky, u);
      z = Math.exp(lerp(Math.log(z), Math.log(kz), u));
    }
    return { x, y, z };
  }

  // ── Artwork Image Loader ───────────────────────────────────────────────────────────────────
  const IMAGES = new Map();
  function loadImages() {
    const entries = Object.entries(SPEC.assets || {});
    return Promise.all(entries.map(([id, url]) => new Promise((resolve, reject) => {
      const img = new Image();
      img.decoding = "sync";
      img.onload = () => { IMAGES.set(id, img); resolve(); };
      img.onerror = () => reject(new Error(`Failed to load approved artwork '${id}'.`));
      img.src = url;
    })));
  }

  function drawArt(g, artId, x, y, w, h, o = {}) {
    const img = IMAGES.get(artId);
    if (!img) throw new Error(`Approved artwork '${artId}' is not loaded.`);
    const c = g.c;
    const pad = o.pad ?? (o.framed ? 24 : 8);
    c.save();
    if (o.framed) {
      card(g, x, y, w, h, { fill: o.fill || PAL.matte, stroke: PAL.rule, r: o.r ?? 24, lw: 2.2, shadow: o.shadow !== false });
    }
    const iw = Math.max(1, w - pad * 2);
    const ih = Math.max(1, h - pad * 2);
    const scale = Math.min(iw / img.naturalWidth, ih / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    const dx = x + (w - dw) / 2;
    const dy = y + (h - dh) / 2;
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    c.drawImage(img, dx, dy, dw, dh);
    if (!o.noAudit) regBox(c, g.s?.id || "scene", "art", dx, dy, dx + dw, dy + dh, artId);
    c.restore();
  }

  // ── Pen handed to Scenes & Procedural BFG Scripts ──────────────────────────────────────────
  class Pen extends BFG_STUDIO.Pen {
    constructor(c, t, s) { super(c, t, s.id); this.s = s; }
    get c() { return this.ctx; }
    at(t0, dur = 0.5, f = eout) { return dur <= 0 ? (this.t >= t0 ? 1 : 0) : f((this.t - t0) / dur); }
    on(t0, dur = 0.6) { return dur <= 0 ? (this.t >= t0 ? 1 : 0) : clamp((this.t - t0) / dur); }
    ease(t0, dur = 0.6, f = eio) { return dur <= 0 ? (this.t >= t0 ? 1 : 0) : f((this.t - t0) / dur); }
    out(t1, dur = 0.35) { return dur <= 0 ? (this.t < t1 ? 1 : 0) : 1 - eio((this.t - t1) / dur); }
    fx(p, fn, o = {}) {
      if (p <= 0) return;
      const c = this.c;
      c.save();
      c.globalAlpha *= clamp(p * (o.fadeK ?? 1.4));
      c.translate((o.dx ?? 0) * (1 - p), (o.dy ?? 24) * (1 - p));
      if (o.from !== undefined && p < 0.999) {
        const k = lerp(o.from, 1, p);
        const cx = o.cx ?? 0, cy = o.cy ?? 0;
        c.translate(cx, cy);
        c.scale(k, k);
        c.translate(-cx, -cy);
      }
      fn(p);
      c.restore();
    }
    in(t0, fn, o = {}) { this.fx(this.at(t0, o.dur ?? 0.5, o.ease || eout), fn, o); }
    pop(t0, x, y, fn, o = {}) { this.fx(this.at(t0, o.dur ?? 0.48, back), fn, { from: 0.62, cx: x, cy: y, dy: 0, ...o }); }
  }

  // ── Layer Rendering (all 10 declarative kinds) ─────────────────────────────────────────────
  function formatNumber(value, unit) {
    const abs = Math.abs(value);
    let formatted;
    if (Number.isInteger(value) && abs < 1e6) {
      formatted = value.toLocaleString("en-US");
    } else if (abs >= 1e9) {
      formatted = (value / 1e9).toFixed(abs % 1e9 === 0 ? 0 : 1) + "B";
    } else if (abs >= 1e6) {
      formatted = (value / 1e6).toFixed(abs % 1e6 === 0 ? 0 : 1) + "M";
    } else {
      formatted = Number(value.toFixed(2)).toLocaleString("en-US");
    }
    if (!unit) return formatted;
    if (unit === "$" || unit === "€" || unit === "£") {
      return value < 0 ? `-${unit}${formatted.replace("-", "")}` : `${unit}${formatted}`;
    }
    if (unit === "%") return `${formatted}%`;
    return `${formatted} ${unit}`;
  }

  function renderBarChart(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const c = g.c;
    const pad = 28;
    card(g, x, y, w, h, { fill: PAL.card, stroke: PAL.rule, r: 22 });
    let top = y + pad;
    if (layer.text) {
      const head = txt(g, layer.text, x + pad, top, {
        size: 32, minSize: 20, weight: 700, col: PAL.ink, maxW: w - pad * 2, maxH: 70,
      });
      top += head.h + 18;
    }
    const items = layer.items || [];
    if (!items.length) return;
    const innerH = Math.max(80, y + h - pad - top);
    const rowH = innerH / items.length;
    const values = items.map(it => Number(it.value) || 0);
    const minV = Math.min(0, ...values);
    const maxV = Math.max(0, ...values);
    const span = (maxV - minV) || 1;
    const labelW = Math.min(w * 0.34, 340);
    const valueW = Math.min(w * 0.22, 220);
    const barX0 = x + pad + labelW + 18;
    const barW = Math.max(100, w - pad * 2 - labelW - valueW - 36);
    const zeroX = barX0 + ((0 - minV) / span) * barW;

    // Zero baseline
    c.save();
    c.strokeStyle = PAL.rule;
    c.lineWidth = 2.5;
    c.beginPath();
    c.moveTo(zeroX, top - 4);
    c.lineTo(zeroX, y + h - pad + 4);
    c.stroke();
    c.restore();

    const barColors = [PAL.accent, PAL.blue, PAL.sage, PAL.mustard, PAL.good, PAL.muted];
    items.forEach((item, idx) => {
      const cy = top + rowH * (idx + 0.5);
      const stagger = clamp((p * (items.length + 1) - idx * 0.65) / 1.8);
      const ease = eio(stagger);
      const val = Number(item.value) || 0;
      txt(g, item.label, x + pad, cy, {
        size: 26, minSize: 16, weight: 600, col: PAL.ink,
        maxW: labelW, maxH: rowH * 0.85, vcenter: true, base: "top",
      });
      const barH = Math.min(40, Math.max(16, rowH * 0.44));
      // Track background
      c.save();
      rr(c, barX0, cy - barH / 2, barW, barH, barH / 2);
      c.fillStyle = PAL.matte;
      c.fill();
      c.restore();

      const targetX = barX0 + ((val - minV) / span) * barW;
      const curX = lerp(zeroX, targetX, ease);
      const bx = Math.min(zeroX, curX);
      const bw = Math.abs(curX - zeroX);
      if (bw > 1) {
        BFG_STUDIO.hbar(g, bx, cy, bw, barH, 1, val < 0 ? PAL.bad : barColors[idx % barColors.length],
          { lin: true, ink: PAL.ink, lw: 0.35, seed: idx + 1 });
      }
      const displayVal = ease >= 0.99 ? val : val * ease;
      txt(g, formatNumber(displayVal, layer.unit), barX0 + barW + 18, cy, {
        size: 25, minSize: 16, weight: 700, col: PAL.ink,
        maxW: valueW, maxH: rowH * 0.8, vcenter: true, base: "top",
      });
    });
  }

  function renderFlow(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 26;
    card(g, x, y, w, h, { fill: PAL.card, stroke: PAL.rule, r: 22 });
    let top = y + pad;
    if (layer.text) {
      const head = txt(g, layer.text, x + pad, top, {
        size: 32, minSize: 20, weight: 700, col: PAL.ink, maxW: w - pad * 2, maxH: 64,
      });
      top += head.h + 18;
    }
    const items = layer.items || [];
    if (!items.length) return;
    const n = items.length;
    const innerW = w - pad * 2;
    const innerH = Math.max(80, y + h - pad - top);
    const horizontal = innerW >= innerH * 1.15;
    const gap = horizontal ? Math.max(44, Math.min(72, innerW * 0.055)) : Math.max(32, Math.min(54, innerH * 0.08));
    const stepW = horizontal ? (innerW - gap * (n - 1)) / n : innerW;
    const stepH = horizontal ? Math.min(innerH, 210) : (innerH - gap * (n - 1)) / n;
    const startY = horizontal ? top + (innerH - stepH) / 2 : top;

    items.forEach((item, idx) => {
      const seg = clamp(p * n - idx * 0.65);
      const pe = eout(seg);
      if (pe <= 0) return;
      const sx = horizontal ? x + pad + idx * (stepW + gap) : x + pad;
      const sy = horizontal ? startY : top + idx * (stepH + gap);
      const isLast = idx === n - 1;
      g.c.save();
      g.c.globalAlpha *= clamp(pe * 1.6);
      g.c.translate(0, (1 - pe) * 18);
      card(g, sx, sy, stepW, stepH, {
        fill: isLast ? PAL.accentPale : PAL.paper,
        stroke: isLast ? PAL.accent : PAL.rule,
        r: 16, lw: isLast ? 3 : 2.2, shadow: false,
      });
      pill(g, String(idx + 1), sx + 22, sy + 24, {
        size: 20, align: "left", fill: PAL.accent, col: "#ffffff", padX: 11, h: 30, noAudit: true,
      });
      txt(g, item.label, sx + stepW / 2, sy + stepH * 0.56, {
        size: 28, minSize: 16, weight: 700, col: PAL.ink, align: "center",
        maxW: stepW - 28, maxH: stepH - 52, vcenter: true, base: "top",
      });
      g.c.restore();
      if (idx < n - 1) {
        const ap = clamp(p * n - (idx + 0.45) * 0.65);
        if (horizontal) {
          const ay = sy + stepH / 2;
          arrow(g, [sx + stepW + 8, ay, sx + stepW + gap - 8, ay], { p: eout(ap), col: PAL.accent, lw: 4 });
        } else {
          const ax = sx + stepW / 2;
          arrow(g, [ax, sy + stepH + 6, ax, sy + stepH + gap - 6], { p: eout(ap), col: PAL.accent, lw: 4 });
        }
      }
    });
  }

  function renderCardLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 28;
    card(g, x, y, w, h, { fill: PAL.card, stroke: PAL.rule, r: 22, band: 12, bandCol: PAL.accent });
    let top = y + pad + 10;
    if (layer.text) {
      const head = txt(g, layer.text, x + pad, top, {
        size: 36, minSize: 20, weight: 700, col: PAL.ink, maxW: w - pad * 2,
        maxH: layer.items?.length ? h * 0.36 : h - pad * 2 - 12,
        p, wordReveal: p < 0.99,
      });
      top += head.h + 18;
    }
    const items = layer.items || [];
    if (items.length) {
      const avail = Math.max(60, y + h - pad - top);
      const rowH = avail / items.length;
      items.forEach((item, idx) => {
        const rp = eout(clamp(p * (items.length + 0.8) - idx * 0.6));
        if (rp <= 0) return;
        g.c.save();
        g.c.globalAlpha *= rp;
        const cy = top + rowH * (idx + 0.5);
        icon(g, "dot", x + pad + 10, cy, 22, PAL.accent);
        txt(g, item.label, x + pad + 32, cy, {
          size: 27, minSize: 16, weight: 500, col: PAL.muted,
          maxW: w - pad * 2 - 36, maxH: rowH * 0.9, vcenter: true, base: "top",
        });
        g.c.restore();
      });
    }
  }

  function renderMeterLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 28;
    const c = g.c;
    card(g, x, y, w, h, { fill: PAL.card, stroke: PAL.rule, r: 22 });
    let top = y + pad;
    if (layer.text) {
      const head = txt(g, layer.text, x + pad, top, {
        size: 32, minSize: 20, weight: 700, col: PAL.ink, maxW: w - pad * 2, maxH: 64,
      });
      top += head.h + 16;
    }
    const items = layer.items || [];
    if (!items.length) return;
    const unit = layer.unit || "";
    const maxScale = unit === "%" ? 100 : Math.max(1, ...items.map(it => Math.abs(Number(it.value) || 0)));
    const avail = Math.max(80, y + h - pad - top);
    const rowH = avail / items.length;
    items.forEach((item, idx) => {
      const target = Number(item.value) || 0;
      const frac = clamp(Math.abs(target) / maxScale);
      const ease = eio(clamp(p * (items.length + 0.5) - idx * 0.4));
      const ry = top + rowH * idx;
      const barH = Math.min(34, Math.max(16, rowH * 0.34));
      const barY = ry + rowH * 0.55;
      txt(g, item.label, x + pad, ry + 6, {
        size: 25, minSize: 16, weight: 600, col: PAL.ink, maxW: w - pad * 2 - 140, maxH: rowH * 0.44,
      });
      txt(g, formatNumber(target * ease, unit), x + w - pad, ry + 6, {
        size: 28, minSize: 18, weight: 800, col: PAL.accent, align: "right", maxW: 150, maxH: rowH * 0.44,
      });
      // Track
      c.save();
      rr(c, x + pad, barY, w - pad * 2, barH, barH / 2);
      inkPath(c, PAL.matte, 1.6, PAL.rule);
      const fw = (w - pad * 2) * frac * ease;
      if (fw > 2) {
        rr(c, x + pad, barY, fw, barH, Math.min(barH / 2, fw / 2));
        c.fillStyle = PAL.accent;
        c.fill();
      }
      c.restore();
    });
  }

  function renderCounterLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 28;
    card(g, x, y, w, h, { fill: PAL.accentPale, stroke: PAL.accent, r: 24, lw: 3 });
    const item = (layer.items && layer.items[0]) || { label: layer.text || "", value: 0 };
    const ease = eout(p);
    const curVal = (Number(item.value) || 0) * ease;
    const numStr = formatNumber(ease >= 0.99 ? Number(item.value) || 0 : curVal, layer.unit);
    const cy = y + h * 0.42;
    txt(g, numStr, x + w / 2, cy, {
      size: Math.min(126, h * 0.46), minSize: 36, weight: 700,
      family: FONT_KICKER, col: PAL.accent, align: "center",
      maxW: w - pad * 2, maxH: h * 0.52, vcenter: true,
    });
    const subtitle = item.label || layer.text;
    if (subtitle) {
      txt(g, subtitle, x + w / 2, y + h * 0.76, {
        size: 32, minSize: 18, weight: 700, col: PAL.ink, align: "center",
        maxW: w - pad * 2, maxH: h * 0.28, vcenter: true,
      });
    }
  }

  function renderChecklistLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 26;
    card(g, x, y, w, h, { fill: PAL.card, stroke: PAL.rule, r: 22 });
    let top = y + pad;
    if (layer.text) {
      const head = txt(g, layer.text, x + pad, top, {
        size: 32, minSize: 20, weight: 700, col: PAL.ink, maxW: w - pad * 2, maxH: 60,
      });
      top += head.h + 16;
    }
    const items = layer.items || [];
    if (!items.length) return;
    const avail = Math.max(80, y + h - pad - top);
    const rowH = avail / items.length;
    items.forEach((item, idx) => {
      const rp = eout(clamp(p * (items.length + 0.6) - idx * 0.55));
      if (rp <= 0) return;
      const val = Number(item.value) || 0;
      const cy = top + rowH * (idx + 0.5);
      g.c.save();
      g.c.globalAlpha *= rp;
      g.c.translate((1 - rp) * -18, 0);
      numRow(g, idx + 1, item.label, x + pad, cy, {
        w: w - pad * 2,
        h: Math.min(rowH * 0.84, 72),
        size: Math.min(29, rowH * 0.42),
        fill: val > 0 ? PAL.good : val < 0 ? PAL.bad : PAL.accent,
        icon: val > 0 ? "check" : val < 0 ? "cross" : null,
        bg: PAL.paper,
      });
      g.c.restore();
    });
  }

  function renderCalloutLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 32;
    const c = g.c;
    card(g, x, y, w, h, { fill: PAL.accentPale, stroke: PAL.accent, r: 22, lw: 2.6 });
    c.save();
    rr(c, x + 16, y + 20, 8, Math.max(16, h - 40), 4);
    c.fillStyle = PAL.accent;
    c.fill();
    c.restore();
    txt(g, layer.text, x + pad + 18, y + h / 2, {
      size: 40, minSize: 20, weight: 700, italic: !IS_HAND,
      family: IS_HAND ? FONT_QUOTE : FONT_DISPLAY,
      col: PAL.ink, maxW: w - pad * 2 - 22, maxH: h - pad * 1.4,
      vcenter: true, p, wordReveal: p < 0.99,
    });
  }

  function renderComparisonLayer(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const pad = 24;
    const items = layer.items || [];
    const leftItems = items.filter(it => (Number(it.value) || 0) < 0);
    const rightItems = items.filter(it => (Number(it.value) || 0) > 0);
    const neutral = items.filter(it => (Number(it.value) || 0) === 0);
    if (!leftItems.length && !rightItems.length && neutral.length) {
      const mid = Math.ceil(neutral.length / 2);
      leftItems.push(...neutral.slice(0, mid));
      rightItems.push(...neutral.slice(mid));
    }
    const headers = String(layer.text || "Left | Right").split("|").map(s => s.trim());
    const leftTitle = headers[0] || "Option A";
    const rightTitle = headers[1] || "Option B";
    const gap = 24;
    const colW = (w - gap) / 2;

    const cols = [
      { cx: x, title: leftTitle, list: leftItems, col: PAL.muted, icon: "cross", fill: PAL.card },
      { cx: x + colW + gap, title: rightTitle, list: rightItems, col: PAL.accent, icon: "check", fill: PAL.accentPale },
    ];
    cols.forEach((side, sideIdx) => {
      const sp = eout(clamp(p * 1.4 - sideIdx * 0.25));
      if (sp <= 0) return;
      g.c.save();
      g.c.globalAlpha *= sp;
      card(g, side.cx, y, colW, h, { fill: side.fill, stroke: side.col, r: 20, lw: 2.5, band: 12, bandCol: side.col });
      const head = txt(g, side.title, side.cx + pad, y + pad + 8, {
        size: 32, minSize: 20, weight: 800, col: side.col, maxW: colW - pad * 2, maxH: 56,
      });
      const listTop = y + pad + head.h + 24;
      const avail = Math.max(60, y + h - pad - listTop);
      const rowH = side.list.length ? avail / side.list.length : avail;
      side.list.forEach((item, idx) => {
        const rp = eout(clamp(sp * (side.list.length + 0.5) - idx * 0.5));
        if (rp <= 0) return;
        g.c.save();
        g.c.globalAlpha *= rp;
        const cy = listTop + rowH * (idx + 0.5);
        icon(g, side.icon, side.cx + pad + 16, cy, 28, side.col);
        txt(g, item.label, side.cx + pad + 44, cy, {
          size: 26, minSize: 16, weight: 600, col: PAL.ink,
          maxW: colW - pad * 2 - 48, maxH: rowH * 0.9, vcenter: true, base: "top",
        });
        g.c.restore();
      });
      g.c.restore();
    });
  }

  // BFG props are authored around different anchors. These bounds include the pen stroke.
  const propBounds = {
    book: [640, 310, 102], clock: [260, 260, 0], hourglass: [245, 425, 0],
    laptop: [400, 220, -100], brain: [264, 210, -15], "coin-stack": [136, 148, -48],
  };
  function drawIllustration(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const subject = layer.text;
    const time = Math.max(0, g.t - layer.start);
    const [width, height, centerY] = layer.kind === "figure" ? [206, 284, -126] : propBounds[subject];
    const scale = Math.min(w / width, h / height) * 0.94;
    const cx = x + w / 2, cy = y + h / 2 - centerY * scale;
    if (layer.kind === "figure") {
      const pose = subject === "point" ? BFG_STUDIO.POSES.point() : BFG_STUDIO.POSES[subject](time * 3);
      BFG_STUDIO.figure(g, { x: cx, y: cy, s: scale, pose, p, shirt: PAL.accent, seed: hstr(layer.id) });
    } else if (subject === "coin-stack") {
      BFG_STUDIO.coinStack(g, cx, cy, 5, p, PAL.mustard, scale);
    } else if (subject === "clock") {
      BFG_STUDIO.PR.clock(g, p, cx, cy, scale, time * 0.12);
    } else {
      BFG_STUDIO.PR[subject](g, p, cx, cy, scale);
    }
    regBox(g.c, g.s.id, "art", cx - width * scale / 2, y + (h - height * scale) / 2,
           cx + width * scale / 2, y + (h + height * scale) / 2, subject);
  }
  function renderLayerBody(g, layer, rect, p) {
    const [x, y, w, h] = rect;
    const kind = layer.kind;
    if (kind === "text") {
      txt(g, layer.text, x + 8, y + h / 2, {
        size: 42, minSize: 18, weight: 600, col: PAL.ink,
        maxW: w - 16, maxH: h - 12, vcenter: true, p, wordReveal: p < 0.99,
      });
    } else if (kind === "figure" || kind === "prop") {
      drawIllustration(g, layer, rect, p);
    } else if (kind === "art") {
      drawArt(g, layer.artId, x, y, w, h, { framed: false, pad: 4 });
    } else if (kind === "bar-chart") {
      renderBarChart(g, layer, rect, p);
    } else if (kind === "flow") {
      renderFlow(g, layer, rect, p);
    } else if (kind === "card") {
      renderCardLayer(g, layer, rect, p);
    } else if (kind === "meter") {
      renderMeterLayer(g, layer, rect, p);
    } else if (kind === "counter") {
      renderCounterLayer(g, layer, rect, p);
    } else if (kind === "checklist") {
      renderChecklistLayer(g, layer, rect, p);
    } else if (kind === "callout") {
      renderCalloutLayer(g, layer, rect, p);
    } else if (kind === "comparison") {
      renderComparisonLayer(g, layer, rect, p);
    } else {
      throw new Error(`Unsupported layer kind '${kind}'.`);
    }
  }

  function renderTimedLayer(g, layer, rect, t, sceneEnd, forcePhase = null) {
    const [x, y, w, h] = rect;
    const enterStart = layer.start;
    const enterEnd = layer.settled;
    const leaveAt = layer.end;
    let p = 1;
    let exitAlpha = 1;
    if (forcePhase !== null) {
      p = clamp(forcePhase);
    } else {
      if (t < enterStart || t >= leaveAt) return;
      const dur = enterEnd - enterStart;
      p = dur > 0 ? clamp((t - enterStart) / dur) : 1;
      // Smooth exit fade when leaving before scene end
      if (leaveAt < sceneEnd - 0.01) {
        const rem = leaveAt - t;
        const exitDur = Math.min(0.28, Math.max(0.08, (leaveAt - enterEnd) * 0.4));
        if (rem < exitDur) exitAlpha = eio(rem / exitDur);
      }
    }
    if (p <= 0 || exitAlpha <= 0) return;

    const c = g.c;
    const effect = layer.enter?.effect || "show";
    c.save();
    c.globalAlpha *= exitAlpha;
    if (effect === "fade") {
      c.globalAlpha *= eout(p);
      renderLayerBody(g, layer, rect, 1);
    } else if (effect === "rise") {
      const e = eout(p);
      c.globalAlpha *= clamp(p * 1.45);
      c.translate(0, (1 - e) * 44);
      renderLayerBody(g, layer, rect, e);
    } else if (effect === "pop") {
      const e = back(p);
      const cx = x + w / 2, cy = y + h / 2;
      c.globalAlpha *= clamp(p * 1.8);
      c.translate(cx, cy);
      c.scale(lerp(0.68, 1, e), lerp(0.68, 1, e));
      c.translate(-cx, -cy);
      renderLayerBody(g, layer, rect, clamp(p * 1.15));
    } else if (effect === "wipe") {
      const e = eio(p);
      c.beginPath();
      c.rect(x - 16, y - 16, (w + 32) * e, h + 32);
      c.clip();
      renderLayerBody(g, layer, rect, 1);
    } else if (effect === "draw") {
      const e = eio(p);
      renderLayerBody(g, layer, rect, e);
    } else {
      renderLayerBody(g, layer, rect, 1);
    }
    c.restore();
  }

  // ── Scene Compilation & Layout ─────────────────────────────────────────────────────────────
  const BFG_API = {
    ...BFG_STUDIO, W, H, PAL, LINES, WORDS, DUR,
    clamp, lerp, ss, eio, eout, ein, back, hash, hstr, noise,
    mixColor, hexAlpha, rr, ink: inkPath, txt, card, pill, numRow, arrow, icon,
    drawArt, camKeys, l0, l1, w, we, wordTime, talking, regBox,
    FONT_DISPLAY, FONT_BODY, FONT_KICKER, FONT_QUOTE,
  };

  const SCENES = SPEC.scenes.map((raw, idx) => {
    const sc = {
      ...raw,
      index: idx,
      id: raw.id || `scene-${idx + 1}`,
      start: Number(raw.start) || 0,
      end: Number(raw.end) || DUR,
      motion: raw.motion || "still",
      layers: raw.layers || [],
      customDraw: BACCHUS_DRAW_HOOKS[raw.id] || null,
    };
    return sc;
  });

  function sceneHeader(g, scene, compact = false, titleP = 1) {
    const c = g.c;
    const left = 96;
    const top = compact ? 54 : 66;
    // Accent bar
    c.save();
    rr(c, left, top, 76, 8, 4);
    c.fillStyle = PAL.accent;
    c.fill();
    c.restore();
    txt(g, scene.title, left, top + 20, {
      size: compact ? 44 : 52, minSize: 26, weight: 800,
      family: FONT_DISPLAY, col: PAL.ink, maxW: W - left * 2, maxH: compact ? 64 : 84,
      p: titleP, wordReveal: titleP < 0.99,
    });
    const ruleY = compact ? 142 : 164;
    c.save();
    g.wl([left, ruleY, W - left, ruleY], 5, 1, { col: PAL.rule, lw: 0.37, amp: 0.6 });
    c.restore();
    return ruleY;
  }

  function sceneFooter(g, scene) {
    if (!scene.caption) return H - 56;
    const left = 96;
    const ruleY = H - 122;
    const c = g.c;
    c.save();
    c.strokeStyle = PAL.rule;
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(left, ruleY);
    c.lineTo(W - left, ruleY);
    c.stroke();
    c.restore();
    txt(g, scene.caption, left, ruleY + 18, {
      size: 29, minSize: 18, weight: 500, col: PAL.muted, maxW: W - left * 2, maxH: 82,
    });
    return ruleY - 18;
  }

  function drawSceneContent(g, scene, t, staticOpts = null) {
    if (scene.customDraw) {
      scene.customDraw(g, t, scene, BFG_API);
      return;
    }
    const elapsed = Math.max(0, t - scene.start);
    const introP = staticOpts ? 1 : eout(clamp(elapsed / 0.55));
    const layout = scene.layout || "editorial";

    if (layout === "layered") {
      const top = sceneHeader(g, scene, true, 1) + 22;
      const bottom = sceneFooter(g, scene);
      const areaX = 96;
      const areaW = W - 192;
      const areaH = Math.max(200, bottom - top);
      for (const layer of scene.layers) {
        if (staticOpts && staticOpts.isolateLayerId && layer.id === staticOpts.isolateLayerId) {
          if (staticOpts.layerPhase !== null && staticOpts.layerPhase !== undefined) {
            const rect = [
              areaX + (layer.box.x / 100) * areaW,
              top + (layer.box.y / 100) * areaH,
              (layer.box.w / 100) * areaW,
              (layer.box.h / 100) * areaH,
            ];
            renderTimedLayer(g, layer, rect, t, scene.end, staticOpts.layerPhase);
            continue;
          }
        }
        const rect = [
          areaX + (layer.box.x / 100) * areaW,
          top + (layer.box.y / 100) * areaH,
          (layer.box.w / 100) * areaW,
          (layer.box.h / 100) * areaH,
        ];
        if (staticOpts && staticOpts.onlyAtSettled) {
          renderTimedLayer(g, layer, rect, staticOpts.settledTime, scene.end, null);
        } else {
          renderTimedLayer(g, layer, rect, t, scene.end, null);
        }
      }
      return;
    }

    if (layout === "title") {
      // Bold centered magazine title card with accent bar & animated underline
      const c = g.c;
      const barW = 110 * introP;
      c.save();
      rr(c, W / 2 - barW / 2, 260, Math.max(8, barW), 10, 5);
      c.fillStyle = PAL.accent;
      c.fill();
      c.restore();
      const head = txt(g, scene.title, W / 2, 330, {
        size: 94, minSize: 44, weight: 800, family: FONT_DISPLAY,
        col: PAL.ink, align: "center", maxW: 1460, maxH: 340,
        p: introP, wordReveal: !staticOpts && introP < 0.99,
      });
      if (scene.caption) {
        const capP = staticOpts ? 1 : eout(clamp((elapsed - 0.18) / 0.5));
        c.save();
        c.globalAlpha *= capP;
        c.translate(0, (1 - capP) * 18);
        txt(g, scene.caption, W / 2, 330 + head.h + 44, {
          size: 40, minSize: 22, weight: 500, col: PAL.muted, align: "center",
          maxW: 1320, maxH: 220,
        });
        c.restore();
      }
      if (scene.artId) {
        drawArt(g, scene.artId, W / 2 - 220, 740, 440, 250, { framed: true, pad: 18 });
      }
      return;
    }

    if (layout === "illustration" && scene.artId) {
      const artP = staticOpts ? 1 : eout(clamp(elapsed / 0.55));
      g.c.save();
      g.c.globalAlpha *= clamp(artP * 1.4);
      g.c.translate((1 - artP) * 28, 0);
      drawArt(g, scene.artId, 96, 110, 1020, 860, { framed: true, pad: 28, r: 28 });
      g.c.restore();

      const textX = 1176;
      const textW = 648;
      g.c.save();
      rr(g.c, textX, 210, 72, 8, 4);
      g.c.fillStyle = PAL.accent;
      g.c.fill();
      g.c.restore();
      const head = txt(g, scene.title, textX, 244, {
        size: 62, minSize: 30, weight: 800, family: FONT_DISPLAY,
        col: PAL.ink, maxW: textW, maxH: 320, p: introP, wordReveal: !staticOpts && introP < 0.99,
      });
      if (scene.caption) {
        txt(g, scene.caption, textX, 244 + head.h + 32, {
          size: 34, minSize: 20, weight: 500, col: PAL.muted, maxW: textW, maxH: 320,
        });
      }
      return;
    }

    // Editorial layout (with or without artwork)
    if (scene.artId) {
      const textX = 108;
      const textW = 860;
      g.c.save();
      rr(g.c, textX, 220, 76, 8, 4);
      g.c.fillStyle = PAL.accent;
      g.c.fill();
      g.c.restore();
      const head = txt(g, scene.title, textX, 256, {
        size: 68, minSize: 32, weight: 800, family: FONT_DISPLAY,
        col: PAL.ink, maxW: textW, maxH: 340, p: introP, wordReveal: !staticOpts && introP < 0.99,
      });
      if (scene.caption) {
        txt(g, scene.caption, textX, 256 + head.h + 34, {
          size: 36, minSize: 20, weight: 500, col: PAL.muted, maxW: textW, maxH: 300,
        });
      }
      drawArt(g, scene.artId, 1024, 156, 788, 768, { framed: true, pad: 28, r: 28 });
    } else {
      const left = 160;
      const maxW = W - left * 2;
      g.c.save();
      rr(g.c, left, 270, 90, 9, 4.5);
      g.c.fillStyle = PAL.accent;
      g.c.fill();
      g.c.restore();
      const head = txt(g, scene.title, left, 312, {
        size: 80, minSize: 36, weight: 800, family: FONT_DISPLAY,
        col: PAL.ink, maxW, maxH: 300, p: introP, wordReveal: !staticOpts && introP < 0.99,
      });
      if (scene.caption) {
        txt(g, scene.caption, left, 312 + head.h + 38, {
          size: 40, minSize: 22, weight: 500, col: PAL.muted, maxW, maxH: 260,
        });
      }
    }
  }

  // Compile the structured plan to BFG scene definitions. Both contact sheets and the
  // encoder call its actual page-camera renderer. A scene is drawn in page coordinates.
  function sceneAt(t) {
    let i = 0;
    while (i + 1 < SCENES.length && SCENES[i + 1].start <= t) i++;
    return i;
  }

  function drawSceneLayer(c, scene, t, staticOpts = null) {
    c.save();
    try {
      c.beginPath();
      c.rect(0, 0, W, H);
      c.clip();
      drawBackdrop(c);
      c.lineCap = "round";
      c.lineJoin = "round";
      const time = Math.max(scene.start, Math.min(t, scene.end - 0.000001));
      drawSceneContent(new Pen(c, time, scene), scene, time, staticOpts);
    } finally {
      c.restore();
    }
  }

  let sceneError;
  BFG_STUDIO.PAGE.paper = PAL.paper;
  for (const s of SCENES) {
    const i = s.index;
    const pan = s.motion === "glide" || s.motion === "slide";
    const duration = Math.min(0.65, (s.end - s.start) * 0.45);
    BFG_STUDIO.scene({
      id: s.id,
      // The BFG camera starts a move LEAD seconds before its nominal anchor.
      at: i ? s.start + BFG_STUDIO.LEAD : 0,
      pos: [i * W, 0],
      cut: !pan,
      glide: duration,
      push: s.motion === "push-in" ? 0.042 : 0,
      draw(pen, t) {
        const elapsed = t - s.start;
        if (t < s.start) return;
        const next = SCENES[i + 1];
        if (next && t >= next.start && next.motion !== "glide" && next.motion !== "slide") return;
        const oldTransition = AUDIT.inTransition;
        AUDIT.inTransition = oldTransition || (pan && elapsed < duration) || t >= s.end;
        try {
          if (s.motion === "fade" && elapsed < duration) {
            AUDIT.inTransition = true;
            if (i) drawSceneLayer(pen.ctx, SCENES[i - 1], SCENES[i - 1].end);
            else drawBackdrop(pen.ctx);
            const opacity = ss(elapsed / duration);
            fadeWhole(pen, opacity, () => drawSceneLayer(pen.ctx, s, t));
          } else {
            drawSceneLayer(pen.ctx, s, t);
          }
        } catch (error) {
          // BFG logs scene exceptions for the interactive player. Fail proofs and export too.
          sceneError = error;
          throw error;
        } finally {
          AUDIT.inTransition = oldTransition;
        }
      },
    });
  }

  function renderFrame(c, t, scale = 1) {
    BFG_STUDIO.renderFrame(c, clamp(Number(t) || 0, 0, DUR), scale);
    if (sceneError) throw sceneError;
  }

  // ── Render Mode / Preview Player Bootstrap ─────────────────────────────────────────────────
  const RENDER = location.hash.includes("render");
  const S = window.__FILM_SCALE || 1;
  const cv = document.createElement("canvas");
  cv.id = "film";
  cv.width = Math.round(W * S);
  cv.height = Math.round(H * S);
  const ctx = cv.getContext("2d", { alpha: false, willReadFrequently: RENDER });

  let READY = false;
  const fontsPromise = typeof loadFonts === "function"
    ? loadFonts()
    : (document.fonts ? document.fonts.ready : Promise.resolve());
  const readyPromise = Promise.all([fontsPromise, loadImages()]).then(() => {
    READY = true;
    return true;
  }).catch(err => {
    window.__film_error = String(err && err.stack || err);
    throw err;
  });

  function requireReady() {
    if (!READY) throw new Error("Await window.__film.ready before rendering.");
  }

  const api = {
    engine: "bacchus-canvas",
    preset: PRESET,
    DUR,
    RW: cv.width,
    RH: cv.height,
    ready: readyPromise,
    scenes() {
      return SCENES.map(s => ({ id: s.id, title: s.title || s.id, t0: +s.start.toFixed(4), t1: +s.end.toFixed(4) }));
    },
    render(t) {
      requireReady();
      renderFrame(ctx, t, S);
      return true;
    },
    renderSceneStatic(sceneIndex, layerId = "", layerPhase = null) {
      requireReady();
      const scene = SCENES[sceneIndex];
      if (!scene) throw new Error("Scene index out of range: " + sceneIndex);
      let settledTime = scene.start;
      if (layerId) {
        const target = scene.layers.find(l => l.id === layerId);
        if (target) settledTime = target.settled;
      }
      ctx.setTransform(S, 0, 0, S, 0, 0);
      ctx.globalAlpha = 1;
      drawSceneLayer(ctx, scene, settledTime, {
        onlyAtSettled: true,
        settledTime,
        isolateLayerId: layerId,
        layerPhase,
      });
      return true;
    },
    frameRGBA() {
      requireReady();
      return ctx.getImageData(0, 0, cv.width, cv.height).data.buffer;
    },
    frameB64() {
      requireReady();
      return cv.toDataURL("image/png").split(",")[1];
    },
    framePNG() {
      requireReady();
      return new Promise((resolve, reject) => cv.toBlob(blob => {
        if (!blob) reject(new Error("PNG encode failed."));
        else blob.arrayBuffer().then(resolve, reject);
      }, "image/png"));
    },
    audit(t) {
      requireReady();
      AUDIT.on = true;
      AUDIT.S = S;
      AUDIT.items = [];
      try {
        renderFrame(ctx, t, S);
      } finally {
        AUDIT.on = false;
      }
      return AUDIT.items;
    },
    auditOcclusion(t, lumThreshold = 150) {
      requireReady();
      const items = api.audit(t);
      const textItems = items.filter(it => it.kind === "text" && it.a >= 0.6);
      if (!textItems.length) return [];
      window.__SUPPRESS_TEXT = true;
      try {
        renderFrame(ctx, t, S);
      } finally {
        window.__SUPPRESS_TEXT = false;
      }
      const paperLum = luminance(PAL.paper);
      const isDarkBg = paperLum < 128;
      const results = [];
      for (const item of textItems) {
        const [bx0, by0, bx1, by1] = item.box;
        const x0 = Math.max(0, Math.floor(bx0 * S + 2));
        const y0 = Math.max(0, Math.floor(by0 * S + 2));
        const x1 = Math.min(cv.width, Math.ceil(bx1 * S - 2));
        const y1 = Math.min(cv.height, Math.ceil(by1 * S - 2));
        if (x1 <= x0 || y1 <= y0) continue;
        const d = ctx.getImageData(x0, y0, x1 - x0, y1 - y0).data;
        let bad = 0;
        const total = (x1 - x0) * (y1 - y0);
        for (let i = 0; i < d.length; i += 4) {
          const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
          if (isDarkBg ? lum > (255 - lumThreshold) : lum < lumThreshold) bad++;
        }
        const frac = bad / total;
        if (frac > 0.08) {
          results.push({ scene: item.id, str: item.str, t: +t.toFixed(2), fraction: +frac.toFixed(3), box: item.box });
        }
      }
      return results;
    },
    sweepAudit(step = 0.25, lumThreshold = 135) {
      requireReady();
      const snapshots = [];
      const occlusion = [];
      const blankFrames = [];
      const times = [];
      for (let t = 0; t < DUR; t += step) times.push(+t.toFixed(4));
      // Ensure every scene's settled moment is included
      for (const s of SCENES) {
        const mid = +(s.start + Math.min(0.8, (s.end - s.start) * 0.5)).toFixed(4);
        if (mid >= 0 && mid < DUR) times.push(mid);
      }
      const uniqueTimes = [...new Set(times)].sort((a, b) => a - b);
      let blankStart = null;
      const [paperR, paperG, paperB] = parseHex(PAL.paper);
      for (const t of uniqueTimes) {
        const items = api.audit(t);
        snapshots.push({ t, items });
        // BFG sweep reports less than 0.2% ink. Compare to the style's paper so this
        // works with a dark palette and excludes the permanent border and paper grain.
        const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
        let ink = 0, sampled = 0;
        for (let i = 0; i < d.length; i += 64) {
          if (Math.abs(d[i] - paperR) + Math.abs(d[i+1] - paperG) + Math.abs(d[i+2] - paperB) > 90) ink++;
          sampled++;
        }
        if (ink / sampled < 0.002) {
          if (blankStart === null) blankStart = t;
          if (t - blankStart >= 0.5) blankFrames.push({ t, scene: SCENES[sceneAt(t)].id });
        } else {
          blankStart = null;
        }
      }
      // Run text-over-artwork occlusion check at each scene's settled midpoint
      for (const s of SCENES) {
        const checkTimes = [
          clamp(s.start + Math.min(0.9, (s.end - s.start) * 0.55), 0, DUR),
          clamp(s.end - 0.1, 0, DUR),
        ];
        for (const ct of checkTimes) {
          occlusion.push(...api.auditOcclusion(ct, lumThreshold));
        }
      }
      return { snapshots, occlusion, blankFrames, scenes: api.scenes(), dur: DUR };
    },
    jumpsSweep(fps = 30, threshold = 90) {
      requireReady();
      const sw = 480, sh = 270;
      const scv = document.createElement("canvas");
      scv.width = sw; scv.height = sh;
      const sctx = scv.getContext("2d", { alpha: false, willReadFrequently: true });
      const n = Math.max(1, Math.floor(DUR * fps));
      const counts = [];
      const boxes = [];
      let prev = null;
      for (let f = 0; f <= n; f++) {
        renderFrame(sctx, f / fps, sw / W);
        const d = sctx.getImageData(0, 0, sw, sh).data;
        let count = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
        if (prev) {
          for (let i = 0, px = 0; i < d.length; i += 4, px++) {
            if (Math.abs(d[i] - prev[i]) + Math.abs(d[i + 1] - prev[i + 1]) + Math.abs(d[i + 2] - prev[i + 2]) <= threshold) continue;
            count++;
            const x = px % sw, y = (px / sw) | 0;
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
          }
        }
        counts.push(count);
        boxes.push(count ? [x0, y0, x1, y1] : null);
        prev = new Uint8ClampedArray(d);
      }
      const median = arr => { const s = [...arr].sort((a, b) => a - b); return s[s.length >> 1] || 0; };
      const k = W / sw;
      const jumps = [];
      for (let f = 6; f < counts.length - 6; f++) {
        const t = f / fps;
        const base = median([...counts.slice(f - 5, f), ...counts.slice(f + 1, f + 6)]);
        if (counts[f] > 3 * base + 0.0016 * sw * sh && boxes[f]) {
          jumps.push({
            f,
            t: +t.toFixed(3),
            scene: SCENES[sceneAt(t)]?.id || "scene",
            changed: counts[f],
            baseline: base,
            box: boxes[f].map(v => Math.round(v * k)),
          });
        }
      }
      jumps.sort((a, b) => (b.changed - b.baseline) - (a.changed - a.baseline));
      return { frames: counts.length, fps, jumps: jumps.slice(0, 40).sort((a, b) => a.t - b.t) };
    },
    sheet(samples, title = "", cols = 2, tw = 768) {
      requireReady();
      const th = Math.round(tw * H / W);
      const margin = 20, header = 64, caption = 39;
      const rows = Math.ceil(samples.length / cols);
      const big = document.createElement("canvas");
      big.width = cols * tw + (cols + 1) * margin;
      big.height = header + rows * (th + caption + margin);
      const b = big.getContext("2d", { alpha: false });
      b.fillStyle = "#f5f4ef";
      b.fillRect(0, 0, big.width, big.height);
      b.fillStyle = "#222b26";
      b.font = fontStr(26, 700, FONT_BODY);
      b.textBaseline = "top";
      b.fillText(title, margin, 18, big.width - margin * 2);

      const tileCanvas = document.createElement("canvas");
      tileCanvas.width = tw;
      tileCanvas.height = th;
      const tctx = tileCanvas.getContext("2d", { alpha: false });
      const tileScale = tw / W;
      const fmtTc = sec => {
        const ms = Math.round(sec * 1000);
        const mins = Math.floor(ms / 60000);
        return `${String(mins).padStart(2, "0")}:${((ms % 60000) / 1000).toFixed(3).padStart(6, "0")}`;
      };
      samples.forEach((sample, idx) => {
        const row = Math.floor(idx / cols);
        const col = idx % cols;
        const x = margin + col * (tw + margin);
        const y = header + row * (th + caption + margin);
        renderFrame(tctx, sample.time, tileScale);
        b.drawImage(tileCanvas, x, y, tw, th);
        b.strokeStyle = "#d5d3cc";
        b.lineWidth = 1;
        b.strokeRect(x - 0.5, y - 0.5, tw + 1, th + 1);
        b.fillStyle = "#44514a";
        b.font = fontStr(18, 500, FONT_BODY);
        b.fillText(`${fmtTc(sample.time)}  ${sample.label || ""}`, x, y + th + 9, tw);
      });
      return big.toDataURL("image/png").split(",")[1];
    },
  };

  window.__film = api;

  if (RENDER) {
    document.body.style.cssText = "margin:0;background:#000;overflow:hidden";
    document.body.appendChild(cv);
    return;
  }

  // ── Interactive 60fps Canvas Preview Player ────────────────────────────────────────────────
  document.title = SPEC.title || "Bacchus Preview";
  document.body.style.cssText = "margin:0;background:#16181c;color:#ecebe7;font:15px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif";
  const wrap = document.createElement("div");
  wrap.style.cssText = "max-width:1280px;margin:18px auto;padding:0 16px;display:grid;gap:12px";
  const topBar = document.createElement("div");
  topBar.style.cssText = "display:flex;justify-content:space-between;align-items:baseline;gap:12px";
  const h1 = document.createElement("h1");
  h1.textContent = SPEC.title || "Bacchus Canvas Preview";
  h1.style.cssText = "margin:0;font-size:20px;font-weight:700";
  const meta = document.createElement("span");
  meta.textContent = `${SCENES.length} scenes · ${DUR.toFixed(1)} s · HTML5 Canvas 60fps`;
  meta.style.cssText = "font-size:13px;color:#9aa1a9";
  topBar.append(h1, meta);

  cv.style.cssText = "width:100%;height:auto;border-radius:8px;display:block;border:1px solid #2d3138;cursor:pointer";
  const controls = document.createElement("div");
  controls.style.cssText = "display:grid;grid-template-columns:auto auto 1fr;gap:12px;align-items:center";
  const playBtn = document.createElement("button");
  playBtn.textContent = "Play";
  playBtn.style.cssText = "padding:8px 18px;border-radius:999px;border:0;background:#b86b49;color:#fff;font:600 14px inherit;cursor:pointer";
  const timeEl = document.createElement("span");
  timeEl.style.cssText = "font-variant-numeric:tabular-nums;min-width:104px;color:#c9cdd2";
  const scrub = document.createElement("input");
  scrub.type = "range"; scrub.min = "0"; scrub.max = String(DUR); scrub.step = "0.01"; scrub.value = "0";
  scrub.style.cssText = "width:100%;accent-color:#b86b49";
  const chips = document.createElement("div");
  chips.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
  const audio = SPEC.audioUrl ? new Audio(SPEC.audioUrl) : null;
  if (audio) audio.preload = "auto";
  controls.append(playBtn, timeEl, scrub);
  wrap.append(topBar, cv, controls, chips);
  document.body.appendChild(wrap);

  const fmt = sec => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
  let curT = 0, playing = false, playStartT = 0, wallStart = 0;
  function setPlay(on) {
    playing = on;
    playBtn.textContent = on ? "Pause" : "Play";
    if (on) {
      if (curT >= DUR - 0.05) curT = 0;
      playStartT = curT;
      wallStart = performance.now();
      if (audio) { audio.currentTime = curT; audio.play().catch(() => {}); }
    } else if (audio) {
      audio.pause();
    }
  }
  function seek(t) {
    curT = clamp(t, 0, DUR);
    playStartT = curT;
    wallStart = performance.now();
    if (audio) audio.currentTime = curT;
    renderFrame(ctx, curT, S);
    scrub.value = String(curT);
    timeEl.textContent = `${fmt(curT)} / ${fmt(DUR)}`;
  }
  playBtn.onclick = () => setPlay(!playing);
  cv.onclick = () => setPlay(!playing);
  scrub.oninput = () => seek(Number(scrub.value));
  document.addEventListener("keydown", e => {
    if (e.code === "Space") { e.preventDefault(); setPlay(!playing); }
    else if (e.code === "ArrowRight") seek(curT + 5);
    else if (e.code === "ArrowLeft") seek(curT - 5);
  });
  readyPromise.then(() => {
    SCENES.forEach((s, idx) => {
      const b = document.createElement("button");
      b.textContent = `${idx + 1}. ${s.title || s.id}`;
      b.style.cssText = "border:1px solid #3c424b;background:#23272e;color:#ecebe7;border-radius:999px;padding:4px 11px;cursor:pointer;font:12px inherit";
      b.onclick = () => seek(s.start + 0.02);
      chips.appendChild(b);
    });
    const tick = () => {
      if (playing) {
        curT = (audio && audio.readyState > 2 && !audio.paused)
          ? audio.currentTime
          : playStartT + (performance.now() - wallStart) / 1000;
        if (curT >= DUR) { curT = DUR; setPlay(false); }
        renderFrame(ctx, curT, S);
        scrub.value = String(curT);
        timeEl.textContent = `${fmt(curT)} / ${fmt(DUR)}`;
      }
      requestAnimationFrame(tick);
    };
    seek(0);
    requestAnimationFrame(tick);
  });
})();
