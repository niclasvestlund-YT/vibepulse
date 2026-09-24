importScripts('config.js');
const BILLING = 'https://lovable.dev/settings/billing#vibepulse';
const LOCAL = `http://127.0.0.1:${VIBEPULSE_PORT}/api/lovable/browser`;
let running = false;

async function status(text, ok = false) {
  await chrome.storage.local.set({status: text, statusAt: Date.now()});
  await chrome.action.setBadgeText({text: ok ? 'ON' : '…'});
  await chrome.action.setBadgeBackgroundColor({color: ok ? '#188038' : '#666666'});
}

async function tick() {
  if (running) return;
  running = true;
  try {
    const tabs = await chrome.tabs.query({url: 'https://lovable.dev/*'});
    const projects = tabs.filter(t => new URL(t.url).pathname.startsWith('/projects/'));
    const saved = await chrome.storage.session.get('billingTabId');
    // Recover only our explicitly marked page after a browser restart.
    const own = tabs.find(t => t.id === saved.billingTabId && t.url === BILLING) ||
      tabs.find(t => t.url === BILLING);
    if (!projects.length) {
      if (own && !own.active) await chrome.tabs.remove(own.id);
      await chrome.storage.session.remove('billingTabId');
      await status('No Lovable project open. Last balance will age to CACHED.');
      return;
    }
    if (!own) {
      const tab = await chrome.tabs.create({url: BILLING, active: false});
      await chrome.storage.session.set({billingTabId: tab.id, lastReload: Date.now()});
      await status('Reading Lovable credit balance…');
    } else {
      await chrome.storage.session.set({billingTabId: own.id});
      const {lastReload = 0} = await chrome.storage.session.get('lastReload');
      // Never navigate or reload the user's editor or a page they are viewing.
      if (!own.active && own.status === 'complete' && Date.now() - lastReload >= 55000) {
        await chrome.storage.session.set({lastReload: Date.now()});
        await chrome.tabs.reload(own.id, {bypassCache: true});
      }
      const {lastAccepted = 0} = await chrome.storage.local.get('lastAccepted');
      if (Date.now() - lastAccepted > 180000)
        await status('No fresh balance. Check login and the VibePulse balance tab.');
    }
  } catch (_) {
    await status('Browser balance unavailable. Open Lovable and check login.');
  } finally { running = false; }
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab ||
      !sender.url?.startsWith('https://lovable.dev/settings/billing') ||
      new URL(sender.url).pathname !== '/settings/billing' || msg?.kind !== 'balance') return;
  (async () => {
    try {
      // Fixed destination and allowlisted body: never a general-purpose proxy.
      const data = msg.value;
      const value = {v: data.v, workspace: data.workspace, credits: data.credits,
        observedAt: data.observedAt};
      for (const key of ['plan', 'monthlyCredits', 'dailyBuildCredits'])
        if (data[key] !== undefined) value[key] = data[key];
      const response = await fetch(LOCAL, {method: 'POST', credentials: 'omit',
        headers: {'Content-Type': 'application/json', 'X-VibePulse-Browser': chrome.runtime.id},
        body: JSON.stringify(value), signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('rejected');
      await chrome.storage.local.set({lastAccepted: Date.now()});
      await status(`${value.credits} credits · updated ${new Date().toLocaleTimeString()}`, true);
      respond({ok: true});
    } catch (_) {
      await status('Not delivered. Check tokenserver and the configured workspace.');
      respond({ok: false});
    }
  })();
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => {if (alarm.name === 'credits') tick();});
chrome.tabs.onUpdated.addListener((_id, change) => {if (change.status === 'complete') tick();});
chrome.tabs.onRemoved.addListener(() => tick());
chrome.runtime.onInstalled.addListener(() => tick());
chrome.runtime.onStartup.addListener(() => tick());
// Ensure alarms exist whenever the service worker wakes, including reloads.
chrome.alarms.get('credits').then(alarm => {
  if (!alarm) chrome.alarms.create('credits', {periodInMinutes: 1});
});
