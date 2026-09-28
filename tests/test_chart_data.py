"""Data behind the "News per day" chart: same filters as the list, so the numbers always agree."""
import tempfile
import unittest
from pathlib import Path

from pulse.store import Store

from tests.test_store_collector import article


class ChartDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'c.db')
        self.store.upsert_articles([
            article('https://a/1', 'Zero-day in gateway', hours_ago=2, topic='Vulnerability', source='THN'),
            article('https://a/2', 'Ransomware wave', hours_ago=2, topic='Threat Intel', source='THN'),
            article('https://a/3', 'Prompt injection study', hours_ago=30, topic='AI Security', source='Krebs'),
            article('https://a/4', 'Botnet takedown', hours_ago=24 * 10, topic='Threat Intel', source='Krebs'),
            article('https://a/5', 'Old breach', hours_ago=24 * 40, topic='Threat Intel', source='THN'),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def total(self, result):
        return sum(h['count'] for h in result['histogram'])

    def test_histogram_matches_the_list_for_every_range(self):
        for time_range in ('24h', '7d', '15d', '30d', 'all'):
            with self.subTest(range=time_range):
                result = self.store.search(time_range=time_range)
                self.assertEqual(self.total(result), result['total'])
        self.assertEqual(self.store.search(time_range='24h')['total'], 2)
        self.assertEqual(self.store.search(time_range='15d')['total'], 4)

    def test_counts_are_per_hour_and_topic(self):
        buckets = self.store.search(time_range='24h')['histogram']
        self.assertEqual({(b['topic'], b['count']) for b in buckets}, {('Vulnerability', 1), ('Threat Intel', 1)})
        self.assertTrue(all(len(b['hour']) == 13 for b in buckets))  # 'YYYY-MM-DDTHH' (UTC)

    def test_histogram_follows_topic_source_and_search_filters(self):
        for kwargs in ({'topic': 'Threat Intel'}, {'sources': ['Krebs']}, {'text': 'ransomware'}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                result = self.store.search(time_range='all', **kwargs)
                self.assertEqual(self.total(result), result['total'])
                self.assertGreater(result['total'], 0)

    def test_range_start_and_paging(self):
        self.assertIsNone(self.store.search(time_range='all')['range_start'])
        self.assertTrue(self.store.search(time_range='7d')['range_start'].endswith('Z'))
        self.assertEqual(self.store.search(time_range='all', offset=2)['histogram'], [])  # only sent with page 1


if __name__ == '__main__':
    unittest.main()
