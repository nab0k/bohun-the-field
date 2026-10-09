// Night side of the Earth with city lights (Serhii, 09.10.2026): NASA Black Marble (VIIRS, public domain) via NASA GIBS,
// shown only where it is night right now. The day/night line is computed from the sun's position and follows the clock.
// The tiles are Web Mercator, so one canvas covers the world in Mercator and MapLibre drapes it as an image source.
const Z = 3, TILE = 256, N = 2 ** Z, SIZE = TILE * N; // 2048 px world
const URL_T = (x, y) => `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/${Z}/${y}/${x}.png`;
const MAX_LAT = 85.0511;
const rad = Math.PI / 180;
const mx = (lon) => ((lon + 180) / 360) * SIZE;
const my = (lat) => { const s = Math.sin(Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * rad); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * SIZE; };

// sub-solar point (simple NOAA-style approximation, good to a fraction of a degree)
export function subsolar(date = new Date()) {
  const d = (date.getTime() / 86400000) - 10957.5; // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * rad, q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad, e = (23.439 - 0.00000036 * d) * rad;
  const dec = Math.asin(Math.sin(e) * Math.sin(L)) / rad;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)) / rad;
  const gmst = (18.697374558 + 24.06570982441908 * d) % 24;
  let lon = ra - gmst * 15; lon = ((lon + 540) % 360) - 180;
  return { lat: dec, lon };
}
// night polygon in canvas pixels: the terminator line plus the dark pole
export function nightPath(ctx, date) {
  const s = subsolar(date), dec = Math.abs(s.lat) < 0.01 ? 0.01 : s.lat;
  // drawn a little past every canvas edge, so the soft (blurred) border never fades out along the date line or the poles
  const PAD = 64;
  ctx.beginPath();
  for (let lon = -200; lon <= 200; lon += 1) {
    const lat = Math.atan(-Math.cos((lon - s.lon) * rad) / Math.tan(dec * rad)) / rad;
    lon === -200 ? ctx.moveTo(mx(lon), my(lat)) : ctx.lineTo(mx(lon), my(lat));
  }
  // northern summer (sun north): the south pole is dark, so close beyond the bottom edge; otherwise beyond the top
  const edge = dec > 0 ? SIZE + PAD : -PAD;
  ctx.lineTo(mx(200), edge); ctx.lineTo(mx(-200), edge); ctx.closePath();
}

export async function createNight() {
  const lights = document.createElement('canvas'); lights.width = lights.height = SIZE;
  const lc = lights.getContext('2d');
  const jobs = [];
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) jobs.push(new Promise((ok) => {
    const im = new Image(); im.crossOrigin = 'anonymous';
    im.onload = () => { lc.drawImage(im, x * TILE, y * TILE); ok(); }; im.onerror = () => ok();
    im.src = URL_T(x, y);
  }));
  await Promise.all(jobs);
  const out = document.createElement('canvas'); out.width = out.height = SIZE;
  const oc = out.getContext('2d');
  const mask = document.createElement('canvas'); mask.width = mask.height = SIZE;
  const mc = mask.getContext('2d');
  function draw(date = new Date()) {
    // soft-edged night mask (twilight band), then the lights image cut to it
    mc.clearRect(0, 0, SIZE, SIZE); mc.filter = 'blur(14px)'; mc.fillStyle = '#fff'; nightPath(mc, date); mc.fill(); mc.filter = 'none';
    oc.globalCompositeOperation = 'source-over'; oc.clearRect(0, 0, SIZE, SIZE);
    oc.drawImage(mask, 0, 0);
    oc.globalCompositeOperation = 'source-in'; oc.drawImage(lights, 0, 0);
    oc.globalCompositeOperation = 'source-over';
  }
  draw();
  return { canvas: out, draw, coordinates: [[-180, MAX_LAT], [180, MAX_LAT], [180, -MAX_LAT], [-180, -MAX_LAT]] };
}
