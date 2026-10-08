import { defineConfig } from 'vite';

// Two entry pages: the landing draft (index.html) and the game module demo (game.html).
export default defineConfig({
  build: {
    rollupOptions: { input: { main: 'index.html', game: 'game.html', flows: 'flows.html' } },
    chunkSizeWarningLimit: 1500,
  },
});
