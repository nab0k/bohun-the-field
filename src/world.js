// Single data model for the website and the game. Rendering reads from here; nothing
// commercial is hardcoded into animation logic.

export const TILE_W = 128;
export const TILE_H = 64;
export const GRID = 26;

export const iso = (gx, gy) => ({ x: ((gx - gy) * TILE_W) / 2, y: ((gx + gy) * TILE_H) / 2 });

// emphasis: which entities light up for each visitor state
export const emphasis = {
  need: { ids: ['supplier', 'partner', 'propulsion'], routes: [['supplier', 'bohun'], ['partner', 'bohun']], caption: 'CAPABILITIES DETECTED' },
  have: { ids: ['buyer', 'ukraine', 'partner'], routes: [['bohun', 'ukraine'], ['bohun', 'buyer']], caption: 'ROUTES AVAILABLE' },
};

export const entities = [
  { id: 'bohun', type: 'hq', label: 'BOHUN', region: 'core', gx: 12, gy: 12, status: 'active', content: 'bohun' },
  { id: 'ukraine', type: 'market', label: 'UKRAINE', region: 'ukraine', gx: 18, gy: 18, status: 'active', content: 'ukraine' },
  { id: 'supplier', type: 'factory', label: 'SUPPLIER', region: 'international', gx: 8, gy: 4, status: 'active', content: 'supplier' },
  { id: 'buyer', type: 'hall', label: 'BUYER / MARKET', region: 'international', gx: 6, gy: 6, status: 'active', content: 'buyer' },
  { id: 'partner', type: 'workshop', label: 'PARTNER', region: 'international', gx: 4, gy: 8, status: 'active', content: 'partner' },
  { id: 'propulsion', type: 'signal', label: 'PROPULSION', region: 'international', gx: 10, gy: 5, status: 'active', content: 'propulsion' },
  { id: 'warehouse', type: 'warehouse', label: null, region: 'core', gx: 16, gy: 12, status: 'ambient' },
  { id: 'antenna', type: 'antenna', label: null, region: 'core', gx: 16, gy: 9, status: 'ambient' },
];

// Game Mode: hidden candidates revealed by RESEARCH on a signal. Generic on purpose.
export const candidates = [
  { id: 'cap-a', label: 'CAPABILITY A', gx: 11, gy: 3 },
  { id: 'cap-b', label: 'CAPABILITY B', gx: 14, gy: 6 },
  { id: 'cap-c', label: 'CAPABILITY C', gx: 15, gy: 9 },
];

export const roads = [
  ['supplier', 'bohun'],
  ['buyer', 'bohun'],
  ['partner', 'bohun'],
  ['bohun', 'ukraine'],
];

export const stops = [
  { fx: 0, fy: 640, z: 0.6, xf: 0.72, yf: 0.5 }, // hero overview
  { fx: 330, fy: 470, z: 1.0, xf: 0.64, yf: 0.5 }, // current interest
  { fx: 0, fy: 960, z: 0.8, xf: 0.68, yf: 0.5 }, // bohun -> ukraine
  { fx: 0, fy: 820, z: 0.55, xf: 0.5, yf: 0.5 }, // final overview
];

export const dossiers = {
  propulsion: {
    file: 'FILE / CAPABILITY',
    title: 'PROPULSION',
    body: [
      'We are interested in hearing from companies working across selected defence and dual-use capabilities.',
      'Areas of interest evolve as new projects and requirements emerge.',
    ],
    status: 'ACTIVE',
    cta: 'INTRODUCE YOUR CAPABILITY →',
  },
  bohun: {
    file: 'FILE / BOHUN',
    title: 'BOHUN DEFENCE',
    body: ['Bohun Defence helps companies navigate defence and dual-use markets across Ukraine and internationally.'],
    status: 'ACTIVE',
    cta: 'START A CONVERSATION →',
  },
  supplier: {
    file: 'FILE / CAPABILITY',
    title: 'I NEED SOMETHING',
    body: [
      'Looking for a technology, manufacturer, supplier, industrial capability or the right partner?',
      'We research the field, identify relevant organisations and help establish the right conversations.',
    ],
    status: 'ACTIVE',
    cta: 'EXPLORE CAPABILITIES →',
  },
  buyer: {
    file: 'FILE / MARKET',
    title: 'I HAVE SOMETHING',
    body: [
      'Have a product, technology or capability and need a route into a new market?',
      'We help you understand the landscape, find relevant partners and explore practical routes to commercial engagement.',
    ],
    status: 'ACTIVE',
    cta: 'EXPLORE MARKETS →',
  },
  partner: {
    file: 'FILE / PARTNER',
    title: 'PARTNERSHIP DEVELOPMENT',
    body: ['Identify and assess potential technology, industrial and commercial partners and help establish productive relationships.'],
    status: 'ACTIVE',
    cta: 'START A CONVERSATION →',
  },
  ukraine: {
    file: 'FILE / MARKET',
    title: 'INTO UKRAINE',
    body: ['Market orientation', 'Partner identification', 'Industrial opportunities', 'Testing & validation pathways', 'Tender & procurement landscape', 'Local commercial context'],
    list: true,
    status: 'ACTIVE',
    cta: 'START A CONVERSATION →',
  },
};
