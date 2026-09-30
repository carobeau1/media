"""Persistent, sequential background jobs for pasted or uploaded movie IDs."""
from __future__ import annotations

import json
import threading

from media_db import connect
import request_pacing

_lock = threading.Lock()
_started = False
_stop = threading.Event()
_thread = None


def _db():
    db = connect()
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('''CREATE TABLE IF NOT EXISTS movie_import_jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued',
        ids_json TEXT NOT NULL,
        results_json TEXT NOT NULL DEFAULT '[]',
        checked INTEGER NOT NULL DEFAULT 0,
        added INTEGER NOT NULL DEFAULT 0,
        existing INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        current TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    db.commit()
    return db


def enqueue(account_id, ids):
    if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(
            not isinstance(item, str) or not item.isdigit() or len(item) > 15 for item in ids):
        raise ValueError('Provide 1 to 10,000 valid numeric movie IDs.')
    ids = list(dict.fromkeys(ids))
    db = _db()
    try:
        db.execute('INSERT INTO movie_import_jobs(account_id,ids_json) VALUES(?,?)',
                   (account_id, json.dumps(ids)))
        db.commit()
    finally:
        db.close()
    return status(account_id)


def status(account_id):
    db = _db()
    try:
        row = db.execute('''SELECT * FROM movie_import_jobs WHERE account_id=?
            ORDER BY CASE WHEN state='running' THEN 0 WHEN state='queued' THEN 1 ELSE 2 END,
            job_id DESC LIMIT 1''', (account_id,)).fetchone()
        if not row:
            return {'state': 'idle'}
        result = dict(row)
        result['total'] = len(json.loads(result.pop('ids_json')))
        result['results'] = json.loads(result.pop('results_json'))
        return result
    finally:
        db.close()


def start_worker(app, importer):
    global _started, _thread
    with _lock:
        if _started or _stop.is_set():
            return
        _started = True
        db = _db()
        try:
            # Continue an interrupted batch from its last recorded ID.
            db.execute("UPDATE movie_import_jobs SET state='queued' WHERE state='running'")
            db.commit()
        finally:
            db.close()
        _thread = threading.Thread(target=_worker, args=(app, importer), name='movie-import', daemon=True)
        _thread.start()


def request_stop():
    _stop.set()


def wait_stop():
    if _thread:
        _thread.join()


def _worker(app, importer):
    while not _stop.is_set():
        row = None
        db = _db()
        try:
            row = db.execute("SELECT * FROM movie_import_jobs WHERE state IN ('queued','running') ORDER BY job_id LIMIT 1").fetchone()
            if not row:
                # Keep one sleeping worker to pick up newly queued jobs.
                pass
            else:
                job_id = row['job_id']
                db.execute("UPDATE movie_import_jobs SET state='running' WHERE job_id=?", (job_id,))
                db.commit()
                ids = json.loads(row['ids_json'])
                results = json.loads(row['results_json'])
                for movie_id in ids[row['checked']:]:
                    if _stop.is_set():
                        break
                    db.execute('UPDATE movie_import_jobs SET current=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?',
                               (movie_id, job_id))
                    db.commit()
                    try:
                        previous = db.execute("SELECT title FROM media WHERE media_type='movie' AND media_id=?", (movie_id,)).fetchone()
                        if previous:
                            result = {'id': movie_id, 'status': 'existing', 'title': previous['title']}
                        else:
                            with app.app_context(), request_pacing.batch_scope():
                                record = importer(movie_id)
                            result = {'id': movie_id, 'status': 'added', 'title': record['title']}
                    except Exception as exc:
                        app.logger.warning('Movie import failed for %s: %s', movie_id, exc)
                        result = {'id': movie_id, 'status': 'failed', 'error': 'Could not fetch this movie; retry later.'}
                    # Keep the job record and status endpoint small for large spreadsheets.
                    results.append(result)
                    if len(results) > 100:
                        results = results[-100:]
                    field = {'added': 'added', 'existing': 'existing', 'failed': 'failed'}[result['status']]
                    db.execute(f'''UPDATE movie_import_jobs SET checked=checked+1,{field}={field}+1,
                        results_json=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?''',
                        (json.dumps(results), job_id))
                    db.commit()
                db.execute("UPDATE movie_import_jobs SET state=?,current='',updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                           ('queued' if _stop.is_set() else 'complete', job_id))
                db.commit()
        except Exception:
            app.logger.exception('Movie import worker error')
        finally:
            db.close()
        _stop.wait(1 if row else 2)
