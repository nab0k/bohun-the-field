// R1 offline contract test: no fake approvals, no new standalone panel.
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const read=p=>readFileSync(p,'utf8');
const ui=read('src/atlas/m1-state.js');
const atlas=read('atlas.html');
const build=read('vite.site.config.js');
assert.match(ui,/const feed=create\('m1-intelligence','Verified Intelligence',true\)/);
assert.match(ui,/const dossier=create\('m1-dossier'/);
assert.match(ui,/r\?\.qa\?\.decision==='APPROVE'/);
assert.match(ui,/r\?\.qa\?\.public_release===true/);
assert.match(ui,/No QA-approved intelligence items available yet/);
assert.match(atlas,/id="map"/);
for(const id of ['companies','industry','sea','night','all','q'])assert.match(atlas,new RegExp('id="'+id+'"'));
for(const href of ['about.html','services.html','contact.html'])assert.ok(atlas.includes(href));
assert.match(build,/rmSync\('dist\/atlas\/pilot-internal-feed.json'/);
console.log('R1 static integration and QA gate PASS');
