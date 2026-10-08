import assert from 'node:assert/strict';
import * as data from '../src/flows/data/network.js';
import { createFlows, MODES, LIMITS, endpointsOk, buildWorld, findPath } from '../src/flows/core.js';

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };
const events = [];
const mk = (seed = 1) => { events.length = 0; return createFlows({ data, seed, bohunStart: data.BOHUN_START, onEvent: (t, p) => events.push([t, p]) }); };
const run = (g, sec) => { for (let i = 0; i < sec * 20; i++) g.tick(0.05); };
const of = (t) => events.filter((e) => e[0] === t);

ok('network: every corridor joins endpoint types that fit its mode', () => {
  const type = Object.fromEntries(data.nodes.map((x) => [x.id, x.type]));
  for (const e of data.edges) assert.ok(endpointsOk(e.mode, type[e.a], type[e.b]), `${e.id}: ${e.mode} ${type[e.a]}-${type[e.b]}`);
});
ok('network: ids are unique and every edge refers to a real node', () => {
  const ids = new Set(data.nodes.map((x) => x.id)); assert.equal(ids.size, data.nodes.length);
  assert.equal(new Set(data.edges.map((e) => e.id)).size, data.edges.length);
  for (const e of data.edges) assert.ok(ids.has(e.a) && ids.has(e.b), e.id);
});
ok('network: every node is connected to the main graph', () => {
  const w = buildWorld(data), start = data.nodes[0].id;
  for (const nd of data.nodes) assert.ok(findPath(w, start, nd.id, (e) => e.time), nd.id + ' unreachable');
});
ok('routes: mine -> New York bank has fast, cheap and no-air options with the right modes', () => {
  const g = mk(); const opts = g.routeOptions('mine-kr', 'bank-newyork');
  assert.ok(opts.length >= 2);
  const fast = opts.find((o) => o.label.includes('Быстро')), cheap = opts.find((o) => o.label.includes('Дёшево'));
  assert.ok(fast.modes.includes('air')); assert.ok(cheap.modes.includes('sea') && !cheap.modes.includes('air'));
  assert.ok(fast.days < cheap.days && fast.price > cheap.price);
});
ok('routes: no option between a node and itself or unknown ids', () => {
  const g = mk(); assert.deepEqual(g.routeOptions('mine-kr', 'mine-kr'), []); assert.deepEqual(g.routeOptions('x', 'mine-kr'), []);
});
ok('flow: start, carriers travel, delivery counts and a bank gives coins', () => {
  const g = mk(); assert.equal(g.startFlow('fac-lviv', 'bank-kyiv', 'fast').ok, true);
  run(g, 40);
  assert.ok(g.state.delivered >= 1); assert.equal(g.state.coins, g.state.delivered * 10);
  assert.ok(of('cargoArrived').every((e) => e[1].bank === true));
});
ok('flow: delivering to a non-bank counts but gives no coins', () => {
  const g = mk(); g.startFlow('mine-kr', 'port-odesa', 'fast'); run(g, 40);
  assert.ok(g.state.delivered >= 1); assert.equal(g.state.coins, 0);
});
ok('flow: only sources can start flows; duplicates, same node and the limit are rejected', () => {
  const g = mk();
  assert.equal(g.startFlow('bank-kyiv', 'port-odesa', 'fast').reason, 'not-a-source');
  assert.equal(g.startFlow('mine-kr', 'mine-kr', 'fast').reason, 'same-node');
  assert.equal(g.startFlow('mine-kr', 'bank-kyiv', 'fast').ok, true);
  assert.equal(g.startFlow('mine-kr', 'bank-kyiv', 'fast').reason, 'duplicate');
  assert.equal(g.startFlow('fac-lviv', 'bank-kyiv', 'fast').ok, true);
  assert.equal(g.startFlow('fac-kharkiv', 'bank-kyiv', 'fast').ok, true);
  assert.equal(g.startFlow('fac-dnipro', 'bank-kyiv', 'fast').reason, 'limit');
  assert.equal(g.state.flows.length, LIMITS.flows);
});
ok('flow: stopping a flow removes its carriers; unknown flow is rejected', () => {
  const g = mk(); const { flowId } = g.startFlow('mine-kr', 'bank-kyiv', 'fast'); run(g, 3.4);
  assert.ok(g.state.carriers.length > 0); assert.equal(g.stopFlow(flowId).ok, true);
  assert.equal(g.state.carriers.length, 0); assert.equal(g.stopFlow(flowId).reason, 'unknown-flow');
});
ok('ambient: traffic appears on its own, never exceeds the cap, uses fitting vehicles', () => {
  const g = mk(); run(g, 120);
  assert.ok(g.state.ambient.length > 5); assert.ok(g.state.ambient.length <= LIMITS.ambient);
  for (const a of g.state.ambient) assert.ok([MODES[a.mode].kind, 'wagon'].includes(a.kind));
});
ok('Bohun: rides to a node, busy while moving, card requested on arrival and when selecting his own node', () => {
  const g = mk(); assert.equal(g.moveBohun('st-lviv').ok, true); assert.equal(g.state.bohun.status, 'moving');
  assert.equal(g.moveBohun('st-kyiv').reason, 'busy'); assert.equal(of('dossierRequested').length, 0);
  run(g, 20); assert.equal(g.state.bohun.at, 'st-lviv'); assert.equal(g.state.bohun.status, 'arrived');
  assert.equal(of('dossierRequested').at(-1)[1].nodeId, 'st-lviv');
  const k = of('dossierRequested').length; g.moveBohun('st-lviv'); assert.equal(of('dossierRequested').length, k + 1);
  assert.equal(g.moveBohun('nowhere').reason, 'unknown-node');
});
ok('reset: clears flows, carriers, ambient, counters and Bohun; nothing fires afterwards', () => {
  const g = mk(); g.startFlow('mine-kr', 'bank-kyiv', 'fast'); g.moveBohun('st-lviv'); run(g, 6);
  g.reset(); const s = g.state;
  assert.equal(s.flows.length + s.carriers.length + s.ambient.length, 0); assert.equal(s.delivered + s.coins, 0);
  assert.equal(s.bohun.at, data.BOHUN_START); assert.equal(s.bohun.status, 'idle');
  const before = events.length; g.tick(0.05); assert.ok(events.slice(before).every((e) => e[0] !== 'cargoArrived' && e[0] !== 'bohunArrived'));
});
ok('determinism: same seed gives the same traffic', () => {
  const a = mk(7), b = mk(7); run(a, 30); run(b, 30);
  assert.deepEqual(a.state.ambient.map((x) => [x.kind, Math.round(x.d)]), b.state.ambient.map((x) => [x.kind, Math.round(x.d)]));
});
console.log(`\n${n} flow checks passed`);
