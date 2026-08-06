# tokenserver — Tokenmätarens Mac-tjänst

Serverar Claude- och Codex-användningen som platt JSON enligt glance-
mönstret (kontrakt v2). Skärmen hämtar `/api/tokens` över LAN var 30:e
sekund. Ren Python 3-stdlib — inget att installera. Tre källor:

1. **Volymen** — `~/.claude/projects/**/*.jsonl` skannas inkrementellt:
   dagens/månadens tokens, brinntakt, sessioner.
2. **Claudes tak** (Clawdmeter-mönstret) — tjänsten läser Claude Codes
   OAuth-token ur macOS-nyckelringen och gör en minimal API-förfrågan
   (`max_tokens: 0` — prefill utan output, i praktiken gratis) var 120:e
   sekund; rate-limit-headrarna i svaret bär usage-panelens tre fönster:
   5-timmars, veckan och veckan för tyngsta modellen (Fable/Opus).
   Tokenen lämnar aldrig Macen — skärmen får bara procenttal.
3. **Codex tak** — passiv läsning av `~/.codex/sessions/**/rollout-*.jsonl`
   (Codex CLI skriver used_percent + resets_at där själv). Har fönstret
   hunnit nollas sedan senaste snapshoten serveras null, inte gamla procent.

## Prova

```
python3 tokenserver.py
curl http://localhost:8737/api/tokens
```

**Första körningen frågar macOS om nyckelringsåtkomst** ("security vill
använda ... Claude Code-credentials") — välj "Tillåt alltid" så tjänsten
kan probea utan att fråga om. Startloggen skriver dessutom ut de exakta
`anthropic-ratelimit-*`-headrarna vid första proben — facit om mappningen
någonsin behöver justeras.

Svar (kontraktet appen parsar, `components/app_tokens/tokens_parse.c`;
null = ärlig frånvaro, skärmen visar streck):

```json
{"v": 2, "dayTokens": 48231907, "dayTokensPerHour": 5120000,
 "daySessions": 4, "monthTokens": 612480233,
 "claudeSessionPct": 21.0, "claudeSessionResetMin": 80,
 "claudeWeekPct": 47.0, "claudeWeekResetMin": 850,
 "claudeModelWeekPct": 73.0, "claudeModelWeekResetMin": 850,
 "codexSessionPct": null, "codexSessionResetMin": null,
 "codexWeekPct": 35.0, "codexWeekResetMin": 2210}
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
- Codex-procenten är senast kända snapshot (Codex loggar bara när den kör);
  passerad resets_at ⇒ null. Claude-proben kostar en tom förfrågan var
  120:e sekund — försumbart mot fönstren den mäter.
- Är Macen av visar skärmen streck efter två minuter (stale), inte gamla
  siffror som låtsas vara färska. Det är rätt beteende, inte ett fel.
