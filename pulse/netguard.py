"""Outbound HTTP for user-supplied URLs, hardened against SSRF.

Every hop (including redirects) is validated: only http/https on ports 80/443,
no credentials in the URL, no local hostnames, and the hostname must resolve
exclusively to public IP addresses. The connection is then pinned to the
address that was checked, so a DNS answer cannot change between the check and
the connect (DNS rebinding).
"""
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import zlib

USER_AGENT = ('Mozilla/5.0 (compatible; CyberSecurityNews/2.0; '
              '+https://github.com/righirr/cyber-security-news-system)')
ALLOWED_PORTS = {80, 443}
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
LOCAL_SUFFIXES = ('.localhost', '.local', '.internal', '.lan', '.home.arpa', '.intranet', '.corp')


class BlockedURL(ValueError):
    """The URL points somewhere the collector must never reach."""


class FetchError(RuntimeError):
    """The remote site could not be fetched."""


def is_public_ip(value):
    addr = ipaddress.ip_address(value.split('%', 1)[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url):
    """Validate the static parts of a URL and return its parsed form."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        raise BlockedURL('Only http and https URLs are allowed')
    if parts.username or parts.password:
        raise BlockedURL('Credentials in URLs are not allowed')
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        raise BlockedURL('URL has no hostname')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError as exc:
        raise BlockedURL('Invalid port') from exc
    if port not in ALLOWED_PORTS:
        raise BlockedURL('Only ports 80 and 443 are allowed')
    single_label = '.' not in host and ':' not in host
    if host == 'localhost' or host.endswith(LOCAL_SUFFIXES) or single_label:
        raise BlockedURL('Local hostnames are not allowed')
    try:
        literal = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        literal = None
    if literal is not None and not is_public_ip(str(literal)):
        raise BlockedURL('Private or reserved addresses are not allowed')
    return parts, host, port


def resolve_public(host, port):
    """Resolve host and return one address, refusing if any address is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f'Cannot resolve {host}') from exc
    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise FetchError(f'Cannot resolve {host}')
    if not all(is_public_ip(a) for a in addresses):
        raise BlockedURL(f'{host} resolves to a private or reserved address')
    return addresses[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_body(response, max_bytes):
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise FetchError('Response is too large')
    encoding = (response.getheader('Content-Encoding') or '').lower()
    if encoding in ('gzip', 'deflate'):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS)
        raw = decoder.decompress(raw, max_bytes + 1)
        if len(raw) > max_bytes or decoder.unconsumed_tail:
            raise FetchError('Response is too large')
    return raw


def fetch(url, timeout=12, max_bytes=MAX_BYTES, accept='*/*'):
    """GET a public URL. Returns (final_url, body_bytes, content_type)."""
    for _ in range(MAX_REDIRECTS + 1):
        parts, host, port = check_url(url)
        address = resolve_public(host, port)
        conn_cls = _PinnedHTTPSConnection if parts.scheme == 'https' else _PinnedHTTPConnection
        conn = conn_cls(host, address, port, timeout)
        path = urllib.parse.urlunsplit(('', '', parts.path or '/', parts.query, ''))
        try:
            conn.request('GET', path, headers={
                'User-Agent': USER_AGENT,
                'Accept': accept,
                'Accept-Encoding': 'gzip, deflate',
            })
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise FetchError(f'Redirect without Location from {host}')
                url = urllib.parse.urljoin(url, location)
                continue
            if response.status >= 400:
                raise FetchError(f'HTTP {response.status} from {host}')
            body = _read_body(response, max_bytes)
            return url, body, response.getheader('Content-Type') or ''
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f'{host}: {exc}') from exc
        finally:
            conn.close()
    raise FetchError('Too many redirects')
