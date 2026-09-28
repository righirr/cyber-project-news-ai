"""Automatic daily refresh: timing, catch-up, settings changes and busy handling."""
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pulse.collector import Busy, Collector, Jobs
from pulse.scheduler import DailyScheduler, latest_slot, next_slot_after, parse_hhmm
from pulse.store import Store

from tests.fixtures import LONG, FakeNet, NoAI, rss


class Clock:
    def __init__(self, start):
        self.value = start

    def __call__(self):
        return self.value

    def at(self, text):
        self.value = datetime.fromisoformat(text).astimezone()
        return self


class FakeJobs:
    """Records starts; can pretend a manual refresh is running."""

    def __init__(self):
        self.started = []
        self.busy = False

    def start(self, sources, days, scope):
        if self.busy:
            raise Busy('busy')
        self.started.append((sorted(s['name'] for s in sources), days, scope))
        return {'id': str(len(self.started)), 'started_at': 'now', 'status': 'running'}

    def get(self, job_id):
        return {'id': job_id, 'status': 'done', 'finished_at': 'later', 'added': 3, 'sources': []}


class SlotMathTests(unittest.TestCase):
    def test_slots(self):
        at = parse_hhmm('02:00')
        now = datetime(2026, 9, 27, 18, 0).astimezone()
        self.assertEqual(latest_slot(now, at).strftime('%d %H:%M'), '27 02:00')
        self.assertEqual(next_slot_after(now, at).strftime('%d %H:%M'), '28 02:00')
        early = datetime(2026, 9, 27, 1, 0).astimezone()
        self.assertEqual(latest_slot(early, at).strftime('%d %H:%M'), '26 02:00')
        self.assertEqual(next_slot_after(early, at).strftime('%d %H:%M'), '27 02:00')


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 's.db')
        self.clock = Clock(None).at('2026-09-27T18:00:00')
        self.jobs = FakeJobs()
        self.scheduler = DailyScheduler(self.store, self.jobs, now=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_on_at_2am(self):
        settings = self.store.settings()
        self.assertTrue(settings['auto_refresh'])
        self.assertEqual(settings['auto_refresh_time'], '02:00')

    def test_first_start_waits_for_next_2am_then_runs_once_with_settings(self):
        self.scheduler.initialize()
        self.assertIsNone(self.scheduler.run_if_due())  # 18:00: nothing yet, no immediate run
        self.assertEqual(self.scheduler.status()['next_run'][:16], '2026-09-28T02:00')
        self.clock.at('2026-09-28T01:59:00')
        self.assertIsNone(self.scheduler.run_if_due())
        self.clock.at('2026-09-28T02:00:05')
        self.assertIsNotNone(self.scheduler.run_if_due())
        names, days, scope = self.jobs.started[0]
        self.assertEqual(scope, 'scheduled')
        self.assertEqual(days, self.store.settings()['lookback_days'])
        self.assertEqual(len(names), 6)  # all enabled default sources
        self.clock.at('2026-09-28T09:00:00')
        self.assertIsNone(self.scheduler.run_if_due())  # only once per day
        self.assertEqual(self.scheduler.status()['next_run'][:16], '2026-09-29T02:00')

    def test_only_enabled_sources(self):
        sources = self.store.list_sources()
        sources[0]['enabled'] = False
        self.store.save_sources(sources, self.store.settings())
        self.scheduler.initialize()
        self.clock.at('2026-09-28T02:01:00')
        self.scheduler.run_if_due()
        self.assertNotIn(sources[0]['name'], self.jobs.started[0][0])
        self.assertEqual(len(self.jobs.started[0][0]), 5)

    def test_missed_run_is_caught_up_once_after_restart(self):
        self.scheduler.initialize()
        self.clock.at('2026-10-01T15:00:00')  # app was off for several 02:00s
        restarted = DailyScheduler(self.store, self.jobs, now=self.clock)
        restarted.initialize()
        self.assertIsNotNone(restarted.run_if_due())
        self.assertIsNone(restarted.run_if_due())
        self.assertEqual(len(self.jobs.started), 1)
        self.assertEqual(restarted.status()['next_run'][:16], '2026-10-02T02:00')

    def test_changing_settings_never_runs_immediately(self):
        self.scheduler.initialize()
        settings = {**self.store.settings(), 'auto_refresh_time': '14:00'}  # already past today at 18:00
        self.store.save_sources(self.store.list_sources(), settings)
        self.scheduler.settings_changed()
        self.assertIsNone(self.scheduler.run_if_due())
        self.assertEqual(self.scheduler.status()['next_run'][:16], '2026-09-28T14:00')

    def test_disabled(self):
        self.store.save_sources(self.store.list_sources(), {**self.store.settings(), 'auto_refresh': False})
        self.scheduler.initialize()
        self.clock.at('2026-09-29T02:00:00')
        self.assertIsNone(self.scheduler.run_if_due())
        self.assertIsNone(self.scheduler.status()['next_run'])

    def test_busy_manual_refresh_is_retried_later(self):
        self.scheduler.initialize()
        self.clock.at('2026-09-28T02:00:00')
        self.jobs.busy = True
        self.assertIsNone(self.scheduler.run_if_due())
        self.assertEqual(self.scheduler.status()['next_run'][:16], '2026-09-28T02:05')
        self.jobs.busy = False
        self.clock.at('2026-09-28T02:05:00')
        self.assertIsNotNone(self.scheduler.run_if_due())

    def test_result_is_recorded_and_interrupted_run_is_marked(self):
        self.scheduler.initialize()
        self.clock.at('2026-09-28T02:00:00')
        job = self.scheduler.run_if_due()
        self.assertEqual(self.scheduler.status()['last_run']['status'], 'running')
        DailyScheduler(self.store, self.jobs, now=self.clock).initialize()  # restart mid-run
        self.assertEqual(self.scheduler.status()['last_run']['status'], 'interrupted')
        self.assertTrue(self.scheduler.record_result(job['id']))
        self.assertEqual(self.scheduler.status()['last_run']['added'], 3)


class SchedulerEndToEndTests(unittest.TestCase):
    def test_background_thread_collects_respecting_article_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'e.db')
            store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                                 'max_items': 2, 'topic_filter': False}],
                               {'lookback_days': 7, 'default_max_items': 5, 'auto_refresh': True,
                                'auto_refresh_time': '02:00'})
            net = FakeNet({'https://blog.example/': (rss([(f'Malware story {i}', f'https://blog.example/{i}', i + 1, LONG)
                                                          for i in range(5)]), 'application/rss+xml')})
            jobs = Jobs(Collector(store, NoAI(), fetcher=net), store)
            clock = Clock(None).at('2026-09-27T18:00:00')
            scheduler = DailyScheduler(store, jobs, now=clock, startup_delay=0)
            scheduler.initialize()
            clock.at('2026-09-28T02:00:01')
            scheduler.start()
            deadline = time.time() + 10
            while time.time() < deadline and (scheduler.status()['last_run'] or {}).get('status') != 'done':
                time.sleep(0.05)
            scheduler.stop()
            self.assertEqual(scheduler.status()['last_run']['status'], 'done')
            self.assertEqual(store.storage_info()['articles'], 2)  # max_items respected
            self.assertEqual(store.get_meta('last_refresh')['scope'], 'scheduled')


if __name__ == '__main__':
    unittest.main()
