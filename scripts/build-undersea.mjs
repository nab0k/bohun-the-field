// Thematic layer for /atlas.html: companies from Serhii Nabok's article "Undersea Infrastructure Defense: Startup Landscape"
// (LinkedIn, 2 Jan 2026). The article gives names, sections and what each company does, but no cities, so the headquarters
// city was researched on 08.10.2026 (one source link per company, kept in the output). City centres are geocoded with
// OpenStreetMap Nominatim (one request per second). Where sources disagree, the note says so.
// Usage: node scripts/build-undersea.mjs
import fs from 'node:fs';

const ARTICLE = { title: 'Undersea Infrastructure Defense: Startup Landscape', author: 'Serhii Nabok', date: '2026-01-02', url: 'https://www.linkedin.com/pulse/undersea-infrastructure-defense-startup-landscape-serhii-nabok-60vqe/' };
// [name, section, what (ru), city query for geocoding, HQ source, note]
const LIST = [
  ['Unseenlabs', 'detect', 'обнаружение судов по радиосигналам из космоса', 'Rennes, France', 'https://www.wikidata.org/wiki/Special:Search?search=Unseenlabs'],
  ['HawkEye 360', 'detect', 'геолокация радиосигналов из космоса и аналитика', 'Herndon, Virginia, USA', 'https://intelligencecommunitynews.com/hawkeye-360-celebrates-opening-of-virginia-hq/'],
  ['Windward', 'detect', 'морской ИИ: риски, санкции, аномалии', 'London, UK', 'https://yespress.io/windward.md', 'В ранних профилях — Тель-Авив; после покупки FTV Capital (2025) указан Лондон.'],
  ['MarineLabs', 'detect', 'буи с датчиками и прибрежная разведка для портов', 'Victoria, British Columbia, Canada', 'https://members.viatec.ca/news/Details/marinelabs-celebrates-grand-opening-of-new-hq-301561'],
  ['Andrenam', 'detect', 'распределённая подводная акустическая сеть с ИИ', 'Hawthorne, California, USA', 'https://www.tectonicdefense.com/exclusive-underwater-sensing-startup-andrenam-raises-18m/'],
  ['Spear AI', 'detect', 'ИИ-анализ подводной акустики', 'Washington, D.C., USA', 'https://app.dealroom.co/companies/spear_ai'],
  ['Indeximate', 'detect', 'мониторинг подводных силовых кабелей', 'Hinckley, Leicestershire, UK', 'https://www.premieralts.com/companies/indeximate'],
  ['Optics11', 'detect', 'оптоволоконные датчики и обнаружение угроз', 'Amsterdam, Netherlands', 'https://www.cbinsights.com/company/optics11'],
  ['FEBUS Optics', 'detect', 'распределённые оптоволоконные датчики для протяжённых объектов', 'Pau, France', 'https://gican.asso.fr/en/annuaire-des-adherents-du-gican-lindustrie-navale-francaise-en/febus-optics/'],
  ['Orca AI', 'detect', 'компьютерное зрение для судов, предотвращение столкновений', 'London, UK', 'https://www.cbinsights.com/company/orca-ai', 'В ранних профилях — Тель-Авив; в свежих источниках Лондон.'],
  ['Seadronix', 'detect', 'ИИ-ситуационная осведомлённость и автономная навигация судов', 'Ulsan, South Korea', 'https://craft.co/seadronix'],
  ['Echodyne', 'detect', 'компактные радары на метаматериалах', 'Kirkland, Washington, USA', 'https://www.cbinsights.com/company/echodyne'],
  ['ELWAVE', 'detect', 'электрополевые датчики для подводных роботов и объектов', 'Carquefou, France', 'https://www.wikidata.org/wiki/Special:Search?search=Elwave'],
  ['Starboard Maritime Intelligence', 'detect', 'мультисенсорная морская осведомлённость, «тёмные» суда', 'Wellington, New Zealand', 'https://www.boatingnz.co.nz/2026/01/prime-minister-opens-starboard-maritime-intelligences-new-wellington-headquarters/'],
  ['Ocean Power Technologies', 'detect', 'буи на энергии волн и объединение морских датчиков', 'Pennington, New Jersey, USA', 'https://www.wikidata.org/wiki/Special:Search?search=Ocean+Power+Technologies'],
  ['Aragon Photonics', 'detect', 'оптоволоконные датчики и ИИ-анализ сигналов', 'Zaragoza, Spain', 'https://www.gophotonics.com/companies-amp/667/aragon-photonics'],
  ['Saildrone', 'patrol', 'автономные надводные аппараты большой автономности', 'Alameda, Alameda County, California, United States', 'https://www.wikidata.org/wiki/Special:Search?search=Saildrone'],
  ['Open Ocean Robotics', 'patrol', 'надводные дроны на солнечной энергии', 'Victoria, British Columbia, Canada', 'https://openoceanrobotics.com/contact'],
  ['Seasats', 'patrol', 'малые автономные надводные аппараты', 'San Diego, California, USA', 'https://oceannews.com/news/science-technology/seasats-autonomous-surface-vessel-crosses-the-pacific/'],
  ['Saronic Technologies', 'patrol', 'автономные военные катера с модульной нагрузкой', 'Austin, Texas, USA', 'https://www.builtinaustin.com/company/saronic'],
  ['Blue Water Autonomy', 'patrol', 'серийные беспилотные океанские корабли', 'Boston, Massachusetts, USA', 'https://www.builtinboston.com/company/blue-water-autonomy'],
  ['Ocean Aero', 'patrol', 'гибридный надводно-подводный автономный аппарат', 'San Diego, California, USA', 'https://www.robotics.press/news/ocean-aero-company-profile/', 'В статье — «Ocean Aerospace»; найдена компания Ocean Aero с этим описанием. Производство — Галфпорт (Миссисипи).'],
  ['Sea Machines Robotics', 'patrol', 'автономия для коммерческих и государственных судов', 'Boston, Massachusetts, USA', 'https://en.wikipedia.org/wiki/Sea_Machines_Robotics'],
  ['SubSeaSail', 'patrol', 'полупогружные автономные аппараты с добычей энергии', 'San Diego, California, USA', 'https://www.marinetechnologynews.com/news/subseasail-receives-national-security-641395'],
  ['Vatn Systems', 'patrol', 'серийные автономные подводные аппараты', 'Portsmouth, Rhode Island, USA', 'https://www.cbinsights.com/company/vatn-systems', 'Производство — Бристоль (Род-Айленд).'],
  ['XOCEAN', 'inspect', 'беспилотные суда для гидрографии и инспекции', 'Carlingford, County Louth, Ireland', 'https://www.eenewseurope.com/en/xocean-opens-usv-technical-centre-in-ireland', 'Техцентр в Ратхоре у Карлингфорда (графство Лаут).'],
  ['Planys Technologies', 'inspect', 'подводная робототехника и ИИ-инспекция в 3D', 'Chennai, India', 'https://indiaai.gov.in/article/how-this-chennai-based-startup-is-pioneering-underwater-ai-based-rovs'],
  ['Tethys Robotics', 'inspect', 'автономный подводный робот для инспекций', 'Zurich, Switzerland', 'https://www.venturelab.swiss/Tethys-Robotics-advances-subsea-automation-with-EUR-35-million-preseed-round'],
  ['subdron', 'inspect', 'навигация подводных аппаратов и обработка данных инспекций', 'Lauterach, Austria', 'https://craft.co/subdron'],
  ['Bedrock Ocean Exploration', 'inspect', 'данные о морском дне с автономных аппаратов', 'Richmond, California, USA', 'https://www.cbinsights.com/company/bedrock-ocean-exploration', 'Есть офисы в Ричмонде (Калифорния) и Бруклине; источники расходятся, где штаб-квартира.'],
  ['Terradepth', 'inspect', 'флоты подводных аппаратов и данные «от дна до облака»', 'Cedar Park, Texas, USA', 'https://www.cbinsights.com/company/sea-proven/people', 'Часть источников указывает Остин.'],
  ['Dive Technologies', 'inspect', 'крупные автономные подводные аппараты (куплена Anduril)', 'Quincy, Massachusetts, USA', 'https://www.defenseone.com/business/2022/02/anduril-buys-robotic-submarine-maker-dive-technologies/361497/'],
  ['Riptide Autonomous Solutions', 'inspect', 'малые подводные аппараты для съёмки (активы у BAE Systems)', 'Plymouth, Massachusetts, USA', 'https://www.baesystems.com/en-us/article/acquisition-of-the-riptide-autonomous-solutions'],
  ['ScrubMarine', 'inspect', 'роботизированная очистка и инспекция корпусов', 'Whitehaven, England', 'https://www.vcbacked.co/company/scrubmarine'],
  ['cosma', 'inspect', 'подводные дроны с ИИ-съёмкой в 3D', 'Nice, France', 'https://tech.eu/2025/06/30/cosma-raises-eur25m-to-map-deep-sea-ecosystems-with-autonomous-robots/'],
  ['PxGeo', 'inspect', 'морская геофизика на автономных системах Sabertooth', 'Dubai, United Arab Emirates', 'https://www.saab.com/newsroom/press-releases/2023/saab-receives-order-for-sabertooth'],
  ['Impossible Metals', 'inspect', 'глубоководная робототехника', 'San Jose, California, USA', 'https://www.miningweekly.com/article/us-autonomous-ocean-miner-plans-advanced-marine-robotics-hub-in-pittsburgh-2026-07-15', 'Источники расходятся: Сан-Хосе или Пасадина; с 2026 года центр робототехники в Питтсбурге.'],
  ['Orpheus Ocean', 'inspect', 'автономный аппарат для любых глубин', 'New Bedford, Massachusetts, USA', 'https://newbedfordlight.org/opinion-deep-sea-robot-orpheus-calls-new-bedford-home/'],
  ['Greensea IQ', 'repair', 'амфибийные подводные наземные аппараты (Bayonet 250)', 'Richmond, Vermont, USA', 'https://www.cbinsights.com/company/greensea-systems'],
  ['Kongsberg Ferrotech', 'repair', 'роботы для подводной инспекции и ремонта', 'Kongsberg, Norway', 'https://businessnorway.com/companies/kongsberg-ferrotech-as'],
  ['Eelume', 'repair', 'постоянно живущие под водой роботы для инспекции и работ', 'Trondheim, Norway', 'https://en.wikipedia.org/wiki/Eelume'],
  ['CyberRidge', 'secure', 'фотонное шифрование оптоволоконных линий', 'Tel Aviv, Israel', 'https://www.securityweek.com/cyberridge-emerges-from-stealth-with-26-million-for-photonic-encryption-solution/'],
  ['CSignum', 'secure', 'беспроводная связь через воду для подводных датчиков', 'Bathgate, West Lothian, UK', 'https://linkedin.com/company/csignum'],
  ['Absolute Ocean Platform', 'secure', 'облачная платформа данных морского дна (часть Terradepth)', 'Cedar Park, Texas, USA', 'https://www.cbinsights.com/company/sea-proven/people', 'Продукт Terradepth: стоит в той же точке.'],
];
const UNPLACED = [
  ['Lume', 'detect', 'оптоволоконные датчики и ИИ для подводного мониторинга', 'Похожая компания — Lumetec (Пасадина); совпадение не подтверждено.'],
  ['ROVing Intelligence', 'inspect', 'инспекция подводных объектов с ROV', 'Австралия; город не найден.'],
  ['Unmanned Dynamics', 'secure', 'буи подводного позиционирования AquaGrid и акустические модемы', 'Регистрация в Сингапуре и, возможно, Литве; штаб-квартира не подтверждена.'],
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
