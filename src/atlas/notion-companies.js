// Country-level directory from the editorially approved Notion export.
// Never infer a factory pin from a company record or from its city.
export function validateNotionCompany(x) {
 if(!x||typeof x!=='object'||Array.isArray(x))return false;
 if(x.review_status!=='accepted'||x.publishable!==true||x.sanctions!=='clear')return false;
 if(!/^[A-Z]{2}$/.test(x.country||''))return false;
 if(typeof x.name!=='string'||!x.name.trim()||x.name.length>240)return false;
 if(typeof x.notion_url!=='string'||!/^https:\/\/(app\.)?notion\.(com|so)\//.test(x.notion_url))return false;
 try {const u=new URL(x.source_url);if(u.protocol!=='https:'||u.username||u.password)return false;}catch{return false;}
 if(x.flags?.length)return false;
 return true;
}
export async function loadNotionCompanies({fetchImpl=fetch}={}){
 try{
  const res=await fetchImpl('/atlas/notion-companies.json',{cache:'no-store'});
  if(!res.ok)return[];
  const data=await res.json();
  if(!Array.isArray(data?.companies)||data.companies.length>10000||!data.companies.every(validateNotionCompany))return[];
  return data.companies;
 }catch{return[];}
}
export function mountNotionCompanyDirectory({records,store,onCountry}){
 const root=document.createElement('section');root.id='notion-company-directory';
 const title=document.createElement('h2');title.textContent='Verified company directory';root.append(title);
 const description=document.createElement('p');description.textContent='Approved country-level company records. No inferred facility locations.';root.append(description);
 const list=document.createElement('div');root.append(list);
 for(const c of records){
  const row=document.createElement('p');
  const button=document.createElement('button');button.type='button';button.textContent=c.name+' · '+c.country;
  button.addEventListener('click',()=>{store.selectCompany(c.notion_url,c.country);onCountry?.(c.country);});
  const source=document.createElement('a');source.href=c.source_url;source.textContent=' Source';source.target='_blank';source.rel='noopener noreferrer';
  row.append(button,source);list.append(row);
 }
 const host=document.getElementById('side')||document.body;host.append(root);
 return {element:root,dispose(){root.remove();}};
}
