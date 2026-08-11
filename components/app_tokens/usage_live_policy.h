#ifndef USAGE_LIVE_POLICY_H
#define USAGE_LIVE_POLICY_H

#include <stdbool.h>
#include <stdint.h>

#include "agent_status.h"

typedef struct {
  char context[64];
  bool halo_active;
} usage_live_header_view;

typedef struct {
  bool has_total;
  bool has_today;
  int total_px;
  int baseline_px;
  int today_px;
  int marker_x;
} usage_today_bar_view;

typedef enum {
  USAGE_UPDATE_DIRECT,
  USAGE_UPDATE_ANIMATE_FORWARD,
  USAGE_UPDATE_SNAP_BACKWARD,
  USAGE_UPDATE_SILENT,
} usage_update_mode;

void usage_live_build_header(const tk_agent_provider_status *provider,
                             uint64_t packet_age_ms, bool data_stale,
                             bool has_agent_data,
                             usage_live_header_view *out);

bool usage_live_build_today_bar(double total_pct, bool has_total,
                                double today_pct, bool has_today,
                                int track_width, usage_today_bar_view *out);

usage_update_mode usage_live_choose_update(bool initialized, bool stale,
                                           bool visible, bool has_old,
                                           double old_pct, bool has_new,
                                           double new_pct);

#endif
