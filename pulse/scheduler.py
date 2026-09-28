"""Automatic daily refresh of all enabled sources at a configured local time (default 02:00).

Rules:
- Runs once per day at `auto_refresh_time` (server local time) when `auto_refresh` is on,
  using the same look-back window and per-source article limits as a manual refresh.
- If the application was not running at that time, the missed refresh runs once shortly
  after the next start (never more than once, however many days were missed).
- Enabling the feature or changing its time never triggers an immediate refresh; the next
  run is the next occurrence of the configured time.
- If a manual refresh is in progress at the scheduled time, it retries a few minutes later.
"""
import logging
import threading
from datetime import datetime, time as dtime, timedelta

from .collector import Busy
from .store import now_iso

log = logging.getLogger(__name__)
STATE_KEY = 'auto_refresh'


def local_now():
    return datetime.now().astimezone()


def parse_hhmm(value):
    hours, minutes = (int(part) for part in value.split(':'))
    return dtime(hours, minutes)


def slot_on(day, at):
    """The configured time on a given date, as an aware local datetime."""
    return datetime.combine(day, at).astimezone()


def latest_slot(now, at):
    """Most recent occurrence of `at` that is not in the future."""
    today = slot_on(now.date(), at)
    return today if today <= now else slot_on(now.date() - timedelta(days=1), at)


def next_slot_after(moment, at):
    candidate = slot_on(moment.date(), at)
    while candidate <= moment:
        candidate = slot_on(candidate.date() + timedelta(days=1), at)
    return candidate


class DailyScheduler:
    def __init__(self, store, jobs, now=local_now, startup_delay=30, retry_delay=300, refresh_log=None):
        self.store = store
        self.jobs = jobs
        self.refresh_log = refresh_log  # for runs without a job (skipped/postponed/crash-interrupted)
        self._watching = None           # id of the automatic job in progress
        self._record_lock = threading.Lock()
        self.now = now
        self.startup_delay = startup_delay
        self.retry_delay = retry_delay
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._retry_at = None

    # ----- state -----------------------------------------------------------
    def _settings(self):
        settings = self.store.settings()
        return bool(settings['auto_refresh']), parse_hhmm(settings['auto_refresh_time']), settings

    def _state(self):
        return self.store.get_meta(STATE_KEY, {}) or {}

    def _save_state(self, **fields):
        self.store.set_meta(STATE_KEY, {**self._state(), **fields})

    def rebaseline(self):
        """Make the next run the next future occurrence (called on start-up and settings changes)."""
        enabled, at, _ = self._settings()
        now = self.now()
        baseline = latest_slot(now, at)
        last = self._state().get('slot')
        if not last or datetime.fromisoformat(last) < baseline:
            self._save_state(slot=baseline.isoformat())
        self._retry_at = None
        self._wake.set()

    def initialize(self):
        """On start-up: set a baseline only the first time, so a refresh missed while the
        application was stopped is still caught up."""
        if not self._state().get('slot'):
            self.rebaseline()
        if self._state().get('status') == 'running':  # the app stopped during an automatic refresh
            self._save_state(status='interrupted', finished_at=now_iso())
            if self.refresh_log:
                self.refresh_log.event('interrupted', 'the application stopped before the refresh finished')

    def settings_changed(self):
        self.rebaseline()

    def next_run(self):
        enabled, at, _ = self._settings()
        if not enabled:
            return None
        last = self._state().get('slot')
        if not last:
            return next_slot_after(self.now(), at)
        due = next_slot_after(datetime.fromisoformat(last), at)
        if self._retry_at and self._retry_at > due:
            return self._retry_at
        return due

    def status(self):
        enabled, at, _ = self._settings()
        upcoming = self.next_run()
        state = self._state()
        return {
            'enabled': enabled,
            'time': at.strftime('%H:%M'),
            'timezone': self.now().strftime('%Z') or self.now().strftime('%z'),
            'next_run': upcoming.isoformat() if upcoming else None,
            'last_run': {k: state.get(k) for k in ('started_at', 'finished_at', 'status', 'added', 'errors')}
            if state.get('started_at') else None,
        }

    # ----- running ---------------------------------------------------------
    def run_if_due(self):
        """Start the daily refresh if it is due. Returns the job, or None."""
        upcoming = self.next_run()
        now = self.now()
        if upcoming is None or upcoming > now:
            return None
        enabled, at, settings = self._settings()
        sources = self.store.get_sources()  # enabled sources, with their own article limits
        slot = latest_slot(now, at)
        if not sources:
            self._save_state(slot=slot.isoformat(), started_at=now_iso(), finished_at=now_iso(),
                             status='skipped', added=0, errors=['No enabled sources'])
            if self.refresh_log:
                self.refresh_log.event('skipped', 'no enabled sources')
            return None
        try:
            job = self.jobs.start(sources, settings['lookback_days'], 'scheduled')
        except Busy:
            self._retry_at = now + timedelta(seconds=self.retry_delay)
            log.info('Automatic refresh postponed: a refresh is already running')
            if self.refresh_log:
                self.refresh_log.event('postponed', 'a manual refresh was running; retrying at '
                                       + self._retry_at.strftime('%H:%M'))
            return None
        self._retry_at = None
        # Mark the slot as taken immediately so a crash or restart cannot repeat it.
        self._save_state(slot=slot.isoformat(), started_at=job['started_at'], finished_at=None,
                         status='running', added=0, errors=[], job_id=job['id'])
        log.warning('Automatic daily refresh started for %d sources', len(sources))
        self._watching = job['id']
        return job

    def record_result(self, job_id):
        with self._record_lock:  # the loop and shutdown may both try; record (and log) exactly once
            if self._watching and self._watching != job_id:
                return False
            job = self.jobs.get(job_id)
            if not job or job['status'] == 'running':
                return False
            state = self._state()
            if state.get('job_id') == job_id and state.get('status') in ('done', 'failed'):
                return True  # already recorded
            self._record(job, job_id)
            return True

    def _record(self, job, job_id):
        self._save_state(finished_at=job['finished_at'], status=job['status'], added=job['added'],
                         errors=[f"{s['name']}: {s['error']}" for s in job['sources'] if s['state'] == 'error'])
        # The finished job itself is written to the refresh log by Jobs (same as manual refreshes).
        if self._watching == job_id:
            self._watching = None

    def flush(self):
        """On shutdown, after waiting for jobs: record a run that finished while stopping, or mark
        it interrupted now (Jobs logs that), so the next start does not log it a second time."""
        if self._watching and not self.record_result(self._watching):
            self._save_state(status='interrupted', finished_at=now_iso())
            self._watching = None

    def _loop(self):
        self.initialize()
        self._stop.wait(self.startup_delay)
        while not self._stop.is_set():
            try:
                if self._watching:
                    self.record_result(self._watching)
                self.run_if_due()
                upcoming = self.next_run()
                wait = 5 if self._watching else 3600
                if upcoming:
                    wait = min(wait, max(1, (upcoming - self.now()).total_seconds()))
            except Exception:
                log.exception('Automatic refresh scheduler error')
                wait = 300
            self._wake.wait(wait)
            self._wake.clear()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name='daily-refresh', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
