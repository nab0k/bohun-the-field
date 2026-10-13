// GitHub Pages approved-only snapshot publisher. Never call this with unreviewed data.
// Input: exact UTF-8 JSON bytes signed by an independent QA release key (Ed25519).
// No input means an empty public feed. Any invalid supplied input aborts the build.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { verify } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';

export function buildApprovedSnapshot({payloadPath, signaturePath, publicKeyPath, outputPath}) {
  let records=[];
  if (payloadPath || signaturePath || publicKeyPath) {
    if (!payloadPath || !signaturePath || !publicKeyPath) throw Error('QA release requires payload, signature and public key');
    const payload=readFileSync(payloadPath);
    const signature=readFileSync(signaturePath,'utf8').trim();
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(signature)) throw Error('Invalid signature encoding');
    const key=readFileSync(publicKeyPath,'utf8');
    if (!verify(null,payload,key,Buffer.from(signature,'base64'))) throw Error('Independent QA signature verification failed');
    const parsed=JSON.parse(payload.toString('utf8'));
    if (!parsed || !Array.isArray(parsed.records) || parsed.records.length>100) throw Error('Invalid approved release payload');
    const {validatePublicRecord}=awaitValidator();
    if (!parsed.records.every(validatePublicRecord)) throw Error('HOLD, missing provenance or invalid record in signed payload');
    records=parsed.records;
  }
  mkdirSync(dirname(outputPath),{recursive:true});
  writeFileSync(outputPath,JSON.stringify({records})+'\n');
  return records.length;
}
function awaitValidator() { return {validatePublicRecord:validate}; }
function validate(r) {
  if (!r || typeof r!=='object' || Array.isArray(r)) return false;
  if (r.qa?.decision!=='APPROVE' || r.qa?.public_release!==true) return false;
  if (!/^[a-f0-9]{64}$/i.test(r.content_hash||'')) return false;
  if (typeof r.source_url!=='string') return false;
  try {const u=new URL(r.source_url);if(u.protocol!=='https:'||u.username||u.password)return false;}catch{return false;}
  if(typeof r.headline!=='string'||!r.headline.trim()||r.headline.length>300)return false;
  if(typeof r.published_at!=='string'||!/^\d{4}-\d\d-\d\d/.test(r.published_at))return false;
  if(r.country_iso2!=null&&!/^[A-Z]{2}$/.test(r.country_iso2))return false;
  if(r.takeaway!=null&&(typeof r.takeaway!=='string'||r.takeaway.length>1200))return false;
  return true;
}
if (process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [payloadPath,signaturePath,publicKeyPath,outputPath='public/intelligence/approved.json']=process.argv.slice(2);
  try {console.log('Published approved records:',buildApprovedSnapshot({payloadPath,signaturePath,publicKeyPath,outputPath}));}
  catch(e){console.error('APPROVED SNAPSHOT BLOCKED:',e.message);process.exitCode=1;}
}
