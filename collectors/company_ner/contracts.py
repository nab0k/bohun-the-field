"""Stable computation identity; source identity and company identity stay separate."""
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

MODEL = {'library': 'gliner2', 'version': '2.1.0',
         'model': 'fastino/gliner2.5-multi-v1',
         'revision': 'cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3'}
SCHEMA = {'organization': 'Named companies, public bodies, armed services and other organizations; preserve the exact name, not a whole sentence.'}
OPTIONS = {'threshold': 0.5, 'chunk_size': 256, 'chunk_overlap': 48, 'batch_size': 1}
CONTRACT = {'version': 'company-ner-v1', 'model': MODEL, 'schema': SCHEMA,
            'options': OPTIONS, 'adapter': 'native-long-spans-v1'}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


# Changing executable adapters or normalization invalidates the relevant cache,
# even if a future edit forgets to bump the human-readable version string.
_HERE = Path(__file__).resolve().parent
CONTRACT['adapter_sha256'] = sha(encode({
    name: hashlib.sha256((_HERE/name).read_bytes()).hexdigest()
    for name in ('native.py', 'native-pins.json', 'contracts.py')}))
CONTRACT_HASH = sha(encode(CONTRACT))
LINK_VERSION = 'company-name-candidates-v1:' + sha(encode({
    name: hashlib.sha256(path.read_bytes()).hexdigest()
    for name,path in [('linker',_HERE/'linker.py'),('catalog',_HERE/'catalog.py'),
                      ('normalization',_HERE.parent/'companies/normalize.py')]}))


def job_key(text, contract=CONTRACT):
    return sha(encode({'text_sha256': sha(text), 'contract': contract}))


def source(row):
    """A saved provider body is usable for NER, not certified as a full article."""
    ident = str(row['item_id'])
    if not re.fullmatch(r'[1-9][0-9]*', ident):
        raise ValueError('ITEM_ID')
    title, url = row.get('title'), row.get('url')
    if not isinstance(title, str) or not title.strip() or len(title) > 4000:
        raise ValueError('SOURCE_TITLE')
    try:
        valid_url = isinstance(url, str) and len(url) <= 8192 and urlsplit(url).scheme in {'https', 'http'} and urlsplit(url).hostname
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError('SOURCE_URL')
    cap = row.get('content_capture')
    if not isinstance(cap, dict) or cap.get('version') != 1 or cap.get('status') != 'BODY_PRESENT_COMPLETENESS_UNVERIFIED':
        raise ValueError('BODY_NOT_ELIGIBLE')
    text = cap.get('text')
    if not isinstance(text, str) or not 200 <= len(text.encode()) <= 131072:
        raise ValueError('BODY_SIZE')
    if cap.get('text_sha256') != sha(text) or cap.get('text_bytes') != len(text.encode()):
        raise ValueError('BODY_HASH')
    return {'id': 'intelligence.items:' + ident, 'source_item_id': ident,
            'title': title, 'url': url, 'published': row.get('published_at'),
            'text': text, 'text_sha256': sha(text), 'completeness': 'UNKNOWN',
            'publication_status': 'HOLD', 'public_release': False}


def predictions(text, answer):
    """Structural/source checks only: exact offsets do not certify entity type."""
    if len(encode(answer).encode()) > 2 * 1024**2:
        raise ValueError('PREDICTION_SIZE')
    if not isinstance(answer, dict) or set(answer) != {'entities'} or not isinstance(answer['entities'], dict):
        raise ValueError('PREDICTION_SHAPE')
    if set(answer['entities']) - {'organization'}:
        raise ValueError('PREDICTION_LABEL')
    spans = answer['entities'].get('organization', [])
    if not isinstance(spans, list) or len(spans) > 4000:
        raise ValueError('SPAN_BOUND')
    clean = []
    for s in spans:
        if not isinstance(s, dict) or set(s) != {'start', 'end', 'text', 'confidence'}:
            raise ValueError('SPAN_SHAPE')
        a, b, word, confidence = (s[k] for k in ('start', 'end', 'text', 'confidence'))
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text) or text[a:b] != word:
            raise ValueError('SPAN_SOURCE')
        if type(confidence) not in {int, float} or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('SPAN_CONFIDENCE')
        clean.append(dict(s))
    return {'entities': {'organization': clean}}
