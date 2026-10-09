// SIGNALS layer (decision 011): AI-written summaries of industry events, built every 15 minutes by
// pipeline/run.mjs and published to the `signals-data` branch. Map points by type + a feed panel.
// Every card says it is AI-generated and links to its sources.

const SIGNALS_URL = import.meta.env.VITE_SIGNALS_URL || 'https://raw.githubusercontent.com/nab0k/bohun-the-field/signals-data/signals.json';
export const SIGNAL_TYPE = {
  contract: ['Contract', '#c8452f'], funding: ['Funding', '#3f8f6a'], opportunity: ['Opportunity', '#d9a520'],
  production: ['Production', '#8a5a2b'], partnership: ['Partnership', '#5b6fb0'], policy: ['Policy', '#7d5aa6'],
  aid_ukraine: ['Aid to Ukraine', '#2f7fd0'], event: ['Event', '#2f9fb8'], technology: ['Technology', '#4e5d23'],
};
const countryName = (() => { try { const d = new Intl.DisplayNames(['en'], { type: 'region' }); return (c) => (c ? d.of(c) : ''); } catch { return (c) => c ?? ''; } })();
const day = (s) => new Date(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

export async function initSignals({ map, $, el, reduced }) {
  let data = { signals: [], updated: null };
  try {
    const r = await fetch(SIGNALS_URL, { cache: 'no-cache' });
    if (r.ok) data = await r.json();
  } catch { /* no signals yet: the feed panel says so */ }
  const list = data.signals ?? [];
  const onMap = list.filter((s) => s.on_map !== false && Number.isFinite(s.lat) && Number.isFinite(s.lon));
  const fresh = (s) => Date.now() - new Date(s.first_seen) < 48 * 3600e3;

  map.addSource('signals', { type: 'geojson', data: { type: 'FeatureCollection', features: onMap.map((s) => ({
    type: 'Feature', properties: { id: s.id, color: (SIGNAL_TYPE[s.type] ?? ['', '#555'])[1], fresh: fresh(s) ? 1 : 0 },
    geometry: { type: 'Point', coordinates: [s.lon, s.lat] } })) } });
  map.addLayer({ id: 'sig-halo', type: 'circle', source: 'signals', filter: ['==', ['get', 'fresh'], 1],
    paint: { 'circle-radius': 15, 'circle-color': ['get', 'color'], 'circle-opacity': 0.18, 'circle-blur': 0.4 } });
  map.addLayer({ id: 'sig-point', type: 'circle', source: 'signals',
    paint: { 'circle-radius': ['case', ['==', ['get', 'fresh'], 1], 8, 6], 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff6cf', 'circle-stroke-width': 1.6 } });
  map.on('click', 'sig-point', (e) => showCard(e.features[0].properties.id));
  map.on('mouseenter', 'sig-point', () => (map.getCanvas().style.cursor = 'pointer'));
  map.on('mouseleave', 'sig-point', () => (map.getCanvas().style.cursor = ''));

  const link = (href, text) => { const a = el('a', null, text); a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a; };
  function showCard(id) {
    const s = list.find((x) => x.id === id); if (!s) return;
    $('#sig-feed').hidden = true;
    const box = $('#sig-card'); box.hidden = false;
    $('#sig-title').textContent = s.title;
    const body = $('#sig-body'); body.replaceChildren();
    const [label, color] = SIGNAL_TYPE[s.type] ?? [s.type, '#555'];
    const tag = el('span', 'sig-tag', label); tag.style.background = color;
    const meta = el('p', 'muted'); meta.append(tag, document.createTextNode(` ${[countryName(s.country_iso2), s.place].filter(Boolean).join(' · ')} · ${day(s.first_seen)}`));
    body.append(meta, el('p', null, s.summary));
    if (s.why) body.append(el('p', 'sig-why', `Why it matters: ${s.why}`));
    if (s.facts?.length) { const ul = el('ul', 'sig-facts'); for (const f of s.facts) ul.append(el('li', null, `${f.label}: ${f.value}`)); body.append(ul); }
    if (s.updated_note) body.append(el('p', 'muted', `${s.updated_note} · ${day(s.updated_at)}`));
    const src = el('ul', 'ind-list sig-sources');
    for (const x of s.sources ?? []) { const li = el('li'); li.append(link(x.url, x.publisher), document.createTextNode(` — ${x.title}`)); src.append(li); }
    body.append(el('b', 'sig-h', 'SOURCES'), src);
    body.append(el('p', 'notice', 'AI-generated summary of the sources above. Not verified by Bohun. Check the sources.'));
    map.easeTo({ center: [s.lon, s.lat], zoom: Math.max(map.getZoom(), 4), duration: reduced ? 0 : 700 });
  }
  function showFeed() {
    $('#sig-card').hidden = true;
    const box = $('#sig-feed'); box.hidden = false;
    const body = $('#sig-feed-body'); body.replaceChildren();
    body.append(el('p', 'muted', data.updated ? `Updated ${new Date(data.updated).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })} · refreshed every 15 minutes` : 'Signals refresh every 15 minutes.'));
    if (!list.length) body.append(el('p', null, 'No signals published yet. The first ones appear after the next run.'));
    const ul = el('ul', 'sig-list');
    for (const s of list.slice(0, 50)) {
      const li = el('li'); const b = el('button', 'sig-item'); b.type = 'button';
      const [label, color] = SIGNAL_TYPE[s.type] ?? [s.type, '#555'];
      const dot = el('i'); dot.style.background = color;
      b.append(dot, el('span', 'sig-item-meta', `${label} · ${countryName(s.country_iso2)} · ${day(s.first_seen)}`), el('span', 'sig-item-title', s.title));
      b.addEventListener('click', () => showCard(s.id)); li.append(b); ul.append(li);
    }
    body.append(ul, el('p', 'notice', 'AI-generated summaries. Each card links to its sources.'));
  }
  $('#sig-close').addEventListener('click', () => { $('#sig-card').hidden = true; });
  $('#sig-feed-close').addEventListener('click', () => { $('#sig-feed').hidden = true; });

  let on = true;
  function setSignals(v) {
    on = v;
    for (const id of ['sig-halo', 'sig-point']) map.setLayoutProperty(id, 'visibility', v ? 'visible' : 'none');
    $('#signals').classList.toggle('active', v);
    if (v) showFeed(); else { $('#sig-feed').hidden = true; $('#sig-card').hidden = true; }
  }
  $('#signals').addEventListener('click', () => setSignals(!on));
  $('#signals').textContent = `SIGNALS${list.length ? ' · ' + list.length : ''}`;
  setSignals(true); $('#sig-feed').hidden = true; // layer on by default, feed opens on click
  return { count: list.length, showFeed, showCard };
}
