"""Offline name candidate retrieval. Never asserts legal identity or writes a DB."""
import hashlib
import json
import re
import unicodedata

from ..companies.normalize import name_norm


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fold(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return ' '.join(value.split())


def tokens(value):
    return tuple(name_norm(value).split())


def whole_tokens(needle, haystack):
    return bool(needle) and any(haystack[n:n+len(needle)] == needle for n in range(len(haystack)-len(needle)+1))


def source_name(text, parts):
    """Find consecutive literal name tokens, retaining original source offsets.

    Punctuation/hyphenation and accents can differ. This is lexical support,
    NOT proof that a corporate group and subsidiary are the same legal entity.
    """
    if not parts:return None
    words = list(re.finditer(r'\w+', text, flags=re.UNICODE))
    folded = []
    for word in words:
        v = unicodedata.normalize('NFKD', word.group().casefold())
        folded.append(''.join(c for c in v if not unicodedata.combining(c)))
    for pos in range(len(words)-len(parts)+1):
        if tuple(folded[pos:pos+len(parts)]) == parts:
            return {'start': words[pos].start(), 'end': words[pos+len(parts)-1].end(),
                    'text': text[words[pos].start():words[pos+len(parts)-1].end()]}
    return None


def citation(doc, span, flank=140):
    a,b,q = span.get('start'),span.get('end'),span.get('text')
    if not (type(a) is int and type(b) is int and isinstance(q,str) and
            0 <= a < b <= len(doc['text']) and doc['text'][a:b] == q):
        raise ValueError('MENTION_NOT_BOUND_TO_SOURCE')
    start,end = max(0,a-flank),min(len(doc['text']),b+flank)
    return {'document_id':doc['id'], 'source_url':doc['url'], 'published':doc.get('published'),
            'text_sha256':sha(doc['text'].encode()), 'quote':doc['text'][start:end],
            'quote_start':start, 'quote_end':end, 'mention_start':a, 'mention_end':b,
            'mention_text':q}


def is_footer_publisher(text,start):
    # Preserve original source/offsets; suppress only a source-credit occurrence,
    # never every mention of the same publisher elsewhere in the article.
    return bool(re.search(r'\bappeared\s+first\s+on\s*$',text[max(0,start-100):start],re.I))


class Index:
    def __init__(self, companies):
        self.companies = companies
        ids=[c['catalog_record_id'] for c in companies]
        if len(ids) != len(set(ids)): raise ValueError('DUPLICATE_CATALOG_ID')
        self.names=[]
        for c in companies:
            for name in [c['name'],*c.get('aliases',[])]:
                parts=tokens(name)
                if parts:self.names.append((c,name,fold(name),parts))

    def candidates(self, surface, text):
        query=tokens(surface)
        if not query:return []
        found={}
        for c,name,key,parts in self.names:
            basis=None;support=None;rank=None
            if fold(surface)==key:
                basis='exact_name';rank=0
            elif query==parts:
                basis='normalized_name';rank=1
            elif len(query)>=2 and len(''.join(query))>=8 and whole_tokens(query,parts):
                # Includes brand/group/subsidiary expansions; explicitly weak.
                basis='name_contained_in_catalog_name';rank=3
            elif len(parts)>=2 and len(''.join(parts))>=8 and whole_tokens(parts,query):
                basis='catalog_name_contained_in_mention';rank=3
            elif len(query)==1 and len(query[0])>=3 and parts[0]==query[0] and len(parts)>=2:
                # BAE -> BAE Systems is considered only if the full catalog base
                # name also occurs somewhere in the SAME source document.
                support=source_name(text,parts)
                if support:
                    basis='short_name_with_full_name_in_source';rank=2
            if basis is None:continue
            item={'catalog_record_id':c['catalog_record_id'], 'notion_page_id':c.get('notion_page_id'),
                  'name':c['name'], 'country':c.get('country'), 'matched_name':name,
                  'retrieval_basis':basis, 'rank':rank, 'identity_confirmed':False,
                  'catalog_identity_status':c.get('identity_status','unresolved'),
                  'supporting_source_span':support,
                  'use_for_proposed_link':rank<=2}
            previous=found.get(c['catalog_record_id'])
            if previous is None or (rank,name)<(previous['rank'],previous['matched_name']):
                found[c['catalog_record_id']]=item
        # No top-k truncation: two or more possible records must remain visible.
        return sorted(found.values(),key=lambda i:(i['rank'],i['name'],i['catalog_record_id']))


def link_documents(documents,predictions,catalog,review=()):
    docs={d['id']:d for d in documents}
    if len(docs)!=len(documents): raise ValueError('DUPLICATE_DOCUMENT')
    if set(predictions)!=set(docs): raise ValueError('PREDICTION_DOCUMENT_INVENTORY')
    held={(x['id'],x['text']):x for x in review if x['task']=='entities' and x['type']=='organization'
          and x['verdict'] in {'WRONG_TYPE','OUT_OF_NAMED_SCOPE','BOUNDARY_ERROR'}}
    index=Index(catalog);mentions=[];seen=set()
    for doc in documents:
        values=predictions[doc['id']]['entities']['entities'].get('organization',[])
        for span in values:
            evidence=citation(doc,span)
            unique=(doc['id'],span['start'],span['end'])
            if unique in seen:continue
            seen.add(unique)
            reviewed=held.get((doc['id'],span['text']))
            footer=is_footer_publisher(doc['text'],span['start'])
            candidates=[] if reviewed or footer else index.candidates(span['text'],doc['text'])
            primary=[c for c in candidates if c['use_for_proposed_link']]
            status=('held_by_source_review' if reviewed else 'source_credit_only' if footer else 'no_candidate' if not candidates else
                    'related_names_only' if not primary else
                    'unique_name_candidate' if len(primary)==1 else 'multiple_name_candidates')
            item={'mention_id':sha(json.dumps([doc['id'],evidence['text_sha256'],span['start'],span['end']],separators=(',',':')).encode()),
                  'surface':span['text'], 'model_label':'organization', 'role':'mentioned_only',
                  'model_confidence':span.get('confidence'), 'status':status, 'evidence':evidence,
                  'candidate_records':candidates, 'company_id':None, 'identity_confirmed':False,
                  'public_release':False, 'review_reason':reviewed['reason'] if reviewed else
                  'Publisher in an appeared-first-on footer, not an article-subject link.' if footer else None}
            mentions.append(item)
    refs=[];related=[]
    by_id={c['catalog_record_id']:c for c in catalog}
    for doc in documents:
        matching=[m for m in mentions if m['evidence']['document_id']==doc['id']]
        primary_ids={c['catalog_record_id'] for m in matching for c in m['candidate_records'] if c['use_for_proposed_link']}
        all_ids={c['catalog_record_id'] for m in matching for c in m['candidate_records']}
        for cid in sorted(all_ids):
            primary=cid in primary_ids
            contributing=[m for m in matching if any(c['catalog_record_id']==cid and (not primary or c['use_for_proposed_link']) for c in m['candidate_records'])]
            ref={'document_id':doc['id'], 'proposed_catalog_record_id':cid,
                         'notion_page_id':by_id[cid]['notion_page_id'], 'proposed_name':by_id[cid]['name'],
                         'status':'candidate_mention_link' if primary else 'name_similarity_only',
                         'role':'mentioned_only' if primary else 'unresolved_name_similarity',
                         'mention_ids':[m['mention_id'] for m in contributing],
                         'evidence':[m['evidence'] for m in contributing],
                         'identity_confirmed':False,'company_id':None,'public_release':False}
            (refs if primary else related).append(ref)
    return {'schema_version':'bohun-local-company-name-candidates-v1', 'mentions':mentions,
            'proposed_news_company_refs':refs, 'related_name_candidates':related,
            'confirmed_links':[], 'public_release':False,
            'new_company_creation':False, 'catalog_mutations':False}
