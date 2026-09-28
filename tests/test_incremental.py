"""A refresh — manual or automatic — must bring only news that was never collected before."""
import tempfile
import time
import unittest
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.store import Store

from tests.fixtures import LONG, FakeNet, NoAI, rss


class IncrementalRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'i.db')
        self.store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                                  'max_items': 10, 'topic_filter': False}],
                                {'lookback_days': 7, 'default_max_items': 10})
        self.feed = [('Old malware story A', 'https://blog.example/a', 30, LONG),
                     ('Old malware story B', 'https://blog.example/b', 20, ''),  # no abstract: page fetch needed
                     ('Old malware story C', 'https://blog.example/c', 10, LONG)]
        self.net = FakeNet({})
        self.publish()
        self.net.pages['https://blog.example/b'] = (b'<meta name="description" content="' + LONG.encode() + b'">', 'text/html')
        self.jobs = Jobs(Collector(self.store, NoAI(), fetcher=self.net), self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self):
        self.net.pages['https://blog.example/'] = (rss(self.feed), 'application/rss+xml')

    def refresh(self, scope='all'):
        job = self.jobs.start(self.store.get_sources(), 7, scope)
        deadline = time.time() + 10
        while self.jobs.get(job['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.02)
        return self.jobs.get(job['id'])

    def test_second_refresh_brings_only_the_new_articles(self):
        first = self.refresh()
        self.assertEqual(first['added'], 3)

        # Two articles are published between refreshes.
        self.feed = [('Fresh ransomware story D', 'https://blog.example/d', 2, LONG),
                     ('Fresh phishing story E', 'https://blog.example/e', 1, LONG)] + self.feed
        self.publish()
        self.net.calls.clear()
        second = self.refresh('scheduled')  # automatic refresh uses the same rule as a manual one
        self.assertEqual(second['added'], 2)
        self.assertEqual(second['sources'][0]['new'], 2)
        self.assertEqual(self.store.storage_info()['articles'], 5)  # nothing duplicated
        # Known articles are not processed again: no page download for story B this time.
        self.assertNotIn('https://blog.example/b', self.net.calls)

        # The UI's "new from the last refresh" view returns exactly those two.
        new_only = self.store.search(time_range='all', run_id=second['run'])
        self.assertEqual(sorted(a['title'] for a in new_only['articles']),
                         ['Fresh phishing story E', 'Fresh ransomware story D'])

        third = self.refresh()
        self.assertEqual(third['added'], 0)
        self.assertEqual(self.store.get_meta('last_refresh')['added'], 0)

    def test_same_headline_under_a_new_url_is_not_new(self):
        self.refresh()
        # The publisher re-issues story B with tracking parameters in the link.
        self.feed[1] = ('Old malware story B', 'https://blog.example/b?utm_source=rss', 20, '')
        self.publish()
        self.net.calls.clear()
        self.assertEqual(self.refresh()['added'], 0)
        self.assertNotIn('https://blog.example/b?utm_source=rss', self.net.calls)  # recognised before any work

    def test_late_arriving_older_article_is_still_collected(self):
        """Why the rule is 'not stored yet' rather than 'published after the last refresh'."""
        self.refresh()
        # An item dated *before* the last refresh appears in the feed only now (delayed publishing).
        self.feed.append(('Backdated advisory F', 'https://blog.example/f', 26, LONG))
        self.publish()
        self.assertEqual(self.refresh()['added'], 1)


if __name__ == '__main__':
    unittest.main()
