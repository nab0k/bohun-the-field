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
 const search=document.createElement('input');search.type='search';search.placeholder='Search companies by name or country';search.setAttribute('aria-label','Search approved companies');root.append(search);
 const count=document.createElement('p');root.append(count);
 const list=document.createElement('div');root.append(list);
 const render=()=>{
  const q=search.value.trim().toLocaleLowerCase();
  const matches=records.filter(c=>!q||c.name.toLocaleLowerCase().includes(q)||c.country.toLocaleLowerCase().includes(q));
  count.textContent=matches.length+' approved companies'+(matches.length>100?' (first 100 shown)':'');
  const rows=[];
  for(const c of matches.slice(0,100)){
   const row=document.createElement('p');
   const button=document.createElement('button');button.type='button';button.textContent=c.name+' · '+c.country;
   button.addEventListener('click',()=>{store.selectCompany(c.notion_url,c.country);onCountry?.(c.country);});
   const source=document.createElement('a');source.href=c.source_url;source.textContent=' Source';source.target='_blank';source.rel='noopener noreferrer';
   row.append(button,source);rows.push(row);
  }
  list.replaceChildren(...rows);
 };
 search.addEventListener('input',render);render();
 const host=document.getElementById('side')||document.body;host.append(root);
 return {element:root,dispose(){root.remove();}};
}
