// MapLibre 6 runs its parser in a module worker. Under the Vite dev server the worker file gets the dev client injected and
// never answers, so /atlas.html loads a verbatim copy from public/ instead. Re-run after updating maplibre-gl:
//   node scripts/copy-maplibre-worker.mjs
import fs from 'node:fs';
const src = new URL('../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs', import.meta.url);
const dst = new URL('../public/atlas/vendor/maplibre-gl-worker.mjs', import.meta.url);
fs.copyFileSync(src, dst);
console.log('copied maplibre-gl-worker.mjs', fs.statSync(dst).size, 'bytes');
