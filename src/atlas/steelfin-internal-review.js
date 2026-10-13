// INTERNAL ONLY: issue A Steelfin source-linked review candidate.
// Never import into production build. No verified event coordinates; US is issuer context.
import {selectApproved,createAtlasState} from './m1-state.js';
export const steelfinCandidate=Object.freeze({
 event_id:'event:issuer-announcement:steelfin:2026-10-12',
 company_id:'company:anduril:unverified-entity-link',
 company_name:'Anduril Industries',
 country_iso2:'US',
 geography_scope:'US company context only; announcement has no verified event point',
 geo:null,
 published_at:'2026-10-12',
 event_type:'product_announcement',
 sector:'air_defence',
 headline:'Anduril announces Steelfin missile family',
 takeaway:'Issuer announcement, not validated production, flight tests, or contract award.',
 source_url:'https://www.anduril.com/news/steelfin',
 secondary_source_url:'https://www.twz.com/land/anduril-unveils-steelfin-family-of-modular-surface-to-air-missiles',
 inoreader_external_id:'tag:google.com,2005:reader/item/0000000bd4c065db',
 inoreader_metadata_sha256:'dd3e9c17b00bccb102590cd53c0043821f308f1e8a694608a972eb5141aa8184',
 content_hash:null, // No immutable first-party original snapshot captured.
 qa:Object.freeze({decision:'HOLD',public_release:false,independent_review:'PENDING'})
});
export function mountSteelfinReview(){
 if(!import.meta.env.DEV) throw Error('STEELFIN_INTERNAL_ONLY');
 if(selectApproved([steelfinCandidate],createAtlasState().get()).length)throw Error('HOLD_LEAK');
 const aside=document.createElement('aside');
 aside.id='internal-steelfin-review';
 aside.style.cssText='position:fixed;z-index:900;left:14px;bottom:25px;width:min(340px,calc(100vw - 28px));max-height:60vh;overflow:auto;background:#17201c;color:#fff6dc;padding:16px;border:2px solid #d9a34b;border-radius:8px;font:14px/1.5 system-ui';
 const title=document.createElement('h2');title.textContent='INTERNAL REVIEW · QA HOLD';aside.append(title);
 const h=document.createElement('h3');h.textContent=steelfinCandidate.headline;aside.append(h);
 for(const line of [steelfinCandidate.takeaway,'Company: Anduril Industries','Geography: United States (issuer context only)','Map event point: NOT VERIFIED; no pin','Evidence: issuer statement; full-content hash NOT AVAILABLE','Rights: UNREVIEWED · Public release: false']){
  const p=document.createElement('p');p.textContent=line;aside.append(p);
 }
 for(const [label,url] of [['Company statement',steelfinCandidate.source_url],['TWZ article',steelfinCandidate.secondary_source_url]]){
  const a=document.createElement('a');a.href=url;a.textContent=label;a.rel='noopener noreferrer';a.target='_blank';a.style.cssText='display:block;color:#f9c56a;margin-top:7px';aside.append(a);
 }
 document.body.append(aside);
 return aside;
}
