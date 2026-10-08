import { defineConfig } from 'vite';

// Entry pages: landing draft (index.html), game module demo (game.html), flows prototype (flows.html), v0.4 field (field.html).
export default defineConfig({
  build: {
    rollupOptions: { input: { main: 'index.html', game: 'game.html', flows: 'flows.html', field: 'field.html' } },
    chunkSizeWarningLimit: 1500,
  },
});
