// Programmer-art, original: everything is drawn with vector primitives. No third-party assets.
import { TILE_W, TILE_H, GRID, iso } from './world.js';

export const COLOR = {
  gold: 0xc9a24a,
  goldDark: 0x8d6a28,
  paper: 0xf1efe8,
  ink: 0x0e1210,
  road: 0x9c8c63,
  roadEdge: 0x6b6044,
};

const HW = TILE_W / 2;
const HH = TILE_H / 2;

function hash(x, y) {
  let h = (x * 374761393 + y * 668265263) ^ (x * y * 1442695041);
  h = (h ^ (h >>> 13)) * 1274126177;
  return (((h ^ (h >>> 16)) >>> 0) % 100000) / 100000;
}

function mix(a, b, t) {
  const ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255;
  const br = (b >> 16) & 255, bg = (b >> 8) & 255, bb = b & 255;
  return (Math.round(ar + (br - ar) * t) << 16) | (Math.round(ag + (bg - ag) * t) << 8) | Math.round(ab + (bb - ab) * t);
}

const TONES = {
  europe: [0x3b4a3d, 0x465646],
  core: [0x44503a, 0x516041],
  ukraine: [0x55583a, 0x66673f],
};

export function drawTerrain(g) {
  for (let gy = 0; gy < GRID; gy++) {
    for (let gx = 0; gx < GRID; gx++) {
      const { x, y } = iso(gx, gy);
      const zone = gy < 8 ? 'europe' : gy > 15 ? 'ukraine' : 'core';
      const t = hash(gx, gy);
      const col = mix(TONES[zone][0], TONES[zone][1], t);
      const pts = [{ x, y: y - HH }, { x: x + HW, y }, { x, y: y + HH }, { x: x - HW, y }];
      g.fillStyle(col, 1);
      g.fillPoints(pts, true);
      g.lineStyle(1, 0x000000, 0.1);
      g.strokePoints(pts, true);
    }
  }
}

export function polyline(g, pts) {
  g.strokePoints(pts, false, false);
}

export function drawRoad(g, pts) {
  g.lineStyle(18, COLOR.roadEdge, 1);
  polyline(g, pts);
  g.lineStyle(13, COLOR.road, 1);
  polyline(g, pts);
}

// iso box; (x, y) is the centre of the ground footprint
export function box(g, x, y, w, h, top, left, right) {
  const hw = w / 2;
  const hh = w / 4;
  g.fillStyle(left, 1);
  g.fillPoints([{ x: x - hw, y: y - h }, { x, y: y - h + hh }, { x, y: y + hh }, { x: x - hw, y }], true);
  g.fillStyle(right, 1);
  g.fillPoints([{ x: x + hw, y: y - h }, { x, y: y - h + hh }, { x, y: y + hh }, { x: x + hw, y }], true);
  g.fillStyle(top, 1);
  g.fillPoints([{ x, y: y - h - hh }, { x: x + hw, y: y - h }, { x, y: y - h + hh }, { x: x - hw, y: y - h }], true);
  g.lineStyle(1, 0x0b0e0c, 0.55);
  g.strokePoints([{ x: x - hw, y: y - h }, { x, y: y - h + hh }, { x: x + hw, y: y - h }], false, false);
  g.strokePoints([{ x, y: y - h + hh }, { x, y: y + hh }], false, false);
}

function pyramid(g, x, y, w, h, left, right) {
  const hw = w / 2;
  const hh = w / 4;
  g.fillStyle(left, 1);
  g.fillPoints([{ x: x - hw, y }, { x, y: y + hh }, { x, y: y - h }], true);
  g.fillStyle(right, 1);
  g.fillPoints([{ x: x + hw, y }, { x, y: y + hh }, { x, y: y - h }], true);
}

function bow(g, x, y, size, color, width) {
  // original abstract bow-and-arrow mark, not a copy of any asset
  g.lineStyle(width, color, 1);
  const pts = [];
  for (let i = 0; i <= 14; i++) {
    const a = Math.PI * 0.12 + (i / 14) * Math.PI * 0.76;
    pts.push({ x: x + Math.cos(a) * size * 1.1, y: y - Math.sin(a) * size * 0.55 + size * 0.2 });
  }
  g.strokePoints(pts, false, false);
  g.strokePoints([{ x, y: y - size * 0.9 }, { x, y: y + size * 0.55 }], false, false);
  g.fillStyle(color, 1);
  g.fillPoints([{ x: x - 4, y: y + size * 0.55 }, { x: x + 4, y: y + size * 0.55 }, { x, y: y + size * 0.55 + 8 }], true);
}

export function drawHQ(g) {
  box(g, 0, 0, 132, 40, 0xb9ad8a, 0x8f8567, 0x756d54);
  box(g, 0, -40, 84, 30, 0xc4b894, 0x978d70, 0x7c7459);
  pyramid(g, 0, -70, 84, 34, 0xb48a38, 0x8d6a28);
  // pole + flag
  g.lineStyle(2, 0x2a2e2c, 1);
  g.strokePoints([{ x: 0, y: -104 }, { x: 0, y: -160 }], false, false);
  g.fillStyle(COLOR.gold, 1);
  g.fillPoints([{ x: 0, y: -160 }, { x: 24, y: -153 }, { x: 0, y: -146 }], true);
  bow(g, 0, -124, 14, COLOR.gold, 2.5);
}

export function drawFactory(g) {
  box(g, -8, 0, 96, 34, 0x8d98a0, 0x69737b, 0x535c63);
  box(g, 26, -6, 46, 26, 0x9aa5ad, 0x75808a, 0x5d676f);
  box(g, 38, -26, 14, 54, 0x4b5156, 0x353a3e, 0x2b2f32);
}

export function drawHall(g) {
  box(g, 0, 0, 100, 30, 0xa89a72, 0x7f7355, 0x665c44);
  box(g, 0, -30, 86, 10, 0x4a6a96, 0x37506f, 0x2d4259);
  g.lineStyle(2, 0x2a2e2c, 1);
  g.strokePoints([{ x: 30, y: -48 }, { x: 30, y: -92 }], false, false);
  g.fillStyle(0xe3c33a, 1);
  g.fillPoints([{ x: 30, y: -92 }, { x: 50, y: -86 }, { x: 30, y: -80 }], true);
}

export function drawWorkshop(g) {
  box(g, -26, 6, 70, 26, 0x8a936b, 0x69714f, 0x545b3f);
  box(g, 26, -4, 56, 36, 0x96a075, 0x737c58, 0x5c6446);
}

export function drawMarket(g) {
  box(g, 0, 0, 120, 26, 0xc7a85e, 0x9c8344, 0x7f6a37);
  box(g, 0, -26, 100, 8, 0xd9bf78, 0xae9556, 0x927c45);
  g.lineStyle(2, 0x2a2e2c, 1);
  g.strokePoints([{ x: 44, y: -40 }, { x: 44, y: -110 }], false, false);
  g.fillStyle(0x2f67b5, 1);
  g.fillRect(44, -110, 34, 10);
  g.fillStyle(0xe3c33a, 1);
  g.fillRect(44, -100, 34, 10);
}

export function drawWarehouse(g) {
  box(g, 0, 0, 130, 24, 0x9a9f98, 0x767b74, 0x5d625c);
}

export function drawAntennaMast(g) {
  g.lineStyle(2, 0x3a4044, 1);
  g.strokePoints([{ x: -10, y: 0 }, { x: 0, y: -90 }, { x: 10, y: 0 }], false, false);
  g.strokePoints([{ x: -7, y: -28 }, { x: 7, y: -28 }], false, false);
  g.strokePoints([{ x: -5, y: -56 }, { x: 5, y: -56 }], false, false);
  box(g, 0, 4, 36, 6, 0x6a7176, 0x4d5357, 0x3d4246);
}

export function drawSignalMarker(g) {
  g.fillStyle(COLOR.gold, 1);
  g.fillPoints([{ x: 0, y: -50 }, { x: 14, y: -36 }, { x: 0, y: -22 }, { x: -14, y: -36 }], true);
  g.lineStyle(2, COLOR.gold, 0.9);
  g.strokePoints([{ x: 0, y: -22 }, { x: 0, y: 8 }], false, false);
  g.fillStyle(COLOR.gold, 0.35);
  g.fillEllipse(0, 8, 50, 25);
}

export function drawCandidate(g) {
  box(g, 0, 0, 56, 18, 0xb7b0a0, 0x8c8678, 0x706b5f);
  g.fillStyle(COLOR.gold, 0.35);
  g.fillEllipse(0, 6, 82, 41);
}

export function drawTree(g, x, y, s) {
  g.fillStyle(0x2f2a22, 1);
  g.fillRect(x - 1.5, y - 8 * s, 3, 8 * s);
  g.fillStyle(0x2c4a31, 1);
  g.fillEllipse(x, y - 17 * s, 20 * s, 17 * s);
  g.fillStyle(0x375b3b, 1);
  g.fillEllipse(x - 2 * s, y - 20 * s, 12 * s, 10 * s);
}

export function drawUnit(g, kind) {
  if (kind === 'truck') {
    box(g, 0, 0, 30, 9, 0x6f7a55, 0x535c3f, 0x434a33);
    box(g, 11, 0, 12, 13, 0x8d9870, 0x6a7452, 0x535b40);
  } else {
    // person: body, head, gold cap
    g.fillStyle(0x1c2220, 1);
    g.fillEllipse(0, 1, 12, 6);
    g.fillStyle(0x3e4d63, 1);
    g.fillRect(-3, -11, 6, 11);
    g.fillStyle(0xd6b898, 1);
    g.fillCircle(0, -14, 3.2);
    g.fillStyle(COLOR.gold, 1);
    g.fillRect(-3.4, -18, 6.8, 3);
  }
}
