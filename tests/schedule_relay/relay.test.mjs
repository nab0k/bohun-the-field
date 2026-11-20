import assert from "node:assert/strict";
import test from "node:test";
import relay, {due, dispatch, jobs, REPO} from "../../cloudflare/schedule-relay/relay.mjs";

const at = s => new Date(`2026-11-20T${s}:00Z`);
const names = list => list.map(j => j.workflow);

test("daily jobs fire at their UTC minute only", () => {
  assert.deepEqual(names(due(at("04:40"))), ["source-search-daily.yml"]);
  assert.deepEqual(names(due(at("06:20"))), ["kb-daily.yml"]);
  assert.deepEqual(names(due(at("15:20"))), ["kb-daily.yml"]);
  assert.deepEqual(names(due(at("07:15"))), ["ted-daily.yml"]);
  assert.deepEqual(names(due(at("04:41"))), []);
});

test("inoreader every 4 hours by default, configurable, never more often than the quota allows by choice", () => {
  const ino = env => jobs(env)[0].at;
  assert.deepEqual(ino({}), ["00:07", "04:07", "08:07", "12:07", "16:07", "20:07"]);
  assert.equal(ino({INOREADER_EVERY_HOURS: "6"}).length, 4);
  assert.throws(() => jobs({INOREADER_EVERY_HOURS: "5"}));
  assert.deepEqual(names(due(at("08:07"))), ["inoreader-cloud.yml"]);
  assert.deepEqual(jobs({})[0].inputs, {scheduled_relay: "true", shared_dry_run: "false"});
});

test("dispatch sends one workflow_dispatch on main with the inputs", async () => {
  const calls = [];
  const fake = async (url, init) => { calls.push([url, init]); return {status: 204}; };
  const status = await dispatch(jobs({})[1], {GITHUB_TOKEN: "t"}, fake);
  assert.equal(status, 204);
  assert.equal(calls[0][0], `https://api.github.com/repos/${REPO}/actions/workflows/source-search-daily.yml/dispatches`);
  assert.equal(calls[0][1].method, "POST");
  assert.equal(calls[0][1].headers.Authorization, "Bearer t");
  assert.deepEqual(JSON.parse(calls[0][1].body), {ref: "main", inputs: {dry_run: "false"}});
});

test("scheduled handler logs only workflow and status", async () => {
  const logged = [], realFetch = globalThis.fetch, realLog = console.log;
  globalThis.fetch = async () => ({status: 204});
  console.log = s => logged.push(s);
  try {
    await relay.scheduled({scheduledTime: at("06:20").getTime()}, {GITHUB_TOKEN: "secret-token"}, {});
  } finally { globalThis.fetch = realFetch; console.log = realLog; }
  assert.deepEqual(logged.map(s => JSON.parse(s)), [{workflow: "kb-daily.yml", status: 204}]);
  assert.ok(!logged.join("").includes("secret-token"));
});
