"""Run the project's movie downloader and expose its live yt-dlp progress."""
from __future__ import annotations

import codecs
import json
from collections import deque
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from media_db import connect

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / 'download.py'
_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_QUEUE = deque()
_WORKER_ACTIVE = False
_WORKER_THREAD: threading.Thread | None = None
_RUN_THREADS: dict[str, threading.Thread] = {}
_PROCESSES: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()
_WAKE_WORKER = threading.Event()
_LOADED = False
_STOPPING = False
_PAUSED = False
_PROGRESS = re.compile(r'(?:\[download\]|yt-dlp).*?\b(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%', re.I)
_DOWNLOAD_SIZE = re.compile(r'\bof\s+~?\s*([\d,.]+)\s*(GiB|MiB|KiB|GB|MB|KB|B)\b', re.I)
_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
_PROBE_LOCK = threading.Lock()
_PROBE_CACHE = {}


def _load_queue():
    """Called with _LOCK held; running jobs from a previous process resume first."""
    global _LOADED, _WORKER_ACTIVE, _WORKER_THREAD, _PAUSED
    if _LOADED:
        return
    db = connect()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS download_queue (
            job_key TEXT PRIMARY KEY, job_json TEXT NOT NULL,
            queue_position INTEGER NOT NULL, state TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS download_settings (
            setting_id INTEGER PRIMARY KEY CHECK(setting_id=1),
            download_threads INTEGER NOT NULL DEFAULT 4)''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(download_settings)')}
        if 'queue_paused' not in columns:
            db.execute('ALTER TABLE download_settings ADD COLUMN queue_paused INTEGER NOT NULL DEFAULT 0')
        if 'download_threads' not in columns:
            db.execute('ALTER TABLE download_settings ADD COLUMN download_threads INTEGER NOT NULL DEFAULT 4')
        setting = db.execute('SELECT queue_paused FROM download_settings WHERE setting_id=1').fetchone()
        _PAUSED = bool(setting[0]) if setting else False
        rows = db.execute('SELECT job_key,job_json FROM download_queue ORDER BY queue_position').fetchall()
        for row in rows:
            try:
                job = json.loads(row['job_json'])
                key = row['job_key']
                if job['kind'] not in ('movie', 'tv') or not str(job['id']).isdigit():
                    continue
                job.update(key=key,state='queued',progress=None,message='Waiting in download queue',
                           started_at=None,progress_started_at=None)
                _JOBS[key] = job
                _QUEUE.append(key)
            except (ValueError, KeyError, TypeError):
                continue
        db.commit()
    finally:
        db.close()
    _LOADED = True
    if _QUEUE and not _STOPPING and not _PAUSED:
        _WORKER_ACTIVE = True
        _WORKER_THREAD = threading.Thread(target=_worker, name='media-download-queue', daemon=True)
        _WORKER_THREAD.start()


def _save_queue():
    """Commit pending order before returning from any queue mutation. Lock held."""
    db = connect()
    try:
        db.execute('DELETE FROM download_queue')
        pending = [key for key, job in _JOBS.items() if job['state'] == 'running'] + list(_QUEUE)
        for position, key in enumerate(pending):
            job = _JOBS[key]
            db.execute('INSERT INTO download_queue(job_key,job_json,queue_position,state) VALUES(?,?,?,?)',
                       (key, json.dumps({field:value for field,value in job.items()
                                         if field not in ('finished_monotonic',)}),
                        position, job['state']))
        db.commit()
    finally:
        db.close()


def start_worker():
    with _LOCK:
        _load_queue()


def request_stop():
    """Finish the active file; do not start another queued download."""
    global _STOPPING
    with _LOCK:
        _STOPPING = True
        _load_queue()
        _save_queue()
        _WAKE_WORKER.set()


def queue_paused() -> bool:
    with _LOCK:
        _load_queue()
        return _PAUSED


def set_queue_paused(paused: bool) -> bool:
    if type(paused) is not bool:
        raise ValueError('Choose whether to pause the download queue.')
    global _PAUSED, _WORKER_ACTIVE, _WORKER_THREAD
    with _LOCK:
        _load_queue()
        db = connect()
        try:
            db.execute('''INSERT INTO download_settings(setting_id,queue_paused) VALUES(1,?)
                ON CONFLICT(setting_id) DO UPDATE SET queue_paused=excluded.queue_paused''', (int(paused),))
            db.commit()
        finally:
            db.close()
        _PAUSED = paused
        _WAKE_WORKER.set()
        if not paused and _QUEUE and not _STOPPING and not _WORKER_ACTIVE:
            _WORKER_ACTIVE = True
            _WORKER_THREAD = threading.Thread(target=_worker, name='media-download-queue', daemon=True)
            _WORKER_THREAD.start()
        return _PAUSED


def wait_stop():
    with _LOCK:
        worker = _WORKER_THREAD
    if worker:
        worker.join()
    # The coordinator exits on shutdown while in-flight downloads finish safely.
    while True:
        with _LOCK:
            runners = list(_RUN_THREADS.values())
        if not runners:
            break
        for runner in runners:
            runner.join()


def _terminate_download(process: subprocess.Popen) -> None:
    """Stop download.py and its yt-dlp child process as one process group."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def edit_queue(key: str, action: str) -> dict:
    process = None
    with _LOCK:
        _load_queue()
        if action == 'stop':
            if _JOBS.get(key, {}).get('state') != 'running':
                raise ValueError('Choose a running download to stop.')
            _CANCELLED.add(key)
            process = _PROCESSES.get(key)
            _JOBS.pop(key)
            _save_queue()  # A restart must not resume the cancelled job.
            _WAKE_WORKER.set()
            result = {'jobs': _active_status()}
        else:
            if action not in ('remove', 'up', 'down') or key not in _QUEUE:
                raise ValueError('Choose a waiting download to remove or move.')
            pending = list(_QUEUE)
            index = pending.index(key)
            if action == 'remove':
                pending.pop(index)
                _JOBS.pop(key, None)
            else:
                other = index + (-1 if action == 'up' else 1)
                if not 0 <= other < len(pending):
                    raise ValueError('This download is already at the end of the queue.')
                pending[index], pending[other] = pending[other], pending[index]
            _QUEUE.clear()
            _QUEUE.extend(pending)
            _save_queue()
            result = {'jobs': _active_status()}
    if process:
        _terminate_download(process)
    return result


def threads_setting() -> int:
    db = connect()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS download_settings (
            setting_id INTEGER PRIMARY KEY CHECK(setting_id=1),
            download_threads INTEGER NOT NULL DEFAULT 4)''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(download_settings)')}
        if 'download_threads' not in columns:
            db.execute('ALTER TABLE download_settings ADD COLUMN download_threads INTEGER NOT NULL DEFAULT 4')
        row = db.execute('SELECT download_threads FROM download_settings WHERE setting_id=1').fetchone()
        db.commit()
        return int(row[0]) if row else 4
    finally:
        db.close()


def set_threads(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 16:
        raise ValueError('Choose 1 to 16 download threads.')
    db = connect()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS download_settings (
            setting_id INTEGER PRIMARY KEY CHECK(setting_id=1),
            download_threads INTEGER NOT NULL DEFAULT 4)''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(download_settings)')}
        if 'download_threads' not in columns:
            db.execute('ALTER TABLE download_settings ADD COLUMN download_threads INTEGER NOT NULL DEFAULT 4')
        db.execute('''INSERT INTO download_settings(setting_id,download_threads) VALUES(1,?)
            ON CONFLICT(setting_id) DO UPDATE SET download_threads=excluded.download_threads''', (value,))
        db.commit()
    finally:
        db.close()


def file_complete(movie_id: str, title: str, year: int | None) -> bool:
    """yt-dlp renames its .part file to the configured filename on completion."""
    try:
        return movie_file(movie_id, title, year) is not None
    except (OSError, ValueError):
        return False


def movie_file(movie_id: str, title: str, year: int | None) -> Path | None:
    """Find a completed movie in the configured per-ID directory."""
    from download import configured_output_path
    destination = ROOT / configured_output_path('movie', title, movie_id, year=year)
    folder = destination.parent
    if not folder.is_dir():
        return None
    candidates = [destination, *sorted(folder.glob('*.mp4'))]
    with _LOCK:
        downloading = _JOBS.get(movie_id, {}).get('state') in ('running', 'queued')
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() == '.mp4' and candidate.stat().st_size > 0:
            # An existing completed file is still playable during a replacement download.
            if downloading and candidate.with_name(candidate.name + '.part').exists():
                continue
            return candidate
    return None


def movie_subtitle(video: Path) -> Path | None:
    exact = video.with_suffix('.srt')
    if exact.is_file():
        return exact
    return next((path for path in sorted(video.parent.glob('*.srt'))
                 if path.is_file() and path.stem.startswith(video.stem + '.')), None)


def media_info(video: Path) -> dict:
    """Probe a completed file once per file version; avoid repeated network-drive reads."""
    stat = video.stat()
    cache_key = (str(video), stat.st_size, stat.st_mtime_ns)
    with _PROBE_LOCK:
        if cache_key in _PROBE_CACHE:
            return dict(_PROBE_CACHE[cache_key])
    info = {'resolution': 'Unavailable', 'video_codec': 'Unavailable',
            'audio_codec': 'Unavailable', 'filesize_bytes': stat.st_size}
    codecs = {'h264':'H.264', 'hevc':'HEVC (H.265)', 'av1':'AV1',
              'vp9':'VP9', 'mpeg4':'MPEG-4', 'aac':'AAC', 'ac3':'AC-3',
              'eac3':'E-AC-3', 'opus':'Opus', 'mp3':'MP3', 'flac':'FLAC'}
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                 'stream=codec_type,codec_name,width,height',
                                 '-of', 'json', str(video)], capture_output=True,
                                text=True, timeout=12, check=False)
        if result.returncode == 0:
            for stream in json.loads(result.stdout).get('streams', []):
                kind = stream.get('codec_type')
                name = str(stream.get('codec_name') or '').lower()
                if kind == 'video' and info['video_codec'] == 'Unavailable':
                    info['video_codec'] = codecs.get(name, name.upper() or 'Unavailable')
                    if stream.get('width') and stream.get('height'):
                        info['resolution'] = f"{stream['width']}×{stream['height']}"
                if kind == 'audio' and info['audio_codec'] == 'Unavailable':
                    info['audio_codec'] = codecs.get(name, name.upper() or 'Unavailable')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    with _PROBE_LOCK:
        if len(_PROBE_CACHE) >= 64:
            _PROBE_CACHE.clear()
        _PROBE_CACHE[cache_key] = info
    return dict(info)


_MEDIA_INFO_LOCK = threading.Lock()
_MEDIA_INFO_RUNNING: set[tuple] = set()
_MEDIA_INFO_MEMORY: dict[tuple, dict] = {}


def cached_media_info(movie_id: str, video: Path) -> dict:
    """Return persisted information immediately; probe new file versions off thread."""
    stat = video.stat()
    version = (str(video), stat.st_size, stat.st_mtime_ns)
    with _MEDIA_INFO_LOCK:
        if version in _MEDIA_INFO_MEMORY:
            return dict(_MEDIA_INFO_MEMORY[version])
    db = connect()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS movie_media_info (
            movie_id TEXT PRIMARY KEY, file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL, file_mtime_ns INTEGER NOT NULL,
            info_json TEXT NOT NULL)''')
        row = db.execute('SELECT file_path,file_size,file_mtime_ns,info_json FROM movie_media_info WHERE movie_id=?',
                         (movie_id,)).fetchone()
    finally:
        db.close()
    if row and (row['file_path'], row['file_size'], row['file_mtime_ns']) == version:
        info = json.loads(row['info_json'])
        with _MEDIA_INFO_LOCK:
            _MEDIA_INFO_MEMORY[version] = info
        return dict(info)
    with _MEDIA_INFO_LOCK:
        if version not in _MEDIA_INFO_RUNNING:
            _MEDIA_INFO_RUNNING.add(version)
            threading.Thread(target=_save_media_info, args=(movie_id, video, version), daemon=True).start()
    return {'resolution': 'Checking…', 'video_codec': 'Checking…',
            'audio_codec': 'Checking…', 'filesize_bytes': stat.st_size}


def _save_media_info(movie_id: str, video: Path, version: tuple) -> None:
    try:
        info = media_info(video)
        stat = video.stat()
        if (str(video), stat.st_size, stat.st_mtime_ns) != version:
            return
        db = connect()
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS movie_media_info (
                movie_id TEXT PRIMARY KEY, file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL, file_mtime_ns INTEGER NOT NULL,
                info_json TEXT NOT NULL)''')
            db.execute('''INSERT INTO movie_media_info(movie_id,file_path,file_size,file_mtime_ns,info_json)
                VALUES(?,?,?,?,?) ON CONFLICT(movie_id) DO UPDATE SET
                file_path=excluded.file_path,file_size=excluded.file_size,
                file_mtime_ns=excluded.file_mtime_ns,info_json=excluded.info_json''',
                (movie_id, *version, json.dumps(info)))
            db.commit()
        finally:
            db.close()
        with _MEDIA_INFO_LOCK:
            if len(_MEDIA_INFO_MEMORY) >= 64:
                _MEDIA_INFO_MEMORY.clear()
            _MEDIA_INFO_MEMORY[version] = info
    except (OSError, ValueError):
        pass
    finally:
        with _MEDIA_INFO_LOCK:
            _MEDIA_INFO_RUNNING.discard(version)


def status(movie_id: str) -> dict:
    with _LOCK:
        _load_queue()
        return _job_status(movie_id)


def episode_status(show_id: str, season: int, episode: int) -> dict:
    with _LOCK:
        _load_queue()
        return _job_status(f'tv:{show_id}:{season}:{episode}')


def _job_status(key: str) -> dict:
    job = dict(_JOBS.get(key, {"state": "idle", "progress": None, "message": ""}))
    job['key'] = key
    if job['state'] == 'queued':
        job['queue_position'] = list(_QUEUE).index(key) + 1
    return _timing(job)


def _timing(job):
    if job['state'] == 'running' and job.get('started_at'):
        try:
            start = datetime.fromisoformat(job['started_at']).timestamp()
            job['elapsed_seconds'] = max(0, int(time.time() - start))
            progress = job.get('progress')
            if progress and 0 < progress < 100:
                progress_start = job.get('progress_started_at') or job['started_at']
                progress_elapsed = max(0, time.time() - datetime.fromisoformat(progress_start).timestamp())
                job['eta_seconds'] = int(progress_elapsed * (100 - progress) / progress)
        except (TypeError, ValueError):
            pass
    return job


def active_status() -> list[dict]:
    """Current downloads plus briefly visible completed and failed jobs."""
    with _LOCK:
        _load_queue()
        return _active_status()


def _active_status():
    running = [_timing(dict(job)) for job in _JOBS.values() if job['state'] == 'running']
    queued = [_job_status(key) for key in _QUEUE]
    recent = [dict(job) for job in _JOBS.values() if job['state'] in ('complete', 'error')
              and time.monotonic() - job.get('finished_monotonic', 0) < 20]
    return running + queued + recent


def start(movie_id: str, title: str = '', source: str | None = None) -> tuple[bool, dict]:
    return _start(movie_id, 'movie', movie_id, title or f'Movie {movie_id}', source=source)


def start_episode(show_id: str, season: int, episode: int, title: str) -> tuple[bool, dict]:
    return _start(f'tv:{show_id}:{season}:{episode}', 'tv', show_id, title,
                  season=season, episode=episode)


def _start(key: str, kind: str, media_id: str, title: str,
           season: int | None = None, episode: int | None = None,
           source: str | None = None) -> tuple[bool, dict]:
    global _WORKER_ACTIVE, _WORKER_THREAD
    if not SCRIPT.is_file():
        return False, {"state": "error", "progress": None, "message": "download.py is missing from the app directory."}
    with _LOCK:
        _load_queue()
        if key in _CANCELLED:
            return False, {'state':'error','progress':None,'message':'The previous download is still stopping.'}
        if _STOPPING:
            return False, {"state":"error","progress":None,"message":"The server is shutting down."}
        if _JOBS.get(key, {}).get('state') in ('running', 'queued'):
            return False, _job_status(key)
        _JOBS[key] = {"state": "queued", "progress": None, "message": "Waiting in download queue",
                           "key":key, "kind": kind, "id": media_id, "title": title,
                           "season": season, "episode": episode, "source": source,
                           "queued_at":datetime.now(timezone.utc).isoformat(),
                           "started_at": None}
        _QUEUE.append(key)
        _save_queue()
        _WAKE_WORKER.set()
        if not _WORKER_ACTIVE and not _PAUSED:
            _WORKER_ACTIVE = True
            _WORKER_THREAD = threading.Thread(target=_worker, name='media-download-queue', daemon=True)
            _WORKER_THREAD.start()
        return True, _job_status(key)


def _worker() -> None:
    global _WORKER_ACTIVE
    while True:
        with _LOCK:
            running = [job for job in _JOBS.values() if job['state'] == 'running']
            if _STOPPING or (not _QUEUE and not running) or (_PAUSED and not running):
                _WORKER_ACTIVE = False
                return
            # Only overlap final processing at 100% with the next transfer.
            can_start = (not _PAUSED and _QUEUE and len(running) < 2 and
                         (not running or any((job.get('progress') or 0) >= 100 for job in running)))
            if can_start:
                key = _QUEUE.popleft()
                job = _JOBS[key]
                job['state'] = 'running'
                job['message'] = 'Starting download…'
                job['started_at'] = datetime.now(timezone.utc).isoformat()
                job['progress_started_at'] = None
                job['progress'] = None
                _save_queue()
                args = ['movie', job['id']] if job['kind'] == 'movie' else [
                    'tv', job['id'], str(job['season']), str(job['episode'])]
                if job['kind'] == 'movie':
                    args.append('--ignore-metadata-errors')
                    if job.get('source'):
                        args += ['--source', job['source']]
                runner = threading.Thread(target=_run_job, args=(key, args),
                                          name=f'media-download-{key}', daemon=True)
                _RUN_THREADS[key] = runner
                runner.start()
                continue
            _WAKE_WORKER.clear()
        _WAKE_WORKER.wait()


def _run_job(key: str, args: list[str]) -> None:
    try:
        _run(key, args)
    finally:
        with _LOCK:
            _RUN_THREADS.pop(key, None)
            _CANCELLED.discard(key)
            _WAKE_WORKER.set()


def _run(key: str, args: list[str]) -> None:
    process = None
    try:
        env = dict(os.environ, PYTHONUNBUFFERED='1')
        process = subprocess.Popen([sys.executable, '-u', str(SCRIPT), *args,
                                    '--download-threads', str(threads_setting())],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=env, bufsize=0,
                                   **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                                      if os.name == 'nt' else {'start_new_session': True}))
        with _LOCK:
            _PROCESSES[key] = process
            cancelled = key in _CANCELLED
        if cancelled:
            _terminate_download(process)
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        pending = ''
        def record(line: str) -> None:
            clean = re.sub(r'https?://\S+', '[URL]', _ANSI.sub('', line)).strip()
            if not clean:
                return
            match = _PROGRESS.search(clean)
            size_match = _DOWNLOAD_SIZE.search(clean)
            with _LOCK:
                job = _JOBS.get(key)
                if job is None or key in _CANCELLED:
                    return
                if size_match:
                    units = {'b':1,'kb':1000,'mb':1000**2,'gb':1000**3,
                             'kib':1024,'mib':1024**2,'gib':1024**3}
                    job['total_bytes'] = int(float(size_match.group(1).replace(',','')) * units[size_match.group(2).lower()])
                if match:
                    if job.get('progress_started_at') is None:
                        job['progress_started_at'] = datetime.now(timezone.utc).isoformat()
                    job['progress'] = max(job['progress'] or 0, min(100, float(match.group(1))))
                    if job['progress'] >= 100:
                        _WAKE_WORKER.set()
                job['message'] = clean[-300:]
        assert process.stdout is not None
        while True:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += decoder.decode(chunk)
            parts = re.split(r'[\r\n]', pending)
            pending = parts.pop()
            for line in parts:
                record(line)
            if len(pending) > 4096:
                record(pending)
                pending = ''
        record(pending + decoder.decode(b'', final=True))
        code = process.wait()
        if code == 0:
            from downloaded_filter import invalidate
            invalidate()
        with _LOCK:
            job = _JOBS.get(key)
            if job is None or key in _CANCELLED:
                return
            job['state'] = 'complete' if code == 0 else 'error'
            if code == 0:
                job['progress'] = 100
                job['message'] = 'Download finished'
            else:
                job['message'] = f'Download failed (exit {code}): {job["message"]}'
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
            job['finished_monotonic'] = time.monotonic()
            _save_queue()
    except Exception as exc:
        with _LOCK:
            if key in _JOBS and key not in _CANCELLED:
                _JOBS[key].update(state='error', message=f'Download could not start: {exc}',
                                       finished_at=datetime.now(timezone.utc).isoformat(),
                                       finished_monotonic=time.monotonic())
                _save_queue()
    finally:
        with _LOCK:
            _PROCESSES.pop(key, None)
        if process and process.stdout:
            process.stdout.close()
