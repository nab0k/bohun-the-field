// Geography for the /field.html hero scene (v0.4, look of the v0.3 slice on real data).
// Same frame as the flows world (equirectangular + fixed vertical squash cos 45, src/flows/data/world-frame.json),
// so every node from the flows network lands on the same spot. Output units are flows world px.
// Data: Natural Earth admin_0_countries_ukr + rivers (public domain). Political representation, not territorial control.
// Usage: node scripts/build-field-hero.mjs <dir with ne-countries-ukr.geojson and ne-rivers.geojson>
import fs from 'node:fs';
import path from 'node:path';
import frame from '../src/flows/data/world-frame.json' with { type: 'json' };

const dir = process.argv[2];
if (!dir) { console.error('usage: node scripts/build-field-hero.mjs <natural-earth-dir>'); process.exit(2); }
const BOUNDS = { lon: [8, 46], lat: [40, 58] }; // hero: Ukraine, Black Sea, Central Europe
const x = (lon) => (lon + 180) * frame.pxPerDeg;
const y = (lat) => (frame.latNorth - lat) * frame.pxPerDeg * frame.squash;
const box = { x0: x(BOUNDS.lon[0]), x1: x(BOUNDS.lon[1]), y0: y(BOUNDS.lat[1]), y1: y(BOUNDS.lat[0]) };
const MIN = 0.12; // world px; drop vertices closer than this to the previous one
const EU = new Set('AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE'.split(' '));

const near = (ring) => ring.some(([lon, lat]) => lon > BOUNDS.lon[0] - 4 && lon < BOUNDS.lon[1] + 4 && lat > BOUNDS.lat[0] - 4 && lat < BOUNDS.lat[1] + 4);
const proj = (ring) => {
  const out = [];
  for (const [lon, lat] of ring) {
    const p = [+x(lon).toFixed(2), +y(lat).toFixed(2)], l = out.at(-1);
    if (!l || Math.hypot(p[0] - l[0], p[1] - l[1]) >= MIN) out.push(p);
  }
  return out;
};

// Sutherland-Hodgman clip of a ring to the hero box plus a margin, so Russia or Africa do not ship whole
const M = 40, CB = { x0: box.x0 - M, x1: box.x1 + M, y0: box.y0 - M, y1: box.y1 + M };
function clip(ring) {
  let pts = ring;
  const edges = [[(p) => p[0] >= CB.x0, (a, b) => { const t = (CB.x0 - a[0]) / (b[0] - a[0]); return [CB.x0, a[1] + t * (b[1] - a[1])]; }],
    [(p) => p[0] <= CB.x1, (a, b) => { const t = (CB.x1 - a[0]) / (b[0] - a[0]); return [CB.x1, a[1] + t * (b[1] - a[1])]; }],
    [(p) => p[1] >= CB.y0, (a, b) => { const t = (CB.y0 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), CB.y0]; }],
    [(p) => p[1] <= CB.y1, (a, b) => { const t = (CB.y1 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), CB.y1]; }]];
  for (const [ins, cut] of edges) {
    const res = [];
    for (let i = 0; i < pts.length; i++) {
      const a = pts[(i + pts.length - 1) % pts.length], b = pts[i];
      if (ins(b)) { if (!ins(a)) res.push(cut(a, b)); res.push(b); } else if (ins(a)) res.push(cut(a, b));
    }
    pts = res;
    if (!pts.length) break;
  }
  return pts.map(([a, b]) => [+a.toFixed(2), +b.toFixed(2)]);
}

const countries = JSON.parse(fs.readFileSync(path.join(dir, 'ne-countries-ukr.geojson')));
const land = [];
for (const f of countries.features) {
  const id = f.properties.ADM0_A3;
  const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  const rings = [];
  for (const poly of polys) for (const ring of poly) if (near(ring)) { const r = clip(proj(ring)); if (r.length > 3) rings.push(r); }
  if (rings.length) land.push({ id, name: f.properties.NAME, cls: id === 'UKR' ? 'ua' : EU.has(id) ? 'eu' : 'other', rings });
}
// Crimea must sit inside the Ukrainian outline in this dataset (checked, not assumed)
const inside = (pt, ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i], b = ring[j]; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
const simferopol = [x(34.1), y(44.95)];
if (!land.find((c) => c.id === 'UKR').rings.some((r) => inside(simferopol, r))) throw new Error('Crimea is not inside the UKR outline');

const riversGj = JSON.parse(fs.readFileSync(path.join(dir, 'ne-rivers.geojson')));
const KEEP = /^(Dnipro|Dnepre|Dniester|Danube|Vistula|Southern Bug|Pripyat|Desna|Don|Prut|Oder|Elbe|Daugava|Tisa|Tisza|Siverskyi Donets|Severskiy Donets)$/;
const rivers = [];
for (const f of riversGj.features) {
  const name = f.properties.name || '';
  if (!KEEP.test(name)) continue;
  const lines = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const line of lines) if (near(line)) { const r = proj(line).filter(([a, b]) => a > CB.x0 && a < CB.x1 && b > CB.y0 && b < CB.y1); if (r.length > 1) rivers.push({ name, rank: f.properties.scalerank, pts: r }); }
}

// large cities as settlement clusters (real positions; names stay in the data, the scene shows only a few)
const citiesGj = JSON.parse(fs.readFileSync(path.join(dir, 'ne-cities.geojson')));
const cities = citiesGj.features.map((f) => f.properties).filter((c) => c.LONGITUDE > BOUNDS.lon[0] && c.LONGITUDE < BOUNDS.lon[1] && c.LATITUDE > BOUNDS.lat[0] && c.LATITUDE < BOUNDS.lat[1] && c.POP_MAX >= (c.ADM0_A3 === 'UKR' ? 150000 : 400000))
  .map((c) => ({ name: c.NAME, a3: c.ADM0_A3, pop: c.POP_MAX, cap: c.ADM0CAP === 1, x: +x(c.LONGITUDE).toFixed(2), y: +y(c.LATITUDE).toFixed(2) }));

const out = { generator: 'scripts/build-field-hero.mjs', source: 'Natural Earth admin_0_countries_ukr, rivers (public domain)', note: 'Political representation, not a map of territorial control.', frame: 'flows world px', bounds: BOUNDS, box, land, rivers, cities };
const file = new URL('../public/field/hero-geo.json', import.meta.url);
fs.mkdirSync(new URL('.', file), { recursive: true });
fs.writeFileSync(file, JSON.stringify(out));
console.log(JSON.stringify({ countries: land.length, rivers: rivers.length, cities: cities.length, box, kB: Math.round(fs.statSync(file).size / 1024) }));
