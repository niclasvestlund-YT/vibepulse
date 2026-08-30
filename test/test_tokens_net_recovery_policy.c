#include <stdbool.h>
#include <stdio.h>

#include "../components/app_tokens/tokens_net_recovery_policy.h"

static int failures;

static void check(const char *name, bool condition) {
  if (!condition) {
    fprintf(stderr, "FAIL: %s\n", name);
    ++failures;
  }
}

int main(void) {
  tk_tokens_net_recovery_state state;
  tk_tokens_net_recovery_init(&state);

  check("staged deadline leaves watchdog jitter before glass stale",
        TK_TOKENS_HTTP_STALL_US + TK_TOKENS_HTTP_RESTART_GRACE_US +
            2LL * 5000000LL < 120LL * 1000000LL);

  check("cold start never recovers",
        tk_tokens_net_recovery_action_for(
            &state, TK_TOKENS_HTTP_STALL_US * 2, true, true) ==
            TK_TOKENS_NET_RECOVERY_NONE);

  tk_tokens_net_recovery_note_success(&state, 1000000);
  check("healthy feed waits",
        tk_tokens_net_recovery_action_for(
            &state, 1000000 + TK_TOKENS_HTTP_STALL_US - 1, true, true) ==
            TK_TOKENS_NET_RECOVERY_NONE);
  check("disassociated station waits",
        tk_tokens_net_recovery_action_for(
            &state, 1000000 + TK_TOKENS_HTTP_STALL_US, false, true) ==
            TK_TOKENS_NET_RECOVERY_NONE);
  check("LAN-only install waits",
        tk_tokens_net_recovery_action_for(
            &state, 1000000 + TK_TOKENS_HTTP_STALL_US, true, false) ==
            TK_TOKENS_NET_RECOVERY_NONE);
  check("associated redundant transport recycles at threshold",
        tk_tokens_net_recovery_action_for(
            &state, 1000000 + TK_TOKENS_HTTP_STALL_US, true, true) ==
            TK_TOKENS_NET_RECOVERY_RECYCLE_WIFI);

  const int64_t recovered_at = 1000000 + TK_TOKENS_HTTP_STALL_US;
  tk_tokens_net_recovery_note_recovery(&state, recovered_at);
  check("recycle gets a bounded recovery grace",
        tk_tokens_net_recovery_action_for(
            &state, recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US - 1,
            true, true) == TK_TOKENS_NET_RECOVERY_NONE);
  check("failed recycle escalates to one device restart",
        tk_tokens_net_recovery_action_for(
            &state, recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US,
            true, true) == TK_TOKENS_NET_RECOVERY_RESTART_DEVICE);
  check("restart escalation requires association",
        tk_tokens_net_recovery_action_for(
            &state, recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US,
            false, true) == TK_TOKENS_NET_RECOVERY_NONE);
  check("restart escalation requires redundant path",
        tk_tokens_net_recovery_action_for(
            &state, recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US,
            true, false) == TK_TOKENS_NET_RECOVERY_NONE);
  check("recovery-clock regression fails closed",
        tk_tokens_net_recovery_action_for(
            &state, recovered_at - 1, true, true) ==
            TK_TOKENS_NET_RECOVERY_NONE);

  tk_tokens_net_recovery_note_success(
      &state, recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US);
  check("success clears the old incident",
        state.last_recovery_us == 0);
  check("new success rearms from its own timestamp",
        tk_tokens_net_recovery_action_for(
            &state,
            recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US +
                TK_TOKENS_HTTP_STALL_US - 1,
            true, true) == TK_TOKENS_NET_RECOVERY_NONE);
  check("new success earns a new recycle",
        tk_tokens_net_recovery_action_for(
            &state,
            recovered_at + TK_TOKENS_HTTP_RESTART_GRACE_US +
                TK_TOKENS_HTTP_STALL_US,
            true, true) == TK_TOKENS_NET_RECOVERY_RECYCLE_WIFI);
  check("clock regression fails closed",
        tk_tokens_net_recovery_action_for(
            &state, state.last_success_us - 1, true, true) ==
            TK_TOKENS_NET_RECOVERY_NONE);

  if (failures == 0) {
    printf("OK: VibePulse HTTP-recoverypolicyn är bounded och fail-closed\n");
    return 0;
  }
  return 1;
}
