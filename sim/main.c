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

#include "app_tokens.h"
#include "agent_monitor.h"
#include "agent_monitor_policy.h"
#include "needs_you_send_policy.h"
#include "agent_status_parse.h"
#include "github_status_parse.h"
#include "max_tracker_parse.h"
#include "boot_screen.h"
#include "ota_ui.h"
#include "tokens_parse.h"
#include "torget.h"
#include "vibepulse_recovery.h"
#include "usage_screen.h"
#include "wifi_setup_ui.h"

/* VibePulse är det här repots app och ligger ALLTID först i registret, så
 * launchern och den obevakade rundan pekar på samma index oavsett vilka
 * companion-appar som råkar vara utcheckade. */
#define SIM_APP_VIBEPULSE 0

/* Solelkollen är en companion-app i ett eget repo: finns den utcheckad drar
 * bygget in den och sätter TORGET_HAVE_SOLELKOLLEN, annars kör simulatorn
 * bara VibePulse — precis som targetet. */
#ifdef TORGET_HAVE_SOLELKOLLEN
#include "app_solelkollen.h"
#include "glance_parse.h"

typedef struct { const char *file; const char *name; uint32_t hold_ms; } fixture_t;

static const fixture_t FIXTURES[] = {
  { "midday.json",    "midday (syntetisk, tickande)",      20000 },
  { "evening.json",   "evening (prod 2026-07-16)",          20000 },
  { "midnight.json",  "midnight (prod 2026-07-17)",         20000 },
  { "error-502.json", "error-502 (håller 140 s → stale)",  140000 },
};

static int fixture_idx = -1;
static lv_timer_t *cycle_timer;
#endif /* TORGET_HAVE_SOLELKOLLEN */

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
  /* The interactive "Needs You" takeover: a parked question, an approval, and
   * the privacy/not-approvable case. S cycles into these so all three states
   * are reachable on the glass. */
  "agent-status-needs-you-question.json",
  "agent-status-needs-you-approval.json",
  "agent-status-needs-you-private.json",
};
static int agent_fixture_idx;
static int capture_failures;
static void pump_ms(uint32_t ms);

static const char *const MAX_TRACKER_FIXTURES[] = {
  "max-tracker-full.json",
  "max-tracker-coldstart.json",
  "max-tracker-empty.json",
};
static int max_tracker_fixture_idx;
static int github_fixture_seq;

/* ---------------------------------------------- plattforms-API:t (torget.h) */

/* Simulatorn är entrådad: lv_timer_handler-loopen är enda exekutören, så
 * låset är en no-op och nätväntan meningslös (fixtures behöver inget nät). */
void torget_ui_lock(void)   {}
void torget_ui_unlock(void) {}
bool torget_ui_try_lock(uint32_t timeout_ms) { (void)timeout_ms; return true; }
void torget_net_wait(void)  {}
bool torget_net_recover_http_stall(void) { return false; }
void torget_net_restart_http_stall(void) {}
bool torget_net_http_stall_recovery_booted(void) { return false; }
void torget_keep_awake(void) {} /* ljusrampen finns bara på panelen */
void torget_data_alive(void) {}  /* bootskärmen drivs manuellt i QA:n */

/* Deterministic fixture for exact raster captures: 0 disconnected, 1 weak,
 * 2 medium, 3 strong. Like target RSSI, this says nothing about relay health. */
static uint8_t sim_wifi_signal_bars = 3;
uint8_t torget_wifi_signal_bars(void) { return sim_wifi_signal_bars; }

int64_t torget_now_us(void) { return (int64_t)lv_tick_get() * 1000; }

/* The sim has no network, so a tap on the glass just prints the canonical
 * message the device would sign and POST — the exact bytes the send policy
 * builds, so the wire is provable by fake-panel while the screens are provable
 * here, and the shared policy proves they agree. */
static void sim_needs_you_verdict(tk_needs_you_verdict verdict,
                                  const tk_ir_decision_context *context) {
  const char *name = tk_needs_you_verdict_name(verdict);
  char message[TK_NEEDS_YOU_MESSAGE_CAP];
  uint64_t ts = (uint64_t)(lv_tick_get() / 1000);
  int written = -1;
  if (name && context && context->has_view_sha256 &&
      (context->provider == TK_AGENT_PROVIDER_CLAUDE ||
       context->provider == TK_AGENT_PROVIDER_CODEX)) {
    const char *provider = context->provider == TK_AGENT_PROVIDER_CODEX
                               ? "codex"
                               : "claude";
    written = tk_needs_you_canonical_message_v2(
        message, sizeof message, provider, context->request_id,
        context->view_sha256, name, ts);
  } else if (name && context &&
             context->provider == TK_AGENT_PROVIDER_CLAUDE) {
    written = tk_needs_you_canonical_message(
        message, sizeof message, context->request_id, name, ts);
  }
  if (written > 0) {
    printf("needs-you verdict: %s\n", message);
  } else {
    printf("needs-you verdict: (unsendable)\n");
  }
}

/* ------------------------------------------------------------------ BMP:er */

static void capture_failed(const char *tag, const char *detail) {
  capture_failures++;
  fprintf(stderr, "snapshot: misslyckades (%s): %s\n", tag, detail);
}

/* Dumpa den faktiska framebuffern till en 32-bpp BMP — P18-verktyget och
 * pixelverifieringens facit. LVGL:s XRGB8888 är little-endian BGRX i minnet,
 * vilket är exakt BMP:s radformat, så raderna kopieras rått. Negativ höjd =
 * uppifrån och ner. */
static void dump_draw_buf_frame(lv_draw_buf_t *buf, const char *tag) {
  const char *capture_dir = getenv("TORGET_CAPTURE_DIR");
  if (!capture_dir || capture_dir[0] == '\0') capture_dir = "/tmp";
  char path[256];
  int path_len = snprintf(path, sizeof path, "%s/torget-%s.bmp", capture_dir, tag);
  if (path_len < 0 || (size_t)path_len >= sizeof path) {
    capture_failed(tag, "sökvägen är för lång");
    return;
  }

  int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
  if (fd < 0) {
    char detail[512];
    snprintf(detail, sizeof detail, "kunde inte öppna %s: %s", path, strerror(errno));
    capture_failed(tag, detail);
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
}

static void dump_obj_frame(lv_obj_t *root, const char *tag) {
  /* QA-dumpar får inte bero på var SDL:s nästa refresh råkar ligga. Tvinga
   * layout + redraw före snapshot så ett helt tillstånd fångas atomiskt. */
  lv_obj_update_layout(root);
  lv_obj_invalidate(root);
  lv_draw_buf_t *buf = lv_snapshot_take(root, LV_COLOR_FORMAT_XRGB8888);
  if (!buf) { capture_failed(tag, "kunde inte skapa LVGL-snapshot"); return; }
  dump_draw_buf_frame(buf, tag);
  lv_draw_buf_destroy(buf);
}

/* Normal app captures include lv_layer_top(): that is where the real panel's
 * one shared Wi-Fi mark lives.  Snapshotting only lv_screen_active() silently
 * omitted it even though the physical display composites both layers. */
static void dump_frame(const char *tag) {
  lv_obj_t *screen = lv_screen_active();
  lv_obj_t *top = lv_layer_top();
  lv_obj_update_layout(screen);
  lv_obj_update_layout(top);
  lv_obj_invalidate(screen);
  lv_obj_invalidate(top);
  lv_draw_buf_t *base = lv_snapshot_take(screen, LV_COLOR_FORMAT_XRGB8888);
  lv_draw_buf_t *over = lv_snapshot_take(top, LV_COLOR_FORMAT_ARGB8888);
  if (!base || !over || base->header.w != over->header.w ||
      base->header.h != over->header.h) {
    if (base) lv_draw_buf_destroy(base);
    if (over) lv_draw_buf_destroy(over);
    capture_failed(tag, "kunde inte komponera skärm + topplager");
    return;
  }
  for (uint32_t y = 0; y < base->header.h; y++) {
    uint8_t *dst = base->data + (size_t)y * base->header.stride;
    const uint8_t *src = over->data + (size_t)y * over->header.stride;
    for (uint32_t x = 0; x < base->header.w; x++, dst += 4, src += 4) {
      uint32_t alpha = src[3];
      if (alpha == 0) continue;
      for (int channel = 0; channel < 3; channel++)
        dst[channel] = (uint8_t)((src[channel] * alpha +
                                 dst[channel] * (255U - alpha) + 127U) / 255U);
    }
  }
  dump_draw_buf_frame(base, tag);
  lv_draw_buf_destroy(over);
  lv_draw_buf_destroy(base);
}

/* OTA-ringen bor på lv_layer_top() — utanför skärmträdet som dump_frame
 * fotograferar. Overlayn täcker hela glaset, så en snapshot av topplagret
 * ÄR overlayramen. */
static void dump_overlay_frame(const char *tag) {
  dump_obj_frame(lv_layer_top(), tag);
}

#ifdef TORGET_HAVE_SOLELKOLLEN
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
#endif /* TORGET_HAVE_SOLELKOLLEN */

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

#ifdef TORGET_HAVE_SOLELKOLLEN
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
#endif /* TORGET_HAVE_SOLELKOLLEN */

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

static void apply_github_file(const char *file, bool unique_event) {
  size_t len = 0;
  char *json = read_fixture(file, &len);
  tk_github_status status;
  if (json && tk_github_status_parse(json, len, &status)) {
    if (unique_event && status.has_event) {
      github_fixture_seq++;
      snprintf(status.event_id, sizeof status.event_id, "sim-star-%d",
               github_fixture_seq);
      status.stars += github_fixture_seq - 1;
      status.event_stars = status.stars;
    }
    tokens_apply_github(&status);
    printf("github: %s (%s)\n", file,
           status.has_data ? "data" : "waiting");
  } else {
    printf("github: %s avvisad\n", file);
  }
  free(json);
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

#ifdef TORGET_HAVE_SOLELKOLLEN
static void next_fixture(lv_timer_t *t) {
  (void)t;
  apply_fixture(fixture_idx + 1);
}
#endif

/* Plattformsrundan efter fixturdumparna: deterministiska statiska
 * VibePulse-vyer för bildgranskningen före AMOLED-grinden. */
static void platform_tour_cb(lv_timer_t *t) {
  static int stage;
  (void)t;
  switch (stage++) {
    case 0: torget_launcher_open(); break;
    case 1: dump_frame("launcher"); break;
    case 2: torget_app_show(SIM_APP_VIBEPULSE); tokens_show_view(VIEW_CLAUDE_FABLE); break;
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
 * M: nästa Max Tracker-fixtur. G: simulera en ny GitHub-stjärna.
 * LVGL:s SDL-drivrutin
 * pumpar eventen, så ren tangentbordspollning räcker — ingen indev-
 * rördragning för ett bänkverktyg. */
static void poll_keys(lv_timer_t *t) {
  (void)t;
  static bool held[12];
  const Uint8 *ks = SDL_GetKeyboardState(NULL);
  const SDL_Scancode keys[12] = { SDL_SCANCODE_1, SDL_SCANCODE_2,
                                  SDL_SCANCODE_3, SDL_SCANCODE_4,
                                  SDL_SCANCODE_T, SDL_SCANCODE_S,
                                  SDL_SCANCODE_L, SDL_SCANCODE_N,
                                  SDL_SCANCODE_LEFTBRACKET,
                                  SDL_SCANCODE_RIGHTBRACKET,
                                  SDL_SCANCODE_M, SDL_SCANCODE_G };
  for (int i = 0; i < 12; i++) {
    bool down = ks[keys[i]];
    if (down && !held[i]) {
      if (i < 4) {
#ifdef TORGET_HAVE_SOLELKOLLEN
        apply_fixture(i);
#endif
      }
      else if (i == 4) feed_tokens();
      else if (i == 5) {
        torget_app_show(SIM_APP_VIBEPULSE);
        apply_agent_fixture(agent_fixture_idx + 1);
      }
      else if (i == 6) torget_launcher_open();
      else if (i == 7) torget_app_next();
      else if (i == 8 || i == 9) {
        torget_app_show(SIM_APP_VIBEPULSE);
        int view = usage_screen_current_view() + (i == 8 ? -1 : 1);
        if (view < 0) view = TK_USAGE_SCREEN_VIEWS - 1;
        if (view >= TK_USAGE_SCREEN_VIEWS) view = 0;
        tokens_show_view(view);
      }
      else if (i == 10)
        apply_max_tracker_fixture(max_tracker_fixture_idx + 1);
      else {
        torget_app_show(SIM_APP_VIBEPULSE);
        apply_github_file("github-star.json", true);
      }
    }
    held[i] = down;
  }
}

static void capture_codex_question_variant(const char *tag,
                                           const char *request_id,
                                           const char *prompt,
                                           bool private_view,
                                           uint8_t wifi_bars) {
  size_t len = 0;
  char *json = read_fixture("agent-status-needs-you-codex-question.json", &len);
  tk_agent_snapshot snapshot;
  bool valid = json && tk_agent_status_parse(json, len, &snapshot) &&
               snapshot.pending.provider == TK_AGENT_PROVIDER_CODEX;
  if (valid) {
    snprintf(snapshot.pending.request_id, sizeof snapshot.pending.request_id,
             "%s", request_id);
    if (prompt) {
      snprintf(snapshot.pending.prompt, sizeof snapshot.pending.prompt, "%s",
               prompt);
      snapshot.pending.has_prompt = true;
    }
    if (private_view) {
      snapshot.pending.prompt[0] = '\0';
      snapshot.pending.has_prompt = false;
      snapshot.pending.title[0] = '\0';
      snapshot.pending.has_title = false;
      snapshot.pending.marked = false;
      snapshot.pending.can_approve = false;
    }
    sim_wifi_signal_bars = wifi_bars;
    torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
    torget_wifi_status_foreground();
    tokens_apply_agent_status(&snapshot);
    tk_agent_monitor_needs_you_tap();
    dump_frame(tag);
  } else {
    capture_failed(tag, "Codex question fixture rejected");
  }
  free(json);
}

typedef enum {
  FIT_TITLE, FIT_SUBTITLE, FIT_DESCRIPTION, FIT_COMMAND, FIT_TOOL, FIT_PROMPT
} fit_field;

static void capture_codex_fit_variant(const char *tag, fit_field field,
                                      const char *value) {
  size_t len = 0;
  char *json = read_fixture("agent-status-needs-you-codex-question.json", &len);
  tk_agent_snapshot snapshot;
  bool valid = json && tk_agent_status_parse(json, len, &snapshot);
  if (valid) {
    snprintf(snapshot.pending.request_id, sizeof snapshot.pending.request_id,
             "fit-%u-%s", (unsigned)field, tag + strlen(tag) - 8);
    if (field == FIT_PROMPT) {
      snprintf(snapshot.pending.prompt, sizeof snapshot.pending.prompt,
               "%s", value);
      snapshot.pending.has_prompt = true;
    } else if (field == FIT_TITLE || field == FIT_SUBTITLE) {
      char *target = field == FIT_TITLE ? snapshot.pending.title
                                        : snapshot.pending.subtitle;
      snprintf(target, TK_PENDING_TITLE_CAP, "%s", value);
      snapshot.pending.has_title = true;
      snapshot.pending.has_subtitle = true;
    } else {
      snapshot.pending.kind = TK_PENDING_APPROVAL;
      snapshot.pending.marked = false;
      snapshot.pending.has_prompt = false;
      snapshot.pending.prompt[0] = '\0';
      snprintf(snapshot.pending.title, sizeof snapshot.pending.title,
               "python3 -c 'print(1)'");
      snprintf(snapshot.pending.subtitle, sizeof snapshot.pending.subtitle,
               "Run a harmless local command");
      snprintf(snapshot.pending.tool, sizeof snapshot.pending.tool, "Shell");
      snapshot.pending.has_title = true;
      snapshot.pending.has_subtitle = true;
      snapshot.pending.has_tool = true;
      if (field == FIT_DESCRIPTION)
        snprintf(snapshot.pending.subtitle, sizeof snapshot.pending.subtitle,
                 "%s", value);
      else if (field == FIT_COMMAND)
        snprintf(snapshot.pending.title, sizeof snapshot.pending.title,
                 "%s", value);
      else
        snprintf(snapshot.pending.tool, sizeof snapshot.pending.tool,
                 "%s", value);
    }
    sim_wifi_signal_bars = 3;
    torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
    torget_wifi_status_foreground();
    tokens_apply_agent_status(&snapshot);
    tk_agent_monitor_needs_you_tap();
    dump_frame(tag);
  } else {
    capture_failed(tag, "Codex fit fixture rejected");
  }
  free(json);
}

static void capture_codex_fit_matrix(void) {
  static const char *const boundary[] = {
      "WWWWWWWWWWWW", "WWWWWWWWWWWWWWWWWWWWWW",
      "WWWWWW WWWWWW", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "WWWWWWWWWWWW"};
  static const char *const overbound[] = {
      "WWWWWWWWWWWWWWWWWWWW", "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",
      "WWWWWWWW WWWWWWWW WWWWWWWW WWWWWWWW",
      "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "WWWWWWWWWWWWWWWWWWWW"};
  static const char *const missing[] = {
      "Use \xE2\x82\xAC", "Desktop \xE2\x82\xAC", "Run \xE2\x82\xAC",
      "echo \xE2\x82\xAC", "Shell\xE2\x82\xAC"};
  static const char *const names[] = {
      "title", "subtitle", "description", "command", "tool"};
  for (int i = 0; i < 5; i++) {
    char tag[96];
    snprintf(tag, sizeof tag, "vibepulse-needs-you-fit-%s-boundary", names[i]);
    capture_codex_fit_variant(tag, (fit_field)i, boundary[i]);
    snprintf(tag, sizeof tag, "vibepulse-needs-you-fit-%s-overbound", names[i]);
    capture_codex_fit_variant(tag, (fit_field)i, overbound[i]);
    snprintf(tag, sizeof tag, "vibepulse-needs-you-fit-%s-missing-glyph",
             names[i]);
    capture_codex_fit_variant(tag, (fit_field)i, missing[i]);
  }

  capture_codex_fit_variant(
      "vibepulse-needs-you-fit-prompt-27-boundary", FIT_PROMPT,
      "WWWWWW WWWWWW");
  capture_codex_fit_variant(
      "vibepulse-needs-you-fit-prompt-21-fallback", FIT_PROMPT,
      "Ship pricing now, or hold for tomorrow's review?");
  capture_codex_fit_variant(
      "vibepulse-needs-you-fit-prompt-21-overbound", FIT_PROMPT,
      "Ship the pricing recalibration to production now, or hold it for "
      "tomorrow's review window?");
  capture_codex_fit_variant(
      "vibepulse-needs-you-fit-prompt-missing-glyph", FIT_PROMPT,
      "Approve \xE2\x82\xAC?");
}

/* Needs You v2, the interactive takeover in every stage the policy can put on
 * the glass: the attract summon, the three decision screens, the page it
 * yields to, and the static payoff beat. The two-stage summon is driven by the
 * deterministic tap/press paths that stand in for a glass touch. Payoff is last
 * because it owns the glass for its static window. */
static void capture_needs_you_v2(void) {
  static const char *const codex_long_prompt =
      "Ship pricing now, or hold for tomorrow's review?";
  torget_app_show(SIM_APP_VIBEPULSE);
  feed_tokens();
  tokens_show_view(VIEW_CLAUDE_FABLE);
  sim_wifi_signal_bars = 3;
  torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
  torget_wifi_status_foreground();

  apply_agent_file("agent-status-needs-you-question.json");
  dump_frame("vibepulse-needs-you-attract");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-question");

  /* Widest realistic copy: a long question must never overwrite the
   * recommendation card (physical review 2026-08-16). */
  apply_agent_file("agent-status-needs-you-question-long.json");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-question-long");

  apply_agent_file("agent-status-needs-you-approval.json");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-approval");

  apply_agent_file("agent-status-needs-you-private.json");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-private");

  apply_agent_file("agent-status-claude-working.json");
  tokens_show_view(VIEW_CLAUDE_FABLE);
  dump_frame("vibepulse-needs-you-none");

  /* Codex uses the same tree and exact vertical anchors.  Only the provider
   * fixture, native icon, accent, copy, and deterministic Wi-Fi fixture vary.
   * The long/private variants are derived from the parsed signed-view fixture
   * solely for raster coverage; no verdict is sent from these frames. */
  apply_agent_file("agent-status-needs-you-codex-question.json");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-codex-question");

  capture_codex_question_variant("vibepulse-needs-you-codex-question-long",
                                 "codex-long-question", codex_long_prompt,
                                 false, 3);

  apply_agent_file("agent-status-needs-you-codex-approval.json");
  tk_agent_monitor_needs_you_tap();
  dump_frame("vibepulse-needs-you-codex-approval");

  capture_codex_question_variant("vibepulse-needs-you-codex-private",
                                 "codex-private-view", NULL, true, 3);
  capture_codex_question_variant("vibepulse-needs-you-codex-wifi-weak",
                                 "codex-wifi-weak", NULL, false, 1);
  capture_codex_question_variant("vibepulse-needs-you-codex-wifi-off",
                                 "codex-wifi-off", NULL, false, 0);

  capture_codex_fit_matrix();

  /* The provider belongs to the accepted verdict, not whichever snapshot
   * happens to arrive during the short payoff beat. */
  size_t codex_len = 0, idle_len = 0, working_len = 0, replacement_len = 0;
  char *codex_json = read_fixture("agent-status-needs-you-codex-question.json",
                                  &codex_len);
  char *idle_json = read_fixture("agent-status-idle.json", &idle_len);
  char *working_json = read_fixture("agent-status-claude-working.json",
                                    &working_len);
  char *replacement_json = read_fixture("agent-status-needs-you-question.json",
                                        &replacement_len);
  tk_agent_snapshot codex_payoff, idle, working, replacement;
  bool payoff_valid = codex_json && idle_json && working_json &&
      replacement_json &&
      tk_agent_status_parse(codex_json, codex_len, &codex_payoff) &&
      tk_agent_status_parse(idle_json, idle_len, &idle) &&
      tk_agent_status_parse(working_json, working_len, &working) &&
      tk_agent_status_parse(replacement_json, replacement_len, &replacement);
  if (payoff_valid) {
    int64_t base_us = torget_now_us() + 1000000LL;
    usage_screen_apply_agent(&codex_payoff, base_us);
    tk_agent_monitor_needs_you_tap();
    tk_agent_monitor_needs_you_press(TK_NEEDS_YOU_VERDICT_APPROVE);
    dump_frame("vibepulse-needs-you-codex-payoff");
    usage_screen_apply_agent(&idle, base_us + 1000);
    dump_frame("vibepulse-needs-you-codex-payoff-empty");
    usage_screen_apply_agent(&working, base_us + 2000);
    dump_frame("vibepulse-needs-you-codex-payoff-claude");
    usage_screen_apply_agent(&replacement, base_us + 2499999LL);
    dump_frame("vibepulse-needs-you-codex-payoff-replacement-pre-expiry");
    usage_screen_tick(base_us + 2500000LL);
    dump_frame("vibepulse-needs-you-codex-payoff-exact-expiry");
    usage_screen_tick(base_us + 2500001LL);
    dump_frame("vibepulse-needs-you-codex-payoff-post-expiry");
  } else {
    capture_failed("vibepulse-needs-you-codex-payoff",
                   "payoff timing fixture rejected");
  }
  free(codex_json);
  free(idle_json);
  free(working_json);
  free(replacement_json);

  /* Payoff owns the glass briefly, so it remains last. */
  sim_wifi_signal_bars = 3;
  apply_agent_file("agent-status-needs-you-question.json");
  tk_agent_monitor_needs_you_tap();
  tk_agent_monitor_needs_you_press(TK_NEEDS_YOU_VERDICT_APPROVE);
  dump_frame("vibepulse-needs-you-payoff");
}

static void capture_wifi_surface(const char *surface) {
  for (uint8_t bars = 0; bars <= 3; bars++) {
    char tag[64];
    sim_wifi_signal_bars = bars;
    torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
    torget_wifi_status_foreground();
    snprintf(tag, sizeof tag, "wifi-global-%s-%u", surface, (unsigned)bars);
    dump_frame(tag);
  }
}

/* Every ordinary surface uses the same page-shell image. The matrix proves
 * all four physical association states on provider pages, non-provider pages,
 * the launcher, Needs You, and the optional companion when present. */
static void capture_global_wifi_matrix(void) {
  torget_launcher_open();
  capture_wifi_surface("launcher");

  torget_app_show(SIM_APP_VIBEPULSE);
  feed_tokens();
  tokens_show_view(VIEW_CLAUDE_FABLE);
  capture_wifi_surface("claude");
  tokens_show_view(VIEW_CODEX_WEEKLY);
  capture_wifi_surface("codex");
  tokens_show_view(VIEW_VALUE);
  capture_wifi_surface("value");
  apply_github_file("github.json", false);
  tokens_show_view(VIEW_GITHUB);
  capture_wifi_surface("github");

  apply_agent_file("agent-status-needs-you-codex-question.json");
  tk_agent_monitor_needs_you_tap();
  capture_wifi_surface("needs-you");

#ifdef TORGET_HAVE_SOLELKOLLEN
  torget_app_show(1);
  capture_wifi_surface("companion");
#endif
}

static void capture_wifi_drift_matrix(void) {
  static const char *tags[5] = {
      "wifi-drift-0", "wifi-drift-1", "wifi-drift-2",
      "wifi-drift-3", "wifi-drift-return",
  };
  torget_app_show(SIM_APP_VIBEPULSE);
  feed_tokens();
  tokens_show_view(VIEW_CODEX_WEEKLY);
  sim_wifi_signal_bars = 3;
  torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
  torget_wifi_status_foreground();
  dump_frame(tags[0]);
  for (int i = 1; i < 5; i++) {
    torget_drift_step();
    pump_ms(1300);
    dump_frame(tags[i]);
  }
}

static int run_vibepulse_static_qa(void) {
  capture_failures = 0;
  torget_wifi_status_set_mode(TG_WIFI_STATUS_NORMAL);
  torget_wifi_status_foreground();
  torget_app_show(SIM_APP_VIBEPULSE);

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

  /* GitHub is one optional full-screen project section: stars dominate,
   * forks stay secondary, and provenance is tested with identical metrics. */
  apply_github_file("github.json", false);
  tokens_show_view(VIEW_GITHUB);
  dump_frame("vibepulse-github-live");
  apply_github_file("github-stale.json", false);
  dump_frame("vibepulse-github-cached");
  apply_github_file("github-missing.json", false);
  dump_frame("vibepulse-github-missing");

  /* The same simulated star event overlays an unrelated current page and
   * hides without changing that page. Motion is intentionally not part of
   * this static gate. */
  apply_github_file("github.json", false);
  tokens_show_view(VIEW_CODEX_WEEKLY);
  dump_frame("vibepulse-github-popup-before");
  apply_github_file("github-star.json", true);
  dump_frame("vibepulse-github-star-popup");
  usage_screen_tick(torget_now_us() + 121000000LL);
  dump_frame("vibepulse-github-popup-return");

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

  /* Tourens två tracker-dumpar (case 15-18 i platform_tour_cb) återanvänder
   * NAMNEN "vibepulse-tracker-claude"/"-codex" för en berättande
   * coldstart→full-övergång; static-QA:n här bevisar istället FYRA skilda
   * tillstånd (coldstart, full, tom, stale) under egna namn så varje
   * rastersökväg får oberoende täckning — stänger Task 8:s uppskjutna
   * anmärkning om att stale/tom/no-data-trackerlägen hörde till Task 9. */
  feed_max_tracker_file("max-tracker-coldstart.json");
  tokens_show_view(VIEW_TRACKER_CLAUDE);
  dump_frame("vibepulse-tracker-claude-coldstart");

  feed_max_tracker_file("max-tracker-full.json");
  tokens_show_view(VIEW_TRACKER_CODEX);
  dump_frame("vibepulse-tracker-codex-full");

  feed_max_tracker_file("max-tracker-empty.json");
  dump_frame("vibepulse-tracker-empty");

  feed_max_tracker_file("max-tracker-full.json");
  usage_screen_set_stale(true);
  dump_frame("vibepulse-tracker-stale");
  usage_screen_set_stale(false);

  /* Bootskärmen: tre stadier innan den river sig själv. Skapas HÄR (inte
   * vid simstart) så övriga QA-dumpar slipper lagret ovanpå sig. */
  torget_wifi_status_set_mode(TG_WIFI_STATUS_HIDDEN);
  torget_boot_screen_create();
  dump_overlay_frame("boot-cold");
  torget_boot_screen_stage(TG_BOOT_WIFI_UP);
  dump_overlay_frame("boot-wifi");
  torget_boot_screen_stage(TG_BOOT_TIME_OK);
  dump_overlay_frame("boot-time");
  torget_boot_screen_stage(TG_BOOT_DATA_OK); /* river lagret */

  /* OTA-ringen (riktning A, 2026-08-14): fyra ärliga lägen — lucktid,
   * mottagen andel, verifiering och omstart. Versionsraden byter från
   * körande till inkommande version exakt som tjänsten gör det: körande
   * vid öppet fönster, inkommande så fort metadatan är läst. Overlayn
   * göms igen sist så en framtida dump efter denna ser apparna. */
  torget_ota_ui_set_version("v0.2.1-16-g9f9af53");
  torget_ota_ui_set(TG_OTA_UI_OPEN, 0, 583);
  dump_overlay_frame("ota-ring-open");
  torget_ota_ui_set_version("v0.2.1-24-g4451646");
  torget_ota_ui_set(TG_OTA_UI_RECEIVING, 62, 0);
  dump_overlay_frame("ota-ring-receiving");
  torget_ota_ui_set(TG_OTA_UI_VERIFYING, 100, 0);
  dump_overlay_frame("ota-ring-verifying");
  torget_ota_ui_set(TG_OTA_UI_RESTARTING, 100, 0);
  dump_overlay_frame("ota-ring-restarting");
  /* UPDATE READY-takeovern: annonserad version pa versionsraden. */
  torget_ota_ui_set_version("v0.2.1-31-gnotice");
  torget_ota_ui_set(TG_OTA_UI_NOTICE, 0, 0);
  dump_overlay_frame("ota-ring-notice");
  torget_ota_ui_set(TG_OTA_UI_HIDDEN, 0, 0);

  /* Wi-Fi onboarding uses the exact target overlay. These states make
   * the phone/Mac setup path visible and keep its copy/layout in the same
   * deterministic 480x480 regression set as OTA and Needs You. */
  torget_wifi_ui_set(TG_WIFI_UI_SEARCHING, "Niclas iPhone", NULL,
                     "NOT SEEN - 2.4 GHZ ONLY", 24);
  dump_overlay_frame("wifi-searching");
  torget_wifi_ui_set(TG_WIFI_UI_STARTING, NULL, NULL, NULL, 0);
  dump_overlay_frame("wifi-starting");
  torget_wifi_ui_set(TG_WIFI_UI_OPEN, "VibePulse-setup", "A1B2C3D4E5F6",
                     NULL, 583);
  dump_overlay_frame("wifi-setup-open");
  dump_overlay_frame("wifi-setup-qr");
  torget_wifi_ui_set_manual_details(true);
  dump_overlay_frame("wifi-setup-manual");
  torget_wifi_ui_set_manual_details(false);
  torget_wifi_ui_set(TG_WIFI_UI_JOINING, "Niclas iPhone", NULL, NULL, 0);
  dump_overlay_frame("wifi-joining");
  torget_wifi_ui_set(TG_WIFI_UI_JOINED, "Niclas iPhone", NULL, NULL, 0);
  dump_overlay_frame("wifi-joined");
  torget_wifi_ui_set(TG_WIFI_UI_FAILED, "Niclas iPhone", NULL,
                     "WRONG PASSWORD - TRY AGAIN ON PHONE", 0);
  dump_overlay_frame("wifi-failed-password");
  torget_wifi_ui_set(TG_WIFI_UI_HIDDEN, NULL, NULL, NULL, 0);

  /* Value multiple. Every state the parser can hand the page gets its own
   * raster, because the whole design premise is that a wrong figure here is
   * worse than no figure — the dashes need proving as much as the number. */
  tk_tokens value_truth = {0};
  value_truth.value.state = TK_VALUE_OK;
  value_truth.value.has_value_usd = 1;
  value_truth.value.value_usd = 312.0;
  value_truth.value.has_plan_usd = 1;
  value_truth.value.plan_usd = 100.0;
  value_truth.value.has_multiple = 1;
  value_truth.value.multiple = 3.12;
  value_truth.value.cost_configured = 1;
  tokens_apply(&value_truth);
  tokens_show_view(VIEW_VALUE);
  dump_frame("vibepulse-value-ahead");

  /* Early in the month: below break-even, so the bar is partial and the
   * hero keeps two decimals rather than rounding up to a false 1.0. */
  tk_tokens value_early = value_truth;
  value_early.value.value_usd = 97.0;
  value_early.value.multiple = 0.97;
  tokens_apply(&value_early);
  dump_frame("vibepulse-value-early");

  /* Widest realistic copy: four-figure value, two-digit multiple, and the
   * estimated-plan caption that runs longest. */
  tk_tokens value_wide = value_truth;
  value_wide.value.value_usd = 2480.0;
  value_wide.value.plan_usd = 200.0;
  value_wide.value.multiple = 12.4;
  value_wide.value.cost_configured = 0;
  tokens_apply(&value_wide);
  dump_frame("vibepulse-value-wide");

  tk_tokens value_no_plan = {0};
  value_no_plan.value.state = TK_VALUE_NO_PLAN_COST;
  value_no_plan.value.has_value_usd = 1;
  value_no_plan.value.value_usd = 312.0;
  tokens_apply(&value_no_plan);
  dump_frame("vibepulse-value-no-plan-cost");

  tk_tokens value_partial = {0};
  value_partial.value.state = TK_VALUE_PARTIAL;
  tokens_apply(&value_partial);
  dump_frame("vibepulse-value-partial");

  tk_tokens value_none = {0};
  tokens_apply(&value_none);
  dump_frame("vibepulse-value-no-data");

  /* Both providers, each with its own plan cost and its own break-even. */
  tk_tokens value_both = value_truth;
  value_both.value.value_usd = 312.0;
  value_both.value.plan_usd = 220.0;
  value_both.value.multiple = 1.42;
  value_both.value.has_claude_usd = 1;
  value_both.value.claude_usd = 280.0;
  value_both.value.has_claude_plan_usd = 1;
  value_both.value.claude_plan_usd = 200.0;
  value_both.value.has_codex_usd = 1;
  value_both.value.codex_usd = 32.0;
  value_both.value.has_codex_plan_usd = 1;
  value_both.value.codex_plan_usd = 20.0;
  tokens_apply(&value_both);
  dump_frame("vibepulse-value-both");

  /* Codex below its own break-even while Claude is well past: the whole
   * reason the bars are separate rather than blended. */
  tk_tokens value_uneven = value_both;
  value_uneven.value.codex_usd = 6.0;
  value_uneven.value.value_usd = 286.0;
  value_uneven.value.multiple = 1.30;
  tokens_apply(&value_uneven);
  dump_frame("vibepulse-value-uneven");

  /* One subscription: one bar, and no empty second block. */
  tk_tokens value_solo = value_both;
  value_solo.value.has_codex_usd = 0;
  value_solo.value.codex_usd = 0;
  value_solo.value.has_codex_plan_usd = 0;
  value_solo.value.value_usd = 280.0;
  value_solo.value.plan_usd = 200.0;
  value_solo.value.multiple = 1.40;
  tokens_apply(&value_solo);
  dump_frame("vibepulse-value-solo");

  capture_wifi_drift_matrix();
  capture_global_wifi_matrix();

  /* The Needs You takeover last: its payoff beat owns the glass, so nothing
   * captured after it would see the page underneath. */
  capture_needs_you_v2();

  return capture_failures == 0 ? 0 : 1;
}

/* Pumpa verklig LVGL-tid: pulsdumparna är MÄNNISKOGRANSKNING, inte
 * pixelfacit — tidsstyrda bildrutor hör inte hemma i deterministiska
 * assertioner. */
static void pump_ms(uint32_t ms) {
  uint32_t start = lv_tick_get();
  while (lv_tick_get() - start < ms) {
    lv_timer_handler();
    usleep(5 * 1000);
  }
}

static void run_vibepulse_pulse_qa(void) {
  torget_app_show(SIM_APP_VIBEPULSE);
  tokens_show_view(VIEW_CLAUDE_FABLE);
  apply_agent_file("agent-status-claude-waiting.json");
  dump_frame("vibepulse-pulse-t0-bright");
  pump_ms(600);
  dump_frame("vibepulse-pulse-t600-dim");
  pump_ms(600);
  dump_frame("vibepulse-pulse-t1200-bright");
}

static void run_vibepulse_completion_qa(void) {
  torget_app_show(SIM_APP_VIBEPULSE);
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

/* Needs You review deliverable: the interactive takeover in each state the
 * policy can produce, plus the no-pending page it yields to. Kept out of the
 * pixel-landmark set on purpose — the design is under review, so these are for
 * a human to look at, not for an assertion to pin geometry that is not approved
 * yet. Taps in the sim resolve locally via tk_needs_you_mark_answered because
 * no verdict callback is wired here (that is the app layer's later job). */
static int run_vibepulse_needs_you_qa(void) {
  capture_failures = 0;
  capture_needs_you_v2();
  return capture_failures == 0 ? 0 : 1;
}

static int run_vibepulse_needs_you_render_qa(void) {
  size_t len = 0;
  char *json = read_fixture("agent-status-needs-you-codex-question.json", &len);
  tk_agent_snapshot snapshot;
  if (!json || !tk_agent_status_parse(json, len, &snapshot)) {
    free(json);
    return 1;
  }
  free(json);

  const int64_t base_us = 1000000LL;
  tk_agent_monitor_render_stats_reset();
  usage_screen_apply_agent(&snapshot, base_us);
  for (int i = 1; i <= 20; i++)
    usage_screen_tick(base_us + (int64_t)i * 100000LL);

  tk_agent_render_stats stats;
  tk_agent_monitor_render_stats(&stats);
  printf("full_repaints=%u ring_updates=%u unchanged_ticks=%u\n",
         (unsigned)stats.full_repaints, (unsigned)stats.ring_updates,
         (unsigned)stats.unchanged_ticks);
  return 0;
}

int main(int argc, char **argv) {
  /* Radbuffrat även vid pipe: fixtureloggen ska överleva en kill. */
  setvbuf(stdout, NULL, _IOLBF, 0);
  lv_init();
  lv_display_t *disp = lv_sdl_window_create(480, 480);
  lv_sdl_window_set_title(disp, "Torget 480x480 — G GitHub-star, S agentstatus, T VibePulse, M Max Tracker, [ och ] vy, N nästa app, L launcher");
  lv_sdl_mouse_create();

  torget_ui_create(); /* bygger apparna via registret, går in i app 0 */
  tk_agent_monitor_set_needs_you_cb(sim_needs_you_verdict);
  /* Wi-Fi-lagret före OTA-ringen — samma topplagerordning som targetet,
   * där READY-ringen ska vinna om båda har något att visa. */
  torget_wifi_ui_create();
  /* OTA-ringen på topplagret, dold tills QA-dumparna väcker den — samma
   * ordning som targetets app_main (overlay EFTER det delade UI:t). */
  torget_ota_ui_create();

  if (argc == 2 &&
      strcmp(argv[1], "--vibepulse-needs-you-render-qa") == 0) {
    return run_vibepulse_needs_you_render_qa();
  }

#ifdef TORGET_HAVE_SOLELKOLLEN
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
#endif

  if (argc == 2 && strcmp(argv[1], "--vibepulse-static-qa") == 0) {
    return run_vibepulse_static_qa();
  }

  if (argc == 2 && strcmp(argv[1], "--vibepulse-needs-you-qa") == 0) {
    return run_vibepulse_needs_you_qa();
  }

  /* VibePulse får sin fixtur direkt: launchern ska visa en levande app,
   * inte ett streck, när man tittar in (tangent T matar om). */
  feed_tokens();

  if (argc == 2 && strcmp(argv[1], "--vibepulse-completion-qa") == 0) {
    run_vibepulse_completion_qa();
    return 0;
  }

  if (argc == 2 && strcmp(argv[1], "--vibepulse-pulse-qa") == 0) {
    run_vibepulse_pulse_qa();
    return 0;
  }

#ifdef TORGET_HAVE_SOLELKOLLEN
  cycle_timer = lv_timer_create(next_fixture, 20000, NULL);
  apply_fixture(0);
#endif
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
