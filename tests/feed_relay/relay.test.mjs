import assert from "node:assert/strict";
import test from "node:test";
import {handle, target, UA, MAX_BYTES} from "../../cloudflare/feed-relay/relay.mjs";

const env = {RELAY_TOKEN: "t0k3n"};
const req = (url, headers = {}) => new Request("https://relay.example/fetch?url=" + encodeURIComponent(url),
  {headers: {"X-Relay-Token": "t0k3n", ...headers}});

test("token, method, path and address checks come before any fetch", async () => {
  let called = 0;
  const f = async () => { called++; return new Response("x"); };
  assert.equal((await handle(new Request("https://relay.example/fetch?url=https://a.example/feed"), env, f)).status, 401);
  assert.equal((await handle(req("https://a.example/feed", {"X-Relay-Token": "wrong"}), env, f)).status, 401);
  assert.equal((await handle(req("https://a.example/feed"), {}, f)).status, 401);
  assert.equal((await handle(new Request("https://relay.example/other", {headers: {"X-Relay-Token": "t0k3n"}}), env, f)).status, 404);
  for (const bad of ["ftp://a.example/x", "http://127.0.0.1/x", "http://localhost/x", "http://[::1]/x",
                     "https://user:pw@a.example/", "https://a.example:8443/x", "http://intranet/x", "not a url"])
    assert.equal((await handle(req(bad), env, f)).status, 400, bad);
  assert.equal(called, 0);
  assert.ok(target("https://www.a.example/feed?x=1"));
});

test("one honest GET; status, body and cache headers come back marked X-Relay", async () => {
  let seen;
  const f = async (url, init) => { seen = {url, init}; return new Response("<rss/>", {status: 200,
    headers: {"Content-Type": "application/rss+xml", ETag: '"v2"', "Set-Cookie": "a=b"}}); };
  const res = await handle(req("https://a.example/feed", {"If-None-Match": '"v1"', Cookie: "c=d"}), env, f);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "<rss/>");
  assert.equal(res.headers.get("X-Relay"), "1");
  assert.equal(res.headers.get("ETag"), '"v2"');
  assert.equal(res.headers.get("Set-Cookie"), null);
  assert.equal(seen.init.headers["User-Agent"], UA);
  assert.equal(seen.init.headers["if-none-match"], '"v1"');
  assert.equal(seen.init.headers.cookie, undefined);
});

test("304 passes without a body, the site's 403 passes as is, a failed fetch is not marked", async () => {
  const r304 = await handle(req("https://a.example/feed"), env, async () => new Response(null, {status: 304}));
  assert.equal((r304.status), 304);
  const r403 = await handle(req("https://a.example/feed"), env, async () => new Response("no", {status: 403}));
  assert.equal(r403.status, 403);
  assert.equal(r403.headers.get("X-Relay"), "1");
  const down = await handle(req("https://a.example/feed"), env, async () => { throw new TypeError("network"); });
  assert.equal(down.status, 502);
  assert.equal(down.headers.get("X-Relay"), null);
});

test("bodies over the size limit are refused", async () => {
  const big = new Uint8Array(MAX_BYTES + 10);
  const res = await handle(req("https://a.example/feed"), env, async () => new Response(big, {status: 200}));
  assert.equal(res.status, 413);
});
