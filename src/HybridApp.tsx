import { useEffect, useRef, useState } from 'react';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import StrategyGame from './game/StrategyGame';
import type { Site } from './game/StrategyScene';
import './hybrid.css';

type Mode = 'world' | 'sector';
function GlobalMap({ onEnter }: { onEnter: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [selected, setSelected] = useState(false);
  useEffect(() => {
    if (!ref.current || map.current) return;
    const m = new maplibregl.Map({ container: ref.current, style: { version: 8, sources: { tiles: { type: 'raster', tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'], tileSize: 256, attribution: '&copy; OpenStreetMap contributors &copy; CARTO' } }, layers: [{ id: 'tiles', type: 'raster', source: 'tiles' }] }, center: [19, 37], zoom: 2.2, attributionControl: false });
    m.addControl(new maplibregl.AttributionControl({ compact: true }));
    const pin = document.createElement('button'); pin.type = 'button'; pin.className = 'hybrid-pin'; pin.textContent = 'UKRAINE ◉'; pin.setAttribute('aria-label', 'Select Ukraine demonstration sector');
    pin.addEventListener('click', () => { setSelected(true); m.flyTo({ center: [30.5, 49.2], zoom: 5, essential: true, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1200 }); });
    const marker = new maplibregl.Marker({ element: pin }).setLngLat([30.5, 49.2]).addTo(m);
    map.current = m;
    return () => { marker.remove(); m.remove(); map.current = null; };
  }, []);
  return <div className="hybrid-map-wrap"><div ref={ref} className="hybrid-map"/><div className="hybrid-map-note">01 / GEOGRAPHIC WORLD <span>DEMO SIGNALS ONLY</span></div>{selected && <div className="hybrid-enter"><p>UKRAINE / INDUSTRIAL CAPABILITY</p><h2>Enter the field</h2><span>A synthetic sector with selectable sites and commercial pathways.</span><button onClick={onEnter}>ENTER STRATEGY SCENE ↗</button></div>}</div>;
}
export default function HybridApp() {
  const [mode, setMode] = useState<Mode>('world');
  const [site, setSite] = useState<Site | null>(null);
  const [intent, setIntent] = useState(false);
  return <div className="hybrid-shell"><header className="hybrid-header"><a href="./" className="hybrid-brand">BOHUN DEFENCE <b>/ THE FIELD</b></a><span>ARCHITECTURE SPIKE P01</span><button onClick={() => { setMode('world'); setSite(null); setIntent(false); }}>← WORLD MAP</button></header><main className="hybrid-main">{mode === 'world' ? <GlobalMap onEnter={() => setMode('sector')}/> : <div className="hybrid-sector"><StrategyGame onSelect={setSite}/><div className="hybrid-help">02 / STRATEGY SECTOR <span>DRAG TO PAN · SCROLL TO ZOOM · SELECT A SITE</span></div></div>}{site && mode === 'sector' && <aside className="hybrid-site"><button className="hybrid-close" onClick={() => { setSite(null); setIntent(false); }}>×</button><p className="hybrid-kicker">CAPABILITY / ILLUSTRATIVE</p><h2>{site.label}</h2><h3>{site.kind}</h3><p>{site.description}</p><button onClick={() => setIntent(true)}>I NEED SOMETHING ↗</button>{intent && <div className="hybrid-mission"><p>MISSION / SUPPLIER DISCOVERY</p><span>Bohun Defence can help qualify a sourcing requirement and identify potential commercial partners.</span><a href="mailto:contact@bohundefence.com?subject=THE%20FIELD%20-%20Supplier%20Discovery">CONTACT BOHUN ↗</a></div>}</aside>}</main><footer className="hybrid-footer"><span>MAPLIBRE → PHASER → REACT</span><span>NO REAL REQUESTS, ROUTES OR UNIT POSITIONS</span></footer></div>;
}
