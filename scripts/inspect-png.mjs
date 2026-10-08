// Inspect a delivered PNG before it goes into the manifest: size, alpha, visible box, hoof/ground contacts.
// Usage: node scripts/inspect-png.mjs <file.png> [--json]
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';

const file = process.argv[2];
if (!file) { console.error('usage: node scripts/inspect-png.mjs <file.png> [--json]'); process.exit(2); }
const buf = readFileSync(file);
if (buf.subarray(1, 4).toString() !== 'PNG') { console.error('not a PNG'); process.exit(1); }

let pos = 8, ihdr = null; const idat = [];
while (pos < buf.length) {
  const len = buf.readUInt32BE(pos), type = buf.subarray(pos + 4, pos + 8).toString(), body = buf.subarray(pos + 8, pos + 8 + len);
  if (type === 'IHDR') ihdr = { w: body.readUInt32BE(0), h: body.readUInt32BE(4), depth: body[8], ctype: body[9], interlace: body[12] };
  if (type === 'IDAT') idat.push(body);
  pos += 12 + len;
}
const { w, h, depth, ctype, interlace } = ihdr;
if (depth !== 8 || ctype !== 6 || interlace) { console.error(`unsupported PNG (need 8-bit RGBA, not interlaced): depth ${depth}, colour type ${ctype}, interlace ${interlace}`); process.exit(1); }

const raw = inflateSync(Buffer.concat(idat)), bpp = 4, stride = w * bpp;
const alpha = new Uint8Array(w * h);
let prev = new Uint8Array(stride);
for (let y = 0, i = 0; y < h; y++) {
  const f = raw[i++]; const line = new Uint8Array(raw.subarray(i, i + stride)); i += stride;
  for (let x = 0; x < stride; x++) {
    const a = x >= bpp ? line[x - bpp] : 0, b = prev[x], c = x >= bpp ? prev[x - bpp] : 0;
    let add = 0;
    if (f === 1) add = a; else if (f === 2) add = b; else if (f === 3) add = (a + b) >> 1;
    else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); add = pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
    line[x] = (line[x] + add) & 255;
  }
  for (let x = 0; x < w; x++) alpha[y * w + x] = line[x * 4 + 3];
  prev = line;
}

const T = 16, total = w * h;
const pct = (n) => +((100 * n) / total).toFixed(1);
let zero = 0, soft = 0, near = 0, full = 0, x0 = w, y0 = h, x1 = -1, y1 = -1, edge = 0;
for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
  const a = alpha[y * w + x];
  if (a === 0) zero++; else if (a < 240) soft++; else if (a < 255) near++; else full++;
  if (a > T) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; if (x === 0 || y === 0 || x === w - 1 || y === h - 1) edge++; }
}
const bbox = x1 < 0 ? null : { x0, y0, x1, y1, w: x1 - x0 + 1, h: y1 - y0 + 1, alphaThreshold: T };

// ground contacts: clusters of columns that are solid inside the lowest 70 px band of the visible box
const contacts = [];
if (bbox) {
  const top = Math.max(0, bbox.y1 - 70), cols = [];
  for (let x = bbox.x0; x <= bbox.x1; x++) { let hit = false; for (let y = top; y <= bbox.y1 && !hit; y++) hit = alpha[y * w + x] > 128; if (hit) cols.push(x); }
  const groups = [];
  for (const x of cols) { const g = groups.at(-1); if (g && x - g.at(-1) <= 12) g.push(x); else groups.push([x]); }
  for (const g of groups) {
    let low = 0; for (let y = h - 1; y >= 0 && !low; y--) for (const x of g) if (alpha[y * w + x] > 128) { low = y; break; }
    contacts.push({ cx: Math.round((g[0] + g.at(-1)) / 2), lowestY: low, width: g.at(-1) - g[0] + 1 });
  }
}
const corners = [alpha[0], alpha[w - 1], alpha[(h - 1) * w], alpha[h * w - 1]];
const report = {
  file, sha256: createHash('sha256').update(buf).digest('hex'), bytes: buf.length, size: [w, h],
  alpha: { transparentPct: pct(zero), softEdgePct: pct(soft), nearOpaquePct: pct(near), opaquePct: pct(full), cornersAlpha: corners, opaquePixelsOnFrameEdge: edge },
  visibleBBox: bbox, groundCandidates: contacts,
  suggestedAnchor: contacts.length ? { x: Math.round(contacts.reduce((s, c) => s + c.cx, 0) / contacts.length), y: Math.round(contacts.reduce((s, c) => s + c.lowestY, 0) / contacts.length), note: 'mean of all low contact clusters; drop raised feet by hand' } : null,
  warnings: [
    ...(edge ? ['figure touches the frame edge: it may be cropped'] : []),
    ...(corners.some((a) => a > T) ? ['corner pixels are not transparent'] : []),
    ...(zero / total < 0.2 ? ['little transparency: is the background really removed?'] : []),
  ],
};
if (process.argv.includes('--json')) console.log(JSON.stringify(report, null, 2));
else {
  console.log(`${file}\n  sha256 ${report.sha256}\n  ${w}x${h}, ${(buf.length / 1024).toFixed(0)} KiB, 8-bit RGBA`);
  console.log(`  alpha: ${report.alpha.transparentPct}% transparent, ${report.alpha.softEdgePct}% soft edge, ${report.alpha.nearOpaquePct}% near-opaque (240-254), ${report.alpha.opaquePct}% opaque`);
  console.log(`  visible box: ${bbox ? `x ${bbox.x0}-${bbox.x1}, y ${bbox.y0}-${bbox.y1} (${bbox.w} x ${bbox.h})` : 'empty'}`);
  console.log(`  ground contacts: ${contacts.map((c) => `(${c.cx}, ${c.lowestY})`).join(' ') || 'none'}`);
  if (report.suggestedAnchor) console.log(`  suggested anchor: (${report.suggestedAnchor.x}, ${report.suggestedAnchor.y})`);
  for (const wmsg of report.warnings) console.log('  WARNING: ' + wmsg);
}
