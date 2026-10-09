// Dev-only relay for AISStream.io (ships worldwide). AISStream forbids browser connections: the key stays on the server,
// one WebSocket keeps the latest position and static data per vessel in memory, and the map asks for its view over HTTP.
// Used by vite.config.js under /live/aisstream/*; the Cloudflare Worker will take over the same paths in production.
//   GET /live/aisstream/positions?w=&s=&e=&n=&limit=  → { updated, total, ships: [[mmsi, lon, lat, sog, cog, heading], ...] }
//   GET /live/aisstream/vessel/<mmsi>                → { mmsi, name, type, destination, callsign, sog, cog, time }
// The Ukraine / Black Sea exclusion is applied here too, before anything leaves the relay.
import fs from 'node:fs';
import { makeExclusion } from '../src/atlas/live.js';

const URL_WS = 'wss://stream.aisstream.io/v0/stream';
const MAX_AGE = 30 * 60 * 1000; // a vessel not heard for 30 minutes is dropped

export function createAisRelay({ apiKey, countriesPath }) {
  const gj = JSON.parse(fs.readFileSync(countriesPath, 'utf8'));
  const excluded = makeExclusion(gj.features.find((f) => f.properties.a3 === 'UKR').geometry);
  const ships = new Map();
  let ws = null, started = false, retry = 2000, lastMsg = 0, status = 'idle';

  function connect() {
    status = 'connecting';
    ws = new WebSocket(URL_WS);
    ws.onopen = () => {
      ws.send(JSON.stringify({ APIKey: apiKey, BoundingBoxes: [[[-90, -180], [90, 180]]], FilterMessageTypes: ['PositionReport', 'StandardClassBPositionReport', 'ShipStaticData'] }));
      status = 'open'; retry = 2000;
    };
    ws.onmessage = async (e) => {
      const m = JSON.parse(typeof e.data === 'string' ? e.data : await e.data.text());
      if (m.error) { status = 'error: ' + m.error; return; }
      const meta = m.MetaData; if (!meta) return;
      lastMsg = Date.now();
      const s = ships.get(meta.MMSI) ?? { mmsi: meta.MMSI };
      const name = meta.ShipName?.trim(); if (name) s.name = name;
      const p = m.Message?.PositionReport ?? m.Message?.StandardClassBPositionReport;
      if (p) {
        if (!p.Valid || Math.abs(p.Latitude) > 90 || Math.abs(p.Longitude) > 180) return;
        Object.assign(s, { lon: p.Longitude, lat: p.Latitude, sog: p.Sog, cog: p.Cog, heading: p.TrueHeading, time: Date.now() });
      }
      const st = m.Message?.ShipStaticData;
      if (st) Object.assign(s, { type: st.Type, destination: st.Destination?.trim() || undefined, callsign: st.CallSign?.trim() || undefined, name: st.Name?.trim() || s.name });
      ships.set(meta.MMSI, s);
    };
    ws.onclose = () => { status = 'reconnecting'; setTimeout(connect, retry); retry = Math.min(retry * 2, 60000); };
    ws.onerror = () => {};
  }
  setInterval(() => { const old = Date.now() - MAX_AGE; for (const [k, s] of ships) if ((s.time ?? 0) < old) ships.delete(k); }, 60000).unref();

  const json = (res, code, body) => { res.statusCode = code; res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store'); res.end(JSON.stringify(body)); };
  return function middleware(req, res, next) {
    const u = new URL(req.url, 'http://x');
    if (!started) { started = true; connect(); }
    if (u.pathname === '/positions') {
      const q = (k, d) => (u.searchParams.has(k) ? Number(u.searchParams.get(k)) : d);
      const w = q('w', -180), s = q('s', -90), e = q('e', 180), n = q('n', 90), limit = Math.min(q('limit', 6000), 20000);
      const wrap = e - w >= 360;
      const inView = (x, y) => y >= s && y <= n && (wrap || (w <= e ? x >= w && x <= e : x >= w || x <= e));
      let list = [];
      for (const v of ships.values()) if (v.lon != null && inView(v.lon, v.lat) && !excluded(v.lon, v.lat)) list.push(v);
      const total = list.length;
      if (list.length > limit) list = list.sort((a, b) => (b.sog ?? 0) - (a.sog ?? 0)).slice(0, limit); // moving ships first
      return json(res, 200, { status, updated: lastMsg, total, ships: list.map((v) => [v.mmsi, +v.lon.toFixed(4), +v.lat.toFixed(4), v.sog, v.cog, v.heading]) });
    }
    const m = u.pathname.match(/^\/vessel\/(\d+)$/);
    if (m) {
      const v = ships.get(Number(m[1]));
      if (!v || v.lon == null || excluded(v.lon, v.lat)) return json(res, 404, { error: 'not found' });
      return json(res, 200, { mmsi: v.mmsi, name: v.name, type: v.type, destination: v.destination, callsign: v.callsign, sog: v.sog, cog: v.cog, time: v.time });
    }
    next();
  };
}
