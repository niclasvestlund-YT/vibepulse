set(TORGET_LVGL_POOL_MIN_KIB 256)
if(TORGET_BOARD STREQUAL "waveshare_18_v2")
  # The transformed viewport needs a full composition layer as well as the UI.
  # SDKCONFIG_DEFAULTS cannot raise the pool in an existing generated config.
  set(TORGET_LVGL_POOL_MIN_KIB 768)
endif()

function(torget_require_lvgl_pool actual_kib)
  if("${actual_kib}" STREQUAL "" OR NOT "${actual_kib}" MATCHES "^[0-9]+$")
    message(FATAL_ERROR
      "LVGL pool size is missing or invalid: '${actual_kib}'")
  endif()

  if(actual_kib LESS TORGET_LVGL_POOL_MIN_KIB)
    message(FATAL_ERROR
      "LVGL pool is ${actual_kib} KiB; Torget requires at least "
      "${TORGET_LVGL_POOL_MIN_KIB} KiB. The generated sdkconfig is stale: "
      "edit sdkconfig and set "
      "CONFIG_LV_MEM_SIZE_KILOBYTES=${TORGET_LVGL_POOL_MIN_KIB} "
      "(matching sdkconfig.defaults), then run: "
      "idf.py reconfigure && idf.py build")
  endif()
endfunction()
