// "Flows" rules. Framework-free: no Phaser, no DOM, no wall-clock. The view drives it with tick(dt) and listens to events.
// Everything here is a DEMONSTRATION: abstract carriers, conditional time and cost units, no real goods or operations.
import frame from './data/world-frame.json' with { type: 'json' };

export const project = (lon, lat) => ({ x: (lon + 180) * frame.pxPerDeg, y: (frame.latNorth - lat) * frame.pxPerDeg * frame.squash });
export const WORLD = { width: frame.width, height: frame.height };

// speed in world px per second; cost per world px (conditional)
export const MODES = {
  rail: { label: 'Ж/д', speed: 70, cost: 1.0, kind: 'train' },
  road: { label: 'Дорога', speed: 55, cost: 1.6, kind: 'truck' },
  sea: { label: 'Море', speed: 38, cost: 0.45, kind: 'ship' },
  air: { label: 'Воздух', speed: 210, cost: 3.6, kind: 'plane' },
};
export const LIMITS = { flows: 3, ambient: 50, perFlowCarriers: 3 };
const SOURCE_TYPES = new Set(['mine', 'factory-s', 'factory-m', 'factory-l']);
export const isSource = (type) => SOURCE_TYPES.has(type);

// which endpoint types each corridor mode may connect (checked in tests against the network data)
const ENDS = {
  sea: ['port'],
  air: ['airfield'],
  rail: ['station', 'port', 'mine', 'factory-s', 'factory-m', 'factory-l', 'bank'],
  road: ['station', 'port', 'airfield', 'mine', 'factory-s', 'factory-m', 'factory-l', 'bank'],
};
export const endpointsOk = (mode, typeA, typeB) => ENDS[mode].includes(typeA) && ENDS[mode].includes(typeB);

function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
function polyLen(pts) { let n = 0; for (let i = 1; i < pts.length; i++) n += dist(pts[i - 1], pts[i]); return n; }
export function pointAt(pts, d) {
  for (let i = 1; i < pts.length; i++) {
    const seg = dist(pts[i - 1], pts[i]);
    if (d <= seg || i === pts.length - 1) { const k = seg ? Math.min(d / seg, 1) : 0; return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * k, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * k, ax: pts[i].x - pts[i - 1].x, ay: pts[i].y - pts[i - 1].y }; }
    d -= seg;
  }
  const l = pts.at(-1); return { x: l.x, y: l.y, ax: 1, ay: 0 };
}

export function buildWorld(data) {
  const nodes = {};
  for (const n of data.nodes) nodes[n.id] = { ...n, ...project(n.lon, n.lat) };
  const edges = {};
  const adj = {};
  for (const n of Object.keys(nodes)) adj[n] = [];
  for (const e of data.edges) {
    const pts = [nodes[e.a], ...(e.via ?? []).map(([lon, lat]) => project(lon, lat)), nodes[e.b]].map((p) => ({ x: p.x, y: p.y }));
    const length = polyLen(pts), m = MODES[e.mode];
    edges[e.id] = { ...e, pts, length, time: length / m.speed, cost: length * m.cost };
    adj[e.a].push(e.id); adj[e.b].push(e.id);
  }
  return { nodes, edges, adj };
}

// shortest path with a weight function; returns steps oriented from -> to
export function findPath(world, from, to, weight) {
  const distTo = { [from]: 0 }, prev = {}, todo = new Set(Object.keys(world.nodes));
  while (todo.size) {
    let u = null;
    for (const id of todo) if (distTo[id] !== undefined && (u === null || distTo[id] < distTo[u])) u = id;
    if (u === null || u === to) break;
    todo.delete(u);
    for (const eid of world.adj[u]) {
      const e = world.edges[eid], w = weight(e);
      if (w === null) continue;
      const v = e.a === u ? e.b : e.a, nd = distTo[u] + w;
      if (distTo[v] === undefined || nd < distTo[v]) { distTo[v] = nd; prev[v] = { u, eid }; }
    }
  }
  if (distTo[to] === undefined) return null;
  const steps = [];
  for (let v = to; v !== from; v = prev[v].u) {
    const { u, eid } = prev[v], e = world.edges[eid];
    steps.unshift({ edgeId: eid, from: u, to: v, mode: e.mode, pts: e.a === u ? e.pts : [...e.pts].reverse(), length: e.length });
  }
  return steps;
}

const PROFILES = [
  { id: 'fast', label: 'Быстро', weight: (e) => e.time },
  { id: 'cheap', label: 'Дёшево', weight: (e) => e.cost },
  { id: 'noair', label: 'Без воздуха', weight: (e) => (e.mode === 'air' ? null : e.time) },
];

export function createFlows({ data, seed = 1, onEvent = () => {}, bohunStart } = {}) {
  const world = buildWorld(data);
  const S = { world };
  let rand = rng(seed);

  function emit(type, p = {}) { S.log.unshift({ type, ...p, t: S.time }); if (S.log.length > 40) S.log.pop(); onEvent(type, p); if (type !== 'rejected') onEvent('stateChanged', {}); }
  const reject = (action, reason, p = {}) => { emit('rejected', { action, reason, ...p }); return { ok: false, reason }; };

  function reset() {
    rand = rng(seed);
    Object.assign(S, { time: 0, flows: [], carriers: [], ambient: [], nextId: 1, delivered: 0, coins: 0, log: [], spawnClock: 0 });
    S.bohun = { at: bohunStart, status: 'idle', from: null, to: null, pts: null, d: 0, len: 0 };
    emit('reset');
  }

  // ---- route options between two nodes (three profiles, identical paths merged) ----
  function routeOptions(fromId, toId) {
    if (!world.nodes[fromId] || !world.nodes[toId] || fromId === toId) return [];
    const out = [];
    for (const p of PROFILES) {
      const steps = findPath(world, fromId, toId, p.weight);
      if (!steps) continue;
      const key = steps.map((s) => s.edgeId).join('>');
      const time = steps.reduce((n, s) => n + s.length / MODES[s.mode].speed, 0), cost = steps.reduce((n, s) => n + s.length * MODES[s.mode].cost, 0);
      const same = out.find((o) => o.key === key);
      if (same) { same.label += ' / ' + p.label; continue; }
      out.push({ id: p.id, key, label: p.label, steps, modes: [...new Set(steps.map((s) => s.mode))], days: Math.max(1, Math.round(time)), price: Math.max(1, Math.round(cost / 10)) });
    }
    return out;
  }

  // ---- action: Bohun rides point to point and the card opens on arrival ----
  function moveBohun(nodeId) {
    const n = world.nodes[nodeId];
    if (!n) return reject('move', 'unknown-node', { nodeId });
    const b = S.bohun;
    if (b.status === 'moving') return reject('move', 'busy', { nodeId });
    if (b.at === nodeId) { emit('dossierRequested', { nodeId, reason: 'select' }); return { ok: true, here: true }; }
    const a = world.nodes[b.at];
    Object.assign(b, { status: 'moving', from: b.at, to: nodeId, pts: [{ x: a.x, y: a.y }, { x: n.x, y: n.y }], d: 0, len: dist(a, n) });
    emit('bohunDeparted', { from: b.from, to: nodeId });
    return { ok: true };
  }

  // ---- action: redirect a flow ----
  function startFlow(fromId, toId, optionId) {
    const from = world.nodes[fromId];
    if (!from || !world.nodes[toId]) return reject('flow', 'unknown-node');
    if (!isSource(from.type)) return reject('flow', 'not-a-source', { fromId });
    if (fromId === toId) return reject('flow', 'same-node');
    if (S.flows.length >= LIMITS.flows) return reject('flow', 'limit');
    if (S.flows.some((f) => f.fromId === fromId && f.toId === toId)) return reject('flow', 'duplicate');
    const opt = routeOptions(fromId, toId).find((o) => o.id === optionId || o.label.split(' / ').includes(PROFILES.find((p) => p.id === optionId)?.label));
    if (!opt) return reject('flow', 'no-route', { fromId, toId });
    const flow = { id: S.nextId++, fromId, toId, label: opt.label, modes: opt.modes, days: opt.days, price: opt.price, steps: opt.steps, clock: 99 };
    S.flows.push(flow);
    emit('flowStarted', { flowId: flow.id, fromId, toId, label: opt.label });
    return { ok: true, flowId: flow.id };
  }
  function stopFlow(flowId) {
    const i = S.flows.findIndex((f) => f.id === flowId);
    if (i < 0) return reject('stop', 'unknown-flow');
    S.flows.splice(i, 1);
    S.carriers = S.carriers.filter((c) => c.flowId !== flowId);
    emit('flowStopped', { flowId });
    return { ok: true };
  }

  function spawnAmbient() {
    if (S.ambient.length >= LIMITS.ambient) return;
    const ids = Object.keys(world.edges), e = world.edges[ids[Math.floor(rand() * ids.length)]];
    const dir = rand() < 0.5;
    S.ambient.push({ id: S.nextId++, mode: e.mode, kind: e.mode === 'road' && rand() < 0.4 ? 'wagon' : MODES[e.mode].kind, pts: dir ? e.pts : [...e.pts].reverse(), len: e.length, d: 0 });
  }

  function tick(dt) {
    S.time += dt;
    // ambient traffic: a new mover now and then, capped
    S.spawnClock += dt;
    while (S.spawnClock >= 0.35) { S.spawnClock -= 0.35; if (rand() < 0.75) spawnAmbient(); }
    for (const a of S.ambient) a.d += MODES[a.mode].speed * dt;
    S.ambient = S.ambient.filter((a) => a.d < a.len);

    // flows spawn carriers every few seconds
    for (const f of S.flows) {
      f.clock += dt;
      if (f.clock >= 3.2 && S.carriers.filter((c) => c.flowId === f.id).length < LIMITS.perFlowCarriers) {
        f.clock = 0;
        S.carriers.push({ id: S.nextId++, flowId: f.id, steps: f.steps, leg: 0, d: 0, toId: f.toId });
      }
    }
    for (const c of [...S.carriers]) {
      c.d += MODES[c.steps[c.leg].mode].speed * dt;
      while (c.leg < c.steps.length && c.d >= c.steps[c.leg].length) { c.d -= c.steps[c.leg].length; c.leg++; }
      if (c.leg >= c.steps.length) {
        S.carriers.splice(S.carriers.indexOf(c), 1);
        const bank = world.nodes[c.toId].type === 'bank';
        S.delivered += 1;
        if (bank) S.coins += 10;
        emit('cargoArrived', { flowId: c.flowId, toId: c.toId, bank, delivered: S.delivered, coins: S.coins });
      }
    }

    const b = S.bohun;
    if (b.status === 'moving') {
      b.d += 170 * dt;
      if (b.d >= b.len) {
        b.at = b.to; b.status = 'arrived';
        emit('bohunArrived', { at: b.at });
        emit('dossierRequested', { nodeId: b.at, reason: 'arrival' });
      }
    }
  }

  const bohunPos = () => { const b = S.bohun; if (b.status === 'moving') return pointAt(b.pts, b.d); const n = world.nodes[b.at]; return { x: n.x, y: n.y, ax: 1, ay: 0 }; };

  reset();
  return { state: S, world, tick, reset, routeOptions, moveBohun, startFlow, stopFlow, bohunPos };
}
