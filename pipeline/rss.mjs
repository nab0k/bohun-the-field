// Minimal RSS 2.0 / Atom parser for the signal pipeline. No dependencies.
// Returns [{ title, url, published_at, text }] where text is the feed's own teaser, stripped of HTML.
// The teaser is used only inside the run (to check quotes); it is never published.

const decode = (s) => s
  .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
  .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
  .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
  .replace(/&(nbsp|amp|lt|gt|quot|apos|rsquo|lsquo|rdquo|ldquo|ndash|mdash|hellip);/g, (_, e) => ({
    nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', ndash: '–', mdash: '—', hellip: '…',
  })[e]);

export const stripHtml = (s) => decode(decode(s ?? '').replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, ' '))
  .replace(/\s+/g, ' ').trim();

const tag = (block, names) => {
  for (const n of names) {
    const m = block.match(new RegExp(`<${n}(?:\\s[^>]*)?>([\\s\\S]*?)</${n}>`, 'i'));
    if (m) return m[1];
  }
  return null;
};

// strip tracking parameters so the same article from two runs gets one URL
export function canonicalUrl(u) {
  try {
    const x = new URL(u.trim());
    for (const k of [...x.searchParams.keys()]) if (/^(utm_|fbclid|gclid|mc_|ref$|outputType$)/i.test(k)) x.searchParams.delete(k);
    x.hash = '';
    return x.toString();
  } catch { return u.trim(); }
}

export function parseFeed(xml) {
  const items = [];
  const blocks = xml.match(/<item[\s>][\s\S]*?<\/item>/gi) ?? xml.match(/<entry[\s>][\s\S]*?<\/entry>/gi) ?? [];
  for (const b of blocks) {
    const title = stripHtml(tag(b, ['title']));
    let url = stripHtml(tag(b, ['link', 'guid']));
    if (!url || !/^https?:/i.test(url)) { // Atom: <link rel="alternate" href="…"/>
      const links = [...b.matchAll(/<link\b([^>]*)\/?>/gi)].map((m) => m[1]);
      const pick = links.find((a) => /rel="alternate"/i.test(a)) ?? links.find((a) => !/rel=/i.test(a)) ?? links[0];
      const href = pick?.match(/href="([^"]+)"/i);
      url = href ? decode(href[1]) : url;
    }
    const date = stripHtml(tag(b, ['pubDate', 'published', 'updated', 'dc:date']));
    const text = stripHtml(tag(b, ['description', 'summary', 'content:encoded', 'content'])).slice(0, 1500);
    const t = Date.parse(date);
    if (!title || !url || !/^https?:/i.test(url)) continue;
    items.push({ title, url: canonicalUrl(url), published_at: Number.isNaN(t) ? null : new Date(t).toISOString(), text });
  }
  return items;
}
