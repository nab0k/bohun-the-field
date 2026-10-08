// /field.html — v0.4: the v0.3 slice's look and HUD on top of the tested "Flows" rules (src/flows/core.js, unchanged)
// and real geography (public/field/hero-geo.json). All art is drawn in code and is temporary.
import Phaser from 'phaser';
import { createFlows, WORLD, MODES, isSource, pointAt, project } from '../flows/core.js';
import * as data from '../flows/data/network.js';
import assets from '../game/data/assets.json' with { type: 'json' };
import { paintHero } from './terrain.js';

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const dpr = Number(params.get('dpr')) || Math.min(window.devicePixelRatio || 1, 2);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const rider = assets.rider;
const REASON = {
  limit: 'Уже три потока. Остановите один, чтобы начать новый.',
  duplicate: 'Такой поток уже идёт.',
  'not-a-source': 'Потоки начинаются только из шахты или завода.',
  'same-node': 'Выберите другое место.',
  'no-route': 'Туда нет пути по коридорам.',
  busy: 'Богун ещё в пути.',
};
const LENS_LINE = { need: 'I NEED SOMETHING · Find capabilities, companies and partners.', have: 'I HAVE SOMETHING · Explore markets, partnerships and commercial opportunities.' };
const lensHit = (lens, type) => (lens === 'need' ? isSource(type) : type === 'bank' || type === 'port');

const [geo, worldSvg] = await Promise.all([fetch('/field/hero-geo.json').then((r) => r.json()), fetch('/flows/world-tilt-base.svg').then((r) => r.text())]);
// the low-detail world map in the v0.3 palette (same file as the flows page, recoloured on the fly)
const worldUrl = URL.createObjectURL(new Blob([worldSvg.replace(/#2f6f95/g, '#263d3f').replace(/#e6cf6e/g, '#46553a').replace(/#8fbf78/g, '#39432f').replace(/#cdbd8c/g, '#30342a').replace(/#6b5f3c/g, '#5d604a')], { type: 'image/svg+xml' }));

// ---------- the guided task (same as the flows page): 3 cargoes from the mine to the New York bank ----------
const M = { source: 'mine-kr', target: 'bank-newyork', need: 3 };
const STEP_TEXT = ['Нажмите на шахту (мигающее кольцо).', 'Нажмите «Направить поток отсюда».', 'Нажмите на банк в Нью-Йорке.', 'Выберите путь: быстро или дёшево.', 'Дождитесь трёх грузов и сравните.'];
let mission = { delivered: 0, flowId: null, opt: null, done: false, results: [] };
let ui = { selected: null, mode: 'idle', source: null, target: null, lens: 'need', explore: false };

let game, S;
const coins = [];
game = createFlows({ data, seed: 7, bohunStart: data.BOHUN_START, onEvent });
S = game.state;
window.__flows = game; // test hook

function stage() {
  if (mission.done) return 'done';
  if (mission.flowId && S.flows.some((f) => f.id === mission.flowId)) return 5;
  if (ui.mode === 'options' && ui.source === M.source && ui.target === M.target) return 4;
  if (ui.mode === 'pick' && ui.source === M.source) return 3;
  if (ui.selected === M.source && ui.mode === 'idle') return 2;
  return 1;
}
let lastStage = 0;

function onEvent(type, p) {
  if (!S) return;
  if (type === 'rejected') note(REASON[p.reason] ?? `Нельзя: ${p.reason}`);
  if (type === 'dossierRequested') { ui.selected = p.nodeId; renderCard(); }
  if (type === 'cargoArrived') {
    if (p.bank) window.__scene?.popCoins(p.toId);
    if (p.flowId === mission.flowId && !mission.done) {
      mission.delivered += 1;
      if (mission.delivered >= M.need) {
        mission.done = true;
        mission.results.push({ label: mission.opt.label, modes: mission.opt.modes, days: mission.opt.days, price: mission.opt.price, total: mission.opt.price * M.need });
        const id = mission.flowId; mission.flowId = null;
        setTimeout(() => game.stopFlow(id), 0);
      }
    }
  }
  if (type === 'flowStarted' || type === 'flowStopped') renderFlows();
  if (type === 'reset') { Object.assign(ui, { selected: null, mode: 'idle', source: null, target: null }); mission = { delivered: 0, flowId: null, opt: null, done: false, results: [] }; renderCard(); renderPick(); renderFlows(); }
}

let noteTimer;
function note(text) { const el = $('#note'); el.textContent = text; clearTimeout(noteTimer); noteTimer = setTimeout(() => (el.textContent = ''), 4500); }
const short = (place) => place.replace(' (тест)', '');
const modesText = (modes) => modes.map((m) => MODES[m].label).join(' + ');

// ---------- HTML layer ----------
const stepEls = STEP_TEXT.map((t, i) => {
  const li = document.createElement('li');
  li.innerHTML = '<span class="n"></span><span class="t"></span>';
  li.querySelector('.n').textContent = String(i + 1);
  li.querySelector('.t').textContent = t;
  $('#m-steps').append(li);
  return li;
});
const sig = {};
const setIf = (key, v, fn) => { if (sig[key] !== v) { sig[key] = v; fn(); } };

function renderMission() {
  const st = stage();
  stepEls.forEach((li, i) => { const c = st === 'done' ? 'done' : i + 1 < st ? 'done' : i + 1 === st ? 'current' : ''; setIf('s' + i, c, () => (li.className = c)); });
  const prog = $('#m-progress');
  prog.hidden = st !== 5;
  if (st === 5) { prog.querySelector('i').style.width = `${(mission.delivered / M.need) * 100}%`; prog.querySelector('span').textContent = `ДОСТАВЛЕНО ${mission.delivered} ИЗ ${M.need}`; }
  const res = $('#m-result');
  res.hidden = st !== 'done';
  if (st === 'done') setIf('res', mission.results.length, () => {
    const last = mission.results.at(-1);
    res.replaceChildren();
    const h = document.createElement('div'); h.innerHTML = '<b>Готово.</b> Три груза доставлены.'; res.append(h);
    const ul = document.createElement('ul');
    mission.results.forEach((r, i) => { const li = document.createElement('li'); li.textContent = `Попытка ${i + 1}: «${r.label}» (${modesText(r.modes)}): ≈ ${r.days} усл. дн. на груз, всего ${r.total} усл. ед.`; ul.append(li); });
    res.append(ul);
    const tip = document.createElement('div');
    tip.textContent = mission.results.length < 2 ? 'Попробуйте другой путь и сравните: что для вас важнее, время или цена?' : (() => { const a = mission.results.at(-2), b = last; return b.days < a.days ? 'Этот путь быстрее, но дороже.' : b.days > a.days ? 'Этот путь дольше, но дешевле.' : 'Результат тот же.'; })();
    res.append(tip);
    const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = 'ПОПРОБОВАТЬ ДРУГОЙ ПУТЬ';
    btn.addEventListener('click', () => { mission.done = false; mission.delivered = 0; mission.flowId = null; Object.assign(ui, { selected: null, mode: 'idle', source: null, target: null }); renderCard(); renderPick(); window.__scene?.home(); });
    res.append(btn);
  });
  $('#k-flow').classList.toggle('hint', st === 2);
  if (st !== lastStage) { lastStage = st; window.__scene?.onStage(st); }
}

function renderCard() {
  const n = ui.selected && S.world.nodes[ui.selected];
  $('#sel').hidden = !n;
  if (!n) return;
  $('#k-type').textContent = data.TYPE_RU[n.type].toUpperCase();
  const b = S.bohun;
  $('#k-status').textContent = b.status === 'moving' && b.to === n.id ? 'БОГУН В ПУТИ' : b.at === n.id ? 'БОГУН ЗДЕСЬ' : '';
  $('#k-name').textContent = n.name;
  $('#k-place').textContent = `${short(n.place)} · ДЕМО: тестовое место`;
  $('#k-role').textContent = data.TYPE_ROLE[n.type];
  $('#k-flow').hidden = !isSource(n.type) || ui.mode !== 'idle';
}
function renderPick() {
  $('#pick').hidden = ui.mode !== 'pick';
  $('#options').hidden = ui.mode !== 'options';
  if (ui.mode !== 'idle') $('#sel').hidden = false;
  if (ui.mode === 'idle') renderCard();
  if (ui.mode === 'options') {
    const a = S.world.nodes[ui.source], b = S.world.nodes[ui.target];
    $('#o-title').textContent = `${a.name} (${short(a.place)}) → ${b.name} (${short(b.place)})`;
    const list = $('#o-list');
    list.replaceChildren();
    for (const o of game.routeOptions(ui.source, ui.target)) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'opt';
      btn.append(`${o.label.toUpperCase()}: ${modesText(o.modes)}`);
      const small = document.createElement('small');
      small.textContent = `≈ ${o.days} усл. дн. и ${o.price} усл. ед. за каждый груз` + (mission.results.some((r) => r.label === o.label) && ui.source === M.source && ui.target === M.target ? '. Этот путь вы уже пробовали' : '');
      btn.append(small);
      btn.addEventListener('click', () => {
        const r = game.startFlow(ui.source, ui.target, o.id);
        if (r.ok) {
          if (ui.source === M.source && ui.target === M.target && !mission.done) { mission.flowId = r.flowId; mission.delivered = 0; mission.opt = o; }
          ui.mode = 'idle'; renderPick(); renderCard(); note('Поток запущен: грузы поедут по выбранному пути.');
        }
      });
      list.append(btn);
    }
    list.querySelector('button')?.focus({ preventScroll: true });
  }
}
function renderFlows() {
  $('#f-count').textContent = String(S.flows.length);
  const ul = $('#f-list');
  ul.replaceChildren();
  if (!S.flows.length) { const li = document.createElement('li'); li.className = 'empty'; li.textContent = 'Пока нет. Поток начинается в шахте или на заводе.'; ul.append(li); return; }
  for (const f of S.flows) {
    const li = document.createElement('li');
    const a = S.world.nodes[f.fromId], b = S.world.nodes[f.toId];
    const t = document.createElement('span');
    t.textContent = `${short(a.place)} → ${short(b.place)}: ${f.label}, ≈ ${f.days} усл. дн.`;
    const x = document.createElement('button');
    x.type = 'button'; x.textContent = 'СТОП'; x.setAttribute('aria-label', 'Остановить поток');
    x.addEventListener('click', () => game.stopFlow(f.id));
    li.append(t, x); ul.append(li);
  }
}
$('#k-flow').addEventListener('click', () => { ui.mode = 'pick'; ui.source = ui.selected; window.__scene?.markTargets(); renderPick(); $('#k-flow').hidden = true; });
$('#pick-cancel').addEventListener('click', cancelPick);
$('#o-cancel').addEventListener('click', cancelPick);
$('#sel-close').addEventListener('click', () => { if (ui.mode !== 'idle') return cancelPick(); ui.selected = null; renderCard(); });
function cancelPick() { ui.mode = 'idle'; ui.source = ui.target = null; renderPick(); renderCard(); }
window.addEventListener('keydown', (e) => e.key === 'Escape' && ui.mode !== 'idle' && cancelPick());
$('#reset').addEventListener('click', () => { game.reset(); window.__scene?.home(); note('Сначала.'); });
$('#all').addEventListener('click', () => window.__scene?.toggleAll());
function setLens(v) {
  ui.lens = v;
  $('#lens-need').classList.toggle('active', v === 'need'); $('#lens-have').classList.toggle('active', v === 'have');
  $('#lens-line').textContent = LENS_LINE[v];
  note(v === 'need' ? 'Подсвечены места, где производят: шахты и заводы.' : 'Подсвечены места, куда продают: банки и порты.');
}
$('#lens-need').addEventListener('click', () => setLens('need'));
$('#lens-have').addEventListener('click', () => setLens('have'));
function setExplore(on) {
  ui.explore = on;
  document.body.classList.toggle('explore', on);
  $('#explore').setAttribute('aria-pressed', String(on));
  $('#explore').textContent = on ? 'ВЕРНУТЬ ПРЕЗЕНТАЦИЮ' : 'ДВИГАТЬ КАРТУ';
  $('#mode-label').textContent = on ? 'РЕЖИМ / КАРТУ ДВИГАЕТЕ ВЫ' : 'РЕЖИМ / ПРЕЗЕНТАЦИЯ';
  window.__scene?.layout();
  if (!on) window.__scene?.home();
}
$('#explore').addEventListener('click', () => setExplore(!ui.explore));

window.__choose = (id) => chooseNode(id); // test hook
function chooseNode(id) {
  if (ui.mode === 'pick') {
    if (id === ui.source) return note('Выберите другое место.');
    if (!game.routeOptions(ui.source, id).length) return note('Туда нет пути по коридорам.');
    ui.target = id; ui.mode = 'options'; renderPick();
    return;
  }
  ui.selected = id; renderCard(); game.moveBohun(id); renderCard();
}

// ---------- drawing helpers (iso boxes, 2:1 footprint, light from the left) ----------
const hex = (s) => parseInt(s.slice(1), 16);
const shade = (c, f) => { const r = Math.min(255, ((c >> 16) & 255) * f), g = Math.min(255, ((c >> 8) & 255) * f), b = Math.min(255, (c & 255) * f); return (r << 16) | (g << 8) | b; };
let OW = 1; // outline width in world px, set per frame (constant on screen)
function face(g, pts, col, a) { g.fillStyle(col, a); g.fillPoints(pts, true); g.lineStyle(OW, 0x14180f, 0.75 * a); g.strokePoints(pts, true); }
const up = (p, h) => ({ x: p.x, y: p.y - h });
function box(g, cx, cy, w, d, h, col, u, a, roof) {
  const L = { x: cx - ((w + d) / 2) * u, y: cy + ((d - w) / 4) * u }, B = { x: cx + ((w - d) / 2) * u, y: cy + ((w + d) / 4) * u };
  const R = { x: cx + ((w + d) / 2) * u, y: cy + ((w - d) / 4) * u }, T = { x: cx - ((w - d) / 2) * u, y: cy - ((w + d) / 4) * u }, H = h * u;
  if (h > 0.05) { face(g, [L, B, up(B, H), up(L, H)], shade(col, 1), a); face(g, [B, R, up(R, H), up(B, H)], shade(col, 0.7), a); }
  if (roof) {
    const ap = { x: cx, y: cy - H - roof.h * u }, rc = roof.col;
    face(g, [up(T, H), up(L, H), ap], shade(rc, 0.85), a); face(g, [up(R, H), up(T, H), ap], shade(rc, 0.62), a);
    face(g, [up(L, H), up(B, H), ap], shade(rc, 1.08), a); face(g, [up(B, H), up(R, H), ap], shade(rc, 0.78), a);
  } else face(g, [up(T, H), up(R, H), up(B, H), up(L, H)], shade(col, 1.18), a);
  return { L, B, R, T, H };
}
const EX = { x: 0.894, y: 0.447 }; // iso x axis, unit
function chimney(g, x, y, h, u, a, smokes, seed) {
  g.fillStyle(0x5a5048, a); g.fillRect(x - 1.3 * u, y - h * u, 2.6 * u, h * u);
  g.fillStyle(0x3b342e, a); g.fillRect(x - 1.5 * u, y - h * u - 0.8 * u, 3 * u, 1.2 * u);
  g.fillStyle(0x000000, 0.2 * a); g.fillRect(x, y - h * u, 1.3 * u, h * u);
  smokes.push({ x, y: y - h * u - 0.8 * u, seed });
}
const C = { brick: hex('#8a5a40'), steel: hex('#8b9298'), steel2: hex('#6f777c'), stone: hex('#cfc4a0'), gold: hex('#c9a24a'), roofRed: hex('#9a4a32'), roofBrown: hex('#7a5a3c'), earth: hex('#4a3d30'), earth2: hex('#5c4b38'), timber: hex('#6b5a45'), concrete: hex('#6c6e66'), tarmac: hex('#3a3d38'), sand: hex('#cbbd94') };

function drawBuilding(g, n, u, a, smokes, t) {
  const x = n.x, y = n.y;
  g.fillStyle(0x0b0e08, 0.28 * a); g.fillEllipse(x + 2 * u, y + 2 * u, 34 * u, 13 * u);
  const s = n.id.length;
  switch (n.type) {
    case 'mine': {
      box(g, x - 5 * u, y + 1 * u, 13, 11, 0, C.earth, u, a, { h: 7, col: C.earth2 });
      box(g, x + 7 * u, y + 2 * u, 8, 6, 5, C.timber, u, a, { h: 3, col: C.roofBrown });
      g.lineStyle(1.4 * u, 0x2a2620, a);
      g.lineBetween(x + 1 * u, y - 1 * u, x + 4 * u, y - 21 * u); g.lineBetween(x + 8 * u, y - 2 * u, x + 4 * u, y - 21 * u); g.lineBetween(x + 2.2 * u, y - 9 * u, x + 6.6 * u, y - 9 * u);
      const r = reduced ? 0 : t / 600;
      g.lineStyle(1.1 * u, 0xb8a272, a); g.strokeCircle(x + 4 * u, y - 21 * u, 2.8 * u);
      g.lineBetween(x + 4 * u + Math.cos(r) * 2.8 * u, y - 21 * u + Math.sin(r) * 2.8 * u, x + 4 * u - Math.cos(r) * 2.8 * u, y - 21 * u - Math.sin(r) * 2.8 * u);
      break;
    }
    case 'factory-s':
      box(g, x, y, 14, 10, 8, C.brick, u, a, { h: 3, col: C.roofBrown });
      chimney(g, x + 4 * u, y - 4 * u, 12, u, a, smokes, s);
      break;
    case 'factory-m':
      box(g, x, y, 20, 14, 10, C.steel, u, a);
      chimney(g, x - 3 * u, y - 9 * u, 13, u, a, smokes, s); chimney(g, x + 5 * u, y - 7 * u, 10, u, a, smokes, s + 3);
      g.fillStyle(C.roofRed, a); g.fillRect(x - 12 * u, y - 13 * u, 4 * u, 1.4 * u);
      break;
    case 'factory-l': {
      box(g, x + 9 * u, y - 5 * u, 12, 10, 16, C.steel2, u, a);
      const m = box(g, x - 3 * u, y + 2 * u, 24, 15, 11, C.steel, u, a);
      for (let i = 0; i < 4; i++) { const k = (i + 0.5) / 4, b = { x: m.L.x + (m.B.x - m.L.x) * k, y: m.L.y + (m.B.y - m.L.y) * k - m.H }; face(g, [{ x: b.x - 2.6 * u, y: b.y - 1.3 * u }, { x: b.x + 2.6 * u, y: b.y + 1.3 * u }, { x: b.x + 0.4 * u, y: b.y - 4.5 * u }], 0x9aa3a8, a); }
      chimney(g, x + 7 * u, y - 18 * u, 14, u, a, smokes, s); chimney(g, x + 11 * u, y - 16 * u, 12, u, a, smokes, s + 2); chimney(g, x - 8 * u, y - 8 * u, 9, u, a, smokes, s + 5);
      break;
    }
    case 'bank': {
      const b = box(g, x, y, 18, 14, 9, C.stone, u, a, { h: 5, col: C.gold });
      g.lineStyle(1.1 * u, 0x8c8366, a);
      for (let i = 1; i < 5; i++) { const k = i / 5, px = b.L.x + (b.B.x - b.L.x) * k, py = b.L.y + (b.B.y - b.L.y) * k; g.lineBetween(px, py - 0.5 * u, px, py - b.H + 1 * u); }
      const cy = y - 24 * u + (reduced ? 0 : Math.sin(t / 400) * 1.2 * u);
      g.fillStyle(0xf5c542, a); g.fillCircle(x, cy, 3.2 * u); g.lineStyle(1 * u, 0x8d6a28, a); g.strokeCircle(x, cy, 3.2 * u); g.lineBetween(x, cy - 1.8 * u, x, cy + 1.8 * u);
      break;
    }
    case 'port': {
      box(g, x, y + 2 * u, 24, 12, 2, C.concrete, u, a);
      box(g, x - 5 * u, y, 10, 8, 7, hex('#7d7456'), u, a, { h: 3, col: C.roofRed });
      g.lineStyle(1.5 * u, 0xd3a640, a);
      g.lineBetween(x + 6 * u, y + 1 * u, x + 6 * u, y - 20 * u); g.lineBetween(x + 6 * u, y - 20 * u, x + 16 * u, y - 16 * u); g.lineBetween(x + 6 * u, y - 14 * u, x + 16 * u, y - 16 * u);
      g.lineStyle(0.7 * u, 0x222222, a); g.lineBetween(x + 14 * u, y - 15 * u, x + 14 * u, y - 8 * u);
      g.fillStyle(0x8a5a40, a); g.fillRect(x + 12.5 * u, y - 8 * u, 3 * u, 2 * u);
      break;
    }
    case 'station': {
      g.lineStyle(1 * u, 0x2b2a24, a);
      for (const o of [-1.6, 1.6]) g.lineBetween(x - EX.x * 20 * u, y - EX.y * 20 * u + o * u + 4 * u, x + EX.x * 20 * u, y + EX.y * 20 * u + o * u + 4 * u);
      box(g, x, y - 1 * u, 22, 8, 6, C.sand, u, a, { h: 4, col: C.roofRed });
      break;
    }
    default: { // airfield
      const r = box(g, x + 2 * u, y + 4 * u, 42, 7, 0.3, C.tarmac, u, a);
      g.lineStyle(0.9 * u, 0xe6e0c7, 0.8 * a);
      for (let i = 0; i < 6; i++) { const k0 = 0.12 + i * 0.13, k1 = k0 + 0.06, mx = (r.T.x + r.B.x) / 2, my = (r.T.y + r.B.y) / 2 - r.H, lx = mx - EX.x * 21 * u, ly = my - EX.y * 21 * u; g.lineBetween(lx + EX.x * 42 * u * k0, ly + EX.y * 42 * u * k0, lx + EX.x * 42 * u * k1, ly + EX.y * 42 * u * k1); }
      box(g, x + 9 * u, y - 7 * u, 12, 9, 6, C.steel, u, a, { h: 3, col: C.steel2 });
      box(g, x - 11 * u, y - 4 * u, 4, 4, 12, hex('#9aa0a0'), u, a);
      box(g, x - 11 * u, y - 16 * u, 6, 6, 3, hex('#7fb2cc'), u, a);
    }
  }
}

function drawMover(g, kind, p, s, a, accent) {
  const len = Math.hypot(p.ax, p.ay) || 1, ux = p.ax / len, uy = p.ay / len, vx = -uy, vy = ux;
  const P = (l, w) => ({ x: p.x + (ux * l + vx * w) * s, y: p.y + (uy * l + vy * w) * s });
  const poly = (pts, fill) => { g.fillStyle(fill, a); g.fillPoints(pts, true); g.lineStyle((accent ? 1.6 : 0.9) * s, accent ? 0xffd54a : 0x14180f, a); g.strokePoints(pts, true); };
  g.fillStyle(0x0b0e08, 0.3 * a);
  if (kind === 'plane') g.fillEllipse(p.x + 4 * s, p.y + 9 * s, 12 * s, 4 * s); else g.fillEllipse(p.x + 1 * s, p.y + 1.5 * s, 12 * s, 4.5 * s);
  if (kind === 'train') for (let i = 0; i < 3; i++) poly([P(-6 + i * 7, -2.4), P(-1 + i * 7, -2.4), P(-1 + i * 7, 2.4), P(-6 + i * 7, 2.4)], i === 2 ? 0x8f3a2a : 0x6b5a45);
  else if (kind === 'truck') { poly([P(-5, -2.8), P(2, -2.8), P(2, 2.8), P(-5, 2.8)], 0x5f6a48); poly([P(2, -2.4), P(5, -2.4), P(5, 2.4), P(2, 2.4)], 0xb8ad6a); }
  else if (kind === 'wagon') { poly([P(-4.5, -3.2), P(3, -3.2), P(3, 3.2), P(-4.5, 3.2)], 0x9a6b3a); poly([P(4, -1.2), P(8, -1.2), P(8, 1.2), P(4, 1.2)], 0x4a3a2a); }
  else if (kind === 'ship') poly([P(-8, -3.2), P(5, -3.2), P(9, 0), P(5, 3.2), P(-8, 3.2)], 0xd8d6c8);
  else poly([P(9, 0), P(-4, -7.5), P(-1, 0), P(-4, 7.5)], 0xe6e0c7);
}
function dashed(g, pts, dash, gap, offset = 0) {
  const per = dash + gap; let o = ((offset % per) + per) % per;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], len = Math.hypot(b.x - a.x, b.y - a.y); if (!len) continue;
    const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
    for (let d = -o; d < len; d += per) { const s = Math.max(0, d), e = Math.min(d + dash, len); if (e > s) g.lineBetween(a.x + ux * s, a.y + uy * s, a.x + ux * e, a.y + uy * e); }
    o = (o + len) % per;
  }
}
function ties(g, pts, step, half) {
  let carry = 0;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], len = Math.hypot(b.x - a.x, b.y - a.y); if (!len) continue;
    const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
    for (let d = carry; d < len; d += step) { const cx = a.x + ux * d, cy = a.y + uy * d; g.lineBetween(cx - uy * half, cy + ux * half, cx + uy * half, cy - ux * half); }
    carry = (carry - len) % step; if (carry < 0) carry += step;
  }
}

// ---------- labels: a few real places (Natural Earth) and region names ----------
const CITY_RU = { Kyiv: 'Киев', Lviv: 'Львов', Odessa: 'Одесса', Kharkiv: 'Харьков', Dnipro: 'Днепр', Warsaw: 'Варшава', Kraków: 'Краков', Krakow: 'Краков', Bucharest: 'Бухарест', Budapest: 'Будапешт', Berlin: 'Берлин', Vienna: 'Вена', Prague: 'Прага', Istanbul: 'Стамбул', Chisinau: 'Кишинёв', Vilnius: 'Вильнюс', Bratislava: 'Братислава', Sofia: 'София' };
const REGIONS = [['УКРАИНА', 27.6, 51.55, 1.6], ['ЧЁРНОЕ МОРЕ', 34.2, 43.3, 1.3], ['ПОЛЬША', 18.6, 53.4, 1], ['РУМЫНИЯ', 24.4, 45.9, 1], ['ВЕНГРИЯ', 19.0, 47.0, 0.9], ['МОЛДОВА', 28.4, 47.6, 0.8]];

class FieldScene extends Phaser.Scene {
  constructor() { super('field'); }
  preload() {
    this.load.svg('world', worldUrl, { width: 4096, height: Math.round((4096 * WORLD.height) / WORLD.width) });
    this.load.image('rider', rider.url);
  }
  create() {
    window.__scene = this;
    this.add.image(0, 0, 'world').setOrigin(0, 0).setDisplaySize(WORLD.width, WORLD.height).setDepth(0);
    const max = this.renderer.getMaxTextureSize?.() ?? 4096, bw = geo.box.x1 - geo.box.x0;
    const res = Math.max(2, Math.min(6, Math.floor(((max - 64) / bw) * 10) / 10));
    const t0 = performance.now();
    const hero = paintHero(geo, res, { southY: project(0, 47.4).y });
    this.paintMs = Math.round(performance.now() - t0); this.heroRes = res;
    this.textures.addCanvas('hero', hero.canvas);
    this.heroImg = this.add.image(geo.box.x0, geo.box.y0, 'hero').setOrigin(0, 0).setDisplaySize(bw, geo.box.y1 - geo.box.y0).setDepth(1);

    this.lines = this.add.graphics().setDepth(2);
    this.nodesG = this.add.graphics().setDepth(4);
    this.dyn = this.add.graphics().setDepth(5);
    this.miniGfx = this.add.graphics().setDepth(6);
    const txt = (size, color, extra = {}) => ({ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: size + 'px', fontStyle: '800', color, stroke: '#0d100b', strokeThickness: 4, resolution: dpr * 2, ...extra });
    this.cityLabels = geo.cities.filter((c) => CITY_RU[c.name]).map((c) => this.add.text(c.x, c.y + 3, CITY_RU[c.name], txt(11, '#d8d3b0')).setOrigin(0.5, 0).setDepth(3).setAlpha(0.8));
    this.regionLabels = REGIONS.map(([name, lon, lat, sz]) => { const p = project(lon, lat); return this.add.text(p.x, p.y, name.split('').join(' '), txt(Math.round(12 * sz), '#d8d3b0', { strokeThickness: 0 })).setOrigin(0.5).setDepth(3).setAlpha(0.42); });
    this.label = this.add.text(0, 0, '', txt(13, '#fff4c2')).setOrigin(0.5, 1).setDepth(10).setVisible(false);
    this.hintLabel = this.add.text(0, 0, '', txt(14, '#ffd54a', { strokeThickness: 5 })).setOrigin(0.5, 1).setDepth(11).setVisible(false);
    this.bohun = this.add.image(0, 0, 'rider').setOrigin(rider.anchor.x / rider.size[0], rider.anchor.y / rider.size[1]).setDepth(8);
    this.facing = 1; this.targets = new Set(); this.tgt = null;

    this.mini = this.cameras.add(0, 0, 10, 10);
    this.mini.setBackgroundColor('#0d100b');
    this.cameras.main.ignore([this.miniGfx]);
    this.mini.ignore([this.lines, this.nodesG, this.dyn, this.label, this.hintLabel, this.bohun, ...this.cityLabels, ...this.regionLabels]);
    this.layout();

    this.minZoom = () => Math.min(innerWidth / WORLD.width, innerHeight / WORLD.height);
    this.zoomv = 1; this.cx = WORLD.width / 2; this.cy = WORLD.height / 2; this.all = false;
    this.home(true);
    this.drag = null;
    this.input.on('pointerdown', (p) => { this.drag = { x: p.x, y: p.y, moved: false, mini: this.inMini(p) }; });
    this.input.on('pointermove', (p) => this.onMove(p));
    this.input.on('pointerup', (p) => { const d = this.drag; this.drag = null; if (d && !d.moved) this.click(p); });
    this.input.on('wheel', (p, _o, _dx, dy) => { if (!ui.explore) return this.askExplore(); this.tgt = null; this.zoomAt(p, Math.exp(-dy * 0.0012)); });
    this.scale.on('resize', () => this.layout());
    renderFlows(); renderMission();
  }
  askExplore() { note('Чтобы двигать и приближать карту, нажмите «ДВИГАТЬ КАРТУ» вверху.'); }

  // screen areas covered by HTML panels, so the camera centres the scene in what is left
  occupied() {
    const narrow = innerWidth <= 820, short = innerHeight <= 560, top = narrow ? (short ? 70 : 84) : 58;
    if (narrow && short) return { left: 0, right: 0, top, bottom: Math.min(innerHeight * 0.46, 140) };
    if (narrow) return { left: 0, right: 0, top: top + (ui.explore ? 70 : 130), bottom: innerHeight * 0.42 + 30 };
    return { left: ui.explore || short ? 0 : 478, right: 388, top, bottom: 40 };
  }
  free() { const o = this.occupied(); return { w: Math.max(200, innerWidth - o.left - o.right), h: Math.max(160, innerHeight - o.top - o.bottom), ox: (o.left - o.right) / 2, oy: (o.bottom - o.top) / 2 }; }
  layout() {
    const narrow = innerWidth <= 820;
    const wcss = narrow ? Math.min(150, innerWidth * 0.36) : Math.min(300, Math.max(180, innerWidth * 0.22)), hcss = (wcss * WORLD.height) / WORLD.width;
    const x = innerWidth - wcss - (narrow ? 18 : 32), y = narrow ? (innerHeight <= 560 ? 70 : 84) + 30 : innerHeight - hcss - 40;
    this.miniRect = { x: x * dpr, y: y * dpr, w: wcss * dpr, h: hcss * dpr };
    Object.assign($('#mini').style, { width: wcss + 'px', height: hcss + 'px', left: x + 'px', top: y + 'px' });
    document.documentElement.style.setProperty('--mini-h', hcss + 'px'); document.documentElement.style.setProperty('--mini-w', wcss + 'px');
    if (this.mini) this.mini.setVisible(getComputedStyle($('#mini')).display !== 'none'); // hidden frame = hidden camera
    if (this.mini) { const r = this.miniRect; this.mini.setViewport(r.x, r.y, r.w, r.h); this.mini.setZoom(r.w / WORLD.width); this.mini.centerOn(WORLD.width / 2, WORLD.height / 2); }
  }
  fly(cx, cy, zoom, now) { this.tgt = { cx, cy, zoom: Phaser.Math.Clamp(zoom, this.minZoom(), 9) }; this.all = false; if (now || reduced) { Object.assign(this, { cx, cy, zoomv: this.tgt.zoom }); this.tgt = null; } }
  fitBox(x0, y0, x1, y1, pad = 1.12, now) { const f = this.free(); this.fly((x0 + x1) / 2, (y0 + y1) / 2, Math.min(f.w / ((x1 - x0) * pad), f.h / ((y1 - y0) * pad)), now); }
  fitPoints(pts, pad = 1.35) { const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y); this.fitBox(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys), pad); }
  // presentation home: Ukraine fills the screen as in v0.3 (panels float over the edges); Crimea always in frame
  home(now) {
    const a = project(22.0, 52.4), b = project(40.3, 44.3), f = this.free();
    const z = Math.min((innerWidth * 0.86) / (b.x - a.x), (innerHeight - f.oy * 0 - 120) / (b.y - a.y));
    this.fly((a.x + b.x) / 2, (a.y + b.y) / 2, z, now);
  }
  focusNode(id, zoom = 3.5) { const n = S.world.nodes[id]; this.fly(n.x, n.y, zoom); }
  toggleAll() { if (this.all) return this.home(); this.fly(WORLD.width / 2, WORLD.height / 2, this.minZoom()); this.all = true; }
  onStage(st) {
    if (st === 1) this.home();
    else if (st === 3) { this.fly(WORLD.width / 2, WORLD.height / 2, this.minZoom()); this.markTargets(); }
    else if (st === 5) { const f = S.flows.find((x) => x.id === mission.flowId); if (f) this.fitPoints(f.steps.flatMap((s) => s.pts)); }
    else if (st === 'done') this.fly(WORLD.width / 2, WORLD.height / 2, this.minZoom());
  }
  zoomAt(p, f) {
    const cam = this.cameras.main, w0 = cam.getWorldPoint(p.x, p.y);
    this.zoomv = Phaser.Math.Clamp(this.zoomv * f, this.minZoom(), 9);
    cam.setZoom(this.zoomv * dpr);
    const w1 = cam.getWorldPoint(p.x, p.y);
    this.cx += w0.x - w1.x; this.cy += w0.y - w1.y; this.all = false;
  }
  inMini(p) { const r = this.miniRect; return this.mini.visible && p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h; }
  jumpMini(p) {
    if (!ui.explore) setExplore(true);
    const r = this.miniRect; this.tgt = null; this.cx = ((p.x - r.x) / r.w) * WORLD.width; this.cy = ((p.y - r.y) / r.h) * WORLD.height; this.all = false;
    if (this.zoomv < 1.2) this.zoomv = 2.5;
  }
  onMove(p) {
    const d = this.drag;
    if (d?.mini && p.isDown) return this.jumpMini(p);
    if (!d || !p.isDown) { this.hover = this.pick(p); return; }
    if (Math.abs(p.x - d.x) + Math.abs(p.y - d.y) > 8 * dpr) { if (!d.moved && !ui.explore && !d.mini) this.askExplore(); d.moved = true; }
    if (!ui.explore) return;
    if (d.moved && !d.mini) { this.tgt = null; this.cx -= (p.x - p.prevPosition.x) / (this.zoomv * dpr); this.cy -= (p.y - p.prevPosition.y) / (this.zoomv * dpr); this.all = false; }
    const a = this.input.pointer1, b = this.input.pointer2;
    if (a.isDown && b.isDown) {
      const dist = Phaser.Math.Distance.Between(a.x, a.y, b.x, b.y);
      if (this.pinch) this.zoomAt({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, dist / this.pinch);
      this.pinch = dist; d.moved = true;
    } else this.pinch = 0;
  }
  click(p) { if (this.inMini(p)) return this.jumpMini(p); const id = this.pick(p); if (id) chooseNode(id); }
  pick(p) {
    if (this.inMini(p)) return null;
    const w = this.cameras.main.getWorldPoint(p.x, p.y), k = 1 / this.zoomv, u = this.unit() ;
    let best = null, bd = Math.max(22 * k, 16 * u);
    for (const n of Object.values(S.world.nodes)) { const d = Math.hypot(w.x - n.x, w.y - (n.y - 6 * u)); if (d < bd) { bd = d; best = n.id; } }
    return best;
  }
  unit() { return Phaser.Math.Clamp(this.zoomv / 3.2, 0.5, 1.5) / this.zoomv; } // building scale: grows a little with zoom
  markTargets() { this.targets = new Set(Object.keys(S.world.nodes).filter((id) => id !== ui.source && game.routeOptions(ui.source, id).length)); }
  popCoins(nodeId) { const n = S.world.nodes[nodeId]; for (let i = 0; i < 6; i++) coins.push({ x: n.x, y: n.y, age: -i * 0.1, kind: i % 2 }); }

  update(time, delta) {
    let remain = Math.min(delta, 1000) / 1000;
    const wv0 = this.cameras.main.worldView, view = { x: wv0.centerX, y: wv0.centerY, r: Math.max(wv0.width, wv0.height) / 2 };
    while (remain > 1e-6) { const dt = Math.min(0.05, remain); game.tick(dt, view); remain -= dt; }
    if (this.tgt) {
      const t = this.tgt, e = 0.1;
      this.cx += (t.cx - this.cx) * e; this.cy += (t.cy - this.cy) * e; this.zoomv *= Math.pow(t.zoom / this.zoomv, e);
      if (Math.abs(t.cx - this.cx) < 1 && Math.abs(t.cy - this.cy) < 1 && Math.abs(Math.log(t.zoom / this.zoomv)) < 0.01) this.tgt = null;
    }
    const cam = this.cameras.main, k = 1 / this.zoomv, f = this.free(), u = this.unit();
    this.cx = Phaser.Math.Clamp(this.cx, 0, WORLD.width); this.cy = Phaser.Math.Clamp(this.cy, 0, WORLD.height);
    cam.setZoom(this.zoomv * dpr); cam.centerOn(this.cx - f.ox * k, this.cy + f.oy * k);
    OW = 0.8 * k;

    const L = this.lines, N = this.nodesG, D = this.dyn, Mg = this.miniGfx;
    L.clear(); N.clear(); D.clear(); Mg.clear();
    const sel = ui.selected, st = stage(), far = this.zoomv < 0.6;
    const vw = cam.worldView, pad = 60 * k;
    const inView = (p) => p.x > vw.x - pad && p.x < vw.right + pad && p.y > vw.y - pad && p.y < vw.bottom + pad;

    // corridors in the v0.3 grammar: roads dark with a dashed light centre, rails with ties, sea lanes and air faint
    for (const e of Object.values(S.world.edges)) {
      const hot = sel && (e.a === sel || e.b === sel);
      if (e.mode === 'road') { L.lineStyle((hot ? 5.4 : 4.4) * k, 0x1b1d14, 0.7); L.strokePoints(e.pts, false, false); L.lineStyle((hot ? 3.2 : 2.6) * k, hot ? 0xb59a5c : 0x7a6a48, 0.95); L.strokePoints(e.pts, false, false); }
      else if (e.mode === 'rail') { if (!far) { L.lineStyle(1.2 * k, hot ? 0xf0d371 : 0x9a927a, 0.85); ties(L, e.pts, 4.5 * k, 2.8 * k); } L.lineStyle((hot ? 2.2 : 1.6) * k, 0x24221c, 1); L.strokePoints(e.pts, false, false); }
      else if (e.mode === 'sea') { L.lineStyle((hot ? 2 : 1.4) * k, hot ? 0xf0d371 : 0x9fc3c4, hot ? 0.9 : 0.45); dashed(L, e.pts, 9 * k, 6 * k); }
      else { L.lineStyle((hot ? 1.6 : 1) * k, hot ? 0xf0d371 : 0xe6e0c7, hot ? 0.8 : far ? 0.3 : 0.16); dashed(L, e.pts, 3 * k, 6 * k); }
    }
    const off = (time / 40) * k;
    for (const fl of S.flows) for (const s of fl.steps) { L.lineStyle(6 * k, 0x0d100b, 0.55); L.strokePoints(s.pts, false, false); L.lineStyle(3.2 * k, 0xe2c667, 1); dashed(L, s.pts, 12 * k, 7 * k, off); }

    // nodes: lens ring under, building on top
    const smokes = [];
    const nodes = Object.values(S.world.nodes).sort((a, b) => a.y - b.y);
    for (const n of nodes) {
      if (!inView(n)) continue;
      const hit = lensHit(ui.lens, n.type), a = hit ? 1 : 0.72;
      if (hit && !far) { N.fillStyle(0xd3b766, 0.16); N.fillEllipse(n.x, n.y + 2 * u, 46 * u, 20 * u); N.lineStyle(1 * k, 0xd3b766, 0.55); N.strokeEllipse(n.x, n.y + 2 * u, 46 * u, 20 * u); }
      if (far) { N.fillStyle(n.type === 'bank' ? 0xf5c542 : n.type === 'port' ? 0x9fc3c4 : isSource(n.type) ? 0xd98c5f : 0xe6e0c7, a); N.fillCircle(n.x, n.y, (hit ? 3.4 : 2.6) * k); N.lineStyle(1 * k, 0x0d100b, 1); N.strokeCircle(n.x, n.y, (hit ? 3.4 : 2.6) * k); }
      else drawBuilding(N, n, u, a, smokes, time);
    }
    if (!reduced) for (const s of smokes) for (let i = 0; i < 3; i++) {
      const age = ((time / 1000 + i * 0.62 + s.seed * 0.37) % 1.9) / 1.9;
      D.fillStyle(0xb9b8a8, 0.32 * (1 - age)); D.fillCircle(s.x + age * 6 * u, s.y - age * 15 * u, (1.6 + age * 3.6) * u);
    }

    for (const am of S.ambient) { const p = pointAt(am.pts, am.d); if (inView(p)) drawMover(D, am.kind, p, Phaser.Math.Clamp(this.zoomv / 3, 0.6, 1.4) * k * 1.1, 0.95, false); }
    for (const c of S.carriers) { const s = c.steps[Math.min(c.leg, c.steps.length - 1)]; drawMover(D, MODES[s.mode].kind, pointAt(s.pts, c.d), Phaser.Math.Clamp(this.zoomv / 3, 0.7, 1.5) * k * 1.3, 1, true); }
    for (let i = coins.length - 1; i >= 0; i--) {
      const c = coins[i]; c.age += delta / 1000;
      if (c.age > 1.4) { coins.splice(i, 1); continue; }
      if (c.age < 0) continue;
      const a = 1 - c.age / 1.4, y = c.y - 20 * u - c.age * 40 * k, x = c.x + (c.kind ? 8 : -8) * k * c.age;
      if (c.kind) { D.fillStyle(0x6a9f4f, a); D.fillRect(x - 7 * k, y - 4 * k, 14 * k, 8 * k); D.lineStyle(1.2 * k, 0x2f4f22, a); D.strokeRect(x - 7 * k, y - 4 * k, 14 * k, 8 * k); }
      else { D.fillStyle(0xf5c542, a); D.fillCircle(x, y, 6 * k); D.lineStyle(1.2 * k, 0x8d6a28, a); D.strokeCircle(x, y, 6 * k); }
    }

    // guidance and selection (RTS ground ellipses)
    const pulse = reduced ? 1 : 0.5 + 0.5 * Math.sin(time / 250), gr = far ? 9 * k : 30 * u;
    let hintId = null, hintText = '';
    if (st === 1) { hintId = M.source; hintText = 'НАЖМИТЕ СЮДА'; }
    if (st === 3) { hintId = M.target; hintText = 'НАЖМИТЕ НА БАНК'; }
    if (hintId) { const n = S.world.nodes[hintId]; D.lineStyle(3 * k, 0xffd54a, 0.5 + 0.5 * pulse); D.strokeEllipse(n.x, n.y + 2 * u, (2.2 + 0.25 * pulse) * gr, (1.0 + 0.12 * pulse) * gr); this.hintLabel.setVisible(true).setScale(k).setPosition(n.x, n.y - (far ? 8 * k : 30 * u)).setText(hintText); } else this.hintLabel.setVisible(false);
    if (ui.mode === 'pick') for (const id of this.targets) { const n = S.world.nodes[id]; D.lineStyle(2 * k, 0x9fc48a, 0.95); D.strokeEllipse(n.x, n.y + 2 * u, 1.7 * gr, 0.8 * gr); }
    if (ui.mode === 'pick' && ui.source) { const n = S.world.nodes[ui.source]; D.lineStyle(3 * k, 0xffd54a, 1); D.strokeEllipse(n.x, n.y + 2 * u, 1.9 * gr, 0.9 * gr); }
    if (sel && ui.mode !== 'pick' && sel !== hintId) { const n = S.world.nodes[sel]; D.lineStyle(2.4 * k, 0xf0d371, 0.7 + 0.3 * pulse); D.strokeEllipse(n.x, n.y + 2 * u, 1.9 * gr, 0.9 * gr); }
    const hv = this.hover && S.world.nodes[this.hover];
    if (hv) this.label.setVisible(true).setScale(k).setPosition(hv.x, hv.y - (far ? 8 * k : 28 * u)).setText(`${hv.name} · ${short(hv.place)}`.toUpperCase()); else this.label.setVisible(false);
    for (const t of this.cityLabels) t.setScale(k).setVisible(this.zoomv > 1.3);
    for (const t of this.regionLabels) t.setScale(k).setVisible(this.zoomv > 1.1 && this.zoomv < 7);

    const bp = game.bohunPos();
    if (S.bohun.status === 'moving') this.facing = bp.ax < 0 ? -1 : 1;
    const bpx = Phaser.Math.Clamp(26 + this.zoomv * 11, 26, 64), sc = (bpx / rider.visibleBBox.h) * k;
    D.fillStyle(0x0b0e08, 0.45); D.fillEllipse(bp.x, bp.y, 44 * k, 11 * k);
    this.bohun.setPosition(bp.x, bp.y + (S.bohun.status === 'moving' && !reduced ? Math.sin(time / 90) * 0.8 * k : 0)).setScale(sc * this.facing, sc);

    for (const n of Object.values(S.world.nodes)) { Mg.fillStyle(n.type === 'bank' ? 0xf5c542 : n.type === 'port' ? 0x9fc3c4 : 0xe6e0c7, 1); Mg.fillCircle(n.x, n.y, 16); }
    for (const fl of S.flows) for (const s of fl.steps) { Mg.lineStyle(14, 0xe2c667, 1); Mg.strokePoints(s.pts, false, false); }
    if (hintId) { const n = S.world.nodes[hintId]; Mg.lineStyle(24, 0xffd54a, 0.5 + 0.5 * pulse); Mg.strokeCircle(n.x, n.y, 90); }
    Mg.fillStyle(0xd3b766, 1); Mg.fillCircle(bp.x, bp.y, 22);
    const wv = cam.worldView; Mg.lineStyle(14, 0xd3b766, 1); Mg.strokeRect(wv.x, wv.y, wv.width, wv.height);
    $('#c-delivered').textContent = String(S.delivered); $('#c-coins').textContent = String(S.coins);
    if (S.bohun.status !== this.lastBohun) { this.lastBohun = S.bohun.status; renderCard(); }
    renderMission();
  }
}

window.__phaser = new Phaser.Game({
  type: Phaser.AUTO, parent: 'stage', backgroundColor: '#0d100b',
  scale: { mode: Phaser.Scale.NONE, width: innerWidth * dpr, height: innerHeight * dpr, zoom: 1 / dpr },
  input: { touch: { capture: true }, activePointers: 2 },
  scene: [FieldScene],
});
window.addEventListener('resize', () => window.__phaser?.scale.resize(innerWidth * dpr, innerHeight * dpr));
