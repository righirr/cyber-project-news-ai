"""v4.2: the log line names the publication days, and the chart can count by collection day."""
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.refreshlog import RefreshLog
from pulse.store import Store, iso

from tests.fixtures import LONG, FakeNet, NoAI, rss
from tests.test_store_collector import article


def local_day(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).astimezone().date().isoformat()


class PublishedDaysInLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'refresh.log'

    def tearDown(self):
        self.tmp.cleanup()

    def test_line_lists_publication_days_in_order(self):
        RefreshLog(self.path).job({
            'started_at': '2026-10-17T05:00:00Z', 'finished_at': '2026-10-17T05:00:04Z', 'status': 'done',
            'scope': 'scheduled', 'added': 16, 'error': None,
            'published': [('2026-10-15', 1), ('2026-10-16', 15)],
            'sources': [{'name': 'The Hacker News', 'state': 'done', 'new': 16, 'error': None}]})
        line = self.path.read_text()
        self.assertIn('| done | 16 new articles (published Oct 15: 1, Oct 16: 15) | 4.0 s |', line)

    def test_no_breakdown_when_nothing_was_added(self):
        RefreshLog(self.path).job({
            'started_at': '2026-10-17T05:00:00Z', 'finished_at': '2026-10-17T05:00:02Z', 'status': 'done',
            'scope': 'all', 'added': 0, 'error': None, 'published': [], 'sources': []})
        self.assertIn('| done | 0 new articles | 2.0 s |', self.path.read_text())

    def test_real_refresh_writes_the_breakdown_and_it_adds_up(self):
        store = Store(Path(self.tmp.name) / 'r.db')
        store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                             'max_items': 10, 'topic_filter': False}], store.settings())
        ages = [1, 2, 30, 31, 60]  # hours ago: today/yesterday/two days ago, depending on the clock
        net = FakeNet({'https://blog.example/': (rss([(f'Malware story {i}', f'https://blog.example/{i}', age, LONG)
                                                      for i, age in enumerate(ages)]), 'application/rss+xml')})
        log = RefreshLog(self.path)
        jobs = Jobs(Collector(store, NoAI(), fetcher=net), store, refresh_log=log)
        job = jobs.start(store.get_sources(), 7, 'all')
        deadline = time.time() + 10
        while jobs.get(job['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.02)
        expected = {}
        for age in ages:
            expected[local_day(age)] = expected.get(local_day(age), 0) + 1
        self.assertEqual(store.published_days(job['run']), sorted(expected.items()))
        line = log.tail(1)[0]
        parts = ', '.join(f"{datetime.fromisoformat(d):%b %d}: {n}" for d, n in sorted(expected.items()))
        self.assertIn(f'5 new articles (published {parts})', line)
        self.assertEqual(jobs.get(job['id'])['published'], sorted(expected.items()))


class CollectedHistogramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'c.db')
        now = datetime.now(timezone.utc)
        # (url, title, published hours ago, collected hours ago, topic, source)
        rows = [('https://a/1', 'Fresh zero-day', 3, 2, 'Vulnerability', 'THN'),
                ('https://a/2', 'Yesterday ransomware', 26, 2, 'Threat Intel', 'THN'),      # published earlier, collected now
                ('https://a/3', 'Old backdated advisory', 24 * 40, 1, 'Threat Intel', 'Krebs'),  # very late arrival
                ('https://a/4', 'Collected long ago', 24 * 20, 24 * 20, 'AI Security', 'Krebs')]
        self.store.upsert_articles([article(u, t, hours_ago=p, topic=tp, source=s) for u, t, p, _, tp, s in rows])
        db = sqlite3.connect(self.store.path)
        for url, _, _, collected, _, _ in rows:
            db.execute('UPDATE articles SET collected_at=? WHERE url=?', (iso(now - timedelta(hours=collected)), url))
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def total(buckets, topic=None):
        return sum(b['count'] for b in buckets if topic is None or b['topic'] == topic)

    def test_time_window_applies_to_collection_date(self):
        week = self.store.search(time_range='7d')
        self.assertEqual(week['total'], 2)                                # list: published in the last 7 days
        self.assertEqual(self.total(week['histogram']), 2)                # "Published" chart = the list
        self.assertEqual(self.total(week['histogram_collected']), 3)      # "Collected" chart: saved in the last 7 days
        everything = self.store.search(time_range='all')
        self.assertEqual(self.total(everything['histogram_collected']), 4)

    def test_buckets_use_the_collection_hour(self):
        buckets = self.store.search(time_range='24h')['histogram_collected']
        expected_hours = {iso(datetime.now(timezone.utc) - timedelta(hours=h))[:13] for h in (1, 2)}
        self.assertEqual({b['hour'] for b in buckets}, expected_hours)

    def test_topic_filter_is_left_to_the_browser_other_filters_apply(self):
        by_topic = self.store.search(time_range='7d', topic='Vulnerability')['histogram_collected']
        self.assertEqual(self.total(by_topic), 3)                         # all topics kept for the legend
        self.assertEqual(self.total(by_topic, 'Vulnerability'), 1)
        by_source = self.store.search(time_range='7d', sources=['Krebs'])['histogram_collected']
        self.assertEqual(self.total(by_source), 1)
        by_text = self.store.search('ransomware', time_range='7d')['histogram_collected']
        self.assertEqual(self.total(by_text), 1)

    def test_only_sent_with_the_first_page(self):
        self.assertEqual(self.store.search(time_range='all', offset=1)['histogram_collected'], [])


if __name__ == '__main__':
    unittest.main()
