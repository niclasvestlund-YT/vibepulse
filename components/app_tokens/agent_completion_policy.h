#ifndef AGENT_COMPLETION_POLICY_H
#define AGENT_COMPLETION_POLICY_H

#include <stdbool.h>
#include <stdint.h>

#include "agent_status.h"

#define TK_COMPLETION_QUEUE_CAP 8
#define TK_COMPLETION_SEEN_CAP 16
#define TK_COMPLETION_PULSE_MS 2400ULL
#define TK_COMPLETION_VISIBLE_MS 10000ULL
#define TK_COMPLETION_INITIAL_MAX_AGE_MS 15000U

typedef enum {
  TK_COMPLETION_HIDDEN,
  TK_COMPLETION_PULSE,
  TK_COMPLETION_STATIC,
} tk_completion_phase;

typedef struct {
  int provider;
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
  uint8_t other_active_count;
} tk_completion_event;

typedef struct {
  tk_completion_event events[TK_COMPLETION_QUEUE_CAP];
  char seen_ids[TK_COMPLETION_SEEN_CAP][TK_AGENT_ID_CAP];
  uint8_t count;
  uint8_t seen_count;
  uint8_t seen_next;
  uint64_t current_started_ms;
  uint64_t last_now_ms;
  bool initialized;
} tk_completion_queue;

void tk_completion_queue_apply(tk_completion_queue *queue,
                               const tk_agent_snapshot *snapshot,
                               uint64_t now_ms);
const tk_completion_event *tk_completion_queue_current(
    const tk_completion_queue *queue);
tk_completion_phase tk_completion_phase_at(tk_completion_queue *queue,
                                            uint64_t now_ms);
void tk_completion_queue_dismiss(tk_completion_queue *queue);

#endif
