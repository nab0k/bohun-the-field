// Our own vector isometric map objects (09.10.2026), replacing the Kenney 3D models on the atlas.
// Style A "draftsman line" on the light maps, style C "dark brand" on the dark map (public/dev/iso-options.html shows the options).
// Every object is drawn around the centre of its footprint, so the icon anchor sits exactly on the map point.
const C = Math.cos(Math.PI / 6), S = 0.5;
const P = (x, y, z) => [(x - y) * C, (x + y) * S - z];
const pts = (list) => list.map((p) => P(...p).map((v) => v.toFixed(1)).join(',')).join(' ');
const poly = (list, fill, st) => `<polygon points="${pts(list)}" fill="${fill}"${st ? ` stroke="${st.c}" stroke-width="${st.w}" stroke-linejoin="round"` : ''}/>`;
function box(x, y, z, w, d, h, t, st) {
  return poly([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h], [x, y + d, z + h]], t.l, st) +
    poly([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]], t.r, st) +
    poly([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]], t.t, st);
}
function prism(x, y, z, w, d, h, t, st) {
  return poly([[x, y + d, z], [x + w, y + d, z], [x + w, y + d / 2, z + h], [x, y + d / 2, z + h]], t.t, st) +
    poly([[x + w, y, z], [x + w, y + d, z], [x + w, y + d / 2, z + h]], t.r, st);
}
const shadow = (w, d, c) => poly([[-2, -2, 0], [w + 4, -2, 0], [w + 4, d + 4, 0], [-2, d + 4, 0]], c);

// [footprint w, d, drawing]
export const OBJECTS = {
  factory: [40, 26, (t, a, st) => shadow(40, 26, t.sh) + box(0, 0, 0, 40, 26, 14, t, st) + prism(0, 0, 14, 13, 26, 8, t, st) + prism(13, 0, 14, 13, 26, 8, t, st) + prism(26, 0, 14, 14, 26, 8, t, st) + box(32, 3, 22, 5, 5, 18, t, st) + box(32, 3, 40, 5, 5, 2, a, st)],
  port: [46, 26, (t, a, st) => shadow(46, 26, t.sh) + box(0, 0, 0, 46, 26, 4, t, st) + box(4, 14, 4, 10, 8, 7, a, st) + box(16, 14, 4, 10, 8, 7, t, st) + box(4, 14, 11, 10, 8, 6, t, st) + box(30, 4, 4, 3, 3, 30, t, st) + box(20, 4, 34, 22, 3, 3, a, st) + box(40, 4, 22, 2, 2, 12, t, st)],
  warehouse: [46, 24, (t, a, st) => shadow(46, 24, t.sh) + box(0, 0, 0, 46, 24, 10, t, st) + prism(0, 0, 10, 46, 24, 6, t, st) + box(46, 4, 0, 1, 5, 6, a, st) + box(46, 12, 0, 1, 5, 6, a, st)],
  bank: [32, 26, (t, a, st) => shadow(32, 26, t.sh) + box(0, 0, 0, 32, 26, 3, t, st) + [3, 10, 17, 24].map((x) => box(x + 1, 22, 3, 3, 3, 14, t, st)).join('') + box(2, 2, 3, 28, 20, 14, t, st) + box(0, 0, 17, 32, 26, 3, t, st) + prism(0, 0, 20, 32, 26, 9, a, st)],
  lab: [32, 26, (t, a, st) => { const [cx, cy] = P(15, 13, 18); return shadow(32, 26, t.sh) + box(0, 0, 0, 32, 26, 12, t, st) + box(8, 6, 12, 14, 14, 6, t, st) + `<ellipse cx="${cx}" cy="${cy - 3}" rx="11" ry="9" fill="${a.t}"${st ? ` stroke="${st.c}" stroke-width="${st.w}"` : ''}/>` + box(26, 18, 12, 3, 3, 10, t, st); }],
  office: [26, 26, (t, a, st) => shadow(26, 26, t.sh) + box(0, 0, 0, 26, 26, 8, t, st) + box(4, 4, 8, 18, 18, 34, t, st) + box(4, 4, 42, 18, 18, 2, a, st) + [14, 22, 30].map((z) => box(4, 22, z, 18, 0.01, 1.4, a)).join('')],
  // mine: low shed and a pithead frame
  mine: [34, 26, (t, a, st) => shadow(34, 26, t.sh) + box(0, 8, 0, 20, 18, 8, t, st) + prism(0, 8, 8, 20, 18, 5, t, st) + box(24, 2, 0, 2, 2, 30, t, st) + box(32, 2, 0, 2, 2, 30, t, st) + box(24, 2, 30, 10, 2, 2, a, st) + box(28, 14, 0, 6, 10, 4, a, st)],
  // railway station: platform, canopy and a small hall
  station: [44, 24, (t, a, st) => shadow(44, 24, t.sh) + box(0, 0, 0, 44, 24, 2, t, st) + box(2, 2, 2, 14, 12, 12, t, st) + prism(2, 2, 14, 14, 12, 5, a, st) + box(18, 16, 2, 2, 2, 8, t, st) + box(38, 16, 2, 2, 2, 8, t, st) + box(18, 14, 10, 22, 8, 1.5, a, st)],
  // airfield: hangar with a control tower
  airfield: [44, 28, (t, a, st) => shadow(44, 28, t.sh) + box(0, 0, 0, 44, 28, 1, t, st) + box(2, 2, 1, 28, 20, 10, t, st) + prism(2, 2, 11, 28, 20, 7, t, st) + box(34, 4, 1, 6, 6, 22, t, st) + box(33, 3, 23, 8, 8, 5, a, st)],
};

export const ISO_STYLES = {
  A: { t: { t: '#f6f1df', l: '#e2dac0', r: '#cfc5a6', sh: '#0000001c' }, a: { t: '#e2b84a', l: '#c99a2e', r: '#b1861f' }, st: { c: '#3a352a', w: 0.9 } },
  C: { t: { t: '#3a3f33', l: '#2a2e25', r: '#1f221b', sh: '#00000040' }, a: { t: '#e2b84a', l: '#b58f2c', r: '#8f7020' }, st: { c: '#d9b75a', w: 0.7 } },
};

// deck.gl IconLayer icon: the anchor is the footprint centre; drawn at 2x for sharp edges
const VB = { x: -55, y: -62, w: 110, h: 92 }, K = 2;
export function isoIcon(kind, styleId = 'A') {
  const [w, d, draw] = OBJECTS[kind];
  const s = ISO_STYLES[styleId];
  const [cx, cy] = P(w / 2, d / 2, 0);
  const body = `<g transform="translate(${(-cx).toFixed(1)} ${(-cy).toFixed(1)})">${draw(s.t, s.a, s.st)}</g>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${VB.w * K}" height="${VB.h * K}" viewBox="${VB.x} ${VB.y} ${VB.w} ${VB.h}">${body}</svg>`;
  return { id: `iso-${kind}-${styleId}`, url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, width: VB.w * K, height: VB.h * K, anchorX: -VB.x * K, anchorY: -VB.y * K, mask: false };
}
