import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pulse.collector import Collector, Jobs
from pulse.refreshlog import RefreshLog
from pulse.store import Store
from pulse.web import App, make_server

from tests.fixtures import LONG, FakeNet, NoAI, rss


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name) / 'w.db')
        store.save_sources([{'id': None, 'name': 'Blog', 'url': 'https://blog.example/', 'enabled': True,
                             'max_items': 5, 'topic_filter': True}], {'lookback_days': 7, 'default_max_items': 5})
        net = FakeNet({'https://blog.example/': (rss([
            ('&lt;img src=x onerror=alert(1)&gt; Evil exploit title', 'https://blog.example/1', 1, LONG)]), 'application/rss+xml')})
        cls.store = store
        log = RefreshLog(Path(cls.tmp.name) / 'refresh.log')
        jobs = Jobs(Collector(store, NoAI(), fetcher=net), store, refresh_log=log)
        cls.server = make_server(App(store, NoAI(), jobs, refresh_log=log), port=0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        payload = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
        hdrs = {'Host': f'localhost:{self.port}'}
        if payload is not None:
            hdrs['Content-Type'] = 'application/json'
        hdrs.update(headers or {})
        conn.request(method, path, body=payload, headers=hdrs)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        return response.status, data, response

    def test_only_allowlisted_static_files_are_served(self):
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/app.js')[0], 200)
        self.assertEqual(self.request('GET', '/chart.js')[0], 200)
        for path in ['/.git/config', '/server.py', '/pulse/web.py', '/data/pulse.db', '/../etc/passwd',
                     '/static/index.html', '/%2e%2e/server.py']:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)

    def test_security_headers(self):
        _, _, response = self.request('GET', '/')
        self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')

    def test_foreign_host_header_is_rejected(self):
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': 'attacker.example'})[0], 421)

    def test_cross_site_writes_are_rejected(self):
        body = {'source_ids': 'enabled'}
        self.assertEqual(self.request('POST', '/api/refresh', json.dumps(body).encode(),
                                      {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/refresh', body, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/refresh', body, {'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_validation_errors_are_400(self):
        self.assertEqual(self.request('POST', '/api/refresh', {'days': 'x'})[0], 400)
        self.assertEqual(self.request('POST', '/api/refresh', {'source_ids': []})[0], 400)
        self.assertEqual(self.request('GET', '/api/articles?range=1y')[0], 400)
        self.assertEqual(self.request('POST', '/api/refresh', b'{not json')[0], 400)
        status, data, _ = self.request('PUT', '/api/sources', {'sources': [
            {'name': 'Internal', 'url': 'http://192.168.0.10/feed'}]})
        self.assertEqual(status, 400)
        self.assertIn('private', data['error'].lower())

    def test_refresh_job_then_search(self):
        status, job, _ = self.request('POST', '/api/refresh', {'source_ids': 'enabled'})
        self.assertEqual(status, 202)
        deadline = time.time() + 10
        while job['status'] == 'running' and time.time() < deadline:
            time.sleep(0.05)
            job = self.request('GET', f'/api/refresh/{job["id"]}')[1]
        self.assertEqual(job['status'], 'done')
        status, data, _ = self.request('GET', '/api/articles?q=evil&range=24h')
        self.assertEqual(status, 200)
        self.assertEqual(data['total'], 1)
        self.assertNotIn('<img', data['articles'][0]['title'])  # markup stripped server-side too
        self.assertEqual(self.request('GET', '/api/refresh/999')[0], 404)
        self.assertEqual(self.request('GET', '/api/status')[1]['articles_total'], 1)
        log = self.request('GET', '/api/refresh-log')
        self.assertEqual(log[0], 200)
        self.assertEqual(len(log[1]['entries']), 1)  # the manual refresh above was logged
        self.assertIn('manual refresh (all enabled sources) | done | 1 new article', log[1]['entries'][0])


if __name__ == '__main__':
    unittest.main()
