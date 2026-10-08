import Phaser from 'phaser';
import { createGame, pointOnPath } from './core.js';
import { scenario } from './scenario.js';
import { SCENE } from './geo.js';
import rider from './data/rider-sprite.json' with { type: 'json' };

const $ = (s) => document.querySelector(s);
const dpr = Math.min(window.devicePixelRatio || 1, 2);

// ---------- plain-language layer (Russian). Ids, coordinates and rules live in core/scenario ----------

const REASON_RU = {
  busy: 'это действие уже выполняется',
  locked: 'этот путь пока закрыт, сначала проведите исследование',
  'no-route': 'такого пути нет',
  'already-there': 'Богун уже там',
  'already-done': 'это уже сделано',
  'unknown-city': 'такого города нет',
  'unknown-route': 'такого пути нет',
  'unknown-node': 'такого узла нет',
};
const CODE_RU = {
  moving: 'Богун сейчас в пути',
  here: 'Богун уже здесь',
  'no-route': 'отсюда туда нет пути',
  locked: 'путь закрыт',
  researching: 'исследование идёт',
  done: 'уже сделано',
  underway: 'груз уже в пути',
};
const NAME_RU = Object.fromEntries(scenario.nodes.map((n) => [n.id, n.labelRu]));

// three steps; each one: what it is, what the player presses, what the result is
const STEPS = [
  {
    title: 'Отправить Богуна в Киев',
    desc: 'Богун поедет по пути из Львова в Киев. Когда он приедет, откроется карточка города.',
    button: 'Отправить Богуна',
    result: () => 'Богун приехал в Киев. Карточка города открылась.',
    action: 'send', target: 'kyiv',
  },
  {
    title: 'Доставить груз из Львова в Киев',
    desc: 'Демонстрационный груз поедет по тому же пути. Когда он доедет, счётчик вырастет на 1.',
    button: 'Отправить груз',
    result: () => `Груз доехал. Доставлено: ${S.counter}.`,
    action: 'ship', target: 'lviv-kyiv',
  },
  {
    title: 'Провести исследование',
    desc: 'Закрытый узел на карте нужно изучить. Когда исследование закончится, откроется путь Львов → Варшава.',
    button: 'Начать исследование',
    result: () => 'Исследование завершено. Путь Львов → Варшава открыт.',
    action: 'research', target: 'site-1',
  },
];

// progress of the current run; reset together with the demo
const ui = { done: [false, false, false], noteTimer: null };

const game = createGame(scenario, { onEvent });
const S = game.state;
window.__game = game; // test hook

function onEvent(type, p) {
  if (type === 'reset') ui.done = [false, false, false];
  if (type === 'bohunArrived' && p.at === 'kyiv') ui.done[0] = true;
  if (type === 'cargoArrived' && p.routeId === 'lviv-kyiv') ui.done[1] = true;
  if (type === 'researchCompleted') ui.done[2] = true;
  if (type === 'dossierRequested') showDossier(p.dossierId);
  if (type === 'rejected') note(`Так нельзя: ${REASON_RU[p.reason] ?? p.reason}.`);
  devLog(type, p);
}

function note(text) {
  const el = $('#note');
  el.textContent = text;
  clearTimeout(ui.noteTimer);
  ui.noteTimer = setTimeout(() => (el.textContent = ''), 4000);
}

// technical event log, kept for the developer section only
function devLog(type, p) {
  const log = $('#log');
  if (!log || type === 'stateChanged') return;
  const li = document.createElement('li');
  li.textContent = `${type} ${JSON.stringify(p)}`;
  if (type === 'rejected') li.className = 'rej';
  log.prepend(li);
  while (log.children.length > 30) log.lastChild.remove();
}

// ---------- panel ----------

const steps = STEPS.map((st, i) => {
  const li = document.createElement('li');
  li.className = 'step';
  li.innerHTML = '<div class="head"><span class="num"></span><h3></h3></div><p class="txt"></p><div class="bar"><i></i></div>';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = st.button;
  btn.addEventListener('click', () => game.run(st.action, st.target));
  li.append(btn);
  li.querySelector('.num').textContent = String(i + 1);
  li.querySelector('h3').textContent = st.title;
  $('#steps').append(li);
  return { li, btn, txt: li.querySelector('.txt'), bar: li.querySelector('.bar i'), cls: '', text: '', disabled: null };
});

const cache = { now: '', counter: '', extrasSig: '', cmdSig: '', mode: '', all: '' };
const setText = (el, key, v) => { if (cache[key] !== v) { cache[key] = v; el.textContent = v; } };

const eta = (len, speed, t) => Math.max(1, Math.ceil(((1 - t) * len) / speed));
const currentStep = () => ui.done.findIndex((d) => !d);

function stepState(i) {
  if (ui.done[i]) return 'done';
  const busy =
    (i === 0 && S.bogun.status === 'moving' && S.bogun.to === 'kyiv') ||
    (i === 1 && S.transports.some((t) => t.routeId === 'lviv-kyiv')) ||
    (i === 2 && S.nodes['site-1'].state === 'researching');
  if (busy) return 'busy';
  return i === currentStep() ? 'current' : 'locked';
}

function renderSteps(cmds) {
  STEPS.forEach((st, i) => {
    const v = steps[i];
    const state = stepState(i);
    let text, disabled = true, pct = 0;
    if (state === 'done') {
      text = '✓ ' + st.result();
    } else if (state === 'busy') {
      if (i === 0) {
        const r = S.routes[S.bogun.routeId];
        text = `Богун едет… осталось около ${eta(r.length, scenario.rules.bogunSpeed, S.bogun.t)} с.`;
        pct = S.bogun.t;
      } else if (i === 1) {
        const t = S.transports.find((x) => x.routeId === 'lviv-kyiv');
        text = `Груз едет… осталось около ${eta(S.routes['lviv-kyiv'].length, scenario.rules.transportSpeed, t.t)} с.`;
        pct = t.t;
      } else {
        pct = S.nodes['site-1'].progress;
        text = `Исследование идёт: ${Math.round(pct * 100)}%.`;
      }
    } else if (state === 'current') {
      const c = cmds.find((x) => x.action === st.action && x.target === st.target);
      disabled = !c?.enabled;
      text = disabled ? `${st.desc} Сейчас недоступно: ${CODE_RU[c?.code] ?? 'подождите'}.` : st.desc;
    } else {
      text = `Откроется после шага ${i}.`;
    }
    const cls = `step ${state}`;
    if (v.cls !== cls) { v.cls = cls; v.li.className = cls; }
    if (v.text !== text) { v.text = text; v.txt.textContent = text; }
    if (v.disabled !== disabled) { v.disabled = disabled; v.btn.disabled = disabled; }
    v.btn.hidden = state === 'done' || state === 'busy';
    v.bar.style.width = `${Math.round(pct * 100)}%`;
  });
}

function nowText() {
  const cur = currentStep();
  if (cur === -1) return 'Готово! Все три шага пройдены, путь Львов → Варшава открыт. Его можно попробовать ниже или нажать «Сначала».';
  return `Шаг ${cur + 1} из 3. ${STEPS[cur].title}.${stepState(cur) === 'busy' ? ' Идёт выполнение.' : ' Нажмите кнопку ниже.'}`;
}

function renderExtras(cmds) {
  $('#more').hidden = !ui.done[2];
  if (!ui.done[2]) return;
  const list = cmds.filter((c) => c.action !== 'research');
  const sig = JSON.stringify(list.map((c) => [c.action, c.target, c.enabled, c.code]));
  if (sig === cache.extrasSig) return;
  cache.extrasSig = sig;
  const wrap = $('#extras');
  wrap.replaceChildren();
  for (const c of list) {
    const b = document.createElement('button');
    b.type = 'button';
    b.disabled = !c.enabled;
    if (c.action === 'send') b.append(`Отправить Богуна: ${NAME_RU[c.target]}`);
    else {
      const r = S.routes[c.target];
      b.append(`Отправить груз: ${NAME_RU[r.fromId]} → ${NAME_RU[r.toId]}`);
    }
    if (c.code) {
      const s = document.createElement('small');
      s.textContent = CODE_RU[c.code] ?? '';
      b.append(s);
    }
    b.addEventListener('click', () => game.run(c.action, c.target));
    wrap.append(b);
  }
}

function renderDev(cmds) {
  const sig = JSON.stringify(cmds.map((c) => [c.action, c.target, c.enabled, c.reason]));
  if (sig !== cache.cmdSig) {
    cache.cmdSig = sig;
    const wrap = $('#commands');
    wrap.replaceChildren();
    for (const c of cmds) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'plain';
      b.disabled = !c.enabled;
      b.append(c.label);
      if (c.reason) {
        const s = document.createElement('small');
        s.textContent = c.reason;
        b.append(s);
      }
      b.addEventListener('click', () => game.run(c.action, c.target));
      wrap.append(b);
    }
  }
  setText($('#mode'), 'mode', `Камерой управляет: ${S.mode === 'game' ? 'игрок' : 'сайт'} (переключить)`);
}

function renderPanel() {
  setText($('#counter'), 'counter', String(S.counter));
  setText($('#now'), 'now', nowText());
  const cmds = game.commands();
  renderSteps(cmds);
  renderExtras(cmds);
  renderDev(cmds);
  const allBtn = $('#allmap');
  setText(allBtn, 'all', window.__view?.all ? 'К городам' : 'Вся карта');
  allBtn.disabled = S.mode !== 'game';
  allBtn.title = S.mode !== 'game' ? 'Камерой сейчас управляет сайт' : '';
}

// the HTML layer listens for dossierRequested; the game logic never reaches into the page
function showDossier(id) {
  const c = scenario.dossiers[id];
  $('#c-file').textContent = c.file;
  $('#c-title').textContent = c.title;
  $('#c-body').textContent = c.body;
  $('#card').hidden = false;
}
const hideDossier = () => ($('#card').hidden = true);

$('#c-close').addEventListener('click', hideDossier);
$('#reset').addEventListener('click', () => {
  hideDossier();
  game.resetDemo();
  window.__view?.showCities();
  note('Демонстрация начата сначала.');
});
$('#mode').addEventListener('click', () => game.setMode(S.mode === 'game' ? 'presentation' : 'game'));
$('#allmap').addEventListener('click', () => window.__view?.toggleAll());
window.addEventListener('keydown', (e) => e.key === 'Escape' && hideDossier());

// ---------- temporary view ----------
// Background: the contract's technical SVG (not final art), drawn at world size 2560 x 1600 so that world units
// equal SVG units. Bohun is the Classic rider PNG (one static pose, see public/assets/bogun/PROVENANCE.md). All object sizes are in
// screen pixels (scaled by k = 1 / zoom) so they stay readable at any zoom.
const MAX_STEP = 0.05; // simulation step, seconds
const MAX_FRAME = 1; // never simulate more than this per rendered frame

function rasterWidth() {
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const max = gl ? gl.getParameter(gl.MAX_TEXTURE_SIZE) : 0;
    return max >= 4096 ? 3840 : SCENE.width;
  } catch {
    return SCENE.width;
  }
}

function dashed(g, pts, dash, gap) {
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
    for (let d = 0; d < len; d += dash + gap) {
      const e = Math.min(d + dash, len);
      g.lineBetween(a.x + ux * d, a.y + uy * d, a.x + ux * e, a.y + uy * e);
    }
  }
}

class View extends Phaser.Scene {
  constructor() { super('view'); }

  preload() {
    this.load.image('rider-src', rider.url);
    const w = rasterWidth();
    this.load.svg('base', scenario.scene.background, { width: w, height: Math.round((w * SCENE.height) / SCENE.width) });
  }

  create() {
    this.cameras.main.setBackgroundColor('#101412');
    this.add.image(0, 0, 'base').setOrigin(0, 0).setDisplaySize(SCENE.width, SCENE.height).setDepth(0);

    const xs = scenario.nodes.map((n) => n.x), ys = scenario.nodes.map((n) => n.y);
    this.home = { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2 };
    this.span = { w: Math.max(...xs) - Math.min(...xs) + 260, h: Math.max(...ys) - Math.min(...ys) + 300 };
    this.all = false;
    this.showCities();
    this.scale.on('resize', () => (this.all ? this.showAll() : this.showCities()));
    window.__view = this; // site hook: in presentation mode the site sets cx, cy, zoomv

    this.lines = this.add.graphics().setDepth(2);
    this.dyn = this.add.graphics().setDepth(4);
    this.buildRider();
    const style = { fontFamily: 'system-ui, sans-serif', fontSize: '14px', fontStyle: '600', color: '#f1efe8', backgroundColor: 'rgba(14,18,16,.88)', padding: { x: 6, y: 3 }, resolution: dpr };
    this.texts = {};
    for (const n of scenario.nodes) this.texts[n.id] = this.add.text(n.x, n.y, n.labelRu, style).setOrigin(0.5, 0).setDepth(10);
    // route geometry never changes, but resetDemo rebuilds the route state objects: keep view data outside them
    this.mids = {};
    for (const r of Object.values(S.routes)) {
      const mid = pointOnPath(r.pts, 0.5);
      this.mids[r.id] = mid;
      this.texts[r.id] = this.add.text(mid.x, mid.y, 'Груз', { ...style, color: '#c9a24a' }).setOrigin(0.5).setDepth(10);
    }

    this.moved = false;
    this.drag = null;
    this.input.on('pointerdown', (p) => { this.drag = { x: p.x, y: p.y }; this.moved = false; });
    this.input.on('pointermove', (p) => {
      if (S.mode !== 'game' || !this.drag || !p.isDown) return; // one camera owner
      if (Math.abs(p.x - this.drag.x) + Math.abs(p.y - this.drag.y) > 8 * dpr) this.moved = true;
      if (this.moved) {
        this.cx = Phaser.Math.Clamp(this.cx - (p.x - p.prevPosition.x) / (this.zoomv * dpr), 0, SCENE.width);
        this.cy = Phaser.Math.Clamp(this.cy - (p.y - p.prevPosition.y) / (this.zoomv * dpr), 0, SCENE.height);
      }
    });
    this.input.on('pointerup', (p) => {
      const wasDrag = this.moved;
      this.drag = null;
      this.moved = false;
      if (!wasDrag) this.click(p);
    });
    this.input.on('wheel', (_p, _o, _dx, dy) => {
      if (S.mode !== 'game') return;
      this.zoomv = Phaser.Math.Clamp(this.zoomv * Math.exp(-dy * 0.001), this.minZoom, 3);
    });
  }

  // Bohun: the Classic rider, one static pose. The PNG is loaded as is; a smaller copy is made in memory only for
  // clean down-scaling (the figure is shown at ~90 screen px, the file is 1254 px). If the file is missing, a
  // plain marker is drawn instead.
  buildRider() {
    this.riderFacing = 1;
    this.rider = null;
    if (!this.textures.exists('rider-src')) return;
    const src = this.textures.get('rider-src').getSourceImage();
    const N = rider.runtimeTextureSize;
    let cur = document.createElement('canvas');
    cur.width = cur.height = src.width;
    cur.getContext('2d').drawImage(src, 0, 0);
    // halve step by step, then land on N: avoids the shimmer of a single 3x+ reduction
    while (cur.width > N * 2) {
      const next = document.createElement('canvas');
      next.width = next.height = Math.ceil(cur.width / 2);
      const c = next.getContext('2d');
      c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high';
      c.drawImage(cur, 0, 0, next.width, next.height);
      cur = next;
    }
    const tex = this.textures.createCanvas('rider', N, N);
    const ctx = tex.getContext();
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(cur, 0, 0, N, N);
    tex.refresh();
    this.textures.remove('rider-src');
    this.rider = this.add.image(0, 0, 'rider').setOrigin(rider.anchor.x / rider.size[0], rider.anchor.y / rider.size[1]).setDepth(6);
  }

  // on-screen height of the visible figure, in screen px
  riderHeightPx() {
    return innerWidth <= 720 ? rider.screenHeightPx.narrow : rider.screenHeightPx.desktop;
  }

  // part of the screen the panel leaves free
  free() {
    const narrow = innerWidth <= 720;
    return {
      w: narrow ? innerWidth : innerWidth - 392,
      h: narrow ? innerHeight * 0.45 : innerHeight,
      ox: narrow ? 0 : 190,
      oy: narrow ? innerHeight * 0.27 : 0,
    };
  }

  // frame Lviv, Kyiv, Warsaw and the closed node
  showCities() {
    const f = this.free();
    this.all = false;
    this.minZoom = Math.min(f.w / SCENE.width, f.h / SCENE.height) * 0.9;
    this.zoomv = Phaser.Math.Clamp(Math.min(f.w / this.span.w, f.h / this.span.h), this.minZoom, 2.5);
    this.cx = this.home.x; this.cy = this.home.y; this.ox = f.ox; this.oy = f.oy;
  }

  // the whole 2560 x 1600 scene
  showAll() {
    const f = this.free();
    this.all = true;
    this.minZoom = Math.min(f.w / SCENE.width, f.h / SCENE.height) * 0.9;
    this.zoomv = Math.min(f.w / SCENE.width, f.h / SCENE.height) * 0.97;
    this.cx = SCENE.width / 2; this.cy = SCENE.height / 2; this.ox = f.ox; this.oy = f.oy;
  }

  toggleAll() {
    if (S.mode !== 'game') return;
    if (this.all) this.showCities(); else this.showAll();
  }

  // screen-constant picking: nearest city, closed node or route chip within ~30 screen px
  click(p) {
    const w = this.cameras.main.getWorldPoint(p.x, p.y);
    const reach = 30 / this.zoomv;
    let best = null;
    const consider = (kind, id, x, y) => {
      const d = Math.hypot(w.x - x, w.y - y);
      if (d <= reach && (!best || d < best.d)) best = { kind, id, d };
    };
    for (const n of scenario.nodes) consider('node', n.id, n.x, n.y - 10 / this.zoomv);
    for (const r of Object.values(S.routes)) consider('route', r.id, this.mids[r.id].x, this.mids[r.id].y);
    if (!best) return;
    if (best.kind === 'route') return game.dispatchCargo(best.id);
    const n = S.nodes[best.id];
    if (n.kind === 'city') game.moveBohun(n.id);
    if (n.kind === 'site') game.research(n.id);
  }

  // where the pulsing hint ring goes: the target of the current step (none while it is running)
  hint() {
    const cur = currentStep();
    if (cur === -1 || stepState(cur) !== 'current') return null;
    if (cur === 0) return S.nodes.kyiv;
    if (cur === 1) return this.mids['lviv-kyiv'];
    return S.nodes['site-1'];
  }

  update(time, delta) {
    // real time: follow the wall clock in fixed steps so a slow frame does not slow the game down
    let remain = Math.min(delta, MAX_FRAME * 1000) / 1000;
    while (remain > 1e-6) {
      const dt = Math.min(MAX_STEP, remain);
      game.tick(dt);
      remain -= dt;
    }

    const cam = this.cameras.main;
    const k = 1 / this.zoomv;
    cam.setZoom(this.zoomv * dpr);
    cam.centerOn(this.cx - this.ox * k, this.cy + this.oy * k);

    const L = this.lines, D = this.dyn;
    L.clear(); D.clear();
    for (const r of Object.values(S.routes)) {
      if (r.available) L.lineStyle(4 * k, 0xf3d98b, 1); else L.lineStyle(3 * k, 0x2b3a36, 0.9);
      dashed(L, r.pts, 14 * k, 10 * k);
      const chip = this.texts[r.id];
      chip.setPosition(this.mids[r.id].x, this.mids[r.id].y).setScale(k).setAlpha(r.available ? 1 : 0.55).setText(r.available ? 'Груз' : 'Путь закрыт');
    }
    for (const n of scenario.nodes) {
      const s = S.nodes[n.id];
      const t = this.texts[n.id];
      t.setPosition(n.x, n.y + 14 * k).setScale(k);
      if (n.kind === 'city') {
        D.fillStyle(0xfbe6b0, 1); D.fillCircle(n.x, n.y, 9 * k);
        D.lineStyle(2.5 * k, 0x182e32, 1); D.strokeCircle(n.x, n.y, 9 * k);
      } else {
        const col = s.state === 'open' ? 0xc9a24a : 0x182e32;
        const pts = [{ x: n.x, y: n.y - 12 * k }, { x: n.x + 12 * k, y: n.y }, { x: n.x, y: n.y + 12 * k }, { x: n.x - 12 * k, y: n.y }];
        D.fillStyle(col, 1); D.fillPoints(pts, true);
        D.lineStyle(2 * k, 0xfbe6b0, 1); D.strokePoints(pts, true);
        if (s.state === 'researching') {
          D.lineStyle(4 * k, 0xc9a24a, 1);
          D.beginPath(); D.arc(n.x, n.y, 20 * k, -Math.PI / 2, -Math.PI / 2 + s.progress * Math.PI * 2); D.strokePath();
        }
      }
      t.setText(s.state === 'researching' ? `${n.labelRu} · ${Math.round(s.progress * 100)}%` : s.state === 'open' && n.kind === 'site' ? 'Узел открыт' : n.labelRu);
    }
    for (const t of S.transports) {
      const p = pointOnPath(S.routes[t.routeId].pts, t.t);
      D.fillStyle(0xe3c33a, 1); D.fillRect(p.x - 6 * k, p.y - 6 * k, 12 * k, 12 * k);
      D.lineStyle(2 * k, 0x0e1210, 1); D.strokeRect(p.x - 6 * k, p.y - 6 * k, 12 * k, 12 * k);
    }
    const b = S.bogun;
    let bp;
    if (b.status === 'moving') {
      const r = S.routes[b.routeId];
      bp = pointOnPath(r.fromId === b.from ? r.pts : [...r.pts].reverse(), b.t);
    } else bp = { x: S.nodes[b.at].x, y: S.nodes[b.at].y };
    // Bohun stands on his anchor (the hooves) and slides along the route in one static pose. No gallop animation.
    D.fillStyle(0x0e1210, 0.5); D.fillEllipse(bp.x, bp.y, 64 * k, 15 * k);
    if (this.rider) {
      if (b.status === 'moving') {
        const r = S.routes[b.routeId];
        const pts = r.fromId === b.from ? r.pts : [...r.pts].reverse();
        const ahead = pointOnPath(pts, Math.min(1, b.t + 0.02)), behind = pointOnPath(pts, Math.max(0, b.t - 0.02));
        if (Math.abs(ahead.x - behind.x) > 0.01) this.riderFacing = ahead.x < behind.x ? -1 : 1; // the picture faces right
      }
      // scale so the visible figure (bbox height) is riderHeightPx() screen px; sizes are screen-constant like all markers
      const sc = (this.riderHeightPx() / rider.visibleBBox.h) * (rider.size[0] / rider.runtimeTextureSize) * k;
      this.rider.setPosition(bp.x, bp.y).setScale(sc * this.riderFacing, sc);
    } else {
      D.fillStyle(0xc9a24a, 1); D.fillTriangle(bp.x, bp.y - 26 * k, bp.x + 11 * k, bp.y - 3 * k, bp.x - 11 * k, bp.y - 3 * k);
    }

    // pulsing ring on the target of the current step
    const h = this.hint();
    if (h) {
      const pulse = 0.5 + 0.5 * Math.sin(time / 280);
      D.lineStyle(3 * k, 0xffd54a, 0.55 + 0.45 * pulse);
      D.strokeCircle(h.x, h.y, (20 + 8 * pulse) * k);
    }
    renderPanel();
  }
}

window.__phaser = new Phaser.Game({
  type: Phaser.AUTO,
  parent: 'stage',
  backgroundColor: '#101412',
  scale: { mode: Phaser.Scale.NONE, width: innerWidth * dpr, height: innerHeight * dpr, zoom: 1 / dpr },
  input: { touch: { capture: true } },
  scene: [View],
});
window.addEventListener('resize', () => window.__phaser?.scale.resize(innerWidth * dpr, innerHeight * dpr));
game.setMode('game'); // standalone demo: the player owns the camera
