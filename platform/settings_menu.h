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

/* Skapas en gång vid start, under anroparens UI-lås — som OTA-overlayn och
 * setupfönstret. Ingen allokering sker sedan när menyn öppnas. */
void torget_settings_create(void);

void torget_settings_open(void);
void torget_settings_close(void);
bool torget_settings_open_p(void);

/* Vad ett tryck på en rad gör. Touch-callbacken delegerar hit, så
 * simulatorn och värdtesterna kan driva EXAKT samma väg som ett finger i
 * stället för en egen QA-bakdörr som kunde hinna glida isär från den. */
void torget_settings_click_row(tg_settings_row row);

/* Hämtar och nollställer en väntande avsikt. Returnerar NONE när inget
 * väntar. Anropas från samma task som äger fönsterordningen. */
tg_settings_intent torget_settings_take_intent(void);

/* ABOUT-raderna. Värden ägs av värden; menyn kopierar dem och visar streck
 * för det som saknas — aldrig en påhittad nolla eller tom rad. */
void torget_settings_set_about(const char *version, const char *ip,
                               bool computer_found);

#endif
