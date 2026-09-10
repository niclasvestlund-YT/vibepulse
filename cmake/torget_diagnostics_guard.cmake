# The crash and log evidence the firmware promises (OBS-02, OBS-03, OBS-28,
# OBS-35) is only as real as the EFFECTIVE sdkconfig. sdkconfig.defaults
# seeds a new generated file and never migrates an old one (docs/lessons.md,
# 2026-08-19), so a checkout that already had a sdkconfig would build and
# flash an image with no coredump partition writer, no LVGL warnings and no
# task watchdog while every source-level test passed. Same medicine as the
# LVGL pool guard: treat the values as build invariants and refuse here.
#
# Each argument is the effective CONFIG_* value as CMake sees it after the
# IDF project() call ("y" when set, "" when unset or =n).
function(torget_require_diagnostics coredump_to_flash coredump_elf
         panic_reboot log_default_info log_max_equals_default task_wdt
         task_wdt_timeout_s task_wdt_panic lv_use_log lv_log_printf
         lv_log_level_warn)
  set(_missing "")
  if(NOT "${coredump_to_flash}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y")
  endif()
  if(NOT "${coredump_elf}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_COREDUMP_DATA_FORMAT_ELF=y")
  endif()
  # A halted or GDB-stubbed panic leaves an unattended shelf display dark
  # with no ledger line and no dump notice; the panel must reboot.
  if(NOT "${panic_reboot}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y")
  endif()
  # ESP_LOGD compiled in would print the relay's secret URL (OBS-35). The
  # ceiling is "equals the default", so the default itself must be INFO:
  # a stale config with DEFAULT_LEVEL_DEBUG + MAXIMUM_EQUALS_DEFAULT would
  # otherwise pass with a DEBUG ceiling.
  if(NOT "${log_default_info}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LOG_DEFAULT_LEVEL_INFO=y")
  endif()
  if(NOT "${log_max_equals_default}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LOG_MAXIMUM_EQUALS_DEFAULT=y")
  endif()
  if(NOT "${task_wdt}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_TASK_WDT_INIT=y")
  endif()
  # The task watchdog is documented as warn-only (docs/observability.md);
  # a stale config with the panic option on would reboot the panel on a
  # starved IDLE instead of the transient serial line the runbook promises.
  # The five-second timeout is the documented behaviour; a stale value
  # warns much earlier or later than the runbook says.
  if(NOT "${task_wdt_timeout_s}" STREQUAL "5")
    list(APPEND _missing "CONFIG_ESP_TASK_WDT_TIMEOUT_S=5")
  endif()
  if("${task_wdt_panic}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_TASK_WDT_PANIC unset (warn-only)")
  endif()
  if(NOT "${lv_use_log}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LV_USE_LOG=y")
  endif()
  # LV_USE_LOG alone has no sink: nothing registers lv_log_register_print_cb,
  # so the printf sink and the WARN level are part of the promise too.
  if(NOT "${lv_log_printf}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LV_LOG_PRINTF=y")
  endif()
  if(NOT "${lv_log_level_warn}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LV_LOG_LEVEL_WARN=y")
  endif()
  if(NOT "${_missing}" STREQUAL "")
    string(REPLACE ";" ", " _missing_text "${_missing}")
    message(FATAL_ERROR
      "Firmware diagnostics are disabled in the generated sdkconfig "
      "(missing: ${_missing_text}). The generated sdkconfig is stale: set "
      "those values to match sdkconfig.defaults, then run: "
      "idf.py reconfigure && idf.py build")
  endif()
endfunction()
