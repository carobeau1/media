"""Give foreground page loads first access to paced outbound requests."""
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from media_db import connect

DEFAULT_DELAY = 5.0
ALLOWED_DELAYS = (2.0, 5.0, 10.0, 20.0)
DEFAULT_INTERACTIVE_DELAY = 0.5
INTERACTIVE_DELAYS = (0.25, 0.5, 1.0, 2.0)
_condition = threading.Condition()
_thread = threading.local()
_last_started = 0.0
_interactive_scopes = 0
_interactive_waiters = 0
_batch_scopes = 0
_active = {}
_sequence = 0
_last_finished = 0.0


def settings():
    db = connect()
    try:
        row = db.execute('SELECT delay_seconds,interactive_delay_seconds FROM request_settings WHERE setting_id=1').fetchone()
        if row:
            return {'delay_seconds': float(row[0]), 'interactive_delay_seconds': float(row[1])}
    except Exception:
        try:
            row = db.execute('SELECT delay_seconds FROM request_settings WHERE setting_id=1').fetchone()
            if row:
                return {'delay_seconds': float(row[0]), 'interactive_delay_seconds': DEFAULT_INTERACTIVE_DELAY}
        except Exception:
            pass
    finally:
        db.close()
    return {'delay_seconds': DEFAULT_DELAY, 'interactive_delay_seconds': DEFAULT_INTERACTIVE_DELAY}


def configured_delay():
    return settings()['delay_seconds']


def is_interactive():
    return bool(getattr(_thread, 'depth', 0))


@contextmanager
def batch_scope():
    """Prioritize an import over routine background work, below page requests."""
    global _batch_scopes
    with _condition:
        _batch_scopes += 1
        _thread.batch_depth = getattr(_thread, 'batch_depth', 0) + 1
        _condition.notify_all()
    try:
        yield
    finally:
        with _condition:
            _thread.batch_depth -= 1
            _batch_scopes -= 1
            _condition.notify_all()


def set_delays(background=None, interactive=None):
    if background is not None and (type(background) not in (int, float) or float(background) not in ALLOWED_DELAYS):
        raise ValueError('Choose a background interval of 2, 5, 10, or 20 seconds.')
    if interactive is not None and (type(interactive) not in (int, float) or float(interactive) not in INTERACTIVE_DELAYS):
        raise ValueError('Choose an interactive interval of 0.25, 0.5, 1, or 2 seconds.')
    if background is None and interactive is None:
        raise ValueError('Select an interval to update.')
    db = connect()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS request_settings (
            setting_id INTEGER PRIMARY KEY CHECK(setting_id=1),
            delay_seconds REAL NOT NULL,
            interactive_delay_seconds REAL NOT NULL DEFAULT 0.5)''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(request_settings)')}
        if 'interactive_delay_seconds' not in columns:
            db.execute('ALTER TABLE request_settings ADD COLUMN interactive_delay_seconds REAL NOT NULL DEFAULT 0.5')
        db.execute('INSERT OR IGNORE INTO request_settings(setting_id,delay_seconds) VALUES(1,?)', (DEFAULT_DELAY,))
        if background is not None:
            db.execute('UPDATE request_settings SET delay_seconds=? WHERE setting_id=1', (float(background),))
        if interactive is not None:
            db.execute('UPDATE request_settings SET interactive_delay_seconds=? WHERE setting_id=1', (float(interactive),))
        db.commit()
    finally:
        db.close()
    with _condition:
        _condition.notify_all()


def set_delay(value):
    set_delays(background=value)


@contextmanager
def interactive_scope():
    """Hold background starts until the page's external work finishes."""
    global _interactive_scopes
    with _condition:
        _interactive_scopes += 1
        _thread.depth = getattr(_thread, 'depth', 0) + 1
        _condition.notify_all()
    try:
        yield
    finally:
        with _condition:
            _thread.depth -= 1
            _interactive_scopes -= 1
            _condition.notify_all()


def wait_for_request(interactive=None):
    """Wait for the appropriate interval; foreground work wins any contention."""
    global _last_started, _interactive_waiters
    if interactive is None:
        interactive = is_interactive()
    batch = not interactive and bool(getattr(_thread, 'batch_depth', 0))
    with _condition:
        if interactive:
            _interactive_waiters += 1
            _condition.notify_all()
        try:
            while True:
                if not interactive and (_interactive_scopes or _interactive_waiters or
                        (not batch and _batch_scopes)):
                    _condition.wait()
                    continue
                interval = settings()['interactive_delay_seconds' if interactive else 'delay_seconds']
                remaining = _last_started + interval - time.monotonic()
                if remaining > 0:
                    _condition.wait(timeout=remaining)
                    continue
                _last_started = time.monotonic()
                _condition.notify_all()
                return
        finally:
            if interactive:
                _interactive_waiters -= 1
                _condition.notify_all()


@contextmanager
def outgoing_request(url):
    global _sequence, _last_finished
    wait_for_request()
    with _condition:
        _sequence += 1
        token = _sequence
        _active[token] = urlparse(url).hostname or 'external site'
    try:
        yield
    finally:
        with _condition:
            _active.pop(token, None)
            _last_finished = time.monotonic()


def activity():
    with _condition:
        return {'active': len(_active), 'recent': time.monotonic() - _last_finished < 3,
                'sites': sorted(set(_active.values()))}
