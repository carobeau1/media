(() => {
  const main = document.querySelector('main.shell');
  if (!main) return;
  const section = document.createElement('section');
  section.className = 'hero-settings';
  section.innerHTML = '<h2>Hero sliders</h2><p>Choose which titles can appear in each banner. Leave years blank or genres unticked to include all.</p><div class="hero-display-settings"><label for="hero-transition">Transition <select id="hero-transition"><option value="slide">Slide</option><option value="fade">Fade</option><option value="dissolve">Dissolve</option></select></label><label for="hero-slide-count">Slides <output id="hero-slide-count-value">5</output><input id="hero-slide-count" type="range" min="5" max="20" step="1" value="5"></label><label for="hero-slide-timeout">Time per slide <output id="hero-slide-timeout-value">5 seconds</output><input id="hero-slide-timeout" type="range" min="2" max="30" step="1" value="5"></label></div><div class="hero-settings-cards"></div><button type="button" class="hero-settings-save">Save hero settings</button><div class="hero-settings-status" role="status" aria-live="polite"></div>';
  main.append(section);
  const cards = section.querySelector('.hero-settings-cards');
  const status = section.querySelector('.hero-settings-status');
  const save = section.querySelector('.hero-settings-save');
  const transition = section.querySelector('#hero-transition');
  const count = section.querySelector('#hero-slide-count');
  const timeout = section.querySelector('#hero-slide-timeout');
  const showDisplay = () => {
    section.querySelector('#hero-slide-count-value').textContent = count.value;
    section.querySelector('#hero-slide-timeout-value').textContent = timeout.value + (timeout.value === '1' ? ' second' : ' seconds');
  };
  count.addEventListener('input',showDisplay);
  timeout.addEventListener('input',showDisplay);
  showDisplay();
  const pages = [['home','Home · Movies','movie'],['movies','Movies page','movie'],['tv','TV page','tv']];
  const controls = new Map();
  const select = (label, values) => {
    const wrap = document.createElement('label');
    wrap.textContent = label;
    const input = document.createElement('select');
    for (const [value,name] of values) {
      const option = document.createElement('option');option.value=value;option.textContent=name;input.append(option);
    }
    wrap.append(input);return [wrap,input];
  };
  const number = (label,min,max,step,placeholder) => {
    const wrap = document.createElement('label');wrap.textContent=label;
    const input=document.createElement('input');input.type='number';input.min=min;input.max=max;
    input.step=step;input.placeholder=placeholder;wrap.append(input);return [wrap,input];
  };
  for (const [key,title] of pages) {
    const card=document.createElement('div');card.className='hero-settings-card';
    const heading=document.createElement('h3');heading.textContent=title;card.append(heading);
    const fields=document.createElement('div');fields.className='hero-settings-fields';card.append(fields);
    const inputs={};
    for(const [field,label,min,max,step] of [['min_rating','Minimum rating',0,10,.1],['max_rating','Maximum rating',0,10,.1],['year_from','Oldest year',1800,2200,1],['year_to','Newest year',1800,2200,1]]){
      const [wrap,input]=number(label,min,max,step,field.startsWith('year')?'Any':'');fields.append(wrap);inputs[field]=input;
    }
    const [sortWrap,sort]=select('Order', [['newest','Newest'],['oldest','Oldest'],['rated','Top rated']]);
    fields.append(sortWrap);inputs.sort=sort;
    const genres=document.createElement('details');genres.className='hero-settings-genres';
    genres.innerHTML='<summary>Genres (all)</summary><div class="hero-settings-genre-list"></div>';
    card.append(genres);inputs.genres=genres;cards.append(card);controls.set(key,inputs);
  }
  let saved;
  async function load() {
    try {
      const [settings,movieGenres,tvGenres]=await Promise.all([
        fetch('/api/profile/settings').then(r=>window.readSettingsJSON(r)),
        fetch('/api/movie-genres').then(r=>r.json()),
        fetch('/api/tv-genres').then(r=>r.json())]);
      saved=settings.hero_rules;
      transition.value=settings.hero_transition||'slide';
      count.value=settings.hero_slide_count??5;
      timeout.value=settings.hero_slide_timeout??5;
      showDisplay();
      for(const [key,,kind] of pages){
        const rule=saved[key],inputs=controls.get(key);
        for(const field of ['min_rating','max_rating','year_from','year_to','sort'])
          inputs[field].value=rule[field]??'';
        const list=inputs.genres.querySelector('.hero-settings-genre-list');
        const available=(kind==='tv'?tvGenres:movieGenres).genres||[];
        for(const genre of [...new Set([...available,...rule.genres])]){
          const label=document.createElement('label');const checkbox=document.createElement('input');
          checkbox.type='checkbox';checkbox.value=genre;checkbox.checked=rule.genres.includes(genre);
          label.append(checkbox,document.createTextNode(genre));list.append(label);
        }
        const update=()=>{const count=list.querySelectorAll('input:checked').length;
          inputs.genres.querySelector('summary').textContent=`Genres (${count||'all'})`;};
        list.addEventListener('change',update);update();
      }
      save.disabled=false;
    } catch(error){status.textContent='Could not load hero rules.';}
  }
  save.disabled=true;
  save.addEventListener('click',async()=>{
    if(!saved)return;
    const rules={};
    for(const [key] of pages){
      const inputs=controls.get(key);
      rules[key]={sort:inputs.sort.value,
        min_rating:Number(inputs.min_rating.value),max_rating:Number(inputs.max_rating.value),
        year_from:inputs.year_from.value?Number(inputs.year_from.value):null,
        year_to:inputs.year_to.value?Number(inputs.year_to.value):null,
        genres:[...inputs.genres.querySelectorAll('input:checked')].map(input=>input.value)};
    }
    save.disabled=true;status.textContent='Saving…';
    try {
      const response=await fetch('/api/profile/settings',{method:'PUT',
        headers:{'Content-Type':'application/json','X-CSRF-Token':window.__settingsCSRF},
        body:JSON.stringify({hero_rules:rules,hero_transition:transition.value,
          hero_slide_count:Number(count.value),hero_slide_timeout:Number(timeout.value)})});
      const result=await window.readSettingsJSON(response);
      if(!response.ok)throw Error(result.error||'Could not save hero rules');
      saved=result.hero_rules;status.textContent='Hero slider settings saved. Open a slider page to see the changes.';
    }catch(error){status.textContent=error.message;}
    finally{save.disabled=false;}
  });
  load();
})();
