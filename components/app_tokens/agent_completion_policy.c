#include "agent_completion_policy.h"

#include <stdio.h>
#include <string.h>

_Static_assert(TK_COMPLETION_QUEUE_CAP >=
                   TK_AGENT_PROVIDER_COUNT * TK_AGENT_JOBS_MAX,
               "completion queue must fit one full agent snapshot");

typedef struct {
  const tk_agent_status *job;
  int provider;
  uint8_t job_order;
} completion_candidate;

static bool is_attention_state(tk_agent_state state) {
  return state == TK_AGENT_WAITING || state == TK_AGENT_ERROR ||
         state == TK_AGENT_DONE;
}

static int state_priority(tk_agent_state state) {
  if (state == TK_AGENT_WAITING) return 3;
  if (state == TK_AGENT_ERROR) return 2;
  if (state == TK_AGENT_DONE) return 1;
  return 0;
}

static bool same_event(int provider, const char *event_id,
                       int other_provider, const char *other_event_id) {
  return provider == other_provider && strcmp(event_id, other_event_id) == 0;
}

static bool seen(const tk_completion_queue *queue, int provider,
                 const char *event_id) {
  for (uint8_t i = 0; i < queue->seen_count; i++) {
    if (same_event(provider, event_id, queue->seen_providers[i],
                   queue->seen_ids[i])) return true;
  }
  return false;
}

static uint8_t bounded_job_count(const tk_agent_provider_status *provider) {
  return provider->job_count > TK_AGENT_JOBS_MAX
             ? TK_AGENT_JOBS_MAX : provider->job_count;
}

static bool snapshot_contains_attention(const tk_agent_snapshot *snapshot,
                                        int provider, const char *event_id) {
  const tk_agent_provider_status *status =
      provider == TK_AGENT_PROVIDER_CLAUDE ? &snapshot->claude :
      provider == TK_AGENT_PROVIDER_CODEX ? &snapshot->codex : NULL;
  if (!status) return false;
  for (uint8_t i = 0; i < bounded_job_count(status); i++) {
    const tk_agent_status *job = &status->jobs[i];
    if (job->event_id[0] && is_attention_state(job->state) &&
        same_event(provider, event_id, provider, job->event_id)) {
      return true;
    }
  }
  return false;
}

static bool remember(tk_completion_queue *queue,
                     const tk_agent_snapshot *snapshot, int provider,
                     const char *event_id) {
  if (!event_id[0] || seen(queue, provider, event_id)) return true;
  uint8_t slot;
  if (queue->seen_count < TK_COMPLETION_SEEN_CAP) {
    slot = queue->seen_count++;
  } else {
    bool found = false;
    for (uint8_t offset = 0; offset < TK_COMPLETION_SEEN_CAP; offset++) {
      uint8_t candidate =
          (uint8_t)((queue->seen_next + offset) % TK_COMPLETION_SEEN_CAP);
      if (!snapshot_contains_attention(snapshot, queue->seen_providers[candidate],
                                       queue->seen_ids[candidate])) {
        slot = candidate;
        found = true;
        break;
      }
    }
    if (!found) return false;
    queue->seen_next =
        (uint8_t)((slot + 1U) % TK_COMPLETION_SEEN_CAP);
  }
  queue->seen_providers[slot] = provider;
  snprintf(queue->seen_ids[slot], sizeof queue->seen_ids[slot], "%s",
           event_id);
  return true;
}

static uint8_t total_active(const tk_agent_snapshot *snapshot) {
  uint16_t total = (uint16_t)snapshot->claude.active_count +
                   (uint16_t)snapshot->codex.active_count;
  return total > UINT8_MAX ? UINT8_MAX : (uint8_t)total;
}

static uint8_t same_state_count(const tk_agent_snapshot *snapshot,
                                tk_agent_state state) {
  uint16_t count = 0;
  const tk_agent_provider_status *providers[TK_AGENT_PROVIDER_COUNT] = {
      &snapshot->claude,
      &snapshot->codex,
  };
  for (int provider = 0; provider < TK_AGENT_PROVIDER_COUNT; provider++) {
    for (uint8_t i = 0; i < bounded_job_count(providers[provider]); i++) {
      const tk_agent_status *job = &providers[provider]->jobs[i];
      if (job->event_id[0] && job->state == state) count++;
    }
  }
  return count > UINT8_MAX ? UINT8_MAX : (uint8_t)count;
}

static bool candidate_precedes(const completion_candidate *left,
                               const completion_candidate *right) {
  int left_priority = state_priority(left->job->state);
  int right_priority = state_priority(right->job->state);
  if (left_priority != right_priority) return left_priority > right_priority;
  if (left->job->updated_ms != right->job->updated_ms) {
    return left->job->updated_ms < right->job->updated_ms;
  }
  if (left->provider != right->provider) return left->provider < right->provider;
  int event_id_order = strcmp(left->job->event_id, right->job->event_id);
  if (event_id_order != 0) return event_id_order < 0;
  return left->job_order < right->job_order;
}

static bool queue_contains(const tk_completion_queue *queue, int provider,
                           const char *event_id) {
  for (uint8_t i = 0; i < queue->count; i++) {
    if (same_event(provider, event_id, queue->events[i].provider,
                   queue->events[i].event_id)) return true;
  }
  return false;
}

static bool candidate_contains(const completion_candidate *candidates,
                               size_t count, int provider,
                               const char *event_id) {
  for (size_t i = 0; i < count; i++) {
    if (same_event(provider, event_id, candidates[i].provider,
                   candidates[i].job->event_id)) return true;
  }
  return false;
}

static void copy_event(tk_completion_event *event,
                       const completion_candidate *candidate,
                       uint8_t active_count, uint8_t state_count) {
  memset(event, 0, sizeof *event);
  event->provider = candidate->provider;
  event->state = candidate->job->state;
  event->same_state_count = state_count;
  event->other_active_count = active_count;
  event->updated_ms = candidate->job->updated_ms;
  snprintf(event->event_id, sizeof event->event_id, "%s",
           candidate->job->event_id);
  snprintf(event->project, sizeof event->project, "%s",
           candidate->job->project);
}

void tk_completion_queue_apply(tk_completion_queue *queue,
                               const tk_agent_snapshot *snapshot,
                               uint64_t now_ms) {
  if (!queue || !snapshot) return;
  bool first_snapshot = !queue->initialized;
  char previous_id[TK_AGENT_ID_CAP] = {0};
  int previous_provider = -1;
  tk_agent_state previous_state = TK_AGENT_UNKNOWN;
  if (queue->count) {
    previous_provider = queue->events[0].provider;
    previous_state = queue->events[0].state;
    snprintf(previous_id, sizeof previous_id, "%s", queue->events[0].event_id);
  }
  queue->initialized = true;
  if (now_ms >= queue->last_now_ms) queue->last_now_ms = now_ms;

  completion_candidate candidates[TK_AGENT_PROVIDER_COUNT *
                                  TK_AGENT_JOBS_MAX];
  size_t candidate_count = 0;
  const tk_agent_provider_status *providers[TK_AGENT_PROVIDER_COUNT] = {
      &snapshot->claude,
      &snapshot->codex,
  };
  for (int provider = 0; provider < TK_AGENT_PROVIDER_COUNT; provider++) {
    for (uint8_t i = 0; i < bounded_job_count(providers[provider]); i++) {
      const tk_agent_status *job = &providers[provider]->jobs[i];
      if (!job->event_id[0] || !is_attention_state(job->state) ||
          candidate_contains(candidates, candidate_count, provider,
                             job->event_id)) {
        continue;
      }
      bool already_queued = queue_contains(queue, provider, job->event_id);
      if (!already_queued && seen(queue, provider, job->event_id)) continue;
      if (!already_queued &&
          !remember(queue, snapshot, provider, job->event_id)) {
        continue;
      }
      if (!already_queued && first_snapshot &&
          job->updated_ms > TK_COMPLETION_INITIAL_MAX_AGE_MS) {
        continue;
      }
      candidates[candidate_count++] = (completion_candidate){
          .job = job,
          .provider = provider,
          .job_order = i,
      };
    }
  }

  for (size_t i = 1; i < candidate_count; i++) {
    completion_candidate candidate = candidates[i];
    size_t position = i;
    while (position > 0 && candidate_precedes(&candidate,
                                               &candidates[position - 1])) {
      candidates[position] = candidates[position - 1];
      position--;
    }
    candidates[position] = candidate;
  }

  /* Each fresh snapshot is the source of truth.  Sorting the reconciled list
   * intentionally lets a newly arrived WAITING or ERROR event supersede a
   * visible DONE event; the displaced event remains queued when still present.
   */
  tk_completion_event reconciled[TK_COMPLETION_QUEUE_CAP] = {0};
  uint8_t active_count = total_active(snapshot);
  size_t kept = candidate_count < TK_COMPLETION_QUEUE_CAP
                    ? candidate_count : TK_COMPLETION_QUEUE_CAP;
  for (size_t i = 0; i < kept; i++) {
    copy_event(&reconciled[i], &candidates[i], active_count,
               same_state_count(snapshot, candidates[i].job->state));
  }
  memcpy(queue->events, reconciled, sizeof reconciled);
  queue->count = (uint8_t)kept;

  if (queue->count &&
      (!same_event(previous_provider, previous_id, queue->events[0].provider,
                   queue->events[0].event_id) ||
       previous_state != queue->events[0].state)) {
    queue->current_started_ms = queue->last_now_ms;
  }
}

const tk_completion_event *tk_completion_queue_current(
    const tk_completion_queue *queue) {
  return queue && queue->count ? &queue->events[0] : NULL;
}

tk_completion_phase tk_completion_phase_at(tk_completion_queue *queue,
                                            uint64_t now_ms) {
  if (!queue || !queue->count) return TK_COMPLETION_HIDDEN;
  uint64_t effective_now = now_ms >= queue->last_now_ms
                               ? now_ms : queue->last_now_ms;
  queue->last_now_ms = effective_now;
  uint64_t elapsed = effective_now >= queue->current_started_ms
                         ? effective_now - queue->current_started_ms : 0;
  if (elapsed < TK_COMPLETION_PULSE_MS) return TK_COMPLETION_PULSE;
  if (queue->events[0].state != TK_AGENT_DONE ||
      elapsed < TK_COMPLETION_VISIBLE_MS) {
    return TK_COMPLETION_STATIC;
  }
  tk_completion_queue_dismiss(queue);
  return queue->count ? TK_COMPLETION_PULSE : TK_COMPLETION_HIDDEN;
}

void tk_completion_queue_dismiss(tk_completion_queue *queue) {
  if (!queue || !queue->count) return;
  memmove(&queue->events[0], &queue->events[1],
          (size_t)(queue->count - 1) * sizeof queue->events[0]);
  queue->count--;
  queue->current_started_ms = queue->last_now_ms;
}

bool tk_completion_render_key_update(tk_completion_render_key *rendered,
                                     const tk_completion_event *event,
                                     bool visible) {
  if (!rendered) return false;
  visible = visible && event;
  bool unchanged = rendered->initialized && rendered->visible == visible;
  if (unchanged && visible) {
    unchanged = rendered->provider == event->provider &&
                rendered->state == event->state &&
                rendered->same_state_count == event->same_state_count &&
                strcmp(rendered->event_id, event->event_id) == 0 &&
                strcmp(rendered->project, event->project) == 0;
  }
  if (unchanged) return false;

  memset(rendered, 0, sizeof *rendered);
  rendered->initialized = true;
  rendered->visible = visible;
  if (visible) {
    rendered->provider = event->provider;
    rendered->state = event->state;
    rendered->same_state_count = event->same_state_count;
    snprintf(rendered->event_id, sizeof rendered->event_id, "%s",
             event->event_id);
    snprintf(rendered->project, sizeof rendered->project, "%s",
             event->project);
  }
  return true;
}
