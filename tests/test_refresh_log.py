"""Every refresh — manual or automatic — appends one text line to data/refresh.log."""
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pulse import refreshlog
from pulse.collector import Collector, Jobs
from pulse.refreshlog import RefreshLog
from pulse.scheduler import DailyScheduler
from pulse.store import Store

from tests.fixtures import LONG, FakeNet, NoAI, rss
from tests.test_scheduler import Clock, FakeJobs

LINE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+ \| (automatic|manual) refresh[^|]* \| ')


class RefreshLogFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'refresh.log'
        self.log = RefreshLog(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_job_line_has_time_count_sources_and_errors(self):
        self.log.job({'started_at': '2026-09-28T05:00:04Z', 'finished_at': '2026-09-28T05:00:12Z', 'status': 'done',
                      'scope': 'scheduled',
                      'added': 12, 'error': None, 'sources': [
                          {'name': 'WIRED Security', 'state': 'done', 'new': 3, 'error': None},
                          {'name': 'Dark Reading', 'state': 'error', 'new': 0, 'error': 'HTTP 503 from darkreading.com'}]})
        line = self.path.read_text().splitlines()[0]
        self.assertRegex(line, LINE)
        self.assertIn('| automatic refresh | done | 12 new articles | 8.0 s |', line)
        self.assertIn('WIRED Security: 3, Dark Reading: failed', line)
        self.assertIn('errors: Dark Reading: HTTP 503 from darkreading.com', line)

    def test_events_append_and_tail_is_newest_first(self):
        self.log.event('skipped', 'no enabled sources')
        self.log.event('postponed', 'a manual refresh was running | retrying at 02:05\nnext line')
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 2)  # one line each: newlines and separators in details are neutralised
        self.assertTrue(all(LINE.match(l) for l in lines))
        self.assertIn('| skipped | 0 new articles |', lines[0])
        self.assertIn('postponed', self.log.tail(1)[0])

    def test_log_survives_a_restart_and_keeps_appending(self):
        self.log.event('skipped', 'before the restart')
        restarted = RefreshLog(self.path)  # a new process / container using the same data folder
        self.assertIn('before the restart', restarted.tail(1)[0])
        restarted.event('postponed', 'after the restart')
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('before the restart', lines[0])
        self.assertIn('after the restart', lines[1])

    def test_missing_file_and_rotation(self):
        self.assertEqual(self.log.tail(), [])
        with mock.patch.object(refreshlog, 'MAX_BYTES', 50):
            for _ in range(3):
                self.log.event('skipped', 'no enabled sources')
        self.assertTrue(self.path.with_name('refresh.log.1').exists())
        self.assertEqual(len(self.path.read_text().splitlines()), 1)

    def test_unwritable_log_never_breaks_the_refresh(self):
        blocked = RefreshLog(Path(self.tmp.name) / 'missing-dir-is-a-file')
        (Path(self.tmp.name) / 'missing-dir-is-a-file').mkdir()  # a directory where the file should be
        blocked.event('skipped', 'x')  # logs a warning, does not raise


class SchedulerLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 's.db')
        self.log = RefreshLog(Path(self.tmp.name) / 'refresh.log')
        self.clock = Clock(None).at('2026-09-27T18:00:00')

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        return self.log.tail(100)[::-1]

    def test_postponed_automatic_run_is_logged(self):
        jobs = FakeJobs()
        scheduler = DailyScheduler(self.store, jobs, now=self.clock, refresh_log=self.log)
        scheduler.initialize()
        self.assertEqual(self.lines(), [])  # nothing happens (and nothing is logged) before 02:00
        self.clock.at('2026-09-28T02:00:00')
        jobs.busy = True
        scheduler.run_if_due()
        self.assertIn('postponed', self.lines()[0])

    def test_interrupted_run_is_logged_on_next_start(self):
        jobs = FakeJobs()
        scheduler = DailyScheduler(self.store, jobs, now=self.clock, refresh_log=self.log)
        scheduler.initialize()
        self.clock.at('2026-09-28T02:00:00')
        scheduler.run_if_due()
        DailyScheduler(self.store, jobs, now=self.clock, refresh_log=self.log).initialize()
        self.assertIn('interrupted', self.lines()[-1])

    def test_real_automatic_run_logs_exactly_one_line_with_the_new_count(self):
        self.store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                                  'max_items': 3, 'topic_filter': False}],
                                {'lookback_days': 7, 'default_max_items': 5, 'auto_refresh': True,
                                 'auto_refresh_time': '02:00'})
        net = FakeNet({'https://blog.example/': (rss([(f'Malware story {i}', f'https://blog.example/{i}', i + 1, LONG)
                                                      for i in range(5)]), 'application/rss+xml')})
        jobs = Jobs(Collector(self.store, NoAI(), fetcher=net), self.store, refresh_log=self.log)
        scheduler = DailyScheduler(self.store, jobs, now=self.clock, refresh_log=self.log)
        scheduler.initialize()
        self.clock.at('2026-09-28T02:00:01')
        job = scheduler.run_if_due()
        deadline = time.time() + 10
        while jobs.get(job['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.02)
        scheduler.record_result(job['id'])
        scheduler.flush()  # shutdown path must not log the same run twice
        scheduler.record_result(job['id'])
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertIn('| automatic refresh | done | 3 new articles |', lines[0])  # per-source maximum respected
        self.assertIn('Blog: 3', lines[0])

        # A manual refresh afterwards is logged too, with its kind and its own new count.
        self.feed_more(net)
        manual = jobs.start(self.store.get_sources(), 7, 'selected')
        deadline = time.time() + 10
        while jobs.get(manual['id'])['status'] == 'running' and time.time() < deadline:
            time.sleep(0.02)
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertIn('| manual refresh (selected sources) | done | 1 new article |', lines[1])

    def feed_more(self, net):
        net.pages['https://blog.example/'] = (rss([('Brand new malware story', 'https://blog.example/new', 0, LONG)]
                                                  + [(f'Malware story {i}', f'https://blog.example/{i}', i + 1, LONG)
                                                     for i in range(5)]), 'application/rss+xml')

    def test_refresh_cut_off_by_shutdown_is_logged_once(self):
        class Slow(FakeNet):
            def __call__(self, *args, **kwargs):
                time.sleep(0.5)
                return super().__call__(*args, **kwargs)
        self.store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                                  'max_items': 3, 'topic_filter': False}], self.store.settings())
        jobs = Jobs(Collector(self.store, NoAI(), fetcher=Slow({})), self.store, refresh_log=self.log)
        jobs.start(self.store.get_sources(), 7, 'all')
        jobs.log_unfinished()  # what server.py does on shutdown when a refresh is still running
        self.assertIn('| manual refresh (all enabled sources) | interrupted |', self.lines()[0])
        jobs.wait(5)


if __name__ == '__main__':
    unittest.main()
