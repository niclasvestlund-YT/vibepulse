/*
 * Torgets värdlager på Macen: hela plattformen + båda apparna i ett
 * SDL-fönster, månader/timmar före hårdvaran. Läser de RIKTIGA fixture-
 * filerna genom samma parsrar som ESP-targetet — en payload som renderar
 * här kan inte felparsa på hyllan. [ och ] byter VibePulse-vy, medan N
 * fortsätter motsvara KEY3:s appbyte.
 *
 * Autocykeln demonstrerar tickande, midnatt och en äkta stale-övergång:
 * felfixturen håller 140 s så apparnas 120 s-tröskel faktiskt slår till,
 * precis som ett routeravbrott. Tangent 1-4 hoppar till en Solelkollen-
 * fixtur; T matar VibePulse igen; S cyklar agentstatus; M cyklar Max
 * Tracker-fixturerna; L öppnar launchern (långtryck med
 * musen fungerar också — det är enhetens gest).
 */
#include <SDL.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "lvgl.h"

#include "app_solelkollen.h"
#include "app_tokens.h"
#include "agent_monitor.h"
#include "agent_monitor_policy.h"
#include "agent_status_parse.h"
#include "glance_parse.h"
#include "max_tracker_parse.h"
#include "tokens_parse.h"
#include "torget.h"
#include "usage_screen.h"

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
static int capture_failures;

static const char *const MAX_TRACKER_FIXTURES[] = {
  "max-tracker-full.json",
  "max-tracker-coldstart.json",
  "max-tracker-empty.json",
};
static int max_tracker_fixture_idx;

/* ---------------------------------------------- plattforms-API:t (torget.h) */

/* Simulatorn är entrådad: lv_timer_handler-loopen är enda exekutören, så
 * låset är en no-op och nätväntan meningslös (fixtures behöver inget nät). */
void torget_ui_lock(void)   {}
void torget_ui_unlock(void) {}
void torget_net_wait(void)  {}
void torget_keep_awake(void) {} /* ljusrampen finns bara på panelen */

int64_t torget_now_us(void) { return (int64_t)lv_tick_get() * 1000; }

/* ------------------------------------------------------------------ BMP:er */

static void capture_failed(const char *tag, const char *detail) {
  capture_failures++;
  fprintf(stderr, "snapshot: misslyckades (%s): %s\n", tag, detail);
}

/* Dumpa den faktiska framebuffern till en 32-bpp BMP — P18-verktyget och
 * pixelverifieringens facit. LVGL:s XRGB8888 är little-endian BGRX i minnet,
 * vilket är exakt BMP:s radformat, så raderna kopieras rått. Negativ höjd =
 * uppifrån och ner. */
static void dump_frame(const char *tag) {
  /* QA-dumpar får inte bero på var SDL:s nästa refresh råkar ligga. Tvinga
   * layout + redraw före snapshot så ett helt tillstånd fångas atomiskt. */
  lv_obj_update_layout(lv_screen_active());
  lv_obj_invalidate(lv_screen_active());
  lv_draw_buf_t *buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_XRGB8888);
  if (!buf) { capture_failed(tag, "kunde inte skapa LVGL-snapshot"); return; }

  const char *capture_dir = getenv("TORGET_CAPTURE_DIR");
  if (!capture_dir || capture_dir[0] == '\0') capture_dir = "/tmp";
  char path[256];
  int path_len = snprintf(path, sizeof path, "%s/torget-%s.bmp", capture_dir, tag);
  if (path_len < 0 || (size_t)path_len >= sizeof path) {
    capture_failed(tag, "sökvägen är för lång");
    lv_draw_buf_destroy(buf);
    return;
  }

  int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
  if (fd < 0) {
    char detail[512];
    snprintf(detail, sizeof detail, "kunde inte öppna %s: %s", path, strerror(errno));
    capture_failed(tag, detail);
    lv_draw_buf_destroy(buf);
    return;
  }

  FILE *f = fdopen(fd, "wb");
  if (!f) {
    int saved_errno = errno;
    close(fd);
    char detail[512];
    snprintf(detail, sizeof detail, "kunde inte fdopen %s: %s", path,
             strerror(saved_errno));
    capture_failed(tag, detail);
    lv_draw_buf_destroy(buf);
    return;
  }

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

  int failed = fwrite(hdr, 1, sizeof hdr, f) != sizeof hdr;
  for (int y = 0; y < h && !failed; y++) {
    size_t row_bytes = (size_t)w * 4;
    if (fwrite(buf->data + (size_t)y * buf->header.stride,
               1, row_bytes, f) != row_bytes) failed = 1;
  }
  if (fclose(f) != 0) failed = 1;

  if (failed) capture_failed(tag, "kunde inte skriva eller stänga BMP-filen");
  else printf("snapshot: %s\n", path);
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

static void feed_tokens_file(const char *file) {
  size_t len = 0;
  char *json = read_fixture(file, &len);
  tk_tokens t;
  if (json && tk_tokens_parse(json, len, &t)) {
    tokens_apply(&t);
    printf("tokens: %.2f Mtok idag, %d sessioner, takt %.2f Mtok/h\n",
           t.day_tokens / 1e6, t.day_sessions, t.day_tokens_per_hour / 1e6);
  } else {
    printf("tokens: fixtur saknas/avvisad, vyn visar streck\n");
  }
  free(json);
}

static void feed_tokens(void) {
  feed_tokens_file("tokens.json");
}

static tk_limit forecast_limit(double pct, int reset_min) {
  tk_limit value = {0};
  value.pct = pct;
  value.reset_min = reset_min;
  value.has_pct = 1;
  value.has_reset = 1;
  return value;
}

static void feed_forecast_outcomes(void) {
  tk_tokens tokens = {0};
  tokens.claude_week = forecast_limit(47, 300);
  tokens.claude_forecast.state = TK_FORECAST_AT_RESET;
  tokens.claude_forecast.pct_at_reset = 85;
  tokens.claude_forecast.pace_factor = 1.4;
  tokens.claude_forecast.has_pct_at_reset = 1;
  tokens.claude_forecast.has_pace_factor = 1;

  tokens.codex_week = forecast_limit(35, 2210);
  tokens.codex_forecast.state = TK_FORECAST_EXHAUSTS;
  struct tm saturday = {0};
  saturday.tm_year = 126;
  saturday.tm_mon = 7;
  saturday.tm_mday = 8;
  saturday.tm_hour = 5;
  saturday.tm_isdst = -1;
  tokens.codex_forecast.at_epoch = (int64_t)mktime(&saturday);
  tokens.codex_forecast.offset_min = -540;
  tokens.codex_forecast.has_at_epoch = 1;
  tokens.codex_forecast.has_offset_min = 1;
  tokens_apply(&tokens);
}

static void apply_agent_file(const char *file) {
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

static void apply_agent_fixture(int idx) {
  int count = (int)(sizeof AGENT_FIXTURES / sizeof AGENT_FIXTURES[0]);
  agent_fixture_idx = (idx % count + count) % count;
  apply_agent_file(AGENT_FIXTURES[agent_fixture_idx]);
}

/* Max Tracker-fixturerna genom samma delade parser som targetet — samma
 * mönster som apply_agent_file: ett avvisat svar lämnar tidigare värden. */
static void feed_max_tracker_file(const char *file) {
  size_t len = 0;
  char *json = read_fixture(file, &len);
  tk_max_tracker t;
  if (json && tk_max_tracker_parse(json, len, &t)) {
    tokens_apply_max_tracker(&t);
    printf("max-tracker: %-24s (streak %d)\n", file, t.coding_streak_days);
  } else {
    printf("max-tracker: %s avvisad\n", file);
  }
  free(json);
}

static void apply_max_tracker_fixture(int idx) {
  int count =
      (int)(sizeof MAX_TRACKER_FIXTURES / sizeof MAX_TRACKER_FIXTURES[0]);
  max_tracker_fixture_idx = (idx % count + count) % count;
  feed_max_tracker_file(MAX_TRACKER_FIXTURES[max_tracker_fixture_idx]);
}

static tk_agent_snapshot static_working_snapshot(tk_agent_provider provider,
                                                 const char *model,
                                                 const char *effort) {
  tk_agent_snapshot snapshot = {0};
  snapshot.seq = provider == TK_AGENT_PROVIDER_CLAUDE ? 901 : 902;
  tk_agent_provider_status *target =
      provider == TK_AGENT_PROVIDER_CLAUDE ? &snapshot.claude
                                           : &snapshot.codex;
  target->active_count = 1;
  target->job_count = 1;
  tk_agent_status *job = &target->jobs[0];
  snprintf(job->task_id, sizeof job->task_id, "static-working");
  snprintf(job->event_id, sizeof job->event_id, "static-working-%u",
           snapshot.seq);
  snprintf(job->project, sizeof job->project, "Torget");
  snprintf(job->model, sizeof job->model, "%s", model);
  snprintf(job->effort, sizeof job->effort, "%s", effort);
  job->has_model = true;
  job->has_effort = true;
  job->state = TK_AGENT_WORKING;
  job->activity = TK_ACTIVITY_EDITING;
  return snapshot;
}

static tk_agent_snapshot static_attention_snapshot(
    tk_agent_provider provider, tk_agent_state state, const char *event_id,
    const char *project) {
  tk_agent_snapshot snapshot = {0};
  snapshot.seq = provider == TK_AGENT_PROVIDER_CLAUDE ? 903 : 904;
  tk_agent_provider_status *target =
      provider == TK_AGENT_PROVIDER_CLAUDE ? &snapshot.claude
                                           : &snapshot.codex;
  target->active_count = state == TK_AGENT_DONE ? 0 : 1;
  target->job_count = 1;
  tk_agent_status *job = &target->jobs[0];
  snprintf(job->task_id, sizeof job->task_id, "attention-%s", event_id);
  snprintf(job->event_id, sizeof job->event_id, "%s", event_id);
  snprintf(job->project, sizeof job->project, "%s", project);
  job->state = state;
  job->updated_ms = 1;
  return snapshot;
}

static tk_agent_snapshot two_waiting_snapshot(void) {
  tk_agent_snapshot snapshot = static_attention_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, TK_AGENT_WAITING,
      "capture-claude-waiting-two", "Buddy");
  snapshot.seq = 905;
  snapshot.claude.active_count = 2;
  snapshot.claude.job_count = 2;
  tk_agent_status *claude = &snapshot.claude.jobs[1];
  snprintf(claude->task_id, sizeof claude->task_id,
           "attention-capture-claude-waiting-queued");
  snprintf(claude->event_id, sizeof claude->event_id,
           "capture-claude-waiting-queued");
  snprintf(claude->project, sizeof claude->project, "Torget");
  claude->state = TK_AGENT_WAITING;
  claude->updated_ms = 2;
  snapshot.codex.active_count = 1;
  snapshot.codex.job_count = 1;
  tk_agent_status *codex = &snapshot.codex.jobs[0];
  snprintf(codex->task_id, sizeof codex->task_id,
           "attention-capture-codex-waiting-two");
  snprintf(codex->event_id, sizeof codex->event_id,
           "capture-codex-waiting-two");
  snprintf(codex->project, sizeof codex->project, "Solelkollen");
  codex->state = TK_AGENT_WAITING;
  codex->updated_ms = 3;
  return snapshot;
}

static void next_fixture(lv_timer_t *t) {
  (void)t;
  apply_fixture(fixture_idx + 1);
}

/* Plattformsrundan efter fixturdumparna: deterministiska statiska
 * VibePulse-vyer för bildgranskningen före AMOLED-grinden. */
static void platform_tour_cb(lv_timer_t *t) {
  static int stage;
  (void)t;
  switch (stage++) {
    case 0: torget_launcher_open(); break;
    case 1: dump_frame("launcher"); break;
    case 2: torget_app_show(1); tokens_show_view(VIEW_CLAUDE_FABLE); break;
    case 3: apply_agent_fixture(1); break;
    case 4: dump_frame("vibepulse-claude-static"); break;
    case 5: apply_agent_fixture(2); break;
    case 6: dump_frame("vibepulse-claude-long-copy"); break;
    case 7: tokens_show_view(VIEW_CODEX_WEEKLY); apply_agent_fixture(5); break;
    case 8: dump_frame("vibepulse-codex-static"); break;
    case 9: tokens_show_view(VIEW_BURN_RATE); break;
    case 10: dump_frame("vibepulse-forecast-collecting"); break;
    case 11:
      tokens_show_view(VIEW_CLAUDE_FABLE);
      apply_agent_fixture(0);
      feed_tokens_file("tokens-missing.json");
      break;
    case 12: dump_frame("vibepulse-claude-missing"); break;
    case 13: feed_tokens(); break;
    case 14: dump_frame("vibepulse-claude-restored"); break;
    case 15:
      tokens_show_view(VIEW_TRACKER_CLAUDE);
      feed_max_tracker_file("max-tracker-coldstart.json");
      break;
    case 16: dump_frame("vibepulse-tracker-claude"); break;
    case 17:
      tokens_show_view(VIEW_TRACKER_CODEX);
      feed_max_tracker_file("max-tracker-full.json");
      break;
    case 18: dump_frame("vibepulse-tracker-codex"); break;
    default: torget_app_show(0); break;
  }
  lv_timer_set_period(t, 500);
}

/* Tangent 1-4: Solelkollen-fixtur. T: mata VibePulse. S: nästa agentläge.
 * L: launchern. [ och ] bläddrar VibePulse-sidor; N byter app (KEY3).
 * M: nästa Max Tracker-fixtur. LVGL:s SDL-drivrutin
 * pumpar eventen, så ren tangentbordspollning räcker — ingen indev-
 * rördragning för ett bänkverktyg. */
static void poll_keys(lv_timer_t *t) {
  (void)t;
  static bool held[11];
  const Uint8 *ks = SDL_GetKeyboardState(NULL);
  const SDL_Scancode keys[11] = { SDL_SCANCODE_1, SDL_SCANCODE_2,
                                  SDL_SCANCODE_3, SDL_SCANCODE_4,
                                  SDL_SCANCODE_T, SDL_SCANCODE_S,
                                  SDL_SCANCODE_L, SDL_SCANCODE_N,
                                  SDL_SCANCODE_LEFTBRACKET,
                                  SDL_SCANCODE_RIGHTBRACKET,
                                  SDL_SCANCODE_M };
  for (int i = 0; i < 11; i++) {
    bool down = ks[keys[i]];
    if (down && !held[i]) {
      if (i < 4) apply_fixture(i);
      else if (i == 4) feed_tokens();
      else if (i == 5) {
        torget_app_show(1);
        apply_agent_fixture(agent_fixture_idx + 1);
      }
      else if (i == 6) torget_launcher_open();
      else if (i == 7) torget_app_next();
      else if (i == 8 || i == 9) {
        torget_app_show(1);
        int view = usage_screen_current_view() + (i == 8 ? -1 : 1);
        if (view < 0) view = TK_USAGE_SCREEN_VIEWS - 1;
        if (view >= TK_USAGE_SCREEN_VIEWS) view = 0;
        tokens_show_view(view);
      }
      else apply_max_tracker_fixture(max_tracker_fixture_idx + 1);
    }
    held[i] = down;
  }
}

static int run_vibepulse_static_qa(void) {
  capture_failures = 0;
  torget_app_show(1);

  feed_tokens();
  tk_agent_snapshot single = static_working_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, "OPUS 4.1", "ULTRA");
  tokens_apply_agent_status(&single);
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-claude-single-working");
  usage_screen_tick(
      torget_now_us() +
      (int64_t)(TK_AGENT_WORKING_LEASE_MS + 1ULL) * 1000LL);
  dump_frame("vibepulse-claude-lease-expired");

  apply_agent_file("agent-status-multi-working.json");
  dump_frame("vibepulse-claude-multi-chat");

  apply_agent_file("agent-status-idle.json");
  dump_frame("vibepulse-claude-idle");
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-idle");

  single = static_working_snapshot(TK_AGENT_PROVIDER_CODEX,
                                   "GPT-5.6 SOL", "ULTRA");
  tokens_apply_agent_status(&single);
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-single-working");

  apply_agent_file("agent-status-multi-working.json");
  dump_frame("vibepulse-codex-multi-chat");
  usage_screen_set_stale(true);
  dump_frame("vibepulse-codex-stale");
  usage_screen_set_stale(false);

  tk_tokens bar_case = {0};
  bar_case.claude_model_week = forecast_limit(73, 3120);
  snprintf(bar_case.claude_model_week_label,
           sizeof bar_case.claude_model_week_label, "FABLE · WEEK");
  bar_case.has_claude_model_week_label = 1;
  tokens_apply(&bar_case);
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-claude-today-missing");

  bar_case.claude_model_week = forecast_limit(9, 3120);
  bar_case.claude_model_week.delta_pct = 12;
  bar_case.claude_model_week.has_delta = 1;
  tokens_apply(&bar_case);
  dump_frame("vibepulse-claude-today-contradictory");

  bar_case.claude_model_week = forecast_limit(0, 3120);
  bar_case.claude_model_week.delta_pct = 0;
  bar_case.claude_model_week.has_delta = 1;
  tokens_apply(&bar_case);
  dump_frame("vibepulse-claude-zero-total");

  memset(&bar_case, 0, sizeof bar_case);
  bar_case.codex_week = forecast_limit(100, 2317);
  bar_case.codex_week.delta_pct = 0;
  bar_case.codex_week.has_delta = 1;
  tokens_apply(&bar_case);
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-full-total");

  feed_tokens();
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-claude-fable");
  tokens_show_view(VIEW_CLAUDE_ALL);
  dump_frame("vibepulse-claude-all");
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-weekly");

  /* Source-provenance evidence is built directly rather than read from the
   * live token service. Each capture therefore proves one exact wire state. */
  tk_tokens quota_truth = {0};
  quota_truth.codex_week = forecast_limit(46, 8640);
  quota_truth.codex_week.delta_pct = 8;
  quota_truth.codex_week.has_delta = 1;
  tokens_apply(&quota_truth);
  single = static_working_snapshot(TK_AGENT_PROVIDER_CODEX,
                                   "GPT-5.6 SOL", "ULTRA");
  tokens_apply_agent_status(&single);
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-weekly-live-46");

  quota_truth.codex_week.stale = 1;
  tokens_apply(&quota_truth);
  dump_frame("vibepulse-codex-weekly-cached-stale");

  memset(&quota_truth, 0, sizeof quota_truth);
  quota_truth.claude_model_week = forecast_limit(73, 3120);
  quota_truth.claude_model_week.delta_pct = 12;
  quota_truth.claude_model_week.has_delta = 1;
  quota_truth.claude_model_week.stale = 1;
  snprintf(quota_truth.claude_model_week_label,
           sizeof quota_truth.claude_model_week_label, "FABLE · WEEK");
  quota_truth.has_claude_model_week_label = 1;
  tokens_apply(&quota_truth);
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-claude-fable-cached-stale");

  memset(&quota_truth, 0, sizeof quota_truth);
  tokens_apply(&quota_truth);
  dump_frame("vibepulse-claude-fable-no-data");

  feed_forecast_outcomes();
  tokens_show_view(VIEW_BURN_RATE);
  dump_frame("vibepulse-burn-speed-up");

  tk_tokens forecast = {0};
  forecast.claude_week = forecast_limit(47, 300);
  forecast.codex_week = forecast_limit(35, 2210);
  forecast.claude_forecast.state = TK_FORECAST_AT_RESET;
  forecast.claude_forecast.pace_factor = 1.0;
  forecast.claude_forecast.pct_at_reset = 100;
  forecast.claude_forecast.has_pace_factor = 1;
  forecast.claude_forecast.has_pct_at_reset = 1;
  forecast.codex_forecast = forecast.claude_forecast;
  tokens_apply(&forecast);
  dump_frame("vibepulse-burn-on-pace");

  forecast.claude_forecast.state = TK_FORECAST_EXHAUSTS;
  forecast.claude_forecast.offset_min = -540;
  forecast.claude_forecast.at_epoch = 1786158000;
  forecast.claude_forecast.has_offset_min = 1;
  forecast.claude_forecast.has_at_epoch = 1;
  forecast.codex_forecast = forecast.claude_forecast;
  tokens_apply(&forecast);
  dump_frame("vibepulse-burn-early");

  forecast.claude_forecast.state = TK_FORECAST_COLLECTING;
  forecast.codex_forecast.state = TK_FORECAST_COLLECTING;
  tokens_apply(&forecast);
  dump_frame("vibepulse-burn-learning");

  forecast.claude_forecast.state = TK_FORECAST_UNAVAILABLE;
  forecast.codex_forecast.state = TK_FORECAST_UNAVAILABLE;
  tokens_apply(&forecast);
  dump_frame("vibepulse-burn-unavailable");

  feed_tokens();

  tokens_show_view(VIEW_CLAUDE_ALL);
  usage_screen_set_stale(true);
  dump_frame("vibepulse-claude-stale");
  usage_screen_set_stale(false);

  feed_tokens_file("tokens-missing.json");
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-claude-missing");
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-codex-missing");

  tk_agent_snapshot attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, TK_AGENT_WAITING,
      "capture-claude-needs-you", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-claude-needs-you");
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CODEX, TK_AGENT_WAITING,
      "capture-codex-needs-you", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-codex-needs-you");
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, TK_AGENT_ERROR,
      "capture-claude-error", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-claude-error");
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CODEX, TK_AGENT_ERROR,
      "capture-codex-error", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-codex-error");
  tk_agent_monitor_dismiss_current();

  attention = two_waiting_snapshot();
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-two-waiting-queued");
  tk_agent_monitor_dismiss_current();
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, TK_AGENT_DONE,
      "capture-claude-done", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-claude-done-static");
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CODEX, TK_AGENT_DONE,
      "capture-codex-done", "Torget");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-codex-done-static");
  tk_agent_monitor_dismiss_current();

  attention = static_attention_snapshot(
      TK_AGENT_PROVIDER_CLAUDE, TK_AGENT_WAITING,
      "capture-claude-swedish-project", "Räksmörgås");
  tokens_apply_agent_status(&attention);
  dump_frame("vibepulse-claude-swedish-project");
  tk_agent_monitor_dismiss_current();

  feed_max_tracker_file("max-tracker-coldstart.json");
  tokens_show_view(VIEW_TRACKER_CLAUDE);
  dump_frame("vibepulse-tracker-claude");

  feed_max_tracker_file("max-tracker-full.json");
  tokens_show_view(VIEW_TRACKER_CODEX);
  dump_frame("vibepulse-tracker-codex");

  return capture_failures == 0 ? 0 : 1;
}

static void run_vibepulse_completion_qa(void) {
  torget_app_show(1);
  tokens_show_view(VIEW_CLAUDE_FABLE);
  apply_agent_file("agent-status-idle.json");

  apply_agent_file("agent-status-multi-working.json");
  dump_frame("vibepulse-multi-working");

  apply_agent_file("agent-status-claude-done.json");
  dump_frame("vibepulse-claude-done-static");
  tk_agent_monitor_dismiss_current();

  apply_agent_file("agent-status-codex-done.json");
  dump_frame("vibepulse-codex-done-static");
  tk_agent_monitor_dismiss_current();

  apply_agent_file("agent-status-multi-done.json");
  dump_frame("vibepulse-two-done-queued");
}

int main(int argc, char **argv) {
  /* Radbuffrat även vid pipe: fixtureloggen ska överleva en kill. */
  setvbuf(stdout, NULL, _IOLBF, 0);
  lv_init();
  lv_display_t *disp = lv_sdl_window_create(480, 480);
  lv_sdl_window_set_title(disp, "Torget 480x480 — S agentstatus, T VibePulse, M Max Tracker, [ och ] VibePulse-vy, N nästa app, L launcher");
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

  if (argc == 2 && strcmp(argv[1], "--vibepulse-static-qa") == 0) {
    return run_vibepulse_static_qa();
  }

  /* VibePulse får sin fixtur direkt: launchern ska visa en levande app,
   * inte ett streck, när man tittar in (tangent T matar om). */
  feed_tokens();

  if (argc == 2 && strcmp(argv[1], "--vibepulse-completion-qa") == 0) {
    run_vibepulse_completion_qa();
    return 0;
  }

  cycle_timer = lv_timer_create(next_fixture, 20000, NULL);
  apply_fixture(0);
  lv_timer_create(poll_keys, 50, NULL);

  /* Plattformsrundan: efter Solelkollen-dumparna (klara ~5,5 s in), dumpa
   * launchern och VibePulse också — en obevakad körning bevisar hela
   * plattformen, inte bara första appen. */
  lv_timer_t *tour = lv_timer_create(platform_tour_cb, 6500, NULL);
  lv_timer_set_repeat_count(tour, 27);

  while (1) {
    uint32_t idle = lv_timer_handler();
    lv_delay_ms(idle > 30 ? 30 : idle);
  }
  return 0;
}
