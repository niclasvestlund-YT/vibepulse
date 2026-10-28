chrome.action.onClicked.addListener(async tab => {
  if (!tab.id || !tab.url) return;
  const url = new URL(tab.url);
  if (url.origin !== 'https://grok.com' || !url.pathname.startsWith('/bot/')) {
    await chrome.action.setBadgeText({tabId:tab.id,text:'BOT'}); return;
  }
  try {
    const result = await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{
      if (globalThis.__vibepulseDisplay) { globalThis.__vibepulseDisplay.stop(); return false; }
      sessionStorage.setItem('vibepulse.layout.v1','on');
      sessionStorage.setItem('vibepulse.bot.path',location.pathname); return true;
    }});
    if (result[0]?.result) {
      await chrome.scripting.insertCSS({target:{tabId:tab.id},files:['display.css']});
      await chrome.scripting.executeScript({target:{tabId:tab.id},files:['display.js']});
    }
    await chrome.action.setBadgeText({tabId:tab.id,text:''});
  } catch {
    await chrome.action.setBadgeText({tabId:tab.id,text:'!'});
  }
});

// Usage is fetched by the extension, never by Grok's page or an internal Grok API.
importScripts('usage.js');
let usageCache = null, usageAt = 0, usagePending = null;
async function readUsage() {
  if (usageCache && Date.now()-usageAt<15000) return usageCache;
  if (usagePending) return usagePending;
  usagePending=(async()=>{
    for (const port of [8737,8741]) {
      try {
        const response=await fetch(`http://127.0.0.1:${port}/api/grok`,{cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(2500)});
        if(!response.ok) continue;
        const text=await response.text();
        if(text.length>8192) continue;
        const payload=JSON.parse(text);
        if(payload.v!==1 || typeof payload.enabled!=='boolean') continue;
        if(payload.enabled===false && port===8737) continue;
        usageCache=VibePulseUsage.format(payload); usageAt=Date.now(); return usageCache;
      } catch { /* No keys, response bodies or errors are logged. */ }
    }
    // Never retain an old fresh-looking percentage after a transport failure.
    return VibePulseUsage.unknown('Bridge offline');
  })();
  try{return await usagePending;}finally{usagePending=null;}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message?.type!=='vp-usage' || !sender.tab?.id) return;
  try{if(new URL(sender.url).origin!=='https://grok.com')return;}catch{return;}
  readUsage().then(reply,()=>reply(VibePulseUsage.unknown('Unavailable')));
  return true;
});

const SHOW_PANEL = false;
let panelCache = null, panelAt = 0, panelPending = null;
const panelOffline = () => ({v:1,cards:[]});
async function readPanel() {
  if (!SHOW_PANEL) return panelOffline();
  if (panelCache && Date.now()-panelAt<15000) return panelCache;
  if (panelPending) return panelPending;
  panelPending=(async()=>{
    for (const port of [8737,8741]) {
      try {
        const response=await fetch(`http://127.0.0.1:${port}/api/panel`,{cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(2500)});
        if(!response.ok) continue;
        const text=await response.text();
        if(new TextEncoder().encode(text).length>8192) continue;
        const payload=JSON.parse(text);
        if(payload.v!==1 || !Array.isArray(payload.cards) || payload.cards.length>6) continue;
        if(!payload.cards.every(c=>c && ['id','title','body','updated_at'].every(k=>typeof c[k]==='string') && ['ok','warn','fail','info'].includes(c.status))) continue;
        return payload;
      } catch { /* Deliberately omit credentials, bodies and error details. */ }
    }
    return panelOffline();
  })();
  try { panelCache=await panelPending; panelAt=Date.now(); return panelCache; }
  finally { panelPending=null; }
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(!SHOW_PANEL || message?.type!=='vp-panel' || !sender.tab?.id) return;
  try {
    if(new URL(sender.url).origin!=='https://grok.com' || new URL(sender.tab.url).origin!=='https://grok.com') return;
  } catch { return; }
  readPanel().then(reply,()=>reply(panelOffline()));
  return true;
});
