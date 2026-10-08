import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const meta = JSON.parse(readFileSync(new URL('../src/game/data/rider-sprite.json', import.meta.url)));
const png = readFileSync(new URL('../public' + meta.url, import.meta.url));
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };

ok('rider PNG is the unmodified original (sha256)', () => {
  assert.equal(createHash('sha256').update(png).digest('hex'), meta.sha256);
});
ok('PNG header: 1254 x 1254, 8-bit RGBA', () => {
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  assert.equal(png.readUInt32BE(16), meta.size[0]);
  assert.equal(png.readUInt32BE(20), meta.size[1]);
  assert.equal(png[24], 8); assert.equal(png[25], 6);
});
ok('anchor lies on the hooves, inside the visible box', () => {
  const b = meta.visibleBBox, a = meta.anchor;
  assert.ok(a.x > b.x0 && a.x < b.x1 && a.y > b.y0 && a.y <= b.y1);
  const grounded = meta.hooves.filter((h) => h.grounded);
  assert.equal(grounded.length, 3);
  assert.equal(a.x, Math.round(grounded.reduce((s, h) => s + h.cx, 0) / grounded.length));
  assert.equal(a.y, Math.round(grounded.reduce((s, h) => s + h.lowestY, 0) / grounded.length));
});
ok('screen height target is 80-95 px on desktop', () => {
  assert.ok(meta.screenHeightPx.desktop >= 80 && meta.screenHeightPx.desktop <= 95);
});
console.log(`\n${n} asset checks passed`);
