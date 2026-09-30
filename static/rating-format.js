(() => {
  const selector = '[class*="__score"] .tabular-nums, [class*="movieCard"] .tabular-nums, [class*="heroItem"] [class*="__score"] .tabular-nums';
  function format(root) {
    root.querySelectorAll?.(selector).forEach(node => {
      const value = node.textContent.trim();
      if (/^\d+(?:\.\d+)?$/.test(value) && Number(value) <= 10 && value !== Number(value).toFixed(1)) node.textContent = Number(value).toFixed(1);
    });
  }
  format(document);
  new MutationObserver(() => format(document)).observe(document.body, {childList:true, subtree:true});
})();
