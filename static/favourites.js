(() => {
  const account = window.__account || {};
  const saved = new Set();
  const key = (type, id) => `${type}/${id}`;
  const path = href => {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin) return null;
      const match = url.pathname.match(/^\/(movie|tv)\/(\d+)\/?$/);
      return match && {type:match[1], id:match[2]};
    } catch (_) { return null; }
  };
  let ready = false;
  const load = account.name ? fetch('/api/profile/favourites').then(r => r.ok ? r.json() : {results:[]}) : Promise.resolve({results:[]});
  load.then(data => { (data.results || []).forEach(item => saved.add(key(item.type,item.id))); })
    .catch(() => {}).finally(() => { ready = true; refresh(); });
  function refresh() {
    if (!ready) return;
    for (const button of document.querySelectorAll('.favourite-heart')) {
      const selected = saved.has(button.dataset.key);
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-label', selected ? 'Remove from favourites' : 'Add to favourites');
      button.title = button.getAttribute('aria-label');
    }
  }
  function heart(item) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'favourite-heart';
    button.textContent = '♥'; button.dataset.key = key(item.type,item.id);
    button.addEventListener('click', async event => {
      event.preventDefault(); event.stopPropagation();
      if (!account.name) { location.assign('/login'); return; }
      button.disabled = true;
      const wasSaved = saved.has(button.dataset.key);
      try {
        const response = await fetch('/api/profile/favourites', {
          method:wasSaved ? 'DELETE' : 'POST',
          headers:{'Content-Type':'application/json','X-CSRF-Token':account.csrf},
          body:JSON.stringify(item)
        });
        if (!response.ok) throw Error('Could not save favourite');
        wasSaved ? saved.delete(button.dataset.key) : saved.add(button.dataset.key);
        refresh();
        window.dispatchEvent(new Event('favourites-changed'));
      } catch (error) { alert(error.message); }
      finally { button.disabled = false; }
    });
    return button;
  }
  function scan() {
    for (const link of document.querySelectorAll('a[href*="/movie/"],a[href*="/tv/"]')) {
      const item = path(link.href);
      if (!item || link.closest('header,nav,footer,.hero-dynamic-nav')) continue;
      let tile = link.closest('.media-card,.genre-card,.backend-search-card,[class*="movieCard"]');
      if (!tile && link.querySelector('img')) tile = link.closest('.group') || link;
      if (!tile || tile.closest('.HomeBanner-module-scss-module__eAYNsa__slideLayer')) continue;
      let container = tile.closest('.favourite-tile');
      if (!container) {
        container = document.createElement('div'); container.className = 'favourite-tile';
        tile.parentNode.insertBefore(container, tile); container.append(tile);
      }
      if (!container.querySelector(':scope > .favourite-heart')) container.append(heart(item));
    }
    const current = path(location.href);
    if (current && !document.querySelector('.favourite-detail-heart')) {
      const detail = heart(current); detail.classList.add('favourite-detail-heart');
      document.body.append(detail);
    }
    refresh();
  }
  let pending = false;
  new MutationObserver(() => {
    if (pending) return;
    pending = true; requestAnimationFrame(() => { pending = false; scan(); });
  }).observe(document.body,{childList:true,subtree:true});
  scan();
})();
