#ifndef AGENT_STATUS_H
#define AGENT_STATUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TK_AGENT_ID_CAP 65
#define TK_AGENT_PROJECT_CAP 17
#define TK_AGENT_MODEL_CAP 25
#define TK_AGENT_EFFORT_CAP 13
#define TK_AGENT_PROVIDER_COUNT 2
#define TK_AGENT_JOBS_MAX 4

typedef enum {
  TK_AGENT_PROVIDER_CLAUDE = 0,
  TK_AGENT_PROVIDER_CODEX = 1,
} tk_agent_provider;

typedef enum {
  TK_AGENT_IDLE,
  TK_AGENT_WORKING,
  TK_AGENT_WAITING,
  TK_AGENT_DONE,
  TK_AGENT_ERROR,
  TK_AGENT_UNKNOWN,
} tk_agent_state;

typedef enum {
  TK_ACTIVITY_NONE,
  TK_ACTIVITY_THINKING,
  TK_ACTIVITY_READING,
  TK_ACTIVITY_EDITING,
  TK_ACTIVITY_SEARCHING,
  TK_ACTIVITY_RUNNING,
  TK_ACTIVITY_TESTING,
  TK_ACTIVITY_BUILDING,
  TK_ACTIVITY_WAITING_INPUT,
  TK_ACTIVITY_WAITING_APPROVAL,
  TK_ACTIVITY_UNKNOWN,
} tk_agent_activity;

typedef struct {
  char task_id[TK_AGENT_ID_CAP];
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
  char model[TK_AGENT_MODEL_CAP];
  char effort[TK_AGENT_EFFORT_CAP];
  bool has_model;
  bool has_effort;
  tk_agent_state state;
  tk_agent_activity activity;
  uint32_t updated_ms;
} tk_agent_status;

typedef struct {
  uint8_t active_count;
  uint8_t job_count;
  tk_agent_status jobs[TK_AGENT_JOBS_MAX];
} tk_agent_provider_status;

typedef struct {
  uint32_t seq;
  tk_agent_provider_status claude;
  tk_agent_provider_status codex;
} tk_agent_snapshot;

static inline const tk_agent_status *tk_agent_provider_primary(
    const tk_agent_provider_status *provider) {
  if (!provider || !provider->job_count) return NULL;
  const tk_agent_status *primary = &provider->jobs[0];
  for (uint8_t i = 1; i < provider->job_count; i++) {
    const tk_agent_status *candidate = &provider->jobs[i];
    int primary_priority =
        primary->state == TK_AGENT_WAITING ? 5 :
        primary->state == TK_AGENT_ERROR ? 4 :
        primary->state == TK_AGENT_WORKING ? 3 :
        primary->state == TK_AGENT_DONE ? 2 :
        primary->state == TK_AGENT_IDLE ? 1 : 0;
    int candidate_priority =
        candidate->state == TK_AGENT_WAITING ? 5 :
        candidate->state == TK_AGENT_ERROR ? 4 :
        candidate->state == TK_AGENT_WORKING ? 3 :
        candidate->state == TK_AGENT_DONE ? 2 :
        candidate->state == TK_AGENT_IDLE ? 1 : 0;
    if (candidate_priority > primary_priority ||
        (candidate_priority == primary_priority &&
         candidate->updated_ms < primary->updated_ms)) {
      primary = candidate;
    }
  }
  return primary;
}

#endif
