import Phaser from 'phaser';
import gsap from 'gsap';
import { entities, candidates, roads, emphasis, iso, GRID, dossiers } from './world.js';
import * as art from './art.js';

const hooks = () => window.__fieldHooks || {};

const DRAWERS = {
  hq: art.drawHQ,
  factory: art.drawFactory,
  hall: art.drawHall,
  workshop: art.drawWorkshop,
  market: art.drawMarket,
  warehouse: art.drawWarehouse,
  antenna: art.drawAntennaMast,
  signal: art.drawSignalMarker,
};

const TYPE_LABEL = {
  hq: 'BOHUN / HQ',
  factory: 'NODE / SUPPLIER',
  hall: 'NODE / MARKET',
  workshop: 'NODE / PARTNER',
  market: 'NODE / MARKET',
  signal: 'SIGNAL / CURRENT INTEREST',
};

const byId = Object.fromEntries(entities.map((e) => [e.id, e]));
const GOLD = '#c9a24a';

// grid-aligned L route between two things that have gx/gy
function lPath(a, b) {
  return [iso(a.gx, a.gy), iso(b.gx, a.gy), iso(b.gx, b.gy)];
}

function cumulative(pts) {
  const acc = [0];
  for (let i = 1; i < pts.length; i++) acc.push(acc[i - 1] + Phaser.Math.Distance.Between(pts[i - 1].x, pts[i - 1].y, pts[i].x, pts[i].y));
  return acc;
}

function pointAt(pts, t) {
  const acc = cumulative(pts);
  const target = acc[acc.length - 1] * Phaser.Math.Clamp(t, 0, 1);
  for (let i = 1; i < pts.length; i++) {
    if (target <= acc[i]) {
      const seg = acc[i] - acc[i - 1] || 1;
      const k = (target - acc[i - 1]) / seg;
      return { x: Phaser.Math.Linear(pts[i - 1].x, pts[i].x, k), y: Phaser.Math.Linear(pts[i - 1].y, pts[i].y, k) };
    }
  }
  return pts[pts.length - 1];
}

function partial(pts, t) {
  if (t >= 1) return pts;
  const acc = cumulative(pts);
  const target = acc[acc.length - 1] * t;
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (target >= acc[i]) out.push(pts[i]);
    else {
      out.push(pointAt(pts, t));
      break;
    }
  }
  return out;
}

function distSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l = dx * dx + dy * dy || 1;
  const t = Phaser.Math.Clamp(((px - x1) * dx + (py - y1) * dy) / l, 0, 1);
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

export class FieldScene extends Phaser.Scene {
  constructor() {
    super('field');
  }

  create() {
    this.dpr = 1 / this.scale.zoom;
    this.zf = window.innerWidth <= 820 ? 0.6 : 1;
    this.reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.view = { fx: 0, fy: 640, z: 0.6, xf: 0.66, yf: 0.5 };
    this.mode = 'presentation';
    this.emph = null;
    this.phase = 'idle';
    this.chosen = null;
    this.selected = null;
    this.nodes = {};
    this.cands = {};
    this.routes = [];
    this.units = [];
    this.labels = [];
    this.drag = { moved: false };

    this.cameras.main.setBackgroundColor('#101412');

    this.buildTerrain();
    this.buildNodes();
    this.buildAmbient();
    this.bindInput();

    this.routeG = this.add.graphics().setDepth(3);
    window.__fieldHooks?.ready?.(this);
  }

  // ---------- construction ----------

  buildTerrain() {
    const g = this.add.graphics().setDepth(0);
    art.drawTerrain(g);

    const rg = this.add.graphics().setDepth(1);
    roads.forEach(([a, b]) => art.drawRoad(rg, lPath(byId[a], byId[b])));

    const tg = this.add.graphics().setDepth(1.5);
    const trees = [];
    const segs = roads.flatMap(([a, b]) => {
      const A = byId[a], B = byId[b];
      return [[A.gx, A.gy, B.gx, A.gy], [B.gx, A.gy, B.gx, B.gy]];
    });
    for (let i = 0; i < 170; i++) {
      const gx = 1 + this.rand(i, 7) * (GRID - 2);
      const gy = 1 + this.rand(i, 13) * (GRID - 2);
      const nearEntity = entities.some((e) => Math.hypot(e.gx - gx, e.gy - gy) < 2.8) || candidates.some((c) => Math.hypot(c.gx - gx, c.gy - gy) < 2.4);
      const nearRoad = segs.some(([x1, y1, x2, y2]) => distSeg(gx, gy, x1, y1, x2, y2) < 1.5);
      if (nearEntity || nearRoad) continue;
      trees.push({ gx, gy, s: 0.8 + this.rand(i, 29) * 0.6 });
    }
    trees.sort((a, b) => a.gx + a.gy - (b.gx + b.gy));
    trees.forEach((t) => {
      const p = iso(t.gx, t.gy);
      art.drawTree(tg, p.x, p.y, t.s);
    });

    const regionStyle = { fontFamily: '"IBM Plex Mono", monospace', fontSize: '64px', color: '#f1efe8', resolution: this.dpr };
    this.add.text(0, 250, 'E   U   R   O   P   E', regionStyle).setOrigin(0.5).setAlpha(0.13).setDepth(2);
    this.add.text(0, 1320, 'U   K   R   A   I   N   E', regionStyle).setOrigin(0.5).setAlpha(0.13).setDepth(2);
  }

  rand(a, b) {
    let h = (a * 374761393 + b * 668265263) ^ (a * b * 1442695041);
    h = (h ^ (h >>> 13)) * 1274126177;
    return (((h ^ (h >>> 16)) >>> 0) % 100000) / 100000;
  }

  labelStyle() {
    return {
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: '13px',
      color: '#f1efe8',
      backgroundColor: 'rgba(14,18,16,0.82)',
      padding: { x: 7, y: 4 },
      resolution: this.dpr,
    };
  }

  makeLabel(text, x, y) {
    const t = this.add.text(x, y, text, this.labelStyle()).setOrigin(0.5, 0).setDepth(100000);
    this.labels.push(t);
    return t;
  }

  buildNodes() {
    entities.forEach((e) => {
      const { x, y } = iso(e.gx, e.gy);
      const c = this.add.container(x, y).setDepth(y);
      const ring = this.add.graphics();
      ring.lineStyle(3, art.COLOR.gold, 1);
      ring.strokeEllipse(0, 0, 176, 88);
      ring.setAlpha(0);
      const g = this.add.graphics();
      DRAWERS[e.type](g);
      c.add([ring, g]);

      const node = { e, c, ring, g, label: null, zone: null };
      this.nodes[e.id] = node;

      if (e.label) {
        node.label = this.makeLabel(e.label, x, y + (e.type === 'signal' ? 22 : 44));
      }
      if (e.content) {
        const zone = this.add.zone(x, y - 34, 124, 110).setInteractive({ useHandCursor: true }).setDepth(99999);
        zone.on('pointerover', () => this.hover(node, true));
        zone.on('pointerout', () => this.hover(node, false));
        zone.on('pointerup', () => this.entityClick(node));
        node.zone = zone;
      }
    });
  }

  hover(node, on) {
    node.label?.setColor(on ? GOLD : '#f1efe8');
    if (this.reduce) return;
    gsap.to(node.c, { scale: on ? 1.045 : 1, duration: 0.18, overwrite: 'auto' });
  }

  buildAmbient() {
    const reduce = this.reduce;

    // antenna dish
    const a = iso(byId.antenna.gx, byId.antenna.gy);
    const dish = this.add.graphics({ x: a.x, y: a.y - 92 }).setDepth(a.y + 3);
    dish.lineStyle(3, 0xc9ccc8, 1);
    dish.strokePoints([{ x: -13, y: 0 }, { x: 13, y: 0 }], false, false);
    dish.fillStyle(0xc9ccc8, 1);
    dish.fillCircle(0, 0, 3);
    if (!reduce) this.tweens.add({ targets: dish, angle: 360, duration: 5200, repeat: -1 });

    // factory smoke
    const f = iso(byId.supplier.gx, byId.supplier.gy);
    if (!reduce) {
      for (let i = 0; i < 4; i++) {
        const puff = this.add.circle(f.x + 38, f.y - 84, 5, 0xd3d6d2, 0.5).setDepth(f.y + 4);
        this.tweens.add({ targets: puff, y: puff.y - 44, x: puff.x + 14, alpha: 0, scale: 2.4, duration: 2800, repeat: -1, delay: i * 700 });
      }
    }

    // signal pulse + bob
    const s = iso(byId.propulsion.gx, byId.propulsion.gy);
    this.signalRing = this.add.ellipse(s.x, s.y + 8, 50, 25).setStrokeStyle(2, art.COLOR.gold, 0.9).setDepth(s.y - 1);
    this.signalRing.setFillStyle(0, 0);
    if (!reduce) {
      this.tweens.add({ targets: this.signalRing, scale: 3.2, alpha: 0, duration: 2000, repeat: -1, ease: 'Sine.easeOut' });
      this.tweens.add({ targets: this.nodes.propulsion.g, y: -7, duration: 1100, yoyo: true, repeat: -1, ease: 'Sine.easeInOut' });
    }

    // trucks on the roads
    const truckRoutes = [['supplier', 'bohun', 70], ['buyer', 'bohun', 60], ['bohun', 'ukraine', 80]];
    truckRoutes.forEach(([from, to, speed], i) => {
      const pts = lPath(byId[from], byId[to]);
      this.spawnUnit('truck', pts, reduce ? 0 : speed, 'pingpong', { t: 0.25 + i * 0.2 });
    });

    // scout: the discoverable way into Game Mode
    const b = byId.bohun;
    const patrol = [iso(b.gx + 1.8, b.gy + 1.8), iso(b.gx + 1.8, b.gy + 3.8), iso(b.gx + 3.8, b.gy + 3.8), iso(b.gx + 3.8, b.gy + 1.8), iso(b.gx + 1.8, b.gy + 1.8)];
    this.scout = this.spawnUnit('person', patrol, reduce ? 0 : 38, 'loop', { zone: true });
    this.scoutRing = this.add.ellipse(0, 0, 34, 17).setStrokeStyle(1.5, art.COLOR.gold, 0.7).setFillStyle(0, 0).setDepth(5);
    if (!reduce) this.tweens.add({ targets: this.scoutRing, scale: 1.7, alpha: 0.1, duration: 1500, repeat: -1 });
    this.scoutTip = this.makeLabel('FIELD CONTROL AVAILABLE', 0, 0).setVisible(false).setOrigin(0.5, 1);
    this.scout.zone.on('pointerover', () => this.mode === 'presentation' && this.scoutTip.setVisible(true));
    this.scout.zone.on('pointerout', () => this.scoutTip.setVisible(false));
    this.scout.zone.on('pointerup', () => {
      if (this.mode === 'presentation' && !this.drag.moved) hooks().onScout?.();
    });
  }

  spawnUnit(kind, pts, speed, mode, opts = {}) {
    const g = this.add.graphics();
    art.drawUnit(g, kind);
    const obj = this.add.container(pts[0].x, pts[0].y, [g]);
    const len = cumulative(pts).slice(-1)[0] || 1;
    const u = { obj, pts, len, t: opts.t || 0, speed, dir: 1, mode, onDone: opts.onDone, game: !!opts.game, done: false };
    if (opts.zone) {
      u.zone = this.add.zone(0, 0, 40, 48).setInteractive({ useHandCursor: true }).setDepth(99999);
    }
    const p = pointAt(pts, u.t);
    obj.setPosition(p.x, p.y).setDepth(p.y + 2);
    this.units.push(u);
    return u;
  }

  // ---------- input ----------

  bindInput() {
    this.input.on('pointerdown', (p) => {
      this.drag = { x: p.x, y: p.y, moved: false };
    });
    this.input.on('pointermove', (p) => {
      if (this.mode !== 'game' || !p.isDown) return;
      if (Math.abs(p.x - this.drag.x) + Math.abs(p.y - this.drag.y) > 10 * this.dpr) this.drag.moved = true;
      gsap.killTweensOf(this.view);
      const zt = this.view.z * this.dpr;
      this.view.fx = Phaser.Math.Clamp(this.view.fx - (p.x - p.prevPosition.x) / zt, -1000, 1000);
      this.view.fy = Phaser.Math.Clamp(this.view.fy - (p.y - p.prevPosition.y) / zt, 150, 1450);
    });
  }

  entityClick(node) {
    if (this.drag.moved) return;
    if (this.mode === 'game') this.select(node.e.id);
    else hooks().onDossier?.(node.e.content);
  }

  // ---------- camera ----------

  applyView() {
    const cam = this.cameras.main;
    const v = this.view;
    const zt = v.z * this.dpr;
    cam.setZoom(zt);
    cam.centerOn(v.fx - ((v.xf - 0.5) * cam.width) / zt, v.fy - ((v.yf - 0.5) * cam.height) / zt);
  }

  goTo(target, dur = 0.9) {
    gsap.to(this.view, { ...target, duration: this.reduce ? 0 : dur, ease: 'power2.out', overwrite: true });
  }

  zoomBy(delta) {
    gsap.killTweensOf(this.view);
    this.view.z = Phaser.Math.Clamp(this.view.z * Math.exp(-delta * 0.0012), 0.3, 1.6);
  }

  // ---------- presentation states ----------

  setEmphasis(mode) {
    const next = this.emph === mode ? null : mode;
    this.emph = next;
    this.routes = this.routes.filter((r) => r.game);
    const cfg = next ? emphasis[next] : null;
    Object.values(this.nodes).forEach((n) => {
      const lit = cfg && cfg.ids.includes(n.e.id);
      const keep = !cfg || lit || n.e.id === 'bohun';
      gsap.to(n.c, { alpha: keep ? 1 : 0.5, duration: this.reduce ? 0 : 0.5 });
      if (n.label) gsap.to(n.label, { alpha: keep ? 1 : 0.5, duration: this.reduce ? 0 : 0.5 });
      gsap.to(n.ring, { alpha: lit ? 0.9 : 0, duration: this.reduce ? 0 : 0.5 });
    });
    if (cfg) {
      cfg.routes.forEach(([a, b], i) => this.addRoute(lPath(byId[a], byId[b]), false, i * 0.25));
      hooks().onCaption?.(cfg.caption);
    } else {
      hooks().onCaption?.(null);
    }
    return next;
  }

  focusNode(id) {
    const n = this.nodes[id];
    if (!n || this.reduce) return;
    gsap.fromTo(n.c, { scale: 1 }, { scale: 1.12, duration: 0.25, yoyo: true, repeat: 1, overwrite: 'auto' });
    gsap.fromTo(n.ring, { alpha: 0 }, { alpha: 1, duration: 0.25, yoyo: true, repeat: 1 });
  }

  addRoute(pts, game, delay = 0) {
    const r = { pts, progress: 0, phase: Math.random(), game };
    this.routes.push(r);
    gsap.to(r, { progress: 1, duration: this.reduce ? 0 : 1.1, delay, ease: 'power1.inOut' });
    return r;
  }

  // ---------- game mode ----------

  enterGame() {
    this.mode = 'game';
    this.scoutTip.setVisible(false);
    this.goTo({ fx: 0, fy: 700, z: 0.75 * this.zf, xf: 0.5, yf: 0.5 }, 1);
    hooks().onMode?.('game');
  }

  exitGame() {
    this.reset();
    this.mode = 'presentation';
    hooks().onMode?.('presentation');
  }

  describe(id) {
    const e = byId[id];
    if (!e) return null;
    const d = dossiers[e.content];
    const actions = [{ id: 'file:' + e.content, label: 'OPEN FILE' }];
    let desc = d ? d.body[0] : '';
    if (id === 'propulsion') {
      desc = 'Category-level interest. Research the field to reveal relevant capabilities.';
      if (this.phase === 'idle') actions.unshift({ id: 'research', label: 'RESEARCH' });
    }
    return { id, title: e.label, sub: TYPE_LABEL[e.type], desc, actions };
  }

  select(id) {
    this.selected = id;
    Object.values(this.nodes).forEach((n) => n.ring.setAlpha(n.e.id === id ? 0.95 : 0));
    Object.entries(this.cands).forEach(([cid, c]) => c.ring.setAlpha(cid === id ? 0.95 : 0));

    let payload = this.describe(id);
    if (!payload && this.cands[id]) {
      const c = this.cands[id];
      const actions = [];
      let desc = 'Organisation undisclosed. Fit to be qualified.';
      if (this.phase === 'revealed') actions.push({ id: 'qualify:' + id, label: 'QUALIFY' });
      if (this.phase === 'qualified' && this.chosen === id) {
        desc = 'Assessed as relevant to the PROPULSION interest. Ready for a first conversation.';
        actions.push({ id: 'engage:' + id, label: 'ESTABLISH CONTACT' });
      }
      if (this.phase === 'qualified' && this.chosen !== id) desc = 'Assessed as not a fit for this interest.';
      if (this.phase === 'routed') desc = this.chosen === id ? 'Route established. A conversation can begin.' : 'Not pursued.';
      payload = { id, title: c.data.label, sub: 'CANDIDATE / UNVERIFIED', desc, actions };
    }
    hooks().onSelect?.(payload);
  }

  clearSelection() {
    this.selected = null;
    Object.values(this.nodes).forEach((n) => n.ring.setAlpha(0));
    Object.values(this.cands).forEach((c) => c.ring.setAlpha(0));
    hooks().onSelect?.(null);
  }

  action(id) {
    const [kind, arg] = id.split(':');
    if (kind === 'file') hooks().onDossier?.(arg);
    if (kind === 'research') this.research();
    if (kind === 'qualify') this.qualify(arg);
    if (kind === 'engage') this.engage(arg);
  }

  setPhase(phase) {
    this.phase = phase;
    hooks().onPhase?.(phase);
  }

  research() {
    if (this.phase !== 'idle') return;
    this.setPhase('researching');
    const pts = lPath(byId.bohun, byId.propulsion);
    this.goTo({ fx: 160, fy: 620, z: 0.85 * this.zf }, 1);
    this.spawnUnit('person', pts, this.reduce ? 9999 : 260, 'once', { game: true, onDone: () => this.reveal() });
  }

  reveal() {
    candidates.forEach((cd, i) => {
      const { x, y } = iso(cd.gx, cd.gy);
      const c = this.add.container(x, y).setDepth(y);
      const ring = this.add.graphics();
      ring.lineStyle(3, art.COLOR.gold, 1);
      ring.strokeEllipse(0, 0, 120, 60);
      ring.setAlpha(0);
      const g = this.add.graphics();
      art.drawCandidate(g);
      const q = this.add
        .text(0, -48, '?', { fontFamily: '"PT Sans Narrow", sans-serif', fontSize: '28px', fontStyle: 'bold', color: GOLD, resolution: this.dpr })
        .setOrigin(0.5);
      c.add([ring, g, q]);
      const label = this.makeLabel(cd.label, x, y + 30);
      const zone = this.add.zone(x, y - 26, 90, 80).setInteractive({ useHandCursor: true }).setDepth(99999);
      zone.on('pointerup', () => !this.drag.moved && this.mode === 'game' && this.select(cd.id));
      zone.on('pointerover', () => label.setColor(GOLD));
      zone.on('pointerout', () => label.setColor('#f1efe8'));
      this.cands[cd.id] = { data: cd, c, ring, q, label, zone };
      c.setScale(0).setAlpha(0);
      label.setAlpha(0);
      const delay = this.reduce ? 0 : 0.2 + i * 0.22;
      gsap.to(c, { scale: 1, alpha: 1, duration: this.reduce ? 0 : 0.55, delay, ease: 'back.out(1.8)' });
      gsap.to(label, { alpha: 1, duration: this.reduce ? 0 : 0.3, delay: delay + 0.2 });
    });
    this.goTo({ fx: 330, fy: 560, z: 0.8 * this.zf }, 1.1);
    this.setPhase('revealed');
    hooks().onCaption?.('3 POTENTIAL CAPABILITIES IDENTIFIED');
  }

  qualify(id) {
    if (this.phase !== 'revealed' || !this.cands[id]) return;
    this.chosen = id;
    Object.entries(this.cands).forEach(([cid, c]) => {
      const yes = cid === id;
      c.q.setText(yes ? '✓' : '×');
      c.label.setText(yes ? c.data.label + ' · RELEVANT' : c.data.label + ' · NOT A FIT');
      gsap.to(c.c, { alpha: yes ? 1 : 0.35, duration: this.reduce ? 0 : 0.4 });
      gsap.to(c.label, { alpha: yes ? 1 : 0.4, duration: this.reduce ? 0 : 0.4 });
    });
    this.setPhase('qualified');
    this.select(id);
  }

  engage(id) {
    if (this.phase !== 'qualified' || this.chosen !== id) return;
    const cd = this.cands[id].data;
    const pts = lPath(byId.bohun, cd);
    this.setPhase('engaging');
    this.addRoute(pts, true);
    this.spawnUnit('truck', pts, this.reduce ? 9999 : 260, 'once', {
      game: true,
      onDone: () => {
        this.setPhase('routed');
        hooks().onCaption?.('ROUTE ESTABLISHED');
        this.select(id);
      },
    });
  }

  reset() {
    Object.values(this.cands).forEach((c) => {
      c.c.destroy();
      c.label.destroy();
      c.zone.destroy();
      this.labels = this.labels.filter((l) => l !== c.label);
    });
    this.cands = {};
    this.routes = this.routes.filter((r) => !r.game);
    this.units = this.units.filter((u) => {
      if (u.game) u.obj.destroy();
      return !u.game;
    });
    this.chosen = null;
    this.setPhase('idle');
    this.clearSelection();
  }

  // ---------- frame loop ----------

  update(time, delta) {
    this.applyView();
    const dt = delta / 1000;

    for (const u of this.units) {
      if (u.done) continue;
      if (u.speed > 0) {
        u.t += ((u.speed * dt) / u.len) * u.dir;
        if (u.t >= 1) {
          if (u.mode === 'loop') u.t = 0;
          else if (u.mode === 'pingpong') {
            u.t = 1;
            u.dir = -1;
          } else {
            u.t = 1;
            u.done = true;
            u.onDone?.();
          }
        } else if (u.t <= 0 && u.mode === 'pingpong') {
          u.t = 0;
          u.dir = 1;
        }
      }
      const p = pointAt(u.pts, u.t);
      u.obj.setPosition(p.x, p.y).setDepth(p.y + 2);
      if (u.zone) u.zone.setPosition(p.x, p.y - 14);
    }

    if (this.scout) {
      const p = this.scout.obj;
      this.scoutRing.setPosition(p.x, p.y + 1);
      this.scoutTip.setPosition(p.x, p.y - 28);
    }

    const g = this.routeG;
    g.clear();
    for (const r of this.routes) {
      const part = partial(r.pts, r.progress);
      if (part.length < 2) continue;
      g.lineStyle(15, art.COLOR.gold, 0.16);
      art.polyline(g, part);
      g.lineStyle(5, art.COLOR.gold, 0.95);
      art.polyline(g, part);
      if (r.progress >= 1 && !this.reduce) {
        const p = pointAt(r.pts, (time * 0.00032 + r.phase) % 1);
        g.fillStyle(0xffffff, 0.95);
        g.fillCircle(p.x, p.y, 4.5);
      }
    }

    const s = Phaser.Math.Clamp(0.95 / this.view.z, 0.9, 1.7);
    for (const l of this.labels) l.setScale(s);
  }
}
