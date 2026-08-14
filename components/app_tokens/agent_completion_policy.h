#ifndef AGENT_COMPLETION_POLICY_H
#define AGENT_COMPLETION_POLICY_H

#include <stdbool.h>
#include <stdint.h>

#include "agent_status.h"

#define TK_COMPLETION_QUEUE_CAP 8
#define TK_COMPLETION_SEEN_CAP 16
/* 45 s puls (beslut 2026-08-14): 4,8 s missades i praktiken — den som
 * tittar bort en halvminut ska fortfarande motas av ett ANDANDES larm.
 * DONE-kort begransas av VISIBLE_MS och pulserar hela sin livstid. */
#define TK_COMPLETION_PULSE_MS 45000ULL
#define TK_COMPLETION_VISIBLE_MS 10000ULL
#define TK_COMPLETION_INITIAL_MAX_AGE_MS 15000U
/* Ett larm är en händelse man kan agera på i stunden. Äldre tillstånd än så
 * här (serveromstart, återhämtat avbrott) hör hemma i headern, inte som
 * helskärm — 2 min täcker pollcykeln på 30 s med god marginal. */
#define TK_COMPLETION_FRESH_MAX_AGE_MS (2U * 60U * 1000U)

typedef enum {
  TK_COMPLETION_HIDDEN,
  TK_COMPLETION_PULSE,
  TK_COMPLETION_STATIC,
} tk_completion_phase;

typedef struct {
  int provider;
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
  tk_agent_state state;
  uint8_t same_state_count;
  uint8_t other_active_count;
  uint32_t updated_ms;
} tk_completion_event;

typedef struct {
  tk_completion_event events[TK_COMPLETION_QUEUE_CAP];
  char seen_ids[TK_COMPLETION_SEEN_CAP][TK_AGENT_ID_CAP];
  int seen_providers[TK_COMPLETION_SEEN_CAP];
  uint8_t count;
  uint8_t seen_count;
  uint8_t seen_next;
  uint64_t current_started_ms;
  uint64_t last_now_ms;
  bool initialized;
} tk_completion_queue;

typedef struct {
  /* Provider/state/count determine provider, title, detail, icon and accent;
   * event_id is the generation key and project is the remaining visible copy. */
  bool initialized;
  bool visible;
  int provider;
  tk_agent_state state;
  uint8_t same_state_count;
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
} tk_completion_render_key;

void tk_completion_queue_apply(tk_completion_queue *queue,
                               const tk_agent_snapshot *snapshot,
                               uint64_t now_ms);
const tk_completion_event *tk_completion_queue_current(
    const tk_completion_queue *queue);
tk_completion_phase tk_completion_phase_at(tk_completion_queue *queue,
                                            uint64_t now_ms);
void tk_completion_queue_dismiss(tk_completion_queue *queue);
bool tk_completion_render_key_update(tk_completion_render_key *rendered,
                                     const tk_completion_event *event,
                                     bool visible);

#endif
