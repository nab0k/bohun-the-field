// Country outlines for /atlas.html (lon/lat GeoJSON, simplified). They replace the basemap's own boundaries and labels,
// so the political representation is Natural Earth admin_0_countries_ukr (Crimea inside Ukraine), not OpenStreetMap's.
// Not a map of territorial control.
// Usage: node scripts/build-atlas-borders.mjs <dir with ne-countries-ukr.geojson>
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
if (!dir) { console.error('usage: node scripts/build-atlas-borders.mjs <natural-earth-dir>'); process.exit(2); }
const EU = new Set('AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE'.split(' '));
const TOL = 0.035; // degrees, Douglas-Peucker tolerance (~4 km)

function dp(pts, tol) {
  if (pts.length < 4) return pts;
  const keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop(); let best = -1, bd = 0;
    const [ax, ay] = pts[a], [bx, by] = pts[b], dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy);
    // closed rings start and end on the same point: then measure plain distance from it
    for (let i = a + 1; i < b; i++) { const d = L < 1e-9 ? Math.hypot(pts[i][0] - ax, pts[i][1] - ay) : Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L; if (d > bd) { bd = d; best = i; } }
    if (bd > tol) { keep[best] = 1; stack.push([a, best], [best, b]); }
  }
  return pts.filter((_, i) => keep[i]).map(([x, y]) => [+x.toFixed(3), +y.toFixed(3)]);
}

const gj = JSON.parse(fs.readFileSync(path.join(dir, 'ne-countries-ukr.geojson')));
const features = [];
let verts = 0;
for (const f of gj.features) {
  const a3 = f.properties.ADM0_A3;
  if (a3 === 'ATA') continue;
  const polys = (f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates)
    .map((poly) => poly.map((r) => dp(r, TOL)).filter((r) => r.length >= 4)).filter((p) => p.length);
  if (!polys.length) continue;
  polys.forEach((p) => p.forEach((r) => (verts += r.length)));
  features.push({ type: 'Feature', properties: { a3, name: f.properties.NAME, cls: a3 === 'UKR' ? 'ua' : EU.has(a3) ? 'eu' : 'other' }, geometry: { type: 'MultiPolygon', coordinates: polys } });
}
const inside = (pt, ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i], b = ring[j]; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
const ua = features.find((f) => f.properties.a3 === 'UKR');
if (!ua.geometry.coordinates.some((p) => inside([34.1, 44.95], p[0]))) throw new Error('Crimea is not inside the UKR outline');
const out = { type: 'FeatureCollection', generator: 'scripts/build-atlas-borders.mjs', source: 'Natural Earth admin_0_countries_ukr (public domain)', note: 'Political representation, not territorial control.', features };
const file = new URL('../public/atlas/countries.geojson', import.meta.url);
fs.writeFileSync(file, JSON.stringify(out));
console.log(JSON.stringify({ countries: features.length, vertices: verts, kB: Math.round(fs.statSync(file).size / 1024) }));
