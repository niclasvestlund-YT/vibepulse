# Granskning: Tokenmätaren som agentmonitor

**Granskare:** Claude (plattformsbyggaren), 2026-08-06
**Underlag:** Codex designspec `agentmonitor-designspec.md` + en dags fysisk
verifiering av Torget-plattformen på hårdvaran.

**Helhetsdom: bygg den.** Specen respekterar Torgets regler (ärlighets-
invarianten, bänk-först, nätverk-i-appen, inga bibliotek i förväg,
versionerade kontrakt) och tillståndsmodellen är rätt tänkt — särskilt
"tystnad ger aldrig KLAR" och tvåminutersleasen. Nedan är det plattformen
KRÄVER, med siffror från dagens fysiska felsökning. Punkterna är ordnade
efter hur dyra de är att upptäcka sent.

## 1. Renderingsbudgeten är hårdare än specen tror

- **Flushpipen går i 24-radsremsor.** main.c äger displaystarten med
  `buffer_height = 24` (≈23 KB per SPI-sändning). Det är INTE en default
  utan en frysningsfix: största sammanhängande DMA-blocket i internminnet
  är ~31 KB, och varje flush bounce-kopieras dit (ritbuffertarna bor i
  PSRAM). En full 480×50-flush à 48 KB dödade ritpipen permanent
  (`ESP_ERR_NO_MEM`-lavin). **Rör aldrig bufferthöjden uppåt.**
- Petens 220×220-yta vid 6–8 fps är ok bandbreddsmässigt (≈10 remsor à
  ~10 KB per frame), men **invalidera bara petens yta** — aldrig hela
  skärmen per frame. Pixeldriften fullinvaliderar redan en gång i minuten;
  det är taket för fullskärmsarbete.
- **Ingen lv_canvas för peten.** LVGL:s widgetpool är 96 KB statisk
  (CONFIG_LV_MEM_SIZE_KILOBYTES) och redan dimensionerad för tre appvyer +
  launcher. En 220×220 ARGB-canvas är ensam 193 KB. Använd i stället
  flashlagrade `lv_image`-assets: 1-bitars/A8-mask + 
  `lv_obj_set_style_image_recolor` för färgtillstånden (vitt/korall/amber/
  rött ur SAMMA mask — precis som specen vill), och Codex-lagren (`>`, `_`)
  som egna småbilder. Kolla `lv_mem_monitor` i bänken efter overlaybygget.

## 2. Fonten för huvudordet finns inte än

`plex_icon_64` innehåller exakt två glyfer (S, T). Huvudordet behöver en ny
range — minst `JOBBARVÄNTKLFE` plus vad underraderna kräver, inklusive
**Ä (0xC4)**. Pipeline finns: `platform/fonts/fetch-and-convert.sh`
(node + TTF:er ligger lokalt; de genererade .c-filerna committas, bygget
kräver aldrig nät). En 64px-range med ~15 versaler kostar ~30–40 KB flash —
ok. Lägg den i fontskriptet så sim och target får den samtidigt
(fonterna kompileras i `torget_app`-komponenten — INTE i main; apparna
länkar dem den vägen, lärdom från första länkfelet).

## 3. Nätverket: 1 Hz-pollen behöver en långlivad klient

`torget_http_get` skapar och river en esp_http_client per anrop. För
30-sekunderskadensen är det fint; **vid 1 Hz blir det ~3 600 sockets/timme**
med TIME_WAIT-churn i lwIP och onödig heappress (lägsta interna heapen har
varit nere på ~7 KB under TLS-rusning — marginalen är inte oändlig).
Statuspollen ska ha EN återanvänd klient med keep-alive i appens egen
statustask (eller 2 s-kadens om det krånglar). Följ `torget: heap:`-raden
i loggen (10 s-intervall) före/efter — största DMA-blocket får inte krypa
under ~24 KB.

Övrigt etablerat som ska följas: tasken startas i `create()` men väntar på
`torget_net_wait()` (eventgruppen skapas FÖRST i app_main — bootloop
annars, dagens första fysiska fynd); all apply sker under
`torget_ui_lock()`; `torget_keep_awake()` kallas under låset.

## 4. Leasen måste bo på ENHETEN också

Specen lägger tvåminutersleasen i servern — rätt — men endpointen kan dö
medan skärmen visar `JOBBAR`. Appen behöver samma lease lokalt med
`torget_now_us()`: färskaste giltiga statuspaketets ålder > 2 min ⇒
`unknown` och overlayn lämnas. (Detta är ordagrant samma mönster som
apparnas stale-tröskel på 120 s — återanvänd formen.)

## 5. Overlayintegration: tre fällor

- Overlayn byggs som **syskon till tileviewen i appens root** med
  HIDDEN-flagga (exakt launcher-mönstret). Ingen ny roothierarki i
  plattformen.
- **Tap-kvittensen får inte äta långtrycket**: registrera
  `LV_EVENT_LONG_PRESSED → torget_launcher_open()` även på overlayn
  (etablerad inputmodell), och kvittera på `LV_EVENT_CLICKED`.
- **KEY3/`torget_app_next()` växlar app när som helst** — overlaystatus
  ska ligga i appens tillstånd, inte i vyn, så att växel bort/tillbaka
  återställer rätt skärm. (Regressionsprovet i specen behövs på riktigt:
  app_show-buggen där apparna ritades ovanpå varandra hittades av just
  KEY3 i dag, fixad i bc05f16.)

## 6. Ljudet: budget ok, ordning viktig

- `espressif__esp_codec_dev` finns redan i byggets komponentgraf (BSP-
  beroende) — kolla BSP:ns audio-API innan egen I2S-rördragning skrivs.
  PA-enable är GPIO46 (spec/hardware.md).
- **I2S-DMA-buffertar bor i internminnet**: dimensionera små (några KB)
  och allokera EN gång vid init — inte per uppspelning. Verifiera
  heap-raden efter ljudinit; internminnet är den knappa resursen.
- Flashbudget: 22 050 Hz × 16 bit mono = 44 KB/s ⇒ tre fraser à ~2 s ≈
  265 KB. Appartitionen har i dag ~2,6 MB fritt — ok, men mät efter.
- Ljudtask: egen, låg prioritet, matas via kö, aldrig under UI-låset.
  NVS är redan initierat i main — använd eget namespace för event-dedup.

## 7. Serversidan

- Statusmaskinen ska svara ur minne — återanvänd trådlås-mönstren som
  redan finns i tokenserver.py (probe-/scan-lås + throttle).
- **Definiera "uttryckligt slut" för Claude Code exakt.** JSONL-loggarna
  har `type:"result"`-poster i `-p`-läge, men interaktiva sessioner
  saknar en entydig "klar"-händelse — ett turslut är INTE done (nästa
  userprompt kan komma om två sekunder). Hellre `waiting`/`unknown` än
  en lögnaktig `KLAR`; specen säger rätt sak, implementationen måste
  våga vara tråkig här.
- Codex rollout-läsning: `resets_at`-vakten och glob-över-datumkataloger
  finns redan som mönster i `_read_codex_limits` — samma försiktighet
  (filrotation, halvskrivna sista rader) gäller watchern.
- Sanering (längdtak, kontrolltecken, okända enum → unknown) ska ha
  hosttester precis som C-parserns kontraktsregler.

## 8. Bänk och verifiering (verktygen finns redan)

- Statusfixtures + en tangent (förslag: S cyklar tillstånden) i sim/main.c;
  BMP-turen (`platform_tour_cb`) utökas med working/waiting/done per
  leverantör — mönstret är etablerat och granskningen sker på bild före
  flash.
- C-statusparsern testas i test/run.sh-sviten: version, error-shape,
  null-fält, fientliga indata — kopiera formen från test_tokens.c
  (och använd `strlen`, inte hårdkodade längder).
- Fysiskt protokoll: heap-raden var 10:e sekund är redan i loggen — kravet
  "ingen växande allokering på 30 min" är direkt avläsbart där.

## 9. Småsaker som annars kostar en kväll var

- LVGL 9.5: `lv_span_set_text`/textbyten ritar inte om själva —
  `lv_spangroup_refresh`/invalidering krävs (dokumenterad fälla).
- Aldrig `bsp_display_lock()` — spegelvänd boolretur; alltid
  `torget_ui_lock()`.
- MADCTL, panel-gap och touch ägs numera av main.c
  (`torget_display_rotation_set`) — overlayn behöver aldrig röra dem,
  och SKA inte.
- En avbruten flashning ger "invalid segment length"-bootloop med svart
  skärm — det ser ut som en död enhet men läks med en ren omflash.
- Animationstimers kör i LVGL-tasken (16 KB stack) — inga stora
  stackbuffertar eller printf-spam i 8 fps-callbacks.

## Svar på specens öppna punkt

Filsnittet (`agent_status.c/h`, `agent_monitor.c/h`, `agent_audio.c/h` i
appen + `agent_status.py` bredvid tokenservern) är rätt; ingen del av det
här hör hemma i platform/ eller som egen komponent förrän en andra app
behöver det (biblioteksregeln). Leveransordningen i specen är bra — men
lägg fysisk AMOLED-granskning EFTER steg 3 också (en statisk overlay på
glaset avslöjar storleks-/kontrastfel innan animationsjobbet börjar;
dagens fotogranskning fångade två buggar bänken missat).
