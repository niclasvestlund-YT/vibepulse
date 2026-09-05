#include "settings_menu.h"

#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "torget.h"

/*
 * Samma raster som OTA-ringen och setupfönstret: äkta svart, lägesordet överst
 * i attention-fonten, muted för sammanhang och vitt för svaret. Ingen
 * provideraccent — det här är plattformens läge, inte en apps. Native
 * fontstorlekar, aldrig transformer (lagerläxan 2026-08-16).
 *
 * Radernas geometri är avsiktligt IDENTISK med setupfönstrets MANUAL
 * SETUP-kontroll (x 74, bredd 332, radie 28, 2 px muted kant): den knappen är
 * redan bevisad på glaset, och en meny som ritar sina träffytor någon
 * annanstans hade varit ett nytt fysiskt påstående utan täckning.
 */

extern const lv_font_t plex_attention_52;
extern const lv_font_t plex_body_27;
extern const lv_font_t plex_ui_21;

#define COL_MUTED lv_color_hex(0x9298A2) /* palette.muted */

/* Innehållet hålls i en mittkolumn: glaset har klippta hörn, så text som
 * söker kanterna tappar tecken (hörnlärdomen från brödsmulorna). */
#define CONTENT_W 400

/* Saved in design/vibepulse/settings-design.json and checked against it in
 * test_settings_design.py. Keep these raw integer lines simple: the validator
 * deliberately parses them instead of trusting comments. */
#define SETTINGS_WORD_Y         24
#define SETTINGS_ROW_X          74
#define SETTINGS_ROW_WIDTH      332
#define SETTINGS_ROW_HEIGHT     84
#define SETTINGS_ROW_GAP        16
#define SETTINGS_FIRST_ROW_Y    120
#define SETTINGS_ROW_RADIUS     28
#define SETTINGS_ROW_BORDER_W   2
#define SETTINGS_FOOTER_Y       442

#define SETTINGS_ABOUT_FIRST_LINE_Y 140
#define SETTINGS_ABOUT_LINE_GAP     62
#define SETTINGS_ABOUT_BACK_Y       285

#define ABOUT_VALUE_CAP 40
#define ABOUT_ROWS 2

typedef enum {
  VIEW_MENU,
  VIEW_ABOUT,
} settings_view;

static struct {
  lv_obj_t *overlay;
  lv_obj_t *word;
  lv_obj_t *foot;
  lv_obj_t *rows[TG_SETTINGS_ROW_COUNT];
  lv_obj_t *row_labels[TG_SETTINGS_ROW_COUNT];
  /* ABOUT: två etikett/värde-par plus en BACK-kontroll som återanvänder
   * radgeometrin. Ingen COMPUTER-rad — se headern för varför. */
  lv_obj_t *about_labels[ABOUT_ROWS];
  lv_obj_t *about_values[ABOUT_ROWS];
  lv_obj_t *about_back;
  lv_obj_t *about_back_label;
  settings_view view;
  bool open;
  tg_settings_intent pending;
  char version[ABOUT_VALUE_CAP];
  char ip[ABOUT_VALUE_CAP];
  bool about_dirty;
} ui;

static const char *const ROW_TEXT[TG_SETTINGS_ROW_COUNT] = {
  "UPDATE", "WIFI", "ABOUT",
};

static const char *const ABOUT_LABEL[ABOUT_ROWS] = {
  "FIRMWARE", "ADDRESS",
};

static void show(lv_obj_t *obj, bool visible) {
  if (!obj) return;
  if (visible) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

static lv_obj_t *line(lv_obj_t *parent, const lv_font_t *font, lv_color_t color,
                      int y) {
  lv_obj_t *label = lv_label_create(parent);
  lv_obj_set_style_text_font(label, font, 0);
  lv_obj_set_style_text_color(label, color, 0);
  lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
  /* Bredd + LV_LABEL_LONG_DOT: en IPv6-adress är längre än glaset och ska
   * klippas snyggt i stället för att spilla ut över de klippta hörnen. */
  lv_obj_set_width(label, CONTENT_W);
  lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
  lv_obj_align(label, LV_ALIGN_TOP_MID, 0, y);
  lv_label_set_text(label, "");
  return label;
}

static lv_obj_t *row_control(lv_obj_t *parent, int y, lv_obj_t **label_out,
                             const char *text) {
  lv_obj_t *row = lv_obj_create(parent);
  lv_obj_remove_style_all(row);
  lv_obj_set_size(row, SETTINGS_ROW_WIDTH, SETTINGS_ROW_HEIGHT);
  lv_obj_set_pos(row, SETTINGS_ROW_X, y);
  lv_obj_set_style_radius(row, SETTINGS_ROW_RADIUS, 0);
  lv_obj_set_style_border_width(row, SETTINGS_ROW_BORDER_W, 0);
  lv_obj_set_style_border_color(row, COL_MUTED, 0);
  lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);

  lv_obj_t *label = lv_label_create(row);
  lv_obj_set_style_text_font(label, &plex_ui_21, 0);
  lv_obj_set_style_text_color(label, lv_color_white(), 0);
  lv_obj_set_style_text_letter_space(label, 2, 0);
  lv_label_set_text(label, text);
  lv_obj_center(label);
  if (label_out) *label_out = label;
  return row;
}

static void render(void);

void torget_settings_click_row(tg_settings_row row) {
  switch (row) {
    case TG_SETTINGS_ROW_UPDATE:
      /* Utan adress kan ett OTA-fönster aldrig ta emot en uppladdning.
       * Raden är nedtonad och trycket ignoreras — hellre en rad som
       * synligt inte går att välja än ett fönster som öppnas och sedan
       * inte kan göra något. ABOUT säger varför: ADDRESS visar streck. */
      if (!ui.ip[0]) break;
      /* Menyn stänger sig själv och lämnar över. Fönsterordningen — vem som
       * äger port 80 — avgörs av main.c, aldrig härifrån. */
      ui.pending = TG_SETTINGS_INTENT_OPEN_UPDATE;
      torget_settings_close();
      break;
    case TG_SETTINGS_ROW_WIFI:
      ui.pending = TG_SETTINGS_INTENT_OPEN_WIFI;
      torget_settings_close();
      break;
    case TG_SETTINGS_ROW_ABOUT:
      ui.view = VIEW_ABOUT;
      render();
      break;
    default:
      break;
  }
}

static void row_clicked_cb(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
  torget_settings_click_row(
      (tg_settings_row)(intptr_t)lv_event_get_user_data(event));
}

static void back_clicked_cb(lv_event_t *event) {
  if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
  ui.view = VIEW_MENU;
  render();
}

void torget_settings_create(void) {
  /* Topplagret, inte appträdet — samma regel som OTA-overlayn och
   * setupfönstret. Kallas under anroparens UI-lås. */
  ui.overlay = lv_obj_create(lv_layer_top());
  lv_obj_remove_style_all(ui.overlay);
  lv_obj_set_size(ui.overlay, 480, 480);
  lv_obj_set_pos(ui.overlay, 0, 0);
  lv_obj_set_style_bg_color(ui.overlay, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(ui.overlay, LV_OPA_COVER, 0);
  /* Slukar touch: fingret ska inte nå apparna bakom svart glas. */
  lv_obj_add_flag(ui.overlay, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_flag(ui.overlay, LV_OBJ_FLAG_HIDDEN);

  ui.word = line(ui.overlay, &plex_attention_52, lv_color_white(),
                 SETTINGS_WORD_Y);
  lv_label_set_text(ui.word, "SETTINGS");

  ui.foot = line(ui.overlay, &plex_ui_21, COL_MUTED, SETTINGS_FOOTER_Y);
  lv_obj_set_style_text_letter_space(ui.foot, 2, 0);
  lv_label_set_text(ui.foot, "KEY3 CLOSES");

  for (int i = 0; i < TG_SETTINGS_ROW_COUNT; i++) {
    int y = SETTINGS_FIRST_ROW_Y + i * (SETTINGS_ROW_HEIGHT + SETTINGS_ROW_GAP);
    ui.rows[i] = row_control(ui.overlay, y, &ui.row_labels[i], ROW_TEXT[i]);
    lv_obj_add_event_cb(ui.rows[i], row_clicked_cb, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
  }

  for (int i = 0; i < ABOUT_ROWS; i++) {
    int y = SETTINGS_ABOUT_FIRST_LINE_Y + i * SETTINGS_ABOUT_LINE_GAP;
    ui.about_labels[i] = line(ui.overlay, &plex_ui_21, COL_MUTED, y);
    lv_obj_set_style_text_letter_space(ui.about_labels[i], 2, 0);
    lv_label_set_text(ui.about_labels[i], ABOUT_LABEL[i]);
    ui.about_values[i] = line(ui.overlay, &plex_body_27, lv_color_white(),
                              y + 24);
  }
  ui.about_back = row_control(ui.overlay, SETTINGS_ABOUT_BACK_Y,
                              &ui.about_back_label, "BACK");
  lv_obj_add_event_cb(ui.about_back, back_clicked_cb, LV_EVENT_CLICKED, NULL);

  ui.view = VIEW_MENU;
  ui.about_dirty = true;
  render();
}

static void render(void) {
  if (!ui.overlay) return;
  bool menu = (ui.view == VIEW_MENU);

  for (int i = 0; i < TG_SETTINGS_ROW_COUNT; i++) show(ui.rows[i], menu);
  /* Samma sanning i två uttryck: tonen på raden och trycket som ignoreras.
   * Muted text + muted kant läser som "går inte att välja just nu" utan att
   * raden försvinner — den ska finnas kvar så menyn inte byter form. */
  {
    bool can_update = ui.ip[0] != '\0';
    lv_obj_set_style_text_color(ui.row_labels[TG_SETTINGS_ROW_UPDATE],
                                can_update ? lv_color_white() : COL_MUTED, 0);
  }
  for (int i = 0; i < ABOUT_ROWS; i++) {
    show(ui.about_labels[i], !menu);
    show(ui.about_values[i], !menu);
  }
  show(ui.about_back, !menu);

  if (!menu && ui.about_dirty) {
    /* Streck för det som saknas — aldrig en tom rad och aldrig en påhittad
     * nolla. Samma regel som resten av skärmen. */
    lv_label_set_text(ui.about_values[0],
                      ui.version[0] ? ui.version : "–");
    lv_label_set_text(ui.about_values[1], ui.ip[0] ? ui.ip : "–");
    ui.about_dirty = false;
  }
}

void torget_settings_open(const char *version, const char *ip) {
  if (!ui.overlay) return;
  snprintf(ui.version, sizeof ui.version, "%s", version ? version : "");
  snprintf(ui.ip, sizeof ui.ip, "%s", ip ? ip : "");
  ui.about_dirty = true;
  ui.view = VIEW_MENU;
  ui.open = true;
  render();
  show(ui.overlay, true);
  torget_settings_keep_foreground();
}

/* Skapelseordningen räcker INTE som företräde, och att lita på den var ett
 * eget misstag: både setupfönstret och OTA-overlayn kallar
 * lv_obj_move_foreground() i sina egna set(). Den som ritade senast ligger
 * överst, oavsett vem som skapades sist. Konkret gick menyn under NO
 * NETWORK-sidan: dess nedräkning ändras varje sekund, så avdupliceringen
 * släpper igenom en omritning i sekunden och lyfter nätlagret igen.
 *
 * Därför hävdar menyn sitt läge varje tick i stället för en gång vid
 * öppning. Det är gratis när den redan ligger överst: lv_obj_move_foreground
 * går till lv_obj_move_to_index, som returnerar direkt när indexet redan
 * stämmer — före lv_obj_invalidate. Ingen omritning, inga extra pixlar.
 *
 * Att detta inte lägger menyn ovanpå en väntande uppdatering garanteras
 * inte av lagerordningen utan av att de två aldrig är uppe samtidigt:
 * main.c vägrar öppna menyn medan notisen syns, och stänger den om notisen
 * dyker upp under tiden. Ömsesidig uteslutning, inte z-ordning. */
void torget_settings_keep_foreground(void) {
  if (!ui.overlay || !ui.open) return;
  lv_obj_move_foreground(ui.overlay);
}

void torget_settings_close(void) {
  if (!ui.overlay) return;
  ui.open = false;
  ui.view = VIEW_MENU;
  show(ui.overlay, false);
}

bool torget_settings_open_p(void) { return ui.open; }

tg_settings_intent torget_settings_take_intent(void) {
  tg_settings_intent intent = ui.pending;
  ui.pending = TG_SETTINGS_INTENT_NONE;
  return intent;
}
