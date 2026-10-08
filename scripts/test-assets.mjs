import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Every delivered asset in the manifest must be the file it claims to be. null entries are "not delivered yet".
const manifest = JSON.parse(readFileSync(new URL('../src/game/data/assets.json', import.meta.url)));
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };

const entries = Object.entries(manifest).filter(([k, v]) => !k.startsWith('_') && v);
ok('manifest has the rider delivered', () => assert.ok(manifest.rider));

for (const [name, meta] of entries) {
  const png = readFileSync(new URL('../public' + meta.url, import.meta.url));
  ok(`${name}: file is the recorded one (sha256)`, () => assert.equal(createHash('sha256').update(png).digest('hex'), meta.sha256));
  ok(`${name}: PNG header matches size, 8-bit RGBA`, () => {
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    assert.equal(png.readUInt32BE(16), meta.size[0]);
    assert.equal(png.readUInt32BE(20), meta.size[1]);
    assert.equal(png[24], 8); assert.equal(png[25], 6);
  });
  if (name === 'background') {
    ok('background: size equals the world (2560 x 1600)', () => assert.deepEqual(meta.size, [2560, 1600]));
    continue;
  }
  ok(`${name}: anchor lies inside the visible box`, () => {
    const b = meta.visibleBBox, a = meta.anchor;
    assert.ok(a.x > b.x0 && a.x < b.x1 && a.y > b.y0 && a.y <= b.y1);
  });
  ok(`${name}: screen height target is set for desktop and narrow`, () => {
    assert.ok(meta.screenHeightPx.desktop > 0 && meta.screenHeightPx.narrow > 0);
    assert.ok(meta.runtimeTextureSize >= 128);
  });
}

ok('rider: anchor is the mean of the grounded hoof contacts', () => {
  const grounded = manifest.rider.hooves.filter((h) => h.grounded);
  assert.equal(grounded.length, 3);
  assert.equal(manifest.rider.anchor.x, Math.round(grounded.reduce((s, h) => s + h.cx, 0) / grounded.length));
  assert.equal(manifest.rider.anchor.y, Math.round(grounded.reduce((s, h) => s + h.lowestY, 0) / grounded.length));
});
ok('rider: screen height target is 80-95 px on desktop', () => {
  const d = manifest.rider.screenHeightPx.desktop;
  assert.ok(d >= 80 && d <= 95);
});
console.log(`\n${n} asset checks passed`);
