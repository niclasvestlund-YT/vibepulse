#include "labs_features.h"
#include "app_tokens_config.h"

_Static_assert(TK_LABS_ALL == (1u << TK_LABS_COUNT) - 1u,
               "Update the persisted Labs mask when adding a feature");
static uint8_t active, selected;
static bool read_only, storage_error;

static uint8_t defaults(void) {
  return (TK_LABS_ANALYTICS_DEFAULT ? 7u : 0u) |
         (TK_GITHUB_SCREEN_ENABLED ? 8u : 0u) |
         (TK_GITHUB_NOTIFICATIONS_ENABLED ? 16u : 0u);
}

void tk_labs_init(void) {
  uint32_t record = 0;
  tk_labs_store_result result = tk_labs_store_read(&record);
  read_only = result == TK_LABS_STORE_ERROR ||
      (result == TK_LABS_STORE_FOUND &&
       (record & ~TK_LABS_ALL) != TK_LABS_RECORD_VERSION);
  active = selected = defaults();
  storage_error = read_only;
  if (result == TK_LABS_STORE_FOUND && !read_only)
    active = selected = (uint8_t)(record & TK_LABS_ALL);
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
    "BURN RATE", "MAX TRACKER", "API VALUE", "GITHUB PAGE", "STAR POPUP"
  };
  return valid(feature) ? names[feature] : "";
}
static bool view_enabled(int view) {
  if (view >= 0 && view <= VIEW_CODEX_WEEKLY) return true;
  switch (view) {
    case VIEW_BURN_RATE: return tk_labs_active(TK_LABS_BURN_RATE);
    case VIEW_TRACKER_CLAUDE:
    case VIEW_TRACKER_CODEX: return tk_labs_active(TK_LABS_TRACKER);
    case VIEW_GITHUB: return tk_labs_active(TK_LABS_GITHUB);
    case VIEW_VALUE: return tk_labs_active(TK_LABS_VALUE);
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
  if (view < 0 || view >= TK_USAGE_SCREEN_VIEWS) return VIEW_CLAUDE_FABLE;
  do {
    view = (view + (direction < 0 ? TK_USAGE_SCREEN_VIEWS - 1 : 1)) %
           TK_USAGE_SCREEN_VIEWS;
  } while (!view_enabled(view));
  return view;
}
