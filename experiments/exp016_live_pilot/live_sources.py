"""Budget and one exact-page capture for each private pilot source. No discovery or retries."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.web_monitor.content import WebPolicy, capture  # noqa: E402

MAX_DOCUMENTS = 200
MAX_MODEL_POSTS = 40
MAX_SECONDS = 2 * 3600
MIN_POST_INTERVAL = 15.0
RIGHTS = 'EXP016 C2-04 private pilot; report is not published'


class LimitReached(RuntimeError):
    pass


class Budget:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep,
                 max_documents=MAX_DOCUMENTS, max_posts=MAX_MODEL_POSTS, interval=MIN_POST_INTERVAL):
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.max_documents, self.max_posts, self.interval = max_documents, max_posts, interval
        self.documents = self.posts = 0
        self.last_post = None

    def check(self):
        if self.clock() - self.started >= MAX_SECONDS:
            raise LimitReached('WALL_LIMIT')

    def document(self):
        self.check()
        if self.documents >= self.max_documents:
            raise LimitReached('DOCUMENT_LIMIT')
        self.documents += 1

    def post(self):
        self.check()
        if self.posts >= self.max_posts:
            raise LimitReached('MODEL_POST_LIMIT')
        if self.last_post is not None:
            wait = self.interval - (self.clock() - self.last_post)
            if wait > 0:
                self.sleep(wait)
        self.last_post = self.clock()
        self.posts += 1


def fetch_source(url, budget, *, capture_fn=capture, clock=None):
    """One attempt. The failure is a bounded code, never remote text."""
    budget.document()
    policy = WebPolicy(allowed_urls=(url,), rights_reference=RIGHTS, primary=True)
    now = clock or (lambda: datetime.now(timezone.utc))
    try:
        page = capture_fn(url, policy, clock=now)
    except ValueError as exc:
        code = str(exc)
        return {'url': url, 'status': code if code.isupper() and len(code) <= 80 else 'WEB_REJECTED'}
    except Exception:
        return {'url': url, 'status': 'WEB_CAPTURE_ERROR'}
    return {**page, 'status': 'CAPTURED'}
