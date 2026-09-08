#include <assert.h>
#include <stdio.h>
#include "../components/app_tokens/labs_features.h"
#include "../components/app_tokens/app_tokens_config.h"
static uint32_t saved;
static int writes;
static bool fail_write;
static tk_labs_store_result result;
tk_labs_store_result tk_labs_store_read(uint32_t *record) {
  *record = saved;
  return result;
}
bool tk_labs_store_write(uint32_t record) {
  writes++;
  if (fail_write) return false;
  saved = record;
  result = TK_LABS_STORE_FOUND;
  return true;
}
int main(void) {
  result = TK_LABS_STORE_EMPTY;
  tk_labs_init();
  assert(tk_labs_view_count() == (TK_LABS_ANALYTICS_DEFAULT ? 7 : 3) +
                                TK_GITHUB_SCREEN_ENABLED);
  assert(tk_labs_active(TK_LABS_GITHUB) == !!TK_GITHUB_SCREEN_ENABLED);
  assert(tk_labs_active(TK_LABS_STAR_POPUP) == !!TK_GITHUB_NOTIFICATIONS_ENABLED);
  assert(writes == 1 && !tk_labs_pending());
  /* Every subset must produce contiguous columns, including Value without
   * GitHub and popup without a page. Boot and selected must never be confused. */
  for (unsigned mask = 0; mask <= TK_LABS_ALL; mask++) {
    saved = TK_LABS_RECORD_VERSION | mask;
    tk_labs_init();
    int expected_count = 3 + !!(mask & 1) + 2 * !!(mask & 2) +
                         !!(mask & 4) + !!(mask & 8);
    assert(tk_labs_view_count() == expected_count);
    int pos = 0, previous = -1;
    for (int view = 0; view < TK_USAGE_SCREEN_VIEWS; view++) {
      int at = tk_labs_view_position(view);
      if (at < 0) continue;
      assert(at == pos++);
      if (previous >= 0) {
        assert(tk_labs_next_view(previous, 1) == view);
        assert(tk_labs_next_view(view, -1) == previous);
      }
      previous = view;
    }
    assert(tk_labs_next_view(previous, 1) == 0);
    assert(tk_labs_next_view(0, -1) == previous);
    assert(tk_labs_view_position(-1) == -1);
    assert(tk_labs_view_position(8) == -1);
    for (int feature = 0; feature < TK_LABS_COUNT; feature++) {
      bool before = !!(mask & (1u << feature));
      assert(tk_labs_active(feature) == before);
      assert(tk_labs_toggle(feature));
      assert(tk_labs_selected(feature) != before);
      assert(tk_labs_active(feature) == before);
      assert(tk_labs_pending());
      assert(tk_labs_view_count() == expected_count);
      assert(tk_labs_toggle(feature));
      assert(!tk_labs_pending());
    }
  }
  saved = TK_LABS_RECORD_VERSION;
  tk_labs_init();
  assert(tk_labs_toggle(TK_LABS_VALUE));
  tk_labs_init(); /* reboot: saved choice wins over any template default */
  assert(tk_labs_active(TK_LABS_VALUE) && !tk_labs_pending());
  assert(tk_labs_view_count() == 4);
  assert(tk_labs_view_position(VIEW_VALUE) == 3);
  fail_write = true;
  assert(!tk_labs_toggle(TK_LABS_VALUE));
  assert(tk_labs_selected(TK_LABS_VALUE) && tk_labs_storage_error());
  assert(!tk_labs_pending());
  fail_write = false;
  assert(tk_labs_toggle(TK_LABS_VALUE));
  assert(!tk_labs_storage_error());
  for (int mode = 0; mode < 3; mode++) {
    result = mode == 0 ? TK_LABS_STORE_ERROR : TK_LABS_STORE_FOUND;
    saved = mode == 1 ? 0x200u : 0x120u; /* newer version / unknown bit */
    int before_writes = writes;
    tk_labs_init();
    assert(tk_labs_storage_error());
    assert(!tk_labs_toggle(TK_LABS_VALUE));
    assert(writes == before_writes); /* don't clobber unreadable/future state */
  }
  result = TK_LABS_STORE_EMPTY;
  fail_write = true;
  tk_labs_init();
  assert(tk_labs_storage_error());
  fail_write = false;
  assert(tk_labs_toggle(TK_LABS_VALUE)); /* initial seed failure can recover */
  int before_writes = writes;
  assert(!tk_labs_toggle(-1) && !tk_labs_toggle(TK_LABS_COUNT));
  assert(writes == before_writes);
  puts("OK: LABS migration, 32 dense page combinations, restart and storage failures");
}
