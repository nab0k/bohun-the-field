// Checks for /atlas.html: borders data and the two journeys (SELL, BUY) run through the real flows rules.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createFlows } from '../src/flows/core.js';
import { createJourneys, atlasData, MARKETS, HOME, CANDIDATES, OFFERS } from '../src/atlas/journeys.js';

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('ok  ' + name); };
const setup = () => {
  let j;
  const g = createFlows({ data: atlasData, seed: 7, bohunStart: atlasData.BOHUN_START, onEvent: (t, p) => j?.onEvent(t, p) });
  j = createJourneys(g, { onChange: (t, id) => { if (t.endsWith(':done')) g.stopFlow(id); } });
  const run = (sec) => { for (let i = 0; i < sec * 20; i++) { g.tick(0.05); j.tick(0.05); } };
  const ride = (fn) => { const r = fn(); assert.ok(r.ok, JSON.stringify(r)); run(150); };
  return { g, j, run, ride };
};

ok('borders: generated from Natural Earth, Crimea inside Ukraine', () => {
  const gj = JSON.parse(fs.readFileSync(new URL('../public/atlas/countries.geojson', import.meta.url)));
  assert.equal(gj.generator, 'scripts/build-atlas-borders.mjs');
  const ua = gj.features.find((f) => f.properties.a3 === 'UKR');
  const inside = (pt, ring) => { let c = false; for (let i = 0, k = ring.length - 1; i < ring.length; k = i++) { const a = ring[i], b = ring[k]; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
  assert.ok(ua.geometry.coordinates.some((p) => inside([34.1, 44.95], p[0])));
});
ok('models: every model file listed in the provenance exists', () => {
  const pv = JSON.parse(fs.readFileSync(new URL('../public/atlas/models/PROVENANCE.json', import.meta.url)));
  assert.match(pv.license, /CC0/);
  for (const f of pv.files) assert.ok(fs.existsSync(new URL('../public/atlas/models/' + f.file, import.meta.url)), f.file);
});
ok('data: candidates reach the buyer factory; the seller factory reaches every market bank', () => {
  const g = createFlows({ data: atlasData, seed: 1, bohunStart: atlasData.BOHUN_START });
  for (const id of Object.keys(CANDIDATES)) assert.ok(g.routeOptions(id, HOME.buy).length, id);
  for (const m of Object.values(MARKETS)) assert.ok(g.routeOptions(HOME.sell, m.bank).length, m.bank);
});
ok('SELL: choose -> scout -> two experts -> monitoring -> offer -> flow -> two loads -> done', () => {
  const { g, j, run, ride } = setup();
  assert.equal(j.sellScout().reason, 'step');
  assert.equal(j.sellChoose('PROPULSION', 'eu').ok, true); assert.equal(j.sell.step, 2);
  assert.equal(j.hidden('exp-eu-1'), true);
  ride(() => j.sellScout()); assert.equal(j.sell.step, 3); assert.equal(j.hidden('exp-eu-1'), false); assert.equal(j.hidden('exp-us-1'), true);
  ride(() => j.sellVisitExpert('exp-eu-1')); assert.equal(j.sell.step, 3);
  assert.equal(j.sellVisitExpert('exp-eu-2').ok, true); run(0.5);
  for (let i = 0; i < 300 && j.sell.step === 3; i++) run(0.5);
  assert.equal(j.sell.step, 4); assert.equal(j.sell.offers, false); assert.equal(j.sellPickOffer('dist').reason, 'step');
  run(5); assert.equal(j.sell.offers, true);
  assert.equal(j.sellPickOffer(OFFERS[1].id).ok, true); assert.equal(j.sell.step, 5);
  const opt = g.routeOptions(HOME.sell, MARKETS.eu.bank)[0], r = g.startFlow(HOME.sell, MARKETS.eu.bank, opt.id);
  assert.equal(j.sellFlowStarted(r.flowId, HOME.sell, MARKETS.eu.bank).ok, true); assert.equal(j.sell.step, 6);
  run(200); assert.equal(j.sell.step, 'done'); assert.equal(g.state.flows.length, 0);
});
ok('BUY: choose -> research reveals candidates -> two fitting found -> shortlist -> meeting (two rides) -> flow starts itself -> done', () => {
  const { g, j, run, ride } = setup();
  assert.equal(j.buyChoose('COMPONENTS').ok, true); assert.equal(j.hidden('cand-rzeszow'), true);
  run(4); assert.equal(j.buy.step, 3); assert.equal(j.hidden('cand-rzeszow'), false);
  ride(() => j.buyVisit('cand-brno')); ride(() => j.buyVisit('cand-rzeszow')); assert.equal(j.buy.step, 3);
  assert.equal(j.buyChoose2('cand-rzeszow').reason, 'step');
  ride(() => j.buyVisit('cand-timisoara')); assert.equal(j.buy.step, 4); assert.deepEqual(j.fits().sort(), ['cand-rzeszow', 'cand-timisoara']);
  assert.equal(j.buyChoose2('cand-brno').reason, 'step');
  assert.equal(j.buyChoose2('cand-timisoara').ok, true); assert.equal(j.buy.step, 5);
  run(300); assert.equal(g.state.bohun.at, HOME.buy); assert.ok(j.buy.step === 6 || j.buy.step === 'done');
  run(200); assert.equal(j.buy.step, 'done');
});
ok('reset and again clear the journeys', () => {
  const { g, j, run } = setup(); j.sellChoose('SOFTWARE', 'us'); j.buyChoose('SOFTWARE'); run(4);
  j.again('sell'); assert.equal(j.sell.step, 1); assert.equal(j.buy.step, 3);
  g.reset(); assert.equal(j.buy.step, 1); assert.equal(j.hidden('cand-gdansk'), true);
});
console.log(`${n} atlas checks passed`);
{
  const a = fs.readFileSync(new URL('../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs', import.meta.url));
  const b = fs.readFileSync(new URL('../public/atlas/vendor/maplibre-gl-worker.mjs', import.meta.url));
  assert.ok(a.equals(b), 'public/atlas/vendor/maplibre-gl-worker.mjs is stale: run node scripts/copy-maplibre-worker.mjs');
  console.log('ok  maplibre worker copy matches the installed version');
}
