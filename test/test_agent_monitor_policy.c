#include <stdbool.h>
#include <stdio.h>

#include "../components/app_tokens/agent_monitor_policy.h"

static int failures;

static void check(const char *what, int condition) {
  if (!condition) {
    printf("FAIL %s\n", what);
    failures++;
  }
}

static tk_agent_status agent(tk_agent_state state, uint32_t updated_ms) {
  tk_agent_status value = {0};
  value.state = state;
  value.updated_ms = updated_ms;
  return value;
}

int main(void) {
  tk_agent_status agents[2] = {
      agent(TK_AGENT_WORKING, 50),
      agent(TK_AGENT_WAITING, 90),
  };
  bool present[2] = {true, true};

  check("utan manuellt val vinner högsta prioritet",
        tk_agent_monitor_resolve_provider(agents, present, 0, false) == 1);

  agents[0] = agent(TK_AGENT_WORKING, 1);
  agents[1] = agent(TK_AGENT_WORKING, 500);
  check("manuellt val består vid lika prioritet trots äldre status",
        tk_agent_monitor_resolve_provider(agents, present, 1, true) == 1);

  agents[0] = agent(TK_AGENT_DONE, 1);
  agents[1] = agent(TK_AGENT_WORKING, 500);
  check("manuellt val består mot lägre prioritet",
        tk_agent_monitor_resolve_provider(agents, present, 1, true) == 1);

  agents[0] = agent(TK_AGENT_WAITING, 100);
  agents[1] = agent(TK_AGENT_WORKING, 1);
  check("strikt högre prioritet bryter manuellt val",
        tk_agent_monitor_resolve_provider(agents, present, 1, true) == 0);

  present[1] = false;
  check("försvunnen manuellt vald provider ersätts",
        tk_agent_monitor_resolve_provider(agents, present, 1, true) == 0);

  present[1] = true;
  agents[0] = agent(TK_AGENT_WORKING, 80);
  agents[1] = agent(TK_AGENT_WORKING, 20);
  check("utan manuellt val avgör färskast vid lika prioritet",
        tk_agent_monitor_resolve_provider(agents, present, 0, false) == 1);

  present[0] = false;
  present[1] = false;
  check("inga visningsbara providers ger ingen vald",
        tk_agent_monitor_resolve_provider(agents, present, 1, true) == -1);

  if (failures == 0) {
    printf("OK: alla agentmonitor-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
