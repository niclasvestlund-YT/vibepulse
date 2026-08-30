#include "tokens_net_recovery_policy.h"

#include <string.h>

void tk_tokens_net_recovery_init(tk_tokens_net_recovery_state *state) {
  if (state) memset(state, 0, sizeof *state);
}

void tk_tokens_net_recovery_note_success(
    tk_tokens_net_recovery_state *state, int64_t now_us) {
  if (!state || now_us <= 0) return;
  state->has_success = true;
  state->last_success_us = now_us;
  /* A recovered feed starts a new incident. A later stall gets its own Wi-Fi
   * recycle instead of inheriting the previous incident's recovery state. */
  state->last_recovery_us = 0;
}

void tk_tokens_net_recovery_note_recovery(
    tk_tokens_net_recovery_state *state, int64_t now_us) {
  if (!state || now_us <= 0) return;
  state->last_recovery_us = now_us;
}

tk_tokens_net_recovery_action tk_tokens_net_recovery_action_for(
    const tk_tokens_net_recovery_state *state, int64_t now_us,
    bool wifi_associated, bool redundant_path_configured) {
  if (!state || !state->has_success || now_us <= 0 ||
      !wifi_associated || !redundant_path_configured ||
      state->last_success_us <= 0 || now_us < state->last_success_us) {
    return TK_TOKENS_NET_RECOVERY_NONE;
  }
  if (now_us - state->last_success_us < TK_TOKENS_HTTP_STALL_US) {
    return TK_TOKENS_NET_RECOVERY_NONE;
  }
  if (state->last_recovery_us <= 0 ||
      state->last_recovery_us < state->last_success_us) {
    return TK_TOKENS_NET_RECOVERY_RECYCLE_WIFI;
  }
  if (now_us < state->last_recovery_us ||
      now_us - state->last_recovery_us <
          TK_TOKENS_HTTP_RESTART_GRACE_US) {
    return TK_TOKENS_NET_RECOVERY_NONE;
  }
  return TK_TOKENS_NET_RECOVERY_RESTART_DEVICE;
}
