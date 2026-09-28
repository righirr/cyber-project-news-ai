"""Collected news must survive stopping and restarting the application."""
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from pulse.store import Store

from tests.test_store_collector import article

ROOT = Path(__file__).resolve().parent.parent


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'data' / 'pulse.db'
        self.path.parent.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_articles_sources_and_settings_survive_reopen(self):
        store = Store(self.path)
        store.upsert_articles([article('https://a/1', 'Kept across restarts')])
        sources = store.list_sources()[:2]
        store.save_sources(sources, {'lookback_days': 5, 'default_max_items': 9})
        store.set_meta('last_refresh', {'at': '2026-09-27T00:00:00Z', 'added': 1, 'sources': ['x'], 'scope': 'all'})
        del store  # "shutdown"

        reopened = Store(self.path)  # "start again"
        self.assertEqual(reopened.search('restarts', 'all')['total'], 1)
        self.assertEqual(len(reopened.list_sources()), 2)  # not re-seeded
        self.assertEqual(reopened.settings()['lookback_days'], 5)
        self.assertEqual(reopened.stats()['last_refresh']['added'], 1)

    def test_checkpoint_backup_and_storage_info(self):
        store = Store(self.path)
        store.upsert_articles([article(f'https://a/{i}', f'Story number {i}') for i in range(5)])
        store.checkpoint()
        wal = Path(str(self.path) + '-wal')
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)  # everything is in pulse.db
        info = store.storage_info()
        self.assertEqual(info['articles'], 5)
        self.assertGreater(info['size_bytes'], 0)
        target = Path(self.tmp.name) / 'backup.db'
        store.backup(target)
        with sqlite3.connect(target) as copy:
            self.assertEqual(copy.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 5)

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root can write anywhere')
    def test_unwritable_data_folder_gives_clear_error(self):
        sys.path.insert(0, str(ROOT))
        import server
        locked = Path(self.tmp.name) / 'locked'
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with self.assertRaises(SystemExit) as ctx:
                server.ensure_writable(locked / 'pulse.db')
            self.assertIn('chown', str(ctx.exception))
        finally:
            locked.chmod(0o700)


class GracefulShutdownTests(unittest.TestCase):
    def test_sigterm_flushes_and_data_is_there_on_next_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'pulse.db'
            Store(db).upsert_articles([article('https://a/1', 'Persisted story')])
            env = {**os.environ, 'PULSE_AI': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'PULSE_STORAGE_LABEL': 'test'}
            proc = subprocess.Popen([sys.executable, 'server.py', '--port', '0', '--db', str(db)], cwd=ROOT,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []
            deadline = time.time() + 10
            while time.time() < deadline:
                line = proc.stdout.readline()
                lines.append(line)
                if 'Claude enrichment' in line:
                    break
            self.assertTrue(any('1 articles kept from previous runs' in l for l in lines), lines)
            started = time.time()
            proc.send_signal(signal.SIGTERM)
            output, _ = proc.communicate(timeout=10)
            self.assertLess(time.time() - started, 5)  # no 10-second docker-style kill needed
            self.assertEqual(proc.returncode, 0)
            self.assertIn('Saved 1 articles', output)
            self.assertEqual(Store(db).storage_info()['articles'], 1)


if __name__ == '__main__':
    unittest.main()
