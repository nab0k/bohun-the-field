// Game logic only: no Phaser, no DOM, no timers. The view drives it with tick(dt) and listens to events.
// All geometry comes from the scenario, so the Codex contract (coordinates, projection) can replace the
// placeholder scenario without touching the rules.

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

function pathLength(pts) {
  let n = 0;
  for (let i = 1; i < pts.length; i++) n += dist(pts[i - 1], pts[i]);
  return n;
}

export function pointOnPath(pts, t) {
  const total = pathLength(pts);
  let target = Math.min(Math.max(t, 0), 1) * total;
  for (let i = 1; i < pts.length; i++) {
    const seg = dist(pts[i - 1], pts[i]);
    if (target <= seg || i === pts.length - 1) {
      const k = seg ? Math.min(target / seg, 1) : 0;
      return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * k, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * k };
    }
    target -= seg;
  }
  return pts[pts.length - 1];
}

export function createGame(scenario, { onEvent = () => {} } = {}) {
  const S = {};
  const cfg = scenario.rules;

  function emit(type, payload = {}) {
    S.log.unshift({ type, ...payload, at: S.time });
    if (S.log.length > 40) S.log.pop();
    onEvent(type, payload);
    if (type !== 'rejected') onEvent('stateChanged', { cause: type });
  }

  function reject(action, reason, payload = {}) {
    emit('rejected', { action, reason, ...payload });
    return { ok: false, reason };
  }

  function resetDemo() {
    S.time = 0;
    S.nodes = Object.fromEntries(scenario.nodes.map((n) => [n.id, { ...n, state: n.initial ?? 'open', progress: 0 }]));
    S.routes = Object.fromEntries(
      scenario.routes.map((r) => {
        const pts = [S.nodes[r.fromId], ...(r.path ?? []), S.nodes[r.toId]].map((p) => ({ x: p.x, y: p.y }));
        return [r.id, { ...r, pts, length: pathLength(pts), available: !r.lockedBy }];
      }),
    );
    S.bogun = { at: scenario.bogun.start, status: 'idle', routeId: null, from: null, to: null, t: 0 };
    S.transports = [];
    S.counter = 0;
    S.dossiers = new Set();
    S.log = [];
    S.nextId = 1;
    S.mode = S.mode ?? 'presentation';
    emit('reset');
  }

  function routeBetween(a, b) {
    return Object.values(S.routes).find((r) => (r.fromId === a && r.toId === b) || (r.fromId === b && r.toId === a));
  }

  // the game never touches the page: it only asks for a dossier, an HTML layer listens
  function requestDossier(dossierId, reason) {
    if (!dossierId || !scenario.dossiers[dossierId]) return;
    S.dossiers.add(dossierId);
    emit('dossierRequested', { dossierId, reason });
  }

  // ---- action 1: send Bohun ----
  function moveBohun(destinationId) {
    const city = S.nodes[destinationId];
    if (!city || city.kind !== 'city') return reject('send', 'unknown-city', { destinationId });
    if (S.bogun.status === 'moving') return reject('send', 'busy', { destinationId });
    if (S.bogun.at === destinationId) return reject('send', 'already-there', { destinationId });
    const route = routeBetween(S.bogun.at, destinationId);
    if (!route) return reject('send', 'no-route', { destinationId });
    if (!route.available) return reject('send', 'locked', { destinationId, routeId: route.id });
    Object.assign(S.bogun, { status: 'moving', routeId: route.id, from: S.bogun.at, to: destinationId, t: 0 });
    emit('bohunDeparted', { from: S.bogun.from, to: destinationId, routeId: route.id });
    return { ok: true };
  }

  // ---- action 2: launch a shipment ----
  function dispatchCargo(routeId) {
    const route = S.routes[routeId];
    if (!route) return reject('ship', 'unknown-route', { routeId });
    if (!route.available) return reject('ship', 'locked', { routeId });
    if (S.transports.some((t) => t.routeId === routeId)) return reject('ship', 'busy', { routeId });
    const t = { id: S.nextId++, routeId, t: 0, cargo: scenario.rules.cargo };
    S.transports.push(t);
    emit('cargoDispatched', { routeId, cargo: t.cargo });
    return { ok: true, id: t.id };
  }

  // ---- action 3: research a closed node ----
  function research(nodeId) {
    const node = S.nodes[nodeId];
    if (!node || node.kind !== 'site') return reject('research', 'unknown-node', { nodeId });
    if (node.state === 'researching') return reject('research', 'busy', { nodeId });
    if (node.state === 'open') return reject('research', 'already-done', { nodeId });
    node.state = 'researching';
    node.progress = 0;
    emit('researchStarted', { nodeId });
    return { ok: true };
  }

  function completeResearch(node) {
    node.state = 'open';
    node.progress = 1;
    emit('researchCompleted', { nodeId: node.id });
    for (const r of Object.values(S.routes)) {
      if (r.lockedBy === node.id && !r.available) {
        r.available = true;
        emit('routeUnlocked', { routeId: r.id });
      }
    }
    requestDossier(node.dossierId, 'research');
  }

  function tick(dt) {
    S.time += dt;
    const b = S.bogun;
    if (b.status === 'moving') {
      const r = S.routes[b.routeId];
      b.t = Math.min(1, b.t + (cfg.bogunSpeed * dt) / r.length);
      if (b.t >= 1) {
        b.at = b.to;
        b.status = 'arrived';
        b.routeId = null;
        emit('bohunArrived', { at: b.at });
        requestDossier(S.nodes[b.at].dossierId, 'arrival');
      }
    }
    for (const t of [...S.transports]) {
      const r = S.routes[t.routeId];
      t.t = Math.min(1, t.t + (cfg.transportSpeed * dt) / r.length);
      if (t.t >= 1) {
        S.transports.splice(S.transports.indexOf(t), 1);
        S.counter += cfg.yieldPerShipment;
        emit('cargoArrived', { routeId: t.routeId, counter: S.counter });
      }
    }
    for (const n of Object.values(S.nodes)) {
      if (n.state !== 'researching') continue;
      n.progress = Math.min(1, n.progress + dt / cfg.researchSeconds);
      if (n.progress >= 1) completeResearch(n);
    }
  }

  // camera ownership: presentation = the site drives the camera, game = the player does. One owner at a time.
  function setMode(mode) {
    if (mode !== 'presentation' && mode !== 'game') return reject('mode', 'unknown-mode', { mode });
    if (S.mode === mode) return { ok: true, unchanged: true };
    S.mode = mode;
    emit('modeSet', { mode });
    return { ok: true };
  }

  // what the player can do right now, with the reason when a command is unavailable
  function commands() {
    const list = [];
    for (const n of Object.values(S.nodes)) {
      if (n.kind === 'city') {
        let reason = null, code = null;
        const r = routeBetween(S.bogun.at, n.id);
        if (S.bogun.status === 'moving') { reason = 'Bohun is travelling'; code = 'moving'; }
        else if (S.bogun.at === n.id) { reason = 'Bohun is already here'; code = 'here'; }
        else if (!r) { reason = 'No route'; code = 'no-route'; }
        else if (!r.available) { reason = 'Route closed: research first'; code = 'locked'; }
        list.push({ action: 'send', target: n.id, label: `SEND BOHUN → ${n.label}`, enabled: !reason, reason, code });
      }
      if (n.kind === 'site') {
        const reason = n.state === 'researching' ? 'Research in progress' : n.state === 'open' ? 'Already researched' : null;
        const code = n.state === 'researching' ? 'researching' : n.state === 'open' ? 'done' : null;
        list.push({ action: 'research', target: n.id, label: `RESEARCH ${n.label}`, enabled: !reason, reason, code });
      }
    }
    for (const r of Object.values(S.routes)) {
      let reason = null, code = null;
      if (!r.available) { reason = 'Route closed: research first'; code = 'locked'; }
      else if (S.transports.some((t) => t.routeId === r.id)) { reason = 'Shipment already underway'; code = 'underway'; }
      list.push({ action: 'ship', target: r.id, label: `SHIP ${S.nodes[r.fromId].label} → ${S.nodes[r.toId].label}`, enabled: !reason, reason, code });
    }
    return list;
  }

  function run(action, target) {
    if (action === 'send') return moveBohun(target);
    if (action === 'ship') return dispatchCargo(target);
    if (action === 'research') return research(target);
    return { ok: false, reason: 'unknown-action' };
  }

  resetDemo();
  return { state: S, moveBohun, dispatchCargo, research, resetDemo, setMode, tick, commands, run };
}
