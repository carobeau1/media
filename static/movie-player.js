(() => {
  const match = location.pathname.match(/^\/movie\/(\d+)\/?$/);
  if (!match) return;
  let url = (window.__movieSources?.sources || []).find(item => item.name === window.__movieSources.selected)?.cloud_url || null;
  window.addEventListener('movie-source-changed', event => { url = event.detail?.cloud_url || null; });
  const play = [...document.querySelectorAll('button')].find(button =>
    [...button.querySelectorAll('span')].some(span => span.textContent.trim() === 'Play'));
  if (!play) return;

  const overlay = document.createElement('div');
  overlay.className = 'movie-player-overlay';
  overlay.hidden = true;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Movie player');
  overlay.innerHTML = '<div class="movie-player-shell">' +
    '<svg class="movie-player-filters" aria-hidden="true" width="0" height="0" xmlns="http://www.w3.org/2000/svg"><defs>' +
    '<filter id="mediaflix-sharp-1" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB"><feConvolveMatrix order="3" kernelMatrix="0 -0.18 0 -0.18 1.72 -0.18 0 -0.18 0" preserveAlpha="true" edgeMode="duplicate"/></filter>' +
    '<filter id="mediaflix-sharp-2" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB"><feConvolveMatrix order="3" kernelMatrix="0 -0.35 0 -0.35 2.4 -0.35 0 -0.35 0" preserveAlpha="true" edgeMode="duplicate"/></filter>' +
    '<filter id="mediaflix-sharp-3" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB"><feConvolveMatrix order="3" kernelMatrix="0 -0.55 0 -0.55 3.2 -0.55 0 -0.55 0" preserveAlpha="true" edgeMode="duplicate"/></filter>' +
    '</defs></svg>' +
    '<div class="movie-player-toolbar"><button type="button" class="movie-player-options-button" aria-label="Video options" aria-expanded="false" aria-controls="movie-player-options" title="Video options">⚙</button>' +
    '<button type="button" class="movie-player-close-button" aria-label="Close player">✕</button></div>' +
    '<div class="movie-player-options" id="movie-player-options" hidden><label for="movie-player-sharpen">Sharpen <output id="movie-player-sharpen-value">Off</output></label>' +
    '<input id="movie-player-sharpen" type="range" min="0" max="3" step="1" value="0" aria-label="Sharpen video" aria-valuetext="Off">' +
    '<div class="movie-player-options-scale" aria-hidden="true"><span>Off</span><span>Low</span><span>Medium</span><span>High</span></div></div>' +
    '<div class="movie-player-viewport"><video controls autoplay playsinline aria-label="Movie player" hidden></video><iframe title="Movie player" width="100%" height="100%" frameborder="0" allowfullscreen allow="encrypted-media; autoplay *; fullscreen *" hidden></iframe></div></div>';
  document.body.appendChild(overlay);
  const frame = overlay.querySelector('iframe');
  const video = overlay.querySelector('video');
  const sourceMenu = document.createElement('div');
  sourceMenu.className = 'movie-player-source-menu';
  sourceMenu.hidden = true;
  sourceMenu.setAttribute('role','dialog');
  sourceMenu.setAttribute('aria-modal','true');
  sourceMenu.setAttribute('aria-label','Choose movie playback source');
  sourceMenu.innerHTML = '<div class="movie-player-source-card"><h2>Play movie</h2><p data-source-message>Checking local file…</p><button type="button" data-source="local" disabled>Play Local</button><button type="button" data-source="cloud">Play Cloud</button><button type="button" data-source="cancel">Cancel</button></div>';
  document.body.appendChild(sourceMenu);
  video.preload = 'auto';
  const closeButton = overlay.querySelector('.movie-player-close-button');
  const optionsButton = overlay.querySelector('.movie-player-options-button');
  const optionsPanel = overlay.querySelector('.movie-player-options');
  const sharpenSlider = overlay.querySelector('#movie-player-sharpen');
  const sharpenValue = overlay.querySelector('#movie-player-sharpen-value');
  const sharpenLevels = ['Off', 'Low', 'Medium', 'High'];
  const setOptionsOpen = open => {
    optionsPanel.hidden = !open;
    optionsButton.setAttribute('aria-expanded', String(open));
    if (open) sharpenSlider.focus();
  };
  const applySharpen = level => {
    const chosen = Math.max(0, Math.min(3, Math.round(Number(level) || 0)));
    sharpenSlider.value = String(chosen);
    sharpenValue.textContent = sharpenLevels[chosen];
    sharpenSlider.setAttribute('aria-valuetext', sharpenLevels[chosen]);
    for (const element of [video, frame]) {
      element.style.filter = chosen ? `url("#mediaflix-sharp-${chosen}")` : '';
    }
    try { localStorage.setItem('mediaflix-sharpen', String(chosen)); } catch (_) {}
  };
  try { applySharpen(localStorage.getItem('mediaflix-sharpen')); }
  catch (_) { applySharpen(0); }
  sharpenSlider.addEventListener('input', () => applySharpen(sharpenSlider.value));
  optionsButton.addEventListener('click', () => setOptionsOpen(optionsPanel.hidden));
  overlay.addEventListener('click', event => {
    if (!optionsPanel.hidden && !optionsPanel.contains(event.target) && !optionsButton.contains(event.target)) {
      setOptionsOpen(false);
    }
  });
  let previousOverflow = '';
  let playbackInfo = null;
  let playbackChecked = false;
  let sourceTimer = null;
  let sourceCountdown = null;
  const localButton = sourceMenu.querySelector('[data-source="local"]');
  const cloudButton = sourceMenu.querySelector('[data-source="cloud"]');
  cloudButton.disabled = !url;
  window.addEventListener('movie-source-changed', () => { cloudButton.disabled = !url; updateSource(); });
  const sourceMessage = sourceMenu.querySelector('[data-source-message]');
  const clearSourceTimer = () => {
    clearTimeout(sourceTimer);
    clearInterval(sourceCountdown);
    sourceTimer = sourceCountdown = null;
  };
  const dismissSource = () => { clearSourceTimer(); sourceMenu.hidden = true; };
  const updateSource = () => {
    if (sourceMenu.hidden) return;
    localButton.disabled = !playbackInfo;
    if (!playbackChecked) {
      sourceMessage.textContent = 'Checking local file…';
      return;
    }
    if (!playbackInfo) {
      sourceMessage.textContent = url ? 'Local file unavailable. You can play from Cloud.' : 'No playback source is configured.';
      return;
    }
    clearSourceTimer();
    const deadline = Date.now() + 5000;
    const countdown = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      sourceMessage.textContent = `Playing Local automatically in ${seconds} seconds…`;
    };
    countdown();
    sourceCountdown = setInterval(countdown, 250);
    sourceTimer = setTimeout(() => {
      if (!sourceMenu.hidden && playbackInfo) start('local', playbackInfo);
    }, 5000);
    localButton.focus();
  };
  // Resolve local availability while the detail page is idle, before Play is clicked.
  const playbackReady = fetch(`/api/movies/${match[1]}/playback`, {cache:'no-store'})
    .then(response => response.ok ? response.json() : null)
    .then(info => { playbackChecked = true; playbackInfo = info; updateSource(); return info; })
    .catch(() => { playbackChecked = true; updateSource(); return null; });
  const close = () => {
    dismissSource();
    setOptionsOpen(false);
    if (overlay.hidden) return;
    overlay.hidden = true;
    frame.removeAttribute('src'); // stop playback on close
    video.pause();
    video.removeAttribute('src');
    video.replaceChildren();
    video.load();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    document.body.style.overflow = previousOverflow;
    play.focus();
  };
  function start(source, local) {
    dismissSource();
    setOptionsOpen(false);
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    overlay.hidden = false;
    window.dispatchEvent(new CustomEvent('media-played', {detail:{type:'movie',id:match[1]}}));
    // Request fullscreen while the Play click still supplies user activation.
    // The overlay fills the viewport if fullscreen is unavailable or declined.
    if (overlay.requestFullscreen) overlay.requestFullscreen().catch(() => {});
    if (source === 'local' && local) {
        frame.hidden = true;
        video.hidden = false;
        video.src = local.video;
        if (local.subtitles) {
          const track = document.createElement('track');
          track.kind = 'subtitles'; track.label = 'Subtitles'; track.srclang = 'en';
          track.src = local.subtitles; track.default = true;
          video.append(track);
        }
        video.play().catch(() => {});
    } else {
        video.hidden = true;
        frame.hidden = false;
        frame.src = url;
    }
    closeButton.focus();
  }
  let choosingPlayback = false;
  play.addEventListener('click', async event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (choosingPlayback) return;
    choosingPlayback = true;
    try {
      if (!playbackChecked) await playbackReady;
      if (!playbackInfo) {
        if (url) start('cloud', null);
        else alert('No cloud playback source is configured.');
        return;
      }
      sourceMenu.hidden = false;
      updateSource();
    } finally { choosingPlayback = false; }
  }, true);
  sourceMenu.addEventListener('click', event => {
    const choice = event.target.closest('button[data-source]');
    if (!choice) {
      if (event.target === sourceMenu) dismissSource();
      return;
    }
    if (choice.dataset.source === 'cancel') {dismissSource();play.focus();return;}
    if (choice.dataset.source === 'local' && !playbackInfo) return;
    if (choice.dataset.source === 'cloud' && !url) return;
    start(choice.dataset.source, playbackInfo);
  });
  closeButton.addEventListener('click', close);
  video.addEventListener('ended', close);
  if (new URLSearchParams(location.search).get('play') === 'true') {
    history.replaceState(null, '', location.pathname);
    play.click();
  }
  overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (!sourceMenu.hidden) {dismissSource();play.focus();}
      else if (!overlay.hidden && !optionsPanel.hidden) {setOptionsOpen(false);optionsButton.focus();event.stopPropagation();}
      else close();
    }
  });
  document.addEventListener('fullscreenchange', () => {
    if (!overlay.hidden && !document.fullscreenElement) close();
  });
  const signalsEnded = value => {
    if (typeof value === 'string') {
      try { value = JSON.parse(value); }
      catch (_) { return /^(?:video[.:_-]?)?ended$/i.test(value.trim()); }
    }
    if (!value || typeof value !== 'object') return false;
    const signal = value.event || value.type || value.action || value.name || value.state;
    if (typeof signal === 'string' && /^(?:video[.:_-]?|player[.:_-]?)?ended$/i.test(signal.trim())) return true;
    return value.data && value.data !== value && signalsEnded(value.data);
  };
  window.addEventListener('message', event => {
    if (overlay.hidden || !url || event.origin !== new URL(url).origin ||
        event.source !== frame.contentWindow) return;
    if (signalsEnded(event.data)) close();
  });
  // This is available only if the iframe happens to use this page's origin.
  frame.addEventListener('load', () => {
    try {
      const doc = frame.contentDocument;
      if (!doc) return;
      const attach = () => doc.querySelectorAll('video').forEach(video => {
        if (video.dataset.movyEndListener) return;
        video.dataset.movyEndListener = 'true';
        video.addEventListener('ended', close);
      });
      attach();
      const observer = new MutationObserver(attach);
      observer.observe(doc, {childList: true, subtree: true});
      frame.addEventListener('load', () => observer.disconnect(), {once: true});
    } catch (_) { /* cross-origin playback is reported through postMessage */ }
  });
})();
