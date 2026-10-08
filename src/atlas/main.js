// /atlas.html — probe A (Notion «Перезапуск», approved 08.10.2026): a ready-made world map (MapLibre + OpenFreeMap) with
// ready-made 3D models (Kenney, CC0) drawn by deck.gl, our own Natural Earth borders on top, the intent question at the entry
// and two journeys (SELL, BUY). Movement rules are the tested flows core (src/flows/core.js), unchanged.
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MapLibreOverlay } from '@deck.gl/maplibre';
import { ScenegraphLayer } from '@deck.gl/mesh-layers';
import { IconLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import { PathStyleExtension } from '@deck.gl/extensions';
import { createFlows, MODES, pointAt } from '../flows/core.js';
import frame from '../flows/data/world-frame.json' with { type: 'json' };
import assets from '../game/data/assets.json' with { type: 'json' };
import { createJourneys, atlasData as data, CATEGORIES, MARKETS, HOME, CANDIDATES, INSIGHTS, OFFERS, SELL_STEPS, BUY_STEPS } from './journeys.js';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const params = new URLSearchParams(location.search);
const PROJECTION = params.get('projection') === 'mercator' ? 'mercator' : 'globe';

// flows world px <-> lon/lat (the flows frame is equirectangular, so this is exact)
const toLL = (x, y) => [x / frame.pxPerDeg - 180, frame.latNorth - y / (frame.pxPerDeg * frame.squash)];
const llOf = (p) => toLL(p.x, p.y);
const heading = (p) => { const [a0, b0] = toLL(p.x, p.y), [a1, b1] = toLL(p.x + p.ax, p.y + p.ay); return (Math.atan2((a1 - a0) * Math.cos((b0 * Math.PI) / 180), b1 - b0) * 180) / Math.PI; };

// ---------- models (Kenney CC0; see public/atlas/models/PROVENANCE.json) ----------
const M = (kit, name) => `/atlas/models/${kit}/${name}.glb`;
const NODE_MODEL = {
  mine: M('city-kit-industrial', 'building-n'), 'factory-s': M('city-kit-industrial', 'building-c'), 'factory-m': M('city-kit-industrial', 'building-e'),
  'factory-l': M('city-kit-industrial', 'building-m'), bank: M('city-kit-commercial', 'building-e'), port: M('city-kit-industrial', 'building-s'),
  station: M('city-kit-industrial', 'building-h'), airfield: M('city-kit-industrial', 'building-i'), actor: M('city-kit-commercial', 'building-b'),
};
const CAND_MODEL = M('city-kit-industrial', 'building-g');
const MOVER_MODEL = { truck: M('car-kit', 'truck'), wagon: M('car-kit', 'delivery-flat'), train: M('train-kit', 'train-diesel-a'), ship: M('watercraft-kit', 'ship-cargo-a') };
const MODEL_YAW = 180;
const SHOW_VEHICLES = params.get('vehicles') === '1'; // Kenney vehicles face -Z; turn them to face the direction of travel

const svg = (body, w = 64, h = 64) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`)}`;
const ICON = {
  expert: { url: svg('<circle cx="32" cy="32" r="29" fill="#11150e" stroke="#d3b766" stroke-width="4"/><circle cx="32" cy="24" r="9" fill="#efe9cc"/><path d="M15 50c3-11 10-15 17-15s14 4 17 15" fill="#efe9cc"/>'), width: 64, height: 64, anchorY: 32 },
  plane: { url: svg('<path d="M32 4l5 20 19 9v6l-19-4-2 14 7 6v4l-10-3-10 3v-4l7-6-2-14-19 4v-6l19-9z" fill="#ffffff" stroke="#11150e" stroke-width="2"/>'), width: 64, height: 64, anchorY: 32, mask: false },
  rider: { url: assets.rider.url, width: assets.rider.size[0], height: assets.rider.size[1], anchorY: assets.rider.anchor.y, anchorX: assets.rider.anchor.x },
};

// ---------- state ----------
let game, S, j;
const coins = [];
let ui = { journey: null, cardId: null, sellCat: null, sellMarket: null, buyCat: null };
game = createFlows({ data, seed: 7, bohunStart: data.BOHUN_START, onEvent });
S = game.state;
j = createJourneys(game, { onChange: onJourney });
window.__flows = game; window.__j = j; // test hooks
const DEFAULT_FLOWS = [['fac-kharkiv', 'port-odesa', 'cheap'], ['fac-krakow', 'bank-kyiv', 'cheap']];
const defaultIds = new Set();
function seedDefaults() { defaultIds.clear(); for (const [a, b, o] of DEFAULT_FLOWS) { const r = game.startFlow(a, b, o); if (r.ok) defaultIds.add(r.flowId); } }
// a journey always gets a slot: the oldest background flow gives way
function freeSlot() { if (S.flows.length >= 3) { const d = S.flows.find((f) => defaultIds.has(f.id)); if (d) game.stopFlow(d.id); } }

function onEvent(type, p) {
  if (!S) return;
  j?.onEvent(type, p);
  if (type === 'cargoArrived' && p.bank) { const n = S.world.nodes[p.toId]; for (let i = 0; i < 6; i++) coins.push({ ll: [n.lon, n.lat], t0: performance.now() + i * 90, kind: i % 2 }); }
  if (type === 'rejected' && p.reason === 'busy') note('Богун ещё в пути. Подождите, пока он доедет.');
  if (type === 'reset') setTimeout(seedDefaults, 0);
}
function onJourney(type, p) {
  if (type === 'sell:done' || type === 'buy:done') setTimeout(() => game.stopFlow(p), 0);
  if (type === 'sell:3') note('Разведка готова: на рынке видны участники. Теперь эксперты.');
  if (type === 'sell:expert') note('Вывод эксперта: ' + INSIGHTS[j.experts().indexOf(p)]);
  if (type === 'sell:offers') note('Богун нашёл три возможности. Выберите одну.');
  if (type === 'buy:3') note('Найдено пять кандидатов. Отправьте Богуна проверить их.');
  if (type === 'buy:verdict') note(CANDIDATES[p].verdict);
  if (type === 'buy:meeting') note('Встреча с поставщиком состоялась. Богун едет к вам.');
  if (type === 'buy:5') freeSlot();
  frameFor();
  render();
}
let noteTimer;
function note(text) { const el = $('#note'); el.textContent = text; clearTimeout(noteTimer); noteTimer = setTimeout(() => (el.textContent = ''), 5000); }
const short = (place) => place.replace(' (тест)', '');
const modesText = (modes) => modes.map((m) => MODES[m].label).join(' + ');

// ---------- labels ----------
const LABELS = [
  ['Киев', 30.52, 50.45], ['Львов', 24.03, 49.84], ['Одесса', 30.73, 46.48], ['Харьков', 36.23, 49.99], ['Днепр', 35.05, 48.46], ['Запорожье', 35.14, 47.84],
  ['Варшава', 21.01, 52.23], ['Краков', 19.94, 50.06], ['Берлин', 13.4, 52.52], ['Франкфурт', 8.68, 50.11], ['Роттердам', 4.48, 51.92], ['Бухарест', 26.1, 44.43],
  ['Стамбул', 28.98, 41.01], ['Нью-Йорк', -74.0, 40.71], ['Шанхай', 121.47, 31.23], ['Сингапур', 103.82, 1.35],
];
const REGIONS = [['УКРАИНА', 31.5, 49.3, 18], ['ЧЁРНОЕ МОРЕ', 34.5, 43.2, 13], ['ЕВРОПЕЙСКИЙ СОЮЗ', 12.0, 47.8, 12]];

// ---------- map ----------
// map looks to compare (?look=...): all use free OpenFreeMap styles; relief adds a public elevation dataset (AWS Terrain Tiles)
export const LOOKS = {
  relief: { label: 'Рельеф (сейчас)', base: 'liberty' },
  hills: { label: 'Рельеф + тени гор', base: 'liberty', hillshade: true },
  parchment: { label: 'Старая карта', base: 'liberty', parchment: true, hillshade: true },
  dark: { label: 'Тёмная', base: 'dark' },
  light: { label: 'Светлая минимальная', base: 'positron' },
};
const LOOK = LOOKS[params.get('look')] ? params.get('look') : 'relief';
async function loadStyle() {
  const L0 = LOOKS[LOOK];
  const style = await fetch('https://tiles.openfreemap.org/styles/' + L0.base).then((r) => r.json());
  // the basemap's own borders and names are dropped: borders come from Natural Earth (Crimea in Ukraine), names are ours
  style.layers = style.layers.filter((l) => l['source-layer'] !== 'boundary' && l.type !== 'symbol');
  for (const l of style.layers) {
    if (L0.base !== 'liberty') break;
    if (l.id === 'background') l.paint = { 'background-color': L0.parchment ? '#e8d9b0' : '#d9d2b4' };
    if (l.id === 'water') l.paint = { ...l.paint, 'fill-color': L0.parchment ? '#a9bdb3' : '#86aab0' };
    if (l.id === 'natural_earth') l.paint = { ...l.paint, 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 0, 0.85, 7, 0.35], ...(L0.parchment ? { 'raster-saturation': -0.55, 'raster-contrast': 0.1 } : {}) };
    if (L0.parchment && l.type === 'fill' && /landcover|park|landuse/.test(l.id)) l.paint = { ...l.paint, 'fill-color': '#cfc290', 'fill-opacity': 0.35 };
    if (L0.parchment && l.type === 'line' && l['source-layer'] === 'transportation') l.minzoom = Math.max(l.minzoom ?? 0, 7);
  }
  if (L0.hillshade) {
    style.sources.dem = { type: 'raster-dem', tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, maxzoom: 12, attribution: 'Terrain Tiles: Mapzen, AWS Open Data' };
    const at = style.layers.findIndex((l) => l.id === 'water');
    style.layers.splice(at + 1, 0, { id: 'hillshade', type: 'hillshade', source: 'dem', paint: { 'hillshade-exaggeration': L0.parchment ? 0.45 : 0.6, 'hillshade-shadow-color': L0.parchment ? '#6b5a3a' : '#4a4a3a', 'hillshade-highlight-color': '#fff8e0', 'hillshade-accent-color': '#5a4a30' } });
  }
  style.sources.countries = { type: 'geojson', data: '/atlas/countries.geojson' };
  const pt = (t, lon, lat, kind, size) => ({ type: 'Feature', properties: { t, kind, size }, geometry: { type: 'Point', coordinates: [lon, lat] } });
  style.sources.labels = { type: 'geojson', data: { type: 'FeatureCollection', features: [...LABELS.map(([t, lon, lat]) => pt(t, lon, lat, 'city', 12)), ...REGIONS.map(([t, lon, lat, size]) => pt(t, lon, lat, 'region', size))] } };
  style.layers.push(
    { id: 'eu-tint', type: 'fill', source: 'countries', filter: ['==', ['get', 'cls'], 'eu'], paint: { 'fill-color': '#5f7f3a', 'fill-opacity': 0.08 } },
    { id: 'ua-tint', type: 'fill', source: 'countries', filter: ['==', ['get', 'a3'], 'UKR'], paint: { 'fill-color': '#e2c667', 'fill-opacity': 0.14 } },
    { id: 'borders', type: 'line', source: 'countries', paint: { 'line-color': '#6b6650', 'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.4, 6, 1.1], 'line-opacity': 0.7, 'line-dasharray': [3, 2] } },
    { id: 'ua-glow', type: 'line', source: 'countries', filter: ['==', ['get', 'a3'], 'UKR'], paint: { 'line-color': '#fff6cf', 'line-width': 6, 'line-opacity': 0.45, 'line-blur': 3 } },
    { id: 'ua-border', type: 'line', source: 'countries', filter: ['==', ['get', 'a3'], 'UKR'], paint: { 'line-color': '#b8932e', 'line-width': 2 } },
    { id: 'label-region', type: 'symbol', source: 'labels', filter: ['==', ['get', 'kind'], 'region'], layout: { 'text-field': ['get', 't'], 'text-font': ['Noto Sans Bold'], 'text-size': ['get', 'size'], 'text-letter-spacing': 0.4, 'text-allow-overlap': true }, paint: { 'text-color': '#5b5334', 'text-opacity': 0.7, 'text-halo-color': '#efe6c4', 'text-halo-width': 1.2 } },
    { id: 'label-city', type: 'symbol', source: 'labels', filter: ['==', ['get', 'kind'], 'city'], minzoom: 3, layout: { 'text-field': ['get', 't'], 'text-font': ['Noto Sans Bold'], 'text-size': 12, 'text-offset': [0, 1.4], 'text-anchor': 'top' }, paint: { 'text-color': '#2a281c', 'text-halo-color': '#f4ecd0', 'text-halo-width': 1.6 } },
  );
  return style;
}
maplibregl.setWorkerUrl('/atlas/vendor/maplibre-gl-worker.mjs'); // verbatim copy, see scripts/copy-maplibre-worker.mjs
const map = new maplibregl.Map({
  container: 'map', style: await loadStyle(), center: [31, 48.4], zoom: 4.6, pitch: 45, bearing: -8, maxPitch: 70,
  attributionControl: { compact: true }, canvasContextAttributes: { antialias: true },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
window.__map = map;
await new Promise((r) => map.once('load', r));
if (PROJECTION === 'globe') map.setProjection({ type: 'globe' });

// ---------- industry layer: real companies from open data (Notion task 08.10.2026) ----------
const PRODUCT = {
  land: ['Наземная техника', '#7a6a48'], air: ['Авиация', '#4f7fa8'], naval: ['Флот и морские системы', '#2f6f95'], munitions: ['Боеприпасы и ВВ', '#9a4a32'],
  missiles: ['Ракеты и ПВО', '#b8562f'], electronics: ['Электроника, радары, связь', '#5b6fb0'], drones: ['Беспилотники', '#3f8f6a'], smallarms: ['Стрелковое оружие', '#6b5a45'],
  software: ['Программное обеспечение', '#7d5aa6'], propulsion: ['Двигатели и приводы', '#c08a2a'], components: ['Компоненты и материалы', '#8a8a5a'], testing: ['Испытания и исследования', '#4a8a8a'],
};
const industry = await fetch('/atlas/industry.json').then((r) => r.json());
// several companies share one city centre: spread them on a small ring so each stays clickable (the point is the city, not an address)
const byCity = {};
for (const c of industry.companies) (byCity[c.lon + ',' + c.lat] ??= []).push(c);
const indFeatures = [];
for (const list of Object.values(byCity)) list.forEach((c, i) => {
  const r = list.length > 1 ? 0.06 + 0.012 * list.length : 0, a = (i / list.length) * Math.PI * 2;
  indFeatures.push({ type: 'Feature', properties: { id: c.id, name: c.name, product: c.product, color: PRODUCT[c.product][1] }, geometry: { type: 'Point', coordinates: [c.lon + r * Math.cos(a), c.lat + r * Math.sin(a) * 0.65] } });
});
map.addSource('industry', { type: 'geojson', data: { type: 'FeatureCollection', features: indFeatures }, cluster: true, clusterRadius: 38, clusterMaxZoom: 6 });
map.addLayer({ id: 'ind-cluster', type: 'circle', source: 'industry', filter: ['has', 'point_count'], paint: { 'circle-color': '#11150e', 'circle-opacity': 0.88, 'circle-stroke-color': '#d3b766', 'circle-stroke-width': 2, 'circle-radius': ['step', ['get', 'point_count'], 13, 5, 17, 12, 22] } });
map.addLayer({ id: 'ind-count', type: 'symbol', source: 'industry', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Noto Sans Bold'], 'text-size': 12, 'text-allow-overlap': true }, paint: { 'text-color': '#ffecaa' } });
map.addLayer({ id: 'ind-point', type: 'circle', source: 'industry', filter: ['!', ['has', 'point_count']], paint: { 'circle-color': ['get', 'color'], 'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 5, 9, 8], 'circle-stroke-color': '#fff6cf', 'circle-stroke-width': 1.5 } });
map.addLayer({ id: 'ind-name', type: 'symbol', source: 'industry', filter: ['!', ['has', 'point_count']], minzoom: 6.5, layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Bold'], 'text-size': 11, 'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-optional': true }, paint: { 'text-color': '#22201a', 'text-halo-color': '#f7efd2', 'text-halo-width': 1.5 } });
const uaBadge = el('div', 'mk ua', `УКРАИНА: ${industry.ukraine.length} КОМПАНИЙ · БЕЗ МЕСТ`);
const uaMarker = new maplibregl.Marker({ element: uaBadge, anchor: 'center' }).setLngLat([31.5, 48.2]);
uaBadge.addEventListener('click', (e) => { e.stopPropagation(); showIndustryCard(null); });
let industryOn = true;
function setIndustry(on) {
  industryOn = on;
  for (const id of ['ind-cluster', 'ind-count', 'ind-point', 'ind-name']) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  if (on) uaMarker.addTo(map); else uaMarker.remove();
  $('#industry').classList.toggle('active', on); $('#legend').hidden = !on;
}
map.on('click', 'ind-cluster', async (e) => {
  const f = e.features[0], z = await map.getSource('industry').getClusterExpansionZoom(f.properties.cluster_id);
  map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.3, duration: reduced ? 0 : 700 });
});
map.on('click', 'ind-point', (e) => showIndustryCard(e.features[0].properties.id));
for (const id of ['ind-cluster', 'ind-point']) { map.on('mouseenter', id, () => (map.getCanvas().style.cursor = 'pointer')); map.on('mouseleave', id, () => (map.getCanvas().style.cursor = '')); }
const COUNTRY_RU = { DE: 'Германия', FR: 'Франция', IT: 'Италия', PL: 'Польша', CZ: 'Чехия', UA: 'Украина' };
function showIndustryCard(id) {
  const box = $('#ind-card'); box.hidden = false;
  box.querySelector('.panel-head span').textContent = 'ОТРАСЛЬ · ОТКРЫТЫЕ ДАННЫЕ';
  const body = $('#ind-body'); body.replaceChildren();
  if (!id) {
    $('#ind-title').textContent = 'Украина';
    body.append(el('p', 'muted', 'Места на карте не показываем. Список — по открытым данным (Wikidata), будет дополнен.'));
    const ul = el('ul', 'ind-list');
    for (const c of industry.ukraine) { const li = el('li'); const a = el('a', null, c.name); a.href = c.source; a.target = '_blank'; a.rel = 'noopener'; li.append(a, document.createTextNode(` — ${c.about}`)); ul.append(li); }
    body.append(ul);
  } else {
    const c = industry.companies.find((x) => x.id === id);
    $('#ind-title').textContent = c.name;
    body.append(el('p', 'muted', `${COUNTRY_RU[c.country]} · ${c.city ?? 'город не указан'} (точка — город, не адрес)`));
    body.append(el('p', null, `${PRODUCT[c.product][0]}: ${c.about}.`));
    if (c.signal) body.append(el('p', 'muted', `Категория интереса Bohun: ${c.signal}`));
    if (c.note) body.append(el('p', 'muted', c.note));
    const links = el('p', 'links');
    const src = el('a', null, 'Источник: Wikidata'); src.href = c.source; src.target = '_blank'; src.rel = 'noopener'; links.append(src);
    if (c.web) { const w = el('a', null, 'Сайт компании'); w.href = c.web; w.target = '_blank'; w.rel = 'noopener'; links.append(document.createTextNode(' · '), w); }
    body.append(links);
  }
  body.append(el('p', 'notice', industry.notice === 'Open data. Not clients or partners of Bohun.' ? 'Открытые данные. Не клиенты и не партнёры Bohun. Тип продукции — оценка по открытому описанию.' : industry.notice));
}
$('#ind-close').addEventListener('click', () => { $('#ind-card').hidden = true; });
$('#legend').replaceChildren(el('b', null, 'ОТРАСЛЬ · ОТКРЫТЫЕ ДАННЫЕ'), ...Object.entries(PRODUCT).filter(([k]) => industry.companies.some((c) => c.product === k)).map(([, [label, color]]) => { const r = el('span', 'lg'); const dot = el('i'); dot.style.background = color; r.append(dot, document.createTextNode(label)); return r; }), el('small', null, 'Не клиенты и не партнёры Bohun'));
$('#industry').addEventListener('click', () => setIndustry(!industryOn));
setIndustry(true);

// ---------- thematic layer: undersea infrastructure defence (Serhii's article, 02.01.2026) ----------
const SECTION = { detect: ['Обнаружение', '#2f8fb0'], patrol: ['Патрулирование', '#3f8f6a'], inspect: ['Инспекция', '#c08a2a'], repair: ['Ремонт', '#b8562f'], secure: ['Защищённая связь', '#7d5aa6'] };
const sea = await fetch('/atlas/undersea.json').then((r) => r.json());
const seaByCity = {};
for (const c of sea.companies) (seaByCity[c.lon + ',' + c.lat] ??= []).push(c);
const seaFeatures = [];
for (const list of Object.values(seaByCity)) list.forEach((c, i) => {
  const r = list.length > 1 ? 0.12 + 0.03 * list.length : 0, a = (i / list.length) * Math.PI * 2 + 0.5;
  seaFeatures.push({ type: 'Feature', properties: { name: c.name, color: SECTION[c.section][1] }, geometry: { type: 'Point', coordinates: [c.lon + r * Math.cos(a), c.lat + r * Math.sin(a) * 0.7] } });
});
map.addSource('sea', { type: 'geojson', data: { type: 'FeatureCollection', features: seaFeatures }, cluster: true, clusterRadius: 34, clusterMaxZoom: 5 });
map.addLayer({ id: 'sea-cluster', type: 'circle', source: 'sea', filter: ['has', 'point_count'], paint: { 'circle-color': '#0e2a36', 'circle-opacity': 0.9, 'circle-stroke-color': '#7fd0e8', 'circle-stroke-width': 2, 'circle-radius': ['step', ['get', 'point_count'], 13, 5, 17, 10, 21] } });
map.addLayer({ id: 'sea-count', type: 'symbol', source: 'sea', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Noto Sans Bold'], 'text-size': 12, 'text-allow-overlap': true }, paint: { 'text-color': '#cff3ff' } });
map.addLayer({ id: 'sea-point', type: 'circle', source: 'sea', filter: ['!', ['has', 'point_count']], paint: { 'circle-color': ['get', 'color'], 'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 5, 9, 9], 'circle-stroke-color': '#e8fbff', 'circle-stroke-width': 2 } });
map.addLayer({ id: 'sea-name', type: 'symbol', source: 'sea', filter: ['!', ['has', 'point_count']], minzoom: 4.5, layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Bold'], 'text-size': 11, 'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-optional': true }, paint: { 'text-color': '#0e2a36', 'text-halo-color': '#e8fbff', 'text-halo-width': 1.5 } });
let seaOn = false;
function setSea(on) {
  seaOn = on;
  for (const id of ['sea-cluster', 'sea-count', 'sea-point', 'sea-name']) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  $('#sea').classList.toggle('active', on); $('#legend-sea').hidden = !on;
  if (on) map.flyTo({ center: [-30, 40], zoom: innerWidth <= 820 ? 0.9 : 1.6, pitch: 0, bearing: 0, duration: reduced ? 0 : 1600 });
}
map.on('click', 'sea-cluster', async (e) => {
  const f = e.features[0], z = await map.getSource('sea').getClusterExpansionZoom(f.properties.cluster_id);
  map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.3, duration: reduced ? 0 : 700 });
});
map.on('click', 'sea-point', (e) => showSeaCard(e.features[0].properties.name));
for (const id of ['sea-cluster', 'sea-point']) { map.on('mouseenter', id, () => (map.getCanvas().style.cursor = 'pointer')); map.on('mouseleave', id, () => (map.getCanvas().style.cursor = '')); }
const link = (text, href) => { const a = el('a', null, text); a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a; };
function showSeaCard(name) {
  const box = $('#ind-card'); box.hidden = false;
  const body = $('#ind-body'); body.replaceChildren();
  box.querySelector('.panel-head span').textContent = 'ПОДВОДНАЯ ИНФРАСТРУКТУРА';
  if (!name) {
    $('#ind-title').textContent = 'Не размещены на карте';
    const ul = el('ul', 'ind-list');
    for (const c of sea.unplaced) { const li = el('li'); li.append(el('b', null, c.name), document.createTextNode(` — ${c.about}. ${c.note}`)); ul.append(li); }
    body.append(ul);
  } else {
    const c = sea.companies.find((x) => x.name === name);
    $('#ind-title').textContent = c.name;
    body.append(el('p', 'muted', `${SECTION[c.section][0]} · штаб-квартира: ${c.place} (точка — город, не адрес)`));
    body.append(el('p', null, c.about[0].toUpperCase() + c.about.slice(1) + '.'));
    if (c.note) body.append(el('p', 'muted', c.note));
    const links = el('p', 'links'); links.append(link('Статья Сергія Набока', sea.article.url), document.createTextNode(' · '), link('Источник по городу', c.source)); body.append(links);
  }
  body.append(el('p', 'notice', 'Открытые данные. Не клиенты и не партнёры Bohun.'));
}
$('#legend-sea').replaceChildren(el('b', null, 'ПОДВОДНАЯ ИНФРАСТРУКТУРА'), el('small', 'src', `По статье «${sea.article.title}», 02.01.2026`), ...Object.values(SECTION).map(([label, color]) => { const r = el('span', 'lg'); const dot = el('i'); dot.style.background = color; r.append(dot, document.createTextNode(label)); return r; }), (() => { const b = el('button', 'link', `Ещё ${sea.unplaced.length} без города`); b.type = 'button'; b.addEventListener('click', () => showSeaCard(null)); return b; })(), el('small', null, 'Не клиенты и не партнёры Bohun'));
$('#sea').addEventListener('click', () => setSea(!seaOn));
const lookSel = $('#look');
for (const [k, v] of Object.entries(LOOKS)) { const o = el('option', null, v.label); o.value = k; o.selected = k === LOOK; lookSel.append(o); }
lookSel.addEventListener('change', () => { const u = new URL(location.href); u.searchParams.set('look', lookSel.value); location.href = u.toString(); });
setSea(false);

const overlay = new MapLibreOverlay({ interleaved: true, layers: [], getCursor: ({ isHovering }) => (isHovering ? 'pointer' : 'grab'), onClick: (info) => info.object?.id && clickNode(info.object.id) });
map.addControl(overlay);

// ---------- camera ----------
const panelPad = () => (innerWidth <= 820 ? { top: 70, bottom: innerHeight * 0.5, left: 20, right: 20 } : { top: 80, bottom: 60, left: 60, right: ui.journey ? 420 : 60 });
function fitIds(ids, maxZoom = 6) {
  const lls = ids.map((id) => S.world.nodes[id]).filter(Boolean).map((n) => [n.lon, n.lat]);
  if (!lls.length) return;
  const b = lls.reduce((bb, p) => bb.extend(p), new maplibregl.LngLatBounds(lls[0], lls[0]));
  map.fitBounds(b, { padding: panelPad(), maxZoom, pitch: 45, duration: reduced ? 0 : 1600 });
}
function home() { map.flyTo({ center: [31, 48.4], zoom: innerWidth <= 820 ? 3.6 : 4.6, pitch: 45, bearing: -8, duration: reduced ? 0 : 1600 }); }
function frameFor() {
  if (!ui.journey) return;
  if (ui.journey === 'sell') {
    const s = j.sell, m = s.market && MARKETS[s.market];
    if (s.step === 2) fitIds([HOME.sell, m.partner], 5.5);
    else if (s.step === 3) fitIds([m.partner, ...j.experts(), ...j.actors()], 7);
    else if (s.step === 5 || s.step === 6) fitIds([HOME.sell, m.bank], 6);
  } else {
    const b = j.buy;
    if (b.step === 2 || b.step === 3) fitIds([...Object.keys(CANDIDATES), HOME.buy], 6);
    else if (b.step === 4) fitIds([...j.fits(), HOME.buy], 6);
    else if (b.step === 5 || b.step === 6) fitIds([b.chosen, HOME.buy], 6);
  }
}

// ---------- clicks ----------
function clickNode(id) {
  if (j.hidden(id)) return;
  if (ui.journey === 'sell' && j.sell.step === 3 && j.experts().includes(id)) j.sellVisitExpert(id);
  if (ui.journey === 'buy' && j.buy.step === 3 && id in CANDIDATES) j.buyVisit(id);
  ui.cardId = id; renderCard();
}
function renderCard() {
  const n = ui.cardId && S.world.nodes[ui.cardId];
  $('#card').hidden = !n;
  if (!n) return;
  let name = n.name, text = data.TYPE_ROLE[n.type] ?? '';
  if (n.id === HOME.sell || n.id === HOME.buy) { name = 'Ваша компания'; text = n.id === HOME.sell ? 'Здесь вы производите продукт (сценарий «Продать»).' : 'Сюда нужны компоненты (сценарий «Купить»).'; }
  if (n.id in CANDIDATES) { name = 'Кандидат'; text = j.buy.visited.has(n.id) ? CANDIDATES[n.id].verdict : 'Ещё не проверен.'; }
  if (n.type === 'expert') { const i = j.experts().indexOf(n.id); text = j.sell.experts.has(n.id) ? 'Вывод: ' + INSIGHTS[i] : 'Нажмите — Богун поедет за советом.'; }
  $('#c-type').textContent = (data.TYPE_RU[n.type] ?? '').toUpperCase();
  $('#c-name').textContent = name; $('#c-place').textContent = short(n.place) + ' · ДЕМО'; $('#c-text').textContent = text;
}
$('#c-close').addEventListener('click', () => { ui.cardId = null; renderCard(); });

// ---------- entry and journey panel ----------
function openEntry(side) {
  $('#entry').hidden = false;
  $('#side-need').style.outline = side === 'need' ? '2px solid var(--gold)' : '';
  $('#side-have').style.outline = side === 'have' ? '2px solid var(--gold)' : '';
}
function startJourney(which) {
  ui.journey = which; $('#entry').hidden = true; $('#panel').hidden = false;
  if ((which === 'sell' ? j.sell : j.buy).step === 'done') j.again(which);
  sig.key = null; render();
  if (which === 'sell') fitIds([HOME.sell, 'bank-frankfurt', 'bank-kyiv'], 4.5); else fitIds([HOME.buy, ...Object.keys(CANDIDATES)], 5);
}
document.querySelectorAll('.go').forEach((b) => b.addEventListener('click', () => startJourney(b.dataset.journey)));
$('#just-look').addEventListener('click', () => { $('#entry').hidden = true; });
$('#open-need').addEventListener('click', () => openEntry('need'));
$('#open-have').addEventListener('click', () => openEntry('have'));
$('#j-close').addEventListener('click', () => { ui.journey = null; $('#panel').hidden = true; });
$('#all').addEventListener('click', () => map.flyTo({ center: [20, 30], zoom: 1.4, pitch: 0, bearing: 0, duration: reduced ? 0 : 1800 }));
$('#reset').addEventListener('click', () => { game.reset(); ui = { journey: null, cardId: null, sellCat: null, sellMarket: null, buyCat: null }; $('#panel').hidden = true; renderCard(); home(); openEntry(); });

const btn = (label, fn, cls = '', small) => { const b = el('button', cls, label); b.type = 'button'; if (small) b.append(el('small', null, small)); b.addEventListener('click', fn); return b; };
const sig = { key: null };

function render() {
  if (!ui.journey) return;
  const sell = ui.journey === 'sell', J = sell ? j.sell : j.buy, steps = sell ? SELL_STEPS : BUY_STEPS;
  const key = [ui.journey, J.step, sell ? [j.sell.market, j.sell.experts.size, j.sell.offers, ui.sellCat, ui.sellMarket] : [j.buy.visited.size, j.buy.research, ui.buyCat], S.bohun.status].join('|');
  if (key === sig.key) return;
  sig.key = key;
  $('#j-label').textContent = sell ? 'I HAVE SOMETHING / ПРОДАТЬ' : 'I NEED SOMETHING / КУПИТЬ';
  $('#j-title').textContent = sell ? 'Как Bohun выводит продукт на рынок' : 'Как Bohun находит поставщика';
  $('#j-steps').replaceChildren(...steps.map((t, i) => { const li = el('li', J.step === 'done' || i + 1 < J.step ? 'done' : i + 1 === J.step ? 'current' : ''); li.append(el('span', 'n', String(i + 1)), el('span', 't', t)); return li; }));
  const body = $('#j-body'); body.replaceChildren(...(sell ? sellBody() : buyBody()));
}
function chips(list, value, set) { const box = el('div', 'chips'); for (const c of list) box.append(btn(c, () => { set(c); sig.key = null; render(); }, c === value ? 'on' : '')); return box; }
const riding = () => S.bohun.status === 'moving';

function sellBody() {
  const s = j.sell, out = [], m = s.market && MARKETS[s.market];
  if (s.step === 1) {
    out.push(el('h4', null, 'ЧТО ВЫ ПРОДАЁТЕ'), chips(CATEGORIES, ui.sellCat, (c) => (ui.sellCat = c)));
    out.push(el('h4', null, 'НА КАКОЙ РЫНОК'), chips(Object.values(MARKETS).map((x) => x.label), ui.sellMarket && MARKETS[ui.sellMarket].label, (lbl) => (ui.sellMarket = Object.keys(MARKETS).find((k) => MARKETS[k].label === lbl))));
    out.push(el('p', 'muted', 'Ваша компания на карте — в Запорожье (демо).'));
    const go = btn('ДАЛЬШЕ', () => j.sellChoose(ui.sellCat, ui.sellMarket), 'primary big'); go.disabled = !(ui.sellCat && ui.sellMarket); out.push(go);
  } else if (s.step === 2) {
    out.push(el('p', null, `Рынок «${m.label}» пока в тумане: непонятно, кто там покупает ${s.category} и как.`));
    const b = btn(riding() ? 'БОГУН В ПУТИ…' : 'ОТПРАВИТЬ БОГУНА НА РАЗВЕДКУ', () => j.sellScout(), 'primary big'); b.disabled = riding(); out.push(b);
  } else if (s.step === 3) {
    out.push(el('p', null, 'На рынке видны участники: покупатель, дистрибьютор, испытательная площадка. Чтобы понять правила, Богун советуется с экспертами.'));
    j.experts().forEach((id, i) => {
      const done = s.experts.has(id);
      if (done) { const it = el('div', 'item ok'); it.append(el('b', null, `ЭКСПЕРТ ${i + 1}: `), document.createTextNode(INSIGHTS[i])); out.push(it); }
      else { const b = btn(riding() ? 'БОГУН В ПУТИ…' : `К ЭКСПЕРТУ ${i + 1}`, () => j.sellVisitExpert(id), 'primary'); b.disabled = riding(); out.push(b); }
    });
  } else if (s.step === 4) {
    if (!s.offers) out.push(el('p', null, 'Богун следит за рынком: конкурсы, дистрибьюторы, пилотные проекты…'));
    else { out.push(el('p', null, 'Богун принёс три возможности. Какую берём?')); for (const o of OFFERS) out.push(btn(o.label.toUpperCase(), () => j.sellPickOffer(o.id), 'primary', o.text)); }
  } else if (s.step === 5) {
    const o = OFFERS.find((x) => x.id === s.offer);
    out.push(el('p', null, `${o.text} Осталось выбрать путь поставок: быстрее обычно дороже.`));
    for (const r of game.routeOptions(HOME.sell, m.bank)) out.push(btn(`${r.label.toUpperCase()}: ${modesText(r.modes)}`, () => { freeSlot(); const f = game.startFlow(HOME.sell, m.bank, r.id); if (f.ok) j.sellFlowStarted(f.flowId, HOME.sell, m.bank); }, 'primary', `≈ ${r.days} усл. дн. и ${r.price} усл. ед. за поставку`));
  } else if (s.step === 6) {
    out.push(el('p', null, `Поставки идут на рынок «${m.label}». Богун остаётся рядом: сопровождение — часть работы.`));
  } else {
    out.push(el('p', null, 'Готово. Вот что сделал Bohun:'));
    const o = OFFERS.find((x) => x.id === s.offer);
    for (const t of [`разведал рынок «${m.label}» для ${s.category}`, 'поговорил с двумя экспертами', `нашёл возможность: ${o.label.toLowerCase()}`, 'наладил путь поставок и сопровождал первые поставки']) out.push(el('div', 'item ok', '✓ ' + t));
    const a = el('a', 'cta', 'TELL US WHAT YOUR COMPANY CAN OFFER →'); a.href = '#contact'; a.addEventListener('click', (e) => { e.preventDefault(); note('Здесь будет форма заявки (секция 10 сайта).'); }); out.push(a);
    out.push(btn('ПРОЙТИ ЕЩЁ РАЗ', () => { j.again('sell'); ui.sellCat = ui.sellMarket = null; }));
  }
  return out;
}
function buyBody() {
  const b = j.buy, out = [];
  if (b.step === 1) {
    out.push(el('h4', null, 'ЧТО ВАМ НУЖНО'), chips(CATEGORIES, ui.buyCat, (c) => (ui.buyCat = c)));
    out.push(el('p', 'muted', 'Ваш завод на карте — в Днепре (демо).'));
    const go = btn('НАЙТИ ПОСТАВЩИКОВ', () => j.buyChoose(ui.buyCat), 'primary big'); go.disabled = !ui.buyCat; out.push(go);
  } else if (b.step === 2) {
    out.push(el('p', null, `Bohun изучает рынок: кто в Украине и Европе работает с ${b.category}…`));
  } else if (b.step === 3) {
    out.push(el('p', null, `Найдено пять кандидатов. Богун с экспертом проверяет их на месте. Подходящих: ${j.fits().length} из 2.`));
    for (const [id, c] of Object.entries(CANDIDATES)) {
      if (b.visited.has(id)) { const it = el('div', 'item ' + (c.fit ? 'ok' : 'bad')); it.append(el('b', null, short(c.place).toUpperCase() + ': '), document.createTextNode(c.verdict)); out.push(it); }
      else { const x = btn(riding() ? `${short(c.place).toUpperCase()} · БОГУН В ПУТИ…` : `ПРОВЕРИТЬ: ${short(c.place).toUpperCase()}`, () => j.buyVisit(id), 'primary'); x.disabled = riding(); out.push(x); }
    }
  } else if (b.step === 4) {
    out.push(el('p', null, 'Короткий список. Сравните и выберите:'));
    for (const id of j.fits()) {
      const r = game.routeOptions(id, HOME.buy).find((o) => o.id === 'cheap') ?? game.routeOptions(id, HOME.buy)[0];
      out.push(btn(`ВЫБРАТЬ: ${short(CANDIDATES[id].place).toUpperCase()}`, () => j.buyChoose2(id), 'primary', `${CANDIDATES[id].verdict} Доставка: ${modesText(r.modes)}, ≈ ${r.days} усл. дн., ${r.price} усл. ед.`));
    }
  } else if (b.step === 5) {
    out.push(el('p', null, `Богун едет к поставщику в ${short(CANDIDATES[b.chosen].place)}, потом к вам: так начинается знакомство.`));
  } else if (b.step === 6) {
    out.push(el('p', null, 'Поставки идут к вашему заводу. Богун сопровождает первые поставки.'));
  } else {
    out.push(el('p', null, 'Готово. Вот что сделал Bohun:'));
    for (const t of [`нашёл пять компаний, работающих с ${b.category}`, 'проверил кандидатов с экспертом на месте', `сократил до двух и помог выбрать: ${short(CANDIDATES[b.chosen].place)}`, 'организовал знакомство и сопровождал первые поставки']) out.push(el('div', 'item ok', '✓ ' + t));
    const a = el('a', 'cta', 'TELL US WHAT YOU ARE LOOKING FOR →'); a.href = '#contact'; a.addEventListener('click', (e) => { e.preventDefault(); note('Здесь будет форма заявки (секция 10 сайта).'); }); out.push(a);
    out.push(btn('ПРОЙТИ ЕЩЁ РАЗ', () => { j.again('buy'); ui.buyCat = null; }));
  }
  return out;
}

// ---------- deck.gl layers ----------
function layers(now) {
  // far out (whole planet) the 3D scene would be a pile of giant models: show only corridors and flows there
  const farOut = map.getZoom() < 3.2;
  const nodes = Object.values(S.world.nodes).filter((n) => !j.hidden(n.id) && !farOut);
  const buildings = nodes.filter((n) => NODE_MODEL[n.type] && !(n.id in CANDIDATES));
  const byModel = {};
  for (const n of buildings) (byModel[NODE_MODEL[n.type]] ??= []).push(n);
  const cands = nodes.filter((n) => n.id in CANDIDATES);
  const pulse = reduced ? 1 : 0.5 + 0.5 * Math.sin(now / 260);

  const edges = Object.values(S.world.edges).filter((e) => !j.hidden(e.a) && !j.hidden(e.b) && e.mode !== 'air');
  const L = [
    new PathLayer({ id: 'corridors', data: edges, getPath: (e) => e.pts.map(llOf), getColor: (e) => (e.mode === 'rail' ? [70, 64, 52, 200] : e.mode === 'road' ? [140, 116, 74, 220] : [70, 120, 140, 150]), getWidth: (e) => (e.mode === 'sea' ? 1.5 : 2.5), widthUnits: 'pixels', getDashArray: (e) => (e.mode === 'sea' ? [6, 4] : e.mode === 'rail' ? [2, 1.5] : [0, 0]), dashJustified: true, extensions: [new PathStyleExtension({ dash: true })], parameters: { depthTest: false } }),
    new PathLayer({ id: 'flows', data: S.flows.flatMap((f) => f.steps.map((s) => ({ f, s }))), getPath: (d) => d.s.pts.map(llOf), getColor: [226, 176, 40, 255], getWidth: 4, widthUnits: 'pixels', getDashArray: [5, 3], extensions: [new PathStyleExtension({ dash: true })], parameters: { depthTest: false } }),
  ];
  for (const [url, list] of Object.entries(byModel)) L.push(new ScenegraphLayer({ id: 'b-' + url, data: list, scenegraph: url, getPosition: (n) => [n.lon, n.lat], getOrientation: [0, 30, 90], sizeScale: 9000, sizeMinPixels: 26, sizeMaxPixels: 84, _lighting: 'pbr', pickable: true }));
  L.push(new ScenegraphLayer({ id: 'cands', data: cands, scenegraph: CAND_MODEL, getPosition: (n) => [n.lon, n.lat], getOrientation: [0, 30, 90], sizeScale: 9000, sizeMinPixels: 30, sizeMaxPixels: 92, _lighting: 'pbr', pickable: true }));

  // vehicles: background traffic and the visitor's cargo
  const movers = [];
  if (!farOut) for (const a of S.ambient) { const p = pointAt(a.pts, a.d); movers.push({ kind: a.kind, p, mine: false }); }
  if (!farOut) for (const c of S.carriers) { const s = c.steps[Math.min(c.leg, c.steps.length - 1)]; movers.push({ kind: MODES[s.mode].kind, p: pointAt(s.pts, c.d), mine: true }); }
  // vehicles are switched off (Serhii, 08.10: "убрать движущиеся объекты"); deliveries still run and show as moving flow lines
  if (SHOW_VEHICLES) for (const [kind, url] of Object.entries(MOVER_MODEL)) {
    const list = movers.filter((m) => m.kind === kind);
    L.push(new ScenegraphLayer({ id: 'm-' + kind, data: list, scenegraph: url, getPosition: (m) => llOf(m.p), getOrientation: (m) => [0, MODEL_YAW - heading(m.p), 90], sizeScale: kind === 'ship' ? 900 : 3500, sizeMinPixels: kind === 'ship' ? 3 : 12, sizeMaxPixels: kind === 'ship' ? 7 : 34, _lighting: 'pbr', updateTriggers: { getPosition: now, getOrientation: now } }));
  }
  if (SHOW_VEHICLES) L.push(new IconLayer({ id: 'planes', data: movers.filter((m) => m.kind === 'plane'), getIcon: () => ICON.plane, getPosition: (m) => [...llOf(m.p), 60000], getAngle: (m) => -heading(m.p), getSize: 22, sizeUnits: 'pixels', billboard: false, updateTriggers: { getPosition: now, getAngle: now } }));

  // fog until the visitor's research or scouting lifts it
  const fog = [];
  if (ui.journey === 'buy' && j.buy.research < 2) { const k = 1 - j.researchProgress(); for (const id of Object.keys(CANDIDATES)) fog.push({ ll: [CANDIDATES[id].lon, CANDIDATES[id].lat], a: k }); }
  if (ui.journey === 'sell' && j.sell.market && !j.sell.scouted) { const n = S.world.nodes[MARKETS[j.sell.market].partner]; fog.push({ ll: [n.lon, n.lat], a: 1, big: true }); }
  L.push(new ScatterplotLayer({ id: 'fog', data: fog, getPosition: (d) => d.ll, getRadius: (d) => (d.big ? 330000 : 170000), getFillColor: (d) => [60, 64, 56, Math.round(170 * d.a)], stroked: false, parameters: { depthTest: false }, updateTriggers: { getFillColor: now } }));

  // experts and market actors (sell), Bohun
  const experts = nodes.filter((n) => n.type === 'expert');
  L.push(new IconLayer({ id: 'experts', data: experts, getIcon: () => ICON.expert, getPosition: (n) => [n.lon, n.lat, 20000], getSize: 34, sizeUnits: 'pixels', pickable: true }));
  const bp = game.bohunPos();
  L.push(new IconLayer({ id: 'bohun', data: farOut ? [] : [bp], getIcon: () => ICON.rider, getPosition: (p) => llOf(p), getSize: 84, sizeUnits: 'pixels', updateTriggers: { getPosition: now } }));

  // what to press next: rings and labels
  const hints = hintList();
  L.push(new ScatterplotLayer({ id: 'hints', data: hints, getPosition: (h) => { const n = S.world.nodes[h[0]]; return [n.lon, n.lat]; }, getRadius: 20 + 6 * pulse, radiusUnits: 'pixels', stroked: true, filled: false, getLineColor: [255, 196, 40, Math.round(140 + 115 * pulse)], getLineWidth: 3, lineWidthUnits: 'pixels', parameters: { depthTest: false }, updateTriggers: { getRadius: now, getLineColor: now } }));
  syncMarkers(hints, cands);
  // coins over a bank on delivery
  const live = coins.filter((c) => now - c.t0 < 1400 && now >= c.t0);
  L.push(new ScatterplotLayer({ id: 'coins', data: live, getPosition: (c) => [c.ll[0] + (c.kind ? 0.15 : -0.15) * ((now - c.t0) / 1400), c.ll[1], 30000 + 120000 * ((now - c.t0) / 1400)], getRadius: 6, radiusUnits: 'pixels', getFillColor: (c) => (c.kind ? [106, 159, 79, 255] : [245, 197, 66, 255]), getLineColor: [60, 50, 20, 255], stroked: true, getLineWidth: 1, lineWidthUnits: 'pixels', updateTriggers: { getPosition: now } }));
  return L;
}
// dynamic labels (hints, your company, verdict badges) as HTML markers: crisp text on the globe and in mercator
const markers = new Map();
function syncMarkers(hints, cands) {
  const want = new Map();
  for (const [id, t] of hints) want.set('h:' + id, { id, t, cls: 'mk hint' });
  const mine = ui.journey === 'sell' ? HOME.sell : ui.journey === 'buy' ? HOME.buy : null;
  if (mine) want.set('m:' + mine, { id: mine, t: 'ВАША КОМПАНИЯ', cls: 'mk mine' });
  for (const n of cands) if (j.buy.visited.has(n.id)) want.set('v:' + n.id, { id: n.id, t: CANDIDATES[n.id].fit ? '✓ ПОДХОДИТ' : '✕ НЕТ', cls: 'mk ' + (CANDIDATES[n.id].fit ? 'fit' : 'nofit') });
  for (const [k, m] of markers) if (!want.has(k)) { m.remove(); markers.delete(k); }
  for (const [k, w] of want) {
    if (markers.has(k)) continue;
    const n = S.world.nodes[w.id], e = el('div', w.cls, w.t);
    e.addEventListener('click', () => clickNode(w.id));
    markers.set(k, new maplibregl.Marker({ element: e, anchor: 'bottom', offset: k.startsWith('m:') ? [0, 40] : [0, -34] }).setLngLat([n.lon, n.lat]).addTo(map));
  }
}
function hintList() {
  const out = [];
  if (ui.journey === 'sell') {
    const s = j.sell;
    if (s.step === 2 && !riding()) out.push([MARKETS[s.market].partner, 'РАЗВЕДКА']);
    if (s.step === 3) j.experts().forEach((id) => !s.experts.has(id) && out.push([id, 'ЭКСПЕРТ']));
  } else if (ui.journey === 'buy') {
    if (j.buy.step === 3) for (const id of Object.keys(CANDIDATES)) if (!j.buy.visited.has(id)) out.push([id, 'КАНДИДАТ?']);
  }
  return out;
}

// ---------- loop ----------
let last = performance.now();
function tickFrame(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  let r = dt; while (r > 1e-6) { const d = Math.min(0.05, r); game.tick(d); j.tick(d); r -= d; }
  for (let i = coins.length - 1; i >= 0; i--) if (now - coins[i].t0 > 1500) coins.splice(i, 1);
  overlay.setProps({ layers: layers(now) });
  render();
  if (ui.journey) { const J = ui.journey === 'sell' ? j.sell : j.buy, prog = $('#j-progress'); let v = null, t = '';
    if (ui.journey === 'buy' && J.step === 2) { v = j.researchProgress(); t = 'ИССЛЕДОВАНИЕ…'; }
    else if (ui.journey === 'sell' && J.step === 4 && !J.offers) { v = j.monitorProgress(); t = 'МОНИТОРИНГ РЫНКА…'; }
    else if (J.step === 6) { v = J.delivered / 2; t = `ПОСТАВОК ${J.delivered} ИЗ 2`; }
    prog.hidden = v === null; if (v !== null) { prog.querySelector('i').style.width = `${v * 100}%`; prog.querySelector('span').textContent = t; } }
}
function loop(now) { tickFrame(now); requestAnimationFrame(loop); }
window.__step = (n = 1) => { for (let i = 0; i < n; i++) { last -= 50; tickFrame(performance.now()); } map.triggerRepaint(); }; // test hook: advance without rAF
seedDefaults();
requestAnimationFrame(loop);
