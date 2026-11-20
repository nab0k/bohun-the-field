// BOHUN schedule relay (20.11, owner "Да, готовь"): GitHub starts this public repository's scheduled workflows
// 7-10 hours late and drops most frequent ones (EXP-034 DIAG_GITHUB_SCHEDULE_DELAY_20261120.md). Cloudflare Cron
// Triggers fire on the minute; every minute this Worker dispatches the workflows due at that UTC minute through
// the GitHub API (workflow_dispatch). The workflows' own cron stays as a fallback: while the repository variable
// SCHEDULE_RELAY_ENABLED is 'true' their scheduled runs skip themselves, so nothing runs twice.
// Secret GITHUB_TOKEN: fine-grained, this repository only, Actions: write. Nothing else is read or written.

export const REPO = "nab0k/bohun-the-field";

// Inoreader shares 100 zone-1 API requests a day between all runs: every INOREADER_EVERY_HOURS (default 4) at :07.
export function jobs(env = {}) {
  const every = Number(env.INOREADER_EVERY_HOURS || 4);
  if (!Number.isInteger(every) || every < 1 || every > 24 || 24 % every) throw new Error("INOREADER_EVERY_HOURS");
  const inoreader = [];
  for (let h = 0; h < 24; h += every) inoreader.push(`${String(h).padStart(2, "0")}:07`);
  return [
    {workflow: "inoreader-cloud.yml", at: inoreader, inputs: {scheduled_relay: "true", shared_dry_run: "false"}},
    {workflow: "source-search-daily.yml", at: ["04:40"], inputs: {dry_run: "false"}},
    {workflow: "research-openalex.yml", at: ["06:15"]},
    {workflow: "kb-daily.yml", at: ["06:20", "15:20"]},
    {workflow: "ted-daily.yml", at: ["07:15"]},
    {workflow: "opportunities-grants-us.yml", at: ["07:45"]},
  ];
}

export function due(date, env) {
  const hhmm = date.toISOString().slice(11, 16);
  return jobs(env).filter(j => j.at.includes(hhmm));
}

export async function dispatch(job, env, fetcher = fetch) {
  const res = await fetcher(`https://api.github.com/repos/${REPO}/actions/workflows/${job.workflow}/dispatches`, {
    method: "POST",
    headers: {Authorization: "Bearer " + env.GITHUB_TOKEN, Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "bohun-schedule-relay", "Content-Type": "application/json"},
    body: JSON.stringify({ref: "main", ...(job.inputs ? {inputs: job.inputs} : {})}),
  });
  return res.status;
}

export default {
  async scheduled(event, env, ctx) {
    const list = due(new Date(event.scheduledTime), env);
    for (const job of list) {
      let status;
      try { status = await dispatch(job, env); } catch (e) { status = "ERROR:" + e.name; }
      // Worker logs are private to the account; still, only the workflow name and the HTTP status
      console.log(JSON.stringify({workflow: job.workflow, status}));
    }
  },
  async fetch() {
    return new Response("bohun schedule relay\n", {status: 404});
  },
};
