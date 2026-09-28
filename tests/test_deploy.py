"""Deployment settings: .env loading, Claude status messages and LAN host checks."""
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pulse import ai as ai_module
from pulse.collector import Collector, Jobs
from pulse.envfile import load_env
from pulse.store import Store
from pulse.web import App, hostname_of, make_server

from tests.fixtures import NoAI


class EnvFileTests(unittest.TestCase):
    def test_parses_quotes_comments_and_export_without_overriding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('# comment\n\nexport PULSE_T_A=one\nPULSE_T_B="two words"\n'
                            "PULSE_T_C='x # not a comment'\nPULSE_T_D=val # trailing\nPULSE_T_KEEP=file\nnot a line\n")
            with mock.patch.dict(os.environ, {'PULSE_T_KEEP': 'shell'}):
                loaded = load_env(path)
                self.assertEqual(os.environ['PULSE_T_A'], 'one')
                self.assertEqual(os.environ['PULSE_T_B'], 'two words')
                self.assertEqual(os.environ['PULSE_T_C'], 'x # not a comment')
                self.assertEqual(os.environ['PULSE_T_D'], 'val')
                self.assertEqual(os.environ['PULSE_T_KEEP'], 'shell')  # real environment wins
                self.assertNotIn('PULSE_T_KEEP', loaded)
            for key in ('PULSE_T_A', 'PULSE_T_B', 'PULSE_T_C', 'PULSE_T_D'):
                os.environ.pop(key, None)

    def test_missing_file_is_fine(self):
        self.assertEqual(load_env('/nonexistent/.env'), [])


class ClaudeStatusTests(unittest.TestCase):
    def status(self, env, sdk=True):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'PULSE_AI', 'PULSE_MODEL')}
        fake_sdk = mock.Mock() if sdk else None
        with mock.patch.dict(os.environ, {**clean_env, **env}, clear=True), \
                mock.patch.object(ai_module, 'anthropic', fake_sdk):
            return ai_module.Claude()

    def test_missing_package_explains_venv_and_docker(self):
        claude = self.status({'ANTHROPIC_API_KEY': 'k'}, sdk=False)
        self.assertFalse(claude.enabled)
        self.assertEqual(claude.reason, 'anthropic package not installed')
        self.assertIn('python3 -m venv .venv', claude.hint)
        self.assertIn('docker compose', claude.hint)

    def test_missing_key_points_to_env_file(self):
        claude = self.status({})
        self.assertFalse(claude.enabled)
        self.assertIn('.env', claude.hint)

    def test_enabled_with_key_and_model_from_env(self):
        claude = self.status({'ANTHROPIC_API_KEY': 'k', 'PULSE_MODEL': 'claude-sonnet-5'})
        self.assertTrue(claude.enabled)
        self.assertEqual(claude.model, 'claude-sonnet-5')

    def test_explicit_off_and_forced_on(self):
        self.assertEqual(self.status({'ANTHROPIC_API_KEY': 'k', 'PULSE_AI': '0'}).reason, 'disabled with PULSE_AI=0')
        self.assertTrue(self.status({'PULSE_AI': '1'}).enabled)  # e.g. `ant auth login` profile


class HostTests(unittest.TestCase):
    def serve(self, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'h.db')
        server = make_server(App(store, NoAI(), Jobs(Collector(store, NoAI()), store)), port=0, **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def status_for(self, server, host_header):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        conn.request('GET', '/api/status', headers={'Host': host_header})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_hostname_of(self):
        self.assertEqual(hostname_of('192.168.3.100:8000'), '192.168.3.100')
        self.assertEqual(hostname_of('[::1]:80'), '::1')
        self.assertEqual(hostname_of('Pulse.LAN'), 'pulse.lan')
        self.assertEqual(hostname_of(''), '')

    def test_allowed_lan_ip_any_port(self):
        server = self.serve(host='127.0.0.1', allowed_hosts=['192.168.3.100'])
        self.assertEqual(self.status_for(server, '192.168.3.100:8000'), 200)
        self.assertEqual(self.status_for(server, '192.168.3.100:18080'), 200)  # docker port mapping
        self.assertEqual(self.status_for(server, 'localhost'), 200)
        self.assertEqual(self.status_for(server, 'attacker.example'), 421)
        self.assertEqual(self.status_for(server, '192.168.3.101:8000'), 421)

    def test_wildcard_bind_accepts_this_machines_address(self):
        server = self.serve(host='0.0.0.0')
        self.assertNotIn('0.0.0.0', server.allowed_hosts)
        self.assertEqual(self.status_for(server, f'127.0.0.1:{server.server_address[1]}'), 200)

    def test_origin_from_allowed_lan_ip_can_write(self):
        server = self.serve(host='127.0.0.1', allowed_hosts=['192.168.3.100'])
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        conn.request('PUT', '/api/sources', body=b'{"reset": true}', headers={
            'Host': '192.168.3.100:8000', 'Origin': 'http://192.168.3.100:8000', 'Content-Type': 'application/json'})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()


if __name__ == '__main__':
    unittest.main()
