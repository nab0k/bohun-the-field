// "Journeys" for /atlas.html: how working with Bohun looks, per visitor intent (Notion: «Перезапуск», decisions of 08.10.2026).
// I HAVE -> SELL (ENTER), I NEED -> BUY (SOURCE). Steps follow the approved HOW WE WORK: understand, map, qualify, engage, support.
// Framework-free (no map library, no DOM), driven by the flows rules in src/flows/core.js; tested in scripts/test-atlas.mjs.
// Everything is a DEMONSTRATION: generic actors and experts, scripted findings, conditional days and units, no real companies or deals.
import * as base from '../flows/data/network.js';

export const CATEGORIES = ['PROPULSION', 'COMMUNICATIONS', 'UAS / UGV', 'COMPONENTS', 'INDUSTRIAL CAPACITY', 'SOFTWARE', 'TESTING & VALIDATION'];
export const HOME = { sell: 'fac-zap', buy: 'fac-dnipro' };

export const MARKETS = {
  ua: { label: 'Украина', partner: 'st-kyiv', bank: 'bank-kyiv' },
  eu: { label: 'ЕС', partner: 'st-frankfurt', bank: 'bank-frankfurt' },
  us: { label: 'США', partner: 'port-newyork', bank: 'bank-newyork' },
};
// isolated nodes (no corridors): Bohun rides to them, goods never do
const EXPERTS = {
  ua: [['exp-ua-1', 31.4, 50.75], ['exp-ua-2', 29.6, 50.05]],
  eu: [['exp-eu-1', 7.6, 50.75], ['exp-eu-2', 9.9, 49.5]],
  us: [['exp-us-1', -72.9, 41.35], ['exp-us-2', -75.3, 40.0]],
};
const ACTORS = {
  ua: [['act-ua-buy', 32.1, 50.2], ['act-ua-dist', 30.0, 49.6], ['act-ua-test', 31.0, 51.2]],
  eu: [['act-eu-buy', 8.9, 51.1], ['act-eu-dist', 7.2, 49.9], ['act-eu-test', 10.4, 50.4]],
  us: [['act-us-buy', -73.6, 41.8], ['act-us-dist', -74.9, 41.2], ['act-us-test', -75.8, 40.6]],
};
export const INSIGHTS = [
  'Покупатели в этой категории ждут сертификацию и испытания на месте.',
  'Без местного партнёра входить долго: цикл закупки 6–12 месяцев.',
];
export const ACTOR_ROLE = { buy: 'Покупатель категории (условный)', dist: 'Дистрибьютор (условный)', test: 'Испытательная площадка (условная)' };
export const OFFERS = [
  { id: 'tender', label: 'Конкурс', text: 'На рынке объявлен конкурс в вашей категории (демо). Bohun поможет собрать заявку.' },
  { id: 'dist', label: 'Дистрибьютор', text: 'Дистрибьютор готов взять продукт в линейку (демо). Bohun организует знакомство.' },
  { id: 'pilot', label: 'Пилотный проект', text: 'Покупатель готов к пилоту (демо). Bohun поможет договориться об условиях.' },
];

export const CANDIDATES = {
  'cand-brno': { lon: 16.6, lat: 49.2, place: 'Брно (тест)', fit: false, verdict: 'Не подходит: работает в другой категории.' },
  'cand-rzeszow': { lon: 22.0, lat: 50.04, place: 'Жешув (тест)', fit: true, verdict: 'Подходит: категория совпадает, мощности есть.' },
  'cand-zhytomyr': { lon: 28.66, lat: 50.25, place: 'Житомир (тест)', fit: false, verdict: 'Пока не подходит: свободных мощностей сейчас нет.' },
  'cand-gdansk': { lon: 18.6, lat: 54.35, place: 'Гданьск (тест)', fit: false, verdict: 'Нужна проверка соответствия до любых переговоров.' },
  'cand-timisoara': { lon: 21.2, lat: 45.75, place: 'Тимишоара (тест)', fit: true, verdict: 'Подходит: опыт в категории, есть свободные мощности.' },
};

const extraNodes = [
  ...Object.entries(CANDIDATES).map(([id, c]) => ({ id, type: id === 'cand-brno' ? 'factory-m' : 'factory-s', name: 'Кандидат', place: c.place, lon: c.lon, lat: c.lat })),
  ...Object.values(EXPERTS).flat().map(([id, lon, lat]) => ({ id, type: 'expert', name: 'Эксперт', place: 'Эксперт по рынку (условный)', lon, lat })),
  ...Object.entries(ACTORS).flatMap(([, list]) => list.map(([id, lon, lat]) => ({ id, type: 'actor', role: id.split('-')[2], name: ACTOR_ROLE[id.split('-')[2]], place: 'Участник рынка (условный)', lon, lat }))),
];
const extraEdges = [
  { id: 'x1', mode: 'road', a: 'cand-brno', b: 'st-krakow' },
  { id: 'x2', mode: 'road', a: 'cand-rzeszow', b: 'st-lviv' },
  { id: 'x3', mode: 'road', a: 'cand-rzeszow', b: 'st-krakow' },
  { id: 'x4', mode: 'road', a: 'cand-zhytomyr', b: 'st-kyiv' },
  { id: 'x5', mode: 'road', a: 'cand-gdansk', b: 'st-warsaw' },
  { id: 'x6', mode: 'road', a: 'cand-timisoara', b: 'st-bucharest' },
];
export const atlasData = {
  ...base,
  TYPE_RU: { ...base.TYPE_RU, expert: 'Эксперт', actor: 'Участник рынка' },
  TYPE_ROLE: { ...base.TYPE_ROLE, expert: 'Знает рынок изнутри (условный).', actor: 'Появляется после разведки (условный).' },
  nodes: [...base.nodes, ...extraNodes],
  edges: [...base.edges, ...extraEdges],
};

export const SELL_STEPS = [
  'Выберите, что вы продаёте и на какой рынок.',
  'Разведка: отправьте Богуна на рынок.',
  'Эксперты: Богун советуется с двумя экспертами.',
  'Мониторинг: Богун ищет возможности и приносит предложения.',
  'Действие: выберите путь, по которому пойдут поставки.',
  'Сопровождение: дождитесь двух поставок.',
];
export const BUY_STEPS = [
  'Выберите, что вам нужно.',
  'Разведка: Bohun ищет, кто это делает.',
  'Проверка: Богун проверяет кандидатов, пока не найдёт двух подходящих.',
  'Короткий список: сравните двух и выберите одного.',
  'Знакомство: Богун организует встречу.',
  'Поставки: дождитесь двух поставок.',
];
const RESEARCH_SEC = 3, MONITOR_SEC = 4, LOADS = 2;

export function createJourneys(game, { onChange = () => {} } = {}) {
  let sell, buy;
  const freshSell = () => ({ step: 1, category: null, market: null, scouted: false, experts: new Set(), monitor: 0, offers: false, offer: null, flowId: null, delivered: 0, done: false });
  const freshBuy = () => ({ step: 1, category: null, research: 0, t: 0, visited: new Set(), chosen: null, meeting: 0, flowId: null, delivered: 0, done: false });
  function reset() { sell = freshSell(); buy = freshBuy(); }
  reset();
  const ch = (t, p) => onChange(t, p);
  const ok = (extra = {}) => ({ ok: true, ...extra });
  const no = (reason) => ({ ok: false, reason });

  // which map objects are visible right now
  function hidden(id) {
    if (id in CANDIDATES) return buy.research < 2;
    if (id.startsWith('exp-') || id.startsWith('act-')) { const m = id.split('-')[1]; return !(sell.scouted && sell.market === m); }
    return false;
  }
  const experts = () => (sell.market ? EXPERTS[sell.market].map((e) => e[0]) : []);
  const actors = () => (sell.market ? ACTORS[sell.market].map((e) => e[0]) : []);
  const fits = () => [...buy.visited].filter((id) => CANDIDATES[id].fit);

  // ---- SELL ----
  function sellChoose(category, market) {
    if (sell.step !== 1) return no('step');
    if (!CATEGORIES.includes(category) || !MARKETS[market]) return no('bad-choice');
    Object.assign(sell, { category, market, step: 2 }); ch('sell:2'); return ok();
  }
  function sellScout() {
    if (sell.step !== 2) return no('step');
    const r = game.moveBohun(MARKETS[sell.market].partner);
    return r.ok ? ok() : r;
  }
  function sellVisitExpert(id) {
    if (sell.step !== 3 || !experts().includes(id)) return no('step');
    return game.moveBohun(id);
  }
  function sellPickOffer(id) {
    if (sell.step !== 4 || !sell.offers || !OFFERS.some((o) => o.id === id)) return no('step');
    sell.offer = id; sell.step = 5; ch('sell:5'); return ok();
  }
  // step 5: the visitor picks a route option; the view starts the flow and reports it here
  function sellFlowStarted(flowId, from, to) {
    if (sell.step !== 5 || from !== HOME.sell || to !== MARKETS[sell.market].bank) return no('step');
    sell.flowId = flowId; sell.delivered = 0; sell.step = 6; ch('sell:6'); return ok();
  }

  // ---- BUY ----
  function buyChoose(category) {
    if (buy.step !== 1 || !CATEGORIES.includes(category)) return no('step');
    Object.assign(buy, { category, step: 2, research: 1, t: 0 }); ch('buy:2'); return ok();
  }
  function buyVisit(id) {
    if (buy.step !== 3 || !(id in CANDIDATES)) return no('step');
    return game.moveBohun(id);
  }
  function buyChoose2(id) {
    if (buy.step !== 4 || !fits().includes(id)) return no('step');
    buy.chosen = id; buy.step = 5; buy.meeting = 1;
    const r = game.moveBohun(id); // leg 1: to the supplier, then to your factory
    if (!r.ok && r.reason !== 'busy') return r;
    if (r.here) { buy.meeting = 2; game.moveBohun(HOME.buy); }
    ch('buy:5'); return ok();
  }

  function tick(dt) {
    if (buy.research === 1) { buy.t += dt; if (buy.t >= RESEARCH_SEC) { buy.research = 2; buy.step = 3; ch('buy:3'); } }
    if (sell.step === 4 && !sell.offers) { sell.monitor += dt; if (sell.monitor >= MONITOR_SEC) { sell.offers = true; ch('sell:offers'); } }
  }
  function onEvent(type, p) {
    if (type === 'reset') { reset(); return; }
    if (type === 'bohunArrived') {
      const at = p.at;
      if (sell.step === 2 && at === MARKETS[sell.market].partner) { sell.scouted = true; sell.step = 3; ch('sell:3'); }
      else if (sell.step === 3 && experts().includes(at)) { sell.experts.add(at); ch('sell:expert', at); if (sell.experts.size >= 2) { sell.step = 4; sell.monitor = 0; ch('sell:4'); } }
      if (buy.step === 3 && at in CANDIDATES) { buy.visited.add(at); ch('buy:verdict', at); if (fits().length >= 2) { buy.step = 4; ch('buy:4'); } }
      else if (buy.step === 5 && buy.meeting === 1 && at === buy.chosen) { buy.meeting = 2; game.moveBohun(HOME.buy); ch('buy:meeting'); }
      else if (buy.step === 5 && buy.meeting === 2 && at === HOME.buy) {
        buy.meeting = 3;
        const opt = game.routeOptions(buy.chosen, HOME.buy).find((o) => o.id === 'cheap') ?? game.routeOptions(buy.chosen, HOME.buy)[0];
        const r = game.startFlow(buy.chosen, HOME.buy, opt.id);
        if (r.ok) { buy.flowId = r.flowId; buy.delivered = 0; buy.step = 6; ch('buy:6'); } else ch('buy:blocked', r.reason);
      }
    }
    if (type === 'flowStopped') { if (p.flowId === sell.flowId && !sell.done) { sell.flowId = null; sell.step = 5; } if (p.flowId === buy.flowId && !buy.done) { buy.flowId = null; } }
    if (type === 'cargoArrived') {
      if (p.flowId === sell.flowId && !sell.done && ++sell.delivered >= LOADS) { sell.done = true; sell.step = 'done'; const id = sell.flowId; sell.flowId = null; ch('sell:done', id); }
      if (p.flowId === buy.flowId && !buy.done && ++buy.delivered >= LOADS) { buy.done = true; buy.step = 'done'; const id = buy.flowId; buy.flowId = null; ch('buy:done', id); }
    }
  }
  function again(which) { if (which === 'sell') sell = freshSell(); else buy = freshBuy(); ch(which + ':1'); }

  return {
    get sell() { return sell; }, get buy() { return buy; },
    reset, again, tick, onEvent, hidden, experts, actors, fits,
    sellChoose, sellScout, sellVisitExpert, sellPickOffer, sellFlowStarted,
    buyChoose, buyVisit, buyChoose2,
    researchProgress: () => (buy.research === 2 ? 1 : buy.research === 1 ? Math.min(1, buy.t / RESEARCH_SEC) : 0),
    monitorProgress: () => (sell.offers ? 1 : Math.min(1, sell.monitor / MONITOR_SEC)),
  };
}
