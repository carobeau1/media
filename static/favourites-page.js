(async () => {
  const list = document.getElementById('favourite-list');
  if (!list) return;
  try {
    const response = await fetch('/api/profile/favourites');
    if (response.status === 401) { list.innerHTML = '<a href="/login">Log in to see your favourites</a>'; return; }
    if (!response.ok) throw Error('Favourites unavailable');
    const data = await response.json();
    list.replaceChildren();
    if (!data.results.length) { list.textContent = 'No favourites yet. Use the heart on a movie or show.'; return; }
    for (const item of data.results) {
      const link = document.createElement('a'); link.href = item.url;
      link.className = 'favourite-list-item';
      if (item.poster) { const image = document.createElement('img'); image.src=item.poster; image.alt=''; link.append(image); }
      const label = document.createElement('span'); label.textContent = `${item.title} ${item.year || ''}`;
      link.append(label); list.append(link);
    }
  } catch (_) { list.textContent = 'Could not load favourites.'; }
})();
