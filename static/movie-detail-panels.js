(() => {
  const hero = document.querySelector('.page-enter > .relative.w-full > .HomeBanner-module-scss-module__eAYNsa__heroRoot');
  if (!hero) return;
  const container = hero.parentElement;
  const content = hero.nextElementSibling?.classList.contains('layout-container') ? hero.nextElementSibling : null;
  if (!content) return;
  document.body.classList.remove('movie-detail-grid-mode');
  container.classList.add('movie-detail-panels');
  content.classList.add('movie-detail-content');
  content.hidden = true;
  content.inert = true;
  const down = document.createElement('button');
  down.type = 'button';down.className = 'movie-detail-down';down.innerHTML = '&#8964;';
  down.setAttribute('aria-label', 'Show cast and recommended titles');
  hero.append(down);
  const up = document.createElement('button');
  up.type = 'button';up.className = 'movie-detail-up';up.innerHTML = '&#8963;';
  up.setAttribute('aria-label', 'Back to movie banner');
  content.prepend(up);
  const openGrid = () => {
    if (document.body.classList.contains('movie-detail-grid-mode')) return;
    content.hidden = false;
    content.inert = false;
    document.body.classList.add('movie-detail-grid-mode');
    down.blur();
    setTimeout(() => content.querySelector('a[href^="/person/"], #you-may-like a[href^="/movie/"]')?.focus({preventScroll:true}), 50);
  };
  const openHero = () => {
    if (!document.body.classList.contains('movie-detail-grid-mode')) return;
    document.body.classList.remove('movie-detail-grid-mode');
    content.hidden = true;
    content.inert = true;
    content.scrollTop=0;
    down.focus({preventScroll:true});
  };
  down.addEventListener('click', openGrid);
  up.addEventListener('click', openHero);
  const sourceData = window.__movieSources || {sources:[], selected:''};
  if (sourceData.sources.length) {
    const panel = document.createElement('div');
    panel.className = 'movie-source-panel';
    const label = document.createElement('label');
    label.textContent = 'Movie source';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Movie source');
    for (const source of sourceData.sources) {
      const option = document.createElement('option');
      option.value = source.name;
      option.textContent = source.name;
      select.append(option);
    }
    select.value = sourceData.selected;
    label.append(select);
    const link = document.createElement('a');
    link.className = 'movie-source-url';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    const render = () => {
      const source = sourceData.sources.find(item => item.name === select.value);
      link.textContent = source?.page_url || 'No movie URL configured';
      link.href = source?.page_url || '#';
      window.dispatchEvent(new CustomEvent('movie-source-changed', {detail:source}));
    };
    select.addEventListener('change', async () => {
      const previous = sourceData.selected;
      const selected = select.value;
      render();
      try {
        const response = await fetch('/api/profile/movie-source', {
          method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':window.__account?.csrf || ''},
          body:JSON.stringify({source:selected})
        });
        if (!response.ok) throw new Error('Could not save movie source');
        sourceData.selected = selected;
      } catch (error) {
        select.value = previous; render();
        link.title = error.message;
      }
    });
    panel.append(label, link);
    hero.append(panel);
    render();
  }
  const firstRow = target => {
    const links = [...content.querySelectorAll('a[href^="/person/"], #you-may-like a[href^="/movie/"]')]
      .filter(link => {const box=link.getBoundingClientRect();return box.width>20 && box.height>20 && getComputedStyle(link).visibility!=='hidden';});
    const focused=target.closest?.('a[href^="/person/"],a[href^="/movie/"]');
    if (!focused || !links.includes(focused)) return false;
    const top=Math.min(...links.map(link=>link.getBoundingClientRect().top));
    return focused.getBoundingClientRect().top<=top+24;
  };
  window.addEventListener('keydown',event=>{
    if (event.altKey||event.ctrlKey||event.metaKey||
        event.target.closest?.('input,textarea,select,[contenteditable="true"],.backend-search-shell.is-open,.movie-player-overlay:not([hidden]),.movie-player-source-menu:not([hidden]),[role="menu"],[role="listbox"]')) return;
    if (!document.body.classList.contains('movie-detail-grid-mode') && event.key==='ArrowDown') {
      event.preventDefault();event.stopImmediatePropagation();openGrid();
    } else if (document.body.classList.contains('movie-detail-grid-mode') && event.key==='ArrowUp' && (content.scrollTop < 8 && (!event.target.closest?.('a[href^="/person/"],a[href^="/movie/"]') || firstRow(event.target)))) {
      event.preventDefault();event.stopImmediatePropagation();openHero();
    }
  },true);
  let touch=null;
  window.addEventListener('touchstart',event=>{
    if (event.touches.length!==1 || event.target.closest?.('input,textarea,select,.movie-player-overlay:not([hidden]),.backend-search-shell.is-open')) {touch=null;return;}
    const onHero=!document.body.classList.contains('movie-detail-grid-mode') && hero.contains(event.target);
    const onGrid=document.body.classList.contains('movie-detail-grid-mode') && content.scrollTop<=12 && content.contains(event.target);
    touch=onHero||onGrid?{x:event.touches[0].clientX,y:event.touches[0].clientY,onHero}:null;
  },{passive:true});
  window.addEventListener('touchmove',event=>{
    if (!touch||!event.cancelable||event.touches.length!==1)return;
    const dx=event.touches[0].clientX-touch.x,dy=event.touches[0].clientY-touch.y;
    if(touch.onHero&&Math.abs(dy)>18&&Math.abs(dy)>Math.abs(dx)*1.2)event.preventDefault();
    else if(!touch.onHero&&dy>18&&Math.abs(dy)>Math.abs(dx)*1.2&&content.scrollTop<=12)event.preventDefault();
  },{passive:false});
  window.addEventListener('touchend',event=>{
    const start=touch;touch=null;if(!start||event.changedTouches.length!==1)return;
    const dx=event.changedTouches[0].clientX-start.x,dy=event.changedTouches[0].clientY-start.y;
    if(Math.abs(dy)<60||Math.abs(dy)<Math.abs(dx)*1.2)return;
    if(start.onHero&&dy<0)openGrid();
    else if(!start.onHero&&dy>0&&content.scrollTop<=12)openHero();
  },{passive:true});
  window.addEventListener('touchcancel',()=>{touch=null},{passive:true});
})();
