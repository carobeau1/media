(() => {
  const section = document.getElementById('recently-played-placeholder');
  if (!section) return;
  const rail = section.querySelector('.recently-played-rail');
  const favouritesSection = document.createElement('section');
  favouritesSection.className = 'recently-played-section recent-favourites-section';
  const favouritesTitle = document.createElement('h2');
  favouritesTitle.textContent = 'Recent favourites';
  const favouritesRail = document.createElement('div');
  favouritesRail.className = 'recently-played-rail';
  favouritesSection.append(favouritesTitle, favouritesRail);
  section.after(favouritesSection);
  const render = (target, items, emptyMessage) => {
    target.replaceChildren();
    for (const item of items) {
      const link=document.createElement('a');link.href=item.url;link.className='recently-played-card';
      const cover=document.createElement('div');cover.className='recently-played-cover';
      if (item.poster) {const img=document.createElement('img');img.src=item.poster;img.alt='';img.loading='lazy';cover.append(img);}
      const name=document.createElement('span');name.className='recently-played-title';name.textContent=item.title;
      const meta=document.createElement('span');meta.className='recently-played-meta';
      meta.textContent=[item.rating?`★ ${item.rating}`:'',item.year||'',item.type==='tv'?'TV':'Movie'].filter(Boolean).join(' · ');
      link.append(cover,name,meta);target.append(link);
    }
    if (!target.children.length) target.textContent=emptyMessage;
  };
  const loadFavourites = async () => {
    if (!window.__account?.name) {favouritesRail.textContent='Log in to see your favourites.';return;}
    favouritesRail.textContent='Loading favourites…';
    try {
      const response = await fetch('/api/profile/favourites');
      if (!response.ok) throw Error('Could not load favourites');
      const favourites = (await response.json()).results || [];
      if (!favourites.length) {render(favouritesRail, [], 'No favourites yet.');return;}
      const details = await fetch('/api/recently-played', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({titles:favourites.slice(0,60).map(item => ({type:item.type,id:item.id}))})});
      if (!details.ok) throw Error('Could not load favourites');
      render(favouritesRail, (await details.json()).results || [], 'No favourites are available.');
    } catch (error) {favouritesRail.textContent=error.message;}
  };
  loadFavourites();
  window.addEventListener('favourites-changed', loadFavourites);
  let loadSequence = 0;
  const loadRecently = () => {
    const sequence = ++loadSequence;
    let stored = [];
    try { stored = JSON.parse(localStorage.getItem('media-played-v1') || '[]'); } catch (_) {}
    if (!Array.isArray(stored)) stored = [];
    const unique = new Set();
    const titles = stored.map(item => {
      const match = /^\/(movie|tv)\/(\d+)$/.exec(item?.url || '');
      return match ? {type:match[1],id:match[2]} : null;
    }).filter(item => {
      if (!item) return false;
      const key = `${item.type}/${item.id}`;
      if (unique.has(key)) return false;
      unique.add(key);return true;
    }).slice(0,30);
    if (!titles.length) {rail.textContent='Play a movie or show to see it here.';return;}
    fetch('/api/recently-played', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({titles})})
      .then(response => {if(!response.ok)throw Error('Could not load recently played titles');return response.json()})
      .then(data => {if (sequence === loadSequence) render(rail, data.results || [], 'No recently played titles are available.');})
      .catch(error => {if (sequence === loadSequence) rail.textContent=error.message;});
  };
  window.addEventListener('recently-played-changed',loadRecently);
  loadRecently();
})();
