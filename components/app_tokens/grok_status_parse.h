#ifndef GROK_STATUS_PARSE_H
#define GROK_STATUS_PARSE_H
#include <stddef.h>
#include "grok_status.h"
bool tk_grok_status_parse(const char *json, size_t len, tk_grok_status *out);
#endif
