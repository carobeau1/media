(() => {
  const hidden = new Set(window.__hiddenMediaPaths || []);
  const titlePath = href => {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin) return null;
      const match = url.pathname.match(/^\/(movie|tv)\/(\d+)\/?$/);
      return match ? `/${match[1]}/${match[2]}` : null;
    } catch (_) { return null; }
  };
  const tileFor = link => link.closest('.media-card,.genre-card,.backend-search-card,.HomeBanner-module-scss-module__eAYNsa__slideLayer') ||
    (link.querySelector('img') ? link.closest('.group') : null) || link;
  const itemFor = tile => {
    const item = tile.closest('.ScrollRow-module-scss-module__-kZXvG__track > *, .movieGrid-module-scss-module__NJm1Na__movieGrid > *');
    return item || tile.closest('.favourite-tile') || tile.closest('.group') || tile;
  };
  const hideRenderedTiles = () => {
    if (!hidden.size) return;
    for (const link of document.querySelectorAll('a[href*="/movie/"],a[href*="/tv/"]')) {
      const path = titlePath(link.href);
      if (!path || !hidden.has(path)) continue;
      const tile = tileFor(link);
      if (tile.closest('header,nav,footer')) continue;
      itemFor(tile).remove();
    }
  };
  // The shared title menu owns rendering and keyboard interaction.
  window.mediaTitleActions = title => {
    const kind = title.kind === 'movie' ? 'movie' : 'tv';
    const path = `/${kind}/${title.id}`;
    if (hidden.has(path)) return [];
    const actions = [{label: kind === 'tv' ? 'Hide Show' : 'Hide Movie', run: async () => {
      const response = await fetch('/api/media' + path + '/hide', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title:title.label || ''})
      });
      if (!response.ok) throw Error('Could not hide this title');
      hidden.add(path);
      hideRenderedTiles();
      if (location.pathname === path) location.assign('/');
      const counter = document.querySelector('.database-status-footer__hidden');
      if (counter) counter.textContent = `Hidden (${hidden.size})`;
    }}];
    let played = [];
    try { played = JSON.parse(localStorage.getItem('media-played-v1') || '[]'); } catch (_) {}
    if (Array.isArray(played) && played.some(item => item?.url === path)) {
      actions.push({label:'Remove from Recently Played', run: () => {
        const current = JSON.parse(localStorage.getItem('media-played-v1') || '[]');
        if (!Array.isArray(current)) throw Error('Could not update Recently Played.');
        localStorage.setItem('media-played-v1', JSON.stringify(current.filter(item => item?.url !== path)));
        window.dispatchEvent(new Event('recently-played-changed'));
      }});
    }
    return actions;
  };
  hideRenderedTiles();
  const observer = new MutationObserver(hideRenderedTiles);
  observer.observe(document.body, {childList:true,subtree:true});
})();
