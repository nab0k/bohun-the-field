import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createGame } from '../src/game/core.js';
import { scenario } from '../src/game/scenario.js';
import { geoToWorld } from '../src/game/geo.js';

const events = [];
const g = createGame(scenario, { onEvent: (t, p) => events.push([t, p]) });
const S = g.state;
const run = (sec) => { for (let i = 0; i < sec * 20; i++) g.tick(0.05); };
const last = (t) => events.filter((e) => e[0] === t).at(-1)?.[1];
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };

ok('starts idle in Lviv, Warsaw route locked', () => {
  assert.equal(S.bogun.at, 'lviv'); assert.equal(S.bogun.status, 'idle');
  assert.equal(S.routes['lviv-warsaw'].available, false);
  assert.equal(S.counter, 0);
});

ok('1a send Bohun to Kyiv: waiting -> moving -> arrived, card opens', () => {
  assert.equal(g.moveBohun('kyiv').ok, true);
  assert.equal(S.bogun.status, 'moving');
  run(1); assert.equal(S.bogun.status, 'moving'); assert.equal(last('dossierRequested'), undefined);
  run(10);
  assert.equal(S.bogun.status, 'arrived'); assert.equal(S.bogun.at, 'kyiv');
  assert.equal(last('dossierRequested').dossierId, 'dossier-kyiv');
});

ok('1b invalid sends are rejected with a reason, state unchanged', () => {
  assert.equal(g.moveBohun('kyiv').reason, 'already-there');
  assert.equal(g.moveBohun('warsaw').reason, 'no-route'); // Kyiv-Warsaw route does not exist in this scenario
  assert.equal(g.moveBohun('nowhere').reason, 'unknown-city');
  g.moveBohun('lviv'); assert.equal(g.moveBohun('lviv').reason, 'busy');
  run(10); assert.equal(S.bogun.at, 'lviv');
});

ok('1c locked route cannot be used by Bohun', () => {
  assert.equal(g.moveBohun('warsaw').reason, 'locked');
  assert.equal(S.bogun.status, 'arrived');
});

ok('2a shipment moves, arrives, counter changes; duplicate on same route rejected', () => {
  assert.equal(g.dispatchCargo('lviv-kyiv').ok, true);
  assert.equal(g.dispatchCargo('lviv-kyiv').reason, 'busy');
  assert.equal(S.transports.length, 1);
  run(1); assert.equal(S.counter, 0);
  run(10); assert.equal(S.counter, 1); assert.equal(S.transports.length, 0);
  assert.equal(last('cargoArrived').counter, 1);
});

ok('2b locked route cannot ship', () => {
  assert.equal(g.dispatchCargo('lviv-warsaw').reason, 'locked');
  assert.equal(g.dispatchCargo('nope').reason, 'unknown-route');
  assert.equal(S.counter, 1);
});

ok('3a research shows progress, then opens node, card and the route', () => {
  assert.equal(g.research('site-1').ok, true);
  run(2);
  assert.ok(S.nodes['site-1'].progress > 0.4 && S.nodes['site-1'].progress < 0.6);
  assert.equal(S.nodes['site-1'].state, 'researching');
  run(3);
  assert.equal(S.nodes['site-1'].state, 'open');
  assert.equal(last('dossierRequested').dossierId, 'dossier-site-1');
  assert.equal(S.routes['lviv-warsaw'].available, true);
});

ok('3b repeated research does not duplicate anything', () => {
  const before = events.filter((e) => e[0] === 'researchCompleted').length;
  assert.equal(g.research('site-1').reason, 'already-done');
  run(5);
  assert.equal(events.filter((e) => e[0] === 'researchCompleted').length, before);
  assert.equal(events.filter((e) => e[0] === 'routeUnlocked').length, 1);
});

ok('3c research started twice mid-way is rejected', () => {
  g.resetDemo(); g.research('site-1');
  assert.equal(g.research('site-1').reason, 'busy');
});

ok('research changes what is available (Warsaw route usable afterwards)', () => {
  g.resetDemo();
  assert.equal(g.commands().find((c) => c.action === 'send' && c.target === 'warsaw').enabled, false);
  g.research('site-1'); run(5);
  assert.equal(g.commands().find((c) => c.action === 'send' && c.target === 'warsaw').enabled, true);
  assert.equal(g.moveBohun('warsaw').ok, true); run(10);
  assert.equal(S.bogun.at, 'warsaw');
  assert.equal(g.dispatchCargo('lviv-warsaw').ok, true); run(10);
  assert.equal(S.counter, 1);
});

ok('reset restores the initial state completely', () => {
  g.resetDemo();
  assert.equal(S.bogun.at, 'lviv'); assert.equal(S.counter, 0); assert.equal(S.transports.length, 0);
  assert.equal(S.nodes['site-1'].state, 'closed'); assert.equal(S.routes['lviv-warsaw'].available, false);
});

ok('resetDemo mid-move and mid-research cancels everything', () => {
  g.resetDemo(); g.moveBohun('kyiv'); g.dispatchCargo('lviv-kyiv'); g.research('site-1'); run(1);
  g.resetDemo();
  assert.equal(S.bogun.status, 'idle'); assert.equal(S.transports.length, 0); assert.equal(S.nodes['site-1'].state, 'closed');
  const before = events.length; run(10);
  assert.equal(events.slice(before).filter((e) => e[0] !== 'stateChanged').length, 0); // no stray timers fire
});

ok('replay gives the same result', () => {
  const seq = () => { g.resetDemo(); const from = events.length; g.moveBohun('kyiv'); g.dispatchCargo('lviv-kyiv'); g.research('site-1'); run(12);
    return events.slice(from).map((e) => e[0]).filter((x) => x !== 'stateChanged').join(','); };
  assert.equal(seq(), seq());
});

ok('projection reproduces the contract world coordinates (< 0.01 units)', () => {
  const layout = JSON.parse(readFileSync(new URL('../src/game/data/world-layout.json', import.meta.url)));
  for (const c of layout.scene.nodes) {
    const w = geoToWorld(c.lonLat[0], c.lonLat[1]);
    assert.ok(Math.hypot(w.x - c.world[0], w.y - c.world[1]) < 0.01, `${c.id} ${w.x} ${w.y} vs ${c.world}`);
  }
});

ok('scenario uses the contract: cities, ids, routes, initial lock state', () => {
  const pos = (id) => [S.nodes[id].x, S.nodes[id].y];
  assert.deepEqual(pos('lviv'), [991.329, 740.685]);
  assert.deepEqual(pos('kyiv'), [1369.588, 657.412]);
  assert.deepEqual(pos('warsaw'), [814.374, 521.08]);
  assert.equal(scenario.scene.width, 2560); assert.equal(scenario.scene.height, 1600);
  g.resetDemo();
  assert.equal(S.routes['lviv-kyiv'].available, true);
  assert.equal(S.routes['lviv-warsaw'].available, false);
  for (const nd of scenario.nodes) assert.ok(nd.x > 0 && nd.x < 2560 && nd.y > 0 && nd.y < 1600, nd.id + ' inside the scene');
});

ok('camera has one owner and setMode validates', () => {
  assert.equal(g.setMode('game').ok, true); assert.equal(S.mode, 'game');
  assert.equal(g.setMode('bogus').reason, 'unknown-mode'); assert.equal(S.mode, 'game');
  g.resetDemo(); assert.equal(S.mode, 'game'); // reset keeps the mode
  g.setMode('presentation'); assert.equal(S.mode, 'presentation');
});

console.log(`\n${n} checks passed`);
