// Scale demo (Serhii, 09.10.2026: "if we have tens of thousands of companies, how will it look?"), opened with ?demo=scale.
// 30,000 GENERATED test points (not real companies) around real industrial cities, shown with four levels of detail:
//   world   (zoom < 3)  — countries shaded by the number of companies, one count per country
//   region  (3 … 7)     — clusters with a ring split by product category
//   city    (7 … 9.5)   — single points coloured by category, names of the larger ones
//   close   (≥ 9.5)     — the same points, larger, every name shown (no isometric objects: Serhii, 09.10.2026)
// plus category filters and a list of the companies in view. The same machinery will carry the real database.
// Rules kept even for test data: nothing in Ukraine, Russia, Belarus, China or North Korea.
const N = 30000;
// [lon, lat, country a3, weight] — defence and dual-use industrial centres
const CITIES = [
  [-77.04, 38.9, 'USA', 9], [-71.06, 42.36, 'USA', 6], [-118.24, 34.05, 'USA', 7], [-117.16, 32.72, 'USA', 5], [-122.42, 37.77, 'USA', 6], [-97.33, 32.75, 'USA', 5], [-95.37, 29.76, 'USA', 4], [-84.39, 33.75, 'USA', 4], [-86.59, 34.73, 'USA', 5], [-104.82, 38.83, 'USA', 4], [-112.07, 33.45, 'USA', 4], [-122.33, 47.61, 'USA', 5], [-83.05, 42.33, 'USA', 3], [-76.29, 36.85, 'USA', 4], [-80.19, 25.76, 'USA', 2], [-87.63, 41.88, 'USA', 3], [-74.0, 40.71, 'USA', 4], [-90.2, 38.63, 'USA', 3], [-81.38, 28.54, 'USA', 4], [-72.92, 41.31, 'USA', 3], [-106.65, 35.08, 'USA', 2], [-111.89, 40.76, 'USA', 2],
  [-75.7, 45.42, 'CAN', 4], [-79.38, 43.65, 'CAN', 4], [-73.57, 45.5, 'CAN', 4], [-123.12, 49.28, 'CAN', 2], [-63.57, 44.65, 'CAN', 2],
  [-0.13, 51.51, 'GBR', 6], [-2.59, 51.45, 'GBR', 4], [-2.24, 53.48, 'GBR', 3], [-1.9, 52.49, 'GBR', 3], [-4.25, 55.86, 'GBR', 3], [-3.19, 55.95, 'GBR', 2], [-1.4, 50.9, 'GBR', 2], [-2.7, 53.76, 'GBR', 3],
  [2.35, 48.86, 'FRA', 6], [1.44, 43.6, 'FRA', 4], [5.37, 43.3, 'FRA', 3], [-1.55, 47.22, 'FRA', 2], [-4.49, 48.39, 'FRA', 2], [4.84, 45.76, 'FRA', 3], [-0.58, 44.84, 'FRA', 3], [7.27, 43.7, 'FRA', 2],
  [13.4, 52.52, 'DEU', 5], [11.58, 48.14, 'DEU', 5], [9.18, 48.78, 'DEU', 4], [6.78, 51.23, 'DEU', 4], [10.0, 53.55, 'DEU', 3], [8.8, 53.08, 'DEU', 3], [10.13, 54.32, 'DEU', 2], [9.99, 48.4, 'DEU', 2], [10.9, 48.37, 'DEU', 2], [8.68, 50.11, 'DEU', 3], [9.73, 52.37, 'DEU', 2], [12.37, 51.34, 'DEU', 2],
  [12.5, 41.9, 'ITA', 4], [9.19, 45.46, 'ITA', 4], [7.69, 45.07, 'ITA', 3], [8.93, 44.41, 'ITA', 3], [14.27, 40.85, 'ITA', 2], [10.33, 43.55, 'ITA', 2], [11.34, 44.49, 'ITA', 2],
  [-3.7, 40.42, 'ESP', 4], [2.17, 41.39, 'ESP', 3], [-5.98, 37.39, 'ESP', 2], [-0.38, 39.47, 'ESP', 2], [-2.93, 43.26, 'ESP', 2],
  [-9.14, 38.72, 'PRT', 2], [-8.61, 41.15, 'PRT', 1],
  [4.9, 52.37, 'NLD', 3], [4.48, 51.92, 'NLD', 2], [5.48, 51.44, 'NLD', 2], [4.35, 50.85, 'BEL', 3], [5.57, 50.63, 'BEL', 2],
  [18.07, 59.33, 'SWE', 3], [11.97, 57.71, 'SWE', 2], [15.62, 58.41, 'SWE', 2], [14.16, 57.78, 'SWE', 1], [10.75, 59.91, 'NOR', 3], [9.66, 59.67, 'NOR', 2], [12.57, 55.68, 'DNK', 3], [24.94, 60.17, 'FIN', 3], [23.76, 61.5, 'FIN', 2],
  [24.11, 56.95, 'LVA', 1], [24.75, 59.44, 'EST', 2], [25.28, 54.69, 'LTU', 1],
  [21.01, 52.23, 'POL', 4], [19.94, 50.06, 'POL', 3], [22.0, 50.04, 'POL', 2], [18.65, 54.35, 'POL', 2], [19.02, 50.26, 'POL', 3], [16.93, 52.41, 'POL', 2], [17.04, 51.11, 'POL', 2], [22.57, 51.25, 'POL', 1],
  [14.42, 50.09, 'CZE', 3], [16.61, 49.2, 'CZE', 3], [17.25, 49.59, 'CZE', 2], [18.26, 49.82, 'CZE', 2], [17.11, 48.15, 'SVK', 2], [19.04, 47.5, 'HUN', 2], [16.37, 48.21, 'AUT', 3], [8.54, 47.38, 'CHE', 3], [7.45, 46.95, 'CHE', 2],
  [26.1, 44.43, 'ROU', 2], [23.6, 46.77, 'ROU', 1], [23.32, 42.7, 'BGR', 2], [23.73, 37.98, 'GRC', 2], [15.98, 45.81, 'HRV', 1], [14.51, 46.06, 'SVN', 1],
  [28.98, 41.01, 'TUR', 4], [32.85, 39.93, 'TUR', 4], [27.14, 38.42, 'TUR', 1],
  [34.78, 32.08, 'ISR', 4], [35.0, 32.79, 'ISR', 2], [54.37, 24.45, 'ARE', 2], [55.27, 25.2, 'ARE', 2], [46.68, 24.71, 'SAU', 2],
  [77.21, 28.61, 'IND', 4], [77.59, 12.97, 'IND', 5], [78.49, 17.39, 'IND', 4], [72.88, 19.08, 'IND', 3], [80.27, 13.08, 'IND', 3], [73.86, 18.52, 'IND', 3],
  [139.69, 35.69, 'JPN', 5], [135.5, 34.69, 'JPN', 3], [136.91, 35.18, 'JPN', 3], [130.4, 33.59, 'JPN', 2], [126.98, 37.57, 'KOR', 5], [129.08, 35.18, 'KOR', 3], [127.38, 36.35, 'KOR', 3], [128.6, 35.87, 'KOR', 2], [121.56, 25.03, 'TWN', 3], [120.68, 24.15, 'TWN', 2],
  [103.82, 1.35, 'SGP', 3], [151.21, -33.87, 'AUS', 4], [138.6, -34.93, 'AUS', 3], [144.96, -37.81, 'AUS', 3], [153.03, -27.47, 'AUS', 2], [115.86, -31.95, 'AUS', 2], [149.13, -35.28, 'AUS', 2], [174.76, -36.85, 'NZL', 1],
  [-46.63, -23.55, 'BRA', 2], [-45.89, -23.18, 'BRA', 2], [18.42, -33.92, 'ZAF', 1], [28.19, -25.75, 'ZAF', 2],
];
const TYPES = [['factory', 0.58], ['office', 0.2], ['lab', 0.12], ['warehouse', 0.1]]; // shown in the card
const SIZE = [['S', 0.62], ['M', 0.28], ['L', 0.1]];

function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const pick = (r, list) => { let x = r(); for (const [v, w] of list) { if ((x -= w) < 0) return v; } return list.at(-1)[0]; };
function inRing([x, y], ring) { let ins = false; for (let i = 0, k = ring.length - 1; i < ring.length; k = i++) { const [xi, yi] = ring[i], [xk, yk] = ring[k]; if ((yi > y) !== (yk > y) && x < ((xk - xi) * (y - yi)) / (yk - yi) + xi) ins = !ins; } return ins; }
const polysOf = (g) => (g.type === 'Polygon' ? [g.coordinates] : g.coordinates);
const inCountry = (p, g) => polysOf(g).some((poly) => inRing(p, poly[0]) && !poly.slice(1).some((h) => inRing(p, h)));
function labelPoint(g) { // centroid of the largest outer ring
  let best = null, bestA = 0;
  for (const poly of polysOf(g)) { const r = poly[0]; let a = 0, cx = 0, cy = 0; for (let i = 0, k = r.length - 1; i < r.length; k = i++) { const f = r[k][0] * r[i][1] - r[i][0] * r[k][1]; a += f; cx += (r[k][0] + r[i][0]) * f; cy += (r[k][1] + r[i][1]) * f; } if (Math.abs(a) > bestA) { bestA = Math.abs(a); best = [cx / (3 * a), cy / (3 * a)]; } }
  return best;
}

export function generate(countries, categories) {
  const r = rng(20261009), byA3 = Object.fromEntries(countries.features.map((f) => [f.properties.a3, f]));
  const total = CITIES.reduce((s, c) => s + c[3], 0), cats = Object.keys(categories);
  const out = [];
  for (let i = 0; i < N; i++) {
    let x = r() * total, city = CITIES[0];
    for (const c of CITIES) { if ((x -= c[3]) < 0) { city = c; break; } }
    const [lon0, lat0, a3, w] = city, g = byA3[a3]?.geometry, spread = 0.25 + 0.12 * w;
    let p = [lon0, lat0];
    for (let t = 0; t < 8; t++) { // gaussian scatter, kept on the city's own country
      const rr = spread * Math.sqrt(-2 * Math.log(r() || 1e-9)) * 0.6, a = r() * Math.PI * 2;
      const q = [lon0 + (rr * Math.cos(a)) / Math.cos((lat0 * Math.PI) / 180), lat0 + rr * Math.sin(a)];
      if (!g || inCountry(q, g)) { p = q; break; }
    }
    out.push({ id: i, name: `Demo company ${String(i + 1).padStart(5, '0')}`, a3, cat: cats[Math.floor(r() * cats.length)], type: pick(r, TYPES), size: pick(r, SIZE), lon: +p[0].toFixed(4), lat: +p[1].toFixed(4) });
  }
  return out;
}

// options for real data: data (records with id, name, a3, cat, lon, lat …), prefix (ids of sources and layers), title and note
// for the panel, card(record) for the click card, subtitle(record) for the list, namesFrom (zoom from which every name shows)
export function createScaleDemo(map, { maplibregl, countries, categories, onCard, $, data, prefix = 'scale', title, note, card: cardFn, subtitle, namesFrom = 9.5 }) {
  const P = prefix;
  const all = data ?? generate(countries, categories);
  const byId = new Map(all.map((c) => [String(c.id), c]));
  const shown = new Set(Object.keys(categories));
  const catKeys = Object.keys(categories);
  const props = (c) => ({ id: c.id, name: c.name, cat: c.cat, color: categories[c.cat][1], big: c.size === 'L', s: c.size === 'L' ? 1 : c.size === 'M' ? 0.8 : 0.65 });
  const fc = (list) => ({ type: 'FeatureCollection', features: list.map((c) => ({ type: 'Feature', properties: props(c), geometry: { type: 'Point', coordinates: [c.lon, c.lat] } })) });

  // world level: countries shaded by count, a count bubble per country
  const countryFc = () => {
    const n = {}; for (const c of all) if (shown.has(c.cat)) n[c.a3] = (n[c.a3] ?? 0) + 1;
    const polys = countries.features.filter((f) => n[f.properties.a3]).map((f) => ({ ...f, properties: { ...f.properties, n: n[f.properties.a3] } }));
    const pts = polys.map((f) => ({ type: 'Feature', properties: { name: f.properties.name, n: f.properties.n }, geometry: { type: 'Point', coordinates: labelPoint(f.geometry) } }));
    return [{ type: 'FeatureCollection', features: polys }, { type: 'FeatureCollection', features: pts }];
  };
  const [cPoly, cPts] = countryFc();
  map.addSource(`${P}-countries`, { type: 'geojson', data: cPoly });
  map.addSource(`${P}-country-pts`, { type: 'geojson', data: cPts });
  map.addSource(`${P}`, { type: 'geojson', data: fc(all), cluster: true, clusterRadius: 52, clusterMaxZoom: 7, clusterProperties: Object.fromEntries(catKeys.map((k) => [k, ['+', ['case', ['==', ['get', 'cat'], k], 1, 0]]])) });
  const before = 'label-city';
  map.addLayer({ id: `${P}-country-fill`, type: 'fill', source: `${P}-countries`, maxzoom: 3.4, paint: { 'fill-color': ['interpolate', ['linear'], ['get', 'n'], 1, '#efe2a8', 300, '#d9b24c', 1500, '#a8741f', 6000, '#5c3d0c'], 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 2.6, 0.62, 3.4, 0] } }, before);
  map.addLayer({ id: `${P}-country-dot`, type: 'circle', source: `${P}-country-pts`, maxzoom: 3, paint: { 'circle-color': '#11150e', 'circle-opacity': 0.88, 'circle-stroke-color': '#d3b766', 'circle-stroke-width': 1.5, 'circle-radius': ['interpolate', ['linear'], ['get', 'n'], 10, 9, 500, 14, 5000, 24] } });
  map.addLayer({ id: `${P}-country-n`, type: 'symbol', source: `${P}-country-pts`, maxzoom: 3, layout: { 'text-field': ['number-format', ['get', 'n'], { locale: 'en' }], 'text-font': ['Noto Sans Bold'], 'text-size': 11, 'text-allow-overlap': true }, paint: { 'text-color': '#ffecaa' } });
  // region level: clusters (the ring by category is drawn as HTML markers below)
  map.addLayer({ id: `${P}-cluster`, type: 'circle', source: `${P}`, minzoom: 3, filter: ['has', 'point_count'], paint: { 'circle-opacity': 0, 'circle-radius': ['step', ['get', 'point_count'], 16, 100, 22, 1000, 30] } });
  // city and close levels: single points; names of the larger ones first, then all
  map.addLayer({ id: `${P}-point`, type: 'circle', source: `${P}`, minzoom: 3, filter: ['!', ['has', 'point_count']], paint: { 'circle-color': ['get', 'color'], 'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, ['case', ['get', 'big'], 5, 3.5], 9, ['case', ['get', 'big'], 8, 5.5], 13, ['case', ['get', 'big'], 11, 8]], 'circle-stroke-color': '#fff6cf', 'circle-stroke-width': 1.2 } });
  map.addLayer({ id: `${P}-name`, type: 'symbol', source: `${P}`, minzoom: 7.5, filter: ['all', ['!', ['has', 'point_count']], ['any', ['get', 'big'], ['>=', ['zoom'], namesFrom]]], layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Bold'], 'text-size': 10, 'text-offset': [0, 1], 'text-anchor': 'top', 'text-optional': true }, paint: { 'text-color': '#22201a', 'text-halo-color': '#f7efd2', 'text-halo-width': 1.4 } });

  // ring markers for clusters: one SVG donut per cluster on screen, cached by cluster id
  const markers = new Map(); let onScreen = new Map();
  function donut(p) {
    const total = p.point_count, r = total >= 1000 ? 30 : total >= 100 ? 23 : 17, r0 = r - 6, w = r * 2;
    let a0 = -Math.PI / 2, segs = '';
    for (const k of catKeys) {
      const v = p[k]; if (!v) continue;
      const a1 = a0 + (v / total) * Math.PI * 2, large = a1 - a0 > Math.PI ? 1 : 0;
      const pt = (rr, a) => `${(r + rr * Math.cos(a)).toFixed(2)} ${(r + rr * Math.sin(a)).toFixed(2)}`;
      segs += v === total ? `<circle cx="${r}" cy="${r}" r="${r - 3}" fill="none" stroke="${categories[k][1]}" stroke-width="6"/>` : `<path d="M ${pt(r, a0)} A ${r} ${r} 0 ${large} 1 ${pt(r, a1)} L ${pt(r0, a1)} A ${r0} ${r0} 0 ${large} 0 ${pt(r0, a0)} Z" fill="${categories[k][1]}"/>`;
      a0 = a1;
    }
    const d = document.createElement('div');
    d.className = 'scale-cluster'; // CSS class
    d.innerHTML = `<svg width="${w}" height="${w}" viewBox="0 0 ${w} ${w}">${segs}<circle cx="${r}" cy="${r}" r="${r0}" fill="#11150e" fill-opacity="0.9"/><text x="${r}" y="${r}" dy="0.35em" text-anchor="middle" fill="#ffecaa" font-family="ui-monospace, monospace" font-weight="800" font-size="${total >= 1000 ? 11 : 10}">${p.point_count_abbreviated}</text></svg>`;
    return d;
  }
  function syncDonuts() {
    const next = new Map();
    if (state.on && map.getZoom() >= 3) for (const f of map.querySourceFeatures(`${P}`, { filter: ['has', 'point_count'] })) {
      const id = f.properties.cluster_id; if (next.has(id)) continue;
      let m = markers.get(id);
      if (!m) { const elm = donut(f.properties); m = new maplibregl.Marker({ element: elm }).setLngLat(f.geometry.coordinates); elm.addEventListener('click', async (e) => { e.stopPropagation(); const z = await map.getSource(`${P}`).getClusterExpansionZoom(id); map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.3 }); }); markers.set(id, m); }
      next.set(id, m);
      if (!onScreen.has(id)) m.addTo(map);
    }
    for (const [id, m] of onScreen) if (!next.has(id)) m.remove();
    onScreen = next;
  }
  map.on('render', () => { if (map.isSourceLoaded(`${P}`)) syncDonuts(); });

  // the list of companies in view (largest first) and the counter
  function syncList() {
    if (!state.on) return;
    const b = map.getBounds();
    const inView = all.filter((c) => shown.has(c.cat) && b.contains([c.lon, c.lat]));
    const order = { L: 0, M: 1, S: 2 };
    inView.sort((a, c) => (order[a.size] ?? 3) - (order[c.size] ?? 3) || String(a.name).localeCompare(c.name));
    $('#scale-count').textContent = `${inView.length.toLocaleString('en')} companies in view`;
    const ul = $('#scale-list'); ul.replaceChildren();
    for (const c of inView.slice(0, 40)) {
      const li = document.createElement('li'), btn = document.createElement('button'); btn.type = 'button';
      btn.innerHTML = `<i style="background:${categories[c.cat][1]}"></i><b></b><small></small>`;
      btn.querySelector('b').textContent = c.name; btn.querySelector('small').textContent = subtitle ? subtitle(c) : `${categories[c.cat][0]} · ${c.a3} · ${c.size}`;
      btn.addEventListener('click', () => { map.flyTo({ center: [c.lon, c.lat], zoom: 11, pitch: 45 }); card(c); });
      li.append(btn); ul.append(li);
    }
  }
  map.on('moveend', syncList);
  const card = (c) => cardFn ? onCard(cardFn(c)) : onCard({ head: 'SCALE DEMO · GENERATED POINT', title: c.name, rows: [['Category', categories[c.cat][0]], ['Type', c.type], ['Size', c.size], ['Country', c.a3]], note: 'Test data generated for the scale demo. Not a real company.' });
  map.on('click', `${P}-point`, (e) => card(byId.get(String(e.features[0].properties.id))));
    map.on('click', `${P}-country-dot`, (e) => map.flyTo({ center: e.features[0].geometry.coordinates, zoom: 4.6 }));
  for (const id of [`${P}-point`, `${P}-country-dot`]) { map.on('mouseenter', id, () => (map.getCanvas().style.cursor = 'pointer')); map.on('mouseleave', id, () => (map.getCanvas().style.cursor = '')); }

  // category filters
  function applyFilter() {
    const list = all.filter((c) => shown.has(c.cat));
    map.getSource(`${P}`).setData(fc(list));
    const [p, q] = countryFc(); map.getSource(`${P}-countries`).setData(p); map.getSource(`${P}-country-pts`).setData(q);
    for (const m of markers.values()) m.remove(); markers.clear(); onScreen = new Map();
    syncList();
  }
  const LAYERS = [`${P}-country-fill`, `${P}-country-dot`, `${P}-country-n`, `${P}-cluster`, `${P}-point`, `${P}-name`];
  const state = { on: false };
  function setOn(on) {
    state.on = on;
    for (const id of LAYERS) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    $('#scale-panel').hidden = !on;
    if (on) { $('#scale-filters').replaceChildren(filterBox); $('#scale-title').textContent = title ?? 'SCALE DEMO · 30,000 GENERATED POINTS'; $('#scale-note').textContent = note ?? 'Test data, not real companies. Zoom in: countries → clusters → points.'; }
    if (!on) { for (const m of onScreen.values()) m.remove(); onScreen = new Map(); } else syncList();
  }
  const filterBox = document.createElement('div');
  for (const k of catKeys.filter((k) => all.some((c) => c.cat === k))) {
    const lab = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true;
    cb.addEventListener('change', () => { if (cb.checked) shown.add(k); else shown.delete(k); applyFilter(); });
    const dot = document.createElement('i'); dot.style.background = categories[k][1];
    lab.append(cb, dot, document.createTextNode(categories[k][0])); filterBox.append(lab);
  }
  return { all, setOn, state, prefix: P };
}
