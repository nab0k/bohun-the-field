"""Bounded restartable stage. No generator, Notion writer or public publisher."""
from .contracts import encode, sha, predictions
from .linker import link_documents
from .native import NativeError


def run(store, extractor, *, ingest_limit=12, model_limit=12, link_limit=12):
    for value in (ingest_limit, model_limit, link_limit):
        if type(value) is not int or not 0 <= value <= 50:
            raise ValueError('BATCH_BOUND')
    summary = dict(ingested=0, intake_held=0, model_calls=0, completed=0,
                   inference_held=0, abandoned_held=len(store.hold_abandoned()),
                   linked_sources=0, proposed_links=0, confirmed_links=0, public_release=False)
    for row in store.pending_sources(ingest_limit):
        receipt = store.enqueue(row)
        summary['ingested'] += 1
        summary['intake_held'] += receipt['intake_reason'] is not None
    for _ in range(model_limit):
        job = store.claim()
        if job is None:
            break
        summary['model_calls'] += 1
        try:
            answer = extractor(job['body'])
        except Exception as exc:
            store.hold(job, exc.code if isinstance(exc, NativeError) else 'INFERENCE_FAILED')
            summary['inference_held'] += 1
            break
        try:
            predictions(job['body'], answer)
        except (ValueError, TypeError, KeyError):
            store.hold(job, 'OUTPUT_REJECTED')
            summary['inference_held'] += 1
            break
        # A commit with unknown outcome must abort this invocation. Never catch
        # it as a model error or call the extractor again.
        store.finish(job, answer)
        summary['completed'] += 1
    catalog = store.catalog()
    catalog_hash = sha(encode(catalog))
    for row in store.to_link(catalog_hash, link_limit):
        doc = {**row['document'], 'text': row['body']}
        if sha(doc['text']) != doc['text_sha256']:
            raise ValueError('LINK_SOURCE_HASH')
        answer = predictions(doc['text'], row['result'])
        packet = link_documents([doc], {doc['id']: {'entities': answer}}, catalog)
        ids = {c['catalog_record_id']: c['db_company_id'] for c in catalog}
        for ref in packet['proposed_news_company_refs']:
            ref['proposed_company_id'] = ids[ref['proposed_catalog_record_id']]
        for mention in packet['mentions']:
            for candidate in mention['candidate_records']:
                candidate['proposed_company_id'] = ids[candidate['catalog_record_id']]
        store.save_links(row['input_key'], catalog_hash, packet)
        summary['linked_sources'] += 1
        summary['proposed_links'] += len(packet['proposed_news_company_refs'])
    return summary
