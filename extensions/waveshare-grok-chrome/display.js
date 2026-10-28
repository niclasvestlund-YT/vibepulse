(() => {
  'use strict';
  // Isolated extension world. Never access credentials, network, audio or app internals.
  const SHOW_PANEL = false;
  const key = '__vibepulseDisplay';
  if (globalThis[key]) return;
  try { if (sessionStorage.getItem('vibepulse.layout.v1') !== 'on') return; } catch { return; }
  const selectedPath = sessionStorage.getItem('vibepulse.bot.path') || location.pathname;
  if (location.pathname !== selectedPath) { document.documentElement.removeAttribute('data-vp-pending'); return; }
  sessionStorage.setItem('vibepulse.bot.path',selectedPath);
  const root = document.documentElement;
  const marked = new Set();
  const waveRatios = new WeakMap();
  let observer, queued = false, stopped = false, lastHero = null;
  let rendered = false, startupTimer, transitionTimer, transitionSince = 0;
  const startupDeadline = Date.now() + 20000;
  const observe = () => observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-pressed','data-state','disabled']});
  const host = document.createElement('div');
  host.id = 'vp-display';
  const fontStyle = document.createElement('style');
  fontStyle.textContent = [400,500,700].map(weight =>
    `@font-face { font-family:"Geist"; font-style:normal; font-weight:${weight}; font-display:swap; src:url("${chrome.runtime.getURL(`fonts/geist-latin-${weight}.woff2`)}") format("woff2"); }`
  ).join('\n');
  document.head.append(fontStyle);
  host.innerHTML = '<header><span>Grok Bot</span><button type="button" id="vp-exit">Original view</button></header><div id="vp-idle-art" aria-hidden="true"></div><section id="vp-copy"><h1></h1><div id="vp-state-card" role="status" aria-live="polite" aria-hidden="true"></div><p id="vp-status"></p></section><div id="vp-pill" aria-hidden="true"></div><footer><span>Grok usage <b>Not connected</b></span><span id="vp-hint"></span></footer>';
  document.body.append(host);
  const frame = document.createElement('div');
  frame.id = 'vp-avatar-frame';
  frame.setAttribute('aria-hidden','true');
  for (const state of ['idle','listening','thinking','speaking']) {
    const layer = document.createElement('span');
    layer.className = `vp-ring-layer vp-ring-${state}`;
    frame.append(layer);
  }
  host.append(frame);
  const name = host.querySelector('h1'), status = host.querySelector('#vp-status');
  const idleArt = host.querySelector('#vp-idle-art');
  status.setAttribute('role','status');
  status.setAttribute('aria-live','polite');
  const hint = host.querySelector('#vp-hint');
  const usage = host.querySelector('footer > span');
  const stateCard = host.querySelector('#vp-state-card');
  let cardState;
  stateCard.textContent = 'Grok Bot · Speaking';
  function setState(state) {
    root.setAttribute('data-vp-state',state);
    if (state === cardState) return;
    cardState = state;
    const speaking = state === 'speaking';
    stateCard.classList.toggle('vp-visible',speaking);
    stateCard.setAttribute('aria-hidden',String(!speaking));
  }
  const strip = SHOW_PANEL ? document.createElement('div') : null;
  if (SHOW_PANEL) {
    strip.id = 'vp-panel';
    host.querySelector('#vp-copy').append(strip);
  }
  const panelCards = new Map();
  function renderPanel(payload) {
    if (!SHOW_PANEL) return;
    const seen = new Set();
    let previous = null;
    for (const [index, card] of payload.cards.slice(0,6).entries()) {
      if (typeof card.id !== 'string' || seen.has(card.id)) continue;
      seen.add(card.id);
      let entry = panelCards.get(card.id);
      const isNew = !entry;
      if (isNew) {
        const row = document.createElement('div'); row.className = 'vp-card';
        const title = document.createElement('strong');
        const body = document.createElement('span');
        row.append(title,body);
        entry = {row,title,body,removalTimer:null};
        panelCards.set(card.id,entry);
        row.addEventListener('animationend', event => {
          if (event.target !== row) return;
          if (['vp-card-enter','vp-card-opacity'].includes(event.animationName)) row.classList.remove('vp-enter');
          if (event.animationName === 'vp-card-pulse') row.classList.remove('vp-pulse');
        });
      }
      const {row,title,body} = entry;
      clearTimeout(entry.removalTimer);
      entry.removalTimer = null;
      row.classList.remove('vp-leaving');
      row.style.setProperty('--vp-i',String(index));
      const nextStatus = ['ok','warn','fail','info'].includes(card.status) ? card.status : 'info';
      const changedStatus = !isNew && row.dataset.status !== nextStatus;
      row.dataset.status = nextStatus;
      title.textContent = card.title;
      body.textContent = card.body;
      row.title = [card.title,card.body,card.updated_at].join(' · ');
      if (isNew) row.classList.add('vp-enter');
      if (changedStatus) {
        row.classList.remove('vp-pulse');
        void row.offsetWidth; // Restart the one-shot highlight for the latest status.
        row.classList.add('vp-pulse');
      }
      const anchor = previous ? previous.nextSibling : strip.firstChild;
      if (anchor !== row) strip.insertBefore(row,anchor);
      previous = row;
    }
    for (const [id, entry] of panelCards) {
      if (seen.has(id) || entry.removalTimer !== null) continue;
      entry.row.classList.remove('vp-enter','vp-pulse');
      entry.row.classList.add('vp-leaving');
      entry.removalTimer = setTimeout(() => {
        entry.row.remove();
        panelCards.delete(id);
      },200);
    }
  }
  async function updatePanel() {
    if (!SHOW_PANEL || stopped) return;
    try {
      const payload = await chrome.runtime.sendMessage({type:'vp-panel'});
      if (!payload || payload.v !== 1 || !Array.isArray(payload.cards)) throw new Error('Unavailable');
      if (!stopped) renderPanel(payload);
    } catch { /* Keep the optional strip empty when unavailable. */ }
  }
  let usageTimer, panelTimer;
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
    for (const e of marked) {
      e.removeAttribute('data-vp'); e.removeAttribute('data-vp-lift');
      e.removeAttribute('data-vp-wave-unit'); e.removeAttribute('data-vp-wave-branch');
      e.style.removeProperty('--vp-wave-ar');
    }
    marked.clear();
  }
  function stop(navigating = false) {
    stopped = true; observer?.disconnect(); clearMarks(); host.remove(); fontStyle.remove();
    clearTimeout(startupTimer);
    clearTimeout(transitionTimer);
    clearInterval(usageTimer);
    clearInterval(panelTimer);
    for (const entry of panelCards.values()) clearTimeout(entry.removalTimer);
    panelCards.clear();
    root.removeAttribute('data-vp-state');
    root.removeAttribute('data-vp-mode');
    root.removeAttribute('data-vp-pending');
    document.removeEventListener('pointerdown', press, true);
    document.removeEventListener('animationend', finishPress);
    document.querySelectorAll('.vp-press').forEach(el => el.classList.remove('vp-press'));
    document.querySelectorAll('.vp-ripple').forEach(el => el.remove());
    document.removeEventListener('keydown', escape);
    document.removeEventListener('click', nativeExtra, true);
    delete globalThis[key];
    if (navigating !== true) { try { sessionStorage.removeItem('vibepulse.layout.v1'); } catch {} }
  }
  function press(event) {
    if (!root.hasAttribute('data-vp-mode') || !(event.target instanceof Element)) return;
    const target = event.target.closest('#vp-pill,[data-vp="mic"],[data-vp="end"],[data-vp="start"],[data-vp="resume"]');
    if (!target || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    target.classList.remove('vp-press');
    void target.offsetWidth;
    target.classList.add('vp-press');
    target.querySelector('.vp-ripple')?.remove();
    const rect = target.getBoundingClientRect();
    const ripple = document.createElement('span');
    ripple.className = 'vp-ripple';
    ripple.setAttribute('aria-hidden','true');
    const size = Math.max(rect.width,rect.height) * 2;
    ripple.style.cssText = `width:${size}px;height:${size}px;left:${event.clientX-rect.left-size/2}px;top:${event.clientY-rect.top-size/2}px`;
    ripple.addEventListener('animationend', () => ripple.remove(), {once:true});
    target.append(ripple);
  }
  function finishPress(event) {
    if (event.animationName === 'vp-press') event.target.classList.remove('vp-press');
  }
  document.addEventListener('animationend', finishPress, {passive:true});
  document.addEventListener('pointerdown', press, {passive:true,capture:true});
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
      setState('connecting');
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
      setState('idle');
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
      setState(Object.hasOwn(labels,raw) ? raw : 'idle');
      status.textContent = mic.getAttribute('aria-pressed') === 'true' ? 'Microphone muted' : (labels[raw] || 'Voice chat');
      hint.textContent = 'Voice chat';
      // Geometry metadata only: never acquire a context or inspect canvas pixels.
      let unit = waves[0].parentElement;
      while (unit && !unit.contains(waves[1])) unit = unit.parentElement;
      if (!unit || unit === panel || waves.some(canvas => canvas.parentElement === unit)) {
        stop(); return; // Unknown waveform structure: leave native controls usable.
      }
      unit.setAttribute('data-vp-wave-unit',''); marked.add(unit);
      // Native DOM order assumption: first branch is bot/output (wave-0),
      // second is user/mic input (wave-1). Keep both native branches mounted.
      waves.forEach((canvas,i) => {
        if (!waveRatios.has(canvas) && canvas.width > 0 && canvas.height > 0) {
          waveRatios.set(canvas,`${canvas.width} / ${canvas.height}`);
        }
        const wave = canvas.parentElement;
        mark(wave,`wave-${i}`);
        wave.style.setProperty('--vp-wave-ar',waveRatios.get(canvas) || 'auto');
        for (let branch = wave; branch && branch !== unit; branch = branch.parentElement) {
          branch.setAttribute('data-vp-wave-branch',''); marked.add(branch);
        }
      });
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
    queued = true; queueMicrotask(sync);
  });
  globalThis[key] = {stop};
  if (SHOW_PANEL) {
    updatePanel();
    panelTimer = setInterval(updatePanel,20000);
  }
  updateUsage();
  usageTimer = setInterval(updateUsage,30000);
  startupTimer = setTimeout(sync,20100);
  sync();
})();

