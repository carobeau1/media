(() => {
  const main = document.querySelector('main:has(.HomeBanner-module-scss-module__eAYNsa__heroRoot)');
  const hero = main?.querySelector('.HomeBanner-module-scss-module__eAYNsa__heroRoot');
  if (!main || !hero) return;
  const rows = document.createElement('div');
  rows.className = 'fullscreen-panel-content';
  const original = [...main.children];
  for (const child of original) {
    if (child === hero) continue;
    if (child.contains(hero)) {
      hero.remove();
      // Keep the rest of the original layout, including any title rows.
    }
    rows.append(child);
  }
  hero.classList.add('fullscreen-panel-hero');
  main.replaceChildren(hero, rows);
  main.classList.add('fullscreen-panels');
  const button = (name, label, parent, action) => {
    const el=document.createElement('button');el.type='button';el.className=name;
    el.innerHTML=name.endsWith('down')?'&#8964;':'&#8963;';el.setAttribute('aria-label',label);
    el.addEventListener('click',action);parent.append(el);return el;
  };
  const cards=()=>[...rows.querySelectorAll('a[href^="/movie/"],a[href^="/tv/"]')]
    .filter(a=>{const r=a.getBoundingClientRect();return r.width>20&&r.height>20&&getComputedStyle(a).visibility!=='hidden'});
  const showRows=()=>{main.classList.add('panel-rows');rows.scrollTop=0;setTimeout(()=>cards()[0]?.focus({preventScroll:true}),50)};
  const showHero=()=>{main.classList.remove('panel-rows');hero.focus?.({preventScroll:true})};
  button('fullscreen-panel-down','Show title rows',hero,showRows);
  button('fullscreen-panel-up','Back to featured titles',rows,showHero);
  document.addEventListener('keydown',e=>{
    if(e.altKey||e.ctrlKey||e.metaKey||e.target.closest?.('input,textarea,select,[contenteditable="true"],[role="menu"],[role="listbox"],.backend-search-shell.is-open,.movie-player-overlay:not([hidden]),.download-queue-drawer.is-open'))return;
    if(!main.classList.contains('panel-rows')){
      if(e.key==='ArrowDown'){e.preventDefault();e.stopImmediatePropagation();showRows()}
      else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
        const nav=[...hero.querySelectorAll('.hero-dynamic-nav button')];
        if(nav.length){e.preventDefault();e.stopImmediatePropagation();const index=Math.max(0,nav.findIndex(b=>b.getAttribute('aria-selected')==='true'));nav[(index+(e.key==='ArrowRight'?1:-1)+nav.length)%nav.length].click()}
      }
    }else if(e.key==='ArrowUp' && (rows.scrollTop<8 || e.target.classList?.contains('fullscreen-panel-up'))){
      const visible=cards();const top=visible.length?Math.min(...visible.map(a=>a.getBoundingClientRect().top)):Infinity;
      const selected=e.target.closest?.('a[href^="/movie/"],a[href^="/tv/"]');
      if(!selected||selected.getBoundingClientRect().top<=top+24){e.preventDefault();e.stopImmediatePropagation();showHero()}
    }
  },true);
  main.addEventListener('wheel',e=>{
    if(!main.classList.contains('panel-rows')&&e.deltaY>0){e.preventDefault();showRows()}
    else if(main.classList.contains('panel-rows')&&e.deltaY<0&&rows.scrollTop<2){e.preventDefault();showHero()}
  },{passive:false});
})();
