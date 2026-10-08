import Phaser from 'phaser';
import { createFlows, WORLD, MODES, isSource, pointAt } from './core.js';
import * as data from './data/network.js';
import assets from '../game/data/assets.json' with { type: 'json' };

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const dpr = Number(params.get('dpr')) || Math.min(window.devicePixelRatio || 1, 2);
const rider = assets.rider;
const REASON = {
  limit: 'Уже три потока. Остановите один, чтобы начать новый.',
  duplicate: 'Такой поток уже идёт.',
  'not-a-source': 'Потоки начинаются только из шахты или завода.',
  'same-node': 'Выберите другое место.',
  'no-route': 'Туда нет пути по коридорам.',
  busy: 'Богун ещё в пути.',
};

// ---------- the guided task: deliver 3 cargoes from the mine to the New York bank, compare routes ----------
const M = { source: 'mine-kr', target: 'bank-newyork', need: 3 };
const STEP_TEXT = [
  'Нажмите на шахту: на карте её отмечает мигающее кольцо. Богун приедет к ней.',
  'Нажмите кнопку «Направить поток отсюда»: так вы решаете, куда поедут грузы.',
  'Нажмите на банк в Нью-Йорке (карта отдалится, банк отмечен кольцом).',
  'Выберите путь: быстро и дорого или долго и дёшево.',
  'Подождите, пока доедут 3 груза. Потом сравните результат.',
];
let mission = { delivered: 0, flowId: null, opt: null, done: false, results: [] };
let ui = { selected: null, mode: 'idle', source: null, target: null }; // mode: idle | pick | options

let game, S; // assigned right after creation: createFlows emits 'reset' before it returns
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
    if (p.bank && window.__scene) window.__scene.popCoins(p.toId);
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
  if (type === 'reset') { ui = { selected: null, mode: 'idle', source: null, target: null }; mission = { delivered: 0, flowId: null, opt: null, done: false, results: [] }; renderCard(); renderPick(); renderFlows(); }
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
  prog.hidden = !(st === 5);
  if (st === 5) { prog.querySelector('i').style.width = `${(mission.delivered / M.need) * 100}%`; prog.querySelector('span').textContent = `Доставлено ${mission.delivered} из ${M.need}`; }
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
    const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = 'Попробовать другой путь';
    btn.addEventListener('click', () => { mission.done = false; mission.delivered = 0; mission.flowId = null; ui = { selected: null, mode: 'idle', source: null, target: null }; renderCard(); renderPick(); window.__scene?.focusNode(M.source, 3.5); });
    res.append(btn);
  });
  const flowBtn = $('#k-flow'); flowBtn.classList.toggle('hint', st === 2);
  if (st !== lastStage) { lastStage = st; window.__scene?.onStage(st); }
}

function renderCard() {
  const n = ui.selected && S.world.nodes[ui.selected];
  $('#card').hidden = !n;
  if (!n) return;
  $('#k-type').textContent = data.TYPE_RU[n.type];
  $('#k-name').textContent = n.name;
  $('#k-place').textContent = `${n.place}. ДЕМО: тестовое место`;
  $('#k-role').textContent = data.TYPE_ROLE[n.type];
  $('#k-flow').hidden = !isSource(n.type) || ui.mode !== 'idle';
}
function renderPick() {
  $('#pick').hidden = ui.mode !== 'pick';
  $('#options').hidden = ui.mode !== 'options';
  if (ui.mode === 'idle') renderCard();
  if (ui.mode === 'options') {
    const a = S.world.nodes[ui.source], b = S.world.nodes[ui.target];
    $('#o-title').textContent = `${a.name} (${short(a.place)}) → ${b.name} (${short(b.place)})`;
    const list = $('#o-list');
    list.replaceChildren();
    for (const o of game.routeOptions(ui.source, ui.target)) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'opt';
      btn.append(`${o.label}: ${modesText(o.modes)}`);
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
  }
}
function renderFlows() {
  $('#f-count').textContent = String(S.flows.length);
  const ul = $('#f-list');
  ul.replaceChildren();
  if (!S.flows.length) { const li = document.createElement('li'); li.textContent = 'Пока нет.'; ul.append(li); return; }
  for (const f of S.flows) {
    const li = document.createElement('li');
    const a = S.world.nodes[f.fromId], b = S.world.nodes[f.toId];
    const t = document.createElement('span');
    t.textContent = `${short(a.place)} → ${short(b.place)}: ${f.label}, ≈ ${f.days} усл. дн.`;
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'plain'; x.textContent = 'Стоп'; x.setAttribute('aria-label', 'Остановить поток');
    x.addEventListener('click', () => game.stopFlow(f.id));
    li.append(t, x); ul.append(li);
  }
}
$('#k-flow').addEventListener('click', () => { ui.mode = 'pick'; ui.source = ui.selected; window.__scene?.markTargets(); renderPick(); $('#k-flow').hidden = true; });
$('#pick-cancel').addEventListener('click', cancelPick);
$('#o-cancel').addEventListener('click', cancelPick);
function cancelPick() { ui.mode = 'idle'; ui.source = ui.target = null; renderPick(); renderCard(); }
window.addEventListener('keydown', (e) => e.key === 'Escape' && ui.mode !== 'idle' && cancelPick());
$('#reset').addEventListener('click', () => { game.reset(); window.__scene?.home(); note('Сначала.'); });
$('#all').addEventListener('click', () => window.__scene?.toggleAll());

window.__choose = (id) => chooseNode(id); // test hook
function chooseNode(id) {
  if (ui.mode === 'pick') {
    if (id === ui.source) return note('Выберите другое место.');
    if (!game.routeOptions(ui.source, id).length) return note('Туда нет пути по коридорам.');
    ui.target = id; ui.mode = 'options'; renderPick();
    return;
  }
  ui.selected = id; renderCard(); game.moveBohun(id);
}

// ---------- scene ----------
const COL = { rail: 0xf0e6c0, road: 0xe0b878, sea: 0x9fd4f5, air: 0xffffff };
const NODE_PX = { mine: 12, 'factory-s': 10, 'factory-m': 13, 'factory-l': 17, bank: 13, port: 12, station: 10, airfield: 12 };

class FlowsScene extends Phaser.Scene {
  constructor() { super('flows'); }
  preload() {
    this.load.svg('world', '/flows/world-tilt-base.svg', { width: 4096, height: Math.round((4096 * WORLD.height) / WORLD.width) });
    this.load.image('rider', rider.url);
  }
  create() {
    window.__scene = this;
    this.add.image(0, 0, 'world').setOrigin(0, 0).setDisplaySize(WORLD.width, WORLD.height).setDepth(0);
    this.lines = this.add.graphics().setDepth(2);
    this.dyn = this.add.graphics().setDepth(4);
    this.miniGfx = this.add.graphics().setDepth(6);
    this.label = this.add.text(0, 0, '', { fontFamily: 'system-ui, sans-serif', fontSize: '14px', fontStyle: '700', color: '#ffffff', stroke: '#101412', strokeThickness: 4, resolution: dpr }).setOrigin(0.5, 1).setDepth(10).setVisible(false);
    this.hintLabel = this.add.text(0, 0, '', { fontFamily: 'system-ui, sans-serif', fontSize: '15px', fontStyle: '700', color: '#ffd54a', stroke: '#101412', strokeThickness: 5, resolution: dpr }).setOrigin(0.5, 1).setDepth(11).setVisible(false);
    this.bohun = this.add.image(0, 0, 'rider').setOrigin(rider.anchor.x / rider.size[0], rider.anchor.y / rider.size[1]).setDepth(8);
    this.facing = 1;
    this.targets = new Set();
    this.tgt = null;

    this.layoutMini();
    this.mini = this.cameras.add(this.miniRect.x, this.miniRect.y, this.miniRect.w, this.miniRect.h);
    this.mini.setBackgroundColor('#101412');
    this.cameras.main.ignore([this.miniGfx]);
    this.mini.ignore([this.lines, this.dyn, this.label, this.hintLabel, this.bohun]);
    this.applyMini();

    this.minZoom = () => Math.min(innerWidth / WORLD.width, innerHeight / WORLD.height);
    const m = S.world.nodes[M.source];
    this.zoomv = 3.5; this.cx = m.x; this.cy = m.y; this.all = false;
    this.drag = null;
    this.input.on('pointerdown', (p) => { this.drag = { x: p.x, y: p.y, moved: false, mini: this.inMini(p) }; });
    this.input.on('pointermove', (p) => this.onMove(p));
    this.input.on('pointerup', (p) => { const d = this.drag; this.drag = null; if (d && !d.moved) this.click(p); });
    this.input.on('wheel', (p, _o, _dx, dy) => { this.tgt = null; this.zoomAt(p, Math.exp(-dy * 0.0012)); });
    this.scale.on('resize', () => { this.layoutMini(); this.applyMini(); });
    renderFlows(); renderMission();
  }

  layoutMini() {
    const wcss = Math.min(300, Math.max(160, innerWidth * 0.26)), hcss = (wcss * WORLD.height) / WORLD.width;
    const narrow = innerWidth <= 720;
    const x = innerWidth - wcss - (narrow ? 8 : 12), y = innerHeight - hcss - (narrow ? innerHeight * 0.46 + 24 : 12);
    this.miniRect = { x: x * dpr, y: y * dpr, w: wcss * dpr, h: hcss * dpr };
    Object.assign($('#mini').style, { width: wcss + 'px', height: hcss + 'px', left: x + 'px', top: y + 'px', right: 'auto', bottom: 'auto' });
  }
  applyMini() { const r = this.miniRect; this.mini.setViewport(r.x, r.y, r.w, r.h); this.mini.setZoom(r.w / WORLD.width); this.mini.centerOn(WORLD.width / 2, WORLD.height / 2); }

  free() { const narrow = innerWidth <= 720; return { w: narrow ? innerWidth : innerWidth - 360, h: narrow ? innerHeight * 0.5 : innerHeight }; }
  fly(cx, cy, zoom) { this.tgt = { cx, cy, zoom: Phaser.Math.Clamp(zoom, this.minZoom(), 9) }; this.all = false; }
  focusNode(id, zoom = 3.5) { const n = S.world.nodes[id]; this.fly(n.x, n.y, zoom); }
  fitPoints(pts, pad = 1.35) {
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y), f = this.free();
    const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys);
    this.fly((Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, Math.min(f.w / (w * pad || 1), f.h / (h * pad || 1)));
  }
  home() { this.focusNode(M.source, 3.5); }
  toggleAll() { if (this.all) return this.home(); this.fly(WORLD.width / 2, WORLD.height / 2, this.minZoom()); this.all = true; }
  onStage(st) {
    if (st === 1) this.focusNode(M.source, 3.5);
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
  inMini(p) { const r = this.miniRect; return p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h; }
  jumpMini(p) { const r = this.miniRect; this.tgt = null; this.cx = ((p.x - r.x) / r.w) * WORLD.width; this.cy = ((p.y - r.y) / r.h) * WORLD.height; this.all = false; }
  onMove(p) {
    const d = this.drag;
    if (d?.mini && p.isDown) return this.jumpMini(p);
    if (!d || !p.isDown) { this.hover = this.pick(p); return; }
    if (Math.abs(p.x - d.x) + Math.abs(p.y - d.y) > 8 * dpr) d.moved = true;
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
    const w = this.cameras.main.getWorldPoint(p.x, p.y), reach = 26 / this.zoomv;
    let best = null, bd = reach;
    for (const n of Object.values(S.world.nodes)) { const d = Math.hypot(w.x - n.x, w.y - n.y); if (d < bd) { bd = d; best = n.id; } }
    return best;
  }
  markTargets() { this.targets = new Set(Object.keys(S.world.nodes).filter((id) => id !== ui.source && game.routeOptions(ui.source, id).length)); }
  popCoins(nodeId) { const n = S.world.nodes[nodeId]; for (let i = 0; i < 6; i++) coins.push({ x: n.x, y: n.y, age: -i * 0.1, kind: i % 2 }); }

  update(time, delta) {
    let remain = Math.min(delta, 1000) / 1000;
    while (remain > 1e-6) { const dt = Math.min(0.05, remain); game.tick(dt); remain -= dt; }
    if (this.tgt) {
      const t = this.tgt, e = 0.12;
      this.cx += (t.cx - this.cx) * e; this.cy += (t.cy - this.cy) * e; this.zoomv *= Math.pow(t.zoom / this.zoomv, e);
      if (Math.abs(t.cx - this.cx) < 1 && Math.abs(t.cy - this.cy) < 1 && Math.abs(Math.log(t.zoom / this.zoomv)) < 0.01) this.tgt = null;
    }
    const cam = this.cameras.main, k = 1 / this.zoomv;
    this.cx = Phaser.Math.Clamp(this.cx, 0, WORLD.width); this.cy = Phaser.Math.Clamp(this.cy, 0, WORLD.height);
    const narrow = innerWidth <= 720, ox = narrow ? 0 : 180, oy = narrow ? innerHeight * 0.23 : 0;
    cam.setZoom(this.zoomv * dpr); cam.centerOn(this.cx - ox * k, this.cy + oy * k);

    const L = this.lines, D = this.dyn, Mg = this.miniGfx;
    L.clear(); D.clear(); Mg.clear();
    const sel = ui.selected, st = stage();
    for (const e of Object.values(S.world.edges)) {
      const hot = sel && (e.a === sel || e.b === sel);
      L.lineStyle((hot ? 6 : 4) * k, 0x101412, 0.5); strokeEdge(L, e, k);
      L.lineStyle((hot ? 3.4 : 2) * k, COL[e.mode], hot ? 1 : 0.75); strokeEdge(L, e, k);
    }
    const off = (time / 40) * k;
    for (const f of S.flows) for (const s of f.steps) { L.lineStyle(7 * k, 0x101412, 0.55); L.strokePoints(s.pts, false, false); L.lineStyle(4 * k, 0xffd54a, 1); dashed(L, s.pts, 14 * k, 8 * k, off); }

    for (const n of Object.values(S.world.nodes)) this.drawNode(D, n, k);
    for (const a of S.ambient) this.drawMover(D, a.kind, pointAt(a.pts, a.d), k, 0.95, false);
    for (const c of S.carriers) { const s = c.steps[Math.min(c.leg, c.steps.length - 1)]; this.drawMover(D, MODES[s.mode].kind, pointAt(s.pts, c.d), k, 1, true); }
    for (let i = coins.length - 1; i >= 0; i--) {
      const c = coins[i]; c.age += delta / 1000;
      if (c.age > 1.4) { coins.splice(i, 1); continue; }
      if (c.age < 0) continue;
      const a = 1 - c.age / 1.4, y = c.y - c.age * 40 * k, x = c.x + (c.kind ? 8 : -8) * k * c.age;
      if (c.kind) { D.fillStyle(0x4caf50, a); D.fillRect(x - 7 * k, y - 4 * k, 14 * k, 8 * k); D.lineStyle(1.2 * k, 0x1d5e20, a); D.strokeRect(x - 7 * k, y - 4 * k, 14 * k, 8 * k); } else { D.fillStyle(0xf5c542, a); D.fillCircle(x, y, 6 * k); D.lineStyle(1.2 * k, 0x8d6a28, a); D.strokeCircle(x, y, 6 * k); }
    }

    // guidance: what to press next
    const pulse = 0.5 + 0.5 * Math.sin(time / 250);
    let hintId = null, hintText = '';
    if (st === 1) { hintId = M.source; hintText = 'Нажмите сюда'; }
    if (st === 3) { hintId = M.target; hintText = 'Нажмите на банк'; }
    if (hintId) { const n = S.world.nodes[hintId]; D.lineStyle(4 * k, 0xffd54a, 0.5 + 0.5 * pulse); D.strokeCircle(n.x, n.y, (20 + 6 * pulse) * k); this.hintLabel.setVisible(true).setScale(k).setPosition(n.x, n.y - 24 * k).setText(hintText); } else this.hintLabel.setVisible(false);
    if (ui.mode === 'pick') for (const id of this.targets) { const n = S.world.nodes[id]; D.lineStyle(2.4 * k, 0x8fe08f, 0.95); D.strokeCircle(n.x, n.y, 17 * k); }
    if (ui.mode === 'pick' && ui.source) { const n = S.world.nodes[ui.source]; D.lineStyle(4 * k, 0xffd54a, 1); D.strokeCircle(n.x, n.y, 19 * k); }
    if (sel && ui.mode !== 'pick' && sel !== hintId) { const n = S.world.nodes[sel]; D.lineStyle(3.4 * k, 0xffd54a, 0.7 + 0.3 * pulse); D.strokeCircle(n.x, n.y, (17 + 3 * pulse) * k); }
    const hv = this.hover && S.world.nodes[this.hover];
    if (hv) this.label.setVisible(true).setScale(k).setPosition(hv.x, hv.y - 20 * k).setText(`${hv.name} · ${short(hv.place)}`); else this.label.setVisible(false);

    const bp = game.bohunPos();
    if (S.bohun.status === 'moving') this.facing = bp.ax < 0 ? -1 : 1;
    const bpx = Phaser.Math.Clamp(24 + this.zoomv * 16, 24, 72), sc = (bpx / rider.visibleBBox.h) * k;
    D.fillStyle(0x0e1210, 0.5); D.fillEllipse(bp.x, bp.y, 46 * k, 11 * k);
    this.bohun.setPosition(bp.x, bp.y).setScale(sc * this.facing, sc);

    for (const n of Object.values(S.world.nodes)) { Mg.fillStyle(n.type === 'bank' ? 0xf5c542 : n.type === 'port' ? 0x9fd4f5 : 0xffffff, 1); Mg.fillCircle(n.x, n.y, 18); }
    if (hintId) { const n = S.world.nodes[hintId]; Mg.lineStyle(26, 0xffd54a, 0.5 + 0.5 * pulse); Mg.strokeCircle(n.x, n.y, 90); }
    const wv = cam.worldView; Mg.lineStyle(16, 0xffd54a, 1); Mg.strokeRect(wv.x, wv.y, wv.width, wv.height);
    $('#c-delivered').textContent = String(S.delivered); $('#c-coins').textContent = String(S.coins);
    renderMission();
  }

  drawNode(g, n, k) {
    const s = NODE_PX[n.type] * k, x = n.x, y = n.y;
    g.fillStyle(0x0e1210, 0.35); g.fillEllipse(x, y + s * 0.9, s * 2.2, s * 0.7);
    g.lineStyle(2 * k, 0x101412, 1);
    if (n.type === 'mine') { g.fillStyle(0x5a4a3a, 1); g.fillTriangle(x, y - s, x + s * 1.1, y + s * 0.8, x - s * 1.1, y + s * 0.8); g.strokeTriangle(x, y - s, x + s * 1.1, y + s * 0.8, x - s * 1.1, y + s * 0.8); g.fillStyle(0x201a14, 1); g.fillRect(x - s * 0.25, y + s * 0.2, s * 0.5, s * 0.6); }
    else if (n.type.startsWith('factory')) { g.fillStyle(0x9aa7b4, 1); g.fillRect(x - s, y - s * 0.6, s * 2, s * 1.4); g.strokeRect(x - s, y - s * 0.6, s * 2, s * 1.4); g.fillStyle(0xc94b3a, 1); g.fillRect(x - s, y - s * 0.9, s * 2, s * 0.35); g.fillStyle(0x4b5156, 1); g.fillRect(x + s * 0.35, y - s * 1.7, s * 0.5, s * 1.1); g.strokeRect(x + s * 0.35, y - s * 1.7, s * 0.5, s * 1.1); }
    else if (n.type === 'bank') { g.fillStyle(0xf0d36a, 1); g.fillRect(x - s, y - s * 0.4, s * 2, s * 1.2); g.strokeRect(x - s, y - s * 0.4, s * 2, s * 1.2); g.fillStyle(0xfff1b8, 1); g.fillTriangle(x - s * 1.2, y - s * 0.4, x + s * 1.2, y - s * 0.4, x, y - s * 1.3); g.strokeTriangle(x - s * 1.2, y - s * 0.4, x + s * 1.2, y - s * 0.4, x, y - s * 1.3); g.fillStyle(0x4caf50, 1); g.fillCircle(x, y + s * 0.2, s * 0.35); }
    else if (n.type === 'port') { g.fillStyle(0x2a7fc4, 1); g.fillCircle(x, y, s); g.strokeCircle(x, y, s); g.lineStyle(2.6 * k, 0xffffff, 1); g.lineBetween(x, y - s * 0.6, x, y + s * 0.6); g.lineBetween(x - s * 0.55, y + s * 0.15, x + s * 0.55, y + s * 0.15); g.strokeCircle(x, y - s * 0.6, s * 0.18); }
    else if (n.type === 'station') { g.fillStyle(0xd9c79a, 1); g.fillRect(x - s, y - s * 0.55, s * 2, s * 1.1); g.strokeRect(x - s, y - s * 0.55, s * 2, s * 1.1); g.fillStyle(0xb3402e, 1); g.fillTriangle(x - s * 1.1, y - s * 0.55, x + s * 1.1, y - s * 0.55, x, y - s * 1.2); g.lineStyle(1.6 * k, 0x3a3328, 1); g.lineBetween(x - s, y + s * 0.1, x + s, y + s * 0.1); }
    else { g.fillStyle(0xeef0fa, 1); g.fillCircle(x, y, s * 0.95); g.strokeCircle(x, y, s * 0.95); g.lineStyle(3 * k, 0x2b2f3a, 1); g.lineBetween(x - s * 0.65, y, x + s * 0.65, y); g.lineBetween(x, y - s * 0.65, x, y + s * 0.65); }
  }

  drawMover(g, kind, p, k, a, accent) {
    const len = Math.hypot(p.ax, p.ay) || 1, ux = p.ax / len, uy = p.ay / len, vx = -uy, vy = ux, z = 1.35;
    const u = (l, w) => ({ x: p.x + (ux * l + vx * w) * k * z, y: p.y + (uy * l + vy * w) * k * z });
    const poly = (pts, fill) => { g.fillStyle(fill, a); g.fillPoints(pts, true); g.lineStyle((accent ? 2 : 1.2) * k, accent ? 0xffd54a : 0x101412, 1); g.strokePoints(pts, true); };
    if (kind === 'train') for (let i = 0; i < 3; i++) poly([u(-6 + i * 7, -2.4), u(-1 + i * 7, -2.4), u(-1 + i * 7, 2.4), u(-6 + i * 7, 2.4)], i === 0 ? 0xc0392b : 0x8b6f47);
    else if (kind === 'truck') { poly([u(-5, -2.8), u(2, -2.8), u(2, 2.8), u(-5, 2.8)], 0x6f7a55); poly([u(2, -2.4), u(5, -2.4), u(5, 2.4), u(2, 2.4)], 0xd9d27a); }
    else if (kind === 'wagon') poly([u(-4.5, -3.2), u(4.5, -3.2), u(4.5, 3.2), u(-4.5, 3.2)], 0xa8743c);
    else if (kind === 'ship') poly([u(-8, -3.2), u(5, -3.2), u(9, 0), u(5, 3.2), u(-8, 3.2)], 0xf2f5f8);
    else poly([u(9, 0), u(-4, -7.5), u(-1, 0), u(-4, 7.5)], 0xffffff);
  }
}

function strokeEdge(g, e, k) { if (e.mode === 'sea') dashed(g, e.pts, 11 * k, 6 * k, 0); else if (e.mode === 'air') dashed(g, e.pts, 4 * k, 7 * k, 0); else g.strokePoints(e.pts, false, false); }
function dashed(g, pts, dash, gap, offset = 0) {
  const per = dash + gap; let o = ((offset % per) + per) % per;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], len = Math.hypot(b.x - a.x, b.y - a.y); if (!len) continue;
    const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
    for (let d = -o; d < len; d += per) { const s = Math.max(0, d), e = Math.min(d + dash, len); if (e > s) g.lineBetween(a.x + ux * s, a.y + uy * s, a.x + ux * e, a.y + uy * e); }
    o = ((o + len) % per);
  }
}

window.__phaser = new Phaser.Game({
  type: Phaser.AUTO, parent: 'stage', backgroundColor: '#101412',
  scale: { mode: Phaser.Scale.NONE, width: innerWidth * dpr, height: innerHeight * dpr, zoom: 1 / dpr },
  input: { touch: { capture: true }, activePointers: 2 },
  scene: [FlowsScene],
});
window.addEventListener('resize', () => window.__phaser?.scale.resize(innerWidth * dpr, innerHeight * dpr));
