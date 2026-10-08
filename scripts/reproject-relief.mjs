// Re-project the Natural Earth relief raster (equirectangular, uncompressed RGB TIFF) into the tilted hero frame (2560 x 1600):
// conic conformal (contract projection, wider latitude range) + the fixed vertical squash cos 45.
// Usage: node scripts/reproject-relief.mjs <NE1_50M_SR_W.tif> <outDir> [ne-countries-ukr.geojson]
import fs from 'node:fs';
import zlib from 'node:zlib';

const [, , tifPath, outDir, bordersPath] = process.argv;
const layout = JSON.parse(fs.readFileSync(new URL('../handoff/classic-tilt-v03/world-layout-tilt-draft.json', import.meta.url)));
const P = layout.projection, K = layout.tilt.squashY, [W, H] = layout.canvas;

// ---- minimal TIFF reader: little endian, uncompressed, 8-bit RGB, chunky ----
const t = fs.readFileSync(tifPath);
if (t.toString('latin1', 0, 2) !== 'II') throw new Error('little-endian TIFF expected');
const ifd = t.readUInt32LE(4), n = t.readUInt16LE(ifd), tag = {};
for (let i = 0; i < n; i++) { const o = ifd + 2 + i * 12, id = t.readUInt16LE(o), type = t.readUInt16LE(o + 2), count = t.readUInt32LE(o + 4);
  const size = { 3: 2, 4: 4, 1: 1 }[type], read = (p) => (size === 2 ? t.readUInt16LE(p) : size === 4 ? t.readUInt32LE(p) : t[p]);
  const base = count * size > 4 ? t.readUInt32LE(o + 8) : o + 8; tag[id] = Array.from({ length: count }, (_, k) => read(base + k * size)); }
const SW = tag[256][0], SH = tag[257][0];
if (tag[259][0] !== 1 || tag[277][0] !== 3 || tag[284]?.[0] === 2) throw new Error('need uncompressed chunky RGB');
const offs = tag[273], cnts = tag[279], src = Buffer.alloc(SW * SH * 3); let w = 0;
for (let i = 0; i < offs.length; i++) { t.copy(src, w, offs[i], offs[i] + cnts[i]); w += cnts[i]; }
console.log(`source ${SW}x${SH}`);

// ---- projection (same maths as src/game/geo.js, with the draft v0.3 parameters) ----
const rad = Math.PI / 180, [p0, p1] = P.parallels.map((d) => d * rad), tany = (y) => Math.tan((Math.PI / 2 + y) / 2);
const nn = Math.log(Math.cos(p0) / Math.cos(p1)) / Math.log(tany(p1) / tany(p0)), f = (Math.cos(p0) * tany(p0) ** nn) / nn;
const raw = (la, ph) => { const r = f / tany(ph) ** nn; return [r * Math.sin(nn * la), f - r * Math.cos(nn * la)]; };
const [cx, cy] = raw(P.center[0] * rad, P.center[1] * rad);
const forward = (lon, lat) => { const [x, y] = raw((lon + P.rotate[0]) * rad, lat * rad); return [P.translate[0] + P.scale * (x - cx), (P.translate[1] - P.scale * (y - cy)) * K]; };
const inverse = (X, Y) => { const rx = (X - P.translate[0]) / P.scale + cx, ry = cy - (Y / K - P.translate[1]) / P.scale, r = Math.hypot(rx, f - ry);
  return [Math.atan2(rx, f - ry) / nn / rad - P.rotate[0], (2 * Math.atan((f / r) ** (1 / nn)) - Math.PI / 2) / rad]; };

// sanity: the three anchors must come out where the draft layout says
for (const nd of layout.nodes) { const [x, y] = forward(...nd.lonLat); if (Math.hypot(x - nd.world[0], y - nd.world[1]) > 0.05) throw new Error(`anchor mismatch ${nd.id}: ${x},${y} vs ${nd.world}`); }
console.log('anchors reproduced:', layout.nodes.map((q) => q.id).join(', '));

// ---- resample ----
const out = Buffer.alloc(W * H * 3);
const px = (c, r, ch) => src[(Math.min(SH - 1, Math.max(0, r)) * SW + (((c % SW) + SW) % SW)) * 3 + ch];
for (let Y = 0; Y < H; Y++) for (let X = 0; X < W; X++) {
  const [lon, lat] = inverse(X + 0.5, Y + 0.5), c = (lon + 180) * 30 - 0.5, r = (90 - lat) * 30 - 0.5, c0 = Math.floor(c), r0 = Math.floor(r), dc = c - c0, dr = r - r0;
  for (let ch = 0; ch < 3; ch++) out[(Y * W + X) * 3 + ch] = Math.round(px(c0, r0, ch) * (1 - dc) * (1 - dr) + px(c0 + 1, r0, ch) * dc * (1 - dr) + px(c0, r0 + 1, ch) * (1 - dc) * dr + px(c0 + 1, r0 + 1, ch) * dc * dr);
}

// ---- PNG writer ----
const crcT = (() => { const a = new Uint32Array(256); for (let i = 0; i < 256; i++) { let c = i; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; a[i] = c >>> 0; } return a; })();
const crc = (b) => { let c = 0xffffffff; for (const v of b) c = crcT[(c ^ v) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const chunk = (ty, d) => { const o = Buffer.alloc(12 + d.length); o.writeUInt32BE(d.length, 0); o.write(ty, 4); d.copy(o, 8); o.writeUInt32BE(crc(o.subarray(4, 8 + d.length)), 8 + d.length); return o; };
const png = (w2, h2, rgb) => { const ih = Buffer.alloc(13); ih.writeUInt32BE(w2, 0); ih.writeUInt32BE(h2, 4); ih[8] = 8; ih[9] = 2; const rows = Buffer.alloc(h2 * (1 + w2 * 3)); for (let y = 0; y < h2; y++) { rows[y * (1 + w2 * 3)] = 0; rgb.copy(rows, y * (1 + w2 * 3) + 1, y * w2 * 3, (y + 1) * w2 * 3); }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', zlib.deflateSync(rows, { level: 6 })), chunk('IEND', Buffer.alloc(0))]); };
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(`${outDir}/first-scene-tilt45-relief-clean.png`, png(W, H, out));

// ---- borders from the vector data: Ukraine in gold and thicker, other countries thin white ----
const lineOn = (buf, x0, y0, x1, y1, w2, col, alpha) => {
  const steps = Math.ceil(Math.hypot(x1 - x0, y1 - y0)); for (let s = 0; s <= steps; s++) { const x = x0 + ((x1 - x0) * s) / (steps || 1), y = y0 + ((y1 - y0) * s) / (steps || 1);
    for (let yy = Math.round(y - w2); yy <= Math.round(y + w2); yy++) for (let xx = Math.round(x - w2); xx <= Math.round(x + w2); xx++) { if (xx < 0 || yy < 0 || xx >= W || yy >= H || (xx - x) ** 2 + (yy - y) ** 2 > w2 * w2 + 0.3) continue; const i = (yy * W + xx) * 3; for (let c = 0; c < 3; c++) buf[i + c] = Math.round(buf[i + c] * (1 - alpha) + col[c] * alpha); } } };
const withBorders = Buffer.from(out);
if (bordersPath) {
  const gj = JSON.parse(fs.readFileSync(bordersPath));
  for (const pass of ['other', 'ukr']) for (const ft of gj.features) {
    const isU = ft.properties.ADM0_A3 === 'UKR'; if ((pass === 'ukr') !== isU) continue;
    const polys = ft.geometry.type === 'Polygon' ? [ft.geometry.coordinates] : ft.geometry.coordinates;
    for (const poly of polys) for (const ring of poly) { let prevPt = null;
      for (const [lon, lat] of ring) { if (lat < 30 || lat > 70 || lon < -5 || lon > 70) { prevPt = null; continue; } const q = forward(lon, lat);
        if (prevPt && Math.hypot(q[0] - prevPt[0], q[1] - prevPt[1]) < 200) isU ? lineOn(withBorders, prevPt[0], prevPt[1], q[0], q[1], 2.2, [255, 200, 60], 0.95) : lineOn(withBorders, prevPt[0], prevPt[1], q[0], q[1], 0.9, [255, 255, 255], 0.55);
        prevPt = q; } }
  }
  fs.writeFileSync(`${outDir}/first-scene-tilt45-relief-borders.png`, png(W, H, withBorders));
}

// guide variant: red city anchors with crosshairs on top (to check alignment; NOT to be painted)
const g = Buffer.from(withBorders);
const dot = (x, y, rx, ry, col) => { for (let yy = Math.floor(y - ry - 2); yy <= y + ry + 2; yy++) for (let xx = Math.floor(x - rx - 2); xx <= x + rx + 2; xx++) { if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue; const d = ((xx - x) / rx) ** 2 + ((yy - y) / ry) ** 2; if (d <= 1.0) { const i = (yy * W + xx) * 3; g[i] = col[0]; g[i + 1] = col[1]; g[i + 2] = col[2]; } } };
for (const nd of layout.nodes) { const [x, y] = nd.world; for (let d = -40; d <= 40; d++) { for (const [xx, yy] of [[x + d, y], [x, y + d * K]]) { const i = (Math.round(yy) * W + Math.round(xx)) * 3; if (i >= 0 && i < g.length - 3) { g[i] = 255; g[i + 1] = 255; g[i + 2] = 255; } } } dot(x, y, 14, 14 * K, [255, 59, 48]); }
fs.writeFileSync(`${outDir}/first-scene-tilt45-relief-guide.png`, png(W, H, g));
console.log('written to', outDir);
