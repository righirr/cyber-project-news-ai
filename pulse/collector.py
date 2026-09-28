"""Collect articles from sources in parallel, summarise, classify and store them."""
import copy
import itertools
import secrets
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import netguard
from .briefing import computed_briefing
from .classify import classify, is_security_relevant
from .feeds import discover_feeds, feed_candidates, google_news_url, parse_feed, strip_publisher_suffix
from .store import iso, now_iso
from .text import SUMMARY_MAX, truncate, clean, extractive_summary, headline_summary, page_excerpt, title_key

log = logging.getLogger(__name__)
FEED_ACCEPT = 'application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5'


class Busy(RuntimeError):
    """A collection job is already running."""


class Collector:
    def __init__(self, store, ai, fetcher=netguard.fetch, workers=6):
        self.store = store
        self.ai = ai
        self.fetch = fetcher
        self.workers = workers

    # ----- feed resolution -------------------------------------------------
    def _get_feed(self, url):
        final, body, _ = self.fetch(url, accept=FEED_ACCEPT)
        return final, body

    def _resolve_entries(self, source, days):
        """Return (entries, feed_url, via). via is 'feed' or 'google'."""
        netguard.check_url(source['url'])  # fail fast with a clear message for blocked addresses
        errors = []
        if source.get('feed_url'):
            try:
                final, body = self._get_feed(source['feed_url'])
                return parse_feed(body, final), source['feed_url'], 'feed'
            except (netguard.FetchError, ValueError) as exc:
                errors.append(str(exc))
        candidates = []
        try:
            final, body = self._get_feed(source['url'])
            try:
                return parse_feed(body, final), source['url'], 'feed'
            except ValueError:
                candidates += discover_feeds(body, final)
        except (netguard.FetchError, ValueError) as exc:
            errors.append(str(exc))
        candidates += feed_candidates(source['url'])
        for url in dict.fromkeys(candidates):
            if url == source.get('feed_url'):
                continue
            try:
                final, body = self._get_feed(url)
                return parse_feed(body, final), url, 'feed'
            except (netguard.FetchError, ValueError) as exc:
                errors.append(str(exc))
        try:
            final, body = self._get_feed(google_news_url(source['url'], days))
            entries = parse_feed(body, final)
        except (netguard.FetchError, ValueError) as exc:
            raise netguard.FetchError(f'No RSS/Atom feed found and Google News failed ({exc})') from exc
        for entry in entries:
            entry['title'] = strip_publisher_suffix(entry['title'], entry.get('publisher'))
        return entries, None, 'google'

    # ----- summaries -------------------------------------------------------
    def _summarise(self, item):
        summary = extractive_summary(item['title'], item['text'])
        item['ai_text'] = item['text']
        if summary:
            item['summary'], item['summary_kind'] = summary, 'feed'
            return item
        if 'news.google.com' not in item['url']:
            try:
                _, body, ctype = self.fetch(item['url'], timeout=8, max_bytes=2_000_000,
                                            accept='text/html,application/xhtml+xml')
                if 'html' in ctype.lower() or not ctype:
                    description, text = page_excerpt(body)
                    item['ai_text'] = (description + ' ' + text).strip()
                    if description:
                        item['summary'], item['summary_kind'] = truncate(description, SUMMARY_MAX), 'page'
                        return item
            except (netguard.FetchError, ValueError) as exc:
                log.info('Page fetch failed for %s: %s', item['url'], exc)
        item['summary'], item['summary_kind'] = headline_summary(item['title'], item['source_name']), 'headline'
        return item

    def _collect_source(self, source, days):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        entries, feed_url, via = self._resolve_entries(source, days)
        items, off_topic = [], 0
        for entry in entries:
            if not entry['link']:
                continue
            published = min(entry['published'] or now, now)
            if published < cutoff:
                continue
            text = clean(entry['text'])
            if source['topic_filter'] and via == 'feed' and not is_security_relevant(entry['title'], text):
                off_topic += 1
                continue
            items.append({'source_id': source['id'], 'source_name': source['name'], 'url': entry['link'],
                          'title': entry['title'], 'title_key': title_key(entry['title']), 'text': text,
                          'published_at': iso(published), 'topic': classify(entry['title'], text)})
        items.sort(key=lambda i: i['published_at'], reverse=True)
        items = items[:source['max_items']]
        # Only articles not stored yet (by URL or by headline) are summarised and saved, so a
        # refresh — manual or automatic — brings only news collected for the first time.
        new = self.store.unknown(items)
        with ThreadPoolExecutor(max_workers=4) as pool:
            new = list(pool.map(self._summarise, new))
        return new, {'found': len(items), 'off_topic': off_topic, 'via': via, 'feed_url': feed_url}

    # ----- run -------------------------------------------------------------
    def collect(self, sources, days, on_update=lambda *a, **k: None, run_id=None):
        pending = []

        def run(source):
            on_update(source['id'], state='running')
            try:
                new, info = self._collect_source(source, days)
            except Exception as exc:  # one failing source must not abort the others
                message = str(exc) or exc.__class__.__name__
                self.store.record_source_check(source['id'], None, message)
                on_update(source['id'], state='error', error=message)
                return []
            self.store.record_source_check(source['id'], info['feed_url'], None)
            on_update(source['id'], state='fetched', found=info['found'], via=info['via'],
                      off_topic=info['off_topic'], candidates=len(new))
            return new

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for batch in pool.map(run, sources):
                pending.extend(batch)

        if pending and self.ai.enabled:
            on_update(None, phase='summarizing')
            filters = {s['id']: s['topic_filter'] for s in sources}
            results = self.ai.enrich(pending)
            kept = []
            for index, item in enumerate(pending):
                result = results.get(index)
                if result:
                    if not result['relevant'] and filters.get(item['source_id']):
                        continue
                    if result['summary'].strip():
                        item['summary'], item['summary_kind'] = truncate(clean(result['summary']), SUMMARY_MAX), 'ai'
                    item['topic'] = result['topic']
                kept.append(item)
            pending = kept

        on_update(None, phase='saving')
        added = {}
        for source in sources:
            batch = [i for i in pending if i['source_id'] == source['id']]
            added[source['id']] = self.store.upsert_articles(batch, run_id=run_id)
            on_update(source['id'], new=added[source['id']])
        return added

    def refresh_briefing(self):
        recent = recent_for_briefing(self.store)
        briefing = self.ai.briefing(recent) if self.ai.enabled else None
        if briefing:
            self.store.set_meta('briefing', {'kind': 'ai', 'generated_at': now_iso(), 'model': self.ai.model,
                                             **briefing})
        return briefing


class Jobs:
    """Runs one collection at a time in a background thread; clients poll for progress."""

    def __init__(self, collector, store):
        self.collector = collector
        self.store = store
        self._lock = threading.Lock()
        self._jobs = {}
        self._threads = []
        self._ids = itertools.count(1)

    def running(self):
        return any(j['status'] == 'running' for j in self._jobs.values())

    def start(self, sources, days, scope):
        with self._lock:
            if self.running():
                raise Busy('A collection is already running')
            job_id = str(next(self._ids))
            job = {'id': job_id, 'run': f'{now_iso()}-{secrets.token_hex(4)}',  # tags the articles it adds
                   'status': 'running', 'phase': 'collecting', 'scope': scope, 'days': days,
                   'started_at': now_iso(), 'finished_at': None, 'added': 0, 'error': None,
                   'sources': [{'id': s['id'], 'name': s['name'], 'state': 'pending', 'found': 0, 'new': 0,
                                'via': None, 'off_topic': 0, 'error': None} for s in sources]}
            self._jobs[job_id] = job
            for old in sorted(self._jobs, key=int)[:-20]:
                del self._jobs[old]
            snapshot = copy.deepcopy(job)
        thread = threading.Thread(target=self._run, args=(job, sources, days), daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        return snapshot

    def wait(self, timeout):
        """Wait for running collections (used on shutdown). Returns True when idle."""
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - time.monotonic()))
        return not self.running()

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def _update(self, job, source_id, **fields):
        with self._lock:
            if source_id is None:
                job.update(fields)
                return
            for entry in job['sources']:
                if entry['id'] == source_id:
                    entry.update(fields)

    def _run(self, job, sources, days):
        try:
            added = self.collector.collect(sources, days, lambda sid, **f: self._update(job, sid, **f),
                                           run_id=job['run'])
            total = sum(added.values())
            with self._lock:
                for entry in job['sources']:
                    if entry['state'] != 'error':
                        entry['state'] = 'done'
                job['added'] = total
            self.store.set_meta('last_refresh', {'at': now_iso(), 'added': total, 'scope': job['scope'],
                                                 'sources': [s['name'] for s in sources]})
            if total:
                self._update(job, None, phase='briefing')
                self.collector.refresh_briefing()
            self._update(job, None, status='done', phase='done', finished_at=now_iso())
        except Exception as exc:
            log.exception('Collection job failed')
            self._update(job, None, status='failed', phase='failed', error=str(exc), finished_at=now_iso())


def briefing_for(store, ai):
    """AI briefing if one exists for the latest data, otherwise a computed one."""
    stored = store.get_meta('briefing')
    last = (store.get_meta('last_refresh') or {}).get('at')
    if ai.enabled and stored and (not last or stored['generated_at'] >= last):
        return stored
    return computed_briefing(recent_for_briefing(store))


def recent_for_briefing(store):
    """Last 48 hours of reporting, widened to 7 days when that is too thin to summarise."""
    recent = store.recent(hours=48)
    return recent if len(recent) >= 5 else store.recent(hours=24 * 7)
