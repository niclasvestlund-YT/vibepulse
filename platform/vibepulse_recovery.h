#pragma once

#include <stdbool.h>

/* True only during a boot caused by the guarded HTTP-stall escalation. The
 * local HTTP client reports this content-free marker to tokenserver so a
 * wall-powered recovery remains diagnosable without a USB serial cable. */
bool torget_net_http_stall_recovery_booted(void);
