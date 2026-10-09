// Signal pipeline: feeds → new items → Gemini → rules → state/signals.json.
// Run by .github/workflows/signals.yml every 15 minutes. State lives on the `signals-data` branch,
// checked out into STATE_DIR. Nothing here needs a server or a database.
//
//   GEMINI_API_KEY=… node pipeline/run.mjs            normal run
//   DRY_RUN=1 node pipeline/run.mjs                    fetch and print, no model call, no writes
// Env: STATE_DIR (default ./state), GEMINI_MODEL (default gemini-3.5-flash-lite), AUTO_PUBLISH ("0" stops publishing)

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { parseFeed } from './rss.mjs';
import { evaluate } from './rules.mjs';
import { SYSTEM_PROMPT, buildUserPrompt, callGemini } from './llm.mjs';

const env = process.env;
const STATE_DIR = env.STATE_DIR || 'state';
const MODEL = env.GEMINI_MODEL || 'gemini-3.5-flash-lite';
const AUTO_PUBLISH = env.AUTO_PUBLISH !== '0';
const DRY = env.DRY_RUN === '1';
const MAX_AGE_H = 72;          // ignore items older than this
const BATCH = 40;              // items per model call
const MAP_DAYS = 30;           // signal stays on the map (§6)
const QUEUE_DAYS = 7;          // queued signals waiting for a second source
const now = new Date();
const iso = now.toISOString();
const hash = (s) => createHash('sha256').update(s).digest('hex').slice(0, 16);

// ---- feeds from the registry (accepted rows with a feed URL) ----
function parseCsv(text) {
  const rows = []; let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c; }
    else if (c === '"') q = true; else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; } else if (c !== '\r') cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const [h, ...b] = rows;
  return b.filter((r) => r.length > 1).map((r) => Object.fromEntries(h.map((k, i) => [k, r[i] ?? ''])));
}
const feeds = parseCsv(readFileSync('data/sources/registry.csv', 'utf8')).filter((r) => r.status === 'accepted' && r.feed_url);

// ---- state ----
mkdirSync(STATE_DIR, { recursive: true });
const statePath = `${STATE_DIR}/state.json`;
const state = existsSync(statePath) ? JSON.parse(readFileSync(statePath, 'utf8')) : { seen: {}, signals: [], hidden: [], runs: [] };
state.hidden ??= [];
const hidden = new Set(state.hidden);

// ---- fetch ----
async function fetchFeed(f) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 20000);
  try {
    const r = await fetch(f.feed_url, { signal: ctl.signal, headers: { 'user-agent': 'BohunDefenceSignals/1.0 (+https://nab0k.github.io/bohun-the-field/)', accept: 'application/rss+xml, application/atom+xml, application/xml, text/xml' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return { feed: f, items: parseFeed(await r.text()) };
  } catch (e) { return { feed: f, items: [], error: String(e.message ?? e) }; } finally { clearTimeout(t); }
}
const results = await Promise.all(feeds.map(fetchFeed));
const feedErrors = results.filter((r) => r.error).map((r) => `${r.feed.name}: ${r.error}`);

const fresh = [];
for (const { feed, items } of results) for (const it of items) {
  const id = hash(it.url);
  if (state.seen[id]) continue;
  if (it.published_at && now - new Date(it.published_at) > MAX_AGE_H * 3600e3) { state.seen[id] = iso; continue; }
  fresh.push({ ...it, id, publisher: feed.name, priority: feed.priority, source_id: feed.source_id });
}
console.log(`feeds ${feeds.length}, errors ${feedErrors.length}, new items ${fresh.length}`);
if (feedErrors.length) console.log('feed errors:\n  ' + feedErrors.join('\n  '));
if (DRY) { for (const it of fresh.slice(0, 15)) console.log(`- [${it.publisher}] ${it.title}`); process.exit(0); }
if (fresh.length && !env.GEMINI_API_KEY) { console.error('GEMINI_API_KEY is not set'); process.exit(2); }

// ---- model + rules ----
const byId = new Map(state.signals.map((s) => [s.id, s]));
// stored items carry no feed text: third-party text is used only inside the run (decision 007)
const itemsFor = (s) => s._items ?? [];
let usage = { prompt: 0, output: 0 };
const log = [];

for (let i = 0; i < fresh.length; i += BATCH) {
  const batch = fresh.slice(i, i + BATCH);
  const recent = state.signals.filter((s) => now - new Date(s.updated_at) < QUEUE_DAYS * 864e5).slice(-60);
  let out;
  try {
    out = await callGemini({ apiKey: env.GEMINI_API_KEY, model: MODEL, system: SYSTEM_PROMPT, user: buildUserPrompt(batch, recent) });
  } catch (e) { console.error(String(e.message ?? e)); process.exitCode = 1; continue; } // items stay unseen, retried next run
  usage.prompt += out.usage?.promptTokenCount ?? 0; usage.output += out.usage?.candidatesTokenCount ?? 0;
  for (const it of batch) state.seen[it.id] = iso;

  for (const cand of out.signals) {
    const cited = (cand.items ?? []).map((n) => batch[n - 1]).filter(Boolean);
    const facts = (cand.facts ?? []).map(({ label, value, quote }) => ({ label, value, quote }));
    const prev = cand.existing_id && byId.get(cand.existing_id);
    const items = [...(prev ? itemsFor(prev) : []), ...cited.map(({ id, title, url, publisher, priority, published_at, text }) => ({ id, title, url, publisher, priority, published_at, text }))]
      .filter((it, k, a) => a.findIndex((x) => x.url === it.url) === k);
    const base = prev ? { ...prev, ...cand, facts: [...(prev.facts ?? []), ...facts] } : { ...cand, facts, id: 'sig_' + hash(cand.title + iso), first_seen: iso };
    const { status, reasons, signal } = evaluate(base, items, { autoPublish: AUTO_PUBLISH });
    log.push(`${status.padEnd(9)} ${signal.type} | ${signal.title}${reasons.length ? '  ← ' + reasons.join('; ') : ''}`);
    if (status === 'rejected') continue;
    const rec = {
      id: signal.id, type: signal.type, title: signal.title, summary: signal.summary, why: signal.why,
      country_iso2: signal.country_iso2, place: signal.place ?? null, lat: signal.lat, lon: signal.lon,
      companies: signal.companies ?? [], facts: signal.facts.map(({ label, value, quote }) => ({ label, value, quote, verified: true })),
      sources: items.map(({ title, url, publisher, published_at }) => ({ title, url, publisher, published_at })),
      confidence: signal.confidence, status: prev?.status === 'published' && status === 'queued' ? 'published' : status,
      reasons, first_seen: signal.first_seen ?? iso, updated_at: iso, model: MODEL,
      _items: items.map(({ text, ...it }) => it),
    };
    if (prev && prev.status === 'published' && status === 'queued') rec.updated_note = 'Updated with a new source';
    byId.set(rec.id, rec);
  }
}

// ---- expiry, output ----
const keep = [...byId.values()].filter((s) => (s.status === 'published' ? now - new Date(s.first_seen) < 90 * 864e5 : now - new Date(s.updated_at) < QUEUE_DAYS * 864e5));
state.signals = keep;
for (const [k, v] of Object.entries(state.seen)) if (now - new Date(v) > 14 * 864e5) delete state.seen[k];
state.runs = [...(state.runs ?? []).slice(-95), { at: iso, feeds: feeds.length, feed_errors: feedErrors.length, new_items: fresh.length, model: MODEL, tokens: usage, log }];

const pub = keep.filter((s) => s.status === 'published' && !hidden.has(s.id)).map(({ _items, reasons, status, ...s }) => ({ ...s, facts: s.facts.map(({ verified, ...f }) => f), on_map: now - new Date(s.first_seen) < MAP_DAYS * 864e5 }));
pub.sort((a, b) => b.first_seen.localeCompare(a.first_seen));
writeFileSync(`${STATE_DIR}/signals.json`, JSON.stringify({ updated: iso, notice: 'AI-generated summaries. Check the sources.', count: pub.length, signals: pub }, null, 1));
writeFileSync(statePath, JSON.stringify(state));
console.log(log.join('\n') || 'no candidate signals');
console.log(`published ${pub.length}, queued ${keep.filter((s) => s.status === 'queued').length}, tokens in/out ${usage.prompt}/${usage.output}`);
