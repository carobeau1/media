(() => {
  const genre = new URLSearchParams(location.search).get('genre');
  const grid = document.querySelector('.movieGrid-module-scss-module__NJm1Na__movieGrid');
  if (!genre || !grid) return;
  grid.hidden = true;
  const panel = document.createElement('section');panel.className='genre-results';
  panel.innerHTML='<p class="genre-message">Loading shows…</p>';
  grid.before(panel);
  fetch('/api/shows/by-genre?genre='+encodeURIComponent(genre)).then(r => {
    if (!r.ok) throw Error('Could not load shows');return r.json();
  }).then(data => {
    panel.replaceChildren();
    const title=document.createElement('h2'); title.textContent=`${genre} shows`;
    panel.append(title);
    if (!(data.results || []).length) {const empty=document.createElement('p');empty.textContent='No matching shows in the database.';panel.append(empty);return;}
    const items=document.createElement('div');items.className='genre-grid';
    for (const show of data.results) {
      const link=document.createElement('a');link.className='genre-card';link.href=show.url;
      const image=document.createElement('img');image.src=show.poster||'';image.alt='';image.loading='lazy';
      const name=document.createElement('strong');name.textContent=show.title;
      link.append(image,name);items.append(link);
    }
    panel.append(items);
  }).catch(error => {panel.textContent=error.message;});
})();
