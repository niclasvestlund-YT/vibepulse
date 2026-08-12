#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/agent_monitor_policy.h"
#include "../components/app_tokens/usage_live_policy.h"

static int failures;

static void check(const char *what, bool condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_agent_status job(tk_agent_state state, uint32_t updated_ms,
                           const char *model, const char *effort) {
  tk_agent_status value = {0};
  value.state = state;
  value.updated_ms = updated_ms;
  if (model) {
    value.has_model = true;
    snprintf(value.model, sizeof value.model, "%s", model);
  }
  if (effort) {
    value.has_effort = true;
    snprintf(value.effort, sizeof value.effort, "%s", effort);
  }
  return value;
}

static void add_job(tk_agent_provider_status *provider,
                    tk_agent_status value) {
  provider->jobs[provider->job_count++] = value;
}

static void check_header(const char *what,
                         const tk_agent_provider_status *provider,
                         uint64_t packet_age_ms, bool data_stale,
                         bool has_agent_data, const char *context,
                         bool halo_active) {
  usage_live_header_view view = {0};
  usage_live_build_header(provider, packet_age_ms, data_stale, has_agent_data,
                          &view);
  check(what, strcmp(view.context, context) == 0 &&
                  view.halo_active == halo_active);
}

static void check_unavailable_bar(const char *what, double total,
                                  bool has_total, double today,
                                  bool has_today, int width) {
  usage_today_bar_view view = {true, true, 1, 1, 1, 1};
  check(what, !usage_live_build_today_bar(total, has_total, today, has_today,
                                          width, &view) &&
                  !view.has_total && !view.has_today && !view.total_px &&
                  !view.baseline_px && !view.today_px && !view.marker_x);
}

int main(void) {
  usage_live_header_view empty = {{'x'}, true};
  usage_live_build_header(NULL, 0, false, false, &empty);
  check("missing agent snapshot leaves header unavailable",
        !empty.context[0] && !empty.halo_active);
  usage_live_build_header(NULL, 0, false, false, NULL);

  tk_agent_provider_status zero_jobs = {0};
  check_header("zero jobs reports no active chat", &zero_jobs, 0, false, true,
               "NO ACTIVE CHAT", false);

  tk_agent_provider_status one_working = {0};
  one_working.active_count = 4;
  add_job(&one_working, job(TK_AGENT_WORKING, 0, "GPT-5", "HIGH"));
  check_header("one working job names its complete metadata", &one_working, 0,
               false, true, "NOW · GPT-5 · HIGH", true);

  tk_agent_provider_status model_only = {0};
  add_job(&model_only, job(TK_AGENT_WORKING, 0, "Claude", NULL));
  check_header("model-only metadata omits effort", &model_only, 0, false,
               true, "NOW · Claude", true);
  tk_agent_provider_status effort_only = {0};
  add_job(&effort_only, job(TK_AGENT_WORKING, 0, NULL, "LOW"));
  check_header("effort-only metadata omits model", &effort_only, 0, false,
               true, "NOW · LOW", true);
  tk_agent_provider_status no_metadata = {0};
  add_job(&no_metadata, job(TK_AGENT_WORKING, 0, NULL, NULL));
  check_header("working without metadata is still NOW", &no_metadata, 0,
               false, true, "NOW", true);

  tk_agent_provider_status two_jobs = {0};
  add_job(&two_jobs, job(TK_AGENT_WORKING, 0, "GPT-5", "HIGH"));
  add_job(&two_jobs, job(TK_AGENT_WAITING, 0, NULL, NULL));
  check_header("two effective jobs never select one model", &two_jobs, 0,
               false, true, "2 CHATS ACTIVE", true);
  tk_agent_provider_status four_jobs = {0};
  add_job(&four_jobs, job(TK_AGENT_WORKING, 0, NULL, NULL));
  add_job(&four_jobs, job(TK_AGENT_WAITING, 0, NULL, NULL));
  add_job(&four_jobs, job(TK_AGENT_ERROR, 0, NULL, NULL));
  add_job(&four_jobs, job(TK_AGENT_DONE, 0, NULL, NULL));
  check_header("four shaped jobs count only effective active jobs", &four_jobs,
               0, false, true, "3 CHATS ACTIVE", true);
  tk_agent_provider_status four_active_jobs = {0};
  add_job(&four_active_jobs, job(TK_AGENT_WORKING, 0, NULL, NULL));
  add_job(&four_active_jobs, job(TK_AGENT_WAITING, 0, NULL, NULL));
  add_job(&four_active_jobs, job(TK_AGENT_ERROR, 0, NULL, NULL));
  add_job(&four_active_jobs, job(TK_AGENT_WORKING, 0, NULL, NULL));
  check_header("four effective jobs report the exact active count",
               &four_active_jobs, 0, false, true, "4 CHATS ACTIVE", true);

  tk_agent_provider_status nonworking = {0};
  add_job(&nonworking, job(TK_AGENT_WAITING, 0, NULL, NULL));
  check_header("one waiting job remains truthful without halo", &nonworking,
               0, false, true, "1 CHAT ACTIVE", false);
  nonworking.jobs[0].state = TK_AGENT_ERROR;
  check_header("one error job remains truthful without halo", &nonworking, 0,
               false, true, "1 CHAT ACTIVE", false);
  check_header("error stays active at packet lease boundary", &nonworking,
               TK_AGENT_WORKING_LEASE_MS, false, true, "1 CHAT ACTIVE", false);
  check_header("error expires after packet lease", &nonworking,
               TK_AGENT_WORKING_LEASE_MS + 1, false, true,
               "NO ACTIVE CHAT", false);
  nonworking.jobs[0].state = TK_AGENT_WAITING;
  check_header("waiting stays active at packet lease boundary", &nonworking,
               TK_AGENT_WORKING_LEASE_MS, false, true, "1 CHAT ACTIVE", false);
  check_header("waiting expires after packet lease", &nonworking,
               TK_AGENT_WORKING_LEASE_MS + 1, false, true,
               "NO ACTIVE CHAT", false);
  nonworking.jobs[0].state = TK_AGENT_DONE;
  check_header("done alone is not an active chat", &nonworking, 0, false,
               true, "NO ACTIVE CHAT", false);

  tk_agent_provider_status expired = {0};
  add_job(&expired, job(TK_AGENT_WORKING, TK_AGENT_WORKING_LEASE_MS, "GPT-5",
                        "HIGH"));
  check_header("expired packet lease never presents stale NOW", &expired, 1,
               false, true, "NO ACTIVE CHAT", false);
  check_header("stale data defers header text to the screen stale contract",
               &one_working, 0, true, true, "", false);
  check_header("unavailable flag suppresses otherwise shaped provider data",
               &one_working, 0, false, false, "", false);

  usage_today_bar_view bar = {0};
  check("missing total is unavailable",
        !usage_live_build_today_bar(0, false, 0, false, 436, &bar) &&
            !bar.has_total);
  check("zero total produces an empty available bar",
        usage_live_build_today_bar(0, true, 0, true, 436, &bar) &&
            bar.has_total && bar.has_today && !bar.total_px &&
            !bar.baseline_px && !bar.today_px && !bar.marker_x);
  check("nine percent rounds independently",
        usage_live_build_today_bar(9, true, 1, true, 436, &bar) &&
            bar.total_px == 39 && bar.baseline_px == 35 &&
            bar.today_px == 4 && bar.marker_x == 35);
  check("seventy-three total and twelve today use the segmented geometry",
        usage_live_build_today_bar(73, true, 12, true, 436, &bar) &&
            bar.total_px == 318 && bar.baseline_px == 266 &&
            bar.today_px == 52 && bar.marker_x == 266);
  check("ninety-nine percent preserves a one percent today segment",
        usage_live_build_today_bar(99, true, 1, true, 436, &bar) &&
            bar.total_px == 432 && bar.baseline_px == 427 &&
            bar.today_px == 5 && bar.marker_x == 427);
  check("one hundred percent total accepts today twelve",
        usage_live_build_today_bar(100, true, 12, true, 436, &bar) &&
            bar.total_px == 436 && bar.baseline_px == 384 &&
            bar.today_px == 52 && bar.marker_x == 384);
  check("missing today retains a single accent fill without marker",
        usage_live_build_today_bar(73, true, 0, false, 436, &bar) &&
            bar.has_total && !bar.has_today && bar.total_px == 318 &&
            bar.baseline_px == 318 && !bar.today_px && !bar.marker_x);
  check_unavailable_bar("today greater than total is rejected", 9, true, 12,
                        true, 436);
  check_unavailable_bar("negative total is rejected", -1, true, 0, true,
                        436);
  check_unavailable_bar("negative today is rejected", 9, true, -1, true,
                        436);
  check_unavailable_bar("nonfinite total is rejected", NAN, true, 0, true,
                        436);
  check_unavailable_bar("nonfinite today is rejected", 9, true, INFINITY,
                        true, 436);
  check_unavailable_bar("nonpositive width is rejected", 9, true, 1, true,
                        0);

  check("missing new data is silent",
        usage_live_choose_update(true, false, true, true, 10, false, 0) ==
            USAGE_UPDATE_SILENT);
  check("nonfinite new data is silent",
        usage_live_choose_update(true, false, true, true, 10, true, NAN) ==
            USAGE_UPDATE_SILENT);
  check("first accepted load is direct",
        usage_live_choose_update(false, false, true, false, 0, true, 10) ==
            USAGE_UPDATE_DIRECT);
  check("first accepted stale sample is silent",
        usage_live_choose_update(false, true, true, false, 0, true, 10) ==
            USAGE_UPDATE_SILENT);
  check("first accepted hidden sample is silent",
        usage_live_choose_update(false, false, false, false, 0, true, 10) ==
            USAGE_UPDATE_SILENT);
  check("stale accepted sample is silent",
        usage_live_choose_update(true, true, true, true, 10, true, 20) ==
            USAGE_UPDATE_SILENT);
  check("hidden accepted sample is silent",
        usage_live_choose_update(true, false, false, true, 10, true, 20) ==
            USAGE_UPDATE_SILENT);
  check("visible fresh increase animates forward",
        usage_live_choose_update(true, false, true, true, 10, true, 20) ==
            USAGE_UPDATE_ANIMATE_FORWARD);
  check("newer increase during animation is another forward decision",
        usage_live_choose_update(true, false, true, true, 20, true, 30) ==
            USAGE_UPDATE_ANIMATE_FORWARD);
  check("visible fresh decrease snaps backward",
        usage_live_choose_update(true, false, true, true, 20, true, 10) ==
            USAGE_UPDATE_SNAP_BACKWARD);
  check("visible fresh equal value is direct",
        usage_live_choose_update(true, false, true, true, 20, true, 20) ==
            USAGE_UPDATE_DIRECT);

  if (!failures) {
    printf("OK: live quota policy tests green\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
