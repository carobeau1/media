"""Slow background repair of incomplete movie/TV detail records."""
from __future__ import annotations

import threading
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from media_db import connect
from sync_media import BASE_URL, MovySync, BackgroundImagePaused

_LOCK = threading.Lock()
_STARTED = False
_STOP = threading.Event()
_THREAD = None
_RESCAN_THREADS = {}
_RESCANS = {}
_STATE = {"state": "starting", "checked": 0, "total": 0, "filled": 0,
          "failed": 0, "current": "", "last_error": "", "images_paused": False,
          "episode_total": 0, "episode_checked": 0, "episode_filled": 0}

# A failed or still-incomplete title may be retried on a subsequent day.
_MISSING = """(m.title = m.media_type || ' ' || m.media_id
    OR m.description IS NULL OR TRIM(m.description) = ''
    OR m.year IS NULL
    OR NOT EXISTS (SELECT 1 FROM media_genres mg WHERE mg.media_type=m.media_type AND mg.media_id=m.media_id)
    OR NOT EXISTS (SELECT 1 FROM images i WHERE i.owner_type=m.media_type AND i.owner_key=m.media_id AND i.image_type='poster'))"""
_VISIBLE = "NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)"


def status() -> dict:
    with _LOCK:
        return dict(_STATE)


def _set(**values):
    with _LOCK:
        _STATE.update(values)


def start():
    global _STARTED, _THREAD
    with _LOCK:
        if _STARTED or _STOP.is_set():
            return
        _STARTED = True
    _THREAD = threading.Thread(target=_run, name="metadata-repair", daemon=True)
    _THREAD.start()


def request_stop():
    _STOP.set()
    _set(state='stopping')


def wait_stop():
    if _THREAD:
        _THREAD.join()
    with _LOCK:
        rescans = list(_RESCAN_THREADS.values())
    for thread in rescans:
        thread.join()


def rescan_status(show_id):
    with _LOCK:
        return dict(_RESCANS.get(str(show_id), {"state": "idle", "checked": 0, "total": 0, "failed": 0}))


def rescan_show(show_id):
    show_id = str(show_id)
    with _LOCK:
        if _STOP.is_set():
            return False
        if _RESCANS.get(show_id, {}).get("state") == "running":
            return False
        _RESCANS[show_id] = {"state": "running", "checked": 0, "total": 0, "failed": 0, "error": ""}
    thread = threading.Thread(target=_rescan_show, args=(show_id,), name=f"rescan-tv-{show_id}", daemon=True)
    with _LOCK:
        _RESCAN_THREADS[show_id] = thread
    thread.start()
    return True


def _rescan_show(show_id):
    sync = None
    try:
        sync = MovySync(0, False, 20, media_type='tv')
        sync.enqueue = lambda *args, **kwargs: None
        sync.discover_links = lambda *args, **kwargs: None
        url = urljoin(BASE_URL, f'/tv/{show_id}')
        sync.parse_detail(url, sync.fetch(url))
        sync.db.commit()
        incomplete = '''(e.air_date IS NULL OR e.runtime_minutes IS NULL OR e.rating IS NULL
            OR e.title IS NULL OR e.title='Episode ' || e.episode_number
            OR e.description IS NULL OR TRIM(e.description)=''
            OR TRIM(e.description)=TRIM(m.description))'''
        rows = sync.db.execute(f'''SELECT e.season_number,e.episode_number,e.source_url
            FROM episodes e JOIN media m ON m.media_type='tv' AND m.media_id=e.show_id
            WHERE e.show_id=? AND {incomplete}
            ORDER BY e.season_number,e.episode_number''', (show_id,)).fetchall()
        with _LOCK:
            _RESCANS[show_id]['total'] = len(rows)
        for row in rows:
            if _STOP.is_set():
                break
            season, number = row['season_number'], row['episode_number']
            episode_url = row['source_url'] or urljoin(BASE_URL, f'/tv/{show_id}/{season}/{number}')
            try:
                sync.parse_episode(episode_url, BeautifulSoup(sync.fetch(episode_url), 'html.parser'),
                                   show_id, str(season), str(number))
                sync.db.commit()
            except Exception as exc:
                sync.db.rollback()
                with _LOCK:
                    _RESCANS[show_id]['failed'] += 1
                    _RESCANS[show_id]['error'] = str(exc)[:250]
            finally:
                with _LOCK:
                    _RESCANS[show_id]['checked'] += 1
        with _LOCK:
            _RESCANS[show_id]['state'] = 'interrupted' if _STOP.is_set() else 'complete'
    except Exception as exc:
        with _LOCK:
            _RESCANS[show_id]['state'] = 'error'
            _RESCANS[show_id]['error'] = str(exc)[:250]
    finally:
        if sync:
            sync.session.close()
            sync.db.close()
        with _LOCK:
            _RESCAN_THREADS.pop(show_id, None)


def _refresh_episode_metadata(db, limit=30):
    """Create every known episode and repair missing fields in paced batches."""
    db.execute('''CREATE TABLE IF NOT EXISTS episode_metadata_refresh (
        show_id TEXT NOT NULL, season_number INTEGER NOT NULL, episode_number INTEGER NOT NULL,
        last_attempt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(show_id,season_number,episode_number))''')
    seasons = db.execute('''SELECT s.show_id,s.season_number,s.episode_count FROM seasons s
        JOIN media m ON m.media_type='tv' AND m.media_id=s.show_id
        WHERE s.episode_count>0 AND NOT EXISTS
        (SELECT 1 FROM hidden_media h WHERE h.media_type='tv' AND h.media_id=s.show_id)
        ORDER BY s.show_id,s.season_number''').fetchall()
    for season in seasons:
        if _STOP.is_set():
            break
        for number in range(1, season['episode_count'] + 1):
            if _STOP.is_set():
                break
            url = urljoin(BASE_URL, f'/tv/{season["show_id"]}/{season["season_number"]}/{number}')
            db.execute('''INSERT OR IGNORE INTO episodes
                (show_id,season_number,episode_number,title,source_url,raw_json)
                VALUES(?,?,?,?,?,'{}')''',
                (season['show_id'], season['season_number'], number, f'Episode {number}', url))
    db.commit()
    incomplete = '''(e.air_date IS NULL OR e.runtime_minutes IS NULL OR e.rating IS NULL
        OR e.title IS NULL OR e.title='Episode ' || e.episode_number
        OR e.description IS NULL OR TRIM(e.description)=''
        OR TRIM(e.description)=TRIM(m.description))'''
    total = db.execute(f'''SELECT COUNT(*) FROM episodes e JOIN media m
        ON m.media_type='tv' AND m.media_id=e.show_id
        WHERE {incomplete} AND NOT EXISTS
        (SELECT 1 FROM hidden_media h WHERE h.media_type='tv' AND h.media_id=e.show_id)''').fetchone()[0]
    _set(state='episodes', episode_total=total, episode_checked=0, episode_filled=0)
    rows = db.execute(f'''WITH candidates AS (
        SELECT e.show_id,e.season_number,e.episode_number,e.source_url,
               ROW_NUMBER() OVER (PARTITION BY e.show_id
                   ORDER BY e.season_number DESC,e.episode_number DESC) AS queue_position
        FROM episodes e
        JOIN media m ON m.media_type='tv' AND m.media_id=e.show_id
        LEFT JOIN episode_metadata_refresh r ON r.show_id=e.show_id
            AND r.season_number=e.season_number AND r.episode_number=e.episode_number
        WHERE {incomplete} AND NOT EXISTS
        (SELECT 1 FROM hidden_media h WHERE h.media_type='tv' AND h.media_id=e.show_id)
        AND (r.last_attempt IS NULL OR r.last_attempt < datetime('now','-1 day')))
        SELECT show_id,season_number,episode_number,source_url FROM candidates
        ORDER BY queue_position,show_id LIMIT ?''', (limit,)).fetchall()
    sync = None
    try:
        for row in rows:
            if _STOP.is_set():
                break
            show_id, season, number = row['show_id'], row['season_number'], row['episode_number']
            url = row['source_url'] or urljoin(BASE_URL, f'/tv/{show_id}/{season}/{number}')
            db.execute('''INSERT INTO episode_metadata_refresh(show_id,season_number,episode_number,last_attempt)
                VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(show_id,season_number,episode_number)
                DO UPDATE SET last_attempt=CURRENT_TIMESTAMP''', (show_id, season, number))
            db.commit()
            _set(current=f'Episode: TV/{show_id} S{season:02}E{number:02}')
            try:
                if sync is None:
                    sync = MovySync(0, False, 20, media_type='tv')
                sync.parse_episode(url, BeautifulSoup(sync.fetch(url), 'html.parser'),
                                   show_id, str(season), str(number))
                sync.db.commit()
                updated = db.execute(f'''SELECT NOT {incomplete} FROM episodes e
                    JOIN media m ON m.media_type='tv' AND m.media_id=e.show_id WHERE
                    e.show_id=? AND e.season_number=? AND e.episode_number=?''',
                    (show_id, season, number)).fetchone()[0]
                if updated:
                    with _LOCK:
                        _STATE['episode_filled'] += 1
            except Exception as exc:
                if sync:
                    sync.db.rollback()
                _set(last_error=f'Episode TV/{show_id} S{season:02}E{number:02}: {exc}'[:250])
            finally:
                with _LOCK:
                    _STATE['episode_checked'] += 1
    finally:
        if sync:
            sync.session.close()
            sync.db.close()


def _run():
    while not _STOP.is_set():
        db = None
        try:
            db = connect()
            db.execute("PRAGMA busy_timeout=5000")
            total = db.execute(
                f"SELECT COUNT(*) FROM media m WHERE m.media_id GLOB '[0-9]*' AND {_VISIBLE} AND {_MISSING}"
            ).fetchone()[0]
            _set(total=total, checked=0, filled=0, failed=0, state="checking", current="", images_paused=False)
            while True:
                if _STOP.is_set():
                    break
                # Claim through SQLite so two server processes cannot fetch the same title together.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(f"""SELECT m.media_type,m.media_id,m.title FROM media m
                    LEFT JOIN metadata_refresh r ON r.media_type=m.media_type AND r.media_id=m.media_id
                    WHERE m.media_id GLOB '[0-9]*' AND {_VISIBLE} AND {_MISSING}
                    AND (r.last_attempt IS NULL OR r.last_attempt < datetime('now','-1 day'))
                    ORDER BY CASE WHEN m.description IS NULL OR TRIM(m.description)='' THEN 0 ELSE 1 END,
                             m.media_type,m.media_id LIMIT 1""").fetchone()
                if not row:
                    db.commit()
                    break
                kind, media_id, title = row
                db.execute("""INSERT INTO metadata_refresh(media_type,media_id,last_attempt,status,error)
                    VALUES(?,?,CURRENT_TIMESTAMP,'running',NULL)
                    ON CONFLICT(media_type,media_id) DO UPDATE SET
                    last_attempt=CURRENT_TIMESTAMP,status='running',error=NULL""", (kind, media_id))
                db.commit()
                _set(state="checking", current=f"{title} ({kind}/{media_id})")
                sync = None
                try:
                    sync = MovySync(0, True, 20, media_type=kind)
                    # Repair the existing title without expanding the catalog through recommendations.
                    sync.enqueue = lambda *args, **kwargs: None
                    sync.discover_links = lambda *args, **kwargs: None
                    url = urljoin(BASE_URL, f"/{kind}/{media_id}")
                    sync.parse_detail(url, sync.fetch(url))
                    sync.db.commit()
                    # Save artwork belonging to this title, including a newly found poster.
                    images = sync.db.execute("""SELECT * FROM images WHERE owner_type=? AND owner_key=?
                        AND local_path IS NULL AND image_type IN ('poster','backdrop','logo')
                        ORDER BY image_id LIMIT 12""", (kind, media_id)).fetchall()
                    for image in images:
                        if _STOP.is_set():
                            break
                        try:
                            sync.cache_image(image)
                            sync.db.commit()
                        except BackgroundImagePaused:
                            sync.db.rollback()
                            _set(images_paused=True)
                            break
                        except Exception:
                            sync.db.rollback()
                    complete = not db.execute(
                        f"SELECT {_MISSING} FROM media m WHERE m.media_type=? AND m.media_id=?",
                        (kind, media_id),
                    ).fetchone()[0]
                    if complete:
                        with _LOCK:
                            _STATE["filled"] += 1
                    db.execute("UPDATE metadata_refresh SET status='done',error=NULL WHERE media_type=? AND media_id=?", (kind, media_id))
                except Exception as exc:
                    error = str(exc)[:250]
                    with _LOCK:
                        _STATE["failed"] += 1
                        _STATE["last_error"] = error
                    db.execute("UPDATE metadata_refresh SET status='error',error=? WHERE media_type=? AND media_id=?", (error, kind, media_id))
                finally:
                    if sync is not None:
                        sync.session.close()
                        sync.db.close()
                    db.commit()
                    with _LOCK:
                        _STATE["checked"] += 1
                    _STOP.wait(1)
                if _STOP.is_set():
                    break
                if status()['checked'] % 10 == 0:
                    _refresh_episode_metadata(db, limit=5)
            if not _STOP.is_set():
                _refresh_episode_metadata(db)
            db.close()
            _set(state="waiting", current="")
        except Exception as exc:
            _set(state="error", current="", last_error=str(exc)[:250])
        finally:
            if db:
                db.close()
        _STOP.wait(60)
    _set(state='stopped',current='')
