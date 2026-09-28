"""Plain-text log of every refresh (manual and automatic): one line per run, appended to data/refresh.log.

Example line:
2026-09-28 02:00:04 -03 | automatic refresh | done | 12 new articles | 8.4 s | WIRED Security: 3, KrebsOnSecurity: 0, ... | errors: none
"""
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
MAX_BYTES = 5_000_000  # rotate to refresh.log.1 beyond this (roughly decades of daily lines)


def _one_line(text):
    return ' '.join(str(text).split()).replace('|', '/')


def _local(iso_utc):
    """'2026-09-28T05:00:04Z' -> '2026-09-28 02:00:04 -03' in the server's time zone."""
    if not iso_utc:
        return datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
    moment = datetime.fromisoformat(iso_utc.replace('Z', '+00:00')).astimezone()
    return moment.strftime('%Y-%m-%d %H:%M:%S %Z')


class RefreshLog:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _append(self, line):
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    os.replace(self.path, self.path.with_name(self.path.name + '.1'))
                with open(self.path, 'a', encoding='utf-8') as handle:
                    handle.write(line + '\n')
        except OSError as exc:  # never let logging break the refresh itself
            log.warning('Could not write the refresh log %s: %s', self.path, exc)

    @staticmethod
    def kind(scope):
        return {'scheduled': 'automatic refresh', 'all': 'manual refresh (all enabled sources)',
                'selected': 'manual refresh (selected sources)'}.get(scope, f'{scope} refresh')

    def job(self, job):
        """Record a finished refresh job (manual or automatic)."""
        started = datetime.fromisoformat(job['started_at'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat((job.get('finished_at') or job['started_at']).replace('Z', '+00:00'))
        per_source = ', '.join(f"{_one_line(s['name'])}: {s['new'] if s['state'] != 'error' else 'failed'}"
                               for s in job['sources'])
        errors = '; '.join(f"{_one_line(s['name'])}: {_one_line(s['error'])}" for s in job['sources'] if s['error'])
        if job.get('error'):
            errors = '; '.join(filter(None, [errors, _one_line(job['error'])]))
        self._append(' | '.join([
            _local(job['started_at']), self.kind(job.get('scope')), job['status'],
            f"{job['added']} new article{'s' if job['added'] != 1 else ''}",
            f"{(finished - started).total_seconds():.1f} s",
            per_source or 'no sources',
            f"errors: {errors or 'none'}",
        ]))

    def event(self, status, detail, kind='automatic refresh'):
        """Record a run that did not complete normally: skipped, postponed or interrupted."""
        self._append(' | '.join([_local(None), kind, status, '0 new articles', '-', '-', _one_line(detail)]))

    def tail(self, lines=20):
        """Last `lines` entries, newest first (for the About dialog)."""
        try:
            with open(self.path, encoding='utf-8') as handle:
                entries = [l.rstrip('\n') for l in handle if l.strip()]
        except FileNotFoundError:
            return []
        return entries[-lines:][::-1]
