(() => {
  const isTV = location.pathname === '/shows';
  const mediaType = isTV ? 'tv' : 'movie';
  const label = isTV ? 'TV' : 'Movie';
  const plural = isTV ? 'TV titles' : 'movies';
  const typeApi = isTV ? '/api/tv' : '/api/movies';
  const choicesApi = isTV ? '/api/tv-' : '/api/movie-';
  const profileApi = isTV ? '/api/profile/tv-filters' : '/api/profile/movie-filters';
  const trigger = document.querySelector('button[aria-label="Genres"]');
  const sortTrigger = document.querySelector('button[aria-label="Sort by"]');
  const yearTrigger = document.querySelector('button[aria-label="Years"]');
  const countryTrigger = document.querySelector('button[aria-label="Countries"]');
  const account = window.__account || {};
  let preferencesReady = false, preferencesTimer;
  const certificationChoices = ['G', 'PG', 'PG-13', 'R', 'NC-17'];
  const parental = window.__parentalSettings || {};
  const blockedGenres = new Set((parental.genres || []).map(name => name.toLowerCase()));
  const blockedClasses = new Set((parental.classifications || []).map(name => name.toLowerCase()));
  for (let i = certificationChoices.length - 1; i >= 0; i--) {
    if (blockedClasses.has(certificationChoices[i].toLowerCase())) certificationChoices.splice(i, 1);
  }
  const grid = document.querySelector('.windowedMovieGrid-module-scss-module__S9vvea__stack');
  if (!trigger || !grid) return;
  const choices = ['4K', 'Comedy', 'Action', 'Drama', 'Horror', 'Romance', 'Adventure',
    'Science Fiction', 'Thriller', 'Animation', 'Crime', 'Fantasy', 'Mystery',
    'Documentary', 'Family', 'History', 'Music', 'War', 'Western'];
  for (let i = choices.length - 1; i >= 0; i--) {
    if (blockedGenres.has(choices[i].toLowerCase())) choices.splice(i, 1);
  }
  const selected = new Set(new URLSearchParams(location.search).getAll('genre'));
  const selectedCountries = new Set(new URLSearchParams(location.search).getAll('country'));
  const selectedCertifications = new Set(new URLSearchParams(location.search).getAll('certification'));
  const sortChoices = {popular:'Most popular', rated:'Top rated', newest:'Newest', oldest:'Oldest',
    ...(isTV ? {latest_episode:'Latest Episode'} : {})};
  let sort = new URLSearchParams(location.search).get('sort') || 'popular';
  if (!(sort in sortChoices)) sort = 'popular';
  const initialParams = new URLSearchParams(location.search);
  let source = ['downloaded','not_downloaded'].includes(initialParams.get('source')) ? initialParams.get('source') : 'all';
  let oldestYear = null, newestYear = null;
  let ratingMin = initialParams.has('rating_min') ? Number(initialParams.get('rating_min')) : 0;
  let ratingMax = initialParams.has('rating_max') ? Number(initialParams.get('rating_max')) : 10;
  let yearFrom = Number(initialParams.get('year_from')) || null;
  let yearTo = Number(initialParams.get('year_to')) || null;
  const wrapper = trigger.parentElement;
  const menu = document.createElement('div');
  menu.className = 'genre-menu';
  menu.id = 'movie-genre-menu';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', `${label} genres`);
  menu.setAttribute('aria-multiselectable', 'true');
  menu.hidden = true;
  trigger.setAttribute('aria-controls', menu.id);
  wrapper.appendChild(menu);
  const results = document.createElement('div');
  results.className = 'genre-results';
  results.hidden = true;
  grid.after(results);
  let certificationTrigger, certificationMenu;
  if (countryTrigger) {
    const wrapper = countryTrigger.parentElement.cloneNode(true);
    certificationTrigger = wrapper.querySelector('button');
    certificationTrigger.setAttribute('aria-label', 'Classification');
    certificationTrigger.querySelector('.whitespace-nowrap').textContent = 'Classification';
    wrapper.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
    countryTrigger.parentElement.after(wrapper);
    certificationMenu = document.createElement('div');
    certificationMenu.className = 'genre-menu movie-certification-menu';
    certificationMenu.id = 'movie-certification-menu';
    certificationMenu.setAttribute('role', 'listbox');
    certificationMenu.setAttribute('aria-label', `${label} age classifications`);
    certificationMenu.setAttribute('aria-multiselectable', 'true');
    certificationMenu.hidden = true;
    wrapper.appendChild(certificationMenu);
    certificationTrigger.setAttribute('aria-controls', certificationMenu.id);
    const renderCertifications = () => {
      certificationMenu.replaceChildren();
      for (const value of certificationChoices) {
        const option = document.createElement('button');
        option.type = 'button'; option.className = 'genre-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(selectedCertifications.has(value)));
        option.innerHTML = '<span class="genre-check" aria-hidden="true">✓</span><span class="genre-label"></span>';
        option.querySelector('.genre-label').textContent = value;
        option.onclick = () => {
          selectedCertifications.has(value) ? selectedCertifications.delete(value) : selectedCertifications.add(value);
          option.setAttribute('aria-selected', String(selectedCertifications.has(value)));
          update();
        };
        certificationMenu.appendChild(option);
      }
      const clear = document.createElement('button');
      clear.type = 'button'; clear.className = 'genre-clear'; clear.textContent = 'Clear classifications';
      clear.onclick = () => { selectedCertifications.clear(); renderCertifications(); update(); };
      certificationMenu.appendChild(clear);
    };
    renderCertifications();
    certificationTrigger.onclick = event => {
      event.preventDefault();
      menu.hidden = true; trigger.setAttribute('aria-expanded', 'false');
      if (yearMenu) { yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false'); }
      if (countryMenu) { countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false'); }
      if (sortMenu) { sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false'); }
      certificationMenu.hidden = !certificationMenu.hidden;
      certificationTrigger.setAttribute('aria-expanded', String(!certificationMenu.hidden));
    };
    fetch(choicesApi+'certifications').then(r => r.json()).then(data => {
      for (const value of data.certifications || []) {
        if (!certificationChoices.some(item => item.toLowerCase() === value.toLowerCase())) certificationChoices.push(value);
      }
      renderCertifications();
    }).catch(() => {});
  }
  let ratingTrigger, ratingMenu;
  if (certificationTrigger) {
    const ratingWrapper = certificationTrigger.parentElement.cloneNode(true);
    ratingTrigger = ratingWrapper.querySelector('button');
    ratingTrigger.setAttribute('aria-label', 'Ratings');
    ratingTrigger.querySelector('.whitespace-nowrap').textContent = 'Ratings';
    ratingWrapper.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
    certificationTrigger.parentElement.after(ratingWrapper);
    ratingMenu = document.createElement('div');
    ratingMenu.className = 'movie-year-menu movie-rating-menu';
    ratingMenu.id = 'movie-rating-menu'; ratingMenu.hidden = true;
    ratingMenu.innerHTML = '<div class="movie-year-heading">IMDb rating</div>' +
      '<div class="movie-year-values"><output class="rating-from-value"></output><span>to</span><output class="rating-to-value"></output></div>' +
      '<div class="movie-year-sliders"><div class="movie-year-track"></div>' +
      '<input type="range" class="rating-from-slider" min="0" max="10" step="0.1" aria-label="Minimum rating">' +
      '<input type="range" class="rating-to-slider" min="0" max="10" step="0.1" aria-label="Maximum rating"></div>' +
      '<button type="button" class="movie-year-reset">Clear ratings</button>';
    ratingWrapper.appendChild(ratingMenu);
    ratingTrigger.setAttribute('aria-controls', ratingMenu.id);
    const from = ratingMenu.querySelector('.rating-from-slider');
    const to = ratingMenu.querySelector('.rating-to-slider');
    const showRatings = () => {
      from.value = ratingMin; to.value = ratingMax;
      ratingMenu.querySelector('.rating-from-value').textContent = ratingMin.toFixed(1);
      ratingMenu.querySelector('.rating-to-value').textContent = ratingMax.toFixed(1);
      ratingMenu.querySelector('.movie-year-track').style.background =
        `linear-gradient(to right,#ffffff35 0%,#ffffff35 ${ratingMin*10}%,#d81a28 ${ratingMin*10}%,#d81a28 ${ratingMax*10}%,#ffffff35 ${ratingMax*10}%)`;
      ratingTrigger.querySelector('.whitespace-nowrap').textContent = ratingMin === 0 && ratingMax === 10 ? 'Ratings' : `${ratingMin.toFixed(1)}–${ratingMax.toFixed(1)}`;
      ratingTrigger.dataset.active = String(ratingMin !== 0 || ratingMax !== 10);
    };
    from.oninput = () => { ratingMin = Math.min(Number(from.value), ratingMax); showRatings(); };
    to.oninput = () => { ratingMax = Math.max(Number(to.value), ratingMin); showRatings(); };
    from.onchange = to.onchange = () => update();
    ratingMenu.querySelector('.movie-year-reset').onclick = () => { ratingMin = 0; ratingMax = 10; showRatings(); update(); };
    ratingTrigger.onclick = event => {
      event.preventDefault();
      for (const [popup, button] of [[menu, trigger], [yearMenu, yearTrigger], [countryMenu, countryTrigger], [certificationMenu, certificationTrigger], [sortMenu, sortTrigger]]) {
        if (popup && button) { popup.hidden = true; button.setAttribute('aria-expanded', 'false'); }
      }
      ratingMenu.hidden = !ratingMenu.hidden;
      ratingTrigger.setAttribute('aria-expanded', String(!ratingMenu.hidden));
    };
    showRatings();
  }
  let sourceTrigger, sourceMenu, renderSource = () => {};
  if (ratingTrigger) {
    const sourceWrapper = ratingTrigger.parentElement.cloneNode(false);
    sourceTrigger = ratingTrigger.cloneNode(true);
    sourceTrigger.removeAttribute('id');
    sourceTrigger.setAttribute('aria-label', 'Source');
    sourceTrigger.querySelector('.whitespace-nowrap').textContent = 'Source';
    sourceWrapper.appendChild(sourceTrigger);
    ratingTrigger.parentElement.after(sourceWrapper);
    sourceMenu = document.createElement('div');
    sourceMenu.className = 'movie-sort-menu movie-source-menu';
    sourceMenu.id = 'movie-source-menu';
    sourceMenu.setAttribute('role', 'listbox');
    sourceMenu.setAttribute('aria-label', `${label} source`);
    sourceMenu.hidden = true;
    sourceWrapper.appendChild(sourceMenu);
    sourceTrigger.setAttribute('aria-controls', sourceMenu.id);
    renderSource = () => {
      sourceMenu.replaceChildren();
      sourceTrigger.querySelector('.whitespace-nowrap').textContent =
        source === 'all' ? 'Source' : source === 'downloaded' ? 'Downloaded' : 'Not Downloaded';
      sourceTrigger.dataset.active = String(source !== 'all');
      for (const [value, title] of [['downloaded','Downloaded'],['not_downloaded','Not Downloaded']]) {
        const option = document.createElement('button');
        option.type = 'button'; option.className = 'movie-sort-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(source === value));
        option.textContent = title;
        option.onclick = () => {
          source = value; renderSource(); sourceMenu.hidden = true;
          sourceTrigger.setAttribute('aria-expanded', 'false'); update();
        };
        sourceMenu.appendChild(option);
      }
      const clear = document.createElement('button');
      clear.type = 'button'; clear.className = 'genre-clear'; clear.textContent = 'Clear source';
      clear.onclick = () => {
        source = 'all'; renderSource(); sourceMenu.hidden = true;
        sourceTrigger.setAttribute('aria-expanded', 'false'); update();
      };
      sourceMenu.appendChild(clear);
    };
    sourceTrigger.onclick = event => {
      event.preventDefault();
      for (const [popup, button] of [[menu, trigger], [yearMenu, yearTrigger], [countryMenu, countryTrigger],
        [certificationMenu, certificationTrigger], [ratingMenu, ratingTrigger], [sortMenu, sortTrigger]]) {
        if (popup && button) { popup.hidden = true; button.setAttribute('aria-expanded', 'false'); }
      }
      sourceMenu.hidden = !sourceMenu.hidden;
      sourceTrigger.setAttribute('aria-expanded', String(!sourceMenu.hidden));
    };
    renderSource();
  }
  let countryMenu;
  if (countryTrigger) {
    countryMenu = document.createElement('div');
    countryMenu.className = 'genre-menu movie-country-menu';
    countryMenu.id = 'movie-country-menu';
    countryMenu.setAttribute('role', 'listbox');
    countryMenu.setAttribute('aria-label', `${label} countries`);
    countryMenu.setAttribute('aria-multiselectable', 'true');
    countryMenu.hidden = true;
    countryTrigger.parentElement.appendChild(countryMenu);
    countryTrigger.setAttribute('aria-controls', countryMenu.id);
    const renderCountries = countries => {
      countryMenu.replaceChildren();
      if (!countries.length) {
        const message = document.createElement('p');
        message.className = 'movie-country-empty';
        message.textContent = `No ${label} countries are recorded yet.`;
        countryMenu.appendChild(message);
        return;
      }
      for (const country of countries) {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'genre-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(selectedCountries.has(country)));
        option.innerHTML = '<span class="genre-check" aria-hidden="true">✓</span><span class="genre-label"></span>';
        option.querySelector('.genre-label').textContent = country;
        option.onclick = () => {
          selectedCountries.has(country) ? selectedCountries.delete(country) : selectedCountries.add(country);
          option.setAttribute('aria-selected', String(selectedCountries.has(country)));
          update();
        };
        countryMenu.appendChild(option);
      }
      const clear = document.createElement('button');
      clear.type = 'button'; clear.className = 'genre-clear'; clear.textContent = 'Clear countries';
      clear.onclick = () => { selectedCountries.clear(); renderCountries(countries); update(); };
      countryMenu.appendChild(clear);
    };
    countryTrigger.onclick = event => {
      event.preventDefault();
      menu.hidden = true; trigger.setAttribute('aria-expanded', 'false');
      if (yearMenu) { yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false'); }
      if (sortMenu) { sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false'); }
      if (certificationMenu) { certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false'); }
      countryMenu.hidden = !countryMenu.hidden;
      countryTrigger.setAttribute('aria-expanded', String(!countryMenu.hidden));
      if (!countryMenu.hidden) {
        fetch(choicesApi+'countries').then(r => r.json()).then(data => {
          renderCountries(data.countries || []);
        }).catch(() => { countryMenu.textContent = 'Country list unavailable'; });
      }
    };
  }
  let yearMenu;
  if (yearTrigger) {
    yearMenu = document.createElement('div');
    yearMenu.className = 'movie-year-menu';
    yearMenu.id = 'movie-year-menu';
    yearMenu.hidden = true;
    yearMenu.innerHTML = '<div class="movie-year-heading">Release years</div>' +
      '<div class="movie-year-values"><output class="year-from-value"></output><span>to</span><output class="year-to-value"></output></div>' +
      '<div class="movie-year-sliders"><div class="movie-year-track"></div>' +
      '<input type="range" class="year-from-slider" aria-label="Oldest year">' +
      '<input type="range" class="year-to-slider" aria-label="Newest year"></div>' +
      '<button type="button" class="movie-year-reset">Clear years</button>';
    yearTrigger.parentElement.appendChild(yearMenu);
    yearTrigger.setAttribute('aria-controls', yearMenu.id);
    const fromSlider = yearMenu.querySelector('.year-from-slider');
    const toSlider = yearMenu.querySelector('.year-to-slider');
    const label = yearTrigger.querySelector('.whitespace-nowrap');
    const showYears = () => {
      if (oldestYear === null) return;
      const from = yearFrom ?? oldestYear, to = yearTo ?? newestYear;
      fromSlider.value = from; toSlider.value = to;
      yearMenu.querySelector('.year-from-value').textContent = from;
      yearMenu.querySelector('.year-to-value').textContent = to;
      const span = Math.max(1, newestYear - oldestYear);
      yearMenu.querySelector('.movie-year-track').style.background =
        `linear-gradient(to right,#ffffff35 0%,#ffffff35 ${(from-oldestYear)/span*100}%,#d81a28 ${(from-oldestYear)/span*100}%,#d81a28 ${(to-oldestYear)/span*100}%,#ffffff35 ${(to-oldestYear)/span*100}%)`;
      label.textContent = (from === oldestYear && to === newestYear) ? 'Years' : `${from}–${to}`;
      yearTrigger.dataset.active = label.textContent === 'Years' ? 'false' : 'true';
    };
    fromSlider.oninput = () => {
      yearFrom = Math.min(Number(fromSlider.value), Number(toSlider.value));
      showYears();
    };
    toSlider.oninput = () => {
      yearTo = Math.max(Number(toSlider.value), Number(fromSlider.value));
      showYears();
    };
    fromSlider.onchange = toSlider.onchange = () => update();
    yearMenu.querySelector('.movie-year-reset').onclick = () => {
      yearFrom = null; yearTo = null; showYears(); update();
    };
    yearTrigger.onclick = event => {
      event.preventDefault();
      menu.hidden = true; trigger.setAttribute('aria-expanded', 'false');
      if (countryMenu) { countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false'); }
      if (sortMenu) { sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false'); }
      if (certificationMenu) { certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false'); }
      yearMenu.hidden = !yearMenu.hidden;
      yearTrigger.setAttribute('aria-expanded', String(!yearMenu.hidden));
    };
    fetch(typeApi+'/year-range').then(r => r.json()).then(data => {
      if (!Number.isInteger(data.oldest) || !Number.isInteger(data.newest)) {
        yearTrigger.disabled = true; yearTrigger.title = `No dated ${plural} in the database`; return;
      }
      oldestYear = data.oldest; newestYear = data.newest;
      yearFrom = yearFrom === null ? null : Math.max(oldestYear, Math.min(yearFrom, newestYear));
      yearTo = yearTo === null ? null : Math.max(oldestYear, Math.min(yearTo, newestYear));
      if (yearFrom !== null && yearTo !== null && yearFrom > yearTo) yearTo = yearFrom;
      for (const slider of [fromSlider, toSlider]) {
        slider.min = oldestYear; slider.max = newestYear; slider.step = 1;
        slider.disabled = oldestYear === newestYear;
      }
      showYears();
      if (yearFrom !== null || yearTo !== null) update();
    }).catch(() => { yearTrigger.disabled = true; yearTrigger.title = 'Year range unavailable'; });
  }
  let sortMenu;
  if (sortTrigger) {
    sortMenu = document.createElement('div');
    sortMenu.className = 'movie-sort-menu';
    sortMenu.id = 'movie-sort-menu';
    sortMenu.setAttribute('role', 'listbox');
    sortMenu.setAttribute('aria-label', `Sort ${plural}`);
    sortMenu.hidden = true;
    sortTrigger.parentElement.appendChild(sortMenu);
    sortTrigger.setAttribute('aria-controls', sortMenu.id);
    const renderSort = () => {
      sortMenu.replaceChildren();
      sortTrigger.querySelector('.whitespace-nowrap').textContent = sortChoices[sort];
      for (const [value, label] of Object.entries(sortChoices)) {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'movie-sort-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(sort === value));
        option.textContent = label;
        option.onclick = () => {
          sort = value;
          renderSort();
          sortMenu.hidden = true;
          sortTrigger.setAttribute('aria-expanded', 'false');
          update();
        };
        sortMenu.appendChild(option);
      }
    };
    renderSort();
    sortTrigger.onclick = event => {
      event.preventDefault();
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (yearMenu) { yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false'); }
      if (countryMenu) { countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false'); }
      if (certificationMenu) { certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false'); }
      sortMenu.hidden = !sortMenu.hidden;
      sortTrigger.setAttribute('aria-expanded', String(!sortMenu.hidden));
    };
  }
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const makeOptions = () => {
    menu.replaceChildren();
    for (const genre of choices) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'genre-option';
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(selected.has(genre)));
      button.innerHTML = '<span class="genre-check" aria-hidden="true">✓</span><span class="genre-label"></span>';
      button.querySelector('.genre-label').textContent = genre;
      button.onclick = () => {
        selected.has(genre) ? selected.delete(genre) : selected.add(genre);
        button.setAttribute('aria-selected', String(selected.has(genre)));
        update();
      };
      menu.appendChild(button);
    }
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'genre-clear'; clear.textContent = 'Clear filters';
    clear.onclick = () => { selected.clear(); makeOptions(); update(); };
    menu.appendChild(clear);
  };
  let requestNumber = 0;
  async function update() {
    const url = new URL(location.href);
    url.searchParams.delete('genre');
    selected.forEach(value => url.searchParams.append('genre', value));
    url.searchParams.delete('country');
    selectedCountries.forEach(value => url.searchParams.append('country', value));
    url.searchParams.delete('certification');
    selectedCertifications.forEach(value => url.searchParams.append('certification', value));
    if (sort === 'popular') url.searchParams.delete('sort');
    else url.searchParams.set('sort', sort);
    if (yearFrom === null || yearFrom === oldestYear) url.searchParams.delete('year_from');
    else url.searchParams.set('year_from', yearFrom);
    if (yearTo === null || yearTo === newestYear) url.searchParams.delete('year_to');
    else url.searchParams.set('year_to', yearTo);
    if (ratingMin > 0) url.searchParams.set('rating_min', ratingMin.toFixed(1));
    else url.searchParams.delete('rating_min');
    if (ratingMax < 10) url.searchParams.set('rating_max', ratingMax.toFixed(1));
    else url.searchParams.delete('rating_max');
    if (source === 'all') url.searchParams.delete('source');
    else url.searchParams.set('source', source);
    history.replaceState(null, '', url);
    if (preferencesReady && account.name) {
      clearTimeout(preferencesTimer);
      preferencesTimer = setTimeout(() => fetch(profileApi, {method:'PUT', headers:{'Content-Type':'application/json','X-CSRF-Token':account.csrf}, body:JSON.stringify({genre:[...selected],country:[...selectedCountries],certification:[...selectedCertifications],sort,year_from:yearFrom,year_to:yearTo,rating_min:ratingMin,rating_max:ratingMax,source})}).catch(() => {}), 350);
    }
    trigger.dataset.active = selected.size ? 'true' : 'false';
    if (countryTrigger) countryTrigger.dataset.active = selectedCountries.size ? 'true' : 'false';
    if (certificationTrigger) certificationTrigger.dataset.active = selectedCertifications.size ? 'true' : 'false';
    const limitedYears = oldestYear !== null && (
      (yearFrom !== null && yearFrom > oldestYear) || (yearTo !== null && yearTo < newestYear));
    if (!selected.size && !selectedCountries.size && !selectedCertifications.size && sort === 'popular' && !limitedYears && ratingMin === 0 && ratingMax === 10 && source === 'all') { requestNumber++; grid.hidden = false; results.hidden = true; return; }
    grid.hidden = true; results.hidden = false;
    results.innerHTML = `<p class="genre-message">Loading ${plural}…</p>`;
    const number = ++requestNumber;
    try {
      const query = new URLSearchParams();
      selected.forEach(value => query.append('genre', value));
      selectedCountries.forEach(value => query.append('country', value));
      selectedCertifications.forEach(value => query.append('certification', value));
      query.set('sort', sort);
      if (source !== 'all') query.set('source', source);
      if (ratingMin > 0) query.set('rating_min', ratingMin.toFixed(1));
      if (ratingMax < 10) query.set('rating_max', ratingMax.toFixed(1));
      if (limitedYears) {
        query.set('year_from', yearFrom ?? oldestYear);
        query.set('year_to', yearTo ?? newestYear);
      }
      const response = await fetch(typeApi+'/by-genre?' + query);
      if (!response.ok) throw Error(`Unable to load ${plural}`);
      const data = await response.json();
      if (number !== requestNumber) return;
      if (!data.results.length) {
        results.innerHTML = '<p class="genre-message">' +
          (selected.size || selectedCountries.size || selectedCertifications.size || ratingMin > 0 || ratingMax < 10 || source !== 'all' ? `No indexed ${plural} match the selected filters yet.` : `No ${plural} found.`) + '</p>';
        return;
      }
      results.innerHTML = '<p class="genre-count">' + data.total + ` matching ${plural}</p><div class="genre-grid">` +
        data.results.map(movie => '<a class="genre-card" href="' + escape(movie.url) + '">' +
          (movie.poster ? '<img loading="lazy" src="' + escape(movie.poster) + '" alt="">' : '<span class="genre-no-poster">No poster</span>') +
          '<strong>' + escape(movie.title) + '</strong><small>' + escape(movie.year || '') +
          (sort === 'latest_episode' ? ' · ' + (movie.latest_episode ? 'Latest episode: ' + escape(movie.latest_episode) : 'Episode date unavailable') : '') +
          '</small></a>').join('') + '</div>';
    } catch (error) {
      if (number === requestNumber) results.innerHTML = '<p class="genre-message">' + escape(error.message) + '</p>';
    }
  }
  trigger.onclick = event => {
    event.preventDefault();
    if (sortMenu) { sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false'); }
    if (yearMenu) { yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false'); }
    if (countryMenu) { countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false'); }
    if (certificationMenu) { certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false'); }
    if (ratingMenu) { ratingMenu.hidden = true; ratingTrigger.setAttribute('aria-expanded', 'false'); }
    if (sourceMenu) { sourceMenu.hidden = true; sourceTrigger.setAttribute('aria-expanded', 'false'); }
    menu.hidden = !menu.hidden;
    trigger.setAttribute('aria-expanded', String(!menu.hidden));
  };
  document.addEventListener('click', event => {
    if (!wrapper.contains(event.target)) { menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }
    if (sortMenu && !sortTrigger.parentElement.contains(event.target)) {
      sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false');
    }
    if (yearMenu && !yearTrigger.parentElement.contains(event.target)) {
      yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false');
    }
    if (countryMenu && !countryTrigger.parentElement.contains(event.target)) {
      countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false');
    }
    if (ratingMenu && !ratingTrigger.parentElement.contains(event.target)) { ratingMenu.hidden = true; ratingTrigger.setAttribute('aria-expanded', 'false'); }
    if (sourceMenu && !sourceTrigger.parentElement.contains(event.target)) { sourceMenu.hidden = true; sourceTrigger.setAttribute('aria-expanded', 'false'); }
    if (certificationMenu && !certificationTrigger.parentElement.contains(event.target)) {
      certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) {
      menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); trigger.focus();
    }
    if (event.key === 'Escape' && sortMenu && !sortMenu.hidden) {
      sortMenu.hidden = true; sortTrigger.setAttribute('aria-expanded', 'false'); sortTrigger.focus();
    }
    if (event.key === 'Escape' && yearMenu && !yearMenu.hidden) {
      yearMenu.hidden = true; yearTrigger.setAttribute('aria-expanded', 'false'); yearTrigger.focus();
    }
    if (event.key === 'Escape' && countryMenu && !countryMenu.hidden) {
      countryMenu.hidden = true; countryTrigger.setAttribute('aria-expanded', 'false'); countryTrigger.focus();
    }
    if (event.key === 'Escape' && ratingMenu && !ratingMenu.hidden) { ratingMenu.hidden = true; ratingTrigger.setAttribute('aria-expanded', 'false'); ratingTrigger.focus(); }
    if (event.key === 'Escape' && sourceMenu && !sourceMenu.hidden) { sourceMenu.hidden = true; sourceTrigger.setAttribute('aria-expanded', 'false'); sourceTrigger.focus(); }
    if (event.key === 'Escape' && certificationMenu && !certificationMenu.hidden) {
      certificationMenu.hidden = true; certificationTrigger.setAttribute('aria-expanded', 'false'); certificationTrigger.focus();
    }
  });
  makeOptions();
  const useSaved = account.name && ![...initialParams.keys()].some(key => ['genre','country','certification','sort','year_from','year_to','rating_min','rating_max','source'].includes(key));
  (useSaved ? fetch(profileApi).then(r => r.json()).then(data => {
    const saved = data.filters || {};
    for (const [values, key] of [[selected,'genre'],[selectedCountries,'country'],[selectedCertifications,'certification']]) {
      values.clear(); if (Array.isArray(saved[key])) saved[key].filter(v => typeof v === 'string').slice(0,30).forEach(v => values.add(v));
    }
    if (saved.sort in sortChoices) sort = saved.sort;
    yearFrom = Number.isInteger(saved.year_from) ? saved.year_from : null;
    yearTo = Number.isInteger(saved.year_to) ? saved.year_to : null;
    ratingMin = Math.max(0, Math.min(10, Number(saved.rating_min) || 0));
    ratingMax = Math.max(ratingMin, Math.min(10, saved.rating_max == null ? 10 : Number(saved.rating_max)));
    source = ['downloaded','not_downloaded'].includes(saved.source) ? saved.source : 'all';
    renderSource();
    makeOptions();
    if (sortTrigger) sortTrigger.querySelector('.whitespace-nowrap').textContent = sortChoices[sort];
    if (certificationMenu) certificationMenu.querySelectorAll('.genre-option').forEach(option =>
      option.setAttribute('aria-selected', String(selectedCertifications.has(option.querySelector('.genre-label').textContent))));
    if (ratingTrigger) { const from = ratingMenu.querySelector('.rating-from-slider'); from.value = ratingMin; from.dispatchEvent(new Event('input')); const to = ratingMenu.querySelector('.rating-to-slider'); to.value = ratingMax; to.dispatchEvent(new Event('input')); }
    update();
  }).catch(() => {}): Promise.resolve()).finally(() => {
    preferencesReady = true;
    if (selected.size || selectedCountries.size || selectedCertifications.size || sort !== 'popular' || ratingMin > 0 || ratingMax < 10 || yearFrom !== null || yearTo !== null || source !== 'all') update();
  });
  fetch(choicesApi+'genres').then(r => r.json()).then(data => {
    for (const genre of data.genres || []) if (!choices.some(item => item.toLowerCase() === genre.toLowerCase())) choices.push(genre);
    makeOptions();
  }).catch(() => {});
})();
