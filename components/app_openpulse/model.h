#ifndef OPENPULSE_MODEL_H
#define OPENPULSE_MODEL_H
#include <stdbool.h>
#include <stddef.h>
typedef struct {
    double day, week, month, budget, percent, remaining, balance, byok;
    double age, account_age;
    bool demo, account_enabled, unlimited;
    int level, key_index, key_count, byok_in_limit;
    char name[25], state[12], account_state[12], updated[32], reset[12];
} op_snapshot;
bool op_parse(const char *json, size_t length, op_snapshot *out);
#endif
