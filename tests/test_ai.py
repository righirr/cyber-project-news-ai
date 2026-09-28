import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pulse.ai import FALLBACK_BETA, Claude
from pulse.collector import Collector
from pulse.store import Store

from tests.fixtures import LONG, FakeNet, rss


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs['messages'][0]['content'].split('<articles>\n', 1)[1].rsplit('\n</articles>', 1)[0])
        return SimpleNamespace(stop_reason='end_turn',
                               content=[SimpleNamespace(type='text', text=json.dumps(self.reply(payload)))])


def fake_claude(reply):
    ai = Claude.__new__(Claude)
    ai.model, ai.enabled, ai.reason = 'claude-opus-5', True, 'enabled'
    ai.messages = FakeMessages(reply)
    ai._client = SimpleNamespace(beta=SimpleNamespace(messages=ai.messages))
    return ai


class ClaudeEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'a.db')
        self.store.save_sources([{'id': None, 'name': 'Mixed', 'url': 'https://mixed.example/', 'enabled': True,
                                  'max_items': 10, 'topic_filter': True}], {'lookback_days': 7, 'default_max_items': 5})
        self.net = FakeNet({'https://mixed.example/': (rss([
            ('Hackers exploit gateway flaw', 'https://mixed.example/1', 1, LONG),
            ('Security of your smart speaker', 'https://mixed.example/2', 2, LONG),
        ]), 'application/rss+xml')})

    def tearDown(self):
        self.tmp.cleanup()

    def test_request_shape_and_results_are_applied(self):
        def reply(items):
            return {'articles': [
                {'id': i['id'], 'summary': f'AI summary of {i["title"]}.', 'topic': 'Vulnerability',
                 'relevant': 'speaker' not in i['title']} for i in items]}
        ai = fake_claude(reply)
        Collector(self.store, ai, fetcher=self.net).collect(self.store.list_sources(), days=7)

        call = ai.messages.calls[0]
        self.assertEqual(call['model'], 'claude-opus-5')
        self.assertEqual(call['fallbacks'], 'default')
        self.assertEqual(call['betas'], [FALLBACK_BETA])
        self.assertEqual(call['output_config']['format']['type'], 'json_schema')
        self.assertIn('untrusted', call['system'])

        rows = self.store.search(time_range='all')['articles']
        self.assertEqual([r['title'] for r in rows], ['Hackers exploit gateway flaw'])  # irrelevant item dropped
        self.assertEqual(rows[0]['summary_kind'], 'ai')
        self.assertEqual(rows[0]['topic'], 'Vulnerability')

    def test_failed_ai_falls_back_to_extractive_summaries(self):
        ai = fake_claude(lambda items: {'articles': []})
        Collector(self.store, ai, fetcher=self.net).collect(self.store.list_sources(), days=7)
        kinds = {r['summary_kind'] for r in self.store.search(time_range='all')['articles']}
        self.assertEqual(kinds, {'feed'})


if __name__ == '__main__':
    unittest.main()
