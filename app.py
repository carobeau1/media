from __future__ import annotations

import html as html_module
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin

from flask import Flask, abort, g, jsonify, render_template, request, send_from_directory, send_file, Response, session, redirect, url_for, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

from media_db import DB_PATH, connect, media_for_app
from sync_media import BASE_URL, MovySync
from delete_media import delete_media
from metadata_worker import start as start_metadata_worker, status as metadata_worker_status, rescan_show, rescan_status, request_stop as metadata_worker_stop, wait_stop as metadata_worker_wait
import similar_scan
import request_pacing
import movie_download
from download import build_cloud_url, build_source_url, load_source_config
import movie_import
from movie_import_xlsx import read_ids as read_movie_ids_xlsx
from downloaded_filter import available as downloaded_titles, episode_available, start_background as start_downloaded_index, storage_stats


ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
_secret_path = ROOT / ".signup-secret"
if not _secret_path.exists():
    try:
        _secret_path.write_text(secrets.token_hex(32), encoding="ascii")
        os.chmod(_secret_path, 0o600)
    except FileExistsError:
        pass
app.secret_key = os.environ.get("MOVY_SECRET_KEY") or _secret_path.read_text(encoding="ascii").strip()
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"
_request_condition = threading.Condition()
_active_requests = 0


@app.before_request
def track_active_request():
    global _active_requests
    with _request_condition:
        _active_requests += 1


@app.teardown_request
def finish_active_request(error=None):
    global _active_requests
    with _request_condition:
        _active_requests -= 1
        _request_condition.notify_all()


@app.before_request
def ensure_metadata_worker():
    movie_download.start_worker()
    start_downloaded_index()
    start_metadata_worker()
    movie_import.start_worker(app, ensure_movie_data)


@app.after_request
def add_database_status_footer(response):
    if not response.content_type.startswith("text/html"):
        return response
    connection = connect()
    counts = {
        media_type: count
        for media_type, count in connection.execute(
            """SELECT media_type,COUNT(*) FROM media m WHERE NOT EXISTS (
               SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
               GROUP BY media_type"""
        )
    }
    people = connection.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    hidden = [(row[0], row[1]) for row in connection.execute(
        "SELECT media_type,media_id FROM hidden_media")]
    blocked = parental_blocked_titles()
    if downloaded_only():
        all_titles = {(row[0], row[1]) for row in connection.execute('SELECT media_type,media_id FROM media')}
        local_titles = downloaded_titles()
        blocked |= all_titles - local_titles
        counts = {kind: sum(1 for title_kind, media_id in local_titles
                            if title_kind == kind and (title_kind, media_id) not in blocked
                            and (title_kind, media_id) not in hidden) for kind in ('movie','tv')}
    connection.close()
    footer = f"""
<style id="database-status-footer-style">
  body {{ padding-bottom: 52px !important; }}
  img.HomeBanner-module-scss-module__eAYNsa__backdropImg {{
    opacity: 1 !important; visibility: visible !important; display: block !important;
  }}
  .HeroCopy-module-scss-module__Y82t7W__metaCollapseClosed {{
    grid-template-rows: 1fr !important;
  }}
  .HeroCopy-module-scss-module__Y82t7W__metaCollapseClosed
  .HeroCopy-module-scss-module__Y82t7W__metaCollapseInner {{
    opacity: 1 !important; transform: none !important; overflow: visible !important;
  }}
  .database-status-footer {{
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483000;
    min-height: 48px; display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 3px 10px; gap: 2px; box-sizing: border-box;
    color: rgba(255,255,255,.88); background: rgba(14,14,16,.94);
    border-top: 1px solid rgba(255,255,255,.12);
    box-shadow: 0 -8px 24px rgba(0,0,0,.28);
    backdrop-filter: blur(14px) saturate(140%);
    -webkit-backdrop-filter: blur(14px) saturate(140%);
    font: 500 11px/1.2 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
    letter-spacing: 0; pointer-events: none;
  }}
  .database-status-footer__counts {{
    display: flex; align-items: center; justify-content: center; gap: 8px;
    white-space: nowrap; width: 100%; min-width: 0; overflow: hidden;
  }}
  .database-status-footer__counts > * {{flex-shrink: 0}}
  .database-status-footer__counts > .database-status-footer__download,
  .database-status-footer__counts > .database-status-footer__task,
  .database-status-footer__counts > .database-status-footer__import {{flex-shrink:1;min-width:0;overflow:hidden;text-overflow:ellipsis}}
  .database-status-footer__activity .database-status-footer__download {{max-width:28vw}}
  .database-status-footer__activity .database-status-footer__task {{max-width:29vw}}
  @media(max-width:760px) {{.database-status-footer{{font-size:10px}}.database-status-footer__counts{{gap:5px}}}}
  .database-status-footer__value {{ color: #fff; font-variant-numeric: tabular-nums; }}
  .database-status-footer__separator {{ color: rgba(255,255,255,.28); }}
  .database-status-footer__task {{ color: rgba(255,255,255,.68); }}
  .database-status-footer__import {{color:#fff;text-decoration:none;max-width:26vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
  .database-status-footer__import:hover {{text-decoration:underline}}
  .database-status-footer__download {{color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
  .database-status-footer__queue {{color:#fff;background:#ffffff12;border:1px solid #ffffff30;border-radius:999px;padding:3px 9px;cursor:pointer;pointer-events:auto;white-space:nowrap;}}
  .download-queue-panel {{position:fixed;right:14px;bottom:56px;z-index:2147483003;min-width:260px;max-width:min(420px,92vw);max-height:50vh;overflow:auto;padding:12px 16px;border:1px solid #ffffff30;border-radius:12px;background:#1a1c1f;color:#fff;box-shadow:0 12px 32px #000a;font:13px/1.5 Inter,system-ui,sans-serif;pointer-events:auto;}}
  .download-queue-row {{display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:8px;border-top:1px solid #ffffff1c}}
  .download-queue-row span {{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}}
  .download-queue-row button {{border:1px solid #ffffff30;border-radius:6px;background:#ffffff13;color:#fff;padding:3px 6px;cursor:pointer}}
  .download-queue-row button:disabled {{opacity:.35;cursor:default}}
  .download-queue-panel[hidden] {{display:none;}}
  .database-status-footer__hidden {{ color:#ddd; text-decoration:none; pointer-events:auto; }}
  .database-status-footer__hidden:hover {{ text-decoration:underline; }}
  .external-request-light {{width:9px;height:9px;display:inline-block;border-radius:50%;background:#555b63;vertical-align:middle;margin-right:6px;}}
  .external-request-light.is-active {{background:#ee4a57;box-shadow:0 0 10px #ee4a57;animation:request-pulse 1s ease-in-out infinite alternate;}}
  @keyframes request-pulse {{to {{opacity:.35;transform:scale(.7);}}}}
  a.keyboard-tile-active {{outline:none!important;position:relative;z-index:30;transform:translateY(-9px) scale(1.09);filter:brightness(1.27);box-shadow:0 0 0 2px rgba(255,255,255,.45),0 20px 38px rgba(0,0,0,.65),0 0 25px rgba(255,255,255,.3);border-radius:10px;animation:tile-focus-bounce .34s ease-out both,tile-glow-pulse 2.8s ease-in-out .34s infinite;}}
  @keyframes tile-focus-bounce {{0%{{transform:translateY(0) scale(1)}}65%{{transform:translateY(-11px) scale(1.12)}}100%{{transform:translateY(-9px) scale(1.09)}}}}
  @keyframes tile-glow-pulse {{0%,100%{{box-shadow:0 0 0 2px rgba(255,255,255,.35),0 20px 38px rgba(0,0,0,.65),0 0 14px rgba(255,255,255,.2)}}50%{{box-shadow:0 0 0 3px rgba(255,255,255,.78),0 23px 42px rgba(0,0,0,.75),0 0 30px rgba(255,255,255,.48)}}}}
  @media (prefers-reduced-motion:reduce) {{a.keyboard-tile-active{{animation:none}}}}
  .favourite-tile:has(a.keyboard-tile-active),.group:has(a.keyboard-tile-active),.home-poster-size-item:has(a.keyboard-tile-active) {{position:relative;z-index:30;}}
  html,body {{overflow-x:hidden}}
  .recently-played-rail,[class*="ScrollRow-module"][class*="track"] {{scrollbar-width:none}}
  .recently-played-rail::-webkit-scrollbar,[class*="ScrollRow-module"][class*="track"]::-webkit-scrollbar {{display:none}}
</style>
<footer class="database-status-footer" aria-label="Local database status">
  <div class="database-status-footer__counts">
    <span><span class="database-status-footer__value">{counts.get('movie', 0):,}</span> Movies</span>
    <span class="database-status-footer__separator">•</span>
    <span><span class="database-status-footer__value">{counts.get('tv', 0):,}</span> TV</span>
    <span class="database-status-footer__separator">•</span>
    <span><span class="database-status-footer__value">{people:,}</span> People</span>
    <span class="database-status-footer__separator">•</span>
    <span class="database-status-footer__task" id="storage-status">DL: … · Avg: … · Media free: … · App free: …</span>
    <span class="database-status-footer__separator">•</span>
    <a class="database-status-footer__hidden" href="/hidden">Hidden ({len(hidden)})</a>
  </div>
  <div class="database-status-footer__counts database-status-footer__activity">
    <span class="database-status-footer__task" id="metadata-task-status">Metadata: starting…</span>
    <span class="database-status-footer__separator" id="import-status-separator" hidden>•</span>
    <a class="database-status-footer__import" id="import-status" href="/import-movies" aria-live="polite" hidden></a>
    <span class="database-status-footer__separator" id="download-status-separator" hidden>•</span>
    <span class="database-status-footer__download" id="download-status" role="status" aria-live="polite" hidden></span>
    <button class="database-status-footer__queue" id="download-queue-toggle" type="button" aria-expanded="false" aria-controls="download-queue-panel" hidden></button>
    <span class="database-status-footer__separator">•</span>
    <span class="database-status-footer__task" id="external-request-status" aria-live="off"><span class="external-request-light"></span>External: idle</span>
  </div>
</footer>
<div id="download-queue-panel" class="download-queue-panel" aria-label="Download queue" hidden></div>
<script id="detail-navigation-controls">
  (() => {{
    const label = document.getElementById('metadata-task-status');
    const storage = document.getElementById('storage-status');
    const formatBytes = bytes => bytes == null ? 'unavailable' :
      bytes >= 1073741824 ? `${{(bytes / 1073741824).toFixed(1)}} GB` :
      `${{(bytes / 1048576).toFixed(0)}} MB`;
    async function refreshStorage() {{
      try {{
        const response = await fetch('/api/storage-stats', {{cache:'no-store'}});
        if (!response.ok) return;
        const stats = await response.json();
        storage.textContent = `DL: ${{stats.downloaded_movies.toLocaleString()}} · ` +
          `Avg: ${{stats.average_movie_bytes == null ? '—' : formatBytes(stats.average_movie_bytes)}} · ` +
          `Media free: ${{formatBytes(stats.media_free_bytes)}} · App free: ${{formatBytes(stats.app_free_bytes)}}`;
      }} catch (_) {{ /* leave the last known snapshot visible */ }}
    }}
    refreshStorage();setInterval(refreshStorage, 20000);
    async function refreshMetadataStatus() {{
      try {{
        const response = await fetch('/api/metadata-task', {{cache:'no-store'}});
        if (!response.ok) return;
        const task = await response.json();
        label.textContent = task.state === 'episodes'
          ? `Episodes: ${{task.episode_checked}}/${{task.episode_total}} checked · ${{task.episode_filled}} filled`
          : task.state === 'checking'
          ? `Metadata: ${{task.checked}}/${{task.total}} checked · ${{task.filled}} complete · ${{task.failed}} failed`
          : task.state === 'waiting'
            ? `Metadata: waiting · ${{task.checked}} checked · ${{task.failed}} failed`
            : `Metadata: ${{task.state}}`;
        if (task.images_paused) label.textContent += ' · images paused (under 2 GB free)';
        label.title = task.current || task.last_error || 'Background metadata check';
      }} catch (_) {{ /* keep the previous status during a network interruption */ }}
    }}
    refreshMetadataStatus();
    setInterval(refreshMetadataStatus, 5000);
    const importStatus = document.getElementById('import-status');
    const importSeparator = document.getElementById('import-status-separator');
    async function refreshImport() {{
      try {{
        const response = await fetch('/api/profile/movie-import', {{cache:'no-store'}});
        if (!response.ok) return;
        const job = await response.json();
        const visible = job.state === 'running' || job.state === 'queued' || job.state === 'complete';
        importStatus.hidden = importSeparator.hidden = !visible;
        if (!visible) return;
        importStatus.textContent = `Movie import: ${{job.state === 'complete' ? 'complete' : job.state === 'queued' ? 'queued' : `${{job.checked}}/${{job.total}}`}} · ${{job.added}} added · ${{job.existing}} already present · ${{job.failed}} failed`;
        importStatus.title = job.current ? `Importing movie ID ${{job.current}}` : 'Open movie import details';
      }} catch (_) {{}}
    }}
    refreshImport();setInterval(refreshImport,3000);
    const external = document.getElementById('external-request-status');
    async function refreshExternal() {{
      try {{
        const response = await fetch('/api/external-request-status', {{cache:'no-store'}});
        if (!response.ok) return;
        const activity = await response.json();
        external.lastChild.textContent = activity.active ? `External: ${{activity.active}} request${{activity.active===1?'':'s'}}` : activity.recent ? 'External: just fetched' : 'External: idle';
        external.querySelector('.external-request-light').classList.toggle('is-active', activity.active > 0 || activity.recent);
        external.title = activity.sites.join(', ') || 'No active outgoing requests';
      }} catch (_) {{ /* leave current indicator during a network interruption */ }}
    }}
    refreshExternal();setInterval(refreshExternal, 1000);
    const downloads = document.getElementById('download-status');
    const downloadSeparator = document.getElementById('download-status-separator');
    const queueToggle = document.getElementById('download-queue-toggle');
    const queuePanel = document.getElementById('download-queue-panel');
    queueToggle.addEventListener('click', () => {{
      queuePanel.hidden = !queuePanel.hidden;
      queueToggle.setAttribute('aria-expanded', String(!queuePanel.hidden));
    }});
    document.addEventListener('keydown', event => {{
      if (event.key === 'Escape' && !queuePanel.hidden) {{ queuePanel.hidden = true; queueToggle.setAttribute('aria-expanded','false'); queueToggle.focus(); }}
    }});
    const duration = seconds => {{
      const value = Math.max(0, Math.floor(Number(seconds) || 0));
      return value >= 3600 ? `${{Math.floor(value / 3600)}}h ${{Math.floor(value % 3600 / 60)}}m`
        : value >= 60 ? `${{Math.floor(value / 60)}}m ${{value % 60}}s` : `${{value}}s`;
    }};
    queuePanel.addEventListener('click', async event => {{
      const control = event.target.closest('button[data-action]');
      if (!control) return;
      control.disabled = true;
      try {{
        const response = await fetch('/api/downloads/queue', {{
          method:'POST',headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{key:control.dataset.key,action:control.dataset.action}})
        }});
        const result = await response.json();
        if (!response.ok) throw Error(result.error || 'Could not change the queue.');
        refreshDownloads();
      }} catch (error) {{alert(error.message);control.disabled = false;}}
    }});
    async function refreshDownloads() {{
      try {{
        const response = await fetch('/api/downloads/status', {{cache:'no-store'}});
        if (!response.ok) return;
        const jobs = (await response.json()).jobs || [];
        const waiting = jobs.filter(item => item.state === 'queued');
        const running = jobs.find(item => item.state === 'running' && (item.progress ?? 0) < 100)
          || jobs.find(item => item.state === 'running');
        queueToggle.hidden = !waiting.length && !running;
        queueToggle.textContent = `Queue: ${{waiting.length}} waiting`;
        queueToggle.title = waiting.map(item => item.title + (item.kind === 'tv' ? ` S${{String(item.season).padStart(2,'0')}}E${{String(item.episode).padStart(2,'0')}}` : '')).join(' · ');
        queuePanel.replaceChildren();
        const heading = document.createElement('strong');heading.textContent = `Download queue (${{waiting.length}} waiting)`;
        queuePanel.append(heading);
        for (const item of jobs.filter(job => job.state === 'running' || job.state === 'queued')) {{
          const line = document.createElement('div');
          line.className = 'download-queue-row';
          const label = document.createElement('span');
          const episodeName = item.kind === 'tv' ? ` S${{String(item.season).padStart(2,'0')}}E${{String(item.episode).padStart(2,'0')}}` : '';
          label.textContent = item.state === 'running'
            ? `Downloading ${{item.title}}${{episodeName}} · ${{duration(item.elapsed_seconds)}} elapsed · ETA ${{item.eta_seconds == null ? 'calculating…' : duration(item.eta_seconds)}}`
            : `#${{item.queue_position}} waiting: ${{item.title}}${{episodeName}}`;
          line.append(label);
          if (item.state === 'running') {{
            const stop = document.createElement('button');stop.type='button';
            stop.dataset.action='stop';stop.dataset.key=item.key;
            stop.textContent='Stop & remove';
            stop.title='Stop and remove active download';
            stop.setAttribute('aria-label', `Stop and remove download: ${{item.title}}${{episodeName}}`);
            line.append(stop);
          }}
          if (item.state === 'queued') {{
            for (const [action,textLabel,disabled] of [['up','↑',item.queue_position === 1],['down','↓',item.queue_position === waiting.length],['remove','Remove',false]]) {{
              const control = document.createElement('button');control.type='button';
              control.dataset.action=action;control.dataset.key=item.key;
              control.textContent=textLabel;control.title=action === 'remove' ? 'Remove from queue' : 'Move ' + action;
              control.setAttribute('aria-label', `${{control.title}}: ${{item.title}}${{episodeName}}`);
              control.disabled=disabled;line.append(control);
            }}
          }}
          queuePanel.append(line);
        }}
        if (!waiting.length && !running) {{queuePanel.hidden = true;queueToggle.setAttribute('aria-expanded','false');}}
        const job = running || jobs.find(item => item.state !== 'queued');
        downloads.hidden = downloadSeparator.hidden = !job;
        if (!job) return;
        const title = job.title || (job.kind === 'tv' ? 'TV episode' : 'Movie');
        const episode = job.kind === 'tv' ? ` (S${{String(job.season).padStart(2,'0')}}E${{String(job.episode).padStart(2,'0')}})` : '';
        const percentage = typeof job.progress === 'number' ? `${{job.progress.toFixed(1)}}%` : 'preparing';
        downloads.textContent = job.state === 'running'
          ? `Downloading ${{percentage}}: ${{title}}${{episode}} · ${{duration(job.elapsed_seconds)}} elapsed · ETA ${{job.eta_seconds == null ? 'calculating…' : duration(job.eta_seconds)}}`
          : job.state === 'complete' ? `Downloaded: ${{title}}${{episode}}`
          : `Download failed: ${{title}}${{episode}}`;
        downloads.title = job.message || downloads.textContent;
      }} catch (_) {{ /* preserve the last known status */ }}
    }}
    refreshDownloads();setInterval(refreshDownloads, 1000);
  }})();
  (() => {{
    const match = location.pathname.match(new RegExp('^/(movie|tv)/([0-9]+)/?$'));
    if (match) {{
      const button = document.createElement('button');
      button.textContent = 'Delete';
      button.title = 'Remove this title, its episodes and cached artwork';
      button.className = 'media-delete-button';
      button.setAttribute('aria-label', 'Delete this ' + (match[1] === 'tv' ? 'show' : 'movie'));
      button.onclick = async () => {{
        if (!confirm('Permanently delete this title and its associated local files?')) return;
        button.disabled = true;
        try {{
          const res = await fetch('/api/media/' + match[1] + '/' + match[2] + '/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}});
          if (!res.ok) throw Error('Deletion failed (' + res.status + ')');
          location.assign('/');
        }} catch (error) {{ alert(error.message); button.disabled = false; }}
      }};
      const download = match[1] === 'movie' ? [...document.querySelectorAll('button')].find(candidate =>
        [...candidate.querySelectorAll('span')].some(span => span.textContent.trim() === 'Download')) : null;
      if (download) {{
        button.className += ' ' + download.className;
        download.parentElement.after(button);
      }} else {{
        button.classList.add('media-delete-floating');
        document.body.appendChild(button);
      }}
    }}
  }})();
  document.addEventListener('click', function (event) {{
    const home = event.target.closest('button[aria-label="Home"],a[aria-label="Home"]');
    if (home) {{
      event.preventDefault();
      event.stopImmediatePropagation();
      window.location.assign('/');
      return;
    }}
    const back = event.target.closest('button[aria-label="Go back"],a[aria-label="Go back"]');
    if (back) {{
      event.preventDefault();
      event.stopImmediatePropagation();
      if (window.history.length > 1) window.history.back();
      else window.location.assign('/');
    }}
  }}, true);
</script>
<script src="/static/keyboard_tiles.js" defer></script>
"""
    html = response.get_data(as_text=True)
    html = re.sub(
        r'<a\b(?=[^>]*\baria-label=["\']Movy["\'])[^>]*>.*?</a>',
        '', html, flags=re.IGNORECASE | re.DOTALL,
    )
    paths = [f"/{kind}/{media_id}" for kind, media_id in set(hidden) | blocked if media_id.isdigit()]
    # Hide captured, static cards before they paint. The script also removes
    # cards inserted later by client-side rendering.
    selectors = [f'.ScrollRow-module-scss-module__-kZXvG__track > *:has(a[href="{path}"]),' \
                 f'.movieGrid-module-scss-module__NJm1Na__movieGrid > *:has(a[href="{path}"]),' \
                 f'.media-card:has(a[href="{path}"]),.group:has(> .media-card a[href="{path}"]),' \
                 f'.genre-card[href="{path}"],' \
                 f'.HomeBanner-module-scss-module__eAYNsa__slideLayer:has(a[href="{path}"])'
                 for path in paths]
    hidden_assets = ('<style id="hidden-title-style">' + ','.join(selectors) +
                     '{display:none!important}</style>' if selectors else '')
    hidden_data = json.dumps(paths).replace('<', '\\u003c')
    parental_data = json.dumps(parental_settings(), ensure_ascii=False).replace('<', '\\u003c')
    current_media = None
    detail_match = re.fullmatch(r'/(movie|tv)/(\d+)/?', request.path)
    if detail_match and hasattr(g, "hero_title"):
        current_media = {"type": detail_match.group(1), "id": detail_match.group(2),
                         "title": g.hero_title, "url": f"/{detail_match.group(1)}/{detail_match.group(2)}"}
    current_data = json.dumps(current_media, ensure_ascii=False).replace('<', '\\u003c')
    display_name = session.get("name", "")
    menu_opacity = 90
    hero_transition = 'slide'
    hero_slide_timeout = 5
    if session.get("account_id"):
        menu_connection = accounts_connection()
        menu_row = menu_connection.execute(
            "SELECT name,menu_background_opacity,hero_transition,hero_slide_timeout FROM accounts WHERE account_id=?",
            (session["account_id"],)).fetchone()
        menu_connection.close()
        if menu_row:
            menu_opacity = menu_row["menu_background_opacity"]
            hero_transition = menu_row["hero_transition"]
            hero_slide_timeout = menu_row["hero_slide_timeout"]
            if not display_name:
                display_name = menu_row["name"]
                session["name"] = display_name
    if session.get("account_id") and not display_name:
        account_connection = accounts_connection()
        account_row = account_connection.execute(
            "SELECT name FROM accounts WHERE account_id=?", (session["account_id"],)).fetchone()
        account_connection.close()
        display_name = account_row["name"] if account_row else ""
        if display_name:
            session["name"] = display_name
    account_data = json.dumps({
        "name": display_name if session.get("account_id") else "",
        "csrf": session.setdefault("signout_csrf", secrets.token_urlsafe(32)) if session.get("account_id") else "",
    }, ensure_ascii=False).replace('<', '\\u003c')
    html = html.replace('</head>', '<link rel="stylesheet" href="/static/hidden-media.css">' + hidden_assets +
                        '<link rel="stylesheet" href="/static/downloaded-toggle.css"><script src="/static/downloaded-toggle.js" defer></script>' +
                        '<link rel="stylesheet" href="/static/title-download-menu.css"><script src="/static/title-download-menu.js" defer></script>' +
                        '<link rel="stylesheet" href="/static/profile-menu.css">' +
                        '<link rel="stylesheet" href="/static/download-queue-drawer.css"><script src="/static/download-queue-drawer.js" defer></script>' +
                        f'<style>:root{{--menu-opacity:{menu_opacity / 100:.2f}}}</style>' +
                        f'<script>window.__heroTransition={json.dumps(hero_transition)};window.__heroSlideTimeout={int(hero_slide_timeout)};window.__hiddenMediaPaths={hidden_data};window.__parentalSettings={parental_data};window.__currentMedia={current_data};window.__account={account_data};</script>' +
                        '<script src="/static/profile-menu.js" defer></script>' +
                        '<script src="/static/hidden-media.js" defer></script></head>', 1)
    html = re.sub(
        r'(<title\b[^>]*>)(.*?)(</title>)',
        lambda match: match.group(1) + re.sub(r'\s*\|\s*Movy\s*$', '', match.group(2), flags=re.IGNORECASE) + match.group(3),
        html, count=1, flags=re.IGNORECASE | re.DOTALL,
    )
    if request.path in ("/", "/movies", "/shows"):
        html = html.replace("</head>", '<link rel="stylesheet" href="/static/fullscreen-panels.css"><script src="/static/fullscreen-panels.js" defer></script></head>', 1)
    if request.path in ("/movies", "/shows"):
        html = html.replace("</head>", '<link rel="stylesheet" href="/static/home-hero.css">'
                            '<script src="/static/home-hero.js" defer></script></head>', 1)
        html = html.replace("</body>", '<link rel="stylesheet" href="/static/genre-filter.css">'
                            '<script src="/static/genre-filter.js" defer></script></body>', 1)
    if request.path == "/":
        html = html.replace("</head>", '<link rel="stylesheet" href="/static/home-posters.css"><script src="/static/home-posters.js" defer></script>'
                            '<link rel="stylesheet" href="/static/home-tile-size.css"><script src="/static/home-tile-size.js" defer></script>'
                            '<link rel="stylesheet" href="/static/recently-played.css"><script src="/static/recently-played.js" defer></script></head>', 1)
    html = re.sub(r'>(\s*)(?:TV|Shows)(\s*)<', r'>\1TV\2<', html)
    html = html.replace('</body>', '<script src="/static/rating-format.js" defer></script></body>', 1)
    if re.fullmatch(r"/movie/\d+/?", request.path):
        movie_id = request.path.strip('/').split('/')[-1]
        try:
            source_config = load_source_config()
            sources = source_config['sources']
            preferred = source_config.get('cloud_play_source', source_config['default_source'])
            if session.get('account_id'):
                db = accounts_connection()
                account = db.execute('SELECT movie_source FROM accounts WHERE account_id=?',
                                     (session['account_id'],)).fetchone()
                db.close()
                if account and account['movie_source'] in sources:
                    preferred = account['movie_source']
            movie_sources = [{
                'name': name,
                'page_url': build_source_url('movie', [movie_id], source=name)
                    if entry.get('movie_url_template') else None,
                'cloud_url': build_cloud_url('movie', [movie_id], source_name=name)
                    if entry.get('movie_cloud_url_template') or entry.get('movie_url_template') else None,
            } for name, entry in sources.items()]
            cloud_url = next((item['cloud_url'] for item in movie_sources if item['name'] == preferred), None)
        except ValueError as exc:
            app.logger.warning('Movie source configuration: %s', exc)
            movie_sources, preferred, cloud_url = [], '', None
        source_data = json.dumps({'sources': movie_sources, 'selected': preferred}, ensure_ascii=False).replace('<', '\\u003c')
        html = html.replace('</head>', f'<script>window.__movieSources={source_data};</script></head>', 1)
        html = html.replace("</head>", '<style id="movie-detail-initial-mask">body:not(.movie-detail-ready) .page-enter{visibility:hidden!important}</style>'
                            '<link rel="stylesheet" href="/static/movie-player.css">'
                            '<link rel="stylesheet" href="/static/movie-download.css">'
                            '<link rel="stylesheet" href="/static/movie-detail-panels.css">'
                            '<script src="/static/movie-player.js" defer></script>'
                            '<script src="/static/movie-download.js" defer></script>'
                            '<script src="/static/movie-detail-panels.js" defer></script></head>', 1)
    if re.fullmatch(r"/(movie|tv)/\d+/?", request.path):
        html = html.replace("</head>", '<link rel="stylesheet" href="/static/recommendation-covers.css"></head>', 1)
    if re.fullmatch(r"/tv/\d+/?", request.path):
        html = html.replace("</head>", '<script src="/static/show-play-history.js" defer></script></head>', 1)
    html = html.replace("</head>", '<link rel="stylesheet" href="/static/favourites.css"><script src="/static/favourites.js" defer></script></head>', 1)
    hero_backdrop = getattr(g, "hero_backdrop", "")
    hero_title = getattr(g, "hero_title", "")
    if hasattr(g, "hero_backdrop"):
        safe_source = html_module.escape(hero_backdrop, quote=True)
        safe_title = html_module.escape(hero_title, quote=True)
        replacement = (
            '<img class="HomeBanner-module-scss-module__eAYNsa__backdropImg" '
            f'src="{safe_source}" alt="{safe_title}" '
            'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;'
            'object-position:center;opacity:1;visibility:visible;display:block">'
        ) if safe_source else '<div aria-hidden="true"></div>'
        hero_picture = re.compile(
            r'<picture\b[^>]*>.*?<img\b[^>]*class="[^"]*'
            r'HomeBanner-module-scss-module__eAYNsa__backdropImg[^"]*"[^>]*>.*?</picture>',
            re.IGNORECASE | re.DOTALL,
        )
        html, replaced = hero_picture.subn(replacement, html, count=1)
        if not replaced:
            app.logger.warning("Hero backdrop picture was not found for %s", request.path)
        html, removed_video = re.subn(
            r'<div\b[^>]*class="[^"]*heroFade-module-scss-module__qCmkoa__mediaFill[^"]*"[^>]*>'
            r'.*?</div>',
            "", html, count=1, flags=re.IGNORECASE | re.DOTALL,
        )
        if not removed_video:
            app.logger.warning("Captured hero video layer was not found for %s", request.path)
        html = re.sub(
            r'<link\b(?=[^>]*\brel="preload")(?=[^>]*\bas="image")[^>]*>',
            "", html, flags=re.IGNORECASE,
        )
    response.set_data(html.replace("</body>", footer + "</body>", 1))
    response.content_length = len(response.get_data())
    if hasattr(g, "hero_backdrop"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def load_catalog():
    connection = connect()
    if DB_PATH.exists():
        records = media_for_app(connection)
        if records or connection.execute("SELECT 1 FROM media LIMIT 1").fetchone():
            connection.close()
            blocked = parental_blocked_titles()
            available = downloaded_titles() if downloaded_only() else None
            return [item for item in records if (item["type"], str(item["id"])) not in blocked
                    and (available is None or (item['type'], str(item['id'])) in available)]
    hidden = {(row[0], row[1]) for row in connection.execute("SELECT media_type,media_id FROM hidden_media")}
    connection.close()
    blocked = parental_blocked_titles()
    available = downloaded_titles() if downloaded_only() else None
    return [item for item in json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
            if (item.get("type"), str(item.get("url", "").rstrip("/").split("/")[-1])) not in hidden | blocked
            and (available is None or (item.get('type'), str(item.get('url', '').rstrip('/').split('/')[-1])) in available)]


def downloaded_only():
    return False  # The header's All titles/Downloaded mode was removed.


def parental_settings():
    if not has_request_context() or not session.get("account_id"):
        return {"genres": [], "classifications": []}
    if not hasattr(g, "parental_settings"):
        connection = accounts_connection()
        row = connection.execute("SELECT parental_controls FROM accounts WHERE account_id=?",
                                 (session["account_id"],)).fetchone()
        connection.close()
        try:
            data = json.loads(row[0] or "{}") if row else {}
        except (ValueError, TypeError):
            data = {}
        g.parental_settings = {key: data.get(key, []) if isinstance(data.get(key), list) else []
                               for key in ("genres", "classifications")}
    return g.parental_settings


def parental_blocked_titles():
    if not has_request_context() or not session.get("account_id"):
        return set()
    if not hasattr(g, "parental_blocked_titles"):
        settings = parental_settings()
        genres = [v.casefold() for v in settings["genres"] if isinstance(v, str)]
        classes = [v.casefold() for v in settings["classifications"] if isinstance(v, str)]
        connection = connect()
        blocked = set()
        if genres:
            placeholders = ",".join("?" for _ in genres)
            blocked.update((r[0], r[1]) for r in connection.execute(
                f"""SELECT DISTINCT mg.media_type,mg.media_id FROM media_genres mg
                    JOIN genres ge ON ge.genre_id=mg.genre_id
                    WHERE lower(ge.name) IN ({placeholders})""", genres))
        if classes:
            placeholders = ",".join("?" for _ in classes)
            blocked.update((r[0], r[1]) for r in connection.execute(
                f"SELECT media_type,media_id FROM media WHERE lower(trim(certification)) IN ({placeholders})", classes))
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parental_exclusions'").fetchone():
            blocked.update((r[0], r[1]) for r in connection.execute(
                "SELECT media_type,media_id FROM parental_exclusions WHERE account_id=?",
                (session["account_id"],)))
        connection.close()
        g.parental_blocked_titles = blocked
    return g.parental_blocked_titles


def is_hidden(media_type, media_id):
    if downloaded_only() and (media_type, str(media_id)) not in downloaded_titles():
        return True
    if (media_type, str(media_id)) in parental_blocked_titles():
        return True
    connection = connect()
    hidden = connection.execute(
        "SELECT 1 FROM hidden_media WHERE media_type=? AND media_id=?", (media_type, str(media_id))
    ).fetchone() is not None
    connection.close()
    return hidden


@app.get("/media-cache/<path:filename>")
def media_cache(filename):
    return send_from_directory(ROOT / "cache" / "images", filename)


def get_media(media_id, media_type):
    suffix = f"/{media_type}/{media_id}" if media_type == "movie" else f"/tv/{media_id}"
    details_path = ROOT / ("movie_details.json" if media_type == "movie" else "show_details.json")
    details = json.loads(details_path.read_text(encoding="utf-8")) if details_path.exists() else {}
    for item in load_catalog():
        if item["type"] == media_type and item.get("url", "").rstrip("/").endswith(suffix):
            return {**item, **details.get(str(media_id), {})}
    label = "TV" if media_type == "tv" else "Movie"
    return {
        "id": str(media_id),
        "title": f"{label} {media_id}",
        "type": media_type,
        "type_label": label,
        "year": "",
        "rating": "",
        "poster": "",
        "url": suffix,
    }


def cached_image(connection, owner_type, owner_key, image_type, preferred_source=""):
    row = connection.execute(
        """SELECT local_path,source_url FROM images
           WHERE owner_type=? AND owner_key=? AND image_type=?
           ORDER BY (source_url=?) DESC,image_id DESC LIMIT 1""",
        (owner_type, str(owner_key), image_type, preferred_source),
    ).fetchone()
    if not row:
        return ""
    return f"/media-cache/{row['local_path']}" if row["local_path"] else row["source_url"]


def display_rating(value):
    return f"{float(value):.1f}" if value not in (None, "") else ""


HERO_DEFAULTS = {
    key: {'min_rating': 7, 'max_rating': 10, 'year_from': None, 'year_to': None,
          'sort': 'newest', 'genres': []}
    for key in ('home', 'movies', 'tv')
}


def normalize_hero_rules(source):
    if not isinstance(source, dict) or set(source) - set(HERO_DEFAULTS):
        raise ValueError('Invalid hero page selection.')
    result = {}
    for page, defaults in HERO_DEFAULTS.items():
        config = source.get(page, {})
        if not isinstance(config, dict) or set(config) - set(defaults):
            raise ValueError(f'Invalid {page} hero settings.')
        rule = {**defaults, **config}
        for key in ('min_rating', 'max_rating'):
            if type(rule[key]) not in (int, float) or not 0 <= rule[key] <= 10:
                raise ValueError('Hero ratings must be between 0 and 10.')
        if rule['min_rating'] > rule['max_rating']:
            raise ValueError('Minimum hero rating cannot exceed maximum rating.')
        for key in ('year_from', 'year_to'):
            if rule[key] is not None and (type(rule[key]) is not int or not 1800 <= rule[key] <= 2200):
                raise ValueError('Hero years must be between 1800 and 2200.')
        if rule['year_from'] and rule['year_to'] and rule['year_from'] > rule['year_to']:
            raise ValueError('Oldest hero year cannot exceed newest year.')
        if rule['sort'] not in ('newest', 'oldest', 'rated'):
            raise ValueError('Invalid hero sort order.')
        if not isinstance(rule['genres'], list) or len(rule['genres']) > 30 or any(
                not isinstance(value, str) or not value.strip() or len(value) > 80 for value in rule['genres']):
            raise ValueError('Choose up to 30 valid hero genres.')
        rule['genres'] = list(dict.fromkeys(value.strip() for value in rule['genres']))
        result[page] = rule
    return result


def hero_rules_for_account():
    if not has_request_context() or not session.get('account_id'):
        return normalize_hero_rules({})
    if not hasattr(g, 'hero_rules'):
        connection = accounts_connection()
        row = connection.execute('SELECT hero_rules FROM accounts WHERE account_id=?',
                                 (session['account_id'],)).fetchone()
        connection.close()
        try:
            g.hero_rules = normalize_hero_rules(json.loads(row[0] or '{}') if row else {})
        except (ValueError, TypeError):
            g.hero_rules = normalize_hero_rules({})
    return g.hero_rules


def hero_titles(kind, page):
    """Apply account rules before choosing the configured number of slides."""
    rule = hero_rules_for_account()[page]
    slide_count = 5
    if has_request_context() and session.get('account_id'):
        settings_db = accounts_connection()
        try:
            setting = settings_db.execute('SELECT hero_slide_count FROM accounts WHERE account_id=?',
                                          (session['account_id'],)).fetchone()
            if setting:
                slide_count = max(5, min(20, int(setting[0])))
        finally:
            settings_db.close()
    clauses = ["m.media_type=?", "m.title IS NOT NULL", "TRIM(m.title)<>''",
               'm.rating BETWEEN ? AND ?',
               "EXISTS (SELECT 1 FROM images i WHERE i.owner_type=m.media_type AND i.owner_key=m.media_id AND i.image_type='backdrop')",
               "NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)"]
    values = [kind, rule['min_rating'], rule['max_rating']]
    if rule['year_from'] is not None:
        clauses.append('m.year>=?'); values.append(rule['year_from'])
    if rule['year_to'] is not None:
        clauses.append('m.year<=?'); values.append(rule['year_to'])
    if rule['genres']:
        placeholders = ','.join('?' for _ in rule['genres'])
        clauses.append(f'''EXISTS (SELECT 1 FROM media_genres mg JOIN genres ge ON ge.genre_id=mg.genre_id
            WHERE mg.media_type=m.media_type AND mg.media_id=m.media_id
            AND ge.name COLLATE NOCASE IN ({placeholders}))''')
        values.extend(rule['genres'])
    date_order = "COALESCE(NULLIF(m.release_date,''),printf('%04d',m.year))"
    order = {'newest': date_order + ' DESC,m.rating DESC,m.media_id DESC',
             'oldest': date_order + ' ASC,m.rating DESC,m.media_id ASC',
             'rated': 'm.rating DESC,' + date_order + ' DESC,m.media_id DESC'}[rule['sort']]
    connection = connect()
    rows = connection.execute('''SELECT m.media_id,m.title,m.description,m.year,m.release_date,
        m.rating,m.runtime_minutes,m.certification FROM media m WHERE ''' + ' AND '.join(clauses) +
        ' ORDER BY ' + order + ' LIMIT 500', values).fetchall()
    blocked = parental_blocked_titles()
    local = downloaded_titles() if downloaded_only() else None
    titles = []
    for row in rows:
        media_id = row['media_id']
        if (kind, media_id) in blocked or (local is not None and (kind, media_id) not in local):
            continue
        titles.append({
            'kind': kind, 'id': media_id, 'title': row['title'],
            'description': row['description'] or '', 'year': row['year'] or '',
            'rating': display_rating(row['rating']),
            'runtime': (f"{row['runtime_minutes'] // 60}h {row['runtime_minutes'] % 60}m"
                        if row['runtime_minutes'] and row['runtime_minutes'] >= 60 else
                        (f"{row['runtime_minutes']}m" if row['runtime_minutes'] else '')),
            'certification': row['certification'] or '',
            'backdrop': cached_image(connection, kind, media_id, 'backdrop'),
            'logo': cached_image(connection, kind, media_id, 'logo'),
            'genres': [item[0] for item in connection.execute('''SELECT ge.name FROM media_genres mg
                JOIN genres ge ON ge.genre_id=mg.genre_id WHERE mg.media_type=? AND mg.media_id=?
                ORDER BY ge.name''', (kind, media_id))],
        })
        if len(titles) == slide_count:
            break
    connection.close()
    return titles


def latest_hero_movies():
    return hero_titles('movie', 'home')


def listing_hero_titles(kind):
    return hero_titles(kind, 'tv' if kind == 'tv' else 'movies')


def movie_from_database(media_id):
    if is_hidden("movie", media_id):
        return None
    connection = connect()
    row = connection.execute(
        "SELECT * FROM media WHERE media_type='movie' AND media_id=?", (str(media_id),)
    ).fetchone()
    if not row:
        return None
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    preferred_poster = raw.get("poster") or ""
    preferred_backdrop = raw.get("background") or raw.get("backdrop") or ""
    preferred_logo = raw.get("logo") or ""
    genres = [item[0] for item in connection.execute(
        """SELECT g.name FROM genres g JOIN media_genres mg ON mg.genre_id=g.genre_id
           WHERE mg.media_type='movie' AND mg.media_id=? ORDER BY g.name""", (str(media_id),)
    )]
    cast = []
    for person in connection.execute(
        """SELECT p.person_key,p.name,c.character_name FROM credits c
           JOIN people p ON p.person_key=c.person_key
           WHERE c.media_type='movie' AND c.media_id=? ORDER BY c.credit_order LIMIT 30""",
        (str(media_id),),
    ):
        cast.append({
            "id": person["person_key"], "name": person["name"],
            "character": person["character_name"] or "",
            "image": cached_image(connection, "person", person["person_key"], "person"),
        })
    recommendations = []
    for related in connection.execute(
        """SELECT r.related_type,r.related_id,m.title,m.year,m.rating
           FROM recommendations r LEFT JOIN media m
             ON m.media_type=r.related_type AND m.media_id=r.related_id
           WHERE r.media_type='movie' AND r.media_id=?
             AND NOT EXISTS (SELECT 1 FROM hidden_media h
                 WHERE h.media_type=r.related_type AND h.media_id=r.related_id)
           ORDER BY r.position LIMIT 24""",
        (str(media_id),),
    ):
        if is_hidden(related["related_type"], related["related_id"]):
            continue
        recommendations.append({
            "id": related["related_id"], "type": related["related_type"],
            "type_label": "TV" if related["related_type"] == "tv" else "Movie",
            "title": related["title"] or f"{related['related_type'].title()} {related['related_id']}",
            "year": related["year"] or "", "rating": display_rating(related["rating"]),
            "poster": cached_image(connection, related["related_type"], related["related_id"], "poster"),
            "url": f"/{related['related_type']}/{related['related_id']}",
        })
    runtime = row["runtime_minutes"]
    runtime_text = f"{runtime // 60}h {runtime % 60}m" if runtime and runtime >= 60 else (f"{runtime}m" if runtime else "")
    return {
        "id": str(media_id), "type": "movie", "type_label": "Movie",
        "title": row["title"], "description": row["description"] or "",
        "year": row["year"] or "", "rating": display_rating(row["rating"]),
        "release_date": row["release_date"] or "", "runtime_minutes": runtime or "",
        "runtime": runtime_text, "certification": row["certification"] or "",
        "genres": genres, "poster": cached_image(connection, "movie", media_id, "poster", preferred_poster),
        "backdrop": cached_image(connection, "movie", media_id, "backdrop", preferred_backdrop),
        "logo": cached_image(connection, "movie", media_id, "logo", preferred_logo),
        "cast": cast, "recommendations": recommendations,
        "url": f"/movie/{media_id}",
    }


def ensure_movie_data(media_id):
    if is_hidden("movie", media_id):
        abort(404)
    media_id = str(media_id)
    if not media_id.isdigit():
        abort(404)
    connection = connect()
    row = connection.execute(
        "SELECT title,description,year,rating,raw_json FROM media WHERE media_type='movie' AND media_id=?",
        (media_id,),
    ).fetchone()
    backdrop_count = connection.execute(
        """SELECT COUNT(*) FROM images WHERE owner_type='movie' AND owner_key=?
           AND image_type='backdrop'""", (media_id,)
    ).fetchone()[0]
    try:
        raw = json.loads(row["raw_json"] or "{}") if row else {}
    except (TypeError, ValueError):
        raw = {}
    placeholder = not row or row["title"] == f"Movie {media_id}" or not row["description"]
    if placeholder or not backdrop_count or not (raw.get("background") or raw.get("backdrop")):
        sync = MovySync(0, True, 30, media_type="movie")
        url = urljoin(BASE_URL, f"/movie/{media_id}")
        try:
            sync.enqueue(url, "on-demand movie page")
            sync.parse_detail(url, sync.fetch(url))
            sync.db.execute(
                "UPDATE crawl_queue SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?",
                (url,),
            )
            sync.db.commit()
        except Exception as exc:
            app.logger.warning("Could not refresh movie %s: %s", media_id, exc)
            sync.db.rollback()

    if not movie_from_database(media_id) or not movie_from_database(media_id).get("description"):
        abort(404)

    sync = MovySync(0, True, 30, media_type="movie")
    pending = sync.db.execute(
        """SELECT DISTINCT i.* FROM images i
           LEFT JOIN credits c ON i.owner_type='person' AND i.owner_key=c.person_key
           WHERE i.local_path IS NULL AND (
             (i.owner_type='movie' AND i.owner_key=?) OR
             (c.media_type='movie' AND c.media_id=?))
           ORDER BY i.image_id""",
        (media_id, media_id),
    ).fetchall()
    for image in pending:
        try:
            sync.cache_image(image)
            sync.db.commit()
        except Exception as exc:
            app.logger.warning("Could not cache image for movie %s: %s", media_id, exc)
    export_current_ids()
    return movie_from_database(media_id)


def show_from_database(media_id):
    if is_hidden("tv", media_id):
        return None
    connection = connect()
    row = connection.execute(
        "SELECT * FROM media WHERE media_type='tv' AND media_id=?", (str(media_id),)
    ).fetchone()
    if not row:
        return None
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    preferred_poster = raw.get("poster") or ""
    preferred_backdrop = raw.get("background") or raw.get("backdrop") or ""
    preferred_logo = raw.get("logo") or ""
    genres = [item[0] for item in connection.execute(
        """SELECT g.name FROM genres g JOIN media_genres mg ON mg.genre_id=g.genre_id
           WHERE mg.media_type='tv' AND mg.media_id=? ORDER BY g.name""", (str(media_id),)
    )]
    cast = []
    for person in connection.execute(
        """SELECT p.person_key,p.name,c.character_name FROM credits c
           JOIN people p ON p.person_key=c.person_key
           WHERE c.media_type='tv' AND c.media_id=? ORDER BY c.credit_order LIMIT 30""",
        (str(media_id),),
    ):
        cast.append({
            "id": person["person_key"], "name": person["name"],
            "character": person["character_name"] or "",
            "image": cached_image(connection, "person", person["person_key"], "person"),
        })
    seasons = []
    for season in connection.execute(
        "SELECT * FROM seasons WHERE show_id=? ORDER BY season_number", (str(media_id),)
    ):
        episodes = []
        for episode in connection.execute(
            """SELECT * FROM episodes WHERE show_id=? AND season_number=?
               ORDER BY episode_number""", (str(media_id), season["season_number"])
        ):
            if downloaded_only() and not episode_available(row['title'], str(media_id), row['year'],
                                                           episode['season_number'], episode['episode_number']):
                continue
            key = f"{media_id}:{episode['season_number']}:{episode['episode_number']}"
            episodes.append({
                "number": episode["episode_number"], "title": episode["title"] or f"Episode {episode['episode_number']}",
                "description": (episode["description"] or "") if
                    (episode["description"] or "").strip() != (row["description"] or "").strip() else "",
                "runtime_minutes": episode["runtime_minutes"] or "",
                "air_date": episode["air_date"] or "",
                "runtime": f"{episode['runtime_minutes']}m" if episode["runtime_minutes"] else "",
                "rating": display_rating(episode["rating"]), "image": cached_image(connection, "episode", key, "episode"),
                "url": f"/tv/{media_id}/{episode['season_number']}/{episode['episode_number']}?play=true",
            })
        if episodes:
            seasons.append({
                "number": season["season_number"], "title": season["title"] or f"Season {season['season_number']}",
                "episode_count": len(episodes), "episodes": episodes,
            })
    episode_total = sum(len(season["episodes"]) for season in seasons)
    recommendations = []
    for related in connection.execute(
        """SELECT r.related_type,r.related_id,m.title,m.year,m.rating
           FROM recommendations r LEFT JOIN media m
             ON m.media_type=r.related_type AND m.media_id=r.related_id
           WHERE r.media_type='tv' AND r.media_id=?
             AND NOT EXISTS (SELECT 1 FROM hidden_media h
                 WHERE h.media_type=r.related_type AND h.media_id=r.related_id)
           ORDER BY r.position LIMIT 24""",
        (str(media_id),),
    ):
        if is_hidden(related["related_type"], related["related_id"]):
            continue
        recommendations.append({
            "id": related["related_id"], "type": related["related_type"],
            "type_label": "TV" if related["related_type"] == "tv" else "Movie",
            "title": related["title"] or f"{related['related_type'].title()} {related['related_id']}",
            "year": related["year"] or "", "rating": display_rating(related["rating"]),
            "poster": cached_image(connection, related["related_type"], related["related_id"], "poster"),
            "url": f"/{related['related_type']}/{related['related_id']}",
        })
    return {
        "id": str(media_id), "type": "tv", "type_label": "TV",
        "title": row["title"], "description": row["description"] or "",
        "year": row["year"] or "", "rating": display_rating(row["rating"]),
        "release_date": row["release_date"] or "", "certification": row["certification"] or "",
        "seasons_count": len(seasons), "episode_total": episode_total, "genres": genres,
        "poster": cached_image(connection, "tv", media_id, "poster", preferred_poster),
        "backdrop": cached_image(connection, "tv", media_id, "backdrop", preferred_backdrop),
        "logo": cached_image(connection, "tv", media_id, "logo", preferred_logo),
        "seasons": seasons, "cast": cast, "recommendations": recommendations,
        "url": f"/tv/{media_id}",
    }


def ensure_show_data(media_id):
    if is_hidden("tv", media_id):
        abort(404)
    media_id = str(media_id)
    if not media_id.isdigit():
        abort(404)
    connection = connect()
    row = connection.execute(
        "SELECT title,description,raw_json FROM media WHERE media_type='tv' AND media_id=?", (media_id,)
    ).fetchone()
    backdrop_count = connection.execute(
        """SELECT COUNT(*) FROM images WHERE owner_type='tv' AND owner_key=?
           AND image_type='backdrop'""", (media_id,)
    ).fetchone()[0]
    try:
        raw = json.loads(row["raw_json"] or "{}") if row else {}
    except (TypeError, ValueError):
        raw = {}
    placeholder = not row or row["title"] == f"Tv {media_id}" or not row["description"]
    if placeholder or not backdrop_count or not (raw.get("background") or raw.get("backdrop")):
        sync = MovySync(0, True, 30, media_type="tv")
        url = urljoin(BASE_URL, f"/tv/{media_id}")
        try:
            sync.enqueue(url, "on-demand TV page")
            sync.parse_detail(url, sync.fetch(url))
            sync.db.execute(
                "UPDATE crawl_queue SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,)
            )
            sync.db.commit()
        except Exception as exc:
            app.logger.warning("Could not refresh TV show %s: %s", media_id, exc)
            sync.db.rollback()
    if not show_from_database(media_id) or not show_from_database(media_id).get("description"):
        abort(404)
    sync = MovySync(0, True, 30, media_type="tv")
    pending = sync.db.execute(
        """SELECT DISTINCT i.* FROM images i
           LEFT JOIN credits c ON i.owner_type='person' AND i.owner_key=c.person_key
           WHERE i.local_path IS NULL AND (
             (i.owner_type='tv' AND i.owner_key=?) OR
             (c.media_type='tv' AND c.media_id=?)) ORDER BY i.image_id""",
        (media_id, media_id),
    ).fetchall()
    for image in pending:
        try:
            sync.cache_image(image)
            sync.db.commit()
        except Exception as exc:
            app.logger.warning("Could not cache image for TV show %s: %s", media_id, exc)
    export_current_ids()
    return show_from_database(media_id)


def export_current_ids():
    connection = connect()
    for name, media_type in (("movie-ids.txt", "movie"), ("show-ids.txt", "tv")):
        ids = [row[0] for row in connection.execute(
            "SELECT media_id FROM media WHERE media_type=? ORDER BY CAST(media_id AS INTEGER)", (media_type,)
        )]
        (ROOT / name).write_text("\n".join(ids) + "\n", encoding="utf-8")
    connection.close()


@app.post("/api/media/<media_type>/<media_id>/delete")
def delete_title(media_type, media_id):
    if not request.is_json or request.headers.get("Origin") != request.host_url.rstrip("/"):
        abort(403)
    try:
        return jsonify(delete_media(media_type, media_id))
    except (ValueError, LookupError):
        abort(404)


@app.get("/")
def home():
    payload = {
        "search_url": "/api/search",
        "detail_routes": {"movie": "/movie/{id}", "tv": "/tv/{id}"},
    }
    return render_template(
        "home.html",
        site={
            "title": "MediaFlix",
            "movies_title": "Movies",
            "shows_title": "TV",
        },
        labels={"search": "Search", "search_placeholder": "Search movies, shows, and anime"},
        home_data_json=json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
        hero_movies=latest_hero_movies(),
    )


@app.get("/movies")
def movies():
    payload = {
        "search_url": "/api/search",
        "detail_routes": {"movie": "/movie/{id}", "tv": "/tv/{id}"},
    }
    return render_template(
        "movies.html",
        site={
            "title": "Movy - Watch Free Movies & TV Online",
            "movies_title": "Movies",
            "shows_title": "TV",
        },
        labels={"search": "Search", "search_placeholder": "Search movies, shows, and anime"},
        home_data_json=json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
        hero_titles=listing_hero_titles('movie'), listing_label='movies',
    )


@app.get("/api/home-posters")
def home_posters():
    return jsonify(results=[{"type": item["type"], "id": str(item["id"]),
                             "poster": item.get("poster") or ""} for item in load_catalog()])


@app.post('/api/recently-played')
def recently_played():
    requested = (request.get_json(silent=True) or {}).get('titles', [])
    if not isinstance(requested, list) or len(requested) > 60:
        abort(400)
    db = connect()
    blocked = parental_blocked_titles()
    result = []
    seen = set()
    for item in requested:
        if not isinstance(item, dict):
            continue
        kind, media_id = item.get('type'), str(item.get('id', ''))
        key = (kind, media_id)
        if kind not in ('movie', 'tv') or not media_id.isdigit() or key in seen or key in blocked or (downloaded_only() and key not in downloaded_titles()):
            continue
        seen.add(key)
        row = db.execute('''SELECT m.title,m.year,m.rating FROM media m WHERE m.media_type=? AND m.media_id=?
            AND NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)''', key).fetchone()
        if row:
            result.append({'type': kind, 'id': media_id, 'title': row['title'], 'year': row['year'],
                           'rating': display_rating(row['rating']), 'poster': cached_image(db, kind, media_id, 'poster'),
                           'url': f'/{kind}/{media_id}'})
    db.close()
    return jsonify(results=result)


@app.get('/api/external-request-status')
def external_request_status():
    return jsonify(request_pacing.activity())


@app.get("/api/shows/by-genre")
def shows_by_genre():
    genre = request.args.get("genre", "").strip()
    if len(genre) > 80:
        abort(400)
    connection = connect()
    rows = connection.execute("""SELECT DISTINCT m.media_id,m.title,m.year FROM media m
        JOIN media_genres mg ON mg.media_type=m.media_type AND mg.media_id=m.media_id
        JOIN genres g ON g.genre_id=mg.genre_id
        WHERE m.media_type='tv' AND g.name COLLATE NOCASE=?
          AND NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
        ORDER BY m.year DESC,m.title COLLATE NOCASE""", (genre,)).fetchall()
    blocked = parental_blocked_titles()
    result = [{"id": row["media_id"], "title": row["title"], "year": row["year"],
               "poster": cached_image(connection, "tv", row["media_id"], "poster"),
               "url": f"/tv/{row['media_id']}"} for row in rows if ("tv", row["media_id"]) not in blocked
               and (not downloaded_only() or ('tv', row['media_id']) in downloaded_titles())]
    connection.close()
    return jsonify(results=result)


@app.get("/api/movie-genres")
@app.get("/api/tv-genres")
def movie_genres():
    kind = 'tv' if request.path.endswith('/tv-genres') else 'movie'
    connection = connect()
    genres = [row[0] for row in connection.execute(
        """SELECT DISTINCT g.name FROM genres g JOIN media_genres mg ON mg.genre_id=g.genre_id
           WHERE mg.media_type=? AND NOT EXISTS (
             SELECT 1 FROM hidden_media h WHERE h.media_type=mg.media_type AND h.media_id=mg.media_id)
           ORDER BY g.name COLLATE NOCASE"""
    , (kind,))]
    connection.close()
    blocked = {name.casefold() for name in parental_settings()["genres"]}
    return jsonify(genres=[name for name in genres if name.casefold() not in blocked])


@app.get("/api/movie-countries")
@app.get("/api/tv-countries")
def movie_countries():
    kind = 'tv' if request.path.endswith('/tv-countries') else 'movie'
    connection = connect()
    countries = [row[0] for row in connection.execute(
        """SELECT DISTINCT c.country FROM media_countries c
           JOIN media m ON m.media_type=c.media_type AND m.media_id=c.media_id
           WHERE c.media_type=? AND NOT EXISTS (
             SELECT 1 FROM hidden_media h WHERE h.media_type=c.media_type AND h.media_id=c.media_id)
           ORDER BY c.country COLLATE NOCASE"""
    , (kind,))]
    connection.close()
    return jsonify(countries=countries)


@app.get("/api/movie-certifications")
@app.get("/api/tv-certifications")
def movie_certifications():
    kind = 'tv' if request.path.endswith('/tv-certifications') else 'movie'
    connection = connect()
    values = [row[0] for row in connection.execute(
        """SELECT DISTINCT TRIM(m.certification) FROM media m
           WHERE m.media_type=? AND m.certification IS NOT NULL
             AND TRIM(m.certification) <> '' AND NOT EXISTS (
               SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
           ORDER BY m.certification COLLATE NOCASE"""
    , (kind,))]
    connection.close()
    blocked = {name.casefold() for name in parental_settings()["classifications"]}
    return jsonify(certifications=[value for value in values if value.casefold() not in blocked])


@app.get("/api/metadata-task")
def metadata_task():
    return jsonify(metadata_worker_status())


@app.get("/api/movies/year-range")
@app.get("/api/tv/year-range")
def movie_year_range():
    kind = 'tv' if request.path.startswith('/api/tv/') else 'movie'
    connection = connect()
    row = connection.execute(
        """SELECT MIN(year), MAX(year) FROM media m WHERE media_type=?
           AND year BETWEEN 1800 AND 2200 AND NOT EXISTS (
             SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)""", (kind,)
    ).fetchone()
    connection.close()
    return jsonify(oldest=row[0], newest=row[1])


@app.get("/api/movies/by-genre")
@app.get("/api/tv/by-genre")
def movies_by_genre():
    kind = 'tv' if request.path.startswith('/api/tv/') else 'movie'
    source = request.args.get('source', 'all')
    if source not in ('all', 'downloaded', 'not_downloaded'):
        abort(400)
    selected = [value.strip() for value in request.args.getlist("genre") if value.strip()][:30]
    selected_countries = [value.strip() for value in request.args.getlist("country") if value.strip()][:30]
    selected_certifications = [value.strip() for value in request.args.getlist("certification") if value.strip()][:30]
    sort = request.args.get("sort", "popular")
    if sort not in ({"popular", "rated", "newest", "oldest", "latest_episode"} if kind == 'tv'
                    else {"popular", "rated", "newest", "oldest"}):
        abort(400)
    try:
        year_from = int(request.args["year_from"]) if "year_from" in request.args else None
        year_to = int(request.args["year_to"]) if "year_to" in request.args else None
        rating_min = float(request.args["rating_min"]) if "rating_min" in request.args else None
        rating_max = float(request.args["rating_max"]) if "rating_max" in request.args else None
    except ValueError:
        abort(400)
    if (year_from is not None and not 1800 <= year_from <= 2200) or (
        year_to is not None and not 1800 <= year_to <= 2200
    ) or (year_from is not None and year_to is not None and year_from > year_to):
        abort(400)
    if (rating_min is not None and not 0 <= rating_min <= 10) or (
        rating_max is not None and not 0 <= rating_max <= 10
    ) or (rating_min is not None and rating_max is not None and rating_min > rating_max):
        abort(400)
    connection = connect()
    where = ""
    parameters = []
    if selected:
        placeholders = ",".join("?" for _ in selected)
        where = f""" AND EXISTS (SELECT 1 FROM media_genres mg
            JOIN genres g ON g.genre_id=mg.genre_id
            WHERE mg.media_type=m.media_type AND mg.media_id=m.media_id
            AND g.name COLLATE NOCASE IN ({placeholders}))"""
        parameters.extend(selected)
    if selected_countries:
        placeholders = ",".join("?" for _ in selected_countries)
        where += f""" AND EXISTS (SELECT 1 FROM media_countries c
            WHERE c.media_type=m.media_type AND c.media_id=m.media_id
            AND c.country COLLATE NOCASE IN ({placeholders}))"""
        parameters.extend(selected_countries)
    if selected_certifications:
        placeholders = ",".join("?" for _ in selected_certifications)
        where += f" AND m.certification COLLATE NOCASE IN ({placeholders})"
        parameters.extend(selected_certifications)
    if year_from is not None:
        where += " AND m.year >= ?"
        parameters.append(year_from)
    if year_to is not None:
        where += " AND m.year <= ?"
        parameters.append(year_to)
    if rating_min is not None:
        where += " AND m.rating >= ?"
        parameters.append(rating_min)
    if rating_max is not None:
        where += " AND m.rating <= ?"
        parameters.append(rating_max)
    blocked_movies = [media_id for media_kind, media_id in parental_blocked_titles() if media_kind == kind]
    if blocked_movies:
        where += f" AND m.media_id NOT IN ({','.join('?' for _ in blocked_movies)})"
        parameters.extend(blocked_movies)
    rows = connection.execute(
        f"""SELECT m.media_id,m.title,m.year,m.release_date,m.rating,i.local_path,i.source_url
            FROM media m
            LEFT JOIN images i ON i.image_id=(SELECT image_id FROM images
                WHERE owner_type=m.media_type AND owner_key=m.media_id AND image_type='poster'
                ORDER BY (local_path IS NOT NULL) DESC,image_id DESC LIMIT 1)
            WHERE m.media_type=? AND NOT EXISTS (
                SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id
            ) {where}""", [kind, *parameters],
    ).fetchall()
    latest_episode_dates = {}
    if kind == 'tv' and sort == 'latest_episode':
        from datetime import date
        today = date.today().isoformat()
        for episode in connection.execute('SELECT show_id,air_date,raw_json FROM episodes'):
            air_date = episode['air_date']
            if not air_date and episode['raw_json']:
                try:
                    source = json.loads(episode['raw_json'])
                    air_date = next((str(source.get(key))[:10] for key in
                                     ('air_date', 'release_date', 'datePublished', 'date')
                                     if source.get(key)), None)
                except (ValueError, TypeError):
                    pass
            if air_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(air_date)[:10]):
                air_date = str(air_date)[:10]
                if air_date <= today and air_date > latest_episode_dates.get(episode['show_id'], ''):
                    latest_episode_dates[episode['show_id']] = air_date
    connection.close()
    # The source's captured movie grid supplies the popularity order. Titles
    # discovered later follow it, sorted by rating and release date.
    template = (ROOT / "templates" / ("shows.html" if kind == "tv" else "movies.html")).read_text(encoding="utf-8")
    grid_start = template.find("windowedMovieGrid-module-scss-module__S9vvea__stack")
    popular_ids = dict.fromkeys(re.findall(r'<a[^>]*href="/' + kind + r'/(\d+)"', template[grid_start:]))
    rank = {media_id: index for index, media_id in enumerate(popular_ids)}
    def date_key(row):
        return row["release_date"] or f"{row['year'] or 0:04d}"
    if sort == "popular":
        rows.sort(key=lambda row: (rank.get(row["media_id"], len(rank)),
                                   -(row["rating"] or 0), date_key(row), row["title"].casefold()))
    elif sort == "rated":
        rows.sort(key=lambda row: (-(row["rating"] or 0), -int(row["year"] or 0), row["title"].casefold()))
    elif sort == 'latest_episode':
        rows.sort(key=lambda row: (latest_episode_dates.get(row['media_id'], ''),
                                   date_key(row), row['rating'] or 0), reverse=True)
    elif sort == "newest":
        rows.sort(key=lambda row: (date_key(row), row["title"].casefold()), reverse=True)
    else:
        rows.sort(key=lambda row: (date_key(row), row["title"].casefold()))
    if source != 'all' or downloaded_only():
        visible_downloads = downloaded_titles()
        rows = [row for row in rows if
                ((kind, row['media_id']) in visible_downloads) == (source != 'not_downloaded')
                and (not downloaded_only() or (kind, row['media_id']) in visible_downloads)]
    return jsonify(results=[{
        "id": row["media_id"], "title": row["title"], "year": row["year"],
        "rating": display_rating(row["rating"]), "poster":
            f"/media-cache/{row['local_path']}" if row["local_path"] else row["source_url"] or "",
        "url": f"/{kind}/{row['media_id']}",
        **({'latest_episode': latest_episode_dates.get(row['media_id'])} if sort == 'latest_episode' else {}),
    } for row in rows], total=len(rows))


@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip().casefold()
    media_type = request.args.get("type", "all")
    if len(query) < 1:
        return jsonify(results=[])

    matches = []
    for item in load_catalog():
        if media_type != "all" and item["type"] != media_type:
            continue
        if query in item["title"].casefold():
            matches.append(item)
        if len(matches) == 48:
            break
    return jsonify(results=matches)


@app.get("/api/catalog/ids")
def catalog_ids():
    connection = connect()
    movie_ids = [row[0] for row in connection.execute("""SELECT media_id FROM media m WHERE media_type='movie'
        AND NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
        ORDER BY CAST(media_id AS INTEGER)""")]
    show_ids = [row[0] for row in connection.execute("""SELECT media_id FROM media m WHERE media_type='tv'
        AND NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
        ORDER BY CAST(media_id AS INTEGER)""")]
    if downloaded_only():
        available = downloaded_titles()
        movie_ids = [media_id for media_id in movie_ids if ('movie', media_id) in available]
        show_ids = [media_id for media_id in show_ids if ('tv', media_id) in available]
    return jsonify(movie_ids=movie_ids, show_ids=show_ids)


@app.get('/api/storage-stats')
def get_storage_stats():
    return jsonify(storage_stats())


@app.get("/api/catalog/stats")
def catalog_stats():
    connection = connect()
    counts = {
        row[0]: row[1]
        for row in connection.execute("""SELECT media_type,COUNT(*) FROM media m
            WHERE NOT EXISTS (SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
            GROUP BY media_type""")
    }
    return jsonify(
        movies=counts.get("movie", 0),
        shows=counts.get("tv", 0),
        people=connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
        episodes=connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
        images=connection.execute("SELECT COUNT(*) FROM images").fetchone()[0],
        cached_images=connection.execute("SELECT COUNT(*) FROM images WHERE local_path IS NOT NULL").fetchone()[0],
        queued=connection.execute("SELECT COUNT(*) FROM crawl_queue WHERE status='pending'").fetchone()[0],
        errors=connection.execute("SELECT COUNT(*) FROM crawl_queue WHERE status='error'").fetchone()[0],
    )


def validate_hidden_request(media_type, media_id):
    if media_type not in {"movie", "tv"} or not media_id.isdigit():
        abort(404)
    if not request.is_json or request.headers.get("Origin") != request.host_url.rstrip("/"):
        abort(403)


@app.post("/api/media/<media_type>/<media_id>/hide")
def hide_title(media_type, media_id):
    validate_hidden_request(media_type, media_id)
    connection = connect()
    row = connection.execute("SELECT title FROM media WHERE media_type=? AND media_id=?", (media_type, media_id)).fetchone()
    body = request.get_json(silent=True) or {}
    fallback = body.get("title", "") if isinstance(body, dict) else ""
    title = row[0] if row else (str(fallback).strip()[:200] or f"{media_type.title()} {media_id}")
    connection.execute("""INSERT INTO hidden_media(media_type,media_id,title) VALUES(?,?,?)
        ON CONFLICT(media_type,media_id) DO UPDATE SET title=excluded.title""", (media_type, media_id, title))
    connection.commit()
    connection.close()
    return jsonify(type=media_type, id=media_id, title=title, hidden=True)


@app.post("/api/media/<media_type>/<media_id>/unhide")
def unhide_title(media_type, media_id):
    validate_hidden_request(media_type, media_id)
    connection = connect()
    connection.execute("DELETE FROM hidden_media WHERE media_type=? AND media_id=?", (media_type, media_id))
    connection.commit()
    connection.close()
    return jsonify(type=media_type, id=media_id, hidden=False)


@app.get("/api/hidden")
def hidden_titles_api():
    connection = connect()
    titles = [dict(row) for row in connection.execute(
        "SELECT media_type,media_id,title,hidden_at FROM hidden_media ORDER BY hidden_at DESC,title COLLATE NOCASE"
    )]
    connection.close()
    return jsonify(results=titles)


@app.get("/hidden")
def hidden_titles_page():
    connection = connect()
    titles = [dict(row) for row in connection.execute(
        "SELECT media_type,media_id,title,hidden_at FROM hidden_media ORDER BY hidden_at DESC,title COLLATE NOCASE"
    )]
    connection.close()
    return render_template("hidden.html", titles=titles)


@app.get("/history")
@app.get("/watch-list")
@app.get("/watch-party")
@app.get("/favourites")
def profile_page():
    pages = {
        "/history": ("History", "history", "Recently opened movies and shows in this browser."),
        "/watch-list": ("Watch List", "watch-list", "Titles saved with Add to List in this browser."),
        "/watch-party": ("Watch Party", None, "Watch Party is not connected to a server yet."),
        "/favourites": ("Favourites", None, "Titles you've marked with a heart."),
    }
    title, list_kind, description = pages[request.path]
    return render_template("profile-page.html", title=title, list_kind=list_kind,
                           description=description)


def accounts_connection():
    connection = connect()
    connection.execute("""CREATE TABLE IF NOT EXISTS accounts (
        account_id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
    if "name" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        connection.execute("UPDATE accounts SET name=username WHERE name='' ")
        connection.commit()
    if "movie_filters" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN movie_filters TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    if "tv_filters" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN tv_filters TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    if 'home_tile_size' not in columns:
        connection.execute('ALTER TABLE accounts ADD COLUMN home_tile_size INTEGER NOT NULL DEFAULT 200')
        connection.commit()
    if 'menu_background_opacity' not in columns:
        connection.execute('ALTER TABLE accounts ADD COLUMN menu_background_opacity INTEGER NOT NULL DEFAULT 90')
        connection.commit()
    if 'hero_transition' not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN hero_transition TEXT NOT NULL DEFAULT 'slide'")
        connection.commit()
    if 'hero_slide_count' not in columns:
        connection.execute('ALTER TABLE accounts ADD COLUMN hero_slide_count INTEGER NOT NULL DEFAULT 5')
        connection.commit()
    if 'hero_slide_timeout' not in columns:
        connection.execute('ALTER TABLE accounts ADD COLUMN hero_slide_timeout INTEGER NOT NULL DEFAULT 5')
        connection.commit()
    if 'hero_rules' not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN hero_rules TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    if "parental_controls" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN parental_controls TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    if "movie_source" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN movie_source TEXT NOT NULL DEFAULT ''")
        connection.commit()
    connection.execute("""CREATE TABLE IF NOT EXISTS favourites (
        account_id INTEGER NOT NULL, media_type TEXT NOT NULL, media_id TEXT NOT NULL,
        saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(account_id,media_type,media_id),
        FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE CASCADE)""")
    return connection


def require_account_write():
    if not session.get("account_id") or not secrets.compare_digest(
        request.headers.get("X-CSRF-Token", ""), session.get("signout_csrf", "")):
        abort(403)


@app.get("/parental-controls")
def parental_controls_page():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return render_template("parental-controls.html")


@app.get("/similar-scan")
def similar_scan_page():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return render_template("similar-scan.html", scan_csrf=session.setdefault("signout_csrf", secrets.token_urlsafe(32)))


@app.get('/import-movies')
def import_movies_page():
    if not session.get('account_id'):
        return redirect(url_for('login'))
    return render_template('import-movies.html', import_csrf=session.setdefault('signout_csrf', secrets.token_urlsafe(32)))


@app.route('/api/profile/movie-import', methods=['GET', 'POST'])
def movie_import_api():
    if not session.get('account_id'):
        abort(401)
    if request.method == 'GET':
        return jsonify(movie_import.status(session['account_id']))
    require_account_write()
    try:
        if 'file' in request.files:
            ids = read_movie_ids_xlsx(request.files['file'])
        elif request.is_json:
            ids = (request.get_json() or {}).get('ids')
        else:
            abort(400)
        return jsonify(movie_import.enqueue(session['account_id'], ids))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post('/api/profile/import-movies/<media_id>')
def import_movie_id(media_id):
    require_account_write()
    if not media_id.isdigit() or len(media_id) > 15 or not request.is_json:
        abort(400)
    if is_hidden('movie', media_id):
        return jsonify(error='This movie is hidden.'), 409
    db = connect()
    try:
        row = db.execute("SELECT title FROM media WHERE media_type='movie' AND media_id=?", (media_id,)).fetchone()
    finally:
        db.close()
    if row:
        return jsonify(status='existing', title=row['title'], id=media_id)
    try:
        with request_pacing.interactive_scope():
            movie_record = ensure_movie_data(media_id)
    except Exception as exc:
        app.logger.warning('Could not import movie %s: %s', media_id, exc)
        return jsonify(error='Movie could not be fetched. Check the ID or retry later.'), 422
    return jsonify(status='added', title=movie_record['title'], id=media_id)


@app.route("/api/profile/similar-scan", methods=["GET", "POST"])
def similar_scan_api():
    if not session.get("account_id"):
        abort(401)
    if request.method == "POST":
        require_account_write()
        try:
            return jsonify(similar_scan.start(session["account_id"]))
        except (ValueError, sqlite3.Error) as exc:
            return jsonify(error=str(exc)), 400
    return jsonify(similar_scan.status(session["account_id"]))


@app.get('/settings')
def settings_page():
    if not session.get('account_id'):
        return redirect(url_for('login'))
    return render_template('settings.html', settings_csrf=session.setdefault('signout_csrf', secrets.token_urlsafe(32)))


@app.route('/api/profile/settings', methods=['GET', 'PUT'])
def profile_settings():
    if not session.get('account_id'):
        return jsonify(error='Your session expired. Sign in again, then reload Settings.'), 401
    if request.method == 'PUT':
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''),
                                      session.get('signout_csrf', '')):
            return jsonify(error='Settings security token expired. Reload Settings and try again.'), 403
        data = request.get_json(silent=True) or {}
        try:
            if not isinstance(data, dict) or not any(key in data for key in
                                                    ('delay_seconds', 'interactive_delay_seconds', 'home_tile_size', 'download_threads', 'hero_rules', 'menu_background_opacity', 'hero_transition', 'hero_slide_count', 'hero_slide_timeout')):
                raise ValueError('Select a setting to update.')
            if 'hero_rules' in data:
                rules = normalize_hero_rules(data['hero_rules'])
                connection = accounts_connection()
                connection.execute('UPDATE accounts SET hero_rules=? WHERE account_id=?',
                                   (json.dumps(rules), session['account_id']))
                connection.commit()
                connection.close()
                g.hero_rules = rules
            if 'home_tile_size' in data:
                size = data['home_tile_size']
                if type(size) is not int or size < 120 or size > 320 or size % 10:
                    raise ValueError('Choose a home tile width from 120 to 320 pixels in steps of 10.')
                connection = accounts_connection()
                connection.execute('UPDATE accounts SET home_tile_size=? WHERE account_id=?',
                                   (size, session['account_id']))
                connection.commit()
                connection.close()
            if 'menu_background_opacity' in data:
                opacity = data['menu_background_opacity']
                if type(opacity) is not int or not 0 <= opacity <= 100:
                    raise ValueError('Choose a menu background opacity between 0 and 100%.')
                connection = accounts_connection()
                connection.execute('UPDATE accounts SET menu_background_opacity=? WHERE account_id=?',
                                   (opacity, session['account_id']))
                connection.commit()
                connection.close()
            if 'hero_transition' in data:
                transition = data['hero_transition']
                if transition not in ('slide', 'fade', 'dissolve'):
                    raise ValueError('Choose Slide, Fade, or Dissolve.')
                connection = accounts_connection()
                connection.execute('UPDATE accounts SET hero_transition=? WHERE account_id=?',
                                   (transition, session['account_id']))
                connection.commit()
                connection.close()
            if 'hero_slide_count' in data or 'hero_slide_timeout' in data:
                count = data.get('hero_slide_count')
                timeout = data.get('hero_slide_timeout')
                if count is not None and (type(count) is not int or not 5 <= count <= 20):
                    raise ValueError('Choose between 5 and 20 hero slides.')
                if timeout is not None and (type(timeout) is not int or not 2 <= timeout <= 30):
                    raise ValueError('Choose a slide display time between 2 and 30 seconds.')
                connection = accounts_connection()
                if count is not None:
                    connection.execute('UPDATE accounts SET hero_slide_count=? WHERE account_id=?',
                                       (count, session['account_id']))
                if timeout is not None:
                    connection.execute('UPDATE accounts SET hero_slide_timeout=? WHERE account_id=?',
                                       (timeout, session['account_id']))
                connection.commit()
                connection.close()
            if 'delay_seconds' in data or 'interactive_delay_seconds' in data:
                request_pacing.set_delays(background=data.get('delay_seconds'), interactive=data.get('interactive_delay_seconds'))
            if 'download_threads' in data:
                movie_download.set_threads(data['download_threads'])
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    connection = accounts_connection()
    row = connection.execute('SELECT home_tile_size,hero_rules,menu_background_opacity,hero_transition,hero_slide_count,hero_slide_timeout FROM accounts WHERE account_id=?',
                             (session['account_id'],)).fetchone()
    connection.close()
    try:
        hero_rules = normalize_hero_rules(json.loads(row['hero_rules'] or '{}')) if row else normalize_hero_rules({})
    except (ValueError, TypeError, KeyError):
        hero_rules = normalize_hero_rules({})
    return jsonify(**request_pacing.settings(), home_tile_size=row[0] if row else 200,
                   menu_background_opacity=row['menu_background_opacity'] if row else 90,
                   hero_transition=row['hero_transition'] if row else 'slide',
                   hero_slide_count=row['hero_slide_count'] if row else 5,
                   hero_slide_timeout=row['hero_slide_timeout'] if row else 5,
                   hero_rules=hero_rules,
                   download_threads=movie_download.threads_setting(),
                   choices=request_pacing.ALLOWED_DELAYS,
                   interactive_choices=request_pacing.INTERACTIVE_DELAYS)


@app.route("/api/profile/parental-controls", methods=["GET", "PUT"])
def profile_parental_controls():
    if not session.get("account_id"):
        abort(401)
    connection = accounts_connection()
    if request.method == "PUT":
        require_account_write()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400)
        allowed = {"genres": "SELECT name FROM genres", "classifications":
                   "SELECT DISTINCT TRIM(certification) FROM media WHERE certification IS NOT NULL AND TRIM(certification)<>''"}
        settings = {}
        for key, query in allowed.items():
            values = data.get(key, [])
            if not isinstance(values, list) or len(values) > 100:
                abort(400)
            names = {row[0].casefold(): row[0] for row in connection.execute(query)}
            settings[key] = list(dict.fromkeys(names[v.casefold()] for v in values
                if isinstance(v, str) and v.casefold() in names))
        connection.execute("UPDATE accounts SET parental_controls=? WHERE account_id=?",
                           (json.dumps(settings), session["account_id"]))
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parental_exclusions'").fetchone():
            connection.execute("DELETE FROM parental_exclusions WHERE account_id=?", (session["account_id"],))
        connection.commit()
        g.parental_settings = settings
        if hasattr(g, "parental_blocked_titles"):
            del g.parental_blocked_titles
    else:
        settings = parental_settings()
    choices = {
        "genres": [r[0] for r in connection.execute("SELECT name FROM genres ORDER BY name COLLATE NOCASE")],
        "classifications": [r[0] for r in connection.execute("""SELECT DISTINCT TRIM(certification) FROM media
            WHERE certification IS NOT NULL AND TRIM(certification)<>'' ORDER BY 1 COLLATE NOCASE""")],
    }
    connection.close()
    return jsonify(settings=settings, choices=choices)


@app.route("/api/profile/movie-filters", methods=["GET", "PUT"])
@app.route("/api/profile/tv-filters", methods=["GET", "PUT"])
def profile_movie_filters():
    column = 'tv_filters' if request.path.endswith('/tv-filters') else 'movie_filters'
    if not session.get("account_id"):
        return jsonify(filters={}) if request.method == "GET" else abort(401)
    connection = accounts_connection()
    if request.method == "PUT":
        require_account_write()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400)
        allowed = {"genre", "country", "certification", "sort", "year_from", "year_to", "rating_min", "rating_max", "source"}
        filters = {key: value for key, value in data.items() if key in allowed}
        if len(json.dumps(filters)) > 3000:
            abort(400)
        connection.execute(f"UPDATE accounts SET {column}=? WHERE account_id=?",
                           (json.dumps(filters), session["account_id"]))
        connection.commit()
    else:
        row = connection.execute(f"SELECT {column} FROM accounts WHERE account_id=?",
                                 (session["account_id"],)).fetchone()
        filters = json.loads(row[0] or "{}") if row else {}
    connection.close()
    return jsonify(filters=filters)


@app.route("/api/profile/favourites", methods=["GET", "POST", "DELETE"])
def profile_favourites():
    if not session.get("account_id"):
        abort(401)
    connection = accounts_connection()
    if request.method == "GET":
        rows = connection.execute("""SELECT f.media_type,f.media_id,m.title,m.year FROM favourites f
            JOIN media m ON m.media_type=f.media_type AND m.media_id=f.media_id
            WHERE f.account_id=? AND NOT EXISTS (SELECT 1 FROM hidden_media h
                WHERE h.media_type=f.media_type AND h.media_id=f.media_id)
            ORDER BY f.saved_at DESC, f.rowid DESC""", (session["account_id"],)).fetchall()
        result = [{"type": row["media_type"], "id": row["media_id"],
                   "title": row["title"], "year": row["year"],
                   "url": f"/{row['media_type']}/{row['media_id']}",
                   "poster": cached_image(connection, row["media_type"], row["media_id"], "poster")}
                  for row in rows if (row["media_type"], row["media_id"]) not in parental_blocked_titles()
                  and (not downloaded_only() or (row['media_type'], row['media_id']) in downloaded_titles())]
        connection.close()
        return jsonify(results=result)
    require_account_write()
    data = request.get_json(silent=True) or {}
    kind, media_id = data.get("type"), str(data.get("id", ""))
    if kind not in ("movie", "tv") or not media_id.isdigit():
        abort(400)
    if request.method == "POST":
        exists = connection.execute("SELECT 1 FROM media WHERE media_type=? AND media_id=?", (kind, media_id)).fetchone()
        if not exists or is_hidden(kind, media_id):
            abort(404)
        connection.execute("INSERT OR IGNORE INTO favourites(account_id,media_type,media_id) VALUES(?,?,?)",
                           (session["account_id"], kind, media_id))
    else:
        connection.execute("DELETE FROM favourites WHERE account_id=? AND media_type=? AND media_id=?",
                           (session["account_id"], kind, media_id))
    connection.commit()
    connection.close()
    return jsonify(saved=request.method == "POST")


@app.route("/sign-up", methods=["GET", "POST"])
def sign_up():
    error = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if request.form.get("csrf") != session.get("signup_csrf"):
            abort(400)
        if not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
            error = "Enter a name of up to 80 characters."
        elif not re.fullmatch(r"[a-z0-9._\-]+@[a-z0-9.\-]+\.[a-z]{2,}", email) or len(email) > 254:
            error = "Enter a valid email address."
        elif not 8 <= len(password) <= 128:
            error = "Password must be 8–128 characters."
        else:
            connection = accounts_connection()
            try:
                cursor = connection.execute(
                    "INSERT INTO accounts(username,email,password_hash,name) VALUES(?,?,?,?)",
                    (email, email, generate_password_hash(password), name))
                connection.commit()
                session.clear()
                session["account_id"] = cursor.lastrowid
                session["name"] = name
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                error = "That email is already registered."
            finally:
                connection.close()
    token = secrets.token_urlsafe(32)
    session["signup_csrf"] = token
    return render_template("sign-up.html", error=error, csrf=token), (400 if error else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("csrf") != session.get("signup_csrf"):
            abort(400)
        connection = accounts_connection()
        account = connection.execute("SELECT * FROM accounts WHERE email=?",
                                     (request.form.get("email", "").strip().lower(),)).fetchone()
        connection.close()
        if account and check_password_hash(account["password_hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = request.form.get("keep_signed_in") == "1"
            session["account_id"] = account["account_id"]
            session["name"] = account["name"]
            return redirect(url_for("home"))
        error = "Invalid email or password."
    token = secrets.token_urlsafe(32)
    session["signup_csrf"] = token
    return render_template("sign-up.html", error=error, csrf=token, login_mode=True,
                           keep_signed_in=request.form.get("keep_signed_in") == "1"), (400 if error else 200)


@app.post("/sign-out")
def sign_out():
    if not session.get("account_id") or not secrets.compare_digest(
        request.form.get("csrf", ""), session.get("signout_csrf", "")):
        abort(403)
    session.clear()
    return redirect(url_for("home"))


# These placeholders are ready for the movie and TV templates you provide later.
@app.get("/movie/<media_id>")
def movie(media_id):
    if is_hidden("movie", media_id):
        abort(404)
    with request_pacing.interactive_scope():
        movie_record = ensure_movie_data(media_id)
    g.hero_backdrop = movie_record.get("backdrop") or movie_record.get("poster") or ""
    g.hero_title = movie_record.get("title") or ""
    g.hero_kind = "movie"
    payload = {
        **movie_record,
        "api_url": f"/api/movies/{media_id}",
    }
    return render_template(
        "movie.html",
        movie=movie_record,
        site={
            "title": "Movy - Watch Free Movies & TV Online",
            "movies_title": "Movies",
            "shows_title": "TV",
        },
        labels={"search": "Search", "search_placeholder": "Search movies, shows, and anime"},
        home_data_json=json.dumps({"search_url": "/api/search"}, ensure_ascii=False).replace("</", "<\\/"),
        movie_data_json=json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
    )


@app.get("/api/movies/<media_id>")
def movie_api(media_id):
    if is_hidden("movie", media_id):
        abort(404)
    with request_pacing.interactive_scope():
        return jsonify(ensure_movie_data(media_id))


@app.post("/api/movies/<media_id>/download")
def start_movie_download(media_id):
    if not media_id.isdigit() or is_hidden('movie', media_id) or ('movie', media_id) in parental_blocked_titles():
        abort(404)
    # A JSON request from this page prevents a cross-site form from starting a
    # long-running server process on a LAN-accessible installation.
    if not request.is_json or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403)
    db = connect()
    try:
        movie_row = db.execute("SELECT title FROM media WHERE media_type='movie' AND media_id=?", (media_id,)).fetchone()
    finally:
        db.close()
    if not movie_row:
        abort(404)
    source_name = selected_movie_source()
    started, job = movie_download.start(media_id, movie_row['title'], source=source_name)
    return jsonify({"started": started, **job}), (503 if job['state'] == 'error' else 200)


@app.get("/api/movies/<media_id>/download")
def movie_download_status(media_id):
    if not media_id.isdigit() or is_hidden('movie', media_id):
        abort(404)
    db = connect()
    try:
        row = db.execute("SELECT title,year FROM media WHERE media_type='movie' AND media_id=?", (media_id,)).fetchone()
    finally:
        db.close()
    if not row:
        abort(404)
    video = movie_download.movie_file(media_id, row['title'], row['year'])
    try:
        details = movie_download.cached_media_info(media_id, video) if video else None
    except OSError:
        details = None
    return jsonify(**movie_download.status(media_id),
                   file_complete=video is not None, media_info=details)


def local_movie(media_id):
    if not media_id.isdigit() or is_hidden('movie', media_id) or ('movie', media_id) in parental_blocked_titles():
        abort(404)
    db = connect()
    try:
        row = db.execute("SELECT title,year FROM media WHERE media_type='movie' AND media_id=?", (media_id,)).fetchone()
    finally:
        db.close()
    if not row:
        abort(404)
    video = movie_download.movie_file(media_id, row['title'], row['year'])
    if video is None:
        abort(404)
    return video


def selected_movie_source():
    config = load_source_config()
    preferred = config.get('cloud_play_source', config['default_source'])
    if session.get('account_id'):
        db = accounts_connection()
        try:
            row = db.execute('SELECT movie_source FROM accounts WHERE account_id=?',
                             (session['account_id'],)).fetchone()
        finally:
            db.close()
        if row and row['movie_source'] in config['sources']:
            preferred = row['movie_source']
    return preferred


@app.route('/api/profile/movie-source', methods=['GET', 'POST'])
def set_movie_source():
    if not session.get('account_id'):
        return jsonify(error='Sign in to choose a playback source.'), 401
    try:
        config = load_source_config()
    except ValueError as exc:
        return jsonify(error=str(exc)), 500
    if request.method == 'GET':
        sources = [
            {'name': name,
             'movie_url_template': entry['movie_url_template'],
             'player_url_template': entry.get('movie_cloud_url_template') or entry['movie_url_template']}
            for name, entry in config['sources'].items() if entry.get('movie_url_template')
        ]
        return jsonify(source=selected_movie_source(), sources=sources)
    if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''),
                                  session.get('signout_csrf', '')):
        return jsonify(error='Settings security token expired. Reload Settings and try again.'), 403
    data = request.get_json(silent=True) or {}
    name = data.get('source')
    if not isinstance(name, str) or name not in config['sources'] or not config['sources'][name].get('movie_url_template'):
        return jsonify(error='Choose a configured movie source.'), 400
    db = accounts_connection()
    try:
        db.execute('UPDATE accounts SET movie_source=? WHERE account_id=?', (name, session['account_id']))
        db.commit()
    finally:
        db.close()
    return jsonify(source=name)


@app.get('/api/movies/<media_id>/playback')
def movie_playback(media_id):
    video = local_movie(media_id)
    return jsonify(local=True, video=f'/api/movies/{media_id}/video',
                   subtitles=f'/api/movies/{media_id}/subtitles' if movie_download.movie_subtitle(video) else None)


@app.get('/api/movies/<media_id>/video')
def movie_video(media_id):
    return send_file(local_movie(media_id), mimetype='video/mp4', conditional=True)


@app.get('/api/movies/<media_id>/subtitles')
def movie_subtitles(media_id):
    subtitle = movie_download.movie_subtitle(local_movie(media_id))
    if subtitle is None:
        abort(404)
    content = subtitle.read_text(encoding='utf-8-sig', errors='replace')
    # Browsers use WebVTT tracks; SRT timestamps differ only in the separator.
    import re as _re
    content = _re.sub(r'(?<=\d),(?=\d{3}\b)', '.', content)
    return Response('WEBVTT\n\n' + content, mimetype='text/vtt')


@app.get('/api/downloads/status')
def downloads_status():
    jobs = movie_download.active_status()
    if jobs and request.args.get('details') == '1':
        db = connect()
        try:
            for job in jobs:
                kind, media_id = job.get('kind'), str(job.get('id') or '')
                if kind not in ('movie', 'tv') or not media_id.isdigit():
                    continue
                row = db.execute('SELECT title,year,rating FROM media WHERE media_type=? AND media_id=?',
                                 (kind, media_id)).fetchone()
                if row:
                    job['title'] = row['title'] or job.get('title')
                    job['year'] = row['year']
                    job['rating'] = display_rating(row['rating'])
                    job['poster'] = cached_image(db, kind, media_id, 'poster')
                    job['genres'] = [entry[0] for entry in db.execute('''
                        SELECT ge.name FROM media_genres mg JOIN genres ge ON ge.genre_id=mg.genre_id
                        WHERE mg.media_type=? AND mg.media_id=? ORDER BY ge.name''', (kind, media_id))]
                    if kind == 'movie' and job.get('state') == 'complete':
                        try:
                            video = movie_download.movie_file(media_id, row['title'], row['year'])
                            if video:
                                job['total_bytes'] = video.stat().st_size
                        except (OSError, ValueError):
                            pass
                if job.get('total_bytes') and isinstance(job.get('progress'), (int, float)):
                    job['downloaded_bytes'] = int(job['total_bytes'] * min(100, max(0, job['progress'])) / 100)
        finally:
            db.close()
    return jsonify({'jobs': jobs, 'paused': movie_download.queue_paused()})


@app.post('/api/downloads/pause')
def pause_download_queue():
    if not request.is_json or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403)
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        abort(403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('paused')) is not bool:
        return jsonify(error='Choose whether to pause the download queue.'), 400
    return jsonify(paused=movie_download.set_queue_paused(data['paused']))


@app.post('/api/downloads/queue')
def edit_download_queue():
    if not request.is_json or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403)
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        abort(403)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not isinstance(data.get('key'), str):
        abort(400)
    try:
        return jsonify(movie_download.edit_queue(data['key'], data.get('action')))
    except ValueError as exc:
        return jsonify(error=str(exc)), 409


@app.post('/api/tv/<media_id>/<int:season>/<int:episode>/download')
def start_tv_episode_download(media_id, season, episode):
    if not media_id.isdigit() or season < 0 or episode < 1 or is_hidden('tv', media_id):
        abort(404)
    if not request.is_json or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403)
    db = connect()
    try:
        item = db.execute('''SELECT m.title AS show_title FROM episodes e
            JOIN media m ON m.media_type='tv' AND m.media_id=e.show_id
            WHERE e.show_id=? AND e.season_number=? AND e.episode_number=?''',
            (media_id, season, episode)).fetchone()
    finally:
        db.close()
    if not item:
        abort(404)
    started, job = movie_download.start_episode(media_id, season, episode, item['show_title'])
    return jsonify({'started': started, **job}), (503 if job['state'] == 'error' else 200)


@app.get('/api/tv/<media_id>/<int:season>/<int:episode>/download')
def tv_episode_download_status(media_id, season, episode):
    if not media_id.isdigit() or is_hidden('tv', media_id):
        abort(404)
    return jsonify(movie_download.episode_status(media_id, season, episode))


@app.get("/tv/<media_id>")
def tv(media_id):
    if is_hidden("tv", media_id):
        abort(404)
    with request_pacing.interactive_scope():
        show = ensure_show_data(media_id)
    g.hero_backdrop = show.get("backdrop") or show.get("poster") or ""
    g.hero_title = show.get("title") or ""
    g.hero_kind = "tv"
    payload = {
        **show,
        "api_url": f"/api/shows/{media_id}",
    }
    return render_template(
        "show.html",
        show=show,
        site={
            "title": "Movy - Watch Free Movies & TV Online",
            "movies_title": "Movies",
            "shows_title": "TV",
        },
        labels={"search": "Search", "search_placeholder": "Search movies, shows, and anime"},
        home_data_json=json.dumps({"search_url": "/api/search"}, ensure_ascii=False).replace("</", "<\\/"),
        show_data_json=json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
    )


@app.get("/api/shows/<media_id>")
def show_api(media_id):
    if is_hidden("tv", media_id):
        abort(404)
    with request_pacing.interactive_scope():
        return jsonify(ensure_show_data(media_id))


@app.post("/api/shows/<media_id>/rescan")
def show_rescan(media_id):
    if not media_id.isdigit() or is_hidden("tv", media_id):
        abort(404)
    db = connect()
    exists = db.execute("SELECT 1 FROM media WHERE media_type='tv' AND media_id=?", (media_id,)).fetchone()
    db.close()
    if not exists:
        abort(404)
    started = rescan_show(media_id)
    return jsonify({"started": started, **rescan_status(media_id)})


@app.get("/api/shows/<media_id>/rescan")
def show_rescan_status(media_id):
    if not media_id.isdigit() or is_hidden("tv", media_id):
        abort(404)
    return jsonify(rescan_status(media_id))


@app.get("/person/<path:person_key>")
def person(person_key):
    connection = connect()
    person_row = connection.execute(
        "SELECT person_key,name FROM people WHERE person_key=?", (person_key,)
    ).fetchone()
    if person_row is None:
        connection.close()
        return "Person not found", 404

    titles = []
    for row in connection.execute(
        """SELECT m.media_type,m.media_id,m.title,m.year,m.rating,
                  GROUP_CONCAT(DISTINCT NULLIF(c.character_name,'')) AS characters
           FROM credits c JOIN media m
             ON m.media_type=c.media_type AND m.media_id=c.media_id
           WHERE c.person_key=? AND NOT EXISTS (
             SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id)
           GROUP BY m.media_type,m.media_id,m.title,m.year,m.rating
           ORDER BY COALESCE(m.year,0) DESC,m.title COLLATE NOCASE""",
        (person_key,),
    ):
        if is_hidden(row['media_type'], row['media_id']):
            continue
        titles.append({
            "type": row["media_type"],
            "type_label": "TV" if row["media_type"] == "tv" else "Movie",
            "id": row["media_id"], "title": row["title"],
            "year": row["year"] or "", "rating": display_rating(row["rating"]),
            "characters": row["characters"] or "",
            "poster": cached_image(connection, row["media_type"], row["media_id"], "poster"),
            "url": f"/{'tv' if row['media_type'] == 'tv' else 'movie'}/{row['media_id']}",
        })
    person_image = cached_image(connection, "person", person_key, "person")
    connection.close()
    return render_template(
        "person.html",
        person={"id": person_row["person_key"], "name": person_row["name"], "image": person_image},
        titles=titles,
    )


@app.get("/shows")
def shows():
    payload = {
        "search_url": "/api/search",
        "detail_routes": {"movie": "/movie/{id}", "tv": "/tv/{id}"},
    }
    return render_template(
        "shows.html",
        site={
            "title": "Movy - Watch Free Movies & TV Online",
            "movies_title": "Movies",
            "shows_title": "TV",
        },
        labels={"search": "Search", "search_placeholder": "Search movies, shows, and anime"},
        home_data_json=json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
        hero_titles=listing_hero_titles('tv'), listing_label='TV shows',
    )


@app.get("/anime")
def anime():
    return "Anime listing template pending", 501


if __name__ == "__main__":
    import signal
    from werkzeug.serving import make_server

    def stop_on_term(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_term)
    server = make_server("0.0.0.0", 8080, app, threaded=True)
    movie_download.start_worker()
    print("MediaFlix listening on port 8080. Press Ctrl+C to stop gracefully.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping: finishing active downloads and metadata work; queued downloads will resume on restart.", flush=True)
    finally:
        server.server_close()
        movie_download.request_stop()
        movie_import.request_stop()
        metadata_worker_stop()
        similar_scan.request_stop()
        with _request_condition:
            _request_condition.wait_for(lambda: _active_requests == 0)
        movie_import.wait_stop()
        metadata_worker_wait()
        similar_scan.wait_stop()
        movie_download.wait_stop()
        print("Stopped safely.", flush=True)
