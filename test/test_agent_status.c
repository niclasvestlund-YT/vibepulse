/* Strict contract tests for VibePulse agent-status v2. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../components/app_tokens/agent_status_parse.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static char *read_file(const char *path, size_t *len_out) {
  FILE *stream = fopen(path, "rb");
  if (!stream) {
    printf("FAIL kan inte läsa %s\n", path);
    failures++;
    return NULL;
  }
  fseek(stream, 0, SEEK_END);
  long length = ftell(stream);
  fseek(stream, 0, SEEK_SET);
  char *data = malloc((size_t)length + 1);
  if (!data) {
    fclose(stream);
    failures++;
    return NULL;
  }
  fread(data, 1, (size_t)length, stream);
  data[length] = '\0';
  fclose(stream);
  *len_out = (size_t)length;
  return data;
}

static void rejected_unchanged(const char *what, const char *json,
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

#define PARSE(JSON, OUT) tk_agent_status_parse((JSON), strlen(JSON), (OUT))

#define WORKING_JOB \
  "{\"task_id\":\"claude-task\",\"event_id\":\"claude-event\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"editing\",\"model\":\"FABLE 5\"," \
  "\"effort\":\"XHIGH\",\"updated_ms\":25}"

#define CODEX_JOB \
  "{\"task_id\":\"codex-task\",\"event_id\":\"codex-event\"," \
  "\"state\":\"working\",\"project\":\"Buddy\"," \
  "\"activity\":\"testing\",\"model\":\"GPT-5.6 SOL\"," \
  "\"effort\":\"XHIGH\",\"updated_ms\":10}"

#define NO_METADATA_JOB \
  "{\"task_id\":\"plain\",\"event_id\":\"plain-event\"," \
  "\"state\":\"done\",\"project\":null,\"activity\":null," \
  "\"updated_ms\":4294967295}"

#define UNKNOWN_JOB \
  "{\"task_id\":\"unknown\",\"event_id\":\"unknown-event\"," \
  "\"state\":\"reviewing\",\"project\":null," \
  "\"activity\":\"reviewing\",\"updated_ms\":1}"

#define EMPTY_CODEX "\"codex\":{\"active_count\":0,\"jobs\":[]}"
#define ONE_CLAUDE(JOB) \
  "\"claude\":{\"active_count\":1,\"jobs\":[" JOB "]}"
#define PAYLOAD(JOB) \
  "{\"v\":2,\"seq\":7,\"agents\":{" ONE_CLAUDE(JOB) "," \
  EMPTY_CODEX "}}"

int main(void) {
  tk_agent_snapshot snapshot = {0};
  size_t fixture_len = 0;
  char *fixture = read_file(
      FIXTURES_DIR "/agent-status-claude-working.json", &fixture_len);
  if (fixture) {
    check("v2-fixturen parsar",
          tk_agent_status_parse(fixture, fixture_len, &snapshot));
    check("fixturens seq", snapshot.seq == 201);
    check("en Claude-session",
          snapshot.claude.active_count == 1 &&
          snapshot.claude.job_count == 1);
    check("Claude ändrar filer",
          snapshot.claude.jobs[0].state == TK_AGENT_WORKING &&
          snapshot.claude.jobs[0].activity == TK_ACTIVITY_EDITING);
    check("modell, effort och projekt bevaras",
          snapshot.claude.jobs[0].has_model &&
          snapshot.claude.jobs[0].has_effort &&
          strcmp(snapshot.claude.jobs[0].model, "FABLE 5") == 0 &&
          strcmp(snapshot.claude.jobs[0].effort, "XHIGH") == 0 &&
          strcmp(snapshot.claude.jobs[0].project, "Torget") == 0);
    check("Codex-listan är tom",
          snapshot.codex.active_count == 0 &&
          snapshot.codex.job_count == 0);
    free(fixture);
  }

  const char multi[] =
      "{\"v\":2,\"seq\":9,\"agents\":{" \
      "\"claude\":{\"active_count\":5,\"jobs\":[" WORKING_JOB "," \
      "{\"task_id\":\"claude-2\",\"event_id\":\"event-2\"," \
      "\"state\":\"waiting\",\"project\":\"Solelkollen\"," \
      "\"activity\":\"waiting_approval\",\"updated_ms\":5}]}," \
      "\"codex\":{\"active_count\":1,\"jobs\":[" CODEX_JOB "]}}}";
  check("flera jobb parsas", PARSE(multi, &snapshot));
  check("alla aktiva räknas även över listtak",
        snapshot.claude.active_count == 5);
  check("två Claude-jobb lagras", snapshot.claude.job_count == 2);
  check("Codex-projekt bevaras",
        snapshot.codex.job_count == 1 &&
        strcmp(snapshot.codex.jobs[0].project, "Buddy") == 0);

  check("valfri modellmetadata kan saknas",
        PARSE(PAYLOAD(NO_METADATA_JOB), &snapshot));
  check("saknad metadata är explicit",
        !snapshot.claude.jobs[0].has_model &&
        !snapshot.claude.jobs[0].has_effort &&
        snapshot.claude.jobs[0].updated_ms == UINT32_MAX);

  check("framtida enumvärden parsas",
        PARSE(PAYLOAD(UNKNOWN_JOB), &snapshot));
  check("framtida enumvärden mappas till unknown",
        snapshot.claude.jobs[0].state == TK_AGENT_UNKNOWN &&
        snapshot.claude.jobs[0].activity == TK_ACTIVITY_UNKNOWN);

  rejected_unchanged(
      "v1 avvisas",
      "{\"v\":1,\"seq\":7,\"agents\":{" ONE_CLAUDE(WORKING_JOB) "," \
      EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged("error-form", "{\"error\":\"nope\"}", &snapshot);
  rejected_unchanged("skräp efter rot", PAYLOAD(WORKING_JOB) "{}",
                     &snapshot);
  rejected_unchanged("giltig JSON efter rot", PAYLOAD(WORKING_JOB) " []",
                     &snapshot);
  rejected_unchanged("rotarray", "[]", &snapshot);

  rejected_unchanged(
      "fem publika jobb avvisas",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":5,\"jobs\":[" \
      WORKING_JOB "," WORKING_JOB "," WORKING_JOB "," WORKING_JOB "," \
      WORKING_JOB "]}," EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "negativ active_count",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":-1,\"jobs\":[]}," \
      EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "active_count över uint8",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":256,\"jobs\":[]}," \
      EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "fraktionell active_count",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":1.5,\"jobs\":[]}," \
      EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "provider med extrafält",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":0,\"jobs\":[],\"private\":1}," \
      EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "jobb med extrafält",
      PAYLOAD("{\"task_id\":\"a\",\"event_id\":\"b\"," \
              "\"state\":\"working\",\"project\":null," \
              "\"activity\":\"thinking\",\"updated_ms\":0," \
              "\"private\":true}"), &snapshot);
  rejected_unchanged(
      "dubbelt providerfält",
      "{\"v\":2,\"seq\":1,\"agents\":{" ONE_CLAUDE(WORKING_JOB) "," \
      ONE_CLAUDE(WORKING_JOB) "," EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "dubbelt jobsfält",
      "{\"v\":2,\"seq\":1,\"agents\":{" \
      "\"claude\":{\"active_count\":1,\"jobs\":[]," \
      "\"jobs\":[" WORKING_JOB "]}," EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "dubbelt jobbfält",
      PAYLOAD("{\"task_id\":\"a\",\"task_id\":\"b\"," \
              "\"event_id\":\"e\",\"state\":\"working\"," \
              "\"project\":null,\"activity\":\"thinking\"," \
              "\"updated_ms\":0}"), &snapshot);

  rejected_unchanged(
      "för långt projekt",
      PAYLOAD("{\"task_id\":\"a\",\"event_id\":\"e\"," \
              "\"state\":\"working\"," \
              "\"project\":\"12345678901234567\"," \
              "\"activity\":\"thinking\",\"updated_ms\":0}"),
      &snapshot);
  rejected_unchanged(
      "kontrollnolla i projekt",
      PAYLOAD("{\"task_id\":\"a\",\"event_id\":\"e\"," \
              "\"state\":\"working\",\"project\":\"Tor\\u0000get\"," \
              "\"activity\":\"thinking\",\"updated_ms\":0}"),
      &snapshot);
  rejected_unchanged(
      "modell med fel typ",
      PAYLOAD("{\"task_id\":\"a\",\"event_id\":\"e\"," \
              "\"state\":\"working\",\"project\":null," \
              "\"activity\":\"thinking\",\"model\":7," \
              "\"updated_ms\":0}"), &snapshot);
  rejected_unchanged(
      "fraktionell updated_ms",
      PAYLOAD("{\"task_id\":\"a\",\"event_id\":\"e\"," \
              "\"state\":\"working\",\"project\":null," \
              "\"activity\":\"thinking\",\"updated_ms\":1.5}"),
      &snapshot);
  rejected_unchanged(
      "seq över uint32",
      "{\"v\":2,\"seq\":4294967296,\"agents\":{" \
      ONE_CLAUDE(WORKING_JOB) "," EMPTY_CODEX "}}", &snapshot);
  rejected_unchanged(
      "seq med inledande nolla",
      "{\"v\":2,\"seq\":01,\"agents\":{" \
      ONE_CLAUDE(WORKING_JOB) "," EMPTY_CODEX "}}", &snapshot);

  check("omordnade providerfält accepteras",
        PARSE("{\"agents\":{" \
              "\"codex\":{\"jobs\":[],\"active_count\":0}," \
              "\"claude\":{\"jobs\":[" WORKING_JOB "] ," \
              "\"active_count\":1}},\"seq\":11,\"v\":2}",
              &snapshot) && snapshot.seq == 11);
  check("BOM accepteras",
        PARSE("\xEF\xBB\xBF" PAYLOAD(WORKING_JOB), &snapshot));

  check("provider-enum har stabil ordning",
        TK_AGENT_PROVIDER_CLAUDE == 0 && TK_AGENT_PROVIDER_CODEX == 1 &&
        TK_AGENT_PROVIDER_COUNT == 2 && TK_AGENT_JOBS_MAX == 4);

  if (failures == 0) {
    printf("OK: alla agentstatus-v2-tester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
