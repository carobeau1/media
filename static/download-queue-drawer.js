(() => {
  const profile = document.querySelector('header .profile-trigger, .profile-trigger');
  if (!profile) return;
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'download-queue-nav-button';
  trigger.setAttribute('aria-label', 'Download queue');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'download-queue-drawer');
  trigger.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span class="download-queue-nav-count" hidden></span>';
  profile.before(trigger);
  const scrim = document.createElement('div');
  scrim.className = 'download-queue-scrim';
  scrim.hidden = true;
  const drawer = document.createElement('aside');
  drawer.className = 'download-queue-drawer';
  drawer.id = 'download-queue-drawer';
  drawer.setAttribute('role','dialog');
  drawer.setAttribute('aria-modal','true');
  drawer.setAttribute('aria-label','Download queue');
  drawer.setAttribute('aria-hidden','true');
  drawer.innerHTML = '<div class="download-queue-drawer-header"><div><h2>Download queue</h2><p class="download-queue-summary" role="status"></p></div><button type="button" class="download-queue-close" aria-label="Close download queue">✕</button></div><div class="download-queue-drawer-controls"><button type="button" class="download-queue-pause" aria-pressed="false">Pause queue</button><span>Current downloads finish before the queue pauses.</span></div><div class="download-queue-drawer-list"></div>';
  document.body.append(scrim,drawer);
  const list = drawer.querySelector('.download-queue-drawer-list');
  const summary = drawer.querySelector('.download-queue-summary');
  const count = trigger.querySelector('.download-queue-nav-count');
  const pauseButton = drawer.querySelector('.download-queue-pause');
  let paused = false;
  const showPaused = value => {
    paused = value;
    pauseButton.textContent = paused ? 'Resume queue' : 'Pause queue';
    pauseButton.setAttribute('aria-pressed',String(paused));
    trigger.classList.toggle('is-paused',paused);
    trigger.title = paused ? 'Download queue paused' : 'Download queue';
  };
  const size = bytes => Number.isFinite(bytes) && bytes >= 0 ?
    (bytes >= 1073741824 ? (bytes / 1073741824).toFixed(2) + ' GB' :
      bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' :
      (bytes / 1024).toFixed(0) + ' KB') : 'Size pending';
  const time = seconds => {
    if (!Number.isFinite(seconds)) return 'Calculating…';
    const n = Math.max(0,Math.floor(seconds));
    return n >= 3600 ? `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m` : `${Math.floor(n / 60)}m ${n % 60}s`;
  };
  let open = false;
  const close = () => {
    if (!open) return;
    open = false;
    trigger.setAttribute('aria-expanded','false');
    drawer.classList.remove('is-open');
    drawer.setAttribute('aria-hidden','true');
    scrim.hidden = true;
    trigger.focus();
  };
  const openDrawer = () => {
    open = true;
    trigger.setAttribute('aria-expanded','true');
    drawer.classList.add('is-open');
    drawer.setAttribute('aria-hidden','false');
    scrim.hidden = false;
    drawer.querySelector('.download-queue-close').focus();
    refresh(true);
  };
  trigger.addEventListener('click',()=>open?close():openDrawer());
  scrim.addEventListener('click',close);
  drawer.querySelector('.download-queue-close').addEventListener('click',close);
  document.addEventListener('keydown',event=>{
    if (!open) return;
    if (event.key==='Escape') {event.preventDefault();close();}
    if (event.key==='Tab') {
      const items=[...drawer.querySelectorAll('button:not([disabled]),a[href]')];
      const index=items.indexOf(document.activeElement);
      if (event.shiftKey && index <= 0) {event.preventDefault();items.at(-1)?.focus();}
      else if (!event.shiftKey && index===items.length-1) {event.preventDefault();items[0]?.focus();}
    }
  });
  drawer.addEventListener('click',async event=>{
    const button=event.target.closest('button[data-action]');
    if (!button) return;
    button.disabled=true;
    try {
      const response=await fetch('/api/downloads/queue',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({key:button.dataset.key,action:button.dataset.action})});
      const result=await response.json();
      if (!response.ok) throw Error(result.error||'Could not change the queue.');
      await refresh(true);
    } catch(error) {summary.textContent=error.message;button.disabled=false;}
  });
  pauseButton.addEventListener('click',async()=>{
    pauseButton.disabled=true;
    try {
      const response=await fetch('/api/downloads/pause',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({paused:!paused})});
      const result=await response.json();
      if (!response.ok) throw Error(result.error||'Could not update the download queue.');
      showPaused(result.paused);
      await refresh(true);
    } catch(error) {summary.textContent=error.message;}
    finally{pauseButton.disabled=false;}
  });
  let pending=false;
  async function refresh(force=false) {
    if (pending || (!open && !force)) return;
    pending=true;
    try {
      const response=await fetch('/api/downloads/status?details=1',{cache:'no-store'});
      if (!response.ok) throw Error('Could not load download queue');
      const payload=await response.json();
      showPaused(Boolean(payload.paused));
      const jobs=payload.jobs || [];
      const active=jobs.filter(item=>['running','queued','complete','error'].includes(item.state));
      const queued=active.filter(item=>item.state==='queued');
      const running=active.filter(item=>item.state==='running');
      count.hidden=!running.length&&!queued.length;
      count.textContent=String(running.length+queued.length);
      summary.textContent=`${paused?'Paused · ':''}${running.length} downloading · ${queued.length} waiting`;
      list.replaceChildren();
      if (!active.length) {list.textContent='Nothing in the download queue.';return;}
      for(const job of active) {
        const row=document.createElement('article');row.className='download-queue-drawer-item';
        const poster=document.createElement('div');poster.className='download-queue-drawer-poster';
        if (job.poster) {const image=document.createElement('img');image.src=job.poster;image.alt='';image.loading='lazy';poster.append(image);}
        else poster.textContent='No poster';
        const content=document.createElement('div');content.className='download-queue-drawer-content';
        const title=document.createElement('a');title.className='download-queue-drawer-title';
        title.href=`/${job.kind==='tv'?'tv':'movie'}/${encodeURIComponent(job.id)}`;
        const episode=job.kind==='tv'&&job.season!=null?` · S${String(job.season).padStart(2,'0')}E${String(job.episode).padStart(2,'0')}`:'';
        title.textContent=(job.title||'Untitled')+episode;
        const meta=document.createElement('div');meta.className='download-queue-drawer-meta';
        meta.textContent=[job.year,job.rating?`★ ${job.rating}`:'',...(job.genres||[]).slice(0,3)].filter(Boolean).join(' · ');
        const progress=document.createElement('div');progress.className='download-queue-drawer-progress';
        const percent=typeof job.progress==='number'?Math.max(0,Math.min(100,job.progress)):null;
        progress.textContent=job.state==='queued'?`${paused?'Paused':'Waiting'} · #${job.queue_position}`:
          job.state==='complete'?`Downloaded · ${size(job.total_bytes)}`:
          job.state==='error'?'Download failed':
          `${percent==null?'Preparing':percent.toFixed(1)+'%'} · ${job.total_bytes?`${size(job.downloaded_bytes)} / ${size(job.total_bytes)}`:'Size pending'} · ${time(job.elapsed_seconds)} elapsed · ETA ${time(job.eta_seconds)}`;
        const track=document.createElement('div');track.className='download-queue-drawer-track';
        const fill=document.createElement('span');fill.style.width=(percent||0)+'%';track.append(fill);
        content.append(title,meta,progress,track);
        if(job.state==='running') {
          const controls=document.createElement('div');controls.className='download-queue-drawer-actions';
          const stop=document.createElement('button');stop.type='button';
          stop.textContent='Stop & remove';stop.setAttribute('aria-label',`Stop and remove download: ${job.title || 'Untitled'}`);
          stop.dataset.action='stop';stop.dataset.key=job.key;controls.append(stop);
          content.append(controls);
        }
        if(job.state==='queued') {
          const controls=document.createElement('div');controls.className='download-queue-drawer-actions';
          for(const [action,label,disabled] of [['up','Move up',job.queue_position===1],['down','Move down',job.queue_position===queued.length],['remove','Remove',false]]){
            const button=document.createElement('button');button.type='button';button.textContent=label;button.dataset.action=action;
            button.dataset.key=job.key;button.disabled=disabled;controls.append(button);
          }
          content.append(controls);
        }
        row.append(poster,content);list.append(row);
      }
    }catch(error){summary.textContent=error.message;}
    finally{pending=false;}
  }
  setInterval(()=>refresh(),2500);
  // Keep the count current even when the drawer is closed.
  setInterval(async()=>{
    if(open)return;
    try{const response=await fetch('/api/downloads/status',{cache:'no-store'});if(!response.ok)return;
      const payload=await response.json();showPaused(Boolean(payload.paused));
      const jobs=payload.jobs||[];
      const n=jobs.filter(item=>item.state==='running'||item.state==='queued').length;
      count.hidden=!n;count.textContent=String(n);
    }catch(_){}
  },10000);
})();
