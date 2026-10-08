// Thematic layer for /atlas.html: companies from Serhii Nabok's article "Undersea Infrastructure Defense: Startup Landscape"
// (LinkedIn, 2 Jan 2026). The article gives names, sections and what each company does, but no cities, so the headquarters
// city was researched on 08.10.2026 (one source link per company, kept in the output). City centres are geocoded with
// OpenStreetMap Nominatim (one request per second). Where sources disagree, the note says so.
// Usage: node scripts/build-undersea.mjs
import fs from 'node:fs';

const ARTICLE = { title: 'Undersea Infrastructure Defense: Startup Landscape', author: 'Serhii Nabok', date: '2026-01-02', url: 'https://www.linkedin.com/pulse/undersea-infrastructure-defense-startup-landscape-serhii-nabok-60vqe/' };
// [name, section, what (ru), city query for geocoding, HQ source, note]
const LIST = [
  ['Unseenlabs', 'detect', 'space-based RF detection of vessels', 'Rennes, France', 'https://www.wikidata.org/wiki/Special:Search?search=Unseenlabs'],
  ['HawkEye 360', 'detect', 'space-based RF geolocation and analytics', 'Herndon, Virginia, USA', 'https://intelligencecommunitynews.com/hawkeye-360-celebrates-opening-of-virginia-hq/'],
  ['Windward', 'detect', 'maritime AI for risk, sanctions and anomalies', 'London, UK', 'https://yespress.io/windward.md', 'Older profiles say Tel Aviv; London since the FTV Capital acquisition (2025).'],
  ['MarineLabs', 'detect', 'sensor buoys and coastal intelligence for ports', 'Victoria, British Columbia, Canada', 'https://members.viatec.ca/news/Details/marinelabs-celebrates-grand-opening-of-new-hq-301561'],
  ['Andrenam', 'detect', 'distributed underwater acoustic sensing with AI', 'Hawthorne, California, USA', 'https://www.tectonicdefense.com/exclusive-underwater-sensing-startup-andrenam-raises-18m/'],
  ['Spear AI', 'detect', 'AI analytics for underwater acoustics', 'Washington, D.C., USA', 'https://app.dealroom.co/companies/spear_ai'],
  ['Indeximate', 'detect', 'monitoring of subsea power cables', 'Hinckley, Leicestershire, UK', 'https://www.premieralts.com/companies/indeximate'],
  ['Optics11', 'detect', 'fibre-optic sensing and threat detection', 'Amsterdam, Netherlands', 'https://www.cbinsights.com/company/optics11'],
  ['FEBUS Optics', 'detect', 'distributed fibre sensing for linear infrastructure', 'Pau, France', 'https://gican.asso.fr/en/annuaire-des-adherents-du-gican-lindustrie-navale-francaise-en/febus-optics/'],
  ['Orca AI', 'detect', 'AI vision for ships, collision avoidance', 'London, UK', 'https://www.cbinsights.com/company/orca-ai', 'Older profiles say Tel Aviv; recent sources say London.'],
  ['Seadronix', 'detect', 'AI situational awareness and autonomous navigation for ships', 'Ulsan, South Korea', 'https://craft.co/seadronix'],
  ['Echodyne', 'detect', 'compact metamaterial radar', 'Kirkland, Washington, USA', 'https://www.cbinsights.com/company/echodyne'],
  ['ELWAVE', 'detect', 'electric-field sensing for underwater robots and infrastructure', 'Carquefou, France', 'https://www.wikidata.org/wiki/Special:Search?search=Elwave'],
  ['Starboard Maritime Intelligence', 'detect', 'multisensor maritime awareness, dark-vessel detection', 'Wellington, New Zealand', 'https://www.boatingnz.co.nz/2026/01/prime-minister-opens-starboard-maritime-intelligences-new-wellington-headquarters/'],
  ['Ocean Power Technologies', 'detect', 'wave-powered buoys and maritime sensor fusion', 'Pennington, New Jersey, USA', 'https://www.wikidata.org/wiki/Special:Search?search=Ocean+Power+Technologies'],
  ['Aragon Photonics', 'detect', 'fibre sensing and AI signal analysis', 'Zaragoza, Spain', 'https://www.gophotonics.com/companies-amp/667/aragon-photonics'],
  ['Saildrone', 'patrol', 'long-endurance uncrewed surface vehicles', 'Alameda, Alameda County, California, United States', 'https://www.wikidata.org/wiki/Special:Search?search=Saildrone'],
  ['Open Ocean Robotics', 'patrol', 'solar-powered uncrewed surface vehicles', 'Victoria, British Columbia, Canada', 'https://openoceanrobotics.com/contact'],
  ['Seasats', 'patrol', 'small autonomous surface vehicles', 'San Diego, California, USA', 'https://oceannews.com/news/science-technology/seasats-autonomous-surface-vessel-crosses-the-pacific/'],
  ['Saronic Technologies', 'patrol', 'autonomous naval vessels with modular payloads', 'Austin, Texas, USA', 'https://www.builtinaustin.com/company/saronic'],
  ['Blue Water Autonomy', 'patrol', 'producible uncrewed ocean-going ships', 'Boston, Massachusetts, USA', 'https://www.builtinboston.com/company/blue-water-autonomy'],
  ['Ocean Aero', 'patrol', 'hybrid surface/subsurface autonomous platform', 'San Diego, California, USA', 'https://www.robotics.press/news/ocean-aero-company-profile/', 'The article says “Ocean Aerospace”; the matching company is Ocean Aero. Manufacturing in Gulfport, Mississippi.'],
  ['Sea Machines Robotics', 'patrol', 'autonomy for commercial and government vessels', 'Boston, Massachusetts, USA', 'https://en.wikipedia.org/wiki/Sea_Machines_Robotics'],
  ['SubSeaSail', 'patrol', 'energy-harvesting semi-submersible autonomous vehicles', 'San Diego, California, USA', 'https://www.marinetechnologynews.com/news/subseasail-receives-national-security-641395'],
  ['Vatn Systems', 'patrol', 'mass-producible autonomous underwater vehicles', 'Portsmouth, Rhode Island, USA', 'https://www.cbinsights.com/company/vatn-systems', 'Manufacturing in Bristol, Rhode Island.'],
  ['XOCEAN', 'inspect', 'uncrewed vessels for hydrographic survey and inspection', 'Carlingford, County Louth, Ireland', 'https://www.eenewseurope.com/en/xocean-opens-usv-technical-centre-in-ireland', 'Technical centre at Rathcor near Carlingford, County Louth.'],
  ['Planys Technologies', 'inspect', 'underwater robotics with AI 3D inspection', 'Chennai, India', 'https://indiaai.gov.in/article/how-this-chennai-based-startup-is-pioneering-underwater-ai-based-rovs'],
  ['Tethys Robotics', 'inspect', 'autonomous underwater robot for inspections', 'Zurich, Switzerland', 'https://www.venturelab.swiss/Tethys-Robotics-advances-subsea-automation-with-EUR-35-million-preseed-round'],
  ['subdron', 'inspect', 'underwater navigation and inspection data processing', 'Lauterach, Austria', 'https://craft.co/subdron'],
  ['Bedrock Ocean Exploration', 'inspect', 'seafloor data from autonomous vehicles', 'Richmond, California, USA', 'https://www.cbinsights.com/company/bedrock-ocean-exploration', 'Offices in Richmond, California, and Brooklyn; sources differ on which is the headquarters.'],
  ['Terradepth', 'inspect', 'AUV fleets and seabed-to-cloud data', 'Cedar Park, Texas, USA', 'https://www.cbinsights.com/company/sea-proven/people', 'Some sources say Austin.'],
  ['Dive Technologies', 'inspect', 'large autonomous underwater vehicles (acquired by Anduril)', 'Quincy, Massachusetts, USA', 'https://www.defenseone.com/business/2022/02/anduril-buys-robotic-submarine-maker-dive-technologies/361497/'],
  ['Riptide Autonomous Solutions', 'inspect', 'small AUVs for survey (assets now with BAE Systems)', 'Plymouth, Massachusetts, USA', 'https://www.baesystems.com/en-us/article/acquisition-of-the-riptide-autonomous-solutions'],
  ['ScrubMarine', 'inspect', 'robotic hull cleaning and inspection', 'Whitehaven, England', 'https://www.vcbacked.co/company/scrubmarine'],
  ['cosma', 'inspect', 'underwater drones with AI 3D imaging', 'Nice, France', 'https://tech.eu/2025/06/30/cosma-raises-eur25m-to-map-deep-sea-ecosystems-with-autonomous-robots/'],
  ['PxGeo', 'inspect', 'marine geophysics with Sabertooth autonomous systems', 'Dubai, United Arab Emirates', 'https://www.saab.com/newsroom/press-releases/2023/saab-receives-order-for-sabertooth'],
  ['Impossible Metals', 'inspect', 'deep-water robotics', 'San Jose, California, USA', 'https://www.miningweekly.com/article/us-autonomous-ocean-miner-plans-advanced-marine-robotics-hub-in-pittsburgh-2026-07-15', 'Sources differ between San Jose and Pasadena; a robotics hub in Pittsburgh since 2026.'],
  ['Orpheus Ocean', 'inspect', 'full-ocean-depth autonomous vehicle', 'New Bedford, Massachusetts, USA', 'https://newbedfordlight.org/opinion-deep-sea-robot-orpheus-calls-new-bedford-home/'],
  ['Greensea IQ', 'repair', 'amphibious underwater ground vehicles (Bayonet 250)', 'Richmond, Vermont, USA', 'https://www.cbinsights.com/company/greensea-systems'],
  ['Kongsberg Ferrotech', 'repair', 'subsea inspection and repair robotics', 'Kongsberg, Norway', 'https://businessnorway.com/companies/kongsberg-ferrotech-as'],
  ['Eelume', 'repair', 'subsea resident robots for inspection and intervention', 'Trondheim, Norway', 'https://en.wikipedia.org/wiki/Eelume'],
  ['CyberRidge', 'secure', 'photonic-layer encryption for fibre links', 'Tel Aviv, Israel', 'https://www.securityweek.com/cyberridge-emerges-from-stealth-with-26-million-for-photonic-encryption-solution/'],
  ['CSignum', 'secure', 'through-water wireless communications for subsea sensors', 'Bathgate, West Lothian, UK', 'https://linkedin.com/company/csignum'],
  ['Absolute Ocean Platform', 'secure', 'cloud platform for seabed data (part of Terradepth)', 'Cedar Park, Texas, USA', 'https://www.cbinsights.com/company/sea-proven/people', 'A Terradepth product: shown at the same point.'],
];
const UNPLACED = [
  ['Lume', 'detect', 'fibre sensing and AI for subsea monitoring', 'A similar company is Lumetec (Pasadena); the match is not confirmed.'],
  ['ROVing Intelligence', 'inspect', 'ROV-based underwater asset inspection', 'Australia; city not found.'],
  ['Unmanned Dynamics', 'secure', 'AquaGrid underwater positioning buoys and acoustic modems', 'Registered in Singapore and possibly Lithuania; headquarters not confirmed.'],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cache = new Map();
async function geocode(q) {
  if (cache.has(q)) return cache.get(q);
  await sleep(1100);
  const r = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`, { headers: { 'User-Agent': 'BohunFieldPrototype/0.1 (research script)' } });
  const j = await r.json();
  const hit = j[0] ? { lon: +(+j[0].lon).toFixed(3), lat: +(+j[0].lat).toFixed(3) } : null;
  cache.set(q, hit); return hit;
}
const out = [], failed = [];
for (const [name, section, about, city, source, note] of LIST) {
  const g = await geocode(city);
  if (!g) { failed.push(name); continue; }
  out.push({ name, section, about, city: city.split(',')[0], place: city, ...g, source, ...(note ? { note } : {}) });
}
const file = new URL('../public/atlas/undersea.json', import.meta.url);
fs.writeFileSync(file, JSON.stringify({
  generator: 'scripts/build-undersea.mjs', retrieved: new Date().toISOString().slice(0, 10), article: ARTICLE,
  method: 'Names, sections and descriptions from the article; headquarters city researched per company (source link each); city centres from OpenStreetMap Nominatim.',
  notice: 'Open data. Not clients or partners of Bohun.', companies: out,
  unplaced: UNPLACED.map(([name, section, about, note]) => ({ name, section, about, note })),
}, null, 1));
console.log(JSON.stringify({ placed: out.length, unplaced: UNPLACED.length, failed }));
