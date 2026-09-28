// Only runs on the billing page. Reads rendered DOM, never cookies, storage,
// requests, page scripts, project source or chat messages.
(() => {
  if (location.pathname !== '/settings/billing') return;
  const number = (value) => {
    const text = String(value ?? '').trim();
    if (!/^\d+(?:[.,]\d+)?$/.test(text)) return undefined;
    const n = Number(text.replace(',', '.'));
    return Number.isFinite(n) && n >= 0 && n <= 1e7 ? n : undefined;
  };
  function readBalance() {
    if (location.pathname !== '/settings/billing') return null;
    const meters = document.querySelectorAll('[role="meter"][aria-label="Credits"]');
    const link = document.querySelector('a[href="/settings/workspace"]');
    const main = document.querySelector('main');
    if (meters.length !== 1 || !link || !main) return null;
    const meter = meters[0];
    if (!meter.getClientRects().length) return null;
    // aria-valuenow can be a percentage; aria-valuetext names actual credits.
    const named = meter.getAttribute('aria-valuetext')?.match(/^([\d.,]+) credits left$/i);
    const credits = named ? number(named[1]) : undefined;
    if (credits === undefined) return null;
    const workspaceNode = link.cloneNode(true);
    workspaceNode.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
    const workspace = workspaceNode.textContent.trim();
    if (!workspace || workspace.length > 200) return null;
    // Exclude the upgrade offers below the current subscription summary.
    const summary = main.innerText.split('Change your plan')[0];
    const plan = summary.match(/(?:^|\n)(?:Lovable )?(Free|Pro|Business|Enterprise)(?:\n|$)/i);
    const monthly = summary.match(/(?:^|\n)([\d.,]+) monthly credits(?:\n|$)/i);
    const dailySection = summary.split('Daily build credits')[1]?.split('Daily chat credits')[0];
    const daily = dailySection?.match(/(?:^|\n)([\d.,]+) left(?:\n|$)/);
    const result = {v: 1, workspace, credits};
    if (plan) result.plan = plan[1].toUpperCase();
    if (monthly && number(monthly[1]) !== undefined) result.monthlyCredits = number(monthly[1]);
    if (daily && number(daily[1]) !== undefined) result.dailyBuildCredits = number(daily[1]);
    return result;
  }
  let previous = '';
  let timer;
  async function observe() {
    const value = readBalance();
    if (!value) return;
    const signature = JSON.stringify(value);
    if (signature === previous) return; // Never renew the age of unchanged DOM.
    try {
      const reply = await chrome.runtime.sendMessage({kind: 'balance',
        value: {...value, observedAt: Date.now() / 1000}});
      if (reply?.ok) previous = signature;
    } catch (_) { /* A page survives extension reload; next navigation reconnects. */ }
  }
  // Debounce rendering so plan and daily rows arrive with the balance.
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(observe, 700);
  });
  observer.observe(document.documentElement, {subtree: true, childList: true,
    characterData: true, attributes: true, attributeFilter: ['aria-valuetext']});
  timer = setTimeout(observe, 700);
})();
