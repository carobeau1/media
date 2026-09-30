// Navigate title and episode cards by their visible rows without wrapping.
(() => {
  const tileSelector = 'a[href^="/movie/"],a[href^="/tv/"],a[href^="/person/"]';
  const movieDetail = /^\/movie\/\d+\/?$/.test(location.pathname);
  let current = null;
  const cards = () => [...document.querySelectorAll(tileSelector)].filter(link => {
    if (link.matches('a[href^="/person/"]')) {
      if (!movieDetail || !link.closest('[class*="ScrollRow-module"][class*="track"]')) return false;
    } else if (!link.matches('.genre-card,.recently-played-card,.home-poster-tile,.favourite-tile') &&
        !link.closest('.media-card,.movieGrid-module-scss-module__NJm1Na__movieGrid,#episodes-section')) return false;
    if (link.closest('[aria-hidden="true"],.backend-search-shell:not(.is-open)')) return false;
    const rect = link.getBoundingClientRect();
    return rect.width > 20 && rect.height > 20 && getComputedStyle(link).visibility !== 'hidden';
  });
  // Layout offsets remain stable while the selected card scales and bounces.
  const layoutBox = link => {
    let top = 0, left = 0;
    for (let node = link; node; node = node.offsetParent) {
      top += node.offsetTop || 0;
      left += node.offsetLeft || 0;
    }
    return {top, left, width:link.offsetWidth};
  };
  const rows = links => {
    const result = [];
    for (const link of links.sort((a,b) => layoutBox(a).top - layoutBox(b).top || layoutBox(a).left - layoutBox(b).left)) {
      const top = layoutBox(link).top;
      const row = result.find(group => Math.abs(group.top - top) < 24);
      if (row) row.links.push(link);
      else result.push({top, links:[link]});
    }
    result.forEach(row => row.links.sort((a,b) => layoutBox(a).left - layoutBox(b).left));
    return result;
  };
  const activate = link => {
    if (!link) return;
    if (current) current.classList.remove('keyboard-tile-active');
    current = link;
    link.classList.add('keyboard-tile-active');
    link.focus({preventScroll:true});
    link.scrollIntoView({behavior:'smooth',block:'nearest',inline:'nearest'});
  };
  document.addEventListener('focusin', event => {
    const link = event.target.closest?.(tileSelector);
    if (link && cards().includes(link)) {
      if (current) current.classList.remove('keyboard-tile-active');
      current = link;
      link.classList.add('keyboard-tile-active');
    }
  });
  document.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Enter'].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target;
    if (target.closest?.('input,textarea,select,[contenteditable="true"],[role="listbox"],[role="menu"],.backend-search-shell.is-open,.movie-player-overlay:not([hidden]),.movie-player-source-menu:not([hidden]),.download-queue-drawer.is-open')) return;
    if (target.closest?.('button') && event.key === 'Enter') return;
    const groups = rows(cards());
    if (!groups.length) return;
    const focused = target.closest?.(tileSelector);
    const selected = groups.flatMap(row => row.links).includes(focused) ? focused :
      groups.flatMap(row => row.links).includes(current) ? current : null;
    if (event.key === 'Enter') {
      if (selected) { event.preventDefault(); selected.click(); }
      return;
    }
    if (!selected) { event.preventDefault();activate(groups[0].links[0]);return; }
    const rowIndex = groups.findIndex(row => row.links.includes(selected));
    const row = groups[rowIndex];
    const index = row.links.indexOf(selected);
    let next;
    if (event.key === 'ArrowLeft') next = row.links[index - 1];
    else if (event.key === 'ArrowRight') next = row.links[index + 1];
    else {
      const adjacent = groups[rowIndex + (event.key === 'ArrowDown' ? 1 : -1)];
      if (adjacent) {
        const selectedBox = layoutBox(selected);
        const center = selectedBox.left + selectedBox.width / 2;
        next = adjacent.links.reduce((best, link) => {
          const rect = layoutBox(link);
          const distance = Math.abs(rect.left + rect.width / 2 - center);
          return !best || distance < best.distance ? {link,distance} : best;
        }, null)?.link;
      }
    }
    event.preventDefault();
    if (next) activate(next);
  }, true);
})();
