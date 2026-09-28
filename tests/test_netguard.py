import socket
import unittest
from unittest import mock

from pulse import netguard
from pulse.netguard import BlockedURL, check_url, is_public_ip, resolve_public


class CheckUrlTests(unittest.TestCase):
    def test_blocks_local_and_private_targets(self):
        for url in ['http://localhost/', 'http://127.0.0.1/', 'http://10.1.2.3/', 'http://172.16.0.1/',
                    'http://192.168.1.1/', 'http://169.254.169.254/latest/meta-data', 'http://0.0.0.0/',
                    'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://2130706433/', 'http://foo.localhost/',
                    'http://metadata.google.internal/', 'http://printer.local/', 'http://100.64.0.1/',
                    'http://intranet/']:
            with self.subTest(url=url), self.assertRaises(BlockedURL):
                check_url(url)

    def test_blocks_bad_schemes_ports_and_credentials(self):
        for url in ['file:///etc/passwd', 'ftp://example.com/', 'gopher://example.com/',
                    'https://example.com:8443/', 'http://example.com:22/', 'https://user:pw@example.com/']:
            with self.subTest(url=url), self.assertRaises(BlockedURL):
                check_url(url)

    def test_allows_public_sites(self):
        for url in ['https://krebsonsecurity.com/feed/', 'http://example.com:80/x', 'https://8.8.8.8/']:
            with self.subTest(url=url):
                check_url(url)

    def test_is_public_ip(self):
        self.assertTrue(is_public_ip('93.184.216.34'))
        self.assertFalse(is_public_ip('127.0.0.1'))
        self.assertFalse(is_public_ip('::ffff:10.0.0.1'))
        self.assertFalse(is_public_ip('fe80::1'))


class ResolveTests(unittest.TestCase):
    def fake_dns(self, *addresses):
        return mock.patch.object(socket, 'getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 443)) for a in addresses])

    def test_hostname_resolving_to_private_address_is_blocked(self):
        with self.fake_dns('10.0.0.5'), self.assertRaises(BlockedURL):
            resolve_public('evil.example.com', 443)

    def test_mixed_answers_are_blocked(self):
        with self.fake_dns('93.184.216.34', '127.0.0.1'), self.assertRaises(BlockedURL):
            resolve_public('rebind.example.com', 443)

    def test_public_answer_is_pinned(self):
        with self.fake_dns('93.184.216.34'):
            self.assertEqual(resolve_public('example.com', 443), '93.184.216.34')

    def test_redirect_to_private_address_is_blocked(self):
        response = mock.Mock(status=302)
        response.getheader.side_effect = lambda name, default=None: 'http://127.0.0.1/admin' if name == 'Location' else default
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with self.fake_dns('93.184.216.34'), \
                mock.patch.object(netguard, '_PinnedHTTPSConnection', return_value=conn), \
                self.assertRaises(BlockedURL):
            netguard.fetch('https://example.com/feed')


if __name__ == '__main__':
    unittest.main()
