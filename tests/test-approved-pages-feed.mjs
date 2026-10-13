import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { generateKeyPairSync, sign } from 'node:crypto';
import { buildApprovedSnapshot } from '../scripts/build-approved-pages-feed.mjs';
const dir=mkdtempSync(join(tmpdir(),'bohun-pages-'));
try {
 const outputPath=join(dir,'approved.json');
 assert.equal(buildApprovedSnapshot({outputPath}),0);
 assert.deepEqual(JSON.parse(readFileSync(outputPath,'utf8')),{records:[]});
 const {privateKey,publicKey}=generateKeyPairSync('ed25519');
 const key=join(dir,'qa.pem');writeFileSync(key,publicKey.export({type:'spki',format:'pem'}));
 const payload=join(dir,'payload.json'),sig=join(dir,'payload.sig');
 const good={qa:{decision:'APPROVE',public_release:true},content_hash:'a'.repeat(64),source_url:'https://example.org/original',headline:'Verified event',published_at:'2026-10-13T10:00:00Z',country_iso2:'AT'};
 function signed(records){const bytes=Buffer.from(JSON.stringify({records}));writeFileSync(payload,bytes);writeFileSync(sig,sign(null,bytes,privateKey).toString('base64'));return {payloadPath:payload,signaturePath:sig,publicKeyPath:key,outputPath};}
 assert.equal(buildApprovedSnapshot(signed([good])),1);
 assert.deepEqual(JSON.parse(readFileSync(outputPath,'utf8')).records,[good]);
 assert.throws(()=>buildApprovedSnapshot(signed([{...good,qa:{decision:'HOLD',public_release:false}}])),/HOLD/);
 assert.throws(()=>buildApprovedSnapshot(signed([{...good,source_url:'http://example.org'}])),/HOLD/);
 assert.throws(()=>buildApprovedSnapshot(signed([{...good,content_hash:'bad'}])),/HOLD/);
 const args=signed([good]);writeFileSync(payload,JSON.stringify({records:[{...good,headline:'Tampered'}]}));
 assert.throws(()=>buildApprovedSnapshot(args),/signature verification failed/);
 assert.throws(()=>buildApprovedSnapshot({payloadPath:payload,outputPath}),/requires payload/);
 console.log('GitHub Pages signed approved snapshot: PASS');
}finally{rmSync(dir,{recursive:true,force:true});}
