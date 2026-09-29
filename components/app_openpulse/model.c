#include "model.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
static double numeric(cJSON *root, const char *key) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsNumber(v) && isfinite(v->valuedouble) && fabs(v->valuedouble) <= 1e12 ? v->valuedouble : NAN;
}
static const char *string(cJSON *root, const char *key) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(v) ? v->valuestring : "";
}
static bool flag(cJSON *root, const char *key) {
    return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, key));
}
static void safe_text(char *out, size_t cap, const char *source) {
    size_t i = 0;
    while (source[i] && i + 1 < cap) {
        unsigned char ch = (unsigned char)source[i];
        out[i] = ch >= 32 && ch <= 126 ? (char)ch : '?';
        i++;
    }
    out[i] = 0;
}
static bool valid_state(const char *value) {
    return !strcmp(value,"fresh") || !strcmp(value,"stale") || !strcmp(value,"error") || !strcmp(value,"no_data");
}
bool op_parse(const char *json, size_t length, op_snapshot *out) {
    if (!json || !out || !length || length > 4096) return false;
    cJSON *root = cJSON_ParseWithLength(json, length);
    if (!root) return false;
    bool valid = numeric(root,"schema") == 1 && !strcmp(string(root,"currency"),"USD") &&
        valid_state(string(root,"state")) &&
        (valid_state(string(root,"accountState")) || !strcmp(string(root,"accountState"),"disabled"));
    double index = numeric(root,"keyIndex"), count = numeric(root,"keyCount");
    valid = valid && isfinite(index) && isfinite(count) && index == floor(index) && count == floor(count) &&
        index >= 0 && count >= 1 && count <= 8 && index < count;
    if (valid) {
        op_snapshot v = {0};
        v.day=numeric(root,"day"); v.week=numeric(root,"week"); v.month=numeric(root,"month");
        v.budget=numeric(root,"budget"); v.percent=numeric(root,"budgetPercent");
        v.remaining=numeric(root,"limitRemaining"); v.balance=numeric(root,"accountBalance");
        v.byok=numeric(root,"byokMonth"); v.age=numeric(root,"ageSeconds");
        v.account_age=numeric(root,"accountAgeSeconds");
        v.demo=flag(root,"demo"); v.account_enabled=flag(root,"accountEnabled");
        v.unlimited=!strcmp(string(root,"limitState"),"unlimited");
        v.level=!strcmp(string(root,"budgetLevel"),"critical") ? 2 :
            !strcmp(string(root,"budgetLevel"),"warning") ? 1 : 0;
        cJSON *includes=cJSON_GetObjectItemCaseSensitive(root,"limitIncludesByok");
        v.byok_in_limit=cJSON_IsBool(includes) ? (cJSON_IsTrue(includes) ? 1 : 0) : -1;
        v.key_index=(int)index; v.key_count=(int)count;
        safe_text(v.name,sizeof v.name,string(root,"name"));
        safe_text(v.state,sizeof v.state,string(root,"state"));
        safe_text(v.account_state,sizeof v.account_state,string(root,"accountState"));
        safe_text(v.updated,sizeof v.updated,string(root,"updatedAt"));
        safe_text(v.reset,sizeof v.reset,string(root,"limitReset"));
        *out=v;
    }
    cJSON_Delete(root);
    return valid;
}
