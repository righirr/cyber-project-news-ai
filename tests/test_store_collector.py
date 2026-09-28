import tempfile
import time
import unittest
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.store import DEFAULT_SOURCES, Store, fts_query, now_iso
from pulse.text import title_key

from tests.fixtures import HTML_WITH_FEED, LONG, FakeNet, NoAI, rss


def article(url, title, hours_ago=1, summary=LONG, kind='feed', topic='Vulnerability', source='Blog'):
    from datetime import datetime, timedelta, timezone
    from pulse.store import iso
    return {'source_id': None, 'source_name': source, 'url': url, 'title': title, 'title_key': title_key(title),
            'summary': summary, 'summary_kind': kind, 'topic': topic,
            'published_at': iso(datetime.now(timezone.utc) - timedelta(hours=hours_ago))}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_seeds_default_sources_once(self):
        self.assertEqual(len(self.store.list_sources()), len(DEFAULT_SOURCES))
        self.store.save_sources([], {'lookback_days': 3, 'default_max_items': 5})
        self.assertEqual(Store(self.store.path).list_sources(), [])  # not re-seeded after deliberate removal
        self.assertEqual(self.store.settings()['lookback_days'], 3)

    def test_dedupes_by_url_and_title_and_upgrades_summaries(self):
        self.assertEqual(self.store.upsert_articles([article('https://a/1', 'Same headline', kind='headline')]), 1)
        self.assertEqual(self.store.upsert_articles([article('https://a/1', 'Same headline')]), 0)
        self.assertEqual(self.store.upsert_articles([article('https://a/1?utm=x', 'Same headline!')]), 0)
        row = self.store.search(time_range='all')['articles'][0]
        self.assertEqual(row['summary_kind'], 'feed')

    def test_search_ranges_topics_sources_and_highlights(self):
        self.store.upsert_articles([
            article('https://a/1', 'Citrix NetScaler zero-day exploited', hours_ago=2, source='THN'),
            article('https://a/2', 'Ransomware gang hits hospital', hours_ago=24 * 10, topic='Threat Intel', source='Krebs',
                    summary='A ransomware gang encrypted systems at a regional hospital, disrupting care for days.'),
            article('https://a/3', 'Old story about CVE-2020-1234', hours_ago=24 * 40, source='THN'),
        ])
        self.assertEqual(self.store.search(time_range='24h')['total'], 1)
        self.assertEqual(self.store.search(time_range='15d')['total'], 2)
        self.assertEqual(self.store.search(time_range='all')['total'], 3)
        self.assertEqual(self.store.search(time_range='all', topic='Threat Intel')['total'], 1)
        self.assertEqual(self.store.search(time_range='all', sources=['THN'])['total'], 2)
        self.assertEqual(self.store.search('exploit', 'all')['total'], 2)  # stemming: exploit ~ exploited/exploiting
        hit = self.store.search('netscaler', 'all')
        self.assertEqual(hit['total'], 1)
        self.assertIn('\x02', hit['articles'][0]['title_hl'])
        self.assertEqual(self.store.search('krebs', 'all')['total'], 1)  # source names are indexed
        self.assertEqual(self.store.search('CVE-2020-1234', 'all')['total'], 1)
        self.assertEqual(self.store.search('"regional hospital"', 'all')['total'], 1)
        self.assertEqual(self.store.search('citrix OR ransomware', 'all')['total'], 2)
        facets = self.store.search(time_range='all', topic='Threat Intel')['facets']
        self.assertEqual(facets['topics']['Vulnerability'], 2)  # topic facet ignores the topic filter

    def test_hostile_queries_are_safe(self):
        for q in ['"', 'AND', 'OR', 'NEAR(', '*', 'title:x', "'; DROP TABLE articles; --", '^^^', 'a OR']:
            with self.subTest(q=q):
                self.store.search(q, 'all')

    def test_fts_query(self):
        self.assertEqual(fts_query('zero day'), '"zero"* "day"*')
        self.assertEqual(fts_query('"zero day" CVE-2026-1'), '"zero day" "CVE 2026 1"')
        self.assertEqual(fts_query('OR a OR OR b OR'), '"a"* OR "b"*')

    def test_rename_source_updates_articles(self):
        src = self.store.list_sources()[0]
        self.store.upsert_articles([{**article('https://a/9', 'Some story'), 'source_id': src['id'],
                                     'source_name': src['name']}])
        renamed = {**src, 'name': 'Renamed'}
        self.store.save_sources([renamed], self.store.settings())
        self.assertEqual(self.store.search('renamed', 'all')['total'], 1)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 't.db')
        self.store.save_sources([
            {'id': None, 'name': 'Direct', 'url': 'https://direct.example/', 'enabled': True, 'max_items': 2, 'topic_filter': True},
            {'id': None, 'name': 'Discover', 'url': 'https://disc.example/blog/', 'enabled': True, 'max_items': 5, 'topic_filter': False},
            {'id': None, 'name': 'Broken', 'url': 'https://broken.example/', 'enabled': False, 'max_items': 5, 'topic_filter': True},
        ], {'lookback_days': 7, 'default_max_items': 5})
        self.net = FakeNet({
            'https://direct.example/': (rss([
                ('Gateway flaw exploited', 'https://direct.example/1', 1, LONG),
                ('Best party speakers of the year', 'https://direct.example/2', 2, 'Great sound for your summer parties and more fun.'),
                ('Old ransomware story', 'https://direct.example/3', 24 * 30, LONG),
                ('Botnet grows', 'https://direct.example/4', 3, ''),
                ('Another malware wave', 'https://direct.example/5', 4, LONG),
            ]), 'application/rss+xml'),
            'https://direct.example/4': (b'<meta name="description" content="' + LONG.encode() + b'">', 'text/html'),
            'https://disc.example/blog/': (HTML_WITH_FEED, 'text/html'),
            'https://disc.example/custom-feed.xml': (rss([('Cloud outage post-mortem', 'https://disc.example/p', 5, LONG)]),
                                                     'application/xml'),
        })
        self.collector = Collector(self.store, NoAI(), fetcher=self.net)

    def tearDown(self):
        self.tmp.cleanup()

    def sources(self, *names):
        return [s for s in self.store.list_sources() if s['name'] in names]

    def test_collects_filters_limits_and_summarises(self):
        added = self.collector.collect(self.sources('Direct', 'Discover'), days=7)
        self.assertEqual(sum(added.values()), 3)
        titles = {a['title']: a for a in self.store.search(time_range='all')['articles']}
        self.assertNotIn('Best party speakers of the year', titles)  # off-topic filtered
        self.assertNotIn('Old ransomware story', titles)  # outside look-back window
        self.assertEqual(titles['Botnet grows']['summary_kind'], 'page')  # summary from the article page
        self.assertEqual(titles['Cloud outage post-mortem']['summary_kind'], 'feed')
        discover = self.sources('Discover')[0]
        self.assertEqual(discover['feed_url'], 'https://disc.example/custom-feed.xml')  # remembered

    def test_single_source_refresh_only_touches_that_source(self):
        self.collector.collect(self.sources('Discover'), days=7)
        self.assertFalse(any('direct.example' in u for u in self.net.calls))
        self.assertEqual({a['source_name'] for a in self.store.search(time_range='all')['articles']}, {'Discover'})

    def test_failing_source_is_reported_not_fatal(self):
        updates = []
        added = self.collector.collect(self.sources('Broken', 'Discover'), days=7,
                                       on_update=lambda sid, **f: updates.append(f))
        self.assertEqual(sum(added.values()), 1)
        self.assertTrue(any(u.get('state') == 'error' for u in updates))
        self.assertIn('Google News', self.sources('Broken')[0]['last_error'])

    def test_second_run_adds_nothing(self):
        self.collector.collect(self.sources('Direct'), days=7)
        self.assertEqual(sum(self.collector.collect(self.sources('Direct'), days=7).values()), 0)

    def test_jobs_run_in_background_and_reject_overlap(self):
        jobs = Jobs(self.collector, self.store)
        job = jobs.start(self.sources('Direct'), 7, 'selected')
        deadline = time.time() + 10
        while jobs.get(job['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.05)
        done = jobs.get(job['id'])
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['added'], 2)
        self.assertEqual(done['sources'][0]['state'], 'done')
        self.assertEqual(self.store.get_meta('last_refresh')['sources'], ['Direct'])
        self.assertLessEqual(self.store.get_meta('last_refresh')['at'], now_iso())


if __name__ == '__main__':
    unittest.main()
