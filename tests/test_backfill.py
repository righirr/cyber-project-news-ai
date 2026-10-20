"""v4.3: backfilling a source's last N days by following its feed pages."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.refreshlog import RefreshLog
from pulse.store import DEFAULT_SOURCES, Store

from tests.fixtures import LONG, FakeNet, NoAI, rss

ROOT = Path(__file__).resolve().parent.parent
FEED = 'https://news.example/feed/'


def paged_feed(per_page=10, pages=6, hours_apart=6):
    """A busy WordPress-style site: page N holds older items than page N-1."""
    net, age = FakeNet({}), 1
    for page in range(1, pages + 1):
        items = []
        for _ in range(per_page):
            items.append((f'Malware story {age}', f'https://news.example/{age}', age, LONG))
            age += hours_apart
        url = FEED if page == 1 else f'{FEED}?paged={page}'
        net.pages[url] = (rss(items), 'application/rss+xml')
    return net


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'b.db')
        self.store.save_sources([{'id': None, 'name': 'Busy News', 'url': 'https://news.example/', 'enabled': True,
                                  'max_items': 10, 'topic_filter': True}], self.store.settings())
        self.store.record_source_check(self.store.list_sources()[0]['id'], FEED, None)  # feed already known
        self.log = RefreshLog(Path(self.tmp.name) / 'refresh.log')

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, net, days, backfill):
        jobs = Jobs(Collector(self.store, NoAI(), fetcher=net, page_delay=0), self.store, refresh_log=self.log)
        job = jobs.start(self.store.list_sources(), days, 'backfill' if backfill else 'all', backfill=backfill)
        deadline = time.time() + 10
        while jobs.get(job['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.02)
        return jobs.get(job['id'])

    def test_follows_pages_until_the_window_and_ignores_the_per_refresh_cap(self):
        net = paged_feed()           # 60 items, one every 6 h: 15 days = items 1..60
        job = self.run_job(net, days=10, backfill=True)
        self.assertEqual(job['status'], 'done')
        within = [h for h in range(1, 361, 6) if h < 10 * 24]
        self.assertEqual(job['added'], len(within))                       # 40, far above max_items=10
        self.assertIn(f'{FEED}?paged=4', net.calls)
        self.assertNotIn(f'{FEED}?paged=6', net.calls)                    # stopped once past the window

    def test_a_normal_refresh_still_takes_only_max_items_from_page_one(self):
        net = paged_feed()
        job = self.run_job(net, days=10, backfill=False)
        self.assertEqual(job['added'], 10)
        self.assertFalse(any('paged=' in url for url in net.calls))

    def test_stops_when_pages_run_out_or_repeat(self):
        net = paged_feed(pages=2)                                         # page 3 is a 404
        self.assertEqual(self.run_job(net, days=30, backfill=True)['added'], 20)
        repeating = FakeNet({FEED: paged_feed(pages=1).pages[FEED]})
        repeating.pages[f'{FEED}?paged=2'] = repeating.pages[FEED]        # a feed that ignores ?paged=
        self.store.upsert_articles([])  # (fresh source list is reused)
        job = self.run_job(repeating, days=30, backfill=True)
        self.assertEqual(job['added'], 0)                                 # all 10 already stored
        self.assertNotIn(f'{FEED}?paged=3', repeating.calls)

    def test_backfill_is_logged_tagged_and_brings_only_new_articles(self):
        net = paged_feed()
        first = self.run_job(net, days=3, backfill=True)
        line = self.log.tail(1)[0]
        self.assertIn(f'| backfill (3 days) | done | {first["added"]} new articles (published ', line)
        self.assertEqual(self.store.search(time_range='all', run_id=first['run'])['total'], first['added'])
        self.assertEqual(self.run_job(net, days=3, backfill=True)['added'], 0)   # nothing twice


class BackfillCommandTests(unittest.TestCase):
    def test_unknown_source_and_bad_days_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'c.db'
            env = {'PULSE_AI': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}
            run = lambda *a: subprocess.run([sys.executable, 'server.py', '--db', str(db), *a], cwd=ROOT, env=env,
                                            capture_output=True, text=True, timeout=30)
            missing = run('--backfill', 'No Such Blog')
            self.assertEqual(missing.returncode, 2)
            self.assertIn('Cyber Security News', missing.stdout)            # lists the available sources
            self.assertEqual(run('--backfill', 'Cyber Security News', '--days', '45').returncode, 2)

    def test_new_source_is_a_default(self):
        self.assertIn(('Cyber Security News', 'https://cybersecuritynews.com/', 'https://cybersecuritynews.com/feed/'),
                      DEFAULT_SOURCES)


if __name__ == '__main__':
    unittest.main()
