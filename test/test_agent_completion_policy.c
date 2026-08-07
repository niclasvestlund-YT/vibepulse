#include <stdio.h>
#include <string.h>

#include "../components/app_tokens/agent_completion_policy.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_agent_status job(tk_agent_state state, const char *event_id,
                           const char *project, uint32_t updated_ms) {
  tk_agent_status value = {0};
  value.state = state;
  value.updated_ms = updated_ms;
  snprintf(value.task_id, sizeof value.task_id, "task-%s", event_id);
  snprintf(value.event_id, sizeof value.event_id, "%s", event_id);
  snprintf(value.project, sizeof value.project, "%s", project);
  return value;
}

static void add_job(tk_agent_provider_status *provider,
                    tk_agent_status value) {
  provider->jobs[provider->job_count++] = value;
}

int main(void) {
  tk_agent_snapshot snapshot = {0};
  snapshot.claude.active_count = 3;
  snapshot.codex.active_count = 2;
  add_job(&snapshot.claude,
          job(TK_AGENT_DONE, "torget-done", "Torget", 500));
  add_job(&snapshot.codex,
          job(TK_AGENT_DONE, "buddy-done", "Buddy", 1000));

  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 1000);
  const tk_completion_event *current =
      tk_completion_queue_current(&queue);
  check("äldsta done visas först",
        current && strcmp(current->project, "Buddy") == 0);
  check("alla andra aktiva jobb räknas",
        current && current->other_active_count == 5);
  check("pulserar före pipgränsen",
        tk_completion_phase_at(&queue, 3399) == TK_COMPLETION_PULSE);
  check("statisk efter 2,4 sekunder",
        tk_completion_phase_at(&queue, 3400) == TK_COMPLETION_STATIC);
  check("autoåtergår efter tio sekunder",
        tk_completion_phase_at(&queue, 11000) == TK_COMPLETION_HIDDEN);

  tk_completion_queue_dismiss(&queue);
  current = tk_completion_queue_current(&queue);
  check("tryck går till nästa köade event",
        current && strcmp(current->project, "Torget") == 0);
  tk_completion_queue_dismiss(&queue);
  check("tom kö är dold",
        tk_completion_phase_at(&queue, 11001) == TK_COMPLETION_HIDDEN);

  tk_completion_queue_apply(&queue, &snapshot, 12000);
  check("samma event-id köas aldrig igen",
        tk_completion_queue_current(&queue) == NULL);

  tk_completion_queue first_snapshot = {0};
  tk_agent_snapshot old = {0};
  add_job(&old.claude,
          job(TK_AGENT_DONE, "old-done", "Old", 15001));
  tk_completion_queue_apply(&first_snapshot, &old, 50000);
  check("första snapshoten ignorerar gamla done",
        tk_completion_queue_current(&first_snapshot) == NULL);
  tk_completion_queue_apply(&first_snapshot, &old, 51000);
  check("ignorerat gammalt event återkommer inte",
        tk_completion_queue_current(&first_snapshot) == NULL);

  tk_agent_provider_status priority = {0};
  add_job(&priority, job(TK_AGENT_WORKING, "work", "Work", 1));
  add_job(&priority, job(TK_AGENT_ERROR, "error", "Error", 100));
  add_job(&priority, job(TK_AGENT_WAITING, "wait", "Wait", 200));
  const tk_agent_status *primary = tk_agent_provider_primary(&priority);
  check("waiting och error går före working i rail",
        primary && primary->state == TK_AGENT_WAITING);

  tk_completion_queue overflow = {0};
  tk_agent_snapshot eight = {0};
  eight.claude.active_count = 9;
  for (int i = 0; i < TK_AGENT_JOBS_MAX; i++) {
    char event_id[16], project[16];
    snprintf(event_id, sizeof event_id, "claude-%d", i);
    snprintf(project, sizeof project, "C%d", i);
    add_job(&eight.claude,
            job(TK_AGENT_DONE, event_id, project, 800 - i));
    snprintf(event_id, sizeof event_id, "codex-%d", i);
    snprintf(project, sizeof project, "X%d", i);
    add_job(&eight.codex,
            job(TK_AGENT_DONE, event_id, project, 400 - i));
  }
  tk_completion_queue_apply(&overflow, &eight, 1000);
  const tk_completion_event *displayed =
      tk_completion_queue_current(&overflow);
  char displayed_id[TK_AGENT_ID_CAP];
  snprintf(displayed_id, sizeof displayed_id, "%s", displayed->event_id);

  tk_agent_snapshot replacement = {0};
  add_job(&replacement.claude,
          job(TK_AGENT_DONE, "ninth", "Ninth", 0));
  tk_completion_queue_apply(&overflow, &replacement, 1100);
  check("queuekapaciteten är hårt begränsad",
        overflow.count == TK_COMPLETION_QUEUE_CAP);
  check("overflow tar aldrig bort det visade eventet",
        strcmp(tk_completion_queue_current(&overflow)->event_id,
               displayed_id) == 0);

  if (failures == 0) {
    printf("OK: alla completion-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
