#ifndef TOKENS_NET_RECOVERY_POLICY_H
#define TOKENS_NET_RECOVERY_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* The UI declares quota data stale after 120 seconds. Intervene after 60
 * seconds so a recycled station gets one bounded retry before the
 * glass crosses that boundary. */
#define TK_TOKENS_HTTP_STALL_US (60LL * 1000000LL)
/* A disconnect is not guaranteed to unwind a wedged TLS/client task. If no
 * real success follows the recycle, restart the process state as a second and
 * final recovery level. Cold boot is fail-closed until a new success, so a
 * real upstream outage cannot become a reboot loop. */
#define TK_TOKENS_HTTP_RESTART_GRACE_US (45LL * 1000000LL)

typedef enum {
  TK_TOKENS_NET_RECOVERY_NONE = 0,
  TK_TOKENS_NET_RECOVERY_RECYCLE_WIFI,
  TK_TOKENS_NET_RECOVERY_RESTART_DEVICE,
} tk_tokens_net_recovery_action;

typedef struct {
  bool has_success;
  int64_t last_success_us;
  int64_t last_recovery_us;
} tk_tokens_net_recovery_state;

void tk_tokens_net_recovery_init(tk_tokens_net_recovery_state *state);
void tk_tokens_net_recovery_note_success(
    tk_tokens_net_recovery_state *state, int64_t now_us);
void tk_tokens_net_recovery_note_recovery(
    tk_tokens_net_recovery_state *state, int64_t now_us);

/* Recovery is deliberately conservative: a known-good feed must first have
 * gone quiet, the station must still claim to be associated, and the build
 * must have a redundant numbers relay. A LAN-only installation may
 * legitimately have a sleeping computer and is never recycled for that. */
tk_tokens_net_recovery_action tk_tokens_net_recovery_action_for(
    const tk_tokens_net_recovery_state *state, int64_t now_us,
    bool wifi_associated, bool redundant_path_configured);

#endif
