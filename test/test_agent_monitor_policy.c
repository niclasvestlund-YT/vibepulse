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

static tk_agent_status agent(tk_agent_state state, uint32_t updated_ms,
                             const char *event_id) {
  tk_agent_status value = {0};
  value.state = state;
  value.updated_ms = updated_ms;
  snprintf(value.event_id, sizeof value.event_id, "%s", event_id);
  return value;
}

int main(void) {
  tk_agent_status agents[2] = {
      agent(TK_AGENT_WORKING, 50, "work-1"),
      agent(TK_AGENT_WAITING, 90, "wait-1"),
  };
  bool present[2] = {true, true};
  tk_agent_manual_choice manual = {0};

  check("utan manuellt val vinner högsta prioritet",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 1);

  tk_agent_monitor_manual_choice_set(&manual, 0, agents);
  check("redan väntande agent bryter inte manuellt val vid identisk repoll",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 0);
  snprintf(agents[1].event_id, sizeof agents[1].event_id, "wait-2");
  check("ny väntande eventgeneration bryter manuellt val",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 1);

  agents[0] = agent(TK_AGENT_WORKING, 1, "work-2");
  agents[1] = agent(TK_AGENT_WORKING, 500, "work-3");
  tk_agent_monitor_manual_choice_set(&manual, 1, agents);
  check("manuellt val består vid lika prioritet trots äldre status",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 1);

  agents[0] = agent(TK_AGENT_DONE, 1, "done-1");
  agents[1] = agent(TK_AGENT_WORKING, 500, "work-4");
  tk_agent_monitor_manual_choice_set(&manual, 1, agents);
  check("manuellt val består mot lägre prioritet",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 1);

  agents[0] = agent(TK_AGENT_WORKING, 100, "work-5");
  agents[1] = agent(TK_AGENT_WORKING, 1, "work-6");
  tk_agent_monitor_manual_choice_set(&manual, 1, agents);
  agents[0] = agent(TK_AGENT_WAITING, 100, "wait-new");
  check("ny strikt högre prioritet bryter manuellt val",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 0);

  present[1] = false;
  check("försvunnen manuellt vald provider ersätts",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 0);

  present[1] = true;
  agents[0] = agent(TK_AGENT_WORKING, 80, "work-7");
  agents[1] = agent(TK_AGENT_WORKING, 20, "work-8");
  tk_agent_monitor_manual_choice_clear(&manual);
  check("utan manuellt val avgör färskast vid lika prioritet",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == 1);

  present[0] = false;
  present[1] = false;
  tk_agent_monitor_manual_choice_set(&manual, 1, agents);
  check("inga visningsbara providers ger ingen vald",
        tk_agent_monitor_resolve_provider(agents, present, &manual) == -1);

  if (failures == 0) {
    printf("OK: alla agentmonitor-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
