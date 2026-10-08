// Checks for /field.html data: the hero geography is generated, uses the flows frame and keeps Crimea inside Ukraine.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as data from '../src/flows/data/network.js';
import { project } from '../src/flows/core.js';

const geo = JSON.parse(fs.readFileSync(new URL('../public/field/hero-geo.json', import.meta.url)));
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };
const inside = (pt, ring) => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i], b = ring[j]; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
const inUA = (lon, lat) => { const p = project(lon, lat); return geo.land.find((c) => c.id === 'UKR').rings.some((r) => inside([p.x, p.y], r)); };

ok('hero geo: generated file names its generator and source', () => {
  assert.equal(geo.generator, 'scripts/build-field-hero.mjs'); assert.match(geo.source, /Natural Earth/);
});
ok('hero geo: same frame as the flows world (Kyiv projects to the same point)', () => {
  const kyiv = geo.cities.find((c) => c.name === 'Kyiv'), p = project(30.5, 50.43);
  assert.ok(Math.hypot(kyiv.x - p.x, kyiv.y - p.y) < 3, `${kyiv.x},${kyiv.y} vs ${p.x},${p.y}`);
});
ok('hero geo: Crimea (Simferopol, Sevastopol) is inside the Ukrainian outline; Dnipro river present', () => {
  assert.ok(inUA(34.1, 44.95)); assert.ok(inUA(33.52, 44.6));
  assert.ok(geo.rivers.some((r) => /Dnipro|Dnepre/.test(r.name)));
});
ok('hero geo: every Ukrainian test node of the network sits inside the hero box', () => {
  const b = geo.box;
  for (const nd of data.nodes.filter((x) => x.lon > 22 && x.lon < 41 && x.lat > 44 && x.lat < 53)) {
    const p = project(nd.lon, nd.lat); assert.ok(p.x > b.x0 && p.x < b.x1 && p.y > b.y0 && p.y < b.y1, nd.id);
  }
});
console.log(`${n} field checks passed`);

// ---- scenarios (src/field/scenarios.js) driven through the real flows rules ----
const { createFlows } = await import('../src/flows/core.js');
const { createScenarios, fieldData, FIT, NEED_HOME, HAVE_HOME, MARKETS } = await import('../src/field/scenarios.js');
const setup = () => {
  const changes = [];
  let sc;
  const g = createFlows({ data: fieldData, seed: 7, bohunStart: fieldData.BOHUN_START, onEvent: (t, p) => sc?.onEvent(t, p) });
  sc = createScenarios(g, { onChange: (t, id) => { changes.push(t); if (t.endsWith('Done')) g.stopFlow(id); } });
  const run = (sec) => { for (let i = 0; i < sec * 20; i++) { g.tick(0.05); sc.tick(0.05); } };
  const ride = (id) => { assert.equal(g.moveBohun(id).ok, true); run(120); assert.equal(g.state.bohun.at, id); };
  return { g, sc, run, ride, changes };
};
let m = 0;
const ok2 = (name, fn) => { fn(); m++; console.log('ok  ' + name); };
ok2('scenario data: extra candidates join the network and reach your factory', () => {
  const g = createFlows({ data: fieldData, seed: 1, bohunStart: fieldData.BOHUN_START });
  for (const c of ['cand-brno', 'cand-rzeszow', 'cand-zhytomyr']) assert.ok(g.routeOptions(c, NEED_HOME).length, c);
  for (const m of Object.values(MARKETS)) assert.ok(g.routeOptions(HAVE_HOME, m.bank).length, m.bank);
});
ok2('I NEED: candidates hidden until research; research takes time and runs once', () => {
  const { sc, run } = setup();
  assert.equal(sc.hidden(FIT), true); assert.equal(sc.needStage(), 1);
  assert.equal(sc.research().ok, true); assert.equal(sc.research().reason, 'running');
  run(1); assert.equal(sc.hidden(FIT), true); run(3);
  assert.equal(sc.hidden(FIT), false); assert.equal(sc.needStage(), 2); assert.equal(sc.research().reason, 'done');
});
ok2('I NEED: checking a wrong candidate does not finish the step; the fitting one does; then two loads finish it', () => {
  const { g, sc, run, ride } = setup();
  sc.research(); run(4);
  ride('cand-zhytomyr'); assert.equal(sc.needStage(), 2); assert.equal(sc.info('cand-zhytomyr').fit, false);
  assert.equal(sc.canFlow('cand-zhytomyr', NEED_HOME).reason, 'not-fit');
  ride(FIT); assert.equal(sc.needStage(), 3);
  const opt = g.routeOptions(FIT, NEED_HOME)[0], r = g.startFlow(FIT, NEED_HOME, opt.id);
  sc.flowStarted(r.flowId, FIT, NEED_HOME, opt); assert.equal(sc.needStage(), 4);
  run(120); assert.equal(sc.needStage(), 'done'); assert.equal(g.state.flows.length, 0);
});
ok2('I HAVE: market closed until Bohun meets the partner; two loads finish it and the result is recorded', () => {
  const { g, sc, run, ride } = setup();
  assert.equal(sc.haveStage(), 1); assert.equal(sc.chooseMarket('eu').ok, true); assert.equal(sc.haveStage(), 2);
  assert.equal(sc.canFlow(HAVE_HOME, MARKETS.eu.bank).reason, 'partner-first');
  ride(MARKETS.eu.partner); assert.equal(sc.haveStage(), 3); assert.equal(sc.canFlow(HAVE_HOME, MARKETS.eu.bank).ok, true);
  const opt = g.routeOptions(HAVE_HOME, MARKETS.eu.bank)[0], r = g.startFlow(HAVE_HOME, MARKETS.eu.bank, opt.id);
  sc.flowStarted(r.flowId, HAVE_HOME, MARKETS.eu.bank, opt); assert.equal(sc.haveStage(), 4);
  run(200); assert.equal(sc.haveStage(), 'done'); assert.equal(sc.have.results.length, 1); assert.equal(sc.have.results[0].market, 'eu');
  assert.equal(sc.again('have').ok, true); assert.equal(sc.haveStage(), 1); assert.ok(sc.have.met.has('eu'));
});
ok2('reset clears both scenarios', () => {
  const { g, sc, run } = setup(); sc.research(); run(4); sc.chooseMarket('us'); g.reset();
  assert.equal(sc.needStage(), 1); assert.equal(sc.haveStage(), 1); assert.equal(sc.hidden(FIT), true);
});
console.log(`${m} scenario checks passed`);
