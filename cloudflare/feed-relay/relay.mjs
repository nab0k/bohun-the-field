// BOHUN feed relay (A4SC-06, owner 21.11 "сделать запасной путь через Cloudflare"). The feed collector runs on GitHub's
// addresses; a few sites refuse them (403) or cannot be reached from them. For those only, the collector asks this
// Worker to make the same GET from Cloudflare. It is a plain reader: one GET, an honest User-Agent, no cookies, no
// challenge solving, no retries; robots rules are checked by the collector before it asks. Secret RELAY_TOKEN.

export const UA = "BOHUN-FeedCollector/1.0 (+https://github.com/nab0k/bohun-the-field; via Cloudflare)";
export const MAX_BYTES = 2_000_000;
const PASS = ["if-none-match", "if-modified-since"];
const KEEP = ["content-type", "etag", "last-modified"];

function same(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export function target(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (!["http:", "https:"].includes(u.protocol) || u.username || u.password || (u.port && !["80", "443"].includes(u.port)))
    return null;
  const h = u.hostname.toLowerCase();
  // names only: no literal addresses, no local names
  if (/^[\d.]+$/.test(h) || h.includes(":") || h === "localhost" || h.endsWith(".local") || h.endsWith(".internal") || !h.includes("."))
    return null;
  return u;
}

async function readCapped(res) {
  if (!res.body) return {bytes: new Uint8Array(0), over: false};
  const reader = res.body.getReader();
  const parts = [];
  let size = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BYTES) { await reader.cancel(); return {bytes: null, over: true}; }
    parts.push(value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.byteLength; }
  return {bytes: out, over: false};
}

export async function handle(request, env, fetcher = fetch) {
  const url = new URL(request.url);
  if (request.method !== "GET" || url.pathname !== "/fetch") return new Response("not found\n", {status: 404});
  if (!env.RELAY_TOKEN || !same(request.headers.get("X-Relay-Token") || "", env.RELAY_TOKEN))
    return new Response("unauthorized\n", {status: 401});
  const t = target(url.searchParams.get("url"));
  if (!t) return new Response("bad url\n", {status: 400});
  const headers = {"User-Agent": UA, Accept: "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5"};
  for (const k of PASS) { const v = request.headers.get(k); if (v) headers[k] = v; }
  let res;
  try {
    res = await fetcher(t.toString(), {headers, redirect: "follow", signal: AbortSignal.timeout(20000)});
  } catch (e) {
    return new Response("upstream error\n", {status: 502});  // no X-Relay: the collector keeps its own result
  }
  const out = new Headers({"X-Relay": "1"});
  for (const k of KEEP) { const v = res.headers.get(k); if (v) out.set(k, v); }
  if (res.status === 304 || res.status === 204) return new Response(null, {status: res.status, headers: out});
  const {bytes, over} = await readCapped(res);
  if (over) return new Response(null, {status: 413, headers: out});
  return new Response(bytes, {status: res.status, headers: out});
}

export default {
  async fetch(request, env) {
    const res = await handle(request, env);
    // Worker logs are private to the account; still, only the status
    console.log(JSON.stringify({status: res.status}));
    return res;
  },
};
