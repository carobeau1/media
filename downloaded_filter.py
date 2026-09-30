"""Index completed local media without probing every catalogue title on the drive."""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from threading import Lock, Thread

from media_db import connect
from download import configured_output_path, load_source_config

ROOT = Path(__file__).resolve().parent
_lock = Lock()
_scan_lock = Lock()
_cached: set[tuple[str, str]] = set()
_episodes: set[tuple[str, int, int]] = set()
_ready = False
_storage_stats = {'downloaded_movies': 0, 'average_movie_bytes': None,
                  'media_free_bytes': None, 'app_free_bytes': None}
_worker_started = False
_EPISODE = re.compile(r'(?<!\d)S0*(\d+)E0*(\d+)(?!\d)', re.I)


def _entries(folder):
    try:
        with os.scandir(folder) as listing:
            return list(listing)
    except OSError:
        return []


def _videos(folder):
    entries = _entries(folder)
    names = {entry.name for entry in entries}
    for entry in entries:
        try:
            if (entry.name.lower().endswith('.mp4') and entry.name + '.part' not in names
                    and entry.is_file() and entry.stat().st_size > 0):
                yield entry.name
        except OSError:
            continue


def _scan():
    db = connect()
    try:
        rows = db.execute('SELECT media_type,media_id,title,year FROM media').fetchall()
    finally:
        db.close()
    known = {(row['media_type'], str(row['media_id'])) for row in rows}
    found, episodes = set(), set()
    movie_sizes = {}
    config = load_source_config()
    movie_fast = config['movie_download_path'].replace('\\', '/').startswith('media/movie/<movie-id>/')
    tv_fast = config['tv_download_path'].replace('\\', '/').startswith(
        'media/tv/<title> [<publication-year>] [<series-id>]/Series <series-number>/')
    if movie_fast:
        for item in _entries(ROOT / 'media' / 'movie'):
            try:
                if item.name.isdecimal() and ('movie', item.name) in known and item.is_dir():
                    for filename in _videos(item.path):
                        movie_sizes[item.name] = (Path(item.path) / filename).stat().st_size
                        found.add(('movie', item.name))
                        break
            except OSError:
                continue
    if tv_fast:
        for item in _entries(ROOT / 'media' / 'tv'):
            match = re.search(r'\[(\d+)\]$', item.name)
            if not match or ('tv', match.group(1)) not in known:
                continue
            try:
                if not item.is_dir():
                    continue
                for season_dir in _entries(item.path):
                    season_match = re.fullmatch(r'Series (\d+)', season_dir.name, re.I)
                    if not season_match or not season_dir.is_dir():
                        continue
                    for filename in _videos(season_dir.path):
                        found.add(('tv', match.group(1)))
                        episode_match = _EPISODE.search(filename)
                        if episode_match and int(episode_match.group(1)) == int(season_match.group(1)):
                            episodes.add((match.group(1), int(episode_match.group(1)), int(episode_match.group(2))))
            except OSError:
                continue
    # Unusual user-configured layouts keep their original per-title fallback.
    for row in rows:
        kind, media_id = row['media_type'], str(row['media_id'])
        if (kind == 'movie' and movie_fast) or (kind == 'tv' and tv_fast):
            continue
        try:
            target = ROOT / configured_output_path(kind, row['title'], media_id,
                                                    year=row['year'], season=1, episode=1)
            folder = target.parent if kind == 'movie' else target.parent.parent
            if kind == 'movie':
                for filename in _videos(folder):
                    movie_sizes[media_id] = (folder / filename).stat().st_size
                    found.add((kind, media_id))
                    break
            else:
                for season_dir in _entries(folder):
                    season_match = re.fullmatch(r'Series (\d+)', season_dir.name, re.I)
                    if not season_match or not season_dir.is_dir():
                        continue
                    for filename in _videos(season_dir.path):
                        found.add((kind, media_id))
                        match = _EPISODE.search(filename)
                        if match:
                            episodes.add((media_id, int(match.group(1)), int(match.group(2))))
        except (OSError, ValueError):
            continue
    def free_bytes(folder):
        try:
            return shutil.disk_usage(folder).free
        except OSError:
            return None

    # A missing media directory uses the application drive, where it will be created.
    media_root = ROOT / 'media'
    stats = {'downloaded_movies': len(movie_sizes),
             'average_movie_bytes': sum(movie_sizes.values()) // len(movie_sizes) if movie_sizes else None,
             'media_free_bytes': free_bytes(media_root if media_root.exists() else ROOT)
             if not (media_root.is_symlink() and not media_root.exists()) else None,
             'app_free_bytes': free_bytes(ROOT)}
    return found, episodes, stats


def _refresh():
    global _cached, _episodes, _storage_stats, _ready
    with _scan_lock:
        try:
            found, episodes, stats = _scan()
        except (OSError, ValueError):
            return
        with _lock:
            _cached, _episodes, _storage_stats, _ready = found, episodes, stats, True


def storage_stats():
    """Read the latest background snapshot without accessing the media drive."""
    with _lock:
        return dict(_storage_stats)


def _work():
    while True:
        _refresh()
        time.sleep(20)


def start_background():
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    Thread(target=_work, name='downloaded-media-index', daemon=True).start()


def invalidate():
    Thread(target=_refresh, name='downloaded-media-refresh', daemon=True).start()


def available() -> set[tuple[str, str]]:
    with _lock:
        if _ready:
            return _cached.copy()
    _refresh()
    with _lock:
        return _cached.copy()


def episode_available(title, media_id, year, season, episode):
    available()
    try:
        key = (str(media_id), int(season), int(episode))
    except (TypeError, ValueError):
        return False
    with _lock:
        return key in _episodes
