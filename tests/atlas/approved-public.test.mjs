import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { loadApprovedSnapshot, MAX_SNAPSHOT_BYTES, validateApprovedSnapshot } from '../../src/atlas/approved-snapshot.js';
import { mountApprovedMaterials } from '../../src/atlas/approved-materials.js';
import { createAtlasState, mountM1Panels } from '../../src/atlas/m1-state.js';

const fixtures = JSON.parse(fs.readFileSync(new URL('../fixtures/exp009-approved-preview-v1.json', import.meta.url)));
const copy = () => structuredClone(fixtures.valid);
const origin = 'https://atlas.example.org';
const response = (value = fixtures.valid, options = {}) => new Response(JSON.stringify(value), {
  headers: { 'content-type': 'application/json' }, ...options,
});

test('existing Python projection: two readable versions, only one point, owned copy', () => {
  const source = copy(), result = validateApprovedSnapshot(source);
  assert.equal(result.items.length, 2); assert.equal(result.map.features.length, 1);
  source.items[0].body = 'changed after validation';
  assert.notEqual(result.items[0].body, source.items[0].body);
  assert.equal(result.items[1].material_id, 'synthetic-exp009-02');
  assert.ok(result.items[1].body.length > 100);
});

test('schema, HOLD, wrong version/hash, orphan/duplicate and unsafe links reject whole snapshot', () => {
  const changes = [
    value => { value.schema_version = 'unknown'; },
    value => { value.items[0].qa.decision = 'HOLD'; },
    value => { value.items[0].qa.public_release = false; },
    value => { value.items[0].version = 0; },
    value => { value.map.features[0].properties.version = 2; },
    value => { value.map.features[0].properties.content_hash = 'a'.repeat(64); },
    value => { value.map.features[0].id = 'unknown'; },
    value => { value.map.features.push(value.map.features[0]); },
    value => { value.items.push(value.items[0]); },
    value => { value.items[0].source_urls = ['javascript:alert(1)']; },
    value => { value.items[0].source_urls = ['data:text/html,x']; },
    value => { value.items[0].source_urls = ['https://name:password@example.org']; },
    value => { value.map.features[0].geometry.coordinates[0] = Infinity; },
    value => { value.map.features[0].geometry.type = 'Polygon'; },
    value => { value.items[0].published_at = 'invented'; },
  ];
  for (const mutate of changes) { const value = copy(); mutate(value); assert.throws(() => validateApprovedSnapshot(value)); }
  assert.throws(() => validateApprovedSnapshot(fixtures['bad-schema']));
});

test('loader uses one bounded same-origin GET without credentials or redirects', async () => {
  const calls = [];
  const result = await loadApprovedSnapshot('/public-approved.json', { origin, fetchImpl: async (...args) => { calls.push(args); return response(); } });
  assert.equal(result.status, 'ready'); assert.equal(calls.length, 1);
  assert.equal(calls[0][0], origin + '/public-approved.json');
  assert.equal(calls[0][1].credentials, 'omit'); assert.equal(calls[0][1].redirect, 'error');
  let requested = false;
  const fetchImpl = async () => { requested = true; return response(); };
  assert.equal((await loadApprovedSnapshot('', { origin, fetchImpl })).status, 'unconfigured');
  assert.equal((await loadApprovedSnapshot('https://other.example/x', { origin, fetchImpl })).status, 'unavailable');
  assert.equal((await loadApprovedSnapshot('/x#fragment', { origin, fetchImpl })).status, 'unavailable');
  assert.equal(requested, false);
});

test('HTTP, JSON, unsupported schema and non-JSON media all fail closed', async () => {
  for (const factory of [() => response({}, { status: 503 }), () => new Response('not json'),
    () => response(fixtures['bad-version']), () => response(copy(), { headers: { 'content-type': 'application/jsonp' } }),
    () => { throw Error('PRIVATE_UNTRUSTED_SERVER_MESSAGE'); }]) {
    const result = await loadApprovedSnapshot('/x', { origin, fetchImpl: async () => factory() });
    assert.deepEqual(result, { status: 'unavailable', snapshot: null });
  }
});

test('oversize stream and a stalled body are cancelled, no retry or unbounded response', async () => {
  let cancels = 0, calls = 0;
  const oversized = () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(MAX_SNAPSHOT_BYTES + 1)); }, cancel() { cancels++; },
  }), { headers: { 'content-type': 'application/json' } });
  assert.equal((await loadApprovedSnapshot('/x', { origin, fetchImpl: async () => { calls++; return oversized(); } })).status, 'unavailable');
  assert.equal(calls, 1); assert.equal(cancels, 1);
  let stalledCancelled = false;
  const stalled = new Response(new ReadableStream({ cancel() { stalledCancelled = true; } }), { headers: { 'content-type': 'application/json' } });
  assert.equal((await loadApprovedSnapshot('/x', { origin, timeoutMs: 10, fetchImpl: async () => stalled })).status, 'unavailable');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(stalledCancelled, true);
});

class Element extends EventTarget {
  constructor(tag, doc) {
    super(); this.tagName = tag.toUpperCase(); this.doc = doc; this.children = []; this.attributes = {}; this.hidden = false;
    this.className = ''; this.id = ''; this.style = {}; this._text = '';
    this.classList = { add: name => { this.className += ' ' + name; } };
  }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parentNode = this; } }
  replaceChildren(...nodes) { for (const node of this.children) node.parentNode = null; this.children = []; this._text = ''; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; }
  focus() { this.doc.activeElement = this; let parent = this.parentNode; while (parent) { parent.dispatchEvent(new Event('focusin')); parent = parent.parentNode; } }
  getBoundingClientRect() { return { width: 320, height: 180 }; }
}
function documentDouble() {
  const doc = { activeElement: null, createElement: tag => new Element(tag, doc) }; doc.body = doc.createElement('body'); return doc;
}
const find = (root, className) => [root, ...root.children.flatMap(child => all(child))].filter(node => node.className.split(' ').includes(className));
const all = root => [root, ...root.children.flatMap(all)];
const event = (node, type) => node.dispatchEvent(new Event(type));
class MapDouble {
  constructor() { this.sources = new Map([['existing-news', { data: 'untouched' }]]); this.layers = new Map(); this.listeners = []; this.moves = []; this.stops = 0; }
  getSource(id) { return this.sources.get(id); }
  addSource(id, value) { this.sources.set(id, { data: value.data, setData(data) { this.data = data; } }); }
  removeSource(id) { this.sources.delete(id); }
  getLayer(id) { return this.layers.get(id); }
  addLayer(value) { this.layers.set(value.id, value); }
  removeLayer(id) { this.layers.delete(id); }
  on(...args) { this.listeners.push(args); }
  off(...args) { this.listeners = this.listeners.filter(row => row.some((value, index) => value !== args[index]) || row.length !== args.length); }
  easeTo(value) { this.moves.push(value); }
  stop() { this.stops++; }
  fire(type, value = {}) { for (const row of this.listeners) if (row[0] === type && row.length === 2) row[1]({ type, ...value }); }
}
function rig(options = {}) {
  const doc = documentDouble(), feed = doc.createElement('aside'), reader = doc.createElement('aside'); reader.id = 'fixture-reader';
  const news = doc.createElement('section'); news.textContent = 'EXISTING SOURCE NEWS'; feed.append(news);
  const map = new MapDouble(), pending = new Map(); let sequence = 0;
  const timers = { setTimeout(fn) { pending.set(++sequence, fn); return sequence; }, clearTimeout(id) { pending.delete(id); } };
  const win = { innerWidth: 1280, innerHeight: 900, location: { origin } };
  const ui = mountApprovedMaterials({ feedHost: feed, readerHost: reader, map, documentImpl: doc, windowImpl: win, timers, ...options });
  return { ui, doc, feed, reader, map, news, win, flush() { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(fn => fn()); } };
}

test('reader displays literal HTML, all text/sources/version; bad refresh preserves news and other layers', () => {
  const r = rig(); assert.equal(r.ui.setSnapshot(copy(), { isSynthetic: true }), true);
  assert.equal(find(r.feed, 'approved-card').length, 2);
  event(find(r.feed, 'approved-title')[0], 'click');
  assert.equal(r.reader.hidden, false); assert.equal(find(r.reader, 'approved-body')[0].textContent, fixtures.valid.items[0].body);
  assert.equal(all(r.reader).some(node => node.tagName === 'SCRIPT'), false);
  assert.equal(find(r.reader, 'approved-source-link')[0].href, fixtures.valid.items[0].source_urls[0]);
  assert.match(r.reader.textContent, /Version 1/);
  assert.equal(r.ui.setSnapshot(fixtures['bad-version']), false);
  assert.equal(r.reader.hidden, true); assert.equal(find(r.feed, 'approved-card').length, 0);
  assert.equal(r.feed.children[0], r.news); assert.equal(r.news.textContent, 'EXISTING SOURCE NEWS');
  assert.equal(r.map.getSource('existing-news').data, 'untouched');
  assert.equal(r.map.getSource('exp009-approved-materials').data.features.length, 0);
});

test('hover/focus moves once; reading, scroll and material without geography never move camera', () => {
  const r = rig(); r.ui.setSnapshot(copy()); const [first, second] = find(r.feed, 'approved-card');
  event(first, 'mouseenter'); event(first, 'focusin'); r.flush(); assert.equal(r.map.moves.length, 1);
  event(find(r.feed, 'approved-title')[0], 'click'); event(r.reader, 'scroll'); r.flush(); assert.equal(r.map.moves.length, 1);
  event(second, 'mouseenter'); event(find(r.feed, 'approved-title')[1], 'click'); r.flush();
  assert.equal(r.map.moves.length, 1); assert.equal(find(r.reader, 'approved-map-button').length, 0);
  assert.equal(r.map.getSource('exp009-approved-materials').data.features.length, 1);
});

test('leaving a card or manually dragging cancels hover; manual gesture stops current transition', () => {
  const r = rig(); r.ui.setSnapshot(copy()); const card = find(r.feed, 'approved-card')[0];
  event(card, 'mouseenter'); event(card, 'mouseleave'); r.flush(); assert.equal(r.map.moves.length, 0);
  event(card, 'focusin'); r.map.fire('dragstart'); r.flush(); assert.equal(r.map.moves.length, 0);
  event(card, 'focusin'); r.flush(); assert.equal(r.map.moves.length, 1);
  r.map.fire('zoomstart', { originalEvent: {} }); r.flush(); assert.equal(r.map.moves.length, 1); assert.equal(r.map.stops, 2);
});

test('reduced motion uses zero duration; touch Show on map closes reader for map space', () => {
  const r = rig({ reducedMotion: true }); r.win.innerWidth = 390; r.ui.setSnapshot(copy());
  event(find(r.feed, 'approved-title')[0], 'click'); event(find(r.reader, 'approved-map-button')[0], 'click');
  assert.equal(r.reader.hidden, true); assert.equal(r.map.moves.length, 1); assert.equal(r.map.moves[0].duration, 0);
  assert.deepEqual(r.map.moves[0].center, [10, 51]); assert.equal(r.map.moves[0].zoom, 3.5);
});

test('Escape returns focus and aria-expanded to the selected title', () => {
  const r = rig(); r.ui.setSnapshot(copy()); const title = find(r.feed, 'approved-title')[0]; event(title, 'click');
  assert.equal(title.getAttribute('aria-expanded'), 'true');
  const key = new Event('keydown', { cancelable: true }); Object.defineProperty(key, 'key', { value: 'Escape' });
  find(r.reader, 'approved-reader')[0].dispatchEvent(key);
  assert.equal(r.reader.hidden, true); assert.equal(title.getAttribute('aria-expanded'), 'false'); assert.equal(r.doc.activeElement, title); r.flush(); assert.equal(r.map.moves.length, 0);
});

test('older in-flight response cannot restore analysis after a newer failed load', async () => {
  const promises = [];
  const r = rig({ loader: () => new Promise(resolve => promises.push(resolve)) });
  const old = r.ui.load('/first'), latest = r.ui.load('/latest');
  promises[1]({ status: 'unavailable', snapshot: null }); await latest;
  promises[0]({ status: 'ready', snapshot: copy() }); await old;
  assert.equal(r.ui.status, 'unavailable'); assert.equal(find(r.feed, 'approved-card').length, 0);
  r.ui.dispose(); assert.equal(r.map.listeners.length, 0); assert.equal(r.feed.children[0], r.news);
});

test('legacy country/company/filter changes cannot erase public reader hosts', () => {
  const doc = documentDouble(), previous = globalThis.document; globalThis.document = doc;
  try {
    const store = createAtlasState(), panels = mountM1Panels({ store, records: [], publicMaterials: true });
    const marker = doc.createElement('p'); marker.textContent = 'OWNED READER'; panels.dossier.append(marker);
    store.selectCountry('DE'); store.setFilters({ query: 'a company' });
    assert.equal(panels.dossier.children[0], marker); panels.dispose(); assert.equal(doc.body.children.length, 0);
  } finally { globalThis.document = previous; }
});


test('browser timers retain their Window receiver for hover and cancellation', () => {
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  let callback, cleared = false;
  globalThis.setTimeout = function (fn) { assert.equal(this, globalThis); callback = fn; return 1; };
  globalThis.clearTimeout = function () { assert.equal(this, globalThis); cleared = true; };
  try {
    const r = rig({ timers: undefined }); r.ui.setSnapshot(copy());
    const card = find(r.feed, 'approved-card')[0]; event(card, 'mouseenter'); callback();
    assert.equal(r.map.moves.length, 1);
    event(card, 'mouseleave'); event(card, 'mouseenter'); event(card, 'mouseleave');
    assert.equal(cleared, true); r.ui.dispose();
  } finally { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; }
});
