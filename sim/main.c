/*
 * Torgets värdlager på Macen: hela plattformen + båda apparna i ett
 * SDL-fönster, månader/timmar före hårdvaran. Läser de RIKTIGA fixture-
 * filerna genom samma parsrar som ESP-targetet — en payload som renderar
 * här kan inte felparsa på hyllan.
 *
 * Autocykeln demonstrerar tickande, midnatt och en äkta stale-övergång:
 * felfixturen håller 140 s så apparnas 120 s-tröskel faktiskt slår till,
 * precis som ett routeravbrott. Tangent 1-4 hoppar till en Solelkollen-
 * fixtur; T matar VibePulse igen; S cyklar agentstatus; L öppnar launchern (långtryck med
 * musen fungerar också — det är enhetens gest).
 */
#include <SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"

#include "app_solelkollen.h"
#include "app_tokens.h"
#include "agent_status_parse.h"
#include "glance_parse.h"
#include "tokens_parse.h"
#include "torget.h"

typedef struct { const char *file; const char *name; uint32_t hold_ms; } fixture_t;

static const fixture_t FIXTURES[] = {
  { "midday.json",    "midday (syntetisk, tickande)",      20000 },
  { "evening.json",   "evening (prod 2026-07-16)",          20000 },
  { "midnight.json",  "midnight (prod 2026-07-17)",         20000 },
  { "error-502.json", "error-502 (håller 140 s → stale)",  140000 },
};

static int fixture_idx = -1;
static lv_timer_t *cycle_timer;

static const char *const AGENT_FIXTURES[] = {
  "agent-status-idle.json",
  "agent-status-claude-working.json",
  "agent-status-claude-waiting.json",
  "agent-status-claude-done.json",
  "agent-status-claude-error.json",
  "agent-status-codex-working.json",
  "agent-status-codex-waiting.json",
  "agent-status-codex-done.json",
  "agent-status-codex-error.json",
  "agent-status-unknown.json",
};
static int agent_fixture_idx;

/* ---------------------------------------------- plattforms-API:t (torget.h) */

/* Simulatorn är entrådad: lv_timer_handler-loopen är enda exekutören, så
 * låset är en no-op och nätväntan meningslös (fixtures behöver inget nät). */
void torget_ui_lock(void)   {}
void torget_ui_unlock(void) {}
void torget_net_wait(void)  {}
void torget_keep_awake(void) {} /* ljusrampen finns bara på panelen */

int64_t torget_now_us(void) { return (int64_t)lv_tick_get() * 1000; }

/* ------------------------------------------------------------------ BMP:er */

/* Dumpa den faktiska framebuffern till en 32-bpp BMP — P18-verktyget och
 * pixelverifieringens facit. LVGL:s XRGB8888 är little-endian BGRX i minnet,
 * vilket är exakt BMP:s radformat, så raderna kopieras rått. Negativ höjd =
 * uppifrån och ner. */
static void dump_frame(const char *tag) {
  lv_draw_buf_t *buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_XRGB8888);
  if (!buf) { printf("snapshot: misslyckades (%s)\n", tag); return; }

  char path[256];
  snprintf(path, sizeof path, "/tmp/torget-%s.bmp", tag);
  FILE *f = fopen(path, "wb");
  if (f) {
    int w = buf->header.w, h = buf->header.h;
    uint32_t img_bytes = (uint32_t)(w * h * 4);
    uint8_t hdr[54] = { 'B', 'M' };
    uint32_t file_size = 54 + img_bytes;
    memcpy(hdr + 2, &file_size, 4);
    uint32_t off = 54;            memcpy(hdr + 10, &off, 4);
    uint32_t dib = 40;            memcpy(hdr + 14, &dib, 4);
    int32_t bw = w, bh = -h;      memcpy(hdr + 18, &bw, 4); memcpy(hdr + 22, &bh, 4);
    uint16_t planes = 1, bpp = 32; memcpy(hdr + 26, &planes, 2); memcpy(hdr + 28, &bpp, 2);
    memcpy(hdr + 34, &img_bytes, 4);
    fwrite(hdr, 1, 54, f);
    for (int y = 0; y < h; y++)
      fwrite(buf->data + (size_t)y * buf->header.stride, 1, (size_t)w * 4, f);
    fclose(f);
    printf("snapshot: %s\n", path);
  }
  lv_draw_buf_destroy(buf);
}

/* Sex halvsekunderssteg per fixtur: dumpa vy 1-4 i tur och ordning och gå
 * tillbaka. Lämnar torget-<fixtur>-<vy>.bmp för alla Solelkollen-vyer. */
static int dump_stage;

static void dump_seq_cb(lv_timer_t *t) {
  static const char *tags[4] = { "midday", "evening", "midnight", "stale" };
  int fixture = (int)(intptr_t)lv_timer_get_user_data(t);
  char tag[32];
  switch (dump_stage++) {
    case 0: snprintf(tag, sizeof tag, "%s-1", tags[fixture]); dump_frame(tag); break;
    case 1: solelkollen_show_view(1); break;
    case 2: snprintf(tag, sizeof tag, "%s-2", tags[fixture]); dump_frame(tag); break;
    case 3: solelkollen_show_view(2); break;
    case 4: snprintf(tag, sizeof tag, "%s-3", tags[fixture]); dump_frame(tag); break;
    case 5: solelkollen_show_view(3); break;
    case 6: snprintf(tag, sizeof tag, "%s-4", tags[fixture]); dump_frame(tag); break;
    default: solelkollen_show_view(0); break;
  }
  lv_timer_set_period(t, 500);
}

/* ---------------------------------------------------------------- fixtures */

static char *read_fixture(const char *file, size_t *len_out) {
  char path[512];
  snprintf(path, sizeof path, "%s/%s", FIXTURES_DIR, file);
  FILE *f = fopen(path, "rb");
  if (!f) return NULL;
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  char *buf = malloc((size_t)n + 1);
  fread(buf, 1, (size_t)n, f);
  buf[n] = '\0';
  fclose(f);
  *len_out = (size_t)n;
  return buf;
}

/* En "hämtning" åt Solelkollen: läs filen, parsa med den delade parsern.
 * Ett parsfel beter sig exakt som på enheten: gamla värden står kvar och
 * appens egen stale-tröskel tar hand om resten. */
static void apply_fixture(int idx) {
  fixture_idx = idx % 4;
  const fixture_t *f = &FIXTURES[fixture_idx];
  lv_timer_set_period(cycle_timer, f->hold_ms);
  lv_timer_reset(cycle_timer);

  size_t len = 0;
  char *json = read_fixture(f->file, &len);
  sg_glance g;
  char day[11];
  bool ok = json && sg_glance_parse(json, len, &g, day, sizeof day);
  free(json);

  if (ok) {
    solelkollen_apply_glance(&g);
    printf("fixtur: %-38s (dag %s)\n", f->name, day);
  } else {
    printf("fixtur: %-38s (avvisad av parsern, värden kvar)\n", f->name);
  }

  /* Två sekunder in (tickern rullar, layouten satt): dumpa ALLA vyer så en
   * obevakad körning lämnar ett komplett P18-bildset per fixtur. */
  dump_stage = 0;
  lv_timer_t *seq = lv_timer_create(dump_seq_cb, 2000, (void *)(intptr_t)fixture_idx);
  lv_timer_set_repeat_count(seq, 8);
}

static void feed_tokens(void) {
  size_t len = 0;
  char *json = read_fixture("tokens.json", &len);
  tk_tokens t;
  if (json && tk_tokens_parse(json, len, &t)) {
    /* Bildfacit för agentoverlayn: Claude jämför alla tre fönster och
     * Fable ligger närmast taket. Den ordinarie vyn behåller samma rad. */
    t.claude_session.has_pct = 1;
    t.claude_session.pct = 21.0;
    t.claude_week.has_pct = 1;
    t.claude_week.pct = 49.0;
    t.claude_model_week.has_pct = 1;
    t.claude_model_week.pct = 73.0;
    tokens_apply(&t);
    printf("tokens: %.2f Mtok idag, %d sessioner, takt %.2f Mtok/h\n",
           t.day_tokens / 1e6, t.day_sessions, t.day_tokens_per_hour / 1e6);
  } else {
    printf("tokens: fixtur saknas/avvisad, vyn visar streck\n");
  }
  free(json);
}

static void apply_agent_fixture(int idx) {
  int count = (int)(sizeof AGENT_FIXTURES / sizeof AGENT_FIXTURES[0]);
  agent_fixture_idx = (idx % count + count) % count;
  const char *file = AGENT_FIXTURES[agent_fixture_idx];
  size_t len = 0;
  char *json = read_fixture(file, &len);
  tk_agent_snapshot snapshot;
  if (json && tk_agent_status_parse(json, len, &snapshot)) {
    tokens_apply_agent_status(&snapshot);
    printf("agentstatus: %s (seq %u)\n", file, snapshot.seq);
  } else {
    printf("agentstatus: %s avvisad\n", file);
  }
  free(json);
}

static void next_fixture(lv_timer_t *t) {
  (void)t;
  apply_fixture(fixture_idx + 1);
}

/* Plattformsrundan efter fixturdumparna: launcher → VibePulse-agentlägen →
 * ordinarie VibePulse-vyer → hem. */
static void platform_tour_cb(lv_timer_t *t) {
  static int stage;
  (void)t;
  switch (stage++) {
    case 0: torget_launcher_open(); break;
    case 1: dump_frame("launcher"); break;
    case 2: torget_app_show(1); break;
    case 3: apply_agent_fixture(1); break;
    case 4: dump_frame("agent-claude-working"); break;
    case 5: apply_agent_fixture(2); break;
    case 6: dump_frame("agent-claude-waiting"); break;
    case 7: apply_agent_fixture(3); break;
    case 8: dump_frame("agent-claude-done"); break;
    case 9: apply_agent_fixture(5); break;
    case 10: dump_frame("agent-codex-working"); break;
    case 11: apply_agent_fixture(6); break;
    case 12: dump_frame("agent-codex-waiting"); break;
    case 13: apply_agent_fixture(7); break;
    case 14: dump_frame("agent-codex-done"); break;
    case 15: apply_agent_fixture(0); break;
    case 16: dump_frame("tokens-claude"); break;
    case 17: tokens_show_view(1); break;
    case 18: dump_frame("tokens-codex"); break;
    case 19: tokens_show_view(2); break;
    case 20: dump_frame("tokens-volym"); break;
    case 21: tokens_show_view(0); break;
    default: torget_app_show(0); break;
  }
  lv_timer_set_period(t, 500);
}

/* Tangent 1-4: Solelkollen-fixtur. T: mata VibePulse. S: nästa agentläge.
 * L: launchern.
 * N: nästa app (KEY3-knappens bänkmotsvarighet). LVGL:s SDL-drivrutin
 * pumpar eventen, så ren tangentbordspollning räcker — ingen indev-
 * rördragning för ett bänkverktyg. */
static void poll_keys(lv_timer_t *t) {
  (void)t;
  static bool held[8];
  const Uint8 *ks = SDL_GetKeyboardState(NULL);
  const SDL_Scancode keys[8] = { SDL_SCANCODE_1, SDL_SCANCODE_2,
                                 SDL_SCANCODE_3, SDL_SCANCODE_4,
                                 SDL_SCANCODE_T, SDL_SCANCODE_S,
                                 SDL_SCANCODE_L,
                                 SDL_SCANCODE_N };
  for (int i = 0; i < 8; i++) {
    bool down = ks[keys[i]];
    if (down && !held[i]) {
      if (i < 4) apply_fixture(i);
      else if (i == 4) feed_tokens();
      else if (i == 5) {
        torget_app_show(1);
        apply_agent_fixture(agent_fixture_idx + 1);
      }
      else if (i == 6) torget_launcher_open();
      else torget_app_next();
    }
    held[i] = down;
  }
}

int main(void) {
  /* Radbuffrat även vid pipe: fixtureloggen ska överleva en kill. */
  setvbuf(stdout, NULL, _IOLBF, 0);
  lv_init();
  lv_display_t *disp = lv_sdl_window_create(480, 480);
  lv_sdl_window_set_title(disp, "Torget 480x480 — S agentstatus, T VibePulse, L launcher");
  lv_sdl_mouse_create();

  torget_ui_create(); /* bygger apparna via registret, går in i app 0 */

  /* Sverige-vyn (P23): en hämtning vid start, genom samma parser som
   * targetet — settlement rör sig inte på en simulatorsession. */
  {
    size_t len = 0;
    char *json = read_fixture("sverige.json", &len);
    sg_sverige sve;
    if (json && sg_sverige_parse(json, len, &sve)) {
      solelkollen_apply_sverige(&sve);
      printf("sverige: avräknat %s, %.1f GWh\n", sve.day, sve.day_gwh);
    } else {
      printf("sverige: fixtur saknas/avvisad, vyn visar streck\n");
    }
    free(json);
  }

  /* VibePulse får sin fixtur direkt: launchern ska visa en levande app,
   * inte ett streck, när man tittar in (tangent T matar om). */
  feed_tokens();

  cycle_timer = lv_timer_create(next_fixture, 20000, NULL);
  apply_fixture(0);
  lv_timer_create(poll_keys, 50, NULL);

  /* Plattformsrundan: efter Solelkollen-dumparna (klara ~5,5 s in), dumpa
   * launchern och VibePulse också — en obevakad körning bevisar hela
   * plattformen, inte bara första appen. */
  lv_timer_t *tour = lv_timer_create(platform_tour_cb, 6500, NULL);
  lv_timer_set_repeat_count(tour, 23);

  while (1) {
    uint32_t idle = lv_timer_handler();
    lv_delay_ms(idle > 30 ? 30 : idle);
  }
  return 0;
}
