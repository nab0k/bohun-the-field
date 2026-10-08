import Phaser from 'phaser';
import gsap from 'gsap';
import { FieldScene } from './scene.js';
import { stops, dossiers } from './world.js';

const $ = (s) => document.querySelector(s);
const body = document.body;
const narrow = () => window.innerWidth <= 820;
const params = new URLSearchParams(location.search);
const dpr = Number(params.get('dpr')) || Math.min(window.devicePixelRatio || 1, 2);

let scene = null;
let game = null;
let lastFocus = null;

// ---------- hooks: the scene talks to the page only through these ----------

const OBJECTIVES = {
  idle: 'OBJECTIVE: select the PROPULSION signal and RESEARCH the field.',
  researching: 'Field agent dispatched…',
  revealed: 'Three potential capabilities found. Inspect each one and QUALIFY the best fit.',
  qualified: 'Fit identified. Select it and ESTABLISH CONTACT.',
  engaging: 'Establishing route…',
  routed: 'ROUTE ESTABLISHED. This is the whole method: research, qualify, connect.',
};

function showCaption(text) {
  const el = $('#caption');
  if (!text) return el.classList.remove('on');
  el.textContent = text;
  el.classList.add('on');
}

function openDossier(key) {
  const d = dossiers[key];
  if (!d) return;
  lastFocus = document.activeElement;
  $('#d-file').textContent = d.file;
  $('#d-title').textContent = d.title;
  const bodyEl = $('#d-body');
  bodyEl.replaceChildren();
  if (d.list) {
    const ul = document.createElement('ul');
    d.body.forEach((t) => {
      const li = document.createElement('li');
      li.textContent = t;
      ul.append(li);
    });
    bodyEl.append(ul);
  } else {
    d.body.forEach((t) => {
      const p = document.createElement('p');
      p.textContent = t;
      bodyEl.append(p);
    });
  }
  $('#d-status').textContent = d.status;
  $('#d-cta').textContent = d.cta;
  const el = $('#dossier');
  el.classList.add('open');
  el.setAttribute('aria-hidden', 'false');
  $('#d-close').focus({ preventScroll: true });
}

function closeDossier() {
  const el = $('#dossier');
  if (!el.classList.contains('open')) return false;
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
  lastFocus?.focus?.({ preventScroll: true });
  return true;
}

function renderInspector(p) {
  const box = $('#inspect');
  if (!p) {
    box.hidden = true;
    return;
  }
  $('#i-sub').textContent = p.sub;
  $('#i-title').textContent = p.title;
  $('#i-desc').textContent = p.desc;
  const wrap = $('#i-actions');
  wrap.replaceChildren();
  p.actions.forEach((a) => {
    const b = document.createElement('button');
    b.textContent = a.label;
    b.addEventListener('click', () => scene.action(a.id));
    wrap.append(b);
  });
  box.hidden = false;
}

function showToast(on) {
  const t = $('#toast');
  t.classList.toggle('on', on);
  t.setAttribute('aria-hidden', String(!on));
}

function setMode(mode) {
  const game_ = mode === 'game';
  body.classList.toggle('game', game_);
  $('#hud').setAttribute('aria-hidden', String(!game_));
  showToast(false);
  closeDossier();
  showCaption(null);
  if (game_) {
    window.scrollTo(0, 0);
    $('#objective').textContent = OBJECTIVES[scene.phase];
    renderInspector(null);
    window.addEventListener('wheel', onGameWheel, { passive: false });
  } else {
    window.removeEventListener('wheel', onGameWheel);
    updateFromScroll();
    scene.view.xf = narrow() ? 0.5 : scene.view.xf;
  }
}

function onGameWheel(e) {
  e.preventDefault();
  scene?.zoomBy(e.deltaY);
}

window.__fieldHooks = {
  ready: (s) => {
    scene = s;
    window.__field = { scene: s, game };
    updateFromScroll(true);
    applyUrlParams();
  },
  onScout: () => showToast(true),
  onDossier: openDossier,
  onCaption: showCaption,
  onMode: setMode,
  onSelect: renderInspector,
  onPhase: (phase) => {
    $('#objective').textContent = OBJECTIVES[phase] || '';
    if (phase !== 'revealed' && phase !== 'qualified' && phase !== 'routed') showCaption(null);
  },
};

// ---------- scroll → camera ----------

function stopFor(i) {
  const s = stops[i];
  return narrow() ? { ...s, xf: 0.5, yf: 0.27, z: s.z * 0.52 } : s;
}

function updateFromScroll(instant) {
  if (!scene || scene.mode !== 'presentation') return;
  const panels = [...document.querySelectorAll('.panel')];
  const centers = panels.map((p) => p.offsetTop + p.offsetHeight / 2);
  const sc = window.scrollY + window.innerHeight / 2;
  let i = 0;
  while (i < centers.length - 1 && sc > centers[i + 1]) i++;
  const j = Math.min(i + 1, centers.length - 1);
  const t = j === i ? 0 : Phaser.Math.Clamp((sc - centers[i]) / (centers[j] - centers[i]), 0, 1);
  const a = stopFor(+panels[i].dataset.stop);
  const b = stopFor(+panels[j].dataset.stop);
  const e = t * t * (3 - 2 * t);
  const target = {};
  for (const k of ['fx', 'fy', 'z', 'xf', 'yf']) target[k] = a[k] + (b[k] - a[k]) * e;
  if (instant) Object.assign(scene.view, target);
  else scene.goTo(target, 0.7);

  const active = panels[Math.round(i + t)];
  if (active?.id === 's-interest' && scene.lastFocused !== 'propulsion') {
    scene.lastFocused = 'propulsion';
    scene.focusNode('propulsion');
  } else if (active?.id === 's-ukraine' && scene.lastFocused !== 'ukraine') {
    scene.lastFocused = 'ukraine';
    scene.focusNode('ukraine');
  } else if (active?.id === 's-hero' || active?.id === 's-contact') {
    scene.lastFocused = null;
  }
}

// ---------- UI wiring ----------

document.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openDossier(b.dataset.open)));
document.querySelectorAll('.choice').forEach((b) =>
  b.addEventListener('click', () => {
    if (!scene) return;
    const next = scene.setEmphasis(b.dataset.emph);
    document.querySelectorAll('.choice').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.emph === next)));
  }),
);

$('#d-close').addEventListener('click', closeDossier);
$('#d-cta').addEventListener('click', () => {
  closeDossier();
  if (scene?.mode === 'presentation') $('#s-contact').scrollIntoView({ behavior: 'smooth', block: 'center' });
});
$('#contact').addEventListener('submit', (e) => {
  e.preventDefault();
  $('#form-note').textContent = 'Prototype: the form is not wired to anything yet. Nothing was sent.';
});
$('#t-yes').addEventListener('click', () => scene?.enterGame());
$('#t-no').addEventListener('click', () => showToast(false));
$('#exit').addEventListener('click', () => scene?.exitGame());

window.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (closeDossier()) return;
  if ($('#toast').classList.contains('on')) return showToast(false);
  if (scene?.mode === 'game') scene.exitGame();
});

window.addEventListener('scroll', () => updateFromScroll(), { passive: true });
window.addEventListener('resize', () => {
  game?.scale.resize(window.innerWidth * dpr, window.innerHeight * dpr);
  updateFromScroll();
});

// test/demo helpers: ?mode=game  ?emph=need|have
function applyUrlParams() {
  const q = new URLSearchParams(location.search);
  if (q.get('emph')) {
    scene.setEmphasis(q.get('emph'));
    document.querySelectorAll('.choice').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.emph === scene.emph)));
  }
  if (q.get('mode') === 'game') scene.enterGame();
}

// ---------- boot ----------

try {
  game = new Phaser.Game({
    type: params.get('renderer') === 'canvas' ? Phaser.CANVAS : Phaser.AUTO,
    parent: 'game',
    backgroundColor: '#101412',
    scale: { mode: Phaser.Scale.NONE, width: window.innerWidth * dpr, height: window.innerHeight * dpr, zoom: 1 / dpr },
    input: { mouse: { preventDefaultWheel: false }, touch: { capture: false } },
    render: { antialias: true, roundPixels: false },
    scene: [FieldScene],
  });
  if (window.__field) window.__field.game = game;
  gsap.ticker.lagSmoothing(0);
} catch (err) {
  console.error('World failed to start', err);
  body.classList.add('no-world');
}
