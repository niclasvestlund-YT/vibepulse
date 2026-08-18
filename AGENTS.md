# VibePulse — firmware för hyllskärmen

**Setting this repo up for someone? Follow
[docs/agent-setup.md](docs/agent-setup.md), not this file.** It is in
English, step by step, with a verification after every step and a
symptom→fix table. This file is maintainer context for working *on* the
platform, and it is mostly Swedish.

Det här repot är **VibePulse**: appen som visar Claude Code- och
Codex-kvoter på en Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480). Appen kör
på **Torget**, en liten LVGL-appplattform som bor i samma repo och äger
panelen, WiFi, ljuset och launchern — därav alla `torget_*`-namn i koden och
`torget.bin` som byggresultat. En skärm = en binär = ett bygge här.
MIT-licens.

**En färsk klon utifrån bygger EN app: VibePulse.** Det är hela innehållet.
Ser du referenser till Solelkollen eller Vibbe/Buddy i den här filen är det
underhållarens egna sidoprojekt, som bor i egna repon och bara byggs in om
de råkar finnas utcheckade — se `## Status`. Du behöver dem inte, och deras
frånvaro är det normala.

Struktur, byggkommandon och hur man skriver en app: **README.md**.
Designsystemet: **spec/ui-spec.md**. Hårdvarusanningen routas under
`Hardware-aware work` nedan; läs den kanoniska femfilslistan där före
hårdvaruarbete.

## Status (2026-08-13, första flashen gjord)

Plattformen bröts ut ur underhållarens tidigare solcells-firmware (den
historiken ligger i ett privat repo och är inget du behöver) och stöptes om
enligt granskningens tre krav: (1) versionerat appkontrakt
`torget_app_t` + appregister i main/registry.c som launchern läser;
(2) nätverk/hämtning bor i varje apps egen komponent (net.c), plattformen
äger bara WiFi/SNTP/lås/ljus/rotation; (3) MIT-licens.

**Det här repot innehåller EN app: VibePulse** (Claude/Codex-användning via
tools/tokenserver på macOS, Linux eller Windows, platt JSON över LAN). Den ligger först i
registret, så en färsk klon utifrån bygger en binär som startar i VibePulse
och ingenting annat.

Solelkollen och Vibbe/Buddy är egna produkter i egna repon och dras in som
companion-inputs när de finns utcheckade — `TORGET_SOLELKOLLEN_DIR`
(default `~/Solelkollen/components`) respektive `TORGET_BUDDY_DIR` (default
`~/Buddy/components`). Byggena sätter `TORGET_HAVE_SOLELKOLLEN` /
`TORGET_HAVE_BUDDY` och registerposterna i main/registry.c är grindade på
dem. Exakt källrevision för companion-inputen finns i
`spec/hardware-sources.yaml`. En skärm = en binär: har du båda utcheckade
får du alla tre apparna i samma bygge, växlingsbara i launchern.

**Detta repo äger nu glaset.** Den första fysiska flashen gjordes 2026-08-13
med användarens uttryckliga tillåtelse, från mergad main; den statiska
fysiska grinden är PASSERAD för kvotsidorna, agentmonitorn och Max Tracker,
och Solceller-kopian är avvecklad som skärmens drivrutin. Evidens:
`docs/superpowers/reviews/2026-08-13-max-tracker-physical-static.md`.
Fortfarande ogrindat: rörelse/animation — den kräver
interaktionsprotokollet i AMOLED-skillen, mätt på panelen först.

## Arbetsregler

- **Bänk/sim först, flash sen.** Simulatorn är specen; en obevakad körning
  dumpar /tmp/torget-*.bmp som pixelfacit.
- **Värdlagren är tunna.** platform/, components/app_* (utom net.c) och
  kärnkomponenterna delas byte-identiskt mellan sim och target. UI-beteende
  hör hemma i appen/platform/, aldrig i main/main.c eller sim/main.c.
- **Kontraktet är heligt.** Bryts torget_app.h eller torget.h bumpas
  TORGET_APP_API_VERSION — launchern hoppar över appar med fel version.
- **Biblioteksregeln:** skörda vid ANDRA användningen, bygg aldrig i förväg
  (torget_fmt, torget_ticker, torget_net skördades när VibePulse, då kallad
  Tokenmätaren, blev andra användaren — det är mallen).
- **Ärlighetsinvarianten:** aldrig påhittade nollor — utan data visas
  streck; räknare backar aldrig; copyn säger vad siffran faktiskt mäter.

## AMOLED visual work

Use `.claude/skills/iterating-esp32-amoled-ui/SKILL.md` for AMOLED work. Show
exact 480 x 480 output at meaningful stages. Review the static physical AMOLED
before motion. Studio approval never authorizes a flash; obtain explicit user
authorization for the physical install.

## Logs, errors, and learning from mistakes

`docs/observability.md` maps every log the system generates (device serial,
tokenserver stdout/service-manager log file, `GET /` diagnostics, state
files, the screen itself) and contains the periodic comb routine — follow it when asked to
comb, audit, or investigate logs or odd behavior. Findings go to
`docs/observability-backlog.md`. Read `docs/lessons.md` before touching
pollers, parsers, staleness logic, or the autostart setup; fixes with a
root-cause story add an entry there.

## Hårdvarufällorna i kortform (detaljer i spec/hardware.md)

- `bsp_display_lock()` LJUGER (esp_err_t genom bool, spegelvänt) — använd
  `esp_lv_adapter_lock(-1)` direkt (plattformens torget_ui_lock gör rätt),
  annars heapkorruption och eviga loopar.
- LVGL 9.5: `lv_span_set_text` ritar INTE om — explicit `lv_spangroup_refresh`.
- För IMU-adress och fysisk verifiering gäller capability
  `sensors.imu-qmi8658`; komponentens header deklarerar `read_accel_mg`, som
  saknas i källan — använd `read_accel`.
- Rotationskalibrering: SG_QUAD_UP 1, SG_QUAD_DIR -1, trösklar med
  iterationshistorik i rotation.c. Kalibrera fysisk hårdvara med ETT
  strukturerat flerlägestest, aldrig fotoforensik; en konstant per iteration.
- S3:an är 2,4 GHz-only; bootskanningen i loggen är facit för vilka nät som
  finns. MADCTL och touch roteras ALLTID i samma grepp.
- ESP-IDF 5.5.2 i `~/esp/esp-idf` (`. ~/esp/esp-idf/export.sh`), LVGL pinnad
  9.5.0 i BÅDE sim och target (bump båda i samma commit).

## Medvetet SENARE (bygg inte förrän triggern slår)

Vibbe/Buddy är underhållarens companion-app via `~/Buddy/components` och
ingår inte i en färsk klon. `audio.microphones`, `audio.speaker-output` och
`usb.device` är
firmware-enabled genom det build-inputet; exakt revision och evidens finns i
`spec/hardware-sources.yaml`. Fysisk mikrofon-/högtalarfunktion är fortfarande
overifierad.

- WiFi-provisionering + OTA + konfig-UI — trigger: första enheten som lämnar
  huset (kompilerad secrets.h duger inte för sålda enheter; ESP-IDF:s
  wifi_provisioning finns färdig).
- Responsiv layout för andra Waveshare-storlekar — trigger: andra skärmtypen.
- Butiks-/paketmaskineri och appar i egna repon via Espressifs registry —
  trigger: bevisad traktion efter open source.
- Röststyrning för befintliga Vibbe/Buddy är kandidat/senare tills privacy-UI,
  fysisk mikrofon-/högtalarverifiering samt full-duplex-, audio- och
  nätverksvalidering finns. Kandidatstatus är inte auktoriserat arbete;
  molntjänst och betalningsmodell är separata produktbeslut.

## Hardware-aware work

Before proposing external hardware, declaring a device limitation, or designing
a hardware-dependent feature, read `spec/hardware.md`,
`spec/hardware-capabilities.yaml`, `spec/hardware-sources.yaml`,
`spec/device-units.yaml`, and `spec/hardware-opportunities.md`. State whether
the idea is only silicon-capable, board-wired, firmware-enabled, and
physically verified on the named unit. Mention a relevant
unused onboard capability when it materially improves the request.
Never copy secrets or turn an opportunity into authorized implementation work.
