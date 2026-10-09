// Builds public/atlas/places.json: every national capital (Natural Earth "Admin-0 capital", public domain) and the 24 oblast
// centres of Ukraine (Serhii, 09.10.2026). Ukrainian names use the official Ukrainian transliteration (Kyiv, Odesa, Dnipro…).
// Kyiv is both the capital and the centre of Kyiv Oblast, so it is listed once, as the capital.
// Usage: node scripts/build-places.mjs
import fs from 'node:fs';

const NE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_populated_places_simple.geojson';
const UA_CENTRES = [ // [name, lon, lat, oblast]
  ['Vinnytsia', 28.468, 49.233, 'Vinnytsia'], ['Lutsk', 25.325, 50.747, 'Volyn'], ['Dnipro', 35.046, 48.465, 'Dnipropetrovsk'],
  ['Donetsk', 37.805, 48.016, 'Donetsk'], ['Zhytomyr', 28.658, 50.254, 'Zhytomyr'], ['Uzhhorod', 22.288, 48.621, 'Zakarpattia'],
  ['Zaporizhzhia', 35.139, 47.838, 'Zaporizhzhia'], ['Ivano-Frankivsk', 24.711, 48.922, 'Ivano-Frankivsk'], ['Kropyvnytskyi', 32.262, 48.508, 'Kirovohrad'],
  ['Luhansk', 39.307, 48.574, 'Luhansk'], ['Lviv', 24.031, 49.842, 'Lviv'], ['Mykolaiv', 31.995, 46.975, 'Mykolaiv'],
  ['Odesa', 30.733, 46.483, 'Odesa'], ['Poltava', 34.551, 49.589, 'Poltava'], ['Rivne', 26.251, 50.619, 'Rivne'],
  ['Sumy', 34.798, 50.907, 'Sumy'], ['Ternopil', 25.594, 49.553, 'Ternopil'], ['Kharkiv', 36.23, 49.994, 'Kharkiv'],
  ['Kherson', 32.617, 46.636, 'Kherson'], ['Khmelnytskyi', 26.987, 49.422, 'Khmelnytskyi'], ['Cherkasy', 32.062, 49.444, 'Cherkasy'],
  ['Chernivtsi', 25.94, 48.292, 'Chernivtsi'], ['Chernihiv', 31.289, 51.498, 'Chernihiv'],
];

const ne = await fetch(NE).then((r) => { if (!r.ok) throw new Error(`Natural Earth → ${r.status}`); return r.json(); });
const capitals = ne.features.filter((f) => f.properties.featurecla === 'Admin-0 capital').map((f) => {
  const p = f.properties;
  return { name: p.name, kind: 'capital', a3: p.adm0_a3, pop: p.pop_max, lon: +f.geometry.coordinates[0].toFixed(4), lat: +f.geometry.coordinates[1].toFixed(4) };
});
const kyiv = capitals.find((c) => c.a3 === 'UKR');
if (!kyiv || kyiv.name !== 'Kyiv') throw new Error('Kyiv must be the capital of Ukraine in the source, spelled Kyiv');
kyiv.note = 'Capital of Ukraine and centre of Kyiv Oblast';
const ua = UA_CENTRES.map(([name, lon, lat, oblast]) => ({ name, kind: 'ua-centre', a3: 'UKR', oblast: `${oblast} Oblast`, lon, lat }));
const places = [...capitals.sort((a, b) => b.pop - a.pop), ...ua];
fs.writeFileSync(new URL('../public/atlas/places.json', import.meta.url), JSON.stringify({
  generator: 'scripts/build-places.mjs', source: 'Natural Earth 1:50m populated places (public domain), Admin-0 capitals; Ukrainian oblast centres listed in the script',
  counts: { capitals: capitals.length, uaCentres: ua.length + 1 }, places,
}, null, 1) + '\n');
console.log(`places: ${capitals.length} capitals + ${ua.length} Ukrainian oblast centres (+ Kyiv as capital = 24)`);
