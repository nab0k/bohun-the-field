// Optional real-runtime contract checks. Supply an already installed Miniflare 5 module;
// this test never installs packages and intercepts ALL Worker outbound traffic.
// BOHUN_MINIFLARE_MODULE=/absolute/path/to/miniflare/dist/src/index.js node --test <this file>
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";
import test from "node:test";

const modulePath = process.env.BOHUN_MINIFLARE_MODULE;
if (!modulePath) {
  test("real workerd OAuth contract requires an installed Miniflare 5", {
    skip: "Set BOHUN_MINIFLARE_MODULE; synthetic transport tests do not prove Worker compatibility."
  }, () => {});
} else {
  const {Miniflare} = await import(pathToFileURL(resolve(modulePath)));
  const source = readFileSync(new URL("../../../../cloudflare/source-discovery/state-core.mjs", import.meta.url), "utf8");
  const url = "https://www.inoreader.com/oauth2/token";
  async function exercise(code, status = 200) {
    let calls = 0;
    // Only the default handler is exported from the test entrypoint. The actual
    // production transport body is included verbatim, with no fetch replacement.
    const script = code.replace(/^export /gm, "") + `
      export default {async fetch() {
        try {
          const reply = await refreshTransport(fetch, {client_id:"SYNTHETIC_ID",
            client_secret:"SYNTHETIC_SECRET"}, "SYNTHETIC_REFRESH");
          return Response.json({ok:true, scope:reply.scope});
        } catch (error) {
          return Response.json({ok:false,stage:error.stage,status:error.status});
        }
      }};`;
    const mf = new Miniflare({cf:false,telemetry:{enabled:false},workers:[{
      config:{name:"oauth-contract",compatibilityDate:"2026-10-26",manifest:{
        mainModule:"worker.mjs",modules:{"worker.mjs":{type:"esm",contents:script}}
      }},
      dev:{outboundService:{type:"fetcher",handler:async request => {
        calls++;
        assert.equal(request.url, url);
        assert.equal(request.method, "POST");
        const body = new URLSearchParams(await request.text());
        assert.equal(body.get("refresh_token"), "SYNTHETIC_REFRESH");
        if (status !== 200) return new Response("SYNTHETIC_PRIVATE_BODY", {
          status, headers:{Location:"https://must-never-follow.invalid/"}
        });
        return Response.json({access_token:"SYNTHETIC_ACCESS",refresh_token:"SYNTHETIC_NEXT",
          token_type:"Bearer",scope:"read",expires_in:86400});
      }}}
    }]});
    try {
      const response = await mf.dispatchFetch("http://localhost/");
      return {reply:await response.json(), calls};
    } finally { await mf.dispose(); }
  }
  test("actual Worker transport refresh succeeds with manual redirect mode", async () => {
    const result = await exercise(source);
    assert.deepEqual(result, {reply:{ok:true,scope:"read"},calls:1});
  });
  test("previous error redirect mode fails before any outbound request in workerd", async () => {
    assert.ok(source.includes('redirect: "manual"'));
    const result = await exercise(source.replace('redirect: "manual"', 'redirect: "error"'));
    assert.deepEqual(result, {reply:{ok:false,stage:"OAUTH_NETWORK_UNKNOWN",status:null},calls:0});
  });
  for (const status of [302, 307, 400]) {
    test(`workerd rejects HTTP ${status} without redirect, retry or error-body disclosure`, async () => {
      const result = await exercise(source, status);
      assert.deepEqual(result, {reply:{ok:false,stage:"OAUTH_HTTP",status},calls:1});
      assert.ok(!JSON.stringify(result).includes("SYNTHETIC_PRIVATE_BODY"));
    });
  }
}
