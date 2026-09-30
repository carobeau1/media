(() => {
  const section = document.getElementById('playback-source-settings');
  if (!section) return;
  const select = section.querySelector('#playback-source');
  const save = section.querySelector('#save-playback-source');
  const playerURL = section.querySelector('#playback-player-url');
  const pageURL = section.querySelector('#playback-page-url');
  const status = section.querySelector('#playback-source-status');
  let sources = [];
  let selected = '';
  const describe = () => {
    const source = sources.find(item => item.name === select.value);
    playerURL.textContent = source?.player_url_template || 'Unavailable';
    pageURL.textContent = source?.movie_url_template || 'Unavailable';
  };
  select.addEventListener('change', describe);
  save.addEventListener('click', async () => {
    if (!sources.some(item => item.name === select.value)) return;
    select.disabled = save.disabled = true;
    status.textContent = 'Saving…';
    try {
      const response = await fetch('/api/profile/movie-source', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.__settingsCSRF || ''},
        body: JSON.stringify({source: select.value})
      });
      const data = await window.readSettingsJSON(response);
      selected = data.source;
      status.textContent = 'Playback source saved to your profile.';
    } catch (error) {
      select.value = selected;
      describe();
      status.textContent = error.message;
    } finally {
      select.disabled = save.disabled = false;
    }
  });
  fetch('/api/profile/movie-source', {credentials: 'same-origin', cache: 'no-store'})
    .then(response => window.readSettingsJSON(response))
    .then(data => {
      sources = data.sources || [];
      selected = data.source;
      select.replaceChildren();
      for (const source of sources) {
        const option = document.createElement('option');
        option.value = source.name;
        option.textContent = source.name + (source.player_url_template.includes('.invalid') ? ' (example only)' : '');
        select.append(option);
      }
      select.value = selected;
      describe();
      select.disabled = save.disabled = sources.length === 0;
      if (!sources.length) status.textContent = 'No movie sources are configured.';
    })
    .catch(error => {status.textContent = error.message;});
})();
