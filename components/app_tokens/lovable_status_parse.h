#ifndef LOVABLE_STATUS_PARSE_H
#define LOVABLE_STATUS_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#include "lovable_status.h"

bool tk_lovable_status_parse(const char *json, size_t len,
                             tk_lovable_status *out);

#endif
