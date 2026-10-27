(() => {
  'use strict';
  // Isolated extension world. Never access credentials, network, audio or app internals.
  const key = '__vibepulseDisplay';
  if (globalThis[key]) return;
  try { if (sessionStorage.getItem('vibepulse.layout.v1') !== 'on') return; } catch { return; }
  const selectedPath = sessionStorage.getItem('vibepulse.bot.path') || location.pathname;
  if (location.pathname !== selectedPath) { document.documentElement.removeAttribute('data-vp-pending'); return; }
  sessionStorage.setItem('vibepulse.bot.path',selectedPath);
  const root = document.documentElement;
  const marked = new Set();
  let observer, queued = false, stopped = false, lastHero = null;
  let rendered = false, startupTimer, transitionTimer, transitionSince = 0;
  const startupDeadline = Date.now() + 20000;
  const observe = () => observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-pressed','data-state','disabled']});
  const host = document.createElement('div');
  host.id = 'vp-display';
  host.style.fontFamily = getComputedStyle(document.body).fontFamily;
  host.innerHTML = '<header><span>Grok Bot</span><button type="button" id="vp-exit">Original view</button></header><div id="vp-idle-art" aria-hidden="true"></div><section id="vp-copy"><h1></h1><p id="vp-status"></p></section><div id="vp-pill" aria-hidden="true"></div><footer><span>Grok usage <b>Not connected</b></span><span id="vp-hint"></span></footer>';
  document.body.append(host);
  const name = host.querySelector('h1'), status = host.querySelector('#vp-status');
  const idleArt = host.querySelector('#vp-idle-art');
  status.setAttribute('role','status');
  status.setAttribute('aria-live','polite');
  const hint = host.querySelector('#vp-hint');
  const usage = host.querySelector('footer > span');
  let usageTimer;
  async function updateUsage() {
    if (stopped) return;
    try {
      if (!globalThis.chrome?.runtime?.id) return;
      const reading = await chrome.runtime.sendMessage({type:'vp-usage'});
      if (stopped || !reading || typeof reading.text !== 'string') return;
      const label = document.createElement('span'); label.textContent = reading.label || 'Grok usage';
      const value = document.createElement('b'); value.textContent = reading.text;
      const detail = document.createElement('small'); detail.textContent = reading.detail || '';
      usage.replaceChildren(label,value,detail);
      usage.dataset.stale = String(reading.stale !== false);
      usage.title = [reading.label,reading.text,reading.detail].filter(Boolean).join(' · ');
    } catch {
      if (!stopped) usage.textContent = 'Grok usage · Bridge unavailable';
    }
  }
  function mark(el, role) { if (!el) return; el.setAttribute('data-vp', role); marked.add(el); }
  function lift(el) {
    for (let p = el?.parentElement; p && p !== document.body; p = p.parentElement) {
      if (!p.hasAttribute('data-vp-lift')) { p.setAttribute('data-vp-lift',''); marked.add(p); }
    }
  }
  function clearMarks() {
    for (const e of marked) { e.removeAttribute('data-vp'); e.removeAttribute('data-vp-lift'); }
    marked.clear();
  }
  function stop(navigating = false) {
    stopped = true; observer?.disconnect(); clearMarks(); host.remove();
    clearTimeout(startupTimer);
    clearTimeout(transitionTimer);
    clearInterval(usageTimer);
    root.removeAttribute('data-vp-mode');
    root.removeAttribute('data-vp-pending');
    document.removeEventListener('keydown', escape);
    document.removeEventListener('click', nativeExtra, true);
    delete globalThis[key];
    if (navigating !== true) { try { sessionStorage.removeItem('vibepulse.layout.v1'); } catch {} }
  }
  function escape(e) { if (e.key === 'Escape') stop(); }
  function nativeExtra(e) {
    // Let the original user click open native settings/transcript in the original layout.
    if (e.target.closest('[data-vp="settings"],[data-vp="transcript"]')) stop();
  }
  host.querySelector('#vp-exit').addEventListener('click', stop);
  document.addEventListener('keydown', escape);
  document.addEventListener('click', nativeExtra, true);
  function awaitNativeView() {
    // React mounts call controls, avatar and waveform in separate updates.
    // Keep observing during that transition instead of permanently disabling.
    clearMarks();
    if (rendered) {
      root.setAttribute('data-vp-mode','waiting');
      status.textContent = 'Opening voice chat…';
      hint.textContent = 'One moment';
      const panel = document.querySelector('[data-testid="bot-voice-call-popover"]');
      if (panel) {
        lift(panel); mark(panel,'call-panel');
        mark(panel.querySelector('[data-testid="bot-voice-call-mic"]'),'mic');
        mark(panel.querySelector('[data-testid="bot-voice-call-end"]'),'end');
      }
    }
    if (!transitionSince) {
      transitionSince = Date.now();
      transitionTimer = setTimeout(sync,15050);
    }
    if (Date.now()-transitionSince >= 15000) { stop(); return; }
    observe();
  }
  function sync() {
    queued = false;
    if (stopped) return;
    observer.disconnect();
    clearMarks();
    const panel = document.querySelector('[data-testid="bot-voice-call-popover"]');
    const start = document.querySelector('[data-testid="bot-voice-call-start"]');
    const end = panel?.querySelector('[data-testid="bot-voice-call-end"]');
    const mic = panel?.querySelector('[data-testid="bot-voice-call-mic"]');
    // Fail open: never cover warnings, permission dialogs, or an unknown call layout.
    const modal = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[role="alert"]')]
      .some(e => {
        // Grok keeps an empty 1px announcement region mounted at all times.
        // It is not an open warning or dialog and must not disable the layout.
        const rect = e.getBoundingClientRect(), style = getComputedStyle(e);
        const content = e.textContent.trim() || e.getAttribute('aria-label') || e.querySelector('button,input,select,textarea');
        return !!content && rect.width > 1 && rect.height > 1 &&
          style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
      });
    if (location.pathname !== selectedPath || modal) { stop(); return; }
    const failure = panel?.querySelector('[data-testid="bot-voice-call-failure"]');
    const resume = panel?.querySelector('[data-testid="bot-voice-call-resume"]');
    if (!failure && ((!panel && !start) || (panel && (!end || !mic)))) {
      if (!rendered && Date.now() < startupDeadline) { observe(); return; }
      awaitNativeView(); return;
    }
    const callName = !failure && panel?.firstElementChild?.firstElementChild?.textContent?.trim();
    name.textContent = callName || document.querySelector('h1:not(#vp-display h1)')?.textContent?.trim() || 'Grok Bot';
    const links = [...new Map([...document.querySelectorAll('a[href^="/bot/"]')].filter(a => !host.contains(a) && a.querySelector('svg.grok-bot-mark')).map(a=>[a.getAttribute('href'),a])).values()];

    if (!panel || failure) {
      root.setAttribute('data-vp-mode',failure ? 'error' : 'idle');
      status.textContent = failure ? 'The call was interrupted.' : 'Here when you need me.';
      hint.textContent = failure ? 'Resume, or open Original view' : 'Tap to talk';
      if (failure && resume) { lift(panel); mark(panel,'call-panel'); mark(resume,'resume'); }
      else if (start && !failure) { lift(start); mark(start,'start'); }
      const active = links.find(a => a.getAttribute('href') === location.pathname);
      const svg = active?.querySelector('svg.grok-bot-mark');
      if (svg && lastHero !== svg) {
        const copy = svg.cloneNode(true);
        copy.querySelectorAll('script,foreignObject,style').forEach(e=>e.remove());
        for (const el of [copy,...copy.querySelectorAll('*')]) {
          for (const attr of [...el.attributes]) if (/^on/i.test(attr.name) || ['id','href','xlink:href'].includes(attr.name)) el.removeAttribute(attr.name);
        }
        copy.removeAttribute('style'); idleArt.replaceChildren(copy); lastHero = svg;
      }
      if (!svg) {
        root.removeAttribute('data-vp-mode'); clearMarks();
        if (!rendered && Date.now() < startupDeadline) { observe(); return; }
        awaitNativeView(); return;
      }
    } else {
      root.setAttribute('data-vp-mode','call');
      const bot = panel.querySelector('.grok-bot');
      const art = bot?.closest('span[style*="--fg"]');
      const waves = [...panel.querySelectorAll('canvas')];
      if (!art || waves.length !== 2) { awaitNativeView(); return; }
      lift(panel); mark(panel,'call-panel'); mark(art,'live-art');
      const raw = bot.getAttribute('data-state');
      const labels = {idle:'Voice chat',listening:'Listening',speaking:'Speaking',thinking:'Thinking',connecting:'Connecting…'};
      status.textContent = mic.getAttribute('aria-pressed') === 'true' ? 'Microphone muted' : (labels[raw] || 'Voice chat');
      hint.textContent = 'Voice chat';
      waves.forEach((canvas,i)=>mark(canvas.parentElement,`wave-${i}`));
      mark(mic,'mic'); mark(end,'end');
      mark(panel.querySelector('[data-testid="bot-voice-call-settings-toggle"]'),'settings');
      mark(panel.querySelector('[data-testid="bot-voice-call-transcript-toggle"]'),'transcript');
    }
    rendered = true;
    root.removeAttribute('data-vp-pending');
    transitionSince = 0; clearTimeout(transitionTimer);
    observe();
  }
  observer = new MutationObserver(records => {
    if (queued || stopped || records.every(r => host.contains(r.target))) return;
    queued = true; requestAnimationFrame(sync);
  });
  globalThis[key] = {stop};
  updateUsage();
  usageTimer = setInterval(updateUsage,30000);
  startupTimer = setTimeout(sync,20100);
  sync();
})();

