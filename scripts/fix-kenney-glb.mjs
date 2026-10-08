// Kenney GLBs carry an identity KHR_texture_transform ({texCoord: 0}) on the base colour texture. luma.gl (deck.gl 9.4)
// misreads it and drops the texture, so the models render white. This removes that no-op extension in place and records it
// in public/atlas/models/PROVENANCE.json. CC0 allows modification. Usage: node scripts/fix-kenney-glb.mjs
import fs from 'node:fs';
import crypto from 'node:crypto';
const root = new URL('../public/atlas/models/', import.meta.url);
const pv = JSON.parse(fs.readFileSync(new URL('PROVENANCE.json', root)));
let changed = 0;
for (const f of pv.files) {
  const url = new URL(f.file, root), b = fs.readFileSync(url);
  const len = b.readUInt32LE(12), json = JSON.parse(b.subarray(20, 20 + len).toString());
  let touched = false;
  for (const m of json.materials ?? []) {
    const t = m.pbrMetallicRoughness?.baseColorTexture;
    const x = t?.extensions?.KHR_texture_transform;
    if (x && Object.keys(x).every((k) => k === 'texCoord') && (x.texCoord ?? 0) === 0) { delete t.extensions.KHR_texture_transform; if (!Object.keys(t.extensions).length) delete t.extensions; touched = true; }
  }
  if (!touched) continue;
  json.extensionsUsed = (json.extensionsUsed ?? []).filter((e) => e !== 'KHR_texture_transform');
  if (!json.extensionsUsed.length) delete json.extensionsUsed;
  let js = Buffer.from(JSON.stringify(json));
  if (js.length % 4) js = Buffer.concat([js, Buffer.alloc(4 - (js.length % 4), 0x20)]);
  const rest = b.subarray(20 + len), header = Buffer.alloc(20);
  header.write('glTF', 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(20 + js.length + rest.length, 8); header.writeUInt32LE(js.length, 12); header.write('JSON', 16);
  const out = Buffer.concat([header, js, rest]);
  fs.writeFileSync(url, out);
  f.originalSha256 ??= f.sha256; f.sha256 = crypto.createHash('sha256').update(out).digest('hex'); f.bytes = out.length;
  f.modified = 'removed identity KHR_texture_transform (texCoord 0) for luma.gl';
  changed++;
}
fs.writeFileSync(new URL('PROVENANCE.json', root), JSON.stringify(pv, null, 1));
console.log('fixed', changed, 'of', pv.files.length);
