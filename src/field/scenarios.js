// Two guided scenarios for /field.html, one per lens. Framework-free (no Phaser, no DOM) so scripts/test-field.mjs can run them.
// I NEED  -> "Найдите поставщика": research a category, Bohun checks candidates, the fitting one supplies your factory.
// I HAVE  -> "Выйдите на рынок": pick a market, Bohun meets the local partner, your factory supplies that market.
// Everything is a DEMONSTRATION: test positions, generic candidates, verdicts are scripted, no real companies or deals.
import * as base from '../flows/data/network.js';

// extra test nodes for the "find a supplier" scenario; hidden until research is done
export const EXTRA_NODES = [
  { id: 'cand-brno', type: 'factory-m', name: 'Кандидат', place: 'Брно (тест)', lon: 16.6, lat: 49.2 },
  { id: 'cand-rzeszow', type: 'factory-s', name: 'Кандидат', place: 'Жешув (тест)', lon: 22.0, lat: 50.04 },
  { id: 'cand-zhytomyr', type: 'factory-s', name: 'Кандидат', place: 'Житомир (тест)', lon: 28.66, lat: 50.25 },
];
export const EXTRA_EDGES = [
  { id: 'x1', mode: 'road', a: 'cand-brno', b: 'st-krakow' },
  { id: 'x2', mode: 'road', a: 'cand-rzeszow', b: 'st-lviv' },
  { id: 'x3', mode: 'road', a: 'cand-rzeszow', b: 'st-krakow' },
  { id: 'x4', mode: 'road', a: 'cand-zhytomyr', b: 'st-kyiv' },
];
export const fieldData = { ...base, nodes: [...base.nodes, ...EXTRA_NODES], edges: [...base.edges, ...EXTRA_EDGES] };

export const CATEGORY = 'PROPULSION'; // one of the approved Current Interest signals
const CANDIDATES = {
  'cand-brno': { fit: false, verdict: 'Не подходит: работает в другой категории.' },
  'cand-zhytomyr': { fit: false, verdict: 'Пока не подходит: свободных мощностей сейчас нет.' },
  'cand-rzeszow': { fit: true, verdict: 'Подходит: категория совпадает, мощности есть.' },
};
export const FIT = 'cand-rzeszow';
export const NEED_HOME = 'fac-dnipro';
export const HAVE_HOME = 'fac-zap';
export const MARKETS = {
  ua: { label: 'Украина', partner: 'st-kyiv', bank: 'bank-kyiv' },
  eu: { label: 'ЕС', partner: 'st-frankfurt', bank: 'bank-frankfurt' },
  us: { label: 'США', partner: 'port-newyork', bank: 'bank-newyork' },
};
const RESEARCH_SEC = 3;
const NEED_LOADS = 2, HAVE_LOADS = 2;

export function createScenarios(game, { onChange = () => {} } = {}) {
  let need, have;
  function reset() {
    need = { research: 0, t: 0, visited: new Set(), flowId: null, delivered: 0, done: false };
    have = { market: null, met: new Set(), flowId: null, delivered: 0, done: false, results: [], opt: null };
  }
  reset();

  const hidden = (id) => id in CANDIDATES && need.research < 2;
  const fitFound = () => need.visited.has(FIT);

  // run one scenario again; the market scenario keeps its results and the partners already met
  function again(lens) {
    if (lens === 'need') { if (need.flowId) return { ok: false, reason: 'busy' }; need = { research: 0, t: 0, visited: new Set(), flowId: null, delivered: 0, done: false }; }
    else { if (have.flowId) return { ok: false, reason: 'busy' }; Object.assign(have, { market: null, flowId: null, delivered: 0, done: false, opt: null }); }
    onChange('again'); return { ok: true };
  }
  function research() {
    if (need.research) return { ok: false, reason: need.research === 1 ? 'running' : 'done' };
    need.research = 1; need.t = 0; onChange('researchStarted'); return { ok: true };
  }
  function tick(dt) {
    if (need.research === 1) { need.t += dt; if (need.t >= RESEARCH_SEC) { need.research = 2; onChange('researchDone'); } }
  }
  function chooseMarket(m) {
    if (!MARKETS[m]) return { ok: false, reason: 'unknown-market' };
    if (have.flowId) return { ok: false, reason: 'busy' };
    have.market = m; have.done = false; have.delivered = 0; onChange('marketChosen'); return { ok: true };
  }
  // the scenario rules on top of the flows rules: a closed market needs the partner meeting first
  function canFlow(from, to) {
    if (hidden(from) || hidden(to)) return { ok: false, reason: 'hidden' };
    const m = Object.entries(MARKETS).find(([, v]) => v.bank === to);
    if (from === HAVE_HOME && m && !have.met.has(m[0])) return { ok: false, reason: 'partner-first', market: m[0] };
    if (from in CANDIDATES && !CANDIDATES[from].fit && need.visited.has(from)) return { ok: false, reason: 'not-fit' };
    return { ok: true };
  }
  function flowStarted(flowId, from, to, opt) {
    if (from === FIT && to === NEED_HOME && !need.done && fitFound()) { need.flowId = flowId; need.delivered = 0; }
    if (from === HAVE_HOME && have.market && to === MARKETS[have.market].bank && !have.done) { have.flowId = flowId; have.delivered = 0; have.opt = opt; }
  }
  function onEvent(type, p) {
    if (type === 'reset') { reset(); return; }
    if (type === 'bohunArrived') {
      if (p.at in CANDIDATES && need.research === 2) need.visited.add(p.at);
      for (const [k, v] of Object.entries(MARKETS)) if (v.partner === p.at) have.met.add(k);
    }
    if (type === 'flowStopped') { if (p.flowId === need.flowId && !need.done) need.flowId = null; if (p.flowId === have.flowId && !have.done) have.flowId = null; }
    if (type === 'cargoArrived') {
      if (p.flowId === need.flowId && !need.done && ++need.delivered >= NEED_LOADS) { need.done = true; const id = need.flowId; need.flowId = null; onChange('needDone', id); }
      if (p.flowId === have.flowId && !have.done && ++have.delivered >= HAVE_LOADS) {
        have.done = true; const id = have.flowId; have.flowId = null;
        have.results.push({ market: have.market, label: have.opt?.label, modes: have.opt?.modes ?? [], days: have.opt?.days, price: have.opt?.price });
        onChange('haveDone', id);
      }
    }
  }
  // current step: 1-based index, or 'done'
  function needStage() {
    if (need.done) return 'done';
    if (need.flowId) return 4;
    if (fitFound()) return 3;
    if (need.research === 2) return 2;
    return 1;
  }
  function haveStage() {
    if (have.done) return 'done';
    if (have.flowId) return 4;
    if (have.market && have.met.has(have.market)) return 3;
    if (have.market) return 2;
    return 1;
  }
  function info(id) {
    if (id in CANDIDATES) return need.visited.has(id) ? { ...CANDIDATES[id], checked: true } : { checked: false, verdict: 'Богун ещё не проверял этого кандидата. Нажмите — он поедет.' };
    if (id === NEED_HOME) return { mine: 'need' };
    if (id === HAVE_HOME) return { mine: 'have' };
    const m = Object.entries(MARKETS).find(([, v]) => v.partner === id);
    if (m) return { partner: m[0], met: have.met.has(m[0]) };
    return null;
  }
  return {
    get need() { return need; }, get have() { return have; },
    reset, again, research, tick, chooseMarket, canFlow, flowStarted, onEvent, needStage, haveStage, hidden, info, fitFound,
    candidates: Object.keys(CANDIDATES), researchProgress: () => (need.research === 2 ? 1 : need.research === 1 ? Math.min(1, need.t / RESEARCH_SEC) : 0),
  };
}
