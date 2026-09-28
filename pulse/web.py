"""HTTP API and static file server."""
import json
import logging
import mimetypes
import re
import socket
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import VERSION, netguard
from .classify import TOPICS
from .collector import Busy, briefing_for
from .feeds import site_url
from .text import clean

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'static'
# Only these files are ever served; nothing else in the project directory is reachable.
STATIC_FILES = {
    '/': STATIC / 'index.html',
    '/index.html': STATIC / 'index.html',
    '/app.js': STATIC / 'app.js',
    '/theme.js': STATIC / 'theme.js',
    '/styles.css': STATIC / 'styles.css',
    '/favicon.svg': STATIC / 'favicon.svg',
    '/assets/hero.png': STATIC / 'assets' / 'hero.png',
    '/RELEASE_NOTES.md': ROOT / 'RELEASE_NOTES.md',
}
MAX_BODY = 100_000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")
log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _int(value, name, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f'{name} must be a whole number')
    if not low <= number <= high:
        raise ApiError(400, f'{name} must be between {low} and {high}')
    return number


def _run_or_none(value):
    """A refresh identifier (job 'run'), used to list exactly the articles that refresh added."""
    if not value:
        return None
    if not re.fullmatch(r'[0-9A-Za-z:TZ-]{1,64}', value):
        raise ApiError(400, 'Invalid refresh identifier')
    return value


def validate_sources(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('sources'), list):
        raise ApiError(400, 'Expected {"sources": [...], "settings": {...}}')
    raw_settings = payload.get('settings') or {}
    auto_time = str(raw_settings.get('auto_refresh_time', '02:00')).strip()
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', auto_time):
        raise ApiError(400, 'Automatic refresh time must be HH:MM (24-hour), e.g. 02:00')
    settings = {
        'lookback_days': _int(raw_settings.get('lookback_days', 7), 'Lookback days', 1, 30),
        'default_max_items': _int(raw_settings.get('default_max_items', 10), 'Default articles', 1, 50),
        'auto_refresh': bool(raw_settings.get('auto_refresh', True)),
        'auto_refresh_time': auto_time,
    }
    if len(payload['sources']) > 60:
        raise ApiError(400, 'At most 60 sources are supported')
    sources, names = [], set()
    for raw in payload['sources']:
        if not isinstance(raw, dict):
            raise ApiError(400, 'Each source must be an object')
        name = clean(str(raw.get('name', '')))[:80]
        if not name:
            raise ApiError(400, 'Every source needs a name')
        if name.lower() in names:
            raise ApiError(400, f'Duplicate source name: {name}')
        names.add(name.lower())
        try:
            url = site_url(str(raw.get('url', ''))[:300])
            netguard.check_url(url)
        except ValueError as exc:  # includes BlockedURL
            raise ApiError(400, f'{name}: {exc}')
        sources.append({
            'id': raw.get('id') if isinstance(raw.get('id'), int) else None,
            'name': name,
            'url': url,
            'enabled': bool(raw.get('enabled', True)),
            'max_items': _int(raw.get('max_items', settings['default_max_items']), f'{name} articles', 1, 50),
            'topic_filter': bool(raw.get('topic_filter', True)),
        })
    return sources, settings


class Handler(BaseHTTPRequestHandler):
    server_version = 'CyberSecurityNews/' + VERSION
    sys_version = ''

    # ----- plumbing --------------------------------------------------------
    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        log.info('%s %s', self.address_string(), fmt % args)

    def _security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', CSP)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        """Reject requests whose Host header is not ours (DNS-rebinding defence).

        Only the hostname is compared, so the check also works behind a port
        mapping such as Docker's 192.168.3.100:8080 -> container:8000.
        """
        return hostname_of(self.headers.get('Host') or '') in self.server.allowed_hosts

    def _check_write(self):
        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ctype != 'application/json':
            raise ApiError(415, 'Content-Type must be application/json')
        origin = self.headers.get('Origin')
        if origin and (urllib.parse.urlsplit(origin).hostname or '') not in self.server.allowed_hosts:
            raise ApiError(403, 'Cross-origin requests are not allowed')
        if (self.headers.get('Sec-Fetch-Site') or 'same-origin') not in ('same-origin', 'none'):
            raise ApiError(403, 'Cross-site requests are not allowed')

    def _body(self):
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            raise ApiError(411, 'Content-Length is required')
        if not 0 <= size <= MAX_BODY:
            raise ApiError(413, 'Request body too large')
        try:
            return json.loads(self.rfile.read(size) or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, 'Request body is not valid JSON')

    def _dispatch(self, method):
        if not self._host_ok():
            return self.send_json(421, {'error': 'This server does not accept the address '
                                              f'"{hostname_of(self.headers.get("Host") or "")}". '
                                              'Start it with --allowed-host <name> (or PULSE_ALLOWED_HOSTS).'})
        parts = urllib.parse.urlsplit(self.path)
        try:
            if method != 'GET':
                self._check_write()
            for route_method, pattern, handler in ROUTES:
                match = re.fullmatch(pattern, parts.path)
                if match and route_method == method:
                    query = {k: v[-1] for k, v in urllib.parse.parse_qs(parts.query).items()}
                    return handler(self, query, *match.groups())
            if method == 'GET':
                return self._static(parts.path)
            raise ApiError(404, 'Not found')
        except ApiError as exc:
            self.send_json(exc.status, {'error': str(exc)})
        except ValueError as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception:
            log.exception('Unhandled error for %s %s', method, self.path)
            self.send_json(500, {'error': 'Internal server error'})

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_PUT(self):
        self._dispatch('PUT')

    def _static(self, path):
        file = STATIC_FILES.get(path)
        if not file or not file.is_file():
            raise ApiError(404, 'Not found')
        body = file.read_bytes()
        ctype = 'text/markdown' if file.suffix == '.md' else mimetypes.guess_type(file.name)[0]
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', f'{ctype or "application/octet-stream"}; charset=utf-8'
                         if (ctype or '').startswith(('text/', 'application/javascript')) else ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    # ----- API -------------------------------------------------------------
    def api_status(self, query):
        self.send_json(200, {'version': VERSION, **self.app.store.stats(), 'ai': self.app.ai.status(),
                             'collecting': self.app.jobs.running(), 'topics': TOPICS,
                             'storage': {**self.app.store.storage_info(), 'location': self.app.storage_label},
                             'auto_refresh': self.app.scheduler.status() if self.app.scheduler else None})

    def api_articles(self, query):
        sources = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('source', [])
        result = self.app.store.search(
            text=query.get('q', '')[:300],
            time_range=query.get('range', '7d'),
            topic=query.get('topic', ''),
            sources=sources[:60],
            sort=query.get('sort', '') if query.get('sort') in ('', 'newest', 'relevance', None) else 'newest',
            limit=_int(query.get('limit', 48), 'limit', 1, 200),
            offset=_int(query.get('offset', 0), 'offset', 0, 100_000),
            run_id=_run_or_none(query.get('run')),
        )
        self.send_json(200, result)

    def api_sources(self, query):
        self.send_json(200, {'sources': self.app.store.list_sources(), 'settings': self.app.store.settings()})

    def api_save_sources(self, query):
        payload = self._body()
        if payload.get('reset'):
            self.app.store.reset_sources()
        else:
            sources, settings = validate_sources(payload)
            before = self.app.store.settings()
            self.app.store.save_sources(sources, settings)
            changed = any(before.get(k) != settings[k] for k in ('auto_refresh', 'auto_refresh_time'))
            if changed and self.app.scheduler:
                self.app.scheduler.settings_changed()  # next run = next occurrence, never immediately
        self.api_sources(query)

    def api_refresh(self, query):
        payload = self._body()
        if not isinstance(payload, dict):
            raise ApiError(400, 'Expected a JSON object')
        settings = self.app.store.settings()
        days = _int(payload.get('days', settings['lookback_days']), 'Lookback days', 1, 30)
        ids = payload.get('source_ids', 'enabled')
        if ids == 'enabled':
            sources, scope = self.app.store.get_sources(), 'all'
        elif isinstance(ids, list) and ids and all(isinstance(i, int) for i in ids):
            sources, scope = self.app.store.get_sources(ids), 'selected'
        else:
            raise ApiError(400, 'source_ids must be "enabled" or a non-empty list of source ids')
        if not sources:
            raise ApiError(400, 'No matching sources to collect from — enable or select at least one')
        try:
            job = self.app.jobs.start(sources, days, scope)
        except Busy as exc:
            raise ApiError(409, str(exc))
        self.send_json(202, job)

    def api_job(self, query, job_id):
        job = self.app.jobs.get(job_id)
        if not job:
            raise ApiError(404, 'Unknown collection job')
        self.send_json(200, job)

    def api_briefing(self, query):
        self.send_json(200, briefing_for(self.app.store, self.app.ai) or {'kind': 'empty'})


ROUTES = [
    ('GET', r'/api/status', Handler.api_status),
    ('GET', r'/api/articles', Handler.api_articles),
    ('GET', r'/api/sources', Handler.api_sources),
    ('PUT', r'/api/sources', Handler.api_save_sources),
    ('POST', r'/api/refresh', Handler.api_refresh),
    ('GET', r'/api/refresh/(\d+)', Handler.api_job),
    ('GET', r'/api/briefing', Handler.api_briefing),
]


class App:
    def __init__(self, store, ai, jobs, storage_label='data/pulse.db', scheduler=None):
        self.store, self.ai, self.jobs, self.scheduler = store, ai, jobs, scheduler
        self.storage_label = storage_label  # shown in About; never the full host path


WILDCARD_HOSTS = ('0.0.0.0', '::', '')
LOOPBACK_NAMES = ('localhost', '127.0.0.1', '::1')


def hostname_of(host_header):
    """'192.168.3.100:8000' -> '192.168.3.100', '[::1]:80' -> '::1' (lower-cased)."""
    return (urllib.parse.urlsplit('//' + host_header.strip()).hostname or '') if host_header else ''


def primary_address():
    """Best-effort LAN address of this machine (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))  # TEST-NET-1: routing lookup only
            return probe.getsockname()[0]
    except OSError:
        return None


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def make_server(app, host='127.0.0.1', port=8000, allowed_hosts=()):
    """Create the HTTP server.

    host: address to bind — 127.0.0.1 (this machine only), a LAN address such as
    192.168.3.100, or 0.0.0.0 for every interface. allowed_hosts: extra hostnames
    or IPs that browsers may use in the URL (needed behind Docker or a DNS name).
    """
    server = (_Server6 if ':' in host else _Server)((host, port), Handler)
    server.app = app
    names = {*LOOPBACK_NAMES, *(h.strip().lower() for h in allowed_hosts if h.strip())}
    if host not in WILDCARD_HOSTS:
        names.add(host.lower())
    else:
        names.update(n.lower() for n in (primary_address(), socket.gethostname()) if n)
    server.allowed_hosts = names
    return server
