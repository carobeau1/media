(() => {
  fetch('/api/profile/settings').then(response => response.ok ? response.json() : null)
    .then(data => {
      if (Number.isInteger(data?.home_tile_size) && data.home_tile_size >= 120 && data.home_tile_size <= 320) {
        document.documentElement.style.setProperty('--home-poster-width', `${data.home_tile_size}px`);
      }
    }).catch(() => {});
})();
