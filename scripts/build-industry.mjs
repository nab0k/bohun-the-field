// "Industry on the map" pilot (Notion task 08.10.2026): real defence and dual-use companies from open data.
// Source of names, headquarters and coordinates: Wikidata (CC0). The selection, the product type and the short description
// are Claude's editorial choice from public descriptions and are marked as such in the output. City-level precision.
// No Russia, no Belarus. Ukraine: names only, without locations (separate list, filled from public sources later).
// Usage: node scripts/build-industry.mjs   (needs network: query.wikidata.org)
import fs from 'node:fs';

const UA = { 'User-Agent': 'BohunFieldPrototype/0.1 (research script)', Accept: 'application/sparql-results+json' };
// product: land | air | naval | munitions | missiles | electronics | drones | smallarms | software | propulsion | components | testing
// signal: one of the seven approved Current Interest signals when it clearly fits, else null
const PICK = [
  // Germany
  ['DE', 'Rheinmetall', 'land', 'INDUSTRIAL CAPACITY', 'armoured vehicles, munitions, air defence'],
  ['DE', 'Hensoldt', 'electronics', 'COMMUNICATIONS', 'radar, optronics, electronic warfare'],
  ['DE', 'Diehl Defence', 'missiles', null, 'missiles and air defence systems'],
  ['DE', 'KNDS Deutschland', 'land', null, 'armoured vehicles and artillery'],
  ['DE', 'Renk', 'propulsion', 'PROPULSION', 'transmissions for military vehicles'],
  ['DE', 'MBDA Deutschland', 'missiles', null, 'missile systems'],
  ['DE', 'Heckler & Koch', 'smallarms', null, 'small arms'],
  ['DE', 'Atlas Elektronik', 'naval', 'COMMUNICATIONS', 'naval electronics and sonar'],
  ['DE', 'Helsing', 'software', 'SOFTWARE', 'AI software for defence'],
  ['DE', 'STARK', 'drones', 'UAS / UGV', 'uncrewed systems'],
  ['DE', 'Junghans Microtec', 'components', 'COMPONENTS', 'fuzes and components'],
  ['DE', 'Rheinmetall MAN Military Vehicles', 'land', null, 'military trucks'],
  ['DE', 'ZF Friedrichshafen', 'propulsion', 'PROPULSION', 'transmissions and drives (dual-use)'],
  ['DE', '@MTU Aero Engines', 'propulsion', 'PROPULSION', 'aircraft engines'],
  ['DE', '@Rohde & Schwarz', 'electronics', 'COMMUNICATIONS', 'communications and test equipment'],
  ['DE', '@Quantum-Systems', 'drones', 'UAS / UGV', 'drones'],
  ['DE', '@thyssenkrupp Marine Systems', 'naval', null, 'submarines and warships'],
  ['DE', '@Lürssen', 'naval', null, 'naval and civil vessels'],
  // France
  ['FR', 'MBDA', 'missiles', null, 'missile systems'],
  ['FR', 'Thales Group', 'electronics', 'COMMUNICATIONS', 'electronics, communications, radar'],
  ['FR', 'Safran', 'propulsion', 'PROPULSION', 'aircraft engines and equipment'],
  ['FR', 'Dassault Aviation', 'air', null, 'military and business aircraft'],
  ['FR', 'Naval Group', 'naval', null, 'warships and submarines'],
  ['FR', 'KNDS France', 'land', null, 'armoured vehicles and artillery'],
  ['FR', 'CS Group', 'software', 'SOFTWARE', 'critical information systems'],
  ['FR', '@ArianeGroup', 'propulsion', 'PROPULSION', 'rocket engines and launchers'],
  ['FR', '@Arquus', 'land', null, 'military vehicles'],
  ['FR', '@Exail', 'naval', 'UAS / UGV', 'maritime drones and navigation'],
  ['FR', '@Delair', 'drones', 'UAS / UGV', 'drones'],
  ['FR', '@Parrot', 'drones', 'UAS / UGV', 'drones (dual-use)'],
  ['FR', '@Eurenco', 'munitions', 'COMPONENTS', 'explosives and propellants'],
  // Italy
  ['IT', 'Leonardo', 'air', 'INDUSTRIAL CAPACITY', 'helicopters, electronics, aerospace'],
  ['IT', 'Avio', 'propulsion', 'PROPULSION', 'rocket motors'],
  ['IT', 'KNDS Ammo Italy', 'munitions', null, 'munitions'],
  ['IT', 'Beretta', 'smallarms', null, 'small arms'],
  ['IT', 'Drass', 'naval', null, 'underwater vehicles'],
  ['IT', '@Fincantieri', 'naval', 'INDUSTRIAL CAPACITY', 'shipbuilding, warships'],
  ['IT', '@Iveco Defence Vehicles', 'land', null, 'military vehicles'],
  ['IT', '@Elettronica', 'electronics', 'COMMUNICATIONS', 'electronic warfare'],
  ['IT', '@Rheinmetall Italia', 'electronics', null, 'radar and air defence'],
  ['IT', '@Fiocchi Munizioni', 'munitions', null, 'munitions'],
  // Poland
  ['PL', 'Polish Armaments Group', 'land', 'INDUSTRIAL CAPACITY', 'state defence group'],
  ['PL', 'Huta Stalowa Wola', 'land', null, 'artillery and tracked vehicles'],
  ['PL', 'WB Group', 'drones', 'UAS / UGV', 'drones and communications'],
  ['PL', 'Mesko', 'munitions', null, 'munitions and short-range missiles'],
  ['PL', 'Łucznik Arms Factory', 'smallarms', null, 'small arms'],
  ['PL', 'Rosomak', 'land', null, 'wheeled armoured vehicles'],
  ['PL', 'PIT-RADWAR', 'electronics', 'COMMUNICATIONS', 'radar'],
  ['PL', 'Flytronic', 'drones', 'UAS / UGV', 'drones'],
  ['PL', 'Advanced Protection Systems', 'electronics', null, 'counter-drone systems and radar'],
  ['PL', 'Zakłady Mechaniczne Tarnów', 'smallarms', null, 'small arms'],
  ['PL', 'Dezamet', 'munitions', null, 'munitions'],
  ['PL', 'Bumar Łabędy', 'land', null, 'tanks and tracked vehicles'],
  ['PL', 'BELMA', 'components', 'COMPONENTS', 'electromechanical components'],
  // Czechia
  ['CZ', 'Czechoslovak Group', 'land', 'INDUSTRIAL CAPACITY', 'defence and industrial group'],
  ['CZ', 'Česká zbrojovka Uherský Brod', 'smallarms', null, 'small arms'],
  ['CZ', 'Colt CZ Group', 'smallarms', null, 'small arms and ammunition'],
  ['CZ', 'Excalibur Army', 'land', null, 'armoured vehicles and upgrades'],
  ['CZ', 'Tatra Defence Vehicle', 'land', null, 'military trucks'],
  ['CZ', 'Explosia', 'munitions', 'COMPONENTS', 'explosives'],
  ['CZ', 'ERA', 'electronics', 'COMMUNICATIONS', 'passive radar'],
  ['CZ', 'RETIA', 'electronics', 'COMMUNICATIONS', 'radar and communications'],
  ['CZ', 'ELDIS Pardubice', 'electronics', null, 'radar'],
  ['CZ', 'VOP CZ', 'land', null, 'vehicle repair and production'],
  ['CZ', 'Meopta Systems', 'electronics', 'COMPONENTS', 'optics'],
  ['CZ', 'Sellier & Bellot', 'munitions', null, 'munitions'],
  ['CZ', 'LOM Praha', 'air', null, 'aircraft maintenance and repair'],
  ['CZ', 'Military Research Institute', 'testing', 'TESTING & VALIDATION', 'military research and testing'],
  ['CZ', 'Military Technical Institute', 'testing', 'TESTING & VALIDATION', 'testing and development'],
  ['CZ', 'První brněnská strojírna Velká Bíteš', 'propulsion', 'PROPULSION', 'small gas turbine engines'],
  ['CZ', 'Crytur', 'components', 'COMPONENTS', 'crystals and optical components'],
  ['CZ', 'Phonexia', 'software', 'SOFTWARE', 'speech recognition'],
  ['CZ', 'MESIT holding', 'electronics', 'COMPONENTS', 'avionics and electronics'],
  ['CZ', 'STV Group', 'munitions', null, 'munitions'],
];
// corrections where Wikidata's headquarters is outdated (kept visible in the output)
const OVERRIDE = {
  TKMS: { city: 'Kiel', lon: 10.135, lat: 54.323, note: 'Headquarters in Kiel per the company website; Wikidata lists Essen (parent group).' },
};
// Ukraine: names only, no coordinates or cities (decision 08.10.2026). Wikidata labels; selection by Claude.
const UA_PICK = [
  ['Ukrainian Defense Industry', 'land', 'state defence conglomerate'],
  ['Luch', 'missiles', 'design bureau, missile systems'],
  ['Kharkiv Morozov Machine Building Design Bureau', 'land', 'armoured vehicles, design bureau'],
  ['Malyshev Factory', 'land', 'armoured vehicles and heavy engineering'],
  ['Zbroyar', 'smallarms', 'small arms'],
  ['SIA FORT', 'smallarms', 'small arms'],
  ['Mayak', 'smallarms', 'weapons'],
  ['Kyiv Arsenal', 'electronics', 'optics and instruments'],
  ['Neo Drones Tech', 'drones', 'uncrewed systems'],
  ['SkyCraft Ukraine', 'drones', 'FPV drones'],
  ['General Chereshnya', 'drones', 'drones'],
  ['Fire Point', 'drones', 'defence technology, long-range drones'],
  ['Kation', 'electronics', 'electronics'],
  ['Radar (ex-Communist)', 'electronics', 'radio electronics'],
  ['Orion Radio Works', 'electronics', 'radio communications'],
  ['LORTA', 'electronics', 'electronics'],
  ['Kharkiv Schevchenko Plant', 'electronics', 'electronics'],
];
const COUNTRY_Q = { DE: 'Q183', FR: 'Q142', IT: 'Q38', PL: 'Q36', CZ: 'Q213' };
const BANNED_COUNTRIES = new Set(['Q159', 'Q184']); // Russia, Belarus

const raw = {};
const scratch = process.argv[2];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// polite access: one request at a time, back off when Wikidata asks to slow down
async function getJSON(url) {
  for (let i = 0; i < 6; i++) {
    await sleep(1200);
    const r = await fetch(url, { headers: UA });
    const t = await r.text();
    if (r.ok && !t.startsWith('You are making')) return JSON.parse(t);
    await sleep(5000 * (i + 1));
  }
  throw new Error('wikidata: too many retries for ' + url.slice(0, 80));
}
const sparql = async (q) => (await getJSON('https://query.wikidata.org/sparql?query=' + encodeURIComponent(q))).results.bindings;
async function byLabel(code, label) {
  // pool first (same query as the research run), else a direct label search
  const pool = raw[code] ??= scratch && fs.existsSync(`${scratch}/raw-${code}.json`) ? JSON.parse(fs.readFileSync(`${scratch}/raw-${code}.json`)).results.bindings : [];
  const hit = pool.find((b) => b.cLabel.value === label && b.coord);
  if (hit) return { qid: hit.c.value.split('/').pop(), name: hit.cLabel.value, hq: hit.hqLabel?.value, coord: hit.coord.value, web: hit.web?.value };
  const s = await getJSON(`https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&limit=3&search=${encodeURIComponent(label)}`);
  for (const c of s.search ?? []) {
    const rows = await sparql(`SELECT ?hqLabel ?coord ?web ?country WHERE { OPTIONAL { wd:${c.id} wdt:P17 ?country } OPTIONAL { wd:${c.id} wdt:P159 ?hq . ?hq wdt:P625 ?coord . } OPTIONAL { wd:${c.id} wdt:P856 ?web } SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 3`);
    const r = rows.find((x) => x.coord);
    if (!r) continue;
    const country = r.country?.value.split('/').pop();
    if (BANNED_COUNTRIES.has(country)) continue;
    return { qid: c.id, name: c.label, hq: r.hqLabel?.value, coord: r.coord.value, web: r.web?.value, country };
  }
  return null;
}

const out = [], missing = [];
for (const [code, label0, product, signal, ru] of PICK) {
  const extra = label0.startsWith('@'), label = extra ? label0.slice(1) : label0;
  const f = await byLabel(code, label);
  if (!f) { missing.push(code + ' ' + label); continue; }
  if (f.country && f.country !== COUNTRY_Q[code]) { missing.push(`${code} ${label} (Wikidata country ${f.country})`); continue; }
  const [lon, lat] = f.coord.replace('Point(', '').replace(')', '').split(' ').map(Number);
  out.push({ id: f.qid, name: f.name, country: code, city: f.hq ?? null, lon: +lon.toFixed(3), lat: +lat.toFixed(3), product, signal, about: ru, web: f.web ?? null, source: `https://www.wikidata.org/wiki/${f.qid}`, ...(OVERRIDE[f.name] ?? {}) });
}
const uaPool = scratch && fs.existsSync(`${scratch}/raw-UA.json`) ? JSON.parse(fs.readFileSync(`${scratch}/raw-UA.json`)).results.bindings : [];
const ukraine = [];
for (const [label, product, ru] of UA_PICK) {
  const b = uaPool.find((x) => x.cLabel.value === label);
  if (!b) { missing.push('UA ' + label); continue; }
  const qid = b.c.value.split('/').pop();
  ukraine.push({ id: qid, name: label, country: 'UA', product, about: ru, web: b.web?.value ?? null, source: `https://www.wikidata.org/wiki/${qid}` });
}
const file = new URL('../public/atlas/industry.json', import.meta.url);
fs.writeFileSync(file, JSON.stringify({
  generator: 'scripts/build-industry.mjs', retrieved: new Date().toISOString().slice(0, 10),
  source: 'Wikidata (CC0): names, headquarters, coordinates, websites',
  editorial: 'Selection, product type, signal and the short description are Claude\'s judgement from public descriptions; to be checked.',
  notice: 'Open data. Not clients or partners of Bohun.', precision: 'headquarters city', excluded: 'Russia, Belarus; Ukraine without locations',
  companies: out, ukraine,
}, null, 1));
console.log(JSON.stringify({ ukraine: ukraine.length, companies: out.length, byCountry: Object.fromEntries(Object.keys(COUNTRY_Q).map((c) => [c, out.filter((x) => x.country === c).length])), missing }, null, 1));
