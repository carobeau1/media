(() => {
  const menu = document.createElement('div');
  menu.className = 'title-download-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', 'Title actions');
  document.body.append(menu);
  const notice = document.createElement('div');
  notice.className = 'title-download-notice';
  notice.setAttribute('role', 'status');
  notice.hidden = true;
  document.body.append(notice);
  let noticeTimer;
  let context = null;
  let requestId = 0;
  const announce = message => {
    notice.textContent = message;
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 5500);
  };
  const close = () => {menu.hidden = true;menu.replaceChildren();context = null;requestId++;};
  const button = (label, action) => {
    const control = document.createElement('button');
    control.type = 'button';control.setAttribute('role', 'menuitem');
    control.textContent = label;control.addEventListener('click', action);
    return control;
  };
  const place = (x, y) => {
    menu.hidden = false;
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  };
  const requestDownload = async (endpoint, label) => {
    close();
    announce(`Adding ${label} to the download queue…`);
    try {
      const response = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || result.error || `Download request failed (HTTP ${response.status})`);
      announce(result.state === 'queued' ? `${label} queued for download.` :
        result.state === 'running' ? `${label} is downloading.` :
        `${label}: ${result.message || 'Download requested.'}`);
    } catch (error) {announce(error.message);}
  };
  const showEpisodes = async (title, x, y) => {
    const token = ++requestId;
    menu.replaceChildren();
    const heading = document.createElement('strong');heading.textContent = 'Choose an episode to download';
    menu.append(heading, button('Loading episodes…', () => {}));
    menu.lastElementChild.disabled = true;
    place(x, y);
    try {
      const response = await fetch(`/api/shows/${encodeURIComponent(title.id)}`);
      if (!response.ok) throw new Error(`Could not load episodes (HTTP ${response.status})`);
      const show = await response.json();
      if (token !== requestId || menu.hidden) return;
      menu.replaceChildren(heading);
      let count = 0;
      for (const season of show.seasons || []) {
        for (const episode of season.episodes || []) {
          count++;
          const number = `S${String(season.number).padStart(2,'0')}E${String(episode.number).padStart(2,'0')}`;
          menu.append(button(`${number} · ${episode.title || 'Episode'}`, () =>
            requestDownload(`/api/tv/${encodeURIComponent(title.id)}/${season.number}/${episode.number}/download`, `${show.title || title.id} ${number}`)));
        }
      }
      if (!count) menu.append(button('No episodes available', () => {}));
      if (!count) menu.lastElementChild.disabled = true;
      place(x, y);
      menu.querySelector('button:not([disabled])')?.focus();
    } catch (error) {
      if (token === requestId) {close();announce(error.message);}
    }
  };
  const titleAt = target => {
    let link = target.closest?.('a[href^="/movie/"],a[href^="/tv/"]');
    if (!link) link = target.closest?.('.media-card,.genre-card,.backend-search-card,.favourite-tile,.movieGrid-module-scss-module__NJm1Na__movieGrid > div,.home-poster-tile')?.querySelector('a[href^="/movie/"],a[href^="/tv/"]');
    let path = link?.getAttribute('href') || '';
    if (!path && target.closest?.('.HomeBanner-module-scss-module__eAYNsa__heroRoot')) path = location.pathname;
    const movie = path.match(/^\/movie\/(\d+)(?:[/?#]|$)/);
    if (movie) return {kind:'movie',id:movie[1],label:link?.textContent.trim() || document.title};
    const episode = path.match(/^\/tv\/(\d+)\/(\d+)\/(\d+)(?:[/?#]|$)/);
    if (episode) return {kind:'episode',id:episode[1],season:episode[2],episode:episode[3],label:link?.textContent.trim() || 'Episode'};
    const show = path.match(/^\/tv\/(\d+)(?:[/?#]|$)/);
    if (show) return {kind:'tv',id:show[1],label:link?.textContent.trim() || document.title};
    return null;
  };
  const openFor = (title, x, y) => {
    close();context = title;
    menu.append(button('Download', () => {
      if (title.kind === 'movie') requestDownload(`/api/movies/${title.id}/download`, title.label);
      else if (title.kind === 'episode') requestDownload(`/api/tv/${title.id}/${title.season}/${title.episode}/download`, title.label);
      else showEpisodes(title, x, y);
    }));
    for (const action of window.mediaTitleActions?.(title) || []) {
      menu.append(button(action.label, async () => {
        close();
        try { await action.run(); } catch (error) { announce(error.message); }
      }));
    }
    place(x, y);
    menu.querySelector('button')?.focus();
  };
  document.addEventListener('contextmenu', event => {
    if (event.target.closest?.('input,textarea,[contenteditable="true"],.title-download-menu')) return;
    const title = titleAt(event.target);
    if (!title) {close();return;}
    event.preventDefault();openFor(title, event.clientX, event.clientY);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) {event.preventDefault();close();return;}
    if ((event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) && !event.target.closest?.('input,textarea')) {
      const title = titleAt(event.target);
      if (title) {event.preventDefault();const rect=event.target.getBoundingClientRect();openFor(title, rect.left, rect.bottom);}
    }
  });
  document.addEventListener('pointerdown', event => {if (!menu.hidden && !menu.contains(event.target)) close();});
  window.addEventListener('scroll', () => {if (!menu.hidden) close();}, true);
})();
