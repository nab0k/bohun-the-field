// Source registry → Inoreader OPML. One source of truth: data/sources/registry.csv.
// Only rows with status=accepted and a feed_url go into the OPML; folders keep the BOHUN_ prefix
// because the site reads only BOHUN_ folders (decision 007). Inoreader folders are flat, so the
// numbering lives in the folder name instead of nesting.
//   node scripts/build-opml.mjs            → data/inoreader/bohun-feeds.opml
import { readFileSync, writeFileSync } from 'node:fs';

const parse = (text) => {
  const rows = []; let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c; }
    else if (c === '"') q = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
    else if (c !== '\r') cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const [head, ...body] = rows;
  return body.filter((r) => r.length > 1).map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ''])));
};
const esc = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

const rows = parse(readFileSync('data/sources/registry.csv', 'utf8'));
const live = rows.filter((r) => r.status === 'accepted' && r.feed_url);
const byFolder = Map.groupBy(live, (r) => r.folder);
const out = [
  '<?xml version="1.0" encoding="UTF-8"?>',
  `<!-- Generated from data/sources/registry.csv by scripts/build-opml.mjs on ${new Date().toISOString().slice(0, 10)}. Do not edit by hand. -->`,
  '<opml version="2.0">', '  <head><title>Bohun Defence feeds</title></head>', '  <body>',
  ...[...byFolder.keys()].sort().flatMap((f) => [
    `    <outline text="${f}" title="${f}">`,
    ...byFolder.get(f).map((r) => `      <outline type="rss" text="${esc(r.name)}" title="${esc(r.name)}" xmlUrl="${esc(r.feed_url)}" htmlUrl="${esc(r.homepage_url)}"/>`),
    '    </outline>',
  ]),
  '  </body>', '</opml>', '',
].join('\n');
writeFileSync('data/inoreader/bohun-feeds.opml', out);
console.log(`${live.length} accepted feeds in ${byFolder.size} folders; ${rows.length - live.length} sources still candidates`);
