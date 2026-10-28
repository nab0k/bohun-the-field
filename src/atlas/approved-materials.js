import { loadApprovedSnapshot, materialKey, sourceURL, validateApprovedSnapshot } from './approved-snapshot.js';

const SOURCE = 'exp009-approved-materials', LAYER = 'exp009-approved-points';
const emptyMap = () => ({ type: 'FeatureCollection', features: [] });

// Each host belongs to the caller. Only our child roots and our map layer are changed.
export function mountApprovedMaterials({ feedHost, readerHost, map = null,
  documentImpl = globalThis.document, windowImpl = globalThis.window,
  loader = loadApprovedSnapshot, reducedMotion = false,
  timers = { setTimeout: (...args) => globalThis.setTimeout(...args), clearTimeout: id => globalThis.clearTimeout(id) },
} = {}) {
  const doc = documentImpl, win = windowImpl;
  const make = (tag, className, value) => {
    const node = doc.createElement(tag); node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const feed = make('section', 'approved-feed'), reader = make('article', 'approved-reader');
  feedHost.append(feed); readerHost.append(reader); readerHost.hidden = true; feedHost.hidden = false;
  let current = null, selected = null, sourceUrl = '', synthetic = false, status = 'unconfigured';
  let camera = null, pending = null, intent = null, generation = 0, disposed = false, request = null;
  const titleButtons = new Map();

  const cancelHover = () => { if (pending !== null) timers.clearTimeout(pending); pending = null; intent = null; };
  const manualMove = event => {
    if (event?.originalEvent || event?.type === 'dragstart') { cancelHover(); camera?.stop?.(); }
  };
  const pointFor = item => current?.map.features.find(f => materialKey(f.properties) === materialKey(item));
  function padding() {
    const width = win.innerWidth || 1200;
    if (width <= 760) return { top: 86, bottom: Math.min(feedHost.getBoundingClientRect().height + 24, (win.innerHeight || 800) * .36), left: 18, right: 18 };
    return { top: 90, bottom: 24, right: Math.min(feedHost.getBoundingClientRect().width + 32, width * .34),
      left: readerHost.hidden ? 24 : Math.min(readerHost.getBoundingClientRect().width + 32, width * .52) };
  }
  function focusPoint(item) {
    const point = pointFor(item);
    if (!point || !camera) return;
    if (pending !== null) timers.clearTimeout(pending);
    pending = null; intent = materialKey(item);
    camera.easeTo({ center: [...point.geometry.coordinates], zoom: 3.5, pitch: 0, bearing: 0,
      duration: reducedMotion ? 0 : 700, padding: padding() });
  }
  function schedulePoint(item) {
    const key = materialKey(item);
    if (!pointFor(item) || intent === key) return;
    cancelHover(); intent = key;
    pending = timers.setTimeout(() => { pending = null; focusPoint(item); }, 150);
  }
  function closeReader(restoreFocus = true) {
    const previous = selected; selected = null;
    readerHost.hidden = true; reader.replaceChildren();
    titleButtons.forEach(button => button.setAttribute('aria-expanded', 'false'));
    if (restoreFocus && previous) {
      titleButtons.get(previous)?.focus();
      // Restoring keyboard focus after reading is not a new map-navigation intent.
      cancelHover();
    }
  }
  function mapButton(item) {
    const button = make('button', 'approved-map-button', 'Show on map'); button.type = 'button';
    button.addEventListener('click', () => {
      // A phone needs map space; explicit map navigation closes the article first.
      if ((win.innerWidth || 1200) <= 760 && !readerHost.hidden) closeReader(false);
      focusPoint(item);
    });
    return button;
  }
  function openItem(item, focus = true) {
    cancelHover(); selected = materialKey(item); reader.replaceChildren(); readerHost.hidden = false;
    titleButtons.forEach((button, key) => button.setAttribute('aria-expanded', String(key === selected)));
    const close = make('button', 'approved-close', 'Close article'); close.type = 'button';
    close.addEventListener('click', () => closeReader()); reader.append(close);
    if (synthetic) reader.append(make('p', 'approved-synthetic', 'SYNTHETIC PREVIEW · Not real reporting'));
    const heading = make('h2', 'approved-reader-title', item.headline); heading.tabIndex = -1;
    reader.append(heading, make('p', 'approved-meta', `Version ${item.version} · ${item.origin === 'human' ? 'Human-edited' : 'System-prepared'}`),
      make('div', 'approved-body', item.body), make('h3', 'approved-sources-title', 'Sources'));
    const sources = make('ol', 'approved-sources');
    for (const value of item.source_urls) {
      const li = make('li', 'approved-source'), link = make('a', 'approved-source-link', value);
      link.href = sourceURL(value); link.target = '_blank'; link.rel = 'noopener noreferrer'; li.append(link); sources.append(li);
    }
    reader.append(sources, make('p', 'approved-version', `Version ${item.version} · ${item.content_hash.slice(0, 12)}`));
    if (pointFor(item)) reader.append(mapButton(item));
    if (focus) heading.focus();
  }
  reader.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeReader(); }
  });
  function drawFeed() {
    feed.replaceChildren(); titleButtons.clear();
    const head = make('div', 'approved-feed-head'); head.append(make('h2', 'approved-heading', 'Analysis'));
    if (sourceUrl) {
      const reload = make('button', 'approved-reload', 'Refresh'); reload.type = 'button';
      reload.disabled = status === 'loading'; reload.addEventListener('click', () => load(sourceUrl)); head.append(reload);
    }
    feed.append(head);
    if (synthetic) feed.append(make('p', 'approved-synthetic', 'SYNTHETIC PREVIEW · Not real reporting'));
    const messages = { loading: 'Loading published analysis…', unconfigured: 'No published analysis is connected yet.',
      unavailable: 'Published analysis is temporarily unavailable. Source news is unaffected.' };
    if (!current?.items.length) {
      const note = make('p', 'approved-status', messages[status] || 'No published analysis is available.');
      note.setAttribute('role', 'status'); feed.append(note); return;
    }
    feed.append(make('p', 'approved-intro', 'Open a material to read its evidence and interpretation.'));
    for (const item of current.items) {
      const card = make('article', 'approved-card'), key = materialKey(item);
      const button = make('button', 'approved-title', item.headline); button.type = 'button';
      button.setAttribute('aria-expanded', String(selected === key)); button.setAttribute('aria-controls', readerHost.id);
      button.addEventListener('click', () => openItem(item)); titleButtons.set(key, button);
      card.append(button, make('p', 'approved-card-meta', `Version ${item.version} · ${item.origin === 'human' ? 'Human-edited' : 'System-prepared'}`));
      // The excerpt is literal prose, never a generated new claim, date, or category.
      const excerpt = Array.from(item.body); card.append(make('p', 'approved-excerpt', excerpt.slice(0, 180).join('') + (excerpt.length > 180 ? '…' : '')));
      if (pointFor(item)) {
        card.append(mapButton(item));
        card.addEventListener('mouseenter', () => schedulePoint(item)); card.addEventListener('mouseleave', cancelHover);
        card.addEventListener('focusin', () => schedulePoint(item));
        card.addEventListener('focusout', event => { if (!card.contains(event.relatedTarget)) cancelHover(); });
      }
      feed.append(card);
    }
    if (current.map.features.length) feed.append(make('p', 'approved-map-note', 'Points show country-level context, not facility addresses.'));
  }
  function syncMap() {
    if (!camera) return;
    const data = current?.map || emptyMap();
    if (camera.getSource(SOURCE)) camera.getSource(SOURCE).setData(data);
    else camera.addSource(SOURCE, { type: 'geojson', data });
    if (!camera.getLayer(LAYER)) camera.addLayer({ id: LAYER, type: 'circle', source: SOURCE,
      paint: { 'circle-radius': 6, 'circle-color': '#d6b25b', 'circle-stroke-width': 2, 'circle-stroke-color': '#182a24' } });
  }
  const clickPoint = event => {
    const properties = event.features?.[0]?.properties;
    const item = current?.items.find(item => properties && materialKey(properties) === materialKey(item));
    if (item) openItem(item);
  };
  function detachMap() {
    if (!camera) return;
    for (const type of ['dragstart', 'zoomstart', 'movestart']) camera.off(type, manualMove);
    camera.off('click', LAYER, clickPoint);
    if (camera.getLayer(LAYER)) camera.removeLayer(LAYER);
    if (camera.getSource(SOURCE)) camera.removeSource(SOURCE);
    camera = null;
  }
  function setMap(value) {
    if (disposed) return;
    cancelHover(); detachMap(); camera = value;
    if (!camera) return;
    syncMap(); camera.on('click', LAYER, clickPoint);
    for (const type of ['dragstart', 'zoomstart', 'movestart']) camera.on(type, manualMove);
  }
  function unavailable(nextStatus = 'unavailable') {
    cancelHover(); closeReader(false); current = null; status = nextStatus; syncMap(); drawFeed();
  }
  function setSnapshot(value, { isSynthetic = false } = {}) {
    if (disposed) return false;
    synthetic = isSynthetic;
    try {
      const snapshot = validateApprovedSnapshot(value);
      if (current?.snapshot_hash === snapshot.snapshot_hash && JSON.stringify(current) !== JSON.stringify(snapshot)) throw Error('SNAPSHOT_ID_CONFLICT');
      const preserved = snapshot.items.find(item => materialKey(item) === selected);
      cancelHover(); current = snapshot; status = 'ready'; synthetic = isSynthetic;
      if (!preserved) closeReader(false);
      syncMap(); drawFeed(); if (preserved) openItem(preserved, false);
      return true;
    } catch { unavailable(); return false; }
  }
  async function load(url) {
    if (disposed) return;
    const ticket = ++generation; request?.abort(); request = new AbortController(); sourceUrl = url || ''; synthetic = false;
    unavailable(sourceUrl ? 'loading' : 'unconfigured');
    if (!sourceUrl) return;
    try {
      const result = await loader(sourceUrl, { origin: win.location.origin, signal: request.signal });
      if (ticket !== generation || disposed) return;
      if (result.snapshot) setSnapshot(result.snapshot); else unavailable(result.status);
    } catch { if (ticket === generation && !disposed) unavailable(); }
  }
  setMap(map); drawFeed();
  return { load, setSnapshot, setMap,
    get status() { return status; },
    dispose() { if (disposed) return; disposed = true; ++generation; request?.abort(); cancelHover(); detachMap(); feed.remove(); reader.remove(); },
  };
}
