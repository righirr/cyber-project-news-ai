"""Text normalisation, extractive summaries and article-page excerpts."""
import html
import re
from html.parser import HTMLParser

_TAG = re.compile(r'<[^>]*>')
_WS = re.compile(r'\s+')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_SPACE_BEFORE_PUNCT = re.compile(r'\s+([.,;:!?](?:\s|$))')  # left behind by stripped inline tags
_BOILERPLATE = [
    re.compile(r'\bThe post .{3,200}? appeared first on .{2,120}?\.?$', re.I),
    re.compile(r'\b(Continue reading|Read more|Read the full (story|article))\b.*$', re.I),
    re.compile(r'\[(…|\.\.\.|&#8230;)\]\s*$'),
    re.compile(r'^(Introduction|Overview|Executive Summary|Summary|Background)\s*:?\s+(?=[A-Z])'),
]
_SENTENCE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9“"\'(])')
SUMMARY_MAX = 420


def clean(value):
    """Strip markup and control characters, collapse whitespace.

    Output is always rendered with textContent on the client, so this is for
    readability, not an XSS defence.
    """
    text = html.unescape(value or '')
    text = _TAG.sub(' ', text)
    text = _CONTROL.sub(' ', text)
    text = _WS.sub(' ', text).strip()
    return _SPACE_BEFORE_PUNCT.sub(r'\1', text)


def title_key(title):
    """Normalised key used to de-duplicate the same headline from one source."""
    return ' '.join(re.findall(r'[a-z0-9]+', title.lower()))[:200]


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0].rstrip(',;:—-') + '…'


def extractive_summary(title, raw, limit=SUMMARY_MAX):
    """Return 1–3 leading sentences of a feed abstract, or None if unusable."""
    text = clean(raw)
    for pattern in _BOILERPLATE:
        text = pattern.sub('', text).strip()
    if not text:
        return None
    t_key = title_key(title)
    sentences = [s.strip() for s in _SENTENCE.split(text) if s.strip()]
    picked = []
    for sentence in sentences:
        if title_key(sentence) == t_key:
            continue
        picked.append(sentence)
        if len(' '.join(picked)) >= 220 or len(picked) == 3:
            break
    summary = truncate(' '.join(picked), limit)
    # Google News descriptions are often just "headline  Publisher".
    words = set(re.findall(r'[a-z0-9]+', summary.lower()))
    title_words = set(re.findall(r'[a-z0-9]+', title.lower()))
    if len(summary) < 60 or (words and len(words - title_words) <= 3):
        return None
    return summary


def headline_summary(title, source):
    return (f'{title.rstrip(". ")}. {source} published this item without an abstract in its '
            'feed, and the article page did not expose a description — open the source for '
            'full details.')


class _PageExtractor(HTMLParser):
    """Collect meta descriptions and paragraph text from an article page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.paragraphs = []
        self._in_p = 0
        self._skip = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or '').lower()
            if key in ('og:description', 'description', 'twitter:description') and a.get('content'):
                self.meta.setdefault(key, a['content'])
        elif tag in ('script', 'style', 'noscript', 'nav', 'footer', 'aside'):
            self._skip += 1
        elif tag == 'p' and not self._skip:
            self._in_p += 1
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'nav', 'footer', 'aside') and self._skip:
            self._skip -= 1
        elif tag == 'p' and self._in_p:
            self._in_p -= 1
            text = clean(''.join(self._buf))
            if len(text) >= 80:
                self.paragraphs.append(text)

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


def page_excerpt(html_bytes):
    """Return (description, body_text) extracted from an article page."""
    parser = _PageExtractor()
    try:
        parser.feed(html_bytes.decode('utf-8', 'replace'))
    except Exception:  # HTMLParser is lenient, but never let a bad page abort collection
        pass
    description = ''
    for key in ('og:description', 'description', 'twitter:description'):
        candidate = clean(parser.meta.get(key, ''))
        if len(candidate) >= 80:
            description = candidate
            break
    body = ' '.join(parser.paragraphs)[:6000]
    return description, body
