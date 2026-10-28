// A session-only presentation preference. No account data or credentials.
(() => {
  try {
    if (sessionStorage.getItem('vibepulse.layout.v1') !== 'on') return;
    const selected = sessionStorage.getItem('vibepulse.bot.path');
    if (selected && selected !== location.pathname) return;
    document.documentElement.setAttribute('data-vp-pending','');
    setTimeout(()=>document.documentElement.removeAttribute('data-vp-pending'),22000);
  } catch { /* Storage unavailable: leave the original page usable. */ }
})();
