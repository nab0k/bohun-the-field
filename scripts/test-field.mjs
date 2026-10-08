// Checks for /field.html data: the hero geography is generated, uses the flows frame and keeps Crimea inside Ukraine.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as data from '../src/flows/data/network.js';
import { project } from '../src/flows/core.js';

const geo = JSON.parse(fs.readFileSync(new URL('../public/field/hero-geo.json', import.meta.url)));
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };
const inside = (pt, ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i], b = ring[j]; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
const inUA = (lon, lat) => { const p = project(lon, lat); return geo.land.find((c) => c.id === 'UKR').rings.some((r) => inside([p.x, p.y], r)); };

ok('hero geo: generated file names its generator and source', () => {
  assert.equal(geo.generator, 'scripts/build-field-hero.mjs'); assert.match(geo.source, /Natural Earth/);
});
ok('hero geo: same frame as the flows world (Kyiv projects to the same point)', () => {
  const kyiv = geo.cities.find((c) => c.name === 'Kyiv'), p = project(30.5, 50.43);
  assert.ok(Math.hypot(kyiv.x - p.x, kyiv.y - p.y) < 3, `${kyiv.x},${kyiv.y} vs ${p.x},${p.y}`);
});
ok('hero geo: Crimea (Simferopol, Sevastopol) is inside the Ukrainian outline; Dnipro river present', () => {
  assert.ok(inUA(34.1, 44.95)); assert.ok(inUA(33.52, 44.6));
  assert.ok(geo.rivers.some((r) => /Dnipro|Dnepre/.test(r.name)));
});
ok('hero geo: every Ukrainian test node of the network sits inside the hero box', () => {
  const b = geo.box;
  for (const nd of data.nodes.filter((x) => x.lon > 22 && x.lon < 41 && x.lat > 44 && x.lat < 53)) {
    const p = project(nd.lon, nd.lat); assert.ok(p.x > b.x0 && p.x < b.x1 && p.y > b.y0 && p.y < b.y1, nd.id);
  }
});
console.log(`${n} field checks passed`);
