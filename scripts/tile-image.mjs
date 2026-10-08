// Cut a large 8-bit RGB/RGBA PNG into a grid of overlapping PNG tiles (no external libraries).
// Usage: node scripts/tile-image.mjs <in.png> <outDir> <tileW> <tileH> <xs comma> <ys comma> [prefix]
import fs from 'node:fs';
import zlib from 'node:zlib';

const [, , inFile, outDir, tw, th, xsArg, ysArg, prefix = 'tile'] = process.argv;
const buf = fs.readFileSync(inFile);
let pos = 8, W, H, ct; const idat = [];
while (pos < buf.length) { const len = buf.readUInt32BE(pos), type = buf.subarray(pos + 4, pos + 8).toString(), body = buf.subarray(pos + 8, pos + 8 + len);
  if (type === 'IHDR') { W = body.readUInt32BE(0); H = body.readUInt32BE(4); ct = body[9]; if (body[8] !== 8 || body[12]) throw new Error('need 8-bit non-interlaced'); } if (type === 'IDAT') idat.push(body); pos += 12 + len; }
const ch = ct === 6 ? 4 : ct === 2 ? 3 : 0; if (!ch) throw new Error('need RGB or RGBA');
const raw = zlib.inflateSync(Buffer.concat(idat)), stride = W * ch, img = Buffer.alloc(H * stride);
let prev = Buffer.alloc(stride);
for (let y = 0, i = 0; y < H; y++) {
  const f = raw[i++], line = Buffer.from(raw.subarray(i, i + stride)); i += stride;
  for (let x = 0; x < stride; x++) { const a = x >= ch ? line[x - ch] : 0, b = prev[x], c = x >= ch ? prev[x - ch] : 0; let add = 0;
    if (f === 1) add = a; else if (f === 2) add = b; else if (f === 3) add = (a + b) >> 1; else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); add = pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
    line[x] = (line[x] + add) & 255; }
  line.copy(img, y * stride); prev = line;
}
const crcTable = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc = (b) => { let c = 0xffffffff; for (const v of b) c = crcTable[(c ^ v) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const chunk = (type, data) => { const out = Buffer.alloc(12 + data.length); out.writeUInt32BE(data.length, 0); out.write(type, 4); data.copy(out, 8); out.writeUInt32BE(crc(out.subarray(4, 8 + data.length)), 8 + data.length); return out; };
const encode = (w, h, rgb) => { const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const rows = Buffer.alloc(h * (1 + w * 3)); for (let y = 0; y < h; y++) { rows[y * (1 + w * 3)] = 0; rgb.copy(rows, y * (1 + w * 3) + 1, y * w * 3, (y + 1) * w * 3); }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(rows, { level: 6 })), chunk('IEND', Buffer.alloc(0))]); };
fs.mkdirSync(outDir, { recursive: true });
const TW = +tw, TH = +th, xs = xsArg.split(',').map(Number), ys = ysArg.split(',').map(Number);
ys.forEach((y0, j) => xs.forEach((x0, i) => {
  const rgb = Buffer.alloc(TW * TH * 3);
  for (let y = 0; y < TH; y++) for (let x = 0; x < TW; x++) { const s = ((y0 + y) * W + (x0 + x)) * ch, d = (y * TW + x) * 3; rgb[d] = img[s]; rgb[d + 1] = img[s + 1]; rgb[d + 2] = img[s + 2]; }
  fs.writeFileSync(`${outDir}/${prefix}-r${j + 1}c${i + 1}.png`, encode(TW, TH, rgb));
}));
console.log(`${W}x${H} -> ${ys.length * xs.length} tiles ${TW}x${TH}`);
