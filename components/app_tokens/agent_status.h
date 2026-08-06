#ifndef AGENT_STATUS_H
#define AGENT_STATUS_H

#include <stdint.h>

#define TK_AGENT_ID_CAP 65
#define TK_AGENT_PROJECT_CAP 17

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
  tk_agent_state state;
  tk_agent_activity activity;
  uint32_t updated_ms;
} tk_agent_status;

typedef struct {
  uint32_t seq;
  tk_agent_status claude;
  tk_agent_status codex;
} tk_agent_snapshot;

#endif
