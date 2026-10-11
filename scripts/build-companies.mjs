// Builds public/atlas/companies-<cc>.json for the map from a Notion export in data/notion/companies-<CC>.json
// (DB | Companies (all countries): Review status = accepted, Publishable = yes). First country: Austria (09.10.2026).
// The point is the centre of the company's city (OpenStreetMap Nominatim), not an address. Geocodes are cached in
// data/notion/geocode-<CC>.json so a rebuild does not query again; Nominatim is asked at most once per second.
// Usage: node scripts/build-companies.mjs AT
import fs from 'node:fs';

const CC = (process.argv[2] ?? 'AT').toUpperCase();
const COUNTRY = { AT: ['Austria', 'at', 'AUT'] }[CC];
if (!COUNTRY) throw new Error(`unknown country ${CC}`);
const src = JSON.parse(fs.readFileSync(new URL(`../data/notion/companies-${CC}.json`, import.meta.url)));
const cacheUrl = new URL(`../data/notion/geocode-${CC}.json`, import.meta.url);
const cache = fs.existsSync(cacheUrl) ? JSON.parse(fs.readFileSync(cacheUrl)) : {};
const CITY_EN = { Vienna: 'Wien' }; // Nominatim works best with the local name

async function geocode(city) {
  if (cache[city]) return cache[city];
  const q = new URLSearchParams({ city: CITY_EN[city] ?? city, countrycodes: COUNTRY[1], format: 'json', limit: '1' });
  const r = await fetch(`https://nominatim.openstreetmap.org/search?${q}`, { headers: { 'User-Agent': 'bohundefence-atlas-build/0.1 (map prototype)' } });
  if (!r.ok) throw new Error(`Nominatim ${city} → ${r.status}`);
  const [hit] = await r.json();
  await new Promise((ok) => setTimeout(ok, 1100));
  if (!hit) throw new Error(`no geocode for ${city}`);
  cache[city] = { lon: +(+hit.lon).toFixed(4), lat: +(+hit.lat).toFixed(4), osm: hit.display_name };
  fs.writeFileSync(cacheUrl, JSON.stringify(cache, null, 1) + '\n');
  return cache[city];
}

const companies = [];
for (const [i, [name, city, product, flags, parent, web, source, confidence, about]] of src.rows.entries()) {
  const g = await geocode(city);
  companies.push({ id: `${CC.toLowerCase()}-${String(i + 1).padStart(3, '0')}`, name, city, a3: COUNTRY[2], cat: product, flags: flags ? flags.split(',') : [], parent: parent || null, web: web || null, source, confidence, about, lon: g.lon, lat: g.lat });
}
fs.writeFileSync(new URL(`../public/atlas/companies-${CC.toLowerCase()}.json`, import.meta.url), JSON.stringify({
  generator: 'scripts/build-companies.mjs', country: COUNTRY[0], exported: src.exported, from: src.from,
  note: 'Open data. Not clients or partners of Bohun. The point is the city centre, not an address.', companies,
}, null, 1) + '\n');
console.log(`${COUNTRY[0]}: ${companies.length} companies in ${new Set(companies.map((c) => c.city)).size} cities`);
