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
  ['DE', 'Rheinmetall', 'land', 'INDUSTRIAL CAPACITY', 'бронетехника, боеприпасы, системы ПВО'],
  ['DE', 'Hensoldt', 'electronics', 'COMMUNICATIONS', 'радары, оптроника, РЭБ'],
  ['DE', 'Diehl Defence', 'missiles', null, 'ракеты и системы ПВО'],
  ['DE', 'KNDS Deutschland', 'land', null, 'бронетехника и артиллерия'],
  ['DE', 'Renk', 'propulsion', 'PROPULSION', 'трансмиссии для военной техники'],
  ['DE', 'MBDA Deutschland', 'missiles', null, 'ракетные системы'],
  ['DE', 'Heckler & Koch', 'smallarms', null, 'стрелковое оружие'],
  ['DE', 'Atlas Elektronik', 'naval', 'COMMUNICATIONS', 'морская электроника и сонары'],
  ['DE', 'Helsing', 'software', 'SOFTWARE', 'программное обеспечение с ИИ для обороны'],
  ['DE', 'STARK', 'drones', 'UAS / UGV', 'беспилотные системы'],
  ['DE', 'Junghans Microtec', 'components', 'COMPONENTS', 'взрыватели и компоненты'],
  ['DE', 'Rheinmetall MAN Military Vehicles', 'land', null, 'военные грузовики'],
  ['DE', 'ZF Friedrichshafen', 'propulsion', 'PROPULSION', 'трансмиссии и приводы (dual-use)'],
  ['DE', '@MTU Aero Engines', 'propulsion', 'PROPULSION', 'авиационные двигатели'],
  ['DE', '@Rohde & Schwarz', 'electronics', 'COMMUNICATIONS', 'связь и измерительная техника'],
  ['DE', '@Quantum-Systems', 'drones', 'UAS / UGV', 'беспилотники'],
  ['DE', '@thyssenkrupp Marine Systems', 'naval', null, 'подводные лодки и корабли'],
  ['DE', '@Lürssen', 'naval', null, 'военные и гражданские суда'],
  // France
  ['FR', 'MBDA', 'missiles', null, 'ракетные системы'],
  ['FR', 'Thales Group', 'electronics', 'COMMUNICATIONS', 'электроника, связь, радары'],
  ['FR', 'Safran', 'propulsion', 'PROPULSION', 'двигатели и оборудование для авиации'],
  ['FR', 'Dassault Aviation', 'air', null, 'военные и деловые самолёты'],
  ['FR', 'Naval Group', 'naval', null, 'военные корабли и подводные лодки'],
  ['FR', 'KNDS France', 'land', null, 'бронетехника и артиллерия'],
  ['FR', 'CS Group', 'software', 'SOFTWARE', 'критические информационные системы'],
  ['FR', '@ArianeGroup', 'propulsion', 'PROPULSION', 'ракетные двигатели и носители'],
  ['FR', '@Arquus', 'land', null, 'военные автомобили'],
  ['FR', '@Exail', 'naval', 'UAS / UGV', 'морские беспилотники и навигация'],
  ['FR', '@Delair', 'drones', 'UAS / UGV', 'беспилотники'],
  ['FR', '@Parrot', 'drones', 'UAS / UGV', 'дроны (dual-use)'],
  ['FR', '@Eurenco', 'munitions', 'COMPONENTS', 'взрывчатые вещества и пороха'],
  // Italy
  ['IT', 'Leonardo', 'air', 'INDUSTRIAL CAPACITY', 'вертолёты, электроника, авиация'],
  ['IT', 'Avio', 'propulsion', 'PROPULSION', 'ракетные двигатели'],
  ['IT', 'KNDS Ammo Italy', 'munitions', null, 'боеприпасы'],
  ['IT', 'Beretta', 'smallarms', null, 'стрелковое оружие'],
  ['IT', 'Drass', 'naval', null, 'подводные аппараты'],
  ['IT', '@Fincantieri', 'naval', 'INDUSTRIAL CAPACITY', 'судостроение, военные корабли'],
  ['IT', '@Iveco Defence Vehicles', 'land', null, 'военные автомобили'],
  ['IT', '@Elettronica', 'electronics', 'COMMUNICATIONS', 'радиоэлектронная борьба'],
  ['IT', '@Rheinmetall Italia', 'electronics', null, 'радары и системы ПВО'],
  ['IT', '@Fiocchi Munizioni', 'munitions', null, 'боеприпасы'],
  // Poland
  ['PL', 'Polish Armaments Group', 'land', 'INDUSTRIAL CAPACITY', 'государственная оборонная группа'],
  ['PL', 'Huta Stalowa Wola', 'land', null, 'артиллерия и гусеничная техника'],
  ['PL', 'WB Group', 'drones', 'UAS / UGV', 'беспилотники и связь'],
  ['PL', 'Mesko', 'munitions', null, 'боеприпасы и ракеты малой дальности'],
  ['PL', 'Łucznik Arms Factory', 'smallarms', null, 'стрелковое оружие'],
  ['PL', 'Rosomak', 'land', null, 'колёсные бронетранспортёры'],
  ['PL', 'PIT-RADWAR', 'electronics', 'COMMUNICATIONS', 'радары'],
  ['PL', 'Flytronic', 'drones', 'UAS / UGV', 'беспилотники'],
  ['PL', 'Advanced Protection Systems', 'electronics', null, 'противодроновые системы и радары'],
  ['PL', 'Zakłady Mechaniczne Tarnów', 'smallarms', null, 'стрелковое оружие'],
  ['PL', 'Dezamet', 'munitions', null, 'боеприпасы'],
  ['PL', 'Bumar Łabędy', 'land', null, 'танки и гусеничная техника'],
  ['PL', 'BELMA', 'components', 'COMPONENTS', 'электромеханические компоненты'],
  // Czechia
  ['CZ', 'Czechoslovak Group', 'land', 'INDUSTRIAL CAPACITY', 'оборонная и промышленная группа'],
  ['CZ', 'Česká zbrojovka Uherský Brod', 'smallarms', null, 'стрелковое оружие'],
  ['CZ', 'Colt CZ Group', 'smallarms', null, 'стрелковое оружие и боеприпасы'],
  ['CZ', 'Excalibur Army', 'land', null, 'бронетехника и модернизация'],
  ['CZ', 'Tatra Defence Vehicle', 'land', null, 'военные грузовики'],
  ['CZ', 'Explosia', 'munitions', 'COMPONENTS', 'взрывчатые вещества'],
  ['CZ', 'ERA', 'electronics', 'COMMUNICATIONS', 'пассивные радары'],
  ['CZ', 'RETIA', 'electronics', 'COMMUNICATIONS', 'радары и связь'],
  ['CZ', 'ELDIS Pardubice', 'electronics', null, 'радары'],
  ['CZ', 'VOP CZ', 'land', null, 'ремонт и производство техники'],
  ['CZ', 'Meopta Systems', 'electronics', 'COMPONENTS', 'оптика'],
  ['CZ', 'Sellier & Bellot', 'munitions', null, 'боеприпасы'],
  ['CZ', 'LOM Praha', 'air', null, 'ремонт и обслуживание авиации'],
  ['CZ', 'Military Research Institute', 'testing', 'TESTING & VALIDATION', 'военные исследования и испытания'],
  ['CZ', 'Military Technical Institute', 'testing', 'TESTING & VALIDATION', 'испытания и разработка'],
  ['CZ', 'První brněnská strojírna Velká Bíteš', 'propulsion', 'PROPULSION', 'малые газотурбинные двигатели'],
  ['CZ', 'Crytur', 'components', 'COMPONENTS', 'кристаллы и оптические компоненты'],
  ['CZ', 'Phonexia', 'software', 'SOFTWARE', 'распознавание речи'],
  ['CZ', 'MESIT holding', 'electronics', 'COMPONENTS', 'авионика и электроника'],
  ['CZ', 'STV Group', 'munitions', null, 'боеприпасы'],
];
// corrections where Wikidata's headquarters is outdated (kept visible in the output)
const OVERRIDE = {
  TKMS: { city: 'Kiel', lon: 10.135, lat: 54.323, note: 'Штаб-квартира в Киле по сайту компании; в Wikidata указан Эссен (головной концерн).' },
};
// Ukraine: names only, no coordinates or cities (decision 08.10.2026). Wikidata labels; selection by Claude.
const UA_PICK = [
  ['Ukrainian Defense Industry', 'land', 'государственный оборонный концерн'],
  ['Luch', 'missiles', 'конструкторское бюро, ракетные системы'],
  ['Kharkiv Morozov Machine Building Design Bureau', 'land', 'бронетехника, конструкторское бюро'],
  ['Malyshev Factory', 'land', 'бронетехника и тяжёлое машиностроение'],
  ['Zbroyar', 'smallarms', 'стрелковое оружие'],
  ['SIA FORT', 'smallarms', 'стрелковое оружие'],
  ['Mayak', 'smallarms', 'вооружение'],
  ['Kyiv Arsenal', 'electronics', 'оптика и приборы'],
  ['Neo Drones Tech', 'drones', 'беспилотные системы'],
  ['SkyCraft Ukraine', 'drones', 'FPV-дроны'],
  ['General Chereshnya', 'drones', 'дроны'],
  ['Fire Point', 'drones', 'оборонные технологии, дальние дроны'],
  ['Kation', 'electronics', 'электроника'],
  ['Radar (ex-Communist)', 'electronics', 'радиоэлектроника'],
  ['Orion Radio Works', 'electronics', 'радиосвязь'],
  ['LORTA', 'electronics', 'электроника'],
  ['Kharkiv Schevchenko Plant', 'electronics', 'электроника'],
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
