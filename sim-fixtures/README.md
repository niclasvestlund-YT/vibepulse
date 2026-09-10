# Simulatorfixturer

Inspelade och konstruerade API-svar som simulatorn och hosttesterna delar.
Samma parsrar som targetet läser dem, så en payload som renderar här kan
inte felparsa på hyllan.

Fixturerna för companion-appar (Solelkollen `/api/glance`) bor i deras egna
repon tillsammans med apparna — det här repot innehåller bara VibePulse.

Regler som fixturerna låser:

- Före FÖRSTA lyckade svaret: streck, aldrig påhittade nollor.
- Efter fel: senaste goda datat kvarstår, stale-markering efter 120 s.

## Tokens-fixturer

| Fil | Ursprung | Testar |
|---|---|---|
| `tokens.json` | SYNTETISK, godkänd statisk AMOLED-layout | Fullt v2-svar: 73/47/21-ringarna, volym, värde, prognoser. |
| `tokens-missing.json` | SYNTETISK | Alla kvoter `null`: streck, aldrig påhittade procent. |
| `tokens-volume-failing.json` | SYNTETISK (issue #62) | Som ovan men `usageTotals.state: "failing"`: omräkningen på datorn kraschar. Firmwaren applicerar kvoten, visar streck på värdesidan (inte gamla siffror som ser färska ut) och loggar en varning. |
| `tokens-warming-up.json` | SYNTETISK (issue #62) | Tjänstens första historikskanning pågår: räknarna är noll med `usageTotals.placeholder: true`, kvoten live. Firmwaren applicerar kvoten men rör inte värdesidan; äldre firmware får aldrig det här svaret (tjänsten kräver `X-VibePulse-Accepts: usage-totals`). |

## Max Tracker-fixturer

Konstruerade `/api/max-tracker`-svar i dense form (kontraktet ligger i
`docs/superpowers/specs/2026-08-12-max-tracker-design.md`).

| Fil | Ursprung | Testar |
|---|---|---|
| `max-tracker-full.json` | SYNTETISK | Mogen användare: båda providers fullfärgade 140 dagar (inga `-1`), sex `[100,2]`-toppar i de senaste 6 veckorna, `weekMaxed` matchande, Codex har `planLabel`. |
| `max-tracker-coldstart.json` | SYNTETISK | Ny användare: Claude börjar med 14 dagar utan loggar, sedan gråa aktivitetsdagar (`lvl` utan `pct`), sedan 5 riktiga kvotdagar; Codex som i full. |
| `max-tracker-empty.json` | SYNTETISK | Innan första hämtningen: alla dagar `[-1,-1]`, alla aggregat `null`/0 — grafen ska rendera helt tom, aldrig påhittade nollor. |
| `max-tracker-live-shape.json` | SERVER-GENERERAD (`snapshot()` via en riktig `MaxTrackerStore` matad med brutna procent) | Regressionsfixtur: till skillnad från de tre ovan (handskrivna, alltid heltal) matas denna med Claude/Codex-utilization som den verkligen anländer — 15.5, 99.96, 0.5, 88.51 osv "by construction". Bevisar att servern avrundar till heltal INNAN serialisering (annars avvisar enhetens `int8_t`-parser hela svaret) och att en dag under 100 aldrig avrundas upp till den reserverade exakt-röda 100-cellen. |

## Needs You-fixturer

Konstruerade `/api/agent-status`-svar med den frivilliga `pending`-posten
(kontraktet ligger i `docs/needs-you-investigation.md`). Alla tre bär också
ett vanligt jobb, eftersom det som ska bevisas är att agentlistan överlever
bredvid en interaktion — enheten kastar hela svaret över
`TK_AGENT_HTTP_BODY_CAP`, så storleken är en del av kontraktet.

| Fil | Ursprung | Testar |
|---|---|---|
| `agent-status-needs-you-question.json` | SYNTETISK | Det vanliga fallet: en fråga med Claudes egen rekommendation (`marked: true`), `can_approve: true`, två alternativ. Panelen visar rekommendationen och en APPROVE. |
| `agent-status-needs-you-approval.json` | SYNTETISK | Ett kommando att godkänna (`npm test`), i den godkännbara nivån, med hela kommandot läsbart. |
| `agent-status-needs-you-private.json` | SYNTETISK | Integritetsläget (`--interaction-detail` av): ingen prompt, ingen titel, `can_approve: false`. Skärmen ska säga att något väntar och i vilket projekt, och bara kunna neka eller lämna till terminalen. |
