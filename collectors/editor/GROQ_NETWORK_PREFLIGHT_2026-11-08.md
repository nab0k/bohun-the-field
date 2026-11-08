# C3-10 | No-auth Groq network preflight, 2026-11-08

## Observed (not inferred)
- Owner launched `editor-groq-access-probe.yml` as GitHub Actions run `42472503368`. The single **benign authenticated** model call returned HTTP **403** with an unreadable first 2 KiB (not parsed as JSON). Groq user screenshots showed one Default Project and one nonexpired named key; Groq dashboard did not visibly show a matching failure. None of this establishes whether the cause was WAF, proxy, egress geography, API key or model restrictions.
- Earlier C3-10 factcheck run `42469818979` likewise recorded HTTP 403 with 0 verified claim outcomes; the underlying publisher was not called.

## New diagnostic
- `collectors/editor/groq_network_probe.py` sends **one GET** to the fixed `https://api.groq.com/openai/v1/models` endpoint, with **no Authorization header, no request body, no model invocation and no DB connection**.
- Refuses HTTP redirects; HTTPS verifies certificates under Python defaults. The response is consumed for at most **192 bytes**. The body and raw response headers are **never retained or logged**.
- The metadata-only report exposes a closed list: HTTP status, JSON-like/HTML-like body prefix class, MIME class, known server family, booleans for `cf-ray`, `cf-mitigated: challenge`, `www-authenticate`, a redirect-location presence flag, and a generic network/TLS failure flag. Never record Cloudflare Ray IDs or raw header values.
- One manual owner-only GitHub Actions workflow: `.github/workflows/editor-groq-network-probe.yml`. Requires exact main SHA and `allow_unauthenticated_get=yes`. No schedule, secrets, external Python dependencies, model quota or database access. Runs the full editor test suite first. Artifacts retained for 3 days.
- Returns exit code 0 only for HTTP **401 + JSON-like prefix**; all other cases return nonzero **after** the metadata artifact is written. Thus a green check means unauthenticated access to the API's auth rejection route, **not** Groq account readiness.

## Interpretation limits
- **401 + JSON-like:** the runner reached an API authentication rejection path. It does not prove the production key, model or full POST will work.
- **403 + HTML-like:** consistent with a proxy, edge policy or WAF rejection. It is not proof that Cloudflare issued the rejection; `server_class` and `cf_*_present` are corroborative only.
- **403 + JSON-like:** an API-style policy response is possible. We deliberately do not parse untrusted error messages or raw vendor codes without an allowlist.
- **No status:** network/TLS failure class, not automatically an API or key problem.
- The new **no-key** response must not be compared to the original authenticated 403 as though the requests were identical. They differ in endpoint, method and authentication.

## Tests and release gate
- 10/10 synthetic local tests passed before PR. The entire editor suite for this new revision has **not** run in GitHub yet.
- Original `editor-independent-shadow.yml` remains manual, and its publication flag remains false. Do not keep retrying model calls until the no-key network result is inspected.
- Groq API endpoint reference: https://console.groq.com/docs/api-reference
- Cloudflare 403 reference: https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/
