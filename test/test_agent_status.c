/*
 * Agentstatusens kontraktstester: den riktiga simulatorfixturen plus
 * fientliga payloads. Avvisade svar får aldrig skriva över senaste goda
 * snapshoten.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../components/app_tokens/agent_status_parse.h"

static int failures = 0;

static char *read_file(const char *path, size_t *len_out) {
  FILE *f = fopen(path, "rb");
  if (!f) { printf("FAIL kan inte läsa %s\n", path); failures++; return NULL; }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  char *buf = malloc((size_t)n + 1);
  fread(buf, 1, (size_t)n, f);
  buf[n] = '\0';
  fclose(f);
  if (len_out) *len_out = (size_t)n;
  return buf;
}

static void check(const char *what, int cond) {
  if (!cond) { printf("FAIL %s\n", what); failures++; }
}

static void check_rejected_unchanged(const char *what, const char *json,
                                     tk_agent_snapshot *out) {
  memset(out, 0xa5, sizeof *out);
  tk_agent_snapshot before;
  memcpy(&before, out, sizeof before);
  if (tk_agent_status_parse(json, strlen(json), out)) {
    printf("FAIL %s accepterades\n", what);
    failures++;
  }
  if (memcmp(&before, out, sizeof before) != 0) {
    printf("FAIL %s rörde utdata\n", what);
    failures++;
  }
}

/* Alla literaler parsas med strlen; inga handräknade payloadlängder. */
#define PARSE(s, out) tk_agent_status_parse((s), strlen(s), (out))
#define IDLE_CODEX \
  "\"codex\":{\"task_id\":null,\"event_id\":null,\"state\":\"idle\"," \
  "\"project\":null,\"activity\":null,\"updated_ms\":0}"
#define PAYLOAD(CLAUDE) \
  "{\"v\":1,\"seq\":1,\"agents\":{" CLAUDE "," IDLE_CODEX "}}"

#define CLAUDE_WORKING \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}"
#define CLAUDE_NEGATIVE_UPDATED \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":-1}"
#define CLAUDE_UNDERFLOW_UPDATED \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":1e-9999}"
#define CLAUDE_LONG_PROJECT \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"12345678901234567\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}"
#define CLAUDE_CONTROL_PROJECT \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Tor\\u0001get\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}"
#define CLAUDE_UNKNOWN_STATE \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"reviewing\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}"
#define CLAUDE_UNKNOWN_ACTIVITY \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"reviewing\",\"updated_ms\":1}"
#define CLAUDE_MAX_UPDATED \
  "\"claude\":{\"task_id\":\"turn-1\",\"event_id\":\"event-1\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":4294967295}"

#define DEPTH_PREFIX "{\"v\":1,\"seq\":1,\"metadata\":"
#define DEPTH_SUFFIX \
  ",\"markers\":\"\\\"[[[[{{{{]]]]}}}}\",\"agents\":{" \
  CLAUDE_WORKING "," IDLE_CODEX "}}"

static char *depth_payload(size_t array_depth) {
  size_t prefix_len = strlen(DEPTH_PREFIX);
  size_t suffix_len = strlen(DEPTH_SUFFIX);
  size_t total = prefix_len + array_depth + strlen("null") + array_depth +
                 suffix_len + 1;
  char *payload = malloc(total);
  if (!payload) {
    printf("FAIL kan inte allokera djup-payload\n");
    failures++;
    return NULL;
  }

  char *cursor = payload;
  memcpy(cursor, DEPTH_PREFIX, prefix_len);
  cursor += prefix_len;
  memset(cursor, '[', array_depth);
  cursor += array_depth;
  memcpy(cursor, "null", strlen("null"));
  cursor += strlen("null");
  memset(cursor, ']', array_depth);
  cursor += array_depth;
  memcpy(cursor, DEPTH_SUFFIX, suffix_len + 1);
  return payload;
}

int main(void) {
  size_t len;
  char *json;
  tk_agent_snapshot snapshot = {0};

  json = read_file(FIXTURES_DIR "/agent-status-claude-working.json", &len);
  if (json) {
    check("fixturen parsar", tk_agent_status_parse(json, len, &snapshot));
    check("seq", snapshot.seq == 184);
    check("Claude arbetar", snapshot.claude.state == TK_AGENT_WORKING);
    check("Claude testar", snapshot.claude.activity == TK_ACTIVITY_TESTING);
    check("projekt", strcmp(snapshot.claude.project, "Torget") == 0);
    check("Codex vilar", snapshot.codex.state == TK_AGENT_IDLE);
    free(json);
  }

  check_rejected_unchanged(
      "fel version",
      "{\"v\":2,\"seq\":1,\"agents\":{" CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged("error-form", "{\"error\":\"nope\"}",
                           &snapshot);
  check_rejected_unchanged("negativ updated_ms",
                           PAYLOAD(CLAUDE_NEGATIVE_UPDATED), &snapshot);
  check_rejected_unchanged("17 teckens projekt",
                           PAYLOAD(CLAUDE_LONG_PROJECT), &snapshot);
  check_rejected_unchanged("kontrolltecken i projekt",
                           PAYLOAD(CLAUDE_CONTROL_PROJECT), &snapshot);

  check("okänd state parsar", PARSE(PAYLOAD(CLAUDE_UNKNOWN_STATE), &snapshot));
  check("okänd state mappas", snapshot.claude.state == TK_AGENT_UNKNOWN);
  check("okänd activity parsar",
        PARSE(PAYLOAD(CLAUDE_UNKNOWN_ACTIVITY), &snapshot));
  check("okänd activity mappas",
        snapshot.claude.activity == TK_ACTIVITY_UNKNOWN);

  check_rejected_unchanged("trasig JSON", "{\"v\":1", &snapshot);
  check_rejected_unchanged("skräp efter JSON",
                           PAYLOAD(CLAUDE_WORKING) "x", &snapshot);
  check_rejected_unchanged("rot som inte är objekt", "[]", &snapshot);
  check_rejected_unchanged(
      "fraktionell seq",
      "{\"v\":1,\"seq\":1.5,\"agents\":{" CLAUDE_WORKING ","
      IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "avrundad fraktion i version",
      "{\"v\":1.00000000000000001,\"seq\":1,\"agents\":{" \
      CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "avrundad fraktion i seq",
      "{\"v\":1,\"seq\":1.00000000000000001,\"agents\":{" \
      CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "underflödande exponent i seq",
      "{\"v\":1,\"seq\":1e-9999,\"agents\":{" CLAUDE_WORKING ","
      IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "seq över uint32",
      "{\"v\":1,\"seq\":4294967296,\"agents\":{" CLAUDE_WORKING ","
      IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "seq med inledande nolla",
      "{\"v\":1,\"seq\":01,\"agents\":{" CLAUDE_WORKING ","
      IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "saknad Codex",
      "{\"v\":1,\"seq\":1,\"agents\":{" CLAUDE_WORKING "}}",
      &snapshot);
  check_rejected_unchanged(
      "saknad agentnyckel",
      PAYLOAD("\"claude\":{\"task_id\":\"turn-1\"," \
              "\"event_id\":\"event-1\",\"state\":\"working\"," \
              "\"project\":\"Torget\",\"updated_ms\":1}"),
      &snapshot);
  check_rejected_unchanged(
      "fel typ i agentsträng",
      PAYLOAD("\"claude\":{\"task_id\":7,\"event_id\":\"event-1\"," \
              "\"state\":\"working\",\"project\":\"Torget\"," \
              "\"activity\":\"testing\",\"updated_ms\":1}"),
      &snapshot);
  check_rejected_unchanged(
      "fraktionell updated_ms",
      PAYLOAD("\"claude\":{\"task_id\":\"turn-1\"," \
              "\"event_id\":\"event-1\",\"state\":\"working\"," \
              "\"project\":\"Torget\",\"activity\":\"testing\"," \
              "\"updated_ms\":1.5}"),
      &snapshot);
  check_rejected_unchanged("underflödande exponent i updated_ms",
                           PAYLOAD(CLAUDE_UNDERFLOW_UPDATED), &snapshot);
  check_rejected_unchanged(
      "kontrollnolla i projekt",
      PAYLOAD("\"claude\":{\"task_id\":\"turn-1\"," \
              "\"event_id\":\"event-1\",\"state\":\"working\"," \
              "\"project\":\"Tor\\u0000get\",\"activity\":\"testing\"," \
              "\"updated_ms\":1}"),
      &snapshot);

  check("irrelevanta nummer och nummersträngar förväxlas inte",
        PARSE("{\"v\":1,\"seq\":1,\"metadata\":{"
              "\"v\":1.00000000000000001,\"seq\":1e-9999,"
              "\"updated_ms\":1.5},\"agents\":{" \
              "\"claude\":{\"task_id\":\"1e-9999\"," \
              "\"event_id\":\"1.00000000000000001\"," \
              "\"state\":\"working\",\"project\":\"Torget\"," \
              "\"activity\":\"testing\",\"updated_ms\":1}," \
              IDLE_CODEX "}}",
              &snapshot));
  check("nummersträng bevaras",
        strcmp(snapshot.claude.task_id, "1e-9999") == 0);

  check("UINT32_MAX accepteras",
        PARSE("{\"v\":1,\"seq\":4294967295,\"agents\":{" \
              CLAUDE_MAX_UPDATED "," IDLE_CODEX "}}",
              &snapshot) &&
            snapshot.seq == UINT32_MAX &&
            snapshot.claude.updated_ms == UINT32_MAX);
  check("omordnade kontraktsfält accepteras",
        PARSE("{\"agents\":{" IDLE_CODEX ","
              "\"claude\":{\"updated_ms\":9,\"activity\":\"testing\"," \
              "\"project\":\"Torget\",\"state\":\"working\"," \
              "\"event_id\":\"event-9\",\"task_id\":\"turn-9\"}},"
              "\"seq\":9,\"v\":1}",
              &snapshot) &&
            snapshot.seq == 9 && snapshot.claude.updated_ms == 9);
  check("BOM accepteras",
        PARSE("\xEF\xBB\xBF" PAYLOAD(CLAUDE_WORKING), &snapshot));
  check("escapade egenskapsnamn accepteras",
        PARSE("{\"\\u0076\":1,\"se\\u0071\":8,\"ag\\u0065nts\":{"
              "\"cl\\u0061ude\":{\"task_\\u0069d\":\"turn-8\"," \
              "\"event_\\u0069d\":\"event-8\",\"st\\u0061te\":\"working\"," \
              "\"pro\\u006aect\":\"Torget\","
              "\"act\\u0069vity\":\"testing\","
              "\"updated_\\u006ds\":8}," IDLE_CODEX "}}",
              &snapshot) &&
            snapshot.seq == 8 && snapshot.claude.updated_ms == 8);
  check("okänd array- och boolmetadata accepteras",
        PARSE("{\"v\":1,\"metadata\":[true,false,{\"nested\":[null]}],"
              "\"seq\":1,\"agents\":{" CLAUDE_WORKING "," \
              IDLE_CODEX "}}",
              &snapshot));

  char *depth16 = depth_payload(15);
  if (depth16) {
    check("totalt djup 16 accepteras", PARSE(depth16, &snapshot));
    free(depth16);
  }
  char *depth17 = depth_payload(16);
  if (depth17) {
    check_rejected_unchanged("totalt djup 17", depth17, &snapshot);
    free(depth17);
  }

  check_rejected_unchanged(
      "dubbel v",
      "{\"v\":1,\"v\":1,\"seq\":1,\"agents\":{" CLAUDE_WORKING ","
      IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "dubbel seq med ogiltig andra",
      "{\"v\":1,\"seq\":1,\"seq\":1.5,\"agents\":{" \
      CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "escapad dubbel v",
      "{\"v\":1,\"\\u0076\":1,\"seq\":1,\"agents\":{" \
      CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "dubbel agent",
      "{\"v\":1,\"seq\":1,\"agents\":{" CLAUDE_WORKING "," \
      CLAUDE_WORKING "," IDLE_CODEX "}}",
      &snapshot);
  check_rejected_unchanged(
      "dubbelt agentfält",
      PAYLOAD("\"claude\":{\"task_id\":\"turn-1\"," \
              "\"event_id\":\"event-1\",\"state\":\"working\"," \
              "\"state\":\"done\",\"project\":\"Torget\"," \
              "\"activity\":\"testing\",\"updated_ms\":1}"),
      &snapshot);

  tk_agent_snapshot before;
  memcpy(&before, &snapshot, sizeof before);
  check("NULL json avvisas", !tk_agent_status_parse(NULL, 0, &snapshot));
  check("NULL json rör inte utdata",
        memcmp(&before, &snapshot, sizeof before) == 0);
  check("NULL out avvisas",
        !tk_agent_status_parse(PAYLOAD(CLAUDE_WORKING),
                               strlen(PAYLOAD(CLAUDE_WORKING)), NULL));

  if (failures == 0) {
    printf("OK: alla agentstatus-tester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
