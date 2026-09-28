"""SQLite persistence with an FTS5 full-text index over articles."""
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .classify import TOPICS
from .terms import trending_terms

RANGES = {'24h': timedelta(hours=24), '7d': timedelta(days=7), '15d': timedelta(days=15),
          '30d': timedelta(days=30), 'all': None}
SUMMARY_RANK = {'headline': 0, 'page': 1, 'feed': 2, 'ai': 3}
DEFAULT_SETTINGS = {'lookback_days': 7, 'default_max_items': 10, 'auto_refresh': True, 'auto_refresh_time': '02:00'}
DEFAULT_SOURCES = [
    ('WIRED Security', 'https://www.wired.com/category/security/',
     'https://www.wired.com/feed/category/security/latest/rss'),
    ('KrebsOnSecurity', 'https://krebsonsecurity.com/', 'https://krebsonsecurity.com/feed/'),
    ('The Hacker News', 'https://thehackernews.com/', 'https://feeds.feedburner.com/TheHackersNews'),
    ('Dark Reading', 'https://www.darkreading.com/', 'https://www.darkreading.com/rss.xml'),
    ('Google Threat Intelligence', 'https://cloud.google.com/blog/topics/threat-intelligence',
     'https://cloudblog.withgoogle.com/topics/threat-intelligence/rss/'),
    ('FortiGuard Labs', 'https://www.fortinet.com/blog/threat-research',
     'https://feeds.fortinet.com/fortinet/blog/threat-research'),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  url TEXT NOT NULL,
  feed_url TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  max_items INTEGER NOT NULL DEFAULT 10,
  topic_filter INTEGER NOT NULL DEFAULT 1,
  position INTEGER NOT NULL DEFAULT 0,
  last_checked TEXT,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS articles(
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  source_name TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  title_key TEXT NOT NULL,
  summary TEXT NOT NULL,
  summary_kind TEXT NOT NULL,
  topic TEXT NOT NULL,
  published_at TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_title ON articles(source_name, title_key);
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title, summary, source_name, topic,
  content='articles', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
  INSERT INTO articles_fts(rowid, title, summary, source_name, topic)
  VALUES (new.id, new.title, new.summary, new.source_name, new.topic);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, summary, source_name, topic)
  VALUES ('delete', old.id, old.title, old.summary, old.source_name, old.topic);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, summary, source_name, topic)
  VALUES ('delete', old.id, old.title, old.summary, old.source_name, old.topic);
  INSERT INTO articles_fts(rowid, title, summary, source_name, topic)
  VALUES (new.id, new.title, new.summary, new.source_name, new.topic);
END;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

ARTICLE_COLUMNS = ('id', 'source_name', 'url', 'title', 'summary', 'summary_kind', 'topic',
                   'published_at', 'collected_at')


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def now_iso():
    return iso(datetime.now(timezone.utc))


def fts_query(text):
    """Turn free text into a safe FTS5 query.

    Words become prefix terms, "quoted phrases" stay phrases, tokens that split
    into several parts (CVE-2026-1234) become phrases, and OR is honoured.
    """
    terms = []
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', text):
        if word == 'OR':
            if terms and terms[-1] != 'OR':
                terms.append('OR')
            continue
        tokens = re.findall(r'\w+', phrase or word)
        if not tokens:
            continue
        if phrase or len(tokens) > 1:
            terms.append('"' + ' '.join(tokens) + '"')
        else:
            terms.append(f'"{tokens[0]}"*')
    while terms and terms[-1] == 'OR':
        terms.pop()
    return ' '.join(terms)


class Store:
    def __init__(self, path):
        self.path = str(path)
        self._write_lock = threading.Lock()
        with self._conn() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            # v3.4: articles remember which refresh added them (existing databases are upgraded in place).
            if 'run_id' not in {r[1] for r in db.execute('PRAGMA table_info(articles)')}:
                db.execute('ALTER TABLE articles ADD COLUMN run_id TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS idx_articles_run ON articles(run_id)')
            if not db.execute('SELECT 1 FROM sources LIMIT 1').fetchone() and not self._meta(db, 'seeded'):
                self._seed(db)

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _seed(self, db):
        for pos, (name, url, feed) in enumerate(DEFAULT_SOURCES):
            # Default feeds are security-only publications, so the off-topic filter stays off.
            db.execute('INSERT INTO sources(name, url, feed_url, position, max_items, topic_filter) '
                       'VALUES (?, ?, ?, ?, 10, 0)', (name, url, feed, pos))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('seeded', '1')")

    # ----- meta / settings -------------------------------------------------
    @staticmethod
    def _meta(db, key, default=None):
        row = db.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def get_meta(self, key, default=None):
        with self._conn() as db:
            return self._meta(db, key, default)

    def set_meta(self, key, value):
        with self._write_lock, self._conn() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, json.dumps(value)))

    def settings(self):
        return {**DEFAULT_SETTINGS, **self.get_meta('settings', {})}

    # ----- sources ---------------------------------------------------------
    def list_sources(self):
        with self._conn() as db:
            rows = db.execute('SELECT * FROM sources ORDER BY position, id').fetchall()
        return [{**dict(r), 'enabled': bool(r['enabled']), 'topic_filter': bool(r['topic_filter'])}
                for r in rows]

    def get_sources(self, ids=None):
        sources = self.list_sources()
        if ids is None:
            return [s for s in sources if s['enabled']]
        wanted = set(ids)
        return [s for s in sources if s['id'] in wanted]

    def save_sources(self, sources, settings):
        """Replace the source list. `sources` are already validated dicts."""
        with self._write_lock, self._conn() as db:
            existing = {r['id']: dict(r) for r in db.execute('SELECT * FROM sources')}
            # Delete removed sources first so a re-added name does not collide.
            for sid in set(existing) - {s.get('id') for s in sources}:
                db.execute('DELETE FROM sources WHERE id=?', (sid,))
            for pos, s in enumerate(sources):
                old = existing.get(s.get('id'))
                if old:
                    feed = old['feed_url'] if old['url'] == s['url'] else None
                    db.execute('UPDATE sources SET name=?, url=?, feed_url=?, enabled=?, max_items=?, '
                               'topic_filter=?, position=? WHERE id=?',
                               (s['name'], s['url'], feed, int(s['enabled']), s['max_items'],
                                int(s['topic_filter']), pos, old['id']))
                    if old['name'] != s['name']:
                        db.execute('UPDATE articles SET source_name=? WHERE source_id=?', (s['name'], old['id']))
                else:
                    db.execute('INSERT INTO sources(name, url, enabled, max_items, topic_filter, position) '
                               'VALUES (?, ?, ?, ?, ?, ?)',
                               (s['name'], s['url'], int(s['enabled']), s['max_items'],
                                int(s['topic_filter']), pos))
            db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('settings', json.dumps(settings)))

    def reset_sources(self):
        with self._write_lock, self._conn() as db:
            db.execute('DELETE FROM sources')
            self._seed(db)
            db.execute('DELETE FROM meta WHERE key = ?', ('settings',))

    def record_source_check(self, source_id, feed_url, error):
        with self._write_lock, self._conn() as db:
            if feed_url:
                db.execute('UPDATE sources SET feed_url=?, last_checked=?, last_error=? WHERE id=?',
                           (feed_url, now_iso(), error, source_id))
            else:
                db.execute('UPDATE sources SET last_checked=?, last_error=? WHERE id=?',
                           (now_iso(), error, source_id))

    # ----- articles --------------------------------------------------------
    def unknown(self, items):
        """Items not stored yet — same rule as upsert: new URL *and* new headline for that source."""
        if not items:
            return []
        with self._conn() as db:
            seen_urls = {r[0] for r in db.execute(
                f'SELECT url FROM articles WHERE url IN ({",".join("?" * len(items))})', [i['url'] for i in items])}
            seen_titles = {(r[0], r[1]) for r in db.execute(
                'SELECT source_name, title_key FROM articles WHERE source_name IN '
                f'({",".join("?" * len({i["source_name"] for i in items}))})',
                list({i['source_name'] for i in items}))}
        return [i for i in items if i['url'] not in seen_urls and (i['source_name'], i['title_key']) not in seen_titles]

    def known_urls(self, urls):
        urls = list(urls)
        if not urls:
            return set()
        with self._conn() as db:
            marks = ','.join('?' * len(urls))
            return {r['url'] for r in db.execute(f'SELECT url FROM articles WHERE url IN ({marks})', urls)}

    def upsert_articles(self, articles, run_id=None):
        """Insert new articles (tagged with the refresh `run_id`); upgrade summaries of known ones.
        Returns the number inserted."""
        added = 0
        with self._write_lock, self._conn() as db:
            for a in articles:
                row = db.execute('SELECT id, summary_kind FROM articles WHERE url=? OR '
                                 '(source_name=? AND title_key=?)',
                                 (a['url'], a['source_name'], a['title_key'])).fetchone()
                if row is None:
                    db.execute('INSERT INTO articles(source_id, source_name, url, title, title_key, summary, '
                               'summary_kind, topic, published_at, collected_at, run_id) '
                               'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (a.get('source_id'), a['source_name'], a['url'], a['title'], a['title_key'],
                                a['summary'], a['summary_kind'], a['topic'], a['published_at'], now_iso(), run_id))
                    added += 1
                elif SUMMARY_RANK[a['summary_kind']] > SUMMARY_RANK.get(row['summary_kind'], 0):
                    db.execute('UPDATE articles SET summary=?, summary_kind=?, topic=? WHERE id=?',
                               (a['summary'], a['summary_kind'], a['topic'], row['id']))
        return added

    def _filters(self, q, time_range, topic, sources, *, skip=(), run_id=None):
        where, params = [], []
        if run_id:
            where.append('a.run_id = ?')
            params.append(run_id)
        cutoff = RANGES.get(time_range)
        if cutoff is not None:
            where.append('a.published_at >= ?')
            params.append(iso(datetime.now(timezone.utc) - cutoff))
        if topic and 'topic' not in skip:
            where.append('a.topic = ?')
            params.append(topic)
        if sources and 'sources' not in skip:
            where.append(f'a.source_name IN ({",".join("?" * len(sources))})')
            params.extend(sources)
        if q:
            where.append('articles_fts MATCH ?')
            params.append(q)
        return (' AND '.join(where) or '1'), params

    def search(self, text='', time_range='7d', topic='', sources=(), sort='', limit=48, offset=0,
               run_id=None):
        if time_range not in RANGES:
            raise ValueError('range must be one of ' + ', '.join(RANGES))
        if topic and topic not in TOPICS:
            raise ValueError('Unknown topic')
        q = fts_query(text or '')
        sort = sort or ('relevance' if q else 'newest')
        base = 'FROM articles a JOIN articles_fts ON articles_fts.rowid = a.id' if q else 'FROM articles a'
        where, params = self._filters(q, time_range, topic, list(sources), run_id=run_id)
        cols = ', '.join('a.' + c for c in ARTICLE_COLUMNS)
        if q:
            cols += (", highlight(articles_fts, 0, char(2), char(3)) AS title_hl"
                     ", snippet(articles_fts, 1, char(2), char(3), '…', 60) AS summary_hl")
        order = 'bm25(articles_fts, 10.0, 3.0, 6.0, 1.0), a.published_at DESC' if (q and sort == 'relevance') \
            else 'a.published_at DESC, a.id DESC'
        with self._conn() as db:
            try:
                rows = db.execute(f'SELECT {cols} {base} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?',
                                  params + [limit, offset]).fetchall()
                total = db.execute(f'SELECT COUNT(*) {base} WHERE {where}', params).fetchone()[0]
                facets = {}
                for column, skip in (('topic', 'topic'), ('source_name', 'sources')):
                    fw, fp = self._filters(q, time_range, topic, list(sources), skip=(skip,),
                                           run_id=run_id)
                    facets[column] = {r[0]: r[1] for r in db.execute(
                        f'SELECT a.{column}, COUNT(*) {base} WHERE {fw} GROUP BY a.{column}', fp)}
                sample = db.execute(f'SELECT a.title, a.summary {base} WHERE {where} '
                                    'ORDER BY a.published_at DESC LIMIT 600', params).fetchall()
            except sqlite3.OperationalError as exc:
                if 'fts5' in str(exc).lower() or 'syntax' in str(exc).lower():
                    raise ValueError('Could not understand the search query') from exc
                raise
        return {
            'articles': [dict(r) for r in rows],
            'total': total,
            'query': q,
            'sort': sort,
            'facets': {'topics': facets['topic'], 'sources': facets['source_name']},
            'terms': trending_terms([(r['title'], r['summary']) for r in sample]),
        }

    def recent(self, hours=48, limit=60):
        with self._conn() as db:
            rows = db.execute('SELECT * FROM articles WHERE published_at >= ? ORDER BY published_at DESC LIMIT ?',
                              (iso(datetime.now(timezone.utc) - timedelta(hours=hours)), limit)).fetchall()
        return [dict(r) for r in rows]

    def checkpoint(self):
        """Fold the write-ahead log into the main file (clean state for shutdown/backup)."""
        with self._write_lock, self._conn() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def backup(self, target):
        """Consistent online copy of the database (safe while the app is running)."""
        with self._conn() as db:
            dest = sqlite3.connect(str(target))
            try:
                db.backup(dest)
            finally:
                dest.close()

    def storage_info(self):
        with self._conn() as db:
            count, first, last = db.execute(
                'SELECT COUNT(*), MIN(collected_at), MAX(collected_at) FROM articles').fetchone()
            sources = db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
        # Everything SQLite keeps on disk for this database: main file, write-ahead log, shared memory.
        size = sum(os.path.getsize(p) for p in (self.path, self.path + '-wal', self.path + '-shm') if os.path.exists(p))
        return {'articles': count, 'sources': sources, 'first_collected': first, 'last_collected': last,
                'size_bytes': size}

    def stats(self):
        now = datetime.now(timezone.utc)
        with self._conn() as db:
            count = lambda since: db.execute('SELECT COUNT(*) FROM articles WHERE published_at >= ?',
                                             (iso(now - since),)).fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
            sources = db.execute('SELECT COUNT(*), SUM(enabled) FROM sources').fetchone()
            result = {
                'articles_total': total,
                'articles_24h': count(timedelta(hours=24)),
                'articles_7d': count(timedelta(days=7)),
                'sources_total': sources[0],
                'sources_enabled': sources[1] or 0,
                'last_refresh': self._meta(db, 'last_refresh'),
            }
        return result
