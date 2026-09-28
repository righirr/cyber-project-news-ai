"""Shared test fixtures: sample feeds and a fake network."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from pulse import netguard


def rfc822(hours_ago):
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=hours_ago))


def rss(items):
    body = ''.join(
        f'<item><title>{t}</title><link>{link}</link><pubDate>{rfc822(age)}</pubDate>'
        f'<description><![CDATA[{desc}]]></description></item>'
        for t, link, age, desc in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


ATOM = f'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Atom &amp; Friends: New Botnet Targets Routers</title>
<link rel="replies" href="https://blog.example.com/p/1#comments"/>
<link rel="alternate" href="/p/1"/>
<updated>{datetime.now(timezone.utc).isoformat()}</updated>
<summary type="html">&lt;p&gt;Researchers found a new botnet that compromises home routers through default credentials. It spreads quickly across exposed devices worldwide.&lt;/p&gt;</summary>
</entry></feed>'''.encode()

HTML_WITH_FEED = b'''<html><head><link rel="alternate" type="application/rss+xml" href="/custom-feed.xml">
<title>Blog</title></head><body>hi</body></html>'''

LONG = ('Attackers are actively exploiting a critical remote code execution flaw in the widely used '
        'Example Gateway appliance. The vendor has released patches and urges customers to update now.')


class FakeNet:
    """Maps URLs to (body, content_type); anything else is a 404."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, timeout=12, max_bytes=None, accept='*/*'):
        self.calls.append(url)
        if url not in self.pages:
            raise netguard.FetchError(f'HTTP 404 from {url}')
        body, ctype = self.pages[url]
        return url, body, ctype


class NoAI:
    enabled = False
    model = None

    def status(self):
        return {'enabled': False, 'model': None, 'reason': 'test'}

    def enrich(self, articles):
        return {}

    def briefing(self, articles):
        return None
