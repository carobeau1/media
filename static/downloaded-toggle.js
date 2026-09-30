(() => {
  document.cookie = 'media_view=; Path=/; Max-Age=0; SameSite=Lax';
  let nav = document.querySelector('header nav.header-search-cluster');
  let headerRow;
  let search, profile;
  if (nav) {
    headerRow = nav.parentElement;
    search = nav.querySelector('[aria-label="Search"]');
    profile = nav.querySelector('.profile-trigger');
  } else {
    const header = document.createElement('header');
    header.className = 'media-shared-header';
    header.innerHTML = '<div class="media-shared-header-inner"><nav class="header-search-cluster" aria-label="Main">' +
      '<a href="/">Home</a><a href="/movies">Movies</a><a href="/shows">TV</a>' +
      '</nav></div>';
    document.body.prepend(header);
    nav = header.querySelector('nav');
    headerRow = header.querySelector('.media-shared-header-inner');
    const active = [...nav.querySelectorAll('a')].find(link => link.pathname === location.pathname);
    if (active) active.setAttribute('aria-current', 'page');
    search = document.createElement('button');
    search.type = 'button';
    search.setAttribute('aria-label', 'Search');
    search.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/></svg>';
    profile = document.createElement('div');
    profile.className = 'profile-trigger';
    profile.innerHTML = '<button type="button" aria-label="Profile" aria-expanded="false" aria-haspopup="menu"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/></svg></button>';
    if (!document.getElementById('backend-search')) {
      search.addEventListener('click', () => { location.assign('/?search=1'); });
    }
  }
  nav.querySelectorAll('a[href="/anime"]').forEach(link => link.remove());
  document.querySelectorAll('h2').forEach(heading => {
    if (/^Trending in Austra(?:l)?ia$/i.test(heading.textContent.trim())) heading.closest('section')?.remove();
  });
  const back = document.createElement('button');
  back.className = 'media-nav-back';
  back.type = 'button';
  back.setAttribute('aria-label', 'Go back');
  back.title = 'Back';
  back.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>';
  back.addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (history.length > 1) history.back();
    else location.assign('/');
  });
  const home = nav.querySelector('a[href="/"]');
  if (home) home.before(back);
  const actions = document.createElement('div');
  actions.className = 'media-header-actions';
  if (search) actions.append(search);
  if (profile) actions.append(profile);
  headerRow.append(actions);

  // The captured navigation highlight has fixed pixel coordinates.
  const updateMarker = () => {
    nav.querySelectorAll('a[href="/movies"],a[href="/shows"]').forEach(link => link.classList.add('media-nav-equal-width'));
    const selected = nav.querySelector('a[aria-current="page"]');
    const marker = nav.querySelector('span[aria-hidden="true"].absolute');
    if (selected && marker) {
      marker.style.transform = `translateX(${selected.offsetLeft}px)`;
      marker.style.width = `${selected.offsetWidth}px`;
    }
  };
  requestAnimationFrame(updateMarker);
  window.addEventListener('resize', updateMarker);
  if (location.pathname === '/' && new URLSearchParams(location.search).has('search')) {
    setTimeout(() => search?.click(), 0);
  }
})();
