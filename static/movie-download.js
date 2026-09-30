(() => {
  const match = location.pathname.match(/^\/movie\/(\d+)\/?$/);
  if (!match) return;
  const button = [...document.querySelectorAll('button')].find(node =>
    [...node.querySelectorAll('span')].some(span => span.textContent.trim() === 'Download'));
  if (!button) return;
  const label = [...button.querySelectorAll('span')].find(span => span.textContent.trim() === 'Download');
  const endpoint = `/api/movies/${match[1]}/download`;
  const classification = document.querySelector('.ageRatingBadge-module-scss-module__MpHirG__badge');
  const badge = classification?.parentElement;
  const availability = document.createElement('span');
  availability.className = 'movie-file-indicator';
  availability.setAttribute('role','img');
  const setAvailability = complete => {
    availability.dataset.complete = String(complete);
    availability.setAttribute('aria-label', complete ? 'Movie downloaded on this server' : 'Movie not downloaded');
    availability.title = complete ? 'Downloaded on this server' : 'Available to download';
    availability.innerHTML = complete
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18h11a4 4 0 0 0 .2-8A6 6 0 0 0 6.4 9 4.5 4.5 0 0 0 7 18Z"/><path d="M12 11v5m-2-2 2 2 2-2"/></svg>';
  };
  setAvailability(false);
  const mediaDetails = document.createElement('span');
  mediaDetails.className = 'movie-media-details';
  mediaDetails.hidden = true;
  if (badge) badge.append(availability, mediaDetails);
  button.setAttribute('aria-label', 'Download movie to server');
  const panel = document.createElement('div');
  panel.className = 'movie-download-status';
  panel.hidden = true;
  panel.innerHTML = '<div class="movie-download-heading" role="status" aria-live="polite"></div>' +
    '<div class="movie-download-progress" role="progressbar" aria-label="Movie download progress" aria-valuemin="0" aria-valuemax="100"><span></span></div>' +
    '<div class="movie-download-message"></div>';
  document.body.appendChild(panel);
  const heading = panel.querySelector('.movie-download-heading');
  const bar = panel.querySelector('.movie-download-progress');
  const fill = bar.querySelector('span');
  const message = panel.querySelector('.movie-download-message');
  let polling = false;
  let timer;
  const render = job => {
    if (typeof job.file_complete === 'boolean') setAvailability(job.file_complete);
    const info = job.media_info;
    mediaDetails.hidden = !job.file_complete;
    if (job.file_complete) {
      const size = info?.filesize_bytes;
      const formatSize = size == null ? 'Unavailable' :
        size >= 1073741824 ? (size / 1073741824).toFixed(2) + ' GB' :
        (size / 1048576).toFixed(1) + ' MB';
      mediaDetails.textContent = `Resolution: ${info?.resolution || 'Unavailable'} · Video: ${info?.video_codec || 'Unavailable'} · Audio: ${info?.audio_codec || 'Unavailable'} · Size: ${formatSize}`;
    } else mediaDetails.textContent = '';
    const running = job.state === 'running';
    const queued = job.state === 'queued';
    button.disabled = running || queued;
    panel.hidden = job.state === 'idle';
    const progress = typeof job.progress === 'number' ? Math.max(0, Math.min(100, job.progress)) : null;
    label.textContent = queued ? `Queued #${job.queue_position || '…'}` :
      running ? (progress === null ? 'Starting…' : `Downloading ${progress.toFixed(1)}%`) :
      job.state === 'complete' ? 'Download again' : 'Download';
    heading.textContent = queued ? `Waiting in queue · #${job.queue_position || '…'}` :
      running ? (progress === null ? 'Preparing download…' : `Downloading: ${progress.toFixed(1)}%`) :
      job.state === 'complete' ? 'Download complete' : job.state === 'error' ? 'Download failed' : '';
    bar.hidden = progress === null;
    if (progress === null) bar.removeAttribute('aria-valuenow');
    else { bar.setAttribute('aria-valuenow', String(progress)); fill.style.width = `${progress}%`; }
    message.textContent = job.message || '';
    if (running || queued) { polling = true; clearTimeout(timer); timer = setTimeout(refresh, 1000); }
    else polling = false;
  };
  async function refresh() {
    try {
      const response = await fetch(endpoint, {cache:'no-store'});
      if (!response.ok) throw Error('Could not read download progress');
      render(await response.json());
    } catch (error) {
      if (polling) { clearTimeout(timer); timer = setTimeout(refresh, 3000); }
      else render({state:'error',message:error.message,progress:null});
    }
  }
  button.addEventListener('click', async event => {
    event.preventDefault();event.stopImmediatePropagation();
    if (button.disabled) return;
    button.disabled = true;
    render({state:'running',progress:null,message:'Starting download.py…'});
    try {
      const response = await fetch(endpoint, {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const job = await response.json();
      if (!response.ok && !job.message) throw Error(`Could not start download (${response.status})`);
      render(job);
    } catch (error) { render({state:'error',progress:null,message:error.message}); }
  }, true);
  refresh();
  setInterval(() => { if (!polling) refresh(); }, 5000);
})();
