# Simulatorfixtures för glance-klienten (P5)

Inspelade och konstruerade `/api/glance`-svar som simulatorn ska matas med.
Klienten ska klara alla fyra utan att någonsin visa skräp.

| Fil | Ursprung | Testar |
|---|---|---|
| `midnight.json` | Fångad på prod 2026-07-17 00:38, EFTER midnattsfixen | Ärlig nolla: heron visar 0,00|0 och tickar inte. Dygnet är nytt, allt annat lever. |
| `evening.json` | Rekonstruerad ur två prod-fångster 2026-07-16 (kr/ore från 22:39-fångsten, årsfälten från 00:00-fångsten) | Stillastående kväll: kr > 0 men takt 0 och w 0. Tickern står stilla på 100,60. Trippelsiffrigt heltal, layoutens värsta realistiska bredd. |
| `midday.json` | SYNTETISK (w-värdet 8931 är dock ett riktigt observerat middagsvärde) | Full fart: tickern rullar på 10,4 kr/tim, nivåprick normal. |
| `error-502.json` | Formen på riktiga felsvar från API:t (HTTP 502) | Klienten behåller senaste goda värden, markerar stale efter 120 s, kraschar inte. |

Regler som fixturerna låser:

- Före FÖRSTA lyckade svaret: streck, aldrig 0 kr.
- Efter fel: senaste goda datat kvarstår, stale-markering efter 120 s.
- `day`-fältet kan användas för att upptäcka att servern bytt dygn medan
  enheten visar gammal data.
- Fältet `at` är serverns klocka och används ALDRIG för tickerns tidsbas;
  enhetens egen monotona klocka gäller (se ticker.h).

## Max Tracker-fixturer

Konstruerade `/api/max-tracker`-svar i dense form (kontraktet ligger i
`docs/superpowers/specs/2026-08-12-max-tracker-design.md`).

| Fil | Ursprung | Testar |
|---|---|---|
| `max-tracker-full.json` | SYNTETISK | Mogen användare: båda providers fullfärgade 140 dagar (inga `-1`), sex `[100,2]`-toppar i de senaste 6 veckorna, `weekMaxed` matchande, Codex har `planLabel`. |
| `max-tracker-coldstart.json` | SYNTETISK | Ny användare: Claude börjar med 14 dagar utan loggar, sedan gråa aktivitetsdagar (`lvl` utan `pct`), sedan 5 riktiga kvotdagar; Codex som i full. |
| `max-tracker-empty.json` | SYNTETISK | Innan första hämtningen: alla dagar `[-1,-1]`, alla aggregat `null`/0 — grafen ska rendera helt tom, aldrig påhittade nollor. |
| `max-tracker-live-shape.json` | SERVER-GENERERAD (`snapshot()` via en riktig `MaxTrackerStore` matad med brutna procent) | Regressionsfixtur: till skillnad från de tre ovan (handskrivna, alltid heltal) matas denna med Claude/Codex-utilization som den verkligen anländer — 15.5, 99.96, 0.5, 88.51 osv "by construction". Bevisar att servern avrundar till heltal INNAN serialisering (annars avvisar enhetens `int8_t`-parser hela svaret) och att en dag under 100 aldrig avrundas upp till den reserverade exakt-röda 100-cellen. |
