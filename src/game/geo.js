// The single geo -> world transform, implementing the contract v0.2 scene projection:
// geoConicConformal, parallels [43, 62], rotate [-22, 0, 0], center [0, 52], scale and translate from
// world-layout.json. World units equal SVG units of first-scene-base.svg (2560 x 1600, north up).
// Same maths as d3-geo's geoConicConformal; verified against the contract's city coordinates in the tests.
import layout from './data/world-layout.json' with { type: 'json' };

const P = layout.scene.projection;
const rad = Math.PI / 180;
const [phi0, phi1] = P.parallels.map((d) => d * rad);
const tany = (y) => Math.tan((Math.PI / 2 + y) / 2);
const n = Math.log(Math.cos(phi0) / Math.cos(phi1)) / Math.log(tany(phi1) / tany(phi0));
const f = (Math.cos(phi0) * Math.pow(tany(phi0), n)) / n;

function raw(lambda, phi) {
  const r = f / Math.pow(tany(phi), n);
  return [r * Math.sin(n * lambda), f - r * Math.cos(n * lambda)];
}

const [cx, cy] = raw(P.center[0] * rad, P.center[1] * rad);

export const SCENE = { width: layout.scene.width, height: layout.scene.height };

export function geoToWorld(lon, lat) {
  const [x, y] = raw((lon + P.rotate[0]) * rad, lat * rad);
  return { x: P.translate[0] + P.scale * (x - cx), y: P.translate[1] - P.scale * (y - cy) };
}
