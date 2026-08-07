#include <stdbool.h>
#include <stdint.h>
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

  tk_agent_status fresh_work = agent(TK_AGENT_WORKING, 100, "work-fresh");
  check("färskt arbete är närvarande",
        tk_agent_monitor_status_present(&fresh_work, "", 0));
  check("färskt närvarande arbete håller skärmen vaken",
        tk_agent_monitor_should_keep_awake(&fresh_work, "", 0));

  tk_agent_status stale_event =
      agent(TK_AGENT_WORKING, 120001, "work-stale-event");
  check("gammal eventtid gör working unknown",
        tk_agent_monitor_effective_state(&stale_event, 0) ==
            TK_AGENT_UNKNOWN);
  check("gammal raw working är inte närvarande",
        !tk_agent_monitor_status_present(&stale_event, "", 0));
  check("gammal raw working håller inte skärmen vaken",
        !tk_agent_monitor_should_keep_awake(&stale_event, "", 0));

  tk_agent_status stale_packet =
      agent(TK_AGENT_WORKING, 0, "work-stale-packet");
  check("gammalt paket gör working unknown, aldrig done",
        tk_agent_monitor_effective_state(&stale_packet, 120001) ==
            TK_AGENT_UNKNOWN);
  check("gammalt paket döljer working",
        !tk_agent_monitor_status_present(&stale_packet, "", 120001));
  check("gammalt paket håller inte skärmen vaken",
        !tk_agent_monitor_should_keep_awake(&stale_packet, "", 120001));

  tk_agent_status combined_stale =
      agent(TK_AGENT_WORKING, 80000, "work-combined-stale");
  check("eventtid plus paketålder följer 120-sekundersleasen",
        tk_agent_monitor_effective_state(&combined_stale, 40001) ==
            TK_AGENT_UNKNOWN);

  tk_agent_status boundary =
      agent(TK_AGENT_WORKING, 80000, "work-boundary");
  check("exakt 120 sekunder är fortfarande giltigt",
        tk_agent_monitor_effective_state(&boundary, 40000) ==
            TK_AGENT_WORKING);

  tk_agent_status dismissed =
      agent(TK_AGENT_WORKING, 0, "work-dismissed");
  check("kvitterat working är inte närvarande",
        !tk_agent_monitor_status_present(&dismissed, "work-dismissed", 0));
  check("kvitterat working håller inte skärmen vaken",
        !tk_agent_monitor_should_keep_awake(&dismissed, "work-dismissed", 0));

  tk_agent_status done = agent(TK_AGENT_DONE, 0, "done-terminal");
  check("terminal status är närvarande",
        tk_agent_monitor_status_present(&done, "", 0));
  check("terminal status håller inte skärmen vaken",
        !tk_agent_monitor_should_keep_awake(&done, "", 0));

  tk_agent_status mixed[2] = {
      agent(TK_AGENT_WAITING, 0, "waiting-selected"),
      agent(TK_AGENT_WORKING, 0, "working-unselected"),
  };
  char dismissed_ids[2][TK_AGENT_ID_CAP] = {{0}};
  uint8_t rendered_mask =
      tk_agent_monitor_presence_mask(mixed, dismissed_ids, 0);
  uint8_t stale_mask =
      tk_agent_monitor_presence_mask(mixed, dismissed_ids, 120001);
  check("vald terminal och ovald working syns först",
        rendered_mask == 0x03);
  check("ovald working försvinner när paketleasen går ut",
        stale_mask == 0x01);
  check("presenceändring hos ovald provider triggar UI-render",
        tk_agent_monitor_tick_should_render(true, 0, rendered_mask,
                                            stale_mask));
  check("vald terminalstatus bevaras efter ovald stale working",
        tk_agent_monitor_effective_state(&mixed[0], 120001) ==
            TK_AGENT_WAITING);
  check("oförändrad presence triggar ingen extra UI-render",
        !tk_agent_monitor_tick_should_render(true, 0, stale_mask,
                                             stale_mask));

  if (failures == 0) {
    printf("OK: alla agentmonitor-policytester gröna\n");
    return 0;
  }
  printf("%d test föll\n", failures);
  return 1;
}
