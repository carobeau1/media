"""Account-scoped background refresh of related titles for visible movies and shows."""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from media_db import connect
from sync_media import BASE_URL, DETAIL_RE, MovySync

_lock = threading.Lock()
_active = set()
_stop = threading.Event()
_threads = {}
_SEED_PAGES = ('/browse/movie', '/browse/tv')


def _db():
    db = connect()
    db.execute('PRAGMA busy_timeout=5000')
    # Existing installations can predate the account preferences migration.
    columns = {row['name'] for row in db.execute('PRAGMA table_info(accounts)')}
    if columns and 'parental_controls' not in columns:
        db.execute("ALTER TABLE accounts ADD COLUMN parental_controls TEXT NOT NULL DEFAULT '{}'")
        db.commit()
    db.execute('''CREATE TABLE IF NOT EXISTS similar_scans (
        account_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'idle',
        total INTEGER NOT NULL DEFAULT 0, checked INTEGER NOT NULL DEFAULT 0,
        found INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
        current TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('''CREATE TABLE IF NOT EXISTS parental_exclusions (
        account_id INTEGER NOT NULL, media_type TEXT NOT NULL, media_id TEXT NOT NULL,
        PRIMARY KEY(account_id,media_type,media_id))''')
    return db


def status(account_id):
    db = _db()
    row = db.execute('SELECT * FROM similar_scans WHERE account_id=?', (account_id,)).fetchone()
    db.close()
    if not row:
        return dict(state='idle', total=0, checked=0, found=0, failed=0, current='', last_error='')
    result = dict(row)
    # Threads do not survive server restarts.
    with _lock:
        active = account_id in _active
    fresh = False
    if result['state'] == 'running':
        check = _db()
        fresh = bool(check.execute("SELECT julianday('now')-julianday(updated_at)<(60.0/86400) FROM similar_scans WHERE account_id=?",
                                   (account_id,)).fetchone()[0])
        check.close()
    if result['state'] == 'running' and not active and not fresh:
        result['state'] = 'interrupted'
    return result


def _set(db, account_id, **values):
    columns = ', '.join(f'{key}=?' for key in values)
    db.execute(f'UPDATE similar_scans SET {columns},updated_at=CURRENT_TIMESTAMP WHERE account_id=?',
               (*values.values(), account_id))
    db.commit()


def _visible(db, account_id, target=None):
    row = db.execute('SELECT parental_controls FROM accounts WHERE account_id=?', (account_id,)).fetchone()
    if not row:
        raise ValueError('Your account is missing from this database. Sign out and log in again.')
    try:
        settings = json.loads(row[0] or '{}')
    except (ValueError, TypeError):
        settings = {}
    genres = [str(v).casefold() for v in settings.get('genres', []) if isinstance(v, str)]
    classes = [str(v).casefold() for v in settings.get('classifications', []) if isinstance(v, str)]
    query = '''SELECT m.media_type,m.media_id,m.title FROM media m WHERE m.media_type IN ('movie','tv')
        AND NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)'''
    params = []
    if target:
        query += ' AND m.media_type=? AND m.media_id=?'
        params.extend(target)
    if genres:
        query += f''' AND NOT EXISTS (SELECT 1 FROM media_genres mg JOIN genres g ON g.genre_id=mg.genre_id
            WHERE mg.media_type=m.media_type AND mg.media_id=m.media_id
            AND lower(g.name) IN ({','.join('?' for _ in genres)}))'''
        params.extend(genres)
    if classes:
        query += f" AND lower(trim(m.certification)) NOT IN ({','.join('?' for _ in classes)})"
        params.extend(classes)
    query += ' ORDER BY m.media_type,m.title COLLATE NOCASE'
    return [(r['media_type'], r['media_id'], r['title']) for r in db.execute(query, params)]


def _candidate_metadata(sync, kind, media_id, html):
    soup = BeautifulSoup(html, 'html.parser')
    details = sync.next_page_props(soup).get('details') or {}
    expected = 'Movie' if kind == 'movie' else 'TVSeries'
    schema = next((entry for entry in sync.parse_json_data(soup) if entry.get('@type') == expected), {})
    if not details and not schema:
        raise ValueError(f'No detail metadata for {kind}/{media_id}')
    raw_genres = details.get('categories') or schema.get('genre') or []
    if isinstance(raw_genres, str):
        raw_genres = [raw_genres]
    genres = [part.strip() for value in raw_genres for part in str(value).split(',') if part.strip()]
    classification = str(details.get('certification') or schema.get('contentRating') or '').strip()
    title = str(details.get('title') or schema.get('name') or '').strip()
    return title, genres, classification


def _related_ids(sync, html, kind):
    """Read related IDs independently of image tags and the normal crawler queue."""
    soup = BeautifulSoup(html, 'html.parser')
    details = sync.next_page_props(soup).get('details') or {}
    found = set()
    def add_item(item):
        if not isinstance(item, dict):
            return
        media_id = str(item.get('id') or item.get('media_id') or '')
        media_type = str(item.get('mediaType') or item.get('media_type') or item.get('type') or kind).lower()
        if media_type in ('show', 'shows', 'series', 'tvshow', 'tv_series'):
            media_type = 'tv'
        if media_type in ('movie', 'tv') and media_id.isdigit():
            found.add((media_type, media_id))
    for key in ('recommendations', 'similar', 'related'):
        for item in details.get(key) or []:
            add_item(item)
    for item in sync.parse_json_data(soup):
        if any(key in item for key in ('mediaType', 'media_type')):
            add_item(item)
    for anchor in soup.select('a[href]'):
        match = DETAIL_RE.match(urlparse(urljoin(BASE_URL, anchor['href'])).path)
        if match:
            found.add(match.groups())
    return found


def _add_candidate(db, sync, account_id, identifier, from_url):
    kind, media_id = identifier
    if kind not in ('movie', 'tv') or not media_id.isdigit():
        return False
    if db.execute('SELECT 1 FROM media WHERE media_type=? AND media_id=?', identifier).fetchone():
        return False
    if db.execute('SELECT 1 FROM hidden_media WHERE media_type=? AND media_id=?', identifier).fetchone():
        return False
    candidate_url = urljoin(BASE_URL, f'/{kind}/{media_id}')
    title, genres, classification = _candidate_metadata(sync, kind, media_id, sync.fetch(candidate_url))
    if not _allowed_candidate(db, account_id, genres, classification):
        db.execute('INSERT OR IGNORE INTO parental_exclusions(account_id,media_type,media_id) VALUES(?,?,?)',
                   (account_id, kind, media_id))
        db.commit()
        return False
    db.execute('''INSERT OR IGNORE INTO media(media_type,media_id,title,certification,source_url,raw_json)
        VALUES(?,?,?,?,?,'{}')''', (kind, media_id, title or f'{kind.title()} {media_id}',
                                    classification or None, candidate_url))
    inserted = db.execute('SELECT changes()').fetchone()[0] > 0
    db.execute('INSERT OR IGNORE INTO crawl_queue(url,discovered_from) VALUES(?,?)',
               (candidate_url, from_url))
    for genre in genres:
        db.execute('INSERT OR IGNORE INTO genres(name) VALUES(?)', (genre,))
        db.execute('''INSERT OR IGNORE INTO media_genres(media_type,media_id,genre_id)
            SELECT ?,?,genre_id FROM genres WHERE name=?''', (kind, media_id, genre))
    db.execute('DELETE FROM parental_exclusions WHERE account_id=? AND media_type=? AND media_id=?',
               (account_id, kind, media_id))
    db.commit()
    return inserted


def _allowed_candidate(db, account_id, genres, classification):
    row = db.execute('SELECT parental_controls FROM accounts WHERE account_id=?', (account_id,)).fetchone()
    if not row:
        return False
    try:
        settings = json.loads(row[0] or '{}')
    except (ValueError, TypeError):
        settings = {}
    blocked_genres = {v.casefold() for v in settings.get('genres', []) if isinstance(v, str)}
    blocked_classes = {v.casefold() for v in settings.get('classifications', []) if isinstance(v, str)}
    # Unknown classifications/genres cannot be verified against active restrictions.
    if (blocked_genres and not genres) or (blocked_classes and not classification):
        return False
    return not (blocked_genres & {v.casefold() for v in genres}) and classification.casefold() not in blocked_classes


def start(account_id):
    if _stop.is_set():
        raise ValueError('The server is shutting down.')
    with _lock:
        if account_id in _active:
            return status_without_lock(account_id)
        _active.add(account_id)
    try:
        db = _db()
        titles = _visible(db, account_id)
        db.execute('''INSERT INTO similar_scans(account_id,state,total,checked,found,failed,current,last_error)
            VALUES(?,'running',?,0,0,0,'','') ON CONFLICT(account_id) DO UPDATE SET
            state='running',total=excluded.total,checked=0,found=0,failed=0,current='',last_error='',updated_at=CURRENT_TIMESTAMP''',
            (account_id, len(titles) + len(_SEED_PAGES)))
        db.commit()
        db.close()
        thread = threading.Thread(target=_run, args=(account_id, titles), name=f'similar-scan-{account_id}', daemon=True)
        with _lock:
            _threads[account_id] = thread
        thread.start()
        return status(account_id)
    except Exception:
        with _lock:
            _active.discard(account_id)
        raise


def status_without_lock(account_id):
    db = _db()
    row = db.execute('SELECT * FROM similar_scans WHERE account_id=?', (account_id,)).fetchone()
    db.close()
    return dict(row) if row else dict(state='running', total=0, checked=0, found=0, failed=0)


def _run(account_id, titles):
    db = None
    seen_candidates = set()
    try:
        db = _db()

        def collect(sync, from_url, candidates, source=None):
            for identifier in candidates:
                if _stop.is_set():
                    break
                if identifier == source or identifier in seen_candidates:
                    continue
                seen_candidates.add(identifier)
                try:
                    if _add_candidate(db, sync, account_id, identifier, from_url):
                        row = status_without_lock(account_id)
                        _set(db, account_id, found=row['found'] + 1)
                except Exception as exc:
                    db.rollback()
                    row = status_without_lock(account_id)
                    _set(db, account_id, failed=row['failed'] + 1, last_error=str(exc)[:250])

        for kind, media_id, title in titles:
            if _stop.is_set():
                break
            # Recheck the account's current controls before each fetch.
            if not _visible(db, account_id, (kind, media_id)):
                _set(db, account_id, checked=status_without_lock(account_id)['checked'] + 1)
                continue
            _set(db, account_id, current=f'{title} ({kind}/{media_id})')
            sync = None
            try:
                sync = MovySync(0, False, 20, media_type=kind)
                url = urljoin(BASE_URL, f'/{kind}/{media_id}')
                html = sync.fetch(url)
                candidates = _related_ids(sync, html, kind)
                candidates.update((r['related_type'], r['related_id']) for r in db.execute(
                    'SELECT related_type,related_id FROM recommendations WHERE media_type=? AND media_id=?',
                    (kind, media_id)))
                collect(sync, url, candidates, (kind, media_id))
            except Exception as exc:
                if sync:
                    sync.db.rollback()
                row = status_without_lock(account_id)
                _set(db, account_id, failed=row['failed'] + 1, last_error=str(exc)[:250])
            finally:
                if sync:
                    sync.session.close()
                    sync.db.close()
                row = status_without_lock(account_id)
                _set(db, account_id, checked=row['checked'] + 1)
                _stop.wait(1)
        for path in _SEED_PAGES:
            if _stop.is_set():
                break
            _set(db, account_id, current=f'Discovering {"TV" if path.endswith("tv") else "movies"} from {path}')
            sync = None
            try:
                sync = MovySync(0, False, 20, media_type='all')
                url = urljoin(BASE_URL, path)
                collect(sync, url, _related_ids(sync, sync.fetch(url),
                                                'tv' if path.endswith('tv') else 'movie'))
            except Exception as exc:
                row = status_without_lock(account_id)
                _set(db, account_id, failed=row['failed'] + 1, last_error=str(exc)[:250])
            finally:
                if sync:
                    sync.session.close()
                    sync.db.close()
                row = status_without_lock(account_id)
                _set(db, account_id, checked=row['checked'] + 1)
        _set(db, account_id, state='interrupted' if _stop.is_set() else 'complete', current='')
    except Exception as exc:
        if db:
            _set(db, account_id, state='error', current='', last_error=str(exc)[:250])
    finally:
        if db:
            db.close()
        with _lock:
            _active.discard(account_id)
            _threads.pop(account_id, None)


def request_stop():
    _stop.set()


def wait_stop():
    with _lock:
        threads = list(_threads.values())
    for thread in threads:
        thread.join()
