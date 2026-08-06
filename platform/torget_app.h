#ifndef TORGET_APP_H
#define TORGET_APP_H

#include "lvgl.h"

/*
 * Torgets appkontrakt — den enda ytan mellan en app och plattformen.
 * Kontraktets stabilitet är det som möjliggör tredjepartsappar (P25-VISION):
 * en app som bygger mot version N ska aldrig tyst gå sönder mot version N.
 * Bryter vi något här bumpas TORGET_APP_API_VERSION, och launchern vägrar
 * appar med fel version i stället för att krascha halvvägs in i create().
 *
 * En app är: ett namn, en ikon och tre livscykelfunktioner. Allt annat —
 * datakällor, endpoints, hämtkadens, parser — är appens ensak och bor i
 * appens komponent (glance-mönstret: platt publik JSON, tal inte strängar,
 * en takt så appen kan ticka lokalt mellan hämtningarna).
 */

#define TORGET_APP_API_VERSION 1

/* Launcherns ikon, deklarativ: en platta med en glyf och en accentprick.
 * Samma proportioner som bänkens ikon (96-platta, radie 22, 64-pixelglyf).
 * Vill en app ha en riktig bild i stället är det en kontraktsversion till —
 * glyfen räcker för de två första apparna och kostar tiotals kB mindre. */
typedef struct {
  const lv_font_t *font;  /* glyffont, t.ex. plex_icon_64 */
  const char *glyph;      /* "S", "T", ... — måste finnas i fontens range */
  uint32_t plate_hex;     /* plattans färg, 0xRRGGBB */
  uint32_t glyph_hex;     /* glyfens färg */
  uint32_t dot_hex;       /* accentprickens färg; 0 = ingen prick */
} torget_icon_t;

typedef struct {
  int api_version;        /* sätt alltid TORGET_APP_API_VERSION */
  const char *name;       /* versaler, visas under ikonen: "SOLELKOLLEN" */
  torget_icon_t icon;

  /* Bygg appens hela UI i root (en 480×480-låda plattformen äger och
   * visar/gömmer). Körs EN gång vid boot, under UI-låset, innan nätet är
   * uppe. Starta appens egen hämttask här — den ska själv vänta på
   * torget_net_wait() innan den rör nätverket. */
  void (*create)(lv_obj_t *root);

  /* Appen blir synlig respektive lämnar skärmen. Plattformen sköter själva
   * visandet/gömmandet av root — det här är för appens egen hushållning
   * (pausa animationer, sänka kadens, ...). Får vara NULL. */
  void (*enter)(void);
  void (*leave)(void);
} torget_app_t;

/* Appregistret — byggets lista över inpluggade appar, i launcherordning.
 * Definieras i main/registry.c (delas byte-identiskt med simulatorn):
 * registret är BYGGETS konfiguration, inte plattformens eller appens. */
extern const torget_app_t *const torget_apps[];
extern const int torget_app_count;

#endif
