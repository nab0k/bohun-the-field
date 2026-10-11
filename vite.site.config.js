// M0 public preview: original Claude MapLibre atlas plus four corporate routes.
// Never deploy from this file alone. Keep source JSON intact, publish safe projection only.
import { defineConfig } from 'vite';
import { renameSync, readFileSync, writeFileSync, existsSync, rmSync, readdirSync } from 'node:fs';
const REQUIRED = new Set(['at-011','at-015','at-016','at-024','at-026','at-040','at-048','at-049','at-050']);
const publicGate = () => ({
  name: 'm0-public-atlas-safety',
  closeBundle() {
    renameSync('dist/atlas.html','dist/index.html');
    const file='dist/atlas/companies-at.json';
    if (!existsSync(file)) throw Error('M0 missing Austria export: fail closed');
    const source=JSON.parse(readFileSync(file,'utf8'));
    if (!Array.isArray(source.companies)) throw Error('M0 Austria schema mismatch, refuse public build');
    const seen = new Set(source.companies.filter(c=>c.flags?.length).map(c=>c.id));
    if ([...REQUIRED].some(id=>!seen.has(id))) throw Error('M0 QA flag identity changed, refuse public build');
    const safe=source.companies.filter(c=>!c.flags?.length);
    if (safe.some(c => REQUIRED.has(c.id) || c.flags?.length)) throw Error('M0 unsafe company reached public projection');
    source.companies=safe;
    source.note='M0 public projection: nine QA-flagged candidate records excluded; remaining companies are open-data candidates, not independently QA-approved.';
    writeFileSync(file,JSON.stringify(source));
    // No dev-only HTML, unrelated map experiments or backup scratch files in public dist.
    rmSync('dist/dev',{recursive:true,force:true});
    for(const path of readdirSync('dist',{recursive:true})) {
      if (/\\.(bak|backup)$/.test(path) || /\\.bohun-backup$/.test(path)) rmSync('dist/'+path,{force:true});
    }
    writeFileSync('dist/_headers','/*\\n  X-Content-Type-Options: nosniff\\n  Referrer-Policy: strict-origin-when-cross-origin\\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\\n');
    writeFileSync('dist/robots.txt','User-agent: *\\nDisallow: /dev/\\nDisallow: /api/internal/\\n');
    writeFileSync('dist/404.html','<!doctype html><html lang="en"><meta charset="utf-8"><title>Page not found | Bohundefence</title><body><h1>Page not found</h1><p><a href="/">Return to THE FIELD</a></p></body></html>');

  }
});
export default defineConfig({
 base: process.env.SITE_BASE || '/',
 plugins:[publicGate()],
 build:{
  rollupOptions:{input:{
   atlas:'atlas.html',
   about:'about.html',
   services:'services.html',
   aboutProject:'about-project.html',
   supportProject:'support-project.html',
   contact:'contact.html',
   privacy:'privacy.html'
  }},
  chunkSizeWarningLimit:2500
 }
});
