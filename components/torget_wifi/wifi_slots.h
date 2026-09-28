#ifndef TORGET_WIFI_SLOTS_H
#define TORGET_WIFI_SLOTS_H

/*
 * Nätverkslistans rena kärna: validering, platsval, vräkning och ordningen
 * kandidaterna provas i. INGEN ESP-IDF här — filen värdtestas i
 * test/run.sh (test_wifi_slots.c), precis som ota_policy och button_policy.
 * Adaptrarna (wifi_creds.c mot NVS, wifi_setup.c mot radion) får röra
 * hårdvara men aldrig bära ett beslut som inte redan är låst här.
 *
 * Modellen: panelen minns upp till TG_WIFI_SLOTS platser i NVS. Varje
 * lyckad uppkoppling höjer platsens seq, så listan sorterar sig själv med
 * det som senast fungerade först och vräker det som varit oanvänt längst
 * när en ny plats ska in. Näten ur secrets.h är INTE med i NVS — de läggs
 * sist som en oföränderlig botten, så ingen felkonfiguration någonsin kan
 * göra hemnätet oåtkomligt och panelen ounderhållbar.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* 32 byte SSID och 63 byte WPA2-PSK är 802.11:s tak; +1 för NUL. */
#define TG_WIFI_SSID_CAP 33
#define TG_WIFI_PASS_CAP 64

/* Sex ihågkomna platser: hem, kontor, telefonens hotspot och tre resor
 * ryms utan att någon vräks. Blobben blir 6 x 101 byte — struntsumma i en
 * NVS-partition på 64 kB. */
#define TG_WIFI_SLOTS 6

typedef struct {
  char ssid[TG_WIFI_SSID_CAP];
  char pass[TG_WIFI_PASS_CAP]; /* tom sträng = öppet nät */
  uint32_t seq;                /* 0 = tom plats; högre = senare lyckad */
} tg_wifi_slot;

/* Missar i rad på ETT nät innan nästa kandidat provas. Fyra ≈ 10-15 s:
 * snabbt nog för bilen, trögt nog att inte fladdra när hemnätet har en
 * dålig stund (beslut 2026-08-07, oförändrat). */
#define TG_WIFI_MISSES_BEFORE_SWITCH 4

/* Setupfönstrets tider. AUTO: så länge utan IP innan glaset självt öppnar
 * fönstret — långt nog att en routeromstart hemma aldrig hinner dit,
 * kort nog att man inte står och väntar på ett hotellrum. COOLDOWN: paus
 * efter ett stängt fönster så vakten inte tuggar upp och ner. WINDOW: tio
 * minuter, samma tak som OTA-fönstret. LINGER: efter IP hålls AP:n kvar en
 * stund så klienten hinner läsa svaret innan nätet byts under den. */
#define TG_WIFI_SETUP_AUTO_US     (90LL * 1000000LL)
#define TG_WIFI_SETUP_COOLDOWN_US (120LL * 1000000LL)
#define TG_WIFI_SETUP_WINDOW_US   (600LL * 1000000LL)
#define TG_WIFI_SETUP_LINGER_US   (5LL * 1000000LL)

/* Setupfönstrets synliga livscykel. STARTING finns uttryckligen eftersom
 * accesspunkt + skanning tar sekunder: knappen och glaset måste ägas redan
 * när användarens håll godkänns, inte först när AP:n är färdig. */
typedef enum {
  TG_WIFI_PHASE_IDLE = 0,
  TG_WIFI_PHASE_STARTING,
  TG_WIFI_PHASE_OPEN,
  TG_WIFI_PHASE_JOINING,
  TG_WIFI_PHASE_JOINED,
  TG_WIFI_PHASE_FAILED,
} tg_wifi_setup_phase;

/* Telefonportalens anslutningsresultat. Bara denna grova status lämnar
 * panelen via /status; SSID och lösenord gör det aldrig. */
typedef enum {
  TG_WIFI_JOIN_IDLE = 0,
  TG_WIFI_JOIN_CONNECTING,
  TG_WIFI_JOIN_CONNECTED,
  TG_WIFI_JOIN_RETRY_PASSWORD,
  TG_WIFI_JOIN_RETRY_NOT_FOUND,
  TG_WIFI_JOIN_RETRY_CONNECTION,
} tg_wifi_join_status;

/* Ett versionsnummer gör att samma formulärpost aldrig appliceras två
 * gånger av vakten, men en ny post alltid kan prova igen. Noll betyder
 * "ingen post". */
bool tg_wifi_join_should_apply(uint32_t submitted, uint32_t applied);

/* Översätt ESP-IDF:s stabila disconnect-koder till begriplig, hemlighetsfri
 * portalstatus. Noll betyder att radion fortfarande försöker. */
tg_wifi_join_status tg_wifi_disconnect_status(int reason);

/* A transient disconnect is not terminal while the same trial keeps retrying.
 * Accept fresh IP proof once, never an old IP or an abandoned/failed-start trial. */
bool tg_wifi_join_should_accept(tg_wifi_join_status status, bool trial_started,
                               bool applied_now, bool have_ip);

/* Alla synliga faser äger KEY3. STARTING slukar däremot den utlösande
 * knappens släpp i stället för att omedelbart stänga eller växla app. */
bool tg_wifi_setup_owns_input(tg_wifi_setup_phase phase);
bool tg_wifi_setup_can_close(tg_wifi_setup_phase phase);

/* Så länge utan IP innan nätsidan tar glaset. Sextio sekunder är valt mot
 * två grannar: bootskärmen äger de första 45 (den ger upp där, och två
 * lager som slåss om samma pixlar hjälper ingen), och en router som startar
 * om ska hinna tillbaka utan att hela skärmen svartnar. Efter det står
 * apparnas NO DATA kvar bakom — men glaset säger också VARFÖR. */
#define TG_WIFI_SEARCH_UI_US      (60LL * 1000000LL)

/* 1-32 byte, inga styrtecken. SSID är en bytesträng, inte text: skiftläge
 * är signifikant och jämförs exakt. */
bool tg_wifi_ssid_valid(const char *ssid);

/* Tom sträng (öppet nät) ELLER 8-63 skrivbara ASCII-tecken. En PSK som är
 * kortare än 8 kan aldrig ha fungerat — att lagra den vore att spara en
 * plats som garanterat aldrig ansluter. */
bool tg_wifi_pass_valid(const char *pass);

/* Index för ssid i listan, annars -1. Tomma platser hoppas över. */
int tg_wifi_slot_find(const tg_wifi_slot *slots, int n, const char *ssid);

/* Platsen ett nytt nät ska skrivas på: samma SSID om det redan finns
 * (uppdatering, inte dubblett), annars första tomma, annars den med lägst
 * seq — den som varit oanvänd längst. Returnerar alltid ett giltigt index
 * för n > 0. */
int tg_wifi_slot_for_write(const tg_wifi_slot *slots, int n, const char *ssid);

/* Nästa seq att stämpla en lyckad uppkoppling med. Mättar vid UINT32_MAX
 * i stället för att slå runt och göra den nyaste platsen till den äldsta. */
uint32_t tg_wifi_seq_next(const tg_wifi_slot *slots, int n);

/* Bygger provordningen: ihågkomna platser med högst seq först, därefter de
 * fasta näten ur secrets.h i sin ordning. Tomma SSID hoppas över och ett
 * SSID som redan lagts in tas inte igen — så ett hemnät som BÅDE står i
 * secrets.h och hunnit hamna i NVS provas en gång, inte två. Pekarna i out
 * pekar in i indataarrayerna; inget kopieras. Returnerar antalet. */
int tg_wifi_candidates(const tg_wifi_slot *remembered, int n_remembered,
                       const tg_wifi_slot *fixed, int n_fixed,
                       const tg_wifi_slot **out, int out_max);

/* Dags att byta kandidat? */
bool tg_wifi_should_switch(int misses);

/* Ska setupfönstret öppna sig självt? Sant först när panelen varit utan IP
 * i TG_WIFI_SETUP_AUTO_US och inget fönster stängdes alldeles nyss. En
 * panel med IP öppnar aldrig — då finns det inget att laga.
 *
 * no_ip_since_us: när nuvarande IP-lösa period började (0 = har IP nu).
 * last_close_us: när förra fönstret stängdes (0 = inget har stängts). */
bool tg_wifi_setup_should_open(bool have_ip, int64_t now_us,
                               int64_t no_ip_since_us, int64_t last_close_us);

/* Ska den ärliga nätsidan synas? Bara efter TG_WIFI_SEARCH_UI_US utan IP,
 * så bootskärmen får äga uppstarten ostört och en kort routersvacka aldrig
 * svartnar hela glaset. */
bool tg_wifi_search_ui_visible(bool have_ip, int64_t now_us,
                               int64_t no_ip_since_us);

/*
 * DMA-grindarna för setupfönstret. Panelflushen behöver ETT sammanhängande
 * DMA-block (flush_bytes); faller största fria blocket under det dör nästa
 * flush i NO_MEM och hela ritpipen fastnar tyst — glaset fryser med levande
 * system bakom (obducerat 2026-08-16, och huvudmisstänkt i kilningen
 * 2026-08-17 när accesspunkten kördes första gången på riktig hårdvara).
 *
 * ÖPPNA kräver x2 plus en fast 8 KiB-startreserv för portalens taskar.
 * FORTSÄTT (mätt igen efter APSTA-bytet, den dyra biten) kräver x2 — under
 * det river vi hellre fönstret än fryser glaset. Ett vägrat fönster är en
 * loggrad och ett nytt försök senare; en fryst panel är en USB-räddning.
 *
 * Kalibrering mot UPPMÄTT verklighet, inte känsla: v0.7.0 med den rättade
 * 256 KiB LVGL-poolen, Wi-Fi-signalen och reläklienten har en stabil frisk
 * baslinje på 31744 byte på torget-home-01 (serielogg 2026-08-21). Den
 * gamla x3-grinden krävde 34560 och gjorde därför telefonsetup omöjlig.
 * 2 x 11520 + 8192 = 31232 släpper den friska panelen igenom men behåller
 * både den etablerade x2-frysvarningen och en uttrycklig startreserv. Den
 * gamla kilningens rotorsak är dessutom fastställd: den effektiva LVGL-
 * poolen var 96 KiB, inte 256 KiB; DMA-brist var bara den tidiga hypotesen.
 */
#define TG_WIFI_SETUP_DMA_OPEN_FACTOR  2
#define TG_WIFI_SETUP_DMA_OPEN_RESERVE_BYTES 8192u
#define TG_WIFI_SETUP_DMA_ABORT_FACTOR 2

bool tg_wifi_setup_dma_ok_to_open(size_t largest_dma, size_t flush_bytes);
bool tg_wifi_setup_dma_ok_to_continue(size_t largest_dma, size_t flush_bytes);

/* Ska ett öppet fönster stängas? Antingen har tiden runnit ut, eller så
 * har uppkopplingen lyckats och klienten fått sina LINGER-sekunder.
 * joined_ip_us: när IP:t kom under fönstret (0 = ingen uppkoppling än). */
bool tg_wifi_setup_should_close(int64_t now_us, int64_t opened_us,
                                int64_t joined_ip_us);

#endif
