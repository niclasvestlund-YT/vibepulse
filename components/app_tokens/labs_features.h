#ifndef TK_LABS_FEATURES_H
#define TK_LABS_FEATURES_H
#include <stdbool.h>
#include <stdint.h>

typedef enum {
  TK_LABS_BURN_RATE, TK_LABS_TRACKER, TK_LABS_VALUE,
  TK_LABS_GITHUB, TK_LABS_STAR_POPUP, TK_LABS_COUNT
} tk_labs_feature;
#define TK_LABS_ALL 31u
#define TK_LABS_RECORD_VERSION 0x100u

/* IDs stay stable; physical tile columns are dense and depend on the boot mask. */
enum {
  VIEW_CLAUDE_FABLE = 0, VIEW_CLAUDE_ALL = 1, VIEW_CODEX_WEEKLY = 2,
  VIEW_BURN_RATE = 3, VIEW_TRACKER_CLAUDE = 4, VIEW_TRACKER_CODEX = 5,
  VIEW_GITHUB = 6, VIEW_VALUE = 7, TK_USAGE_SCREEN_VIEWS = 8
};

/* Init before creating UI/tasks. Active is immutable until the next boot.
 * Selected changes only after a successful durable write. UI-lock-only setters. */
void tk_labs_init(void);
bool tk_labs_active(tk_labs_feature feature);
bool tk_labs_selected(int feature);
bool tk_labs_toggle(int feature);
bool tk_labs_pending(void);
bool tk_labs_storage_error(void);
const char *tk_labs_name(int feature);
int tk_labs_view_position(int view);
int tk_labs_view_count(void);
int tk_labs_next_view(int view, int direction);

typedef enum { TK_LABS_STORE_FOUND, TK_LABS_STORE_EMPTY, TK_LABS_STORE_ERROR }
    tk_labs_store_result;
/* Target NVS adapter; simulator uses process-local memory, tests inject errors. */
tk_labs_store_result tk_labs_store_read(uint32_t *record);
bool tk_labs_store_write(uint32_t record);
#endif
