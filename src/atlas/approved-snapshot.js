// Public transport validation, not publication authority or a QA signature check.
export const SNAPSHOT_SCHEMA = 'exp009-approved-preview-v1';
export const MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024;
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const bytes = value => new TextEncoder().encode(value).byteLength;
const text = (value, max) => typeof value === 'string' && value.trim() && bytes(value) <= max;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function keys(value, names) {
  if (!object(value) || Object.keys(value).length !== names.length || !names.every(name => Object.hasOwn(value, name))) {
    throw Error('SNAPSHOT_FIELDS');
  }
}

export function sourceURL(value) {
  if (!text(value, 8192)) throw Error('SOURCE_URL');
  const parsed = new URL(value);
  if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password) throw Error('SOURCE_URL');
  return parsed.href;
}

export const materialKey = item => JSON.stringify([item.material_id, item.version, item.content_hash]);

export function validateApprovedSnapshot(value) {
  keys(value, ['schema_version', 'snapshot_hash', 'items', 'map']);
  if (value.schema_version !== SNAPSHOT_SCHEMA || !hash(value.snapshot_hash) || !Array.isArray(value.items) || value.items.length > 20) {
    throw Error('SNAPSHOT_VERSION_OR_BOUND');
  }
  const byId = new Map();
  for (const item of value.items) {
    keys(item, ['material_id', 'version', 'content_hash', 'headline', 'body', 'origin', 'qa', 'source_urls']);
    keys(item.qa, ['decision', 'public_release']);
    if (!text(item.material_id, 256) || byId.has(item.material_id) || !Number.isSafeInteger(item.version) || item.version < 1 ||
        !hash(item.content_hash) || !text(item.headline, 1200) || !text(item.body, 128 * 1024) ||
        !['machine', 'human'].includes(item.origin) || item.qa.decision !== 'APPROVE' || item.qa.public_release !== true ||
        !Array.isArray(item.source_urls) || item.source_urls.length < 1 || item.source_urls.length > 5) {
      throw Error('SNAPSHOT_ITEM');
    }
    item.source_urls.forEach(sourceURL);
    byId.set(item.material_id, item);
  }
  keys(value.map, ['type', 'features']);
  if (value.map.type !== 'FeatureCollection' || !Array.isArray(value.map.features) || value.map.features.length > value.items.length) {
    throw Error('SNAPSHOT_MAP');
  }
  const mapped = new Set();
  for (const feature of value.map.features) {
    keys(feature, ['type', 'id', 'geometry', 'properties']);
    keys(feature.geometry, ['type', 'coordinates']);
    keys(feature.properties, ['material_id', 'version', 'content_hash', 'headline']);
    const item = byId.get(feature.id), coordinates = feature.geometry.coordinates;
    if (feature.type !== 'Feature' || !item || mapped.has(feature.id) ||
        materialKey(feature.properties) !== materialKey(item) || feature.properties.headline !== item.headline ||
        feature.geometry.type !== 'Point' || !Array.isArray(coordinates) || coordinates.length !== 2 ||
        !coordinates.every(Number.isFinite) || Math.abs(coordinates[0]) > 180 || Math.abs(coordinates[1]) > 90) {
      throw Error('SNAPSHOT_POINT_VERSION');
    }
    mapped.add(feature.id);
  }
  // Own the data. A caller cannot mutate a previously checked version in place.
  return structuredClone(value);
}

export async function loadApprovedSnapshot(url, {
  origin = globalThis.location?.origin, fetchImpl = globalThis.fetch, timeoutMs = 8000, signal,
} = {}) {
  if (!url) return { status: 'unconfigured', snapshot: null };
  const controller = new AbortController();
  let timer, activeReader;
  const abort = () => {
    controller.abort();
    activeReader?.cancel().catch(() => {});
  };
  try {
    const target = new URL(url, origin);
    if (target.origin !== origin || target.username || target.password || target.hash || !['https:', 'http:'].includes(target.protocol)) {
      throw Error('SNAPSHOT_ORIGIN');
    }
    if (signal?.aborted) controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    // A deadline also covers a response whose stream stalls after its headers.
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => { abort(); reject(Error('SNAPSHOT_TIMEOUT')); }, timeoutMs);
    });
    const read = async () => {
      if (controller.signal.aborted) throw Error('SNAPSHOT_ABORTED');
      const response = await fetchImpl(target.href, {
        method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'error',
        headers: { Accept: 'application/json' }, signal: controller.signal,
      });
      const mediaType = (response.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase();
      if (!response.ok || response.redirected || mediaType !== 'application/json') {
        throw Error('SNAPSHOT_HTTP');
      }
      const declared = response.headers.get('content-length');
      if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_SNAPSHOT_BYTES)) throw Error('SNAPSHOT_SIZE');
      if (!response.body?.getReader) throw Error('SNAPSHOT_STREAM_REQUIRED');
      const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
      activeReader = reader;
      let size = 0, raw = '';
      try {
        while (true) {
          const chunk = await reader.read();
          if (controller.signal.aborted) throw Error('SNAPSHOT_ABORTED');
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > MAX_SNAPSHOT_BYTES) throw Error('SNAPSHOT_SIZE');
          raw += decoder.decode(chunk.value, { stream: true });
        }
        raw += decoder.decode();
        return validateApprovedSnapshot(JSON.parse(raw));
      } finally {
        // Explicit cancellation also covers streams detached from the fetch signal.
        if (controller.signal.aborted || size > MAX_SNAPSHOT_BYTES) reader.cancel().catch(() => {});
        activeReader = undefined;
        reader.releaseLock();
      }
    };
    return { status: 'ready', snapshot: await Promise.race([read(), deadline]) };
  } catch {
    abort();
    return { status: 'unavailable', snapshot: null };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
