#include "labs_features.h"
#include "app_tokens_config.h"

_Static_assert(TK_LABS_ALL == (1u << TK_LABS_COUNT) - 1u,
               "Update the persisted Labs mask when adding a feature");
static uint8_t active, selected;
static bool read_only, storage_error;

static uint8_t defaults(void) {
  return (TK_LABS_ANALYTICS_DEFAULT ? 7u : 0u) |
         (TK_GITHUB_SCREEN_ENABLED ? 8u : 0u) |
         (TK_GITHUB_NOTIFICATIONS_ENABLED ? 16u : 0u) |
         (TK_LOVABLE_SCREEN_ENABLED ? 32u : 0u) |
         (1u << TK_LABS_CLAUDE_CODE) | (1u << TK_LABS_CODEX);
}

void tk_labs_init(void) {
  uint32_t record = 0;
  tk_labs_store_result result = tk_labs_store_read(&record);
  read_only = result == TK_LABS_STORE_ERROR;
  active = selected = defaults();
  storage_error = read_only;
  if (result == TK_LABS_STORE_FOUND) {
    uint32_t version = record & ~TK_LABS_ALL;
    if (version == TK_LABS_RECORD_VERSION) {
      active = selected = (uint8_t)(record & TK_LABS_ALL);
    } else if ((record & ~TK_LABS_PREVIOUS_ALL) ==
               TK_LABS_PREVIOUS_RECORD_VERSION) {
      /* Upgrade existing six-switch records without changing the owner's
       * saved Labs choices. Claude Code and Codex remain enabled by default. */
      uint8_t migrated = (uint8_t)(record & TK_LABS_PREVIOUS_ALL) |
          (1u << TK_LABS_CLAUDE_CODE) | (1u << TK_LABS_CODEX);
      active = selected = migrated;
      if (!tk_labs_store_write(TK_LABS_RECORD_VERSION | migrated)) {
        read_only = storage_error = true;
      }
    } else {
      read_only = storage_error = true;
    }
  }
  if (result == TK_LABS_STORE_EMPTY)
    storage_error = !tk_labs_store_write(TK_LABS_RECORD_VERSION | selected);
}

static bool valid(int feature) { return feature >= 0 && feature < TK_LABS_COUNT; }
bool tk_labs_active(tk_labs_feature feature) {
  return valid(feature) && (active & (1u << feature));
}
bool tk_labs_selected(int feature) {
  return valid(feature) && (selected & (1u << feature));
}
bool tk_labs_toggle(int feature) {
  if (!valid(feature) || read_only) return false;
  uint8_t next = selected ^ (1u << feature);
  if (!tk_labs_store_write(TK_LABS_RECORD_VERSION | next)) {
    storage_error = true;
    return false;
  }
  selected = next;
  storage_error = false;
  return true;
}
bool tk_labs_pending(void) { return active != selected; }
bool tk_labs_storage_error(void) { return storage_error; }
const char *tk_labs_name(int feature) {
  static const char *const names[] = {
    "BURN RATE", "MAX TRACKER", "API VALUE", "GITHUB PAGE", "STAR POPUP",
    "LOVABLE PAGE", "CLAUDE CODE", "CODEX"
  };
  return valid(feature) ? names[feature] : "";
}
static bool view_enabled(int view) {
  switch (view) {
    case VIEW_CLAUDE_FABLE:
    case VIEW_CLAUDE_ALL: return tk_labs_active(TK_LABS_CLAUDE_CODE);
    case VIEW_CODEX_WEEKLY: return tk_labs_active(TK_LABS_CODEX);
    case VIEW_BURN_RATE: return tk_labs_active(TK_LABS_BURN_RATE);
    case VIEW_TRACKER_CLAUDE:
    case VIEW_TRACKER_CODEX: return tk_labs_active(TK_LABS_TRACKER);
    case VIEW_GITHUB: return tk_labs_active(TK_LABS_GITHUB);
    case VIEW_VALUE: return tk_labs_active(TK_LABS_VALUE);
    case VIEW_LOVABLE: return tk_labs_active(TK_LABS_LOVABLE);
    default: return false;
  }
}
int tk_labs_view_position(int view) {
  if (!view_enabled(view)) return -1;
  int position = 0;
  for (int i = 0; i < view; i++) if (view_enabled(i)) position++;
  return position;
}
int tk_labs_view_count(void) {
  int count = 0;
  for (int i = 0; i < TK_USAGE_SCREEN_VIEWS; i++) if (view_enabled(i)) count++;
  return count;
}
int tk_labs_next_view(int view, int direction) {
  if (view < 0 || view >= TK_USAGE_SCREEN_VIEWS)
    view = direction < 0 ? 0 : TK_USAGE_SCREEN_VIEWS - 1;
  for (int attempt = 0; attempt < TK_USAGE_SCREEN_VIEWS; attempt++) {
    view = (view + (direction < 0 ? TK_USAGE_SCREEN_VIEWS - 1 : 1)) %
           TK_USAGE_SCREEN_VIEWS;
    if (view_enabled(view)) return view;
  }
  return -1; /* All pages off: keep Settings and the agent overlay usable. */
}
