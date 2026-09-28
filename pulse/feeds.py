"""RSS / Atom parsing, feed discovery and the Google News fallback query."""
import email.utils
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser

from .text import clean

GOOGLE_NEWS_KEYWORDS = ('cybersecurity OR security OR malware OR ransomware OR vulnerability '
                        'OR hackers OR breach OR "artificial intelligence" OR "threat intelligence"')
COMMON_FEED_PATHS = ['/feed/', '/rss/', '/feed.xml', '/rss.xml', '/atom.xml', '/index.xml']
_DATE_TAGS = ('pubdate', 'published', 'updated', 'date', 'issued', 'modified')


def _local(tag):
    return tag.rsplit('}', 1)[-1].lower() if isinstance(tag, str) else ''


def parse_date(value):
    value = clean(value)
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_link(entry, base_url):
    candidates = []
    for child in entry:
        name = _local(child.tag)
        if name == 'link':
            href = child.attrib.get('href')
            rel = child.attrib.get('rel', 'alternate')
            if href and rel == 'alternate':
                candidates.insert(0, href)
            elif href:
                candidates.append(href)
            elif child.text and child.text.strip():
                candidates.insert(0, child.text.strip())
        elif name in ('guid', 'id') and (child.text or '').strip().startswith('http'):
            if child.attrib.get('isPermaLink', 'true') != 'false':
                candidates.append(child.text.strip())
    for link in candidates:
        absolute = urllib.parse.urljoin(base_url, clean(link))
        if urllib.parse.urlsplit(absolute).scheme in ('http', 'https'):
            return absolute
    return ''


def parse_feed(data, base_url):
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom. Raises ValueError if not a feed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('Not a valid RSS or Atom document') from exc
    if _local(root.tag) not in ('rss', 'feed', 'rdf'):
        raise ValueError('Not an RSS or Atom document')
    entries = []
    for node in root.iter():
        if _local(node.tag) not in ('item', 'entry'):
            continue
        fields = {}
        publisher = ''
        for child in node:
            name = _local(child.tag)
            text = ''.join(child.itertext())
            if name == 'source':
                publisher = clean(text)
            elif name not in fields and text.strip():
                fields[name] = text
        title = clean(fields.get('title', ''))
        if not title:
            continue
        abstract = fields.get('description') or fields.get('summary') or ''
        content = fields.get('encoded') or fields.get('content') or ''
        if len(clean(abstract)) < 80 and content:
            abstract = content
        published = next((parse_date(fields[t]) for t in _DATE_TAGS if t in fields), None)
        entries.append({
            'title': title,
            'link': _entry_link(node, base_url),
            'text': abstract,
            'published': published,
            'publisher': publisher,
        })
    return entries


class _FeedLinkFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != 'link':
            return
        a = {k.lower(): (v or '') for k, v in attrs}
        kind = a.get('type', '').lower()
        if 'alternate' in a.get('rel', '').lower() and kind in (
                'application/rss+xml', 'application/atom+xml', 'application/rdf+xml') and a.get('href'):
            self.links.append(a['href'])


def discover_feeds(html_bytes, base_url):
    """Find <link rel="alternate"> feed URLs advertised by an HTML page."""
    finder = _FeedLinkFinder()
    try:
        finder.feed(html_bytes[:400_000].decode('utf-8', 'replace'))
    except Exception:
        pass
    return [urllib.parse.urljoin(base_url, href) for href in finder.links][:3]


def site_url(value):
    """Normalise a user-entered website or feed address to an https URL."""
    value = value.strip()
    if not re.match(r'^[a-z][a-z0-9+.-]*://', value, re.I):
        value = 'https://' + value
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(f'Invalid website address: {value}')
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or '/', parts.query, ''))


def feed_candidates(url):
    """Fallback feed URLs to try for a site that did not advertise one."""
    parts = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))
    return [base + path for path in COMMON_FEED_PATHS]


def google_news_url(url, days):
    parts = urllib.parse.urlsplit(url)
    scope = (parts.hostname or '') + parts.path.rstrip('/')
    query = f'({GOOGLE_NEWS_KEYWORDS}) site:{scope} when:{int(days)}d'
    return 'https://news.google.com/rss/search?' + urllib.parse.urlencode(
        {'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})


def strip_publisher_suffix(title, publisher):
    """Google News appends ' - Publisher' to headlines."""
    if publisher and title.lower().endswith(' - ' + publisher.lower()):
        return title[:-(len(publisher) + 3)].strip()
    return title
