// M0 public release candidate: existing Claude atlas, no production deploy.
import { defineConfig } from 'vite';
import { renameSync, readFileSync, writeFileSync, existsSync, rmSync, readdirSync } from 'node:fs';
const FLAG_IDS = new Set(['at-011','at-015','at-016','at-024','at-026','at-040','at-048','at-049','at-050']);
const publicGate = () => ({
  name: 'm0-public-atlas-safety',
  closeBundle() {
    renameSync('dist/atlas.html','dist/index.html');
    const path='dist/atlas/companies-at.json';
    if(!existsSync(path)) throw Error('M0 Austria JSON missing');
    const data=JSON.parse(readFileSync(path,'utf8'));
    if(!Array.isArray(data.companies)) throw Error('M0 Austria schema mismatch');
    const flagged=new Set(data.companies.filter(c=>c.flags?.length).map(c=>c.id));
    if([...FLAG_IDS].some(id=>!flagged.has(id))) throw Error('M0 QA flag mapping changed, refusing release');
    data.companies=data.companies.filter(c=>!c.flags?.length);
    if(data.companies.some(c=>FLAG_IDS.has(c.id))) throw Error('M0 blocked Austrian record leaked');
    data.note='Research candidates from public sources, not individually certified. Nine flagged records excluded from M0 public export.';
    writeFileSync(path,JSON.stringify(data));
    // Internal HOLD files are never part of a public release artifact.
    rmSync('dist/atlas/pilot-internal-feed.json',{force:true});
    rmSync('dist/atlas/intelligence-v1-internal.json',{force:true});
    rmSync('dist/dev',{recursive:true,force:true});
    for(const item of readdirSync('dist',{recursive:true})) {
      if(/\.(bak|backup)$/.test(item) || /\.bohun-backup$/.test(item)) rmSync('dist/'+item,{force:true});
    }
    if(existsSync('dist/atlas/pilot-internal-feed.json'))throw Error('R1 HOLD feed leaked into release');
    writeFileSync('dist/_headers','/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\n');
    writeFileSync('dist/robots.txt','User-agent: *\nDisallow: /dev/\nDisallow: /api/internal/\n');
    writeFileSync('dist/404.html','<!doctype html><html lang="en"><meta charset="utf-8"><title>Page not found | Bohundefence</title><body><h1>Page not found</h1><p><a href="/">Return to THE FIELD</a></p></body></html>');
  }
});
export default defineConfig({
  base: process.env.SITE_BASE || '/',
  plugins:[publicGate()],
  build:{rollupOptions:{input:{
    atlas:'atlas.html',about:'about.html',services:'services.html',
    aboutProject:'about-project.html',supportProject:'support-project.html',
    contact:'contact.html',privacy:'privacy.html'
  }},chunkSizeWarningLimit:2500}
});
