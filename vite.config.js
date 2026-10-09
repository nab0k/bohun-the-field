import { defineConfig } from 'vite';

// Entry pages: landing draft (index.html), game module demo (game.html), flows prototype (flows.html), v0.4 field (field.html).
// Dev-only proxy for the LIVE layer (src/atlas/live.js): these APIs either send no CORS headers or ask for an identifying header.
// In production the same /live/* paths will be served by the Cloudflare Worker.
const live = (target, headers = {}) => ({ target, changeOrigin: true, headers: { 'User-Agent': 'bohundefence-atlas-prototype', ...headers }, rewrite: (p) => p.replace(/^\/live\/[a-z]+/, '') });
export default defineConfig({
  server: {
    proxy: {
      '/live/adsb': live('https://api.adsb.lol'),
      '/live/ais': live('https://meri.digitraffic.fi', { 'Digitraffic-User': 'bohundefence/atlas-prototype' }),
      '/live/rail': live('https://rata.digitraffic.fi', { 'Digitraffic-User': 'bohundefence/atlas-prototype' }),
    },
  },
  build: {
    rollupOptions: { input: { main: 'index.html', game: 'game.html', flows: 'flows.html', field: 'field.html', atlas: 'atlas.html' } },
    chunkSizeWarningLimit: 1500,
  },
});
