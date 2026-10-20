"""v4.4: the daily news calendar (counts per day) and the one-day filter it opens."""
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.store import Store
from pulse.web import App, make_server

from tests.fixtures import NoAI
from tests.test_store_collector import article

# (url, title, published, collected, topic, source)
ROWS = [
    ('https://a/1', 'Gateway zero-day exploited', '2026-10-16T14:00:00Z', '2026-10-17T05:00:00Z', 'Vulnerability', 'THN'),
    ('https://a/2', 'Ransomware hits hospital', '2026-10-16T20:00:00Z', '2026-10-17T05:00:00Z', 'Threat Intel', 'THN'),
    ('https://a/3', 'Prompt injection in agents', '2026-10-17T09:00:00Z', '2026-10-18T05:00:00Z', 'AI Security', 'Krebs'),
    ('https://a/4', 'Late-night botnet', '2026-10-17T23:59:59Z', '2026-10-18T05:00:00Z', 'Threat Intel', 'Krebs'),
    ('https://a/5', 'Next day phishing wave', '2026-10-18T00:00:00Z', '2026-10-18T05:00:00Z', 'Threat Intel', 'THN'),
]
OCT17 = ('2026-10-17T00:00:00Z', '2026-10-18T00:00:00Z')  # one day, UTC for the test


def seeded_store(path):
    store = Store(path)
    store.upsert_articles([article(u, t, topic=tp, source=s) for u, t, _, _, tp, s in ROWS])
    db = sqlite3.connect(store.path)
    for url, _, published, collected, _, _ in ROWS:
        db.execute('UPDATE articles SET published_at=?, collected_at=? WHERE url=?', (published, collected, url))
    db.commit()
    db.close()
    return store


class DayFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = seeded_store(Path(self.tmp.name) / 'cal.db')

    def tearDown(self):
        self.tmp.cleanup()

    def titles(self, **kwargs):
        return sorted(a['title'] for a in self.store.search(time_range='all', **kwargs)['articles'])

    def test_published_day_includes_start_excludes_end(self):
        self.assertEqual(self.titles(day=(*OCT17, 'published')), ['Late-night botnet', 'Prompt injection in agents'])

    def test_collected_day(self):
        self.assertEqual(self.titles(day=(*OCT17, 'collected')), ['Gateway zero-day exploited', 'Ransomware hits hospital'])

    def test_day_combines_with_other_filters_and_feeds_facets_and_chart(self):
        result = self.store.search(time_range='all', day=(*OCT17, 'collected'), topic='Vulnerability')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['facets']['topics'], {'Vulnerability': 1, 'Threat Intel': 1})  # topic facet ignores topic
        self.assertEqual(sum(h['count'] for h in result['histogram']), 1)
        self.assertEqual(sum(h['count'] for h in result['histogram_collected']), 2)  # all topics, browser filters
        self.assertEqual(self.titles(day=(*OCT17, 'published'), sources=['THN']), [])
        self.assertEqual(self.titles(day=(*OCT17, 'published'), text='botnet'), ['Late-night botnet'])

    def test_calendar_counts_per_hour_for_both_dates(self):
        counts = self.store.calendar_counts('2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')
        self.assertEqual(sum(h['count'] for h in counts['published']), 5)
        self.assertEqual(sum(h['count'] for h in counts['collected']), 5)
        self.assertEqual({h['hour']: h['count'] for h in counts['collected']}, {'2026-10-17T05': 2, '2026-10-18T05': 3})
        empty = self.store.calendar_counts('2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z')
        self.assertEqual(empty, {'published': [], 'collected': []})


class CalendarApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = seeded_store(Path(cls.tmp.name) / 'api.db')
        cls.server = make_server(App(store, NoAI(), Jobs(Collector(store, NoAI()), store)), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        conn.request('GET', path, headers={'Host': 'localhost'})
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        return response.status, body

    def test_calendar_endpoint(self):
        status, body = self.get('/api/calendar?start=2026-10-01T03:00:00Z&end=2026-11-01T03:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual(sum(h['count'] for h in body['published']), 5)

    def test_articles_day_filter_over_http(self):
        status, body = self.get('/api/articles?range=all&day_start=2026-10-17T00:00:00Z'
                                '&day_end=2026-10-18T00:00:00Z&day_basis=collected')
        self.assertEqual((status, body['total']), (200, 2))

    def test_bad_input_is_400(self):
        for path in ['/api/calendar?start=2026-10-01&end=2026-11-01',                         # not instants
                     '/api/calendar?start=2026-10-01T00:00:00Z&end=2027-10-01T00:00:00Z',     # too long
                     '/api/calendar?start=2026-11-01T00:00:00Z&end=2026-10-01T00:00:00Z',     # backwards
                     '/api/articles?day_start=2026-10-17T00:00:00Z&day_end=2026-10-25T00:00:00Z',
                     '/api/articles?day_start=2026-10-17T00:00:00Z&day_end=2026-10-18T00:00:00Z&day_basis=x',
                     "/api/articles?day_start=2026-10-17T00:00:00Z'--&day_end=2026-10-18T00:00:00Z"]:
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 400)


if __name__ == '__main__':
    unittest.main()
