/**
 * BOHUN company enrichment: Cloudflare Worker read-only diagnostic.
 * No secrets in code, no Notion/Supabase writes, no deployment on commit.
 * POST /run requires bearer ADMIN_TOKEN. GET /health is public.
 */
export interface Env {
  NOTION_TOKEN: string;
  NOTION_DATA_SOURCE_ID: string;
  ADMIN_TOKEN: string;
  MAX_PAGES?: string;
}
type NotionPage = { id: string; last_edited_time?: string; properties?: Record<string, unknown> };
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
function authorized(request: Request, token: string): boolean {
  if (!token || token.length < 24) return false;
  const h = request.headers.get("authorization") ?? "";
  return h === `Bearer ${token}`;
}
async function scan(env: Env) {
  if (!env.NOTION_TOKEN || !env.NOTION_DATA_SOURCE_ID) return { error: "missing Notion secrets", status: 503 };
  const max = Math.min(10, Math.max(1, Number(env.MAX_PAGES || "10") || 10));
  const response = await fetch(`https://api.notion.com/v1/data_sources/${encodeURIComponent(env.NOTION_DATA_SOURCE_ID)}/query`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.NOTION_TOKEN}`, "notion-version": "2025-09-03", "content-type": "application/json" },
    body: JSON.stringify({ page_size: max }),
  });
  if (!response.ok) return { error: "notion_query_failed", status: response.status };
  const payload = await response.json() as { results?: NotionPage[]; has_more?: boolean };
  return {
    mode: "READ_ONLY_DIAGNOSTIC", count: payload.results?.length ?? 0, has_more: Boolean(payload.has_more),
    records: (payload.results ?? []).map(p => ({
      notion_page_id: p.id, last_edited_time: p.last_edited_time,
      available_properties: Object.keys(p.properties ?? {}).sort(),
    })),
    writes: 0, published: 0,
  };
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health" && request.method === "GET") return json({ ok: true, mode: "READ_ONLY_DIAGNOSTIC" });
    if (url.pathname !== "/run" || request.method !== "POST") return json({ error: "not_found" }, 404);
    if (!authorized(request, env.ADMIN_TOKEN)) return json({ error: "unauthorized" }, 401);
    try {
      const result = await scan(env);
      if ("error" in result) return json({ error: result.error }, result.status);
      return json(result);
    } catch {
      return json({ error: "scan_failed" }, 502);
    }
  },
};
