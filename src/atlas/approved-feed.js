// Public feed adapter: no token, no direct database connection, fail closed.
// GitHub Pages is static: consume only an independently signed, prevalidated build snapshot.\n// Never fetch directly from Supabase or treat client-side checks as independent QA.
// Client-side checks are defense in depth, NOT a replacement for server-side QA.
export function validatePublicRecord(r) {
  if (!r || typeof r !== 'object' || Array.isArray(r)) return false;
  if (r.qa?.decision !== 'APPROVE' || r.qa?.public_release !== true) return false;
  if (typeof r.content_hash !== 'string' || !/^[a-f0-9]{64}$/i.test(r.content_hash)) return false;
  if (typeof r.source_url !== 'string' || !/^https:\/\//.test(r.source_url)) return false;
  try { const u = new URL(r.source_url); if (u.username || u.password) return false; } catch { return false; }
  if (typeof r.headline !== 'string' || !r.headline.trim() || r.headline.length > 300) return false;
  if (typeof r.published_at !== 'string' || !/^\d{4}-\d\d-\d\d/.test(r.published_at)) return false;
  if (r.country_iso2 != null && !/^[A-Z]{2}$/.test(r.country_iso2)) return false;
  if (r.takeaway != null && (typeof r.takeaway !== 'string' || r.takeaway.length > 1200)) return false;
  return true;
}
export async function loadApprovedIntelligence({fetchImpl = fetch, signal} = {}) {
  try {
    const response = await fetchImpl('/intelligence/approved.json', {
      method: 'GET', credentials: 'omit', cache: 'no-store',
      headers: { Accept: 'application/json' }, signal
    });
    if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) return [];
    const body = await response.json();
    if (!body || !Array.isArray(body.records) || body.records.length > 500) return [];
    // Fail closed on any malformed or unapproved row, rather than silently accepting a partial batch.
    if (!body.records.every(validatePublicRecord)) return [];
    return body.records.slice(0, 100);
  } catch { return []; } // unavailable endpoint means honest empty state
}
