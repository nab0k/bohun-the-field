// Warm placeholder world base for the flows prototype. Whole planet, equirectangular + the fixed 45 degree squash (cos 45).
// Data: Natural Earth admin_0_countries_ukr (public domain; political representation: not a map of territorial control).
// Usage: node scripts/build-world-base.mjs <path/to/ne-countries-ukr.geojson>
import fs from 'node:fs';

const src = process.argv[2];
if (!src) { console.error('usage: node scripts/build-world-base.mjs <ne-countries-ukr.geojson>'); process.exit(2); }
const W = 8192, PX = W / 360, SQ = Math.cos(Math.PI / 4), LAT_N = 84, LAT_S = -58;
const H = Math.round((LAT_N - LAT_S) * PX * SQ);
const x = (lon) => (lon + 180) * PX;
const y = (lat) => (LAT_N - lat) * PX * SQ;
const eu = new Set('AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE'.split(' '));
const gj = JSON.parse(fs.readFileSync(src));
const MIN = 1.1; // drop vertices closer than this (px) to the previous one
let paths = '', n = 0;
for (const f of gj.features) {
  const id = f.properties.ADM0_A3;
  if (id === 'ATA') continue; // Antarctica is outside the shown latitude band
  const fill = id === 'UKR' ? '#e6cf6e' : eu.has(id) ? '#8fbf78' : '#cdbd8c';
  const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  let d = '';
  for (const poly of polys) for (const ring of poly) {
    let last = null, seg = '';
    for (const [lon, lat] of ring) {
      const px = x(lon), py = y(lat);
      if (last && Math.hypot(px - last[0], py - last[1]) < MIN) continue;
      seg += (last ? 'L' : 'M') + px.toFixed(1) + ',' + py.toFixed(1);
      last = [px, py]; n++;
    }
    if (last && seg.split('L').length > 3) d += seg + 'Z';
  }
  if (d) paths += `<path d="${d}" fill="${fill}" stroke="#6b5f3c" stroke-width="0.9"/>`;
}
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="100%" height="100%" fill="#2f6f95"/>${paths}</svg>`;
fs.writeFileSync(new URL('../public/flows/world-tilt-base.svg', import.meta.url), svg);
fs.writeFileSync(new URL('../src/flows/data/world-frame.json', import.meta.url), JSON.stringify({ width: W, height: H, pxPerDeg: PX, squash: SQ, latNorth: LAT_N, latSouth: LAT_S, projection: 'equirectangular + vertical squash cos45' }, null, 2) + '\n');
console.log(JSON.stringify({ size: [W, H], vertices: n, svgKB: Math.round(svg.length / 1024) }));
