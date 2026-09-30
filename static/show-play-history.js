(() => {
  const match = location.pathname.match(/^\/tv\/(\d+)\/?$/);
  if (!match) return;
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    const episode = link && new RegExp(`^/tv/${match[1]}/\\d+/\\d+/?$`).test(new URL(link.href).pathname);
    const linkedPlay = link && (/^play(?:\s|$)/i.test(link.textContent.trim()) || /play/i.test(link.getAttribute('aria-label') || ''));
    const button = event.target.closest('button');
    const play = button && (/^play(?:\s|$)/i.test(button.textContent.trim()) || /play/i.test(button.getAttribute('aria-label') || ''));
    if (episode || linkedPlay || play) window.dispatchEvent(new CustomEvent('media-played', {detail:{type:'tv',id:match[1]}}));
  }, true);
})();
