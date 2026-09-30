(() => {
  const historyKey = 'media-history-v1';
  const playedKey = 'media-played-v1';
  const listKey = 'media-watch-list-v1';
  const read = key => {
    try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value : []; }
    catch (_) { return []; }
  };
  const save = (key, items) => { try { localStorage.setItem(key, JSON.stringify(items)); } catch (_) {} };
  const hidden = new Set(window.__hiddenMediaPaths || []);
  const current = window.__currentMedia;
  window.addEventListener('media-played', event => {
    const item = event.detail;
    if (!item || !/^(movie|tv)$/.test(item.type) || !/^\d+$/.test(String(item.id))) return;
    const url = `/${item.type}/${item.id}`;
    if (hidden.has(url)) return;
    const items = read(playedKey).filter(entry => entry.url !== url);
    items.unshift({type:item.type,id:String(item.id),url,played:new Date().toISOString()});
    save(playedKey,items.slice(0,60));
  });
  if (current && !hidden.has(current.url)) {
    const items = read(historyKey).filter(item => item.url !== current.url && !hidden.has(item.url));
    items.unshift({...current, visited: new Date().toISOString()});
    save(historyKey, items.slice(0, 200));
    const add = document.querySelector('button[aria-label="Add to List"]');
    if (add) {
      const update = () => {
        const onList = read(listKey).some(item => item.url === current.url);
        add.setAttribute('aria-label', onList ? 'Remove from Watch List' : 'Add to List');
        add.title = onList ? 'Remove from Watch List' : 'Add to List';
        const label = [...add.querySelectorAll('span')].find(span => span.textContent.trim() === 'Add to List' || span.textContent.trim() === 'In Watch List');
        if (label) label.textContent = onList ? 'In Watch List' : 'Add to List';
      };
      add.addEventListener('click', event => {
        event.preventDefault(); event.stopImmediatePropagation();
        const items = read(listKey).filter(item => item.url !== current.url && !hidden.has(item.url));
        if (!read(listKey).some(item => item.url === current.url)) items.unshift(current);
        save(listKey, items); update();
      }, true);
      update();
    }
  }

  const trigger = document.querySelector('.profile-trigger');
  let wrap = trigger;
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'profile-trigger profile-fallback';
    wrap.innerHTML = '<button type="button" aria-label="Profile" aria-expanded="false" aria-haspopup="menu">' +
      '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/></svg></button>';
    document.body.appendChild(wrap);
  }
  const button = wrap.querySelector('button');
  if (!button) return;
  button.setAttribute('aria-label', 'Profile');
  button.setAttribute('aria-haspopup', 'menu');
  const account = window.__account || {};
  if (account.name) {
    wrap.classList.add('profile-signed-in');
    const username = document.createElement('span');
    username.className = 'profile-username';
    username.textContent = account.name;
    wrap.append(username);
  }
  const menu = document.createElement('div');
  menu.id = 'profile-dropdown';
  menu.className = 'profile-dropdown';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', 'Profile');
  menu.hidden = true;
  menu.innerHTML = '<a role="menuitem" href="/history">History</a>' +
    '<a role="menuitem" href="/watch-list">Watch List</a>' +
    '<a role="menuitem" href="/favourites">Favourites</a>' +
    (account.name ? '<a role="menuitem" href="/parental-controls">Parental Controls</a>' : '') +
    (account.name ? '<a role="menuitem" href="/similar-scan">Scan Similar Titles</a>' : '') +
    (account.name ? '<a role="menuitem" href="/import-movies">Import Movies</a>' : '') +
    (account.name ? '<a role="menuitem" href="/settings">Settings</a>' : '') +
    '<a role="menuitem" href="/watch-party">Watch Party</a>' +
    (account.name
      ? '<div class="profile-menu-divider"></div><form class="profile-account-actions" method="post" action="/sign-out"><button type="submit" role="menuitem">Sign Out</button></form>'
      : '<div class="profile-menu-divider"></div><div class="profile-account-actions"><a role="menuitem" href="/login">Login</a><a role="menuitem" href="/sign-up">Sign Up</a></div>');
  if (account.name) {
    const csrf = document.createElement('input');
    csrf.type = 'hidden'; csrf.name = 'csrf'; csrf.value = account.csrf || '';
    menu.querySelector('form').append(csrf);
  }
  wrap.appendChild(menu);
  button.setAttribute('aria-controls', menu.id);
  const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', event => {
    event.preventDefault(); event.stopImmediatePropagation();
    menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('a,button')?.focus();
  }, true);
  document.addEventListener('click', event => { if (!wrap.contains(event.target)) close(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) { close(); button.focus(); }
  });

  const list = document.getElementById('profile-local-list');
  if (list) {
    const kind = list.dataset.kind;
    const key = kind === 'history' ? historyKey : listKey;
    const items = read(key).filter(item => item.url && !hidden.has(item.url));
    save(key, items);
    if (!items.length) {
      list.textContent = kind === 'history' ? 'No viewing history yet.' : 'Your Watch List is empty.';
    } else {
      list.replaceChildren();
      for (const item of items) {
        const row = document.createElement('div'); row.className = 'profile-list-row';
        const link = document.createElement('a');
        link.href = /^\/(movie|tv)\/\d+$/.test(item.url) ? item.url : '#';
        link.textContent = item.title || item.url;
        row.appendChild(link);
        const type = document.createElement('small'); type.textContent = item.type === 'tv' ? 'Show' : 'Movie';
        row.appendChild(type);
        if (kind !== 'history') {
          const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
          remove.onclick = () => {
            save(key, read(key).filter(entry => entry.url !== item.url)); row.remove();
            if (!list.children.length) list.textContent = 'Your Watch List is empty.';
          };
          row.appendChild(remove);
        }
        list.appendChild(row);
      }
    }
  }
})();
