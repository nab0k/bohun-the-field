// Private operational checkpoints, not a source/business registry or collector.
export const MAX_SNAPSHOT = 2 * 1024 * 1024;
export const CHUNK_SIZE = 64 * 1024;
const HEX = /^[0-9a-f]{64}$/;
const NAME = /^[A-Za-z0-9_-]{1,80}$/;
const ROUTES = new Set(["/v1/bootstrap", "/v1/acquire", "/v1/download", "/v1/checkpoint", "/v1/release", "/v1/status"]);

class Hold extends Error {}
function check(value) { if (!value) throw new Hold(); }
function hex(bytes) { return Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, "0")).join(""); }
async function hash(bytes) { return hex(await crypto.subtle.digest("SHA-256", bytes)); }
function json(data, status = 200) {
  return new Response(JSON.stringify(data), {status, headers: {
    "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"
  }});
}

export async function readBounded(request, cap) {
  const length = request.headers.get("Content-Length");
  check(length === null || (/^[0-9]+$/.test(length) && Number(length) <= cap));
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader(), chunks = [];
  const deadline = Date.now() + 10000;
  let size = 0;
  try {
    while (true) {
      check(Date.now() < deadline);
      let timer;
      let result;
      try {
        result = await Promise.race([reader.read(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Hold()), deadline - Date.now());
        })]);
      } finally { clearTimeout(timer); }
      if (result.done) break;
      check(result.value instanceof Uint8Array);
      size += result.value.byteLength;
      check(size <= cap);
      chunks.push(result.value);
    }
    const raw = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.byteLength; }
    return raw;
  } catch (error) {
    void reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
}

export async function authenticated(request, env) {
  const token = env.RUNTIME_TOKEN, header = request.headers.get("Authorization");
  if (typeof token !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(token) ||
      typeof header !== "string" || header.length > 300) return false;
  const encoder = new TextEncoder();
  const a = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(header)));
  const b = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode("Bearer " + token)));
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}

export class StateCoordinator {
  constructor(storage, expected) {
    check(NAME.test(expected.deployment) && HEX.test(expected.code_sha256) && HEX.test(expected.policy_sha256));
    this.storage = storage;
    this.expected = {...expected};
    storage.sql.exec("CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, generation INTEGER NOT NULL, owner TEXT, manifest TEXT NOT NULL)");
    storage.sql.exec("CREATE TABLE IF NOT EXISTS checkpoints (revision INTEGER NOT NULL, part INTEGER NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(revision,part))");
    storage.sql.exec("CREATE TABLE IF NOT EXISTS manifests (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)");
  }

  identity() {
    return {version: 1, deployment: this.expected.deployment, code_sha256: this.expected.code_sha256,
            policy_sha256: this.expected.policy_sha256};
  }

  control() {
    const rows = this.storage.sql.exec("SELECT * FROM control WHERE id=1").toArray();
    check(rows.length === 1);
    const row = rows[0], manifest = JSON.parse(row.manifest), identity = this.identity();
    check(Object.entries(identity).every(([key, val]) => manifest[key] === val));
    check(manifest.schema === 1 && HEX.test(manifest.sha256) && Number.isInteger(manifest.size) &&
          manifest.size > 0 && manifest.size <= MAX_SNAPSHOT);
    return {...row, manifest};
  }

  lease(request) {
    const row = this.control();
    const generation = request.headers.get("X-Generation"), revision = request.headers.get("X-Revision");
    check(row.owner !== null && request.headers.get("X-Owner") === row.owner &&
          /^[0-9]+$/.test(generation ?? "") && /^[0-9]+$/.test(revision ?? "") &&
          Number(generation) === row.generation && Number(revision) === row.revision);
    return row;
  }

  save(revision, raw, manifest) {
    for (let start = 0, part = 0; start < raw.byteLength; start += CHUNK_SIZE, part++) {
      this.storage.sql.exec("INSERT INTO checkpoints VALUES (?,?,?)", revision, part,
                           raw.slice(start, start + CHUNK_SIZE).buffer);
    }
    this.storage.sql.exec("INSERT INTO manifests VALUES (?,?)", revision, JSON.stringify(manifest));
    this.storage.sql.exec("DELETE FROM checkpoints WHERE revision < ?", revision - 1);
    this.storage.sql.exec("DELETE FROM manifests WHERE revision < ?", revision - 1);
  }

  async handle(request) {
    try {
      const url = new URL(request.url), path = url.pathname;
      check(request.method === "POST" && !url.search && ROUTES.has(path));
      const identity = this.identity();
      check(request.headers.get("X-Deployment") === identity.deployment &&
            request.headers.get("X-Code-Sha256") === identity.code_sha256 &&
            request.headers.get("X-Policy-Sha256") === identity.policy_sha256);
      let reply;
      if (path === "/v1/bootstrap" || path === "/v1/checkpoint") {
        check(request.headers.get("Content-Type") === "application/octet-stream");
        const raw = await readBounded(request, MAX_SNAPSHOT);
        check(raw.byteLength > 0 && new TextDecoder().decode(raw.slice(0, 16)) === "SQLite format 3\x00");
        const sha256 = await hash(raw);
        check(request.headers.get("X-Snapshot-Sha256") === sha256);
        const manifest = {...identity, schema: 1, sha256, size: raw.byteLength};
        reply = this.storage.transactionSync(() => {
          if (path === "/v1/bootstrap") {
            check(this.expected.bootstrap === true &&
                  this.storage.sql.exec("SELECT id FROM control").toArray().length === 0 &&
                  this.storage.sql.exec("SELECT revision FROM manifests").toArray().length === 0);
            this.save(0, raw, manifest);
            this.storage.sql.exec("INSERT INTO control VALUES (1,0,0,NULL,?)", JSON.stringify(manifest));
            return {...identity, status: "BOOTSTRAPPED", revision: 0, sha256};
          }
          const row = this.lease(request), revision = row.revision + 1;
          check(Number.isSafeInteger(revision));
          this.save(revision, raw, manifest);
          this.storage.sql.exec("UPDATE control SET revision=?,manifest=? WHERE id=1", revision, JSON.stringify(manifest));
          return {...identity, status: "CHECKPOINTED", revision, generation: row.generation,
                  owner: row.owner, sha256};
        });
      } else {
        const bytes = await readBounded(request, 4096);
        const body = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
        check(body !== null && typeof body === "object" && !Array.isArray(body));
        reply = this.storage.transactionSync(() => {
          if (path === "/v1/status") {
            check(Object.keys(body).length === 0);
            const row = this.control();
            return {...identity, revision: row.revision, generation: row.generation, leased: row.owner !== null};
          }
          if (path === "/v1/acquire") {
            check(Object.keys(body).length === 1 && HEX.test(body.owner));
            const row = this.control(), generation = row.generation + 1;
            check(row.owner === null && Number.isSafeInteger(generation));
            this.storage.sql.exec("UPDATE control SET owner=?,generation=? WHERE id=1", body.owner, generation);
            return {...identity, owner: body.owner, generation, revision: row.revision, manifest: row.manifest};
          }
          check(Object.keys(body).length === 0);
          const row = this.lease(request);
          if (path === "/v1/release") {
            this.storage.sql.exec("UPDATE control SET owner=NULL WHERE id=1");
            return {...identity, status: "RELEASED", revision: row.revision};
          }
          check(path === "/v1/download");
          const rows = this.storage.sql.exec("SELECT part,payload FROM checkpoints WHERE revision=? ORDER BY part", row.revision).toArray();
          const raw = new Uint8Array(row.manifest.size);
          let offset = 0;
          for (let i = 0; i < rows.length; i++) {
            const chunk = new Uint8Array(rows[i].payload);
            check(rows[i].part === i && chunk.byteLength > 0 && chunk.byteLength <= CHUNK_SIZE &&
                  offset + chunk.byteLength <= raw.byteLength);
            raw.set(chunk, offset); offset += chunk.byteLength;
          }
          check(offset === raw.byteLength);
          return raw;
        });
      }
      // Never acknowledge pending data (including the lease) before durability.
      await this.storage.sync();
      if (reply instanceof Uint8Array) return new Response(reply, {headers: {
        "Content-Type": "application/octet-stream", "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"}});
      return json(reply);
    } catch {
      // No upstream diagnostics, owner nonces, body, URLs, SQL or tokens in errors.
      return json({status: "HOLD", reason: "STATE_CONTRACT_OR_STORAGE"}, 409);
    }
  }
}

export async function route(request, env) {
  if (!(await authenticated(request, env))) return json({status: "DENIED"}, 401);
  const url = new URL(request.url);
  if (request.method !== "POST" || url.search || !ROUTES.has(url.pathname)) return json({status: "HOLD"}, 404);
  try {
    const id = env.STATE.idFromName(env.DEPLOYMENT_ID);
    return await env.STATE.get(id).fetch(request);
  } catch { return json({status: "HOLD", reason: "STATE_UNAVAILABLE"}, 503); }
}
