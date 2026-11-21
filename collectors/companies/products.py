"""Product extraction for company cards (B4CE-04, slice 3.3).

The model only finds candidate products with a quote; the code decides what is kept:
- the quote must be found verbatim (whitespace-normalised) in the page text it names;
- the product name must appear inside the quote;
- `specialized` (military-specific) is kept only when the quote itself carries military wording.
Kept items become claims for level.classify: a named specialized product or service from the company's
own site gives L1 (R01), a component gives L2 (R02); confidence stays what level.py assigns to a self
source. Nothing here changes level rules. Free OpenAI-compatible providers (Groq, Mistral) or Gemini,
keys from the environment only.
"""
import json
import os
import re
import unicodedata
import urllib.request

MAX_CHARS_PER_PAGE = 12_000
MAX_ITEMS = 12
# Wording that makes an item military-specific. Bare 'ammunition', 'weapon' or 'defence' are not enough:
# hunting and sport ammunition, 'self-defense' cartridges (trial 21.11.2026, Sellier & Bellot) use them too.
MILITARY = re.compile(
    r'\b(military|army|armed forces|armies|soldiers?|battlefield|combat|tactical|nato|missiles?|artillery|'
    r'warships?|naval forces|armou?red (?:vehicle|personnel)|air defen[cs]e|missile defen[cs]e|ministry of defen[cs]e|'
    r'defen[cs]e (?:forces|industry|sector|ministry|procurement|customers)|electronic warfare|'
    r'(?<!self-)(?<!self )defen[cs]e (?:solutions|systems|applications)|loitering munitions?|mortars?|howitzers?|'
    r'grenades?|warheads?|torpedo\w*|interceptors?|v?shorad|manpads|anti-(?:ship|tank|armou?r|aircraft)|'
    r'counter-battery|istar|c4isr|c4istar|elint|sigint|comint|frigates?|corvettes?|submarines?|destroyers?|'
    r'fighter (?:jets?|aircraft)|armou?red|minomet\w*|granát\w*|houfnic\w*|'
    r'vojensk\w*|armád\w*|bojov\w*|protivzdušn\w*|ozbrojen\w*|elektronick\w* boj\w*|'
    r'puolustusvoim\w*|militaire|armée|forsvaret)\b', re.I)
# An item also meant for civil use is dual-use, not military-specific (trial 21.11.2026, VZLU MiYa).
DUAL = re.compile(r'\b(civil\w*|commercial|komerčn\w*|siviili\w*|dual[- ]use|dvojího užití)\b', re.I)
KINDS = {'product', 'service', 'component'}

PROMPT = """You read pages from the website of {name} ({country}). List the products, systems, components or
services that THIS company itself makes or provides. Do not list customers, partners' products or news.

For every item give a quote copied EXACTLY from the page text (one or two sentences, at most 300 characters)
that names the item, and the page URL it comes from.
kind: "product" (a finished system or equipment), "component" (a part or subsystem for others' systems),
or "service".
specialized: true only if the quote shows the item is made for military or defence use; otherwise false.

Answer with one JSON object only:
{{"items": [{{"name": "...", "kind": "product|component|service", "specialized": true|false,
  "quote": "...", "url": "..."}}]}}
At most {max_items} items. If the pages name no products, answer {{"items": []}}.

{pages}"""


def build_prompt(company, pages):
    blocks = [f'=== PAGE {p["url"]} ===\n{p["text"][:MAX_CHARS_PER_PAGE]}' for p in pages]
    return PROMPT.format(name=company['name'], country=company.get('country') or '?', max_items=MAX_ITEMS,
                         pages='\n\n'.join(blocks))


def _norm(s):
    s = unicodedata.normalize('NFKC', s or '').replace('\xad', '')
    s = re.sub(r'[‘’‚′]', "'", s)
    s = re.sub(r'[“”„″]', '"', s)
    s = re.sub(r'[‐-―−]', '-', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def parse(answer):
    """Model answer (text) -> list of item dicts; tolerant of code fences and text around the JSON."""
    m = re.search(r'\{.*\}', answer or '', re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get('items') if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict)][:MAX_ITEMS]


def verify(items, pages):
    """(kept, rejected): kept items have a verbatim quote on the named page and the name inside the quote."""
    by_url = {p['url'].rstrip('/'): _norm(p['text']) for p in pages}
    kept, rejected, seen = [], [], set()
    for it in items:
        name, quote, url = (it.get('name') or '').strip(), (it.get('quote') or '').strip(), (it.get('url') or '').rstrip('/')
        kind = it.get('kind') if it.get('kind') in KINDS else None
        reason = None
        if not name or not quote or not kind:
            reason = 'missing name, quote or kind'
        elif url not in by_url:
            reason = 'quote page is not one of the pages read'
        elif _norm(quote) not in by_url[url]:
            reason = 'quote not found verbatim on the page'
        elif _norm(name) not in _norm(quote):
            reason = 'product name not inside the quote'
        elif _norm(name) in seen:
            reason = 'duplicate'
        if reason:
            rejected.append({**it, 'reason': reason})
            continue
        seen.add(_norm(name))
        military = bool(MILITARY.search(quote)) and not DUAL.search(quote)
        kept.append({'name': name, 'kind': kind, 'quote': quote, 'url': it.get('url'),
                     'specialized': bool(it.get('specialized')) and military,
                     'model_said_specialized': bool(it.get('specialized'))})
    return kept, rejected


def to_claims(kept):
    """Claims for level.classify. Company site = self source, named product, documented by the quote."""
    return [{'kind': k['kind'], 'specialized': k['specialized'], 'documented': True, 'source': 'self',
             'named': True, 'group': 'self'} for k in kept]


# --- model call ---------------------------------------------------------------------------------------
def providers():
    out = []
    if os.environ.get('GROQ_API_KEY'):
        out.append(('groq', 'https://api.groq.com/openai/v1/chat/completions', os.environ['GROQ_API_KEY'],
                    os.environ.get('PRODUCTS_GROQ_MODEL', 'openai/gpt-oss-120b')))
    if os.environ.get('MISTRAL_API_KEY'):
        out.append(('mistral', 'https://api.mistral.ai/v1/chat/completions', os.environ['MISTRAL_API_KEY'],
                    os.environ.get('PRODUCTS_MISTRAL_MODEL', 'mistral-small-latest')))
    key = os.environ.get('GEMINI_FREE_API_KEY') or os.environ.get('GEMINI_SEARCH_API_KEY')
    if key:
        out.append(('gemini', None, key, os.environ.get('PRODUCTS_GEMINI_MODEL', 'gemini-3.5-flash-lite')))
    return out


def ask(prompt, provider, opener=urllib.request.urlopen, timeout=120):
    name, url, key, model = provider
    if name == 'gemini':
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        body = {'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {'temperature': 0}}
        headers = {'x-goog-api-key': key}
    else:
        body = {'model': model, 'temperature': 0, 'max_tokens': 6000,
                'messages': [{'role': 'user', 'content': prompt}]}
        headers = {'Authorization': 'Bearer ' + key}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'BOHUN-research/1.0', **headers})
    with opener(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
    if name == 'gemini':
        return ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'])
    return data['choices'][0]['message']['content']


def extract(company, pages, provider, opener=urllib.request.urlopen):
    """One company -> {'kept', 'rejected', 'claims', 'provider'}; no pages -> nothing asked."""
    if not pages:
        return {'kept': [], 'rejected': [], 'claims': [], 'provider': None}
    items = parse(ask(build_prompt(company, pages), provider, opener))
    kept, rejected = verify(items, pages)
    return {'kept': kept, 'rejected': rejected, 'claims': to_claims(kept), 'provider': provider[0] + ':' + provider[3]}
