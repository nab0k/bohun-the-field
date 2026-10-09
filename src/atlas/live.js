// LIVE layer (Serhii, 09.10.2026): real aircraft, ships and trains from free open APIs whose licences allow a commercial site.
//   aircraft — adsb.lol (ODbL 1.0), queried around the current view only (the API answers by point and radius, max 250 NM)
//   ships    — Fintraffic Digitraffic Marine AIS (CC BY 4.0), Baltic Sea from Finnish coastal receivers
//   trains   — Fintraffic Digitraffic Rail GPS (CC BY 4.0), Finland
// Not used: OpenSky and adsb.fi (non-commercial only), AISStream (needs our own server and key; later, via the Worker).
// Rule (08.10–09.10): nothing is shown over Ukraine or the Black Sea; the filter runs on every record before it reaches the map.
// The browser calls /live/<source>/... — in dev Vite proxies it (vite.config.js); in production the Cloudflare Worker will.
const POLL = { air: 30000, ais: 60000, rail: 30000 };
const AIR_MIN_ZOOM = 4.5;
const NM = 1.852;

// ---------- exclusion zone: Ukraine (with a ~30 km margin), the Black Sea and the Sea of Azov (a box) ----------
const BLACK_SEA = [[27.4, 42.0], [28.0, 43.3], [28.6, 44.5], [29.6, 45.4], [30.8, 46.6], [33.5, 46.2], [35.0, 45.4], [36.7, 45.5], [37.5, 47.1], [39.3, 47.3], [39.0, 46.4], [38.0, 45.2], [37.0, 44.9], [38.7, 44.3], [39.9, 43.4], [41.6, 42.6], [41.7, 41.5], [40.0, 41.0], [37.0, 41.1], [34.8, 41.9], [33.0, 42.0], [31.3, 41.2], [29.1, 41.25], [28.0, 41.7], [27.4, 42.0]];
const MARGIN_DEG = 0.3;
function inRing([x, y], ring) {
  let inside = false;
  for (let i = 0, k = ring.length - 1; i < ring.length; k = i++) {
    const [xi, yi] = ring[i], [xk, yk] = ring[k];
    if ((yi > y) !== (yk > y) && x < ((xk - xi) * (y - yi)) / (yk - yi) + xi) inside = !inside;
  }
  return inside;
}
function nearRing([x, y], ring, d) {
  const c = Math.cos((y * Math.PI) / 180);
  for (let i = 1; i < ring.length; i++) {
    const ax = ring[i - 1][0] * c, ay = ring[i - 1][1], bx = ring[i][0] * c, by = ring[i][1], px = x * c;
    const dx = bx - ax, dy = by - ay, t = Math.max(0, Math.min(1, ((px - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
    if (Math.hypot(px - ax - t * dx, y - ay - t * dy) < d) return true;
  }
  return false;
}
export function makeExclusion(ukraineGeometry) {
  const polys = ukraineGeometry.type === 'Polygon' ? [ukraineGeometry.coordinates] : ukraineGeometry.coordinates;
  const rings = polys.map((p) => p[0]);
  const box = [21.5, 43.8, 41.0, 52.8]; // Ukraine bbox plus margin: cheap pre-check
  return (lon, lat) => {
    if (lon > 27 && lon < 42 && lat > 40.8 && lat < 47.5 && inRing([lon, lat], BLACK_SEA)) return true;
    if (lon > 34.7 && lon < 39.5 && lat > 45.2 && lat < 47.4) return true; // Sea of Azov
    if (lon < box[0] || lon > box[2] || lat < box[1] || lat > box[3]) return false;
    return rings.some((r) => inRing([lon, lat], r) || nearRing([lon, lat], r, MARGIN_DEG));
  };
}

// ---------- icons ----------
const svgImage = (body, size = 64) => new Promise((ok, fail) => {
  const im = new Image(size, size);
  im.onload = () => ok(im); im.onerror = fail;
  im.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 64 64">${body}</svg>`)}`;
});
const ICONS = {
  'live-plane': '<path d="M32 4 L36 24 L58 36 L58 41 L36 35 L35 51 L42 56 L42 60 L32 57 L22 60 L22 56 L29 51 L28 35 L6 41 L6 36 L28 24 Z" fill="#f2b632" stroke="#2a2416" stroke-width="2.5" stroke-linejoin="round"/>',
  'live-ship': '<path d="M32 6 L44 26 L44 56 L20 56 L20 26 Z" fill="#2f9fb8" stroke="#0e2a36" stroke-width="3" stroke-linejoin="round"/>',
  'live-ship-still': '<circle cx="32" cy="32" r="13" fill="#2f9fb8" fill-opacity="0.75" stroke="#0e2a36" stroke-width="3"/>',
  'live-train': '<rect x="14" y="14" width="36" height="36" rx="8" fill="#c8452f" stroke="#fff3e6" stroke-width="4"/><rect x="22" y="22" width="20" height="10" rx="2" fill="#fff3e6"/>',
};

// ---------- sources ----------
const AIS_TYPE = (t) => (t >= 70 && t <= 79 ? 'Cargo' : t >= 80 && t <= 89 ? 'Tanker' : t >= 60 && t <= 69 ? 'Passenger' : t === 35 ? 'Military' : t === 30 ? 'Fishing' : t === 52 ? 'Tug' : t === 51 ? 'Search and rescue' : t === 36 || t === 37 ? 'Sailing / pleasure' : t >= 40 && t <= 49 ? 'High-speed craft' : 'Other');
const getJSON = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(`${url} → ${r.status}`); return r.json(); };

// query points that cover the visible area: ~600 km grid, at most 6 points, closest to the view centre first
function airPoints(map, excluded) {
  const b = map.getBounds(), c = map.getCenter();
  const s = Math.max(-75, b.getSouth()), n = Math.min(80, b.getNorth());
  const stepLat = 600 / 111;
  const pts = [];
  for (let lat = s + stepLat / 2; lat < n + stepLat / 2; lat += stepLat) {
    const stepLon = stepLat / Math.max(0.2, Math.cos((lat * Math.PI) / 180));
    let w = b.getWest(), e = b.getEast(); if (e - w > 360) { w = -180; e = 180; }
    for (let lon = w + stepLon / 2; lon < e + stepLon / 2; lon += stepLon) {
      const L = ((lon + 540) % 360) - 180;
      if (!excluded(L, lat)) pts.push([L, Math.min(lat, n)]);
    }
  }
  pts.sort((p, q) => Math.hypot(p[0] - c.lng, p[1] - c.lat) - Math.hypot(q[0] - c.lng, q[1] - c.lat));
  return pts.slice(0, 6);
}

export async function createLive(map, { ukraineGeometry, onCard }) {
  const excluded = makeExclusion(ukraineGeometry);
  for (const [id, body] of Object.entries(ICONS)) if (!map.hasImage(id)) map.addImage(id, await svgImage(body), { pixelRatio: 2 });
  const empty = { type: 'FeatureCollection', features: [] };
  for (const id of ['live-air', 'live-ais', 'live-rail']) map.addSource(id, { type: 'geojson', data: empty });
  const size = (k) => ['interpolate', ['linear'], ['zoom'], 3, 0.35 * k, 6, 0.55 * k, 10, 0.8 * k];
  map.addLayer({ id: 'live-rail', type: 'symbol', source: 'live-rail', layout: { 'icon-image': 'live-train', 'icon-size': size(1), 'icon-allow-overlap': true, 'text-field': ['step', ['zoom'], '', 7, ['get', 'label']], 'text-font': ['Noto Sans Bold'], 'text-size': 10, 'text-offset': [0, 1.2], 'text-anchor': 'top', 'text-optional': true }, paint: { 'text-color': '#3a1a12', 'text-halo-color': '#fff3e6', 'text-halo-width': 1.4 } });
  map.addLayer({ id: 'live-ais', type: 'symbol', source: 'live-ais', layout: { 'icon-image': ['case', ['get', 'moving'], 'live-ship', 'live-ship-still'], 'icon-size': size(0.9), 'icon-rotate': ['get', 'course'], 'icon-rotation-alignment': 'map', 'icon-pitch-alignment': 'map', 'icon-allow-overlap': true } });
  map.addLayer({ id: 'live-air', type: 'symbol', source: 'live-air', minzoom: AIR_MIN_ZOOM - 0.5, layout: { 'icon-image': 'live-plane', 'icon-size': size(1), 'icon-rotate': ['get', 'track'], 'icon-rotation-alignment': 'map', 'icon-pitch-alignment': 'map', 'icon-allow-overlap': true, 'text-field': ['step', ['zoom'], '', 7.5, ['get', 'label']], 'text-font': ['Noto Sans Bold'], 'text-size': 10, 'text-offset': [0, 1.3], 'text-anchor': 'top', 'text-optional': true }, paint: { 'text-color': '#2a2416', 'text-halo-color': '#fff8e0', 'text-halo-width': 1.4 } });

  const state = { on: false, show: { air: true, ais: true, rail: true }, counts: { air: 0, ais: 0, rail: 0 }, dropped: 0, updated: {}, error: {} };
  const records = { air: new Map(), ais: new Map(), rail: new Map() };
  const listeners = new Set();
  const changed = () => listeners.forEach((f) => f(state));
  const put = (kind, src, feats) => { state.counts[kind] = feats.length; state.updated[kind] = new Date(); map.getSource(src).setData({ type: 'FeatureCollection', features: feats }); changed(); };
  const pt = (lon, lat, props) => ({ type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: [lon, lat] } });

  // adsb.lol limits are dynamic: one query at a time with a pause, at most 4 points; on HTTP 429 keep what we have and slow down
  async function pollAir() {
    if (map.getZoom() < AIR_MIN_ZOOM) { records.air.clear(); put('air', 'live-air', []); return; }
    const seen = new Map();
    let limited = false;
    for (const [i, [lon, lat]] of airPoints(map, excluded).slice(0, 4).entries()) {
      if (i) await new Promise((r) => setTimeout(r, 1500));
      const r = await fetch(`/live/adsb/v2/point/${lat.toFixed(3)}/${lon.toFixed(3)}/250`);
      if (r.status === 429) { limited = true; break; }
      if (!r.ok) throw new Error(`adsb.lol → ${r.status}`);
      for (const a of (await r.json()).ac ?? []) if (a.lat != null && a.alt_baro !== 'ground') seen.set(a.hex, a);
    }
    backoff.air = limited ? Math.min(backoff.air * 2, 8) : 1;
    state.limited = limited;
    if (limited && !seen.size) { changed(); return; } // nothing new: the last picture stays
    records.air = new Map([...seen].filter(([, a]) => !excluded(a.lon, a.lat)));
    state.dropped = seen.size - records.air.size;
    put('air', 'live-air', [...records.air.values()].map((a) => pt(a.lon, a.lat, { id: a.hex, track: a.track ?? a.true_heading ?? 0, label: (a.flight ?? '').trim() })));
  }
  async function pollAis() {
    const d = await getJSON('/live/ais/api/ais/v1/locations');
    records.ais = new Map(d.features.filter((f) => !excluded(...f.geometry.coordinates)).map((f) => [f.properties.mmsi, f]));
    put('ais', 'live-ais', [...records.ais.values()].map((f) => { const p = f.properties, moving = p.sog > 0.5; return pt(...f.geometry.coordinates, { id: p.mmsi, moving, course: moving ? (p.heading < 360 ? p.heading : p.cog) : 0 }); }));
  }
  async function pollRail() {
    const d = await getJSON('/live/rail/api/v1/train-locations/latest/');
    records.rail = new Map(d.filter((t) => !excluded(...t.location.coordinates)).map((t) => [String(t.trainNumber), t]));
    put('rail', 'live-rail', [...records.rail.values()].map((t) => pt(...t.location.coordinates, { id: String(t.trainNumber), label: `Train ${t.trainNumber}` })));
  }

  const POLLERS = { air: pollAir, ais: pollAis, rail: pollRail };
  const timers = {};
  const backoff = { air: 1, ais: 1, rail: 1 }, busy = {};
  async function run(kind) {
    clearTimeout(timers[kind]);
    if (!state.on || !state.show[kind]) return;
    if (busy[kind]) { busy[kind] = 'again'; return; } // one request chain per source at a time
    busy[kind] = true;
    if (document.visibilityState === 'visible' || !state.updated[kind]) { // hidden tab: only the first load, no polling
      try { await POLLERS[kind](); state.error[kind] = null; } catch (e) { state.error[kind] = String(e.message || e); changed(); }
    }
    const again = busy[kind] === 'again'; busy[kind] = false;
    timers[kind] = setTimeout(() => run(kind), again ? 1000 : POLL[kind] * backoff[kind]);
  }
  let moveTimer;
  map.on('moveend', () => { if (!state.on || !state.show.air) return; clearTimeout(moveTimer); moveTimer = setTimeout(() => run('air'), 600); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') for (const k of Object.keys(POLLERS)) run(k); });

  const LAYER = { air: 'live-air', ais: 'live-ais', rail: 'live-rail' };
  function apply() {
    for (const [k, id] of Object.entries(LAYER)) map.setLayoutProperty(id, 'visibility', state.on && state.show[k] ? 'visible' : 'none');
    for (const k of Object.keys(POLLERS)) run(k);
    changed();
  }

  // ---------- cards ----------
  const fmt = (v, unit) => (v == null ? '—' : `${Math.round(v).toLocaleString('en')} ${unit}`);
  async function card(kind, id) {
    if (kind === 'air') {
      const a = records.air.get(id); if (!a) return;
      onCard({ head: 'LIVE · AIRCRAFT', title: (a.flight ?? '').trim() || a.r || a.hex.toUpperCase(), rows: [['Registration', a.r ?? '—'], ['Aircraft type', a.desc ?? a.t ?? '—'], ['Altitude', fmt(a.alt_baro, 'ft')], ['Ground speed', fmt(a.gs, 'kn')], ['ICAO address', a.hex.toUpperCase()]], source: 'adsb.lol, ODbL 1.0', sourceUrl: 'https://www.adsb.lol/docs/open-data/api/' });
    } else if (kind === 'ais') {
      const f = records.ais.get(Number(id)); if (!f) return;
      const p = f.properties;
      const base = { head: 'LIVE · SHIP', title: `MMSI ${p.mmsi}`, rows: [['Speed', `${p.sog} kn`], ['Course', `${Math.round(p.cog)}°`]], source: 'Fintraffic / digitraffic.fi, CC BY 4.0', sourceUrl: 'https://www.digitraffic.fi/en/terms-of-service/' };
      onCard(base);
      try {
        const v = await getJSON(`/live/ais/api/ais/v1/vessels/${p.mmsi}`);
        onCard({ ...base, title: v.name?.trim() || base.title, rows: [['Type', AIS_TYPE(v.shipType)], ['Destination', v.destination?.trim() || '—'], ...base.rows, ['MMSI', String(p.mmsi)]] });
      } catch { /* the position card stays */ }
    } else if (kind === 'rail') {
      const t = records.rail.get(String(id)); if (!t) return;
      const base = { head: 'LIVE · TRAIN', title: `Train ${t.trainNumber}`, rows: [['Speed', `${t.speed} km/h`], ['Position time', new Date(t.timestamp).toUTCString().slice(17, 25) + ' UTC']], source: 'Fintraffic / digitraffic.fi, CC BY 4.0', sourceUrl: 'https://www.digitraffic.fi/en/terms-of-service/' };
      onCard(base);
      try {
        const [tr] = await getJSON(`/live/rail/api/v1/trains/latest/${t.trainNumber}`);
        const rows = tr?.timeTableRows ?? [];
        onCard({ ...base, title: `${tr.trainType} ${tr.trainNumber}`, rows: [['Operator', tr.operatorShortCode?.toUpperCase() ?? '—'], ['Category', tr.trainCategory ?? '—'], ['Route', rows.length ? `${rows[0].stationShortCode} → ${rows.at(-1).stationShortCode}` : '—'], ...base.rows] });
      } catch { /* the position card stays */ }
    }
  }
  for (const [k, id] of Object.entries(LAYER)) {
    map.on('click', id, (e) => card(k, e.features[0].properties.id));
    map.on('mouseenter', id, () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', id, () => (map.getCanvas().style.cursor = ''));
  }

  apply();
  return {
    state, excluded, AIR_MIN_ZOOM,
    setOn(on) { state.on = on; apply(); },
    setShow(kind, on) { state.show[kind] = on; if (!on) { records[kind].clear(); put(kind, LAYER[kind], []); } apply(); },
    onChange(f) { listeners.add(f); f(state); },
  };
}
