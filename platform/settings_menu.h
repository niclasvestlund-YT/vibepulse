#ifndef TORGET_SETTINGS_MENU_H
#define TORGET_SETTINGS_MENU_H

#include <stdbool.h>

/*
 * SETTINGS — det som ett tresekundershåll på KEY3 öppnar.
 *
 * Menyn ÄGER ingenting. Den är en vägvisare till fönster som redan finns och
 * är oförändrade: UPDATE öppnar OTA-underhållsfönstret, WIFI öppnar
 * setupfönstret. Samtyckesmodellen står kvar precis som den var — fönstren
 * öppnas fortfarande bara från enheten, aldrig av ett skript, och tokenet och
 * tiominutersfönstret är orörda. Det enda som ändras är att hållet landar i en
 * meny i stället för att gissa vilket av två fönster användaren menade.
 *
 * ABOUT visar version, IP och om datorn hittats. Aldrig en hemlighet: inga
 * tokens, ingen enhetsnyckel, inget lösenord. Raderna är desamma som redan
 * loggas och syns på glaset i andra lägen.
 *
 * FEATURES och PAIR står i specen men finns inte här ännu: FEATURES kräver att
 * internminnesbudgeten mäts om på enheten först (GitHub-sidan blir alltid
 * resident när kompileringsvakterna blir runtime-val), och PAIR hör till
 * sekvenssteg 4. En rad som inte gör något vore ett löfte skärmen inte kan
 * hålla, så de ritas inte förrän de fungerar.
 */

typedef enum {
  TG_SETTINGS_ROW_UPDATE,
  TG_SETTINGS_ROW_WIFI,
  TG_SETTINGS_ROW_ABOUT,
  TG_SETTINGS_ROW_COUNT,
} tg_settings_row;

/* Vad menyn vill att värden gör härnäst. Menyn rör aldrig OTA:n eller nätet
 * själv — main.c äger ordningen mellan fönstren (porten är delad) och får
 * beslutet som ett värde i stället för ett sidoeffektsanrop. */
typedef enum {
  TG_SETTINGS_INTENT_NONE,
  TG_SETTINGS_INTENT_OPEN_UPDATE,
  TG_SETTINGS_INTENT_OPEN_WIFI,
} tg_settings_intent;

/*
 * LÅSREGELN, och den skiljer sig från OTA-overlayn och setupfönstret med
 * flit: de tar torget_ui_try_lock(200) själva, för de anropas från ANDRA
 * tasks (nättasken, OTA-tjänsten). Menyn anropas bara från LVGL-tasken —
 * tick_cb öppnar och stänger den, och radernas callbacks är LVGL-events —
 * där låset redan är taget. Ett try_lock här hade därför inte skyddat något
 * utan stallat ticken i 200 ms och sedan tyst gjort ingenting, vilket är
 * exakt varför main.c:s KEY3-block aldrig får kalla torget_ota_ui_set.
 * Alla funktioner nedan: kallas UNDER torget_ui_lock(), aldrig utanför.
 */

/* Skapas en gång vid start. Ingen allokering sker sedan när menyn öppnas. */
void torget_settings_create(void);

/* ABOUT-raderna tas som en ögonblicksbild när menyn öppnas, inte som en
 * ström: värdena bor i andra tasks, och en meny som läste dem live hade
 * behövt lås eller atomics för två rader som visas i några sekunder.
 * ``ip`` NULL eller tom betyder ingen adress — raden visar streck, och
 * UPDATE tonas ner, för ett OTA-fönster utan adress kan aldrig ta emot
 * en uppladdning. Att erbjuda det ändå vore ett löfte skärmen inte kan
 * hålla.
 *
 * Det finns medvetet INGEN "COMPUTER"-rad. Den skrevs först mot
 * ``s_data_alive``, som bara sätts till true en gång och aldrig tillbaka —
 * raden hade sagt FOUND för alltid efter en enda lyckad hämtning, även med
 * datorn borta, och även när siffrorna kom via reläet i stället för en
 * upptäckt dator. Ingen befintlig signal betyder det etiketten påstår, och
 * en rad som säger något falskt är värre än en rad som inte finns. Den
 * kommer tillbaka när det finns ett värde som bär den. */
void torget_settings_open(const char *version, const char *ip);
void torget_settings_close(void);
bool torget_settings_open_p(void);

/* Vad ett tryck på en rad gör. Touch-callbacken delegerar hit, så
 * simulatorn och värdtesterna kan driva EXAKT samma väg som ett finger i
 * stället för en egen QA-bakdörr som kunde hinna glida isär från den. */
void torget_settings_click_row(tg_settings_row row);

/* Hämtar och nollställer en väntande avsikt. Returnerar NONE när inget
 * väntar. Anropas från samma task som äger fönsterordningen. */
tg_settings_intent torget_settings_take_intent(void);

#endif
