// One-time Inoreader OAuth for the news feeds (Serhii's subscriptions). Reads INOREADER_APP_ID / INOREADER_APP_KEY from
// .env.local, opens Inoreader's consent page in the browser, catches the redirect on http://localhost:8787/callback,
// swaps the code for tokens and writes them back to .env.local (git-ignored). Tokens are never printed.
// The redirect URI must be set to exactly http://localhost:8787/callback in the app settings on inoreader.com.
// Usage: node scripts/inoreader-auth.mjs        (then click "Authorize" in the browser)
import fs from 'node:fs';
import http from 'node:http';
import crypto from 'node:crypto';
import { execFile } from 'node:child_process';

const ENV = new URL('../.env.local', import.meta.url);
const env = Object.fromEntries(fs.readFileSync(ENV, 'utf8').split('\n').filter((l) => /^[A-Z_]+=/.test(l)).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const { INOREADER_APP_ID: id, INOREADER_APP_KEY: key } = env;
if (!id || !key) { console.error('INOREADER_APP_ID / INOREADER_APP_KEY missing in .env.local'); process.exit(2); }
const REDIRECT = 'http://localhost:8787/callback', state = crypto.randomBytes(12).toString('hex');
const authUrl = `https://www.inoreader.com/oauth2/auth?client_id=${id}&redirect_uri=${encodeURIComponent(REDIRECT)}&response_type=code&scope=read&state=${state}`;

function save(vars) {
  let text = fs.readFileSync(ENV, 'utf8');
  for (const [k, v] of Object.entries(vars)) {
    const line = `${k}=${v}`;
    text = new RegExp(`^${k}=.*$`, 'm').test(text) ? text.replace(new RegExp(`^${k}=.*$`, 'm'), line) : text.replace(/\n?$/, '\n') + line + '\n';
  }
  fs.writeFileSync(ENV, text, { mode: 0o600 });
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://localhost:8787');
  if (u.pathname !== '/callback') { res.writeHead(404).end(); return; }
  if (u.searchParams.get('state') !== state || !u.searchParams.get('code')) { res.writeHead(400).end('Authorization failed or was cancelled.'); console.error('no code / state mismatch'); server.close(); return; }
  try {
    const r = await fetch('https://www.inoreader.com/oauth2/token', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ code: u.searchParams.get('code'), redirect_uri: REDIRECT, client_id: id, client_secret: key, scope: '', grant_type: 'authorization_code' }),
    });
    const t = await r.json();
    if (!t.access_token) throw new Error('token exchange failed: ' + JSON.stringify({ status: r.status, error: t.error, description: t.error_description }));
    save({ INOREADER_ACCESS_TOKEN: t.access_token, INOREADER_REFRESH_TOKEN: t.refresh_token ?? '', INOREADER_TOKEN_EXPIRES: String(Date.now() + (t.expires_in ?? 0) * 1000) });
    // smoke test: who am I and how many subscriptions (counts only)
    const h = { Authorization: `Bearer ${t.access_token}` };
    const user = await fetch('https://www.inoreader.com/reader/api/0/user-info', { headers: h }).then((x) => x.json());
    const subs = await fetch('https://www.inoreader.com/reader/api/0/subscription/list', { headers: h }).then((x) => x.json());
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end('<p>Done. Inoreader is connected. You can close this tab.</p>');
    console.log(JSON.stringify({ connected: true, user: user.userName ? 'ok' : 'unknown', subscriptions: subs.subscriptions?.length ?? 0, folders: [...new Set((subs.subscriptions ?? []).flatMap((s) => s.categories.map((c) => c.label)))].length }));
  } catch (e) {
    res.writeHead(500).end('Token exchange failed, see the terminal.'); console.error(String(e.message || e));
  }
  server.close();
});
server.listen(8787, '127.0.0.1', () => {
  console.log('Opening Inoreader in the browser. If it does not open, go to:\n' + authUrl);
  execFile('open', [authUrl]);
});
