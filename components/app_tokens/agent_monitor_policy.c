#include "agent_monitor_policy.h"

#include <string.h>

static bool utf8_continuation(unsigned char byte) {
  return (byte & 0xC0U) == 0x80U;
}

static size_t utf8_sequence_length(const unsigned char *source) {
  unsigned char lead = source[0];
  if (lead < 0x80U) return 1;
  if (lead >= 0xC2U && lead <= 0xDFU && source[1] &&
      utf8_continuation(source[1])) {
    return 2;
  }
  if (lead >= 0xE0U && lead <= 0xEFU && source[1] && source[2] &&
      utf8_continuation(source[1]) && utf8_continuation(source[2])) {
    return 3;
  }
  if (lead >= 0xF0U && lead <= 0xF4U && source[1] && source[2] &&
      source[3] && utf8_continuation(source[1]) &&
      utf8_continuation(source[2]) && utf8_continuation(source[3])) {
    return 4;
  }
  return 1;
}

static bool project_ascii(unsigned char byte) {
  return (byte >= 'A' && byte <= 'Z') ||
         (byte >= '0' && byte <= '9') || byte == ' ' || byte == '-' ||
         byte == '.' || byte == '_';
}

void tk_agent_monitor_project_label(const char *source, char *destination,
                                    size_t capacity) {
  if (!destination || capacity == 0) return;
  destination[0] = '\0';
  if (!source) return;

  const unsigned char *cursor = (const unsigned char *)source;
  size_t output = 0;
  while (*cursor) {
    unsigned char encoded[2] = {'?', 0};
    size_t encoded_length = 1;
    size_t consumed = utf8_sequence_length(cursor);

    if (cursor[0] < 0x80U) {
      unsigned char byte = cursor[0];
      if (byte >= 'a' && byte <= 'z') byte = (unsigned char)(byte - 32U);
      encoded[0] = project_ascii(byte) ? byte : '?';
    } else if (consumed == 2 && cursor[0] == 0xC3U &&
               (cursor[1] == 0x84U || cursor[1] == 0x85U ||
                cursor[1] == 0x96U || cursor[1] == 0xA4U ||
                cursor[1] == 0xA5U || cursor[1] == 0xB6U)) {
      encoded[0] = 0xC3U;
      encoded[1] = cursor[1] == 0xA4U ? 0x84U :
                   cursor[1] == 0xA5U ? 0x85U :
                   cursor[1] == 0xB6U ? 0x96U : cursor[1];
      encoded_length = 2;
    }

    if (output + encoded_length >= capacity) break;
    memcpy(destination + output, encoded, encoded_length);
    output += encoded_length;
    cursor += consumed;
  }
  destination[output] = '\0';
}

tk_agent_state tk_agent_monitor_effective_state(
    const tk_agent_status *status, uint64_t packet_age_ms) {
  if (!status) return TK_AGENT_UNKNOWN;
  if (status->state != TK_AGENT_WORKING) return status->state;
  if ((uint64_t)status->updated_ms > TK_AGENT_WORKING_LEASE_MS ||
      packet_age_ms >
          TK_AGENT_WORKING_LEASE_MS - (uint64_t)status->updated_ms) {
    return TK_AGENT_UNKNOWN;
  }
  return TK_AGENT_WORKING;
}

bool tk_agent_monitor_status_present(const tk_agent_status *status,
                                     const char *dismissed_event_id,
                                     uint64_t packet_age_ms) {
  tk_agent_state state =
      tk_agent_monitor_effective_state(status, packet_age_ms);
  if (state == TK_AGENT_IDLE || state == TK_AGENT_UNKNOWN) return false;
  return !dismissed_event_id || !status->event_id[0] ||
         strcmp(status->event_id, dismissed_event_id) != 0;
}

bool tk_agent_monitor_should_keep_awake(const tk_agent_status *status,
                                        const char *dismissed_event_id,
                                        uint64_t packet_age_ms) {
  return tk_agent_monitor_effective_state(status, packet_age_ms) ==
             TK_AGENT_WORKING &&
         tk_agent_monitor_status_present(status, dismissed_event_id,
                                         packet_age_ms);
}

uint8_t tk_agent_monitor_presence_mask(
    const tk_agent_status agents[2],
    const char dismissed_event_ids[2][TK_AGENT_ID_CAP],
    uint64_t packet_age_ms) {
  if (!agents || !dismissed_event_ids) return 0;
  uint8_t mask = 0;
  for (int provider = 0; provider < 2; provider++) {
    if (tk_agent_monitor_status_present(&agents[provider],
                                        dismissed_event_ids[provider],
                                        packet_age_ms)) {
      mask |= (uint8_t)(1U << provider);
    }
  }
  return mask;
}

bool tk_agent_monitor_tick_should_render(bool has_snapshot, int selected,
                                         uint8_t rendered_presence_mask,
                                         uint8_t current_presence_mask) {
  return has_snapshot && selected >= 0 &&
         rendered_presence_mask != current_presence_mask;
}

static int state_priority(tk_agent_state state) {
  switch (state) {
    case TK_AGENT_WAITING: return 5;
    case TK_AGENT_ERROR: return 4;
    case TK_AGENT_WORKING: return 3;
    case TK_AGENT_DONE: return 2;
    case TK_AGENT_IDLE: return 1;
    case TK_AGENT_UNKNOWN: return 0;
  }
  return 0;
}

static int best_provider(const tk_agent_status agents[2],
                         const bool present[2]) {
  int best = -1;
  for (int provider = 0; provider < 2; provider++) {
    if (!present[provider]) continue;
    if (best < 0 ||
        state_priority(agents[provider].state) >
            state_priority(agents[best].state) ||
        (state_priority(agents[provider].state) ==
             state_priority(agents[best].state) &&
         agents[provider].updated_ms < agents[best].updated_ms)) {
      best = provider;
    }
  }
  return best;
}

void tk_agent_monitor_manual_choice_set(tk_agent_manual_choice *choice,
                                        int provider,
                                        const tk_agent_status agents[2]) {
  if (!choice) return;
  memset(choice, 0, sizeof *choice);
  if (!agents || provider < 0 || provider > 1) return;
  choice->active = true;
  choice->provider = provider;
  for (int i = 0; i < 2; i++) {
    memcpy(choice->seen_event_id[i], agents[i].event_id,
           sizeof choice->seen_event_id[i]);
    choice->seen_state[i] = agents[i].state;
  }
}

void tk_agent_monitor_manual_choice_clear(tk_agent_manual_choice *choice) {
  if (choice) memset(choice, 0, sizeof *choice);
}

static bool generation_changed(const tk_agent_manual_choice *choice,
                               int provider,
                               const tk_agent_status agents[2]) {
  return choice->seen_state[provider] != agents[provider].state ||
         strcmp(choice->seen_event_id[provider],
                agents[provider].event_id) != 0;
}

int tk_agent_monitor_resolve_provider(const tk_agent_status agents[2],
                                      const bool present[2],
                                      const tk_agent_manual_choice *choice) {
  int best = best_provider(agents, present);
  if (!choice || !choice->active || choice->provider < 0 ||
      choice->provider > 1 || !present[choice->provider]) {
    return best;
  }
  int selected = choice->provider;
  if (best < 0 || state_priority(agents[best].state) <=
                      state_priority(agents[selected].state)) {
    return selected;
  }
  /* Ett redan synligt waiting/error-event ska inte rycka tillbaka vyn på
   * varje identisk repoll. Bara en ny högre eventgeneration efter trycket
   * får bryta användarens uttryckliga provider-val. */
  return generation_changed(choice, best, agents) ? best : selected;
}

const char *tk_agent_monitor_activity_text(const tk_agent_status *status) {
  if (!status) return "";
  if (status->state == TK_AGENT_IDLE || status->state == TK_AGENT_UNKNOWN)
    return "";
  if (status->state == TK_AGENT_DONE) return "VÄNTAR PÅ DIG";
  if (status->state == TK_AGENT_ERROR) return "FEL";
  switch (status->activity) {
    case TK_ACTIVITY_THINKING: return "TÄNKER";
    case TK_ACTIVITY_READING: return "LÄSER KOD";
    case TK_ACTIVITY_EDITING: return "ÄNDRAR FILER";
    case TK_ACTIVITY_SEARCHING: return "SÖKER I PROJEKTET";
    case TK_ACTIVITY_RUNNING: return "KÖR KOMMANDO";
    case TK_ACTIVITY_TESTING: return "KÖR TESTER";
    case TK_ACTIVITY_BUILDING: return "BYGGER PROJEKTET";
    case TK_ACTIVITY_WAITING_INPUT: return "BEHÖVER ETT SVAR";
    case TK_ACTIVITY_WAITING_APPROVAL: return "BEHÖVER GODKÄNNAN";
    case TK_ACTIVITY_NONE:
    case TK_ACTIVITY_UNKNOWN:
      return status->state == TK_AGENT_WORKING ? "JOBBAR" : "";
  }
  return "";
}
