#include "agent_completion_policy.h"

#include <stdio.h>
#include <string.h>

typedef struct {
  const tk_agent_status *job;
  int provider;
} completion_candidate;

static bool seen(const tk_completion_queue *queue, const char *event_id) {
  for (uint8_t i = 0; i < queue->seen_count; i++) {
    if (strcmp(queue->seen_ids[i], event_id) == 0) return true;
  }
  return false;
}

static void remember(tk_completion_queue *queue, const char *event_id) {
  if (!event_id[0] || seen(queue, event_id)) return;
  uint8_t slot;
  if (queue->seen_count < TK_COMPLETION_SEEN_CAP) {
    slot = queue->seen_count++;
  } else {
    slot = queue->seen_next;
    queue->seen_next =
        (uint8_t)((queue->seen_next + 1U) % TK_COMPLETION_SEEN_CAP);
  }
  snprintf(queue->seen_ids[slot], sizeof queue->seen_ids[slot], "%s",
           event_id);
}

static uint8_t total_active(const tk_agent_snapshot *snapshot) {
  uint16_t total = (uint16_t)snapshot->claude.active_count +
                   (uint16_t)snapshot->codex.active_count;
  return total > UINT8_MAX ? UINT8_MAX : (uint8_t)total;
}

static void drop_oldest_pending(tk_completion_queue *queue) {
  if (queue->count < 2) return;
  memmove(&queue->events[1], &queue->events[2],
          (size_t)(queue->count - 2) * sizeof queue->events[0]);
  queue->count--;
}

static void append_candidate(tk_completion_queue *queue,
                             const completion_candidate *candidate,
                             uint8_t other_active_count, uint64_t now_ms) {
  if (queue->count == TK_COMPLETION_QUEUE_CAP) {
    drop_oldest_pending(queue);
  }
  if (queue->count == TK_COMPLETION_QUEUE_CAP) return;
  bool was_empty = queue->count == 0;
  tk_completion_event *event = &queue->events[queue->count++];
  memset(event, 0, sizeof *event);
  event->provider = candidate->provider;
  event->other_active_count = other_active_count;
  snprintf(event->event_id, sizeof event->event_id, "%s",
           candidate->job->event_id);
  snprintf(event->project, sizeof event->project, "%s",
           candidate->job->project);
  if (was_empty) queue->current_started_ms = now_ms;
}

void tk_completion_queue_apply(tk_completion_queue *queue,
                               const tk_agent_snapshot *snapshot,
                               uint64_t now_ms) {
  if (!queue || !snapshot) return;
  bool first_snapshot = !queue->initialized;
  queue->initialized = true;
  queue->last_now_ms = now_ms;

  completion_candidate candidates[TK_AGENT_PROVIDER_COUNT *
                                  TK_AGENT_JOBS_MAX];
  size_t candidate_count = 0;
  const tk_agent_provider_status *providers[TK_AGENT_PROVIDER_COUNT] = {
      &snapshot->claude,
      &snapshot->codex,
  };
  for (int provider = 0; provider < TK_AGENT_PROVIDER_COUNT; provider++) {
    for (uint8_t i = 0; i < providers[provider]->job_count; i++) {
      const tk_agent_status *job = &providers[provider]->jobs[i];
      if (job->state != TK_AGENT_DONE || !job->event_id[0] ||
          seen(queue, job->event_id)) {
        continue;
      }
      remember(queue, job->event_id);
      if (first_snapshot &&
          job->updated_ms > TK_COMPLETION_INITIAL_MAX_AGE_MS) {
        continue;
      }
      candidates[candidate_count++] = (completion_candidate){
          .job = job,
          .provider = provider,
      };
    }
  }

  for (size_t i = 1; i < candidate_count; i++) {
    completion_candidate candidate = candidates[i];
    size_t position = i;
    while (position > 0) {
      completion_candidate previous = candidates[position - 1];
      bool comes_first =
          candidate.job->updated_ms > previous.job->updated_ms ||
          (candidate.job->updated_ms == previous.job->updated_ms &&
           candidate.provider < previous.provider);
      if (!comes_first) break;
      candidates[position] = previous;
      position--;
    }
    candidates[position] = candidate;
  }

  uint8_t active_count = total_active(snapshot);
  for (size_t i = 0; i < candidate_count; i++) {
    append_candidate(queue, &candidates[i], active_count, now_ms);
  }
}

const tk_completion_event *tk_completion_queue_current(
    const tk_completion_queue *queue) {
  return queue && queue->count ? &queue->events[0] : NULL;
}

tk_completion_phase tk_completion_phase_at(tk_completion_queue *queue,
                                            uint64_t now_ms) {
  if (!queue || !queue->count) return TK_COMPLETION_HIDDEN;
  queue->last_now_ms = now_ms;
  uint64_t elapsed = now_ms >= queue->current_started_ms
                         ? now_ms - queue->current_started_ms : 0;
  if (elapsed < TK_COMPLETION_PULSE_MS) return TK_COMPLETION_PULSE;
  if (elapsed < TK_COMPLETION_VISIBLE_MS) return TK_COMPLETION_STATIC;
  return TK_COMPLETION_HIDDEN;
}

void tk_completion_queue_dismiss(tk_completion_queue *queue) {
  if (!queue || !queue->count) return;
  memmove(&queue->events[0], &queue->events[1],
          (size_t)(queue->count - 1) * sizeof queue->events[0]);
  queue->count--;
  queue->current_started_ms = queue->last_now_ms;
}
