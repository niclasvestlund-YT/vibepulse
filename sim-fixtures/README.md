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
