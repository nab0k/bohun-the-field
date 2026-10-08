// Scenario built from the contract v0.2 world-layout.json (scene section). City positions are the contract's
// world coordinates; geoToWorld must reproduce them (checked in the tests). Routes are schematic connections,
// NOT verified transport corridors. Cargo and the counter are fictional.
import layout from './data/world-layout.json' with { type: 'json' };
import { geoToWorld } from './geo.js';

const EN = { lviv: 'LVIV', kyiv: 'KYIV', warsaw: 'WARSAW' };
const RU = { lviv: 'Львов', kyiv: 'Киев', warsaw: 'Варшава' };

const cities = layout.scene.nodes.map((c) => ({
  id: c.id,
  label: EN[c.id],
  labelUk: c.label,
  labelRu: RU[c.id],
  kind: 'city',
  lon: c.lonLat[0],
  lat: c.lonLat[1],
  x: c.world[0],
  y: c.world[1],
  dossierId: `dossier-${c.dossierId}`,
}));

// Generic closed node, NOT in the contract and not a real place: a demonstration object for the research
// action. Position is a placeholder chosen away from the schematic routes; Codex may move it.
const site = (() => {
  const lon = 27.4, lat = 51.4;
  return { id: 'site-1', label: 'CLOSED NODE', labelRu: 'Закрытый узел', kind: 'site', initial: 'closed', lon, lat, dossierId: 'dossier-site-1', ...geoToWorld(lon, lat) };
})();

const routes = layout.edges.map((e) => ({
  id: e.id,
  fromId: e.fromId,
  toId: e.toId,
  // intermediate points only; endpoints are the cities
  path: e.pathLonLat.slice(1, -1).map(([lon, lat]) => geoToWorld(lon, lat)),
  ...(e.initiallyUnlocked ? {} : { lockedBy: site.id }),
  schematic: !!e.schematic,
}));

export const scenario = {
  id: 'first-scene-v0.2',
  scene: { width: layout.scene.width, height: layout.scene.height, background: '/map/first-scene-base.svg' },
  rules: {
    bogunSpeed: 80, // world units per second
    transportSpeed: 60,
    researchSeconds: 4,
    yieldPerShipment: 1,
    cargo: 'DEMO CARGO',
  },
  bogun: { start: 'lviv' },
  nodes: [...cities, site],
  routes,
  // Demonstration cards only (Russian explanations for the player). Approved landing copy is connected
  // during integration, not rewritten here.
  dossiers: {
    'dossier-lviv': { file: 'ДЕМО-КАРТОЧКА', title: 'Львов', body: 'Так будет выглядеть карточка места. Настоящий текст подключится позже из утверждённого копирайта.' },
    'dossier-kyiv': { file: 'ДЕМО-КАРТОЧКА', title: 'Киев', body: 'Богун прибыл. Так будет выглядеть карточка места: посетитель узнаёт, что здесь можно сделать. Настоящий текст подключится позже.' },
    'dossier-warsaw': { file: 'ДЕМО-КАРТОЧКА', title: 'Варшава', body: 'Богун прибыл по новому пути. Это демонстрационная карточка, настоящий текст подключится позже.' },
    'dossier-site-1': { file: 'ДЕМО-КАРТОЧКА', title: 'Узел открыт', body: 'Исследование завершено. Открылся путь Львов → Варшава: теперь по нему можно отправить Богуна и груз.' },
  },
};
