(() => {
  const billboard = document.querySelector('.HomeBanner-module-scss-module__eAYNsa__billboard');
  if (!billboard) return;
  const slides = [...billboard.querySelectorAll('.HomeBanner-module-scss-module__eAYNsa__slideLayer')];
  if (!slides.length) { billboard.remove(); return; }
  const nav = document.createElement('div');
  nav.className = 'HomeBanner-module-scss-module__eAYNsa__markers hero-dynamic-nav';
  nav.setAttribute('role', 'tablist');
  nav.setAttribute('aria-label', 'Featured titles');
  let current = 0;
  let changing = false;
  const transition = ['slide','fade','dissolve'].includes(window.__heroTransition) ? window.__heroTransition : 'slide';
  const buttons = slides.map((slide, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'HomeBanner-module-scss-module__eAYNsa__marker';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-label', slide.querySelector('.hero-detail-title, .HeroCopy-module-scss-module__Y82t7W__titleWrap img')?.getAttribute('alt') || slide.querySelector('.hero-detail-title')?.textContent.trim() || `Featured movie ${index + 1}`);
    button.addEventListener('click', () => show(index));
    nav.append(button);
    return button;
  });
  function show(index) {
    const next = (index + slides.length) % slides.length;
    if (changing || (next === current && slides[current].classList.contains('HomeBanner-module-scss-module__eAYNsa__slideCurrent'))) return;
    const previous = slides[current], incoming = slides[next];
    if (!previous.classList.contains('HomeBanner-module-scss-module__eAYNsa__slideCurrent')) {
      incoming.classList.add('HomeBanner-module-scss-module__eAYNsa__slideCurrent');
    } else {
      changing = true;
      const direction = index < current ? 'backward' : 'forward';
      const done = () => {
        previous.classList.remove('hero-slide-outgoing','HomeBanner-module-scss-module__eAYNsa__slideCurrent');
        incoming.classList.remove('hero-slide-incoming');
        incoming.classList.add('HomeBanner-module-scss-module__eAYNsa__slideCurrent');
        changing = false;
      };
      billboard.dataset.heroTransition = transition;
      billboard.dataset.heroDirection = direction;
      previous.classList.add('hero-slide-outgoing');
      if (transition === 'fade') {
        setTimeout(() => {
          previous.classList.remove('HomeBanner-module-scss-module__eAYNsa__slideCurrent');
          incoming.classList.add('hero-slide-incoming','HomeBanner-module-scss-module__eAYNsa__slideCurrent');
          setTimeout(done, 190);
        }, 170);
      } else {
        previous.classList.remove('HomeBanner-module-scss-module__eAYNsa__slideCurrent');
        incoming.classList.add('hero-slide-incoming','HomeBanner-module-scss-module__eAYNsa__slideCurrent');
        setTimeout(done, transition === 'dissolve' ? 760 : 580);
      }
    }
    current = next;
    buttons.forEach((button, i) => {
      button.classList.toggle('HomeBanner-module-scss-module__eAYNsa__markerActive', i === current);
      button.setAttribute('aria-selected', String(i === current));
    });
  }
  billboard.append(nav);
  show(0);
  const seconds = Number(window.__heroSlideTimeout);
  if (slides.length > 1) setInterval(() => show(current + 1), (Number.isFinite(seconds) && seconds >= 2 && seconds <= 30 ? seconds : 5) * 1000);
})();
