// Tests for the signal pipeline: RSS parsing, publication rules, and one full run with mocked network.
//   node pipeline/test.mjs
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseFeed, canonicalUrl } from './rss.mjs';
import { evaluate, verifyFact, copiesSource, sourcesOk, bannedHit, UA_CENTRE } from './rules.mjs';

let n = 0; const ok = (msg) => { n++; console.log('ok ', msg); };

// ---- RSS / Atom ----
const rss = `<?xml version="1.0"?><rss><channel><title>X</title>
<item><title><![CDATA[Saab wins &amp; Sweden orders]]></title><link>https://ex.com/a?utm_source=rss&amp;id=7</link>
<pubDate>Thu, 09 Oct 2026 08:00:00 GMT</pubDate><description><![CDATA[<p>The Swedish FMV signed a SEK 2.1 billion order.</p>]]></description></item>
<item><title>No link</title></item></channel></rss>`;
const r = parseFeed(rss);
assert.equal(r.length, 1); assert.equal(r[0].title, 'Saab wins & Sweden orders');
assert.equal(r[0].url, 'https://ex.com/a?id=7'); assert.equal(r[0].published_at, '2026-10-09T08:00:00.000Z');
assert.equal(r[0].text, 'The Swedish FMV signed a SEK 2.1 billion order.');
ok('RSS: CDATA, entities, tracking parameters, date, HTML stripped, items without link skipped');
const atom = `<feed><entry><title>Helsing raises</title><link rel="self" href="https://ex.com/self"/><link rel="alternate" href="https://ex.com/h"/>
<updated>2026-10-09T10:00:00Z</updated><summary>Helsing raised EUR 600 million.</summary></entry></feed>`;
const a = parseFeed(atom);
assert.equal(a[0].url, 'https://ex.com/h'); assert.equal(a[0].text, 'Helsing raised EUR 600 million.');
ok('Atom: alternate link preferred over self');
assert.equal(canonicalUrl('https://x.com/p?fbclid=1#top'), 'https://x.com/p');
ok('canonical URL drops fbclid and hash');

// ---- rules ----
const it1 = { title: 'Saab wins Swedish order', text: 'The Swedish FMV signed a SEK 2.1 billion order for Carl-Gustaf ammunition.', publisher: 'Defense News', priority: 'P1' };
const it2 = { title: 'Sweden orders ammunition', text: 'FMV placed an order worth SEK 2.1 billion with Saab.', publisher: 'Breaking Defense', priority: 'P1' };
assert.ok(verifyFact({ quote: 'SEK 2.1 billion order' }, [it1]));
assert.ok(verifyFact({ quote: 'sek 2.1   BILLION order' }, [it1]));
assert.ok(!verifyFact({ quote: 'SEK 3 billion order' }, [it1]));
ok('fact counts only if its quote is in the source (case and spacing ignored)');
assert.ok(copiesSource('Intro. The Swedish FMV signed a SEK 2.1 billion order for Carl-Gustaf ammunition, the agency said today in Stockholm.', [it1]) === false);
assert.ok(copiesSource('x The Swedish FMV signed a SEK 2.1 billion order for Carl-Gustaf ammunition and more words here now', [{ ...it1, text: it1.text + ' and more words here now' }]));
ok('15-word copy check');
assert.ok(!sourcesOk([it1])); assert.ok(sourcesOk([it1, it2])); assert.ok(sourcesOk([{ ...it1, priority: 'P0' }]));
ok('one P0 or two publishers');
assert.ok(bannedHit('Russian shelling of Kharkiv')); assert.ok(bannedHit('Front line update')); assert.ok(!bannedHit('Saab signs ammunition contract'));
ok('military-situation terms are blocked');

const sig = { type: 'contract', title: 'Saab — ammunition order — Sweden', summary: 'Sweden’s procurement agency ordered Carl-Gustaf rounds from Saab.', why: 'Shows sustained Nordic demand.',
  country_iso2: 'SE', place: 'Stockholm', lat: 59.33, lon: 18.07, confidence: 0.9, facts: [{ label: 'amount', value: 'SEK 2.1bn', quote: 'SEK 2.1 billion order' }] };
assert.equal(evaluate(sig, [it1, it2]).status, 'published');
ok('two publishers + verified amount → published');
const one = evaluate(sig, [it1]);
assert.equal(one.status, 'queued'); assert.match(one.reasons.join(), /second publisher/);
ok('one P1 source → queued, waits for a second');
assert.equal(evaluate({ ...sig, facts: [{ quote: 'SEK 9 billion' }] }, [it1, it2]).status, 'queued');
ok('contract without a verified amount → queued');
assert.equal(evaluate({ ...sig, type: 'opinion' }, [it1, it2]).status, 'rejected');
assert.equal(evaluate({ ...sig, country_iso2: 'RU' }, [it1, it2]).status, 'rejected');
assert.equal(evaluate({ ...sig, summary: 'Troop movements near the front line.' }, [it1, it2]).status, 'rejected');
ok('wrong type, excluded country, military situation → rejected');
const ua = evaluate({ ...sig, country_iso2: 'UA', place: 'Kharkiv', lat: 50, lon: 36.2 }, [it1, it2]).signal;
assert.equal(ua.place, null); assert.deepEqual([ua.lon, ua.lat], UA_CENTRE);
ok('Ukraine: place removed, country point only');
assert.equal(evaluate({ ...sig, confidence: 0.6 }, [it1, it2]).status, 'queued');
assert.equal(evaluate(sig, [it1, it2], { autoPublish: false }).status, 'queued');
ok('low confidence or AUTO_PUBLISH=0 → not published');

// ---- full run with mocked network ----
const dir = mkdtempSync(join(tmpdir(), 'sig-'));
const feedXml = (t, d) => `<rss><channel><item><title>${t}</title><link>https://news.example/${encodeURIComponent(t)}</link><pubDate>${new Date().toUTCString()}</pubDate><description>${d}</description></item></channel></rss>`;
let geminiCalls = 0;
globalThis.fetch = async (url, opts) => {
  url = String(url);
  if (url.includes('generativelanguage.googleapis.com')) {
    geminiCalls++;
    const body = JSON.parse(opts.body);
    assert.match(body.systemInstruction.parts[0].text, /Never return: opinions/);
    const text = JSON.stringify({ signals: [
      { existing_id: null, type: 'contract', title: 'Saab — Carl-Gustaf ammunition order — Sweden', summary: 'Sweden’s procurement agency placed a new ammunition order with Saab.', why: 'Nordic demand for anti-armour rounds keeps growing.', country_iso2: 'SE', place: 'Stockholm', lat: 59.33, lon: 18.07, companies: ['Saab'], facts: [{ label: 'amount', value: 'SEK 2.1bn', quote: 'SEK 2.1 billion', item: 1 }], items: [1, 2], confidence: 0.9 },
      { existing_id: null, type: 'contract', title: 'Ukraine plant — drones — Lviv', summary: 'A drone maker expands output.', why: 'Capacity.', country_iso2: 'UA', place: 'Lviv', lat: 49.8, lon: 24, companies: [], facts: [{ label: 'amount', value: 'UAH 1bn', quote: 'not in any source text', item: 3 }], items: [3], confidence: 0.8 },
      { existing_id: null, type: 'policy', title: 'Shelling update', summary: 'Front line shelling continues.', why: 'x', country_iso2: 'UA', items: [3], confidence: 0.9 },
    ] });
    return new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text }] } }], usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 200 } }), { status: 200 });
  }
  if (url.includes('defensenews')) return new Response(feedXml('Saab wins order', 'FMV signed a SEK 2.1 billion deal.'));
  if (url.includes('breakingdefense')) return new Response(feedXml('Sweden buys rounds', 'Order worth SEK 2.1 billion.'));
  if (url.includes('ukrinform')) return new Response(feedXml('Drone output grows', 'Ukrainian maker expands.'));
  return new Response('<rss><channel></channel></rss>');
};
process.env.STATE_DIR = dir; process.env.GEMINI_API_KEY = 'test'; process.env.DRY_RUN = '';
await import('./run.mjs?run=1');
const out = JSON.parse(readFileSync(join(dir, 'signals.json'), 'utf8'));
const st = JSON.parse(readFileSync(join(dir, 'state.json'), 'utf8'));
assert.equal(geminiCalls, 1);
assert.equal(out.count, 1); assert.equal(out.signals[0].companies[0], 'Saab');
assert.equal(out.signals[0].sources.length, 2); assert.ok(!('_items' in out.signals[0]) && !('reasons' in out.signals[0]));
ok('end to end: one published signal with two sources; internals not in the public file');
const q = st.signals.find((s) => s.country_iso2 === 'UA');
assert.equal(q.status, 'queued'); assert.equal(q.place, null); assert.equal(q.facts.length, 0);
assert.ok(st.signals.every((s) => (s._items ?? []).every((i) => !('text' in i))));
assert.ok(!st.signals.some((s) => /Shelling/.test(s.title)));
ok('UA signal queued without place or unverified fact; shelling rejected; no feed text stored in state');
await import('./run.mjs?run=2');
assert.equal(geminiCalls, 1);
ok('second run: already-seen items are not sent to the model again');
rmSync(dir, { recursive: true });
console.log(`${n} pipeline checks passed`);
