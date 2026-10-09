// Production build of the public site: only the atlas, served as the home page.
// SITE_BASE: '/' on a custom domain or Cloudflare Pages, '/<repo>/' on a GitHub Pages project site.
// Prototypes (index/game/flows/field) stay local: they are drafts and are not published.
import { defineConfig } from 'vite';
import { renameSync } from 'node:fs';

const homePage = () => ({
  name: 'atlas-as-home',
  closeBundle() { renameSync('dist/atlas.html', 'dist/index.html'); },
});

export default defineConfig({
  base: process.env.SITE_BASE || '/',
  plugins: [homePage()],
  build: {
    rollupOptions: { input: { atlas: 'atlas.html' } },
    chunkSizeWarningLimit: 2500,
  },
});
