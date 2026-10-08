// Hero terrain for /field.html: the v0.3 look (diamond-cell ground, river, trees, settlements) painted once on a canvas
// over REAL geography (public/field/hero-geo.json, built by scripts/build-field-hero.mjs). Coastlines and borders come
// from the data and are drawn as vectors on top of the cells, so the cells never move a border.
// Trees and villages are decoration: deterministic, not data.

const PAL = {
  sea: ['#2d4547', '#30494b', '#345053'],
  ua: ['#485739', '#3f4d31', '#38452c'],
  uaSouth: ['#56543a', '#4b4d33', '#514f36'],
  eu: ['#3a4530', '#343e2b', '#2f3827'],
  other: ['#30342a', '#2c3026', '#292d24'],
};
const MASK = { ua: [200, 0, 0], eu: [0, 200, 0], other: [0, 0, 200] };

function rng(seed) { let s = seed >>> 0; return () => { s = (s + 0x6d2b79f5) >>> 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const hash2 = (i, j) => { let h = Math.imul(i, 374761393) + Math.imul(j, 668265263); h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
// smooth patches so the field reads as farmland and woodland, not as noise
function patch(x, y, sc) {
  const xi = Math.floor(x / sc), yi = Math.floor(y / sc), fx = x / sc - xi, fy = y / sc - yi, s = (t) => t * t * (3 - 2 * t);
  const a = hash2(xi, yi), b = hash2(xi + 1, yi), c = hash2(xi, yi + 1), d = hash2(xi + 1, yi + 1);
  return a + (b - a) * s(fx) + (c - a) * s(fy) + (a - b - c + d) * s(fx) * s(fy);
}

function pathOf(rings, ox, oy) {
  const p = new Path2D();
  for (const r of rings) { r.forEach(([x, y], i) => (i ? p.lineTo(x - ox, y - oy) : p.moveTo(x - ox, y - oy))); p.closePath(); }
  return p;
}

// geo: hero-geo.json; res: canvas px per world px; southY: world y of the farmland belt
export function paintHero(geo, res, { southY }) {
  const { x0, y0, x1, y1 } = geo.box, W = x1 - x0, H = y1 - y0;
  const cv = document.createElement('canvas');
  cv.width = Math.round(W * res); cv.height = Math.round(H * res);
  const ctx = cv.getContext('2d');

  // ---- class mask at 2 px per world px: which country class is under a point ----
  const mk = document.createElement('canvas'), MR = 2;
  mk.width = Math.ceil(W * MR); mk.height = Math.ceil(H * MR);
  const mc = mk.getContext('2d', { willReadFrequently: true });
  mc.setTransform(MR, 0, 0, MR, 0, 0);
  const paths = geo.land.map((c) => ({ ...c, path: pathOf(c.rings, x0, y0) }));
  for (const c of paths) { const [r, g, b] = MASK[c.cls]; mc.fillStyle = `rgb(${r},${g},${b})`; mc.fill(c.path, 'evenodd'); }
  const md = mc.getImageData(0, 0, mk.width, mk.height).data;
  const classAt = (wx, wy) => {
    const i = (Math.min(mk.height - 1, Math.max(0, Math.round(wy * MR))) * mk.width + Math.min(mk.width - 1, Math.max(0, Math.round(wx * MR)))) * 4;
    return md[i] > 100 ? 'ua' : md[i + 1] > 100 ? 'eu' : md[i + 2] > 100 ? 'other' : 'sea';
  };
  const land = new Path2D();
  for (const c of paths) land.addPath(c.path);

  ctx.setTransform(res, 0, 0, res, 0, 0);
  const T = 3.6, TH = T / 2; // one ground cell in world px (2:1 diamond)
  function cells(pick) {
    for (let j = -1, row = 0; j * TH < H + TH; j++, row++) {
      const cy = j * TH, off = row % 2 ? T / 2 : 0;
      for (let cx = off - T; cx < W + T; cx += T) {
        const fill = pick(cx, cy, Math.round(cx / T * 2), j);
        if (!fill) continue;
        ctx.beginPath(); ctx.moveTo(cx, cy - TH / 2 - 0.02); ctx.lineTo(cx + T / 2 + 0.02, cy); ctx.lineTo(cx, cy + TH / 2 + 0.02); ctx.lineTo(cx - T / 2 - 0.02, cy); ctx.closePath();
        ctx.fillStyle = fill; ctx.fill();
      }
    }
  }
  // sea everywhere first
  ctx.fillStyle = PAL.sea[0]; ctx.fillRect(0, 0, W, H);
  cells((cx, cy, i, j) => PAL.sea[(hash2(i, j) < 0.18 ? 2 : patch(cx, cy, 30) > 0.5 ? 1 : 0)]);
  // land cells, clipped to the exact coastline
  ctx.save(); ctx.clip(land, 'nonzero');
  ctx.fillStyle = PAL.other[1]; ctx.fillRect(0, 0, W, H);
  cells((cx, cy, i, j) => {
    let cls = classAt(cx, cy); if (cls === 'sea') cls = 'other';
    const pal = cls === 'ua' && cy + y0 > southY ? PAL.uaSouth : PAL[cls];
    const v = patch(cx, cy, cls === 'ua' ? 9 : 14) * 0.75 + hash2(i, j) * 0.25; // fields with a bit of grain
    return pal[v < 0.36 ? 0 : v < 0.68 ? 1 : 2];
  });
  // faint cell grid, the RTS ground grammar
  ctx.strokeStyle = 'rgba(139,145,106,0.10)'; ctx.lineWidth = 0.12;
  for (let j = -1; j * TH < H + TH; j++) { ctx.beginPath(); for (let cx = -T; cx < W + T; cx += T) { const cy = j * TH; ctx.moveTo(cx - T / 2, cy); ctx.lineTo(cx, cy - TH / 2); ctx.lineTo(cx + T / 2, cy); } ctx.stroke(); }
  ctx.restore();

  // ---- coast: a light surf line on the sea side ----
  ctx.save(); ctx.strokeStyle = 'rgba(160,185,170,0.35)'; ctx.lineWidth = 1.1;
  for (const c of paths) ctx.stroke(c.path);
  ctx.restore();

  // ---- rivers ----
  for (const r of geo.rivers) {
    const wide = /Dnipro|Dnepre|Danube/.test(r.name);
    ctx.beginPath(); r.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x - x0, y - y0) : ctx.moveTo(x - x0, y - y0)));
    ctx.lineJoin = ctx.lineCap = 'round';
    ctx.strokeStyle = '#3f6466'; ctx.lineWidth = wide ? 1.5 : 0.8; ctx.stroke();
    ctx.strokeStyle = 'rgba(122,152,150,0.7)'; ctx.lineWidth = wide ? 0.55 : 0.3; ctx.stroke();
  }

  // ---- decoration: forests and villages (deterministic) ----
  const R = rng(20261008), deco = [];
  const forestBias = (wy) => (wy + y0 < southY - 40 ? 0.85 : wy + y0 < southY ? 0.55 : 0.18); // more wood in the north
  for (let k = 0; k < 2600; k++) {
    const wx = R() * W, wy = R() * H, cls = classAt(wx, wy);
    if (cls === 'sea') continue;
    const dens = cls === 'ua' ? 1 : cls === 'eu' ? 0.7 : 0.45;
    if (R() > dens) continue;
    if (R() < forestBias(wy) * 0.6) deco.push({ t: 'forest', x: wx, y: wy, n: 3 + Math.floor(R() * 4), v: R() });
    else if (R() < 0.35) deco.push({ t: 'village', x: wx, y: wy, n: 1 + Math.floor(R() * 3), v: R() });
  }
  for (const c of geo.cities) {
    const n = Math.min(12, 4 + Math.round(Math.log10(c.pop / 1e5) * 5));
    deco.push({ t: 'city', x: c.x - x0, y: c.y - y0, n, v: 0.5 });
  }
  deco.sort((a, b) => a.y - b.y); // back to front
  for (const d of deco) {
    if (d.t === 'forest') for (let i = 0; i < d.n; i++) tree(ctx, d.x + ((i % 3) - 1) * 1.3 + d.v, d.y + Math.floor(i / 3) * 0.9 - 0.4, (i + Math.round(d.v * 9)) % 2);
    else for (let i = 0; i < d.n; i++) { const a = i * 2.4 + d.v * 6, r = d.t === 'city' ? Math.sqrt(i) * 1.6 : i * 1.4; house(ctx, d.x + Math.cos(a) * r, d.y + Math.sin(a) * r * 0.5, d.t === 'city' ? 1.15 : 0.9, (i + Math.round(d.v * 5)) % 3); }
  }

  // ---- borders on top: Ukraine strong, the rest faint ----
  ctx.lineJoin = 'round';
  for (const c of paths) if (c.cls !== 'ua') { ctx.strokeStyle = c.cls === 'eu' ? 'rgba(190,190,150,0.45)' : 'rgba(150,150,120,0.35)'; ctx.lineWidth = 0.35; ctx.setLineDash([1.2, 0.8]); ctx.stroke(c.path); }
  ctx.setLineDash([]);
  const ua = paths.find((c) => c.cls === 'ua');
  ctx.strokeStyle = 'rgba(20,24,15,0.55)'; ctx.lineWidth = 1.6; ctx.stroke(ua.path);
  ctx.strokeStyle = '#d6cf98'; ctx.lineWidth = 0.6; ctx.stroke(ua.path);

  // ---- soft edge so the hero blends into the low-detail world map ----
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  const fade = 26 * res;
  const gx = ctx.createLinearGradient(0, 0, cv.width, 0);
  gx.addColorStop(0, 'rgba(0,0,0,0)'); gx.addColorStop(fade / cv.width, '#000'); gx.addColorStop(1 - fade / cv.width, '#000'); gx.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gx; ctx.fillRect(0, 0, cv.width, cv.height);
  const gy = ctx.createLinearGradient(0, 0, 0, cv.height);
  gy.addColorStop(0, 'rgba(0,0,0,0)'); gy.addColorStop(fade / cv.height, '#000'); gy.addColorStop(1 - fade / cv.height, '#000'); gy.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gy; ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.globalCompositeOperation = 'source-over';
  return { canvas: cv, classAt: (wx, wy) => classAt(wx - x0, wy - y0) };
}

function tree(ctx, x, y, v) {
  ctx.fillStyle = 'rgba(12,16,10,0.35)'; ctx.beginPath(); ctx.ellipse(x + 0.5, y + 0.15, 1.1, 0.4, 0, 0, 7); ctx.fill();
  ctx.fillStyle = '#29261c'; ctx.fillRect(x - 0.12, y - 0.4, 0.24, 0.6);
  ctx.fillStyle = v ? '#20341f' : '#263923'; ctx.beginPath(); ctx.moveTo(x, y - 2.3); ctx.lineTo(x + 0.95, y - 0.25); ctx.lineTo(x - 0.95, y - 0.25); ctx.closePath(); ctx.fill();
  ctx.fillStyle = v ? '#2c4529' : '#31492c'; ctx.beginPath(); ctx.moveTo(x, y - 2.3); ctx.lineTo(x - 0.95, y - 0.25); ctx.lineTo(x - 0.1, y - 0.25); ctx.closePath(); ctx.fill();
}
function house(ctx, x, y, s, v) {
  const w = 0.9 * s, h = 0.7 * s;
  ctx.fillStyle = 'rgba(12,16,10,0.35)'; ctx.fillRect(x - w + 0.3, y - 0.1, w * 2, 0.4);
  ctx.fillStyle = ['#777052', '#6b684d', '#7d7456'][v]; ctx.fillRect(x - w, y - h, w * 2, h);
  ctx.fillStyle = 'rgba(0,0,0,0.18)'; ctx.fillRect(x, y - h, w, h);
  ctx.fillStyle = ['#8e7955', '#8a5a40', '#7e6a4c'][v]; ctx.beginPath(); ctx.moveTo(x - w * 1.15, y - h); ctx.lineTo(x, y - h - 0.75 * s); ctx.lineTo(x + w * 1.15, y - h); ctx.closePath(); ctx.fill();
}
