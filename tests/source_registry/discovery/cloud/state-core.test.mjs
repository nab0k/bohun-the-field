// Offline storage tests and an IPC bridge used by Python protocol tests. No HTTP server.
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {createHash} from "node:crypto";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createInterface} from "node:readline";
import test from "node:test";
import net from "node:net";
import dns from "node:dns";
import {StateCoordinator, authenticated, route, MAX_SNAPSHOT, CHUNK_SIZE} from "../../../../cloudflare/source-discovery/state-core.mjs";

const TOKEN = "SYNTHETIC_" + "t".repeat(64);
const identity = {deployment: "synthetic-cloud-v1", code_sha256: "a".repeat(64), policy_sha256: "b".repeat(64)};
const owner = "c".repeat(64);
const sha = value => createHash("sha256").update(value).digest("hex");
const blocked = () => { throw Error("REAL_NETWORK_FORBIDDEN"); };
globalThis.fetch = blocked;
net.connect = net.createConnection = dns.lookup = dns.resolve = blocked;

function storage(path = ":memory:") {
  const db = new DatabaseSync(path);
  const value = {
    db, syncs: 0, failSync: false,
    sql: {exec(query, ...params) {
      const values = params.map(p => p instanceof ArrayBuffer ? new Uint8Array(p) : p);
      const rows = db.prepare(query).all(...values).map(row => {
        for (const key of Object.keys(row)) if (row[key] instanceof Uint8Array) {
          const b = row[key]; row[key] = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
        }
        return row;
      });
      return {toArray: () => rows};
    }},
    transactionSync(fn) {
      db.exec("BEGIN IMMEDIATE");
      try { const result = fn(); db.exec("COMMIT"); return result; }
      catch (e) { db.exec("ROLLBACK"); throw e; }
    },
    async sync() { this.syncs++; if (this.failSync) throw Error("SYNTHETIC_STORAGE_FAILURE"); }
  };
  return value;
}

function fixture(expected = identity, path) {
  const s = storage(path), core = new StateCoordinator(s, {...expected, bootstrap: true});
  const env = {RUNTIME_TOKEN: TOKEN, DEPLOYMENT_ID: expected.deployment,
    STATE: {idFromName(name) { assert.equal(name, expected.deployment); return "fixed-id"; },
            get(id) { assert.equal(id, "fixed-id"); return {fetch: request => core.handle(request)}; }}};
  const request = async (path, {body = {}, raw, headers = {}, lease} = {}) => {
    const h = {Authorization: "Bearer " + TOKEN, "Content-Type": raw ? "application/octet-stream" : "application/json",
      "X-Deployment": expected.deployment, "X-Code-Sha256": expected.code_sha256, "X-Policy-Sha256": expected.policy_sha256};
    if (raw) h["X-Snapshot-Sha256"] = sha(raw);
    if (lease) Object.assign(h, {"X-Owner": lease.owner, "X-Generation": String(lease.generation), "X-Revision": String(lease.revision)});
    return route(new Request("https://bohun-source-discovery-state.synthetic.workers.dev" + path,
      {method: "POST", headers: {...h, ...headers}, body: raw ?? JSON.stringify(body)}), env);
  };
  return {s, core, env, request};
}

const snapshot = Buffer.from("SQLite format 3\0SYNTHETIC_PRIVATE_STATE");
async function bootstrap(f, raw = snapshot) { const r = await f.request("/v1/bootstrap", {raw}); assert.equal(r.status, 200); }
async function acquire(f, token = owner) {
  const r = await f.request("/v1/acquire", {body: {owner: token}});
  assert.equal(r.status, 200); return r.json();
}

if (process.argv.includes("--protocol")) {
  // Test-only stdin/stdout messages call actual JS coordinator and real local SQLite.
  let f;
  for await (const line of createInterface({input: process.stdin})) {
    try {
      const item = JSON.parse(line);
      if (item.setup) {
        assert.ok(item.setup.token.startsWith("SYNTHETIC_"));
        f = fixture(item.setup);
        f.env.RUNTIME_TOKEN = item.setup.token;
        console.log(JSON.stringify({ready: true}));
      } else {
        assert.ok(f);
        const request = new Request(item.url, {method: "POST", headers: item.headers,
          body: Buffer.from(item.body, "base64")});
        const response = await route(request, f.env);
        console.log(JSON.stringify({status: response.status,
          body: Buffer.from(await response.arrayBuffer()).toString("base64")}));
      }
    } catch { console.log(JSON.stringify({bridge_error: true})); }
  }
  f?.s.db.close();
} else {
  test("missing state cannot acquire or silently bootstrap", async () => {
    const f = fixture(); assert.equal((await f.request("/v1/acquire", {body: {owner}})).status, 409); f.s.db.close();
  });
  test("bootstrap is explicit, one-time and requires server flag", async () => {
    const f = fixture(); f.core.expected.bootstrap = false;
    assert.equal((await f.request("/v1/bootstrap", {raw: snapshot})).status, 409);
    f.core.expected.bootstrap = true; await bootstrap(f);
    assert.equal((await f.request("/v1/bootstrap", {raw: snapshot})).status, 409); f.s.db.close();
  });
  test("only one concurrent owner acquires", async () => {
    const f = fixture(); await bootstrap(f);
    const responses = await Promise.all([f.request("/v1/acquire", {body: {owner}}),
      f.request("/v1/acquire", {body: {owner: "d".repeat(64)}})]);
    assert.deepEqual(responses.map(r => r.status).sort(), [200, 409]); f.s.db.close();
  });
  test("no elapsed-time lease stealing or owner reacquire", async () => {
    const f = fixture(); await bootstrap(f); await acquire(f);
    const old = Date.now; Date.now = () => old() + 30 * 86400000;
    try { assert.equal((await f.request("/v1/acquire", {body: {owner}})).status, 409); }
    finally { Date.now = old; f.s.db.close(); }
  });
  test("download returns exact bytes only to current lease", async () => {
    const f = fixture(); await bootstrap(f); const lease = await acquire(f);
    const r = await f.request("/v1/download", {lease}); assert.equal(r.status, 200);
    assert.deepEqual(Buffer.from(await r.arrayBuffer()), snapshot);
    assert.equal((await f.request("/v1/download", {lease: {...lease, owner: "d".repeat(64)}})).status, 409);
    f.s.db.close();
  });
  test("stale revision cannot checkpoint or release", async () => {
    const f = fixture(); await bootstrap(f); const lease = await acquire(f);
    assert.equal((await f.request("/v1/checkpoint", {lease, raw: snapshot})).status, 200);
    assert.equal((await f.request("/v1/checkpoint", {lease, raw: snapshot})).status, 409);
    assert.equal((await f.request("/v1/release", {lease})).status, 409); f.s.db.close();
  });
  test("normal release permits new fenced generation", async () => {
    const f = fixture(); await bootstrap(f); const first = await acquire(f);
    assert.equal((await f.request("/v1/release", {lease: first})).status, 200);
    const next = await acquire(f, "e".repeat(64)); assert.equal(next.generation, first.generation + 1);
    assert.equal((await f.request("/v1/download", {lease: first})).status, 409); f.s.db.close();
  });
  test("retain only current and previous chunks and manifests", async () => {
    const f = fixture(); await bootstrap(f); let lease = await acquire(f);
    for (let i = 0; i < 4; i++) {
      const r = await f.request("/v1/checkpoint", {lease, raw: snapshot}); lease = {...lease, ...await r.json()};
    }
    assert.deepEqual(f.s.db.prepare("SELECT revision FROM manifests ORDER BY revision").all().map(x => x.revision), [3, 4]);
    assert.equal(f.s.db.prepare("SELECT count(*) AS n FROM checkpoints").get().n, 2); f.s.db.close();
  });
  test("multi-chunk snapshot roundtrips; oversized input and wrong digest reject", async () => {
    const f = fixture(), raw = Buffer.alloc(CHUNK_SIZE * 2 + 1); snapshot.copy(raw);
    await bootstrap(f, raw); const lease = await acquire(f);
    assert.deepEqual(Buffer.from(await (await f.request("/v1/download", {lease})).arrayBuffer()), raw);
    assert.equal((await f.request("/v1/checkpoint", {lease, raw: snapshot, headers: {"X-Snapshot-Sha256": "a".repeat(64)}})).status, 409);
    assert.equal((await f.request("/v1/checkpoint", {lease, raw: Buffer.alloc(MAX_SNAPSHOT + 1)})).status, 409); f.s.db.close();
  });
  test("chunk loss gives HOLD, never shorter successful snapshot", async () => {
    const f = fixture(); await bootstrap(f); const lease = await acquire(f);
    f.s.db.exec("DELETE FROM checkpoints");
    assert.equal((await f.request("/v1/download", {lease})).status, 409); f.s.db.close();
  });
  test("unauthenticated callers cannot touch binding or storage", async () => {
    let called = false;
    const env = {RUNTIME_TOKEN: TOKEN, STATE: {idFromName() { called = true; }}};
    const request = new Request("https://state.invalid/v1/status", {method: "POST", body: "{}"});
    assert.equal((await route(request, env)).status, 401); assert.equal(called, false);
    assert.equal(await authenticated(request, {RUNTIME_TOKEN: ""}), false);
  });
  test("pins, unexpected paths/query/method and recovery endpoint reject", async () => {
    const f = fixture(); await bootstrap(f);
    assert.equal((await f.request("/v1/status", {headers: {"X-Code-Sha256": "f".repeat(64)}})).status, 409);
    assert.equal((await f.request("/v1/status?secret=SYNTHETIC_PRIVATE")).status, 404);
    assert.equal((await f.request("/v1/recover")).status, 404);
    assert.equal((await route(new Request("https://state.invalid/", {headers: {Authorization: "Bearer " + TOKEN}}), f.env)).status, 404);
    f.s.db.close();
  });
  test("storage sync failure never returns successful ACK", async () => {
    const f = fixture(); await bootstrap(f); f.s.failSync = true;
    const r = await f.request("/v1/acquire", {body: {owner}});
    assert.equal(r.status, 409); assert.equal(f.core.control().owner, owner); f.s.db.close();
  });
  test("failed SQL checkpoint transaction leaves revision unchanged", async () => {
    const f = fixture(); await bootstrap(f); const lease = await acquire(f), save = f.core.save;
    f.core.save = function (...args) { save.apply(this, args); throw Error("SYNTHETIC_AFTER_CHUNK"); };
    assert.equal((await f.request("/v1/checkpoint", {lease, raw: snapshot})).status, 409);
    assert.equal(f.core.control().revision, 0); assert.equal(f.s.db.prepare("SELECT count(*) AS n FROM manifests").get().n, 1);
    f.s.db.close();
  });
  test("disk restart preserves lease and state rather than reinitializing", async () => {
    const dir = mkdtempSync(join(tmpdir(), "discovery-state-test-")), path = join(dir, "private.sqlite");
    try {
      const first = fixture(identity, path); await bootstrap(first); await acquire(first); first.s.db.close();
      const second = fixture(identity, path);
      assert.equal((await second.request("/v1/acquire", {body: {owner: "d".repeat(64)}})).status, 409);
      assert.equal(second.core.control().revision, 0); second.s.db.close();
    } finally { rmSync(dir, {recursive: true, force: true}); }
  });
  test("errors never reveal auth, private body or storage exception", async () => {
    const f = fixture(); f.s.sync = async () => { throw Error(TOKEN); };
    const r = await f.request("/v1/bootstrap", {raw: snapshot}), text = await r.text();
    assert.equal(r.status, 409); assert.ok(!text.includes(TOKEN) && !text.includes("SYNTHETIC_PRIVATE_STATE")); f.s.db.close();
  });
}
