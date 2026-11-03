// Private operational checkpoints, not a source/business registry or collector.
export const MAX_SNAPSHOT = 2 * 1024 * 1024;
export const CHUNK_SIZE = 64 * 1024;
const HEX = /^[0-9a-f]{64}$/;
const NAME = /^[A-Za-z0-9_-]{1,80}$/;
const ROUTES = new Set(["/v1/bootstrap", "/v1/acquire", "/v1/download", "/v1/checkpoint", "/v1/release", "/v1/status", "/v1/oauth-bootstrap", "/v1/oauth-access", "/v1/oauth-recover"]);
const SECRET = /^[\x21-\x7e]{1,4096}$/;
const OAUTH_URL = "https://www.inoreader.com/oauth2/token";
const MAX_TTL = 31 * 86400;
function scopes(value) {
  check(typeof value === "string" && /^(read|read write)$/.test(value));
  return value.split(" ");
}

// Only fixed codes and numeric status survive an OAuth failure; never provider text.
const OAUTH_STAGES = new Set(["PREPARED", "REFRESH_DISPATCHED", "READY", "RECOVERED",
  "OAUTH_CREDENTIALS", "OAUTH_NETWORK_UNKNOWN", "OAUTH_TIMEOUT_UNKNOWN", "OAUTH_HTTP",
  "OAUTH_REDIRECT", "OAUTH_BODY_UNKNOWN", "OAUTH_JSON", "OAUTH_RESPONSE_CONTRACT",
  "OAUTH_STORAGE_BEFORE_REQUEST", "OAUTH_STORAGE_UNKNOWN"]);
export class OAuthFailure extends Error {
  constructor(stage, status = null, attempted = false) {
    super("OAUTH_HOLD");
    this.stage = OAUTH_STAGES.has(stage) ? stage : "OAUTH_NETWORK_UNKNOWN";
    this.status = Number.isInteger(status) && status >= 100 && status <= 599 ? status : null;
    this.attempted = attempted === true;
  }
}

// Called only inside the DO refresh callback, never at import or construction.
export async function refreshTransport(fetcher, credentials, refresh) {
  let stage = "OAUTH_CREDENTIALS", status = null, attempted = false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    check(typeof refresh === "string" && SECRET.test(refresh) &&
      typeof credentials.client_id === "string" && SECRET.test(credentials.client_id) &&
      credentials.client_id.length <= 1024 && typeof credentials.client_secret === "string" &&
      SECRET.test(credentials.client_secret));
    const body = new URLSearchParams({client_id: credentials.client_id,
      client_secret: credentials.client_secret, refresh_token: refresh, grant_type: "refresh_token"});
    stage = "OAUTH_NETWORK_UNKNOWN"; attempted = true;
    // Workers supports manual/follow only. Reject non-200 responses below without following Location.
    const response = await fetcher(OAUTH_URL, {method: "POST", redirect: "manual",
      signal: controller.signal, headers: {"Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"}, body: body.toString()});
    status = response.status;
    stage = "OAUTH_HTTP"; check(status === 200);
    stage = "OAUTH_REDIRECT"; check(response.url === OAUTH_URL && !response.redirected);
    stage = "OAUTH_BODY_UNKNOWN";
    const raw = await readBounded(response, 16384);
    stage = "OAUTH_JSON";
    return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(raw));
  } catch (error) {
    if (controller.signal.aborted) stage = "OAUTH_TIMEOUT_UNKNOWN";
    throw new OAuthFailure(stage, status, attempted);
  } finally { clearTimeout(timer); }
}

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
    // Separate from opaque operational checkpoint blobs. Never included in download/status.
    storage.sql.exec("CREATE TABLE IF NOT EXISTS oauth_credentials (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL, status TEXT NOT NULL, access TEXT NOT NULL, refresh TEXT NOT NULL, expires_at INTEGER NOT NULL, scope TEXT NOT NULL)");
    storage.sql.exec("CREATE TABLE IF NOT EXISTS oauth_diagnostics (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL, stage TEXT NOT NULL, http_status INTEGER, upstream_may_have_started INTEGER NOT NULL)");
    storage.sql.exec("CREATE TABLE IF NOT EXISTS oauth_recoveries (operation_hash TEXT PRIMARY KEY, payload TEXT NOT NULL)");
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

  credential() {
    const rows = this.storage.sql.exec("SELECT * FROM oauth_credentials WHERE id=1").toArray();
    check(rows.length === 1);
    const row = rows[0];
    check(Number.isSafeInteger(row.generation) && row.generation >= 0 &&
      (row.status === "READY" || row.status === "REFRESHING") &&
      typeof row.refresh === "string" && SECRET.test(row.refresh) &&
      typeof row.access === "string" && (!row.access || SECRET.test(row.access)) &&
      Number.isSafeInteger(row.expires_at) && row.expires_at >= 0);
    if (row.access) scopes(row.scope);
    return row;
  }

  oauthIdle() {
    const rows = this.storage.sql.exec("SELECT status FROM oauth_credentials WHERE id=1").toArray();
    check(rows.length === 0 || (rows.length === 1 && rows[0].status === "READY"));
  }

  checkpointBytes(row) {
    check(Number.isInteger(row.manifest.size) && row.manifest.size > 0 && row.manifest.size <= MAX_SNAPSHOT);
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
  }

  diagnostic(generation, stage, httpStatus = null, attempted = false) {
    check(OAUTH_STAGES.has(stage));
    this.storage.sql.exec("INSERT OR REPLACE INTO oauth_diagnostics VALUES (1,?,?,?,?)",
      generation, stage, Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599 ? httpStatus : null,
      attempted === true ? 1 : 0);
  }

  oauthMetadata() {
    const row = this.storage.sql.exec("SELECT generation,status,expires_at,scope,length(access) AS cached_access_bytes FROM oauth_credentials WHERE id=1").toArray()[0] ?? null;
    const diagnostic = this.storage.sql.exec("SELECT generation,stage,http_status,upstream_may_have_started FROM oauth_diagnostics WHERE id=1").toArray()[0] ?? null;
    const audit = this.storage.sql.exec("SELECT payload FROM oauth_recoveries ORDER BY rowid DESC LIMIT 1").toArray()[0];
    return {credential: row, diagnostic, recovery: audit ? JSON.parse(audit.payload) : null};
  }

  async recoverOAuth(request) {
    // An explicit maintenance capability, separate from ordinary runtime access.
    check(this.expected.oauthRecovery === true && this.expected.bootstrap !== true &&
      this.expected.oauthBootstrap !== true && request.headers.get("Content-Type") === "application/json");
    const body = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(await readBounded(request, 4096)));
    check(body && Object.keys(body).length === 1 && typeof body.operation_id === "string" && HEX.test(body.operation_id));
    const spec = this.expected.oauthRecoverySpec?.(), grant = this.expected.oauthRecoveryGrant?.();
    check(spec && typeof spec.operation_id === "string" && HEX.test(spec.operation_id) && body.operation_id === spec.operation_id &&
      HEX.test(spec.previous_code_sha256) && HEX.test(spec.snapshot_sha256) &&
      [spec.revision, spec.lease_generation, spec.credential_generation].every(n => Number.isSafeInteger(n) && n >= 0));
    const operationHash = await hash(new TextEncoder().encode(body.operation_id));
    const now = Math.floor((this.expected.now ? this.expected.now() : Date.now()) / 1000);
    check(grant && grant.client_id === this.expected.oauthClientId && typeof grant.client_id === "string" &&
      typeof grant.access_token === "string" && SECRET.test(grant.access_token) &&
      typeof grant.refresh_token === "string" && SECRET.test(grant.refresh_token) &&
      grant.token_type === "Bearer" && grant.scope === "read" &&
      Number.isSafeInteger(grant.expires_at) && grant.expires_at > now + 60 && grant.expires_at <= now + MAX_TTL);
    const preview = this.storage.sql.exec("SELECT * FROM control WHERE id=1").toArray();
    check(preview.length === 1);
    const previewManifest = JSON.parse(preview[0].manifest);
    const raw = this.checkpointBytes({...preview[0], manifest: previewManifest});
    check(await hash(raw) === spec.snapshot_sha256 && previewManifest.sha256 === spec.snapshot_sha256);
    const reply = this.storage.transactionSync(() => {
      check(this.storage.sql.exec("SELECT operation_hash FROM oauth_recoveries WHERE operation_hash=?", operationHash).toArray().length === 0);
      // A new executable pin may be installed while the old journal remains held.
      // This is the ONLY explicit, audited pin transition; policy/deployment cannot change.
      const rows = this.storage.sql.exec("SELECT * FROM control WHERE id=1").toArray(); check(rows.length === 1);
      const row = rows[0], old = JSON.parse(row.manifest), credential = this.credential();
      check(row.manifest === preview[0].manifest && row.revision === spec.revision && row.generation === spec.lease_generation && row.owner !== null &&
        credential.generation === spec.credential_generation && credential.status === "REFRESHING" &&
        grant.refresh_token !== credential.refresh && grant.access_token !== credential.access &&
        old.version === 1 && old.schema === 1 && old.deployment === this.expected.deployment &&
        old.policy_sha256 === this.expected.policy_sha256 && old.code_sha256 === spec.previous_code_sha256 &&
        old.sha256 === spec.snapshot_sha256 && Number.isInteger(old.size) && old.size > 0 && old.size <= MAX_SNAPSHOT);
      const stored = this.storage.sql.exec("SELECT payload FROM manifests WHERE revision=?", row.revision).toArray();
      check(stored.length === 1 && stored[0].payload === row.manifest);
      const generation = row.generation + 1, credentialGeneration = credential.generation + 1;
      check(Number.isSafeInteger(generation) && Number.isSafeInteger(credentialGeneration));
      const manifest = {...old, code_sha256: this.expected.code_sha256};
      const audit = {operation_hash: operationHash, revision: row.revision, previous_generation: row.generation,
        generation, previous_credential_generation: credential.generation, credential_generation: credentialGeneration,
        previous_code_sha256: old.code_sha256, code_sha256: manifest.code_sha256, snapshot_sha256: old.sha256};
      this.storage.sql.exec("UPDATE oauth_credentials SET generation=?,status='READY',access=?,refresh=?,expires_at=?,scope=? WHERE id=1",
        credentialGeneration, grant.access_token, grant.refresh_token, grant.expires_at, grant.scope);
      this.storage.sql.exec("UPDATE manifests SET payload=? WHERE revision=?", JSON.stringify(manifest), row.revision);
      this.storage.sql.exec("UPDATE control SET owner=NULL,generation=?,manifest=? WHERE id=1", generation, JSON.stringify(manifest));
      this.diagnostic(credentialGeneration, "RECOVERED");
      this.storage.sql.exec("INSERT INTO oauth_recoveries VALUES (?,?)", operationHash, JSON.stringify(audit));
      return {...this.identity(), status: "OAUTH_RECOVERED", ...audit};
    });
    await this.storage.sync(); // Unknown ACK requires inspection, never a blind retry.
    return reply;
  }

  async oauth(request, path) {
    const bytes = await readBounded(request, 4096);
    const body = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
    check(body !== null && typeof body === "object" && !Array.isArray(body));
    let credential, generation;
    if (path === "/v1/oauth-bootstrap") {
      const lease = this.storage.transactionSync(() => {
        const row = this.lease(request);
        check(Object.keys(body).length === 0 && this.expected.oauthBootstrap === true &&
          this.storage.sql.exec("SELECT id FROM oauth_credentials").toArray().length === 0);
        const seed = this.expected.oauthSeed?.();
        check(typeof seed === "string" && SECRET.test(seed));
        this.storage.sql.exec("INSERT INTO oauth_credentials VALUES (1,0,'READY','',?,0,'')", seed);
        return row;
      });
      await this.storage.sync();
      return {...this.identity(), status: "OAUTH_BOOTSTRAPPED", credential_generation: 0,
        owner: lease.owner, generation: lease.generation, revision: lease.revision};
    }
    check(Object.keys(body).length === 1 && Object.hasOwn(body, "scope"));
    const required = scopes(body.scope);
    const now = () => Math.floor((this.expected.now ? this.expected.now() : Date.now()) / 1000);
    const choice = this.storage.transactionSync(() => {
      this.lease(request);
      const row = this.credential();
      check(row.status === "READY");
      if (row.scope) check(required.every(s => scopes(row.scope).includes(s)));
      if (row.access && Number.isSafeInteger(row.expires_at) && row.expires_at > now() + 60) {
        check(SECRET.test(row.access) && row.expires_at <= now() + MAX_TTL && row.scope);
        return {cached: true, row};
      }
      check(typeof this.expected.oauthRefresh === "function");
      generation = row.generation + 1;
      check(Number.isSafeInteger(generation));
      this.storage.sql.exec("UPDATE oauth_credentials SET generation=?,status='REFRESHING' WHERE id=1", generation);
      this.diagnostic(generation, "PREPARED");
      return {cached: false, row};
    });
    credential = choice.row;
    if (!choice.cached) {
      let stage = "OAUTH_STORAGE_BEFORE_REQUEST", attempted = false;
      try {
        await this.storage.sync(); // REFRESHING durable before the only upstream POST.
        this.diagnostic(generation, "REFRESH_DISPATCHED", null, true);
        await this.storage.sync();
        attempted = true;
        stage = "OAUTH_NETWORK_UNKNOWN";
        const data = await this.expected.oauthRefresh(credential.refresh);
        stage = "OAUTH_RESPONSE_CONTRACT";
        check(data && typeof data === "object" && typeof data.access_token === "string" &&
          SECRET.test(data.access_token) && typeof data.refresh_token === "string" &&
          SECRET.test(data.refresh_token) && data.token_type === "Bearer" &&
          Number.isSafeInteger(data.expires_in) && data.expires_in > 60 && data.expires_in <= MAX_TTL &&
          required.every(s => scopes(data.scope).includes(s)));
        const expires = now() + data.expires_in; check(Number.isSafeInteger(expires));
        stage = "OAUTH_STORAGE_UNKNOWN";
        this.storage.transactionSync(() => {
          this.lease(request);
          const pending = this.credential();
          check(pending.status === "REFRESHING" && pending.generation === generation);
          this.storage.sql.exec("UPDATE oauth_credentials SET status='READY',access=?,refresh=?,expires_at=?,scope=? WHERE id=1",
            data.access_token, data.refresh_token, expires, data.scope);
          this.diagnostic(generation, "READY", 200, true);
        });
        await this.storage.sync();
      } catch (error) {
        // Never overwrite diagnostics after an explicit recovery fenced this attempt.
        try {
          this.storage.transactionSync(() => {
            const row = this.credential();
            if (row.generation === generation) this.diagnostic(generation,
              error instanceof OAuthFailure ? error.stage : stage,
              error instanceof OAuthFailure ? error.status : null,
              error instanceof OAuthFailure ? error.attempted : attempted);
          });
          await this.storage.sync();
        } catch { /* No retry or fallback if the diagnostic itself cannot persist. */ }
        throw new Hold();
      }
    }
    const lease = this.lease(request), current = this.credential();
    check(current.status === "READY" && SECRET.test(current.access) && current.expires_at > now() + 60 &&
      required.every(s => scopes(current.scope).includes(s)));
    return {...this.identity(), status: "OAUTH_ACCESS", owner: lease.owner,
      generation: lease.generation, revision: lease.revision, credential_generation: current.generation,
      access_token: current.access, expires_at: current.expires_at, scope: current.scope};
  }

  async handle(request) {
    try {
      const url = new URL(request.url), path = url.pathname;
      check(request.method === "POST" && !url.search && ROUTES.has(path));
      const identity = this.identity();
      check(request.headers.get("X-Deployment") === identity.deployment &&
            request.headers.get("X-Code-Sha256") === identity.code_sha256 &&
            request.headers.get("X-Policy-Sha256") === identity.policy_sha256);
      if (path === "/v1/oauth-recover") return json(await this.recoverOAuth(request));
      if (path === "/v1/oauth-bootstrap" || path === "/v1/oauth-access") {
        check(request.headers.get("Content-Type") === "application/json");
        return json(await this.oauth(request, path));
      }
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
          this.oauthIdle();
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
            return {...identity, revision: row.revision, generation: row.generation, leased: row.owner !== null, oauth: this.oauthMetadata()};
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
            this.oauthIdle();
            this.storage.sql.exec("UPDATE control SET owner=NULL WHERE id=1");
            return {...identity, status: "RELEASED", revision: row.revision};
          }
          check(path === "/v1/download");
          return this.checkpointBytes(row);
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
