# One explicit board selector for firmware and the native LVGL simulator.
set(TORGET_BOARD "waveshare_216" CACHE STRING "VibePulse board profile")
set_property(CACHE TORGET_BOARD PROPERTY STRINGS waveshare_216 waveshare_241_v2)
if(TORGET_BOARD STREQUAL "waveshare_241_v2")
  add_compile_definitions(TORGET_BOARD_241_V2=1)
elseif(NOT TORGET_BOARD STREQUAL "waveshare_216")
  message(FATAL_ERROR "Unsupported TORGET_BOARD: ${TORGET_BOARD}. Choose waveshare_216 or waveshare_241_v2; 2.41 V1 is not supported.")
endif()
