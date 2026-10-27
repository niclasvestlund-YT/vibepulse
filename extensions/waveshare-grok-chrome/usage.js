/* Render the VibePulse /api/grok contract without conflating plan and API spend. */
(() => {
  const validNumber = x => typeof x === 'number' && Number.isFinite(x) && x >= 0;
  const unknown = text => ({label:'Grok usage',text,detail:'',stale:true,pct:null});
  function format(p) {
    if (!p || p.v !== 1) return unknown('Unavailable');
    if (p.enabled !== true) return unknown('Not enabled');
    if (p.source === 'plan' && Number.isInteger(p.usedPct) && p.usedPct >= 0 && p.usedPct <= 100) {
      const stale = p.stale !== false || (validNumber(p.ageSeconds) && p.ageSeconds > 600) || p.resetSeconds === 0;
      const age = validNumber(p.ageSeconds) ? Math.floor(p.ageSeconds/60) : null;
      const tag = p.estimated === true ? 'EST' : 'Saved reading';
      const detail = [tag,age === null ? null : age < 1 ? 'just now' : age+'m ago',stale ? 'STALE' : null].filter(Boolean).join(' · ');
      return {label:'Grok usage',text:p.usedPct+'% used',detail,stale,pct:p.usedPct};
    }
    if (['xai','openrouter'].includes(p.source) && validNumber(p.spentCents)) {
      return {label:p.source==='xai'?'xAI API spend':'OpenRouter spend',text:'$'+(p.spentCents/100).toFixed(2),detail:p.stale!==false?'STALE':'API billing · not subscription',stale:p.stale!==false,pct:null};
    }
    return unknown(p.source==='bots'?'Quota unavailable':'No saved reading');
  }
  globalThis.VibePulseUsage = {format,unknown};
})();
