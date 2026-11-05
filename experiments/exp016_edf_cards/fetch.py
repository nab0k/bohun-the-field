"""One home page per company. At most one redirect, and only inside the same site (www or not)."""
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from experiments.exp016_live_pilot.live_sources import Budget, fetch_source  # noqa: F401
from collectors.web_monitor.content import USER_AGENT, _public_host


class KeepRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # urllib then raises HTTPError with the 3xx status and its headers


def https(url):
    p = urlsplit(url.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('URL_INVALID')
    return urlunsplit(('https', p.netloc.lower(), p.path or '/', p.query, ''))


def site(host):
    host = (host or '').lower()
    return host[4:] if host.startswith('www.') else host


def same_site(a, b):
    return site(urlsplit(a).hostname) == site(urlsplit(b).hostname)


def redirect_target(url, *, opener=None, check_host=_public_host):
    """Returns the Location of a 3xx answer or None. Reads no body and follows nothing."""
    check_host(url)
    opener = opener or build_opener(KeepRedirect())
    try:
        with opener.open(Request(url, headers={'User-Agent': USER_AGENT}), timeout=20):
            return None
    except HTTPError as exc:
        if 300 <= exc.code < 400 and exc.headers.get('Location'):
            return urljoin(url, exc.headers['Location'])
        return None


def resolve(url, *, probe):
    """The page to capture: the listed URL, or its single same-site redirect target."""
    first = https(url)
    target = probe(first)
    if not target:
        return first, False
    second = https(target)
    if not same_site(first, second):
        raise ValueError('WEB_REDIRECT_OTHER_SITE')
    if probe(second) not in (None,):
        raise ValueError('WEB_REDIRECT_CHAIN')
    return second, True


def fetch_home(url, budget, *, probe=None, capture_fn=None):
    probe = probe or (lambda u: redirect_target(u))
    try:
        final, moved = resolve(url, probe=probe)
    except ValueError as exc:
        code = str(exc)
        return {'url': url, 'status': code if code.isupper() else 'WEB_REJECTED'}
    except Exception:
        return {'url': url, 'status': 'WEB_PROBE_ERROR'}
    page = fetch_source(final, budget, **({'capture_fn': capture_fn} if capture_fn else {}))
    page['redirected'] = moved
    page.setdefault('url', final)
    return page
