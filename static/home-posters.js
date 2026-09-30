(() => {
  const posterMap = new Map();
  const titleKey = href => {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin) return '';
      const match = url.pathname.match(/^\/(movie|tv)\/(\d+)\/?$/);
      return match ? `${match[1]}/${match[2]}` : '';
    } catch (_) { return ''; }
  };
  function apply() {
    for (const anchor of document.querySelectorAll('a[href*="/movie/"],a[href*="/tv/"]')) {
      if (anchor.closest('.HomeBanner-module-scss-module__eAYNsa__slideLayer')) continue;
      const key = titleKey(anchor.href);
      if (!key) continue;
      const frame = anchor.closest('[class*="homeSlider-module"], [class*="w-[140px]"], [class*="w-[200px]"]');
      if (frame) frame.classList.add('home-poster-size-item');
      const poster = posterMap.get(key);
      if (!poster) continue;
      anchor.querySelectorAll('picture source').forEach(source => { source.srcset = poster; });
      for (const image of anchor.querySelectorAll('img')) {
        if (image.dataset.homePoster === poster) continue;
        image.src = poster;
        image.removeAttribute('srcset');
        image.removeAttribute('data-savepage-src');
        image.dataset.homePoster = poster;
        image.classList.add('home-title-poster');
      }
      anchor.classList.add('home-poster-tile');
    }
  }
  fetch('/api/home-posters').then(r => r.json()).then(data => {
    for (const item of data.results || []) if (item.poster) posterMap.set(`${item.type}/${item.id}`, item.poster);
    apply();
    let scheduled = false;
    new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; apply(); });
    }).observe(document.body,{childList:true,subtree:true});
  }).catch(() => {});
})();
