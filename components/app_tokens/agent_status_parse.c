#include "agent_status_parse.h"

#include <stdint.h>
#include <string.h>

#include "../../third_party/cjson/cJSON.h"

/* V1-kontraktet behöver bara djup 3. Extra metadata får gott om marginal,
 * men rekursionen i både cJSON och råtoken-vandringen hålls ESP-säker. */
#define TK_AGENT_JSON_MAX_DEPTH 16U

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

static const char *const root_required_keys[] = {
    "v", "seq", "agents", NULL,
};

static const char *const agents_required_keys[] = {
    "claude", "codex", NULL,
};

static const char *const agent_required_keys[] = {
    "task_id", "event_id", "state", "project", "activity", "updated_ms",
    NULL,
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
  size_t depth = 0;

  for (size_t i = 0; i < len; i++) {
    unsigned char byte = (unsigned char)json[i];

    if (!in_string) {
      if (byte == '"') {
        in_string = true;
      } else if (byte < 0x20 && !json_whitespace(byte)) {
        return false;
      } else if ((byte == '-') || json_digit(byte)) {
        if (!json_number_valid(json, len, &i)) return false;
      } else if (byte == '{' || byte == '[') {
        if (depth >= TK_AGENT_JSON_MAX_DEPTH) return false;
        depth++;
      } else if (byte == '}' || byte == ']') {
        if (depth == 0) return false;
        depth--;
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

  return depth == 0;
}

static bool trailing_is_whitespace(const char *json, size_t len,
                                   const char *parse_end) {
  if (!parse_end || parse_end < json || parse_end > json + len) return false;
  for (const char *cursor = parse_end; cursor < json + len; cursor++) {
    if (!json_whitespace((unsigned char)*cursor)) return false;
  }
  return true;
}

typedef struct {
  const char *json;
  size_t len;
  size_t offset;
} raw_cursor;

static void raw_skip_whitespace(raw_cursor *cursor) {
  while (cursor->offset < cursor->len &&
         json_whitespace((unsigned char)cursor->json[cursor->offset])) {
    cursor->offset++;
  }
}

static bool raw_take(raw_cursor *cursor, char expected) {
  raw_skip_whitespace(cursor);
  if (cursor->offset >= cursor->len ||
      cursor->json[cursor->offset] != expected) {
    return false;
  }
  cursor->offset++;
  return true;
}

static bool raw_skip_string(raw_cursor *cursor) {
  raw_skip_whitespace(cursor);
  if (cursor->offset >= cursor->len || cursor->json[cursor->offset] != '"') {
    return false;
  }
  cursor->offset++;

  while (cursor->offset < cursor->len) {
    char byte = cursor->json[cursor->offset++];
    if (byte == '"') return true;
    if (byte == '\\') {
      if (cursor->offset >= cursor->len) return false;
      cursor->offset++;
    }
  }
  return false;
}

static bool raw_skip_literal(raw_cursor *cursor, const char *literal,
                             size_t literal_len) {
  raw_skip_whitespace(cursor);
  if (literal_len > cursor->len - cursor->offset ||
      memcmp(cursor->json + cursor->offset, literal, literal_len) != 0) {
    return false;
  }
  cursor->offset += literal_len;
  return true;
}

static bool raw_exact_uint32(raw_cursor *cursor, uint32_t *out) {
  raw_skip_whitespace(cursor);
  if (cursor->offset >= cursor->len ||
      !json_digit((unsigned char)cursor->json[cursor->offset])) {
    return false;
  }

  uint32_t value = 0;
  do {
    uint32_t digit = (uint32_t)(cursor->json[cursor->offset] - '0');
    if (value > (UINT32_MAX - digit) / 10U) return false;
    value = value * 10U + digit;
    cursor->offset++;
  } while (cursor->offset < cursor->len &&
           json_digit((unsigned char)cursor->json[cursor->offset]));

  if (cursor->offset < cursor->len &&
      (cursor->json[cursor->offset] == '.' ||
       cursor->json[cursor->offset] == 'e' ||
       cursor->json[cursor->offset] == 'E')) {
    return false;
  }

  *out = value;
  return true;
}

/* cJSON-nodens pekaridentitet väljer rätt medlem även när samma nyckelnamn
 * finns i ett annat objekt. Samtidig gång genom råtext och träd bevarar det
 * ursprungliga nummertoken som cJSON:s double annars hade avrundat. */
static bool raw_find_item(raw_cursor *cursor, const cJSON *item,
                          const cJSON *target, uint32_t *out, bool *found) {
  raw_skip_whitespace(cursor);

  if (item == target) {
    if (!raw_exact_uint32(cursor, out)) return false;
    *found = true;
    return true;
  }

  if (cJSON_IsObject(item)) {
    if (!raw_take(cursor, '{')) return false;
    const cJSON *child = item->child;
    if (!child) return raw_take(cursor, '}');

    while (child) {
      if (!raw_skip_string(cursor) || !raw_take(cursor, ':') ||
          !raw_find_item(cursor, child, target, out, found)) {
        return false;
      }
      child = child->next;
      if (child) {
        if (!raw_take(cursor, ',')) return false;
      } else if (!raw_take(cursor, '}')) {
        return false;
      }
    }
    return true;
  }

  if (cJSON_IsArray(item)) {
    if (!raw_take(cursor, '[')) return false;
    const cJSON *child = item->child;
    if (!child) return raw_take(cursor, ']');

    while (child) {
      if (!raw_find_item(cursor, child, target, out, found)) return false;
      child = child->next;
      if (child) {
        if (!raw_take(cursor, ',')) return false;
      } else if (!raw_take(cursor, ']')) {
        return false;
      }
    }
    return true;
  }

  if (cJSON_IsString(item)) return raw_skip_string(cursor);
  if (cJSON_IsNull(item)) return raw_skip_literal(cursor, "null", 4);
  if (cJSON_IsTrue(item)) return raw_skip_literal(cursor, "true", 4);
  if (cJSON_IsFalse(item)) return raw_skip_literal(cursor, "false", 5);
  if (cJSON_IsNumber(item)) {
    if (cursor->offset >= cursor->len) return false;
    size_t number_offset = cursor->offset;
    if (!json_number_valid(cursor->json, cursor->len, &number_offset)) {
      return false;
    }
    cursor->offset = number_offset + 1;
    return true;
  }

  return false;
}

static bool raw_uint32_for_item(const char *json, size_t len,
                                const cJSON *root, const cJSON *target,
                                uint32_t *out) {
  raw_cursor cursor = {.json = json, .len = len, .offset = 0};
  if (len >= 3 && memcmp(json, "\xEF\xBB\xBF", 3) == 0) cursor.offset = 3;

  bool found = false;
  return raw_find_item(&cursor, root, target, out, &found) && found;
}

static bool uint32_member(const char *json, size_t len, const cJSON *root,
                          const cJSON *object, const char *key,
                          uint32_t *out) {
  const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
  if (!cJSON_IsNumber(item)) return false;
  return raw_uint32_for_item(json, len, root, item, out);
}

/* cJSON lagrar avkodade egenskapsnamn i child->string, så även t.ex.
 * "\\u0076" räknas som v. Bara kontraktsnycklar måste vara unika; okända
 * extranycklar lämnas orörda. */
static bool required_keys_once(const cJSON *object,
                               const char *const *required_keys) {
  if (!cJSON_IsObject(object)) return false;

  for (size_t i = 0; required_keys[i]; i++) {
    size_t matches = 0;
    for (const cJSON *child = object->child; child; child = child->next) {
      if (child->string && strcmp(child->string, required_keys[i]) == 0) {
        matches++;
        if (matches > 1) return false;
      }
    }
    if (matches != 1) return false;
  }

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

static bool agent_member(const char *json, size_t len, const cJSON *root,
                         const cJSON *agents, const char *key,
                         tk_agent_status *out) {
  const cJSON *agent = cJSON_GetObjectItemCaseSensitive(agents, key);
  if (!required_keys_once(agent, agent_required_keys)) return false;

  return nullable_string_member(agent, "task_id", out->task_id,
                                sizeof out->task_id) &&
         nullable_string_member(agent, "event_id", out->event_id,
                                sizeof out->event_id) &&
         state_member(agent, &out->state) &&
         nullable_string_member(agent, "project", out->project,
                                sizeof out->project) &&
         activity_member(agent, &out->activity) &&
         uint32_member(json, len, root, agent, "updated_ms",
                       &out->updated_ms);
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
  if (!required_keys_once(root, root_required_keys)) goto done;
  if (!uint32_member(json, len, root, root, "v", &version) || version != 1) {
    goto done;
  }
  if (!uint32_member(json, len, root, root, "seq", &next.seq)) goto done;

  const cJSON *agents = cJSON_GetObjectItemCaseSensitive(root, "agents");
  if (!required_keys_once(agents, agents_required_keys)) goto done;
  if (!agent_member(json, len, root, agents, "claude", &next.claude)) {
    goto done;
  }
  if (!agent_member(json, len, root, agents, "codex", &next.codex)) goto done;

  *out = next;
  ok = true;

done:
  cJSON_Delete(root);
  return ok;
}
