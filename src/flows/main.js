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

// ---------- game state ----------
let ui = { selected: null, mode: 'idle', source: null, target: null }; // mode: idle | pick | options
const coins = []; // visual coin effects
let game, S; // assigned right after creation: createFlows emits 'reset' before it returns
game = createFlows({ data, seed: 7, bohunStart: data.BOHUN_START, onEvent });
S = game.state;
window.__flows = game; // test hook

function onEvent(type, p) {
  if (!S) return;
  if (type === 'rejected') note(REASON[p.reason] ?? `Нельзя: ${p.reason}`);
  if (type === 'dossierRequested') { ui.selected = p.nodeId; renderCard(); }
  if (type === 'cargoArrived' && p.bank && window.__scene) window.__scene.popCoins(p.toId);
  if (type === 'flowStarted' || type === 'flowStopped' || type === 'reset') renderFlows();
  if (type === 'reset') { ui = { selected: null, mode: 'idle', source: null, target: null }; renderCard(); renderPick(); }
}

let noteTimer;
function note(text) { const el = $('#note'); el.textContent = text; clearTimeout(noteTimer); noteTimer = setTimeout(() => (el.textContent = ''), 4500); }

// ---------- HTML layer ----------
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
  $('#k-flow').hidden = !(ui.selected && isSource(S.world.nodes[ui.selected].type) && ui.mode === 'idle');
  if (ui.mode === 'options') {
    const a = S.world.nodes[ui.source], b = S.world.nodes[ui.target];
    $('#o-title').textContent = `${a.name} (${a.place.replace(' (тест)', '')}) → ${b.name} (${b.place.replace(' (тест)', '')})`;
    const list = $('#o-list');
    list.replaceChildren();
    for (const o of game.routeOptions(ui.source, ui.target)) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'opt';
      btn.append(`${o.label}: ${o.modes.map((m) => MODES[m].label).join(' + ')}`);
      const small = document.createElement('small');
      small.textContent = `≈ ${o.days} усл. дн., цена ${o.price} усл. ед.`;
      btn.append(small);
      btn.addEventListener('click', () => {
        const r = game.startFlow(ui.source, ui.target, o.id);
        if (r.ok) { ui.mode = 'idle'; renderPick(); renderCard(); note('Поток запущен: грузы поедут по выбранному маршруту.'); }
      });
      list.append(btn);
    }
  }
}
function renderFlows() {
  $('#f-count').textContent = String(S.flows.length);
  const ul = $('#f-list');
  ul.replaceChildren();
  if (!S.flows.length) { const li = document.createElement('li'); li.textContent = 'Пока нет. Выберите шахту или завод.'; ul.append(li); return; }
  for (const f of S.flows) {
    const li = document.createElement('li');
    const a = S.world.nodes[f.fromId], b = S.world.nodes[f.toId];
    const t = document.createElement('span');
    t.textContent = `${a.place.replace(' (тест)', '')} → ${b.place.replace(' (тест)', '')}: ${f.label}, ≈ ${f.days} усл. дн.`;
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'plain'; x.textContent = 'Стоп'; x.setAttribute('aria-label', 'Остановить поток');
    x.addEventListener('click', () => game.stopFlow(f.id));
    li.append(t, x); ul.append(li);
  }
}
$('#k-flow').addEventListener('click', () => { ui.mode = 'pick'; ui.source = ui.selected; window.__scene?.markTargets(); renderPick(); });
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
  ui.selected = id;
  renderCard();
  game.moveBohun(id);
}

// ---------- scene ----------
const COL = { rail: 0xe6dcb4, road: 0xb79b6a, sea: 0x69b3e8, air: 0xf4f4ff };
const NODE_PX = { mine: 9, 'factory-s': 8, 'factory-m': 11, 'factory-l': 14, bank: 10, port: 9, station: 8, airfield: 9 };

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
    this.label = this.add.text(0, 0, '', { fontFamily: 'system-ui, sans-serif', fontSize: '13px', fontStyle: '600', color: '#f1efe8', backgroundColor: 'rgba(14,18,16,.9)', padding: { x: 5, y: 2 }, resolution: dpr }).setOrigin(0.5, 1).setDepth(10).setVisible(false);
    this.bohun = this.add.image(0, 0, 'rider').setOrigin(rider.anchor.x / rider.size[0], rider.anchor.y / rider.size[1]).setDepth(8);
    this.facing = 1;
    this.targets = new Set();

    this.layoutMini();
    this.mini = this.cameras.add(this.miniRect.x, this.miniRect.y, this.miniRect.w, this.miniRect.h);
    this.mini.setBackgroundColor('#101412');
    this.cameras.main.ignore([this.miniGfx]);
    this.mini.ignore([this.lines, this.dyn, this.label, this.bohun]);
    this.applyMini();

    this.zoomv = 2.0; this.cx = project0().x; this.cy = project0().y; this.all = false;
    this.drag = null;
    this.input.on('pointerdown', (p) => { this.drag = { x: p.x, y: p.y, moved: false, mini: this.inMini(p) }; });
    this.input.on('pointermove', (p) => this.onMove(p));
    this.input.on('pointerup', (p) => { const d = this.drag; this.drag = null; if (d && !d.moved) this.click(p); });
    this.input.on('wheel', (p, _o, _dx, dy) => this.zoomAt(p, Math.exp(-dy * 0.0012)));
    this.scale.on('resize', () => { this.layoutMini(); this.applyMini(); });
    this.minZoom = () => Math.min(innerWidth / WORLD.width, innerHeight / WORLD.height) * 1.0;
    renderFlows();
  }

  layoutMini() {
    const wcss = Math.min(300, Math.max(160, innerWidth * 0.26)), hcss = (wcss * WORLD.height) / WORLD.width;
    const narrow = innerWidth <= 720;
    const x = innerWidth - wcss - (narrow ? 8 : 12), y = innerHeight - hcss - (narrow ? innerHeight * 0.46 + 24 : 12);
    this.miniRect = { x: x * dpr, y: y * dpr, w: wcss * dpr, h: hcss * dpr, css: { x, y, w: wcss, h: hcss } };
    const m = $('#mini'); Object.assign(m.style, { width: wcss + 'px', height: hcss + 'px', left: x + 'px', top: y + 'px', right: 'auto', bottom: 'auto' });
  }
  applyMini() {
    const r = this.miniRect;
    this.mini.setViewport(r.x, r.y, r.w, r.h); this.mini.setZoom(r.w / WORLD.width); this.mini.centerOn(WORLD.width / 2, WORLD.height / 2);
  }

  home() { this.all = false; this.zoomv = 2.0; this.cx = project0().x; this.cy = project0().y; }
  toggleAll() {
    if (this.all) return this.home();
    this.all = true; this.zoomv = this.minZoom(); this.cx = WORLD.width / 2; this.cy = WORLD.height / 2;
  }
  zoomAt(p, f) {
    const cam = this.cameras.main, w0 = cam.getWorldPoint(p.x, p.y);
    this.zoomv = Phaser.Math.Clamp(this.zoomv * f, this.minZoom(), 9);
    cam.setZoom(this.zoomv * dpr);
    const w1 = cam.getWorldPoint(p.x, p.y);
    this.cx += w0.x - w1.x; this.cy += w0.y - w1.y; this.all = false;
  }
  inMini(p) { const r = this.miniRect; return p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h; }
  jumpMini(p) { const r = this.miniRect; this.cx = ((p.x - r.x) / r.w) * WORLD.width; this.cy = ((p.y - r.y) / r.h) * WORLD.height; this.all = false; }

  onMove(p) {
    const d = this.drag;
    if (d?.mini && p.isDown) return this.jumpMini(p);
    if (!d || !p.isDown) { this.hover = this.pick(p); return; }
    if (Math.abs(p.x - d.x) + Math.abs(p.y - d.y) > 8 * dpr) d.moved = true;
    if (d.moved && !d.mini) { this.cx -= (p.x - p.prevPosition.x) / (this.zoomv * dpr); this.cy -= (p.y - p.prevPosition.y) / (this.zoomv * dpr); this.all = false; }
    // pinch
    const a = this.input.pointer1, b = this.input.pointer2;
    if (a.isDown && b.isDown) {
      const dist = Phaser.Math.Distance.Between(a.x, a.y, b.x, b.y);
      if (this.pinch) this.zoomAt({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, dist / this.pinch);
      this.pinch = dist; d.moved = true;
    } else this.pinch = 0;
  }
  click(p) {
    if (this.inMini(p)) return this.jumpMini(p);
    const id = this.pick(p);
    if (id) chooseNode(id);
  }
  pick(p) {
    if (this.inMini(p)) return null;
    const w = this.cameras.main.getWorldPoint(p.x, p.y), reach = 24 / this.zoomv;
    let best = null, bd = reach;
    for (const n of Object.values(S.world.nodes)) { const d = Math.hypot(w.x - n.x, w.y - n.y); if (d < bd) { bd = d; best = n.id; } }
    return best;
  }
  markTargets() {
    this.targets = new Set(Object.keys(S.world.nodes).filter((id) => id !== ui.source && game.routeOptions(ui.source, id).length));
  }
  popCoins(nodeId) { const n = S.world.nodes[nodeId]; for (let i = 0; i < 5; i++) coins.push({ x: n.x, y: n.y, age: -i * 0.12, kind: i % 2 }); }

  update(time, delta) {
    let remain = Math.min(delta, 1000) / 1000;
    while (remain > 1e-6) { const dt = Math.min(0.05, remain); game.tick(dt); remain -= dt; }
    const cam = this.cameras.main, k = 1 / this.zoomv;
    this.cx = Phaser.Math.Clamp(this.cx, 0, WORLD.width); this.cy = Phaser.Math.Clamp(this.cy, 0, WORLD.height);
    // keep the interesting part clear of the panel: desktop panel on the left, phone panel at the bottom
    const narrow = innerWidth <= 720, ox = narrow ? 0 : 176, oy = narrow ? innerHeight * 0.23 : 0;
    cam.setZoom(this.zoomv * dpr); cam.centerOn(this.cx - ox * k, this.cy + oy * k);

    const L = this.lines, D = this.dyn, M = this.miniGfx;
    L.clear(); D.clear(); M.clear();
    const sel = ui.selected;
    for (const e of Object.values(S.world.edges)) {
      const hot = sel && (e.a === sel || e.b === sel);
      L.lineStyle((hot ? 3.6 : 1.8) * k, COL[e.mode], hot ? 0.95 : 0.5);
      if (e.mode === 'sea' || e.mode === 'air') dashed(L, e.pts, (e.mode === 'air' ? 4 : 10) * k, 6 * k); else L.strokePoints(e.pts, false, false);
    }
    for (const f of S.flows) for (const s of f.steps) { L.lineStyle(5 * k, 0xffd54a, 0.9); L.strokePoints(s.pts, false, false); }

    for (const n of Object.values(S.world.nodes)) this.drawNode(D, n, k);
    // ambient movers and flow carriers
    for (const a of S.ambient) this.drawMover(D, a.kind, pointAt(a.pts, a.d), k, 0.85, false);
    for (const c of S.carriers) { const s = c.steps[Math.min(c.leg, c.steps.length - 1)]; this.drawMover(D, MODES[s.mode].kind, pointAt(s.pts, c.d), k, 1, true); }
    // coins
    for (let i = coins.length - 1; i >= 0; i--) {
      const c = coins[i]; c.age += delta / 1000;
      if (c.age > 1.4) { coins.splice(i, 1); continue; }
      if (c.age < 0) continue;
      const a = 1 - c.age / 1.4, y = c.y - c.age * 34 * k, x = c.x + (c.kind ? 6 : -6) * k * c.age;
      if (c.kind) { D.fillStyle(0x4caf50, a); D.fillRect(x - 5 * k, y - 3 * k, 10 * k, 6 * k); } else { D.fillStyle(0xf5c542, a); D.fillCircle(x, y, 4 * k); }
    }
    // rings: hover, selection, valid targets
    if (ui.mode === 'pick') for (const id of this.targets) { const n = S.world.nodes[id]; D.lineStyle(2 * k, 0x8fe08f, 0.9); D.strokeCircle(n.x, n.y, 15 * k); }
    if (ui.mode === 'pick' && ui.source) { const n = S.world.nodes[ui.source]; D.lineStyle(3 * k, 0xffd54a, 1); D.strokeCircle(n.x, n.y, 16 * k); }
    if (sel && ui.mode !== 'pick') { const n = S.world.nodes[sel]; const pulse = 0.5 + 0.5 * Math.sin(time / 260); D.lineStyle(3 * k, 0xffd54a, 0.6 + 0.4 * pulse); D.strokeCircle(n.x, n.y, (14 + 3 * pulse) * k); }
    const hv = this.hover && S.world.nodes[this.hover];
    if (hv) { this.label.setVisible(true).setScale(k).setPosition(hv.x, hv.y - 14 * k).setText(`${hv.name} · ${hv.place.replace(' (тест)', '')}`); } else this.label.setVisible(false);

    // Bohun
    const bp = game.bohunPos();
    if (bp.ax) this.facing = bp.ax < 0 ? -1 : (bp.ax > 0 && S.bohun.status === 'moving' ? 1 : this.facing);
    const bpx = Phaser.Math.Clamp(20 + this.zoomv * 18, 20, 56); // smaller when the whole world is shown
    const sc = ((bpx / rider.visibleBBox.h) * k);
    D.fillStyle(0x0e1210, 0.5); D.fillEllipse(bp.x, bp.y, 40 * k, 10 * k);
    this.bohun.setPosition(bp.x, bp.y).setScale(sc * this.facing, sc);

    // minimap: nodes and the current view
    for (const n of Object.values(S.world.nodes)) { M.fillStyle(n.type === 'bank' ? 0xf5c542 : n.type === 'port' ? 0x69b3e8 : 0xffffff, 1); M.fillCircle(n.x, n.y, 16); }
    const wv = cam.worldView;
    M.lineStyle(14, 0xffd54a, 1); M.strokeRect(wv.x, wv.y, wv.width, wv.height);
    $('#c-delivered').textContent = String(S.delivered); $('#c-coins').textContent = String(S.coins);
  }

  drawNode(g, n, k) {
    const s = NODE_PX[n.type] * k, x = n.x, y = n.y;
    g.lineStyle(1.5 * k, 0x0e1210, 1);
    if (n.type === 'mine') { g.fillStyle(0x3b3a37, 1); g.fillTriangle(x, y - s, x + s, y + s * 0.7, x - s, y + s * 0.7); g.strokeTriangle(x, y - s, x + s, y + s * 0.7, x - s, y + s * 0.7); g.lineStyle(2 * k, 0xc9a24a, 1); g.lineBetween(x - s * 0.5, y + s * 0.1, x + s * 0.5, y + s * 0.1); }
    else if (n.type.startsWith('factory')) { g.fillStyle(0x8a97a3, 1); g.fillRect(x - s, y - s * 0.6, s * 2, s * 1.4); g.strokeRect(x - s, y - s * 0.6, s * 2, s * 1.4); g.fillStyle(0x4b5156, 1); g.fillRect(x + s * 0.3, y - s * 1.5, s * 0.5, s * 1.0); }
    else if (n.type === 'bank') { g.fillStyle(0xe8c85a, 1); g.fillRect(x - s, y - s * 0.5, s * 2, s * 1.3); g.strokeRect(x - s, y - s * 0.5, s * 2, s * 1.3); g.fillStyle(0xf6efd0, 1); g.fillTriangle(x - s * 1.15, y - s * 0.5, x + s * 1.15, y - s * 0.5, x, y - s * 1.3); }
    else if (n.type === 'port') { g.fillStyle(0x2a7fc4, 1); g.fillCircle(x, y, s); g.strokeCircle(x, y, s); g.lineStyle(2 * k, 0xffffff, 1); g.lineBetween(x, y - s * 0.6, x, y + s * 0.6); g.lineBetween(x - s * 0.5, y + s * 0.2, x + s * 0.5, y + s * 0.2); }
    else if (n.type === 'station') { g.fillStyle(0xc8b78c, 1); g.fillRect(x - s, y - s * 0.5, s * 2, s); g.strokeRect(x - s, y - s * 0.5, s * 2, s); g.lineStyle(1.5 * k, 0x3a3328, 1); g.lineBetween(x - s, y, x + s, y); }
    else { g.fillStyle(0xeaeaf5, 1); g.fillCircle(x, y, s * 0.9); g.strokeCircle(x, y, s * 0.9); g.lineStyle(2.4 * k, 0x2b2f3a, 1); g.lineBetween(x - s * 0.6, y, x + s * 0.6, y); g.lineBetween(x, y - s * 0.6, x, y + s * 0.6); }
  }

  drawMover(g, kind, p, k, a, accent) {
    const len = Math.hypot(p.ax, p.ay) || 1, ux = p.ax / len, uy = p.ay / len, vx = -uy, vy = ux;
    const u = (l, w) => ({ x: p.x + ux * l * k + vx * w * k, y: p.y + uy * l * k + vy * w * k });
    const poly = (pts, fill) => { g.fillStyle(fill, a); g.fillPoints(pts, true); if (accent) { g.lineStyle(1.6 * k, 0xffd54a, 1); g.strokePoints(pts, true); } };
    if (kind === 'train') for (let i = 0; i < 3; i++) poly([u(-6 + i * 7, -2.2), u(-1 + i * 7, -2.2), u(-1 + i * 7, 2.2), u(-6 + i * 7, 2.2)], i === 0 ? 0xc0392b : 0x8b6f47);
    else if (kind === 'truck') poly([u(-5, -2.6), u(5, -2.6), u(5, 2.6), u(-5, 2.6)], 0x6f7a55);
    else if (kind === 'wagon') poly([u(-4, -3), u(4, -3), u(4, 3), u(-4, 3)], 0x9a6b3a);
    else if (kind === 'ship') poly([u(-8, -3), u(5, -3), u(9, 0), u(5, 3), u(-8, 3)], 0xdfe6ee);
    else poly([u(8, 0), u(-4, -7), u(-1, 0), u(-4, 7)], 0xf4f4ff);
  }
}

function dashed(g, pts, dash, gap) {
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], len = Math.hypot(b.x - a.x, b.y - a.y); if (!len) continue;
    const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
    for (let d = 0; d < len; d += dash + gap) { const e = Math.min(d + dash, len); g.lineBetween(a.x + ux * d, a.y + uy * d, a.x + ux * e, a.y + uy * e); }
  }
}
function project0() { const n = S.world.nodes['bank-kyiv']; return { x: n.x, y: n.y }; }

window.__phaser = new Phaser.Game({
  type: Phaser.AUTO, parent: 'stage', backgroundColor: '#101412',
  scale: { mode: Phaser.Scale.NONE, width: innerWidth * dpr, height: innerHeight * dpr, zoom: 1 / dpr },
  input: { touch: { capture: true }, activePointers: 2 },
  scene: [FlowsScene],
});
window.addEventListener('resize', () => window.__phaser?.scale.resize(innerWidth * dpr, innerHeight * dpr));
