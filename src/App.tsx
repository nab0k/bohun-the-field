import { useEffect, useRef, useState } from 'react';
import maplibregl, { type Map as MapLibreMap, type Marker } from 'maplibre-gl';

type Signal = { country: string; tag: string; title: string; coordinates: [number, number]; kind: 'requirement' | 'capability' | 'partner' | 'tender' };
const signals: Signal[] = [
  { country: 'Ukraine', tag: 'REQUIREMENT', title: 'Components & technology', coordinates: [30.52, 50.45], kind: 'requirement' },
  { country: 'Morocco', tag: 'REQUIREMENT', title: 'Industrial requirement', coordinates: [-6.85, 33.97], kind: 'requirement' },
  { country: 'India', tag: 'REQUIREMENT', title: 'Industrial & technology need', coordinates: [77.21, 28.61], kind: 'requirement' },
  { country: 'Germany', tag: 'CAPABILITY', title: 'Manufacturer: product ready', coordinates: [13.4, 52.52], kind: 'capability' },
  { country: 'United States', tag: 'PARTNERS', title: 'Partnership opportunities', coordinates: [-77.04, 38.91], kind: 'partner' },
  { country: 'France', tag: 'TENDER', title: 'Tender opportunity', coordinates: [2.35, 48.86], kind: 'tender' },
];
const pathways = {
  have: { title: 'I HAVE SOMETHING', description: 'Tell us what you offer. Explore market access, distribution, integration and commercial opportunities.', next: 'Market Access & Sales Development' },
  need: { title: 'I NEED SOMETHING', description: 'Describe a capability, component, technology or partner you need. We can help discover potential sources.', next: 'Sourcing & Supplier Discovery' },
};
type Pathway = keyof typeof pathways;

function WorldMap({ onSelect }: { onSelect: (signal: Signal) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const selectRef = useRef(onSelect);
  useEffect(() => { selectRef.current = onSelect; }, [onSelect]);
  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: { dark: { type: 'raster', tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'], tileSize: 256, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>' } },
        layers: [{ id: 'dark', type: 'raster', source: 'dark' }]
      },
      center: [18, 28], zoom: 1.6, minZoom: 1.2, maxZoom: 7, attributionControl: false
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');
    const markers: Marker[] = signals.map(signal => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'signal-marker signal-' + signal.kind;
      el.setAttribute('aria-label', signal.country + ': ' + signal.title);
      el.title = signal.country + ': ' + signal.title;
      el.addEventListener('click', () => selectRef.current(signal));
      return new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(signal.coordinates).addTo(map);
    });
    mapRef.current = map;
    return () => { markers.forEach(m => m.remove()); map.remove(); mapRef.current = null; };
  }, []);
  return <div ref={container} className="world-map" aria-label="Interactive world map showing six illustrative market signals" />;
}

export default function App() {
  const [selected, setSelected] = useState<Signal | null>(null);
  const [pathway, setPathway] = useState<Pathway | null>(null);
  return <main className="shell">
    <header className="topbar"><a className="brand" href="#home" aria-label="Bohun Defence home"><span className="brand-symbol">⌁</span><span>BOHUN <b>DEFENCE</b></span></a><span className="top-label">THE FIELD <span className="version">/ PREVIEW 00</span></span><a className="top-contact" href="mailto:contact@bohundefence.com">CONTACT ↗</a></header>
    <section id="home" className="hero" aria-labelledby="hero-title">
      <div className="map-container"><WorldMap onSelect={setSelected} /><div className="map-vignette" aria-hidden="true" /></div>
      <div className="coordinates">GLOBAL SIGNAL ENVIRONMENT <span>·</span> ILLUSTRATIVE DATA</div>
      <div className="hero-copy"><p className="eyebrow"><span className="status-dot"/> BOHUN / FIELD INTELLIGENCE</p><h1 id="hero-title">DEFENCE REQUIREMENTS.<br/>CAPABILITIES.<br/><em>OPPORTUNITIES.</em></h1><p className="subtitle">Bohun Defence helps companies navigate defence and dual-use markets across Ukraine and internationally.</p>
        <div className="actions"><button className="primary" onClick={() => setPathway('need')}>I NEED SOMETHING <span>↗</span><small>Find capabilities, companies and partners</small></button><button className="secondary" onClick={() => setPathway('have')}>I HAVE SOMETHING <span>↗</span><small>Explore markets and commercial opportunities</small></button></div>
      </div>
      <div className="hud"><span>UKRAINE ↔ EUROPE ↔ INTERNATIONAL</span><span className="live">● 06 DEMO SIGNALS</span></div>
      {selected && <aside className="signal-panel" aria-live="polite"><button className="close" onClick={() => setSelected(null)} aria-label="Close signal">×</button><p className="eyebrow">{selected.tag} / DEMONSTRATION</p><h2>{selected.country}</h2><p>{selected.title}</p><p className="disclaimer">Illustrative scenario. Not a live request, tender, supplier or verified opportunity.</p><button className="panel-action" onClick={() => { setPathway(selected.kind === 'capability' ? 'have' : 'need'); setSelected(null); }}>EXPLORE PATHWAY ↗</button></aside>}
    </section>
    <section className="intent-strip" aria-label="Explore the field"><div><p className="eyebrow">TWO SIDES OF THE MAP</p><h2>WHAT BRINGS YOU HERE?</h2></div><div className="intent-buttons"><button onClick={() => setPathway('have')}>I HAVE SOMETHING ↗</button><button onClick={() => setPathway('need')}>I NEED SOMETHING ↗</button></div></section>
    {pathway && <div className="modal-backdrop" onClick={() => setPathway(null)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="pathway-title" onClick={e => e.stopPropagation()}><button className="close" onClick={() => setPathway(null)} aria-label="Close">×</button><p className="eyebrow">MISSION PATHWAY / 01</p><h2 id="pathway-title">{pathways[pathway].title}</h2><p>{pathways[pathway].description}</p><div className="mission-line"><span>01 DEFINE</span><span>02 EXPLORE</span><span>03 CONNECT</span></div><p className="next">SERVICE PATHWAY: {pathways[pathway].next}</p><a className="primary contact-link" href={'mailto:contact@bohundefence.com?subject=' + encodeURIComponent('THE FIELD: ' + pathways[pathway].title)}>START A CONVERSATION ↗</a><p className="disclaimer">Interactive scenario steps are planned for subsequent sprints. No live matching is performed in this preview.</p></section></div>}
    <footer>BOHUN DEFENCE <span>THE FIELD / CONCEPT BUILD 00</span><span>ILLUSTRATIVE SIGNALS ONLY</span></footer>
  </main>;
}
