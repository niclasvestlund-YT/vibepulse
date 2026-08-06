#include "agent_status_parse.h"

#include <stdint.h>
#include <string.h>

#include "../../third_party/cjson/cJSON.h"

typedef struct {
  const char *name;
  tk_agent_state value;
} state_entry;

typedef struct {
  const char *name;
  tk_agent_activity value;
} activity_entry;

static const state_entry state_entries[] = {
    {"idle", TK_AGENT_IDLE},
    {"working", TK_AGENT_WORKING},
    {"waiting", TK_AGENT_WAITING},
    {"done", TK_AGENT_DONE},
    {"error", TK_AGENT_ERROR},
    {"unknown", TK_AGENT_UNKNOWN},
};

static const activity_entry activity_entries[] = {
    {"thinking", TK_ACTIVITY_THINKING},
    {"reading", TK_ACTIVITY_READING},
    {"editing", TK_ACTIVITY_EDITING},
    {"searching", TK_ACTIVITY_SEARCHING},
    {"running", TK_ACTIVITY_RUNNING},
    {"testing", TK_ACTIVITY_TESTING},
    {"building", TK_ACTIVITY_BUILDING},
    {"waiting_input", TK_ACTIVITY_WAITING_INPUT},
    {"waiting_approval", TK_ACTIVITY_WAITING_APPROVAL},
};

static bool json_whitespace(unsigned char byte) {
  return byte == ' ' || byte == '\t' || byte == '\n' || byte == '\r';
}

static bool json_digit(unsigned char byte) {
  return byte >= '0' && byte <= '9';
}

static bool json_number_valid(const char *json, size_t len, size_t *offset) {
  size_t cursor = *offset;

  if (json[cursor] == '-') cursor++;
  if (cursor >= len) return false;

  if (json[cursor] == '0') {
    cursor++;
    if (cursor < len && json_digit((unsigned char)json[cursor])) return false;
  } else if (json[cursor] >= '1' && json[cursor] <= '9') {
    do {
      cursor++;
    } while (cursor < len && json_digit((unsigned char)json[cursor]));
  } else {
    return false;
  }

  if (cursor < len && json[cursor] == '.') {
    cursor++;
    if (cursor >= len || !json_digit((unsigned char)json[cursor])) return false;
    do {
      cursor++;
    } while (cursor < len && json_digit((unsigned char)json[cursor]));
  }

  if (cursor < len && (json[cursor] == 'e' || json[cursor] == 'E')) {
    cursor++;
    if (cursor < len && (json[cursor] == '+' || json[cursor] == '-')) cursor++;
    if (cursor >= len || !json_digit((unsigned char)json[cursor])) return false;
    do {
      cursor++;
    } while (cursor < len && json_digit((unsigned char)json[cursor]));
  }

  *offset = cursor - 1;
  return true;
}

/* cJSON intentionally accepts any raw byte <= 0x20 as whitespace and keeps
 * decoded NUL bytes in C strings without exposing their decoded length. Its
 * number scanner also accepts leading zeroes. Tighten those edges before
 * reading the tree. */
static bool json_lexically_valid(const char *json, size_t len) {
  bool in_string = false;
  bool escaped = false;

  for (size_t i = 0; i < len; i++) {
    unsigned char byte = (unsigned char)json[i];

    if (!in_string) {
      if (byte == '"') {
        in_string = true;
      } else if (byte < 0x20 && !json_whitespace(byte)) {
        return false;
      } else if ((byte == '-') || json_digit(byte)) {
        if (!json_number_valid(json, len, &i)) return false;
      }
      continue;
    }

    if (escaped) {
      escaped = false;
      if (byte == 'u' && i + 4 < len && json[i + 1] == '0' &&
          json[i + 2] == '0' && json[i + 3] == '0' && json[i + 4] == '0') {
        return false;
      }
    } else if (byte == '\\') {
      escaped = true;
    } else if (byte == '"') {
      in_string = false;
    } else if (byte < 0x20) {
      return false;
    }
  }

  return true;
}

static bool trailing_is_whitespace(const char *json, size_t len,
                                   const char *parse_end) {
  if (!parse_end || parse_end < json || parse_end > json + len) return false;
  for (const char *cursor = parse_end; cursor < json + len; cursor++) {
    if (!json_whitespace((unsigned char)*cursor)) return false;
  }
  return true;
}

static bool uint32_member(const cJSON *object, const char *key,
                          uint32_t *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
  if (!cJSON_IsNumber(item)) return false;

  double value = item->valuedouble;
  if (!(value >= 0.0 && value <= (double)UINT32_MAX)) return false;

  uint32_t converted = (uint32_t)value;
  if ((double)converted != value) return false;
  *out = converted;
  return true;
}

static bool nullable_string_member(const cJSON *object, const char *key,
                                   char *destination, size_t capacity) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
  if (!item) return false;
  if (cJSON_IsNull(item)) return true;
  if (!cJSON_IsString(item) || !item->valuestring) return false;

  const unsigned char *source = (const unsigned char *)item->valuestring;
  size_t length = 0;
  while (source[length] != '\0') {
    if (source[length] < 0x20 || length + 1 >= capacity) return false;
    length++;
  }

  memcpy(destination, source, length + 1);
  return true;
}

static bool state_member(const cJSON *object, tk_agent_state *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "state");
  if (!cJSON_IsString(item) || !item->valuestring) return false;

  for (size_t i = 0; i < sizeof state_entries / sizeof state_entries[0]; i++) {
    if (strcmp(item->valuestring, state_entries[i].name) == 0) {
      *out = state_entries[i].value;
      return true;
    }
  }

  *out = TK_AGENT_UNKNOWN;
  return true;
}

static bool activity_member(const cJSON *object, tk_agent_activity *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "activity");
  if (!item) return false;
  if (cJSON_IsNull(item)) {
    *out = TK_ACTIVITY_NONE;
    return true;
  }
  if (!cJSON_IsString(item) || !item->valuestring) return false;

  for (size_t i = 0;
       i < sizeof activity_entries / sizeof activity_entries[0]; i++) {
    if (strcmp(item->valuestring, activity_entries[i].name) == 0) {
      *out = activity_entries[i].value;
      return true;
    }
  }

  *out = TK_ACTIVITY_UNKNOWN;
  return true;
}

static bool agent_member(const cJSON *agents, const char *key,
                         tk_agent_status *out) {
  const cJSON *agent = cJSON_GetObjectItemCaseSensitive(agents, key);
  if (!cJSON_IsObject(agent)) return false;

  return nullable_string_member(agent, "task_id", out->task_id,
                                sizeof out->task_id) &&
         nullable_string_member(agent, "event_id", out->event_id,
                                sizeof out->event_id) &&
         state_member(agent, &out->state) &&
         nullable_string_member(agent, "project", out->project,
                                sizeof out->project) &&
         activity_member(agent, &out->activity) &&
         uint32_member(agent, "updated_ms", &out->updated_ms);
}

bool tk_agent_status_parse(const char *json, size_t len,
                           tk_agent_snapshot *out) {
  if (!json || !out || !json_lexically_valid(json, len)) return false;

  const char *parse_end = NULL;
  cJSON *root = cJSON_ParseWithLengthOpts(json, len, &parse_end, false);
  if (!root) return false;

  bool ok = false;
  tk_agent_snapshot next = {0};
  uint32_t version = 0;

  if (!trailing_is_whitespace(json, len, parse_end)) goto done;
  if (!cJSON_IsObject(root)) goto done;
  if (cJSON_GetObjectItemCaseSensitive(root, "error")) goto done;
  if (!uint32_member(root, "v", &version) || version != 1) goto done;
  if (!uint32_member(root, "seq", &next.seq)) goto done;

  const cJSON *agents = cJSON_GetObjectItemCaseSensitive(root, "agents");
  if (!cJSON_IsObject(agents)) goto done;
  if (!agent_member(agents, "claude", &next.claude)) goto done;
  if (!agent_member(agents, "codex", &next.codex)) goto done;

  *out = next;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}
