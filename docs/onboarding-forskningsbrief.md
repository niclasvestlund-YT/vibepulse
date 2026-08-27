# Onboarding-forskningsbrief — från kartong till siffror på glaset

**Skrivet:** 2026-08-27, nattresearch inför de fyra nya AMOLED-panelerna.
**Läge:** ren research — ingenting här är beslutat, och ingenting här
authoriserar implementation, hårdvaruinköp eller flashning. Statusarna följer
`spec/hardware.md`:s regler: en idé märks som kisel-kapabel, kort-kopplad,
firmware-aktiverad eller fysiskt verifierad, aldrig mer än evidensen bär.
**Mål:** en onboarding så enkel att någon som *inte* orkar öppna repot i
Claude eller Codex ändå får upp en panel — och att Niclas själv kan onboarda
en ny enhet utan att läsa fyra dokument.

---

## 1. Drömscenariot, ärligt sönderplockat

Drömmen: *"man kör in en QR-kod för Claude Code, sen Codex, kanske GitHub —
och sen är man igång"*, eller *"man konfar via en hemsida vid uppstart och
kryssar i vad man vill köra"*.

Tre hårda sanningar avgör vad av det som går att bygga:

1. **VibePulse är tvåsidig by design.** Datan — Claude/Codex-kvot,
   agentaktivitet, Needs You-frågor — finns bara på datorn där agenterna
   kör. Panelen kan aldrig ensam hämta den. Varje onboarding, hur enkel den
   än görs, måste onboarda *två* saker: panelen och datorn. Det går att göra
   båda triviala; det går inte att ta bort datorn.

2. **QR-koden kan aldrig "ge Claude-access".** Anthropic har under 2026
   uttryckligen förbjudit och serverside-blockerat consumer-OAuth-tokens
   (Free/Pro/Max) utanför Claude Code och Claude.ai — tokens svarar med fel
   utanför de klienterna, och policyn säger att tredjepartsbruk bryter mot
   Consumer Terms. Det finns inget publikt tredjeparts device flow för
   kvotdata. Det legitima mönstret är exakt det tokenservern redan
   använder: Claude Codes egen inloggning lämnar en credential lokalt på
   datorn, tjänsten läser den *på samma dator* och den lämnar aldrig
   maskinen. Codex är samma sak: lokal inloggning, lokal läsning, inget
   tredjepartsflöde att luta sig mot. Alltså: **ingen QR, ingen hemsida och
   inget moln kan ersätta att användaren är inloggad i Claude Code/Codex på
   sin dator.** Onboardingens jobb är att göra allt *runt* det trivialt.

3. **Panelen kan inte skanna QR-koder.** Ingen kamera finns
   (`spec/hardware-capabilities.yaml`). QR-riktningen är alltid: *glaset
   visar, telefonen skannar* — precis som WiFi-fönstret redan gör i dag.

Översatt till vad som faktiskt går:

> **QR:en onboardar panelen** (WiFi + parning med datorn), **ett enda
> kommando eller en installer onboardar datorn**, och **"hemsidan" är två
> saker: en statisk webbflashare (GitHub Pages, ingen backend) och en lokal
> konfigurationssida som tokenservern själv serverar** (`localhost:8737`).
> **GitHub är undantaget som har ett riktigt device flow** — där kan glaset
> faktiskt visa en QR som loggar in GitHub-pulsen.

---

## 2. Nuläget: vad onboarding kostar i dag

Ur `docs/agent-setup.md`, `docs/wifi.md`, `docs/ota.md`:

| Steg | Vad som krävs | Vem klarar det |
|---|---|---|
| `secrets.h` | Kopiera exempel, fylla i WiFi, värd-URL, OTA-token, device key (64 hex) | Terminalvan person eller agent |
| Bygga | ESP-IDF 5.5 (gigabyte-installation), `idf.py set-target && build` | Utvecklare |
| Flasha | USB, download mode (BOOT+RESET-dansen), rätt port, egen strömförsörjning efteråt | Utvecklare med instruktion |
| Tokenserver | `python3 tools/tokenserver/tokenserver.py` + autostart (launchd/Task Scheduler) | Terminalvan person |
| Needs You | `vibepulse_setup.py install`, hooks i Claude Code, Codex `/hooks`-trust, smoke test | Agent + användare tillsammans |
| Ny plats | Löst! WiFi-fönstret + QR + portal finns redan | Vem som helst med telefon |

Två observationer:

- **Roten till nästan all friktion är att fyra hemligheter kompileras in i
  binären** (`TG_WIFI_*`, `TK_VIBEPULSE_BASE_URL`, `TG_OTA_TOKEN`,
  `TK_VIBEPULSE_DEVICE_KEY`). Det är därför varje användare måste bygga
  själv, därför releaser är source-only (`torget.bin` innehåller WiFi-creds
  + device key), och därför webbflashning är omöjlig i dag.
- **Halva onboardingen är redan byggd och bra.** WiFi-fönstret (SoftAP + QR
  + captive portal + NVS-slots, portalen fysiskt nådd från telefon
  2026-08-21), OTA A/B med hälsogate och konsentmodell, den guidade
  `vibepulse_setup.py` med `doctor`, och tokenserverns HTTP-yta på 8737 som
  redan är rätt plats för en lokal setup-sida. Simulatorn gör att hela
  onboarding-UI:t kan byggas och granskas utan hårdvara.

---

## 3. Möjligt / inte möjligt

### Inte möjligt (och varför det inte är en förhandlingsfråga)

| Idé | Varför inte |
|---|---|
| QR-kod som ger panelen/molnet access till Claude- eller Codex-kontot | Uttryckligen förbjudet i Anthropics villkor sedan början av 2026 och serverside-blockerat; inget tredjeparts-OAuth/device-flow finns. Tekniskt vore panelen dessutom fel plats för en OAuth-token (okrypterad NVS). Codex: samma mönster, lokal auth utan tredjepartsflöde |
| Onboarding helt utan dator | Datakällan *är* datorn. En molntjänst som håller användarnas agent-tokens är både förbjuden (ovan) och emot projektets löfte: "Local mode needs no VibePulse account" |
| Panelen skannar en QR | Ingen kamera på något kort i familjen |
| Molnhemsida (HTTPS) som pratar direkt med panelen på LAN | Mixed content + Private Network Access blockerar HTTPS-sida → HTTP-LAN-anrop i moderna webbläsare. Kan inte byggas stabilt; konfigurationssidan måste serveras lokalt (av panelen eller tokenservern) |
| Webbflashning från iPhone/iPad eller Firefox | Web Serial finns bara i Chromium-webbläsare på desktop |
| Captive portal utan "Not Secure" | Ingen certifikatväg för `192.168.4.1`; redan dokumenterat och accepterat i `docs/wifi.md` |
| 5 GHz, WPA2-Enterprise, hotell-captive-portals | Radions och firmwarens kända gränser; onboarding ändrar dem inte |

### Möjligt (med evidensläge per hårdvaruregeln)

| Idé | Bedömning | Kisel / kort / firmware / fysiskt verifierat |
|---|---|---|
| **Webbflashare** (ESP Web Tools på GitHub Pages: välj bräda → Install i webbläsaren) | Beprövat mönster (ESPHome, WLED); esptool-js + Web Serial + manifest per board. Kräver hemlighetsfri firmware + binärreleaser | Kisel ja (native USB, GPIO 19/20) / kort ja / firmware **nej** (releaser är source-only i dag) / verifierat nej |
| **Improv Serial** (WiFi-creds över USB direkt i flashflödet) | Öppen standard, liten firmware-komponent; komplement till SoftAP-portalen så att panelen är online direkt efter webbflash | Kisel ja / kort ja / firmware nej / verifierat nej |
| **Utbyggd förstaboot-wizard på glaset** (WiFi → PAIR ME → klart) | SoftAP/portal/DNS/NVS finns; 90 s-självöppningen finns; "oprovisionerad enhet öppnar direkt" är en liten policyändring i befintlig kod | Kisel ja / kort ja / firmware **delvis** (WiFi-delen finns; parningsdelen saknas) / portal fysiskt nådd 2026-08-21, QR/join ännu kandidat |
| **Värdparning** (datorn hittar panelen, kod på glaset bekräftar, nycklar byts) | Panelen kan annonsera `_vibepulse._tcp` via mDNS (ESP-IDF har mDNS-komponent); tokenservern annonserar inget på Windows i dag, så riktningen "panelen annonserar, värden lyssnar" är den robusta. Fallback: IP på glaset | Kisel ja / kort ja / firmware nej / verifierat nej |
| **GitHub device flow** för GitHub-pulsen | Officiellt stött av GitHub (user code + `github.com/login/device`). Värden kör flödet och håller token; glaset kan visa koden och en QR till verifierings-URL:en | Ren mjukvara på värden + befintlig QR-rendering på glaset |
| **Lokal konfigurationssida** `http://localhost:8737/setup` | Tokenservern serverar redan HTTP i ren stdlib; en setup-/skärmroll-sida är samma mönster. Ingen CORS/mixed-content-problematik: sidan och API:t är samma origin | Ren mjukvara |
| **Per-skärm-roller** ("vad visas på vilken panel") | Kräver enhetsidentitet, config i NVS och multi-enhetsstöd i tokenservern; ren mjukvara men rör många ytor | Ren mjukvara |
| **BLE-provisioning** | Kandidat i `spec/hardware-opportunities.md` med namngivna evidensgap (radio/minnesmätning). SoftAP-portalen löser redan samma problem, så BLE är inte första val | Kisel ja / kort ja / firmware nej / verifierat nej |

---

## 4. Teknisk förutsättning nr 1: hemlighetsfri generisk firmware

Allt annat i den här briefen står och faller med detta. Migrationskartan:

| Kompileras in i dag | Flyttas till | Hur den sätts |
|---|---|---|
| `TG_WIFI_SSID/PASS` (immutable floor) | NVS — **slots-mekanismen finns redan** | Förstaboot-portalen (finns), Improv (ny), eller `wifi-here.sh` (finns). Generisk build har tomt golv; golvet blir "det första nät som någonsin lärts in" i stället för ett inkompilerat |
| `TK_VIBEPULSE_BASE_URL` | NVS | Sätts av värden vid parning (värden vet själv sin Bonjour-/IP-adress) |
| `TG_OTA_TOKEN` | NVS | Genereras på enheten vid första boot (`esp_random`), delas till värden vid parning. `wifi-here.sh`:s härledda AP-lösenord (`sha256("vibepulse-softap-v1"+token)`) överlever eftersom båda sidor fortfarande kan token — wiring-testet uppdateras, inte principen |
| `TK_VIBEPULSE_DEVICE_KEY` | NVS | Parningsutbyte i stället för manuell 64-hex-kopiering i två filer |

Konsekvenser, i fallande vikt:

- **Releaser kan skeppa `torget.bin`.** Dagens release-regel ("aldrig bifoga
  binären — WiFi-creds + device key är inkompilerade") är formulerad kring
  ett symptom. När binären är hemlighetsfri kan regeln omformuleras och
  webbflasharen blir möjlig. Detta är ett medvetet regelbeslut för Niclas,
  inte något en agent gör i förbifarten.
- **En flashad panel är en generisk panel.** De fyra nya enheterna kan
  flashas en gång över USB (eller webben) och därefter provisioneras helt
  utan verktygskedja — inklusive att byta hem, byta värd, byta roll.
- **Konsentmodellen får inte försvagas.** Parningsfönstret ska ärva OTA:s
  tre faktorer: fysisk närvaro (koden står på glaset), kunskap (koden),
  tid (fönstret stänger sig). Lazy-surface-regeln gäller: parningsservern
  existerar bara medan fönstret är öppet.
- **NVS förblir okrypterad** — samma exponering som `secrets.h` redan har
  och som `docs/wifi.md` dokumenterar ärligt (borttappad panel = rotera).
  NVS-kryptering + flash encryption är en möjlig *senare* härdning; den ska
  inte blockera onboardingen.

---

## 5. Den föreslagna resan: kartong → siffror, ~10 minuter

Rekommenderat flöde, ur användarens ögon:

1. **Flasha (engångs).** `vibepulse.github.io`-stil sida (GitHub Pages, ren
   statisk): *välj din bräda* (bilder på de validerade enheterna) →
   **Install** → Web Serial flashar rätt binär → Improv frågar efter WiFi
   direkt i dialogen. Utan Chromium: `installer`-CLI:t gör samma sak via
   esptool. En redan flashad panel hoppar över steget.
2. **WiFi (om inte satt via Improv).** Panelen hittar inget nät →
   VibePulse-setup + QR efter 90 s (finns redan, oprovisionerad enhet kan
   öppna direkt). Telefonen skannar, portalen lär panelen nätet.
3. **PAIR ME.** Online men oparad visar glaset en parningsskärm: sexsiffrig
   kod + "kör installern på din dator" (+ QR till installationssidan).
4. **Datorn, ett kommando.** En installer/one-liner som: installerar och
   autostartar tokenservern, lyssnar efter panelens `_vibepulse._tcp`
   (fallback: skriv IP:n som glaset visar), ber användaren mata in koden
   från glaset, och byter nycklar (device key, OTA-token, värd-URL →
   panelens NVS genom det tidsbegränsade parningsfönstret). TOFU +
   kod-på-glas = samma fysiska-närvaro-faktor som OTA.
5. **Kryssa i vad du vill köra.** Installern öppnar
   `http://localhost:8737/setup` — samma val som `vibepulse_setup.py
   install` (off/claude/codex/both + detaljnivå, allt av som default) fast
   klickbart, med ärlig status per källa (`claudeProbe`-tabellen finns
   redan). Claude/Codex-rutan säger sanningen: *"kräver att du är inloggad
   i Claude Code/Codex på den här datorn"* — och `doctor`-logiken visar
   live om det är uppfyllt.
6. **GitHub (valfritt).** Knapp i lokala sidan → device flow → koden och en
   QR till `github.com/login/device` visas (på sidan och/eller på glaset).
   Token stannar på värden.
7. **Skärmroller (när flera paneler finns).** Samma lokala sida listar
   parade paneler och låter användaren välja per panel: vilka sidor som
   roterar, vilken som tar Needs You, ljusstyrka/nattläge. Sparas på
   värden, levereras som config vid poll.

Steg 5–7 är exakt det "kryssa i vad man vill köra på en hemsida"-scenariot —
skillnaden mot drömmen är bara att hemsidan är lokal, vilket är vad som gör
den både möjlig (ingen PNA/mixed-content-vägg) och förenlig med
privacy-modellen.

---

## 6. De fyra nya panelerna — multi-board-verkligheten

Waveshare-familjen ESP32-S3-Touch-AMOLED (docs.waveshare.com) omfattar i
skrivande stund åtminstone: **1.43 (466×466, rund), 1.64 (280×456), 1.75
(466×466, rund), 1.8 (368×448, SH8601 + FT3168), 2.06 (410×502,
klockformad), 2.16 (480×480, CO5300 + CST9217 — dagens enda validerade)**
och möjligen en 2.41. Vilka fyra som är beställda vet vi först vid
uppackning — **allt nedan är planeringsantaganden, inte hårdvaruclaims.**

Vad olika storlekar faktiskt kostar:

- **UI:t är pixel-exakt per kontrakt** (exakta 480×480-frames, landmark-
  tester, studio-granskning). Nya upplösningar betyder inte "responsiv CSS"
  utan **per-storlek-layouter** med egna exakta captures och egna
  landmark-checks. Runda skärmar (1.43/1.75) är en egen designklass —
  hörnen finns inte.
- **BSP:erna skiljer sig per bräda**: annan displaydrivare (SH8601 vs
  CO5300), annan touchkrets (FT3168 vs CST9217), andra pinnar, eventuellt
  annan PMU-koppling. 2.16-reconens läxa gäller: produktblad ljuger,
  BSP-källkod och docs.waveshare.com är facit, och varje bräda kan ha sin
  egen `bsp_display_lock`-klass av fällor.
- **Intake-rutin när de anländer** (per `spec/hardware.md`-reglerna):
  1. Ny unit-id i `spec/device-units.yaml` med `sku_evidence` och foto.
  2. Board-recon per modell: BSP-namn/version i ESP Component Registry,
     display/touch/PMU/pinnar, `hardware-sources.yaml`-poster.
  3. Ingen capability promoveras utan källa; fysisk verifiering kräver
     Niclas vid panelen.
  4. Först därefter: build-target + layoutarbete.
- **Tokenservern blir fleretalig**: enhetsregister (flera device keys,
  `.ota-device` → enhetslista), `otaAvailableVersion` **per board-typ**
  (olika binärer — dagens "newest build wins" räcker inte), Needs
  You-routing (en panel? alla? den som användaren pekat ut?), per-enhet
  konfig-endpoint.

---

## 7. Arkitekturkandidater

| | A. Webbflashare + panelportal + lokal parning | B. Värd-först (installern gör allt, inkl. USB-flash via esptool) | C. Hostad konfigurator med backend/konton |
|---|---|---|---|
| Molninfra | Ingen (statisk sida) | Ingen | Server, drift, kostnad |
| Privacy-löftet | Intakt | Intakt | Bryter "no VibePulse account"; kan ändå inte nå panelen från HTTPS |
| Utan Chromium/på iOS | Nej (flash-steget) → B som fallback | Ja | — |
| Icke-terminal-användare | Ja (flash + konfig i webbläsare) | Nej (terminal) | — |
| Bedömning | **Rekommenderad** | **Byggs ändå** — CLI-vägen delar manifest/logik med A och är agent-vänlig | **Avråds** |

Rekommendation: **A med B som fallback**, i den ordningen. "Hemsidan" i
drömmen blir två konkreta, molnfria sidor: GitHub Pages-flasharen och
tokenserverns lokala setup-sida.

---

## 8. Fasplan (beroenden, inte datum)

| Fas | Innehåll | Beror på | Risk |
|---|---|---|---|
| **0. Beslut + intake** | Regelbeslutet om hemlighetsfria binärreleaser; uppackning + recon av de fyra panelerna; välja vilka som blir "supported" | Panelerna anländer | Låg |
| **1. Hemlighetsfri firmware** | Migrationskartan i §4: NVS-provisionering av URL/OTA-token/device key, tomt WiFi-golv, förstaboot-policy. Kan börja **nu** på 2.16:an, oberoende av nya paneler | — | **Störst** — rör OTA-token-härledning, wiring-tester, konsentmodellen |
| **2. Parning** | PAIR ME-skärm, parningsfönster (lazy-surface, kod, timeout), mDNS-annonsering, `vibepulse_setup.py pair`, lokal setup-sida på 8737 | Fas 1 | Medel |
| **3. Distribution** | Binärreleaser, ESP Web Tools-manifest + GitHub Pages-sida, Improv Serial, installer-CLI | Fas 1 (+ regelbeslutet) | Medel |
| **4. Multi-board** | BSP-abstraktion, per-storlek-layouter, per-board-binärer i manifest och OTA | Fas 0 (fysiska paneler), Fas 3 för distribution | Hög (okända BSP-fällor) |
| **5. Multi-panel-värd** | Enhetsregister i tokenservern, per-enhet config, skärmroll-UI, Needs You-routing | Fas 2 | Medel |
| **6. GitHub device flow + polish** | Device flow på värden, kod/QR på glaset, dokumentationssvep, README + release enligt release-reglerna | Fas 2 | Låg |

Varje fas har samma verifieringstrappa som resten av projektet: host-tester
→ simulator-captures → studio-granskning → **explicit flash-godkännande**
→ fysisk verifiering innan något promoveras i `spec/`.

---

## 9. Öppna frågor för Niclas

1. **Vilka fyra brädor är beställda?** Avgör hela Fas 4-scopet (runda
   skärmar? vilka BSP:er finns?).
2. **Vad ska "supported device" betyda på flash-sidan** — fysiskt validerad
   av dig (device-units-evidens), eller "BSP finns och bygget är grönt"?
   Din formulering ("ett scenario där jag validerat alla") pekar på det
   förra; sidan kan visa båda kategorierna ärligt märkta.
3. **Är binärreleaser OK när binären är hemlighetsfri?** Dagens
   release-regel förbjuder dem av ett skäl som Fas 1 tar bort — men det är
   ditt regelbeslut.
4. **Flera paneler, en Needs You-fråga — vem larmar?** Alla, en utpekad,
   eller rundgång?
5. **Windows-discovery**: tokenservern annonserar inget mDNS på Windows i
   dag. Är "panelen annonserar, värden lyssnar" + IP-på-glaset-fallback
   acceptabelt, eller ska Windows-sidan få en riktig announcer?
6. **Hur mycket wizard på glaset vs i lokala sidan?** Glaset kan i princip
   bara behöva WiFi + PAIR ME; allt annat är bekvämare i webbläsaren.
7. **Ska den gamla `secrets.h`-vägen leva kvar** som utvecklarväg (golvet +
   inbyggd token) parallellt med provisioneringen? (Förslag: ja, som
   compile-time-override — det är dagens beteende och kostar inget.)

---

## 10. Färdig prompt för nästa session (implementationsplan Fas 1–2)

> Läs `docs/onboarding-forskningsbrief.md`, `docs/wifi.md`, `docs/ota.md`
> och `docs/agent-setup.md`. Skriv en implementationsplan under
> `docs/superpowers/plans/` för **Fas 1 (hemlighetsfri firmware) och Fas 2
> (parning)** ur briefen: NVS-provisionering av `TK_VIBEPULSE_BASE_URL`,
> `TG_OTA_TOKEN` och `TK_VIBEPULSE_DEVICE_KEY` med `secrets.h` kvar som
> compile-time-override, förstaboot-policy för oprovisionerad enhet,
> PAIR ME-skärmen, ett tidsbegränsat parningsfönster som ärver OTA:s
> konsentmodell och lazy-surface-regeln, mDNS-annonsering från panelen,
> `vibepulse_setup.py pair` samt en lokal setup-sida på tokenserverns
> port 8737. Planen ska följa repo-mönstren: rena host-testbara moduler,
> exakta 480×480-simulator-frames för varje ny skärm via
> `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`, uppdaterade
> wiring-tester för AP-lösenordshärledningen, och en explicit lista över
> vad som INTE görs (ingen molntjänst, ingen OAuth mot Claude/Codex,
> ingen försvagning av konsentmodellen). Bygg och visa simulatorframes,
> men flasha ingenting utan mitt uttryckliga godkännande.

---

## Källor (omvärld)

- Anthropic-policyn och blockeringen av tredjeparts-OAuth:
  [claude-code#28091](https://github.com/anthropics/claude-code/issues/28091),
  [Gigazine 2026-02-20](https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block/)
- ESP Web Tools (webbflashning + Improv + manifest):
  [esphome.github.io/esp-web-tools](https://esphome.github.io/esp-web-tools/)
- Improv Wi-Fi-standarden: [improv-wifi.com](https://www.improv-wifi.com/)
- GitHub OAuth device flow:
  [docs.github.com — Authorizing OAuth apps, device flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow)
- Waveshare ESP32-S3-Touch-AMOLED-familjen:
  [docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.75](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.75),
  [waveshare.com — 1.8-tums produktsida](https://www.waveshare.com/esp32-s3-touch-amoled-1.8.htm),
  [waveshare.com — 2.06-tums produktsida](https://www.waveshare.com/esp32-s3-touch-amoled-2.06.htm)
- Web Serial-stöd (Chromium-only, ej iOS): se ESP Web Tools-dokumentationen
  ovan.
