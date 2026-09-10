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
         log_default_info log_max_equals_default task_wdt lv_use_log)
  set(_missing "")
  if(NOT "${coredump_to_flash}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y")
  endif()
  if(NOT "${coredump_elf}" STREQUAL "y")
    list(APPEND _missing "CONFIG_ESP_COREDUMP_DATA_FORMAT_ELF=y")
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
  if(NOT "${lv_use_log}" STREQUAL "y")
    list(APPEND _missing "CONFIG_LV_USE_LOG=y")
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
