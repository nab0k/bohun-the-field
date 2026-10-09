// Publication rules from docs/AI_EDITORIAL_RULES.md, enforced in code (the model is told the same rules,
// but nothing is published on the model's word alone). Pure functions: tested in pipeline/test.mjs.

export const TYPES = ['contract', 'funding', 'opportunity', 'production', 'partnership', 'policy', 'aid_ukraine', 'event', 'technology'];
export const NEEDS_FACT = new Set(['contract', 'funding', 'policy']);
export const EXCLUDED_COUNTRIES = new Set(['RU', 'BY', 'KP', 'IR']);
export const MIN_CONFIDENCE = 0.75;
export const UA_CENTRE = [31.2, 48.4]; // lon, lat — Ukraine is shown only as a country point (decision 006)

// §2.2 military situation: positions, movements, strikes, losses
const BANNED = [
  /\bfront[- ]?lines?\b/i, /\bshell(ing|ed)\b/i, /\bcasualt(y|ies)\b/i, /\b(killed|wounded|dead)\b/i,
  /\btroop (movements?|positions?|build-?up)\b/i, /\b(counter-?)?offensive\b/i, /\bstrikes? (on|against|hit)\b/i,
  /\bair ?raid\b/i, /\bmissile attack\b/i, /\bdrone attack\b/i, /\bbattlefield (situation|update)\b/i, /\boccupied\b.*\b(advance|captured)\b/i,
];
export const bannedHit = (text) => BANNED.find((r) => r.test(text))?.source ?? null;

const norm = (s) => (s ?? '').toLowerCase().replace(/[’‘]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, ' ').trim();

// §3: a fact counts only if its quote is literally in one of the cited items' text or title
export function verifyFact(fact, items) {
  const q = norm(fact.quote);
  if (q.length < 8 || q.length > 300) return false;
  return items.some((it) => norm(`${it.title} ${it.text}`).includes(q));
}

// §4: no run of 15+ words copied from a source
export function copiesSource(summary, items, n = 15) {
  const words = norm(summary).replace(/[^\p{L}\p{N}' ]/gu, ' ').split(/\s+/).filter(Boolean);
  if (words.length < n) return false;
  const src = items.map((it) => ' ' + norm(`${it.title} ${it.text}`).replace(/[^\p{L}\p{N}' ]/gu, ' ').replace(/\s+/g, ' ') + ' ');
  for (let i = 0; i + n <= words.length; i++) {
    const gram = ' ' + words.slice(i, i + n).join(' ') + ' ';
    if (src.some((s) => s.includes(gram))) return true;
  }
  return false;
}

// §5: one primary source (P0) or two independent publishers
export function sourcesOk(items) {
  if (items.some((it) => it.priority === 'P0')) return true;
  return new Set(items.map((it) => it.publisher)).size >= 2;
}

// Decide what happens to one candidate signal. Returns { status, reasons, signal } where status is
// 'published' | 'queued' (waits for a second source) | 'rejected' (never published).
export function evaluate(sig, items, { autoPublish = true } = {}) {
  const reasons = [];
  const s = { ...sig };
  if (!TYPES.includes(s.type)) return { status: 'rejected', reasons: [`type ${s.type} not publishable`], signal: s };
  if (EXCLUDED_COUNTRIES.has(s.country_iso2)) return { status: 'rejected', reasons: [`excluded country ${s.country_iso2}`], signal: s };
  const hit = bannedHit(`${s.title} ${s.summary} ${s.why}`);
  if (hit) return { status: 'rejected', reasons: [`military situation: ${hit}`], signal: s };
  if (!items.length) return { status: 'rejected', reasons: ['no sources'], signal: s };

  // Ukraine: country point only, no place name
  if (s.country_iso2 === 'UA') { s.place = null; [s.lon, s.lat] = UA_CENTRE; }
  if (!(Number.isFinite(s.lat) && Number.isFinite(s.lon) && Math.abs(s.lat) <= 85 && Math.abs(s.lon) <= 180)) { s.lat = null; s.lon = null; reasons.push('no valid point'); }

  // facts verified in an earlier run keep their flag (their source text is not stored, see run.mjs)
  s.facts = (s.facts ?? []).filter((f) => f.verified || verifyFact(f, items)).map((f) => ({ ...f, verified: true }));
  if (NEEDS_FACT.has(s.type) && !s.facts.length) reasons.push('no verified fact for amount or subject');
  if (copiesSource(s.summary, items)) reasons.push('summary copies 15+ words from a source');
  if (!(s.confidence >= MIN_CONFIDENCE)) reasons.push(`confidence ${s.confidence} < ${MIN_CONFIDENCE}`);
  if (!sourcesOk(items)) reasons.push('needs a primary source or a second publisher');
  if (!autoPublish) reasons.push('AUTO_PUBLISH is off');
  if (s.lat === null) reasons.push('no map point');
  if ((s.title ?? '').length > 120 || !(s.summary ?? '').trim()) reasons.push('title or summary malformed');
  return { status: reasons.length ? 'queued' : 'published', reasons, signal: s };
}
