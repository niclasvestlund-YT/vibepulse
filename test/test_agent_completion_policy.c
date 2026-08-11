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
                           const char *project, uint32_t age_ms) {
  tk_agent_status value = {0};
  value.state = state;
  value.updated_ms = age_ms;
  snprintf(value.task_id, sizeof value.task_id, "task-%s", event_id);
  snprintf(value.event_id, sizeof value.event_id, "%s", event_id);
  snprintf(value.project, sizeof value.project, "%s", project);
  return value;
}

static void add_job(tk_agent_provider_status *provider,
                    tk_agent_status value) {
  provider->jobs[provider->job_count++] = value;
}

static const tk_completion_event *current(tk_completion_queue *queue) {
  return tk_completion_queue_current(queue);
}

static void test_priority_order(void) {
  tk_agent_snapshot snapshot = {0};
  add_job(&snapshot.claude, job(TK_AGENT_DONE, "done", "Done", 1));
  add_job(&snapshot.codex, job(TK_AGENT_ERROR, "error", "Error", 1));
  add_job(&snapshot.claude, job(TK_AGENT_WAITING, "wait", "Wait", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 100);
  check("waiting ranks before error and done",
        current(&queue) && strcmp(current(&queue)->event_id, "wait") == 0);
  tk_completion_queue_dismiss(&queue);
  check("error ranks before done", current(&queue) &&
        strcmp(current(&queue)->event_id, "error") == 0);
  tk_completion_queue_dismiss(&queue);
  check("done follows attention failures", current(&queue) &&
        strcmp(current(&queue)->event_id, "done") == 0);
}

static void test_freshness_and_provider_tie_break(void) {
  tk_agent_snapshot fresh = {0};
  add_job(&fresh.claude, job(TK_AGENT_WAITING, "older", "Older", 100));
  add_job(&fresh.codex, job(TK_AGENT_WAITING, "fresh", "Fresh", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &fresh, 100);
  check("fresher same-state event wins", current(&queue) &&
        strcmp(current(&queue)->event_id, "fresh") == 0);

  tk_agent_snapshot tied = {0};
  add_job(&tied.codex, job(TK_AGENT_WAITING, "codex", "Codex", 1));
  add_job(&tied.claude, job(TK_AGENT_WAITING, "claude", "Claude", 1));
  tk_completion_queue tied_queue = {0};
  tk_completion_queue_apply(&tied_queue, &tied, 100);
  check("claude resolves equal freshness tie", current(&tied_queue) &&
        strcmp(current(&tied_queue)->event_id, "claude") == 0);

  tk_agent_snapshot forward = {0};
  add_job(&forward.claude, job(TK_AGENT_WAITING, "zeta", "Zeta", 1));
  add_job(&forward.claude, job(TK_AGENT_WAITING, "alpha", "Alpha", 1));
  tk_agent_snapshot reversed = {0};
  add_job(&reversed.claude, job(TK_AGENT_WAITING, "alpha", "Alpha", 1));
  add_job(&reversed.claude, job(TK_AGENT_WAITING, "zeta", "Zeta", 1));
  tk_completion_queue forward_queue = {0};
  tk_completion_queue reversed_queue = {0};
  tk_completion_queue_apply(&forward_queue, &forward, 100);
  tk_completion_queue_apply(&reversed_queue, &reversed, 100);
  check("same-provider ties ignore snapshot order", current(&forward_queue) &&
        current(&reversed_queue) &&
        strcmp(current(&forward_queue)->event_id, "alpha") == 0 &&
        strcmp(current(&forward_queue)->event_id,
               current(&reversed_queue)->event_id) == 0);
}

static void test_non_attention_and_stale_events(void) {
  tk_agent_snapshot working = {0};
  add_job(&working.claude, job(TK_AGENT_WORKING, "working", "Work", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &working, 100);
  check("working events never enter queue", current(&queue) == NULL);

  tk_agent_snapshot old = {0};
  add_job(&old.claude, job(TK_AGENT_WAITING, "old", "Old", 15001));
  tk_completion_queue stale_queue = {0};
  tk_completion_queue_apply(&stale_queue, &old, 100);
  check("old first-snapshot event is ignored", current(&stale_queue) == NULL);
  old.claude.jobs[0].updated_ms = 1;
  tk_completion_queue_apply(&stale_queue, &old, 101);
  check("ignored first-snapshot event is remembered", current(&stale_queue) == NULL);
}

static void test_independent_waiting_and_counts(void) {
  tk_agent_snapshot snapshot = {0};
  add_job(&snapshot.claude, job(TK_AGENT_WAITING, "one", "One", 1));
  add_job(&snapshot.codex, job(TK_AGENT_WAITING, "two", "Two", 2));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 100);
  check("same-state count spans both providers", current(&queue) &&
        current(&queue)->same_state_count == 2);
  check("first waiting event is displayed", current(&queue) &&
        strcmp(current(&queue)->event_id, "one") == 0);
  tk_completion_queue_dismiss(&queue);
  check("dismissing one waiting event reveals next", current(&queue) &&
        strcmp(current(&queue)->event_id, "two") == 0);
}

static void test_reconciliation_and_transitions(void) {
  tk_agent_snapshot snapshot = {0};
  add_job(&snapshot.claude, job(TK_AGENT_WAITING, "moving", "Before", 3));
  add_job(&snapshot.codex, job(TK_AGENT_DONE, "fallback", "Fallback", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 100);

  tk_agent_snapshot changed = {0};
  add_job(&changed.claude, job(TK_AGENT_ERROR, "moving", "After", 1));
  add_job(&changed.codex, job(TK_AGENT_DONE, "fallback", "Fallback", 1));
  tk_completion_queue_apply(&queue, &changed, 101);
  check("same event id updates its state", current(&queue) &&
        current(&queue)->state == TK_AGENT_ERROR);
  check("same event id updates its project", current(&queue) &&
        strcmp(current(&queue)->project, "After") == 0);

  tk_agent_snapshot resumed = {0};
  add_job(&resumed.claude, job(TK_AGENT_WORKING, "moving", "After", 1));
  add_job(&resumed.codex, job(TK_AGENT_DONE, "fallback", "Fallback", 1));
  tk_completion_queue_apply(&queue, &resumed, 102);
  check("resumed working current is invalidated", current(&queue) &&
        strcmp(current(&queue)->event_id, "fallback") == 0);
  tk_completion_queue_apply(&queue, &resumed, 103);
  check("identical repoll does not duplicate", queue.count == 1);

  tk_completion_queue_apply(&queue, &(tk_agent_snapshot){0}, 104);
  check("gone current is invalidated", current(&queue) == NULL);
}

static void test_state_transition_restarts_entry_phase(void) {
  tk_agent_snapshot waiting = {0};
  add_job(&waiting.claude,
          job(TK_AGENT_WAITING, "same-id", "Waiting", 1));
  tk_completion_queue error_queue = {0};
  tk_completion_queue_apply(&error_queue, &waiting, 100);
  check("old waiting event becomes static",
        tk_completion_phase_at(&error_queue, 10100) == TK_COMPLETION_STATIC);

  tk_agent_snapshot error = {0};
  add_job(&error.claude, job(TK_AGENT_ERROR, "same-id", "Error", 1));
  tk_completion_queue_apply(&error_queue, &error, 10101);
  check("waiting-to-error transition starts a fresh pulse",
        tk_completion_phase_at(&error_queue, 10101) == TK_COMPLETION_PULSE);
  check("error transition pulse lasts through 4799ms",
        tk_completion_phase_at(&error_queue, 14900) == TK_COMPLETION_PULSE);
  check("error transition becomes static at 4800ms",
        tk_completion_phase_at(&error_queue, 14901) == TK_COMPLETION_STATIC);

  tk_completion_queue done_queue = {0};
  tk_completion_queue_apply(&done_queue, &waiting, 100);
  check("second old waiting event becomes static",
        tk_completion_phase_at(&done_queue, 10100) == TK_COMPLETION_STATIC);
  tk_agent_snapshot done = {0};
  add_job(&done.claude, job(TK_AGENT_DONE, "same-id", "Done", 1));
  tk_completion_queue_apply(&done_queue, &done, 10101);
  check("waiting-to-done transition starts a fresh pulse",
        tk_completion_phase_at(&done_queue, 10101) == TK_COMPLETION_PULSE);
  check("transitioned done becomes static after fresh pulse",
        tk_completion_phase_at(&done_queue, 14901) == TK_COMPLETION_STATIC);
  check("transitioned done hides from its new start",
        tk_completion_phase_at(&done_queue, 20101) == TK_COMPLETION_HIDDEN);
}

static void test_phases(void) {
  tk_agent_snapshot waiting = {0};
  add_job(&waiting.claude, job(TK_AGENT_WAITING, "wait", "Wait", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &waiting, 100);
  check("pulse lasts through 4799ms", tk_completion_phase_at(&queue, 4899) ==
        TK_COMPLETION_PULSE);
  check("pulse becomes static at 4800ms", tk_completion_phase_at(&queue, 4900) ==
        TK_COMPLETION_STATIC);
  check("waiting remains static after ten seconds",
        tk_completion_phase_at(&queue, 10100) == TK_COMPLETION_STATIC);
  check("waiting stays static when time moves backward",
        tk_completion_phase_at(&queue, 200) == TK_COMPLETION_STATIC);

  tk_agent_snapshot error = {0};
  add_job(&error.claude, job(TK_AGENT_ERROR, "error", "Error", 1));
  tk_completion_queue error_queue = {0};
  tk_completion_queue_apply(&error_queue, &error, 100);
  check("error remains static after ten seconds",
        tk_completion_phase_at(&error_queue, 10100) == TK_COMPLETION_STATIC);
  check("error stays static when time moves backward",
        tk_completion_phase_at(&error_queue, 200) == TK_COMPLETION_STATIC);

  tk_agent_snapshot done = {0};
  add_job(&done.claude, job(TK_AGENT_DONE, "done", "Done", 1));
  tk_completion_queue done_queue = {0};
  tk_completion_queue_apply(&done_queue, &done, 100);
  check("done has bounded visibility", tk_completion_phase_at(&done_queue, 10100) ==
        TK_COMPLETION_HIDDEN);
  check("done stays hidden when time moves backward",
        tk_completion_phase_at(&done_queue, 200) == TK_COMPLETION_HIDDEN);
}

static void test_seen_rollover_keeps_dismissed_current_snapshot_event(void) {
  tk_agent_snapshot target = {0};
  add_job(&target.claude, job(TK_AGENT_WAITING, "dismissed", "Dismissed", 1));
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &target, 100);
  tk_completion_queue_dismiss(&queue);

  for (int i = 0; i < TK_COMPLETION_SEEN_CAP; i++) {
    tk_agent_snapshot snapshot = target;
    char event_id[TK_AGENT_ID_CAP];
    snprintf(event_id, sizeof event_id, "new-%d", i);
    add_job(&snapshot.codex, job(TK_AGENT_DONE, event_id, "New", 1));
    tk_completion_queue_apply(&queue, &snapshot, (uint64_t)(101 + i));
    tk_completion_queue_dismiss(&queue);
  }
  tk_completion_queue_apply(&queue, &target, 200);
  check("seen rollover never re-admits dismissed current-snapshot event",
        current(&queue) == NULL);
}

static void test_hostile_job_count_is_clamped(void) {
  tk_agent_snapshot snapshot = {0};
  add_job(&snapshot.claude, job(TK_AGENT_WAITING, "first", "First", 1));
  snapshot.claude.job_count = UINT8_MAX;
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 100);
  check("hostile job count only reads declared job storage", current(&queue) &&
        queue.count == 1 && strcmp(current(&queue)->event_id, "first") == 0);
}

static void test_full_snapshot_fits_queue_capacity(void) {
  check("queue capacity fits every job in one snapshot",
        TK_COMPLETION_QUEUE_CAP >=
            TK_AGENT_PROVIDER_COUNT * TK_AGENT_JOBS_MAX);

  tk_agent_snapshot snapshot = {0};
  for (int i = 0; i < TK_AGENT_JOBS_MAX; i++) {
    char event_id[TK_AGENT_ID_CAP];
    snprintf(event_id, sizeof event_id, "claude-%d", i);
    add_job(&snapshot.claude,
            job(TK_AGENT_WAITING, event_id, "Claude", 1));
    snprintf(event_id, sizeof event_id, "codex-%d", i);
    add_job(&snapshot.codex,
            job(TK_AGENT_WAITING, event_id, "Codex", 1));
  }
  tk_completion_queue queue = {0};
  tk_completion_queue_apply(&queue, &snapshot, 100);
  check("full valid snapshot fills queue exactly",
        queue.count == TK_AGENT_PROVIDER_COUNT * TK_AGENT_JOBS_MAX);
  check("full valid snapshot keeps deterministic current", current(&queue) &&
        strcmp(current(&queue)->event_id, "claude-0") == 0);
}

int main(void) {
  test_priority_order();
  test_freshness_and_provider_tie_break();
  test_non_attention_and_stale_events();
  test_independent_waiting_and_counts();
  test_reconciliation_and_transitions();
  test_state_transition_restarts_entry_phase();
  test_phases();
  test_seen_rollover_keeps_dismissed_current_snapshot_event();
  test_hostile_job_count_is_clamped();
  test_full_snapshot_fits_queue_capacity();

  if (failures == 0) {
    printf("OK: all completion-policy tests pass\n");
    return 0;
  }
  printf("%d tests failed\n", failures);
  return 1;
}
