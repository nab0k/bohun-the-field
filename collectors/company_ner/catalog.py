"""Minimal private catalog projection; excluded cards are not proposed."""
def project_catalog(rows):
    if not isinstance(rows,list) or len(rows)>5000:raise ValueError('CATALOG_BOUND')
    seen=set();out=[]
    for row in rows:
        ident=row.get('id');name=row.get('name');status=row.get('status')
        if not isinstance(ident,str) or not ident or ident in seen:raise ValueError('CATALOG_ID')
        seen.add(ident)
        if not isinstance(name,str) or not name.strip():raise ValueError('CATALOG_NAME')
        if status not in {'candidate','card','excluded'}:raise ValueError('CATALOG_STATUS')
        aliases=row.get('aliases',[])
        if not isinstance(aliases,list) or len(aliases)>100 or not all(isinstance(a,str) and 0<len(a)<=2000 for a in aliases):
            raise ValueError('ALIAS_BOUND')
        if status=='excluded':continue
        out.append({'catalog_record_id':'companies.company:'+ident,'db_company_id':ident,
                    'notion_page_id':row.get('notion_page_id'),'name':name,'aliases':aliases,
                    'country':row.get('country'),'identity_status':'unresolved',
                    'catalog_status':status,'catalog_updated_at':row.get('updated_at')})
    return out

