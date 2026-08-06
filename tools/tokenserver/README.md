# tokenserver — Tokenmätarens Mac-tjänst

Serverar Claude Code-användningen som platt JSON enligt glance-mönstret.
Skärmen hämtar `/api/tokens` över LAN var 30:e sekund; tjänsten skannar
`~/.claude/projects/**/*.jsonl` inkrementellt och räknar om högst var 30:e
sekund. Ren Python 3-stdlib — inget att installera.

## Prova

```
python3 tokenserver.py
curl http://localhost:8737/api/tokens
```

Svar (kontraktet Tokenmätar-appen parsar, `components/app_tokens/tokens_parse.c`):

```json
{"v": 1, "dayTokens": 48231907, "dayTokensPerHour": 5120000,
 "daySessions": 4, "monthTokens": 612480233, "at": "2026-08-06T21:14:02+02:00"}
```

Peka skärmen hit i repytrotens `secrets.h`:

```c
#define TK_TOKENS_URL "http://<macens-lan-ip>:8737/api/tokens"
```

Macens LAN-IP: `ipconfig getifaddr en0`. Ge gärna Macen fast DHCP-lease i
routern — byter IP:t adress står skärmen med streck tills secrets.h flashas om.

## Autostart via launchd

```
cp se.torget.tokenserver.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/se.torget.tokenserver.plist
```

Plisten antar att repot bor i `~/Torget` — redigera sökvägen annars.
Loggen hamnar i `/tmp/torget-tokenserver.log`.

## Ärlighetsnoter

- Tokens = in + ut + cacheskrivning + cacheläsning, dedupade på
  message.id + requestId (återupptagna sessioner dubbelräknas inte).
- `dayTokensPerHour` är senaste timmens faktiska förbrukning — 0 betyder
  paus, och då låter skärmen bli att ticka. Inga hittade takter.
- Är Macen av visar skärmen streck efter två minuter (stale), inte gamla
  siffror som låtsas vara färska. Det är rätt beteende, inte ett fel.
